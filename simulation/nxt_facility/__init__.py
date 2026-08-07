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
from .briefing import render_briefing
from .decisions import Confidence, Recommendation, Urgency, recommend
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
    "Confidence",
    "Counts",
    "Recommendation",
    "Urgency",
    "recommend",
    "render_briefing",
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


def __getattr__(name: str):
    # Lazy so the contract (state + analysis) stays importable without the
    # simulation stack; the builder needs the simulator and is loaded only
    # when actually requested (PEP 562).
    if name == "build_facility_state":
        from .build import build_facility_state

        return build_facility_state
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
