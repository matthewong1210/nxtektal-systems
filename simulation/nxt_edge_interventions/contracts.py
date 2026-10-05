"""Vocabulary, identities, configuration, and the plain-data input contract.

Everything here is deterministic and stdlib-only.  Time enters as
``datetime`` values supplied by the caller; identities are content digests;
messages come from fixed templates.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

INTERVENTION_JOURNAL_SCHEMA = "nxt-edge-interventions/journal/v1"
RECEIVER_JOURNAL_SCHEMA = "nxt-edge-interventions/receiver-journal/v1"
NOTIFICATION_SCHEMA = "nxt.edge.intervention.notification/v1"
CONFIG_SCHEMA = "nxt-edge-interventions/config/v1"
ENVIRONMENT_KIND_SIMULATION = "SIMULATION"

DISCLAIMER = "SIMULATION — local notification rehearsal; no real contact, no device command"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InterventionError(ValueError):
    """A refused input or an invalid operator action; carries a closed code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------


class CaseKind(StrEnum):
    ASSISTANCE_REQUIRED = "ASSISTANCE_REQUIRED"
    DEVICE_UNREACHABLE = "DEVICE_UNREACHABLE"
    RESULT_UNCONFIRMED = "RESULT_UNCONFIRMED"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"


CASE_KINDS = frozenset(kind.value for kind in CaseKind)

# Human-handling state (never a device or task state).
OPEN = "OPEN"
ACKNOWLEDGED = "ACKNOWLEDGED"
RESOLVED = "RESOLVED"
HUMAN_STATES = frozenset({OPEN, ACKNOWLEDGED, RESOLVED})

# Severity: only two levels in V0; CRITICAL > WARNING.
WARNING = "WARNING"
CRITICAL = "CRITICAL"
SEVERITY_RANK = {WARNING: 1, CRITICAL: 2}

# Notification (delivery) state.
PENDING = "PENDING"
ATTEMPTING = "ATTEMPTING"
UNKNOWN = "UNKNOWN"
DELIVERED = "DELIVERED"
FAILED = "FAILED"
EXHAUSTED = "EXHAUSTED"
NOTIFICATION_STATES = frozenset({PENDING, ATTEMPTING, UNKNOWN, DELIVERED, FAILED, EXHAUSTED})

# Why a notification intent exists.
INTENT_OPENED = "OPENED"
INTENT_REMINDER = "REMINDER"
INTENT_ESCALATION = "ESCALATION"
INTENT_KINDS = frozenset({INTENT_OPENED, INTENT_REMINDER, INTENT_ESCALATION})

# Attempt results reported by the transport adapter (composition root).
RESULT_DELIVERED = "delivered"  # receiver confirmed with a receipt
RESULT_UNKNOWN = "unknown"  # sent, no answer (timeout, connection lost)
RESULT_FAILED = "failed"  # receiver refused (4xx/5xx) or could not connect
ATTEMPT_RESULTS = frozenset({RESULT_DELIVERED, RESULT_UNKNOWN, RESULT_FAILED})

# Upstream (PR A) vocabulary this package recognises as plain strings.  These
# are read from summaries; nothing here imports the upstream package.
TASK_BLOCKED = "BLOCKED_AWAITING_HUMAN"
TASK_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "INCONCLUSIVE", "REJECTED"})
DEVICE_OFFLINE = "OFFLINE"
EFFECTIVE_CONFLICT = "CONFLICT"
EVIDENCE_CONFLICT_REASONS = frozenset(
    {"post_terminal_activity", "unexpected_acceptance", "unexpected_rejection", "conflicting_replay", "conflicting_terminal"}
)

# Fixed message templates keyed by case kind; placeholders are filled from the
# case payload only.  No free text from any message or operator reaches them.
TEMPLATES = {
    CaseKind.ASSISTANCE_REQUIRED.value: (
        "[{severity}] {kind}: robot {robot_id} reported ASSISTANCE_REQUIRED "
        "({reason_code}) on task {task_id}; task blocked awaiting human."
    ),
    CaseKind.DEVICE_UNREACHABLE.value: (
        "[{severity}] {kind}: robot {robot_id} is OFFLINE at the Edge since "
        "{offline_since_utc}; last valid status {last_valid_status_received_at_utc} "
        "(availability {last_reported_availability}, task {current_task_id}); "
        "current state UNKNOWN — not confirmed stopped or parked."
    ),
    CaseKind.RESULT_UNCONFIRMED.value: (
        "[{severity}] {kind}: task {task_id} on robot {robot_id} stays {task_state} "
        "after {publish_attempts} publish attempts ({detail}); result cannot be confirmed."
    ),
    CaseKind.EVIDENCE_CONFLICT.value: (
        "[{severity}] {kind}: {subject_kind} {subject_id} has conflicting or "
        "unexplained evidence ({detail}); authorization stays closed until a "
        "separately defined recovery contract exists."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise InterventionError("naive_datetime", "timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_utc(value: object, name: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not _UTC.match(value):
        raise InterventionError("invalid_timestamp", f"{name} must be an RFC 3339 UTC timestamp with Z")
    text = value[:-1]
    if "." not in text:
        text += ".000000"
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)


def identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.match(value):
        raise InterventionError("invalid_identifier", f"{name} must be a bounded identifier")
    return value


def bounded_text(value: object, name: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or value != value.strip():
        raise InterventionError("invalid_text", f"{name} must be non-empty trimmed text of at most {maximum} chars")
    return value


def exact_keys(payload: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if not isinstance(payload, Mapping):
        raise InterventionError("invalid_object", f"{label} must be an object")
    keys = frozenset(payload)
    if keys != expected:
        missing = ", ".join(sorted(expected - keys)) or "-"
        extra = ", ".join(sorted(keys - expected)) or "-"
        raise InterventionError("unexpected_keys", f"{label}: missing [{missing}] extra [{extra}]")


def case_identity(kind: str, subject_kind: str, subject_id: str, evidence_key: Any) -> str:
    """One case per (kind, subject, evidence key); the same evidence never re-opens."""

    return "case_" + stable_digest({"kind": kind, "subject_kind": subject_kind, "subject_id": subject_id, "evidence_key": evidence_key})[:24]


def notification_identity(case_id: str, intent_kind: str, ordinal: int) -> str:
    """Stable across retries: every attempt of one intent carries the same id."""

    return "ntf_" + stable_digest({"case_id": case_id, "intent": intent_kind, "ordinal": ordinal})[:24]


def render_message(kind: str, severity: str, fields: Mapping[str, Any]) -> str:
    template = TEMPLATES[kind]
    filled = {key: ("unknown" if value is None else value) for key, value in fields.items()}
    filled.setdefault("severity", severity)
    filled.setdefault("kind", kind)
    return template.format_map(_Defaulting(filled))


class _Defaulting(dict):
    def __missing__(self, key: str) -> str:
        return "unknown"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CONFIG_KEYS = frozenset(
    {
        "schema",
        "site_id",
        "deployment_id",
        "simulation_env_id",
        "evidence_dir",
        "receiver",
        "reminder_interval_s",
        "max_reminders",
        "notify_max_attempts",
        "notify_retry_interval_s",
        "tick_interval_s",
    }
)
_RECEIVER_KEYS = frozenset({"receiver_id", "host", "port", "timeout_s", "evidence_dir"})


def _positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0 or value != value:
        raise InterventionError("invalid_config", f"{name} must be a positive number")
    return float(value)


def _bounded_int(value: object, name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or type(value) is not int or value < minimum or value > maximum:
        raise InterventionError("invalid_config", f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def is_loopback_literal(host: object) -> bool:
    """Only ``127.0.0.0/8`` dotted quads (ASCII digits) or ``::1``."""

    if not isinstance(host, str):
        return False
    if host == "::1":
        return True
    parts = host.split(".")
    if len(parts) != 4 or parts[0] != "127":
        return False
    for part in parts:
        if not (part.isascii() and part.isdigit()) or len(part) > 3 or (len(part) > 1 and part[0] == "0") or int(part) > 255:
            return False
    return True


@dataclass(frozen=True)
class ReceiverEndpoint:
    receiver_id: str
    host: str
    port: int
    timeout_s: float
    evidence_dir: str

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}/notifications"


@dataclass(frozen=True)
class InterventionConfig:
    site_id: str
    deployment_id: str
    simulation_env_id: str
    evidence_dir: str
    receiver: ReceiverEndpoint
    reminder_interval_s: float
    max_reminders: int
    notify_max_attempts: int
    notify_retry_interval_s: float
    tick_interval_s: float

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InterventionConfig":
        exact_keys(payload, _CONFIG_KEYS, "config")
        if payload["schema"] != CONFIG_SCHEMA:
            raise InterventionError("invalid_config", f"config.schema must be {CONFIG_SCHEMA}")
        receiver = payload["receiver"]
        exact_keys(receiver, _RECEIVER_KEYS, "config.receiver")
        host = receiver["host"]
        if not is_loopback_literal(host):
            raise InterventionError("invalid_config", "config.receiver.host must be a loopback IP literal (127.0.0.0/8 or ::1)")
        port = _bounded_int(receiver["port"], "config.receiver.port", minimum=1024, maximum=65_535)
        endpoint = ReceiverEndpoint(
            receiver_id=identifier(receiver["receiver_id"], "config.receiver.receiver_id"),
            host=host,
            port=port,
            timeout_s=_positive_number(receiver["timeout_s"], "config.receiver.timeout_s"),
            evidence_dir=bounded_text(receiver["evidence_dir"], "config.receiver.evidence_dir", maximum=200),
        )
        return cls(
            site_id=identifier(payload["site_id"], "config.site_id"),
            deployment_id=identifier(payload["deployment_id"], "config.deployment_id"),
            simulation_env_id=identifier(payload["simulation_env_id"], "config.simulation_env_id"),
            evidence_dir=bounded_text(payload["evidence_dir"], "config.evidence_dir", maximum=200),
            receiver=endpoint,
            reminder_interval_s=_positive_number(payload["reminder_interval_s"], "reminder_interval_s"),
            max_reminders=_bounded_int(payload["max_reminders"], "max_reminders", minimum=0, maximum=100),
            notify_max_attempts=_bounded_int(payload["notify_max_attempts"], "notify_max_attempts", minimum=1, maximum=20),
            notify_retry_interval_s=_positive_number(payload["notify_retry_interval_s"], "notify_retry_interval_s"),
            tick_interval_s=_positive_number(payload["tick_interval_s"], "tick_interval_s"),
        )

    @classmethod
    def from_json(cls, text: str) -> "InterventionConfig":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InterventionError("invalid_config", f"config is not JSON: {exc.msg}") from exc
        return cls.from_dict(payload)


# ---------------------------------------------------------------------------
# Plain-data input: the Edge view as the composition root hands it over
# ---------------------------------------------------------------------------

_TASK_REQUIRED = frozenset(
    {
        "task_id",
        "target_robot_id",
        "state",
        "effective_result",
        "reconciliation_reasons",
        "publish_attempts",
        "expired_unconfirmed",
        "blocking_event",
        "expires_at_utc",
    }
)
_DEVICE_REQUIRED = frozenset(
    {
        "robot_id",
        "connectivity",
        "connectivity_since_utc",
        "last_valid_status_received_at_utc",
        "last_reported_availability",
        "current_task",
        "session_regression",
    }
)
_EDGE_REQUIRED = frozenset({"last_record_id", "max_republish_attempts"})


@dataclass(frozen=True)
class EdgeSnapshot:
    """Validated, read-only view of what the Edge journal derived.

    Built by a composition root from the upstream summaries; missing fields
    are refused rather than defaulted, so an incomplete snapshot can never be
    read as "healthy", "idle", or "parked".
    """

    tasks: Mapping[str, Mapping[str, Any]]
    devices: Mapping[str, Mapping[str, Any]]
    edge: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EdgeSnapshot":
        if not isinstance(payload, Mapping):
            raise InterventionError("invalid_snapshot", "snapshot must be an object")
        exact_keys(payload, frozenset({"tasks", "devices", "edge"}), "snapshot")
        tasks = payload["tasks"]
        devices = payload["devices"]
        edge = payload["edge"]
        if not isinstance(tasks, Mapping) or not isinstance(devices, Mapping) or not isinstance(edge, Mapping):
            raise InterventionError("invalid_snapshot", "snapshot.tasks, .devices and .edge must be objects")
        missing = _EDGE_REQUIRED - frozenset(edge)
        if missing:
            raise InterventionError("invalid_snapshot", f"snapshot.edge missing {sorted(missing)}")
        if isinstance(edge["max_republish_attempts"], bool) or type(edge["max_republish_attempts"]) is not int:
            raise InterventionError("invalid_snapshot", "snapshot.edge.max_republish_attempts must be an integer")
        for task_id, task in tasks.items():
            if not isinstance(task, Mapping):
                raise InterventionError("invalid_snapshot", f"task {task_id} must be an object")
            missing = _TASK_REQUIRED - frozenset(task)
            if missing:
                raise InterventionError("invalid_snapshot", f"task {task_id} missing {sorted(missing)}")
            if task["task_id"] != task_id:
                raise InterventionError("invalid_snapshot", f"task {task_id} carries a different task_id")
        for robot_id, device in devices.items():
            if not isinstance(device, Mapping):
                raise InterventionError("invalid_snapshot", f"device {robot_id} must be an object")
            missing = _DEVICE_REQUIRED - frozenset(device)
            if missing:
                raise InterventionError("invalid_snapshot", f"device {robot_id} missing {sorted(missing)}")
            if device["robot_id"] != robot_id:
                raise InterventionError("invalid_snapshot", f"device {robot_id} carries a different robot_id")
        return cls(tasks=tasks, devices=devices, edge=edge)


__all__ = [
    "ACKNOWLEDGED",
    "ATTEMPTING",
    "ATTEMPT_RESULTS",
    "CASE_KINDS",
    "CONFIG_SCHEMA",
    "CRITICAL",
    "DELIVERED",
    "DISCLAIMER",
    "ENVIRONMENT_KIND_SIMULATION",
    "EXHAUSTED",
    "FAILED",
    "HUMAN_STATES",
    "INTENT_ESCALATION",
    "INTENT_KINDS",
    "INTENT_OPENED",
    "INTENT_REMINDER",
    "INTERVENTION_JOURNAL_SCHEMA",
    "NOTIFICATION_SCHEMA",
    "NOTIFICATION_STATES",
    "OPEN",
    "PENDING",
    "RECEIVER_JOURNAL_SCHEMA",
    "RESOLVED",
    "RESULT_DELIVERED",
    "RESULT_FAILED",
    "RESULT_UNKNOWN",
    "SEVERITY_RANK",
    "TEMPLATES",
    "UNKNOWN",
    "WARNING",
    "CaseKind",
    "EdgeSnapshot",
    "InterventionConfig",
    "InterventionError",
    "ReceiverEndpoint",
    "bounded_text",
    "canonical_json",
    "case_identity",
    "exact_keys",
    "identifier",
    "is_loopback_literal",
    "notification_identity",
    "parse_utc",
    "render_message",
    "stable_digest",
    "utc_text",
]
