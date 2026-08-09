"""Checkpoint persistence, atomicity, and replay recovery."""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from nxt_telemetry.assemble import assemble_from_observations
from nxt_telemetry.observations import ObservationFrame

from nxt_site_runtime import (
    CheckpointError,
    FailureCode,
    InMemoryCheckpointStore,
    JsonCheckpointStore,
    QualityGate,
    RuntimeCheckpoint,
    SiteRuntimeError,
    SiteRuntimePipeline,
)

from .conftest import (
    CapturingPublisher,
    ReplayableObservationSource,
    make_batch,
)


class UncertainPublisher(CapturingPublisher):
    """Delivers once, then reports one ambiguous failure."""

    def __init__(self):
        super().__init__()
        self.fail_once = True

    def publish(self, envelope) -> None:
        super().publish(envelope)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("delivery acknowledgement lost")


class FlakyAcknowledgeSource(ReplayableObservationSource):
    def __init__(self, batches):
        super().__init__(batches)
        self.fail_once = True

    def acknowledge(self, sequence_number: int) -> None:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("source acknowledgement unavailable")
        super().acknowledge(sequence_number)


def _runtime(runtime_inputs, publisher, store, **kwargs):
    return SiteRuntimePipeline(
        site_id="site-recovery",
        deployment_id="deploy-recovery",
        site_config=runtime_inputs["site_config"],
        publisher=publisher,
        checkpoint_store=store,
        **kwargs,
    )


def test_checkpoint_transition_tracks_success_and_recovery_metadata():
    initial = RuntimeCheckpoint(site_id="site-a", deployment_id="deploy-a")
    assert initial.next_sequence is None
    prepared = initial.prepare(
        sequence_number=12,
        observation_timestamp_s=123.0,
        envelope_id="fse:abc",
    )
    assert prepared.last_successful_sequence is None
    assert prepared.recovery_metadata == {
        "pending_sequence": 12,
        "pending_observation_timestamp_s": 123.0,
        "pending_envelope_id": "fse:abc",
        "pending_recovery_attempts": 0,
    }
    replay = prepared.prepare(
        sequence_number=12,
        observation_timestamp_s=123.0,
        envelope_id="fse:abc",
    )
    complete = replay.complete()
    assert complete.last_successful_sequence == 12
    assert complete.last_recovery_attempts == 1
    assert complete.has_pending_publish is False


def test_corrupt_recovery_count_is_rejected():
    with pytest.raises(CheckpointError, match="completed publish"):
        RuntimeCheckpoint(
            site_id="site-a",
            deployment_id="deploy-a",
            last_recovery_attempts=1,
        )


def test_json_checkpoint_survives_store_reconstruction(tmp_path):
    first_store = JsonCheckpointStore(tmp_path)
    checkpoint = RuntimeCheckpoint(
        site_id="site-json", deployment_id="deploy-json"
    ).prepare(
        sequence_number=0,
        observation_timestamp_s=55.0,
        envelope_id="fse:persisted",
    )
    first_store.save(checkpoint)

    restored = JsonCheckpointStore(tmp_path).load("site-json", "deploy-json")
    assert restored == checkpoint
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert "site-json" not in next(tmp_path.glob("*.json")).name


def test_uncertain_publish_recovers_after_runtime_and_store_restart(
    runtime_inputs, tmp_path
):
    publisher = UncertainPublisher()
    source = ReplayableObservationSource([make_batch(runtime_inputs)])
    first = _runtime(
        runtime_inputs,
        publisher,
        JsonCheckpointStore(tmp_path),
        observation_source=source,
    )

    with pytest.raises(SiteRuntimeError) as raised:
        first.run_once()
    assert raised.value.failure.code is FailureCode.PUBLISH_FAILED
    assert source.acknowledged == []
    assert len(publisher.published) == 1

    recovered = _runtime(
        runtime_inputs,
        publisher,
        JsonCheckpointStore(tmp_path),
        observation_source=source,
    ).run_once()
    completed = JsonCheckpointStore(tmp_path).load(
        "site-recovery", "deploy-recovery"
    )
    assert publisher.attempts == [recovered.envelope_id, recovered.envelope_id]
    assert len(publisher.published) == 1
    assert source.acknowledged == [0]
    assert completed.last_successful_sequence == 0
    assert completed.last_recovery_attempts == 1


def test_pending_sequence_rejects_changed_immutable_batch(runtime_inputs):
    publisher = UncertainPublisher()
    store = InMemoryCheckpointStore()
    runtime = _runtime(runtime_inputs, publisher, store)
    original = make_batch(runtime_inputs)
    with pytest.raises(SiteRuntimeError):
        runtime.process(original)

    observations = list(runtime_inputs["frame"].observations)
    observations[0] = dataclasses.replace(observations[0], confidence=0.9)
    changed_frame = ObservationFrame(
        t_s=runtime_inputs["frame"].t_s,
        observations=tuple(observations),
    )
    changed = make_batch(runtime_inputs, frame=changed_frame)
    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(changed)

    assert raised.value.failure.code is FailureCode.REPLAY_MISMATCH
    assert len(publisher.attempts) == 1
    assert store.load("site-recovery", "deploy-recovery").pending_sequence == 0


def test_completed_replay_recovers_lost_source_ack_without_republish(runtime_inputs):
    publisher = CapturingPublisher()
    store = InMemoryCheckpointStore()
    source = FlakyAcknowledgeSource([make_batch(runtime_inputs)])
    runtime = _runtime(
        runtime_inputs,
        publisher,
        store,
        observation_source=source,
    )

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.run_once()
    assert raised.value.failure.code is FailureCode.INGESTION_FAILED
    assert len(publisher.published) == 1
    assert source.batches

    replay = runtime.run_once()
    assert source.acknowledged == [0]
    assert publisher.attempts == [replay.envelope_id]
    assert len(publisher.published) == 1


def test_completed_replay_bypasses_new_quality_policy(runtime_inputs):
    observations = tuple(
        dataclasses.replace(observation, confidence=0.9)
        for observation in runtime_inputs["frame"].observations
    )
    frame = ObservationFrame(
        t_s=runtime_inputs["frame"].t_s, observations=observations
    )
    source = FlakyAcknowledgeSource(
        [make_batch(runtime_inputs, frame=frame)]
    )
    publisher = CapturingPublisher()
    store = InMemoryCheckpointStore()
    first = _runtime(
        runtime_inputs,
        publisher,
        store,
        observation_source=source,
        quality_gate=QualityGate(minimum_effective_confidence=0.8),
    )

    with pytest.raises(SiteRuntimeError) as raised:
        first.run_once()
    assert raised.value.failure.code is FailureCode.INGESTION_FAILED

    replay = _runtime(
        runtime_inputs,
        publisher,
        store,
        observation_source=source,
        quality_gate=QualityGate(minimum_effective_confidence=0.95),
    ).run_once()

    assert replay.runtime_quality.effective_confidence == 0.9
    assert source.acknowledged == [0]
    assert len(publisher.published) == 1


def test_pending_replay_bypasses_new_quality_policy(runtime_inputs):
    observations = tuple(
        dataclasses.replace(observation, confidence=0.9)
        for observation in runtime_inputs["frame"].observations
    )
    frame = ObservationFrame(
        t_s=runtime_inputs["frame"].t_s, observations=observations
    )
    source = ReplayableObservationSource(
        [make_batch(runtime_inputs, frame=frame)]
    )
    publisher = UncertainPublisher()
    store = InMemoryCheckpointStore()
    first = _runtime(
        runtime_inputs,
        publisher,
        store,
        observation_source=source,
        quality_gate=QualityGate(minimum_effective_confidence=0.8),
    )

    with pytest.raises(SiteRuntimeError) as raised:
        first.run_once()
    assert raised.value.failure.code is FailureCode.PUBLISH_FAILED

    recovered = _runtime(
        runtime_inputs,
        publisher,
        store,
        observation_source=source,
        quality_gate=QualityGate(minimum_effective_confidence=0.95),
    ).run_once()

    assert recovered.runtime_quality.effective_confidence == 0.9
    assert source.acknowledged == [0]
    assert publisher.attempts == [recovered.envelope_id, recovered.envelope_id]
    assert len(publisher.published) == 1


def test_compare_and_save_allows_only_one_concurrent_reservation(runtime_inputs):
    barrier = Barrier(2)

    def synchronized_assembler(frame, site, upstream):
        result = assemble_from_observations(frame, site, upstream)
        barrier.wait(timeout=5)
        return result

    store = InMemoryCheckpointStore()
    publisher = CapturingPublisher()
    first = _runtime(
        runtime_inputs, publisher, store, assembler=synchronized_assembler
    )
    second = _runtime(
        runtime_inputs, publisher, store, assembler=synchronized_assembler
    )
    observations = list(runtime_inputs["frame"].observations)
    observations[0] = dataclasses.replace(observations[0], confidence=0.9)
    changed_frame = ObservationFrame(
        t_s=runtime_inputs["frame"].t_s,
        observations=tuple(observations),
    )
    batches = [make_batch(runtime_inputs), make_batch(runtime_inputs, frame=changed_frame)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(runtime.process, batch)
            for runtime, batch in zip((first, second), batches)
        ]
        results = []
        failures = []
        for future in futures:
            try:
                results.append(future.result())
            except SiteRuntimeError as exc:
                failures.append(exc.failure)

    assert len(results) == 1
    assert [failure.code for failure in failures] == [
        FailureCode.CHECKPOINT_FAILED
    ]
    assert len(publisher.published) == 1


def test_json_store_serializes_two_independent_runtime_instances(
    runtime_inputs, tmp_path
):
    barrier = Barrier(2)

    def synchronized_assembler(frame, site, upstream):
        result = assemble_from_observations(frame, site, upstream)
        barrier.wait(timeout=5)
        return result

    publisher = CapturingPublisher()
    first = _runtime(
        runtime_inputs,
        publisher,
        JsonCheckpointStore(tmp_path),
        assembler=synchronized_assembler,
    )
    second = _runtime(
        runtime_inputs,
        publisher,
        JsonCheckpointStore(tmp_path),
        assembler=synchronized_assembler,
    )
    observations = list(runtime_inputs["frame"].observations)
    observations[0] = dataclasses.replace(observations[0], confidence=0.9)
    changed_frame = ObservationFrame(
        t_s=runtime_inputs["frame"].t_s,
        observations=tuple(observations),
    )
    batches = [
        make_batch(runtime_inputs),
        make_batch(runtime_inputs, frame=changed_frame),
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(runtime.process, batch)
            for runtime, batch in zip((first, second), batches)
        ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except SiteRuntimeError as exc:
                outcomes.append(exc.failure)

    assert sum(hasattr(outcome, "envelope_id") for outcome in outcomes) == 1
    failures = [outcome for outcome in outcomes if not hasattr(outcome, "envelope_id")]
    assert [failure.code for failure in failures] == [
        FailureCode.CHECKPOINT_FAILED
    ]
    assert len(publisher.published) == 1
    restored = JsonCheckpointStore(tmp_path).load(
        "site-recovery", "deploy-recovery"
    )
    assert restored.last_successful_sequence == 0
