"""Append-only journal: canonical bytes, contiguity, fail-loud integrity, in-lock builders."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

import pytest

from nxt_edge_task.journal import JournalIntegrityError, JsonlJournal, PreconditionFailed, RecordSpec

NOW = "2026-09-12T08:00:00.000000Z"


def spec(kind: str = "note", origin: str = "EDGE", **payload) -> RecordSpec:
    return RecordSpec(record_kind=kind, origin=origin, recorded_at_utc=NOW, payload=payload)


def test_records_are_canonical_contiguous_and_content_addressed(tmp_path: Path) -> None:
    journal = JsonlJournal(tmp_path / "j.jsonl")
    first = journal.append(spec(n=1))
    second = journal.append(spec(n=2))
    assert (first.sequence, second.sequence) == (1, 2)
    assert first.record_id != second.record_id and first.record_id.startswith("rec_")
    lines = (tmp_path / "j.jsonl").read_bytes().split(b"\n")
    assert lines[-1] == b""
    for line in lines[:-1]:
        raw = json.loads(line)
        assert json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() == line
    assert [r.payload["n"] for r in journal.read()] == [1, 2]


def test_truncated_last_line_fails_loud_and_never_repairs(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = JsonlJournal(path)
    journal.append(spec(n=1))
    data = path.read_bytes()
    path.write_bytes(data + b'{"schema_version":"nxt-edge-task/journal/v1","sequence":2')
    with pytest.raises(JournalIntegrityError):
        JsonlJournal(path).read()
    with pytest.raises(JournalIntegrityError):
        JsonlJournal(path).append(spec(n=2))
    assert path.read_bytes().endswith(b'"sequence":2')  # untouched


def test_tampered_line_fails_loud(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = JsonlJournal(path)
    journal.append(spec(n=1))
    journal.append(spec(n=2))
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"n":1', '"n":9'), encoding="utf-8")
    with pytest.raises(JournalIntegrityError):
        JsonlJournal(path).read()
    # The original instance's verified-prefix cache must not mask the tamper.
    with pytest.raises(JournalIntegrityError):
        journal.read()


def test_builder_runs_inside_the_lock_and_sees_other_writers(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    writer_a = JsonlJournal(path)
    writer_b = JsonlJournal(path)
    writer_a.append(spec(n=1))
    seen: list[int] = []

    def build(records):
        # The builder must run while the exclusive lock is held: a second file
        # description cannot even take a shared lock.
        fd = os.open(path, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        seen.append(len(records))
        if any(r.payload.get("n") == 1 for r in records):
            return [spec(n=2)]
        return []

    appended = writer_b.append_via(build)
    assert seen == [1] and appended[0].sequence == 2
    assert [r.payload["n"] for r in writer_a.read()] == [1, 2]

    def refuse(records):
        raise PreconditionFailed("robot_has_active_task", "demo")

    with pytest.raises(PreconditionFailed):
        writer_a.append_via(refuse)
    assert len(writer_b.read()) == 2


def test_allowed_kinds_and_origins_are_enforced(tmp_path: Path) -> None:
    journal = JsonlJournal(tmp_path / "j.jsonl", allowed_kinds=frozenset({"only"}))
    with pytest.raises(ValueError):
        journal.append(spec(kind="other"))
    with pytest.raises(ValueError):
        journal.append(spec(kind="only", origin="ALIEN"))
    journal.append(spec(kind="only"))
    with pytest.raises(JournalIntegrityError):
        JsonlJournal(tmp_path / "j.jsonl", allowed_kinds=frozenset({"different"})).read()


def test_nested_payloads_are_frozen_and_round_trip(tmp_path: Path) -> None:
    journal = JsonlJournal(tmp_path / "j.jsonl")
    record = journal.append(spec(nested={"a": [1, {"b": None}], "c": 1.5}))
    with pytest.raises(TypeError):
        record.payload["nested"]["a"] = 0  # type: ignore[index]
    assert journal.read()[0].to_dict()["payload"]["nested"] == {"a": [1, {"b": None}], "c": 1.5}


def test_non_finite_number_line_is_an_integrity_error(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = JsonlJournal(path)
    journal.append(spec(n=1))
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"n":1', '"n":NaN'), encoding="utf-8")
    with pytest.raises(JournalIntegrityError):
        JsonlJournal(path).read()


# ---------------------------------------------------------------------------
# High-water anchor: rollback to a valid prefix is state loss, not a crash
# ---------------------------------------------------------------------------


def _spec(kind: str = "edge_started", n: int = 0) -> RecordSpec:
    return RecordSpec(record_kind=kind, origin="EDGE", recorded_at_utc="2026-09-12T08:00:00.000000Z", payload={"n": n})


def test_anchor_advances_with_every_append_and_lags_never_leads(tmp_path: Path) -> None:
    journal = JsonlJournal(tmp_path / "j.jsonl")
    assert journal.read_anchor() is None
    journal.append(_spec(n=1))
    assert journal.read_anchor() == (1, journal.read()[0].record_id)
    journal.append_via(lambda records: [_spec(n=2), _spec(n=3)])
    records = journal.read()
    assert journal.read_anchor() == (3, records[2].record_id)


def test_rollback_to_a_valid_prefix_fails_loud_on_read_and_append(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = JsonlJournal(path)
    for n in range(1, 4):
        journal.append(_spec(n=n))
    lines = path.read_bytes().split(b"\n")[:-1]
    path.write_bytes(b"".join(line + b"\n" for line in lines[:2]))  # a perfectly valid two-record journal
    with pytest.raises(JournalIntegrityError, match="rolled back"):
        JsonlJournal(path).read()
    with pytest.raises(JournalIntegrityError, match="rolled back"):
        JsonlJournal(path).append(_spec(n=9))
    assert path.read_bytes() == b"".join(line + b"\n" for line in lines[:2])  # nothing appended on the way out


def test_rewritten_record_at_the_anchor_fails_loud(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = JsonlJournal(path)
    journal.append(_spec(n=1))
    journal.append(_spec(n=2))
    lines = path.read_bytes().split(b"\n")[:-1]
    other = JsonlJournal(tmp_path / "other.jsonl")
    other.append(_spec(n=1))
    other.append(_spec(n=7))  # a different, individually valid second record
    replacement = (tmp_path / "other.jsonl").read_bytes().split(b"\n")[:-1][1]
    path.write_bytes(lines[0] + b"\n" + replacement + b"\n")
    with pytest.raises(JournalIntegrityError, match="differs from the anchored record"):
        JsonlJournal(path).read()


def test_missing_journal_with_an_anchor_and_journal_without_anchor_both_fail(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = JsonlJournal(path)
    journal.append(_spec(n=1))
    path.unlink()
    with pytest.raises(JournalIntegrityError, match="rolled back"):
        JsonlJournal(path).read()
    journal.anchor_path.unlink()
    assert JsonlJournal(path).read() == ()  # nothing ever existed: a genuine first boot
    JsonlJournal(path).append(_spec(n=1))
    JsonlJournal(path).anchor_path.unlink()
    with pytest.raises(JournalIntegrityError, match="no anchor"):
        JsonlJournal(path).read()


def test_discard_anchor_only_for_a_missing_or_empty_journal(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = JsonlJournal(path)
    journal.append(_spec(n=1))
    with pytest.raises(JournalIntegrityError, match="non-empty"):
        journal.discard_anchor()
    path.unlink()
    journal.discard_anchor()
    assert not journal.anchor_path.exists() and JsonlJournal(path).read() == ()


def test_torn_batch_is_not_a_rollback(tmp_path: Path) -> None:
    """Records past the anchor are allowed: the anchor lags a crash between the two writes."""

    path = tmp_path / "j.jsonl"
    journal = JsonlJournal(path)
    journal.append(_spec(n=1))
    anchor_after_first = journal.anchor_path.read_bytes()
    journal.append(_spec(n=2))
    journal.anchor_path.write_bytes(anchor_after_first)  # the second anchor write "never happened"
    assert len(JsonlJournal(path).read()) == 2
    JsonlJournal(path).append(_spec(n=3))
    assert JsonlJournal(path).read_anchor()[0] == 3
