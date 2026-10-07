import dataclasses
import hmac
import json

import pytest

from nxt_model_gateway import (
    FailureCode, GatewayContractError, GenerationStatus, Provider, TokenUsage,
    canonical_json, stable_digest,
)
from nxt_model_gateway.adapters import PreparedRequest, AdapterOutcome, _failure_outcome
from nxt_model_gateway.kimi import KimiAdapter
from nxt_model_gateway.transport import _KIMI_ENDPOINT

from .conftest import HTTP_CASES, RecordingTransport, config, request, success_bytes


def test_prepared_request_and_outcome_have_exact_fields():
    assert tuple(f.name for f in dataclasses.fields(PreparedRequest)) == (
        'request_id', 'provider', 'model_id', 'endpoint', 'headers', 'body',
        'output_schema', 'input_digest',
    )
    assert tuple(f.name for f in dataclasses.fields(AdapterOutcome)) == (
        'status', 'decoded_json', 'failure_code', 'provider_request_id',
        'finish_reason', 'usage', 'input_digest', 'output_digest',
        'retryable', 'security_failure',
    )


def test_records_detach_nested_inputs_and_hide_secrets():
    schema = {'type': 'object', 'properties': {'result': {'type': 'array'}}}
    headers, body = {'Authorization': 'secret-key'}, bytearray(b'secret-key')
    prepared = PreparedRequest('req-1', Provider.KIMI, 'kimi-pinned', _KIMI_ENDPOINT,
                               headers, body, schema, 'a' * 64)
    output = {'result': ['sensitive-output']}
    digest = stable_digest(output)
    outcome = AdapterOutcome(GenerationStatus.SUCCEEDED, output, None, None, None,
                             None, 'a' * 64, digest, False, False)
    schema['properties']['result']['type'] = 'string'
    headers['Authorization'] = 'changed'
    body[0] = 0
    output['result'].append('changed')
    assert prepared.output_schema['properties']['result']['type'] == 'array'
    assert prepared.headers['Authorization'] == 'secret-key'
    assert prepared.body == b'secret-key'
    assert outcome.decoded_json['result'] == ('sensitive-output',)
    assert stable_digest(outcome.decoded_json) == digest
    assert all(secret not in repr(value) for value in (prepared, outcome)
               for secret in ('secret-key', 'sensitive-output'))
    with pytest.raises(TypeError):
        prepared.headers['x'] = 'y'
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.output_digest = 'b' * 64


@pytest.mark.parametrize('status,decoded,digest', [
    (GenerationStatus.SUCCEEDED, {'result': []}, 'b' * 64),
    (GenerationStatus.SUCCEEDED, None, 'b' * 64),
    (GenerationStatus.INVALID_RESPONSE, {'result': []}, None),
    (GenerationStatus.INVALID_RESPONSE, None, 'b' * 64),
])


def test_outcome_rejects_inconsistent_output(status, decoded, digest):
    code = None if status is GenerationStatus.SUCCEEDED else FailureCode.SCHEMA_MISMATCH
    with pytest.raises(GatewayContractError):
        AdapterOutcome(status, decoded, code, None, None, None, 'a' * 64, digest, False, False)


DISPOSITIONS = [
    ('UNAVAILABLE', False, True, 'DNS_FAILURE CONNECT_TIMEOUT CONNECT_FAILED READ_TIMEOUT CONNECTION_INTERRUPTED HTTP_TIMEOUT RATE_LIMITED PROVIDER_UNAVAILABLE'),
    ('UNAVAILABLE', False, False, 'DEADLINE_EXHAUSTED BACKUP_UNCONFIGURED'),
    ('REFUSED', False, False, 'PROVIDER_REFUSED'),
    ('INVALID_RESPONSE', False, False, 'MALFORMED_PROVIDER_RESPONSE SCHEMA_MISMATCH'),
    ('CONFIGURATION_ERROR', False, False, 'INPUT_TOO_LARGE AUTHENTICATION_FAILED PERMISSION_DENIED MODEL_NOT_FOUND INVALID_PROVIDER_REQUEST PROVIDER_UNCONFIGURED'),
    ('SECURITY_ERROR', True, False, 'RESPONSE_TOO_LARGE UNSUPPORTED_CONTENT_ENCODING REDIRECT_REFUSED ENDPOINT_NOT_ALLOWED TLS_VERIFICATION_FAILED'),
    ('PROVIDER_ERROR', False, False, 'PROVIDER_CLIENT_ERROR'),
]


def test_complete_failure_disposition():
    seen = set()
    for status, security, retryable, codes in DISPOSITIONS:
        for name in codes.split():
            code = FailureCode[name]
            seen.add(code)
            outcome = _failure_outcome(code, 'a' * 64)
            assert (outcome.status.value, outcome.security_failure, outcome.retryable) == (status, security, retryable)
            assert outcome.failure_code is code
            assert outcome.decoded_json is outcome.output_digest is None
    assert seen == set(FailureCode)


def test_neutral_outcome_does_not_make_retryable_authoritative():
    outcome = AdapterOutcome(GenerationStatus.UNAVAILABLE, None, FailureCode.RATE_LIMITED,
                             None, None, None, 'a' * 64, None, False, False)
    assert outcome.retryable is False


def test_provider_native_success_fixture():
    from nxt_model_gateway import Provider, GenerationStatus, stable_digest
    from .conftest import config, request, success_bytes, RecordingTransport
    transport = RecordingTransport(body=success_bytes(Provider.KIMI))
    adapter = KimiAdapter(config=config(Provider.KIMI), transport=transport)
    outcome = adapter.send(adapter.prepare(request()), timeout_s=3.0)
    assert outcome.status is GenerationStatus.SUCCEEDED
    assert outcome.decoded_json['result'] == ()
    assert outcome.output_digest == stable_digest({'result': []})
    assert transport.calls == ['transport.post']


def request_with_serialized_body_size(size):
    from .conftest import request
    from nxt_model_gateway import GenerationMessage, MessageRole, canonical_json
    messages = [GenerationMessage(MessageRole.USER, 'x' * 32700) for _ in range(7)]
    messages.append(GenerationMessage(MessageRole.USER, 'x'))
    candidate = request(messages=tuple(messages))
    payload = {'model': 'kimi-pinned', 'messages': [
        {'role': m.role.value, 'content': m.content} for m in messages
    ], 'max_tokens': 256, 'response_format': {'type': 'json_schema', 'json_schema': {
        'name': 'structured_output', 'strict': True, 'schema': candidate.output_schema,
    }}, 'stream': False}
    missing = size - len(canonical_json(payload).encode('utf-8'))
    messages[-1] = GenerationMessage(MessageRole.USER, 'x' * (1 + missing))
    return request(messages=tuple(messages))


def test_oversized_prepared_body_fails_before_transport():
    from .conftest import config, RecordingTransport
    calls = []
    adapter = KimiAdapter(config=config(Provider.KIMI), transport=RecordingTransport(calls))
    with pytest.raises(GatewayContractError) as raised:
        adapter.prepare(request_with_serialized_body_size(262145))
    assert raised.value.code is FailureCode.INPUT_TOO_LARGE
    assert calls == []


def test_prepared_body_at_256_kib_is_allowed():
    from .conftest import config, RecordingTransport
    calls = []
    adapter = KimiAdapter(config=config(Provider.KIMI), transport=RecordingTransport(calls))
    prepared = adapter.prepare(request_with_serialized_body_size(262144))
    assert len(prepared.body) == 262144
    assert calls == []


@pytest.mark.parametrize('status,code,want_status,retryable', HTTP_CASES)


def test_http_failure_classified_before_secret_body(status, code, want_status, retryable):
    adapter, transport = make_adapter(body=b'secret-key sensitive-output invalid JSON', status=status)
    outcome = adapter.send(adapter.prepare(request()), timeout_s=2.5)
    assert outcome.failure_code is FailureCode[code]
    assert outcome.status.value == want_status
    assert outcome.retryable is retryable
    assert outcome.security_failure is False
    assert outcome.decoded_json is outcome.output_digest is None
    assert 'secret-key' not in repr(outcome)
    assert 'sensitive-output' not in repr(outcome)
    assert transport.calls == ['transport.post']


def test_constructor_rejects_wrong_provider_before_transport():
    calls = []
    other = Provider.OPENAI if PROVIDER is Provider.KIMI else Provider.KIMI
    with pytest.raises(GatewayContractError) as raised:
        ADAPTER(config=config(other), transport=RecordingTransport(calls))
    assert raised.value.code is FailureCode.PROVIDER_UNCONFIGURED
    assert str(raised.value) == 'PROVIDER_UNCONFIGURED: provider config does not match adapter'
    assert calls == []


@pytest.mark.parametrize('raw', [b'{', b'[]', b'null', b'{"id":"x","id":"y"}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":"\\ud800"}'])


def test_malformed_envelope_is_safe(raw):
    adapter, transport = make_adapter(body=raw)
    outcome = adapter.send(adapter.prepare(request()), timeout_s=2.5)
    assert outcome.failure_code is FailureCode.MALFORMED_PROVIDER_RESPONSE
    assert outcome.status is GenerationStatus.INVALID_RESPONSE
    assert transport.calls == ['transport.post']


PROVIDER, ADAPTER = Provider.KIMI, KimiAdapter


def make_adapter(**kwargs):
    transport = RecordingTransport(**kwargs)
    return KimiAdapter(config=config(PROVIDER), transport=transport), transport


def test_exact_kimi_envelope_and_metadata():
    adapter, transport = make_adapter(body=success_bytes(PROVIDER))
    prepared = adapter.prepare(request())
    assert transport.calls == []
    outcome = adapter.send(prepared, timeout_s=2.5)
    sent = transport.sent
    expected = {'model': 'kimi-pinned', 'messages': [{'role': 'user', 'content': 'return an object'}],
                'max_tokens': 256, 'response_format': {'type': 'json_schema', 'json_schema': {
                    'name': 'structured_output', 'strict': True, 'schema': json.loads(canonical_json(request().output_schema))}},
                'stream': False}
    assert sent.endpoint == _KIMI_ENDPOINT
    assert json.loads(sent.body) == expected
    assert set(sent.headers) == {'Authorization', 'Content-Type', 'Accept'}
    assert hmac.compare_digest(sent.headers['Authorization'], 'Bearer ' + config(PROVIDER).api_key)
    assert sent.headers['Content-Type'] == sent.headers['Accept'] == 'application/json'
    assert 'x-api-key' not in sent.headers
    assert sent.timeout_s == 2.5
    assert outcome.provider_request_id == 'kimi_req_001'
    assert outcome.finish_reason == 'stop'
    assert outcome.usage == TokenUsage(10, 4, 14)
    assert outcome.input_digest == request().canonical_input_digest
    assert transport.calls == ['transport.post']


def test_kimi_k2_6_disables_default_thinking_in_prepared_request():
    adapter = KimiAdapter(
        config=dataclasses.replace(config(Provider.KIMI), model_id='kimi-k2.6'),
        transport=RecordingTransport(),
    )

    payload = json.loads(adapter.prepare(request()).body)

    assert payload['model'] == 'kimi-k2.6'
    assert payload['thinking'] == {'type': 'disabled'}


@pytest.mark.parametrize('message,finish,code', [
    ({'content': '{'}, 'stop', 'MALFORMED_PROVIDER_RESPONSE'),
    ({'content': '{"result":[],"result":[]}'}, 'stop', 'MALFORMED_PROVIDER_RESPONSE'),
    ({'reasoning_content': '{"result":[]}'}, 'stop', 'MALFORMED_PROVIDER_RESPONSE'),
    ({'content': '{"wrong":[]}'}, 'stop', 'SCHEMA_MISMATCH'),
    ({'refusal': 'sensitive-output'}, 'stop', 'PROVIDER_REFUSED'),
    ({'content': '{"result":[]}'}, 'content_filter', 'PROVIDER_REFUSED'),
    ({'content': '{"result":[]}'}, 'length', 'MALFORMED_PROVIDER_RESPONSE'),
])


def test_kimi_answer_fields(message, finish, code):
    body = {'choices': [{'finish_reason': finish, 'message': message}]}
    adapter, transport = make_adapter(body=canonical_json(body).encode())
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.failure_code is FailureCode[code]
    assert outcome.decoded_json is None


@pytest.mark.parametrize('refusal', [
    '', False, True, 0, 1, 1.5, {}, {'reason': 'blocked'}, [], ['blocked'],
])
def test_kimi_empty_or_non_string_refusal_is_terminal_malformed(refusal):
    body = {'choices': [{'finish_reason': 'stop', 'message': {
        'content': '{"result":[]}', 'refusal': refusal,
    }}]}
    adapter, transport = make_adapter(body=canonical_json(body).encode())
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.status is GenerationStatus.INVALID_RESPONSE
    assert outcome.failure_code is FailureCode.MALFORMED_PROVIDER_RESPONSE
    assert outcome.retryable is outcome.security_failure is False
    assert outcome.decoded_json is outcome.output_digest is None
    assert transport.calls == ['transport.post']


def test_kimi_null_refusal_is_absent():
    body = {'choices': [{'finish_reason': 'stop', 'message': {
        'content': '{"result":[]}', 'refusal': None,
    }}]}
    adapter, transport = make_adapter(body=canonical_json(body).encode())
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.status is GenerationStatus.SUCCEEDED
    assert outcome.decoded_json == {'result': ()}
    assert outcome.failure_code is None
    assert transport.calls == ['transport.post']


def test_kimi_transport_failure_one_call():
    from nxt_model_gateway.transport import _failure
    adapter, transport = make_adapter(failure=_failure(FailureCode.READ_TIMEOUT))
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.failure_code is FailureCode.READ_TIMEOUT
    assert outcome.status is GenerationStatus.UNAVAILABLE
    assert outcome.retryable is True
    assert transport.calls == ['transport.post']


TRANSPORT_CASES = [
    ('INPUT_TOO_LARGE', 'CONFIGURATION_ERROR', False, False),
    ('RESPONSE_TOO_LARGE', 'SECURITY_ERROR', False, True),
    ('UNSUPPORTED_CONTENT_ENCODING', 'SECURITY_ERROR', False, True),
    ('REDIRECT_REFUSED', 'SECURITY_ERROR', False, True),
    ('ENDPOINT_NOT_ALLOWED', 'SECURITY_ERROR', False, True),
    ('TLS_VERIFICATION_FAILED', 'SECURITY_ERROR', False, True),
    ('DNS_FAILURE', 'UNAVAILABLE', True, False),
    ('CONNECT_TIMEOUT', 'UNAVAILABLE', True, False),
    ('CONNECT_FAILED', 'UNAVAILABLE', True, False),
    ('READ_TIMEOUT', 'UNAVAILABLE', True, False),
    ('CONNECTION_INTERRUPTED', 'UNAVAILABLE', True, False),
]


@pytest.mark.parametrize('code,status,retryable,security', TRANSPORT_CASES)


def test_transport_failure_disposition_without_hidden_retry(code, status, retryable, security):
    from nxt_model_gateway.transport import _failure
    failure = _failure(FailureCode[code])
    assert (failure.retryable, failure.security_failure) == (retryable, security)
    adapter, transport = make_adapter(failure=failure)
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.status.value == status
    assert outcome.failure_code is FailureCode[code]
    assert outcome.retryable is retryable
    assert outcome.security_failure is security
    assert outcome.decoded_json is outcome.output_digest is None
    assert transport.calls == ['transport.post']


def test_failure_code_overrides_fake_transport_flags():
    from dataclasses import replace
    from nxt_model_gateway.transport import _failure
    failure = replace(_failure(FailureCode.READ_TIMEOUT), retryable=False, security_failure=True)
    adapter, transport = make_adapter(failure=failure)
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.status is GenerationStatus.UNAVAILABLE
    assert outcome.retryable is True
    assert outcome.security_failure is False
    assert transport.calls == ['transport.post']


@pytest.mark.parametrize('size', [262144, 262145])


def test_each_provider_prepare_byte_limit(size):
    from nxt_model_gateway import GenerationMessage, MessageRole
    adapter, transport = make_adapter()
    messages = [GenerationMessage(MessageRole.USER, 'x' * 32700) for _ in range(7)]
    messages.append(GenerationMessage(MessageRole.USER, 'x'))
    baseline = adapter.prepare(request(messages=tuple(messages)))
    messages[-1] = GenerationMessage(MessageRole.USER, 'x' * (1 + size - len(baseline.body)))
    source = request(messages=tuple(messages))
    if size == 262144:
        assert len(adapter.prepare(source).body) == size
    else:
        with pytest.raises(GatewayContractError) as raised:
            adapter.prepare(source)
        assert raised.value.code is FailureCode.INPUT_TOO_LARGE
    assert transport.calls == []


def test_answer_text_does_not_trigger_failure_classification():
    envelope = json.loads(success_bytes(PROVIDER))
    answer = {'result': ['refusal rate limit unavailable secret-key sensitive-output']}
    if PROVIDER is Provider.KIMI:
        envelope['choices'][0]['message']['content'] = canonical_json(answer)
    elif PROVIDER is Provider.OPENAI:
        envelope['output'][0]['content'][0]['text'] = canonical_json(answer)
    else:
        envelope['content'][0]['input'] = answer
    adapter, transport = make_adapter(body=canonical_json(envelope).encode())
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.status is GenerationStatus.SUCCEEDED
    assert outcome.decoded_json['result'] == tuple(answer['result'])
    assert 'secret-key' not in repr(outcome)
    assert 'sensitive-output' not in repr(outcome)


@pytest.mark.parametrize('usage', [
    {'input_tokens': -1, 'output_tokens': 4, 'prompt_tokens': -1, 'completion_tokens': 4, 'total_tokens': 3},
    {'input_tokens': True, 'output_tokens': 4, 'prompt_tokens': True, 'completion_tokens': 4, 'total_tokens': 5},
    'sensitive-output', {},
])


def test_invalid_usage_is_malformed_provider_response(usage):
    envelope = json.loads(success_bytes(PROVIDER))
    envelope['usage'] = usage
    adapter, transport = make_adapter(body=canonical_json(envelope).encode())
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.failure_code is FailureCode.MALFORMED_PROVIDER_RESPONSE
    assert outcome.usage is None
    assert outcome.decoded_json is None


def test_public_adapter_exports_preserve_contract_surface():
    import nxt_model_gateway as gateway
    for name in ('AdapterOutcome', 'PreparedRequest', 'ProviderAdapter', 'KimiAdapter',
                 'OpenAIAdapter', 'AnthropicAdapter', 'GenerationRequest', 'stable_digest'):
        assert name in gateway.__all__
        assert hasattr(gateway, name)
    assert not any(name in gateway.__all__ for name in (
        '_KIMI_ENDPOINT', '_OPENAI_ENDPOINT', '_ANTHROPIC_ENDPOINT', 'HttpTransport', 'HttpResponse'))
