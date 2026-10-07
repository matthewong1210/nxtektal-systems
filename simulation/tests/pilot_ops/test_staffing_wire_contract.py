"""Executable contract checks for the staffing Manager API v1 wire format."""

from __future__ import annotations

import copy
import json
import math
from datetime import date, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


CONTRACT = Path(__file__).resolve().parents[2] / "docs/contracts/staffing-v1"
EXPECTED_EXAMPLES = {
    "cold-start.json", "roster-import.json", "exception-correction.json",
    "generation-success.json", "generation-unavailable.json",
    "result-unknown.json", "manager-response.json", "errors.json",
}
EXPECTED_ROUTES = {
    ("GET", "/api/v1/staffing"),
    ("GET", "/api/v1/staffing/dates/{service_date}"),
    ("GET", "/api/v1/staffing/requests/{operation_kind}/{request_id}"),
    ("POST", "/api/v1/staffing/roster-imports"),
    ("POST", "/api/v1/staffing/exceptions"),
    ("POST", "/api/v1/staffing/exceptions/{exception_id}/cancel"),
    ("POST", "/api/v1/staffing/exceptions/{exception_id}/correct"),
    ("POST", "/api/v1/staffing/suggestions"),
    ("POST", "/api/v1/staffing/suggestions/{suggestion_id}/accept"),
    ("POST", "/api/v1/staffing/suggestions/{suggestion_id}/modify"),
    ("POST", "/api/v1/staffing/suggestions/{suggestion_id}/reject"),
}
EXPECTED_RECEIPT_TRIPLES = {
    ("roster-import", "COMMITTED", "ROSTER_IMPORTED"),
    ("exception-record", "COMMITTED", "EXCEPTION_RECORDED"),
    ("exception-cancel", "COMMITTED", "EXCEPTION_CANCELLED"),
    ("exception-correct", "COMMITTED", "EXCEPTION_CORRECTED"),
    ("suggestion-generate", "RESERVED", "GENERATION_RESERVED"),
    ("suggestion-generate", "IN_PROGRESS", "GENERATION_IN_PROGRESS"),
    ("suggestion-generate", "RESULT_UNKNOWN", "GENERATION_INTERRUPTED"),
    ("suggestion-generate", "SUCCEEDED", "SUGGESTION_ISSUED"),
    *(("suggestion-generate", state, "SUGGESTION_UNAVAILABLE") for state in (
        "NO_VALID_SUGGESTION", "UNAVAILABLE", "REFUSED", "INVALID_RESPONSE",
        "PROVIDER_ERROR", "CONFIGURATION_ERROR", "SECURITY_ERROR",
    )),
    ("manager-response", "COMMITTED", "MANAGER_RESPONSE_COMMITTED"),
}
EXPECTED_ERROR_STATUS = {
    "staffing_invalid_request": 400,
    "staffing_not_found": 404,
    "staffing_request_not_found": 404,
    "staffing_conflict": 409,
    "staffing_exception_overlap": 409,
    "staffing_stale_suggestion": 409,
    "staffing_busy": 429,
    "staffing_unavailable": 503,
}
EXPECTED_SUCCESS_EXCHANGES = {
    "cold-start.json": {"current-empty", "date-empty"},
    "roster-import.json": {"roster-committed"},
    "exception-correction.json": {
        "exception-recorded", "exception-cancelled", "exception-corrected",
    },
    "generation-success.json": {
        "generation-reserved", "generation-in-progress", "suggestion-issued",
    },
    "generation-unavailable.json": {
        "no-valid-suggestion", "unavailable", "refused", "invalid-response",
        "provider-error", "configuration-error", "security-error",
    },
    "result-unknown.json": {"generation-interrupted"},
    "manager-response.json": {
        "manager-accepted", "manager-modified", "manager-rejected",
        "date-after-manager-response",
    },
}
REQUEST_DEF_BY_ROUTE = {
    "/api/v1/staffing/roster-imports": "RosterImportRequest",
    "/api/v1/staffing/exceptions": "ExceptionRecordRequest",
    "/api/v1/staffing/exceptions/{exception_id}/cancel": "ExceptionCancelRequest",
    "/api/v1/staffing/exceptions/{exception_id}/correct": "ExceptionCorrectRequest",
    "/api/v1/staffing/suggestions": "SuggestionGenerateRequest",
    "/api/v1/staffing/suggestions/{suggestion_id}/accept": "ManagerResponseRequest",
    "/api/v1/staffing/suggestions/{suggestion_id}/modify": "ManagerResponseRequest",
    "/api/v1/staffing/suggestions/{suggestion_id}/reject": "ManagerResponseRequest",
}
EXPECTED_SUCCESS_SEMANTICS = {
    ("cold-start.json", "current-empty"): ("GET", "/api/v1/staffing", None, "snapshot", 200, None, None, None),
    ("cold-start.json", "date-empty"): ("GET", "/api/v1/staffing/dates/{service_date}", None, "snapshot", 200, None, None, None),
    ("roster-import.json", "roster-committed"): ("POST", "/api/v1/staffing/roster-imports", "RosterImportRequest", "receipt", 200, "roster-import", None, "created"),
    ("exception-correction.json", "exception-recorded"): ("POST", "/api/v1/staffing/exceptions", "ExceptionRecordRequest", "receipt", 200, "exception-record", None, "created"),
    ("exception-correction.json", "exception-cancelled"): ("POST", "/api/v1/staffing/exceptions/{exception_id}/cancel", "ExceptionCancelRequest", "receipt", 200, "exception-cancel", None, "created"),
    ("exception-correction.json", "exception-corrected"): ("POST", "/api/v1/staffing/exceptions/{exception_id}/correct", "ExceptionCorrectRequest", "receipt", 200, "exception-correct", None, "created"),
    ("generation-success.json", "generation-reserved"): ("POST", "/api/v1/staffing/suggestions", "SuggestionGenerateRequest", "receipt", 202, "suggestion-generate", None, "created"),
    ("generation-success.json", "generation-in-progress"): ("POST", "/api/v1/staffing/suggestions", "SuggestionGenerateRequest", "receipt", 202, "suggestion-generate", None, "created"),
    ("generation-success.json", "suggestion-issued"): ("POST", "/api/v1/staffing/suggestions", "SuggestionGenerateRequest", "receipt", 202, "suggestion-generate", None, "created"),
    ("generation-unavailable.json", "no-valid-suggestion"): ("POST", "/api/v1/staffing/suggestions", "SuggestionGenerateRequest", "receipt", 202, "suggestion-generate", None, "created"),
    ("generation-unavailable.json", "unavailable"): ("POST", "/api/v1/staffing/suggestions", "SuggestionGenerateRequest", "receipt", 202, "suggestion-generate", None, "created"),
    ("generation-unavailable.json", "refused"): ("POST", "/api/v1/staffing/suggestions", "SuggestionGenerateRequest", "receipt", 202, "suggestion-generate", None, "created"),
    ("generation-unavailable.json", "invalid-response"): ("POST", "/api/v1/staffing/suggestions", "SuggestionGenerateRequest", "receipt", 202, "suggestion-generate", None, "created"),
    ("generation-unavailable.json", "provider-error"): ("POST", "/api/v1/staffing/suggestions", "SuggestionGenerateRequest", "receipt", 202, "suggestion-generate", None, "created"),
    ("generation-unavailable.json", "configuration-error"): ("POST", "/api/v1/staffing/suggestions", "SuggestionGenerateRequest", "receipt", 202, "suggestion-generate", None, "created"),
    ("generation-unavailable.json", "security-error"): ("POST", "/api/v1/staffing/suggestions", "SuggestionGenerateRequest", "receipt", 202, "suggestion-generate", None, "created"),
    ("result-unknown.json", "generation-interrupted"): ("GET", "/api/v1/staffing/requests/{operation_kind}/{request_id}", None, "receipt", 200, "suggestion-generate", None, "duplicate"),
    ("manager-response.json", "manager-accepted"): ("POST", "/api/v1/staffing/suggestions/{suggestion_id}/accept", "ManagerResponseRequest", "receipt", 200, "manager-response", "ACCEPT", "created"),
    ("manager-response.json", "manager-modified"): ("POST", "/api/v1/staffing/suggestions/{suggestion_id}/modify", "ManagerResponseRequest", "receipt", 200, "manager-response", "MODIFY", "created"),
    ("manager-response.json", "manager-rejected"): ("POST", "/api/v1/staffing/suggestions/{suggestion_id}/reject", "ManagerResponseRequest", "receipt", 200, "manager-response", "REJECT", "created"),
    ("manager-response.json", "date-after-manager-response"): ("GET", "/api/v1/staffing/dates/{service_date}", None, "snapshot", 200, None, None, None),
}
FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date", raises=ValueError)
def valid_date(value: object) -> bool:
    if not isinstance(value, str):
        return True
    return date.fromisoformat(value).isoformat() == value


@FORMAT_CHECKER.checks("date-time", raises=ValueError)
def valid_datetime(value: object) -> bool:
    # jsonschema's optional RFC3339 dependency is not installed in this stack.
    if not isinstance(value, str):
        return True
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _strict_json_text(text: str) -> dict[str, object]:
    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON number: {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(
        text,
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )
    if not isinstance(value, dict):
        raise ValueError("contract document root must be an object")
    return value


def load_example(name: str) -> dict[str, object]:
    return _strict_json_text(
        (CONTRACT / "examples" / name).read_text(encoding="utf-8")
    )


def all_exchanges():
    for path in sorted((CONTRACT / "examples").glob("*.json")):
        document = _strict_json_text(path.read_text(encoding="utf-8"))
        assert set(document) == {"schema", "exchanges"}
        for exchange in document["exchanges"]:
            assert set(exchange) in (
                {"name", "method", "route_template", "http_status", "body"},
                {"name", "method", "route_template", "request", "http_status", "body"},
            )
            yield path.name, exchange


def assert_shared_provenance_input_digest(value: object) -> None:
    if isinstance(value, dict):
        provenance = value.get("provenance")
        if isinstance(provenance, list) and provenance:
            assert len({row["input_digest"] for row in provenance}) == 1
        for child in value.values():
            assert_shared_provenance_input_digest(child)
    elif isinstance(value, list):
        for child in value:
            assert_shared_provenance_input_digest(child)


def schema_document() -> dict[str, object]:
    return _strict_json_text((CONTRACT / "schema.json").read_text(encoding="utf-8"))


def validator(ref: str) -> Draft202012Validator:
    return Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": ref,
            "$defs": schema_document()["$defs"],
        },
        format_checker=FORMAT_CHECKER,
    )


def valid_receipt(operation_kind: str, state: str | None = None) -> dict[str, object]:
    for filename, exchange in all_exchanges():
        if filename == "errors.json":
            continue
        data = exchange["body"].get("data")
        if not isinstance(data, dict) or data.get("operation_kind") != operation_kind:
            continue
        if state is None or data.get("state") == state:
            return copy.deepcopy(data)
    raise AssertionError(f"missing receipt fixture: {operation_kind}/{state}")


def test_schema_is_valid_and_contract_inventory_is_exact() -> None:
    schema = schema_document()
    Draft202012Validator.check_schema(schema)
    assert {path.name for path in (CONTRACT / "examples").glob("*.json")} == EXPECTED_EXAMPLES


@pytest.mark.parametrize("malformed", [
    '{"schema":"x","schema":"y"}',
    '{"value":NaN}',
    '{"value":Infinity}',
    '{"value":1e9999}',
])
def test_contract_loader_rejects_non_strict_json(malformed: str) -> None:
    with pytest.raises(ValueError):
        _strict_json_text(malformed)


def test_root_schema_has_closed_useful_entry_points() -> None:
    root = Draft202012Validator(schema_document(), format_checker=FORMAT_CHECKER)
    for name in EXPECTED_EXAMPLES:
        root.validate(load_example(name))
    root.validate(load_example("roster-import.json")["exchanges"][0]["request"])
    root.validate(load_example("roster-import.json")["exchanges"][0]["body"])
    with pytest.raises(ValidationError):
        root.validate({"arbitrary": "object"})


def test_root_schema_rejects_route_semantic_mutations() -> None:
    root = Draft202012Validator(schema_document(), format_checker=FORMAT_CHECKER)
    roster = load_example("roster-import.json")
    malformed = copy.deepcopy(roster)
    malformed["exchanges"][0]["http_status"] = 202
    with pytest.raises(ValidationError):
        root.validate(malformed)

    cold = load_example("cold-start.json")
    malformed = copy.deepcopy(cold)
    malformed["exchanges"][0]["http_status"] = 202
    with pytest.raises(ValidationError):
        root.validate(malformed)
    malformed = copy.deepcopy(cold)
    malformed["exchanges"][0]["body"] = roster["exchanges"][0]["body"]
    with pytest.raises(ValidationError):
        root.validate(malformed)

    malformed = copy.deepcopy(roster)
    malformed["exchanges"].append(copy.deepcopy(malformed["exchanges"][0]))
    with pytest.raises(ValidationError):
        root.validate(malformed)


def test_example_route_inventory_is_exact() -> None:
    actual = {(row["method"], row["route_template"]) for _, row in all_exchanges()}
    assert actual == EXPECTED_ROUTES


def test_success_routes_bind_request_response_and_http_semantics() -> None:
    seen: set[tuple[str, str]] = set()
    for filename, row in all_exchanges():
        if filename == "errors.json":
            continue
        key = (filename, row["name"])
        assert key not in seen
        seen.add(key)
        (
            method, route, request_def, response_def, status,
            operation_kind, manager_kind, disposition,
        ) = EXPECTED_SUCCESS_SEMANTICS[key]
        assert (row["method"], row["route_template"], row["http_status"]) == (
            method, route, status,
        )
        data = row["body"]["data"]
        if request_def is None:
            assert "request" not in row
        else:
            validator(f"#/$defs/{request_def}").validate(row["request"])
        if response_def == "snapshot":
            validator("#/$defs/StaffingDateSnapshot").validate(data)
            assert "operation_kind" not in data
        else:
            validator("#/$defs/StaffingReceipt").validate(data)
            assert data["operation_kind"] == operation_kind
            assert data["disposition"] == disposition
        if manager_kind is not None:
            assert row["request"]["kind"] == manager_kind
    assert seen == set(EXPECTED_SUCCESS_SEMANTICS)


def test_success_examples_are_closed_and_validate_by_branch() -> None:
    for filename, expected_names in EXPECTED_SUCCESS_EXCHANGES.items():
        document = load_example(filename)
        assert document["schema"] == "nxt-staffing-exchanges/v1"
        names = [row["name"] for row in document["exchanges"]]
        assert len(names) == len(set(names)) == len(expected_names)
        assert set(names) == expected_names
        for row in document["exchanges"]:
            assert row["http_status"] in {200, 202}
            if row["method"] == "POST":
                validator(f"#/$defs/{REQUEST_DEF_BY_ROUTE[row['route_template']]}").validate(
                    row["request"]
                )
            else:
                assert "request" not in row
            validator("#/$defs/SuccessEnvelope").validate(row["body"])
            data = row["body"]["data"]
            target = (
                "#/$defs/StaffingReceipt"
                if "operation_kind" in data else "#/$defs/StaffingDateSnapshot"
            )
            validator(target).validate(data)


def test_fixture_receipt_triples_are_exact() -> None:
    actual = set()
    for filename, exchange in all_exchanges():
        data = exchange["body"].get("data")
        if filename != "errors.json" and isinstance(data, dict) and "operation_kind" in data:
            actual.add((data["operation_kind"], data["state"], data["record"]["record_kind"]))
    assert actual == EXPECTED_RECEIPT_TRIPLES


@pytest.mark.parametrize(("operation_kind", "state", "record_kind"), [
    ("roster-import", "COMMITTED", "GENERATION_RESERVED"),
    ("suggestion-generate", "RESERVED", "SUGGESTION_ISSUED"),
    ("suggestion-generate", "RESULT_UNKNOWN", "GENERATION_IN_PROGRESS"),
    ("manager-response", "SUCCEEDED", "MANAGER_RESPONSE_COMMITTED"),
])
def test_receipt_rejects_mismatched_operation_state_and_record(
    operation_kind: str, state: str, record_kind: str
) -> None:
    malformed = valid_receipt("roster-import")
    malformed.update(operation_kind=operation_kind, state=state)
    malformed["record"]["record_kind"] = record_kind
    with pytest.raises(ValidationError):
        validator("#/$defs/StaffingReceipt").validate(malformed)


def test_error_examples_have_fixed_envelopes_and_statuses() -> None:
    document = load_example("errors.json")
    assert document["schema"] == "nxt-staffing-errors/v1"
    exchanges = document["exchanges"]
    names = [row["name"] for row in exchanges]
    codes = [row["body"]["error"]["code"] for row in exchanges]
    assert len(names) == len(set(names)) == len(EXPECTED_ERROR_STATUS)
    assert len(codes) == len(set(codes)) == len(EXPECTED_ERROR_STATUS)
    assert set(codes) == set(EXPECTED_ERROR_STATUS)
    for row in exchanges:
        assert set(row) == {"name", "method", "route_template", "http_status", "body"}
        validator("#/$defs/ErrorEnvelope").validate(row["body"])
        assert row["http_status"] == EXPECTED_ERROR_STATUS[row["body"]["error"]["code"]]

    malformed = copy.deepcopy(document)
    malformed["exchanges"][0]["http_status"] = 409
    with pytest.raises(ValidationError):
        Draft202012Validator(
            schema_document(), format_checker=FORMAT_CHECKER
        ).validate(malformed)


def test_every_object_is_closed_at_representative_depths() -> None:
    receipt = valid_receipt("suggestion-generate", "SUCCEEDED")
    mutations = [
        receipt | {"extra": True},
        receipt | {"record": receipt["record"] | {"extra": True}},
        receipt | {"record": receipt["record"] | {
            "candidates": [receipt["record"]["candidates"][0] | {"extra": True}]
        }},
    ]
    for malformed in mutations:
        with pytest.raises(ValidationError):
            validator("#/$defs/StaffingReceipt").validate(malformed)


def test_requests_enforce_exception_and_manager_branches() -> None:
    recorded = load_example("exception-correction.json")["exchanges"][0]["request"]
    invalid = copy.deepcopy(recorded)
    invalid["kind"] = "LEAVE"
    invalid["time_local"] = "10:30"
    with pytest.raises(ValidationError):
        validator("#/$defs/ExceptionRecordRequest").validate(invalid)

    rows = load_example("manager-response.json")["exchanges"][:3]
    by_kind = {row["request"]["kind"]: row["request"] for row in rows}
    for request in by_kind.values():
        validator("#/$defs/ManagerResponseRequest").validate(request)
    malformed = copy.deepcopy(by_kind["REJECT"])
    malformed["candidate_index"] = 1
    with pytest.raises(ValidationError):
        validator("#/$defs/ManagerResponseRequest").validate(malformed)
    malformed = copy.deepcopy(by_kind["MODIFY"])
    malformed["edited_operations"] = []
    with pytest.raises(ValidationError):
        validator("#/$defs/ManagerResponseRequest").validate(malformed)


def test_timestamp_profiles_reject_signed_zero_and_non_utc_audit_time() -> None:
    issued = valid_receipt("suggestion-generate", "SUCCEEDED")
    for bad in (
        "2026-10-06T09:00:00+00:00",
        "2026-10-06T09:00:00-00:00",
        "2026-10-06T09:00:01+08:00",
        "2026-10-06T09:00:00.100000+08:00",
    ):
        malformed = copy.deepcopy(issued)
        malformed["record"]["candidates"][0]["operations"][0]["start_at"] = bad
        with pytest.raises(ValidationError):
            validator("#/$defs/StaffingReceipt").validate(malformed)
    for bad in (
        "2026-10-06T01:00:00+08:00",
        "2026-10-06T01:00:00Z",
        "2026-02-30T01:00:00.000000Z",
    ):
        roster = valid_receipt("roster-import")
        roster["record"]["imported_at_utc"] = bad
        with pytest.raises(ValidationError):
            validator("#/$defs/StaffingReceipt").validate(roster)

    snapshot = load_example("cold-start.json")["exchanges"][0]["body"]["data"]
    malformed = copy.deepcopy(snapshot)
    malformed["server_time_utc"] = "2026-10-06T01:00:00Z"
    with pytest.raises(ValidationError):
        validator("#/$defs/StaffingDateSnapshot").validate(malformed)

    request = load_example("generation-success.json")["exchanges"][0]["request"]
    malformed = copy.deepcopy(request)
    malformed["service_date"] = "2026-02-30"
    with pytest.raises(ValidationError):
        validator("#/$defs/SuggestionGenerateRequest").validate(malformed)

    malformed = copy.deepcopy(issued)
    malformed["record"]["candidates"][0]["operations"][0]["start_at"] = (
        "2026-10-06T09:00:00+24:00"
    )
    with pytest.raises(ValidationError):
        validator("#/$defs/StaffingReceipt").validate(malformed)


def test_generation_state_and_manager_response_coherence() -> None:
    snapshot = next(
        row["body"]["data"]
        for row in load_example("manager-response.json")["exchanges"]
        if row["name"] == "date-after-manager-response"
    )
    generation = snapshot["generations"][0]
    assert generation["manager_response"]["response_kind"] == "ACCEPT"
    assert generation["manager_response"]["effective_plan"] is not None
    malformed = copy.deepcopy(snapshot)
    malformed["generations"][0]["manager_response"]["effective_plan"] = None
    with pytest.raises(ValidationError):
        validator("#/$defs/StaffingDateSnapshot").validate(malformed)
    malformed = copy.deepcopy(snapshot)
    malformed["generations"][0]["state"] = "RESULT_UNKNOWN"
    malformed["generations"][0]["failure_code"] = None
    with pytest.raises(ValidationError):
        validator("#/$defs/StaffingDateSnapshot").validate(malformed)
    malformed = copy.deepcopy(snapshot)
    malformed["generations"][0]["manager_response"]["effective_plan"]["status"] = "REVIEW_REQUIRED"
    with pytest.raises(ValidationError):
        validator("#/$defs/StaffingDateSnapshot").validate(malformed)


def test_generation_projection_state_branches_and_failure_sets_are_closed() -> None:
    snapshot = next(
        row["body"]["data"]
        for row in load_example("manager-response.json")["exchanges"]
        if row["name"] == "date-after-manager-response"
    )
    succeeded = snapshot["generations"][0]
    generation_validator = validator("#/$defs/GenerationProjection")

    reserved = copy.deepcopy(succeeded)
    reserved.update(
        state="RESERVED", candidates=[], coverage_gaps=[], provenance=[],
        failure_code=None, manager_response=None,
    )
    generation_validator.validate(reserved)
    malformed = copy.deepcopy(reserved)
    malformed["provenance"] = [
        load_example("generation-success.json")["exchanges"][2]["body"]["data"]
        ["record"]["provenance"][0]
    ]
    with pytest.raises(ValidationError):
        generation_validator.validate(malformed)

    in_progress = copy.deepcopy(reserved)
    in_progress["state"] = "IN_PROGRESS"
    malformed = copy.deepcopy(in_progress)
    row = copy.deepcopy(
        load_example("generation-success.json")["exchanges"][2]["body"]["data"]
        ["record"]["provenance"][0]
    )
    row["attempt_index"] = 1
    malformed["provenance"] = [row]
    with pytest.raises(ValidationError):
        generation_validator.validate(malformed)
    row["attempt_index"] = 0
    in_progress["provenance"] = [row]
    generation_validator.validate(in_progress)

    no_valid = copy.deepcopy(reserved)
    no_valid.update(
        state="NO_VALID_SUGGESTION", failure_code="NO_VALID_SUGGESTION",
        provenance=[copy.deepcopy(row)],
    )
    generation_validator.validate(no_valid)

    unavailable = copy.deepcopy(reserved)
    unavailable.update(state="UNAVAILABLE", failure_code="DEADLINE_EXHAUSTED")
    generation_validator.validate(unavailable)
    unavailable["failure_code"] = "PROVIDER_REFUSED"
    with pytest.raises(ValidationError):
        generation_validator.validate(unavailable)

    malformed = copy.deepcopy(succeeded)
    malformed["candidates"] = [
        load_example("generation-unavailable.json")["exchanges"][0]["body"]["data"]
        ["record"]["candidates"][0]
    ]
    with pytest.raises(ValidationError):
        generation_validator.validate(malformed)
    malformed = copy.deepcopy(succeeded)
    malformed["candidates"] = [
        copy.deepcopy(succeeded["candidates"][0]),
        copy.deepcopy(succeeded["candidates"][0]),
    ]
    with pytest.raises(ValidationError):
        generation_validator.validate(malformed)


def test_provider_provenance_route_and_outcome_tuples_are_closed() -> None:
    provenance_validator = validator("#/$defs/ProviderProvenance")
    success = copy.deepcopy(
        load_example("generation-success.json")["exchanges"][2]["body"]["data"]
        ["record"]["provenance"][0]
    )
    provenance_validator.validate(success)

    malformed = copy.deepcopy(success)
    malformed["attempt_index"] = 1
    with pytest.raises(ValidationError):
        provenance_validator.validate(malformed)
    malformed = copy.deepcopy(success)
    malformed["output_digest"] = None
    with pytest.raises(ValidationError):
        provenance_validator.validate(malformed)
    malformed = copy.deepcopy(success)
    malformed["retryable"] = True
    with pytest.raises(ValidationError):
        provenance_validator.validate(malformed)

    failed = copy.deepcopy(success)
    failed.update(
        failure_code="BOGUS", retryable=False, security_failure=False,
        provider_request_id=None, finish_reason=None, output_digest=None,
        input_tokens=None, output_tokens=None,
    )
    with pytest.raises(ValidationError):
        provenance_validator.validate(failed)
    failed["failure_code"] = "PROVIDER_CLIENT_ERROR"
    failed["retryable"] = True
    with pytest.raises(ValidationError):
        provenance_validator.validate(failed)
    failed["retryable"] = False
    failed["provider_request_id"] = "must-be-null-on-failure"
    with pytest.raises(ValidationError):
        provenance_validator.validate(failed)


def test_terminal_records_bind_provenance_route_and_final_outcome() -> None:
    receipt_validator = validator("#/$defs/StaffingReceipt")

    success_exchange = next(
        row for row in load_example("generation-success.json")["exchanges"]
        if row["name"] == "suggestion-issued"
    )
    success_receipt = copy.deepcopy(success_exchange["body"]["data"])
    success_attempt = copy.deepcopy(success_receipt["record"]["provenance"][0])
    provider_failure = copy.deepcopy(next(
        row for row in load_example("generation-unavailable.json")["exchanges"]
        if row["name"] == "provider-error"
    )["body"]["data"]["record"]["provenance"][0])
    security_failure = copy.deepcopy(next(
        row for row in load_example("generation-unavailable.json")["exchanges"]
        if row["name"] == "security-error"
    )["body"]["data"]["record"]["provenance"][0])

    malformed = copy.deepcopy(success_receipt)
    malformed["record"]["provenance"] = [provider_failure]
    with pytest.raises(ValidationError):
        receipt_validator.validate(malformed)

    no_valid = copy.deepcopy(next(
        row for row in load_example("generation-unavailable.json")["exchanges"]
        if row["name"] == "no-valid-suggestion"
    )["body"]["data"])
    no_valid["record"]["provenance"] = [provider_failure]
    with pytest.raises(ValidationError):
        receipt_validator.validate(no_valid)

    refused = copy.deepcopy(next(
        row for row in load_example("generation-unavailable.json")["exchanges"]
        if row["name"] == "refused"
    )["body"]["data"])
    refused["record"]["provenance"] = [security_failure]
    with pytest.raises(ValidationError):
        receipt_validator.validate(refused)

    unconfigured = copy.deepcopy(next(
        row for row in load_example("generation-unavailable.json")["exchanges"]
        if row["name"] == "configuration-error"
    )["body"]["data"])
    assert unconfigured["record"]["failure_code"] == "PROVIDER_UNCONFIGURED"
    unconfigured["record"]["provenance"] = [success_attempt]
    with pytest.raises(ValidationError):
        receipt_validator.validate(unconfigured)

    anthropic_success = copy.deepcopy(success_attempt)
    anthropic_success.update(
        attempt_index=1, provider="ANTHROPIC", region="GLOBAL",
        route_role="BACKUP", route_id="global-anthropic-v1",
        model_id="claude-sonnet", finish_reason="tool_use",
    )
    malformed = copy.deepcopy(success_receipt)
    malformed["record"]["provenance"] = [success_attempt, anthropic_success]
    with pytest.raises(ValidationError):
        receipt_validator.validate(malformed)

    openai_fallback = copy.deepcopy(success_attempt)
    openai_fallback.update(
        provider="OPENAI", region="GLOBAL", route_id="global-openai-v1",
        model_id="gpt-5", failure_code="CONNECT_TIMEOUT", retryable=True,
        provider_request_id=None, finish_reason=None, output_digest=None,
        input_tokens=None, output_tokens=None,
    )
    two_route = copy.deepcopy(success_receipt)
    two_route["record"]["provenance"] = [openai_fallback, anthropic_success]
    receipt_validator.validate(two_route)
    assert_shared_provenance_input_digest(two_route)
    two_route["record"]["provenance"][1]["input_digest"] = "e" * 64
    with pytest.raises(AssertionError):
        assert_shared_provenance_input_digest(two_route)


def test_candidate_rejection_codes_and_gap_coherence_are_closed() -> None:
    candidate = copy.deepcopy(
        load_example("generation-unavailable.json")["exchanges"][0]["body"]["data"]
        ["record"]["candidates"][0]
    )
    candidate_validator = validator("#/$defs/CandidateProjection")
    candidate_validator.validate(candidate)

    malformed = copy.deepcopy(candidate)
    malformed["rejection_codes"] = ["NOT_A_DOMAIN_CODE"]
    with pytest.raises(ValidationError):
        candidate_validator.validate(malformed)
    malformed = copy.deepcopy(candidate)
    malformed["coverage_gaps"] = []
    with pytest.raises(ValidationError):
        candidate_validator.validate(malformed)
    malformed = copy.deepcopy(candidate)
    malformed["rejection_codes"] = ["COVERAGE_GAP", "INVALID_INTERVAL"]
    with pytest.raises(ValidationError):
        candidate_validator.validate(malformed)

    valid = copy.deepcopy(
        load_example("generation-success.json")["exchanges"][2]["body"]["data"]
        ["record"]["candidates"][0]
    )
    valid["rejection_codes"] = ["INVALID_INTERVAL"]
    with pytest.raises(ValidationError):
        candidate_validator.validate(valid)


def test_generation_capability_allows_only_frozen_route_tuples() -> None:
    capability_validator = validator("#/$defs/GenerationCapability")
    valid = [
        {"status":"UNAVAILABLE","region":None,"primary_provider":None,"backup_provider":None,"failure_code":"PROVIDER_UNCONFIGURED"},
        {"status":"READY","region":"CN","primary_provider":"KIMI","backup_provider":None,"failure_code":None},
        {"status":"UNAVAILABLE","region":"CN","primary_provider":"KIMI","backup_provider":None,"failure_code":"PROVIDER_UNCONFIGURED"},
        {"status":"READY","region":"GLOBAL","primary_provider":"OPENAI","backup_provider":"ANTHROPIC","failure_code":None},
        {"status":"DEGRADED_BACKUP_UNCONFIGURED","region":"GLOBAL","primary_provider":"OPENAI","backup_provider":None,"failure_code":"BACKUP_UNCONFIGURED"},
        {"status":"UNAVAILABLE","region":"GLOBAL","primary_provider":"OPENAI","backup_provider":None,"failure_code":"PROVIDER_UNCONFIGURED"},
    ]
    for item in valid:
        capability_validator.validate(item)
    for malformed in (
        valid[1] | {"primary_provider": "OPENAI"},
        valid[3] | {"backup_provider": None},
        valid[4] | {"failure_code": None},
        valid[0] | {"region": "CN"},
    ):
        with pytest.raises(ValidationError):
            capability_validator.validate(malformed)


def test_manager_receipts_retain_operator_attribution() -> None:
    for row in load_example("manager-response.json")["exchanges"][:3]:
        record = row["body"]["data"]["record"]
        assert record["operator"] == row["request"]["operator"]
        malformed = copy.deepcopy(row["body"]["data"])
        del malformed["record"]["operator"]
        with pytest.raises(ValidationError):
            validator("#/$defs/StaffingReceipt").validate(malformed)
    accepted = copy.deepcopy(
        load_example("manager-response.json")["exchanges"][0]["body"]["data"]
    )
    accepted["record"]["effective_plan"]["status"] = "REVIEW_REQUIRED"
    with pytest.raises(ValidationError):
        validator("#/$defs/StaffingReceipt").validate(accepted)


def test_coverage_gap_examples_obey_relational_count_contract() -> None:
    for filename, exchange in all_exchanges():
        if filename == "errors.json":
            continue
        stack = [exchange["body"]]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if {"required_count", "assigned_count"} <= set(value):
                    assert 0 <= value["assigned_count"] < value["required_count"]
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)


def test_contract_omits_provider_private_material() -> None:
    forbidden = {
        "worker_alias", "assignment_alias", "worker_alias_to_staff_id",
        "assignment_alias_to_assignment_id", "alias_nonce", "alias_nonce_digest",
        "api_key", "headers", "prompt", "raw_response", "reasoning",
    }
    for _, exchange in all_exchanges():
        stack = [exchange]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                assert forbidden.isdisjoint(value)
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)


def test_example_provenance_rows_share_one_input_digest() -> None:
    for _, exchange in all_exchanges():
        assert_shared_provenance_input_digest(exchange)
