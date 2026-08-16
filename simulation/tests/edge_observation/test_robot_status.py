"""Robot-status adapter: identity, battery units, heartbeat, no inference."""

from __future__ import annotations

import dataclasses

import pytest

from nxt_edge_observation import (
    EdgeAdapterError,
    RawSampleTiming,
    RejectionCode,
    RobotStatusAdapter,
    RobotStatusProfile,
)
from nxt_telemetry.observations import ObservationStatus, SourceType

from scripts.pilot_course_a_edge_fixture import (
    COORDINATE_FRAME,
    ROBOT_IDS,
    ZONE_ID,
    adapter_kit,
)

from .conftest import (
    FRAME_T_S,
    convert_one,
    rejection_codes,
    rejections_for,
    robot_sample,
    timing,
    valued_channels,
)

ROBOT = ROBOT_IDS[0]
BATTERY_CHANNEL = f"robot.{ROBOT}.battery_frac"
ACTIVITY_CHANNEL = f"robot.{ROBOT}.activity"
PAYLOAD_CHANNEL = f"robot.{ROBOT}.payload_balls"
ESTOP_CHANNEL = f"robot.{ROBOT}.estop_latched"
LOCATION_CHANNEL = f"robot.{ROBOT}.location"
ZONE_CHANNEL = f"robot.{ROBOT}.assigned_zone"

CANONICAL_CHANNELS = {
    f"robot.{ROBOT}.{field}"
    for field in (
        "activity",
        "health",
        "battery_frac",
        "payload_balls",
        "location",
        "destination",
        "assigned_zone",
        "estop_latched",
        "awaiting_human",
    )
}


def test_a_known_robot_maps_every_commissioned_channel(kit):
    result, by_channel = convert_one(kit, robots=(robot_sample(),))
    valued = valued_channels(result)
    assert set(valued) == CANONICAL_CHANNELS
    assert all(item.status is ObservationStatus.OK for item in valued.values())
    assert by_channel[ACTIVITY_CHANNEL].source_type is SourceType.EXTERNAL_SYSTEM
    assert rejections_for(result.report, ROBOT) == []


def test_an_unknown_robot_identity_is_rejected(kit):
    result, _ = convert_one(kit, robots=(robot_sample(robot_id="R99"),))
    # An uncommissioned robot produces no value, and every commissioned
    # robot channel is still reported as an explicit gap.
    assert valued_channels(result) == {}
    assert RejectionCode.IDENTITY_MISMATCH.value in rejection_codes(result.report)


def test_declared_percent_battery_is_converted_to_a_fraction(kit):
    _, by_channel = convert_one(kit, robots=(robot_sample(battery=82.0),))
    assert by_channel[BATTERY_CHANNEL].value == pytest.approx(0.82)


def test_a_fraction_reading_is_not_guessed_from_magnitude(kit):
    """0.82 under a declared percent unit is 0.82 %, not 82 %."""
    _, by_channel = convert_one(kit, robots=(robot_sample(battery=0.82),))
    assert by_channel[BATTERY_CHANNEL].value == pytest.approx(0.0082)


def test_a_declared_fraction_profile_reads_the_same_number_differently(site):
    fraction_kit = _kit_with_battery_unit(site, "fraction")
    _, by_channel = convert_one(fraction_kit, robots=(robot_sample(battery=0.82),))
    assert by_channel[BATTERY_CHANNEL].value == pytest.approx(0.82)


def _kit_with_battery_unit(site, unit: str):
    original = adapter_kit(site)
    robots = tuple(
        dataclasses.replace(profile, battery_unit=unit)
        for profile in original.robot_profiles
    )
    return type(original)(
        bindings=original.bindings,
        coordinate_frame=COORDINATE_FRAME,
        load_cell_profiles=original.load_cell_profiles,
        digital_device_profiles=original.digital_device_profiles,
        digital_input_profiles=original.digital_input_profiles,
        robot_profiles=robots,
    )


@pytest.mark.parametrize("battery", [-1.0, 100.1, 250.0, float("nan")])
def test_out_of_range_or_non_finite_battery_fails_closed(kit, battery):
    result, by_channel = convert_one(kit, robots=(robot_sample(battery=battery),))
    assert by_channel[BATTERY_CHANNEL].status is ObservationStatus.MISSING
    assert rejection_codes(result.report) & {
        RejectionCode.VALUE_OUT_OF_RANGE.value,
        RejectionCode.NON_FINITE_VALUE.value,
    }


def test_a_fresh_heartbeat_is_ok(kit):
    _, by_channel = convert_one(kit, robots=(robot_sample(timing=timing()),))
    assert by_channel[ACTIVITY_CHANNEL].status is ObservationStatus.OK


def test_a_stale_heartbeat_never_reads_as_available(kit):
    _, by_channel = convert_one(
        kit, robots=(robot_sample(timing=timing(age_s=900.0)),)
    )
    for channel in CANONICAL_CHANNELS:
        assert by_channel[channel].status is ObservationStatus.STALE
        assert by_channel[channel].confidence == 0.5


def test_a_missing_status_report_becomes_missing_not_healthy(kit):
    result, by_channel = convert_one(
        kit, robots=(robot_sample(device_status="no_data"),)
    )
    for channel in CANONICAL_CHANNELS:
        assert by_channel[channel].status is ObservationStatus.MISSING
        assert by_channel[channel].value is None
    assert RejectionCode.DEVICE_REPORTED_MISSING.value in rejection_codes(
        result.report
    )


def test_a_robot_fault_state_maps_to_the_health_channel(kit):
    _, by_channel = convert_one(kit, robots=(robot_sample(health="failed"),))
    assert by_channel[f"robot.{ROBOT}.health"].value == "failed"


def test_an_unknown_activity_fails_closed_instead_of_reaching_assembly(kit):
    result, by_channel = convert_one(
        kit, robots=(robot_sample(activity="teleporting"),)
    )
    assert by_channel[ACTIVITY_CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.UNKNOWN_VALUE.value in rejection_codes(result.report)


def test_payload_maps_and_negative_payload_fails_closed(kit):
    _, ok = convert_one(kit, robots=(robot_sample(payload_balls=250),))
    assert ok[PAYLOAD_CHANNEL].value == 250
    result, bad = convert_one(kit, robots=(robot_sample(payload_balls=-1),))
    assert bad[PAYLOAD_CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.VALUE_OUT_OF_RANGE.value in rejection_codes(result.report)


def test_location_maps_and_an_undeclared_location_fails_closed(kit):
    _, ok = convert_one(kit, robots=(robot_sample(location=f"zone:{ZONE_ID}"),))
    assert ok[LOCATION_CHANNEL].value == f"zone:{ZONE_ID}"
    result, bad = convert_one(kit, robots=(robot_sample(location="the-moon"),))
    assert bad[LOCATION_CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.UNKNOWN_VALUE.value in rejection_codes(result.report)


def test_blank_assignment_is_a_legal_empty_string(kit):
    _, by_channel = convert_one(kit, robots=(robot_sample(assigned_zone=""),))
    assert by_channel[ZONE_CHANNEL].value == ""
    assert by_channel[ZONE_CHANNEL].status is ObservationStatus.OK


def test_metric_position_has_no_canonical_channel(kit):
    result, by_channel = convert_one(kit, robots=(robot_sample(),))
    assert not any(channel.endswith(".position") for channel in by_channel)
    assert "position" in {item.raw_field for item in result.report.unmapped}


def test_a_mismatched_coordinate_frame_is_rejected(kit):
    result, _ = convert_one(
        kit, robots=(robot_sample(coordinate_frame="EPSG:4326"),)
    )
    assert RejectionCode.COORDINATE_FRAME_MISMATCH.value in rejection_codes(
        result.report
    )


def test_a_fault_code_is_reported_as_unmapped(kit):
    result, _ = convert_one(kit, robots=(robot_sample(fault_code="F-17"),))
    assert "fault_code" in {item.raw_field for item in result.report.unmapped}


def test_estop_is_observed_but_never_actionable(kit):
    _, by_channel = convert_one(kit, robots=(robot_sample(estop_latched=True),))
    observation = by_channel[ESTOP_CHANNEL]
    assert observation.value is True
    assert observation.status is ObservationStatus.OK
    # Telemetry only: the adapter exposes nothing that could clear or set it.
    for name in dir(RobotStatusAdapter):
        assert "estop" not in name.lower() or name.startswith("__")
        assert "reset" not in name.lower()
        assert "command" not in name.lower()


def test_the_adapter_infers_no_capability_eta_yield_or_permission(kit):
    result, by_channel = convert_one(kit, robots=(robot_sample(),))
    forbidden = ("eta", "yield", "capability", "permission", "washer_available")
    for channel in by_channel:
        assert not any(token in channel for token in forbidden)
    assert set(valued_channels(result)) == CANONICAL_CHANNELS


def test_a_robot_sample_rejects_non_boolean_flags():
    with pytest.raises(EdgeAdapterError):
        robot_sample(estop_latched="yes")
    with pytest.raises(EdgeAdapterError):
        robot_sample(payload_balls=1.5)


def test_a_profile_requires_declared_vocabularies():
    base = {
        "sensor_id": "fleet-R1",
        "calibration_id": "calibration:not-required",
        "stale_after_s": 30.0,
        "provenance": "synthetic test constant",
        "robot_id": "R1",
        "battery_unit": "percent",
        "allowed_activities": ("idle",),
        "allowed_health": ("ok",),
        "allowed_locations": ("charger",),
        "allowed_zones": ("Z1",),
    }
    for changes in (
        {"battery_unit": "millivolts"},
        {"allowed_activities": ()},
        {"allowed_health": ("ok", "ok")},
        {"allowed_locations": ("",)},
    ):
        with pytest.raises(EdgeAdapterError):
            RobotStatusProfile(**{**base, **changes})


def test_a_future_heartbeat_fails_closed(kit):
    future = RawSampleTiming(
        sample_timestamp_s=FRAME_T_S + 60.0,
        available_timestamp_s=FRAME_T_S + 60.0,
    )
    result, by_channel = convert_one(kit, robots=(robot_sample(timing=future),))
    assert by_channel[ACTIVITY_CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.FUTURE_SAMPLE.value in rejection_codes(result.report)
