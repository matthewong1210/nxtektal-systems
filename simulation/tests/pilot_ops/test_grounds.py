"""Grounds workflow semantics and serialized observation-seam parity."""
from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from nxt_pilot_ops.grounds import GroundsError, GroundsWorkflow
from nxt_telemetry.course_condition import build_observation


IDENTITY = {"site_id": "synthetic-course", "deployment_id": "grounds-rehearsal", "map_revision": "map-revision-01"}
HUMAN = {"id": "scripted-crew", "kind": "HUMAN", "capabilities": ("INSPECT", "RAKE_BUNKER", "REPAIR_DIVOT")}
ROBOT = {"id": "synthetic-raker", "kind": "SIMULATED_ROBOT", "capabilities": ("RAKE_BUNKER", "INSPECT")}


def timestamp(second=0):
    return f"2026-09-16T08:00:{second:02d}Z"


def observation(second=0, *, condition="DIVOT", inspected_condition=None, **changes):
    data = {**IDENTITY, "frame_id": f"synthetic-frame-{second}", "checkpoint_id": "checkpoint-1",
            "hole_number": 1, "feature_id": "fairway-1", "cart_id": "synthetic-cart-1",
            "camera_id": "synthetic-camera-1", "captured_at_utc": timestamp(second),
            "condition": condition, "inspected_condition": inspected_condition or ("DIVOT" if condition == "CLEAR" else condition),
            "quality": "USABLE", "cart_position": {"x_m": 1, "y_m": 2, "accuracy_m": 3},
            "target_position": {"x_m": 20, "y_m": 10, "accuracy_m": 4},
            "detection_score": 0.7, "evidence_ref": f"synthetic:frame-{second}"}
    data.update(changes)
    return build_observation(**data)


def complete_case(workflow=None, *, condition="DIVOT", resource=HUMAN, task_kind="REPAIR_DIVOT"):
    workflow = workflow or GroundsWorkflow(**IDENTITY)
    case_id = workflow.observe(observation(condition=condition))["case_id"]
    workflow.review(case_id, "confirm", "scripted-demo-operator", "Review fixture evidence", timestamp(1))
    task_id = workflow.assign(case_id, resource, task_kind, "scripted-demo-operator", "Run synthetic work", timestamp(2))["task_id"]
    workflow.start(task_id, timestamp(3))
    workflow.complete(task_id, "scripted-demo-operator", timestamp(4))
    return workflow, case_id, task_id


def test_complete_needs_independent_verification_not_automatic_success():
    workflow, case_id, task_id = complete_case()
    before = workflow.snapshot()
    assert before["cases"][0]["status"] == "AWAITING_VERIFICATION"
    assert before["tasks"][0]["status"] == "AWAITING_VERIFICATION"
    assert before["cases"][0]["verification_observation_id"] is None
    clear = observation(5, condition="CLEAR")
    result = workflow.verify(case_id, clear, True, "scripted-reviewer", "Independent new frame is clear", timestamp(6))
    assert result["case"]["status"] == "VERIFIED"
    assert workflow.snapshot()["tasks"][0]["task_id"] == task_id
    assert workflow.snapshot()["tasks"][0]["status"] == "VERIFIED"
    assert len(workflow.snapshot()["observations"]) == 2


def test_repeated_observation_and_operations_do_not_duplicate_cases_tasks_or_events():
    workflow = GroundsWorkflow(**IDENTITY)
    first = workflow.observe(observation())
    for _ in range(3):
        assert workflow.observe(observation(), timestamp(1))["disposition"] == "duplicate"
    case_id = first["case_id"]
    args = (case_id, "confirm", "manager", "confirm synthetic evidence", timestamp(1))
    workflow.review(*args)
    assert workflow.review(*args)["disposition"] == "duplicate"
    args = (case_id, HUMAN, "REPAIR_DIVOT", "manager", "assignment", timestamp(2))
    task_id = workflow.assign(*args)["task_id"]
    assert workflow.assign(*args)["task_id"] == task_id
    workflow.start(task_id, timestamp(3))
    assert workflow.start(task_id, timestamp(3))["disposition"] == "duplicate"
    workflow.complete(task_id, "crew", timestamp(4))
    assert workflow.complete(task_id, "crew", timestamp(4))["disposition"] == "duplicate"
    args = (case_id, observation(5, condition="CLEAR"), True, "manager", "rechecked", timestamp(6))
    workflow.verify(*args)
    assert workflow.verify(*args)["disposition"] == "duplicate"
    assert len(workflow.events) == 6
    assert len(workflow.snapshot()["cases"]) == len(workflow.snapshot()["tasks"]) == 1


def test_multiple_frames_correlate_active_case_without_confirming_it():
    workflow = GroundsWorkflow(**IDENTITY)
    case_id = workflow.observe(observation())["case_id"]
    assert workflow.observe(observation(1))["case_id"] == case_id
    snapshot = workflow.snapshot()
    assert len(snapshot["cases"]) == 1
    assert len(snapshot["cases"][0]["observation_ids"]) == 2
    assert snapshot["cases"][0]["status"] == "CANDIDATE"
    assert snapshot["tasks"] == []


@pytest.mark.parametrize("quality", ["BLURRED", "OCCLUDED", "POSE_UNCERTAIN"])
@pytest.mark.parametrize("condition", ["DIVOT", "CLEAR"])
def test_bad_quality_records_evidence_without_asserting_health_or_issuing_case(quality, condition):
    workflow = GroundsWorkflow(**IDENTITY)
    receipt = workflow.observe(observation(condition=condition, quality=quality, cart_position=None, target_position=None))
    assert receipt["case_id"] is None
    assert len(workflow.snapshot()["observations"]) == 1
    assert workflow.snapshot()["cases"] == workflow.snapshot()["tasks"] == []


def test_clear_observation_does_not_close_existing_case_implicitly():
    workflow, case_id, _ = complete_case()
    workflow.observe(observation(5, condition="CLEAR"))
    assert workflow.snapshot()["cases"][0]["status"] == "AWAITING_VERIFICATION"
    workflow.verify(case_id, observation(5, condition="CLEAR"), True, "manager", "reviewed later", timestamp(6))
    assert workflow.snapshot()["cases"][0]["status"] == "VERIFIED"
    assert len(workflow.snapshot()["observations"]) == 2


@pytest.mark.parametrize("resource,task_kind", [
    ({"id": "CE82A", "kind": "SIMULATED_ROBOT", "capabilities": ["COLLECT_BALLS"]}, "REPAIR_DIVOT"),
    ({"id": "sim-divot-robot", "kind": "SIMULATED_ROBOT", "capabilities": ["REPAIR_DIVOT"]}, "REPAIR_DIVOT"),
    (HUMAN, "RAKE_BUNKER"), (HUMAN, "MOW"),
])
def test_incompatible_resource_and_task_rejected(resource, task_kind):
    workflow = GroundsWorkflow(**IDENTITY)
    case_id = workflow.observe(observation())["case_id"]
    workflow.review(case_id, "confirm", "manager", "review", timestamp(1))
    with pytest.raises(GroundsError, match="grounds_incompatible_resource"):
        workflow.assign(case_id, resource, task_kind, "manager", "assignment", timestamp(2))
    assert workflow.snapshot()["tasks"] == []


def test_synthetic_robot_can_rake_only_after_explicit_confirmation():
    workflow = GroundsWorkflow(**IDENTITY)
    case_id = workflow.observe(observation(condition="BUNKER_SURFACE"))["case_id"]
    with pytest.raises(GroundsError, match="grounds_transition_rejected"):
        workflow.assign(case_id, ROBOT, "RAKE_BUNKER", "manager", "assignment", timestamp(1))
    workflow.review(case_id, "confirm", "manager", "review", timestamp(1))
    assigned = workflow.assign(case_id, ROBOT, "RAKE_BUNKER", "manager", "assignment", timestamp(2))
    assert assigned["task"]["resource"]["kind"] == "SIMULATED_ROBOT"
    assert assigned["task"]["status"] == "ASSIGNED"


def test_standing_water_only_supports_inspection_not_an_invented_drainage_operation():
    workflow, case_id, _ = complete_case(condition="STANDING_WATER", task_kind="INSPECT")
    workflow.verify(case_id, observation(5, condition="CLEAR", inspected_condition="STANDING_WATER"), True, "reviewer", "Synthetic area inspected clear", timestamp(6))
    assert workflow.snapshot()["cases"][0]["status"] == "VERIFIED"


def test_one_resource_cannot_be_double_booked():
    workflow = GroundsWorkflow(**IDENTITY)
    first = workflow.observe(observation())["case_id"]
    second = workflow.observe(observation(1, checkpoint_id="checkpoint-2"))["case_id"]
    for case_id in (first, second):
        workflow.review(case_id, "confirm", "manager", "review", timestamp(2))
    workflow.assign(first, HUMAN, "REPAIR_DIVOT", "manager", "first", timestamp(3))
    with pytest.raises(GroundsError, match="grounds_resource_busy"):
        workflow.assign(second, HUMAN, "REPAIR_DIVOT", "manager", "second", timestamp(3))


@pytest.mark.parametrize("second", [0, 3, 4])
def test_verification_must_be_captured_strictly_after_completion(second):
    workflow, case_id, _ = complete_case()
    with pytest.raises(GroundsError):
        workflow.verify(case_id, observation(second, condition="CLEAR", frame_id="old-independent-frame"), True, "reviewer", "old image", timestamp(6))
    assert workflow.snapshot()["cases"][0]["status"] == "AWAITING_VERIFICATION"


@pytest.mark.parametrize("change", [
    {"checkpoint_id": "another-point"}, {"hole_number": 2}, {"feature_id": "another-feature"},
    {"inspected_condition": "BUNKER_SURFACE"}, {"quality": "BLURRED"},
    {"frame_id": "synthetic-frame-0"},
])
def test_verification_requires_independent_usable_same_point_and_condition(change):
    workflow, case_id, _ = complete_case()
    before = workflow.to_dict()
    with pytest.raises(GroundsError):
        workflow.verify(case_id, observation(5, condition="CLEAR", **change), True, "reviewer", "verify", timestamp(6))
    assert workflow.to_dict() == before


def test_failed_verification_reopens_without_erasing_previous_task():
    workflow, case_id, task_id = complete_case()
    result = workflow.verify(case_id, observation(5), False, "reviewer", "Defect persists", timestamp(6))
    assert result["case"]["status"] == "REOPENED"
    assert workflow.snapshot()["tasks"][0]["status"] == "VERIFICATION_FAILED"
    second = workflow.assign(case_id, HUMAN, "REPAIR_DIVOT", "manager", "second repair", timestamp(7))["task_id"]
    assert second != task_id
    workflow.start(second, timestamp(8))
    workflow.complete(second, "crew", timestamp(9))
    workflow.verify(case_id, observation(10, condition="CLEAR"), True, "reviewer", "New independent evidence clear", timestamp(11))
    assert len(workflow.snapshot()["tasks"]) == 2
    assert workflow.snapshot()["cases"][0]["status"] == "VERIFIED"


@pytest.mark.parametrize("condition,passed", [("DIVOT", True), ("CLEAR", False)])
def test_verification_result_must_agree_with_evidence(condition, passed):
    workflow, case_id, _ = complete_case()
    with pytest.raises(GroundsError, match="grounds_verification_mismatch"):
        workflow.verify(case_id, observation(5, condition=condition), passed, "reviewer", "verify", timestamp(6))


def test_newer_observation_blocks_older_clear_verification():
    workflow, case_id, _ = complete_case()
    workflow.observe(observation(6))
    with pytest.raises(GroundsError, match="grounds_old_verification"):
        workflow.verify(case_id, observation(5, condition="CLEAR"), True, "reviewer", "old clear frame", timestamp(7))
    assert workflow.snapshot()["cases"][0]["status"] == "AWAITING_VERIFICATION"


@pytest.mark.parametrize("recorded,verification,passed", [
    ("DIVOT", "CLEAR", True), ("CLEAR", "DIVOT", False),
])
def test_conflicting_usable_same_time_frames_require_newer_verification(recorded, verification, passed):
    workflow, case_id, _ = complete_case()
    workflow.observe(observation(5, condition=recorded, frame_id="camera-a-frame"), timestamp(6))
    before = workflow.to_dict()
    with pytest.raises(GroundsError, match="grounds_conflicting_verification"):
        workflow.verify(case_id, observation(5, condition=verification, frame_id="camera-b-frame"), passed,
                        "reviewer", "Select one conflicting frame", timestamp(7))
    assert workflow.to_dict() == before
    assert workflow.snapshot()["cases"][0]["status"] == "AWAITING_VERIFICATION"
    result = workflow.verify(case_id, observation(8, condition="CLEAR", frame_id="new-independent-frame"), True,
                             "reviewer", "Later evidence resolves the conflict", timestamp(9))
    assert result["case"]["status"] == "VERIFIED"


def test_agreeing_clear_same_time_frames_can_verify_without_false_conflict():
    workflow, case_id, _ = complete_case()
    workflow.observe(observation(5, condition="CLEAR", frame_id="camera-a-frame"), timestamp(6))
    result = workflow.verify(case_id, observation(5, condition="CLEAR", frame_id="camera-b-frame"), True,
                             "reviewer", "Independent agreeing frames", timestamp(7))
    assert result["case"]["status"] == "VERIFIED"


def test_delayed_historical_findings_cannot_open_new_episode_after_verification():
    workflow, case_id, _ = complete_case()
    workflow.verify(case_id, observation(5, condition="CLEAR"), True, "reviewer", "verified", timestamp(6))
    delayed = observation(3, frame_id="delayed-historical-frame")
    assert workflow.observe(delayed, timestamp(7))["case_id"] is None
    assert len(workflow.snapshot()["cases"]) == 1
    fresh = workflow.observe(observation(8))
    assert fresh["case_id"] != case_id
    assert len(workflow.snapshot()["cases"]) == 2


def test_delayed_historical_findings_cannot_reopen_dismissed_case():
    workflow = GroundsWorkflow(**IDENTITY)
    case_id = workflow.observe(observation())["case_id"]
    workflow.review(case_id, "dismiss", "manager", "false positive", timestamp(3))
    assert workflow.observe(observation(2), timestamp(4))["case_id"] is None
    assert len(workflow.snapshot()["cases"]) == 1


def test_priority_and_deadline_are_explicit_human_records_with_reason():
    workflow = GroundsWorkflow(**IDENTITY)
    case_id = workflow.observe(observation())["case_id"]
    with pytest.raises(GroundsError):
        workflow.adjust(case_id, "URGENT", timestamp(9), "manager", "", timestamp(1))
    workflow.adjust(case_id, "HIGH", timestamp(9), "manager", "Upcoming group", timestamp(1))
    before = workflow.events[-1]
    workflow.adjust(case_id, "NORMAL", None, "manager", "Group rescheduled", timestamp(2))
    assert before == workflow.events[-2]
    case = workflow.snapshot()["cases"][0]
    assert case["priority"] == "NORMAL" and case["deadline_at_utc"] is None
    assert len(workflow.events) == 3


def test_dismissal_retains_evidence_and_requires_new_evidence_for_new_case():
    workflow = GroundsWorkflow(**IDENTITY)
    first_obs = observation()
    first = workflow.observe(first_obs)["case_id"]
    workflow.review(first, "dismiss", "manager", "Synthetic false positive", timestamp(1))
    assert workflow.observe(first_obs, timestamp(2))["case_id"] == first
    second = workflow.observe(observation(3))["case_id"]
    assert second != first
    assert [c["status"] for c in workflow.snapshot()["cases"]] == ["DISMISSED", "CANDIDATE"]


def test_export_replay_and_detached_views_are_deterministic():
    workflow, case_id, _ = complete_case()
    workflow.verify(case_id, observation(5, condition="CLEAR"), True, "reviewer", "checked", timestamp(6))
    exported = workflow.to_dict()
    restored = GroundsWorkflow.from_dict(json.loads(json.dumps(exported)))
    assert restored.snapshot() == workflow.snapshot()
    snapshot = restored.snapshot()
    snapshot["cases"][0]["status"] = "tampered"
    snapshot["events"][0]["payload"]["case_id"] = "tampered"
    restored.events[0]["payload"]["case_id"] = "tampered"
    assert restored.to_dict() == exported


@pytest.mark.parametrize("corrupt", [
    lambda body: body.update(environment="PHYSICAL"),
    lambda body: body.update(map_revision="changed"),
    lambda body: body["events"][0].update(event_hash="0" * 64),
    lambda body: body["events"][0]["payload"].update(case_id="changed"),
    lambda body: body["events"][1]["payload"].update(decision="delete"),
    lambda body: body["events"].append(deepcopy(body["events"][-1])),
    lambda body: body["events"].reverse(),
    lambda body: body["events"][1].update(sequence=9),
])
def test_tampered_or_illegal_history_is_rejected(corrupt):
    workflow, _, _ = complete_case()
    exported = workflow.to_dict()
    corrupt(exported)
    with pytest.raises(GroundsError, match="grounds_invalid_history"):
        GroundsWorkflow.from_dict(exported)


def test_semantically_illegal_history_rejected_even_with_recomputed_hash():
    workflow, _, _ = complete_case()
    body = workflow.to_dict()
    body["events"][-1]["kind"] = "task_started"
    body["events"][-1]["payload"].pop("operator")
    event = body["events"][-1]
    digest = hashlib.sha256(json.dumps({k: v for k, v in event.items() if k not in {"event_hash", "event_id"}}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    event.update(event_hash=digest, event_id="grounds_event_" + digest[:32])
    with pytest.raises(GroundsError, match="grounds_invalid_history"):
        GroundsWorkflow.from_dict(body)


@pytest.mark.parametrize("change", [
    {"source_kind": "CAMERA"}, {"environment": "PHYSICAL"}, {"score_calibration": "CALIBRATED"},
    {"observation_id": "observation_tampered"}, {"deployment_id": "another"}, {"map_revision": "another"},
])
def test_observation_seam_rejects_wrong_identity_provenance_or_content(change):
    workflow = GroundsWorkflow(**IDENTITY)
    obs = observation()
    obs.update(change)
    with pytest.raises(GroundsError):
        workflow.observe(obs)
    assert workflow.events == ()


def test_same_frame_conflicting_payload_is_not_a_second_case():
    workflow = GroundsWorkflow(**IDENTITY)
    workflow.observe(observation())
    with pytest.raises(GroundsError, match="grounds_observation_conflict"):
        workflow.observe(observation(detection_score=0.8))
    assert len(workflow.snapshot()["cases"]) == len(workflow.events) == 1


def test_explicit_times_and_canonical_fractional_observation_ids():
    workflow = GroundsWorkflow(**IDENTITY)
    obs = observation(captured_at_utc="2026-09-16T08:00:00.100000Z")
    receipt = workflow.observe(obs, datetime(2026, 9, 16, 8, 0, 1, tzinfo=timezone.utc))
    assert receipt["case_id"] is not None
    assert workflow.snapshot()["observations"][0]["captured_at_utc"].endswith(".1Z")
    with pytest.raises(GroundsError, match="grounds_future_observation"):
        workflow.observe(observation(2), timestamp(1))
    with pytest.raises(GroundsError, match="grounds_time_regression"):
        workflow.review(receipt["case_id"], "confirm", "manager", "review", timestamp(0))


def test_workflow_stays_stdlib_and_has_no_execution_or_io_imports():
    source = (Path(__file__).parents[2] / "nxt_pilot_ops" / "grounds.py").read_text()
    imports = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".")[0])
            assert node.level == 0
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            assert name not in {"open", "now", "utcnow", "uuid4", "random", "apply_directive", "send_robot_command"}
    assert imports <= {"__future__", "datetime", "hashlib", "json", "math", "re", "typing"}
