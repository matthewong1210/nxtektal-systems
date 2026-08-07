"""NXTektal Site OS facility-state layer (Phase 0.75).

A typed, read-only projection over the validated ``nxt_range_ops``
simulator: "the unit of autonomy is the site, not the robot."

Consumers import from this package only:

- :class:`FacilityState` — the frozen state contract
- :func:`build_facility_state` — capture one snapshot from a live sim
- :func:`estimate_stockout` / :func:`classify_state` /
  :func:`derive_indicators` — pure operational metrics over a snapshot
"""

from .analysis import (
    CRITICAL_FLEET_OPERABLE_FRAC,
    CRITICAL_STOCKOUT_HORIZON_MIN,
    STRAINED_STOCKOUT_HORIZON_MIN,
    FacilityIndicators,
    OperationalState,
    StockoutEstimate,
    classify_state,
    derive_indicators,
    estimate_stockout,
)
from .build import build_facility_state
from .state import (
    BallFlow,
    ChargingState,
    Counts,
    DemandState,
    EnvironmentState,
    FacilityMeta,
    FacilityState,
    FleetSummary,
    StaffState,
    WasherState,
)

__all__ = [
    "BallFlow",
    "ChargingState",
    "Counts",
    "CRITICAL_FLEET_OPERABLE_FRAC",
    "CRITICAL_STOCKOUT_HORIZON_MIN",
    "STRAINED_STOCKOUT_HORIZON_MIN",
    "DemandState",
    "EnvironmentState",
    "FacilityIndicators",
    "FacilityMeta",
    "FacilityState",
    "FleetSummary",
    "OperationalState",
    "StaffState",
    "StockoutEstimate",
    "WasherState",
    "build_facility_state",
    "classify_state",
    "derive_indicators",
    "estimate_stockout",
]
