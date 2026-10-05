#!/usr/bin/env python3
"""Bounded SIMULATION runner from Planning confirmation to V3 collection.

This is a composition root, not a new decision or execution owner.  Planning
v1 confirms the existing bound schedule, ``ScheduleService`` creates the sole
Edge task, the simulator-backed device records Edge lifecycle evidence, and
the unchanged ``JointDispatchPolicy`` plus ``SafetyShield`` drive the actual
``RangeSimulation``/``BallLedger`` transfers.

No physical adapter, MockRobotDevice, Planning outcome, washing result,
supply result, or inventory update exists on this path.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sys
from typing import Callable

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_task.cases import EDGE_RECORD_KINDS, TASK_CREATED, derive_edge_view  # noqa: E402
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
from nxt_pilot_ops.planning_workflow import PLANNING_RECORD_KINDS  # noqa: E402
from scripts import course_collection_execution as execution_api  # noqa: E402
from scripts import course_session_v3  # noqa: E402
from scripts.course_session_task_device import SimulatorBackedTaskDevice  # noqa: E402
from scripts.edge_task_gateway_v0 import EdgeGateway  # noqa: E402
from scripts.edge_task_transport import InMemoryBroker  # noqa: E402
from scripts.joint_learning import atomic_json, read_json  # noqa: E402
from scripts.pilot_course_a_task_fixture import admission_facts, commissioned_site  # noqa: E402
from scripts.planning_operations import PlanningOperations  # noqa: E402


DISCLAIMER = (
    "SIMULATION ONLY — Planning-confirmed V3 ledger execution; no physical robot"
)
MARKER = {
    "schema": "nxt-course-collection-execution-demo/v1",
    "environment": "SIMULATION",
}
CONFIG_PATH = SIM_ROOT / "configs/edge_task/pilot-course-a.sim.example.json"
ROBOT_ID = "picker-01"


def _wall_utc() -> datetime:
    return datetime.now(timezone.utc)


def _evidence(value, unit, source_ref):
    return {
        "value": value,
        "source_kind": "MANUAL_ESTIMATE",
        "source_ref": source_ref,
        "observed_at_utc": "2026-09-16T08:00:00Z",
        "valid_until_utc": "2026-09-16T09:00:00Z",
        "unit": unit,
    }


def planning_requests() -> tuple[dict, dict, dict]:
    """Frozen human input, plan selection and confirmation for the demo."""

    source = {
        "schema": "nxt-planning-input/v1",
        "request_id": "collection-demo-input-001",
        "expected_revision": 0,
        "site_id": "pilot-course-a",
        "deployment_id": "pilot-a-edge-task-sim-v0",
        "site_timezone": "Asia/Shanghai",
        "operator": "course-manager",
        "reason": "Deterministic V3 collection execution rehearsal.",
        "scope": "SHIFT",
        "effective_at_utc": "2026-09-16T08:00:00Z",
        "valid_until_utc": "2026-09-16T09:00:00Z",
        "operating_window": {
            "start_at_utc": "2026-09-16T08:00:00Z",
            "end_at_utc": "2026-09-16T09:00:00Z",
        },
        "inventory_clean_balls": {
            **_evidence(600, "balls", "opening-clean-bin-count-v3"),
            "source_kind": "MEASURED",
        },
        "demand": _evidence(
            {
                "bucket_minutes": 10,
                "low": [5, 5, 5, 5, 5, 5],
                "typical": [8, 8, 8, 8, 8, 8],
                "high": [10, 10, 10, 10, 10, 10],
            },
            "balls/minute",
            "manager-demand-v3",
        ),
        "safety_stock_balls": _evidence(
            280, "balls", "manager-safety-target-v3"
        ),
        "buffer_minutes": _evidence(2, "minutes", "manager-buffer-v3"),
        "operations_allowed": _evidence(
            True, "boolean", "operator-work-window-v3"
        ),
        "washer_available": _evidence(
            True, "boolean", "operator-washer-check-v3"
        ),
        "zones": [
            {
                "zone_id": "Z1",
                "robot_id": ROBOT_ID,
                "collection_allowed": _evidence(
                    True, "boolean", "operator-zone-Z1-v3"
                ),
                "clean_yield_balls": _evidence(
                    {"low": 600, "high": 600},
                    "balls",
                    "operator-yield-Z1-v3",
                ),
                "cycle_minutes": _evidence(
                    {
                        "travel": 2,
                        "collect": 5,
                        "return": 2,
                        "unload": 2,
                        "wash": 3,
                        "supply": 1,
                    },
                    "minutes",
                    "operator-cycle-Z1-v3",
                ),
            }
        ],
    }
    plan = {
        "schema": "nxt-planning-request/v1",
        "request_id": "collection-demo-plan-001",
        "plan_id": None,
        "expected_plan_version": 0,
        "input_revision": 1,
        "operator": "course-manager",
        "reason": "Confirm the deterministic 08:10 simulator slot.",
        "scope": "ONE_TASK",
        "valid_until_utc": "2026-09-16T09:00:00Z",
        "selection": {
            "zone_id": "Z1",
            "robot_id": ROBOT_ID,
            "start_at_utc": "2026-09-16T08:10:00Z",
        },
    }
    confirmation = {
        "schema": "nxt-planning-confirmation/v1",
        "request_id": "collection-demo-confirmation-001",
        "plan_id": None,
        "plan_version": 1,
        "operator": "course-manager",
    }
    return source, plan, confirmation


def v3_config(config: EdgeTaskConfig, manifest_digest: str) -> dict:
    value = deepcopy(course_session_v3.DEFAULT_CONFIG)
    value.update(
        {
            "series_id": "collection-execution-series-v3",
            "session_id": "collection-execution-session-v3",
            "round_id": "collection-execution-round-v3",
            "round_index": 0,
            "session_epoch_utc": "2026-09-16T00:00:00Z",
            "seed": 53,
            "days": 1,
            "staff_count": 1,
            "initial_stock": 0,
            "demand_scale": 0,
            "assumptions": {},
            "control_interval_s": 600,
            "advance_steps": 1,
            "site_id": config.site_id,
            "deployment_id": config.deployment_id,
            "commissioned_site_digest": manifest_digest,
            "runtime_bindings": [
                {
                    "robot_id": ROBOT_ID,
                    "zone_id": "Z1",
                    "runtime_robot_id": "R1",
                    "runtime_zone_id": "NEAR_LEFT",
                    "handoff_station_id": "H1",
                }
            ],
        }
    )
    return value


class CourseCollectionExecutionDemo:
    """One bounded local composition over durable owner journals."""

    def __init__(
        self,
        root: str | Path,
        *,
        initialize: bool = False,
        wall_clock: Callable[[], datetime] = _wall_utc,
    ) -> None:
        self.root = Path(root)
        self.session_root = self.root / "session-v3"
        self.initialize = initialize
        self.wall_clock = wall_clock
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
        self.started = False
        self._process_lock = None

    def _require_started(self) -> None:
        if not self.started:
            raise RuntimeError("collection execution demo is not started")

    def runtime_status(self) -> dict:
        return course_session_v3.read_runtime_status(self.session_root, ROBOT_ID)

    def simulation_clock(self) -> datetime:
        return parse_utc(self.runtime_status()["simulation_time_utc"])

    def _prepare_root(self) -> None:
        if self.root.is_symlink():
            raise ValueError("demo root must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        lock = (self.root / ".collection-execution.lock").open("a+b")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise RuntimeError("another collection execution runner owns this root") from None
        self._process_lock = lock
        marker = self.root / "demo.json"
        if self.initialize:
            existing = [
                path for path in self.root.iterdir()
                if path.name != ".collection-execution.lock"
            ]
            if existing:
                raise RuntimeError("--initialize requires a new empty output directory")
            atomic_json(marker, MARKER)
        else:
            if not marker.exists() or read_json(marker) != MARKER:
                raise RuntimeError("new evidence requires --initialize; demo identity is absent")

    def start(self) -> dict:
        if self.started or self._process_lock is not None:
            raise RuntimeError("this collection execution runner already started")
        try:
            self._prepare_root()
            if self.initialize:
                state = course_session_v3.run(
                    self.session_root, v3_config(self.config, self.facts.manifest_digest)
                )
                if state["now_sim_t_s"] != 29400:
                    raise RuntimeError("deterministic V3 warm-up did not reach 08:10")
            elif (
                course_session_v3.structural_recovery_status(self.session_root)
                == "COMMITTED_CURSOR_STALE"
            ):
                # A committed simulator prefix is causal truth. Repair only
                # its disposable cursor before the device attests its restart;
                # PREPARED_NO_COMMIT deliberately takes the device-first path.
                course_session_v3.recover_committed_cursor(self.session_root)

            # On resume the device must attest/reconcile its own durable restart
            # before any read or runner can replay a prepared authorization.
            self.device = SimulatorBackedTaskDevice(
                self.session_root,
                self.config,
                ROBOT_ID,
                journal_path=self.root / "device" / ROBOT_ID / "robot_task_journal.jsonl",
                initialize=self.initialize,
                provisioning_nonce=(
                    (lambda: "collection-execution-v3") if self.initialize else None
                ),
            )
            self.device.start()

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
            self.planning = PlanningOperations(
                self.edge_journal,
                self.config,
                self.facts,
                self.simulation_clock,
                site_timezone=self.site.timezone,
            )
            self.started = True
            self._publish_status()
            return self.runtime_status()
        except Exception:
            self.close()
            raise

    def _publish_status(self) -> None:
        self._require_started()
        assert self.device is not None and self.publisher is not None
        assert self.broker is not None
        status = self.device.status_message(self.runtime_status())
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

    def _publish_pending_events(self) -> list[dict]:
        self._require_started()
        assert self.device is not None and self.publisher is not None
        assert self.broker is not None
        published = []
        for event in self.device.pending_publications():
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
            self.device.confirm_published(event)
            published.append(deepcopy(event))
        return published

    def _ensure_planning_confirmation(self) -> dict:
        self._require_started()
        assert self.planning is not None
        source, request, confirmation = planning_requests()
        snapshot = self.planning.snapshot(self.simulation_clock())
        if snapshot["latest_input"] is None:
            self.planning.route("POST", "/api/v1/planning/inputs", source)
            snapshot = self.planning.snapshot(self.simulation_clock())
        if not snapshot["plans"]:
            plan = self.planning.route(
                "POST", "/api/v1/planning/plans", request
            )["record"]
        else:
            plan = snapshot["plans"][0]
        if not snapshot["confirmations"]:
            confirmation["plan_id"] = plan["plan_id"]
            self.planning.route(
                "POST", "/api/v1/planning/confirmations", confirmation
            )
        self.planning.recover()
        self.planning.schedules.tick(self.simulation_clock())
        snapshot = self.planning.snapshot(self.simulation_clock())
        if len(snapshot["confirmations"]) != 1:
            raise RuntimeError("demo requires exactly one confirmed plan")
        confirmed = snapshot["confirmations"][0]
        if confirmed["task_id"] is None or confirmed["schedule_status"] != "DISPATCHED":
            raise RuntimeError("confirmed schedule did not create its sole Edge task")
        return snapshot

    def ensure_execution(self) -> dict:
        """Idempotently bind, submit, accept and publish one confirmed task."""

        self._require_started()
        assert self.device is not None
        planning = self._ensure_planning_confirmation()
        records = self.edge_journal.read()
        task_records = [
            row for row in records
            if row.record_kind == TASK_CREATED
            and row.payload.get("task_id") == planning["confirmations"][0]["task_id"]
        ]
        if len(task_records) != 1:
            raise RuntimeError("confirmed plan must have one verified TASK_CREATED")
        task = TaskRequest.from_dict(
            task_records[0].to_dict()["payload"]["request"]
        )
        with course_session_v3.execution_admission(self.session_root) as admission:
            persisted = admission.store.replay()
            matches = [
                row for row in persisted["bindings"].values()
                if row["task_id"] == task.task_id
            ]
            if not matches:
                matches = [
                    row for row in admission.store.bind_confirmed_tasks(
                        planning, records, admission.identity, admission.now_sim_t_s
                    )
                    if row["task_id"] == task.task_id
                ]
            if len(matches) != 1:
                raise RuntimeError("confirmed task must have one V3 binding")
            schedule = planning["confirmations"][0]["schedule"]
            request = execution_api.make_request(
                matches[0],
                "collection-demo-execution-request-001",
                schedule["due_at_utc"],
                schedule["expires_at_utc"],
            )
        # Device admission owns its own session-lock transaction.  Never call
        # the V3 runner while either transaction is held.
        receipt = self.device.admit(task, request)
        self._publish_pending_events()
        self._publish_status()
        return receipt

    def advance_once(self) -> dict:
        """Advance at most one V3 tick, then drain its device outbox."""

        self._require_started()
        assert self.device is not None
        state = course_session_v3.run(self.session_root)
        delivered = self.device.consume_committed()
        published = self._publish_pending_events()
        self._publish_status()
        return {
            "session": state,
            "delivered": delivered,
            "published": published,
        }

    def planning_snapshot(self) -> dict:
        self._require_started()
        assert self.planning is not None
        return self.planning.snapshot(self.simulation_clock())

    def collection_executions(self) -> dict:
        self._require_started()
        return course_session_v3.read_execution_snapshot(
            self.session_root, server_time_utc=utc_text(self.wall_clock())
        )

    def create_schedule(self, payload: dict) -> dict:
        """Legacy form seam: date validation uses simulation, never wall UTC."""

        self._require_started()
        assert self.planning is not None
        return self.planning.schedules.create(payload, self.simulation_clock())

    def task_operations_snapshot(self) -> dict:
        """Wall read metadata and simulation PAUSED state remain independent."""

        self._require_started()
        assert self.planning is not None and self.gateway is not None
        self.gateway.core.refresh(self.edge_journal.read())
        edge = self.gateway.snapshot()
        failure = self.gateway.failure
        data = self.planning.schedules.snapshot(self.simulation_clock())
        data.update(
            {
                "server_time_utc": utc_text(self.wall_clock()),
                "scheduler": {
                    "state": "FAILED" if failure is not None else "RUNNING",
                    "detail": None if failure is None else str(failure),
                },
                "runtime": self.runtime_status(),
                "devices": edge["devices"],
                "tasks": edge["tasks"],
            }
        )
        return data

    def evidence(self) -> dict:
        """Detached diagnostic proof for local reproduction and tests."""

        self._require_started()
        snapshot = self.collection_executions()
        execution_id = snapshot["executions"][0]["execution_id"]
        with course_session_v3.execution_admission(self.session_root) as admission:
            plans = admission.store.replay_plan()
        assignments = [
            committed["result"]["runtime_snapshots"][execution_id]
            for committed in (row["committed"] for row in plans)
            if execution_id in committed["result"]["runtime_snapshots"]
        ]
        if not assignments:
            raise RuntimeError("execution has no committed assignment evidence")
        assignment = assignments[-1]
        records = self.edge_journal.read()
        edge = derive_edge_view(self.config, records)
        task_id = snapshot["executions"][0]["task_id"]
        return {
            "assignment": deepcopy(assignment),
            "replay_plan": deepcopy(plans),
            "edge_records": [row.to_dict() for row in records],
            "planning_record_kinds": [row.record_kind for row in records],
            "edge_task_state": edge.tasks[task_id].state.value,
        }

    def run(self, *, advance: int) -> dict:
        if type(advance) is not int or advance < 0:
            raise ValueError("advance must be a nonnegative integer")
        if not self.started:
            self.start()
        receipt = self.ensure_execution()
        ticks = [self.advance_once() for _ in range(advance)]
        return {
            "disclaimer": DISCLAIMER,
            "request_receipt": receipt,
            "ticks": ticks,
            "planning": self.planning_snapshot(),
            "task_operations": self.task_operations_snapshot(),
            "collection_executions": self.collection_executions(),
        }

    def close(self) -> None:
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
        self._process_lock = None
        self.started = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--advance", type=int, default=0)
    parser.add_argument(
        "--no-serve",
        action="store_true",
        help="run a bounded chunk without the read-only API added in Task 6",
    )
    args = parser.parse_args(argv)
    if not args.no_serve:
        parser.error("Task 5 is bounded-only; pass --no-serve")
    runtime = CourseCollectionExecutionDemo(args.out, initialize=args.initialize)
    try:
        result = runtime.run(advance=args.advance)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
