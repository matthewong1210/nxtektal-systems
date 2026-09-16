"""Delivery scheduling (sender side) and receipt ledger (receiver side).

Both sides are pure decisions over their own journals.  The sender never
reads the receiver's storage and the receiver never reads the sender's: a
receipt exists only because the receiver persisted the notification and
answered; the sender's record of that answer is the only thing that turns an
attempt into ``DELIVERED``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .cases import NotificationView, RecordSpec, _spec
from .contracts import (
    ATTEMPTING,
    DELIVERED,
    EXHAUSTED,
    NOTIFICATION_SCHEMA,
    PENDING,
    InterventionConfig,
    InterventionError,
    exact_keys,
    identifier,
    stable_digest,
    utc_text,
)

_NOTIFICATION_KEYS = frozenset(
    {
        "schema",
        "environment",
        "site_id",
        "deployment_id",
        "notification_id",
        "case_id",
        "intent",
        "kind",
        "severity",
        "subject_kind",
        "subject_id",
        "robot_id",
        "human_state",
        "message",
        "evidence",
    }
)


def due_attempts(notifications: Mapping[str, NotificationView], config: InterventionConfig, now: datetime) -> list[NotificationView]:
    """Which notifications the sender should attempt now (bounded, same id).

    PENDING: first attempt.  UNKNOWN/FAILED: retry after the retry interval
    while attempts remain.  ATTEMPTING (an attempt journaled without a
    result, e.g. after a crash mid-send): retry after the interval measured
    from that attempt, never earlier, and never as a new notification.
    """

    due: list[NotificationView] = []
    for notification in notifications.values():
        if notification.state in {DELIVERED, EXHAUSTED}:
            continue
        if notification.attempts >= config.notify_max_attempts:
            continue  # decide_tick will journal the exhaustion
        if notification.state == PENDING:
            due.append(notification)
            continue
        anchor = notification.last_result_at if notification.state != ATTEMPTING else notification.last_attempt_at
        if anchor is None or (now - anchor).total_seconds() >= config.notify_retry_interval_s:
            due.append(notification)
    return due


# ---------------------------------------------------------------------------
# Receiver side
# ---------------------------------------------------------------------------

RECEIVER_STARTED = "receiver_started"
NOTIFICATION_RECEIVED = "notification_received"
NOTIFICATION_DUPLICATE = "notification_duplicate_attempt"
NOTIFICATION_REFUSED = "notification_refused"
RECEIVER_RECORD_KINDS = frozenset({RECEIVER_STARTED, NOTIFICATION_RECEIVED, NOTIFICATION_DUPLICATE, NOTIFICATION_REFUSED})


@dataclass
class ReceiverLedger:
    """The receiver's own durable dedup: one receipt per notification id."""

    receiver_id: str
    receipts: dict[str, str] = field(default_factory=dict)  # notification_id -> receipt_id
    duplicates: dict[str, int] = field(default_factory=dict)
    refused: int = 0
    applied_count: int = 0

    def apply_all(self, records: Sequence[Any]) -> None:
        for record in records[self.applied_count :]:
            kind = record.record_kind
            payload = record.payload
            if kind == NOTIFICATION_RECEIVED:
                self.receipts[payload["notification_id"]] = payload["receipt_id"]
            elif kind == NOTIFICATION_DUPLICATE:
                self.duplicates[payload["notification_id"]] = self.duplicates.get(payload["notification_id"], 0) + 1
            elif kind == NOTIFICATION_REFUSED:
                self.refused += 1
            elif kind != RECEIVER_STARTED:
                raise InterventionError("unknown_record_kind", f"receiver cannot apply {kind!r}")
            self.applied_count += 1

    @property
    def unique_count(self) -> int:
        return len(self.receipts)


def validate_notification(payload: Mapping[str, Any], *, site_id: str, deployment_id: str) -> dict[str, Any]:
    exact_keys(payload, _NOTIFICATION_KEYS, "notification")
    if payload["schema"] != NOTIFICATION_SCHEMA:
        raise InterventionError("invalid_notification", f"schema must be {NOTIFICATION_SCHEMA}")
    environment = payload["environment"]
    exact_keys(environment, frozenset({"kind", "simulation_env_id"}), "notification.environment")
    if environment["kind"] != "SIMULATION":
        raise InterventionError("invalid_notification", "environment.kind must be SIMULATION")
    if payload["site_id"] != site_id or payload["deployment_id"] != deployment_id:
        raise InterventionError("invalid_notification", "site/deployment do not match this receiver")
    identifier(payload["notification_id"], "notification_id")
    identifier(payload["case_id"], "case_id")
    if not isinstance(payload["message"], str) or not payload["message"] or len(payload["message"]) > 2000:
        raise InterventionError("invalid_notification", "message must be non-empty text of at most 2000 chars")
    if not isinstance(payload["evidence"], Mapping):
        raise InterventionError("invalid_notification", "evidence must be an object")
    return dict(payload)


def receipt_specs(ledger: ReceiverLedger, payload: Mapping[str, Any], now: datetime, *, site_id: str, deployment_id: str) -> tuple[dict[str, Any], list[RecordSpec], int]:
    """Decide the receiver's response and records for one delivery attempt.

    Returns ``(response_body, records_to_append, http_status)``.  An invalid
    payload is refused (400) and journaled without a receipt; a known
    notification id answers with its original receipt (duplicate=true) and
    journals the duplicate attempt; a new id gets a receipt derived from the
    receiver identity and the notification id.
    """

    try:
        notification = validate_notification(payload, site_id=site_id, deployment_id=deployment_id)
    except InterventionError as exc:
        spec = _spec(NOTIFICATION_REFUSED, "CHANNEL", now, {"code": exc.code, "detail": exc.detail})
        return {"error": exc.code, "detail": exc.detail}, [spec], 400
    notification_id = notification["notification_id"]
    if notification_id in ledger.receipts:
        receipt_id = ledger.receipts[notification_id]
        spec = _spec(NOTIFICATION_DUPLICATE, "CHANNEL", now, {"notification_id": notification_id, "receipt_id": receipt_id})
        return {"receipt_id": receipt_id, "duplicate": True, "received_at_utc": None}, [spec], 200
    receipt_id = "rcpt_" + stable_digest({"receiver_id": ledger.receiver_id, "notification_id": notification_id})[:24]
    spec = _spec(
        NOTIFICATION_RECEIVED,
        "CHANNEL",
        now,
        {
            "notification_id": notification_id,
            "receipt_id": receipt_id,
            "case_id": notification["case_id"],
            "intent": notification["intent"],
            "kind": notification["kind"],
            "severity": notification["severity"],
            "message": notification["message"],
            "evidence": dict(notification["evidence"]),
        },
    )
    return {"receipt_id": receipt_id, "duplicate": False, "received_at_utc": utc_text(now)}, [spec], 200


def receiver_started_spec(receiver_id: str, now: datetime, *, host: str, port: int) -> RecordSpec:
    return _spec(RECEIVER_STARTED, "CHANNEL", now, {"receiver_id": receiver_id, "host": host, "port": port})


__all__ = [
    "NOTIFICATION_DUPLICATE",
    "NOTIFICATION_RECEIVED",
    "NOTIFICATION_REFUSED",
    "RECEIVER_RECORD_KINDS",
    "RECEIVER_STARTED",
    "ReceiverLedger",
    "due_attempts",
    "receipt_specs",
    "receiver_started_spec",
    "validate_notification",
]
