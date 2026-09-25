"""Continuous V4 composition, recovery, and single-driver contracts."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import threading

import pytest

from nxt_edge_task.schedules import ScheduleService
from nxt_edge_task.journal import JsonlJournal
from nxt_range_ops.core.sim import RangeSimulation
from nxt_site_agent import SiteAgentApiServer, SiteAgentError
from scripts import course_collection_execution as execution_api
from scripts import course_collection_execution_v4_service as v4_service
from scripts import course_session_v3
from scripts.course_collection_execution_demo import planning_requests
from scripts.course_collection_execution_service import (
    CollectionExecutionServiceRuntime,
)
from scripts.course_session_task_device import SimulatorBackedTaskDevice
from scripts.course_collection_execution_v4_service import (
    ContinuousCollectionExecutionRuntime,
    classify_continuous_runtime,
)


class WallClock:
    def __init__(self) -> None:
        self.value = datetime(2035, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


TERMINAL_EXECUTIONS = {
    "SUCCEEDED",
    "PARTIAL",
    "REJECTED",
    "MISSED",
    "FAILED",
    "INCONCLUSIVE",
}
TWO_TASK_ACTIVE_WITNESS = (
    Path(__file__).resolve().parents[1]
    / "fixtures/continuous-collection-v4/two-task-active.json"
)


def call(server, method, path, body=None):
    connection = http.client.HTTPConnection(server.host, server.port, timeout=10)
    try:
        headers = {} if body is None else {"Content-Type": "application/json"}
        connection.request(
            method,
            path,
            body=None if body is None else json.dumps(body),
            headers=headers,
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def serve(runtime, launch, root):
    service = launch(root / "site-agent")
    server = SiteAgentApiServer(service, **runtime.api_callbacks())
    server.start_background()
    return service, server


def planning_chain_requests(
    revision: int,
    start_at_utc: str,
    *,
    inventory_clean_balls: int | None = None,
):
    source, plan, confirmation = deepcopy(planning_requests())
    suffix = f"{revision:03d}"
    source["request_id"] = f"continuous-input-{suffix}"
    source["expected_revision"] = revision - 1
    source["reason"] = f"Continuous input revision {revision}."
    source["inventory_clean_balls"]["source_ref"] = (
        f"continuous-clean-bin-{suffix}"
    )
    if inventory_clean_balls is not None:
        source["inventory_clean_balls"]["value"] = inventory_clean_balls
    elif revision > 1:
        source["inventory_clean_balls"]["value"] = 3000
    plan["request_id"] = f"continuous-plan-{suffix}"
    plan["input_revision"] = revision
    plan["selection"]["start_at_utc"] = start_at_utc
    plan["reason"] = f"Continuous collection plan {revision}."
    confirmation["request_id"] = f"continuous-confirmation-{suffix}"
    return source, plan, confirmation


def closing_horizon_planning_requests(start_at_utc: str):
    """Build valid all-day owner input for an exact V3 close boundary."""

    source, plan, confirmation = planning_chain_requests(
        1, start_at_utc, inventory_clean_balls=10000
    )
    end = "2026-09-16T18:00:00Z"
    source["valid_until_utc"] = end
    source["operating_window"]["end_at_utc"] = end
    for field in (
        "inventory_clean_balls",
        "demand",
        "safety_stock_balls",
        "buffer_minutes",
        "operations_allowed",
        "washer_available",
    ):
        source[field]["valid_until_utc"] = end
    for evidence in source["zones"][0].values():
        if isinstance(evidence, dict) and "valid_until_utc" in evidence:
            evidence["valid_until_utc"] = end
    demand = source["demand"]["value"]
    demand["low"] = [5] * 60
    demand["typical"] = [8] * 60
    demand["high"] = [10] * 60
    plan["valid_until_utc"] = end
    return source, plan, confirmation


def post_planning_chain(
    server,
    *,
    revision: int,
    start_at_utc: str,
    inventory_clean_balls: int | None = None,
):
    source, plan_request, confirmation_request = planning_chain_requests(
        revision,
        start_at_utc,
        inventory_clean_balls=inventory_clean_balls,
    )
    status, payload = call(server, "POST", "/api/v1/planning/inputs", source)
    assert status == 200, payload
    input_receipt = payload["data"]
    status, payload = call(
        server, "POST", "/api/v1/planning/plans", plan_request
    )
    assert status == 200, payload
    plan_receipt = payload["data"]
    confirmation_request["plan_id"] = plan_receipt["record"]["plan_id"]
    status, payload = call(
        server,
        "POST",
        "/api/v1/planning/confirmations",
        confirmation_request,
    )
    assert status == 200, payload
    confirmation_receipt = payload["data"]
    return {
        "record": confirmation_receipt["record"],
        "requests": (source, plan_request, confirmation_request),
        "receipts": (input_receipt, plan_receipt, confirmation_receipt),
    }


def post_planning_chain_route(
    runtime,
    *,
    revision: int,
    start_at_utc: str,
    inventory_clean_balls: int | None = None,
):
    source, plan_request, confirmation_request = planning_chain_requests(
        revision,
        start_at_utc,
        inventory_clean_balls=inventory_clean_balls,
    )
    input_receipt = runtime.route_planning(
        "POST", "/api/v1/planning/inputs", source
    )
    plan_receipt = runtime.route_planning(
        "POST", "/api/v1/planning/plans", plan_request
    )
    confirmation_request["plan_id"] = plan_receipt["record"]["plan_id"]
    confirmation_receipt = runtime.route_planning(
        "POST", "/api/v1/planning/confirmations", confirmation_request
    )
    return {
        "record": confirmation_receipt["record"],
        "requests": (source, plan_request, confirmation_request),
        "receipts": (input_receipt, plan_receipt, confirmation_receipt),
    }


def execution_for_confirmation(snapshot, confirmation):
    binding = next(
        row
        for row in snapshot["bindings"]
        if row["confirmation_id"] == confirmation["confirmation_id"]
    )
    return next(
        row
        for row in snapshot["executions"]
        if row["binding_id"] == binding["binding_id"]
    )


def advance_until(runtime, predicate, *, maximum_ticks: int):
    snapshot = runtime.collection_executions()
    for _ in range(maximum_ticks):
        try:
            if predicate(snapshot):
                return snapshot
        except StopIteration:
            pass
        runtime.tick()
        snapshot = runtime.collection_executions()
    pytest.fail(f"predicate did not become true; final snapshot={snapshot!r}")


def ledger_state(runtime):
    with course_session_v3._lock(runtime.session_root / ".session.lock"):
        session, _saved, _control = course_session_v3._validated_read_runtime(
            runtime.session_root
        )
        counts = session.env.sim.ledger.counts()
        return session.env.sim.ledger.total, counts


def reconstructed_v3_prefix(root: Path) -> dict:
    """Read the causal simulator prefix without advancing or writing it."""

    with course_session_v3._lock(root / ".session.lock"):
        session, _saved, _control = course_session_v3._existing_runtime(root)
        plan, recovery = session._verify_prefix_unlocked()
        return {
            "tick_sequence": recovery["tick_sequence"],
            "replay_digest": recovery["replay_digest"],
            "now_sim_t_s": session.env.sim.now,
            "replay_plan": plan,
            "ledger_total": session.env.sim.ledger.total,
            "ledger_counts": session.env.sim.ledger.counts(),
        }


def post_confirmation(runtime: ContinuousCollectionExecutionRuntime) -> dict:
    source, request, confirmation = planning_requests()
    runtime.route_planning("POST", "/api/v1/planning/inputs", source)
    plan = runtime.route_planning(
        "POST", "/api/v1/planning/plans", request
    )["record"]
    confirmation["plan_id"] = plan["plan_id"]
    return runtime.route_planning(
        "POST", "/api/v1/planning/confirmations", confirmation
    )["record"]


def evidence_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in {".collection-execution.lock", ".session.lock"}
    }


def bind_dispatched_task(runtime: ContinuousCollectionExecutionRuntime) -> dict:
    assert runtime.planning is not None
    runtime.planning.recover()
    runtime.planning.schedules.tick(runtime.simulation_clock())
    planning = runtime.planning.execution_binding_snapshot(runtime.simulation_clock())
    task_id = planning["confirmations"][0]["task_id"]
    assert task_id is not None
    with course_session_v3.execution_admission(runtime.session_root) as admission:
        return admission.store.bind_confirmed_task(
            planning,
            runtime.edge_journal.read(),
            admission.identity,
            admission.now_sim_t_s,
            task_id=task_id,
        )


def execution_row(state: str, *, protected: bool = False, blocked: bool = False):
    return {
        "state": state,
        "device_protection": {
            "protected": protected,
            "authorization_blocked": blocked,
        },
    }


def test_v4_marker_is_isolated_from_fixed_3c_root(tmp_path):
    fixed = tmp_path / "fixed"
    fixed_runtime = CollectionExecutionServiceRuntime(
        fixed, initialize=True, wall_clock=WallClock()
    )
    fixed_runtime.start()
    fixed_runtime.close()

    with pytest.raises(RuntimeError, match="continuous service identity"):
        ContinuousCollectionExecutionRuntime(
            fixed, initialize=False, wall_clock=WallClock()
        ).start()

    continuous = tmp_path / "continuous"
    runtime = ContinuousCollectionExecutionRuntime(
        continuous, initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    try:
        marker = json.loads((continuous / "continuous-service.json").read_text())
        assert marker == {
            "schema": "nxt-course-continuous-collection-service/v1",
            "environment": "SIMULATION",
        }
    finally:
        runtime.close()

    with pytest.raises(RuntimeError, match="demo identity"):
        CollectionExecutionServiceRuntime(
            continuous, initialize=False, wall_clock=WallClock()
        ).start()


def test_initialize_rejects_nonempty_root_and_process_lock_is_unique(tmp_path):
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "existing-evidence").write_text("keep")
    refused = ContinuousCollectionExecutionRuntime(
        nonempty, initialize=True, wall_clock=WallClock()
    )
    with pytest.raises(RuntimeError, match="empty output directory"):
        refused.start()
    assert not (nonempty / "continuous-service.json").exists()

    root = tmp_path / "owned"
    owner = ContinuousCollectionExecutionRuntime(
        root, initialize=True, wall_clock=WallClock()
    )
    contender = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )
    owner.start()
    try:
        with pytest.raises(RuntimeError, match="owns this root"):
            contender.start()
    finally:
        contender.close()
        owner.close()


@pytest.mark.parametrize(
    ("runtime_status", "executions", "expected"),
    [
        ({"session_state": "ACTIVE"}, [], "RUNNING"),
        (
            {"session_state": "ACTIVE"},
            [execution_row("SUCCEEDED")],
            "RUNNING",
        ),
        ({"session_state": "PAUSED"}, [], "PAUSED"),
        (
            {"session_state": "PAUSED"},
            [execution_row("PENDING", protected=True)],
            "PROTECTED",
        ),
        (
            {"session_state": "PAUSED"},
            [execution_row("REJECTED", blocked=True)],
            "PROTECTED",
        ),
        (
            {"session_state": "ACTIVE"},
            [execution_row("PENDING", protected=True)],
            "PROTECTED",
        ),
        (
            {"session_state": "ACTIVE"},
            [execution_row("REJECTED", blocked=True)],
            "PROTECTED",
        ),
        (
            {"session_state": "ENDED"},
            [execution_row("SUCCEEDED"), execution_row("MISSED")],
            "ENDED",
        ),
        (
            {"session_state": "ENDED", "paused": True},
            [execution_row("SUCCEEDED")],
            "ENDED",
        ),
    ],
)
def test_classifier_keeps_session_lifecycle_separate_from_execution_history(
    runtime_status, executions, expected
):
    assert classify_continuous_runtime(runtime_status, executions) == expected


def test_classifier_fails_closed_when_ended_session_has_nonterminal_execution():
    with pytest.raises(RuntimeError, match="ended with nonterminal"):
        classify_continuous_runtime(
            {"session_state": "ENDED"}, [execution_row("RUNNING")]
        )


def test_only_one_background_driver_can_start(tmp_path):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous",
        initialize=True,
        wall_clock=WallClock(),
        step_interval_s=60,
    )
    runtime.start()
    try:
        assert runtime.driver_state == "RUNNING"
        assert runtime.start_driver() is True
        thread = runtime._thread
        assert thread is not None and thread.is_alive()
        assert thread.name == "continuous-collection-execution-driver"
        with pytest.raises(RuntimeError, match="already started"):
            runtime.start_driver()
        assert runtime._thread is thread
    finally:
        runtime.close()


def test_tick_uses_exact_owner_order_and_exits_admission_before_device(
    tmp_path, monkeypatch
):
    hook_calls = []

    def crash_hook(boundary, payload):
        hook_calls.append((boundary, payload))

    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous",
        initialize=True,
        wall_clock=WallClock(),
        crash_hook=crash_hook,
    )
    runtime.start()
    post_confirmation(runtime)
    assert runtime.planning is not None and runtime.device is not None

    trace = []
    admission_depth = 0
    original_admission = course_session_v3.execution_admission
    original_bind = execution_api.CollectionExecutionStore.bind_confirmed_task
    original_classify = runtime._classify_unlocked
    original_recover = runtime.planning.recover
    original_schedule_tick = runtime.planning.schedules.tick
    original_admit = runtime.device.admit
    original_consume = runtime.device.consume_committed
    original_publish_events = runtime._publish_pending_events
    original_publish_status = runtime._publish_status
    original_run = course_session_v3.run
    consume_count = publish_events_count = publish_status_count = run_count = 0

    @contextmanager
    def tracked_admission(root):
        nonlocal admission_depth
        with original_admission(root) as admission:
            admission_depth += 1
            try:
                yield admission
            finally:
                admission_depth -= 1

    def tracked_bind(store, *args, **kwargs):
        trace.append("bind")
        return original_bind(store, *args, **kwargs)

    def tracked_classify():
        trace.append("classify")
        return original_classify()

    def tracked_recover():
        trace.append("planning.recover")
        return original_recover()

    def tracked_schedule_tick(now):
        trace.append("schedules.tick")
        return original_schedule_tick(now)

    def tracked_admit(task, request, *, crash_hook=None):
        assert admission_depth == 0
        assert crash_hook is runtime.crash_hook
        trace.append("device.admit")
        return original_admit(task, request, crash_hook=crash_hook)

    def tracked_consume(*, crash_hook=None, limit=None):
        nonlocal consume_count
        assert crash_hook is runtime.crash_hook
        consume_count += 1
        trace.append(
            "device.consume_committed.before"
            if consume_count == 1
            else "device.consume_committed.after"
        )
        return original_consume(crash_hook=crash_hook, limit=limit)

    def tracked_publish_events():
        nonlocal publish_events_count
        publish_events_count += 1
        trace.append(
            "publish.events.before"
            if publish_events_count == 1
            else "publish.events.after"
        )
        return original_publish_events()

    def tracked_publish_status():
        nonlocal publish_status_count
        publish_status_count += 1
        trace.append(
            "publish.status.before"
            if publish_status_count == 1
            else "publish.status.after"
        )
        return original_publish_status()

    def tracked_run(*args, **kwargs):
        nonlocal run_count
        assert kwargs["crash_hook"] is runtime.crash_hook
        run_count += 1
        trace.append("course_session_v3.run")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(course_session_v3, "execution_admission", tracked_admission)
    monkeypatch.setattr(
        execution_api.CollectionExecutionStore,
        "bind_confirmed_task",
        tracked_bind,
    )
    monkeypatch.setattr(runtime, "_classify_unlocked", tracked_classify)
    monkeypatch.setattr(runtime.planning, "recover", tracked_recover)
    monkeypatch.setattr(runtime.planning.schedules, "tick", tracked_schedule_tick)
    monkeypatch.setattr(runtime.device, "admit", tracked_admit)
    monkeypatch.setattr(runtime.device, "consume_committed", tracked_consume)
    monkeypatch.setattr(runtime, "_publish_pending_events", tracked_publish_events)
    monkeypatch.setattr(runtime, "_publish_status", tracked_publish_status)
    monkeypatch.setattr(course_session_v3, "run", tracked_run)

    try:
        runtime.tick()
        assert runtime.failure is None
        assert run_count == 1
        assert trace == [
            "classify",
            "planning.recover",
            "schedules.tick",
            "bind",
            "device.admit",
            "device.consume_committed.before",
            "publish.events.before",
            "publish.status.before",
            "course_session_v3.run",
            "device.consume_committed.after",
            "publish.events.after",
            "publish.status.after",
            "classify",
        ]
        assert runtime.collection_executions()["executions"]
        assert hook_calls
    finally:
        runtime.close()


def test_start_keeps_reads_closed_until_reconciliation_finishes(tmp_path, monkeypatch):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous", initialize=True, wall_clock=WallClock()
    )
    original_publish_status = runtime._publish_status
    observations = []

    def guarded_publish_status():
        observations.append(runtime.started)
        with pytest.raises(SiteAgentError, match="not running"):
            runtime.collection_executions()
        return original_publish_status()

    monkeypatch.setattr(runtime, "_publish_status", guarded_publish_status)
    runtime.start()
    try:
        assert observations == [False, False, False]
        assert runtime.started is True
        assert runtime.collection_executions()["executions"] == []
    finally:
        runtime.close()

    failed = ContinuousCollectionExecutionRuntime(
        tmp_path / "failed", initialize=True, wall_clock=WallClock()
    )

    def fail_status():
        raise RuntimeError("injected startup failure")

    monkeypatch.setattr(failed, "_publish_status", fail_status)
    with pytest.raises(RuntimeError, match="injected startup failure"):
        failed.start()
    assert failed.started is False
    assert failed._process_lock is None
    assert failed.device is None
    with pytest.raises(SiteAgentError, match="not running"):
        failed.collection_executions()


def test_paused_restart_is_read_only_and_does_not_bind_new_task(tmp_path, monkeypatch):
    root = tmp_path / "continuous"
    initial = ContinuousCollectionExecutionRuntime(
        root, initialize=True, wall_clock=WallClock()
    )
    initial.start()
    post_confirmation(initial)
    assert initial.planning is not None
    initial.planning.recover()
    initial.planning.schedules.tick(initial.simulation_clock())
    assert initial.planning.snapshot(initial.simulation_clock())["confirmations"][0][
        "task_id"
    ]
    with course_session_v3.execution_admission(initial.session_root) as admission:
        assert admission.store.replay()["bindings"] == {}
    initial.close()
    course_session_v3.set_paused(root / "session-v3", True)

    restarted = ContinuousCollectionExecutionRuntime(
        root,
        initialize=False,
        wall_clock=WallClock(),
        step_interval_s=60,
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read-only paused startup advanced an owner")

    monkeypatch.setattr(ScheduleService, "tick", forbidden)
    monkeypatch.setattr(restarted, "_materialize_new_tasks_unlocked", forbidden)
    monkeypatch.setattr(course_session_v3, "run", forbidden)
    restarted.start()
    try:
        assert restarted.driver_state == "PAUSED"
        assert restarted.collection_executions()["executions"] == []
        with course_session_v3.execution_admission(restarted.session_root) as admission:
            assert admission.store.replay()["bindings"] == {}
        assert restarted.start_driver() is True
    finally:
        restarted.close()


def test_restart_resumes_only_preexisting_binding_with_stable_request_id(tmp_path):
    root = tmp_path / "continuous"
    initial = ContinuousCollectionExecutionRuntime(
        root, initialize=True, wall_clock=WallClock()
    )
    initial.start()
    post_confirmation(initial)
    binding = bind_dispatched_task(initial)
    with course_session_v3.execution_admission(initial.session_root) as admission:
        state = admission.store.replay()
        assert len(state["bindings"]) == 1
        assert state["requests"] == {}
    initial.close()

    restarted = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )
    restarted.start()
    try:
        snapshot = restarted.collection_executions()
        assert len(snapshot["bindings"]) == 1
        assert snapshot["bindings"][0]["binding_id"] == binding["binding_id"]
        assert len(snapshot["requests"]) == 1
        assert snapshot["requests"][0]["request_id"] == (
            execution_api.request_id_for_binding(binding["binding_id"])
        )
        assert len(snapshot["executions"]) == 1
        assert snapshot["executions"][0]["state"] == "PENDING"
        assert snapshot["executions"][0]["edge_evidence"]["accepted"] is True
        assert restarted.driver_state == "RUNNING"
    finally:
        restarted.close()


def test_confirmation_gate_maps_execution_conflict_before_commit(tmp_path, monkeypatch):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    source, request, confirmation = planning_requests()
    runtime.route_planning("POST", "/api/v1/planning/inputs", source)
    plan = runtime.route_planning(
        "POST", "/api/v1/planning/plans", request
    )["record"]
    confirmation["plan_id"] = plan["plan_id"]

    def reject(**_kwargs):
        raise execution_api.CollectionExecutionError("conflict", "injected horizon")

    monkeypatch.setattr(execution_api, "derive_execution_requirements", reject)
    try:
        with pytest.raises(SiteAgentError) as raised:
            runtime.route_planning(
                "POST", "/api/v1/planning/confirmations", confirmation
            )
        assert raised.value.code == "planning_conflict"
        assert "injected horizon" in raised.value.detail
        assert runtime.planning_snapshot()["confirmations"] == []
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("start_at_utc", "accepted"),
    [
        ("2026-09-16T17:40:00Z", True),
        ("2026-09-16T17:40:01Z", False),
    ],
)
def test_confirmation_horizon_accepts_exact_end_and_rejects_one_second_past(
    tmp_path, start_at_utc, accepted
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / start_at_utc[-9:-1].replace(":", "-"),
        initialize=True,
        wall_clock=WallClock(),
    )
    runtime.start()
    source, plan_request, confirmation = closing_horizon_planning_requests(
        start_at_utc
    )
    try:
        runtime.route_planning("POST", "/api/v1/planning/inputs", source)
        plan = runtime.route_planning(
            "POST", "/api/v1/planning/plans", plan_request
        )["record"]
        assert plan["status"] == "READY"
        confirmation["plan_id"] = plan["plan_id"]
        if accepted:
            receipt = runtime.route_planning(
                "POST", "/api/v1/planning/confirmations", confirmation
            )
            assert receipt["record"]["confirmation_id"]
            assert len(runtime.planning_snapshot()["confirmations"]) == 1
            # HTTP confirmation persists only the Planning owner's record;
            # schedule recovery remains in the serialized driver pass.
            assert runtime.task_operations_snapshot()["schedules"] == []
        else:
            with pytest.raises(SiteAgentError) as raised:
                runtime.route_planning(
                    "POST", "/api/v1/planning/confirmations", confirmation
                )
            assert raised.value.code == "planning_conflict"
            assert "session horizon" in raised.value.detail
            assert runtime.planning_snapshot()["confirmations"] == []
            assert runtime.task_operations_snapshot()["schedules"] == []
        snapshot = runtime.collection_executions()
        assert snapshot["bindings"] == []
        assert snapshot["requests"] == []
        assert snapshot["executions"] == []
        assert not any(
            record.record_kind == "task_created"
            for record in runtime.edge_journal.read()
        )
    finally:
        runtime.close()


def test_missing_runtime_mapping_rejects_before_confirmation_append(
    tmp_path, monkeypatch
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "missing-mapping", initialize=True, wall_clock=WallClock()
    )
    original_construct = runtime._construct_planning_with_horizon_gate
    original_admission = course_session_v3.execution_admission

    def construct_with_missing_mapping():
        @contextmanager
        def admission_without_mapping(root):
            with original_admission(root) as admission:
                identity = deepcopy(admission.identity)
                identity["runtime_bindings"] = []
                yield course_session_v3.ExecutionAdmission(
                    store=admission.store,
                    identity=identity,
                    now_sim_t_s=admission.now_sim_t_s,
                )

        monkeypatch.setattr(
            course_session_v3, "execution_admission", admission_without_mapping
        )
        try:
            original_construct()
        finally:
            monkeypatch.setattr(
                course_session_v3, "execution_admission", original_admission
            )

    monkeypatch.setattr(
        runtime,
        "_construct_planning_with_horizon_gate",
        construct_with_missing_mapping,
    )
    runtime.start()
    source, plan_request, confirmation = planning_chain_requests(
        1, "2026-09-16T08:10:00Z"
    )
    try:
        runtime.route_planning("POST", "/api/v1/planning/inputs", source)
        plan = runtime.route_planning(
            "POST", "/api/v1/planning/plans", plan_request
        )["record"]
        confirmation["plan_id"] = plan["plan_id"]
        with pytest.raises(SiteAgentError) as raised:
            runtime.route_planning(
                "POST", "/api/v1/planning/confirmations", confirmation
            )
        assert raised.value.code == "planning_conflict"
        assert raised.value.detail == "unique explicit robot/zone binding required"
        assert runtime.planning_snapshot()["confirmations"] == []
        assert runtime.task_operations_snapshot()["schedules"] == []
        snapshot = runtime.collection_executions()
        assert snapshot["bindings"] == snapshot["requests"] == []
        assert snapshot["executions"] == []
        assert not any(
            record.record_kind == "task_created"
            for record in runtime.edge_journal.read()
        )
    finally:
        runtime.close()


def test_unexpected_tick_failure_latches_and_is_never_retried(tmp_path, monkeypatch):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    assert runtime.planning is not None
    calls = []

    def fail_recover():
        calls.append("recover")
        raise RuntimeError("injected driver failure")

    monkeypatch.setattr(runtime.planning, "recover", fail_recover)
    try:
        runtime.tick()
        assert calls == ["recover"]
        assert runtime.driver_state == "FAILED"
        assert runtime.failure == "RuntimeError: injected driver failure"
        runtime.tick()
        assert calls == ["recover"]
        assert runtime.collection_executions()["executions"] == []
        assert runtime.start_driver() is False
    finally:
        runtime.close()


def test_get_helpers_are_pure_verified_projections_with_v2_capabilities(tmp_path):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    try:
        before = evidence_bytes(runtime.root)
        assert runtime.collection_executions()["schema"] == (
            "nxt-collection-executions/v1"
        )
        assert runtime.planning_snapshot()["schema"] == "nxt-planning/v1"
        task_ops = runtime.task_operations_snapshot()
        assert task_ops["service_capabilities"] == {
            "schema": "nxt-pilot-dispatch/service-capabilities/v2",
            "mode": "CONTINUOUS_V3_EXECUTION",
            "operations": {
                "planning_inputs_create": "SUPPORTED",
                "planning_plans_create": "SUPPORTED",
                "planning_confirmations_create": "SUPPORTED",
                "planning_outcomes_create": "SUPPORTED",
                "schedules_create": "UNAVAILABLE",
                "schedules_cancel": "SUPPORTED",
                "notifications_acknowledge": "SUPPORTED",
                "notifications_resolve": "SUPPORTED",
            },
        }
        callbacks = runtime.api_callbacks()
        assert set(callbacks) == {
            "task_operations",
            "planning_operations",
            "collection_executions",
            "collection_execution_request",
            "collection_execution_parser",
        }
        assert evidence_bytes(runtime.root) == before
    finally:
        runtime.close()


def test_real_http_accepts_second_confirmation_while_first_runs_and_executes_both(
    tmp_path, launch
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    try:
        initial_total, initial_counts = ledger_state(runtime)
        first_chain = post_planning_chain(
            server, revision=1, start_at_utc="2026-09-16T08:10:00Z"
        )
        first = first_chain["record"]
        running = advance_until(
            runtime,
            lambda snapshot: execution_for_confirmation(snapshot, first)[
                "state"
            ]
            == "RUNNING",
            maximum_ticks=20,
        )
        assert execution_for_confirmation(running, first)["raw_quantity"][
            "balls"
        ] > 0

        second_chain = post_planning_chain(
            server, revision=2, start_at_utc="2026-09-16T08:40:00Z"
        )
        second = second_chain["record"]

        for chain in (first_chain, second_chain):
            for path, body, receipt in zip(
                (
                    "/api/v1/planning/inputs",
                    "/api/v1/planning/plans",
                    "/api/v1/planning/confirmations",
                ),
                chain["requests"],
                chain["receipts"],
                strict=True,
            ):
                status, replayed = call(server, "POST", path, body)
                assert status == 200
                assert replayed["data"] == {
                    **receipt,
                    "disposition": "duplicate",
                }

        changed = deepcopy(second_chain["requests"][0])
        changed["reason"] = "different content under one request ID"
        before_conflict = evidence_bytes(runtime.root)
        status, conflict = call(
            server, "POST", "/api/v1/planning/inputs", changed
        )
        assert status == 409
        assert conflict["error"]["code"] == "planning_conflict"
        assert evidence_bytes(runtime.root) == before_conflict

        snapshot = advance_until(
            runtime,
            lambda value: len(value["executions"]) == 2
            and all(
                row["state"] in TERMINAL_EXECUTIONS
                for row in value["executions"]
            ),
            maximum_ticks=60,
        )
        executions = snapshot["executions"]
        assert len(executions) == 2
        first_record = execution_for_confirmation(snapshot, first)
        second_record = execution_for_confirmation(snapshot, second)
        assert first_record["execution_id"] != second_record["execution_id"]
        assert first_record["terminal_sim_t_s"] <= second_record[
            "started_sim_t_s"
        ]
        assert sum(row["state"] == "RUNNING" for row in executions) <= 1
        assert first_record["raw_quantity"]["balls"] > 0
        assert second_record["raw_quantity"]["balls"] > 0
        assert runtime.driver_state == "RUNNING"

        planning_status, planning_payload = call(
            server, "GET", "/api/v1/planning"
        )
        task_ops_status, task_ops_payload = call(
            server, "GET", "/api/v0/task-ops"
        )
        assert planning_status == task_ops_status == 200
        planning = planning_payload["data"]
        task_ops = task_ops_payload["data"]
        assert {row["confirmation_id"] for row in planning["confirmations"]} == {
            first["confirmation_id"],
            second["confirmation_id"],
        }
        assert len({row["schedule_id"] for row in task_ops["schedules"]}) == 2
        assert len({row["task_id"] for row in executions}) == 2
        assert len({row["binding_id"] for row in executions}) == 2
        assert len({row["request_id"] for row in executions}) == 2
        for row in executions:
            assert row["request_id"] == execution_api.request_id_for_binding(
                row["binding_id"]
            )

        with runtime._lock:
            counts_before = tuple(
                map(len, (snapshot["bindings"], snapshot["requests"], executions))
            )
            runtime._materialize_new_tasks_unlocked()
            runtime._materialize_new_tasks_unlocked()
            after_materialize = runtime._read_collection_executions_unlocked()
            assert tuple(
                map(
                    len,
                    (
                        after_materialize["bindings"],
                        after_materialize["requests"],
                        after_materialize["executions"],
                    ),
                )
            ) == counts_before

        final_total, final_counts = ledger_state(runtime)
        assert final_total == initial_total
        assert sum(final_counts.values()) == sum(initial_counts.values())
        assert sum(final_counts.values()) == final_total
        assert runtime.planning_snapshot()["outcomes"] == []

        before_gets = evidence_bytes(runtime.root)
        get_paths = [
            "/api/v1/planning",
            "/api/v0/task-ops",
            "/api/v1/collection-executions",
        ]
        get_paths.extend(
            f"/api/v1/planning/requests/{request['request_id']}"
            for chain in (first_chain, second_chain)
            for request in chain["requests"]
        )
        get_paths.extend(
            f"/api/v1/collection-executions/requests/{row['request_id']}"
            for row in executions
        )
        for _ in range(2):
            for path in get_paths:
                status, _payload = call(server, "GET", path)
                assert status == 200, (path, _payload)
        assert evidence_bytes(runtime.root) == before_gets

        status, direct = call(
            server,
            "POST",
            "/api/v0/task-ops/schedules",
            {
                "robot_id": "picker-01",
                "zone_id": "Z1",
                "due_at_utc": "2026-09-16T08:50:00Z",
                "expires_at_utc": "2026-09-16T08:55:00Z",
                "operator": "manager",
            },
        )
        assert status == 409
        assert direct["error"]["code"] == "task_ops_conflict"
        assert direct["error"]["detail"] == (
            "direct schedule creation is not installed; confirm a Planning plan"
        )
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_two_task_active_fixture_regenerates_from_exact_http_data(
    tmp_path, launch
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "witness", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path / "witness-http")
    try:
        first = post_planning_chain(
            server, revision=1, start_at_utc="2026-09-16T08:10:00Z"
        )["record"]
        advance_until(
            runtime,
            lambda snapshot: execution_for_confirmation(snapshot, first)["state"]
            == "RUNNING",
            maximum_ticks=3,
        )
        second = post_planning_chain(
            server,
            revision=2,
            start_at_utc="2026-09-16T08:40:00Z",
            inventory_clean_balls=3000,
        )["record"]
        expected = advance_until(
            runtime,
            lambda snapshot: (
                execution_for_confirmation(snapshot, first)["state"]
                in TERMINAL_EXECUTIONS
                and execution_for_confirmation(snapshot, second)["state"]
                in {"PENDING", "RUNNING"}
                and snapshot["session_state"] == "ACTIVE"
            ),
            maximum_ticks=6,
        )
        status, response = call(
            server, "GET", "/api/v1/collection-executions"
        )
        assert status == 200
        assert response["data"] == expected
        rows = {
            row["execution_id"]: row for row in response["data"]["executions"]
        }
        first_row = execution_for_confirmation(response["data"], first)
        second_row = execution_for_confirmation(response["data"], second)
        assert rows[first_row["execution_id"]]["state"] in TERMINAL_EXECUTIONS
        assert rows[second_row["execution_id"]]["state"] in {
            "PENDING",
            "RUNNING",
        }
        canonical = (
            json.dumps(
                response["data"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode()
        assert TWO_TASK_ACTIVE_WITNESS.read_bytes() == canonical
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_pending_cancel_delegates_to_owner_survives_restart_and_dispatch_wins(
    tmp_path, launch
):
    pending_root = tmp_path / "pending"
    pending = ContinuousCollectionExecutionRuntime(
        pending_root, initialize=True, wall_clock=WallClock()
    )
    pending.start()
    service, server = serve(pending, launch, tmp_path / "pending-http")
    try:
        chain = post_planning_chain(
            server,
            revision=1,
            start_at_utc="2026-09-16T08:40:00Z",
            inventory_clean_balls=3000,
        )
        pending.tick()
        status, payload = call(server, "GET", "/api/v0/task-ops")
        assert status == 200
        schedule = payload["data"]["schedules"][0]
        assert schedule["status"] == "SCHEDULED"
        status, cancelled = call(
            server,
            "POST",
            f"/api/v0/task-ops/schedules/{schedule['schedule_id']}/cancel",
            {"operator": "course-manager"},
        )
        assert status == 200
        assert cancelled["data"]["schedule"]["status"] == "CANCELLED"
        assert cancelled["data"]["schedule"]["task_id"] is None
        for _ in range(4):
            pending.tick()
        assert pending.collection_executions()["executions"] == []
        assert chain["record"]["confirmation_id"]
    finally:
        server.shutdown()
        service.stop()
        pending.close()

    reopened = ContinuousCollectionExecutionRuntime(
        pending_root, initialize=False, wall_clock=WallClock()
    )
    reopened.start()
    try:
        row = reopened.task_operations_snapshot()["schedules"][0]
        assert row["status"] == "CANCELLED"
        assert row["task_id"] is None
        assert reopened.collection_executions()["executions"] == []
    finally:
        reopened.close()

    dispatched = ContinuousCollectionExecutionRuntime(
        tmp_path / "dispatched", initialize=True, wall_clock=WallClock()
    )
    dispatched.start()
    service, server = serve(dispatched, launch, tmp_path / "dispatched-http")
    try:
        post_planning_chain(
            server, revision=1, start_at_utc="2026-09-16T08:10:00Z"
        )
        dispatched.tick()
        schedule = dispatched.task_operations_snapshot()["schedules"][0]
        assert schedule["status"] == "DISPATCHED"
        before = evidence_bytes(dispatched.root)
        status, conflict = call(
            server,
            "POST",
            f"/api/v0/task-ops/schedules/{schedule['schedule_id']}/cancel",
            {"operator": "course-manager"},
        )
        assert status == 409
        assert conflict["error"]["code"] == "task_ops_conflict"
        assert evidence_bytes(dispatched.root) == before
        assert dispatched.collection_executions()["executions"][0][
            "edge_evidence"
        ]["accepted"] is True
    finally:
        server.shutdown()
        service.stop()
        dispatched.close()


def test_notification_actions_delegate_and_do_not_clear_execution_state(
    tmp_path, launch
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    try:
        first = post_planning_chain(
            server, revision=1, start_at_utc="2026-09-16T08:10:00Z"
        )["record"]
        running = advance_until(
            runtime,
            lambda snapshot: execution_for_confirmation(snapshot, first)[
                "state"
            ]
            == "RUNNING",
            maximum_ticks=4,
        )
        first_before = execution_for_confirmation(running, first)
        post_planning_chain(
            server,
            revision=2,
            start_at_utc="2026-09-16T08:20:00Z",
            inventory_clean_balls=3000,
        )
        runtime.tick()
        status, task_ops_payload = call(server, "GET", "/api/v0/task-ops")
        assert status == 200
        rejected = next(
            row
            for row in task_ops_payload["data"]["schedules"]
            if row["status"] == "REJECTED"
        )
        assert rejected["task_id"] is None
        notice = next(
            row
            for row in task_ops_payload["data"]["notifications"]
            if row["schedule_id"] == rejected["schedule_id"]
        )
        assert notice["status"] == "OPEN"
        assert notice["condition_active"] is False
        status, acknowledged = call(
            server,
            "POST",
            f"/api/v0/task-ops/notifications/{notice['notification_id']}/acknowledge",
            {"operator": "course-manager", "note": "Reviewed refusal."},
        )
        assert status == 200
        assert acknowledged["data"]["status"] == "acknowledged"
        status, resolved = call(
            server,
            "POST",
            f"/api/v0/task-ops/notifications/{notice['notification_id']}/resolve",
            {"operator": "course-manager", "note": "No task was admitted."},
        )
        assert status == 200
        assert resolved["data"]["status"] == "resolved"
        first_after = execution_for_confirmation(
            runtime.collection_executions(), first
        )
        assert first_after["task_id"] == first_before["task_id"]
        assert first_after["request_id"] == first_before["request_id"]
        execution_snapshot = runtime.collection_executions()
        assert len(execution_snapshot["bindings"]) == 1
        assert len(execution_snapshot["requests"]) == 1
        assert len(execution_snapshot["executions"]) == 1
        assert sum(
            record.record_kind == "task_created"
            for record in runtime.edge_journal.read()
        ) == 1
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_paused_accepts_planning_intent_without_advancing_business_time(
    tmp_path, launch
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    try:
        before = runtime.runtime_status()
        course_session_v3.set_paused(runtime.session_root, True)
        runtime.tick()
        assert runtime.driver_state == "PAUSED"
        chain = post_planning_chain(
            server,
            revision=1,
            start_at_utc="2026-09-16T08:40:00Z",
            inventory_clean_balls=3000,
        )
        runtime.tick()
        after = runtime.runtime_status()
        assert after["simulation_time_utc"] == before["simulation_time_utc"]
        assert runtime.collection_executions()["executions"] == []
        assert runtime.planning_snapshot()["confirmations"][0][
            "confirmation_id"
        ] == chain["record"]["confirmation_id"]
        task_ops = runtime.task_operations_snapshot()
        assert task_ops["scheduler"] == {"state": "RUNNING", "detail": None}
        assert task_ops["runtime"]["accepts_new_confirmations"] is True
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_failed_state_keeps_gets_readable_and_rejects_every_write_without_mutation(
    tmp_path, launch
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    try:
        runtime._fail_unlocked(RuntimeError("injected fail-stop"))
        before = evidence_bytes(runtime.root)
        assert call(server, "GET", "/api/v1/planning")[0] == 200
        status, task_ops = call(server, "GET", "/api/v0/task-ops")
        assert status == 200
        assert task_ops["data"]["scheduler"] == {
            "state": "FAILED",
            "detail": "RuntimeError: injected fail-stop",
        }
        assert task_ops["data"]["service_capabilities"]["mode"] == (
            "CONTINUOUS_V3_EXECUTION"
        )
        assert task_ops["data"]["runtime"]["accepts_new_confirmations"] is False
        for path, body, expected in (
            (
                "/api/v1/planning/inputs",
                planning_chain_requests(1, "2026-09-16T08:10:00Z")[0],
                "planning_unavailable",
            ),
            (
                "/api/v0/task-ops/schedules",
                {},
                "task_ops_unavailable",
            ),
            (
                "/api/v0/task-ops/schedules/unknown/cancel",
                {"operator": "course-manager"},
                "task_ops_unavailable",
            ),
            (
                "/api/v0/task-ops/notifications/unknown/acknowledge",
                {"operator": "course-manager", "note": "blocked"},
                "task_ops_unavailable",
            ),
        ):
            status, payload = call(server, "POST", path, body)
            assert status == 503
            assert payload["error"]["code"] == expected
        assert evidence_bytes(runtime.root) == before
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_safety_shield_rejection_prevents_each_assignment_and_keeps_gets_readable(
    tmp_path, launch, monkeypatch
):
    original_access = RangeSimulation.collection_access_allowed

    def blocked(owner, zone_id):
        if zone_id == "NEAR_LEFT":
            return False
        return original_access(owner, zone_id)

    monkeypatch.setattr(RangeSimulation, "collection_access_allowed", blocked)
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "safety-shield", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path / "safety-http")
    try:
        first = post_planning_chain(
            server, revision=1, start_at_utc="2026-09-16T08:10:00Z"
        )["record"]
        runtime.tick()
        first_row = execution_for_confirmation(
            runtime.collection_executions(), first
        )
        assert (first_row["state"], first_row["reason"]) == (
            "REJECTED",
            "SAFETY_REJECTED",
        )
        assert first_row["assignment_id"] is None
        assert first_row["started_sim_t_s"] is None
        assert first_row["raw_quantity"]["balls"] is None
        assert first_row["unload_quantity"]["balls"] is None

        second = post_planning_chain(
            server,
            revision=2,
            start_at_utc="2026-09-16T08:20:00Z",
            inventory_clean_balls=3000,
        )["record"]
        runtime.tick()
        snapshot = runtime.collection_executions()
        second_row = execution_for_confirmation(snapshot, second)
        assert (second_row["state"], second_row["reason"]) == (
            "REJECTED",
            "SAFETY_REJECTED",
        )
        assert second_row["assignment_id"] is None
        assert second_row["started_sim_t_s"] is None
        assert runtime.driver_state == "RUNNING"
        for path in (
            "/api/v1/planning",
            "/api/v0/task-ops",
            "/api/v1/collection-executions",
            f"/api/v1/collection-executions/requests/{first_row['request_id']}",
        ):
            assert call(server, "GET", path)[0] == 200
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def assert_all_continuous_reads(runtime, request_id):
    assert runtime.route_planning("GET", "/api/v1/planning", {})["schema"] == (
        "nxt-planning/v1"
    )
    assert runtime.route_task_operations(
        "GET", "/api/v0/task-ops", {}
    )["schema"] == "nxt-pilot-dispatch/v0"
    assert runtime.collection_executions()["schema"] == (
        "nxt-collection-executions/v1"
    )
    assert runtime.collection_execution_request(request_id)["request_id"] == (
        request_id
    )


@pytest.mark.parametrize(
    ("cause", "reason"),
    [
        ("fault", "ROBOT_FAULT"),
        ("estop", "ESTOP_LATCHED"),
        ("assistance", "HUMAN_ASSISTANCE_REQUIRED"),
    ],
)
def test_protected_runtime_blocks_new_writes_and_keeps_all_gets_readable(
    tmp_path, launch, monkeypatch, cause, reason
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / cause, initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path / f"{cause}-http")
    try:
        first = post_planning_chain(
            server, revision=1, start_at_utc="2026-09-16T08:10:00Z"
        )["record"]
        running = advance_until(
            runtime,
            lambda snapshot: execution_for_confirmation(snapshot, first)["state"]
            == "RUNNING",
            maximum_ticks=3,
        )
        request_id = execution_for_confirmation(running, first)["request_id"]
        original_policy_action = course_session_v3.V3Session._policy_action

        def protected_action(owner):
            if owner.env.sim.now >= 30000:
                robot = owner.env.sim._robots["R1"]
                if cause == "fault":
                    owner.env.sim._fail_robot(robot, "continuous test fault")
                elif cause == "estop":
                    owner.env.sim._latch_estop(robot, "continuous test stop")
                else:
                    index = owner.env.catalog.index_of(
                        "request_human_assistance(R1,other)"
                    )
                    return owner._action(index)
            return original_policy_action(owner)

        monkeypatch.setattr(
            course_session_v3.V3Session, "_policy_action", protected_action
        )
        runtime.tick()
        snapshot = runtime.collection_executions()
        row = execution_for_confirmation(snapshot, first)
        assert row["reason"] == reason
        assert row["state"] != "SUCCEEDED"
        assert row["device_protection"]["protected"] is True
        assert row["device_protection"]["authorization_blocked"] is True
        assert runtime.driver_state == "PROTECTED"

        before = evidence_bytes(runtime.root)
        for path in (
            "/api/v1/planning",
            "/api/v0/task-ops",
            "/api/v1/collection-executions",
            f"/api/v1/collection-executions/requests/{request_id}",
        ):
            assert call(server, "GET", path)[0] == 200
        source = planning_chain_requests(
            2,
            "2026-09-16T08:40:00Z",
            inventory_clean_balls=3000,
        )[0]
        status, rejected = call(
            server, "POST", "/api/v1/planning/inputs", source
        )
        assert status == 503
        assert rejected["error"]["code"] == "planning_unavailable"
        assert evidence_bytes(runtime.root) == before
        assert len(runtime.collection_executions()["executions"]) == 1
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_terminal_conflict_uses_owner_projection_for_live_protected_reads(
    tmp_path
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "terminal-conflict", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    confirmation = post_planning_chain_route(
        runtime, revision=1, start_at_utc="2026-09-16T08:10:00Z"
    )["record"]
    try:
        finished = advance_until(
            runtime,
            lambda snapshot: execution_for_confirmation(snapshot, confirmation)[
                "state"
            ]
            in TERMINAL_EXECUTIONS,
            maximum_ticks=5,
        )
        original = execution_for_confirmation(finished, confirmation)
        assert original["state"] == "SUCCEEDED"
        published_utc = course_session_v3.read_record(
            runtime.session_root / "state.json"
        )["simulation_time_utc"]
        with course_session_v3.execution_admission(
            runtime.session_root
        ) as admission:
            admission.store.record_edge_evidence(
                original["execution_id"],
                terminal_states=["INCONCLUSIVE"],
                event_ids=["conflicting-terminal"],
                now_sim_t_s=admission.now_sim_t_s + 60,
            )
            assert admission.store.recovery_state()["status"] == (
                "EDGE_WITHOUT_COMMIT"
            )

        runtime.tick()
        assert runtime.driver_state == "PROTECTED"
        conflicted = runtime.collection_executions()["executions"][0]
        assert (conflicted["state"], conflicted["reason"]) == (
            "INCONCLUSIVE",
            "TERMINAL_CONFLICT",
        )
        assert conflicted["conflicts"]["terminal_conflict"] is True
        assert conflicted["device_protection"]["authorization_blocked"] is True
        assert runtime.planning_snapshot()["server_time_utc"] == published_utc
        task_runtime = runtime.task_operations_snapshot()["runtime"]
        assert task_runtime["simulation_time_utc"] == published_utc
        assert "activity" not in task_runtime
        assert "payload_balls" not in task_runtime
        assert_all_continuous_reads(runtime, original["request_id"])
        before = evidence_bytes(runtime.root)
        source = planning_chain_requests(
            2,
            "2026-09-16T08:40:00Z",
            inventory_clean_balls=3000,
        )[0]
        with pytest.raises(SiteAgentError) as raised:
            runtime.route_planning("POST", "/api/v1/planning/inputs", source)
        assert raised.value.code == "planning_unavailable"
        assert evidence_bytes(runtime.root) == before
    finally:
        runtime.close()


def test_sealed_replay_mismatch_is_readable_live_and_after_restart(
    tmp_path, launch, monkeypatch
):
    root = tmp_path / "replay-mismatch"
    runtime = ContinuousCollectionExecutionRuntime(
        root, initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    confirmation = post_planning_chain_route(
        runtime, revision=1, start_at_utc="2026-09-16T08:10:00Z"
    )["record"]
    running = advance_until(
        runtime,
        lambda snapshot: execution_for_confirmation(snapshot, confirmation)[
            "state"
        ]
        == "RUNNING",
        maximum_ticks=3,
    )
    request_id = execution_for_confirmation(running, confirmation)["request_id"]
    with course_session_v3.execution_admission(runtime.session_root) as admission:
        expected = admission.store.replay_plan()[0]["prepared"]["decision"][
            "original_action"
        ]
    original_policy_action = course_session_v3.V3Session._policy_action

    def mismatching_policy(owner):
        if owner.env.sim.now == 29400:
            candidates = [
                owner.env.catalog.index_of("wait"),
                owner.env.catalog.index_of("pause_robot(R1)"),
            ]
            different = next(
                index for index in candidates if index != expected["index"]
            )
            return owner._action(different)
        return original_policy_action(owner)

    monkeypatch.setattr(
        course_session_v3.V3Session, "_policy_action", mismatching_policy
    )
    with pytest.raises(course_session_v3.ReplayMismatch):
        course_session_v3.run(runtime.session_root)
    assert course_session_v3.structural_recovery_status(runtime.session_root) == (
        "REPLAY_MISMATCH"
    )
    try:
        runtime.tick()
        assert runtime.driver_state == "PROTECTED"
        row = runtime.collection_executions()["executions"][0]
        assert (row["state"], row["reason"]) == (
            "INCONCLUSIVE",
            "REPLAY_MISMATCH",
        )
        assert row["conflicts"]["replay_mismatch"] is True
        assert row["device_protection"]["authorization_blocked"] is True
        assert_all_continuous_reads(runtime, request_id)
    finally:
        runtime.close()

    reopened = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )

    def forbidden_sealed_start(*_args, **_kwargs):
        pytest.fail("sealed restart attempted an active runtime operation")

    for name in (
        "_advance_v3_unlocked",
        "_start_device_and_reconcile",
        "_start_gateway_and_publisher",
        "_publish_status",
        "_resume_bound_admissions_unlocked",
        "_materialize_new_tasks_unlocked",
    ):
        monkeypatch.setattr(reopened, name, forbidden_sealed_start)
    monkeypatch.setattr(
        v4_service.PlanningOperations, "recover", forbidden_sealed_start
    )
    monkeypatch.setattr(ScheduleService, "tick", forbidden_sealed_start)
    reopened.start()
    service, server = serve(reopened, launch, tmp_path / "sealed-http")
    try:
        assert reopened.driver_state == "PROTECTED"
        assert reopened.device is None
        assert reopened.gateway is None
        assert reopened.publisher is None
        sealed_runtime = reopened.runtime_status()
        assert sealed_runtime["recovery_status"] == "REPLAY_MISMATCH"
        assert "activity" not in sealed_runtime
        assert "payload_balls" not in sealed_runtime
        assert_all_continuous_reads(reopened, request_id)
        before_gets = evidence_bytes(root)
        for _ in range(2):
            for path in (
                "/api/v1/planning",
                "/api/v1/planning/requests/continuous-input-001",
                "/api/v0/task-ops",
                "/api/v1/collection-executions",
                f"/api/v1/collection-executions/requests/{request_id}",
            ):
                status, payload = call(server, "GET", path)
                assert status == 200, (path, payload)
        for path in (
            "/api/v1/planning/inputs",
            "/api/v1/planning/plans",
            "/api/v1/planning/confirmations",
            "/api/v1/planning/outcomes",
        ):
            status, payload = call(server, "POST", path, {})
            assert status == 503
            assert payload["error"]["code"] == "planning_unavailable"
        for path in (
            "/api/v0/task-ops/schedules",
            "/api/v0/task-ops/schedules/unknown/cancel",
            "/api/v0/task-ops/notifications/unknown/acknowledge",
            "/api/v0/task-ops/notifications/unknown/resolve",
        ):
            status, payload = call(server, "POST", path, {})
            assert status == 503
            assert payload["error"]["code"] == "task_ops_unavailable"
        status, payload = call(
            server, "POST", "/api/v1/collection-executions", {}
        )
        assert status == 405
        assert payload["error"]["code"] == "method_not_allowed"
        assert evidence_bytes(root) == before_gets
    finally:
        server.shutdown()
        service.stop()
        reopened.close()


@pytest.mark.parametrize("terminal", [True, False])
def test_session_end_classification_rejects_writes_and_keeps_gets_readable(
    tmp_path, launch, monkeypatch, terminal
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / ("ended-clean" if terminal else "ended-nonterminal"),
        initialize=True,
        wall_clock=WallClock(),
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path / f"ended-http-{terminal}")
    confirmation = post_planning_chain(
        server, revision=1, start_at_utc="2026-09-16T08:10:00Z"
    )["record"]
    try:
        if terminal:
            snapshot = advance_until(
                runtime,
                lambda value: execution_for_confirmation(value, confirmation)[
                    "state"
                ]
                in TERMINAL_EXECUTIONS,
                maximum_ticks=5,
            )
        else:
            snapshot = advance_until(
                runtime,
                lambda value: execution_for_confirmation(value, confirmation)[
                    "state"
                ]
                == "RUNNING",
                maximum_ticks=3,
            )
        request_id = execution_for_confirmation(snapshot, confirmation)["request_id"]
        ended = {**runtime.runtime_status(), "session_state": "ENDED"}
        monkeypatch.setattr(runtime, "_runtime_status_unlocked", lambda: ended)
        with runtime._lock:
            runtime._classify_unlocked()
        assert runtime.driver_state == ("ENDED" if terminal else "FAILED")
        if not terminal:
            assert runtime.failure == (
                "RuntimeError: V3 session ended with nonterminal execution"
            )
        before = evidence_bytes(runtime.root)
        for path in (
            "/api/v1/planning",
            "/api/v0/task-ops",
            "/api/v1/collection-executions",
            f"/api/v1/collection-executions/requests/{request_id}",
        ):
            assert call(server, "GET", path)[0] == 200
        source = planning_chain_requests(
            2,
            "2026-09-16T08:40:00Z",
            inventory_clean_balls=3000,
        )[0]
        status, rejected = call(
            server, "POST", "/api/v1/planning/inputs", source
        )
        assert status == (409 if terminal else 503)
        assert rejected["error"]["code"] == (
            "planning_conflict" if terminal else "planning_unavailable"
        )
        assert evidence_bytes(runtime.root) == before
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


@pytest.mark.parametrize("iteration", range(3))
def test_confirmation_and_tick_are_linearizable_through_public_routes(
    tmp_path, iteration
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / f"confirmation-{iteration}",
        initialize=True,
        wall_clock=WallClock(),
    )
    runtime.start()
    source, plan_request, confirmation = planning_chain_requests(
        1,
        "2026-09-16T08:40:00Z",
        inventory_clean_balls=3000,
    )
    runtime.route_planning("POST", "/api/v1/planning/inputs", source)
    plan = runtime.route_planning(
        "POST", "/api/v1/planning/plans", plan_request
    )["record"]
    confirmation["plan_id"] = plan["plan_id"]
    barrier = threading.Barrier(2)
    results = []

    def confirm():
        barrier.wait()
        results.append(
            runtime.route_planning(
                "POST", "/api/v1/planning/confirmations", confirmation
            )
        )

    def tick():
        barrier.wait()
        runtime.tick()

    threads = [threading.Thread(target=confirm), threading.Thread(target=tick)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive()
        assert len(results) == 1
        runtime.tick()
        planning = runtime.planning_snapshot()
        task_ops = runtime.task_operations_snapshot()
        assert len(planning["confirmations"]) == 1
        assert len(task_ops["schedules"]) == 1
        assert task_ops["schedules"][0]["status"] == "SCHEDULED"
        assert runtime.failure is None
    finally:
        runtime.close()


@pytest.mark.parametrize("iteration", range(3))
def test_pending_cancel_and_dispatch_are_linearizable_through_public_routes(
    tmp_path, iteration
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / f"cancel-{iteration}",
        initialize=True,
        wall_clock=WallClock(),
    )
    runtime.start()
    post_planning_chain_route(
        runtime,
        revision=1,
        start_at_utc="2026-09-16T08:20:00Z",
        inventory_clean_balls=3000,
    )
    runtime.tick()
    schedule = runtime.task_operations_snapshot()["schedules"][0]
    assert schedule["status"] == "SCHEDULED"
    assert runtime.runtime_status()["simulation_time_utc"] == (
        "2026-09-16T08:20:00Z"
    )
    barrier = threading.Barrier(2)
    cancel_results = []

    def cancel():
        barrier.wait()
        try:
            cancel_results.append(
                runtime.route_task_operations(
                    "POST",
                    f"/api/v0/task-ops/schedules/{schedule['schedule_id']}/cancel",
                    {"operator": "course-manager"},
                )
            )
        except SiteAgentError as exc:
            cancel_results.append(exc)

    def tick():
        barrier.wait()
        runtime.tick()

    threads = [threading.Thread(target=cancel), threading.Thread(target=tick)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive()
        assert len(cancel_results) == 1
        final = runtime.task_operations_snapshot()["schedules"][0]
        matching_tasks = [
            record
            for record in runtime.edge_journal.read()
            if record.record_kind == "task_created"
            and record.payload.get("schedule_id") == schedule["schedule_id"]
        ]
        if final["status"] == "CANCELLED":
            assert final["task_id"] is None
            assert matching_tasks == []
            assert not isinstance(cancel_results[0], SiteAgentError)
        else:
            assert final["status"] == "DISPATCHED"
            assert len(matching_tasks) == 1
            assert isinstance(cancel_results[0], SiteAgentError)
            assert cancel_results[0].code == "task_ops_conflict"
        assert not (final["status"] == "CANCELLED" and matching_tasks)
        assert runtime.failure is None
    finally:
        runtime.close()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_collection_execution_mutations_are_405_and_byte_pure(
    tmp_path, launch, method
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / method.lower(), initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path / f"http-{method.lower()}")
    try:
        before = evidence_bytes(runtime.root)
        status, payload = call(
            server, method, "/api/v1/collection-executions", {}
        )
        assert status == 405
        assert payload["error"] == {
            "code": "method_not_allowed",
            "detail": "collection execution evidence is read-only",
        }
        assert evidence_bytes(runtime.root) == before
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_after_device_acceptance_crash_fires_once_and_restart_protects(
    tmp_path
):
    root = tmp_path / "after-device-acceptance"
    fired = []
    armed = False

    class InjectedCrash(RuntimeError):
        pass

    def crash_hook(boundary, payload):
        if armed and boundary == "after_device_acceptance":
            fired.append((boundary, deepcopy(payload)))
            raise InjectedCrash(boundary)

    runtime = ContinuousCollectionExecutionRuntime(
        root,
        initialize=True,
        wall_clock=WallClock(),
        crash_hook=crash_hook,
    )
    runtime.start()
    post_planning_chain_route(
        runtime, revision=1, start_at_utc="2026-09-16T08:10:00Z"
    )
    armed = True
    runtime.tick()
    assert [boundary for boundary, _payload in fired] == [
        "after_device_acceptance"
    ]
    assert runtime.driver_state == "FAILED"
    assert runtime.failure == "InjectedCrash: after_device_acceptance"
    before_retry = evidence_bytes(root)
    runtime.tick()
    assert len(fired) == 1
    assert evidence_bytes(root) == before_retry
    accepted = runtime.collection_executions()["executions"][0]
    assert accepted["edge_evidence"]["accepted"] is True
    request_id = accepted["request_id"]
    attempt_id = accepted["attempt_id"]
    runtime.close()

    recovered = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )
    recovered.start()
    try:
        snapshot = recovered.collection_executions()
        assert len(snapshot["requests"]) == len(snapshot["receipts"]) == 1
        row = snapshot["executions"][0]
        assert row["request_id"] == request_id
        assert row["attempt_id"] == attempt_id
        assert row["state"] in {"FAILED", "INCONCLUSIVE"}
        assert row["device_protection"]["protected"] is True
        assert row["device_protection"]["authorization_blocked"] is True
        assert recovered.driver_state == "PROTECTED"
        move_ids = (
            row["raw_quantity"]["source_event_ids"]
            + row["unload_quantity"]["source_event_ids"]
        )
        assert len(move_ids) == len(set(move_ids))
    finally:
        recovered.close()


def assert_paused_protected_runtime_rejects_writes_without_mutation(runtime):
    assert runtime.driver_state == "PROTECTED"
    task_ops = runtime.task_operations_snapshot()
    assert task_ops["scheduler"]["state"] == "FAILED"
    assert task_ops["runtime"]["accepts_new_confirmations"] is False
    assert runtime.start_driver() is False

    before = evidence_bytes(runtime.root)
    source = planning_chain_requests(
        2,
        "2026-09-16T08:40:00Z",
        inventory_clean_balls=3000,
    )[0]
    with pytest.raises(SiteAgentError) as planning_rejected:
        runtime.route_planning(
            "POST", "/api/v1/planning/inputs", source
        )
    assert planning_rejected.value.code == "planning_unavailable"
    with pytest.raises(SiteAgentError) as task_ops_rejected:
        runtime.route_task_operations(
            "POST", "/api/v0/task-ops/schedules", {}
        )
    assert task_ops_rejected.value.code == "task_ops_unavailable"
    assert evidence_bytes(runtime.root) == before
    assert len(runtime.planning_snapshot()["confirmations"]) == 1
    assert len(runtime.collection_executions()["executions"]) == 1


def test_paused_reopen_keeps_durable_execution_protection_above_pause(tmp_path):
    root = tmp_path / "paused-protected"
    fired = []
    armed = False

    class InjectedCrash(RuntimeError):
        pass

    def crash_hook(boundary, payload):
        if armed and boundary == "after_device_acceptance":
            fired.append((boundary, deepcopy(payload)))
            raise InjectedCrash(boundary)

    runtime = ContinuousCollectionExecutionRuntime(
        root,
        initialize=True,
        wall_clock=WallClock(),
        crash_hook=crash_hook,
    )
    runtime.start()
    post_planning_chain_route(
        runtime, revision=1, start_at_utc="2026-09-16T08:10:00Z"
    )
    armed = True
    runtime.tick()
    assert [boundary for boundary, _payload in fired] == [
        "after_device_acceptance"
    ]
    runtime.close()
    course_session_v3.set_paused(root / "session-v3", True)

    reopened = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )
    reopened.start()
    try:
        row = reopened.collection_executions()["executions"][0]
        assert row["device_protection"]["protected"] is True
        assert row["device_protection"]["authorization_blocked"] is True
        assert reopened.runtime_status()["session_state"] == "PAUSED"
        assert_paused_protected_runtime_rejects_writes_without_mutation(
            reopened
        )
    finally:
        reopened.close()


def test_paused_after_cursor_reopen_repairs_prefix_before_device_and_stays_protected(
    tmp_path, monkeypatch
):
    root = tmp_path / "paused-after-cursor"
    fired = []
    armed = False

    class InjectedCrash(RuntimeError):
        pass

    def crash_hook(boundary, payload):
        if armed and boundary == "after_cursor":
            fired.append((boundary, deepcopy(payload)))
            raise InjectedCrash(boundary)

    runtime = ContinuousCollectionExecutionRuntime(
        root,
        initialize=True,
        wall_clock=WallClock(),
        crash_hook=crash_hook,
    )
    runtime.start()
    post_planning_chain_route(
        runtime, revision=1, start_at_utc="2026-09-16T08:10:00Z"
    )
    armed = True
    runtime.tick()
    assert [boundary for boundary, _payload in fired] == ["after_cursor"]
    assert course_session_v3.structural_recovery_status(
        runtime.session_root
    ) == "COMMITTED_OUTBOX_UNCONFIRMED"
    prefix_before_reopen = reconstructed_v3_prefix(runtime.session_root)
    runtime.close()
    course_session_v3.set_paused(root / "session-v3", True)

    reopened = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )
    original_device_start = reopened._start_device_and_reconcile
    prefix_at_device_start = []

    def require_repaired_prefix_before_device():
        observed = reconstructed_v3_prefix(reopened.session_root)
        prefix_at_device_start.append(observed)
        assert observed == prefix_before_reopen
        with course_session_v3._lock(
            reopened.session_root / ".session.lock"
        ):
            session, saved, control = (
                course_session_v3._validated_restart_runtime(
                    reopened.session_root
                )
            )
            durable = session.store.recovery_state()
            persisted = session.store.replay()
            assert control == {"paused": True}
            assert durable["status"] == "COMMITTED_OUTBOX_UNCONFIRMED"
            assert saved["status"] == "OUTBOX_PENDING"
            assert saved["step"] == durable["tick_sequence"]
            assert saved["replay_digest"] == durable["replay_digest"]
            assert saved["now_sim_t_s"] == persisted["now_sim_t_s"]
        return original_device_start()

    monkeypatch.setattr(
        reopened,
        "_start_device_and_reconcile",
        require_repaired_prefix_before_device,
    )
    reopened.start()
    try:
        assert prefix_at_device_start == [prefix_before_reopen]
        assert reopened.runtime_status()["session_state"] == "PAUSED"
        row = reopened.collection_executions()["executions"][0]
        assert row["device_protection"]["protected"] is True
        assert row["device_protection"]["authorization_blocked"] is True
        assert_paused_protected_runtime_rejects_writes_without_mutation(
            reopened
        )
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "boundary",
    [
        "after_prepare",
        "after_commit",
        "after_device_append",
        "after_v3_evidence",
        "after_v3_confirm",
        "after_cursor",
    ],
)
def test_accepted_or_running_crash_boundaries_fail_stop_without_duplicate_move(
    tmp_path, monkeypatch, boundary
):
    root = tmp_path / boundary
    fired = []
    armed = False

    class InjectedCrash(RuntimeError):
        pass

    def crash_hook(observed, payload):
        if armed and observed == boundary:
            fired.append((observed, deepcopy(payload)))
            raise InjectedCrash(observed)

    runtime = ContinuousCollectionExecutionRuntime(
        root,
        initialize=True,
        wall_clock=WallClock(),
        crash_hook=crash_hook,
    )
    runtime.start()
    post_planning_chain_route(
        runtime, revision=1, start_at_utc="2026-09-16T08:10:00Z"
    )
    armed = True
    runtime.tick()
    assert [observed for observed, _payload in fired] == [boundary]
    assert runtime.driver_state == "FAILED"
    assert runtime.failure == f"InjectedCrash: {boundary}"
    durable_after_crash = evidence_bytes(root)
    runtime.tick()
    assert len(fired) == 1
    assert evidence_bytes(root) == durable_after_crash
    runtime.close()

    prefix_before_startup = None
    if boundary == "after_cursor":
        assert course_session_v3.structural_recovery_status(
            root / "session-v3"
        ) == "COMMITTED_OUTBOX_UNCONFIRMED"
        prefix_before_startup = reconstructed_v3_prefix(root / "session-v3")

    recovered = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )
    observed_before_device = []
    if boundary == "after_cursor":
        original_device_start = recovered._start_device_and_reconcile

        def verify_recovery_only_before_device():
            observed = reconstructed_v3_prefix(recovered.session_root)
            observed_before_device.append(observed)
            assert observed == prefix_before_startup
            with course_session_v3._lock(
                recovered.session_root / ".session.lock"
            ):
                session, saved, _control = (
                    course_session_v3._validated_restart_runtime(
                        recovered.session_root
                    )
                )
                durable = session.store.recovery_state()
                persisted = session.store.replay()
                assert saved["step"] == durable["tick_sequence"]
                assert saved["replay_digest"] == durable["replay_digest"]
                assert saved["now_sim_t_s"] == persisted["now_sim_t_s"]
            return original_device_start()

        monkeypatch.setattr(
            recovered,
            "_start_device_and_reconcile",
            verify_recovery_only_before_device,
        )
    recovered.start()
    try:
        if boundary == "after_cursor":
            assert observed_before_device == [prefix_before_startup]
        snapshot = recovered.collection_executions()
        assert len(snapshot["bindings"]) == 1
        assert len(snapshot["requests"]) == len(snapshot["receipts"]) == 1
        assert len(snapshot["executions"]) == 1
        row = snapshot["executions"][0]
        assert row["attempt_id"] == "attempt-" + row["execution_id"]
        assert row["state"] in {"FAILED", "INCONCLUSIVE"}
        assert row["device_protection"]["protected"] is True
        assert row["device_protection"]["authorization_blocked"] is True
        assert recovered.driver_state == "PROTECTED"
        move_ids = (
            row["raw_quantity"]["source_event_ids"]
            + row["unload_quantity"]["source_event_ids"]
        )
        assert len(move_ids) == len(set(move_ids))
        recovered.tick()
        assert recovered.collection_executions()["executions"] == [row]
    finally:
        recovered.close()


@pytest.mark.parametrize(
    ("boundary", "record_kind", "journal_name"),
    [
        ("after_confirmation_append", "planning_confirmation_recorded", "edge"),
        ("after_schedule_append", "schedule_created", "edge"),
        ("after_task_created_append", "task_created", "edge"),
        ("after_binding_append", "binding", "execution"),
        ("after_request_append", "request", "execution"),
    ],
)
def test_same_incarnation_recovers_each_durable_append_once(
    tmp_path, monkeypatch, boundary, record_kind, journal_name
):
    root = tmp_path / boundary
    fired = []
    armed = False

    class InjectedCrash(RuntimeError):
        pass

    original_append_via = JsonlJournal.append_via
    target = (
        root / "edge" / "edge_task_journal.jsonl"
        if journal_name == "edge"
        else root / "session-v3" / "collection-execution.jsonl"
    )

    def append_then_crash(journal, builder):
        appended = original_append_via(journal, builder)
        if (
            armed
            and not fired
            and journal.path == target
            and any(row.record_kind == record_kind for row in appended)
        ):
            fired.append(boundary)
            raise InjectedCrash(boundary)
        return appended

    monkeypatch.setattr(JsonlJournal, "append_via", append_then_crash)
    runtime = ContinuousCollectionExecutionRuntime(
        root, initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    source, plan_request, confirmation = planning_chain_requests(
        1, "2026-09-16T08:10:00Z"
    )
    runtime.route_planning("POST", "/api/v1/planning/inputs", source)
    plan = runtime.route_planning(
        "POST", "/api/v1/planning/plans", plan_request
    )["record"]
    confirmation["plan_id"] = plan["plan_id"]
    armed = True
    if boundary == "after_confirmation_append":
        with pytest.raises(SiteAgentError) as raised:
            runtime.route_planning(
                "POST", "/api/v1/planning/confirmations", confirmation
            )
        assert raised.value.code == "planning_result_unknown"
    else:
        runtime.route_planning(
            "POST", "/api/v1/planning/confirmations", confirmation
        )
        runtime.tick()
    assert fired == [boundary]
    assert runtime.driver_state == "FAILED"
    durable = evidence_bytes(root)
    runtime.tick()
    assert fired == [boundary]
    assert evidence_bytes(root) == durable
    runtime.close()

    identity = course_session_v3.read_record(
        root / "session-v3" / "identity.json"
    )
    store_before = execution_api.CollectionExecutionStore(
        root / "session-v3" / "collection-execution.jsonl",
        identity,
        policy_id=course_session_v3.POLICY_ID,
    ).replay()
    prior_bindings = deepcopy(store_before["bindings"])
    prior_requests = deepcopy(store_before["requests"])
    prior_receipts = deepcopy(store_before["receipts"])

    recovered = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )
    recovered.start()
    try:
        snapshot = advance_until(
            recovered,
            lambda value: len(value["executions"]) == 1
            and value["executions"][0]["state"] in TERMINAL_EXECUTIONS,
            maximum_ticks=20,
        )
        assert len(recovered.planning_snapshot()["confirmations"]) == 1
        assert len(recovered.task_operations_snapshot()["schedules"]) == 1
        assert sum(
            row.record_kind == "task_created"
            for row in recovered.edge_journal.read()
        ) == 1
        assert len(snapshot["bindings"]) == 1
        assert len(snapshot["requests"]) == len(snapshot["receipts"]) == 1
        assert len(snapshot["executions"]) == 1
        row = snapshot["executions"][0]
        assert row["request_id"] == execution_api.request_id_for_binding(
            row["binding_id"]
        )
        assert row["attempt_id"] == "attempt-" + row["execution_id"]
        replayed = execution_api.CollectionExecutionStore(
            root / "session-v3" / "collection-execution.jsonl",
            identity,
            policy_id=course_session_v3.POLICY_ID,
        ).replay()
        for key, value in prior_bindings.items():
            assert replayed["bindings"][key] == value
        for key, value in prior_requests.items():
            assert replayed["requests"][key] == value
        for key, value in prior_receipts.items():
            assert replayed["receipts"][key] == value
        terminals = [
            record
            for record in recovered.device.journal.read()
            if record.record_kind == "task_event_persisted"
            and record.payload["event"]["kind"]
            in {"SUCCEEDED", "FAILED", "INCONCLUSIVE", "REJECTED"}
        ]
        assert len(terminals) == 1
        move_ids = (
            row["raw_quantity"]["source_event_ids"]
            + row["unload_quantity"]["source_event_ids"]
        )
        assert len(move_ids) == len(set(move_ids))
    finally:
        recovered.close()


@pytest.mark.parametrize(
    "boundary",
    ["after_preacceptance_device_rejection", "after_preacceptance_v3_rejection"],
)
def test_explicit_reprovision_closes_preacceptance_request_once(
    tmp_path, monkeypatch, boundary
):
    root = tmp_path / boundary

    class InjectedCrash(RuntimeError):
        pass

    request_crashed = []
    armed_request = False
    original_append_via = JsonlJournal.append_via
    execution_journal = root / "session-v3" / "collection-execution.jsonl"

    def append_request_then_crash(journal, builder):
        appended = original_append_via(journal, builder)
        if (
            armed_request
            and not request_crashed
            and journal.path == execution_journal
            and any(row.record_kind == "request" for row in appended)
        ):
            request_crashed.append("after_request_append")
            raise InjectedCrash("after_request_append")
        return appended

    monkeypatch.setattr(JsonlJournal, "append_via", append_request_then_crash)
    initial = ContinuousCollectionExecutionRuntime(
        root, initialize=True, wall_clock=WallClock()
    )
    initial.start()
    post_planning_chain_route(
        initial, revision=1, start_at_utc="2026-09-16T08:10:00Z"
    )
    armed_request = True
    initial.tick()
    assert request_crashed == ["after_request_append"]
    assert initial.driver_state == "FAILED"
    config = initial.config
    initial.close()

    identity = course_session_v3.read_record(
        root / "session-v3" / "identity.json"
    )
    before_store = execution_api.CollectionExecutionStore(
        execution_journal,
        identity,
        policy_id=course_session_v3.POLICY_ID,
    ).replay()
    assert len(before_store["requests"]) == len(before_store["receipts"]) == 1
    before_execution = next(iter(before_store["executions"].values()))
    assert before_execution["edge_evidence"]["accepted"] is False
    original_incarnation = before_execution["incarnation"]

    canonical_device = (
        root / "device" / "picker-01" / "robot_task_journal.jsonl"
    )
    canonical_anchor = canonical_device.with_name(canonical_device.name + ".hwm")
    archive = canonical_device.parent / "replaced-device-evidence"
    archive.mkdir()
    canonical_device.rename(archive / canonical_device.name)
    canonical_anchor.rename(archive / canonical_anchor.name)
    archived_bytes = {
        path.name: path.read_bytes() for path in sorted(archive.iterdir())
    }

    replacement = SimulatorBackedTaskDevice(
        root / "session-v3",
        config,
        "picker-01",
        journal_path=canonical_device,
        initialize=True,
        provisioning_nonce=lambda: f"explicit-replacement-{boundary}",
    )
    replacement.start()
    assert replacement.view.incarnation != original_incarnation

    fired = []

    def crash_hook(observed, payload):
        if observed == boundary and not fired:
            fired.append((observed, deepcopy(payload)))
            raise InjectedCrash(observed)

    interrupted = ContinuousCollectionExecutionRuntime(
        root,
        initialize=False,
        wall_clock=WallClock(),
        crash_hook=crash_hook,
    )
    with pytest.raises(InjectedCrash, match=boundary):
        interrupted.start()
    assert [observed for observed, _payload in fired] == [boundary]
    assert interrupted.started is False
    assert interrupted._process_lock is None

    recovered = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )
    recovered.start()
    try:
        snapshot = recovered.collection_executions()
        assert len(snapshot["requests"]) == len(snapshot["receipts"]) == 1
        assert len(snapshot["executions"]) == 1
        row = snapshot["executions"][0]
        assert row["request_id"] == before_execution["request_id"]
        assert row["attempt_id"] == before_execution["attempt_id"]
        assert (row["state"], row["reason"], row["stage"]) == (
            "REJECTED",
            "IDENTITY_CONFLICT",
            "TERMINAL",
        )
        assert row["started_sim_t_s"] is None
        assert row["assignment_id"] is None
        assert row["raw_quantity"]["status"] == "NOT_REACHED"
        assert row["unload_quantity"]["status"] == "NOT_REACHED"
        assert row["raw_quantity"]["source_event_ids"] == []
        assert row["unload_quantity"]["source_event_ids"] == []
        assert row["edge_evidence"]["accepted"] is False
        assert row["edge_evidence"]["effective_state"] == "REJECTED"
        assert row["edge_evidence"]["reason"] == "incarnation_mismatch"
        assert row["edge_evidence"]["result_verification"] == "VERIFIED"
        assert row["conflicts"]["incarnation_mismatch"] is True
        assert row["device_protection"]["authorization_blocked"] is True
        assert recovered.driver_state == "PROTECTED"
        rejections = [
            record
            for record in recovered.device.journal.read()
            if record.record_kind == "task_event_persisted"
            and record.payload["event"]["event_sequence"] == 0
            and record.payload["event"]["kind"] == "REJECTED"
            and record.payload["event"]["reason_code"]
            == "incarnation_mismatch"
        ]
        assert len(rejections) == 1
        final_store = execution_api.CollectionExecutionStore(
            execution_journal,
            identity,
            policy_id=course_session_v3.POLICY_ID,
        ).replay()
        assert len(final_store["preacceptance_rejections"]) == 1
        assert {
            path.name: path.read_bytes() for path in sorted(archive.iterdir())
        } == archived_bytes
    finally:
        recovered.close()


@pytest.mark.parametrize(
    ("path", "record_kind"),
    [
        ("/api/v1/planning/inputs", "planning_input_recorded"),
        ("/api/v1/planning/plans", "planning_plan_recorded"),
        (
            "/api/v1/planning/confirmations",
            "planning_confirmation_recorded",
        ),
    ],
)
def test_response_lost_after_fsync_recovers_by_request_get_then_exact_retry(
    tmp_path, monkeypatch, path, record_kind
):
    root = tmp_path / record_kind
    runtime = ContinuousCollectionExecutionRuntime(
        root, initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    source, plan_request, confirmation = planning_chain_requests(
        1, "2026-09-16T08:10:00Z"
    )
    if path != "/api/v1/planning/inputs":
        runtime.route_planning("POST", "/api/v1/planning/inputs", source)
    if path == "/api/v1/planning/plans":
        body = plan_request
    elif path == "/api/v1/planning/confirmations":
        plan = runtime.route_planning(
            "POST", "/api/v1/planning/plans", plan_request
        )["record"]
        confirmation["plan_id"] = plan["plan_id"]
        body = confirmation
    else:
        body = source

    fired = []
    original_append_via = JsonlJournal.append_via
    target = root / "edge" / "edge_task_journal.jsonl"

    def append_then_lose_response(journal, builder):
        appended = original_append_via(journal, builder)
        if (
            not fired
            and journal.path == target
            and any(row.record_kind == record_kind for row in appended)
        ):
            fired.append(record_kind)
            raise OSError("injected response loss after fsync")
        return appended

    monkeypatch.setattr(JsonlJournal, "append_via", append_then_lose_response)
    try:
        with pytest.raises(SiteAgentError) as raised:
            runtime.route_planning("POST", path, body)
        assert raised.value.code == "planning_result_unknown"
        assert fired == [record_kind]
        assert runtime.driver_state == "FAILED"
    finally:
        runtime.close()

    recovered = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )
    recovered.start()
    try:
        receipt = recovered.route_planning(
            "GET",
            f"/api/v1/planning/requests/{body['request_id']}",
            {},
        )
        assert receipt["request_id"] == body["request_id"]
        assert receipt["disposition"] == "duplicate"
        assert recovered.route_planning("POST", path, body) == receipt
        changed = deepcopy(body)
        changed["reason"] = "conflicting retry content"
        before = evidence_bytes(root)
        with pytest.raises(SiteAgentError) as conflict:
            recovered.route_planning("POST", path, changed)
        assert conflict.value.code == "planning_conflict"
        assert evidence_bytes(root) == before
        records = recovered.edge_journal.read()
        assert sum(row.record_kind == record_kind for row in records) == 1
    finally:
        recovered.close()


def test_restart_between_first_terminal_and_second_due_keeps_all_ids_and_attempts(
    tmp_path
):
    root = tmp_path / "restart-between"
    first_runtime = ContinuousCollectionExecutionRuntime(
        root, initialize=True, wall_clock=WallClock()
    )
    first_runtime.start()
    first = post_planning_chain_route(
        first_runtime, revision=1, start_at_utc="2026-09-16T08:10:00Z"
    )["record"]
    running = advance_until(
        first_runtime,
        lambda snapshot: execution_for_confirmation(snapshot, first)["state"]
        == "RUNNING",
        maximum_ticks=4,
    )
    post_planning_chain_route(
        first_runtime,
        revision=2,
        start_at_utc="2026-09-16T08:40:00Z",
        inventory_clean_balls=3000,
    )
    before_restart = advance_until(
        first_runtime,
        lambda snapshot: execution_for_confirmation(snapshot, first)["state"]
        in TERMINAL_EXECUTIONS,
        maximum_ticks=4,
    )
    assert before_restart["simulation_time_utc"] == "2026-09-16T08:30:00Z"
    first_before = execution_for_confirmation(before_restart, first)
    stable_first = {
        key: first_before[key]
        for key in (
            "task_id",
            "binding_id",
            "request_id",
            "execution_id",
            "attempt_id",
        )
    }
    assert running["executions"][0]["attempt_id"] == stable_first["attempt_id"]
    first_runtime.close()

    recovered = ContinuousCollectionExecutionRuntime(
        root, initialize=False, wall_clock=WallClock()
    )
    recovered.start()
    try:
        immediate = recovered.collection_executions()
        assert {
            key: execution_for_confirmation(immediate, first)[key]
            for key in stable_first
        } == stable_first
        final = advance_until(
            recovered,
            lambda snapshot: len(snapshot["executions"]) == 2
            and all(
                row["state"] in TERMINAL_EXECUTIONS
                for row in snapshot["executions"]
            ),
            maximum_ticks=12,
        )
        assert len(final["bindings"]) == 2
        assert len(final["requests"]) == len(final["receipts"]) == 2
        assert len({row["execution_id"] for row in final["executions"]}) == 2
        assert len({row["attempt_id"] for row in final["executions"]}) == 2
        assert {
            key: execution_for_confirmation(final, first)[key]
            for key in stable_first
        } == stable_first
    finally:
        recovered.close()


def test_cli_exposes_continuous_flags_and_uses_required_lifecycle_order(
    tmp_path, monkeypatch, capsys
):
    with pytest.raises(SystemExit) as help_exit:
        v4_service.main(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    for flag in (
        "--out",
        "--initialize",
        "--port",
        "--driver-interval",
        "--console",
        "--api-only",
    ):
        assert flag in help_text

    events = []

    class FakeRuntime:
        def __init__(self, root, *, initialize, step_interval_s):
            events.append(("runtime.init", Path(root), initialize, step_interval_s))

        def start(self):
            events.append("runtime.start")

        def api_callbacks(self):
            events.append("runtime.callbacks")
            return {"collection_executions": lambda: {}}

        def start_driver(self):
            events.append("runtime.start_driver")
            return True

        def close(self):
            events.append("runtime.close")

    class FakeService:
        @classmethod
        def launch(cls, **kwargs):
            events.append(("service.launch", kwargs["runs_root"]))
            return cls()

        def stop(self):
            events.append("service.stop")

    class FakeServer:
        url = "http://127.0.0.1:0"

        def __init__(self, service, **kwargs):
            events.append(("server.init", kwargs["port"], kwargs["console_dir"]))

        def start_background(self):
            events.append("server.start")

        def shutdown(self):
            events.append("server.shutdown")

    class FakeStop:
        def wait(self, _seconds):
            events.append("wait")
            return True

        def set(self):
            events.append("signal.stop")

    monkeypatch.setattr(v4_service, "ContinuousCollectionExecutionRuntime", FakeRuntime)
    monkeypatch.setattr(v4_service, "SiteAgentService", FakeService)
    monkeypatch.setattr(v4_service, "SiteAgentApiServer", FakeServer)
    monkeypatch.setattr(v4_service.threading, "Event", FakeStop)
    monkeypatch.setattr(v4_service.signal, "signal", lambda *_args: None)

    assert v4_service.main(
        [
            "--out",
            str(tmp_path / "cli"),
            "--initialize",
            "--port",
            "0",
            "--driver-interval",
            "0.25",
            "--api-only",
        ]
    ) == 0
    assert events == [
        ("runtime.init", tmp_path / "cli", True, 0.25),
        "runtime.start",
        ("service.launch", tmp_path / "cli" / "site-agent"),
        "runtime.callbacks",
        ("server.init", 0, None),
        "server.start",
        "runtime.start_driver",
        "wait",
        "server.shutdown",
        "runtime.close",
        "service.stop",
    ]
