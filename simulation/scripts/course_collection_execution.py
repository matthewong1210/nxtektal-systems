"""SIMULATION-only durable V3 execution composition evidence.

RangeSimulation/its BallLedger own all runtime quantities. This store owns only
binding, durable requests, arbitration and replay evidence. It never steps an
environment. The session driver must hold its session lock across prepare,
one live step, commit and cursor publication. JsonlJournal is reused solely as
the canonical/fsynced persistence mechanism, not as a new Edge business owner.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from nxt_edge_task.contracts import TaskRequest, TaskEvent
from nxt_edge_task.journal import JsonlJournal, RecordSpec, JournalRecord, JournalIntegrityError

ARBITER = "WAIT_ONLY_NON_PREEMPTIVE_V1"
SESSION_KEYS = ("series_id", "session_id", "round_id", "round_index", "engine_digest", "config_digest",
                "session_epoch_utc", "control_interval_s", "session_end_sim_t_s")
RECORD_KINDS = frozenset({"session", "binding", "binding_window", "request", "receipt", "accepted", "action_prepared",
                          "action_committed", "outbox_confirmed", "cursor", "device_restart", "edge_evidence", "replay_failure",
                          "committed_replay_failure", "preacceptance_rejection"})
TERMINALS = {"SUCCEEDED", "PARTIAL", "REJECTED", "MISSED", "FAILED", "INCONCLUSIVE"}
_OUTBOX_PHASE_ORDER = {
    "collecting": 0,
    "raw_collected": 1,
    "returning": 2,
    "unloading": 3,
    None: 4,
}
_SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "docs/contracts/collection-execution-v1/schema.json").read_text())


class CollectionExecutionError(ValueError):
    def __init__(self, code, detail):
        self.code = "collection_execution_" + code
        self.detail = detail
        super().__init__(f"{self.code}: {detail}")


def _require(condition, detail, code="conflict"):
    if not condition:
        raise CollectionExecutionError(code, detail)


def primitive(value):
    if isinstance(value, Mapping):
        return {k: primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [primitive(v) for v in value]
    return value


def canonical(value):
    return json.dumps(primitive(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _validate(value, name):
    """Validate the frozen schema vocabulary with stdlib only.

    Public output is independently checked with JSON Schema and the relational
    oracle in tests. This script must not acquire an undeclared runtime extra.
    """
    def matches(v, rule):
        if "$ref" in rule: return matches(v, _SCHEMA["$defs"][rule["$ref"].rsplit("/",1)[1]])
        if "const" in rule and v != rule["const"]: return False
        if "enum" in rule and v not in rule["enum"]: return False
        kinds = {"object":dict,"array":list,"string":str,"integer":int,"boolean":bool,"null":type(None)}
        kind = rule.get("type")
        if kind == "number":
            if type(v) not in (int,float) or not math.isfinite(v): return False
        elif kind is not None and type(v) is not kinds[kind]: return False
        if "anyOf" in rule and not any(matches(v,x) for x in rule["anyOf"]): return False
        if "allOf" in rule and not all(matches(v,x) for x in rule["allOf"]): return False
        if "not" in rule and matches(v,rule["not"]): return False
        if "if" in rule:
            if not matches(v, rule.get("then" if matches(v,rule["if"]) else "else", {})): return False
        if type(v) is dict:
            props = rule.get("properties",{})
            if not set(rule.get("required",[])) <= v.keys(): return False
            if rule.get("additionalProperties") is False and not v.keys() <= props.keys(): return False
            if not all(matches(v[k],r) for k,r in props.items() if k in v): return False
        if type(v) is list:
            if not rule.get("minItems",0) <= len(v) <= rule.get("maxItems",math.inf): return False
            if rule.get("uniqueItems") and len({digest(x) for x in v}) != len(v): return False
            if "items" in rule and not all(matches(x,rule["items"]) for x in v): return False
        if type(v) is str:
            if not rule.get("minLength",0) <= len(v) <= rule.get("maxLength",math.inf): return False
            if "pattern" in rule and re.search(rule["pattern"],v) is None: return False
            if rule.get("format") == "date-time": _utc(v)
        if type(v) in (int,float):
            if not math.isfinite(v) or not rule.get("minimum",-math.inf) <= v <= rule.get("maximum",math.inf): return False
        return True
    try:
        _require(matches(value,_SCHEMA["$defs"][name]), "invalid " + name, "invalid_request")
    except Exception as exc:
        raise CollectionExecutionError("invalid_request", f"invalid {name}: {exc.message if hasattr(exc, 'message') else exc}") from exc


def parse_collection_execution_read_contract(value, definition):
    """Validate and detach one frozen Manager read shape.

    This is the composition-root parser seam used before the transport wraps
    callback output.  The schema loaded above remains the single structural
    contract owner; the Site Agent package receives this callable and never
    imports the execution store, its schema path, or a JSON Schema runtime.
    """
    _require(
        definition in {"ExecutionSnapshot", "RequestReceipt"},
        "unsupported collection execution read definition",
        "invalid_request",
    )
    _validate(value, definition)
    return deepcopy(value)


def _utc(value):
    _require(isinstance(value, str) and value.endswith("Z"), "UTC must end in Z", "invalid_request")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def simulation_utc(identity, seconds):
    _require(type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0,
             "invalid simulation seconds", "invalid_request")
    return (_utc(identity["session_epoch_utc"]) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _identity(identity):
    _require(identity.get("schema") == "nxt-whole-course-session/v3" and identity.get("environment") == "SIMULATION", "V3 SIMULATION session required")
    _require(len(identity.get("station_ids", [])) == 1, "exactly one scenario station required")
    _require(all(k in identity for k in SESSION_KEYS), "incomplete session identity")
    _require(type(identity["control_interval_s"]) is int and identity["control_interval_s"] > 0, "positive control interval required")


def derive_execution_requirements(
    *, plan: Mapping[str, Any], input_records: Sequence[Mapping[str, Any]],
    session_identity: Mapping[str, Any], evidence_at_utc: str,
) -> dict[str, Any]:
    """Derive the bounded cycle from one exact retained Planning input."""
    s = primitive(session_identity)
    _identity(s)
    selected = plan["selection"]
    sources = [source for source in input_records if source["revision"] == plan["input_revision"]
               and source["input_digest"] == plan["input_digest"]]
    _require(len(sources) == 1, "plan must reference one historical input")
    source = sources[0]
    zones = [row for row in source["zones"] if (row["robot_id"], row["zone_id"])
             == (selected["robot_id"], selected["zone_id"])]
    mappings = [row for row in s.get("runtime_bindings", []) if (row["robot_id"], row["zone_id"])
                == (selected["robot_id"], selected["zone_id"])]
    _require(len(zones) == len(mappings) == 1, "unique explicit robot/zone binding required")
    mapping = mappings[0]
    _require(mapping["handoff_station_id"] == s["station_ids"][0], "bound station is not sole station")
    e = zones[0].get("cycle_minutes")
    _require(isinstance(e, dict) and isinstance(e.get("value"), dict), "missing cycle evidence")
    _require(_utc(e["observed_at_utc"]) <= _utc(evidence_at_utc) <= _utc(e["valid_until_utc"]), "stale or future cycle evidence")
    cycle = {k: e["value"][k] for k in ("travel", "collect", "return", "unload")}
    evidence = {k: e[k] for k in ("source_kind", "source_ref", "observed_at_utc", "valid_until_utc")}
    evidence.update(input_record_id=source["request_id"], input_revision=source["revision"], cycle_minutes=cycle,
                    derivation_rule="CEIL_TRAVEL_COLLECT_RETURN_UNLOAD_TO_CONTROL_INTERVAL_V1")
    _validate(evidence, "CycleEvidence")
    limit = math.ceil(sum(cycle.values()) * 60 / s["control_interval_s"]) * s["control_interval_s"]
    return {"runtime_binding": mapping, "cycle_evidence": evidence, "max_execution_s": limit}


def require_session_horizon(
    *, start_at_utc: str, max_execution_s: float, session_identity: Mapping[str, Any],
) -> None:
    selected_end = _utc(start_at_utc) + timedelta(seconds=max_execution_s)
    session_end = _utc(session_identity["session_epoch_utc"]) + timedelta(seconds=session_identity["session_end_sim_t_s"])
    _require(selected_end <= session_end, "confirmed collection cannot finish inside the active V3 session horizon")


def request_id_for_binding(binding_id: str) -> str:
    _require(isinstance(binding_id, str) and len(binding_id) == 64, "invalid binding ID")
    return "exec_req_" + digest({"schema": "nxt-collection-execution-request-id/v1",
        "kind": "EXECUTE_BOUND_COLLECTION", "binding_id": binding_id})[:24]


def bind_confirmed_tasks(planning_snapshot, edge_records, session_identity, now_sim_t_s):
    _identity(session_identity)
    _require(planning_snapshot.get("schema") == "nxt-planning/v1"
             and planning_snapshot.get("environment") == "SIMULATION", "unsupported planning evidence")
    simulation_utc(session_identity, now_sim_t_s)
    confirmations = [c for c in planning_snapshot["confirmations"] if c.get("task_id") is not None]
    _require(len({c["confirmation_id"] for c in confirmations}) == len(confirmations), "duplicate confirmation")
    bindings = [bind_confirmed_task(planning_snapshot, edge_records, session_identity, now_sim_t_s,
                                   task_id=c["task_id"]) for c in confirmations]
    return sorted(bindings, key=lambda b: b["binding_id"])


def bind_confirmed_task(planning_snapshot, edge_records, session_identity, now_sim_t_s, *, task_id: str) -> dict:
    matches = [c for c in planning_snapshot["confirmations"] if c.get("task_id") == task_id]
    _require(len(matches) == 1, "task must have one confirmed planning source")
    return _binding_for_confirmation(planning_snapshot, edge_records, session_identity, now_sim_t_s, matches[0])


def _binding_for_confirmation(planning_snapshot, edge_records, session_identity, now_sim_t_s, confirmation):
    """Bind an already verified PlanningOperations snapshot to verified Edge records.

    session_identity adds explicit station_ids/runtime_bindings to SESSION_KEYS
    and commissioned identity. Runtime aliases are declared fixture inputs.
    Historical Planning input records may be retained in input_records; otherwise
    only the exact latest_input revision can bind. No inferred revision fallback.
    """
    s, p = primitive(session_identity), primitive(planning_snapshot)
    _identity(s)
    _require(p.get("schema") == "nxt-planning/v1" and p.get("environment") == "SIMULATION", "unsupported planning evidence")
    bound = simulation_utc(s, now_sim_t_s)
    records = [r.to_dict() if hasattr(r, "to_dict") else primitive(r) for r in edge_records]
    c = primitive(confirmation)
    matched = [r for r in records if r["record_kind"] == "task_created" and r["payload"].get("task_id") == c["task_id"]]
    _require(len(matched) == 1, "exact TASK_CREATED evidence required")
    rec = matched[0]
    try:
        task = TaskRequest.from_dict(rec["payload"]["request"])
    except ValueError as exc:
        raise CollectionExecutionError("conflict", "invalid task content") from exc
    schedule = c["schedule"]
    _require(rec["payload"].get("schedule_id") == c["schedule_id"] and task.task_id == c["task_id"], "task/schedule identity mismatch")
    for key, expected in {"site_id": s["site_id"], "deployment_id": s["deployment_id"],
                          "target_robot_id": schedule["robot_id"], "zone_id": schedule["zone_id"],
                          "task_type": "COLLECT_BALLS_ZONE", "issued_by": "SIMULATION_TEST_ENTRY:" + schedule["operator"]}.items():
        _require(getattr(task, key) == expected, "task differs from frozen confirmation: " + key)
    # Planning v1 preserves the manager's valid RFC3339 spelling while
    # ScheduleService canonicalizes the admitted TaskRequest to
    # microseconds.  These are clock instants, not content aliases: only
    # semantic UTC equality is compatible, and the frozen Planning strings
    # remain the binding/request window below.
    for task_key, schedule_key in (
        ("issued_at_utc", "due_at_utc"),
        ("expires_at_utc", "expires_at_utc"),
    ):
        _require(
            _utc(getattr(task, task_key)) == _utc(schedule[schedule_key]),
            "task differs from frozen confirmation: " + task_key,
        )
    _require(schedule["admission_reference"] == c["confirmation_id"], "confirmation link mismatch")
    created = rec["recorded_at_utc"]
    _require(_utc(task.issued_at_utc) <= _utc(created) < _utc(task.expires_at_utc) and _utc(created) <= _utc(bound), "binding precedes valid task creation")
    _require(c.get("task_created_at_utc") == created, "planning task evidence differs")
    plans = [v for v in p["plans"] if v["plan_id"] == c["plan_id"] and v["version"] == c["plan_version"]]
    _require(len(plans) == 1, "exact plan revision required")
    plan = plans[0]
    _require(plan["input_revision"] == c["input_revision"] and all(plan["selection"][k] == schedule[k] for k in ("robot_id", "zone_id")) and plan["selection"]["start_at_utc"] == schedule["due_at_utc"], "plan confirmation mismatch")
    requirements = derive_execution_requirements(
        plan=plan,
        input_records=p.get("input_records", [p["latest_input"]] if p.get("latest_input") is not None else []),
        session_identity=s, evidence_at_utc=bound,
    )
    mapping, evidence, limit = (requirements[k] for k in ("runtime_binding", "cycle_evidence", "max_execution_s"))
    b = {k: s[k] for k in (*SESSION_KEYS, "site_id", "deployment_id", "commissioned_site_digest")}
    b.update({k: c[k] for k in ("plan_id", "plan_version", "confirmation_id", "schedule_id", "task_id")})
    b.update({k: mapping[k] for k in ("robot_id", "zone_id", "runtime_robot_id", "runtime_zone_id", "handoff_station_id")})
    b.update(schema="nxt-collection-execution-binding/v1", environment="SIMULATION", task_content_digest=task.content_digest(),
             incarnation=task.target_incarnation, max_execution_s=limit, cycle_evidence=evidence,
             handoff_binding_mode="SOLE_SCENARIO_STATION_V1", bound_at_sim_t_s=now_sim_t_s,
             bound_at_utc=bound, task_created_at_utc=created)
    b["binding_id"] = digest(b)
    _validate(b, "Binding")
    return b


def make_request(binding, request_id, due_at_utc, expires_at_utc):
    r = {k: binding[k] for k in ("binding_id", "session_id", "round_id", "task_id", "task_content_digest", "incarnation")}
    r.update(schema="nxt-collection-execution-request/v1", environment="SIMULATION", kind="EXECUTE_BOUND_COLLECTION", request_id=request_id,
             due_at_utc=due_at_utc, expires_at_utc=expires_at_utc,
             eligible_sim_t_s=(_utc(due_at_utc) - _utc(binding["session_epoch_utc"])).total_seconds(),
             latest_start_sim_t_s=(_utc(expires_at_utc) - _utc(binding["session_epoch_utc"])).total_seconds())
    r["execution_id"] = digest({k: r[k] for k in ("task_id", "task_content_digest", "session_id", "round_id", "binding_id")})
    _validate(r, "ExecutionRequest")
    return r


def _quantity(milestone, destination):
    return dict(milestone=milestone, status="NOT_REACHED", balls=None, source="RANGE_SIMULATION_BALL_LEDGER",
                assignment_id=None, source_event_ids=[], event_digest=None, destination_id=destination)


def _receipt(request, sequence, high_water):
    return dict(schema="nxt-collection-execution-request-receipt/v1", environment="SIMULATION",
                request_id=request["request_id"], request_digest=digest(request), binding_id=request["binding_id"],
                execution_id=request["execution_id"], attempt_id="attempt-" + request["execution_id"],
                sequence=sequence, request_log_high_water_digest=high_water, durable=True)


def _execution(b, req, receipt, policy):
    r = {k: b[k] for k in ("binding_id", "session_id", "round_id", "task_id", "incarnation", "runtime_robot_id", "runtime_zone_id", "handoff_station_id", "max_execution_s")}
    r.update({k: req[k] for k in ("request_id", "execution_id", "eligible_sim_t_s", "latest_start_sim_t_s")})
    r.update(attempt_id=receipt["attempt_id"], assignment_id=None, state="PENDING", stage="WAITING_FOR_POLICY_SLOT", reason=None,
             started_sim_t_s=None, execution_deadline_sim_t_s=None, terminal_sim_t_s=None, arbiter_version=ARBITER, policy_id=policy, actions=[],
             raw_quantity=_quantity("RAW_COLLECTED_TO_ROBOT", b["runtime_robot_id"]), unload_quantity=_quantity("UNLOADED_TO_STATION", b["handoff_station_id"]),
             runtime_evidence=dict(start_admitted=False, assignment_accepted=False, assignment_terminal=False, collection_exit_reason=None,
                                   event_sequence_complete=True, event_start_sequence=None, event_end_sequence=None, event_digest=None, conservation_passed=None, payload_parity_passed=None),
             edge_evidence=dict(task_id=b["task_id"], accepted=False, verified=False, effective_state="CREATED", reason=None, terminal_states=[], event_ids=[], result_verification="UNVERIFIED"),
             device_protection=dict(protected=False, reasons=[], authorization_blocked=False),
             conflicts={k: False for k in ("missing_events", "terminal_conflict", "session_round_drift", "incarnation_mismatch", "replay_mismatch")},
             reconciliation="NOT_PERFORMED", success_display_allowed=False)
    return r


def _protect(r, reason):
    p = r["device_protection"]
    p["reasons"] = sorted(set(p["reasons"]) | {reason})
    p.update(protected=True, authorization_blocked=True)


def _terminal(r, state, reason, now):
    # Delivery/conflict observations overlay a result; they cannot re-date an
    # already established causal terminal (in particular an exact deadline).
    terminal_time = r["terminal_sim_t_s"] if r["terminal_sim_t_s"] is not None else now
    r.update(state=state, stage="TERMINAL", reason=reason, terminal_sim_t_s=terminal_time, success_display_allowed=state == "SUCCEEDED")
    edge = r["edge_evidence"]
    mapped = "FAILED" if state == "PARTIAL" or state in ("MISSED", "REJECTED") and edge["accepted"] else "REJECTED" if state == "MISSED" else state
    edge_reason = "unknown:partial_execution" if state == "PARTIAL" else {
        "POLICY_SLOT_MISSED": "unknown:policy_slot_missed", "INSUFFICIENT_SESSION_HORIZON": "unknown:insufficient_session_horizon",
        "SAFETY_REJECTED": "unknown:safety_rejected", "NOT_STARTED_AFTER_RESTART": "not_started_after_restart",
        "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME": "interrupted_execution_unknown_outcome",
    }.get(reason, None if state == "SUCCEEDED" else "unknown:" + reason.lower())
    verified = state != "INCONCLUSIVE"
    edge.update(effective_state=mapped, reason=edge_reason, terminal_states=[mapped], verified=verified,
                result_verification="VERIFIED" if verified else "UNVERIFIED")
    if state == "INCONCLUSIVE": _protect(r, "RESTART_UNKNOWN")
    if reason in ("ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED"): _protect(r, reason)


def _apply_preacceptance_rejection(state, payload):
    _require(set(payload) == {"execution_id", "now_sim_t_s", "edge_record"}, "invalid pre-acceptance rejection")
    execution_id = payload["execution_id"]
    _require(execution_id in state["executions"], "unknown execution")
    _require(execution_id not in state["preacceptance_rejections"], "duplicate pre-acceptance rejection")
    _require(state["replay_failure"] is None, "session sealed by replay mismatch")
    now = payload["now_sim_t_s"]
    _require(type(now) in (int, float) and math.isfinite(now) and now >= state["now_sim_t_s"],
             "pre-acceptance rejection clock regression")

    evidence = payload["edge_record"]
    record_keys = {"schema_version", "sequence", "record_id", "record_kind", "origin", "recorded_at_utc", "payload"}
    _require(isinstance(evidence, dict) and set(evidence) == record_keys, "verified device JournalRecord required")
    _require(evidence["schema_version"] == "nxt-edge-task/journal/v1"
             and type(evidence["sequence"]) is int and evidence["sequence"] > 0
             and evidence["record_kind"] == "task_event_persisted"
             and evidence["origin"] == "DEVICE"
             and isinstance(evidence["payload"], dict) and set(evidence["payload"]) == {"event"},
             "persisted device task event required")
    expected_record_id = "rec_" + digest({
        "sequence": evidence["sequence"],
        "record_kind": evidence["record_kind"],
        "origin": evidence["origin"],
        "recorded_at_utc": evidence["recorded_at_utc"],
        "payload": evidence["payload"],
    })[:24]
    _require(evidence["record_id"] == expected_record_id, "device record identity mismatch")
    try:
        event = TaskEvent.from_dict(evidence["payload"]["event"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CollectionExecutionError("conflict", "invalid pre-acceptance task event") from exc
    _require(evidence["recorded_at_utc"] == event.reported_at_utc, "device record/event clock mismatch")
    _require(event.event_sequence == 0 and event.kind.value == "REJECTED"
             and event.reason_code == "incarnation_mismatch" and event.phase is None,
             "not an incarnation-mismatch request rejection")

    r = state["executions"][execution_id]
    b = state["bindings"][r["binding_id"]]
    _require(event.task_id == r["task_id"] and event.robot_id == b["robot_id"]
             and event.site_id == b["site_id"] and event.deployment_id == b["deployment_id"],
             "pre-acceptance rejection identity mismatch")
    _require(event.incarnation != r["incarnation"], "rejection does not prove a different incarnation")
    _require(execution_id not in state["accepted"] and r["state"] == "PENDING"
             and r["stage"] == "WAITING_FOR_POLICY_SLOT" and r["reason"] is None
             and r["started_sim_t_s"] is None and r["execution_deadline_sim_t_s"] is None
             and r["terminal_sim_t_s"] is None and r["assignment_id"] is None
             and not r["actions"] and state["pending_prepared"] is None,
             "rejection arrived after execution authorization")
    _require(r["runtime_evidence"] == {
        "start_admitted": False, "assignment_accepted": False, "assignment_terminal": False,
        "collection_exit_reason": None, "event_sequence_complete": True,
        "event_start_sequence": None, "event_end_sequence": None, "event_digest": None,
        "conservation_passed": None, "payload_parity_passed": None,
    }, "rejection contradicts runtime evidence")
    _require(r["edge_evidence"] == {
        "task_id": r["task_id"], "accepted": False, "verified": False,
        "effective_state": "CREATED", "reason": None, "terminal_states": [],
        "event_ids": [], "result_verification": "UNVERIFIED",
    }, "rejection contradicts prior Edge evidence")
    _require(not any(r["conflicts"].values()) and r["device_protection"] == {
        "protected": False, "reasons": [], "authorization_blocked": False,
    }, "rejection follows an existing conflict")
    for quantity in (r["raw_quantity"], r["unload_quantity"]):
        _require(quantity["status"] == "NOT_REACHED" and quantity["balls"] is None
                 and quantity["assignment_id"] is None and not quantity["source_event_ids"]
                 and quantity["event_digest"] is None,
                 "rejection contradicts quantity evidence")
    record_id = evidence["record_id"]
    _require(all(existing["edge_record"]["record_id"] != record_id
                 for existing in state["preacceptance_rejections"].values()),
             "duplicate pre-acceptance rejection record")

    _terminal(r, "REJECTED", "IDENTITY_CONFLICT", now)
    r["edge_evidence"].update(reason="incarnation_mismatch", event_ids=[record_id])
    r["conflicts"]["incarnation_mismatch"] = True
    _protect(r, "INCARNATION_MISMATCH")
    state["preacceptance_rejections"][execution_id] = deepcopy(payload)
    state["now_sim_t_s"] = now


def _runtime(r, snapshot, now, *, prefix_complete=True):
    """Only correlated actual-transfer events establish quantities."""
    e = primitive(snapshot)
    events = e.get("events", [])
    exact = (e.get("execution_id") == r["execution_id"] and e.get("robot_id") == r["runtime_robot_id"]
             and e.get("zone_id") == r["runtime_zone_id"] and e.get("handoff_station_id") == r["handoff_station_id"])
    if not exact:
        r["conflicts"]["session_round_drift"] = True
    start = e.get("started_sim_t_s")
    _require(start is not None and r["eligible_sim_t_s"] <= start < r["latest_start_sim_t_s"], "runtime start outside frozen window")
    _require(e.get("execution_deadline_sim_t_s") == start + r["max_execution_s"], "runtime deadline mismatch")
    r.update(assignment_id=e["assignment_id"], started_sim_t_s=start, execution_deadline_sim_t_s=e["execution_deadline_sim_t_s"])
    complete = (prefix_complete and exact and bool(events) and e.get("event_digest") == digest(events)
                and [v.get("sequence") for v in events] == list(range(1, len(events)+1))
                and len({v.get("event_id") for v in events}) == len(events)
                and all(v.get("assignment_id") == e["assignment_id"] and v.get("execution_id") == r["execution_id"]
                        and v.get("robot_id") == r["runtime_robot_id"] and v.get("zone_id") == r["runtime_zone_id"] for v in events))
    # Native collection exit is an immutable first boundary. Wire result cause
    # additionally reflects any later abnormal assignment terminal.
    terminal = e.get("terminal_reason")
    causal = terminal if terminal and terminal != "UNLOADED_ALL_COLLECTED_BALLS" else e.get("collection_exit_reason")
    runtime = r["runtime_evidence"]
    runtime.update(start_admitted=True, assignment_accepted=True, assignment_terminal=e.get("terminal_reason") is not None,
                   collection_exit_reason=causal, event_sequence_complete=complete,
                   event_start_sequence=1 if events else None, event_end_sequence=len(events) if events else None,
                   event_digest=e.get("event_digest"), conservation_passed=e.get("ledger_conserved"), payload_parity_passed=e.get("robot_payload_parity"))
    for key, total in (("raw_quantity", "raw_collected_balls"), ("unload_quantity", "unloaded_balls")):
        q = r[key]
        moves = [v for v in events if v["kind"] == q["milestone"]]
        valid = complete and all(type(v.get("balls")) is int and v["balls"] >= 0 for v in moves)
        if key == "raw_quantity":
            valid = valid and all(v.get("source_location") == "zone:" + r["runtime_zone_id"] and v.get("destination_location") == "robot:" + r["runtime_robot_id"] for v in moves)
        else:
            valid = valid and all(v.get("station_id") == r["handoff_station_id"] and v.get("source_location") == "robot:" + r["runtime_robot_id"] and v.get("destination_location") == "station:" + r["handoff_station_id"] for v in moves)
        amount = sum(v["balls"] for v in moves) if valid else None
        valid = valid and amount == e.get(total)
        # A complete assignment prefix proves zero transfers even before a move.
        q.update(status="COMPLETE" if valid else "INCOMPLETE", balls=amount if valid else None,
                 assignment_id=e["assignment_id"], source_event_ids=[v["event_id"] for v in moves] or ([events[-1]["event_id"]] if valid else []), event_digest=e.get("event_digest"))
    if not complete or any(r[k]["status"] == "INCOMPLETE" for k in ("raw_quantity", "unload_quantity")):
        r["conflicts"]["missing_events"] = True
        for key in ("raw_quantity", "unload_quantity"):
            r[key].update(status="INCOMPLETE", balls=None)
        reason = causal if causal in {"POLICY_PREEMPTED", "ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED", "EXECUTION_TIMEOUT"} else "EVIDENCE_INCOMPLETE"
        _terminal(r, "INCONCLUSIVE", reason, e.get("terminal_sim_t_s") if e.get("terminal_sim_t_s") is not None else now)
        return
    raw, unloaded = r["raw_quantity"]["balls"], r["unload_quantity"]["balls"]
    if unloaded > raw or not e.get("ledger_conserved") or not e.get("robot_payload_parity"):
        for key in ("raw_quantity", "unload_quantity"): r[key].update(status="INCOMPLETE", balls=None)
        r["conflicts"]["missing_events"] = True
        _terminal(r, "INCONCLUSIVE", "EVIDENCE_INCOMPLETE", now)
    elif e.get("terminal_reason"):
        success = (e["terminal_reason"] == "UNLOADED_ALL_COLLECTED_BALLS" and e["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
                   and raw > 0 and unloaded == raw and e["terminal_sim_t_s"] <= r["execution_deadline_sim_t_s"]
                   and not any(r["conflicts"].values()) and not r["device_protection"]["protected"])
        reason = "UNLOADED_ALL_COLLECTED_BALLS" if success else causal or terminal
        _terminal(r, "SUCCEEDED" if success else "PARTIAL" if raw > 0 else "FAILED", reason, e["terminal_sim_t_s"])
    else:
        r.update(state="RUNNING", stage="RAW_COLLECTED_TO_ROBOT" if raw else "TRAVEL_TO_COLLECTION")
        r["edge_evidence"].update(effective_state="RUNNING")


class CollectionExecutionStore:
    def __init__(self, path, session_identity, *, policy_id="JointDispatchPolicy-v1"):
        _identity(session_identity)
        self.session_identity = primitive(session_identity)
        self.policy_id = policy_id
        self.journal = JsonlJournal(path, allowed_kinds=RECORD_KINDS, allowed_origins=frozenset({"SIM_ENTRY"}))

    def _spec(self, kind, payload, now):
        return RecordSpec(kind, "SIM_ENTRY", simulation_utc(self.session_identity, now), payload)

    def initialize_session(self, now_sim_t_s=0):
        """Fsync immutable identity even when no task has been bound."""
        spec = self._spec("session", {"identity":self.session_identity, "policy_id":self.policy_id}, now_sim_t_s)
        def build(records):
            self._replay(records)
            return [] if records else [spec]
        self.journal.append_via(build)

    def _replay(self, records):
        state = dict(bindings={}, windows={}, requests={}, receipts={}, executions={}, accepted=set(), preacceptance_rejections={}, tick_sequence=0,
                     replay_digest=digest({"session": self.session_identity, "policy_id": self.policy_id, "arbiter": ARBITER}),
                     pending_prepared=None, commits={}, outbox={}, confirmed={}, blocked_outbox=set(), cursor=None, now_sim_t_s=0,
                     request_high_water=digest([]), edge_without_commit=False, replay_plan=[], replay_failure=None)
        receipt_records = set()
        session = False
        for rec in records:
            p, kind = primitive(rec.payload), rec.record_kind
            if kind == "session":
                _require(not session and p == {"identity": self.session_identity, "policy_id": self.policy_id}, "session identity drift")
                session = True
                state["now_sim_t_s"] = (_utc(rec.recorded_at_utc) - _utc(self.session_identity["session_epoch_utc"])).total_seconds()
                _require(state["now_sim_t_s"] >= 0, "session precedes epoch")
            else:
                _require(session, "missing session identity")
            if kind in ("binding", "request", "accepted"):
                _require(state["replay_failure"] is None, "session sealed against new authorization")
            if kind == "binding":
                b = p
                _validate(b, "Binding")
                _require(b["binding_id"] == digest({k:v for k,v in b.items() if k != "binding_id"}), "binding digest mismatch")
                _require(b["binding_id"] not in state["bindings"], "duplicate binding")
                _require(all(b[k] == self.session_identity[k] for k in SESSION_KEYS), "binding session drift")
                state["bindings"][b["binding_id"]] = b
                state["now_sim_t_s"] = max(state["now_sim_t_s"], b["bound_at_sim_t_s"])
            elif kind == "binding_window":
                _require(p["binding_id"] in state["bindings"] and p["binding_id"] not in state["windows"], "orphan or repeated binding window")
                state["windows"][p["binding_id"]] = p
            elif kind == "request":
                _validate(p, "ExecutionRequest")
                _require(p["request_id"] not in state["requests"], "duplicate request record")
                b = state["bindings"].get(p["binding_id"])
                _require(b is not None, "unknown request binding")
                window = state["windows"][b["binding_id"]]
                _require(all(p[k] == window[k] for k in ("due_at_utc", "expires_at_utc"))
                         and p == make_request(b, p["request_id"], p["due_at_utc"], p["expires_at_utc"])
                         and p["eligible_sim_t_s"] < p["latest_start_sim_t_s"], "request identity or clock mismatch")
                _require(p["execution_id"] not in state["executions"], "duplicate logical execution")
                state["requests"][p["request_id"]] = p
                state["request_high_water"] = digest({"previous":state["request_high_water"], "request":p, "record_id":rec.record_id})
                # A complete canonical request is the semantic commit. Receipt
                # is deterministic readback, including after a request-only tail.
                receipt = _receipt(p, len(state["requests"]), state["request_high_water"])
                state["receipts"][p["request_id"]] = receipt
                state["executions"][p["execution_id"]] = _execution(b, p, receipt, self.policy_id)
            elif kind == "receipt":
                _require(p == state["receipts"].get(p["request_id"]), "receipt/request mismatch")
                _require(p["request_id"] not in receipt_records, "duplicate receipt")
                receipt_records.add(p["request_id"])
            elif kind == "accepted":
                r = state["executions"][p["execution_id"]]
                _require(r["state"] not in TERMINALS, "acceptance after terminal execution")
                state["accepted"].add(p["execution_id"])
                r["edge_evidence"].update(accepted=True, effective_state="ACCEPTED", verified=True, result_verification="VERIFIED")
                r["edge_evidence"]["event_ids"].append(p["event_id"])
            elif kind == "preacceptance_rejection":
                _apply_preacceptance_rejection(state, p)
            elif kind == "action_prepared":
                _require(state["replay_failure"] is None, "session sealed by replay mismatch")
                _require(state["pending_prepared"] is None and p["tick_sequence"] == state["tick_sequence"] + 1
                         and p["previous_digest"] == state["replay_digest"] and p["previous_cursor"] == state["tick_sequence"]
                         and p["request_log_high_water_digest"] == state["request_high_water"], "invalid prepared predecessor")
                state["pending_prepared"] = p
            elif kind == "action_committed":
                _require(state["pending_prepared"] is not None and p["prepared_digest"] == digest(state["pending_prepared"]), "commit without exact prepared intent")
                expected = self._commit(state, state["pending_prepared"], p["result"])
                _require(expected == p, "committed replay mismatch")
                state["replay_plan"].append(dict(prepared=deepcopy(state["pending_prepared"]), committed=deepcopy(p)))
                # Delivery/restart overlays must never mutate the immutable
                # committed artifact that replay/digest verification consumes.
                state["executions"] = deepcopy(p["executions"])
                state["commits"][p["tick_sequence"]] = p
                state["tick_sequence"] = p["tick_sequence"]
                state["replay_digest"] = p["replay_digest"]
                state["now_sim_t_s"] = p["result"]["now_sim_t_s"]
                state["pending_prepared"] = None
                state["outbox"].update({v["outbox_id"]:v for v in p["outbox"]})
            elif kind == "replay_failure":
                self._seal_replay_failure(state, p)
            elif kind == "committed_replay_failure":
                self._seal_committed_replay_failure(state, p)
            elif kind == "outbox_confirmed":
                _require(p["outbox_id"] in state["outbox"], "Edge confirmation without committed outbox")
                _require(p["outbox_id"] not in state["blocked_outbox"],
                         "blocked outbox cannot be confirmed during replay")
                _require(p["outbox_id"] not in state["confirmed"] or
                         state["confirmed"][p["outbox_id"]] == p["event_id"],
                         "outbox event identity conflict during replay")
                state["confirmed"][p["outbox_id"]] = p["event_id"]
                r = state["executions"][state["outbox"][p["outbox_id"]]["execution_id"]]
                if p["event_id"] not in r["edge_evidence"]["event_ids"]: r["edge_evidence"]["event_ids"].append(p["event_id"])
            elif kind == "cursor":
                _require(p["tick_sequence"] == state["tick_sequence"] and p["replay_digest"] == state["replay_digest"], "cursor leads or differs from commit")
                state["cursor"] = p
            elif kind == "device_restart":
                r = state["executions"][p["execution_id"]]
                event = TaskEvent.from_dict(p["edge_record"]["payload"]["event"])
                unknown = event.reason_code == "interrupted_execution_unknown_outcome"
                expected_relation = ("MATCHED" if (r["started_sim_t_s"] is None) == (event.kind.value == "FAILED")
                                     else "START_EVIDENCE_CONFLICT")
                _require(p.get("start_evidence_relation") == expected_relation,
                         "restart start-evidence relation differs during replay")
                expected_blocked = sorted(
                    outbox_id for outbox_id, outbox in state["outbox"].items()
                    if outbox["execution_id"] == p["execution_id"] and outbox_id not in state["confirmed"]
                )
                _require(p.get("blocked_outbox_ids") == expected_blocked,
                         "restart blocked-outbox evidence differs during replay")
                state["blocked_outbox"].update(expected_blocked)
                terminals = sorted(set(r["edge_evidence"]["terminal_states"]) | {event.kind.value})
                if expected_relation == "START_EVIDENCE_CONFLICT":
                    _terminal(r, "INCONCLUSIVE", "REPLAY_MISMATCH", p["now_sim_t_s"])
                    r["conflicts"]["replay_mismatch"] = True
                    _protect(r, "ORPHANED_ACTIVITY")
                    r["edge_evidence"].update(
                        effective_state="CONFLICT", reason="unknown:replay_mismatch",
                        verified=False, result_verification="CONFLICT")
                elif len(terminals) > 1:
                    _terminal(r, "INCONCLUSIVE", "TERMINAL_CONFLICT", p["now_sim_t_s"])
                    r["conflicts"]["terminal_conflict"] = True
                    _protect(r, "TERMINAL_CONFLICT")
                    r["edge_evidence"].update(effective_state="CONFLICT", verified=False, result_verification="CONFLICT")
                elif r["state"] not in TERMINALS:
                    _terminal(r, event.kind.value, "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME" if unknown else "NOT_STARTED_AFTER_RESTART", p["now_sim_t_s"])
                    if unknown:
                        for key in ("raw_quantity", "unload_quantity"): r[key].update(status="INCOMPLETE", balls=None)
                        r["runtime_evidence"]["event_sequence_complete"] = False
                if expected_relation == "MATCHED":
                    _protect(r, "RESTART_UNKNOWN" if unknown else "ORPHANED_ACTIVITY")
                r["edge_evidence"]["terminal_states"] = terminals
                r["edge_evidence"]["event_ids"].append(p["edge_record"]["record_id"])
                pending = state["pending_prepared"]
                if (pending is not None
                        and pending["decision"]["execution_id"] == p["execution_id"]):
                    # The fsynced device restart terminal is explicit evidence
                    # that this exact old authorization cannot execute.  It
                    # cancels only that uncommitted intent; ordinary chunk
                    # recovery (and unrelated prepared policy ticks) remains
                    # replayable under the existing prepared/commit contract.
                    state["pending_prepared"] = None
                state["now_sim_t_s"] = max(state["now_sim_t_s"], p["now_sim_t_s"])
            elif kind == "edge_evidence":
                _require(p.get("outbox_id") not in state["blocked_outbox"],
                         "blocked outbox cannot receive Edge evidence during replay")
                r = state["executions"][p["execution_id"]]
                terminals = sorted(set(r["edge_evidence"]["terminal_states"]) | set(p["terminal_states"]))
                conflict = len(terminals) > 1
                outbox = state["outbox"].get(p.get("outbox_id"))
                orphan = (outbox is None or outbox["execution_id"] != p["execution_id"]
                          or any(kind != outbox["event_kind"] for kind in p["terminal_states"]))
                if conflict or orphan:
                    r["conflicts"]["terminal_conflict" if conflict else "replay_mismatch"] = True
                    _terminal(r, "INCONCLUSIVE", "TERMINAL_CONFLICT" if conflict else "REPLAY_MISMATCH", p["now_sim_t_s"])
                    _protect(r, "TERMINAL_CONFLICT" if conflict else "ORPHANED_ACTIVITY")
                    r["edge_evidence"].update(effective_state="CONFLICT", verified=False, result_verification="CONFLICT")
                    state["edge_without_commit"] |= orphan
                r["edge_evidence"]["terminal_states"] = terminals
                r["edge_evidence"]["event_ids"] = sorted(set(r["edge_evidence"]["event_ids"]) | set(p["event_ids"]))
                state["now_sim_t_s"] = max(state["now_sim_t_s"], p["now_sim_t_s"])
        _require(state["requests"].keys() == state["receipts"].keys(), "incomplete request/receipt append", "result_unknown")
        return state

    def replay(self):
        return self._replay(self.journal.read())

    def replay_plan(self):
        """Detached ordered full intents/results from verified committed ticks."""
        return deepcopy(self.replay()["replay_plan"])

    def _seal_replay_failure(self, state, payload):
        _require(set(payload) == {"prepared_digest", "now_sim_t_s", "observed_digest", "reason"}
                 and payload["reason"] == "REPLAY_MISMATCH", "invalid replay failure")
        _validate(payload["prepared_digest"], "Digest")
        if payload["observed_digest"] is not None: _validate(payload["observed_digest"], "Digest")
        simulation_utc(self.session_identity, payload["now_sim_t_s"])
        _require(state["replay_failure"] is None, "replay failure already sealed")
        pending = state["pending_prepared"]
        _require(pending is not None and digest(pending) == payload["prepared_digest"], "failure does not match exact pending intent")
        _require(payload["now_sim_t_s"] >= max(state["now_sim_t_s"], pending["decision"]["sim_t_s"]), "replay failure clock regression")
        self._apply_replay_failure(state, payload, pending["decision"]["execution_id"])

    def _apply_replay_failure(self, state, payload, eid):
        if eid is not None:
            r = state["executions"][eid]
            # This is evidence loss, not an Edge lifecycle event. Preserve its
            # durable identities/terminal facts while invalidating trust.
            edge = deepcopy(r["edge_evidence"])
            protection = deepcopy(r["device_protection"])
            _terminal(r, "INCONCLUSIVE", "REPLAY_MISMATCH", payload["now_sim_t_s"])
            r["device_protection"] = protection
            edge.update(effective_state="CONFLICT", verified=False, result_verification="CONFLICT", reason="unknown:replay_mismatch")
            r["edge_evidence"] = edge
            r["conflicts"]["replay_mismatch"] = True
            r["runtime_evidence"]["event_sequence_complete"] = False
            for key in ("raw_quantity", "unload_quantity"): r[key].update(status="INCOMPLETE", balls=None)
            _protect(r, "ORPHANED_ACTIVITY")
        state["replay_failure"] = deepcopy(payload)
        state["pending_prepared"] = None
        state["now_sim_t_s"] = payload["now_sim_t_s"]

    def record_replay_failure(self, prepared_digest, *, now_sim_t_s, observed_digest=None):
        """Irreversibly seal the exact pending intent; never emit Edge work."""
        payload = dict(prepared_digest=prepared_digest, now_sim_t_s=now_sim_t_s,
                       observed_digest=observed_digest, reason="REPLAY_MISMATCH")
        def build(records):
            state = self._replay(records)
            if state["replay_failure"] is not None:
                _require(state["replay_failure"] == payload, "replay failure content conflict")
                return []
            self._seal_replay_failure(state, payload)
            return [self._spec("replay_failure", payload, now_sim_t_s)]
        self.journal.append_via(build)

    def _validate_committed_replay_failure(self, payload):
        _require(set(payload) == {"tick_sequence", "prepared_digest", "committed_digest", "observed_digest", "now_sim_t_s", "reason"}
                 and payload["reason"] == "REPLAY_MISMATCH", "invalid committed replay failure")
        _require(type(payload["tick_sequence"]) is int and payload["tick_sequence"] > 0, "invalid committed tick sequence")
        for key in ("prepared_digest", "committed_digest", "observed_digest"): _validate(payload[key], "Digest")
        simulation_utc(self.session_identity, payload["now_sim_t_s"])

    def _seal_committed_replay_failure(self, state, payload):
        self._validate_committed_replay_failure(payload)
        _require(state["replay_failure"] is None, "replay failure already sealed")
        sequence = payload["tick_sequence"]
        _require(sequence <= len(state["replay_plan"]), "unknown committed tick")
        pair = state["replay_plan"][sequence - 1]
        _require(pair["prepared"]["tick_sequence"] == pair["committed"]["tick_sequence"] == sequence
                 and digest(pair["prepared"]) == payload["prepared_digest"]
                 and digest(pair["committed"]) == payload["committed_digest"], "failure does not match exact committed entry")
        _require(payload["now_sim_t_s"] >= state["now_sim_t_s"], "committed replay failure clock regression")
        self._apply_replay_failure(state, payload, pair["prepared"]["decision"]["execution_id"])

    def record_committed_replay_failure(self, tick_sequence, *, prepared_digest, committed_digest, observed_digest, now_sim_t_s):
        """Seal an exact verified prefix entry without rewriting its artifacts."""
        payload = dict(tick_sequence=tick_sequence, prepared_digest=prepared_digest, committed_digest=committed_digest,
                       observed_digest=observed_digest, now_sim_t_s=now_sim_t_s, reason="REPLAY_MISMATCH")
        self._validate_committed_replay_failure(payload)
        def build(records):
            state = self._replay(records)
            if state["replay_failure"] is not None:
                _require(digest(state["replay_failure"]) == digest(payload), "replay failure content conflict")
                return []
            self._seal_committed_replay_failure(state, payload)
            return [self._spec("committed_replay_failure", payload, now_sim_t_s)]
        self.journal.append_via(build)

    def bind_confirmed_tasks(self, planning_snapshot, edge_records, session_identity, now_sim_t_s):
        _require(primitive(session_identity) == self.session_identity, "session identity conflict")
        bindings = bind_confirmed_tasks(planning_snapshot, edge_records, session_identity, now_sim_t_s)
        def build(records):
            state = self._replay(records)
            specs = [] if records else [self._spec("session", {"identity":self.session_identity, "policy_id":self.policy_id}, now_sim_t_s)]
            for b in bindings:
                matches = [x for x in state["bindings"].values() if x["task_id"] == b["task_id"]]
                _require(not matches or matches == [b], "task already bound differently")
                if not matches:
                    _require(state["replay_failure"] is None, "session sealed against new authorization")
                    specs.append(self._spec("binding", b, now_sim_t_s))
                    c = next(c for c in planning_snapshot["confirmations"] if c["confirmation_id"] == b["confirmation_id"])
                    specs.append(self._spec("binding_window", dict(binding_id=b["binding_id"], due_at_utc=c["schedule"]["due_at_utc"], expires_at_utc=c["schedule"]["expires_at_utc"]), now_sim_t_s))
            return specs
        self.journal.append_via(build)
        return bindings

    def bind_confirmed_task(self, planning_snapshot, edge_records, session_identity, now_sim_t_s, *, task_id: str) -> dict:
        """Bind only this task; exact retries retain the original binding time."""
        _require(primitive(session_identity) == self.session_identity, "session identity conflict")
        result = {}

        def build(records):
            state = self._replay(records)
            matches = [b for b in state["bindings"].values() if b["task_id"] == task_id]
            _require(len(matches) <= 1, "task already bound differently")
            bound_at = matches[0]["bound_at_sim_t_s"] if matches else now_sim_t_s
            binding = bind_confirmed_task(planning_snapshot, edge_records, session_identity, bound_at, task_id=task_id)
            _require(not matches or matches == [binding], "task already bound differently")
            result.update(matches[0] if matches else binding)
            if matches:
                return []
            _require(state["replay_failure"] is None, "session sealed against new authorization")
            confirmation = next(c for c in planning_snapshot["confirmations"] if c.get("task_id") == task_id)
            specs = [] if records else [self._spec("session", {"identity": self.session_identity, "policy_id": self.policy_id}, now_sim_t_s)]
            specs.append(self._spec("binding", binding, now_sim_t_s))
            specs.append(self._spec("binding_window", dict(binding_id=binding["binding_id"],
                due_at_utc=confirmation["schedule"]["due_at_utc"], expires_at_utc=confirmation["schedule"]["expires_at_utc"]), now_sim_t_s))
            return specs

        self.journal.append_via(build)
        return result

    def submit(self, request):
        request = primitive(request)
        result = {}
        def build(records):
            state = self._replay(records)
            existing = state["requests"].get(request.get("request_id"))
            if existing is not None:
                _require(existing == request, "request ID content conflict")
                result.update(state["receipts"][existing["request_id"]]); return []
            _require(state["replay_failure"] is None, "session sealed against new authorization")
            _validate(request, "ExecutionRequest")
            b = state["bindings"].get(request["binding_id"])
            _require(b is not None, "unknown binding", "invalid_request")
            window = state["windows"][b["binding_id"]]
            _require(all(request[k] == window[k] for k in ("due_at_utc", "expires_at_utc")), "request rebases the frozen schedule")
            _require(request == make_request(b, request["request_id"], request["due_at_utc"], request["expires_at_utc"]), "request identity or clock mismatch")
            _require(request["eligible_sim_t_s"] < request["latest_start_sim_t_s"], "empty start window")
            for old in state["requests"].values():
                if old["execution_id"] == request["execution_id"]:
                    _require({k:v for k,v in old.items() if k != "request_id"} == {k:v for k,v in request.items() if k != "request_id"}, "logical task content conflict")
                    result.update(state["receipts"][old["request_id"]]); return []
            now = state["now_sim_t_s"]
            spec = self._spec("request", request, now)
            # Same public canonical identity formula as JsonlJournal, without
            # importing its private helper; verifies receipt read-back below.
            record_id = "rec_" + digest(dict(sequence=len(records)+1, record_kind=spec.record_kind, origin=spec.origin, recorded_at_utc=spec.recorded_at_utc, payload=request))[:24]
            hwm = digest({"previous":state["request_high_water"], "request":request, "record_id":record_id})
            receipt = _receipt(request, len(state["requests"])+1, hwm)
            result.update(receipt)
            return [spec, self._spec("receipt", receipt, now)]
        try:
            self.journal.append_via(build)
        except OSError as exc:
            raise CollectionExecutionError("result_unknown", "query or retry the original request ID") from exc
        return self.request_result(result["request_id"])

    def request_result(self, request_id):
        receipt = self.replay()["receipts"].get(request_id)
        _require(receipt is not None, "no durable receipt for request", "request_not_found")
        return deepcopy(receipt)

    def record_acceptance(self, execution_id, event_id):
        def build(records):
            state = self._replay(records)
            _require(execution_id in state["executions"], "unknown execution")
            if execution_id in state["accepted"]:
                _require(event_id in state["executions"][execution_id]["edge_evidence"]["event_ids"], "acceptance conflict")
                return []
            _require(state["replay_failure"] is None, "session sealed against new authorization")
            _require(state["executions"][execution_id]["state"] not in TERMINALS, "acceptance after terminal execution")
            return [self._spec("accepted", dict(execution_id=execution_id, event_id=event_id), state["now_sim_t_s"])]
        self.journal.append_via(build)

    def record_preacceptance_rejection(
        self,
        execution_id: str,
        edge_record: JournalRecord,
        *,
        now_sim_t_s: float,
    ) -> None:
        _require(isinstance(edge_record, JournalRecord), "verified device JournalRecord required")
        payload = {
            "execution_id": execution_id,
            "now_sim_t_s": now_sim_t_s,
            "edge_record": edge_record.to_dict(),
        }

        def build(records):
            state = self._replay(records)
            existing = state["preacceptance_rejections"].get(execution_id)
            if existing is not None:
                _require(canonical(existing) == canonical(payload), "conflicting pre-acceptance rejection")
                return []
            _apply_preacceptance_rejection(state, payload)
            return [self._spec("preacceptance_rejection", payload, now_sim_t_s)]

        self.journal.append_via(build)

    def arbitrate(self, original_action, now_sim_t_s, runtime_view):
        state = self.replay()
        _require(state["replay_failure"] is None, "session sealed by replay mismatch")
        _validate(original_action, "Action")
        _require(now_sim_t_s >= state["now_sim_t_s"], "simulation clock regression")
        _require(all(runtime_view.get(k) == self.session_identity[k] for k in ("session_id", "round_id")), "runtime session drift")
        executions = state["executions"]
        pending, expired = [], {}
        for r in executions.values():
            if r["state"] != "PENDING": continue
            if max(now_sim_t_s, r["eligible_sim_t_s"]) + r["max_execution_s"] > self.session_identity["session_end_sim_t_s"]:
                expired[r["execution_id"]] = "INSUFFICIENT_SESSION_HORIZON"
            elif now_sim_t_s >= r["latest_start_sim_t_s"]:
                expired[r["execution_id"]] = "POLICY_SLOT_MISSED"
            elif now_sim_t_s >= r["eligible_sim_t_s"] and r["edge_evidence"]["accepted"]:
                pending.append({k:r[k] for k in ("execution_id", "eligible_sim_t_s", "latest_start_sim_t_s")})
        pending.sort(key=lambda r:(r["latest_start_sim_t_s"],r["eligible_sim_t_s"],r["execution_id"]))
        running = [r for r in executions.values() if r["state"] == "RUNNING"]
        _require(len(running) <= 1, "multiple running leases")
        selected, selection, eid = deepcopy(original_action), "ORIGINAL_POLICY_UNCHANGED", None
        if running:
            r = running[0]; eid = r["execution_id"]
            if original_action["name"] != "Wait" and original_action["robot_id"] == r["runtime_robot_id"]:
                selection = ("ORIGINAL_POLICY_UNCHANGED" if original_action["name"] == "RequestHumanAssistance" else
                             "ORIGINAL_POLICY_CONVERGED" if original_action["name"] == "SendToHandoff" else "POLICY_PREEMPTED")
            elif original_action["name"] == "Wait" and now_sim_t_s < r["execution_deadline_sim_t_s"]:
                action = runtime_view.get("continuations", {}).get(eid)
                if action is not None:
                    expected = "AssignCollection" if r["runtime_evidence"]["collection_exit_reason"] is None else "SendToHandoff"
                    _require(action["name"] == expected and action["robot_id"] == r["runtime_robot_id"]
                             and action["target_id"] == (r["runtime_zone_id"] if action["name"] == "AssignCollection" else None), "invalid continuation")
                    selected, selection = deepcopy(action), "RUNNING_CONTINUATION"
        elif original_action["name"] == "Wait" and pending and not any(r["device_protection"]["authorization_blocked"] for r in executions.values()):
            r = executions[pending[0]["execution_id"]]
            robot = runtime_view.get("robots", {}).get(r["runtime_robot_id"], {})
            if robot.get("activity") == "IDLE" and robot.get("payload_balls") == 0:
                choices = [a for a in runtime_view["catalog_actions"] if a["name"] == "AssignCollection" and a["robot_id"] == r["runtime_robot_id"] and a["target_id"] == r["runtime_zone_id"]]
                _require(len(choices) == 1, "collection catalog action is missing or ambiguous")
                selected, selection, eid = deepcopy(choices[0]), "WAIT_SLOT", r["execution_id"]
        _require(selected in runtime_view["catalog_actions"], "selected action not in catalog")
        return dict(sim_t_s=now_sim_t_s, original_action=deepcopy(original_action), selected_action=selected, selection=selection,
                    eligible_pending=pending, execution_id=eid, terminalizations=expired, policy_id=self.policy_id, arbiter_version=ARBITER,
                    request_log_high_water_digest=state["request_high_water"])

    def prepare_tick(self, decision, *, previous_cursor, previous_digest):
        result = {}
        def build(records):
            s = self._replay(records)
            _require(s["replay_failure"] is None, "session sealed by replay mismatch")
            _require(s["pending_prepared"] is None, "uncommitted prepared action exists")
            _require(previous_cursor == s["tick_sequence"] and previous_digest == s["replay_digest"], "stale driver cursor/digest")
            _require(decision["request_log_high_water_digest"] == s["request_high_water"], "request prefix changed")
            result.update(tick_sequence=s["tick_sequence"]+1, previous_cursor=previous_cursor, previous_digest=previous_digest,
                          request_log_high_water_digest=s["request_high_water"], policy_id=self.policy_id, arbiter_version=ARBITER,
                          decision=primitive(decision))
            return [self._spec("action_prepared", result, decision["sim_t_s"])]
        self.journal.append_via(build)
        return deepcopy(result)

    def _commit(self, state, prepared, result):
        rows = deepcopy(state["executions"])
        d = prepared["decision"]
        now = result["now_sim_t_s"]
        _require(d["sim_t_s"] < now <= self.session_identity["session_end_sim_t_s"], "invalid committed simulation time")
        for eid, reason in d["terminalizations"].items():
            _terminal(rows[eid], "MISSED", reason, d["sim_t_s"])
        eid = d["execution_id"]
        if eid is not None:
            r = rows[eid]
            _require(r["state"] not in TERMINALS and r["edge_evidence"]["accepted"], "prepared task authorization no longer active")
            action = {k:deepcopy(d[k]) for k in ("sim_t_s", "original_action", "selected_action", "selection", "eligible_pending")}
            shield = result["safety_shield"]
            action.update(safety_shield="ACCEPTED" if shield["allowed"] else "REJECTED", safety_reason=None if shield["allowed"] else shield["reason"])
            if not shield["allowed"] and action["selection"] in ("ORIGINAL_POLICY_CONVERGED", "POLICY_PREEMPTED"):
                action["selection"] = "ORIGINAL_POLICY_UNCHANGED"
            elif shield["allowed"] and action["selection"] == "ORIGINAL_POLICY_CONVERGED":
                native = result["runtime_snapshots"].get(eid) or {}
                # A threshold handoff before the collection boundary can end
                # this assignment. Classify its actual same-tick effect, not
                # the prepared proposal or a later unrelated terminal.
                if native.get("terminal_reason") == "POLICY_PREEMPTED" and native.get("terminal_sim_t_s") == d["sim_t_s"]:
                    action["selection"] = "POLICY_PREEMPTED"
            _validate(action, "ActionDecision")
            r["actions"].append(action)
            if d["selection"] == "WAIT_SLOT" and not shield["allowed"]:
                _terminal(r, "REJECTED", "SAFETY_REJECTED", d["sim_t_s"])
            elif d["selection"] == "WAIT_SLOT":
                _require(eid in result["runtime_snapshots"], "accepted start has no runtime evidence")
        for eid, snapshot in result["runtime_snapshots"].items():
            _require(eid in rows, "unknown runtime assignment")
            if snapshot is None:
                _require(rows[eid]["started_sim_t_s"] is not None or rows[eid]["state"] in TERMINALS, "accepted start has no runtime evidence")
                continue
            if rows[eid]["state"] not in TERMINALS:
                if rows[eid]["started_sim_t_s"] is None:
                    _require(d["execution_id"] == eid and d["selection"] == "WAIT_SLOT" and result["safety_shield"]["allowed"], "assignment lacks admitted start decision")
                previous = next((c["result"]["runtime_snapshots"][eid] for c in reversed(list(state["commits"].values())) if eid in c["result"]["runtime_snapshots"]), None)
                prefix_complete = previous is None or snapshot.get("events",[])[:len(previous["events"])] == previous["events"]
                _runtime(rows[eid], snapshot, now, prefix_complete=prefix_complete)
        for r in rows.values():
            if r["state"] == "PENDING":
                if max(now, r["eligible_sim_t_s"]) + r["max_execution_s"] > self.session_identity["session_end_sim_t_s"]:
                    _terminal(r, "MISSED", "INSUFFICIENT_SESSION_HORIZON", now)
                elif now >= r["latest_start_sim_t_s"]:
                    _terminal(r, "MISSED", "POLICY_SLOT_MISSED", now)
            if r["state"] == "RUNNING" and (now >= r["execution_deadline_sim_t_s"] or result["runtime_snapshots"].get(r["execution_id"]) is None):
                r["conflicts"]["missing_events"] = True
                r["runtime_evidence"]["event_sequence_complete"] = False
                for key in ("raw_quantity","unload_quantity"): r[key].update(status="INCOMPLETE",balls=None)
                _terminal(r,"INCONCLUSIVE","EVIDENCE_INCOMPLETE",now)
        outbox = []
        for eid, r in sorted(rows.items()):
            before = state["executions"][eid]
            kinds = []
            if before["started_sim_t_s"] is None and r["started_sim_t_s"] is not None: kinds.append(("PROGRESS", "collecting"))
            if (r["raw_quantity"]["balls"] or 0) > 0 and not (before["raw_quantity"]["balls"] or 0): kinds.append(("PROGRESS", "raw_collected"))
            if d["execution_id"] == eid and d["selected_action"]["name"] == "SendToHandoff" and result["safety_shield"]["allowed"]:
                kinds.append(("PROGRESS", "returning"))
            if (r["unload_quantity"]["balls"] or 0) > 0 and not (before["unload_quantity"]["balls"] or 0):
                kinds.append(("PROGRESS", "unloading"))
            if r["state"] in TERMINALS and before["state"] not in TERMINALS: kinds.append((r["edge_evidence"]["effective_state"], None))
            for kind, phase in kinds:
                item = dict(execution_id=eid, tick_sequence=prepared["tick_sequence"], event_kind=kind, phase=phase,
                            reason=r["edge_evidence"]["reason"] if phase is None else None, task_id=r["task_id"], incarnation=r["incarnation"],
                            device_protection=deepcopy(r["device_protection"]))
                item["outbox_id"] = digest({"execution_id":eid, "tick_sequence":prepared["tick_sequence"], "event_kind":kind + (":" + phase if phase else "")})
                outbox.append(item)
        body = dict(tick_sequence=prepared["tick_sequence"], prepared_digest=digest(prepared), result=primitive(result), executions=rows, outbox=outbox)
        body["replay_digest"] = digest({"previous":state["replay_digest"], "commit":body})
        return body

    def commit_tick(self, prepared, *, now_sim_t_s, runtime_snapshots, safety_shield, post_state_digest):
        result = dict(now_sim_t_s=now_sim_t_s, runtime_snapshots=primitive(runtime_snapshots), safety_shield=primitive(safety_shield), post_state_digest=post_state_digest)
        answer = {}
        def build(records):
            s = self._replay(records)
            prior = s["commits"].get(prepared["tick_sequence"])
            if prior:
                _require(prior["prepared_digest"] == digest(prepared) and prior["result"] == result, "different result for committed tick")
                answer.update(prior); return []
            _require(s["pending_prepared"] == prepared, "commit without exact prepared record")
            answer.update(self._commit(s, prepared, result))
            return [self._spec("action_committed", answer, now_sim_t_s)]
        self.journal.append_via(build)
        return deepcopy(answer)

    def publish_cursor(self, tick_sequence, replay_digest):
        def build(records):
            s = self._replay(records)
            _require(tick_sequence == s["tick_sequence"] and replay_digest == s["replay_digest"] and tick_sequence > 0, "cursor requires matching commit")
            body = dict(tick_sequence=tick_sequence, replay_digest=replay_digest)
            return [] if s["cursor"] == body else [self._spec("cursor", body, s["now_sim_t_s"])]
        self.journal.append_via(build)

    def committed_outbox(self, *, unconfirmed_only=False):
        s = self.replay()
        values = [
            deepcopy(value)
            for outbox_id, value in s["outbox"].items()
            if not unconfirmed_only
            or outbox_id not in s["confirmed"] and outbox_id not in s["blocked_outbox"]
        ]
        return sorted(
            values,
            key=lambda value: (
                value["tick_sequence"],
                _OUTBOX_PHASE_ORDER[value["phase"]],
                value["outbox_id"],
            ),
        )

    def confirm_outbox(self, outbox_id, event_id):
        def build(records):
            s = self._replay(records)
            _require(outbox_id in s["outbox"], "unknown committed outbox")
            if outbox_id in s["confirmed"]:
                _require(s["confirmed"][outbox_id] == event_id, "outbox event identity conflict"); return []
            _require(outbox_id not in s["blocked_outbox"], "blocked outbox cannot be confirmed")
            return [self._spec("outbox_confirmed", dict(outbox_id=outbox_id, event_id=event_id), s["now_sim_t_s"])]
        self.journal.append_via(build)

    def record_edge_evidence(self, execution_id, *, terminal_states, event_ids, now_sim_t_s, outbox_id=None):
        def build(records):
            s=self._replay(records)
            _require(execution_id in s["executions"], "unknown execution")
            _require(isinstance(terminal_states,list) and set(terminal_states) <= {"SUCCEEDED","FAILED","REJECTED","INCONCLUSIVE"}, "invalid Edge terminal vocabulary")
            _require(isinstance(event_ids,list) and bool(event_ids) and len(set(event_ids)) == len(event_ids), "missing/duplicate Edge event identity")
            for event_id in event_ids: _validate(event_id,"Id")
            _require(now_sim_t_s >= s["now_sim_t_s"], "Edge evidence clock regression")
            payload=dict(execution_id=execution_id,terminal_states=terminal_states,event_ids=event_ids,now_sim_t_s=now_sim_t_s,outbox_id=outbox_id)
            prior = [primitive(r.payload) for r in records
                     if r.record_kind == "edge_evidence" and
                     (r.payload.get("outbox_id") == outbox_id if outbox_id is not None
                      else primitive(r.payload) == payload)]
            if prior:
                _require(len(prior) == 1 and all(prior[0][key] == payload[key]
                         for key in ("execution_id", "terminal_states", "event_ids", "outbox_id")),
                         "outbox Edge evidence identity conflict")
                return []
            if outbox_id is not None and outbox_id in s["outbox"]:
                _require(outbox_id not in s["blocked_outbox"],
                         "blocked outbox cannot receive Edge evidence")
            return [self._spec("edge_evidence",payload,now_sim_t_s)]
        self.journal.append_via(build)

    def record_device_restart_outcome(self, execution_id, edge_record, *, now_sim_t_s):
        """Ingest an already fsynced RobotCore restart terminal, never decide it.

        Caller supplies a JournalRecord from its verified device journal read.
        No clock, boot/incarnation generation or RobotCore lifecycle runs here.
        """
        _require(hasattr(edge_record,"to_dict"), "verified device JournalRecord required")
        evidence = edge_record.to_dict()
        _require(evidence["record_kind"] == "task_event_persisted", "persisted task event required")
        event = TaskEvent.from_dict(evidence["payload"]["event"])
        _require((event.kind.value,event.reason_code) in (("FAILED","not_started_after_restart"),("INCONCLUSIVE","interrupted_execution_unknown_outcome")), "not an Edge-owned restart terminal")
        def build(records):
            s = self._replay(records)
            _require(execution_id in s["executions"], "unknown execution")
            r = s["executions"][execution_id]
            b = s["bindings"][r["binding_id"]]
            _require(event.task_id == r["task_id"] and event.incarnation == r["incarnation"] and event.robot_id == b["robot_id"]
                     and event.site_id == b["site_id"] and event.deployment_id == b["deployment_id"], "restart evidence identity mismatch")
            relation = ("MATCHED" if (r["started_sim_t_s"] is None) == (event.kind.value == "FAILED")
                        else "START_EVIDENCE_CONFLICT")
            blocked = sorted(
                outbox_id for outbox_id, outbox in s["outbox"].items()
                if outbox["execution_id"] == execution_id and outbox_id not in s["confirmed"]
            )
            payload = dict(
                execution_id=execution_id,
                now_sim_t_s=now_sim_t_s,
                edge_record=evidence,
                start_evidence_relation=relation,
                blocked_outbox_ids=blocked,
            )
            for record in records:
                if record.record_kind == "device_restart" and record.payload["edge_record"]["record_id"] == evidence["record_id"]:
                    _require(primitive(record.payload) == payload, "restart evidence conflict")
                    return []
            _require(now_sim_t_s >= s["now_sim_t_s"], "restart evidence clock regression")
            return [self._spec("device_restart", payload, now_sim_t_s)]
        self.journal.append_via(build)

    def recovery_state(self):
        s = self.replay()
        status = ("REPLAY_MISMATCH" if s["replay_failure"] else "EDGE_WITHOUT_COMMIT" if s["edge_without_commit"] else "PREPARED_NO_COMMIT" if s["pending_prepared"]
                  else "COMMITTED_CURSOR_STALE" if s["tick_sequence"] and (s["cursor"] is None or s["cursor"]["tick_sequence"] != s["tick_sequence"])
                  else "COMMITTED_OUTBOX_UNCONFIRMED" if set(s["outbox"]) - set(s["confirmed"]) - set(s["blocked_outbox"]) else "NO_PREPARED")
        eid = s["pending_prepared"]["decision"]["execution_id"] if s["pending_prepared"] else None
        permitted = s["pending_prepared"] is not None and (eid is None or s["executions"][eid]["state"] not in TERMINALS)
        return dict(status=status, replay_permitted=permitted, pending_prepared=s["pending_prepared"], tick_sequence=s["tick_sequence"], replay_digest=s["replay_digest"],
                    unconfirmed_outbox=self.committed_outbox(unconfirmed_only=True), replay_failure=deepcopy(s["replay_failure"]))

    def snapshot(self, *, server_time_utc, session_state="ACTIVE"):
        """Pure read. Wall-clock read time is excluded from every persisted digest."""
        _utc(server_time_utc)
        s = self.replay()
        _require(s["replay_failure"] is None, "session sealed by replay mismatch", "unavailable")
        _require(all(r["edge_evidence"]["accepted"] or r["state"] in ("MISSED","REJECTED") for r in s["executions"].values()), "durable Edge acceptance not yet recorded", "unavailable")
        _require(not set(s["outbox"]) - set(s["confirmed"]) - set(s["blocked_outbox"]), "committed outbox awaits durable Edge evidence", "unavailable")
        data = {k:self.session_identity[k] for k in SESSION_KEYS}
        data.update(schema="nxt-collection-executions/v1", environment="SIMULATION", session_state=session_state,
                    now_sim_t_s=s["now_sim_t_s"], simulation_time_utc=simulation_utc(self.session_identity, s["now_sim_t_s"]),
                    server_time_utc=server_time_utc, replay_digest=s["replay_digest"],
                    bindings=list(s["bindings"].values()), requests=list(s["requests"].values()), receipts=list(s["receipts"].values()), executions=list(s["executions"].values()))
        _validate(data, "ExecutionSnapshot")
        return deepcopy(data)
