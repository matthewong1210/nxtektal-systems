"""The integrated dated-task flow, exercised through the actual local API."""

from __future__ import annotations

import ast
import http.client
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nxt_edge_task.contracts import utc_text
from nxt_edge_task.executor import ROBOT_RECORD_KINDS
from nxt_edge_task.journal import JsonlJournal
from nxt_site_agent import SiteAgentApiServer, SiteAgentError
from scripts.pilot_dispatch_demo import PilotDispatchRuntime


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 16, 8, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture()
def runner(tmp_path):
    clock = Clock()
    runtime = PilotDispatchRuntime(tmp_path / "pilot", initialize=True, clock=clock, step_interval_s=0)
    runtime.start(background=False)
    runtime.tick()
    yield runtime, clock
    runtime.close()


def schedule(clock, *, delay=5, window=60):
    return {
        "robot_id": "picker-01", "zone_id": "Z1", "operator": "course-manager",
        "due_at_utc": utc_text(clock() + timedelta(seconds=delay)),
        "expires_at_utc": utc_text(clock() + timedelta(seconds=delay + window)),
    }


def cycle(runtime, clock, count=15):
    for _ in range(count):
        clock.advance(1)
        runtime.tick()
        assert runtime.failure is None


def execution_count(root):
    records = JsonlJournal(root / "robots/picker-01/robot_task_journal.jsonl", allowed_kinds=ROBOT_RECORD_KINDS).read()
    return sum(r.record_kind == "execution_started" for r in records)


def call(server, method, path, payload=None, **headers):
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    try:
        connection.request(method, path, body=None if payload is None else json.dumps(payload), headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_http_schedule_executes_once_and_restart_recovers(runner, launch, tmp_path):
    runtime, clock = runner
    service = launch(tmp_path / "site-agent")
    server = SiteAgentApiServer(service, task_operations=runtime.route)
    server.start_background()
    try:
        payload = schedule(clock)
        status, created = call(server, "POST", "/api/v0/task-ops/schedules", payload)
        assert status == 200
        status, duplicate = call(server, "POST", "/api/v0/task-ops/schedules", payload)
        assert status == 200 and duplicate["data"]["status"] == "idempotent"
        assert runtime.snapshot()["tasks"] == {}
        cycle(runtime, clock)
        status, snapshot = call(server, "GET", "/api/v0/task-ops")
        assert status == 200
        data = snapshot["data"]
        assert data["schedules"][0]["status"] == "DISPATCHED"
        assert data["schedules"][0]["schedule_id"] == created["data"]["schedule"]["schedule_id"]
        assert len(data["tasks"]) == 1
        assert next(iter(data["tasks"].values()))["state"] == "SUCCEEDED"
        assert execution_count(runtime.root) == 1
        assert data["transport"] == "in_memory" and data["environment"] == "SIMULATION"
        assert data["service_capabilities"] == {
            "schema": "nxt-pilot-dispatch/service-capabilities/v1",
            "mode": "LEGACY_PILOT_DISPATCH",
            "operations": {
                "planning_inputs_create": "SUPPORTED",
                "planning_plans_create": "SUPPORTED",
                "planning_confirmations_create": "SUPPORTED",
                "planning_outcomes_create": "SUPPORTED",
                "schedules_create": "SUPPORTED",
                "schedules_cancel": "SUPPORTED",
                "notifications_acknowledge": "SUPPORTED",
                "notifications_resolve": "SUPPORTED",
            },
        }
        # Advice state is a separate fixture: task progress did not create a
        # facility observation or recommendation behind the manager's back.
        _, state = call(server, "GET", "/api/v0/state")
        assert state["data"]["available"] is False
    finally:
        server.shutdown()
        service.stop()
    runtime.close()
    resumed = PilotDispatchRuntime(runtime.root, clock=clock, step_interval_s=0)
    try:
        resumed.start(background=False)
        cycle(resumed, clock, 5)
        assert len(resumed.snapshot()["tasks"]) == 1
        assert execution_count(runtime.root) == 1
    finally:
        resumed.close()


def test_cancel_prevents_execution(runner):
    runtime, clock = runner
    created = runtime.route("POST", "/api/v0/task-ops/schedules", schedule(clock))
    sid = created["schedule"]["schedule_id"]
    runtime.route("POST", f"/api/v0/task-ops/schedules/{sid}/cancel", {"operator": "manager"})
    cycle(runtime, clock)
    assert runtime.snapshot()["schedules"][0]["status"] == "CANCELLED"
    assert execution_count(runtime.root) == 0


def test_missed_window_is_visible_and_local_response_survives_restart(runner):
    runtime, clock = runner
    runtime.route("POST", "/api/v0/task-ops/schedules", schedule(clock, window=3))
    runtime.close()
    clock.advance(20)
    resumed = PilotDispatchRuntime(runtime.root, clock=clock, step_interval_s=0)
    try:
        resumed.start(background=False)
        resumed.tick()
        data = resumed.snapshot()
        assert data["schedules"][0]["status"] == "MISSED"
        notice = next(n for n in data["notifications"] if n["schedule_id"] is not None)
        nid = notice["notification_id"]
        response = {"operator": "manager", "note": "Reviewed missed window; schedule a new date separately."}
        resumed.route("POST", f"/api/v0/task-ops/notifications/{nid}/acknowledge", response)
        resumed.route("POST", f"/api/v0/task-ops/notifications/{nid}/resolve", response)
        assert next(n for n in resumed.snapshot()["notifications"] if n["notification_id"] == nid)["status"] == "RESOLVED"
        assert execution_count(runtime.root) == 0
    finally:
        resumed.close()
    again = PilotDispatchRuntime(runtime.root, clock=clock, step_interval_s=0)
    try:
        again.start(background=False)
        assert next(n for n in again.snapshot()["notifications"] if n["notification_id"] == nid)["status"] == "RESOLVED"
    finally:
        again.close()


def test_assistance_acknowledgement_never_unlocks_task(tmp_path):
    clock = Clock()
    runtime = PilotDispatchRuntime(tmp_path / "assistance", initialize=True, behavior="help_needs_manual_recharge", clock=clock, step_interval_s=0)
    try:
        runtime.start(background=False)
        runtime.tick()
        runtime.route("POST", "/api/v0/task-ops/schedules", schedule(clock))
        cycle(runtime, clock)
        snapshot = runtime.snapshot()
        notice = next(n for n in snapshot["notifications"] if n["task_id"] is not None)
        nid = notice["notification_id"]
        runtime.route("POST", f"/api/v0/task-ops/notifications/{nid}/acknowledge", {"operator": "manager", "note": "On site, checking."})
        with pytest.raises(SiteAgentError):
            runtime.route("POST", f"/api/v0/task-ops/notifications/{nid}/resolve", {"operator": "manager", "note": "Attempt to bypass device evidence."})
        assert runtime.snapshot()["tasks"] == snapshot["tasks"]
        assert runtime.failure is None
    finally:
        runtime.close()


def test_invalid_user_input_does_not_stop_scheduler(runner):
    runtime, _clock = runner
    for payload in ({}, {"robot_id": []}, {"operator": None}):
        with pytest.raises(SiteAgentError) as exc:
            runtime.route("POST", "/api/v0/task-ops/schedules", payload)
        assert exc.value.code == "task_ops_conflict"
        assert runtime.failure is None


def test_storage_failure_stops_loop_and_refuses_mutation(runner, monkeypatch):
    runtime, clock = runner
    def failed(_now):
        raise OSError("injected disk failure")
    monkeypatch.setattr(runtime.schedules, "tick", failed)
    runtime.tick()
    assert runtime.snapshot()["scheduler"]["state"] == "FAILED"
    with pytest.raises(SiteAgentError) as exc:
        runtime.route("POST", "/api/v0/task-ops/schedules", schedule(clock))
    assert exc.value.code == "task_ops_unavailable"
    assert runtime.snapshot()["schedules"] == []


def test_single_runner_and_explicit_initialization(runner):
    runtime, clock = runner
    contender = PilotDispatchRuntime(runtime.root, clock=clock)
    with pytest.raises(RuntimeError, match="another pilot"):
        contender.start(background=False)
    assert runtime.snapshot()["scheduler"]["state"] == "RUNNING"
    runtime.close()
    with pytest.raises(RuntimeError, match="new empty"):
        PilotDispatchRuntime(runtime.root, initialize=True).start(background=False)


def test_missing_edge_evidence_cannot_be_reinitialized_on_restart(runner):
    runtime, clock = runner
    runtime.close()
    runtime.journal.path.unlink()
    runtime.journal.anchor_path.unlink()
    with pytest.raises(RuntimeError, match="Edge evidence was lost"):
        PilotDispatchRuntime(runtime.root, clock=clock).start(background=False)


def test_new_routes_preserve_origin_and_fixture_boundaries(runner, launch, tmp_path):
    runtime, clock = runner
    service = launch(tmp_path / "service")
    server = SiteAgentApiServer(service, task_operations=runtime.route)
    server.start_background()
    try:
        status, _ = call(server, "POST", "/api/v0/task-ops/schedules", schedule(clock), Origin="https://foreign.example")
        assert status == 403 and runtime.snapshot()["schedules"] == []
        status, _ = call(server, "GET", "/api/v0/task-ops", Host="foreign.example")
        assert status == 403
        status, _ = call(server, "POST", "/api/v0/demo/advance", {})
        assert status == 200 and runtime.snapshot()["schedules"] == []
    finally:
        server.shutdown()
        service.stop()


def test_legacy_runner_has_no_task_route(launch, tmp_path):
    service = launch(tmp_path)
    server = SiteAgentApiServer(service)
    server.start_background()
    try:
        assert call(server, "GET", "/api/v0/task-ops")[0] == 404
    finally:
        server.shutdown()
        service.stop()


def test_runner_is_a_narrow_simulation_composition_root():
    path = Path(__file__).resolve().parents[2] / "scripts/pilot_dispatch_demo.py"
    tree = ast.parse(path.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(n.name.split(".")[0] for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= {"__future__", "argparse", "fcntl", "json", "signal", "sys", "threading", "datetime", "pathlib", "typing", "nxt_edge_task", "nxt_pilot_ops", "nxt_site_agent", "nxt_workflow_enablement", "scripts"}
    for forbidden in ("RobotTaskInterface", "apply_directive", "import paho", "--live", "--real-robot"):
        assert forbidden not in path.read_text()
