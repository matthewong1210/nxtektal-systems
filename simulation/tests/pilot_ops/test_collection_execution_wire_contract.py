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


def strict_json_loads(source):
    """Read fixture/schema JSON without erasing ambiguous claims; test-only."""
    def exact_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"nonfinite JSON constant: {value}")

    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("nonfinite JSON number")
        return parsed

    return json.loads(source, object_pairs_hook=exact_object,
                      parse_constant=reject_constant, parse_float=finite_float)


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
    return strict_json_loads(path.read_text(encoding="utf-8"))


def validator(schema, reference=None):
    selected = schema if reference is None else {
        "$schema": schema["$schema"], "$ref": reference, "$defs": schema["$defs"],
    }
    return Draft202012Validator(selected, format_checker=FORMAT_CHECKER)


def example(name="success.json"):
    path = CONTRACT / "examples" / name
    assert path.is_file(), f"missing contract assets: {name}"
    return strict_json_loads(path.read_text(encoding="utf-8"))


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
    started_records = [r for r in executions.values() if r["started_sim_t_s"] is not None]
    for index, record in enumerate(started_records):
        start = record["started_sim_t_s"]
        end = record["terminal_sim_t_s"] if record["terminal_sim_t_s"] is not None else math.inf
        for other in started_records[index + 1:]:
            if other["execution_id"] == record["execution_id"]:
                continue
            other_start = other["started_sim_t_s"]
            other_end = other["terminal_sim_t_s"] if other["terminal_sim_t_s"] is not None else math.inf
            assert start != other_start, "distinct attempts cannot acquire the lease at the same tick"
            assert max(start, other_start) >= min(end, other_end), "overlapping running lease intervals"
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
        runtime, edge = record["runtime_evidence"], record["edge_evidence"]
        protection = record["device_protection"]
        if start is None:
            assert deadline is None and record["assignment_id"] is None
            assert not runtime["assignment_accepted"] and not runtime["start_admitted"]
        else:
            assert request["eligible_sim_t_s"] <= start < request["latest_start_sim_t_s"]
            assert binding["bound_at_sim_t_s"] <= start
            assert deadline == start + binding["max_execution_s"] <= data["session_end_sim_t_s"]
            assert start <= data["now_sim_t_s"]
            assert record["assignment_id"] is not None
            assert edge["accepted"] and runtime["assignment_accepted"] and runtime["start_admitted"]
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
            assert data["session_state"] != "ENDED"
        else:
            assert end is not None and end <= data["now_sim_t_s"]
            assert binding["bound_at_sim_t_s"] <= end
            assert record["stage"] == "TERMINAL" and record["reason"] is not None
            if start is not None:
                assert start <= end
        previous = -1
        for action in record["actions"]:
            assert previous <= action["sim_t_s"] <= data["now_sim_t_s"]
            assert binding["bound_at_sim_t_s"] <= action["sim_t_s"]
            assert end is None or action["sim_t_s"] <= end
            assert (action["safety_reason"] is None) == (action["safety_shield"] == "ACCEPTED")
            for action_evidence in (action["original_action"], action["selected_action"]):
                if action_evidence["name"] == "Wait":
                    assert action_evidence["robot_id"] is None and action_evidence["target_id"] is None
                if action_evidence["name"] == "SendToHandoff":
                    assert action_evidence["robot_id"] is not None and action_evidence["target_id"] is None
            previous = action["sim_t_s"]
            candidates = action["eligible_pending"]
            unique(candidates, "execution_id")
            for candidate in candidates:
                assert candidate["eligible_sim_t_s"] <= action["sim_t_s"] < candidate["latest_start_sim_t_s"]
                included = executions.get(candidate["execution_id"])
                if included is not None:
                    for key in ("eligible_sim_t_s", "latest_start_sim_t_s"):
                        assert candidate[key] == included[key]
                    assert included["started_sim_t_s"] is None or action["sim_t_s"] <= included["started_sim_t_s"]
                    assert included["terminal_sim_t_s"] is None or action["sim_t_s"] <= included["terminal_sim_t_s"]
            assert candidates == sorted(candidates, key=lambda x: (x["latest_start_sim_t_s"], x["eligible_sim_t_s"], x["execution_id"]))
            if action["selection"] == "WAIT_SLOT":
                assert action["original_action"]["name"] == "Wait"
                assert candidates and candidates[0]["execution_id"] == record["execution_id"]
                assert action["selected_action"]["name"] == "AssignCollection"
                assert action["selected_action"]["robot_id"] == binding["runtime_robot_id"]
                assert action["selected_action"]["target_id"] == binding["runtime_zone_id"]
                if start is not None:
                    assert action["sim_t_s"] == start, "WAIT_SLOT only starts a new attempt"
                if action["safety_shield"] == "ACCEPTED":
                    assert start == action["sim_t_s"]
                # A pending start cannot displace any already running lease.
                for other in executions.values():
                    if other["execution_id"] == record["execution_id"]:
                        continue
                    other_start, other_end = other["started_sim_t_s"], other["terminal_sim_t_s"]
                    active = other_start is not None and other_start <= action["sim_t_s"] and (other_end is None or action["sim_t_s"] < other_end)
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
                assert action["safety_shield"] == "ACCEPTED"
                assert start is not None and start < action["sim_t_s"] < deadline
                assert action["selected_action"]["name"] == "SendToHandoff"
                assert action["selected_action"]["robot_id"] == binding["runtime_robot_id"]
                assert action["selected_action"]["target_id"] is None
            if action["selection"] == "POLICY_PREEMPTED":
                assert action["safety_shield"] == "ACCEPTED"
                assert start is not None and start <= action["sim_t_s"] and action["selected_action"]["name"] != "Wait"
                assert action["selected_action"]["robot_id"] == binding["runtime_robot_id"]
            original = action["original_action"]
            if start is not None and action["sim_t_s"] >= start and original["name"] != "Wait" and original["robot_id"] == binding["runtime_robot_id"]:
                terminal_preemption = runtime["collection_exit_reason"] == "POLICY_PREEMPTED" and action["sim_t_s"] == end
                expected = ("ORIGINAL_POLICY_UNCHANGED" if action["safety_shield"] == "REJECTED" or original["name"] == "RequestHumanAssistance" else
                            "ORIGINAL_POLICY_CONVERGED" if original["name"] == "SendToHandoff" and not terminal_preemption else "POLICY_PREEMPTED")
                assert action["selection"] == expected, "leased-robot policy action must classify its effect"
                if action["safety_shield"] == "ACCEPTED" and original["name"] == "RequestHumanAssistance":
                    assert runtime["collection_exit_reason"] == "HUMAN_ASSISTANCE_REQUIRED"
        # Conflict overlays retain the causal runtime exit; they never rewrite
        # a preemption/fault into success or discard its required protection.
        raw, unload = record["raw_quantity"], record["unload_quantity"]
        flags = record["conflicts"]
        true_conflicts = [name for name, enabled in flags.items() if enabled]
        known_identity_conflict = (
            record["state"] == "REJECTED"
            and record["reason"] == "IDENTITY_CONFLICT"
            and record["stage"] == "TERMINAL"
            and start is None
            and deadline is None
            and record["assignment_id"] is None
            and not record["actions"]
            and raw["status"] == unload["status"] == "NOT_REACHED"
            and raw["balls"] is unload["balls"] is None
            and raw["assignment_id"] is unload["assignment_id"] is None
            and raw["source_event_ids"] == unload["source_event_ids"] == []
            and raw["event_digest"] is unload["event_digest"] is None
            and not runtime["start_admitted"]
            and not runtime["assignment_accepted"]
            and not runtime["assignment_terminal"]
            and runtime["collection_exit_reason"] is None
            and runtime["event_sequence_complete"] is True
            and runtime["event_start_sequence"] is None
            and runtime["event_end_sequence"] is None
            and runtime["event_digest"] is None
            and runtime["conservation_passed"] is None
            and runtime["payload_parity_passed"] is None
            and edge["accepted"] is False
            and edge["verified"] is True
            and edge["effective_state"] == "REJECTED"
            and edge["reason"] == "incarnation_mismatch"
            and edge["terminal_states"] == ["REJECTED"]
            and len(edge["event_ids"]) == 1
            and edge["result_verification"] == "VERIFIED"
            and protection == {"protected": True, "reasons": ["INCARNATION_MISMATCH"],
                               "authorization_blocked": True}
            and true_conflicts == ["incarnation_mismatch"]
        )
        identity_conflict_claim = (
            record["reason"] == "IDENTITY_CONFLICT"
            or edge["reason"] == "incarnation_mismatch"
            or flags["incarnation_mismatch"]
            or "INCARNATION_MISMATCH" in protection["reasons"]
        )
        if identity_conflict_claim:
            assert known_identity_conflict, "contradictory pre-acceptance identity conflict"
        conflict_overlay = record["state"] == "INCONCLUSIVE" and (
            (record["reason"] == "TERMINAL_CONFLICT" and flags["terminal_conflict"])
            or (record["reason"] == "REPLAY_MISMATCH" and flags["replay_mismatch"]))
        preempted = any(a["selection"] == "POLICY_PREEMPTED" for a in record["actions"])
        if preempted or record["reason"] == "POLICY_PREEMPTED" or runtime["collection_exit_reason"] == "POLICY_PREEMPTED":
            assert preempted and runtime["collection_exit_reason"] == "POLICY_PREEMPTED"
            assert record["reason"] == "POLICY_PREEMPTED" or conflict_overlay
            assert record["state"] in ("PARTIAL", "FAILED", "INCONCLUSIVE")
        for protected_reason in ("ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED"):
            if record["reason"] == protected_reason or runtime["collection_exit_reason"] == protected_reason:
                assert runtime["collection_exit_reason"] == protected_reason
                assert record["reason"] == protected_reason or conflict_overlay
                assert record["state"] in ("PARTIAL", "FAILED", "INCONCLUSIVE")
                assert protection["protected"] and protection["authorization_blocked"]
                assert protected_reason in protection["reasons"]
        if record["reason"] == "EXECUTION_TIMEOUT" or runtime["collection_exit_reason"] == "EXECUTION_TIMEOUT":
            assert record["reason"] == "EXECUTION_TIMEOUT" or conflict_overlay
            assert runtime["collection_exit_reason"] == "EXECUTION_TIMEOUT"
            assert start is not None and end is not None and end == deadline
            assert record["state"] in ("PARTIAL", "FAILED", "INCONCLUSIVE")
        for quantity, milestone in ((raw, "RAW_COLLECTED_TO_ROBOT"), (unload, "UNLOADED_TO_STATION")):
            assert quantity["milestone"] == milestone
            destination = binding["runtime_robot_id"] if milestone == "RAW_COLLECTED_TO_ROBOT" else binding["handoff_station_id"]
            assert quantity["destination_id"] == destination
            assert quantity["assignment_id"] is None or quantity["assignment_id"] == record["assignment_id"]
            if quantity["status"] == "COMPLETE":
                assert quantity["source_event_ids"] and quantity["event_digest"] is not None
                assert quantity["assignment_id"] == record["assignment_id"]
            else:
                assert quantity["balls"] is None
        if raw["status"] == unload["status"] == "COMPLETE":
            assert unload["balls"] <= raw["balls"]
        assert (runtime["event_start_sequence"] is None) == (runtime["event_end_sequence"] is None)
        if runtime["event_start_sequence"] is not None:
            assert runtime["event_start_sequence"] <= runtime["event_end_sequence"]
            assert runtime["event_digest"] is not None
        assert edge["task_id"] == record["task_id"]
        assert record["success_display_allowed"] == (record["state"] == "SUCCEEDED")
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
            assert start is None
            assert edge["effective_state"] == ("FAILED" if edge["accepted"] else "REJECTED")
        if record["state"] in ("PENDING", "RUNNING"):
            assert not terminals
        if record["state"] == "PENDING":
            assert start is None and edge["effective_state"] == "ACCEPTED"
            assert record["stage"] == "WAITING_FOR_POLICY_SLOT" and edge["accepted"]
            assert data["now_sim_t_s"] < record["latest_start_sim_t_s"]
            assert max(data["now_sim_t_s"], record["eligible_sim_t_s"]) + record["max_execution_s"] <= data["session_end_sim_t_s"]
        if record["state"] == "RUNNING":
            assert start is not None and edge["effective_state"] == "RUNNING"
            assert record["stage"] != "WAITING_FOR_POLICY_SLOT" and edge["accepted"]
            assert data["now_sim_t_s"] < deadline
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
            assert not protection["authorization_blocked"]
            assert edge["effective_state"] == "SUCCEEDED" and edge["verified"] and edge["accepted"]
        elif record["state"] == "PARTIAL":
            assert start is not None and runtime["assignment_terminal"]
            assert edge["effective_state"] == "FAILED" and edge["reason"] == "unknown:partial_execution"
            assert raw["status"] == "COMPLETE" and raw["balls"] > 0
            assert unload["status"] != "INCOMPLETE"
            assert runtime["event_sequence_complete"]
        elif record["state"] == "INCONCLUSIVE":
            assert edge["effective_state"] in ("INCONCLUSIVE", "CONFLICT")
            assert record["device_protection"]["protected"]
        elif record["state"] == "FAILED":
            assert edge["effective_state"] == "FAILED"
            assert raw["status"] != "INCOMPLETE" and unload["status"] != "INCOMPLETE"
            assert runtime["event_sequence_complete"]
            if start is not None:
                assert raw["status"] == "COMPLETE" and raw["balls"] == 0
                assert runtime["assignment_terminal"]
            else:
                assert raw["balls"] is None
        if any(record["conflicts"].values()) and not known_identity_conflict:
            assert record["state"] == "INCONCLUSIVE"
            assert record["device_protection"]["protected"]
        if edge["effective_state"] == "CONFLICT":
            assert record["conflicts"]["terminal_conflict"] or record["conflicts"]["replay_mismatch"]
        if record["reason"] == "POLICY_SLOT_MISSED":
            assert record["state"] == "MISSED" and start is None and end >= request["latest_start_sim_t_s"]
            assert edge["reason"] == "unknown:policy_slot_missed"
        if record["reason"] == "SAFETY_REJECTED":
            assert record["state"] == "REJECTED" and start is None
            assert edge["reason"] == "unknown:safety_rejected"
            assert any(a["safety_shield"] == "REJECTED" for a in record["actions"])
        if record["reason"] == "INSUFFICIENT_SESSION_HORIZON" or edge["reason"] == "unknown:insufficient_session_horizon":
            assert record["reason"] == "INSUFFICIENT_SESSION_HORIZON" and edge["reason"] == "unknown:insufficient_session_horizon"
            assert record["state"] == "MISSED" and start is None and end is not None
            assert not record["actions"] and not runtime["assignment_terminal"] and runtime["collection_exit_reason"] is None
            assert raw["status"] == unload["status"] == "NOT_REACHED"
            assert max(end, record["eligible_sim_t_s"]) + record["max_execution_s"] > data["session_end_sim_t_s"]
        if record["reason"] == "NOT_STARTED_AFTER_RESTART" or edge["reason"] == "not_started_after_restart":
            assert record["reason"] == "NOT_STARTED_AFTER_RESTART" and edge["reason"] == "not_started_after_restart"
            assert record["state"] == "FAILED" and start is None and edge["accepted"]
            assert edge["effective_state"] == "FAILED" and edge["verified"] and edge["result_verification"] == "VERIFIED"
            assert not runtime["assignment_terminal"] and runtime["collection_exit_reason"] is None
            assert raw["status"] == unload["status"] == "NOT_REACHED"
        if record["reason"] == "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME" or edge["reason"] == "interrupted_execution_unknown_outcome":
            assert record["reason"] == "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME" and edge["reason"] == "interrupted_execution_unknown_outcome"
            assert record["state"] == "INCONCLUSIVE" and start is not None and edge["accepted"]
            assert edge["effective_state"] == "INCONCLUSIVE" and not edge["verified"] and edge["result_verification"] == "UNVERIFIED"
            assert protection["protected"] and protection["authorization_blocked"] and "RESTART_UNKNOWN" in protection["reasons"]
            assert raw["status"] == unload["status"] == "INCOMPLETE" and raw["balls"] is None and unload["balls"] is None


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


@pytest.mark.parametrize("name", ["PauseRobot", "SendToHandoff"])
def test_rejected_leased_robot_proposal_has_no_admitted_effect(schema, name):
    data = snapshot()
    record = data["executions"][0]
    record.update(state="RUNNING", stage="COLLECTING", reason=None,
                  terminal_sim_t_s=None, success_display_allowed=False)
    record["actions"] = record["actions"][:1]
    record["edge_evidence"].update(effective_state="RUNNING", terminal_states=[])
    record["runtime_evidence"].update(assignment_terminal=False, collection_exit_reason=None)
    for key in ("raw_quantity", "unload_quantity"):
        record[key].update(status="NOT_REACHED", balls=None, source_event_ids=[], event_digest=None)
    proposal = dict(name=name, index=2, robot_id=record["runtime_robot_id"], target_id=None)
    record["actions"].append(dict(sim_t_s=480, original_action=proposal, selected_action=proposal,
        selection="ORIGINAL_POLICY_UNCHANGED", eligible_pending=[], safety_shield="REJECTED", safety_reason="unsafe"))
    validate_snapshot(schema, data)
    record["actions"][-1]["selection"] = "ORIGINAL_POLICY_CONVERGED" if name == "SendToHandoff" else "POLICY_PREEMPTED"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_accepted_human_assistance_keeps_causal_protection_not_preemption(schema):
    data = snapshot("partial-preempted.json")
    record = data["executions"][0]
    record["reason"] = record["runtime_evidence"]["collection_exit_reason"] = "HUMAN_ASSISTANCE_REQUIRED"
    record["device_protection"].update(protected=True, authorization_blocked=True, reasons=["HUMAN_ASSISTANCE_REQUIRED"])
    action = record["actions"][-1]
    action["selection"] = "ORIGINAL_POLICY_UNCHANGED"
    action["original_action"]["name"] = action["selected_action"]["name"] = "RequestHumanAssistance"
    validate_snapshot(schema, data)
    action["selection"] = "POLICY_PREEMPTED"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_accepted_human_assistance_cannot_claim_success(schema):
    data = snapshot()
    action = data["executions"][0]["actions"][1]
    action["original_action"]["name"] = action["selected_action"]["name"] = "RequestHumanAssistance"
    action["selection"] = "ORIGINAL_POLICY_UNCHANGED"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_early_handoff_is_preemption_only_at_causal_terminal_tick(schema):
    data = snapshot("partial-preempted.json")
    record = data["executions"][0]
    action = record["actions"][-1]
    action["original_action"]["name"] = action["selected_action"]["name"] = "SendToHandoff"
    earlier = copy.deepcopy(action)
    earlier.update(sim_t_s=180, selection="ORIGINAL_POLICY_CONVERGED")
    record["actions"].insert(1, earlier)
    validate_snapshot(schema, data)
    action["selection"] = "ORIGINAL_POLICY_CONVERGED"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)
    action["selection"] = "POLICY_PREEMPTED"
    earlier["selection"] = "POLICY_PREEMPTED"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


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


def unique_terminal_snapshot(second_start=120):
    data = snapshot()
    binding = data["bindings"][0]
    binding.update(task_id="task_" + "a" * 24, plan_id="plan-002",
                   confirmation_id="confirmation-002", schedule_id="schedule-002")
    data["requests"][0]["request_id"] = "request-002"
    data["receipts"][0].update(attempt_id="attempt-002", sequence=2)
    record = data["executions"][0]
    record["assignment_id"] = "assignment-002"
    record["started_sim_t_s"] = second_start
    record["execution_deadline_sim_t_s"] = second_start + record["max_execution_s"]
    record["actions"][0]["sim_t_s"] = second_start
    for field in ("raw_quantity", "unload_quantity"):
        record[field]["assignment_id"] = "assignment-002"
    rekey_single_snapshot(data)
    return data


@pytest.mark.parametrize("second_start", [120, 180])
def test_lease_rejects_overlapping_completed_attempts(schema, second_start):
    first, second = snapshot(), unique_terminal_snapshot(second_start)
    validate_snapshot(schema, first)
    validate_snapshot(schema, second)
    combined = copy.deepcopy(first)
    for key in ("bindings", "requests", "receipts", "executions"):
        combined[key].extend(second[key])
    with pytest.raises(AssertionError, match="lease|running continuation"):
        validate_snapshot(schema, combined)


def test_lease_allows_exact_terminal_to_start_boundary(schema):
    first, second = snapshot(), unique_terminal_snapshot(180)
    # Both complete within their admitted windows; equality releases the lease.
    first_record = first["executions"][0]
    first_record["terminal_sim_t_s"] = 180
    first_record["actions"][1]["sim_t_s"] = 180
    validate_snapshot(schema, first)
    validate_snapshot(schema, second)
    combined = copy.deepcopy(first)
    for key in ("bindings", "requests", "receipts", "executions"):
        combined[key].extend(second[key])
    validate_snapshot(schema, combined)


def test_lease_rejects_distinct_zero_duration_failed_attempts_at_same_start(schema):
    first, second = snapshot(), unique_terminal_snapshot()
    for data in (first, second):
        record = data["executions"][0]
        record.update(state="FAILED", reason="ZONE_EMPTY", terminal_sim_t_s=120,
                      success_display_allowed=False)
        record["actions"] = record["actions"][:1]
        record["runtime_evidence"]["collection_exit_reason"] = "ZONE_EMPTY"
        record["raw_quantity"]["balls"] = 0
        record["unload_quantity"].update(status="NOT_REACHED", balls=None,
                                         source_event_ids=[], event_digest=None)
        record["edge_evidence"].update(effective_state="FAILED", terminal_states=["FAILED"],
                                      reason="unknown:zone_empty")
        # Each complete-zero terminal is valid alone, including all independent
        # task/request/receipt/binding/assignment links and canonical hashes.
        validate_snapshot(schema, data)
    combined = copy.deepcopy(first)
    for key in ("bindings", "requests", "receipts", "executions"):
        combined[key].extend(second[key])
    with pytest.raises(AssertionError, match="lease"):
        validate_snapshot(schema, combined)


def parity_case(kind):
    """Construct valid read evidence; this never runs or emulates an executor."""
    if kind == "pending":
        data = snapshot("policy-missed.json")
        data.update(now_sim_t_s=120, simulation_time_utc="2026-09-16T00:02:00Z")
        record = data["executions"][0]
        record.update(state="PENDING", stage="WAITING_FOR_POLICY_SLOT", reason=None,
                      terminal_sim_t_s=None, actions=[])
        record["edge_evidence"].update(effective_state="ACCEPTED", reason=None, terminal_states=[])
        return data
    if kind == "running":
        return active_snapshot()
    if kind == "restart-before":
        data = snapshot("policy-missed.json")
        data["executions"][0].update(state="FAILED", reason="NOT_STARTED_AFTER_RESTART")
        data["executions"][0]["edge_evidence"]["reason"] = "not_started_after_restart"
        return data
    if kind == "failed":
        data = snapshot("partial-preempted.json")
        data["executions"][0]["state"] = "FAILED"
        data["executions"][0]["raw_quantity"]["balls"] = 0
        data["executions"][0]["edge_evidence"]["reason"] = "unknown:policy_preempted"
        return data
    if kind in ("ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED"):
        data = snapshot("partial-preempted.json")
        record = data["executions"][0]
        record["actions"].pop()
        record["reason"] = record["runtime_evidence"]["collection_exit_reason"] = kind
        record["device_protection"] = {"protected": True, "authorization_blocked": True, "reasons": [kind]}
        return data
    return snapshot(kind + ".json")


def preacceptance_identity_conflict_case():
    data = parity_case("pending")
    record = data["executions"][0]
    record.update(
        state="REJECTED",
        stage="TERMINAL",
        reason="IDENTITY_CONFLICT",
        terminal_sim_t_s=60,
        assignment_id=None,
        started_sim_t_s=None,
        execution_deadline_sim_t_s=None,
        actions=[],
        success_display_allowed=False,
    )
    record["edge_evidence"] = {
        "task_id": record["task_id"],
        "accepted": False,
        "verified": True,
        "effective_state": "REJECTED",
        "reason": "incarnation_mismatch",
        "terminal_states": ["REJECTED"],
        "event_ids": ["device-record-preacceptance"],
        "result_verification": "VERIFIED",
    }
    record["device_protection"] = {
        "protected": True,
        "reasons": ["INCARNATION_MISMATCH"],
        "authorization_blocked": True,
    }
    record["conflicts"]["incarnation_mismatch"] = True
    return data


def change_fields(value, changes):
    for path, replacement in changes.items():
        parts = path.split(".")
        target = value
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        if isinstance(target, list):
            target[int(parts[-1])] = replacement
        else:
            target[parts[-1]] = replacement


def test_preacceptance_identity_conflict_accepts_exact_verified_rejection(schema):
    data = preacceptance_identity_conflict_case()
    validate_snapshot(schema, data)
    record = data["executions"][0]
    assert (record["state"], record["reason"]) == ("REJECTED", "IDENTITY_CONFLICT")
    assert record["conflicts"]["incarnation_mismatch"] is True


@pytest.mark.parametrize("changes", [
    {"state": "INCONCLUSIVE"},
    {"started_sim_t_s": 60},
    {"assignment_id": "assignment-illegal"},
    {"conflicts.terminal_conflict": True},
    {"runtime_evidence.event_sequence_complete": False},
    {"raw_quantity.source_event_ids": ["runtime-evidence"],
     "raw_quantity.event_digest": "a" * 64},
])
def test_preacceptance_identity_conflict_rejects_contradictory_evidence(schema, changes):
    data = preacceptance_identity_conflict_case()
    change_fields(data["executions"][0], changes)
    with pytest.raises((AssertionError, ValidationError)):
        validate_snapshot(schema, data)


@pytest.mark.parametrize(("kind", "changes"), [
    ("failed", {"raw_quantity.balls": 12}),
    ("failed", {"raw_quantity.status": "INCOMPLETE", "raw_quantity.balls": None}),
    ("failed", {"raw_quantity.status": "NOT_REACHED", "raw_quantity.balls": None}),
    ("failed", {"unload_quantity.status": "INCOMPLETE"}),
    ("failed", {"runtime_evidence.event_sequence_complete": False}),
    ("partial-preempted", {"runtime_evidence.start_admitted": False}),
    ("partial-preempted", {"runtime_evidence.assignment_terminal": False}),
    ("policy-missed", {"edge_evidence.reason": "unknown:wrong"}),
    ("safety-rejected", {"edge_evidence.reason": "unknown:wrong"}),
    ("safety-rejected", {"actions": []}),
    ("safety-rejected", {"actions.0.safety_shield": "ACCEPTED", "actions.0.safety_reason": None}),
    ("partial-preempted", {"unload_quantity.assignment_id": "wrong-assignment"}),
    ("restart-unknown", {"raw_quantity.assignment_id": "wrong-assignment"}),
    ("partial-preempted", {"runtime_evidence.event_start_sequence": None}),
    ("partial-preempted", {"runtime_evidence.event_end_sequence": None}),
    ("partial-preempted", {"runtime_evidence.event_start_sequence": 10}),
    ("partial-preempted", {"runtime_evidence.event_digest": None}),
    ("partial-preempted", {"actions.1.sim_t_s": 300}),
    ("success", {"device_protection.authorization_blocked": True}),
    ("pending", {"runtime_evidence.start_admitted": True}),
    ("pending", {"edge_evidence.accepted": False}),
    ("pending", {"stage": "COLLECTING"}),
    ("running", {"stage": "WAITING_FOR_POLICY_SLOT"}),
    ("success", {"actions.0.original_action.robot_id": "R1"}),
    ("success", {"actions.0.safety_reason": "unexpected rejection detail"}),
    ("partial-preempted", {"actions.1.selected_action.robot_id": "R2", "actions.1.original_action.robot_id": "R2"}),
])
def test_parity_rejects_contradictory_evidence(schema, kind, changes):
    data = parity_case(kind)
    validate_snapshot(schema, data)
    change_fields(data["executions"][0], changes)
    with pytest.raises((AssertionError, ValidationError)):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("kind", ["pending", "running", "restart-before", "failed",
                                  "ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED"])
def test_parity_accepts_valid_control_cases(schema, kind):
    validate_snapshot(schema, parity_case(kind))


@pytest.mark.parametrize(("kind", "now", "valid"), [
    ("pending", 299, True), ("pending", 300, False), ("pending", 301, False),
    ("running", 779, True), ("running", 780, False), ("running", 781, False),
])
def test_parity_nonterminal_exclusive_time_boundaries(schema, kind, now, valid):
    data = parity_case(kind)
    data["now_sim_t_s"] = now
    epoch = datetime.fromisoformat(data["session_epoch_utc"].replace("Z", "+00:00"))
    data["simulation_time_utc"] = (epoch + timedelta(seconds=now)).isoformat().replace("+00:00", "Z")
    if valid:
        validate_snapshot(schema, data)
    else:
        with pytest.raises(AssertionError):
            validate_snapshot(schema, data)


@pytest.mark.parametrize("kind", ["pending", "running"])
def test_parity_nonterminal_ended_rejected_but_paused_valid(schema, kind):
    data = parity_case(kind)
    data["session_state"] = "PAUSED"
    validate_snapshot(schema, data)
    data["session_state"] = "ENDED"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("future_eligible", [False, True])
def test_parity_pending_requires_possible_execution_horizon(schema, future_eligible):
    data = parity_case("pending")
    horizon = 900 if future_eligible else 780
    data["session_end_sim_t_s"] = data["bindings"][0]["session_end_sim_t_s"] = horizon
    if future_eligible:
        request, record = data["requests"][0], data["executions"][0]
        request.update(eligible_sim_t_s=240, latest_start_sim_t_s=360,
                       due_at_utc="2026-09-16T00:04:00Z", expires_at_utc="2026-09-16T00:06:00Z")
        record.update(eligible_sim_t_s=240, latest_start_sim_t_s=360)
    rekey_single_snapshot(data)
    validate_snapshot(schema, data)
    data["session_end_sim_t_s"] = data["bindings"][0]["session_end_sim_t_s"] = horizon - 1
    rekey_single_snapshot(data)
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize(("kind", "changes"), [
    ("restart-before", {"reason": "ZONE_EMPTY"}),
    ("restart-before", {"edge_evidence.reason": "unknown:other"}),
    ("restart-before", {"edge_evidence.accepted": False}),
    ("restart-before", {"raw_quantity.status": "INCOMPLETE"}),
    ("restart-before", {"runtime_evidence.assignment_terminal": True}),
    ("restart-before", {"runtime_evidence.collection_exit_reason": "ZONE_EMPTY"}),
    ("restart-unknown", {"reason": "EVIDENCE_INCOMPLETE"}),
    ("restart-unknown", {"edge_evidence.reason": "unknown:other"}),
    ("restart-unknown", {"edge_evidence.accepted": False}),
    ("restart-unknown", {"device_protection.reasons": ["ORPHANED_ACTIVITY"]}),
    ("restart-unknown", {"raw_quantity.status": "NOT_REACHED"}),
    ("restart-unknown", {"unload_quantity.status": "NOT_REACHED"}),
])
def test_parity_restart_claims_are_bidirectional(schema, kind, changes):
    data = parity_case(kind)
    change_fields(data["executions"][0], changes)
    with pytest.raises((AssertionError, ValidationError)):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("reason", ["ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED"])
@pytest.mark.parametrize("mutation", ["unprotected", "wrong-protection", "unblocked", "wrong-reason", "wrong-exit"])
def test_parity_protected_exits_retain_cause_and_protection(schema, reason, mutation):
    data = parity_case(reason)
    record = data["executions"][0]
    if mutation == "unprotected":
        record["device_protection"] = {"protected": False, "authorization_blocked": False, "reasons": []}
    elif mutation == "wrong-protection":
        record["device_protection"]["reasons"] = ["ORPHANED_ACTIVITY"]
    elif mutation == "unblocked":
        record["device_protection"]["authorization_blocked"] = False
    elif mutation == "wrong-reason":
        record["reason"] = "ZONE_EMPTY"
    else:
        record["runtime_evidence"]["collection_exit_reason"] = "ZONE_EMPTY"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("mutation", ["reason", "exit", "no-action", "isolated-reason", "isolated-exit"])
def test_parity_preemption_claims_are_bidirectional(schema, mutation):
    data = parity_case("partial-preempted")
    record = data["executions"][0]
    if mutation in ("no-action", "isolated-reason", "isolated-exit"):
        record["actions"].pop()
    if mutation == "reason":
        record["reason"] = "ZONE_EMPTY"
    if mutation == "exit":
        record["runtime_evidence"]["collection_exit_reason"] = "ZONE_EMPTY"
    if mutation == "isolated-reason":
        record["runtime_evidence"]["collection_exit_reason"] = "ZONE_EMPTY"
    if mutation == "isolated-exit":
        record["reason"] = "ZONE_EMPTY"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("selection", ["POLICY_PREEMPTED", "ORIGINAL_POLICY_UNCHANGED"])
def test_parity_success_cannot_hide_a_same_robot_preemption(schema, selection):
    data = snapshot()
    action = snapshot("partial-preempted.json")["executions"][0]["actions"][1]
    action["selection"] = selection
    data["executions"][0]["actions"][1] = action
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_parity_preemption_must_occur_during_its_lease(schema):
    data = snapshot("partial-preempted.json")
    record = data["executions"][0]
    preemption = record["actions"].pop()
    preemption["sim_t_s"] = 60
    record["actions"].insert(0, preemption)
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("state", ["PARTIAL", "FAILED", "INCONCLUSIVE"])
def test_parity_preemption_preserves_quantity_classification(schema, state):
    data = parity_case("failed" if state == "FAILED" else "partial-preempted")
    record = data["executions"][0]
    if state == "INCONCLUSIVE":
        record["state"] = state
        record["raw_quantity"].update(status="INCOMPLETE", balls=None)
        record["runtime_evidence"]["event_sequence_complete"] = False
        record["conflicts"]["missing_events"] = True
        record["edge_evidence"].update(effective_state=state, terminal_states=[state],
                                      reason="unknown:policy_preempted", verified=False, result_verification="UNVERIFIED")
        record["device_protection"] = {"protected": True, "authorization_blocked": True, "reasons": ["ORPHANED_ACTIVITY"]}
    validate_snapshot(schema, data)


@pytest.mark.parametrize("reason", ["POLICY_PREEMPTED", "ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED"])
@pytest.mark.parametrize("overlay", ["TERMINAL_CONFLICT", "REPLAY_MISMATCH"])
def test_parity_conflict_overlay_preserves_causal_exit(schema, reason, overlay):
    data = parity_case("partial-preempted" if reason == "POLICY_PREEMPTED" else reason)
    record = data["executions"][0]
    record.update(state="INCONCLUSIVE", reason=overlay)
    record["edge_evidence"].update(effective_state="CONFLICT", reason="unknown:conflict",
                                  terminal_states=["FAILED", "SUCCEEDED"], verified=False, result_verification="CONFLICT")
    record["conflicts"]["terminal_conflict"] = True
    record["conflicts"]["replay_mismatch"] = overlay == "REPLAY_MISMATCH"
    reasons = ["TERMINAL_CONFLICT"] + ([] if reason == "POLICY_PREEMPTED" else [reason])
    record["device_protection"] = {"protected": True, "authorization_blocked": True, "reasons": reasons}
    validate_snapshot(schema, data)


@pytest.mark.parametrize("asset", ["schema", "example"])
@pytest.mark.parametrize("source", [
    '{"x": 1, "x": 2}', r'{"x": 1, "\u0078": 2}',
    r'{"outer": [{"key": 1, "k\u0065y": 2}]}',
    '{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}', '{"x": 1e999}',
])
def test_parity_asset_loaders_reject_ambiguous_json(tmp_path, monkeypatch, asset, source):
    monkeypatch.setattr(__import__(__name__, fromlist=["CONTRACT"]), "CONTRACT", tmp_path)
    if asset == "schema":
        (tmp_path / "schema.json").write_text(source, encoding="utf-8")
        load = schema.__wrapped__
    else:
        (tmp_path / "examples").mkdir()
        (tmp_path / "examples" / "probe.json").write_text(source, encoding="utf-8")
        load = lambda: example("probe.json")
    with pytest.raises(ValueError):
        load()


@pytest.mark.parametrize("asset", ["schema", "example"])
def test_parity_asset_loaders_preserve_separate_object_keys(tmp_path, monkeypatch, asset):
    monkeypatch.setattr(__import__(__name__, fromlist=["CONTRACT"]), "CONTRACT", tmp_path)
    source = '{"objects": [{"same": 1}, {"same": 2}], "same": 3}'
    if asset == "schema":
        (tmp_path / "schema.json").write_text(source, encoding="utf-8")
        value = schema.__wrapped__()
    else:
        (tmp_path / "examples").mkdir()
        (tmp_path / "examples" / "probe.json").write_text(source, encoding="utf-8")
        value = example("probe.json")
    assert value == {"objects": [{"same": 1}, {"same": 2}], "same": 3}


def timeout_case(state="PARTIAL"):
    data = snapshot("partial-preempted.json")
    data.update(now_sim_t_s=900, simulation_time_utc="2026-09-16T00:15:00Z")
    record = data["executions"][0]
    record["actions"].pop()
    record.update(state=state, reason="EXECUTION_TIMEOUT", terminal_sim_t_s=780)
    record["runtime_evidence"]["collection_exit_reason"] = "EXECUTION_TIMEOUT"
    if state == "FAILED":
        record["raw_quantity"]["balls"] = 0
        record["edge_evidence"]["reason"] = "unknown:execution_timeout"
    if state == "INCONCLUSIVE":
        record["raw_quantity"].update(status="INCOMPLETE", balls=None, source_event_ids=[], event_digest=None)
        record["edge_evidence"].update(effective_state=state, terminal_states=[state],
                                      verified=False, result_verification="UNVERIFIED", reason="unknown:execution_timeout")
        record["runtime_evidence"].update(event_sequence_complete=False, assignment_terminal=False)
        record["conflicts"]["missing_events"] = True
        record["device_protection"] = {"protected": True, "authorization_blocked": True, "reasons": ["ORPHANED_ACTIVITY"]}
    return data


def horizon_case(accepted=True):
    data = snapshot("policy-missed.json")
    data["session_end_sim_t_s"] = data["bindings"][0]["session_end_sim_t_s"] = 779
    record = data["executions"][0]
    record.update(reason="INSUFFICIENT_SESSION_HORIZON", terminal_sim_t_s=120, actions=[])
    state = "FAILED" if accepted else "REJECTED"
    record["edge_evidence"].update(accepted=accepted, reason="unknown:insufficient_session_horizon",
                                  effective_state=state, terminal_states=[state])
    rekey_single_snapshot(data)
    return data


@pytest.mark.parametrize("state", ["PARTIAL", "FAILED", "INCONCLUSIVE"])
def test_terminal_limits_timeout_controls_retain_admission(schema, state):
    data = timeout_case(state)
    validate_snapshot(schema, data)
    data["executions"][0]["edge_evidence"]["accepted"] = False
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_terminal_limits_started_failed_requires_assignment_terminal(schema):
    data = timeout_case("FAILED")
    data["executions"][0]["runtime_evidence"]["assignment_terminal"] = False
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_terminal_limits_conflict_retains_admission(schema):
    data = snapshot("terminal-conflict.json")
    data["executions"][0]["edge_evidence"]["accepted"] = False
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("kind", ["restart-before", "restart-unknown"])
def test_terminal_limits_restart_does_not_fabricate_assignment_terminal(schema, kind):
    data = parity_case(kind)
    assert not data["executions"][0]["runtime_evidence"]["assignment_terminal"]
    validate_snapshot(schema, data)


@pytest.mark.parametrize("changes", [
    {"reason": "ZONE_EMPTY"}, {"runtime_evidence.collection_exit_reason": "ZONE_EMPTY"},
    {"runtime_evidence.collection_exit_reason": None}, {"terminal_sim_t_s": 779},
    {"terminal_sim_t_s": 781}, {"reason": "TERMINAL_CONFLICT"}, {"reason": "REPLAY_MISMATCH"},
])
def test_terminal_limits_timeout_rejects_contradictions(schema, changes):
    data = timeout_case()
    change_fields(data["executions"][0], changes)
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize(("state", "balls"), [("PARTIAL", 0), ("FAILED", 12)])
def test_terminal_limits_timeout_preserves_quantity_rules(schema, state, balls):
    data = timeout_case(state)
    data["executions"][0]["raw_quantity"]["balls"] = balls
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("kind", ["success", "running", "policy-missed"])
def test_terminal_limits_timeout_requires_started_terminal_non_success(schema, kind):
    data = parity_case(kind)
    record = data["executions"][0]
    record["runtime_evidence"]["collection_exit_reason"] = "EXECUTION_TIMEOUT"
    if kind == "policy-missed":
        record["reason"] = "EXECUTION_TIMEOUT"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("overlay", ["TERMINAL_CONFLICT", "REPLAY_MISMATCH"])
def test_terminal_limits_timeout_conflict_overlay_keeps_exact_deadline(schema, overlay):
    data = timeout_case()
    record = data["executions"][0]
    record.update(state="INCONCLUSIVE", reason=overlay)
    terminal = overlay == "TERMINAL_CONFLICT"
    record["conflicts"].update(terminal_conflict=terminal, replay_mismatch=not terminal)
    record["edge_evidence"].update(effective_state="CONFLICT", verified=False, result_verification="CONFLICT",
                                  terminal_states=["SUCCEEDED", "FAILED"] if terminal else ["FAILED"])
    record["device_protection"] = {"protected": True, "authorization_blocked": True,
                                   "reasons": ["TERMINAL_CONFLICT" if terminal else "ORPHANED_ACTIVITY"]}
    validate_snapshot(schema, data)
    record["terminal_sim_t_s"] = 779
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("accepted", [True, False])
def test_terminal_limits_horizon_controls(schema, accepted):
    validate_snapshot(schema, horizon_case(accepted))


@pytest.mark.parametrize("end", [780, 900])
def test_terminal_limits_horizon_rejects_sufficient_window(schema, end):
    data = horizon_case()
    data["session_end_sim_t_s"] = data["bindings"][0]["session_end_sim_t_s"] = end
    rekey_single_snapshot(data)
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_terminal_limits_horizon_uses_future_eligibility(schema):
    data = horizon_case()
    data["session_end_sim_t_s"] = data["bindings"][0]["session_end_sim_t_s"] = 899
    data["requests"][0].update(eligible_sim_t_s=240, due_at_utc="2026-09-16T00:04:00Z")
    data["executions"][0]["eligible_sim_t_s"] = 240
    rekey_single_snapshot(data)
    validate_snapshot(schema, data)
    data["session_end_sim_t_s"] = data["bindings"][0]["session_end_sim_t_s"] = 900
    rekey_single_snapshot(data)
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


@pytest.mark.parametrize("changes", [
    {"state": "FAILED"}, {"runtime_evidence.assignment_terminal": True},
    {"runtime_evidence.collection_exit_reason": "ZONE_EMPTY"}, {"edge_evidence.reason": "unknown:other"},
    {"reason": "ZONE_EMPTY"}, {"raw_quantity.status": "INCOMPLETE"}, {"unload_quantity.status": "INCOMPLETE"},
])
def test_terminal_limits_horizon_rejects_contradictions(schema, changes):
    data = horizon_case()
    change_fields(data["executions"][0], changes)
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_terminal_limits_horizon_rejects_policy_action(schema):
    data = horizon_case()
    data["executions"][0]["actions"] = snapshot("policy-missed.json")["executions"][0]["actions"][:1]
    rekey_single_snapshot(data)
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)


def test_terminal_limits_horizon_cannot_relabel_started_partial(schema):
    data = timeout_case()
    record = data["executions"][0]
    record["reason"] = "INSUFFICIENT_SESSION_HORIZON"
    record["runtime_evidence"]["collection_exit_reason"] = None
    record["edge_evidence"]["reason"] = "unknown:insufficient_session_horizon"
    with pytest.raises(AssertionError):
        validate_snapshot(schema, data)
