"""Local deterministic snapshot publisher for demos and evidence output.

``JsonlSnapshotPublisher`` implements the existing
``nxt_site_runtime.StatePublisher`` port by appending each published
envelope's existing ``to_dict()`` serialization to a JSONL file,
idempotently keyed by ``envelope_id``.

This is a local composition/demo publisher, not a production state sink:
the file is a regenerable presentation/replay projection of published
envelopes.  ``FacilityState`` truth ownership is unchanged, and the file
must never be read back as live state input.

Verification strategy mirrors the evaluation journal: each instance
verifies every byte once (a fresh instance verifies the whole file on its
first publish), then verifies only appended bytes plus a tail anchor per
publish, keeping a run of N publications linear instead of quadratic.
``published_envelope_ids()`` always verifies the complete file.  A
duplicate publish re-reads the stored line and compares bytes, so an
identical envelope no-ops and a same-ID content mismatch fails closed.

Agent Runtime V1 is POSIX-only: the publisher requires ``fcntl`` advisory
locking and fails loudly at construction on hosts without it.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

try:  # POSIX advisory file locking; module import remains portable.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on non-POSIX platforms.
    _fcntl = None

from nxt_site_runtime.envelope import canonical_json

if TYPE_CHECKING:
    from nxt_site_runtime.envelope import FacilitySnapshotEnvelope
else:
    FacilitySnapshotEnvelope = Any


class SnapshotPublisherError(RuntimeError):
    """The snapshot stream file violates its append-only contract."""


def _sync_directory(directory: Path) -> None:
    """Make the stream's directory entry durable (POSIX-only runtime)."""
    directory_fd = os.open(
        directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _parse_line(raw_line: bytes, line_number: int) -> str:
    if not raw_line.endswith(b"\n"):
        raise SnapshotPublisherError(
            f"snapshot record at line {line_number} is not newline-terminated"
        )
    try:
        payload = json.loads(raw_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotPublisherError(
            f"invalid snapshot record at line {line_number}: {exc}"
        ) from exc
    if type(payload) is not dict:
        raise SnapshotPublisherError(
            f"line {line_number} must contain a JSON object"
        )
    envelope_id = payload.get("envelope_id")
    if type(envelope_id) is not str or not envelope_id:
        raise SnapshotPublisherError(
            f"line {line_number} is missing envelope_id"
        )
    return envelope_id


class JsonlSnapshotPublisher:
    """Idempotent JSONL publisher keyed by ``envelope.envelope_id``."""

    def __init__(self, path: str | Path) -> None:
        if _fcntl is None:
            raise RuntimeError(
                "JsonlSnapshotPublisher requires POSIX fcntl file locking; "
                "use a supported POSIX host or provide a platform backend"
            )
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.RLock()
        # Incremental verification state: every byte up to _verified_offset
        # has been parsed and duplicate-checked by this instance.
        self._verified_offset = 0
        self._verified_line_count = 0
        self._tail_line = b""
        self._stored: dict[str, tuple[int, int]] = {}

    def _verify_suffix(self, handle: BinaryIO, size: int) -> None:
        if size < self._verified_offset:
            raise SnapshotPublisherError(
                "snapshot stream shrank below its verified offset; "
                "append-only output was truncated or replaced"
            )
        if self._tail_line:
            handle.seek(self._verified_offset - len(self._tail_line))
            if handle.read(len(self._tail_line)) != self._tail_line:
                raise SnapshotPublisherError(
                    "the verified snapshot tail changed on disk"
                )
        if size == self._verified_offset:
            return
        handle.seek(self._verified_offset)
        suffix = handle.read(size - self._verified_offset)
        raw_lines = suffix.splitlines(keepends=True)
        offset = self._verified_offset
        new_entries: dict[str, tuple[int, int]] = {}
        last_line = self._tail_line
        for index, raw_line in enumerate(raw_lines):
            envelope_id = _parse_line(
                raw_line, self._verified_line_count + index + 1
            )
            if envelope_id in self._stored or envelope_id in new_entries:
                raise SnapshotPublisherError(
                    "duplicate envelope_id at line "
                    f"{self._verified_line_count + index + 1}"
                )
            new_entries[envelope_id] = (offset, len(raw_line))
            offset += len(raw_line)
            last_line = raw_line
        self._verified_offset = size
        self._verified_line_count += len(raw_lines)
        self._tail_line = last_line
        self._stored.update(new_entries)

    def published_envelope_ids(self) -> tuple[str, ...]:
        """Fully parse and return every stored envelope ID."""
        with self._thread_lock:
            if not self.path.exists():
                return ()
            with self.path.open("rb") as handle:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_SH)
                try:
                    data = handle.read()
                finally:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
            raw_lines = data.splitlines(keepends=True)
            stored: dict[str, tuple[int, int]] = {}
            offset = 0
            for index, raw_line in enumerate(raw_lines):
                envelope_id = _parse_line(raw_line, index + 1)
                if envelope_id in stored:
                    raise SnapshotPublisherError(
                        f"duplicate envelope_id at line {index + 1}"
                    )
                stored[envelope_id] = (offset, len(raw_line))
                offset += len(raw_line)
            self._verified_offset = offset
            self._verified_line_count = len(raw_lines)
            self._tail_line = raw_lines[-1] if raw_lines else b""
            self._stored = stored
            return tuple(stored)

    def publish(self, envelope: FacilitySnapshotEnvelope) -> None:
        serialized = (canonical_json(envelope.to_dict()) + "\n").encode("utf-8")
        with self._thread_lock:
            with self.path.open("a+b") as handle:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
                try:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    self._verify_suffix(handle, size)
                    existing = self._stored.get(envelope.envelope_id)
                    if existing is not None:
                        offset, length = existing
                        handle.seek(offset)
                        if handle.read(length) != serialized:
                            raise SnapshotPublisherError(
                                "envelope "
                                f"{envelope.envelope_id} is already stored "
                                "with different content"
                            )
                        return
                    handle.seek(0, os.SEEK_END)
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                    _sync_directory(self.path.parent)
                    self._stored[envelope.envelope_id] = (size, len(serialized))
                    self._verified_offset = size + len(serialized)
                    self._verified_line_count += 1
                    self._tail_line = serialized
                finally:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
