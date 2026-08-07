"""SafetyShield enforcement: hard constraints, not reward penalties."""

from __future__ import annotations

from nxt_range_ops.config.models import SafetyConfig, TimeWindow
from nxt_range_ops.core.directives import (
    AssignCollection,
    PauseRobot,
    RequestHumanAssistance,
    ResumeRobot,
    SendToCharge,
    SendToHandoff,
)
from nxt_range_ops.core.entities import HumanAssistReason, RobotActivity
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.scenarios.generators import make_scenario

from tests.range_ops.conftest import short_scenario


def test_closed_zone_assignment_rejected():
    scenario = short_scenario(
        zones=[
            z.model_copy(
                update={
                    "closure_windows": [TimeWindow(start_minute=0, end_minute=1440)]
                }
            )
            if z.zone_id == "Z1"
            else z
            for z in make_scenario("normal_weekday").zones
        ]
    )
    sim = RangeSimulation(scenario, seed=1)
    sim.advance(120.0)  # let the closure process run
    decision = sim.apply_directive(AssignCollection(robot_id="R1", zone_id="Z1"))
    assert not decision.allowed
    assert "closed" in decision.reason
    # Open zone still fine.
    assert sim.apply_directive(AssignCollection(robot_id="R1", zone_id="Z2")).allowed


def test_battery_reserve_is_hard_constraint():
    scenario = short_scenario(
        robots=[
            r.model_copy(update={"initial_battery_frac": 0.10})
            for r in make_scenario("normal_weekday").robots
        ]
    )
    sim = RangeSimulation(scenario, seed=1)
    decision = sim.apply_directive(AssignCollection(robot_id="R1", zone_id="Z2"))
    assert not decision.allowed
    assert "reserve" in decision.reason
    # Charging is exactly what the shield should still allow.
    assert sim.apply_directive(SendToCharge(robot_id="R1")).allowed


def test_zone_occupancy_cap():
    scenario = short_scenario(safety=SafetyConfig(max_robots_per_zone=1))
    sim = RangeSimulation(scenario, seed=1)
    assert sim.apply_directive(AssignCollection(robot_id="R1", zone_id="Z3")).allowed
    decision = sim.apply_directive(AssignCollection(robot_id="R2", zone_id="Z3"))
    assert not decision.allowed
    assert "occupancy" in decision.reason


def test_station_queue_capacity_is_hard():
    scenario = short_scenario(
        stations=[
            s.model_copy(update={"max_queue_length": 0, "dock_slots": 1})
            for s in make_scenario("normal_weekday").stations
        ]
    )
    sim = RangeSimulation(scenario, seed=1)
    # Give robots payload (white-box) so handoff is otherwise legal. Seed
    # from the dispenser — zones only hold ~200 balls at reset — and keep
    # payload consistent with what the ledger actually moved.
    for rid in ("R1", "R2", "R3"):
        moved = sim.ledger.move("dispenser", f"robot:{rid}", 600)
        assert moved == 600
        sim._robots[rid].payload_balls = moved
    assert sim.apply_directive(SendToHandoff(robot_id="R1")).allowed
    sim.advance(70.0)  # R1 has reached the station and holds the only dock
    r1 = sim.robot_or_none("R1")
    assert r1.activity in (RobotActivity.UNLOADING, RobotActivity.QUEUED_HANDOFF)
    # Dock occupied and max_queue_length == 0: further sends must be
    # rejected outright — a hard constraint, not a penalty.
    decision = sim.apply_directive(SendToHandoff(robot_id="R2"))
    assert not decision.allowed
    assert "queue" in decision.reason or "capacity" in decision.reason


def test_estop_is_latched_until_human_reset():
    scenario = short_scenario(
        "repeated_docking_failure",
        skills=make_scenario("repeated_docking_failure").skills.model_copy(
            update={"dock_failure_prob": 1.0, "max_dock_retries": 50}
        ),
        safety=SafetyConfig(dock_incident_estop_prob=1.0),
    )
    sim = RangeSimulation(scenario, seed=2)
    moved = sim.ledger.move("dispenser", "robot:R1", 50)
    assert moved == 50
    sim._robots["R1"].payload_balls = moved
    assert sim.apply_directive(SendToHandoff(robot_id="R1")).allowed
    for _ in range(30):
        sim.advance(60.0)
        if sim.robot_or_none("R1").estop_latched:
            break
    robot = sim.robot_or_none("R1")
    assert robot.estop_latched
    assert robot.activity is RobotActivity.EMERGENCY_STOPPED
    # Everything except human assistance is rejected while latched.
    for directive in (
        AssignCollection(robot_id="R1", zone_id="Z2"),
        SendToHandoff(robot_id="R1"),
        SendToCharge(robot_id="R1"),
        PauseRobot(robot_id="R1"),
        ResumeRobot(robot_id="R1"),
    ):
        decision = sim.apply_directive(directive)
        assert not decision.allowed
        assert "latched" in decision.reason or "human" in decision.reason
    # The one legal move: request human help; after service, robot recovers.
    assert sim.apply_directive(
        RequestHumanAssistance(
            robot_id="R1", reason=HumanAssistReason.EMERGENCY_STOP_RESET
        )
    ).allowed
    sim.advance(1200.0)
    recovered = sim.robot_or_none("R1")
    assert not recovered.estop_latched
    assert recovered.activity is RobotActivity.IDLE
    assert sim.metrics.safe_recoveries >= 1


def test_shield_cannot_be_bypassed_via_direct_sim_call():
    scenario = make_scenario("normal_weekday")
    env = RangeOpsEnv(scenario)
    env.reset(seed=1)
    # Bypassing the env and calling the simulation directly still validates.
    decision = env.sim.apply_directive(
        AssignCollection(robot_id="R1", zone_id="does_not_exist")
    )
    assert not decision.allowed
    assert env.sim.metrics.unsafe_rejections == 1


def test_no_low_level_or_estop_actions_exist():
    """The action vocabulary is closed: only the eight fleet-level directive
    types exist, so wheel-speed/steering/actuator/e-stop commands cannot even
    be expressed by a policy."""
    from nxt_range_ops.core.directives import (
        AssignCollection as DAssign,
        PauseRobot as DPause,
        ReassignRobot as DReassign,
        RequestHumanAssistance as DHuman,
        ResumeRobot as DResume,
        SendToCharge as DCharge,
        SendToHandoff as DHandoff,
        Wait as DWait,
    )

    scenario = make_scenario("normal_weekday")
    env = RangeOpsEnv(scenario)
    allowed = {DWait, DAssign, DHandoff, DCharge, DReassign, DPause, DResume, DHuman}
    used = {type(spec.directive) for spec in env.catalog.specs}
    assert used == allowed
    # And none of the operation names read as motion-level commands.
    operations = {spec.name.split("(")[0] for spec in env.catalog.specs}
    assert operations == {
        "wait",
        "assign_collection",
        "send_to_handoff",
        "send_to_charge",
        "reassign_robot",
        "pause_robot",
        "resume_robot",
        "request_human_assistance",
    }
