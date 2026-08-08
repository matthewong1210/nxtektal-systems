"""NXTektal Site Runtime v0 -- orchestration between evidence and consumers."""

from .checkpoints import (
    CheckpointConflictError,
    CheckpointError,
    CheckpointStore,
    InMemoryCheckpointStore,
    JsonCheckpointStore,
    RuntimeCheckpoint,
)
from .composition import RuntimeSiteBinding, bind_commissioned_site
from .envelope import (
    SCHEMA_VERSION,
    FacilitySnapshotEnvelope,
    RuntimeQuality,
    SourceReference,
)
from .ports import (
    ObservationSource,
    RuntimeSink,
    SequencedObservationFrame,
    StatePublisher,
)

__all__ = [
    "SCHEMA_VERSION",
    "CheckpointConflictError",
    "CheckpointError",
    "CheckpointStore",
    "FacilitySnapshotEnvelope",
    "InMemoryCheckpointStore",
    "JsonCheckpointStore",
    "ObservationSource",
    "RuntimeCheckpoint",
    "RuntimeQuality",
    "RuntimeSink",
    "RuntimeSiteBinding",
    "SequencedObservationFrame",
    "SourceReference",
    "StatePublisher",
    "bind_commissioned_site",
    "FailureCode",
    "PipelineStage",
    "QualityGate",
    "RuntimeFailure",
    "SiteRuntimeError",
    "SiteRuntimePipeline",
]


def __getattr__(name: str):
    # Importing the public pipeline class stays lightweight; the existing
    # simulation-backed assembler is resolved only when process() runs.
    if name in {
        "FailureCode",
        "PipelineStage",
        "QualityGate",
        "RuntimeFailure",
        "SiteRuntimeError",
        "SiteRuntimePipeline",
    }:
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
