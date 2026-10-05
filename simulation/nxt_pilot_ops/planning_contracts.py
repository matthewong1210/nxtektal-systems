"""Strict, detached JSON contracts for human-led planning evidence.

This surface has no persistence, clock, identity-provider or execution access.
The caller supplies rehearsal identity and time; operator names are attribution.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import datetime, timezone


class PlanningError(ValueError):
    """A transport-independent planning failure with a stable public code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _invalid(detail: str) -> None:
    raise PlanningError("planning_invalid_request", detail)


def utc(value: str | datetime) -> datetime:
    """Parse wire UTC, or normalize an explicitly supplied aware clock value."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            _invalid("time must be timezone-aware")
        return value.astimezone(timezone.utc)
    if type(value) is not str or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        _invalid("time must be RFC3339 UTC with Z and at most microsecond precision")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _invalid("time is not a valid calendar timestamp")


def utc_text(value: datetime) -> str:
    value = utc(value)
    return value.isoformat(timespec="microseconds" if value.microsecond else "seconds").replace(
        "+00:00", "Z"
    )


def _json(value: object, path: str = "request") -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            _invalid(f"{path} must contain finite JSON numbers")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _json(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                _invalid(f"{path} requires string keys")
            _json(item, f"{path}.{key}")
        return
    _invalid(f"{path} must contain only JSON values")


def _object(value: object, fields: str, path: str) -> dict:
    if type(value) is not dict or set(value) != set(fields.split()):
        _invalid(f"{path} requires exactly: {fields}")
    return value


def _text(value: object, path: str) -> str:
    if type(value) is not str or not value.strip():
        _invalid(f"{path} must be a nonempty string")
    return value


def _number(value: object, path: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float):
        _invalid(f"{path} must be a number, not a boolean")
    try:
        number = float(value)
    except OverflowError:
        _invalid(f"{path} must be finite")
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        _invalid(f"{path} must be finite and {'positive' if positive else 'nonnegative'}")
    return number


def _integer(value: object, path: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        _invalid(f"{path} must be an integer >= {minimum}")


def _scope(value: object) -> None:
    if value not in ("SHIFT", "DAY", "ONE_TASK"):
        _invalid("scope must be SHIFT, DAY or ONE_TASK")


INPUT_FIELDS = (
    "schema request_id expected_revision site_id deployment_id site_timezone operator "
    "reason scope effective_at_utc valid_until_utc operating_window inventory_clean_balls "
    "demand safety_stock_balls buffer_minutes operations_allowed washer_available zones"
)
PLAN_FIELDS = (
    "schema request_id plan_id expected_plan_version input_revision operator reason scope "
    "valid_until_utc selection"
)


def _evidence(value: object, path: str, unit: str, now: datetime, input_end: datetime) -> object:
    if value is None:
        return None
    evidence = _object(
        value, "value source_kind source_ref observed_at_utc valid_until_utc unit", path
    )
    if evidence["source_kind"] not in ("MEASURED", "MANUAL_ESTIMATE"):
        _invalid(f"{path}.source_kind must be MEASURED or MANUAL_ESTIMATE")
    _text(evidence["source_ref"], f"{path}.source_ref")
    if evidence["unit"] != unit:
        _invalid(f"{path}.unit must be {unit}")
    observed, end = utc(evidence["observed_at_utc"]), utc(evidence["valid_until_utc"])
    if observed > now:
        _invalid(f"{path} observation cannot be in the future")
    if observed >= end:
        _invalid(f"{path} evidence validity must end after observation")
    if input_end > end:
        _invalid(f"input validity cannot extend {path} evidence validity")
    result = evidence["value"]
    if result is None:
        _invalid(f"{path}.value cannot be null; represent unknown evidence as a null field")
    if unit == "boolean" and type(result) is not bool:
        _invalid(f"{path}.value must be a boolean")
    return result


def validate_input_request(payload: object, context: dict, now: str | datetime) -> dict:
    """Validate a replacement input document without inventing absent evidence."""
    _json(payload)
    item = _object(payload, INPUT_FIELDS, "input")
    if item["schema"] != "nxt-planning-input/v1":
        _invalid("unsupported input schema")
    now = utc(now)
    for field in ("request_id", "site_id", "deployment_id", "site_timezone", "operator", "reason"):
        _text(item[field], field)
    for field in ("site_id", "deployment_id", "site_timezone"):
        if item[field] != context[field]:
            _invalid(f"{field} does not match rehearsal context")
    _integer(item["expected_revision"], "expected_revision")
    _scope(item["scope"])
    origin, end = utc(item["effective_at_utc"]), utc(item["valid_until_utc"])
    if origin >= end:
        _invalid("input validity must end after its effective time")
    window = _object(item["operating_window"], "start_at_utc end_at_utc", "operating_window")
    if utc(window["start_at_utc"]) >= utc(window["end_at_utc"]):
        _invalid("operating window must have positive duration")
    for field, unit in (
        ("inventory_clean_balls", "balls"), ("safety_stock_balls", "balls"),
        ("buffer_minutes", "minutes"), ("operations_allowed", "boolean"),
        ("washer_available", "boolean"),
    ):
        value = _evidence(item[field], field, unit, now, end)
        if value is not None and unit != "boolean":
            _number(value, f"{field}.value")
    if item["inventory_clean_balls"] is not None:
        if utc(item["inventory_clean_balls"]["observed_at_utc"]) != origin:
            _invalid("opening inventory observation must equal effective_at_utc")
    demand = _evidence(item["demand"], "demand", "balls/minute", now, end)
    if demand is not None:
        _object(demand, "bucket_minutes low typical high", "demand.value")
        _number(demand["bucket_minutes"], "demand.bucket_minutes", positive=True)
        for level in ("low", "typical", "high"):
            values = demand[level]
            if type(values) is not list or not values:
                _invalid(f"demand.{level} must be a nonempty array")
            for value in values:
                _number(value, f"demand.{level}")
        if len({len(demand[level]) for level in ("low", "typical", "high")}) != 1:
            _invalid("demand arrays must have equal lengths")
        if any(not low <= typical <= high for low, typical, high in zip(
            demand["low"], demand["typical"], demand["high"]
        )):
            _invalid("demand must satisfy low <= typical <= high in every bucket")
    if type(item["zones"]) is not list:
        _invalid("zones must be an array")
    pairs = set()
    for index, zone in enumerate(item["zones"]):
        path = f"zones[{index}]"
        _object(zone, "zone_id robot_id collection_allowed clean_yield_balls cycle_minutes", path)
        for field, allowed in (("zone_id", "zone_ids"), ("robot_id", "robot_ids")):
            _text(zone[field], f"{path}.{field}")
            if zone[field] not in context[allowed]:
                _invalid(f"{path}.{field} is not in rehearsal context")
        pair = (zone["zone_id"], zone["robot_id"])
        if pair in pairs:
            _invalid("zones must contain unique (zone_id, robot_id) pairs")
        pairs.add(pair)
        _evidence(zone["collection_allowed"], f"{path}.collection_allowed", "boolean", now, end)
        yields = _evidence(zone["clean_yield_balls"], f"{path}.clean_yield_balls", "balls", now, end)
        if yields is not None:
            _object(yields, "low high", f"{path}.clean_yield_balls.value")
            for bound in ("low", "high"):
                _integer(yields[bound], f"{path}.clean_yield_balls.{bound}")
                _number(yields[bound], f"{path}.clean_yield_balls.{bound}")
            if yields["low"] > yields["high"]:
                _invalid(f"{path} yield low must not exceed high")
        cycle = _evidence(zone["cycle_minutes"], f"{path}.cycle_minutes", "minutes", now, end)
        if cycle is not None:
            _object(cycle, "travel collect return unload wash supply", f"{path}.cycle_minutes.value")
            for stage, value in cycle.items():
                _number(value, f"{path}.cycle_minutes.{stage}")
    return deepcopy(item)


def validate_plan_request(payload: object, now: str | datetime) -> dict:
    _json(payload)
    item = _object(payload, PLAN_FIELDS, "plan request")
    if item["schema"] != "nxt-planning-request/v1":
        _invalid("unsupported plan request schema")
    utc(now)
    for field in ("request_id", "operator", "reason"):
        _text(item[field], field)
    _integer(item["input_revision"], "input_revision", 1)
    _integer(item["expected_plan_version"], "expected_plan_version")
    if item["plan_id"] is None:
        if item["expected_plan_version"] != 0:
            _invalid("new plans require expected_plan_version zero")
    else:
        _text(item["plan_id"], "plan_id")
        if item["expected_plan_version"] == 0:
            _invalid("plan revisions require a positive expected_plan_version")
    _scope(item["scope"])
    utc(item["valid_until_utc"])
    if item["selection"] is not None:
        selection = _object(item["selection"], "zone_id robot_id start_at_utc", "selection")
        for field in ("zone_id", "robot_id"):
            _text(selection[field], f"selection.{field}")
        utc(selection["start_at_utc"])
    return deepcopy(item)


def validate_confirmation_request(payload: object) -> dict:
    _json(payload)
    item = _object(payload, "schema request_id plan_id plan_version operator", "confirmation")
    if item["schema"] != "nxt-planning-confirmation/v1":
        _invalid("unsupported confirmation schema")
    for field in ("request_id", "plan_id", "operator"):
        _text(item[field], field)
    _integer(item["plan_version"], "plan_version", 1)
    return deepcopy(item)


def validate_outcome_request(payload: object, now: str | datetime) -> dict:
    _json(payload)
    item = _object(payload,
        "schema request_id confirmation_id task_id operator reason stage quantity_balls "
        "started_at_utc completed_at_utc source_kind source_ref supersedes_outcome_id", "outcome")
    if item["schema"] != "nxt-planning-outcome/v1":
        _invalid("unsupported outcome schema")
    for field in ("request_id", "confirmation_id", "task_id", "operator", "reason", "source_ref"):
        _text(item[field], field)
    if item["supersedes_outcome_id"] is not None:
        _text(item["supersedes_outcome_id"], "supersedes_outcome_id")
    if item["stage"] not in ("COLLECTED", "UNLOADED", "WASHED", "SUPPLIED"):
        _invalid("unknown actual result stage")
    if item["source_kind"] not in ("MEASURED", "MANUAL_ESTIMATE"):
        _invalid("outcome quality must be MEASURED or MANUAL_ESTIMATE")
    _integer(item["quantity_balls"], "quantity_balls")
    if not utc(item["started_at_utc"]) <= utc(item["completed_at_utc"]) <= utc(now):
        _invalid("outcome times must be ordered and cannot be future")
    return deepcopy(item)
