"""Real local Mosquitto, independent processes, independent storage (PR A must-run).

Skips with an explicit reason when ``mosquitto`` is not on PATH.  A skip is
not acceptance evidence: the PR A hand-off must attach a local run of this
module on its exact head.  No Docker, no shared broker, task-specific port.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SIM_ROOT = Path(__file__).resolve().parents[2]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_task.cases import EDGE_RECORD_KINDS  # noqa: E402
from nxt_edge_task.executor import ROBOT_RECORD_KINDS  # noqa: E402
from nxt_edge_task.journal import JsonlJournal  # noqa: E402

MOSQUITTO = shutil.which("mosquitto")
pytestmark = pytest.mark.skipif(MOSQUITTO is None, reason="mosquitto not on PATH: PR A local broker acceptance not run here")

BASE_CONFIG = SIM_ROOT / "configs" / "edge_task" / "pilot-course-a.sim.example.json"
BASE_CONF = SIM_ROOT / "deploy" / "edge-task-v0" / "mosquitto.loopback.conf"
PY = [sys.executable, "-B"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_port(port: int, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise AssertionError("broker port never opened")


class Stack:
    """Broker + Edge + Picker + Carrier as separate processes with separate evidence dirs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.port = _free_port()
        self.conf = root / "mosquitto.conf"
        self.conf.write_text(BASE_CONF.read_text(encoding="utf-8").replace("listener 18830 127.0.0.1", f"listener {self.port} 127.0.0.1"), encoding="utf-8")
        config = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
        config["broker"]["port"] = self.port
        config["edge"].update({"stale_after_s": 4, "offline_after_s": 8, "restart_grace_s": 8, "min_republish_interval_s": 3, "default_progress_window_s": 6, "tick_interval_s": 0.25})
        config["edge"]["evidence_dir"] = "edge"
        for robot in config["robots"]:
            robot["heartbeat_interval_s"] = 1
            robot["evidence_dir"] = f"robots/{robot['robot_id']}"
        self.config_path = root / "config.json"
        self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        self.config = config
        self.procs: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, Path] = {}
        self.broker: subprocess.Popen | None = None

    # -- processes ----------------------------------------------------------

    def start_broker(self) -> None:
        log = (self.root / "mosquitto.log").open("ab")
        self.broker = subprocess.Popen([MOSQUITTO, "-c", str(self.conf)], stdout=log, stderr=subprocess.STDOUT)
        _wait_port(self.port)

    def stop_broker(self) -> None:
        if self.broker is not None:
            self.broker.terminate()
            self.broker.wait(timeout=10)
            self.broker = None

    def _spawn(self, name: str, args: list[str]) -> None:
        log_path = self.root / f"{name}.log"
        self.logs[name] = log_path
        log = log_path.open("ab")
        self.procs[name] = subprocess.Popen(PY + args, cwd=SIM_ROOT, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": str(SIM_ROOT)})

    def start_edge(self) -> None:
        self._spawn("edge", ["scripts/edge_task_gateway_v0.py", "--config", str(self.config_path), "--evidence-root", str(self.root)])

    def start_robot(self, robot_id: str, behavior: str = "accept_and_succeed") -> None:
        self._spawn(robot_id, ["scripts/mock_robot_task_device.py", "--config", str(self.config_path), "--robot-id", robot_id, "--behavior", behavior, "--evidence-root", str(self.root)])

    def stop(self, name: str, *, timeout: float = 10.0) -> int:
        proc = self.procs.pop(name, None)
        if proc is None:
            return -1
        if proc.poll() is None:
            proc.terminate()
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait(timeout=5)

    def wait_exit(self, name: str, timeout: float = 30.0) -> int:
        proc = self.procs[name]
        return proc.wait(timeout=timeout)

    def stop_all(self) -> None:
        for name in list(self.procs):
            self.stop(name)
        self.stop_broker()

    # -- CLI ------------------------------------------------------------------

    def create(self, robot_id: str = "picker-01", *, issued: str, expires: str, operator: str = "integration") -> dict:
        result = subprocess.run(
            PY + ["scripts/edge_task_cli.py", "--config", str(self.config_path), "--evidence-root", str(self.root), "create-task", "--robot", robot_id, "--zone", "Z1", "--issued-at-utc", issued, "--expires-at-utc", expires, "--operator", operator],
            cwd=SIM_ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(SIM_ROOT)},
        )
        assert result.returncode in (0, 1), result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])

    # -- evidence (read by the test process only) --------------------------------

    def edge_records(self) -> list:
        path = self.root / "edge" / self.config["site_id"] / self.config["deployment_id"] / "edge_task_journal.jsonl"
        return list(JsonlJournal(path, allowed_kinds=EDGE_RECORD_KINDS).read())

    def robot_records(self, robot_id: str) -> list:
        path = self.root / "robots" / robot_id / "robot_task_journal.jsonl"
        if not path.exists():
            return []
        return list(JsonlJournal(path, allowed_kinds=ROBOT_RECORD_KINDS).read())

    def task_state(self, task_id: str) -> tuple[str | None, str | None]:
        state, result = None, None
        for record in self.edge_records():
            if record.record_kind == "task_event_received" and record.payload["task_id"] == task_id and record.payload["disposition"] == "applied":
                state = record.payload["state_after"]
            if record.record_kind == "conflicting_terminal" and record.payload["task_id"] == task_id:
                result = "CONFLICT"
        return state, result

    def executions(self, robot_id: str, task_id: str | None = None) -> int:
        return sum(1 for r in self.robot_records(robot_id) if r.record_kind == "execution_started" and (task_id is None or r.payload["task_id"] == task_id))

    def wait_state(self, task_id: str, expected: set[str], timeout_s: float = 30.0) -> str:
        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            last, _ = self.task_state(task_id)
            if last in expected:
                return last
            time.sleep(0.25)
        raise AssertionError(f"task {task_id} reached {last!r}, expected one of {expected}; logs: {self._tail()}")

    def wait_records(self, predicate, timeout_s: float = 30.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate(self.edge_records()):
                return
            time.sleep(0.25)
        raise AssertionError(f"records condition not met; logs: {self._tail()}")

    def _tail(self) -> dict[str, str]:
        return {name: path.read_text(encoding="utf-8", errors="replace")[-1500:] for name, path in self.logs.items()}


def _times(offset_minutes: int = 0, valid_minutes: int = 10) -> tuple[str, str]:
    issued = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    fmt = lambda d: d.isoformat(timespec="microseconds").replace("+00:00", "Z")  # noqa: E731
    return fmt(issued), fmt(issued + timedelta(minutes=valid_minutes))


@pytest.fixture
def stack(tmp_path: Path):
    stack = Stack(tmp_path)
    stack.start_broker()
    try:
        yield stack
    finally:
        stack.stop_all()


def test_normal_flow_over_real_broker(stack: Stack) -> None:
    stack.start_edge()
    stack.start_robot("picker-01")
    stack.start_robot("carrier-01")
    time.sleep(2.0)
    issued, expires = _times()
    created = stack.create(issued=issued, expires=expires)
    assert created["status"] == "created"
    task_id = created["task_id"]
    assert stack.wait_state(task_id, {"SUCCEEDED"}) == "SUCCEEDED"
    assert stack.executions("picker-01", task_id) == 1
    carrier = [r.record_kind for r in stack.robot_records("carrier-01")]
    assert carrier.count("execution_started") == 0 and carrier.count("task_decision") == 0
    received = [r for r in stack.edge_records() if r.record_kind == "task_event_received" and r.payload["task_id"] == task_id]
    assert received and all(r.payload["transport"] == "mqtt" for r in received)
    assert [r.payload["event"]["kind"] for r in received] == ["ACCEPTED", "PROGRESS", "PROGRESS", "SUCCEEDED"]
    # Duplicate SIMULATION entry: idempotent, no second task, no second execution.
    again = stack.create(issued=issued, expires=expires)
    assert again["status"] == "idempotent" and again["task_id"] == task_id
    time.sleep(1.5)
    assert stack.executions("picker-01", task_id) == 1
    assert sum(1 for r in stack.edge_records() if r.record_kind == "task_created") == 1
    # Carrier rejects an unsupported task and never executes.
    issued2, expires2 = _times(1)
    carrier_task = stack.create("carrier-01", issued=issued2, expires=expires2)["task_id"]
    assert stack.wait_state(carrier_task, {"REJECTED"}) == "REJECTED"
    assert stack.executions("carrier-01") == 0
    # Restart Edge and Picker separately: nothing regresses, nothing re-executes.
    stack.stop("edge")
    stack.start_edge()
    stack.stop("picker-01")
    stack.start_robot("picker-01")
    time.sleep(3.0)
    assert stack.task_state(task_id)[0] == "SUCCEEDED"
    assert stack.executions("picker-01", task_id) == 1
    started = [r for r in stack.robot_records("picker-01") if r.record_kind == "robot_started"]
    assert [r.payload["boot_sequence"] for r in started] == [1, 2]


def test_silent_after_accept_leaves_persistent_unverified_progress_evidence(stack: Stack) -> None:
    stack.start_edge()
    stack.start_robot("picker-01", "silent_after_accept")
    stack.start_robot("carrier-01")
    time.sleep(2.0)
    issued, expires = _times()
    task_id = stack.create(issued=issued, expires=expires)["task_id"]
    stack.wait_state(task_id, {"ACCEPTED"})
    stack.wait_records(lambda records: any(r.record_kind == "task_reconciliation_flagged" and r.payload["task_id"] == task_id and r.payload["reason"] == "progress_window_elapsed" for r in records), timeout_s=30)
    time.sleep(2.0)
    assert stack.task_state(task_id)[0] == "ACCEPTED"
    assert stack.executions("picker-01", task_id) == 1
    confirmations = [r for r in stack.edge_records() if r.record_kind == "task_publish_confirmed" and r.payload["task_id"] == task_id]
    assert 1 <= len(confirmations) <= stack.config["edge"]["max_republish_attempts"]


def test_crash_after_execution_started_reports_inconclusive_and_never_reruns(stack: Stack) -> None:
    stack.start_edge()
    stack.start_robot("picker-01", "crash_after_execution_started")
    stack.start_robot("carrier-01")
    time.sleep(2.0)
    issued, expires = _times()
    task_id = stack.create(issued=issued, expires=expires)["task_id"]
    assert stack.wait_exit("picker-01", timeout=30) == 3  # simulated crash exit code
    assert stack.executions("picker-01", task_id) == 1
    stack.procs.pop("picker-01", None)
    stack.start_robot("picker-01", "accept_and_succeed")
    assert stack.wait_state(task_id, {"INCONCLUSIVE"}) == "INCONCLUSIVE"
    time.sleep(2.0)
    assert stack.executions("picker-01", task_id) == 1
    robot_kinds = [r.record_kind for r in stack.robot_records("picker-01")]
    assert robot_kinds.count("execution_completed") == 0
    # A second SIMULATION task is refused by the awaiting-human robot (task-level REJECTED).
    issued2, expires2 = _times(1)
    second = stack.create(issued=issued2, expires=expires2)["task_id"]
    assert stack.wait_state(second, {"REJECTED"}) == "REJECTED"
    assert stack.executions("picker-01") == 1


def test_broker_restart_mid_task_converges_without_duplicate_execution(stack: Stack) -> None:
    stack.start_edge()
    stack.start_robot("picker-01", "silent_after_accept")
    stack.start_robot("carrier-01")
    time.sleep(2.0)
    issued, expires = _times()
    task_id = stack.create(issued=issued, expires=expires)["task_id"]
    stack.wait_state(task_id, {"ACCEPTED"})
    stack.stop_broker()
    time.sleep(2.0)
    stack.start_broker()
    stack.wait_records(lambda records: any(r.record_kind == "transport_session" and r.payload["event"] == "session_lost" for r in records), timeout_s=30)
    time.sleep(4.0)
    assert stack.task_state(task_id)[0] == "ACCEPTED"
    assert stack.executions("picker-01", task_id) == 1
    decisions = [r for r in stack.robot_records("picker-01") if r.record_kind == "task_decision"]
    assert len(decisions) == 1


def test_late_terminal_history_is_applied_and_conflict_gate_holds(stack: Stack) -> None:
    """A1/B3′ over the real broker: the Edge is down while the robot finishes; history arrives late."""

    stack.start_edge()
    stack.start_robot("picker-01")
    stack.start_robot("carrier-01")
    time.sleep(2.0)
    issued, expires = _times()
    task_id = stack.create(issued=issued, expires=expires)["task_id"]
    stack.wait_state(task_id, {"ACCEPTED", "RUNNING"})
    stack.stop("edge")
    time.sleep(4.0)  # the robot finishes while the Edge is down; QoS 1 events queue in the persistent session
    stack.start_edge()
    assert stack.wait_state(task_id, {"SUCCEEDED"}) == "SUCCEEDED"
    assert stack.executions("picker-01", task_id) == 1
    confirmations = [r for r in stack.edge_records() if r.record_kind == "task_publish_confirmed" and r.payload["task_id"] == task_id]
    assert len(confirmations) >= 1
    # Inject a conflicting terminal from a later boot through the real broker and check the gate.
    import paho.mqtt.client as mqtt  # noqa: PLC0415 - test-only injection

    from nxt_edge_task.contracts import ENVIRONMENT_KIND_SIMULATION, EVENT_SCHEMA, event_topic, utc_text

    event = {
        "schema": EVENT_SCHEMA, "site_id": stack.config["site_id"], "deployment_id": stack.config["deployment_id"],
        "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": stack.config["simulation_env_id"]},
        "task_id": task_id, "robot_id": "picker-01", "boot_id": "boot-picker-01-9", "boot_sequence": 9, "event_sequence": 1,
        "kind": "INCONCLUSIVE", "reason_code": "interrupted_execution_unknown_outcome", "detail": "injected by integration test",
        "reported_at_utc": utc_text(datetime.now(timezone.utc)), "progress": None,
    }
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id="integration-injector", protocol=mqtt.MQTTv311, clean_session=True)
    client.connect("127.0.0.1", stack.port, keepalive=10)
    info = client.publish(event_topic(stack.config["site_id"], "picker-01"), json.dumps(event).encode(), qos=1, retain=False)
    for _ in range(40):
        client.loop(0.1)
        if info.is_published():
            break
    client.disconnect()
    stack.wait_records(lambda records: any(r.record_kind == "conflicting_terminal" and r.payload["task_id"] == task_id for r in records), timeout_s=20)
    issued2, expires2 = _times(2)
    blocked = stack.create(issued=issued2, expires=expires2)
    assert blocked["status"] == "rejected" and blocked["code"] == "authorization_blocked"
    assert stack.executions("picker-01", task_id) == 1
