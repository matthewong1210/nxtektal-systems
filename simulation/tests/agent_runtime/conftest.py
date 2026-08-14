"""Deterministic Pilot Course A fixtures for Agent Runtime V1 tests.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

Frames derive from one synthetic-bank sample of the existing
``normal_weekday`` scenario; evening values are hand-set with explicit
ball-flow conservation so every cycle passes the Site Runtime quality
gate.  All timestamps are scenario time; no fixture reads a wall clock.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

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
from nxt_site_runtime import SourceReference
from nxt_site_runtime.checkpoints import JsonCheckpointStore
from nxt_site_runtime.ports import SequencedObservationFrame

SITE_ID = "pilot-course-a"
DEPLOYMENT_ID = "pilot-a-sim"
PILOT_SEED = 73
SIMULATION_MIDNIGHT = datetime(2026, 8, 8, tzinfo=timezone.utc)

# Evening cycle times (seconds since midnight, scenario clock).
CALM_T_S = 63000.0  # 17:30 — demand covered through the risk horizon
SPIKE_T_S = 66600.0  # 18:30 — evening spike has drained the dispenser

# Hand-set evening forecasts (balls per minute per 30-minute bucket).
CALM_FORECAST = (24.0, 26.0, 28.0, 30.0, 30.0, 30.0)
SPIKE_FORECAST = (34.0, 36.0, 36.0, 34.0, 32.0, 30.0)

CALM_CLEAN_BALLS = 6000
SPIKE_CLEAN_BALLS = 2400


@pytest.fixture(scope="session")
def base_inputs():
    scenario = make_scenario("normal_weekday")
    sim = RangeSimulation(scenario, seed=PILOT_SEED)
    frame = SyntheticSensorBank(sim).sample()
    return {
        "scenario": scenario,
        "frame": frame,
        "site_config": site_config_from_scenario(scenario, seed=PILOT_SEED),
    }


def evening_frame(
    base_frame: ObservationFrame,
    *,
    t_s: float,
    cycle: int,
    clean_balls: int,
    total_balls: int = 8000,
) -> ObservationFrame:
    """Derive one conserved evening frame from the sampled base frame.

    Balls leaving the dispenser are placed on the field in zone Z1 so
    ``clean + on_field`` always equals the site total and the assembler
    reports no consistency issue.
    """
    base_by_channel = base_frame.by_channel()
    base_zone_total = sum(
        int(observation.value)
        for channel, observation in base_by_channel.items()
        if channel.startswith("scan.zone.")
    )
    base_clean = int(base_by_channel["inventory.dispenser.count"].value)
    if base_clean + base_zone_total != total_balls:
        raise AssertionError("base frame does not conserve the ball total")
    extra_on_field = base_clean - clean_balls
    if extra_on_field < 0:
        raise AssertionError("evening frames cannot mint clean balls")
    overrides: dict[str, object] = {
        "inventory.dispenser.count": clean_balls,
        "inventory.dispenser.sensed": float(clean_balls),
        "scan.zone.Z1.balls": (
            int(base_by_channel["scan.zone.Z1.balls"].value) + extra_on_field
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


def pilot_upstream(
    *,
    forecast: tuple[float, ...],
    demand_balls_total: int,
    demand_balls_served: int,
) -> UpstreamInputs:
    return UpstreamInputs(
        forecast_balls_per_minute=forecast,
        demand_balls_total=demand_balls_total,
        demand_balls_served=demand_balls_served,
        stockout_minutes=0.0,
        service_availability=1.0,
    )


def upstream_reference(
    frame: ObservationFrame, sequence_number: int = 0, **changes
) -> SourceReference:
    values = {
        "source_type": "simulation",
        "source_id": "synthetic.upstream",
        "channel": "upstream.site",
        "reference_id": f"upstream.site:o{sequence_number:06d}",
        "sample_timestamp_s": frame.t_s,
        "available_timestamp_s": frame.t_s,
        "confidence": 1.0,
        "status": "ok",
        "calibration_id": "not-applicable",
    }
    values.update(changes)
    return SourceReference(**values)


def make_batch(
    frame: ObservationFrame,
    upstream: UpstreamInputs,
    *,
    sequence_number: int,
    upstream_source_references=None,
) -> SequencedObservationFrame:
    references = (
        upstream_source_references
        if upstream_source_references is not None
        else (upstream_reference(frame, sequence_number),)
    )
    return SequencedObservationFrame(
        sequence_number=sequence_number,
        frame=frame,
        upstream=upstream,
        upstream_source_references=tuple(references),
    )


def calm_batch(base_inputs, *, sequence_number: int = 0, cycle: int = 0):
    """17:30 — inventory covers the whole risk horizon: expect NO_ACTION."""
    frame = evening_frame(
        base_inputs["frame"],
        t_s=CALM_T_S,
        cycle=cycle,
        clean_balls=CALM_CLEAN_BALLS,
    )
    upstream = pilot_upstream(
        forecast=CALM_FORECAST, demand_balls_total=3600, demand_balls_served=3600
    )
    return make_batch(frame, upstream, sequence_number=sequence_number)


def spike_batch(base_inputs, *, sequence_number: int = 1, cycle: int = 1):
    """18:30 — the evening spike projects a stockout: expect escalation."""
    frame = evening_frame(
        base_inputs["frame"],
        t_s=SPIKE_T_S,
        cycle=cycle,
        clean_balls=SPIKE_CLEAN_BALLS,
    )
    upstream = pilot_upstream(
        forecast=SPIKE_FORECAST, demand_balls_total=7200, demand_balls_served=7200
    )
    return make_batch(frame, upstream, sequence_number=sequence_number)


@dataclass
class PilotObservationSource:
    """At-least-once fixture source: peeks until ack or reject."""

    batches: list[SequencedObservationFrame]
    acknowledged: list[int] = field(default_factory=list)
    rejected: list[tuple[int, str]] = field(default_factory=list)

    def observe(self) -> SequencedObservationFrame:
        if not self.batches:
            raise SourceExhausted("pilot fixture source is exhausted")
        return self.batches[0]

    def acknowledge(self, sequence_number: int) -> None:
        if not self.batches or self.batches[0].sequence_number != sequence_number:
            raise RuntimeError("acknowledgement does not match current batch")
        self.acknowledged.append(sequence_number)
        self.batches.pop(0)

    def reject(self, sequence_number: int, reason: str) -> None:
        if not self.batches or self.batches[0].sequence_number != sequence_number:
            raise RuntimeError("rejection does not match current batch")
        self.rejected.append((sequence_number, reason))
        self.batches.pop(0)
        if self.batches:
            self.batches[0] = dataclasses.replace(
                self.batches[0], sequence_number=sequence_number
            )


@dataclass
class CapturingPublisher:
    attempts: list = field(default_factory=list)
    delivered: dict = field(default_factory=dict)

    def publish(self, envelope) -> None:
        self.attempts.append(envelope.envelope_id)
        self.delivered.setdefault(envelope.envelope_id, envelope)

    @property
    def published(self) -> list:
        return list(self.delivered.values())


@dataclass
class CapturingSink:
    published: list = field(default_factory=list)
    rejected: list = field(default_factory=list)

    def on_published(self, envelope) -> None:
        self.published.append(envelope)

    def on_rejected(self, failure) -> None:
        self.rejected.append(failure)


def evidence_paths(root):
    return {
        "snapshots": root / "snapshots.jsonl",
        "ledger": root / "ledger.jsonl",
        "journal": root / "evaluations.jsonl",
        "site_checkpoints": root / "checkpoints" / "site",
        "evaluation_checkpoints": root / "checkpoints" / "evaluation",
    }


def make_runtime(root, base_inputs, source, **overrides) -> AgentRuntime:
    """Compose a runtime against persistent stores under ``root``."""
    paths = evidence_paths(root)
    values = {
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "site_config": base_inputs["site_config"],
        "observation_source": source,
        "publisher": JsonlSnapshotPublisher(paths["snapshots"]),
        "ledger": JsonlEventLedger(paths["ledger"]),
        "journal": EvaluationJournal(paths["journal"]),
        "simulation_midnight": SIMULATION_MIDNIGHT,
        "clean_sensed_valid": True,
        "site_checkpoint_store": JsonCheckpointStore(paths["site_checkpoints"]),
        "evaluation_checkpoint_store": JsonEvaluationCheckpointStore(
            paths["evaluation_checkpoints"]
        ),
    }
    values.update(overrides)
    return AgentRuntime(**values)
