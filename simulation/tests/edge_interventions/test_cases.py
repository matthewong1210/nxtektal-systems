"""Acceptance 8 and the remaining case kinds: unreachable devices keep last valid data and an explicit unknown marker; exhausted retries and evidence conflicts open cases."""

from __future__ import annotations

from nxt_edge_interventions import CRITICAL, WARNING
from tests.edge_interventions.conftest import InterventionHarness, run_until
from tests.edge_task.conftest import run_until as edge_run_until


def test_lost_robot_with_open_task_opens_a_critical_case_with_last_valid_data_and_unknown_markers(stack: InterventionHarness) -> None:
    stack.edge.start_all("silent_after_accept")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create(progress_window_s=600)["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "ACCEPTED")
    last_seen = stack.edge.device("picker-01")["last_valid_status_received_at_utc"]
    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: len(stack.cases("DEVICE_UNREACHABLE")) == 1, max_rounds=40)
    case = stack.cases("DEVICE_UNREACHABLE")[0]
    assert case.severity == CRITICAL
    evidence = case.evidence
    assert evidence["open_task_id"] == task_id
    assert evidence["last_valid_status_received_at_utc"] is not None
    device = stack.edge.device("picker-01")
    assert evidence["last_reported_availability"] == device["last_reported_availability"]  # the Edge's last valid word, not invented
    assert evidence["state_unknown"] is True and evidence["stopped_confirmed"] is False
    message = stack.notifications(case.case_id)[0].message
    assert "UNKNOWN" in message and "not confirmed stopped" in message and device["last_reported_availability"] in message
    assert evidence["last_valid_status_received_at_utc"] in message
    # The Edge keeps its own view: OFFLINE with the last valid status intact.
    assert device["connectivity"] == "OFFLINE" and device["last_valid_status_received_at_utc"] == evidence["last_valid_status_received_at_utc"]


def test_exhausted_republish_budget_opens_a_result_unconfirmed_case(stack: InterventionHarness) -> None:
    stack.edge.broker.drop = lambda topic, payload: topic.endswith("/task/request")
    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create(progress_window_s=4)["task_id"]
    run_until(stack, lambda: stack.edge.task(task_id)["publish_attempts"] >= stack.edge.config.max_republish_attempts, max_rounds=120)
    run_until(stack, lambda: len(stack.cases("RESULT_UNCONFIRMED")) == 1, max_rounds=10)
    case = stack.cases("RESULT_UNCONFIRMED")[0]
    assert case.severity == WARNING and case.subject_id == task_id
    assert case.evidence["detail"] == "republish budget exhausted"
    assert case.evidence["publish_attempts"] == stack.edge.config.max_republish_attempts
    assert stack.edge.task(task_id)["state"] == "CREATED"  # the case changes nothing
    stack.step(30)
    assert len(stack.cases("RESULT_UNCONFIRMED")) == 1  # steady evidence: one case


def test_evidence_conflict_opens_a_critical_case_and_resolution_does_not_reopen_authorization(stack: InterventionHarness) -> None:
    from tests.edge_task.test_cases import inject_event

    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "SUCCEEDED")
    # A same-key different terminal: PR A closes the gate.
    key = stack.edge.task(task_id)["first_terminal"]
    inject_event(stack.edge, task_id, "FAILED", key["boot_sequence"], key["event_sequence"], reason="cannot_continue")
    assert stack.edge.task(task_id)["effective_result"] == "CONFLICT"
    blocked = stack.edge.create(issued="2026-09-12T08:05:00.000000Z")
    assert blocked["code"] == "authorization_blocked"
    run_until(stack, lambda: len(stack.cases("EVIDENCE_CONFLICT")) == 1, max_rounds=10)
    case = stack.cases("EVIDENCE_CONFLICT")[0]
    assert case.severity == CRITICAL and case.subject_id == task_id
    assert "conflicting_terminal" in case.evidence["detail"]
    assert "authorization stays closed" in stack.notifications(case.case_id)[0].message
    assert stack.ack(case.case_id)["status"] == "recorded"
    assert stack.resolve(case.case_id, resolution="reviewed both terminals; needs the recovery contract")["status"] == "recorded"
    stack.step(3, robots=False)
    still_blocked = stack.edge.create(issued="2026-09-12T08:06:00.000000Z")
    assert still_blocked["code"] == "authorization_blocked"  # resolve reopened nothing
    assert stack.edge.task(task_id)["effective_result"] == "CONFLICT"


def test_session_regression_opens_a_device_conflict_case(stack: InterventionHarness) -> None:
    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    stack.edge.crash_robot("picker-01")
    stack.edge.robot_journal_path("picker-01").unlink()
    stack.edge.start_robot("picker-01", initialize=True)
    run_until(stack, lambda: stack.edge.device("picker-01")["session_regression"] is True, max_rounds=10)
    run_until(stack, lambda: len(stack.cases("EVIDENCE_CONFLICT")) == 1, max_rounds=10)
    case = stack.cases("EVIDENCE_CONFLICT")[0]
    assert case.subject_kind == "device" and case.evidence["detail"] == "session_regression"
    assert stack.notifications(case.case_id)[0].severity == CRITICAL


def test_unresolved_case_records_when_its_condition_returns(stack: InterventionHarness) -> None:
    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: len(stack.cases("DEVICE_UNREACHABLE")) == 1, max_rounds=40)
    case = stack.cases("DEVICE_UNREACHABLE")[0]
    first_evidence_key = case.evidence_key
    assert stack.ack(case.case_id)["status"] == "recorded"  # suppress reminders while the condition changes

    stack.edge.start_robot("picker-01")
    run_until(stack, lambda: not stack.view().cases[case.case_id].condition_active, max_rounds=20)
    assert stack.kinds().count("case_condition_cleared") == 1

    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: stack.kinds().count("case_condition_returned") == 1, max_rounds=40)
    returned = next(r for r in stack.records() if r.record_kind == "case_condition_returned")
    live = stack.view().cases[case.case_id]
    assert returned.payload["case_id"] == case.case_id
    assert live.condition_active is True
    assert live.evidence_key == list(returned.payload["evidence_key"])
    assert live.evidence_key != first_evidence_key
    assert live.evidence_history[-1]["record_id"] == returned.record_id
    assert len(stack.cases("DEVICE_UNREACHABLE")) == 1
    assert [n.intent for n in stack.notifications(case.case_id)] == ["OPENED"]
