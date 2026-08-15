"""Runtime lifecycle: startup, bounded runs, graceful stop, status."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nxt_agent_runtime import AgentRuntimeError, CycleKind, RuntimeState

from .conftest import (
    DEPLOYMENT_ID,
    SITE_ID,
    PilotObservationSource,
    calm_batch,
    make_runtime,
    spike_batch,
)


def test_initial_startup_reports_created_state(tmp_path, base_inputs):
    runtime = make_runtime(
        tmp_path, base_inputs, PilotObservationSource([calm_batch(base_inputs)])
    )
    status = runtime.status()
    assert status.runtime_state is RuntimeState.CREATED
    assert status.site_id == SITE_ID
    assert status.deployment_id == DEPLOYMENT_ID
    assert status.cycles_completed == 0
    assert status.last_evaluated_sequence is None
    assert status.pending_decision_count == 0
    assert not status.degraded


def test_bounded_multi_cycle_run_evaluates_each_admitted_envelope(
    tmp_path, base_inputs
):
    source = PilotObservationSource(
        [calm_batch(base_inputs), spike_batch(base_inputs)]
    )
    runtime = make_runtime(tmp_path, base_inputs, source)
    outcomes = runtime.run(10)
    kinds = [outcome.kind for outcome in outcomes]
    assert kinds == [
        CycleKind.EVALUATED,
        CycleKind.EVALUATED,
        CycleKind.SOURCE_EXHAUSTED,
    ]
    assert source.acknowledged == [0, 1]
    status = runtime.status()
    assert status.runtime_state is RuntimeState.RUNNING
    assert status.source_exhausted
    assert status.cycles_completed == 3
    assert status.evaluations_completed == 2
    assert status.last_evaluated_sequence == 1


def test_run_respects_the_cycle_bound(tmp_path, base_inputs):
    source = PilotObservationSource(
        [calm_batch(base_inputs), spike_batch(base_inputs)]
    )
    runtime = make_runtime(tmp_path, base_inputs, source)
    outcomes = runtime.run(1)
    assert [outcome.kind for outcome in outcomes] == [CycleKind.EVALUATED]
    assert source.acknowledged == [0]
    assert len(source.batches) == 1


def test_graceful_stop_prevents_further_cycles(tmp_path, base_inputs):
    source = PilotObservationSource(
        [calm_batch(base_inputs), spike_batch(base_inputs)]
    )
    runtime = make_runtime(tmp_path, base_inputs, source)
    runtime.run(1)
    runtime.request_stop()
    assert runtime.status().runtime_state is RuntimeState.STOPPED
    assert runtime.run(5) == ()
    assert source.acknowledged == [0]


def test_run_once_after_stop_raises_and_stop_is_idempotent(
    tmp_path, base_inputs
):
    runtime = make_runtime(
        tmp_path, base_inputs, PilotObservationSource([calm_batch(base_inputs)])
    )
    runtime.request_stop()
    runtime.request_stop()
    with pytest.raises(AgentRuntimeError) as raised:
        runtime.run_once()
    assert raised.value.incident_code == "runtime_stopped"
    assert runtime.status().runtime_state is RuntimeState.STOPPED


def test_source_exhaustion_is_a_clean_bounded_outcome(tmp_path, base_inputs):
    runtime = make_runtime(tmp_path, base_inputs, PilotObservationSource([]))
    outcomes = runtime.run(3)
    assert [outcome.kind for outcome in outcomes] == [CycleKind.SOURCE_EXHAUSTED]
    assert runtime.status().source_exhausted


def test_status_is_deterministic_and_json_ready(tmp_path, base_inputs):
    source = PilotObservationSource([calm_batch(base_inputs)])
    runtime = make_runtime(tmp_path, base_inputs, source)
    runtime.run(2)
    first = runtime.status()
    second = runtime.status()
    assert first == second
    payload = first.to_dict()
    assert payload["runtime_state"] == "running"
    assert payload["site_id"] == SITE_ID
    import json

    assert json.loads(json.dumps(payload, sort_keys=True)) == payload


def test_constructor_rejects_invalid_identity_and_clock(tmp_path, base_inputs):
    source = PilotObservationSource([])
    with pytest.raises(ValueError):
        make_runtime(tmp_path, base_inputs, source, site_id=" padded ")
    with pytest.raises(ValueError):
        make_runtime(
            tmp_path,
            base_inputs,
            source,
            simulation_midnight=datetime(2026, 8, 8, 7, 0, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError):
        make_runtime(
            tmp_path,
            base_inputs,
            source,
            simulation_midnight=datetime(2026, 8, 8),
        )
    with pytest.raises(TypeError):
        make_runtime(tmp_path, base_inputs, source, clean_sensed_valid="yes")


def test_failed_runtime_refuses_further_work(tmp_path, base_inputs):
    source = PilotObservationSource([calm_batch(base_inputs)])
    runtime = make_runtime(tmp_path, base_inputs, source)
    runtime.run(2)

    class WrongIdentityStore:
        def load(self, site_id, deployment_id):
            from nxt_agent_runtime import EvaluationCheckpoint

            return EvaluationCheckpoint(site_id="other", deployment_id="other")

        def compare_and_save(self, expected, updated):
            raise AssertionError("unused")

    broken = make_runtime(
        tmp_path / "broken",
        base_inputs,
        PilotObservationSource([calm_batch(base_inputs)]),
        evaluation_checkpoint_store=WrongIdentityStore(),
    )
    with pytest.raises(AgentRuntimeError):
        broken.run_once()
    status = broken.status()
    assert status.runtime_state is RuntimeState.FAILED
    assert status.last_failure_code == "evaluation_checkpoint_failed"
    with pytest.raises(AgentRuntimeError) as raised:
        broken.run_once()
    assert raised.value.incident_code == "runtime_failed"
