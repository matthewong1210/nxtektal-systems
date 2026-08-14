"""Site Runtime composition: rejected input never reaches Shadow Ops."""

from __future__ import annotations

import dataclasses

import pytest

from nxt_agent_runtime import CycleKind, EvaluationJournal, FailurePolicy
from nxt_pilot_ops.ledger import JsonlEventLedger
from nxt_site_runtime.pipeline import FailureCode
from nxt_telemetry.observations import ObservationFrame, ObservationStatus

from .conftest import (
    CapturingSink,
    PilotObservationSource,
    calm_batch,
    evidence_paths,
    make_runtime,
    spike_batch,
)


def _assert_no_policy_artifacts(root):
    paths = evidence_paths(root)
    assert JsonlEventLedger(paths["ledger"]).records() == ()
    assert EvaluationJournal(paths["journal"]).read() == ()


def _reframe(batch, frame):
    return dataclasses.replace(batch, frame=frame)


def _replace_observation(frame, channel, **changes):
    observations = tuple(
        dataclasses.replace(observation, **changes)
        if observation.channel == channel
        else observation
        for observation in frame.observations
    )
    return ObservationFrame(t_s=frame.t_s, observations=observations)


def _run_rejected(tmp_path, base_inputs, batch, *, sink=None):
    source = PilotObservationSource([batch])
    runtime = make_runtime(
        tmp_path, base_inputs, source, **({} if sink is None else {"runtime_sink": sink})
    )
    outcomes = runtime.run(1, failure_policy=FailurePolicy.CONTINUE)
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.kind is CycleKind.REJECTED
    assert outcome.record is None
    _assert_no_policy_artifacts(tmp_path)
    status = runtime.status()
    assert status.degraded
    assert status.last_evaluated_sequence is None
    assert status.evaluation_checkpoint_has_pending is False
    assert status.pending_decision_count == 0
    return outcome, source, runtime


def test_admitted_envelope_reaches_shadow_ops(tmp_path, base_inputs):
    source = PilotObservationSource([calm_batch(base_inputs)])
    runtime = make_runtime(tmp_path, base_inputs, source)
    outcome = runtime.run_once()
    assert outcome.kind is CycleKind.EVALUATED
    record = outcome.record
    assert record is not None
    assert record.policy_id == "ball-availability-guardian"
    assert record.trace_id.startswith("trace_")
    journal = EvaluationJournal(evidence_paths(tmp_path)["journal"])
    assert [item.evaluation_id for item in journal.read()] == [
        record.evaluation_id
    ]


def test_stale_observation_is_rejected_before_policy(tmp_path, base_inputs):
    batch = calm_batch(base_inputs)
    frame = _replace_observation(
        batch.frame,
        "inventory.dispenser.count",
        sample_timestamp_s=batch.frame.t_s - 1200.0,
        available_timestamp_s=batch.frame.t_s - 1200.0,
    )
    outcome, source, _ = _run_rejected(
        tmp_path, base_inputs, _reframe(batch, frame)
    )
    assert outcome.failure.code is FailureCode.STALE_OBSERVATION
    assert source.rejected == [(0, "stale_observation")]


def test_missing_observation_is_rejected_before_policy(tmp_path, base_inputs):
    batch = calm_batch(base_inputs)
    frame = _replace_observation(
        batch.frame,
        "inventory.dispenser.count",
        value=None,
        status=ObservationStatus.MISSING,
    )
    outcome, source, _ = _run_rejected(
        tmp_path, base_inputs, _reframe(batch, frame)
    )
    assert outcome.failure.code is FailureCode.INSUFFICIENT_QUALITY
    assert "missing_inputs" in outcome.failure.detail
    assert source.rejected == [(0, "insufficient_data_quality")]


def test_invalid_observation_is_rejected_before_policy(tmp_path, base_inputs):
    batch = calm_batch(base_inputs)
    frame = _replace_observation(
        batch.frame,
        "inventory.dispenser.count",
        available_timestamp_s=batch.frame.t_s + 60.0,
    )
    outcome, source, _ = _run_rejected(
        tmp_path, base_inputs, _reframe(batch, frame)
    )
    assert outcome.failure.code is FailureCode.INVALID_OBSERVATION
    assert source.rejected == [(0, "invalid_observation")]


def test_low_quality_input_is_rejected_before_policy(tmp_path, base_inputs):
    batch = calm_batch(base_inputs)
    frame = _replace_observation(
        batch.frame, "inventory.dispenser.count", confidence=0.4
    )
    outcome, _, _ = _run_rejected(tmp_path, base_inputs, _reframe(batch, frame))
    assert outcome.failure.code is FailureCode.INSUFFICIENT_QUALITY
    assert "effective_confidence" in outcome.failure.detail


def test_sequence_gap_is_a_retained_recovery_incident(tmp_path, base_inputs):
    source = PilotObservationSource(
        [
            calm_batch(base_inputs),
            spike_batch(base_inputs, sequence_number=5, cycle=1),
        ]
    )
    runtime = make_runtime(tmp_path, base_inputs, source)
    outcomes = runtime.run(2, failure_policy=FailurePolicy.CONTINUE)
    assert outcomes[0].kind is CycleKind.EVALUATED
    assert outcomes[1].kind is CycleKind.REJECTED
    assert outcomes[1].failure.code is FailureCode.INVALID_SEQUENCE
    # The gap frame is retained for remediation, never silently resequenced.
    assert source.rejected == []
    assert len(source.batches) == 1
    journal = EvaluationJournal(evidence_paths(tmp_path)["journal"])
    assert len(journal.read()) == 1


def test_rejected_input_is_visible_through_the_sink(tmp_path, base_inputs):
    batch = calm_batch(base_inputs)
    frame = _replace_observation(
        batch.frame, "inventory.dispenser.count", confidence=0.0
    )
    sink = CapturingSink()
    outcome, _, _ = _run_rejected(
        tmp_path, base_inputs, _reframe(batch, frame), sink=sink
    )
    assert [failure.code for failure in sink.rejected] == [outcome.failure.code]
    assert sink.published == []


def test_publisher_failure_creates_no_evaluation_and_no_ack(
    tmp_path, base_inputs
):
    class FailingPublisher:
        def publish(self, envelope):
            raise RuntimeError("simulated publisher outage")

    source = PilotObservationSource([calm_batch(base_inputs)])
    runtime = make_runtime(
        tmp_path, base_inputs, source, publisher=FailingPublisher()
    )
    outcomes = runtime.run(1)
    assert outcomes[0].kind is CycleKind.REJECTED
    assert outcomes[0].failure.code is FailureCode.PUBLISH_FAILED
    assert outcomes[0].failure.retryable
    assert source.acknowledged == []
    assert len(source.batches) == 1
    _assert_no_policy_artifacts(tmp_path)
    status = runtime.status()
    assert status.site_checkpoint_has_pending_publish
    assert status.evaluation_checkpoint_has_pending is False


def test_site_checkpoint_failure_propagates_without_policy_artifacts(
    tmp_path, base_inputs
):
    class FailingCheckpointStore:
        def load(self, site_id, deployment_id):
            raise RuntimeError("simulated checkpoint outage")

        def compare_and_save(self, expected, updated):
            raise RuntimeError("simulated checkpoint outage")

    source = PilotObservationSource([calm_batch(base_inputs)])
    runtime = make_runtime(
        tmp_path,
        base_inputs,
        source,
        site_checkpoint_store=FailingCheckpointStore(),
    )
    outcomes = runtime.run(1)
    assert outcomes[0].kind is CycleKind.REJECTED
    assert outcomes[0].failure.code is FailureCode.CHECKPOINT_FAILED
    assert source.acknowledged == []
    _assert_no_policy_artifacts(tmp_path)


def test_run_stops_after_rejection_under_the_default_policy(
    tmp_path, base_inputs
):
    batch = calm_batch(base_inputs)
    frame = _replace_observation(
        batch.frame, "inventory.dispenser.count", confidence=0.4
    )
    bad = _reframe(batch, frame)
    good = spike_batch(base_inputs, sequence_number=0, cycle=1)
    source = PilotObservationSource([bad, good])
    runtime = make_runtime(tmp_path, base_inputs, source)
    outcomes = runtime.run(5)
    assert [outcome.kind for outcome in outcomes] == [CycleKind.REJECTED]


def test_rejected_frame_sequence_is_reused_by_the_replacement_frame(
    tmp_path, base_inputs
):
    batch = calm_batch(base_inputs)
    frame = _replace_observation(
        batch.frame, "inventory.dispenser.count", confidence=0.4
    )
    bad = _reframe(batch, frame)
    good = spike_batch(base_inputs, sequence_number=1, cycle=1)
    source = PilotObservationSource([bad, good])
    runtime = make_runtime(tmp_path, base_inputs, source)
    outcomes = runtime.run(3, failure_policy=FailurePolicy.CONTINUE)
    assert [outcome.kind for outcome in outcomes] == [
        CycleKind.REJECTED,
        CycleKind.EVALUATED,
        CycleKind.SOURCE_EXHAUSTED,
    ]
    assert outcomes[1].sequence_number == 0
    assert source.rejected == [(0, "insufficient_data_quality")]
    assert source.acknowledged == [0]
