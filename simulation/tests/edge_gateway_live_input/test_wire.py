"""Strict wire, deployment-config, topic, and identity contracts for V0.

The gateway is a deployment composition root.  These tests deliberately target
its public script API while leaving canonical observation and commissioning
contracts with their existing owners.
"""

from __future__ import annotations

import copy
import json

import pytest

from scripts.edge_gateway_live_input_v0 import (
    GatewayConfig,
    GatewayError,
    GatewayErrorCode,
    LoadCellWireMessage,
    MAX_ERROR_DETAIL_CHARS,
    MAX_WIRE_PAYLOAD_BYTES,
    expected_topic,
    validate_topic_and_identity,
)
from scripts.pilot_course_a_edge_fixture import (
    CALIBRATION_ID_LOAD_CELL,
    DEPLOYMENT_ID,
    SENSOR_DISPENSER_COUNT,
    SENSOR_DISPENSER_SENSED,
    SITE_ID,
    commissioned_site,
)

CONFIG_SCHEMA = "nxt-edge-gateway/config/v0"
WIRE_SCHEMA = "nxt.edge.load-cell.raw/v1"
GATEWAY_ID = "gw-pilot-a-01"
DEVICE_ID = "loadcell-controller-01"
BOOT_ID = "boot-20260828-001"


def _config_mapping() -> dict:
    return {
        "edge_gateway": {
            "schema": CONFIG_SCHEMA,
            "mode": "LOAD_CELL_DIAGNOSTIC",
            "site_id": SITE_ID,
            "deployment_id": DEPLOYMENT_ID,
            "gateway_id": GATEWAY_ID,
            "broker": {
                "host": "localhost",
                "port": 1883,
                "keepalive_s": 30,
                "qos": 1,
                "client_id": GATEWAY_ID,
            },
            "devices": [
                {
                    "device_id": DEVICE_ID,
                    "sensor_ids": [
                        SENSOR_DISPENSER_COUNT,
                        SENSOR_DISPENSER_SENSED,
                    ],
                }
            ],
            "status": {"host": "127.0.0.1", "port": 0},
            "evidence_dir": "reports/edge-gateway-v0",
            "fixture_cycle_index": 0,
        }
    }


def _wire_payload(**changes: object) -> dict:
    payload = {
        "schema": WIRE_SCHEMA,
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "gateway_id": GATEWAY_ID,
        "device_id": DEVICE_ID,
        "sensor_id": SENSOR_DISPENSER_COUNT,
        "boot_id": BOOT_ID,
        "device_sequence": 1842,
        "sampled_at_utc": "2026-08-28T03:15:04.120Z",
        "published_at_utc": "2026-08-28T03:15:04.220Z",
        "raw_value": 288.5,
        "raw_unit": "kg",
        "device_status": "ok",
        "calibration_id": CALIBRATION_ID_LOAD_CELL,
        "diagnostic_code": None,
    }
    payload.update(changes)
    return payload


WIRE_FIELDS = tuple(_wire_payload())
CONFIG_FIELDS = tuple(_config_mapping()["edge_gateway"])


def _decode(payload: dict) -> LoadCellWireMessage:
    return LoadCellWireMessage.from_json(json.dumps(payload, allow_nan=True))


def _assert_error_code(excinfo: pytest.ExceptionInfo[GatewayError], code) -> None:
    assert excinfo.value.code is code


def test_gateway_config_accepts_only_the_exact_inner_or_wrapped_shape():
    mapping = _config_mapping()
    wrapped = GatewayConfig.from_mapping(mapping)
    inner = GatewayConfig.from_mapping(mapping["edge_gateway"])

    assert wrapped == inner
    assert wrapped.site_id == SITE_ID
    assert wrapped.deployment_id == DEPLOYMENT_ID
    assert wrapped.gateway_id == GATEWAY_ID


@pytest.mark.parametrize("field", CONFIG_FIELDS)
def test_gateway_config_rejects_each_missing_required_field(field):
    mapping = _config_mapping()
    mapping["edge_gateway"].pop(field)

    with pytest.raises(GatewayError) as excinfo:
        GatewayConfig.from_mapping(mapping)
    _assert_error_code(excinfo, GatewayErrorCode.INVALID_CONFIG)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unexpected": True}),
        lambda value: value["broker"].update({"password": "not-allowed"}),
        lambda value: value["status"].update({"path": "/status"}),
        lambda value: value["devices"][0].update({"topic": "invented/topic"}),
    ],
)
def test_gateway_config_rejects_unknown_fields_at_every_level(mutate):
    mapping = _config_mapping()
    mutate(mapping["edge_gateway"])

    with pytest.raises(GatewayError) as excinfo:
        GatewayConfig.from_mapping(mapping)
    _assert_error_code(excinfo, GatewayErrorCode.INVALID_CONFIG)


def test_gateway_config_rejects_unknown_wrapper_fields():
    mapping = _config_mapping()
    mapping["another_gateway"] = copy.deepcopy(mapping["edge_gateway"])

    with pytest.raises(GatewayError) as excinfo:
        GatewayConfig.from_mapping(mapping)
    _assert_error_code(excinfo, GatewayErrorCode.INVALID_CONFIG)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["broker"].update({"port": True}),
        lambda value: value["broker"].update({"keepalive_s": float("inf")}),
        lambda value: value["broker"].update({"keepalive_s": 65_536}),
        lambda value: value["broker"].update({"qos": 1.0}),
        lambda value: value["status"].update({"port": False}),
        lambda value: value.update({"fixture_cycle_index": True}),
    ],
)
def test_gateway_config_rejects_boolean_or_nonfinite_numeric_fields(mutate):
    mapping = _config_mapping()
    mutate(mapping["edge_gateway"])

    with pytest.raises(GatewayError) as excinfo:
        GatewayConfig.from_mapping(mapping)
    _assert_error_code(excinfo, GatewayErrorCode.INVALID_CONFIG)


def test_valid_v1_wire_message_is_decoded_without_relabelling_identity():
    message = _decode(_wire_payload())

    assert isinstance(message, LoadCellWireMessage)
    assert message.schema == WIRE_SCHEMA
    assert message.site_id == SITE_ID
    assert message.deployment_id == DEPLOYMENT_ID
    assert message.gateway_id == GATEWAY_ID
    assert message.device_id == DEVICE_ID
    assert message.sensor_id == SENSOR_DISPENSER_COUNT
    assert message.boot_id == BOOT_ID
    assert message.device_sequence == 1842
    assert message.raw_value == 288.5
    assert message.raw_unit == "kg"
    assert message.calibration_id == CALIBRATION_ID_LOAD_CELL


def test_wire_null_value_is_a_declared_missing_reading_not_malformed_json():
    message = _decode(_wire_payload(raw_value=None, device_status="no_data"))
    assert message.raw_value is None
    assert message.device_status == "no_data"


@pytest.mark.parametrize("field", WIRE_FIELDS)
def test_v1_wire_message_rejects_each_missing_field(field):
    payload = _wire_payload()
    payload.pop(field)

    with pytest.raises(GatewayError) as excinfo:
        _decode(payload)
    _assert_error_code(excinfo, GatewayErrorCode.MISSING_FIELD)


def test_v1_wire_message_rejects_an_unknown_schema():
    with pytest.raises(GatewayError) as excinfo:
        _decode(_wire_payload(schema="nxt.edge.load-cell.raw/v2"))
    _assert_error_code(excinfo, GatewayErrorCode.SCHEMA_MISMATCH)


def test_v1_wire_message_rejects_unknown_fields_instead_of_ignoring_drift():
    with pytest.raises(GatewayError) as excinfo:
        _decode(_wire_payload(raw_value_volts=4.2))
    _assert_error_code(excinfo, GatewayErrorCode.UNKNOWN_FIELD)


@pytest.mark.parametrize("raw", ["{", "[]", "null", '"not-an-object"'])
def test_malformed_json_or_a_non_object_root_fails_closed(raw):
    with pytest.raises(GatewayError) as excinfo:
        LoadCellWireMessage.from_json(raw)
    _assert_error_code(excinfo, GatewayErrorCode.MALFORMED_JSON)


def test_oversized_wire_payload_is_rejected_before_json_decoding():
    raw = b"{" + b"x" * MAX_WIRE_PAYLOAD_BYTES + b"}"

    with pytest.raises(GatewayError) as excinfo:
        LoadCellWireMessage.from_json(raw)

    _assert_error_code(excinfo, GatewayErrorCode.PAYLOAD_TOO_LARGE)


def test_excessively_deep_json_fails_closed_without_escaping_recursion_error():
    raw = "[" * 2_000 + "0" + "]" * 2_000

    with pytest.raises(GatewayError) as excinfo:
        LoadCellWireMessage.from_json(raw)

    _assert_error_code(excinfo, GatewayErrorCode.MALFORMED_JSON)


def test_attacker_controlled_unknown_field_detail_is_bounded():
    payload = _wire_payload()
    payload["x" * 10_000] = True

    with pytest.raises(GatewayError) as excinfo:
        _decode(payload)

    _assert_error_code(excinfo, GatewayErrorCode.UNKNOWN_FIELD)
    assert len(excinfo.value.detail) <= MAX_ERROR_DETAIL_CHARS
    assert excinfo.value.detail.endswith("...[detail truncated]")


def test_duplicate_json_keys_are_rejected_before_normal_decoding():
    raw = json.dumps(_wire_payload(), separators=(",", ":"))
    raw = raw.replace(
        "{",
        '{"sensor_id":"sensor-lc-shadow-claim",',
        1,
    )

    with pytest.raises(GatewayError) as excinfo:
        LoadCellWireMessage.from_json(raw)
    _assert_error_code(excinfo, GatewayErrorCode.DUPLICATE_JSON_KEY)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("device_sequence", True),
        ("device_sequence", 1842.0),
        ("device_sequence", -1),
        ("raw_value", True),
        ("raw_value", float("nan")),
        ("raw_value", float("inf")),
        ("raw_value", float("-inf")),
    ],
)
def test_boolean_nonintegral_negative_and_nonfinite_numbers_are_rejected(
    field, value
):
    with pytest.raises(GatewayError) as excinfo:
        _decode(_wire_payload(**{field: value}))
    _assert_error_code(excinfo, GatewayErrorCode.INVALID_NUMBER)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sampled_at_utc", "2026-08-28T03:15:04.120"),
        ("sampled_at_utc", "2026-08-28T11:15:04.120+08:00"),
        ("published_at_utc", "2026-02-30T03:15:04Z"),
        ("published_at_utc", "not-a-timestamp"),
    ],
)
def test_wire_timestamps_must_be_well_formed_and_utc(field, value):
    with pytest.raises(GatewayError) as excinfo:
        _decode(_wire_payload(**{field: value}))
    _assert_error_code(excinfo, GatewayErrorCode.INVALID_TIMESTAMP)


def test_an_explicit_zero_utc_offset_is_accepted_as_utc():
    message = _decode(
        _wire_payload(
            sampled_at_utc="2026-08-28T03:15:04.120+00:00",
            published_at_utc="2026-08-28T03:15:04.220+00:00",
        )
    )
    assert message.device_sequence == 1842


def test_published_timestamp_cannot_precede_sampled_timestamp():
    with pytest.raises(GatewayError) as excinfo:
        _decode(
            _wire_payload(
                sampled_at_utc="2026-08-28T03:15:04.220Z",
                published_at_utc="2026-08-28T03:15:04.120Z",
            )
        )
    _assert_error_code(excinfo, GatewayErrorCode.TIMESTAMP_ORDER)


def test_equal_sample_and_publish_timestamps_are_valid():
    timestamp = "2026-08-28T03:15:04.120Z"
    message = _decode(
        _wire_payload(sampled_at_utc=timestamp, published_at_utc=timestamp)
    )
    assert message.device_sequence == 1842


def test_topic_is_derived_from_config_not_supplied_as_a_competing_fact():
    config = GatewayConfig.from_mapping(_config_mapping())
    assert expected_topic(config, DEVICE_ID) == (
        f"nxt/v1/sites/{SITE_ID}/devices/{DEVICE_ID}/load-cell"
    )


def test_valid_topic_message_config_and_commissioned_identity_agree():
    config = GatewayConfig.from_mapping(_config_mapping())
    message = _decode(_wire_payload())
    topic = expected_topic(config, DEVICE_ID)

    assert validate_topic_and_identity(
        topic, message, config, commissioned_site()
    ) is None


@pytest.mark.parametrize(
    "topic",
    [
        f"nxt/v1/sites/wrong-site/devices/{DEVICE_ID}/load-cell",
        f"nxt/v1/sites/{SITE_ID}/devices/wrong-device/load-cell",
        f"nxt/v1/sites/{SITE_ID}/devices/{DEVICE_ID}/load-cell/extra",
        f"nxt/v1/sites/{SITE_ID}/devices/+/load-cell",
        f"nxt/v2/sites/{SITE_ID}/devices/{DEVICE_ID}/load-cell",
    ],
)
def test_topic_disagreement_and_wildcards_fail_closed(topic):
    config = GatewayConfig.from_mapping(_config_mapping())
    message = _decode(_wire_payload())

    with pytest.raises(GatewayError) as excinfo:
        validate_topic_and_identity(topic, message, config, commissioned_site())
    _assert_error_code(excinfo, GatewayErrorCode.INVALID_TOPIC)


@pytest.mark.parametrize(
    "changes",
    [
        {"site_id": "another-site"},
        {"deployment_id": "another-deployment"},
        {"gateway_id": "another-gateway"},
        {"device_id": "another-device"},
        {"sensor_id": "sensor-not-routed-to-this-device"},
    ],
)
def test_message_identity_must_match_configured_routing(changes):
    config = GatewayConfig.from_mapping(_config_mapping())
    message = _decode(_wire_payload(**changes))

    with pytest.raises(GatewayError) as excinfo:
        validate_topic_and_identity(
            expected_topic(config, DEVICE_ID),
            message,
            config,
            commissioned_site(),
        )
    _assert_error_code(excinfo, GatewayErrorCode.IDENTITY_MISMATCH)


def test_config_and_wire_identity_cannot_override_the_commissioned_site():
    mapping = _config_mapping()
    mapping["edge_gateway"]["site_id"] = "uncommissioned-site"
    config = GatewayConfig.from_mapping(mapping)
    message = _decode(_wire_payload(site_id="uncommissioned-site"))

    with pytest.raises(GatewayError) as excinfo:
        validate_topic_and_identity(
            expected_topic(config, DEVICE_ID),
            message,
            config,
            commissioned_site(),
        )
    _assert_error_code(excinfo, GatewayErrorCode.IDENTITY_MISMATCH)


def test_a_sensor_must_exist_in_the_commissioned_binding_set():
    mapping = _config_mapping()
    mapping["edge_gateway"]["devices"][0]["sensor_ids"] = ["sensor-invented"]
    config = GatewayConfig.from_mapping(mapping)
    message = _decode(_wire_payload(sensor_id="sensor-invented"))

    with pytest.raises(GatewayError) as excinfo:
        validate_topic_and_identity(
            expected_topic(config, DEVICE_ID),
            message,
            config,
            commissioned_site(),
        )
    _assert_error_code(excinfo, GatewayErrorCode.IDENTITY_MISMATCH)


def test_calibration_disagreement_is_left_for_the_existing_adapter_report():
    """Transport checks identity; the adapter owns calibration semantics."""
    config = GatewayConfig.from_mapping(_config_mapping())
    message = _decode(_wire_payload(calibration_id="CAL-WRONG-0001"))

    assert validate_topic_and_identity(
        expected_topic(config, DEVICE_ID),
        message,
        config,
        commissioned_site(),
    ) is None
