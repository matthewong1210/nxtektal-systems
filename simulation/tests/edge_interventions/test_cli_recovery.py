"""Read-only CLI visibility for durable cases whose notification intent is incomplete."""

from __future__ import annotations

import json
import subprocess
import sys

from nxt_edge_interventions import derive_view, reconcile
from nxt_edge_task.journal import RecordSpec
from scripts.edge_intervention_cli import _processing_summary
from scripts.edge_intervention_service_v0 import _to_spec, edge_snapshot
from tests.edge_interventions.conftest import INTERVENTION_CONFIG_PATH, InterventionHarness
from tests.edge_task.conftest import SIM_ROOT, run_until as edge_run_until


def _persist_case_without_its_intent(stack: InterventionHarness):
    stack.edge.start_all("help_needs_manual_recharge")
    stack.edge.step(2)
    task_id = stack.edge.create()["task_id"]
    edge_run_until(
        stack.edge,
        lambda: stack.edge.task(task_id)["state"] == "BLOCKED_AWAITING_HUMAN",
    )
    specs = reconcile(
        derive_view(stack.config, ()),
        edge_snapshot(stack.edge.config, stack.edge.edge_journal()),
        stack.clock(),
    )
    assert [spec.record_kind for spec in specs] == ["case_opened", "notification_intended"]
    stack.journal().append(_to_spec(specs[0]))
    return specs


def test_list_and_show_expose_incomplete_batch_without_writing_or_acting(stack: InterventionHarness) -> None:
    case_spec, intent_spec = _persist_case_without_its_intent(stack)
    case_id = case_spec.payload["case_id"]
    journal_before = stack.journal_path.read_bytes()
    anchor_before = stack.journal().anchor_path.read_bytes()
    edge_before = stack.edge.edge_journal_path.read_bytes()
    deliveries_before = list(stack.receiver.deliveries)
    base = [
        sys.executable,
        "-B",
        str(SIM_ROOT / "scripts" / "edge_intervention_cli.py"),
        "--config",
        str(INTERVENTION_CONFIG_PATH),
        "--evidence-root",
        str(stack.root),
    ]

    listing = subprocess.run([*base, "list"], capture_output=True, text=True, check=False, cwd=SIM_ROOT)
    shown = subprocess.run([*base, "show", case_id], capture_output=True, text=True, check=False, cwd=SIM_ROOT)

    assert listing.returncode == 0, listing.stderr
    assert shown.returncode == 0, shown.stderr
    listed_processing = json.loads(listing.stdout)["cases"][case_id]["processing"]
    shown_processing = json.loads(shown.stdout)["processing"]
    assert listed_processing == shown_processing
    assert shown_processing == {
        "scope": "notification_intent_persistence",
        "status": "pending_recovery",
        "reason": "persisted case event has no matching notification_intended record",
        "persisted_records": [
            {
                "record_id": stack.records()[0].record_id,
                "record_kind": "case_opened",
                "recorded_at_utc": case_spec.recorded_at_utc,
            }
        ],
        "inferred_pending_records": [
            {
                "derived_from": "case_opened",
                "record_kind": "notification_intended",
                "recorded_at_utc": intent_spec.recorded_at_utc,
                "intent": "OPENED",
                "notification_id": intent_spec.payload["notification_id"],
            }
        ],
    }
    assert json.loads(listing.stdout)["notifications"] == {}
    assert json.loads(shown.stdout)["notifications"] == []
    assert stack.journal_path.read_bytes() == journal_before
    assert stack.journal().anchor_path.read_bytes() == anchor_before
    assert stack.edge.edge_journal_path.read_bytes() == edge_before
    assert stack.receiver.deliveries == deliveries_before == []
    assert all(record.record_kind != "operator_action_rejected" for record in stack.records())


def test_processing_summary_marks_unreliable_pending_derivation_unknown(stack: InterventionHarness) -> None:
    case_spec, _intent_spec = _persist_case_without_its_intent(stack)
    case_id = case_spec.payload["case_id"]
    view = derive_view(stack.config, stack.records())
    view.pending_intents[(case_id, "OPENED")] = RecordSpec(
        record_kind="notification_intended",
        origin="EDGE",
        recorded_at_utc=case_spec.recorded_at_utc,
        payload={"case_id": case_id, "intent": "OPENED"},
    )

    summary = _processing_summary(view, stack.records(), case_id)

    assert summary["status"] == "unknown"
    assert summary["inferred_pending_records"] == []
    assert "notification_id is unavailable" in summary["reason"]
