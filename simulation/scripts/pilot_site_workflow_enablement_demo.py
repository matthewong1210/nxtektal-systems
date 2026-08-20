"""Pilot Site Workflow Enablement V0 demo: Pilot Course A — Multi-Workflow Enablement.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

A bounded, deterministic composition-root demo of shared-site
commissioning plus independent per-workflow readiness:

    candidate commissioned manifest
      -> shared-site gates (commissioning validation, identity, digest)
      -> independent readiness per registered workflow
      -> deterministic enablement report (NOT_READY, then corrected READY)
      -> fixture-only Shadow Mode launch plan for Range Operations only
      -> existing Site Runtime + Agent Runtime composition, bounded run
      -> restart recomposition with stable identities

Grounds Condition Intelligence and Player Caddy Experience are
registered and evaluated but remain NOT_READY with their exact missing
prerequisites; no runtime, state, evaluation, or evidence is produced
for them.  Nothing here connects to a physical device, network, or
facility.  There is no Modbus, serial, MQTT, Kafka, OPC-UA, ROS 2,
Nav2, vendor SDK, camera, socket, or cloud path, and no robot,
actuator, motion, charging, or emergency-stop surface.  Every timestamp
is scenario time; the demo reads no wall clock, and identical
invocations produce byte-identical evidence.

Usage (from simulation/):
    .venv/bin/python scripts/pilot_site_workflow_enablement_demo.py \
        --out reports/workflow-enablement
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_workflow_enablement import (  # noqa: E402
    RANGE_OPS_WORKFLOW_ID,
    ReadinessVerdict,
    WorkflowEnablementError,
    canonical_report_json,
    plan_range_ops_launch,
)

from scripts.pilot_course_a_enablement_fixture import (  # noqa: E402
    BROKEN_CHANNEL,
    DEPLOYMENT_ID,
    DISCLAIMER,
    SITE_ID,
    assemble_range_ops_runtime,
    broken_manifest_payload,
    enablement_context,
    enablement_manifest_payload,
    enablement_site,
    evaluate_enablement,
    range_ops_evidence,
    runtime_evidence_root_is_empty,
)

NOT_READY_REPORT_NAME = "workflow_enablement_report.not_ready.json"
READY_REPORT_NAME = "workflow_enablement_report.ready.json"
DEMO_EVIDENCE_NAME = "pilot_site_workflow_enablement.json"


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
    )


def _readiness_lines(report_payload: dict) -> list[str]:
    lines = []
    for workflow_id, section in sorted(report_payload["workflows"].items()):
        lines.append(f"  {workflow_id}: {section['verdict']}")
        for label in ("missing", "unsupported_in_v0", "deferred"):
            if section[label]:
                lines.append(
                    f"    {label}: " + ", ".join(section[label])
                )
    return lines


def _outcome_payload(outcome) -> dict:
    return {
        "kind": outcome.kind.value,
        "sequence_number": outcome.sequence_number,
        "envelope_id": outcome.envelope_id,
        "evaluation_id": outcome.evaluation_id,
        "verdict": (
            None if outcome.record is None else outcome.record.verdict.value
        ),
        "recommendation_action": (
            None
            if outcome.record is None
            or outcome.record.recommendation_action is None
            else outcome.record.recommendation_action.value
        ),
        "acknowledged": outcome.acknowledged,
    }


def run_demo(out_dir: Path) -> str:
    """Run the bounded demo, write evidence, and return the printed report."""
    root = out_dir / SITE_ID / DEPLOYMENT_ID
    if root.exists() and any(root.iterdir()):
        raise SystemExit(
            f"refusing to write into a non-empty evidence directory: {root} "
            "(evidence streams are append-only; use a fresh --out directory)"
        )
    root.mkdir(parents=True, exist_ok=True)

    lines = [DISCLAIMER, ""]
    lines.append(
        "Pilot Course A — Multi-Workflow Enablement  "
        f"({SITE_ID}/{DEPLOYMENT_ID})"
    )
    lines.append("")

    # --- Step 1: an invalid Range Operations configuration.  The
    # candidate manifest omits one commissioned dispenser binding, so
    # the shared site stays valid while Range Operations fails closed.
    broken_payload = broken_manifest_payload()
    # root_is_empty is declared evidence: prove it against the real
    # filesystem before declaring it.
    runtime_root = root / RANGE_OPS_WORKFLOW_ID
    root_is_empty = runtime_evidence_root_is_empty(runtime_root)
    broken_evaluation, broken_report = evaluate_enablement(
        broken_payload, root_is_empty=root_is_empty
    )
    broken_payload_dict = broken_report.to_payload()
    (root / NOT_READY_REPORT_NAME).write_text(
        canonical_report_json(broken_report), encoding="utf-8"
    )
    lines.append(
        f"step 1 — candidate manifest without {BROKEN_CHANNEL!r}:"
    )
    lines.extend(_readiness_lines(broken_payload_dict))

    broken_range_ops = next(
        item
        for item in broken_evaluation.workflows
        if item.workflow_id == RANGE_OPS_WORKFLOW_ID
    )
    try:
        plan_range_ops_launch(
            readiness=broken_range_ops,
            shared=broken_evaluation.shared,
            context=enablement_context(),
            evidence=range_ops_evidence(broken_payload),
        )
    except WorkflowEnablementError as exc:
        launch_refusal = str(exc)
    else:  # pragma: no cover - the planner must fail closed
        raise SystemExit(
            "a NOT_READY workflow must never yield a launch plan"
        )
    lines.append(f"  launch plan: refused ({launch_refusal})")
    lines.append("")

    # --- Step 2: the corrected shared manifest.
    payload = enablement_manifest_payload()
    evaluation, report = evaluate_enablement(
        payload, root_is_empty=runtime_evidence_root_is_empty(runtime_root)
    )
    report_payload = report.to_payload()
    (root / READY_REPORT_NAME).write_text(
        canonical_report_json(report), encoding="utf-8"
    )
    lines.append("step 2 — corrected shared manifest:")
    lines.extend(_readiness_lines(report_payload))
    lines.append("")

    range_ops = next(
        item
        for item in evaluation.workflows
        if item.workflow_id == RANGE_OPS_WORKFLOW_ID
    )
    if range_ops.verdict is not ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE:
        raise SystemExit(
            "the corrected pilot fixture must be READY_FOR_FIXTURE_SHADOW_MODE"
        )
    plan = plan_range_ops_launch(
        readiness=range_ops,
        shared=evaluation.shared,
        context=enablement_context(),
        evidence=range_ops_evidence(payload),
    )

    # --- Step 3: assemble and run the existing fixture-backed loop for
    # the one READY workflow only.
    site = enablement_site(payload)
    runtime = assemble_range_ops_runtime(plan, site, runtime_root)
    outcomes = runtime.run(max_cycles=plan.max_cycles)
    life1_status = runtime.status().to_dict()
    pending = [
        {
            "recommendation_id": entry.recommendation_id,
            "action": entry.action.value,
            "case_status": entry.case_status.value,
            "source_sequence": entry.source_sequence,
            "summary": entry.summary,
        }
        for entry in runtime.queue.pending()
    ]
    lines.append("step 3 — bounded Shadow Mode run (Range Operations only):")
    for index, outcome in enumerate(outcomes):
        payload_line = _outcome_payload(outcome)
        verdict_text = (
            ""
            if payload_line["verdict"] is None
            else (
                f' verdict="{payload_line["verdict"]}" '
                f'action="{payload_line["recommendation_action"] or "none"}"'
            )
        )
        lines.append(
            f"  cycle {index}: {payload_line['kind']}"
            f" seq={payload_line['sequence_number']}{verdict_text}"
        )
    lines.append(f"  pending manager decisions: {len(pending)}")
    lines.append("")

    # --- Step 4: restart recomposition with stable identities.  The
    # in-memory fixture feed resumes explicitly, exactly like a real
    # at-least-once reader must.
    # A cycle is consumed only when the at-least-once feed advanced past
    # it: an acknowledged evaluation (or idempotent replay skip).  A
    # retained or deferred frame still sits in the feed, so counting bare
    # sequence numbers would resume past an undelivered cycle.  A general
    # resume must read the feed cursor itself; the bounded storyline here
    # acknowledges every delivered cycle.
    completed_cycles = sum(
        1
        for outcome in outcomes
        if outcome.kind.value in ("evaluated", "replay_skipped")
        and outcome.acknowledged
    )
    last_published = life1_status["last_published_sequence"]
    if last_published is None:
        raise SystemExit(
            "restart recomposition requires a published sequence; the site "
            "checkpoint reported none after the bounded Shadow Mode run"
        )
    next_sequence = last_published + 1
    restarted = assemble_range_ops_runtime(
        plan,
        site,
        runtime_root,
        consumed_cycles=completed_cycles,
        first_sequence_number=next_sequence,
    )
    recovery = dataclasses.asdict(restarted.recover())
    restart_outcomes = restarted.run(max_cycles=1)
    restart_status = restarted.status().to_dict()
    lines.append("step 4 — restart recomposition:")
    lines.append(
        "  recovery: journal_records="
        f"{recovery['journal_record_count']} "
        f"last_published_sequence={recovery['last_published_sequence']} "
        f"pending_decisions={recovery['pending_decision_count']}"
    )
    lines.append(
        "  restart cycle outcome: "
        + ", ".join(outcome.kind.value for outcome in restart_outcomes)
    )
    lines.append("")

    artifacts = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )
    workflow_evidence_dirs = sorted(
        str(path.relative_to(root))
        for path in root.iterdir()
        if path.is_dir()
    )

    evidence = {
        "disclaimer": DISCLAIMER,
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "physical_boundary": {
            "live_transport": False,
            "physical_device_connection": False,
            "robot_command_surface": False,
            "actuator_or_estop_control": False,
            "network_call": False,
            "note": (
                "fixture-backed synthetic samples only; readiness gating "
                "and Shadow Mode evaluation can neither reach a device nor "
                "command a robot"
            ),
        },
        "reports": {
            "not_ready": {
                "file": NOT_READY_REPORT_NAME,
                "report_id": broken_payload_dict["report_id"],
                "manifest_digest": broken_payload_dict["manifest_digest"],
                "workflow_verdicts": {
                    workflow_id: section["verdict"]
                    for workflow_id, section in broken_payload_dict[
                        "workflows"
                    ].items()
                },
                "launch_plan_refusal": launch_refusal,
            },
            "ready": {
                "file": READY_REPORT_NAME,
                "report_id": report_payload["report_id"],
                "manifest_digest": report_payload["manifest_digest"],
                "workflow_verdicts": {
                    workflow_id: section["verdict"]
                    for workflow_id, section in report_payload[
                        "workflows"
                    ].items()
                },
            },
        },
        "launch_plan": dataclasses.asdict(plan),
        "shadow_run": {
            "cycles": [_outcome_payload(outcome) for outcome in outcomes],
            "pending_manager_decisions": pending,
            "runtime_status": life1_status,
        },
        "restart": {
            "recovery_report": recovery,
            "cycles": [
                _outcome_payload(outcome) for outcome in restart_outcomes
            ],
            "runtime_status": restart_status,
        },
        "workflow_evidence_directories": workflow_evidence_dirs,
        "artifacts": artifacts,
    }
    (root / DEMO_EVIDENCE_NAME).write_text(
        _canonical_json(evidence) + "\n", encoding="utf-8"
    )

    lines.append(
        "workflow evidence directories: "
        + ", ".join(workflow_evidence_dirs)
    )
    lines.append("evidence artifacts:")
    lines.extend(
        f"  {name}" for name in sorted([*artifacts, DEMO_EVIDENCE_NAME])
    )
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic Pilot Course A multi-workflow enablement demo "
            "(synthetic fixtures only)"
        )
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="output directory for deterministic JSON evidence",
    )
    args = parser.parse_args(argv)
    print(run_demo(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
