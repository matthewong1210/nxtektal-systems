#!/usr/bin/env python3
"""Edge Intervention CLI V0 -- local view and human ack/resolve (SIMULATION).

``list`` and ``show`` derive the current cases and notifications from the
intervention journal.  ``ack`` and ``resolve`` append OPERATOR records under
the journal lock through the package's validated transitions.  They record
human handling only: nothing here writes the Edge or robot journals, forges
a robot event, edits evidence, clears a conflict, reopens an authorization
gate, marks a task successful, or creates a task.  The operator id is a
recorded label, not an authenticated identity.

Run from ``simulation/``::

    uv run --no-sync python -B scripts/edge_intervention_cli.py \
        --config configs/edge_task/pilot-course-a.interventions.sim.example.json list
    uv run --no-sync python -B scripts/edge_intervention_cli.py \
        --config configs/edge_task/pilot-course-a.interventions.sim.example.json \
        ack --case <case_id> --operator alice --note "on my way"
    uv run --no-sync python -B scripts/edge_intervention_cli.py \
        --config configs/edge_task/pilot-course-a.interventions.sim.example.json \
        resolve --case <case_id> --operator alice --resolution "battery swapped on site; robot still needs re-authorization"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_interventions import InterventionConfig, InterventionCore, InterventionError, decide_acknowledge, decide_resolve, derive_view  # noqa: E402
from nxt_edge_task.journal import JournalIntegrityError, JsonlJournal, RecordSpec  # noqa: E402
from scripts.edge_intervention_service_v0 import DISCLAIMER, intervention_journal  # noqa: E402


def _to_spec(spec) -> RecordSpec:
    return RecordSpec(record_kind=spec.record_kind, origin=spec.origin, recorded_at_utc=spec.recorded_at_utc, payload=dict(spec.payload))


def list_view(journal: JsonlJournal, config: InterventionConfig) -> dict[str, Any]:
    view = derive_view(config, journal.read())
    return {"disclaimer": DISCLAIMER, **view.snapshot()}


def show_view(journal: JsonlJournal, config: InterventionConfig, case_id: str) -> dict[str, Any] | None:
    view = derive_view(config, journal.read())
    case = view.cases.get(case_id)
    if case is None:
        return None
    return {"disclaimer": DISCLAIMER, **case.summary(), "notifications": [n.summary() for n in view.notifications_for(case_id)]}


def operator_action(journal: JsonlJournal, config: InterventionConfig, action: str, case_id: str, operator: str, text: str, *, now: datetime) -> dict[str, Any]:
    core = InterventionCore(config)
    outcome: dict[str, Any] = {}

    def decide(view):
        if action == "ack":
            ok, specs = decide_acknowledge(view, case_id, operator, text, now)
        else:
            ok, specs = decide_resolve(view, case_id, operator, text, now)
        outcome["ok"] = ok
        outcome["records"] = [s.record_kind for s in specs]
        return specs

    inner = core.builder(decide)
    appended = journal.append_via(lambda records: [_to_spec(s) for s in inner(records)])
    core.absorb(appended)
    case = core.view.cases.get(case_id)
    return {
        "disclaimer": DISCLAIMER,
        "action": action,
        "case_id": case_id,
        "status": "recorded" if outcome["ok"] else "rejected",
        "records": outcome["records"],
        "detail": None if outcome["ok"] else appended[-1].payload.get("detail"),
        "human_state": None if case is None else case.human_state,
        "condition_active": None if case is None else case.condition_active,
        "authorization_effect": "none",
        "device_state_effect": "none",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=SIM_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list cases and notifications")
    show = sub.add_parser("show", help="show one case with its notifications")
    show.add_argument("case_id")
    for name, text_flag, help_text in (("ack", "--note", "take the case (OPEN -> ACKNOWLEDGED)"), ("resolve", "--resolution", "record the disposition (ACKNOWLEDGED -> RESOLVED)")):
        action = sub.add_parser(name, help=help_text)
        action.add_argument("--case", required=True)
        action.add_argument("--operator", required=True, help="recorded operator label (not an authenticated identity)")
        action.add_argument(text_flag, required=True, dest="text")
    args = parser.parse_args(argv)

    config = InterventionConfig.from_json(args.config.read_text(encoding="utf-8"))
    journal = intervention_journal(config, args.evidence_root)
    try:
        if args.command == "list":
            print(json.dumps(list_view(journal, config), indent=2, sort_keys=True))
            return 0
        if args.command == "show":
            result = show_view(journal, config, args.case_id)
            if result is None:
                print(json.dumps({"error": "unknown_case", "case_id": args.case_id}), file=sys.stderr)
                return 1
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        result = operator_action(journal, config, args.command, args.case, args.operator, args.text, now=datetime.now(timezone.utc))
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "recorded" else 1
    except InterventionError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}), file=sys.stderr)
        return 1
    except JournalIntegrityError as exc:
        print(json.dumps({"error": "journal_integrity", "detail": str(exc)}), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
