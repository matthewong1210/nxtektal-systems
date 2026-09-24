"""Versioned task-operation service capabilities shared with the console."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from scripts.task_ops_service_capabilities import task_ops_service_capabilities


CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "docs/contracts/pilot-dispatch-v0/service-capabilities"
)
SCHEMA = CONTRACT / "schema.json"
EXAMPLES = CONTRACT / "examples"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_capability_schema_and_both_service_modes_are_frozen():
    schema = load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    examples = {path.stem: load(path) for path in EXAMPLES.glob("*.json")}
    assert set(examples) == {"fixed-v3-execution", "legacy-pilot-dispatch"}
    validator = Draft202012Validator(schema)
    for example in examples.values():
        validator.validate(example)

    assert examples["fixed-v3-execution"]["operations"] == {
        "planning_inputs_create": "UNAVAILABLE",
        "planning_plans_create": "UNAVAILABLE",
        "planning_confirmations_create": "UNAVAILABLE",
        "planning_outcomes_create": "SUPPORTED",
        "schedules_create": "UNAVAILABLE",
        "schedules_cancel": "UNAVAILABLE",
        "notifications_acknowledge": "SUPPORTED",
        "notifications_resolve": "SUPPORTED",
    }
    assert set(examples["legacy-pilot-dispatch"]["operations"].values()) == {
        "SUPPORTED"
    }


def test_declared_capabilities_reject_missing_unknown_and_inconsistent_operations():
    schema = load(SCHEMA)
    validator = Draft202012Validator(schema)
    fixed = load(EXAMPLES / "fixed-v3-execution.json")

    missing = json.loads(json.dumps(fixed))
    missing["operations"].pop("schedules_cancel")
    unknown = json.loads(json.dumps(fixed))
    unknown["operations"]["invented_write"] = "SUPPORTED"
    mismatch = json.loads(json.dumps(fixed))
    mismatch["operations"]["schedules_create"] = "SUPPORTED"
    for malformed in (missing, unknown, mismatch):
        with pytest.raises(ValidationError):
            validator.validate(malformed)


def test_service_producer_matches_examples_and_refuses_an_unknown_mode():
    assert task_ops_service_capabilities("FIXED_V3_EXECUTION") == load(
        EXAMPLES / "fixed-v3-execution.json"
    )
    assert task_ops_service_capabilities("LEGACY_PILOT_DISPATCH") == load(
        EXAMPLES / "legacy-pilot-dispatch.json"
    )
    with pytest.raises(ValueError, match="unknown task operations service mode"):
        task_ops_service_capabilities("AUTO_DETECT")
