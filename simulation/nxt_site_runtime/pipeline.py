"""Observation -> validation -> assembly -> quality gate -> publication."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import TYPE_CHECKING, Any, Protocol

from nxt_facility.state import FacilityState
from nxt_telemetry.observations import (
    Observation,
    ObservationFrame,
    ObservationStatus,
    SiteConfig,
    SourceType,
    UpstreamInputs,
)

from .checkpoints import (
    CheckpointError,
    CheckpointStore,
    InMemoryCheckpointStore,
    RuntimeCheckpoint,
)
from .envelope import (
    FacilitySnapshotEnvelope,
    RuntimeQuality,
    SourceReference,
    canonical_json,
)
from .ports import (
    ObservationSource,
    RuntimeSink,
    SequencedObservationFrame,
    StatePublisher,
)

if TYPE_CHECKING:
    from nxt_telemetry.assemble import AssemblyReport
else:
    AssemblyReport = Any


class FailureCode(str, Enum):
    MISSING_IDENTITY = "missing_identity"
    INGESTION_FAILED = "ingestion_failed"
    INVALID_OBSERVATION = "invalid_observation"
    INVALID_SEQUENCE = "invalid_sequence"
    STALE_OBSERVATION = "stale_observation"
    ASSEMBLY_FAILED = "assembly_failed"
    INSUFFICIENT_QUALITY = "insufficient_data_quality"
    REPLAY_MISMATCH = "replay_mismatch"
    PUBLISH_FAILED = "publish_failed"
    CHECKPOINT_FAILED = "checkpoint_failed"


class PipelineStage(str, Enum):
    OBSERVE = "observe"
    VALIDATE = "validate"
    ASSEMBLE = "assemble"
    QUALITY_GATE = "quality_gate"
    CHECKPOINT = "checkpoint"
    PUBLISH = "publish"


class _SequenceMode(str, Enum):
    NEW = "new"
    PENDING_REPLAY = "pending_replay"
    COMPLETED_REPLAY = "completed_replay"


_DISCARDABLE_INPUT_FAILURES = frozenset(
    {
        FailureCode.INVALID_OBSERVATION,
        FailureCode.STALE_OBSERVATION,
        FailureCode.INSUFFICIENT_QUALITY,
    }
)


@dataclass(frozen=True)
class RuntimeFailure:
    code: FailureCode
    stage: PipelineStage
    site_id: str
    deployment_id: str
    detail: str
    sequence_number: int | None = None
    observation_timestamp_s: float | None = None
    retryable: bool = False


class SiteRuntimeError(RuntimeError):
    def __init__(self, failure: RuntimeFailure):
        super().__init__(f"{failure.code.value}: {failure.detail}")
        self.failure = failure


@dataclass(frozen=True)
class QualityGate:
    """Mechanical thresholds over existing input and assembly quality.

    Runtime v0 always rejects missing or stale inputs.  That invariant avoids
    publishing assembler fallback defaults as current facility truth.
    """

    minimum_effective_confidence: float = 0.8
    maximum_consistency_issues: int = 0
    accepted_provenance_grades: tuple[str, ...] = ("high", "medium")
    maximum_observation_age_s: float = 900.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.minimum_effective_confidence, bool)
            or not isinstance(self.minimum_effective_confidence, (int, float))
            or not math.isfinite(self.minimum_effective_confidence)
            or not 0.0 <= self.minimum_effective_confidence <= 1.0
        ):
            raise ValueError("minimum_effective_confidence must be in [0, 1]")
        if (
            isinstance(self.maximum_consistency_issues, bool)
            or not isinstance(self.maximum_consistency_issues, int)
            or self.maximum_consistency_issues < 0
        ):
            raise ValueError(
                "maximum_consistency_issues must be a non-negative integer"
            )
        grades = self.accepted_provenance_grades
        if not grades or any(
            not isinstance(grade, str) or not grade or grade != grade.strip()
            for grade in grades
        ):
            raise ValueError(
                "accepted_provenance_grades must contain non-blank trimmed strings"
            )
        if len(grades) != len(set(grades)):
            raise ValueError("accepted_provenance_grades must be unique")
        if (
            isinstance(self.maximum_observation_age_s, bool)
            or not isinstance(self.maximum_observation_age_s, (int, float))
            or not math.isfinite(self.maximum_observation_age_s)
            or self.maximum_observation_age_s < 0
        ):
            raise ValueError(
                "maximum_observation_age_s must be finite and non-negative"
            )


class StateAssembler(Protocol):
    def __call__(
        self,
        frame: ObservationFrame,
        site: SiteConfig,
        upstream: UpstreamInputs,
    ) -> tuple[FacilityState, AssemblyReport]:
        ...


@dataclass(frozen=True)
class _InputAssessment:
    source_references: tuple[SourceReference, ...]
    stale_references: tuple[str, ...]
    missing_references: tuple[str, ...]
    upstream_confidence: float


def _observation_reference(observation: Observation) -> SourceReference:
    return SourceReference(
        source_type=observation.source_type.value,
        source_id=observation.source_id,
        channel=observation.channel,
        reference_id=observation.observation_id,
        sample_timestamp_s=observation.sample_timestamp_s,
        available_timestamp_s=observation.available_timestamp_s,
        confidence=observation.confidence,
        status=observation.status.value,
        calibration_id=observation.calibration_id,
    )


class SiteRuntimePipeline:
    """Coordinates existing contracts without owning their business logic."""

    def __init__(
        self,
        *,
        site_id: str,
        deployment_id: str,
        site_config: SiteConfig,
        publisher: StatePublisher,
        observation_source: ObservationSource | None = None,
        checkpoint_store: CheckpointStore | None = None,
        runtime_sink: RuntimeSink | None = None,
        quality_gate: QualityGate | None = None,
        assembler: StateAssembler | None = None,
    ) -> None:
        self.site_id = site_id
        self.deployment_id = deployment_id
        self.site_config = site_config
        self.publisher = publisher
        self.observation_source = observation_source
        self.checkpoint_store = (
            checkpoint_store
            if checkpoint_store is not None
            else InMemoryCheckpointStore()
        )
        self.runtime_sink = runtime_sink
        self.quality_gate = quality_gate if quality_gate is not None else QualityGate()
        self.assembler = assembler
        self._process_lock = RLock()

    def run_once(self) -> FacilitySnapshotEnvelope:
        """Peek one source batch, process it, then explicitly ack or reject."""
        self._validate_identity()
        if self.observation_source is None:
            self._fail(
                FailureCode.INGESTION_FAILED,
                PipelineStage.OBSERVE,
                "run_once requires an observation source",
            )
        try:
            batch = self.observation_source.observe()
        except Exception as exc:
            self._fail(
                FailureCode.INGESTION_FAILED,
                PipelineStage.OBSERVE,
                f"{type(exc).__name__}: {exc}",
                retryable=True,
            )

        try:
            envelope = self.process(batch)
        except SiteRuntimeError as original:
            sequence_number = getattr(batch, "sequence_number", None)
            if self._should_reject_source_input(batch, original.failure):
                try:
                    self.observation_source.reject(
                        sequence_number, original.failure.code.value
                    )
                except Exception as exc:
                    self._fail(
                        FailureCode.INGESTION_FAILED,
                        PipelineStage.OBSERVE,
                        "source could not reject terminal input after "
                        f"{original.failure.code.value}: {type(exc).__name__}: {exc}",
                        sequence_number=sequence_number,
                        observation_timestamp_s=original.failure.observation_timestamp_s,
                        retryable=True,
                    )
            raise

        try:
            self.observation_source.acknowledge(batch.sequence_number)
        except Exception as exc:
            self._fail(
                FailureCode.INGESTION_FAILED,
                PipelineStage.OBSERVE,
                f"source acknowledgement failed: {type(exc).__name__}: {exc}",
                sequence_number=batch.sequence_number,
                observation_timestamp_s=batch.frame.t_s,
                retryable=True,
            )
        return envelope

    def _should_reject_source_input(
        self,
        batch: object,
        failure: RuntimeFailure,
    ) -> bool:
        """Discard only a bad frame occupying the next publish position.

        Gaps and replay mismatches are recovery incidents.  Rejecting either
        would resequence a later frame and silently lose the missing/replayed
        evidence, so an at-least-once source must retain them for operator or
        adapter remediation.
        """
        if (
            failure.retryable
            or failure.code not in _DISCARDABLE_INPUT_FAILURES
            or not isinstance(batch, SequencedObservationFrame)
        ):
            return False
        sequence_number = batch.sequence_number
        try:
            checkpoint = self.checkpoint_store.load(
                self.site_id, self.deployment_id
            )
        except Exception:
            return False
        if (
            not isinstance(checkpoint, RuntimeCheckpoint)
            or (checkpoint.site_id, checkpoint.deployment_id)
            != (self.site_id, self.deployment_id)
            or checkpoint.has_pending_publish
        ):
            return False
        expected = checkpoint.next_sequence
        return expected is None or sequence_number == expected

    def process(
        self, batch: SequencedObservationFrame
    ) -> FacilitySnapshotEnvelope:
        """Process one immutable batch; push adapters may call this directly."""
        with self._process_lock:
            return self._process_locked(batch)

    def _process_locked(
        self, batch: SequencedObservationFrame
    ) -> FacilitySnapshotEnvelope:
        self._validate_identity()
        if not isinstance(batch, SequencedObservationFrame):
            self._fail(
                FailureCode.INVALID_OBSERVATION,
                PipelineStage.VALIDATE,
                "batch must be a SequencedObservationFrame",
            )
        sequence_number = batch.sequence_number
        assessment = self._validate_inputs(batch)
        checkpoint = self._load_checkpoint(sequence_number, batch.frame.t_s)
        mode = self._sequence_mode(
            checkpoint, sequence_number, batch.frame.t_s
        )

        state, report = self._assemble(batch)
        quality = self._apply_quality_gate(
            report,
            assessment=assessment,
            sequence_number=sequence_number,
            observation_timestamp_s=batch.frame.t_s,
            enforce=mode is _SequenceMode.NEW,
        )
        try:
            envelope = FacilitySnapshotEnvelope(
                site_id=self.site_id,
                deployment_id=self.deployment_id,
                observation_timestamp_s=batch.frame.t_s,
                sequence_number=sequence_number,
                facility_state=state,
                assembly_report=report,
                runtime_quality=quality,
                source_references=assessment.source_references,
            )
            # Construction snapshots the exact canonical id before checkpointing.
            _ = envelope.envelope_id
        except Exception as exc:
            self._fail(
                FailureCode.ASSEMBLY_FAILED,
                PipelineStage.ASSEMBLE,
                f"assembled snapshot is not publishable: {type(exc).__name__}: {exc}",
                sequence_number=sequence_number,
                observation_timestamp_s=batch.frame.t_s,
                retryable=True,
            )

        if mode is _SequenceMode.COMPLETED_REPLAY:
            if envelope.envelope_id != checkpoint.last_envelope_id:
                self._fail(
                    FailureCode.REPLAY_MISMATCH,
                    PipelineStage.CHECKPOINT,
                    "completed sequence replayed with different content",
                    sequence_number=sequence_number,
                    observation_timestamp_s=batch.frame.t_s,
                )
            return envelope

        try:
            prepared = checkpoint.prepare(
                sequence_number=sequence_number,
                observation_timestamp_s=batch.frame.t_s,
                envelope_id=envelope.envelope_id,
            )
        except CheckpointError as exc:
            self._fail(
                FailureCode.REPLAY_MISMATCH,
                PipelineStage.CHECKPOINT,
                str(exc),
                sequence_number=sequence_number,
                observation_timestamp_s=batch.frame.t_s,
            )
        self._save_transition(
            checkpoint, prepared, sequence_number, batch.frame.t_s
        )

        try:
            self.publisher.publish(envelope)
        except Exception as exc:
            self._fail(
                FailureCode.PUBLISH_FAILED,
                PipelineStage.PUBLISH,
                f"{type(exc).__name__}: {exc}",
                sequence_number=sequence_number,
                observation_timestamp_s=batch.frame.t_s,
                retryable=True,
            )

        completed = prepared.complete()
        self._save_transition(
            prepared, completed, sequence_number, batch.frame.t_s
        )
        self._notify_published(envelope)
        return envelope

    def _validate_identity(self) -> None:
        missing = [
            name
            for name, value in (
                ("site_id", self.site_id),
                ("deployment_id", self.deployment_id),
            )
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            self._fail(
                FailureCode.MISSING_IDENTITY,
                PipelineStage.VALIDATE,
                f"missing runtime identity: {', '.join(missing)}",
            )
        if (
            self.site_id != self.site_id.strip()
            or self.deployment_id != self.deployment_id.strip()
        ):
            self._fail(
                FailureCode.MISSING_IDENTITY,
                PipelineStage.VALIDATE,
                "runtime identity must be trimmed",
            )

    def _validate_inputs(self, batch: SequencedObservationFrame) -> _InputAssessment:
        frame = batch.frame
        if not isinstance(frame, ObservationFrame):
            self._invalid_observation(
                "batch.frame must be an ObservationFrame",
                batch.sequence_number,
                None,
            )
        self._valid_timestamp("frame timestamp", frame.t_s, batch.sequence_number)
        if not frame.observations:
            self._invalid_observation(
                "observation frame must not be empty",
                batch.sequence_number,
                frame.t_s,
            )
        if any(not isinstance(item, Observation) for item in frame.observations):
            self._invalid_observation(
                "frame entries must be Observation instances",
                batch.sequence_number,
                frame.t_s,
            )
        references: list[SourceReference] = []
        stale: list[str] = []
        missing: list[str] = []
        for observation in frame.observations:
            self._validate_observation(observation, frame, batch.sequence_number)
            reference = _observation_reference(observation)
            references.append(reference)
            age = frame.t_s - observation.sample_timestamp_s
            if (
                observation.status is ObservationStatus.STALE
                or age > self.quality_gate.maximum_observation_age_s
            ):
                stale.append(reference.reference_id)
            if observation.status is ObservationStatus.MISSING:
                missing.append(reference.reference_id)

        channels = [observation.channel for observation in frame.observations]
        if len(channels) != len(set(channels)):
            self._invalid_observation(
                "observation frame contains duplicate channels",
                batch.sequence_number,
                frame.t_s,
            )

        self._validate_upstream(batch.upstream, batch.sequence_number, frame.t_s)
        if not isinstance(batch.upstream_source_references, tuple):
            self._invalid_observation(
                "upstream source references must be a tuple",
                batch.sequence_number,
                frame.t_s,
            )
        upstream_refs = batch.upstream_source_references
        if not upstream_refs or any(
            not isinstance(reference, SourceReference)
            for reference in upstream_refs
        ):
            self._invalid_observation(
                "upstream inputs require structured source references",
                batch.sequence_number,
                frame.t_s,
            )
        for reference in upstream_refs:
            if reference.available_timestamp_s > frame.t_s:
                self._invalid_observation(
                    f"{reference.reference_id}: upstream input is not yet available",
                    batch.sequence_number,
                    frame.t_s,
                )
            age = frame.t_s - reference.sample_timestamp_s
            if age < 0:
                self._invalid_observation(
                    f"{reference.reference_id}: upstream sample is in the future",
                    batch.sequence_number,
                    frame.t_s,
                )
            if (
                reference.status == "stale"
                or age > self.quality_gate.maximum_observation_age_s
            ):
                stale.append(reference.reference_id)
            if reference.status == "missing":
                missing.append(reference.reference_id)
        references.extend(upstream_refs)
        if len(references) != len(
            {reference.identity_key for reference in references}
        ):
            self._invalid_observation(
                "source reference identities must be unique",
                batch.sequence_number,
                frame.t_s,
            )
        return _InputAssessment(
            source_references=tuple(sorted(references)),
            stale_references=tuple(sorted(set(stale))),
            missing_references=tuple(sorted(set(missing))),
            upstream_confidence=min(
                reference.confidence for reference in upstream_refs
            ),
        )

    def _validate_observation(
        self,
        observation: Observation,
        frame: ObservationFrame,
        sequence_number: int,
    ) -> None:
        for name, value in (
            ("channel", observation.channel),
            ("source_id", observation.source_id),
            ("calibration_id", observation.calibration_id),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                self._invalid_observation(
                    f"observation {name} must be a non-blank trimmed string",
                    sequence_number,
                    frame.t_s,
                )
        if not isinstance(observation.status, ObservationStatus):
            self._invalid_observation(
                f"{observation.channel}: status must be ObservationStatus",
                sequence_number,
                frame.t_s,
            )
        if not isinstance(observation.source_type, SourceType):
            self._invalid_observation(
                f"{observation.channel}: source_type must be SourceType",
                sequence_number,
                frame.t_s,
            )
        if (
            isinstance(observation.seq, bool)
            or not isinstance(observation.seq, int)
            or observation.seq < 0
        ):
            self._invalid_observation(
                f"{observation.channel}: seq must be a non-negative integer",
                sequence_number,
                frame.t_s,
            )
        self._valid_fraction(
            f"{observation.channel}: confidence",
            observation.confidence,
            sequence_number,
            frame.t_s,
        )
        self._valid_timestamp(
            f"{observation.channel}: sample timestamp",
            observation.sample_timestamp_s,
            sequence_number,
        )
        self._valid_timestamp(
            f"{observation.channel}: available timestamp",
            observation.available_timestamp_s,
            sequence_number,
        )
        if observation.available_timestamp_s < observation.sample_timestamp_s:
            self._invalid_observation(
                f"{observation.channel}: availability precedes sampling",
                sequence_number,
                frame.t_s,
            )
        if observation.available_timestamp_s > frame.t_s:
            self._invalid_observation(
                f"{observation.channel}: observation is not yet available",
                sequence_number,
                frame.t_s,
            )
        if observation.sample_timestamp_s > frame.t_s:
            self._invalid_observation(
                f"{observation.channel}: sample timestamp is in the future",
                sequence_number,
                frame.t_s,
            )
        value = observation.value
        if value is None:
            if observation.status is not ObservationStatus.MISSING:
                self._invalid_observation(
                    f"{observation.channel}: None requires MISSING status",
                    sequence_number,
                    frame.t_s,
                )
        elif observation.status is ObservationStatus.MISSING:
            self._invalid_observation(
                f"{observation.channel}: MISSING requires value None",
                sequence_number,
                frame.t_s,
            )
        elif not isinstance(value, (float, int, str, bool)):
            self._invalid_observation(
                f"{observation.channel}: unsupported observation value type",
                sequence_number,
                frame.t_s,
            )
        elif isinstance(value, float) and not math.isfinite(value):
            self._invalid_observation(
                f"{observation.channel}: numeric value must be finite",
                sequence_number,
                frame.t_s,
            )

    def _validate_upstream(
        self,
        upstream: UpstreamInputs,
        sequence_number: int,
        observation_timestamp_s: float,
    ) -> None:
        if not isinstance(upstream, UpstreamInputs):
            self._invalid_observation(
                "batch.upstream must be UpstreamInputs",
                sequence_number,
                observation_timestamp_s,
            )
        if not isinstance(upstream.forecast_balls_per_minute, tuple):
            self._invalid_observation(
                "upstream forecast must be a tuple",
                sequence_number,
                observation_timestamp_s,
            )
        for rate in upstream.forecast_balls_per_minute:
            if (
                isinstance(rate, bool)
                or not isinstance(rate, (int, float))
                or not math.isfinite(rate)
                or rate < 0
            ):
                self._invalid_observation(
                    "upstream forecast rates must be finite and non-negative",
                    sequence_number,
                    observation_timestamp_s,
                )
        for name in ("demand_balls_total", "demand_balls_served"):
            value = getattr(upstream, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                self._invalid_observation(
                    f"upstream {name} must be a non-negative integer",
                    sequence_number,
                    observation_timestamp_s,
                )
        if upstream.demand_balls_served > upstream.demand_balls_total:
            self._invalid_observation(
                "upstream served demand cannot exceed total demand",
                sequence_number,
                observation_timestamp_s,
            )
        if (
            isinstance(upstream.stockout_minutes, bool)
            or not isinstance(upstream.stockout_minutes, (int, float))
            or not math.isfinite(upstream.stockout_minutes)
            or upstream.stockout_minutes < 0
        ):
            self._invalid_observation(
                "upstream stockout_minutes must be finite and non-negative",
                sequence_number,
                observation_timestamp_s,
            )
        self._valid_fraction(
            "upstream service_availability",
            upstream.service_availability,
            sequence_number,
            observation_timestamp_s,
        )

    def _assemble(
        self, batch: SequencedObservationFrame
    ) -> tuple[FacilityState, AssemblyReport]:
        try:
            assembler = self.assembler
            if assembler is None:
                from nxt_telemetry.assemble import assemble_from_observations

                assembler = assemble_from_observations
            state, report = assembler(
                batch.frame, self.site_config, batch.upstream
            )
            from nxt_telemetry.assemble import AssemblyReport

            if type(state) is not FacilityState:
                raise TypeError("assembler returned the wrong FacilityState contract")
            if type(report) is not AssemblyReport:
                raise TypeError("assembler returned the wrong AssemblyReport contract")
            if state.meta.t_s != batch.frame.t_s:
                raise ValueError(
                    "assembled FacilityState timestamp does not match frame"
                )
            self._validate_report(report)
            state_payload = state.to_dict()
            if not isinstance(state_payload, dict):
                raise TypeError("FacilityState.to_dict() must return a dict")
            canonical_json(state_payload)
            canonical_json(report.to_dict())
            return state, report
        except SiteRuntimeError:
            raise
        except Exception as exc:
            self._fail(
                FailureCode.ASSEMBLY_FAILED,
                PipelineStage.ASSEMBLE,
                f"{type(exc).__name__}: {exc}",
                sequence_number=batch.sequence_number,
                observation_timestamp_s=batch.frame.t_s,
                retryable=True,
            )

    @staticmethod
    def _validate_report(report: AssemblyReport) -> None:
        for name in ("missing_channels", "stale_channels", "consistency_issues"):
            value = getattr(report, name)
            if not isinstance(value, tuple) or any(
                not isinstance(item, str) or not item or item != item.strip()
                for item in value
            ):
                raise ValueError(f"AssemblyReport.{name} must be a tuple of strings")
        if len(report.missing_channels) != len(set(report.missing_channels)):
            raise ValueError("AssemblyReport missing channels must be unique")
        if len(report.stale_channels) != len(set(report.stale_channels)):
            raise ValueError("AssemblyReport stale channels must be unique")
        if (
            isinstance(report.overall_confidence, bool)
            or not isinstance(report.overall_confidence, (int, float))
            or not math.isfinite(report.overall_confidence)
            or not 0.0 <= report.overall_confidence <= 1.0
        ):
            raise ValueError(
                "AssemblyReport confidence must be finite and in [0, 1]"
            )
        if (
            not isinstance(report.provenance_grade, str)
            or not report.provenance_grade
            or report.provenance_grade != report.provenance_grade.strip()
        ):
            raise ValueError("AssemblyReport provenance grade must be explicit")
        if not isinstance(report.to_dict(), dict):
            raise TypeError("AssemblyReport.to_dict() must return a dict")

    def _apply_quality_gate(
        self,
        report: AssemblyReport,
        *,
        assessment: _InputAssessment,
        sequence_number: int,
        observation_timestamp_s: float,
        enforce: bool,
    ) -> RuntimeQuality:
        stale = tuple(
            sorted(set(assessment.stale_references) | set(report.stale_channels))
        )
        if enforce and stale:
            self._fail(
                FailureCode.STALE_OBSERVATION,
                PipelineStage.QUALITY_GATE,
                f"stale inputs: {', '.join(stale)}",
                sequence_number=sequence_number,
                observation_timestamp_s=observation_timestamp_s,
            )
        missing = tuple(
            sorted(
                set(assessment.missing_references) | set(report.missing_channels)
            )
        )
        effective_confidence = min(
            report.overall_confidence, assessment.upstream_confidence
        )
        violations = []
        if missing:
            violations.append(f"missing_inputs={len(missing)}")
        if (
            len(report.consistency_issues)
            > self.quality_gate.maximum_consistency_issues
        ):
            violations.append(
                f"consistency_issues={len(report.consistency_issues)}"
            )
        if effective_confidence < self.quality_gate.minimum_effective_confidence:
            violations.append(
                f"effective_confidence={effective_confidence:.6f}"
            )
        if report.provenance_grade not in self.quality_gate.accepted_provenance_grades:
            violations.append(f"provenance_grade={report.provenance_grade}")
        if enforce and violations:
            self._fail(
                FailureCode.INSUFFICIENT_QUALITY,
                PipelineStage.QUALITY_GATE,
                "; ".join(violations),
                sequence_number=sequence_number,
                observation_timestamp_s=observation_timestamp_s,
            )
        return RuntimeQuality(
            assembly_confidence=report.overall_confidence,
            upstream_confidence=assessment.upstream_confidence,
            effective_confidence=effective_confidence,
        )

    def _load_checkpoint(
        self, sequence_number: int, observation_timestamp_s: float
    ) -> RuntimeCheckpoint:
        try:
            checkpoint = self.checkpoint_store.load(
                self.site_id, self.deployment_id
            )
            if not isinstance(checkpoint, RuntimeCheckpoint):
                raise TypeError("checkpoint store returned the wrong contract")
            if (checkpoint.site_id, checkpoint.deployment_id) != (
                self.site_id,
                self.deployment_id,
            ):
                raise CheckpointError("checkpoint identity does not match runtime")
            return checkpoint
        except Exception as exc:
            self._fail(
                FailureCode.CHECKPOINT_FAILED,
                PipelineStage.CHECKPOINT,
                f"could not load checkpoint: {type(exc).__name__}: {exc}",
                sequence_number=sequence_number,
                observation_timestamp_s=observation_timestamp_s,
                retryable=True,
            )

    def _save_transition(
        self,
        expected: RuntimeCheckpoint,
        updated: RuntimeCheckpoint,
        sequence_number: int,
        observation_timestamp_s: float,
    ) -> None:
        try:
            self.checkpoint_store.compare_and_save(expected, updated)
        except Exception as exc:
            self._fail(
                FailureCode.CHECKPOINT_FAILED,
                PipelineStage.CHECKPOINT,
                f"checkpoint transition failed: {type(exc).__name__}: {exc}",
                sequence_number=sequence_number,
                observation_timestamp_s=observation_timestamp_s,
                retryable=True,
            )

    def _sequence_mode(
        self,
        checkpoint: RuntimeCheckpoint,
        sequence_number: int,
        observation_timestamp_s: float,
    ) -> _SequenceMode:
        if checkpoint.has_pending_publish:
            if sequence_number != checkpoint.pending_sequence:
                self._fail(
                    FailureCode.INVALID_SEQUENCE,
                    PipelineStage.VALIDATE,
                    f"sequence {sequence_number} is invalid; expected "
                    f"pending {checkpoint.pending_sequence}",
                    sequence_number=sequence_number,
                    observation_timestamp_s=observation_timestamp_s,
                )
            if observation_timestamp_s != checkpoint.pending_observation_timestamp_s:
                self._fail(
                    FailureCode.REPLAY_MISMATCH,
                    PipelineStage.VALIDATE,
                    "pending sequence changed observation timestamp",
                    sequence_number=sequence_number,
                    observation_timestamp_s=observation_timestamp_s,
                )
            return _SequenceMode.PENDING_REPLAY

        if checkpoint.last_successful_sequence is None:
            return _SequenceMode.NEW
        if sequence_number == checkpoint.last_successful_sequence:
            if observation_timestamp_s != checkpoint.last_observation_timestamp_s:
                self._fail(
                    FailureCode.REPLAY_MISMATCH,
                    PipelineStage.VALIDATE,
                    "completed sequence changed observation timestamp",
                    sequence_number=sequence_number,
                    observation_timestamp_s=observation_timestamp_s,
                )
            return _SequenceMode.COMPLETED_REPLAY
        expected = checkpoint.last_successful_sequence + 1
        if sequence_number != expected:
            self._fail(
                FailureCode.INVALID_SEQUENCE,
                PipelineStage.VALIDATE,
                f"sequence {sequence_number} is invalid; expected {expected}",
                sequence_number=sequence_number,
                observation_timestamp_s=observation_timestamp_s,
            )
        return _SequenceMode.NEW

    def _valid_timestamp(
        self, name: str, value: float, sequence_number: int
    ) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            self._invalid_observation(
                f"{name} must be finite and non-negative",
                sequence_number,
                value if isinstance(value, (int, float)) else None,
            )

    def _valid_fraction(
        self,
        name: str,
        value: float,
        sequence_number: int,
        observation_timestamp_s: float,
    ) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0.0 <= value <= 1.0
        ):
            self._invalid_observation(
                f"{name} must be finite and in [0, 1]",
                sequence_number,
                observation_timestamp_s,
            )

    def _invalid_observation(
        self,
        detail: str,
        sequence_number: int,
        observation_timestamp_s: float | None,
    ) -> None:
        self._fail(
            FailureCode.INVALID_OBSERVATION,
            PipelineStage.VALIDATE,
            detail,
            sequence_number=sequence_number,
            observation_timestamp_s=observation_timestamp_s,
        )

    def _fail(
        self,
        code: FailureCode,
        stage: PipelineStage,
        detail: str,
        *,
        sequence_number: int | None = None,
        observation_timestamp_s: float | None = None,
        retryable: bool = False,
    ) -> None:
        failure = RuntimeFailure(
            code=code,
            stage=stage,
            site_id=self.site_id,
            deployment_id=self.deployment_id,
            detail=detail,
            sequence_number=sequence_number,
            observation_timestamp_s=observation_timestamp_s,
            retryable=retryable,
        )
        self._notify_rejected(failure)
        raise SiteRuntimeError(failure)

    def _notify_published(self, envelope: FacilitySnapshotEnvelope) -> None:
        if self.runtime_sink is None:
            return
        try:
            self.runtime_sink.on_published(envelope)
        except Exception:
            return

    def _notify_rejected(self, failure: RuntimeFailure) -> None:
        if self.runtime_sink is None:
            return
        try:
            self.runtime_sink.on_rejected(failure)
        except Exception:
            return
