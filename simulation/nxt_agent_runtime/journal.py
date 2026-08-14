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
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

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
    """Make the journal's directory entry durable on POSIX hosts.

    The first append creates the file, so file-content fsync alone does
    not guarantee the entry survives a power loss.  Windows does not
    expose directory fsync.
    """
    if os.name == "nt":  # pragma: no cover - exercised on Windows hosts.
        return
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


def _read_records(handle: TextIO) -> list[EvaluationRecord]:
    records: list[EvaluationRecord] = []
    previous: EvaluationRecord | None = None
    for line_number, line in enumerate(handle, start=1):
        if not line.endswith("\n"):
            raise JournalIntegrityError(
                f"journal record at line {line_number} is not newline-terminated"
            )
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
        records.append(record)
        previous = record
    return records


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

    def read(self) -> tuple[EvaluationRecord, ...]:
        with self._thread_lock:
            if not self.path.exists():
                return ()
            with self.path.open("r", encoding="utf-8", newline="") as handle:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_SH)
                try:
                    return tuple(_read_records(handle))
                finally:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)

    def record_for_sequence(self, sequence_number: int) -> EvaluationRecord | None:
        for record in self.read():
            if record.sequence_number == sequence_number:
                return record
        return None

    def latest(self) -> EvaluationRecord | None:
        records = self.read()
        return records[-1] if records else None

    def append(self, record: EvaluationRecord) -> EvaluationRecord:
        """Append one record; identical replays no-op, divergence fails closed."""
        if not isinstance(record, EvaluationRecord):
            raise TypeError("record must be an EvaluationRecord")
        with self._thread_lock:
            with self.path.open("a+", encoding="utf-8", newline="\n") as handle:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
                try:
                    handle.seek(0)
                    existing = _read_records(handle)
                    for stored in existing:
                        if stored.sequence_number == record.sequence_number:
                            if stored.evaluation_id == record.evaluation_id:
                                return stored
                            raise JournalIntegrityError(
                                "sequence "
                                f"{record.sequence_number} is already journaled "
                                "with different content"
                            )
                    if existing:
                        last = existing[-1]
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
                    handle.seek(0, os.SEEK_END)
                    handle.write(canonical_json(record) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    _sync_directory(self.path.parent)
                    return record
                finally:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
