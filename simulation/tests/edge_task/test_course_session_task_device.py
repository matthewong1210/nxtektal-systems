"""Simulator-backed Edge lifecycle consumes only committed V3 evidence."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from nxt_edge_task.contracts import (
    Availability,
    EdgeTaskError,
    EventKind,
    TaskEvent,
    TaskRequest,
    request_topic,
    stable_digest,
)
from nxt_edge_task.executor import (
    COMMITTED_EVENT_ADMITTED,
    EXECUTION_COMPLETED,
    EXECUTION_STARTED,
    ROBOT_RECORD_KINDS,
    TASK_EVENT_PERSISTED,
    RobotCore,
    derive_robot_view,
)
from nxt_edge_task.journal import JsonlJournal, PreconditionFailed, RecordSpec
from tests.edge_task.conftest import load_config
from tests.course_monitoring import test_course_collection_execution as execution_fixtures


NOW = datetime(2026, 9, 16, 0, 1, tzinfo=timezone.utc)


class CrashInjected(RuntimeError):
    pass


def crash_at(expected):
    def inject(boundary, _payload):
        if boundary == expected:
            raise CrashInjected(boundary)

    return inject


def _started_core(tmp_path, *, initialize=True, behavior="simulator_backed"):
    config = load_config()
    robot = config.robot("picker-01")
    journal = JsonlJournal(tmp_path / "robot.jsonl", allowed_kinds=ROBOT_RECORD_KINDS)
    core = RobotCore(config, robot, behavior)
    appended = journal.append_via(
        core.builder(
            lambda view: core.on_start(
                view,
                NOW,
                initialize=initialize,
                provisioning_nonce="task4-device",
            )
        )
    )
    core.absorb(appended)
    return config, robot, journal, core


def _request(config, core):
    return TaskRequest.build(
        site_id=config.site_id,
        deployment_id=config.deployment_id,
        simulation_env_id=config.simulation_env_id,
        target_robot_id="picker-01",
        target_incarnation=core.view.incarnation,
        task_type="COLLECT_BALLS_ZONE",
        zone_id="Z1",
        issued_at_utc="2026-09-16T00:01:00Z",
        expires_at_utc="2026-09-16T00:05:00Z",
        progress_window_s=60,
        issued_by="SIMULATION_TEST_ENTRY:manager",
    )


def _accept(config, journal, core, request):
    topic = request_topic(config.site_id, request.target_robot_id)
    appended = journal.append_via(
        core.builder(lambda view: core.on_request(view, topic, request.canonical_bytes(), NOW)[0])
    )
    core.absorb(appended)
    return next(
        record
        for record in appended
        if record.record_kind == TASK_EVENT_PERSISTED
        and record.payload["event"]["kind"] == "ACCEPTED"
    )


def _outbox(
    request,
    *,
    kind="PROGRESS",
    phase="collecting",
    tick=1,
    reason=None,
    protected=False,
    execution_id="a" * 64,
):
    item = {
        "execution_id": execution_id,
        "tick_sequence": tick,
        "event_kind": kind,
        "phase": phase,
        "reason": reason,
        "task_id": request.task_id,
        "incarnation": request.target_incarnation,
        "device_protection": {
            "protected": protected,
            "reasons": ["ROBOT_FAULT"] if protected else [],
            "authorization_blocked": protected,
        },
    }
    token = kind + (":" + phase if phase else "")
    item["outbox_id"] = stable_digest(
        {"execution_id": execution_id, "tick_sequence": tick, "event_kind": token}
    )
    return item


def _append_external(journal, core, outbox):
    appended = journal.append_via(
        core.builder(lambda view: core.on_committed_execution_event(view, outbox, NOW))
    )
    core.absorb(appended)
    return appended


def _tear_complete_records(journal, keep):
    lines = journal.path.read_bytes().splitlines(keepends=True)
    assert 0 <= keep <= len(lines)
    journal.path.write_bytes(b"".join(lines[:keep]))
    last_id = json.loads(lines[keep - 1])["record_id"] if keep else None
    journal.anchor_path.write_text(
        json.dumps(
            {
                "schema": "nxt-edge-task/journal-anchor/v1",
                "records": keep,
                "last_record_id": last_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def test_simulator_backed_advertises_but_tick_never_executes(tmp_path):
    _config, robot, journal, core = _started_core(tmp_path)
    request = _request(_config, core)
    _accept(_config, journal, core, request)

    assert core.effective_task_types == robot.task_types
    before = journal.path.read_bytes()
    assert core.tick(core.view, NOW) == []
    assert journal.path.read_bytes() == before
    assert not core.view.tasks[request.task_id].execution_started


def test_mock_behavior_rejects_external_committed_progress_without_mutation(tmp_path):
    config, _robot, journal, core = _started_core(
        tmp_path, behavior="accept_and_succeed"
    )
    request = _request(config, core)
    _accept(config, journal, core, request)
    before_journal = journal.path.read_bytes()
    before_view = deepcopy(core.view)

    with pytest.raises(PreconditionFailed, match="simulator_backed"):
        journal.append_via(
            core.builder(
                lambda view: core.on_committed_execution_event(
                    view, _outbox(request), NOW
                )
            )
        )

    assert journal.path.read_bytes() == before_journal
    assert core.view == before_view


def test_mock_behavior_rejects_preaccepted_committed_terminal_without_mutation(
    tmp_path,
):
    config, _robot, journal, core = _started_core(
        tmp_path, behavior="accept_and_succeed"
    )
    request = _request(config, core)
    rejected = _outbox(
        request,
        kind="REJECTED",
        phase=None,
        reason="unknown:policy_slot_missed",
    )
    before_journal = journal.path.read_bytes()
    before_view = deepcopy(core.view)

    with pytest.raises(PreconditionFailed, match="simulator_backed"):
        journal.append_via(
            core.builder(
                lambda view: core.on_preaccepted_committed_terminal(
                    view, request, rejected, NOW
                )
            )
        )

    assert journal.path.read_bytes() == before_journal
    assert core.view == before_view


def test_committed_progress_and_terminal_are_persisted_in_causal_order_and_idempotent(tmp_path):
    config, _robot, journal, core = _started_core(tmp_path)
    request = _request(config, core)
    _accept(config, journal, core, request)

    collecting = _outbox(request)
    appended = _append_external(journal, core, collecting)
    assert [record.record_kind for record in appended] == [
        COMMITTED_EVENT_ADMITTED,
        EXECUTION_STARTED,
        TASK_EVENT_PERSISTED,
    ]
    first_event = appended[-1]
    assert first_event.payload["event"]["kind"] == "PROGRESS"
    assert first_event.payload["event"]["progress"] == {"phase": "collecting"}

    before = journal.path.read_bytes()
    assert _append_external(journal, core, collecting) == ()
    assert journal.path.read_bytes() == before
    linked = core.committed_event_record(journal.read(), collecting["outbox_id"])
    assert linked.record_id == first_event.record_id

    altered = {
        **collecting,
        "device_protection": {
            "protected": True,
            "reasons": ["ROBOT_FAULT"],
            "authorization_blocked": True,
        },
    }
    with pytest.raises(PreconditionFailed, match="content conflict"):
        _append_external(journal, core, altered)

    for tick, phase in enumerate(("raw_collected", "returning", "unloading"), 2):
        _append_external(journal, core, _outbox(request, phase=phase, tick=tick))
    terminal = _outbox(request, kind="SUCCEEDED", phase=None, tick=5)
    completed = _append_external(journal, core, terminal)
    assert [record.record_kind for record in completed] == [
        COMMITTED_EVENT_ADMITTED,
        EXECUTION_COMPLETED,
        TASK_EVENT_PERSISTED,
    ]
    assert completed[-1].payload["event"]["kind"] == "SUCCEEDED"
    assert core.view.tasks[request.task_id].execution_completed
    assert core.view.tasks[request.task_id].terminal_kind == "SUCCEEDED"


def test_committed_event_rejects_wrong_identity_order_and_post_terminal_work(tmp_path):
    config, _robot, journal, core = _started_core(tmp_path)
    request = _request(config, core)
    _accept(config, journal, core, request)

    collecting = _outbox(request)
    _append_external(journal, core, collecting)
    wrong_task = {**_outbox(request, phase="raw_collected", tick=2)}
    wrong_task["task_id"] = "task_" + "f" * 24
    with pytest.raises(PreconditionFailed, match="unknown task"):
        _append_external(journal, core, wrong_task)
    wrong_incarnation = _outbox(request, phase="raw_collected", tick=2)
    wrong_incarnation["incarnation"] = "boot-picker-01-wrong"
    with pytest.raises(PreconditionFailed, match="incarnation"):
        _append_external(journal, core, wrong_incarnation)
    with pytest.raises(PreconditionFailed, match="execution identity"):
        _append_external(
            journal,
            core,
            _outbox(request, phase="raw_collected", tick=2, execution_id="b" * 64),
        )
    _append_external(journal, core, _outbox(request, phase="unloading", tick=3))
    with pytest.raises(PreconditionFailed, match="order"):
        _append_external(journal, core, _outbox(request, phase="returning", tick=4))
    _append_external(
        journal,
        core,
        _outbox(
            request,
            kind="FAILED",
            phase=None,
            tick=5,
            reason="unknown:partial_execution",
        ),
    )
    with pytest.raises(PreconditionFailed, match="terminal"):
        _append_external(journal, core, _outbox(request, phase="unloading", tick=6))


def test_accepted_task_rejects_preacceptance_terminal_and_inexact_protection(tmp_path):
    config, _robot, journal, core = _started_core(tmp_path)
    request = _request(config, core)
    _accept(config, journal, core, request)

    rejected = _outbox(
        request,
        kind="REJECTED",
        phase=None,
        reason="unknown:policy_slot_missed",
    )
    with pytest.raises(PreconditionFailed, match="pre-acceptance"):
        _append_external(journal, core, rejected)

    inexact = _outbox(request)
    inexact["device_protection"]["authorization_blocked"] = True
    with pytest.raises(PreconditionFailed, match="protection"):
        _append_external(journal, core, inexact)


def test_explicit_v3_protection_controls_device_release_not_unknown_prefix(tmp_path):
    config, _robot, journal, core = _started_core(tmp_path)
    request = _request(config, core)
    _accept(config, journal, core, request)
    _append_external(journal, core, _outbox(request))
    partial = _outbox(
        request,
        kind="FAILED",
        phase=None,
        tick=2,
        reason="unknown:partial_execution",
        protected=False,
    )
    _append_external(journal, core, partial)
    assert core.view.tasks[request.task_id].terminal_kind == "FAILED"
    assert core.view.availability.value == "available"
    assert not core.view.awaiting_human

    other_root = tmp_path / "protected"
    config2, _robot2, journal2, core2 = _started_core(other_root)
    request2 = _request(config2, core2)
    _accept(config2, journal2, core2, request2)
    _append_external(journal2, core2, _outbox(request2))
    _append_external(
        journal2,
        core2,
        _outbox(
            request2,
            kind="FAILED",
            phase=None,
            tick=2,
            reason="unknown:partial_execution",
            protected=True,
        ),
    )
    assert core2.view.awaiting_human
    assert core2.view.availability.value == "faulted"
    assert core2.view.fault_code == "robot_fault"


def test_protected_progress_fails_closed_before_later_terminal(tmp_path):
    config, _robot, journal, core = _started_core(tmp_path)
    request = _request(config, core)
    _accept(config, journal, core, request)
    _append_external(journal, core, _outbox(request, protected=True))
    assert core.view.awaiting_human
    assert core.view.availability.value == "faulted"
    assert core.view.fault_code == "robot_fault"


def test_preacceptance_committed_miss_persists_rejected_without_accepted(tmp_path):
    config, _robot, journal, core = _started_core(tmp_path)
    request = _request(config, core)
    rejected = _outbox(
        request,
        kind="REJECTED",
        phase=None,
        reason="unknown:insufficient_session_horizon",
    )
    appended = journal.append_via(
        core.builder(
            lambda view: core.on_preaccepted_committed_terminal(
                view, request, rejected, NOW
            )
        )
    )
    core.absorb(appended)
    events = [
        record.payload["event"]["kind"]
        for record in appended
        if record.record_kind == TASK_EVENT_PERSISTED
    ]
    assert events == [EventKind.REJECTED.value]
    task = core.view.tasks[request.task_id]
    assert task.decision == "rejected" and task.terminal_kind == "REJECTED"
    assert all(event["kind"] != "ACCEPTED" for event in task.events)
    assert not task.execution_completed
    assert all(record.record_kind != EXECUTION_COMPLETED for record in appended)


def test_replay_derived_view_keeps_external_event_links(tmp_path):
    config, robot, journal, core = _started_core(tmp_path)
    request = _request(config, core)
    _accept(config, journal, core, request)
    outbox = _outbox(request)
    event = _append_external(journal, core, outbox)[-1]

    replayed = derive_robot_view(robot.robot_id, journal.read())
    assert replayed.tasks[request.task_id].committed_events[outbox["outbox_id"]][
        "event"
    ] == event.payload["event"]


def test_torn_external_terminal_recovery_reuses_exact_event_and_protection(tmp_path):
    config, robot, journal, core = _started_core(tmp_path)
    request = _request(config, core)
    _accept(config, journal, core, request)
    _append_external(journal, core, _outbox(request))
    partial = _outbox(
        request,
        kind="FAILED",
        phase=None,
        tick=2,
        reason="unknown:partial_execution",
        protected=False,
    )
    appended = _append_external(journal, core, partial)
    source_event = appended[0].payload["event"]
    assert [record.record_kind for record in appended] == [
        COMMITTED_EVENT_ADMITTED,
        EXECUTION_COMPLETED,
        TASK_EVENT_PERSISTED,
    ]
    _tear_complete_records(journal, appended[-1].sequence - 1)

    restarted_journal = JsonlJournal(
        journal.path, allowed_kinds=ROBOT_RECORD_KINDS
    )
    restarted = RobotCore(config, robot, "simulator_backed")
    recovered = restarted_journal.append_via(
        restarted.builder(
            lambda view: restarted.on_start(view, NOW, initialize=False)
        )
    )
    restarted.absorb(recovered)
    recovered_event = next(
        record
        for record in recovered
        if record.record_kind == TASK_EVENT_PERSISTED
    )
    assert recovered_event.payload["event"] == source_event
    assert recovered_event.payload["committed_outbox_id"] == partial["outbox_id"]
    assert recovered_event.to_dict()["payload"]["device_protection"] == partial[
        "device_protection"
    ]
    assert restarted.view.availability.value == "available"
    assert not restarted.view.awaiting_human
    assert restarted.view.executions_for(request.task_id) == 1


def _device_store(tmp_path, config, incarnation):
    from scripts import course_collection_execution as execution_api

    identity, planning, _old_edge = execution_fixtures.inputs(tmp_path / "inputs")
    identity["site_id"] = config.site_id
    identity["deployment_id"] = config.deployment_id
    schedule = planning["confirmations"][0]["schedule"]
    task = TaskRequest.build(
        site_id=config.site_id,
        deployment_id=config.deployment_id,
        simulation_env_id=config.simulation_env_id,
        target_robot_id=schedule["robot_id"],
        target_incarnation=incarnation,
        task_type="COLLECT_BALLS_ZONE",
        zone_id=schedule["zone_id"],
        issued_at_utc=schedule["due_at_utc"],
        expires_at_utc=schedule["expires_at_utc"],
        progress_window_s=60,
        issued_by="SIMULATION_TEST_ENTRY:manager",
    )
    planning["confirmations"][0]["task_id"] = task.task_id
    planning["confirmations"][0]["task_created_at_utc"] = task.issued_at_utc
    edge = JsonlJournal(tmp_path / "task-created.jsonl")
    from nxt_edge_task.journal import RecordSpec

    edge.append(
        RecordSpec(
            "task_created",
            "EDGE",
            task.issued_at_utc,
            {
                "task_id": task.task_id,
                "schedule_id": "schedule-001",
                "request": task.to_dict(),
            },
        )
    )
    store = execution_api.CollectionExecutionStore(tmp_path / "execution.jsonl", identity)
    binding = store.bind_confirmed_tasks(planning, edge.read(), identity, 60)[0]
    request = execution_api.make_request(
        binding,
        "device-request-001",
        schedule["due_at_utc"],
        schedule["expires_at_utc"],
    )
    return store, identity, task, request


def _device_fixture(tmp_path, monkeypatch, *, task_incarnation=None):
    from scripts import course_session_task_device as device_api

    config = load_config()
    token = stable_digest(
        {
            "robot_id": "picker-01",
            "provisioned_at_utc": "2026-09-16T00:01:00.000000Z",
            "nonce": "task4-device",
        }
    )[:12]
    incarnation = f"boot-picker-01-{token}"
    store, identity, task, request = _device_store(
        tmp_path, config, task_incarnation or incarnation
    )

    @contextmanager
    def admission(_root):
        yield SimpleNamespace(
            store=store,
            identity=identity,
            now_sim_t_s=store.replay()["now_sim_t_s"],
        )

    monkeypatch.setattr(device_api, "execution_admission", admission)
    monkeypatch.setattr(device_api, "restart_reconciliation", admission)
    device = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        config,
        "picker-01",
        journal_path=tmp_path / "device.jsonl",
        initialize=True,
        provisioning_nonce=lambda: "task4-device",
    )
    device.start()
    return device_api, device, store, identity, task, request


def _append_seq0_rejection(device, task, *, reason="incarnation_mismatch"):
    event = TaskEvent(
        site_id=task.site_id,
        deployment_id=task.deployment_id,
        simulation_env_id=task.simulation_env_id,
        task_id=task.task_id,
        robot_id=task.target_robot_id,
        boot_id=device.view.boot_id,
        boot_sequence=device.view.boot_sequence,
        event_sequence=0,
        kind=EventKind.REJECTED,
        reason_code=reason,
        detail="request rejected before task admission",
        reported_at_utc="2026-09-16T00:01:00.000000Z",
        phase=None,
    )
    return device.journal.append(
        RecordSpec(
            TASK_EVENT_PERSISTED,
            "DEVICE",
            event.reported_at_utc,
            {"event": event.to_dict()},
        )
    )


def _commit_collection(store, request):
    decision = store.arbitrate(
        execution_fixtures.WAIT, 60, execution_fixtures.view()
    )
    prepared = store.prepare_tick(
        decision,
        previous_cursor=store.replay()["tick_sequence"],
        previous_digest=store.replay()["replay_digest"],
    )
    committed = store.commit_tick(
        prepared,
        now_sim_t_s=120,
        runtime_snapshots={
            request["execution_id"]: execution_fixtures.assignment(request)
        },
        safety_shield={"allowed": True, "reason": None},
        post_state_digest="f" * 64,
    )
    store.publish_cursor(committed["tick_sequence"], committed["replay_digest"])
    return committed


def test_old_incarnation_admit_closes_seq0_rejection_without_execution(
    tmp_path, monkeypatch
):
    device_api, device, store, _identity, task, request = _device_fixture(
        tmp_path,
        monkeypatch,
        task_incarnation="boot-picker-01-previous-device",
    )
    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()

    receipt = restarted.admit(task, request)
    snapshot = store.snapshot(
        server_time_utc="2026-09-16T00:02:00Z",
        session_state="ACTIVE",
    )

    assert receipt["execution_id"] == request["execution_id"]
    assert [r.record_kind for r in restarted.journal.read()].count(
        TASK_EVENT_PERSISTED
    ) == 1
    assert [r.record_kind for r in store.journal.read()].count(
        "preacceptance_rejection"
    ) == 1
    assert snapshot["executions"][0]["state"] == "REJECTED"
    assert snapshot["executions"][0]["actions"] == []
    assert snapshot["executions"][0]["raw_quantity"]["status"] == "NOT_REACHED"
    assert restarted.core.view.executions_for(task.task_id) == 0


@pytest.mark.parametrize(
    "boundary",
    ["after_preacceptance_device_rejection", "after_preacceptance_v3_rejection"],
)
def test_preacceptance_rejection_restart_reuses_both_journals(
    tmp_path, monkeypatch, boundary
):
    device_api, device, store, _identity, task, request = _device_fixture(
        tmp_path,
        monkeypatch,
        task_incarnation="boot-picker-01-previous-device",
    )
    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()
    with pytest.raises(CrashInjected, match=boundary):
        restarted.admit(task, request, crash_hook=crash_at(boundary))
    reopened = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    reopened.start()
    assert store.replay()["executions"][request["execution_id"]]["state"] == "REJECTED"
    before = (device.journal.path.read_bytes(), store.journal.path.read_bytes())
    reopened.admit(task, request)
    assert (device.journal.path.read_bytes(), store.journal.path.read_bytes()) == before
    rejections = [
        record
        for record in device.journal.read()
        if record.record_kind == TASK_EVENT_PERSISTED
        and record.payload["event"]["event_sequence"] == 0
    ]
    assert len(rejections) == 1


def test_duplicate_matching_seq0_rejections_fail_closed(tmp_path, monkeypatch):
    _api, device, store, _identity, task, request = _device_fixture(
        tmp_path,
        monkeypatch,
        task_incarnation="boot-picker-01-previous-device",
    )
    _append_seq0_rejection(device, task)
    _append_seq0_rejection(device, task)
    before_device = device.journal.path.read_bytes()

    with pytest.raises(
        PreconditionFailed, match="multiple seq-0 incarnation rejections"
    ):
        device.admit(task, request)

    assert device.journal.path.read_bytes() == before_device
    assert all(
        record.record_kind != "preacceptance_rejection"
        for record in store.journal.read()
    )


def test_near_match_seq0_rejection_fails_closed_without_second_rejection(
    tmp_path, monkeypatch
):
    _api, device, store, _identity, task, request = _device_fixture(
        tmp_path,
        monkeypatch,
        task_incarnation="boot-picker-01-previous-device",
    )
    _append_seq0_rejection(device, task, reason="deployment_mismatch")
    before_device = device.journal.path.read_bytes()

    with pytest.raises(
        PreconditionFailed, match="conflicting seq-0 rejection exists for one task"
    ):
        device.admit(task, request)

    assert device.journal.path.read_bytes() == before_device
    assert all(
        record.record_kind != "preacceptance_rejection"
        for record in store.journal.read()
    )


def test_seq0_publication_confirmation_uses_device_record_sequence(
    tmp_path, monkeypatch
):
    _api, device, _store, _identity, task, request = _device_fixture(
        tmp_path,
        monkeypatch,
        task_incarnation="boot-picker-01-previous-device",
    )
    device.admit(task, request)
    rejection_record = next(
        record
        for record in device.journal.read()
        if record.record_kind == TASK_EVENT_PERSISTED
        and record.payload["event"]["event_sequence"] == 0
    )
    pending = device.pending_publications()
    assert pending == [rejection_record.payload["event"]]

    device.confirm_published(pending[0])

    assert rejection_record.sequence in device.view.confirmed_rejections
    confirmation = device.journal.read()[-1]
    assert confirmation.record_kind == "event_publish_confirmed"
    assert confirmation.payload["record_sequence"] == rejection_record.sequence
    before = device.journal.path.read_bytes()
    device.confirm_published(pending[0])
    assert device.journal.path.read_bytes() == before


def test_seq0_rejection_after_v3_acceptance_remains_authorization_blocked(
    tmp_path, monkeypatch
):
    device_api, device, store, _identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    device.admit(task, request)
    replacement = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=tmp_path / "replacement-device.jsonl",
        initialize=True,
        provisioning_nonce=lambda: "replacement-device",
    )
    replacement.start()

    with pytest.raises(EdgeTaskError, match="authorization_blocked"):
        replacement.admit(task, request)

    row = store.replay()["executions"][request["execution_id"]]
    assert row["edge_evidence"]["accepted"]
    assert all(
        record.record_kind != "preacceptance_rejection"
        for record in store.journal.read()
    )
    assert replacement.core.view.executions_for(task.task_id) == 0


def test_device_durably_accepts_then_consumes_only_committed_outbox(tmp_path, monkeypatch):
    api, device, store, _identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    receipt = device.admit(task, request)
    assert receipt == store.request_result(request["request_id"])
    state = store.replay()
    row = state["executions"][request["execution_id"]]
    assert row["edge_evidence"]["accepted"]
    assert device.core.view.tasks[task.task_id].terminal_kind is None
    assert not device.core.view.tasks[task.task_id].execution_started

    # A prepared record alone is never visible to the device.
    decision = store.arbitrate(
        execution_fixtures.WAIT, 60, execution_fixtures.view()
    )
    prepared = store.prepare_tick(
        decision,
        previous_cursor=0,
        previous_digest=store.replay()["replay_digest"],
    )
    assert device.consume_committed() == []
    assert not device.core.view.tasks[task.task_id].execution_started

    store.commit_tick(
        prepared,
        now_sim_t_s=120,
        runtime_snapshots={
            request["execution_id"]: execution_fixtures.assignment(request)
        },
        safety_shield={"allowed": True, "reason": None},
        post_state_digest="f" * 64,
    )
    store.publish_cursor(1, store.replay()["replay_digest"])
    delivered = device.consume_committed()
    assert [item["phase"] for item in delivered] == ["collecting", "raw_collected"]
    assert store.committed_outbox(unconfirmed_only=True) == []
    assert device.core.view.tasks[task.task_id].execution_started
    assert all(
        record.record_kind != "event_publish_confirmed"
        for record in device.journal.read()
    )
    pending = device.pending_publications()
    assert [event["kind"] for event in pending] == ["ACCEPTED", "PROGRESS", "PROGRESS"]
    enters = {"count": 0}
    original_admission = api.execution_admission

    @contextmanager
    def counted_admission(root):
        enters["count"] += 1
        with original_admission(root) as admitted:
            yield admitted

    monkeypatch.setattr(api, "execution_admission", counted_admission)
    device.confirm_published(pending[0])
    assert enters["count"] == 1
    assert device.pending_publications()[0]["progress"] == {"phase": "collecting"}
    before_confirmation_retry = device.journal.path.read_bytes()
    device.confirm_published(pending[0])
    assert device.journal.path.read_bytes() == before_confirmation_retry


@pytest.mark.parametrize(
    "boundary",
    ["after_device_append", "after_v3_evidence", "after_v3_confirm"],
)
def test_device_retry_at_each_cross_journal_boundary_reuses_event_identity(
    tmp_path, monkeypatch, boundary
):
    _api, device, store, _identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    device.admit(task, request)
    _commit_collection(store, request)

    class Crash(RuntimeError):
        pass

    def crash(name, _payload):
        if name == boundary:
            raise Crash(name)

    with pytest.raises(Crash, match=boundary):
        device.consume_committed(crash_hook=crash, limit=1)
    externally_visible = [
        event
        for event in device.pending_publications()
        if event["kind"] == EventKind.PROGRESS.value
    ]
    assert bool(externally_visible) is (boundary == "after_v3_confirm")
    before = [
        record
        for record in device.journal.read()
        if record.record_kind == TASK_EVENT_PERSISTED
        and record.payload.get("committed_outbox_id") is not None
    ]
    device.consume_committed(limit=1)
    after = [
        record
        for record in device.journal.read()
        if record.record_kind == TASK_EVENT_PERSISTED
        and record.payload.get("committed_outbox_id") is not None
    ]
    assert [(r.sequence, r.record_id, r.to_dict()) for r in after[:1]] == [
        (r.sequence, r.record_id, r.to_dict()) for r in before[:1]
    ]
    assert len({r.payload["committed_outbox_id"] for r in after}) == len(after)


def test_restart_reconciles_prior_device_restart_terminal_before_residual_work(
    tmp_path, monkeypatch
):
    device_api, device, store, _identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    device.admit(task, request)

    # Model a real process that durably completed RobotCore.on_start(), then
    # crashed before the cross-journal V3 restart evidence write.
    crashed = RobotCore(device.config, device.robot, "simulator_backed")
    appended = device.journal.append_via(
        crashed.builder(
            lambda view: crashed.on_start(view, NOW, initialize=False)
        )
    )
    restart_terminal = next(
        record
        for record in appended
        if record.record_kind == TASK_EVENT_PERSISTED
        and record.payload["event"]["reason_code"] == "not_started_after_restart"
    )
    assert restart_terminal.record_id not in store.replay()["executions"][
        request["execution_id"]
    ]["edge_evidence"]["event_ids"]

    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()
    row = store.replay()["executions"][request["execution_id"]]
    assert row["state"] == "FAILED"
    assert row["reason"] == "NOT_STARTED_AFTER_RESTART"
    assert restart_terminal.record_id in row["edge_evidence"]["event_ids"]
    assert store.committed_outbox(unconfirmed_only=True) == []


def test_restart_recovers_device_acceptance_before_its_restart_terminal(
    tmp_path, monkeypatch
):
    device_api, device, store, identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    store.submit(request)
    now = device._when(identity, store.replay()["now_sim_t_s"])
    device._append(
        lambda view: device.core.on_request(
            view,
            request_topic(device.config.site_id, "picker-01"),
            task.canonical_bytes(),
            now,
        )[0]
    )
    assert not store.replay()["executions"][request["execution_id"]][
        "edge_evidence"
    ]["accepted"]

    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()
    row = store.replay()["executions"][request["execution_id"]]
    assert row["edge_evidence"]["accepted"]
    assert row["state"] == "FAILED"
    assert row["reason"] == "NOT_STARTED_AFTER_RESTART"
    assert [event["kind"] for event in restarted.pending_publications()] == [
        "ACCEPTED",
        "FAILED",
    ]


def test_request_only_torn_prefix_is_decided_only_when_current_v3_admit_retries(
    tmp_path, monkeypatch
):
    device_api, device, store, identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    store.submit(request)
    now = device._when(identity, store.replay()["now_sim_t_s"])
    before = len(device.journal.read())
    appended = device._append(
        lambda view: device.core.on_request(
            view,
            request_topic(device.config.site_id, "picker-01"),
            task.canonical_bytes(),
            now,
        )[0]
    )
    assert [record.record_kind for record in appended] == [
        "request_received",
        "task_decision",
        TASK_EVENT_PERSISTED,
    ]
    _tear_complete_records(device.journal, before + 1)

    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()
    assert restarted.view.tasks[task.task_id].decision is None
    assert not store.replay()["executions"][request["execution_id"]][
        "edge_evidence"
    ]["accepted"]

    receipt = restarted.admit(task, request)

    assert receipt == store.request_result(request["request_id"])
    task_view = restarted.view.tasks[task.task_id]
    assert task_view.decision == "accepted"
    assert [event["kind"] for event in task_view.events] == ["ACCEPTED"]
    assert not task_view.execution_started and not task_view.execution_completed
    assert store.replay()["executions"][request["execution_id"]][
        "edge_evidence"
    ]["accepted"]


def test_new_v3_session_ignores_historical_device_tasks_but_keeps_protection(
    tmp_path, monkeypatch
):
    device_api, device, store, _identity, _task, _request = _device_fixture(
        tmp_path, monkeypatch
    )
    historical = TaskRequest.build(
        site_id=device.config.site_id,
        deployment_id=device.config.deployment_id,
        simulation_env_id=device.config.simulation_env_id,
        target_robot_id="picker-01",
        target_incarnation=device.view.incarnation,
        task_type="COLLECT_BALLS_ZONE",
        zone_id="historical-zone",
        issued_at_utc="2026-09-16T00:01:00Z",
        expires_at_utc="2026-09-16T00:05:00Z",
        progress_window_s=60,
        issued_by="SIMULATION_TEST_ENTRY:historical-session",
    )
    device._append(
        lambda view: device.core.on_request(
            view,
            request_topic(device.config.site_id, "picker-01"),
            historical.canonical_bytes(),
            NOW,
        )[0]
    )

    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()
    assert store.replay()["executions"] == {}
    old = restarted.view.tasks[historical.task_id]
    assert old.terminal_kind == "FAILED"
    assert old.events[-1]["reason_code"] == "not_started_after_restart"
    assert restarted.view.awaiting_human


def test_real_device_restart_precedes_and_blocks_residual_outbox(tmp_path, monkeypatch):
    device_api, device, store, _identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    device.admit(task, request)
    _commit_collection(store, request)
    assert store.committed_outbox(unconfirmed_only=True)

    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()
    row = store.replay()["executions"][request["execution_id"]]
    assert row["state"] == "INCONCLUSIVE"
    assert row["reason"] == "REPLAY_MISMATCH"
    assert row["conflicts"]["replay_mismatch"]
    assert store.committed_outbox(unconfirmed_only=True) == []
    assert restarted.core.view.executions_for(task.task_id) == 0


@pytest.mark.parametrize("started", [False, True])
def test_real_device_restart_uses_existing_matched_branches(
    tmp_path, monkeypatch, started
):
    device_api, device, store, _identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    device.admit(task, request)
    if started:
        _commit_collection(store, request)
        device.consume_committed(limit=1)
        assert device.core.view.executions_for(task.task_id) == 1

    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()
    row = store.replay()["executions"][request["execution_id"]]
    assert row["state"] == ("INCONCLUSIVE" if started else "FAILED")
    assert row["reason"] == (
        "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME"
        if started
        else "NOT_STARTED_AFTER_RESTART"
    )
    assert not row["conflicts"]["replay_mismatch"]
    assert row["device_protection"]["protected"]
    assert row["device_protection"]["authorization_blocked"]
    assert (
        "RESTART_UNKNOWN" if started else "ORPHANED_ACTIVITY"
    ) in row["device_protection"]["reasons"]
    assert restarted.core.view.awaiting_human
    assert restarted.core.view.executions_for(task.task_id) == (1 if started else 0)
    assert store.committed_outbox(unconfirmed_only=True) == []


def test_completed_device_restart_retains_original_terminal_identity(tmp_path):
    config, robot, journal, core = _started_core(tmp_path)
    request = _request(config, core)
    _accept(config, journal, core, request)
    _append_external(journal, core, _outbox(request))
    terminal = _append_external(
        journal,
        core,
        _outbox(request, kind="SUCCEEDED", phase=None, tick=2),
    )[-1]

    restarted = RobotCore(config, robot, "simulator_backed")
    appended = journal.append_via(
        restarted.builder(
            lambda view: restarted.on_start(view, NOW, initialize=False)
        )
    )
    restarted.absorb(appended)
    terminals = [
        record
        for record in journal.read()
        if record.record_kind == TASK_EVENT_PERSISTED
        and record.payload["event"]["kind"] == "SUCCEEDED"
    ]
    assert [record.record_id for record in terminals] == [terminal.record_id]
    assert restarted.view.executions_for(request.task_id) == 1
    assert any(
        event["kind"] == "SUCCEEDED"
        for event, _record in restarted.view.pending_publications()
    )


def test_new_incarnation_rejects_old_authorization_without_execution(tmp_path):
    config, robot, _journal, old = _started_core(tmp_path / "old")
    request = _request(config, old)
    new_journal = JsonlJournal(
        tmp_path / "new" / "robot.jsonl", allowed_kinds=ROBOT_RECORD_KINDS
    )
    new = RobotCore(config, robot, "simulator_backed")
    appended = new_journal.append_via(
        new.builder(
            lambda view: new.on_start(
                view,
                NOW,
                initialize=True,
                provisioning_nonce="replacement-device",
            )
        )
    )
    new.absorb(appended)
    topic = request_topic(config.site_id, robot.robot_id)
    rejected = new_journal.append_via(
        new.builder(
            lambda view: new.on_request(
                view, topic, request.canonical_bytes(), NOW
            )[0]
        )
    )
    new.absorb(rejected)
    events = [
        record.payload["event"]
        for record in rejected
        if record.record_kind == TASK_EVENT_PERSISTED
    ]
    assert len(events) == 1 and events[0]["event_sequence"] == 0
    assert events[0]["reason_code"] == "incarnation_mismatch"
    assert request.task_id not in new.view.tasks
    assert new.view.executions_started == 0


def test_runtime_status_is_detached_complete_and_fail_closed(tmp_path, monkeypatch):
    _api, device, _store, identity, _task, _request = _device_fixture(
        tmp_path, monkeypatch
    )
    with pytest.raises(ValueError, match="runtime status"):
        device.status_message({})
    status = device.status_message(
        {
            "session_id": identity["session_id"],
            "round_id": identity["round_id"],
            "robot_id": "picker-01",
            "simulation_time_utc": "2026-09-16T00:01:00Z",
            "session_state": "ACTIVE",
            "activity": "IDLE",
            "payload_balls": 0,
            "paused": False,
            "faulted": False,
            "estop_latched": False,
            "awaiting_human": False,
        }
    )
    assert status.reported_at_utc == "2026-09-16T00:01:00.000000Z"
    assert status.availability.value == "available"
    second = device.status_message(
        {
            "session_id": identity["session_id"],
            "round_id": identity["round_id"],
            "robot_id": "picker-01",
            "simulation_time_utc": "2026-09-16T00:01:00Z",
            "session_state": "ACTIVE",
            "activity": "IDLE",
            "payload_balls": 0,
            "paused": False,
            "faulted": False,
            "estop_latched": False,
            "awaiting_human": False,
        }
    )
    assert (status.status_sequence, second.status_sequence) == (1, 2)


@pytest.mark.parametrize(
    "reason,expected,estop",
    [
        ("ROBOT_FAULT", Availability.FAULTED, False),
        ("ESTOP_LATCHED", Availability.ESTOPPED, True),
    ],
)
def test_runtime_status_cannot_downgrade_persisted_device_protection(
    tmp_path, monkeypatch, reason, expected, estop
):
    _api, device, _store, identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    device.admit(task, request)
    outbox = _outbox(
        task,
        protected=True,
        execution_id=request["execution_id"],
    )
    outbox["device_protection"]["reasons"] = [reason]
    device._append(
        lambda view: device.core.on_committed_execution_event(view, outbox, NOW)
    )
    assert device.view.availability is expected

    status = device.status_message(
        {
            "session_id": identity["session_id"],
            "round_id": identity["round_id"],
            "robot_id": "picker-01",
            "simulation_time_utc": "2026-09-16T00:01:00Z",
            "session_state": "ACTIVE",
            "activity": "IDLE",
            "payload_balls": 0,
            "paused": False,
            "faulted": False,
            "estop_latched": False,
            "awaiting_human": False,
        }
    )
    assert status.availability is expected
    assert status.estop_latched is estop


def test_preaccepted_terminal_device_path_never_writes_accepted(tmp_path, monkeypatch):
    _api, device, store, _identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    store.submit(request)
    decision = store.arbitrate(
        execution_fixtures.WAIT, 3000, execution_fixtures.view()
    )
    prepared = store.prepare_tick(
        decision,
        previous_cursor=0,
        previous_digest=store.replay()["replay_digest"],
    )
    committed = store.commit_tick(
        prepared,
        now_sim_t_s=3060,
        runtime_snapshots={},
        safety_shield={"allowed": True, "reason": None},
        post_state_digest="b" * 64,
    )
    store.publish_cursor(1, committed["replay_digest"])
    receipt = device.admit(task, request)
    kinds = [event["kind"] for event in device.core.view.tasks[task.task_id].events]
    assert kinds == ["REJECTED"]
    assert not store.replay()["executions"][request["execution_id"]]["edge_evidence"][
        "accepted"
    ]
    assert store.committed_outbox(unconfirmed_only=True) == []
    before_device = device.journal.path.read_bytes()
    before_v3 = store.journal.path.read_bytes()
    assert device.admit(task, request) == receipt
    assert device.journal.path.read_bytes() == before_device
    assert store.journal.path.read_bytes() == before_v3


@pytest.mark.parametrize("keep_from_batch", [0, 1, 2, 3, 4])
def test_preaccepted_rejected_torn_batch_reuses_committed_event_without_execution(
    tmp_path, monkeypatch, keep_from_batch
):
    _api, device, store, identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    store.submit(request)
    decision = store.arbitrate(
        execution_fixtures.WAIT, 3000, execution_fixtures.view()
    )
    prepared = store.prepare_tick(
        decision,
        previous_cursor=0,
        previous_digest=store.replay()["replay_digest"],
    )
    committed = store.commit_tick(
        prepared,
        now_sim_t_s=3060,
        runtime_snapshots={},
        safety_shield={"allowed": True, "reason": None},
        post_state_digest="b" * 64,
    )
    store.publish_cursor(1, committed["replay_digest"])
    outbox = next(
        item
        for item in store.committed_outbox(unconfirmed_only=True)
        if item["execution_id"] == request["execution_id"]
    )
    before = len(device.journal.read())
    now = device._when(identity, store.replay()["now_sim_t_s"])
    appended = device._append(
        lambda view: device.core.on_preaccepted_committed_terminal(
            view, task, outbox, now
        )
    )
    assert [record.record_kind for record in appended] == [
        "request_received",
        COMMITTED_EVENT_ADMITTED,
        "task_decision",
        TASK_EVENT_PERSISTED,
    ]
    source_event = appended[1].payload["event"]
    _tear_complete_records(device.journal, before + keep_from_batch)

    from scripts import course_session_task_device as device_api

    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()
    restarted.admit(task, request)
    task_view = restarted.view.tasks[task.task_id]
    assert [event["kind"] for event in task_view.events] == ["REJECTED"]
    assert not task_view.execution_started and not task_view.execution_completed
    linked = [
        record
        for record in restarted.journal.read()
        if record.record_kind == TASK_EVENT_PERSISTED
        and record.payload.get("committed_outbox_id") == outbox["outbox_id"]
    ]
    assert len(linked) == 1
    if keep_from_batch >= 2:
        assert linked[0].payload["event"] == source_event
    assert all(
        record.record_kind != EXECUTION_COMPLETED
        for record in restarted.journal.read()
        if record.payload.get("task_id") == task.task_id
        or record.payload.get("event", {}).get("task_id") == task.task_id
    )
    assert store.committed_outbox(unconfirmed_only=True) == []


@pytest.mark.parametrize(
    "cause,expected_reason",
    [
        ("safety", "unknown:safety_rejected"),
        ("missed", "unknown:policy_slot_missed"),
    ],
)
def test_postacceptance_rejection_or_miss_maps_to_failed_not_rejected(
    tmp_path, monkeypatch, cause, expected_reason
):
    _api, device, store, _identity, task, request = _device_fixture(
        tmp_path, monkeypatch
    )
    device.admit(task, request)
    now = 60 if cause == "safety" else 300
    original = (
        execution_fixtures.WAIT
        if cause == "safety"
        else execution_fixtures.PAUSE
    )
    decision = store.arbitrate(original, now, execution_fixtures.view())
    prepared = store.prepare_tick(
        decision,
        previous_cursor=0,
        previous_digest=store.replay()["replay_digest"],
    )
    committed = store.commit_tick(
        prepared,
        now_sim_t_s=now + 60,
        runtime_snapshots={},
        safety_shield={
            "allowed": cause != "safety",
            "reason": "zone closed" if cause == "safety" else None,
        },
        post_state_digest="a" * 64,
    )
    store.publish_cursor(1, committed["replay_digest"])
    device.consume_committed()
    events = device.core.view.tasks[task.task_id].events
    assert [event["kind"] for event in events] == ["ACCEPTED", "FAILED"]
    assert events[-1]["reason_code"] == expected_reason
    assert device.core.view.tasks[task.task_id].terminal_kind == "FAILED"
    assert device.core.view.availability.value == "available"
