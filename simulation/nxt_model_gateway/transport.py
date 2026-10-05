"""Direct, verified HTTPS to fixed providers with one bounded attempt."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import http.client
import io
import math
import re
import socket
import ssl
import threading
from types import MappingProxyType
from typing import Protocol

from .contracts import FailureCode, Provider


@dataclass(frozen=True, slots=True)
class ProviderEndpoint:
    provider: Provider
    host: str
    port: int
    path: str


class TransportPhase(StrEnum):
    RESOLVING = 'RESOLVING'
    CONNECTING = 'CONNECTING'
    READING = 'READING'


_KIMI_ENDPOINT = ProviderEndpoint(Provider.KIMI, 'api.moonshot.ai', 443, '/v1/chat/completions')
_OPENAI_ENDPOINT = ProviderEndpoint(Provider.OPENAI, 'api.openai.com', 443, '/v1/responses')
_ANTHROPIC_ENDPOINT = ProviderEndpoint(Provider.ANTHROPIC, 'api.anthropic.com', 443, '/v1/messages')
_ENDPOINTS = (_KIMI_ENDPOINT, _OPENAI_ENDPOINT, _ANTHROPIC_ENDPOINT)
_REQUEST_LIMIT = 262144
_RESPONSE_LIMIT = 524288
_RESERVED_HEADERS = frozenset(('host', 'content-length', 'transfer-encoding',
                               'connection', 'accept-encoding'))
_DIAGNOSTICS = {
    FailureCode.INPUT_TOO_LARGE: 'request exceeds byte limit',
    FailureCode.RESPONSE_TOO_LARGE: 'response exceeds byte limit',
    FailureCode.UNSUPPORTED_CONTENT_ENCODING: 'response encoding is unsupported',
    FailureCode.REDIRECT_REFUSED: 'redirect is prohibited',
    FailureCode.ENDPOINT_NOT_ALLOWED: 'endpoint is not allowed',
    FailureCode.TLS_VERIFICATION_FAILED: 'TLS verification failed',
    FailureCode.DNS_FAILURE: 'name resolution failed or expired',
    FailureCode.CONNECT_TIMEOUT: 'connection deadline expired',
    FailureCode.CONNECT_FAILED: 'connection failed',
    FailureCode.READ_TIMEOUT: 'response deadline expired',
    FailureCode.CONNECTION_INTERRUPTED: 'connection interrupted',
    FailureCode.INVALID_PROVIDER_REQUEST: 'invalid transport request',
    FailureCode.MALFORMED_PROVIDER_RESPONSE: 'invalid response framing',
}
_SECURITY_CODES = frozenset((
    FailureCode.RESPONSE_TOO_LARGE, FailureCode.UNSUPPORTED_CONTENT_ENCODING,
    FailureCode.REDIRECT_REFUSED, FailureCode.ENDPOINT_NOT_ALLOWED,
    FailureCode.TLS_VERIFICATION_FAILED,
))
_RETRYABLE_CODES = frozenset((
    FailureCode.DNS_FAILURE, FailureCode.CONNECT_TIMEOUT, FailureCode.CONNECT_FAILED,
    FailureCode.READ_TIMEOUT, FailureCode.CONNECTION_INTERRUPTED,
))


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError('invalid HTTP status')
        object.__setattr__(self, 'headers', MappingProxyType({
            name.lower(): value for name, value in self.headers.items()
        }))
        object.__setattr__(self, 'body', bytes(self.body))


@dataclass(frozen=True, slots=True)
class TransportFailure(Exception):
    code: FailureCode
    retryable: bool
    security_failure: bool
    detail: str

    def __post_init__(self) -> None:
        if (not isinstance(self.code, FailureCode)
                or self.code not in _DIAGNOSTICS
                or type(self.detail) is not str
                or self.detail != _DIAGNOSTICS.get(self.code)
                or type(self.retryable) is not bool
                or type(self.security_failure) is not bool):
            raise ValueError('invalid transport failure')

    def __str__(self) -> str:
        return f'{self.code.value}: {self.detail}'


def _failure(code: FailureCode) -> TransportFailure:
    return TransportFailure(code, code in _RETRYABLE_CODES, code in _SECURITY_CODES,
                            _DIAGNOSTICS[code])


class HttpTransport(Protocol):
    def post(self, *, endpoint: ProviderEndpoint, headers: Mapping[str, str],
             body: bytes, timeout_s: float) -> HttpResponse:
        raise AssertionError('protocol method executed')


def _remaining(monotonic: Callable[[], float], deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError()
    return remaining


class _DeadlineReader(io.RawIOBase):
    """Clamp every raw read, including reads inside HTTP header/chunk parsing."""

    def __init__(self, sock, monotonic, deadline):
        self._sock = sock
        self._raw = sock.makefile('rb', buffering=0)
        self._monotonic = monotonic
        self._deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        self._sock.settimeout(_remaining(self._monotonic, self._deadline))
        result = self._raw.readinto(buffer)
        _remaining(self._monotonic, self._deadline)
        return result

    def close(self):
        try:
            self._raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    def __init__(self, sock, monotonic, deadline):
        self._sock = sock
        self._monotonic = monotonic
        self._deadline = deadline

    def settimeout(self, timeout):
        self._sock.settimeout(min(timeout, _remaining(self._monotonic, self._deadline)))

    def sendall(self, data):
        self._sock.settimeout(_remaining(self._monotonic, self._deadline))
        self._sock.sendall(data)
        _remaining(self._monotonic, self._deadline)

    def makefile(self, mode):
        return io.BufferedReader(_DeadlineReader(self._sock, self._monotonic, self._deadline))

    def close(self):
        self._sock.close()


class _ResolvedHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, *, host, port, address, context, timeout, monotonic, deadline):
        super().__init__(host, port, timeout=timeout, context=context)
        self._address = address
        self._monotonic = monotonic
        self._deadline = deadline
        # No implicit reconnect if a write or response fails.
        self.auto_open = 0

    def connect(self):
        family, kind, protocol, _, address = self._address
        raw = socket.socket(family, kind, protocol)
        try:
            raw.settimeout(_remaining(self._monotonic, self._deadline))
            raw.connect(address)
            raw.settimeout(_remaining(self._monotonic, self._deadline))
            secured = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise
        self.sock = _DeadlineSocket(secured, self._monotonic, self._deadline)


def _resolve_worker(resolver, host, port, result, done, cancelled):
    # This worker owns DNS only. It cannot connect or send a request, even late.
    try:
        addresses = resolver(host, port, type=socket.SOCK_STREAM)
        outcome = addresses[0] if addresses else socket.gaierror()
    except Exception as error:
        outcome = error
    if not cancelled.is_set():
        result.append(outcome)
    done.set()


def _resolve(resolver, host, port, monotonic, deadline):
    result = []
    done, cancelled = threading.Event(), threading.Event()
    worker = threading.Thread(target=_resolve_worker,
                              args=(resolver, host, port, result, done, cancelled),
                              daemon=True)
    worker.start()
    try:
        while not done.wait(min(0.05, _remaining(monotonic, deadline))):
            pass
        _remaining(monotonic, deadline)
        if cancelled.is_set() or not result:
            raise socket.gaierror()
        if isinstance(result[0], Exception):
            raise result[0]
        return result[0]
    finally:
        cancelled.set()


def _exception_failure(error: Exception, phase: TransportPhase) -> TransportFailure:
    if isinstance(error, ssl.SSLError):
        code = FailureCode.TLS_VERIFICATION_FAILED
    elif isinstance(error, socket.gaierror) or phase is TransportPhase.RESOLVING:
        code = FailureCode.DNS_FAILURE
    elif isinstance(error, TimeoutError):
        code = (FailureCode.READ_TIMEOUT if phase is TransportPhase.READING
                else FailureCode.CONNECT_TIMEOUT)
    elif isinstance(error, ConnectionRefusedError) and phase is TransportPhase.CONNECTING:
        code = FailureCode.CONNECT_FAILED
    elif isinstance(error, (ConnectionResetError, BrokenPipeError, http.client.IncompleteRead)):
        code = FailureCode.CONNECTION_INTERRUPTED
    elif isinstance(error, OSError):
        code = (FailureCode.CONNECT_FAILED if phase is TransportPhase.CONNECTING
                else FailureCode.CONNECTION_INTERRUPTED)
    else:
        code = FailureCode.MALFORMED_PROVIDER_RESPONSE
    return _failure(code)


def _response_headers(response):
    if type(response.status) is not int or not 100 <= response.status <= 599:
        raise _failure(FailureCode.MALFORMED_PROVIDER_RESPONSE)
    if 300 <= response.status <= 399:
        raise _failure(FailureCode.REDIRECT_REFUSED)
    headers = {}
    for name, value in response.getheaders():
        key = name.lower()
        # Preserve repeats for gates; a later identity header must not hide gzip.
        headers[key] = headers[key] + ', ' + value if key in headers else value
    if 'content-encoding' in headers and headers['content-encoding'].strip().lower() != 'identity':
        raise _failure(FailureCode.UNSUPPORTED_CONTENT_ENCODING)
    length = None
    if 'content-length' in headers:
        raw = headers['content-length'].strip()
        if re.fullmatch(r'[0-9]+', raw) is None:
            raise _failure(FailureCode.MALFORMED_PROVIDER_RESPONSE)
        digits = raw.lstrip('0') or '0'
        # Bound conversion too: malicious headers can exceed Python's int limit.
        if len(digits) > 6 or int(digits) > _RESPONSE_LIMIT:
            raise _failure(FailureCode.RESPONSE_TOO_LARGE)
        length = int(digits)
    if 'transfer-encoding' in headers:
        if headers['transfer-encoding'].strip().lower() != 'chunked' or length is not None:
            raise _failure(FailureCode.MALFORMED_PROVIDER_RESPONSE)
    return headers, length


class StdlibHttpsTransport:
    def __init__(self, *, monotonic: Callable[[], float],
                 resolver: Callable = socket.getaddrinfo,
                 connection_factory: Callable = _ResolvedHttpsConnection):
        self._monotonic = monotonic
        self._resolver = resolver
        self._connection_factory = connection_factory

    def post(self, *, endpoint: ProviderEndpoint, headers: Mapping[str, str],
             body: bytes, timeout_s: float) -> HttpResponse:
        if type(endpoint) is not ProviderEndpoint or endpoint not in _ENDPOINTS:
            raise _failure(FailureCode.ENDPOINT_NOT_ALLOWED)
        outbound = {}
        seen = set()
        for name, value in headers.items():
            if (type(name) is not str
                    or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is None
                    or name.lower() in _RESERVED_HEADERS or name.lower() in seen
                    or type(value) is not str
                    or any(ord(char) < 32 or ord(char) > 126 for char in value)):
                raise _failure(FailureCode.INVALID_PROVIDER_REQUEST)
            seen.add(name.lower())
            outbound[name] = value
        if type(body) is not bytes:
            raise _failure(FailureCode.INVALID_PROVIDER_REQUEST)
        if len(body) > _REQUEST_LIMIT:
            raise _failure(FailureCode.INPUT_TOO_LARGE)
        if (type(timeout_s) not in (float, int) or timeout_s <= 0
                or not math.isfinite(timeout_s)):
            raise _failure(FailureCode.INVALID_PROVIDER_REQUEST)
        outbound.update({'Host': endpoint.host, 'Content-Length': str(len(body)),
                         'Connection': 'close', 'Accept-Encoding': 'identity'})
        deadline = self._monotonic() + timeout_s
        phase = TransportPhase.RESOLVING
        connection = None
        response = None
        try:
            address = _resolve(self._resolver, endpoint.host, endpoint.port,
                               self._monotonic, deadline)
            phase = TransportPhase.CONNECTING
            context = ssl.create_default_context()
            connection = self._connection_factory(
                host=endpoint.host, port=endpoint.port, address=address, context=context,
                timeout=_remaining(self._monotonic, deadline),
                monotonic=self._monotonic, deadline=deadline,
            )
            connection.connect()
            self._clamp(connection, deadline)
            connection.request('POST', endpoint.path, body=body, headers=outbound)
            phase = TransportPhase.READING
            self._clamp(connection, deadline)
            response = connection.getresponse()
            _remaining(self._monotonic, deadline)
            response_headers, length = _response_headers(response)
            chunks = []
            total = 0
            while True:
                self._clamp(connection, deadline)
                chunk = response.read1(min(16384, _RESPONSE_LIMIT + 1 - total))
                _remaining(self._monotonic, deadline)
                if not chunk:
                    break
                total += len(chunk)
                chunks.append(chunk)
                if total > _RESPONSE_LIMIT:
                    raise _failure(FailureCode.RESPONSE_TOO_LARGE)
            if length is not None and total != length:
                raise _failure(FailureCode.CONNECTION_INTERRUPTED)
            return HttpResponse(response.status, response_headers, b''.join(chunks))
        except TransportFailure:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise _exception_failure(error, phase) from None
        finally:
            # Cleanup must not replace the classified outcome or disclose text
            # from an OS error; attempt both independent resource closures.
            for resource in (response, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except OSError:
                        pass

    def _clamp(self, connection, deadline):
        remaining = _remaining(self._monotonic, deadline)
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
