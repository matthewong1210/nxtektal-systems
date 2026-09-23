"""Acceptance 4-5: lost receipts retry under one id; crashes never lose the request for help or fake success."""

from __future__ import annotations

import pytest

from nxt_edge_interventions import DELIVERED, EXHAUSTED, UNKNOWN
from tests.edge_interventions.conftest import InterventionHarness, run_until
from tests.edge_task.conftest import run_until as edge_run_until


def _blocked_task(stack: InterventionHarness) -> str:
    stack.edge.start_all("help_needs_manual_recharge")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    return task_id


def test_lost_receipt_is_retried_under_the_same_id_and_received_once(stack: InterventionHarness) -> None:
    stack.receiver.drop_responses = 1  # the receiver persists, the answer is lost
    _blocked_task(stack)
    run_until(stack, lambda: stack.attempts() >= 1)
    notification = stack.notifications()[0]
    assert notification.state == UNKNOWN and notification.attempts == 1
    assert stack.receiver.unique_received() == 1  # already persisted at the receiver
    # No retry before the retry interval; then exactly one retry with the same id.
    stack.step(2)
    assert stack.attempts() == 1
    run_until(stack, lambda: stack.notifications()[0].state == DELIVERED, max_rounds=20)
    notification = stack.notifications()[0]
    assert notification.attempts == 2 and notification.receipt_id is not None
    assert {d["notification_id"] for d in stack.receiver.deliveries} == {notification.notification_id}
    assert stack.receiver.unique_received() == 1
    assert stack.receiver.duplicate_attempts() == 1
    # Attempts and unique receipts are separate facts.
    assert stack.attempts(notification.notification_id) == 2
    stack.step(30)
    assert stack.attempts(notification.notification_id) == 2


def test_unreachable_receiver_bounds_retries_and_never_reports_delivery(stack: InterventionHarness) -> None:
    stack.receiver.unreachable = True
    _blocked_task(stack)
    run_until(stack, lambda: any(n.state == EXHAUSTED for n in stack.notifications()), max_rounds=80)
    notification = stack.notifications()[0]
    assert notification.state == EXHAUSTED
    assert notification.attempts == stack.config.notify_max_attempts
    assert notification.receipt_id is None and notification.last_result == "unknown"
    assert stack.receiver.unique_received() == 0
    assert stack.attempts(notification.notification_id) == stack.config.notify_max_attempts
    assert "notification_exhausted" in stack.kinds()
    # Reminders are separate notifications with their own bounded budget; once
    # every intent is exhausted nothing is attempted again.
    stack.step(int(stack.config.reminder_interval_s) * 4)
    view = stack.view()
    assert all(n.state == EXHAUSTED for n in view.notifications.values())
    assert len(view.notifications) == 1 + stack.config.max_reminders
    assert stack.attempts() == len(view.notifications) * stack.config.notify_max_attempts
    total = stack.attempts()
    stack.step(40)
    assert stack.attempts() == total  # no further attempts
    # The case is still open and visible; exhaustion is not success.
    case = stack.cases()[0]
    assert case.human_state == "OPEN"
    assert stack.receiver.unique_received() == 0


def test_refusing_receiver_records_failed_attempts(stack: InterventionHarness) -> None:
    stack.receiver.refuse = True
    _blocked_task(stack)
    run_until(stack, lambda: any(n.state == EXHAUSTED for n in stack.notifications()), max_rounds=80)
    notification = stack.notifications()[0]
    assert notification.last_result == "failed" and notification.receipt_id is None
    assert stack.receiver.unique_received() == 0


def test_torn_batch_between_case_and_intent_is_repaired_on_restart(stack: InterventionHarness) -> None:
    """The case line is durable but the intent line was lost: the request for help survives."""

    task_id = _blocked_task(stack)
    run_until(stack, lambda: stack.receiver.unique_received() == 1)
    stack.crash_service()
    records = stack.records()
    opened_index = next(i for i, r in enumerate(records) if r.record_kind == "case_opened")
    # Tear after the case line: the intent, attempt, and result lines never happened.
    from tests.edge_task.conftest import Harness

    Harness._tear(stack.journal_path, opened_index + 1, keep_anchor=False)
    # The receiver's earlier receipt must not be assumed; wipe its ledger too so
    # the repaired intent is a genuinely new delivery.
    stack.receiver.journal.path.unlink()
    stack.receiver.journal.anchor_path.unlink()
    stack.receiver.__post_init__()
    stack.start_service()
    run_until(stack, lambda: stack.receiver.unique_received() == 1, max_rounds=10)
    assert len(stack.cases()) == 1
    notifications = stack.notifications()
    assert len(notifications) == 1 and notifications[0].intent == "OPENED" and notifications[0].state == DELIVERED
    assert stack.kinds().count("case_opened") == 1
    assert stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN"


@pytest.mark.parametrize("resolve_before_restart", [False, True])
def test_durable_escalation_repairs_its_missing_intent_once(stack: InterventionHarness, resolve_before_restart: bool) -> None:
    from nxt_edge_interventions import reconcile
    from scripts.edge_intervention_service_v0 import _to_spec, edge_snapshot

    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: len(stack.cases("DEVICE_UNREACHABLE")) == 1, max_rounds=40)
    case = stack.cases("DEVICE_UNREACHABLE")[0]
    assert case.severity == "WARNING"
    assert stack.ack(case.case_id)["status"] == "recorded"
    task_id = stack.edge.create()["task_id"]
    specs = reconcile(stack.view(), edge_snapshot(stack.edge.config, stack.edge.edge_journal()), stack.clock())
    assert [s.record_kind for s in specs] == ["case_escalated", "notification_intended"]
    expected = specs[1]
    # Only the first complete line survives the append; the original anchor
    # may lag this durable suffix, as permitted by the shared journal contract.
    stack.journal().append(_to_spec(specs[0]))
    stack.crash_service()
    if resolve_before_restart:
        assert stack.resolve(case.case_id)["status"] == "recorded"
    edge_before = stack.edge.edge_journal_path.read_bytes()
    stack.step(3, edge=False, robots=False, service=False)
    stack.start_service()
    stack.step(3, edge=False, robots=False)
    notices = stack.notifications(case.case_id)
    assert [n.intent for n in notices] == ["OPENED", "ESCALATION"]
    assert notices[1].state == DELIVERED and notices[1].payload["evidence"]["open_task_id"] == task_id
    repaired = next(r for r in stack.records() if r.record_kind == "notification_intended" and r.payload["intent"] == "ESCALATION")
    assert repaired.payload == expected.payload
    assert repaired.recorded_at_utc == expected.recorded_at_utc
    assert stack.receiver.unique_received() == 2
    stack.crash_service()
    stack.start_service()
    stack.step(3, edge=False, robots=False)
    assert stack.kinds().count("case_escalated") == 1
    assert len(stack.notifications(case.case_id)) == 2
    assert stack.receiver.unique_received() == 2
    assert stack.edge.edge_journal_path.read_bytes() == edge_before


def test_missing_opening_intent_is_repaired_before_new_escalation(stack: InterventionHarness) -> None:
    from nxt_edge_interventions import reconcile
    from scripts.edge_intervention_service_v0 import _to_spec, edge_snapshot

    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: stack.edge.device("picker-01")["connectivity"] == "OFFLINE", max_rounds=40)
    # Remove the already observed opening batch and replay only its case line.
    from tests.edge_task.conftest import Harness

    records = stack.records()
    index = next(i for i, r in enumerate(records) if r.record_kind == "case_opened")
    expected = records[index + 1]
    Harness._tear(stack.journal_path, index + 1, keep_anchor=False)
    stack.crash_service()
    stack.edge.create()
    specs = reconcile(stack.view(), edge_snapshot(stack.edge.config, stack.edge.edge_journal()), stack.clock())
    assert [s.record_kind for s in specs] == ["notification_intended"]
    assert specs[0].payload == expected.payload
    stack.journal().append(_to_spec(specs[0]))
    stack.start_service()
    stack.step(3, edge=False, robots=False)
    notices = stack.notifications()
    assert [(n.intent, n.ordinal, n.severity) for n in notices] == [("OPENED", 0, "WARNING"), ("ESCALATION", 1, "CRITICAL")]
    assert len({n.notification_id for n in notices}) == 2


def test_crash_after_attempt_before_send_retries_same_id_once_delivered(stack: InterventionHarness) -> None:
    """The attempt line is durable, the request never left: after restart it is retried under the same id."""

    stack.receiver.crash_sender_before_send = True
    stack.edge.start_all("help_needs_manual_recharge")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    try:
        run_until(stack, lambda: stack.attempts() >= 1, max_rounds=10)
    except RuntimeError as crash:  # the receiver double raises SenderCrashed (a RuntimeError)
        assert "process died" in str(crash)
    stack.crash_service()
    notification = stack.notifications()[0]
    assert notification.state == "ATTEMPTING" and notification.attempts == 1 and notification.receipt_id is None
    assert stack.receiver.unique_received() == 0
    stack.start_service()
    stack.step(1)
    assert stack.attempts(notification.notification_id) == 1  # not before the retry interval
    run_until(stack, lambda: stack.view().notifications[notification.notification_id].state == DELIVERED, max_rounds=20)
    assert stack.attempts(notification.notification_id) == 2
    assert stack.receiver.unique_received() == 1
    assert stack.kinds().count("notification_intended") == 1


def test_crash_after_receiver_persisted_before_result_is_journaled_never_fakes_or_loses_delivery(stack: InterventionHarness) -> None:
    """The receiver has it, the sender died before recording the result: retry yields the original receipt."""

    stack.receiver.crash_sender_after_persist = True
    stack.edge.start_all("help_needs_manual_recharge")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    try:
        run_until(stack, lambda: stack.attempts() >= 1, max_rounds=10)
    except RuntimeError as crash:  # the receiver double raises SenderCrashed (a RuntimeError)
        assert "process died" in str(crash)
    stack.crash_service()
    notification = stack.notifications()[0]
    assert notification.state == "ATTEMPTING" and notification.receipt_id is None  # never reported as delivered
    assert stack.receiver.unique_received() == 1  # the receiver did persist it
    stack.start_service()
    run_until(stack, lambda: stack.view().notifications[notification.notification_id].state == DELIVERED, max_rounds=20)
    live = stack.view().notifications[notification.notification_id]
    assert live.attempts == 2 and live.receipt_id == stack.receiver.ledger.receipts[notification.notification_id]
    assert stack.receiver.unique_received() == 1 and stack.receiver.duplicate_attempts() == 1


def test_result_persistence_failure_fail_stops_without_undoing_the_receiver_record(stack: InterventionHarness) -> None:
    """A send that already reached the receiver remains evidence when the local result append fails."""

    from scripts.edge_intervention_service_v0 import InterventionService, ServiceFailStop

    class FailSixthAppend:
        def __init__(self, journal) -> None:
            self.journal = journal
            self.calls = 0

        def append_via(self, builder):
            self.calls += 1
            if self.calls == 6:
                raise OSError("simulated durable result write failure")
            return self.journal.append_via(builder)

    stack.edge.start_all("help_needs_manual_recharge")
    stack.edge.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    journal = FailSixthAppend(stack.journal())
    service = InterventionService(
        stack.config,
        stack.edge.config,
        journal,
        stack.edge.edge_journal(),
        stack.receiver,
        clock=stack.clock,
        emit=stack.events.append,
    )
    stack.service = service
    service.start()

    with pytest.raises(ServiceFailStop) as caught:
        service.tick()

    assert isinstance(caught.value.__cause__, OSError)
    assert "simulated durable result write failure" in str(caught.value)
    notification = stack.notifications()[0]
    assert notification.state == "ATTEMPTING" and notification.attempts == 1
    assert stack.receiver.unique_received() == 1
    assert len(stack.receiver.deliveries) == 1
    assert "notification_result" not in stack.kinds()
    # Once failed, another tick cannot append or send anything else.
    records_before = [record.record_id for record in stack.records()]
    service.tick()
    assert [record.record_id for record in stack.records()] == records_before
    assert stack.receiver.unique_received() == 1 and len(stack.receiver.deliveries) == 1


def _persist_escalation_without_intent(stack: InterventionHarness):
    from nxt_edge_interventions import reconcile
    from scripts.edge_intervention_service_v0 import _to_spec, edge_snapshot

    stack.edge.start_all()
    stack.start_service()
    stack.step(2)
    stack.edge.crash_robot("picker-01")
    run_until(stack, lambda: len(stack.cases("DEVICE_UNREACHABLE")) == 1, max_rounds=40)
    case = stack.cases("DEVICE_UNREACHABLE")[0]
    assert case.severity == "WARNING"
    stack.crash_service()
    stack.step(10, edge=False, robots=False, service=False)
    stack.edge.create()
    specs = reconcile(stack.view(), edge_snapshot(stack.edge.config, stack.edge.edge_journal()), stack.clock())
    assert [s.record_kind for s in specs] == ["case_escalated", "notification_intended"]
    stack.journal().append(_to_spec(specs[0]))
    return case.case_id, specs[1]


def test_recovered_older_escalation_does_not_move_the_reminder_clock_backwards(stack: InterventionHarness) -> None:
    from datetime import timedelta
    from nxt_edge_interventions import decide_tick
    from nxt_edge_interventions.cases import attempt_spec, result_spec
    from nxt_edge_interventions.contracts import RESULT_DELIVERED, utc_text
    from scripts.edge_intervention_service_v0 import _to_spec

    case_id, expected = _persist_escalation_without_intent(stack)
    # Simulate the pre-fix service continuing after the torn escalation: it
    # failed to repair the intent, but kept recording bounded reminders.
    for _ in range(stack.config.max_reminders):
        stack.step(31, edge=False, robots=False, service=False)
        specs = [s for s in decide_tick(stack.view(), stack.clock()) if s.record_kind == "notification_intended"]
        assert [s.payload["intent"] for s in specs] == ["REMINDER"]
        record = stack.journal().append(_to_spec(specs[0]))
        notification_id = record.payload["notification_id"]
        notification = stack.view().notifications[notification_id]
        stack.journal().append(_to_spec(attempt_spec(notification, stack.clock())))
        notification = stack.view().notifications[notification_id]
        stack.journal().append(_to_spec(result_spec(notification, RESULT_DELIVERED, stack.clock(), receipt_id="historical-receipt", detail="receipt")))
    last_reminder_at = stack.view().cases[case_id].last_intent_at
    assert stack.view().cases[case_id].reminders_sent == 2
    stack.start_service()
    stack.step(5, edge=False, robots=False)
    escalation = next(n for n in stack.notifications(case_id) if n.intent == "ESCALATION")
    assert utc_text(escalation.intended_at) == expected.recorded_at_utc
    assert stack.view().cases[case_id].last_intent_at == last_reminder_at
    assert "reminders_exhausted" not in stack.kinds()
    stack.step(26, edge=False, robots=False)
    exhausted = [r for r in stack.records() if r.record_kind == "reminders_exhausted"]
    assert len(exhausted) == 1
    assert exhausted[0].recorded_at_utc == utc_text(last_reminder_at + timedelta(seconds=stack.config.reminder_interval_s))
    assert [n.intent for n in stack.notifications(case_id)] == ["OPENED", "REMINDER", "REMINDER", "ESCALATION"]


def test_pending_escalation_is_persisted_before_reminders_when_edge_snapshot_is_unreadable(stack: InterventionHarness, monkeypatch) -> None:
    import scripts.edge_intervention_service_v0 as service_module

    case_id, expected = _persist_escalation_without_intent(stack)
    # OPENED is 31 seconds old and would permit a reminder. ESCALATION is only
    # 21 seconds old, so recovery must move the reminder deadline forward first.
    stack.step(21, edge=False, robots=False, service=False)
    edge_before = stack.edge.edge_journal_path.read_bytes()
    stack.start_service()

    def unavailable(*_args):
        raise OSError("transient Edge journal read failure")

    with monkeypatch.context() as patch:
        patch.setattr(service_module, "edge_snapshot", unavailable)
        stack.step(3, edge=False, robots=False)
    assert any(e["event"] == "edge_snapshot_unavailable" for e in stack.events)
    notices = stack.notifications(case_id)
    assert [(n.intent, n.ordinal) for n in notices] == [("OPENED", 0), ("ESCALATION", 1)]
    assert notices[1].notification_id == expected.payload["notification_id"]
    assert notices[1].state == DELIVERED and stack.receiver.unique_received() == 2
    assert not stack.view().pending_intents
    # Once the snapshot is readable and the actual interval expires, the next
    # reminder follows the recovered escalation with a fresh ordinal.
    stack.step(7, edge=False, robots=False)
    notices = stack.notifications(case_id)
    assert [(n.intent, n.ordinal) for n in notices] == [("OPENED", 0), ("ESCALATION", 1), ("REMINDER", 2)]
    assert stack.receiver.unique_received() == 3
    assert stack.edge.edge_journal_path.read_bytes() == edge_before
