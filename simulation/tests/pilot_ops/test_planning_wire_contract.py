"""Validate the shared planning shapes, units and every published HTTP example.

Arithmetic, freshness and retry behavior belong to planning behavioral tests.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

CONTRACT = Path(__file__).resolve().parents[2] / "docs/contracts/planning-v1"
SCHEMA = json.loads((CONTRACT / "schema.json").read_text(encoding="utf-8"))
EXAMPLES = sorted((CONTRACT / "examples").glob("*.json"))
FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time", raises=ValueError)
def valid_datetime(value):
    # jsonschema's optional RFC3339 dependency is not part of the Python stack.
    # Validate the format explicitly instead of silently skipping it.
    if not isinstance(value, str):
        return True
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validator(reference: str | None = None) -> Draft202012Validator:
    schema = SCHEMA if reference is None else {
        "$schema": SCHEMA["$schema"], "$ref": reference, "$defs": SCHEMA["$defs"],
    }
    return Draft202012Validator(schema, format_checker=FORMAT_CHECKER)


def requests() -> dict[str, dict]:
    example = json.loads((CONTRACT / "examples/success.json").read_text(encoding="utf-8"))
    return {
        item["request"]["schema_ref"].split("/")[-1]: item["request"]["body"]
        for item in example["exchanges"] if "body" in item["request"]
    }


def test_schema_is_valid_and_required_example_cases_exist():
    Draft202012Validator.check_schema(SCHEMA)
    assert {path.name for path in EXAMPLES} == {
        "success.json", "missing-data.json", "expired.json", "conflict.json",
        "duplicate-confirmation.json", "unknown-result.json",
    }


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_every_example_request_and_response_validates(path):
    example = json.loads(path.read_text(encoding="utf-8"))
    validator("#/$defs/UtcTimestamp").validate(example["clock_at_utc"])
    assert example["description"] and example["exchanges"]
    error_status = {
        "planning_invalid_request": 400, "planning_not_found": 404,
        "planning_request_not_found": 404, "planning_conflict": 409,
        "planning_expired": 409, "planning_not_ready": 409,
        "planning_unavailable": 503, "planning_result_unknown": 503,
    }
    for item in example["exchanges"]:
        validator("#/$defs/UtcTimestamp").validate(item["server_time_utc"])
        request, response = item["request"], item["response"]
        assert request["method"] in {"GET", "POST"}
        assert request["path"].startswith("/api/v1/planning")
        if request["method"] == "POST":
            validator(request["schema_ref"]).validate(request["body"])
            validator().validate(request["body"])
        else:
            assert "body" not in request
        validator(response["schema_ref"]).validate(response["body"])
        validator().validate(response["body"])
        body = response["body"]
        assert response["http_status"] == (
            error_status[body["error"]["code"]] if "error" in body else 200
        )


@pytest.mark.parametrize("definition", [
    "InputRequest", "PlanRequest", "ConfirmationRequest", "OutcomeRequest",
])
def test_requests_reject_unknown_fields_and_missing_fields(definition):
    sample = requests()[definition]
    with pytest.raises(ValidationError):
        validator(f"#/$defs/{definition}").validate({**sample, "invented_field": True})
    for required in SCHEMA["$defs"][definition]["required"]:
        malformed = copy.deepcopy(sample)
        malformed.pop(required)
        with pytest.raises(ValidationError):
            validator(f"#/$defs/{definition}").validate(malformed)


@pytest.mark.parametrize(("path", "value"), [
    (("schema",), "nxt-planning-input/v2"),
    (("expected_revision",), True),
    (("inventory_clean_balls", "unit"), "buckets"),
    (("inventory_clean_balls", "value"), -1),
    (("inventory_clean_balls", "value"), True),
    (("demand", "value", "high"), []),
    (("demand", "source_kind"), "SYSTEM_SUGGESTION"),
    (("demand", "source_ref"), ""),
    (("operations_allowed", "value"), 1),
    (("zones", 0, "clean_yield_balls", "value", "low"), 0.5),
    (("zones", 0, "cycle_minutes", "value", "wash"), -1),
    (("zones", 0, "cycle_minutes", "value", "invented_stage"), 5),
    (("valid_until_utc",), "2026-09-16T09:00:00+00:00"),
    (("valid_until_utc",), "2026-09-16T09:00:00.1234567Z"),
    (("valid_until_utc",), "2026-99-16T09:00:00Z"),
])
def test_input_schema_rejects_wrong_units_sources_numbers_and_times(path, value):
    malformed = copy.deepcopy(requests()["InputRequest"])
    target = malformed
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        validator("#/$defs/InputRequest").validate(malformed)


@pytest.mark.parametrize(("field", "value"), [
    ("quantity_balls", 1.5), ("quantity_balls", True),
    ("stage", "TASK_SUCCEEDED"), ("source_kind", "SYSTEM_SUGGESTION"),
])
def test_actual_results_require_distinct_stage_and_attributed_counts(field, value):
    malformed = {**requests()["OutcomeRequest"], field: value}
    with pytest.raises(ValidationError):
        validator("#/$defs/OutcomeRequest").validate(malformed)


def test_empty_snapshot_and_unknown_values_do_not_create_inventory_facts():
    example = json.loads((CONTRACT / "examples/success.json").read_text(encoding="utf-8"))
    initial = example["exchanges"][0]["response"]["body"]["data"]
    assert initial["latest_input"] is None
    assert initial["plans"] == initial["confirmations"] == initial["outcomes"] == []
    missing = json.loads((CONTRACT / "examples/missing-data.json").read_text(encoding="utf-8"))
    incomplete = missing["exchanges"][0]["request"]["body"]
    assert incomplete["inventory_clean_balls"] is None and incomplete["demand"] is None
    assert incomplete["zones"] == []
    validator("#/$defs/InputRequest").validate(incomplete)


def confirmation_projection():
    example = json.loads((CONTRACT / "examples/success.json").read_text(encoding="utf-8"))
    return example["exchanges"][-1]["response"]["body"]["data"]["confirmations"][0]


def test_task_admission_read_field_accepts_new_and_legacy_shapes():
    projected = confirmation_projection()
    assert projected["task_created_at_utc"] == "2026-09-16T08:05:03.123456Z"
    for timestamp in [None, "2026-09-16T08:05:03Z", projected["task_created_at_utc"]]:
        validator("#/$defs/ConfirmationRecord").validate({**projected, "task_created_at_utc": timestamp})
    del projected["task_created_at_utc"]
    validator("#/$defs/ConfirmationRecord").validate(projected)
    # Optional read fields never enter the original durable confirmation receipt.
    example = json.loads((CONTRACT / "examples/success.json").read_text(encoding="utf-8"))
    durable = next(e["response"]["body"]["data"]["record"] for e in example["exchanges"]
                   if e["request"].get("schema_ref") == "#/$defs/ConfirmationRequest")
    assert "task_created_at_utc" not in durable
    validator("#/$defs/ConfirmationRecord").validate(durable)
    with pytest.raises(ValidationError):
        validator("#/$defs/ConfirmationRecord").validate({**projected, "invented_admission_time": None})


@pytest.mark.parametrize("timestamp", [
    0, True, {}, [], "", "2026-09-16T08:05:03+00:00",
    "2026-09-16T08:05:03.1234567Z", "2026-02-30T08:05:03Z",
])
def test_task_admission_read_field_rejects_invalid_times(timestamp):
    with pytest.raises(ValidationError):
        validator("#/$defs/ConfirmationRecord").validate({
            **confirmation_projection(), "task_created_at_utc": timestamp,
        })
