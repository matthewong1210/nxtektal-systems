"""A bounded, deterministic, at-least-once cursor over raw sample batches.

This is a **fixture** feed for local and integration testing.  It is not
a transport and is deliberately not shaped like one: it holds an
in-memory tuple of already-read batches, has no thread, no polling loop,
no filesystem watch, no socket, and no retry timer.  Replacing it with a
real Modbus/MQTT/robot-vendor reader is the next seam, and that reader
must satisfy exactly this peek/acknowledge/reject contract so nothing
downstream changes.

The cursor mirrors the semantics the Site Runtime's ``ObservationSource``
port documents: ``peek`` redelivers the identical batch until it is
acknowledged or terminally rejected, ``acknowledge`` advances exactly
once, and ``reject`` discards a bad batch while reusing its sequence
number so published ordering stays contiguous.  Each delivery accepts
exactly one decision: after an acknowledge or reject, a fresh ``peek``
is required before the next decision, so a duplicated decision fails
loudly instead of consuming the batch that inherited the rejected
position.  Composing that into an actual ``ObservationSource`` is the
composition root's job -- this package must not import the Site Runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import EdgeAdapterError, RawSampleBatch


class FeedExhausted(Exception):
    """The bounded feed has no further batch to deliver."""


class FeedProtocolError(RuntimeError):
    """An acknowledgement or rejection did not match the delivered batch."""


@dataclass(frozen=True, slots=True)
class SequencedRawBatch:
    """One raw batch bound to the publication sequence number it occupies."""

    sequence_number: int
    batch: RawSampleBatch

    def __post_init__(self) -> None:
        if isinstance(self.sequence_number, bool) or not isinstance(
            self.sequence_number, int
        ):
            raise EdgeAdapterError("sequence_number must be an integer")
        if self.sequence_number < 0:
            raise EdgeAdapterError("sequence_number must be non-negative")
        if not isinstance(self.batch, RawSampleBatch):
            raise EdgeAdapterError("batch must be a RawSampleBatch")


class FixtureRawSampleFeed:
    """At-least-once delivery of a fixed, ordered list of raw batches."""

    def __init__(
        self,
        batches: tuple[RawSampleBatch, ...],
        *,
        first_sequence_number: int = 0,
    ) -> None:
        if not isinstance(batches, tuple):
            raise EdgeAdapterError("batches must be a tuple")
        for batch in batches:
            if not isinstance(batch, RawSampleBatch):
                raise EdgeAdapterError("batches must contain RawSampleBatch items")
        if isinstance(first_sequence_number, bool) or not isinstance(
            first_sequence_number, int
        ):
            raise EdgeAdapterError("first_sequence_number must be an integer")
        if first_sequence_number < 0:
            raise EdgeAdapterError("first_sequence_number must be non-negative")
        self._pending: list[RawSampleBatch] = list(batches)
        self._next_sequence = first_sequence_number
        self._acknowledged: list[int] = []
        self._rejected: list[tuple[int, str]] = []
        # Per-delivery decision state: a decision is only legal after a
        # fresh peek, and every peek permits exactly one decision.  Without
        # this, ``reject`` reusing the sequence position makes a duplicate
        # reject(n) match the NEXT batch -- which now occupies position n --
        # and silently destroy it.
        self._delivery_open = False

    # -- read model ------------------------------------------------------

    @property
    def acknowledged(self) -> tuple[int, ...]:
        return tuple(self._acknowledged)

    @property
    def rejected(self) -> tuple[tuple[int, str], ...]:
        return tuple(self._rejected)

    @property
    def remaining(self) -> int:
        return len(self._pending)

    @property
    def exhausted(self) -> bool:
        return not self._pending

    # -- at-least-once cursor -------------------------------------------

    def peek(self) -> SequencedRawBatch:
        """Return the current batch without consuming it.

        Repeated calls return an equal value until the batch is
        acknowledged or rejected, so a consumer that fails part-way
        through a cycle re-observes the identical input.

        This guarantee is **in-process only**: the cursor lives in memory.
        Surviving a restart is the caller's responsibility -- construct
        the feed with ``first_sequence_number`` set to the next expected
        publication position, and with the already-delivered batches
        removed.  A real transport needs the same resume hook, because
        Site Runtime retains an ``invalid_sequence`` frame rather than
        discarding it.
        """
        if not self._pending:
            raise FeedExhausted("the fixture raw-sample feed is exhausted")
        self._delivery_open = True
        return SequencedRawBatch(
            sequence_number=self._next_sequence, batch=self._pending[0]
        )

    def acknowledge(self, sequence_number: int) -> None:
        """Advance past the current batch exactly once.

        Legal only after a fresh :meth:`peek` of this delivery; a second
        decision without a new peek raises :class:`FeedProtocolError`.
        """
        self._require_current(sequence_number, "acknowledge")
        self._delivery_open = False
        self._acknowledged.append(sequence_number)
        self._pending.pop(0)
        self._next_sequence += 1

    def reject(self, sequence_number: int, reason: str) -> None:
        """Discard the current batch, reusing its sequence for the next one.

        Legal only after a fresh :meth:`peek` of this delivery.  Sequence
        reuse means the NEXT batch occupies the rejected position, so
        without the peek gate a duplicate ``reject(n)`` would match -- and
        silently destroy -- that unrelated batch.
        """
        if type(reason) is not str or not reason.strip():
            raise FeedProtocolError("reject requires a non-blank reason")
        self._require_current(sequence_number, "reject")
        self._delivery_open = False
        self._rejected.append((sequence_number, reason))
        self._pending.pop(0)
        # The sequence number is intentionally NOT advanced: the next batch
        # takes the discarded position so published ordering has no gap.

    def _require_current(self, sequence_number: int, operation: str) -> None:
        if isinstance(sequence_number, bool) or not isinstance(
            sequence_number, int
        ):
            raise FeedProtocolError(f"{operation} requires an integer sequence")
        if not self._delivery_open:
            raise FeedProtocolError(
                f"{operation} for sequence {sequence_number} requires a fresh "
                "peek: the current delivery was already decided (or never "
                "peeked), and a duplicate decision would consume the next "
                "unrelated batch"
            )
        if not self._pending:
            raise FeedProtocolError(
                f"{operation} for sequence {sequence_number} arrived after the "
                "feed was exhausted"
            )
        if sequence_number != self._next_sequence:
            raise FeedProtocolError(
                f"{operation} for sequence {sequence_number} does not match the "
                f"delivered sequence {self._next_sequence}"
            )


__all__ = [
    "FeedExhausted",
    "FeedProtocolError",
    "FixtureRawSampleFeed",
    "SequencedRawBatch",
]
