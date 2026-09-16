"""Configuration, snapshot, identity, and template contracts of the intervention package."""

from __future__ import annotations

import json

import pytest

from nxt_edge_interventions import EdgeSnapshot, InterventionConfig, InterventionError, render_message
from nxt_edge_interventions.contracts import case_identity, is_loopback_literal, notification_identity
from nxt_edge_interventions.notify import validate_notification
from tests.edge_interventions.conftest import INTERVENTION_CONFIG_PATH


def _config_payload(**changes):
    payload = json.loads(INTERVENTION_CONFIG_PATH.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if key.startswith("receiver."):
            payload["receiver"][key.split(".", 1)[1]] = value
        else:
            payload[key] = value
    return payload


def test_example_config_loads_and_is_loopback_only() -> None:
    config = InterventionConfig.from_dict(_config_payload())
    assert config.receiver.host == "127.0.0.1" and config.receiver.url == "http://127.0.0.1:18831/notifications"
    for host in ("localhost", "10.0.0.5", "0.0.0.0", "127.１.0.1", "example.invalid", "::ffff:127.0.0.1"):
        with pytest.raises(InterventionError) as raised:
            InterventionConfig.from_dict(_config_payload(**{"receiver.host": host}))
        assert raised.value.code == "invalid_config"
    assert is_loopback_literal("::1") and is_loopback_literal("127.255.0.9") and not is_loopback_literal("127.0.0.256")


def test_config_is_strict_and_bounded() -> None:
    for changes in ({"max_reminders": -1}, {"notify_max_attempts": 0}, {"notify_max_attempts": True}, {"reminder_interval_s": 0}, {"schema": "other"}, {"extra": 1}, {"receiver.port": 80}):
        with pytest.raises(InterventionError):
            InterventionConfig.from_dict(_config_payload(**changes))
    payload = _config_payload()
    del payload["tick_interval_s"]
    with pytest.raises(InterventionError):
        InterventionConfig.from_dict(payload)


def _snapshot():
    return {
        "tasks": {
            "task_a": {
                "task_id": "task_a",
                "target_robot_id": "picker-01",
                "state": "RUNNING",
                "effective_result": None,
                "reconciliation_reasons": [],
                "publish_attempts": 1,
                "expired_unconfirmed": False,
                "blocking_event": None,
                "expires_at_utc": "2026-09-12T08:10:00.000000Z",
            }
        },
        "devices": {
            "picker-01": {
                "robot_id": "picker-01",
                "connectivity": "ONLINE",
                "connectivity_since_utc": "2026-09-12T08:00:01.000000Z",
                "last_valid_status_received_at_utc": "2026-09-12T08:00:01.000000Z",
                "last_reported_availability": "busy",
                "current_task": {"task_id": "task_a"},
                "session_regression": False,
            }
        },
        "edge": {"last_record_id": "rec_x", "max_republish_attempts": 6},
    }


def test_snapshot_refuses_missing_fields_instead_of_defaulting() -> None:
    EdgeSnapshot.from_dict(_snapshot())
    for path in (("tasks", "task_a", "blocking_event"), ("devices", "picker-01", "connectivity"), ("devices", "picker-01", "last_reported_availability"), ("edge", "max_republish_attempts")):
        payload = _snapshot()
        node = payload
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
        with pytest.raises(InterventionError) as raised:
            EdgeSnapshot.from_dict(payload)
        assert raised.value.code == "invalid_snapshot"
    payload = _snapshot()
    payload["devices"]["picker-01"]["robot_id"] = "other"
    with pytest.raises(InterventionError):
        EdgeSnapshot.from_dict(payload)
    with pytest.raises(InterventionError):
        EdgeSnapshot.from_dict({**_snapshot(), "extra": 1})


def test_identities_are_stable_and_distinct_per_evidence() -> None:
    a = case_identity("ASSISTANCE_REQUIRED", "task", "task_a", [1, 2])
    assert a == case_identity("ASSISTANCE_REQUIRED", "task", "task_a", [1, 2])
    assert a != case_identity("ASSISTANCE_REQUIRED", "task", "task_a", [1, 3])
    assert a != case_identity("DEVICE_UNREACHABLE", "task", "task_a", [1, 2])
    n0 = notification_identity(a, "OPENED", 0)
    assert n0 == notification_identity(a, "OPENED", 0) and n0 != notification_identity(a, "REMINDER", 1)


def test_templates_mark_unknown_values_and_never_claim_a_stop() -> None:
    message = render_message(
        "DEVICE_UNREACHABLE",
        "CRITICAL",
        {"robot_id": "picker-01", "offline_since_utc": "2026-09-12T08:00:20.000000Z", "last_valid_status_received_at_utc": None, "last_reported_availability": None, "current_task_id": None},
    )
    assert "unknown" in message and "not confirmed stopped or parked" in message and "[CRITICAL]" in message
    assert "parked" not in message.replace("not confirmed stopped or parked", "")


def test_receiver_validates_the_notification_envelope() -> None:
    good = {
        "schema": "nxt.edge.intervention.notification/v1",
        "environment": {"kind": "SIMULATION", "simulation_env_id": "sim-local-01"},
        "site_id": "pilot-course-a",
        "deployment_id": "pilot-a-edge-task-sim-v0",
        "notification_id": "ntf_x",
        "case_id": "case_x",
        "intent": "OPENED",
        "kind": "ASSISTANCE_REQUIRED",
        "severity": "WARNING",
        "subject_kind": "task",
        "subject_id": "task_a",
        "robot_id": "picker-01",
        "human_state": "OPEN",
        "message": "help",
        "evidence": {},
    }
    validate_notification(good, site_id="pilot-course-a", deployment_id="pilot-a-edge-task-sim-v0")
    for bad in ({**good, "environment": {"kind": "LIVE", "simulation_env_id": "x"}}, {**good, "site_id": "other"}, {**good, "message": ""}, {k: v for k, v in good.items() if k != "evidence"}, {**good, "extra": 1}):
        with pytest.raises(InterventionError):
            validate_notification(bad, site_id="pilot-course-a", deployment_id="pilot-a-edge-task-sim-v0")
