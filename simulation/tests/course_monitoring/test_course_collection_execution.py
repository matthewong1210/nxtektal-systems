"""Durable composition evidence, checked against the independent 3A oracle."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from nxt_edge_task.contracts import TaskRequest, TaskEvent, EventKind
from nxt_edge_task.journal import JsonlJournal, JournalRecord, RecordSpec, JournalIntegrityError
from nxt_range_ops.core.assignment_evidence import AssignmentCandidate, AssignmentEvidence

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("wire_oracle", ROOT / "tests/pilot_ops/test_collection_execution_wire_contract.py")
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
SCHEMA = json.loads((ROOT / "docs/contracts/collection-execution-v1/schema.json").read_text())


def test_durable_execution_surface_exists():
    assert (ROOT / "scripts/course_collection_execution.py").exists(), "durable V3 execution store is not implemented"


@pytest.fixture
def api():
    path = ROOT / "scripts/course_collection_execution.py"
    assert path.exists(), "durable V3 execution store is not implemented"
    from scripts import course_collection_execution
    return course_collection_execution


def digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def inputs(tmp_path):
    b = oracle.snapshot()["bindings"][0]
    identity = {k: b[k] for k in ("series_id", "session_id", "round_id", "round_index", "engine_digest", "config_digest", "session_epoch_utc", "control_interval_s", "session_end_sim_t_s", "site_id", "deployment_id", "commissioned_site_digest")}
    identity.update(schema="nxt-whole-course-session/v3", environment="SIMULATION", station_ids=["H1"],
                    runtime_bindings=[{"robot_id": "picker-01", "zone_id": "Z1", "runtime_robot_id": "R1", "runtime_zone_id": "NEAR_LEFT", "handoff_station_id": "H1"}])
    task = TaskRequest.build(site_id=b["site_id"], deployment_id=b["deployment_id"], simulation_env_id="fixture", target_robot_id="picker-01", target_incarnation="fixture-incarnation-001", task_type="COLLECT_BALLS_ZONE", zone_id="Z1", issued_at_utc="2026-09-16T00:01:00Z", expires_at_utc="2026-09-16T00:05:00Z", progress_window_s=60, issued_by="SIMULATION_TEST_ENTRY:manager")
    cycle = b["cycle_evidence"]
    evidence = {k: cycle[k] for k in ("source_kind", "source_ref", "observed_at_utc", "valid_until_utc")}
    evidence["value"] = dict(cycle["cycle_minutes"], wash=3, supply=1)
    source = {"request_id": "input-001", "revision": 1, "input_digest": "a"*64, "zones": [{"robot_id": "picker-01", "zone_id": "Z1", "cycle_minutes": evidence}]}
    schedule = {"robot_id": "picker-01", "zone_id": "Z1", "due_at_utc": task.issued_at_utc, "expires_at_utc": task.expires_at_utc, "operator": "manager", "admission_reference": "confirmation-001"}
    confirmation = {"confirmation_id": "confirmation-001", "plan_id": "plan-001", "plan_version": 1, "input_revision": 1, "schedule_id": "schedule-001", "schedule": schedule, "task_id": task.task_id, "task_created_at_utc": task.issued_at_utc}
    plan = {"plan_id": "plan-001", "version": 1, "input_revision": 1, "input_digest": "a"*64, "selection": {"robot_id": "picker-01", "zone_id": "Z1", "start_at_utc": task.issued_at_utc}}
    planning = {"schema": "nxt-planning/v1", "environment": "SIMULATION", "latest_input": source, "plans": [plan], "confirmations": [confirmation]}
    journal = JsonlJournal(tmp_path / "edge.jsonl")
    journal.append(RecordSpec("task_created", "EDGE", task.issued_at_utc, {"task_id": task.task_id, "schedule_id": "schedule-001", "request": task.to_dict()}))
    return identity, planning, journal.read()


def setup(api, tmp_path):
    identity, planning, edge = inputs(tmp_path)
    store = api.CollectionExecutionStore(tmp_path / "execution.jsonl", identity)
    binding = store.bind_confirmed_tasks(planning, edge, identity, 60)[0]
    request = api.make_request(binding, "request-001", "2026-09-16T00:01:00Z", "2026-09-16T00:05:00Z")
    store.submit(request)
    store.record_acceptance(request["execution_id"], "edge-accepted")
    return store, binding, request


def setup_without_acceptance(api, tmp_path):
    identity, planning, edge_records = inputs(tmp_path / "binding")
    store = api.CollectionExecutionStore(tmp_path / "execution.jsonl", identity)
    binding = store.bind_confirmed_tasks(
        planning, edge_records, identity, 60
    )[0]
    request = api.make_request(
        binding,
        "request-preacceptance",
        "2026-09-16T00:01:00Z",
        "2026-09-16T00:05:00Z",
    )
    store.submit(request)
    return store, binding, request


def append_incarnation_rejection(tmp_path, request, **overrides):
    values = dict(
        site_id="synthetic-site",
        deployment_id="synthetic-sim",
        simulation_env_id="fixture",
        task_id=request["task_id"],
        robot_id="picker-01",
        boot_id="fixture-incarnation-002-2",
        boot_sequence=2,
        event_sequence=0,
        kind=EventKind.REJECTED,
        reason_code="incarnation_mismatch",
        detail="request targets a prior device incarnation",
        reported_at_utc="2026-09-16T00:01:00Z",
        phase=None,
    )
    values.update(overrides)
    event = TaskEvent(**values)
    journal = JsonlJournal(tmp_path / "device.jsonl")
    return journal.append(RecordSpec(
        "task_event_persisted",
        "DEVICE",
        event.reported_at_utc,
        {"event": event.to_dict()},
    ))


WAIT = {"name": "Wait", "index": 0, "robot_id": None, "target_id": None}
COLLECT = {"name": "AssignCollection", "index": 1, "robot_id": "R1", "target_id": "NEAR_LEFT"}
UNLOAD = {"name": "SendToHandoff", "index": 2, "robot_id": "R1", "target_id": None}
PAUSE = {"name": "PauseRobot", "index": 3, "robot_id": "R1", "target_id": None}


def view(identity=None):
    return {"session_id": "fixture-session-v3", "round_id": "round-001", "robots": {"R1": {"activity": "IDLE", "payload_balls": 0}}, "catalog_actions": [WAIT, COLLECT, UNLOAD, PAUSE], "continuations": {}}


def conform(store, **kwargs):
    snap = store.snapshot(server_time_utc="2026-09-24T00:00:00Z", **kwargs)
    oracle.validator(SCHEMA, "#/$defs/ExecutionSnapshot").validate(snap)
    oracle.relations(snap)
    return snap


def tick(store, now, runtime=None, action=WAIT, snapshots=None, shield=None):
    d = store.arbitrate(action, now, runtime or view())
    p = store.prepare_tick(d, previous_cursor=store.replay()["tick_sequence"], previous_digest=store.replay()["replay_digest"])
    c = store.commit_tick(p, now_sim_t_s=now + 60, runtime_snapshots=snapshots or {}, safety_shield=shield or {"allowed": True, "reason": None}, post_state_digest="f"*64)
    store.publish_cursor(c["tick_sequence"], c["replay_digest"])
    for entry in store.committed_outbox(unconfirmed_only=True):
        store.confirm_outbox(entry["outbox_id"], "event-" + entry["outbox_id"])
    return c


def assignment(request, *, terminal=False, exit_reason="ROBOT_PAYLOAD_FULL", balls=7):
    e = AssignmentEvidence(AssignmentCandidate(request["execution_id"], "R1", "NEAR_LEFT", "H1", 720), 60)
    e.append("ASSIGNMENT_STARTED", 60, payload_after=0)
    if balls:
        e.append("RAW_COLLECTED_TO_ROBOT", 70, balls=balls, source_location="zone:NEAR_LEFT", destination_location="robot:R1", payload_after=balls)
    if terminal:
        e.append("COLLECTION_EXIT", 80, reason=exit_reason, payload_after=balls)
        if exit_reason != "POLICY_PREEMPTED":
            e.append("UNLOADED_TO_STATION", 130, balls=balls, source_location="robot:R1", destination_location="station:H1", station_id="H1", payload_after=0)
        e.append("ASSIGNMENT_TERMINAL", 140, reason="UNLOADED_ALL_COLLECTED_BALLS" if exit_reason == "ROBOT_PAYLOAD_FULL" else exit_reason, payload_after=0)
    return e.snapshot()


def restart(store, req, tmp_path, *, running, now=120):
    event = TaskEvent(site_id="synthetic-site",deployment_id="synthetic-sim",simulation_env_id="fixture",task_id=req["task_id"],robot_id="picker-01",boot_id="fixture-incarnation-001-2",boot_sequence=2,event_sequence=1,
                      kind=EventKind.INCONCLUSIVE if running else EventKind.FAILED,
                      reason_code="interrupted_execution_unknown_outcome" if running else "not_started_after_restart",detail="persisted Edge restart evidence",reported_at_utc="2026-09-16T00:02:00Z",phase=None)
    journal = JsonlJournal(tmp_path / "device.jsonl")
    record = journal.append(RecordSpec("task_event_persisted","DEVICE",event.reported_at_utc,{"event":event.to_dict()}))
    store.record_device_restart_outcome(req["execution_id"],record,now_sim_t_s=now)
    return record


def test_preacceptance_incarnation_rejection_projects_verified_identity_terminal(api, tmp_path):
    store, _binding, request = setup_without_acceptance(api, tmp_path)
    edge_record = append_incarnation_rejection(tmp_path, request)

    store.record_preacceptance_rejection(
        request["execution_id"], edge_record, now_sim_t_s=60
    )

    record = conform(store)["executions"][0]
    assert (record["state"], record["reason"], record["stage"]) == (
        "REJECTED", "IDENTITY_CONFLICT", "TERMINAL"
    )
    assert record["started_sim_t_s"] is None
    assert record["execution_deadline_sim_t_s"] is None
    assert record["terminal_sim_t_s"] == 60
    assert record["assignment_id"] is None
    assert record["actions"] == []
    assert record["raw_quantity"]["status"] == "NOT_REACHED"
    assert record["unload_quantity"]["status"] == "NOT_REACHED"
    assert record["raw_quantity"]["balls"] is None
    assert record["unload_quantity"]["balls"] is None
    assert record["edge_evidence"] == {
        "task_id": request["task_id"],
        "accepted": False,
        "verified": True,
        "effective_state": "REJECTED",
        "reason": "incarnation_mismatch",
        "terminal_states": ["REJECTED"],
        "event_ids": [edge_record.record_id],
        "result_verification": "VERIFIED",
    }
    assert record["conflicts"]["incarnation_mismatch"] is True
    assert record["device_protection"] == {
        "protected": True,
        "reasons": ["INCARNATION_MISMATCH"],
        "authorization_blocked": True,
    }


def test_preacceptance_incarnation_rejection_is_record_idempotent(api, tmp_path):
    store, _binding, request = setup_without_acceptance(api, tmp_path)
    edge_record = append_incarnation_rejection(tmp_path, request)
    store.record_preacceptance_rejection(
        request["execution_id"], edge_record, now_sim_t_s=60
    )
    before = store.journal.path.read_bytes()

    store.record_preacceptance_rejection(
        request["execution_id"], edge_record, now_sim_t_s=60
    )

    assert store.journal.path.read_bytes() == before
    reopened = api.CollectionExecutionStore(store.journal.path, store.session_identity)
    assert conform(reopened) == conform(store)


def test_preacceptance_rejection_retry_rejects_boolean_record_sequence_alias(api, tmp_path):
    store, _binding, request = setup_without_acceptance(api, tmp_path)
    edge_record = append_incarnation_rejection(tmp_path, request)
    store.record_preacceptance_rejection(
        request["execution_id"], edge_record, now_sim_t_s=60
    )
    before = store.journal.path.read_bytes()
    malformed = JournalRecord(
        edge_record.schema_version,
        True,
        edge_record.record_id,
        edge_record.record_kind,
        edge_record.origin,
        edge_record.recorded_at_utc,
        edge_record.payload,
    )

    with pytest.raises(api.CollectionExecutionError):
        store.record_preacceptance_rejection(
            request["execution_id"], malformed, now_sim_t_s=60
        )

    assert store.journal.path.read_bytes() == before


def test_preacceptance_rejection_retry_rejects_boolean_event_sequence_alias(api, tmp_path):
    store, _binding, request = setup_without_acceptance(api, tmp_path)
    edge_record = append_incarnation_rejection(tmp_path, request)
    store.record_preacceptance_rejection(
        request["execution_id"], edge_record, now_sim_t_s=60
    )
    before = store.journal.path.read_bytes()
    payload = edge_record.to_dict()["payload"]
    payload["event"]["event_sequence"] = False
    malformed = JournalRecord(
        edge_record.schema_version,
        edge_record.sequence,
        edge_record.record_id,
        edge_record.record_kind,
        edge_record.origin,
        edge_record.recorded_at_utc,
        payload,
    )

    with pytest.raises(api.CollectionExecutionError):
        store.record_preacceptance_rejection(
            request["execution_id"], malformed, now_sim_t_s=60
        )

    assert store.journal.path.read_bytes() == before


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_sequence": 1},
        {"reason_code": "invalid_request"},
        {"boot_id": "fixture-incarnation-001-2"},
        {"robot_id": "picker-02"},
        {"site_id": "other-site"},
        {"deployment_id": "other-deployment"},
        {"task_id": "task_" + "a" * 24},
    ],
)
def test_preacceptance_rejection_near_misses_fail_without_append(api, tmp_path, overrides):
    store, _binding, request = setup_without_acceptance(api, tmp_path)
    edge_record = append_incarnation_rejection(tmp_path, request, **overrides)
    before = store.journal.path.read_bytes()

    with pytest.raises(api.CollectionExecutionError):
        store.record_preacceptance_rejection(
            request["execution_id"], edge_record, now_sim_t_s=60
        )

    assert store.journal.path.read_bytes() == before


def test_preacceptance_rejection_refuses_a_second_record_id(api, tmp_path):
    store, _binding, request = setup_without_acceptance(api, tmp_path)
    first = append_incarnation_rejection(tmp_path / "first", request)
    second = append_incarnation_rejection(
        tmp_path / "second",
        request,
        detail="same verified rejection carried by a different record",
    )
    assert first.record_id != second.record_id
    store.record_preacceptance_rejection(
        request["execution_id"], first, now_sim_t_s=60
    )
    before = store.journal.path.read_bytes()

    with pytest.raises(api.CollectionExecutionError):
        store.record_preacceptance_rejection(
            request["execution_id"], second, now_sim_t_s=60
        )

    assert store.journal.path.read_bytes() == before


def test_binding_is_content_addressed_and_reads_cycle_at_binding_time(api, tmp_path):
    store, b, request = setup(api, tmp_path)
    assert b["max_execution_s"] == 660
    assert b["binding_id"] == digest({k: v for k, v in b.items() if k != "binding_id"})
    assert b["bound_at_utc"] == "2026-09-16T00:01:00Z"
    assert conform(store)["executions"][0]["raw_quantity"]["balls"] is None


def test_binding_uses_confirmation_historical_input_after_newer_revision(api, tmp_path):
    from nxt_edge_task.schedules import normalized_schedule
    from nxt_pilot_ops.planning_contracts import utc_text
    from tests.pilot_ops.test_planning import planning_operations, confirmed_plan_request, input_request

    operations = planning_operations(tmp_path / "planning")
    payload = confirmed_plan_request(operations)
    confirmation = operations.route("POST", "/api/v1/planning/confirmations", payload)["record"]
    operations.recover()
    second = input_request()
    second.update(request_id="newer-input", expected_revision=1)
    second["zones"][0]["zone_id"] = operations.context["zone_ids"][0]
    second["zones"][0]["cycle_minutes"]["value"].update(travel=3, collect=7, **{"return": 3, "unload": 3})
    operations.route("POST", "/api/v1/planning/inputs", second)

    # Inject verified historical TASK_CREATED evidence after revision 2. This
    # is a binding replay test, not an assertion that the due-time gate admits
    # a superseded plan (its existing current-input rule remains unchanged).
    schedule = normalized_schedule(confirmation["schedule"], operations.config, operations.facts)
    task = TaskRequest.build(site_id=schedule["site_id"], deployment_id=schedule["deployment_id"],
        simulation_env_id=schedule["simulation_env_id"], target_robot_id=schedule["robot_id"],
        target_incarnation="fixture-incarnation-001", task_type="COLLECT_BALLS_ZONE",
        zone_id=schedule["zone_id"], issued_at_utc=schedule["due_at_utc"],
        expires_at_utc=schedule["expires_at_utc"], progress_window_s=schedule["progress_window_s"],
        issued_by="SIMULATION_TEST_ENTRY:" + schedule["operator"])
    operations.journal.append(RecordSpec("task_created", "EDGE", utc_text(operations.clock()),
        {"task_id": task.task_id, "schedule_id": confirmation["schedule_id"], "request": task.to_dict()}))
    planning = operations.execution_binding_snapshot(operations.clock())
    identity, _, _ = inputs(tmp_path / "identity")
    identity.update(site_id=operations.config.site_id, deployment_id=operations.config.deployment_id,
                    session_epoch_utc=utc_text(operations.clock()))
    identity["runtime_bindings"][0]["zone_id"] = schedule["zone_id"]
    planning["input_records"].reverse()  # selection is by exact revision/digest, never order
    requirements = api.derive_execution_requirements(plan=planning["plans"][0],
        input_records=planning["input_records"], session_identity=identity,
        evidence_at_utc=planning["confirmations"][0]["task_created_at_utc"])
    assert requirements["cycle_evidence"]["input_revision"] == 1
    assert requirements["cycle_evidence"]["cycle_minutes"] == {"travel": 1, "collect": 1, "return": 1, "unload": 1}
    assert requirements["max_execution_s"] == 240
    assert planning["latest_input"]["revision"] == 2
    store = api.CollectionExecutionStore(tmp_path / "execution.jsonl", identity)
    binding = store.bind_confirmed_task(planning, operations.journal.read(), identity, 0, task_id=task.task_id)
    assert binding["cycle_evidence"] == requirements["cycle_evidence"]
    assert binding["task_created_at_utc"] == planning["confirmations"][0]["task_created_at_utc"]


@pytest.mark.parametrize("fault", ["missing_input", "wrong_digest", "duplicate_input", "missing_mapping", "duplicate_mapping"])
def test_execution_requirements_reject_missing_or_ambiguous_sources(api, tmp_path, fault):
    identity, planning, _ = inputs(tmp_path)
    sources = [planning["latest_input"]]
    if fault == "missing_input": sources = []
    if fault == "wrong_digest": sources[0]["input_digest"] = "b" * 64
    if fault == "duplicate_input": sources *= 2
    if fault == "missing_mapping": identity["runtime_bindings"] = []
    if fault == "duplicate_mapping": identity["runtime_bindings"] *= 2
    with pytest.raises(api.CollectionExecutionError):
        api.derive_execution_requirements(plan=planning["plans"][0], input_records=sources,
            session_identity=identity, evidence_at_utc=planning["confirmations"][0]["task_created_at_utc"])


@pytest.mark.parametrize(("observed", "expires", "accepted"), [
    ("2026-09-16T00:01:00Z", "2026-09-16T00:01:00Z", True),
    ("2026-09-16T00:01:01Z", "2026-09-16T00:02:00Z", False),
    ("2026-09-16T00:00:00Z", "2026-09-16T00:00:59Z", False),
])
def test_execution_requirements_cycle_validity_and_four_stage_ceiling(api, tmp_path, observed, expires, accepted):
    identity, planning, _ = inputs(tmp_path)
    source = planning["latest_input"]
    cycle = source["zones"][0]["cycle_minutes"]
    cycle.update(observed_at_utc=observed, valid_until_utc=expires)
    cycle["value"].update(travel=1, collect=1.1, **{"return": 1, "unload": 1, "wash": 100, "supply": 100})
    args = dict(plan=planning["plans"][0], input_records=[source], session_identity=identity,
                evidence_at_utc="2026-09-16T00:01:00Z")
    if not accepted:
        with pytest.raises(api.CollectionExecutionError, match="stale or future"):
            api.derive_execution_requirements(**args)
    else:
        assert api.derive_execution_requirements(**args)["max_execution_s"] == 300


@pytest.mark.parametrize(("limit", "accepted"), [(660, True), (661, False)])
def test_session_horizon_includes_exact_end(api, tmp_path, limit, accepted):
    identity, _, _ = inputs(tmp_path)
    identity["session_end_sim_t_s"] = 720
    args = dict(start_at_utc="2026-09-16T00:01:00Z", max_execution_s=limit, session_identity=identity)
    if accepted:
        assert api.require_session_horizon(**args) is None
    else:
        with pytest.raises(api.CollectionExecutionError, match="session horizon"):
            api.require_session_horizon(**args)


def test_request_id_is_derived_only_from_binding_id(api):
    binding_id = "a" * 64
    expected = "exec_req_" + digest({"schema": "nxt-collection-execution-request-id/v1",
        "kind": "EXECUTE_BOUND_COLLECTION", "binding_id": binding_id})[:24]
    assert api.request_id_for_binding(binding_id) == expected
    assert api.request_id_for_binding("b" * 64) != expected


@pytest.mark.parametrize("binding_id", [None, 64, "", "a" * 63, "a" * 65])
def test_request_id_rejects_invalid_binding_id(api, binding_id):
    with pytest.raises(api.CollectionExecutionError, match="invalid binding ID"):
        api.request_id_for_binding(binding_id)


def test_single_task_binding_is_exact_and_ignores_other_confirmations(api, tmp_path):
    identity, planning, records = inputs(tmp_path)
    task_id = planning["confirmations"][0]["task_id"]
    expected = api.bind_confirmed_tasks(planning, records, identity, 60)[0]
    planning["confirmations"].append({"task_id": "unrelated-not-yet-bindable"})
    assert api.bind_confirmed_task(planning, records, identity, 60, task_id=task_id) == expected
    with pytest.raises(api.CollectionExecutionError, match="one confirmed planning source"):
        api.bind_confirmed_task(planning, records, identity, 60, task_id="unknown")
    planning["confirmations"].append(deepcopy(planning["confirmations"][0]))
    with pytest.raises(api.CollectionExecutionError, match="one confirmed planning source"):
        api.bind_confirmed_task(planning, records, identity, 60, task_id=task_id)


def test_single_task_store_retry_returns_persisted_binding_after_clock_advances(api, tmp_path):
    identity, planning, records = inputs(tmp_path)
    task_id = planning["confirmations"][0]["task_id"]
    planning["confirmations"].append({"task_id": "unrelated-not-yet-bindable"})
    store = api.CollectionExecutionStore(tmp_path / "execution.jsonl", identity)
    first = store.bind_confirmed_task(planning, records, identity, 60, task_id=task_id)
    before = store.journal.path.read_bytes(), store.journal.anchor_path.read_bytes()
    reopened = api.CollectionExecutionStore(store.journal.path, identity)
    again = reopened.bind_confirmed_task(planning, records, identity, 120, task_id=task_id)
    assert again == first
    assert api.request_id_for_binding(again["binding_id"]) == api.request_id_for_binding(first["binding_id"])
    assert (store.journal.path.read_bytes(), store.journal.anchor_path.read_bytes()) == before
    planning["latest_input"]["zones"][0]["cycle_minutes"]["value"]["collect"] += 1
    with pytest.raises(api.CollectionExecutionError, match="bound differently"):
        reopened.bind_confirmed_task(planning, records, identity, 120, task_id=task_id)
    assert (store.journal.path.read_bytes(), store.journal.anchor_path.read_bytes()) == before


@pytest.mark.parametrize("fault", ["planning_schema", "session_schema", "clock"])
def test_empty_batch_still_validates_its_evidence_boundary(api, tmp_path, fault):
    identity, planning, records = inputs(tmp_path)
    planning["confirmations"] = []
    now = 60
    if fault == "planning_schema": planning["schema"] = "nxt-planning/unknown"
    if fault == "session_schema": identity["schema"] = "nxt-whole-course-session/v2"
    if fault == "clock": now = -1
    with pytest.raises(api.CollectionExecutionError):
        api.bind_confirmed_tasks(planning, records, identity, now)


def test_binding_accepts_planning_seconds_and_keeps_its_frozen_window(api, tmp_path):
    identity, planning, records = inputs(tmp_path / "fixture")
    schedule = planning["confirmations"][0]["schedule"]
    schedule["due_at_utc"] = "2026-09-16T00:01:00Z"
    schedule["expires_at_utc"] = "2026-09-16T00:05:00Z"
    planning["plans"][0]["selection"]["start_at_utc"] = schedule["due_at_utc"]
    source_task = TaskRequest.from_dict(records[0].to_dict()["payload"]["request"])
    task = TaskRequest.build(
        site_id=source_task.site_id, deployment_id=source_task.deployment_id,
        simulation_env_id=source_task.simulation_env_id,
        target_robot_id=source_task.target_robot_id,
        target_incarnation=source_task.target_incarnation,
        task_type=source_task.task_type, zone_id=source_task.zone_id,
        issued_at_utc="2026-09-16T00:01:00.000000Z",
        expires_at_utc="2026-09-16T00:05:00.000000Z",
        progress_window_s=source_task.progress_window_s,
        issued_by=source_task.issued_by,
    )
    planning["confirmations"][0].update(
        task_id=task.task_id,
        task_created_at_utc="2026-09-16T00:01:00.000000Z",
    )
    edge = JsonlJournal(tmp_path / "normalized-edge.jsonl")
    edge.append(RecordSpec(
        "task_created", "EDGE", "2026-09-16T00:01:00.000000Z",
        {"task_id": task.task_id, "schedule_id": "schedule-001", "request": task.to_dict()},
    ))
    records = edge.read()
    store = api.CollectionExecutionStore(tmp_path / "execution.jsonl", identity)

    binding = store.bind_confirmed_tasks(planning, records, identity, 60)[0]
    window = store.replay()["windows"][binding["binding_id"]]
    assert window == {
        "binding_id": binding["binding_id"],
        "due_at_utc": "2026-09-16T00:01:00Z",
        "expires_at_utc": "2026-09-16T00:05:00Z",
    }
    request = api.make_request(
        binding, "planning-normalized", window["due_at_utc"], window["expires_at_utc"]
    )
    assert store.submit(request)["request_id"] == "planning-normalized"


@pytest.mark.parametrize(
    ("field", "different"),
    [
        ("due_at_utc", "2026-09-16T00:01:01Z"),
        ("expires_at_utc", "2026-09-16T00:05:01Z"),
    ],
)
def test_binding_rejects_a_different_planning_task_instant(
    api, tmp_path, field, different
):
    identity, planning, records = inputs(tmp_path / "fixture")
    schedule = planning["confirmations"][0]["schedule"]
    schedule["due_at_utc"] = "2026-09-16T00:01:00Z"
    schedule["expires_at_utc"] = "2026-09-16T00:05:00Z"
    schedule[field] = different
    planning["plans"][0]["selection"]["start_at_utc"] = schedule["due_at_utc"]
    source_task = TaskRequest.from_dict(records[0].to_dict()["payload"]["request"])
    task = TaskRequest.build(
        site_id=source_task.site_id, deployment_id=source_task.deployment_id,
        simulation_env_id=source_task.simulation_env_id,
        target_robot_id=source_task.target_robot_id,
        target_incarnation=source_task.target_incarnation,
        task_type=source_task.task_type, zone_id=source_task.zone_id,
        issued_at_utc="2026-09-16T00:01:00.000000Z",
        expires_at_utc="2026-09-16T00:05:00.000000Z",
        progress_window_s=source_task.progress_window_s,
        issued_by=source_task.issued_by,
    )
    planning["confirmations"][0].update(
        task_id=task.task_id,
        task_created_at_utc="2026-09-16T00:01:00.000000Z",
    )
    edge = JsonlJournal(tmp_path / "normalized-edge.jsonl")
    edge.append(RecordSpec(
        "task_created", "EDGE", "2026-09-16T00:01:00.000000Z",
        {"task_id": task.task_id, "schedule_id": "schedule-001", "request": task.to_dict()},
    ))
    records = edge.read()

    with pytest.raises(api.CollectionExecutionError, match="frozen confirmation"):
        api.bind_confirmed_tasks(planning, records, identity, 60)


@pytest.mark.parametrize("fault", ["stale", "future", "stations", "wrong_station", "wrong_round", "task_missing", "task_duplicate", "input_revision", "mapping_duplicate", "content"])
def test_binding_rejects_ambiguous_or_stale_chain(api, tmp_path, fault):
    identity, planning, records = inputs(tmp_path)
    if fault == "stale": planning["latest_input"]["zones"][0]["cycle_minutes"]["valid_until_utc"] = "2026-09-16T00:00:59Z"
    if fault == "future": planning["latest_input"]["zones"][0]["cycle_minutes"]["observed_at_utc"] = "2026-09-16T00:01:01Z"
    if fault == "stations": identity["station_ids"].append("H2")
    if fault == "wrong_station": identity["runtime_bindings"][0]["handoff_station_id"] = "H2"
    if fault == "wrong_round": identity["schema"] = "nxt-whole-course-session/v2"
    if fault == "task_missing": records = ()
    if fault == "task_duplicate": records = records + records
    if fault == "input_revision": planning["latest_input"]["revision"] = 2
    if fault == "mapping_duplicate": identity["runtime_bindings"] *= 2
    if fault == "content": planning["confirmations"][0]["schedule"]["zone_id"] = "Z2"
    with pytest.raises(api.CollectionExecutionError):
        api.bind_confirmed_tasks(planning, records, identity, 60)


def test_submit_idempotency_unknown_and_conflict_are_durable(api, tmp_path):
    store, b, req = setup(api, tmp_path)
    receipt = store.submit(req)
    assert store.submit(dict(req, request_id="alias")) == receipt
    assert store.request_result(req["request_id"]) == receipt
    with pytest.raises(api.CollectionExecutionError, match="request_not_found"):
        store.request_result("alias")
    with pytest.raises(api.CollectionExecutionError, match="conflict"):
        store.submit(dict(req, latest_start_sim_t_s=301))
    with pytest.raises(api.CollectionExecutionError):
        store.submit(dict(req, request_id="unknown", binding_id="0"*64))
    assert len(conform(store)["requests"]) == 1


def test_unknown_append_response_can_retry_original_id(api, tmp_path, monkeypatch):
    identity, planning, edge = inputs(tmp_path)
    store = api.CollectionExecutionStore(tmp_path / "execution.jsonl", identity)
    b = store.bind_confirmed_tasks(planning, edge, identity, 60)[0]
    req = api.make_request(b, "r", "2026-09-16T00:01:00Z", "2026-09-16T00:05:00Z")
    append = store.journal.append_via
    def uncertain(builder):
        append(builder)
        raise OSError("response lost after fsync")
    monkeypatch.setattr(store.journal, "append_via", uncertain)
    with pytest.raises(api.CollectionExecutionError, match="result_unknown"):
        store.submit(req)
    monkeypatch.setattr(store.journal, "append_via", append)
    assert store.submit(req) == store.request_result("r")


@pytest.mark.parametrize("now,reason", [(300, "POLICY_SLOT_MISSED"), (3000, "INSUFFICIENT_SESSION_HORIZON")])
def test_exclusive_window_and_horizon_issue_no_assignment(api, tmp_path, now, reason):
    store, _, _ = setup(api, tmp_path)
    tick(store, now, action=PAUSE)
    result = conform(store)["executions"][0]
    assert result["state"] == "MISSED" and result["reason"] == reason
    assert result["assignment_id"] is None and result["actions"] == []
    assert result["edge_evidence"]["effective_state"] == "FAILED"


def test_wait_only_and_idle_empty_start_gate(api, tmp_path):
    store, _, _ = setup(api, tmp_path)
    assert store.arbitrate(PAUSE, 60, view())["selected_action"] == PAUSE
    runtime = view(); runtime["robots"]["R1"]["payload_balls"] = 1
    assert store.arbitrate(WAIT, 60, runtime)["selected_action"] == WAIT
    runtime["robots"]["R1"]["payload_balls"] = 0
    assert store.arbitrate(WAIT, 60, runtime)["selected_action"] == COLLECT


def test_prepared_is_not_a_commit_and_cursor_cannot_lead(api, tmp_path):
    store, _, req = setup(api, tmp_path)
    d = store.arbitrate(WAIT, 60, view())
    p = store.prepare_tick(d, previous_cursor=0, previous_digest=store.replay()["replay_digest"])
    assert store.committed_outbox() == []
    assert store.replay()["pending_prepared"] == p
    with pytest.raises(api.CollectionExecutionError): store.publish_cursor(1, "f"*64)
    with pytest.raises(api.CollectionExecutionError): store.prepare_tick(d, previous_cursor=0, previous_digest="f"*64)
    c = store.commit_tick(p, now_sim_t_s=120, runtime_snapshots={req["execution_id"]: assignment(req)}, safety_shield={"allowed": True, "reason": None}, post_state_digest="f"*64)
    assert store.replay()["cursor"] is None
    assert store.commit_tick(p, now_sim_t_s=120, runtime_snapshots={req["execution_id"]: assignment(req)}, safety_shield={"allowed": True, "reason": None}, post_state_digest="f"*64) == c
    store.publish_cursor(1, c["replay_digest"])
    for entry in store.committed_outbox():
        store.confirm_outbox(entry["outbox_id"], "event-" + entry["outbox_id"])
    assert conform(store)["executions"][0]["state"] == "RUNNING"


def test_running_priority_policy_convergence_and_complete_unload(api, tmp_path):
    store, _, req = setup(api, tmp_path)
    tick(store, 60, snapshots={req["execution_id"]: assignment(req)})
    runtime = view(); runtime["continuations"][req["execution_id"]] = COLLECT
    assert store.arbitrate(WAIT, 120, runtime)["selection"] == "RUNNING_CONTINUATION"
    assert store.arbitrate(UNLOAD, 120, runtime)["selection"] == "ORIGINAL_POLICY_CONVERGED"
    tick(store, 120, runtime, UNLOAD, {req["execution_id"]: assignment(req, terminal=True)})
    result = conform(store)["executions"][0]
    assert result["state"] == "SUCCEEDED" and result["success_display_allowed"]
    assert result["raw_quantity"]["balls"] == result["unload_quantity"]["balls"] == 7
    assert all("washed" not in item and "supplied" not in item for item in result)
    reopened = api.CollectionExecutionStore(store.journal.path, store.session_identity)
    assert conform(reopened) == conform(store)


def test_committed_outbox_has_explicit_lifecycle_order_and_v3_protection(api, tmp_path):
    store, _, req = setup(api, tmp_path)
    tick(store, 60, snapshots={req["execution_id"]: assignment(req)})
    runtime = view()
    decision = store.arbitrate(UNLOAD, 120, runtime)
    prepared = store.prepare_tick(
        decision,
        previous_cursor=store.replay()["tick_sequence"],
        previous_digest=store.replay()["replay_digest"],
    )
    committed = store.commit_tick(
        prepared,
        now_sim_t_s=180,
        runtime_snapshots={req["execution_id"]: assignment(req, terminal=True)},
        safety_shield={"allowed": True, "reason": None},
        post_state_digest="e" * 64,
    )
    store.publish_cursor(committed["tick_sequence"], committed["replay_digest"])

    rows = store.committed_outbox(unconfirmed_only=True)
    assert [(row["event_kind"], row["phase"]) for row in rows] == [
        ("PROGRESS", "returning"),
        ("PROGRESS", "unloading"),
        ("SUCCEEDED", None),
    ]
    assert all(
        row["device_protection"]
        == {"protected": False, "reasons": [], "authorization_blocked": False}
        for row in rows
    )


def test_safe_partial_outbox_carries_unprotected_failed_mapping(api, tmp_path):
    store, _, req = setup(api, tmp_path)
    tick(store, 60, snapshots={req["execution_id"]: assignment(req)})
    decision = store.arbitrate(UNLOAD, 120, view())
    prepared = store.prepare_tick(
        decision,
        previous_cursor=store.replay()["tick_sequence"],
        previous_digest=store.replay()["replay_digest"],
    )
    committed = store.commit_tick(
        prepared,
        now_sim_t_s=180,
        runtime_snapshots={
            req["execution_id"]: assignment(
                req, terminal=True, exit_reason="ZONE_EMPTY", balls=7
            )
        },
        safety_shield={"allowed": True, "reason": None},
        post_state_digest="d" * 64,
    )
    store.publish_cursor(committed["tick_sequence"], committed["replay_digest"])
    terminal = store.committed_outbox(unconfirmed_only=True)[-1]
    assert terminal["event_kind"] == "FAILED"
    assert terminal["reason"] == "unknown:partial_execution"
    assert terminal["device_protection"] == {
        "protected": False,
        "reasons": [],
        "authorization_blocked": False,
    }


def test_outbox_edge_evidence_retry_reuses_identity_when_sim_clock_advanced(api, tmp_path):
    store, _, req = setup(api, tmp_path)
    decision = store.arbitrate(WAIT, 60, view())
    prepared = store.prepare_tick(
        decision,
        previous_cursor=0,
        previous_digest=store.replay()["replay_digest"],
    )
    committed = store.commit_tick(
        prepared,
        now_sim_t_s=120,
        runtime_snapshots={req["execution_id"]: assignment(req)},
        safety_shield={"allowed": True, "reason": None},
        post_state_digest="f" * 64,
    )
    store.publish_cursor(1, committed["replay_digest"])
    progress = store.committed_outbox(unconfirmed_only=True)[0]
    store.record_edge_evidence(
        req["execution_id"],
        terminal_states=[],
        event_ids=["persisted-edge-event"],
        now_sim_t_s=120,
        outbox_id=progress["outbox_id"],
    )
    before = store.journal.path.read_bytes()
    store.record_edge_evidence(
        req["execution_id"],
        terminal_states=[],
        event_ids=["persisted-edge-event"],
        now_sim_t_s=180,
        outbox_id=progress["outbox_id"],
    )
    assert store.journal.path.read_bytes() == before


@pytest.mark.parametrize("exit_reason,balls,want", [("ZONE_EMPTY",7,"PARTIAL"), ("ZONE_EMPTY",0,"FAILED"), ("ROBOT_FAULT",7,"PARTIAL"), ("POLICY_PREEMPTED",7,"PARTIAL")])
def test_non_success_and_protection_preserve_evidence(api, tmp_path, exit_reason, balls, want):
    store, _, req = setup(api, tmp_path)
    tick(store, 60, snapshots={req["execution_id"]: assignment(req, balls=balls)})
    tick(store, 120, action=PAUSE if exit_reason == "POLICY_PREEMPTED" else UNLOAD,
         snapshots={req["execution_id"]: assignment(req, terminal=True, exit_reason=exit_reason, balls=balls)})
    r = conform(store)["executions"][0]
    assert r["state"] == want and r["edge_evidence"]["effective_state"] == "FAILED"
    if want == "PARTIAL": assert r["edge_evidence"]["reason"] == "unknown:partial_execution"
    if exit_reason == "ROBOT_FAULT": assert r["device_protection"]["authorization_blocked"]


def test_shield_rejection_has_no_assignment_and_post_acceptance_failure(api, tmp_path):
    store, _, _ = setup(api, tmp_path)
    tick(store, 60, shield={"allowed": False, "reason": "zone closed"})
    r = conform(store)["executions"][0]
    assert r["state"] == "REJECTED" and r["assignment_id"] is None
    assert r["actions"][0]["safety_reason"] == "zone closed"
    assert r["edge_evidence"]["reason"] == "unknown:safety_rejected"


def test_missing_events_never_become_zero_and_terminal_conflict_never_success(api, tmp_path):
    store, _, req = setup(api, tmp_path)
    tick(store, 60, snapshots={req["execution_id"]: assignment(req)})
    broken = assignment(req, terminal=True); broken["events"].pop(1)
    tick(store, 120, action=UNLOAD, snapshots={req["execution_id"]: broken})
    r = conform(store)["executions"][0]
    assert r["state"] == "INCONCLUSIVE" and r["raw_quantity"]["balls"] is None
    store.record_edge_evidence(req["execution_id"], terminal_states=["SUCCEEDED", "FAILED"], event_ids=["a", "b"], now_sim_t_s=180)
    r = conform(store)["executions"][0]
    assert r["edge_evidence"]["effective_state"] == "CONFLICT" and not r["success_display_allowed"]


def test_sealed_evidence_snapshot_exposes_only_closed_terminal_conflict(
    api, tmp_path
):
    store, _, request = setup(api, tmp_path)
    tick(
        store,
        60,
        snapshots={request["execution_id"]: assignment(request)},
    )
    tick(
        store,
        120,
        action=UNLOAD,
        snapshots={
            request["execution_id"]: assignment(request, terminal=True)
        },
    )
    store.record_edge_evidence(
        request["execution_id"],
        terminal_states=["INCONCLUSIVE"],
        event_ids=["conflicting-terminal"],
        now_sim_t_s=180,
    )
    assert store.recovery_state()["status"] == "EDGE_WITHOUT_COMMIT"
    assert store.committed_outbox(unconfirmed_only=True) == []
    before = store.journal.path.read_bytes()

    snapshot = store.sealed_evidence_snapshot(
        server_time_utc="2035-01-02T03:04:05Z"
    )
    oracle.validator(SCHEMA, "#/$defs/ExecutionSnapshot").validate(snapshot)
    oracle.relations(snapshot)
    row = snapshot["executions"][0]
    assert (row["state"], row["reason"]) == (
        "INCONCLUSIVE",
        "TERMINAL_CONFLICT",
    )
    assert row["conflicts"]["terminal_conflict"] is True
    assert row["device_protection"]["authorization_blocked"] is True
    assert store.sealed_request_result(request["request_id"]) == (
        snapshot["receipts"][0]
    )
    snapshot["executions"][0]["reason"] = "mutated"
    assert store.sealed_evidence_snapshot(
        server_time_utc="2035-01-02T03:04:05Z"
    )["executions"][0]["reason"] == "TERMINAL_CONFLICT"
    assert store.journal.path.read_bytes() == before


def test_sealed_evidence_snapshot_exposes_replay_mismatch_but_not_normal_root(
    api, tmp_path
):
    store, _, request = setup(api, tmp_path)
    tick(
        store,
        60,
        snapshots={request["execution_id"]: assignment(request)},
    )
    with pytest.raises(api.CollectionExecutionError, match="sealed evidence"):
        store.sealed_evidence_snapshot(
            server_time_utc="2035-01-02T03:04:05Z"
        )
    recovery = store.recovery_state()
    decision = store.arbitrate(WAIT, 120, view())
    pending = store.prepare_tick(
        decision,
        previous_cursor=recovery["tick_sequence"],
        previous_digest=recovery["replay_digest"],
    )
    store.record_replay_failure(
        digest(pending), now_sim_t_s=180, observed_digest="e" * 64
    )
    assert store.recovery_state()["status"] == "REPLAY_MISMATCH"
    snapshot = store.sealed_evidence_snapshot(
        server_time_utc="2035-01-02T03:04:05Z"
    )
    row = snapshot["executions"][0]
    assert (row["state"], row["reason"]) == (
        "INCONCLUSIVE",
        "REPLAY_MISMATCH",
    )
    assert row["conflicts"]["replay_mismatch"] is True
    assert row["device_protection"]["authorization_blocked"] is True


def test_sealed_request_receipt_remains_readable_when_list_is_not_closed(
    api, tmp_path
):
    store, _, request = setup_without_acceptance(api, tmp_path)
    recovery = store.recovery_state()
    pending = store.prepare_tick(
        store.arbitrate(WAIT, 60, view()),
        previous_cursor=recovery["tick_sequence"],
        previous_digest=recovery["replay_digest"],
    )
    store.record_replay_failure(
        digest(pending), now_sim_t_s=60, observed_digest="e" * 64
    )

    with pytest.raises(api.CollectionExecutionError, match="acceptance"):
        store.sealed_evidence_snapshot(
            server_time_utc="2035-01-02T03:04:05Z"
        )
    assert store.sealed_request_result(request["request_id"])[
        "request_id"
    ] == request["request_id"]


@pytest.mark.parametrize("running", [False, True])
def test_explicit_device_restart_differs_from_prefix_replay(api, tmp_path, running):
    store, _, req = setup(api, tmp_path)
    if running: tick(store, 60, snapshots={req["execution_id"]: assignment(req)})
    restart(store,req,tmp_path,running=running)
    r = conform(store)["executions"][0]
    assert r["state"] == ("INCONCLUSIVE" if running else "FAILED")
    assert r["reason"] == ("INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME" if running else "NOT_STARTED_AFTER_RESTART")


@pytest.mark.parametrize("targets_execution", [False, True])
def test_device_restart_cancels_only_its_exact_pending_authorization(
    api, tmp_path, targets_execution
):
    store, _, req = setup(api, tmp_path)
    decision = store.arbitrate(
        WAIT if targets_execution else PAUSE,
        60,
        view(),
    )
    assert (decision["execution_id"] == req["execution_id"]) is targets_execution
    recovery = store.recovery_state()
    pending = store.prepare_tick(
        decision,
        previous_cursor=recovery["tick_sequence"],
        previous_digest=recovery["replay_digest"],
    )

    restart(store, req, tmp_path, running=False, now=120)

    after = store.recovery_state()
    assert after["replay_digest"] == recovery["replay_digest"]
    assert after["tick_sequence"] == recovery["tick_sequence"] == 0
    row = store.replay()["executions"][req["execution_id"]]
    assert (row["state"], row["reason"]) == (
        "FAILED",
        "NOT_STARTED_AFTER_RESTART",
    )
    if targets_execution:
        assert after["status"] == "NO_PREPARED"
        assert after["pending_prepared"] is None
        # The cancelled sequence was never committed and may be reused by the
        # next ordinary policy tick without changing the committed digest.
        next_decision = store.arbitrate(PAUSE, 120, view())
        replacement = store.prepare_tick(
            next_decision,
            previous_cursor=after["tick_sequence"],
            previous_digest=after["replay_digest"],
        )
        assert replacement["tick_sequence"] == pending["tick_sequence"] == 1
    else:
        assert after["status"] == "PREPARED_NO_COMMIT"
        assert after["pending_prepared"] == pending


@pytest.mark.parametrize(
    "v3_started,device_started",
    [(True, False), (False, True)],
)
def test_restart_start_evidence_mismatch_is_preserved_conflict_and_blocks_old_outbox(
    api, tmp_path, v3_started, device_started
):
    store, _, req = setup(api, tmp_path)
    if v3_started:
        decision = store.arbitrate(WAIT, 60, view())
        prepared = store.prepare_tick(
            decision,
            previous_cursor=0,
            previous_digest=store.replay()["replay_digest"],
        )
        committed = store.commit_tick(
            prepared,
            now_sim_t_s=120,
            runtime_snapshots={req["execution_id"]: assignment(req)},
            safety_shield={"allowed": True, "reason": None},
            post_state_digest="c" * 64,
        )
        store.publish_cursor(committed["tick_sequence"], committed["replay_digest"])

    record = restart(store, req, tmp_path, running=device_started, now=120)
    before = store.journal.path.read_bytes()
    store.record_device_restart_outcome(
        req["execution_id"], record, now_sim_t_s=120
    )
    assert store.journal.path.read_bytes() == before

    state = store.replay()
    row = state["executions"][req["execution_id"]]
    assert row["state"] == "INCONCLUSIVE"
    assert row["reason"] == "REPLAY_MISMATCH"
    assert row["conflicts"]["replay_mismatch"]
    assert row["edge_evidence"]["effective_state"] == "CONFLICT"
    assert row["edge_evidence"]["result_verification"] == "CONFLICT"
    assert "ORPHANED_ACTIVITY" in row["device_protection"]["reasons"]
    assert store.committed_outbox(unconfirmed_only=True) == []
    blocked = store.committed_outbox(unconfirmed_only=False)
    if blocked:
        outbox_id = blocked[0]["outbox_id"]
        with pytest.raises(api.CollectionExecutionError, match="blocked"):
            store.confirm_outbox(outbox_id, "late-old-authorization")
        with pytest.raises(api.CollectionExecutionError, match="blocked"):
            store.record_edge_evidence(
                req["execution_id"],
                terminal_states=[],
                event_ids=["late-old-authorization"],
                now_sim_t_s=120,
                outbox_id=outbox_id,
            )
    assert store.recovery_state()["status"] == "NO_PREPARED"
    assert conform(store)["executions"][0]["state"] == "INCONCLUSIVE"


@pytest.mark.parametrize("record_kind", ["outbox_confirmed", "edge_evidence"])
def test_replay_rejects_manual_write_for_restart_blocked_outbox(
    api, tmp_path, record_kind
):
    store, _, req = setup(api, tmp_path)
    decision = store.arbitrate(WAIT, 60, view())
    prepared = store.prepare_tick(
        decision,
        previous_cursor=0,
        previous_digest=store.replay()["replay_digest"],
    )
    committed = store.commit_tick(
        prepared,
        now_sim_t_s=120,
        runtime_snapshots={req["execution_id"]: assignment(req)},
        safety_shield={"allowed": True, "reason": None},
        post_state_digest="c" * 64,
    )
    store.publish_cursor(1, committed["replay_digest"])
    restart(store, req, tmp_path, running=False, now=120)
    outbox = store.committed_outbox(unconfirmed_only=False)[0]
    payload = (
        {"outbox_id": outbox["outbox_id"], "event_id": "late-old-authorization"}
        if record_kind == "outbox_confirmed"
        else {
            "execution_id": req["execution_id"],
            "terminal_states": [],
            "event_ids": ["late-old-authorization"],
            "now_sim_t_s": 120,
            "outbox_id": outbox["outbox_id"],
        }
    )
    store.journal.append(store._spec(record_kind, payload, 120))
    with pytest.raises(api.CollectionExecutionError, match="blocked"):
        store.replay()


@pytest.mark.parametrize("fault", ["truncate", "rollback", "unknown_kind"])
def test_journal_corruption_fails_loud(api, tmp_path, fault):
    store, _, _ = setup(api, tmp_path)
    path = store.journal.path; data = path.read_bytes()
    if fault == "truncate": path.write_bytes(data[:-3])
    if fault == "rollback": path.write_bytes(data.splitlines(keepends=True)[0])
    if fault == "unknown_kind":
        with pytest.raises(ValueError): store.journal.append(RecordSpec("not_v3", "SIM_ENTRY", "2026-09-16T00:01:00Z", {}))
        return
    with pytest.raises((api.CollectionExecutionError, JournalIntegrityError)): store.snapshot(server_time_utc="2026-09-24T00:00:00Z")


def test_request_cannot_rebase_frozen_schedule(api, tmp_path):
    store, b, req = setup(api, tmp_path)
    altered = api.make_request(b, "new", "2026-09-16T00:02:00Z", "2026-09-16T00:06:00Z")
    with pytest.raises(api.CollectionExecutionError): store.submit(altered)


def test_pre_acceptance_horizon_miss_is_edge_rejected(api,tmp_path):
    identity,planning,edge=inputs(tmp_path)
    identity["session_end_sim_t_s"]=700
    store=api.CollectionExecutionStore(tmp_path/"execution.jsonl",identity)
    b=store.bind_confirmed_tasks(planning,edge,identity,60)[0]
    req=api.make_request(b,"r","2026-09-16T00:01:00Z","2026-09-16T00:05:00Z")
    store.submit(req)
    tick(store,60)
    r=conform(store)["executions"][0]
    assert r["state"] == "MISSED" and r["edge_evidence"]["effective_state"] == "REJECTED"
    assert not r["edge_evidence"]["accepted"]


def test_rejected_handoff_does_not_publish_returning_progress(api,tmp_path):
    store,_,req=setup(api,tmp_path)
    tick(store,60,snapshots={req["execution_id"]:assignment(req)})
    c=tick(store,120,action=UNLOAD,snapshots={req["execution_id"]:assignment(req)},shield={"allowed":False,"reason":"handoff unsafe"})
    assert not any(e["phase"] == "returning" for e in c["outbox"])
    conform(store)


def test_snapshot_requires_confirmed_outbox_and_cannot_mutate_clock(api, tmp_path):
    store, _, req = setup(api, tmp_path)
    p = store.prepare_tick(store.arbitrate(WAIT, 60, view()), previous_cursor=0, previous_digest=store.replay()["replay_digest"])
    c = store.commit_tick(p, now_sim_t_s=120, runtime_snapshots={req["execution_id"]: assignment(req)}, safety_shield={"allowed":True,"reason":None}, post_state_digest="f"*64)
    assert store.recovery_state()["status"] == "COMMITTED_CURSOR_STALE"
    with pytest.raises(api.CollectionExecutionError, match="unavailable"):
        conform(store)
    store.publish_cursor(1, c["replay_digest"])
    assert store.recovery_state()["status"] == "COMMITTED_OUTBOX_UNCONFIRMED"
    for entry in store.committed_outbox():
        store.confirm_outbox(entry["outbox_id"], "edge-" + entry["outbox_id"])
        store.confirm_outbox(entry["outbox_id"], "edge-" + entry["outbox_id"])
    assert store.recovery_state()["status"] == "NO_PREPARED"
    before = store.journal.path.read_bytes()
    a = conform(store, session_state="PAUSED")
    b = store.snapshot(server_time_utc="2030-01-01T00:00:00Z", session_state="PAUSED")
    assert a["replay_digest"] == b["replay_digest"] and a["simulation_time_utc"] == b["simulation_time_utc"]
    assert before == store.journal.path.read_bytes()
    assert store.replay()["commits"][1] == c, "Edge delivery must not mutate the committed replay artifact"


def test_restart_cancels_prepared_recovery_and_never_starts_old_task(api, tmp_path):
    store, _, req = setup(api, tmp_path)
    p = store.prepare_tick(store.arbitrate(WAIT, 60, view()), previous_cursor=0, previous_digest=store.replay()["replay_digest"])
    restart(store,req,tmp_path,running=False,now=60)
    assert not store.recovery_state()["replay_permitted"]
    with pytest.raises(api.CollectionExecutionError):
        store.commit_tick(p, now_sim_t_s=120, runtime_snapshots={req["execution_id"]:assignment(req)}, safety_shield={"allowed":True,"reason":None},post_state_digest="f"*64)


def test_missing_runtime_at_deadline_is_unknown_not_running_or_zero(api, tmp_path):
    store, _, req = setup(api, tmp_path)
    tick(store, 60, snapshots={req["execution_id"]: assignment(req)})
    tick(store, 660)
    r = conform(store)["executions"][0]
    assert r["state"] == "INCONCLUSIVE" and r["raw_quantity"]["balls"] is None


@pytest.mark.parametrize("missing",[{},None])
def test_running_tick_without_current_runtime_cannot_reuse_old_quantity(api,tmp_path,missing):
    store,_,req=setup(api,tmp_path)
    tick(store,60,snapshots={req["execution_id"]:assignment(req)})
    snapshots={} if missing == {} else {req["execution_id"]:None}
    tick(store,120,snapshots=snapshots)
    r=conform(store)["executions"][0]
    assert r["state"] == "INCONCLUSIVE" and r["raw_quantity"]["balls"] is None


def test_changed_event_prefix_is_unknown_even_with_rehashed_digest(api, tmp_path):
    store, _, req = setup(api, tmp_path)
    tick(store, 60, snapshots={req["execution_id"]:assignment(req)})
    changed = assignment(req, terminal=True, balls=8)
    tick(store, 120, action=UNLOAD, snapshots={req["execution_id"]:changed})
    r = conform(store)["executions"][0]
    assert r["state"] == "INCONCLUSIVE" and r["raw_quantity"]["balls"] is None


def test_pending_order_and_running_lease_never_depend_on_request_arrival(api, tmp_path):
    store, b, req = setup(api, tmp_path)
    identity, planning, edge = inputs(tmp_path / "other")
    task = TaskRequest.from_dict(edge[0].to_dict()["payload"]["request"])
    body = task.to_dict(); body["issued_by"] = "SIMULATION_TEST_ENTRY:second"
    fields = dict(site_id=task.site_id,deployment_id=task.deployment_id,simulation_env_id=task.simulation_env_id,target_robot_id=task.target_robot_id,target_incarnation=task.target_incarnation,task_type=task.task_type,zone_id=task.zone_id,issued_at_utc=task.issued_at_utc,expires_at_utc="2026-09-16T00:04:00Z",progress_window_s=60,issued_by="SIMULATION_TEST_ENTRY:second")
    other = TaskRequest.build(**fields)
    c = planning["confirmations"][0]; c.update(confirmation_id="confirmation-002",task_id=other.task_id,schedule_id="schedule-002")
    c["schedule"].update(operator="second",admission_reference="confirmation-002",expires_at_utc=other.expires_at_utc)
    j = JsonlJournal(tmp_path / "second-edge.jsonl")
    j.append(RecordSpec("task_created","EDGE",other.issued_at_utc,{"task_id":other.task_id,"schedule_id":"schedule-002","request":other.to_dict()}))
    second = store.bind_confirmed_tasks(planning,j.read(),identity,60)[0]
    request = api.make_request(second,"second",other.issued_at_utc,other.expires_at_utc)
    store.submit(request); store.record_acceptance(request["execution_id"],"edge-second")
    d = store.arbitrate(WAIT,60,view())
    assert d["execution_id"] == request["execution_id"]
    assert [c["latest_start_sim_t_s"] for c in d["eligible_pending"]] == [240,300]
    tick(store,60,snapshots={request["execution_id"]:assignment(request)})
    runtime=view(); runtime["continuations"][request["execution_id"]]=COLLECT
    assert store.arbitrate(WAIT,120,runtime)["selection"] == "RUNNING_CONTINUATION"
    conform(store)


def test_invalid_edge_evidence_does_not_poison_verified_journal(api, tmp_path):
    store,_,req=setup(api,tmp_path)
    before=store.journal.path.read_bytes()
    with pytest.raises(api.CollectionExecutionError):
        store.record_edge_evidence("unknown",terminal_states=["SUCCEEDED"],event_ids=["e"],now_sim_t_s=60)
    assert store.journal.path.read_bytes() == before
    conform(store)


def test_terminal_evidence_cannot_borrow_a_progress_outbox(api,tmp_path):
    store,_,req=setup(api,tmp_path)
    tick(store,60,snapshots={req["execution_id"]:assignment(req)})
    progress=store.committed_outbox()[0]
    store.record_edge_evidence(req["execution_id"],terminal_states=["SUCCEEDED"],event_ids=["unmatched-terminal"],now_sim_t_s=120,outbox_id=progress["outbox_id"])
    r=conform(store)["executions"][0]
    assert r["state"] == "INCONCLUSIVE" and r["conflicts"]["replay_mismatch"]
    assert r["edge_evidence"]["effective_state"] == "CONFLICT"


@pytest.mark.parametrize("reason",["ROBOT_FAULT","ESTOP_LATCHED","HUMAN_ASSISTANCE_REQUIRED","POLICY_PREEMPTED"])
def test_unknown_quantity_preserves_causal_exit_and_protection(api,tmp_path,reason):
    store,_,req=setup(api,tmp_path)
    tick(store,60,snapshots={req["execution_id"]:assignment(req)})
    broken=assignment(req,terminal=True,exit_reason=reason)
    broken["events"].pop(1)
    tick(store,120,action=PAUSE if reason=="POLICY_PREEMPTED" else UNLOAD,snapshots={req["execution_id"]:broken})
    r=conform(store)["executions"][0]
    assert r["state"] == "INCONCLUSIVE" and r["reason"] == reason
    assert r["raw_quantity"]["balls"] is None


def live_store(api, tmp_path, *, capacity=20):
    from nxt_range_ops.core import ledger
    from nxt_range_ops.core.skills import SkillOutcome, SkillOutcomeModel, SkillType
    from nxt_range_ops.env.range_ops_env import RangeOpsEnv
    from nxt_range_ops.scenarios.generators import make_scenario
    class Skills(SkillOutcomeModel):
        def sample(self, request, rng):
            return SkillOutcome(True,10 if request.skill is SkillType.COLLECT_CYCLE else 1,0)
    identity,planning,edge=inputs(tmp_path)
    identity["runtime_bindings"][0]["runtime_zone_id"]="Z1"
    base=make_scenario("normal_weekday")
    scenario=base.model_copy(update={"robots":[base.robots[0].model_copy(update={"payload_capacity_balls":capacity})],
        "hours":base.hours.model_copy(update={"open_minute":0,"close_minute":60}),
        "episode":base.episode.model_copy(update={"control_interval_s":60}),
        "skills":base.skills.model_copy(update={"collect_cycle_balls":7})})
    env=RangeOpsEnv(scenario,lambda _:Skills(),collection_assignment_evidence=True)
    env.reset(seed=53)
    env.step(0)
    env.sim.ledger.move(ledger.DISPENSER,ledger.zone_loc("Z1"),100)
    store=api.CollectionExecutionStore(tmp_path/"live.jsonl",identity)
    b=store.bind_confirmed_tasks(planning,edge,identity,60)[0]
    req=api.make_request(b,"actual", "2026-09-16T00:01:00Z","2026-09-16T00:05:00Z")
    store.submit(req);store.record_acceptance(req["execution_id"],"accepted")
    collect=dict(COLLECT,target_id="Z1",index=env.catalog.index_of("assign_collection(R1,Z1)"))
    unload=dict(UNLOAD,index=env.catalog.index_of("send_to_handoff(R1)"))
    runtime=view();runtime["catalog_actions"]=[WAIT,collect,unload]
    return env,store,req,runtime,unload


def live_tick(env,store,req,runtime,original):
    now=env.sim.now
    d=store.arbitrate(original,now,runtime)
    p=store.prepare_tick(d,previous_cursor=store.replay()["tick_sequence"],previous_digest=store.replay()["replay_digest"])
    if d["selection"] == "WAIT_SLOT":
        env.arm_collection_assignment(req["execution_id"],"R1","Z1","H1",now+660)
    _,_,_,_,info=env.step(d["selected_action"]["index"])
    c=store.commit_tick(p,now_sim_t_s=env.sim.now,runtime_snapshots={req["execution_id"]:env.collection_assignment_snapshot(req["execution_id"])},safety_shield=info["shield"],post_state_digest="a"*64)
    store.publish_cursor(c["tick_sequence"],c["replay_digest"])
    for entry in store.committed_outbox(unconfirmed_only=True):store.confirm_outbox(entry["outbox_id"],"e-"+entry["outbox_id"])
    return c


def test_actual_task1_snapshot_unload_passes_frozen_oracle(api, tmp_path):
    env,store,req,runtime,unload=live_store(api,tmp_path)
    for original in (WAIT,unload): live_tick(env,store,req,runtime,original)
    r=conform(store)["executions"][0]
    assert r["state"] == "SUCCEEDED" and r["raw_quantity"]["balls"] == 20 and r["unload_quantity"]["balls"] == 20


def test_restart_unknown_overlays_unconfirmed_success_and_survives_late_delivery(api,tmp_path):
    store,_,req=setup(api,tmp_path)
    tick(store,60,snapshots={req["execution_id"]:assignment(req)})
    p=store.prepare_tick(store.arbitrate(UNLOAD,120,view()),previous_cursor=1,previous_digest=store.replay()["replay_digest"])
    c=store.commit_tick(p,now_sim_t_s=180,runtime_snapshots={req["execution_id"]:assignment(req,terminal=True)},safety_shield={"allowed":True,"reason":None},post_state_digest="a"*64)
    assert c["executions"][req["execution_id"]]["state"] == "SUCCEEDED"
    restart(store,req,tmp_path,running=True,now=180)
    assert any(r.record_kind == "device_restart" for r in store.journal.read())
    evidence=JsonlJournal(tmp_path/"device.jsonl").read()[-1]
    before=store.journal.path.read_bytes()
    store.record_device_restart_outcome(req["execution_id"],evidence,now_sim_t_s=180)
    assert store.journal.path.read_bytes() == before
    for entry in store.committed_outbox(unconfirmed_only=True):store.confirm_outbox(entry["outbox_id"],"late-"+entry["outbox_id"])
    r=conform(store)["executions"][0]
    assert r["state"] == "INCONCLUSIVE" and not r["success_display_allowed"]
    assert r["conflicts"]["terminal_conflict"] and r["device_protection"]["authorization_blocked"]
    assert set(r["edge_evidence"]["terminal_states"]) == {"SUCCEEDED","INCONCLUSIVE"}
    assert r["edge_evidence"]["effective_state"] == r["edge_evidence"]["result_verification"] == "CONFLICT"
    assert evidence.record_id in r["edge_evidence"]["event_ids"]
    assert store.replay()["commits"][2] == c
    reopened=api.CollectionExecutionStore(store.journal.path,store.session_identity)
    assert conform(reopened) == conform(store)


@pytest.mark.parametrize("cause",["POLICY_PREEMPTED","EXECUTION_TIMEOUT"])
def test_actual_full_payload_keeps_later_terminal_cause(api,tmp_path,cause):
    env,store,req,runtime,_=live_store(api,tmp_path)
    live_tick(env,store,req,runtime,WAIT)
    assert env.collection_assignment_snapshot(req["execution_id"])["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
    if cause == "POLICY_PREEMPTED":
        action=dict(PAUSE,index=env.catalog.index_of("pause_robot(R1)"))
        runtime["catalog_actions"].append(action)
        last=live_tick(env,store,req,runtime,action)
    else:
        while env.sim.now < 720:last=live_tick(env,store,req,runtime,WAIT)
    native=env.collection_assignment_snapshot(req["execution_id"])
    assert native["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL" and native["terminal_reason"] == cause
    assert last["result"]["runtime_snapshots"][req["execution_id"]] == native
    r=conform(store)["executions"][0]
    assert r["state"] == "PARTIAL" and r["reason"] == r["runtime_evidence"]["collection_exit_reason"] == cause


@pytest.mark.parametrize("horizon,now,reason",[(3600,240,"POLICY_SLOT_MISSED"),(950,240,"INSUFFICIENT_SESSION_HORIZON")])
def test_nonwait_step_crosses_pending_gate_at_commit(api,tmp_path,horizon,now,reason):
    identity,planning,edge=inputs(tmp_path)
    identity["session_end_sim_t_s"]=horizon
    store=api.CollectionExecutionStore(tmp_path/"execution.jsonl",identity)
    b=store.bind_confirmed_tasks(planning,edge,identity,60)[0]
    req=api.make_request(b,"r","2026-09-16T00:01:00Z","2026-09-16T00:05:00Z")
    store.submit(req);store.record_acceptance(req["execution_id"],"accepted")
    tick(store,now,action=PAUSE)
    r=conform(store)["executions"][0]
    assert r["state"] == "MISSED" and r["reason"] == reason and r["terminal_sim_t_s"] == 300
    assert r["assignment_id"] is None and not r["actions"]


def test_late_acceptance_cannot_regress_preacceptance_terminal(api,tmp_path):
    identity,planning,edge=inputs(tmp_path)
    identity["session_end_sim_t_s"]=700
    store=api.CollectionExecutionStore(tmp_path/"execution.jsonl",identity)
    b=store.bind_confirmed_tasks(planning,edge,identity,60)[0]
    req=api.make_request(b,"r","2026-09-16T00:01:00Z","2026-09-16T00:05:00Z")
    store.submit(req);tick(store,60)
    before=store.journal.path.read_bytes()
    with pytest.raises(api.CollectionExecutionError):store.record_acceptance(req["execution_id"],"late-accepted")
    assert store.journal.path.read_bytes() == before
    assert conform(store)["executions"][0]["edge_evidence"]["effective_state"] == "REJECTED"


@pytest.mark.parametrize("recovery",["retry","query"])
def test_complete_request_only_tail_recovers_without_second_attempt(api,tmp_path,monkeypatch,recovery):
    identity,planning,edge=inputs(tmp_path)
    store=api.CollectionExecutionStore(tmp_path/"execution.jsonl",identity)
    b=store.bind_confirmed_tasks(planning,edge,identity,60)[0]
    req=api.make_request(b,"r","2026-09-16T00:01:00Z","2026-09-16T00:05:00Z")
    append=store.journal.append_via
    expected={}
    def interrupted(builder):
        def request_only(records):
            specs=builder(records)
            expected.update(specs[1].payload)
            return specs[:1]
        append(request_only)
        raise OSError("crash after complete request line")
    monkeypatch.setattr(store.journal,"append_via",interrupted)
    with pytest.raises(api.CollectionExecutionError,match="result_unknown"):store.submit(req)
    monkeypatch.setattr(store.journal,"append_via",append)
    assert store.journal.read()[-1].record_kind == "request"
    before=store.journal.path.read_bytes()
    recovered=store.submit(req) if recovery == "retry" else store.request_result("r")
    assert recovered == expected
    assert store.journal.path.read_bytes() == before
    store.record_acceptance(req["execution_id"],"accepted")
    tick(store,60,snapshots={req["execution_id"]:assignment(req)})
    snap=conform(store)
    assert len(snap["requests"]) == len(snap["receipts"]) == len(snap["executions"]) == 1
    assert sum(r.record_kind == "request" for r in store.journal.read()) == 1


def test_real_shield_rejected_reassignment_does_not_preempt(api,tmp_path):
    env,store,req,runtime,_=live_store(api,tmp_path)
    live_tick(env,store,req,runtime,WAIT)
    env.sim._zones["Z2"].is_open=False
    proposal={"name":"ReassignRobot","index":env.catalog.index_of("reassign_robot(R1,Z2)"),"robot_id":"R1","target_id":"Z2"}
    runtime["catalog_actions"].append(proposal)
    native=deepcopy(env.collection_assignment_snapshot(req["execution_id"]))
    c=live_tick(env,store,req,runtime,proposal)
    assert not c["result"]["safety_shield"]["allowed"]
    assert env.collection_assignment_snapshot(req["execution_id"]) == native
    r=conform(store)["executions"][0]
    assert r["state"] == "RUNNING" and r["terminal_sim_t_s"] is None
    action=r["actions"][-1]
    assert action["selection"] == "ORIGINAL_POLICY_UNCHANGED" and action["safety_shield"] == "REJECTED"
    assert action["original_action"] == action["selected_action"] == proposal and action["safety_reason"]


@pytest.mark.parametrize("source", ["restart", "edge_conflict"])
def test_later_conflict_preserves_real_timeout_timestamp(api,tmp_path,source):
    env,store,req,runtime,_=live_store(api,tmp_path)
    live_tick(env,store,req,runtime,WAIT)
    while env.sim.now < 720:live_tick(env,store,req,runtime,WAIT)
    native=deepcopy(env.collection_assignment_snapshot(req["execution_id"]))
    assert native["terminal_sim_t_s"] == 720
    if source == "restart":
        restart(store,req,tmp_path,running=True,now=780)
    else:
        terminal=next(e for e in store.committed_outbox() if e["event_kind"] == "FAILED")
        store.record_edge_evidence(req["execution_id"],terminal_states=["INCONCLUSIVE"],event_ids=["late-terminal"],now_sim_t_s=780,outbox_id=terminal["outbox_id"])
    r=store.replay()["executions"][req["execution_id"]]
    assert r["terminal_sim_t_s"] == 720
    assert conform(store)["now_sim_t_s"] == 780
    assert r["state"] == "INCONCLUSIVE" and r["reason"] == "TERMINAL_CONFLICT"
    assert r["runtime_evidence"]["collection_exit_reason"] == "EXECUTION_TIMEOUT"
    assert r["device_protection"]["authorization_blocked"]
    assert env.collection_assignment_snapshot(req["execution_id"]) == native


def test_actual_accepted_human_assistance_is_not_policy_preemption(api,tmp_path):
    env,store,req,runtime,_=live_store(api,tmp_path)
    live_tick(env,store,req,runtime,WAIT)
    spec=next(s for s in env.catalog.specs if s.name.startswith("request_human_assistance(R1,"))
    proposal=dict(name="RequestHumanAssistance",index=spec.index,robot_id="R1",target_id=None)
    runtime["catalog_actions"].append(proposal)
    last=live_tick(env,store,req,runtime,proposal)
    native=env.collection_assignment_snapshot(req["execution_id"])
    assert native["terminal_reason"] == "HUMAN_ASSISTANCE_REQUIRED"
    assert last["result"]["runtime_snapshots"][req["execution_id"]] == native
    r=store.replay()["executions"][req["execution_id"]]
    action=r["actions"][-1]
    assert action["selection"] == "ORIGINAL_POLICY_UNCHANGED"
    assert action["original_action"] == action["selected_action"] == proposal
    assert action["safety_shield"] == "ACCEPTED"
    assert r["reason"] == r["runtime_evidence"]["collection_exit_reason"] == "HUMAN_ASSISTANCE_REQUIRED"
    assert "HUMAN_ASSISTANCE_REQUIRED" in r["device_protection"]["reasons"]
    assert conform(store)["executions"][0]["state"] == "PARTIAL"


@pytest.mark.parametrize("capacity,balls,selection,state", [
    (40,35,"POLICY_PREEMPTED","PARTIAL"), (20,20,"ORIGINAL_POLICY_CONVERGED","SUCCEEDED")])
def test_real_threshold_handoff_classifies_actual_tick_effect(api,tmp_path,capacity,balls,selection,state):
    from nxt_range_ops.policies.joint_dispatch import JointDispatchPolicy, candidate_catalog
    env,store,req,runtime,unload=live_store(api,tmp_path,capacity=capacity)
    live_tick(env,store,req,runtime,WAIT)
    before=env.collection_assignment_snapshot(req["execution_id"])
    assert before["raw_collected_balls"] == balls
    assert before["collection_exit_reason"] == (None if capacity == 40 else "ROBOT_PAYLOAD_FULL")
    policy=JointDispatchPolicy(env.scenario,env.catalog,candidate_catalog()[0])
    assert policy.act(*env.refresh_observation_info()) == unload["index"]
    last=live_tick(env,store,req,runtime,unload)
    native=env.collection_assignment_snapshot(req["execution_id"])
    assert native["terminal_reason"] == ("POLICY_PREEMPTED" if capacity == 40 else "UNLOADED_ALL_COLLECTED_BALLS")
    assert last["result"]["runtime_snapshots"][req["execution_id"]] == native
    prepared=next(r.payload for r in reversed(store.journal.read()) if r.record_kind == "action_prepared")
    assert prepared["decision"]["selection"] == "ORIGINAL_POLICY_CONVERGED"
    assert prepared["decision"]["original_action"] == prepared["decision"]["selected_action"] == unload
    r=store.replay()["executions"][req["execution_id"]]
    assert r["actions"][-1]["selection"] == selection
    assert r["actions"][-1]["safety_shield"] == "ACCEPTED"
    assert conform(store)["executions"][0]["state"] == state


@pytest.mark.parametrize("capacity", [40,20])
def test_running_continuation_requires_committed_native_phase(api,tmp_path,capacity):
    env,store,req,runtime,unload=live_store(api,tmp_path,capacity=capacity)
    live_tick(env,store,req,runtime,WAIT)
    native=deepcopy(env.collection_assignment_snapshot(req["execution_id"]))
    collect=next(a for a in runtime["catalog_actions"] if a["name"] == "AssignCollection")
    before_boundary=native["collection_exit_reason"] is None
    assert before_boundary == (capacity == 40)
    good,bad=(collect,unload) if before_boundary else (unload,collect)
    runtime["continuations"][req["execution_id"]]=bad
    before=store.journal.path.read_bytes()
    now=env.sim.now
    with pytest.raises(api.CollectionExecutionError,match="continuation"):
        store.arbitrate(WAIT,now,runtime)
    assert store.journal.path.read_bytes() == before and env.sim.now == now
    assert env.collection_assignment_snapshot(req["execution_id"]) == native
    assert store.recovery_state()["pending_prepared"] is None
    runtime["continuations"][req["execution_id"]]=good
    decision=store.arbitrate(WAIT,now,runtime)
    assert decision["selection"] == "RUNNING_CONTINUATION" and decision["selected_action"] == good
    last=live_tick(env,store,req,runtime,WAIT)
    native=env.collection_assignment_snapshot(req["execution_id"])
    assert native["terminal_reason"] != "POLICY_PREEMPTED"
    assert last["result"]["runtime_snapshots"][req["execution_id"]] == native
    assert conform(store)["executions"][0]["state"] == ("RUNNING" if before_boundary else "SUCCEEDED")


def test_initialize_empty_session_supports_first_policy_tick_and_reopen(api,tmp_path):
    identity,_,_=inputs(tmp_path)
    store=api.CollectionExecutionStore(tmp_path/"empty.jsonl",identity)
    store.initialize_session(now_sim_t_s=60)
    before=store.journal.path.read_bytes()
    store.initialize_session()
    assert store.journal.path.read_bytes() == before
    assert conform(store)["now_sim_t_s"] == 60
    c=tick(store,60,action=PAUSE)
    assert not c["executions"] and not c["outbox"]
    reopened=api.CollectionExecutionStore(store.journal.path,identity)
    assert conform(reopened) == conform(store)
    assert reopened.recovery_state()["tick_sequence"] == 1


@pytest.mark.parametrize("initialize_first", [False,True])
def test_initialize_session_preserves_both_binding_orders(api,tmp_path,initialize_first):
    identity,planning,edge=inputs(tmp_path)
    store=api.CollectionExecutionStore(tmp_path/"ordered.jsonl",identity)
    if initialize_first:store.initialize_session()
    bindings=store.bind_confirmed_tasks(planning,edge,identity,60)
    store.initialize_session()
    assert store.bind_confirmed_tasks(planning,edge,identity,60) == bindings
    assert sum(r.record_kind == "session" for r in store.journal.read()) == 1
    assert conform(store)["bindings"] == bindings


@pytest.mark.parametrize("kind,payload", [("receipt",{}),("session",{"identity":{},"policy_id":"wrong"})])
def test_initialize_rejects_malformed_existing_prefix(api,tmp_path,kind,payload):
    identity,_,_=inputs(tmp_path)
    store=api.CollectionExecutionStore(tmp_path/"bad.jsonl",identity)
    store.journal.append(RecordSpec(kind,"SIM_ENTRY",identity["session_epoch_utc"],payload))
    before=store.journal.path.read_bytes()
    with pytest.raises(api.CollectionExecutionError):store.initialize_session()
    assert store.journal.path.read_bytes() == before


def test_replay_plan_is_ordered_detached_and_excludes_pending_policy_tick(api,tmp_path):
    identity,_,_=inputs(tmp_path)
    store=api.CollectionExecutionStore(tmp_path/"plan.jsonl",identity)
    store.initialize_session()
    commits=[tick(store,now,action=PAUSE) for now in (0,60,120)]
    recovery=store.recovery_state()
    pending=store.prepare_tick(store.arbitrate(WAIT,180,view()),previous_cursor=3,previous_digest=recovery["replay_digest"])
    before=store.journal.path.read_bytes()
    plan=store.replay_plan()
    assert len(plan) == 3 and [p["committed"] for p in plan] == commits
    for n,pair in enumerate(plan,1):
        assert set(pair) == {"prepared","committed"}
        assert pair["prepared"]["tick_sequence"] == pair["committed"]["tick_sequence"] == n
        assert pair["prepared"]["decision"]["execution_id"] is None
        assert digest(pair["prepared"]) == pair["committed"]["prepared_digest"]
    plan[0]["prepared"]["decision"]["original_action"]["name"]="mutated"
    plan[0]["committed"]["result"]["runtime_snapshots"]["fake"]={}
    reopened=api.CollectionExecutionStore(store.journal.path,identity)
    assert [p["committed"] for p in reopened.replay_plan()] == commits
    assert reopened.replay_plan()[0]["prepared"]["decision"]["original_action"] == PAUSE
    assert reopened.recovery_state()["pending_prepared"] == pending
    assert store.journal.path.read_bytes() == before


@pytest.mark.parametrize("execution", ["none","unstarted","running","terminal"])
def test_replay_failure_seals_exact_pending_without_edge_fabrication(api,tmp_path,execution):
    if execution == "none":
        identity,_,_=inputs(tmp_path)
        store=api.CollectionExecutionStore(tmp_path/"failure.jsonl",identity)
        store.initialize_session()
        tick(store,0)
        req=None
    else:
        store,_,req=setup(api,tmp_path)
        if execution != "unstarted":tick(store,60,snapshots={req["execution_id"]:assignment(req)})
    recovery=store.recovery_state()
    now=120 if execution in ("running","terminal") else 60
    decision=store.arbitrate(WAIT,now,view())
    pending=store.prepare_tick(decision,previous_cursor=recovery["tick_sequence"],previous_digest=recovery["replay_digest"])
    if execution == "terminal":
        # Use an independent terminal overlay here. A device_restart targeting
        # this exact prepared execution now cancels it by contract, so it is no
        # longer a valid setup for the generic replay-failure path.
        store.record_edge_evidence(
            req["execution_id"], terminal_states=["INCONCLUSIVE"],
            event_ids=["terminal-before-replay-failure"], now_sim_t_s=120,
        )
    prefix=store.replay_plan()
    outbox=store.committed_outbox()
    edge=deepcopy(store.replay()["executions"][req["execution_id"]]["edge_evidence"]) if req else None
    before=store.journal.path.read_bytes()
    with pytest.raises(api.CollectionExecutionError):store.record_replay_failure("f"*64,now_sim_t_s=180)
    assert store.journal.path.read_bytes() == before
    store.record_replay_failure(digest(pending),now_sim_t_s=180,observed_digest="e"*64)
    sealed=store.journal.path.read_bytes()
    store.record_replay_failure(digest(pending),now_sim_t_s=180,observed_digest="e"*64)
    assert store.journal.path.read_bytes() == sealed
    with pytest.raises(api.CollectionExecutionError):store.record_replay_failure(digest(pending),now_sim_t_s=180,observed_digest="d"*64)
    reopened=api.CollectionExecutionStore(store.journal.path,store.session_identity)
    status=reopened.recovery_state()
    assert status["status"] == "REPLAY_MISMATCH" and not status["replay_permitted"]
    assert status["pending_prepared"] is None
    assert reopened.replay_plan() == prefix and reopened.committed_outbox() == outbox
    assert reopened.recovery_state()["replay_digest"] == recovery["replay_digest"]
    with pytest.raises(api.CollectionExecutionError):reopened.prepare_tick(decision,previous_cursor=recovery["tick_sequence"],previous_digest=recovery["replay_digest"])
    with pytest.raises(api.CollectionExecutionError):reopened.commit_tick(pending,now_sim_t_s=180,runtime_snapshots={},safety_shield={"allowed":True,"reason":None},post_state_digest="a"*64)
    with pytest.raises(api.CollectionExecutionError,match="unavailable"):conform(reopened)
    rows=reopened.replay()["executions"]
    if req:
        r=rows[req["execution_id"]]
        assert r["state"] == "INCONCLUSIVE" and r["reason"] == "REPLAY_MISMATCH"
        assert r["terminal_sim_t_s"] == (120 if execution == "terminal" else 180)
        assert r["conflicts"]["replay_mismatch"] and "ORPHANED_ACTIVITY" in r["device_protection"]["reasons"]
        if execution != "terminal":assert r["device_protection"]["reasons"] == ["ORPHANED_ACTIVITY"]
        assert r["device_protection"]["authorization_blocked"] and not r["success_display_allowed"]
        for key in ("raw_quantity","unload_quantity"):assert r[key]["status"] == "INCOMPLETE" and r[key]["balls"] is None
        assert r["edge_evidence"]["event_ids"] == edge["event_ids"]
        assert r["edge_evidence"]["terminal_states"] == edge["terminal_states"]
    else:assert not rows
    assert store.journal.path.read_bytes() == sealed


def test_replay_failure_rejects_nonpending_bad_digest_and_regressed_clock(api,tmp_path):
    store,_,_=setup(api,tmp_path)
    before=store.journal.path.read_bytes()
    with pytest.raises(api.CollectionExecutionError):store.record_replay_failure("a"*64,now_sim_t_s=60)
    assert store.journal.path.read_bytes() == before
    status=store.recovery_state()
    pending=store.prepare_tick(store.arbitrate(WAIT,60,view()),previous_cursor=0,previous_digest=status["replay_digest"])
    before=store.journal.path.read_bytes()
    for value in ("not-a-digest", 7):
        with pytest.raises(api.CollectionExecutionError):store.record_replay_failure(value,now_sim_t_s=60)
        with pytest.raises(api.CollectionExecutionError):store.record_replay_failure(digest(pending),now_sim_t_s=60,observed_digest=value)
    with pytest.raises(api.CollectionExecutionError):store.record_replay_failure(digest(pending),now_sim_t_s=59)
    assert store.journal.path.read_bytes() == before
    store.record_replay_failure(digest(pending),now_sim_t_s=60)
    status=store.recovery_state()
    assert status["replay_failure"]["observed_digest"] is None
    status["replay_failure"]["reason"]="mutated"
    assert store.recovery_state()["replay_failure"]["reason"] == "REPLAY_MISMATCH"
    with pytest.raises(api.CollectionExecutionError):store.record_replay_failure(digest(pending),now_sim_t_s=61)
    with pytest.raises(api.CollectionExecutionError):store.arbitrate(WAIT,120,view())


@pytest.mark.parametrize("execution", ["success","none","unaccepted"])
def test_committed_replay_failure_preserves_prefix_and_blocks_session(api,tmp_path,execution):
    if execution == "success":
        env,store,req,runtime,unload=live_store(api,tmp_path)
        for action in (WAIT,unload):live_tick(env,store,req,runtime,action)
    else:
        identity,planning,edge=inputs(tmp_path)
        store=api.CollectionExecutionStore(tmp_path/"prefix.jsonl",identity)
        binding=store.bind_confirmed_tasks(planning,edge,identity,60)[0]
        req=api.make_request(binding,"new","2026-09-16T00:01:00Z","2026-09-16T00:05:00Z")
        if execution == "unaccepted":store.submit(req)
        for now in (60,120):tick(store,now,action=PAUSE)
    prefix=store.replay_plan()
    target=prefix[0]
    recovery=store.recovery_state()
    later=store.arbitrate(WAIT,180,view())
    store.prepare_tick(later,previous_cursor=2,previous_digest=recovery["replay_digest"])
    rows=deepcopy(store.replay()["executions"])
    outbox=store.committed_outbox()
    confirmed=deepcopy(store.replay()["confirmed"])
    before=store.journal.path.read_bytes()
    kwargs=dict(prepared_digest=digest(target["prepared"]),committed_digest=digest(target["committed"]),observed_digest="e"*64,now_sim_t_s=180)
    store.record_committed_replay_failure(1,**kwargs)
    sealed=store.journal.path.read_bytes()
    assert sealed.startswith(before)
    assert len(store.journal.read()) == len(before.splitlines())+1
    store.record_committed_replay_failure(1,**kwargs)
    assert store.journal.path.read_bytes() == sealed
    for field,value in (("prepared_digest","d"*64),("committed_digest","d"*64),("observed_digest","d"*64),("now_sim_t_s",181),("now_sim_t_s",180.0)):
        with pytest.raises(api.CollectionExecutionError):store.record_committed_replay_failure(1,**dict(kwargs,**{field:value}))
    with pytest.raises(api.CollectionExecutionError):store.record_committed_replay_failure(2,**kwargs)
    with pytest.raises(api.CollectionExecutionError):store.record_replay_failure(digest(later),now_sim_t_s=180)
    reopened=api.CollectionExecutionStore(store.journal.path,store.session_identity)
    assert reopened.replay_plan() == prefix and reopened.committed_outbox() == outbox
    assert reopened.replay()["confirmed"] == confirmed
    status=reopened.recovery_state()
    assert status["status"] == "REPLAY_MISMATCH" and not status["replay_permitted"] and status["pending_prepared"] is None
    assert status["replay_failure"] == dict(kwargs,tick_sequence=1,reason="REPLAY_MISMATCH")
    status["replay_failure"]["tick_sequence"]=99
    assert reopened.recovery_state()["replay_failure"]["tick_sequence"] == 1
    assert reopened.recovery_state()["replay_digest"] == recovery["replay_digest"]
    with pytest.raises(api.CollectionExecutionError,match="unavailable"):conform(reopened)
    with pytest.raises(api.CollectionExecutionError):reopened.arbitrate(WAIT,180,view())
    with pytest.raises(api.CollectionExecutionError):reopened.prepare_tick(later,previous_cursor=2,previous_digest=recovery["replay_digest"])
    if execution == "success":
        r=reopened.replay()["executions"][req["execution_id"]]
        prior=rows[req["execution_id"]]
        assert prior["state"] == "SUCCEEDED" and r["state"] == "INCONCLUSIVE" and r["reason"] == "REPLAY_MISMATCH"
        assert r["terminal_sim_t_s"] == prior["terminal_sim_t_s"]
        assert r["runtime_evidence"]["collection_exit_reason"] == prior["runtime_evidence"]["collection_exit_reason"]
        assert r["conflicts"]["replay_mismatch"] and not r["success_display_allowed"]
        assert r["device_protection"] == dict(protected=True,authorization_blocked=True,reasons=["ORPHANED_ACTIVITY"])
        for key in ("raw_quantity","unload_quantity"):assert r[key]["balls"] is None and r[key]["status"] == "INCOMPLETE"
        for key in ("event_ids","terminal_states"):assert r["edge_evidence"][key] == prior["edge_evidence"][key]
    else:
        assert reopened.replay()["executions"] == rows
        if execution == "none":
            with pytest.raises(api.CollectionExecutionError):reopened.submit(req)
        else:
            with pytest.raises(api.CollectionExecutionError):reopened.record_acceptance(req["execution_id"],"late-accepted")
    assert store.journal.path.read_bytes() == sealed


def test_committed_replay_failure_requires_exact_verified_entry_and_clock(api,tmp_path):
    identity,_,_=inputs(tmp_path)
    store=api.CollectionExecutionStore(tmp_path/"validated-prefix.jsonl",identity)
    store.initialize_session()
    tick(store,0);tick(store,60)
    target=store.replay_plan()[0]
    kwargs=dict(prepared_digest=digest(target["prepared"]),committed_digest=digest(target["committed"]),observed_digest="e"*64,now_sim_t_s=120)
    before=store.journal.path.read_bytes()
    for number in (0,3,True,1.0,"1"):
        with pytest.raises(api.CollectionExecutionError):store.record_committed_replay_failure(number,**kwargs)
    for field,value in (("prepared_digest","f"*64),("committed_digest","f"*64),("observed_digest",None),("observed_digest","invalid"),("now_sim_t_s",119),("now_sim_t_s",float("nan"))):
        with pytest.raises(api.CollectionExecutionError):store.record_committed_replay_failure(1,**dict(kwargs,**{field:value}))
    assert store.journal.path.read_bytes() == before
    assert store.recovery_state()["status"] == "NO_PREPARED"
    store.record_committed_replay_failure(1,**kwargs)
    assert store.recovery_state()["status"] == "REPLAY_MISMATCH"
    assert not store.replay()["executions"] and not store.committed_outbox()
