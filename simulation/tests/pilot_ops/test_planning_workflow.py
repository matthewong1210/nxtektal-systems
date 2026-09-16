"""Independent workflow coverage: immutable evidence, replay and human intent."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest

from nxt_pilot_ops.planning_contracts import PlanningError, utc, utc_text
from nxt_pilot_ops.planning_workflow import (
    CONFIRMATION, INPUT, OUTCOME, PLAN, PlanningHistory, confirmation_identity,
    existing_confirmation, planning_snapshot, prepare_confirmation, prepare_input,
    prepare_outcome, prepare_plan, replay_planning, require_current, schedule_operator,
)
from nxt_pilot_ops.serialization import canonical_json
from tests.pilot_ops.test_planning import CONTEXT, NOW, input_request, plan_request
from tests.pilot_ops.test_planning_wire_contract import validator

CLOCK = utc(NOW)


def appended(entries, kind, result):
    assert result["disposition"] == "created"
    return [*deepcopy(entries), {"kind": kind, "record": deepcopy(result["record"])}]


def history(entries):
    return replay_planning(entries, CONTEXT)


def initial():
    result = prepare_input(PlanningHistory(()), input_request(), CONTEXT, CLOCK)
    return appended([], INPUT, result)


def planned(entries=None):
    entries = initial() if entries is None else entries
    request = plan_request(selection={"zone_id": "A", "robot_id": "picker-01",
                                      "start_at_utc": "2026-09-16T01:02:00Z"})
    result = prepare_plan(history(entries), request, CLOCK)
    assert result["record"]["status"] == "READY"
    return appended(entries, PLAN, result)


def confirm_payload(plan, request_id="confirm-first", operator="经理 李"):
    return {"schema": "nxt-planning-confirmation/v1", "request_id": request_id,
            "plan_id": plan["plan_id"], "plan_version": plan["version"], "operator": operator}


def schedule_for(plan, request):
    chosen = next(c for c in plan["candidates"]
                  if c["zone_id"] == plan["selection"]["zone_id"]
                  and c["robot_id"] == plan["selection"]["robot_id"])
    expiry = min(utc(plan["valid_until_utc"]), utc(chosen["latest_start_at_utc"]))
    return {"robot_id": plan["selection"]["robot_id"], "zone_id": plan["selection"]["zone_id"],
            "due_at_utc": plan["selection"]["start_at_utc"], "expires_at_utc": utc_text(expiry),
            "operator": schedule_operator(request["operator"]),
            "admission_reference": confirmation_identity(plan)}


def confirmed(entries=None):
    entries = planned() if entries is None else entries
    plan = history(entries).records(PLAN)[-1]
    request = confirm_payload(plan)
    result = prepare_confirmation(history(entries), request, CLOCK,
                                  schedule=schedule_for(plan, request), schedule_id="schedule_test_001")
    return appended(entries, CONFIRMATION, result)


def outcome_payload(confirmation, stage="COLLECTED", request_id="result-first", **changes):
    return {"schema": "nxt-planning-outcome/v1", "request_id": request_id,
            "confirmation_id": confirmation["confirmation_id"], "task_id": "task-test-001",
            "operator": "现场操作员", "reason": "Recorded separately after this operation",
            "stage": stage, "quantity_balls": 1, "started_at_utc": "2026-09-16T01:03:00Z",
            "completed_at_utc": "2026-09-16T01:04:00Z", "source_kind": "MEASURED",
            "source_ref": "shift-ledger-page-1", "supersedes_outcome_id": None, **changes}


def prepare_result(entries, payload):
    return prepare_outcome(history(entries), payload, CLOCK + timedelta(minutes=10),
                           admitted_task_id="task-test-001", task_created_at_utc="2026-09-16T01:02:00Z")


def assert_code(code, function, *args, **kwargs):
    with pytest.raises(PlanningError) as exc:
        function(*args, **kwargs)
    assert exc.value.code == code


def test_input_cas_preserves_original_values_attribution_and_missingness():
    entries = initial()
    original = canonical_json(entries)
    correction = input_request()
    correction.update(request_id="correct-opening", expected_revision=1,
                      operator="Another operator", reason="Correct the opening count")
    correction["inventory_clean_balls"]["value"] = 90
    correction["washer_available"] = None
    result = prepare_input(history(entries), correction, CONTEXT, CLOCK)
    assert canonical_json(entries) == original
    assert result["record"]["revision"] == 2
    assert result["record"]["operator"] == "Another operator"
    changes = {c["path"]: c for c in result["record"]["changes"]}
    assert changes["/inventory_clean_balls/value"] == {
        "path": "/inventory_clean_balls/value", "before": 100, "after": 90,
    }
    assert changes["/washer_available"]["before"]["value"] is True
    assert changes["/washer_available"]["after"] is None
    updated = appended(entries, INPUT, result)
    assert history(updated).latest_input["revision"] == 2
    assert history(updated).records(INPUT)[0]["inventory_clean_balls"]["value"] == 100
    assert_code("planning_conflict", prepare_input, history(updated),
                {**correction, "request_id": "stale-write"}, CONTEXT, CLOCK)


def test_request_replay_precedes_cas_and_expiry_but_changed_content_conflicts():
    entries = initial()
    correction = {**input_request(), "request_id": "second-input", "expected_revision": 1}
    entries = appended(entries, INPUT, prepare_input(history(entries), correction, CONTEXT, CLOCK))
    result = prepare_input(history(entries), input_request(), CONTEXT, CLOCK + timedelta(days=1))
    assert result["disposition"] == "duplicate" and result["record"]["revision"] == 1
    assert len(history(entries).records(INPUT)) == 2
    assert_code("planning_conflict", prepare_input, history(entries),
                {**input_request(), "reason": "Different content"}, CONTEXT, CLOCK)
    assert_code("planning_conflict", prepare_plan, history(entries),
                plan_request(request_id="opening", input_revision=2), CLOCK)


def test_retry_does_not_conflate_json_boolean_and_numeric_values():
    entries = initial()
    malformed = {**input_request(), "expected_revision": False}
    assert_code("planning_conflict", prepare_input, history(entries), malformed, CONTEXT, CLOCK)


def test_plan_revisions_and_restore_append_history_and_recalculate_impact():
    entries = planned()
    first = history(entries).records(PLAN)[0]
    delayed = {"zone_id": "A", "robot_id": "picker-01", "start_at_utc": "2026-09-16T01:12:00Z"}
    second_request = plan_request(request_id="delay", plan_id=first["plan_id"],
                                  expected_plan_version=1, selection=delayed,
                                  operator="Field operator", reason="Access blocked")
    second_result = prepare_plan(history(entries), second_request, CLOCK)
    second = second_result["record"]
    assert second["status"] == "INFEASIBLE" and second["version"] == 2
    assert second["adjustment"]["before"] == first["selection"]
    assert second["adjustment"]["after"] == delayed
    entries = appended(entries, PLAN, second_result)
    restore = plan_request(request_id="restore", plan_id=first["plan_id"], expected_plan_version=2,
                           selection=None, operator="Manager", reason="Restore system timing")
    restored = prepare_plan(history(entries), restore, CLOCK)
    assert restored["record"]["status"] == "READY"
    assert restored["record"]["adjustment"]["before"] == delayed
    assert restored["record"]["selection"] == restored["record"]["system_selection"]
    entries = appended(entries, PLAN, restored)
    rows = history(entries).records(PLAN)
    assert [r["version"] for r in rows] == [1, 2, 3]
    assert rows[0] == first and rows[1] == second
    assert_code("planning_conflict", prepare_plan, history(entries),
                {**restore, "request_id": "stale-plan-write"}, CLOCK)
    assert prepare_plan(history(entries), second_request, CLOCK)["disposition"] == "duplicate"


def test_snapshot_invalidates_old_inputs_and_versions_without_mutating_history():
    entries = planned()
    first = history(entries).records(PLAN)[0]
    revised = prepare_plan(history(entries), plan_request(request_id="revision-2", plan_id=first["plan_id"],
                           expected_plan_version=1), CLOCK)
    entries = appended(entries, PLAN, revised)
    frozen = canonical_json(entries)
    snapshot = planning_snapshot(history(entries), CONTEXT, CLOCK)
    assert [p["current_status"] for p in snapshot["plans"]] == ["INVALIDATED", "READY"]
    snapshot["plans"][1]["selection"]["zone_id"] = "tampered-projection"
    assert canonical_json(entries) == frozen
    changed = {**input_request(), "request_id": "input-2", "expected_revision": 1}
    changed["zones"][0]["collection_allowed"]["value"] = False
    entries = appended(entries, INPUT, prepare_input(history(entries), changed, CONTEXT, CLOCK))
    assert all(p["current_status"] == "INVALIDATED" for p in planning_snapshot(history(entries), CONTEXT, CLOCK)["plans"])
    assert_code("planning_conflict", require_current, history(entries), first, CLOCK)


def test_snapshot_expiry_is_exclusive_and_does_not_rewrite_saved_calculation():
    entries = planned()
    saved = history(entries).records(PLAN)[0]
    boundary = utc(saved["candidates"][0]["latest_start_at_utc"])
    current = planning_snapshot(history(entries), CONTEXT, boundary - timedelta(microseconds=1))
    expired = planning_snapshot(history(entries), CONTEXT, boundary)
    assert current["plans"][0]["current_status"] == "READY"
    assert expired["plans"][0]["current_status"] == "EXPIRED"
    assert expired["plans"][0]["status"] == "READY"
    assert history(entries).records(PLAN)[0] == saved


def test_confirmation_duplicates_return_original_request_and_never_new_intent():
    entries = confirmed()
    state = history(entries)
    row = state.records(CONFIRMATION)[0]
    exact = existing_confirmation(state, row["request"])
    alternate = existing_confirmation(state, {**row["request"], "request_id": "different-confirm-id", "operator": "Other manager"})
    assert exact == alternate
    assert alternate["disposition"] == "duplicate"
    assert alternate["request_id"] == "confirm-first"
    assert alternate["record"] == row
    assert state.by_request("different-confirm-id") is None
    assert_code("planning_conflict", existing_confirmation, state,
                {**row["request"], "operator": "Changed under same ID"})
    assert_code("planning_conflict", existing_confirmation, state,
                {**row["request"], "request_id": "version-2-confirm", "plan_version": 2})
    assert row["schedule"]["operator"] != row["request"]["operator"]
    assert row["schedule"]["operator"].isascii()
    assert len(row["schedule"]["operator"]) <= 64


def test_confirmation_consumes_plan_id_and_remains_historical_after_input_change():
    entries = confirmed()
    plan = history(entries).records(PLAN)[0]
    assert_code("planning_conflict", prepare_plan, history(entries),
                plan_request(request_id="revise-confirmed", plan_id=plan["plan_id"], expected_plan_version=1), CLOCK)
    original_request = plan["request"]
    assert prepare_plan(history(entries), original_request, CLOCK)["disposition"] == "duplicate"
    correction = {**input_request(), "request_id": "new-input", "expected_revision": 1}
    entries = appended(entries, INPUT, prepare_input(history(entries), correction, CONTEXT, CLOCK))
    snapshot = planning_snapshot(history(entries), CONTEXT, CLOCK + timedelta(days=1))
    assert snapshot["plans"][0]["current_status"] == "CONFIRMED"
    assert snapshot["plans"][0]["input_revision"] == 1
    assert snapshot["latest_input"]["revision"] == 2
    request = snapshot["confirmations"][0]["request"]
    assert existing_confirmation(history(entries), request)["disposition"] == "duplicate"


@pytest.mark.parametrize("change", [
    {"robot_id": "another-robot"}, {"zone_id": "another-zone"},
    {"due_at_utc": "2026-09-16T01:03:00Z"}, {"admission_reference": "wrong-confirmation"},
    {"operator": "manager-name-is-not-the-token"},
])
def test_confirmation_rejects_mismatched_frozen_schedule(change):
    entries = planned()
    plan = history(entries).records(PLAN)[0]
    request = confirm_payload(plan)
    assert_code("planning_conflict", prepare_confirmation, history(entries), request, CLOCK,
                schedule={**schedule_for(plan, request), **change}, schedule_id="schedule_test_001")


@pytest.mark.parametrize("expiry", ["2026-09-16T01:30:00Z", "2026-09-16T01:05:19Z"])
def test_confirmation_requires_the_exact_dispatch_window(expiry):
    entries = planned()
    plan = history(entries).records(PLAN)[0]
    request = confirm_payload(plan)
    schedule = {**schedule_for(plan, request), "expires_at_utc": expiry}
    assert_code("planning_conflict", prepare_confirmation, history(entries), request, CLOCK,
                schedule=schedule, schedule_id="schedule_test_001")


def test_outcome_stages_are_independent_and_do_not_mutate_inventory_or_plan():
    entries = confirmed()
    before = history(entries)
    confirmation = before.records(CONFIRMATION)[0]
    assert planning_snapshot(before, CONTEXT, CLOCK)["outcomes"] == []
    collected = outcome_payload(confirmation)
    result = prepare_result(entries, collected)
    entries = appended(entries, OUTCOME, result)
    assert [r["request"]["stage"] for r in history(entries).records(OUTCOME)] == ["COLLECTED"]
    for stage, quantity, quality in [("UNLOADED", 3, "MANUAL_ESTIMATE"), ("WASHED", 2, "MEASURED"), ("SUPPLIED", 0, "MANUAL_ESTIMATE")]:
        request = outcome_payload(confirmation, stage, "result-" + stage,
                                  quantity_balls=quantity, source_kind=quality)
        entries = appended(entries, OUTCOME, prepare_result(entries, request))
    after = history(entries)
    assert [r["request"]["quantity_balls"] for r in after.records(OUTCOME)] == [1, 3, 2, 0]
    assert all(r["evidence_kind"] == "ACTUAL_EXECUTION_RESULT" for r in after.records(OUTCOME))
    assert after.latest_input == before.latest_input
    assert after.records(PLAN) == before.records(PLAN)
    assert after.records(CONFIRMATION) == before.records(CONFIRMATION)
    assert prepare_result(entries, collected)["disposition"] == "duplicate"
    assert_code("planning_conflict", prepare_result, entries, {**collected, "quantity_balls": True})


def test_outcome_correction_appends_and_requires_latest_stage_record():
    entries = confirmed()
    confirmation = history(entries).records(CONFIRMATION)[0]
    original = prepare_result(entries, outcome_payload(confirmation))
    entries = appended(entries, OUTCOME, original)
    corrected = outcome_payload(confirmation, request_id="correct-result", quantity_balls=7,
                                reason="Correct transcription", supersedes_outcome_id=original["record"]["outcome_id"])
    second = prepare_result(entries, corrected)
    entries = appended(entries, OUTCOME, second)
    records = history(entries).records(OUTCOME)
    assert records[0] == original["record"]
    assert records[1]["request"]["supersedes_outcome_id"] == records[0]["outcome_id"]
    assert_code("planning_conflict", prepare_result, entries, {**corrected, "request_id": "stale-correction"})
    assert_code("planning_conflict", prepare_result, entries,
                outcome_payload(confirmation, request_id="unlinked-stage-overwrite", quantity_balls=9))


@pytest.mark.parametrize(("task", "created", "code"), [
    (None, None, "planning_conflict"),
    ("another-task", "2026-09-16T01:02:00Z", "planning_conflict"),
    ("task-test-001", None, "planning_invalid_request"),
    ("task-test-001", "2026-09-16T01:04:00Z", "planning_invalid_request"),
])
def test_outcome_requires_external_admission_and_ordered_time(task, created, code):
    entries = confirmed()
    request = outcome_payload(history(entries).records(CONFIRMATION)[0])
    assert_code(code, prepare_outcome, history(entries), request, CLOCK + timedelta(minutes=10),
                admitted_task_id=task, task_created_at_utc=created)


def test_replay_is_deterministic_detached_and_matches_shared_wire_schema():
    entries = confirmed()
    request = outcome_payload(history(entries).records(CONFIRMATION)[0])
    entries = appended(entries, OUTCOME, prepare_result(entries, request))
    first = replay_planning(entries, CONTEXT)
    second = replay_planning(deepcopy(entries), deepcopy(CONTEXT))
    assert canonical_json(first.entries) == canonical_json(second.entries)
    for entry in entries:
        definition = {INPUT: "InputRecord", PLAN: "PlanRecord", CONFIRMATION: "ConfirmationRecord", OUTCOME: "OutcomeRecord"}[entry["kind"]]
        validator("#/$defs/" + definition).validate(entry["record"])
    validator("#/$defs/Snapshot").validate(planning_snapshot(first, CONTEXT, CLOCK))
    entries[0]["record"]["inventory_clean_balls"]["value"] = 9999
    assert first.latest_input["inventory_clean_balls"]["value"] == 100


@pytest.mark.parametrize("corruption", [
    "input_digest", "input_cas", "input_missing_field", "plan_scenarios",
    "plan_unknown_field", "plan_unknown_rules", "confirmation_reference",
    "outcome_plan_link", "duplicate_record", "unknown_kind",
])
def test_replay_rejects_corrupt_or_illegal_history_as_unavailable(corruption):
    entries = confirmed()
    request = outcome_payload(history(entries).records(CONFIRMATION)[0])
    entries = appended(entries, OUTCOME, prepare_result(entries, request))
    if corruption == "input_digest": entries[0]["record"]["input_digest"] = "0" * 64
    elif corruption == "input_cas": entries[0]["record"]["expected_revision"] = 3
    elif corruption == "input_missing_field": entries[0]["record"].pop("request_id")
    elif corruption == "plan_scenarios": entries[1]["record"]["scenarios"][0]["baseline_end_balls"] += 1
    elif corruption == "plan_unknown_field": entries[1]["record"]["invented"] = True
    elif corruption == "plan_unknown_rules": entries[1]["record"]["rules_version"] = "future-policy/v2"
    elif corruption == "confirmation_reference": entries[2]["record"]["schedule"]["admission_reference"] = "wrong"
    elif corruption == "outcome_plan_link": entries[3]["record"]["plan_id"] = "another-plan"
    elif corruption == "duplicate_record": entries.append(deepcopy(entries[3]))
    elif corruption == "unknown_kind": entries[0]["kind"] = "foreign-record"
    assert_code("planning_unavailable", replay_planning, entries, CONTEXT)


def test_confirmation_after_earliest_start_preserves_due_until_exclusive_deadline():
    entries = planned()
    plan = history(entries).records(PLAN)[0]
    request = confirm_payload(plan)
    frozen = schedule_for(plan, request)
    later = CLOCK + timedelta(minutes=3)
    result = prepare_confirmation(history(entries), request, later,
                                  schedule=frozen, schedule_id="schedule_test_001")
    assert result["record"]["confirmed_at_utc"] == "2026-09-16T01:03:00Z"
    assert result["record"]["schedule"] == frozen
    assert result["record"]["schedule"]["due_at_utc"] == "2026-09-16T01:02:00Z"
    assert_code("planning_expired", prepare_confirmation, history(entries), request,
                utc(frozen["expires_at_utc"]), schedule=frozen, schedule_id="schedule_test_001")
    committed = appended(entries, CONFIRMATION, result)
    replayed = history(committed).records(CONFIRMATION)[0]
    assert replayed == result["record"]
    duplicate_after_expiry = prepare_confirmation(history(committed), request,
        CLOCK + timedelta(days=1), schedule=frozen, schedule_id="schedule_test_001")
    assert duplicate_after_expiry["disposition"] == "duplicate"
    assert duplicate_after_expiry["record"] == replayed


@pytest.mark.parametrize("number", [float("nan"), float("inf")])
def test_nonfinite_retry_is_invalid_input_not_unknown_commit(number):
    entries = initial()
    malformed = input_request()
    malformed["inventory_clean_balls"]["value"] = number
    assert_code("planning_invalid_request", prepare_input, history(entries), malformed, CONTEXT, CLOCK)
