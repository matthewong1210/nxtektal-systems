#!/usr/bin/env python3
"""Continuous SIMULATION composition over one durable V3 course session.

Planning, dated schedules, Edge lifecycle evidence, collection execution
evidence, and simulator stepping remain in their existing owners.  This module
serializes those owners behind one process lock and one background driver.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import math
from pathlib import Path
import sys
import threading
from typing import Any, Callable, Mapping

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_task.cases import (  # noqa: E402
    EDGE_RECORD_KINDS,
    TASK_CREATED,
)
from nxt_edge_task.contracts import (  # noqa: E402
    EdgeTaskConfig,
    TaskRequest,
    canonical_bytes,
    event_topic,
    parse_utc,
    status_topic,
    utc_text,
)
from nxt_edge_task.journal import JsonlJournal  # noqa: E402
from nxt_pilot_ops.planning_contracts import PlanningError  # noqa: E402
from nxt_pilot_ops.planning_workflow import (  # noqa: E402
    INPUT,
    PLANNING_RECORD_KINDS,
)
from nxt_site_agent import SiteAgentError  # noqa: E402
from scripts import course_collection_execution as execution_api  # noqa: E402
from scripts import course_session_v3  # noqa: E402
from scripts.course_collection_execution_demo import (  # noqa: E402
    CONFIG_PATH,
    DISCLAIMER,
    ROBOT_ID,
    v3_config,
)
from scripts.course_session_task_device import (  # noqa: E402
    SimulatorBackedTaskDevice,
)
from scripts.edge_task_cli import list_view  # noqa: E402
from scripts.edge_task_gateway_v0 import EdgeGateway  # noqa: E402
from scripts.edge_task_transport import InMemoryBroker  # noqa: E402
from scripts.joint_learning import atomic_json, read_json  # noqa: E402
from scripts.pilot_course_a_task_fixture import (  # noqa: E402
    admission_facts,
    commissioned_site,
)
from scripts.planning_operations import PlanningOperations  # noqa: E402
from scripts.task_ops_service_capabilities import (  # noqa: E402
    task_ops_service_capabilities,
)


MARKER = {
    "schema": "nxt-course-continuous-collection-service/v1",
    "environment": "SIMULATION",
}
_TERMINAL_EXECUTIONS = frozenset(
    {"SUCCEEDED", "PARTIAL", "REJECTED", "MISSED", "FAILED", "INCONCLUSIVE"}
)
_DRIVER_DONE = frozenset({"ENDED", "PROTECTED", "FAILED"})


def _wall_utc() -> datetime:
    return datetime.now(timezone.utc)


def classify_continuous_runtime(
    runtime: Mapping[str, Any], executions: list[Mapping[str, Any]]
) -> str:
    """Classify session health without treating ordinary history as completion."""

    if runtime["session_state"] == "PAUSED":
        return "PAUSED"
    if any(
        row["device_protection"]["protected"]
        or row["device_protection"]["authorization_blocked"]
        for row in executions
    ):
        return "PROTECTED"
    if runtime["session_state"] == "ENDED":
        if any(row["state"] not in _TERMINAL_EXECUTIONS for row in executions):
            raise RuntimeError("V3 session ended with nonterminal execution")
        return "ENDED"
    return "RUNNING"


class ContinuousCollectionExecutionRuntime:
    """One serialized V4 composition and, optionally, one background driver."""

    def __init__(
        self,
        root: str | Path,
        *,
        initialize: bool = False,
        wall_clock: Callable[[], datetime] = _wall_utc,
        step_interval_s: float = 6.0,
        crash_hook: Callable[[str, Mapping[str, Any]], None] | None = None,
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
        if not callable(wall_clock):
            raise TypeError("wall_clock must be callable")
        if crash_hook is not None and not callable(crash_hook):
            raise TypeError("crash_hook must be callable or None")

        self.root = Path(root)
        self.session_root = self.root / "session-v3"
        self.initialize = initialize
        self.wall_clock = wall_clock
        self.step_interval_s = float(step_interval_s)
        self.crash_hook = crash_hook
        self.config = EdgeTaskConfig.from_json(CONFIG_PATH.read_bytes())
        self.site = commissioned_site()
        self.facts = admission_facts(self.site)
        self.edge_journal = JsonlJournal(
            self.root / "edge" / "edge_task_journal.jsonl",
            allowed_kinds=EDGE_RECORD_KINDS | PLANNING_RECORD_KINDS,
        )

        self.broker: InMemoryBroker | None = None
        self.gateway: EdgeGateway | None = None
        self.publisher = None
        self.device: SimulatorBackedTaskDevice | None = None
        self.planning: PlanningOperations | None = None
        self.session_identity: dict[str, Any] | None = None
        self.failure: str | None = None
        self.driver_state = "STOPPED"
        self.started = False
        self._process_lock = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _require_started(self) -> None:
        if not self.started:
            raise SiteAgentError(
                "task_ops_unavailable",
                "continuous collection execution service is not running",
            )

    def _require_device(self) -> SimulatorBackedTaskDevice:
        if self.device is None:
            raise RuntimeError("continuous collection device is unavailable")
        return self.device

    def _require_planning(self) -> PlanningOperations:
        if self.planning is None:
            raise RuntimeError("continuous Planning composition is unavailable")
        return self.planning

    def _fail_unlocked(self, exc: BaseException) -> None:
        if self.failure is None:
            if isinstance(exc, SiteAgentError):
                self.failure = f"{exc.code}: {exc.detail}"
            else:
                self.failure = f"{type(exc).__name__}: {exc}"
        self.driver_state = "FAILED"
        self._stop.set()

    def _prepare_root(self) -> None:
        if self.root.is_symlink():
            raise ValueError("continuous service root must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        lock = (self.root / ".collection-execution.lock").open("a+b")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise RuntimeError(
                "another collection execution runner owns this root"
            ) from None
        self._process_lock = lock

        marker = self.root / "continuous-service.json"
        if self.initialize:
            existing = [
                path
                for path in self.root.iterdir()
                if path.name != ".collection-execution.lock"
            ]
            if existing:
                raise RuntimeError(
                    "--initialize requires a new empty output directory"
                )
            atomic_json(marker, MARKER)
            return
        if not marker.exists() or read_json(marker) != MARKER:
            raise RuntimeError(
                "new evidence requires --initialize; continuous service identity "
                "is absent or invalid"
            )

    def _advance_v3_unlocked(self, config: Mapping[str, Any] | None = None) -> dict:
        return course_session_v3.run(
            self.session_root, config, crash_hook=self.crash_hook
        )

    def _initialize_or_repair_v3(self) -> None:
        if self.initialize:
            state = self._advance_v3_unlocked(
                v3_config(self.config, self.facts.manifest_digest)
            )
            if state["now_sim_t_s"] != 29400:
                raise RuntimeError("deterministic V3 warm-up did not reach 08:10")
            return
        if (
            course_session_v3.structural_recovery_status(self.session_root)
            == "COMMITTED_CURSOR_STALE"
        ):
            course_session_v3.recover_committed_cursor(self.session_root)

    def _start_device_and_reconcile(self) -> None:
        self.device = SimulatorBackedTaskDevice(
            self.session_root,
            self.config,
            ROBOT_ID,
            journal_path=(
                self.root / "device" / ROBOT_ID / "robot_task_journal.jsonl"
            ),
            initialize=self.initialize,
            provisioning_nonce=(
                (lambda: "continuous-collection-execution-v4")
                if self.initialize
                else None
            ),
        )
        self.device.start()

    def _runtime_status_unlocked(self) -> dict[str, Any]:
        return course_session_v3.read_runtime_status(self.session_root, ROBOT_ID)

    def _simulation_clock_unlocked(self) -> datetime:
        return parse_utc(self._runtime_status_unlocked()["simulation_time_utc"])

    def simulation_clock(self) -> datetime:
        with self._lock:
            return self._simulation_clock_unlocked()

    def _start_gateway_and_publisher(self) -> None:
        self.broker = InMemoryBroker()
        self.gateway = EdgeGateway(
            self.config,
            self.facts,
            self.edge_journal,
            self.broker.client(self.config.edge_client_id),
            clock=self.simulation_clock,
            transport_name="inmemory",
            emit=lambda _event: None,
        )
        self.gateway.start()
        robot = self.config.robot(ROBOT_ID)
        self.publisher = self.broker.client(robot.client_id)
        self.publisher.set_handlers(
            on_message=lambda _delivery: None,
            on_connect=lambda _present: None,
            on_disconnect=lambda _reason: None,
        )
        self.publisher.connect()

    def _construct_planning_with_horizon_gate(self) -> None:
        with course_session_v3.execution_admission(
            self.session_root
        ) as admission:
            closure_identity = deepcopy(admission.identity)
            self.session_identity = deepcopy(admission.identity)

        def confirmation_gate(history, confirmation, _now):
            plan = history.plan(
                confirmation["plan_id"], confirmation["plan_version"]
            )
            try:
                requirements = execution_api.derive_execution_requirements(
                    plan=plan,
                    input_records=history.records(INPUT),
                    session_identity=closure_identity,
                    evidence_at_utc=confirmation["schedule"]["due_at_utc"],
                )
                execution_api.require_session_horizon(
                    start_at_utc=confirmation["schedule"]["due_at_utc"],
                    max_execution_s=requirements["max_execution_s"],
                    session_identity=closure_identity,
                )
            except execution_api.CollectionExecutionError as exc:
                raise PlanningError("planning_conflict", exc.detail) from exc

        self.planning = PlanningOperations(
            self.edge_journal,
            self.config,
            self.facts,
            self.simulation_clock,
            site_timezone=self.site.timezone,
            confirmation_gate=confirmation_gate,
        )

    def _publish_status(self) -> None:
        device = self._require_device()
        if self.publisher is None or self.broker is None:
            raise RuntimeError("continuous publication transport is unavailable")
        status = device.status_message(self._runtime_status_unlocked())
        handle = self.publisher.publish(
            status_topic(self.config.site_id, ROBOT_ID),
            status.canonical_bytes(),
            0,
        )
        if not handle.wait(1):
            raise RuntimeError("simulator status publication was not accepted")
        self.broker.pump()
        if self.gateway is not None and self.gateway.failure is not None:
            raise RuntimeError(f"Edge gateway failed: {self.gateway.failure}")

    def _publish_pending_events(self) -> list[dict[str, Any]]:
        device = self._require_device()
        if self.publisher is None or self.broker is None:
            raise RuntimeError("continuous publication transport is unavailable")
        published = []
        for event in device.pending_publications():
            handle = self.publisher.publish(
                event_topic(self.config.site_id, ROBOT_ID),
                canonical_bytes(event),
                1,
            )
            if not handle.wait(1):
                raise RuntimeError("simulator event publication was not accepted")
            self.broker.pump()
            if self.gateway is not None and self.gateway.failure is not None:
                raise RuntimeError(f"Edge gateway failed: {self.gateway.failure}")
            device.confirm_published(event)
            published.append(deepcopy(event))
        return published

    @staticmethod
    def _task_records_by_id(edge_records) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for record in edge_records:
            if record.record_kind != TASK_CREATED:
                continue
            task_id = record.payload["task_id"]
            if task_id in result:
                raise RuntimeError("task has duplicate TASK_CREATED evidence")
            result[task_id] = record
        return result

    def _resume_bound_admissions_unlocked(self) -> list[dict[str, Any]]:
        """Finish only requests whose binding predates this process start."""

        device = self._require_device()
        edge_records = self.edge_journal.read()
        task_records = self._task_records_by_id(edge_records)
        admissions: list[tuple[TaskRequest, dict[str, Any]]] = []
        with course_session_v3.execution_admission(
            self.session_root
        ) as admission:
            state = admission.store.replay()
            for binding in sorted(
                state["bindings"].values(), key=lambda row: row["binding_id"]
            ):
                executions = [
                    row
                    for row in state["executions"].values()
                    if row["binding_id"] == binding["binding_id"]
                ]
                if len(executions) > 1:
                    raise RuntimeError("binding has multiple execution records")
                if executions and (
                    executions[0]["edge_evidence"]["accepted"]
                    or executions[0]["state"] in _TERMINAL_EXECUTIONS
                ):
                    continue
                record = task_records.get(binding["task_id"])
                if record is None:
                    raise RuntimeError("bound task has no TASK_CREATED evidence")
                window = state["windows"].get(binding["binding_id"])
                if window is None:
                    raise RuntimeError("bound task has no frozen request window")
                task = TaskRequest.from_dict(
                    record.to_dict()["payload"]["request"]
                )
                request = execution_api.make_request(
                    binding,
                    execution_api.request_id_for_binding(binding["binding_id"]),
                    window["due_at_utc"],
                    window["expires_at_utc"],
                )
                admissions.append((task, request))
        return [
            device.admit(task, request, crash_hook=self.crash_hook)
            for task, request in admissions
        ]

    def _materialize_new_tasks_unlocked(self) -> list[dict[str, Any]]:
        planning_owner = self._require_planning()
        device = self._require_device()
        planning = planning_owner.execution_binding_snapshot(
            self._simulation_clock_unlocked()
        )
        edge_records = self.edge_journal.read()
        task_records = sorted(
            (
                record
                for record in edge_records
                if record.record_kind == TASK_CREATED
            ),
            key=lambda record: (
                record.recorded_at_utc,
                record.payload["task_id"],
            ),
        )
        schedules_by_task = {
            row["task_id"]: row["schedule"]
            for row in planning["confirmations"]
            if row.get("task_id") is not None
        }
        admissions: list[tuple[TaskRequest, dict[str, Any]]] = []
        with course_session_v3.execution_admission(
            self.session_root
        ) as admission:
            replayed = admission.store.replay()
            bindings_by_task = {
                row["task_id"]: row
                for row in replayed["bindings"].values()
            }
            closed_or_accepted_bindings = {
                row["binding_id"]
                for row in replayed["executions"].values()
                if row["edge_evidence"]["accepted"]
                or row["state"] in _TERMINAL_EXECUTIONS
            }
            seen_task_ids: set[str] = set()
            for task_record in task_records:
                task = TaskRequest.from_dict(
                    task_record.to_dict()["payload"]["request"]
                )
                if task.task_id in seen_task_ids:
                    raise RuntimeError("task has duplicate TASK_CREATED evidence")
                seen_task_ids.add(task.task_id)
                if task.task_id in bindings_by_task:
                    binding = bindings_by_task[task.task_id]
                else:
                    binding = admission.store.bind_confirmed_task(
                        planning,
                        edge_records,
                        admission.identity,
                        admission.now_sim_t_s,
                        task_id=task.task_id,
                    )
                    bindings_by_task[task.task_id] = binding
                if binding["binding_id"] in closed_or_accepted_bindings:
                    continue
                schedule = schedules_by_task.get(task.task_id)
                if schedule is None:
                    raise RuntimeError(
                        "TASK_CREATED has no verified Planning confirmation"
                    )
                request = execution_api.make_request(
                    binding,
                    execution_api.request_id_for_binding(binding["binding_id"]),
                    schedule["due_at_utc"],
                    schedule["expires_at_utc"],
                )
                admissions.append((task, request))
        return [
            device.admit(task, request, crash_hook=self.crash_hook)
            for task, request in admissions
        ]

    def _read_collection_executions_unlocked(self) -> dict[str, Any]:
        return course_session_v3.read_execution_snapshot(
            self.session_root, server_time_utc=utc_text(self.wall_clock())
        )

    def _classify_unlocked(self) -> None:
        if self.failure is not None:
            self.driver_state = "FAILED"
            self._stop.set()
            return
        runtime = self._runtime_status_unlocked()
        snapshot = self._read_collection_executions_unlocked()
        try:
            state = classify_continuous_runtime(
                runtime, snapshot["executions"]
            )
        except RuntimeError as exc:
            self._fail_unlocked(exc)
            return
        self.driver_state = state
        if state in _DRIVER_DONE:
            self._stop.set()

    def _return_read_only_if_not_running(self) -> bool:
        if self.driver_state == "RUNNING":
            return False
        self.started = True
        return True

    def start(self) -> None:
        """Reconcile the complete durable prefix before enabling any reads."""

        with self._lock:
            if self.started or self._process_lock is not None:
                raise RuntimeError(
                    "continuous collection execution service already started"
                )
            if self.failure is not None:
                raise RuntimeError(
                    "failed continuous runtime requires a new process"
                )
            self._stop.clear()
            self.driver_state = "STARTING"
            try:
                self._prepare_root()
                self._initialize_or_repair_v3()
                self._start_device_and_reconcile()
                self._start_gateway_and_publisher()
                self._construct_planning_with_horizon_gate()
                self._publish_status()
                self._resume_bound_admissions_unlocked()
                self._require_device().consume_committed(
                    crash_hook=self.crash_hook
                )
                self._publish_pending_events()
                self._publish_status()
                self._classify_unlocked()
                if self._return_read_only_if_not_running():
                    return
                planning = self._require_planning()
                planning.recover()
                planning.schedules.tick(self._simulation_clock_unlocked())
                self._materialize_new_tasks_unlocked()
                self._require_device().consume_committed(
                    crash_hook=self.crash_hook
                )
                self._publish_pending_events()
                self._publish_status()
                self._classify_unlocked()
                self.started = True
            except Exception as exc:
                self._fail_unlocked(exc)
                self._close_components_unlocked()
                raise

    def start_driver(self) -> bool:
        """Start the sole background driver; PAUSED roots keep a live waiter."""

        with self._lock:
            self._require_started()
            if self._thread is not None:
                raise RuntimeError(
                    "continuous collection execution driver already started"
                )
            if self.driver_state in _DRIVER_DONE:
                return False
            self._thread = threading.Thread(
                target=self._run,
                name="continuous-collection-execution-driver",
                daemon=True,
            )
            self._thread.start()
            return True

    def tick(self) -> None:
        """Perform one serialized owner pass and exactly one live V3 step."""

        with self._lock:
            if not self.started or self.failure is not None:
                return
            if self.driver_state in _DRIVER_DONE:
                return
            try:
                self._classify_unlocked()
                if self.driver_state != "RUNNING":
                    return
                planning = self._require_planning()
                planning.recover()
                planning.schedules.tick(self._simulation_clock_unlocked())
                self._materialize_new_tasks_unlocked()
                self._require_device().consume_committed(
                    crash_hook=self.crash_hook
                )
                self._publish_pending_events()
                self._publish_status()
                self._advance_v3_unlocked()
                self._require_device().consume_committed(
                    crash_hook=self.crash_hook
                )
                self._publish_pending_events()
                self._publish_status()
                self._classify_unlocked()
            except Exception as exc:
                self._fail_unlocked(exc)

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.step_interval_s):
                self.tick()
        except Exception as exc:
            with self._lock:
                if self.started:
                    self._fail_unlocked(exc)

    def runtime_status(self) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            return self._runtime_status_unlocked()

    def collection_executions(self) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            return self._read_collection_executions_unlocked()

    def collection_execution_request(self, request_id: str) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            if self.session_root.is_symlink():
                raise ValueError("V3 session root must not be a symlink")
            with course_session_v3._lock(
                self.session_root / ".session.lock"
            ):
                session, _saved, _control = (
                    course_session_v3._validated_read_runtime(
                        self.session_root
                    )
                )
                return session.store.request_result(request_id)

    def planning_snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            return self._require_planning().snapshot(
                self._simulation_clock_unlocked()
            )

    def task_operations_snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            planning = self._require_planning()
            if self.gateway is None:
                raise RuntimeError("continuous Edge gateway is unavailable")
            simulation_now = self._simulation_clock_unlocked()
            edge = list_view(
                self.edge_journal,
                self.config,
                now=simulation_now,
            )
            gateway_failure = self.gateway.failure
            failure = self.failure
            if failure is None and gateway_failure is not None:
                failure = str(gateway_failure)
            result = planning.schedules.snapshot(simulation_now)
            result.update(
                {
                    "schema": "nxt-pilot-dispatch/v0",
                    "environment": "SIMULATION",
                    "disclaimer": DISCLAIMER,
                    "server_time_utc": utc_text(self.wall_clock()),
                    "scheduler": {
                        "state": "FAILED" if failure is not None else "RUNNING",
                        "detail": failure,
                    },
                    "service_capabilities": task_ops_service_capabilities(
                        "CONTINUOUS_V3_EXECUTION"
                    ),
                    "runtime": {
                        **self._runtime_status_unlocked(),
                        "driver_state": self.driver_state,
                        "fixed_confirmation": False,
                        "accepts_new_confirmations": (
                            self.failure is None
                            and self.driver_state not in _DRIVER_DONE
                        ),
                        "accepts_new_schedules": False,
                    },
                    "devices": edge["devices"],
                    "tasks": edge["tasks"],
                    "available_robots": [
                        robot_id
                        for robot_id in self.config.robot_ids
                        if self.config.robot(robot_id).task_types
                    ],
                    "available_zones": sorted(self.facts.zone_ids),
                    "transport": "in_memory",
                }
            )
            return result

    def route_planning(
        self, method: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            self._require_started()
            planning = self._require_planning()
            if method == "POST" and self.failure is not None:
                raise SiteAgentError("planning_unavailable", self.failure)
            try:
                return planning.route(method, path, body)
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

    def route_task_operations(
        self, method: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        del body
        with self._lock:
            self._require_started()
            if method == "GET" and path == "/api/v0/task-ops":
                return self.task_operations_snapshot()
            raise SiteAgentError(
                "not_found", "unknown continuous task operations route"
            )

    def api_callbacks(self) -> dict[str, Callable]:
        return {
            "task_operations": self.route_task_operations,
            "planning_operations": self.route_planning,
            "collection_executions": self.collection_executions,
            "collection_execution_request": self.collection_execution_request,
            "collection_execution_parser": (
                execution_api.parse_collection_execution_read_contract
            ),
        }

    def _close_components_unlocked(self) -> None:
        if self.publisher is not None:
            self.publisher.disconnect()
        if self.gateway is not None:
            self.gateway.client.disconnect()
        if self._process_lock is not None:
            self._process_lock.close()
        self.publisher = None
        self.gateway = None
        self.broker = None
        self.device = None
        self.planning = None
        self.session_identity = None
        self._process_lock = None
        self.started = False

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10.0)
        with self._lock:
            self._thread = None
            self._close_components_unlocked()
            if self.driver_state not in _DRIVER_DONE:
                self.driver_state = "STOPPED"


__all__ = [
    "ContinuousCollectionExecutionRuntime",
    "MARKER",
    "classify_continuous_runtime",
]
