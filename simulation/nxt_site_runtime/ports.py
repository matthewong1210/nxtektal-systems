"""Dependency-inversion ports for physical inputs and downstream outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from nxt_telemetry.observations import ObservationFrame, UpstreamInputs

from .envelope import FacilitySnapshotEnvelope, SourceReference

if TYPE_CHECKING:
    from .pipeline import RuntimeFailure
else:
    RuntimeFailure = Any


@dataclass(frozen=True)
class SequencedObservationFrame:
    """One immutable assembly input, including legacy upstream inputs.

    ``upstream`` is the existing ``nxt_telemetry.UpstreamInputs`` contract,
    not a replacement model.  Binding it to the frame makes retries use the
    exact same POS/forecast facts.  Its structured references provide the
    provenance and quality that ``AssemblyReport`` cannot cover.
    """

    sequence_number: int
    frame: ObservationFrame
    upstream: UpstreamInputs
    upstream_source_references: tuple[SourceReference, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence_number, bool)
            or not isinstance(self.sequence_number, int)
            or self.sequence_number < 0
        ):
            raise ValueError("sequence_number must be a non-negative integer")


@runtime_checkable
class ObservationSource(Protocol):
    """At-least-once source: observe peeks until ack or reject.

    ``acknowledge`` advances the publication sequence after success.
    ``reject`` discards the bad physical input but reuses its sequence number
    for the next frame, keeping published snapshot ordering contiguous. The
    runtime only rejects a terminally invalid frame at the next expected
    position; sequence gaps and replay mismatches remain retained recovery
    incidents and are never automatically resequenced.
    """

    def observe(self) -> SequencedObservationFrame:
        ...

    def acknowledge(self, sequence_number: int) -> None:
        ...

    def reject(self, sequence_number: int, reason: str) -> None:
        ...


@runtime_checkable
class StatePublisher(Protocol):
    def publish(self, envelope: FacilitySnapshotEnvelope) -> None:
        """Publish idempotently using ``envelope.envelope_id`` as the key."""
        ...


@runtime_checkable
class RuntimeSink(Protocol):
    """Best-effort operational visibility; never a decision/control port."""

    def on_published(self, envelope: FacilitySnapshotEnvelope) -> None:
        ...

    def on_rejected(self, failure: RuntimeFailure) -> None:
        ...
