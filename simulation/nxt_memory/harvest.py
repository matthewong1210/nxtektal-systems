"""Read-only harvest of simulation truth for the memory recorder.

The ONLY module in nxt_memory allowed to import the simulator (mirroring
``nxt_facility.build``'s privileged position). Both functions are pure
reads: no RNG, no mutation, no event scheduling — trajectory neutrality
is guarded by tests/memory/test_guards.py.
"""

from __future__ import annotations

from nxt_range_ops.core.events import EventLog
from nxt_range_ops.core.metrics import OpsMetrics


def harvest_events(event_log: EventLog, since_index: int) -> tuple[list[dict], int]:
    """Events appended since ``since_index``, verbatim, plus the new cursor."""
    records = event_log.since(since_index)
    return [r.to_dict() for r in records], since_index + len(records)


def snapshot_metrics(metrics: OpsMetrics) -> dict:
    """Plain-dict copy of the cumulative operational metrics."""
    return metrics.to_dict()
