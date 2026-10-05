"""Strict contract validation rejects invented, ambiguous or malformed evidence."""

from copy import deepcopy

import pytest

from nxt_pilot_ops.planning_contracts import (
    PlanningError, utc, validate_confirmation_request, validate_input_request,
    validate_outcome_request, validate_plan_request,
)
from .test_planning import CONTEXT, END, NOW, input_request, plan_request


def test_input_validation_detaches_and_preserves_measured_or_estimated_quality():
    payload = input_request()
    payload["inventory_clean_balls"]["source_kind"] = "MEASURED"
    accepted = validate_input_request(payload, CONTEXT, NOW)
    accepted["zones"][0]["cycle_minutes"]["value"]["wash"] = 99
    assert payload["zones"][0]["cycle_minutes"]["value"]["wash"] == 1
    assert accepted["inventory_clean_balls"]["source_kind"] == "MEASURED"
    assert accepted["demand"]["source_kind"] == "MANUAL_ESTIMATE"


@pytest.mark.parametrize("bad", [True, False, -1, float("nan"), float("inf"), "100", 10**1000])
def test_invalid_inventory_numbers_fail(bad):
    payload = input_request()
    payload["inventory_clean_balls"]["value"] = bad
    with pytest.raises(PlanningError) as error:
        validate_input_request(payload, CONTEXT, NOW)
    assert error.value.code == "planning_invalid_request"


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra=True),
    lambda p: p.update(schema="nxt-planning-input/v2"),
    lambda p: p.update(expected_revision=True),
    lambda p: p.update(site_id="another-site"),
    lambda p: p.update(deployment_id="another-deployment"),
    lambda p: p.update(site_timezone="UTC"),
    lambda p: p.update(operator=" "),
    lambda p: p.update(scope="AUTOMATIC"),
    lambda p: p["inventory_clean_balls"].update(unit="kilograms"),
    lambda p: p["inventory_clean_balls"].update(source_kind="SYSTEM_SUGGESTION"),
    lambda p: p["inventory_clean_balls"].update(observed_at_utc="2026-09-16T00:59:00Z"),
    lambda p: p["demand"].update(observed_at_utc="2026-09-16T01:00:01Z"),
    lambda p: p["demand"].update(valid_until_utc="2026-09-16T01:59:59Z"),
    lambda p: p["demand"]["value"].update(bucket_minutes=0),
    lambda p: p["demand"]["value"].update(low=[]),
    lambda p: p["demand"]["value"].update(low=[7, 2, 2]),
    lambda p: p["demand"]["value"].update(typical=[4]),
    lambda p: p["demand"]["value"].update(high=[True, 6, 6]),
    lambda p: p["operations_allowed"].update(value=1),
    lambda p: p["zones"][0].update(zone_id="unknown"),
    lambda p: p["zones"][0].update(robot_id="unknown"),
    lambda p: p["zones"].append(deepcopy(p["zones"][0])),
    lambda p: p["zones"][0]["clean_yield_balls"]["value"].update(low=120.5),
    lambda p: p["zones"][0]["clean_yield_balls"]["value"].update(low=181),
    lambda p: p["zones"][0]["cycle_minutes"]["value"].pop("wash"),
    lambda p: p["zones"][0]["cycle_minutes"]["value"].update(wash=None),
    lambda p: p["zones"][0]["cycle_minutes"]["value"].update(wash=-1),
])
def test_input_semantic_and_shape_errors_fail_closed(change):
    payload = input_request()
    change(payload)
    with pytest.raises(PlanningError):
        validate_input_request(payload, CONTEXT, NOW)


def test_expired_input_is_storable_as_historical_evidence():
    payload = input_request()
    assert validate_input_request(payload, CONTEXT, "2026-09-16T03:00:00Z") == payload


@pytest.mark.parametrize("field", ["inventory_clean_balls", "safety_stock_balls", "buffer_minutes",
    "demand", "operations_allowed", "washer_available", "collection_allowed", "clean_yield_balls", "cycle_minutes"])
def test_null_evidence_value_cannot_bypass_missingness_and_create_ready_plan(field):
    from nxt_pilot_ops.planning import evaluate_plan
    from .test_planning import input_record

    payload = input_request()
    owner = payload["zones"][0] if field in ("collection_allowed", "clean_yield_balls", "cycle_minutes") else payload
    owner[field]["value"] = None
    # In particular, null safety/buffer previously skipped number validation and
    # yielded READY with no missing evidence and no latest-safe-start deadline.
    with pytest.raises(PlanningError, match="value cannot be null"):
        validated = input_record(payload)
        evaluate_plan(validated, plan_request(), NOW)

    # Unknown evidence has exactly one legal representation, and cannot become
    # a READY plan even when the other inputs are complete.
    owner[field] = None
    plan = evaluate_plan(input_record(payload), plan_request(), NOW)
    assert plan["status"] == "MISSING_DATA"
    assert plan["missing"]


@pytest.mark.parametrize("bad", ["2026-09-16T01:00:00+00:00", "2026-09-16T01:00Z",
    "2026-09-16T01:00:00", "2026-02-30T01:00:00Z", "2026-09-16T01:00:00.1234567Z", True])
def test_wire_time_rejects_offsets_missing_seconds_invalid_dates_and_precision_loss(bad):
    with pytest.raises(PlanningError):
        utc(bad)


@pytest.mark.parametrize("changes", [{"expected_plan_version": True}, {"input_revision": 0},
    {"plan_id": "existing", "expected_plan_version": 0}, {"plan_id": None, "expected_plan_version": 1},
    {"scope": "FOREVER"}, {"selection": {"zone_id": "A"}}, {"unknown": "field"}])
def test_plan_request_validation(changes):
    with pytest.raises(PlanningError):
        validate_plan_request(plan_request(**changes), NOW)


def test_confirmation_requires_explicit_version_and_does_not_claim_authentication():
    payload = {"schema": "nxt-planning-confirmation/v1", "request_id": "confirm",
               "plan_id": "plan", "plan_version": 1, "operator": "Any attributed name"}
    assert validate_confirmation_request(payload) == payload
    with pytest.raises(PlanningError):
        validate_confirmation_request({**payload, "plan_version": True})
    with pytest.raises(PlanningError):
        validate_confirmation_request({**payload, "authenticated": True})


def outcome_request():
    return {"schema": "nxt-planning-outcome/v1", "request_id": "result", "confirmation_id": "confirm",
            "task_id": "task", "operator": "Operator", "reason": "Counted batch", "stage": "SUPPLIED",
            "quantity_balls": 100, "started_at_utc": NOW, "completed_at_utc": NOW,
            "source_kind": "MEASURED", "source_ref": "counter:manual-reading", "supersedes_outcome_id": None}


@pytest.mark.parametrize("stage", ["COLLECTED", "UNLOADED", "WASHED", "SUPPLIED"])
def test_actual_stages_are_separately_validated_evidence(stage):
    payload = {**outcome_request(), "stage": stage}
    assert validate_outcome_request(payload, NOW) == payload


@pytest.mark.parametrize("changes", [{"quantity_balls": True}, {"quantity_balls": 1.5},
    {"quantity_balls": -1}, {"stage": "TASK_SUCCESS"}, {"source_kind": "SYSTEM_SUGGESTION"},
    {"completed_at_utc": END}, {"started_at_utc": END}, {"supersedes_outcome_id": ""}])
def test_outcome_contract_rejects_inferred_success_future_times_and_bad_quantities(changes):
    with pytest.raises(PlanningError):
        validate_outcome_request({**outcome_request(), **changes}, NOW)
