#!/usr/bin/env python3
"""Edge Task CLI V0 -- SIMULATION test entry and local read-only views.

``create-task`` is the only task producer in this rehearsal.  It appends a
``task_created`` record (origin ``SIM_ENTRY``) to the Edge journal under
the same lock and admission rules the gateway uses; the running gateway
picks it up and publishes it.  ``list`` and ``show`` derive the current
view from the journal.  PR B adds ``ack``/``resolve``; they do not exist
here and nothing in this CLI can command a robot.

Run from ``simulation/``::

    uv run --no-sync python -B scripts/edge_task_cli.py \
        --config configs/edge_task/pilot-course-a.sim.example.json \
        create-task --robot picker-01 --zone Z1 \
        --issued-at-utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --expires-at-utc <ten minutes later, UTC Z>

Both timestamps must lie in the future at creation time.
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

from nxt_edge_task.cases import EDGE_RECORD_KINDS, EdgeView, decide_task_create  # noqa: E402
from nxt_edge_task.contracts import (  # noqa: E402
    TASK_TYPE_COLLECT_BALLS_ZONE,
    AdmissionFacts,
    EdgeTaskConfig,
    EdgeTaskError,
    TaskRequest,
    utc_text,
)
from nxt_edge_task.journal import JsonlJournal  # noqa: E402

DISCLAIMER = "SIMULATION — test entry; not a production scheduler"


def journal_path(config: EdgeTaskConfig, root: Path) -> Path:
    return root / config.edge_evidence_dir / config.site_id / config.deployment_id / "edge_task_journal.jsonl"


def create_task(
    journal: JsonlJournal,
    config: EdgeTaskConfig,
    facts: AdmissionFacts,
    *,
    robot_id: str,
    zone_id: str,
    issued_at_utc: str,
    expires_at_utc: str,
    operator: str,
    progress_window_s: int | None,
    now: datetime,
) -> dict[str, Any]:
    request = TaskRequest.build(
        site_id=config.site_id,
        deployment_id=config.deployment_id,
        simulation_env_id=config.simulation_env_id,
        target_robot_id=robot_id,
        task_type=TASK_TYPE_COLLECT_BALLS_ZONE,
        zone_id=zone_id,
        issued_at_utc=issued_at_utc,
        expires_at_utc=expires_at_utc,
        progress_window_s=config.default_progress_window_s if progress_window_s is None else progress_window_s,
        issued_by=f"SIMULATION_TEST_ENTRY:{operator}",
    )
    outcome_box: dict[str, Any] = {}

    def build(records):
        view = EdgeView(config=config)
        view.apply_all(records)
        outcome, specs = decide_task_create(view, facts, request, now)
        outcome_box["outcome"] = outcome
        return specs

    journal.append_via(build)
    outcome = outcome_box["outcome"]
    return {
        "status": outcome.status,
        "task_id": outcome.task_id,
        "code": outcome.code,
        "detail": outcome.detail,
        "issued_by": request.issued_by,
        "disclaimer": DISCLAIMER,
    }


def view_from(journal: JsonlJournal, config: EdgeTaskConfig) -> EdgeView:
    view = EdgeView(config=config)
    view.apply_all(journal.read())
    return view


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=SIM_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-task", help="SIMULATION test entry: create one collection task")
    create.add_argument("--robot", required=True)
    create.add_argument("--zone", required=True)
    create.add_argument("--issued-at-utc", required=True)
    create.add_argument("--expires-at-utc", required=True)
    create.add_argument("--operator", default="local-dev")
    create.add_argument("--progress-window-s", type=int, default=None)
    sub.add_parser("list", help="list tasks and devices")
    show = sub.add_parser("show", help="show one task")
    show.add_argument("task_id")
    args = parser.parse_args(argv)

    from scripts.pilot_course_a_task_fixture import admission_facts  # noqa: E402

    config = EdgeTaskConfig.from_json(args.config.read_bytes())
    facts = admission_facts()
    journal = JsonlJournal(journal_path(config, args.evidence_root), allowed_kinds=EDGE_RECORD_KINDS)
    try:
        if args.command == "create-task":
            result = create_task(
                journal,
                config,
                facts,
                robot_id=args.robot,
                zone_id=args.zone,
                issued_at_utc=args.issued_at_utc,
                expires_at_utc=args.expires_at_utc,
                operator=args.operator,
                progress_window_s=args.progress_window_s,
                now=datetime.now(timezone.utc),
            )
            print(json.dumps(result, sort_keys=True, ensure_ascii=False))
            return 0 if result["status"] in {"created", "idempotent"} else 1
        view = view_from(journal, config)
        if args.command == "list":
            print(
                json.dumps(
                    {
                        "disclaimer": DISCLAIMER,
                        "as_of_utc": utc_text(datetime.now(timezone.utc)),
                        "devices": {k: v.summary() for k, v in view.devices.items()},
                        "tasks": {k: v.summary() for k, v in view.tasks.items()},
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        task = view.tasks.get(args.task_id)
        if task is None:
            print(json.dumps({"error": "unknown_task", "task_id": args.task_id}))
            return 1
        print(json.dumps({"disclaimer": DISCLAIMER, **task.summary(), "request": task.request.to_dict()}, sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    except EdgeTaskError as exc:
        print(json.dumps({"error": exc.code.value, "detail": exc.detail}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
