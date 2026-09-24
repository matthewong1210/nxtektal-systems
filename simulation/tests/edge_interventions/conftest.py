"""Harness for the intervention rehearsal: PR A's in-memory Edge stack plus an in-process receiver.

Everything runs on the injected clock.  The receiver double persists to its
own journal through the same ``receipt_specs`` decision the real receiver
uses, and offers fault hooks (lose the answer, refuse, be unreachable) that
the delivery tests drive.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from nxt_edge_interventions import (
    INTERVENTION_JOURNAL_SCHEMA,
    INTERVENTION_RECORD_KINDS,
    RECEIVER_JOURNAL_SCHEMA,
    RECEIVER_RECORD_KINDS,
    InterventionConfig,
    ReceiverLedger,
    receipt_specs,
)
from nxt_edge_interventions.contracts import RESULT_DELIVERED, RESULT_FAILED, RESULT_UNKNOWN
from nxt_edge_interventions.notify import receiver_started_spec
from nxt_edge_task.journal import JsonlJournal, RecordSpec
from scripts.edge_intervention_cli import operator_action
from scripts.edge_intervention_service_v0 import InterventionService
from scripts.edge_task_transport import InMemoryBroker
from scripts.pilot_course_a_task_fixture import admission_facts, commissioned_site
from tests.edge_task.conftest import SIM_ROOT, FakeClock, Harness, load_config

INTERVENTION_CONFIG_PATH = SIM_ROOT / "configs" / "edge_task" / "pilot-course-a.interventions.sim.example.json"


def load_intervention_config(**overrides: Any) -> InterventionConfig:
    payload = json.loads(INTERVENTION_CONFIG_PATH.read_text(encoding="utf-8"))
    payload.update(overrides)
    return InterventionConfig.from_dict(payload)


def _to_spec(spec) -> RecordSpec:
    return RecordSpec(record_kind=spec.record_kind, origin=spec.origin, recorded_at_utc=spec.recorded_at_utc, payload=dict(spec.payload))


class SenderCrashed(RuntimeError):
    """Raised by the receiver double to simulate the sender process dying mid-delivery."""


@dataclass
class FakeReceiver:
    """In-process receiver: persists like the real one, answers or loses the answer."""

    config: InterventionConfig
    journal: JsonlJournal
    clock: Callable[[], datetime]
    drop_responses: int = 0  # persist, then lose the answer (sender sees UNKNOWN)
    refuse: bool = False  # answer 400 without persisting (sender sees FAILED)
    unreachable: bool = False  # connection refused (sender sees UNKNOWN)
    crash_sender_after_persist: bool = False  # the sender process dies after the receiver persisted
    crash_sender_before_send: bool = False  # the sender process dies with the attempt journaled, nothing sent
    dropped: int = 0
    deliveries: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ledger = ReceiverLedger(receiver_id=self.config.receiver.receiver_id)
        now = self.clock()
        appended = self.journal.append_via(self._builder(lambda _l: [receiver_started_spec(self.config.receiver.receiver_id, now, host=self.config.receiver.host, port=self.config.receiver.port)]))
        self.ledger.apply_all(appended)

    def _builder(self, decide):
        def build(records):
            self.ledger.apply_all(records)
            return [_to_spec(s) for s in decide(self.ledger)]

        return build

    def send(self, payload: dict[str, Any]) -> tuple[str, str | None, str]:
        if self.crash_sender_before_send:
            self.crash_sender_before_send = False
            raise SenderCrashed("process died after journaling the attempt, before the request left")
        self.deliveries.append(payload)
        if self.unreachable:
            return RESULT_UNKNOWN, None, "URLError: connection refused"
        if self.refuse:
            return RESULT_FAILED, None, "http 400"
        now = self.clock()
        outcome: dict[str, Any] = {}

        def decide(ledger):
            body, specs, status = receipt_specs(ledger, payload, now, site_id=self.config.site_id, deployment_id=self.config.deployment_id)
            outcome["body"], outcome["status"] = body, status
            return specs

        appended = self.journal.append_via(self._builder(decide))
        self.ledger.apply_all(appended)
        body, status = outcome["body"], outcome["status"]
        if self.crash_sender_after_persist:
            self.crash_sender_after_persist = False
            raise SenderCrashed("process died after the receiver persisted, before the result was journaled")
        if status != 200:
            return RESULT_FAILED, None, f"http {status}"
        if not body.get("duplicate") and self.dropped < self.drop_responses:
            self.dropped += 1
            return RESULT_UNKNOWN, None, "TimeoutError: answer lost"
        return RESULT_DELIVERED, body["receipt_id"], "duplicate receipt" if body.get("duplicate") else "receipt"

    # -- inspection ---------------------------------------------------------

    def records(self) -> list:
        return list(self.journal.read(anchored=False))

    def unique_received(self) -> int:
        return sum(1 for r in self.records() if r.record_kind == "notification_received")

    def duplicate_attempts(self) -> int:
        return sum(1 for r in self.records() if r.record_kind == "notification_duplicate_attempt")


@dataclass
class InterventionHarness:
    edge: Harness
    config: InterventionConfig
    root: Path
    receiver: FakeReceiver
    service: InterventionService | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def clock(self) -> FakeClock:
        return self.edge.clock

    @property
    def journal_path(self) -> Path:
        # Same layout the scripts derive from the config, so the real CLI can
        # be pointed at this root and read/write the very same journal.
        return self.root / self.config.evidence_dir / self.config.site_id / self.config.deployment_id / "edge_interventions_journal.jsonl"

    def journal(self) -> JsonlJournal:
        return JsonlJournal(self.journal_path, allowed_kinds=INTERVENTION_RECORD_KINDS, schema=INTERVENTION_JOURNAL_SCHEMA)

    def start_service(self) -> InterventionService:
        self.service = InterventionService(self.config, self.edge.config, self.journal(), self.edge.edge_journal(), self.receiver, clock=self.clock, emit=self.events.append)
        self.service.start()
        return self.service

    def crash_service(self) -> None:
        self.service = None

    def step(self, rounds: int = 1, seconds: float = 1.0, *, robots: bool = True, edge: bool = True, service: bool = True) -> None:
        for _ in range(rounds):
            self.edge.step(1, seconds, robots=robots, edge=edge)
            if service and self.service is not None and self.service.failure is None:
                self.service.tick()

    # -- operator actions (through the same code path as the CLI) -------------

    def ack(self, case_id: str, operator: str = "alice", note: str = "taking it") -> dict[str, Any]:
        return operator_action(self.journal(), self.config, "ack", case_id, operator, note, now=self.clock())

    def resolve(self, case_id: str, operator: str = "alice", resolution: str = "handled on site") -> dict[str, Any]:
        return operator_action(self.journal(), self.config, "resolve", case_id, operator, resolution, now=self.clock())

    # -- inspection ---------------------------------------------------------

    def records(self) -> list:
        return list(self.journal().read(anchored=False))

    def kinds(self) -> list[str]:
        return [r.record_kind for r in self.records()]

    def view(self):
        from nxt_edge_interventions import derive_view

        return derive_view(self.config, self.records())

    def cases(self, kind: str | None = None) -> list:
        return [c for c in self.view().cases.values() if kind is None or c.kind == kind]

    def notifications(self, case_id: str | None = None) -> list:
        view = self.view()
        return [n for n in view.notifications.values() if case_id is None or n.case_id == case_id]

    def attempts(self, notification_id: str | None = None) -> int:
        return sum(1 for r in self.records() if r.record_kind == "notification_attempted" and (notification_id is None or r.payload["notification_id"] == notification_id))


def run_until(harness: InterventionHarness, predicate: Callable[[], bool], *, max_rounds: int = 60, seconds: float = 1.0) -> None:
    for _ in range(max_rounds):
        if predicate():
            return
        harness.step(1, seconds)
    assert predicate(), "condition not reached within the round budget"


@pytest.fixture
def stack(tmp_path: Path) -> InterventionHarness:
    edge = Harness(root=tmp_path, config=load_config(), clock=FakeClock(), broker=InMemoryBroker(), facts=admission_facts(commissioned_site()))
    config = load_intervention_config(reminder_interval_s=30, max_reminders=2, notify_max_attempts=4, notify_retry_interval_s=5)
    receiver_journal = JsonlJournal(tmp_path / "receiver" / "receiver_journal.jsonl", allowed_kinds=RECEIVER_RECORD_KINDS, schema=RECEIVER_JOURNAL_SCHEMA)
    receiver = FakeReceiver(config=config, journal=receiver_journal, clock=edge.clock)
    return InterventionHarness(edge=edge, config=config, root=tmp_path, receiver=receiver)
