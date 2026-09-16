"""Bounded MQTT service-loop regression coverage."""

from __future__ import annotations

import dataclasses
import json

import pytest

from nxt_agent_runtime import AgentRuntimeError, CycleKind, CycleOutcome
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
        self.ack_attempts = []
        self.ack_result = 0
        self.ack_results = None
        self.subscribe_result = 0
        self.subscribe_calls = 0
        self.suback_failure = False
        self.reconnect_count = 0
        self.loop_count = 0
        self.max_loop_count = None
        self.message_batches = None
        self.reconnect_messages = None
        self.session_present_on_reconnect = True
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
        self.subscribe_calls += 1
        return self.subscribe_result, 1

    def manual_ack_set(self, enabled):
        assert enabled is True
        self.manual_ack = enabled

    def ack(self, mid, qos):
        assert self.manual_ack is True
        self.ack_attempts.append((mid, qos))
        result = (
            self.ack_results.pop(0)
            if self.ack_results is not None
            else self.ack_result
        )
        if result == 0:
            self.acknowledged.append((mid, qos))
        return result

    def loop_forever(self, retry_first_connection=False):
        assert retry_first_connection is False
        self.loop_count += 1
        if (
            self.max_loop_count is not None
            and self.loop_count > self.max_loop_count
        ):
            raise AssertionError("gateway exceeded the expected reconnect bound")
        flags = type(
            "Flags",
            (),
            {
                "session_present": self.reconnect_count > 0
                and self.session_present_on_reconnect
            },
        )()
        self.on_connect(self, None, flags, 0, None)
        if self.disconnected:
            return 0
        if not flags.session_present:
            self.before_suback()
            if self.disconnected:
                return 0
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
        if self.message_batches is not None:
            messages = self.message_batches[self.loop_count - 1]
        elif self.reconnect_count > 0 and self.reconnect_messages is not None:
            messages = self.reconnect_messages
        else:
            messages = self.messages
        for message in messages:
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


def _message(config, *, mid=78, **payload_changes):
    payload = build_payload(config, **payload_changes)
    return type(
        "Message",
        (),
        {
            "topic": publisher_topic(config),
            "payload": json.dumps(payload).encode("utf-8"),
            "mid": mid,
            "qos": 1,
            "retain": False,
        },
    )()


def _message_with_raw_payload(template, *, mid, payload):
    return type(
        "Message",
        (),
        {
            "topic": template.topic,
            "payload": payload,
            "mid": mid,
            "qos": template.qos,
            "retain": template.retain,
        },
    )()


def _install_always_deferred_processor(monkeypatch):
    processors = []
    processor_class = gateway.GatewayProcessor

    class AlwaysDeferredProcessor(processor_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            source = self._hybrid_source
            assert source is not None

            class AlwaysDeferredRuntime:
                calls = 0

                def run_once(runtime_self):
                    runtime_self.calls += 1
                    sequence = source.observe().sequence_number
                    return CycleOutcome(
                        kind=CycleKind.EVALUATION_DEFERRED,
                        sequence_number=sequence,
                        envelope_id="fse:persistently-deferred",
                        evaluation_id=None,
                        record=None,
                        failure=None,
                        acknowledged=False,
                    )

            self.test_runtime = AlwaysDeferredRuntime()
            processors.append(self)

        def _make_runtime(self, operating_day_id):
            del operating_day_id
            return self.test_runtime

    monkeypatch.setattr(gateway, "GatewayProcessor", AlwaysDeferredProcessor)
    return processors


def _install_first_ready_then_always_deferred_processor(monkeypatch):
    processors = []
    processor_class = gateway.GatewayProcessor

    class FirstReadyThenAlwaysDeferredProcessor(processor_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            source = self._hybrid_source
            assert source is not None

            class FirstReadyThenAlwaysDeferredRuntime:
                calls = 0

                def run_once(runtime_self):
                    runtime_self.calls += 1
                    sequence = source.observe().sequence_number
                    if runtime_self.calls == 1:
                        source.acknowledge(sequence)
                        return CycleOutcome(
                            kind=CycleKind.EVALUATED,
                            sequence_number=sequence,
                            envelope_id="fse:first-ready",
                            evaluation_id="aev:first-ready",
                            record=None,
                            failure=None,
                            acknowledged=True,
                        )
                    return CycleOutcome(
                        kind=CycleKind.EVALUATION_DEFERRED,
                        sequence_number=sequence,
                        envelope_id="fse:later-pending",
                        evaluation_id=None,
                        record=None,
                        failure=None,
                        acknowledged=False,
                    )

            self.test_runtime = FirstReadyThenAlwaysDeferredRuntime()
            processors.append(self)

        def _make_runtime(self, operating_day_id):
            del operating_day_id
            return self.test_runtime

    monkeypatch.setattr(
        gateway,
        "GatewayProcessor",
        FirstReadyThenAlwaysDeferredProcessor,
    )
    return processors


def _install_second_delivery_deferred_once_processor(monkeypatch):
    processors = []
    processor_class = gateway.GatewayProcessor

    class SecondDeliveryDeferredOnceProcessor(processor_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            source = self._hybrid_source
            assert source is not None

            class SecondDeliveryDeferredOnceRuntime:
                calls = 0

                def run_once(runtime_self):
                    runtime_self.calls += 1
                    sequence = source.observe().sequence_number
                    if runtime_self.calls == 2:
                        return CycleOutcome(
                            kind=CycleKind.EVALUATION_DEFERRED,
                            sequence_number=sequence,
                            envelope_id="fse:second-delivery",
                            evaluation_id=None,
                            record=None,
                            failure=None,
                            acknowledged=False,
                        )
                    source.acknowledge(sequence)
                    return CycleOutcome(
                        kind=(
                            CycleKind.EVALUATED
                            if runtime_self.calls == 1
                            else CycleKind.REPLAY_SKIPPED
                        ),
                        sequence_number=sequence,
                        envelope_id=f"fse:sequence-{sequence}",
                        evaluation_id=f"aev:sequence-{sequence}",
                        record=None,
                        failure=None,
                        acknowledged=True,
                    )

            self.test_runtime = SecondDeliveryDeferredOnceRuntime()
            processors.append(self)

        def _make_runtime(self, operating_day_id):
            del operating_day_id
            return self.test_runtime

    monkeypatch.setattr(
        gateway,
        "GatewayProcessor",
        SecondDeliveryDeferredOnceProcessor,
    )
    return processors


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
        process_calls = 0

        @property
        def has_pending_hybrid_delivery(self):
            return self.process_calls > 0

        def process_message(self, topic, payload):
            del topic, payload
            self.process_calls += 1
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


def test_operating_day_rollover_stops_without_acknowledging_the_new_day(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    first = _message(config, mid=81)
    next_day = _message(
        config,
        mid=82,
        device_sequence=1,
        sampled_at_utc="2026-08-09T09:29:55.000Z",
        published_at_utc="2026-08-09T09:30:00.000Z",
    )
    fake_mqtt = _FakeMqtt((first, next_day))
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config, max_messages=2)

    assert exc_info.value.code is gateway.GatewayErrorCode.OPERATING_DAY_ROLLOVER
    assert fake_mqtt.client.acknowledged == [(81, 1)]


def test_overtaking_delivery_is_not_pubacked_while_a_frame_is_pending(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    deferred = _message(config, mid=83)
    overtaking = _message(config, mid=84, device_sequence=1)
    fake_mqtt = _FakeMqtt(deferred)
    fake_mqtt.client.reconnect_messages = (overtaking,)
    fake_mqtt.client.max_loop_count = 2
    processors = _install_always_deferred_processor(monkeypatch)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config)

    assert exc_info.value.code is gateway.GatewayErrorCode.SOURCE_PROTOCOL
    assert processors[0].test_runtime.calls == 1
    assert fake_mqtt.client.loop_count == 2
    assert fake_mqtt.client.reconnect_count == 1
    assert fake_mqtt.client.acknowledged == []


def test_older_acknowledged_duplicate_stays_terminal_while_newer_frame_is_pending(
    monkeypatch, tmp_path, capsys
):
    config = _bounded_config(tmp_path)
    first = _message(config, mid=95, device_sequence=0)
    later = _message(config, mid=96, device_sequence=1)
    older_duplicate = _message(config, mid=97, device_sequence=0)
    later_redelivery = _message(config, mid=98, device_sequence=1)
    fake_mqtt = _FakeMqtt((first, later))
    fake_mqtt.client.reconnect_messages = (older_duplicate, later_redelivery)
    processors = _install_second_delivery_deferred_once_processor(monkeypatch)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    assert gateway.run_gateway(config, max_messages=4) == 4

    assert fake_mqtt.client.acknowledged == [(95, 1), (97, 1), (98, 1)]
    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    assert not [event for event in events if event["event"] == "message_rejected"]
    duplicate_results = [
        event
        for event in events
        if event["event"] == "message_result"
        and event["kind"] == gateway.ProcessingKind.DUPLICATE.value
    ]
    assert len(duplicate_results) == 1
    processor = processors[0]
    assert processor.test_runtime.calls == 3
    assert processor.has_pending_hybrid_delivery is False
    status = processor.status.snapshot()
    assert status["adapter_healthy"] is True
    assert status["runtime_ready"] is True
    assert status["last_failure"] is None


def test_unseen_lower_sequence_is_pubacked_without_displacing_a_pending_frame(
    monkeypatch, tmp_path, capsys
):
    config = _bounded_config(tmp_path)
    first = _message(config, mid=101, device_sequence=10)
    pending = _message(config, mid=102, device_sequence=12)
    lower_unseen = _message(config, mid=103, device_sequence=11)
    pending_redelivery = _message(config, mid=104, device_sequence=12)
    fake_mqtt = _FakeMqtt((first, pending))
    fake_mqtt.client.reconnect_messages = (lower_unseen, pending_redelivery)
    processors = _install_second_delivery_deferred_once_processor(monkeypatch)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    assert gateway.run_gateway(config, max_messages=4) == 4

    assert fake_mqtt.client.acknowledged == [(101, 1), (103, 1), (104, 1)]
    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    rejections = [
        event for event in events if event["event"] == "message_rejected"
    ]
    assert [event["failure"]["code"] for event in rejections] == [
        gateway.GatewayErrorCode.OUT_OF_ORDER_SEQUENCE.value
    ]
    processor = processors[0]
    assert processor.test_runtime.calls == 3
    assert processor.has_pending_hybrid_delivery is False
    status = processor.status.snapshot()
    assert status["adapter_healthy"] is True
    assert status["runtime_ready"] is True
    assert status["last_failure"] is None


def test_retired_boot_is_pubacked_without_displacing_a_pending_frame(
    monkeypatch, tmp_path, capsys
):
    config = _bounded_config(tmp_path)
    retired = _message(
        config,
        mid=105,
        boot_id="boot-retired-a",
        device_sequence=0,
    )
    pending = _message(
        config,
        mid=106,
        boot_id="boot-active-b",
        device_sequence=0,
    )
    retired_redelivery = _message(
        config,
        mid=107,
        boot_id="boot-retired-a",
        device_sequence=0,
    )
    pending_redelivery = _message(
        config,
        mid=108,
        boot_id="boot-active-b",
        device_sequence=0,
    )
    fake_mqtt = _FakeMqtt((retired, pending))
    fake_mqtt.client.reconnect_messages = (
        retired_redelivery,
        pending_redelivery,
    )
    processors = _install_second_delivery_deferred_once_processor(monkeypatch)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    assert gateway.run_gateway(config, max_messages=4) == 4

    assert fake_mqtt.client.acknowledged == [(105, 1), (107, 1), (108, 1)]
    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    rejections = [
        event for event in events if event["event"] == "message_rejected"
    ]
    assert [event["failure"]["code"] for event in rejections] == [
        gateway.GatewayErrorCode.RETIRED_BOOT.value
    ]
    processor = processors[0]
    assert processor.test_runtime.calls == 3
    assert processor.has_pending_hybrid_delivery is False
    status = processor.status.snapshot()
    assert status["adapter_healthy"] is True
    assert status["runtime_ready"] is True
    assert status["last_failure"] is None


def test_lost_broker_session_with_a_pending_frame_fails_before_resubscribe(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    fake_mqtt = _FakeMqtt(_message(config, mid=85))
    fake_mqtt.client.session_present_on_reconnect = False
    processors = _install_always_deferred_processor(monkeypatch)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config, max_messages=2)

    assert exc_info.value.code is gateway.GatewayErrorCode.MQTT_SESSION_LOST
    assert processors[0].test_runtime.calls == 1
    assert fake_mqtt.client.reconnect_count == 1
    assert fake_mqtt.client.acknowledged == []


def test_session_loss_before_initial_suback_fails_with_pending_frame(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    message = _message(config, mid=109)
    fake_mqtt = _FakeMqtt(message)
    fake_mqtt.client.session_present_on_reconnect = False
    fake_mqtt.client.max_loop_count = 2
    processors = _install_always_deferred_processor(monkeypatch)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    def deliver_before_first_suback():
        if fake_mqtt.client.loop_count == 1:
            fake_mqtt.client.on_message(fake_mqtt.client, None, message)

    fake_mqtt.client.before_suback = deliver_before_first_suback

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config)

    assert exc_info.value.code is gateway.GatewayErrorCode.MQTT_SESSION_LOST
    assert processors[0].test_runtime.calls == 1
    assert fake_mqtt.client.loop_count == 2
    assert fake_mqtt.client.reconnect_count == 1
    assert fake_mqtt.client.subscribe_calls == 1
    assert fake_mqtt.client.acknowledged == []


def test_default_redelivery_budget_is_eight_attempts():
    assert gateway.MAX_REDELIVERY_ATTEMPTS == 8


@pytest.mark.parametrize("attempt_limit", [1, 2])
def test_persistently_deferred_delivery_exhausts_on_the_limit_attempt(
    monkeypatch, tmp_path, attempt_limit
):
    config = _bounded_config(tmp_path)
    fake_mqtt = _FakeMqtt(_message(config, mid=86))
    fake_mqtt.client.max_loop_count = attempt_limit + 1
    processors = _install_always_deferred_processor(monkeypatch)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "MAX_REDELIVERY_ATTEMPTS", attempt_limit)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config)

    assert exc_info.value.code is gateway.GatewayErrorCode.REDELIVERY_EXHAUSTED
    assert processors[0].test_runtime.calls == attempt_limit
    assert fake_mqtt.client.loop_count == attempt_limit
    assert fake_mqtt.client.reconnect_count == attempt_limit - 1
    assert fake_mqtt.client.acknowledged == []


def test_failed_puback_for_the_same_delivery_exhausts_the_same_retry_budget(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    fake_mqtt = _FakeMqtt(_message(config, mid=87))
    fake_mqtt.client.ack_result = 17
    fake_mqtt.client.max_loop_count = 3
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "MAX_REDELIVERY_ATTEMPTS", 2)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config)

    assert exc_info.value.code is gateway.GatewayErrorCode.REDELIVERY_EXHAUSTED
    assert fake_mqtt.client.ack_attempts == [(87, 1), (87, 1)]
    assert fake_mqtt.client.acknowledged == []
    assert fake_mqtt.client.loop_count == 2
    assert fake_mqtt.client.reconnect_count == 1


def test_lost_session_after_failed_puback_stops_before_resubscribing(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    message = _message(config, mid=119)
    fake_mqtt = _FakeMqtt(message)
    fake_mqtt.client.ack_result = 17
    fake_mqtt.client.session_present_on_reconnect = False
    fake_mqtt.client.max_loop_count = 2
    processors = []
    processor_class = gateway.GatewayProcessor

    class CapturingProcessor(processor_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.test_results = []
            processors.append(self)

        def process_message(self, topic, payload):
            result = super().process_message(topic, payload)
            self.test_results.append(result)
            return result

    monkeypatch.setattr(gateway, "GatewayProcessor", CapturingProcessor)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    def reject_resubscribe_after_lost_session():
        if fake_mqtt.client.loop_count == 2:
            raise AssertionError("lost persistent session was resubscribed")

    fake_mqtt.client.before_suback = reject_resubscribe_after_lost_session

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config)

    assert exc_info.value.code is gateway.GatewayErrorCode.MQTT_SESSION_LOST
    assert len(processors[0].test_results) == 1
    result = processors[0].test_results[0]
    assert result.kind is gateway.ProcessingKind.ACCEPTED
    assert result.runtime_outcome is not None
    assert result.runtime_outcome.acknowledged is True
    assert processors[0].has_pending_hybrid_delivery is False
    assert fake_mqtt.client.loop_count == 2
    assert fake_mqtt.client.reconnect_count == 1
    assert fake_mqtt.client.subscribe_calls == 1
    assert fake_mqtt.client.ack_attempts == [(119, 1)]
    assert fake_mqtt.client.acknowledged == []
    assert processors[0].status.snapshot()["last_failure"]["code"] == (
        gateway.GatewayErrorCode.MQTT_SESSION_LOST.value
    )


def test_canonical_pending_delivery_variants_share_one_retry_budget(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    template = _message(config, mid=110)
    logical_payload = json.loads(template.payload)
    raw_payloads = (
        template.payload,
        json.dumps(
            logical_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        json.dumps(
            {
                key: logical_payload[key]
                for key in reversed(tuple(logical_payload))
            },
            indent=2,
        ).encode("utf-8"),
    )
    assert len(set(raw_payloads)) == 3
    assert len(
        {
            gateway.LoadCellWireMessage.from_json(payload).canonical_bytes()
            for payload in raw_payloads
        }
    ) == 1
    deliveries = tuple(
        _message_with_raw_payload(template, mid=mid, payload=payload)
        for mid, payload in zip((110, 111, 112), raw_payloads, strict=True)
    )
    fake_mqtt = _FakeMqtt(deliveries[0])
    fake_mqtt.client.message_batches = tuple(
        (delivery,) for delivery in deliveries
    )
    fake_mqtt.client.max_loop_count = 3
    processors = _install_always_deferred_processor(monkeypatch)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "MAX_REDELIVERY_ATTEMPTS", 3)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config)

    assert exc_info.value.code is gateway.GatewayErrorCode.REDELIVERY_EXHAUSTED
    assert processors[0].test_runtime.calls == 3
    assert processors[0].has_pending_hybrid_delivery is True
    assert fake_mqtt.client.loop_count == 3
    assert fake_mqtt.client.reconnect_count == 2
    assert fake_mqtt.client.ack_attempts == []
    assert fake_mqtt.client.acknowledged == []
    assert processors[0].status.snapshot()["last_failure"]["code"] == (
        gateway.GatewayErrorCode.REDELIVERY_EXHAUSTED.value
    )


def test_duplicate_ack_cycle_does_not_reset_pending_delivery_retry_budget(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    completed_a = _message(config, mid=113, device_sequence=0)
    pending_b = _message(config, mid=114, device_sequence=1)
    duplicate_a_failed_ack = _message(config, mid=115, device_sequence=0)
    duplicate_a_successful_ack = _message(config, mid=116, device_sequence=0)
    pending_b_second_attempt = _message(config, mid=117, device_sequence=1)
    pending_b_third_attempt = _message(config, mid=118, device_sequence=1)
    fake_mqtt = _FakeMqtt(completed_a)
    fake_mqtt.client.message_batches = (
        (completed_a, pending_b),
        (duplicate_a_failed_ack,),
        (duplicate_a_successful_ack, pending_b_second_attempt),
        (pending_b_third_attempt,),
    )
    fake_mqtt.client.ack_results = [0, 17, 0]
    fake_mqtt.client.max_loop_count = 4
    processors = _install_first_ready_then_always_deferred_processor(monkeypatch)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "MAX_REDELIVERY_ATTEMPTS", 3)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config)

    assert exc_info.value.code is gateway.GatewayErrorCode.REDELIVERY_EXHAUSTED
    assert processors[0].test_runtime.calls == 4
    assert processors[0].has_pending_hybrid_delivery is True
    assert fake_mqtt.client.loop_count == 4
    assert fake_mqtt.client.reconnect_count == 3
    assert fake_mqtt.client.ack_attempts == [(113, 1), (115, 1), (116, 1)]
    assert fake_mqtt.client.acknowledged == [(113, 1), (116, 1)]
    pending_mids = {114, 117, 118}
    assert pending_mids.isdisjoint(
        mid for mid, _qos in fake_mqtt.client.ack_attempts
    )
    assert processors[0].status.snapshot()["last_failure"]["code"] == (
        gateway.GatewayErrorCode.REDELIVERY_EXHAUSTED.value
    )


def test_successful_puback_resets_retry_count_for_the_same_payload(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    deliveries = tuple(_message(config, mid=mid) for mid in (91, 92, 93, 94))
    fake_mqtt = _FakeMqtt(deliveries[0])
    fake_mqtt.client.message_batches = (
        (deliveries[0],),
        (deliveries[1], deliveries[2]),
        (deliveries[3],),
    )
    fake_mqtt.client.ack_results = [17, 0, 17, 17]
    fake_mqtt.client.max_loop_count = 3
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "MAX_REDELIVERY_ATTEMPTS", 2)
    monkeypatch.setattr(gateway, "REDELIVERY_BACKOFF_S", 0.0)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config)

    assert exc_info.value.code is gateway.GatewayErrorCode.REDELIVERY_EXHAUSTED
    assert fake_mqtt.client.ack_attempts == [
        (91, 1),
        (92, 1),
        (93, 1),
        (94, 1),
    ]
    assert fake_mqtt.client.acknowledged == [(92, 1)]
    assert fake_mqtt.client.loop_count == 3
    assert fake_mqtt.client.reconnect_count == 2


def test_replay_capacity_incident_stops_without_acknowledging_the_delivery(
    monkeypatch, tmp_path
):
    config = _bounded_config(tmp_path)
    messages = tuple(
        _message(
            config,
            mid=mid,
            boot_id=f"boot-capacity-{mid}",
            device_sequence=0,
        )
        for mid in (88, 89, 90)
    )
    fake_mqtt = _FakeMqtt(messages)
    monkeypatch.setattr(gateway, "_mqtt_module", lambda: fake_mqtt)
    monkeypatch.setattr(gateway, "MAX_RETIRED_BOOTS_PER_DEVICE", 1)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.run_gateway(config, max_messages=3)

    assert exc_info.value.code is gateway.GatewayErrorCode.REPLAY_CAPACITY_EXCEEDED
    assert fake_mqtt.client.acknowledged == [(88, 1), (89, 1)]


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
