"""Human-handling cases and notification intents derived from the Edge view.

The intervention view is mutated only by applying journal records, so replay
equals live.  ``reconcile`` compares a fresh Edge snapshot with the view and
returns the records to append; ``decide_tick`` schedules reminders,
escalation notifications, and delivery retries; ``decide_acknowledge`` and
``decide_resolve`` validate operator actions.  Nothing here writes task or
device state, and no case transition changes any authorization.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .contracts import (
    ACKNOWLEDGED,
    ATTEMPT_RESULTS,
    ATTEMPTING,
    CRITICAL,
    DELIVERED,
    DEVICE_OFFLINE,
    EFFECTIVE_CONFLICT,
    EVIDENCE_CONFLICT_REASONS,
    EXHAUSTED,
    FAILED,
    INTENT_ESCALATION,
    INTENT_OPENED,
    INTENT_REMINDER,
    NOTIFICATION_SCHEMA,
    OPEN,
    PENDING,
    RESOLVED,
    RESULT_DELIVERED,
    RESULT_FAILED,
    RESULT_UNKNOWN,
    SEVERITY_RANK,
    TASK_BLOCKED,
    TASK_TERMINAL_STATES,
    UNKNOWN,
    WARNING,
    CaseKind,
    EdgeSnapshot,
    InterventionConfig,
    InterventionError,
    bounded_text,
    case_identity,
    identifier,
    notification_identity,
    parse_utc,
    render_message,
    utc_text,
)

# ---------------------------------------------------------------------------
# Record vocabulary (this package's own journal)
# ---------------------------------------------------------------------------

SERVICE_STARTED = "intervention_service_started"
CASE_OPENED = "case_opened"
CASE_EVIDENCE_ADDED = "case_evidence_added"
CASE_ESCALATED = "case_escalated"
CASE_CONDITION_CLEARED = "case_condition_cleared"
CASE_CONDITION_RETURNED = "case_condition_returned"
CASE_ACKNOWLEDGED = "case_acknowledged"
CASE_RESOLVED = "case_resolved"
OPERATOR_ACTION_REJECTED = "operator_action_rejected"
NOTIFICATION_INTENDED = "notification_intended"
NOTIFICATION_ATTEMPTED = "notification_attempted"
NOTIFICATION_RESULT = "notification_result"
NOTIFICATION_EXHAUSTED = "notification_exhausted"
REMINDERS_EXHAUSTED = "reminders_exhausted"

INTERVENTION_RECORD_KINDS = frozenset(
    {
        SERVICE_STARTED,
        CASE_OPENED,
        CASE_EVIDENCE_ADDED,
        CASE_ESCALATED,
        CASE_CONDITION_CLEARED,
        CASE_CONDITION_RETURNED,
        CASE_ACKNOWLEDGED,
        CASE_RESOLVED,
        OPERATOR_ACTION_REJECTED,
        NOTIFICATION_INTENDED,
        NOTIFICATION_ATTEMPTED,
        NOTIFICATION_RESULT,
        NOTIFICATION_EXHAUSTED,
        REMINDERS_EXHAUSTED,
    }
)


@dataclass(frozen=True)
class RecordSpec:
    """What the journal port appends (same shape as the upstream journal spec)."""

    record_kind: str
    origin: str
    recorded_at_utc: str
    payload: Mapping[str, Any]


def _spec(kind: str, origin: str, now: datetime, payload: Mapping[str, Any]) -> RecordSpec:
    return RecordSpec(record_kind=kind, origin=origin, recorded_at_utc=utc_text(now), payload=dict(payload))


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@dataclass
class NotificationView:
    notification_id: str
    case_id: str
    intent: str
    severity: str
    ordinal: int
    intended_at: datetime
    message: str
    payload: dict[str, Any]
    attempts: int = 0
    last_attempt_at: datetime | None = None
    last_result: str | None = None
    last_result_at: datetime | None = None
    receipt_id: str | None = None
    state: str = PENDING

    @property
    def open_attempt(self) -> bool:
        """An attempt was journaled and no result followed (crash or in flight)."""

        return self.state == ATTEMPTING

    def summary(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "case_id": self.case_id,
            "intent": self.intent,
            "severity": self.severity,
            "state": self.state,
            "attempts": self.attempts,
            "last_attempt_at_utc": None if self.last_attempt_at is None else utc_text(self.last_attempt_at),
            "last_result": self.last_result,
            "receipt_id": self.receipt_id,
            "message": self.message,
        }


@dataclass
class CaseView:
    case_id: str
    kind: str
    subject_kind: str  # "task" | "device"
    subject_id: str
    robot_id: str
    severity: str
    evidence_key: Any
    evidence: dict[str, Any]
    opened_at: datetime
    human_state: str = OPEN
    condition_active: bool = True
    evidence_history: list[dict[str, Any]] = field(default_factory=list)
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    acknowledge_note: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    resolution: str | None = None
    recurrence_of: str | None = None
    notification_ids: list[str] = field(default_factory=list)
    reminders_sent: int = 0
    reminders_exhausted: bool = False
    last_intent_at: datetime | None = None

    @property
    def is_resolved(self) -> bool:
        return self.human_state == RESOLVED

    def summary(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "kind": self.kind,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "robot_id": self.robot_id,
            "severity": self.severity,
            "human_state": self.human_state,
            "condition_active": self.condition_active,
            "opened_at_utc": utc_text(self.opened_at),
            "evidence": dict(self.evidence),
            "evidence_history_count": len(self.evidence_history),
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at_utc": None if self.acknowledged_at is None else utc_text(self.acknowledged_at),
            "acknowledge_note": self.acknowledge_note,
            "resolved_by": self.resolved_by,
            "resolved_at_utc": None if self.resolved_at is None else utc_text(self.resolved_at),
            "resolution": self.resolution,
            "recurrence_of": self.recurrence_of,
            "notification_ids": list(self.notification_ids),
            "reminders_sent": self.reminders_sent,
            "reminders_exhausted": self.reminders_exhausted,
            # Human handling never changes these; they are restated so a reader
            # cannot mistake a resolved case for a healthy device or a
            # reopened authorization.
            "authorization_effect": "none",
            "device_state_effect": "none",
        }


@dataclass
class InterventionView:
    config: InterventionConfig
    cases: dict[str, CaseView] = field(default_factory=dict)
    notifications: dict[str, NotificationView] = field(default_factory=dict)
    # Derived from durable case events, removed when their intent is replayed.
    # V0 has one opening and at most one severity rise per case. Keep the event's
    # payload/time/ordinal so a torn batch repairs the original notification.
    pending_intents: dict[tuple[str, str], RecordSpec] = field(default_factory=dict)
    applied_count: int = 0
    last_record_id: str | None = None
    started_at: datetime | None = None

    # ---- queries --------------------------------------------------------

    def open_case_for(self, kind: str, subject_kind: str, subject_id: str) -> CaseView | None:
        for case in self.cases.values():
            if case.kind == kind and case.subject_kind == subject_kind and case.subject_id == subject_id and not case.is_resolved:
                return case
        return None

    def latest_case_for(self, kind: str, subject_kind: str, subject_id: str) -> CaseView | None:
        latest: CaseView | None = None
        for case in self.cases.values():
            if case.kind == kind and case.subject_kind == subject_kind and case.subject_id == subject_id:
                if latest is None or case.opened_at >= latest.opened_at:
                    latest = case
        return latest

    def notifications_for(self, case_id: str) -> list[NotificationView]:
        return [self.notifications[n] for n in self.cases[case_id].notification_ids]

    def snapshot(self) -> dict[str, Any]:
        return {
            "cases": {case_id: case.summary() for case_id, case in self.cases.items()},
            "notifications": {nid: n.summary() for nid, n in self.notifications.items()},
            "last_record_id": self.last_record_id,
        }

    # ---- applying records ----------------------------------------------

    def apply_all(self, records: Sequence[Any]) -> None:
        for record in records[self.applied_count :]:
            self.apply(record)

    def apply(self, record: Any) -> None:
        kind = record.record_kind
        payload = record.payload
        when = parse_utc(record.recorded_at_utc, "recorded_at_utc")
        if kind == SERVICE_STARTED:
            self.started_at = when
        elif kind == CASE_OPENED:
            case = CaseView(
                case_id=payload["case_id"],
                kind=payload["kind"],
                subject_kind=payload["subject_kind"],
                subject_id=payload["subject_id"],
                robot_id=payload["robot_id"],
                severity=payload["severity"],
                evidence_key=_thaw(payload["evidence_key"]),
                evidence=_thaw(payload["evidence"]),
                opened_at=when,
                recurrence_of=payload.get("recurrence_of"),
            )
            case.evidence_history.append({"at_utc": record.recorded_at_utc, "evidence_key": case.evidence_key, "record_id": record.record_id})
            self.cases[case.case_id] = case
            self.pending_intents[(case.case_id, INTENT_OPENED)] = _intent_specs(self, case, INTENT_OPENED, when, severity=case.severity, evidence=case.evidence)[0]
        elif kind == CASE_EVIDENCE_ADDED:
            case = self.cases[payload["case_id"]]
            case.evidence = _thaw(payload["evidence"])
            case.evidence_key = _thaw(payload["evidence_key"])
            case.evidence_history.append({"at_utc": record.recorded_at_utc, "evidence_key": case.evidence_key, "record_id": record.record_id})
        elif kind == CASE_ESCALATED:
            case = self.cases[payload["case_id"]]
            case.severity = payload["severity"]
            case.evidence = _thaw(payload["evidence"])
            case.evidence_key = _thaw(payload["evidence_key"])
            case.evidence_history.append({"at_utc": record.recorded_at_utc, "evidence_key": _thaw(payload["evidence_key"]), "record_id": record.record_id, "escalated_to": payload["severity"]})
            self.pending_intents[(case.case_id, INTENT_ESCALATION)] = _intent_specs(self, case, INTENT_ESCALATION, when, severity=case.severity, evidence=case.evidence)[0]
        elif kind == CASE_CONDITION_CLEARED:
            self.cases[payload["case_id"]].condition_active = False
        elif kind == CASE_CONDITION_RETURNED:
            case = self.cases[payload["case_id"]]
            case.condition_active = True
            case.evidence = _thaw(payload["evidence"])
            case.evidence_key = _thaw(payload["evidence_key"])
            case.evidence_history.append({"at_utc": record.recorded_at_utc, "evidence_key": case.evidence_key, "record_id": record.record_id})
        elif kind == CASE_ACKNOWLEDGED:
            case = self.cases[payload["case_id"]]
            case.human_state = ACKNOWLEDGED
            case.acknowledged_by = payload["operator_id"]
            case.acknowledged_at = when
            case.acknowledge_note = payload["note"]
        elif kind == CASE_RESOLVED:
            case = self.cases[payload["case_id"]]
            case.human_state = RESOLVED
            case.resolved_by = payload["operator_id"]
            case.resolved_at = when
            case.resolution = payload["resolution"]
        elif kind == OPERATOR_ACTION_REJECTED:
            pass  # visible in the journal; changes no state
        elif kind == NOTIFICATION_INTENDED:
            notification = NotificationView(
                notification_id=payload["notification_id"],
                case_id=payload["case_id"],
                intent=payload["intent"],
                severity=payload["severity"],
                ordinal=payload["ordinal"],
                intended_at=when,
                message=payload["message"],
                payload=_thaw(payload["payload"]),
            )
            self.notifications[notification.notification_id] = notification
            self.pending_intents.pop((notification.case_id, notification.intent), None)
            case = self.cases[notification.case_id]
            case.notification_ids.append(notification.notification_id)
            case.last_intent_at = max(case.last_intent_at, when) if case.last_intent_at is not None else when
            if notification.intent == INTENT_REMINDER:
                case.reminders_sent += 1
        elif kind == NOTIFICATION_ATTEMPTED:
            notification = self.notifications[payload["notification_id"]]
            notification.attempts = payload["attempt"]
            notification.last_attempt_at = when
            notification.state = ATTEMPTING
        elif kind == NOTIFICATION_RESULT:
            notification = self.notifications[payload["notification_id"]]
            notification.last_result = payload["result"]
            notification.last_result_at = when
            if payload["result"] == RESULT_DELIVERED:
                notification.state = DELIVERED
                notification.receipt_id = payload.get("receipt_id")
            elif payload["result"] == RESULT_UNKNOWN:
                notification.state = UNKNOWN
            else:
                notification.state = FAILED
        elif kind == NOTIFICATION_EXHAUSTED:
            self.notifications[payload["notification_id"]].state = EXHAUSTED
        elif kind == REMINDERS_EXHAUSTED:
            self.cases[payload["case_id"]].reminders_exhausted = True
        else:
            raise InterventionError("unknown_record_kind", f"cannot apply record kind {kind!r}")
        self.applied_count += 1
        self.last_record_id = record.record_id


def derive_view(config: InterventionConfig, records: Sequence[Any]) -> InterventionView:
    view = InterventionView(config=config)
    view.apply_all(records)
    return view


# ---------------------------------------------------------------------------
# Deriving cases from the Edge snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Observed:
    kind: str
    subject_kind: str
    subject_id: str
    robot_id: str
    severity: str
    evidence_key: Any
    evidence: dict[str, Any]


def _task_open(task: Mapping[str, Any]) -> bool:
    return task["state"] not in TASK_TERMINAL_STATES


def observe(snapshot: EdgeSnapshot) -> list[_Observed]:
    """Which conditions the Edge evidence supports right now (pure)."""

    found: list[_Observed] = []
    open_task_by_robot: dict[str, Mapping[str, Any]] = {}
    for task in snapshot.tasks.values():
        if _task_open(task):
            open_task_by_robot[task["target_robot_id"]] = task

    for task_id, task in snapshot.tasks.items():
        robot_id = task["target_robot_id"]
        blocking = task["blocking_event"]
        if task["state"] == TASK_BLOCKED and isinstance(blocking, Mapping):
            key = [blocking.get("boot_sequence"), blocking.get("event_sequence")]
            found.append(
                _Observed(
                    kind=CaseKind.ASSISTANCE_REQUIRED.value,
                    subject_kind="task",
                    subject_id=task_id,
                    robot_id=robot_id,
                    severity=CRITICAL if str(blocking.get("reason_code", "")).startswith("unknown:") else WARNING,
                    evidence_key=key,
                    evidence={
                        "task_id": task_id,
                        "robot_id": robot_id,
                        "reason_code": blocking.get("reason_code"),
                        "event_record_id": blocking.get("record_id"),
                        "event_received_at_utc": blocking.get("received_at_utc"),
                        "task_state": task["state"],
                    },
                )
            )
        conflict_reasons = sorted(r for r in task["reconciliation_reasons"] if r in EVIDENCE_CONFLICT_REASONS)
        if task["effective_result"] == EFFECTIVE_CONFLICT or conflict_reasons:
            found.append(
                _Observed(
                    kind=CaseKind.EVIDENCE_CONFLICT.value,
                    subject_kind="task",
                    subject_id=task_id,
                    robot_id=robot_id,
                    severity=CRITICAL,
                    evidence_key={"reasons": conflict_reasons, "effective_result": task["effective_result"]},
                    evidence={
                        "subject_kind": "task",
                        "subject_id": task_id,
                        "task_id": task_id,
                        "robot_id": robot_id,
                        "detail": ", ".join(conflict_reasons) or "conflicting terminals",
                        "effective_result": task["effective_result"],
                        "task_state": task["state"],
                    },
                )
            )
        exhausted = _task_open(task) and task["publish_attempts"] >= snapshot.edge["max_republish_attempts"]
        if _task_open(task) and (exhausted or task["expired_unconfirmed"]):
            detail = "republish budget exhausted" if exhausted else "expired without confirmation"
            found.append(
                _Observed(
                    kind=CaseKind.RESULT_UNCONFIRMED.value,
                    subject_kind="task",
                    subject_id=task_id,
                    robot_id=robot_id,
                    severity=WARNING,
                    evidence_key={"detail": detail, "publish_attempts": task["publish_attempts"]},
                    evidence={
                        "task_id": task_id,
                        "robot_id": robot_id,
                        "task_state": task["state"],
                        "publish_attempts": task["publish_attempts"],
                        "expires_at_utc": task["expires_at_utc"],
                        "detail": detail,
                    },
                )
            )

    for robot_id, device in snapshot.devices.items():
        if device["connectivity"] == DEVICE_OFFLINE:
            open_task = open_task_by_robot.get(robot_id)
            current = device["current_task"]
            found.append(
                _Observed(
                    kind=CaseKind.DEVICE_UNREACHABLE.value,
                    subject_kind="device",
                    subject_id=robot_id,
                    robot_id=robot_id,
                    severity=CRITICAL if open_task is not None else WARNING,
                    evidence_key=[device["connectivity_since_utc"]] + ([] if open_task is None else [open_task["task_id"]]),
                    evidence={
                        "robot_id": robot_id,
                        "offline_since_utc": device["connectivity_since_utc"],
                        "last_valid_status_received_at_utc": device["last_valid_status_received_at_utc"],
                        "last_reported_availability": device["last_reported_availability"],
                        "current_task_id": None if not isinstance(current, Mapping) else current.get("task_id"),
                        "open_task_id": None if open_task is None else open_task["task_id"],
                        "state_unknown": True,
                        "stopped_confirmed": False,
                    },
                )
            )
        if device["session_regression"]:
            found.append(
                _Observed(
                    kind=CaseKind.EVIDENCE_CONFLICT.value,
                    subject_kind="device",
                    subject_id=robot_id,
                    robot_id=robot_id,
                    severity=CRITICAL,
                    evidence_key={"session_regression": True},
                    evidence={
                        "subject_kind": "device",
                        "subject_id": robot_id,
                        "robot_id": robot_id,
                        "detail": "session_regression",
                        "task_id": None,
                    },
                )
            )
    return found


def _intent_specs(view: InterventionView, case: CaseView, intent: str, now: datetime, *, severity: str, evidence: Mapping[str, Any]) -> list[RecordSpec]:
    ordinal = len(case.notification_ids)
    notification_id = notification_identity(case.case_id, intent, ordinal)
    message = render_message(case.kind, severity, {**evidence, "severity": severity, "kind": case.kind})
    payload = {
        "schema": NOTIFICATION_SCHEMA,
        "environment": {"kind": "SIMULATION", "simulation_env_id": view.config.simulation_env_id},
        "site_id": view.config.site_id,
        "deployment_id": view.config.deployment_id,
        "notification_id": notification_id,
        "case_id": case.case_id,
        "intent": intent,
        "kind": case.kind,
        "severity": severity,
        "subject_kind": case.subject_kind,
        "subject_id": case.subject_id,
        "robot_id": case.robot_id,
        "human_state": case.human_state,
        "message": message,
        "evidence": dict(evidence),
    }
    return [
        _spec(
            NOTIFICATION_INTENDED,
            "EDGE",
            now,
            {"notification_id": notification_id, "case_id": case.case_id, "intent": intent, "severity": severity, "ordinal": ordinal, "message": message, "payload": payload},
        )
    ]


def reconcile(view: InterventionView, snapshot: EdgeSnapshot, now: datetime) -> list[RecordSpec]:
    """Compare the Edge evidence with the intervention view; return the records to append.

    - A condition with new identity opens a case (a recurrence after a
      RESOLVED case names the previous one).
    - The same identity never opens a second case and never re-notifies.
    - New evidence on an open case is recorded; a severity rise escalates and
      notifies once per rise.
    - A condition that is no longer supported by the evidence is recorded as
      cleared on the open case; the case stays until a human resolves it.
    """

    if view.pending_intents:
        # Complete durable obligations before deriving newer evidence/ordinals.
        # Human ack/resolve does not cancel an already committed escalation.
        return list(view.pending_intents.values())

    specs: list[RecordSpec] = []
    observed = observe(snapshot)
    seen_open: set[str] = set()
    for item in observed:
        existing = view.open_case_for(item.kind, item.subject_kind, item.subject_id)
        if existing is None:
            previous = view.latest_case_for(item.kind, item.subject_kind, item.subject_id)
            if previous is not None and previous.is_resolved and _same_evidence(previous, item):
                # Same evidence the human already resolved: not a recurrence.
                continue
            case_id = case_identity(item.kind, item.subject_kind, item.subject_id, item.evidence_key)
            if case_id in view.cases:
                continue  # identical identity already recorded (resolved); never reopened
            specs.append(
                _spec(
                    CASE_OPENED,
                    "EDGE",
                    now,
                    {
                        "case_id": case_id,
                        "kind": item.kind,
                        "subject_kind": item.subject_kind,
                        "subject_id": item.subject_id,
                        "robot_id": item.robot_id,
                        "severity": item.severity,
                        "evidence_key": item.evidence_key,
                        "evidence": item.evidence,
                        "recurrence_of": None if previous is None else previous.case_id,
                        "edge_last_record_id": snapshot.edge["last_record_id"],
                    },
                )
            )
            shadow = CaseView(
                case_id=case_id,
                kind=item.kind,
                subject_kind=item.subject_kind,
                subject_id=item.subject_id,
                robot_id=item.robot_id,
                severity=item.severity,
                evidence_key=item.evidence_key,
                evidence=item.evidence,
                opened_at=now,
            )
            specs.extend(_intent_specs(view, shadow, INTENT_OPENED, now, severity=item.severity, evidence=item.evidence))
            seen_open.add(case_id)
            continue
        seen_open.add(existing.case_id)
        if not existing.condition_active:
            specs.append(_spec(CASE_CONDITION_RETURNED, "EDGE", now, {"case_id": existing.case_id, "evidence_key": item.evidence_key, "evidence": item.evidence}))
            existing = _copy_case(existing, condition_active=True, evidence_key=item.evidence_key, evidence=item.evidence)
        if SEVERITY_RANK[item.severity] > SEVERITY_RANK[existing.severity]:
            specs.append(_spec(CASE_ESCALATED, "EDGE", now, {"case_id": existing.case_id, "severity": item.severity, "previous_severity": existing.severity, "evidence_key": item.evidence_key, "evidence": item.evidence}))
            escalated = _copy_case(existing, severity=item.severity, evidence_key=item.evidence_key, evidence=item.evidence)
            specs.extend(_intent_specs(view, escalated, INTENT_ESCALATION, now, severity=item.severity, evidence=item.evidence))
        elif item.evidence_key != existing.evidence_key:
            specs.append(_spec(CASE_EVIDENCE_ADDED, "EDGE", now, {"case_id": existing.case_id, "evidence_key": item.evidence_key, "evidence": item.evidence}))
    for case in view.cases.values():
        if case.is_resolved:
            continue
        if case.case_id in seen_open or not case.condition_active:
            continue
        specs.append(_spec(CASE_CONDITION_CLEARED, "EDGE", now, {"case_id": case.case_id, "edge_last_record_id": snapshot.edge["last_record_id"]}))
    return specs


def _same_evidence(case: CaseView, item: _Observed) -> bool:
    if item.kind == CaseKind.DEVICE_UNREACHABLE.value:
        # Pre-fix PR B records keyed only on the offline transition, but
        # already carried open_task_id in evidence. Losing task occupancy in
        # the same offline episode is not recurrence; a different new task is.
        same_episode = case.evidence["offline_since_utc"] == item.evidence["offline_since_utc"]
        observed_task = item.evidence["open_task_id"]
        return same_episode and (observed_task is None or observed_task == case.evidence["open_task_id"])
    return case.evidence_key == item.evidence_key


def _copy_case(case: CaseView, **changes: Any) -> CaseView:
    data = {**case.__dict__, **changes}
    data["evidence_history"] = list(case.evidence_history)
    data["notification_ids"] = list(case.notification_ids)
    return CaseView(**data)


# ---------------------------------------------------------------------------
# Time-driven decisions: reminders, delivery retries
# ---------------------------------------------------------------------------


def decide_tick(view: InterventionView, now: datetime) -> list[RecordSpec]:
    """Reminders for unacknowledged cases (bounded) and exhaustion of delivery."""

    specs: list[RecordSpec] = []
    config = view.config
    for case in view.cases.values():
        if case.human_state != OPEN or case.reminders_exhausted:
            continue  # acknowledged: no nagging; resolved: nothing
        if case.last_intent_at is None:
            continue
        if (now - case.last_intent_at).total_seconds() < config.reminder_interval_s:
            continue
        if case.reminders_sent >= config.max_reminders:
            specs.append(_spec(REMINDERS_EXHAUSTED, "EDGE", now, {"case_id": case.case_id, "reminders_sent": case.reminders_sent, "max_reminders": config.max_reminders}))
            continue
        specs.extend(_intent_specs(view, case, INTENT_REMINDER, now, severity=case.severity, evidence=case.evidence))
    for notification in view.notifications.values():
        if notification.state in {DELIVERED, EXHAUSTED}:
            continue
        if notification.state in {UNKNOWN, FAILED, ATTEMPTING} and notification.attempts >= config.notify_max_attempts:
            if notification.state == ATTEMPTING:
                # A crash between the attempt and its result: the last attempt
                # has no outcome and no budget remains; never call it delivered.
                specs.append(_spec(NOTIFICATION_RESULT, "CHANNEL", now, {"notification_id": notification.notification_id, "attempt": notification.attempts, "result": RESULT_UNKNOWN, "receipt_id": None, "detail": "no result recorded for the last attempt"}))
            specs.append(_spec(NOTIFICATION_EXHAUSTED, "CHANNEL", now, {"notification_id": notification.notification_id, "attempts": notification.attempts, "last_result": notification.last_result or RESULT_UNKNOWN}))
    return specs


# ---------------------------------------------------------------------------
# Operator actions
# ---------------------------------------------------------------------------


def _operator(payload: Mapping[str, Any]) -> tuple[str, str]:
    operator_id = identifier(payload.get("operator_id"), "operator_id")
    note = bounded_text(payload.get("note"), "note")
    return operator_id, note


def decide_acknowledge(view: InterventionView, case_id: str, operator_id: str, note: str, now: datetime) -> tuple[bool, list[RecordSpec]]:
    """OPEN -> ACKNOWLEDGED.  Records who took the case; changes nothing else."""

    try:
        operator_id, note = _operator({"operator_id": operator_id, "note": note})
        case = view.cases.get(case_id)
        if case is None:
            raise InterventionError("unknown_case", f"no case {case_id}")
        if case.human_state != OPEN:
            raise InterventionError("invalid_transition", f"case {case_id} is {case.human_state}, not OPEN")
    except InterventionError as exc:
        return False, [_spec(OPERATOR_ACTION_REJECTED, "OPERATOR", now, {"action": "acknowledge", "case_id": case_id, "code": exc.code, "detail": exc.detail})]
    return True, [_spec(CASE_ACKNOWLEDGED, "OPERATOR", now, {"case_id": case_id, "operator_id": operator_id, "note": note})]


def decide_resolve(view: InterventionView, case_id: str, operator_id: str, resolution: str, now: datetime) -> tuple[bool, list[RecordSpec]]:
    """ACKNOWLEDGED -> RESOLVED.  A human record of disposition, nothing more.

    Resolution requires a prior acknowledgement (someone took the case) and a
    non-empty disposition text.  It does not attest that the device is
    healthy, does not reopen authorization, and leaves an active condition
    visible (``condition_active`` on the case).
    """

    try:
        operator_id, resolution = _operator({"operator_id": operator_id, "note": resolution})
        case = view.cases.get(case_id)
        if case is None:
            raise InterventionError("unknown_case", f"no case {case_id}")
        if case.human_state != ACKNOWLEDGED:
            raise InterventionError("invalid_transition", f"case {case_id} is {case.human_state}; resolve requires ACKNOWLEDGED")
    except InterventionError as exc:
        return False, [_spec(OPERATOR_ACTION_REJECTED, "OPERATOR", now, {"action": "resolve", "case_id": case_id, "code": exc.code, "detail": exc.detail})]
    return True, [
        _spec(
            CASE_RESOLVED,
            "OPERATOR",
            now,
            {
                "case_id": case_id,
                "operator_id": operator_id,
                "resolution": resolution,
                "condition_active_at_resolution": case.condition_active,
                "authorization_effect": "none",
                "device_state_effect": "none",
            },
        )
    ]


# ---------------------------------------------------------------------------
# Core: builder with journal precondition (replay == live)
# ---------------------------------------------------------------------------


class InterventionCore:
    """Holds the live view and turns decision functions into in-lock builders."""

    def __init__(self, config: InterventionConfig) -> None:
        self.config = config
        self.view = InterventionView(config=config)

    def _sync(self, records: Sequence[Any]) -> None:
        if len(records) < self.view.applied_count:
            raise InterventionError("journal_shrank", "the intervention journal has fewer records than were applied")
        if self.view.applied_count and records[self.view.applied_count - 1].record_id != self.view.last_record_id:
            raise InterventionError("journal_diverged", "the intervention journal diverged from the live view")
        self.view.apply_all(records)

    def builder(self, decide: Callable[[InterventionView], Sequence[RecordSpec]]) -> Callable[[Sequence[Any]], list[RecordSpec]]:
        def build(records: Sequence[Any]) -> list[RecordSpec]:
            self._sync(records)
            return list(decide(self.view))

        return build

    def absorb(self, appended: Sequence[Any]) -> None:
        for record in appended:
            self.view.apply(record)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def started_spec(config: InterventionConfig, now: datetime) -> RecordSpec:
    return _spec(SERVICE_STARTED, "EDGE", now, {"site_id": config.site_id, "deployment_id": config.deployment_id, "receiver_id": config.receiver.receiver_id})


def attempt_spec(notification: NotificationView, now: datetime) -> RecordSpec:
    return _spec(NOTIFICATION_ATTEMPTED, "CHANNEL", now, {"notification_id": notification.notification_id, "attempt": notification.attempts + 1})


def result_spec(notification: NotificationView, result: str, now: datetime, *, receipt_id: str | None, detail: str) -> RecordSpec:
    if result not in ATTEMPT_RESULTS:
        raise InterventionError("invalid_result", f"unknown attempt result {result!r}")
    if result == RESULT_DELIVERED and not receipt_id:
        raise InterventionError("invalid_result", "a delivered result needs the receiver's receipt id")
    if result != RESULT_DELIVERED:
        receipt_id = None
    return _spec(NOTIFICATION_RESULT, "CHANNEL", now, {"notification_id": notification.notification_id, "attempt": notification.attempts, "result": result, "receipt_id": receipt_id, "detail": bounded_text(detail or "-", "detail", maximum=500)})


__all__ = [
    "CASE_ACKNOWLEDGED",
    "CASE_CONDITION_CLEARED",
    "CASE_CONDITION_RETURNED",
    "CASE_ESCALATED",
    "CASE_EVIDENCE_ADDED",
    "CASE_OPENED",
    "CASE_RESOLVED",
    "INTERVENTION_RECORD_KINDS",
    "NOTIFICATION_ATTEMPTED",
    "NOTIFICATION_EXHAUSTED",
    "NOTIFICATION_INTENDED",
    "NOTIFICATION_RESULT",
    "OPERATOR_ACTION_REJECTED",
    "REMINDERS_EXHAUSTED",
    "SERVICE_STARTED",
    "CaseView",
    "InterventionCore",
    "InterventionView",
    "NotificationView",
    "RecordSpec",
    "attempt_spec",
    "decide_acknowledge",
    "decide_resolve",
    "decide_tick",
    "derive_view",
    "observe",
    "reconcile",
    "result_spec",
    "started_spec",
]
