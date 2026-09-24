#!/usr/bin/env python3
"""Edge Intervention Service V0 -- SIMULATION human-handling cases and local notifications.

Composition root: reads the Edge task journal (read-only, like the task CLI
does), derives the Edge view with the upstream package, hands the view to
``nxt_edge_interventions`` as plain data, and appends that package's
decisions to the intervention journal.  Notification attempts go over
loopback HTTP to the local test receiver only; the attempt is journaled
before the request and its result after, under the same notification id
for every retry.

Run from ``simulation/`` (after the Edge gateway and the receiver)::

    uv run --no-sync python -B scripts/edge_intervention_service_v0.py \
        --edge-config configs/edge_task/pilot-course-a.sim.example.json \
        --config configs/edge_task/pilot-course-a.interventions.sim.example.json

Nothing here commands a device, edits Edge evidence, or reopens any
authorization gate.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_interventions import (  # noqa: E402
    INTERVENTION_JOURNAL_SCHEMA,
    INTERVENTION_RECORD_KINDS,
    EdgeSnapshot,
    InterventionConfig,
    InterventionCore,
    InterventionError,
    decide_tick,
    due_attempts,
    reconcile,
)
from nxt_edge_interventions.cases import attempt_spec, result_spec, started_spec  # noqa: E402
from nxt_edge_interventions.contracts import RESULT_DELIVERED, RESULT_FAILED, RESULT_UNKNOWN, is_loopback_literal  # noqa: E402
from nxt_edge_task.cases import EDGE_RECORD_KINDS, derive_edge_view  # noqa: E402
from nxt_edge_task.contracts import EdgeTaskConfig  # noqa: E402
from nxt_edge_task.journal import JournalIntegrityError, JsonlJournal, PreconditionFailed, RecordSpec  # noqa: E402

DISCLAIMER = "SIMULATION — human-handling rehearsal; local test receiver only, no real contact"


class ServiceFailStop(RuntimeError):
    """The service can no longer make durable progress; stop loudly."""


class ServiceAlreadyRunning(RuntimeError):
    """Another dispatcher owns this intervention journal's runtime lock."""


class ServiceRunLock:
    """Non-blocking process-lifetime qualification, separate from journal locks."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> "ServiceRunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise ServiceAlreadyRunning(
                f"an intervention dispatcher already owns runtime lock {self.path}"
            ) from exc
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def edge_journal_path(config: EdgeTaskConfig, root: Path) -> Path:
    return root / config.edge_evidence_dir / config.site_id / config.deployment_id / "edge_task_journal.jsonl"


def intervention_journal_path(config: InterventionConfig, root: Path) -> Path:
    return root / config.evidence_dir / config.site_id / config.deployment_id / "edge_interventions_journal.jsonl"


def intervention_journal(config: InterventionConfig, root: Path) -> JsonlJournal:
    return JsonlJournal(intervention_journal_path(config, root), allowed_kinds=INTERVENTION_RECORD_KINDS, schema=INTERVENTION_JOURNAL_SCHEMA)


def service_run_lock_path(config: InterventionConfig, root: Path) -> Path:
    """One dispatcher qualification for one persistent intervention journal."""

    return intervention_journal_path(config, root).with_name("edge_interventions_service.lock")


def _to_spec(spec) -> RecordSpec:
    return RecordSpec(record_kind=spec.record_kind, origin=spec.origin, recorded_at_utc=spec.recorded_at_utc, payload=dict(spec.payload))


def edge_snapshot(edge_config: EdgeTaskConfig, edge_journal: JsonlJournal) -> EdgeSnapshot:
    """The Edge view as plain data: summaries plus the bounds the engine needs."""

    view = derive_edge_view(edge_config, edge_journal.read())
    return EdgeSnapshot.from_dict(
        {
            "tasks": {task_id: task.summary() for task_id, task in view.tasks.items()},
            "devices": {robot_id: device.summary() for robot_id, device in view.devices.items()},
            "edge": {"last_record_id": view.last_record_id, "max_republish_attempts": edge_config.max_republish_attempts},
        }
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Even a loopback redirect is not the configured receiver.


class HttpNotificationTransport:
    """POSTs one notification to the loopback receiver; classifies the outcome."""

    def __init__(self, url: str, timeout_s: float, host: str) -> None:
        if not is_loopback_literal(host):
            raise InterventionError("invalid_config", "receiver host must be a loopback literal")
        endpoint = urllib.parse.urlsplit(url)
        if endpoint.scheme != "http" or endpoint.hostname != host or endpoint.username is not None or endpoint.password is not None:
            raise InterventionError("invalid_config", "receiver URL must be HTTP at the configured loopback literal, without credentials")
        self.url = url
        self.timeout_s = timeout_s
        # A per-transport opener neither trusts environment proxies nor follows
        # Location headers beyond the configured loopback endpoint.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def send(self, payload: dict[str, Any]) -> tuple[str, str | None, str]:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, method="POST", headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout_s) as response:
                answer = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return RESULT_FAILED, None, f"http {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return RESULT_UNKNOWN, None, f"{type(exc).__name__}: {str(exc)[:200]}"
        receipt = answer.get("receipt_id") if isinstance(answer, dict) else None
        if not isinstance(receipt, str) or not receipt:
            return RESULT_UNKNOWN, None, "response without receipt id"
        return RESULT_DELIVERED, receipt, "duplicate receipt" if answer.get("duplicate") else "receipt"


class InterventionService:
    """Drives derivation, notification delivery, and reminders over one journal."""

    def __init__(
        self,
        config: InterventionConfig,
        edge_config: EdgeTaskConfig,
        journal: JsonlJournal,
        edge_journal: JsonlJournal,
        transport: Any,
        *,
        clock: Callable[[], datetime] = utcnow,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.edge_config = edge_config
        self.journal = journal
        self.edge_journal = edge_journal
        self.transport = transport
        self.clock = clock
        self.emit = emit or _json_line
        self.core = InterventionCore(config)
        self.failure: Exception | None = None
        self.send_calls: list[str] = []

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        now = self.clock()
        self._append(lambda _view: [started_spec(self.config, now)])
        self.emit({"event": "intervention_service_started", "at": now.isoformat(), "cases": len(self.core.view.cases), "disclaimer": DISCLAIMER})

    def _append(self, decide):
        try:
            appended = self.journal.append_via(self._builder(decide))
        except (JournalIntegrityError, PreconditionFailed, InterventionError, OSError) as exc:
            failure = ServiceFailStop(f"journal append failed: {type(exc).__name__}: {exc}")
            self._fail_stop(failure)
            raise failure from exc
        self.core.absorb(appended)
        return appended

    def _builder(self, decide):
        inner = self.core.builder(decide)

        def build(records):
            return [_to_spec(spec) for spec in inner(records)]

        return build

    def _fail_stop(self, error: Exception) -> None:
        self.failure = error
        self.emit({"event": "fail_stop", "reason": str(error)})

    # -- one tick -------------------------------------------------------------

    def tick(self) -> None:
        if self.failure is not None:
            return
        # These obligations are already durable in our journal. Repair them
        # before reading fresh Edge evidence or assigning reminder ordinals.
        appended = self._append(lambda view: list(view.pending_intents.values()))
        for record in appended:
            self.emit({"event": record.record_kind, "sequence": record.sequence, **_summary(record)})
        now = self.clock()
        try:
            snapshot = edge_snapshot(self.edge_config, self.edge_journal)
        except (JournalIntegrityError, InterventionError, OSError) as exc:
            # No new case/evidence can be derived from an unreadable snapshot.
            # Delivery of already committed notification obligations continues.
            self.emit({"event": "edge_snapshot_unavailable", "reason": str(exc)[:300]})
            snapshot = None
        if snapshot is not None:
            appended = self._append(lambda view: reconcile(view, snapshot, now))
            for record in appended:
                self.emit({"event": record.record_kind, "sequence": record.sequence, **_summary(record)})
        appended = self._append(lambda view: decide_tick(view, self.clock()))
        for record in appended:
            self.emit({"event": record.record_kind, "sequence": record.sequence, **_summary(record)})
        self._deliver()

    def _deliver(self) -> None:
        for notification in due_attempts(self.core.view.notifications, self.config, self.clock()):
            if self.failure is not None:
                return
            live = self.core.view.notifications[notification.notification_id]
            attempt_now = self.clock()
            # The attempt is durable before the socket opens, so a crash in
            # flight leaves an attempt without a result, never a lost intent.
            self._append(lambda view, nid=live.notification_id: [attempt_spec(view.notifications[nid], attempt_now)])
            self.send_calls.append(live.notification_id)
            result, receipt_id, detail = self.transport.send(dict(live.payload))
            result_now = self.clock()
            self._append(lambda view, nid=live.notification_id, r=result, rid=receipt_id, d=detail: [result_spec(view.notifications[nid], r, result_now, receipt_id=rid, detail=d)])
            self.emit({"event": "notification_attempt", "notification_id": live.notification_id, "attempt": self.core.view.notifications[live.notification_id].attempts, "result": result, "receipt_id": receipt_id})

    def run(self, *, max_seconds: float | None = None) -> int:
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        while self.failure is None:
            self.tick()
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(self.config.tick_interval_s)
        return 0 if self.failure is None else 2

    def snapshot(self) -> dict[str, Any]:
        return {"disclaimer": DISCLAIMER, **self.core.view.snapshot()}


def _summary(record) -> dict[str, Any]:
    payload = record.payload
    out: dict[str, Any] = {}
    for key in ("case_id", "kind", "severity", "subject_id", "notification_id", "intent", "result", "attempt", "code", "detail"):
        if key in payload:
            out[key] = payload[key]
    return out


def _json_line(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load_edge_config(path: Path) -> EdgeTaskConfig:
    return EdgeTaskConfig.from_json(path.read_text(encoding="utf-8"))


def load_config(path: Path) -> InterventionConfig:
    return InterventionConfig.from_json(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edge-config", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=SIM_ROOT, help="root for relative evidence_dir values")
    parser.add_argument("--max-seconds", type=float, default=None, help="bounded run for smoke tests")
    args = parser.parse_args(argv)

    edge_config = load_edge_config(args.edge_config)
    config = load_config(args.config)
    if (config.site_id, config.deployment_id) != (edge_config.site_id, edge_config.deployment_id):
        raise SystemExit("intervention config site/deployment do not match the Edge config; refusing to start")
    if config.simulation_env_id != edge_config.simulation_env_id:
        raise SystemExit("intervention config simulation_env_id does not match the Edge config; refusing to start")
    journal = intervention_journal(config, args.evidence_root)
    edge_journal = JsonlJournal(edge_journal_path(edge_config, args.evidence_root), allowed_kinds=EDGE_RECORD_KINDS)
    transport = HttpNotificationTransport(config.receiver.url, config.receiver.timeout_s, config.receiver.host)
    service = InterventionService(config, edge_config, journal, edge_journal, transport)

    stop = {"requested": False}

    def _stop(signum, frame):  # noqa: ANN001, ARG001
        stop["requested"] = True
        service.failure = service.failure or ServiceFailStop("signal")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        with ServiceRunLock(service_run_lock_path(config, args.evidence_root)):
            service.start()
            code = service.run(max_seconds=args.max_seconds)
    except ServiceAlreadyRunning as exc:
        _json_line({"event": "intervention_service_already_running", "reason": str(exc)})
        code = 2
    except ServiceFailStop:
        code = 2
    if stop["requested"]:
        _json_line({"event": "intervention_service_stopped", "reason": "signal"})
        return 0
    return code


if __name__ == "__main__":
    raise SystemExit(main())
