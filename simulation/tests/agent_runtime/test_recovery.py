"""Cross-layer checkpoint recovery: the five restart cases."""

from __future__ import annotations

import dataclasses

import pytest

from nxt_agent_runtime import (
    AgentRuntimeError,
    CycleKind,
    EvaluationCheckpoint,
    EvaluationJournal,
    JsonEvaluationCheckpointStore,
    JsonlSnapshotPublisher,
    RuntimeState,
)
from nxt_pilot_ops.ledger import JsonlEventLedger
from nxt_site_runtime import SiteRuntimePipeline
from nxt_site_runtime.checkpoints import JsonCheckpointStore
from nxt_site_runtime.pipeline import FailureCode
from nxt_telemetry.observations import ObservationFrame

from .conftest import (
    DEPLOYMENT_ID,
    SITE_ID,
    PilotObservationSource,
    calm_batch,
    evidence_paths,
    make_runtime,
    spike_batch,
)


def _journal(root) -> EvaluationJournal:
    return EvaluationJournal(evidence_paths(root)["journal"])


def _ledger(root) -> JsonlEventLedger:
    return JsonlEventLedger(evidence_paths(root)["ledger"])


def _reference_run(tmp_path, base_inputs, batches):
    """A crash-free run used as the deterministic expectation baseline."""
    root = tmp_path / "reference"
    runtime = make_runtime(
        root, base_inputs, PilotObservationSource(list(batches))
    )
    outcomes = runtime.run(len(batches) + 1)
    return root, outcomes


def test_case1_published_but_unevaluated_state_is_evaluated_once_on_restart(
    tmp_path, base_inputs
):
    batch = calm_batch(base_inputs)
    reference_root, _ = _reference_run(tmp_path, base_inputs, [batch])
    root = tmp_path / "site"
    paths = evidence_paths(root)

    # Previous process life: Site Runtime published the envelope, then the
    # process crashed before the evaluation started.  The at-least-once
    # transport redelivers the frame because its acknowledgement was lost.
    crashed_source = PilotObservationSource([batch])
    pipeline = SiteRuntimePipeline(
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_config=base_inputs["site_config"],
        publisher=JsonlSnapshotPublisher(paths["snapshots"]),
        observation_source=crashed_source,
        checkpoint_store=JsonCheckpointStore(paths["site_checkpoints"]),
    )
    envelope = pipeline.run_once()
    snapshots_after_publish = paths["snapshots"].read_bytes()
    assert _journal(root).read() == ()

    restarted = make_runtime(
        root, base_inputs, PilotObservationSource([batch])
    )
    report = restarted.recover()
    assert report.last_published_sequence == 0
    assert report.last_evaluated_sequence is None
    outcomes = restarted.run(2)
    assert [outcome.kind for outcome in outcomes] == [
        CycleKind.EVALUATED,
        CycleKind.SOURCE_EXHAUSTED,
    ]
    assert outcomes[0].envelope_id == envelope.envelope_id
    # Evaluated exactly once, and the state was not republished.
    assert paths["snapshots"].read_bytes() == snapshots_after_publish
    assert _journal(root).read() == _journal(reference_root).read()


def test_case2_prepared_evaluation_recomputes_identically_after_crash(
    tmp_path, base_inputs
):
    batch = calm_batch(base_inputs)
    reference_root, _ = _reference_run(tmp_path, base_inputs, [batch])
    root = tmp_path / "site"
    paths = evidence_paths(root)

    class CrashingJournal(EvaluationJournal):
        def append(self, record):
            raise RuntimeError("simulated crash before journal persistence")

    crashed = make_runtime(
        root,
        base_inputs,
        PilotObservationSource([batch]),
        journal=CrashingJournal(paths["journal"]),
    )
    outcome = crashed.run_once()
    assert outcome.kind is CycleKind.EVALUATION_DEFERRED
    assert not outcome.acknowledged
    assert crashed.status().last_failure_code == "journal_unavailable"
    store = JsonEvaluationCheckpointStore(paths["evaluation_checkpoints"])
    pending = store.load(SITE_ID, DEPLOYMENT_ID)
    assert pending.has_pending_evaluation
    assert _journal(root).read() == ()

    restarted = make_runtime(root, base_inputs, PilotObservationSource([batch]))
    report = restarted.recover()
    assert report.evaluation_has_pending
    assert report.pending_evaluation_sequence == 0
    outcomes = restarted.run(2)
    assert outcomes[0].kind is CycleKind.EVALUATED
    completed = store.load(SITE_ID, DEPLOYMENT_ID)
    assert not completed.has_pending_evaluation
    assert completed.last_evaluation_id == pending.pending_evaluation_id
    assert completed.last_recovery_attempts == 1
    assert _journal(root).read() == _journal(reference_root).read()


def test_case2_recommend_path_does_not_duplicate_the_ledger_event(
    tmp_path, base_inputs
):
    batch = spike_batch(base_inputs, sequence_number=0, cycle=0)
    reference_root, _ = _reference_run(tmp_path, base_inputs, [batch])
    root = tmp_path / "site"
    paths = evidence_paths(root)

    class CrashingJournal(EvaluationJournal):
        def append(self, record):
            raise RuntimeError("simulated crash after ledger persistence")

    crashed = make_runtime(
        root,
        base_inputs,
        PilotObservationSource([batch]),
        journal=CrashingJournal(paths["journal"]),
    )
    outcome = crashed.run_once()
    assert outcome.kind is CycleKind.EVALUATION_DEFERRED
    assert len(_ledger(root).records()) == 1

    restarted = make_runtime(root, base_inputs, PilotObservationSource([batch]))
    outcomes = restarted.run(2)
    assert outcomes[0].kind is CycleKind.EVALUATED
    assert len(_ledger(root).records()) == 1
    assert _ledger(root).records() == _ledger(reference_root).records()
    assert _journal(root).read() == _journal(reference_root).read()
    assert len(restarted.queue.pending()) == 1


def test_case3_persisted_evaluation_completes_without_duplication(
    tmp_path, base_inputs
):
    batch = calm_batch(base_inputs)
    reference_root, _ = _reference_run(tmp_path, base_inputs, [batch])
    root = tmp_path / "site"
    paths = evidence_paths(root)

    class CrashBeforeCompletionStore(JsonEvaluationCheckpointStore):
        def compare_and_save(self, expected, updated):
            if expected.has_pending_evaluation and not updated.has_pending_evaluation:
                raise RuntimeError("simulated crash before checkpoint completion")
            super().compare_and_save(expected, updated)

    crashed = make_runtime(
        root,
        base_inputs,
        PilotObservationSource([batch]),
        evaluation_checkpoint_store=CrashBeforeCompletionStore(
            paths["evaluation_checkpoints"]
        ),
    )
    outcome = crashed.run_once()
    assert outcome.kind is CycleKind.EVALUATION_DEFERRED
    assert (
        crashed.status().last_failure_code
        == "evaluation_checkpoint_unavailable"
    )
    assert len(_journal(root).read()) == 1

    restarted = make_runtime(root, base_inputs, PilotObservationSource([batch]))
    outcomes = restarted.run(2)
    assert outcomes[0].kind is CycleKind.EVALUATED
    store = JsonEvaluationCheckpointStore(paths["evaluation_checkpoints"])
    completed = store.load(SITE_ID, DEPLOYMENT_ID)
    assert not completed.has_pending_evaluation
    assert completed.last_sequence == 0
    assert _journal(root).read() == _journal(reference_root).read()


def test_case4_identical_replay_is_idempotent(tmp_path, base_inputs):
    batch = spike_batch(base_inputs, sequence_number=0, cycle=0)
    root = tmp_path / "site"
    paths = evidence_paths(root)
    first = make_runtime(root, base_inputs, PilotObservationSource([batch]))
    first.run(2)
    journal_bytes = paths["journal"].read_bytes()
    ledger_bytes = paths["ledger"].read_bytes()
    snapshot_bytes = paths["snapshots"].read_bytes()
    assert len(first.queue.pending()) == 1

    replay_source = PilotObservationSource([batch])
    replayed = make_runtime(root, base_inputs, replay_source)
    outcomes = replayed.run(2)
    assert [outcome.kind for outcome in outcomes] == [
        CycleKind.REPLAY_SKIPPED,
        CycleKind.SOURCE_EXHAUSTED,
    ]
    assert outcomes[0].evaluation_id is not None
    assert replay_source.acknowledged == [0]
    assert paths["journal"].read_bytes() == journal_bytes
    assert paths["ledger"].read_bytes() == ledger_bytes
    assert paths["snapshots"].read_bytes() == snapshot_bytes
    # No duplicate evaluation and no duplicate pending decision.
    assert len(_journal(root).read()) == 1
    assert len(replayed.queue.pending()) == 1


def test_case5_divergent_replay_fails_closed_at_the_site_layer(
    tmp_path, base_inputs
):
    batch = calm_batch(base_inputs)
    root = tmp_path / "site"
    first = make_runtime(root, base_inputs, PilotObservationSource([batch]))
    first.run(2)
    journal_before = _journal(root).read()

    tampered_frame = ObservationFrame(
        t_s=batch.frame.t_s,
        observations=tuple(
            dataclasses.replace(observation, value=5999)
            if observation.channel == "inventory.dispenser.count"
            else observation
            for observation in batch.frame.observations
        ),
    )
    tampered = dataclasses.replace(batch, frame=tampered_frame)
    source = PilotObservationSource([tampered])
    replayed = make_runtime(root, base_inputs, source)
    outcomes = replayed.run(1)
    assert outcomes[0].kind is CycleKind.REJECTED
    assert outcomes[0].failure.code is FailureCode.REPLAY_MISMATCH
    # The incident is retained: nothing acked, nothing rejected, no new
    # evidence, no resequencing.
    assert source.acknowledged == []
    assert source.rejected == []
    assert _journal(root).read() == journal_before
    status = replayed.status()
    assert status.degraded
    assert status.last_failure_code == "replay_mismatch"


def test_case5_divergent_evaluation_checkpoint_fails_closed(
    tmp_path, base_inputs
):
    batch = calm_batch(base_inputs)
    root = tmp_path / "site"
    paths = evidence_paths(root)
    runtime = make_runtime(root, base_inputs, PilotObservationSource([batch]))
    runtime.run(2)

    store = JsonEvaluationCheckpointStore(paths["evaluation_checkpoints"])
    completed = store.load(SITE_ID, DEPLOYMENT_ID)
    forged = EvaluationCheckpoint(
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        last_sequence=completed.last_sequence,
        last_observation_timestamp_s=completed.last_observation_timestamp_s,
        last_envelope_id="fse:" + "0" * 64,
        last_evaluation_id=completed.last_evaluation_id,
    )
    store.save(forged)

    journal_before = paths["journal"].read_bytes()
    replayed = make_runtime(root, base_inputs, PilotObservationSource([batch]))
    with pytest.raises(AgentRuntimeError) as raised:
        replayed.run_once()
    assert raised.value.incident_code == "evaluation_replay_mismatch"
    assert replayed.status().runtime_state is RuntimeState.FAILED
    assert paths["journal"].read_bytes() == journal_before


def test_recover_fails_closed_when_journal_runs_ahead_of_the_checkpoint(
    tmp_path, base_inputs
):
    batch = calm_batch(base_inputs)
    root = tmp_path / "site"
    paths = evidence_paths(root)
    runtime = make_runtime(root, base_inputs, PilotObservationSource([batch]))
    runtime.run(2)

    # Roll the evaluation checkpoint back so the journal is ahead of it.
    store = JsonEvaluationCheckpointStore(paths["evaluation_checkpoints"])
    store.save(
        EvaluationCheckpoint(site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)
    )
    restarted = make_runtime(root, base_inputs, PilotObservationSource([]))
    with pytest.raises(AgentRuntimeError) as raised:
        restarted.recover()
    assert raised.value.incident_code == "journal_divergence"
    assert restarted.status().runtime_state is RuntimeState.FAILED
