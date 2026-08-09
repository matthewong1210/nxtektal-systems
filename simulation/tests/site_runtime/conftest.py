"""Shared real-contract fixtures and replayable test adapters."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import pytest

from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario
from nxt_telemetry.bank import (
    SyntheticSensorBank,
    site_config_from_scenario,
    upstream_from_sim,
)

from nxt_site_runtime import SourceReference
from nxt_site_runtime.ports import SequencedObservationFrame
from tests.commissioning.conftest import (
    commissioned_site,
    commissioned_site_payload,
)


@dataclass
class ReplayableObservationSource:
    """Peeks until ack/reject, matching the production source contract."""

    batches: list[SequencedObservationFrame]
    acknowledged: list[int] = field(default_factory=list)
    rejected: list[tuple[int, str]] = field(default_factory=list)

    def observe(self) -> SequencedObservationFrame:
        if not self.batches:
            raise RuntimeError("source exhausted")
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


def upstream_reference(frame, sequence_number: int = 0, **changes) -> SourceReference:
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
    runtime_inputs,
    *,
    sequence_number: int = 0,
    frame=None,
    upstream=None,
    upstream_source_references=None,
) -> SequencedObservationFrame:
    actual_frame = frame if frame is not None else runtime_inputs["frame"]
    references = (
        upstream_source_references
        if upstream_source_references is not None
        else (upstream_reference(actual_frame, sequence_number),)
    )
    return SequencedObservationFrame(
        sequence_number=sequence_number,
        frame=actual_frame,
        upstream=upstream if upstream is not None else runtime_inputs["upstream"],
        upstream_source_references=tuple(references),
    )


@pytest.fixture
def runtime_inputs():
    scenario = make_scenario("normal_weekday")
    sim = RangeSimulation(scenario, seed=73)
    frame = SyntheticSensorBank(sim).sample()
    return {
        "scenario": scenario,
        "sim": sim,
        "frame": frame,
        "site_config": site_config_from_scenario(scenario, seed=73),
        "upstream": upstream_from_sim(sim),
    }
