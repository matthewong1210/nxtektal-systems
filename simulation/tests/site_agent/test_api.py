"""Manager API transport tests for the Pilot Site Agent service."""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

from nxt_site_agent import (
    API_SCHEMA_VERSION,
    DISCLAIMER,
    SiteAgentApiServer,
    SiteAgentError,
)

EVIDENCE_STREAMS = ("ledger.jsonl", "evaluations.jsonl", "snapshots.jsonl")


@pytest.fixture()
def served(tmp_path, launch):
    service = launch(tmp_path)
    server = SiteAgentApiServer(service, port=0)
    server.start_background()
    connection = http.client.HTTPConnection(
        server.host, server.port, timeout=10
    )
    yield service, server, connection
    connection.close()
    server.shutdown()
    service.stop()


def get(connection, path):
    connection.request("GET", path)
    response = connection.getresponse()
    return response.status, json.loads(response.read())


def post(connection, path, payload=None):
    body = json.dumps(payload) if payload is not None else "{}"
    connection.request(
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    return response.status, json.loads(response.read())


def evidence_bytes(service):
    return {
        name: (
            service.storage.workflow_evidence_root / name
        ).read_bytes()
        for name in EVIDENCE_STREAMS
        if (service.storage.workflow_evidence_root / name).is_file()
    }


def test_every_endpoint_carries_schema_and_disclaimer(served):
    service, _, connection = served
    post(connection, "/api/v0/demo/advance")
    for path in (
        "/api/v0/health",
        "/api/v0/state",
        "/api/v0/evaluations",
        "/api/v0/recommendations",
        "/api/v0/briefing",
        "/api/v0/demo",
    ):
        status, payload = get(connection, path)
        assert status == 200, path
        assert payload["schema"] == API_SCHEMA_VERSION
        assert payload["disclaimer"] == DISCLAIMER
        assert "data" in payload


def test_health_projection_shape(served):
    _, _, connection = served
    status, payload = get(connection, "/api/v0/health")
    assert status == 200
    data = payload["data"]
    assert data["service_state"] == "serving"
    assert data["fixture_mode"] is True
    assert data["source_type"] == "fixture"
    assert data["mode_label"] == "fixture-backed Shadow Mode"
    assert data["workflow_id"] == "range.closed_loop_collection_handoff"
    assert data["workflow_readiness"] == "READY_FOR_FIXTURE_SHADOW_MODE"
    assert data["runtime"]["runtime_state"] in ("created", "running", "stopped")
    assert data["source"]["cursor"] == {
        "consumed_cycles": 0,
        "next_sequence_number": 0,
    }


def test_state_endpoint_is_explicit_before_any_publication(served):
    _, _, connection = served
    status, payload = get(connection, "/api/v0/state")
    assert status == 200
    assert payload["data"]["available"] is False
    assert payload["data"]["dispenser"] is None


def test_reads_do_not_mutate_canonical_evidence(served):
    service, _, connection = served
    post(connection, "/api/v0/demo/advance")
    before = evidence_bytes(service)
    for path in (
        "/api/v0/health",
        "/api/v0/state",
        "/api/v0/evaluations",
        "/api/v0/recommendations",
        "/api/v0/briefing",
        "/api/v0/demo",
    ):
        get(connection, path)
    assert evidence_bytes(service) == before


def test_advance_then_state_projects_the_published_envelope(served):
    _, _, connection = served
    status, payload = post(connection, "/api/v0/demo/advance")
    assert status == 200
    assert payload["data"]["outcome"] == "evaluated"
    status, payload = get(connection, "/api/v0/state")
    data = payload["data"]
    assert data["available"] is True
    assert data["dispenser"]["clean_available_balls"] == 6000
    assert data["dispenser"]["count_source"]["status"] == "ok"
    assert data["dispenser"]["reading_age_s"] == 5.0
    assert data["quality"]["runtime_quality"]["effective_confidence"] == 1.0


def test_accept_reject_modify_and_error_paths(served):
    service, _, connection = served
    post(connection, "/api/v0/demo/advance")
    post(connection, "/api/v0/demo/advance")
    status, payload = get(connection, "/api/v0/recommendations")
    pending = [
        item
        for item in payload["data"]
        if item["case_status"] == "pending"
    ]
    assert len(pending) == 1
    recommendation_id = pending[0]["recommendation_id"]
    assert pending[0]["action"] == "operator_intervention"
    assert pending[0]["trace"]["missing_data_reasons"]

    # invalid payloads
    status, payload = post(
        connection,
        f"/api/v0/recommendations/{recommendation_id}/accept",
        {"reason_code": "x"},
    )
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    status, payload = post(
        connection,
        f"/api/v0/recommendations/{recommendation_id}/accept",
        {"operator_id": "mgr", "reason_code": "x", "surprise": True},
    )
    assert status == 400

    # unknown id
    status, payload = post(
        connection,
        "/api/v0/recommendations/rec_does_not_exist/accept",
        {"operator_id": "mgr", "reason_code": "x"},
    )
    assert status == 404
    assert payload["error"]["code"] == "unknown_recommendation"

    # accept
    ledger_path = service.storage.workflow_evidence_root / "ledger.jsonl"
    records_before = len(ledger_path.read_text(encoding="utf-8").splitlines())
    status, payload = post(
        connection,
        f"/api/v0/recommendations/{recommendation_id}/accept",
        {
            "operator_id": "mgr-demo-01",
            "reason_code": "staffing_available",
            "note": "Will refill manually.",
        },
    )
    assert status == 200
    assert payload["data"]["case_status"] == "accepted"
    records_after = len(ledger_path.read_text(encoding="utf-8").splitlines())
    assert records_after == records_before + 1

    # duplicate response is rejected by existing workflow semantics
    status, payload = post(
        connection,
        f"/api/v0/recommendations/{recommendation_id}/reject",
        {"operator_id": "mgr-demo-01", "reason_code": "changed_mind"},
    )
    assert status == 409
    assert payload["error"]["code"] == "workflow_transition_rejected"


def test_modify_requires_replacement_fields(served):
    _, _, connection = served
    post(connection, "/api/v0/demo/advance")
    post(connection, "/api/v0/demo/advance")
    _, payload = get(connection, "/api/v0/recommendations")
    recommendation_id = payload["data"][0]["recommendation_id"]
    status, payload = post(
        connection,
        f"/api/v0/recommendations/{recommendation_id}/modify",
        {"operator_id": "mgr", "reason_code": "adjust"},
    )
    assert status == 400
    status, payload = post(
        connection,
        f"/api/v0/recommendations/{recommendation_id}/modify",
        {
            "operator_id": "mgr",
            "reason_code": "adjust",
            "replacement_action": "operator_intervention",
            "replacement_execute_before": "2026-08-08T19:15:00+00:00",
        },
    )
    assert status == 200
    assert payload["data"]["case_status"] == "modified"


def test_malformed_json_and_oversized_bodies_are_rejected(served):
    _, _, connection = served
    connection.request(
        "POST",
        "/api/v0/demo/advance",
        body="{not json",
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    payload = json.loads(response.read())
    assert response.status == 400
    assert payload["error"]["code"] == "invalid_request"

    connection.request(
        "POST",
        "/api/v0/demo/advance",
        body="x" * 70000,
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    payload = json.loads(response.read())
    assert response.status == 413
    assert payload["error"]["code"] == "body_too_large"


def test_unknown_paths_and_methods(served):
    _, _, connection = served
    status, payload = get(connection, "/api/v0/nothing")
    assert status == 404
    assert payload["error"]["code"] == "not_found"
    status, payload = post(connection, "/api/v0/recommendations/x/approve")
    assert status == 404
    connection.request("PUT", "/api/v0/health", body="{}")
    response = connection.getresponse()
    response.read()
    assert response.status == 501


def test_advance_after_exhaustion_is_refused(served):
    _, _, connection = served
    for _ in range(7):
        status, _ = post(connection, "/api/v0/demo/advance")
        assert status == 200
    status, payload = post(connection, "/api/v0/demo/advance")
    assert status == 409
    assert payload["error"]["code"] == "advance_refused"


def test_fixture_metadata_marks_controls_and_catalog(served):
    _, _, connection = served
    status, payload = get(connection, "/api/v0/demo")
    assert status == 200
    data = payload["data"]
    assert data["fixture_mode"] is True
    assert len(data["cycle_catalog"]) == 6
    assert data["next_cycle"]["cycle_index"] == 0
    assert all(
        item["source"] == "SIMULATED" for item in data["cycle_catalog"]
    )
    assert data["controls"]["advance"] is True


def test_demo_restart_and_reset_endpoints(served):
    service, _, connection = served
    post(connection, "/api/v0/demo/advance")
    status, payload = post(connection, "/api/v0/demo/restart")
    assert status == 200
    assert payload["data"]["service_state"] == "serving"
    assert payload["data"]["source"]["cursor"]["consumed_cycles"] == 1
    status, payload = post(connection, "/api/v0/demo/reset")
    assert status == 200
    assert payload["data"]["run_directory"] == "run-002"
    assert payload["data"]["source"]["cursor"] == {
        "consumed_cycles": 0,
        "next_sequence_number": 0,
    }
    assert service.storage.run_root.name == "run-002"


def test_static_console_serving_and_traversal_defense(tmp_path, launch):
    service = launch(tmp_path / "runs")
    console = tmp_path / "console"
    (console / "assets").mkdir(parents=True)
    (console / "index.html").write_text(
        "<!doctype html><title>console</title>", encoding="utf-8"
    )
    (console / "assets" / "app.js").write_text("// app", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    server = SiteAgentApiServer(service, port=0, console_dir=console)
    server.start_background()
    connection = http.client.HTTPConnection(
        server.host, server.port, timeout=10
    )
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "console" in body
        assert response.getheader("Content-Type").startswith("text/html")

        connection.request("GET", "/assets/app.js")
        response = connection.getresponse()
        response.read()
        assert response.status == 200
        assert response.getheader("Content-Type").startswith(
            "text/javascript"
        )

        connection.request("GET", "/../secret.txt")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 404
        assert payload["error"]["code"] == "not_found"
    finally:
        connection.close()
        server.shutdown()
        service.stop()


def test_api_without_console_reports_api_only(served):
    _, _, connection = served
    connection.request("GET", "/")
    response = connection.getresponse()
    payload = json.loads(response.read())
    assert response.status == 404
    assert "Manager API" in payload["error"]["detail"]


def test_cross_origin_and_foreign_host_requests_are_refused(served):
    """A page the operator merely visits must not drive the service."""
    service, server, _ = served
    attack = http.client.HTTPConnection(server.host, server.port, timeout=10)
    try:
        ledger_path = service.storage.workflow_evidence_root / "ledger.jsonl"
        before = (
            ledger_path.read_bytes() if ledger_path.is_file() else b""
        )
        attack.request(
            "POST",
            "/api/v0/demo/advance",
            body="{}",
            headers={
                "Content-Type": "application/json",
                "Origin": "https://evil.example",
            },
        )
        response = attack.getresponse()
        payload = json.loads(response.read())
        assert response.status == 403
        assert payload["error"]["code"] == "forbidden_origin"

        rebind = http.client.HTTPConnection(
            server.host, server.port, timeout=10
        )
        rebind.request(
            "GET",
            "/api/v0/state",
            headers={"Host": "evil.example"},
        )
        response = rebind.getresponse()
        payload = json.loads(response.read())
        rebind.close()
        assert response.status == 403
        assert payload["error"]["code"] == "forbidden_origin"

        # same-origin console requests still work
        ok = http.client.HTTPConnection(server.host, server.port, timeout=10)
        ok.request(
            "GET",
            "/api/v0/health",
            headers={"Origin": f"http://{server.host}:{server.port}"},
        )
        response = ok.getresponse()
        response.read()
        ok.close()
        assert response.status == 200

        after = ledger_path.read_bytes() if ledger_path.is_file() else b""
        assert after == before
    finally:
        attack.close()


def test_origin_matrix_for_mutating_endpoints(served):
    """Exact-origin rule: scheme + loopback host + bound port, or absent."""
    service, server, connection = served
    exact_origin = f"http://{server.host}:{server.port}"

    def post_with_origin(path, origin, payload=None):
        probe = http.client.HTTPConnection(
            server.host, server.port, timeout=10
        )
        try:
            headers = {"Content-Type": "application/json"}
            if origin is not None:
                headers["Origin"] = origin
            probe.request(
                "POST",
                path,
                body=json.dumps(payload) if payload is not None else "{}",
                headers=headers,
            )
            response = probe.getresponse()
            return response.status, json.loads(response.read())
        finally:
            probe.close()

    # valid exact same-origin POST succeeds on a fixture-control endpoint
    status, payload = post_with_origin("/api/v0/demo/advance", exact_origin)
    assert status == 200
    assert payload["data"]["outcome"] == "evaluated"

    # Origin: null (sandboxed iframe, file:/data: documents) is refused
    status, payload = post_with_origin("/api/v0/demo/advance", "null")
    assert status == 403
    assert payload["error"]["code"] == "forbidden_origin"

    # right authority, wrong scheme: https against this plain-HTTP service
    status, payload = post_with_origin(
        "/api/v0/demo/advance", f"https://{server.host}:{server.port}"
    )
    assert status == 403
    assert payload["error"]["code"] == "forbidden_origin"

    # malformed origins
    for malformed in ("garbage", "http://", f"{server.host}:{server.port}"):
        status, payload = post_with_origin("/api/v0/demo/advance", malformed)
        assert status == 403, malformed
        assert payload["error"]["code"] == "forbidden_origin"

    # foreign origin refused on a recommendation mutation endpoint too,
    # and the refusal happens before any workflow dispatch (403, not 404)
    status, payload = post_with_origin(
        "/api/v0/recommendations/rec_does_not_exist/accept",
        "https://evil.example",
        {"operator_id": "mgr", "reason_code": "x"},
    )
    assert status == 403
    assert payload["error"]["code"] == "forbidden_origin"

    # absent Origin (non-browser local tooling) is accepted; the second
    # cycle advances normally
    status, payload = post_with_origin("/api/v0/demo/advance", None)
    assert status == 200
    assert payload["data"]["outcome"] == "evaluated"


def test_oversized_body_closes_the_connection_against_desync(served):
    _, server, _ = served
    connection = http.client.HTTPConnection(
        server.host, server.port, timeout=10
    )
    try:
        connection.request(
            "POST",
            "/api/v0/demo/advance",
            body="x" * 70000,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 413
        # the server must have closed the connection so the unread body
        # bytes can never be parsed as a smuggled second request
        assert response.getheader("Connection", "").lower() == "close"
    finally:
        connection.close()


def test_chunked_bodies_are_refused(served):
    _, server, _ = served
    connection = http.client.HTTPConnection(
        server.host, server.port, timeout=10
    )
    try:
        connection.putrequest("POST", "/api/v0/demo/advance")
        connection.putheader("Transfer-Encoding", "chunked")
        connection.endheaders()
        connection.send(b"0\r\n\r\n")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 400
        assert payload["error"]["code"] == "invalid_request"
    finally:
        connection.close()


def test_nonlocal_bind_is_refused(tmp_path, launch):
    service = launch(tmp_path)
    with pytest.raises(SiteAgentError) as excinfo:
        SiteAgentApiServer(service, host="0.0.0.0", port=0)
    assert excinfo.value.code == "nonlocal_bind_refused"
    with pytest.raises(SiteAgentError) as excinfo:
        SiteAgentApiServer(service, host="192.168.1.10", port=0)
    assert excinfo.value.code == "nonlocal_bind_refused"
    service.stop()


def test_missing_console_dir_is_refused(tmp_path, launch):
    service = launch(tmp_path)
    with pytest.raises(SiteAgentError) as excinfo:
        SiteAgentApiServer(
            service, port=0, console_dir=Path(tmp_path / "absent")
        )
    assert excinfo.value.code == "console_dir_missing"
    service.stop()


def test_browser_refresh_reconstructs_from_the_api(served):
    """The API is the only state a refreshed console needs."""
    service, _, connection = served
    post(connection, "/api/v0/demo/advance")
    post(connection, "/api/v0/demo/advance")
    _, first = get(connection, "/api/v0/recommendations")
    recommendation_id = first["data"][0]["recommendation_id"]
    post(
        connection,
        f"/api/v0/recommendations/{recommendation_id}/accept",
        {"operator_id": "mgr", "reason_code": "ok"},
    )
    # a "refresh" is just a second full read: everything the console
    # shows must come back identical from persisted evidence
    snapshot_one = {
        path: get(connection, path)[1]["data"]
        for path in (
            "/api/v0/state",
            "/api/v0/evaluations",
            "/api/v0/recommendations",
        )
    }
    snapshot_two = {
        path: get(connection, path)[1]["data"]
        for path in (
            "/api/v0/state",
            "/api/v0/evaluations",
            "/api/v0/recommendations",
        )
    }
    assert snapshot_one == snapshot_two
    assert snapshot_one["/api/v0/recommendations"][0]["case_status"] == (
        "accepted"
    )
