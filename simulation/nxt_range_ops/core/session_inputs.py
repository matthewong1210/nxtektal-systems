"""Bounded immutable inputs for an opt-in multi-day SIMULATION session.

The runtime alone owns admitted jobs, balls, resources and execution.  These
inputs supply exogenous requested shots and their pre-sampled destinations;
unobserved staff slots are capacity declarations, never actionable evidence.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from nxt_range_ops.core.joint_inputs import _identifier, _integer, _keys


SCHEMA = "nxt-course-range-session/v1"
TASK_KINDS = frozenset({"INSPECT", "REPHOTOGRAPH", "REPAIR_DIVOT", "RAKE_BUNKER", "CLEAR_DEBRIS"})
MAX_STAFF_JOB_SLOTS = 4096
MAX_REQUESTED_SHOTS = 2_000_000


def validate_session_inputs(value: Any, *, zone_ids: list[str], open_minute: int,
                            close_minute: int) -> dict[str, Any] | None:
    """Validate, normalize and detach a complete finite session declaration.

    All indexed minutes are absolute since day-zero midnight; the first index
    represents day-zero opening.  Nighttime requested shots must be explicitly
    zero.  Missing zones, future observation payloads and unknown keys fail.
    """
    if value is None:
        return None
    _keys(value, {"schema", "environment", "days", "demand_by_minute",
                  "landing_zones_by_minute", "collection_blocks", "staff_job_slots"}, "session inputs")
    if value["schema"] != SCHEMA or value["environment"] != "SIMULATION":
        raise ValueError("session inputs require the versioned SIMULATION contract")
    days = _integer(value["days"], "days", 1, 7)
    end_minute = (days - 1) * 1440 + close_minute
    length = end_minute - open_minute
    demand, landings = value["demand_by_minute"], value["landing_zones_by_minute"]
    if type(demand) is not list or len(demand) != length:
        raise ValueError("demand_by_minute must cover every session minute exactly")
    if type(landings) is not list or len(landings) != length:
        raise ValueError("landing_zones_by_minute must cover every session minute exactly")
    known_zones = set(zone_ids)
    total = 0
    for index, (requested, targets) in enumerate(zip(demand, landings)):
        _integer(requested, "requested shots", 0, 10_000)
        total += requested
        if total > MAX_REQUESTED_SHOTS:
            raise ValueError("session requested shots exceed the bounded limit")
        if not open_minute <= (open_minute + index) % 1440 < close_minute and requested:
            raise ValueError("nighttime demand must be zero")
        if type(targets) is not list or len(targets) != requested:
            raise ValueError("each landing list must match its requested shot count")
        if any(type(target) is not str or target not in known_zones for target in targets):
            raise ValueError("landing target must identify a declared range zone")

    blocks = value["collection_blocks"]
    _keys(blocks, known_zones, "collection_blocks")
    normalized = {}
    for zone_id in sorted(known_zones):
        windows = blocks[zone_id]
        if type(windows) is not list or len(windows) > length:
            raise ValueError("collection blocks require a bounded list of windows")
        spans = []
        for window in windows:
            _keys(window, {"start_minute", "end_minute"}, "collection window")
            start = _integer(window["start_minute"], "start_minute", 0, end_minute)
            end = _integer(window["end_minute"], "end_minute", 0, end_minute)
            if end <= start:
                raise ValueError("collection windows must be nonempty half-open intervals")
            spans.append((start, end))
        merged = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1]["end_minute"]:
                merged[-1]["end_minute"] = max(end, merged[-1]["end_minute"])
            else:
                merged.append({"start_minute": start, "end_minute": end})
        normalized[zone_id] = merged

    slots = value["staff_job_slots"]
    if type(slots) is not list or len(slots) > MAX_STAFF_JOB_SLOTS:
        raise ValueError("staff_job_slots must be a bounded list")
    identifiers = set()
    for slot in slots:
        _keys(slot, {"job_id", "hole_number", "checkpoint_id", "duration_minutes", "task_kind"}, "staff job slot")
        job_id = _identifier(slot["job_id"], "job_id")
        if job_id in identifiers:
            raise ValueError("staff job slots must have unique IDs")
        identifiers.add(job_id)
        _identifier(slot["checkpoint_id"], "checkpoint_id")
        _integer(slot["hole_number"], "hole_number", 1, 18)
        if type(slot["task_kind"]) is not str or slot["task_kind"] not in TASK_KINDS:
            raise ValueError("unsupported simulation task_kind")
        duration = slot["duration_minutes"]
        try:
            valid = type(duration) in (int, float) and math.isfinite(duration) and 0 < duration <= 1440
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("duration_minutes must be positive and at most one day")
    return {"schema": SCHEMA, "environment": "SIMULATION", "days": days,
            "demand_by_minute": list(demand), "landing_zones_by_minute": deepcopy(landings),
            "collection_blocks": normalized,
            "staff_job_slots": deepcopy(sorted(slots, key=lambda slot: slot["job_id"]))}


def validate_observation_admission(*, job_id: str, observation_id: str, evidence_ref: str,
                                   captured_minute: int, deadline_minute: int,
                                   open_minute: int, end_minute: int) -> dict[str, Any]:
    """Strict request shape; current-time checks and idempotency are runtime-owned."""
    _identifier(job_id, "job_id")
    _identifier(observation_id, "observation_id")
    if (type(evidence_ref) is not str or not evidence_ref.startswith("synthetic:")
            or not 10 < len(evidence_ref) <= 1024 or any(char.isspace() for char in evidence_ref)):
        raise ValueError("evidence_ref must identify bounded explicit synthetic evidence")
    _integer(captured_minute, "captured_minute", open_minute, end_minute - 1)
    _integer(deadline_minute, "deadline_minute", captured_minute, end_minute)
    return {"job_id": job_id, "observation_id": observation_id, "evidence_ref": evidence_ref,
            "captured_minute": captured_minute, "deadline_minute": deadline_minute}
