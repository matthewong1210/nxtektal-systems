"""Deterministic replay: seed + action sequence fully determine an episode."""

from __future__ import annotations

from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.scenarios.generators import make_scenario

from tests.range_ops.conftest import replay_actions, run_policy_episode


def test_same_seed_same_policy_identical_episode():
    scenario = make_scenario("normal_weekday")
    env_a, env_b = RangeOpsEnv(scenario), RangeOpsEnv(scenario)
    actions_a, rewards_a, _, last_a = run_policy_episode(env_a, "inventory_threshold", 123)
    actions_b, rewards_b, _, last_b = run_policy_episode(env_b, "inventory_threshold", 123)
    assert actions_a == actions_b
    assert rewards_a == rewards_b
    assert env_a.sim.events.to_dicts() == env_b.sim.events.to_dicts()
    assert last_a["metrics"] == last_b["metrics"]


def test_replay_recorded_actions_reproduces_event_log():
    scenario = make_scenario("repeated_docking_failure")
    env_a = RangeOpsEnv(scenario)
    actions, rewards_a, _, _ = run_policy_episode(env_a, "demand_forecast_dispatch", 7)

    env_b = RangeOpsEnv(scenario)
    rewards_b, _ = replay_actions(env_b, seed=7, actions=actions)
    assert rewards_a == rewards_b
    assert env_a.sim.events.to_dicts() == env_b.sim.events.to_dicts()
    assert env_a.sim.metrics.to_dict() == env_b.sim.metrics.to_dict()
    assert env_a.sim.ledger.counts() == env_b.sim.ledger.counts()


def test_different_seed_diverges():
    scenario = make_scenario("normal_weekday")
    env_a, env_b = RangeOpsEnv(scenario), RangeOpsEnv(scenario)
    _, rewards_a, _, _ = run_policy_episode(env_a, "random_valid", 1)
    _, rewards_b, _, _ = run_policy_episode(env_b, "random_valid", 2)
    assert rewards_a != rewards_b


def test_unseeded_reset_reports_replayable_seed():
    scenario = make_scenario("normal_weekday")
    env = RangeOpsEnv(scenario)
    env.reset(seed=555)
    obs, info = env.reset()  # unseeded: seed drawn from gym np_random
    episode_seed = info["episode_seed"]
    assert isinstance(episode_seed, int)

    env_b = RangeOpsEnv(scenario)
    obs_b, info_b = env_b.reset(seed=episode_seed)
    assert env.sim.events.to_dicts() == env_b.sim.events.to_dicts()
