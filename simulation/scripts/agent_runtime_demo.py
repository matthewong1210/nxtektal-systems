"""Agent Runtime V1 demonstration: Pilot Course A — Evening Demand Spike.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

A bounded, deterministic composition-root demo of the full loop:
observation frames -> Site Runtime publication -> Shadow Ops evaluation
-> pending manager decision -> scripted human response -> restart
recovery -> final health/status.  Every timestamp is scenario time; the
demo reads no wall clock, and identical invocations produce byte-identical
evidence artifacts.

The native FacilityState deployment path deliberately lacks collector
capability, ETA, expected yield, collection permission, current demand,
and live washer availability, so the projected evening stockout escalates
to OPERATOR_INTERVENTION instead of fabricating a dispatch.

Usage (from simulation/):
    .venv/bin/python scripts/agent_runtime_demo.py --out reports/agent_runtime
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario
from nxt_telemetry.bank import SyntheticSensorBank, site_config_from_scenario
from nxt_telemetry.observations import ObservationFrame, UpstreamInputs

from nxt_agent_runtime import (
    AgentRuntime,
    EvaluationJournal,
    JsonEvaluationCheckpointStore,
    JsonlSnapshotPublisher,
    SourceExhausted,
)
from nxt_pilot_ops.ledger import JsonlEventLedger
from nxt_pilot_ops.workflow import CaseStatus
from nxt_site_runtime import SourceReference
from nxt_site_runtime.checkpoints import JsonCheckpointStore
from nxt_site_runtime.ports import SequencedObservationFrame

DISCLAIMER = "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA"
SITE_ID = "pilot-course-a"
DEPLOYMENT_ID = "pilot-a-sim"
SIMULATION_MIDNIGHT = datetime(2026, 8, 8, tzinfo=timezone.utc)

# Evening storyline (scenario clock, seconds since midnight).
CALM_T_S = 63000.0  # 17:30 — demand covered through the risk horizon
SPIKE_T_S = 66600.0  # 18:30 — the spike has drained the dispenser
CALM_FORECAST = (24.0, 26.0, 28.0, 30.0, 30.0, 30.0)
SPIKE_FORECAST = (34.0, 36.0, 36.0, 34.0, 32.0, 30.0)
CALM_CLEAN_BALLS = 6000
SPIKE_CLEAN_BALLS = 2400
MANAGER_RESPONDED_AT = datetime(2026, 8, 8, 19, 0, tzinfo=timezone.utc)


def evening_frame(
    base_frame: ObservationFrame, *, t_s: float, cycle: int, clean_balls: int
) -> ObservationFrame:
    """Derive one conserved evening frame from the sampled base frame."""
    by_channel = base_frame.by_channel()
    base_clean = int(by_channel["inventory.dispenser.count"].value)
    extra_on_field = base_clean - clean_balls
    if extra_on_field < 0:
        raise SystemExit("evening frames cannot mint clean balls")
    overrides: dict[str, object] = {
        "inventory.dispenser.count": clean_balls,
        "inventory.dispenser.sensed": float(clean_balls),
        "scan.zone.Z1.balls": (
            int(by_channel["scan.zone.Z1.balls"].value) + extra_on_field
        ),
    }
    observations = tuple(
        dataclasses.replace(
            observation,
            value=overrides.get(observation.channel, observation.value),
            sample_timestamp_s=t_s,
            available_timestamp_s=t_s,
            seq=cycle,
        )
        for observation in base_frame.observations
    )
    return ObservationFrame(t_s=t_s, observations=observations)


def pilot_batch(
    frame: ObservationFrame,
    forecast: tuple[float, ...],
    *,
    sequence_number: int,
    demand_balls_total: int,
    demand_balls_served: int,
) -> SequencedObservationFrame:
    upstream = UpstreamInputs(
        forecast_balls_per_minute=forecast,
        demand_balls_total=demand_balls_total,
        demand_balls_served=demand_balls_served,
        stockout_minutes=0.0,
        service_availability=1.0,
    )
    reference = SourceReference(
        source_type="simulation",
        source_id="synthetic.upstream",
        channel="upstream.site",
        reference_id=f"upstream.site:o{sequence_number:06d}",
        sample_timestamp_s=frame.t_s,
        available_timestamp_s=frame.t_s,
        confidence=1.0,
        status="ok",
        calibration_id="not-applicable",
    )
    return SequencedObservationFrame(
        sequence_number=sequence_number,
        frame=frame,
        upstream=upstream,
        upstream_source_references=(reference,),
    )


class PilotObservationSource:
    """At-least-once fixture source: peeks until ack or reject."""

    def __init__(self, batches):
        self.batches = list(batches)

    def observe(self):
        if not self.batches:
            raise SourceExhausted("pilot fixture source is exhausted")
        return self.batches[0]

    def acknowledge(self, sequence_number):
        assert self.batches and (
            self.batches[0].sequence_number == sequence_number
        )
        self.batches.pop(0)

    def reject(self, sequence_number, reason):
        raise SystemExit(
            f"demo frame {sequence_number} was rejected ({reason}); "
            "the pilot fixture is expected to pass the quality gate"
        )


class CrashBeforeAcknowledgement(PilotObservationSource):
    """Simulate a process crash after evaluation, before acknowledgement."""

    def __init__(self, batches, *, crash_on_sequence):
        super().__init__(batches)
        self.crash_on_sequence = crash_on_sequence

    def acknowledge(self, sequence_number):
        if sequence_number == self.crash_on_sequence:
            raise RuntimeError(
                "simulated power loss before source acknowledgement"
            )
        super().acknowledge(sequence_number)


def build_batches(seed: int):
    scenario = make_scenario("normal_weekday")
    sim = RangeSimulation(scenario, seed=seed)
    base_frame = SyntheticSensorBank(sim).sample()
    site_config = site_config_from_scenario(scenario, seed=seed)
    calm = pilot_batch(
        evening_frame(
            base_frame, t_s=CALM_T_S, cycle=0, clean_balls=CALM_CLEAN_BALLS
        ),
        CALM_FORECAST,
        sequence_number=0,
        demand_balls_total=3600,
        demand_balls_served=3600,
    )
    spike = pilot_batch(
        evening_frame(
            base_frame, t_s=SPIKE_T_S, cycle=1, clean_balls=SPIKE_CLEAN_BALLS
        ),
        SPIKE_FORECAST,
        sequence_number=1,
        demand_balls_total=7200,
        demand_balls_served=7200,
    )
    return site_config, [calm, spike]


def make_runtime(root: Path, site_config, source) -> AgentRuntime:
    return AgentRuntime(
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_config=site_config,
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


def show(title: str, payload) -> None:
    print(f"\n== {title}")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def outcome_payload(outcome) -> dict:
    return {
        "kind": outcome.kind.value,
        "sequence_number": outcome.sequence_number,
        "envelope_id": outcome.envelope_id,
        "evaluation_id": outcome.evaluation_id,
        "verdict": None if outcome.record is None else outcome.record.verdict.value,
        "recommendation_action": (
            None
            if outcome.record is None or outcome.record.recommendation_action is None
            else outcome.record.recommendation_action.value
        ),
        "acknowledged": outcome.acknowledged,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="reports/agent_runtime",
        help="output root for evidence artifacts (default: %(default)s)",
    )
    parser.add_argument(
        "--seed", type=int, default=73, help="scenario seed (default: 73)"
    )
    arguments = parser.parse_args(argv)

    root = Path(arguments.out) / SITE_ID / DEPLOYMENT_ID
    if (root / "evaluations.jsonl").exists():
        raise SystemExit(
            f"{root} already contains a run; evidence is append-only — "
            "choose a fresh --out or remove the previous demo output"
        )
    root.mkdir(parents=True, exist_ok=True)

    print(DISCLAIMER)
    print(f"Pilot Course A — Evening Demand Spike (seed {arguments.seed})")
    site_config, batches = build_batches(arguments.seed)

    # --- Life 1: two evening cycles; the process "crashes" before the
    # second acknowledgement, after evaluation evidence was persisted.
    life1_source = CrashBeforeAcknowledgement(
        list(batches), crash_on_sequence=1
    )
    calm_runtime = make_runtime(root, site_config, life1_source)
    first = calm_runtime.run_once()
    show("cycle 1 (17:30) — calm evening", outcome_payload(first))

    second = calm_runtime.run_once()
    show(
        "cycle 2 (18:30) — spike escalation, crash before acknowledgement",
        outcome_payload(second),
    )
    show("status before restart", calm_runtime.status().to_dict())

    # --- Life 2: restart.  The at-least-once source redelivers the
    # un-acknowledged frame; recovery completes it idempotently.
    life2_source = PilotObservationSource([batches[1]])
    restarted = make_runtime(root, site_config, life2_source)
    report = restarted.recover()
    show("recovery report after restart", dataclasses.asdict(report))
    replay = restarted.run(2)
    show(
        "restart cycles — idempotent completion",
        [outcome_payload(outcome) for outcome in replay],
    )

    # --- Manager decision: scripted deterministic human response.
    queue = restarted.queue
    pending = queue.pending()
    show(
        "pending manager decisions",
        [
            {
                "recommendation_id": entry.recommendation_id,
                "action": entry.action.value,
                "issued_at": entry.issued_at,
                "execute_before": entry.execute_before,
                "source_envelope_id": entry.source_envelope_id,
                "trace_id": entry.trace_id,
                "summary": entry.summary,
            }
            for entry in pending
        ],
    )
    for entry in pending:
        if entry.case_status is CaseStatus.PENDING:
            queue.accept(
                entry.recommendation_id,
                operator_id="scripted-demo-manager",
                responded_at=MANAGER_RESPONDED_AT,
                reason_code="staffing_confirmed",
                note=(
                    "Scripted demo response; acceptance is a human workflow "
                    "record only and commands no robot."
                ),
            )
            print(
                f"manager accepted {entry.recommendation_id} at "
                f"{MANAGER_RESPONDED_AT.isoformat()} (workflow record only)"
            )

    show("final health/status", restarted.status().to_dict())
    artifacts = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )
    show("evidence artifacts (append-only, deterministic)", artifacts)
    print(f"\n{DISCLAIMER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
