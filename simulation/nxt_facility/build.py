"""Snapshot builder: RangeSimulation -> FacilityState.

Pure read of the simulator's existing public query surface, safe to call
at any time without perturbing the simulation.

RNG DISCIPLINE (load-bearing): this module must NEVER call
``RangeSimulation.sensed_zone_counts()`` or ``sensed_battery_frac()`` —
each body draws ``self._rng_sensors.normal(...)`` per call, so calling
them here would silently shift every subsequent sensed observation and
break byte-identical seed+action replay.
``sensed_dispenser_count()`` is a pure buffer read and is allowed.
Enforced by tests/facility/test_regressions.py (RNG-state equality plus a
static source scan of this file).
"""

from __future__ import annotations

from nxt_range_ops.core import ledger as ledger_mod
from nxt_range_ops.core.entities import (
    INOPERATIVE_ACTIVITIES,
    RobotActivity,
)
from nxt_range_ops.core.sim import RangeSimulation

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


def _ledger_group(counts: dict[str, int], ids: list[str], loc) -> Counts:
    return tuple((entity_id, counts[loc(entity_id)]) for entity_id in sorted(ids))


def build_facility_state(sim: RangeSimulation) -> FacilityState:
    """Capture one immutable FacilityState from a live simulation.

    Snapshots are most meaningful right after ``advance()`` (accounting
    flushed, conservation asserted), but building one at any time is safe
    and side-effect free.
    """
    scenario = sim.scenario
    counts = sim.ledger.counts()
    robots = tuple(sim.robot_snapshots())
    zones = tuple(sim.zone_snapshots())
    stations = tuple(sim.station_snapshots())
    metrics = sim.metrics

    ball_flow = BallFlow(
        total_balls=scenario.total_balls,
        clean_available=sim.dispenser_count(),
        # float() coercions here and below keep numpy scalar types (which
        # leak out of the sim's noise draws) out of the pure-Python contract.
        clean_sensed=float(sim.sensed_dispenser_count()),
        in_wash=sim.washer_wip(),
        dirty_buffered=_ledger_group(counts, scenario.station_ids, ledger_mod.station_loc),
        on_field=_ledger_group(counts, scenario.zone_ids, ledger_mod.zone_loc),
        in_transit=_ledger_group(counts, scenario.robot_ids, ledger_mod.robot_loc),
    )

    inoperative = sum(1 for r in robots if r.activity in INOPERATIVE_ACTIVITIES)
    charging = sum(1 for r in robots if r.activity is RobotActivity.CHARGING)

    return FacilityState(
        meta=FacilityMeta(
            t_s=sim.now,
            minute_of_day=sim.minute_of_day,
            facility_open=sim.facility_open,
            scenario_name=scenario.name,
            seed=sim.seed,
        ),
        ball_flow=ball_flow,
        washer=WasherState(
            throughput_balls_per_minute=scenario.washer.balls_per_minute.value,
            batch_size_balls=scenario.washer.batch_size_balls,
            wip=ball_flow.in_wash,
        ),
        demand=DemandState(
            forecast_balls_per_minute=tuple(float(x) for x in sim.forecast_window()),
            forecast_bucket_minutes=scenario.demand.forecast_bucket_minutes,
            minutes_to_close=max(0.0, scenario.hours.close_minute - sim.minute_of_day),
            demand_balls_total=metrics.demand_balls_total,
            demand_balls_served=metrics.demand_balls_served,
            stockout_minutes=metrics.stockout_minutes,
            service_availability=metrics.service_availability,
        ),
        fleet=FleetSummary(
            total=len(robots),
            operable=len(robots) - inoperative,
            inoperative=inoperative,
            charging=charging,
            awaiting_human=sum(1 for r in robots if r.awaiting_human),
        ),
        charging=ChargingState(
            slots=scenario.charger.slots,
            in_use=charging,
            queue_length=sim.charger_queue_length(),
        ),
        staff=StaffState(*sim.staff_summary()),
        environment=EnvironmentState(
            wet_ground_speed_multiplier=scenario.skills.wet_ground_speed_multiplier,
            zones_open=sum(1 for z in zones if z.is_open),
            zones_total=len(zones),
            stations_open=sum(1 for s in stations if s.is_open),
            stations_total=len(stations),
        ),
        robots=robots,
        zones=zones,
        stations=stations,
    )
