"""OpenAI Responses adapter contract tests."""

import hmac
import json

import pytest

from nxt_model_gateway import (
    FailureCode, GatewayContractError, GenerationStatus, Provider, TokenUsage, canonical_json,
)
from nxt_model_gateway.openai import OpenAIAdapter

from .conftest import HTTP_CASES, RecordingTransport, config, request, success_bytes


def test_provider_native_success_fixture():
    from nxt_model_gateway.openai import OpenAIAdapter
    from nxt_model_gateway import Provider, GenerationStatus, stable_digest
    from .conftest import config, request, success_bytes, RecordingTransport
    transport = RecordingTransport(body=success_bytes(Provider.OPENAI))
    adapter = OpenAIAdapter(config=config(Provider.OPENAI), transport=transport)
    outcome = adapter.send(adapter.prepare(request()), timeout_s=3.0)
    assert outcome.status is GenerationStatus.SUCCEEDED
    assert outcome.decoded_json['result'] == ()
    assert outcome.output_digest == stable_digest({'result': []})
    assert transport.calls == ['transport.post']


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


PROVIDER = Provider.OPENAI


ADAPTER = OpenAIAdapter


def make_adapter(**kwargs):
    transport = RecordingTransport(**kwargs)
    return ADAPTER(config=config(PROVIDER), transport=transport), transport


def test_exact_responses_envelope_and_metadata():
    from nxt_model_gateway.transport import _OPENAI_ENDPOINT
    adapter, transport = make_adapter(body=success_bytes(PROVIDER))
    prepared = adapter.prepare(request())
    assert transport.calls == []
    outcome = adapter.send(prepared, timeout_s=2.5)
    sent = transport.sent
    expected = {'model': 'openai-pinned', 'input': [{'role': 'user', 'content': 'return an object'}],
                'max_output_tokens': 256, 'text': {'format': {'type': 'json_schema',
                'name': 'structured_output', 'strict': True,
                'schema': json.loads(canonical_json(request().output_schema))}}, 'store': False}
    assert sent.endpoint == _OPENAI_ENDPOINT
    assert json.loads(sent.body) == expected
    assert set(sent.headers) == {'Authorization', 'Content-Type', 'Accept'}
    assert hmac.compare_digest(sent.headers['Authorization'], 'Bearer ' + config(PROVIDER).api_key)
    assert sent.headers['Content-Type'] == sent.headers['Accept'] == 'application/json'
    assert 'x-api-key' not in sent.headers
    assert sent.timeout_s == 2.5
    assert outcome.provider_request_id == 'resp_001'
    assert outcome.finish_reason == 'completed'
    assert outcome.usage == TokenUsage(10, 4, 14)
    assert outcome.input_digest == request().canonical_input_digest
    assert transport.calls == ['transport.post']


@pytest.mark.parametrize('body,code', [
    ({'choices': [{'message': {'content': '{"result":[]}'}}]}, 'MALFORMED_PROVIDER_RESPONSE'),
    ({'status': 'completed', 'output': [{'type': 'reasoning', 'text': '{"result":[]}'}]}, 'MALFORMED_PROVIDER_RESPONSE'),
    ({'status': 'incomplete', 'output': []}, 'MALFORMED_PROVIDER_RESPONSE'),
    ({'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': '{"result":[]}'}, {'type': 'output_text', 'text': '{"result":[]}'}]}]}, 'MALFORMED_PROVIDER_RESPONSE'),
    ({'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'refusal', 'refusal': 'sensitive-output'}]}]}, 'PROVIDER_REFUSED'),
    ({'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': '{'}]}]}, 'MALFORMED_PROVIDER_RESPONSE'),
    ({'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': '{"wrong":[]}'}]}]}, 'SCHEMA_MISMATCH'),
    ({'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': '{"result":[],"result":[]}'}]}]}, 'MALFORMED_PROVIDER_RESPONSE'),
])


def test_responses_only_one_completed_answer(body, code):
    adapter, transport = make_adapter(body=canonical_json(body).encode())
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.failure_code is FailureCode[code]
    assert outcome.decoded_json is None


@pytest.mark.parametrize('choices', [
    [{'message': {'content': '{"result":["other"]}'}}], [], None,
])
def test_completed_responses_rejects_any_top_level_choices(choices):
    envelope = json.loads(success_bytes(PROVIDER))
    envelope['choices'] = choices
    envelope['output'][0]['content'][0]['text'] = canonical_json({
        'result': ['secret-key', 'sensitive-output'],
    })
    adapter, transport = make_adapter(body=canonical_json(envelope).encode())
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.failure_code is FailureCode.MALFORMED_PROVIDER_RESPONSE
    assert outcome.status is GenerationStatus.INVALID_RESPONSE
    assert outcome.decoded_json is outcome.output_digest is None
    assert outcome.retryable is outcome.security_failure is False
    assert transport.calls == ['transport.post']
    assert 'secret-key' not in repr(outcome)
    assert 'sensitive-output' not in repr(outcome)


@pytest.mark.parametrize('message_status', ['incomplete', 'in_progress', None, False, 'sensitive-output'])
def test_completed_responses_rejects_explicit_unfinished_message(message_status):
    envelope = json.loads(success_bytes(PROVIDER))
    envelope['output'][0]['status'] = message_status
    envelope['output'][0]['content'][0]['text'] = canonical_json({
        'result': ['secret-key', 'sensitive-output'],
    })
    adapter, transport = make_adapter(body=canonical_json(envelope).encode())
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.failure_code is FailureCode.MALFORMED_PROVIDER_RESPONSE
    assert outcome.status is GenerationStatus.INVALID_RESPONSE
    assert outcome.decoded_json is outcome.output_digest is None
    assert outcome.retryable is outcome.security_failure is False
    assert transport.calls == ['transport.post']
    assert 'secret-key' not in repr(outcome)
    assert 'sensitive-output' not in repr(outcome)


def test_explicit_completed_message_status_is_accepted():
    envelope = json.loads(success_bytes(PROVIDER))
    envelope['output'][0]['status'] = 'completed'
    adapter, transport = make_adapter(body=canonical_json(envelope).encode())
    outcome = adapter.send(adapter.prepare(request()), timeout_s=1.0)
    assert outcome.status is GenerationStatus.SUCCEEDED
    assert outcome.decoded_json['result'] == ()
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
