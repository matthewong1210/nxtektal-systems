"""Typed, frozen Site OS facility-state contract.

``FacilityState`` is a read-only projection over existing simulation truth
in ``nxt_range_ops``. It duplicates no live state: every field is copied
scalar data captured at one instant by
:func:`nxt_facility.build.build_facility_state`. The simulator remains the
single source of truth; this object is what agents, sensors, dashboards,
and future Site OS services consume.

This module deliberately imports no simulation libraries (simpy,
gymnasium, numpy) so the same contract can later be populated from real
facility telemetry rather than the simulator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    # Annotation-only: importing these at runtime would execute
    # nxt_range_ops.core.__init__, which eagerly imports the simulator
    # (simpy/numpy) and would break this module's no-simulation-stack
    # contract. The snapshot classes are never instantiated here; to_dict()
    # and robot_or_none() are duck-typed.
    from nxt_range_ops.core.entities import (
        RobotStateSnapshot,
        StationStateSnapshot,
        ZoneStateSnapshot,
    )

# (entity_id, ball_count) pairs, sorted by id for deterministic serialization.
Counts = tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class FacilityMeta:
    """Identity and clock context for one snapshot."""

    t_s: float
    minute_of_day: float
    facility_open: bool
    scenario_name: str
    seed: int


@dataclass(frozen=True)
class BallFlow:
    """The clean/dirty ball circuit, named explicitly.

    The underlying ledger is positional (one location per ball); this view
    names the flows: clean at the dispenser, in-wash WIP, dirty buffered at
    handoff stations, on-field awaiting collection, in-transit in robot
    payloads. ``clean_sensed`` is the delayed, RNG-free operator reading —
    the view an inventory display would show.
    """

    total_balls: int
    clean_available: int
    clean_sensed: float
    in_wash: int
    dirty_buffered: Counts
    on_field: Counts
    in_transit: Counts

    @property
    def dirty_buffered_total(self) -> int:
        return sum(n for _, n in self.dirty_buffered)

    @property
    def on_field_total(self) -> int:
        return sum(n for _, n in self.on_field)

    @property
    def in_transit_total(self) -> int:
        return sum(n for _, n in self.in_transit)

    @property
    def conserved(self) -> bool:
        """Every ball accounted for across the five named flows."""
        return (
            self.clean_available
            + self.in_wash
            + self.dirty_buffered_total
            + self.on_field_total
            + self.in_transit_total
        ) == self.total_balls


@dataclass(frozen=True)
class WasherState:
    throughput_balls_per_minute: float
    batch_size_balls: int
    wip: int


@dataclass(frozen=True)
class DemandState:
    """Forecast window plus served-demand history from the metrics ledger."""

    forecast_balls_per_minute: tuple[float, ...]
    forecast_bucket_minutes: int
    minutes_to_close: float
    demand_balls_total: int
    demand_balls_served: int
    stockout_minutes: float
    service_availability: float


@dataclass(frozen=True)
class FleetSummary:
    total: int
    operable: int
    inoperative: int
    charging: int
    awaiting_human: int


@dataclass(frozen=True)
class ChargingState:
    slots: int
    in_use: int
    queue_length: int


@dataclass(frozen=True)
class StaffState:
    capacity: int
    busy: int
    queued_requests: int


@dataclass(frozen=True)
class EnvironmentState:
    """Static terrain/closure context surfaced from configuration.

    There is no dynamic terrain model in the simulator today; this group
    surfaces what exists rather than inventing state.
    """

    wet_ground_speed_multiplier: float
    zones_open: int
    zones_total: int
    stations_open: int
    stations_total: int


@dataclass(frozen=True)
class FacilityState:
    """One immutable snapshot of the whole facility."""

    meta: FacilityMeta
    ball_flow: BallFlow
    washer: WasherState
    demand: DemandState
    fleet: FleetSummary
    charging: ChargingState
    staff: StaffState
    environment: EnvironmentState
    robots: tuple[RobotStateSnapshot, ...]
    zones: tuple[ZoneStateSnapshot, ...]
    stations: tuple[StationStateSnapshot, ...]

    def to_dict(self) -> dict:
        """JSON-ready dict; nested snapshots use their own to_dict()."""
        return {
            "meta": asdict(self.meta),
            "ball_flow": {
                "total_balls": self.ball_flow.total_balls,
                "clean_available": self.ball_flow.clean_available,
                "clean_sensed": round(self.ball_flow.clean_sensed, 6),
                "in_wash": self.ball_flow.in_wash,
                "dirty_buffered": dict(self.ball_flow.dirty_buffered),
                "on_field": dict(self.ball_flow.on_field),
                "in_transit": dict(self.ball_flow.in_transit),
                "conserved": self.ball_flow.conserved,
            },
            "washer": asdict(self.washer),
            "demand": asdict(self.demand),
            "fleet": asdict(self.fleet),
            "charging": asdict(self.charging),
            "staff": asdict(self.staff),
            "environment": asdict(self.environment),
            "robots": [r.to_dict() for r in self.robots],
            "zones": [z.to_dict() for z in self.zones],
            "stations": [s.to_dict() for s in self.stations],
        }

    def robot_or_none(self, robot_id: str) -> Optional[RobotStateSnapshot]:
        for snapshot in self.robots:
            if snapshot.robot_id == robot_id:
                return snapshot
        return None
