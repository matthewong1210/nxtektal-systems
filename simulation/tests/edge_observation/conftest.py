"""Shared fixtures for the Edge Observation Adapter Kit V0 tests.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The tests deliberately drive the *real* commissioning seam: bindings come
from ``project_telemetry_adapter_config`` over the validated synthetic
Pilot Course A manifest, so a change to the commissioned channel
vocabulary, canonical units, or calibration contract surfaces here rather
than in production.
"""

from __future__ import annotations

import dataclasses

import pytest

from nxt_edge_observation import (
    DigitalInputSample,
    DigitalIOSnapshot,
    LoadCellSample,
    RawSampleBatch,
    RawSampleTiming,
    RobotStatusSample,
)

from scripts.pilot_course_a_edge_fixture import (
    ACQUISITION_LAG_S,
    CALIBRATION_ID_LOAD_CELL,
    COORDINATE_FRAME,
    DIGITAL_DEVICE_ID,
    PILOT_CYCLES,
    ROBOT_IDS,
    SENSOR_DISPENSER_COUNT,
    SENSOR_STATION_DOCKED,
    SENSOR_STATION_OPEN,
    SENSOR_ZONE_OPEN,
    SYNTHETIC_DISPENSER_TARE_KG,
    SYNTHETIC_MASS_PER_BALL_KG,
    adapter_kit,
    commissioned_site,
    raw_batch,
)

FRAME_T_S = PILOT_CYCLES[0].t_s


@pytest.fixture(scope="session")
def site():
    return commissioned_site()


@pytest.fixture(scope="session")
def kit(site):
    return adapter_kit(site)


@pytest.fixture(scope="session")
def bindings(kit):
    return kit.bindings


@pytest.fixture
def nominal_batch():
    return raw_batch(PILOT_CYCLES[0])


def timing(*, age_s: float = ACQUISITION_LAG_S, t_s: float = FRAME_T_S):
    return RawSampleTiming(
        sample_timestamp_s=t_s - age_s, available_timestamp_s=t_s
    )


def dispenser_mass_kg(balls: int) -> float:
    return SYNTHETIC_DISPENSER_TARE_KG + balls * SYNTHETIC_MASS_PER_BALL_KG


def load_cell_sample(**changes) -> LoadCellSample:
    """A valid dispenser-count load-cell sample with targeted overrides."""
    values = {
        "sensor_id": SENSOR_DISPENSER_COUNT,
        "timing": timing(),
        "raw_value": dispenser_mass_kg(6000),
        "raw_unit": "kg",
        "device_status": "ok",
        "calibration_id": CALIBRATION_ID_LOAD_CELL,
        "diagnostic_code": None,
    }
    values.update(changes)
    return LoadCellSample(**values)


def digital_snapshot(inputs: tuple[DigitalInputSample, ...], **changes):
    values = {
        "device_id": DIGITAL_DEVICE_ID,
        "timing": timing(),
        "device_status": "ok",
        "inputs": inputs,
    }
    values.update(changes)
    return DigitalIOSnapshot(**values)


def robot_sample(**changes) -> RobotStatusSample:
    """A valid robot status report with targeted overrides."""
    values = {
        "robot_id": ROBOT_IDS[0],
        "timing": timing(),
        "device_status": "ok",
        "battery": 82.0,
        "payload_balls": 100,
        "activity": "idle",
        "health": "ok",
        "location": "charger",
        "destination": "",
        "assigned_zone": "",
        "estop_latched": False,
        "awaiting_human": False,
        "fault_code": None,
        "position_x_m": 12.0,
        "position_y_m": 4.0,
        "coordinate_frame": COORDINATE_FRAME,
    }
    values.update(changes)
    return RobotStatusSample(**values)


def batch(**changes) -> RawSampleBatch:
    values = {
        "cycle_index": 0,
        "frame_t_s": FRAME_T_S,
        "load_cells": (),
        "digital_io": (),
        "robots": (),
    }
    values.update(changes)
    return RawSampleBatch(**values)


def convert_one(kit, **changes):
    """Convert a single-family batch and return ``(result, by_channel)``."""
    result = kit.convert(batch(**changes))
    return result, {item.channel: item for item in result.observations}


def rejection_codes(report) -> set[str]:
    return {item.code.value for item in report.rejected}


def replace_input(
    snapshot: DigitalIOSnapshot, input_name: str, **changes
) -> DigitalIOSnapshot:
    inputs = tuple(
        dataclasses.replace(item, **changes) if item.input_name == input_name
        else item
        for item in snapshot.inputs
    )
    return dataclasses.replace(snapshot, inputs=inputs)


__all__ = [
    "FRAME_T_S",
    "batch",
    "convert_one",
    "digital_snapshot",
    "dispenser_mass_kg",
    "load_cell_sample",
    "rejection_codes",
    "replace_input",
    "robot_sample",
    "timing",
]
