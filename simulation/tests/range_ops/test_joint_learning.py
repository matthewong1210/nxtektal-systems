"""Hidden-input isolation, paired evidence and held-out selection boundaries."""

from copy import deepcopy
import json

import numpy as np
import pytest

from nxt_range_ops.config.models import EpisodeConfig, OperatingHoursConfig
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.evaluation.joint_learning import (
    RESULT_SCHEMA, choose_training_finalist, evaluate_episode, score_result,
    validation_gate,
)
from nxt_range_ops.evaluation import joint_learning
from nxt_range_ops.policies.joint_dispatch import (
    JointDispatchPolicy, candidate_catalog, policy_inputs, policy_scenario,
    validate_candidate,
)
from nxt_range_ops.scenarios.generators import make_scenario


def candidate(name="baseline"):
    return next(row for row in candidate_catalog() if row["candidate_id"] == name)


def short_episode(seed=1, split="train"):
    scenario = make_scenario("normal_weekday").model_copy(update={
        "hours": OperatingHoursConfig(open_minute=480, close_minute=540),
        "episode": EpisodeConfig(control_interval_s=60, max_steps=100),
    })
    return {"schema": "nxt-joint-episode/v0", "environment": "SIMULATION",
            "episode_id": f"{split}-{seed}", "split": split, "seed": seed,
            "regime": "dry", "scenario": scenario,
            "joint_inputs": {"schema": "nxt-joint-scenario-inputs/v0",
                             "environment": "SIMULATION",
                             "demand_by_minute": [20] * 60,
                             "collection_blocks": {zone: [] for zone in scenario.zone_ids},
                             "staff_jobs": [{"job_id": "inspect-1",
                                 "available_minute": 485, "duration_minutes": 10,
                                 "deadline_minute": 525, "hole_number": 1,
                                 "checkpoint_id": "cp-h01-green"}]}}


def evidence(name, seed=1, split="train", fill=0.80, **overrides):
    summary = {"success": True, "error": None, "truncated": False,
               "conservation_ok": True, "demand_matches_input": True,
               "termination_reason": "day_complete", "demand_fill_rate": fill,
               "stockout_minutes": 10.0, "inspection_completion_rate": 0.8,
               "energy_wh": 1000.0, "course_staff_wait_minutes": 20.0,
               "unsafe_rejections": 0, "hard_failures": 1, "estops": 0,
               "staff_jobs_total": 10, "staff_jobs_completed": 8}
    summary.update(overrides)
    return {"schema": RESULT_SCHEMA, "environment": "SIMULATION",
            "episode_id": f"day-{seed}", "seed": seed, "split": split,
            "regime": "dry", "candidate_id": name, "candidate": candidate(name),
            "input_digest": "a" * 64, "demand_digest": "b" * 64,
            "summary": summary}


def comparison(split="validation"):
    return [evidence(name, seed, split, fill=0.9 if name == "responsive" else 0.8)
            for seed in range(3) for name in ("baseline", "responsive")]


def test_candidate_catalog_is_finite_detached_and_strict():
    assert len(candidate_catalog()) == 6
    first = candidate()
    first["low_inventory_frac"] = 1.0
    with pytest.raises(ValueError, match="frozen"):
        validate_candidate(first)
    first = candidate()
    first["use_inventory_trend"] = 0
    with pytest.raises(ValueError):
        validate_candidate(first)
    first = candidate()
    first["safety"] = {"min_battery_reserve_frac": 0}
    with pytest.raises(ValueError):
        validate_candidate(first)
    assert candidate()["low_inventory_frac"] == 0.6


def test_scenario_hides_shock_identity_and_future_windows():
    original = make_scenario("demand_spike")
    visible = policy_scenario(original)
    assert original.demand.spikes
    assert not visible.demand.spikes
    assert all(window.balls_per_minute == 0 for window in visible.demand.windows)
    assert visible.name == "policy-visible-layout" and visible.description == ""
    assert visible.total_balls == original.total_balls
    assert visible.safety == original.safety
    assert visible is not original
    outage = policy_scenario(make_scenario("handoff_station_outage"))
    assert not any(station.outage_windows for station in outage.stations)


def test_policy_view_drops_truth_future_jobs_and_aliases():
    env = RangeOpsEnv(make_scenario("normal_weekday"))
    obs, info = env.reset(seed=22)
    info["ledger"] = {"future": 999}
    info["future_timeline"] = ["unforecast-shock"]
    now = info["t_s"] / 60
    info["joint_ops"] = {"staff": {"capacity": 2, "busy": 0, "queued": 0,
                                             "secret": "hidden"},
                         "staff_jobs": [{"job_id": "visible", "available_minute": now,
                             "status": "PENDING", "hidden": "groundtruth"},
                             {"job_id": "future", "available_minute": now + 1}],
                         "collection_access": {"Z1": True}, "metrics": {"secret": 4}}
    info["robots"][0]["battery_frac"] = 0.999
    obs["robot_battery"][0] = 0.42
    visible_obs, visible_info = policy_inputs(obs, info)
    assert set(visible_info) == {"t_s", "step", "action_mask", "robots", "joint_ops"}
    assert visible_info["robots"][0]["battery_frac"] == pytest.approx(0.42)
    assert [row["job_id"] for row in visible_info["joint_ops"]["staff_jobs"]] == ["visible"]
    assert "hidden" not in visible_info["joint_ops"]["staff_jobs"][0]
    visible_obs["dispenser_inventory_frac"][0] = 999
    with pytest.raises(TypeError):
        visible_info["action_mask"][0] = False
    visible_info["joint_ops"]["collection_access"]["Z1"] = False
    assert obs["dispenser_inventory_frac"][0] != 999
    assert info["action_mask"][0]
    assert info["joint_ops"]["collection_access"]["Z1"]


def test_hidden_evaluation_changes_cannot_change_policy_action():
    env = RangeOpsEnv(make_scenario("demand_spike"))
    obs, info = env.reset(seed=10)
    polluted = deepcopy(info)
    polluted.update(true_dispenser_count=0, ledger={"dispenser": 0},
                    metrics={"demand_balls_total": 10**12}, future_jobs=["secret"])
    first = JointDispatchPolicy(env.scenario, env.catalog, candidate("responsive"))
    second = JointDispatchPolicy(env.scenario, env.catalog, candidate("responsive"))
    assert first.act(obs, info) == second.act(obs, polluted)
    assert not first.scenario.demand.spikes


def test_future_job_catalog_size_and_labels_do_not_reach_policy():
    episode = short_episode()
    altered = deepcopy(episode["joint_inputs"])
    altered["staff_jobs"].extend({"job_id": f"hidden-future-{index}",
        "available_minute": 535, "duration_minutes": 2, "deadline_minute": 539,
        "hole_number": 2, "checkpoint_id": "cp-h02-green"} for index in range(5))
    original_env = RangeOpsEnv(episode["scenario"], joint_inputs=episode["joint_inputs"])
    altered_env = RangeOpsEnv(episode["scenario"], joint_inputs=altered)
    assert len(original_env.catalog) != len(altered_env.catalog)
    first = JointDispatchPolicy(episode["scenario"], original_env.catalog, candidate())
    second = JointDispatchPolicy(episode["scenario"], altered_env.catalog, candidate())
    obs_a, info_a = original_env.reset(seed=1)
    obs_b, info_b = altered_env.reset(seed=1)
    assert first.act(obs_a, info_a) == second.act(obs_b, info_b)
    assert dict(policy_inputs(obs_a, info_a)[1]["action_mask"]) == dict(policy_inputs(obs_b, info_b)[1]["action_mask"])
    assert not hasattr(first.catalog, "specs")
    with pytest.raises(TypeError):
        len(second.catalog)
    with pytest.raises(KeyError):
        second.catalog.index_of("assign_staff_work(hidden-future-0)")


class StaffCatalog:
    def index_of(self, name):
        if name == "assign_staff_work(inspect)":
            return 1
        raise KeyError(name)


def staff_inputs(inventory=0.9, forecast=100, busy=0, queued=0):
    obs = {"dispenser_inventory_frac": np.asarray([inventory]),
           "washer_wip_frac": np.asarray([0.0]), "demand_forecast": np.asarray([forecast]),
           "zone_balls": np.asarray([0.0] * 6), "zone_open": np.zeros(6),
           "robot_battery": np.asarray([]), "robot_payload_frac": np.asarray([])}
    info = {"t_s": 480 * 60, "step": 0, "action_mask": np.asarray([True, True]),
            "robots": [], "joint_ops": {"staff": {"capacity": 1, "busy": busy, "queued": queued},
                "staff_jobs": [{"job_id": "inspect", "available_minute": 480,
                    "deadline_minute": 500, "status": "PENDING"}], "collection_access": {}}}
    return obs, info


def test_staff_reserve_uses_visible_pressure_and_capacity_only():
    scenario = make_scenario("normal_weekday")
    routine = JointDispatchPolicy(scenario, StaffCatalog(), candidate())
    reserve = JointDispatchPolicy(scenario, StaffCatalog(), candidate("reserve"))
    obs, info = staff_inputs(inventory=0.1)
    assert routine.act(obs, info) == 1
    assert reserve.act(obs, info) == 0
    obs, info = staff_inputs(inventory=0.9)
    assert reserve.act(obs, info) == 1
    for kwargs in ({"busy": 1}, {"queued": 1}):
        obs, info = staff_inputs(**kwargs)
        assert reserve.act(obs, info) == 0


def test_episode_replay_has_paired_demand_and_bounded_serializable_timeline():
    episode = short_episode()
    first = evaluate_episode(episode, candidate(), max_timeline_points=8)
    replay = evaluate_episode(episode, candidate(), max_timeline_points=8)
    other = evaluate_episode(episode, candidate("responsive"), max_timeline_points=8)
    assert first == replay
    assert first["summary"]["success"], first["summary"]
    assert first["summary"]["conservation_ok"]
    assert first["summary"]["demand_balls_total"] == 1200
    assert first["demand_digest"] == other["demand_digest"]
    assert first["input_digest"] == other["input_digest"]
    assert 2 <= len(first["timeline"]) <= 8
    assert first["timeline"][0]["t_s"] == 480 * 60
    assert first["timeline"][-1]["t_s"] == 540 * 60
    json.dumps(first, allow_nan=False)


def test_episode_truncation_never_scores_as_a_complete_day():
    episode = short_episode()
    episode["scenario"] = episode["scenario"].model_copy(update={
        "episode": EpisodeConfig(max_steps=2)})
    result = evaluate_episode(episode, candidate())
    assert result["summary"]["truncated"]
    assert not result["summary"]["success"]
    assert not result["summary"]["demand_matches_input"]
    with pytest.raises(ValueError, match="incomplete"):
        score_result(result)


def test_runtime_exception_is_explicit_failed_evidence(monkeypatch):
    def fail(self, action):
        raise RuntimeError("deliberate simulated failure")
    monkeypatch.setattr(RangeOpsEnv, "step", fail)
    result = evaluate_episode(short_episode(), candidate())
    assert result["summary"]["success"] is False
    assert result["summary"]["error"]["type"] == "RuntimeError"
    assert result["score"] is None
    json.dumps(result, allow_nan=False)


def test_evaluator_never_passes_true_metrics_or_timeline_to_act(monkeypatch):
    original = JointDispatchPolicy.act
    seen = []
    def inspect(self, obs, info):
        seen.append(info["t_s"])
        assert not ({"metrics", "true_dispenser_count", "ledger", "episode_seed",
                     "future_jobs", "demand_by_minute", "course_frames"} & set(info))
        assert not self.scenario.demand.spikes
        assert all(job["available_minute"] * 60 <= info["t_s"]
                   for job in info.get("joint_ops", {}).get("staff_jobs", []))
        return original(self, obs, info)
    monkeypatch.setattr(joint_learning.JointDispatchPolicy, "act", inspect)
    result = evaluate_episode(short_episode(), candidate("responsive"))
    assert result["summary"]["success"]
    assert len(seen) == 60


def test_training_selects_finalist_but_rejects_validation_and_test_inputs():
    choice = choose_training_finalist(comparison("train"))
    assert choice["candidate_id"] == "responsive"
    assert "requires_fresh_validation" in choice["reason"]
    assert choice["production_promotion"] is False
    for split in ("validation", "test"):
        with pytest.raises(ValueError, match="another data split"):
            choose_training_finalist(comparison(split))


def test_validation_only_accepts_frozen_finalist_with_paired_gain():
    result = validation_gate(comparison(), "responsive")
    assert result["accepted"] and result["candidate_id"] == "responsive"
    assert result["paired"]["baseline"]["demand_paired"]
    assert result["production_promotion"] is False
    with pytest.raises(ValueError, match="another data split"):
        validation_gate(comparison("test"), "responsive")
    with pytest.raises(ValueError, match="frozen finalist"):
        validation_gate(comparison() + [evidence("reserve", split="validation")], "responsive")


@pytest.mark.parametrize("change,reason", [
    ({"truncated": True}, "failed_or_incomplete"),
    ({"conservation_ok": False}, "failed_or_incomplete"),
    ({"error": {"type": "RuntimeError"}}, "failed_or_incomplete"),
    ({"unsafe_rejections": 1}, "unsafe_rejections_regression"),
    ({"hard_failures": 2}, "hard_failures_regression"),
    ({"estops": 1}, "estops_regression"),
    ({"demand_fill_rate": 0.1}, "fill_rate_regression"),
    ({"inspection_completion_rate": 0.0}, "inspection_regression"),
    ({"energy_wh": 10000}, "energy_regression"),
    ({"demand_fill_rate": float("nan")}, "failed_or_incomplete"),
])
def test_validation_fails_closed(change, reason):
    rows = comparison()
    rows[1]["summary"].update(change)
    result = validation_gate(rows, "responsive")
    assert not result["accepted"] and result["candidate_id"] == "baseline"
    assert reason in result["reason"]


def test_missing_pairs_digest_mismatch_and_small_holdout_fail_closed():
    rows = comparison()
    assert validation_gate(rows[:-1], "responsive")["reason"] == "missing_paired_episodes"
    rows[1]["demand_digest"] = "c" * 64
    assert validation_gate(rows, "responsive")["reason"] == "paired_demand_digest_mismatch"
    assert validation_gate(comparison()[:2], "responsive")["reason"] == "insufficient_paired_validation_episodes"


def test_validation_compares_nonbaseline_incumbent_and_ties_keep_it():
    rows = comparison()
    rows.extend(evidence("balanced", seed, "validation", fill=0.9) for seed in range(3))
    result = validation_gate(rows, "responsive", incumbent_id="balanced")
    assert not result["accepted"] and result["candidate_id"] == "balanced"
    assert result["reason"] == "retain_incumbent_no_validation_improvement"


def test_duplicate_episode_evidence_cannot_inflate_sample_count():
    rows = comparison()
    with pytest.raises(ValueError, match="duplicate"):
        validation_gate(rows + [deepcopy(rows[0])], "responsive")
    for row in rows:
        row["seed"] = 3
    assert validation_gate(rows, "responsive")["reason"] == "duplicate_seed_regime_exposure"


def test_worst_regime_decline_cannot_be_hidden_by_aggregate_improvement():
    rows = comparison()
    rows.extend(evidence(name, seed, "validation", fill=0.1 if name == "responsive" else 0.2)
                for seed in range(3, 6) for name in ("baseline", "responsive"))
    for row in rows[6:]:
        row["regime"] = "rain"
    result = validation_gate(rows, "responsive")
    assert not result["accepted"]
    assert result["reason"] == "fill_rate_regression:rain"
