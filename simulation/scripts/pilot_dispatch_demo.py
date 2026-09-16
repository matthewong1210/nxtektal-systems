#!/usr/bin/env python3
"""One local console for dated collection rehearsals and operator notifications.

SIMULATION ONLY. The default transport is the existing in-memory broker double;
both robots are protocol doubles. No vendor connection or physical action exists.
The facility/advice panels remain an independent, clearly labelled fixture.

First launch requires --initialize and a new, empty evidence directory. Restart
with the same --out and without --initialize; lost device journals fail closed.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_task.cases import EDGE_RECORD_KINDS  # noqa: E402
from nxt_edge_task.contracts import EdgeTaskConfig, utc_text  # noqa: E402
from nxt_edge_task.executor import ROBOT_RECORD_KINDS  # noqa: E402
from nxt_edge_task.journal import JsonlJournal, PreconditionFailed  # noqa: E402
from nxt_edge_task.schedules import ScheduleService  # noqa: E402
from nxt_site_agent import SiteAgentApiServer, SiteAgentError, SiteAgentService  # noqa: E402
from nxt_workflow_enablement import RANGE_OPS_WORKFLOW_ID  # noqa: E402
from scripts.edge_task_cli import list_view  # noqa: E402
from scripts.edge_task_gateway_v0 import EdgeGateway  # noqa: E402
from scripts.edge_task_transport import InMemoryBroker  # noqa: E402
from scripts.mock_robot_task_device import MockRobotDevice  # noqa: E402
from scripts.pilot_course_a_task_fixture import admission_facts  # noqa: E402
from scripts.site_agent_fixture import DEPLOYMENT_ID, SITE_ID, service_composition_seam  # noqa: E402

DISCLAIMER = "SIMULATION ONLY — scheduled protocol rehearsal; no physical robot or live customer data"
CONFIG_PATH = SIM_ROOT / "configs/edge_task/pilot-course-a.sim.example.json"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PilotDispatchRuntime:
    """Composition root; all domain decisions remain in their existing owners."""

    def __init__(
        self,
        root: Path,
        *,
        initialize: bool = False,
        behavior: str = "accept_and_succeed",
        clock: Callable[[], datetime] = utcnow,
        step_interval_s: float = 2.0,
    ) -> None:
        self.root = Path(root)
        self.clock = clock
        self.behavior = behavior
        self.step_interval_s = step_interval_s
        self.initialize = initialize
        self.config = EdgeTaskConfig.from_json(CONFIG_PATH.read_bytes())
        self.facts = admission_facts()
        self.journal = JsonlJournal(self.root / "edge" / "edge_task_journal.jsonl", allowed_kinds=EDGE_RECORD_KINDS)
        self.schedules = ScheduleService(self.journal, self.config, self.facts)
        self.broker = InMemoryBroker()
        self.devices: list[MockRobotDevice] = []
        self.gateway: EdgeGateway | None = None
        self.failure: str | None = None
        self.started = False
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process_lock = None
        self._edge_lock = None

    def start(self, *, background: bool = True) -> None:
        with self._lock:
            if self.started or self._process_lock is not None:
                raise RuntimeError("this runner instance has already been started")
            self.root.mkdir(parents=True, exist_ok=True)
            lock = (self.root / ".pilot-dispatch.lock").open("a+b")
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock.close()
                raise RuntimeError("another pilot dispatch runner owns this evidence directory") from None
            self._process_lock = lock
            try:
                marker = self.root / "rehearsal.json"
                if self.initialize:
                    existing = [p for p in self.root.iterdir() if p.name != ".pilot-dispatch.lock"]
                    if existing:
                        raise RuntimeError("--initialize requires a new empty evidence directory; restart without it")
                    marker.write_text(json.dumps({"schema": "nxt-pilot-dispatch/evidence/v0", "environment": "SIMULATION"}) + "\n")
                else:
                    if not marker.exists():
                        raise RuntimeError("new evidence needs explicit --initialize; existing device evidence must never be silently replaced")
                    if json.loads(marker.read_text()) != {"schema": "nxt-pilot-dispatch/evidence/v0", "environment": "SIMULATION"}:
                        raise RuntimeError("evidence identity is invalid")
                    # If both the Edge journal and its anchor disappear, absence
                    # cannot masquerade as a fresh empty schedule store.
                    if not self.journal.path.exists() or not self.journal.anchor_path.exists():
                        raise RuntimeError("Edge evidence was lost; refusing a fresh schedule store")
                self.journal.path.parent.mkdir(parents=True, exist_ok=True)
                self._edge_lock = (self.journal.path.parent / ".edge.lock").open("a+b")
                fcntl.flock(self._edge_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.gateway = EdgeGateway(
                    self.config, self.facts, self.journal,
                    self.broker.client(self.config.edge_client_id),
                    clock=self.clock, transport_name="inmemory", emit=lambda _event: None,
                )
                self.gateway.start()
                for robot_id in self.config.robot_ids:
                    robot = self.config.robot(robot_id)
                    device = MockRobotDevice(
                        self.config, robot,
                        self.behavior if robot.role == "picker" else "standby",
                        JsonlJournal(self.root / "robots" / robot_id / "robot_task_journal.jsonl", allowed_kinds=ROBOT_RECORD_KINDS),
                        self.broker.client(robot.client_id),
                        clock=self.clock, emit=lambda _event: None,
                        initialize=self.initialize, step_interval_s=self.step_interval_s,
                    )
                    device.start()
                    self.devices.append(device)
                self.broker.pump()
                self.started = True
                # Bring heartbeat receipt and the Edge's restart grace up to date
                # before a due schedule is examined by the first loop iteration.
                if background:
                    self._thread = threading.Thread(target=self._run, name="pilot-dispatch", daemon=True)
                    self._thread.start()
            except Exception:
                self._close_resources()
                raise

    def tick(self) -> None:
        with self._lock:
            if not self.started or self.failure is not None:
                return
            try:
                self.broker.pump()
                for device in self.devices:
                    if device.core.exit_requested:
                        device.client.disconnect()
                        continue
                    device.tick()
                    if device.failure is not None:
                        raise RuntimeError(f"{device.robot.robot_id}: {device.failure}")
                self.broker.pump()
                assert self.gateway is not None
                self.gateway.tick()
                self.broker.pump()
                if self.gateway.failure is not None:
                    raise RuntimeError(str(self.gateway.failure))
                self.schedules.tick(self.clock())
            except Exception as exc:  # fail-stop; never continue with unreliable evidence
                self.failure = f"{type(exc).__name__}: {exc}"
                self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.config.tick_interval_s)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self.clock()
            edge = list_view(self.journal, self.config, now=now)
            data = self.schedules.snapshot(now)
            data.update({
                "schema": "nxt-pilot-dispatch/v0",
                "environment": "SIMULATION",
                "disclaimer": DISCLAIMER,
                "server_time_utc": utc_text(now),
                "scheduler": {"state": "FAILED" if self.failure is not None or not self.started else "RUNNING", "detail": self.failure},
                "devices": edge["devices"],
                "tasks": edge["tasks"],
                "available_robots": [r for r in self.config.robot_ids if self.config.robot(r).task_types],
                "available_zones": sorted(self.facts.zone_ids),
                "transport": "in_memory",
            })
            return data

    def route(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            try:
                if method == "GET" and path == "/api/v0/task-ops":
                    return self.snapshot()
                if method != "POST":
                    raise SiteAgentError("not_found", "unknown task operations route")
                if not self.started or self.failure is not None:
                    raise SiteAgentError("task_ops_unavailable", self.failure or "scheduler is not running")
                now = self.clock()
                if path == "/api/v0/task-ops/schedules":
                    return self.schedules.create(body, now)
                prefix = "/api/v0/task-ops/"
                parts = path[len(prefix):].split("/") if path.startswith(prefix) else []
                if len(parts) == 3 and parts[0] == "schedules" and parts[2] == "cancel":
                    if set(body) != {"operator"}:
                        raise SiteAgentError("invalid_request", "cancellation requires only operator")
                    return self.schedules.cancel(parts[1], body["operator"], now)
                if len(parts) == 3 and parts[0] == "notifications" and parts[2] in {"acknowledge", "resolve"}:
                    if set(body) != {"operator", "note"}:
                        raise SiteAgentError("invalid_request", "notification handling requires operator and note")
                    handler = self.schedules.acknowledge if parts[2] == "acknowledge" else self.schedules.resolve
                    return handler(parts[1], body["operator"], body["note"], now)
                raise SiteAgentError("not_found", "unknown task operations route")
            except PreconditionFailed as exc:
                raise SiteAgentError("task_ops_conflict", f"{exc.code}: {exc.detail}") from exc
            except SiteAgentError:
                raise
            except Exception as exc:
                # Any unexpected evidence failure also stops the scheduling loop.
                self.failure = f"{type(exc).__name__}: {exc}"
                self._stop.set()
                raise SiteAgentError("task_ops_unavailable", self.failure) from exc

    def _close_resources(self) -> None:
        for device in self.devices:
            device.client.disconnect()
        if self.gateway is not None:
            self.gateway.client.disconnect()
        for handle in (self._edge_lock, self._process_lock):
            if handle is not None:
                handle.close()
        self._edge_lock = None
        self._process_lock = None
        self.started = False

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        with self._lock:
            self._close_resources()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--console", type=Path, default=SIM_ROOT.parent / "apps/site-agent-console/out")
    parser.add_argument("--behavior", choices=("accept_and_succeed", "fail_cannot_continue", "help_needs_manual_recharge", "silent_after_accept"), default="accept_and_succeed")
    args = parser.parse_args(argv)
    runtime = PilotDispatchRuntime(args.out, initialize=args.initialize, behavior=args.behavior)
    service = None
    server = None
    stop = threading.Event()
    try:
        runtime.start()
        service = SiteAgentService.launch(
            runs_root=args.out / "site-agent", site_id=SITE_ID,
            deployment_id=DEPLOYMENT_ID, workflow_id=RANGE_OPS_WORKFLOW_ID,
            seam=service_composition_seam(),
        )
        server = SiteAgentApiServer(service, port=args.port, console_dir=args.console, task_operations=runtime.route)
        server.start_background()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda _sig, _frame: stop.set())
        print(json.dumps({"url": server.url, "disclaimer": DISCLAIMER, "transport": "in_memory"}), flush=True)
        while not stop.wait(0.5):
            # Keep the API available to report a fail-stop condition; only an
            # explicit operator restart can resume after the fault is repaired.
            pass
        return 0
    finally:
        if server is not None:
            server.shutdown()
        runtime.close()
        if service is not None:
            service.stop()


if __name__ == "__main__":
    raise SystemExit(main())
