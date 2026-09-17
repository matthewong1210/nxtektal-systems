"""Local planning-to-rehearsal composition; no physical integration.

All semantic planning decisions stay in the advisory owner. Existing simulation
schedule admission stays in its owner. This root supplies clock, verified
journal records and the explicit human-confirmation bridge between them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from urllib.parse import unquote

from nxt_edge_task.cases import TASK_CREATED
from nxt_edge_task.contracts import EdgeTaskError, TaskRequest, stable_digest
from nxt_edge_task.journal import JsonlJournal, PreconditionFailed, RecordSpec
from nxt_edge_task.schedules import ScheduleService, derive_schedules, normalized_schedule
from nxt_pilot_ops.planning_contracts import PlanningError, utc, utc_text
from nxt_pilot_ops.planning_workflow import (
    INPUT, PLAN, CONFIRMATION, OUTCOME, PLANNING_RECORD_KINDS,
    PlanningHistory, confirmation_identity, existing_confirmation, planning_snapshot,
    prepare_input, prepare_plan, prepare_confirmation, prepare_outcome,
    replay_planning, require_current, receipt, schedule_operator,
)
from nxt_pilot_ops.serialization import to_primitive
from nxt_site_agent import SiteAgentError


class PlanningOperations:
    def __init__(self, journal: JsonlJournal, config, facts, clock: Callable[[], datetime],
                 *, site_timezone: str) -> None:
        self.journal, self.config, self.facts, self.clock = journal, config, facts, clock
        self.context = {
            "site_id": config.site_id, "deployment_id": config.deployment_id,
            "site_timezone": site_timezone, "zone_ids": sorted(facts.zone_ids),
            "robot_ids": sorted(r for r in config.robot_ids
                                if "COLLECT_BALLS_ZONE" in config.robot(r).task_types),
        }
        self.schedules = ScheduleService(journal, config, facts, admission_gate=self.admission_gate)

    def history(self, records) -> PlanningHistory:
        try:
            return self._history(records)
        except (PreconditionFailed, PlanningError, EdgeTaskError, KeyError, TypeError, ValueError) as exc:
            raise PlanningError("planning_unavailable", f"evidence association failed verification: {exc}") from exc

    def _history(self, records) -> PlanningHistory:
        history = replay_planning([
            {"kind": r.record_kind, "record": to_primitive(r.payload)}
            for r in records if r.record_kind in PLANNING_RECORD_KINDS
        ], self.context)
        rows = derive_schedules(records, self.config, self.facts)
        for c in history.records(CONFIRMATION):
            body = normalized_schedule(c["schedule"], self.config, self.facts)
            if c["schedule_id"] != "schedule_" + stable_digest(body)[:24]:
                raise PlanningError("planning_unavailable", "confirmation schedule content identity disagrees")
            existing = rows.get(c["schedule_id"])
            if existing is not None:
                if any(existing.get(k) != v for k, v in body.items()):
                    raise PlanningError("planning_unavailable", "schedule differs from frozen confirmation")
                self._task_link(records, existing)
        for outcome in history.records(OUTCOME):
            c = history.confirmation(outcome["request"]["confirmation_id"])
            row = rows.get(c["schedule_id"])
            task_id, admitted_at = self._task_link(records, row)
            if task_id != outcome["request"]["task_id"] or admitted_at is None or utc(outcome["request"]["started_at_utc"]) < utc(admitted_at):
                raise PlanningError("planning_unavailable", "outcome task association failed replay")
        return history

    @staticmethod
    def _task_link(records, row):
        if row is None or row["task_id"] is None:
            return None, None
        for record in records:
            if record.record_kind == TASK_CREATED and record.payload.get("task_id") == row["task_id"]:
                request = TaskRequest.from_dict(to_primitive(record.payload["request"]))
                expected = {
                    "task_id": row["task_id"], "site_id": row["site_id"],
                    "deployment_id": row["deployment_id"], "simulation_env_id": row["simulation_env_id"],
                    "target_robot_id": row["robot_id"], "zone_id": row["zone_id"],
                    "task_type": "COLLECT_BALLS_ZONE", "issued_at_utc": row["due_at_utc"],
                    "expires_at_utc": row["expires_at_utc"], "progress_window_s": row["progress_window_s"],
                    "issued_by": "SIMULATION_TEST_ENTRY:" + row["operator"],
                }
                if record.payload.get("schedule_id") != row["schedule_id"] or any(
                    getattr(request, key) != value for key, value in expected.items()
                ):
                    raise PlanningError("planning_unavailable", "task payload differs from its frozen schedule")
                if not utc(row["due_at_utc"]) <= utc(record.recorded_at_utc) < utc(row["expires_at_utc"]):
                    raise PlanningError("planning_unavailable", "task admission is outside its frozen window")
                return row["task_id"], record.recorded_at_utc
        raise PlanningError("planning_unavailable", "schedule task has no admission evidence")

    def admission_gate(self, records, row, now):
        """Called under the journal lock: consume the passed records, never read."""
        history = self.history(records)
        try:
            c = history.confirmation(row["admission_reference"])
            if c["schedule_id"] != row["schedule_id"]:
                return "planning_conflict", "confirmation is bound to another schedule"
            plan = history.plan(c["plan_id"], c["plan_version"])
            require_current(history, plan, now)
        except PlanningError as exc:
            if exc.code == "planning_unavailable":
                raise
            return exc.code, exc.detail
        return None

    def recover(self) -> None:
        """Materialize durable intents using their original, frozen request.

        The creation time is the original intent time; a late recovery creates
        that same schedule and due admission marks it missed/rejected. Never
        rebase a lost response to a new due time or extend its expiration.
        """
        history = self.history(self.journal.read())
        for c in history.records(CONFIRMATION):
            self.schedules.create(c["schedule"], utc(c["confirmed_at_utc"]))

    def snapshot(self, now: datetime) -> dict[str, Any]:
        records = self.journal.read()
        history = self.history(records)
        result = planning_snapshot(history, self.context, now)
        rows = derive_schedules(records, self.config, self.facts)
        for c in result["confirmations"]:
            schedule = rows.get(c["schedule_id"])
            c["schedule_status"] = schedule["status"] if schedule else None
            # Read projection only: actual admission evidence, not a stage start.
            c["task_id"], c["task_created_at_utc"] = self._task_link(records, schedule)
        return result

    def _mutate(self, kind: str, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
        result: dict[str, Any] = {}

        def build(records):
            history = self.history(records)
            if kind == INPUT:
                response = prepare_input(history, payload, self.context, now)
            elif kind == PLAN:
                response = prepare_plan(history, payload, now)
            elif kind == CONFIRMATION:
                response = existing_confirmation(history, payload)
                if response is None:
                    plan = history.plan(payload["plan_id"], payload["plan_version"])
                    require_current(history, plan, now)
                    selected = plan["selection"]
                    candidate = next(c for c in plan["candidates"]
                                     if c["zone_id"] == selected["zone_id"] and c["robot_id"] == selected["robot_id"])
                    expiry = min(utc(plan["valid_until_utc"]), utc(candidate["latest_start_at_utc"]))
                    schedule = {"robot_id": selected["robot_id"], "zone_id": selected["zone_id"],
                                "due_at_utc": selected["start_at_utc"], "expires_at_utc": utc_text(expiry),
                                "operator": schedule_operator(payload["operator"]), "admission_reference": confirmation_identity(plan)}
                    body = normalized_schedule(schedule, self.config, self.facts)
                    response = prepare_confirmation(history, payload, now, schedule=schedule,
                                                    schedule_id="schedule_" + stable_digest(body)[:24])
            else:
                # The pure owner validates fields; task association is supplied
                # from this same locked, verified Edge prefix.
                from nxt_pilot_ops.planning_contracts import validate_outcome_request
                request = validate_outcome_request(payload, now)
                c = history.confirmation(request["confirmation_id"])
                rows = derive_schedules(records, self.config, self.facts)
                task_id, admitted_at = self._task_link(records, rows.get(c["schedule_id"]))
                response = prepare_outcome(history, payload, now, admitted_task_id=task_id,
                                           task_created_at_utc=admitted_at)
            result.update(response)
            if response["disposition"] == "duplicate":
                return []
            return [RecordSpec(kind, "OPERATOR", utc_text(now), response["record"])]

        # A builder refusal is definitely uncommitted. Any storage error after
        # it returned may have persisted the record and must be called unknown.
        self.journal.append_via(build)
        return result

    def route(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        prefix = "/api/v1/planning"
        try:
            now = self.clock()
            if method == "GET" and path == prefix:
                return self.snapshot(now)
            if method == "GET" and path.startswith(prefix + "/requests/"):
                try:
                    request_id = unquote(path[len(prefix + "/requests/"):], encoding="utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise PlanningError("planning_invalid_request", "request ID path must be valid UTF-8") from exc
                history = self.history(self.journal.read())
                found = history.by_request(request_id)
                if found is None:
                    raise PlanningError("planning_request_not_found", "no committed request in verified evidence")
                return receipt(found[1], request_id, "duplicate")
            routes = {prefix + "/inputs": INPUT, prefix + "/plans": PLAN,
                      prefix + "/confirmations": CONFIRMATION, prefix + "/outcomes": OUTCOME}
            if method != "POST" or path not in routes:
                raise PlanningError("planning_not_found", "unknown planning route")
            result = self._mutate(routes[path], payload, now)
            # The confirmation intent is the durable commit. Materialization
            # is performed by tick/recovery, never as a fallible read-after-write
            # prerequisite for a reliable API acknowledgement.
            return result
        except PlanningError as exc:
            raise SiteAgentError(exc.code, exc.detail) from exc
        except PreconditionFailed as exc:
            raise SiteAgentError("planning_conflict", f"{exc.code}: {exc.detail}") from exc
        except Exception as exc:
            code = "planning_result_unknown" if method == "POST" else "planning_unavailable"
            raise SiteAgentError(code, f"Verified evidence unavailable; query/retry the original request_id. {type(exc).__name__}: {exc}") from exc
