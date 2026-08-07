"""Builder correctness: FacilityState mirrors simulation truth exactly."""

from __future__ import annotations

import json

from nxt_range_ops.core import ledger as ledger_mod
from nxt_range_ops.core.entities import RobotActivity

from nxt_facility.build import _ledger_group, build_facility_state


def advance_minutes(sim, minutes: float) -> None:
    sim.advance(minutes * 60.0)


def assert_state_matches_sim(state, sim) -> None:
    counts = sim.ledger.counts()
    scenario = sim.scenario

    assert state.meta.t_s == sim.now
    assert state.meta.minute_of_day == sim.minute_of_day
    assert state.meta.facility_open == sim.facility_open
    assert state.meta.scenario_name == scenario.name
    assert state.meta.seed == sim.seed

    flow = state.ball_flow
    assert flow.total_balls == scenario.total_balls
    assert flow.clean_available == sim.dispenser_count()
    assert flow.clean_sensed == sim.sensed_dispenser_count()
    assert flow.in_wash == sim.washer_wip()
    assert dict(flow.dirty_buffered) == {
        s: counts[ledger_mod.station_loc(s)] for s in scenario.station_ids
    }
    assert dict(flow.on_field) == {
        z: counts[ledger_mod.zone_loc(z)] for z in scenario.zone_ids
    }
    assert dict(flow.in_transit) == {
        r: counts[ledger_mod.robot_loc(r)] for r in scenario.robot_ids
    }
    assert flow.conserved is True

    assert state.washer.throughput_balls_per_minute == scenario.washer.balls_per_minute.value
    assert state.washer.batch_size_balls == scenario.washer.batch_size_balls
    assert state.washer.wip == flow.in_wash

    assert state.demand.forecast_balls_per_minute == tuple(sim.forecast_window())
    assert state.demand.forecast_bucket_minutes == scenario.demand.forecast_bucket_minutes
    assert state.demand.minutes_to_close == max(
        0.0, scenario.hours.close_minute - sim.minute_of_day
    )
    assert state.demand.demand_balls_total == sim.metrics.demand_balls_total
    assert state.demand.demand_balls_served == sim.metrics.demand_balls_served
    assert state.demand.stockout_minutes == sim.metrics.stockout_minutes
    assert state.demand.service_availability == sim.metrics.service_availability

    robots = sim.robot_snapshots()
    assert state.robots == tuple(robots)
    assert state.zones == tuple(sim.zone_snapshots())
    assert state.stations == tuple(sim.station_snapshots())

    assert state.fleet.total == len(robots)
    assert state.fleet.operable + state.fleet.inoperative == state.fleet.total
    assert state.fleet.charging == sum(
        1 for r in robots if r.activity is RobotActivity.CHARGING
    )
    assert state.fleet.awaiting_human == sum(1 for r in robots if r.awaiting_human)

    assert state.charging.slots == scenario.charger.slots
    assert state.charging.in_use == state.fleet.charging
    assert state.charging.queue_length == sim.charger_queue_length()

    assert state.staff == type(state.staff)(*sim.staff_summary())

    env = state.environment
    assert env.wet_ground_speed_multiplier == scenario.skills.wet_ground_speed_multiplier
    assert env.zones_total == len(scenario.zones)
    assert env.zones_open == sum(1 for z in sim.zone_snapshots() if z.is_open)
    assert env.stations_total == len(scenario.stations)
    assert env.stations_open == sum(1 for s in sim.station_snapshots() if s.is_open)


def test_builder_matches_fresh_sim(sim):
    assert_state_matches_sim(build_facility_state(sim), sim)


def test_builder_matches_mid_episode_sim(sim):
    advance_minutes(sim, 90.0)
    assert_state_matches_sim(build_facility_state(sim), sim)


def test_consecutive_builds_are_identical(sim):
    advance_minutes(sim, 30.0)
    first = json.dumps(build_facility_state(sim).to_dict(), sort_keys=True)
    second = json.dumps(build_facility_state(sim).to_dict(), sort_keys=True)
    assert first == second


def test_ledger_group_sorts_ids_deterministically():
    # The Counts contract promises id-sorted pairs regardless of input order;
    # scenario ids happen to come pre-sorted, so pin it with unsorted input.
    counts = {"zone:zb": 2, "zone:za": 1, "zone:zc": 3}
    grouped = _ledger_group(counts, ["zc", "zb", "za"], lambda z: f"zone:{z}")
    assert grouped == (("za", 1), ("zb", 2), ("zc", 3))
