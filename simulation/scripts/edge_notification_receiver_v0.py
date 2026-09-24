#!/usr/bin/env python3
"""Local Notification Test Receiver V0 -- SIMULATION only, loopback only.

Accepts ``POST /notifications`` from the intervention service, validates the
payload, persists one ``notification_received`` record per notification id
in its own journal (its own directory, its own anchor), and answers with a
receipt.  A repeated id is journaled as a duplicate attempt and answered
with the original receipt.  The receiver never reads the sender's storage.

Fault hooks (tests only): ``--drop-responses N`` persists the first N new
notifications but closes the connection without answering, so the sender
sees an unknown result and must retry with the same id.

Run from ``simulation/``::

    uv run --no-sync python -B scripts/edge_notification_receiver_v0.py \
        --config configs/edge_task/pilot-course-a.interventions.sim.example.json

A receipt from this receiver proves local persistence, not that any person
saw anything.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_interventions import RECEIVER_JOURNAL_SCHEMA, RECEIVER_RECORD_KINDS, InterventionConfig, ReceiverLedger, receipt_specs  # noqa: E402
from nxt_edge_interventions.contracts import is_loopback_literal  # noqa: E402
from nxt_edge_interventions.notify import receiver_started_spec  # noqa: E402
from nxt_edge_task.journal import JsonlJournal, RecordSpec  # noqa: E402

DISCLAIMER = "SIMULATION — local test receiver; a receipt proves persistence here, not human attention"
MAX_BODY = 65_536


def receiver_journal_path(config: InterventionConfig, root: Path) -> Path:
    return root / config.receiver.evidence_dir / "receiver_journal.jsonl"


def receiver_journal(config: InterventionConfig, root: Path) -> JsonlJournal:
    return JsonlJournal(receiver_journal_path(config, root), allowed_kinds=RECEIVER_RECORD_KINDS, schema=RECEIVER_JOURNAL_SCHEMA)


def _to_spec(spec) -> RecordSpec:
    return RecordSpec(record_kind=spec.record_kind, origin=spec.origin, recorded_at_utc=spec.recorded_at_utc, payload=dict(spec.payload))


class ReceiverState:
    """Journal-backed ledger shared by the request handlers (one lock)."""

    def __init__(self, config: InterventionConfig, journal: JsonlJournal, *, drop_responses: int = 0) -> None:
        self.config = config
        self.journal = journal
        self.ledger = ReceiverLedger(receiver_id=config.receiver.receiver_id)
        self.lock = threading.Lock()
        self.drop_responses = drop_responses
        self.dropped = 0

    def start(self) -> None:
        now = datetime.now(timezone.utc)
        appended = self.journal.append_via(self._builder(lambda _ledger: [receiver_started_spec(self.config.receiver.receiver_id, now, host=self.config.receiver.host, port=self.config.receiver.port)]))
        self.ledger.apply_all(appended)

    def _builder(self, decide):
        def build(records):
            self.ledger.apply_all(records)
            return [_to_spec(spec) for spec in decide(self.ledger)]

        return build

    def handle(self, payload: Any) -> tuple[dict[str, Any], int, bool]:
        """Persist first, then decide whether this answer is dropped (test hook)."""

        now = datetime.now(timezone.utc)
        with self.lock:
            outcome: dict[str, Any] = {}

            def decide(ledger: ReceiverLedger):
                body, specs, status = receipt_specs(ledger, payload if isinstance(payload, dict) else {}, now, site_id=self.config.site_id, deployment_id=self.config.deployment_id)
                outcome["body"], outcome["status"] = body, status
                return specs

            appended = self.journal.append_via(self._builder(decide))
            self.ledger.apply_all(appended)
            drop = False
            if outcome["status"] == 200 and not outcome["body"].get("duplicate") and self.dropped < self.drop_responses:
                self.dropped += 1
                drop = True
        return outcome["body"], outcome["status"], drop


class _Handler(BaseHTTPRequestHandler):
    state: ReceiverState

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/notifications":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > MAX_BODY:
            self.send_error(413 if length > MAX_BODY else 400)
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        body, status, drop = self.state.handle(payload)
        if drop:
            # Persisted, but the answer is lost on the way back (test hook).
            self.close_connection = True
            _json_line({"event": "response_dropped", "notification_id": payload.get("notification_id") if isinstance(payload, dict) else None})
            return
        encoded = json.dumps(body, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        _json_line({"event": "notification_received" if status == 200 and not body.get("duplicate") else ("duplicate_attempt" if status == 200 else "refused"), "notification_id": payload.get("notification_id") if isinstance(payload, dict) else None, "status": status})


def _json_line(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve(config: InterventionConfig, root: Path, *, drop_responses: int = 0, max_seconds: float | None = None) -> int:
    if not is_loopback_literal(config.receiver.host):
        raise SystemExit("receiver host must be a loopback literal; refusing to bind")
    state = ReceiverState(config, receiver_journal(config, root), drop_responses=drop_responses)
    state.start()
    handler = type("BoundHandler", (_Handler,), {"state": state})
    server = HTTPServer((config.receiver.host, config.receiver.port), handler)
    server.timeout = 0.5
    stop = {"requested": False}

    def _stop(signum, frame):  # noqa: ANN001, ARG001
        stop["requested"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    _json_line({"event": "receiver_started", "host": config.receiver.host, "port": config.receiver.port, "receiver_id": config.receiver.receiver_id, "unique_notifications": state.ledger.unique_count, "disclaimer": DISCLAIMER})
    import time

    deadline = None if max_seconds is None else time.monotonic() + max_seconds
    try:
        while not stop["requested"]:
            server.handle_request()
            if deadline is not None and time.monotonic() >= deadline:
                break
    finally:
        server.server_close()
    _json_line({"event": "receiver_stopped", "reason": "signal" if stop["requested"] else "deadline", "unique_notifications": state.ledger.unique_count})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=SIM_ROOT)
    parser.add_argument("--drop-responses", type=int, default=0, help="test hook: persist but do not answer the first N new notifications")
    parser.add_argument("--max-seconds", type=float, default=None)
    args = parser.parse_args(argv)
    config = InterventionConfig.from_json(args.config.read_text(encoding="utf-8"))
    return serve(config, args.evidence_root, drop_responses=max(0, args.drop_responses), max_seconds=args.max_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
