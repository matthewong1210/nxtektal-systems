"""Stream schema and JSONL IO for the twin's file contracts."""
import json
from pathlib import Path

import pytest

from nxt_range_twin.stream import (
    REQUIRED_META_KEYS,
    STREAM_SCHEMA,
    dump_json,
    dump_json_line,
    read_jsonl,
    validate_stream_meta,
    write_jsonl,
)


def _meta() -> dict:
    return {
        "schema": STREAM_SCHEMA,
        "site_id": "sim-baseline",
        "deployment_id": "dev",
        "episode_id": "normal_weekday-seed7",
        "scenario_name": "normal_weekday",
        "seed": 7,
        "policy": "inventory_threshold",
        "policy_version": "0",
        "control_interval_s": 60.0,
        "every_steps": 1,
        "n_records": 3,
        "simulator_version": "x",
        "git_commit": None,
        "disclaimer": "placeholder",
    }


def test_schema_tag_and_meta_validation():
    assert STREAM_SCHEMA == "nxt-range-twin/facility-state-stream/v1"
    validate_stream_meta(_meta())  # must not raise
    broken = _meta()
    del broken["site_id"]
    with pytest.raises(ValueError, match="site_id"):
        validate_stream_meta(broken)


def test_jsonl_roundtrip_sorted_and_byte_stable(tmp_path: Path):
    records = [{"b": 2, "a": 1}, {"z": [3, 2], "a": {"y": 1, "x": 0}}]
    p = tmp_path / "s.jsonl"
    n = write_jsonl(p, records)
    assert n == 2
    line0 = p.read_text().splitlines()[0]
    assert line0 == '{"a":1,"b":2}'  # sorted keys, compact separators
    assert read_jsonl(p) == [json.loads(dump_json_line(r)) for r in records]
    # byte stability: writing again produces identical bytes
    before = p.read_bytes()
    write_jsonl(p, records)
    assert p.read_bytes() == before


def test_dump_json_formatting():
    """Test dump_json output: newline termination, indentation, sorted keys, and determinism."""
    record = {"b": 1, "a": 2}
    output = dump_json(record)

    # ends with newline
    assert output.endswith("\n")

    # is indented (contains indented lines)
    assert "\n  " in output

    # keys are sorted: "a" appears before "b"
    a_pos = output.index('"a"')
    b_pos = output.index('"b"')
    assert a_pos < b_pos

    # calling twice yields identical output
    output2 = dump_json(record)
    assert output == output2


def test_validate_stream_meta_schema_mismatch():
    """Test that validate_stream_meta raises ValueError for unexpected schema."""
    meta = _meta()
    meta["schema"] = "wrong/schema"
    with pytest.raises(ValueError, match="unexpected schema"):
        validate_stream_meta(meta)
