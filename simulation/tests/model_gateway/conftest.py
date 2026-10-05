"""Neutral fixtures; every adapter call uses this in-memory transport."""

from pathlib import Path
from types import SimpleNamespace

from nxt_model_gateway import (
    GenerationMessage, GenerationRequest, MessageRole, ProviderConfig,
)
from nxt_model_gateway.transport import HttpResponse


SCHEMA = {'type': 'object', 'properties': {'result': {'type': 'array'}},
          'required': ['result'], 'additionalProperties': False}


def request(*, messages=None):
    return GenerationRequest('req-1', 'v1', messages or (
        GenerationMessage(MessageRole.USER, 'return an object'),
    ), SCHEMA, 256, 20.0)


def config(provider):
    return ProviderConfig(provider, provider.value.lower() + '-pinned', 'secret-key')


def success_bytes(provider):
    return (Path(__file__).parent / 'fixtures' / (provider.value.lower() + '_success.json')).read_bytes()


class RecordingTransport:
    def __init__(self, calls=None, *, body=b'{}', status=200, failure=None):
        self.calls = [] if calls is None else calls
        self.body, self.status, self.failure = body, status, failure
        self.sent = None

    def post(self, *, endpoint, headers, body, timeout_s):
        self.calls.append('transport.post')
        self.sent = SimpleNamespace(endpoint=endpoint, headers=headers, body=body, timeout_s=timeout_s)
        if self.failure is not None:
            raise self.failure
        return HttpResponse(self.status, {}, self.body)


HTTP_CASES = [
    (201, 'MALFORMED_PROVIDER_RESPONSE', 'INVALID_RESPONSE', False),
    (204, 'MALFORMED_PROVIDER_RESPONSE', 'INVALID_RESPONSE', False),
    (400, 'INVALID_PROVIDER_REQUEST', 'CONFIGURATION_ERROR', False),
    (401, 'AUTHENTICATION_FAILED', 'CONFIGURATION_ERROR', False),
    (403, 'PERMISSION_DENIED', 'CONFIGURATION_ERROR', False),
    (404, 'MODEL_NOT_FOUND', 'CONFIGURATION_ERROR', False),
    (408, 'HTTP_TIMEOUT', 'UNAVAILABLE', True),
    (409, 'INVALID_PROVIDER_REQUEST', 'CONFIGURATION_ERROR', False),
    (422, 'INVALID_PROVIDER_REQUEST', 'CONFIGURATION_ERROR', False),
    (429, 'RATE_LIMITED', 'UNAVAILABLE', True),
    (418, 'PROVIDER_CLIENT_ERROR', 'PROVIDER_ERROR', False),
    (499, 'PROVIDER_CLIENT_ERROR', 'PROVIDER_ERROR', False),
    (500, 'PROVIDER_UNAVAILABLE', 'UNAVAILABLE', True),
    (502, 'PROVIDER_UNAVAILABLE', 'UNAVAILABLE', True),
    (503, 'PROVIDER_UNAVAILABLE', 'UNAVAILABLE', True),
    (504, 'PROVIDER_UNAVAILABLE', 'UNAVAILABLE', True),
    (599, 'PROVIDER_UNAVAILABLE', 'UNAVAILABLE', True),
]
