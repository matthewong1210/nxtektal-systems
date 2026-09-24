"""Acceptance 7: ack/resolve are human records only; task result, gate, and execution count never change."""

from __future__ import annotations

import json
import subprocess
import sys

from nxt_edge_interventions import ACKNOWLEDGED, OPEN, RESOLVED
from tests.edge_interventions.conftest import INTERVENTION_CONFIG_PATH, InterventionHarness, run_until
from tests.edge_task.conftest import SIM_ROOT, run_until as edge_run_until


def _blocked_task(stack: InterventionHarness) -> str:
    stack.edge.start_all("help_needs_manual_recharge")
    stack.start_service()
    stack.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(stack.edge, lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN")
    run_until(stack, lambda: stack.receiver.unique_received() == 1)
    return task_id


def _edge_facts(stack: InterventionHarness, task_id: str) -> tuple:
    task = stack.edge.task(task_id)
    return (
        task["state"],
        task["effective_result"],
        tuple(task["reconciliation_reasons"]),
        stack.edge.executions("picker-01", task_id),
        stack.edge.device("picker-01")["last_reported_availability"],
        stack.edge.create(issued="2026-09-12T08:05:00.000000Z")["code"],  # the gate/slot answer
        len(stack.edge.edge_records()),
    )


def test_ack_then_resolve_change_only_human_state(stack: InterventionHarness) -> None:
    task_id = _blocked_task(stack)
    case = stack.cases()[0]
    before = _edge_facts(stack, task_id)
    edge_records_before = len(stack.edge.edge_records())
    assert stack.ack(case.case_id, operator="alice", note="taking it")["status"] == "recorded"
    assert stack.resolve(case.case_id, operator="alice", resolution="battery swapped; robot still needs a separate re-authorization")["status"] == "recorded"
    stack.step(3, robots=False)  # no new robot evidence during the check
    after = _edge_facts(stack, task_id)
    assert after[:5] == before[:5]
    assert after[5] == before[5]  # the Edge still refuses a new task the same way (robot busy/blocked)
    # The human records touched only the intervention journal.
    assert len(stack.edge.edge_records()) == edge_records_before + 1  # exactly the second create rejection above
    view = stack.view()
    resolved = view.cases[case.case_id]
    assert resolved.human_state == RESOLVED and resolved.resolved_by == "alice"
    assert resolved.condition_active is True  # the robot is still blocked and that stays visible
    assert resolved.summary()["authorization_effect"] == "none"
    kinds = stack.kinds()
    assert kinds.count("case_acknowledged") == 1 and kinds.count("case_resolved") == 1


def test_resolve_requires_acknowledgement_and_valid_operator(stack: InterventionHarness) -> None:
    _blocked_task(stack)
    case = stack.cases()[0]
    rejected = stack.resolve(case.case_id)
    assert rejected["status"] == "rejected" and "requires ACKNOWLEDGED" in rejected["detail"]
    assert stack.view().cases[case.case_id].human_state == OPEN
    bad_operator = stack.ack(case.case_id, operator="", note="x")
    assert bad_operator["status"] == "rejected"
    bad_note = stack.ack(case.case_id, operator="alice", note="   ")
    assert bad_note["status"] == "rejected"
    unknown = stack.ack("case_doesnotexist")
    assert unknown["status"] == "rejected" and unknown["records"] == ["operator_action_rejected"]
    assert stack.ack(case.case_id)["status"] == "recorded"
    twice = stack.ack(case.case_id)
    assert twice["status"] == "rejected"
    assert stack.view().cases[case.case_id].human_state == ACKNOWLEDGED
    assert stack.kinds().count("operator_action_rejected") == 5


def test_cli_entry_points_round_trip_and_never_touch_the_edge_journal(stack: InterventionHarness) -> None:
    task_id = _blocked_task(stack)
    case = stack.cases()[0]
    edge_bytes = stack.edge.edge_journal_path.read_bytes()
    base = [sys.executable, "-B", str(SIM_ROOT / "scripts" / "edge_intervention_cli.py"), "--config", str(INTERVENTION_CONFIG_PATH), "--evidence-root", str(stack.root)]

    def cli(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([*base, *args], capture_output=True, text=True, check=False, cwd=SIM_ROOT)

    listing = cli("list")
    assert listing.returncode == 0, listing.stderr
    assert case.case_id in json.loads(listing.stdout)["cases"]
    shown = cli("show", case.case_id)
    assert shown.returncode == 0 and json.loads(shown.stdout)["human_state"] == OPEN
    missing = cli("show", "case_nope")
    assert missing.returncode == 1
    premature = cli("resolve", "--case", case.case_id, "--operator", "bob", "--resolution", "done")
    assert premature.returncode == 1 and json.loads(premature.stdout)["status"] == "rejected"
    acked = cli("ack", "--case", case.case_id, "--operator", "bob", "--note", "on it")
    assert acked.returncode == 0 and json.loads(acked.stdout)["human_state"] == ACKNOWLEDGED
    resolved = cli("resolve", "--case", case.case_id, "--operator", "bob", "--resolution", "handled")
    assert resolved.returncode == 0 and json.loads(resolved.stdout)["human_state"] == RESOLVED
    assert stack.edge.edge_journal_path.read_bytes() == edge_bytes  # the Edge journal is byte-identical
    assert stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN"
