"""NXTektal Agent Runtime V1 -- composition and lifecycle only.

This package continuously composes the existing Site Runtime and Shadow
Ops contracts.  It owns runtime lifecycle, the cross-layer evaluation
checkpoint, the evaluation journal, the pending manager-decision view,
and read-only health/status.  It owns no observation, state, policy,
recommendation, trace, workflow, memory, or execution semantics, and it
has no robot, ROS, actuator, navigation, or emergency-stop surface.
"""

from .checkpoints import (
    EvaluationCheckpoint,
    EvaluationCheckpointConflictError,
    EvaluationCheckpointError,
    EvaluationCheckpointStore,
    InMemoryEvaluationCheckpointStore,
    JsonEvaluationCheckpointStore,
)
from .journal import EvaluationJournal, JournalIntegrityError
from .publisher import JsonlSnapshotPublisher, SnapshotPublisherError
from .queue import (
    DeferralNote,
    ManagerDecisionQueue,
    ManagerDecisionQueueError,
    PendingDecision,
)
from .records import EVALUATION_SCHEMA_VERSION, EvaluationRecord
from .runtime import (
    AgentRuntime,
    AgentRuntimeError,
    CycleKind,
    CycleOutcome,
    FailurePolicy,
    RecoveryReport,
    SourceExhausted,
)
from .status import RuntimeState, RuntimeStatus

__all__ = [
    "EVALUATION_SCHEMA_VERSION",
    "AgentRuntime",
    "AgentRuntimeError",
    "CycleKind",
    "CycleOutcome",
    "DeferralNote",
    "EvaluationCheckpoint",
    "EvaluationCheckpointConflictError",
    "EvaluationCheckpointError",
    "EvaluationCheckpointStore",
    "EvaluationJournal",
    "EvaluationRecord",
    "FailurePolicy",
    "InMemoryEvaluationCheckpointStore",
    "JournalIntegrityError",
    "JsonEvaluationCheckpointStore",
    "JsonlSnapshotPublisher",
    "ManagerDecisionQueue",
    "ManagerDecisionQueueError",
    "PendingDecision",
    "RecoveryReport",
    "RuntimeState",
    "RuntimeStatus",
    "SnapshotPublisherError",
    "SourceExhausted",
]
