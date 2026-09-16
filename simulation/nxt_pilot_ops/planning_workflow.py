"""Pure append-only planning workflow decisions over injected verified evidence.

The caller owns serialization, locking and persistence. These functions never
create schedules or tasks and never update facility or inventory truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .planning import evaluate_plan
from .planning_contracts import (
    PlanningError, utc, utc_text, validate_input_request, validate_plan_request,
    validate_confirmation_request, validate_outcome_request,
)
from .serialization import canonical_json, stable_digest, to_primitive

INPUT = "planning_input_recorded"
PLAN = "planning_plan_recorded"
CONFIRMATION = "planning_confirmation_recorded"
OUTCOME = "planning_outcome_recorded"
PLANNING_RECORD_KINDS = frozenset({INPUT, PLAN, CONFIRMATION, OUTCOME})
SCHEMA = "nxt-planning/v1"


def _fail(code: str, detail: str) -> None:
    raise PlanningError(code, detail)


def _copy(value: Any) -> Any:
    return to_primitive(value)


def _request(kind: str, record: Mapping[str, Any]) -> dict[str, Any]:
    if kind == INPUT:
        return {k: _copy(v) for k, v in record.items() if k not in {
            "revision", "input_digest", "recorded_at_utc", "changes",
        }}
    return _copy(record["request"])


@dataclass(frozen=True)
class PlanningHistory:
    """Detached replay result; not mutable facility state."""
    entries: tuple[dict[str, Any], ...]

    def records(self, kind: str) -> list[dict[str, Any]]:
        return [_copy(e["record"]) for e in self.entries if e["kind"] == kind]

    @property
    def latest_input(self) -> dict[str, Any] | None:
        rows = self.records(INPUT)
        return rows[-1] if rows else None

    def plan(self, plan_id: str, version: int | None = None) -> dict[str, Any]:
        rows = [r for r in self.records(PLAN) if r["plan_id"] == plan_id
                and (version is None or r["version"] == version)]
        if not rows:
            _fail("planning_not_found", "plan/version does not exist")
        return rows[-1]

    def confirmation(self, confirmation_id: str) -> dict[str, Any]:
        for row in self.records(CONFIRMATION):
            if row["confirmation_id"] == confirmation_id:
                return row
        _fail("planning_not_found", "confirmation does not exist")

    def by_request(self, request_id: str) -> tuple[str, dict[str, Any]] | None:
        for e in self.entries:
            if _request(e["kind"], e["record"])["request_id"] == request_id:
                return e["kind"], _copy(e["record"])
        return None


def receipt(record: Mapping[str, Any], request_id: str, disposition: str) -> dict[str, Any]:
    return {"schema": SCHEMA, "request_id": request_id,
            "disposition": disposition, "record": _copy(record)}


def duplicate(history: PlanningHistory, kind: str, request: Mapping[str, Any]) -> dict[str, Any] | None:
    if type(request) is not dict or type(request.get("request_id")) is not str:
        return None
    existing = history.by_request(request["request_id"])
    if existing is None:
        return None
    old_kind, row = existing
    try:
        identical = canonical_json(_request(old_kind, row)) == canonical_json(request)
    except (TypeError, ValueError) as exc:
        raise PlanningError("planning_invalid_request", f"request is not finite JSON: {exc}") from exc
    if kind != old_kind or not identical:
        _fail("planning_conflict", "request_id is already bound to different content")
    return receipt(row, request["request_id"], "duplicate")


def _changes(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [change for key in sorted(before.keys() | after.keys())
                for change in _changes(before.get(key), after.get(key), f"{path}/{key}")]
    return [{"path": path or "/", "before": _copy(before), "after": _copy(after)}]


def prepare_input(history: PlanningHistory, payload: dict[str, Any], context: dict[str, Any], now: datetime) -> dict[str, Any]:
    repeated = duplicate(history, INPUT, payload)
    if repeated:
        return repeated
    request = validate_input_request(payload, context, now)
    previous = history.latest_input
    revision = previous["revision"] if previous else 0
    if request["expected_revision"] != revision:
        _fail("planning_conflict", "input revision changed; reload before correcting")
    old = _request(INPUT, previous) if previous else None
    # Request metadata is retained in full; changes enumerate evidence/intent,
    # excluding bookkeeping such as the idempotency key.
    ignored = {"request_id", "expected_revision", "operator", "reason"}
    old_values = {k: v for k, v in old.items() if k not in ignored} if old else None
    new_values = {k: v for k, v in request.items() if k not in ignored}
    row = {**request, "revision": revision + 1, "input_digest": stable_digest(request),
           "recorded_at_utc": utc_text(now), "changes": _changes(old_values, new_values)}
    return receipt(row, request["request_id"], "created")


def prepare_plan(history: PlanningHistory, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    repeated = duplicate(history, PLAN, payload)
    if repeated:
        return repeated
    request = validate_plan_request(payload, now)
    source = history.latest_input
    if source is None or source["revision"] != request["input_revision"]:
        _fail("planning_conflict", "plan requires the current input revision")
    previous = None
    if request["plan_id"] is not None:
        previous = history.plan(request["plan_id"])
        if previous["version"] != request["expected_plan_version"]:
            _fail("planning_conflict", "plan version changed; reload before revising")
        if any(r["plan_id"] == previous["plan_id"] for r in history.records(CONFIRMATION)):
            _fail("planning_conflict", "confirmed plan is immutable; use a new plan")
    elif request["expected_plan_version"] != 0:
        _fail("planning_conflict", "new plans require expected_plan_version zero")
    row = evaluate_plan(source, request, now, previous_plan=previous)
    return receipt(row, request["request_id"], "created")


def require_current(history: PlanningHistory, plan: dict[str, Any], now: datetime) -> None:
    source = history.latest_input
    if source is None or source["revision"] != plan["input_revision"] or source["input_digest"] != plan["input_digest"]:
        _fail("planning_conflict", "plan input was superseded")
    if history.plan(plan["plan_id"])["version"] != plan["version"]:
        _fail("planning_conflict", "plan version was superseded")
    if now < utc(source["effective_at_utc"]):
        _fail("planning_not_ready", "input has not become effective")
    if now >= min(utc(source["valid_until_utc"]), utc(plan["valid_until_utc"])):
        _fail("planning_expired", "input or plan validity elapsed")
    if plan["status"] != "READY" or plan["selection"] is None:
        _fail("planning_not_ready", "only a complete feasible READY plan can be confirmed")
    candidate = next((r for r in plan["candidates"] if r["zone_id"] == plan["selection"]["zone_id"]
                      and r["robot_id"] == plan["selection"]["robot_id"]), None)
    if candidate is None or not candidate["eligible"] or not candidate["protects_high_demand_horizon"]:
        _fail("planning_not_ready", "selected candidate does not protect the high demand horizon")
    if candidate["latest_start_at_utc"] is not None and now >= utc(candidate["latest_start_at_utc"]):
        _fail("planning_expired", "latest safe start has elapsed")


def schedule_operator(label: str) -> str:
    """Stable internal attribution compatible with the rehearsal wire vocabulary."""
    return "planning-" + stable_digest(label)[:24]


def confirmation_identity(plan: dict[str, Any]) -> str:
    return "confirmation_" + stable_digest({"plan_id": plan["plan_id"], "plan_version": plan["version"]})[:24]


def existing_confirmation(history: PlanningHistory, payload: dict[str, Any]) -> dict[str, Any] | None:
    repeated = duplicate(history, CONFIRMATION, payload)
    if repeated:
        return repeated
    request = validate_confirmation_request(payload)
    for row in history.records(CONFIRMATION):
        if row["plan_id"] == request["plan_id"]:
            if row["plan_version"] != request["plan_version"]:
                _fail("planning_conflict", "this plan already has a confirmed version")
            return receipt(row, row["request"]["request_id"], "duplicate")
    return None


def prepare_confirmation(history: PlanningHistory, payload: dict[str, Any], now: datetime,
                         *, schedule: dict[str, Any], schedule_id: str) -> dict[str, Any]:
    repeated = existing_confirmation(history, payload)
    if repeated:
        return repeated
    request = validate_confirmation_request(payload)
    plan = history.plan(request["plan_id"], request["plan_version"])
    require_current(history, plan, now)
    selected = plan["selection"]
    expected = {"robot_id", "zone_id", "due_at_utc", "expires_at_utc", "operator", "admission_reference"}
    if set(schedule) != expected or any((
        schedule["robot_id"] != selected["robot_id"], schedule["zone_id"] != selected["zone_id"],
        schedule["due_at_utc"] != selected["start_at_utc"], schedule["operator"] != schedule_operator(request["operator"]),
        schedule["admission_reference"] != confirmation_identity(plan),
    )):
        _fail("planning_conflict", "schedule binding differs from the exact confirmed plan")
    if utc(schedule["expires_at_utc"]) <= utc(schedule["due_at_utc"]):
        _fail("planning_expired", "the selected start/dispatch window has elapsed")
    candidate = next(c for c in plan["candidates"] if c["zone_id"] == selected["zone_id"]
                     and c["robot_id"] == selected["robot_id"])
    deadline = min(utc(plan["valid_until_utc"]), utc(candidate["latest_start_at_utc"]))
    if utc(schedule["expires_at_utc"]) != deadline:
        _fail("planning_conflict", "schedule expiry must equal the plan admission deadline")
    row = {"confirmation_id": confirmation_identity(plan), "request": request,
           "plan_id": plan["plan_id"], "plan_version": plan["version"],
           "input_revision": plan["input_revision"], "confirmed_at_utc": utc_text(now),
           "schedule_id": schedule_id, "schedule": _copy(schedule)}
    return receipt(row, request["request_id"], "created")


def prepare_outcome(history: PlanningHistory, payload: dict[str, Any], now: datetime,
                    *, admitted_task_id: str | None, task_created_at_utc: str | None) -> dict[str, Any]:
    repeated = duplicate(history, OUTCOME, payload)
    if repeated:
        return repeated
    request = validate_outcome_request(payload, now)
    confirmation = history.confirmation(request["confirmation_id"])
    if not admitted_task_id or request["task_id"] != admitted_task_id:
        _fail("planning_conflict", "result must reference the admitted task for this confirmation")
    if task_created_at_utc is None or utc(request["started_at_utc"]) < utc(task_created_at_utc):
        _fail("planning_invalid_request", "result cannot start before task admission")
    earlier = [r for r in history.records(OUTCOME) if r["request"]["confirmation_id"] == request["confirmation_id"]
               and r["request"]["stage"] == request["stage"]]
    expected = earlier[-1]["outcome_id"] if earlier else None
    if request["supersedes_outcome_id"] != expected:
        _fail("planning_conflict", "stage correction must reference the latest outcome")
    row = {"outcome_id": "outcome_" + stable_digest(request)[:24], "recorded_at_utc": utc_text(now),
           "evidence_kind": "ACTUAL_EXECUTION_RESULT", "request": request,
           "plan_id": confirmation["plan_id"], "plan_version": confirmation["plan_version"],
           "input_revision": confirmation["input_revision"], "schedule_id": confirmation["schedule_id"]}
    return receipt(row, request["request_id"], "created")


def _replay_planning(entries: Sequence[Mapping[str, Any]], context: dict[str, Any]) -> PlanningHistory:
    """Recompute semantic history, failing loudly on edits, drift or illegal CAS.

    The injected storage has already verified byte integrity and ordering.
    Planning semantic verification is independent of its persistence mechanism.
    """
    verified: list[dict[str, Any]] = []
    for entry in entries:
        if set(entry) != {"kind", "record"} or entry["kind"] not in PLANNING_RECORD_KINDS:
            _fail("planning_unavailable", "unknown planning evidence kind")
        kind, row = entry["kind"], _copy(entry["record"])
        history = PlanningHistory(tuple(verified))
        request = _request(kind, row)
        if history.by_request(request["request_id"]) is not None:
            _fail("planning_unavailable", "duplicate request record in immutable history")
        if kind == INPUT:
            result = prepare_input(history, request, context, utc(row["recorded_at_utc"]))
        elif kind == PLAN:
            result = prepare_plan(history, request, utc(row["generated_at_utc"]))
        elif kind == CONFIRMATION:
            result = prepare_confirmation(history, request, utc(row["confirmed_at_utc"]),
                                          schedule=row["schedule"], schedule_id=row["schedule_id"])
        else:
            # External task association is checked by the composition root
            # against its own verified history on both write and recovery.
            result = prepare_outcome(history, request, utc(row["recorded_at_utc"]),
                                     admitted_task_id=request["task_id"],
                                     task_created_at_utc=request["started_at_utc"])
        if result["disposition"] != "created" or canonical_json(result["record"]) != canonical_json(row):
            _fail("planning_unavailable", "planning evidence failed deterministic replay")
        verified.append({"kind": kind, "record": row})
    return PlanningHistory(tuple(verified))


def replay_planning(entries: Sequence[Mapping[str, Any]], context: dict[str, Any]) -> PlanningHistory:
    try:
        return _replay_planning(entries, context)
    except (PlanningError, KeyError, TypeError, ValueError, StopIteration) as exc:
        raise PlanningError("planning_unavailable", f"planning evidence failed verification: {exc}") from exc


def planning_snapshot(history: PlanningHistory, context: dict[str, Any], now: datetime) -> dict[str, Any]:
    plans = history.records(PLAN)
    confirmations = history.records(CONFIRMATION)
    for plan in plans:
        current = plan["status"]
        try:
            require_current(history, plan, now)
        except PlanningError as exc:
            if exc.code == "planning_conflict":
                current = "INVALIDATED"
            elif exc.code == "planning_expired":
                current = "EXPIRED"
        if any(c["plan_id"] == plan["plan_id"] and c["plan_version"] == plan["version"] for c in confirmations):
            current = "CONFIRMED"
        plan["current_status"] = current
    return {"schema": SCHEMA, "environment": "SIMULATION", "mode": "MANUAL_LED",
            "server_time_utc": utc_text(now), "context": _copy(context),
            "latest_input": history.latest_input, "plans": plans,
            "confirmations": confirmations, "outcomes": history.records(OUTCOME)}
