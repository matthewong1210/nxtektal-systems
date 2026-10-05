"""Read-only whole-course transport: real loopback requests and injected facts."""
from __future__ import annotations

import http.client
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from nxt_site_agent import API_SCHEMA_VERSION, DISCLAIMER, SiteAgentApiServer, SiteAgentError

PNG = b"\x89PNG\r\n\x1a\n" + b"test-evidence"
IMAGE_QUERY = "?sha256=" + hashlib.sha256(PNG).hexdigest()


@pytest.fixture()
def course_server(tmp_path, launch):
    service = launch(tmp_path)
    calls = []
    state = {"error": None}

    def snapshot():
        calls.append("snapshot")
        if state["error"]:
            raise SiteAgentError("course_ops_unavailable", "saved evidence is unavailable")
        return {"schema": "nxt-course-ops/v1", "environment": "SIMULATION", "mode": "READ_ONLY"}

    def media(round_id, frame_id, expected_sha):
        calls.append((round_id, frame_id, expected_sha))
        if frame_id != "frame-000001":
            raise SiteAgentError("course_ops_not_found", "image not declared")
        return PNG

    server = SiteAgentApiServer(service, course_operations=snapshot, course_media=media)
    server.start_background()
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    yield connection, calls, state
    connection.close()
    server.shutdown()
    service.stop()


def request(connection, method, path, headers=None):
    connection.request(method, path, body="{}" if method == "POST" else None, headers=headers or {})
    response = connection.getresponse()
    return response.status, dict(response.getheaders()), response.read()


def test_course_snapshot_uses_manager_envelope(course_server):
    connection, calls, _ = course_server
    status, headers, raw = request(connection, "GET", "/api/v1/course-ops")
    payload = json.loads(raw)
    assert status == 200
    assert payload["schema"] == API_SCHEMA_VERSION
    assert payload["disclaimer"] == DISCLAIMER
    assert payload["data"]["schema"] == "nxt-course-ops/v1"
    assert headers["Cache-Control"] == "no-store"
    assert calls == ["snapshot"]


def test_course_media_delivers_only_injected_png(course_server):
    connection, calls, _ = course_server
    status, headers, raw = request(connection, "GET", "/api/v1/course-ops/media/round-0000000001/frame-000001.png" + IMAGE_QUERY)
    assert status == 200 and raw == PNG
    assert headers["Content-Type"] == "image/png"
    assert headers["Cache-Control"] == "no-store"
    assert calls == [("round-0000000001", "frame-000001", IMAGE_QUERY.removeprefix("?sha256="))]


@pytest.mark.parametrize("query", ["", "?sha256=invalid", IMAGE_QUERY + "&extra=1"])
def test_course_media_requires_one_snapshot_bound_hash(course_server, query):
    connection, calls, _ = course_server
    status, _, _ = request(connection, "GET", "/api/v1/course-ops/media/round-0000000001/frame-000001.png" + query)
    assert status == 404 and calls == []


@pytest.mark.parametrize("path", [
    "/api/v1/course-ops", "/api/v1/course-ops/resume",
    "/api/v1/course-ops/media/round-0000000001/frame-000001.png",
])
def test_course_namespace_has_no_mutation_surface(course_server, path):
    connection, calls, _ = course_server
    status, _, raw = request(connection, "POST", path)
    assert status == 405
    assert json.loads(raw)["error"]["code"] == "method_not_allowed"
    assert calls == []


@pytest.mark.parametrize("path", [
    "/api/v1/course-ops/media/../reference/frame-000001.json",
    "/api/v1/course-ops/media/round-0000000001/%2e%2e.png",
    "/api/v1/course-ops/media/round-0000000001/frame-000001.json",
    "/api/v1/course-ops/media/round-0000000001/extra/frame-000001.png",
    "/api/v1/course-ops/unknown",
])
def test_course_invalid_paths_never_call_media_reader(course_server, path):
    connection, calls, _ = course_server
    status, _, _ = request(connection, "GET", path)
    assert status == 404
    assert calls == []


def test_course_unavailable_is_not_success_or_internal_error(course_server):
    connection, _, state = course_server
    state["error"] = True
    status, _, raw = request(connection, "GET", "/api/v1/course-ops")
    assert status == 503
    assert json.loads(raw)["error"]["code"] == "course_ops_unavailable"


def test_course_media_missing_is_not_server_error(course_server):
    connection, _, _ = course_server
    status, _, _ = request(connection, "GET", "/api/v1/course-ops/media/round-0000000001/frame-000002.png" + IMAGE_QUERY)
    assert status == 404


def test_course_read_keeps_same_origin_restriction(course_server):
    connection, calls, _ = course_server
    status, _, _ = request(connection, "GET", "/api/v1/course-ops", {"Origin": "https://example.com"})
    assert status == 403 and calls == []


def test_course_is_optional_for_old_runners(tmp_path, launch):
    service = launch(tmp_path)
    server = SiteAgentApiServer(service)
    server.start_background()
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    try:
        status, _, _ = request(connection, "GET", "/api/v1/course-ops")
        assert status == 404
    finally:
        connection.close()
        server.shutdown()
        service.stop()


def test_pilot_runner_binds_only_read_callbacks_and_translates_errors(monkeypatch, tmp_path):
    from scripts import pilot_dispatch_demo

    calls = []

    class ReaderError(ValueError):
        code = "course_ops_unavailable"

    class Reader:
        def __init__(self, root):
            calls.append(("init", root))

        def snapshot(self):
            calls.append("snapshot")
            return {"mode": "READ_ONLY"}

        def media(self, round_id, frame_id, expected_sha):
            calls.append((round_id, frame_id, expected_sha))
            raise ReaderError("bad image hash")

    monkeypatch.setitem(sys.modules, "scripts.course_operations", SimpleNamespace(CourseOpsReader=Reader, CourseOpsError=ReaderError))
    snapshot, media = pilot_dispatch_demo.course_read_callbacks(tmp_path)
    assert calls == [("init", tmp_path)]
    assert snapshot() == {"mode": "READ_ONLY"}
    with pytest.raises(SiteAgentError) as error:
        media("round-0000000001", "frame-000001")
    assert error.value.code == "course_ops_unavailable"
    assert calls == [("init", tmp_path), "snapshot", ("round-0000000001", "frame-000001", None)]


def test_pilot_runner_without_course_source_creates_no_reader():
    from scripts import pilot_dispatch_demo

    assert pilot_dispatch_demo.course_read_callbacks(None) == (None, None)


@pytest.mark.parametrize("callback_index", [0, 1])
def test_pilot_reader_malformed_source_is_unavailable(monkeypatch, tmp_path, callback_index):
    from scripts import pilot_dispatch_demo

    class Reader:
        def __init__(self, _root):
            pass

        def snapshot(self):
            raise KeyError("private source field")

        def media(self, *_args):
            raise TypeError("private source field")

    monkeypatch.setitem(sys.modules, "scripts.course_operations", SimpleNamespace(CourseOpsReader=Reader, CourseOpsError=ValueError))
    callbacks = pilot_dispatch_demo.course_read_callbacks(tmp_path)
    args = () if callback_index == 0 else ("round-0000000001", "frame-000001", "a" * 64)
    with pytest.raises(SiteAgentError) as error:
        callbacks[callback_index](*args)
    assert error.value.code == "course_ops_unavailable"
    assert "private source field" not in error.value.detail
