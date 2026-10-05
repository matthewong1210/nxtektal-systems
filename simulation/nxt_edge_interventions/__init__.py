"""SIMULATION-only human intervention cases and notification delivery state.

A stdlib-only leaf: it imports no other package of this repository.  The
composition roots under ``simulation/scripts/`` hand it the Edge task
exchange's derived view as plain data (task and device summaries plus the
Edge's configured bounds) and a journal port; it derives human-handling
cases, decides notification intents, attempts, results, reminders and
escalations, and validates operator acknowledgements and resolutions.

Three state families stay separate: task/device state (owned upstream and
never written here), human-handling state (OPEN, ACKNOWLEDGED, RESOLVED),
and notification state (PENDING, ATTEMPTING, UNKNOWN, DELIVERED, FAILED,
EXHAUSTED).  An acknowledgement or resolution records human handling only:
it never clears an authorization gate or an evidence conflict, never
re-dispatches, never marks a device idle, never releases a task, and never
claims a device stopped.
"""

from __future__ import annotations

from .cases import (
    INTERVENTION_RECORD_KINDS,
    InterventionCore,
    InterventionView,
    decide_acknowledge,
    decide_resolve,
    decide_tick,
    derive_view,
    reconcile,
)
from .contracts import (
    ACKNOWLEDGED,
    CASE_KINDS,
    CRITICAL,
    DELIVERED,
    EXHAUSTED,
    FAILED,
    INTERVENTION_JOURNAL_SCHEMA,
    OPEN,
    PENDING,
    RECEIVER_JOURNAL_SCHEMA,
    RESOLVED,
    UNKNOWN,
    WARNING,
    EdgeSnapshot,
    InterventionConfig,
    InterventionError,
    render_message,
)
from .notify import (
    RECEIVER_RECORD_KINDS,
    ReceiverLedger,
    due_attempts,
    receipt_specs,
)

__all__ = [
    "ACKNOWLEDGED",
    "CASE_KINDS",
    "CRITICAL",
    "DELIVERED",
    "EXHAUSTED",
    "FAILED",
    "INTERVENTION_JOURNAL_SCHEMA",
    "INTERVENTION_RECORD_KINDS",
    "OPEN",
    "PENDING",
    "RECEIVER_JOURNAL_SCHEMA",
    "RECEIVER_RECORD_KINDS",
    "RESOLVED",
    "UNKNOWN",
    "WARNING",
    "EdgeSnapshot",
    "InterventionConfig",
    "InterventionCore",
    "InterventionError",
    "InterventionView",
    "ReceiverLedger",
    "decide_acknowledge",
    "decide_resolve",
    "decide_tick",
    "derive_view",
    "due_attempts",
    "receipt_specs",
    "reconcile",
    "render_message",
]
