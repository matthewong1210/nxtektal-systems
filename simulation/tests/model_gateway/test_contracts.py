"""The public generation boundary rejects drift and keeps sensitive data private."""

import dataclasses
import hashlib
import json

import pytest

from nxt_model_gateway import (
    FailureCode,
    DeploymentRegion,
    AttemptObserver,
    AttemptObserverError,
    AttemptRecord,
    AttemptStarted,
    GatewayContractError,
    GenerationMessage,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    MessageRole,
    Provider,
    ProviderConfig,
    TokenUsage,
    canonical_json,
    decode_validated_json,
    stable_digest,
)
from nxt_model_gateway.serialization import _freeze_json


SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["x"],
    "properties": {"x": {"type": "integer"}},
}


def request(**overrides):
    fields = dict(
        request_id="req-001", template_version="template-v1",
        messages=(GenerationMessage(MessageRole.USER, "return an object"),),
        output_schema=SCHEMA, max_output_tokens=256, deadline_budget_s=20.0,
    )
    fields.update(overrides)
    return GenerationRequest(**fields)


def test_generation_request_digest_excludes_operational_identity_and_deadline():
    first = request(request_id="req-a", deadline_budget_s=20.0)
    second = request(request_id="req-b", deadline_budget_s=7.0)
    assert first.canonical_input_digest == second.canonical_input_digest


def test_provider_config_never_reveals_api_key():
    config = ProviderConfig(Provider.OPENAI, "gpt-pinned", "fixture-secret")
    assert "fixture-secret" not in repr(config)


def test_generation_message_never_reveals_prompt_content():
    message = GenerationMessage(MessageRole.USER, "sensitive prompt")
    assert "sensitive prompt" not in repr(message)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'[1]', b'{"x":"wrong"}'])
def test_output_decoder_rejects_duplicate_keys_nan_non_object_and_schema_drift(raw):
    with pytest.raises(GatewayContractError):
        decode_validated_json(raw, schema=SCHEMA)


@pytest.mark.parametrize("schema", [
    {"$ref": "https://example.invalid/schema"}, {"$dynamicRef": "#node"},
])
def test_generation_request_rejects_remote_or_dynamic_schema_references(schema):
    with pytest.raises(GatewayContractError):
        request(output_schema=schema)


EXPECTED_FAILURE_CODES = {
    "INPUT_TOO_LARGE", "RESPONSE_TOO_LARGE", "UNSUPPORTED_CONTENT_ENCODING",
    "REDIRECT_REFUSED", "ENDPOINT_NOT_ALLOWED", "TLS_VERIFICATION_FAILED",
    "DNS_FAILURE", "CONNECT_TIMEOUT", "CONNECT_FAILED", "READ_TIMEOUT",
    "CONNECTION_INTERRUPTED", "HTTP_TIMEOUT", "RATE_LIMITED",
    "PROVIDER_UNAVAILABLE", "AUTHENTICATION_FAILED", "PERMISSION_DENIED",
    "MODEL_NOT_FOUND", "INVALID_PROVIDER_REQUEST", "PROVIDER_CLIENT_ERROR",
    "PROVIDER_REFUSED", "MALFORMED_PROVIDER_RESPONSE", "SCHEMA_MISMATCH",
    "BACKUP_UNCONFIGURED", "DEADLINE_EXHAUSTED", "PROVIDER_UNCONFIGURED",
}

EXPECTED_FIELDS = {
    GatewayContractError: ("code", "detail"),
    GenerationMessage: ("role", "content"),
    GenerationRequest: (
        "request_id", "template_version", "messages", "output_schema",
        "max_output_tokens", "deadline_budget_s", "canonical_input_digest",
    ),
    ProviderConfig: ("provider", "model_id", "api_key"),
    TokenUsage: ("input_tokens", "output_tokens", "total_tokens"),
    AttemptStarted: (
        "request_id", "attempt_index", "provider", "model_id", "input_digest", "timeout_s",
    ),
    AttemptRecord: (
        "request_id", "attempt_index", "provider", "model_id", "status",
        "failure_code", "retryable", "security_failure", "provider_request_id",
        "finish_reason", "usage", "input_digest", "output_digest",
    ),
    AttemptObserverError: ("request_id", "attempt_index", "provider", "phase"),
    GenerationResult: (
        "request_id", "status", "output", "failure_code", "selected_provider",
        "selected_model_id", "provider_request_id", "finish_reason", "usage",
        "input_digest", "output_digest", "attempts",
    ),
}


def attempt(**overrides):
    fields = dict(
        request_id="req-001", attempt_index=0, provider=Provider.OPENAI,
        model_id="gpt-pinned", status=GenerationStatus.SUCCEEDED, failure_code=None,
        retryable=False, security_failure=False, provider_request_id="provider-001",
        finish_reason="stop", usage=TokenUsage(2, 3, 5),
        input_digest=request().canonical_input_digest, output_digest=stable_digest({"x": 1}),
    )
    fields.update(overrides)
    return AttemptRecord(**fields)


def result(**overrides):
    record = attempt()
    fields = dict(
        request_id=record.request_id, status=record.status, output={"x": 1},
        failure_code=record.failure_code, selected_provider=record.provider,
        selected_model_id=record.model_id, provider_request_id=record.provider_request_id,
        finish_reason=record.finish_reason, usage=record.usage, input_digest=record.input_digest,
        output_digest=record.output_digest, attempts=(record,),
    )
    fields.update(overrides)
    return GenerationResult(**fields)


def test_public_records_have_frozen_exact_fields():
    for record_type, expected in EXPECTED_FIELDS.items():
        assert tuple(field.name for field in dataclasses.fields(record_type)) == expected
        assert record_type.__dataclass_params__.frozen is True
    instances = (
        GatewayContractError(FailureCode.SCHEMA_MISMATCH, "output schema mismatch"),
        GenerationMessage(MessageRole.USER, "hello"), request(),
        ProviderConfig(Provider.OPENAI, "pinned", "fixture-key"), TokenUsage(2, 3, 5),
        AttemptStarted("req-001", 0, Provider.OPENAI, "pinned", "a" * 64, 1.0),
        attempt(), AttemptObserverError("req-001", 0, Provider.OPENAI, "started"), result(),
    )
    for instance in instances:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, dataclasses.fields(instance)[0].name, None)


def test_public_enum_values_are_the_versioned_wire_vocabulary():
    assert {item.value for item in FailureCode} == EXPECTED_FAILURE_CODES
    assert {item.value for item in Provider} == {"KIMI", "OPENAI", "ANTHROPIC"}
    assert {item.value for item in DeploymentRegion} == {"CN", "GLOBAL"}
    assert {item.value for item in MessageRole} == {"system", "user", "assistant"}
    assert {item.value for item in GenerationStatus} == {
        "SUCCEEDED", "UNAVAILABLE", "REFUSED", "INVALID_RESPONSE", "PROVIDER_ERROR",
        "CONFIGURATION_ERROR", "SECURITY_ERROR",
    }


@pytest.mark.parametrize("changes", [
    {"template_version": "template-v2"},
    {"messages": (GenerationMessage(MessageRole.SYSTEM, "return an object"),)},
    {"messages": (GenerationMessage(MessageRole.USER, "different text"),)},
    {"output_schema": {"type": "object"}}, {"max_output_tokens": 257},
])
def test_request_digest_binds_every_semantic_field(changes):
    assert request(**changes).canonical_input_digest != request().canonical_input_digest


def test_request_digest_uses_exact_semantic_payload():
    payload = {
        "template_version": "template-v1",
        "messages": [{"role": "user", "content": "return an object"}],
        "output_schema": SCHEMA, "max_output_tokens": 256,
    }
    expected = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    assert request().canonical_input_digest == expected
    with pytest.raises(TypeError):
        request(canonical_input_digest="a" * 64)


@pytest.mark.parametrize("changes", [
    {"request_id": ""}, {"request_id": "bad id"}, {"request_id": "_bad"},
    {"request_id": "é"}, {"request_id": "a" * 129},
    {"template_version": ""}, {"template_version": "x" * 65},
    {"template_version": "v\n1"}, {"template_version": "é"},
    {"messages": ()}, {"messages": []}, {"messages": ("hello",)},
    {"messages": (GenerationMessage(MessageRole.USER, "hello"),) * 65},
    {"max_output_tokens": 0}, {"max_output_tokens": 16385},
    {"max_output_tokens": True}, {"max_output_tokens": 1.0},
    {"deadline_budget_s": 0}, {"deadline_budget_s": -1},
    {"deadline_budget_s": float("nan")}, {"deadline_budget_s": float("inf")},
    {"deadline_budget_s": True}, {"deadline_budget_s": "1"},
])
def test_invalid_request_fields_fail_with_redacted_contract_error(changes):
    with pytest.raises(GatewayContractError) as raised:
        request(**changes)
    assert isinstance(raised.value.code, FailureCode)


@pytest.mark.parametrize("role,content", [
    ("user", "hello"), (MessageRole.USER, ""), (MessageRole.USER, "a" * 32769),
    (MessageRole.USER, 1), (MessageRole.USER, "\ud800"),
])
def test_message_validates_role_and_unicode_text(role, content):
    with pytest.raises(GatewayContractError):
        GenerationMessage(role, content)


def test_request_accepts_inclusive_boundaries():
    message = GenerationMessage(MessageRole.USER, "字" * 32768)
    value = request(request_id="A" + "a._:-" * 25 + "xy", template_version="v" * 64,
                    messages=(message,) * 64, max_output_tokens=16384, deadline_budget_s=0.001)
    assert value.max_output_tokens == 16384
    assert request(max_output_tokens=1).max_output_tokens == 1


@pytest.mark.parametrize("field,value", [
    ("provider", "OPENAI"), ("model_id", ""), ("model_id", " "),
    ("model_id", "m" * 129), ("model_id", "m\n"), ("model_id", "模型"),
    ("api_key", ""), ("api_key", " "), ("api_key", "k" * 4097),
    ("api_key", "key\r"), ("api_key", "密钥"), ("api_key", None),
])
def test_provider_config_rejects_invalid_fields_without_echoing_them(field, value):
    fields = dict(provider=Provider.OPENAI, model_id="pinned", api_key="fixture-secret")
    fields[field] = value
    with pytest.raises(GatewayContractError) as raised:
        ProviderConfig(**fields)
    assert "fixture-secret" not in str(raised.value)
    assert "fixture-secret" not in repr(raised.value)


def test_provider_config_accepts_maximum_lengths():
    assert len(ProviderConfig(Provider.KIMI, "m" * 128, "k" * 4096).api_key) == 4096


@pytest.mark.parametrize("values", [(-1, 1, 0), (1, -1, 0), (1, 2, 4), (True, 2, 3), (1.0, 2, 3)])
def test_token_usage_requires_nonnegative_integer_counts_and_a_correct_sum(values):
    with pytest.raises(GatewayContractError):
        TokenUsage(*values)
    assert TokenUsage(0, 0, 0).total_tokens == 0


@pytest.mark.parametrize("raw,code", [
    (b'{"x":1,"x":2}', FailureCode.MALFORMED_PROVIDER_RESPONSE),
    (b'{"nested":{"x":1,"x":2}}', FailureCode.MALFORMED_PROVIDER_RESPONSE),
    (b'{"x":NaN}', FailureCode.MALFORMED_PROVIDER_RESPONSE),
    (b'{"x":Infinity}', FailureCode.MALFORMED_PROVIDER_RESPONSE),
    (b'{"x":1e999}', FailureCode.MALFORMED_PROVIDER_RESPONSE),
    (b'{"x":"\\ud800"}', FailureCode.MALFORMED_PROVIDER_RESPONSE),
    (b'\xff', FailureCode.MALFORMED_PROVIDER_RESPONSE),
    (b'{', FailureCode.MALFORMED_PROVIDER_RESPONSE),
    (b'null', FailureCode.MALFORMED_PROVIDER_RESPONSE),
    (b'[]', FailureCode.MALFORMED_PROVIDER_RESPONSE),
    (b'{"x":"sensitive-output"}', FailureCode.SCHEMA_MISMATCH),
    (b'{"x":1,"unexpected":"sensitive-output"}', FailureCode.SCHEMA_MISMATCH),
    (b'{}', FailureCode.SCHEMA_MISMATCH),
])
def test_decoder_errors_are_typed_and_redacted(raw, code):
    with pytest.raises(GatewayContractError) as raised:
        decode_validated_json(raw, schema=SCHEMA)
    assert raised.value.code is code
    assert "sensitive-output" not in repr(raised.value)
    assert "sensitive-output" not in str(raised.value)
    assert raised.value.__suppress_context__ is True


@pytest.mark.parametrize("schema", [
    {}, {"type": "array"}, {"type": "object", "required": "bad"},
    {"type": "object", "$ref": "#local"},
    {"type": "object", "properties": {"x": {"$dynamicRef": "#node"}}},
    {"type": "object", "$id": "https://example.invalid/schema"},
    {"type": "object", "$id": "urn:example:schema"},
    {"type": "object", "$id": "//example.invalid/schema"},
    {"type": "object", "$id": "/absolute"},
    {"type": "object", "examples": [{"$ref": "#node"}]},
    {"type": "object", "properties": {"x": {"pattern": "a{999999999999999999999}"}}},
])
def test_both_schema_entry_points_reject_invalid_or_referencing_schema(schema):
    for invoke in (lambda: request(output_schema=schema), lambda: decode_validated_json(b'{}', schema=schema)):
        with pytest.raises(GatewayContractError):
            invoke()


def test_schema_size_and_depth_are_bounded_before_validation():
    base = {"type": "object", "description": ""}
    capacity = 65536 - len(canonical_json(base).encode("utf-8"))
    assert request(output_schema={**base, "description": "x" * capacity})
    oversized = {**base, "description": "x" * (capacity + 1)}
    nested = {}
    for _ in range(21):
        nested = {"nested": nested}
    for schema in (oversized, {"type": "object", "examples": [nested]}):
        for invoke in (lambda: request(output_schema=schema), lambda: decode_validated_json(b'{}', schema=schema)):
            with pytest.raises(GatewayContractError):
                invoke()


def test_canonical_json_and_digest_use_sorted_compact_utf8():
    value = {"z": [True, None, "字"], "a": 1}
    assert canonical_json(value) == '{"a":1,"z":[true,null,"字"]}'
    assert stable_digest({}) == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    assert canonical_json(value) == canonical_json(_freeze_json(value))


@pytest.mark.parametrize("value", [{1: "x"}, {"x": float("nan")}, {"x": float("inf")}, {"x": {1}}, object(), b"x"])
def test_canonical_and_frozen_json_reject_non_json_values(value):
    for invoke in (lambda: canonical_json(value), lambda: _freeze_json(value)):
        with pytest.raises(GatewayContractError):
            invoke()


def test_nested_caller_mutation_cannot_change_schema_output_or_digests():
    schema = {"type": "object", "properties": {"items": {"type": "array"}}, "examples": [{"x": [1]}]}
    output = {"items": [{"text": "sensitive-output"}]}
    assert canonical_json(output) == canonical_json(_freeze_json(output))
    req = request(output_schema=schema)
    digest = stable_digest(output)
    record = attempt(input_digest=req.canonical_input_digest, output_digest=digest)
    res = result(output=output, output_digest=digest, input_digest=req.canonical_input_digest, attempts=(record,))
    original_input_digest = req.canonical_input_digest
    schema["examples"][0]["x"].append(2)
    output["items"][0]["text"] = "changed"
    assert canonical_json(req.output_schema["examples"]) == '[{"x":[1]}]'
    assert canonical_json(res.output) == '{"items":[{"text":"sensitive-output"}]}'
    assert req.canonical_input_digest == original_input_digest
    assert res.output_digest == digest == stable_digest(res.output)
    assert "sensitive-output" not in repr(res)
    assert "return an object" not in repr(req)
    with pytest.raises(TypeError):
        res.output["items"][0]["text"] = "changed again"
    decoded = decode_validated_json(b'{"x":1}', schema=SCHEMA)
    with pytest.raises(TypeError):
        decoded["x"] = 2


@pytest.mark.parametrize("changes", [
    {"attempt_index": -1}, {"attempt_index": True}, {"provider": "OPENAI"},
    {"input_digest": "invalid"}, {"request_id": "bad id"}, {"model_id": ""},
    {"provider_request_id": "bad\nidentifier"}, {"finish_reason": "x" * 161},
    {"finish_reason": "é"}, {"usage": {}}, {"retryable": 1}, {"security_failure": 0},
    {"status": "SUCCEEDED"}, {"failure_code": FailureCode.SCHEMA_MISMATCH},
    {"output_digest": None},
])
def test_attempt_validation_rejects_invalid_metadata_and_success_shape(changes):
    with pytest.raises(GatewayContractError):
        attempt(**changes)


@pytest.mark.parametrize("changes", [
    {"output": None}, {"output": [1]}, {"output_digest": "a" * 64},
    {"failure_code": FailureCode.SCHEMA_MISMATCH}, {"attempts": []},
    {"attempts": ("bad",)}, {"attempts": (attempt(request_id="other"),)},
    {"attempts": (attempt(attempt_index=1),)},
    {"attempts": (attempt(input_digest="a" * 64),)},
    {"selected_provider": Provider.KIMI}, {"selected_model_id": "different"},
])
def test_result_rejects_inconsistent_output_and_provenance(changes):
    with pytest.raises(GatewayContractError):
        result(**changes)


def test_failed_records_forbid_success_output_and_require_failure_code():
    with pytest.raises(GatewayContractError):
        attempt(status=GenerationStatus.UNAVAILABLE)
    failed = attempt(status=GenerationStatus.UNAVAILABLE, failure_code=FailureCode.READ_TIMEOUT,
                     output_digest=None, retryable=True)
    assert failed.failure_code is FailureCode.READ_TIMEOUT
    with pytest.raises(GatewayContractError):
        result(status=GenerationStatus.UNAVAILABLE, failure_code=FailureCode.READ_TIMEOUT)
    unavailable = result(status=GenerationStatus.UNAVAILABLE, output=None, output_digest=None,
                         failure_code=FailureCode.BACKUP_UNCONFIGURED, attempts=(failed,))
    assert unavailable.output is None


def test_zero_attempt_configuration_failure_is_representable():
    value = result(status=GenerationStatus.CONFIGURATION_ERROR, output=None, output_digest=None,
                   failure_code=FailureCode.PROVIDER_UNCONFIGURED, selected_provider=None,
                   selected_model_id=None, provider_request_id=None, finish_reason=None,
                   usage=None, attempts=())
    assert value.attempts == ()


def test_attempt_started_checks_identity_and_finite_timeout():
    fields = dict(request_id="req-001", attempt_index=0, provider=Provider.OPENAI,
                  model_id="pinned", input_digest="a" * 64, timeout_s=1.0)
    for changes in ({"attempt_index": -1}, {"timeout_s": 0}, {"timeout_s": float("nan")},
                    {"timeout_s": True}, {"model_id": "bad\n"}, {"input_digest": "bad"}):
        with pytest.raises(GatewayContractError):
            AttemptStarted(**{**fields, **changes})


def test_contract_error_requires_typed_code_and_bounded_detail():
    for code, detail in (("SCHEMA_MISMATCH", "bad"), (FailureCode.SCHEMA_MISMATCH, ""),
                         (FailureCode.SCHEMA_MISMATCH, "x" * 161)):
        with pytest.raises(ValueError):
            GatewayContractError(code, detail)
    error = GatewayContractError(FailureCode.SCHEMA_MISMATCH, "constant detail")
    assert str(error) == "SCHEMA_MISMATCH: constant detail"
    assert "constant detail" not in repr(error)


def test_observer_error_only_renders_safe_context_and_protocol_fails_loud():
    for phase in ("started", "finished"):
        error = AttemptObserverError("req-001", 0, Provider.OPENAI, phase)
        assert str(error) == f"attempt observer failed: provider=OPENAI index=0 phase={phase}"
    with pytest.raises(GatewayContractError):
        AttemptObserverError("req-001", 0, Provider.OPENAI, "secret error")
    with pytest.raises(AssertionError, match="protocol method executed"):
        AttemptObserver.started(None, None)
    with pytest.raises(AssertionError, match="protocol method executed"):
        AttemptObserver.finished(None, None)


def test_json_keys_are_valid_unicode_at_every_entry_point():
    source = {"\ud800": "value"}
    for invoke in (lambda: canonical_json(source), lambda: stable_digest(source),
                   lambda: _freeze_json(source),
                   lambda: request(output_schema={"type": "object", "examples": [source]}),
                   lambda: decode_validated_json(b'{"\\ud800":"value"}', schema={"type": "object"})):
        with pytest.raises(GatewayContractError):
            invoke()


def test_two_attempt_result_preserves_order_and_final_success_provenance():
    failed = attempt(status=GenerationStatus.UNAVAILABLE, failure_code=FailureCode.READ_TIMEOUT,
                     output_digest=None, retryable=True)
    final = attempt(attempt_index=1, provider=Provider.ANTHROPIC, model_id="claude-pinned")
    value = result(attempts=(failed, final), selected_provider=Provider.ANTHROPIC,
                   selected_model_id="claude-pinned")
    assert value.attempts == (failed, final)
    with pytest.raises(GatewayContractError):
        result(attempts=(final, failed))


def test_observer_error_does_not_render_original_exception_in_traceback():
    import traceback

    try:
        try:
            raise RuntimeError("sensitive observer detail")
        except RuntimeError:
            raise AttemptObserverError("req-001", 0, Provider.OPENAI, "finished")
    except AttemptObserverError as error:
        rendered = "".join(traceback.format_exception(error))
        assert "sensitive observer detail" not in rendered
        assert error.__suppress_context__ is True
