"""Pure operational metrics over FacilityState. No simulation access.

Every function here is state in → answers out: the same code can run
against a live simulation snapshot, a serialized state, or (later) real
facility telemetry. Nothing in this module may import simulation
libraries or touch RangeSimulation.

PLACEHOLDER PROVENANCE: the classification thresholds below are
engineering placeholders (source: placeholder, per the house provenance
policy). They gate demo classifications only and must never be presented
as validated operating policy.

ESTIMATE SEMANTICS: `estimate_stockout` is an expected-value projection
over the *frozen day forecast* carried by the snapshot — the forecast is
deliberately biased/noisy in some scenarios (e.g. demand_forecast_error),
so the ETA inherits that error by design and must be labeled an estimate,
never ground truth. Within each forecast bucket the washer is modeled at
its average feasible rate (supply spread across the bucket); this is a
deterministic approximation, not a simulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .state import FacilityState

# --- Placeholder thresholds (source: placeholder) --------------------------

CRITICAL_STOCKOUT_HORIZON_MIN = 30.0
STRAINED_STOCKOUT_HORIZON_MIN = 120.0
CRITICAL_FLEET_OPERABLE_FRAC = 0.34


class OperationalState(str, Enum):
    """Coarse facility mode, classified by ordered threshold rules."""

    CLOSED = "closed"
    NOMINAL = "nominal"
    STRAINED = "strained"
    CRITICAL = "critical"
    STOCKOUT = "stockout"


@dataclass(frozen=True)
class StockoutEstimate:
    """Projected clean-ball stockout within the forecast horizon.

    ``eta_minutes`` is minutes from the snapshot instant; ``None`` means no
    projected stockout within ``horizon_minutes``. ``limited_by`` names the
    binding constraint at the crossing: ``"demand"`` (washer at full rate
    still loses to demand) or ``"dirty_supply"`` (washer starved of
    washable balls), else ``"none"``.
    """

    eta_minutes: Optional[float]
    horizon_minutes: float
    limited_by: str


def estimate_stockout(state: FacilityState) -> StockoutEstimate:
    """Deterministic mass-balance walk over the snapshot's forecast window.

    Washable supply = in_wash + dirty_buffered + in_transit (payloads en
    route to stations). On-field balls are excluded: their arrival depends
    on collection decisions this layer does not model.
    """
    flow = state.ball_flow
    demand = state.demand
    bucket_min = float(demand.forecast_bucket_minutes)
    horizon = len(demand.forecast_balls_per_minute) * bucket_min

    clean = float(flow.clean_available)
    supply = float(flow.in_wash + flow.dirty_buffered_total + flow.in_transit_total)
    throughput = state.washer.throughput_balls_per_minute

    if clean <= 0:
        return StockoutEstimate(eta_minutes=0.0, horizon_minutes=horizon, limited_by="none")

    elapsed = 0.0
    for demand_rate in demand.forecast_balls_per_minute:
        wash_rate = min(throughput, supply / bucket_min)
        net = wash_rate - demand_rate
        if clean + net * bucket_min <= 0:
            # net < 0 is guaranteed here (clean > 0), so the denominator
            # of the linear interpolation is strictly positive.
            eta = elapsed + clean / (demand_rate - wash_rate)
            limited = "demand" if wash_rate >= throughput else "dirty_supply"
            return StockoutEstimate(
                eta_minutes=eta, horizon_minutes=horizon, limited_by=limited
            )
        clean += net * bucket_min
        supply = max(0.0, supply - wash_rate * bucket_min)
        elapsed += bucket_min

    return StockoutEstimate(eta_minutes=None, horizon_minutes=horizon, limited_by="none")


def classify_state(
    state: FacilityState, stockout: Optional[StockoutEstimate] = None
) -> OperationalState:
    """Ordered threshold rules; first match wins."""
    if not state.meta.facility_open:
        return OperationalState.CLOSED
    if state.ball_flow.clean_available <= 0:
        return OperationalState.STOCKOUT

    est = stockout if stockout is not None else estimate_stockout(state)
    fleet = state.fleet
    operable_frac = fleet.operable / fleet.total if fleet.total else 1.0

    if est.eta_minutes is not None and est.eta_minutes <= CRITICAL_STOCKOUT_HORIZON_MIN:
        return OperationalState.CRITICAL
    if operable_frac < CRITICAL_FLEET_OPERABLE_FRAC:
        return OperationalState.CRITICAL

    staff_saturated = (
        state.staff.capacity > 0
        and state.staff.busy >= state.staff.capacity
        and state.staff.queued_requests > 0
    )
    if (
        (est.eta_minutes is not None and est.eta_minutes <= STRAINED_STOCKOUT_HORIZON_MIN)
        or state.environment.stations_open < state.environment.stations_total
        or staff_saturated
        or fleet.inoperative > 0
    ):
        return OperationalState.STRAINED

    return OperationalState.NOMINAL


@dataclass(frozen=True)
class FacilityIndicators:
    """Normalized 0..1 health indicators derived from one snapshot."""

    clean_frac: float
    washable_supply_frac: float
    fleet_operable_frac: float
    stations_open_frac: float
    zones_open_frac: float
    staff_utilization: float
    service_availability: float
    demand_fill_rate: float


def derive_indicators(state: FacilityState) -> FacilityIndicators:
    flow = state.ball_flow
    env = state.environment
    total = flow.total_balls or 1
    demand = state.demand
    return FacilityIndicators(
        clean_frac=flow.clean_available / total,
        washable_supply_frac=(
            flow.in_wash + flow.dirty_buffered_total + flow.in_transit_total
        )
        / total,
        fleet_operable_frac=(
            state.fleet.operable / state.fleet.total if state.fleet.total else 1.0
        ),
        stations_open_frac=(
            env.stations_open / env.stations_total if env.stations_total else 1.0
        ),
        zones_open_frac=env.zones_open / env.zones_total if env.zones_total else 1.0,
        staff_utilization=(
            state.staff.busy / state.staff.capacity if state.staff.capacity else 0.0
        ),
        service_availability=demand.service_availability,
        demand_fill_rate=(
            demand.demand_balls_served / demand.demand_balls_total
            if demand.demand_balls_total > 0
            else 1.0
        ),
    )
