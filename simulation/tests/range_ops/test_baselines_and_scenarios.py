"""Baseline-policy execution across all scenario generators, and skill-model
interface conformance (including the documented future stubs)."""

from __future__ import annotations

import numpy as np
import pytest

from nxt_range_ops.core.skills import (
    EmpiricalSkillOutcomeModel,
    IsaacSkillOutcomeModel,
    MockSkillOutcomeModel,
    SkillRequest,
    SkillType,
)
from nxt_range_ops.core.entities import RobotHealth
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.scenarios.generators import SCENARIO_GENERATORS, make_scenario

from tests.range_ops.conftest import run_policy_episode

ALL_POLICIES = (
    "random_valid",
    "inventory_threshold",
    "nearest_available_robot",
    "demand_forecast_dispatch",
)


@pytest.mark.parametrize("scenario_name", sorted(SCENARIO_GENERATORS))
def test_every_scenario_builds_and_runs(scenario_name):
    scenario = make_scenario(scenario_name)
    env = RangeOpsEnv(scenario)
    _, rewards, infos, last = run_policy_episode(env, "inventory_threshold", seed=19)
    assert all(np.isfinite(r) for r in rewards)
    assert last["termination_reason"] in ("day_complete", "max_steps")


@pytest.mark.parametrize("policy_name", ALL_POLICIES)
def test_every_baseline_completes_a_day(policy_name):
    scenario = make_scenario("normal_weekday")
    env = RangeOpsEnv(scenario)
    _, rewards, infos, last = run_policy_episode(env, policy_name, seed=23)
    assert last["termination_reason"] == "day_complete"
    # Baselines must never emit invalid actions.
    assert all(i["shield"]["allowed"] for i in infos)


def test_heuristics_beat_random_on_throughput():
    scenario = make_scenario("normal_weekday")
    results = {}
    for policy_name in ("random_valid", "inventory_threshold"):
        env = RangeOpsEnv(scenario)
        run_policy_episode(env, policy_name, seed=29)
        results[policy_name] = env.sim.metrics.balls_processed
    assert results["inventory_threshold"] > results["random_valid"]


def test_mock_skill_model_is_deterministic_per_rng():
    scenario = make_scenario("normal_weekday")
    model = MockSkillOutcomeModel(
        scenario.skills, {r.robot_id: r.speed_mps.value for r in scenario.robots}
    )
    request = SkillRequest(
        skill=SkillType.TRAVEL,
        robot_id="R1",
        robot_health=RobotHealth.OK,
        battery_frac=0.8,
        payload_balls=0,
        distance_m=120.0,
    )
    a = model.sample(request, np.random.default_rng(42))
    b = model.sample(request, np.random.default_rng(42))
    assert a == b
    assert a.duration_s > 0 and a.energy_wh > 0


def test_skill_outcome_contains_required_fields():
    scenario = make_scenario("normal_weekday")
    model = MockSkillOutcomeModel(
        scenario.skills, {r.robot_id: r.speed_mps.value for r in scenario.robots}
    )
    rng = np.random.default_rng(0)
    for skill in SkillType:
        outcome = model.sample(
            SkillRequest(
                skill=skill,
                robot_id="R1",
                robot_health=RobotHealth.OK,
                battery_frac=0.9,
                payload_balls=100,
                distance_m=50.0,
                n_balls=40,
            ),
            rng,
        )
        # The full required interface: success, duration, energy, human
        # intervention, failure reason, resulting robot state.
        assert isinstance(outcome.success, bool)
        assert outcome.duration_s >= 0.0
        assert outcome.energy_wh >= 0.0
        assert isinstance(outcome.human_intervention_required, bool)
        assert outcome.failure_reason is not None
        assert isinstance(outcome.resulting_health, RobotHealth)


def test_future_stub_models_are_documented_not_implemented():
    with pytest.raises(NotImplementedError):
        IsaacSkillOutcomeModel()
    with pytest.raises(NotImplementedError):
        EmpiricalSkillOutcomeModel()
    assert "Isaac" in (IsaacSkillOutcomeModel.__doc__ or "")
    assert "real-facility" in (EmpiricalSkillOutcomeModel.__doc__ or "")
