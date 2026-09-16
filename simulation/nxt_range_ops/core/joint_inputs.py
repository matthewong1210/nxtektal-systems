"""Strict plain-data inputs for joint SIMULATION episodes.

These frozen exogenous inputs are evaluation truth, never policy observations.
No clock, transport, weather provider or additional mutable runtime is owned here.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any


SCHEMA = "nxt-joint-scenario-inputs/v0"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", re.ASCII)


def _keys(value: Any, expected: set[str], label: str) -> None:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{label} requires exactly {sorted(expected)}")


def _integer(value: Any, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} through {high}")
    return value


def _identifier(value: Any, label: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError(f"{label} must be a bounded identifier")
    return value


def validate_joint_inputs(value: Any, *, zone_ids: list[str], open_minute: int,
                          close_minute: int) -> dict[str, Any] | None:
    """Validate and detach the complete day contract; preserve legacy None.

    Access windows use absolute minutes and are unioned to prevent overlapping
    windows from reopening early. Jobs may exceed their deadlines; that is
    meaningful workload pressure, not a malformed input.
    """
    if value is None:
        return None
    _keys(value, {"schema", "environment", "demand_by_minute", "collection_blocks", "staff_jobs"}, "joint inputs")
    if value["schema"] != SCHEMA or value["environment"] != "SIMULATION":
        raise ValueError("joint inputs support only the versioned SIMULATION contract")
    demand = value["demand_by_minute"]
    if type(demand) is not list or len(demand) != close_minute - open_minute:
        raise ValueError("demand_by_minute must cover every operating minute exactly")
    for item in demand:
        _integer(item, "demand sample", 0, 2**31 - 1)
    blocks = value["collection_blocks"]
    _keys(blocks, set(zone_ids), "collection_blocks")
    normalized_blocks = {}
    for zone_id in sorted(zone_ids):
        windows = blocks[zone_id]
        if type(windows) is not list:
            raise ValueError("each zone requires an explicit list of access windows")
        spans = []
        for window in windows:
            _keys(window, {"start_minute", "end_minute"}, "collection window")
            start = _integer(window["start_minute"], "start_minute", 0, 1440)
            end = _integer(window["end_minute"], "end_minute", 0, 1440)
            if end <= start:
                raise ValueError("collection windows must be nonempty half-open intervals")
            spans.append((start, end))
        merged: list[dict[str, int]] = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1]["end_minute"]:
                merged[-1]["end_minute"] = max(end, merged[-1]["end_minute"])
            else:
                merged.append({"start_minute": start, "end_minute": end})
        normalized_blocks[zone_id] = merged
    jobs = value["staff_jobs"]
    if type(jobs) is not list:
        raise ValueError("staff_jobs must be a list")
    job_ids = set()
    for job in jobs:
        _keys(job, {"job_id", "available_minute", "duration_minutes", "deadline_minute", "hole_number", "checkpoint_id"}, "staff job")
        job_id = _identifier(job["job_id"], "job_id")
        if job_id in job_ids:
            raise ValueError("staff job IDs must be unique")
        job_ids.add(job_id)
        _identifier(job["checkpoint_id"], "checkpoint_id")
        available = _integer(job["available_minute"], "available_minute", open_minute, close_minute - 1)
        _integer(job["deadline_minute"], "deadline_minute", available, close_minute)
        _integer(job["hole_number"], "hole_number", 1, 18)
        duration = job["duration_minutes"]
        if type(duration) not in (int, float):
            raise ValueError("duration_minutes must be positive and finite")
        try:
            valid = math.isfinite(duration) and 0 < duration <= 1440
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("duration_minutes must be positive and at most one day")
    return {"schema": SCHEMA, "environment": "SIMULATION", "demand_by_minute": list(demand),
            "collection_blocks": normalized_blocks,
            "staff_jobs": deepcopy(sorted(jobs, key=lambda row: row["job_id"]))}
