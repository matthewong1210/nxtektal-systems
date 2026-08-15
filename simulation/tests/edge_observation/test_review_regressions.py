"""Regressions for defects confirmed by the adversarial review.

Each test fails against the pre-review implementation and names the
concrete failing input the reviewer executed.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from nxt_edge_observation import (
    AdapterBindingSet,
    DigitalInputProfile,
    DigitalInputSample,
    DigitalIOSnapshot,
    EdgeObservationAdapterKit,
    RejectionCode,
)
from nxt_telemetry.observations import ObservationStatus

from scripts.pilot_course_a_edge_fixture import (
    COORDINATE_FRAME,
    DIGITAL_DEVICE_ID,
    NOT_REQUIRED_CALIBRATION_ID,
    ROBOT_IDS,
    SENSOR_STATION_OPEN,
    SENSOR_WASHER_WIP,
    SENSOR_ZONE_OPEN,
    STATION_ID,
    ZONE_ID,
    adapter_kit,
    commissioned_site,
    commissioned_site_payload,
)

from .conftest import (
    convert_one,
    dispenser_mass_kg,
    load_cell_sample,
    rejection_codes,
    robot_sample,
    timing,
    valued_channels,
)


# -- a single bit must never become a ball count -------------------------


def test_a_digital_bit_cannot_publish_a_ball_denominated_channel(kit):
    """Reviewer scenario A: a bit routed at the washer load-cell binding.

    Before the fix this emitted wash.washer.wip = 1, status ok,
    confidence 1.0, source_type sensor -- a fabricated physical fact.
    """
    result, by_channel = convert_one(
        kit,
        digital_io=(
            DigitalIOSnapshot(
                device_id=DIGITAL_DEVICE_ID,
                timing=timing(),
                device_status="ok",
                inputs=(
                    DigitalInputSample(
                        sensor_id=SENSOR_WASHER_WIP,
                        input_name="drum_occupied",
                        raw_state=True,
                    ),
                ),
            ),
        ),
    )
    observation = by_channel["wash.washer.wip"]
    assert observation.status is ObservationStatus.MISSING
    assert observation.value is None
    assert "wash.washer.wip" not in valued_channels(result)
    codes = {
        item.code
        for item in result.report.rejected
        if item.sensor_id == SENSOR_WASHER_WIP
    }
    assert codes & {
        RejectionCode.UNSUPPORTED_UNIT,
        RejectionCode.CALIBRATION_MISSING,
    }


def test_a_digital_bit_cannot_satisfy_a_calibrated_binding(kit):
    result, _ = convert_one(
        kit,
        digital_io=(
            DigitalIOSnapshot(
                device_id=DIGITAL_DEVICE_ID,
                timing=timing(),
                device_status="ok",
                inputs=(
                    DigitalInputSample(
                        sensor_id=SENSOR_WASHER_WIP,
                        input_name="drum_occupied",
                        raw_state=True,
                    ),
                ),
            ),
        ),
    )
    assert rejection_codes(result.report) & {
        RejectionCode.UNSUPPORTED_UNIT.value,
        RejectionCode.CALIBRATION_MISSING.value,
    }


# -- mutual exclusion must respect declared polarity ---------------------


def _kit_with_exclusive_boolean_pair(site, *, active_low: bool):
    """Two commissioned BOOLEAN points declared mutually exclusive."""
    original = adapter_kit(site)
    inputs = tuple(
        DigitalInputProfile(
            sensor_id=sensor_id,
            calibration_id=NOT_REQUIRED_CALIBRATION_ID,
            stale_after_s=60.0,
            provenance="synthetic regression constant",
            active_low=active_low,
        )
        for sensor_id in (SENSOR_STATION_OPEN, SENSOR_ZONE_OPEN)
    )
    device = dataclasses.replace(
        next(iter(original._digital_devices.values())),
        mutually_exclusive_inputs=(("gate_a", "gate_b"),),
    )
    return EdgeObservationAdapterKit(
        bindings=original.bindings,
        coordinate_frame=COORDINATE_FRAME,
        load_cell_profiles=tuple(original._load_cells.values()),
        digital_device_profiles=(device,),
        digital_input_profiles=inputs,
        robot_profiles=tuple(original._robots.values()),
    )


def _gate_snapshot(state_a: bool, state_b: bool) -> DigitalIOSnapshot:
    return DigitalIOSnapshot(
        device_id=DIGITAL_DEVICE_ID,
        timing=timing(),
        device_status="ok",
        inputs=(
            DigitalInputSample(
                sensor_id=SENSOR_STATION_OPEN,
                input_name="gate_a",
                raw_state=state_a,
            ),
            DigitalInputSample(
                sensor_id=SENSOR_ZONE_OPEN,
                input_name="gate_b",
                raw_state=state_b,
            ),
        ),
    )


def test_active_low_impossible_state_is_detected(site):
    """Both normally-closed points asserted reads as raw False, not True."""
    kit = _kit_with_exclusive_boolean_pair(site, active_low=True)
    result, by_channel = convert_one(kit, digital_io=(_gate_snapshot(False, False),))
    assert RejectionCode.IMPOSSIBLE_STATE.value in rejection_codes(result.report)
    for channel in (f"station.{STATION_ID}.is_open", f"zone.{ZONE_ID}.is_open"):
        assert by_channel[channel].status is ObservationStatus.MISSING


def test_active_low_normal_state_is_not_flagged_impossible(site):
    """Neither asserted is ordinary and must not be demoted."""
    kit = _kit_with_exclusive_boolean_pair(site, active_low=True)
    result, by_channel = convert_one(kit, digital_io=(_gate_snapshot(True, True),))
    assert RejectionCode.IMPOSSIBLE_STATE.value not in rejection_codes(
        result.report
    )
    for channel in (f"station.{STATION_ID}.is_open", f"zone.{ZONE_ID}.is_open"):
        assert by_channel[channel].status is ObservationStatus.OK
        assert by_channel[channel].value is False


def test_active_high_impossible_state_still_detected(site):
    kit = _kit_with_exclusive_boolean_pair(site, active_low=False)
    result, _ = convert_one(kit, digital_io=(_gate_snapshot(True, True),))
    assert RejectionCode.IMPOSSIBLE_STATE.value in rejection_codes(result.report)


# -- a huge finite reading must fail closed, not crash the batch ---------


def test_an_enormous_finite_reading_fails_closed_without_overflow(kit):
    """int(round(inf)) previously raised OverflowError out of convert()."""
    result, by_channel = convert_one(
        kit,
        load_cells=(
            load_cell_sample(raw_value=1.7976931348623157e308),
            load_cell_sample(
                sensor_id="sensor-lc-dispenser-sensed",
                raw_value=dispenser_mass_kg(6000),
            ),
        ),
    )
    assert by_channel["inventory.dispenser.count"].status is (
        ObservationStatus.MISSING
    )
    assert rejection_codes(result.report) & {
        RejectionCode.NON_FINITE_VALUE.value,
        RejectionCode.VALUE_OUT_OF_RANGE.value,
    }
    # The rest of the batch survives.
    assert by_channel["inventory.dispenser.sensed"].status is ObservationStatus.OK


# -- a device claiming calibration a binding denies ----------------------


def _kit_with_uncalibrated_dispenser(site_payload):
    """A manifest where the dispenser count binding needs no calibration.

    ``sensor_type="other"`` is inside commissioning's count-sensor set, so
    this manifest is genuinely valid and the not_required branch is
    reachable in production.
    """
    from nxt_commissioning import (
        CommissionedSite,
        project_telemetry_adapter_config,
    )

    payload = copy.deepcopy(site_payload)
    for binding in payload["sensor_bindings"]:
        if binding["channel"] == "inventory.dispenser.count":
            binding["sensor_type"] = "other"
            binding["calibration"] = {
                "status": "not_required",
                "calibration_id": None,
                "calibrated_at": None,
                "valid_until": None,
                "method": "declared uncalibrated counter",
                "provenance": copy.deepcopy(binding["provenance"]),
            }
    site = CommissionedSite.from_dict(payload)
    bindings = AdapterBindingSet.from_projection(
        project_telemetry_adapter_config(site)
    )
    original = adapter_kit(commissioned_site())
    return EdgeObservationAdapterKit(
        bindings=bindings,
        coordinate_frame=COORDINATE_FRAME,
        load_cell_profiles=tuple(
            dataclasses.replace(
                profile, calibration_id=NOT_REQUIRED_CALIBRATION_ID
            )
            if profile.sensor_id == "sensor-lc-dispenser-count"
            else profile
            for profile in original._load_cells.values()
        ),
        digital_device_profiles=tuple(original._digital_devices.values()),
        digital_input_profiles=tuple(original._digital_inputs.values()),
        robot_profiles=tuple(original._robots.values()),
    )


def test_a_calibration_claim_on_an_uncalibrated_binding_is_rejected():
    """The device asserts an identity the manifest says it does not have."""
    kit = _kit_with_uncalibrated_dispenser(commissioned_site_payload())
    result, by_channel = convert_one(
        kit,
        load_cells=(load_cell_sample(calibration_id="CAL-EXPIRED-2019"),),
    )
    observation = by_channel["inventory.dispenser.count"]
    assert observation.status is ObservationStatus.MISSING
    assert observation.value is None
    assert RejectionCode.CALIBRATION_MISMATCH.value in rejection_codes(
        result.report
    )


def test_an_uncalibrated_binding_accepts_a_sample_making_no_claim():
    kit = _kit_with_uncalibrated_dispenser(commissioned_site_payload())
    _, by_channel = convert_one(
        kit, load_cells=(load_cell_sample(calibration_id=None),)
    )
    observation = by_channel["inventory.dispenser.count"]
    assert observation.status is ObservationStatus.OK
    assert observation.value == 6000
    assert observation.calibration_id == NOT_REQUIRED_CALIBRATION_ID


# -- silent devices must publish an explicit gap -------------------------


def test_a_silent_device_publishes_missing_not_an_absent_key(kit, nominal_batch):
    """Reviewer probe 5: drop the dispenser load cell and robot R2."""
    reduced = dataclasses.replace(
        nominal_batch,
        load_cells=tuple(
            item
            for item in nominal_batch.load_cells
            if item.sensor_id != "sensor-lc-dispenser-count"
        ),
        robots=tuple(
            item for item in nominal_batch.robots if item.robot_id != ROBOT_IDS[1]
        ),
    )
    result = kit.convert(reduced)
    by_channel = {item.channel: item for item in result.observations}

    assert "inventory.dispenser.count" in by_channel
    assert by_channel["inventory.dispenser.count"].status is (
        ObservationStatus.MISSING
    )
    assert by_channel["inventory.dispenser.count"].value is None
    for field in ("health", "activity", "battery_frac", "estop_latched"):
        channel = f"robot.{ROBOT_IDS[1]}.{field}"
        assert by_channel[channel].status is ObservationStatus.MISSING
        assert by_channel[channel].value is None

    assert result.report.has_rejections
    silent = {
        item.sensor_id
        for item in result.report.rejected
        if item.code is RejectionCode.NO_SAMPLE
    }
    assert "sensor-lc-dispenser-count" in silent


def test_a_complete_batch_reports_no_silent_device(kit, nominal_batch):
    result = kit.convert(nominal_batch)
    assert not result.report.has_rejections
    assert len(valued_channels(result)) == len(kit.bindings.bindings)


def test_a_robot_with_no_profile_still_reports_every_channel_as_missing(kit):
    result, by_channel = convert_one(kit, robots=(robot_sample(robot_id="R99"),))
    for field in ("health", "activity", "battery_frac"):
        for robot_id in ROBOT_IDS:
            channel = f"robot.{robot_id}.{field}"
            assert by_channel[channel].status is ObservationStatus.MISSING


# -- the rejection taxonomy carries no unreachable members ---------------


def test_every_rejection_code_is_reachable_in_the_package():
    """A documented fail-closed code that no path can emit is a false claim."""
    import pathlib

    import nxt_edge_observation.adapters as adapters_module

    source = pathlib.Path(adapters_module.__file__).read_text(encoding="utf-8")
    unreachable = [
        code.name
        for code in RejectionCode
        if f"RejectionCode.{code.name}" not in source
    ]
    assert unreachable == [], unreachable
