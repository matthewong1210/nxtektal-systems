"""Station capacity and queue behavior (handoff docks, charger slots)."""

from __future__ import annotations

from nxt_range_ops.core.directives import SendToCharge, SendToHandoff
from nxt_range_ops.core.entities import RobotActivity
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.scenarios.generators import make_scenario

from tests.range_ops.conftest import run_policy_episode, short_scenario


def test_station_capacity_never_exceeded_all_day():
    scenario = make_scenario("weekend_peak")
    env = RangeOpsEnv(scenario)
    _, _, infos, _ = run_policy_episode(env, "nearest_available_robot", seed=13)
    for info in infos:
        for station in info["stations"]:
            cfg = next(
                s for s in scenario.stations if s.station_id == station["station_id"]
            )
            assert station["docked"] <= cfg.dock_slots
            assert station["queue_length"] <= cfg.max_queue_length
            assert station["buffer_balls"] <= cfg.buffer_capacity_balls


def test_charger_with_one_slot_queues_fifo():
    scenario = short_scenario("charger_congestion")
    sim = RangeSimulation(scenario, seed=4)
    for rid in ("R1", "R2", "R3"):
        sim._robots[rid].battery_wh = 0.3 * sim._robots[rid].cfg.battery_capacity_wh.value
    assert sim.apply_directive(SendToCharge(robot_id="R1")).allowed
    assert sim.apply_directive(SendToCharge(robot_id="R2")).allowed
    assert sim.apply_directive(SendToCharge(robot_id="R3")).allowed
    sim.advance(300.0)  # all arrive; only one slot
    states = {r.robot_id: r.activity for r in sim.robot_snapshots()}
    charging = [r for r, a in states.items() if a is RobotActivity.CHARGING]
    queued = [r for r, a in states.items() if a is RobotActivity.QUEUED_CHARGER]
    assert len(charging) == 1
    assert len(queued) == 2
    assert sim.charger_queue_length() == 2
    first_charging = charging[0]
    # FIFO: whoever charges first finishes and hands the slot to the next.
    for _ in range(600):
        sim.advance(60.0)
        states = {r.robot_id: r.activity for r in sim.robot_snapshots()}
        if states[first_charging] is not RobotActivity.CHARGING:
            break
    charging_now = [r for r, a in states.items() if a is RobotActivity.CHARGING]
    assert first_charging not in charging_now
    assert len(charging_now) <= 1
    assert sim.metrics.charger_queue_s > 0


def test_handoff_queueing_accumulates_queue_time():
    # One dock slot and three loaded robots -> someone must wait.
    scenario = short_scenario(
        stations=[
            s.model_copy(update={"dock_slots": 1, "max_queue_length": 4})
            for s in make_scenario("normal_weekday").stations
        ]
    )
    sim = RangeSimulation(scenario, seed=5)
    for rid in ("R1", "R2", "R3"):
        moved = sim.ledger.move("dispenser", f"robot:{rid}", 400)
        assert moved == 400
        sim._robots[rid].payload_balls = moved
    for rid in ("R1", "R2", "R3"):
        assert sim.apply_directive(SendToHandoff(robot_id=rid)).allowed
    assert len(sim.station_snapshots()) == 1  # single-station scenario
    docked_seen = queue_seen = 0
    for _ in range(60):
        sim.advance(30.0)
        snap = sim.station_snapshots()[0]
        assert snap.docked <= 1
        docked_seen = max(docked_seen, snap.docked)
        queue_seen = max(queue_seen, snap.queue_length)
    assert docked_seen == 1
    assert queue_seen >= 1
    assert sim.metrics.handoff_queue_s > 0


def test_station_buffer_blocks_unload_until_washer_frees_space():
    # Tiny buffer + slow washer: unloading must stall, never overflow.
    base = make_scenario("normal_weekday")
    scenario = short_scenario(
        stations=[
            s.model_copy(update={"buffer_capacity_balls": 150}) for s in base.stations
        ],
        washer=base.washer.model_copy(
            update={
                "balls_per_minute": base.washer.balls_per_minute.model_copy(
                    update={"value": 5.0}
                ),
                "batch_size_balls": 50,
            }
        ),
    )
    sim = RangeSimulation(scenario, seed=6)
    moved = sim.ledger.move("dispenser", "robot:R1", 500)
    assert moved == 500
    sim._robots["R1"].payload_balls = moved
    assert sim.apply_directive(SendToHandoff(robot_id="R1")).allowed
    for _ in range(120):
        sim.advance(60.0)
        snap = sim.station_snapshots()[0]
        assert snap.buffer_balls <= snap.buffer_capacity_balls
    # The washer keeps digesting, so some balls made it through.
    assert sim.metrics.balls_processed > 0
