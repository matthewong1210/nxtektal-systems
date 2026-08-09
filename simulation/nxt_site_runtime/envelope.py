"""Deterministic publication envelope around canonical FacilityState."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from nxt_facility.state import FacilityState

if TYPE_CHECKING:
    from nxt_telemetry.assemble import AssemblyReport
else:
    AssemblyReport = Any


SCHEMA_VERSION = "nxt-site-runtime/facility-snapshot/v1"
_SOURCE_STATUSES = frozenset({"ok", "stale", "missing"})


def _finite_fraction(name: str, value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError(f"{name} must be finite and in [0, 1]")


def _finite_timestamp(name: str, value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be finite and non-negative")


def _trimmed(name: str, value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-blank trimmed string")


def canonical_json(payload: dict) -> str:
    """Canonical JSON used by IDs and verification tests."""
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True, order=True)
class SourceReference:
    """Structured provenance for one observation or upstream input snapshot."""

    source_type: str
    source_id: str
    channel: str
    reference_id: str
    sample_timestamp_s: float
    available_timestamp_s: float
    confidence: float
    status: str
    calibration_id: str

    def __post_init__(self) -> None:
        for name in (
            "source_type",
            "source_id",
            "channel",
            "reference_id",
            "calibration_id",
        ):
            _trimmed(name, getattr(self, name))
        if not isinstance(self.status, str) or self.status not in _SOURCE_STATUSES:
            raise ValueError(f"status must be one of {sorted(_SOURCE_STATUSES)}")
        _finite_timestamp("sample_timestamp_s", self.sample_timestamp_s)
        _finite_timestamp("available_timestamp_s", self.available_timestamp_s)
        if self.available_timestamp_s < self.sample_timestamp_s:
            raise ValueError(
                "available_timestamp_s must be >= sample_timestamp_s"
            )
        _finite_fraction("confidence", self.confidence)

    @property
    def identity_key(self) -> tuple[str, str, str, str]:
        """Stable provenance identity, independent of asserted quality fields."""
        return (
            self.source_type,
            self.source_id,
            self.channel,
            self.reference_id,
        )

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "channel": self.channel,
            "reference_id": self.reference_id,
            "sample_timestamp_s": self.sample_timestamp_s,
            "available_timestamp_s": self.available_timestamp_s,
            "confidence": self.confidence,
            "status": self.status,
            "calibration_id": self.calibration_id,
        }


@dataclass(frozen=True)
class RuntimeQuality:
    """Quality that spans telemetry assembly and non-sensor upstream inputs."""

    assembly_confidence: float
    upstream_confidence: float
    effective_confidence: float

    def __post_init__(self) -> None:
        _finite_fraction("assembly_confidence", self.assembly_confidence)
        _finite_fraction("upstream_confidence", self.upstream_confidence)
        _finite_fraction("effective_confidence", self.effective_confidence)
        expected = min(self.assembly_confidence, self.upstream_confidence)
        if self.effective_confidence != expected:
            raise ValueError(
                "effective_confidence must equal the minimum input confidence"
            )

    def to_dict(self) -> dict:
        return {
            "assembly_confidence": self.assembly_confidence,
            "upstream_confidence": self.upstream_confidence,
            "effective_confidence": self.effective_confidence,
        }


@dataclass(frozen=True)
class FacilitySnapshotEnvelope:
    """Publishable metadata plus the exact existing FacilityState object."""

    site_id: str
    deployment_id: str
    observation_timestamp_s: float
    sequence_number: int
    facility_state: FacilityState
    assembly_report: AssemblyReport
    runtime_quality: RuntimeQuality
    source_references: tuple[SourceReference, ...]
    schema_version: str = SCHEMA_VERSION
    _envelope_id: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("site_id", "deployment_id", "schema_version"):
            _trimmed(name, getattr(self, name))
        if (
            isinstance(self.sequence_number, bool)
            or not isinstance(self.sequence_number, int)
            or self.sequence_number < 0
        ):
            raise ValueError("sequence_number must be a non-negative integer")
        _finite_timestamp("observation_timestamp_s", self.observation_timestamp_s)
        if not isinstance(self.source_references, tuple):
            raise ValueError("source_references must be a tuple")
        references = self.source_references
        if not references or any(
            not isinstance(reference, SourceReference) for reference in references
        ):
            raise ValueError(
                "source_references must contain SourceReference instances"
            )
        if len(references) != len(
            {reference.identity_key for reference in references}
        ):
            raise ValueError("source reference identities must be unique")
        object.__setattr__(self, "source_references", tuple(sorted(references)))
        from nxt_telemetry.assemble import AssemblyReport

        if type(self.facility_state) is not FacilityState:
            raise ValueError("facility_state must be the canonical FacilityState")
        if type(self.assembly_report) is not AssemblyReport:
            raise ValueError("assembly_report must be the canonical AssemblyReport")
        digest = hashlib.sha256(canonical_json(self._identity_payload()).encode())
        object.__setattr__(self, "_envelope_id", f"fse:{digest.hexdigest()}")

    def _identity_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "site_id": self.site_id,
            "deployment_id": self.deployment_id,
            "observation_timestamp_s": self.observation_timestamp_s,
            "sequence_number": self.sequence_number,
            # Existing public serializers intentionally round display values.
            # Replay identity must retain exact immutable fields so distinct
            # observations can never collapse onto one idempotency key.
            "facility_state": asdict(self.facility_state),
            "assembly_report": asdict(self.assembly_report),
            "runtime_quality": self.runtime_quality.to_dict(),
            "source_references": [
                reference.to_dict() for reference in self.source_references
            ],
        }

    @property
    def envelope_id(self) -> str:
        return self._envelope_id

    @property
    def payload_reference(self) -> str:
        return f"{self.envelope_id}#facility_state"

    def metadata_dict(self) -> dict:
        return {
            "envelope_id": self.envelope_id,
            "schema_version": self.schema_version,
            "site_id": self.site_id,
            "deployment_id": self.deployment_id,
            "observation_timestamp_s": self.observation_timestamp_s,
            "sequence_number": self.sequence_number,
            "payload_reference": self.payload_reference,
            "assembly_report": self.assembly_report.to_dict(),
            "runtime_quality": self.runtime_quality.to_dict(),
            "source_references": [
                reference.to_dict() for reference in self.source_references
            ],
        }

    def to_dict(self) -> dict:
        payload = self.metadata_dict()
        payload["facility_state"] = self.facility_state.to_dict()
        return payload
