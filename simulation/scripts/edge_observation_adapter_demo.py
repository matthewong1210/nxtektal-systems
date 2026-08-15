"""Edge Observation Adapter Kit V0 demo: Pilot Course A — Edge Intake.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

A bounded, deterministic composition-root demo of the whole synthetic
edge path:

    synthetic raw device samples
      -> nxt_edge_observation adapter conversion
      -> canonical ObservationFrame
      -> SequencedObservationFrame
      -> SiteRuntimePipeline
      -> AgentRuntime

Nothing here connects to a physical device, network, or facility.  There
is no Modbus, serial, MQTT, Kafka, OPC-UA, ROS 2, Nav2, vendor SDK,
camera, socket, or cloud path, and no robot, actuator, motion, charging,
or emergency-stop surface.  Every timestamp is scenario time; the demo
reads no wall clock, and identical invocations produce byte-identical
evidence.

Usage (from simulation/):
    .venv/bin/python scripts/edge_observation_adapter_demo.py \
        --out reports/edge-observation
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_agent_runtime import (  # noqa: E402
    AgentRuntime,
    EvaluationJournal,
    FailurePolicy,
    JsonEvaluationCheckpointStore,
    JsonlSnapshotPublisher,
)
from nxt_pilot_ops.ledger import JsonlEventLedger  # noqa: E402
from nxt_site_runtime.checkpoints import JsonCheckpointStore  # noqa: E402

from scripts.pilot_course_a_edge_fixture import (  # noqa: E402
    DEPLOYMENT_ID,
    DISCLAIMER,
    FACILITY_SYSTEM_CHANNELS,
    PILOT_CYCLES,
    SITE_ID,
    commissioned_site,
    pilot_observation_source,
    site_config,
)

SIMULATION_MIDNIGHT = datetime(2026, 8, 8, tzinfo=timezone.utc)
MAX_CYCLES = 8


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
    )


def _coverage(source, site) -> dict:
    """Which canonical channels the adapter kit covers, and which it does not."""
    adapter_channels = sorted(source.kit.bindings.channels)
    return {
        "adapter_version": source.kit.convert(
            source.feed.peek().batch
        ).report.adapter_version,
        "commissioned_binding_count": len(source.kit.bindings.bindings),
        "adapter_produced_channels": adapter_channels,
        "channels_without_a_v0_adapter_family": sorted(FACILITY_SYSTEM_CHANNELS),
        "coordinate_reference_system": (
            site.spatial_reference.coordinate_system.identifier
        ),
    }


def run_demo(out_dir: Path) -> str:
    """Run the bounded demo, write evidence, and return the printed report."""
    site = commissioned_site()
    config = site_config(site)
    source = pilot_observation_source(site)

    root = out_dir / SITE_ID / DEPLOYMENT_ID
    if root.exists() and any(root.iterdir()):
        raise SystemExit(
            f"refusing to write into a non-empty evidence directory: {root} "
            "(evidence streams are append-only; use a fresh --out directory)"
        )
    root.mkdir(parents=True, exist_ok=True)

    coverage = _coverage(source, site)

    runtime = AgentRuntime(
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_config=config,
        observation_source=source,
        publisher=JsonlSnapshotPublisher(root / "snapshots.jsonl"),
        ledger=JsonlEventLedger(root / "ledger.jsonl"),
        journal=EvaluationJournal(root / "evaluations.jsonl"),
        simulation_midnight=SIMULATION_MIDNIGHT,
        clean_sensed_valid=True,
        site_checkpoint_store=JsonCheckpointStore(root / "checkpoints" / "site"),
        evaluation_checkpoint_store=JsonEvaluationCheckpointStore(
            root / "checkpoints" / "evaluation"
        ),
    )

    outcomes = runtime.run(
        max_cycles=MAX_CYCLES, failure_policy=FailurePolicy.CONTINUE
    )

    labels = {spec.cycle_index: spec.label for spec in PILOT_CYCLES}
    cycles = []
    for index, outcome in enumerate(outcomes):
        report = source.reports.get(index)
        cycles.append(
            {
                "cycle": index,
                "label": labels.get(index, "source exhausted"),
                "kind": outcome.kind.value,
                "sequence_number": outcome.sequence_number,
                "acknowledged": outcome.acknowledged,
                "envelope_id": outcome.envelope_id,
                "evaluation_id": outcome.evaluation_id,
                "verdict": (
                    None
                    if outcome.record is None
                    else outcome.record.to_payload().get("verdict")
                ),
                "recommendation_action": (
                    None
                    if outcome.record is None
                    else outcome.record.to_payload().get("recommendation_action")
                ),
                "site_runtime_failure": (
                    None
                    if outcome.failure is None
                    else {
                        "code": outcome.failure.code.value,
                        "stage": outcome.failure.stage.value,
                        "detail": outcome.failure.detail,
                    }
                ),
                "adapter_report": None if report is None else report.to_dict(),
            }
        )

    status = runtime.status().to_dict()
    pending = [
        {
            "recommendation_id": item.recommendation_id,
            "action": item.action.value,
            "case_status": item.case_status.value,
            "source_sequence": item.source_sequence,
        }
        for item in runtime.queue.pending()
    ]

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
                "fixture-backed synthetic samples only; the adapter kit reads "
                "already-decoded payloads and can neither write a register nor "
                "command a robot"
            ),
        },
        "coverage": coverage,
        "cycles": cycles,
        "pending_manager_decisions": pending,
        "runtime_status": status,
    }

    (root / "edge_observation_demo.json").write_text(
        _canonical_json(evidence) + "\n", encoding="utf-8"
    )

    lines = [DISCLAIMER, ""]
    lines.append(
        f"Pilot Course A — Edge Observation Intake  ({SITE_ID}/{DEPLOYMENT_ID})"
    )
    lines.append(
        f"  adapter version .......... {coverage['adapter_version']}"
    )
    lines.append(
        f"  commissioned bindings .... {coverage['commissioned_binding_count']}"
    )
    lines.append(
        "  channels without a V0 adapter family: "
        + ", ".join(coverage["channels_without_a_v0_adapter_family"])
    )
    lines.append("")
    for cycle in cycles:
        report = cycle["adapter_report"]
        lines.append(f"cycle {cycle['cycle']} — {cycle['label']}")
        if report is None:
            lines.append("  adapter: no batch (feed exhausted)")
        else:
            stale = sum(
                1 for item in report["accepted"] if item["status"] == "stale"
            )
            lines.append(
                f"  adapter: {len(report['accepted'])} accepted "
                f"({stale} stale), {len(report['rejected'])} rejected, "
                f"{len(report['unmapped'])} unmapped"
            )
            for item in report["rejected"]:
                lines.append(
                    f"    rejected {item['raw_field']} "
                    f"({item['sensor_id']}): {item['code']}"
                )
        failure = cycle["site_runtime_failure"]
        if cycle["kind"] == "source_exhausted":
            lines.append("  site runtime: not invoked (no batch)")
        elif failure is None:
            lines.append(
                f"  site runtime: admitted seq={cycle['sequence_number']}"
            )
        else:
            lines.append(
                f"  site runtime: rejected seq={cycle['sequence_number']} "
                f"({failure['code']})"
            )
        verdict_text = ""
        if cycle["verdict"] is not None:
            action = cycle["recommendation_action"] or "none"
            verdict_text = f' verdict="{cycle["verdict"]}" action="{action}"'
        lines.append(f"  agent runtime: {cycle['kind']}{verdict_text}")
        lines.append("")
    lines.append("runtime status: " + _canonical_json(status))
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic Pilot Course A edge-observation adapter demo "
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
