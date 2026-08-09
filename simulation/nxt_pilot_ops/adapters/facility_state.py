"""Read-only adapters for the repository's real ``FacilityState`` contract.

The live adapter accepts a frozen :class:`nxt_facility.state.FacilityState`
plus identity and clock context that the upstream object does not carry.  The
offline reader accepts the native two-file capture written by
``scripts/facility_twin_capture.py``: raw ``FacilityState.to_dict()`` JSONL
records plus ``stream.meta.json``.

No upstream value is written back, and unavailable safety facts stay
unavailable.  In particular, the baseline does not expose current demand,
collector capability, collector ETA/yield, global collection permission, or
washer availability.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# This is the only production boundary allowed to import the upstream state
# contract.  Import the pure state module, never the simulator or twin package.
from nxt_facility.state import BallFlow, FacilityState

from ..contracts import OperationalSnapshot, RobotOperationalState, RobotStatus


NATIVE_STREAM_SCHEMA = "nxt-range-twin/facility-state-stream/v1"

_NATIVE_META_KEYS = frozenset(
    {
        "schema",
        "site_id",
        "deployment_id",
        "episode_id",
        "scenario_name",
        "seed",
        "policy",
        "policy_version",
        "control_interval_s",
        "every_steps",
        "n_records",
        "simulator_version",
        "git_commit",
        "disclaimer",
    }
)
_STATE_KEYS = frozenset(
    {
        "meta",
        "ball_flow",
        "washer",
        "demand",
        "fleet",
        "charging",
        "staff",
        "environment",
        "robots",
        "zones",
        "stations",
    }
)
_META_KEYS = frozenset(
    {"t_s", "minute_of_day", "facility_open", "scenario_name", "seed"}
)
_BALL_FLOW_KEYS = frozenset(
    {
        "total_balls",
        "clean_available",
        "clean_sensed",
        "in_wash",
        "dirty_buffered",
        "on_field",
        "in_transit",
        "conserved",
    }
)
_WASHER_KEYS = frozenset(
    {"throughput_balls_per_minute", "batch_size_balls", "wip"}
)
_DEMAND_KEYS = frozenset(
    {
        "forecast_balls_per_minute",
        "forecast_bucket_minutes",
        "minutes_to_close",
        "demand_balls_total",
        "demand_balls_served",
        "stockout_minutes",
        "service_availability",
    }
)
_FLEET_KEYS = frozenset(
    {"total", "operable", "inoperative", "charging", "awaiting_human"}
)
_CHARGING_KEYS = frozenset({"slots", "in_use", "queue_length"})
_STAFF_KEYS = frozenset({"capacity", "busy", "queued_requests"})
_ENVIRONMENT_KEYS = frozenset(
    {
        "wet_ground_speed_multiplier",
        "zones_open",
        "zones_total",
        "stations_open",
        "stations_total",
    }
)
_ROBOT_KEYS = frozenset(
    {
        "robot_id",
        "activity",
        "health",
        "battery_frac",
        "payload_balls",
        "payload_capacity_balls",
        "location",
        "destination",
        "assigned_zone",
        "estop_latched",
        "awaiting_human",
    }
)
_ZONE_KEYS = frozenset({"zone_id", "balls", "is_open", "robots_present"})
_STATION_KEYS = frozenset(
    {
        "station_id",
        "is_open",
        "docked",
        "queue_length",
        "buffer_balls",
        "buffer_capacity_balls",
    }
)

_ACTIVITY_STATUS = {
    "idle": RobotStatus.AVAILABLE,
    "traveling": RobotStatus.BUSY,
    "collecting": RobotStatus.BUSY,
    "queued_handoff": RobotStatus.BUSY,
    "unloading": RobotStatus.BUSY,
    "queued_charger": RobotStatus.CHARGING,
    "charging": RobotStatus.CHARGING,
    "paused": RobotStatus.PAUSED,
    "failed": RobotStatus.FAULTED,
    "emergency_stopped": RobotStatus.ESTOPPED,
    "awaiting_human": RobotStatus.AWAITING_HUMAN,
}
_HEALTH_VALUES = frozenset({"ok", "degraded", "failed"})


class AdapterError(ValueError):
    """The upstream object or native serialized stream violates its contract."""


@dataclass(frozen=True, slots=True)
class FacilityStateAdapterContext:
    """Identity, clock, and sensed-validity facts absent from FacilityState.

    ``simulation_midnight`` is the calendar midnight corresponding to
    ``FacilityState.meta.t_s``.  The simulator's ``t_s`` is seconds since
    midnight, not seconds since episode start.
    """

    site_id: str
    deployment_id: str
    simulation_midnight: datetime
    source_sequence: int
    source_ref: str
    clean_sensed_valid: bool

    def __post_init__(self) -> None:
        _text(self.site_id, "context.site_id")
        _text(self.deployment_id, "context.deployment_id")
        _aware_midnight(self.simulation_midnight, "context.simulation_midnight")
        _integer(self.source_sequence, "context.source_sequence")
        _text(self.source_ref, "context.source_ref")
        _boolean(self.clean_sensed_valid, "context.clean_sensed_valid")


@dataclass(frozen=True, slots=True)
class NativeStreamMetadata:
    """Validated identity and simulation disclaimer from ``stream.meta.json``."""

    schema: str
    site_id: str
    deployment_id: str
    episode_id: str
    scenario_name: str
    seed: int
    policy: str
    policy_version: str
    control_interval_s: float
    every_steps: int
    n_records: int
    simulator_version: str
    git_commit: str | None
    disclaimer: str
    source_ref: str
    first_record_clean_sensed_valid: bool
    unmapped_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeFacilityStateCapture:
    """A validated offline capture and its adapted snapshots."""

    metadata: NativeStreamMetadata
    snapshots: tuple[OperationalSnapshot, ...]


@dataclass(frozen=True, slots=True)
class _StateValues:
    t_s: float
    minute_of_day: float
    facility_open: bool
    clean_available: int
    clean_sensed: float
    forecast: tuple[float, ...]
    forecast_bucket_minutes: float
    minutes_to_close: float
    robots: tuple[dict[str, Any], ...]


def adapt_facility_state(
    state: FacilityState,
    *,
    context: FacilityStateAdapterContext,
) -> OperationalSnapshot:
    """Map one real upstream object without mutation or optimistic defaults."""

    if not isinstance(state, FacilityState):
        raise AdapterError("state must be an nxt_facility.state.FacilityState")
    values = _values_from_facility_state(state)
    return _build_snapshot(
        values,
        context=context,
        source_schema_version=None,
        invalid_sensed_reason=(
            None if context.clean_sensed_valid else "declared_invalid_by_adapter_context"
        ),
        valid_sensed_reason=(
            "caller_declared_valid_by_adapter_context"
            if context.clean_sensed_valid
            else None
        ),
    )


def read_native_facility_state_capture(
    states_path: str | Path,
    meta_path: str | Path,
    *,
    simulation_midnight: datetime,
    first_record_clean_sensed_valid: bool,
) -> NativeFacilityStateCapture:
    """Read and strictly validate a native FacilityState capture.

    Source sequence is the zero-based JSONL record order because the native
    state record has no embedded sequence.  The v1 sidecar does not say
    whether record zero precedes the first sensor tick, so the caller must
    provide that fact explicitly rather than relying on ordinal position.
    """

    midnight = _aware_midnight(simulation_midnight, "simulation_midnight")
    state_source = Path(states_path)
    meta_source = Path(meta_path)
    first_sensed_valid = _boolean(
        first_record_clean_sensed_valid,
        "first_record_clean_sensed_valid",
    )
    meta = _load_json_object(meta_source)
    _validate_native_meta(meta)
    metadata = _native_stream_metadata(
        meta,
        meta_source=meta_source,
        first_record_clean_sensed_valid=first_sensed_valid,
    )

    records: list[tuple[int, _StateValues]] = []
    previous_t_s: float | None = None
    try:
        handle = state_source.open("r", encoding="utf-8")
    except OSError as exc:
        raise AdapterError(f"cannot read native state stream {state_source}: {exc}") from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = _loads_json_object(
                line, source=f"{state_source}:line:{line_number}"
            )
            values = _validate_native_record(record, line_number=line_number)
            if values.t_s <= (previous_t_s if previous_t_s is not None else -1.0):
                raise AdapterError(
                    f"{state_source}: non-monotonic meta.t_s at line {line_number}"
                )
            previous_t_s = values.t_s
            if record["meta"]["scenario_name"] != meta["scenario_name"]:
                raise AdapterError(
                    f"{state_source}: line {line_number} scenario_name does not "
                    "match stream metadata"
                )
            if record["meta"]["seed"] != meta["seed"]:
                raise AdapterError(
                    f"{state_source}: line {line_number} seed does not match stream metadata"
                )
            records.append((line_number, values))

    if len(records) != metadata.n_records:
        raise AdapterError(
            f"stream metadata declares {metadata.n_records} records, read {len(records)}"
        )

    snapshots = []
    for sequence, (line_number, values) in enumerate(records):
        source_ref = f"{metadata.episode_id}/{state_source.name}:line:{line_number}"
        sensed_valid = first_sensed_valid if sequence == 0 else True
        context = FacilityStateAdapterContext(
            site_id=metadata.site_id,
            deployment_id=metadata.deployment_id,
            simulation_midnight=midnight,
            source_sequence=sequence,
            source_ref=source_ref,
            clean_sensed_valid=sensed_valid,
        )
        snapshots.append(
            _build_snapshot(
                values,
                context=context,
                source_schema_version=NATIVE_STREAM_SCHEMA,
                invalid_sensed_reason=(
                    "caller_declared_pre_first_sensor_tick"
                    if sequence == 0 and not first_sensed_valid
                    else None
                ),
                valid_sensed_reason=(
                    "caller_declared_valid_first_record"
                    if sequence == 0 and first_sensed_valid
                    else "post_first_record_sensed_value"
                    if sequence > 0
                    else None
                ),
            )
        )
    return NativeFacilityStateCapture(metadata=metadata, snapshots=tuple(snapshots))


def read_native_facility_state_stream(
    states_path: str | Path,
    meta_path: str | Path,
    *,
    simulation_midnight: datetime,
    first_record_clean_sensed_valid: bool,
) -> tuple[OperationalSnapshot, ...]:
    """Return only snapshots from :func:`read_native_facility_state_capture`."""

    return read_native_facility_state_capture(
        states_path,
        meta_path,
        simulation_midnight=simulation_midnight,
        first_record_clean_sensed_valid=first_record_clean_sensed_valid,
    ).snapshots


def _values_from_facility_state(state: FacilityState) -> _StateValues:
    meta = state.meta
    flow = state.ball_flow
    demand = state.demand
    _validate_object_ball_flow(flow)
    robots: list[dict[str, Any]] = []
    for index, robot in enumerate(state.robots):
        prefix = f"state.robots[{index}]"
        activity = _enum_text(
            robot.activity, f"{prefix}.activity", expected_type="RobotActivity"
        )
        health = _enum_text(
            robot.health, f"{prefix}.health", expected_type="RobotHealth"
        )
        robots.append(
            _validate_robot_record(
                {
                    "robot_id": robot.robot_id,
                    "activity": activity,
                    "health": health,
                    "battery_frac": robot.battery_frac,
                    "payload_balls": robot.payload_balls,
                    "payload_capacity_balls": robot.payload_capacity_balls,
                    "location": robot.location,
                    "destination": robot.destination,
                    "assigned_zone": robot.assigned_zone,
                    "estop_latched": robot.estop_latched,
                    "awaiting_human": robot.awaiting_human,
                },
                prefix,
            )
        )
    return _state_values(
        t_s=meta.t_s,
        minute_of_day=meta.minute_of_day,
        facility_open=meta.facility_open,
        clean_available=flow.clean_available,
        clean_sensed=flow.clean_sensed,
        forecast=demand.forecast_balls_per_minute,
        forecast_bucket_minutes=demand.forecast_bucket_minutes,
        minutes_to_close=demand.minutes_to_close,
        robots=tuple(robots),
        prefix="state",
    )


def _build_snapshot(
    values: _StateValues,
    *,
    context: FacilityStateAdapterContext,
    source_schema_version: str | None,
    invalid_sensed_reason: str | None,
    valid_sensed_reason: str | None,
) -> OperationalSnapshot:
    observed_at = context.simulation_midnight + timedelta(seconds=values.t_s)
    source_ref = context.source_ref
    clean_sensed = values.clean_sensed if context.clean_sensed_valid else None
    sensed_provenance = f"{source_ref}#ball_flow.clean_sensed"
    missing_reasons = {
        "committed inbound batch ETAs unavailable in FacilityState",
        "current demand rate unavailable in FacilityState",
        "global collection permission unavailable in FacilityState",
        "robot capability, ETA, expected yield, and washing requirement "
        "unavailable in FacilityState",
        "washer availability unavailable in FacilityState",
    }
    if invalid_sensed_reason is not None:
        sensed_provenance += f" unavailable:{invalid_sensed_reason}"
        missing_reasons.add(f"clean_sensed unavailable: {invalid_sensed_reason}")
    else:
        sensed_provenance += (
            f" (delayed sensed estimate; {valid_sensed_reason})"
            if valid_sensed_reason is not None
            else " (delayed sensed estimate)"
        )

    robots = tuple(_adapt_robot(robot) for robot in values.robots)
    return OperationalSnapshot(
        site_id=context.site_id,
        deployment_id=context.deployment_id,
        observed_at=observed_at,
        source_sequence=context.source_sequence,
        source_schema_version=source_schema_version,
        source_ref=source_ref,
        clean_available=values.clean_available,
        clean_available_provenance=(
            f"{source_ref}#ball_flow.clean_available (FacilityState accounting value)"
        ),
        clean_sensed=clean_sensed,
        clean_sensed_provenance=sensed_provenance,
        current_demand_balls_per_minute=None,
        current_demand_provenance=(
            "unavailable: FacilityState exposes forecast buckets and cumulative demand, "
            "not a current demand rate"
        ),
        forecast_demand_balls_per_minute=values.forecast,
        forecast_bucket_minutes=values.forecast_bucket_minutes,
        forecast_demand_provenance=(
            f"{source_ref}#demand.forecast_balls_per_minute; "
            "bucket=demand.forecast_bucket_minutes"
        ),
        minutes_to_close=values.minutes_to_close,
        minutes_to_close_provenance=(
            f"{source_ref}#demand.minutes_to_close (direct FacilityState field)"
        ),
        range_open=values.facility_open,
        range_open_provenance=(
            f"{source_ref}#meta.facility_open (operating-hours flag; "
            "not global collection permission)"
        ),
        collection_allowed=None,
        collection_permission_provenance=(
            "unavailable: FacilityState has per-zone openness but no global "
            "collection permission or route-block fact"
        ),
        collection_block_reason=None,
        washer_available=None,
        washer_availability_provenance=(
            "unavailable: configured throughput is not live washer availability"
        ),
        inbound_batches=(),
        robots=robots,
        missing_data_reasons=tuple(missing_reasons),
    )


def _adapt_robot(raw: dict[str, Any]) -> RobotOperationalState:
    robot_id = raw["robot_id"]
    activity = raw["activity"]
    health = raw["health"]
    status = _map_status(
        activity=activity,
        health=health,
        estop_latched=raw["estop_latched"],
        awaiting_human=raw["awaiting_human"],
    )
    return RobotOperationalState(
        robot_id=robot_id,
        status=status,
        battery_fraction=raw["battery_frac"],
        expected_clean_ball_yield=None,
        replenishment_eta_minutes=None,
        yield_provenance=(
            "unavailable: FacilityState exposes payload and field counts, not "
            "expected clean-ball yield"
        ),
        eta_provenance=(
            "unavailable: FacilityState exposes node labels, not a replenishment ETA"
        ),
        capabilities=None,
        capability_provenance=(
            "unavailable: FacilityState has no explicit robot capability field"
        ),
        requires_washing=None,
        washing_requirement_provenance=(
            "unavailable: FacilityState has no per-candidate washing requirement"
        ),
        payload_balls=raw["payload_balls"],
        current_task_id=None,
        fault_code=None,
        raw_activity=activity,
        raw_health=health,
    )


def _map_status(
    *,
    activity: str,
    health: str,
    estop_latched: bool,
    awaiting_human: bool,
) -> RobotStatus:
    if activity not in _ACTIVITY_STATUS:
        raise AdapterError(f"unmapped RobotActivity value: {activity!r}")
    if health not in _HEALTH_VALUES:
        raise AdapterError(f"unmapped RobotHealth value: {health!r}")
    if estop_latched or activity == "emergency_stopped":
        return RobotStatus.ESTOPPED
    if health == "failed" or activity == "failed":
        return RobotStatus.FAULTED
    if awaiting_human or activity == "awaiting_human":
        return RobotStatus.AWAITING_HUMAN
    if health == "degraded":
        # The downstream enum has no degraded-but-operable state and the
        # baseline exposes no degraded-speed ETA.  Exclude conservatively.
        return RobotStatus.OFFLINE
    return _ACTIVITY_STATUS[activity]


def _validate_native_meta(meta: dict[str, Any]) -> None:
    _required_keys(meta, _NATIVE_META_KEYS, "stream metadata")
    schema = _text(meta["schema"], "stream metadata.schema")
    if schema != NATIVE_STREAM_SCHEMA:
        raise AdapterError(
            f"expected stream schema {NATIVE_STREAM_SCHEMA!r}, received {schema!r}"
        )
    for key in (
        "site_id",
        "deployment_id",
        "episode_id",
        "scenario_name",
        "policy",
        "policy_version",
        "simulator_version",
        "disclaimer",
    ):
        _text(meta[key], f"stream metadata.{key}")
    _integer(meta["seed"], "stream metadata.seed")
    _number(
        meta["control_interval_s"],
        "stream metadata.control_interval_s",
        minimum=1e-12,
    )
    _integer(meta["every_steps"], "stream metadata.every_steps", minimum=1)
    _integer(meta["n_records"], "stream metadata.n_records", minimum=1)
    if meta["git_commit"] is not None:
        _text(meta["git_commit"], "stream metadata.git_commit")


def _native_stream_metadata(
    meta: dict[str, Any],
    *,
    meta_source: Path,
    first_record_clean_sensed_valid: bool,
) -> NativeStreamMetadata:
    return NativeStreamMetadata(
        schema=meta["schema"],
        site_id=meta["site_id"],
        deployment_id=meta["deployment_id"],
        episode_id=meta["episode_id"],
        scenario_name=meta["scenario_name"],
        seed=meta["seed"],
        policy=meta["policy"],
        policy_version=meta["policy_version"],
        control_interval_s=float(meta["control_interval_s"]),
        every_steps=meta["every_steps"],
        n_records=meta["n_records"],
        simulator_version=meta["simulator_version"],
        git_commit=meta["git_commit"],
        disclaimer=meta["disclaimer"],
        source_ref=f"{meta['episode_id']}/{meta_source.name}",
        first_record_clean_sensed_valid=first_record_clean_sensed_valid,
        unmapped_keys=tuple(sorted(set(meta) - _NATIVE_META_KEYS)),
    )


def _validate_native_record(
    record: dict[str, Any], *, line_number: int
) -> _StateValues:
    prefix = f"record[{line_number - 1}]"
    _exact_keys(record, _STATE_KEYS, prefix)
    meta = _object(record["meta"], f"{prefix}.meta")
    flow = _object(record["ball_flow"], f"{prefix}.ball_flow")
    washer = _object(record["washer"], f"{prefix}.washer")
    demand = _object(record["demand"], f"{prefix}.demand")
    fleet = _object(record["fleet"], f"{prefix}.fleet")
    charging = _object(record["charging"], f"{prefix}.charging")
    staff = _object(record["staff"], f"{prefix}.staff")
    environment = _object(record["environment"], f"{prefix}.environment")
    _exact_keys(meta, _META_KEYS, f"{prefix}.meta")
    _exact_keys(flow, _BALL_FLOW_KEYS, f"{prefix}.ball_flow")
    _exact_keys(washer, _WASHER_KEYS, f"{prefix}.washer")
    _exact_keys(demand, _DEMAND_KEYS, f"{prefix}.demand")
    _exact_keys(fleet, _FLEET_KEYS, f"{prefix}.fleet")
    _exact_keys(charging, _CHARGING_KEYS, f"{prefix}.charging")
    _exact_keys(staff, _STAFF_KEYS, f"{prefix}.staff")
    _exact_keys(environment, _ENVIRONMENT_KEYS, f"{prefix}.environment")

    _text(meta["scenario_name"], f"{prefix}.meta.scenario_name")
    _integer(meta["seed"], f"{prefix}.meta.seed")
    _boolean(meta["facility_open"], f"{prefix}.meta.facility_open")
    total_balls = _integer(
        flow["total_balls"], f"{prefix}.ball_flow.total_balls"
    )
    clean_available = _integer(
        flow["clean_available"], f"{prefix}.ball_flow.clean_available"
    )
    in_wash = _integer(flow["in_wash"], f"{prefix}.ball_flow.in_wash")
    dirty_buffered = _count_mapping(
        flow["dirty_buffered"], f"{prefix}.ball_flow.dirty_buffered"
    )
    on_field = _count_mapping(
        flow["on_field"], f"{prefix}.ball_flow.on_field"
    )
    in_transit = _count_mapping(
        flow["in_transit"], f"{prefix}.ball_flow.in_transit"
    )
    conserved = _boolean(flow["conserved"], f"{prefix}.ball_flow.conserved")
    accounted_balls = (
        clean_available
        + in_wash
        + sum(dirty_buffered.values())
        + sum(on_field.values())
        + sum(in_transit.values())
    )
    if not conserved or accounted_balls != total_balls:
        raise AdapterError(
            f"{prefix}.ball_flow violates the upstream conservation ledger"
        )

    _number(
        washer["throughput_balls_per_minute"],
        f"{prefix}.washer.throughput_balls_per_minute",
    )
    _integer(
        washer["batch_size_balls"],
        f"{prefix}.washer.batch_size_balls",
        minimum=1,
    )
    _integer(washer["wip"], f"{prefix}.washer.wip")
    if washer["wip"] != flow["in_wash"]:
        raise AdapterError(f"{prefix}: washer.wip must equal ball_flow.in_wash")

    forecast_raw = _array(
        demand["forecast_balls_per_minute"],
        f"{prefix}.demand.forecast_balls_per_minute",
    )
    if not forecast_raw:
        raise AdapterError(f"{prefix}.demand.forecast_balls_per_minute is empty")
    for index, rate in enumerate(forecast_raw):
        _number(
            rate,
            f"{prefix}.demand.forecast_balls_per_minute[{index}]",
        )
    _integer(
        demand["forecast_bucket_minutes"],
        f"{prefix}.demand.forecast_bucket_minutes",
        minimum=1,
    )
    _number(demand["minutes_to_close"], f"{prefix}.demand.minutes_to_close")
    _integer(demand["demand_balls_total"], f"{prefix}.demand.demand_balls_total")
    _integer(demand["demand_balls_served"], f"{prefix}.demand.demand_balls_served")
    _number(demand["stockout_minutes"], f"{prefix}.demand.stockout_minutes")
    availability = _number(
        demand["service_availability"], f"{prefix}.demand.service_availability"
    )
    if availability > 1.0:
        raise AdapterError(f"{prefix}.demand.service_availability must be <= 1")

    for group_name, group, keys in (
        ("fleet", fleet, _FLEET_KEYS),
        ("charging", charging, _CHARGING_KEYS),
        ("staff", staff, _STAFF_KEYS),
    ):
        for key in keys:
            _integer(group[key], f"{prefix}.{group_name}.{key}")
    _number(
        environment["wet_ground_speed_multiplier"],
        f"{prefix}.environment.wet_ground_speed_multiplier",
        minimum=1e-12,
    )
    for key in ("zones_open", "zones_total", "stations_open", "stations_total"):
        _integer(environment[key], f"{prefix}.environment.{key}")

    robots_raw = _array(record["robots"], f"{prefix}.robots")
    robots = tuple(
        _validate_robot_record(
            _object(robot, f"{prefix}.robots[{index}]"),
            f"{prefix}.robots[{index}]",
        )
        for index, robot in enumerate(robots_raw)
    )
    zones = _array(record["zones"], f"{prefix}.zones")
    for index, item in enumerate(zones):
        path = f"{prefix}.zones[{index}]"
        zone = _object(item, path)
        _exact_keys(zone, _ZONE_KEYS, path)
        _text(zone["zone_id"], f"{path}.zone_id")
        _integer(zone["balls"], f"{path}.balls")
        _boolean(zone["is_open"], f"{path}.is_open")
        _integer(zone["robots_present"], f"{path}.robots_present")
    stations = _array(record["stations"], f"{prefix}.stations")
    for index, item in enumerate(stations):
        path = f"{prefix}.stations[{index}]"
        station = _object(item, path)
        _exact_keys(station, _STATION_KEYS, path)
        _text(station["station_id"], f"{path}.station_id")
        _boolean(station["is_open"], f"{path}.is_open")
        for key in (
            "docked",
            "queue_length",
            "buffer_balls",
            "buffer_capacity_balls",
        ):
            _integer(station[key], f"{path}.{key}")

    return _state_values(
        t_s=meta["t_s"],
        minute_of_day=meta["minute_of_day"],
        facility_open=meta["facility_open"],
        clean_available=flow["clean_available"],
        clean_sensed=flow["clean_sensed"],
        forecast=tuple(forecast_raw),
        forecast_bucket_minutes=demand["forecast_bucket_minutes"],
        minutes_to_close=demand["minutes_to_close"],
        robots=robots,
        prefix=prefix,
    )


def _validate_robot_record(raw: dict[str, Any], prefix: str) -> dict[str, Any]:
    _exact_keys(raw, _ROBOT_KEYS, prefix)
    _text(raw["robot_id"], f"{prefix}.robot_id")
    activity = _text(raw["activity"], f"{prefix}.activity")
    health = _text(raw["health"], f"{prefix}.health")
    if activity not in _ACTIVITY_STATUS:
        raise AdapterError(f"{prefix}.activity has unmapped value {activity!r}")
    if health not in _HEALTH_VALUES:
        raise AdapterError(f"{prefix}.health has unmapped value {health!r}")
    battery = _number(raw["battery_frac"], f"{prefix}.battery_frac")
    if battery > 1.0:
        raise AdapterError(f"{prefix}.battery_frac must be <= 1")
    _integer(raw["payload_balls"], f"{prefix}.payload_balls")
    _integer(
        raw["payload_capacity_balls"],
        f"{prefix}.payload_capacity_balls",
        minimum=1,
    )
    _text(raw["location"], f"{prefix}.location")
    _optional_text(raw["destination"], f"{prefix}.destination")
    _optional_text(raw["assigned_zone"], f"{prefix}.assigned_zone")
    _boolean(raw["estop_latched"], f"{prefix}.estop_latched")
    _boolean(raw["awaiting_human"], f"{prefix}.awaiting_human")
    return raw


def _state_values(
    *,
    t_s: object,
    minute_of_day: object,
    facility_open: object,
    clean_available: object,
    clean_sensed: object,
    forecast: object,
    forecast_bucket_minutes: object,
    minutes_to_close: object,
    robots: tuple[dict[str, Any], ...],
    prefix: str,
) -> _StateValues:
    seconds = _number(t_s, f"{prefix}.meta.t_s")
    if seconds > 86400.0:
        raise AdapterError(f"{prefix}.meta.t_s must be seconds within one day")
    minute = _number(minute_of_day, f"{prefix}.meta.minute_of_day")
    if minute > 1440.0:
        raise AdapterError(f"{prefix}.meta.minute_of_day must be <= 1440")
    if not math.isclose(seconds / 60.0, minute, rel_tol=0.0, abs_tol=1e-6):
        raise AdapterError(
            f"{prefix}: meta.t_s is seconds since midnight and must equal minute_of_day * 60"
        )
    open_value = _boolean(facility_open, f"{prefix}.meta.facility_open")
    available = _integer(
        clean_available, f"{prefix}.ball_flow.clean_available"
    )
    sensed = _number(clean_sensed, f"{prefix}.ball_flow.clean_sensed")
    if type(forecast) not in (tuple, list) or not forecast:
        raise AdapterError(f"{prefix}.demand.forecast_balls_per_minute must be non-empty")
    rates = tuple(
        _number(rate, f"{prefix}.demand.forecast_balls_per_minute[{index}]")
        for index, rate in enumerate(forecast)
    )
    bucket = _number(
        forecast_bucket_minutes,
        f"{prefix}.demand.forecast_bucket_minutes",
        minimum=1e-12,
    )
    close = _number(minutes_to_close, f"{prefix}.demand.minutes_to_close")
    return _StateValues(
        t_s=seconds,
        minute_of_day=minute,
        facility_open=open_value,
        clean_available=available,
        clean_sensed=sensed,
        forecast=rates,
        forecast_bucket_minutes=bucket,
        minutes_to_close=close,
        robots=robots,
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AdapterError(f"cannot read JSON object {path}: {exc}") from exc
    return _loads_json_object(text, source=str(path))


def _loads_json_object(text: str, *, source: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise AdapterError(f"{source}: non-finite JSON number {value!r} is forbidden")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AdapterError(f"{source}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except json.JSONDecodeError as exc:
        raise AdapterError(f"{source}: invalid JSON: {exc.msg}") from exc
    return _object(value, source)


def _exact_keys(value: dict[str, Any], expected: frozenset[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        raise AdapterError(
            f"{path} keys differ from native contract: {sorted(actual ^ expected)}"
        )


def _required_keys(value: dict[str, Any], required: frozenset[str], path: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise AdapterError(f"{path} is missing native contract keys: {missing}")


def _object(value: object, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise AdapterError(f"{path} must be a JSON object")
    return value


def _array(value: object, path: str) -> list[Any]:
    if type(value) is not list:
        raise AdapterError(f"{path} must be a JSON array")
    return value


def _text(value: object, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise AdapterError(f"{path} must be a non-empty string")
    return value


def _optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _text(value, path)


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise AdapterError(f"{path} must be a boolean")
    return value


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int:
        raise AdapterError(f"{path} must be an integer (booleans are not integers)")
    if value < minimum:
        raise AdapterError(f"{path} must be >= {minimum}")
    return value


def _number(value: object, path: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterError(f"{path} must be a number (booleans are not numbers)")
    result = float(value)
    if not math.isfinite(result):
        raise AdapterError(f"{path} must be finite")
    if result < minimum:
        raise AdapterError(f"{path} must be >= {minimum}")
    return result


def _count_mapping(value: object, path: str) -> dict[str, Any]:
    mapping = _object(value, path)
    for key, count in mapping.items():
        _text(key, f"{path}.<key>")
        _integer(count, f"{path}.{key}")
    return mapping


def _enum_text(value: object, path: str, *, expected_type: str) -> str:
    value_type = type(value)
    if (
        value_type.__module__ != "nxt_range_ops.core.entities"
        or value_type.__qualname__ != expected_type
    ):
        raise AdapterError(
            f"{path} must be the upstream {expected_type} enum"
        )
    raw = getattr(value, "value", None)
    if type(raw) is not str:
        raise AdapterError(f"{path} must be the upstream {expected_type} string enum")
    return raw


def _validate_object_ball_flow(flow: object) -> None:
    if not isinstance(flow, BallFlow):
        raise AdapterError("state.ball_flow must be an nxt_facility.state.BallFlow")
    total_balls = _integer(flow.total_balls, "state.ball_flow.total_balls")
    clean_available = _integer(
        flow.clean_available, "state.ball_flow.clean_available"
    )
    _number(flow.clean_sensed, "state.ball_flow.clean_sensed")
    in_wash = _integer(flow.in_wash, "state.ball_flow.in_wash")
    accounted_balls = clean_available + in_wash
    for name in ("dirty_buffered", "on_field", "in_transit"):
        counts = getattr(flow, name)
        if type(counts) is not tuple:
            raise AdapterError(f"state.ball_flow.{name} must be a tuple")
        seen: set[str] = set()
        for index, item in enumerate(counts):
            path = f"state.ball_flow.{name}[{index}]"
            if type(item) is not tuple or len(item) != 2:
                raise AdapterError(f"{path} must be an (id, count) tuple")
            entity_id = _text(item[0], f"{path}[0]")
            if entity_id in seen:
                raise AdapterError(f"state.ball_flow.{name} contains duplicate IDs")
            seen.add(entity_id)
            accounted_balls += _integer(item[1], f"{path}[1]")
    if accounted_balls != total_balls or flow.conserved is not True:
        raise AdapterError("state.ball_flow violates the upstream conservation ledger")


def _aware_midnight(value: object, path: str) -> datetime:
    if not isinstance(value, datetime):
        raise AdapterError(f"{path} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AdapterError(f"{path} must be timezone-aware")
    if any((value.hour, value.minute, value.second, value.microsecond)):
        raise AdapterError(f"{path} must be calendar midnight")
    return value
