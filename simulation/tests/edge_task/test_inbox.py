"""Human responses persist but never substitute for robot evidence."""

from __future__ import annotations

import pytest

from nxt_edge_task.cases import derive_edge_view
from nxt_edge_task.contracts import utc_text
from nxt_edge_task.journal import PreconditionFailed
from tests.edge_task.conftest import Harness
from tests.edge_task.test_cases import inject_event
from tests.edge_task.test_schedules import payload, service


def created_task(harness: Harness) -> str:
    harness.broker.drop = lambda topic, _payload: topic.endswith("/task/request")
    harness.start_all()
    harness.step(2)
    return harness.create()["task_id"]


def test_schedule_rejection_can_be_acknowledged_and_closed_without_changing_tasks(harness: Harness) -> None:
    api = service(harness)
    api.create(payload(harness, due_at_utc=utc_text(harness.clock())), harness.clock())
    api.tick(harness.clock())
    notice = api.snapshot(harness.clock())["notifications"][0]
    nid = notice["notification_id"]
    assert notice["status"] == "OPEN" and not notice["can_resolve"]
    with pytest.raises(PreconditionFailed, match="acknowledge first"):
        api.resolve(nid, "manager", "No dispatch needed", harness.clock())
    ack = api.acknowledge(nid, "manager", "I will inspect", harness.clock())
    assert ack["notification"]["status"] == "ACKNOWLEDGED"
    assert ack["notification"]["can_resolve"]
    before = len(harness.edge_records())
    assert api.acknowledge(nid, "manager", "I will inspect", harness.clock())["status"] == "idempotent"
    assert len(harness.edge_records()) == before
    done = api.resolve(nid, "manager", "No task was sent; schedule discarded", harness.clock())
    assert done["notification"]["status"] == "RESOLVED"
    assert service(harness).snapshot(harness.clock())["notifications"][0]["status"] == "RESOLVED"
    assert not derive_edge_view(harness.config, harness.edge_journal().read()).tasks


def test_active_assistance_cannot_be_resolved_and_ack_does_not_change_task(harness: Harness) -> None:
    task_id = created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "ASSISTANCE_REQUIRED", 1, 2, reason="needs_manual_recharge")
    api = service(harness)
    notice = api.snapshot(harness.clock())["notifications"][0]
    before = harness.task(task_id)
    api.acknowledge(notice["notification_id"], "manager", "Checking battery", harness.clock())
    with pytest.raises(PreconditionFailed) as raised:
        api.resolve(notice["notification_id"], "manager", "Looks good", harness.clock())
    assert raised.value.code == "resolution_blocked"
    assert harness.task(task_id) == before
    assert api.snapshot(harness.clock())["notifications"][0]["condition_active"]


def test_failed_task_with_complete_evidence_can_be_handled_without_altering_result(harness: Harness) -> None:
    task_id = created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "FAILED", 1, 2, reason="robot_faulted")
    api = service(harness)
    notice = api.snapshot(harness.clock())["notifications"][0]
    api.acknowledge(notice["notification_id"], "manager", "Inspected failed run", harness.clock())
    api.resolve(notice["notification_id"], "manager", "Failure recorded for maintenance", harness.clock())
    assert harness.task(task_id)["state"] == "FAILED"
    assert harness.task(task_id)["effective_result"] == "FAILED"


def test_inconclusive_outcome_cannot_be_resolved_by_a_human_note(harness: Harness) -> None:
    task_id = created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "INCONCLUSIVE", 1, 2, reason="interrupted_execution_unknown_outcome")
    api = service(harness)
    notice = api.snapshot(harness.clock())["notifications"][0]
    api.acknowledge(notice["notification_id"], "manager", "Investigating", harness.clock())
    with pytest.raises(PreconditionFailed):
        api.resolve(notice["notification_id"], "manager", "Assume done", harness.clock())
    assert harness.task(task_id)["state"] == "INCONCLUSIVE"


def test_conflict_gate_survives_inbox_responses(harness: Harness) -> None:
    task_id = created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "FAILED", 1, 2, reason="robot_faulted")
    inject_event(harness, task_id, "SUCCEEDED", 1, 3)
    api = service(harness)
    before = derive_edge_view(harness.config, harness.edge_journal().read()).authorization_block_reason("picker-01")
    assert before
    for notice in api.snapshot(harness.clock())["notifications"]:
        api.acknowledge(notice["notification_id"], "manager", "Investigating conflict", harness.clock())
        with pytest.raises(PreconditionFailed):
            api.resolve(notice["notification_id"], "manager", "Dismiss", harness.clock())
    after = derive_edge_view(harness.config, harness.edge_journal().read()).authorization_block_reason("picker-01")
    assert after == before


def test_duplicate_delivery_does_not_create_another_notification(harness: Harness) -> None:
    task_id = created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    event = inject_event(harness, task_id, "ASSISTANCE_REQUIRED", 1, 2, reason="needs_manual_recharge")
    api = service(harness)
    first = api.snapshot(harness.clock())["notifications"]
    harness.replay_event(event)
    assert api.snapshot(harness.clock())["notifications"] == first


def test_robot_refusal_can_be_handled_without_fabricating_acceptance(harness: Harness) -> None:
    task_id = created_task(harness)
    inject_event(harness, task_id, "REJECTED", 1, 1, reason="task_expired")
    api = service(harness)
    notice = api.snapshot(harness.clock())["notifications"][0]
    api.acknowledge(notice["notification_id"], "manager", "Reviewed refusal", harness.clock())
    api.resolve(notice["notification_id"], "manager", "No work performed; discarded", harness.clock())
    assert harness.task(task_id)["state"] == "REJECTED"
    assert not harness.task(task_id)["acceptance_observed"]


def test_request_level_refusal_keeps_its_reported_reason(harness: Harness) -> None:
    task_id = created_task(harness)
    inject_event(harness, task_id, "REJECTED", 1, 0, reason="unsupported_task_type")
    notice = service(harness).snapshot(harness.clock())["notifications"][0]
    assert notice["source_kind"] == "robot_request_rejected"
    assert notice["reason_code"] == "unsupported_task_type"
    assert notice["condition_active"]


def test_success_without_history_stays_unverified_until_natural_late_evidence(harness: Harness) -> None:
    task_id = created_task(harness)
    inject_event(harness, task_id, "SUCCEEDED", 1, 3)
    api = service(harness)
    notice = api.snapshot(harness.clock())["notifications"][0]
    assert notice["reason_code"] == "result_evidence_incomplete"
    api.acknowledge(notice["notification_id"], "manager", "Waiting for robot history", harness.clock())
    with pytest.raises(PreconditionFailed):
        api.resolve(notice["notification_id"], "manager", "Assume the earlier steps happened", harness.clock())
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "PROGRESS", 1, 2)
    api.resolve(notice["notification_id"], "manager", "History arrived and is complete", harness.clock())
    assert harness.task(task_id)["state"] == "SUCCEEDED"


def test_offline_notice_requires_fresh_heartbeat_before_resolution(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    harness.step(2, seconds=harness.config.offline_after_s, robots=False)
    api = service(harness)
    notice = next(row for row in api.snapshot(harness.clock())["notifications"] if row["reason_code"] == "device_offline")
    api.acknowledge(notice["notification_id"], "manager", "Checking connection", harness.clock())
    with pytest.raises(PreconditionFailed):
        api.resolve(notice["notification_id"], "manager", "Should be online", harness.clock())
    harness.step(2)
    api.resolve(notice["notification_id"], "manager", "Fresh heartbeat is visible", harness.clock())


@pytest.mark.parametrize("operator,note", [("", "observed"), ("manager", ""), ("manager", " " * 3), ("manager", "a" * 513)])
def test_human_responses_require_named_operator_and_note(harness: Harness, operator: str, note: str) -> None:
    api = service(harness)
    api.create(payload(harness, due_at_utc=utc_text(harness.clock())), harness.clock())
    api.tick(harness.clock())
    notice = api.snapshot(harness.clock())["notifications"][0]
    before = len(harness.edge_records())
    with pytest.raises(PreconditionFailed):
        api.acknowledge(notice["notification_id"], operator, note, harness.clock())
    assert len(harness.edge_records()) == before


@pytest.mark.parametrize("bad_id", [None, [], {}, 42, ""])
def test_malformed_reference_is_a_validation_error(harness: Harness, bad_id) -> None:
    api = service(harness)
    with pytest.raises(PreconditionFailed):
        api.cancel(bad_id, "manager", harness.clock())
    with pytest.raises(PreconditionFailed):
        api.acknowledge(bad_id, "manager", "Reviewed", harness.clock())
