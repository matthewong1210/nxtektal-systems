"""Load-cell diagnostic mode composes existing edge-observation contracts.

The diagnostic path is genuine transport-to-Observation evidence.  It is not a
complete FacilityState and must not become a second adapter or assembler.
"""

from __future__ import annotations

import json

import pytest

from nxt_edge_observation import (
    ConversionResult,
    EdgeAdapterReport,
    EdgeObservationAdapterKit,
    LoadCellSample,
    RawSampleBatch,
    RawSampleTiming,
    RejectionCode,
)
from nxt_telemetry.observations import Observation, ObservationStatus, SourceType

from scripts.edge_gateway_live_input_v0 import (
    GatewayConfig,
    GatewayMode,
    GatewayProcessor,
    ProcessingKind,
    diagnose_load_cell_samples,
)
from scripts.pilot_course_a_edge_fixture import (
    CALIBRATION_ID_LOAD_CELL,
    DEPLOYMENT_ID,
    SENSOR_DISPENSER_COUNT,
    SITE_ID,
    SYNTHETIC_DISPENSER_TARE_KG,
    SYNTHETIC_MASS_PER_BALL_KG,
    commissioned_site,
)

DEVICE_ID = "loadcell-controller-01"
GATEWAY_ID = "gw-pilot-a-01"
TOPIC = f"nxt/v1/sites/{SITE_ID}/devices/{DEVICE_ID}/load-cell"
FRAME_T_S = 63_000.0
DISPENSER_CHANNEL = "inventory.dispenser.count"


def _config_mapping(tmp_path, mode: str = "LOAD_CELL_DIAGNOSTIC") -> dict:
    return {
        "edge_gateway": {
            "schema": "nxt-edge-gateway/config/v0",
            "mode": mode,
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
                        "sensor-lc-dispenser-sensed",
                    ],
                }
            ],
            "status": {"host": "127.0.0.1", "port": 0},
            "evidence_dir": str(tmp_path),
            "fixture_cycle_index": 0,
        }
    }


def _raw_mass(ball_count: int = 6000) -> float:
    return SYNTHETIC_DISPENSER_TARE_KG + (
        ball_count * SYNTHETIC_MASS_PER_BALL_KG
    )


def _wire_payload(**changes: object) -> bytes:
    payload = {
        "schema": "nxt.edge.load-cell.raw/v1",
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "gateway_id": GATEWAY_ID,
        "device_id": DEVICE_ID,
        "sensor_id": SENSOR_DISPENSER_COUNT,
        "boot_id": "boot-20260808-001",
        "device_sequence": 1842,
        "sampled_at_utc": "2026-08-08T09:29:55.000Z",
        "published_at_utc": "2026-08-08T09:30:00.000Z",
        "raw_value": _raw_mass(),
        "raw_unit": "kg",
        "device_status": "ok",
        "calibration_id": CALIBRATION_ID_LOAD_CELL,
        "diagnostic_code": None,
    }
    payload.update(changes)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _sample(**changes: object) -> LoadCellSample:
    values = {
        "sensor_id": SENSOR_DISPENSER_COUNT,
        "timing": RawSampleTiming(
            sample_timestamp_s=FRAME_T_S - 5.0,
            available_timestamp_s=FRAME_T_S,
        ),
        "raw_value": _raw_mass(),
        "raw_unit": "kg",
        "device_status": "ok",
        "calibration_id": CALIBRATION_ID_LOAD_CELL,
        "diagnostic_code": None,
    }
    values.update(changes)
    return LoadCellSample(**values)


def _target(result: ConversionResult) -> Observation:
    return next(
        item for item in result.observations if item.channel == DISPENSER_CHANNEL
    )


def test_diagnostic_process_emits_canonical_sensor_evidence_not_facility_state(
    tmp_path,
):
    config = GatewayConfig.from_mapping(_config_mapping(tmp_path))
    processor = GatewayProcessor(config, site=commissioned_site())

    result = processor.process_message(TOPIC, _wire_payload())

    assert result.kind is ProcessingKind.ACCEPTED
    assert result.mode is GatewayMode.LOAD_CELL_DIAGNOSTIC
    assert result.complete_facility_state is False
    assert result.runtime_outcome is None
    assert isinstance(result.adapter_report, EdgeAdapterReport)
    target = next(
        item for item in result.observations if item.channel == DISPENSER_CHANNEL
    )
    assert type(target) is Observation
    assert target.value == 6000
    assert target.status is ObservationStatus.OK
    assert target.source_type is SourceType.SENSOR
    assert target.source_id == SENSOR_DISPENSER_COUNT
    assert target.calibration_id == CALIBRATION_ID_LOAD_CELL
    assert DISPENSER_CHANNEL in {
        item.channel for item in result.adapter_report.accepted
    }

    payload = result.to_dict()
    assert payload["complete_facility_state"] is False
    assert "facility_state" not in payload
    assert "complete" in result.disclaimer.lower()
    assert "facility" in result.disclaimer.lower()


def test_diagnostic_helper_builds_existing_raw_batch_and_calls_existing_adapter(
    monkeypatch,
):
    seen: list[RawSampleBatch] = []
    original_convert = EdgeObservationAdapterKit.convert

    def capture_batch(self, batch):
        assert type(batch) is RawSampleBatch
        seen.append(batch)
        return original_convert(self, batch)

    monkeypatch.setattr(EdgeObservationAdapterKit, "convert", capture_batch)

    result = diagnose_load_cell_samples(
        commissioned_site(),
        (_sample(),),
        frame_t_s=FRAME_T_S,
        cycle_index=7,
    )

    assert type(result) is ConversionResult
    assert len(seen) == 1
    assert seen[0].load_cells == (_sample(),)
    assert seen[0].frame_t_s == FRAME_T_S
    assert seen[0].cycle_index == 7
    assert _target(result).observation_id == f"{DISPENSER_CHANNEL}:o000007"
    assert type(result.report) is EdgeAdapterReport


@pytest.mark.parametrize(
    ("changes", "expected_status", "expected_code", "keeps_value"),
    [
        (
            {"calibration_id": "CAL-WRONG-0001"},
            ObservationStatus.MISSING,
            RejectionCode.CALIBRATION_MISMATCH,
            False,
        ),
        (
            {"raw_unit": "lb"},
            ObservationStatus.MISSING,
            RejectionCode.UNSUPPORTED_UNIT,
            False,
        ),
        (
            {"raw_value": None},
            ObservationStatus.MISSING,
            RejectionCode.DEVICE_REPORTED_MISSING,
            False,
        ),
        (
            {"device_status": "fault"},
            ObservationStatus.MISSING,
            RejectionCode.DEVICE_FAULT,
            False,
        ),
        (
            {
                "timing": RawSampleTiming(
                    sample_timestamp_s=FRAME_T_S - 120.0,
                    available_timestamp_s=FRAME_T_S - 115.0,
                )
            },
            ObservationStatus.STALE,
            None,
            True,
        ),
    ],
)
def test_diagnostic_preserves_adapter_calibration_unit_missing_fault_and_stale_honesty(
    changes,
    expected_status,
    expected_code,
    keeps_value,
):
    result = diagnose_load_cell_samples(
        commissioned_site(),
        (_sample(**changes),),
        frame_t_s=FRAME_T_S,
        cycle_index=0,
    )

    observation = _target(result)
    assert observation.status is expected_status
    if keeps_value:
        assert observation.value == 6000
        assert observation.confidence == 0.5
    else:
        assert observation.value is None
        assert observation.value != 0
        assert observation.confidence == 0.0
    # The existing kit also reconciles every other commissioned channel as a
    # named no-sample gap.  This assertion is about the target live channel,
    # not those honest diagnostic gaps.
    codes = {
        item.code
        for item in result.report.rejected
        if item.channel == DISPENSER_CHANNEL
    }
    if expected_code is None:
        assert not codes
    else:
        assert expected_code in codes


def test_diagnostic_duplicate_canonical_channel_claim_fails_closed():
    result = diagnose_load_cell_samples(
        commissioned_site(),
        (_sample(), _sample(raw_value=_raw_mass(2400))),
        frame_t_s=FRAME_T_S,
        cycle_index=0,
    )

    observation = _target(result)
    assert observation.status is ObservationStatus.MISSING
    assert observation.value is None
    assert RejectionCode.DUPLICATE_CHANNEL in {
        item.code for item in result.report.rejected
    }
    assert DISPENSER_CHANNEL not in {
        item.channel for item in result.report.accepted
    }
