"""Closed-loop contract over the in-memory broker: normal flow, A, B3, D."""

from __future__ import annotations

import json

import pytest

from nxt_edge_task.contracts import request_topic
from tests.edge_task.conftest import EXPIRES, ISSUED, Harness, run_until


def _event_kind(payload: bytes) -> str | None:
    try:
        return json.loads(payload.decode("utf-8")).get("kind")
    except Exception:  # noqa: BLE001
        return None


def _is_request(topic: str) -> bool:
    return topic.endswith("/task/request")


def _succeeded(harness: Harness, task_id: str) -> bool:
    return harness.task(task_id)["state"] == "SUCCEEDED"


def test_normal_flow_contract(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    result = harness.create()
    assert result["status"] == "created"
    task_id = result["task_id"]
    run_until(harness, lambda: _succeeded(harness, task_id))

    task = harness.task(task_id)
    assert task["effective_result"] == "SUCCEEDED"
    assert task["result_verification"] == "robot_reported"
    assert task["missing_sequences"] == []
    assert task["acceptance_observed"] is True
    assert task["evidence_incomplete"] is False
    assert task["reconciliation_required"] is False

    # Picker executed exactly once; Carrier never decided or executed anything.
    assert harness.executions("picker-01", task_id) == 1
    picker_kinds = harness.kinds(harness.robot_records("picker-01"))
    assert picker_kinds.count("execution_completed") == 1
    carrier_kinds = harness.kinds(harness.robot_records("carrier-01"))
    assert carrier_kinds.count("execution_started") == 0
    assert carrier_kinds.count("task_decision") == 0

    # The Edge learned the result through the transport, not by reading files.
    received = [r for r in harness.edge_records() if r.record_kind == "task_event_received"]
    assert received and all(r.payload["transport"] == "inmemory" for r in received)
    assert [e[0] for e in harness.event_dispositions(task_id)] == ["ACCEPTED", "PROGRESS", "PROGRESS", "SUCCEEDED"]
    assert harness.publish_calls_for(task_id) == [(task_id, "initial", 1)]

    # Re-running the identical SIMULATION entry is idempotent on the Edge.
    again = harness.create()
    assert again["status"] == "idempotent" and again["task_id"] == task_id
    assert harness.kinds(harness.edge_records()).count("task_created") == 1


def test_duplicate_terminal_is_idempotent(harness: Harness) -> None:
    harness.broker.duplicate = lambda topic, payload: _event_kind(payload) == "SUCCEEDED"
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: _succeeded(harness, task_id))
    harness.step(3)
    dispositions = [d for d in harness.event_dispositions(task_id) if d[0] == "SUCCEEDED"]
    assert [d[3] for d in dispositions] == ["applied", "duplicate"]
    assert harness.executions("picker-01", task_id) == 1
    assert harness.publish_calls_for(task_id) == [(task_id, "initial", 1)]
    assert harness.task(task_id)["state"] == "SUCCEEDED"


def test_terminal_before_acceptance_preserves_gaps_without_resend(harness: Harness) -> None:
    """A1, branch 1: ACCEPTED and PROGRESS are lost forever; SUCCEEDED arrives alone."""

    harness.broker.drop = lambda topic, payload: _event_kind(payload) in {"ACCEPTED", "PROGRESS"}
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: _succeeded(harness, task_id))
    task = harness.task(task_id)
    assert task["state"] == "SUCCEEDED"
    assert task["missing_sequences"] == [1, 2, 3]
    assert task["acceptance_observed"] is False
    assert task["evidence_incomplete"] is True
    dispositions = harness.event_dispositions(task_id)
    assert dispositions == [("SUCCEEDED", 1, 4, "applied")]
    # No fabricated ACCEPTED, no human-case trigger, no resend for history.
    calls_before = list(harness.publish_calls_for(task_id))
    harness.step(40)
    assert harness.publish_calls_for(task_id) == calls_before == [(task_id, "initial", 1)]
    assert harness.task(task_id)["missing_sequences"] == [1, 2, 3]
    assert harness.executions("picker-01", task_id) == 1
    assert harness.kinds(harness.robot_records("picker-01")).count("task_decision") == 1


def test_naturally_late_history_fills_gaps_without_resend_or_state_regression(harness: Harness) -> None:
    """A1, branch 2: ACCEPTED and PROGRESS are held at the broker and released late."""

    harness.broker.hold = lambda topic, payload: _event_kind(payload) in {"ACCEPTED", "PROGRESS"}
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: _succeeded(harness, task_id))
    assert harness.task(task_id)["missing_sequences"] == [1, 2, 3]
    harness.broker.hold = None
    harness.broker.release_held()
    harness.step(2)
    task = harness.task(task_id)
    assert task["state"] == "SUCCEEDED"
    assert task["missing_sequences"] == []
    assert task["acceptance_observed"] is True
    assert task["evidence_incomplete"] is False
    late = [d for d in harness.event_dispositions(task_id) if d[3] == "late_evidence"]
    assert sorted(d[2] for d in late) == [1, 2, 3]
    assert harness.publish_calls_for(task_id) == [(task_id, "initial", 1)]
    assert harness.executions("picker-01", task_id) == 1


def test_late_legitimate_terminal_is_applied_after_resend(harness: Harness) -> None:
    """B3: the terminal is lost; the Edge restart re-sends; the robot replays history."""

    harness.broker.drop = lambda topic, payload: _event_kind(payload) == "SUCCEEDED"
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "RUNNING")
    harness.step(4)
    assert harness.task(task_id)["state"] == "RUNNING"
    harness.broker.drop = None
    harness.crash_edge()
    harness.start_edge()
    harness.step(4)
    task = harness.task(task_id)
    assert task["state"] == "SUCCEEDED"
    assert task["effective_result"] == "SUCCEEDED"
    replayed = [c for c in harness.publish_calls_for(task_id) if c[1].startswith("reconcile:")]
    assert len(replayed) == 1
    assert harness.executions("picker-01", task_id) == 1
    assert harness.kinds(harness.robot_records("picker-01")).count("task_decision") == 1


def test_resend_to_robot_that_never_received_request_executes_once_within_validity(harness: Harness) -> None:
    """D1: the first publication is lost at the broker; the acceptance window triggers one re-send."""

    dropped = {"count": 0}

    def drop_first_request(topic: str, payload: bytes) -> bool:
        if _is_request(topic) and dropped["count"] == 0:
            dropped["count"] += 1
            return True
        return False

    harness.broker.drop = drop_first_request
    harness.start_all()
    harness.step(2)
    task_id = harness.create(progress_window_s=5)["task_id"]
    harness.step(2)
    assert harness.task(task_id)["state"] == "CREATED"
    assert harness.kinds(harness.robot_records("picker-01")).count("request_received") == 0
    run_until(harness, lambda: _succeeded(harness, task_id), max_rounds=40)
    calls = harness.publish_calls_for(task_id)
    assert [c[1] for c in calls] == ["initial", "reconcile:acceptance_window_elapsed"]
    assert harness.executions("picker-01", task_id) == 1
    assert harness.kinds(harness.robot_records("picker-01")).count("task_decision") == 1


def test_expired_unknown_task_is_rejected_and_never_executed(harness: Harness) -> None:
    """D3: robot never received the request; after expiry one re-send yields task_expired."""

    harness.broker.drop = lambda topic, payload: _is_request(topic) and harness.clock().isoformat() < "2026-09-12T08:09"
    harness.start_all()
    harness.step(2)
    task_id = harness.create(expires="2026-09-12T08:09:00.000000Z", progress_window_s=3600)["task_id"]
    harness.step(1)
    # Advance past expiry in coarse steps; keep heartbeats alive.
    for _ in range(40):
        harness.step(1, 15.0)
    harness.broker.drop = None
    harness.step(6)
    task = harness.task(task_id)
    assert task["state"] == "REJECTED"
    assert task["expired_unconfirmed"] is True
    assert task["first_terminal"]["reason_code"] == "task_expired"
    calls = harness.publish_calls_for(task_id)
    assert [c[1] for c in calls][:1] == ["initial"]
    assert sum(1 for c in calls if c[1].startswith("reconcile:")) == 1
    assert harness.executions("picker-01", task_id) == 0
    harness.step(10, 15.0)
    assert len(harness.publish_calls_for(task_id)) == len(calls)


def test_terminal_and_blocked_tasks_are_not_republished(harness: Harness) -> None:
    """D4 (PR A): terminal and blocked tasks receive no further request publications."""

    harness.start_all("help_needs_manual_recharge")
    harness.step(2)
    task_id = harness.create(progress_window_s=3)["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    calls = list(harness.publish_calls_for(task_id))
    harness.step(30)
    assert harness.publish_calls_for(task_id) == calls == [(task_id, "initial", 1)]
    assert harness.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN"
    assert harness.device("picker-01")["last_reported_availability"] == "awaiting_human"
    # A second task for the blocked robot is refused at the entry, never sent.
    second = harness.create(issued="2026-09-12T08:05:00.000000Z")
    assert second["status"] == "rejected" and second["code"] == "robot_has_active_task"
    # An Edge restart flags the open task but a BLOCKED task is never republished.
    harness.crash_edge()
    harness.start_edge()
    harness.step(6)
    task = harness.task(task_id)
    assert "edge_restart" in task["reconciliation_reasons"]
    assert task["state"] == "BLOCKED_AWAITING_HUMAN"
    assert harness.edge.publish_calls == []
    assert harness.executions("picker-01", task_id) == 1
    assert harness.kinds(harness.robot_records("picker-01")).count("task_decision") == 1


def test_terminal_applied_during_hop1_wait_is_not_republished(harness: Harness) -> None:
    """A terminal delivered while an earlier candidate's hop-1 wait pumps the loop cancels that candidate."""

    from scripts.edge_task_transport import Delivery

    captured: dict[str, tuple[str, bytes]] = {}

    def drop_events(topic: str, payload: bytes) -> bool:
        if topic.endswith("/task/event"):
            if _event_kind(payload) == "REJECTED":
                captured["rejected"] = (topic, payload)
            return True
        return False

    harness.broker.drop = drop_events
    harness.start_all()
    harness.step(2)
    picker_task = harness.create()["task_id"]
    carrier_task = harness.create(robot_id="carrier-01")["task_id"]
    harness.step(3)
    assert "rejected" in captured
    assert harness.task(carrier_task)["state"] == "CREATED"
    harness.crash_edge()
    harness.start_edge()
    harness.step(2, edge=False)
    harness.clock.advance(harness.config.min_republish_interval_s + 1)
    edge = harness.edge
    assert edge is not None
    real_publish = edge.client.publish

    class _DeliveringHandle:
        def __init__(self, inner):
            self._inner = inner

        def wait(self, timeout_s: float) -> bool:
            topic, payload = captured["rejected"]
            edge.on_message(Delivery(topic=topic, payload=payload, qos=1, retain=False, mid=4242))
            return self._inner.wait(timeout_s)

    def publish(topic: str, payload: bytes, qos: int):
        handle = real_publish(topic, payload, qos)
        return _DeliveringHandle(handle) if topic.endswith("/carrier-01/task/request") is False else handle

    edge.client.publish = publish  # type: ignore[method-assign]
    edge.tick()
    assert harness.task(carrier_task)["state"] == "REJECTED"
    assert harness.publish_calls_for(carrier_task) == []
    records = harness.edge_records()
    rejected_index = next(i for i, r in enumerate(records) if r.record_kind == "task_event_received" and r.payload["task_id"] == carrier_task)
    assert not any(r.record_kind in {"task_publish_attempted", "task_publish_confirmed"} and r.payload["task_id"] == carrier_task for r in records[rejected_index:])


def test_standby_double_with_supported_task_type_still_rejects_and_never_executes(tmp_path) -> None:
    """The carrier's refusal is the standby rule, not a config accident."""

    from tests.edge_task.conftest import FakeClock, Harness, load_config
    from scripts.edge_task_transport import InMemoryBroker
    from scripts.pilot_course_a_task_fixture import admission_facts, commissioned_site
    import json as _json
    from nxt_edge_task.contracts import EdgeTaskConfig
    from tests.edge_task.conftest import CONFIG_PATH

    payload = _json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for robot in payload["robots"]:
        if robot["robot_id"] == "carrier-01":
            robot["task_types"] = ["COLLECT_BALLS_ZONE"]
    config = EdgeTaskConfig.from_dict(payload)
    harness = Harness(root=tmp_path, config=config, clock=FakeClock(), broker=InMemoryBroker(), facts=admission_facts(commissioned_site()))
    harness.start_all()
    harness.step(2)
    task_id = harness.create(robot_id="carrier-01")["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "REJECTED")
    assert harness.task(task_id)["first_terminal"]["reason_code"] == "unsupported_task_type"
    assert harness.executions("carrier-01") == 0
    decisions = [r.payload["decision"] for r in harness.robot_records("carrier-01") if r.record_kind == "task_decision"]
    assert decisions == ["rejected"]
    status = harness.carrier.core.status_message(harness.carrier.view, harness.clock())
    assert status.task_types == ()
    assert harness.device("carrier-01")["last_reported_availability"] == "available"


def test_carrier_rejects_unsupported_task_and_never_executes(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    task_id = harness.create(robot_id="carrier-01")["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "REJECTED")
    task = harness.task(task_id)
    assert task["first_terminal"]["reason_code"] == "unsupported_task_type"
    assert harness.executions("carrier-01") == 0
    carrier_kinds = harness.kinds(harness.robot_records("carrier-01"))
    assert carrier_kinds.count("execution_started") == 0
    assert harness.device("carrier-01")["last_reported_availability"] == "available"


def test_restart_of_edge_and_picker_preserves_contract(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: _succeeded(harness, task_id))
    before = harness.task(task_id)
    harness.crash_edge()
    harness.start_edge()
    harness.step(3)
    after = harness.task(task_id)
    for key in ("state", "effective_result", "missing_sequences", "acceptance_observed", "first_terminal"):
        assert after[key] == before[key]
    assert harness.publish_calls_for(task_id) == []  # new process object: no republish of a terminal task
    harness.crash_robot("picker-01")
    harness.start_robot("picker-01")
    harness.step(3)
    assert harness.executions("picker-01", task_id) == 1
    assert harness.task(task_id)["state"] == "SUCCEEDED"
    assert harness.picker is not None and harness.picker.view.boot_sequence == 2


def test_forged_request_with_wrong_environment_is_rejected_at_sequence_zero(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "RUNNING")
    intruder = harness.broker.client("intruder", clean_session=True)
    intruder.set_handlers(on_message=lambda d: None, on_connect=lambda p: None, on_disconnect=lambda r: None)
    intruder.connect()
    forged = json.loads(harness.edge.core.view.tasks[task_id].request.canonical_bytes())
    forged["environment"]["simulation_env_id"] = "sim-other"
    intruder.publish(request_topic(harness.config.site_id, "picker-01"), json.dumps(forged).encode(), 1)
    harness.step(1)
    rejected = [r for r in harness.robot_records("picker-01") if r.record_kind == "request_rejected"]
    assert rejected and rejected[-1].payload["code"] == "task_id_content_conflict"
    run_until(harness, lambda: _succeeded(harness, task_id))
    assert harness.executions("picker-01", task_id) == 1
