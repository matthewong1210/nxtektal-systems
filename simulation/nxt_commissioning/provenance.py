"""Immutable provenance contracts for commissioned facility facts.

Commissioning records are authoritative inputs, so provenance is explicit:
there is no implicit source, capture time, operator, or placeholder coercion.
The module is stdlib-only and has no dependency on any runtime Site OS layer.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse


class ProvenanceSource(str, Enum):
    """How a commissioned fact was established."""

    MANUFACTURER_SPECIFICATION = "manufacturer_specification"
    MANUAL_MEASUREMENT = "manual_measurement"
    OPERATOR_INPUT = "operator_input"
    SURVEY_DATA = "survey_data"
    SENSOR_CALIBRATION = "sensor_calibration"
    IMPORTED_RECORD = "imported_record"


def _mapping_with_exact_keys(
    data: object,
    expected: frozenset[str],
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a mapping")
    if any(not isinstance(key, str) for key in data):
        raise ValueError(f"{label} keys must be strings")
    keys = set(data)
    missing = sorted(expected - keys)
    unknown = sorted(keys - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing keys: {missing}")
        if unknown:
            details.append(f"unknown keys: {unknown}")
        raise ValueError(f"invalid {label}: {'; '.join(details)}")
    return data


def _nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain surrounding whitespace")
    return value


def _optional_nonempty_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field_name)


def _validate_timestamp(value: object) -> str:
    captured_at = _nonempty_string(value, "captured_at")
    if "T" not in captured_at:
        raise ValueError("captured_at must be an ISO 8601 date-time containing 'T'")
    candidate = captured_at[:-1] + "+00:00" if captured_at.endswith("Z") else captured_at
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(
            "captured_at must be a valid timezone-aware ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("captured_at must include a UTC offset or 'Z'")
    return captured_at


def _validate_evidence_uri(value: object) -> str | None:
    evidence_uri = _optional_nonempty_string(value, "evidence_uri")
    if evidence_uri is None:
        return None
    if any(character.isspace() for character in evidence_uri):
        raise ValueError("evidence_uri must not contain whitespace")
    parsed = urlparse(evidence_uri)
    if not parsed.scheme or not (parsed.netloc or parsed.path):
        raise ValueError("evidence_uri must be an absolute URI with a scheme")
    return evidence_uri


@dataclass(frozen=True, slots=True)
class Provenance:
    """Evidence identifying where and when one commissioned fact came from."""

    source_type: ProvenanceSource
    source_id: str
    captured_at: str
    captured_by: str
    evidence_uri: str | None
    notes: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, ProvenanceSource):
            raise ValueError("source_type must be a ProvenanceSource")
        _nonempty_string(self.source_id, "source_id")
        _validate_timestamp(self.captured_at)
        _nonempty_string(self.captured_by, "captured_by")
        _validate_evidence_uri(self.evidence_uri)
        _optional_nonempty_string(self.notes, "notes")

    def to_dict(self) -> dict[str, object]:
        return {
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "captured_at": self.captured_at,
            "captured_by": self.captured_by,
            "evidence_uri": self.evidence_uri,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "Provenance":
        payload = _mapping_with_exact_keys(
            data,
            frozenset(
                {
                    "source_type",
                    "source_id",
                    "captured_at",
                    "captured_by",
                    "evidence_uri",
                    "notes",
                }
            ),
            "Provenance",
        )
        source_raw = _nonempty_string(payload["source_type"], "source_type")
        try:
            source_type = ProvenanceSource(source_raw)
        except ValueError as exc:
            allowed = sorted(source.value for source in ProvenanceSource)
            raise ValueError(
                f"unknown provenance source {source_raw!r}; expected one of {allowed}"
            ) from exc
        return cls(
            source_type=source_type,
            source_id=_nonempty_string(payload["source_id"], "source_id"),
            captured_at=_validate_timestamp(payload["captured_at"]),
            captured_by=_nonempty_string(payload["captured_by"], "captured_by"),
            evidence_uri=_validate_evidence_uri(payload["evidence_uri"]),
            notes=_optional_nonempty_string(payload["notes"], "notes"),
        )


@dataclass(frozen=True, slots=True)
class MeasuredValue:
    """One numeric commissioned fact with unit and inseparable provenance."""

    name: str
    value: int | float
    unit: str
    provenance: Provenance

    def __post_init__(self) -> None:
        _nonempty_string(self.name, "name")
        if type(self.value) not in (int, float):
            raise ValueError("value must be an int or float; booleans are invalid")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("value must be finite")
        _nonempty_string(self.unit, "unit")
        if not isinstance(self.provenance, Provenance):
            raise ValueError("provenance must be a Provenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "MeasuredValue":
        payload = _mapping_with_exact_keys(
            data,
            frozenset({"name", "value", "unit", "provenance"}),
            "MeasuredValue",
        )
        value = payload["value"]
        if type(value) not in (int, float):
            raise ValueError("value must be an int or float; booleans are invalid")
        provenance_payload = payload["provenance"]
        return cls(
            name=_nonempty_string(payload["name"], "name"),
            value=value,
            unit=_nonempty_string(payload["unit"], "unit"),
            provenance=Provenance.from_dict(provenance_payload),
        )


__all__ = ["MeasuredValue", "Provenance", "ProvenanceSource"]
