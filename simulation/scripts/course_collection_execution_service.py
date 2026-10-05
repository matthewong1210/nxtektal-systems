#!/usr/bin/env python3
"""Serve one Planning-bound V3 collection execution on loopback.

SIMULATION ONLY.  One ``CourseCollectionExecutionDemo`` owns the V3 session,
simulator-backed device, Edge journal, Planning records and in-memory broker.
Exactly one background loop may advance it.  HTTP callbacks hold only the
composition lock and are read-only with respect to execution.

The demo is intentionally fixed to its one seeded confirmation.  New planning
inputs, plans, confirmations and dated schedules are rejected because this
service has no admission path that could promise them an execution result.
Manual Planning outcomes and local notification handling retain their existing
evidence-only semantics.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import signal
import sys
import threading
from typing import Any, Callable

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_task.contracts import utc_text  # noqa: E402
from nxt_edge_task.journal import PreconditionFailed  # noqa: E402
from nxt_site_agent import (  # noqa: E402
    SiteAgentApiServer,
    SiteAgentError,
    SiteAgentService,
)
from nxt_workflow_enablement import RANGE_OPS_WORKFLOW_ID  # noqa: E402
from scripts import course_collection_execution as execution_api  # noqa: E402
from scripts import course_session_v3  # noqa: E402
from scripts.course_collection_execution_demo import (  # noqa: E402
    CourseCollectionExecutionDemo,
    DISCLAIMER,
)
from scripts.edge_task_cli import list_view  # noqa: E402
from scripts.site_agent_fixture import (  # noqa: E402
    DEPLOYMENT_ID,
    SITE_ID,
    service_composition_seam,
)
from scripts.task_ops_service_capabilities import (  # noqa: E402
    task_ops_service_capabilities,
)


_TERMINAL_EXECUTIONS = frozenset(
    {"SUCCEEDED", "PARTIAL", "REJECTED", "MISSED", "FAILED", "INCONCLUSIVE"}
)
_DRIVER_DONE = frozenset({"COMPLETED", "ENDED", "PROTECTED", "FAILED"})


class CollectionExecutionServiceRuntime:
    """Thread-safe service composition around one durable demo instance."""

    def __init__(
        self,
        root: str | Path,
        *,
        initialize: bool = False,
        wall_clock: Callable[[], Any] | None = None,
        step_interval_s: float = 6.0,
    ) -> None:
        if (
            isinstance(step_interval_s, bool)
            or not isinstance(step_interval_s, (int, float))
            or not math.isfinite(step_interval_s)
            or step_interval_s <= 0
            or step_interval_s > threading.TIMEOUT_MAX
        ):
            raise ValueError(
                "step_interval_s must be positive, finite and within the "
                "platform thread timeout"
            )
        self.root = Path(root)
        demo_kwargs: dict[str, Any] = {"initialize": initialize}
        if wall_clock is not None:
            demo_kwargs["wall_clock"] = wall_clock
        self.demo = CourseCollectionExecutionDemo(self.root, **demo_kwargs)
        self.step_interval_s = float(step_interval_s)
        self.failure: str | None = None
        self.driver_state = "STOPPED"
        self.started = False
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _require_started(self) -> None:
        if not self.started:
            raise SiteAgentError(
                "task_ops_unavailable", "collection execution service is not running"
            )

    def _fail_unlocked(self, exc: BaseException) -> None:
        if self.failure is None:
            if isinstance(exc, SiteAgentError):
                self.failure = f"{exc.code}: {exc.detail}"
            else:
                self.failure = f"{type(exc).__name__}: {exc}"
        self.driver_state = "FAILED"
        self._stop.set()

    def _classify_unlocked(self) -> None:
        status = self.demo.runtime_status()
        snapshot = self.demo.collection_executions()
        executions = snapshot["executions"]
        if len(executions) != 1:
            self._fail_unlocked(
                RuntimeError("fixed collection execution service requires one execution")
            )
            return
        record = executions[0]
        if status["session_state"] == "PAUSED":
            self.driver_state = "PAUSED"
            return
        if status["session_state"] == "ENDED":
            if record["state"] not in _TERMINAL_EXECUTIONS:
                self._fail_unlocked(
                    RuntimeError(
                        "V3 session ended without terminal execution evidence"
                    )
                )
                return
            self.driver_state = "ENDED"
            self._stop.set()
            return
        protection = record["device_protection"]
        if protection["protected"] or protection["authorization_blocked"]:
            self.driver_state = "PROTECTED"
            self._stop.set()
            return
        if record["state"] in _TERMINAL_EXECUTIONS:
            self.driver_state = "COMPLETED"
            self._stop.set()
            return
        self.driver_state = "RUNNING"

    def start(self) -> None:
        """Initialize and bind the fixed execution without arming live ticks."""

        with self._lock:
            if self.started:
                raise RuntimeError("collection execution service already started")
            self._stop.clear()
            self.driver_state = "STARTING"
            try:
                self.demo.start()
                self.demo.ensure_execution()
                self.started = True
                self._classify_unlocked()
            except Exception:
                self.started = False
                self.driver_state = "STOPPED"
                self.demo.close()
                raise

    def start_driver(self) -> bool:
        """Arm the sole live driver, or stay read-only for an already done root."""

        with self._lock:
            self._require_started()
            if self._thread is not None:
                raise RuntimeError("collection execution driver already started")
            if self.driver_state in _DRIVER_DONE:
                return False
            self._thread = threading.Thread(
                target=self._run,
                name="collection-execution-driver",
                daemon=True,
            )
            self._thread.start()
            return True

    def tick(self) -> None:
        """Advance at most one live tick; any exception permanently stops this process."""

        with self._lock:
            if not self.started or self.failure is not None:
                return
            if self.driver_state in _DRIVER_DONE:
                return
            try:
                self._classify_unlocked()
                if self.driver_state != "RUNNING":
                    return
                self.demo.advance_once()
                self._classify_unlocked()
            except Exception as exc:  # fail-stop; explicit restart owns recovery
                self._fail_unlocked(exc)

    def _run(self) -> None:
        # The initial wait makes the durable PENDING state observable by the
        # console before the first live simulator tick.  Wall cadence is not a
        # business clock and never enters V3 identities or journals.
        try:
            while not self._stop.wait(self.step_interval_s):
                self.tick()
        except Exception as exc:
            with self._lock:
                if self.started:
                    self._fail_unlocked(exc)

    def collection_executions(self) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            return self.demo.collection_executions()

    def collection_execution_request(self, request_id: str) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            root = self.demo.session_root
            if root.is_symlink():
                raise ValueError("V3 session root must not be a symlink")
            # This is the same verified, non-sealing read used by the V3
            # snapshot.  Keep it in the 3C composition root: changing the
            # frozen V3 engine source would intentionally change engine_digest.
            with course_session_v3._lock(root / ".session.lock"):
                session, _saved, _control = (
                    course_session_v3._validated_read_runtime(root)
                )
                return session.store.request_result(request_id)

    def task_operations_snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            assert self.demo.planning is not None and self.demo.gateway is not None
            simulation_now = self.demo.simulation_clock()
            edge = list_view(
                self.demo.edge_journal,
                self.demo.config,
                now=simulation_now,
            )
            gateway_failure = self.demo.gateway.failure
            failure = self.failure
            if failure is None and gateway_failure is not None:
                failure = str(gateway_failure)
            result = self.demo.planning.schedules.snapshot(simulation_now)
            result.update(
                {
                    "schema": "nxt-pilot-dispatch/v0",
                    "environment": "SIMULATION",
                    "disclaimer": DISCLAIMER,
                    "server_time_utc": utc_text(self.demo.wall_clock()),
                    "scheduler": {
                        "state": "FAILED" if failure is not None else "RUNNING",
                        "detail": failure,
                    },
                    "service_capabilities": task_ops_service_capabilities(
                        "FIXED_V3_EXECUTION"
                    ),
                    "runtime": {
                        **self.demo.runtime_status(),
                        "driver_state": self.driver_state,
                        "fixed_confirmation": True,
                        "accepts_new_confirmations": False,
                        "accepts_new_schedules": False,
                    },
                    "devices": edge["devices"],
                    "tasks": edge["tasks"],
                    "available_robots": [
                        robot_id
                        for robot_id in self.demo.config.robot_ids
                        if self.demo.config.robot(robot_id).task_types
                    ],
                    "available_zones": sorted(self.demo.facts.zone_ids),
                    "transport": "in_memory",
                }
            )
            return result

    def route_task_operations(
        self, method: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            if method == "GET" and path == "/api/v0/task-ops":
                return self.task_operations_snapshot()
            if method != "POST":
                raise SiteAgentError("not_found", "unknown task operations route")
            if self.failure is not None:
                raise SiteAgentError("task_ops_unavailable", self.failure)
            prefix = "/api/v0/task-ops/"
            parts = path[len(prefix) :].split("/") if path.startswith(prefix) else []
            if (
                len(parts) == 3
                and parts[0] == "notifications"
                and parts[2] in {"acknowledge", "resolve"}
            ):
                if set(body) != {"operator", "note"}:
                    raise SiteAgentError(
                        "invalid_request",
                        "notification handling requires operator and note",
                    )
                assert self.demo.planning is not None
                handler = (
                    self.demo.planning.schedules.acknowledge
                    if parts[2] == "acknowledge"
                    else self.demo.planning.schedules.resolve
                )
                try:
                    return handler(
                        parts[1], body["operator"], body["note"],
                        self.demo.simulation_clock(),
                    )
                except PreconditionFailed as exc:
                    raise SiteAgentError(
                        "task_ops_conflict", f"{exc.code}: {exc.detail}"
                    ) from exc
                except Exception as exc:
                    self._fail_unlocked(exc)
                    raise SiteAgentError(
                        "task_ops_unavailable", self.failure or str(exc)
                    ) from exc
            if path == "/api/v0/task-ops/schedules" or (
                len(parts) == 3
                and parts[0] == "schedules"
                and parts[2] == "cancel"
            ):
                raise SiteAgentError(
                    "task_ops_conflict",
                    "this fixed V3 demo does not admit or cancel schedules",
                )
            raise SiteAgentError("not_found", "unknown task operations route")

    def route_planning(
        self, method: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            assert self.demo.planning is not None
            try:
                if method == "GET":
                    return self.demo.planning.route(method, path, body)
                if method == "POST" and self.failure is not None:
                    raise SiteAgentError("planning_unavailable", self.failure)
                if method == "POST" and path == "/api/v1/planning/outcomes":
                    return self.demo.planning.route(method, path, body)
                if method == "POST" and path in {
                    "/api/v1/planning/inputs",
                    "/api/v1/planning/plans",
                    "/api/v1/planning/confirmations",
                }:
                    raise SiteAgentError(
                        "planning_conflict",
                        "this service is fixed to one prebound Planning confirmation",
                    )
                raise SiteAgentError(
                    "planning_not_found", "unknown planning route"
                )
            except SiteAgentError as exc:
                if exc.code in {
                    "planning_unavailable",
                    "planning_result_unknown",
                }:
                    self._fail_unlocked(exc)
                raise
            except Exception as exc:
                self._fail_unlocked(exc)
                code = (
                    "planning_result_unknown"
                    if method == "POST"
                    else "planning_unavailable"
                )
                raise SiteAgentError(code, self.failure or str(exc)) from exc

    def api_callbacks(self) -> dict[str, Callable]:
        """Return the complete same-instance callback set for SiteAgentApiServer."""

        return {
            "task_operations": self.route_task_operations,
            "planning_operations": self.route_planning,
            "collection_executions": self.collection_executions,
            "collection_execution_request": self.collection_execution_request,
            "collection_execution_parser": (
                execution_api.parse_collection_execution_read_contract
            ),
        }

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10.0)
        with self._lock:
            self._thread = None
            self.demo.close()
            self.started = False
            if self.driver_state not in _DRIVER_DONE:
                self.driver_state = "STOPPED"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--driver-interval", type=float, default=6.0)
    parser.add_argument(
        "--console",
        type=Path,
        default=SIM_ROOT.parent / "apps/site-agent-console/out",
    )
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="serve the local API without requiring a console export",
    )
    args = parser.parse_args(argv)

    runtime = CollectionExecutionServiceRuntime(
        args.out,
        initialize=args.initialize,
        step_interval_s=args.driver_interval,
    )
    service = None
    server = None
    stop = threading.Event()
    try:
        # Initialization must precede creation of the sibling Site Agent
        # fixture root because the V3 demo enforces an empty evidence root.
        runtime.start()
        service = SiteAgentService.launch(
            runs_root=args.out / "site-agent",
            site_id=SITE_ID,
            deployment_id=DEPLOYMENT_ID,
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            seam=service_composition_seam(),
        )
        server = SiteAgentApiServer(
            service,
            port=args.port,
            console_dir=None if args.api_only else args.console,
            **runtime.api_callbacks(),
        )
        server.start_background()
        # The PENDING read is available before the first driver wait/tick.
        runtime.start_driver()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda _sig, _frame: stop.set())
        print(
            json.dumps(
                {
                    "url": server.url,
                    "disclaimer": DISCLAIMER,
                    "transport": "in_memory",
                    "scope": "one fixed Planning confirmation",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        while not stop.wait(0.5):
            # Keep read APIs online after completion, protection or fail-stop.
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
