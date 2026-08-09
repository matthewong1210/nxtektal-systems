"""Frozen contracts for the Shadow Ops operational-decision layer.

This module is deliberately stdlib-only.  It has no simulator, telemetry,
digital-twin, robot-control, network, or persistence dependency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum

from .serialization import stable_digest


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


def _require_bool_or_none(name: str, value: object) -> None:
    if value is not None and type(value) is not bool:
        raise TypeError(f"{name} must be a boolean or None")


def _require_int(name: str, value: object, *, minimum: int = 0) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_number(
    name: str,
    value: object,
    *,
    minimum: float = 0.0,
    allow_none: bool = False,
) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number" + (" or None" if allow_none else ""))
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    if converted < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return converted


def _require_fraction(name: str, value: object) -> float:
    converted = _require_number(name, value)
    assert converted is not None
    if converted > 1.0:
        raise ValueError(f"{name} must be <= 1")
    return converted


class RobotStatus(StrEnum):
    AVAILABLE = "available"
    BUSY = "busy"
    CHARGING = "charging"
    PAUSED = "paused"
    AWAITING_HUMAN = "awaiting_human"
    FAULTED = "faulted"
    ESTOPPED = "estopped"
    OFFLINE = "offline"


class RecommendationAction(StrEnum):
    """Advisory actions.  These values are never robot commands."""

    DISPATCH_COLLECTOR = "dispatch_collector"
    OPERATOR_INTERVENTION = "operator_intervention"


class EvaluationVerdict(StrEnum):
    NO_ACTION = "no_action"
    RECOMMEND = "recommend"


@dataclass(frozen=True, slots=True)
class InboundBallBatch:
    """A committed clean-ball arrival with an upstream ETA."""

    source_id: str
    eta_minutes: float
    balls: int
    provenance: str

    def __post_init__(self) -> None:
        _require_text("source_id", self.source_id)
        eta = _require_number("eta_minutes", self.eta_minutes)
        assert eta is not None
        object.__setattr__(self, "eta_minutes", eta)
        _require_int("balls", self.balls, minimum=1)
        _require_text("provenance", self.provenance)


@dataclass(frozen=True, slots=True)
class RobotOperationalState:
    robot_id: str
    status: RobotStatus
    battery_fraction: float
    expected_clean_ball_yield: int | None
    replenishment_eta_minutes: float | None
    yield_provenance: str
    eta_provenance: str
    capabilities: frozenset[str] | None
    capability_provenance: str
    requires_washing: bool | None
    washing_requirement_provenance: str
    payload_balls: int = 0
    current_task_id: str | None = None
    fault_code: str | None = None
    raw_activity: str | None = None
    raw_health: str | None = None

    def __post_init__(self) -> None:
        _require_text("robot_id", self.robot_id)
        if not isinstance(self.status, RobotStatus):
            raise TypeError("status must be a RobotStatus")
        battery = _require_fraction("battery_fraction", self.battery_fraction)
        object.__setattr__(self, "battery_fraction", battery)
        if self.expected_clean_ball_yield is not None:
            _require_int(
                "expected_clean_ball_yield", self.expected_clean_ball_yield
            )
        eta = _require_number(
            "replenishment_eta_minutes",
            self.replenishment_eta_minutes,
            allow_none=True,
        )
        object.__setattr__(self, "replenishment_eta_minutes", eta)
        _require_text("yield_provenance", self.yield_provenance)
        _require_text("eta_provenance", self.eta_provenance)
        _require_text("capability_provenance", self.capability_provenance)
        _require_text(
            "washing_requirement_provenance", self.washing_requirement_provenance
        )
        if self.capabilities is not None:
            if type(self.capabilities) is not frozenset:
                raise TypeError("capabilities must be a frozenset or None")
            for item in self.capabilities:
                _require_text("capability", item)
        _require_bool_or_none("requires_washing", self.requires_washing)
        _require_int("payload_balls", self.payload_balls)
        for name in ("current_task_id", "fault_code", "raw_activity", "raw_health"):
            value = getattr(self, name)
            if value is not None:
                _require_text(name, value)


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    """The only input read by policy-core modules.

    Missing safety facts are represented by ``None``.  No operational boolean,
    collector capability, ETA, or yield defaults to an optimistic value.
    """

    site_id: str
    deployment_id: str
    observed_at: datetime
    source_sequence: int
    source_schema_version: str | None
    source_ref: str
    clean_available: int
    clean_available_provenance: str
    clean_sensed: float | None
    clean_sensed_provenance: str
    current_demand_balls_per_minute: float | None
    current_demand_provenance: str
    forecast_demand_balls_per_minute: tuple[float, ...] | None
    forecast_bucket_minutes: float | None
    forecast_demand_provenance: str
    minutes_to_close: float | None
    minutes_to_close_provenance: str
    range_open: bool | None
    range_open_provenance: str
    collection_allowed: bool | None
    collection_permission_provenance: str
    collection_block_reason: str | None
    washer_available: bool | None
    washer_availability_provenance: str
    inbound_batches: tuple[InboundBallBatch, ...] = ()
    robots: tuple[RobotOperationalState, ...] = ()
    missing_data_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("site_id", self.site_id)
        _require_text("deployment_id", self.deployment_id)
        _require_aware("observed_at", self.observed_at)
        _require_int("source_sequence", self.source_sequence)
        if self.source_schema_version is not None:
            _require_text("source_schema_version", self.source_schema_version)
        _require_text("source_ref", self.source_ref)
        _require_int("clean_available", self.clean_available)
        _require_text("clean_available_provenance", self.clean_available_provenance)
        sensed = _require_number(
            "clean_sensed", self.clean_sensed, allow_none=True
        )
        object.__setattr__(self, "clean_sensed", sensed)
        _require_text("clean_sensed_provenance", self.clean_sensed_provenance)
        current = _require_number(
            "current_demand_balls_per_minute",
            self.current_demand_balls_per_minute,
            allow_none=True,
        )
        object.__setattr__(self, "current_demand_balls_per_minute", current)
        _require_text("current_demand_provenance", self.current_demand_provenance)
        forecast = self.forecast_demand_balls_per_minute
        if forecast is not None:
            if type(forecast) is not tuple or not forecast:
                raise ValueError(
                    "forecast_demand_balls_per_minute must be a non-empty tuple or None"
                )
            normalized: list[float] = []
            for index, rate in enumerate(forecast):
                converted = _require_number(f"forecast[{index}]", rate)
                assert converted is not None
                normalized.append(converted)
            object.__setattr__(
                self, "forecast_demand_balls_per_minute", tuple(normalized)
            )
            bucket = _require_number(
                "forecast_bucket_minutes",
                self.forecast_bucket_minutes,
                minimum=1e-12,
            )
            object.__setattr__(self, "forecast_bucket_minutes", bucket)
        elif self.forecast_bucket_minutes is not None:
            raise ValueError("forecast_bucket_minutes requires a forecast")
        _require_text("forecast_demand_provenance", self.forecast_demand_provenance)
        minutes = _require_number(
            "minutes_to_close", self.minutes_to_close, allow_none=True
        )
        object.__setattr__(self, "minutes_to_close", minutes)
        _require_text(
            "minutes_to_close_provenance", self.minutes_to_close_provenance
        )
        _require_bool_or_none("range_open", self.range_open)
        _require_bool_or_none("collection_allowed", self.collection_allowed)
        _require_bool_or_none("washer_available", self.washer_available)
        _require_text("range_open_provenance", self.range_open_provenance)
        _require_text(
            "collection_permission_provenance",
            self.collection_permission_provenance,
        )
        _require_text(
            "washer_availability_provenance", self.washer_availability_provenance
        )
        if self.collection_block_reason is not None:
            _require_text("collection_block_reason", self.collection_block_reason)
        if self.collection_allowed is False and self.collection_block_reason is None:
            raise ValueError(
                "collection_block_reason is required when collection is blocked"
            )
        if type(self.inbound_batches) is not tuple:
            raise TypeError("inbound_batches must be a tuple")
        if type(self.robots) is not tuple:
            raise TypeError("robots must be a tuple")
        if type(self.missing_data_reasons) is not tuple:
            raise TypeError("missing_data_reasons must be a tuple")
        for reason in self.missing_data_reasons:
            _require_text("missing_data_reason", reason)
        robot_ids = [robot.robot_id for robot in self.robots]
        if len(robot_ids) != len(set(robot_ids)):
            raise ValueError("robot IDs must be unique within a snapshot")
        inbound_ids = [batch.source_id for batch in self.inbound_batches]
        if len(inbound_ids) != len(set(inbound_ids)):
            raise ValueError("inbound batch source IDs must be unique within a snapshot")
        if any(source_id.startswith("counterfactual:") for source_id in inbound_ids):
            raise ValueError(
                "committed inbound batch source IDs cannot use the reserved "
                "counterfactual: prefix"
            )
        object.__setattr__(
            self,
            "inbound_batches",
            tuple(sorted(self.inbound_batches, key=lambda b: (b.eta_minutes, b.source_id))),
        )
        object.__setattr__(
            self, "robots", tuple(sorted(self.robots, key=lambda robot: robot.robot_id))
        )
        object.__setattr__(
            self, "missing_data_reasons", tuple(sorted(set(self.missing_data_reasons)))
        )

    @property
    def decision_inventory_balls(self) -> float:
        return (
            self.clean_sensed
            if self.clean_sensed is not None
            else float(self.clean_available)
        )

    @property
    def decision_inventory_basis(self) -> str:
        return "clean_sensed" if self.clean_sensed is not None else "clean_available"

    @property
    def decision_inventory_provenance(self) -> str:
        return (
            self.clean_sensed_provenance
            if self.clean_sensed is not None
            else self.clean_available_provenance
        )


@dataclass(frozen=True, slots=True)
class BallAvailabilityPolicyConfig:
    policy_id: str = "ball-availability-guardian"
    policy_version: str = "0.1.0"
    safety_buffer_minutes: float = 12.0
    alert_lead_minutes: float = 15.0
    minimum_battery_fraction: float = 0.30
    risk_horizon_minutes: float = 180.0

    def __post_init__(self) -> None:
        _require_text("policy_id", self.policy_id)
        _require_text("policy_version", self.policy_version)
        for name in (
            "safety_buffer_minutes",
            "alert_lead_minutes",
            "risk_horizon_minutes",
        ):
            value = _require_number(
                name,
                getattr(self, name),
                minimum=(1e-12 if name == "risk_horizon_minutes" else 0.0),
            )
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "minimum_battery_fraction",
            _require_fraction(
                "minimum_battery_fraction", self.minimum_battery_fraction
            ),
        )


def derive_demand_projection(
    *,
    current_demand_balls_per_minute: float | None,
    forecast_demand_balls_per_minute: tuple[float, ...] | None,
    forecast_bucket_minutes: float | None,
    minutes_to_close: float | None,
    config: BallAvailabilityPolicyConfig,
) -> tuple[tuple[float, ...], float, float, str] | None:
    """Derive the one deterministic demand schedule allowed by policy v0.1."""

    horizon_cap = config.risk_horizon_minutes
    if minutes_to_close is not None:
        horizon_cap = min(horizon_cap, minutes_to_close)
    if horizon_cap <= 0.0:
        return None

    if forecast_demand_balls_per_minute is not None:
        assert forecast_bucket_minutes is not None
        available_horizon = (
            len(forecast_demand_balls_per_minute) * forecast_bucket_minutes
        )
        horizon = min(horizon_cap, available_horizon)
        if horizon <= 0.0:
            return None
        count = max(1, math.ceil(horizon / forecast_bucket_minutes))
        selected_forecast = forecast_demand_balls_per_minute[:count]
        if current_demand_balls_per_minute is None:
            return (
                selected_forecast,
                forecast_bucket_minutes,
                horizon,
                "forecast_demand",
            )
        return (
            tuple(
                max(current_demand_balls_per_minute, rate)
                for rate in selected_forecast
            ),
            forecast_bucket_minutes,
            horizon,
            "per_bucket_max(current_demand,forecast_demand)",
        )

    if current_demand_balls_per_minute is not None:
        return (
            (current_demand_balls_per_minute,),
            horizon_cap,
            horizon_cap,
            "current_demand",
        )
    return None


def candidate_safety_exclusion_reasons(
    *,
    robot: RobotOperationalState,
    config: BallAvailabilityPolicyConfig,
    range_open: bool | None,
    collection_allowed: bool | None,
    collection_block_reason: str | None,
    washer_available: bool | None,
    first_stockout_minutes: float | None,
) -> tuple[str, ...]:
    """Derive the auditable, pre-counterfactual candidate exclusion reasons."""

    reasons: list[str] = []
    if range_open is None:
        reasons.append("range_open_unknown")
    elif range_open is False:
        reasons.append("range_closed")
    if collection_allowed is None:
        reasons.append("collection_permission_unknown")
    elif collection_allowed is False:
        reasons.append(f"collection_blocked:{collection_block_reason}")
    if robot.status is not RobotStatus.AVAILABLE:
        reasons.append(f"status:{robot.status.value}")
    if robot.fault_code is not None:
        reasons.append(f"fault:{robot.fault_code}")
    if robot.capabilities is None:
        reasons.append("missing_capabilities")
    elif "collect" not in robot.capabilities:
        reasons.append("missing_capability:collect")
    if robot.battery_fraction < config.minimum_battery_fraction:
        reasons.append("battery_below_minimum")
    if robot.expected_clean_ball_yield is None:
        reasons.append("missing_expected_clean_ball_yield")
    elif robot.expected_clean_ball_yield <= 0:
        reasons.append("nonpositive_expected_clean_ball_yield")
    if robot.replenishment_eta_minutes is None:
        reasons.append("missing_replenishment_eta")
    if robot.requires_washing is None:
        reasons.append("washing_requirement_unknown")
    elif robot.requires_washing:
        if washer_available is None:
            reasons.append("washer_availability_unknown")
        elif washer_available is False:
            reasons.append("washer_unavailable")
    if (
        first_stockout_minutes is not None
        and robot.replenishment_eta_minutes is not None
        and robot.replenishment_eta_minutes >= first_stockout_minutes
    ):
        reasons.append("arrival_not_before_first_stockout")
    return tuple(reasons)


@dataclass(frozen=True, slots=True)
class RobotCandidateTrace:
    robot_id: str
    status: RobotStatus
    raw_activity: str | None
    raw_health: str | None
    battery_fraction: float
    capabilities: tuple[str, ...] | None
    payload_balls: int
    current_task_id: str | None
    fault_code: str | None
    requires_washing: bool | None
    eligible: bool
    exclusion_reasons: tuple[str, ...]
    replenishment_eta_minutes: float | None
    expected_clean_ball_yield: int | None
    yield_provenance: str
    eta_provenance: str
    arrival_before_first_stockout: bool
    projected_stockout_with_action_minutes: float | None
    protected_through_horizon: bool
    projected_extension_minutes: float

    def __post_init__(self) -> None:
        _require_text("robot_id", self.robot_id)
        if not isinstance(self.status, RobotStatus):
            raise TypeError("status must be a RobotStatus")
        _require_optional_text("raw_activity", self.raw_activity)
        _require_optional_text("raw_health", self.raw_health)
        object.__setattr__(
            self, "battery_fraction", _require_fraction("battery_fraction", self.battery_fraction)
        )
        if self.capabilities is not None:
            if type(self.capabilities) is not tuple:
                raise TypeError("capabilities must be a tuple or None")
            normalized_capabilities = tuple(
                sorted({_require_text("capability", item) for item in self.capabilities})
            )
            object.__setattr__(self, "capabilities", normalized_capabilities)
        _require_int("payload_balls", self.payload_balls)
        _require_optional_text("current_task_id", self.current_task_id)
        _require_optional_text("fault_code", self.fault_code)
        _require_bool_or_none("requires_washing", self.requires_washing)
        if type(self.eligible) is not bool:
            raise TypeError("eligible must be a boolean")
        if type(self.exclusion_reasons) is not tuple:
            raise TypeError("exclusion_reasons must be a tuple")
        for reason in self.exclusion_reasons:
            _require_text("exclusion_reason", reason)
        if self.eligible and self.exclusion_reasons:
            raise ValueError("eligible candidates cannot have exclusion reasons")
        eta = _require_number(
            "replenishment_eta_minutes",
            self.replenishment_eta_minutes,
            allow_none=True,
        )
        object.__setattr__(self, "replenishment_eta_minutes", eta)
        if self.expected_clean_ball_yield is not None:
            _require_int(
                "expected_clean_ball_yield", self.expected_clean_ball_yield
            )
        _require_text("yield_provenance", self.yield_provenance)
        _require_text("eta_provenance", self.eta_provenance)
        if type(self.arrival_before_first_stockout) is not bool:
            raise TypeError("arrival_before_first_stockout must be a boolean")
        stockout = _require_number(
            "projected_stockout_with_action_minutes",
            self.projected_stockout_with_action_minutes,
            allow_none=True,
        )
        object.__setattr__(
            self, "projected_stockout_with_action_minutes", stockout
        )
        if type(self.protected_through_horizon) is not bool:
            raise TypeError("protected_through_horizon must be a boolean")
        if self.protected_through_horizon and stockout is not None:
            raise ValueError(
                "protected candidates cannot also have an in-horizon stockout"
            )
        extension = _require_number(
            "projected_extension_minutes", self.projected_extension_minutes
        )
        object.__setattr__(self, "projected_extension_minutes", extension)


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    trace_id: str
    policy_id: str
    policy_version: str
    policy_config: BallAvailabilityPolicyConfig
    site_id: str
    deployment_id: str
    observed_at: datetime
    source_sequence: int
    source_schema_version: str | None
    source_ref: str
    snapshot_digest: str
    range_open: bool | None
    range_open_provenance: str
    collection_allowed: bool | None
    collection_permission_provenance: str
    collection_block_reason: str | None
    washer_available: bool | None
    washer_availability_provenance: str
    clean_available: int
    clean_available_provenance: str
    clean_sensed: float | None
    clean_sensed_provenance: str
    inventory_basis: str
    inventory_balls: float
    inventory_provenance: str
    current_demand_balls_per_minute: float | None
    current_demand_provenance: str
    forecast_demand_balls_per_minute: tuple[float, ...] | None
    forecast_bucket_minutes: float | None
    forecast_demand_provenance: str
    minutes_to_close: float | None
    minutes_to_close_provenance: str
    selected_demand_basis: str
    demand_rates_used_balls_per_minute: tuple[float, ...]
    projection_horizon_minutes: float | None
    committed_inbound_batches: tuple[InboundBallBatch, ...]
    projected_stockout_without_action_minutes: float | None
    candidates: tuple[RobotCandidateTrace, ...]
    selected_robot_id: str | None
    latest_safe_dispatch_delay_minutes: float | None
    data_completeness_score: float
    missing_data_reasons: tuple[str, ...]
    rationale: tuple[str, ...]

    @classmethod
    def create(cls, **values: object) -> "DecisionTrace":
        if "trace_id" in values:
            raise ValueError("trace_id is derived and must not be supplied")
        normalized = dict(values)
        for name in (
            "clean_sensed",
            "inventory_balls",
            "current_demand_balls_per_minute",
            "forecast_bucket_minutes",
            "minutes_to_close",
            "projection_horizon_minutes",
            "projected_stockout_without_action_minutes",
            "latest_safe_dispatch_delay_minutes",
            "data_completeness_score",
        ):
            value = normalized.get(name)
            if value is not None and not isinstance(value, bool) and isinstance(
                value, (int, float)
            ):
                normalized[name] = float(value)
        for name in (
            "forecast_demand_balls_per_minute",
            "demand_rates_used_balls_per_minute",
        ):
            value = normalized.get(name)
            if type(value) is tuple:
                normalized[name] = tuple(
                    float(item)
                    if not isinstance(item, bool) and isinstance(item, (int, float))
                    else item
                    for item in value
                )
        return cls(
            trace_id=f"trace_{stable_digest(normalized)[:24]}",
            **normalized,
        )

    def __post_init__(self) -> None:
        for name in (
            "trace_id",
            "policy_id",
            "policy_version",
            "site_id",
            "deployment_id",
            "source_ref",
            "range_open_provenance",
            "collection_permission_provenance",
            "washer_availability_provenance",
            "clean_available_provenance",
            "clean_sensed_provenance",
            "inventory_provenance",
            "current_demand_provenance",
            "forecast_demand_provenance",
            "minutes_to_close_provenance",
            "selected_demand_basis",
        ):
            _require_text(name, getattr(self, name))
        _require_aware("observed_at", self.observed_at)
        if not isinstance(self.policy_config, BallAvailabilityPolicyConfig):
            raise TypeError("policy_config must be a BallAvailabilityPolicyConfig")
        if (self.policy_id, self.policy_version) != (
            self.policy_config.policy_id,
            self.policy_config.policy_version,
        ):
            raise ValueError("trace policy identity must match policy_config")
        _require_int("source_sequence", self.source_sequence)
        _require_optional_text("source_schema_version", self.source_schema_version)
        digest = _require_text("snapshot_digest", self.snapshot_digest)
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("snapshot_digest must be a lowercase SHA-256 digest")
        _require_bool_or_none("range_open", self.range_open)
        _require_bool_or_none("collection_allowed", self.collection_allowed)
        _require_bool_or_none("washer_available", self.washer_available)
        _require_optional_text("collection_block_reason", self.collection_block_reason)
        if self.collection_allowed is False and self.collection_block_reason is None:
            raise ValueError(
                "collection_block_reason is required when collection is blocked"
            )
        _require_int("clean_available", self.clean_available)
        sensed = _require_number("clean_sensed", self.clean_sensed, allow_none=True)
        object.__setattr__(self, "clean_sensed", sensed)
        if self.inventory_basis not in {"clean_available", "clean_sensed"}:
            raise ValueError("inventory_basis must identify clean_available or clean_sensed")
        expected_basis = "clean_sensed" if sensed is not None else "clean_available"
        if self.inventory_basis != expected_basis:
            raise ValueError("inventory_basis does not match sensed-value availability")
        inventory = _require_number("inventory_balls", self.inventory_balls)
        object.__setattr__(self, "inventory_balls", inventory)
        expected_inventory = sensed if sensed is not None else float(self.clean_available)
        if inventory != expected_inventory:
            raise ValueError("inventory_balls does not match the selected inventory basis")
        expected_provenance = (
            self.clean_sensed_provenance
            if sensed is not None
            else self.clean_available_provenance
        )
        if self.inventory_provenance != expected_provenance:
            raise ValueError(
                "inventory_provenance does not match the selected inventory basis"
            )
        current = _require_number(
            "current_demand_balls_per_minute",
            self.current_demand_balls_per_minute,
            allow_none=True,
        )
        object.__setattr__(self, "current_demand_balls_per_minute", current)
        forecast = self.forecast_demand_balls_per_minute
        if forecast is not None:
            if type(forecast) is not tuple or not forecast:
                raise ValueError(
                    "forecast_demand_balls_per_minute must be a non-empty tuple or None"
                )
            object.__setattr__(
                self,
                "forecast_demand_balls_per_minute",
                tuple(
                    _require_number(f"forecast[{index}]", value)
                    for index, value in enumerate(forecast)
                ),
            )
            bucket = _require_number(
                "forecast_bucket_minutes",
                self.forecast_bucket_minutes,
                minimum=1e-12,
            )
            object.__setattr__(self, "forecast_bucket_minutes", bucket)
        elif self.forecast_bucket_minutes is not None:
            raise ValueError("forecast_bucket_minutes requires a forecast")
        minutes = _require_number(
            "minutes_to_close", self.minutes_to_close, allow_none=True
        )
        object.__setattr__(self, "minutes_to_close", minutes)
        if type(self.demand_rates_used_balls_per_minute) is not tuple:
            raise TypeError("demand_rates_used_balls_per_minute must be a tuple")
        rates = tuple(
            _require_number(f"demand_rate[{index}]", value)
            for index, value in enumerate(self.demand_rates_used_balls_per_minute)
        )
        object.__setattr__(self, "demand_rates_used_balls_per_minute", rates)
        horizon = _require_number(
            "projection_horizon_minutes",
            self.projection_horizon_minutes,
            minimum=1e-12,
            allow_none=True,
        )
        object.__setattr__(self, "projection_horizon_minutes", horizon)
        if bool(rates) != (horizon is not None):
            raise ValueError("projection horizon and demand rates must appear together")
        expected_demand = derive_demand_projection(
            current_demand_balls_per_minute=current,
            forecast_demand_balls_per_minute=self.forecast_demand_balls_per_minute,
            forecast_bucket_minutes=self.forecast_bucket_minutes,
            minutes_to_close=minutes,
            config=self.policy_config,
        )
        if expected_demand is None:
            expected_rates: tuple[float, ...] = ()
            expected_horizon = None
            expected_basis = "unavailable"
        else:
            expected_rates, _, expected_horizon, expected_basis = expected_demand
        if (
            self.selected_demand_basis != expected_basis
            or rates != expected_rates
            or horizon != expected_horizon
        ):
            raise ValueError(
                "selected demand basis, rates, and horizon must match policy derivation"
            )
        if type(self.committed_inbound_batches) is not tuple or not all(
            isinstance(batch, InboundBallBatch)
            for batch in self.committed_inbound_batches
        ):
            raise TypeError("committed_inbound_batches must contain InboundBallBatch values")
        inbound_ids = [batch.source_id for batch in self.committed_inbound_batches]
        if len(inbound_ids) != len(set(inbound_ids)):
            raise ValueError("trace inbound batch source IDs must be unique")
        if any(source_id.startswith("counterfactual:") for source_id in inbound_ids):
            raise ValueError(
                "committed inbound batch source IDs cannot use the reserved "
                "counterfactual: prefix"
            )
        if tuple(
            sorted(
                self.committed_inbound_batches,
                key=lambda batch: (batch.eta_minutes, batch.source_id),
            )
        ) != self.committed_inbound_batches:
            raise ValueError("trace inbound batches must use deterministic order")
        baseline = _require_number(
            "projected_stockout_without_action_minutes",
            self.projected_stockout_without_action_minutes,
            allow_none=True,
        )
        object.__setattr__(
            self, "projected_stockout_without_action_minutes", baseline
        )
        if type(self.candidates) is not tuple or not all(
            isinstance(candidate, RobotCandidateTrace) for candidate in self.candidates
        ):
            raise TypeError("candidates must contain RobotCandidateTrace values")
        candidate_ids = [candidate.robot_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate robot IDs must be unique")
        if candidate_ids != sorted(candidate_ids):
            raise ValueError("candidates must use deterministic robot-ID order")
        for candidate in self.candidates:
            expected_arrival = bool(
                baseline is not None
                and candidate.replenishment_eta_minutes is not None
                and candidate.replenishment_eta_minutes < baseline
            )
            if candidate.arrival_before_first_stockout != expected_arrival:
                raise ValueError(
                    "candidate arrival flag does not match ETA and first stockout"
                )
            base_exclusions = candidate_safety_exclusion_reasons(
                robot=candidate,  # type: ignore[arg-type]
                config=self.policy_config,
                range_open=self.range_open,
                collection_allowed=self.collection_allowed,
                collection_block_reason=self.collection_block_reason,
                washer_available=self.washer_available,
                first_stockout_minutes=baseline,
            )
            expected_exclusions = list(base_exclusions)
            if base_exclusions and (
                candidate.projected_stockout_with_action_minutes is not None
                or candidate.protected_through_horizon
                or candidate.projected_extension_minutes != 0.0
            ):
                raise ValueError(
                    "safety-excluded candidate cannot carry a dispatch projection"
                )
            if (
                not base_exclusions
                and baseline is not None
                and rates
                and candidate.projected_extension_minutes <= 0.0
            ):
                expected_exclusions.append("no_positive_counterfactual_extension")
            if candidate.exclusion_reasons != tuple(expected_exclusions):
                raise ValueError(
                    "candidate exclusion reasons do not match the policy predicates"
                )
            if candidate.eligible != (not expected_exclusions):
                raise ValueError("candidate eligibility does not match exclusion reasons")
            if candidate.protected_through_horizon:
                if horizon is None or baseline is None:
                    raise ValueError(
                        "protected candidate requires a finite projection horizon and stockout"
                    )
                expected_extension = horizon - baseline
            elif candidate.projected_stockout_with_action_minutes is not None:
                if baseline is None:
                    raise ValueError(
                        "candidate action stockout requires a baseline stockout"
                    )
                expected_extension = max(
                    0.0,
                    candidate.projected_stockout_with_action_minutes - baseline,
                )
            else:
                expected_extension = 0.0
            if candidate.projected_extension_minutes != expected_extension:
                raise ValueError(
                    "candidate extension does not match its stockout projection"
                )
            if candidate.eligible:
                if not (
                    self.range_open is True
                    and self.collection_allowed is True
                    and candidate.status is RobotStatus.AVAILABLE
                    and candidate.fault_code is None
                    and candidate.capabilities is not None
                    and "collect" in candidate.capabilities
                    and candidate.battery_fraction
                    >= self.policy_config.minimum_battery_fraction
                    and candidate.expected_clean_ball_yield is not None
                    and candidate.expected_clean_ball_yield > 0
                    and candidate.replenishment_eta_minutes is not None
                    and candidate.requires_washing is not None
                    and (
                        not candidate.requires_washing
                        or self.washer_available is True
                    )
                ):
                    raise ValueError(
                        "eligible candidate violates a safety eligibility predicate"
                    )
            elif not candidate.exclusion_reasons:
                raise ValueError("ineligible candidates require exclusion reasons")
        _require_optional_text("selected_robot_id", self.selected_robot_id)
        latest_delay = _require_number(
            "latest_safe_dispatch_delay_minutes",
            self.latest_safe_dispatch_delay_minutes,
            minimum=-math.inf,
            allow_none=True,
        )
        object.__setattr__(
            self, "latest_safe_dispatch_delay_minutes", latest_delay
        )
        beneficial = [
            candidate
            for candidate in self.candidates
            if candidate.eligible
            and candidate.arrival_before_first_stockout
            and candidate.projected_extension_minutes > 0.0
        ]
        expected_selected = (
            None
            if not beneficial
            else min(
                beneficial,
                key=lambda candidate: (
                    -candidate.projected_extension_minutes,
                    candidate.replenishment_eta_minutes
                    if candidate.replenishment_eta_minutes is not None
                    else float("inf"),
                    candidate.robot_id,
                ),
            ).robot_id
        )
        if self.selected_robot_id != expected_selected:
            raise ValueError(
                "selected robot does not match deterministic candidate ranking"
            )
        if self.selected_robot_id is None:
            if latest_delay is not None:
                raise ValueError("latest dispatch delay requires a selected robot")
        else:
            selected = [
                candidate
                for candidate in self.candidates
                if candidate.robot_id == self.selected_robot_id
            ]
            if len(selected) != 1:
                raise ValueError("selected robot must appear exactly once in candidates")
            candidate = selected[0]
            if not (
                candidate.eligible
                and candidate.arrival_before_first_stockout
                and candidate.projected_extension_minutes > 0.0
            ):
                raise ValueError("selected robot must be an eligible beneficial candidate")
            if latest_delay is None or baseline is None:
                raise ValueError(
                    "selected robot requires stockout and latest-dispatch projections"
                )
            assert candidate.replenishment_eta_minutes is not None
            expected_delay = (
                baseline
                - candidate.replenishment_eta_minutes
                - self.policy_config.safety_buffer_minutes
            )
            if latest_delay != expected_delay:
                raise ValueError(
                    "latest dispatch delay does not match stockout, ETA, and safety buffer"
                )
        completeness = _require_fraction(
            "data_completeness_score", self.data_completeness_score
        )
        object.__setattr__(self, "data_completeness_score", completeness)
        for name in ("missing_data_reasons", "rationale"):
            values = getattr(self, name)
            if type(values) is not tuple:
                raise TypeError(f"{name} must be a tuple")
            for value in values:
                _require_text(name[:-1], value)
        expected_trace_id = f"trace_{stable_digest(self._content_values())[:24]}"
        if self.trace_id != expected_trace_id:
            raise ValueError("trace_id does not match the complete decision trace")

    def _content_values(self) -> dict[str, object]:
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name != "trace_id"
        }


@dataclass(frozen=True, slots=True)
class Recommendation:
    recommendation_id: str
    site_id: str
    deployment_id: str
    issued_at: datetime
    execute_before: datetime
    action: RecommendationAction
    target_robot_id: str | None
    policy_id: str
    policy_version: str
    decision_trace_id: str
    expected_stockout_without_action_minutes: float | None
    expected_stockout_with_action_minutes: float | None
    protected_through_horizon: bool
    projected_stockout_delay_minutes: float
    summary: str

    @classmethod
    def create(cls, **values: object) -> "Recommendation":
        if "recommendation_id" in values:
            raise ValueError("recommendation_id is derived and must not be supplied")
        normalized = dict(values)
        for name in (
            "expected_stockout_without_action_minutes",
            "expected_stockout_with_action_minutes",
            "projected_stockout_delay_minutes",
        ):
            value = normalized.get(name)
            if value is not None and not isinstance(value, bool) and isinstance(
                value, (int, float)
            ):
                normalized[name] = float(value)
        return cls(
            recommendation_id=f"rec_{stable_digest(normalized)[:24]}",
            **normalized,
        )

    def __post_init__(self) -> None:
        for name in (
            "recommendation_id",
            "site_id",
            "deployment_id",
            "policy_id",
            "policy_version",
            "decision_trace_id",
            "summary",
        ):
            _require_text(name, getattr(self, name))
        _require_aware("issued_at", self.issued_at)
        _require_aware("execute_before", self.execute_before)
        if self.execute_before < self.issued_at:
            raise ValueError("execute_before cannot precede issued_at")
        if not isinstance(self.action, RecommendationAction):
            raise TypeError("action must be a RecommendationAction")
        if self.action is RecommendationAction.DISPATCH_COLLECTOR:
            _require_text("target_robot_id", self.target_robot_id)
        elif self.target_robot_id is not None:
            raise ValueError("operator intervention must not target a robot")
        for name in (
            "expected_stockout_without_action_minutes",
            "expected_stockout_with_action_minutes",
        ):
            value = _require_number(name, getattr(self, name), allow_none=True)
            object.__setattr__(self, name, value)
        if type(self.protected_through_horizon) is not bool:
            raise TypeError("protected_through_horizon must be a boolean")
        delay = _require_number(
            "projected_stockout_delay_minutes", self.projected_stockout_delay_minutes
        )
        object.__setattr__(self, "projected_stockout_delay_minutes", delay)
        expected_id = f"rec_{stable_digest(self._content_values())[:24]}"
        if self.recommendation_id != expected_id:
            raise ValueError(
                "recommendation_id does not match the complete recommendation"
            )

    def _content_values(self) -> dict[str, object]:
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name != "recommendation_id"
        }


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    verdict: EvaluationVerdict
    trace: DecisionTrace
    recommendation: Recommendation | None

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, EvaluationVerdict):
            raise TypeError("verdict must be an EvaluationVerdict")
        if not isinstance(self.trace, DecisionTrace):
            raise TypeError("trace must be a DecisionTrace")
        if self.recommendation is not None and not isinstance(
            self.recommendation, Recommendation
        ):
            raise TypeError("recommendation must be a Recommendation or None")

        # Imported lazily so contracts remain the dependency root while the
        # composed policy result still receives full semantic verification.
        from .semantics import validate_policy_evaluation

        validate_policy_evaluation(
            verdict=self.verdict,
            trace=self.trace,
            recommendation=self.recommendation,
        )
