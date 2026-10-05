"""Behavioral coverage for explicit-evidence, human-led planning."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest

from nxt_pilot_ops.contracts import InboundBallBatch
from nxt_pilot_ops.planning import evaluate_plan
from nxt_pilot_ops.planning_contracts import (
    PlanningError, utc, utc_text, validate_input_request, validate_plan_request,
)
from nxt_pilot_ops.projection import project_stockout_minutes
from nxt_pilot_ops.serialization import canonical_json, stable_digest
from nxt_site_agent import SiteAgentError


NOW = "2026-09-16T01:00:00Z"
END = "2026-09-16T02:00:00Z"
CONTEXT = {"site_id": "pilot-course-a", "deployment_id": "pilot-a-edge-task-sim-v0",
           "site_timezone": "Asia/Shanghai", "zone_ids": ["A", "B"], "robot_ids": ["picker-01"]}


def evidence(value, unit):
    return {"value": value, "unit": unit, "source_kind": "MANUAL_ESTIMATE",
            "source_ref": "operator:opening-estimate", "observed_at_utc": NOW, "valid_until_utc": END}


def input_request():
    return {
        "schema": "nxt-planning-input/v1", "request_id": "opening", "expected_revision": 0,
        "site_id": CONTEXT["site_id"], "deployment_id": CONTEXT["deployment_id"],
        "site_timezone": CONTEXT["site_timezone"], "operator": "Manager", "reason": "Opening estimates",
        "scope": "SHIFT", "effective_at_utc": NOW, "valid_until_utc": END,
        "operating_window": {"start_at_utc": NOW, "end_at_utc": END},
        "inventory_clean_balls": evidence(100, "balls"),
        "safety_stock_balls": evidence(20, "balls"), "buffer_minutes": evidence(2, "minutes"),
        "demand": evidence({"bucket_minutes": 10, "low": [2, 2, 2],
                            "typical": [4, 4, 4], "high": [6, 6, 6]}, "balls/minute"),
        "operations_allowed": evidence(True, "boolean"), "washer_available": evidence(True, "boolean"),
        "zones": [{"zone_id": "A", "robot_id": "picker-01",
            "collection_allowed": evidence(True, "boolean"),
            "clean_yield_balls": evidence({"low": 120, "high": 180}, "balls"),
            "cycle_minutes": evidence({"travel": 1, "collect": 1, "return": 1,
                "unload": 1, "wash": 1, "supply": 1}, "minutes")}],
    }


def input_record(payload=None):
    payload = input_request() if payload is None else payload
    record = validate_input_request(payload, CONTEXT, NOW)
    return {**record, "revision": 1, "input_digest": stable_digest(record),
            "recorded_at_utc": NOW, "changes": []}


def plan_request(**changes):
    return {"schema": "nxt-planning-request/v1", "request_id": "plan-first", "plan_id": None,
            "expected_plan_version": 0, "input_revision": 1, "operator": "Manager",
            "reason": "Review a replenishment cycle", "scope": "ONE_TASK", "valid_until_utc": END,
            "selection": None, **changes}


def planning_operations(tmp_path, **options):
    from nxt_edge_task.contracts import EdgeTaskConfig
    from nxt_edge_task.journal import JsonlJournal
    from scripts.pilot_course_a_task_fixture import admission_facts
    from scripts.planning_operations import PlanningOperations

    config_path = Path(__file__).resolve().parents[2] / "configs/edge_task/pilot-course-a.sim.example.json"
    config = EdgeTaskConfig.from_dict(json.loads(config_path.read_text()))
    return PlanningOperations(JsonlJournal(tmp_path / "planning.jsonl"), config,
        admission_facts(), lambda: utc(NOW), site_timezone=CONTEXT["site_timezone"], **options)


def confirmed_plan_request(operations):
    source = input_request()
    source["zones"][0]["zone_id"] = operations.context["zone_ids"][0]
    operations.route("POST", "/api/v1/planning/inputs", source)
    plan = operations.route("POST", "/api/v1/planning/plans", plan_request())["record"]
    assert plan["status"] == "READY"
    return {"schema": "nxt-planning-confirmation/v1", "request_id": "confirm-first",
            "plan_id": plan["plan_id"], "plan_version": plan["version"], "operator": "Manager"}


def test_confirmation_gate_runs_once_inside_new_confirmation_append(tmp_path, monkeypatch):
    from nxt_pilot_ops.planning_workflow import CONFIRMATION, INPUT

    calls, inside_builder = [], []

    def gate(history, confirmation, now):
        assert inside_builder == [True]
        assert history.records(CONFIRMATION) == []
        assert len(history.records(INPUT)) == 1
        calls.append((confirmation["confirmation_id"], now))

    operations = planning_operations(tmp_path, confirmation_gate=gate)
    payload = confirmed_plan_request(operations)
    append_via = operations.journal.append_via

    def tracked_append(builder):
        def build(records):
            inside_builder.append(True)
            try:
                return builder(records)
            finally:
                inside_builder.pop()
        return append_via(build)

    monkeypatch.setattr(operations.journal, "append_via", tracked_append)
    first = operations.route("POST", "/api/v1/planning/confirmations", payload)
    before = operations.journal.path.read_bytes()
    duplicate = operations.route("POST", "/api/v1/planning/confirmations", payload)
    assert first["record"] == duplicate["record"]
    assert calls == [(first["record"]["confirmation_id"], operations.clock())]
    assert operations.journal.path.read_bytes() == before

    def reject(*_args):
        raise AssertionError("duplicate recovery must skip the gate")

    reopened = planning_operations(tmp_path, confirmation_gate=reject)
    reopened.clock = lambda: utc(END) + timedelta(days=1)
    assert reopened.route("POST", "/api/v1/planning/confirmations", payload)["record"] == first["record"]
    assert reopened.journal.path.read_bytes() == before


def test_confirmation_gate_conflict_appends_no_confirmation_or_schedule(tmp_path):
    def reject(_history, _confirmation, _now):
        raise PlanningError("planning_conflict", "selection exceeds V3 session horizon")

    operations = planning_operations(tmp_path, confirmation_gate=reject)
    payload = confirmed_plan_request(operations)
    before = operations.journal.path.read_bytes(), operations.journal.anchor_path.read_bytes()
    with pytest.raises(SiteAgentError, match="session horizon") as raised:
        operations.route("POST", "/api/v1/planning/confirmations", payload)
    assert raised.value.code == "planning_conflict"
    assert (operations.journal.path.read_bytes(), operations.journal.anchor_path.read_bytes()) == before


def test_confirmation_gate_mutation_cannot_change_committed_or_returned_record(tmp_path):
    from nxt_pilot_ops.planning_workflow import CONFIRMATION

    original = []

    def mutate(_history, confirmation, _now):
        original.append(deepcopy(confirmation))
        confirmation["schedule"]["zone_id"] = "unvalidated-zone"
        confirmation["request"]["operator"] = "unvalidated-operator"

    operations = planning_operations(tmp_path, confirmation_gate=mutate)
    payload = confirmed_plan_request(operations)
    response = operations.route("POST", "/api/v1/planning/confirmations", payload)
    assert response["record"] == original[0]
    stored = [record.to_dict()["payload"] for record in operations.journal.read()
              if record.record_kind == CONFIRMATION]
    assert stored == original
    snapshot = operations.snapshot(operations.clock())
    assert snapshot["confirmations"][0]["schedule"] == original[0]["schedule"]
    assert operations.route("POST", "/api/v1/planning/confirmations", payload)["record"] == original[0]
    assert len(original) == 1


def test_public_and_internal_snapshots_read_one_verified_prefix(tmp_path, monkeypatch):
    operations = planning_operations(tmp_path)
    confirmed_plan_request(operations)
    second = input_request()
    second["zones"][0]["zone_id"] = operations.context["zone_ids"][0]
    second.update(request_id="input-second", expected_revision=1)
    operations.route("POST", "/api/v1/planning/inputs", second)
    read, reads = operations.journal.read, []

    def counted_read():
        reads.append(True)
        return read()

    monkeypatch.setattr(operations.journal, "read", counted_read)
    public = operations.snapshot(operations.clock())
    assert len(reads) == 1
    assert "input_records" not in public
    internal = operations.execution_binding_snapshot(operations.clock())
    assert len(reads) == 2
    assert {k: v for k, v in internal.items() if k != "input_records"} == public
    assert [r["revision"] for r in internal["input_records"]] == [1, 2]
    internal["input_records"][0]["zones"].clear()
    assert operations.execution_binding_snapshot(operations.clock())["input_records"][0]["zones"]


def test_ready_scenarios_reuse_existing_stockout_projection_and_preserve_evidence():
    record, request = input_record(), plan_request()
    before = canonical_json((record, request))
    plan = evaluate_plan(record, request, NOW)
    assert plan["status"] == "READY"
    assert plan["rules_version"] == "human-led-ball-supply/v1"
    assert canonical_json((record, request)) == before
    assert plan["valid_until_utc"] == "2026-09-16T01:30:00Z"
    high = plan["scenarios"][2]
    expected = project_stockout_minutes(initial_clean_balls=100,
        demand_rates_balls_per_minute=(6, 6, 6), demand_bucket_minutes=10,
        inbound_batches=(), horizon_minutes=30)
    assert high["baseline_stockout_at_utc"] == utc_text(utc(NOW) + timedelta(minutes=expected))
    assert high["baseline_end_balls"] == -80
    assert high["with_plan_end_balls"] == 40
    assert high["with_plan_stockout_at_utc"] is None
    assert plan["candidates"][0]["cycle_minutes"] == 6
    assert plan["candidates"][0]["supply_at_utc"] == "2026-09-16T01:06:00Z"
    assert plan["adjustment"]["before"] == plan["system_selection"]
    assert plan["adjustment"]["after"] == plan["selection"]
    assert plan == evaluate_plan(record, request, NOW)
    plan["request"]["operator"] = "mutated"
    assert request["operator"] == "Manager"


def test_conditional_arrival_matches_existing_projection_exactly():
    record = input_record()
    plan = evaluate_plan(record, plan_request(), NOW)
    for scenario in plan["scenarios"]:
        expected = project_stockout_minutes(initial_clean_balls=100,
            demand_rates_balls_per_minute=tuple(record["demand"]["value"][scenario["level"]]),
            demand_bucket_minutes=10,
            inbound_batches=(InboundBallBatch("conditional", 6, 120, "manual"),),
            horizon_minutes=30)
        assert scenario["with_plan_stockout_at_utc"] == (
            None if expected is None else utc_text(utc(NOW) + timedelta(minutes=expected)))


def test_low_yield_insufficient_for_high_horizon_is_not_ready():
    payload = input_request()
    payload["zones"][0]["clean_yield_balls"]["value"] = {"low": 20, "high": 1_000}
    plan = evaluate_plan(input_record(payload), plan_request(), NOW)
    assert plan["status"] == "INFEASIBLE"
    assert plan["selection"] is not None
    assert plan["scenarios"][2]["with_plan_end_balls"] == -60
    assert plan["candidates"][0]["protects_high_demand_horizon"] is False


def test_all_six_stages_and_buffer_enter_latest_start_and_wash_delay():
    payload = input_request()
    first = evaluate_plan(input_record(payload), plan_request(), NOW)
    candidate = first["candidates"][0]
    assert utc(candidate["latest_start_at_utc"]) == utc("2026-09-16T01:05:20Z")
    payload["zones"][0]["cycle_minutes"]["value"]["wash"] = 20
    delayed = evaluate_plan(input_record(payload), plan_request(), NOW)
    assert delayed["status"] == "INFEASIBLE"
    assert delayed["candidates"][0]["cycle_minutes"] == 25
    assert "safety_stock_deadline_missed" in delayed["candidates"][0]["exclusion_reasons"]
    assert delayed["candidates"][0]["protects_high_demand_horizon"] is False


def test_manual_change_preserves_original_and_new_selection_and_impact():
    first = evaluate_plan(input_record(), plan_request(), NOW)
    selection = {"zone_id": "A", "robot_id": "picker-01", "start_at_utc": "2026-09-16T01:12:00Z"}
    request = plan_request(request_id="delay", plan_id=first["plan_id"], expected_plan_version=1,
                           selection=selection, operator="Operator", reason="Busy access path")
    revised = evaluate_plan(input_record(), request, NOW, previous_plan=first)
    assert revised["plan_id"] == first["plan_id"] and revised["version"] == 2
    assert revised["status"] == "INFEASIBLE"
    assert revised["adjustment"] == {"before": first["selection"], "after": selection,
        "operator": "Operator", "reason": "Busy access path", "scope": "ONE_TASK",
        "valid_until_utc": first["valid_until_utc"]}
    assert revised["system_selection"] == first["system_selection"]
    assert revised["scenarios"][2]["with_plan_stockout_at_utc"] == first["scenarios"][2]["baseline_stockout_at_utc"]
    restored = evaluate_plan(input_record(), plan_request(request_id="restore", plan_id=first["plan_id"],
        expected_plan_version=2), NOW, previous_plan=revised)
    assert restored["status"] == "READY" and restored["version"] == 3
    assert restored["adjustment"]["before"] == selection
    assert restored["selection"] == restored["system_selection"]


def test_elapsed_demand_is_not_erased_by_refreshing_the_clock():
    original = evaluate_plan(input_record(), plan_request(), NOW)
    late = evaluate_plan(input_record(), plan_request(), "2026-09-16T01:15:00Z")
    assert late["status"] == "INFEASIBLE"
    assert late["scenarios"][2]["baseline_stockout_at_utc"] == original["scenarios"][2]["baseline_stockout_at_utc"]
    assert late["scenarios"][2]["baseline_end_balls"] == -80
    assert late["candidates"][0]["supply_at_utc"] == "2026-09-16T01:21:00Z"
    assert late["candidates"][0]["protects_high_demand_horizon"] is False


@pytest.mark.parametrize("field", ["inventory_clean_balls", "demand", "safety_stock_balls",
    "buffer_minutes", "operations_allowed", "washer_available"])
def test_missing_evidence_does_not_become_zero_or_permission(field):
    payload = input_request()
    payload[field] = None
    plan = evaluate_plan(input_record(payload), plan_request(), NOW)
    assert plan["status"] == "MISSING_DATA"
    assert field in plan["missing"]
    assert plan["system_selection"] is None
    assert plan["candidates"][0]["start_at_utc"] is None
    assert plan["candidates"][0]["latest_start_at_utc"] is None
    assert plan["candidates"][0]["supply_at_utc"] is None
    if field in ("inventory_clean_balls", "demand"):
        assert plan["scenarios"] == []


@pytest.mark.parametrize("field", ["collection_allowed", "clean_yield_balls", "cycle_minutes"])
def test_incomplete_zone_is_missing_data_and_keeps_unknown_computation_null(field):
    payload = input_request()
    payload["zones"][0][field] = None
    plan = evaluate_plan(input_record(payload), plan_request(), NOW)
    assert plan["status"] == "MISSING_DATA"
    assert f"zones[A,picker-01].{field}" in plan["missing"]
    assert plan["candidates"][0]["eligible"] is False
    if field == "cycle_minutes":
        assert plan["candidates"][0]["supply_at_utc"] is None


@pytest.mark.parametrize("field", ["operations_allowed", "washer_available", "collection_allowed"])
def test_explicit_prohibitions_fail_closed(field):
    payload = input_request()
    owner = payload["zones"][0] if field == "collection_allowed" else payload
    owner[field]["value"] = False
    plan = evaluate_plan(input_record(payload), plan_request(), NOW)
    assert plan["status"] == "INFEASIBLE"
    assert plan["candidates"][0]["eligible"] is False


def test_expiry_is_exclusive_and_demand_does_not_extend_past_last_bucket():
    plan = evaluate_plan(input_record(), plan_request(), "2026-09-16T01:30:00Z")
    assert plan["status"] == "EXPIRED"
    assert plan["valid_until_utc"] == "2026-09-16T01:30:00Z"
    assert plan["scenarios"][2]["baseline_end_balls"] == -80


def test_latest_start_is_an_exclusive_admission_deadline():
    plan = evaluate_plan(input_record(), plan_request(selection={"zone_id": "A", "robot_id": "picker-01",
        "start_at_utc": "2026-09-16T01:05:20Z"}), NOW)
    assert plan["status"] == "INFEASIBLE"
    assert "safety_stock_deadline_missed" in plan["candidates"][0]["exclusion_reasons"]


def test_missing_empty_zones_and_explicit_zero_yield_are_distinct():
    payload = input_request()
    payload["zones"] = []
    assert evaluate_plan(input_record(payload), plan_request(), NOW)["status"] == "MISSING_DATA"
    payload = input_request()
    payload["zones"][0]["clean_yield_balls"]["value"] = {"low": 0, "high": 0}
    plan = evaluate_plan(input_record(payload), plan_request(), NOW)
    assert plan["status"] == "INFEASIBLE"
    assert plan["candidates"][0]["clean_yield_balls"] == {"low": 0, "high": 0}


def test_manual_selection_of_incomplete_candidate_keeps_missingness_and_user_intent():
    payload = input_request()
    payload["zones"][0]["cycle_minutes"] = None
    selection = {"zone_id": "A", "robot_id": "picker-01", "start_at_utc": NOW}
    plan = evaluate_plan(input_record(payload), plan_request(selection=selection), NOW)
    assert plan["status"] == "MISSING_DATA"
    assert plan["selection"] == selection
    assert plan["candidates"][0]["supply_at_utc"] is None


def test_candidate_ranking_is_deterministic_and_prefers_horizon_protection():
    payload = input_request()
    second = deepcopy(payload["zones"][0])
    second["zone_id"] = "B"
    payload["zones"][0]["clean_yield_balls"]["value"] = {"low": 20, "high": 40}
    payload["zones"].append(second)
    plan = evaluate_plan(input_record(payload), plan_request(), NOW)
    assert plan["selection"]["zone_id"] == "B"
    payload["zones"].reverse()
    reordered = evaluate_plan(input_record(payload), plan_request(), NOW)
    assert reordered["candidates"] == plan["candidates"]
    assert reordered["selection"] == plan["selection"]


def test_nearer_sufficient_zone_wins_over_larger_but_slower_yield():
    payload = input_request()
    farther = deepcopy(payload["zones"][0])
    farther["zone_id"] = "B"
    farther["clean_yield_balls"]["value"] = {"low": 500, "high": 600}
    farther["cycle_minutes"]["value"]["travel"] = 3
    payload["zones"].append(farther)
    plan = evaluate_plan(input_record(payload), plan_request(), NOW)
    assert all(candidate["protects_high_demand_horizon"] for candidate in plan["candidates"])
    assert plan["selection"]["zone_id"] == "A"
    assert any("zone A" in reason for reason in plan["rationale"])


def test_piecewise_demand_keeps_origin_even_part_way_through_a_bucket():
    payload = input_request()
    payload["demand"]["value"] = {"bucket_minutes": 10, "low": [1, 2, 3],
        "typical": [2, 4, 5], "high": [3, 6, 9]}
    original = evaluate_plan(input_record(payload), plan_request(), NOW)
    refreshed = evaluate_plan(input_record(payload), plan_request(), "2026-09-16T01:07:30Z")
    assert refreshed["scenarios"][2]["baseline_stockout_at_utc"] == original["scenarios"][2]["baseline_stockout_at_utc"]
    assert refreshed["scenarios"][2]["baseline_end_balls"] == -80


def test_plan_conflicts_and_unknown_selection_fail_without_evaluation():
    record = input_record()
    with pytest.raises(PlanningError, match="input revision"):
        evaluate_plan(record, plan_request(input_revision=2), NOW)
    with pytest.raises(PlanningError, match="absent"):
        evaluate_plan(record, plan_request(selection={"zone_id": "B", "robot_id": "picker-01", "start_at_utc": NOW}), NOW)
    first = evaluate_plan(record, plan_request(), NOW)
    with pytest.raises(PlanningError, match="version changed"):
        evaluate_plan(record, plan_request(plan_id=first["plan_id"], expected_plan_version=2), NOW, first)


@pytest.mark.parametrize("variant", ["ready", "missing", "expired", "infeasible", "manual_missing"])
def test_generated_plan_matches_the_shared_wire_schema(variant):
    import jsonschema
    schema = json.loads((Path(__file__).resolve().parents[2] / "docs/contracts/planning-v1/schema.json").read_text())
    plan_schema = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/PlanRecord"}
    payload, request, now = input_request(), plan_request(), NOW
    if variant == "missing":
        payload["inventory_clean_balls"] = None
    elif variant == "expired":
        now = END
    elif variant == "infeasible":
        payload["washer_available"]["value"] = False
    elif variant == "manual_missing":
        payload["zones"][0]["cycle_minutes"] = None
        request["selection"] = {"zone_id": "A", "robot_id": "picker-01", "start_at_utc": NOW}
    jsonschema.Draft202012Validator(plan_schema).validate(evaluate_plan(input_record(payload), request, now))
