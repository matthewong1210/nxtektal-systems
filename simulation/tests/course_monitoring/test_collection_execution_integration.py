"""Planning confirmation drives actual V3 ledger collection and unload."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect

import pytest

from nxt_edge_task.cases import TASK_CREATED
from nxt_edge_task.contracts import parse_utc
from scripts import course_session_v3
from scripts.joint_learning import read_record


WALL = datetime(2035, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class WallClock:
    def __init__(self, value=WALL):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def demo_api():
    from scripts import course_collection_execution_demo

    return course_collection_execution_demo


def _run(root, demo_api):
    runtime = demo_api.CourseCollectionExecutionDemo(
        root, initialize=True, wall_clock=WallClock()
    )
    try:
        result = runtime.run(advance=2)
        duplicate = runtime.ensure_execution()
        return result, duplicate, runtime.evidence()
    finally:
        runtime.close()


def test_fresh_root_planning_to_actual_collection_and_unload_is_reproducible(
    tmp_path, demo_api
):
    first, duplicate, first_evidence = _run(tmp_path / "first", demo_api)
    second, _, second_evidence = _run(tmp_path / "second", demo_api)

    snapshot = first["collection_executions"]
    execution = snapshot["executions"][0]
    planning = first["planning"]
    trace = first_evidence["assignment"]
    replay = first_evidence["replay_plan"]
    created = [
        row for row in first_evidence["edge_records"]
        if row["record_kind"] == TASK_CREATED
    ]

    assert len(created) == 1
    warmup = replay[0]["prepared"]["decision"]
    assert warmup["sim_t_s"] == 28800
    assert warmup["original_action"]["name"] == "Wait"
    assert warmup["selected_action"] == warmup["original_action"]
    assert warmup["selection"] == "ORIGINAL_POLICY_UNCHANGED"
    assert [tick["session"]["now_sim_t_s"] for tick in first["ticks"]] == [
        30000,
        30600,
    ]
    assert all(tick["delivered"] for tick in first["ticks"])
    assert snapshot["now_sim_t_s"] == 30600
    assert duplicate == first["request_receipt"]
    assert len(snapshot["requests"]) == len(snapshot["receipts"]) == 1
    assert execution["state"] == "SUCCEEDED"
    assert execution["reason"] == "UNLOADED_ALL_COLLECTED_BALLS"
    assert execution["policy_id"] == "JointDispatchPolicy-v1"
    assert execution["max_execution_s"] == 1200
    assert execution["started_sim_t_s"] == 29400
    assert execution["execution_deadline_sim_t_s"] == 30600
    assert 30000 < execution["terminal_sim_t_s"] <= 30600
    assert execution["runtime_evidence"]["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
    assert execution["runtime_evidence"]["conservation_passed"] is True
    assert execution["runtime_evidence"]["payload_parity_passed"] is True
    assert execution["edge_evidence"]["effective_state"] == "SUCCEEDED"
    assert execution["edge_evidence"]["result_verification"] == "VERIFIED"
    assert execution["success_display_allowed"] is True
    assert execution["device_protection"] == {
        "protected": False,
        "reasons": [],
        "authorization_blocked": False,
    }

    raw = execution["raw_quantity"]
    unloaded = execution["unload_quantity"]
    assert raw["source"] == unloaded["source"] == "RANGE_SIMULATION_BALL_LEDGER"
    assert raw["status"] == unloaded["status"] == "COMPLETE"
    assert raw["balls"] == unloaded["balls"] == 600
    assert raw["balls"] > 0
    assert raw["assignment_id"] == unloaded["assignment_id"] == trace["assignment_id"]
    assert raw["event_digest"] == unloaded["event_digest"] == trace["event_digest"]
    assert raw["source_event_ids"] == [
        event["event_id"] for event in trace["events"]
        if event["kind"] == "RAW_COLLECTED_TO_ROBOT"
    ]
    assert unloaded["source_event_ids"] == [
        event["event_id"] for event in trace["events"]
        if event["kind"] == "UNLOADED_TO_STATION"
    ]
    raw_moves = [event for event in trace["events"] if event["kind"] == "RAW_COLLECTED_TO_ROBOT"]
    unload_moves = [event for event in trace["events"] if event["kind"] == "UNLOADED_TO_STATION"]
    assert sum(event["balls"] for event in raw_moves) == 600
    assert sum(event["balls"] for event in unload_moves) == 600
    assert {event["source_location"] for event in raw_moves} == {"zone:NEAR_LEFT"}
    assert {event["destination_location"] for event in raw_moves} == {"robot:R1"}
    assert {event["source_location"] for event in unload_moves} == {"robot:R1"}
    assert {event["destination_location"] for event in unload_moves} == {"station:H1"}

    assert [(row["sim_t_s"], row["original_action"]["name"],
             row["selected_action"]["name"], row["selection"])
            for row in execution["actions"]] == [
        (29400, "Wait", "AssignCollection", "WAIT_SLOT"),
        (30000, "SendToHandoff", "SendToHandoff", "ORIGINAL_POLICY_CONVERGED"),
    ]
    assert execution["actions"][0]["selected_action"] == {
        "name": "AssignCollection", "index": 8,
        "robot_id": "R1", "target_id": "NEAR_LEFT",
    }
    assert execution["actions"][1]["original_action"] == {
        "name": "SendToHandoff", "index": 28,
        "robot_id": "R1", "target_id": None,
    }
    assert execution["actions"][1]["selected_action"] == execution["actions"][1]["original_action"]

    assert planning["outcomes"] == []
    assert planning["latest_input"]["inventory_clean_balls"]["value"] == 600
    assert all("washed" not in event["kind"].lower() and "supplied" not in event["kind"].lower()
               for event in trace["events"])
    assert first_evidence["planning_record_kinds"].count("planning_confirmation_recorded") == 1
    assert first_evidence["planning_record_kinds"].count("planning_outcome_recorded") == 0
    assert first_evidence["edge_task_state"] == "SUCCEEDED"
    assert len(execution["edge_evidence"]["event_ids"]) >= 6
    assert "mock_robot_task_device" not in inspect.getsource(demo_api).lower()

    stable = lambda value: {
        "request_id": value["request_receipt"]["request_id"],
        "execution_id": value["collection_executions"]["executions"][0]["execution_id"],
        "assignment_id": value["collection_executions"]["executions"][0]["assignment_id"],
        "replay_digest": value["collection_executions"]["replay_digest"],
        "event_digest": value["collection_executions"]["executions"][0]["runtime_evidence"]["event_digest"],
        "edge_event_ids": value["collection_executions"]["executions"][0]["edge_evidence"]["event_ids"],
    }
    assert stable(first) == stable(second)
    assert first_evidence["assignment"] == second_evidence["assignment"]


def test_schedule_form_uses_simulation_date_while_service_read_uses_wall_health(
    tmp_path, demo_api
):
    wall = WallClock()
    runtime = demo_api.CourseCollectionExecutionDemo(
        tmp_path, initialize=True, wall_clock=wall
    )
    try:
        runtime.start()
        status = runtime.runtime_status()
        assert status["simulation_time_utc"] == "2026-09-16T08:10:00Z"
        created = runtime.create_schedule(
            {
                "robot_id": "picker-01",
                "zone_id": "Z1",
                "due_at_utc": "2026-09-16T08:11:00Z",
                "expires_at_utc": "2026-09-16T08:12:00Z",
                "operator": "simulation-form-test",
            }
        )
        assert created["status"] == "created"
        read = runtime.task_operations_snapshot()
        assert parse_utc(read["server_time_utc"]) == WALL
        assert read["scheduler"] == {"state": "RUNNING", "detail": None}
        assert read["runtime"]["session_state"] == "ACTIVE"

        wall.advance(16)
        course_session_v3.set_paused(runtime.session_root, True)
        paused = runtime.task_operations_snapshot()
        assert parse_utc(paused["server_time_utc"]) == WALL + timedelta(seconds=16)
        assert paused["scheduler"] == {"state": "RUNNING", "detail": None}
        assert paused["runtime"]["session_state"] == "PAUSED"
        assert paused["runtime"]["simulation_time_utc"] == status["simulation_time_utc"]
    finally:
        runtime.close()


def test_completed_root_restart_replays_without_a_second_task_or_transfer(
    tmp_path, demo_api
):
    first = demo_api.CourseCollectionExecutionDemo(
        tmp_path, initialize=True, wall_clock=WallClock()
    )
    try:
        before = first.run(advance=2)["collection_executions"]
        assignment = first.evidence()["assignment"]
    finally:
        first.close()

    resumed = demo_api.CourseCollectionExecutionDemo(
        tmp_path, initialize=False, wall_clock=WallClock()
    )
    try:
        after = resumed.run(advance=0)["collection_executions"]
        evidence = resumed.evidence()
        assert after["replay_digest"] == before["replay_digest"]
        assert after["executions"] == before["executions"]
        assert evidence["assignment"] == assignment
        assert sum(
            row["record_kind"] == TASK_CREATED
            for row in evidence["edge_records"]
        ) == 1
    finally:
        resumed.close()


def test_after_prepare_crash_real_device_restart_cancels_old_authorization(
    tmp_path, demo_api, monkeypatch
):
    """A real device restart attests before any prepared simulator replay."""

    runtime = demo_api.CourseCollectionExecutionDemo(
        tmp_path, initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    runtime.ensure_execution()
    config = runtime.config
    session_root = runtime.session_root
    device_journal = runtime.device.journal.path
    with course_session_v3.execution_admission(session_root) as admission:
        committed_before = len(admission.store.replay_plan())

    class Crash(RuntimeError):
        pass

    def crash_after_prepare(boundary, _payload):
        if boundary == "after_prepare":
            raise Crash(boundary)

    try:
        with pytest.raises(Crash, match="after_prepare"):
            course_session_v3.run(
                session_root, crash_hook=crash_after_prepare
            )
    finally:
        runtime.close()

    identity = read_record(session_root / "identity.json")
    store = demo_api.execution_api.CollectionExecutionStore(
        session_root / "collection-execution.jsonl",
        identity,
        policy_id=course_session_v3.POLICY_ID,
    )
    pending = store.recovery_state()
    assert pending["status"] == "PREPARED_NO_COMMIT"
    execution_id = pending["pending_prepared"]["decision"]["execution_id"]
    assert execution_id is not None

    env_steps = 0
    original_step = course_session_v3.RangeOpsEnv.step

    def counted_step(env, action):
        nonlocal env_steps
        env_steps += 1
        return original_step(env, action)

    monkeypatch.setattr(course_session_v3.RangeOpsEnv, "step", counted_step)
    restarted = demo_api.SimulatorBackedTaskDevice(
        session_root,
        config,
        demo_api.ROBOT_ID,
        journal_path=device_journal,
        initialize=False,
    )
    restarted.start()

    state = store.replay()
    row = state["executions"][execution_id]
    task_events = [
        record.payload["event"]
        for record in restarted.journal.read()
        if record.record_kind == "task_event_persisted"
        and record.payload["event"]["task_id"] == row["task_id"]
    ]
    assert env_steps == 0
    assert store.recovery_state()["status"] == "NO_PREPARED"
    assert len(store.replay_plan()) == committed_before
    assert store.committed_outbox(unconfirmed_only=True) == []
    assert (row["state"], row["reason"]) == (
        "FAILED",
        "NOT_STARTED_AFTER_RESTART",
    )
    assert row["device_protection"]["protected"] is True
    assert row["device_protection"]["authorization_blocked"] is True
    assert row["assignment_id"] is None
    assert row["raw_quantity"]["balls"] is None
    assert row["unload_quantity"]["balls"] is None
    assert [event["kind"] for event in task_events] == ["ACCEPTED", "FAILED"]
    assert restarted.view.executions_for(row["task_id"]) == 0

    journal_before_second_restart = [
        event
        for event in task_events
        if event["kind"] in {"ACCEPTED", "FAILED", "INCONCLUSIVE"}
    ]
    again = demo_api.SimulatorBackedTaskDevice(
        session_root,
        config,
        demo_api.ROBOT_ID,
        journal_path=device_journal,
        initialize=False,
    )
    again.start()
    journal_after_second_restart = [
        record.payload["event"]
        for record in again.journal.read()
        if record.record_kind == "task_event_persisted"
        and record.payload["event"]["task_id"] == row["task_id"]
        and record.payload["event"]["kind"]
        in {"ACCEPTED", "FAILED", "INCONCLUSIVE"}
    ]
    assert journal_after_second_restart == journal_before_second_restart
    assert store.replay()["executions"][execution_id]["state"] == "FAILED"


def test_after_commit_crash_demo_repairs_cursor_before_device_restart(
    tmp_path, demo_api, monkeypatch
):
    """Committed simulator truth is replayed once, never re-authorized."""

    runtime = demo_api.CourseCollectionExecutionDemo(
        tmp_path, initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    runtime.ensure_execution()
    session_root = runtime.session_root
    device_journal = runtime.device.journal.path

    class Crash(RuntimeError):
        pass

    def crash_after_commit(boundary, _payload):
        if boundary == "after_commit":
            raise Crash(boundary)

    try:
        with pytest.raises(Crash, match="after_commit"):
            course_session_v3.run(
                session_root, crash_hook=crash_after_commit
            )
    finally:
        runtime.close()

    identity = read_record(session_root / "identity.json")
    store = demo_api.execution_api.CollectionExecutionStore(
        session_root / "collection-execution.jsonl",
        identity,
        policy_id=course_session_v3.POLICY_ID,
    )
    before = store.recovery_state()
    assert before["status"] == "COMMITTED_CURSOR_STALE"
    plan_before = store.replay_plan()
    assert len(plan_before) == before["tick_sequence"]
    assert before["tick_sequence"] > 0
    committed = plan_before[-1]["committed"]
    execution_id = plan_before[-1]["prepared"]["decision"]["execution_id"]
    assignment_before = committed["result"]["runtime_snapshots"][execution_id]
    assert assignment_before["raw_collected_balls"] > 0
    outbox_before = {
        row["outbox_id"]: row
        for row in store.committed_outbox(unconfirmed_only=False)
    }
    assert outbox_before

    prepare_calls = commit_calls = replay_steps = 0
    original_prepare = demo_api.execution_api.CollectionExecutionStore.prepare_tick
    original_commit = demo_api.execution_api.CollectionExecutionStore.commit_tick
    original_step = course_session_v3.RangeOpsEnv.step

    def counted_prepare(store_self, *args, **kwargs):
        nonlocal prepare_calls
        prepare_calls += 1
        return original_prepare(store_self, *args, **kwargs)

    def counted_commit(store_self, *args, **kwargs):
        nonlocal commit_calls
        commit_calls += 1
        return original_commit(store_self, *args, **kwargs)

    def counted_step(env, action):
        nonlocal replay_steps
        replay_steps += 1
        return original_step(env, action)

    monkeypatch.setattr(
        demo_api.execution_api.CollectionExecutionStore,
        "prepare_tick",
        counted_prepare,
    )
    monkeypatch.setattr(
        demo_api.execution_api.CollectionExecutionStore,
        "commit_tick",
        counted_commit,
    )
    monkeypatch.setattr(course_session_v3.RangeOpsEnv, "step", counted_step)

    resumed = demo_api.CourseCollectionExecutionDemo(
        tmp_path, initialize=False, wall_clock=WallClock()
    )
    try:
        resumed.start()
        state = store.replay()
        row = state["executions"][execution_id]
        task_events = [
            record.payload["event"]
            for record in resumed.device.journal.read()
            if record.record_kind == "task_event_persisted"
            and record.payload["event"]["task_id"] == row["task_id"]
        ]

        assert replay_steps > 0
        assert prepare_calls == commit_calls == 0
        assert store.replay_plan() == plan_before
        assert state["cursor"] == {
            "tick_sequence": committed["tick_sequence"],
            "replay_digest": committed["replay_digest"],
        }
        assert state["blocked_outbox"] == set(outbox_before)
        assert store.committed_outbox(unconfirmed_only=True) == []
        assert state["outbox"] == outbox_before
        assert (row["state"], row["reason"]) == (
            "INCONCLUSIVE",
            "REPLAY_MISMATCH",
        )
        assert row["device_protection"]["protected"] is True
        assert row["device_protection"]["authorization_blocked"] is True
        assert row["raw_quantity"]["balls"] == assignment_before[
            "raw_collected_balls"
        ]
        assert row["unload_quantity"]["balls"] in {
            None,
            assignment_before["unloaded_balls"],
        }
        assert [
            event["kind"]
            for event in task_events
            if event["kind"] in {"FAILED", "INCONCLUSIVE", "SUCCEEDED"}
        ] == ["FAILED"]
        assert resumed.device.view.executions_for(row["task_id"]) == 0
        assert resumed.device.journal.path == device_journal
    finally:
        resumed.close()


def test_restart_protected_execution_blocks_unowned_live_handoff(
    tmp_path, demo_api, monkeypatch
):
    """A restart terminal cannot leave simulator work free to continue."""

    runtime = demo_api.CourseCollectionExecutionDemo(
        tmp_path, initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    runtime.ensure_execution()
    first = runtime.advance_once()
    session_root = runtime.session_root
    config = runtime.config
    device_journal = runtime.device.journal.path
    running = first["session"]
    snapshot = runtime.collection_executions()
    row = snapshot["executions"][0]
    execution_id = row["execution_id"]
    task_id = row["task_id"]
    progress = [
        record
        for record in runtime.device.journal.read()
        if record.record_kind == "task_event_persisted"
        and record.payload["event"]["task_id"] == task_id
        and record.payload["event"]["kind"] == "PROGRESS"
    ]
    assert running["now_sim_t_s"] == 30000
    assert row["state"] == "RUNNING"
    assert runtime.device.view.current_task_id == task_id
    assert runtime.device.view.tasks[task_id].execution_started is True
    assert runtime.device.view.tasks[task_id].terminal_kind is None
    assert progress

    class Crash(RuntimeError):
        pass

    def crash_after_prepare(boundary, _payload):
        if boundary == "after_prepare":
            raise Crash(boundary)

    try:
        with pytest.raises(Crash, match="after_prepare"):
            course_session_v3.run(
                session_root, crash_hook=crash_after_prepare
            )
    finally:
        runtime.close()

    identity = read_record(session_root / "identity.json")
    store = demo_api.execution_api.CollectionExecutionStore(
        session_root / "collection-execution.jsonl",
        identity,
        policy_id=course_session_v3.POLICY_ID,
    )
    pending = store.recovery_state()
    assert pending["status"] == "PREPARED_NO_COMMIT"
    assert pending["pending_prepared"]["decision"]["execution_id"] == execution_id
    assert pending["pending_prepared"]["decision"]["selected_action"]["name"] == (
        "SendToHandoff"
    )
    pending_handoff_index = pending["pending_prepared"]["decision"][
        "selected_action"
    ]["index"]

    restarted = demo_api.SimulatorBackedTaskDevice(
        session_root,
        config,
        demo_api.ROBOT_ID,
        journal_path=device_journal,
        initialize=False,
    )
    restarted.start()

    public = course_session_v3.read_execution_snapshot(
        session_root, server_time_utc="2035-01-02T03:04:05Z"
    )
    protected = public["executions"][0]
    assert (protected["state"], protected["reason"]) == (
        "INCONCLUSIVE",
        "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME",
    )
    assert protected["device_protection"] == {
        "protected": True,
        "reasons": ["RESTART_UNKNOWN"],
        "authorization_blocked": True,
    }
    assert protected["raw_quantity"]["balls"] is None
    assert protected["unload_quantity"]["balls"] is None
    assert protected["success_display_allowed"] is False
    assert store.recovery_state()["status"] == "NO_PREPARED"

    plan_before = store.replay_plan()
    last_assignment = plan_before[-1]["committed"]["result"][
        "runtime_snapshots"
    ][execution_id]
    assert last_assignment["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
    assert last_assignment["terminal_reason"] is None
    outbox_before = store.replay()["outbox"]
    durable_before = {
        str(path.relative_to(session_root)): path.read_bytes()
        for path in session_root.rglob("*")
        if path.is_file()
    }

    replayed_actions = []
    original_step = course_session_v3.RangeOpsEnv.step

    def counted_step(env, action):
        replayed_actions.append(action)
        return original_step(env, action)

    monkeypatch.setattr(course_session_v3.RangeOpsEnv, "step", counted_step)

    with pytest.raises(
        course_session_v3.ReplayMismatch,
        match="protected execution still has a live simulator assignment",
    ):
        course_session_v3.run(session_root)

    assert replayed_actions == [
        item["prepared"]["decision"]["selected_action"]["index"]
        for item in plan_before
    ]
    assert pending_handoff_index not in replayed_actions
    assert store.replay_plan() == plan_before
    assert store.replay()["outbox"] == outbox_before
    assert {
        str(path.relative_to(session_root)): path.read_bytes()
        for path in session_root.rglob("*")
        if path.is_file()
    } == durable_before
