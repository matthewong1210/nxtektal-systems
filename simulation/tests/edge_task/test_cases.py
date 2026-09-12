"""Edge transition table, late evidence, request-level rejections, terminal conflicts."""

from __future__ import annotations

import json
from datetime import timezone

import pytest

from nxt_edge_task.cases import TaskState
from nxt_edge_task.contracts import ENVIRONMENT_KIND_SIMULATION, EVENT_SCHEMA, event_topic, utc_text
from tests.edge_task.conftest import Harness, run_until


def _intruder(harness: Harness):
    client = harness.broker.client("intruder", clean_session=True)
    client.set_handlers(on_message=lambda d: None, on_connect=lambda p: None, on_disconnect=lambda r: None)
    client.connect()
    return client


def inject_event(harness: Harness, task_id: str, kind: str, boot: int, seq: int, *, reason: str | None = None, robot_id: str = "picker-01", phase: str | None = None) -> None:
    """Publish a crafted task event on the robot's topic (test-only injection)."""

    event = {
        "schema": EVENT_SCHEMA,
        "site_id": harness.config.site_id,
        "deployment_id": harness.config.deployment_id,
        "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": harness.config.simulation_env_id},
        "task_id": task_id,
        "robot_id": robot_id,
        "boot_id": f"{harness.known_incarnation(robot_id)}-{boot}",
        "boot_sequence": boot,
        "event_sequence": seq,
        "kind": kind,
        "reason_code": reason,
        "detail": "injected by test",
        "reported_at_utc": utc_text(harness.clock()),
        "progress": None if phase is None else {"phase": phase},
    }
    _intruder(harness).publish(event_topic(harness.config.site_id, robot_id), json.dumps(event).encode(), 1)
    harness.broker.pump()
    return event


def replay_event(harness: Harness, event: dict) -> None:
    """Redeliver the exact same bytes (a broker redelivery / robot history replay)."""

    _intruder(harness).publish(event_topic(harness.config.site_id, event["robot_id"]), json.dumps(event).encode(), 1)
    harness.broker.pump()


def _created_task(harness: Harness, *, drop_requests: bool = True) -> str:
    """A CREATED task the robot never sees, so the test controls every event."""

    if drop_requests:
        harness.broker.drop = lambda topic, payload: topic.endswith("/task/request")
    harness.start_all()
    harness.step(2)
    return harness.create()["task_id"]


# ---------------------------------------------------------------------------
# Transition table (every cell)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sequence, expected",
    [
        ([("ACCEPTED", 1, 1)], "ACCEPTED"),
        ([("ACCEPTED", 1, 1), ("PROGRESS", 1, 2)], "RUNNING"),
        ([("PROGRESS", 1, 2)], "RUNNING"),  # gap: CREATED -> RUNNING with missing 1
        ([("ACCEPTED", 1, 1), ("ASSISTANCE_REQUIRED", 1, 2)], "BLOCKED_AWAITING_HUMAN"),
        ([("ACCEPTED", 1, 1), ("ASSISTANCE_REQUIRED", 1, 2), ("PROGRESS", 1, 3)], "RUNNING"),
        ([("SUCCEEDED", 1, 4)], "SUCCEEDED"),
        ([("ACCEPTED", 1, 1), ("FAILED", 1, 2)], "FAILED"),
        ([("ACCEPTED", 1, 1), ("PROGRESS", 1, 2), ("INCONCLUSIVE", 2, 1)], "INCONCLUSIVE"),
        ([("REJECTED", 1, 1)], "REJECTED"),
    ],
)
def test_transition_table_cells(harness: Harness, sequence, expected) -> None:
    task_id = _created_task(harness)
    for kind, boot, seq in sequence:
        inject_event(harness, task_id, kind, boot, seq, reason="task_expired" if kind == "REJECTED" else None)
    assert harness.task(task_id)["state"] == expected


def test_gap_evidence_is_tracked_when_progress_arrives_first(harness: Harness) -> None:
    task_id = _created_task(harness)
    inject_event(harness, task_id, "PROGRESS", 1, 2)
    task = harness.task(task_id)
    assert task["state"] == "RUNNING" and task["missing_sequences"] == [1] and task["acceptance_observed"] is False
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    task = harness.task(task_id)
    assert task["state"] == "RUNNING" and task["missing_sequences"] == [] and task["acceptance_observed"] is True
    assert harness.event_dispositions(task_id)[-1][3] == "late_evidence"


def test_unexpected_acceptance_and_rejection_after_progress_do_not_transition(harness: Harness) -> None:
    task_id = _created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "PROGRESS", 1, 2)
    inject_event(harness, task_id, "ACCEPTED", 2, 1)  # a newer boot re-accepting: not a fresh acceptance
    inject_event(harness, task_id, "REJECTED", 2, 2, reason="robot_busy")
    task = harness.task(task_id)
    assert task["state"] == "RUNNING"
    dispositions = [d[3] for d in harness.event_dispositions(task_id)]
    assert dispositions[-2:] == ["unexpected_acceptance", "unexpected_rejection"]
    assert {"unexpected_acceptance", "unexpected_rejection"} <= set(task["reconciliation_reasons"])
    assert task["acceptance_observed"] is True  # the genuine (1,1) acceptance, not the anomalous (2,1)


def test_old_boot_progress_after_new_boot_terminal_is_late_evidence(harness: Harness) -> None:
    """B1: INCONCLUSIVE(b2,1) applied, then PROGRESS(b1,2) arrives late."""

    task_id = _created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "INCONCLUSIVE", 2, 1, reason="interrupted_execution_unknown_outcome")
    assert harness.task(task_id)["state"] == "INCONCLUSIVE"
    before = harness.task(task_id)
    inject_event(harness, task_id, "PROGRESS", 1, 2, phase="collecting")
    after = harness.task(task_id)
    assert after["state"] == "INCONCLUSIVE"
    assert after["effective_result"] == "INCONCLUSIVE" and after["result_verification"] == "robot_reported"
    assert after["first_terminal"] == before["first_terminal"]
    assert harness.event_dispositions(task_id)[-1] == ("PROGRESS", 1, 2, "late_evidence")
    assert after["reconciliation_required"] is False


def test_sequence_zero_rejection_never_transitions(harness: Harness) -> None:
    task_id = _created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "PROGRESS", 1, 2)
    inject_event(harness, task_id, "REJECTED", 1, 0, reason="task_id_content_conflict")
    task = harness.task(task_id)
    assert task["state"] == "RUNNING"
    kinds = harness.kinds(harness.edge_records())
    assert kinds.count("robot_request_rejected") == 1
    assert task["exceptions"][-1]["reason_code"] == "task_id_content_conflict"
    inject_event(harness, task_id, "SUCCEEDED", 1, 3)
    assert harness.task(task_id)["state"] == "SUCCEEDED"


def test_duplicate_and_conflicting_replay_dispositions(harness: Harness) -> None:
    task_id = _created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "PROGRESS", 1, 1, phase="x")  # same key, different content
    dispositions = [d[3] for d in harness.event_dispositions(task_id)]
    assert dispositions == ["applied", "duplicate", "conflicting_replay"]
    task = harness.task(task_id)
    assert task["state"] == "ACCEPTED"
    assert "conflicting_replay" in task["reconciliation_reasons"]


# ---------------------------------------------------------------------------
# Terminal conflicts and the authorization gate (B3′a / B3′b)
# ---------------------------------------------------------------------------


def test_inconclusive_then_success_exposes_conflict_and_blocks_authorization(harness: Harness) -> None:
    """B3′a: INCONCLUSIVE(b2,1) first, then SUCCEEDED(b1,3) late."""

    task_id = _created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "INCONCLUSIVE", 2, 1, reason="interrupted_execution_unknown_outcome")
    late_success = inject_event(harness, task_id, "SUCCEEDED", 1, 3)
    task = harness.task(task_id)
    assert task["state"] == "INCONCLUSIVE"  # displayed state stays the first terminal
    assert task["effective_result"] == "CONFLICT"
    assert task["result_verification"] == "conflicting"
    assert task["reconciliation_required"] is True and "conflicting_terminal" in task["reconciliation_reasons"]
    assert [t["kind"] for t in task["terminals"]] == ["INCONCLUSIVE", "SUCCEEDED"]
    kinds = harness.kinds(harness.edge_records())
    assert kinds.count("conflicting_terminal") == 1
    # Gate: no new authorization for the device, no republication.
    blocked = harness.create(issued="2026-09-12T08:05:00.000000Z")
    assert blocked["status"] == "rejected" and blocked["code"] == "authorization_blocked"
    calls = list(harness.publish_calls_for(task_id))
    harness.step(20)
    assert harness.publish_calls_for(task_id) == calls
    # The same conflicting terminal redelivered byte-for-byte is a duplicate, not a second conflict.
    replay_event(harness, late_success)
    assert harness.kinds(harness.edge_records()).count("conflicting_terminal") == 1
    assert harness.event_dispositions(task_id)[-1][3] == "duplicate"
    # Same key with different content is a conflicting replay, also not a second conflict record.
    inject_event(harness, task_id, "SUCCEEDED", 1, 3)
    assert harness.kinds(harness.edge_records()).count("conflicting_terminal") == 1
    assert harness.event_dispositions(task_id)[-1][3] == "conflicting_replay"


@pytest.mark.parametrize("late_kind", ["INCONCLUSIVE", "FAILED"])
def test_success_then_conflicting_terminal_blocks_future_authorization(harness: Harness, late_kind: str) -> None:
    """B3′b: SUCCEEDED(b1,4) first, then a conflicting terminal from a later boot."""

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    assert harness.executions("picker-01", task_id) == 1
    inject_event(harness, task_id, late_kind, 2, 1, reason="interrupted_execution_unknown_outcome" if late_kind == "INCONCLUSIVE" else "cannot_continue")
    task = harness.task(task_id)
    assert task["state"] == "SUCCEEDED"
    assert task["effective_result"] == "CONFLICT" and task["result_verification"] == "conflicting"
    assert task["first_terminal"]["kind"] == "SUCCEEDED"
    blocked = harness.create(issued="2026-09-12T08:05:00.000000Z")
    assert blocked["status"] == "rejected" and blocked["code"] == "authorization_blocked"
    calls_before = len(harness.edge.publish_calls)
    harness.step(20)
    assert len(harness.edge.publish_calls) == calls_before
    assert harness.executions("picker-01", task_id) == 1
    # Restart: the gate is restored from persisted evidence before any authorization.
    harness.crash_edge()
    harness.start_edge()
    harness.step(3)
    assert harness.task(task_id)["effective_result"] == "CONFLICT"
    again = harness.create(issued="2026-09-12T08:06:00.000000Z")
    assert again["code"] == "authorization_blocked"
    assert harness.edge.publish_calls == []


def test_conflict_preserves_prior_release_and_dispatch_facts(harness: Harness) -> None:
    """B3′b, released-and-dispatched branch: T succeeded, U was dispatched, then T conflicts."""

    harness.start_all()
    harness.step(2)
    task_t = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_t)["state"] == "SUCCEEDED")
    # Release happened: a second task U was authorized, accepted, and its execution started.
    task_u = harness.create(issued="2026-09-12T08:05:00.000000Z")["task_id"]
    run_until(harness, lambda: harness.executions("picker-01", task_u) == 1)
    u_calls_before = list(harness.publish_calls_for(task_u))
    records_before = harness.edge_records()
    u_created = [r for r in records_before if r.record_kind == "task_created" and r.payload["task_id"] == task_u]
    assert len(u_created) == 1
    # Conflict on T arrives after U was dispatched.
    inject_event(harness, task_t, "INCONCLUSIVE", 2, 1, reason="interrupted_execution_unknown_outcome")
    assert harness.task(task_t)["effective_result"] == "CONFLICT"
    # Prior facts are preserved verbatim; nothing is rewritten or cancelled.
    records_after = harness.edge_records()
    assert [r.to_dict() for r in records_after[: len(records_before)]] == [r.to_dict() for r in records_before]
    # No new authorization or publication after the conflict; U's own evidence still flows.
    harness.step(10)
    assert harness.publish_calls_for(task_u) == u_calls_before
    blocked = harness.create(issued="2026-09-12T08:07:00.000000Z")
    assert blocked["code"] == "authorization_blocked"
    assert harness.executions("picker-01", task_t) == 1
    assert harness.executions("picker-01", task_u) == 1  # U's count is untouched by the Edge
    assert harness.task(task_u)["state"] in {"RUNNING", "SUCCEEDED"}  # U's own evidence still applies


@pytest.mark.parametrize("prior_kind, prior_key, prior_reason", [("FAILED", (1, 2), "cannot_continue"), ("REJECTED", (1, 3), "robot_busy")])
def test_conflicting_terminal_detected_regardless_of_arrival_order(harness: Harness, prior_kind, prior_key, prior_reason) -> None:
    """A differing terminal already held as late/unexpected evidence conflicts with the applied one."""

    task_id = _created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "PROGRESS", 1, 3 if prior_kind == "FAILED" else 2)
    inject_event(harness, task_id, prior_kind, prior_key[0], prior_key[1], reason=prior_reason)
    task = harness.task(task_id)
    assert task["state"] == "RUNNING" and task["effective_result"] is None
    assert [t["kind"] for t in task["terminals"]] == [prior_kind]
    inject_event(harness, task_id, "SUCCEEDED", 1, 4)
    task = harness.task(task_id)
    assert task["state"] == "SUCCEEDED"
    assert task["effective_result"] == "CONFLICT" and task["result_verification"] == "conflicting"
    assert harness.kinds(harness.edge_records()).count("conflicting_terminal") == 1
    blocked = harness.create(issued="2026-09-12T08:05:00.000000Z")
    assert blocked["code"] == "authorization_blocked"


def test_post_terminal_acceptance_from_later_boot_does_not_complete_evidence(harness: Harness) -> None:
    task_id = _created_task(harness)
    inject_event(harness, task_id, "INCONCLUSIVE", 2, 1, reason="interrupted_execution_unknown_outcome")
    task = harness.task(task_id)
    assert task["acceptance_observed"] is False and task["evidence_incomplete"] is True
    inject_event(harness, task_id, "ACCEPTED", 3, 1)
    task = harness.task(task_id)
    assert harness.event_dispositions(task_id)[-1] == ("ACCEPTED", 3, 1, "evidence")
    assert "post_terminal_activity" in task["reconciliation_reasons"]
    assert task["acceptance_observed"] is False and task["evidence_incomplete"] is True
    # The genuine, earlier acceptance still counts when it arrives late.
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    task = harness.task(task_id)
    assert harness.event_dispositions(task_id)[-1] == ("ACCEPTED", 1, 1, "late_evidence")
    assert task["acceptance_observed"] is True


def test_task_state_enum_matches_effective_results() -> None:
    assert {s.value for s in TaskState} >= {"SUCCEEDED", "FAILED", "INCONCLUSIVE", "REJECTED"}


# ---------------------------------------------------------------------------
# Codex review round 1 regressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("conflicting_kind, reason", [("FAILED", "cannot_continue"), ("INCONCLUSIVE", "interrupted_execution_unknown_outcome")])
def test_same_key_conflicting_terminal_closes_the_authorization_gate(harness: Harness, conflicting_kind: str, reason: str) -> None:
    """Codex R1 #3 (P2): same (boot, seq) with a different terminal is a terminal conflict, not just a replay anomaly."""

    task_id = _created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "PROGRESS", 1, 2, phase="traveling")
    inject_event(harness, task_id, "SUCCEEDED", 1, 3)
    assert harness.task(task_id)["state"] == "SUCCEEDED"
    calls = list(harness.publish_calls_for(task_id))
    inject_event(harness, task_id, conflicting_kind, 1, 3, reason=reason)
    task = harness.task(task_id)
    assert task["state"] == "SUCCEEDED"  # the first accepted terminal stays the displayed state
    assert task["effective_result"] == "CONFLICT" and task["result_verification"] == "conflicting"
    assert "conflicting_terminal" in task["reconciliation_reasons"] and "conflicting_replay" in task["reconciliation_reasons"]
    assert [t["kind"] for t in task["terminals"]] == ["SUCCEEDED", conflicting_kind]  # both raw pieces of evidence kept
    assert harness.kinds(harness.edge_records()).count("conflicting_terminal") == 1
    last = harness.event_dispositions(task_id)[-1]
    assert last == (conflicting_kind, 1, 3, "conflicting_replay")
    blocked = harness.create(issued="2026-09-12T08:05:00.000000Z")
    assert blocked["code"] == "authorization_blocked"
    harness.step(30)
    assert harness.publish_calls_for(task_id) == calls
    # Replay == live: the gate is derived again from the journal after an Edge restart.
    harness.crash_edge()
    harness.start_edge()
    task = harness.task(task_id)
    assert task["effective_result"] == "CONFLICT" and harness.create(issued="2026-09-12T08:06:00.000000Z")["code"] == "authorization_blocked"


def test_same_key_conflict_against_a_late_evidence_terminal_is_kept_without_a_second_conflict_record(harness: Harness) -> None:
    """The applied INCONCLUSIVE already conflicts with a late SUCCEEDED; a same-key FAILED at the late key is a third raw result."""

    task_id = _created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 2, 1)
    inject_event(harness, task_id, "INCONCLUSIVE", 2, 2, reason="interrupted_execution_unknown_outcome")
    inject_event(harness, task_id, "SUCCEEDED", 1, 4)  # late evidence, differing terminal -> conflict already
    assert harness.task(task_id)["effective_result"] == "CONFLICT"
    before = harness.kinds(harness.edge_records()).count("conflicting_terminal")
    event = inject_event(harness, task_id, "FAILED", 1, 4, reason="cannot_continue")  # same key as the late SUCCEEDED
    task = harness.task(task_id)
    assert task["effective_result"] == "CONFLICT"
    assert harness.kinds(harness.edge_records()).count("conflicting_terminal") == before  # written once per task
    last = [r for r in harness.edge_records() if r.record_kind == "task_event_received" and r.payload["task_id"] == task_id][-1]
    assert last.payload["disposition"] == "conflicting_replay" and last.payload["conflict"] is True
    assert [t["kind"] for t in task["terminals"]] == ["INCONCLUSIVE", "SUCCEEDED", "FAILED"]  # every raw result kept
    # A redelivery of the same conflicting bytes is a duplicate, not a fourth result.
    replay_event(harness, event)
    assert harness.event_dispositions(task_id)[-1] == ("FAILED", 1, 4, "duplicate")
    assert [t["kind"] for t in harness.task(task_id)["terminals"]] == ["INCONCLUSIVE", "SUCCEEDED", "FAILED"]


def test_two_differing_late_terminals_on_an_open_task_conflict(harness: Harness) -> None:
    """Differing terminals below the applied maximum contradict each other as much as applied ones."""

    task_id = _created_task(harness)
    inject_event(harness, task_id, "ACCEPTED", 2, 1)
    inject_event(harness, task_id, "SUCCEEDED", 1, 4)
    assert harness.task(task_id)["effective_result"] is None  # one late terminal alone is just evidence
    inject_event(harness, task_id, "FAILED", 1, 3, reason="cannot_continue")
    task = harness.task(task_id)
    assert task["state"] == "ACCEPTED" and task["effective_result"] == "CONFLICT" and task["result_verification"] == "conflicting"
    assert harness.kinds(harness.edge_records()).count("conflicting_terminal") == 1
    assert harness.create(issued="2026-09-12T08:05:00.000000Z")["code"] == "authorization_blocked"


@pytest.mark.parametrize(
    "prelude, anomaly, reason",
    [
        ([("ACCEPTED", 1, 1), ("SUCCEEDED", 1, 2)], ("PROGRESS", 1, 3), "post_terminal_activity"),
        ([("ACCEPTED", 1, 1), ("PROGRESS", 1, 2)], ("ACCEPTED", 1, 3), "unexpected_acceptance"),
        ([("ACCEPTED", 1, 1), ("PROGRESS", 1, 2)], ("REJECTED", 1, 3), "unexpected_rejection"),
    ],
)
def test_evidence_conflicts_close_the_authorization_gate(harness: Harness, prelude, anomaly, reason) -> None:
    """A robot reporting activity the Edge's record cannot explain gets no new authorization until a human reconciles."""

    task_id = _created_task(harness)
    for kind, boot, seq in prelude:
        inject_event(harness, task_id, kind, boot, seq, phase="traveling" if kind == "PROGRESS" else None)
    assert harness.create(issued="2026-09-12T08:05:00.000000Z")["code"] in {None, "robot_has_active_task"}
    kind, boot, seq = anomaly
    inject_event(harness, task_id, kind, boot, seq, reason="robot_busy" if kind == "REJECTED" else None, phase="collecting" if kind == "PROGRESS" else None)
    task = harness.task(task_id)
    assert reason in task["reconciliation_reasons"]
    blocked = harness.create(issued="2026-09-12T08:06:00.000000Z")
    assert blocked["code"] == "authorization_blocked" and reason in (blocked["detail"] or "")
    calls = list(harness.publish_calls_for(task_id))
    harness.step(30)
    assert harness.publish_calls_for(task_id) == calls
    harness.crash_edge()
    harness.start_edge()
    assert harness.create(issued="2026-09-12T08:07:00.000000Z")["code"] == "authorization_blocked"
