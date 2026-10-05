"""Acceptance 1-3: a normal task raises nothing; an explicit request raises one case and one notification; repeats do not multiply."""

from __future__ import annotations

from nxt_edge_interventions import ACKNOWLEDGED, DELIVERED, OPEN, WARNING
from tests.edge_interventions.conftest import InterventionHarness, run_until
from tests.edge_task.conftest import run_until as edge_run_until


def test_normal_task_produces_no_human_case_or_notification(stack: InterventionHarness) -> None:
    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "SUCCEEDED")
    stack.step(5)
    assert stack.cases() == []
    assert stack.notifications() == []
    assert stack.receiver.unique_received() == 0
    assert stack.kinds() == ["intervention_service_started"]


def test_explicit_assistance_request_opens_one_case_and_one_unique_notification(stack: InterventionHarness) -> None:
    stack.edge.start_all("help_needs_manual_recharge")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    run_until(stack, lambda: stack.receiver.unique_received() == 1)
    cases = stack.cases("ASSISTANCE_REQUIRED")
    assert len(cases) == 1
    case = cases[0]
    assert case.human_state == OPEN and case.severity == WARNING and case.subject_id == task_id
    assert case.evidence["reason_code"] == "energy_insufficient"
    assert case.evidence["event_record_id"] is not None
    notifications = stack.notifications(case.case_id)
    assert len(notifications) == 1 and notifications[0].intent == "OPENED" and notifications[0].state == DELIVERED
    assert notifications[0].receipt_id is not None
    assert stack.attempts() == 1
    delivered = stack.receiver.deliveries[0]
    assert delivered["notification_id"] == notifications[0].notification_id
    assert "ASSISTANCE_REQUIRED" in delivered["message"] and "energy_insufficient" in delivered["message"]
    assert delivered["environment"] == {"kind": "SIMULATION", "simulation_env_id": stack.config.simulation_env_id}
    # The task and device state are untouched by the case.
    assert stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN"
    assert stack.edge.executions("picker-01", task_id) == 1


def test_repeated_assistance_evidence_does_not_reopen_or_renotify(stack: InterventionHarness) -> None:
    stack.edge.start_all("help_needs_manual_recharge")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    run_until(stack, lambda: stack.receiver.unique_received() == 1)
    before = stack.kinds()
    # The robot keeps heartbeating with the same blocked task and the same
    # ASSISTANCE_REQUIRED evidence; the service keeps reconciling.  Below the
    # reminder interval nothing new may appear.
    stack.step(20)
    assert stack.kinds() == before
    assert len(stack.cases()) == 1 and len(stack.notifications()) == 1
    assert stack.receiver.unique_received() == 1 and stack.receiver.duplicate_attempts() == 0
    # A replayed duplicate of the same ASSISTANCE_REQUIRED event at the Edge
    # (same key) is not new evidence either.
    from nxt_edge_task.contracts import EventKind

    replay = next(
        r.payload["event"]
        for r in stack.edge.edge_records()
        if r.record_kind == "task_event_received" and r.payload["event"]["kind"] == EventKind.ASSISTANCE_REQUIRED.value
    )
    stack.edge.replay_event(replay)
    stack.step(3)
    assert len(stack.cases()) == 1 and len(stack.notifications()) == 1
    assert stack.receiver.unique_received() == 1


def test_case_survives_service_restart_and_is_not_reopened(stack: InterventionHarness) -> None:
    stack.edge.start_all("help_needs_manual_recharge")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    run_until(stack, lambda: stack.receiver.unique_received() == 1)
    stack.crash_service()
    stack.step(3, service=False)
    stack.start_service()
    stack.step(3)
    assert len(stack.cases()) == 1 and len(stack.notifications()) == 1
    assert stack.receiver.unique_received() == 1
    assert stack.cases()[0].human_state == OPEN


def test_acknowledgement_is_recorded_without_touching_task_or_device_state(stack: InterventionHarness) -> None:
    stack.edge.start_all("help_needs_manual_recharge")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    run_until(stack, lambda: stack.receiver.unique_received() == 1)
    case = stack.cases()[0]
    result = stack.ack(case.case_id, operator="alice", note="on my way")
    assert result["status"] == "recorded" and result["human_state"] == ACKNOWLEDGED
    assert result["authorization_effect"] == "none" and result["device_state_effect"] == "none"
    stack.step(3)
    assert stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN"
    assert stack.edge.device("picker-01")["last_reported_availability"] == "awaiting_human"
    assert stack.edge.executions("picker-01", task_id) == 1
