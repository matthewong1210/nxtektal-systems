"""Acceptance 4-5: lost receipts retry under one id; crashes never lose the request for help or fake success."""

from __future__ import annotations

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
