"""Local human-response evidence over the simulated task exchange.

Notifications are projections of existing durable incident records. Human
responses append evidence only; they never change task, device, or admission
facts. Active uncertainty and conflicts cannot be marked resolved.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .cases import EdgeView, TaskState, derive_edge_view, read_time_liveness
from .contracts import stable_digest, utc_text
from .journal import JournalRecord, PreconditionFailed, RecordSpec

ACKNOWLEDGED = "notification_acknowledged"
RESOLVED = "notification_resolved"
_OPERATOR = re.compile(r"[A-Za-z0-9._-]{1,64}")


def validate_operator(operator: Any) -> str:
    if type(operator) is not str or not _OPERATOR.fullmatch(operator):
        raise PreconditionFailed("invalid_operator", "operator must contain 1–64 letters, digits, dot, underscore, or hyphen")
    return operator


def validate_note(note: Any) -> str:
    if type(note) is not str or not note.strip() or len(note) > 512:
        raise PreconditionFailed("invalid_note", "a non-empty note of at most 512 characters is required")
    return note.strip()


def validate_reference(value: Any, label: str) -> str:
    if type(value) is not str or not value or len(value) > 128:
        raise PreconditionFailed("invalid_reference", f"{label} must be a non-empty identifier of at most 128 characters")
    return value


def _incident(record: JournalRecord) -> dict[str, Any] | None:
    p = record.payload
    kind = record.record_kind
    task_id = p.get("task_id")
    robot_id = p.get("robot_id") or p.get("target_robot_id")
    reason = p.get("reason") or p.get("reason_code") or p.get("code") or kind
    detail = p.get("detail") or reason
    if kind == "task_event_received":
        if p["disposition"] == "duplicate":
            return None
        event = p["event"]
        incomplete_success = event["kind"] == "SUCCEEDED" and (p.get("missing_sequences_after") or not p.get("acceptance_observed_after"))
        if event["kind"] not in {"ASSISTANCE_REQUIRED", "FAILED", "INCONCLUSIVE", "REJECTED"} and not incomplete_success and not p.get("conflict") and p["disposition"] not in {"conflicting_replay", "unexpected_acceptance", "unexpected_rejection"}:
            return None
        reason = "result_evidence_incomplete" if incomplete_success else event.get("reason_code") or event["kind"].lower()
        detail = event.get("detail") or reason
        robot_id = event.get("robot_id") or robot_id
    elif kind == "device_liveness_changed":
        if p["to"] not in {"STALE", "OFFLINE"}:
            return None
        reason = "device_" + p["to"].lower()
        detail = p.get("reason") or reason
    elif kind == "task_create_rejected":
        request = p.get("request")
        if request:
            robot_id = request.get("target_robot_id")
    elif kind not in {"schedule_rejected", "schedule_missed", "task_reconciliation_flagged", "conflicting_terminal", "session_regression", "robot_request_rejected"}:
        return None
    return {
        "notification_id": "notice_" + stable_digest({"source_record_id": record.record_id})[:24],
        "source_record_id": record.record_id,
        "source_kind": kind,
        "source_sequence": record.sequence,
        "robot_id": robot_id,
        "task_id": task_id,
        "schedule_id": p.get("schedule_id"),
        "reason_code": reason,
        "detail": str(detail),
        "created_at_utc": record.recorded_at_utc,
        "status": "OPEN",
        "acknowledged_by": None,
        "acknowledged_at_utc": None,
        "acknowledgement_note": None,
        "resolved_by": None,
        "resolved_at_utc": None,
        "resolution_note": None,
    }


def _condition(incident: dict[str, Any], view: EdgeView, now: datetime) -> tuple[bool, bool]:
    """Return current condition and evidence-based permission to close inbox work."""
    robot_id = incident["robot_id"]
    task = view.tasks.get(incident["task_id"])
    if task is not None:
        robot_id = task.request.target_robot_id
        incident["robot_id"] = robot_id
    if robot_id is not None and view.authorization_block_reason(robot_id) is not None:
        return True, False
    kind = incident["source_kind"]
    if kind == "device_liveness_changed":
        device = view.devices.get(robot_id)
        active = device is None or read_time_liveness(device, view.config, now)["connectivity"] != "ONLINE"
        return active, not active
    if kind == "session_regression" or kind == "conflicting_terminal":
        return True, False
    if kind in {"schedule_rejected", "schedule_missed", "task_create_rejected"}:
        # No new authorization was produced. Closing the notification only
        # records that a person dealt with this unsuccessful attempt.
        return False, True
    if task is None:
        return True, False
    if kind == "task_reconciliation_flagged":
        active = incident["reason_code"] in task.reconciliation_reasons
    else:
        active = task.blocked or not task.is_terminal or task.state is TaskState.INCONCLUSIVE
    # A refusal before acceptance is a complete negative outcome when it has
    # no sequence gaps. It must not require a fabricated ACCEPTED event.
    incomplete = bool(task.missing_sequences) or (task.evidence_incomplete and task.state is not TaskState.REJECTED)
    unresolved = active or task.reconciliation_required or incomplete
    return unresolved, not unresolved


def derive_notifications(records: tuple[JournalRecord, ...], config, now: datetime) -> list[dict[str, Any]]:
    """Derive incidents and responses without mutating any task/device facts."""
    view = derive_edge_view(config, records)
    task_schedules = {
        record.payload["task_id"]: record.payload.get("schedule_id")
        for record in records if record.record_kind == "task_created"
    }
    notices: dict[str, dict[str, Any]] = {}
    for record in records:
        notice = _incident(record)
        if notice is not None:
            if notice["schedule_id"] is None:
                notice["schedule_id"] = task_schedules.get(notice["task_id"])
            notices[notice["notification_id"]] = notice
        elif record.record_kind in {ACKNOWLEDGED, RESOLVED}:
            p = record.payload
            target = notices.get(p["notification_id"])
            if target is None:
                raise PreconditionFailed("orphan_notification_response", "response refers to unavailable incident evidence")
            if record.record_kind == ACKNOWLEDGED:
                target.update(status="ACKNOWLEDGED", acknowledged_by=p["operator"], acknowledged_at_utc=record.recorded_at_utc, acknowledgement_note=p["note"])
            else:
                target.update(status="RESOLVED", resolved_by=p["operator"], resolved_at_utc=record.recorded_at_utc, resolution_note=p["note"])
    for notice in notices.values():
        active, evidence_allows = _condition(notice, view, now)
        notice["condition_active"] = active
        notice["can_resolve"] = evidence_allows and notice["status"] == "ACKNOWLEDGED"
        # A later conflict may make an already handled historical incident
        # unsafe again. Keep the current condition visible beside the response.
        notice["requires_attention"] = active or notice["status"] != "RESOLVED"
    return list(notices.values())


def decide_response(records: tuple[JournalRecord, ...], config, notification_id: str, action: str, operator: str, note: str, now: datetime) -> tuple[dict[str, Any], list[RecordSpec]]:
    notification_id = validate_reference(notification_id, "notification_id")
    operator = validate_operator(operator)
    note = validate_note(note)
    if action not in {"acknowledge", "resolve"}:
        raise PreconditionFailed("invalid_action", "unsupported inbox response")
    notices = {row["notification_id"]: row for row in derive_notifications(records, config, now)}
    target = notices.get(notification_id)
    if target is None:
        raise PreconditionFailed("unknown_notification", "notification does not exist")
    kind = ACKNOWLEDGED if action == "acknowledge" else RESOLVED
    for record in records:
        if record.record_kind == kind and record.payload["notification_id"] == notification_id:
            if record.payload["operator"] == operator and record.payload["note"] == note:
                return {"status": "idempotent", "notification_id": notification_id}, []
            raise PreconditionFailed("response_conflict", "the recorded response is immutable")
    if action == "acknowledge" and target["status"] != "OPEN":
        raise PreconditionFailed("invalid_transition", "only an open notification can be acknowledged")
    if action == "resolve" and not target["can_resolve"]:
        raise PreconditionFailed("resolution_blocked", "acknowledge first; active uncertainty or conflicting evidence cannot be resolved by a note")
    spec = RecordSpec(kind, "OPERATOR", utc_text(now), {"notification_id": notification_id, "source_record_id": target["source_record_id"], "operator": operator, "note": note})
    return {"status": "acknowledged" if action == "acknowledge" else "resolved", "notification_id": notification_id}, [spec]
