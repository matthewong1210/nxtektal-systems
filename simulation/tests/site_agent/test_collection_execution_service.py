"""Isolated 3C service composition over one durable V3 execution runtime."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import threading
import time

import pytest

from nxt_range_ops.core.sim import RangeSimulation
from nxt_edge_task.contracts import parse_utc
from nxt_site_agent import SiteAgentApiServer, SiteAgentError
from scripts import course_session_v3
from scripts.course_collection_execution_service import (
    CollectionExecutionServiceRuntime,
)


class WallClock:
    def __init__(self) -> None:
        self.value = datetime(2035, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


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


def evidence_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {".collection-execution.lock", ".session.lock"}
        and "site-agent" not in path.parts
    }


def execution(server):
    status, payload = call(server, "GET", "/api/v1/collection-executions")
    assert status == 200
    return payload["data"], payload["data"]["executions"][0]


def test_runtime_allows_exactly_one_background_driver(tmp_path):
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution",
        initialize=True,
        wall_clock=WallClock(),
        step_interval_s=60,
    )
    runtime.start()
    assert runtime.start_driver() is True
    try:
        thread = runtime._thread
        assert thread is not None and thread.is_alive()
        assert thread.name == "collection-execution-driver"
        with pytest.raises(RuntimeError, match="already started"):
            runtime.start_driver()
        assert runtime._thread is thread
    finally:
        runtime.close()


def test_invalid_platform_timeout_is_rejected_and_driver_death_latches_failure(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="platform thread timeout"):
        CollectionExecutionServiceRuntime(
            tmp_path / "invalid",
            initialize=True,
            step_interval_s=threading.TIMEOUT_MAX * 2,
        )

    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution",
        initialize=True,
        wall_clock=WallClock(),
    )
    runtime.start()

    def fail_wait(_timeout):
        raise OverflowError("injected driver wait failure")

    monkeypatch.setattr(runtime._stop, "wait", fail_wait)
    try:
        assert runtime.start_driver() is True
        assert runtime._thread is not None
        runtime._thread.join(timeout=2)
        assert not runtime._thread.is_alive()
        assert runtime.driver_state == "FAILED"
        assert runtime.failure == "OverflowError: injected driver wait failure"
    finally:
        runtime.close()


def test_maximum_platform_timeout_driver_closes_without_overflow(tmp_path):
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution",
        initialize=True,
        wall_clock=WallClock(),
        step_interval_s=threading.TIMEOUT_MAX,
    )
    runtime.start()
    try:
        assert runtime.start_driver() is True
        thread = runtime._thread
        assert thread is not None and thread.is_alive()
        runtime.close()
        assert not thread.is_alive()
        assert runtime._thread is None
        assert runtime.started is False
    finally:
        if runtime.started:
            runtime.close()


def test_http_is_available_before_background_driver_and_observes_every_stage(
    tmp_path, launch
):
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution",
        initialize=True,
        wall_clock=WallClock(),
        step_interval_s=0.5,
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    try:
        _, pending = execution(server)
        assert (pending["state"], pending["raw_quantity"]["balls"]) == (
            "PENDING",
            None,
        )
        runtime.start_driver()
        observed = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, row = execution(server)
            observed.append(
                (
                    row["state"],
                    row["raw_quantity"]["balls"],
                    row["unload_quantity"]["balls"],
                )
            )
            if row["state"] == "SUCCEEDED":
                break
            time.sleep(0.05)
        assert ("RUNNING", 600, 0) in observed
        assert observed[-1] == ("SUCCEEDED", 600, 600)
        assert runtime.driver_state == "COMPLETED"
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_real_service_runs_one_fixed_confirmation_to_equal_positive_unload(
    tmp_path, launch
):
    clock = WallClock()
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution", initialize=True, wall_clock=clock
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    try:
        initial, row = execution(server)
        assert (row["state"], row["stage"]) == (
            "PENDING",
            "WAITING_FOR_POLICY_SLOT",
        )
        assert initial["now_sim_t_s"] == 29400

        status, task_payload = call(server, "GET", "/api/v0/task-ops")
        task_view = task_payload["data"]
        assert status == 200
        assert task_view["schema"] == "nxt-pilot-dispatch/v0"
        assert task_view["environment"] == "SIMULATION"
        assert task_view["transport"] == "in_memory"
        assert task_view["available_robots"] and task_view["available_zones"]
        assert task_view["runtime"]["driver_state"] == "RUNNING"
        device = task_view["devices"]["picker-01"]
        assert set(device["as_read"]) == {"connectivity", "status_age_s", "basis"}
        task = task_view["tasks"][row["task_id"]]
        assert {
            "task_id",
            "target_robot_id",
            "zone_id",
            "state",
            "effective_result",
            "result_verification",
            "acceptance_observed",
            "evidence_incomplete",
            "reconciliation_required",
            "reconciliation_reasons",
            "last_progress_at_utc",
            "expires_at_utc",
        } <= set(task)

        before_gets = evidence_bytes(runtime.root)
        assert execution(server)[0]["now_sim_t_s"] == 29400
        assert call(server, "GET", "/api/v0/task-ops")[0] == 200
        assert evidence_bytes(runtime.root) == before_gets

        runtime.tick()
        collecting, row = execution(server)
        assert collecting["now_sim_t_s"] == 30000
        assert row["state"] == "RUNNING"
        assert row["raw_quantity"]["balls"] == 600
        assert row["unload_quantity"]["balls"] == 0

        runtime.tick()
        completed, row = execution(server)
        assert runtime.driver_state == "COMPLETED"
        assert completed["now_sim_t_s"] == 30600
        assert row["state"] == "SUCCEEDED"
        assert row["raw_quantity"]["balls"] == row["unload_quantity"]["balls"] == 600
        assert row["success_display_allowed"] is True
        assert runtime.demo.planning_snapshot()["outcomes"] == []

        committed = evidence_bytes(runtime.root)
        runtime.tick()
        assert evidence_bytes(runtime.root) == committed

        request_id = row["request_id"]
        status, receipt = call(
            server,
            "GET",
            f"/api/v1/collection-executions/requests/{request_id}",
        )
        assert status == 200 and receipt["data"]["request_id"] == request_id
        status, missing = call(
            server,
            "GET",
            "/api/v1/collection-executions/requests/missing-request",
        )
        assert status == 404
        assert missing["error"]["code"] == (
            "collection_execution_request_not_found"
        )
        assert call(
            server, "POST", "/api/v1/collection-executions", {}
        )[0] == 405

        before_rejected = evidence_bytes(runtime.root)
        status, rejected = call(
            server,
            "POST",
            "/api/v1/planning/confirmations",
            {"request_id": "unsupported-confirmation"},
        )
        assert status == 409
        assert rejected["error"]["code"] == "planning_conflict"
        status, rejected = call(
            server,
            "POST",
            "/api/v0/task-ops/schedules",
            {
                "robot_id": "picker-01",
                "zone_id": "Z1",
                "due_at_utc": "2026-09-16T08:20:00Z",
                "expires_at_utc": "2026-09-16T08:30:00Z",
                "operator": "unsupported",
            },
        )
        assert status == 409
        assert rejected["error"]["code"] == "task_ops_conflict"
        status, rejected = call(
            server,
            "POST",
            "/api/v0/task-ops/not-a-route",
            {},
        )
        assert status == 404 and rejected["error"]["code"] == "not_found"
        status, rejected = call(
            server,
            "POST",
            "/api/v1/planning/not-a-route",
            {},
        )
        assert status == 404
        assert rejected["error"]["code"] == "planning_not_found"
        assert evidence_bytes(runtime.root) == before_rejected
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_pause_freezes_business_time_while_wall_read_health_keeps_moving(
    tmp_path, launch
):
    clock = WallClock()
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution", initialize=True, wall_clock=clock
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    try:
        before, row = execution(server)
        course_session_v3.set_paused(runtime.demo.session_root, True)
        clock.advance(16)
        runtime.tick()
        paused, paused_row = execution(server)
        _, task_payload = call(server, "GET", "/api/v0/task-ops")
        assert paused["session_state"] == "PAUSED"
        assert paused["simulation_time_utc"] == before["simulation_time_utc"]
        assert paused_row == row
        assert parse_utc(task_payload["data"]["server_time_utc"]) == clock.value
        assert task_payload["data"]["scheduler"] == {
            "state": "RUNNING",
            "detail": None,
        }
        assert task_payload["data"]["runtime"]["driver_state"] == "PAUSED"

        course_session_v3.set_paused(runtime.demo.session_root, False)
        runtime.tick()
        assert execution(server)[0]["now_sim_t_s"] == 30000
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_driver_failure_is_fail_stop_readable_and_rejects_new_confirmation(
    tmp_path, launch, monkeypatch
):
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        raise OSError("injected driver failure")

    monkeypatch.setattr(runtime.demo, "advance_once", fail)
    try:
        before = evidence_bytes(runtime.root)
        runtime.tick()
        runtime.tick()
        assert calls == 1
        assert runtime.driver_state == "FAILED"
        assert runtime.failure == "OSError: injected driver failure"
        assert evidence_bytes(runtime.root) == before

        snapshot, row = execution(server)
        assert snapshot["now_sim_t_s"] == 29400 and row["state"] == "PENDING"
        _, task_payload = call(server, "GET", "/api/v0/task-ops")
        assert task_payload["data"]["scheduler"] == {
            "state": "FAILED",
            "detail": "OSError: injected driver failure",
        }
        status, rejected = call(
            server,
            "POST",
            "/api/v1/planning/confirmations",
            {"request_id": "unsupported-after-failure"},
        )
        assert status == 503
        assert rejected["error"]["code"] == "planning_unavailable"
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_postcommit_device_failure_stops_and_requires_explicit_recovery(
    tmp_path, launch, monkeypatch
):
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    assert runtime.demo.device is not None
    _, pending = execution(server)
    request_id = pending["request_id"]

    def fail_after_commit():
        raise OSError("injected device reconciliation failure")

    monkeypatch.setattr(runtime.demo.device, "consume_committed", fail_after_commit)
    try:
        assert pending["state"] == "PENDING"
        before = evidence_bytes(runtime.root)
        runtime.tick()
        committed = evidence_bytes(runtime.root)
        assert committed != before
        assert runtime.driver_state == "FAILED"
        assert runtime.failure == (
            "OSError: injected device reconciliation failure"
        )

        runtime.tick()
        assert evidence_bytes(runtime.root) == committed
        status, unavailable = call(
            server, "GET", "/api/v1/collection-executions"
        )
        assert status == 503
        assert unavailable["error"]["code"] == (
            "collection_execution_unavailable"
        )
        status, receipt = call(
            server,
            "GET",
            f"/api/v1/collection-executions/requests/{request_id}",
        )
        assert status == 200
        assert receipt["data"]["request_id"] == request_id
        status, task_payload = call(server, "GET", "/api/v0/task-ops")
        assert status == 200
        assert task_payload["data"]["scheduler"] == {
            "state": "FAILED",
            "detail": "OSError: injected device reconciliation failure",
        }
        assert evidence_bytes(runtime.root) == committed
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


@pytest.mark.parametrize("surface", ["planning", "notification"])
def test_uncertain_evidence_write_fail_stops_before_next_tick(
    tmp_path, launch, monkeypatch, surface
):
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / surface, initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path / surface)
    try:
        before = evidence_bytes(runtime.root)
        if surface == "planning":
            assert runtime.demo.planning is not None

            def fail_planning(_method, _path, _body):
                raise SiteAgentError(
                    "planning_result_unknown", "injected planning result"
                )

            monkeypatch.setattr(runtime.demo.planning, "route", fail_planning)
            status, failed = call(
                server,
                "POST",
                "/api/v1/planning/outcomes",
                {},
            )
            expected_code = "planning_result_unknown"
            expected_failure = (
                "planning_result_unknown: injected planning result"
            )
        else:
            assert runtime.demo.planning is not None

            def fail_notification(*_args):
                raise OSError("injected notification result")

            monkeypatch.setattr(
                runtime.demo.planning.schedules,
                "acknowledge",
                fail_notification,
            )
            status, failed = call(
                server,
                "POST",
                "/api/v0/task-ops/notifications/notice-unknown/acknowledge",
                {"operator": "manager", "note": "uncertain write"},
            )
            expected_code = "task_ops_unavailable"
            expected_failure = "OSError: injected notification result"

        assert status == 503
        assert failed["error"]["code"] == expected_code
        assert runtime.failure == expected_failure
        runtime.tick()
        assert evidence_bytes(runtime.root) == before
        _, task_payload = call(server, "GET", "/api/v0/task-ops")
        assert task_payload["data"]["scheduler"] == {
            "state": "FAILED",
            "detail": expected_failure,
        }
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_unexpected_session_end_fails_before_another_live_tick(
    tmp_path, monkeypatch
):
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    before = evidence_bytes(runtime.root)
    ended = {**runtime.demo.runtime_status(), "session_state": "ENDED"}
    monkeypatch.setattr(runtime.demo, "runtime_status", lambda: deepcopy(ended))
    monkeypatch.setattr(
        runtime.demo,
        "advance_once",
        lambda: pytest.fail("an ended session must not execute another tick"),
    )
    try:
        runtime.tick()
        assert runtime.driver_state == "FAILED"
        assert runtime.failure == (
            "RuntimeError: V3 session ended without terminal execution evidence"
        )
        assert evidence_bytes(runtime.root) == before
    finally:
        runtime.close()


def test_real_service_surfaces_safety_shield_rejection_without_ball_move(
    tmp_path, launch, monkeypatch
):
    original = RangeSimulation.collection_access_allowed

    def blocked(owner, zone_id):
        if zone_id == "NEAR_LEFT":
            return False
        return original(owner, zone_id)

    monkeypatch.setattr(RangeSimulation, "collection_access_allowed", blocked)
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    try:
        runtime.tick()
        _, row = execution(server)
        assert (row["state"], row["reason"]) == (
            "REJECTED",
            "SAFETY_REJECTED",
        )
        assert row["assignment_id"] is None
        assert row["raw_quantity"]["balls"] is None
        assert row["unload_quantity"]["balls"] is None
        assert row["edge_evidence"]["effective_state"] == "FAILED"
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_human_assistance_stops_driver_and_acknowledgement_cannot_unlock(
    tmp_path, launch, monkeypatch
):
    runtime = CollectionExecutionServiceRuntime(
        tmp_path / "execution", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    runtime.tick()
    original = course_session_v3.V3Session._policy_action

    def assistance(owner):
        if owner.env.sim.now >= 30000:
            index = owner.env.catalog.index_of(
                "request_human_assistance(R1,other)"
            )
            return owner._action(index)
        return original(owner)

    monkeypatch.setattr(course_session_v3.V3Session, "_policy_action", assistance)
    service, server = serve(runtime, launch, tmp_path)
    try:
        runtime.tick()
        snapshot, row = execution(server)
        assert snapshot["now_sim_t_s"] == 30600
        assert row["reason"] == "HUMAN_ASSISTANCE_REQUIRED"
        assert row["state"] != "SUCCEEDED"
        assert row["device_protection"]["authorization_blocked"] is True
        assert runtime.driver_state == "PROTECTED"

        _, task_payload = call(server, "GET", "/api/v0/task-ops")
        notices = [
            item
            for item in task_payload["data"]["notifications"]
            if item["task_id"] == row["task_id"]
        ]
        assert notices and notices[0]["requires_attention"] is True
        assert notices[0]["can_resolve"] is False
        notice = notices[0]
        before = deepcopy(task_payload["data"]["tasks"][row["task_id"]])
        status, _ = call(
            server,
            "POST",
            f"/api/v0/task-ops/notifications/{notice['notification_id']}/acknowledge",
            {"operator": "manager", "note": "On site; protection remains."},
        )
        assert status == 200
        status, resolved = call(
            server,
            "POST",
            f"/api/v0/task-ops/notifications/{notice['notification_id']}/resolve",
            {"operator": "manager", "note": "Must not clear protection."},
        )
        assert status == 200
        assert resolved["data"]["status"] == "resolved"
        _, after_payload = call(server, "GET", "/api/v0/task-ops")
        assert after_payload["data"]["tasks"][row["task_id"]] == before
        assert execution(server)[1]["device_protection"]["authorization_blocked"]
    finally:
        server.shutdown()
        service.stop()
        runtime.close()


def test_running_device_restart_becomes_unknown_and_never_reexecutes(
    tmp_path, launch
):
    root = tmp_path / "execution"
    first = CollectionExecutionServiceRuntime(
        root, initialize=True, wall_clock=WallClock()
    )
    first.start()
    first.tick()
    before = first.demo.collection_executions()["executions"][0]
    plan_count = len(first.demo.evidence()["replay_plan"])
    assert before["state"] == "RUNNING" and before["raw_quantity"]["balls"] == 600
    first.close()

    resumed = CollectionExecutionServiceRuntime(root, wall_clock=WallClock())
    resumed.start()
    service, server = serve(resumed, launch, tmp_path)
    try:
        _, row = execution(server)
        assert (row["state"], row["reason"]) == (
            "INCONCLUSIVE",
            "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME",
        )
        assert row["raw_quantity"]["status"] == "INCOMPLETE"
        assert row["unload_quantity"]["status"] == "INCOMPLETE"
        assert row["raw_quantity"]["balls"] is None
        assert row["unload_quantity"]["balls"] is None
        assert row["raw_quantity"]["source_event_ids"]
        assert row["device_protection"]["authorization_blocked"] is True
        assert resumed.driver_state == "PROTECTED"
        assert resumed.start_driver() is False
        assert resumed._thread is None
        assert len(resumed.demo.evidence()["replay_plan"]) == plan_count
        resumed.tick()
        assert len(resumed.demo.evidence()["replay_plan"]) == plan_count
        assert execution(server)[1] == row
    finally:
        server.shutdown()
        service.stop()
        resumed.close()
