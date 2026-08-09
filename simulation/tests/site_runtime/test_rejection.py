"""Fail-closed validation, quality, assembly, and source behavior."""

from __future__ import annotations

import dataclasses

import pytest

from nxt_telemetry.assemble import AssemblyReport, assemble_from_observations
from nxt_telemetry.observations import ObservationFrame, ObservationStatus

from nxt_site_runtime import (
    FailureCode,
    InMemoryCheckpointStore,
    SiteRuntimeError,
    SiteRuntimePipeline,
)

from .conftest import (
    CapturingPublisher,
    CapturingSink,
    ReplayableObservationSource,
    make_batch,
)


def _runtime(
    runtime_inputs,
    *,
    site_id="site-valid",
    deployment_id="deploy-valid",
    assembler=None,
    observation_source=None,
):
    publisher = CapturingPublisher()
    sink = CapturingSink()
    store = InMemoryCheckpointStore()
    runtime = SiteRuntimePipeline(
        site_id=site_id,
        deployment_id=deployment_id,
        site_config=runtime_inputs["site_config"],
        publisher=publisher,
        checkpoint_store=store,
        runtime_sink=sink,
        assembler=assembler,
        observation_source=observation_source,
    )
    return runtime, publisher, sink, store


@pytest.mark.parametrize(
    ("site_id", "deployment_id"),
    [("", "deploy"), ("site", ""), ("   ", "deploy"), ("site", "  ")],
)
def test_missing_identity_rejected_before_publish(
    runtime_inputs, site_id, deployment_id
):
    runtime, publisher, sink, _ = _runtime(
        runtime_inputs, site_id=site_id, deployment_id=deployment_id
    )
    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs))
    assert raised.value.failure.code is FailureCode.MISSING_IDENTITY
    assert publisher.published == []
    assert sink.rejected[-1] == raised.value.failure


def test_stale_observation_rejected_without_checkpoint_advance(runtime_inputs):
    frame = runtime_inputs["frame"]
    observations = list(frame.observations)
    observations[0] = dataclasses.replace(
        observations[0],
        sample_timestamp_s=frame.t_s - 901.0,
        status=ObservationStatus.OK,
    )
    stale = ObservationFrame(t_s=frame.t_s, observations=tuple(observations))
    runtime, publisher, _, store = _runtime(runtime_inputs)

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs, frame=stale))

    assert raised.value.failure.code is FailureCode.STALE_OBSERVATION
    assert publisher.published == []
    assert store.load("site-valid", "deploy-valid").last_successful_sequence is None


def test_explicit_stale_status_is_rejected(runtime_inputs):
    frame = runtime_inputs["frame"]
    observations = list(frame.observations)
    observations[0] = dataclasses.replace(
        observations[0], status=ObservationStatus.STALE
    )
    stale = ObservationFrame(t_s=frame.t_s, observations=tuple(observations))
    runtime, _, _, _ = _runtime(runtime_inputs)
    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs, frame=stale))
    assert raised.value.failure.code is FailureCode.STALE_OBSERVATION


def test_low_confidence_is_insufficient_quality(runtime_inputs):
    frame = runtime_inputs["frame"]
    observations = tuple(
        dataclasses.replace(observation, confidence=0.5)
        for observation in frame.observations
    )
    low_quality = ObservationFrame(t_s=frame.t_s, observations=observations)
    runtime, publisher, _, _ = _runtime(runtime_inputs)

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs, frame=low_quality))

    assert raised.value.failure.code is FailureCode.INSUFFICIENT_QUALITY
    assert "effective_confidence" in raised.value.failure.detail
    assert publisher.published == []


def test_low_confidence_upstream_reference_is_gated(runtime_inputs):
    batch = make_batch(runtime_inputs)
    low_reference = dataclasses.replace(
        batch.upstream_source_references[0], confidence=0.4
    )
    runtime, _, _, _ = _runtime(runtime_inputs)
    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(
            dataclasses.replace(
                batch, upstream_source_references=(low_reference,)
            )
        )
    assert raised.value.failure.code is FailureCode.INSUFFICIENT_QUALITY


def test_missing_channel_is_insufficient_quality(runtime_inputs):
    frame = runtime_inputs["frame"]
    observations = list(frame.observations)
    observations[0] = dataclasses.replace(
        observations[0], value=None, status=ObservationStatus.MISSING, confidence=0.0
    )
    missing = ObservationFrame(t_s=frame.t_s, observations=tuple(observations))
    runtime, publisher, _, _ = _runtime(runtime_inputs)

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs, frame=missing))

    assert raised.value.failure.code is FailureCode.INSUFFICIENT_QUALITY
    assert "missing_inputs" in raised.value.failure.detail
    assert publisher.published == []


@pytest.mark.parametrize(
    "mutation",
    ["empty", "duplicate", "future", "source", "object", "nan", "negative_time"],
)
def test_invalid_observations_are_typed_rejections(runtime_inputs, mutation):
    frame = runtime_inputs["frame"]
    observations = list(frame.observations)
    if mutation == "empty":
        invalid = ObservationFrame(t_s=frame.t_s, observations=())
    elif mutation == "duplicate":
        invalid = ObservationFrame(
            t_s=frame.t_s, observations=(observations[0], observations[0])
        )
    elif mutation == "future":
        observations[0] = dataclasses.replace(
            observations[0],
            sample_timestamp_s=frame.t_s + 1.0,
            available_timestamp_s=frame.t_s + 1.0,
        )
        invalid = ObservationFrame(t_s=frame.t_s, observations=tuple(observations))
    elif mutation == "source":
        observations[0] = dataclasses.replace(observations[0], source_id=123)
        invalid = ObservationFrame(t_s=frame.t_s, observations=tuple(observations))
    elif mutation == "object":
        observations[0] = dataclasses.replace(observations[0], value=object())
        invalid = ObservationFrame(t_s=frame.t_s, observations=tuple(observations))
    elif mutation == "nan":
        observations[0] = dataclasses.replace(observations[0], value=float("nan"))
        invalid = ObservationFrame(t_s=frame.t_s, observations=tuple(observations))
    else:
        observations[0] = dataclasses.replace(
            observations[0],
            sample_timestamp_s=-1.0,
            available_timestamp_s=frame.t_s,
        )
        invalid = ObservationFrame(t_s=frame.t_s, observations=tuple(observations))
    runtime, publisher, sink, _ = _runtime(runtime_inputs)

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs, frame=invalid))

    assert raised.value.failure.code is FailureCode.INVALID_OBSERVATION
    assert publisher.published == []
    assert sink.rejected[-1] == raised.value.failure


def test_nonfinite_upstream_is_typed_before_assembly(runtime_inputs):
    invalid_upstream = dataclasses.replace(
        runtime_inputs["upstream"], stockout_minutes=float("nan")
    )
    runtime, publisher, _, _ = _runtime(runtime_inputs)
    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs, upstream=invalid_upstream))
    assert raised.value.failure.code is FailureCode.INVALID_OBSERVATION
    assert publisher.published == []


def test_malformed_upstream_reference_container_is_typed(runtime_inputs):
    batch = dataclasses.replace(
        make_batch(runtime_inputs), upstream_source_references=None
    )
    runtime, publisher, sink, _ = _runtime(runtime_inputs)

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(batch)

    assert raised.value.failure.code is FailureCode.INVALID_OBSERVATION
    assert "must be a tuple" in raised.value.failure.detail
    assert publisher.published == []
    assert sink.rejected[-1] == raised.value.failure


def test_conflicting_source_reference_identity_is_rejected(runtime_inputs):
    batch = make_batch(runtime_inputs)
    reference = batch.upstream_source_references[0]
    conflicting = dataclasses.replace(reference, confidence=0.9)
    runtime, publisher, _, _ = _runtime(runtime_inputs)

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(
            dataclasses.replace(
                batch,
                upstream_source_references=(reference, conflicting),
            )
        )

    assert raised.value.failure.code is FailureCode.INVALID_OBSERVATION
    assert "identities must be unique" in raised.value.failure.detail
    assert publisher.published == []


def test_consistency_issue_is_gated(runtime_inputs):
    frame = runtime_inputs["frame"]
    observations = list(frame.observations)
    index = next(
        i
        for i, observation in enumerate(observations)
        if observation.channel == "inventory.dispenser.count"
    )
    observations[index] = dataclasses.replace(
        observations[index], value=observations[index].value + 1
    )
    inconsistent = ObservationFrame(
        t_s=frame.t_s, observations=tuple(observations)
    )
    runtime, _, _, _ = _runtime(runtime_inputs)
    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs, frame=inconsistent))
    assert raised.value.failure.code is FailureCode.INSUFFICIENT_QUALITY
    assert "consistency_issues" in raised.value.failure.detail


def test_unaccepted_provenance_grade_is_gated(runtime_inputs):
    def low_grade_assembler(frame, site, upstream):
        state, report = assemble_from_observations(frame, site, upstream)
        return state, dataclasses.replace(report, provenance_grade="low")

    runtime, _, _, _ = _runtime(runtime_inputs, assembler=low_grade_assembler)
    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs))
    assert raised.value.failure.code is FailureCode.INSUFFICIENT_QUALITY
    assert "provenance_grade" in raised.value.failure.detail


def test_failed_assembly_is_retryable_and_source_retains_batch(runtime_inputs):
    def broken_assembler(frame, site, upstream):
        raise RuntimeError("assembly exploded")

    source = ReplayableObservationSource([make_batch(runtime_inputs)])
    runtime, publisher, _, store = _runtime(
        runtime_inputs,
        assembler=broken_assembler,
        observation_source=source,
    )
    with pytest.raises(SiteRuntimeError) as raised:
        runtime.run_once()

    assert raised.value.failure.code is FailureCode.ASSEMBLY_FAILED
    assert raised.value.failure.retryable is True
    assert publisher.published == []
    assert len(source.batches) == 1
    assert source.rejected == []
    checkpoint = store.load("site-valid", "deploy-valid")
    assert checkpoint.last_successful_sequence is None
    assert checkpoint.has_pending_publish is False


def test_injected_assembler_must_return_canonical_assembly_report(runtime_inputs):
    class ReportSubclass(AssemblyReport):
        pass

    def noncanonical_assembler(frame, site, upstream):
        state, report = assemble_from_observations(frame, site, upstream)
        return state, ReportSubclass(
            missing_channels=report.missing_channels,
            stale_channels=report.stale_channels,
            consistency_issues=report.consistency_issues,
            overall_confidence=report.overall_confidence,
            provenance_grade=report.provenance_grade,
        )

    runtime, publisher, _, store = _runtime(
        runtime_inputs, assembler=noncanonical_assembler
    )

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.process(make_batch(runtime_inputs))

    assert raised.value.failure.code is FailureCode.ASSEMBLY_FAILED
    assert raised.value.failure.retryable is True
    assert "wrong AssemblyReport contract" in raised.value.failure.detail
    assert publisher.published == []
    assert store.load("site-valid", "deploy-valid").has_pending_publish is False


def test_terminal_rejection_discards_frame_and_reuses_sequence(runtime_inputs):
    frame = runtime_inputs["frame"]
    stale_observations = list(frame.observations)
    stale_observations[0] = dataclasses.replace(
        stale_observations[0], status=ObservationStatus.STALE
    )
    stale_frame = ObservationFrame(
        t_s=frame.t_s, observations=tuple(stale_observations)
    )
    source = ReplayableObservationSource(
        [
            make_batch(runtime_inputs, sequence_number=0, frame=stale_frame),
            make_batch(runtime_inputs, sequence_number=1),
        ]
    )
    runtime, publisher, _, _ = _runtime(
        runtime_inputs, observation_source=source
    )

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.run_once()
    assert raised.value.failure.code is FailureCode.STALE_OBSERVATION
    assert source.rejected == [(0, FailureCode.STALE_OBSERVATION.value)]

    envelope = runtime.run_once()
    assert envelope.sequence_number == 0
    assert source.acknowledged == [0]
    assert publisher.published == [envelope]


def test_completed_replay_mismatch_is_retained_until_remediated(runtime_inputs):
    source = ReplayableObservationSource([make_batch(runtime_inputs)])
    runtime, publisher, _, store = _runtime(
        runtime_inputs, observation_source=source
    )
    runtime.run_once()

    observations = list(runtime_inputs["frame"].observations)
    observations[0] = dataclasses.replace(observations[0], confidence=0.9)
    changed_frame = ObservationFrame(
        t_s=runtime_inputs["frame"].t_s,
        observations=tuple(observations),
    )
    source.batches.extend(
        [
            make_batch(runtime_inputs, sequence_number=0, frame=changed_frame),
            make_batch(runtime_inputs, sequence_number=1),
        ]
    )

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.run_once()

    assert raised.value.failure.code is FailureCode.REPLAY_MISMATCH
    assert [batch.sequence_number for batch in source.batches] == [0, 1]
    assert source.rejected == []

    # Explicit operator/adapter remediation removes the conflicting replay;
    # the runtime has neither discarded nor resequenced the valid next frame.
    source.batches.pop(0)
    recovered = runtime.run_once()
    assert recovered.sequence_number == 1
    assert len(publisher.published) == 2
    assert store.load("site-valid", "deploy-valid").last_successful_sequence == 1


def test_sequence_gap_is_retained_without_resequencing_later_frames(runtime_inputs):
    source = ReplayableObservationSource([make_batch(runtime_inputs)])
    runtime, _, _, store = _runtime(runtime_inputs, observation_source=source)
    runtime.run_once()
    expected = make_batch(runtime_inputs, sequence_number=1)
    gap = make_batch(runtime_inputs, sequence_number=2)
    source.batches.extend([gap, expected])

    with pytest.raises(SiteRuntimeError) as raised:
        runtime.run_once()

    assert raised.value.failure.code is FailureCode.INVALID_SEQUENCE
    assert [batch.sequence_number for batch in source.batches] == [2, 1]
    assert source.rejected == []

    # An adapter can restore source order without repairing sequence numbers.
    source.batches[:] = [expected, gap]
    assert runtime.run_once().sequence_number == 1
    assert runtime.run_once().sequence_number == 2
    assert source.acknowledged == [0, 1, 2]
    assert store.load("site-valid", "deploy-valid").last_successful_sequence == 2


def test_empty_source_failure_is_typed(runtime_inputs):
    source = ReplayableObservationSource([])
    runtime, _, _, _ = _runtime(runtime_inputs, observation_source=source)
    with pytest.raises(SiteRuntimeError) as raised:
        runtime.run_once()
    assert raised.value.failure.code is FailureCode.INGESTION_FAILED
    assert raised.value.failure.retryable is True
