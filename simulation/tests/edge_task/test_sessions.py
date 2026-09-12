"""Device sessions, liveness, progress windows, and session regression (B4)."""

from __future__ import annotations

from tests.edge_task.conftest import Harness, run_until


def test_liveness_transitions_online_stale_offline(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    assert harness.device("picker-01")["connectivity"] == "ONLINE"
    # Robots stop heartbeating; the Edge keeps ticking on the injected clock.
    harness.step(7, robots=False)
    assert harness.device("picker-01")["connectivity"] == "STALE"
    harness.step(10, robots=False)
    device = harness.device("picker-01")
    assert device["connectivity"] == "OFFLINE"
    assert device["last_reported_availability"] == "available"
    assert device["last_valid_status_received_at_utc"] is not None
    transitions = [r.payload["to"] for r in harness.edge_records() if r.record_kind == "device_liveness_changed" and r.payload["robot_id"] == "picker-01"]
    assert transitions == ["ONLINE", "STALE", "OFFLINE"]
    # Heartbeats resume: ONLINE again with a fresh status.
    harness.step(2)
    assert harness.device("picker-01")["connectivity"] == "ONLINE"


def test_edge_restart_grace_marks_silent_devices_offline(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    harness.crash_edge()
    harness.start_edge()
    assert harness.device("picker-01")["connectivity"] == "UNKNOWN"
    harness.step(16, robots=False)
    device = harness.device("picker-01")
    assert device["connectivity"] == "OFFLINE"
    last = [r for r in harness.edge_records() if r.record_kind == "device_liveness_changed" and r.payload["robot_id"] == "picker-01"][-1]
    assert last.payload["reason"] == "no_status_since_edge_restart"
    assert last.payload["last_valid_status"] is not None


def test_edge_restart_with_live_heartbeats_returns_online_and_flags_open_tasks(harness: Harness) -> None:
    harness.broker.drop = lambda topic, payload: topic.endswith("/task/request")
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    harness.step(2)
    harness.crash_edge()
    harness.start_edge()
    harness.step(2)
    assert harness.device("picker-01")["connectivity"] == "ONLINE"
    task = harness.task(task_id)
    assert task["state"] == "CREATED"
    assert "edge_restart" in task["reconciliation_reasons"]


def test_boot_sequence_regression_blocks_resend_and_opens_conflict(harness: Harness) -> None:
    """B4: the robot's journal is wiped; it restarts at boot_sequence 1 after the Edge saw 2."""

    harness.start_all()
    harness.step(2)
    harness.crash_robot("picker-01")
    harness.start_robot("picker-01")
    harness.step(2)
    assert harness.device("picker-01")["boot_sequence"] == 2
    # Keep an open task on the robot's slot that the robot never receives.
    harness.broker.drop = lambda topic, payload: topic.endswith("/task/request")
    task_id = harness.create()["task_id"]
    harness.step(1)
    calls_before = list(harness.publish_calls_for(task_id))
    harness.crash_robot("picker-01")
    harness.robot_journal_path("picker-01").unlink()
    harness.start_robot("picker-01")
    harness.step(2)
    device = harness.device("picker-01")
    assert device["session_regression"] is True
    assert device["boot_sequence"] == 2  # the regressed session is not applied
    kinds = harness.kinds(harness.edge_records())
    assert kinds.count("session_regression") == 1
    task = harness.task(task_id)
    assert task["state"] == "CREATED" and "session_regression" in task["reconciliation_reasons"]
    blocked = harness.create(issued="2026-09-12T08:05:00.000000Z")
    assert blocked["code"] == "authorization_blocked"
    harness.broker.drop = None
    harness.step(30)
    assert harness.publish_calls_for(task_id) == calls_before
    assert harness.executions("picker-01") == 0


def test_events_from_regressed_session_are_evidence_not_applied(harness: Harness) -> None:
    """B4, event half: a wiped robot re-executes a queued request; its events never move the task."""

    harness.start_all()
    harness.step(2)
    harness.crash_robot("picker-01")
    harness.start_robot("picker-01")
    harness.step(2)
    assert harness.device("picker-01")["boot_sequence"] == 2
    # Robot goes down with its persistent session intact; the request queues in that session.
    harness.crash_robot("picker-01")
    task_id = harness.create()["task_id"]
    harness.step(1, robots=False)
    assert harness.publish_calls_for(task_id) == [(task_id, "initial", 1)]
    harness.robot_journal_path("picker-01").unlink()
    harness.start_robot("picker-01")
    harness.step(8)
    device = harness.device("picker-01")
    assert device["session_regression"] is True
    records = harness.edge_records()
    regression_index = next(i for i, r in enumerate(records) if r.record_kind == "session_regression")
    event_indices = [i for i, r in enumerate(records) if r.record_kind == "task_event_received" and r.payload["task_id"] == task_id]
    assert event_indices and all(i > regression_index for i in event_indices)
    dispositions = harness.event_dispositions(task_id)
    assert dispositions and all(d[3] == "evidence" and d[1] == 1 for d in dispositions)
    assert any(d[0] == "SUCCEEDED" for d in dispositions)  # the wiped robot really did execute again
    task = harness.task(task_id)
    assert task["state"] == "CREATED"
    assert task["effective_result"] is None and task["result_verification"] is None
    assert "session_regression" in task["reconciliation_reasons"]
    assert harness.edge.core.view.active_task_for("picker-01") is not None
    assert harness.publish_calls_for(task_id) == [(task_id, "initial", 1)]
    blocked = harness.create(issued="2026-09-12T08:05:00.000000Z")
    assert blocked["code"] == "authorization_blocked"


def test_offline_device_gets_no_republication(harness: Harness) -> None:
    """D4: a CREATED task for an OFFLINE device is not re-sent even when its window elapses."""

    harness.broker.drop = lambda topic, payload: topic.endswith("/task/request")
    harness.start_all()
    harness.step(2)
    # The acceptance window elapses only after the device has gone OFFLINE (15 s).
    task_id = harness.create(progress_window_s=30)["task_id"]
    harness.step(1)
    calls = list(harness.publish_calls_for(task_id))
    harness.step(45, robots=False)
    assert harness.device("picker-01")["connectivity"] == "OFFLINE"
    assert harness.publish_calls_for(task_id) == calls == [(task_id, "initial", 1)]
    assert harness.task(task_id)["state"] == "CREATED"
    assert "acceptance_window_elapsed" not in harness.task(task_id)["reconciliation_reasons"]
    # Once heartbeats resume the window fires and bounded re-sends follow (the broker still drops them).
    harness.step(8)
    triggers = [c[1] for c in harness.publish_calls_for(task_id)]
    assert triggers[0] == "initial" and len(triggers) >= 2
    assert all(t == "reconcile:acceptance_window_elapsed" for t in triggers[1:])
    assert len(triggers) <= harness.config.max_republish_attempts


def test_progress_window_elapsed_flags_silent_robot_without_terminal(harness: Harness) -> None:
    harness.start_all("silent_after_accept")
    harness.step(2)
    task_id = harness.create(progress_window_s=4)["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "ACCEPTED")
    run_until(harness, lambda: any(r.record_kind == "task_reconciliation_flagged" and r.payload["reason"] == "progress_window_elapsed" for r in harness.edge_records()), max_rounds=20)
    harness.step(40)
    task = harness.task(task_id)
    assert task["state"] == "ACCEPTED"
    assert task["effective_result"] is None
    assert harness.executions("picker-01", task_id) == 1
    calls = harness.publish_calls_for(task_id)
    assert 1 < len(calls) <= harness.config.max_republish_attempts
    assert all(c[1] == "initial" or c[1].startswith("reconcile:") for c in calls)
    assert harness.kinds(harness.robot_records("picker-01")).count("task_decision") == 1
    assert harness.device("picker-01")["current_task"]["task_id"] == task_id


def test_stale_status_sequence_is_rejected_and_new_session_flags_open_task(harness: Harness) -> None:
    harness.broker.drop = lambda topic, payload: topic.endswith("/task/request")
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    harness.step(1)
    # Replay an old heartbeat: same boot, lower status_sequence.
    picker = harness.picker
    assert picker is not None
    old = picker.core.status_message(picker.view, harness.clock()).to_dict()
    old["status_sequence"] = 1
    import json

    from nxt_edge_task.contracts import status_topic

    intruder = harness.broker.client("intruder", clean_session=True)
    intruder.set_handlers(on_message=lambda d: None, on_connect=lambda p: None, on_disconnect=lambda r: None)
    intruder.connect()
    intruder.publish(status_topic(harness.config.site_id, "picker-01"), json.dumps(old).encode(), 0)
    harness.broker.pump()
    rejected = [r for r in harness.edge_records() if r.record_kind == "device_status_rejected"]
    assert rejected and rejected[-1].payload["code"] == "stale_status"
    harness.crash_robot("picker-01")
    harness.start_robot("picker-01")
    harness.step(2)
    assert "new_boot_session" in harness.task(task_id)["reconciliation_reasons"]
