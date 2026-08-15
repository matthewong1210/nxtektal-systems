"""Load-cell adapter: calibration, units, ranges, staleness, and honesty."""

from __future__ import annotations

import math

import pytest

from nxt_edge_observation import (
    EdgeAdapterError,
    LoadCellProfile,
    RawSampleTiming,
    RejectionCode,
)
from nxt_telemetry.observations import ObservationStatus, SourceType

from scripts.pilot_course_a_edge_fixture import (
    CALIBRATION_ID_LOAD_CELL,
    DISPENSER_CAPACITY_BALLS,
    SENSOR_DISPENSER_COUNT,
    SENSOR_DISPENSER_SENSED,
)

from .conftest import (
    FRAME_T_S,
    convert_one,
    dispenser_mass_kg,
    load_cell_sample,
    rejection_codes,
    timing,
)

CHANNEL = "inventory.dispenser.count"
SENSED_CHANNEL = "inventory.dispenser.sensed"


def test_valid_calibration_produces_an_ok_count(kit):
    result, by_channel = convert_one(kit, load_cells=(load_cell_sample(),))
    observation = by_channel[CHANNEL]
    assert observation.value == 6000
    assert observation.status is ObservationStatus.OK
    assert observation.confidence == 1.0
    assert observation.source_type is SourceType.SENSOR
    assert observation.source_id == SENSOR_DISPENSER_COUNT
    assert observation.calibration_id == CALIBRATION_ID_LOAD_CELL
    assert not result.report.rejected


def test_sensed_channel_keeps_a_real_quantity(kit):
    _, by_channel = convert_one(
        kit, load_cells=(load_cell_sample(sensor_id=SENSOR_DISPENSER_SENSED),)
    )
    observation = by_channel[SENSED_CHANNEL]
    assert isinstance(observation.value, float)
    assert observation.value == pytest.approx(6000.0)


def test_zero_balls_is_a_real_measurement_not_a_gap(kit):
    _, by_channel = convert_one(
        kit, load_cells=(load_cell_sample(raw_value=dispenser_mass_kg(0)),)
    )
    observation = by_channel[CHANNEL]
    assert observation.value == 0
    assert observation.status is ObservationStatus.OK


def test_maximum_declared_capacity_is_accepted(kit):
    _, by_channel = convert_one(
        kit,
        load_cells=(
            load_cell_sample(
                raw_value=dispenser_mass_kg(DISPENSER_CAPACITY_BALLS)
            ),
        ),
    )
    assert by_channel[CHANNEL].value == DISPENSER_CAPACITY_BALLS


def test_above_capacity_is_rejected_as_out_of_range(kit):
    result, by_channel = convert_one(
        kit,
        load_cells=(
            load_cell_sample(
                raw_value=dispenser_mass_kg(DISPENSER_CAPACITY_BALLS + 1)
            ),
        ),
    )
    assert by_channel[CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.VALUE_OUT_OF_RANGE.value in rejection_codes(result.report)


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"calibration_id": None}, RejectionCode.CALIBRATION_MISSING),
        ({"calibration_id": "CAL-WRONG-0001"}, RejectionCode.CALIBRATION_MISMATCH),
        ({"raw_unit": "lb"}, RejectionCode.UNSUPPORTED_UNIT),
        ({"raw_value": float("nan")}, RejectionCode.NON_FINITE_VALUE),
        ({"raw_value": float("inf")}, RejectionCode.NON_FINITE_VALUE),
        ({"raw_value": True}, RejectionCode.NON_FINITE_VALUE),
        ({"raw_value": None}, RejectionCode.DEVICE_REPORTED_MISSING),
        ({"raw_value": 0.0}, RejectionCode.VALUE_OUT_OF_RANGE),
        ({"device_status": "fault"}, RejectionCode.DEVICE_FAULT),
        ({"device_status": "no_data"}, RejectionCode.DEVICE_REPORTED_MISSING),
        ({"device_status": "probably_fine"}, RejectionCode.UNKNOWN_VALUE),
    ],
)
def test_untrustworthy_readings_become_explicit_missing(kit, changes, expected):
    result, by_channel = convert_one(
        kit, load_cells=(load_cell_sample(**changes),)
    )
    observation = by_channel[CHANNEL]
    assert observation.status is ObservationStatus.MISSING
    assert observation.value is None
    assert observation.confidence == 0.0
    assert expected.value in rejection_codes(result.report)


def test_a_missing_reading_never_becomes_zero_inventory(kit):
    for changes in (
        {"raw_value": None},
        {"device_status": "fault"},
        {"calibration_id": None},
    ):
        _, by_channel = convert_one(
            kit, load_cells=(load_cell_sample(**changes),)
        )
        assert by_channel[CHANNEL].value is None, changes
        assert by_channel[CHANNEL].value != 0, changes


def test_negative_net_mass_is_rejected_not_clamped(kit):
    result, by_channel = convert_one(
        kit, load_cells=(load_cell_sample(raw_value=1.0),)
    )
    assert by_channel[CHANNEL].status is ObservationStatus.MISSING
    detail = " ".join(item.detail for item in result.report.rejected)
    assert "negative" in detail


def test_stale_reading_is_marked_stale_and_keeps_its_value(kit):
    result, by_channel = convert_one(
        kit, load_cells=(load_cell_sample(timing=timing(age_s=600.0)),)
    )
    observation = by_channel[CHANNEL]
    assert observation.status is ObservationStatus.STALE
    assert observation.value == 6000
    assert observation.confidence == 0.5
    assert not result.report.rejected


def test_a_sample_timestamped_in_the_future_fails_closed(kit):
    future = RawSampleTiming(
        sample_timestamp_s=FRAME_T_S + 30.0,
        available_timestamp_s=FRAME_T_S + 30.0,
    )
    result, by_channel = convert_one(
        kit, load_cells=(load_cell_sample(timing=future),)
    )
    assert by_channel[CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.FUTURE_SAMPLE.value in rejection_codes(result.report)


def test_a_reading_not_yet_available_fails_closed(kit):
    not_yet_visible = RawSampleTiming(
        sample_timestamp_s=FRAME_T_S - 5.0,
        available_timestamp_s=FRAME_T_S + 5.0,
    )
    result, by_channel = convert_one(
        kit, load_cells=(load_cell_sample(timing=not_yet_visible),)
    )
    assert by_channel[CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.INCONSISTENT_TIMESTAMPS.value in rejection_codes(
        result.report
    )


def test_availability_cannot_precede_sampling_at_all():
    with pytest.raises(EdgeAdapterError):
        RawSampleTiming(
            sample_timestamp_s=FRAME_T_S, available_timestamp_s=FRAME_T_S - 1.0
        )


def test_unknown_sensor_has_no_binding_and_emits_no_observation(kit):
    result, by_channel = convert_one(
        kit, load_cells=(load_cell_sample(sensor_id="sensor-unknown-99"),)
    )
    assert by_channel == {}
    assert RejectionCode.NO_BINDING.value in rejection_codes(result.report)


def test_diagnostic_code_is_reported_as_unmapped_not_dropped(kit):
    result, _ = convert_one(
        kit, load_cells=(load_cell_sample(diagnostic_code="E-0042"),)
    )
    assert [item.raw_field for item in result.report.unmapped] == [
        "diagnostic_code"
    ]


def test_observation_identity_is_deterministic(kit):
    first, first_channels = convert_one(
        kit, load_cells=(load_cell_sample(),), cycle_index=7
    )
    second, second_channels = convert_one(
        kit, load_cells=(load_cell_sample(),), cycle_index=7
    )
    assert first_channels[CHANNEL].observation_id == (
        second_channels[CHANNEL].observation_id
    )
    assert first_channels[CHANNEL].observation_id == (
        f"{CHANNEL}:o000007"
    )
    assert first.report.to_dict() == second.report.to_dict()
    assert [item.to_dict() for item in first.observations] == [
        item.to_dict() for item in second.observations
    ]


def test_conflicting_observations_for_one_channel_fail_closed(kit):
    result, by_channel = convert_one(
        kit,
        load_cells=(
            load_cell_sample(),
            load_cell_sample(raw_value=dispenser_mass_kg(1000)),
        ),
    )
    assert by_channel[CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.DUPLICATE_CHANNEL.value in rejection_codes(result.report)
    assert [item.channel for item in result.report.accepted].count(CHANNEL) == 0


def test_profile_rejects_impossible_coefficients():
    base = {
        "sensor_id": "s1",
        "calibration_id": "c1",
        "stale_after_s": 60.0,
        "provenance": "synthetic test constant",
        "raw_unit": "kg",
        "tare_raw": 1.0,
        "mass_per_ball_raw": 0.046,
        "capacity_balls": 10,
    }
    for changes in (
        {"mass_per_ball_raw": 0.0},
        {"mass_per_ball_raw": -1.0},
        {"capacity_balls": 0},
        {"capacity_balls": True},
        {"raw_unit": "stone"},
        {"stale_after_s": 0.0},
        {"tare_raw": math.nan},
        {"provenance": "  "},
    ):
        with pytest.raises(EdgeAdapterError):
            LoadCellProfile(**{**base, **changes})
