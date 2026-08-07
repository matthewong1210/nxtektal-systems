"""Contract tests for nxt_telemetry.observations (pure, no simulator)."""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from nxt_telemetry.observations import (
    SENSED_FIELD_PREFIXES,
    Observation,
    ObservationFrame,
    ObservationStatus,
    SiteConfig,
    SourceType,
    UpstreamInputs,
    sensed_diff_paths,
)

TELEMETRY_PKG = Path(__file__).resolve().parents[2] / "nxt_telemetry"


def obs(
    channel: str = "inventory.dispenser.count",
    *,
    value=7200,
    seq: int = 0,
    status: ObservationStatus = ObservationStatus.OK,
    source_type: SourceType = SourceType.SIMULATION,
    confidence: float = 1.0,
    t: float = 21600.0,
) -> Observation:
    return Observation(
        channel=channel,
        value=value,
        sample_timestamp_s=t,
        available_timestamp_s=t,
        status=status,
        source_type=source_type,
        source_id="synthetic.loadcell",
        calibration_id="cal:placeholder:v0",
        confidence=confidence,
        seq=seq,
    )


def test_observation_is_frozen_with_derived_id():
    observation = obs(seq=41)
    assert observation.observation_id == "inventory.dispenser.count:o000041"
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.value = 0
    payload = observation.to_dict()
    assert payload["source_type"] == "simulation"
    assert payload["calibration_id"] == "cal:placeholder:v0"
    json.dumps(payload, sort_keys=True)


def test_observation_carries_founder_contract_fields():
    fields = {f.name for f in dataclasses.fields(Observation)}
    assert {
        "channel",
        "value",
        "sample_timestamp_s",
        "available_timestamp_s",
        "status",
        "source_type",
        "source_id",
        "calibration_id",
        "confidence",
    } <= fields


def test_missing_observation_is_explicit_with_none_value():
    missing = obs(value=None, status=ObservationStatus.MISSING, confidence=0.0)
    assert missing.value is None
    assert missing.to_dict()["status"] == "missing"
    with pytest.raises(ValueError, match="MISSING"):
        obs(value=None, status=ObservationStatus.OK)  # None value demands MISSING


def test_available_timestamp_cannot_precede_sample():
    with pytest.raises(ValueError, match="available"):
        Observation(
            channel="c",
            value=1,
            sample_timestamp_s=100.0,
            available_timestamp_s=90.0,
            status=ObservationStatus.OK,
            source_type=SourceType.SENSOR,
            source_id="s",
            calibration_id="cal",
            confidence=1.0,
            seq=0,
        )


def test_frame_sorts_by_channel_and_round_trips():
    frame = ObservationFrame(
        t_s=21600.0,
        observations=(obs("scan.zone.Z1.balls", value=120), obs("inventory.dispenser.count")),
    )
    channels = [o.channel for o in frame.observations]
    assert channels == sorted(channels)
    assert frame.by_channel()["scan.zone.Z1.balls"].value == 120
    a = json.dumps(frame.to_dict(), sort_keys=True)
    b = json.dumps(frame.to_dict(), sort_keys=True)
    assert a == b


def test_sensed_diff_paths_detects_nested_leaves():
    a = {"ball_flow": {"clean_available": 100, "on_field": {"Z1": 5}}, "meta": {"seed": 1}}
    b = {"ball_flow": {"clean_available": 90, "on_field": {"Z1": 7}}, "meta": {"seed": 1}}
    paths = sensed_diff_paths(a, b)
    assert paths == {"ball_flow.clean_available", "ball_flow.on_field.Z1"}
    # sensed prefixes cover ball-flow but never demand forecast or site statics
    assert any(p.startswith(pref) for p in paths for pref in SENSED_FIELD_PREFIXES)
    assert not any(pref.startswith("demand.forecast") for pref in SENSED_FIELD_PREFIXES)


def test_site_config_and_upstream_are_frozen_stdlib_dataclasses():
    site = SiteConfig(
        scenario_name="unit",
        seed=7,
        total_balls=8000,
        washer_throughput_bpm=40.0,
        washer_batch_size=200,
        charger_slots=2,
        staff_capacity=1,
        wet_ground_speed_multiplier=1.0,
        open_minute=360,
        close_minute=1320,
        forecast_bucket_minutes=30,
        zone_ids=("Z1",),
        station_ids=("H1",),
        robot_ids=("R1",),
        robot_payload_capacity=(("R1", 200),),
        station_buffer_capacity=(("H1", 1200),),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        site.total_balls = 0
    upstream = UpstreamInputs(
        forecast_balls_per_minute=(5.0, 5.0),
        demand_balls_total=10,
        demand_balls_served=10,
        stockout_minutes=0.0,
        service_availability=1.0,
    )
    json.dumps({**site.to_dict(), **upstream.to_dict()})


def test_contract_module_bans_sim_and_clock_imports():
    banned = {"simpy", "gymnasium", "numpy", "pandas", "nxt_range_ops", "nxt_sim",
              "time", "datetime", "uuid", "random", "secrets"}
    tree = ast.parse((TELEMETRY_PKG / "observations.py").read_text())
    for node in ast.walk(tree):
        roots = set()
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots = {node.module.split(".")[0]}
        assert not (roots & banned), f"observations.py imports banned: {roots & banned}"
