"""Shared harness for the Edge Task Exchange V0 tests.

Every logic test runs the real gateway, mock robots, journals, and CLI
admission code over the synchronous in-memory broker double with an
injected clock.  Processes never read each other's journals; the harness
(the test) reads them for assertions, exactly as the plan requires.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

SIM_ROOT = Path(__file__).resolve().parents[2]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_task.cases import EDGE_RECORD_KINDS  # noqa: E402
from nxt_edge_task.contracts import EdgeTaskConfig  # noqa: E402
from nxt_edge_task.executor import ROBOT_RECORD_KINDS  # noqa: E402
from nxt_edge_task.journal import JsonlJournal  # noqa: E402
from scripts.edge_task_cli import create_task  # noqa: E402
from scripts.edge_task_gateway_v0 import EdgeGateway  # noqa: E402
from scripts.edge_task_transport import InMemoryBroker  # noqa: E402
from scripts.mock_robot_task_device import MockRobotDevice  # noqa: E402
from scripts.pilot_course_a_task_fixture import admission_facts, commissioned_site  # noqa: E402

CONFIG_PATH = SIM_ROOT / "configs" / "edge_task" / "pilot-course-a.sim.example.json"
T0 = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
ISSUED = "2026-09-12T08:00:00.000000Z"
EXPIRES = "2026-09-12T08:10:00.000000Z"


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def load_config(**overrides: Any) -> EdgeTaskConfig:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        payload["edge"][key] = value
    return EdgeTaskConfig.from_dict(payload)


def silent(_payload: dict[str, Any]) -> None:
    return None


@dataclass
class Harness:
    root: Path
    config: EdgeTaskConfig
    clock: FakeClock
    broker: InMemoryBroker
    facts: Any
    edge: EdgeGateway | None = None
    picker: MockRobotDevice | None = None
    carrier: MockRobotDevice | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    # -- construction -----------------------------------------------------

    @property
    def edge_journal_path(self) -> Path:
        return self.root / "edge" / "edge_task_journal.jsonl"

    def robot_journal_path(self, robot_id: str) -> Path:
        return self.root / "robots" / robot_id / "robot_task_journal.jsonl"

    def edge_journal(self) -> JsonlJournal:
        return JsonlJournal(self.edge_journal_path, allowed_kinds=EDGE_RECORD_KINDS)

    def robot_journal(self, robot_id: str) -> JsonlJournal:
        return JsonlJournal(self.robot_journal_path(robot_id), allowed_kinds=ROBOT_RECORD_KINDS)

    def start_edge(self) -> EdgeGateway:
        self.edge = EdgeGateway(
            self.config,
            self.facts,
            self.edge_journal(),
            self.broker.client(self.config.edge_client_id),
            clock=self.clock,
            transport_name="inmemory",
            emit=self.events.append,
        )
        self.edge.start()
        return self.edge

    def start_robot(self, robot_id: str, behavior: str = "accept_and_succeed", *, initialize: bool = False) -> MockRobotDevice:
        """Start (or restart) a robot process.

        ``initialize=True`` is the explicit first-boot provisioning act; a
        restart never passes it.  A missing journal without it is a refused
        start (state loss), exactly as the script behaves.
        """

        robot = self.config.robot(robot_id)

        def purge_session() -> None:
            purger = self.broker.client(robot.client_id, clean_session=True)
            purger.set_handlers(on_message=lambda d: None, on_connect=lambda p: None, on_disconnect=lambda r: None)
            purger.connect()
            purger.disconnect()

        device = MockRobotDevice(
            self.config,
            robot,
            behavior if robot.role == "picker" else "standby",
            self.robot_journal(robot_id),
            self.broker.client(robot.client_id),
            clock=self.clock,
            emit=self.events.append,
            initialize=initialize,
            purge_session=purge_session,
        )
        device.start()
        if robot.role == "picker":
            self.picker = device
        else:
            self.carrier = device
        return device

    def start_all(self, behavior: str = "accept_and_succeed") -> None:
        self.start_edge()
        self.start_robot("picker-01", behavior, initialize=True)
        self.start_robot("carrier-01", initialize=True)

    # -- driving ----------------------------------------------------------

    def step(self, rounds: int = 1, seconds: float = 1.0, *, robots: bool = True, edge: bool = True) -> None:
        for _ in range(rounds):
            self.clock.advance(seconds)
            if robots:
                for device in (self.picker, self.carrier):
                    if device is not None and not device.core.exit_requested and device.failure is None:
                        device.tick()
                self.broker.pump()
            if edge and self.edge is not None:
                self.edge.tick()
                self.broker.pump()

    def create(self, robot_id: str = "picker-01", *, issued: str = ISSUED, expires: str = EXPIRES, operator: str = "test", progress_window_s: int | None = None, zone: str = "Z1") -> dict[str, Any]:
        return create_task(
            self.edge_journal(),
            self.config,
            self.facts,
            robot_id=robot_id,
            zone_id=zone,
            issued_at_utc=issued,
            expires_at_utc=expires,
            operator=operator,
            progress_window_s=progress_window_s,
            now=self.clock(),
        )

    # -- crash / restart ---------------------------------------------------

    def crash_edge(self) -> None:
        """Simulate an Edge process death: drop the object, keep the journal."""
        assert self.edge is not None
        self.broker.disconnect_client(self.config.edge_client_id)
        self.edge = None

    def crash_robot(self, robot_id: str) -> None:
        robot = self.config.robot(robot_id)
        self.broker.disconnect_client(robot.client_id)
        if robot.role == "picker":
            self.picker = None
        else:
            self.carrier = None

    @staticmethod
    def _tear(path: Path, keep: int, *, keep_anchor: bool) -> int:
        lines = path.read_bytes().split(b"\n")[:-1]
        assert 0 <= keep <= len(lines)
        path.write_bytes(b"".join(line + b"\n" for line in lines[:keep]))
        if not keep_anchor:
            # A torn batch is a crash before the anchor was advanced: the anchor
            # still points at the last record that survived.
            anchor = path.with_name(path.name + ".hwm")
            last_id = json.loads(lines[keep - 1])["record_id"] if keep else None
            anchor.write_text(json.dumps({"schema": "nxt-edge-task/journal-anchor/v1", "records": keep, "last_record_id": last_id}, separators=(",", ":"), sort_keys=True))
        return len(lines) - keep

    def truncate_robot_journal(self, robot_id: str, keep: int) -> int:
        """Model a torn batch: records after ``keep`` never became durable, nor did the anchor."""

        return self._tear(self.robot_journal_path(robot_id), keep, keep_anchor=False)

    def rollback_robot_journal(self, robot_id: str, keep: int) -> int:
        """Model state loss that is not a deletion: the journal is rolled back to a valid prefix; the anchor is intact."""

        return self._tear(self.robot_journal_path(robot_id), keep, keep_anchor=True)

    def truncate_edge_journal(self, keep: int) -> int:
        return self._tear(self.edge_journal_path, keep, keep_anchor=False)

    def request_bytes(self, task_id: str) -> bytes:
        """The byte-identical request as the Edge journaled it (test-side resend material)."""

        from nxt_edge_task.contracts import TaskRequest

        for record in self.edge_records():
            if record.record_kind == "task_created" and record.payload["task_id"] == task_id:
                return TaskRequest.from_dict(json.loads(json.dumps(record.payload["request"], default=dict))).canonical_bytes()
        raise KeyError(task_id)

    def known_incarnation(self, robot_id: str) -> str:
        """The incarnation prefix the Edge last saw for ``robot_id`` (injected events must belong to it)."""

        from nxt_edge_task.contracts import incarnation_of

        if self.edge is not None:
            device = self.device(robot_id)
            if device["boot_id"] is not None:
                return incarnation_of(device["boot_id"], device["boot_sequence"])
        return f"boot-{robot_id}-unseen"

    def intruder(self, client_id: str = "intruder"):
        client = self.broker.client(client_id, clean_session=True)
        client.set_handlers(on_message=lambda d: None, on_connect=lambda p: None, on_disconnect=lambda r: None)
        client.connect()
        return client

    def replay_event(self, event: Any, *, robot_id: str = "picker-01") -> None:
        """Redeliver exactly these event bytes (broker redelivery / robot history replay)."""

        from nxt_edge_task.contracts import event_topic

        payload = json.loads(json.dumps(event, default=dict))
        self.intruder().publish(event_topic(self.config.site_id, robot_id), json.dumps(payload).encode(), 1)
        self.broker.pump()

    # -- assertions ---------------------------------------------------------

    def edge_records(self) -> list:
        # The harness is an out-of-process inspector: it reads what survived,
        # anchored or not; the processes themselves always read anchored.
        return list(self.edge_journal().read(anchored=False))

    def robot_records(self, robot_id: str) -> list:
        return list(self.robot_journal(robot_id).read(anchored=False))

    def kinds(self, records) -> list[str]:
        return [record.record_kind for record in records]

    def executions(self, robot_id: str, task_id: str | None = None) -> int:
        count = 0
        for record in self.robot_records(robot_id):
            if record.record_kind == "execution_started" and (task_id is None or record.payload["task_id"] == task_id):
                count += 1
        return count

    def _refresh_edge(self) -> None:
        assert self.edge is not None
        # The CLI appends through its own journal instance; mirror the sync the
        # gateway performs inside every in-lock builder.
        self.edge.core.refresh(self.edge.journal.read())

    def task(self, task_id: str) -> dict[str, Any]:
        self._refresh_edge()
        return self.edge.snapshot()["tasks"][task_id]

    def device(self, robot_id: str) -> dict[str, Any]:
        self._refresh_edge()
        return self.edge.snapshot()["devices"][robot_id]

    def publish_calls_for(self, task_id: str) -> list[tuple[str, str, int]]:
        assert self.edge is not None
        return [call for call in self.edge.publish_calls if call[0] == task_id]

    def event_dispositions(self, task_id: str) -> list[tuple[str, int, int, str]]:
        out = []
        for record in self.edge_records():
            if record.record_kind == "task_event_received" and record.payload["task_id"] == task_id:
                event = record.payload["event"]
                out.append((event["kind"], event["boot_sequence"], event["event_sequence"], record.payload["disposition"]))
        return out


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    config = load_config()
    site = commissioned_site()
    return Harness(root=tmp_path, config=config, clock=FakeClock(), broker=InMemoryBroker(), facts=admission_facts(site))


def run_until(harness: Harness, predicate: Callable[[], bool], *, max_rounds: int = 60, seconds: float = 1.0) -> None:
    for _ in range(max_rounds):
        if predicate():
            return
        harness.step(1, seconds)
    assert predicate(), "condition not reached within the round budget"
