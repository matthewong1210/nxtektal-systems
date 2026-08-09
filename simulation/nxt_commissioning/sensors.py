"""Immutable commissioned sensor bindings.

Bindings describe how a physical or external source is expected to produce a
canonical telemetry channel.  They contain no readings or transport state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .provenance import Provenance


def _require_exact_keys(
    data: Mapping[str, object], expected: frozenset[str], contract: str
) -> None:
    if not isinstance(data, Mapping):
        raise TypeError(f"{contract} must be decoded from a mapping")
    if any(not isinstance(key, str) for key in data):
        raise ValueError(f"{contract} keys must be strings")
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"{contract} keys must be exactly {sorted(expected)}; "
            f"missing={missing}, extra={extra}"
        )


def _require_non_blank(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain surrounding whitespace")


def _require_optional_non_blank(value: object, field_name: str) -> None:
    if value is not None:
        _require_non_blank(value, field_name)


def _require_provenance(value: object, field_name: str = "provenance") -> None:
    if not isinstance(value, Provenance):
        raise TypeError(f"{field_name} must be Provenance")


def _enum_from_value(enum_type: type[Enum], value: object, field_name: str) -> Enum:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        choices = sorted(member.value for member in enum_type)
        raise ValueError(f"{field_name} must be one of {choices}; got {value!r}") from exc


def _parse_aware_iso8601(value: str, field_name: str) -> datetime:
    if "T" not in value:
        raise ValueError(f"{field_name} must be an ISO-8601 date-time containing 'T'")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed


class SensorType(str, Enum):
    LOAD_CELL = "load_cell"
    PROXIMITY_SWITCH = "proximity_switch"
    LIDAR = "lidar"
    CAMERA = "camera"
    RFID_READER = "rfid_reader"
    BATTERY_MONITOR = "battery_monitor"
    ENCODER = "encoder"
    TEMPERATURE = "temperature"
    EXTERNAL_SYSTEM = "external_system"
    MANUAL_INPUT = "manual_input"
    OTHER = "other"


class CalibrationStatus(str, Enum):
    CALIBRATED = "calibrated"
    NOT_REQUIRED = "not_required"


@dataclass(frozen=True, slots=True)
class CalibrationInfo:
    """Calibration evidence, or an explicit declaration that it is unnecessary."""

    status: CalibrationStatus
    calibration_id: str | None
    calibrated_at: str | None
    valid_until: str | None
    method: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.status, CalibrationStatus):
            raise TypeError("status must be CalibrationStatus")
        _require_optional_non_blank(self.calibration_id, "calibration_id")
        _require_optional_non_blank(self.calibrated_at, "calibrated_at")
        _require_optional_non_blank(self.valid_until, "valid_until")
        _require_non_blank(self.method, "method")
        _require_provenance(self.provenance)

        if self.status is CalibrationStatus.CALIBRATED:
            required = {
                "calibration_id": self.calibration_id,
                "calibrated_at": self.calibrated_at,
                "valid_until": self.valid_until,
            }
            missing = sorted(name for name, value in required.items() if value is None)
            if missing:
                raise ValueError(
                    f"calibrated status requires fields: {', '.join(missing)}"
                )
            calibrated_at = _parse_aware_iso8601(
                self.calibrated_at, "calibrated_at"  # type: ignore[arg-type]
            )
            valid_until = _parse_aware_iso8601(
                self.valid_until, "valid_until"  # type: ignore[arg-type]
            )
            if valid_until <= calibrated_at:
                raise ValueError("valid_until must be later than calibrated_at")
        else:
            supplied = sorted(
                name
                for name, value in (
                    ("calibration_id", self.calibration_id),
                    ("calibrated_at", self.calibrated_at),
                    ("valid_until", self.valid_until),
                )
                if value is not None
            )
            if supplied:
                raise ValueError(
                    "not_required calibration must not supply calibrated fields: "
                    + ", ".join(supplied)
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "calibration_id": self.calibration_id,
            "calibrated_at": self.calibrated_at,
            "valid_until": self.valid_until,
            "method": self.method,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "CalibrationInfo":
        expected = frozenset(
            {
                "status",
                "calibration_id",
                "calibrated_at",
                "valid_until",
                "method",
                "provenance",
            }
        )
        _require_exact_keys(data, expected, cls.__name__)
        raw_provenance = data["provenance"]
        if not isinstance(raw_provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        return cls(
            status=_enum_from_value(
                CalibrationStatus, data["status"], "status"
            ),  # type: ignore[arg-type]
            calibration_id=data["calibration_id"],  # type: ignore[arg-type]
            calibrated_at=data["calibrated_at"],  # type: ignore[arg-type]
            valid_until=data["valid_until"],  # type: ignore[arg-type]
            method=data["method"],  # type: ignore[arg-type]
            provenance=Provenance.from_dict(raw_provenance),
        )


@dataclass(frozen=True, slots=True)
class SensorBinding:
    """Static mapping from a commissioned source to one telemetry channel."""

    sensor_id: str
    sensor_type: SensorType
    source: str
    channel: str
    associated_asset_id: str
    units: str
    calibration: CalibrationInfo
    provenance: Provenance

    def __post_init__(self) -> None:
        _require_non_blank(self.sensor_id, "sensor_id")
        if not isinstance(self.sensor_type, SensorType):
            raise TypeError("sensor_type must be SensorType")
        _require_non_blank(self.source, "source")
        _require_non_blank(self.channel, "channel")
        if any(character.isspace() for character in self.channel):
            raise ValueError("channel must not contain whitespace")
        _require_non_blank(self.associated_asset_id, "associated_asset_id")
        _require_non_blank(self.units, "units")
        if not isinstance(self.calibration, CalibrationInfo):
            raise TypeError("calibration must be CalibrationInfo")
        _require_provenance(self.provenance)

    def to_dict(self) -> dict[str, object]:
        return {
            "sensor_id": self.sensor_id,
            "sensor_type": self.sensor_type.value,
            "source": self.source,
            "channel": self.channel,
            "associated_asset_id": self.associated_asset_id,
            "units": self.units,
            "calibration": self.calibration.to_dict(),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "SensorBinding":
        expected = frozenset(
            {
                "sensor_id",
                "sensor_type",
                "source",
                "channel",
                "associated_asset_id",
                "units",
                "calibration",
                "provenance",
            }
        )
        _require_exact_keys(data, expected, cls.__name__)
        raw_calibration = data["calibration"]
        if not isinstance(raw_calibration, Mapping):
            raise TypeError("calibration must be a mapping")
        raw_provenance = data["provenance"]
        if not isinstance(raw_provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        return cls(
            sensor_id=data["sensor_id"],  # type: ignore[arg-type]
            sensor_type=_enum_from_value(
                SensorType, data["sensor_type"], "sensor_type"
            ),  # type: ignore[arg-type]
            source=data["source"],  # type: ignore[arg-type]
            channel=data["channel"],  # type: ignore[arg-type]
            associated_asset_id=data["associated_asset_id"],  # type: ignore[arg-type]
            units=data["units"],  # type: ignore[arg-type]
            calibration=CalibrationInfo.from_dict(raw_calibration),
            provenance=Provenance.from_dict(raw_provenance),
        )


__all__ = [
    "CalibrationInfo",
    "CalibrationStatus",
    "SensorBinding",
    "SensorType",
]
