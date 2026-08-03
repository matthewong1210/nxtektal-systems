"""Valid-action masking: the mask and the SafetyShield can never disagree."""

from __future__ import annotations

import numpy as np

from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.scenarios.generators import make_scenario

from tests.range_ops.conftest import run_policy_episode


def test_wait_always_valid_and_mask_matches_shield():
    scenario = make_scenario("normal_weekday")
    env = RangeOpsEnv(scenario)
    obs, info = env.reset(seed=3)
    rng = np.random.default_rng(0)
    for _ in range(120):
        mask = info["action_mask"]
        assert mask.dtype == bool and len(mask) == env.action_space.n
        assert mask[0], "wait must always be valid"
        # The mask must agree with a fresh shield evaluation, action by action.
        for spec in env.catalog.specs:
            assert mask[spec.index] == env.sim.shield.check(spec.directive).allowed
        action = int(rng.choice(np.flatnonzero(mask)))
        obs, _, terminated, truncated, info = env.step(action)
        if terminated or truncated:
            break


def test_masked_valid_actions_are_never_rejected():
    scenario = make_scenario("weekend_peak")
    env = RangeOpsEnv(scenario)
    _, _, infos, _ = run_policy_episode(env, "random_valid", seed=9)
    rejected = [i for i in infos if not i["shield"]["allowed"]]
    assert rejected == []
    assert env.sim.metrics.unsafe_rejections == 0


def test_invalid_action_is_rejected_not_executed():
    scenario = make_scenario("normal_weekday")
    env = RangeOpsEnv(scenario)
    obs, info = env.reset(seed=5)
    # resume_robot on a robot that is not paused is impossible.
    idx = env.catalog.index_of("resume_robot(R1)")
    assert not info["action_mask"][idx]
    robots_before = info["robots"]
    obs, reward, _, _, info = env.step(idx)
    assert info["shield"]["allowed"] is False
    assert info["shield"]["reason"]
    assert env.sim.metrics.unsafe_rejections == 1
    assert info["reward_components"]["unsafe_action_rejection"] < 0
    # The rejected directive changed nothing about the fleet.
    assert [r["activity"] for r in info["robots"]] == [
        r["activity"] for r in robots_before
    ]
