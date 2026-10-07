"""Hash-chained, prefix-anchored persistence for staffing advisory events.

The ledger deliberately owns durability only.  Event meaning and transition
validity remain in :mod:`nxt_pilot_ops.staffing.workflow`.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator, overload

try:  # The storage contract is intentionally POSIX-only.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised by capability tests.
    _fcntl = None

from ..serialization import canonical_json_bytes, stable_digest, to_primitive
from .contracts import (
    EVENT_TO_OPERATION,
    HEX_DIGEST_PATTERN,
    IDENTIFIER_PATTERN,
    OPERATION_KINDS,
    AppendDecision,
    AppendEventDecision,
    CommittedReceipt,
    ConflictDecision,
    ConflictReceipt,
    DuplicateReceipt,
    ReceiptResult,
    ReturnReceiptDecision,
    StaffingError,
    StaffingEvent,
    StaffingHistory,
    StaffingReceipt,
    VerifiedLedgerState,
)
from .workflow import EVENT_RECORD_KEYS, parse_event, replay_staffing, transition


GENESIS_HASH = "0" * 64
_RECORD_SCHEMA_VERSION = 1
_ANCHOR_SCHEMA = "nxt-staffing-ledger-anchor/v1"
_LEDGER_NAME = "staffing.jsonl"
_ANCHOR_NAME = "staffing.anchor.json"
_ANCHOR_TEMP_NAME = "staffing.anchor.json.tmp"
_LOCK_NAME = ".staffing.lock"
_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "sequence",
        "event_id",
        "event_type",
        "site_id",
        "deployment_id",
        "occurred_at_utc",
        "payload",
        "causation_id",
        "previous_hash",
        "record_hash",
    }
)
_ANCHOR_KEYS = frozenset(
    {"schema", "site_id", "deployment_id", "record_count", "head_hash"}
)
_CONFLICT_CODES = frozenset(
    {
        "IDEMPOTENCY_CONFLICT",
        "STALE_REQUEST",
        "STALE_SUGGESTION",
        "INVALID_TRANSITION",
        "OVERLAPPING_EXCEPTION",
    }
)
_INTERNAL_EVENT_TYPES = frozenset(
    {
        "provider_attempt_started",
        "provider_attempt_finished",
        "suggestion_issued",
        "suggestion_unavailable",
        "generation_interrupted",
    }
)

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _close_fd_state(state: list[int]) -> None:
    """Close the owned lock then root descriptors exactly once."""

    for index in (0, 1):
        fd = state[index]
        state[index] = -1
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


class StaffingLedgerIntegrityError(RuntimeError):
    """Stable failure for untrusted or unsupported persistent state."""


def _integrity(component: str) -> StaffingLedgerIntegrityError:
    return StaffingLedgerIntegrityError(f"staffing ledger integrity: {component}")


def _require_identifier(value: object, field: str) -> str:
    if type(value) is not str or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise StaffingError("staffing_invalid_evidence", field)
    return value


def _require_digest(value: object, field: str) -> str:
    if type(value) is not str or HEX_DIGEST_PATTERN.fullmatch(value) is None:
        raise StaffingError("staffing_invalid_evidence", field)
    return value


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    event: StaffingEvent
    sequence: int
    previous_hash: str
    record_hash: str
    canonical_line: bytes


@dataclass(frozen=True, slots=True)
class EventCommit:
    event_id: str
    sequence: int
    record_hash: str

    def __post_init__(self) -> None:
        _require_identifier(self.event_id, "event_id")
        if type(self.sequence) is not int or self.sequence < 1:
            raise StaffingError("staffing_invalid_evidence", "sequence")
        _require_digest(self.record_hash, "record_hash")


@dataclass(frozen=True, slots=True)
class _PreparedAppend:
    record: LedgerRecord
    result: CommittedReceipt | EventCommit
    anchor_bytes: bytes


class _DuplicateJsonKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError


def _decode_json(data: bytes, *, component: str) -> object:
    if type(data) is not bytes:
        raise _integrity(component)
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except Exception:
        raise _integrity(component) from None


def parse_record(
    canonical_line: bytes, *, site_id: str, deployment_id: str
) -> LedgerRecord:
    """Parse one complete canonical line without trusting its event payload."""

    _require_identifier(site_id, "site_id")
    _require_identifier(deployment_id, "deployment_id")
    if (
        type(canonical_line) is not bytes
        or not canonical_line.endswith(b"\n")
        or canonical_line.endswith(b"\r\n")
    ):
        raise _integrity("ledger record framing")
    raw = _decode_json(canonical_line[:-1], component="ledger record JSON")
    if type(raw) is not dict:
        raise _integrity("ledger record shape")
    try:
        if canonical_json_bytes(raw) + b"\n" != canonical_line:
            raise _integrity("ledger record canonicality")
    except StaffingLedgerIntegrityError:
        raise
    except Exception:
        raise _integrity("ledger record canonicality") from None
    if set(raw) != set(_RECORD_KEYS):
        raise _integrity("ledger record fields")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise _integrity("ledger record schema")
    if type(raw["sequence"]) is not int or raw["sequence"] < 1:
        raise _integrity("ledger record sequence")
    if (
        type(raw["previous_hash"]) is not str
        or HEX_DIGEST_PATTERN.fullmatch(raw["previous_hash"]) is None
        or type(raw["record_hash"]) is not str
        or HEX_DIGEST_PATTERN.fullmatch(raw["record_hash"]) is None
    ):
        raise _integrity("ledger record hash")
    body = dict(raw)
    supplied_hash = body.pop("record_hash")
    try:
        expected_hash = stable_digest(body)
    except Exception:
        raise _integrity("ledger record hash") from None
    if supplied_hash != expected_hash:
        raise _integrity("ledger record hash")
    event_subset = {key: raw[key] for key in EVENT_RECORD_KEYS}
    try:
        event = parse_event(
            event_subset,
            site_id=site_id,
            deployment_id=deployment_id,
        )
    except StaffingError:
        raise _integrity("ledger event") from None
    except Exception:
        raise _integrity("ledger event") from None
    return LedgerRecord(
        event,
        raw["sequence"],
        raw["previous_hash"],
        supplied_hash,
        canonical_line,
    )


def exact_record_body(event: StaffingEvent, previous_hash: str) -> dict[str, object]:
    _require_digest(previous_hash, "previous_hash")
    primitive = to_primitive(event)
    if type(primitive) is not dict or set(primitive) != set(EVENT_RECORD_KEYS):
        raise StaffingError("staffing_invalid_event", "event record")
    return {
        "schema_version": _RECORD_SCHEMA_VERSION,
        **primitive,
        "previous_hash": previous_hash,
    }


def canonical_anchor_bytes(
    site_id: str, deployment_id: str, record_count: int, head_hash: str
) -> bytes:
    _require_identifier(site_id, "site_id")
    _require_identifier(deployment_id, "deployment_id")
    if type(record_count) is not int or record_count < 0:
        raise StaffingError("staffing_invalid_evidence", "record_count")
    _require_digest(head_hash, "head_hash")
    return canonical_json_bytes(
        {
            "schema": _ANCHOR_SCHEMA,
            "site_id": site_id,
            "deployment_id": deployment_id,
            "record_count": record_count,
            "head_hash": head_hash,
        }
    )


def receipt_from_record(
    record: LedgerRecord, *, duplicate: bool = False
) -> StaffingReceipt:
    payload = record.event.payload
    try:
        operation_kind = EVENT_TO_OPERATION[record.event.event_type]
        request_id = payload.request_id
        request_digest = payload.request_digest
    except (KeyError, AttributeError):
        raise StaffingError("staffing_invalid_evidence", "receipt record") from None
    return StaffingReceipt(
        operation_kind,
        request_id,
        request_digest,
        record.event.event_id,
        record.sequence,
        record.record_hash,
        duplicate,
    )


def validate_conflict_decision(decision: ConflictDecision) -> None:
    if type(decision) is not ConflictDecision:
        raise StaffingError("staffing_invalid_evidence", "conflict decision")
    if type(decision.operation_kind) is not str or decision.operation_kind not in OPERATION_KINDS:
        raise StaffingError("staffing_invalid_evidence", "operation_kind")
    _require_identifier(decision.request_id, "request_id")
    if type(decision.code) is not str or decision.code not in _CONFLICT_CODES:
        raise StaffingError("staffing_invalid_evidence", "conflict code")


class StaffingLedger:
    """Verified append-only staffing storage rooted at one pinned directory."""

    def __init__(
        self,
        root: str | Path,
        *,
        site_id: str,
        deployment_id: str,
    ) -> None:
        self.site_id = _require_identifier(site_id, "site_id")
        self.deployment_id = _require_identifier(deployment_id, "deployment_id")
        if _fcntl is None or os.name != "posix":
            raise _integrity("unsupported platform")
        try:
            root_path = Path(os.path.abspath(os.fspath(root)))
        except Exception:
            raise _integrity("root path") from None
        self._root_path = root_path
        self._thread_lock = _thread_lock(root_path)
        self._active_critical = 0
        self._root_fd = -1
        self._lock_fd = -1
        root_fd = self._open_pinned_root(root_path)
        self._root_fd = root_fd
        try:
            self._inspect_existing_children()
            self._lock_fd = self._open_regular(
                _LOCK_NAME,
                os.O_RDWR | os.O_CREAT,
                create_mode=0o600,
                component="lock file",
            )
            try:
                os.fchmod(self._lock_fd, 0o600)
            except OSError:
                raise _integrity("lock file") from None
            fd_state = [self._lock_fd, self._root_fd]
            try:
                self._finalizer = weakref.finalize(self, _close_fd_state, fd_state)
            except Exception:
                raise _integrity("descriptor ownership") from None
            self._fd_state = fd_state
        except BaseException:
            _close_fd_state([self._lock_fd, self._root_fd])
            self._lock_fd = -1
            self._root_fd = -1
            raise

    @staticmethod
    def _open_pinned_root(root: Path) -> int:
        if not root.is_absolute() or root == Path(root.anchor):
            raise _integrity("root path")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        current_fd = -1
        try:
            current_fd = os.open(root.anchor, flags)
            if not stat.S_ISDIR(os.fstat(current_fd).st_mode):
                raise _integrity("root component")
            for part in root.parts[1:]:
                child_fd = -1
                try:
                    child_fd = os.open(part, flags, dir_fd=current_fd)
                except FileNotFoundError:
                    try:
                        os.mkdir(part, 0o700, dir_fd=current_fd)
                        os.fsync(current_fd)
                    except FileExistsError:
                        pass
                    child_fd = os.open(part, flags, dir_fd=current_fd)
                opened = os.fstat(child_fd)
                entry = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
                if (
                    stat.S_ISLNK(entry.st_mode)
                    or not stat.S_ISDIR(opened.st_mode)
                    or not stat.S_ISDIR(entry.st_mode)
                    or (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino)
                ):
                    raise _integrity("root component")
                os.close(current_fd)
                current_fd = child_fd
                child_fd = -1
            os.fchmod(current_fd, 0o700)
            result = current_fd
            current_fd = -1
            return result
        except StaffingLedgerIntegrityError:
            raise
        except OSError:
            raise _integrity("root component") from None
        finally:
            if current_fd >= 0:
                os.close(current_fd)
            if "child_fd" in locals() and child_fd >= 0:
                os.close(child_fd)

    def close(self) -> None:
        with self._thread_lock:
            if self._active_critical:
                raise _integrity("active ledger")
            finalizer = getattr(self, "_finalizer", None)
            if finalizer is not None and finalizer.alive:
                finalizer()
            self._lock_fd = -1
            self._root_fd = -1

    def __enter__(self) -> StaffingLedger:
        with self._thread_lock:
            self._ensure_open()
            return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self.close()
        return False

    def _ensure_open(self) -> None:
        if self._root_fd < 0 or self._lock_fd < 0:
            raise _integrity("closed ledger")

    def _inspect_existing_children(self) -> None:
        for name, component in (
            (_LEDGER_NAME, "ledger file"),
            (_ANCHOR_NAME, "anchor file"),
            (_LOCK_NAME, "lock file"),
            (_ANCHOR_TEMP_NAME, "anchor temp file"),
        ):
            try:
                details = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError:
                raise _integrity(component) from None
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise _integrity(component)
            fd = self._open_regular(name, os.O_RDONLY, component=component)
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                raise _integrity(component) from None
            finally:
                os.close(fd)

    def _open_regular(
        self,
        name: str,
        flags: int,
        *,
        component: str,
        create_mode: int = 0o600,
    ) -> int:
        guarded_flags = flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, guarded_flags, create_mode, dir_fd=self._root_fd)
        except OSError:
            raise _integrity(component) from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise _integrity(component)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _existing_regular_fd(
        self, name: str, *, flags: int, component: str
    ) -> int | None:
        try:
            details = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError:
            raise _integrity(component) from None
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise _integrity(component)
        return self._open_regular(name, flags, component=component)

    @contextmanager
    def _critical_section(self, *, exclusive: bool) -> Iterator[None]:
        with self._thread_lock:
            self._ensure_open()
            operation = _fcntl.LOCK_EX if exclusive else _fcntl.LOCK_SH
            root_locked = False
            lock_locked = False
            try:
                _fcntl.flock(self._root_fd, operation)
                root_locked = True
                _fcntl.flock(self._lock_fd, operation)
                lock_locked = True
            except OSError:
                if root_locked:
                    try:
                        _fcntl.flock(self._root_fd, _fcntl.LOCK_UN)
                    except OSError:
                        pass
                raise _integrity("lock file") from None
            try:
                self._active_critical += 1
                self._validate_live_lock_entry()
                self._check_temp_type()
                yield
            finally:
                self._active_critical -= 1
                try:
                    if lock_locked:
                        _fcntl.flock(self._lock_fd, _fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    if root_locked:
                        _fcntl.flock(self._root_fd, _fcntl.LOCK_UN)
                except OSError:
                    pass

    def _validate_live_lock_entry(self) -> None:
        try:
            entry = os.stat(_LOCK_NAME, dir_fd=self._root_fd, follow_symlinks=False)
            opened = os.fstat(self._lock_fd)
        except OSError:
            raise _integrity("lock file") from None
        if (
            stat.S_ISLNK(entry.st_mode)
            or not stat.S_ISREG(entry.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise _integrity("lock file")

    def _check_temp_type(self) -> None:
        try:
            details = os.stat(
                _ANCHOR_TEMP_NAME,
                dir_fd=self._root_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        except OSError:
            raise _integrity("anchor temp file") from None
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise _integrity("anchor temp file")

    def read(self) -> StaffingHistory:
        with self._critical_section(exclusive=False):
            return self._read_verified_unlocked().history

    def verify(self) -> tuple[int, str]:
        with self._critical_section(exclusive=False):
            state = self._read_verified_unlocked()
            return state.record_count, state.head_hash

    def probe_request(
        self, operation_kind: str, request_id: str, request_digest: str
    ) -> DuplicateReceipt | ConflictReceipt | None:
        with self._critical_section(exclusive=False):
            if type(operation_kind) is not str or operation_kind not in OPERATION_KINDS:
                raise StaffingError("staffing_invalid_evidence", "operation_kind")
            _require_identifier(request_id, "request_id")
            _require_digest(request_digest, "request_digest")
            state = self._read_verified_unlocked()
            try:
                prior = state.request(operation_kind, request_id, request_digest)
            except StaffingError as error:
                if error.code != "IDEMPOTENCY_CONFLICT":
                    raise
                return ConflictReceipt(operation_kind, request_id, error.code)
            return (
                DuplicateReceipt(replace(prior, duplicate=True))
                if prior is not None
                else None
            )

    def append_via(
        self, builder: Callable[[VerifiedLedgerState], AppendDecision]
    ) -> ReceiptResult:
        with self._critical_section(exclusive=True):
            state = self._read_verified_unlocked()
            decision = builder(state)
            if type(decision) is ReturnReceiptDecision:
                if not any(decision.receipt is item for item in state.receipts):
                    raise StaffingError("staffing_invalid_evidence", "return receipt")
                return DuplicateReceipt(replace(decision.receipt, duplicate=True))
            if type(decision) is ConflictDecision:
                validate_conflict_decision(decision)
                return ConflictReceipt(
                    decision.operation_kind, decision.request_id, decision.code
                )
            if type(decision) is not AppendEventDecision:
                raise StaffingError("staffing_invalid_evidence", "append decision")
            if type(decision.event) is not StaffingEvent:
                raise StaffingError("staffing_invalid_event", "business append category")
            if decision.event.event_type not in EVENT_TO_OPERATION:
                raise StaffingError("staffing_invalid_event", "business append category")
            prepared = self._prepare_append(state, decision.event, business=True)
            self._persist_prepared(prepared)
            return prepared.result

    @overload
    def append_event_via(
        self,
        builder: Callable[[VerifiedLedgerState], AppendEventDecision],
    ) -> EventCommit: ...

    @overload
    def append_event_via(
        self,
        builder: Callable[
            [VerifiedLedgerState], AppendEventDecision | ConflictDecision
        ],
    ) -> EventCommit | ConflictReceipt: ...

    def append_event_via(
        self,
        builder: Callable[
            [VerifiedLedgerState], AppendEventDecision | ConflictDecision
        ],
    ) -> EventCommit | ConflictReceipt:
        with self._critical_section(exclusive=True):
            state = self._read_verified_unlocked()
            decision = builder(state)
            if type(decision) is ConflictDecision:
                validate_conflict_decision(decision)
                return ConflictReceipt(
                    decision.operation_kind, decision.request_id, decision.code
                )
            if type(decision) is not AppendEventDecision:
                raise StaffingError("staffing_invalid_evidence", "event append decision")
            if type(decision.event) is not StaffingEvent:
                raise StaffingError("staffing_invalid_event", "internal append category")
            if decision.event.event_type not in _INTERNAL_EVENT_TYPES:
                raise StaffingError("staffing_invalid_event", "internal append category")
            prepared = self._prepare_append(state, decision.event, business=False)
            self._persist_prepared(prepared)
            return prepared.result

    def _read_verified_unlocked(self) -> VerifiedLedgerState:
        lines = self._read_lines()
        records = tuple(
            parse_record(
                line,
                site_id=self.site_id,
                deployment_id=self.deployment_id,
            )
            for line in lines
        )
        self._verify_hash_chain(records)
        self._verify_anchor(records)
        events = tuple(item.event for item in records)
        try:
            history = replay_staffing(
                events,
                site_id=self.site_id,
                deployment_id=self.deployment_id,
            )
            receipts = tuple(
                receipt_from_record(item)
                for item in records
                if item.event.event_type in EVENT_TO_OPERATION
            )
        except StaffingError:
            raise _integrity("semantic replay") from None
        except Exception:
            raise _integrity("semantic replay") from None
        head_hash = records[-1].record_hash if records else GENESIS_HASH
        return VerifiedLedgerState(history, receipts, len(records), head_hash)

    def _read_lines(self) -> tuple[bytes, ...]:
        fd = self._existing_regular_fd(
            _LEDGER_NAME,
            flags=os.O_RDONLY,
            component="ledger file",
        )
        if fd is None:
            return ()
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "rb", closefd=True) as handle:
                fd = -1
                data = handle.read()
        except OSError:
            raise _integrity("ledger file") from None
        finally:
            if fd >= 0:
                os.close(fd)
        if not data:
            return ()
        if not data.endswith(b"\n"):
            raise _integrity("ledger record framing")
        return tuple(data.splitlines(keepends=True))

    @staticmethod
    def _verify_hash_chain(records: tuple[LedgerRecord, ...]) -> None:
        previous_hash = GENESIS_HASH
        event_ids: set[str] = set()
        for expected_sequence, record in enumerate(records, start=1):
            if record.sequence != expected_sequence:
                raise _integrity("ledger sequence")
            if record.previous_hash != previous_hash:
                raise _integrity("ledger hash chain")
            if record.event.event_id in event_ids:
                raise _integrity("ledger event identity")
            event_ids.add(record.event.event_id)
            previous_hash = record.record_hash

    def _read_anchor(self) -> dict[str, object] | None:
        fd = self._existing_regular_fd(
            _ANCHOR_NAME,
            flags=os.O_RDONLY,
            component="anchor file",
        )
        if fd is None:
            return None
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "rb", closefd=True) as handle:
                fd = -1
                data = handle.read()
        except OSError:
            raise _integrity("anchor file") from None
        finally:
            if fd >= 0:
                os.close(fd)
        raw = _decode_json(data, component="anchor JSON")
        if type(raw) is not dict or set(raw) != set(_ANCHOR_KEYS):
            raise _integrity("anchor fields")
        try:
            if canonical_json_bytes(raw) != data:
                raise _integrity("anchor canonicality")
        except StaffingLedgerIntegrityError:
            raise
        except Exception:
            raise _integrity("anchor canonicality") from None
        return raw

    def _verify_anchor(self, records: tuple[LedgerRecord, ...]) -> None:
        anchor = self._read_anchor()
        if anchor is None:
            if records:
                raise _integrity("missing anchor")
            return
        if (
            type(anchor["schema"]) is not str
            or anchor["schema"] != _ANCHOR_SCHEMA
            or type(anchor["site_id"]) is not str
            or anchor["site_id"] != self.site_id
            or type(anchor["deployment_id"]) is not str
            or anchor["deployment_id"] != self.deployment_id
        ):
            raise _integrity("anchor identity")
        count = anchor["record_count"]
        head_hash = anchor["head_hash"]
        if type(count) is not int or count < 0 or count > len(records):
            raise _integrity("anchor count")
        if type(head_hash) is not str or HEX_DIGEST_PATTERN.fullmatch(head_hash) is None:
            raise _integrity("anchor hash")
        expected_hash = GENESIS_HASH if count == 0 else records[count - 1].record_hash
        if head_hash != expected_hash:
            raise _integrity("anchor prefix")

    def _prepare_append(
        self,
        state: VerifiedLedgerState,
        supplied_event: StaffingEvent,
        *,
        business: bool,
    ) -> _PreparedAppend:
        transition(state.history, supplied_event)
        event_primitive = to_primitive(supplied_event)
        canonical_event = parse_event(
            event_primitive,
            site_id=self.site_id,
            deployment_id=self.deployment_id,
        )
        next_history = transition(state.history, canonical_event)
        if (
            next_history.record_count != state.record_count + 1
            or canonical_event.sequence != state.record_count + 1
        ):
            raise StaffingError("staffing_invalid_event", "append transition")
        body = exact_record_body(canonical_event, state.head_hash)
        record_hash = stable_digest(body)
        full_body = {**body, "record_hash": record_hash}
        canonical_line = canonical_json_bytes(full_body) + b"\n"
        record = LedgerRecord(
            canonical_event,
            canonical_event.sequence,
            state.head_hash,
            record_hash,
            canonical_line,
        )
        result: CommittedReceipt | EventCommit
        if business:
            result = CommittedReceipt(receipt_from_record(record))
        else:
            result = EventCommit(
                canonical_event.event_id,
                canonical_event.sequence,
                record_hash,
            )
        anchor_bytes = canonical_anchor_bytes(
            self.site_id,
            self.deployment_id,
            canonical_event.sequence,
            record_hash,
        )
        return _PreparedAppend(record, result, anchor_bytes)

    def _persist_prepared(self, prepared: _PreparedAppend) -> None:
        try:
            if prepared.record.sequence == 1 and self._read_anchor() is None:
                self._replace_anchor(
                    canonical_anchor_bytes(
                        self.site_id,
                        self.deployment_id,
                        0,
                        GENESIS_HASH,
                    )
                )
            self._append_and_fsync(prepared.record.canonical_line)
            os.fsync(self._root_fd)
            self._replace_anchor(prepared.anchor_bytes)
        except StaffingLedgerIntegrityError:
            raise
        except OSError:
            raise _integrity("persistence I/O") from None

    def _append_and_fsync(self, canonical_line: bytes) -> None:
        fd = self._open_regular(
            _LEDGER_NAME,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            create_mode=0o600,
            component="ledger file",
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "ab", closefd=True) as handle:
                fd = -1
                written = handle.write(canonical_line)
                if written != len(canonical_line):
                    raise OSError
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if fd >= 0:
                os.close(fd)

    def _replace_anchor(self, anchor_bytes: bytes) -> None:
        self._check_temp_type()
        existing_anchor = self._existing_regular_fd(
            _ANCHOR_NAME,
            flags=os.O_RDONLY,
            component="anchor file",
        )
        if existing_anchor is not None:
            os.close(existing_anchor)
        fd = self._open_regular(
            _ANCHOR_TEMP_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            create_mode=0o600,
            component="anchor temp file",
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                fd = -1
                written = handle.write(anchor_bytes)
                if written != len(anchor_bytes):
                    raise OSError
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(
                _ANCHOR_TEMP_NAME,
                _ANCHOR_NAME,
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._root_fd,
            )
            os.fsync(self._root_fd)
        finally:
            if fd >= 0:
                os.close(fd)


__all__ = [
    "GENESIS_HASH",
    "EventCommit",
    "LedgerRecord",
    "StaffingLedger",
    "StaffingLedgerIntegrityError",
    "canonical_anchor_bytes",
    "exact_record_body",
    "parse_record",
    "receipt_from_record",
    "validate_conflict_decision",
]
