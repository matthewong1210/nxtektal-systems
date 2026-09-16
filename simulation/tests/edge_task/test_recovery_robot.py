"""Robot-side crash injection: C2, C3, C4, B2, D2."""

from __future__ import annotations

import json

import pytest

from nxt_edge_task.contracts import request_topic
from tests.edge_task.conftest import Harness, run_until


def _event_kind(payload: bytes) -> str | None:
    try:
        return json.loads(payload.decode("utf-8")).get("kind")
    except Exception:  # noqa: BLE001
        return None


def _wait_crash(harness: Harness) -> None:
    run_until(harness, lambda: harness.picker is not None and harness.picker.core.exit_requested)


def test_crash_after_accept_persist_before_start_reports_failed_not_started(harness: Harness) -> None:
    """C2: task_decision(accepted) and ACCEPTED persisted, process dies before publishing them."""

    harness.start_all("crash_after_accept")
    harness.step(2)
    task_id = harness.create()["task_id"]
    _wait_crash(harness)
    assert harness.picker is not None and harness.picker.core.exit_reason == "crash_after_accept"
    assert harness.executions("picker-01", task_id) == 0
    # Persisted but never published: the Edge has seen nothing from the robot.
    unconfirmed = harness.picker.view.summary()["tasks"][task_id]["unconfirmed"]
    assert unconfirmed in ([(1, 1)], [[1, 1]])
    assert harness.picker.event_publishes == []
    harness.step(2)
    assert harness.task(task_id)["state"] == "CREATED"

    harness.crash_robot("picker-01")
    harness.start_robot("picker-01", "accept_and_succeed")
    harness.step(3)
    # Restart republishes the unconfirmed ACCEPTED(b1,1) then reports FAILED(b2,1).
    assert harness.picker.event_publishes == [(1, 1, "ACCEPTED"), (2, 1, "FAILED")]
    assert harness.event_dispositions(task_id) == [("ACCEPTED", 1, 1, "applied"), ("FAILED", 2, 1, "applied")]
    task = harness.task(task_id)
    assert task["state"] == "FAILED"
    assert task["first_terminal"]["reason_code"] == "not_started_after_restart"
    assert task["first_terminal"]["boot_sequence"] == 2
    assert task["acceptance_observed"] is True and task["missing_sequences"] == []
    assert harness.executions("picker-01", task_id) == 0
    assert harness.device("picker-01")["last_reported_availability"] == "awaiting_human"
    # The restarted robot refuses new work until an explicit local reset.
    second = harness.create(issued="2026-09-12T08:05:00.000000Z")
    assert second["status"] == "created"
    run_until(harness, lambda: harness.task(second["task_id"])["state"] == "REJECTED")
    assert harness.task(second["task_id"])["first_terminal"]["reason_code"] == "robot_faulted"
    assert harness.executions("picker-01") == 0


def test_crash_during_execution_reports_inconclusive_and_never_reruns(harness: Harness) -> None:
    """C3: execution_started persisted, process dies before completion."""

    harness.start_all("crash_after_execution_started")
    harness.step(2)
    task_id = harness.create()["task_id"]
    _wait_crash(harness)
    assert harness.executions("picker-01", task_id) == 1
    harness.step(2)
    assert harness.task(task_id)["state"] in {"ACCEPTED", "RUNNING"}

    harness.crash_robot("picker-01")
    harness.start_robot("picker-01", "accept_and_succeed")
    harness.step(3)
    task = harness.task(task_id)
    assert task["state"] == "INCONCLUSIVE"
    assert task["first_terminal"]["reason_code"] == "interrupted_execution_unknown_outcome"
    assert harness.executions("picker-01", task_id) == 1
    picker_kinds = harness.kinds(harness.robot_records("picker-01"))
    assert picker_kinds.count("execution_completed") == 0
    device = harness.device("picker-01")
    assert device["last_reported_availability"] == "awaiting_human"
    status = harness.picker.core.status_message(harness.picker.view, harness.clock())
    assert status.safe_return_confirmed is None and status.awaiting_human is True
    harness.step(20)
    assert harness.executions("picker-01", task_id) == 1


def test_crash_after_result_persisted_before_publish_republishes_original_boot_event(harness: Harness) -> None:
    """C4: SUCCEEDED persisted with execution_completed, process dies before publishing it."""

    harness.start_all("crash_after_result_persisted")
    harness.step(2)
    task_id = harness.create()["task_id"]
    _wait_crash(harness)
    picker_kinds = harness.kinds(harness.robot_records("picker-01"))
    assert picker_kinds.count("execution_completed") == 1
    unconfirmed = harness.picker.view.summary()["tasks"][task_id]["unconfirmed"]
    assert unconfirmed == [[1, 4]] or unconfirmed == [(1, 4)]
    harness.step(2)
    assert harness.task(task_id)["state"] == "RUNNING"

    harness.crash_robot("picker-01")
    harness.start_robot("picker-01", "accept_and_succeed")
    harness.step(3)
    task = harness.task(task_id)
    assert task["state"] == "SUCCEEDED"
    assert task["first_terminal"]["boot_sequence"] == 1 and task["first_terminal"]["event_sequence"] == 4
    assert harness.executions("picker-01", task_id) == 1
    assert harness.picker.view.boot_sequence == 2
    assert harness.device("picker-01")["last_reported_availability"] == "available"


def test_forged_same_id_request_is_rejected_at_sequence_zero_without_touching_task(harness: Harness) -> None:
    """B2: same task_id, different content, injected on the robot's request topic."""

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "RUNNING")
    intruder = harness.broker.client("intruder", clean_session=True)
    intruder.set_handlers(on_message=lambda d: None, on_connect=lambda p: None, on_disconnect=lambda r: None)
    intruder.connect()
    forged = json.loads(harness.edge.core.view.tasks[task_id].request.canonical_bytes())
    forged["parameters"]["zone_id"] = "Z9"
    intruder.publish(request_topic(harness.config.site_id, "picker-01"), json.dumps(forged).encode(), 1)
    harness.step(2)  # delivery on the first tick's pump, the queued seq-0 publish on the next tick
    picker_records = harness.robot_records("picker-01")
    rejected = [r for r in picker_records if r.record_kind == "request_rejected"]
    assert len(rejected) == 1 and rejected[0].payload["code"] == "task_id_content_conflict"
    seq0 = [r for r in picker_records if r.record_kind == "task_event_persisted" and r.payload["event"]["event_sequence"] == 0]
    assert len(seq0) == 1 and seq0[0].payload["event"]["reason_code"] == "task_id_content_conflict"
    # The forged request touched no task history: the accepted decision stands and no
    # task-level REJECTED was persisted for the legitimate task.
    picker_task = harness.picker.view.tasks[task_id]
    assert picker_task.decision == "accepted"
    assert all(e["kind"] != "REJECTED" for e in picker_task.events)
    edge_kinds = harness.kinds(harness.edge_records())
    assert edge_kinds.count("robot_request_rejected") == 1
    assert harness.task(task_id)["state"] in {"RUNNING", "SUCCEEDED"}
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    assert harness.executions("picker-01", task_id) == 1
    assert harness.kinds(harness.robot_records("picker-01")).count("task_decision") == 1


def _intruder(harness: Harness):
    client = harness.broker.client("intruder", clean_session=True)
    client.set_handlers(on_message=lambda d: None, on_connect=lambda p: None, on_disconnect=lambda r: None)
    client.connect()
    return client


def test_unknown_task_identity_rejection_is_acked_published_and_survives_restart(harness: Harness) -> None:
    """A self-consistent request addressed to another robot: seq-0 rejection, no task, robot restarts cleanly."""

    from nxt_edge_task.contracts import TaskRequest

    harness.start_all()
    harness.step(2)
    foreign = TaskRequest.build(
        site_id=harness.config.site_id,
        deployment_id=harness.config.deployment_id,
        simulation_env_id=harness.config.simulation_env_id,
        target_robot_id="carrier-01",
        target_incarnation=harness.known_incarnation("carrier-01"),
        task_type="COLLECT_BALLS_ZONE",
        zone_id="Z1",
        issued_at_utc="2026-09-12T08:00:00.000000Z",
        expires_at_utc="2026-09-12T08:10:00.000000Z",
        progress_window_s=20,
        issued_by="SIMULATION_TEST_ENTRY:test",
    )
    _intruder(harness).publish(request_topic(harness.config.site_id, "picker-01"), foreign.canonical_bytes(), 1)
    harness.step(2)
    records = harness.robot_records("picker-01")
    rejected = [r for r in records if r.record_kind == "request_rejected"]
    assert len(rejected) == 1 and rejected[0].payload["code"] == "target_mismatch"
    assert foreign.task_id not in harness.picker.view.tasks
    confirmed = [r for r in records if r.record_kind == "event_publish_confirmed" and r.payload["event_sequence"] == 0]
    assert len(confirmed) == 1 and confirmed[0].payload["record_sequence"] is not None
    assert (1, 0, "REJECTED") in harness.picker.event_publishes
    edge_rejected = [r for r in harness.edge_records() if r.record_kind == "task_event_rejected" and r.payload.get("code") == "unknown_task"]
    assert len(edge_rejected) == 1
    harness.crash_robot("picker-01")
    harness.start_robot("picker-01")
    assert harness.picker.view.boot_sequence == 2
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    assert harness.executions("picker-01", task_id) == 1


def test_repeated_forged_same_id_requests_each_publish_a_sequence_zero_rejection(harness: Harness) -> None:
    harness.start_all("silent_after_accept")
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "ACCEPTED")
    intruder = _intruder(harness)
    for zone in ("Z8", "Z9"):
        forged = json.loads(harness.edge.core.view.tasks[task_id].request.canonical_bytes())
        forged["parameters"]["zone_id"] = zone
        intruder.publish(request_topic(harness.config.site_id, "picker-01"), json.dumps(forged).encode(), 1)
        harness.step(2)
    assert harness.picker.event_publishes.count((1, 0, "REJECTED")) == 2
    assert harness.kinds(harness.edge_records()).count("robot_request_rejected") == 2
    assert harness.task(task_id)["state"] == "ACCEPTED"


def test_legit_resend_after_forged_same_id_request_replays_no_sequence_zero(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "RUNNING")
    intruder = _intruder(harness)
    forged = json.loads(harness.edge.core.view.tasks[task_id].request.canonical_bytes())
    forged["parameters"]["zone_id"] = "Z9"
    intruder.publish(request_topic(harness.config.site_id, "picker-01"), json.dumps(forged).encode(), 1)
    harness.step(2)
    assert harness.kinds(harness.edge_records()).count("robot_request_rejected") == 1
    before = len(harness.picker.event_publishes)
    intruder.publish(request_topic(harness.config.site_id, "picker-01"), harness.edge.core.view.tasks[task_id].request.canonical_bytes(), 1)
    harness.step(2)
    replayed = harness.picker.event_publishes[before:]
    assert replayed and all(item[1] >= 1 for item in replayed)
    assert harness.kinds(harness.edge_records()).count("robot_request_rejected") == 1
    assert len(harness.task(task_id)["exceptions"]) == 1
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    assert harness.executions("picker-01", task_id) == 1


def test_truncated_robot_journal_fails_stop_without_second_execution_started(harness: Harness) -> None:
    from nxt_edge_task.journal import JournalIntegrityError

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    harness.step(1, robots=False)  # request delivered: decision + ACCEPTED persisted, not yet published
    picker = harness.picker
    assert picker is not None
    assert picker.view.tasks[task_id].decision == "accepted"
    # The file loses its last record under a live process (the anchor still names it).
    path = harness.robot_journal_path("picker-01")
    lines = path.read_bytes().split(b"\n")[:-1]
    path.write_bytes(b"\n".join(lines[:-1]) + b"\n")
    with pytest.raises(JournalIntegrityError, match="rolled back"):
        picker.tick()
    assert picker.failure is not None
    assert harness.executions("picker-01", task_id) == 0
    harness.step(4)
    assert harness.executions("picker-01", task_id) == 0
    assert any(e.get("event") == "fail_stop" for e in harness.events)


def test_known_task_resend_replays_history_without_execution(harness: Harness) -> None:
    """D2: a duplicate identical request makes the robot replay history, never re-execute."""

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    resender = harness.broker.client("resender", clean_session=True)
    resender.set_handlers(on_message=lambda d: None, on_connect=lambda p: None, on_disconnect=lambda r: None)
    resender.connect()
    payload = harness.edge.core.view.tasks[task_id].request.canonical_bytes()
    resender.publish(request_topic(harness.config.site_id, "picker-01"), payload, 1)
    harness.step(2)
    received = [r for r in harness.robot_records("picker-01") if r.record_kind == "request_received"]
    assert [r.payload["disposition"] for r in received] == ["new", "known_duplicate"]
    assert harness.executions("picker-01", task_id) == 1
    replayed = [p for p in harness.picker.event_publishes]
    assert replayed.count((1, 4, "SUCCEEDED")) == 2  # original publish + replayed history
    duplicates = [d for d in harness.event_dispositions(task_id) if d[3] == "duplicate"]
    assert len(duplicates) == 4
    assert harness.task(task_id)["state"] == "SUCCEEDED"


# ---------------------------------------------------------------------------
# Codex review round 1 regressions: torn recovery batches (every complete-line boundary)
# ---------------------------------------------------------------------------


def _restart_picker(harness: Harness, behavior: str = "accept_and_succeed") -> None:
    harness.crash_robot("picker-01")
    harness.start_robot("picker-01", behavior)


@pytest.mark.parametrize("keep_from_batch", [0, 1, 2, 3])
def test_torn_restart_recovery_batch_still_protects_the_robot(harness: Harness, keep_from_batch: int) -> None:
    """Codex R1 #2 (P2): the human-handling protection is derived from the terminal evidence, not from a trailing record."""

    harness.start_all("crash_after_execution_started")
    harness.step(2)
    task_id = harness.create()["task_id"]
    _wait_crash(harness)
    before = len(harness.robot_records("picker-01"))
    _restart_picker(harness)  # appends the recovery batch (robot_started, INCONCLUSIVE, ...)
    batch = len(harness.robot_records("picker-01")) - before
    keep = before + min(keep_from_batch, batch)
    harness.crash_robot("picker-01")
    harness.truncate_robot_journal("picker-01", keep)
    harness.start_robot("picker-01")
    harness.step(4)
    picker = harness.picker
    assert picker is not None
    assert picker.view.awaiting_human is True and picker.view.availability.value == "awaiting_human"
    assert picker.view.current_task_id is None
    task = harness.task(task_id)
    assert task["state"] == "INCONCLUSIVE"
    assert harness.executions("picker-01", task_id) == 1
    second = harness.create(issued="2026-09-12T08:05:00.000000Z")
    assert second["status"] == "created"
    run_until(harness, lambda: harness.task(second["task_id"])["state"] == "REJECTED")
    assert harness.task(second["task_id"])["first_terminal"]["reason_code"] == "robot_faulted"
    assert harness.executions("picker-01") == 1
    assert harness.device("picker-01")["last_reported_availability"] == "awaiting_human"


@pytest.mark.parametrize("keep_from_batch, expected", [(1, "SUCCEEDED"), (2, "FAILED")])
def test_torn_request_batch_is_resolved_at_restart(harness: Harness, keep_from_batch: int, expected: str) -> None:
    """Codex R1 #2: a request persisted without its decision (or a decision without its event) is resolved deterministically."""

    harness.start_all()
    harness.step(2)
    before = len(harness.robot_records("picker-01"))
    task_id = harness.create()["task_id"]
    harness.step(1, robots=False)  # the Edge publishes; the robot journals request + decision + ACCEPTED in one batch
    batch = len(harness.robot_records("picker-01")) - before
    assert batch == 3
    harness.crash_robot("picker-01")
    harness.truncate_robot_journal("picker-01", before + keep_from_batch)
    harness.start_robot("picker-01")
    run_until(harness, lambda: harness.task(task_id)["state"] == expected, max_rounds=40)
    task = harness.task(task_id)
    assert task["state"] == expected
    decisions = [r for r in harness.robot_records("picker-01") if r.record_kind == "task_decision"]
    assert len(decisions) == 1
    if expected == "SUCCEEDED":
        assert harness.executions("picker-01", task_id) == 1 and task["acceptance_observed"] is True
    else:
        assert harness.executions("picker-01", task_id) == 0 and task["first_terminal"]["reason_code"] == "not_started_after_restart"
    # A duplicate request now replays a non-empty history instead of nothing.
    harness.intruder("resender").publish(request_topic(harness.config.site_id, "picker-01"), harness.request_bytes(task_id), 1)
    harness.step(2)
    assert any(d[3] == "duplicate" for d in harness.event_dispositions(task_id))


def test_torn_rejection_batch_publishes_the_rejection_at_restart(harness: Harness) -> None:
    """A persisted rejected decision whose REJECTED event was lost is completed at restart, never left silent."""

    harness.start_all()
    harness.step(2)
    before = len(harness.robot_records("carrier-01"))
    task_id = harness.create("carrier-01")["task_id"]
    harness.step(1, robots=False)
    assert len(harness.robot_records("carrier-01")) - before == 3
    harness.crash_robot("carrier-01")
    harness.truncate_robot_journal("carrier-01", before + 2)  # request_received + task_decision(rejected)
    harness.start_robot("carrier-01")
    run_until(harness, lambda: harness.task(task_id)["state"] == "REJECTED", max_rounds=20)
    assert harness.task(task_id)["first_terminal"]["reason_code"] == "unsupported_task_type"
    assert harness.executions("carrier-01") == 0
    assert harness.kinds(harness.robot_records("carrier-01")).count("task_decision") == 1


def test_torn_completion_batch_reports_the_persisted_outcome_at_restart(harness: Harness) -> None:
    """execution_completed persisted, terminal event lost: the outcome is reported, nothing re-executes."""

    harness.start_all()
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    records = harness.robot_records("picker-01")
    completed_index = next(i for i, r in enumerate(records) if r.record_kind == "execution_completed")
    harness.crash_robot("picker-01")
    harness.truncate_robot_journal("picker-01", completed_index + 1)
    harness.start_robot("picker-01")
    harness.step(4)
    picker = harness.picker
    assert picker is not None
    view = picker.view
    assert view.tasks[task_id].terminal_kind == "SUCCEEDED" and view.current_task_id is None
    assert view.availability.value == "available" and view.awaiting_human is False
    assert harness.executions("picker-01", task_id) == 1
    assert harness.task(task_id)["state"] == "SUCCEEDED"
    terminal_events = [(e["boot_sequence"], e["event_sequence"]) for e in view.tasks[task_id].events if e["kind"] == "SUCCEEDED"]
    assert terminal_events == [(2, 1)]  # reported from the new boot; the lost original is never forged


@pytest.mark.parametrize("keep_from_batch, expected_available, edge_terminal", [(0, False, "INCONCLUSIVE"), (1, False, "FAILED"), (2, False, "FAILED"), (3, True, "FAILED")])
def test_torn_operator_reset_batch_leaves_the_robot_protected(harness: Harness, keep_from_batch: int, expected_available: bool, edge_terminal: str) -> None:
    """simulate_reset persists the abandoned task's FAILED first and the OPERATOR record last; any tear keeps protection.

    The tear happens before anything from the batch is published (a torn write
    cannot lose records that were already fsynced and sent).
    """

    harness.start_all("help_needs_manual_recharge")
    harness.step(2)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    picker = harness.picker
    assert picker is not None and picker.view.awaiting_human and picker.view.needs_manual_recharge is True
    before = len(harness.robot_records("picker-01"))
    now = harness.clock()
    picker._append(lambda view: picker.core.simulate_reset(view, now))  # the batch is durable, nothing published yet
    kinds = harness.kinds(harness.robot_records("picker-01"))[before:]
    assert kinds == ["execution_completed", "task_event_persisted", "simulate_reset"]
    harness.crash_robot("picker-01")
    harness.truncate_robot_journal("picker-01", before + keep_from_batch)
    harness.start_robot("picker-01", "accept_and_succeed")
    harness.step(4)
    view = harness.picker.view
    assert (view.awaiting_human is False) is expected_available
    run_until(harness, lambda: harness.task(task_id)["state"] == edge_terminal)
    if not expected_available:
        assert view.needs_manual_recharge is True  # the energy fact survives every tear
        second = harness.create(issued="2026-09-12T08:05:00.000000Z")
        assert second["status"] == "created", second
        run_until(harness, lambda: harness.task(second["task_id"])["state"] == "REJECTED")
        assert harness.task(second["task_id"])["first_terminal"]["reason_code"] == "energy_insufficient"
    assert harness.executions("picker-01") == 1


def test_initialize_on_a_provisioned_identity_is_refused_without_writing(harness: Harness) -> None:
    from nxt_edge_task.contracts import EdgeTaskError, ErrorCode

    harness.start_all()
    harness.step(2)
    harness.crash_robot("picker-01")
    path = harness.robot_journal_path("picker-01")
    before = (path.read_bytes(), path.with_name(path.name + ".hwm").read_bytes())
    with pytest.raises(EdgeTaskError) as refused:
        harness.start_robot("picker-01", initialize=True)
    assert refused.value.code is ErrorCode.ROBOT_ALREADY_PROVISIONED
    assert (path.read_bytes(), path.with_name(path.name + ".hwm").read_bytes()) == before
    assert harness.broker.sessions[harness.config.robot("picker-01").client_id].connected is False


def test_purge_failure_during_provisioning_writes_nothing(harness: Harness) -> None:
    from scripts.mock_robot_task_device import MockRobotDevice

    robot = harness.config.robot("picker-01")

    def failing_purge() -> None:
        raise RuntimeError("broker unreachable")

    device = MockRobotDevice(harness.config, robot, "accept_and_succeed", harness.robot_journal("picker-01"), harness.broker.client(robot.client_id), clock=harness.clock, emit=harness.events.append, initialize=True, purge_session=failing_purge)
    with pytest.raises(RuntimeError):
        device.start()
    assert not harness.robot_journal_path("picker-01").exists()
    assert not harness.robot_journal_path("picker-01").with_name("robot_task_journal.jsonl.hwm").exists()
