"""Idempotent import decisions and the journal record vocabulary.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The leaf never writes a file.  A composition root reads the verified
journal records, calls :func:`decide_import` inside the journal's exclusive
lock, and appends exactly the plain-data record specs it returns.  Every
record kind is closed, every spec payload is JSON-primitive, and the whole
decision is a pure function of (existing records, batch).

Idempotency: a batch whose ``import_batch_id`` is already committed appends
one ``import_batch_duplicate`` record and no event; an event whose
``event_id`` is already recorded with the same content is skipped; the same
``event_id`` with different content is a conflict and rejects the whole
batch.  Corrections never delete: a revision supersedes the current head of
its chain and a reversal points at the captured sale it reverses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .adapters import AdapterResult, BatchRejection
from .contracts import (
    JOURNAL_SCHEMA,
    BatchContext,
    Correction,
    CorrectionKind,
    EventType,
    OperationalContextError,
    OperationalEvent,
    utc_text,
)
from .privacy import RowError, safe_errors

RECORD_KIND_BATCH_COMMITTED = "import_batch_committed"
RECORD_KIND_BATCH_REJECTED = "import_batch_rejected"
RECORD_KIND_BATCH_DUPLICATE = "import_batch_duplicate"
RECORD_KIND_EVENT = "event_recorded"
RECORD_KIND_PROFILE = "source_profile_declared"

CONTEXT_RECORD_KINDS = frozenset(
    {RECORD_KIND_BATCH_COMMITTED, RECORD_KIND_BATCH_REJECTED, RECORD_KIND_BATCH_DUPLICATE, RECORD_KIND_EVENT, RECORD_KIND_PROFILE}
)
ORIGIN_ADAPTER = "ADAPTER"
ORIGIN_SERVICE = "SERVICE"
CONTEXT_ORIGINS = frozenset({ORIGIN_ADAPTER, ORIGIN_SERVICE})


@dataclass(frozen=True, slots=True)
class RecordSpecData:
    """Plain-data append request the composition root turns into its journal's spec."""

    record_kind: str
    origin: str
    recorded_at_utc: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ImportDecision:
    outcome: str  # committed | duplicate_batch | rejected
    import_batch_id: str
    specs: tuple[RecordSpecData, ...]
    events_recorded: int
    events_duplicate: int
    errors: tuple[RowError, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "import_batch_id": self.import_batch_id,
            "events_recorded": self.events_recorded,
            "events_duplicate": self.events_duplicate,
            "errors": safe_errors(self.errors),
        }


def _kind_of(record: Any) -> str:
    return record["record_kind"] if isinstance(record, Mapping) else record.record_kind


def _payload(record: Any) -> Mapping[str, Any]:
    return record["payload"] if isinstance(record, Mapping) else record.payload


def committed_batch_ids(records: Sequence[Any]) -> frozenset[str]:
    return frozenset(
        str(_payload(record)["import_batch_id"]) for record in records if _kind_of(record) == RECORD_KIND_BATCH_COMMITTED
    )


def recorded_events(records: Sequence[Any]) -> tuple[OperationalEvent, ...]:
    """Every recorded event, in journal order, re-validated from its stored form."""

    events: list[OperationalEvent] = []
    for record in records:
        if _kind_of(record) != RECORD_KIND_EVENT:
            continue
        events.append(OperationalEvent.from_dict(_thaw(_payload(record)["event"])))
    return tuple(events)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def recorded_batches(records: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    """Every batch outcome record (committed, rejected, duplicate) as plain dicts."""

    batches: list[dict[str, Any]] = []
    for record in records:
        kind = _kind_of(record)
        if kind in (RECORD_KIND_BATCH_COMMITTED, RECORD_KIND_BATCH_REJECTED, RECORD_KIND_BATCH_DUPLICATE):
            batches.append({"record_kind": kind, **_thaw(_payload(record))})
    return tuple(batches)


def _chain_key(event: OperationalEvent) -> tuple[str, str, str]:
    return (str(event.source_system), event.source_record_id, str(event.event_type))


def _head_by_chain(events: Sequence[OperationalEvent]) -> dict[tuple[str, str, str], OperationalEvent]:
    heads: dict[tuple[str, str, str], OperationalEvent] = {}
    for event in events:
        key = _chain_key(event)
        current = heads.get(key)
        if current is None or _revision_order(event) > _revision_order(current):
            heads[key] = event
    return heads


def _revision_order(event: OperationalEvent) -> tuple[str, str]:
    return (event.source_revision_key, event.event_id)


def resolve_corrections(existing: Sequence[OperationalEvent], incoming: Sequence[OperationalEvent]) -> tuple[OperationalEvent, ...]:
    """Fill ``correction.target_event_id`` for revisions and reversals.

    A revision supersedes the current head of its chain (existing events and
    earlier incoming ones); the original is never altered.  A reversal points
    at the captured sale whose record id it names when one is known;
    otherwise the target stays ``None`` and projections count it as an
    unmatched reversal rather than subtracting it.
    """

    heads = _head_by_chain(existing)
    captured: dict[str, OperationalEvent] = {
        head.source_record_id: head for head in heads.values() if head.event_type is EventType.SALE_CAPTURED
    }
    resolved: list[OperationalEvent] = []
    for event in incoming:
        correction = event.correction
        key = _chain_key(event)
        head = heads.get(key)
        if event.source_revision_key:
            # A revision: it supersedes the head when that head is an earlier
            # revision; a stale revision (older than the head) still records
            # its own place in history without superseding anything newer.
            target = head.event_id if head is not None and _revision_order(head) < _revision_order(event) else None
            kind = correction.kind if correction is not None else CorrectionKind.REPLACEMENT
            correction = Correction(
                kind=kind,
                target_event_id=target if kind is CorrectionKind.REPLACEMENT else (correction.target_event_id if correction else None),
                source_correction_time=correction.source_correction_time if correction is not None else None,
                original_record_id=correction.original_record_id if correction is not None else None,
            )
        if event.event_type is EventType.SALE_REVERSED and correction is not None:
            original = captured.get(correction.original_record_id or "")
            correction = Correction(
                kind=CorrectionKind.REVERSAL,
                target_event_id=None if original is None else original.event_id,
                source_correction_time=correction.source_correction_time,
                original_record_id=correction.original_record_id,
            )
        updated = OperationalEvent(
            site_id=event.site_id,
            source_system=event.source_system,
            source_record_id=event.source_record_id,
            source_revision_key=event.source_revision_key,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            received_at=event.received_at,
            import_batch_id=event.import_batch_id,
            source_timezone=event.source_timezone,
            source_status=event.source_status,
            payload=event.payload,
            provenance=event.provenance,
            correction=correction,
        )
        resolved.append(updated)
        if head is None or _revision_order(updated) > _revision_order(head):
            heads[key] = updated
        if updated.event_type is EventType.SALE_CAPTURED:
            captured[updated.source_record_id] = heads[key]
    return tuple(resolved)


def decide_import(records: Sequence[Any], result: AdapterResult | BatchRejection, recorded_at: datetime) -> ImportDecision:
    """Pure decision for one adapter outcome against the verified journal."""

    recorded_at_text = utc_text(recorded_at)
    batch: BatchContext = result.batch
    batch_payload = batch.to_dict()
    if isinstance(result, BatchRejection):
        spec = RecordSpecData(
            RECORD_KIND_BATCH_REJECTED,
            ORIGIN_ADAPTER,
            recorded_at_text,
            {**batch_payload, "rows_seen": result.rows_seen, "error_count": len(result.errors), "errors": safe_errors(result.errors)},
        )
        return ImportDecision("rejected", batch.import_batch_id, (spec,), 0, 0, result.errors)
    if batch.import_batch_id in committed_batch_ids(records):
        spec = RecordSpecData(RECORD_KIND_BATCH_DUPLICATE, ORIGIN_ADAPTER, recorded_at_text, {**batch_payload, "events_in_file": len(result.events)})
        return ImportDecision("duplicate_batch", batch.import_batch_id, (spec,), 0, len(result.events), ())
    existing = recorded_events(records)
    known: dict[str, str] = {event.event_id: event.content_digest for event in existing}
    fresh: list[OperationalEvent] = []
    duplicates = 0
    conflicts: list[RowError] = []
    for event in result.events:
        digest = known.get(event.event_id)
        if digest is None:
            fresh.append(event)
            continue
        if digest == event.content_digest:
            duplicates += 1
            continue
        conflicts.append(RowError(int(event.provenance.get("row_number", 0)) or None, None, "event_conflict"))
    if conflicts:
        spec = RecordSpecData(
            RECORD_KIND_BATCH_REJECTED,
            ORIGIN_ADAPTER,
            recorded_at_text,
            {**batch_payload, "rows_seen": result.rows_seen, "error_count": len(conflicts), "errors": safe_errors(conflicts)},
        )
        return ImportDecision("rejected", batch.import_batch_id, (spec,), 0, duplicates, tuple(conflicts))
    resolved = resolve_corrections(existing, fresh)
    specs = [RecordSpecData(RECORD_KIND_EVENT, ORIGIN_ADAPTER, recorded_at_text, {"import_batch_id": batch.import_batch_id, "event": event.to_dict()}) for event in resolved]
    coverage_end = batch.exported_at
    if coverage_end is None:
        times = [event.occurred_at for event in result.events]
        coverage_end = max(times) if times else None
    specs.append(
        RecordSpecData(
            RECORD_KIND_BATCH_COMMITTED,
            ORIGIN_ADAPTER,
            recorded_at_text,
            {
                **batch_payload,
                "rows_seen": result.rows_seen,
                "events_in_file": len(result.events),
                "events_recorded": len(resolved),
                "events_duplicate": duplicates,
                "coverage_end": None if coverage_end is None else utc_text(coverage_end),
                "coverage_basis": "declared_export_time" if batch.exported_at is not None else "newest_recorded_event",
                "diagnostics": dict(result.diagnostics),
            },
        )
    )
    return ImportDecision("committed", batch.import_batch_id, tuple(specs), len(resolved), duplicates, ())


def profile_spec(profile_payload: Mapping[str, Any], recorded_at: datetime) -> RecordSpecData:
    """Record the declared profile so a journal replays with its own mapping history."""

    return RecordSpecData(RECORD_KIND_PROFILE, ORIGIN_SERVICE, utc_text(recorded_at), dict(profile_payload))


def check_journal_schema(schema: str) -> None:
    if schema != JOURNAL_SCHEMA:
        raise OperationalContextError("schema_mismatch", f"journal schema must be {JOURNAL_SCHEMA}")


__all__ = [
    "CONTEXT_ORIGINS",
    "CONTEXT_RECORD_KINDS",
    "ORIGIN_ADAPTER",
    "ORIGIN_SERVICE",
    "RECORD_KIND_BATCH_COMMITTED",
    "RECORD_KIND_BATCH_DUPLICATE",
    "RECORD_KIND_BATCH_REJECTED",
    "RECORD_KIND_EVENT",
    "RECORD_KIND_PROFILE",
    "ImportDecision",
    "RecordSpecData",
    "check_journal_schema",
    "committed_batch_ids",
    "decide_import",
    "profile_spec",
    "recorded_batches",
    "recorded_events",
    "resolve_corrections",
]
