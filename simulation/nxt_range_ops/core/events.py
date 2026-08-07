"""Structured event logging for the operational simulator.

Every state-changing occurrence appends an :class:`EventRecord` with the
simulated timestamp and a JSON-serializable payload. The event log is the
ground truth used by the reproducibility tests: two runs with the same seed
and action sequence must produce identical logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventKind(str, Enum):
    EPISODE_START = "episode_start"
    EPISODE_END = "episode_end"
    DEMAND_SERVED = "demand_served"
    STOCKOUT = "stockout"
    BALLS_LANDED = "balls_landed"
    DIRECTIVE_APPLIED = "directive_applied"
    DIRECTIVE_REJECTED = "directive_rejected"
    TRAVEL_STARTED = "travel_started"
    TRAVEL_COMPLETED = "travel_completed"
    COLLECT_CYCLE = "collect_cycle"
    COLLECTION_DONE = "collection_done"
    HANDOFF_ENQUEUED = "handoff_enqueued"
    DOCK_ATTEMPT = "dock_attempt"
    DOCK_FAILED = "dock_failed"
    UNLOADED = "unloaded"
    WASH_BATCH_STARTED = "wash_batch_started"
    WASH_BATCH_DONE = "wash_batch_done"
    CHARGE_STARTED = "charge_started"
    CHARGE_DONE = "charge_done"
    ROBOT_PAUSED = "robot_paused"
    ROBOT_RESUMED = "robot_resumed"
    ROBOT_FAILED = "robot_failed"
    ROBOT_DEGRADED = "robot_degraded"
    ROBOT_RECOVERED = "robot_recovered"
    BATTERY_DEPLETED = "battery_depleted"
    EMERGENCY_STOP = "emergency_stop"
    HUMAN_REQUESTED = "human_requested"
    HUMAN_ARRIVED = "human_arrived"
    HUMAN_DONE = "human_done"
    ZONE_CLOSED = "zone_closed"
    ZONE_REOPENED = "zone_reopened"
    STATION_OUTAGE = "station_outage"
    STATION_RESTORED = "station_restored"
    TASK_SWITCHED = "task_switched"
    FACILITY_CLOSED = "facility_closed"


@dataclass(frozen=True)
class EventRecord:
    t_s: float
    kind: EventKind
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"t_s": self.t_s, "kind": self.kind.value, "payload": self.payload}


class EventLog:
    def __init__(self) -> None:
        self._records: list[EventRecord] = []

    def emit(self, t_s: float, kind: EventKind, **payload: Any) -> None:
        self._records.append(EventRecord(t_s=round(t_s, 6), kind=kind, payload=payload))

    @property
    def records(self) -> list[EventRecord]:
        return list(self._records)

    def since(self, index: int) -> list[EventRecord]:
        return self._records[index:]

    def __len__(self) -> int:
        return len(self._records)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._records]
