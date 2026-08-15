"""Atomic evaluation-lifecycle checkpoints for Agent Runtime V1.

This checkpoint tracks evaluation progress only.  It is deliberately a
separate fact class from ``nxt_site_runtime.checkpoints.RuntimeCheckpoint``:
the Site Runtime checkpoint owns state-publication progress and must not
carry policy or human-workflow semantics.  The persistence pattern
(prepare/complete, compare-and-save, atomic JSON files) mirrors the Site
Runtime store so operators find one recovery discipline, but the two
checkpoints never share a schema or a file.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Iterator, Protocol, runtime_checkable

try:  # POSIX advisory file locking; module import remains portable.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on non-POSIX platforms.
    _fcntl = None

_IDENTITY_NAMESPACE = "nxt-agent-runtime/evaluation-checkpoint/v1"


class EvaluationCheckpointError(RuntimeError):
    """Checkpoint data or transition is invalid."""


class EvaluationCheckpointConflictError(EvaluationCheckpointError):
    """The stored checkpoint changed after the caller loaded it."""


def _valid_sequence(name: str, value: int | None) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise EvaluationCheckpointError(f"{name} must be a non-negative integer")


def _valid_timestamp(name: str, value: float | None) -> None:
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise EvaluationCheckpointError(f"{name} must be finite and non-negative")


def _valid_identifier(name: str, value: str | None) -> None:
    if value is not None and (
        not isinstance(value, str) or not value or value != value.strip()
    ):
        raise EvaluationCheckpointError(f"{name} must be non-blank and trimmed")


@dataclass(frozen=True)
class EvaluationCheckpoint:
    """Per-(site, deployment) evaluation progress with a pending slot."""

    site_id: str
    deployment_id: str
    last_sequence: int | None = None
    last_observation_timestamp_s: float | None = None
    last_envelope_id: str | None = None
    last_evaluation_id: str | None = None
    last_recovery_attempts: int = 0
    pending_sequence: int | None = None
    pending_observation_timestamp_s: float | None = None
    pending_envelope_id: str | None = None
    pending_evaluation_id: str | None = None
    pending_recovery_attempts: int = 0

    def __post_init__(self) -> None:
        for name in ("site_id", "deployment_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise EvaluationCheckpointError(
                    "checkpoint identity must be non-blank and trimmed"
                )
        completed = (
            self.last_sequence,
            self.last_observation_timestamp_s,
            self.last_envelope_id,
            self.last_evaluation_id,
        )
        pending = (
            self.pending_sequence,
            self.pending_observation_timestamp_s,
            self.pending_envelope_id,
            self.pending_evaluation_id,
        )
        if any(value is None for value in completed) and any(
            value is not None for value in completed
        ):
            raise EvaluationCheckpointError(
                "completed metadata must be all-or-none"
            )
        if any(value is None for value in pending) and any(
            value is not None for value in pending
        ):
            raise EvaluationCheckpointError("pending metadata must be all-or-none")
        _valid_sequence("last_sequence", self.last_sequence)
        _valid_sequence("pending_sequence", self.pending_sequence)
        _valid_timestamp(
            "last_observation_timestamp_s", self.last_observation_timestamp_s
        )
        _valid_timestamp(
            "pending_observation_timestamp_s",
            self.pending_observation_timestamp_s,
        )
        _valid_identifier("last_envelope_id", self.last_envelope_id)
        _valid_identifier("pending_envelope_id", self.pending_envelope_id)
        _valid_identifier("last_evaluation_id", self.last_evaluation_id)
        _valid_identifier("pending_evaluation_id", self.pending_evaluation_id)
        for name in ("last_recovery_attempts", "pending_recovery_attempts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EvaluationCheckpointError(
                    f"{name} must be a non-negative integer"
                )
        if self.last_sequence is None and self.last_recovery_attempts:
            raise EvaluationCheckpointError(
                "recovery count requires a completed evaluation"
            )
        if self.pending_sequence is None and self.pending_recovery_attempts:
            raise EvaluationCheckpointError(
                "pending retries require a pending evaluation"
            )
        if (
            self.last_sequence is not None
            and self.pending_sequence is not None
            and self.pending_sequence != self.last_sequence + 1
        ):
            raise EvaluationCheckpointError(
                "pending sequence must immediately follow the last evaluation"
            )

    @property
    def next_sequence(self) -> int | None:
        """Expected next sequence; ``None`` means the first envelope sets it."""
        if self.last_sequence is None:
            return None
        return self.last_sequence + 1

    @property
    def has_pending_evaluation(self) -> bool:
        return self.pending_sequence is not None

    def prepare(
        self,
        *,
        sequence_number: int,
        observation_timestamp_s: float,
        envelope_id: str,
        evaluation_id: str,
    ) -> "EvaluationCheckpoint":
        if self.has_pending_evaluation:
            if (
                sequence_number != self.pending_sequence
                or observation_timestamp_s != self.pending_observation_timestamp_s
                or envelope_id != self.pending_envelope_id
                or evaluation_id != self.pending_evaluation_id
            ):
                raise EvaluationCheckpointError(
                    "recovery must replay the pending sequence with the "
                    "identical envelope and evaluation identity"
                )
            return replace(
                self,
                pending_recovery_attempts=self.pending_recovery_attempts + 1,
            )
        if self.next_sequence is not None and sequence_number != self.next_sequence:
            raise EvaluationCheckpointError(
                f"sequence {sequence_number} is invalid; "
                f"expected {self.next_sequence}"
            )
        return replace(
            self,
            pending_sequence=sequence_number,
            pending_observation_timestamp_s=observation_timestamp_s,
            pending_envelope_id=envelope_id,
            pending_evaluation_id=evaluation_id,
            pending_recovery_attempts=0,
        )

    def complete(self) -> "EvaluationCheckpoint":
        if not self.has_pending_evaluation:
            raise EvaluationCheckpointError(
                "cannot complete without a pending evaluation"
            )
        return EvaluationCheckpoint(
            site_id=self.site_id,
            deployment_id=self.deployment_id,
            last_sequence=self.pending_sequence,
            last_observation_timestamp_s=self.pending_observation_timestamp_s,
            last_envelope_id=self.pending_envelope_id,
            last_evaluation_id=self.pending_evaluation_id,
            last_recovery_attempts=self.pending_recovery_attempts,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "EvaluationCheckpoint":
        if not isinstance(payload, dict):
            raise EvaluationCheckpointError("checkpoint payload must be an object")
        try:
            return cls(**payload)
        except TypeError as exc:
            raise EvaluationCheckpointError(
                f"invalid checkpoint fields: {exc}"
            ) from exc


@runtime_checkable
class EvaluationCheckpointStore(Protocol):
    def load(self, site_id: str, deployment_id: str) -> EvaluationCheckpoint:
        ...

    def compare_and_save(
        self,
        expected: EvaluationCheckpoint,
        updated: EvaluationCheckpoint,
    ) -> None:
        """Atomically save only when current state still equals ``expected``."""
        ...


class InMemoryEvaluationCheckpointStore:
    def __init__(self) -> None:
        self._checkpoints: dict[tuple[str, str], EvaluationCheckpoint] = {}
        self._lock = RLock()

    @staticmethod
    def _initial(site_id: str, deployment_id: str) -> EvaluationCheckpoint:
        return EvaluationCheckpoint(site_id=site_id, deployment_id=deployment_id)

    def load(self, site_id: str, deployment_id: str) -> EvaluationCheckpoint:
        with self._lock:
            return self._checkpoints.get(
                (site_id, deployment_id), self._initial(site_id, deployment_id)
            )

    def save(self, checkpoint: EvaluationCheckpoint) -> None:
        """Unconditional save for setup/tests; the runtime uses compare-and-save."""
        with self._lock:
            key = (checkpoint.site_id, checkpoint.deployment_id)
            self._checkpoints[key] = checkpoint

    def compare_and_save(
        self,
        expected: EvaluationCheckpoint,
        updated: EvaluationCheckpoint,
    ) -> None:
        if (expected.site_id, expected.deployment_id) != (
            updated.site_id,
            updated.deployment_id,
        ):
            raise EvaluationCheckpointError(
                "checkpoint transition changed identity"
            )
        key = (expected.site_id, expected.deployment_id)
        with self._lock:
            current = self._checkpoints.get(key, self._initial(*key))
            if current != expected:
                raise EvaluationCheckpointConflictError(
                    "checkpoint changed concurrently"
                )
            self._checkpoints[key] = updated


class JsonEvaluationCheckpointStore:
    """Atomic JSON persistence with cross-instance compare-and-save locking.

    Agent Runtime V1 is POSIX-only: like the evaluation journal and the
    snapshot publisher, this store requires ``fcntl`` advisory locking and
    fails loudly at construction on hosts without it.
    """

    def __init__(self, root: str | Path) -> None:
        if _fcntl is None:
            raise RuntimeError(
                "JsonEvaluationCheckpointStore requires POSIX fcntl file "
                "locking; use a supported POSIX host or provide a platform "
                "checkpoint store"
            )
        self.root = Path(root)
        self._lock = RLock()

    @staticmethod
    def _key(site_id: str, deployment_id: str) -> str:
        identity = json.dumps(
            [_IDENTITY_NAMESPACE, site_id, deployment_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    def _path(self, site_id: str, deployment_id: str) -> Path:
        return self.root / f"{self._key(site_id, deployment_id)}.json"

    def _load_unlocked(
        self, site_id: str, deployment_id: str
    ) -> EvaluationCheckpoint:
        path = self._path(site_id, deployment_id)
        if not path.exists():
            return EvaluationCheckpoint(
                site_id=site_id, deployment_id=deployment_id
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            checkpoint = EvaluationCheckpoint.from_dict(payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise EvaluationCheckpointError(
                f"invalid checkpoint {path.name}: {exc}"
            ) from exc
        if (checkpoint.site_id, checkpoint.deployment_id) != (
            site_id,
            deployment_id,
        ):
            raise EvaluationCheckpointError(
                "checkpoint identity does not match lookup key"
            )
        return checkpoint

    @contextmanager
    def _identity_lock(self, site_id: str, deployment_id: str) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / f".{self._key(site_id, deployment_id)}.lock"
        with lock_path.open("a+b") as lock_file:
            _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)

    def load(self, site_id: str, deployment_id: str) -> EvaluationCheckpoint:
        with self._lock:
            return self._load_unlocked(site_id, deployment_id)

    def _write_unlocked(self, checkpoint: EvaluationCheckpoint) -> None:
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
            # as the file contents.
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
            raise EvaluationCheckpointError(
                f"could not persist checkpoint: {exc}"
            ) from exc

    def save(self, checkpoint: EvaluationCheckpoint) -> None:
        """Unconditional save for setup/tests; the runtime uses compare-and-save."""
        with self._lock, self._identity_lock(
            checkpoint.site_id, checkpoint.deployment_id
        ):
            self._write_unlocked(checkpoint)

    def compare_and_save(
        self,
        expected: EvaluationCheckpoint,
        updated: EvaluationCheckpoint,
    ) -> None:
        if (expected.site_id, expected.deployment_id) != (
            updated.site_id,
            updated.deployment_id,
        ):
            raise EvaluationCheckpointError(
                "checkpoint transition changed identity"
            )
        with self._lock, self._identity_lock(
            expected.site_id, expected.deployment_id
        ):
            current = self._load_unlocked(
                expected.site_id, expected.deployment_id
            )
            if current != expected:
                raise EvaluationCheckpointConflictError(
                    "checkpoint changed concurrently"
                )
            self._write_unlocked(updated)
