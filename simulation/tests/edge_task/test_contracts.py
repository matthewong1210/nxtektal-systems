"""Wire contract strictness, identity derivation, and vocabulary parity."""

from __future__ import annotations

import json

import pytest

from nxt_edge_task.contracts import (
    ENVIRONMENT_KIND_SIMULATION,
    EVENT_SCHEMA,
    REQUEST_SCHEMA,
    STATUS_SCHEMA,
    Availability,
    EdgeTaskConfig,
    EdgeTaskError,
    ErrorCode,
    RobotStatusMessage,
    TaskEvent,
    TaskRequest,
    decode_object,
    derive_task_id,
    normalize_reason,
    reason_class,
    topic_parts,
)
from nxt_pilot_ops.contracts import RobotStatus
from tests.edge_task.conftest import CONFIG_PATH


def _request_fields(**overrides):
    fields = {
        "schema": REQUEST_SCHEMA,
        "site_id": "pilot-course-a",
        "deployment_id": "pilot-a-edge-task-sim-v0",
        "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": "sim-local-01"},
        "target_robot_id": "picker-01",
        "task_type": "COLLECT_BALLS_ZONE",
        "parameters": {"zone_id": "Z1"},
        "issued_at_utc": "2026-09-12T08:00:00.000000Z",
        "expires_at_utc": "2026-09-12T08:10:00.000000Z",
        "progress_window_s": 20,
        "issued_by": "SIMULATION_TEST_ENTRY:test",
    }
    fields.update(overrides)
    fields["task_id"] = derive_task_id(fields)
    return fields


def test_task_id_is_content_derived_and_verified() -> None:
    fields = _request_fields()
    request = TaskRequest.from_dict(fields)
    assert request.task_id == fields["task_id"]
    assert TaskRequest.from_json(request.canonical_bytes()) == request
    tampered = dict(fields)
    tampered["parameters"] = {"zone_id": "Z2"}
    with pytest.raises(EdgeTaskError) as raised:
        TaskRequest.from_dict(tampered)
    assert raised.value.code is ErrorCode.TASK_ID_CONTENT_CONFLICT
    # Different issued_at is a different task (independent creation), same task otherwise.
    other = _request_fields(issued_at_utc="2026-09-12T08:01:00.000000Z")
    assert other["task_id"] != fields["task_id"]
    assert _request_fields()["task_id"] == fields["task_id"]


def test_environment_kind_is_single_member() -> None:
    for kind in ("LIVE", "PRODUCTION", "simulation", "", None):
        fields = _request_fields()
        fields["environment"] = {"kind": kind, "simulation_env_id": "sim-local-01"}
        fields["task_id"] = derive_task_id(fields)
        with pytest.raises(EdgeTaskError) as raised:
            TaskRequest.from_dict(fields)
        assert raised.value.code is ErrorCode.ENVIRONMENT_MISMATCH


def test_issued_by_must_be_the_simulation_test_entry() -> None:
    for issued_by in ("scheduler", "SIMULATION_TEST_ENTRY:", "PRODUCTION:x", "SIMULATION_TEST_ENTRY:with space"):
        fields = _request_fields(issued_by=issued_by)
        with pytest.raises(EdgeTaskError):
            TaskRequest.from_dict(fields)


@pytest.mark.parametrize(
    "raw, code",
    [
        (b'{"a":1,"a":2}', ErrorCode.INVALID_JSON),
        (b'{"a":NaN}', ErrorCode.INVALID_JSON),
        (b"[1]", ErrorCode.INVALID_JSON),
        (b"\xff\xfe", ErrorCode.INVALID_JSON),
        (b"{" * 40 + b"}" * 40, ErrorCode.INVALID_JSON),
        (b"x" * 70_000, ErrorCode.PAYLOAD_TOO_LARGE),
    ],
)
def test_strict_json_decoding(raw: bytes, code: ErrorCode) -> None:
    with pytest.raises(EdgeTaskError) as raised:
        decode_object(raw)
    assert raised.value.code is code


def test_unknown_and_missing_keys_fail_closed() -> None:
    fields = _request_fields()
    fields["extra"] = 1
    with pytest.raises(EdgeTaskError) as raised:
        TaskRequest.from_dict(fields)
    assert raised.value.code is ErrorCode.INVALID_FIELD
    fields = _request_fields()
    del fields["issued_by"]
    with pytest.raises(EdgeTaskError):
        TaskRequest.from_dict(fields)


def test_event_sequence_zero_is_reserved_for_rejected() -> None:
    base = {
        "schema": EVENT_SCHEMA,
        "site_id": "pilot-course-a",
        "deployment_id": "pilot-a-edge-task-sim-v0",
        "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": "sim-local-01"},
        "task_id": _request_fields()["task_id"],
        "robot_id": "picker-01",
        "boot_id": "boot-picker-01-1",
        "boot_sequence": 1,
        "event_sequence": 0,
        "kind": "ACCEPTED",
        "reason_code": None,
        "detail": "",
        "reported_at_utc": "2026-09-12T08:00:00.000000Z",
        "progress": None,
    }
    with pytest.raises(EdgeTaskError):
        TaskEvent.from_dict(base)
    base["kind"] = "REJECTED"
    base["reason_code"] = "task_id_content_conflict"
    event = TaskEvent.from_dict(base)
    assert event.is_request_level_rejection and not event.is_terminal


def test_unknown_reason_codes_are_preserved_and_classified() -> None:
    assert normalize_reason("E42") == "unknown:E42"
    assert normalize_reason("unknown:E42") == "unknown:E42"
    assert normalize_reason("energy_insufficient") == "energy_insufficient"
    assert reason_class("energy_insufficient").value == "robot_condition"
    assert reason_class("task_expired").value == "protocol_entry"
    assert reason_class("unknown:E42").value == "unknown"
    with pytest.raises(EdgeTaskError):
        normalize_reason("bad reason with spaces")
    base = json.loads(
        json.dumps(
            {
                "schema": EVENT_SCHEMA,
                "site_id": "pilot-course-a",
                "deployment_id": "pilot-a-edge-task-sim-v0",
                "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": "sim-local-01"},
                "task_id": _request_fields()["task_id"],
                "robot_id": "picker-01",
                "boot_id": "boot-picker-01-1",
                "boot_sequence": 1,
                "event_sequence": 2,
                "kind": "ASSISTANCE_REQUIRED",
                "reason_code": "E42",
                "detail": "raw code",
                "reported_at_utc": "2026-09-12T08:00:00.000000Z",
                "progress": None,
            }
        )
    )
    with pytest.raises(EdgeTaskError):
        TaskEvent.from_dict(base)  # wire must carry the normalized form
    base["reason_code"] = "unknown:E42"
    assert TaskEvent.from_dict(base).reason_code == "unknown:E42"


def test_availability_vocabulary_matches_shadow_ops_robot_status_minus_offline() -> None:
    expected = {status.value for status in RobotStatus} - {"offline"}
    assert {item.value for item in Availability} == expected


def test_status_message_round_trip_and_null_semantics() -> None:
    payload = {
        "schema": STATUS_SCHEMA,
        "site_id": "pilot-course-a",
        "deployment_id": "pilot-a-edge-task-sim-v0",
        "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": "sim-local-01"},
        "robot_id": "picker-01",
        "boot_id": "boot-picker-01-1",
        "boot_sequence": 1,
        "status_sequence": 3,
        "reported_at_utc": "2026-09-12T08:00:00.000000Z",
        "availability": "available",
        "current_task": None,
        "capabilities": {"task_types": ["COLLECT_BALLS_ZONE"]},
        "energy": {"level_fraction": None, "can_continue": None, "needs_manual_recharge": None},
        "safety": {"estop_latched": None, "awaiting_human": None, "safe_return_confirmed": None},
        "fault_code": None,
        "location": {"zone_id": None, "x_m": None, "y_m": None, "coordinate_frame": None},
    }
    message = RobotStatusMessage.from_dict(payload)
    assert message.can_continue is None and message.level_fraction is None
    assert RobotStatusMessage.from_json(message.canonical_bytes()) == message
    payload["availability"] = "offline"
    with pytest.raises(EdgeTaskError):
        RobotStatusMessage.from_dict(payload)
    payload["availability"] = "available"
    payload["energy"]["level_fraction"] = 1.5
    with pytest.raises(EdgeTaskError):
        RobotStatusMessage.from_dict(payload)


def test_topics_are_exact() -> None:
    assert topic_parts("nxt/v1/sites/s/robots/r/status") == ("s", "r", "status")
    assert topic_parts("nxt/v1/sites/s/robots/r/task/request") == ("s", "r", "request")
    assert topic_parts("nxt/v1/sites/s/robots/r/task/event") == ("s", "r", "event")
    for bad in ("nxt/v1/sites/s/robots/+/status", "nxt/v2/sites/s/robots/r/status", "nxt/v1/sites/s/robots/r/task", "nxt/v1/sites//robots/r/status", 7):
        with pytest.raises(EdgeTaskError):
            topic_parts(bad)


def test_config_rejects_unknown_keys_and_a_kind_switch() -> None:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config = EdgeTaskConfig.from_dict(payload)
    assert config.robot_ids == ("picker-01", "carrier-01")
    payload["environment_kind"] = "LIVE"
    with pytest.raises(EdgeTaskError) as raised:
        EdgeTaskConfig.from_dict(payload)
    assert raised.value.code is ErrorCode.INVALID_CONFIG
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["robots"][0]["client_id"] = payload["edge"]["client_id"]
    with pytest.raises(EdgeTaskError):
        EdgeTaskConfig.from_dict(payload)
