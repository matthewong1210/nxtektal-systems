"""Bounded transport tests: all resolver and network operations are injected."""

from dataclasses import dataclass
import http.client
import io
import socket
import ssl
import threading
import traceback

import pytest

from nxt_model_gateway import FailureCode, Provider
from nxt_model_gateway.transport import (
    HttpResponse, ProviderEndpoint, StdlibHttpsTransport, TransportFailure,
    TransportPhase, _ANTHROPIC_ENDPOINT, _KIMI_ENDPOINT, _OPENAI_ENDPOINT,
    _ResolvedHttpsConnection,
)


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class FakeResponse:
    def __init__(self, status=200, body=b'{}', headers=None, failure=None, step=None):
        self.status = status
        self.headers = headers or {}
        self.stream = io.BytesIO(body)
        self.failure = failure
        self.step = step or (lambda: None)
        self.read_calls = []
        self.total_bytes_returned = 0

    def getheaders(self):
        return list(self.headers.items())

    def read1(self, size):
        self.read_calls.append(size)
        if self.failure:
            raise self.failure
        self.step()
        result = self.stream.read(min(size, 16384))
        self.total_bytes_returned += len(result)
        return result

    def close(self):
        self.stream.close()


class FakeHttpsConnection:
    def __init__(self, response=None, connect_failure=None, response_failure=None):
        self.response = response or FakeResponse()
        self.connect_failure = connect_failure
        self.response_failure = response_failure
        self.sock = self
        self.timeouts = []
        self.close_count = 0
        self.request_count = 0

    def connect(self):
        if self.connect_failure:
            raise self.connect_failure

    def settimeout(self, value):
        self.timeouts.append(value)

    def request(self, method, path, body, headers):
        self.request_count += 1
        self.request_args = (method, path)
        self.request_headers = headers
        self.body = body

    def getresponse(self):
        if self.response_failure:
            raise self.response_failure
        return self.response

    def close(self):
        self.close_count += 1


class RecordingFactory:
    def __init__(self, fake):
        self.fake = fake
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.fake


def resolver(host, port, *, type):
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('203.0.113.10', port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('203.0.113.11', port))]


def transport(fake=None, *, clock=None, resolve=resolver, factory=None):
    return StdlibHttpsTransport(
        monotonic=clock or Clock(), resolver=resolve,
        connection_factory=factory or RecordingFactory(fake or FakeHttpsConnection()),
    )


def post(instance, **kwargs):
    args = dict(endpoint=_OPENAI_ENDPOINT, headers={'Authorization': 'Bearer fixture-key'},
                body=b'private-request-body', timeout_s=2.0)
    args.update(kwargs)
    return instance.post(**args)


@pytest.mark.parametrize('endpoint,host,path', [
    (_OPENAI_ENDPOINT, 'api.openai.com', '/v1/responses'),
    (_KIMI_ENDPOINT, 'api.moonshot.ai', '/v1/chat/completions'),
    (_ANTHROPIC_ENDPOINT, 'api.anthropic.com', '/v1/messages'),
])
def test_direct_verified_tls_identity_encoding_and_close(monkeypatch, endpoint, host, path):
    monkeypatch.setenv('HTTPS_PROXY', 'http://attacker.invalid:8080')
    monkeypatch.setenv('ALL_PROXY', 'http://attacker.invalid:8080')
    fake = FakeHttpsConnection()
    factory = RecordingFactory(fake)
    result = post(transport(factory=factory), endpoint=endpoint, body='你好'.encode())
    assert result.status_code == 200
    assert result.body == b'{}'
    assert fake.request_args == ('POST', path)
    assert fake.request_headers == {
        'Authorization': 'Bearer fixture-key', 'Host': host,
        'Content-Length': '6', 'Connection': 'close', 'Accept-Encoding': 'identity',
    }
    assert len(factory.calls) == 1
    call = factory.calls[0]
    assert call['host'] == host
    assert call['address'] == (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('203.0.113.10', 443))
    assert call['context'].check_hostname is True
    assert call['context'].verify_mode == ssl.CERT_REQUIRED
    assert fake.close_count == 1


@pytest.mark.parametrize('endpoint', [
    ProviderEndpoint(Provider.OPENAI, 'attacker.invalid', 443, '/v1/responses'),
    ProviderEndpoint(Provider.OPENAI, 'api.openai.com', 80, '/v1/responses'),
    ProviderEndpoint(Provider.OPENAI, 'api.openai.com', 443, '//attacker.invalid'),
    ProviderEndpoint(Provider.KIMI, 'api.openai.com', 443, '/v1/responses'),
    'https://api.openai.com/v1/responses',
])
def test_endpoint_rejected_before_resolution(endpoint):
    calls = []
    with pytest.raises(TransportFailure) as raised:
        post(transport(resolve=lambda *a, **k: calls.append(a)), endpoint=endpoint)
    assert raised.value.code is FailureCode.ENDPOINT_NOT_ALLOWED
    assert raised.value.security_failure is True
    assert raised.value.retryable is False
    assert calls == []


@pytest.mark.parametrize('headers', [
    {name: 'value'} for name in ('host', 'HOST', 'Content-Length', 'tRaNsFeR-EnCoDiNg',
                               'Connection', 'Accept-Encoding', 'bad\r\nname', 'bad name')
] + [{'Authorization': 'key\r\nx-api-key: injected'}, {'x-api-key': 'key\nvalue'},
     {'X-Key': 'a', 'x-key': 'b'}, {'Authorization': '不可编码'}])
def test_reserved_or_invalid_headers_fail_before_resolution(headers):
    calls = []
    with pytest.raises(TransportFailure) as raised:
        post(transport(resolve=lambda *a, **k: calls.append(a)), headers=headers)
    assert raised.value.code is FailureCode.INVALID_PROVIDER_REQUEST
    assert raised.value.retryable is False
    assert calls == []


def test_request_size_limit_is_checked_before_resolution_and_factory():
    calls = []
    factory = RecordingFactory(FakeHttpsConnection())
    with pytest.raises(TransportFailure) as raised:
        post(transport(resolve=lambda *a, **k: calls.append(a), factory=factory),
             body=b'x' * 262145)
    assert (raised.value.code, raised.value.retryable, raised.value.security_failure) == (
        FailureCode.INPUT_TOO_LARGE, False, False)
    assert calls == factory.calls == []


def test_exact_request_and_response_limits_are_accepted():
    fake = FakeHttpsConnection(FakeResponse(body=b'x' * 524288,
                                         headers={'Content-Length': '524288'}))
    result = post(transport(fake), body=b'x' * 262144)
    assert len(result.body) == 524288
    assert fake.request_headers['Content-Length'] == '262144'
    assert fake.close_count == 1


@pytest.mark.parametrize('status', [300, 301, 302, 303, 304, 307, 308, 399])
def test_redirect_is_security_failure_without_body_read_or_follow(status):
    response = FakeResponse(status, headers={'Location': 'https://attacker.invalid'})
    fake = FakeHttpsConnection(response)
    with pytest.raises(TransportFailure) as raised:
        post(transport(fake))
    assert (raised.value.code, raised.value.retryable, raised.value.security_failure) == (
        FailureCode.REDIRECT_REFUSED, False, True)
    assert fake.request_count == fake.close_count == 1
    assert response.read_calls == []


@pytest.mark.parametrize('encoding', ['gzip', 'br', 'deflate', 'identity, gzip', ''])
def test_compressed_response_is_refused_before_read(encoding):
    response = FakeResponse(headers={'Content-Encoding': encoding})
    fake = FakeHttpsConnection(response)
    with pytest.raises(TransportFailure) as raised:
        post(transport(fake))
    assert (raised.value.code, raised.value.retryable, raised.value.security_failure) == (
        FailureCode.UNSUPPORTED_CONTENT_ENCODING, False, True)
    assert response.read_calls == []
    assert fake.close_count == 1


def test_content_length_limit_rejected_before_read():
    response = FakeResponse(headers={'Content-Length': '524289'})
    fake = FakeHttpsConnection(response)
    with pytest.raises(TransportFailure) as raised:
        post(transport(fake))
    assert (raised.value.code, raised.value.retryable, raised.value.security_failure) == (
        FailureCode.RESPONSE_TOO_LARGE, False, True)
    assert response.read_calls == []
    assert fake.close_count == 1


def test_stream_limit_reads_only_one_sentinel_byte_extra():
    response = FakeResponse(body=b'x' * 600000)
    fake = FakeHttpsConnection(response)
    with pytest.raises(TransportFailure) as raised:
        post(transport(fake))
    assert (raised.value.code, raised.value.retryable, raised.value.security_failure) == (
        FailureCode.RESPONSE_TOO_LARGE, False, True)
    assert response.total_bytes_returned == 524289
    assert fake.close_count == 1


@dataclass
class FailureCase:
    exception: Exception
    phase: TransportPhase
    code: FailureCode
    retryable: bool
    security_failure: bool


FAILURE_CASES = (
    FailureCase(socket.gaierror(-2, 'name failure'), TransportPhase.RESOLVING,
                FailureCode.DNS_FAILURE, True, False),
    FailureCase(TimeoutError(), TransportPhase.CONNECTING,
                FailureCode.CONNECT_TIMEOUT, True, False),
    FailureCase(ssl.SSLCertVerificationError(1, 'certificate failure'),
                TransportPhase.CONNECTING, FailureCode.TLS_VERIFICATION_FAILED, False, True),
    FailureCase(TimeoutError(), TransportPhase.READING,
                FailureCode.READ_TIMEOUT, True, False),
    FailureCase(ConnectionResetError(), TransportPhase.READING,
                FailureCode.CONNECTION_INTERRUPTED, True, False),
    FailureCase(http.client.IncompleteRead(b'', 1), TransportPhase.READING,
                FailureCode.CONNECTION_INTERRUPTED, True, False),
    FailureCase(BrokenPipeError('fixture-key'), TransportPhase.CONNECTING,
                FailureCode.CONNECTION_INTERRUPTED, True, False),
    FailureCase(OSError(5, 'private-response-body'), TransportPhase.READING,
                FailureCode.CONNECTION_INTERRUPTED, True, False),
    FailureCase(ssl.SSLError('fixture-key'), TransportPhase.READING,
                FailureCode.TLS_VERIFICATION_FAILED, False, True),
)


@pytest.mark.parametrize('case', FAILURE_CASES)
def test_phase_failures_are_typed_closed_and_redacted(case):
    def fail_resolver(*args, **kwargs):
        raise case.exception

    fake = FakeHttpsConnection(
        FakeResponse(body=b'private-response-body',
                     failure=case.exception if case.phase is TransportPhase.READING else None),
        connect_failure=case.exception if case.phase is TransportPhase.CONNECTING else None,
    )
    with pytest.raises(TransportFailure) as raised:
        post(transport(fake, resolve=fail_resolver if case.phase is TransportPhase.RESOLVING
                       else resolver))
    failure = raised.value
    assert (failure.code, failure.retryable, failure.security_failure) == (
        case.code, case.retryable, case.security_failure)
    assert fake.close_count == (0 if case.phase is TransportPhase.RESOLVING else 1)
    diagnostic = str(failure) + repr(failure) + ''.join(traceback.format_exception(failure))
    for secret in ('Authorization', 'x-api-key', 'fixture-key', 'private-request-body',
                   'private-response-body'):
        assert secret not in diagnostic


@pytest.mark.parametrize('failure', [ConnectionRefusedError(111, 'refused'), OSError(113, 'no route')])
def test_connect_os_errors_are_retryable_connect_failed(failure):
    fake = FakeHttpsConnection(connect_failure=failure)
    with pytest.raises(TransportFailure) as raised:
        post(transport(fake))
    assert (raised.value.code, raised.value.retryable, raised.value.security_failure) == (
        FailureCode.CONNECT_FAILED, True, False)
    assert fake.request_count == 0
    assert fake.close_count == 1


def test_waiting_for_response_headers_is_read_phase():
    fake = FakeHttpsConnection(response_failure=TimeoutError())
    with pytest.raises(TransportFailure) as raised:
        post(transport(fake))
    assert raised.value.code is FailureCode.READ_TIMEOUT
    assert fake.close_count == 1


def test_blocking_resolver_deadline_and_late_result_never_connect():
    clock = Clock()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    worker_threads, outcomes = [], []
    fake = FakeHttpsConnection()
    factory = RecordingFactory(fake)

    def blocked(host, port, *, type):
        worker_threads.append(threading.current_thread())
        started.set()
        release.wait(2)
        finished.set()
        return resolver(host, port, type=type)

    def invoke():
        try:
            post(transport(clock=clock, resolve=blocked, factory=factory))
        except TransportFailure as error:
            outcomes.append(error)

    caller = threading.Thread(target=invoke)
    caller.start()
    try:
        assert started.wait(1)
        clock.now = 2.1
        caller.join(0.5)
        assert not caller.is_alive()
        assert len(outcomes) == 1
        assert (outcomes[0].code, outcomes[0].retryable) == (FailureCode.DNS_FAILURE, True)
        assert factory.calls == []
        assert worker_threads[0].daemon is True
    finally:
        release.set()
        caller.join(2)
        assert finished.wait(1)
        for worker in worker_threads:
            worker.join(1)
    assert factory.calls == []
    assert fake.request_count == 0


def test_body_read_deadline_discards_late_response_and_clamps_timeouts():
    clock = Clock()

    def advance():
        clock.now += 0.75

    fake = FakeHttpsConnection(FakeResponse(body=b'x' * 65536, step=advance))
    with pytest.raises(TransportFailure) as raised:
        post(transport(fake, clock=clock))
    assert raised.value.code is FailureCode.READ_TIMEOUT
    assert min(fake.timeouts) == 0.5
    assert fake.close_count == 1


def test_response_copies_headers_body_and_hides_body():
    headers, body = {'X-Request-ID': 'abc'}, bytearray(b'private-response-body')
    result = HttpResponse(200, headers, body)
    headers['X-Request-ID'] = 'changed'
    body[0] = 0
    assert result.headers == {'x-request-id': 'abc'}
    assert result.body == b'private-response-body'
    with pytest.raises(TypeError):
        result.headers['x'] = 'y'
    assert 'private-response-body' not in repr(result)


@pytest.mark.parametrize('status', [99, 600, True, 200.0, '200'])
def test_response_rejects_invalid_status(status):
    with pytest.raises(ValueError):
        HttpResponse(status, {}, b'')


@pytest.mark.parametrize('code,detail', [
    (FailureCode.CONNECT_FAILED, 'fixture-key'),
    (FailureCode.AUTHENTICATION_FAILED, None),
])
def test_transport_failure_rejects_arbitrary_diagnostics(code, detail):
    with pytest.raises(ValueError):
        TransportFailure(code, True, False, detail)


def test_resolved_connection_uses_one_address_and_original_tls_hostname(monkeypatch):
    clock = Clock()
    calls = []

    class RawSocket:
        def settimeout(self, value):
            calls.append(('timeout', value))

        def connect(self, address):
            calls.append(('connect', address))
            clock.now = 0.5

        def close(self):
            calls.append(('close',))

    class Context:
        def wrap_socket(self, sock, *, server_hostname):
            calls.append(('tls', server_hostname))
            return sock

    monkeypatch.setattr(socket, 'socket', lambda *args: RawSocket())
    connection = _ResolvedHttpsConnection(
        host='api.openai.com', port=443, address=resolver('', 443, type=socket.SOCK_STREAM)[0],
        context=Context(), timeout=2.0, monotonic=clock, deadline=2.0,
    )
    connection.connect()
    connection.close()
    assert calls == [('timeout', 2.0), ('connect', ('203.0.113.10', 443)),
                     ('timeout', 1.5), ('tls', 'api.openai.com'), ('close',)]


@pytest.mark.parametrize('headers', [
    {'Content-Length': '-1'}, {'Content-Length': 'secret'},
    {'Content-Length': '2, 3'}, {'Content-Length': '2', 'Transfer-Encoding': 'chunked'},
    {'Transfer-Encoding': 'gzip'},
    {'Content-Length': '2', 'content-length': '2'},
    {'Content-Length': '2', 'content-length': '3'},
])
def test_malformed_response_framing_is_refused_before_read(headers):
    response = FakeResponse(headers=headers)
    with pytest.raises(TransportFailure) as raised:
        post(transport(FakeHttpsConnection(response)))
    assert raised.value.code is FailureCode.MALFORMED_PROVIDER_RESPONSE
    assert response.read_calls == []


def test_repeated_encoding_headers_cannot_hide_compression():
    response = FakeResponse(headers={'Content-Encoding': 'gzip', 'content-encoding': 'identity'})
    with pytest.raises(TransportFailure) as raised:
        post(transport(FakeHttpsConnection(response)))
    assert raised.value.code is FailureCode.UNSUPPORTED_CONTENT_ENCODING
    assert response.read_calls == []


def test_content_length_truncation_is_interrupted_not_success():
    with pytest.raises(TransportFailure) as raised:
        post(transport(FakeHttpsConnection(FakeResponse(body=b'{}', headers={'Content-Length': '9'}))))
    assert raised.value.code is FailureCode.CONNECTION_INTERRUPTED
    assert raised.value.retryable is True


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf'), True, '2'])
def test_invalid_timeout_is_rejected_before_factory(timeout):
    factory = RecordingFactory(FakeHttpsConnection())
    with pytest.raises(TransportFailure) as raised:
        post(transport(factory=factory), timeout_s=timeout)
    assert raised.value.code is FailureCode.INVALID_PROVIDER_REQUEST
    assert factory.calls == []


def test_empty_dns_result_never_connects():
    factory = RecordingFactory(FakeHttpsConnection())
    with pytest.raises(TransportFailure) as raised:
        post(transport(resolve=lambda *a, **k: [], factory=factory))
    assert raised.value.code is FailureCode.DNS_FAILURE
    assert factory.calls == []


def test_factory_connect_error_is_connect_failure():
    def fail(**kwargs):
        raise ConnectionRefusedError('fixture-key')

    with pytest.raises(TransportFailure) as raised:
        post(transport(factory=fail))
    assert raised.value.code is FailureCode.CONNECT_FAILED
    assert 'fixture-key' not in repr(raised.value)


@pytest.mark.parametrize('status', [99, 600])
def test_invalid_wire_status_is_typed_and_closed(status):
    fake = FakeHttpsConnection(FakeResponse(status=status))
    with pytest.raises(TransportFailure) as raised:
        post(transport(fake))
    assert raised.value.code is FailureCode.MALFORMED_PROVIDER_RESPONSE
    assert fake.close_count == 1


def test_cleanup_failure_cannot_leak_or_override_security_failure():
    class BadCloseResponse(FakeResponse):
        def close(self):
            raise OSError('private-response-body')

    class BadCloseConnection(FakeHttpsConnection):
        def close(self):
            super().close()
            raise OSError('fixture-key')

    fake = BadCloseConnection(BadCloseResponse(status=302))
    with pytest.raises(TransportFailure) as raised:
        post(transport(fake))
    assert raised.value.code is FailureCode.REDIRECT_REFUSED
    assert fake.close_count == 1
    assert 'fixture-key' not in repr(raised.value)
    assert 'private-response-body' not in str(raised.value)


@pytest.mark.parametrize('wire,advance,want_code', [
    (b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}', 0, None),
    (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n', 0, None),
    (b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}', 0.25, FailureCode.READ_TIMEOUT),
    (b'HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\n{}', 0, FailureCode.CONNECTION_INTERRUPTED),
])
def test_real_http_parser_over_fake_tls_socket(monkeypatch, wire, advance, want_code):
    clock = Clock()
    socket_calls, sends, timeouts, identities = [], [], [], []

    class RawStream(io.RawIOBase):
        def __init__(self):
            self.stream = io.BytesIO(wire)

        def readable(self):
            return True

        def readinto(self, buffer):
            clock.now += advance
            data = self.stream.read(1 if advance else len(buffer))
            buffer[:len(data)] = data
            return len(data)

    class Socket:
        def settimeout(self, value):
            timeouts.append(value)

        def connect(self, address):
            socket_calls.append(address)

        def makefile(self, mode, buffering):
            return RawStream()

        def sendall(self, data):
            sends.append(data)

        def close(self):
            pass

    class Context:
        def wrap_socket(self, raw, *, server_hostname):
            identities.append(server_hostname)
            return raw

    monkeypatch.setattr(socket, 'socket', lambda *args: Socket())
    monkeypatch.setattr(ssl, 'create_default_context', Context)
    instance = transport(clock=clock, factory=_ResolvedHttpsConnection)
    if want_code:
        with pytest.raises(TransportFailure) as raised:
            post(instance)
        assert raised.value.code is want_code
    else:
        assert post(instance).body == b'{}'
    assert socket_calls == [('203.0.113.10', 443)]
    assert identities == ['api.openai.com']
    wire_request = b''.join(sends)
    assert wire_request.startswith(b'POST /v1/responses HTTP/1.1\r\n')
    assert wire_request.count(b'Host: api.openai.com\r\n') == 1
    assert max(timeouts) <= 2.0
