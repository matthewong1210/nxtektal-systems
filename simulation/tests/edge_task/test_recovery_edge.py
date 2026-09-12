"""Edge-side crash injection: C1, C5, C6, broker restart."""

from __future__ import annotations

import json

import pytest

from tests.edge_task.conftest import Harness, run_until


class _Crash(Exception):
    pass


def _event_kind(payload: bytes) -> str | None:
    try:
        return json.loads(payload.decode("utf-8")).get("kind")
    except Exception:  # noqa: BLE001
        return None


def test_crash_after_task_created_before_publish_confirmed_republishes_same_bytes(harness: Harness) -> None:
    """C1: task_created is durable, the Edge dies before any publish confirmation."""

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    # Crash before the gateway ever ticks: nothing published.
    assert harness.kinds(harness.edge_records()).count("task_publish_confirmed") == 0
    harness.crash_edge()
    harness.start_edge()
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    calls = harness.publish_calls_for(task_id)
    assert calls == [(task_id, "initial", 1)]
    assert harness.kinds(harness.edge_records()).count("task_created") == 1
    assert harness.executions("picker-01", task_id) == 1


def test_publish_without_hop1_confirmation_is_retried_and_deduplicated_by_the_robot(harness: Harness) -> None:
    """C1 variant: the first publish reaches the robot but its PUBACK is lost."""

    withheld = {"count": 0}

    def withhold_first(topic: str, payload: bytes) -> bool:
        if topic.endswith("/task/request") and withheld["count"] == 0:
            withheld["count"] += 1
            return True
        return False

    harness.broker.withhold_puback = withhold_first
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    # The robot received the first delivery; only the publisher's confirmation was lost.
    # The attempt is journaled, the confirmation is not, and the terminal ends republishing.
    calls = harness.publish_calls_for(task_id)
    assert calls == [(task_id, "initial", 1)]
    kinds = harness.kinds(harness.edge_records())
    assert kinds.count("task_publish_attempted") == 1 and kinds.count("task_publish_confirmed") == 0
    received = [r.payload["disposition"] for r in harness.robot_records("picker-01") if r.record_kind == "request_received"]
    assert received == ["new"]
    assert harness.executions("picker-01", task_id) == 1
    harness.step(20)
    assert harness.publish_calls_for(task_id) == calls


def test_unconfirmed_initial_publish_is_bounded_by_interval_and_attempt_cap(harness: Harness) -> None:
    """A publisher that never sees hop-1 confirmation retries only under the configured bounds."""

    harness.broker.withhold_puback = lambda topic, payload: topic.endswith("/task/request")
    harness.start_all("silent_after_accept")
    harness.step(2)
    task_id = harness.create(progress_window_s=3600)["task_id"]
    harness.step(4)
    assert harness.publish_calls_for(task_id) == [(task_id, "initial", 1)]
    harness.step(60)
    calls = harness.publish_calls_for(task_id)
    assert 1 < len(calls) <= harness.config.max_republish_attempts
    assert all(c[1] == "initial" for c in calls)
    assert [c[2] for c in calls] == list(range(1, len(calls) + 1))
    kinds = harness.kinds(harness.edge_records())
    assert kinds.count("task_publish_attempted") == len(calls)
    assert kinds.count("task_publish_confirmed") == 0
    assert harness.executions("picker-01", task_id) == 1
    assert harness.kinds(harness.robot_records("picker-01")).count("task_decision") == 1


def test_torn_conflict_batch_restores_gate_from_evidence_record_on_restart(harness: Harness) -> None:
    """Only the first line of the conflict batch survives; the gate must still derive from it."""

    from tests.edge_task.test_cases import inject_event

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    inject_event(harness, task_id, "INCONCLUSIVE", 2, 1, reason="interrupted_execution_unknown_outcome")
    assert harness.task(task_id)["effective_result"] == "CONFLICT"
    records = harness.edge_records()
    kinds = [r.record_kind for r in records]
    evidence_index = max(i for i, r in enumerate(records) if r.record_kind == "task_event_received" and r.payload.get("conflict") is True)
    assert kinds[evidence_index + 1 :] == ["conflicting_terminal", "task_reconciliation_flagged"]
    # Tear the batch: keep the evidence line, drop its two companions.
    harness.truncate_edge_journal(evidence_index + 1)
    harness.crash_edge()
    harness.start_edge()
    task = harness.task(task_id)
    assert task["effective_result"] == "CONFLICT" and task["result_verification"] == "conflicting"
    assert "conflicting_terminal" in task["reconciliation_reasons"]
    blocked = harness.create(issued="2026-09-12T08:05:00.000000Z")
    assert blocked["code"] == "authorization_blocked"
    harness.step(5)
    assert harness.edge.publish_calls == []


def test_terminal_lost_with_broker_session_triggers_single_resend_and_replay(harness: Harness) -> None:
    """C5 second branch: the terminal is lost with the broker; one re-send makes the robot replay."""

    harness.start_all("silent_after_accept")
    harness.step(2)
    task_id = harness.create(progress_window_s=3600)["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "ACCEPTED")
    harness.broker.restart()
    for device in (harness.edge, harness.picker, harness.carrier):
        assert device is not None
        device.client.connect()
    harness.step(8)
    resends = [c for c in harness.publish_calls_for(task_id) if c[1].startswith("reconcile:")]
    assert len(resends) == 1 and "transport_session_lost" in resends[0][1]
    received = [r.payload["disposition"] for r in harness.robot_records("picker-01") if r.record_kind == "request_received"]
    assert received == ["new", "known_duplicate"]
    dispositions = [d for d in harness.event_dispositions(task_id) if d[0] == "ACCEPTED"]
    assert [d[3] for d in dispositions] == ["applied", "duplicate"]
    assert harness.executions("picker-01", task_id) == 1
    assert harness.task(task_id)["state"] == "ACCEPTED"


def test_crash_before_journaling_result_recovers_by_redelivery_or_resend(harness: Harness) -> None:
    """C5: the Edge receives SUCCEEDED and dies before the journal append (no PUBACK)."""

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "RUNNING")
    edge = harness.edge
    assert edge is not None
    original_on_message = edge.on_message

    def dying(delivery):
        # Death exactly on the terminal delivery, before any journal append or PUBACK.
        if _event_kind(delivery.payload) == "SUCCEEDED":
            raise _Crash("edge died before journaling the terminal")
        original_on_message(delivery)

    edge.client.set_handlers(on_message=dying, on_connect=edge.on_connect, on_disconnect=edge.on_disconnect)
    with pytest.raises(_Crash):
        run_until(harness, lambda: False, max_rounds=8)
    session = harness.broker.sessions[harness.config.edge_client_id]
    inflight = list(session.inflight.values())
    assert [_event_kind(d.payload) for d in inflight] == ["SUCCEEDED"]
    assert harness.task(task_id)["state"] == "RUNNING"
    assert all(d[0] != "SUCCEEDED" for d in harness.event_dispositions(task_id))
    harness.crash_edge()
    # Persistent session: the unacknowledged QoS 1 delivery is redelivered on reconnect.
    harness.start_edge()
    harness.step(3)
    assert harness.task(task_id)["state"] == "SUCCEEDED"
    assert [d for d in harness.event_dispositions(task_id) if d[0] == "SUCCEEDED"] == [("SUCCEEDED", 1, 4, "applied")]
    assert harness.task(task_id)["missing_sequences"] == []
    assert harness.edge.publish_calls == []  # recovered by redelivery, not by re-send
    assert harness.executions("picker-01", task_id) == 1


def test_crash_after_journal_before_puback_yields_single_duplicate_record(harness: Harness) -> None:
    """C6: the append is durable, the Edge dies before sending the manual PUBACK."""

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "RUNNING")
    edge = harness.edge
    assert edge is not None
    swallowed = {"count": 0}

    def no_ack(delivery):
        swallowed["count"] += 1

    edge.client.ack = no_ack  # type: ignore[method-assign]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    assert swallowed["count"] >= 1
    harness.crash_edge()
    harness.start_edge()
    harness.step(3)
    dispositions = harness.event_dispositions(task_id)
    terminal = [d for d in dispositions if d[0] == "SUCCEEDED"]
    assert [d[3] for d in terminal] == ["applied", "duplicate"]
    assert harness.task(task_id)["state"] == "SUCCEEDED"
    assert harness.executions("picker-01", task_id) == 1


def test_broker_restart_mid_task_converges_without_duplicate_execution(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "ACCEPTED")
    harness.broker.restart()
    # Both endpoints reconnect with session_present=False.
    for device in (harness.edge, harness.picker, harness.carrier):
        assert device is not None
        device.client.connect()
    harness.step(1)
    session = [r for r in harness.edge_records() if r.record_kind == "transport_session"]
    assert session[-1].payload["event"] == "session_lost"
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED", max_rounds=40)
    assert harness.executions("picker-01", task_id) == 1
    assert harness.task(task_id)["missing_sequences"] == []


def test_rolled_back_edge_journal_fail_stops_the_gateway_before_any_socket(harness: Harness) -> None:
    """The Edge's own journal below its anchor is state loss: no start, no connect."""

    from scripts.edge_task_gateway_v0 import GatewayFailStop

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    harness.crash_edge()
    harness._tear(harness.edge_journal_path, 3, keep_anchor=True)  # a valid three-record prefix, anchor intact
    with pytest.raises((GatewayFailStop, Exception)) as raised:
        harness.start_edge()
    assert "rolled back" in str(raised.value)
    assert any(e.get("event") == "fail_stop" for e in harness.events)
    assert harness.broker.sessions[harness.config.edge_client_id].connected is False
