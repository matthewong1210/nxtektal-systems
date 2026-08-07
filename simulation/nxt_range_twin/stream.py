"""facility-state-stream/v1 — the twin's dynamic input contract.

stdlib only. One FacilityState.to_dict() per JSONL line; sidecar meta file
carries identity. Sorted keys and compact separators make every artifact
byte-reproducible.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

STREAM_SCHEMA = "nxt-range-twin/facility-state-stream/v1"

REQUIRED_META_KEYS = frozenset(
    {
        "schema", "site_id", "deployment_id", "episode_id", "scenario_name",
        "seed", "policy", "policy_version", "control_interval_s",
        "every_steps", "n_records", "simulator_version", "git_commit",
        "disclaimer",
    }
)


def dump_json_line(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)


def dump_json(record: dict) -> str:
    return json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"


def write_jsonl(path: str | Path, records: Iterable[dict]) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(dump_json_line(record) + "\n")
            count += 1
    return count


def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def validate_stream_meta(meta: dict) -> None:
    missing = sorted(REQUIRED_META_KEYS - meta.keys())
    if missing:
        raise ValueError(f"stream meta missing keys: {missing}")
    if meta["schema"] != STREAM_SCHEMA:
        raise ValueError(f"unexpected schema {meta['schema']!r}")
