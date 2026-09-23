"""Frozen wire assets plus relational conformance; no execution implementation.

JSON Schema validates structure. The explicit relational oracle below verifies
the published examples and negative controls; it is not a runtime admission API.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

CONTRACT = Path(__file__).resolve().parents[2] / "docs/contracts/collection-execution-v1"
EXPECTED_EXAMPLES = {
    "success.json", "policy-missed.json", "partial-preempted.json",
    "safety-rejected.json", "restart-unknown.json",
    "identity-conflict.json", "duplicate-request.json", "terminal-conflict.json",
}
FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time", raises=ValueError)
def valid_utc(value):
    if not isinstance(value, str):
        return True
    if not value.endswith("Z"):
        return False
    return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() == timedelta(0)


@pytest.fixture
def schema():
    path = CONTRACT / "schema.json"
    assert path.is_file(), "missing contract assets: schema.json"
    return json.loads(path.read_text())


def validator(schema, reference=None):
    selected = schema if reference is None else {
        "$schema": schema["$schema"], "$ref": reference, "$defs": schema["$defs"],
    }
    return Draft202012Validator(selected, format_checker=FORMAT_CHECKER)


def example(name="success.json"):
    path = CONTRACT / "examples" / name
    assert path.is_file(), f"missing contract assets: {name}"
    return json.loads(path.read_text())


def snapshot(name="success.json"):
    return example(name)["snapshot"]["body"]["data"]


def finite_numbers(value):
    if isinstance(value, float):
        assert math.isfinite(value), "nonfinite number"
    elif isinstance(value, dict):
        for item in value.values():
            finite_numbers(item)
    elif isinstance(value, list):
        for item in value:
            finite_numbers(item)


def unique(items, field):
    result = {item[field]: item for item in items}
    assert len(result) == len(items), f"duplicate {field}"
    return result


def digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def relations(data):
    """Normative cross-field rules beyond portable JSON Schema."""
    finite_numbers(data)
    bindings = unique(data["bindings"], "binding_id")
    requests = unique(data["requests"], "request_id")
    receipts = unique(data["receipts"], "request_id")
    executions = unique(data["executions"], "execution_id")
    unique(data["executions"], "attempt_id")
    seen_tasks = set()
    epoch = datetime.fromisoformat(data["session_epoch_utc"].replace("Z", "+00:00"))
    assert datetime.fromisoformat(data["simulation_time_utc"].replace("Z", "+00:00")) == epoch + timedelta(seconds=data["now_sim_t_s"])
    assert data["now_sim_t_s"] <= data["session_end_sim_t_s"]
    for binding in bindings.values():
        assert binding["binding_id"] == digest({k: v for k, v in binding.items() if k != "binding_id"})
        for key in ("session_id", "series_id", "round_id", "round_index", "session_epoch_utc", "session_end_sim_t_s", "control_interval_s", "engine_digest", "config_digest"):
            assert binding[key] == data[key], key
        evidence = binding["cycle_evidence"]
        observed = datetime.fromisoformat(evidence["observed_at_utc"].replace("Z", "+00:00"))
        valid = datetime.fromisoformat(evidence["valid_until_utc"].replace("Z", "+00:00"))
        assert observed <= epoch < valid
        total = sum(evidence["cycle_minutes"].values()) * 60
        assert binding["max_execution_s"] == math.ceil(total / binding["control_interval_s"]) * binding["control_interval_s"]
    for request in requests.values():
        binding = bindings[request["binding_id"]]
        assert request["execution_id"] == digest({k: request[k] for k in ("task_id", "task_content_digest", "session_id", "round_id", "binding_id")})
        for key in ("session_id", "round_id", "task_id", "task_content_digest", "incarnation"):
            assert request[key] == binding[key], key
        assert request["eligible_sim_t_s"] < request["latest_start_sim_t_s"]
        for utc, seconds in (("due_at_utc", "eligible_sim_t_s"), ("expires_at_utc", "latest_start_sim_t_s")):
            assert datetime.fromisoformat(request[utc].replace("Z", "+00:00")) == epoch + timedelta(seconds=request[seconds])
        identity = (request["task_id"], request["session_id"], request["binding_id"])
        assert identity not in seen_tasks, "duplicate logical task"
        seen_tasks.add(identity)
        receipt = receipts[request["request_id"]]
        assert receipt["request_digest"] == digest(request)
        for key in ("execution_id", "binding_id", "request_id"):
            assert receipt[key] == request[key]
    for record in executions.values():
        request = requests[record["request_id"]]
        binding = bindings[record["binding_id"]]
        receipt = receipts[record["request_id"]]
        assert record["execution_id"] == request["execution_id"] == receipt["execution_id"]
        assert record["attempt_id"] == receipt["attempt_id"]
        for key in ("session_id", "round_id", "task_id", "incarnation", "runtime_robot_id", "runtime_zone_id", "handoff_station_id", "max_execution_s"):
            assert record[key] == binding[key], key
        for key in ("eligible_sim_t_s", "latest_start_sim_t_s"):
            assert record[key] == request[key], key
        start, end, deadline = (record[key] for key in ("started_sim_t_s", "terminal_sim_t_s", "execution_deadline_sim_t_s"))
        if start is None:
            assert deadline is None and record["assignment_id"] is None
            assert not record["runtime_evidence"]["assignment_accepted"]
        else:
            assert request["eligible_sim_t_s"] <= start < request["latest_start_sim_t_s"]
            assert deadline == start + binding["max_execution_s"] <= data["session_end_sim_t_s"]
            assert start <= data["now_sim_t_s"]
            assert record["assignment_id"] is not None
            assert record["runtime_evidence"]["assignment_accepted"]
        if record["state"] in ("PENDING", "RUNNING"):
            assert end is None and record["reason"] is None and record["stage"] != "TERMINAL"
        else:
            assert end is not None and end <= data["now_sim_t_s"]
            assert record["stage"] == "TERMINAL" and record["reason"] is not None
            if start is not None:
                assert start <= end
        previous = -1
        for action in record["actions"]:
            assert previous <= action["sim_t_s"] <= data["now_sim_t_s"]
            previous = action["sim_t_s"]
            candidates = action["eligible_pending"]
            assert candidates == sorted(candidates, key=lambda x: (x["latest_start_sim_t_s"], x["eligible_sim_t_s"], x["execution_id"]))
            if action["selection"] == "WAIT_SLOT":
                assert action["original_action"]["name"] == "Wait"
                assert candidates and candidates[0]["execution_id"] == record["execution_id"]
            else:
                assert action["original_action"] == action["selected_action"]
            if action["selection"] == "ORIGINAL_POLICY_CONVERGED":
                assert action["selected_action"]["name"] == "SendToHandoff"
                assert action["selected_action"]["robot_id"] == binding["runtime_robot_id"]
                assert action["selected_action"]["target_id"] == binding["handoff_station_id"]
        raw, unload = record["raw_quantity"], record["unload_quantity"]
        for quantity, milestone in ((raw, "RAW_COLLECTED_TO_ROBOT"), (unload, "UNLOADED_TO_STATION")):
            assert quantity["milestone"] == milestone
            if quantity["status"] == "COMPLETE":
                assert quantity["source_event_ids"] and quantity["event_digest"] is not None
                assert quantity["assignment_id"] == record["assignment_id"]
            else:
                assert quantity["balls"] is None
        if raw["status"] == unload["status"] == "COMPLETE":
            assert unload["balls"] <= raw["balls"]
        runtime, edge = record["runtime_evidence"], record["edge_evidence"]
        assert edge["task_id"] == record["task_id"]
        assert record["success_display_allowed"] == (record["state"] == "SUCCEEDED")
        protection = record["device_protection"]
        assert protection["protected"] == bool(protection["reasons"])
        assert not protection["protected"] or protection["authorization_blocked"]
        if record["state"] in ("MISSED", "REJECTED"):
            assert edge["effective_state"] == ("FAILED" if edge["accepted"] else "REJECTED")
        if record["state"] == "PENDING":
            assert start is None and edge["effective_state"] == "ACCEPTED"
        if record["state"] == "RUNNING":
            assert start is not None and edge["effective_state"] == "RUNNING"
        if record["state"] == "SUCCEEDED":
            assert record["reason"] == "UNLOADED_ALL_COLLECTED_BALLS"
            assert raw["status"] == unload["status"] == "COMPLETE"
            assert raw["balls"] > 0 and raw["balls"] == unload["balls"]
            assert end <= deadline
            assert runtime["start_admitted"] and runtime["assignment_accepted"]
            assert runtime["conservation_passed"] and runtime["payload_parity_passed"]
            assert runtime["assignment_terminal"] and runtime["event_sequence_complete"]
            assert runtime["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
            assert not any(record["conflicts"].values())
            assert not record["device_protection"]["protected"]
            assert edge["effective_state"] == "SUCCEEDED" and edge["verified"]
        elif record["state"] == "PARTIAL":
            assert edge["effective_state"] == "FAILED" and edge["reason"] == "partial_execution"
            assert raw["status"] == "COMPLETE" and raw["balls"] > 0
        elif record["state"] == "INCONCLUSIVE":
            assert edge["effective_state"] in ("INCONCLUSIVE", "CONFLICT")
            assert record["device_protection"]["protected"]
        elif record["state"] == "FAILED":
            assert edge["effective_state"] == "FAILED"
        if any(record["conflicts"].values()):
            assert record["state"] == "INCONCLUSIVE"
            assert record["device_protection"]["protected"]
        if edge["effective_state"] == "CONFLICT":
            assert record["conflicts"]["terminal_conflict"]
        if record["reason"] == "POLICY_SLOT_MISSED":
            assert start is None and end >= request["latest_start_sim_t_s"]
        if edge["reason"] == "interrupted_execution_unknown_outcome":
            assert record["state"] == "INCONCLUSIVE" and start is not None
            assert raw["balls"] is None and unload["balls"] is None


def validate_snapshot(schema, data):
    validator(schema, "#/$defs/ExecutionSnapshot").validate(data)
    relations(data)


def test_assets_and_schema_exist(schema):
    assert (CONTRACT / "README.md").is_file()
    assert {p.name for p in (CONTRACT / "examples").glob("*.json")} == EXPECTED_EXAMPLES
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == "urn:nxtektal:collection-execution:v1"


@pytest.mark.parametrize("name", sorted(EXPECTED_EXAMPLES))
def test_all_declared_bodies_and_relations(schema, name):
    fixture = example(name)
    assert fixture["description"]
    for item in [*fixture["bodies"], fixture["snapshot"]]:
        validator(schema, item["schema_ref"]).validate(item["body"])
        validator(schema).validate(item["body"])
    validate_snapshot(schema, snapshot(name))


def test_exact_objects_reject_unknown_and_missing_fields(schema):
    data = snapshot()
    objects = [
        ("Binding", data["bindings"][0]), ("ExecutionRequest", data["requests"][0]),
        ("RequestReceipt", data["receipts"][0]), ("ExecutionSnapshot", data),
        ("ExecutionRecord", data["executions"][0]),
        ("QuantityEvidence", data["executions"][0]["raw_quantity"]),
        ("SuccessEnvelope", example()["snapshot"]["body"]),
        ("ErrorEnvelope", example("identity-conflict.json")["bodies"][0]["body"]),
    ]
    for definition, obj in objects:
        check = validator(schema, f"#/$defs/{definition}")
        with pytest.raises(ValidationError):
            check.validate({**obj, "unknown": 1})
        for key in obj:
            bad = copy.deepcopy(obj)
            del bad[key]
            with pytest.raises(ValidationError):
                check.validate(bad)


@pytest.mark.parametrize(("path", "value"), [
    (("environment",), "PHYSICAL"),
    (("now_sim_t_s",), True), (("now_sim_t_s",), -1),
    (("now_sim_t_s",), float("inf")), (("now_sim_t_s",), float("nan")),
    (("simulation_time_utc",), "2026-09-16T00:10:00+00:00"),
    (("simulation_time_utc",), "2026-02-30T00:00:00Z"),
    (("simulation_time_utc",), "2026-09-16T00:00:00.1234567Z"),
    (("bindings", 0, "binding_id"), "ABC"),
    (("bindings", 0, "engine_digest"), "A" * 64),
    (("bindings", 0, "plan_version"), True),
    (("bindings", 0, "max_execution_s"), 0),
    (("executions", 0, "state"), "COLLECTED"),
    (("executions", 0, "reason"), "ALL_GOOD"),
    (("executions", 0, "raw_quantity", "balls"), True),
    (("executions", 0, "raw_quantity", "balls"), None),
    (("executions", 0, "unload_quantity", "status"), "INCOMPLETE"),
])
def test_schema_rejects_malformed_values(schema, path, value):
    bad = snapshot()
    target = bad
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises((ValidationError, AssertionError)):
        validate_snapshot(schema, bad)


@pytest.mark.parametrize(("path", "value"), [
    (("bindings", 0, "max_execution_s"), 600),
    (("bindings", 0, "round_id"), "other-round"),
    (("requests", 0, "task_id"), "b" * 64),
    (("requests", 0, "due_at_utc"), "2026-09-16T00:02:00Z"),
    (("executions", 0, "execution_deadline_sim_t_s"), 999),
    (("executions", 0, "terminal_sim_t_s"), 10),
    (("executions", 0, "started_sim_t_s"), 300),
    (("executions", 0, "unload_quantity", "balls"), 43),
    (("executions", 0, "unload_quantity", "assignment_id"), "wrong-assignment"),
    (("executions", 0, "edge_evidence", "effective_state"), "FAILED"),
    (("executions", 0, "conflicts", "terminal_conflict"), True),
    (("executions", 0, "actions", 0, "original_action", "name"), "SendToCharge"),
    (("executions", 0, "actions", 1, "selected_action", "target_id"), "wrong-station"),
])
def test_relational_negative_controls(schema, path, value):
    bad = snapshot()
    target = bad
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises((ValidationError, AssertionError, KeyError)):
        validate_snapshot(schema, bad)


@pytest.mark.parametrize(("collection", "identity"), [
    ("bindings", "binding_id"), ("requests", "request_id"),
    ("receipts", "request_id"), ("executions", "execution_id"),
])
def test_duplicate_identities_rejected(schema, collection, identity):
    bad = snapshot()
    bad[collection].append(copy.deepcopy(bad[collection][0]))
    with pytest.raises(AssertionError, match="duplicate"):
        validate_snapshot(schema, bad)


def test_frozen_success_is_closed_loop_and_policy_preserved(schema):
    data = snapshot()
    validate_snapshot(schema, data)
    binding, record = data["bindings"][0], data["executions"][0]
    assert (binding["robot_id"], binding["zone_id"], binding["runtime_robot_id"], binding["runtime_zone_id"], binding["handoff_station_id"]) == ("picker-01", "Z1", "R1", "NEAR_LEFT", "handoff-1")
    assert binding["cycle_evidence"]["cycle_minutes"] == {"travel": 2, "collect": 5, "return": 2, "unload": 2}
    assert binding["control_interval_s"] == 60 and binding["max_execution_s"] == 660
    assert record["raw_quantity"]["balls"] == record["unload_quantity"]["balls"] == 44
    assert [a["selection"] for a in record["actions"]] == ["WAIT_SLOT", "ORIGINAL_POLICY_CONVERGED"]
    candidates = record["actions"][0]["eligible_pending"]
    assert len(candidates) >= 3
    bad = copy.deepcopy(data)
    bad["executions"][0]["actions"][0]["eligible_pending"].reverse()
    with pytest.raises(AssertionError):
        relations(bad)


def test_partial_restart_and_conflict_cannot_be_success(schema):
    for name in ("partial-preempted.json", "restart-unknown.json", "terminal-conflict.json"):
        data = snapshot(name)
        data["executions"][0]["edge_evidence"]["effective_state"] = "SUCCEEDED"
        with pytest.raises((ValidationError, AssertionError)):
            validate_snapshot(schema, data)
    conflict = snapshot("terminal-conflict.json")["executions"][0]
    assert conflict["edge_evidence"]["terminal_states"] == ["SUCCEEDED", "FAILED"]
    assert conflict["edge_evidence"]["effective_state"] == "CONFLICT"


def test_duplicate_requests_reuse_original_receipt():
    fixture = example("duplicate-request.json")
    requests = [b["body"] for b in fixture["bodies"] if b["schema_ref"] == "#/$defs/ExecutionRequest"]
    receipts = [b["body"] for b in fixture["bodies"] if b["schema_ref"] == "#/$defs/RequestReceipt"]
    assert len(requests) == len(receipts) == 3
    assert requests[0] == requests[1]
    assert requests[2]["request_id"] != requests[0]["request_id"]
    assert {k: v for k, v in requests[2].items() if k != "request_id"} == {k: v for k, v in requests[0].items() if k != "request_id"}
    assert receipts[0] == receipts[1] == receipts[2]
    assert len(snapshot("duplicate-request.json")["requests"]) == 1
    assert len(snapshot("duplicate-request.json")["executions"]) == 1


@pytest.mark.parametrize("name", ["policy-missed.json", "safety-rejected.json"])
def test_post_acceptance_exits_are_edge_failed_not_rejected(schema, name):
    data = snapshot(name)
    edge = data["executions"][0]["edge_evidence"]
    assert edge["accepted"] and edge["effective_state"] == "FAILED"
    edge["effective_state"] = "REJECTED"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)
    edge["accepted"] = False
    validate_snapshot(schema, data)


@pytest.mark.parametrize("field", ["raw_quantity", "unload_quantity"])
def test_unknown_quantity_cannot_be_normalized_to_zero(schema, field):
    data = snapshot("restart-unknown.json")
    data["executions"][0][field]["balls"] = 0
    with pytest.raises(ValidationError):
        validate_snapshot(schema, data)


def test_false_success_display_and_unprotected_conflict_rejected(schema):
    data = snapshot("terminal-conflict.json")
    data["executions"][0]["success_display_allowed"] = True
    with pytest.raises(ValidationError):
        validate_snapshot(schema, data)
    data = snapshot("terminal-conflict.json")
    data["executions"][0]["device_protection"]["authorization_blocked"] = False
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_identity_conflict_preserves_rejected_input_without_an_attempt():
    fixture = example("identity-conflict.json")
    rejected = fixture["bodies"][1]["body"]
    data = fixture["snapshot"]["body"]["data"]
    assert rejected["incarnation"] != data["bindings"][0]["incarnation"]
    assert fixture["bodies"][0]["body"]["error"]["code"] == "collection_execution_conflict"
    assert data["requests"] == data["receipts"] == data["executions"] == []
