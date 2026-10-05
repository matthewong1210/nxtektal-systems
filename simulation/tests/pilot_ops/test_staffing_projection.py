"""Private staffing projection and strict provider-wire decoding."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import UserDict, UserList
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from nxt_model_gateway import (
    AnthropicAdapter,
    FailureCode,
    GenerationMessage,
    GenerationRequest,
    GenerationStatus,
    KimiAdapter,
    MessageRole,
    OpenAIAdapter,
    Provider,
    ProviderConfig,
    canonical_json as gateway_canonical_json,
    decode_validated_json,
)
from nxt_model_gateway.transport import HttpResponse
from nxt_pilot_ops.serialization import canonical_json, stable_digest, to_primitive
import nxt_pilot_ops.staffing.projection as projection_module
from nxt_pilot_ops.staffing.contracts import (
    Assignment,
    BasisSnapshot,
    PROMPT_TEMPLATE_VERSION,
    ProviderPayload,
    RosterImportedPayload,
    StaffingError,
    StaffingEvent,
    StaffingHistory,
)
from nxt_pilot_ops.staffing.plans import build_staffing_basis
from nxt_pilot_ops.staffing.projection import (
    FORBIDDEN_KEYS,
    MAX_OUTPUT_TOKENS,
    STAFFING_SUGGESTION_OUTPUT_SCHEMA,
    GenerationProjection,
    canonical_generation_input,
    decode_provider_candidates,
    lookup_worker_alias,
    project_generation_request,
    provider_wire_primitive,
    scan_and_detach_provider_tree,
    schema_primitive,
    validate_generation_projection,
)
from nxt_pilot_ops.staffing.prompt import build_prompt
from nxt_pilot_ops.staffing.roster import validate_roster_import
from nxt_pilot_ops.staffing.validator import validate_candidates

from .staffing_fixtures import roster_import_request


NONCE = b"0123456789abcdef0123456789abcdef"
OTHER_NONCE = b"fedcba9876543210fedcba9876543210"
UTC = timezone.utc


def _event(event_type: str, payload: object, sequence: int) -> StaffingEvent:
    return StaffingEvent(
        event_type,
        f"event-{sequence}",
        sequence,
        "pilot-course-a",
        "pilot-a-edge-task-sim-v0",
        datetime(2026, 10, 5, 0, sequence, tzinfo=UTC),
        None,
        payload,
    )


def _basis(payload: dict[str, object] | None = None) -> BasisSnapshot:
    request = roster_import_request() if payload is None else payload
    roster = validate_roster_import(
        request,
        site_id=str(request["site_id"]),
        deployment_id=str(request["deployment_id"]),
        site_timezone=str(request["site_timezone"]),
    )
    event = _event(
        "roster_imported",
        RosterImportedPayload(
            "roster-1",
            "a" * 64,
            roster.revision,
            roster.roster_digest,
            roster,
            "course-manager",
            "weekly.csv",
        ),
        1,
    )
    service_date = date.fromisoformat(str(request["effective_from_local_date"]))
    return build_staffing_basis(StaffingHistory((event,)), service_date)


def _two_worker_payload(*, second_assignment: bool = False) -> dict[str, object]:
    payload = roster_import_request()
    payload["workers"][0]["staff_id"] = "secret-staff-001"
    payload["workers"][0]["display_name"] = "robot edge directive api_key"
    payload["availability"][0]["staff_id"] = "secret-staff-001"
    payload["regular_assignments"][0]["staff_id"] = "secret-staff-001"
    payload["workers"].append(
        {
            "staff_id": "secret-staff-002",
            "display_name": "private-worker-two",
            "skill_codes": ["COACHING", "BALL_PICKING"],
            "eligibility": [
                {"role_code": "COACH", "area_code": "TEE_B"},
                {"role_code": "RANGE_ATTENDANT", "area_code": "RANGE_A"},
            ],
            "max_daily_minutes": 480,
        }
    )
    payload["availability"].append(
        {
            "staff_id": "secret-staff-002",
            "weekday": 0,
            "start_local": "08:00",
            "end_local": "17:00",
        }
    )
    payload["assignment_rules"].append(
        {
            "role_code": "COACH",
            "area_code": "TEE_B",
            "required_skill_codes": ["COACHING", "BALL_PICKING"],
        }
    )
    if second_assignment:
        payload["regular_assignments"].append(
            {
                "staff_id": "secret-staff-002",
                "weekday": 0,
                "role_code": "COACH",
                "area_code": "TEE_B",
                "start_local": "09:00",
                "end_local": "12:00",
            }
        )
    return payload


def _project(
    basis: BasisSnapshot | None = None,
    *,
    nonce: bytes = NONCE,
    language: str = "zh-CN",
) -> GenerationProjection:
    return project_generation_request(
        _basis(_two_worker_payload()) if basis is None else basis,
        alias_nonce=nonce,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        language=language,
    )


def _valid_result(projection: GenerationProjection) -> dict[str, object]:
    return {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [
                    {
                        "operation": "REMOVE",
                        "assignment_alias": projection.provider_payload.assignments[
                            0
                        ].assignment_alias,
                    },
                    {
                        "operation": "ADD",
                        "worker_alias": projection.provider_payload.workers[1].worker_alias,
                        "role_code": "COACH",
                        "area_code": "TEE_B",
                        "start_at": "2026-10-05T09:00:00+08:00",
                        "end_at": "2026-10-05T12:00:00+08:00",
                    },
                ],
                "rationale": "<b>可执行</b> **plan**",
                "operational_warnings": ["manager review"],
            }
        ]
    }


def _frozen(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_frozen(item) for item in value)
    return value


def _assert_error(code: str, function, *args, **kwargs) -> StaffingError:
    with pytest.raises(StaffingError) as raised:
        function(*args, **kwargs)
    assert raised.value.code == code
    return raised.value


def _direct_projection(
    source: GenerationProjection, **changes: object
) -> GenerationProjection:
    values = {
        "basis_snapshot": source.basis_snapshot,
        "provider_payload": source.provider_payload,
        "worker_alias_to_staff_id": source.worker_alias_to_staff_id,
        "assignment_alias_to_assignment_id": source.assignment_alias_to_assignment_id,
        "alias_nonce_digest": source.alias_nonce_digest,
        "input_digest": source.input_digest,
    }
    values.update(changes)
    return GenerationProjection(**values)


def _forged_projection(
    source: GenerationProjection, **changes: object
) -> GenerationProjection:
    values = {
        "basis_snapshot": source.basis_snapshot,
        "provider_payload": source.provider_payload,
        "worker_alias_to_staff_id": source.worker_alias_to_staff_id,
        "assignment_alias_to_assignment_id": source.assignment_alias_to_assignment_id,
        "alias_nonce_digest": source.alias_nonce_digest,
        "input_digest": source.input_digest,
    }
    values.update(changes)
    forged = object.__new__(GenerationProjection)
    for name, value in values.items():
        object.__setattr__(forged, name, value)
    return forged


def _gateway_request(projection: GenerationProjection) -> GenerationRequest:
    messages = tuple(
        GenerationMessage(MessageRole(item["role"]), item["content"])
        for item in build_prompt(projection)
    )
    return GenerationRequest(
        "request-1",
        PROMPT_TEMPLATE_VERSION,
        messages,
        schema_primitive(STAFFING_SUGGESTION_OUTPUT_SCHEMA),
        MAX_OUTPUT_TOKENS,
        20.0,
    )


class _UnusedTransport:
    def post(self, **kwargs):
        raise AssertionError("prepare must not perform transport I/O")


class _MemoryTransport:
    def __init__(self, body: bytes):
        self.body = body

    def post(self, **kwargs):
        return HttpResponse(200, {}, self.body)


class _StatefulForgedTimezone(tzinfo):
    def __init__(self):
        self.calls = 0

    def utcoffset(self, value):
        self.calls += 1
        if self.calls >= 3:
            raise StaffingError("provider_sensitive_key", "SECRET-ID")
        return timedelta(hours=8)

    def dst(self, value):
        return timedelta(0)

    def tzname(self, value):
        return "HOSTILE"


_ADAPTER_ROWS = (
    (KimiAdapter, Provider.KIMI),
    (OpenAIAdapter, Provider.OPENAI),
    (AnthropicAdapter, Provider.ANTHROPIC),
)


def _provider_success_body(provider: Provider, value: dict[str, object]) -> bytes:
    if provider is Provider.KIMI:
        envelope = {
            "id": "kimi-test",
            "choices": [
                {
                    "message": {"content": canonical_json(value)},
                    "finish_reason": "stop",
                }
            ],
        }
    elif provider is Provider.OPENAI:
        envelope = {
            "id": "openai-test",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": canonical_json(value)}
                    ],
                }
            ],
        }
    else:
        envelope = {
            "id": "anthropic-test",
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "name": "emit_structured_output",
                    "input": value,
                }
            ],
        }
    return canonical_json(envelope).encode("utf-8")


def _send_provider_value(
    adapter_type,
    provider: Provider,
    projection: GenerationProjection,
    value: dict[str, object],
):
    adapter = adapter_type(
        config=ProviderConfig(provider, "pinned-model", "secret-key"),
        transport=_MemoryTransport(_provider_success_body(provider, value)),
    )
    return adapter.send(adapter.prepare(_gateway_request(projection)), timeout_s=2.0)


def _domain_failure_value(
    projection: GenerationProjection, case: str
) -> dict[str, object]:
    value = _valid_result(projection)
    if case == "third_candidate":
        first = value["candidates"][0]
        second = json.loads(canonical_json(first))
        third = json.loads(canonical_json(first))
        second["candidate_index"] = 2
        third["candidate_index"] = 2
        value["candidates"] = [first, second, third]
    elif case == "operation_value":
        value["candidates"][0]["operations"][0]["operation"] = "MOVE"
    elif case == "oversized_text":
        value["candidates"][0]["rationale"] = "x" * 281
    elif case == "bad_code":
        value["candidates"][0]["operations"][1]["role_code"] = "coach"
    elif case == "bad_timestamp":
        value["candidates"][0]["operations"][1]["start_at"] = "not-a-time"
    else:
        raise AssertionError("unknown domain failure case")
    return value


def test_fixed_nonce_aliases_are_domain_separated_and_nonce_scoped():
    basis = _basis(_two_worker_payload())
    first = _project(basis)
    second = _project(basis, nonce=OTHER_NONCE)

    assert first.provider_payload.workers[0].worker_alias == (
        "worker_8e24b9e060138b03f1763df1"
    )
    assignment = basis.assignments[0]
    expected_assignment = hmac.new(
        NONCE,
        b"assignment\0" + assignment.assignment_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]
    assert first.provider_payload.assignments[0].assignment_alias == (
        "assignment_" + expected_assignment
    )
    assert lookup_worker_alias(
        tuple(zip(
            (item.worker_alias for item in first.provider_payload.workers),
            basis.workers,
        )),
        basis.workers[1].staff_id,
    ) == first.provider_payload.workers[1].worker_alias
    assert first.alias_nonce_digest == hashlib.sha256(
        b"staffing-alias-nonce-v1\0" + NONCE
    ).hexdigest()
    assert {
        row.worker_alias for row in first.provider_payload.workers
    }.isdisjoint(row.worker_alias for row in second.provider_payload.workers)
    assert {
        row.assignment_alias for row in first.provider_payload.assignments
    }.isdisjoint(row.assignment_alias for row in second.provider_payload.assignments)


@pytest.mark.parametrize("nonce", [None, "x" * 32, b"", b"short", bytearray(NONCE)])
def test_alias_nonce_requires_exact_bytes_and_minimum_entropy(nonce):
    error = _assert_error(
        "invalid_alias_nonce",
        project_generation_request,
        _basis(),
        alias_nonce=nonce,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        language="en",
    )
    assert str(error) == "invalid_alias_nonce: minimum 16 bytes"


def test_projection_includes_unassigned_workers_and_exact_assignment_rules():
    projection = _project()
    assert len(projection.basis_snapshot.assignments) == 1
    assert len(projection.provider_payload.workers) == 2
    wire = provider_wire_primitive(
        projection.basis_snapshot, projection.provider_payload
    )
    assert wire["assignment_rules"] == [
        {
            "role_code": "COACH",
            "area_code": "TEE_B",
            "required_skill_codes": ["BALL_PICKING", "COACHING"],
        },
        {
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_A",
            "required_skill_codes": ["BALL_PICKING"],
        },
    ]
    candidate = _valid_result(projection)
    decoded = decode_provider_candidates(candidate, projection)
    assert decoded[0].operations[1].worker_alias == (
        projection.provider_payload.workers[1].worker_alias
    )


def test_provider_wire_and_messages_do_not_expose_local_identity_or_metadata():
    projection = _project()
    wire_text = canonical_json(
        provider_wire_primitive(
            projection.basis_snapshot, projection.provider_payload
        )
    )
    message_text = canonical_json(build_prompt(projection))
    combined = wire_text + message_text
    forbidden = (
        "secret-staff-001",
        "secret-staff-002",
        "robot",
        "edge",
        "directive",
        "api_key",
        projection.basis_snapshot.basis.basis_digest,
        projection.basis_snapshot.basis.roster_digest,
        projection.basis_snapshot.basis.exception_set_digest,
        projection.basis_snapshot.basis.effective_plan_digest,
        NONCE.decode("ascii"),
        projection.alias_nonce_digest,
    )
    assert not any(item in combined for item in forbidden)
    assert "basis_digest" not in wire_text
    assert set(json.loads(wire_text)) == {
        "service_date",
        "site_timezone",
        "workers",
        "assignment_rules",
        "assignments",
        "availability",
        "unavailable",
        "coverage",
        "prompt_template_version",
        "language",
    }
    representation = repr(projection)
    assert representation.startswith("GenerationProjection(input_digest=")
    assert not any(item in representation for item in forbidden)


def test_projection_errors_do_not_leak_real_ids_or_nonce_digest():
    projection = _project()
    error = _assert_error(
        "staffing_invalid_evidence",
        _direct_projection,
        projection,
        input_digest="0" * 64,
    )
    text = str(error)
    assert "secret-staff" not in text
    assert projection.alias_nonce_digest not in text
    assert NONCE.decode("ascii") not in text


def test_alias_maps_are_position_bound_to_provider_rows_for_workers_and_assignments():
    worker_projection = _project()
    worker_pairs = worker_projection.worker_alias_to_staff_id
    swapped_worker_aliases = (
        (worker_pairs[1][0], worker_pairs[0][1]),
        (worker_pairs[0][0], worker_pairs[1][1]),
    )
    _assert_error(
        "staffing_invalid_evidence",
        _direct_projection,
        worker_projection,
        worker_alias_to_staff_id=swapped_worker_aliases,
    )

    assignment_projection = _project(_basis(_two_worker_payload(second_assignment=True)))
    assignment_pairs = assignment_projection.assignment_alias_to_assignment_id
    swapped_assignment_aliases = (
        (assignment_pairs[1][0], assignment_pairs[0][1]),
        (assignment_pairs[0][0], assignment_pairs[1][1]),
    )
    _assert_error(
        "staffing_invalid_evidence",
        _direct_projection,
        assignment_projection,
        assignment_alias_to_assignment_id=swapped_assignment_aliases,
    )


def test_projection_validation_binds_payload_basis_language_template_and_outer_fields():
    projection = _project()
    assert validate_generation_projection(projection) is projection
    assert validate_generation_projection(
        projection,
        outer_prompt_template_version=PROMPT_TEMPLATE_VERSION,
        outer_language="zh-CN",
    ) is projection
    for changes in (
        {
            "provider_payload": replace(
                projection.provider_payload, basis_digest="0" * 64
            )
        },
        {
            "provider_payload": replace(
                projection.provider_payload, language="en"
            )
        },
        {"alias_nonce_digest": "not-a-digest"},
    ):
        _assert_error(
            "staffing_invalid_evidence", _direct_projection, projection, **changes
        )
    _assert_error(
        "staffing_invalid_evidence",
        validate_generation_projection,
        projection,
        outer_prompt_template_version="other/v1",
    )
    _assert_error(
        "staffing_invalid_evidence",
        validate_generation_projection,
        projection,
        outer_language="en",
    )
    for outer_fields in (
        {"outer_prompt_template_version": None},
        {"outer_language": None},
        {
            "outer_prompt_template_version": None,
            "outer_language": None,
        },
    ):
        _assert_error(
            "staffing_invalid_evidence",
            validate_generation_projection,
            projection,
            **outer_fields,
        )


def _timezone_basis(name: str, service_date: str, weekday: int) -> BasisSnapshot:
    payload = roster_import_request()
    payload["site_timezone"] = name
    payload["effective_from_local_date"] = service_date
    for collection in ("availability", "regular_assignments", "coverage"):
        for row in payload[collection]:
            row["weekday"] = weekday
    return _basis(payload)


def test_provider_wire_renders_site_local_offsets_and_true_utc_z():
    shanghai = _project(_basis())
    shanghai_wire = provider_wire_primitive(
        shanghai.basis_snapshot, shanghai.provider_payload
    )
    assert shanghai_wire["assignments"][0]["start_at"] == (
        "2026-10-05T09:00:00+08:00"
    )
    assert shanghai_wire["service_date"] == "2026-10-05"
    assert shanghai_wire["site_timezone"] == "Asia/Shanghai"

    utc_projection = _project(_timezone_basis("UTC", "2026-10-05", 0))
    utc_wire = provider_wire_primitive(
        utc_projection.basis_snapshot, utc_projection.provider_payload
    )
    assert utc_wire["assignments"][0]["start_at"].endswith("Z")
    assert "+00:00" not in canonical_json(utc_wire)
    assert "-00:00" not in canonical_json(utc_wire)


def test_provider_wire_preserves_both_new_york_fallback_offsets():
    payload = roster_import_request()
    payload["site_timezone"] = "America/New_York"
    payload["effective_from_local_date"] = "2026-11-01"
    payload["workers"][0]["max_daily_minutes"] = 600
    payload["availability"][0].update(
        {"weekday": 6, "start_local": "00:00", "end_local": "03:00"}
    )
    payload["regular_assignments"] = []
    payload["coverage"][0].update(
        {"weekday": 6, "start_local": "00:00", "end_local": "03:00"}
    )
    basis = _basis(payload)
    cross_fold = Assignment(
        "assignment-fold",
        basis.workers[0].staff_id,
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        datetime(2026, 11, 1, 6, 15, tzinfo=UTC),
    )
    projection = _project(replace(basis, assignments=(cross_fold,)))
    row = provider_wire_primitive(
        projection.basis_snapshot, projection.provider_payload
    )["assignments"][0]
    assert row["start_at"] == "2026-11-01T01:30:00-04:00"
    assert row["end_at"] == "2026-11-01T01:15:00-05:00"


@pytest.mark.parametrize(
    "call",
    [
        lambda projection, payload: provider_wire_primitive(
            projection.basis_snapshot, payload
        ),
        lambda projection, payload: canonical_generation_input(
            projection.basis_snapshot,
            payload,
            PROMPT_TEMPLATE_VERSION,
        ),
    ],
)
def test_provider_wire_totalizes_forged_timezone_staffing_errors(call):
    projection = _project()
    hostile_zone = _StatefulForgedTimezone()
    hostile_assignment = replace(
        projection.provider_payload.assignments[0],
        start_at=datetime(2026, 10, 5, 9, 0, tzinfo=hostile_zone),
    )
    payload = replace(
        projection.provider_payload,
        assignments=(hostile_assignment,),
    )

    error = _assert_error("staffing_invalid_evidence", call, projection, payload)
    assert str(error) == "staffing_invalid_evidence: provider timestamp"
    assert "SECRET-ID" not in str(error)


def test_projection_rejects_fold_tamper_even_with_recomputed_input_digest():
    payload = roster_import_request()
    payload["site_timezone"] = "America/New_York"
    payload["effective_from_local_date"] = "2026-11-01"
    payload["workers"][0]["max_daily_minutes"] = 600
    payload["availability"][0].update(
        {"weekday": 6, "start_local": "00:00", "end_local": "03:00"}
    )
    payload["regular_assignments"] = []
    payload["coverage"][0].update(
        {"weekday": 6, "start_local": "00:00", "end_local": "03:00"}
    )
    basis = _basis(payload)
    zone = ZoneInfo("America/New_York")
    assignment = Assignment(
        "assignment-fold-tamper",
        basis.workers[0].staff_id,
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 11, 1, 1, 0, tzinfo=zone, fold=0),
        datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0),
    )
    projection = _project(replace(basis, assignments=(assignment,)))
    forged_start = datetime(2026, 11, 1, 1, 0, tzinfo=zone, fold=1)
    assert forged_start == assignment.start_at
    assert forged_start.astimezone(UTC) != assignment.start_at.astimezone(UTC)
    forged_assignment = replace(
        projection.provider_payload.assignments[0], start_at=forged_start
    )
    forged_payload = replace(
        projection.provider_payload, assignments=(forged_assignment,)
    )

    forged_wire = provider_wire_primitive(
        projection.basis_snapshot, projection.provider_payload
    )
    forged_wire["assignments"][0]["start_at"] = "2026-11-01T01:00:00-05:00"
    semantic = canonical_generation_input(
        projection.basis_snapshot,
        projection.provider_payload,
        PROMPT_TEMPLATE_VERSION,
    )
    semantic["messages"] = (
        semantic["messages"][0],
        {"role": "user", "content": canonical_json(forged_wire)},
    )
    forged_digest = stable_digest(to_primitive(semantic))

    _assert_error(
        "staffing_invalid_evidence",
        GenerationProjection,
        projection.basis_snapshot,
        forged_payload,
        projection.worker_alias_to_staff_id,
        projection.assignment_alias_to_assignment_id,
        projection.alias_nonce_digest,
        forged_digest,
    )


@pytest.mark.parametrize("field", ["worker_limit", "coverage_minimum"])
def test_projection_rejects_bool_int_and_float_int_payload_aliases(field):
    projection = _project()
    forged_wire = provider_wire_primitive(
        projection.basis_snapshot, projection.provider_payload
    )
    if field == "worker_limit":
        forged_row = replace(
            projection.provider_payload.workers[0], max_daily_minutes=480.0
        )
        forged_payload = replace(
            projection.provider_payload,
            workers=(forged_row, *projection.provider_payload.workers[1:]),
        )
        forged_wire["workers"][0]["max_daily_minutes"] = 480.0
    else:
        forged_row = replace(
            projection.provider_payload.coverage[0], minimum_staff=True
        )
        forged_payload = replace(
            projection.provider_payload,
            coverage=(forged_row, *projection.provider_payload.coverage[1:]),
        )
        forged_wire["coverage"][0]["minimum_staff"] = True
    semantic = canonical_generation_input(
        projection.basis_snapshot,
        projection.provider_payload,
        PROMPT_TEMPLATE_VERSION,
    )
    semantic["messages"] = (
        semantic["messages"][0],
        {"role": "user", "content": canonical_json(forged_wire)},
    )
    forged_digest = stable_digest(to_primitive(semantic))
    _assert_error(
        "staffing_invalid_evidence",
        GenerationProjection,
        projection.basis_snapshot,
        forged_payload,
        projection.worker_alias_to_staff_id,
        projection.assignment_alias_to_assignment_id,
        projection.alias_nonce_digest,
        forged_digest,
    )


def test_output_schema_is_recursively_frozen_portable_and_closed():
    primitive = schema_primitive(STAFFING_SUGGESTION_OUTPUT_SCHEMA)
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "anyOf",
        "enum",
    }
    seen_any_of = False

    def visit(schema: object) -> None:
        nonlocal seen_any_of
        assert type(schema) is dict
        assert set(schema).issubset(allowed)
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
        if "anyOf" in schema:
            seen_any_of = True
            for child in schema["anyOf"]:
                visit(child)
        if "properties" in schema:
            for child in schema["properties"].values():
                visit(child)
        if "items" in schema:
            visit(schema["items"])

    visit(primitive)
    assert seen_any_of
    operation_schema = primitive["properties"]["candidates"]["items"][
        "properties"
    ]["operations"]["items"]["anyOf"]
    assert all(
        branch["properties"]["operation"] == {"type": "string"}
        for branch in operation_schema
    )
    forbidden = {
        "$schema",
        "$id",
        "$ref",
        "$dynamicRef",
        "oneOf",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "dependentRequired",
        "dependentSchemas",
        "pattern",
        "maxLength",
        "minLength",
        "maxItems",
        "minItems",
    }
    assert forbidden.isdisjoint(gateway_canonical_json(primitive).split('"'))
    with pytest.raises(TypeError):
        STAFFING_SUGGESTION_OUTPUT_SCHEMA["type"] = "array"
    with pytest.raises(TypeError):
        STAFFING_SUGGESTION_OUTPUT_SCHEMA["properties"]["x"] = {}


@pytest.mark.parametrize(
    "adapter_type,provider,path,strict_path",
    [
        (
            KimiAdapter,
            Provider.KIMI,
            ("response_format", "json_schema", "schema"),
            ("response_format", "json_schema", "strict"),
        ),
        (
            OpenAIAdapter,
            Provider.OPENAI,
            ("text", "format", "schema"),
            ("text", "format", "strict"),
        ),
        (
            AnthropicAdapter,
            Provider.ANTHROPIC,
            ("tools", 0, "input_schema"),
            ("tools", 0, "strict"),
        ),
    ],
)
def test_all_prepared_bodies_embed_the_exact_common_schema_and_strict(
    adapter_type, provider, path, strict_path
):
    projection = _project()
    request = _gateway_request(projection)
    adapter = adapter_type(
        config=ProviderConfig(provider, "pinned-model", "secret-key"),
        transport=_UnusedTransport(),
    )
    body = json.loads(adapter.prepare(request).body)

    def at(value, parts):
        for part in parts:
            value = value[part]
        return value

    assert at(body, path) == schema_primitive(STAFFING_SUGGESTION_OUTPUT_SCHEMA)
    assert at(body, strict_path) is True


@pytest.mark.parametrize("adapter_type,provider", _ADAPTER_ROWS)
@pytest.mark.parametrize("violation", ["extra_key", "bad_index"])
def test_each_adapter_discards_schema_expressible_failures_before_domain_decode(
    adapter_type, provider, violation
):
    projection = _project()
    value = _valid_result(projection)
    if violation == "extra_key":
        value["unexpected"] = True
    else:
        value["candidates"][0]["candidate_index"] = 3

    outcome = _send_provider_value(adapter_type, provider, projection, value)

    assert outcome.status is GenerationStatus.INVALID_RESPONSE
    assert outcome.failure_code is FailureCode.SCHEMA_MISMATCH
    assert outcome.decoded_json is None
    assert outcome.output_digest is None


@pytest.mark.parametrize("adapter_type,provider", _ADAPTER_ROWS)
@pytest.mark.parametrize(
    "case,expected_code",
    [
        ("third_candidate", "invalid_provider_shape"),
        ("operation_value", "invalid_provider_shape"),
        ("oversized_text", "invalid_provider_shape"),
        ("bad_code", "invalid_provider_shape"),
        ("bad_timestamp", "invalid_provider_timestamp"),
    ],
)
def test_each_adapter_passes_schema_valid_failures_to_the_domain_classifier(
    adapter_type, provider, case, expected_code
):
    projection = _project()
    value = _domain_failure_value(projection, case)

    outcome = _send_provider_value(adapter_type, provider, projection, value)

    assert outcome.status is GenerationStatus.SUCCEEDED
    assert outcome.failure_code is None
    assert outcome.decoded_json is not None
    _assert_error(
        expected_code,
        decode_provider_candidates,
        outcome.decoded_json,
        projection,
    )


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize(
    "case,expected_code",
    [
        ("third_candidate", "invalid_provider_shape"),
        ("operation_value", "invalid_provider_shape"),
        ("oversized_text", "invalid_provider_shape"),
        ("bad_code", "invalid_provider_shape"),
        ("bad_timestamp", "invalid_provider_timestamp"),
    ],
)
def test_representative_business_failures_match_raw_and_frozen_trees(
    frozen, case, expected_code
):
    projection = _project()
    value = _domain_failure_value(projection, case)
    _assert_error(
        expected_code,
        decode_provider_candidates,
        _frozen(value) if frozen else value,
        projection,
    )


def test_canonical_input_matches_real_gateway_request_and_prompt():
    projection = _project()
    semantic = canonical_generation_input(
        projection.basis_snapshot,
        projection.provider_payload,
        PROMPT_TEMPLATE_VERSION,
    )
    request = _gateway_request(projection)
    assert semantic == {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "messages": build_prompt(projection),
        "output_schema": schema_primitive(STAFFING_SUGGESTION_OUTPUT_SCHEMA),
        "max_output_tokens": 2048,
    }
    assert MAX_OUTPUT_TOKENS == 2048
    assert projection.input_digest == request.canonical_input_digest
    assert to_primitive(semantic) == {
        "template_version": request.template_version,
        "messages": [
            {"role": item.role.value, "content": item.content}
            for item in request.messages
        ],
        "output_schema": schema_primitive(request.output_schema),
        "max_output_tokens": request.max_output_tokens,
    }
    serialized = canonical_json(semantic)
    assert NONCE.decode("ascii") not in serialized
    assert projection.alias_nonce_digest not in serialized


@pytest.mark.parametrize(
    "drift",
    [
        "template",
        "messages",
        "schema",
        "token_budget",
        "service_date",
        "site_timezone",
        "wire_time",
    ],
)
def test_canonical_input_digest_changes_for_every_frozen_semantic_field(drift):
    projection = _project()
    semantic = canonical_generation_input(
        projection.basis_snapshot,
        projection.provider_payload,
        PROMPT_TEMPLATE_VERSION,
    )
    mutable = json.loads(canonical_json(to_primitive(semantic)))
    assert stable_digest(mutable) == projection.input_digest

    if drift == "template":
        mutable["template_version"] = "staffing-adjustment/drift"
    elif drift == "messages":
        mutable["messages"][0]["content"] += " drift"
    elif drift == "schema":
        candidate = mutable["output_schema"]["properties"]["candidates"]["items"]
        candidate["properties"]["candidate_index"]["enum"] = [1]
    elif drift == "token_budget":
        mutable["max_output_tokens"] += 1
    else:
        wire = json.loads(mutable["messages"][1]["content"])
        if drift == "service_date":
            wire["service_date"] = "2026-10-06"
        elif drift == "site_timezone":
            wire["site_timezone"] = "Asia/Tokyo"
        else:
            wire["assignments"][0]["start_at"] = (
                "2026-10-05T09:01:00+08:00"
            )
        mutable["messages"][1]["content"] = canonical_json(wire)

    assert stable_digest(mutable) != projection.input_digest


def test_generation_projection_is_frozen_and_prompt_is_fixed():
    projection = _project()
    with pytest.raises(FrozenInstanceError):
        projection.input_digest = "0" * 64
    system, user = build_prompt(projection)
    assert system["role"] == "system"
    assert "untrusted data" in system["content"]
    assert "uppercase ADD or REMOVE" in system["content"]
    assert "IANA" in system["content"]
    assert user == {
        "role": "user",
        "content": canonical_json(
            provider_wire_primitive(
                projection.basis_snapshot, projection.provider_payload
            )
        ),
    }


@pytest.mark.parametrize("frozen", [False, True])
def test_decoder_accepts_raw_and_gateway_frozen_trees(frozen):
    projection = _project()
    value = _valid_result(projection)
    decoded = decode_provider_candidates(_frozen(value) if frozen else value, projection)
    assert len(decoded) == 1
    assert decoded[0].candidate_index == 1
    assert tuple(item.operation for item in decoded[0].operations) == (
        "REMOVE",
        "ADD",
    )
    assert decoded[0].rationale == "<b>可执行</b> **plan**"


@pytest.mark.parametrize(
    "spelling,expected",
    [
        ("add", "ADD"),
        ("Add", "ADD"),
        ("aDd", "ADD"),
        ("ADD", "ADD"),
        ("remove", "REMOVE"),
        ("Remove", "REMOVE"),
        ("rEmOvE", "REMOVE"),
        ("REMOVE", "REMOVE"),
    ],
)
def test_decoder_normalizes_only_ascii_case_variants(spelling, expected):
    projection = _project()
    value = _valid_result(projection)
    operation = value["candidates"][0]["operations"][0 if expected == "REMOVE" else 1]
    value["candidates"][0]["operations"] = [operation]
    operation["operation"] = spelling
    decoded = decode_provider_candidates(value, projection)
    assert decoded[0].operations[0].operation == expected


@pytest.mark.parametrize("operation", ["Add ", "ＡＤＤ", "Straße", 1, True, None])
def test_decoder_rejects_non_exact_ascii_operation_values(operation):
    projection = _project()
    value = _valid_result(projection)
    value["candidates"][0]["operations"][1]["operation"] = operation
    _assert_error("invalid_provider_shape", decode_provider_candidates, value, projection)


def _shape_mutations(projection: GenerationProjection):
    third = _valid_result(projection)
    third["candidates"] *= 3
    third["candidates"][0]["candidate_index"] = 1
    third["candidates"][1]["candidate_index"] = 2
    third["candidates"][2]["candidate_index"] = 2
    too_many_operations = _valid_result(projection)
    too_many_operations["candidates"][0]["operations"] = [
        dict(too_many_operations["candidates"][0]["operations"][0])
        for _ in range(33)
    ]
    unknown = _valid_result(projection)
    unknown["candidates"][0]["surprise"] = True
    bad_index = _valid_result(projection)
    bad_index["candidates"][0]["candidate_index"] = 3
    bool_index = _valid_result(projection)
    bool_index["candidates"][0]["candidate_index"] = True
    missing = _valid_result(projection)
    del missing["candidates"][0]["rationale"]
    mismatch = _valid_result(projection)
    del mismatch["candidates"][0]["operations"][1]["worker_alias"]
    over_rationale = _valid_result(projection)
    over_rationale["candidates"][0]["rationale"] = "x" * 281
    over_warning = _valid_result(projection)
    over_warning["candidates"][0]["operational_warnings"] = ["x" * 201]
    too_many_warnings = _valid_result(projection)
    too_many_warnings["candidates"][0]["operational_warnings"] = ["x"] * 6
    noncanonical_code = _valid_result(projection)
    noncanonical_code["candidates"][0]["operations"][1]["role_code"] = "coach"
    duplicate_index = _valid_result(projection)
    duplicate_index["candidates"].append(
        dict(duplicate_index["candidates"][0])
    )
    noncontiguous = _valid_result(projection)
    noncontiguous["candidates"][0]["candidate_index"] = 2
    return (
        third,
        too_many_operations,
        unknown,
        bad_index,
        bool_index,
        missing,
        mismatch,
        over_rationale,
        over_warning,
        too_many_warnings,
        noncanonical_code,
        duplicate_index,
        noncontiguous,
    )


def test_decoder_rejects_all_closed_shape_count_and_lexical_violations():
    projection = _project()
    for value in _shape_mutations(projection):
        _assert_error(
            "invalid_provider_shape", decode_provider_candidates, value, projection
        )


@pytest.mark.parametrize(
    "case,expected_code",
    [
        ("forbidden", "provider_sensitive_key"),
        ("rationale", "invalid_provider_shape"),
        ("warning", "invalid_provider_shape"),
        ("third", "invalid_provider_shape"),
        ("second_candidate", "invalid_provider_shape"),
    ],
)
def test_whole_result_is_staged_before_any_domain_record_is_constructed(
    monkeypatch, case, expected_code
):
    projection = _project()
    value = _valid_result(projection)
    if case == "forbidden":
        value["candidates"][0]["operations"][0]["metadata"] = "secret"
    elif case == "rationale":
        value["candidates"][0]["rationale"] = "x" * 281
    elif case == "warning":
        value["candidates"][0]["operational_warnings"] = ["x" * 201]
    elif case == "third":
        candidate = json.loads(canonical_json(value["candidates"][0]))
        value["candidates"] = [candidate, dict(candidate), dict(candidate)]
        value["candidates"][0]["candidate_index"] = 1
        value["candidates"][1]["candidate_index"] = 2
        value["candidates"][2]["candidate_index"] = 2
    else:
        second = json.loads(canonical_json(value["candidates"][0]))
        second["candidate_index"] = 2
        second["operational_warnings"] = ["x" * 201]
        value["candidates"].append(second)

    constructed: list[str] = []

    def trap(name):
        def construct(*args, **kwargs):
            constructed.append(name)
            raise AssertionError(f"{name} constructed before whole-result validation")

        return construct

    monkeypatch.setattr(projection_module, "RemoveOperation", trap("REMOVE"))
    monkeypatch.setattr(projection_module, "AddOperation", trap("ADD"))
    monkeypatch.setattr(projection_module, "CandidatePatch", trap("candidate"))
    _assert_error(expected_code, decode_provider_candidates, value, projection)
    assert constructed == []


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-05T09:00:00",
        "2026-10-05 09:00:00+08:00",
        "2026-10-05T09:00:00z",
        "2026-10-05T01:00:00+00:00",
        "2026-10-05T01:00:00-00:00",
        "2026-10-05T09:00:00.1234567+08:00",
        "2026-13-05T09:00:00+08:00",
        "2026-10-05T09:00:00+99:99",
        "2026-10-05T09:00:00+08:60",
    ],
)
def test_decoder_rejects_lexical_and_calendar_invalid_timestamps(timestamp):
    projection = _project()
    value = _valid_result(projection)
    value["candidates"][0]["operations"][1]["start_at"] = timestamp
    _assert_error(
        "invalid_provider_timestamp", decode_provider_candidates, value, projection
    )


def test_decoder_accepts_fractional_nonminute_timestamp_for_task3_semantics():
    projection = _project()
    value = _valid_result(projection)
    value["candidates"][0]["operations"][1]["start_at"] = (
        "2026-10-05T09:00:01.123456+08:00"
    )
    candidate = decode_provider_candidates(value, projection)[0]
    assert candidate.operations[1].start_at.second == 1
    validation = validate_candidates(
        projection.basis_snapshot,
        (candidate,),
        worker_alias_to_staff_id=projection.worker_alias_to_staff_id,
        assignment_alias_to_assignment_id=projection.assignment_alias_to_assignment_id,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
    )
    assert validation[0].rejection_codes == ("INVALID_MINUTE_PRECISION",)


def test_unknown_alias_and_duplicate_remove_are_candidate_set_failures():
    projection = _project()
    unknown = _valid_result(projection)
    unknown["candidates"][0]["operations"][1]["worker_alias"] = "worker_unknown"
    _assert_error(
        "invalid_candidate_set", decode_provider_candidates, unknown, projection
    )
    duplicate = _valid_result(projection)
    duplicate["candidates"][0]["operations"] = [
        duplicate["candidates"][0]["operations"][0],
        dict(duplicate["candidates"][0]["operations"][0]),
    ]
    _assert_error(
        "invalid_candidate_set", decode_provider_candidates, duplicate, projection
    )


def test_two_candidates_may_reuse_baseline_alias_and_semantic_rejection_is_local():
    projection = _project()
    value = _valid_result(projection)
    first = value["candidates"][0]
    first["operations"] = [first["operations"][1]]
    first["operations"][0]["start_at"] = "2026-10-05T09:00:01+08:00"
    second = {
        "candidate_index": 2,
        "operations": [],
        "rationale": "keep baseline",
        "operational_warnings": [],
    }
    value["candidates"].append(second)
    candidates = decode_provider_candidates(value, projection)
    results = validate_candidates(
        projection.basis_snapshot,
        candidates,
        worker_alias_to_staff_id=projection.worker_alias_to_staff_id,
        assignment_alias_to_assignment_id=projection.assignment_alias_to_assignment_id,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
    )
    assert results[0].rejection_codes == ("INVALID_MINUTE_PRECISION",)
    assert results[1].valid is True


@pytest.mark.parametrize("key", sorted(FORBIDDEN_KEYS))
def test_forbidden_keys_win_over_shape_errors_at_every_reachable_depth(key):
    projection = _project()
    for value in (
        {"unexpected": True, key: "secret"},
        {"candidates": [{"unexpected": {key: "secret"}}]},
        {"candidates": [{"operations": [{key: "secret"}]}]},
    ):
        _assert_error(
            "provider_sensitive_key", decode_provider_candidates, value, projection
        )


def test_forbidden_key_precedes_non_string_and_shape_failures():
    value = {1: "wrong", "metadata": "secret", "unexpected": True}
    _assert_error("provider_sensitive_key", scan_and_detach_provider_tree, value)


@pytest.mark.parametrize("frozen", [False, True])
def test_reachable_forbidden_key_precedes_an_invalid_earlier_sibling(frozen):
    value = {"a": object(), "z": {"metadata": "secret"}}
    candidate = _frozen(value) if frozen else value
    _assert_error("provider_sensitive_key", scan_and_detach_provider_tree, candidate)


class _DuplicateKeys(Mapping):
    def __iter__(self):
        return iter(("x", "x"))

    def __len__(self):
        return 2

    def __getitem__(self, key):
        return 1


class _ExplodingMapping(Mapping):
    def __init__(self, failure: BaseException):
        self.failure = failure

    def __iter__(self):
        raise self.failure

    def __len__(self):
        raise self.failure

    def __getitem__(self, key):
        raise self.failure


class _MalformedItems(Mapping):
    def __init__(self, *, with_forbidden: bool = False):
        self.with_forbidden = with_forbidden

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __getitem__(self, key):
        raise KeyError(key)

    def items(self):
        prefix = [("metadata", "secret")] if self.with_forbidden else []
        return [*prefix, "not-an-entry-pair"]


class _StatefulMapping(Mapping):
    def __init__(self, source: dict[str, object]):
        self.source = source
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations > 1:
            raise RuntimeError("second traversal leaked")
        return iter(self.source)

    def __len__(self):
        return len(self.source)

    def __getitem__(self, key):
        return self.source[key]


class _OversizedItemsMapping(Mapping):
    def __init__(self, size: int):
        self.size = size
        self.yielded = 0

    def __iter__(self):
        return iter(())

    def __len__(self):
        return self.size

    def __getitem__(self, key):
        raise KeyError(key)

    def items(self):
        for index in range(self.size):
            self.yielded += 1
            yield (f"key_{index}", None)


class _LengthExplodingMapping(Mapping):
    def __init__(self, failure: Exception):
        self.failure = failure

    def __iter__(self):
        return iter(("candidates",))

    def __len__(self):
        raise self.failure

    def __getitem__(self, key):
        if key == "candidates":
            return []
        raise KeyError(key)


class _AccessExplodingMapping(Mapping):
    def __init__(self, stage: str, failure: Exception):
        self.stage = stage
        self.failure = failure

    def __iter__(self):
        if self.stage == "iteration":
            raise self.failure
        return iter(("candidates",))

    def __len__(self):
        if self.stage == "length":
            raise self.failure
        return 1

    def __getitem__(self, key):
        if self.stage == "get":
            raise self.failure
        if key == "candidates":
            return []
        raise KeyError(key)

    def items(self):
        if self.stage == "items":
            raise self.failure
        return super().items()


class _ForgedStaffingError(StaffingError):
    pass


class _HostileAccessError(Exception):
    pass


@pytest.mark.parametrize(
    "stage",
    ["items", "iteration", "get", "length"],
)
@pytest.mark.parametrize(
    "failure",
    [
        _HostileAccessError("hostile"),
        _ForgedStaffingError("provider_sensitive_key", "forged"),
    ],
)
def test_each_hostile_mapping_access_is_totalized_without_text_leakage(
    stage, failure
):
    proxy = MappingProxyType(_AccessExplodingMapping(stage, failure))
    error = _assert_error("invalid_provider_shape", scan_and_detach_provider_tree, proxy)
    assert str(error) == "invalid_provider_shape: provider tree"
    assert "hostile" not in str(error)
    assert "forged" not in str(error)


def test_hostile_mapping_malformed_items_are_totalized():
    proxy = MappingProxyType(_MalformedItems())
    error = _assert_error("invalid_provider_shape", scan_and_detach_provider_tree, proxy)
    assert str(error) == "invalid_provider_shape: provider tree"


def test_forbidden_key_precedes_a_hostile_malformed_entry_pair():
    proxy = MappingProxyType(_MalformedItems(with_forbidden=True))
    _assert_error("provider_sensitive_key", scan_and_detach_provider_tree, proxy)


def test_scan_does_not_catch_base_exception():
    proxy = MappingProxyType(_ExplodingMapping(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        scan_and_detach_provider_tree(proxy)


def test_scan_reads_stateful_mapping_once_and_returns_fresh_builtins():
    projection = _project()
    source = _valid_result(projection)
    backing = _StatefulMapping(source)
    proxy = MappingProxyType(backing)
    detached = scan_and_detach_provider_tree(proxy)
    assert backing.iterations == 1
    assert type(detached) is dict
    assert type(detached["candidates"]) is list
    source["metadata"] = "would only appear on a second traversal"
    assert decode_provider_candidates(detached, projection)
    assert backing.iterations == 1


def test_scan_stops_hostile_mapping_iteration_at_the_occurrence_bound():
    backing = _OversizedItemsMapping(10_000)
    proxy = MappingProxyType(backing)
    _assert_error("invalid_provider_shape", scan_and_detach_provider_tree, proxy)
    assert backing.yielded == 4096


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("LEN-LEAK"),
        _ForgedStaffingError("provider_sensitive_key", "forged"),
    ],
)
def test_hostile_mapping_length_is_totalized_without_text_leakage(failure):
    proxy = MappingProxyType(_LengthExplodingMapping(failure))
    error = _assert_error("invalid_provider_shape", scan_and_detach_provider_tree, proxy)
    assert str(error) == "invalid_provider_shape: provider tree"
    assert "LEN-LEAK" not in str(error)
    assert "forged" not in str(error)


def test_scan_rejects_custom_containers_duplicate_keys_cycles_depth_and_nodes():
    _assert_error(
        "invalid_provider_shape",
        scan_and_detach_provider_tree,
        MappingProxyType(_DuplicateKeys()),
    )
    _assert_error("invalid_provider_shape", scan_and_detach_provider_tree, UserDict())
    _assert_error("invalid_provider_shape", scan_and_detach_provider_tree, UserList())
    cycle: list[object] = []
    cycle.append(cycle)
    _assert_error("invalid_provider_shape", scan_and_detach_provider_tree, cycle)
    deep: object = None
    for _ in range(22):
        deep = [deep]
    _assert_error("invalid_provider_shape", scan_and_detach_provider_tree, deep)
    _assert_error(
        "invalid_provider_shape",
        scan_and_detach_provider_tree,
        [None] * 4097,
    )


def test_scan_accepts_exactly_twenty_container_levels_and_rejects_twenty_one():
    def nested(levels: int) -> object:
        value: object = None
        for _ in range(levels):
            value = [value]
        return value

    assert scan_and_detach_provider_tree(nested(20)) == nested(20)
    _assert_error(
        "invalid_provider_shape", scan_and_detach_provider_tree, nested(21)
    )


def test_scan_allows_shared_immutable_containers_in_separate_branches():
    shared = ()
    assert scan_and_detach_provider_tree({"a": shared, "b": shared}) == {
        "a": [],
        "b": [],
    }


def test_decoder_4096_business_bound_precedes_warning_and_operation_bounds():
    projection = _project()
    warnings = _valid_result(projection)
    warnings["candidates"][0]["operational_warnings"] = [""] * 4097
    _assert_error(
        "invalid_provider_shape", decode_provider_candidates, warnings, projection
    )
    operations = _valid_result(projection)
    remove = operations["candidates"][0]["operations"][0]
    operations["candidates"][0]["operations"] = [dict(remove) for _ in range(1400)]
    _assert_error(
        "invalid_provider_shape", decode_provider_candidates, operations, projection
    )


def test_corrupt_projection_wins_over_malformed_provider_value():
    projection = _forged_projection(_project(), input_digest="0" * 64)
    _assert_error(
        "staffing_invalid_evidence",
        decode_provider_candidates,
        {"metadata": "secret"},
        projection,
    )


def test_decoder_errors_are_limited_to_four_provider_wire_codes():
    projection = _project()
    cases = []
    cases.extend(_shape_mutations(projection))
    cases.append({"metadata": "secret"})
    bad_time = _valid_result(projection)
    bad_time["candidates"][0]["operations"][1]["start_at"] = "bad"
    cases.append(bad_time)
    bad_alias = _valid_result(projection)
    bad_alias["candidates"][0]["operations"][1]["worker_alias"] = "unknown"
    cases.append(bad_alias)
    codes = set()
    for value in cases:
        with pytest.raises(StaffingError) as raised:
            decode_provider_candidates(value, projection)
        codes.add(raised.value.code)
    assert codes == {
        "invalid_provider_shape",
        "provider_sensitive_key",
        "invalid_provider_timestamp",
        "invalid_candidate_set",
    }


def test_common_schema_catches_expressible_failures_but_leaves_parser_bounds():
    schema = schema_primitive(STAFFING_SUGGESTION_OUTPUT_SCHEMA)
    projection = _project()
    valid = _valid_result(projection)
    extra = _valid_result(projection)
    extra["candidates"][0]["extra"] = True
    bad_index = _valid_result(projection)
    bad_index["candidates"][0]["candidate_index"] = 3
    for value in (extra, bad_index):
        with pytest.raises(Exception) as raised:
            decode_validated_json(
                gateway_canonical_json(value).encode("utf-8"), schema=schema
            )
        assert getattr(raised.value, "code", None).value == "SCHEMA_MISMATCH"
    third = _valid_result(projection)
    third["candidates"] = [dict(valid["candidates"][0]) for _ in range(3)]
    third["candidates"][0]["candidate_index"] = 1
    third["candidates"][1]["candidate_index"] = 2
    third["candidates"][2]["candidate_index"] = 2
    frozen = decode_validated_json(
        gateway_canonical_json(third).encode("utf-8"), schema=schema
    )
    _assert_error(
        "invalid_provider_shape", decode_provider_candidates, frozen, projection
    )


def test_direct_projection_construction_requires_full_local_validation():
    projection = _project()
    malformed_payload = ProviderPayload(
        projection.provider_payload.basis_digest,
        (),
        projection.provider_payload.assignments,
        projection.provider_payload.availability,
        projection.provider_payload.unavailable,
        projection.provider_payload.coverage,
        projection.provider_payload.prompt_template_version,
        projection.provider_payload.language,
    )
    _assert_error(
        "staffing_invalid_evidence",
        GenerationProjection,
        projection.basis_snapshot,
        malformed_payload,
        projection.worker_alias_to_staff_id,
        projection.assignment_alias_to_assignment_id,
        projection.alias_nonce_digest,
        projection.input_digest,
    )
