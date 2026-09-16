"""Acceptance 6: bounded reminders for unacknowledged cases; escalation and real recurrence are not deduplicated away."""

from __future__ import annotations

from nxt_edge_interventions import ACKNOWLEDGED, CRITICAL, WARNING
from tests.edge_interventions.conftest import InterventionHarness, run_until
from tests.edge_task.conftest import run_until as edge_run_until


def _blocked_task(stack: InterventionHarness) -> str:
    stack.edge.start_all("help_needs_manual_recharge")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    run_until(stack, lambda: stack.receiver.unique_received() == 1)
    return task_id


def test_unacknowledged_case_is_reminded_at_the_interval_then_stops_at_the_cap(stack: InterventionHarness) -> None:
    case = _blocked_task(stack)
    case = stack.cases()[0]
    interval = stack.config.reminder_interval_s
    assert stack.config.max_reminders == 2
    stack.step(int(interval) - 3)
    assert len(stack.notifications(case.case_id)) == 1  # not before the interval
    run_until(stack, lambda: len(stack.notifications(case.case_id)) == 2, max_rounds=10)
    reminder = stack.notifications(case.case_id)[1]
    assert reminder.intent == "REMINDER" and reminder.severity == WARNING
    run_until(stack, lambda: len(stack.notifications(case.case_id)) == 3, max_rounds=int(interval) + 5)
    stack.step(int(interval) * 2 + 5)
    assert len(stack.notifications(case.case_id)) == 3  # OPENED + 2 reminders, then the cap
    assert stack.cases()[0].reminders_exhausted is True
    assert stack.kinds().count("reminders_exhausted") == 1
    assert stack.receiver.unique_received() == 3  # each intent is a distinct notification id


def test_acknowledged_case_gets_no_reminders_but_stays_visible(stack: InterventionHarness) -> None:
    _blocked_task(stack)
    case = stack.cases()[0]
    assert stack.ack(case.case_id)["status"] == "recorded"
    stack.step(int(stack.config.reminder_interval_s) * 3)
    assert len(stack.notifications(case.case_id)) == 1
    view = stack.view()
    assert view.cases[case.case_id].human_state == ACKNOWLEDGED
    assert view.cases[case.case_id].condition_active is True  # the robot still needs help


def test_escalation_notifies_even_when_acknowledged_and_is_not_deduplicated(stack: InterventionHarness) -> None:
    """A silent robot with no task is WARNING; a task then targets it: CRITICAL, one escalation notice."""

    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: stack.edge.device("picker-01")["connectivity"] == "OFFLINE", max_rounds=40)
    run_until(stack, lambda: len(stack.cases("DEVICE_UNREACHABLE")) == 1)
    case = stack.cases("DEVICE_UNREACHABLE")[0]
    assert case.severity == WARNING and case.evidence["open_task_id"] is None
    assert stack.ack(case.case_id)["status"] == "recorded"
    # The Edge still accepts a task for the seen incarnation; it stays CREATED
    # because the robot is gone: the unreachable case now covers an open task.
    task_id = stack.edge.create()["task_id"]
    run_until(stack, lambda: stack.cases("DEVICE_UNREACHABLE")[0].severity == CRITICAL, max_rounds=10)
    case = stack.cases("DEVICE_UNREACHABLE")[0]
    assert case.evidence["open_task_id"] == task_id and case.human_state == ACKNOWLEDGED
    notifications = stack.notifications(case.case_id)
    assert [n.intent for n in notifications] == ["OPENED", "ESCALATION"]
    assert notifications[1].severity == CRITICAL
    assert stack.kinds().count("case_escalated") == 1
    stack.step(20)
    assert [n.intent for n in stack.notifications(case.case_id)] == ["OPENED", "ESCALATION"]  # no second escalation


def test_real_recurrence_after_resolution_opens_a_new_case_naming_the_old_one(stack: InterventionHarness) -> None:
    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: len(stack.cases("DEVICE_UNREACHABLE")) == 1, max_rounds=40)
    first = stack.cases("DEVICE_UNREACHABLE")[0]
    assert stack.ack(first.case_id)["status"] == "recorded"
    assert stack.resolve(first.case_id, resolution="cable reseated")["status"] == "recorded"
    # The robot returns: the condition clears but nothing reopens.
    stack.edge.start_robot("picker-01")
    stack.step(3)
    assert len(stack.cases("DEVICE_UNREACHABLE")) == 1
    # It disappears again later: new OFFLINE transition = new evidence = recurrence.
    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: len(stack.cases("DEVICE_UNREACHABLE")) == 2, max_rounds=40)
    second = [c for c in stack.cases("DEVICE_UNREACHABLE") if c.case_id != first.case_id][0]
    assert second.recurrence_of == first.case_id
    assert second.evidence_key != first.evidence_key
    assert [n.intent for n in stack.notifications(second.case_id)] == ["OPENED"]
    assert stack.receiver.unique_received() == 2


def test_resolved_case_with_unchanged_evidence_is_not_reopened(stack: InterventionHarness) -> None:
    _blocked_task(stack)
    case = stack.cases()[0]
    stack.ack(case.case_id)
    assert stack.resolve(case.case_id, resolution="operator noted; robot still blocked")["status"] == "recorded"
    stack.step(20)
    assert len(stack.cases()) == 1  # same evidence, human already recorded a disposition
    assert stack.view().cases[case.case_id].condition_active is True  # the robot is still blocked; visible
    assert stack.receiver.unique_received() == 1
