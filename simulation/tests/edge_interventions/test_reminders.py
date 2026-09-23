"""Acceptance 6: bounded reminders for unacknowledged cases; escalation and real recurrence are not deduplicated away."""

from __future__ import annotations

import pytest

from nxt_edge_interventions import ACKNOWLEDGED, CRITICAL, DELIVERED, UNKNOWN, WARNING
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
    assert case.evidence_key == [case.evidence["offline_since_utc"], task_id]
    notifications = stack.notifications(case.case_id)
    assert [n.intent for n in notifications] == ["OPENED", "ESCALATION"]
    assert notifications[1].severity == CRITICAL
    assert stack.kinds().count("case_escalated") == 1
    stack.step(20)
    assert [n.intent for n in stack.notifications(case.case_id)] == ["OPENED", "ESCALATION"]  # no second escalation
    assert "case_evidence_added" not in stack.kinds()  # replay already synchronized the escalation key
    stack.crash_service()
    stack.start_service()
    stack.step(3, edge=False, robots=False)
    assert "case_evidence_added" not in stack.kinds()


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
    stack.step(int(stack.config.reminder_interval_s) * 2 + 1)
    assert len(stack.cases()) == 1  # same evidence, human already recorded a disposition
    assert stack.view().cases[case.case_id].condition_active is True  # the robot is still blocked; visible
    assert [n.intent for n in stack.notifications(case.case_id)] == ["OPENED"]  # beyond the interval: no new reminder
    assert stack.receiver.unique_received() == 1


def test_resolve_does_not_cancel_an_already_committed_reminder(stack: InterventionHarness) -> None:
    _blocked_task(stack)
    case = stack.cases()[0]
    stack.receiver.unreachable = True
    run_until(
        stack,
        lambda: any(n.intent == "REMINDER" and n.attempts == 1 for n in stack.notifications(case.case_id)),
        max_rounds=int(stack.config.reminder_interval_s) + 5,
    )
    reminder = next(n for n in stack.notifications(case.case_id) if n.intent == "REMINDER")
    assert reminder.state == UNKNOWN
    assert stack.receiver.unique_received() == 1  # only OPENED reached the receiver

    assert stack.ack(case.case_id)["status"] == "recorded"
    assert stack.resolve(case.case_id)["status"] == "recorded"
    stack.receiver.unreachable = False
    run_until(
        stack,
        lambda: stack.view().notifications[reminder.notification_id].state == DELIVERED,
        max_rounds=int(stack.config.notify_retry_interval_s) + 5,
    )
    stack.step(int(stack.config.reminder_interval_s) * 2 + 1)

    notices = stack.notifications(case.case_id)
    assert [n.intent for n in notices] == ["OPENED", "REMINDER"]
    assert notices[1].notification_id == reminder.notification_id
    delivered_reminder = stack.view().notifications[reminder.notification_id]
    assert delivered_reminder.state == DELIVERED and delivered_reminder.attempts == 2
    assert stack.receiver.unique_received() == 2


def test_new_task_on_resolved_offline_warning_opens_a_critical_recurrence(stack: InterventionHarness) -> None:
    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: len(stack.cases("DEVICE_UNREACHABLE")) == 1, max_rounds=40)
    first = stack.cases("DEVICE_UNREACHABLE")[0]
    assert first.severity == WARNING and first.evidence["open_task_id"] is None
    edge_before = stack.edge.edge_journal_path.read_bytes()
    assert stack.ack(first.case_id)["status"] == "recorded"
    assert stack.resolve(first.case_id)["status"] == "recorded"
    assert stack.edge.edge_journal_path.read_bytes() == edge_before
    stack.step(3, edge=False, robots=False)
    assert len(stack.cases()) == 1 and len(stack.notifications()) == 1
    task_id = stack.edge.create()["task_id"]
    edge_before = stack.edge.edge_journal_path.read_bytes()
    stack.step(3, edge=False, robots=False)
    cases = stack.cases("DEVICE_UNREACHABLE")
    assert len(cases) == 2
    second = next(c for c in cases if c.case_id != first.case_id)
    assert second.severity == CRITICAL and second.recurrence_of == first.case_id
    assert second.evidence["offline_since_utc"] == first.evidence["offline_since_utc"]
    assert second.evidence["open_task_id"] == task_id
    assert [(n.intent, n.severity) for n in stack.notifications(second.case_id)] == [("OPENED", CRITICAL)]
    stack.ack(second.case_id)
    stack.resolve(second.case_id)
    stack.crash_service()
    stack.start_service()
    stack.step(3, edge=False, robots=False)
    assert len(stack.cases()) == 2 and stack.receiver.unique_received() == 2
    assert stack.edge.edge_journal_path.read_bytes() == edge_before


def _legacy_offline_case(stack: InterventionHarness, has_task: bool) -> tuple[str, str | None]:
    """Pre-fix PR B keyed both severities on offline time, with the task in evidence."""
    from nxt_edge_interventions import reconcile
    from nxt_edge_interventions.contracts import case_identity, utc_text
    from nxt_edge_task.journal import RecordSpec
    from scripts.edge_intervention_service_v0 import _to_spec, edge_snapshot

    stack.edge.start_all("silent_after_accept")
    stack.edge.step(2)
    task_id = stack.edge.create()["task_id"] if has_task else None
    if has_task:
        edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "ACCEPTED")
    stack.edge.crash_robot("picker-01")
    edge_run_until(stack.edge, lambda: stack.edge.device("picker-01")["connectivity"] == "OFFLINE", max_rounds=40)
    snapshot = edge_snapshot(stack.edge.config, stack.edge.edge_journal())
    new_specs = reconcile(stack.view(), snapshot, stack.clock())
    opened = next(s for s in new_specs if s.record_kind == "case_opened" and s.payload["kind"] == "DEVICE_UNREACHABLE")
    legacy_key = [opened.payload["evidence"]["offline_since_utc"]]
    legacy_id = case_identity("DEVICE_UNREACHABLE", "device", "picker-01", legacy_key)
    stack.journal().append(RecordSpec(
        record_kind="case_opened", origin="EDGE", recorded_at_utc=utc_text(stack.clock()),
        payload={**opened.payload, "case_id": legacy_id, "evidence_key": legacy_key},
    ))
    for spec in reconcile(stack.view(), snapshot, stack.clock()):
        stack.journal().append(_to_spec(spec))
    return legacy_id, task_id


@pytest.mark.parametrize("has_task", [False, True])
def test_legacy_resolved_offline_identity_stays_quiet_on_restart(stack: InterventionHarness, has_task: bool) -> None:
    legacy_id, task_id = _legacy_offline_case(stack, has_task)
    # Resolve before the patched service can normalize the old key. Comparing
    # evidence_key alone must fail this case when the legacy case has a task.
    assert stack.ack(legacy_id)["status"] == "recorded"
    assert stack.resolve(legacy_id)["status"] == "recorded"
    assert "case_evidence_added" not in stack.kinds()
    stack.start_service()
    stack.step(3, edge=False, robots=False)
    cases = stack.cases("DEVICE_UNREACHABLE")
    assert len(cases) == 1 and cases[0].case_id == legacy_id
    assert cases[0].evidence["open_task_id"] == task_id
    assert len(stack.notifications(legacy_id)) == 1
    assert "case_evidence_added" not in stack.kinds()
    assert stack.receiver.unique_received() == 1


def test_legacy_open_critical_case_normalizes_its_key_once_without_notifying(stack: InterventionHarness) -> None:
    legacy_id, task_id = _legacy_offline_case(stack, True)
    stack.start_service()
    before = len(stack.records())
    stack.step(1, edge=False, robots=False)
    case_records = [r.record_kind for r in stack.records()[before:] if r.record_kind.startswith("case_")]
    assert case_records == ["case_evidence_added"]
    case = stack.view().cases[legacy_id]
    assert case.evidence_key == [case.evidence["offline_since_utc"], task_id]
    stack.crash_service()
    stack.start_service()
    stack.step(3, edge=False, robots=False)
    assert [c.case_id for c in stack.cases("DEVICE_UNREACHABLE")] == [legacy_id]
    assert stack.kinds().count("case_evidence_added") == 1
    assert [n.intent for n in stack.notifications(legacy_id)] == ["OPENED"]
    assert stack.receiver.unique_received() == 1


def test_resolved_critical_offline_case_stays_quiet_when_task_ends_but_not_for_a_new_task(stack: InterventionHarness) -> None:
    from tests.edge_task.test_cases import inject_event

    stack.edge.start_all("silent_after_accept")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create(progress_window_s=600)["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "ACCEPTED")
    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: len(stack.cases("DEVICE_UNREACHABLE")) == 1, max_rounds=40)
    first = stack.cases("DEVICE_UNREACHABLE")[0]
    assert first.severity == CRITICAL
    edge_before = stack.edge.edge_journal_path.read_bytes()
    assert stack.ack(first.case_id)["status"] == "recorded"
    assert stack.resolve(first.case_id)["status"] == "recorded"
    assert stack.edge.edge_journal_path.read_bytes() == edge_before
    # A late terminal is task evidence; it does not refresh device liveness.
    inject_event(stack.edge, task_id, "SUCCEEDED", 1, 2)
    assert stack.edge.task(task_id)["state"] == "SUCCEEDED"
    assert stack.edge.device("picker-01")["connectivity"] == "OFFLINE"
    edge_before = stack.edge.edge_journal_path.read_bytes()
    stack.step(3, edge=False, robots=False)
    stack.crash_service()
    stack.start_service()
    stack.step(3, edge=False, robots=False)
    assert [c.case_id for c in stack.cases("DEVICE_UNREACHABLE")] == [first.case_id]
    assert len(stack.notifications()) == 1 and stack.receiver.unique_received() == 1
    assert stack.edge.edge_journal_path.read_bytes() == edge_before
    # Removing occupancy is quiet; a different new task is new risk evidence.
    second_task = stack.edge.create(issued="2026-09-12T08:01:00.000000Z")["task_id"]
    stack.step(3, edge=False, robots=False)
    cases = stack.cases("DEVICE_UNREACHABLE")
    assert len(cases) == 2
    second = next(c for c in cases if c.case_id != first.case_id)
    assert second.severity == CRITICAL and second.recurrence_of == first.case_id
    assert second.evidence["offline_since_utc"] == first.evidence["offline_since_utc"]
    assert second.evidence["open_task_id"] == second_task
    assert len(stack.notifications()) == 2 and stack.receiver.unique_received() == 2
