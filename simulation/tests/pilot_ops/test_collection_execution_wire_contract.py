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
    assert sum(r["state"] == "RUNNING" for r in executions.values()) <= 1, "single running lease per session/round"
    assert requests.keys() == receipts.keys(), "orphan request or receipt"
    unique(data["receipts"], "execution_id")
    unique(data["receipts"], "attempt_id")
    unique(data["executions"], "attempt_id")
    unique(data["executions"], "request_id")
    assert {r["execution_id"] for r in receipts.values()} == executions.keys(), "orphan execution or receipt"
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
        bound = datetime.fromisoformat(binding["bound_at_utc"].replace("Z", "+00:00"))
        created = datetime.fromisoformat(binding["task_created_at_utc"].replace("Z", "+00:00"))
        assert bound == epoch + timedelta(seconds=binding["bound_at_sim_t_s"])
        assert binding["bound_at_sim_t_s"] <= data["now_sim_t_s"]
        assert created <= bound
        assert observed <= bound <= valid
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
        assert record["binding_id"] == request["binding_id"] == receipt["binding_id"]
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
            assert binding["bound_at_sim_t_s"] <= start
            assert deadline == start + binding["max_execution_s"] <= data["session_end_sim_t_s"]
            assert start <= data["now_sim_t_s"]
            assert record["assignment_id"] is not None
            assert record["runtime_evidence"]["assignment_accepted"]
            starts = [a for a in record["actions"] if a["sim_t_s"] == start
                      and a["selected_action"]["name"] == "AssignCollection"]
            assert len(starts) == 1, "start requires one accepted collection action"
            initial = starts[0]
            assert initial["selection"] == "WAIT_SLOT"
            assert initial["safety_shield"] == "ACCEPTED"
            assert initial["selected_action"]["robot_id"] == binding["runtime_robot_id"]
            assert initial["selected_action"]["target_id"] == binding["runtime_zone_id"]
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
            assert binding["bound_at_sim_t_s"] <= action["sim_t_s"]
            previous = action["sim_t_s"]
            candidates = action["eligible_pending"]
            unique(candidates, "execution_id")
            for candidate in candidates:
                assert candidate["eligible_sim_t_s"] <= action["sim_t_s"] < candidate["latest_start_sim_t_s"]
                if candidate["execution_id"] == record["execution_id"]:
                    for key in ("eligible_sim_t_s", "latest_start_sim_t_s"):
                        assert candidate[key] == record[key]
            assert candidates == sorted(candidates, key=lambda x: (x["latest_start_sim_t_s"], x["eligible_sim_t_s"], x["execution_id"]))
            if action["selection"] == "WAIT_SLOT":
                assert action["original_action"]["name"] == "Wait"
                assert candidates and candidates[0]["execution_id"] == record["execution_id"]
                assert action["selected_action"]["name"] == "AssignCollection"
                assert action["selected_action"]["robot_id"] == binding["runtime_robot_id"]
                assert action["selected_action"]["target_id"] == binding["runtime_zone_id"]
                if start is not None:
                    assert action["sim_t_s"] == start, "WAIT_SLOT only starts a new attempt"
                # A pending start cannot displace any already running lease.
                for other in executions.values():
                    other_start, other_end = other["started_sim_t_s"], other["terminal_sim_t_s"]
                    active = other_start is not None and other_start < action["sim_t_s"] and (other_end is None or action["sim_t_s"] < other_end)
                    assert not active, "running continuation precedes pending starts"
            elif action["selection"] == "RUNNING_CONTINUATION":
                assert start is not None and start < action["sim_t_s"] < deadline
                assert end is None or action["sim_t_s"] <= end
                assert action["original_action"]["name"] == "Wait"
                selected = action["selected_action"]
                assert selected["name"] in ("AssignCollection", "SendToHandoff")
                assert selected["robot_id"] == binding["runtime_robot_id"]
                assert selected["target_id"] == (binding["runtime_zone_id"] if selected["name"] == "AssignCollection" else None)
                assert all(c["execution_id"] != record["execution_id"] for c in candidates)
            else:
                assert action["original_action"] == action["selected_action"]
            if action["selection"] == "ORIGINAL_POLICY_CONVERGED":
                assert action["selected_action"]["name"] == "SendToHandoff"
                assert action["selected_action"]["robot_id"] == binding["runtime_robot_id"]
                assert action["selected_action"]["target_id"] is None
        raw, unload = record["raw_quantity"], record["unload_quantity"]
        for quantity, milestone in ((raw, "RAW_COLLECTED_TO_ROBOT"), (unload, "UNLOADED_TO_STATION")):
            assert quantity["milestone"] == milestone
            destination = binding["runtime_robot_id"] if milestone == "RAW_COLLECTED_TO_ROBOT" else binding["handoff_station_id"]
            assert quantity["destination_id"] == destination
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
        terminals = set(edge["terminal_states"])
        conflict = len(terminals) > 1 or record["conflicts"]["terminal_conflict"] or record["conflicts"]["replay_mismatch"]
        assert edge["verified"] == (edge["result_verification"] == "VERIFIED")
        if conflict:
            assert record["state"] == "INCONCLUSIVE"
            assert edge["effective_state"] == "CONFLICT"
            assert edge["result_verification"] == "CONFLICT"
            assert not edge["verified"] and not record["success_display_allowed"]
            assert protection["protected"] and protection["authorization_blocked"]
            if len(terminals) > 1:
                assert record["conflicts"]["terminal_conflict"]
                assert "TERMINAL_CONFLICT" in protection["reasons"]
        else:
            assert edge["effective_state"] != "CONFLICT" and edge["result_verification"] != "CONFLICT"
            if terminals:
                assert terminals == {edge["effective_state"]}
        if edge["effective_state"] in ("SUCCEEDED", "FAILED", "REJECTED"):
            assert terminals == {edge["effective_state"]}
            assert edge["result_verification"] == "VERIFIED"
        if record["state"] == "INCONCLUSIVE" and not conflict:
            assert edge["result_verification"] == "UNVERIFIED"
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
            assert runtime["event_start_sequence"] is not None
            assert runtime["event_end_sequence"] is not None
            assert runtime["event_start_sequence"] <= runtime["event_end_sequence"]
            assert runtime["event_digest"] is not None
            assert runtime["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
            assert not any(record["conflicts"].values())
            assert not record["device_protection"]["protected"]
            assert edge["effective_state"] == "SUCCEEDED" and edge["verified"] and edge["accepted"]
        elif record["state"] == "PARTIAL":
            assert edge["effective_state"] == "FAILED" and edge["reason"] == "unknown:partial_execution"
            assert raw["status"] == "COMPLETE" and raw["balls"] > 0
            assert unload["status"] != "INCOMPLETE"
            assert runtime["event_sequence_complete"]
        elif record["state"] == "INCONCLUSIVE":
            assert edge["effective_state"] in ("INCONCLUSIVE", "CONFLICT")
            assert record["device_protection"]["protected"]
        elif record["state"] == "FAILED":
            assert edge["effective_state"] == "FAILED"
        if any(record["conflicts"].values()):
            assert record["state"] == "INCONCLUSIVE"
            assert record["device_protection"]["protected"]
        if edge["effective_state"] == "CONFLICT":
            assert record["conflicts"]["terminal_conflict"] or record["conflicts"]["replay_mismatch"]
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
    assert (binding["robot_id"], binding["zone_id"], binding["runtime_robot_id"], binding["runtime_zone_id"], binding["handoff_station_id"]) == ("picker-01", "Z1", "R1", "NEAR_LEFT", "H1")
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
    edge["terminal_states"] = ["REJECTED"]
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


@pytest.mark.parametrize(("path", "value"), [
    (("edge_evidence", "terminal_states"), ["SUCCEEDED", "FAILED"]),
    (("edge_evidence", "terminal_states"), ["FAILED"]),
    (("edge_evidence", "accepted"), False),
    (("runtime_evidence", "event_start_sequence"), None),
    (("runtime_evidence", "event_end_sequence"), None),
    (("runtime_evidence", "event_digest"), None),
    (("actions", 0, "selected_action", "robot_id"), "R2"),
    (("actions", 0, "selected_action", "target_id"), "OTHER_ZONE"),
    (("actions", 0, "safety_shield"), "REJECTED"),
    (("actions", 0, "eligible_pending", 2, "eligible_sim_t_s"), 180),
    (("actions", 0, "eligible_pending", 0, "latest_start_sim_t_s"), 60),
])
def test_review_success_contradictions_are_rejected(schema, path, value):
    data = snapshot()
    target = data["executions"][0]
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises((ValidationError, AssertionError)):
        validate_snapshot(schema, data)


def test_review_orphan_receipt_is_rejected(schema):
    data = snapshot()
    orphan = copy.deepcopy(data["receipts"][0])
    orphan["request_id"] = "orphan-request"
    data["receipts"].append(orphan)
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_review_partial_with_incomplete_unload_is_not_conclusive(schema):
    data = snapshot("partial-preempted.json")
    data["executions"][0]["unload_quantity"]["status"] = "INCOMPLETE"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_review_handoff_action_is_generic_and_destination_is_separate():
    data = snapshot()
    record, binding = data["executions"][0], data["bindings"][0]
    handoff = record["actions"][1]
    assert handoff["original_action"]["target_id"] is None
    assert handoff["selected_action"]["target_id"] is None
    assert record["raw_quantity"]["destination_id"] == binding["runtime_robot_id"]
    assert record["unload_quantity"]["destination_id"] == binding["handoff_station_id"]


def test_review_every_published_pending_candidate_is_currently_eligible():
    for name in EXPECTED_EXAMPLES:
        for record in snapshot(name)["executions"]:
            for action in record["actions"]:
                for candidate in action["eligible_pending"]:
                    assert candidate["eligible_sim_t_s"] <= action["sim_t_s"] < candidate["latest_start_sim_t_s"]


@pytest.mark.parametrize("collection", ["requests", "receipts", "executions"])
def test_review_missing_reverse_link_is_rejected(schema, collection):
    data = snapshot()
    data[collection] = []
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize(("name", "field", "value"), [
    ("success.json", "result_verification", "UNVERIFIED"),
    ("terminal-conflict.json", "result_verification", "VERIFIED"),
    ("terminal-conflict.json", "verified", True),
    ("terminal-conflict.json", "effective_state", "SUCCEEDED"),
])
def test_review_edge_verification_must_match_result(schema, name, field, value):
    data = snapshot(name)
    data["executions"][0]["edge_evidence"][field] = value
    with pytest.raises((ValidationError, AssertionError)):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("field", ["raw_quantity", "unload_quantity"])
def test_review_quantity_destination_must_match_binding(schema, field):
    data = snapshot()
    data["executions"][0][field]["destination_id"] = "OTHER_DESTINATION"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_review_partial_edge_reason_is_normalized(schema):
    data = snapshot("partial-preempted.json")
    assert data["executions"][0]["edge_evidence"]["reason"] == "unknown:partial_execution"
    data["executions"][0]["edge_evidence"]["reason"] = "partial_execution"
    with pytest.raises((ValidationError, AssertionError)):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("task_id", [
    "a" * 64, "task_" + "A" * 24, "task_" + "a" * 23,
    "task_" + "a" * 25, "other_" + "a" * 24,
])
def test_source_edge_task_id_uses_existing_v1_shape(schema, task_id):
    for definition, item in (
        ("Binding", snapshot()["bindings"][0]),
        ("ExecutionRequest", snapshot()["requests"][0]),
        ("ExecutionRecord", snapshot()["executions"][0]),
        ("EdgeEvidence", snapshot()["executions"][0]["edge_evidence"]),
    ):
        item["task_id"] = task_id
        with pytest.raises(ValidationError):
            validator(schema, f"#/$defs/{definition}").validate(item)


def test_source_handoff_uses_sole_scenario_station_h1():
    data = snapshot()
    binding = data["bindings"][0]
    assert binding["handoff_binding_mode"] == "SOLE_SCENARIO_STATION_V1"
    assert binding["handoff_station_id"] == "H1"
    assert data["executions"][0]["handoff_station_id"] == "H1"
    assert data["executions"][0]["unload_quantity"]["destination_id"] == "H1"


@pytest.mark.parametrize("mode", [None, "MULTI_STATION", "SOLE_SCENARIO_STATION_V2"])
def test_source_handoff_binding_rejects_other_modes(schema, mode):
    binding = snapshot()["bindings"][0]
    binding["handoff_binding_mode"] = mode
    with pytest.raises(ValidationError):
        validator(schema, "#/$defs/Binding").validate(binding)


def test_source_handoff_unload_cannot_claim_another_station(schema):
    data = snapshot()
    data["executions"][0]["unload_quantity"]["destination_id"] = "H2"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_source_task_id_and_full_content_digest_remain_distinct(schema):
    binding = snapshot()["bindings"][0]
    assert binding["task_id"] == "task_" + "5" * 24
    assert binding["task_content_digest"] == "6" * 64
    validator(schema, "#/$defs/TaskId").validate(binding["task_id"])
    validator(schema, "#/$defs/Digest").validate(binding["task_content_digest"])
    with pytest.raises(ValidationError):
        validator(schema, "#/$defs/Digest").validate(binding["task_id"])


def rekey_single_snapshot(data):
    """Keep negative controls cross-linked when changing an immutable binding."""
    binding, request, receipt, record = (data[key][0] for key in ("bindings", "requests", "receipts", "executions"))
    previous_execution = record["execution_id"]
    binding["binding_id"] = digest({k: v for k, v in binding.items() if k != "binding_id"})
    for key in ("binding_id", "task_id", "task_content_digest", "session_id", "round_id", "incarnation"):
        request[key] = binding[key]
    request["execution_id"] = digest({k: request[k] for k in ("task_id", "task_content_digest", "session_id", "round_id", "binding_id")})
    for key in ("request_id", "execution_id", "binding_id"):
        receipt[key] = request[key]
        record[key] = request[key]
    receipt["request_digest"] = digest(request)
    record["attempt_id"] = receipt["attempt_id"]
    for key in ("task_id", "runtime_robot_id", "runtime_zone_id", "handoff_station_id"):
        record[key] = binding[key]
    record["edge_evidence"]["task_id"] = binding["task_id"]
    for action in record["actions"]:
        # This isolated fixture has only its own pending start.
        action["eligible_pending"] = [
            {**candidate, "execution_id": request["execution_id"]}
            for candidate in action["eligible_pending"]
            if candidate["execution_id"] == previous_execution
        ]


def active_snapshot():
    data = snapshot()
    record = data["executions"][0]
    record.update(state="RUNNING", stage="TRAVEL_TO_UNLOAD", reason=None,
                  terminal_sim_t_s=None, success_display_allowed=False)
    record["actions"] = record["actions"][:1]
    record["runtime_evidence"]["assignment_terminal"] = False
    record["unload_quantity"].update(status="NOT_REACHED", balls=None,
                                    source_event_ids=[], event_digest=None)
    record["edge_evidence"].update(effective_state="RUNNING", result_verification="UNVERIFIED",
                                   verified=False, terminal_states=[])
    record["device_protection"] = {
        "protected": True, "reasons": ["RUNTIME_ACTIVE"], "authorization_blocked": True,
    }
    return data


def test_final_review_two_otherwise_valid_running_records_rejected(schema):
    first, second = active_snapshot(), active_snapshot()
    binding = second["bindings"][0]
    binding.update(task_id="task_" + "a" * 24, plan_id="plan-002",
                   confirmation_id="confirmation-002", schedule_id="schedule-002")
    second["requests"][0]["request_id"] = "request-002"
    second["receipts"][0].update(attempt_id="attempt-002", sequence=2)
    second["executions"][0]["assignment_id"] = "assignment-002"
    for field in ("raw_quantity", "unload_quantity"):
        second["executions"][0][field]["assignment_id"] = "assignment-002"
    rekey_single_snapshot(second)
    # Both inputs independently satisfy all identity, timing and evidence rules.
    validate_snapshot(schema, first)
    validate_snapshot(schema, second)
    combined = copy.deepcopy(first)
    for key in ("bindings", "requests", "receipts", "executions"):
        combined[key].extend(second[key])
    with pytest.raises(AssertionError, match="running lease"):
        validate_snapshot(schema, combined)


def test_final_review_running_handoff_after_latest_start_is_valid(schema):
    data = snapshot("duplicate-request.json")
    record = data["executions"][0]
    action = record["actions"][1]
    assert action["selection"] == "RUNNING_CONTINUATION"
    assert record["latest_start_sim_t_s"] < action["sim_t_s"] < record["execution_deadline_sim_t_s"]
    assert action["eligible_pending"], "continuation outranks a currently eligible pending start"
    validate_snapshot(schema, data)


def test_final_review_pending_start_cannot_displace_running_continuation(schema):
    data = snapshot("duplicate-request.json")
    action = data["executions"][0]["actions"][1]
    action["selection"] = "WAIT_SLOT"
    action["selected_action"] = {"name": "AssignCollection", "index": 1, "robot_id": "R2", "target_id": "NEAR_LEFT"}
    with pytest.raises((ValidationError, AssertionError)):
        validate_snapshot(schema, data)


def test_final_review_binding_carries_derived_clock_and_task_creation():
    binding = snapshot()["bindings"][0]
    assert binding["bound_at_sim_t_s"] == 60
    assert binding["bound_at_utc"] == "2026-09-16T00:01:00Z"
    assert binding["task_created_at_utc"] == "2026-09-16T00:01:00Z"


@pytest.mark.parametrize(("field", "value"), [
    ("evidence_valid_until", "2026-09-16T00:00:30Z"),
    ("bound_at_utc", "2026-09-16T00:02:00Z"),
    ("task_created_at_utc", "2026-09-16T00:02:00Z"),
])
def test_final_review_binding_clock_and_freshness(schema, field, value):
    data = snapshot()
    binding = data["bindings"][0]
    binding.update(bound_at_sim_t_s=60, bound_at_utc="2026-09-16T00:01:00Z",
                   task_created_at_utc="2026-09-16T00:01:00Z")
    if field == "evidence_valid_until":
        binding["cycle_evidence"]["valid_until_utc"] = value
    else:
        binding[field] = value
    rekey_single_snapshot(data)
    # Call the relational oracle directly to isolate chronology from shape.
    with pytest.raises(AssertionError):
        relations(data)


def test_final_review_freshness_uses_inclusive_binding_time_not_epoch(schema):
    data = snapshot()
    evidence = data["bindings"][0]["cycle_evidence"]
    evidence["observed_at_utc"] = "2026-09-16T00:00:30Z"
    evidence["valid_until_utc"] = "2026-09-16T00:01:00Z"
    rekey_single_snapshot(data)
    validate_snapshot(schema, data)


def test_final_review_binding_cannot_follow_its_execution_start(schema):
    data = snapshot()
    data["bindings"][0].update(bound_at_sim_t_s=180, bound_at_utc="2026-09-16T00:03:00Z")
    rekey_single_snapshot(data)
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)
