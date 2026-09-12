#!/usr/bin/env python3
"""Mock robot task device -- a message-level protocol double.

SIMULATION ONLY.  This process has no physics, motion, navigation, micro
handoff interface, adapter, ROS, actuator, or emergency-stop surface.  It
subscribes to its own task-request topic, decides with the pure
``nxt_edge_task.executor`` rules, persists every decision and event to its
own journal before publishing, and heartbeats its declared state.  Its
"local protection" is script-driven: nothing the Edge sends can pause,
resume, reset, or clear it.

Run from ``simulation/``::

    uv run --no-sync python -B scripts/mock_robot_task_device.py \
        --config configs/edge_task/pilot-course-a.sim.example.json \
        --robot-id picker-01 --behavior accept_and_succeed

``--simulate-reset`` records an explicit operator reset at the robot and exits.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_task.contracts import (  # noqa: E402
    EdgeTaskConfig,
    RobotConfig,
    canonical_bytes,
    event_topic,
    request_topic,
    status_topic,
)
from nxt_edge_task.executor import ROBOT_RECORD_KINDS, RobotCore  # noqa: E402
from nxt_edge_task.journal import JournalIntegrityError, JsonlJournal, PreconditionFailed  # noqa: E402
from scripts.edge_task_transport import Delivery, PahoClient, TransportClient  # noqa: E402

DISCLAIMER = "SIMULATION — protocol double; no physical robot"
PUBLISH_WAIT_S = 5.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def journal_path(robot: RobotConfig, root: Path) -> Path:
    return root / robot.evidence_dir / "robot_task_journal.jsonl"


class MockRobotDevice:
    def __init__(
        self,
        config: EdgeTaskConfig,
        robot: RobotConfig,
        behavior: str,
        journal: JsonlJournal,
        client: TransportClient,
        *,
        clock: Callable[[], datetime] = utcnow,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.robot = robot
        self.journal = journal
        self.client = client
        self.clock = clock
        self.emit = emit or _json_line
        self.core = RobotCore(config, robot, behavior)
        self.failure: Exception | None = None
        self.last_heartbeat: datetime | None = None
        self.status_publishes = 0
        self.event_publishes: list[tuple[int, int, str]] = []
        # History replays requested inside the message callback are published
        # from the main loop so hop-1 waits never nest inside a transport callback.
        self._replay_queue: list[dict[str, Any]] = []
        client.set_handlers(on_message=self.on_message, on_connect=self.on_connect, on_disconnect=self.on_disconnect)

    @property
    def view(self):
        return self.core.view

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        now = self.clock()
        appended = self.journal.append_via(self.core.builder(lambda view: self.core.on_start(view, now)))
        self.core.absorb(appended)
        self.emit({"event": "robot_started", "robot_id": self.robot.robot_id, "boot_sequence": self.view.boot_sequence, "behavior": self.core.behavior, "disclaimer": DISCLAIMER})
        self.client.connect()

    def on_connect(self, session_present: bool) -> None:
        self.client.subscribe(request_topic(self.config.site_id, self.robot.robot_id), 1)
        self.emit({"event": "transport_connected", "session_present": session_present})
        # Unconfirmed events are republished by the next tick.

    def on_disconnect(self, reason: str) -> None:
        self.emit({"event": "transport_disconnected", "reason": reason})

    # -- inbound --------------------------------------------------------------

    def on_message(self, delivery: Delivery) -> None:
        now = self.clock()
        if delivery.retain or delivery.qos != 1:
            code = "retained_delivery" if delivery.retain else "qos_mismatch"
            self._append(lambda _view: [self.core.delivery_rejected_spec(delivery.topic, code, "retained or non-QoS-1 request delivery refused", now)])
            self.emit({"event": "delivery_rejected", "code": code, "topic": delivery.topic})
            self._ack(delivery)
            return
        replay: list[dict[str, Any]] = []

        def decide(view):
            nonlocal replay
            specs, replay = self.core.on_request(view, delivery.topic, delivery.payload, now)
            return specs

        appended = self._append(decide)
        for record in appended:
            self.emit({"event": record.record_kind, "sequence": record.sequence, **_summary(record)})
        # hop-2: durable first, then PUBACK.  Replays and new events are
        # published by the main loop (tick), never from inside this callback.
        self._ack(delivery)
        self._replay_queue.extend(replay)

    # -- periodic -------------------------------------------------------------

    def tick(self) -> None:
        if self.failure is not None:
            return
        now = self.clock()
        appended = self._append(lambda view: self.core.tick(view, now))
        for record in appended:
            self.emit({"event": record.record_kind, "sequence": record.sequence, **_summary(record)})
        if self.core.exit_requested:
            self.emit({"event": "simulated_crash", "reason": self.core.exit_reason, "disclaimer": DISCLAIMER})
            return
        self.publish_pending()
        self.heartbeat_if_due(now)

    def publish_pending(self) -> None:
        if self.core.exit_requested:
            return
        replay, self._replay_queue = self._replay_queue, []
        for event in replay:
            self._publish_event(event, replayed=True)
        for event, rejection_ref in self.view.pending_publications():
            confirmed = self._publish_event(event, replayed=False)
            if confirmed:
                now = self.clock()
                self._append(lambda _view, ev=event, when=now, ref=rejection_ref: [self.core.publish_confirmed_spec(ev, when, record_sequence=ref)])

    def _publish_event(self, event: dict[str, Any], *, replayed: bool) -> bool:
        topic = event_topic(self.config.site_id, self.robot.robot_id)
        handle = self.client.publish(topic, canonical_bytes(event), 1)
        confirmed = handle.wait(PUBLISH_WAIT_S)
        self.event_publishes.append((event["boot_sequence"], event["event_sequence"], event["kind"]))
        self.emit({"event": "task_event_published", "kind": event["kind"], "boot_sequence": event["boot_sequence"], "event_sequence": event["event_sequence"], "replayed": replayed, "hop1_confirmed": confirmed})
        return confirmed

    def heartbeat_if_due(self, now: datetime, *, force: bool = False) -> None:
        if not force and self.last_heartbeat is not None and (now - self.last_heartbeat).total_seconds() < self.robot.heartbeat_interval_s:
            return
        message = self.core.status_message(self.view, now)
        self.client.publish(status_topic(self.config.site_id, self.robot.robot_id), message.canonical_bytes(), 0)
        self.status_publishes += 1
        self.last_heartbeat = now

    def simulate_reset(self) -> None:
        now = self.clock()
        appended = self._append(lambda view: self.core.simulate_reset(view, now))
        for record in appended:
            self.emit({"event": record.record_kind, "sequence": record.sequence, **_summary(record)})
        self.publish_pending()
        self.heartbeat_if_due(now, force=True)

    def run(self, *, max_seconds: float | None = None) -> int:
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        last_tick = 0.0
        tick_s = self.config.tick_interval_s
        while self.failure is None and not self.core.exit_requested:
            self.client.loop(min(tick_s, 0.25))
            if time.monotonic() - last_tick >= tick_s:
                self.tick()
                last_tick = time.monotonic()
            if deadline is not None and time.monotonic() >= deadline:
                break
        if self.core.exit_requested:
            return 3
        return 0 if self.failure is None else 2

    # -- plumbing -------------------------------------------------------------

    def _append(self, decide):
        try:
            appended = self.journal.append_via(self.core.builder(decide))
        except (JournalIntegrityError, PreconditionFailed, OSError) as exc:
            self.failure = exc
            self.emit({"event": "fail_stop", "detail": str(exc)})
            try:
                self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise
        self.core.absorb(appended)
        return appended

    def _ack(self, delivery: Delivery) -> None:
        try:
            self.client.ack(delivery)
        except Exception as exc:  # noqa: BLE001
            self.failure = exc
            self.emit({"event": "fail_stop", "detail": f"manual PUBACK failed: {exc}"})


def _summary(record) -> dict[str, Any]:
    payload = record.payload
    out: dict[str, Any] = {}
    for key in ("task_id", "decision", "reason_code", "disposition", "code", "outcome", "phase", "reason"):
        if key in payload:
            out[key] = payload[key]
    if "event" in payload and hasattr(payload["event"], "get"):
        event = payload["event"]
        out["event"] = {"kind": event.get("kind"), "boot_sequence": event.get("boot_sequence"), "event_sequence": event.get("event_sequence"), "reason_code": event.get("reason_code")}
    return out


def _json_line(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--behavior", default="accept_and_succeed")
    parser.add_argument("--evidence-root", type=Path, default=SIM_ROOT)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--simulate-reset", action="store_true", help="record an operator reset at the robot (SIMULATION) and exit")
    args = parser.parse_args(argv)

    config = EdgeTaskConfig.from_json(args.config.read_bytes())
    robot = config.robot(args.robot_id)
    behavior = args.behavior if robot.role == "picker" else "standby"
    journal = JsonlJournal(journal_path(robot, args.evidence_root), allowed_kinds=ROBOT_RECORD_KINDS)
    client = PahoClient(robot.client_id, config.broker_host, config.broker_port, config.keepalive_s)
    device = MockRobotDevice(config, robot, behavior, journal, client)

    stop = {"requested": False}

    def _stop(signum, frame):  # noqa: ANN001, ARG001
        stop["requested"] = True
        device.failure = device.failure or RuntimeError("signal")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        device.start()
        if args.simulate_reset:
            device.simulate_reset()
            for _ in range(10):
                client.loop(0.2)
            return 0
        code = device.run(max_seconds=args.max_seconds)
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    if stop["requested"] and code == 2:
        _json_line({"event": "robot_stopped", "reason": "signal"})
        return 0
    return code


if __name__ == "__main__":
    raise SystemExit(main())
