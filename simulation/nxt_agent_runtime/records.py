"""Runtime evaluation outcome records for Agent Runtime V1.

One :class:`EvaluationRecord` is emitted per admitted
``FacilitySnapshotEnvelope``.  The record owns only runtime evaluation
lifecycle identity (``evaluation_id`` and record framing).  Every policy
fact inside it — verdict, policy identity, trace, recommendation — is
owned by ``nxt_pilot_ops`` and is referenced by canonical ID or embedded
verbatim through the existing canonical serializer, never re-modeled.

Ownership of the embedded evidence is asymmetric on purpose:

- ``NO_ACTION`` evaluations embed the canonical ``DecisionTrace`` payload
  because no other durable store records why the Agent did not act.
- ``RECOMMEND`` evaluations reference the Shadow Ops ledger event that
  canonically stores the recommendation and trace; the journal does not
  duplicate them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from nxt_pilot_ops.contracts import EvaluationVerdict, RecommendationAction
from nxt_pilot_ops.serialization import stable_digest, to_primitive

EVALUATION_SCHEMA_VERSION = "nxt-agent-runtime/evaluation/v1"

_HEX_DIGITS = frozenset("0123456789abcdef")


def _require_text(name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_optional_text(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_text(name, value)


def _require_aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _require_int(name: str, value: object, *, minimum: int = 0) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_timestamp(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    converted = float(value)
    if not math.isfinite(converted) or converted < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return converted


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("trace payload mappings require string keys")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise TypeError(f"unsupported trace payload value: {type(value)!r}")


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    """One canonical runtime evaluation outcome for one admitted envelope."""

    evaluation_id: str
    schema_version: str
    site_id: str
    deployment_id: str
    sequence_number: int
    envelope_id: str
    observation_timestamp_s: float
    observed_at: datetime
    verdict: EvaluationVerdict
    policy_id: str
    policy_version: str
    trace_id: str
    snapshot_digest: str
    recommendation_id: str | None
    recommendation_action: RecommendationAction | None
    ledger_event_id: str | None
    decision_trace: Mapping[str, Any] | None

    def __post_init__(self) -> None:
        _require_text("evaluation_id", self.evaluation_id)
        if self.schema_version != EVALUATION_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {EVALUATION_SCHEMA_VERSION!r}"
            )
        _require_text("site_id", self.site_id)
        _require_text("deployment_id", self.deployment_id)
        _require_int("sequence_number", self.sequence_number)
        _require_text("envelope_id", self.envelope_id)
        object.__setattr__(
            self,
            "observation_timestamp_s",
            _require_timestamp(
                "observation_timestamp_s", self.observation_timestamp_s
            ),
        )
        _require_aware("observed_at", self.observed_at)
        if not isinstance(self.verdict, EvaluationVerdict):
            raise TypeError("verdict must be an EvaluationVerdict")
        _require_text("policy_id", self.policy_id)
        _require_text("policy_version", self.policy_version)
        _require_text("trace_id", self.trace_id)
        digest = _require_text("snapshot_digest", self.snapshot_digest)
        if len(digest) != 64 or any(
            character not in _HEX_DIGITS for character in digest
        ):
            raise ValueError(
                "snapshot_digest must be a lowercase SHA-256 digest"
            )
        _require_optional_text("recommendation_id", self.recommendation_id)
        _require_optional_text("ledger_event_id", self.ledger_event_id)
        if self.recommendation_action is not None and not isinstance(
            self.recommendation_action, RecommendationAction
        ):
            raise TypeError(
                "recommendation_action must be a RecommendationAction or None"
            )
        if self.verdict is EvaluationVerdict.NO_ACTION:
            if (
                self.recommendation_id is not None
                or self.recommendation_action is not None
                or self.ledger_event_id is not None
            ):
                raise ValueError(
                    "NO_ACTION records cannot reference a recommendation"
                )
            if self.decision_trace is None:
                raise ValueError(
                    "NO_ACTION records must embed the canonical decision trace"
                )
        else:
            if (
                self.recommendation_id is None
                or self.recommendation_action is None
                or self.ledger_event_id is None
            ):
                raise ValueError(
                    "RECOMMEND records must reference the recommendation, "
                    "its action, and its ledger issuance event"
                )
            if self.decision_trace is not None:
                raise ValueError(
                    "RECOMMEND records reference the ledger; they do not "
                    "duplicate the decision trace"
                )
        if self.decision_trace is not None:
            primitive = to_primitive(self.decision_trace)
            if type(primitive) is not dict:
                raise TypeError("decision_trace must be a mapping payload")
            if primitive.get("trace_id") != self.trace_id:
                raise ValueError(
                    "embedded decision trace does not match trace_id"
                )
            object.__setattr__(self, "decision_trace", _freeze(primitive))
        expected = f"aev_{stable_digest(self._content_values())[:24]}"
        if self.evaluation_id != expected:
            raise ValueError(
                "evaluation_id does not match the record content"
            )

    def _content_values(self) -> dict[str, object]:
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name != "evaluation_id"
        }

    @classmethod
    def create(cls, **values: object) -> "EvaluationRecord":
        if "evaluation_id" in values:
            raise ValueError("evaluation_id is derived and must not be supplied")
        normalized = dict(values)
        normalized.setdefault("schema_version", EVALUATION_SCHEMA_VERSION)
        timestamp = normalized.get("observation_timestamp_s")
        if (
            timestamp is not None
            and not isinstance(timestamp, bool)
            and isinstance(timestamp, (int, float))
        ):
            normalized["observation_timestamp_s"] = float(timestamp)
        trace_payload = normalized.get("decision_trace")
        if trace_payload is not None:
            normalized["decision_trace"] = to_primitive(trace_payload)
        return cls(
            evaluation_id=f"aev_{stable_digest(normalized)[:24]}",
            **normalized,
        )

    def to_payload(self) -> dict[str, Any]:
        """JSON-ready canonical payload for the evaluation journal."""
        payload = to_primitive(self)
        assert type(payload) is dict
        return payload
