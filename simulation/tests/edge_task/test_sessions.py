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
    """B4: the robot's journal is wiped; a re-provisioned robot restarts at boot_sequence 1 after the Edge saw 2."""

    import pytest

    from nxt_edge_task.contracts import EdgeTaskError, ErrorCode

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
    # A plain restart is refused: state loss is not a first boot.
    with pytest.raises(EdgeTaskError) as refused:
        harness.start_robot("picker-01")
    assert refused.value.code is ErrorCode.ROBOT_STATE_LOST
    # Only an explicit operator provisioning brings the identity back, as a new incarnation.
    harness.start_robot("picker-01", initialize=True)
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
    """B4, event half: a re-provisioned robot never inherits the queued request, and any event from
    the regressed session (here forged by the test, since the double no longer produces one) is evidence only."""

    from tests.edge_task.test_cases import inject_event

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
    picker_client_id = harness.config.robot("picker-01").client_id
    assert harness.broker.sessions[picker_client_id].queue
    harness.robot_journal_path("picker-01").unlink()
    harness.start_robot("picker-01", initialize=True)  # purges the session: the queued request is discarded
    harness.step(8)
    device = harness.device("picker-01")
    assert device["session_regression"] is True
    assert harness.executions("picker-01") == 0
    assert harness.kinds(harness.robot_records("picker-01")).count("request_received") == 0
    assert harness.event_dispositions(task_id) == []
    # Events claiming the regressed session are recorded as evidence and never move the task.
    inject_event(harness, task_id, "ACCEPTED", 1, 1)
    inject_event(harness, task_id, "SUCCEEDED", 1, 2)
    records = harness.edge_records()
    regression_index = next(i for i, r in enumerate(records) if r.record_kind == "session_regression")
    event_indices = [i for i, r in enumerate(records) if r.record_kind == "task_event_received" and r.payload["task_id"] == task_id]
    assert event_indices and all(i > regression_index for i in event_indices)
    dispositions = harness.event_dispositions(task_id)
    assert dispositions and all(d[3] == "evidence" and d[1] == 1 for d in dispositions)
    task = harness.task(task_id)
    assert task["state"] == "CREATED"
    assert task["effective_result"] is None and task["result_verification"] is None
    assert "session_regression" in task["reconciliation_reasons"]
    assert harness.edge.core.view.active_task_for("picker-01") is not None
    harness.step(30)
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


# ---------------------------------------------------------------------------
# Codex review round 1 regressions (B4 identity continuity, progress window)
# ---------------------------------------------------------------------------


def test_lost_robot_journal_is_not_a_first_boot_and_never_re_executes_a_queued_request(harness: Harness) -> None:
    """Codex R1 #1 (P1): a wiped journal is state loss, not provisioning; the queued request is never executed."""

    import pytest

    from nxt_edge_task.contracts import EdgeTaskError, ErrorCode, request_topic

    harness.start_all()
    harness.step(2)
    harness.crash_robot("picker-01")
    harness.start_robot("picker-01")
    harness.step(2)
    assert harness.device("picker-01")["boot_sequence"] == 2
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    assert harness.executions("picker-01", task_id) == 1
    # The robot goes down; the byte-identical original request is queued in its persistent session.
    harness.crash_robot("picker-01")
    picker_client_id = harness.config.robot("picker-01").client_id
    harness.intruder("resender").publish(request_topic(harness.config.site_id, "picker-01"), harness.request_bytes(task_id), 1)
    harness.broker.pump()
    assert harness.broker.sessions[picker_client_id].queue, "request must be queued for the offline robot"
    harness.robot_journal_path("picker-01").unlink()
    # Same identity, no journal, no explicit provisioning: refused before any request can be read.
    with pytest.raises(EdgeTaskError) as refused:
        harness.start_robot("picker-01")
    assert refused.value.code is ErrorCode.ROBOT_STATE_LOST
    assert not harness.robot_journal_path("picker-01").exists(), "no empty dedup store may be auto-established"
    assert harness.broker.sessions[picker_client_id].connected is False
    assert harness.broker.sessions[picker_client_id].queue, "the queued request was not consumed"
    harness.step(3)
    # Explicit re-provisioning is a new incarnation: the stale broker session is purged first.
    harness.start_robot("picker-01", initialize=True)
    harness.step(8)
    kinds = harness.kinds(harness.robot_records("picker-01"))
    assert kinds[0] == "robot_provisioned"
    assert kinds.count("request_received") == 0 and kinds.count("task_decision") == 0 and kinds.count("execution_started") == 0
    device = harness.device("picker-01")
    assert device["session_regression"] is True and device["boot_sequence"] == 2
    assert harness.create(issued="2026-09-12T08:05:00.000000Z")["code"] == "authorization_blocked"
    assert harness.publish_calls_for(task_id) == [(task_id, "initial", 1)]


def test_reprovisioned_robot_at_the_same_boot_number_is_a_session_regression(harness: Harness) -> None:
    """Codex R1 #1 corollary: a fresh incarnation that restarts its counter at the seen boot is still detected."""

    harness.start_all()
    harness.step(2)
    first = harness.device("picker-01")
    assert first["boot_sequence"] == 1
    harness.crash_robot("picker-01")
    harness.robot_journal_path("picker-01").unlink()
    harness.start_robot("picker-01", initialize=True)
    harness.step(3)
    device = harness.device("picker-01")
    assert device["boot_sequence"] == 1 and device["boot_id"] == first["boot_id"]  # the old session is kept
    assert device["session_regression"] is True
    regression = [r for r in harness.edge_records() if r.record_kind == "session_regression"]
    assert len(regression) == 1 and regression[0].payload["received_boot_id"] != first["boot_id"]
    assert harness.create(issued="2026-09-12T08:05:00.000000Z")["code"] == "authorization_blocked"


def test_duplicate_history_replays_do_not_reset_the_progress_window(harness: Harness) -> None:
    """Codex R1 #4 (P2): receipt time is not trusted progress time; duplicates never clear unverified progress."""

    harness.start_all("silent_after_accept")
    harness.step(2)
    task_id = harness.create(progress_window_s=20)["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "ACCEPTED")
    accepted = next(r.payload["event"] for r in harness.edge_records() if r.record_kind == "task_event_received" and r.payload["task_id"] == task_id)
    attempts_before = len(harness.publish_calls_for(task_id))
    for _ in range(6):
        harness.step(10)  # the robot keeps heartbeating (ONLINE); it never progresses
        harness.replay_event(accepted)  # the original ACCEPTED redelivered every 10 s
    flags = [r for r in harness.edge_records() if r.record_kind == "task_reconciliation_flagged" and r.payload["task_id"] == task_id and r.payload["reason"] == "progress_window_elapsed"]
    assert len(flags) >= 1, "60 s without new progress must be flagged despite duplicate receipts"
    from nxt_edge_task.contracts import parse_utc

    accepted_at = parse_utc(next(r for r in harness.edge_records() if r.record_kind == "task_event_received" and r.payload["task_id"] == task_id).recorded_at_utc, "at")
    assert (parse_utc(flags[0].recorded_at_utc, "at") - accepted_at).total_seconds() <= 21  # flagged at the first window, not later
    task = harness.task(task_id)
    assert task["state"] == "ACCEPTED"
    assert "progress_window_elapsed" in task["reconciliation_reasons"]
    dispositions = harness.event_dispositions(task_id)
    assert dispositions.count(("ACCEPTED", 1, 1, "duplicate")) >= 6
    assert all(d[3] in {"applied", "duplicate"} for d in dispositions)
    # Bounded reconciliation continues while the task is unresolved, but duplicates never accelerate it:
    # consecutive reconcile attempts are at least one progress window apart, and the total stays within the cap.
    attempted = [r for r in harness.edge_records() if r.record_kind == "task_publish_attempted" and r.payload["task_id"] == task_id and r.payload["trigger"].startswith("reconcile:")]
    assert attempted and len(attempted) + attempts_before <= harness.config.max_republish_attempts
    times = [parse_utc(r.recorded_at_utc, "at") for r in attempted]
    assert all((later - earlier).total_seconds() >= 20 for earlier, later in zip(times, times[1:]))
    assert harness.executions("picker-01", task_id) == 1


# ---------------------------------------------------------------------------
# Adversarial round on the identity-continuity fix (rollback, unobserved
# re-provisioning, events outrunning the first heartbeat)
# ---------------------------------------------------------------------------


def test_rolled_back_robot_journal_is_refused_even_though_the_prefix_is_valid(harness: Harness) -> None:
    """State loss that is not a deletion: only [robot_provisioned, robot_started(1)] survive, the anchor does not."""

    import pytest

    from nxt_edge_task.contracts import EdgeTaskError, ErrorCode, request_topic

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    harness.crash_robot("picker-01")
    harness.intruder("resender").publish(request_topic(harness.config.site_id, "picker-01"), harness.request_bytes(task_id), 1)
    harness.broker.pump()
    harness.rollback_robot_journal("picker-01", keep=2)
    with pytest.raises(EdgeTaskError) as refused:
        harness.start_robot("picker-01")
    assert refused.value.code is ErrorCode.ROBOT_STATE_LOST and "rolled back" in refused.value.detail
    with pytest.raises(EdgeTaskError) as still:
        harness.start_robot("picker-01", initialize=True)  # the journal is not empty: no silent re-provisioning either
    assert still.value.code is ErrorCode.ROBOT_STATE_LOST
    harness.step(3)
    assert harness.broker.sessions[harness.config.robot("picker-01").client_id].queue  # never consumed
    assert harness.executions("picker-01") == 0  # the surviving prefix holds no execution and none was added


def test_reprovisioned_incarnation_is_detected_even_after_its_counter_passes_the_seen_boot(harness: Harness) -> None:
    """Edge down; robot journal lost, re-provisioned, and restarted once more before the Edge returns."""

    import pytest

    from nxt_edge_task.contracts import EdgeTaskError, ErrorCode

    harness.start_all("silent_after_accept")
    harness.step(2)
    task_id = harness.create(progress_window_s=600)["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "ACCEPTED")
    seen = harness.device("picker-01")
    assert seen["boot_sequence"] == 1
    harness.crash_edge()
    harness.crash_robot("picker-01")
    harness.robot_journal_path("picker-01").unlink()
    with pytest.raises(EdgeTaskError) as refused:
        harness.start_robot("picker-01")
    assert refused.value.code is ErrorCode.ROBOT_STATE_LOST
    harness.start_robot("picker-01", "accept_and_succeed", initialize=True)  # boot 1 of the new incarnation, unobserved
    harness.step(3, edge=False)
    harness.crash_robot("picker-01")
    harness.start_robot("picker-01", "accept_and_succeed")  # boot 2 of the new incarnation
    harness.start_edge()
    harness.step(30)
    device = harness.device("picker-01")
    assert device["session_regression"] is True
    assert device["boot_sequence"] == 1 and device["boot_id"] == seen["boot_id"]  # the old session is never overwritten
    regression = next(r for r in harness.edge_records() if r.record_kind == "session_regression")
    assert regression.payload["received_boot_sequence"] == 2 and regression.payload["received_boot_id"] != seen["boot_id"]
    assert harness.publish_calls_for(task_id) == []  # the restarted Edge never resent T to the new incarnation
    assert sum(1 for r in harness.edge_records() if r.record_kind == "task_publish_attempted" and r.payload["task_id"] == task_id) == 1
    assert harness.executions("picker-01", task_id) == 0  # the new incarnation never executed T
    task = harness.task(task_id)
    assert task["state"] == "ACCEPTED" and "session_regression" in task["reconciliation_reasons"]
    assert harness.create(issued="2026-09-12T08:05:00.000000Z")["code"] == "authorization_blocked"


def test_events_of_a_new_incarnation_arriving_before_its_heartbeat_are_evidence(harness: Harness) -> None:
    """A resend reaches the fresh incarnation and its events outrun its status: nothing is applied."""

    harness.broker.drop = lambda topic, payload: topic.endswith("/task/request")
    harness.start_all()
    harness.step(2)
    task_id = harness.create(progress_window_s=4)["task_id"]
    harness.step(1)
    seen = harness.device("picker-01")
    harness.crash_robot("picker-01")
    harness.robot_journal_path("picker-01").unlink()
    harness.broker.drop = None
    harness.broker.hold = lambda topic, payload: topic.endswith("/status")  # every heartbeat of the new incarnation is delayed
    harness.start_robot("picker-01", initialize=True)
    harness.step(12)
    # The resend reached the new incarnation: it is rejected at sequence 0 (the authorization names the
    # dead incarnation), and that rejection alone reveals the new incarnation to the Edge.
    assert harness.executions("picker-01") == 0 and harness.kinds(harness.robot_records("picker-01")).count("task_decision") == 0
    rejections = [r for r in harness.edge_records() if r.record_kind == "robot_request_rejected" and r.payload["task_id"] == task_id]
    assert rejections and rejections[-1].payload["reason_code"] == "incarnation_mismatch"
    regression = [r for r in harness.edge_records() if r.record_kind == "session_regression"]
    assert len(regression) == 1 and regression[0].payload["source"] == "task_event"
    assert harness.device("picker-01")["boot_id"] == seen["boot_id"]
    assert harness.create(issued="2026-09-12T08:05:00.000000Z")["code"] == "authorization_blocked"
    # Task events claiming the new incarnation (forged here; the double never produces them) are evidence only.
    from tests.edge_task.test_cases import inject_event

    new_prefix = harness.picker.view.incarnation
    for kind, seq in (("ACCEPTED", 1), ("SUCCEEDED", 2)):
        inject_event(harness, task_id, kind, 1, seq, incarnation=new_prefix)
    assert harness.event_dispositions(task_id) and all(d[3] == "evidence" for d in harness.event_dispositions(task_id))
    assert harness.task(task_id)["state"] == "CREATED"
    harness.broker.release_held()
    harness.broker.hold = None
    harness.step(3)
    assert harness.kinds(harness.edge_records()).count("session_regression") == 1  # the later heartbeat adds nothing
    assert harness.task(task_id)["state"] == "CREATED"


def test_differing_terminal_from_a_regressed_session_marks_the_result_conflicting(harness: Harness) -> None:
    """Evidence from a regressed session never moves the task, but a differing terminal still makes the result unverifiable."""

    from tests.edge_task.test_cases import inject_event

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    harness.crash_robot("picker-01")
    harness.robot_journal_path("picker-01").unlink()
    harness.start_robot("picker-01", initialize=True)
    harness.step(3)
    assert harness.device("picker-01")["session_regression"] is True
    inject_event(harness, task_id, "FAILED", 1, 5, reason="cannot_continue")  # a fresh key under the old incarnation's prefix, as the Edge knows it
    task = harness.task(task_id)
    assert task["state"] == "SUCCEEDED"
    assert harness.event_dispositions(task_id)[-1] == ("FAILED", 1, 5, "evidence")
    assert task["effective_result"] == "CONFLICT" and task["result_verification"] == "conflicting"
    assert harness.kinds(harness.edge_records()).count("conflicting_terminal") == 1
    assert harness.create(issued="2026-09-12T08:05:00.000000Z")["code"] == "authorization_blocked"


# ---------------------------------------------------------------------------
# Codex review round 2 regressions
# ---------------------------------------------------------------------------


def test_in_flight_resend_released_after_reprovisioning_is_refused_by_the_new_incarnation(harness: Harness) -> None:
    """Codex R2 ① (P1): a resend already in flight when the identity is re-provisioned never executes on the new incarnation.

    Authorization is bound to the incarnation the Edge saw when it created the
    task; a byte-identical resend therefore names the dead incarnation and the
    new one rejects it at sequence 0.  Cumulative executions across both
    journals stay at one.
    """

    import pytest

    from nxt_edge_task.contracts import EdgeTaskError, ErrorCode

    harness.start_all("silent_after_accept")
    harness.step(2)
    task_id = harness.create(progress_window_s=4)["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "ACCEPTED")
    harness.step(2)
    old_executions = harness.executions("picker-01", task_id)
    assert old_executions == 1
    # Every request from now on stays in flight at the broker (the Edge's reconcile resend included).
    harness.broker.hold = lambda topic, payload: topic.endswith("/task/request")
    run_until(harness, lambda: any(c[1].startswith("reconcile:") for c in harness.publish_calls_for(task_id)), max_rounds=20)
    assert harness.broker.held, "the Edge's resend must be in flight"
    harness.crash_robot("picker-01")
    harness.robot_journal_path("picker-01").unlink()
    with pytest.raises(EdgeTaskError) as refused:
        harness.start_robot("picker-01")
    assert refused.value.code is ErrorCode.ROBOT_STATE_LOST
    harness.start_robot("picker-01", "accept_and_succeed", initialize=True)  # purge, new incarnation
    harness.step(3)
    assert harness.device("picker-01")["session_regression"] is True
    calls = list(harness.publish_calls_for(task_id))
    # The in-flight resend now reaches the new incarnation.
    harness.broker.hold = None
    harness.broker.release_held()
    harness.step(10)
    kinds = harness.kinds(harness.robot_records("picker-01"))
    assert kinds.count("task_decision") == 0 and kinds.count("execution_started") == 0
    rejected = [r for r in harness.robot_records("picker-01") if r.record_kind == "request_rejected"]
    assert rejected and rejected[-1].payload["code"] == "incarnation_mismatch" and rejected[-1].payload["task_id"] == task_id
    assert old_executions + harness.executions("picker-01", task_id) == 1
    edge_rejections = [r for r in harness.edge_records() if r.record_kind == "robot_request_rejected" and r.payload["task_id"] == task_id]
    assert edge_rejections and edge_rejections[-1].payload["reason_code"] == "incarnation_mismatch"
    task = harness.task(task_id)
    assert task["state"] == "ACCEPTED" and "session_regression" in task["reconciliation_reasons"]
    assert harness.publish_calls_for(task_id) == calls  # no further publication to the new incarnation
    assert harness.create(issued="2026-09-12T08:05:00.000000Z")["code"] == "authorization_blocked"


def _event_kind(payload: bytes) -> str | None:
    import json as _json

    try:
        return _json.loads(payload.decode("utf-8")).get("kind")
    except Exception:  # noqa: BLE001
        return None


def test_reconciliation_continues_after_partial_history_answers_until_the_result_arrives(harness: Harness) -> None:
    """Codex R2 ② (P2): replayed old history answers one probe; it never ends the bounded reconciliation of an unresolved task."""

    harness.broker.drop = lambda topic, payload: topic.endswith("/task/event") and _event_kind(payload) == "SUCCEEDED"
    harness.start_all()
    harness.step(2)
    task_id = harness.create(progress_window_s=20)["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "RUNNING")
    harness.step(6)  # the robot finishes; its SUCCEEDED is lost in transit
    assert harness.executions("picker-01", task_id) == 1
    assert harness.kinds(harness.robot_records("picker-01")).count("execution_completed") == 1
    assert harness.task(task_id)["state"] == "RUNNING"
    harness.step(30)
    from nxt_edge_task.cases import PROGRESS_REASONS
    from nxt_edge_task.contracts import parse_utc

    attempts = lambda: [r for r in harness.edge_records() if r.record_kind == "task_publish_attempted" and r.payload["task_id"] == task_id]  # noqa: E731
    probes = lambda: [r for r in harness.edge_records() if r.record_kind == "task_reconciliation_flagged" and r.payload["task_id"] == task_id and r.payload["reason"] in PROGRESS_REASONS]  # noqa: E731
    assert harness.task(task_id)["state"] == "RUNNING"
    assert set(harness.task(task_id)["reconciliation_reasons"]) & PROGRESS_REASONS
    assert len(probes()) >= 2, "probing must be re-armed after the replayed history answered it"
    assert 3 <= len(attempts()) <= harness.config.max_republish_attempts
    # Never faster than one probe per progress window, whichever progress-type reason fires.
    times = [parse_utc(r.recorded_at_utc, "at") for r in probes()]
    assert all((later - earlier).total_seconds() >= 20 for earlier, later in zip(times, times[1:]))
    assert all(d[3] in {"applied", "duplicate"} for d in harness.event_dispositions(task_id))
    # Delivery is restored: the next bounded probe recovers the result the robot has held all along.
    harness.broker.drop = None
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED", max_rounds=60)
    assert harness.executions("picker-01", task_id) == 1
    assert harness.kinds(harness.robot_records("picker-01")).count("task_decision") == 1
    assert len(attempts()) <= harness.config.max_republish_attempts
    assert not (set(harness.task(task_id)["reconciliation_reasons"]) & PROGRESS_REASONS)


def test_reconciliation_stays_bounded_when_the_result_never_arrives(harness: Harness) -> None:
    """The re-armed probing is still capped: after max_republish_attempts the task stays visibly unresolved, nothing else is sent."""

    harness.broker.drop = lambda topic, payload: topic.endswith("/task/event") and _event_kind(payload) == "SUCCEEDED"
    harness.start_all()
    harness.step(2)
    task_id = harness.create(progress_window_s=20)["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "RUNNING")
    harness.step(200)
    attempts = [r for r in harness.edge_records() if r.record_kind == "task_publish_attempted" and r.payload["task_id"] == task_id]
    assert len(attempts) == harness.config.max_republish_attempts
    task = harness.task(task_id)
    assert task["state"] == "RUNNING" and "progress_window_elapsed" in task["reconciliation_reasons"]
    assert harness.executions("picker-01", task_id) == 1
    assert harness.kinds(harness.robot_records("picker-01")).count("task_decision") == 1


def test_reprovisioning_at_the_same_clock_instant_is_still_a_new_incarnation(harness: Harness) -> None:
    """The incarnation token carries a composition-root nonce, so the injected clock alone never reproduces an incarnation."""

    harness.start_all()
    harness.step(1)
    first = harness.picker.view.incarnation
    harness.crash_robot("picker-01")
    harness.robot_journal_path("picker-01").unlink()
    harness.start_robot("picker-01", initialize=True)  # same FakeClock instant as the previous provisioning would be if not advanced
    assert harness.picker.view.incarnation != first
    harness.crash_robot("picker-01")
    harness.robot_journal_path("picker-01").unlink()
    harness.start_robot("picker-01", initialize=True)
    third = harness.picker.view.incarnation
    assert len({first, harness.picker.view.incarnation, third}) == 2 and third != first


def test_a_continuous_duplicate_stream_cannot_postpone_reprobing(harness: Harness) -> None:
    """The re-arm waits one window after the evidence that answered the flag, not after the latest duplicate."""

    harness.broker.drop = lambda topic, payload: topic.endswith("/task/event") and _event_kind(payload) == "SUCCEEDED"
    harness.start_all()
    harness.step(2)
    task_id = harness.create(progress_window_s=20)["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "RUNNING")
    harness.step(6)
    progress = next(r.payload["event"] for r in harness.edge_records() if r.record_kind == "task_event_received" and r.payload["task_id"] == task_id and r.payload["event"]["kind"] == "PROGRESS")
    first_duplicate_at = None
    for _ in range(6):
        harness.step(10)
        harness.replay_event(progress)  # an unsolicited duplicate every 10 s, faster than the window
        first_duplicate_at = first_duplicate_at or harness.clock()
    attempts = [r for r in harness.edge_records() if r.record_kind == "task_publish_attempted" and r.payload["task_id"] == task_id]
    from nxt_edge_task.contracts import parse_utc

    after_first_duplicate = [a for a in attempts if a.payload["trigger"].startswith("reconcile:") and parse_utc(a.recorded_at_utc, "at") > first_duplicate_at]
    # Under a latest-evidence clock every duplicate would push the re-arm out by a full window and no
    # reconcile probe could ever follow the first duplicate; the answer clock keeps them coming, one per window.
    assert len(after_first_duplicate) >= 2, [(a.payload["trigger"], a.recorded_at_utc) for a in attempts]
    assert len(attempts) <= harness.config.max_republish_attempts
    harness.broker.drop = None
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED", max_rounds=60)
    assert harness.executions("picker-01", task_id) == 1
