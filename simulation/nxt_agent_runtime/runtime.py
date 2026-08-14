"""NXTektal Agent Runtime V1: deterministic evaluation composition.

The runtime continuously composes existing contracts, owning none of
their semantics:

``ObservationSource -> SiteRuntimePipeline -> FacilitySnapshotEnvelope
-> nxt_pilot_ops.adapters.adapt_facility_state -> BallAvailabilityGuardian
-> PolicyEvaluation -> evaluation journal / Shadow Ops ledger -> human
workflow``.

Cross-layer ordering: the runtime hands the Site Runtime pipeline a
wrapper around the real at-least-once observation source that defers the
source acknowledgement until the evaluation lifecycle for the published
envelope has completed.  Terminal input rejections still delegate
immediately, so Site Runtime keeps sole ownership of ack/reject
semantics.  Because the assembler and the guardian are deterministic, an
un-acknowledged frame redelivered after a crash reproduces the identical
envelope (verified by the pipeline's replay-mismatch check) and the
identical evaluation, recommendation, trace, and evaluation IDs.

The runtime has no robot, ROS, actuator, navigation, or e-stop surface,
and no path to one: its only outputs are evaluation evidence, ledger
workflow records, and read-only status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, NoReturn

from nxt_pilot_ops.adapters import FacilityStateAdapterContext, adapt_facility_state
from nxt_pilot_ops.contracts import EvaluationVerdict
from nxt_pilot_ops.guardian import BallAvailabilityGuardian
from nxt_pilot_ops.ledger import (
    JsonlEventLedger,
    LedgerEvent,
    LedgerIntegrityError,
    LedgerTransitionError,
    recommendation_issued_event,
)
from nxt_pilot_ops.serialization import to_primitive
from nxt_site_runtime import SiteRuntimePipeline
from nxt_site_runtime.envelope import FacilitySnapshotEnvelope
from nxt_site_runtime.pipeline import (
    FailureCode,
    PipelineStage,
    RuntimeFailure,
    SiteRuntimeError,
)

from .checkpoints import (
    EvaluationCheckpoint,
    EvaluationCheckpointStore,
    InMemoryEvaluationCheckpointStore,
)
from .journal import EvaluationJournal, JournalIntegrityError
from .queue import ManagerDecisionQueue
from .records import EvaluationRecord
from .status import RuntimeState, RuntimeStatus

if TYPE_CHECKING:
    from nxt_site_runtime.checkpoints import CheckpointStore
    from nxt_site_runtime.pipeline import QualityGate, StateAssembler
    from nxt_site_runtime.ports import (
        ObservationSource,
        RuntimeSink,
        SequencedObservationFrame,
        StatePublisher,
    )
    from nxt_telemetry.observations import SiteConfig
else:
    CheckpointStore = Any
    QualityGate = Any
    StateAssembler = Any
    ObservationSource = Any
    RuntimeSink = Any
    SequencedObservationFrame = Any
    StatePublisher = Any
    SiteConfig = Any


class SourceExhausted(Exception):
    """A bounded observation source has no further frames to deliver."""


class AgentRuntimeError(RuntimeError):
    """A fail-closed runtime incident; the runtime refuses further cycles."""

    def __init__(self, incident_code: str, detail: str) -> None:
        super().__init__(f"{incident_code}: {detail}")
        self.incident_code = incident_code
        self.detail = detail


class FailurePolicy(StrEnum):
    """What ``run`` does after a cycle whose input was rejected upstream."""

    STOP = "stop"
    CONTINUE = "continue"


class CycleKind(StrEnum):
    EVALUATED = "evaluated"
    REPLAY_SKIPPED = "replay_skipped"
    REJECTED = "rejected"
    EVALUATION_DEFERRED = "evaluation_deferred"
    SOURCE_EXHAUSTED = "source_exhausted"


class _RetryableRuntimeFailure(Exception):
    """A transient store outage; the un-acknowledged frame will be
    redelivered, so the evaluation retries once the store recovers."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class CycleOutcome:
    """The result of one bounded runtime cycle."""

    kind: CycleKind
    sequence_number: int | None
    envelope_id: str | None
    evaluation_id: str | None
    record: EvaluationRecord | None
    failure: RuntimeFailure | None
    acknowledged: bool


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Read-only reconciliation of persistent stores after a (re)start."""

    site_has_pending_publish: bool
    evaluation_has_pending: bool
    pending_evaluation_sequence: int | None
    last_published_sequence: int | None
    last_evaluated_sequence: int | None
    ledger_record_count: int
    journal_record_count: int
    pending_decision_count: int


class _DeferredAcknowledgementSource:
    """Present the real source to the pipeline, but hold its acknowledgement.

    Rejections delegate immediately (Site Runtime owns terminal-input
    rejection).  Acknowledgements are recorded and delegated only after
    the runtime completes the evaluation lifecycle, so an at-least-once
    source retains any frame whose evaluation has not finished.
    """

    def __init__(self, source: ObservationSource) -> None:
        self._source = source
        self._pending_ack: int | None = None

    def observe(self) -> SequencedObservationFrame:
        return self._source.observe()

    def acknowledge(self, sequence_number: int) -> None:
        if self._pending_ack is not None:
            raise RuntimeError(
                "an acknowledgement is already pending; commit it first"
            )
        self._pending_ack = sequence_number

    def reject(self, sequence_number: int, reason: str) -> None:
        self._source.reject(sequence_number, reason)

    def reset(self) -> None:
        self._pending_ack = None

    def commit(self) -> int | None:
        if self._pending_ack is None:
            return None
        sequence_number = self._pending_ack
        self._source.acknowledge(sequence_number)
        self._pending_ack = None
        return sequence_number


class AgentRuntime:
    """Deterministic, restart-safe, human-in-the-loop evaluation runtime."""

    def __init__(
        self,
        *,
        site_id: str,
        deployment_id: str,
        site_config: SiteConfig,
        observation_source: ObservationSource,
        publisher: StatePublisher,
        ledger: JsonlEventLedger,
        journal: EvaluationJournal,
        simulation_midnight: datetime,
        clean_sensed_valid: bool,
        site_checkpoint_store: CheckpointStore | None = None,
        evaluation_checkpoint_store: EvaluationCheckpointStore | None = None,
        guardian: BallAvailabilityGuardian | None = None,
        quality_gate: QualityGate | None = None,
        runtime_sink: RuntimeSink | None = None,
        assembler: StateAssembler | None = None,
    ) -> None:
        for name, value in (("site_id", site_id), ("deployment_id", deployment_id)):
            if type(value) is not str or not value or value != value.strip():
                raise ValueError(f"{name} must be a non-blank trimmed string")
        if not isinstance(simulation_midnight, datetime):
            raise TypeError("simulation_midnight must be a datetime")
        if (
            simulation_midnight.tzinfo is None
            or simulation_midnight.utcoffset() is None
        ):
            raise ValueError("simulation_midnight must be timezone-aware")
        if (
            simulation_midnight.hour,
            simulation_midnight.minute,
            simulation_midnight.second,
            simulation_midnight.microsecond,
        ) != (0, 0, 0, 0):
            raise ValueError("simulation_midnight must be a calendar midnight")
        if type(clean_sensed_valid) is not bool:
            raise TypeError("clean_sensed_valid must be a boolean")
        if not isinstance(ledger, JsonlEventLedger):
            raise TypeError("ledger must be a JsonlEventLedger")
        if not isinstance(journal, EvaluationJournal):
            raise TypeError("journal must be an EvaluationJournal")
        if guardian is None:
            guardian = BallAvailabilityGuardian()
        elif not isinstance(guardian, BallAvailabilityGuardian):
            raise TypeError("guardian must be a BallAvailabilityGuardian or None")

        self._site_id = site_id
        self._deployment_id = deployment_id
        self._simulation_midnight = simulation_midnight
        self._clean_sensed_valid = clean_sensed_valid
        self._source = observation_source
        self._wrapped = _DeferredAcknowledgementSource(observation_source)
        self._pipeline = SiteRuntimePipeline(
            site_id=site_id,
            deployment_id=deployment_id,
            site_config=site_config,
            publisher=publisher,
            observation_source=self._wrapped,
            checkpoint_store=site_checkpoint_store,
            runtime_sink=runtime_sink,
            quality_gate=quality_gate,
            assembler=assembler,
        )
        self._evaluation_store: EvaluationCheckpointStore = (
            evaluation_checkpoint_store
            if evaluation_checkpoint_store is not None
            else InMemoryEvaluationCheckpointStore()
        )
        self._guardian = guardian
        self._ledger = ledger
        self._journal = journal
        self._queue = ManagerDecisionQueue(
            ledger=ledger,
            journal=journal,
            site_id=site_id,
            deployment_id=deployment_id,
        )

        self._state = RuntimeState.CREATED
        self._stop_requested = False
        self._recovered = False
        self._degraded = False
        self._source_exhausted = False
        self._cycles_completed = 0
        self._evaluations_completed = 0
        self._last_observed_sequence: int | None = None
        self._last_effective_confidence: float | None = None
        self._last_failure_code: str | None = None
        self._last_failure_detail: str | None = None

    @property
    def queue(self) -> ManagerDecisionQueue:
        """The pending manager-decision view over existing records."""
        return self._queue

    # -- lifecycle ---------------------------------------------------------

    def request_stop(self) -> None:
        """Ask the runtime to stop before the next cycle.  Idempotent."""
        self._stop_requested = True
        if self._state is not RuntimeState.FAILED:
            self._state = RuntimeState.STOPPED

    def recover(self) -> RecoveryReport:
        """Verify persistent stores and reconcile the two checkpoint layers.

        Recovery is read-only: pending publications and pending
        evaluations are completed by the normal cycle path when the
        at-least-once source redelivers the un-acknowledged frame.
        """
        self._ensure_runnable()
        try:
            ledger_count, _ = self._ledger.verify()
            ledger_records = self._ledger.records()
            journal_records = self._journal.read()
        except ValueError as exc:
            self._fail_closed("evidence_verification_failed", str(exc))
        except Exception as exc:
            self._record_failure("recovery_unavailable", str(exc))
            raise AgentRuntimeError("recovery_unavailable", str(exc)) from exc
        try:
            site = self._load_site_checkpoint()
            evaluation = self._load_evaluation_checkpoint()
        except AgentRuntimeError:
            raise
        except Exception as exc:
            self._record_failure("recovery_unavailable", str(exc))
            raise AgentRuntimeError("recovery_unavailable", str(exc)) from exc

        for record in journal_records:
            if (record.site_id, record.deployment_id) != (
                self._site_id,
                self._deployment_id,
            ):
                self._fail_closed(
                    "journal_identity_mismatch",
                    "journal records do not belong to this runtime identity",
                )
        for ledger_record in ledger_records:
            if (ledger_record.site_id, ledger_record.deployment_id) != (
                self._site_id,
                self._deployment_id,
            ):
                self._fail_closed(
                    "ledger_identity_mismatch",
                    "ledger records do not belong to this runtime identity; "
                    "evidence stores are per (site_id, deployment_id)",
                )
        if (
            evaluation.last_sequence is not None
            and site.last_successful_sequence is not None
            and evaluation.last_sequence
            not in (site.last_successful_sequence, site.last_successful_sequence - 1)
        ):
            self._fail_closed(
                "checkpoint_divergence",
                "evaluation progress does not align with state publication "
                f"(evaluated {evaluation.last_sequence}, published "
                f"{site.last_successful_sequence})",
            )
        if (
            evaluation.pending_sequence is not None
            and site.last_successful_sequence is not None
            and evaluation.pending_sequence != site.last_successful_sequence
        ):
            self._fail_closed(
                "checkpoint_divergence",
                "pending evaluation does not reference the last published "
                f"sequence (pending {evaluation.pending_sequence}, published "
                f"{site.last_successful_sequence})",
            )
        if journal_records:
            newest = journal_records[-1].sequence_number
            expected_max = evaluation.pending_sequence
            if expected_max is None:
                expected_max = evaluation.last_sequence
            if expected_max is None or newest > expected_max:
                self._fail_closed(
                    "journal_divergence",
                    f"journal records sequence {newest} beyond the "
                    "evaluation checkpoint",
                )
        if evaluation.last_sequence is not None:
            # The completed evaluation must be durably journaled; a missing
            # or truncated journal behind the checkpoint is lost evidence.
            completed = next(
                (
                    record
                    for record in journal_records
                    if record.sequence_number == evaluation.last_sequence
                ),
                None,
            )
            if (
                completed is None
                or completed.evaluation_id != evaluation.last_evaluation_id
            ):
                self._fail_closed(
                    "journal_divergence",
                    "the evaluation checkpoint records a completed "
                    f"evaluation for sequence {evaluation.last_sequence} "
                    "that the journal does not contain",
                )
        self._recovered = True
        return RecoveryReport(
            site_has_pending_publish=site.has_pending_publish,
            evaluation_has_pending=evaluation.has_pending_evaluation,
            pending_evaluation_sequence=evaluation.pending_sequence,
            last_published_sequence=site.last_successful_sequence,
            last_evaluated_sequence=evaluation.last_sequence,
            ledger_record_count=ledger_count,
            journal_record_count=len(journal_records),
            pending_decision_count=len(self._queue.pending()),
        )

    def run_once(self) -> CycleOutcome:
        """Run one bounded cycle: observe, publish, evaluate, acknowledge."""
        self._ensure_runnable()
        if not self._recovered:
            try:
                self.recover()
            except AgentRuntimeError as exc:
                if exc.incident_code != "recovery_unavailable":
                    raise
                # A transient store outage: continue into the cycle, which
                # surfaces the outage through the pipeline's own retryable
                # taxonomy; full reconciliation reruns on the next cycle.
        self._state = RuntimeState.RUNNING

        try:
            batch = self._source.observe()
        except SourceExhausted:
            self._source_exhausted = True
            self._cycles_completed += 1
            return CycleOutcome(
                kind=CycleKind.SOURCE_EXHAUSTED,
                sequence_number=None,
                envelope_id=None,
                evaluation_id=None,
                record=None,
                failure=None,
                acknowledged=False,
            )
        except Exception as exc:
            # A transient source outage at the peek gets the same structured
            # retryable taxonomy the pipeline applies to its own observe call.
            failure = RuntimeFailure(
                code=FailureCode.INGESTION_FAILED,
                stage=PipelineStage.OBSERVE,
                site_id=self._site_id,
                deployment_id=self._deployment_id,
                detail=f"{type(exc).__name__}: {exc}",
                retryable=True,
            )
            self._record_failure(failure.code.value, failure.detail)
            self._cycles_completed += 1
            return CycleOutcome(
                kind=CycleKind.REJECTED,
                sequence_number=None,
                envelope_id=None,
                evaluation_id=None,
                record=None,
                failure=failure,
                acknowledged=False,
            )
        self._source_exhausted = False
        observed_sequence = getattr(batch, "sequence_number", None)
        if isinstance(observed_sequence, int) and not isinstance(
            observed_sequence, bool
        ):
            self._last_observed_sequence = observed_sequence

        self._wrapped.reset()
        try:
            envelope = self._pipeline.run_once()
        except SiteRuntimeError as exc:
            self._record_failure(exc.failure.code.value, exc.failure.detail)
            self._cycles_completed += 1
            return CycleOutcome(
                kind=CycleKind.REJECTED,
                sequence_number=exc.failure.sequence_number,
                envelope_id=None,
                evaluation_id=None,
                record=None,
                failure=exc.failure,
                acknowledged=False,
            )

        self._last_effective_confidence = (
            envelope.runtime_quality.effective_confidence
        )
        try:
            kind, record, evaluation_id = self._evaluate_admitted(envelope)
        except _RetryableRuntimeFailure as exc:
            # The frame stays un-acknowledged; the at-least-once source
            # redelivers it and the evaluation retries deterministically.
            self._record_failure(exc.code, exc.detail)
            self._cycles_completed += 1
            return CycleOutcome(
                kind=CycleKind.EVALUATION_DEFERRED,
                sequence_number=envelope.sequence_number,
                envelope_id=envelope.envelope_id,
                evaluation_id=None,
                record=None,
                failure=None,
                acknowledged=False,
            )

        acknowledged = True
        try:
            self._wrapped.commit()
        except Exception as exc:
            acknowledged = False
            self._record_failure(
                "acknowledge_failed", f"{type(exc).__name__}: {exc}"
            )
        else:
            self._degraded = False
            self._last_failure_code = None
            self._last_failure_detail = None

        if kind is CycleKind.EVALUATED:
            self._evaluations_completed += 1
        self._cycles_completed += 1
        return CycleOutcome(
            kind=kind,
            sequence_number=envelope.sequence_number,
            envelope_id=envelope.envelope_id,
            evaluation_id=evaluation_id,
            record=record,
            failure=None,
            acknowledged=acknowledged,
        )

    def run(
        self,
        max_cycles: int | None = None,
        *,
        failure_policy: FailurePolicy = FailurePolicy.STOP,
    ) -> tuple[CycleOutcome, ...]:
        """Run bounded cycles until exhaustion, stop request, or the bound.

        ``max_cycles=None`` runs until the source is exhausted or a stop
        is requested; the source must either block in ``observe`` or
        raise :class:`SourceExhausted`, so there is no polling loop.  The
        failure policy is explicit: ``STOP`` (default) ends the run after
        a rejected cycle so a retained bad frame can never spin the loop;
        ``CONTINUE`` proceeds, for sources that discard terminal input.
        Under ``CONTINUE`` the run still ends after two consecutive
        identical rejections, because a retained frame can make no
        progress without external remediation.
        """
        if max_cycles is not None and (
            isinstance(max_cycles, bool)
            or not isinstance(max_cycles, int)
            or max_cycles < 0
        ):
            raise ValueError("max_cycles must be a non-negative integer or None")
        if not isinstance(failure_policy, FailurePolicy):
            raise TypeError("failure_policy must be a FailurePolicy")
        outcomes: list[CycleOutcome] = []
        previous_rejection: tuple | None = None
        while max_cycles is None or len(outcomes) < max_cycles:
            if self._stop_requested:
                break
            outcome = self.run_once()
            outcomes.append(outcome)
            if outcome.kind is CycleKind.SOURCE_EXHAUSTED:
                break
            if outcome.kind is CycleKind.REJECTED:
                if failure_policy is FailurePolicy.STOP:
                    break
                rejection = (
                    None
                    if outcome.failure is None
                    else (
                        outcome.failure.code,
                        outcome.failure.sequence_number,
                        outcome.failure.detail,
                    )
                )
                if rejection is not None and rejection == previous_rejection:
                    # The source retained the frame and nothing changed; a
                    # CONTINUE loop would spin on the identical rejection.
                    break
                previous_rejection = rejection
                continue
            previous_rejection = None
            if outcome.kind is CycleKind.EVALUATION_DEFERRED:
                # No progress is possible until the store recovers; a loop
                # here would re-observe the identical retained frame.
                break
            if (
                outcome.kind
                in (CycleKind.EVALUATED, CycleKind.REPLAY_SKIPPED)
                and not outcome.acknowledged
            ):
                break
        return tuple(outcomes)

    def status(self) -> RuntimeStatus:
        """Read-only structured health/status.  Never a decision input.

        Status must stay readable even when a persistent store is broken,
        so store reads degrade to ``None`` fields instead of raising; the
        recorded failure code still names the underlying incident.
        """
        try:
            site = self._load_site_checkpoint()
        except Exception:
            site = None
        try:
            evaluation = self._evaluation_store.load(
                self._site_id, self._deployment_id
            )
            if not isinstance(evaluation, EvaluationCheckpoint):
                evaluation = None
        except Exception:
            evaluation = None
        try:
            pending = self._queue.pending()
        except Exception:
            pending = ()
        try:
            latest = self._journal.latest()
        except Exception:
            latest = None
        return RuntimeStatus(
            site_id=self._site_id,
            deployment_id=self._deployment_id,
            runtime_state=self._state,
            degraded=self._degraded,
            cycles_completed=self._cycles_completed,
            evaluations_completed=self._evaluations_completed,
            source_exhausted=self._source_exhausted,
            last_observed_sequence=self._last_observed_sequence,
            last_published_sequence=(
                None if site is None else site.last_successful_sequence
            ),
            last_published_envelope_id=(
                None if site is None else site.last_envelope_id
            ),
            site_checkpoint_has_pending_publish=(
                False if site is None else site.has_pending_publish
            ),
            last_evaluated_sequence=(
                None if evaluation is None else evaluation.last_sequence
            ),
            last_evaluated_envelope_id=(
                None if evaluation is None else evaluation.last_envelope_id
            ),
            last_evaluation_id=(
                None if evaluation is None else evaluation.last_evaluation_id
            ),
            last_verdict=None if latest is None else latest.verdict.value,
            evaluation_checkpoint_has_pending=(
                False
                if evaluation is None
                else evaluation.has_pending_evaluation
            ),
            pending_decision_count=len(pending),
            deferred_decision_count=sum(
                1 for entry in pending if entry.deferred_until is not None
            ),
            last_observation_timestamp_s=(
                None if site is None else site.last_observation_timestamp_s
            ),
            last_effective_confidence=self._last_effective_confidence,
            last_failure_code=self._last_failure_code,
            last_failure_detail=self._last_failure_detail,
        )

    # -- evaluation lifecycle ---------------------------------------------

    def _evaluate_admitted(
        self, envelope: FacilitySnapshotEnvelope
    ) -> tuple[CycleKind, EvaluationRecord | None, str | None]:
        try:
            checkpoint = self._load_evaluation_checkpoint()
        except AgentRuntimeError:
            raise
        except Exception as exc:
            raise _RetryableRuntimeFailure(
                "evaluation_checkpoint_unavailable",
                f"{type(exc).__name__}: {exc}",
            ) from exc
        sequence = envelope.sequence_number

        if checkpoint.last_sequence == sequence:
            if checkpoint.last_envelope_id != envelope.envelope_id:
                self._fail_closed(
                    "evaluation_replay_mismatch",
                    f"sequence {sequence} was evaluated for envelope "
                    f"{checkpoint.last_envelope_id}, not {envelope.envelope_id}",
                )
            return (
                CycleKind.REPLAY_SKIPPED,
                None,
                checkpoint.last_evaluation_id,
            )
        if (
            checkpoint.pending_sequence is not None
            and checkpoint.pending_sequence != sequence
        ):
            self._fail_closed(
                "evaluation_replay_mismatch",
                f"pending evaluation expects sequence "
                f"{checkpoint.pending_sequence}, received {sequence}",
            )
        if (
            checkpoint.pending_sequence is None
            and checkpoint.last_sequence is not None
            and sequence != checkpoint.last_sequence + 1
        ):
            self._fail_closed(
                "evaluation_sequence_gap",
                f"sequence {sequence} does not follow the last evaluated "
                f"sequence {checkpoint.last_sequence}",
            )
        if (
            checkpoint.pending_sequence == sequence
            and checkpoint.pending_envelope_id != envelope.envelope_id
        ):
            self._fail_closed(
                "evaluation_replay_mismatch",
                f"pending evaluation for sequence {sequence} references "
                f"envelope {checkpoint.pending_envelope_id}, not "
                f"{envelope.envelope_id}",
            )

        try:
            snapshot = adapt_facility_state(
                envelope.facility_state,
                context=FacilityStateAdapterContext(
                    site_id=self._site_id,
                    deployment_id=self._deployment_id,
                    simulation_midnight=self._simulation_midnight,
                    source_sequence=sequence,
                    source_ref=envelope.payload_reference,
                    clean_sensed_valid=self._clean_sensed_valid,
                ),
            )
            evaluation = self._guardian.evaluate(snapshot)
        except AgentRuntimeError:
            raise
        except Exception as exc:
            self._fail_closed(
                "evaluation_failed", f"{type(exc).__name__}: {exc}"
            )

        if evaluation.verdict is EvaluationVerdict.RECOMMEND:
            # Explicit runtime validation, never an assert: optimized Python
            # strips asserts, and a malformed cross-package result must take
            # the documented fail-closed path before any evidence is written.
            if evaluation.recommendation is None:
                self._fail_closed(
                    "evaluation_failed",
                    "RECOMMEND evaluation carries no recommendation",
                )
            event: LedgerEvent | None = recommendation_issued_event(
                evaluation.recommendation, evaluation.trace
            )
            record = EvaluationRecord.create(
                site_id=self._site_id,
                deployment_id=self._deployment_id,
                sequence_number=sequence,
                envelope_id=envelope.envelope_id,
                observation_timestamp_s=envelope.observation_timestamp_s,
                observed_at=snapshot.observed_at,
                verdict=evaluation.verdict,
                policy_id=evaluation.trace.policy_id,
                policy_version=evaluation.trace.policy_version,
                trace_id=evaluation.trace.trace_id,
                snapshot_digest=evaluation.trace.snapshot_digest,
                recommendation_id=evaluation.recommendation.recommendation_id,
                recommendation_action=evaluation.recommendation.action,
                ledger_event_id=event.event_id,
                decision_trace=None,
            )
        else:
            event = None
            record = EvaluationRecord.create(
                site_id=self._site_id,
                deployment_id=self._deployment_id,
                sequence_number=sequence,
                envelope_id=envelope.envelope_id,
                observation_timestamp_s=envelope.observation_timestamp_s,
                observed_at=snapshot.observed_at,
                verdict=evaluation.verdict,
                policy_id=evaluation.trace.policy_id,
                policy_version=evaluation.trace.policy_version,
                trace_id=evaluation.trace.trace_id,
                snapshot_digest=evaluation.trace.snapshot_digest,
                recommendation_id=None,
                recommendation_action=None,
                ledger_event_id=None,
                decision_trace=to_primitive(evaluation.trace),
            )

        if checkpoint.pending_sequence == sequence and (
            checkpoint.pending_evaluation_id != record.evaluation_id
        ):
            self._fail_closed(
                "evaluation_replay_mismatch",
                f"pending evaluation for sequence {sequence} recomputed with "
                "different content; refusing to replace evidence",
            )

        try:
            prepared = checkpoint.prepare(
                sequence_number=sequence,
                observation_timestamp_s=envelope.observation_timestamp_s,
                envelope_id=envelope.envelope_id,
                evaluation_id=record.evaluation_id,
            )
        except Exception as exc:
            self._fail_closed(
                "evaluation_checkpoint_failed", f"{type(exc).__name__}: {exc}"
            )
        try:
            self._evaluation_store.compare_and_save(checkpoint, prepared)
        except Exception as exc:
            raise _RetryableRuntimeFailure(
                "evaluation_checkpoint_unavailable",
                f"{type(exc).__name__}: {exc}",
            ) from exc

        if event is not None:
            self._append_ledger_idempotent(event)
        try:
            self._journal.append(record)
        except JournalIntegrityError as exc:
            self._fail_closed("journal_divergence", str(exc))
        except Exception as exc:
            raise _RetryableRuntimeFailure(
                "journal_unavailable", f"{type(exc).__name__}: {exc}"
            ) from exc

        try:
            completed = prepared.complete()
            self._evaluation_store.compare_and_save(prepared, completed)
        except Exception as exc:
            raise _RetryableRuntimeFailure(
                "evaluation_checkpoint_unavailable",
                f"{type(exc).__name__}: {exc}",
            ) from exc
        return CycleKind.EVALUATED, record, record.evaluation_id

    def _append_ledger_idempotent(self, event: LedgerEvent) -> None:
        try:
            if any(
                record.event_id == event.event_id
                for record in self._ledger.records()
            ):
                return
            self._ledger.append(event)
        except (LedgerTransitionError, LedgerIntegrityError) as exc:
            self._fail_closed("ledger_transition_rejected", str(exc))
        except AgentRuntimeError:
            raise
        except Exception as exc:
            raise _RetryableRuntimeFailure(
                "ledger_unavailable", f"{type(exc).__name__}: {exc}"
            ) from exc

    # -- internal helpers --------------------------------------------------

    def _ensure_runnable(self) -> None:
        if self._state is RuntimeState.FAILED:
            raise AgentRuntimeError(
                "runtime_failed",
                "the runtime failed closed; repair its stores and start a "
                "new runtime instance",
            )
        if self._state is RuntimeState.STOPPED or self._stop_requested:
            raise AgentRuntimeError(
                "runtime_stopped", "the runtime has been stopped"
            )

    def _load_site_checkpoint(self):
        return self._pipeline.checkpoint_store.load(
            self._site_id, self._deployment_id
        )

    def _load_evaluation_checkpoint(self) -> EvaluationCheckpoint:
        checkpoint = self._evaluation_store.load(
            self._site_id, self._deployment_id
        )
        if not isinstance(checkpoint, EvaluationCheckpoint):
            self._fail_closed(
                "evaluation_checkpoint_failed",
                "evaluation checkpoint store returned the wrong contract",
            )
        if (checkpoint.site_id, checkpoint.deployment_id) != (
            self._site_id,
            self._deployment_id,
        ):
            self._fail_closed(
                "evaluation_checkpoint_failed",
                "evaluation checkpoint identity does not match runtime",
            )
        return checkpoint

    def _record_failure(self, code: str, detail: str) -> None:
        self._degraded = True
        self._last_failure_code = code
        self._last_failure_detail = detail

    def _fail_closed(self, incident_code: str, detail: str) -> NoReturn:
        self._record_failure(incident_code, detail)
        self._state = RuntimeState.FAILED
        raise AgentRuntimeError(incident_code, detail)
