"""Bounded MQTT service-loop regression coverage."""

from __future__ import annotations

import dataclasses
import json

import pytest

from nxt_agent_runtime import AgentRuntimeError
from scripts import edge_gateway_live_input_v0 as gateway
from scripts.mock_edge_load_cell_publisher import build_payload, publisher_topic


class _CallbackApiVersion:
    VERSION2 = object()


class _FakeClient:
    def __init__(self, message) -> None:
        self.messages = message if isinstance(message, tuple) else (message,)
        self.message = self.messages[0]
        self.disconnected = False
        self.manual_ack = False
        self.acknowledged = []
        self.subscribe_result = 0
        self.suback_failure = False
        self.reconnect_count = 0
        self.before_suback = lambda: None
        self.after_suback = lambda: None
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.on_subscribe = None

    def connect(self, host, port, keepalive):
        assert host == "broker.invalid"
        assert port == 1883
        assert keepalive == 30
        self.disconnected = False
        return 0

    def reconnect(self):
        self.reconnect_count += 1
        self.disconnected = False
        return 0

    def subscribe(self, subscriptions):
        assert subscriptions == [(self.message.topic, 1)]
        return self.subscribe_result, 1

    def manual_ack_set(self, enabled):
        assert enabled is True
        self.manual_ack = enabled

    def ack(self, mid, qos):
        assert self.manual_ack is True
        self.acknowledged.append((mid, qos))
        return 0

    def loop_forever(self, retry_first_connection=False):
        assert retry_first_connection is False
        flags = type(
            "Flags", (), {"session_present": self.reconnect_count > 0}
        )()
        self.on_connect(self, None, flags, 0, None)
        if self.disconnected:
            return 0
        if not flags.session_present:
            self.before_suback()
            self.on_subscribe(
                self,
                None,
                1,
                [
                    type(
                        "Reason",
                        (),
                        {"is_failure": self.suback_failure},
                    )()
                ],
                None,
            )
            self.after_suback()
            if self.disconnected:
                return 0
        for message in self.messages:
            self.on_message(self, None, message)
            if self.disconnected:
                break
        assert self.disconnected is True
        # Paho can surface MQTT_ERR_CONN_LOST after disconnecting inside the
        # callback. A completed explicit message limit is still successful.
        return 7

    def disconnect(self):
        self.disconnected = True
        return 0


class _FakeMqtt:
    CallbackAPIVersion = _CallbackApiVersion
    MQTTv311 = 4
    MQTT_ERR_SUCCESS = 0

    def __init__(self, message) -> None:
        self.client = _FakeClient(message)

    def Client(self, **kwargs):  # noqa: N802 - mirrors Paho's public class
        assert kwargs["callback_api_version"] is _CallbackApiVersion.VERSION2
        assert kwargs["client_id"] == "gw-pilot-a-01"
        assert kwargs["protocol"] == self.MQTTv311
        assert kwargs["clean_session"] is False
        return self.client


def test_bounded_gateway_exit_is_success_after_one_real_callback(monkeypatch, tmp_path):
    config = gateway.load_gateway_config(
        gateway.SIM_ROOT
        / "configs"
        / "edge_gateway"
        / "pilot-course-a.example.yaml",
        site=gateway.commissioned_site(),
    )
    config = dataclasses.replace(
        config,
        broker=dataclasses.replace(config.broker, host="broker.invalid"),
        status=dataclasses.replace(config.status, host="127.0.0.1", port=0),
        evidence_dir=tmp_path,
    )
    payload = build_payload(config)
    message = type(
        "Message",
        (),
        {
            "topic": publisher_topic(config),
            "payload": json.dumps(payload).encode("utf-8"),
            "mid": 77,
            "qos": 1,
            "retain": False,
        },
    )()
    fake_mqtt = _FakeMqtt(message)
    processors = []
    processor_class = gateway.GatewayProcessor

    class CapturingProcessor(processor_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            processors.append(self)

    monkeypatch.setattr(gateway, "GatewayProcessor", CapturingProcessor)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)

    def assert_before_suback():
        assert processors
        assert processors[0].status.snapshot()["broker_connected"] is False

    def assert_after_suback():
        assert processors
        assert processors[0].status.snapshot()["broker_connected"] is True

    fake_mqtt.client.before_suback = assert_before_suback
    fake_mqtt.client.after_suback = assert_after_suback

    assert gateway.run_gateway(config, max_messages=1) == 1
    assert fake_mqtt.client.manual_ack is True
    assert fake_mqtt.client.acknowledged == [(77, 1)]


def _bounded_config(tmp_path):
    config = gateway.load_gateway_config(
        gateway.SIM_ROOT
        / "configs"
        / "edge_gateway"
        / "pilot-course-a.example.yaml",
        site=gateway.commissioned_site(),
    )
    return dataclasses.replace(
        config,
        broker=dataclasses.replace(config.broker, host="broker.invalid"),
        status=dataclasses.replace(config.status, host="127.0.0.1", port=0),
        evidence_dir=tmp_path,
    )


def _message(config):
    return type(
        "Message",
        (),
        {
            "topic": publisher_topic(config),
            "payload": json.dumps(build_payload(config)).encode("utf-8"),
            "mid": 78,
            "qos": 1,
            "retain": False,
        },
    )()


def test_immediate_subscribe_failure_is_not_a_successful_gateway_exit(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    fake_mqtt = _FakeMqtt(_message(config))
    fake_mqtt.client.subscribe_result = 2
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config, max_messages=1)

    assert exc_info.value.code is gateway.GatewayErrorCode.MQTT_UNAVAILABLE
    assert fake_mqtt.client.acknowledged == []


def test_rejected_suback_is_not_a_successful_gateway_exit(monkeypatch, tmp_path):
    config = _bounded_config(tmp_path)
    fake_mqtt = _FakeMqtt(_message(config))
    fake_mqtt.client.suback_failure = True
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config, max_messages=1)

    assert exc_info.value.code is gateway.GatewayErrorCode.MQTT_UNAVAILABLE
    assert fake_mqtt.client.acknowledged == []


def test_deferred_hybrid_callback_keeps_qos1_unacknowledged_and_exits_nonzero(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    fake_mqtt = _FakeMqtt(_message(config))
    processor_class = gateway.GatewayProcessor

    class DeferredProcessor(processor_class):
        @property
        def has_pending_hybrid_delivery(self):
            return True

        def process_message(self, topic, payload):
            del topic, payload
            self.status.record_sensor_result(
                adapter_healthy=True,
                runtime_ready=False,
                operating_day_id="2026-08-28",
            )
            return gateway.ProcessingResult(
                kind=gateway.ProcessingKind.REJECTED,
                mode=self.config.mode,
                operating_day_id="2026-08-28",
                site_sequence=0,
                observations=(),
                adapter_report=None,
                complete_facility_state=True,
                disclaimer=gateway.HYBRID_DISCLAIMER,
            )

    monkeypatch.setattr(gateway, "GatewayProcessor", DeferredProcessor)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config, max_messages=1)

    assert exc_info.value.code is gateway.GatewayErrorCode.RUNTIME_RETRY_REQUIRED
    assert fake_mqtt.client.acknowledged == []


def test_persistent_session_redrives_deferred_frame_in_the_same_processor(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    fake_mqtt = _FakeMqtt(_message(config))
    processor_class = gateway.GatewayProcessor

    class DeferredThenReadyProcessor(processor_class):
        calls = 0

        @property
        def has_pending_hybrid_delivery(self):
            return self.calls == 1

        def process_message(self, topic, payload):
            del topic, payload
            self.calls += 1
            ready = self.calls == 2
            self.status.record_sensor_result(
                adapter_healthy=True,
                runtime_ready=ready,
                operating_day_id="2026-08-28",
            )
            return gateway.ProcessingResult(
                kind=(
                    gateway.ProcessingKind.ACCEPTED
                    if ready
                    else gateway.ProcessingKind.REJECTED
                ),
                mode=self.config.mode,
                operating_day_id="2026-08-28",
                site_sequence=0,
                observations=(),
                adapter_report=None,
                complete_facility_state=True,
                disclaimer=gateway.HYBRID_DISCLAIMER,
            )

    monkeypatch.setattr(gateway, "GatewayProcessor", DeferredThenReadyProcessor)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    assert gateway.run_gateway(config, max_messages=2) == 2
    assert fake_mqtt.client.reconnect_count == 1
    assert fake_mqtt.client.acknowledged == [(78, 1)]


def test_agent_runtime_incident_code_survives_the_real_callback_boundary(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    fake_mqtt = _FakeMqtt(_message(config))
    processor_class = gateway.GatewayProcessor

    class FailingProcessor(processor_class):
        def process_message(self, topic, payload):
            del topic, payload
            raise AgentRuntimeError(
                "evidence_verification_failed", "journal hash mismatch"
            )

    monkeypatch.setattr(gateway, "GatewayProcessor", FailingProcessor)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)

    with pytest.raises(AgentRuntimeError) as exc_info:
        gateway.run_gateway(config, max_messages=1)

    assert exc_info.value.incident_code == "evidence_verification_failed"
    assert fake_mqtt.client.acknowledged == []


def test_unexpected_processing_failure_stays_typed_and_unacknowledged(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    fake_mqtt = _FakeMqtt(_message(config))
    processor_class = gateway.GatewayProcessor

    class UnexpectedProcessor(processor_class):
        def process_message(self, topic, payload):
            del topic, payload
            raise RuntimeError("unexpected test failure")

    monkeypatch.setattr(gateway, "GatewayProcessor", UnexpectedProcessor)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config, max_messages=1)

    assert (
        exc_info.value.code
        is gateway.GatewayErrorCode.UNEXPECTED_PROCESSING_FAILURE
    )
    assert fake_mqtt.client.acknowledged == []


def test_bounded_smoke_does_not_hide_a_terminal_malformed_message(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    valid = _message(config)
    malformed = type(
        "Message",
        (),
        {
            "topic": valid.topic,
            "payload": b"{",
            "mid": 79,
            "qos": 1,
            "retain": False,
        },
    )()
    fake_mqtt = _FakeMqtt((valid, malformed))
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config, max_messages=2)

    assert exc_info.value.code is gateway.GatewayErrorCode.SOURCE_PROTOCOL
    assert fake_mqtt.client.acknowledged == [(78, 1), (79, 1)]


@pytest.mark.parametrize(
    ("qos", "retain", "expected_acknowledged"),
    [(0, False, []), (1, True, [(80, 1)])],
)
def test_callback_rejects_qos0_and_retained_deliveries(
    monkeypatch, tmp_path, qos, retain, expected_acknowledged
):
    config = _bounded_config(tmp_path)
    valid = _message(config)
    invalid = type(
        "Message",
        (),
        {
            "topic": valid.topic,
            "payload": valid.payload,
            "mid": 80,
            "qos": qos,
            "retain": retain,
        },
    )()
    fake_mqtt = _FakeMqtt(invalid)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config, max_messages=1)

    assert exc_info.value.code is gateway.GatewayErrorCode.SOURCE_PROTOCOL
    assert fake_mqtt.client.acknowledged == expected_acknowledged
