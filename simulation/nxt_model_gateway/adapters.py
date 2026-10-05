"""Secret-safe two-phase provider adaptation and closed failure disposition."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from .contracts import (
    FailureCode, GatewayContractError, GenerationRequest, GenerationStatus,
    Provider, ProviderConfig, TokenUsage, _digest, _invalid, _optional_metadata, _outcome,
)
from .serialization import _freeze_json, canonical_json, decode_validated_json, stable_digest
from .transport import HttpTransport, ProviderEndpoint, TransportFailure


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    request_id: str
    provider: Provider
    model_id: str
    endpoint: ProviderEndpoint
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    output_schema: Mapping[str, object] = field(repr=False)
    input_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, 'headers', MappingProxyType(dict(self.headers)))
        object.__setattr__(self, 'body', bytes(self.body))
        object.__setattr__(self, 'output_schema', _freeze_json(self.output_schema))


@dataclass(frozen=True, slots=True)
class AdapterOutcome:
    status: GenerationStatus
    decoded_json: Mapping[str, object] | None = field(repr=False)
    failure_code: FailureCode | None
    provider_request_id: str | None
    finish_reason: str | None
    usage: TokenUsage | None
    input_digest: str
    output_digest: str | None
    retryable: bool
    security_failure: bool

    def __post_init__(self) -> None:
        _digest(self.input_digest)
        _outcome(self.status, self.failure_code, self.output_digest)
        _optional_metadata(self.provider_request_id, self.finish_reason, self.usage)
        if type(self.retryable) is not bool or type(self.security_failure) is not bool:
            raise _invalid('outcome flags must be bool')
        if self.status is GenerationStatus.SUCCEEDED:
            if not isinstance(self.decoded_json, Mapping):
                raise _invalid('successful outcome requires an object')
            frozen = _freeze_json(self.decoded_json)
            if stable_digest(frozen) != self.output_digest:
                raise _invalid('output digest does not match output')
            object.__setattr__(self, 'decoded_json', frozen)
        elif self.decoded_json is not None:
            raise _invalid('failed outcome cannot contain output')


class ProviderAdapter(Protocol):
    provider: Provider
    model_id: str

    def prepare(self, request: GenerationRequest) -> PreparedRequest:
        raise AssertionError('protocol method executed')

    def send(self, prepared: PreparedRequest, *, timeout_s: float) -> AdapterOutcome:
        raise AssertionError('protocol method executed')


_AVAILABILITY_CODES = frozenset((
    FailureCode.DNS_FAILURE, FailureCode.CONNECT_TIMEOUT, FailureCode.CONNECT_FAILED,
    FailureCode.READ_TIMEOUT, FailureCode.CONNECTION_INTERRUPTED, FailureCode.HTTP_TIMEOUT,
    FailureCode.RATE_LIMITED, FailureCode.PROVIDER_UNAVAILABLE,
))
_DISPOSITION_ROWS = (
    (GenerationStatus.UNAVAILABLE, False, _AVAILABILITY_CODES),
    (GenerationStatus.UNAVAILABLE, False, (FailureCode.DEADLINE_EXHAUSTED, FailureCode.BACKUP_UNCONFIGURED)),
    (GenerationStatus.REFUSED, False, (FailureCode.PROVIDER_REFUSED,)),
    (GenerationStatus.INVALID_RESPONSE, False, (FailureCode.MALFORMED_PROVIDER_RESPONSE, FailureCode.SCHEMA_MISMATCH)),
    (GenerationStatus.CONFIGURATION_ERROR, False, (
        FailureCode.INPUT_TOO_LARGE, FailureCode.AUTHENTICATION_FAILED,
        FailureCode.PERMISSION_DENIED, FailureCode.MODEL_NOT_FOUND,
        FailureCode.INVALID_PROVIDER_REQUEST, FailureCode.PROVIDER_UNCONFIGURED,
    )),
    (GenerationStatus.SECURITY_ERROR, True, (
        FailureCode.RESPONSE_TOO_LARGE, FailureCode.UNSUPPORTED_CONTENT_ENCODING,
        FailureCode.REDIRECT_REFUSED, FailureCode.ENDPOINT_NOT_ALLOWED,
        FailureCode.TLS_VERIFICATION_FAILED,
    )),
    (GenerationStatus.PROVIDER_ERROR, False, (FailureCode.PROVIDER_CLIENT_ERROR,)),
)
_FAILURE_DISPOSITIONS = MappingProxyType({
    code: (status, security) for status, security, codes in _DISPOSITION_ROWS for code in codes
})
assert set(_FAILURE_DISPOSITIONS) == set(FailureCode)


def _failure_outcome(code: FailureCode, input_digest: str) -> AdapterOutcome:
    status, security = _FAILURE_DISPOSITIONS[code]
    return AdapterOutcome(status, None, code, None, None, None, input_digest,
                          None, code in _AVAILABILITY_CODES, security)


class _BaseAdapter:
    provider: Provider
    _endpoint: ProviderEndpoint

    def __init__(self, *, config: ProviderConfig, transport: HttpTransport):
        if not isinstance(config, ProviderConfig) or config.provider is not self.provider:
            raise GatewayContractError(FailureCode.PROVIDER_UNCONFIGURED,
                                       'provider config does not match adapter')
        self.model_id = config.model_id
        self._config = config
        self._transport = transport

    def _prepare(self, request: GenerationRequest, payload: Mapping[str, object],
                 headers: Mapping[str, str]) -> PreparedRequest:
        body = canonical_json(payload).encode('utf-8')
        if len(body) > 262144:
            raise GatewayContractError(FailureCode.INPUT_TOO_LARGE, 'request exceeds byte limit')
        return PreparedRequest(request.request_id, self.provider, self.model_id, self._endpoint,
                               headers, body, request.output_schema, request.canonical_input_digest)

    def _bearer_headers(self) -> Mapping[str, str]:
        return {'Authorization': 'Bearer ' + self._config.api_key,
                'Content-Type': 'application/json', 'Accept': 'application/json'}

    def send(self, prepared: PreparedRequest, *, timeout_s: float) -> AdapterOutcome:
        try:
            response = self._transport.post(endpoint=prepared.endpoint, headers=prepared.headers,
                                            body=prepared.body, timeout_s=timeout_s)
        except TransportFailure as error:
            return _failure_outcome(error.code, prepared.input_digest)
        if response.status_code != 200:
            return _failure_outcome(_http_failure(response.status_code), prepared.input_digest)
        try:
            envelope = decode_validated_json(response.body, schema={'type': 'object'})
            decoded, request_id, finish, usage = self._parse(envelope, prepared.output_schema)
            return AdapterOutcome(GenerationStatus.SUCCEEDED, decoded, None, request_id, finish,
                                  usage, prepared.input_digest, stable_digest(decoded), False, False)
        except GatewayContractError as error:
            code = error.code if error.code in (
                FailureCode.PROVIDER_REFUSED, FailureCode.SCHEMA_MISMATCH,
                FailureCode.MALFORMED_PROVIDER_RESPONSE,
            ) else FailureCode.MALFORMED_PROVIDER_RESPONSE
            return _failure_outcome(code, prepared.input_digest)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, UnicodeError, RecursionError):
            return _failure_outcome(FailureCode.MALFORMED_PROVIDER_RESPONSE, prepared.input_digest)


def _http_failure(status: int) -> FailureCode:
    exact = {
        400: FailureCode.INVALID_PROVIDER_REQUEST, 401: FailureCode.AUTHENTICATION_FAILED,
        403: FailureCode.PERMISSION_DENIED, 404: FailureCode.MODEL_NOT_FOUND,
        408: FailureCode.HTTP_TIMEOUT, 409: FailureCode.INVALID_PROVIDER_REQUEST,
        422: FailureCode.INVALID_PROVIDER_REQUEST, 429: FailureCode.RATE_LIMITED,
    }
    if status in exact:
        return exact[status]
    if 500 <= status <= 599:
        return FailureCode.PROVIDER_UNAVAILABLE
    if 400 <= status <= 499:
        return FailureCode.PROVIDER_CLIENT_ERROR
    return FailureCode.MALFORMED_PROVIDER_RESPONSE


def _malformed() -> GatewayContractError:
    return GatewayContractError(FailureCode.MALFORMED_PROVIDER_RESPONSE, 'invalid provider envelope')


def _refused() -> GatewayContractError:
    return GatewayContractError(FailureCode.PROVIDER_REFUSED, 'provider refused output')


def _usage(envelope: Mapping[str, object], input_name: str, output_name: str,
           *, derive_total: bool = False) -> TokenUsage | None:
    value = envelope.get('usage')
    if value is None:
        return None
    total = value[input_name] + value[output_name] if derive_total else value['total_tokens']
    return TokenUsage(value[input_name], value[output_name], total)
