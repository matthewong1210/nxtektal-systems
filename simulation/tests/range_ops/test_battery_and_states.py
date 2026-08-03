"""Battery bounds and robot state exclusivity across full episodes."""

from __future__ import annotations

import pytest

from nxt_range_ops.core.entities import RobotActivity
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.scenarios.generators import make_scenario

from tests.range_ops.conftest import run_policy_episode, short_scenario


@pytest.mark.parametrize("policy_name", ["random_valid", "demand_forecast_dispatch"])
def test_battery_stays_within_bounds_all_day(policy_name):
    scenario = make_scenario("charger_congestion")
    env = RangeOpsEnv(scenario)
    obs, info = env.reset(seed=17)
    from nxt_range_ops.policies.baselines import make_baseline

    policy = make_baseline(policy_name, scenario, env.catalog, seed=17)
    while True:
        action = policy.act(obs, info)
        obs, _, terminated, truncated, info = env.step(action)
        for robot in info["robots"]:
            assert 0.0 <= robot["battery_frac"] <= 1.0 + 1e-9
        for value in obs["robot_battery"]:
            assert 0.0 <= float(value) <= 1.0
        if terminated or truncated:
            break


def test_battery_depletion_fails_robot_and_needs_human():
    # Tiny battery + long distances: deplete quickly mid-work.
    base = make_scenario("normal_weekday")
    scenario = short_scenario(
        robots=[
            r.model_copy(
                update={
                    "initial_battery_frac": 0.18,
                    "battery_capacity_wh": r.battery_capacity_wh.model_copy(
                        update={"value": 40.0}
                    ),
                }
            )
            for r in base.robots
        ]
    )
    sim = RangeSimulation(scenario, seed=3)
    from nxt_range_ops.core.directives import AssignCollection

    assert sim.apply_directive(AssignCollection(robot_id="R1", zone_id="Z6")).allowed
    depleted = False
    for _ in range(240):
        sim.advance(60.0)
        robot = sim.robot_or_none("R1")
        if robot.activity is RobotActivity.FAILED:
            depleted = True
            break
    assert depleted, "robot should hard-fail once battery hits the floor"
    kinds = [e["kind"] for e in sim.events.to_dicts()]
    assert "battery_depleted" in kinds or "robot_failed" in kinds


def test_robot_state_exclusivity_invariants():
    scenario = make_scenario("robot_failure")
    env = RangeOpsEnv(scenario)
    _, _, infos, _ = run_policy_episode(env, "inventory_threshold", seed=21)
    activities = {a.value for a in RobotActivity}
    for info in infos:
        for robot in info["robots"]:
            # Exactly one activity, from the closed vocabulary.
            assert robot["activity"] in activities
            # A latched e-stop always shows as EMERGENCY_STOPPED.
            if robot["estop_latched"]:
                assert robot["activity"] == RobotActivity.EMERGENCY_STOPPED.value
            # Idle/failed/paused robots never keep a zone assignment.
            if robot["activity"] in (
                RobotActivity.IDLE.value,
                RobotActivity.FAILED.value,
                RobotActivity.PAUSED.value,
                RobotActivity.AWAITING_HUMAN.value,
            ):
                assert robot["assigned_zone"] is None
            # Payload is bounded by capacity.
            assert 0 <= robot["payload_balls"] <= robot["payload_capacity_balls"]


def test_paused_robot_stays_paused_until_resume():
    scenario = make_scenario("normal_weekday")
    env = RangeOpsEnv(scenario)
    obs, info = env.reset(seed=2)
    pause = env.catalog.index_of("pause_robot(R2)")
    resume = env.catalog.index_of("resume_robot(R2)")
    obs, _, _, _, info = env.step(pause)
    r2 = next(r for r in info["robots"] if r["robot_id"] == "R2")
    assert r2["activity"] == RobotActivity.PAUSED.value
    for _ in range(5):
        obs, _, _, _, info = env.step(0)  # wait
        r2 = next(r for r in info["robots"] if r["robot_id"] == "R2")
        assert r2["activity"] == RobotActivity.PAUSED.value
    assert info["action_mask"][resume]
    obs, _, _, _, info = env.step(resume)
    r2 = next(r for r in info["robots"] if r["robot_id"] == "R2")
    assert r2["activity"] == RobotActivity.IDLE.value
