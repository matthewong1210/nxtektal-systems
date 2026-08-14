"""Local deterministic snapshot publisher for demos and evidence output.

``JsonlSnapshotPublisher`` implements the existing
``nxt_site_runtime.StatePublisher`` port by appending each published
envelope's existing ``to_dict()`` serialization to a JSONL file,
idempotently keyed by ``envelope_id``.

This is a local composition/demo publisher, not a production state sink:
the file is a regenerable presentation/replay projection of published
envelopes.  ``FacilityState`` truth ownership is unchanged, and the file
must never be read back as live state input.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    """Make the stream's directory entry durable on POSIX hosts."""
    if os.name == "nt":  # pragma: no cover - exercised on Windows hosts.
        return
    directory_fd = os.open(
        directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


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

    @staticmethod
    def _parse_lines(lines: list[str]) -> dict[str, str]:
        stored: dict[str, str] = {}
        for line_number, line in enumerate(lines, start=1):
            if not line.endswith("\n"):
                raise SnapshotPublisherError(
                    f"snapshot record at line {line_number} is not "
                    "newline-terminated"
                )
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SnapshotPublisherError(
                    f"invalid JSON at line {line_number}: {exc.msg}"
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
            if envelope_id in stored:
                raise SnapshotPublisherError(
                    f"duplicate envelope_id at line {line_number}"
                )
            stored[envelope_id] = line
        return stored

    def published_envelope_ids(self) -> tuple[str, ...]:
        with self._thread_lock:
            if not self.path.exists():
                return ()
            with self.path.open("r", encoding="utf-8", newline="") as handle:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_SH)
                try:
                    stored = self._parse_lines(handle.readlines())
                finally:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
            return tuple(stored)

    def publish(self, envelope: FacilitySnapshotEnvelope) -> None:
        serialized = canonical_json(envelope.to_dict()) + "\n"
        with self._thread_lock:
            with self.path.open("a+", encoding="utf-8", newline="\n") as handle:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
                try:
                    handle.seek(0)
                    stored = self._parse_lines(handle.readlines())
                    existing = stored.get(envelope.envelope_id)
                    if existing is not None:
                        if existing != serialized:
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
                finally:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
