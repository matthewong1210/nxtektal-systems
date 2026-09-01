"""Hybrid rehearsal replaces one channel and keeps every other fact simulated."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from nxt_agent_runtime import (
    AgentRuntime,
    CycleKind,
    CycleOutcome,
    EvaluationJournal,
    InMemoryEvaluationCheckpointStore,
    JsonlSnapshotPublisher,
)
from nxt_edge_observation import EdgeAdapterReport
from nxt_pilot_ops.ledger import JsonlEventLedger
from nxt_site_runtime import InMemoryCheckpointStore, SequencedObservationFrame
from nxt_telemetry.assemble import assemble_from_observations
from nxt_telemetry.observations import ObservationStatus, SourceType

from scripts.edge_gateway_live_input_v0 import (
    HYBRID_DISCLAIMER,
    GatewayConfig,
    GatewayError,
    GatewayErrorCode,
    GatewayMode,
    GatewayProcessor,
    HybridObservationSource,
    ProcessingKind,
    ValidatedWireDelivery,
)
from scripts.pilot_course_a_edge_fixture import (
    CALIBRATION_ID_LOAD_CELL,
    DEPLOYMENT_ID,
    PILOT_CYCLES,
    SENSOR_DISPENSER_COUNT,
    SITE_ID,
    SYNTHETIC_DISPENSER_TARE_KG,
    SYNTHETIC_MASS_PER_BALL_KG,
    commissioned_site,
    site_config,
)

DEVICE_ID = "loadcell-controller-01"
GATEWAY_ID = "gw-pilot-a-01"
TOPIC = f"nxt/v1/sites/{SITE_ID}/devices/{DEVICE_ID}/load-cell"
DISPENSER_CHANNEL = "inventory.dispenser.count"


def _config_mapping(tmp_path, mode: str = "HYBRID_RUNTIME_REHEARSAL") -> dict:
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


def _wire_payload(*, device_sequence: int = 1842, **changes: object) -> bytes:
    payload = {
        "schema": "nxt.edge.load-cell.raw/v1",
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "gateway_id": GATEWAY_ID,
        "device_id": DEVICE_ID,
        "sensor_id": SENSOR_DISPENSER_COUNT,
        "boot_id": "boot-20260808-001",
        "device_sequence": device_sequence,
        "sampled_at_utc": "2026-08-08T09:29:55.000Z",
        "published_at_utc": "2026-08-08T09:30:00.000Z",
        "raw_value": SYNTHETIC_DISPENSER_TARE_KG
        + 6000 * SYNTHETIC_MASS_PER_BALL_KG,
        "raw_unit": "kg",
        "device_status": "ok",
        "calibration_id": CALIBRATION_ID_LOAD_CELL,
        "diagnostic_code": None,
    }
    payload.update(changes)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _processor(tmp_path) -> GatewayProcessor:
    config = GatewayConfig.from_mapping(_config_mapping(tmp_path))
    return GatewayProcessor(config, site=commissioned_site())


def _delivery(processor: GatewayProcessor, **changes: object) -> ValidatedWireDelivery:
    delivery = processor.prepare_message(TOPIC, _wire_payload(**changes))
    assert type(delivery) is ValidatedWireDelivery
    return delivery


def _runtime(tmp_path, source: HybridObservationSource) -> AgentRuntime:
    site = commissioned_site()
    return AgentRuntime(
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_config=site_config(site),
        observation_source=source,
        publisher=JsonlSnapshotPublisher(tmp_path / "snapshots.jsonl"),
        ledger=JsonlEventLedger(tmp_path / "ledger.jsonl"),
        journal=EvaluationJournal(tmp_path / "evaluations.jsonl"),
        simulation_midnight=datetime(2026, 8, 8, tzinfo=ZoneInfo(site.timezone)),
        clean_sensed_valid=True,
        site_checkpoint_store=InMemoryCheckpointStore(),
        evaluation_checkpoint_store=InMemoryEvaluationCheckpointStore(),
    )


def test_hybrid_source_replaces_only_live_channel_and_labels_everything_else_simulated(
    tmp_path,
):
    site = commissioned_site()
    processor = _processor(tmp_path / "prepare")
    delivery = _delivery(processor)
    source = HybridObservationSource(site, fixture_spec=PILOT_CYCLES[0])

    source.stage(delivery)
    sequenced = source.observe()

    assert type(sequenced) is SequencedObservationFrame
    assert sequenced.sequence_number == 0
    assert delivery.device_sequence == 1842
    target = sequenced.frame.by_channel()[DISPENSER_CHANNEL]
    assert target.value == 6000
    assert target.status is ObservationStatus.OK
    assert target.source_type is SourceType.SENSOR
    assert target.source_id == SENSOR_DISPENSER_COUNT
    assert target.seq == sequenced.sequence_number
    assert target.observation_id == f"{DISPENSER_CHANNEL}:o000000"

    simulated = tuple(
        item
        for item in sequenced.frame.observations
        if item.channel != DISPENSER_CHANNEL
    )
    assert simulated
    assert all(item.source_type is SourceType.SIMULATION for item in simulated)
    assert all(item.source_id.startswith("synthetic.") for item in simulated)
    assert all(
        reference.source_type == "simulation"
        and reference.source_id.startswith("synthetic.")
        for reference in sequenced.upstream_source_references
    )
    assert type(source.pending_report) is EdgeAdapterReport
    assert "HYBRID" in HYBRID_DISCLAIMER
    assert "SIMULATION" in HYBRID_DISCLAIMER


def test_hybrid_source_double_peek_is_immutable_and_side_effect_free(tmp_path):
    processor = _processor(tmp_path / "prepare")
    source = HybridObservationSource(
        commissioned_site(), fixture_spec=PILOT_CYCLES[0]
    )
    source.stage(_delivery(processor))

    first = source.observe()
    report = source.pending_report
    second = source.observe()

    assert first == second
    assert first.frame.to_dict() == second.frame.to_dict()
    assert source.pending_report == report
    assert source.next_sequence == 0


def test_hybrid_source_reject_discards_delivery_and_reuses_site_sequence(tmp_path):
    processor = _processor(tmp_path / "prepare")
    source = HybridObservationSource(
        commissioned_site(), fixture_spec=PILOT_CYCLES[0]
    )
    source.stage(_delivery(processor))
    assert source.observe().sequence_number == 0

    source.reject(0, "insufficient_data_quality")
    assert source.next_sequence == 0
    source.stage(_delivery(processor, device_sequence=1843))

    assert source.observe().sequence_number == 0
    source.acknowledge(0)
    assert source.next_sequence == 1


def test_admitted_hybrid_frame_runs_existing_agent_runtime_and_keeps_exact_state_report(
    tmp_path,
):
    processor = _processor(tmp_path / "prepare")
    source = HybridObservationSource(
        commissioned_site(), fixture_spec=PILOT_CYCLES[0]
    )
    source.stage(_delivery(processor))
    staged = source.observe()
    expected_state, expected_report = assemble_from_observations(
        staged.frame,
        site_config(commissioned_site()),
        staged.upstream,
    )
    runtime_root = tmp_path / "runtime"
    runtime = _runtime(runtime_root, source)

    outcome = runtime.run_once()

    assert outcome.kind is CycleKind.EVALUATED
    assert outcome.record is not None
    assert outcome.envelope_id is not None
    assert outcome.evaluation_id is not None
    assert source.next_sequence == 1
    published = json.loads(
        (runtime_root / "snapshots.jsonl").read_text(encoding="utf-8").strip()
    )
    expected_state_json = json.loads(json.dumps(expected_state.to_dict()))
    expected_report_json = json.loads(json.dumps(expected_report.to_dict()))
    assert published["facility_state"] == expected_state_json
    assert published["assembly_report"] == expected_report_json


def test_adapter_rejected_hybrid_input_creates_no_policy_evaluation(tmp_path):
    processor = _processor(tmp_path / "gateway")

    result = processor.process_message(
        TOPIC,
        _wire_payload(calibration_id="CAL-WRONG-0001"),
    )

    assert result.kind is ProcessingKind.REJECTED
    assert result.mode is GatewayMode.HYBRID_RUNTIME_REHEARSAL
    assert result.runtime_outcome is not None
    assert result.runtime_outcome.kind is CycleKind.REJECTED
    assert result.runtime_outcome.record is None
    assert result.runtime_outcome.evaluation_id is None
    assert result.runtime_outcome.envelope_id is None
    assert result.complete_facility_state is False
    target = next(
        item for item in result.observations if item.channel == DISPENSER_CHANNEL
    )
    assert target.status is ObservationStatus.MISSING
    assert target.value is None
    assert target.value != 0


def test_hybrid_process_exposes_disclaimer_and_stable_content_ids(tmp_path):
    first = _processor(tmp_path / "first").process_message(TOPIC, _wire_payload())
    second = _processor(tmp_path / "second").process_message(TOPIC, _wire_payload())

    for result in (first, second):
        assert result.kind is ProcessingKind.ACCEPTED
        assert result.mode is GatewayMode.HYBRID_RUNTIME_REHEARSAL
        assert result.complete_facility_state is True
        assert result.disclaimer == HYBRID_DISCLAIMER
        assert result.runtime_outcome is not None
        assert result.runtime_outcome.kind is CycleKind.EVALUATED
    assert [item.to_dict() for item in first.observations] == [
        item.to_dict() for item in second.observations
    ]
    assert first.adapter_report.to_dict() == second.adapter_report.to_dict()
    assert first.runtime_outcome.envelope_id == second.runtime_outcome.envelope_id
    assert first.runtime_outcome.evaluation_id == second.runtime_outcome.evaluation_id
    transport_outcome = first.to_dict()["runtime_outcome"]
    assert "verdict" not in transport_outcome
    assert "recommendation_action" not in transport_outcome


def test_identical_wire_redelivery_is_duplicate_and_does_not_evaluate_again(
    tmp_path,
):
    processor = _processor(tmp_path / "gateway")
    admitted = processor.process_message(TOPIC, _wire_payload())

    duplicate = processor.process_message(TOPIC, _wire_payload())

    assert admitted.kind is ProcessingKind.ACCEPTED
    assert duplicate.kind is ProcessingKind.DUPLICATE
    assert duplicate.site_sequence is None
    assert duplicate.runtime_outcome is None
    assert duplicate.observations == ()


def test_deferred_runtime_redrives_exact_delivery_before_admitting_new_sequence(
    monkeypatch, tmp_path
):
    processor = _processor(tmp_path / "gateway")
    source = processor._hybrid_source
    assert source is not None

    class DeferredThenAcknowledged:
        calls = 0

        def run_once(self):
            self.calls += 1
            sequence = source.observe().sequence_number
            if self.calls == 1:
                return CycleOutcome(
                    kind=CycleKind.EVALUATION_DEFERRED,
                    sequence_number=sequence,
                    envelope_id="fse:deferred",
                    evaluation_id=None,
                    record=None,
                    failure=None,
                    acknowledged=False,
                )
            source.acknowledge(sequence)
            return CycleOutcome(
                kind=CycleKind.REPLAY_SKIPPED,
                sequence_number=sequence,
                envelope_id="fse:deferred",
                evaluation_id="aev:recovered",
                record=None,
                failure=None,
                acknowledged=True,
            )

    runtime = DeferredThenAcknowledged()
    monkeypatch.setattr(processor, "_make_runtime", lambda operating_day: runtime)

    deferred = processor.process_message(TOPIC, _wire_payload())

    assert deferred.kind is ProcessingKind.REJECTED
    assert deferred.runtime_outcome.kind is CycleKind.EVALUATION_DEFERRED
    assert source.pending_delivery is not None
    assert source.next_sequence == 0

    with pytest.raises(GatewayError) as exc_info:
        processor.process_message(TOPIC, _wire_payload(device_sequence=1843))
    assert exc_info.value.code is GatewayErrorCode.SOURCE_PROTOCOL

    redriven = processor.process_message(TOPIC, _wire_payload())

    assert redriven.kind is ProcessingKind.ACCEPTED
    assert redriven.runtime_outcome.kind is CycleKind.REPLAY_SKIPPED
    assert source.pending_delivery is None
    assert source.next_sequence == 1

    later = processor.process_message(
        TOPIC, _wire_payload(device_sequence=1843)
    )
    assert later.kind is ProcessingKind.ACCEPTED
    assert later.site_sequence == 1
    assert runtime.calls == 3
