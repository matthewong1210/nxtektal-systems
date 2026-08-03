"""Typed configuration models for the Range Operations Training Environment.

Reuses the Phase 0 provenance vocabulary (``StrictModel``, ``PhysicalParam``,
``ParamSource`` from ``nxt_sim.config.models``): every physical or economic
quantity carries a provenance tag, and all shipped scenarios use
``source: placeholder`` values. Placeholder-driven results exercise the
pipeline only and must never be presented as real facility performance.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from nxt_sim.config.models import ParamSource, PhysicalParam, StrictModel


def P(value: float, note: str = "") -> PhysicalParam:
    """Shorthand for an explicit placeholder-provenance parameter."""
    return PhysicalParam(value=value, source=ParamSource.PLACEHOLDER, note=note)


class Point2D(StrictModel):
    """Planar facility coordinate in meters (site frame, origin at dispenser)."""

    x_m: float
    y_m: float

    def distance_to(self, other: "Point2D") -> float:
        return ((self.x_m - other.x_m) ** 2 + (self.y_m - other.y_m) ** 2) ** 0.5


class TimeWindow(StrictModel):
    """Half-open window [start, end) in minutes since midnight."""

    start_minute: int = Field(ge=0, le=1440)
    end_minute: int = Field(ge=0, le=1440)

    @model_validator(mode="after")
    def _ordered(self) -> "TimeWindow":
        if self.end_minute <= self.start_minute:
            raise ValueError("end_minute must be greater than start_minute")
        return self

    def contains(self, minute: float) -> bool:
        return self.start_minute <= minute < self.end_minute


class OperatingHoursConfig(StrictModel):
    """Facility operating hours. Episodes span one operating day."""

    open_minute: int = Field(default=360, ge=0, le=1440)  # 06:00
    close_minute: int = Field(default=1320, ge=0, le=1440)  # 22:00

    @model_validator(mode="after")
    def _ordered(self) -> "OperatingHoursConfig":
        if self.close_minute <= self.open_minute:
            raise ValueError("close_minute must be greater than open_minute")
        return self

    @property
    def open_seconds(self) -> float:
        return self.open_minute * 60.0

    @property
    def close_seconds(self) -> float:
        return self.close_minute * 60.0


class DemandWindow(StrictModel):
    """Piecewise-constant customer demand rate over part of the day."""

    window: TimeWindow
    balls_per_minute: float = Field(ge=0.0)


class DemandSpike(StrictModel):
    """An unexpected demand surge (e.g. a walk-in group event)."""

    window: TimeWindow
    multiplier: float = Field(gt=0.0)


class DemandConfig(StrictModel):
    """Time-varying stochastic customer demand and its (imperfect) forecast.

    True demand per simulated minute is Poisson with rate taken from the
    active window (times any spike multiplier). The *forecast* the agent sees
    is the base profile (spikes are unforecast by definition) distorted by
    ``forecast_bias`` and per-horizon-bucket noise — modelling forecast
    uncertainty explicitly.
    """

    windows: list[DemandWindow] = Field(min_length=1)
    spikes: list[DemandSpike] = Field(default_factory=list)
    forecast_bias: float = Field(default=1.0, gt=0.0)
    forecast_noise_sd: float = Field(default=0.1, ge=0.0)
    forecast_horizon_minutes: int = Field(default=120, gt=0)
    forecast_bucket_minutes: int = Field(default=30, gt=0)

    def base_rate_at(self, minute: float) -> float:
        for w in self.windows:
            if w.window.contains(minute):
                return w.balls_per_minute
        return 0.0

    def true_rate_at(self, minute: float) -> float:
        rate = self.base_rate_at(minute)
        for spike in self.spikes:
            if spike.window.contains(minute):
                rate *= spike.multiplier
        return rate


class ZoneConfig(StrictModel):
    """A ball-collection zone on the range."""

    zone_id: str
    position: Point2D
    landing_weight: float = Field(gt=0.0)
    closure_windows: list[TimeWindow] = Field(default_factory=list)


class RobotConfig(StrictModel):
    """One collection robot (no arm; basket handoff per Phase 0 concept)."""

    robot_id: str
    payload_capacity_balls: int = Field(gt=0)
    battery_capacity_wh: PhysicalParam
    speed_mps: PhysicalParam
    idle_power_w: PhysicalParam
    mean_time_between_failures_h: PhysicalParam
    degraded_fraction: float = Field(default=0.5, ge=0.0, le=1.0)
    initial_battery_frac: float = Field(default=1.0, gt=0.0, le=1.0)


class HandoffStationConfig(StrictModel):
    """Handoff/washer intake station with limited dock slots and buffer."""

    station_id: str
    position: Point2D
    dock_slots: int = Field(gt=0)
    max_queue_length: int = Field(default=4, ge=0)
    buffer_capacity_balls: int = Field(gt=0)
    outage_windows: list[TimeWindow] = Field(default_factory=list)


class WasherConfig(StrictModel):
    """Ball washer: pulls from station buffers, returns to the dispenser."""

    balls_per_minute: PhysicalParam
    batch_size_balls: int = Field(default=200, gt=0)


class ChargerConfig(StrictModel):
    """Charging area with limited simultaneous charge slots."""

    position: Point2D
    slots: int = Field(gt=0)
    charge_rate_w: PhysicalParam
    charge_target_frac: float = Field(default=0.95, gt=0.0, le=1.0)


class SensorConfig(StrictModel):
    """Sensor imperfection: the agent observes noisy, delayed state."""

    inventory_delay_s: float = Field(default=60.0, ge=0.0)
    inventory_noise_rel_sd: float = Field(default=0.02, ge=0.0)
    zone_count_noise_rel_sd: float = Field(default=0.15, ge=0.0)
    battery_noise_abs_sd: float = Field(default=0.01, ge=0.0)
    update_period_s: float = Field(default=30.0, gt=0.0)


class SkillModelConfig(StrictModel):
    """Parameters consumed by the MockSkillOutcomeModel (all placeholder)."""

    collect_cycle_s: PhysicalParam
    collect_cycle_balls: int = Field(gt=0)
    collect_power_w: PhysicalParam
    travel_power_w: PhysicalParam
    dock_attempt_s: PhysicalParam
    dock_failure_prob: float = Field(default=0.05, ge=0.0, le=1.0)
    max_dock_retries: int = Field(default=3, ge=0)
    dock_power_w: PhysicalParam
    unload_balls_per_s: PhysicalParam
    unload_power_w: PhysicalParam
    duration_jitter_rel_sd: float = Field(default=0.1, ge=0.0)
    collect_failure_prob: float = Field(default=0.002, ge=0.0, le=1.0)
    travel_failure_prob: float = Field(default=0.001, ge=0.0, le=1.0)
    wet_ground_speed_multiplier: float = Field(default=1.0, gt=0.0, le=1.0)
    degraded_speed_multiplier: float = Field(default=0.6, gt=0.0, le=1.0)


class SafetyConfig(StrictModel):
    """Hard operational safety constraints (enforced, not just penalized)."""

    min_battery_reserve_frac: float = Field(default=0.15, ge=0.0, le=1.0)
    hard_battery_floor_frac: float = Field(default=0.02, ge=0.0, le=1.0)
    max_robots_per_zone: int = Field(default=2, ge=1)
    dock_incident_estop_prob: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _floor_below_reserve(self) -> "SafetyConfig":
        if self.hard_battery_floor_frac >= self.min_battery_reserve_frac:
            raise ValueError(
                "hard_battery_floor_frac must be below min_battery_reserve_frac"
            )
        return self


class HumanOpsConfig(StrictModel):
    """Human staff responding to assistance requests and failures."""

    response_delay_s: PhysicalParam
    fix_duration_s: PhysicalParam
    staff_count: int = Field(default=1, ge=1)


class RewardWeights(StrictModel):
    """Weights for the decomposed reward. Components are reported separately;
    the scalar is only their weighted sum."""

    service_availability: float = 1.0
    stockout_per_minute: float = -2.0
    per_ball_processed: float = 0.01
    per_human_intervention: float = -10.0
    per_wh_energy: float = -0.005
    per_empty_travel_s: float = -0.002
    per_idle_robot_s: float = -0.0005
    per_handoff_queue_s: float = -0.002
    per_task_switch: float = -0.5
    per_unsafe_action_rejection: float = -1.0


class EconomicsConfig(StrictModel):
    """Placeholder cost rates for the estimated-operating-cost metric."""

    energy_cost_per_kwh: PhysicalParam
    cost_per_human_intervention: PhysicalParam
    robot_hour_cost: PhysicalParam


class EpisodeConfig(StrictModel):
    """Decision cadence and episode bounds. One episode = one operating day."""

    control_interval_s: float = Field(default=60.0, gt=0.0)
    max_steps: int = Field(default=2000, gt=0)


class RangeOpsScenario(StrictModel):
    """Complete, self-contained scenario for one simulated operating day."""

    name: str
    description: str = ""
    total_balls: int = Field(gt=0)
    initial_dispenser_frac: float = Field(default=0.9, ge=0.0, le=1.0)
    dispenser_position: Point2D = Field(default_factory=lambda: Point2D(x_m=0.0, y_m=0.0))
    hours: OperatingHoursConfig = Field(default_factory=OperatingHoursConfig)
    demand: DemandConfig
    zones: list[ZoneConfig] = Field(min_length=1)
    robots: list[RobotConfig] = Field(min_length=1)
    stations: list[HandoffStationConfig] = Field(min_length=1)
    washer: WasherConfig
    charger: ChargerConfig
    sensors: SensorConfig = Field(default_factory=SensorConfig)
    skills: SkillModelConfig
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    human_ops: HumanOpsConfig
    rewards: RewardWeights = Field(default_factory=RewardWeights)
    economics: EconomicsConfig
    episode: EpisodeConfig = Field(default_factory=EpisodeConfig)

    @model_validator(mode="after")
    def _charge_target_above_reserve(self) -> "RangeOpsScenario":
        if self.charger.charge_target_frac <= self.safety.min_battery_reserve_frac:
            raise ValueError(
                "charger.charge_target_frac must exceed "
                "safety.min_battery_reserve_frac, or a fully charged robot "
                "could still be barred from collection assignments"
            )
        return self

    @model_validator(mode="after")
    def _unique_ids(self) -> "RangeOpsScenario":
        zone_ids = [z.zone_id for z in self.zones]
        robot_ids = [r.robot_id for r in self.robots]
        station_ids = [s.station_id for s in self.stations]
        for label, ids in (
            ("zone", zone_ids),
            ("robot", robot_ids),
            ("station", station_ids),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"duplicate {label}_id in scenario {self.name!r}")
        return self

    @property
    def zone_ids(self) -> list[str]:
        return [z.zone_id for z in self.zones]

    @property
    def robot_ids(self) -> list[str]:
        return [r.robot_id for r in self.robots]

    @property
    def station_ids(self) -> list[str]:
        return [s.station_id for s in self.stations]


def placeholder_census(model: StrictModel) -> list[str]:
    """Walk a config tree and list dotted paths of placeholder parameters.

    Mirrors the Phase 0 reporting rule: every placeholder in use is counted
    so no result can silently masquerade as validated engineering data.
    """
    found: list[str] = []

    def _walk(node: object, path: str) -> None:
        if isinstance(node, PhysicalParam):
            if node.source is ParamSource.PLACEHOLDER:
                found.append(path)
            return
        if isinstance(node, StrictModel):
            for name in type(node).model_fields:
                _walk(getattr(node, name), f"{path}.{name}" if path else name)
        elif isinstance(node, (list, tuple)):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(model, "")
    return found
