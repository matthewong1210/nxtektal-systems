"""Episode termination semantics and reward decomposition."""

from __future__ import annotations

import math

from nxt_range_ops.config.models import EpisodeConfig
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.env.rewards import REWARD_COMPONENT_NAMES
from nxt_range_ops.scenarios.generators import make_scenario

from tests.range_ops.conftest import run_policy_episode, short_scenario


def test_terminates_at_facility_close_with_reason():
    scenario = make_scenario("normal_weekday")
    env = RangeOpsEnv(scenario)
    _, _, infos, last = run_policy_episode(env, "inventory_threshold", seed=8)
    day_minutes = scenario.hours.close_minute - scenario.hours.open_minute
    expected_steps = math.ceil(
        day_minutes * 60 / scenario.episode.control_interval_s
    )
    assert len(infos) == expected_steps
    assert last["termination_reason"] == "day_complete"
    assert env.sim.facility_closed
    # terminated on the final step, not truncated
    non_final = infos[:-1]
    assert all(i["termination_reason"] is None for i in non_final)


def test_truncates_at_max_steps():
    scenario = short_scenario(episode=EpisodeConfig(max_steps=25))
    env = RangeOpsEnv(scenario)
    obs, info = env.reset(seed=8)
    steps = 0
    while True:
        obs, _, terminated, truncated, info = env.step(0)
        steps += 1
        if terminated or truncated:
            break
    assert steps == 25
    assert truncated and not terminated
    assert info["termination_reason"] == "max_steps"


def test_reward_total_equals_component_sum_every_step():
    scenario = make_scenario("demand_spike")
    env = RangeOpsEnv(scenario)
    _, rewards, infos, _ = run_policy_episode(env, "random_valid", seed=31)
    for reward, info in zip(rewards, infos):
        components = info["reward_components"]
        assert set(components) == set(REWARD_COMPONENT_NAMES)
        assert math.isclose(reward, sum(components.values()), rel_tol=0, abs_tol=1e-9)
        assert math.isclose(reward, info["reward_total"], rel_tol=0, abs_tol=1e-9)


def test_components_respond_to_their_drivers():
    scenario = make_scenario("normal_weekday")
    env = RangeOpsEnv(scenario)

    # Doing nothing all day: stockout occurs, no balls processed, no energy
    # from tasks beyond idle drain accounting.
    obs, info = env.reset(seed=12)
    stockout_seen = False
    idle_penalty_seen = False
    while True:
        obs, _, terminated, truncated, info = env.step(0)
        c = info["reward_components"]
        if c["stockout_duration"] < 0:
            stockout_seen = True
        if c["robot_idle"] < 0:
            idle_penalty_seen = True
        assert c["task_switching"] == 0.0
        assert c["unsafe_action_rejection"] == 0.0
        if terminated or truncated:
            break
    assert stockout_seen
    assert idle_penalty_seen
    assert env.sim.metrics.balls_processed == 0

    # A rejected action prices only the rejection component.
    obs, info = env.reset(seed=12)
    bad = env.catalog.index_of("resume_robot(R1)")
    obs, _, _, _, info = env.step(bad)
    assert info["reward_components"]["unsafe_action_rejection"] < 0

    # An active day earns balls_processed credit.
    _, _, infos, _ = run_policy_episode(env, "inventory_threshold", seed=12)
    assert any(i["reward_components"]["balls_processed"] > 0 for i in infos)
    assert any(i["reward_components"]["energy"] < 0 for i in infos)
