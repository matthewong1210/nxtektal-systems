"""The Observation contract: the Site OS input boundary.

One ``Observation`` is one channel reading from any source — hardware
sensor, simulation, external system (POS, tee sheet, weather API), or
human input — with explicit timing, provenance, and confidence. Missing
data is a real Observation with ``status=MISSING``, never an absent key.

Channel naming: ``<family>.<asset_id>.<measure>`` —
``inventory.dispenser.count`` / ``inventory.station.<sid>.buffer_balls``
(load cells), ``scan.zone.<zid>.balls`` (facility scanning),
``robot.<rid>.<field>`` (fleet telemetry), ``wash.washer.wip``,
``staff.site.{busy,queued}``, ``charger.site.queue_length``,
``zone.<zid>.is_open`` / ``station.<sid>.{is_open,docked,queue_length}``
(facility systems). ``env.*`` is a RESERVED namespace for future
weather/environment feeds — nothing synthetic is faked for it because the
simulator has no dynamic environment state.

Two timestamps are deliberate and locked: ``sample_timestamp_s`` (when the
quantity was measured) vs ``available_timestamp_s`` (when the reading
became visible). Real load cells and scan rigs report late; conflating
the two corrupts staleness math unrecoverably. All times are simulation
time (seconds); this module never touches wall clocks.

This module is a pure-Python contract: stdlib only, importable without
the simulation stack.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional, Union

Value = Union[float, int, str, bool, None]


class ObservationStatus(str, Enum):
    OK = "ok"
    STALE = "stale"
    MISSING = "missing"


class SourceType(str, Enum):
    """What kind of thing produced the observation.

    The synthetic bank always emits SIMULATION — synthetic data never
    claims to be a SENSOR; real hardware adapters will."""

    SENSOR = "sensor"
    SIMULATION = "simulation"
    EXTERNAL_SYSTEM = "external_system"
    HUMAN = "human"


@dataclass(frozen=True)
class Observation:
    channel: str
    value: Value
    sample_timestamp_s: float
    available_timestamp_s: float
    status: ObservationStatus
    source_type: SourceType
    source_id: str
    calibration_id: str
    confidence: float  # 0..1, placeholder heuristic (source: placeholder)
    seq: int = 0       # per-channel monotonic counter, drives observation_id

    def __post_init__(self) -> None:
        if self.value is None and self.status is not ObservationStatus.MISSING:
            raise ValueError("value None requires status MISSING")
        if self.status is ObservationStatus.MISSING and self.value is not None:
            raise ValueError("MISSING observations must carry value None")
        if self.available_timestamp_s < self.sample_timestamp_s:
            raise ValueError(
                "available_timestamp_s must be >= sample_timestamp_s"
            )

    @property
    def observation_id(self) -> str:
        return f"{self.channel}:o{self.seq:06d}"

    def to_dict(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "channel": self.channel,
            "value": self.value,
            "sample_timestamp_s": self.sample_timestamp_s,
            "available_timestamp_s": self.available_timestamp_s,
            "status": self.status.value,
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "calibration_id": self.calibration_id,
            "confidence": round(self.confidence, 6),
            "seq": self.seq,
        }


@dataclass(frozen=True)
class ObservationFrame:
    """All observations visible at one instant, sorted by channel."""

    t_s: float
    observations: tuple[Observation, ...]

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(self.observations, key=lambda o: o.channel)
        )
        object.__setattr__(self, "observations", ordered)

    def by_channel(self) -> dict[str, Observation]:
        return {o.channel: o for o in self.observations}

    def to_dict(self) -> dict:
        return {
            "t_s": self.t_s,
            "observations": [o.to_dict() for o in self.observations],
        }


@dataclass(frozen=True)
class SiteConfig:
    """Static site identity and capacities — facts sensors cannot report.

    In simulation this is derived from the scenario (see
    ``bank.site_config_from_scenario``); in deployment it is the site's
    commissioning record."""

    scenario_name: str
    seed: int
    total_balls: int
    washer_throughput_bpm: float
    washer_batch_size: int
    charger_slots: int
    staff_capacity: int
    wet_ground_speed_multiplier: float
    open_minute: int
    close_minute: int
    forecast_bucket_minutes: int
    zone_ids: tuple[str, ...]
    station_ids: tuple[str, ...]
    robot_ids: tuple[str, ...]
    robot_payload_capacity: tuple[tuple[str, int], ...]
    station_buffer_capacity: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["robot_payload_capacity"] = dict(self.robot_payload_capacity)
        payload["station_buffer_capacity"] = dict(self.station_buffer_capacity)
        return payload


@dataclass(frozen=True)
class UpstreamInputs:
    """Non-sensor operational inputs from upstream services.

    Deployment: POS / forecasting services. Simulation: the sim's frozen
    forecast and metrics accumulator."""

    forecast_balls_per_minute: tuple[float, ...]
    demand_balls_total: int
    demand_balls_served: int
    stockout_minutes: float
    service_availability: float

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# The sensed-field whitelist: the single statement of which FacilityState
# fields are sourced from observations (vs SiteConfig / UpstreamInputs).
# Imported by the assembler, the docs, and the imperfection-diff test.
# ---------------------------------------------------------------------------

SENSED_FIELD_PREFIXES: frozenset[str] = frozenset(
    {
        "meta.t_s",
        "meta.minute_of_day",
        "meta.facility_open",
        "ball_flow.clean_available",
        "ball_flow.clean_sensed",
        "ball_flow.in_wash",
        "ball_flow.dirty_buffered",
        "ball_flow.on_field",
        "ball_flow.in_transit",
        "ball_flow.conserved",
        "washer.wip",
        "fleet.",
        "charging.in_use",
        "charging.queue_length",
        "staff.busy",
        "staff.queued_requests",
        "environment.zones_open",
        "environment.stations_open",
        "robots",
        "zones",
        "stations",
    }
)


def sensed_diff_paths(a: dict, b: dict, prefix: str = "") -> set[str]:
    """Dotted paths of leaves that differ between two nested dicts/lists."""
    paths: set[str] = set()
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            if key not in a or key not in b:
                paths.add(child_prefix)
            else:
                paths |= sensed_diff_paths(a[key], b[key], child_prefix)
        return paths
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            paths.add(prefix)
            return paths
        for index, (item_a, item_b) in enumerate(zip(a, b)):
            paths |= sensed_diff_paths(item_a, item_b, f"{prefix}[{index}]")
        return paths
    if a != b:
        paths.add(prefix)
    return paths
