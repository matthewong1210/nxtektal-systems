"""Regression tests for defects confirmed by the adversarial review pass."""

from __future__ import annotations

from nxt_range_ops.config.models import TimeWindow
from nxt_range_ops.core.directives import (
    AssignCollection,
    RequestHumanAssistance,
    SendToHandoff,
)
from nxt_range_ops.core.entities import HumanAssistReason, RobotActivity
from nxt_range_ops.core.events import EventKind
from nxt_range_ops.core.sim import RangeSimulation, _merge_windows
from nxt_range_ops.scenarios.generators import make_scenario

from tests.range_ops.conftest import short_scenario


def _deplete(sim: RangeSimulation, robot_id: str) -> None:
    robot = sim._robots[robot_id]
    robot.battery_wh = 0.01 * robot.cfg.battery_capacity_wh.value
    sim.advance(60.0)
    assert sim.robot_or_none(robot_id).activity is RobotActivity.FAILED


def test_battery_depleted_robot_recovers_after_human_assist():
    """A depleted robot must be genuinely recoverable — the human fix
    restores at least the minimum reserve, so no request/re-fail livelock."""
    scenario = short_scenario()
    sim = RangeSimulation(scenario, seed=1)
    _deplete(sim, "R1")
    failures_before = sim.metrics.hard_failures
    assert sim.apply_directive(
        RequestHumanAssistance(robot_id="R1", reason=HumanAssistReason.BATTERY_DEPLETED)
    ).allowed
    sim.advance(900.0)  # response + fix
    robot = sim.robot_or_none("R1")
    assert robot.activity is RobotActivity.IDLE
    # Restored to the minimum reserve at fix time; a little idle drain may
    # have accrued between the fix and this snapshot.
    assert robot.battery_frac >= scenario.safety.min_battery_reserve_frac - 0.01
    # And it stays recovered across further flushes (no re-fail loop).
    sim.advance(300.0)
    assert sim.robot_or_none("R1").activity is RobotActivity.IDLE
    assert sim.metrics.hard_failures == failures_before
    # A charge command is now legal again.
    from nxt_range_ops.core.directives import SendToCharge

    assert sim.apply_directive(SendToCharge(robot_id="R1")).allowed


def test_concurrent_unloaders_cannot_overflow_buffer():
    """Check-then-act across the unload yield must not breach the buffer cap."""
    base = make_scenario("normal_weekday")
    scenario = short_scenario(
        stations=[
            s.model_copy(
                update={
                    "dock_slots": 3,
                    "buffer_capacity_balls": 700,
                }
            )
            for s in base.stations
        ],
        washer=base.washer.model_copy(
            update={
                "balls_per_minute": base.washer.balls_per_minute.model_copy(
                    update={"value": 1.0}
                ),
                "batch_size_balls": 10,
            }
        ),
    )
    sim = RangeSimulation(scenario, seed=2)
    for rid in ("R1", "R2", "R3"):
        moved = sim.ledger.move("dispenser", f"robot:{rid}", 600)
        assert moved == 600
        sim._robots[rid].payload_balls = moved
        assert sim.apply_directive(SendToHandoff(robot_id=rid)).allowed
    for _ in range(240):
        sim.advance(30.0)
        snap = sim.station_snapshots()[0]
        assert snap.buffer_balls <= snap.buffer_capacity_balls, (
            f"buffer overflow: {snap.buffer_balls}/{snap.buffer_capacity_balls}"
        )


def test_inflight_travel_drains_battery_each_step():
    """The shield must never see a stale battery for a mid-travel robot:
    travel energy is pro-rated at every control-interval flush."""
    scenario = short_scenario()
    sim = RangeSimulation(scenario, seed=3)
    start_battery = sim.robot_or_none("R1").battery_frac
    assert sim.apply_directive(AssignCollection(robot_id="R1", zone_id="Z6")).allowed
    sim.advance(60.0)
    r1 = sim.robot_or_none("R1")
    # Z6 is ~210 m out at 1.5 m/s: R1 must still be en route after 60 s.
    assert r1.activity is RobotActivity.TRAVELING, (
        f"precondition failed: R1 is {r1.activity.value}, not traveling"
    )
    assert r1.battery_frac < start_battery, (
        "mid-travel battery must already reflect pro-rated drain"
    )
    assert sim.metrics.empty_travel_s > 0


def test_merge_windows_handles_overlap_and_adjacency():
    def w(a, b):
        return TimeWindow(start_minute=a, end_minute=b)

    assert _merge_windows([w(60, 120), w(90, 180)]) == [(60, 180)]
    assert _merge_windows([w(60, 120), w(120, 180)]) == [(60, 180)]
    assert _merge_windows([w(300, 360), w(60, 120)]) == [(60, 120), (300, 360)]
    assert _merge_windows([]) == []


def test_overlapping_closure_windows_keep_zone_closed():
    base = make_scenario("normal_weekday")
    scenario = short_scenario(
        zones=[
            z.model_copy(
                update={
                    "closure_windows": [
                        TimeWindow(start_minute=400, end_minute=500),
                        TimeWindow(start_minute=450, end_minute=600),
                    ]
                }
            )
            if z.zone_id == "Z1"
            else z
            for z in base.zones
        ]
    )
    sim = RangeSimulation(scenario, seed=4)
    closed_span_checked = False
    for _ in range(300):
        sim.advance(60.0)
        minute = sim.minute_of_day
        zone = sim.zone_or_none("Z1")
        if 401 <= minute < 599:
            assert not zone.is_open, f"Z1 reported open at minute {minute}"
            closed_span_checked = True
        if minute >= 601:
            assert zone.is_open
            break
    assert closed_span_checked


def test_time_windows_fire_on_minute_of_day_not_open_relative():
    """TimeWindow minutes are minutes since midnight, and the sim clock
    starts at ``open_seconds`` — so the handoff_station_outage window
    (720-840) fires at 12:00-14:00 sharp, not 6 h late as it would if the
    scheduler read window minutes as minutes-since-open."""
    scenario = make_scenario("handoff_station_outage")
    sim = RangeSimulation(scenario, seed=7)
    assert sim.now == scenario.hours.open_seconds
    while not sim.facility_closed:
        sim.advance(600.0)
    times = {
        ev.kind: ev.t_s
        for ev in sim.events.records
        if ev.kind in (EventKind.STATION_OUTAGE, EventKind.STATION_RESTORED)
    }
    assert times[EventKind.STATION_OUTAGE] == 720 * 60.0
    assert times[EventKind.STATION_RESTORED] == 840 * 60.0


def test_demand_with_all_zones_closed_is_not_stockout():
    base = make_scenario("normal_weekday")
    scenario = short_scenario(
        zones=[
            z.model_copy(
                update={
                    "closure_windows": [TimeWindow(start_minute=0, end_minute=1440)]
                }
            )
            for z in base.zones
        ]
    )
    sim = RangeSimulation(scenario, seed=5)
    sim.advance(3600.0)
    assert sim.metrics.stockout_minutes == 0.0
    assert sim.metrics.demand_balls_total == 0
