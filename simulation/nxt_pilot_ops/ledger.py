"""Deterministic append-only JSONL ledger with verified workflow replay.

The hash chain provides tamper evidence inside this file boundary.  It is not a
claim of non-repudiation, external timestamping, or regulatory compliance.
"""

from __future__ import annotations

import json
import os
import string
import threading
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, TextIO

try:  # POSIX advisory file locking; module import remains portable.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on non-POSIX platforms.
    _fcntl = None

from .contracts import (
    BallAvailabilityPolicyConfig,
    DecisionTrace,
    InboundBallBatch,
    Recommendation,
    RecommendationAction,
    RobotCandidateTrace,
    RobotStatus,
)
from .semantics import validate_recommendation_for_trace
from .serialization import canonical_json, stable_digest, to_primitive
from .workflow import (
    ExecutionAcknowledgement,
    ExecutionRequest,
    ModifiedRecommendation,
    RecommendationCase,
    RecommendationOutcome,
    RecommendationResponse,
    ResponseKind,
)

_LEDGER_SCHEMA = "nxt-pilot-ops-ledger/v1"
_GENESIS_HASH = "0" * 64
_EVENT_TYPES = frozenset(
    {
        "recommendation_issued",
        "recommendation_response",
        "execution_requested",
        "execution_acknowledged",
        "recommendation_outcome",
    }
)
_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "sequence",
        "event_id",
        "event_type",
        "site_id",
        "deployment_id",
        "occurred_at",
        "payload",
        "causation_id",
        "previous_hash",
        "record_hash",
    }
)


def _require_text(name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("ledger payload mappings require string keys")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise TypeError(f"unsupported ledger payload value: {type(value)!r}")


def _event_seed(
    *,
    event_type: str,
    site_id: str,
    deployment_id: str,
    occurred_at: datetime,
    payload: Mapping[str, Any],
    causation_id: str | None,
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "site_id": site_id,
        "deployment_id": deployment_id,
        "occurred_at": occurred_at,
        "payload": payload,
        "causation_id": causation_id,
    }


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    event_id: str
    event_type: str
    site_id: str
    deployment_id: str
    occurred_at: datetime
    payload: Mapping[str, Any]
    causation_id: str | None = None

    def __post_init__(self) -> None:
        _require_text("event_id", self.event_id)
        _require_text("event_type", self.event_type)
        _require_text("site_id", self.site_id)
        _require_text("deployment_id", self.deployment_id)
        _require_aware("occurred_at", self.occurred_at)
        if self.event_type not in _EVENT_TYPES:
            raise ValueError(f"unsupported event_type: {self.event_type!r}")
        if self.causation_id is not None:
            _require_text("causation_id", self.causation_id)
        primitive = to_primitive(self.payload)
        if type(primitive) is not dict:
            raise TypeError("payload must be a mapping")
        frozen_payload = _freeze(primitive)
        object.__setattr__(self, "payload", frozen_payload)
        expected = "evt_" + stable_digest(
            _event_seed(
                event_type=self.event_type,
                site_id=self.site_id,
                deployment_id=self.deployment_id,
                occurred_at=self.occurred_at,
                payload=frozen_payload,
                causation_id=self.causation_id,
            )
        )[:24]
        if self.event_id != expected:
            raise ValueError("event_id does not match its deterministic content")


def _make_event(
    *,
    event_type: str,
    site_id: str,
    deployment_id: str,
    occurred_at: datetime,
    payload: Mapping[str, Any],
    causation_id: str | None,
) -> LedgerEvent:
    primitive = to_primitive(payload)
    assert type(primitive) is dict
    seed = _event_seed(
        event_type=event_type,
        site_id=site_id,
        deployment_id=deployment_id,
        occurred_at=occurred_at,
        payload=primitive,
        causation_id=causation_id,
    )
    return LedgerEvent(
        event_id=f"evt_{stable_digest(seed)[:24]}",
        event_type=event_type,
        site_id=site_id,
        deployment_id=deployment_id,
        occurred_at=occurred_at,
        payload=primitive,
        causation_id=causation_id,
    )


def recommendation_issued_event(
    recommendation: Recommendation,
    trace: DecisionTrace,
) -> LedgerEvent:
    validate_recommendation_for_trace(recommendation, trace)
    return _make_event(
        event_type="recommendation_issued",
        site_id=recommendation.site_id,
        deployment_id=recommendation.deployment_id,
        occurred_at=recommendation.issued_at,
        payload={"recommendation": recommendation, "decision_trace": trace},
        causation_id=None,
    )

def recommendation_response_event(
    *,
    response: RecommendationResponse,
    site_id: str,
    deployment_id: str,
) -> LedgerEvent:
    return _make_event(
        event_type="recommendation_response",
        site_id=site_id,
        deployment_id=deployment_id,
        occurred_at=response.responded_at,
        payload={"response": response},
        causation_id=response.recommendation_id,
    )


def execution_requested_event(
    *,
    request: ExecutionRequest,
    site_id: str,
    deployment_id: str,
) -> LedgerEvent:
    return _make_event(
        event_type="execution_requested",
        site_id=site_id,
        deployment_id=deployment_id,
        occurred_at=request.requested_at,
        payload={"execution_request": request},
        causation_id=request.recommendation_id,
    )


def execution_acknowledged_event(
    *,
    acknowledgement: ExecutionAcknowledgement,
    site_id: str,
    deployment_id: str,
) -> LedgerEvent:
    return _make_event(
        event_type="execution_acknowledged",
        site_id=site_id,
        deployment_id=deployment_id,
        occurred_at=acknowledgement.acknowledged_at,
        payload={"execution_acknowledgement": acknowledgement},
        causation_id=acknowledgement.request_id,
    )


def recommendation_outcome_event(
    *,
    outcome: RecommendationOutcome,
    site_id: str,
    deployment_id: str,
) -> LedgerEvent:
    return _make_event(
        event_type="recommendation_outcome",
        site_id=site_id,
        deployment_id=deployment_id,
        occurred_at=outcome.recorded_at,
        payload={"outcome": outcome},
        causation_id=outcome.recommendation_id,
    )


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    schema_version: str
    sequence: int
    event_id: str
    event_type: str
    site_id: str
    deployment_id: str
    occurred_at: str
    payload: Mapping[str, Any]
    causation_id: str | None
    previous_hash: str
    record_hash: str


@dataclass(frozen=True, slots=True)
class WorkflowReplay:
    """Deterministic workflow state rebuilt from a fully verified ledger."""

    cases: tuple[RecommendationCase, ...]
    record_count: int
    head_hash: str

    def case_for(self, recommendation_id: str) -> RecommendationCase:
        for case in self.cases:
            if case.recommendation.recommendation_id == recommendation_id:
                return case
        raise KeyError(recommendation_id)


class LedgerIntegrityError(ValueError):
    pass


class LedgerTransitionError(ValueError):
    pass


class JsonlEventLedger:
    """Append-only ledger with POSIX file locking and verification before append."""

    def __init__(self, path: str | Path) -> None:
        if _fcntl is None:
            raise RuntimeError(
                "JsonlEventLedger requires POSIX fcntl file locking; "
                "use a supported POSIX host or provide a platform ledger backend"
            )
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.RLock()
        self._records: tuple[LedgerRecord, ...] = ()
        self._replay = WorkflowReplay((), 0, _GENESIS_HASH)
        if self.path.exists():
            self._refresh()

    def append(self, event: LedgerEvent) -> LedgerRecord:
        if not isinstance(event, LedgerEvent):
            raise TypeError("event must be a LedgerEvent")
        with self._thread_lock:
            with self.path.open("a+", encoding="utf-8", newline="\n") as handle:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
                try:
                    handle.seek(0)
                    records, cases = _verify_handle(handle)
                    if any(record.event_id == event.event_id for record in records):
                        raise ValueError(f"duplicate event_id: {event.event_id}")
                    try:
                        _apply_event(cases, event)
                    except (TypeError, ValueError) as exc:
                        raise LedgerTransitionError(str(exc)) from exc
                    previous_hash = records[-1].record_hash if records else _GENESIS_HASH
                    body = {
                        "schema_version": _LEDGER_SCHEMA,
                        "sequence": len(records) + 1,
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "site_id": event.site_id,
                        "deployment_id": event.deployment_id,
                        "occurred_at": to_primitive(event.occurred_at),
                        "payload": event.payload,
                        "causation_id": event.causation_id,
                        "previous_hash": previous_hash,
                    }
                    record_hash = stable_digest(body)
                    handle.seek(0, os.SEEK_END)
                    handle.write(canonical_json({**body, "record_hash": record_hash}) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    record_data = {
                        **body,
                        "payload": _freeze(to_primitive(event.payload)),
                        "record_hash": record_hash,
                    }
                    record = LedgerRecord(**record_data)
                    records.append(record)
                    self._set_cache(records, cases)
                    return record
                finally:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)

    def verify(self) -> tuple[int, str]:
        replay = self.replay()
        return replay.record_count, replay.head_hash

    def replay(self) -> WorkflowReplay:
        with self._thread_lock:
            self._refresh()
            return self._replay

    def records(self) -> tuple[LedgerRecord, ...]:
        with self._thread_lock:
            self._refresh()
            return self._records

    def _refresh(self) -> None:
        if not self.path.exists():
            self._records = ()
            self._replay = WorkflowReplay((), 0, _GENESIS_HASH)
            return
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_SH)
            try:
                records, cases = _verify_handle(handle)
                self._set_cache(records, cases)
            finally:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)

    def _set_cache(
        self,
        records: list[LedgerRecord],
        cases: dict[str, RecommendationCase],
    ) -> None:
        head = records[-1].record_hash if records else _GENESIS_HASH
        self._records = tuple(records)
        self._replay = WorkflowReplay(
            cases=tuple(cases[key] for key in sorted(cases)),
            record_count=len(records),
            head_hash=head,
        )


def _verify_handle(
    handle: TextIO,
) -> tuple[list[LedgerRecord], dict[str, RecommendationCase]]:
    records = _read_hash_chain(handle)
    cases: dict[str, RecommendationCase] = {}
    for record in records:
        try:
            event = _event_from_record(record)
            _apply_event(cases, event)
        except (TypeError, ValueError, KeyError) as exc:
            raise LedgerIntegrityError(
                f"semantic integrity failure at sequence {record.sequence}: {exc}"
            ) from exc
    return records, cases


def _read_hash_chain(handle: TextIO) -> list[LedgerRecord]:
    records: list[LedgerRecord] = []
    previous_hash = _GENESIS_HASH
    seen: set[str] = set()
    for line_number, line in enumerate(handle, start=1):
        if not line.endswith("\n"):
            raise LedgerIntegrityError(
                f"ledger record at line {line_number} is not newline-terminated"
            )

        def reject_constant(value: str) -> None:
            raise LedgerIntegrityError(
                f"non-finite JSON number {value!r} at line {line_number}"
            )

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise LedgerIntegrityError(
                        f"duplicate JSON key {key!r} at line {line_number}"
                    )
                result[key] = value
            return result

        try:
            raw = json.loads(
                line,
                parse_constant=reject_constant,
                object_pairs_hook=unique_object,
            )
        except json.JSONDecodeError as exc:
            raise LedgerIntegrityError(
                f"invalid JSON at line {line_number}: {exc.msg}"
            ) from exc
        if type(raw) is not dict:
            raise LedgerIntegrityError(f"line {line_number} must contain a JSON object")
        try:
            canonical_line = canonical_json(raw) + "\n"
        except (TypeError, ValueError) as exc:
            raise LedgerIntegrityError(
                f"non-canonical record value at line {line_number}: {exc}"
            ) from exc
        if line != canonical_line:
            raise LedgerIntegrityError(
                f"non-canonical JSON serialization at line {line_number}"
            )
        if frozenset(raw) != _RECORD_KEYS:
            difference = sorted(frozenset(raw) ^ _RECORD_KEYS)
            raise LedgerIntegrityError(
                f"unexpected ledger keys at line {line_number}: {difference}"
            )
        if raw["schema_version"] != _LEDGER_SCHEMA:
            raise LedgerIntegrityError(f"unsupported schema at line {line_number}")
        if type(raw["sequence"]) is not int or raw["sequence"] != line_number:
            raise LedgerIntegrityError(f"sequence mismatch at line {line_number}")
        for name in (
            "event_id",
            "event_type",
            "site_id",
            "deployment_id",
            "occurred_at",
        ):
            if type(raw[name]) is not str or not raw[name].strip():
                raise LedgerIntegrityError(f"invalid {name} at line {line_number}")
        if type(raw["payload"]) is not dict:
            raise LedgerIntegrityError(f"payload must be an object at line {line_number}")
        if raw["causation_id"] is not None and (
            type(raw["causation_id"]) is not str or not raw["causation_id"].strip()
        ):
            raise LedgerIntegrityError(f"invalid causation_id at line {line_number}")
        for name in ("previous_hash", "record_hash"):
            if not _is_hash(raw[name]):
                raise LedgerIntegrityError(f"invalid {name} at line {line_number}")
        if raw["event_id"] in seen:
            raise LedgerIntegrityError(f"duplicate event_id at line {line_number}")
        if raw["previous_hash"] != previous_hash:
            raise LedgerIntegrityError(f"hash-chain break at line {line_number}")
        supplied_hash = raw.pop("record_hash")
        try:
            calculated_hash = stable_digest(raw)
        except (TypeError, ValueError) as exc:
            raise LedgerIntegrityError(
                f"non-canonical record value at line {line_number}: {exc}"
            ) from exc
        if supplied_hash != calculated_hash:
            raise LedgerIntegrityError(f"record hash mismatch at line {line_number}")
        raw["record_hash"] = supplied_hash
        raw["payload"] = _freeze(raw["payload"])
        record = LedgerRecord(**raw)
        records.append(record)
        seen.add(record.event_id)
        previous_hash = record.record_hash
    return records


def _is_hash(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in string.hexdigits.lower()[:16] for character in value)
    )


def _event_from_record(record: LedgerRecord) -> LedgerEvent:
    occurred_at = _parse_datetime(record.occurred_at, "occurred_at")
    if to_primitive(occurred_at) != record.occurred_at:
        raise ValueError("occurred_at is not in canonical UTC form")
    return LedgerEvent(
        event_id=record.event_id,
        event_type=record.event_type,
        site_id=record.site_id,
        deployment_id=record.deployment_id,
        occurred_at=occurred_at,
        payload=record.payload,
        causation_id=record.causation_id,
    )


def _apply_event(
    cases: dict[str, RecommendationCase],
    event: LedgerEvent,
) -> None:
    if event.event_type == "recommendation_issued":
        _expect_keys(event.payload, {"recommendation", "decision_trace"}, "issuance payload")
        recommendation = _decode_recommendation(event.payload["recommendation"])
        trace = _decode_trace(event.payload["decision_trace"])
        recommendation_issued_event(recommendation, trace)
        if event.event_id != recommendation_issued_event(recommendation, trace).event_id:
            raise ValueError("issuance event metadata does not match its payload")
        if event.causation_id is not None:
            raise ValueError("recommendation issuance cannot have a causation_id")
        if recommendation.recommendation_id in cases:
            raise ValueError("recommendation was already issued")
        cases[recommendation.recommendation_id] = RecommendationCase(recommendation)
        return

    payload_name = {
        "recommendation_response": "response",
        "execution_requested": "execution_request",
        "execution_acknowledged": "execution_acknowledgement",
        "recommendation_outcome": "outcome",
    }[event.event_type]
    _expect_keys(event.payload, {payload_name}, f"{event.event_type} payload")
    if event.event_type == "recommendation_response":
        workflow_event = _decode_response(event.payload[payload_name])
        recommendation_id = workflow_event.recommendation_id
        expected_causation = recommendation_id
        expected_event = recommendation_response_event(
            response=workflow_event,
            site_id=event.site_id,
            deployment_id=event.deployment_id,
        )
        operation = lambda case: case.apply_response(workflow_event)
    elif event.event_type == "execution_requested":
        workflow_event = _decode_execution_request(event.payload[payload_name])
        recommendation_id = workflow_event.recommendation_id
        expected_causation = recommendation_id
        expected_event = execution_requested_event(
            request=workflow_event,
            site_id=event.site_id,
            deployment_id=event.deployment_id,
        )
        operation = lambda case: case.apply_execution_request(workflow_event)
    elif event.event_type == "execution_acknowledged":
        workflow_event = _decode_execution_acknowledgement(event.payload[payload_name])
        recommendation_id = workflow_event.recommendation_id
        expected_causation = workflow_event.request_id
        expected_event = execution_acknowledged_event(
            acknowledgement=workflow_event,
            site_id=event.site_id,
            deployment_id=event.deployment_id,
        )
        operation = lambda case: case.apply_execution_acknowledgement(workflow_event)
    else:
        workflow_event = _decode_outcome(event.payload[payload_name])
        recommendation_id = workflow_event.recommendation_id
        expected_causation = recommendation_id
        expected_event = recommendation_outcome_event(
            outcome=workflow_event,
            site_id=event.site_id,
            deployment_id=event.deployment_id,
        )
        operation = lambda case: case.apply_outcome(workflow_event)

    try:
        case = cases[recommendation_id]
    except KeyError as exc:
        raise ValueError(f"unknown recommendation: {recommendation_id}") from exc
    if (event.site_id, event.deployment_id) != (
        case.recommendation.site_id,
        case.recommendation.deployment_id,
    ):
        raise ValueError("event site/deployment does not match the recommendation")
    if event.causation_id != expected_causation:
        raise ValueError("event causation_id is invalid")
    if event.event_id != expected_event.event_id or event.occurred_at != expected_event.occurred_at:
        raise ValueError("event metadata does not match its payload")
    cases[recommendation_id] = operation(case)


def _expect_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    return value


def _expect_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has unexpected fields: {sorted(set(value) ^ expected)}")


def _dataclass_keys(data_type: type[object]) -> set[str]:
    return {field.name for field in fields(data_type)}


def _parse_datetime(value: object, name: str) -> datetime:
    if type(value) is not str:
        raise TypeError(f"{name} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO datetime string") from exc
    return _require_aware(name, parsed)


def _parse_optional_datetime(value: object, name: str) -> datetime | None:
    return None if value is None else _parse_datetime(value, name)


def _decode_recommendation(value: object) -> Recommendation:
    raw = _expect_mapping(value, "recommendation")
    _expect_keys(raw, _dataclass_keys(Recommendation), "recommendation")
    return Recommendation(
        recommendation_id=raw["recommendation_id"],
        site_id=raw["site_id"],
        deployment_id=raw["deployment_id"],
        issued_at=_parse_datetime(raw["issued_at"], "issued_at"),
        execute_before=_parse_datetime(raw["execute_before"], "execute_before"),
        action=RecommendationAction(raw["action"]),
        target_robot_id=raw["target_robot_id"],
        policy_id=raw["policy_id"],
        policy_version=raw["policy_version"],
        decision_trace_id=raw["decision_trace_id"],
        expected_stockout_without_action_minutes=raw[
            "expected_stockout_without_action_minutes"
        ],
        expected_stockout_with_action_minutes=raw[
            "expected_stockout_with_action_minutes"
        ],
        protected_through_horizon=raw["protected_through_horizon"],
        projected_stockout_delay_minutes=raw["projected_stockout_delay_minutes"],
        summary=raw["summary"],
    )


def _decode_trace(value: object) -> DecisionTrace:
    raw = _expect_mapping(value, "decision_trace")
    _expect_keys(raw, _dataclass_keys(DecisionTrace), "decision_trace")
    inbound_raw = raw["committed_inbound_batches"]
    if type(inbound_raw) not in (list, tuple):
        raise TypeError("committed_inbound_batches must be an array")
    inbound: list[InboundBallBatch] = []
    for item in inbound_raw:
        batch = _expect_mapping(item, "inbound batch")
        _expect_keys(batch, _dataclass_keys(InboundBallBatch), "inbound batch")
        inbound.append(InboundBallBatch(**batch))
    candidates_raw = raw["candidates"]
    if type(candidates_raw) not in (list, tuple):
        raise TypeError("candidates must be an array")
    candidates: list[RobotCandidateTrace] = []
    for item in candidates_raw:
        candidate = dict(_expect_mapping(item, "candidate"))
        _expect_keys(candidate, _dataclass_keys(RobotCandidateTrace), "candidate")
        candidate["status"] = RobotStatus(candidate["status"])
        candidate["capabilities"] = (
            None
            if candidate["capabilities"] is None
            else tuple(candidate["capabilities"])
        )
        candidate["exclusion_reasons"] = tuple(candidate["exclusion_reasons"])
        candidates.append(RobotCandidateTrace(**candidate))
    forecast = raw["forecast_demand_balls_per_minute"]
    policy_config_raw = _expect_mapping(raw["policy_config"], "policy_config")
    _expect_keys(
        policy_config_raw,
        _dataclass_keys(BallAvailabilityPolicyConfig),
        "policy_config",
    )
    policy_config = BallAvailabilityPolicyConfig(**policy_config_raw)
    return DecisionTrace(
        **{
            **raw,
            "policy_config": policy_config,
            "observed_at": _parse_datetime(raw["observed_at"], "observed_at"),
            "forecast_demand_balls_per_minute": (
                None if forecast is None else tuple(forecast)
            ),
            "demand_rates_used_balls_per_minute": tuple(
                raw["demand_rates_used_balls_per_minute"]
            ),
            "committed_inbound_batches": tuple(inbound),
            "candidates": tuple(candidates),
            "missing_data_reasons": tuple(raw["missing_data_reasons"]),
            "rationale": tuple(raw["rationale"]),
        }
    )


def _decode_modified(value: object) -> ModifiedRecommendation:
    raw = _expect_mapping(value, "modified_recommendation")
    _expect_keys(raw, _dataclass_keys(ModifiedRecommendation), "modified_recommendation")
    return ModifiedRecommendation(
        modified_recommendation_id=raw["modified_recommendation_id"],
        original_recommendation=_decode_recommendation(raw["original_recommendation"]),
        modified_at=_parse_datetime(raw["modified_at"], "modified_at"),
        operator_id=raw["operator_id"],
        action=RecommendationAction(raw["action"]),
        target_robot_id=raw["target_robot_id"],
        execute_before=_parse_datetime(raw["execute_before"], "execute_before"),
        reason_code=raw["reason_code"],
        note=raw["note"],
    )


def _decode_response(value: object) -> RecommendationResponse:
    raw = _expect_mapping(value, "response")
    _expect_keys(raw, _dataclass_keys(RecommendationResponse), "response")
    modified = raw["modified_recommendation"]
    return RecommendationResponse(
        response_id=raw["response_id"],
        recommendation_id=raw["recommendation_id"],
        responded_at=_parse_datetime(raw["responded_at"], "responded_at"),
        operator_id=raw["operator_id"],
        kind=ResponseKind(raw["kind"]),
        reason_code=raw["reason_code"],
        note=raw["note"],
        modified_recommendation=(None if modified is None else _decode_modified(modified)),
    )


def _decode_execution_request(value: object) -> ExecutionRequest:
    raw = _expect_mapping(value, "execution_request")
    _expect_keys(raw, _dataclass_keys(ExecutionRequest), "execution_request")
    return ExecutionRequest(
        **{
            **raw,
            "requested_at": _parse_datetime(raw["requested_at"], "requested_at"),
        }
    )


def _decode_execution_acknowledgement(value: object) -> ExecutionAcknowledgement:
    raw = _expect_mapping(value, "execution_acknowledgement")
    _expect_keys(
        raw,
        _dataclass_keys(ExecutionAcknowledgement),
        "execution_acknowledgement",
    )
    return ExecutionAcknowledgement(
        **{
            **raw,
            "acknowledged_at": _parse_datetime(
                raw["acknowledged_at"], "acknowledged_at"
            ),
        }
    )


def _decode_outcome(value: object) -> RecommendationOutcome:
    raw = _expect_mapping(value, "outcome")
    _expect_keys(raw, _dataclass_keys(RecommendationOutcome), "outcome")
    return RecommendationOutcome(
        **{
            **raw,
            "recorded_at": _parse_datetime(raw["recorded_at"], "recorded_at"),
            "actual_start_at": _parse_optional_datetime(
                raw["actual_start_at"], "actual_start_at"
            ),
            "actual_completed_at": _parse_optional_datetime(
                raw["actual_completed_at"], "actual_completed_at"
            ),
        }
    )
