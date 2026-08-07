"""Shared fixtures/helpers for the Range Ops Phase 0.5 test suite."""

from __future__ import annotations

import pytest

from nxt_range_ops.config.models import EpisodeConfig, RangeOpsScenario
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.scenarios.generators import make_scenario


@pytest.fixture
def weekday() -> RangeOpsScenario:
    return make_scenario("normal_weekday")


@pytest.fixture
def weekday_env(weekday) -> RangeOpsEnv:
    return RangeOpsEnv(weekday)


def short_scenario(base: str = "normal_weekday", **updates) -> RangeOpsScenario:
    """A shortened copy of a named scenario for fast targeted tests."""
    scenario = make_scenario(base)
    merged = {"episode": EpisodeConfig(max_steps=2000), **updates}
    return scenario.model_copy(update=merged)


def run_policy_episode(env: RangeOpsEnv, policy_name: str, seed: int):
    """Run one full episode; returns (actions, rewards, infos, final_info)."""
    policy = make_baseline(policy_name, env.scenario, env.catalog, seed=seed)
    obs, info = env.reset(seed=seed)
    policy.reset()
    actions, rewards, infos = [], [], []
    while True:
        action = policy.act(obs, info)
        obs, reward, terminated, truncated, info = env.step(action)
        actions.append(action)
        rewards.append(reward)
        infos.append(info)
        if terminated or truncated:
            return actions, rewards, infos, info


def replay_actions(env: RangeOpsEnv, seed: int, actions: list[int]):
    """Replay a recorded action sequence from the same seed."""
    obs, info = env.reset(seed=seed)
    rewards, infos = [], []
    for action in actions:
        obs, reward, terminated, truncated, info = env.step(action)
        rewards.append(reward)
        infos.append(info)
        if terminated or truncated:
            break
    return rewards, infos
