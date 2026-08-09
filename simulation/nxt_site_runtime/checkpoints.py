"""Atomic, replay-safe checkpoints for Site Runtime publication."""

from __future__ import annotations

import json
import math
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Iterator, Protocol, runtime_checkable


class CheckpointError(RuntimeError):
    """Checkpoint data or transition is invalid."""


class CheckpointConflictError(CheckpointError):
    """The stored checkpoint changed after the caller loaded it."""


def _valid_sequence(name: str, value: int | None) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise CheckpointError(f"{name} must be a non-negative integer")


def _valid_timestamp(name: str, value: float | None) -> None:
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise CheckpointError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class RuntimeCheckpoint:
    site_id: str
    deployment_id: str
    last_successful_sequence: int | None = None
    last_observation_timestamp_s: float | None = None
    last_envelope_id: str | None = None
    last_recovery_attempts: int = 0
    pending_sequence: int | None = None
    pending_observation_timestamp_s: float | None = None
    pending_envelope_id: str | None = None
    pending_recovery_attempts: int = 0

    def __post_init__(self) -> None:
        for name in ("site_id", "deployment_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise CheckpointError(
                    "checkpoint identity must be non-blank and trimmed"
                )

        completed = (
            self.last_successful_sequence,
            self.last_observation_timestamp_s,
            self.last_envelope_id,
        )
        pending = (
            self.pending_sequence,
            self.pending_observation_timestamp_s,
            self.pending_envelope_id,
        )
        if any(value is None for value in completed) and any(
            value is not None for value in completed
        ):
            raise CheckpointError("completed metadata must be all-or-none")
        if any(value is None for value in pending) and any(
            value is not None for value in pending
        ):
            raise CheckpointError("pending metadata must be all-or-none")

        _valid_sequence("last_successful_sequence", self.last_successful_sequence)
        _valid_sequence("pending_sequence", self.pending_sequence)
        _valid_timestamp(
            "last_observation_timestamp_s", self.last_observation_timestamp_s
        )
        _valid_timestamp(
            "pending_observation_timestamp_s",
            self.pending_observation_timestamp_s,
        )
        for name in ("last_recovery_attempts", "pending_recovery_attempts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CheckpointError(f"{name} must be a non-negative integer")
        for name in ("last_envelope_id", "pending_envelope_id"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value or value != value.strip()
            ):
                raise CheckpointError(f"{name} must be non-blank and trimmed")

        if self.last_successful_sequence is None and self.last_recovery_attempts:
            raise CheckpointError("recovery count requires a completed publish")
        if self.pending_sequence is None and self.pending_recovery_attempts:
            raise CheckpointError("pending retries require a pending publish")
        if (
            self.last_successful_sequence is not None
            and self.pending_sequence is not None
            and self.pending_sequence != self.last_successful_sequence + 1
        ):
            raise CheckpointError(
                "pending sequence must immediately follow last successful sequence"
            )

    @property
    def next_sequence(self) -> int | None:
        """Expected next sequence; ``None`` means the first frame sets it."""
        if self.last_successful_sequence is None:
            return None
        return self.last_successful_sequence + 1

    @property
    def has_pending_publish(self) -> bool:
        return self.pending_sequence is not None

    @property
    def recovery_metadata(self) -> dict:
        return {
            "pending_sequence": self.pending_sequence,
            "pending_observation_timestamp_s": self.pending_observation_timestamp_s,
            "pending_envelope_id": self.pending_envelope_id,
            "pending_recovery_attempts": self.pending_recovery_attempts,
        }

    def prepare(
        self,
        *,
        sequence_number: int,
        observation_timestamp_s: float,
        envelope_id: str,
    ) -> RuntimeCheckpoint:
        if self.has_pending_publish:
            if (
                sequence_number != self.pending_sequence
                or observation_timestamp_s != self.pending_observation_timestamp_s
                or envelope_id != self.pending_envelope_id
            ):
                raise CheckpointError(
                    "recovery must replay the pending sequence and exact envelope"
                )
            return replace(
                self,
                pending_recovery_attempts=self.pending_recovery_attempts + 1,
            )
        if self.next_sequence is not None and sequence_number != self.next_sequence:
            raise CheckpointError(
                f"sequence {sequence_number} is invalid; expected {self.next_sequence}"
            )
        return replace(
            self,
            pending_sequence=sequence_number,
            pending_observation_timestamp_s=observation_timestamp_s,
            pending_envelope_id=envelope_id,
            pending_recovery_attempts=0,
        )

    def complete(self) -> RuntimeCheckpoint:
        if not self.has_pending_publish:
            raise CheckpointError("cannot complete without a pending publish")
        return RuntimeCheckpoint(
            site_id=self.site_id,
            deployment_id=self.deployment_id,
            last_successful_sequence=self.pending_sequence,
            last_observation_timestamp_s=self.pending_observation_timestamp_s,
            last_envelope_id=self.pending_envelope_id,
            last_recovery_attempts=self.pending_recovery_attempts,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> RuntimeCheckpoint:
        if not isinstance(payload, dict):
            raise CheckpointError("checkpoint payload must be an object")
        try:
            return cls(**payload)
        except TypeError as exc:
            raise CheckpointError(f"invalid checkpoint fields: {exc}") from exc


@runtime_checkable
class CheckpointStore(Protocol):
    def load(self, site_id: str, deployment_id: str) -> RuntimeCheckpoint:
        ...

    def compare_and_save(
        self,
        expected: RuntimeCheckpoint,
        updated: RuntimeCheckpoint,
    ) -> None:
        """Atomically save only when current state still equals ``expected``."""
        ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._checkpoints: dict[tuple[str, str], RuntimeCheckpoint] = {}
        self._lock = RLock()

    @staticmethod
    def _initial(site_id: str, deployment_id: str) -> RuntimeCheckpoint:
        return RuntimeCheckpoint(site_id=site_id, deployment_id=deployment_id)

    def load(self, site_id: str, deployment_id: str) -> RuntimeCheckpoint:
        with self._lock:
            return self._checkpoints.get(
                (site_id, deployment_id), self._initial(site_id, deployment_id)
            )

    def save(self, checkpoint: RuntimeCheckpoint) -> None:
        """Unconditional save for setup/tests; pipeline uses compare-and-save."""
        with self._lock:
            self._checkpoints[(checkpoint.site_id, checkpoint.deployment_id)] = checkpoint

    def compare_and_save(
        self, expected: RuntimeCheckpoint, updated: RuntimeCheckpoint
    ) -> None:
        if (expected.site_id, expected.deployment_id) != (
            updated.site_id,
            updated.deployment_id,
        ):
            raise CheckpointError("checkpoint transition changed identity")
        key = (expected.site_id, expected.deployment_id)
        with self._lock:
            current = self._checkpoints.get(key, self._initial(*key))
            if current != expected:
                raise CheckpointConflictError("checkpoint changed concurrently")
            self._checkpoints[key] = updated


class JsonCheckpointStore:
    """Atomic JSON persistence with cross-instance compare-and-save locking."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = RLock()

    @staticmethod
    def _key(site_id: str, deployment_id: str) -> str:
        import hashlib

        identity = json.dumps(
            [site_id, deployment_id], ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    def _path(self, site_id: str, deployment_id: str) -> Path:
        return self.root / f"{self._key(site_id, deployment_id)}.json"

    def _load_unlocked(
        self, site_id: str, deployment_id: str
    ) -> RuntimeCheckpoint:
        path = self._path(site_id, deployment_id)
        if not path.exists():
            return RuntimeCheckpoint(site_id=site_id, deployment_id=deployment_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            checkpoint = RuntimeCheckpoint.from_dict(payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"invalid checkpoint {path.name}: {exc}") from exc
        if (checkpoint.site_id, checkpoint.deployment_id) != (
            site_id,
            deployment_id,
        ):
            raise CheckpointError("checkpoint identity does not match lookup key")
        return checkpoint

    @contextmanager
    def _identity_lock(self, site_id: str, deployment_id: str) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / f".{self._key(site_id, deployment_id)}.lock"
        with lock_path.open("a+b") as lock_file:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def load(self, site_id: str, deployment_id: str) -> RuntimeCheckpoint:
        with self._lock:
            return self._load_unlocked(site_id, deployment_id)

    def _write_unlocked(self, checkpoint: RuntimeCheckpoint) -> None:
        path = self._path(checkpoint.site_id, checkpoint.deployment_id)
        serialized = json.dumps(
            checkpoint.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=self.root,
                encoding="utf-8",
                delete=False,
                prefix=f".{path.name}.",
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(serialized)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
            # POSIX durability requires syncing the directory entry as well
            # as the file contents. Windows does not expose directory fsync.
            if os.name != "nt":
                directory_fd = os.open(
                    self.root,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OSError as exc:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise CheckpointError(f"could not persist checkpoint: {exc}") from exc

    def save(self, checkpoint: RuntimeCheckpoint) -> None:
        """Unconditional save for setup/tests; pipeline uses compare-and-save."""
        with self._lock, self._identity_lock(
            checkpoint.site_id, checkpoint.deployment_id
        ):
            self._write_unlocked(checkpoint)

    def compare_and_save(
        self, expected: RuntimeCheckpoint, updated: RuntimeCheckpoint
    ) -> None:
        if (expected.site_id, expected.deployment_id) != (
            updated.site_id,
            updated.deployment_id,
        ):
            raise CheckpointError("checkpoint transition changed identity")
        with self._lock, self._identity_lock(
            expected.site_id, expected.deployment_id
        ):
            current = self._load_unlocked(
                expected.site_id, expected.deployment_id
            )
            if current != expected:
                raise CheckpointConflictError("checkpoint changed concurrently")
            self._write_unlocked(updated)
