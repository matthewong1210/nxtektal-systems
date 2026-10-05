"""End-to-end synthetic day: evidence, capacity, missingness and reproducibility."""

from collections import Counter
from copy import deepcopy
import json

import pytest

from nxt_pilot_ops.grounds import GroundsWorkflow
from scripts.course_monitoring_demo import run_demo, write_bundle


@pytest.fixture(scope="module")
def day():
    return run_demo()


def test_whole_course_and_unknown_are_honest(day):
    report = day["report"]
    assert len(day["course_model"]["holes"]) == 18
    assert len(report["checkpoints"]) == 54
    assert {cp["hole_number"] for cp in report["checkpoints"]} == set(range(1, 19))
    assert len(report["frames"]) == 97
    final = {row["checkpoint_id"]: row["status"] for row in report["frames"][-1]["coverage"]}
    assert final["cp-h02-green"] == "UNOBSERVED"
    assert any(row["status"] == "STALE" for frame in report["frames"] for row in frame["coverage"])
    assert any(row["status"] == "UNUSABLE" for frame in report["frames"] for row in frame["coverage"])
    assert sum(report["summary"]["coverage"].values()) == 54


def test_completion_requires_later_evidence_and_crew_capacity(day):
    report = day["report"]
    assert any(case["status"] == "VERIFIED" for case in report["cases"])
    assert any(case["status"] == "AWAITING_VERIFICATION" for frame in report["frames"] for case in frame["cases"])
    assert any(task["status"] == "VERIFICATION_FAILED" for task in report["tasks"])
    observations = {row["observation_id"]: row for row in day["observations"]}
    tasks = {row["task_id"]: row for row in report["tasks"]}
    for case in report["cases"]:
        if case["status"] == "VERIFIED":
            obs = observations[case["verification_observation_id"]]
            task = tasks[case["latest_task_id"]]
            assert obs["captured_at_utc"] > task["completed_at_utc"]
            assert obs["condition"] == "CLEAR" and obs["quality"] == "USABLE"
            assert obs["inspected_condition"] == case["condition_kind"]
    for frame in report["frames"]:
        active = Counter(task["resource"]["id"] for task in frame["tasks"] if task["status"] in {"ASSIGNED", "IN_PROGRESS"})
        assert max(active.values(), default=0) <= 1


def test_case_input_is_only_validated_observations_and_replay_is_exact(day):
    replay = GroundsWorkflow.from_dict(day["grounds_state"])
    assert replay.to_dict() == day["grounds_state"]
    assert all(obs["source_kind"] == "SYNTHETIC_FIXTURE" for obs in day["observations"])
    assert all(obs["evidence_ref"].startswith("synthetic:") for obs in day["observations"])
    assert "scene_intervals" not in json.dumps(day["grounds_state"])


def test_run_and_artifact_bytes_are_deterministic(tmp_path):
    left = run_demo(duration_minutes=45, seed=2)
    right = run_demo(duration_minutes=45, seed=2)
    assert left == right
    write_bundle(left, tmp_path / "a")
    write_bundle(right, tmp_path / "b")
    for path in (tmp_path / "a").iterdir():
        assert path.read_bytes() == (tmp_path / "b" / path.name).read_bytes()
    html = (tmp_path / "a" / "report.html").read_text()
    assert "SIMULATION" in html
    assert "54" in html


def test_manager_review_and_priority_are_retained():
    actions = [
        {"minute": 5, "checkpoint_id": "cp-h01-fairway", "operator": "manager-test", "action": "confirm", "reason": "Staff photo reviewed"},
        {"minute": 5, "checkpoint_id": "cp-h01-fairway", "operator": "manager-test", "action": "adjust", "reason": "Opening event", "priority": "URGENT", "deadline_at_utc": "2026-09-16T01:00:00Z"},
        {"minute": 10, "checkpoint_id": "cp-h01-fairway", "operator": "manager-test", "action": "assign", "reason": "Crew available", "resource_id": "grounds-crew"},
    ]
    original = deepcopy(actions)
    bundle = run_demo(duration_minutes=45, scripted_review=False, manager_actions=actions)
    assert actions == original
    case = next(row for row in bundle["report"]["cases"] if row["checkpoint_id"] == "cp-h01-fairway")
    assert case["priority"] == "URGENT" and case["reviewed_by"] == "manager-test"
    assert case["status"] == "AWAITING_VERIFICATION"
    assert len(bundle["manager_action_receipts"]) == 3
    assert any(row["status"] == "CANDIDATE" for row in bundle["report"]["cases"])


def test_invalid_action_does_not_silently_disappear():
    with pytest.raises(ValueError, match="one active case"):
        run_demo(duration_minutes=5, manager_actions=[{"minute": 0, "checkpoint_id": "cp-h18-green", "operator": "manager", "action": "confirm", "reason": "No observation yet"}])


@pytest.mark.parametrize("actions", [{}, "", False, [{"minute": 0, "checkpoint_id": "cp-h01-fairway", "operator": "manager", "action": "confirm", "reason": "Reviewed", "priority": "URGENT"}]])
def test_no_silent_manager_action_fields(actions):
    with pytest.raises(ValueError):
        run_demo(duration_minutes=5, manager_actions=actions)


def test_manager_adjustment_precedes_same_tick_scripted_verification():
    action = {"minute": 180, "checkpoint_id": "cp-h01-fairway", "operator": "manager", "action": "adjust", "reason": "Review priority", "priority": "HIGH", "deadline_at_utc": None}
    result = run_demo(duration_minutes=180, manager_actions=[action])
    case = next(row for row in result["report"]["cases"] if row["checkpoint_id"] == "cp-h01-fairway")
    assert case["status"] == "VERIFIED" and case["priority"] == "HIGH"


def test_scene_cannot_precede_map_revision():
    with pytest.raises(ValueError, match="effective time"):
        run_demo(duration_minutes=5, start_utc="2026-09-15T00:00:00Z")


def test_resource_can_be_assigned_when_prior_task_completes():
    base = {"operator": "manager", "reason": "Planned maintenance"}
    actions = [
        {**base, "minute": 0, "checkpoint_id": "cp-h01-fairway", "action": "confirm"},
        {**base, "minute": 0, "checkpoint_id": "cp-h01-fairway", "action": "assign", "resource_id": "grounds-crew"},
        {**base, "minute": 15, "checkpoint_id": "cp-h02-bunker", "action": "confirm"},
        {**base, "minute": 30, "checkpoint_id": "cp-h02-bunker", "action": "assign", "resource_id": "grounds-crew"},
    ]
    result = run_demo(duration_minutes=30, scripted_review=False, manager_actions=actions)
    states = {task["checkpoint_id"]: task["status"] for task in result["report"]["tasks"]}
    assert states == {"cp-h01-fairway": "AWAITING_VERIFICATION", "cp-h02-bunker": "IN_PROGRESS"}


@pytest.mark.parametrize("kwargs", [{"duration_minutes": 0}, {"duration_minutes": 7}, {"duration_minutes": 1445}, {"seed": True}, {"freshness_minutes": 0}, {"realtime_speed": float("nan")}])
def test_invalid_run_parameters_fail_closed(kwargs):
    with pytest.raises(ValueError):
        run_demo(**kwargs)
