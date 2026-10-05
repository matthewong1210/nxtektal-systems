"""Independent fault and recovery checks for the planning composition boundary."""

from copy import deepcopy
from datetime import timedelta
import http.client
import json
from urllib.parse import quote

import pytest

from nxt_edge_task.cases import derive_edge_view
from nxt_edge_task.contracts import TaskRequest, stable_digest
from nxt_edge_task.journal import RecordSpec
from nxt_edge_task.schedules import normalized_schedule
from nxt_pilot_ops.planning_contracts import PlanningError, utc, utc_text
from nxt_pilot_ops.planning_workflow import CONFIRMATION, INPUT, PLAN
from nxt_site_agent import SiteAgentApiServer, SiteAgentError
from scripts.pilot_dispatch_demo import PilotDispatchRuntime
from tests.edge_task.conftest import FakeClock
from tests.pilot_ops.test_planning import NOW, input_request, plan_request


@pytest.fixture()
def planning_runner(tmp_path):
    clock = FakeClock(utc(NOW))
    runner = PilotDispatchRuntime(tmp_path / "pilot", initialize=True,
                                  clock=clock, step_interval_s=0)
    runner.start(background=False)
    runner.tick()
    yield runner, clock
    runner.close()


def opening_payload(**updates):
    payload = input_request()
    payload["zones"][0]["zone_id"] = "Z1"
    payload.update(updates)
    return payload


def plan_for(runner):
    runner.route_planning("POST", "/api/v1/planning/inputs", opening_payload())
    return runner.route_planning("POST", "/api/v1/planning/plans", plan_request())["record"]


def confirmation(plan, **updates):
    return {"schema": "nxt-planning-confirmation/v1", "request_id": "confirm-once",
            "plan_id": plan["plan_id"], "plan_version": plan["version"],
            "operator": "经理 李", **updates}


def http_call(server, method, path, body=None):
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    try:
        connection.request(method, path, body=None if body is None else json.dumps(body),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_confirmation_after_human_delay_keeps_frozen_plan_and_actual_admission_time(planning_runner):
    runner, clock = planning_runner
    plan = plan_for(runner)
    original = deepcopy(plan)
    clock.advance(1)
    accepted = runner.route_planning("POST", "/api/v1/planning/confirmations", confirmation(plan))
    assert accepted["record"]["request"]["operator"] == "经理 李"
    assert accepted["record"]["schedule"]["operator"].startswith("planning-")
    assert accepted["record"]["schedule"]["due_at_utc"] == plan["selection"]["start_at_utc"]
    runner.tick()
    assert runner.failure is None
    records = runner.journal.read()
    tasks = [r for r in records if r.record_kind == "task_created"]
    assert len(tasks) == 1
    assert utc(tasks[0].recorded_at_utc) == clock()
    assert utc(tasks[0].recorded_at_utc) > utc(plan["selection"]["start_at_utc"])
    assert next(r for r in records if r.record_kind == PLAN).to_dict()["payload"] == original
    result = {
        "schema": "nxt-planning-outcome/v1", "request_id": "actual-one",
        "confirmation_id": accepted["record"]["confirmation_id"],
        "task_id": tasks[0].payload["task_id"], "operator": "operator",
        "reason": "Count at collection", "stage": "COLLECTED", "quantity_balls": 10,
        "started_at_utc": plan["selection"]["start_at_utc"],
        "completed_at_utc": utc_text(clock()), "source_kind": "MANUAL_ESTIMATE",
        "source_ref": "operator:field-note", "supersedes_outcome_id": None,
    }
    with pytest.raises(SiteAgentError) as raised:
        runner.route_planning("POST", "/api/v1/planning/outcomes", result)
    assert raised.value.code == "planning_invalid_request"
    result["started_at_utc"] = utc_text(clock())
    assert runner.route_planning("POST", "/api/v1/planning/outcomes", result)["disposition"] == "created"


def test_confirmation_at_latest_start_is_refused_without_any_schedule(planning_runner):
    runner, clock = planning_runner
    plan = plan_for(runner)
    clock.now = utc(plan["candidates"][0]["latest_start_at_utc"])
    with pytest.raises(SiteAgentError) as raised:
        runner.route_planning("POST", "/api/v1/planning/confirmations", confirmation(plan))
    assert raised.value.code == "planning_expired"
    assert all(r.record_kind not in {CONFIRMATION, "schedule_created", "task_created"}
               for r in runner.journal.read())


@pytest.mark.parametrize("request_id", ["retry one", "请求/第一次?x=1"])
def test_encoded_request_id_recovers_committed_record_over_http(planning_runner, launch, tmp_path, request_id):
    runner, _ = planning_runner
    server = SiteAgentApiServer(launch(tmp_path / "site-agent"), planning_operations=runner.route_planning)
    server.start_background()
    try:
        status, payload = http_call(server, "POST", "/api/v1/planning/inputs", opening_payload(request_id=request_id))
        assert status == 200
        status, recovered = http_call(server, "GET", "/api/v1/planning/requests/" + quote(request_id, safe=""))
        assert status == 200
        assert recovered["data"]["record"] == payload["data"]["record"]
        assert recovered["data"]["request_id"] == request_id
    finally:
        server.shutdown()


@pytest.mark.parametrize("field,value", [("schema", "unknown-version"), ("expected_revision", 999)])
def test_semantically_invalid_but_byte_valid_journal_is_unavailable(planning_runner, field, value):
    runner, clock = planning_runner
    record = runner.route_planning("POST", "/api/v1/planning/inputs", opening_payload())["record"]
    damaged = deepcopy(record)
    damaged["request_id"] = "injected-record"
    damaged[field] = value
    # The append port verifies bytes/anchors; its semantic owner must reject
    # this record as unavailable evidence rather than blaming a GET request.
    runner.journal.append(RecordSpec(INPUT, "OPERATOR", utc_text(clock()), damaged))
    with pytest.raises(SiteAgentError) as raised:
        runner.route_planning("GET", "/api/v1/planning", {})
    assert raised.value.code == "planning_unavailable"
    assert runner.failure is not None


def test_invalid_stored_schedule_is_evidence_failure_not_user_conflict(planning_runner):
    runner, clock = planning_runner
    body = normalized_schedule({
        "robot_id": "picker-01", "zone_id": "Z1", "operator": "operator",
        "due_at_utc": utc_text(clock()),
        "expires_at_utc": utc_text(clock() + timedelta(minutes=1)),
    }, runner.config, runner.facts)
    body["schema"] = "unrecognized-schedule-version"
    runner.journal.append(RecordSpec("schedule_created", "OPERATOR", utc_text(clock()), {
        "schedule_id": "schedule_" + stable_digest(body)[:24], "schedule": body,
    }))
    with pytest.raises(SiteAgentError) as raised:
        runner.route_planning("GET", "/api/v1/planning", {})
    assert raised.value.code == "planning_unavailable"
    assert runner.failure is not None


def test_invalid_utf8_request_path_cannot_fail_scheduler(planning_runner, launch, tmp_path):
    runner, _ = planning_runner
    server = SiteAgentApiServer(launch(tmp_path / "site-agent"), planning_operations=runner.route_planning)
    server.start_background()
    try:
        status, payload = http_call(server, "GET", "/api/v1/planning/requests/%FF")
        assert status == 400
        assert payload["error"]["code"] == "planning_invalid_request"
        assert runner.failure is None
    finally:
        server.shutdown()


@pytest.mark.parametrize("endpoint,body", [
    ("confirmations", {}),
    ("confirmations", {"schema": "nxt-planning-confirmation/v1", "request_id": "bad", "plan_id": [], "plan_version": 1, "operator": "manager"}),
    ("inputs", {"schema": "unexpected"}),
    ("outcomes", {"task_id": "guessed"}),
])
def test_malformed_body_does_not_poison_scheduler(planning_runner, endpoint, body):
    runner, _ = planning_runner
    before = runner.journal.path.read_bytes()
    with pytest.raises(SiteAgentError) as raised:
        runner.route_planning("POST", "/api/v1/planning/" + endpoint, body)
    assert raised.value.code == "planning_invalid_request"
    assert runner.failure is None
    assert runner.journal.path.read_bytes() == before


def test_unavailable_projection_after_known_commit_does_not_erase_receipt(planning_runner, monkeypatch):
    runner, _ = planning_runner
    read = runner.journal.read

    def unavailable():
        raise OSError("read failed after mutation commit")

    monkeypatch.setattr(runner.journal, "read", unavailable)
    result = runner.route_planning("POST", "/api/v1/planning/inputs", opening_payload())
    assert result["disposition"] == "created"
    monkeypatch.setattr(runner.journal, "read", read)
    recovered = runner.route_planning("GET", "/api/v1/planning/requests/opening", {})
    assert recovered["record"] == result["record"]


def test_lost_confirmation_ack_recovers_one_schedule_and_one_task(planning_runner, monkeypatch):
    runner, clock = planning_runner
    plan = plan_for(runner)
    request = confirmation(plan)
    append = runner.journal.append_via

    def commit_then_disconnect(builder):
        append(builder)
        raise OSError("connection lost after durable confirmation")

    with monkeypatch.context() as patch:
        patch.setattr(runner.journal, "append_via", commit_then_disconnect)
        with pytest.raises(SiteAgentError) as raised:
            runner.route_planning("POST", "/api/v1/planning/confirmations", request)
    assert raised.value.code == "planning_result_unknown"
    assert runner.failure is not None
    recovered = runner.route_planning("GET", "/api/v1/planning/requests/confirm-once", {})
    assert recovered["record"]["plan_id"] == plan["plan_id"]
    assert not any(r.record_kind == "task_created" for r in runner.journal.read())
    runner.close()
    restarted = PilotDispatchRuntime(runner.root, initialize=False, clock=clock, step_interval_s=0)
    restarted.start(background=False)
    try:
        clock.advance(1)
        restarted.tick()
        assert restarted.failure is None
        repeated = restarted.route_planning("POST", "/api/v1/planning/confirmations", request)
        assert repeated["disposition"] == "duplicate"
        assert repeated["record"] == recovered["record"]
        for _ in range(15):
            clock.advance(1)
            restarted.tick()
            assert restarted.failure is None
        kinds = [r.record_kind for r in restarted.journal.read()]
        assert kinds.count(CONFIRMATION) == 1
        assert kinds.count("schedule_created") == 1
        assert kinds.count("task_created") == 1
    finally:
        restarted.close()


def test_input_correction_after_dispatch_cannot_rewrite_bound_task(planning_runner):
    runner, clock = planning_runner
    plan = plan_for(runner)
    request = confirmation(plan)
    runner.route_planning("POST", "/api/v1/planning/confirmations", request)
    runner.tick()
    assert runner.failure is None
    task = next(r for r in runner.journal.read() if r.record_kind == "task_created")
    correction = opening_payload(request_id="close-zone", expected_revision=1)
    correction["zones"][0]["collection_allowed"]["value"] = False
    runner.route_planning("POST", "/api/v1/planning/inputs", correction)
    clock.advance(1)
    runner.tick()
    assert runner.failure is None
    tasks = [r for r in runner.journal.read() if r.record_kind == "task_created"]
    assert tasks == [task]
    snapshot = runner.route_planning("GET", "/api/v1/planning", {})
    assert snapshot["confirmations"][0]["task_id"] == task.payload["task_id"]
    assert snapshot["outcomes"] == []


def inject_mismatched_task(runner, clock, mismatch):
    plan = plan_for(runner)
    accepted = runner.route_planning("POST", "/api/v1/planning/confirmations", confirmation(plan))["record"]
    runner.planning.recover()
    schedule = runner.schedules.snapshot(clock())["schedules"][0]
    device = derive_edge_view(runner.config, runner.journal.read()).devices["picker-01"]
    wire = TaskRequest.build(
        site_id=runner.config.site_id, deployment_id=runner.config.deployment_id,
        simulation_env_id=runner.config.simulation_env_id, target_robot_id="picker-01",
        target_incarnation=device.incarnation, task_type="COLLECT_BALLS_ZONE",
        zone_id="wrong-zone" if mismatch == "zone_id" else schedule["zone_id"],
        issued_at_utc=utc_text(clock() + timedelta(seconds=1)) if mismatch == "issued_at_utc" else schedule["due_at_utc"],
        expires_at_utc=schedule["expires_at_utc"], progress_window_s=schedule["progress_window_s"],
        issued_by="SIMULATION_TEST_ENTRY:" + schedule["operator"],
    )
    runner.journal.append(RecordSpec("task_created", "SIM_ENTRY", utc_text(clock()), {
        "task_id": "task_unrelated" if mismatch == "task_id" else wire.task_id,
        "request": wire.to_dict(), "schedule_id": accepted["schedule_id"],
        "provenance": {"issued_by": wire.issued_by, "manifest_digest": runner.facts.manifest_digest,
                       "config_digest": runner.config.config_digest, "environment": "SIMULATION"},
    }))


@pytest.mark.parametrize("mismatch", ["task_id", "zone_id", "issued_at_utc"])
def test_byte_valid_task_record_must_match_its_frozen_schedule(planning_runner, mismatch):
    runner, clock = planning_runner
    inject_mismatched_task(runner, clock, mismatch)
    with pytest.raises(SiteAgentError) as raised:
        runner.route_planning("GET", "/api/v1/planning", {})
    assert raised.value.code == "planning_unavailable"
    assert runner.failure is not None


def test_restart_verifies_bad_task_binding_before_gateway_or_double_start(planning_runner, monkeypatch):
    runner, clock = planning_runner
    inject_mismatched_task(runner, clock, "zone_id")
    runner.close()
    from scripts.pilot_dispatch_demo import EdgeGateway, MockRobotDevice

    started = []

    def forbidden_start(component):
        started.append(type(component).__name__)
        raise AssertionError("invalid evidence reached transport/device start")

    monkeypatch.setattr(EdgeGateway, "start", forbidden_start)
    monkeypatch.setattr(MockRobotDevice, "start", forbidden_start)
    restarted = PilotDispatchRuntime(runner.root, initialize=False, clock=clock, step_interval_s=0)
    try:
        with pytest.raises(PlanningError, match="task payload differs"):
            restarted.start(background=False)
        assert started == []
        assert restarted.devices == []
    finally:
        restarted.close()


def test_unreadable_planning_evidence_stops_before_pending_task_publication(planning_runner):
    runner, clock = planning_runner
    plan = plan_for(runner)
    runner.route_planning("POST", "/api/v1/planning/confirmations", confirmation(plan))
    runner.tick()
    assert runner.failure is None
    assert any(r.record_kind == "task_created" for r in runner.journal.read())
    published_before = list(runner.gateway.publish_calls)
    damaged = runner.route_planning("GET", "/api/v1/planning", {})["latest_input"]
    damaged["schema"] = "unknown-input-version"
    damaged["request_id"] = "damaged-before-publish"
    runner.journal.append(RecordSpec(INPUT, "OPERATOR", utc_text(clock()), damaged))
    clock.advance(1)
    runner.tick()
    assert runner.failure is not None
    assert runner.gateway.publish_calls == published_before
