"""Append-only JSONL journal shared by the Edge and the protocol doubles.

One file per writer role.  Every record is canonical JSON on one line
with a contiguous ``sequence``, a content-derived ``record_id``, the
``record_kind``, the ``origin`` of the fact (who asserted it), and the
caller-supplied ``recorded_at_utc`` -- the package itself never reads a
clock.  Appends take an exclusive POSIX advisory lock, verify the whole
file first, run the caller's *builder* against the verified records so
every precondition is evaluated inside the lock, write the new lines,
fsync the file, and fsync the directory entry.  Reads verify the whole
file.  A truncated, non-canonical, out-of-sequence, or mismatched-digest
line is an integrity error: the journal never repairs itself and never
presents a partially readable file as a clean state.

The persistence pattern combines the Shadow Ops ledger's
lock-verify-append-fsync protocol with the Agent Runtime evaluation
journal's directory fsync (both reimplemented here; nothing is imported);
it deliberately has no hash chain because tamper evidence is not a V0
claim of this rehearsal journal.

Multiple processes may append to the same file (the Edge gateway and
the local CLI): the builder protocol is the only write path, so no
writer decides from a pre-lock replay.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

try:  # POSIX advisory locking; the module imports on every platform.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX hosts fail at construction
    _fcntl = None

from .contracts import canonical_json, stable_digest

JOURNAL_SCHEMA = "nxt-edge-task/journal/v1"
ANCHOR_SCHEMA = "nxt-edge-task/journal-anchor/v1"

ORIGINS = frozenset({"EDGE", "ROBOT", "SIM_ENTRY", "OPERATOR", "CHANNEL", "DEVICE"})

_RECORD_KEYS = frozenset(
    {"schema_version", "sequence", "record_id", "record_kind", "origin", "recorded_at_utc", "payload"}
)


class JournalIntegrityError(ValueError):
    """The journal file violates its append-only canonical contract."""


class PreconditionFailed(ValueError):
    """A builder refused to append; ``code`` is machine-readable."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class RecordSpec:
    """What a builder asks the journal to append."""

    record_kind: str
    origin: str
    recorded_at_utc: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class JournalRecord:
    schema_version: str
    sequence: int
    record_id: str
    record_kind: str
    origin: str
    recorded_at_utc: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "record_id": self.record_id,
            "record_kind": self.record_kind,
            "origin": self.origin,
            "recorded_at_utc": self.recorded_at_utc,
            "payload": _thaw(self.payload),
        }


def _record_id(sequence: int, spec: RecordSpec) -> str:
    seed = {
        "sequence": sequence,
        "record_kind": spec.record_kind,
        "origin": spec.origin,
        "recorded_at_utc": spec.recorded_at_utc,
        "payload": _thaw(spec.payload),
    }
    return "rec_" + stable_digest(seed)[:24]


def _sync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class JsonlJournal:
    """Verified append-only JSONL journal with in-lock builders."""

    def __init__(
        self,
        path: str | Path,
        *,
        allowed_kinds: frozenset[str] | None = None,
        allowed_origins: frozenset[str] = ORIGINS,
    ) -> None:
        if _fcntl is None:
            raise RuntimeError("JsonlJournal requires POSIX fcntl advisory locking")
        self.path = Path(path)
        # The directory is created on the first append, never by a reader.
        self._allowed_kinds = allowed_kinds
        self._allowed_origins = allowed_origins
        # Verified-prefix cache: (byte length, sha256 of those bytes, records).
        # A prefix whose bytes still hash identically was already verified by
        # this instance, so only the new suffix is parsed; any change inside
        # the prefix invalidates the cache and forces a full re-verification.
        self._cache: tuple[int, str, tuple[JournalRecord, ...]] | None = None
        # High-water anchor: a sibling file recording how many records this
        # journal has reached and the id of the last one.  A journal that is
        # shorter than its anchor, or whose record at the anchor differs, was
        # rolled back to an older (still valid-looking) prefix: that is state
        # loss, not a crash, and it fails loud on every read and append.
        self.anchor_path = self.path.with_name(self.path.name + ".hwm")

    # ------------------------------------------------------------------
    # High-water anchor
    # ------------------------------------------------------------------

    def read_anchor(self) -> tuple[int, str | None] | None:
        """``(records, last_record_id)`` from the anchor file, or ``None`` when absent."""

        if not self.anchor_path.exists():
            return None
        try:
            raw = json.loads(self.anchor_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise JournalIntegrityError(f"{self.anchor_path.name}: unreadable anchor ({exc})") from exc
        if (
            type(raw) is not dict
            or frozenset(raw) != frozenset({"schema", "records", "last_record_id"})
            or raw["schema"] != ANCHOR_SCHEMA
            or type(raw["records"]) is not int
            or raw["records"] < 0
            or (raw["last_record_id"] is not None and type(raw["last_record_id"]) is not str)
            or (raw["records"] == 0) != (raw["last_record_id"] is None)
        ):
            raise JournalIntegrityError(f"{self.anchor_path.name}: malformed anchor")
        return raw["records"], raw["last_record_id"]

    def _check_anchor(self, records: tuple[JournalRecord, ...]) -> None:
        anchor = self.read_anchor()
        if anchor is None:
            if records:
                raise JournalIntegrityError(
                    f"{self.path.name}: {len(records)} records but no anchor; state continuity cannot be established"
                )
            return
        expected, last_id = anchor
        if len(records) < expected:
            raise JournalIntegrityError(
                f"{self.path.name}: rolled back to {len(records)} records below the anchored {expected} (state loss)"
            )
        if expected and records[expected - 1].record_id != last_id:
            raise JournalIntegrityError(f"{self.path.name}: record {expected} differs from the anchored record (state loss)")

    def _write_anchor(self, records: int, last_id: str | None) -> None:
        body = canonical_json({"schema": ANCHOR_SCHEMA, "records": records, "last_record_id": last_id}).encode("utf-8")
        tmp = self.anchor_path.with_name(self.anchor_path.name + ".tmp")
        with tmp.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.anchor_path)
        _sync_directory(self.path.parent)

    def discard_anchor(self) -> None:
        """Explicitly forget the anchor of a missing or empty journal.

        The only legitimate caller is an operator re-provisioning an identity
        whose journal is gone; a non-empty journal keeps its anchor.
        """

        if self.path.exists() and self.path.stat().st_size:
            raise JournalIntegrityError(f"{self.path.name}: refusing to discard the anchor of a non-empty journal")
        if self.anchor_path.exists():
            self.anchor_path.unlink()
            _sync_directory(self.path.parent)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read(self, *, anchored: bool = True) -> tuple[JournalRecord, ...]:
        """Verified records.  ``anchored=False`` skips the high-water check.

        Every process reads anchored; only an out-of-process inspector (a
        test asserting what survived a rollback) may read unanchored.
        """

        if not self.path.exists():
            if anchored:
                self._check_anchor(())
            return ()
        with self.path.open("rb") as handle:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_SH)
            try:
                records = self._verify(handle.read())
                if anchored:
                    self._check_anchor(records)
                return records
            finally:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)

    def _verify(self, data: bytes) -> tuple[JournalRecord, ...]:
        if not data:
            self._cache = None
            return ()
        if not data.endswith(b"\n"):
            raise JournalIntegrityError(
                f"{self.path.name}: last record is not newline-terminated (truncated write)"
            )
        prefix_records: tuple[JournalRecord, ...] = ()
        start_line = 1
        offset = 0
        cache = self._cache
        if cache is not None:
            length, digest, cached_records = cache
            if len(data) >= length and hashlib.sha256(data[:length]).hexdigest() == digest:
                prefix_records = cached_records
                start_line = len(cached_records) + 1
                offset = length
        records = self._parse_lines(data[offset:], start_line)
        result = prefix_records + records
        self._cache = (len(data), hashlib.sha256(data).hexdigest(), result)
        return result

    def _parse_lines(self, data: bytes, start_line: int) -> tuple[JournalRecord, ...]:
        records: list[JournalRecord] = []
        if not data:
            return ()
        for line_number, raw_line in enumerate(data.split(b"\n")[:-1], start=start_line):
            try:
                text = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise JournalIntegrityError(f"{self.path.name}: line {line_number} is not UTF-8") from exc
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise JournalIntegrityError(
                    f"{self.path.name}: line {line_number} is not JSON ({exc.msg})"
                ) from exc
            if type(raw) is not dict or frozenset(raw) != _RECORD_KEYS:
                raise JournalIntegrityError(f"{self.path.name}: line {line_number} has unexpected keys")
            try:
                canonical = canonical_json(raw)
            except (TypeError, ValueError) as exc:
                raise JournalIntegrityError(f"{self.path.name}: line {line_number} is not canonical JSON ({exc})") from exc
            if canonical != text:
                raise JournalIntegrityError(f"{self.path.name}: line {line_number} is not canonical JSON")
            if raw["schema_version"] != JOURNAL_SCHEMA:
                raise JournalIntegrityError(f"{self.path.name}: line {line_number} has an unsupported schema")
            if type(raw["sequence"]) is not int or raw["sequence"] != line_number:
                raise JournalIntegrityError(f"{self.path.name}: sequence mismatch at line {line_number}")
            if raw["origin"] not in self._allowed_origins:
                raise JournalIntegrityError(f"{self.path.name}: unknown origin at line {line_number}")
            if self._allowed_kinds is not None and raw["record_kind"] not in self._allowed_kinds:
                raise JournalIntegrityError(
                    f"{self.path.name}: unknown record_kind {raw['record_kind']!r} at line {line_number}"
                )
            if type(raw["payload"]) is not dict:
                raise JournalIntegrityError(f"{self.path.name}: payload must be an object at line {line_number}")
            spec = RecordSpec(
                record_kind=raw["record_kind"],
                origin=raw["origin"],
                recorded_at_utc=raw["recorded_at_utc"],
                payload=raw["payload"],
            )
            if raw["record_id"] != _record_id(line_number, spec):
                raise JournalIntegrityError(f"{self.path.name}: record_id mismatch at line {line_number}")
            records.append(
                JournalRecord(
                    schema_version=JOURNAL_SCHEMA,
                    sequence=line_number,
                    record_id=raw["record_id"],
                    record_kind=raw["record_kind"],
                    origin=raw["origin"],
                    recorded_at_utc=raw["recorded_at_utc"],
                    payload=_freeze(raw["payload"]),
                )
            )
        return tuple(records)

    # ------------------------------------------------------------------
    # Appending
    # ------------------------------------------------------------------

    def append_via(
        self,
        builder: Callable[[tuple[JournalRecord, ...]], Sequence[RecordSpec]],
    ) -> tuple[JournalRecord, ...]:
        """Run ``builder`` inside the exclusive lock and append what it returns.

        The builder receives every verified record and may raise
        ``PreconditionFailed``; returning an empty sequence appends nothing.
        All returned specs are written in order under the same lock, then the
        file and its directory are fsynced.
        """

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as handle:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            try:
                handle.seek(0)
                records = self._verify(handle.read())
                self._check_anchor(records)
                specs = list(builder(records))
                appended: list[JournalRecord] = []
                if not specs:
                    return ()
                lines: list[bytes] = []
                next_sequence = len(records) + 1
                for spec in specs:
                    if spec.origin not in self._allowed_origins:
                        raise ValueError(f"origin {spec.origin!r} is not allowed in this journal")
                    if self._allowed_kinds is not None and spec.record_kind not in self._allowed_kinds:
                        raise ValueError(f"record_kind {spec.record_kind!r} is not allowed in this journal")
                    payload = _thaw(spec.payload)
                    if type(payload) is not dict:
                        raise TypeError("payload must be a mapping")
                    record_id = _record_id(next_sequence, spec)
                    body = {
                        "schema_version": JOURNAL_SCHEMA,
                        "sequence": next_sequence,
                        "record_id": record_id,
                        "record_kind": spec.record_kind,
                        "origin": spec.origin,
                        "recorded_at_utc": spec.recorded_at_utc,
                        "payload": payload,
                    }
                    lines.append(canonical_json(body).encode("utf-8") + b"\n")
                    appended.append(
                        JournalRecord(
                            schema_version=JOURNAL_SCHEMA,
                            sequence=next_sequence,
                            record_id=record_id,
                            record_kind=spec.record_kind,
                            origin=spec.origin,
                            recorded_at_utc=spec.recorded_at_utc,
                            payload=_freeze(payload),
                        )
                    )
                    next_sequence += 1
                handle.seek(0, os.SEEK_END)
                handle.write(b"".join(lines))
                handle.flush()
                os.fsync(handle.fileno())
                _sync_directory(self.path.parent)
                # The anchor is written only after the records are durable, so
                # it can lag the journal (a crash between the two) but never
                # lead it.
                self._write_anchor(appended[-1].sequence, appended[-1].record_id)
                return tuple(appended)
            finally:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)

    def append(self, spec: RecordSpec) -> JournalRecord:
        """Append one record with no precondition."""

        return self.append_via(lambda _records: [spec])[0]


__all__ = [
    "ANCHOR_SCHEMA",
    "JOURNAL_SCHEMA",
    "ORIGINS",
    "JournalIntegrityError",
    "JournalRecord",
    "JsonlJournal",
    "PreconditionFailed",
    "RecordSpec",
]
