"""Deterministic mock MQTT publisher contract tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from scripts import mock_edge_load_cell_publisher as publisher
from scripts.edge_gateway_live_input_v0 import WIRE_SCHEMA, load_gateway_config
from scripts.mock_edge_load_cell_publisher import (
    build_payload,
    publish_payload,
    publisher_topic,
)
from scripts.pilot_course_a_edge_fixture import CALIBRATION_ID_LOAD_CELL


SIMULATION_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = (
    SIMULATION_ROOT
    / "configs"
    / "edge_gateway"
    / "pilot-course-a.example.yaml"
)
SAMPLED = "2026-08-28T03:15:04.120Z"
PUBLISHED = "2026-08-28T03:15:04.220Z"


def _config():
    return load_gateway_config(EXAMPLE)


def test_topic_helper_is_exact_and_derived_from_configured_identity():
    config = _config()
    device_id = config.devices[0].device_id
    expected = f"nxt/v1/sites/{config.site_id}/devices/{device_id}/load-cell"
    assert publisher_topic(config) == expected
    assert publisher_topic(config, device_id=device_id) == expected


def test_topic_helper_refuses_an_unconfigured_device():
    with pytest.raises(ValueError):
        publisher_topic(_config(), device_id="unconfigured-device")


def test_payload_helper_emits_the_exact_versioned_wire_contract():
    config = _config()
    sensor_id = config.devices[0].sensor_ids[0]
    payload = build_payload(
        config,
        raw_value=57.314,
        sampled_at_utc=SAMPLED,
        published_at_utc=PUBLISHED,
        device_sequence=1842,
        boot_id="boot-20260828-001",
    )

    assert payload == {
        "schema": WIRE_SCHEMA,
        "site_id": config.site_id,
        "deployment_id": config.deployment_id,
        "gateway_id": config.gateway_id,
        "device_id": config.devices[0].device_id,
        "sensor_id": sensor_id,
        "boot_id": "boot-20260828-001",
        "device_sequence": 1842,
        "sampled_at_utc": SAMPLED,
        "published_at_utc": PUBLISHED,
        "raw_value": 57.314,
        "raw_unit": "kg",
        "device_status": "ok",
        "calibration_id": CALIBRATION_ID_LOAD_CELL,
        "diagnostic_code": None,
    }


def test_payload_helper_is_deterministic_across_fresh_config_loads():
    kwargs = {
        "raw_value": 57.314,
        "sampled_at_utc": SAMPLED,
        "published_at_utc": PUBLISHED,
        "device_sequence": 7,
        "boot_id": "boot-fixed",
    }
    assert build_payload(_config(), **kwargs) == build_payload(_config(), **kwargs)


def test_payload_helper_defaults_to_the_first_configured_sensor():
    config = _config()
    payload = build_payload(
        config,
        sampled_at_utc=SAMPLED,
        published_at_utc=PUBLISHED,
    )
    assert payload["sensor_id"] == config.devices[0].sensor_ids[0]
    assert payload["device_sequence"] == 0
    assert payload["boot_id"] == "boot-mock-001"


def test_payload_helper_refuses_an_unconfigured_sensor():
    with pytest.raises(ValueError):
        build_payload(
            _config(),
            sensor_id="sensor-not-configured",
            sampled_at_utc=SAMPLED,
            published_at_utc=PUBLISHED,
        )


def test_explicit_overrides_support_negative_smoke_inputs_without_mutating_them():
    overrides = {"raw_unit": "lb", "device_status": "fault"}
    payload = build_payload(
        _config(),
        sampled_at_utc=SAMPLED,
        published_at_utc=PUBLISHED,
        overrides=overrides,
    )
    assert payload["raw_unit"] == "lb"
    assert payload["device_status"] == "fault"
    assert overrides == {"raw_unit": "lb", "device_status": "fault"}


class _CallbackApiVersion:
    VERSION2 = object()


class _PublishInfo:
    rc = 0

    def __init__(self):
        self.published = False

    def is_published(self):
        return self.published


class _LoopDrivenClient:
    def __init__(self, *, connect_on_loop: bool) -> None:
        self.connect_on_loop = connect_on_loop
        self.connect_timeout = None
        self.disconnected = False
        self.info = None
        self.on_connect = None

    def connect(self, host, port, keepalive):
        assert host
        assert port == 1883
        assert keepalive == 30
        return 0

    def loop(self, timeout):
        if self.connect_on_loop and self.on_connect is not None:
            callback = self.on_connect
            self.on_connect = None
            reason = type("Reason", (), {"value": 0, "is_failure": False})()
            callback(self, None, None, reason, None)
        elif self.info is not None:
            self.info.published = True
        else:
            time.sleep(timeout)
        return 0

    def publish(self, topic, payload, qos, retain):
        assert topic == publisher_topic(_config())
        assert isinstance(payload, str)
        assert qos == 1
        assert retain is False
        self.info = _PublishInfo()
        return self.info

    def disconnect(self):
        self.disconnected = True
        return 0


class _FakeMqtt:
    CallbackAPIVersion = _CallbackApiVersion
    MQTT_ERR_SUCCESS = 0

    def __init__(self, client):
        self.client = client

    def Client(self, callback_version, client_id):  # noqa: N802
        assert callback_version is _CallbackApiVersion.VERSION2
        assert client_id.endswith("-mock-publisher")
        return self.client


def test_publish_uses_one_monotonic_deadline_and_loop_driven_cleanup(monkeypatch):
    config = _config()
    client = _LoopDrivenClient(connect_on_loop=True)
    monkeypatch.setattr(publisher, "_load_paho", lambda: _FakeMqtt(client))

    topic = publish_payload(config, build_payload(config), timeout_s=0.2)

    assert topic == publisher_topic(config)
    assert 0 < client.connect_timeout <= 0.2
    assert client.info is not None and client.info.is_published()
    assert client.disconnected is True


def test_publish_timeout_bounds_silent_connection_and_still_disconnects(
    monkeypatch,
):
    config = _config()
    client = _LoopDrivenClient(connect_on_loop=False)
    monkeypatch.setattr(publisher, "_load_paho", lambda: _FakeMqtt(client))
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="broker connection"):
        publish_payload(config, build_payload(config), timeout_s=0.03)

    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert 0 < client.connect_timeout <= 0.03
    assert client.disconnected is True
