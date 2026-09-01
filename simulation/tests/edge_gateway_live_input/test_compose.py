"""Static deployment-bundle guards for the local broker smoke stack."""

from __future__ import annotations

from pathlib import Path

import yaml


SIMULATION_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_ROOT = SIMULATION_ROOT / "deploy" / "edge-gateway-v0"
COMPOSE = DEPLOY_ROOT / "compose.yaml"
DOCKERFILE = DEPLOY_ROOT / "Dockerfile"
MOSQUITTO_CONFIG = DEPLOY_ROOT / "mosquitto.conf"


def _compose() -> dict:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _flatten(value) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(item) for item in value)
    return str(value)


def _dependencies(service: dict) -> dict[str, str | None]:
    depends_on = service.get("depends_on", {})
    if isinstance(depends_on, list):
        return {name: None for name in depends_on}
    assert isinstance(depends_on, dict)
    result: dict[str, str | None] = {}
    for name, settings in depends_on.items():
        if isinstance(settings, dict):
            result[name] = settings.get("condition")
        else:
            result[name] = None
    return result


def test_deployment_bundle_contains_only_the_three_required_roles():
    assert COMPOSE.is_file()
    assert DOCKERFILE.is_file()
    assert MOSQUITTO_CONFIG.is_file()
    services = _compose()["services"]
    assert set(services) == {"mosquitto", "gateway", "publisher"}


def test_compose_dependency_order_is_broker_then_gateway_then_publisher():
    services = _compose()["services"]
    assert _dependencies(services["gateway"]) == {
        "mosquitto": "service_healthy"
    }
    assert _dependencies(services["publisher"]) == {
        "mosquitto": "service_healthy",
        "gateway": "service_healthy",
    }


def test_healthchecks_do_not_deadlock_before_the_first_sensor_message():
    services = _compose()["services"]
    broker_health = _flatten(services["mosquitto"]["healthcheck"]).lower()
    gateway_health = _flatten(services["gateway"]["healthcheck"]).lower()
    assert "mosquitto" in broker_health
    assert "/healthz" in gateway_health
    # Publisher waits for gateway health before sending the first message;
    # readiness necessarily stays false until that message is accepted.
    assert "/readyz" not in gateway_health


def test_gateway_exposes_only_its_read_only_status_http_port():
    gateway = _compose()["services"]["gateway"]
    published = _flatten(gateway.get("ports", ()))
    assert "8080" in published
    health = _flatten(gateway["healthcheck"])
    assert "/healthz" in health


def test_services_invoke_the_gateway_and_deterministic_mock_publisher():
    services = _compose()["services"]
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    gateway_text = _flatten(services["gateway"]) + " " + dockerfile
    publisher_text = _flatten(services["publisher"]) + " " + dockerfile
    assert "edge_gateway_live_input_v0.py" in gateway_text
    assert "mock_edge_load_cell_publisher.py" in publisher_text
    assert "pilot-course-a.example.yaml" in gateway_text
    assert "pilot-course-a.example.yaml" in publisher_text


def test_container_installs_mqtt_only_through_the_named_optional_extra():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8").lower()
    assert "edge-gateway" in dockerfile
    assert "paho-mqtt" not in dockerfile
    assert "user edgegateway" in dockerfile


def test_compose_declares_no_privileged_hardware_or_host_control_surface():
    services = _compose()["services"]
    text = COMPOSE.read_text(encoding="utf-8").lower()
    for service in services.values():
        assert service.get("privileged") not in (True, "true")
        assert service.get("network_mode") != "host"
        assert not service.get("devices")
        assert not service.get("cap_add")
    assert "/var/run/docker.sock" not in text
    assert "rclpy" not in text
    assert "ros2" not in text
    assert "nav2" not in text
    assert "sqlite" not in text
    assert "cloud_sync" not in text
    assert "ota" not in text


def test_local_broker_configuration_is_explicit_and_command_free():
    text = MOSQUITTO_CONFIG.read_text(encoding="utf-8").lower()
    assert "listener 1883" in text
    assert "allow_anonymous true" in text
    assert "persistence false" in text
    assert "max_packet_size 65536" in text
    assert "acl_file" not in text
