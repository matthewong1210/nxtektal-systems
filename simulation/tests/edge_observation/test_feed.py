"""The fixture raw-sample feed: at-least-once, bounded, deterministic."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nxt_edge_observation import (
    EdgeAdapterError,
    FeedExhausted,
    FeedProtocolError,
    FixtureRawSampleFeed,
)
from nxt_edge_observation import feed as feed_module

from scripts.pilot_course_a_edge_fixture import PILOT_CYCLES, raw_batch


@pytest.fixture
def batches():
    return tuple(raw_batch(spec) for spec in PILOT_CYCLES[:3])


def test_peek_redelivers_the_identical_batch_until_acknowledged(batches):
    feed = FixtureRawSampleFeed(batches)
    first = feed.peek()
    second = feed.peek()
    assert first == second
    assert first.sequence_number == 0
    assert feed.remaining == 3
    feed.acknowledge(0)
    assert feed.peek().sequence_number == 1
    assert feed.remaining == 2


def test_acknowledge_advances_exactly_once(batches):
    feed = FixtureRawSampleFeed(batches)
    feed.peek()
    feed.acknowledge(0)
    feed.peek()
    feed.acknowledge(1)
    assert feed.acknowledged == (0, 1)
    assert feed.remaining == 1


def test_a_duplicate_acknowledgement_fails_visibly(batches):
    feed = FixtureRawSampleFeed(batches)
    feed.peek()
    feed.acknowledge(0)
    with pytest.raises(FeedProtocolError):
        feed.acknowledge(0)
    assert feed.acknowledged == (0,)
    assert feed.remaining == 2


def test_a_decision_without_any_peek_fails_visibly(batches):
    """A decision is only legal against a delivery the caller has seen."""
    feed = FixtureRawSampleFeed(batches)
    with pytest.raises(FeedProtocolError):
        feed.acknowledge(0)
    with pytest.raises(FeedProtocolError):
        feed.reject(0, "never peeked")
    assert feed.remaining == 3


def test_a_mismatched_sequence_fails_visibly(batches):
    feed = FixtureRawSampleFeed(batches)
    feed.peek()
    with pytest.raises(FeedProtocolError):
        feed.acknowledge(1)
    with pytest.raises(FeedProtocolError):
        feed.reject(7, "wrong sequence")
    assert feed.acknowledged == ()
    assert feed.rejected == ()


def test_reject_discards_the_batch_and_reuses_its_sequence(batches):
    feed = FixtureRawSampleFeed(batches)
    feed.peek()
    feed.acknowledge(0)
    assert feed.peek().sequence_number == 1
    feed.reject(1, "insufficient_data_quality")
    assert feed.rejected == ((1, "insufficient_data_quality"),)
    # Publication ordering stays contiguous: the next batch takes position 1.
    assert feed.peek().sequence_number == 1
    # And after a fresh peek, deciding the reused position works normally.
    feed.acknowledge(1)
    assert feed.acknowledged == (0, 1)


def test_reject_requires_a_reason(batches):
    feed = FixtureRawSampleFeed(batches)
    feed.peek()
    with pytest.raises(FeedProtocolError):
        feed.reject(0, "   ")


def test_exhaustion_is_explicit(batches):
    feed = FixtureRawSampleFeed(batches)
    for sequence in range(3):
        feed.peek()
        feed.acknowledge(sequence)
    assert feed.exhausted
    with pytest.raises(FeedExhausted):
        feed.peek()
    with pytest.raises(FeedProtocolError):
        feed.acknowledge(3)


# -- per-delivery decision state: exactly one decision per peek -----------


def test_a_duplicate_reject_cannot_consume_the_next_batch(batches):
    """Regression for the review-confirmed data-loss defect.

    Sequence reuse means the next batch inherits the rejected position, so
    before the peek gate a second reject(0) matched -- and silently
    destroyed -- the unrelated batch at cycle 1.
    """
    feed = FixtureRawSampleFeed(batches)
    feed.peek()
    feed.reject(0, "bad frame")
    assert feed.remaining == 2
    with pytest.raises(FeedProtocolError):
        feed.reject(0, "duplicate reject")
    # The next batch is intact: same count, same identity, same position.
    assert feed.remaining == 2
    survivor = feed.peek()
    assert survivor.sequence_number == 0
    assert survivor.batch.cycle_index == 1
    assert feed.rejected == ((0, "bad frame"),)


def test_acknowledge_after_reject_without_a_new_peek_fails(batches):
    feed = FixtureRawSampleFeed(batches)
    feed.peek()
    feed.reject(0, "bad frame")
    with pytest.raises(FeedProtocolError):
        feed.acknowledge(0)
    assert feed.remaining == 2
    assert feed.acknowledged == ()


def test_reject_after_acknowledge_without_a_new_peek_fails(batches):
    feed = FixtureRawSampleFeed(batches)
    feed.peek()
    feed.acknowledge(0)
    with pytest.raises(FeedProtocolError):
        feed.reject(1, "stale decision")
    assert feed.remaining == 2
    assert feed.rejected == ()


def test_repeated_peeks_still_permit_exactly_one_decision(batches):
    """Peek is idempotent; the gate counts decisions, not peeks."""
    feed = FixtureRawSampleFeed(batches)
    assert feed.peek() == feed.peek() == feed.peek()
    feed.acknowledge(0)
    with pytest.raises(FeedProtocolError):
        feed.acknowledge(1)
    feed.peek()
    feed.acknowledge(1)
    assert feed.acknowledged == (0, 1)


def test_an_empty_feed_is_immediately_exhausted():
    feed = FixtureRawSampleFeed(())
    assert feed.exhausted
    with pytest.raises(FeedExhausted):
        feed.peek()


def test_replay_is_deterministic(batches):
    def drive(feed):
        seen = []
        while not feed.exhausted:
            sequenced = feed.peek()
            seen.append(
                (sequenced.sequence_number, sequenced.batch.cycle_index)
            )
            feed.acknowledge(sequenced.sequence_number)
        return seen

    assert drive(FixtureRawSampleFeed(batches)) == drive(
        FixtureRawSampleFeed(batches)
    )


def test_a_first_sequence_offset_is_honoured(batches):
    feed = FixtureRawSampleFeed(batches, first_sequence_number=5)
    assert feed.peek().sequence_number == 5
    feed.acknowledge(5)
    assert feed.peek().sequence_number == 6


@pytest.mark.parametrize(
    "kwargs",
    [
        {"batches": [1, 2]},
        {"batches": ("not a batch",)},
        {"batches": (), "first_sequence_number": -1},
        {"batches": (), "first_sequence_number": True},
    ],
)
def test_construction_fails_closed_on_bad_input(kwargs):
    with pytest.raises(EdgeAdapterError):
        FixtureRawSampleFeed(**kwargs)


def test_the_feed_has_no_transport_thread_or_polling_surface():
    source = Path(feed_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.split(".")[0])
    forbidden = {
        "socket",
        "threading",
        "asyncio",
        "selectors",
        "queue",
        "subprocess",
        "time",
        "urllib",
        "http",
        "serial",
        "paho",
        "pymodbus",
        "kafka",
        "asyncua",
    }
    assert not imported & forbidden, imported & forbidden
    assert "while True" not in source
