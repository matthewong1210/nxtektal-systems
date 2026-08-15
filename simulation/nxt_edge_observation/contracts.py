"""Raw edge-device sample shapes, adapter-local profiles, and diagnostics.

Everything here is *pre-canonical*.  A raw sample is what an already-read
device payload looks like after a transport has decoded it; it is never
facility truth and never a second Observation contract.  The only
canonical output of this package is
:class:`nxt_telemetry.observations.Observation`.

Two kinds of adapter-local input live here and must not be mistaken for
commissioned facts:

* :class:`LoadCellProfile`, :class:`DigitalInputProfile`, and
  :class:`RobotStatusProfile` carry the *conversion coefficients* a
  device needs (tare, mass per ball, polarity, battery unit, declared
  vocabularies, staleness horizon).  The commissioned
  ``CalibrationInfo`` contract records calibration **identity and
  validity only** -- it has no numeric scale/offset block -- so these
  coefficients are supplied by the caller, carry their own explicit
  provenance string, and are checked against the commissioned
  ``calibration_id``.  See ``simulation/docs/edge_observation_v0.md``.
* :class:`EdgeAdapterReport` carries conversion diagnostics.  It is not a
  telemetry envelope, is not consumed by the assembler, and must never be
  fed to ``FacilityState``.

This module is stdlib-only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

EDGE_ADAPTER_VERSION = "nxt-edge-observation/adapter/v0"
ADAPTER_REPORT_SCHEMA = "nxt-edge-observation/adapter-report/v0"

# Confidence attached to each honest status (source: placeholder).  The
# values intentionally match the synthetic bank's placeholder table so a
# device-sourced frame and a simulated frame grade identically.
CONFIDENCE_BY_STATUS: dict[str, float] = {"ok": 1.0, "stale": 0.5, "missing": 0.0}


class EdgeAdapterError(ValueError):
    """A raw batch cannot be converted at all; the caller must fail closed."""


class RejectionCode(str, Enum):
    """Why one raw field did not become a trustworthy canonical value.

    These are *adapter* diagnostics.  They never replace
    ``ObservationStatus`` or the Site Runtime ``FailureCode`` taxonomy:
    every rejected field still yields an explicit ``MISSING`` Observation
    whenever its binding is known, so the existing runtime taxonomy makes
    the admission decision.
    """

    NO_BINDING = "no_binding"
    NO_SAMPLE = "no_sample"
    UNKNOWN_SOURCE = "unknown_source"
    IDENTITY_MISMATCH = "identity_mismatch"
    CALIBRATION_MISSING = "calibration_missing"
    CALIBRATION_MISMATCH = "calibration_mismatch"
    UNSUPPORTED_UNIT = "unsupported_unit"
    NON_FINITE_VALUE = "non_finite_value"
    VALUE_OUT_OF_RANGE = "value_out_of_range"
    UNKNOWN_VALUE = "unknown_value"
    FUTURE_SAMPLE = "future_sample"
    INCONSISTENT_TIMESTAMPS = "inconsistent_timestamps"
    DEVICE_FAULT = "device_fault"
    DEVICE_REPORTED_MISSING = "device_reported_missing"
    IMPOSSIBLE_STATE = "impossible_state"
    COORDINATE_FRAME_MISMATCH = "coordinate_frame_mismatch"
    DUPLICATE_CHANNEL = "duplicate_channel"
    UNSUPPORTED_CHANNEL = "unsupported_channel"


def _require_identifier(value: object, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise EdgeAdapterError(f"{field_name} must be a non-blank trimmed string")
    return value


def _require_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EdgeAdapterError(f"{field_name} must be a real number")
    number = float(value)
    if not math.isfinite(number):
        raise EdgeAdapterError(f"{field_name} must be finite")
    return number


def _require_non_negative(value: object, field_name: str) -> float:
    number = _require_finite(value, field_name)
    if number < 0.0:
        raise EdgeAdapterError(f"{field_name} must be non-negative")
    return number


# ---------------------------------------------------------------------------
# Adapter-local device profiles (NOT commissioned facts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Adapter-local conversion inputs for one commissioned sensor.

    ``calibration_id`` must equal the commissioned binding's calibration
    identity; a mismatch is a fail-closed rejection rather than a silent
    substitution.  ``provenance`` is a free-form but mandatory statement
    of where the coefficients came from, so a synthetic fixture constant
    can never be read as a measured product fact.
    """

    sensor_id: str
    calibration_id: str
    stale_after_s: float
    provenance: str

    def __post_init__(self) -> None:
        _require_identifier(self.sensor_id, "sensor_id")
        _require_identifier(self.calibration_id, "calibration_id")
        _require_identifier(self.provenance, "provenance")
        stale_after = _require_finite(self.stale_after_s, "stale_after_s")
        if stale_after <= 0.0:
            raise EdgeAdapterError("stale_after_s must be greater than zero")


@dataclass(frozen=True, slots=True)
class LoadCellProfile(DeviceProfile):
    """Mass-to-count conversion coefficients for one load cell.

    No universal golf-ball mass, tare, or capacity exists in this
    repository and none is introduced here: every coefficient is supplied
    per device and carries ``provenance``.
    """

    raw_unit: str
    tare_raw: float
    mass_per_ball_raw: float
    capacity_balls: int

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_identifier(self.raw_unit, "raw_unit")
        if self.raw_unit not in MASS_UNIT_TO_KG:
            raise EdgeAdapterError(
                f"raw_unit {self.raw_unit!r} is not a supported mass unit; "
                f"supported units are {sorted(MASS_UNIT_TO_KG)}"
            )
        _require_finite(self.tare_raw, "tare_raw")
        mass_per_ball = _require_finite(self.mass_per_ball_raw, "mass_per_ball_raw")
        if mass_per_ball <= 0.0:
            raise EdgeAdapterError("mass_per_ball_raw must be greater than zero")
        if isinstance(self.capacity_balls, bool) or not isinstance(
            self.capacity_balls, int
        ):
            raise EdgeAdapterError("capacity_balls must be an integer")
        if self.capacity_balls <= 0:
            raise EdgeAdapterError("capacity_balls must be greater than zero")


@dataclass(frozen=True, slots=True)
class DigitalInputProfile(DeviceProfile):
    """Electrical polarity for one commissioned digital input.

    ``active_low`` describes the wiring only.  The adapter emits the
    *logical* state; what that state means is the commissioned binding's
    channel, never an adapter-side reinterpretation.
    """

    active_low: bool

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.active_low) is not bool:
            raise EdgeAdapterError("active_low must be a boolean")


@dataclass(frozen=True, slots=True)
class DigitalDeviceProfile:
    """Device-level declarations for one remote digital-I/O unit.

    ``raw_only_inputs`` are points the operator has explicitly declared as
    having no canonical destination -- washer running/fault, basket
    present, lift limits, cabinet door.  Declaring them keeps the coverage
    gap visible; an *undeclared* input with no commissioned binding is an
    unknown bit and is rejected.

    ``mutually_exclusive_inputs`` names pairs the commissioned equipment
    makes physically impossible together (a lift cannot be at both
    limits).  Observing both asserted is evidence of a broken device, not
    a state to publish.
    """

    device_id: str
    provenance: str
    raw_only_inputs: tuple[str, ...] = ()
    mutually_exclusive_inputs: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.device_id, "device_id")
        _require_identifier(self.provenance, "provenance")
        if not isinstance(self.raw_only_inputs, tuple):
            raise EdgeAdapterError("raw_only_inputs must be a tuple")
        for item in self.raw_only_inputs:
            _require_identifier(item, "raw_only_inputs entry")
        if len(set(self.raw_only_inputs)) != len(self.raw_only_inputs):
            raise EdgeAdapterError("raw_only_inputs must not repeat a name")
        if not isinstance(self.mutually_exclusive_inputs, tuple):
            raise EdgeAdapterError("mutually_exclusive_inputs must be a tuple")
        for pair in self.mutually_exclusive_inputs:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise EdgeAdapterError(
                    "mutually_exclusive_inputs entries must be input-name pairs"
                )
            first, second = pair
            _require_identifier(first, "mutually_exclusive_inputs entry")
            _require_identifier(second, "mutually_exclusive_inputs entry")
            if first == second:
                raise EdgeAdapterError(
                    "a mutually exclusive pair must name two different inputs"
                )


@dataclass(frozen=True, slots=True)
class RobotStatusProfile(DeviceProfile):
    """Declared units and vocabularies for one commissioned robot.

    ``battery_unit`` must be declared: percent and fraction are never
    inferred from the magnitude of a reading.  The activity/health/
    location vocabularies are supplied by the caller so this package
    never carries a private copy of a downstream enum, and an
    unrecognised value fails closed here instead of poisoning state
    assembly.
    """

    robot_id: str
    battery_unit: str
    allowed_activities: tuple[str, ...]
    allowed_health: tuple[str, ...]
    allowed_locations: tuple[str, ...]
    allowed_zones: tuple[str, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_identifier(self.robot_id, "robot_id")
        if self.battery_unit not in BATTERY_UNITS:
            raise EdgeAdapterError(
                f"battery_unit must be one of {sorted(BATTERY_UNITS)}; "
                f"got {self.battery_unit!r}"
            )
        for name in (
            "allowed_activities",
            "allowed_health",
            "allowed_locations",
            "allowed_zones",
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values:
                raise EdgeAdapterError(f"{name} must be a non-empty tuple")
            for item in values:
                _require_identifier(item, f"{name} entry")
            if len(set(values)) != len(values):
                raise EdgeAdapterError(f"{name} must not repeat a value")


MASS_UNIT_TO_KG: dict[str, float] = {"kg": 1.0, "g": 0.001}
BATTERY_UNITS: frozenset[str] = frozenset({"fraction", "percent"})


# ---------------------------------------------------------------------------
# Raw device samples (already read by some transport; never a connection)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawSampleTiming:
    """When a raw measurement was taken and when it became visible."""

    sample_timestamp_s: float
    available_timestamp_s: float

    def __post_init__(self) -> None:
        sample = _require_non_negative(
            self.sample_timestamp_s, "sample_timestamp_s"
        )
        available = _require_non_negative(
            self.available_timestamp_s, "available_timestamp_s"
        )
        if available < sample:
            raise EdgeAdapterError(
                "available_timestamp_s must be >= sample_timestamp_s"
            )


@dataclass(frozen=True, slots=True)
class LoadCellSample:
    """One already-read load-cell measurement.

    ``raw_value`` is the device reading in ``raw_unit``.  ``device_status``
    is the device's own self-report; ``ok`` is the only value that can
    become an ``OK`` Observation.
    """

    sensor_id: str
    timing: RawSampleTiming
    raw_value: float | None
    raw_unit: str
    device_status: str
    calibration_id: str | None
    diagnostic_code: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.sensor_id, "sensor_id")
        if not isinstance(self.timing, RawSampleTiming):
            raise EdgeAdapterError("timing must be a RawSampleTiming")
        _require_identifier(self.raw_unit, "raw_unit")
        _require_identifier(self.device_status, "device_status")
        if self.calibration_id is not None:
            _require_identifier(self.calibration_id, "calibration_id")
        if self.diagnostic_code is not None:
            _require_identifier(self.diagnostic_code, "diagnostic_code")


@dataclass(frozen=True, slots=True)
class DigitalInputSample:
    """One already-read digital-input bit.

    ``raw_state`` must be a real boolean or ``None``.  No truthy string,
    integer, or empty-value coercion is ever performed.
    """

    sensor_id: str
    input_name: str
    raw_state: bool | None

    def __post_init__(self) -> None:
        _require_identifier(self.sensor_id, "sensor_id")
        _require_identifier(self.input_name, "input_name")
        if self.raw_state is not None and type(self.raw_state) is not bool:
            raise EdgeAdapterError("raw_state must be a boolean or None")


@dataclass(frozen=True, slots=True)
class DigitalIOSnapshot:
    """One already-read digital-input snapshot from a single I/O device."""

    device_id: str
    timing: RawSampleTiming
    inputs: tuple[DigitalInputSample, ...]
    device_status: str

    def __post_init__(self) -> None:
        _require_identifier(self.device_id, "device_id")
        if not isinstance(self.timing, RawSampleTiming):
            raise EdgeAdapterError("timing must be a RawSampleTiming")
        _require_identifier(self.device_status, "device_status")
        if not isinstance(self.inputs, tuple):
            raise EdgeAdapterError("inputs must be a tuple")
        for item in self.inputs:
            if not isinstance(item, DigitalInputSample):
                raise EdgeAdapterError("inputs must contain DigitalInputSample items")
        names = [item.input_name for item in self.inputs]
        if len(set(names)) != len(names):
            raise EdgeAdapterError("digital snapshot repeats an input name")


@dataclass(frozen=True, slots=True)
class RobotStatusSample:
    """One already-received robot status report.

    Every field is optional evidence.  ``estop_latched`` is telemetry
    only: this package can neither set, clear, nor reset an emergency
    stop, and exposes no command surface of any kind.
    """

    robot_id: str
    timing: RawSampleTiming
    device_status: str
    battery: float | None = None
    payload_balls: int | None = None
    activity: str | None = None
    health: str | None = None
    location: str | None = None
    destination: str | None = None
    assigned_zone: str | None = None
    estop_latched: bool | None = None
    awaiting_human: bool | None = None
    fault_code: str | None = None
    position_x_m: float | None = None
    position_y_m: float | None = None
    coordinate_frame: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.robot_id, "robot_id")
        if not isinstance(self.timing, RawSampleTiming):
            raise EdgeAdapterError("timing must be a RawSampleTiming")
        _require_identifier(self.device_status, "device_status")
        for name in ("estop_latched", "awaiting_human"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise EdgeAdapterError(f"{name} must be a boolean or None")
        if self.payload_balls is not None and (
            isinstance(self.payload_balls, bool)
            or not isinstance(self.payload_balls, int)
        ):
            raise EdgeAdapterError("payload_balls must be an integer or None")


@dataclass(frozen=True, slots=True)
class RawSampleBatch:
    """Every raw device sample already read for one observation cycle.

    ``cycle_index`` is the caller's deterministic cycle counter; it drives
    the per-channel ``Observation.seq`` and therefore ``observation_id``.
    No wall clock, UUID, or RNG participates in canonical identity.
    """

    cycle_index: int
    frame_t_s: float
    load_cells: tuple[LoadCellSample, ...] = ()
    digital_io: tuple[DigitalIOSnapshot, ...] = ()
    robots: tuple[RobotStatusSample, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.cycle_index, bool) or not isinstance(
            self.cycle_index, int
        ):
            raise EdgeAdapterError("cycle_index must be an integer")
        if self.cycle_index < 0:
            raise EdgeAdapterError("cycle_index must be non-negative")
        _require_non_negative(self.frame_t_s, "frame_t_s")
        for name, expected in (
            ("load_cells", LoadCellSample),
            ("digital_io", DigitalIOSnapshot),
            ("robots", RobotStatusSample),
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise EdgeAdapterError(f"{name} must be a tuple")
            for item in values:
                if not isinstance(item, expected):
                    raise EdgeAdapterError(
                        f"{name} must contain {expected.__name__} items"
                    )


# ---------------------------------------------------------------------------
# Adapter diagnostics (never facility truth)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcceptedField:
    """One raw field that became a canonical Observation."""

    sensor_id: str
    channel: str
    raw_field: str
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sensor_id": self.sensor_id,
            "channel": self.channel,
            "raw_field": self.raw_field,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RejectedField:
    """One raw field that could not be trusted, and exactly why."""

    sensor_id: str
    channel: str | None
    raw_field: str
    code: RejectionCode
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sensor_id": self.sensor_id,
            "channel": self.channel,
            "raw_field": self.raw_field,
            "code": self.code.value,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class UnmappedField:
    """One raw field with no canonical destination in this repository.

    Recording it is deliberate: a device field that the canonical
    contract cannot represent is a coverage gap to report, never a reason
    to widen ``FacilityState`` or invent a channel.
    """

    sensor_id: str
    raw_field: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sensor_id": self.sensor_id,
            "raw_field": self.raw_field,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class EdgeAdapterReport:
    """Conversion diagnostics for one raw batch.

    Deliberately separate from ``AssemblyReport`` and from
    ``FacilitySnapshotEnvelope``: nothing downstream consumes this as
    state, and no assembler reads it.
    """

    schema: str
    adapter_version: str
    cycle_index: int
    frame_t_s: float
    accepted: tuple[AcceptedField, ...]
    rejected: tuple[RejectedField, ...]
    unmapped: tuple[UnmappedField, ...]

    @property
    def has_rejections(self) -> bool:
        return bool(self.rejected)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "adapter_version": self.adapter_version,
            "cycle_index": self.cycle_index,
            "frame_t_s": self.frame_t_s,
            "accepted": [item.to_dict() for item in self.accepted],
            "rejected": [item.to_dict() for item in self.rejected],
            "unmapped": [item.to_dict() for item in self.unmapped],
        }


__all__ = [
    "ADAPTER_REPORT_SCHEMA",
    "BATTERY_UNITS",
    "CONFIDENCE_BY_STATUS",
    "EDGE_ADAPTER_VERSION",
    "MASS_UNIT_TO_KG",
    "AcceptedField",
    "DeviceProfile",
    "DigitalDeviceProfile",
    "DigitalIOSnapshot",
    "DigitalInputProfile",
    "DigitalInputSample",
    "EdgeAdapterError",
    "EdgeAdapterReport",
    "LoadCellProfile",
    "LoadCellSample",
    "RawSampleBatch",
    "RawSampleTiming",
    "RejectedField",
    "RejectionCode",
    "RobotStatusProfile",
    "RobotStatusSample",
    "UnmappedField",
]
