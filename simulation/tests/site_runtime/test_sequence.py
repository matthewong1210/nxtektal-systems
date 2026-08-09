"""Site-level sequence ordering, replay, and clock-reset behavior."""

from __future__ import annotations

import dataclasses

import pytest

from nxt_telemetry.observations import ObservationFrame

from nxt_site_runtime import (
    FailureCode,
    InMemoryCheckpointStore,
    SiteRuntimeError,
    SiteRuntimePipeline,
)

from .conftest import CapturingPublisher, make_batch, upstream_reference


def _runtime(runtime_inputs):
    publisher = CapturingPublisher()
    store = InMemoryCheckpointStore()
    return (
        SiteRuntimePipeline(
            site_id="site-seq",
            deployment_id="deploy-seq",
            site_config=runtime_inputs["site_config"],
            publisher=publisher,
            checkpoint_store=store,
        ),
        publisher,
        store,
    )


def test_sequences_advance_exactly_once(runtime_inputs):
    runtime, publisher, store = _runtime(runtime_inputs)
    runtime.process(make_batch(runtime_inputs, sequence_number=0))
    runtime.process(make_batch(runtime_inputs, sequence_number=1))

    assert len(publisher.published) == 2
    assert store.load("site-seq", "deploy-seq").last_successful_sequence == 1


def test_completed_sequence_replay_is_idempotent(runtime_inputs):
    runtime, publisher, _ = _runtime(runtime_inputs)
    batch = make_batch(runtime_inputs, sequence_number=0)
    first = runtime.process(batch)
    replay = runtime.process(batch)

    assert replay.envelope_id == first.envelope_id
    assert publisher.attempts == [first.envelope_id]
    assert len(publisher.published) == 1


def test_sub_microprecision_change_cannot_collide_on_replay(runtime_inputs):
    runtime, publisher, _ = _runtime(runtime_inputs)
    frame = runtime_inputs["frame"]
    first = make_batch(
        runtime_inputs,
        upstream_source_references=(
            upstream_reference(frame, confidence=0.9000001),
        ),
    )
    runtime.process(first)
    changed = make_batch(
        runtime_inputs,
        upstream_source_references=(
            upstream_reference(frame, confidence=0.9000002),
        ),
    )

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(changed)

    assert raised.value.failure.code is FailureCode.REPLAY_MISMATCH
    assert len(publisher.published) == 1


def test_sub_microprecision_state_change_cannot_collide_on_replay(runtime_inputs):
    runtime, publisher, _ = _runtime(runtime_inputs)
    frame = runtime_inputs["frame"]
    sensed_index = next(
        index
        for index, observation in enumerate(frame.observations)
        if observation.channel == "inventory.dispenser.sensed"
    )
    base = float(frame.observations[sensed_index].value)

    def with_sensed(value: float) -> ObservationFrame:
        observations = list(frame.observations)
        observations[sensed_index] = dataclasses.replace(
            observations[sensed_index], value=value
        )
        return ObservationFrame(t_s=frame.t_s, observations=tuple(observations))

    runtime.process(make_batch(runtime_inputs, frame=with_sensed(base + 0.0000001)))

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(
            make_batch(runtime_inputs, frame=with_sensed(base + 0.0000002))
        )

    assert raised.value.failure.code is FailureCode.REPLAY_MISMATCH
    assert len(publisher.published) == 1


@pytest.mark.parametrize("bad_sequence", [2, 9])
def test_sequence_gaps_are_rejected(runtime_inputs, bad_sequence):
    runtime, publisher, store = _runtime(runtime_inputs)
    runtime.process(make_batch(runtime_inputs, sequence_number=0))

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs, sequence_number=bad_sequence))

    assert raised.value.failure.code is FailureCode.INVALID_SEQUENCE
    assert len(publisher.published) == 1
    assert store.load("site-seq", "deploy-seq").last_successful_sequence == 0


def test_first_frame_establishes_nonzero_source_offset(runtime_inputs):
    runtime, _, store = _runtime(runtime_inputs)
    envelope = runtime.process(make_batch(runtime_inputs, sequence_number=42))
    assert envelope.sequence_number == 42
    assert store.load("site-seq", "deploy-seq").next_sequence == 43


def test_clock_may_reset_when_sequence_still_advances(runtime_inputs):
    runtime, publisher, _ = _runtime(runtime_inputs)
    frame = runtime_inputs["frame"]
    runtime.process(make_batch(runtime_inputs, sequence_number=0))
    earlier_observations = tuple(
        dataclasses.replace(
            observation,
            sample_timestamp_s=observation.sample_timestamp_s - 3600.0,
            available_timestamp_s=observation.available_timestamp_s - 3600.0,
            seq=observation.seq + 1,
        )
        for observation in frame.observations
    )
    earlier = ObservationFrame(
        t_s=frame.t_s - 3600.0, observations=earlier_observations
    )
    second = make_batch(
        runtime_inputs,
        sequence_number=1,
        frame=earlier,
        upstream_source_references=(upstream_reference(earlier, 1),),
    )

    envelope = runtime.process(second)

    assert envelope.observation_timestamp_s == earlier.t_s
    assert len(publisher.published) == 2
