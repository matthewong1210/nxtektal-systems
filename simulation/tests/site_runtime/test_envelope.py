"""Envelope schema and byte-level determinism."""

from __future__ import annotations

import dataclasses
import json

import pytest

from nxt_site_runtime import (
    SCHEMA_VERSION,
    InMemoryCheckpointStore,
    SiteRuntimePipeline,
)

from .conftest import CapturingPublisher, make_batch


def _build_envelope(runtime_inputs):
    publisher = CapturingPublisher()
    runtime = SiteRuntimePipeline(
        site_id="site-deterministic",
        deployment_id="deploy-1",
        site_config=runtime_inputs["site_config"],
        publisher=publisher,
        checkpoint_store=InMemoryCheckpointStore(),
    )
    return runtime.process(make_batch(runtime_inputs))


def test_same_observation_sequence_produces_same_metadata(runtime_inputs):
    first = _build_envelope(runtime_inputs)
    second = _build_envelope(runtime_inputs)

    assert first.schema_version == SCHEMA_VERSION
    assert first.envelope_id == second.envelope_id
    assert first.payload_reference == second.payload_reference
    assert first.metadata_dict() == second.metadata_dict()
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
        second.to_dict(), sort_keys=True
    )
    assert first.source_references == tuple(sorted(first.source_references))
    assert first.runtime_quality.effective_confidence == 1.0
    assert first.payload_reference.endswith("#facility_state")


def test_envelope_keeps_exact_canonical_state_reference(runtime_inputs):
    publisher = CapturingPublisher()
    runtime = SiteRuntimePipeline(
        site_id="site-deterministic",
        deployment_id="deploy-1",
        site_config=runtime_inputs["site_config"],
        publisher=publisher,
        checkpoint_store=InMemoryCheckpointStore(),
    )
    envelope = runtime.process(make_batch(runtime_inputs))
    assert publisher.published[0] is envelope
    assert publisher.published[0].facility_state is envelope.facility_state
    assert envelope.to_dict()["facility_state"] == envelope.facility_state.to_dict()


def test_envelope_rejects_conflicting_provenance_identity(runtime_inputs):
    envelope = _build_envelope(runtime_inputs)
    reference = envelope.source_references[0]
    conflicting = dataclasses.replace(reference, confidence=0.9)

    with pytest.raises(ValueError, match="identities must be unique"):
        dataclasses.replace(
            envelope,
            source_references=envelope.source_references + (conflicting,),
        )


def test_envelope_rejects_malformed_reference_fields_and_container(runtime_inputs):
    envelope = _build_envelope(runtime_inputs)
    with pytest.raises(ValueError, match="status must be one of"):
        dataclasses.replace(envelope.source_references[0], status=[])
    with pytest.raises(ValueError, match="must be a tuple"):
        dataclasses.replace(envelope, source_references=None)
