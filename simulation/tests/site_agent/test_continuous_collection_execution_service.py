"""Continuous V4 composition, recovery, and single-driver contracts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from nxt_edge_task.schedules import ScheduleService
from nxt_site_agent import SiteAgentError
from scripts import course_collection_execution as execution_api
from scripts import course_session_v3
from scripts.course_collection_execution_demo import planning_requests
from scripts.course_collection_execution_service import (
    CollectionExecutionServiceRuntime,
)
from scripts.course_collection_execution_v4_service import (
    ContinuousCollectionExecutionRuntime,
    classify_continuous_runtime,
)


class WallClock:
    def __init__(self) -> None:
        self.value = datetime(2035, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


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
