"""Transport-only tests for the optional staffing Manager API seam."""

from __future__ import annotations

import http.client
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

from nxt_site_agent import SiteAgentApiServer, SiteAgentError


STAFFING_STATUS_BY_CODE = {
    "staffing_invalid_request": 400,
    "staffing_not_found": 404,
    "staffing_request_not_found": 404,
    "staffing_conflict": 409,
    "staffing_stale_suggestion": 409,
    "staffing_busy": 429,
    "staffing_unavailable": 503,
}
STAFFING_DETAIL_BY_CODE = {
    "staffing_invalid_request": "request body is invalid",
    "staffing_not_found": "staffing resource was not found",
    "staffing_request_not_found": (
        "no committed request in verified evidence"
    ),
    "staffing_conflict": (
        "request_id is already bound to different content"
    ),
    "staffing_stale_suggestion": "staffing basis has changed",
    "staffing_busy": "generation capacity is full",
    "staffing_unavailable": "staffing evidence is unavailable",
}

STAFFING_ROUTES = (
    ("GET", "/api/v1/staffing", 200),
    ("GET", "/api/v1/staffing/dates/2026-10-03", 200),
    (
        "GET",
        "/api/v1/staffing/requests/suggestion-generate/request-1",
        200,
    ),
    ("POST", "/api/v1/staffing/roster-imports", 200),
    ("POST", "/api/v1/staffing/exceptions", 200),
    ("POST", "/api/v1/staffing/exceptions/exception-1/cancel", 200),
    ("POST", "/api/v1/staffing/exceptions/exception-1/correct", 200),
    ("POST", "/api/v1/staffing/suggestions", 202),
    ("POST", "/api/v1/staffing/suggestions/suggestion-1/accept", 200),
    ("POST", "/api/v1/staffing/suggestions/suggestion-1/modify", 200),
    ("POST", "/api/v1/staffing/suggestions/suggestion-1/reject", 200),
)

STAFFING_POST_PATHS = tuple(
    path for method, path, _ in STAFFING_ROUTES if method == "POST"
)


@dataclass
class RecordingCallback:
    result: object = field(
        default_factory=lambda: {
            "schema": "nxt-staffing/v1",
            "environment": "SIMULATION",
        }
    )
    error: BaseException | None = None
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def __call__(
        self, method: str, path: str, body: dict[str, Any]
    ) -> object:
        self.calls.append((method, path, body))
        if self.error is not None:
            raise self.error
        return self.result


class JsonDictSubclass(dict):
    """A dict callback result that must be normalized before framing."""


class ExplodingItemsDict(dict):
    def items(self):
        raise RuntimeError("secret-body")


@dataclass(frozen=True)
class RunningStaffingServer:
    server: SiteAgentApiServer
    callback: RecordingCallback | Callable[..., object] | None


@pytest.fixture()
def start_staffing_server(tmp_path, launch):
    """Start independent servers while keeping each test's cleanup local."""

    running: list[tuple[object, SiteAgentApiServer]] = []

    def start(
        callback: RecordingCallback | Callable[..., object] | None,
    ) -> RunningStaffingServer:
        service = launch(tmp_path / f"runs-{len(running)}")
        server = SiteAgentApiServer(
            service,
            port=0,
            staffing_operations=callback,
        )
        server.start_background()
        running.append((service, server))
        return RunningStaffingServer(server=server, callback=callback)

    yield start

    for service, server in reversed(running):
        server.shutdown()
        service.stop()  # type: ignore[attr-defined]


def request(
    server: SiteAgentApiServer,
    method: str,
    path: str,
    body: bytes | str | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    connection = http.client.HTTPConnection(
        server.host, server.port, timeout=10
    )
    request_headers = dict(headers or {})
    if body is None and method == "POST":
        body = b"{}"
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        return response.status, payload, {
            key.lower(): value for key, value in response.getheaders()
        }
    finally:
        connection.close()


def json_object_of_size(size: int) -> bytes:
    prefix = b'{"value":"'
    suffix = b'"}'
    assert size >= len(prefix) + len(suffix)
    return prefix + (b"x" * (size - len(prefix) - len(suffix))) + suffix


def request_with_declared_length(
    server: SiteAgentApiServer, path: str, length: int
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Send headers only when the server must reject before reading a body."""

    connection = http.client.HTTPConnection(
        server.host, server.port, timeout=10
    )
    try:
        connection.putrequest("POST", path)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(length))
        connection.endheaders()
        response = connection.getresponse()
        payload = json.loads(response.read())
        return response.status, payload, {
            key.lower(): value for key, value in response.getheaders()
        }
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("method", "path", "expected_status"), STAFFING_ROUTES
)
def test_staffing_routes_delegate_once_with_query_stripped(
    start_staffing_server, method, path, expected_status
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, payload, _ = request(
        running.server, method, path + "?ignored=1"
    )

    assert status == expected_status
    assert callback.calls == [(method, path, {})]
    assert payload["data"]["schema"] == "nxt-staffing/v1"


@pytest.mark.parametrize(
    ("code", "expected_status"), STAFFING_STATUS_BY_CODE.items()
)
def test_staffing_error_codes_have_fixed_http_status(
    start_staffing_server, code, expected_status
):
    callback = RecordingCallback(error=SiteAgentError(code, "secret-body"))
    running = start_staffing_server(callback)

    status, payload, _ = request(running.server, "GET", "/api/v1/staffing")

    assert status == expected_status
    assert payload["error"] == {
        "code": code,
        "detail": STAFFING_DETAIL_BY_CODE[code],
    }
    assert "secret-body" not in json.dumps(payload, sort_keys=True)
    assert callback.calls == [("GET", "/api/v1/staffing", {})]


def test_missing_optional_callback_fails_only_staffing_routes(
    start_staffing_server,
):
    running = start_staffing_server(None)

    status, payload, _ = request(running.server, "GET", "/api/v1/staffing")
    health_status, health_payload, _ = request(
        running.server, "GET", "/api/v0/health"
    )

    assert status == 503
    assert payload["error"] == {
        "code": "staffing_unavailable",
        "detail": "staffing evidence is unavailable",
    }
    assert health_status == 200
    assert health_payload["data"]["service_state"] == "serving"


@pytest.mark.parametrize(
    "callback",
    (
        RecordingCallback(error=RuntimeError("secret-body")),
        RecordingCallback(
            error=SiteAgentError("invalid_request", "secret-body")
        ),
        RecordingCallback(result=["secret-body"]),
        RecordingCallback(result="secret-body"),
        RecordingCallback(result=None),
        RecordingCallback(result=ExplodingItemsDict(secret="secret-body")),
        RecordingCallback(result={"value": "\ud800secret-body"}),
        RecordingCallback(result={"value": float("nan")}),
    ),
)
def test_unexpected_callback_failure_or_return_is_generic_and_redacted(
    start_staffing_server, callback, capsys
):
    running = start_staffing_server(callback)

    status, payload, _ = request(running.server, "GET", "/api/v1/staffing")

    captured = capsys.readouterr()
    serialized = json.dumps(payload, sort_keys=True)
    assert status == 503
    assert payload["error"] == {
        "code": "staffing_unavailable",
        "detail": "staffing evidence is unavailable",
    }
    assert "secret-body" not in serialized
    assert "RuntimeError" not in serialized
    assert "TypeError" not in serialized
    assert "secret-body" not in captured.out
    assert "secret-body" not in captured.err


def test_callback_dict_subclass_is_normalized_to_json_safe_builtins(
    start_staffing_server,
):
    callback = RecordingCallback(
        result=JsonDictSubclass(
            {
                "schema": "nxt-staffing/v1",
                "nested": ("one", "two"),
            }
        )
    )
    running = start_staffing_server(callback)

    status, payload, _ = request(running.server, "GET", "/api/v1/staffing")

    assert status == 200
    assert payload["data"] == {
        "schema": "nxt-staffing/v1",
        "nested": ["one", "two"],
    }


@pytest.mark.parametrize(
    ("method", "path", "expected_status", "expected_code"),
    (
        ("GET", "/api/v1/staffing/unknown", 404, "staffing_not_found"),
        ("POST", "/api/v1/staffing/unknown", 404, "staffing_not_found"),
        ("PUT", "/api/v1/staffing/unknown", 404, "staffing_not_found"),
        ("GET", "/api/v1/staffing/roster-imports", 405, "method_not_allowed"),
        ("POST", "/api/v1/staffing", 405, "method_not_allowed"),
        ("PUT", "/api/v1/staffing", 405, "method_not_allowed"),
        ("PATCH", "/api/v1/staffing/exceptions", 405, "method_not_allowed"),
        (
            "DELETE",
            "/api/v1/staffing/suggestions/suggestion-1/reject",
            405,
            "method_not_allowed",
        ),
    ),
)
def test_unknown_shape_is_distinct_from_known_shape_with_wrong_method(
    start_staffing_server, method, path, expected_status, expected_code
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, payload, _ = request(running.server, method, path)

    assert status == expected_status
    assert payload["error"]["code"] == expected_code
    assert callback.calls == []


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/api/v1/staffing/"),
        ("GET", "/api/v1/staffing/dates/"),
        ("GET", "/api/v1/staffing/dates/2026-10-03/extra"),
        ("GET", "/api/v1/staffing/requests/suggestion-generate"),
        ("POST", "/api/v1/staffing/roster-imports/extra"),
        ("POST", "/api/v1/staffing/exceptions/exception-1/archive"),
        ("POST", "/api/v1/staffing/suggestions/suggestion-1/execute"),
    ),
)
def test_near_miss_paths_do_not_expand_the_exact_allowlist(
    start_staffing_server, method, path
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, payload, _ = request(running.server, method, path)

    assert status == 404
    assert payload["error"]["code"] == "staffing_not_found"
    assert callback.calls == []


@pytest.mark.parametrize("path", STAFFING_POST_PATHS)
@pytest.mark.parametrize(
    "raw",
    (
        b'{"request_id":"a","request_id":"b"}',
        b'{"minutes":NaN}',
        b'{"minutes":Infinity}',
        b'{"minutes":-Infinity}',
        b'{"minutes":1e10000}',
    ),
)
def test_every_staffing_post_rejects_duplicate_keys_and_nonfinite_numbers(
    start_staffing_server, path, raw
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, payload, _ = request(running.server, "POST", path, raw)

    assert status == 400
    assert payload["error"] == {
        "code": "staffing_invalid_request",
        "detail": "request body is invalid",
    }
    assert callback.calls == []


@pytest.mark.parametrize(
    "raw",
    (
        b"\xff",
        b'{"unterminated":',
        b"[]",
        b"null",
        b'"string"',
        b"7",
        b"true",
        b'{"value":' + (b"[" * 10_000) + b"0" + (b"]" * 10_000) + b"}",
    ),
)
def test_staffing_post_rejects_invalid_utf8_syntax_and_nonobject_roots(
    start_staffing_server, raw
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, payload, _ = request(
        running.server, "POST", "/api/v1/staffing/exceptions", raw
    )

    assert status == 400
    assert payload["error"] == {
        "code": "staffing_invalid_request",
        "detail": "request body is invalid",
    }
    assert callback.calls == []


def test_ordinary_staffing_body_limit_is_exact_and_overflow_closes(
    start_staffing_server,
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, _, _ = request(
        running.server,
        "POST",
        "/api/v1/staffing/exceptions",
        json_object_of_size(65_536),
    )
    overflow_status, overflow_payload, overflow_headers = (
        request_with_declared_length(
            running.server,
            "/api/v1/staffing/exceptions",
            65_537,
        )
    )

    assert status == 200
    assert len(callback.calls) == 1
    assert overflow_status == 413
    assert overflow_payload["error"]["code"] == "body_too_large"
    assert overflow_headers["connection"].lower() == "close"
    assert len(callback.calls) == 1


def test_exact_roster_route_has_one_mebibyte_limit_and_no_other_route_does(
    start_staffing_server,
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, _, _ = request(
        running.server,
        "POST",
        "/api/v1/staffing/roster-imports?ignored=1",
        json_object_of_size(1_048_576),
    )
    overflow_status, overflow_payload, overflow_headers = (
        request_with_declared_length(
            running.server,
            "/api/v1/staffing/roster-imports",
            1_048_577,
        )
    )
    near_miss_status, near_miss_payload, near_miss_headers = (
        request_with_declared_length(
            running.server,
            "/api/v1/staffing/roster-imports/extra",
            65_537,
        )
    )

    assert status == 200
    assert callback.calls[0][1] == "/api/v1/staffing/roster-imports"
    assert overflow_status == 413
    assert overflow_payload["error"]["code"] == "body_too_large"
    assert overflow_headers["connection"].lower() == "close"
    assert near_miss_status == 404
    assert near_miss_payload["error"]["code"] == "staffing_not_found"
    assert near_miss_headers["connection"].lower() == "close"
    assert len(callback.calls) == 1


def test_foreign_host_and_origin_are_rejected_before_staffing_dispatch(
    start_staffing_server,
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    host_status, host_payload, _ = request(
        running.server,
        "GET",
        "/api/v1/staffing",
        headers={"Host": "evil.example"},
    )
    origin_status, origin_payload, origin_headers = request(
        running.server,
        "POST",
        "/api/v1/staffing/exceptions",
        headers={"Origin": "https://evil.example"},
    )
    put_status, put_payload, put_headers = request(
        running.server,
        "PUT",
        "/api/v1/staffing",
        headers={"Origin": "https://evil.example"},
    )

    assert host_status == 403
    assert host_payload["error"]["code"] == "forbidden_origin"
    assert origin_status == 403
    assert origin_payload["error"]["code"] == "forbidden_origin"
    assert origin_headers["connection"].lower() == "close"
    assert put_status == 403
    assert put_payload["error"]["code"] == "forbidden_origin"
    assert put_headers["connection"].lower() == "close"
    assert callback.calls == []


@pytest.mark.parametrize(
    ("body", "headers"),
    (
        (b"{}", {"Host": "evil.example"}),
        (
            None,
            {
                "Origin": "https://evil.example",
                "Transfer-Encoding": "chunked",
            },
        ),
    ),
)
def test_framed_staffing_get_closes_even_when_host_or_origin_is_refused(
    start_staffing_server, body, headers
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, payload, response_headers = request(
        running.server,
        "GET",
        "/api/v1/staffing",
        body,
        headers=headers,
    )

    assert status == 403
    assert payload["error"]["code"] == "forbidden_origin"
    assert response_headers["connection"].lower() == "close"
    assert callback.calls == []


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_code"),
    (
        ("/api/v1/staffing", 405, "method_not_allowed"),
        ("/api/v1/staffing/unknown", 404, "staffing_not_found"),
    ),
)
def test_post_classifies_staffing_path_before_reading_wrong_method_body(
    start_staffing_server, path, expected_status, expected_code
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, payload, headers = request(
        running.server, "POST", path, b"not-json"
    )

    assert status == expected_status
    assert payload["error"]["code"] == expected_code
    assert headers["connection"].lower() == "close"
    assert callback.calls == []


def test_chunked_staffing_body_keeps_existing_framing_error_and_closes(
    start_staffing_server,
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)
    connection = http.client.HTTPConnection(
        running.server.host, running.server.port, timeout=10
    )
    try:
        connection.putrequest("POST", "/api/v1/staffing/exceptions")
        connection.putheader("Transfer-Encoding", "chunked")
        connection.endheaders()
        connection.send(b"0\r\n\r\n")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 400
        assert payload["error"]["code"] == "invalid_request"
        assert response.getheader("Connection", "").lower() == "close"
        assert callback.calls == []
    finally:
        connection.close()


def test_staffing_get_with_body_dispatches_empty_body_then_closes(
    start_staffing_server,
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, _, headers = request(
        running.server, "GET", "/api/v1/staffing", b"{}"
    )

    assert status == 200
    assert headers["connection"].lower() == "close"
    assert callback.calls == [("GET", "/api/v1/staffing", {})]


@pytest.mark.parametrize("method", ("PUT", "PATCH", "DELETE"))
def test_other_staffing_mutation_methods_never_dispatch_and_close_on_body(
    start_staffing_server, method
):
    callback = RecordingCallback()
    running = start_staffing_server(callback)

    status, payload, headers = request(
        running.server, method, "/api/v1/staffing/suggestions", b"{}"
    )

    assert status == 405
    assert payload["error"]["code"] == "method_not_allowed"
    assert headers["connection"].lower() == "close"
    assert callback.calls == []
