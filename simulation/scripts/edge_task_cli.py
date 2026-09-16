#!/usr/bin/env python3
"""Edge Task CLI V0 -- SIMULATION test entry and local read-only views.

``create-task`` is the only task producer in this rehearsal.  It appends a
``task_created`` record (origin ``SIM_ENTRY``) to the Edge journal under
the same lock and admission rules the gateway uses; the running gateway
picks it up and publishes it.  ``list`` and ``show`` derive the current
view from the journal.  PR B adds ``ack``/``resolve``; they do not exist
here and nothing in this CLI can command a robot.

Read views never re-stamp journaled state as current: ``list`` and ``show``
report the journal's last derived state next to what can be verified at
read time (``as_read``: the same receipt thresholds applied to the reader's
clock, plus the evidence age) and whether an Edge process holds the lock.

Run from ``simulation/``::

    uv run --no-sync python -B scripts/edge_task_cli.py \
        --config configs/edge_task/pilot-course-a.sim.example.json \
        create-task --robot picker-01 --zone Z1 \
        --issued-at-utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --expires-at-utc <ten minutes later, UTC Z>

Both timestamps must lie in the future at creation time.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_edge_task.cases import EDGE_RECORD_KINDS, TASK_CREATE_REJECTED, CreateOutcome, EdgeView, decide_task_create, read_time_liveness  # noqa: E402
from nxt_edge_task.contracts import (  # noqa: E402
    TASK_TYPE_COLLECT_BALLS_ZONE,
    AdmissionFacts,
    EdgeTaskConfig,
    EdgeTaskError,
    ErrorCode,
    TaskRequest,
    parse_utc,
    utc_text,
)
from nxt_edge_task.journal import JournalIntegrityError, JsonlJournal, RecordSpec  # noqa: E402

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
    issued_by = f"SIMULATION_TEST_ENTRY:{operator}"
    outcome_box: dict[str, Any] = {}

    def build(records):
        view = EdgeView(config=config)
        view.apply_all(records)
        device = view.devices.get(robot_id)
        incarnation = None if device is None else device.incarnation
        if incarnation is None:
            # The authorization is bound to the robot incarnation the Edge has
            # seen; without any received status there is nothing to bind to.
            detail = "no status has ever been received from this robot; the authorization cannot be bound to an incarnation"
            outcome_box["outcome"] = CreateOutcome("rejected", None, ErrorCode.DEVICE_INCARNATION_UNKNOWN.value, detail)
            return [
                RecordSpec(
                    record_kind=TASK_CREATE_REJECTED,
                    origin="SIM_ENTRY",
                    recorded_at_utc=utc_text(now),
                    payload={"task_id": None, "code": ErrorCode.DEVICE_INCARNATION_UNKNOWN.value, "detail": detail, "request": None, "target_robot_id": robot_id},
                )
            ]
        request = TaskRequest.build(
            site_id=config.site_id,
            deployment_id=config.deployment_id,
            simulation_env_id=config.simulation_env_id,
            target_robot_id=robot_id,
            target_incarnation=incarnation,
            task_type=TASK_TYPE_COLLECT_BALLS_ZONE,
            zone_id=zone_id,
            issued_at_utc=issued_at_utc,
            expires_at_utc=expires_at_utc,
            progress_window_s=config.default_progress_window_s if progress_window_s is None else progress_window_s,
            issued_by=issued_by,
        )
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
        "issued_by": issued_by,
        "disclaimer": DISCLAIMER,
    }


def view_from(journal: JsonlJournal, config: EdgeTaskConfig) -> EdgeView:
    view = EdgeView(config=config)
    view.apply_all(journal.read())
    return view


def edge_lock_held(journal_file: Path) -> bool | None:
    """Read-only probe: does some Edge gateway process hold ``.edge.lock``?

    Never creates the lock file and never keeps a lock.  ``None`` when the
    lock file exists but cannot be probed (permissions).
    """

    lock_path = journal_file.parent / ".edge.lock"
    if not lock_path.exists():
        return False
    try:
        with lock_path.open("rb") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
    except OSError:
        return None


def _freshness(journal: JsonlJournal, config: EdgeTaskConfig, now: datetime) -> tuple[EdgeView, dict[str, Any]]:
    records = journal.read()
    view = EdgeView(config=config)
    view.apply_all(records)
    last = records[-1].recorded_at_utc if records else None
    age = None if last is None else (now - parse_utc(last, "recorded_at_utc")).total_seconds()
    return view, {
        "read_at_utc": utc_text(now),
        "journal_last_record_at_utc": last,
        "journal_age_s": age,
        "edge_lock_held": edge_lock_held(journal.path),
    }


def list_view(journal: JsonlJournal, config: EdgeTaskConfig, *, now: datetime) -> dict[str, Any]:
    """Journaled state plus read-time freshness for every device and task."""

    view, freshness = _freshness(journal, config, now)
    return {
        "disclaimer": DISCLAIMER,
        **freshness,
        "devices": {
            robot_id: {**device.summary(), "as_read": read_time_liveness(device, config, now)}
            for robot_id, device in view.devices.items()
        },
        "tasks": {task_id: task.summary() for task_id, task in view.tasks.items()},
    }


def show_view(journal: JsonlJournal, config: EdgeTaskConfig, task_id: str, *, now: datetime) -> dict[str, Any] | None:
    view, freshness = _freshness(journal, config, now)
    task = view.tasks.get(task_id)
    if task is None:
        return None
    device = view.devices[task.request.target_robot_id]
    return {
        "disclaimer": DISCLAIMER,
        **freshness,
        **task.summary(),
        "request": task.request.to_dict(),
        "target_device_journaled_connectivity": device.connectivity.value,
        "target_device_as_read": read_time_liveness(device, config, now),
    }


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
        now = datetime.now(timezone.utc)
        if args.command == "list":
            print(json.dumps(list_view(journal, config, now=now), sort_keys=True, ensure_ascii=False, indent=2))
            return 0
        shown = show_view(journal, config, args.task_id, now=now)
        if shown is None:
            print(json.dumps({"error": "unknown_task", "task_id": args.task_id}))
            return 1
        print(json.dumps(shown, sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    except EdgeTaskError as exc:
        print(json.dumps({"error": exc.code.value, "detail": exc.detail}), file=sys.stderr)
        return 1
    except JournalIntegrityError as exc:
        # A rolled-back, torn, or tampered journal: loud, machine-readable, distinct exit.
        print(json.dumps({"error": "journal_integrity", "detail": str(exc)}), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
