"""Immutable V3-only evidence owned and appended by RangeSimulation.

This log is independent of legacy EventLog and consumes no random draws.
It records actual ledger transfers, never inferred inventory deltas.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class AssignmentCandidate:
    execution_id: str
    robot_id: str
    zone_id: str
    handoff_station_id: str
    execution_deadline_sim_t_s: float


@dataclass(frozen=True)
class AssignmentEvent:
    event_id: str
    sequence: int
    assignment_id: str
    execution_id: str
    kind: str
    t_s: float
    robot_id: str
    zone_id: str
    station_id: str | None = None
    source_location: str | None = None
    destination_location: str | None = None
    balls: int | None = None
    payload_after: int | None = None
    reason: str | None = None
    ledger_conserved: bool = True
    robot_payload_parity: bool = True


class AssignmentEvidence:
    """Append-only immutable events; snapshots are detached plain data."""

    def __init__(self, candidate: AssignmentCandidate, started_sim_t_s: float):
        self.candidate = candidate
        self.started_sim_t_s = started_sim_t_s
        self.assignment_id = "assignment-" + _digest({**asdict(candidate), "started_sim_t_s": started_sim_t_s})
        self._events: tuple[AssignmentEvent, ...] = ()

    @property
    def terminal_reason(self) -> str | None:
        return next((e.reason for e in reversed(self._events) if e.kind == "ASSIGNMENT_TERMINAL"), None)

    @property
    def collection_exit_reason(self) -> str | None:
        return next((e.reason for e in self._events if e.kind == "COLLECTION_EXIT"), None)

    def quantity(self, kind: str) -> int:
        return sum(e.balls for e in self._events if e.kind == kind and e.balls is not None)

    def append(self, kind: str, t_s: float, **fields) -> None:
        if self.terminal_reason is not None:
            return
        sequence = len(self._events) + 1
        data = dict(sequence=sequence, assignment_id=self.assignment_id,
                    execution_id=self.candidate.execution_id, kind=kind, t_s=t_s,
                    robot_id=self.candidate.robot_id, zone_id=self.candidate.zone_id, **fields)
        event = AssignmentEvent(event_id="assignment-event-" + _digest(data), **data)
        self._events += (event,)

    def snapshot(self) -> dict:
        events = [asdict(e) for e in self._events]
        terminal = next((e for e in self._events if e.kind == "ASSIGNMENT_TERMINAL"), None)
        return {**asdict(self.candidate), "assignment_id": self.assignment_id,
                "started_sim_t_s": self.started_sim_t_s,
                "terminal_sim_t_s": terminal.t_s if terminal else None,
                "collection_exit_reason": self.collection_exit_reason,
                "terminal_reason": self.terminal_reason,
                "raw_collected_balls": self.quantity("RAW_COLLECTED_TO_ROBOT"),
                "unloaded_balls": self.quantity("UNLOADED_TO_STATION"),
                "ledger_conserved": all(e.ledger_conserved for e in self._events),
                "robot_payload_parity": all(e.robot_payload_parity for e in self._events),
                "events": events, "event_digest": _digest(events)}
