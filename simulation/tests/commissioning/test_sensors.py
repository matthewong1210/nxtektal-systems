"""Sensor association, channel, units, and calibration validation."""

from __future__ import annotations

import copy

import pytest

from nxt_commissioning import CommissionedSite, CommissioningValidationError

from .conftest import clone_payload


@pytest.mark.parametrize("field", ["calibration_id", "calibrated_at", "valid_until"])
def test_calibrated_sensor_requires_complete_calibration_information(
    commissioned_site_payload, field
):
    payload = clone_payload(commissioned_site_payload)
    payload["sensor_bindings"][0]["calibration"][field] = None
    with pytest.raises(ValueError, match=field):
        CommissionedSite.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("calibrated_at", "2026-07-20T01:00:00"),
        ("valid_until", "2026-07-19T01:00:00Z"),
    ],
)
def test_calibration_timestamps_are_aware_and_ordered(
    commissioned_site_payload, field, value
):
    payload = clone_payload(commissioned_site_payload)
    payload["sensor_bindings"][0]["calibration"][field] = value
    with pytest.raises(ValueError, match=field):
        CommissionedSite.from_dict(payload)


def test_not_required_calibration_is_explicit_and_round_trips(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    payload["sensor_bindings"][1]["sensor_type"] = "external_system"
    info = payload["sensor_bindings"][1]["calibration"]
    info.update(
        {
            "status": "not_required",
            "calibration_id": None,
            "calibrated_at": None,
            "valid_until": None,
            "method": "digitally reported by the commissioned BMS",
        }
    )
    site = CommissionedSite.from_dict(payload)
    binding = next(
        item for item in site.sensor_bindings if item.sensor_id == "sensor-battery-01"
    )
    assert binding.calibration.status.value == "not_required"
    assert binding.calibration.calibration_id is None


def test_not_required_calibration_rejects_certificate_fields(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    info = payload["sensor_bindings"][1]["calibration"]
    info.update(
        {
            "status": "not_required",
            "calibrated_at": None,
            "valid_until": None,
            "method": "not required by manufacturer",
        }
    )
    with pytest.raises(ValueError, match="calibration_id"):
        CommissionedSite.from_dict(payload)


def test_duplicate_sensor_id_is_rejected(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    duplicate = copy.deepcopy(payload["sensor_bindings"][0])
    duplicate["channel"] = "inventory.washer-01.secondary"
    payload["sensor_bindings"].append(duplicate)
    with pytest.raises(CommissioningValidationError, match="duplicate sensor ID"):
        CommissionedSite.from_dict(payload)


def test_duplicate_telemetry_channel_is_rejected(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    duplicate = copy.deepcopy(payload["sensor_bindings"][0])
    duplicate["sensor_id"] = "sensor-load-03"
    payload["sensor_bindings"].append(duplicate)
    with pytest.raises(CommissioningValidationError, match="duplicate channel"):
        CommissionedSite.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value", "token"),
    [
        ("associated_asset_id", "missing-asset", "associated_asset"),
        ("channel", "not a channel", "channel"),
        ("source", "", "source"),
        ("units", "", "units"),
        ("sensor_type", "laser_beam", "sensor_type"),
    ],
)
def test_invalid_sensor_binding_is_rejected(
    commissioned_site_payload, field, value, token
):
    payload = clone_payload(commissioned_site_payload)
    payload["sensor_bindings"][0][field] = value
    with pytest.raises((CommissioningValidationError, TypeError, ValueError), match=token):
        CommissionedSite.from_dict(payload)


def test_load_cell_requires_a_measured_unit(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["sensor_bindings"][0]["units"] = "1"
    with pytest.raises(CommissioningValidationError, match="canonical unit"):
        CommissionedSite.from_dict(payload)


def test_proximity_switch_requires_dimensionless_unit(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["sensor_bindings"][0]["sensor_type"] = "proximity_switch"
    with pytest.raises(CommissioningValidationError, match="incompatible"):
        CommissionedSite.from_dict(payload)


def test_physical_measurement_sensor_cannot_skip_calibration(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    info = payload["sensor_bindings"][0]["calibration"]
    info.update(
        {
            "status": "not_required",
            "calibration_id": None,
            "calibrated_at": None,
            "valid_until": None,
            "method": "incorrectly declared not required",
        }
    )
    with pytest.raises(CommissioningValidationError, match="requires a calibrated"):
        CommissionedSite.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value", "token"),
    [
        ("valid_until", "2026-08-01T00:00:00Z", "expired"),
        ("calibrated_at", "2026-08-09T00:00:00Z", "after manifest"),
    ],
)
def test_calibration_must_be_valid_at_commissioning_approval(
    commissioned_site_payload, field, value, token
):
    payload = clone_payload(commissioned_site_payload)
    payload["sensor_bindings"][0]["calibration"][field] = value
    with pytest.raises(CommissioningValidationError, match=token):
        CommissionedSite.from_dict(payload)


def test_sensor_type_channel_asset_and_unit_must_be_semantically_consistent(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    binding = payload["sensor_bindings"][0]
    binding.update(
        {
            "sensor_type": "temperature",
            "channel": "robot.robot-01.battery_frac",
            "associated_asset_id": "washer-01",
            "units": "balls",
        }
    )
    with pytest.raises(CommissioningValidationError) as caught:
        CommissionedSite.from_dict(payload)
    message = str(caught.value)
    assert "sensor_type" in message
    assert "associated_asset_id" in message
    assert "units" in message
