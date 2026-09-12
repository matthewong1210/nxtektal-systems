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
from nxt_edge_task.contracts import ENVIRONMENT_KIND_SIMULATION, EVENT_SCHEMA, TaskRequest, event_topic, request_topic, utc_text  # noqa: E402
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
        # Harness bookkeeping only: the first start of each identity is the
        # explicit provisioning act; every later start is a plain restart.
        self.provisioned: set[str] = set()

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

    def start_robot(self, robot_id: str, behavior: str = "accept_and_succeed", *, initialize: bool | None = None, step_interval_s: float | None = None) -> None:
        args = ["scripts/mock_robot_task_device.py", "--config", str(self.config_path), "--robot-id", robot_id, "--behavior", behavior, "--evidence-root", str(self.root)]
        if initialize is None:
            initialize = robot_id not in self.provisioned
        if initialize:
            args.append("--initialize")
            self.provisioned.add(robot_id)
        if step_interval_s is not None:
            args += ["--step-interval-s", str(step_interval_s)]
        self._spawn(robot_id, args)

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

    def wait_robot_records(self, robot_id: str, predicate, timeout_s: float = 30.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate(self.robot_records(robot_id)):
                return
            time.sleep(0.2)
        raise AssertionError(f"robot records condition not met; logs: {self._tail()}")

    def received(self, task_id: str) -> list:
        return [r for r in self.edge_records() if r.record_kind == "task_event_received" and r.payload["task_id"] == task_id]

    def publish_attempts(self, task_id: str) -> int:
        return sum(1 for r in self.edge_records() if r.record_kind == "task_publish_attempted" and r.payload["task_id"] == task_id)

    def request_bytes(self, task_id: str) -> bytes:
        for record in self.edge_records():
            if record.record_kind == "task_created" and record.payload["task_id"] == task_id:
                return TaskRequest.from_dict(json.loads(json.dumps(record.payload["request"], default=dict))).canonical_bytes()
        raise KeyError(task_id)

    # -- test-side transport helpers (fault injection through the real broker) ----

    def _paho(self, client_id: str, *, clean_session: bool):
        import paho.mqtt.client as mqtt  # noqa: PLC0415 - test-only injection

        connected = {"ok": False}
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv311, clean_session=clean_session)
        client.on_connect = lambda c, u, f, rc, p=None: connected.__setitem__("ok", True)
        client.connect("127.0.0.1", self.port, keepalive=10)
        for _ in range(50):
            client.loop(0.1)
            if connected["ok"]:
                break
        assert connected["ok"], "test client could not connect to the local broker"
        return client

    def inject(self, topic: str, payload: bytes, *, qos: int = 1) -> None:
        client = self._paho("integration-injector", clean_session=True)
        info = client.publish(topic, payload, qos=qos, retain=False)
        for _ in range(40):
            client.loop(0.1)
            if info.is_published():
                break
        assert info.is_published()
        client.disconnect()
        client.loop(0.1)

    def known_incarnation(self, robot_id: str) -> str:
        """The incarnation prefix the Edge journal last recorded for ``robot_id``."""

        from nxt_edge_task.contracts import incarnation_of

        boot_id, boot_sequence = None, None
        for record in self.edge_records():
            if record.record_kind in {"device_status_changed", "device_liveness_changed"} and record.payload["robot_id"] == robot_id and record.payload.get("status"):
                boot_id, boot_sequence = record.payload["status"]["boot_id"], record.payload["status"]["boot_sequence"]
        assert boot_id is not None, "the Edge has not seen this robot yet"
        return incarnation_of(boot_id, boot_sequence)

    def inject_event(self, task_id: str, kind: str, boot: int, seq: int, *, reason: str | None = None, robot_id: str = "picker-01") -> None:
        event = {
            "schema": EVENT_SCHEMA, "site_id": self.config["site_id"], "deployment_id": self.config["deployment_id"],
            "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": self.config["simulation_env_id"]},
            "task_id": task_id, "robot_id": robot_id, "boot_id": f"{self.known_incarnation(robot_id)}-{boot}", "boot_sequence": boot, "event_sequence": seq,
            "kind": kind, "reason_code": reason, "detail": "injected by integration test",
            "reported_at_utc": utc_text(datetime.now(timezone.utc)), "progress": None,
        }
        self.inject(event_topic(self.config["site_id"], robot_id), json.dumps(event).encode())

    def reset_edge_session(self) -> None:
        """Broker-side fault: the Edge's queued deliveries are lost, its persistent session is re-created empty.

        Emulates a broker that lost the queue (e.g. restart without persistence)
        while the Edge was down, without losing the robots' sessions.
        """

        client_id = self.config["edge"]["client_id"]
        wipe = self._paho(client_id, clean_session=True)
        wipe.disconnect()
        wipe.loop(0.1)
        keep = self._paho(client_id, clean_session=False)
        for robot in self.config["robots"]:
            keep.subscribe(event_topic(self.config["site_id"], robot["robot_id"]), qos=1)
        for _ in range(10):
            keep.loop(0.1)
        keep.disconnect()
        keep.loop(0.1)

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


def test_late_history_after_edge_downtime_is_applied_over_real_broker(stack: Stack) -> None:
    """C5 shape: the Edge is down while the robot finishes; the queued history arrives late, in order, with no resend."""

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
    assert stack.publish_attempts(task_id) == 1


def test_terminal_before_acceptance_over_real_broker_has_zero_resend_and_natural_late_history(stack: Stack) -> None:
    """A1 over the real broker (v3.1 §3.A): SUCCEEDED is the first thing the Edge sees.

    Fault: the Edge's queued ACCEPTED/PROGRESS deliveries are lost at the broker
    while the Edge is down.  Recovery: the terminal is applied with the gap
    recorded; the Edge never republishes to recover history; a duplicate of
    the original request still in flight makes the robot replay its history,
    which fills the gap as late evidence without moving the terminal.
    """

    stack.start_edge()
    stack.start_robot("carrier-01")
    stack.start_robot("picker-01")  # provisions and subscribes; its persistent session outlives the process
    time.sleep(2.0)
    stack.stop("picker-01")
    issued, expires = _times()
    task_id = stack.create(issued=issued, expires=expires)["task_id"]
    stack.wait_records(lambda records: any(r.record_kind == "task_publish_confirmed" and r.payload["task_id"] == task_id for r in records))
    stack.stop("edge")
    # The robot (paced: one execution step per 5 s) receives the queued request and runs the task while the Edge is down.
    stack.start_robot("picker-01", step_interval_s=5.0)
    stack.wait_robot_records("picker-01", lambda records: any(r.record_kind == "event_publish_confirmed" and (r.payload["boot_sequence"], r.payload["event_sequence"]) == (2, 3) for r in records), timeout_s=30)
    stack.reset_edge_session()  # ACCEPTED(2,1) PROGRESS(2,2) PROGRESS(2,3) are lost; SUCCEEDED(2,4) will queue in the fresh session
    stack.wait_robot_records("picker-01", lambda records: any(r.record_kind == "event_publish_confirmed" and (r.payload["boot_sequence"], r.payload["event_sequence"]) == (2, 4) for r in records), timeout_s=30)
    stack.start_edge()
    assert stack.wait_state(task_id, {"SUCCEEDED"}) == "SUCCEEDED"
    first = stack.received(task_id)[0].payload
    assert first["event"]["kind"] == "SUCCEEDED" and first["disposition"] == "applied" and first["state_before"] == "CREATED"
    assert list(first["missing_sequences_after"]) == [1, 2, 3] and first["acceptance_observed_after"] is False
    # Zero proactive resend after a legitimate terminal, even with the history gap, across two progress windows.
    assert stack.publish_attempts(task_id) == 1
    time.sleep(2 * stack.config["edge"]["default_progress_window_s"] + 2)
    assert stack.publish_attempts(task_id) == 1
    assert stack.executions("picker-01", task_id) == 1
    # Natural late history: a copy of the original request still in flight reaches the robot; it replays its history.
    stack.inject(request_topic(stack.config["site_id"], "picker-01"), stack.request_bytes(task_id))
    stack.wait_records(lambda records: any(r.record_kind == "task_event_received" and r.payload["task_id"] == task_id and not r.payload["missing_sequences_after"] and r.payload["acceptance_observed_after"] is True for r in records), timeout_s=20)
    dispositions = [(r.payload["event"]["kind"], r.payload["event"]["event_sequence"], r.payload["disposition"]) for r in stack.received(task_id)]
    assert dispositions[0] == ("SUCCEEDED", 4, "applied")
    assert sorted(dispositions[1:]) == [("ACCEPTED", 1, "late_evidence"), ("PROGRESS", 2, "late_evidence"), ("PROGRESS", 3, "late_evidence"), ("SUCCEEDED", 4, "duplicate")]
    assert stack.task_state(task_id) == ("SUCCEEDED", None)
    assert stack.publish_attempts(task_id) == 1
    assert stack.executions("picker-01", task_id) == 1
    assert sum(1 for r in stack.robot_records("picker-01") if r.record_kind == "task_decision") == 1


def test_conflict_gate_holds_when_inconclusive_arrives_first_over_real_broker(stack: Stack) -> None:
    """B3′a over the real broker: a genuine INCONCLUSIVE (crash + restart) then a late conflicting SUCCEEDED."""

    stack.start_edge()
    stack.start_robot("picker-01", "crash_after_execution_started")
    stack.start_robot("carrier-01")
    time.sleep(2.0)
    issued, expires = _times()
    task_id = stack.create(issued=issued, expires=expires)["task_id"]
    assert stack.wait_exit("picker-01", timeout=30) == 3
    stack.procs.pop("picker-01", None)
    stack.start_robot("picker-01", "accept_and_succeed")
    assert stack.wait_state(task_id, {"INCONCLUSIVE"}) == "INCONCLUSIVE"
    attempts = stack.publish_attempts(task_id)
    stack.inject_event(task_id, "SUCCEEDED", 1, 4)  # a late "success" for the same task from the crashed boot
    stack.wait_records(lambda records: any(r.record_kind == "conflicting_terminal" and r.payload["task_id"] == task_id for r in records), timeout_s=20)
    conflict = next(r for r in stack.edge_records() if r.record_kind == "conflicting_terminal" and r.payload["task_id"] == task_id)
    assert conflict.payload["first_terminal"]["kind"] == "INCONCLUSIVE" and conflict.payload["conflicting_terminal"]["kind"] == "SUCCEEDED"
    last = stack.received(task_id)[-1].payload
    assert last["disposition"] == "late_evidence" and last["conflict"] is True
    assert stack.task_state(task_id) == ("INCONCLUSIVE", "CONFLICT")
    issued2, expires2 = _times(2)
    blocked = stack.create(issued=issued2, expires=expires2)
    assert blocked["status"] == "rejected" and blocked["code"] == "authorization_blocked"
    time.sleep(3.0)
    assert stack.publish_attempts(task_id) == attempts
    assert stack.executions("picker-01", task_id) == 1
    # Replay == live: the gate survives an Edge restart.
    stack.stop("edge")
    stack.start_edge()
    time.sleep(2.0)
    blocked = stack.create(issued=issued2, expires=expires2)
    assert blocked["code"] == "authorization_blocked"


@pytest.mark.parametrize("kind, boot, seq, reason", [("INCONCLUSIVE", 9, 1, "interrupted_execution_unknown_outcome"), ("FAILED", 9, 1, "cannot_continue"), ("FAILED", 1, 4, "cannot_continue")])
def test_conflict_gate_holds_when_success_arrives_first_over_real_broker(stack: Stack, kind: str, boot: int, seq: int, reason: str) -> None:
    """B3′b over the real broker: a genuine SUCCEEDED, then a conflicting terminal from a later boot (INCONCLUSIVE, FAILED) or with the same key."""

    stack.start_edge()
    stack.start_robot("picker-01")
    stack.start_robot("carrier-01")
    time.sleep(2.0)
    issued, expires = _times()
    task_id = stack.create(issued=issued, expires=expires)["task_id"]
    assert stack.wait_state(task_id, {"SUCCEEDED"}) == "SUCCEEDED"
    attempts = stack.publish_attempts(task_id)
    stack.inject_event(task_id, kind, boot, seq, reason=reason)
    stack.wait_records(lambda records: any(r.record_kind == "conflicting_terminal" and r.payload["task_id"] == task_id for r in records), timeout_s=20)
    last = stack.received(task_id)[-1].payload
    assert last["event"]["kind"] == kind and last["conflict"] is True
    assert last["disposition"] == ("conflicting_replay" if (boot, seq) == (1, 4) else "evidence")
    assert stack.task_state(task_id) == ("SUCCEEDED", "CONFLICT")
    issued2, expires2 = _times(2)
    blocked = stack.create(issued=issued2, expires=expires2)
    assert blocked["status"] == "rejected" and blocked["code"] == "authorization_blocked"
    time.sleep(3.0)
    assert stack.publish_attempts(task_id) == attempts
    assert stack.executions("picker-01", task_id) == 1


def test_lost_robot_journal_is_refused_and_never_re_executes_over_real_broker(stack: Stack) -> None:
    """B4 identity continuity over the real broker: a wiped journal exits 4; re-provisioning purges the queued request."""

    stack.start_edge()
    stack.start_robot("picker-01")
    stack.start_robot("carrier-01")
    time.sleep(2.0)
    issued, expires = _times()
    task_id = stack.create(issued=issued, expires=expires)["task_id"]
    assert stack.wait_state(task_id, {"SUCCEEDED"}) == "SUCCEEDED"
    stack.stop("picker-01")
    stack.inject(request_topic(stack.config["site_id"], "picker-01"), stack.request_bytes(task_id))  # queued for the offline robot
    journal = stack.root / "robots" / "picker-01" / "robot_task_journal.jsonl"
    journal.unlink()
    stack.start_robot("picker-01", initialize=False)
    assert stack.wait_exit("picker-01", timeout=20) == 4
    stack.procs.pop("picker-01", None)
    assert not journal.exists()
    assert "robot_state_lost" in stack.logs["picker-01"].read_text(encoding="utf-8")
    stack.start_robot("picker-01", initialize=True)
    time.sleep(4.0)
    kinds = [r.record_kind for r in stack.robot_records("picker-01")]
    assert kinds[0] == "robot_provisioned"
    assert kinds.count("request_received") == 0 and kinds.count("execution_started") == 0
    assert any(r.record_kind == "session_regression" and r.payload["robot_id"] == "picker-01" for r in stack.edge_records())
    issued2, expires2 = _times(2)
    blocked = stack.create(issued=issued2, expires=expires2)
    assert blocked["code"] == "authorization_blocked"
