"""Read-only Manager transport for V3 collection execution evidence."""
from __future__ import annotations

import http.client
import json
from copy import deepcopy
from pathlib import Path

import pytest

from nxt_site_agent import (
    API_SCHEMA_VERSION,
    DISCLAIMER,
    SiteAgentApiServer,
    SiteAgentError,
)


EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "docs/contracts/collection-execution-v1/examples/success.json"
)
_DEFAULT = object()


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _request(
    server: SiteAgentApiServer,
    method: str,
    path: str,
    *,
    body: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict]:
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return (
            response.status,
            dict(response.getheaders()),
            json.loads(response.read()),
        )
    finally:
        connection.close()


@pytest.fixture()
def execution_server(tmp_path, launch):
    service = launch(tmp_path / "site-agent")
    calls: list[object] = []
    state = {"snapshot": _DEFAULT, "request": _DEFAULT}
    example = _example()
    snapshot = example["snapshot"]["body"]["data"]
    receipt = next(
        item["body"]
        for item in example["bodies"]
        if item["schema_ref"] == "#/$defs/RequestReceipt"
    )

    def read_snapshot():
        calls.append(("snapshot", ()))
        value = state["snapshot"]
        if isinstance(value, BaseException):
            raise value
        return deepcopy(snapshot if value is _DEFAULT else value)

    def read_request(request_id: str):
        calls.append(("request", (request_id,)))
        value = state["request"]
        if isinstance(value, BaseException):
            raise value
        result = deepcopy(receipt if value is _DEFAULT else value)
        if value is _DEFAULT:
            result["request_id"] = request_id
        return result

    server = SiteAgentApiServer(
        service,
        collection_executions=read_snapshot,
        collection_execution_request=read_request,
    )
    server.start_background()
    yield server, calls, state
    server.shutdown()
    service.stop()


def test_snapshot_get_uses_existing_manager_envelope_and_no_store(execution_server):
    server, calls, _ = execution_server
    status, headers, payload = _request(
        server, "GET", "/api/v1/collection-executions"
    )

    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload == {
        "schema": API_SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "data": _example()["snapshot"]["body"]["data"],
    }
    assert calls == [("snapshot", ())]


def test_request_get_decodes_exactly_one_strict_segment(execution_server):
    server, calls, _ = execution_server
    status, headers, payload = _request(
        server,
        "GET",
        "/api/v1/collection-executions/requests/request%3A001",
    )

    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload["schema"] == API_SCHEMA_VERSION
    assert payload["disclaimer"] == DISCLAIMER
    assert payload["data"]["schema"] == (
        "nxt-collection-execution-request-receipt/v1"
    )
    assert payload["data"]["request_id"] == "request:001"
    assert calls == [("request", ("request:001",))]


@pytest.mark.parametrize(
    "segment",
    [
        "",
        "%",
        "%0",
        "%GG",
        "%FF",
        "%C3%A9",
        "request%2F001",
        "+request",
        "a" * 129,
    ],
)
def test_invalid_request_id_is_400_before_callback(execution_server, segment):
    server, calls, _ = execution_server
    status, _, payload = _request(
        server,
        "GET",
        f"/api/v1/collection-executions/requests/{segment}",
    )

    assert status == 400
    assert payload["error"]["code"] == "collection_execution_invalid_request"
    assert calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/collection-executions/unknown",
        "/api/v1/collection-executions/requests/request/001",
    ],
)
def test_unknown_collection_execution_path_uses_frozen_not_found(
    execution_server, path
):
    server, calls, _ = execution_server
    status, _, payload = _request(server, "GET", path)

    assert status == 404
    assert payload["error"]["code"] == "collection_execution_not_found"
    assert calls == []


@pytest.mark.parametrize("missing", ["snapshot", "request"])
def test_missing_reader_is_sanitized_unavailable(tmp_path, launch, missing):
    service = launch(tmp_path / "site-agent")
    keyword = (
        {"collection_execution_request": lambda request_id: {}}
        if missing == "snapshot"
        else {"collection_executions": lambda: {}}
    )
    server = SiteAgentApiServer(service, **keyword)
    server.start_background()
    try:
        path = (
            "/api/v1/collection-executions"
            if missing == "snapshot"
            else "/api/v1/collection-executions/requests/request-001"
        )
        status, _, payload = _request(server, "GET", path)
        assert status == 503
        assert payload["error"]["code"] == "collection_execution_unavailable"
    finally:
        server.shutdown()
        service.stop()


@pytest.mark.parametrize(
    ("reader", "bad_value"),
    [
        ("snapshot", None),
        ("snapshot", {"schema": "wrong", "environment": "SIMULATION"}),
        (
            "snapshot",
            {
                "schema": "nxt-collection-executions/v1",
                "environment": "PHYSICAL",
            },
        ),
        ("request", []),
        (
            "request",
            {
                "schema": "nxt-collection-execution-request/v1",
                "environment": "SIMULATION",
                "request_id": "request-001",
            },
        ),
        (
            "request",
            {
                "schema": "nxt-collection-execution-request-receipt/v1",
                "environment": "SIMULATION",
                "request_id": "different-request",
            },
        ),
    ],
)
def test_malformed_reader_data_is_sanitized_unavailable(
    execution_server, reader, bad_value
):
    server, calls, state = execution_server
    state[reader] = bad_value
    path = (
        "/api/v1/collection-executions"
        if reader == "snapshot"
        else "/api/v1/collection-executions/requests/request-001"
    )

    status, _, payload = _request(server, "GET", path)

    assert status == 503
    assert payload["error"] == {
        "code": "collection_execution_unavailable",
        "detail": "collection execution evidence is unavailable",
    }
    assert len(calls) == 1


def test_non_json_snapshot_value_is_sanitized_unavailable(execution_server):
    server, _, state = execution_server
    malformed = deepcopy(_example()["snapshot"]["body"]["data"])
    malformed["now_sim_t_s"] = float("nan")
    state["snapshot"] = malformed

    status, _, payload = _request(
        server, "GET", "/api/v1/collection-executions"
    )

    assert status == 503
    assert payload["error"]["code"] == "collection_execution_unavailable"


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("collection_execution_invalid_request", 400),
        ("collection_execution_not_found", 404),
        ("collection_execution_request_not_found", 404),
        ("collection_execution_conflict", 409),
        ("collection_execution_unavailable", 503),
        ("collection_execution_result_unknown", 503),
    ],
)
def test_recognized_reader_errors_preserve_code_and_detail(
    execution_server, code, status
):
    server, _, state = execution_server
    state["request"] = SiteAgentError(code, "stable public detail")

    actual, _, payload = _request(
        server,
        "GET",
        "/api/v1/collection-executions/requests/request-001",
    )

    assert actual == status
    assert payload["error"] == {"code": code, "detail": "stable public detail"}


def test_unknown_reader_exception_does_not_leak(execution_server):
    server, _, state = execution_server
    state["snapshot"] = KeyError("private execution root")

    status, _, payload = _request(
        server, "GET", "/api/v1/collection-executions"
    )

    assert status == 503
    assert payload["error"]["code"] == "collection_execution_unavailable"
    assert "private execution root" not in payload["error"]["detail"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/collection-executions",
        "/api/v1/collection-executions/requests/request-001",
    ],
)
def test_collection_execution_namespace_has_no_mutation_surface(
    execution_server, method, path
):
    server, calls, _ = execution_server
    status, _, payload = _request(
        server,
        method,
        path,
        body="{not-json",
        headers={"Content-Type": "application/json"},
    )

    assert status == 405
    assert payload["error"]["code"] == "method_not_allowed"
    assert calls == []


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_namespaces_keep_existing_unsupported_method_behavior(
    execution_server, method
):
    server, _, _ = execution_server
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    try:
        connection.request(method, "/api/v0/health", body="{}")
        response = connection.getresponse()
        response.read()
        assert response.status == 501
    finally:
        connection.close()


def test_get_has_no_runtime_or_clock_capability_and_mutates_no_saved_state(
    tmp_path, launch
):
    service = launch(tmp_path / "site-agent")
    execution_root = tmp_path / "execution"
    execution_root.mkdir()
    cursor_path = execution_root / "cursor.json"
    cursor_path.write_bytes(b'{"tick_sequence":17}\n')
    cursor = {"tick_sequence": 17, "advance_calls": 0}
    snapshot = _example()["snapshot"]["body"]["data"]

    def reader():
        assert cursor == {"tick_sequence": 17, "advance_calls": 0}
        assert cursor_path.read_bytes() == b'{"tick_sequence":17}\n'
        return deepcopy(snapshot)

    server = SiteAgentApiServer(service, collection_executions=reader)
    server.start_background()
    before = cursor_path.read_bytes()
    try:
        assert _request(server, "GET", "/api/v1/collection-executions")[0] == 200
        assert cursor == {"tick_sequence": 17, "advance_calls": 0}
        assert cursor_path.read_bytes() == before
    finally:
        server.shutdown()
        service.stop()
