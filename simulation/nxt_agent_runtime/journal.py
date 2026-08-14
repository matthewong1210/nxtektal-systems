"""Append-only evaluation journal: canonical runtime evaluation evidence.

The journal is the durable record that an evaluation happened for each
admitted envelope, including explicit ``NO_ACTION`` outcomes, which have
no other durable home.  It is evidence output, never live-loop input, and
never a second FacilityState or policy source of truth.

Duplicate protection: exactly one record per envelope sequence.  An
identical re-append (same ``evaluation_id``) is an idempotent no-op; a
different record for an already-journaled sequence fails closed.  Tamper
evidence for decision/workflow records remains the Shadow Ops ledger's
responsibility; the journal verifies canonical bytes, contiguity, and
content-derived identity.

Verification strategy: ``read()`` (used by ``recover()`` and every queue
projection) always verifies the complete file.  ``append()`` verifies
every byte it has not already verified in this instance's lifetime — a
fresh instance therefore verifies the whole file on its first append —
and re-anchors on the last verified line so shrinkage or tail edits fail
closed.  An in-place edit strictly inside the already-verified region is
caught by the next full ``read()``/``recover()``, not by ``append()``;
this trade keeps a run of N cycles linear instead of quadratic in N.

Agent Runtime V1 is POSIX-only: the journal requires ``fcntl`` advisory
locking and fails loudly at construction on hosts without it.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

try:  # POSIX advisory file locking; module import remains portable.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on non-POSIX platforms.
    _fcntl = None

from nxt_pilot_ops.contracts import EvaluationVerdict, RecommendationAction
from nxt_pilot_ops.serialization import canonical_json

from .records import EVALUATION_SCHEMA_VERSION, EvaluationRecord


class JournalIntegrityError(ValueError):
    """The journal file violates its canonical append-only contract."""


def _sync_directory(directory: Path) -> None:
    """Make the journal's directory entry durable.

    The first append creates the file, so file-content fsync alone does
    not guarantee the entry survives a power loss.  Agent Runtime V1 is
    POSIX-only, so directory fsync is always available.
    """
    directory_fd = os.open(
        directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _parse_datetime(value: object, name: str) -> datetime:
    if type(value) is not str:
        raise JournalIntegrityError(f"{name} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JournalIntegrityError(
            f"{name} must be an ISO datetime string"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise JournalIntegrityError(f"{name} must be timezone-aware")
    return parsed


def _record_from_payload(payload: dict[str, Any], line_number: int) -> EvaluationRecord:
    try:
        return EvaluationRecord(
            evaluation_id=payload["evaluation_id"],
            schema_version=payload["schema_version"],
            site_id=payload["site_id"],
            deployment_id=payload["deployment_id"],
            sequence_number=payload["sequence_number"],
            envelope_id=payload["envelope_id"],
            observation_timestamp_s=payload["observation_timestamp_s"],
            observed_at=_parse_datetime(payload["observed_at"], "observed_at"),
            verdict=EvaluationVerdict(payload["verdict"]),
            policy_id=payload["policy_id"],
            policy_version=payload["policy_version"],
            trace_id=payload["trace_id"],
            snapshot_digest=payload["snapshot_digest"],
            recommendation_id=payload["recommendation_id"],
            recommendation_action=(
                None
                if payload["recommendation_action"] is None
                else RecommendationAction(payload["recommendation_action"])
            ),
            ledger_event_id=payload["ledger_event_id"],
            decision_trace=payload["decision_trace"],
        )
    except KeyError as exc:
        raise JournalIntegrityError(
            f"journal record at line {line_number} is missing {exc}"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise JournalIntegrityError(
            f"invalid journal record at line {line_number}: {exc}"
        ) from exc


def _verify_line(
    raw_line: bytes,
    line_number: int,
    previous: EvaluationRecord | None,
) -> EvaluationRecord:
    if not raw_line.endswith(b"\n"):
        raise JournalIntegrityError(
            f"journal record at line {line_number} is not newline-terminated"
        )
    try:
        line = raw_line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JournalIntegrityError(
            f"invalid encoding at line {line_number}: {exc}"
        ) from exc
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise JournalIntegrityError(
            f"invalid JSON at line {line_number}: {exc.msg}"
        ) from exc
    if type(raw) is not dict:
        raise JournalIntegrityError(
            f"line {line_number} must contain a JSON object"
        )
    record = _record_from_payload(raw, line_number)
    if canonical_json(record) + "\n" != line:
        raise JournalIntegrityError(
            f"non-canonical journal serialization at line {line_number}"
        )
    if record.schema_version != EVALUATION_SCHEMA_VERSION:
        raise JournalIntegrityError(
            f"unsupported journal schema at line {line_number}"
        )
    if previous is not None:
        if (record.site_id, record.deployment_id) != (
            previous.site_id,
            previous.deployment_id,
        ):
            raise JournalIntegrityError(
                f"journal identity changed at line {line_number}"
            )
        if record.sequence_number != previous.sequence_number + 1:
            raise JournalIntegrityError(
                f"journal sequence is not contiguous at line {line_number}"
            )
    return record


def _split_lines(data: bytes) -> list[bytes]:
    return data.splitlines(keepends=True)


class EvaluationJournal:
    """Append-only JSONL journal with verified reads and idempotent appends."""

    def __init__(self, path: str | Path) -> None:
        if _fcntl is None:
            raise RuntimeError(
                "EvaluationJournal requires POSIX fcntl file locking; "
                "use a supported POSIX host or provide a platform backend"
            )
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.RLock()
        # Incremental verification state for this instance: every byte up
        # to _verified_offset has been fully verified by this instance.
        self._verified_offset = 0
        self._verified_line_count = 0
        self._tail_line = b""
        self._last_record: EvaluationRecord | None = None
        self._sequences: dict[int, str] = {}

    # -- cache management ---------------------------------------------------

    def _reset_cache(self) -> None:
        self._verified_offset = 0
        self._verified_line_count = 0
        self._tail_line = b""
        self._last_record = None
        self._sequences = {}

    def _adopt(self, records: list[EvaluationRecord], raw_lines: list[bytes]) -> None:
        self._verified_offset = sum(len(line) for line in raw_lines)
        self._verified_line_count = len(raw_lines)
        self._tail_line = raw_lines[-1] if raw_lines else b""
        self._last_record = records[-1] if records else None
        self._sequences = {
            record.sequence_number: record.evaluation_id for record in records
        }

    def _verify_suffix(self, handle: BinaryIO, size: int) -> None:
        """Verify every byte beyond the instance's verified offset.

        Raises and leaves the cache untouched on any violation; a fresh
        instance (offset zero) verifies the complete file here.
        """
        if size < self._verified_offset:
            raise JournalIntegrityError(
                "journal shrank below its verified offset; append-only "
                "evidence was truncated or replaced"
            )
        if self._tail_line:
            handle.seek(self._verified_offset - len(self._tail_line))
            if handle.read(len(self._tail_line)) != self._tail_line:
                raise JournalIntegrityError(
                    "the verified journal tail changed on disk"
                )
        if size == self._verified_offset:
            return
        handle.seek(self._verified_offset)
        suffix = handle.read(size - self._verified_offset)
        raw_lines = _split_lines(suffix)
        records: list[EvaluationRecord] = []
        previous = self._last_record
        for index, raw_line in enumerate(raw_lines):
            record = _verify_line(
                raw_line, self._verified_line_count + index + 1, previous
            )
            records.append(record)
            previous = record
        # Commit the cache only after the whole suffix verified.
        self._verified_offset = size
        self._verified_line_count += len(raw_lines)
        self._tail_line = raw_lines[-1] if raw_lines else self._tail_line
        self._last_record = previous
        for record in records:
            self._sequences[record.sequence_number] = record.evaluation_id

    # -- public API ---------------------------------------------------------

    def read(self) -> tuple[EvaluationRecord, ...]:
        """Fully verify and return every record.  Never incremental."""
        with self._thread_lock:
            if not self.path.exists():
                self._reset_cache()
                return ()
            with self.path.open("rb") as handle:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_SH)
                try:
                    data = handle.read()
                finally:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
            raw_lines = _split_lines(data)
            records: list[EvaluationRecord] = []
            previous: EvaluationRecord | None = None
            for index, raw_line in enumerate(raw_lines):
                record = _verify_line(raw_line, index + 1, previous)
                records.append(record)
                previous = record
            self._adopt(records, raw_lines)
            return tuple(records)

    def record_for_sequence(self, sequence_number: int) -> EvaluationRecord | None:
        for record in self.read():
            if record.sequence_number == sequence_number:
                return record
        return None

    def latest(self) -> EvaluationRecord | None:
        records = self.read()
        return records[-1] if records else None

    def append(self, record: EvaluationRecord) -> EvaluationRecord:
        """Append one record; identical replays no-op, divergence fails closed.

        Bytes this instance has not yet verified are verified before the
        write, so append cost is linear in a run instead of quadratic.
        """
        if not isinstance(record, EvaluationRecord):
            raise TypeError("record must be an EvaluationRecord")
        with self._thread_lock:
            with self.path.open("a+b") as handle:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
                try:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    self._verify_suffix(handle, size)
                    stored_id = self._sequences.get(record.sequence_number)
                    if stored_id is not None:
                        if stored_id == record.evaluation_id:
                            # evaluation_id is a content digest, so an equal
                            # ID is the identical verified record.
                            return record
                        raise JournalIntegrityError(
                            "sequence "
                            f"{record.sequence_number} is already journaled "
                            "with different content"
                        )
                    if self._last_record is not None:
                        last = self._last_record
                        if (record.site_id, record.deployment_id) != (
                            last.site_id,
                            last.deployment_id,
                        ):
                            raise JournalIntegrityError(
                                "record identity does not match the journal"
                            )
                        if record.sequence_number != last.sequence_number + 1:
                            raise JournalIntegrityError(
                                f"sequence {record.sequence_number} is invalid; "
                                f"expected {last.sequence_number + 1}"
                            )
                    raw_line = (canonical_json(record) + "\n").encode("utf-8")
                    handle.seek(0, os.SEEK_END)
                    handle.write(raw_line)
                    handle.flush()
                    os.fsync(handle.fileno())
                    _sync_directory(self.path.parent)
                    self._verified_offset = size + len(raw_line)
                    self._verified_line_count += 1
                    self._tail_line = raw_line
                    self._last_record = record
                    self._sequences[record.sequence_number] = (
                        record.evaluation_id
                    )
                    return record
                finally:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
