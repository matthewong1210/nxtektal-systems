"""Digital-I/O adapter: polarity, faults, impossible states, no write path."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nxt_edge_observation import (
    DigitalInputSample,
    EdgeAdapterError,
    RawSampleTiming,
    RejectionCode,
)
from nxt_edge_observation import adapters as adapters_module
from nxt_telemetry.observations import ObservationStatus

from scripts.pilot_course_a_edge_fixture import (
    DIGITAL_DEVICE_ID,
    SENSOR_STATION_DOCKED,
    SENSOR_STATION_OPEN,
    SENSOR_ZONE_OPEN,
    STATION_ID,
    ZONE_ID,
)

from .conftest import (
    FRAME_T_S,
    convert_one,
    valued_channels,
    digital_snapshot,
    rejection_codes,
    timing,
)

STATION_OPEN_CHANNEL = f"station.{STATION_ID}.is_open"
STATION_DOCKED_CHANNEL = f"station.{STATION_ID}.docked"
ZONE_OPEN_CHANNEL = f"zone.{ZONE_ID}.is_open"


def _input(sensor_id: str, input_name: str, raw_state):
    return DigitalInputSample(
        sensor_id=sensor_id, input_name=input_name, raw_state=raw_state
    )


def _snapshot(*inputs, **changes):
    return digital_snapshot(tuple(inputs), **changes)


def test_active_high_input_maps_straight_through(kit):
    _, by_channel = convert_one(
        kit,
        digital_io=(
            _snapshot(_input(SENSOR_STATION_OPEN, "equipment_ready", True)),
        ),
    )
    observation = by_channel[STATION_OPEN_CHANNEL]
    assert observation.value is True
    assert observation.status is ObservationStatus.OK


def test_active_high_false_is_a_real_false_not_a_gap(kit):
    _, by_channel = convert_one(
        kit,
        digital_io=(
            _snapshot(_input(SENSOR_STATION_OPEN, "equipment_ready", False)),
        ),
    )
    assert by_channel[STATION_OPEN_CHANNEL].value is False
    assert by_channel[STATION_OPEN_CHANNEL].status is ObservationStatus.OK


def test_active_low_input_is_inverted_by_declared_polarity(kit):
    """The docked switch is declared active-low, so True reads as absent."""
    _, asserted = convert_one(
        kit,
        digital_io=(
            _snapshot(_input(SENSOR_STATION_DOCKED, "handoff_docked", True)),
        ),
    )
    _, released = convert_one(
        kit,
        digital_io=(
            _snapshot(_input(SENSOR_STATION_DOCKED, "handoff_docked", False)),
        ),
    )
    assert asserted[STATION_DOCKED_CHANNEL].value == 0
    assert released[STATION_DOCKED_CHANNEL].value == 1


def test_a_count_channel_receives_an_integer_not_a_bool(kit):
    _, by_channel = convert_one(
        kit,
        digital_io=(
            _snapshot(_input(SENSOR_STATION_DOCKED, "handoff_docked", False)),
        ),
    )
    value = by_channel[STATION_DOCKED_CHANNEL].value
    assert type(value) is int


def test_missing_input_state_becomes_explicit_missing(kit):
    result, by_channel = convert_one(
        kit,
        digital_io=(
            _snapshot(_input(SENSOR_ZONE_OPEN, "zone_gate_open", None)),
        ),
    )
    assert by_channel[ZONE_OPEN_CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.DEVICE_REPORTED_MISSING.value in rejection_codes(
        result.report
    )


def test_device_fault_makes_every_bound_point_missing(kit):
    result, by_channel = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input(SENSOR_STATION_OPEN, "equipment_ready", True),
                _input(SENSOR_ZONE_OPEN, "zone_gate_open", True),
                device_status="fault",
            ),
        ),
    )
    for channel in (STATION_OPEN_CHANNEL, ZONE_OPEN_CHANNEL):
        assert by_channel[channel].status is ObservationStatus.MISSING
    assert RejectionCode.DEVICE_FAULT.value in rejection_codes(result.report)


def test_stale_snapshot_marks_points_stale(kit):
    _, by_channel = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input(SENSOR_STATION_OPEN, "equipment_ready", True),
                timing=timing(age_s=600.0),
            ),
        ),
    )
    assert by_channel[STATION_OPEN_CHANNEL].status is ObservationStatus.STALE


def test_declared_raw_only_inputs_are_reported_not_dropped(kit):
    result, _ = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input("di-washer-run", "washer_running", True),
                _input("di-washer-fault", "washer_fault", False),
                _input("di-basket", "basket_present", True),
            ),
        ),
    )
    # Raw-only points name no canonical channel, so nothing is valued.
    assert valued_channels(result) == {}
    assert {item.raw_field for item in result.report.unmapped} == {
        "washer_running",
        "washer_fault",
        "basket_present",
    }
    # The only rejections are the silent-device gaps, never a raw-only point.
    assert {item.code for item in result.report.rejected} == {
        RejectionCode.NO_SAMPLE
    }


def test_an_undeclared_unknown_bit_is_rejected(kit):
    result, _ = convert_one(
        kit,
        digital_io=(_snapshot(_input("di-mystery", "spare_bit_7", True)),),
    )
    assert RejectionCode.NO_BINDING.value in rejection_codes(result.report)
    assert not result.report.unmapped


def test_mutually_exclusive_limits_are_an_impossible_state(kit):
    result, _ = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input("di-lift-upper", "lift_upper_limit", True),
                _input("di-lift-lower", "lift_lower_limit", True),
            ),
        ),
    )
    assert RejectionCode.IMPOSSIBLE_STATE.value in rejection_codes(result.report)
    impossible = [
        item.raw_field
        for item in result.report.rejected
        if item.code is RejectionCode.IMPOSSIBLE_STATE
    ]
    assert sorted(impossible) == ["lift_lower_limit", "lift_upper_limit"]


def test_a_duplicate_binding_within_one_snapshot_fails_closed(kit):
    result, by_channel = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input(SENSOR_STATION_OPEN, "equipment_ready", True),
                _input(SENSOR_STATION_OPEN, "equipment_ready_b", False),
            ),
        ),
    )
    assert by_channel[STATION_OPEN_CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.DUPLICATE_CHANNEL.value in rejection_codes(result.report)


def test_a_snapshot_cannot_repeat_one_input_name():
    with pytest.raises(EdgeAdapterError):
        _snapshot(
            _input(SENSOR_STATION_OPEN, "equipment_ready", True),
            _input(SENSOR_ZONE_OPEN, "equipment_ready", True),
        )


def test_a_mismatched_device_identity_is_rejected(kit):
    """`by_channel` is never empty now, so assert the converted value itself.

    Silent-device reconciliation puts every commissioned channel in the
    frame, so a bare truthiness check on `by_channel` would pass even when
    the device was rejected outright.
    """
    _, matched = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input(SENSOR_STATION_OPEN, "equipment_ready", True),
                device_id=DIGITAL_DEVICE_ID,
            ),
        ),
    )
    assert matched[STATION_OPEN_CHANNEL].value is True
    assert matched[STATION_OPEN_CHANNEL].status is ObservationStatus.OK

    result, unknown = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input(SENSOR_STATION_OPEN, "equipment_ready", True),
                device_id="io-not-declared",
            ),
        ),
    )
    assert RejectionCode.UNKNOWN_SOURCE.value in rejection_codes(result.report)
    # The undeclared device produces no value; the channel is an explicit gap.
    assert unknown[STATION_OPEN_CHANNEL].status is ObservationStatus.MISSING
    assert unknown[STATION_OPEN_CHANNEL].value is None


@pytest.mark.parametrize("state", ["1", 1, "true", "", 0])
def test_non_boolean_states_are_refused_at_the_contract(state):
    with pytest.raises(EdgeAdapterError):
        DigitalInputSample(
            sensor_id=SENSOR_ZONE_OPEN, input_name="zone_gate_open", raw_state=state
        )


def test_an_estop_observation_is_evidence_only_and_influences_nothing(kit):
    """A remote I/O point can never act as a safety authority.

    The adapter maps every point one-to-one to its own commissioned
    binding, so no other channel's value can be derived from an
    emergency-stop input, and no code path can command or clear one.
    """
    _, latched = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input(SENSOR_STATION_OPEN, "equipment_ready", True),
                _input("di-estop-observed", "estop_observed", True),
            ),
        ),
    )
    _, clear = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input(SENSOR_STATION_OPEN, "equipment_ready", True),
                _input("di-estop-observed", "estop_observed", False),
            ),
        ),
    )
    assert latched[STATION_OPEN_CHANNEL].value is True
    assert clear[STATION_OPEN_CHANNEL].value is True
    assert latched[STATION_OPEN_CHANNEL].to_dict() == (
        clear[STATION_OPEN_CHANNEL].to_dict()
    )


def test_the_adapter_module_defines_no_write_or_output_surface():
    source = Path(adapters_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden_fragments = (
        "write",
        "send",
        "command",
        "actuate",
        "set_output",
        "coil",
        "gpio",
        "publish",
        "dispatch",
    )
    offenders = [
        name
        for name in names
        for fragment in forbidden_fragments
        if fragment in name.lower()
    ]
    assert offenders == [], offenders


def test_timing_contract_rejects_impossible_windows():
    with pytest.raises(EdgeAdapterError):
        RawSampleTiming(
            sample_timestamp_s=-1.0, available_timestamp_s=FRAME_T_S
        )


def test_diagnostic_precedence_names_the_most_fundamental_cause(kit):
    """A faulted device is reported as a fault, not as a downstream symptom.

    Every branch still yields MISSING, so precedence never changes whether
    the reading is trusted -- only which cause the adapter report names.
    """
    result, by_channel = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input("di-lift-upper", "lift_upper_limit", True),
                _input("di-lift-lower", "lift_lower_limit", True),
                _input(SENSOR_STATION_OPEN, "equipment_ready", True),
                device_status="fault",
            ),
        ),
    )
    assert by_channel[STATION_OPEN_CHANNEL].status is ObservationStatus.MISSING
    bound = [
        item
        for item in result.report.rejected
        if item.sensor_id == SENSOR_STATION_OPEN
    ]
    assert [item.code for item in bound] == [RejectionCode.DEVICE_FAULT]
    # The impossible combination is still reported on its own raw points.
    assert RejectionCode.IMPOSSIBLE_STATE.value in rejection_codes(result.report)


def test_a_future_timestamp_outranks_a_device_fault(kit):
    future = RawSampleTiming(
        sample_timestamp_s=FRAME_T_S + 30.0,
        available_timestamp_s=FRAME_T_S + 30.0,
    )
    result, by_channel = convert_one(
        kit,
        digital_io=(
            _snapshot(
                _input(SENSOR_STATION_OPEN, "equipment_ready", True),
                timing=future,
                device_status="fault",
            ),
        ),
    )
    assert by_channel[STATION_OPEN_CHANNEL].status is ObservationStatus.MISSING
    assert RejectionCode.FUTURE_SAMPLE.value in rejection_codes(result.report)
