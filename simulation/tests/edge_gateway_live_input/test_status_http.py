"""Read-only gateway status and HTTP endpoint acceptance tests."""

from __future__ import annotations

import json
from http.client import HTTPMessage
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from scripts.edge_gateway_live_input_v0 import GatewayStatus, GatewayStatusServer
from scripts.pilot_course_a_edge_fixture import DEPLOYMENT_ID, SITE_ID


def _status(mode: str = "LOAD_CELL_DIAGNOSTIC") -> GatewayStatus:
    return GatewayStatus(
        mode=mode,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
    )


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
) -> tuple[int, HTTPMessage, bytes]:
    data = b"{}" if method in {"POST", "PUT", "PATCH"} else None
    request = Request(base_url + path, data=data, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()
    except HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def _json(body: bytes) -> dict:
    value = json.loads(body.decode("utf-8"))
    assert isinstance(value, dict)
    return value


def _base_url(server: GatewayStatusServer) -> str:
    host, port = server.address
    return f"http://{host}:{port}"


def test_status_starts_not_ready_with_each_health_dimension_visible():
    status = _status()
    snapshot = status.snapshot()
    assert snapshot["mode"] == "LOAD_CELL_DIAGNOSTIC"
    assert snapshot["site_id"] == SITE_ID
    assert snapshot["deployment_id"] == DEPLOYMENT_ID
    assert snapshot["broker_connected"] is False
    assert snapshot["sensor_seen"] is False
    assert snapshot["adapter_healthy"] is False
    assert snapshot["runtime_ready"] is False
    assert snapshot["ready"] is False
    assert snapshot["last_failure"] is None
    assert "current" in snapshot
    assert "disclaimer" in snapshot


def test_diagnostic_readiness_requires_broker_and_a_valid_adapter_result():
    status = _status()
    status.set_broker_connected(True)
    assert status.snapshot()["ready"] is False

    status.record_sensor_result(
        adapter_healthy=True,
        runtime_ready=None,
        operating_day_id="2026-08-28",
        detail={"canonical_observation_count": 2},
    )
    snapshot = status.snapshot()
    assert snapshot["broker_connected"] is True
    assert snapshot["sensor_seen"] is True
    assert snapshot["adapter_healthy"] is True
    assert snapshot["runtime_ready"] is False
    assert snapshot["ready"] is True
    assert snapshot["current"]["operating_day_id"] == "2026-08-28"
    assert snapshot["current"]["canonical_observation_count"] == 2


def test_hybrid_readiness_also_requires_an_admitted_runtime_frame():
    status = _status("HYBRID_RUNTIME_REHEARSAL")
    status.set_broker_connected(True)
    status.record_sensor_result(adapter_healthy=True, runtime_ready=False)
    assert status.snapshot()["ready"] is False

    status.record_sensor_result(adapter_healthy=True, runtime_ready=True)
    snapshot = status.snapshot()
    assert snapshot["ready"] is True
    assert snapshot["runtime_ready"] is True
    disclaimer = snapshot["disclaimer"].upper()
    assert "HYBRID" in disclaimer
    assert "SIMULATION" in disclaimer


def test_failure_is_diagnostic_and_cannot_create_readiness():
    status = _status()
    status.set_broker_connected(True)
    status.record_failure("malformed_wire_message", "payload was not JSON")
    snapshot = status.snapshot()
    assert snapshot["ready"] is False
    assert snapshot["sensor_seen"] is False
    assert snapshot["last_failure"] == {
        "code": "malformed_wire_message",
        "detail": "payload was not JSON",
    }


def test_transport_failure_preserves_independent_adapter_and_runtime_evidence():
    status = _status("HYBRID_RUNTIME_REHEARSAL")
    status.set_broker_connected(True)
    status.record_sensor_result(adapter_healthy=True, runtime_ready=True)
    assert status.snapshot()["ready"] is True

    status.record_transport_failure("mqtt_disconnected", "broker unavailable")

    snapshot = status.snapshot()
    assert snapshot["broker_connected"] is False
    assert snapshot["sensor_seen"] is True
    assert snapshot["adapter_healthy"] is True
    assert snapshot["runtime_ready"] is True
    assert snapshot["ready"] is False
    assert snapshot["last_failure"] == {
        "code": "mqtt_disconnected",
        "detail": "broker unavailable",
    }


def test_failure_detail_is_bounded_before_status_or_log_serialization():
    status = _status()
    status.record_failure("malformed_wire_message", "x" * 10_000)

    detail = status.snapshot()["last_failure"]["detail"]
    assert len(detail) <= 1_024
    assert detail.endswith("...[detail truncated]")


def test_get_and_head_are_side_effect_free_and_expose_all_dimensions():
    status = _status("HYBRID_RUNTIME_REHEARSAL")
    before = status.snapshot()
    with GatewayStatusServer(status) as server:
        base_url = _base_url(server)
        expected_codes = {
            "/healthz": 200,
            "/readyz": 503,
            "/api/v0/status": 200,
        }
        for path, expected_code in expected_codes.items():
            code, headers, body = _request(base_url, path)
            assert code == expected_code
            assert headers.get_content_type() == "application/json"
            payload = _json(body)
            if path == "/healthz":
                assert payload["schema"] == "nxt-edge-gateway/health/v0"
            for dimension in (
                "broker_connected",
                "sensor_seen",
                "adapter_healthy",
                "runtime_ready",
                "ready",
            ):
                assert payload[dimension] is False

            head_code, head_headers, head_body = _request(
                base_url, path, method="HEAD"
            )
            assert head_code == expected_code
            assert head_headers.get_content_type() == "application/json"
            assert head_body == b""

    assert status.snapshot() == before


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "path", ["/healthz", "/readyz", "/api/v0/status"]
)
def test_mutation_methods_are_405_and_leave_status_unchanged(method, path):
    status = _status()
    status.set_broker_connected(True)
    status.record_sensor_result(adapter_healthy=True, runtime_ready=None)
    before = status.snapshot()

    with GatewayStatusServer(status, host="127.0.0.1", port=0) as server:
        code, headers, _ = _request(_base_url(server), path, method=method)
        assert code == 405
        allowed = {item.strip() for item in headers["Allow"].split(",")}
        assert allowed == {"GET", "HEAD"}

    assert status.snapshot() == before


def test_ready_endpoint_turns_200_only_after_valid_mode_specific_evidence():
    status = _status("HYBRID_RUNTIME_REHEARSAL")
    with GatewayStatusServer(status) as server:
        base_url = _base_url(server)
        assert _request(base_url, "/readyz")[0] == 503

        status.set_broker_connected(True)
        status.record_sensor_result(adapter_healthy=True, runtime_ready=False)
        assert _request(base_url, "/readyz")[0] == 503

        status.record_sensor_result(adapter_healthy=True, runtime_ready=True)
        code, _, body = _request(base_url, "/readyz")
        assert code == 200
        assert _json(body)["ready"] is True


def test_unknown_endpoint_is_404_without_status_mutation():
    status = _status()
    before = status.snapshot()
    with GatewayStatusServer(status) as server:
        assert _request(_base_url(server), "/commands")[0] == 404
    assert status.snapshot() == before
