"""Durable composition evidence, checked against the independent 3A oracle."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from nxt_edge_task.contracts import TaskRequest, TaskEvent, EventKind
from nxt_edge_task.journal import JsonlJournal, RecordSpec, JournalIntegrityError
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


def test_binding_is_content_addressed_and_reads_cycle_at_binding_time(api, tmp_path):
    store, b, request = setup(api, tmp_path)
    assert b["max_execution_s"] == 660
    assert b["binding_id"] == digest({k: v for k, v in b.items() if k != "binding_id"})
    assert b["bound_at_utc"] == "2026-09-16T00:01:00Z"
    assert conform(store)["executions"][0]["raw_quantity"]["balls"] is None


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
    runtime = view(); runtime["continuations"][req["execution_id"]] = UNLOAD
    assert store.arbitrate(WAIT, 120, runtime)["selection"] == "RUNNING_CONTINUATION"
    assert store.arbitrate(UNLOAD, 120, runtime)["selection"] == "ORIGINAL_POLICY_CONVERGED"
    tick(store, 120, runtime, UNLOAD, {req["execution_id"]: assignment(req, terminal=True)})
    result = conform(store)["executions"][0]
    assert result["state"] == "SUCCEEDED" and result["success_display_allowed"]
    assert result["raw_quantity"]["balls"] == result["unload_quantity"]["balls"] == 7
    assert all("washed" not in item and "supplied" not in item for item in result)
    reopened = api.CollectionExecutionStore(store.journal.path, store.session_identity)
    assert conform(reopened) == conform(store)


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


@pytest.mark.parametrize("running", [False, True])
def test_explicit_device_restart_differs_from_prefix_replay(api, tmp_path, running):
    store, _, req = setup(api, tmp_path)
    if running: tick(store, 60, snapshots={req["execution_id"]: assignment(req)})
    restart(store,req,tmp_path,running=running)
    r = conform(store)["executions"][0]
    assert r["state"] == ("INCONCLUSIVE" if running else "FAILED")
    assert r["reason"] == ("INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME" if running else "NOT_STARTED_AFTER_RESTART")


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
    runtime=view(); runtime["continuations"][request["execution_id"]]=UNLOAD
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


def live_store(api, tmp_path):
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
    scenario=base.model_copy(update={"robots":[base.robots[0].model_copy(update={"payload_capacity_balls":20})],
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
