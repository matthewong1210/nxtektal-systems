#!/usr/bin/env python3
"""Edge Task Gateway V0 -- the Edge side of the simulated task exchange.

SIMULATION ONLY -- protocol rehearsal; no physical robot, no live site.

Composition root: owns the transport client, the wall clock, the process
lifetime, and the single-instance lock.  Every decision is delegated to the
pure ``nxt_edge_task`` rules and every fact is written to the Edge journal
before the corresponding MQTT acknowledgement is sent (hop-2 manual PUBACK).
The Edge learns about robots only through received messages; it never reads
a robot's directory.

Run from ``simulation/``::

    uv run --no-sync python -B scripts/edge_task_gateway_v0.py \
        --config configs/edge_task/pilot-course-a.sim.example.json

Stop with Ctrl-C; the journal is the only state and is safe to resume.
"""

from __future__ import annotations

import argparse
import fcntl
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

from nxt_edge_task.cases import (  # noqa: E402
    EDGE_RECORD_KINDS,
    EdgeCore,
    decide_event,
    decide_status,
    decide_tick,
    delivery_rejected_spec,
    edge_started_spec,
    publish_attempted_spec,
    publish_confirmed_spec,
    republish_candidates,
    transport_session_spec,
)
from nxt_edge_task.contracts import (  # noqa: E402
    AdmissionFacts,
    EdgeTaskConfig,
    EdgeTaskError,
    event_topic,
    request_topic,
    status_topic,
    topic_parts,
)
from nxt_edge_task.journal import JournalIntegrityError, JsonlJournal, PreconditionFailed  # noqa: E402
from scripts.edge_task_transport import Delivery, PahoClient, TransportClient  # noqa: E402

DISCLAIMER = "SIMULATION — protocol rehearsal; no physical robot, no live site"
PUBLISH_WAIT_S = 5.0


class GatewayFailStop(RuntimeError):
    """The gateway can no longer make durable progress; stop without ack."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def journal_path(config: EdgeTaskConfig, root: Path) -> Path:
    return root / config.edge_evidence_dir / config.site_id / config.deployment_id / "edge_task_journal.jsonl"


class EdgeGateway:
    """Drives the Edge rules over one journal and one transport client."""

    def __init__(
        self,
        config: EdgeTaskConfig,
        facts: AdmissionFacts,
        journal: JsonlJournal,
        client: TransportClient,
        *,
        clock: Callable[[], datetime] = utcnow,
        transport_name: str = "mqtt",
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.facts = facts
        self.journal = journal
        self.client = client
        self.clock = clock
        self.transport_name = transport_name
        self.emit = emit or _json_line
        self.core = EdgeCore(config, facts, transport=transport_name)
        self.connected_once = False
        self.failure: Exception | None = None
        self.publish_calls: list[tuple[str, str, int]] = []
        client.set_handlers(on_message=self.on_message, on_connect=self.on_connect, on_disconnect=self.on_disconnect)

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        now = self.clock()
        appended = self.journal.append_via(self.core.builder(lambda _view: [edge_started_spec(self.config, self.facts, now)]))
        self.core.absorb(appended)
        self.emit({"event": "edge_started", "at": now.isoformat(), "tasks": len(self.core.view.tasks), "disclaimer": DISCLAIMER})
        self.client.connect()

    def on_connect(self, session_present: bool) -> None:
        now = self.clock()
        event = "connected"
        if self.connected_once and not session_present:
            event = "session_lost"
        self.connected_once = True
        appended = self.journal.append_via(
            self.core.builder(lambda _view: [transport_session_spec(event, now, session_present=session_present)])
        )
        self.core.absorb(appended)
        for robot_id in self.config.robot_ids:
            self.client.subscribe(status_topic(self.config.site_id, robot_id), 0)
            self.client.subscribe(event_topic(self.config.site_id, robot_id), 1)
        self.emit({"event": "transport_" + event, "session_present": session_present})

    def on_disconnect(self, reason: str) -> None:
        self.emit({"event": "transport_disconnected", "reason": reason})

    # -- inbound --------------------------------------------------------------

    def on_message(self, delivery: Delivery) -> None:
        now = self.clock()
        try:
            _site, _robot, family = topic_parts(delivery.topic)
        except EdgeTaskError as exc:
            self._append(lambda view: decide_event(view, delivery.topic, delivery.payload, now, transport=self.transport_name))
            self.emit({"event": "delivery_rejected", "code": exc.code.value, "topic": delivery.topic})
            self._ack(delivery)
            return
        if delivery.retain:
            self._append(lambda _view: [delivery_rejected_spec(delivery.topic, "retained_delivery", "retained deliveries are forbidden in V0", now, transport=self.transport_name, qos=delivery.qos)])
            self.emit({"event": "delivery_rejected", "code": "retained_delivery", "topic": delivery.topic})
            self._ack(delivery)
            return
        expected_qos = 0 if family == "status" else 1
        if delivery.qos != expected_qos:
            self._append(lambda _view: [delivery_rejected_spec(delivery.topic, "qos_mismatch", f"expected QoS {expected_qos} on this topic family", now, transport=self.transport_name, qos=delivery.qos)])
            self.emit({"event": "delivery_rejected", "code": "qos_mismatch", "topic": delivery.topic, "qos": delivery.qos})
            self._ack(delivery)
            return
        if family == "status":
            appended = self._append(lambda view: decide_status(view, delivery.topic, delivery.payload, now, transport=self.transport_name))
        elif family == "event":
            appended = self._append(lambda view: decide_event(view, delivery.topic, delivery.payload, now, transport=self.transport_name))
        else:
            appended = self._append(lambda view: decide_event(view, delivery.topic, delivery.payload, now, transport=self.transport_name))
        for record in appended:
            self.emit({"event": record.record_kind, "sequence": record.sequence, **_summary(record)})
        # hop-2: acknowledge only after the durable append above returned.
        self._ack(delivery)

    def _ack(self, delivery: Delivery) -> None:
        try:
            self.client.ack(delivery)
        except Exception as exc:  # noqa: BLE001 - any ack failure is fail-stop
            self._fail_stop(GatewayFailStop(f"manual PUBACK failed: {exc}"))

    def _append(self, decide):
        try:
            appended = self.journal.append_via(self.core.builder(decide))
        except (JournalIntegrityError, PreconditionFailed, OSError) as exc:
            self._fail_stop(GatewayFailStop(f"journal append failed: {exc}"))
            raise
        self.core.absorb(appended)
        return appended

    def _fail_stop(self, error: Exception) -> None:
        if self.failure is None:
            self.failure = error
        self.emit({"event": "fail_stop", "detail": str(error)})
        try:
            self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    # -- periodic -------------------------------------------------------------

    def tick(self) -> None:
        if self.failure is not None:
            return
        now = self.clock()
        appended = self._append(lambda view: decide_tick(view, now))
        for record in appended:
            self.emit({"event": record.record_kind, "sequence": record.sequence, **_summary(record)})
        for planned in republish_candidates(self.core.view, now):
            if self.failure is not None:
                return
            # An earlier candidate's hop-1 wait pumps the network loop and may
            # have applied a terminal, block, conflict, or regression for this
            # task: re-derive the candidate from the live view before publishing.
            live = [c for c in republish_candidates(self.core.view, self.clock()) if c.task_id == planned.task_id]
            if not live:
                continue
            candidate = live[0]
            task = self.core.view.tasks[candidate.task_id]
            topic = request_topic(self.config.site_id, task.request.target_robot_id)
            payload = task.request.canonical_bytes()
            attempt_now = self.clock()
            # Persist the attempt before the transport call so the bound holds
            # even when hop-1 never confirms.
            self._append(lambda view, tid=candidate.task_id, trig=candidate.trigger, att=candidate.attempt: [publish_attempted_spec(view, tid, attempt_now, trigger=trig, attempt=att)])
            self.publish_calls.append((candidate.task_id, candidate.trigger, candidate.attempt))
            handle = self.client.publish(topic, payload, 1)
            confirmed = handle.wait(PUBLISH_WAIT_S)
            self.emit({"event": "task_request_published", "task_id": candidate.task_id, "trigger": candidate.trigger, "attempt": candidate.attempt, "hop1_confirmed": confirmed})
            if confirmed:
                confirm_now = self.clock()
                self._append(lambda view, tid=candidate.task_id, trig=candidate.trigger, att=candidate.attempt: [publish_confirmed_spec(view, tid, confirm_now, trigger=trig, attempt=att)])

    def run(self, *, max_seconds: float | None = None) -> int:
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        last_tick = 0.0
        while self.failure is None:
            self.client.loop(min(self.config.tick_interval_s, 0.25))
            if time.monotonic() - last_tick >= self.config.tick_interval_s:
                self.tick()
                last_tick = time.monotonic()
            if deadline is not None and time.monotonic() >= deadline:
                break
        return 0 if self.failure is None else 2

    # -- read models ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        view = self.core.view
        return {
            "disclaimer": DISCLAIMER,
            "tasks": {task_id: task.summary() for task_id, task in view.tasks.items()},
            "devices": {robot_id: device.summary() for robot_id, device in view.devices.items()},
        }


def _summary(record) -> dict[str, Any]:
    payload = record.payload
    keys = ("task_id", "robot_id", "disposition", "state_after", "code", "reason", "to", "from")
    out: dict[str, Any] = {}
    for key in keys:
        if key in payload:
            out[key] = payload[key]
    value = payload.get("event")
    if hasattr(value, "get"):
        # Nested under a distinct key so the emitted line's "event" stays the record kind.
        out["task_event"] = {"kind": value.get("kind"), "boot_sequence": value.get("boot_sequence"), "event_sequence": value.get("event_sequence"), "reason_code": value.get("reason_code")}
    return out


def _json_line(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False), flush=True)


def load_config(path: Path) -> EdgeTaskConfig:
    return EdgeTaskConfig.from_json(path.read_bytes())


def acquire_instance_lock(journal_file: Path):
    lock_path = journal_file.parent / ".edge.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise SystemExit(f"another Edge gateway holds {lock_path}; refusing to start a second instance") from exc
    return handle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=SIM_ROOT, help="root for relative evidence_dir values")
    parser.add_argument("--max-seconds", type=float, default=None, help="bounded run for smoke tests")
    args = parser.parse_args(argv)

    from scripts.pilot_course_a_task_fixture import admission_facts, commissioned_site  # noqa: E402

    config = load_config(args.config)
    site = commissioned_site()
    facts = admission_facts(site)
    if (config.site_id, config.deployment_id) != (facts.site_id, facts.deployment_id):
        raise SystemExit("config site/deployment do not match the commissioned fixture; refusing to start")
    if not set(config.robot_ids) <= facts.robot_ids:
        raise SystemExit("config lists a robot the commissioned fixture does not declare; refusing to start")
    path = journal_path(config, args.evidence_root)
    lock = acquire_instance_lock(path)
    journal = JsonlJournal(path, allowed_kinds=EDGE_RECORD_KINDS)
    client = PahoClient(config.edge_client_id, config.broker_host, config.broker_port, config.keepalive_s)
    gateway = EdgeGateway(config, facts, journal, client)

    stop = {"requested": False}

    def _stop(signum, frame):  # noqa: ANN001, ARG001
        stop["requested"] = True
        gateway.failure = gateway.failure or GatewayFailStop("signal")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        gateway.start()
        code = gateway.run(max_seconds=args.max_seconds)
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        lock.close()
    if stop["requested"]:
        _json_line({"event": "edge_stopped", "reason": "signal"})
        return 0
    return code


if __name__ == "__main__":
    raise SystemExit(main())
