"""Durable, single-date SIMULATION schedules over the existing Edge journal.

A schedule is operator intent, not task authorization. At its due time the
service checks the current journal and invokes the existing task admission
function under the same journal lock. The admitted task record itself carries
the schedule identity; there is no separate 'fired' write to lose in a crash.
Transport and clock ownership remain with the caller.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .cases import TASK_CREATED, decide_task_create, derive_edge_view, read_time_liveness
from .contracts import AdmissionFacts, EdgeTaskConfig, EdgeTaskError, TASK_TYPE_COLLECT_BALLS_ZONE, TaskRequest, parse_utc, stable_digest, utc_text
from .inbox import decide_response, derive_notifications, validate_operator, validate_reference
from .journal import JsonlJournal, JournalRecord, PreconditionFailed, RecordSpec

SCHEDULE_SCHEMA = "nxt-edge-schedule/v1"
SNAPSHOT_SCHEMA = "nxt-edge-schedules/v0"
_REQUIRED = frozenset({"robot_id", "zone_id", "due_at_utc", "expires_at_utc", "operator"})
_OPTIONAL = frozenset({"progress_window_s"})
_TERMINAL_KINDS = {"schedule_cancelled": "CANCELLED", "schedule_rejected": "REJECTED", "schedule_missed": "MISSED"}


def _now(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise PreconditionFailed("invalid_time", "now must be an explicit timezone-aware datetime")
    return now.astimezone(timezone.utc)


def _payload(payload: Any, config: EdgeTaskConfig, facts: AdmissionFacts) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or not _REQUIRED <= payload.keys() or payload.keys() - (_REQUIRED | _OPTIONAL):
        raise PreconditionFailed("invalid_schedule", "exact fields are robot_id, zone_id, due_at_utc, expires_at_utc, operator, and optional progress_window_s")
    operator = validate_operator(payload["operator"])
    robot_id, zone_id = payload["robot_id"], payload["zone_id"]
    if type(robot_id) is not str or robot_id not in facts.robot_ids or robot_id not in config.robot_ids:
        raise PreconditionFailed("unknown_robot", "robot_id must be commissioned and configured")
    if type(zone_id) is not str or zone_id not in facts.zone_ids:
        raise PreconditionFailed("unknown_zone", "zone_id must be commissioned")
    try:
        due = parse_utc(payload["due_at_utc"], "due_at_utc")
        expiry = parse_utc(payload["expires_at_utc"], "expires_at_utc")
    except EdgeTaskError as exc:
        raise PreconditionFailed("invalid_schedule", exc.detail) from exc
    if expiry <= due:
        raise PreconditionFailed("invalid_schedule", "expires_at_utc must follow due_at_utc")
    progress = payload.get("progress_window_s", config.default_progress_window_s)
    if type(progress) is not int or not 1 <= progress <= 86_400:
        raise PreconditionFailed("invalid_schedule", "progress_window_s must be an integer from 1 through 86400")
    return {
        "schema": SCHEDULE_SCHEMA,
        "site_id": config.site_id,
        "deployment_id": config.deployment_id,
        "simulation_env_id": config.simulation_env_id,
        "config_digest": config.config_digest,
        "manifest_digest": facts.manifest_digest,
        "robot_id": robot_id,
        "zone_id": zone_id,
        "due_at_utc": utc_text(due),
        "expires_at_utc": utc_text(expiry),
        "operator": operator,
        "progress_window_s": progress,
    }


def derive_schedules(records: tuple[JournalRecord, ...], config: EdgeTaskConfig, facts: AdmissionFacts) -> dict[str, dict[str, Any]]:
    """Reconstruct dated operator intent from verified evidence only."""
    rows: dict[str, dict[str, Any]] = {}
    identity = {
        "site_id": config.site_id, "deployment_id": config.deployment_id,
        "simulation_env_id": config.simulation_env_id,
        "config_digest": config.config_digest, "manifest_digest": facts.manifest_digest,
    }
    for record in records:
        p = record.payload
        if record.record_kind == "edge_started" and any(p.get(key) != value for key, value in identity.items()):
            raise PreconditionFailed("schedule_identity_mismatch", "Edge journal belongs to another deployment, configuration, or manifest")
        if record.record_kind == "schedule_created":
            body = dict(p["schedule"])
            expected = "schedule_" + stable_digest(body)[:24]
            if p["schedule_id"] != expected or body.get("schema") != SCHEDULE_SCHEMA:
                raise PreconditionFailed("schedule_integrity", "schedule content identity or schema disagrees")
            if any(body.get(key) != value for key, value in identity.items()):
                raise PreconditionFailed("schedule_identity_mismatch", "schedule belongs to another deployment, configuration, or manifest")
            if expected in rows:
                raise PreconditionFailed("schedule_integrity", "duplicate schedule creation evidence")
            rows[expected] = {
                **body, "schedule_id": expected, "status": "SCHEDULED", "task_id": None,
                "reason_code": None, "detail": None, "created_at_utc": record.recorded_at_utc,
                "updated_at_utc": record.recorded_at_utc, "cancelled_by": None,
            }
        elif record.record_kind in _TERMINAL_KINDS or (record.record_kind == TASK_CREATED and p.get("schedule_id")):
            row = rows.get(p["schedule_id"])
            if row is None or row["status"] != "SCHEDULED":
                raise PreconditionFailed("schedule_integrity", "schedule outcome has no pending schedule")
            row.update(
                status="DISPATCHED" if record.record_kind == TASK_CREATED else _TERMINAL_KINDS[record.record_kind],
                task_id=p.get("task_id"), reason_code=p.get("reason_code"), detail=p.get("detail"),
                updated_at_utc=record.recorded_at_utc, cancelled_by=p.get("operator") if record.record_kind == "schedule_cancelled" else None,
            )
    return rows


class ScheduleService:
    """Small journal-backed composition API; no worker, transport, or clock."""

    def __init__(self, journal: JsonlJournal, config: EdgeTaskConfig, facts: AdmissionFacts) -> None:
        if (config.site_id, config.deployment_id) != (facts.site_id, facts.deployment_id):
            raise PreconditionFailed("deployment_mismatch", "configuration and admission facts must name one deployment")
        self.journal = journal
        self.config = config
        self.facts = facts

    def create(self, payload: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        now = _now(now)
        body = _payload(payload, self.config, self.facts)
        schedule_id = "schedule_" + stable_digest(body)[:24]
        outcome: dict[str, Any] = {}

        def build(records):
            rows = derive_schedules(records, self.config, self.facts)
            if schedule_id in rows:
                outcome["status"] = "idempotent"
                return []
            if parse_utc(body["expires_at_utc"]) <= now:
                raise PreconditionFailed("schedule_expired", "a new schedule must have an unexpired dispatch window")
            outcome["status"] = "created"
            return [RecordSpec("schedule_created", "OPERATOR", utc_text(now), {"schedule_id": schedule_id, "schedule": body})]

        self.journal.append_via(build)
        outcome["schedule"] = derive_schedules(self.journal.read(), self.config, self.facts)[schedule_id]
        return outcome

    def cancel(self, schedule_id: str, operator: str, now: datetime) -> dict[str, Any]:
        now = _now(now)
        schedule_id = validate_reference(schedule_id, "schedule_id")
        operator = validate_operator(operator)
        outcome: dict[str, Any] = {}

        def build(records):
            row = derive_schedules(records, self.config, self.facts).get(schedule_id)
            if row is None:
                raise PreconditionFailed("unknown_schedule", "schedule does not exist")
            if row["status"] == "CANCELLED" and row["cancelled_by"] == operator:
                outcome["status"] = "idempotent"
                return []
            if row["status"] != "SCHEDULED":
                raise PreconditionFailed("schedule_not_pending", "only a pending schedule can be cancelled; this never cancels an admitted task")
            outcome["status"] = "cancelled"
            return [RecordSpec("schedule_cancelled", "OPERATOR", utc_text(now), {"schedule_id": schedule_id, "operator": operator})]

        self.journal.append_via(build)
        outcome["schedule"] = derive_schedules(self.journal.read(), self.config, self.facts)[schedule_id]
        return outcome

    def _due(self, records: tuple[JournalRecord, ...], schedule_id: str, now: datetime) -> list[RecordSpec]:
        row = derive_schedules(records, self.config, self.facts).get(schedule_id)
        if row is None or row["status"] != "SCHEDULED" or now < parse_utc(row["due_at_utc"]):
            return []

        def refused(code: str, detail: str, *, missed: bool = False) -> list[RecordSpec]:
            return [RecordSpec("schedule_missed" if missed else "schedule_rejected", "EDGE", utc_text(now), {
                "schedule_id": schedule_id, "robot_id": row["robot_id"], "task_id": None,
                "reason_code": code, "code": code, "detail": detail,
            })]

        if now >= parse_utc(row["expires_at_utc"]):
            return refused("dispatch_window_missed", "The dispatch window elapsed before admission; no task was created.", missed=True)
        view = derive_edge_view(self.config, records)
        device = view.devices[row["robot_id"]]
        if read_time_liveness(device, self.config, now)["connectivity"] != "ONLINE" or device.connectivity.value != "ONLINE":
            return refused("device_not_online", "A fresh online heartbeat is required at the scheduled time.")
        status = device.last_valid_status
        if status is None or status["availability"] != "available":
            return refused("device_not_available", "The robot has not reported availability at the scheduled time.")
        # This is a conservative local precondition; the protocol double still
        # owns its admission and protection when it receives the request.
        if status["safety"]["estop_latched"] is not False or status["safety"]["awaiting_human"] is not False:
            return refused("device_safety_unconfirmed", "The latest robot status does not confirm a clear local protection state.")
        if device.incarnation is None:
            return refused("device_incarnation_unknown", "No robot incarnation is available for task binding.")
        request = TaskRequest.build(
            site_id=self.config.site_id, deployment_id=self.config.deployment_id,
            simulation_env_id=self.config.simulation_env_id, target_robot_id=row["robot_id"],
            target_incarnation=device.incarnation, task_type=TASK_TYPE_COLLECT_BALLS_ZONE,
            zone_id=row["zone_id"], issued_at_utc=row["due_at_utc"], expires_at_utc=row["expires_at_utc"],
            progress_window_s=row["progress_window_s"], issued_by="SIMULATION_TEST_ENTRY:" + row["operator"],
        )
        outcome, specs = decide_task_create(view, self.facts, request, now)
        if outcome.status != "created":
            return refused(outcome.code or "task_already_exists", outcome.detail or "An identical task already exists; the schedule did not create a second authorization.")
        # One authoritative line binds this schedule to the admitted task.
        # A crash after it cannot create a second task on a new incarnation.
        created = specs[0]
        return [RecordSpec(created.record_kind, created.origin, created.recorded_at_utc, {**created.payload, "schedule_id": schedule_id})]

    def tick(self, now: datetime) -> None:
        now = _now(now)
        rows = derive_schedules(self.journal.read(), self.config, self.facts)
        ordered = sorted(rows.values(), key=lambda row: (row["due_at_utc"], row["schedule_id"]))
        for row in ordered:
            if row["status"] == "SCHEDULED" and parse_utc(row["due_at_utc"]) <= now:
                self.journal.append_via(lambda records, sid=row["schedule_id"]: self._due(records, sid, now))

    def snapshot(self, now: datetime) -> dict[str, Any]:
        now = _now(now)
        records = self.journal.read()
        schedules = sorted(derive_schedules(records, self.config, self.facts).values(), key=lambda row: (row["due_at_utc"], row["schedule_id"]))
        return {
            "schema": SNAPSHOT_SCHEMA, "environment": "SIMULATION",
            "site_id": self.config.site_id, "deployment_id": self.config.deployment_id,
            "schedules": schedules, "notifications": derive_notifications(records, self.config, now),
        }

    def _respond(self, notification_id: str, action: str, operator: str, note: str, now: datetime) -> dict[str, Any]:
        now = _now(now)
        outcome: dict[str, Any] = {}

        def build(records):
            result, specs = decide_response(records, self.config, notification_id, action, operator, note, now)
            outcome.update(result)
            return specs

        self.journal.append_via(build)
        outcome["notification"] = next(row for row in derive_notifications(self.journal.read(), self.config, now) if row["notification_id"] == notification_id)
        return outcome

    def acknowledge(self, notification_id: str, operator: str, note: str, now: datetime) -> dict[str, Any]:
        return self._respond(notification_id, "acknowledge", operator, note, now)

    def resolve(self, notification_id: str, operator: str, note: str, now: datetime) -> dict[str, Any]:
        return self._respond(notification_id, "resolve", operator, note, now)
