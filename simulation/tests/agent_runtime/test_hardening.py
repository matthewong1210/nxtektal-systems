"""Review-hardening coverage: store contracts, divergence gates, spin guards."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from nxt_agent_runtime import (
    AgentRuntimeError,
    CycleKind,
    EvaluationCheckpoint,
    EvaluationCheckpointConflictError,
    EvaluationJournal,
    FailurePolicy,
    InMemoryEvaluationCheckpointStore,
    JournalIntegrityError,
    JsonEvaluationCheckpointStore,
    ManagerDecisionQueue,
    ManagerDecisionQueueError,
    RuntimeState,
)
from nxt_pilot_ops.ledger import JsonlEventLedger
from nxt_site_runtime.pipeline import FailureCode

from .conftest import (
    DEPLOYMENT_ID,
    SITE_ID,
    PilotObservationSource,
    calm_batch,
    evidence_paths,
    make_runtime,
    spike_batch,
)


# -- evaluation checkpoint store contract -------------------------------------


def _completed_checkpoint(sequence=0):
    return EvaluationCheckpoint(
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        last_sequence=sequence,
        last_observation_timestamp_s=63000.0,
        last_envelope_id="fse:" + "a" * 64,
        last_evaluation_id="aev_" + "b" * 24,
    )


@pytest.mark.parametrize("store_kind", ["memory", "json"])
def test_compare_and_save_rejects_concurrent_modification(
    tmp_path, store_kind
):
    store = (
        InMemoryEvaluationCheckpointStore()
        if store_kind == "memory"
        else JsonEvaluationCheckpointStore(tmp_path / "cp")
    )
    initial = store.load(SITE_ID, DEPLOYMENT_ID)
    winner = _completed_checkpoint()
    store.compare_and_save(initial, winner)
    stale_update = dataclasses.replace(initial)
    with pytest.raises(EvaluationCheckpointConflictError):
        store.compare_and_save(stale_update, _completed_checkpoint(1))
    assert store.load(SITE_ID, DEPLOYMENT_ID) == winner


# -- journal append-time fail-closed contract ---------------------------------


def test_journal_append_rejects_divergent_content_for_a_sequence(
    tmp_path, base_inputs
):
    root = tmp_path / "run"
    runtime = make_runtime(
        root, base_inputs, PilotObservationSource([calm_batch(base_inputs)])
    )
    record = runtime.run(2)[0].record
    journal = EvaluationJournal(evidence_paths(root)["journal"])
    # Identical replay is an idempotent no-op.
    assert journal.append(record) == record
    assert len(journal.read()) == 1
    # Divergent content for the journaled sequence fails closed.
    forged_fields = {
        name: getattr(record, name)
        for name in (
            "site_id",
            "deployment_id",
            "sequence_number",
            "envelope_id",
            "observation_timestamp_s",
            "observed_at",
            "verdict",
            "policy_id",
            "policy_version",
            "trace_id",
            "snapshot_digest",
            "recommendation_id",
            "recommendation_action",
            "ledger_event_id",
        )
    }
    from nxt_agent_runtime import EvaluationRecord

    divergent = EvaluationRecord.create(
        **{
            **forged_fields,
            "trace_id": "trace_" + "0" * 24,
            "decision_trace": {
                **dict(record.decision_trace),
                "trace_id": "trace_" + "0" * 24,
            },
        }
    )
    with pytest.raises(JournalIntegrityError):
        journal.append(divergent)
    assert len(journal.read()) == 1


# -- recover() divergence gates -----------------------------------------------


def test_recover_fails_closed_when_journal_is_behind_the_checkpoint(
    tmp_path, base_inputs
):
    root = tmp_path / "run"
    runtime = make_runtime(
        root, base_inputs, PilotObservationSource([calm_batch(base_inputs)])
    )
    runtime.run(2)
    # Simulate a power loss that lost the journal file's directory entry
    # while the completed evaluation checkpoint survived.
    evidence_paths(root)["journal"].unlink()
    restarted = make_runtime(root, base_inputs, PilotObservationSource([]))
    with pytest.raises(AgentRuntimeError) as raised:
        restarted.recover()
    assert raised.value.incident_code == "journal_divergence"
    assert restarted.status().runtime_state is RuntimeState.FAILED


def test_recover_fails_closed_on_checkpoint_misalignment(
    tmp_path, base_inputs
):
    from nxt_site_runtime.checkpoints import (
        JsonCheckpointStore,
        RuntimeCheckpoint,
    )

    root = tmp_path / "run"
    runtime = make_runtime(
        root, base_inputs, PilotObservationSource([calm_batch(base_inputs)])
    )
    runtime.run(2)
    # Forge the site publication checkpoint two sequences ahead of the
    # evaluation checkpoint, as if the site pipeline ran standalone.
    site_store = JsonCheckpointStore(evidence_paths(root)["site_checkpoints"])
    site_store.save(
        RuntimeCheckpoint(
            site_id=SITE_ID,
            deployment_id=DEPLOYMENT_ID,
            last_successful_sequence=2,
            last_observation_timestamp_s=70200.0,
            last_envelope_id="fse:" + "c" * 64,
        )
    )
    misaligned = make_runtime(root, base_inputs, PilotObservationSource([]))
    with pytest.raises(AgentRuntimeError) as raised:
        misaligned.recover()
    assert raised.value.incident_code == "checkpoint_divergence"
    assert misaligned.status().runtime_state is RuntimeState.FAILED


def test_recover_fails_closed_when_pending_disagrees_with_publication(
    tmp_path, base_inputs
):
    root = tmp_path / "run"
    runtime = make_runtime(
        root, base_inputs, PilotObservationSource([calm_batch(base_inputs)])
    )
    runtime.run(2)
    store = JsonEvaluationCheckpointStore(
        evidence_paths(root)["evaluation_checkpoints"]
    )
    completed = store.load(SITE_ID, DEPLOYMENT_ID)
    forged_pending = dataclasses.replace(
        completed,
        pending_sequence=completed.last_sequence + 1,
        pending_observation_timestamp_s=66600.0,
        pending_envelope_id="fse:" + "d" * 64,
        pending_evaluation_id="aev_" + "d" * 24,
    )
    store.save(forged_pending)
    restarted = make_runtime(root, base_inputs, PilotObservationSource([]))
    with pytest.raises(AgentRuntimeError) as raised:
        restarted.recover()
    assert raised.value.incident_code == "checkpoint_divergence"


def test_recover_fails_closed_on_foreign_journal_identity(
    tmp_path, base_inputs
):
    foreign_root = tmp_path / "foreign"
    foreign = make_runtime(
        foreign_root,
        base_inputs,
        PilotObservationSource([calm_batch(base_inputs)]),
        site_id="other-site",
        deployment_id="other-deployment",
    )
    foreign.run(2)
    local_root = tmp_path / "local"
    local_paths = evidence_paths(local_root)
    local_paths["journal"].parent.mkdir(parents=True, exist_ok=True)
    local_paths["journal"].write_bytes(
        evidence_paths(foreign_root)["journal"].read_bytes()
    )
    runtime = make_runtime(local_root, base_inputs, PilotObservationSource([]))
    with pytest.raises(AgentRuntimeError) as raised:
        runtime.recover()
    assert raised.value.incident_code == "journal_identity_mismatch"


def test_recover_fails_closed_on_foreign_ledger_identity(
    tmp_path, base_inputs
):
    foreign_root = tmp_path / "foreign"
    foreign = make_runtime(
        foreign_root,
        base_inputs,
        PilotObservationSource(
            [spike_batch(base_inputs, sequence_number=0, cycle=0)]
        ),
        site_id="other-site",
        deployment_id="other-deployment",
    )
    foreign.run(2)
    local_root = tmp_path / "local"
    local_paths = evidence_paths(local_root)
    local_paths["ledger"].parent.mkdir(parents=True, exist_ok=True)
    local_paths["ledger"].write_bytes(
        evidence_paths(foreign_root)["ledger"].read_bytes()
    )
    runtime = make_runtime(local_root, base_inputs, PilotObservationSource([]))
    with pytest.raises(AgentRuntimeError) as raised:
        runtime.recover()
    assert raised.value.incident_code == "ledger_identity_mismatch"


def test_transient_recovery_outage_is_not_a_permanent_failure(
    tmp_path, base_inputs
):
    class FlakyStore(JsonEvaluationCheckpointStore):
        def __init__(self, root):
            super().__init__(root)
            self.failures_remaining = 1

        def load(self, site_id, deployment_id):
            if self.failures_remaining:
                self.failures_remaining -= 1
                raise OSError("simulated transient store outage")
            return super().load(site_id, deployment_id)

    root = tmp_path / "run"
    store = FlakyStore(evidence_paths(root)["evaluation_checkpoints"])
    runtime = make_runtime(
        root,
        base_inputs,
        PilotObservationSource([calm_batch(base_inputs)]),
        evaluation_checkpoint_store=store,
    )
    with pytest.raises(AgentRuntimeError) as raised:
        runtime.recover()
    assert raised.value.incident_code == "recovery_unavailable"
    assert runtime.status().runtime_state is not RuntimeState.FAILED
    outcomes = runtime.run(2)
    assert [outcome.kind for outcome in outcomes] == [
        CycleKind.EVALUATED,
        CycleKind.SOURCE_EXHAUSTED,
    ]


# -- queue identity scoping ---------------------------------------------------


def test_scoped_queue_never_shows_or_operates_on_foreign_cases(
    tmp_path, base_inputs
):
    root = tmp_path / "shared"
    foreign = make_runtime(
        root,
        base_inputs,
        PilotObservationSource(
            [spike_batch(base_inputs, sequence_number=0, cycle=0)]
        ),
        site_id="other-site",
        deployment_id="other-deployment",
    )
    foreign.run(2)
    foreign_entry = foreign.queue.pending()[0]
    scoped = ManagerDecisionQueue(
        ledger=JsonlEventLedger(evidence_paths(root)["ledger"]),
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
    )
    assert scoped.entries() == ()
    assert scoped.pending() == ()
    with pytest.raises(
        ManagerDecisionQueueError,
        match=f"unknown recommendation: {foreign_entry.recommendation_id}",
    ):
        scoped.accept(
            foreign_entry.recommendation_id,
            operator_id="manager-1",
            responded_at=datetime(2026, 8, 8, 19, 0, tzinfo=timezone.utc),
            reason_code="cross_site",
        )


# -- run() termination and failure surfacing ----------------------------------


def test_run_stops_after_a_deferred_evaluation(tmp_path, base_inputs):
    class CrashingJournal(EvaluationJournal):
        def append(self, record):
            raise RuntimeError("simulated journal outage")

    root = tmp_path / "run"
    runtime = make_runtime(
        root,
        base_inputs,
        PilotObservationSource(
            [calm_batch(base_inputs), spike_batch(base_inputs)]
        ),
        journal=CrashingJournal(evidence_paths(root)["journal"]),
    )
    outcomes = runtime.run(5, failure_policy=FailurePolicy.CONTINUE)
    assert [outcome.kind for outcome in outcomes] == [
        CycleKind.EVALUATION_DEFERRED
    ]


def test_run_stops_when_acknowledgement_fails_then_recovers(
    tmp_path, base_inputs
):
    class AckFailingSource(PilotObservationSource):
        def acknowledge(self, sequence_number):
            raise RuntimeError("simulated ack transport outage")

    root = tmp_path / "run"
    source = AckFailingSource(
        [calm_batch(base_inputs), spike_batch(base_inputs)]
    )
    runtime = make_runtime(root, base_inputs, source)
    outcomes = runtime.run(5)
    assert [outcome.kind for outcome in outcomes] == [CycleKind.EVALUATED]
    assert not outcomes[0].acknowledged
    status = runtime.status()
    assert status.degraded
    assert status.last_failure_code == "acknowledge_failed"

    healthy = PilotObservationSource(
        [calm_batch(base_inputs), spike_batch(base_inputs)]
    )
    restarted = make_runtime(root, base_inputs, healthy)
    replay = restarted.run(3)
    assert [outcome.kind for outcome in replay] == [
        CycleKind.REPLAY_SKIPPED,
        CycleKind.EVALUATED,
        CycleKind.SOURCE_EXHAUSTED,
    ]
    assert healthy.acknowledged == [0, 1]


def test_continue_policy_stops_on_identical_retained_rejection(
    tmp_path, base_inputs
):
    source = PilotObservationSource(
        [
            calm_batch(base_inputs),
            spike_batch(base_inputs, sequence_number=5, cycle=1),
        ]
    )
    runtime = make_runtime(tmp_path, base_inputs, source)
    outcomes = runtime.run(
        max_cycles=None, failure_policy=FailurePolicy.CONTINUE
    )
    kinds = [outcome.kind for outcome in outcomes]
    assert kinds[0] is CycleKind.EVALUATED
    assert all(kind is CycleKind.REJECTED for kind in kinds[1:])
    assert len(kinds) <= 3  # bounded: no hot spin on the retained gap frame
    assert outcomes[-1].failure.code is FailureCode.INVALID_SEQUENCE


def test_source_peek_outage_surfaces_as_a_structured_rejection(
    tmp_path, base_inputs
):
    class OutageSource(PilotObservationSource):
        def __init__(self, batches):
            super().__init__(batches)
            self.failures_remaining = 1

        def observe(self):
            if self.failures_remaining:
                self.failures_remaining -= 1
                raise OSError("transient source outage")
            return super().observe()

    source = OutageSource([calm_batch(base_inputs)])
    runtime = make_runtime(tmp_path, base_inputs, source)
    first = runtime.run_once()
    assert first.kind is CycleKind.REJECTED
    assert first.failure.code is FailureCode.INGESTION_FAILED
    assert first.failure.retryable
    status = runtime.status()
    assert status.degraded
    assert status.last_failure_code == "ingestion_failed"
    second = runtime.run_once()
    assert second.kind is CycleKind.EVALUATED


# -- fail-closed validation must survive optimized Python ---------------------


class _MalformedEvaluation:
    """A cross-package contract violation: RECOMMEND with no recommendation."""

    from nxt_pilot_ops.contracts import EvaluationVerdict as _Verdict

    verdict = _Verdict.RECOMMEND
    recommendation = None


def _malformed_guardian():
    from nxt_pilot_ops.guardian import BallAvailabilityGuardian

    class MalformedGuardian(BallAvailabilityGuardian):
        def evaluate(self, snapshot):
            return _MalformedEvaluation()

    return MalformedGuardian()


def test_malformed_recommend_evaluation_fails_closed(tmp_path, base_inputs):
    root = tmp_path / "run"
    source = PilotObservationSource([spike_batch(base_inputs, sequence_number=0, cycle=0)])
    runtime = make_runtime(
        root, base_inputs, source, guardian=_malformed_guardian()
    )
    with pytest.raises(AgentRuntimeError) as raised:
        runtime.run_once()
    assert raised.value.incident_code == "evaluation_failed"
    assert "no recommendation" in raised.value.detail
    status = runtime.status()
    assert status.runtime_state is RuntimeState.FAILED
    assert status.last_failure_code == "evaluation_failed"
    # No partial evidence: no journal record, no ledger event, no ack, and
    # no pending evaluation checkpoint.
    paths = evidence_paths(root)
    assert not paths["journal"].exists()
    assert not paths["ledger"].exists()
    assert source.acknowledged == []
    checkpoint = JsonEvaluationCheckpointStore(
        paths["evaluation_checkpoints"]
    ).load(SITE_ID, DEPLOYMENT_ID)
    assert not checkpoint.has_pending_evaluation
    assert checkpoint.last_sequence is None


def test_malformed_recommend_fails_closed_under_optimized_python(tmp_path):
    """python -O strips asserts; the fail-closed path must not depend on
    them.  The probe runs the full composed runtime under -O and proves the
    documented incident still fires with no partial evidence."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    probe = textwrap.dedent(
        """
        import sys
        if sys.flags.optimize != 1:
            raise SystemExit("probe requires python -O")
        from pathlib import Path

        from nxt_range_ops.core.sim import RangeSimulation
        from nxt_range_ops.scenarios.generators import make_scenario
        from nxt_telemetry.bank import (
            SyntheticSensorBank,
            site_config_from_scenario,
        )
        from tests.agent_runtime import conftest as C
        from nxt_agent_runtime import AgentRuntimeError, RuntimeState
        from nxt_pilot_ops.contracts import EvaluationVerdict
        from nxt_pilot_ops.guardian import BallAvailabilityGuardian

        scenario = make_scenario("normal_weekday")
        sim = RangeSimulation(scenario, seed=C.PILOT_SEED)
        base = {
            "scenario": scenario,
            "frame": SyntheticSensorBank(sim).sample(),
            "site_config": site_config_from_scenario(
                scenario, seed=C.PILOT_SEED
            ),
        }

        class Malformed:
            verdict = EvaluationVerdict.RECOMMEND
            recommendation = None

        class MalformedGuardian(BallAvailabilityGuardian):
            def evaluate(self, snapshot):
                return Malformed()

        root = Path(sys.argv[1])
        source = C.PilotObservationSource(
            [C.spike_batch(base, sequence_number=0, cycle=0)]
        )
        runtime = C.make_runtime(
            root, base, source, guardian=MalformedGuardian()
        )
        try:
            runtime.run_once()
        except AgentRuntimeError as exc:
            if exc.incident_code != "evaluation_failed":
                raise SystemExit(f"wrong incident: {exc.incident_code}")
        else:
            raise SystemExit("malformed evaluation did not fail closed")
        status = runtime.status()
        if status.runtime_state is not RuntimeState.FAILED:
            raise SystemExit("runtime did not enter FAILED")
        paths = C.evidence_paths(root)
        if paths["journal"].exists() or paths["ledger"].exists():
            raise SystemExit("partial evidence was written")
        if source.acknowledged:
            raise SystemExit("malformed evaluation acknowledged the frame")
        print("OPTIMIZED-FAIL-CLOSED-OK")
        """
    )
    simulation_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-O", "-B", "-c", probe, str(tmp_path / "opt")],
        cwd=simulation_root,
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "PYTHONPATH": str(simulation_root),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "OPTIMIZED-FAIL-CLOSED-OK" in result.stdout


# -- incremental verification keeps failing closed ----------------------------


def test_journal_append_fails_closed_on_external_truncation(
    tmp_path, base_inputs
):
    root = tmp_path / "run"
    source = PilotObservationSource(
        [calm_batch(base_inputs), spike_batch(base_inputs)]
    )
    runtime = make_runtime(root, base_inputs, source)
    first = runtime.run_once()
    path = evidence_paths(root)["journal"]
    journal = EvaluationJournal(path)
    journal.read()  # adopt the verified state
    path.write_text("", encoding="utf-8")
    with pytest.raises(JournalIntegrityError, match="shrank"):
        journal.append(first.record)


def test_journal_append_fails_closed_on_tail_tampering(tmp_path, base_inputs):
    root = tmp_path / "run"
    runtime = make_runtime(
        root, base_inputs, PilotObservationSource([calm_batch(base_inputs)])
    )
    record = runtime.run_once().record
    path = evidence_paths(root)["journal"]
    journal = EvaluationJournal(path)
    journal.read()
    raw = path.read_bytes()
    path.write_bytes(raw[:-10] + b"x" * 9 + b"\n")
    with pytest.raises(JournalIntegrityError, match="tail changed"):
        journal.append(record)


def test_fresh_journal_instance_fully_verifies_on_first_append(
    tmp_path, base_inputs
):
    root = tmp_path / "run"
    runtime = make_runtime(
        root, base_inputs, PilotObservationSource([calm_batch(base_inputs)])
    )
    record = runtime.run_once().record
    path = evidence_paths(root)["journal"]
    tampered = path.read_text(encoding="utf-8").replace(
        '"verdict":"no_action"', '"verdict":"recommend"'
    )
    path.write_text(tampered, encoding="utf-8")
    fresh = EvaluationJournal(path)
    with pytest.raises(JournalIntegrityError):
        fresh.append(record)


def test_publisher_fails_closed_on_external_truncation(tmp_path, base_inputs):
    from nxt_agent_runtime import JsonlSnapshotPublisher, SnapshotPublisherError

    root = tmp_path / "run"
    source = PilotObservationSource([calm_batch(base_inputs)])
    runtime = make_runtime(root, base_inputs, source)
    runtime.run_once()
    path = evidence_paths(root)["snapshots"]
    publisher = JsonlSnapshotPublisher(path)
    assert len(publisher.published_envelope_ids()) == 1
    path.write_text("", encoding="utf-8")

    class StubEnvelope:
        envelope_id = "fse:" + "e" * 64

        def to_dict(self):
            return {"envelope_id": self.envelope_id}

    with pytest.raises(SnapshotPublisherError, match="shrank"):
        publisher.publish(StubEnvelope())


# -- POSIX-only platform contract ---------------------------------------------


def test_stores_fail_loudly_without_posix_fcntl(tmp_path, monkeypatch):
    import nxt_agent_runtime.checkpoints as checkpoints_module
    import nxt_agent_runtime.journal as journal_module
    import nxt_agent_runtime.publisher as publisher_module

    monkeypatch.setattr(journal_module, "_fcntl", None)
    monkeypatch.setattr(publisher_module, "_fcntl", None)
    monkeypatch.setattr(checkpoints_module, "_fcntl", None)
    with pytest.raises(RuntimeError, match="POSIX fcntl"):
        journal_module.EvaluationJournal(tmp_path / "j.jsonl")
    with pytest.raises(RuntimeError, match="POSIX fcntl"):
        publisher_module.JsonlSnapshotPublisher(tmp_path / "s.jsonl")
    with pytest.raises(RuntimeError, match="POSIX fcntl"):
        checkpoints_module.JsonEvaluationCheckpointStore(tmp_path / "cp")
