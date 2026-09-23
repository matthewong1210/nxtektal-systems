"""Real local Mosquitto, real receiver, separate processes, separate storage (PR B must-run).

Composes PR A's process stack (broker, Edge, picker, carrier) with the
intervention service, the local test receiver, and the operator CLI: each is
its own process with its own evidence directory, and the test process reads
evidence only.  Skips with an explicit reason when ``mosquitto`` is not on
PATH; a skip is not acceptance evidence.  Hosted CI has no broker, so this
module only ever runs locally and the hand-off must attach a local run.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

SIM_ROOT = Path(__file__).resolve().parents[2]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_interventions import (  # noqa: E402
    ACKNOWLEDGED,
    CRITICAL,
    DELIVERED,
    INTERVENTION_JOURNAL_SCHEMA,
    INTERVENTION_RECORD_KINDS,
    OPEN,
    RECEIVER_JOURNAL_SCHEMA,
    RECEIVER_RECORD_KINDS,
    RESOLVED,
    InterventionConfig,
    derive_view,
)
from nxt_edge_task.journal import JsonlJournal  # noqa: E402
from tests.edge_interventions.conftest import INTERVENTION_CONFIG_PATH  # noqa: E402
from tests.edge_task.test_integration_mosquitto import MOSQUITTO, PY, Stack, _free_port, _times, _wait_port  # noqa: E402

pytestmark = pytest.mark.skipif(MOSQUITTO is None, reason="mosquitto not on PATH: PR B local broker acceptance not run here")

ENV = {**os.environ, "PYTHONPATH": str(SIM_ROOT)}


class InterventionStack(Stack):
    """PR A's process stack plus receiver, service, and CLI as separate processes."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.receiver_port = _free_port()
        payload = json.loads(INTERVENTION_CONFIG_PATH.read_text(encoding="utf-8"))
        payload.update({"evidence_dir": "interventions", "reminder_interval_s": 6, "max_reminders": 2, "notify_max_attempts": 4, "notify_retry_interval_s": 2, "tick_interval_s": 0.25})
        payload["receiver"].update({"port": self.receiver_port, "evidence_dir": "receiver", "timeout_s": 2.0})
        self.intervention_config_path = root / "interventions.json"
        self.intervention_config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.intervention_config = InterventionConfig.from_dict(payload)

    # -- processes ----------------------------------------------------------

    def start_receiver(self, *, drop_responses: int = 0) -> None:
        args = ["scripts/edge_notification_receiver_v0.py", "--config", str(self.intervention_config_path), "--evidence-root", str(self.root)]
        if drop_responses:
            args += ["--drop-responses", str(drop_responses)]
        self._spawn("receiver", args)
        _wait_port(self.receiver_port)

    def start_service(self, *, name: str = "service", max_seconds: float | None = None) -> None:
        args = ["scripts/edge_intervention_service_v0.py", "--edge-config", str(self.config_path), "--config", str(self.intervention_config_path), "--evidence-root", str(self.root)]
        if max_seconds is not None:
            args += ["--max-seconds", str(max_seconds)]
        self._spawn(name, args)

    def kill(self, name: str) -> None:
        """Abrupt loss: no clean disconnect, no farewell message."""

        proc = self.procs.pop(name)
        proc.kill()
        proc.wait(timeout=5)

    # -- operator CLI (its own process) ----------------------------------------

    def cli(self, *args: str) -> tuple[int, dict[str, Any]]:
        result = subprocess.run(
            PY + ["scripts/edge_intervention_cli.py", "--config", str(self.intervention_config_path), "--evidence-root", str(self.root), *args],
            cwd=SIM_ROOT, capture_output=True, text=True, env=ENV, check=False,
        )
        assert result.returncode in (0, 1), result.stderr
        return result.returncode, json.loads(result.stdout)  # one indented JSON document

    # -- evidence (read by the test process only) --------------------------------

    def intervention_records(self) -> list:
        path = self.root / "interventions" / self.config["site_id"] / self.config["deployment_id"] / "edge_interventions_journal.jsonl"
        if not path.exists():
            return []
        return list(JsonlJournal(path, allowed_kinds=INTERVENTION_RECORD_KINDS, schema=INTERVENTION_JOURNAL_SCHEMA).read())

    def view(self):
        return derive_view(self.intervention_config, self.intervention_records())

    def receiver_records(self) -> list:
        path = self.root / "receiver" / "receiver_journal.jsonl"
        if not path.exists():
            return []
        return list(JsonlJournal(path, allowed_kinds=RECEIVER_RECORD_KINDS, schema=RECEIVER_JOURNAL_SCHEMA).read())

    def received_ids(self) -> list[str]:
        return [r.payload["notification_id"] for r in self.receiver_records() if r.record_kind == "notification_received"]

    def duplicate_attempts(self, notification_id: str | None = None) -> int:
        return sum(1 for r in self.receiver_records() if r.record_kind == "notification_duplicate_attempt" and (notification_id is None or r.payload["notification_id"] == notification_id))

    def attempts(self, notification_id: str) -> int:
        return sum(1 for r in self.intervention_records() if r.record_kind == "notification_attempted" and r.payload["notification_id"] == notification_id)

    def wait_view(self, predicate: Callable[[Any], bool], timeout_s: float = 30.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            view = self.view()
            if predicate(view):
                return view
            time.sleep(0.25)
        raise AssertionError(f"intervention condition not met; logs: {self._tail()}")

    def edge_task_records(self, task_id: str) -> int:
        return sum(1 for r in self.edge_records() if r.payload.get("task_id") == task_id)

    # -- direct receiver access (independence check) -----------------------------

    def post(self, payload: Any) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(self.intervention_config.receiver.url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, json.loads(raw.decode("utf-8")) if raw else {}


@pytest.fixture
def stack(tmp_path: Path):
    stack = InterventionStack(tmp_path)
    stack.start_broker()
    try:
        yield stack
    finally:
        stack.stop_all()


def _start_all(stack: InterventionStack, picker_behavior: str, *, drop_responses: int = 0) -> None:
    stack.start_receiver(drop_responses=drop_responses)
    stack.start_edge()
    stack.start_robot("picker-01", picker_behavior)
    stack.start_robot("carrier-01")
    stack.start_service()
    time.sleep(2.0)


def _blocked_task(stack: InterventionStack, offset_minutes: int = 0) -> str:
    issued, expires = _times(offset_minutes)
    task_id = stack.create(issued=issued, expires=expires)["task_id"]
    assert stack.wait_state(task_id, {"BLOCKED_AWAITING_HUMAN"}) == "BLOCKED_AWAITING_HUMAN"
    return task_id


def test_normal_task_then_explicit_request_ack_and_resolve_over_real_broker_and_receiver(stack: InterventionStack) -> None:
    """Acceptance 1, 2, 3, 6, 7, 9 with every party in its own process."""

    _start_all(stack, "accept_and_succeed")
    issued, expires = _times()
    first = stack.create(issued=issued, expires=expires)["task_id"]
    assert stack.wait_state(first, {"SUCCEEDED"}) == "SUCCEEDED"
    time.sleep(1.5)
    assert [r.record_kind for r in stack.intervention_records()] == ["intervention_service_started"]  # a normal task raises nothing
    assert stack.received_ids() == []
    # The picker restarts needing help on its next task (a plain restart: same identity).
    stack.stop("picker-01")
    stack.start_robot("picker-01", "help_needs_manual_recharge")
    time.sleep(2.0)
    task_id = _blocked_task(stack, 1)
    view = stack.wait_view(lambda v: any(n.intent == "OPENED" and n.state == DELIVERED for n in v.notifications.values()))
    assert len(view.cases) == 1
    case = next(iter(view.cases.values()))
    assert case.kind == "ASSISTANCE_REQUIRED" and case.subject_id == task_id and case.human_state == OPEN
    opened = next(n for n in view.notifications.values() if n.intent == "OPENED")
    assert opened.attempts == 1 and opened.receipt_id is not None
    assert stack.received_ids() == [opened.notification_id]
    # The same evidence keeps arriving every tick; it multiplies nothing.  A reminder is a new, identifiable notification.
    view = stack.wait_view(lambda v: any(n.intent == "REMINDER" and n.state == DELIVERED for n in v.notifications.values()), timeout_s=25)
    assert len(view.cases) == 1
    assert sum(1 for r in stack.intervention_records() if r.record_kind == "case_opened") == 1
    reminder = next(n for n in view.notifications.values() if n.intent == "REMINDER")
    assert reminder.notification_id != opened.notification_id
    assert set(stack.received_ids()) == {opened.notification_id, reminder.notification_id}
    assert len(stack.received_ids()) == len(set(stack.received_ids()))
    # Edge facts before any human record.
    executions = stack.executions("picker-01", task_id)
    edge_records = stack.edge_task_records(task_id)
    issued3, expires3 = _times(2)
    refused_before = stack.create(issued=issued3, expires=expires3)
    assert refused_before["status"] == "rejected" and refused_before["code"] == "robot_has_active_task"
    # Operator CLI: list, premature resolve, ack.
    code, listing = stack.cli("list")
    assert code == 0 and case.case_id in listing["cases"]
    code, rejected = stack.cli("resolve", "--case", case.case_id, "--operator", "bob", "--resolution", "too soon")
    assert code == 1 and rejected["status"] == "rejected"
    code, acked = stack.cli("ack", "--case", case.case_id, "--operator", "bob", "--note", "walking over")
    assert code == 0 and acked["human_state"] == ACKNOWLEDGED and acked["authorization_effect"] == "none"
    reminders_at_ack = sum(1 for n in stack.view().notifications.values() if n.intent == "REMINDER")
    time.sleep(2 * stack.intervention_config.reminder_interval_s + 2)
    assert sum(1 for n in stack.view().notifications.values() if n.intent == "REMINDER") == reminders_at_ack  # acknowledged: no nagging
    code, shown = stack.cli("show", case.case_id)
    assert code == 0 and shown["human_state"] == ACKNOWLEDGED and shown["condition_active"] is True  # still visible, still blocked
    code, resolved = stack.cli("resolve", "--case", case.case_id, "--operator", "bob", "--resolution", "battery swapped; re-authorization is a separate contract")
    assert code == 0 and resolved["human_state"] == RESOLVED and resolved["condition_active"] is True  # resolved while still blocked
    time.sleep(1.5)
    # Nothing on the Edge side moved: same state, same result, same execution count, same refusal, no new task records.
    assert stack.task_state(task_id) == ("BLOCKED_AWAITING_HUMAN", None)
    assert stack.executions("picker-01", task_id) == executions == 1
    refused_after = stack.create(issued=issued3, expires=expires3)
    assert (refused_after["status"], refused_after["code"]) == (refused_before["status"], refused_before["code"])  # the slot is not freed
    assert stack.edge_task_records(task_id) == edge_records
    assert sum(1 for r in stack.edge_records() if r.record_kind == "task_created") == 2  # the two tasks above, nothing from the CLI
    carrier = [r.record_kind for r in stack.robot_records("carrier-01")]
    assert carrier.count("execution_started") == 0 and carrier.count("task_decision") == 0
    # Resolved cases stay visible; a service restart reopens nothing.
    stack.stop("service")
    stack.start_service()
    time.sleep(2.5)
    view = stack.view()
    assert len(view.cases) == 1 and view.cases[case.case_id].human_state == RESOLVED
    assert sum(1 for r in stack.intervention_records() if r.record_kind == "case_opened") == 1


def test_lost_receipt_service_restart_and_receiver_restart_keep_one_unique_receipt(stack: InterventionStack) -> None:
    """Acceptance 4-5 over real processes: the receiver persists, the answer is lost, the retry carries the same id."""

    _start_all(stack, "help_needs_manual_recharge", drop_responses=1)
    task_id = _blocked_task(stack)
    view = stack.wait_view(lambda v: any(n.intent == "OPENED" and n.attempts >= 1 and n.last_result == "unknown" for n in v.notifications.values()))
    opened = next(n for n in view.notifications.values() if n.intent == "OPENED")
    assert opened.receipt_id is None
    assert stack.received_ids() == [opened.notification_id]  # persisted at the receiver although the sender does not know
    view = stack.wait_view(lambda v: v.notifications[opened.notification_id].state == DELIVERED, timeout_s=20)
    live = view.notifications[opened.notification_id]
    assert live.attempts == 2 and live.receipt_id is not None
    assert stack.received_ids().count(opened.notification_id) == 1
    assert stack.duplicate_attempts(opened.notification_id) == 1
    # Service restart: the same case, no second OPENED intent, no second delivery of it.
    stack.stop("service")
    stack.start_service()
    time.sleep(2.5)
    assert sum(1 for r in stack.intervention_records() if r.record_kind == "case_opened") == 1
    assert [n.intent for n in stack.view().notifications.values()].count("OPENED") == 1
    assert stack.attempts(opened.notification_id) == 2
    assert stack.received_ids().count(opened.notification_id) == 1
    # Receiver restart: its ledger is rebuilt from its own journal only; the same id is still a duplicate with the original receipt.
    stack.stop("receiver")
    stack.start_receiver()
    status, body = stack.post(live.payload)
    assert status == 200 and body["duplicate"] is True and body["receipt_id"] == live.receipt_id
    assert stack.received_ids().count(opened.notification_id) == 1
    assert stack.duplicate_attempts(opened.notification_id) == 2
    # An invalid message is refused and never becomes a receipt.
    status, body = stack.post({"schema": "not-a-notification"})
    assert status == 400 and "receipt_id" not in body
    assert sum(1 for r in stack.receiver_records() if r.record_kind == "notification_refused") == 1
    assert stack.task_state(task_id) == ("BLOCKED_AWAITING_HUMAN", None)


def test_one_dispatcher_per_persistent_service_identity_with_crash_recovery(stack: InterventionStack) -> None:
    """Two real service processes cannot spend one notification budget concurrently."""

    _start_all(stack, "help_needs_manual_recharge", drop_responses=1)
    _blocked_task(stack)
    view = stack.wait_view(
        lambda current: any(
            notification.intent == "OPENED"
            and notification.attempts == 1
            and notification.last_result == "unknown"
            for notification in current.notifications.values()
        )
    )
    opened = next(notification for notification in view.notifications.values() if notification.intent == "OPENED")
    first = stack.procs["service"]
    first.send_signal(signal.SIGSTOP)
    try:
        records_before = [record.record_id for record in stack.intervention_records()]
        receiver_before = [record.record_id for record in stack.receiver_records()]

        stack.start_service(name="service-contender", max_seconds=10)
        contender_code = stack.wait_exit("service-contender", timeout=5)
        assert contender_code == 2
        assert [record.record_id for record in stack.intervention_records()] == records_before
        assert [record.record_id for record in stack.receiver_records()] == receiver_before
        assert stack.attempts(opened.notification_id) == 1
        assert stack.received_ids() == [opened.notification_id]

        # The service-lifetime lock is separate from the journal append lock:
        # read-only views and valid human records remain available.
        code, listing = stack.cli("list")
        assert code == 0 and opened.case_id in listing["cases"]
        code, acked = stack.cli("ack", "--case", opened.case_id, "--operator", "lock-test", "--note", "taking the case")
        assert code == 0 and acked["human_state"] == ACKNOWLEDGED
        code, resolved = stack.cli("resolve", "--case", opened.case_id, "--operator", "lock-test", "--resolution", "human disposition only")
        assert code == 0 and resolved["human_state"] == RESOLVED
    finally:
        first.send_signal(signal.SIGCONT)

    # The original dispatcher continues normally. Its retry is legitimate:
    # one notification id, two persisted attempts, one unique receiver row.
    view = stack.wait_view(lambda current: current.notifications[opened.notification_id].state == DELIVERED, timeout_s=20)
    delivered = view.notifications[opened.notification_id]
    assert delivered.attempts == 2
    assert stack.attempts(opened.notification_id) == 2
    assert stack.received_ids() == [opened.notification_id]
    assert stack.duplicate_attempts(opened.notification_id) == 1

    # Abrupt process loss releases only the runtime qualification. A new
    # instance uses the same journal and ids without deleting any evidence.
    started_before = sum(1 for record in stack.intervention_records() if record.record_kind == "intervention_service_started")
    stack.kill("service")
    stack.start_service()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        started_after = sum(1 for record in stack.intervention_records() if record.record_kind == "intervention_service_started")
        if started_after == started_before + 1:
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f"replacement service did not start; logs: {stack._tail()}")
    assert stack.procs["service"].poll() is None
    assert stack.attempts(opened.notification_id) == 2
    assert stack.received_ids() == [opened.notification_id]


def test_service_process_exits_two_on_journal_failure_without_delivery_side_effects(stack: InterventionStack) -> None:
    """The real service process fail-stops at its journal boundary, preserving the cause."""

    stack.start_receiver()
    journal_path = stack.root / "interventions" / stack.config["site_id"] / stack.config["deployment_id"] / "edge_interventions_journal.jsonl"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_bytes(b'{"truncated"')
    journal_before = journal_path.read_bytes()
    receiver_before = [record.record_id for record in stack.receiver_records()]

    stack.start_service(name="failing-service", max_seconds=5)
    code = stack.wait_exit("failing-service", timeout=5)
    log = stack.logs["failing-service"].read_text(encoding="utf-8")

    assert code == 2
    assert '"event": "fail_stop"' in log
    assert "journal append failed" in log
    assert "JournalIntegrityError" in log
    assert "last record is not newline-terminated" in log
    assert journal_path.read_bytes() == journal_before
    assert [record.record_id for record in stack.receiver_records()] == receiver_before


def test_lost_device_with_open_task_over_real_broker_keeps_last_valid_data_and_unknown_marker(stack: InterventionStack) -> None:
    """Acceptance 8 over real processes: an abruptly killed robot with an open task."""

    _start_all(stack, "silent_after_accept")
    issued, expires = _times()
    task_id = stack.create(issued=issued, expires=expires)["task_id"]
    assert stack.wait_state(task_id, {"ACCEPTED"}) == "ACCEPTED"
    stack.kill("picker-01")
    view = stack.wait_view(lambda v: any(c.kind == "DEVICE_UNREACHABLE" for c in v.cases.values()), timeout_s=40)
    cases = [c for c in view.cases.values() if c.kind == "DEVICE_UNREACHABLE"]
    assert len(cases) == 1
    case = cases[0]
    assert case.severity == CRITICAL and case.subject_id == "picker-01"
    evidence = case.evidence
    assert evidence["open_task_id"] == task_id
    assert evidence["state_unknown"] is True and evidence["stopped_confirmed"] is False
    assert evidence["last_valid_status_received_at_utc"] is not None
    # The last valid data is the Edge's own last word, read from the Edge journal, not invented.
    last_status = [r for r in stack.edge_records() if r.record_kind in {"device_status_changed", "device_liveness_changed"} and r.payload["robot_id"] == "picker-01" and r.payload.get("status")]
    assert last_status, "the Edge never recorded a picker status"
    assert evidence["last_reported_availability"] == last_status[-1].payload["status"]["availability"]
    stack.wait_view(lambda v: any(n.case_id == case.case_id and n.state == DELIVERED for n in v.notifications.values()), timeout_s=20)
    message = next(n for n in stack.view().notifications.values() if n.case_id == case.case_id).message
    assert "UNKNOWN" in message and "not confirmed stopped or parked" in message
    assert evidence["last_valid_status_received_at_utc"] in message
    # The Edge keeps the task open and the device OFFLINE; nothing was set idle or parked.
    assert stack.task_state(task_id)[0] == "ACCEPTED"
    offline = [r for r in stack.edge_records() if r.record_kind == "device_liveness_changed" and r.payload["robot_id"] == "picker-01"]
    assert offline and offline[-1].payload["to"] == "OFFLINE"
    # Killed right after acceptance: execution may or may not have been journaled, but nothing ever completed.
    assert stack.executions("picker-01", task_id) <= 1
    assert not any(r.record_kind == "execution_completed" for r in stack.robot_records("picker-01"))
