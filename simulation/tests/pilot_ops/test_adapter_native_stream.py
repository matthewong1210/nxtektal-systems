from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nxt_facility.build import build_facility_state
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario
from nxt_range_twin.stream import dump_json_line, write_jsonl

from nxt_pilot_ops.adapters import (
    NATIVE_STREAM_SCHEMA,
    AdapterError,
    read_native_facility_state_capture,
    read_native_facility_state_stream,
)


MIDNIGHT = datetime(2026, 8, 8, tzinfo=timezone.utc)


def _meta(*, n_records: int) -> dict:
    return {
        "schema": NATIVE_STREAM_SCHEMA,
        "site_id": "sim-baseline",
        "deployment_id": "dev",
        "episode_id": "normal_weekday-seed7",
        "scenario_name": "normal_weekday",
        "seed": 7,
        "policy": "inventory_threshold",
        "policy_version": "0",
        "control_interval_s": 60.0,
        "every_steps": 1,
        "n_records": n_records,
        "simulator_version": "0.5.0",
        "git_commit": "abc1234",
        "disclaimer": "placeholder-driven simulation output",
    }


def _records() -> list[dict]:
    scenario = make_scenario("normal_weekday")
    simulation = RangeSimulation(scenario, seed=7)
    first = build_facility_state(simulation).to_dict()
    simulation.advance(60.0)
    second = build_facility_state(simulation).to_dict()
    return [first, second]


def _write_capture(
    tmp_path: Path, records: list[dict], *, meta: dict | None = None
) -> tuple[Path, Path]:
    states_path = tmp_path / "facility_states.jsonl"
    meta_path = tmp_path / "stream.meta.json"
    write_jsonl(states_path, records)
    meta_path.write_text(
        json.dumps(meta or _meta(n_records=len(records)), sort_keys=True),
        encoding="utf-8",
    )
    return states_path, meta_path


def _read(
    states_path: Path, meta_path: Path, *, first_sensed_valid: bool = False
):
    return read_native_facility_state_stream(
        states_path,
        meta_path,
        simulation_midnight=MIDNIGHT,
        first_record_clean_sensed_valid=first_sensed_valid,
    )


def test_adapter_reads_actual_native_stream_and_preserves_zero_based_sequence(
    tmp_path: Path,
) -> None:
    records = _records()
    states_path, meta_path = _write_capture(tmp_path, records)
    snapshots = _read(states_path, meta_path)

    assert [item.source_sequence for item in snapshots] == [0, 1]
    assert [item.observed_at.isoformat() for item in snapshots] == [
        "2026-08-08T06:00:00+00:00",
        "2026-08-08T06:01:00+00:00",
    ]
    assert all(item.source_schema_version == NATIVE_STREAM_SCHEMA for item in snapshots)
    assert snapshots[0].site_id == "sim-baseline"
    assert snapshots[0].deployment_id == "dev"
    assert snapshots[0].forecast_demand_balls_per_minute == tuple(
        records[0]["demand"]["forecast_balls_per_minute"]
    )
    assert snapshots[0].source_ref.endswith("facility_states.jsonl:line:1")


def test_adapter_marks_first_native_sensed_value_pre_sensor_tick(
    tmp_path: Path,
) -> None:
    records = _records()
    assert records[0]["ball_flow"]["clean_available"] > 0
    assert records[0]["ball_flow"]["clean_sensed"] == 0.0
    states_path, meta_path = _write_capture(tmp_path, records)
    first, second = _read(states_path, meta_path)

    assert first.clean_sensed is None
    assert "pre_first_sensor_tick" in first.clean_sensed_provenance
    assert (
        "clean_sensed unavailable: caller_declared_pre_first_sensor_tick"
        in first.missing_data_reasons
    )
    assert second.clean_sensed == records[1]["ball_flow"]["clean_sensed"]


def test_adapter_rejects_wrong_native_schema(tmp_path: Path) -> None:
    records = _records()[:1]
    meta = _meta(n_records=1)
    meta["schema"] = "facility-state-stream/v1"
    states_path, meta_path = _write_capture(tmp_path, records, meta=meta)
    with pytest.raises(AdapterError, match="expected stream schema"):
        _read(states_path, meta_path)


@pytest.mark.parametrize(
    ("field_path", "bad_value"),
    [
        (("meta", "t_s"), True),
        (("ball_flow", "clean_available"), False),
        (("demand", "forecast_bucket_minutes"), True),
        (("robots", 0, "battery_frac"), True),
    ],
)
def test_adapter_rejects_boolean_values_masquerading_as_numbers(
    tmp_path: Path, field_path: tuple, bad_value: object
) -> None:
    record = _records()[0]
    target = record
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = bad_value
    states_path, meta_path = _write_capture(tmp_path, [record])
    with pytest.raises(AdapterError, match="boolean|booleans"):
        _read(states_path, meta_path)


def test_adapter_rejects_boolean_meta_count(tmp_path: Path) -> None:
    record = _records()[0]
    meta = _meta(n_records=1)
    meta["n_records"] = True
    states_path, meta_path = _write_capture(tmp_path, [record], meta=meta)
    with pytest.raises(AdapterError, match="booleans are not integers"):
        _read(states_path, meta_path)


def test_adapter_rejects_non_monotonic_native_timestamps(tmp_path: Path) -> None:
    first, second = _records()
    states_path, meta_path = _write_capture(tmp_path, [second, first])
    with pytest.raises(AdapterError, match="non-monotonic"):
        _read(states_path, meta_path)


def test_adapter_rejects_record_count_mismatch(tmp_path: Path) -> None:
    states_path, meta_path = _write_capture(
        tmp_path, _records()[:1], meta=_meta(n_records=2)
    )
    with pytest.raises(AdapterError, match="declares 2 records, read 1"):
        _read(states_path, meta_path)


@pytest.mark.parametrize("lie_about_flag", [False, True])
def test_adapter_rejects_nonconserving_native_ball_flow(
    tmp_path: Path, lie_about_flag: bool
) -> None:
    record = _records()[0]
    record["ball_flow"]["clean_available"] += 1
    record["ball_flow"]["conserved"] = lie_about_flag
    states_path, meta_path = _write_capture(tmp_path, [record])
    with pytest.raises(AdapterError, match="conservation ledger"):
        _read(states_path, meta_path)


def test_adapter_rejects_duplicate_metadata_keys(tmp_path: Path) -> None:
    states_path, meta_path = _write_capture(tmp_path, _records()[:1])
    meta_path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
    with pytest.raises(AdapterError, match="duplicate JSON key"):
        _read(states_path, meta_path)


def test_adapter_rejects_malformed_state_json(tmp_path: Path) -> None:
    states_path, meta_path = _write_capture(tmp_path, _records()[:1])
    states_path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(AdapterError, match="invalid JSON"):
        _read(states_path, meta_path)


def test_adapter_ignores_blank_lines_like_the_upstream_reader(tmp_path: Path) -> None:
    states_path, meta_path = _write_capture(tmp_path, _records()[:1])
    states_path.write_text("\n" + states_path.read_text(encoding="utf-8") + "\n")
    snapshot = _read(states_path, meta_path)[0]
    assert snapshot.source_sequence == 0
    assert snapshot.source_ref.endswith("facility_states.jsonl:line:2")


def test_first_record_sensed_validity_is_explicit_not_ordinal(tmp_path: Path) -> None:
    post_tick = _records()[1]
    states_path, meta_path = _write_capture(tmp_path, [post_tick])

    valid = _read(states_path, meta_path, first_sensed_valid=True)[0]
    pre_sensor = _read(states_path, meta_path, first_sensed_valid=False)[0]

    assert valid.clean_sensed == post_tick["ball_flow"]["clean_sensed"]
    assert "caller_declared_valid_first_record" in valid.clean_sensed_provenance
    assert pre_sensor.clean_sensed is None
    assert "caller_declared_pre_first_sensor_tick" in (
        pre_sensor.clean_sensed_provenance
    )


def test_adapter_preserves_disclaimer_and_reports_extra_metadata_keys(
    tmp_path: Path,
) -> None:
    meta = _meta(n_records=1)
    meta["producer_note"] = "accepted v1 metadata extension"
    states_path, meta_path = _write_capture(tmp_path, _records()[:1], meta=meta)
    capture = read_native_facility_state_capture(
        states_path,
        meta_path,
        simulation_midnight=MIDNIGHT,
        first_record_clean_sensed_valid=False,
    )

    assert capture.metadata.disclaimer == meta["disclaimer"]
    assert capture.metadata.first_record_clean_sensed_valid is False
    assert capture.metadata.unmapped_keys == ("producer_note",)


def test_adapter_native_json_is_repository_shape_not_candidate_wrapper(
    tmp_path: Path,
) -> None:
    record = _records()[0]
    states_path, _ = _write_capture(tmp_path, [record])
    parsed = json.loads(states_path.read_text(encoding="utf-8"))
    assert "ball_flow" in parsed
    assert "state" not in parsed
    assert "schema_version" not in parsed
    assert dump_json_line(record) == states_path.read_text(encoding="utf-8").strip()
