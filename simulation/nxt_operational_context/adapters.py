"""CSV source adapters: synthetic exports to normalized operational events.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

Three adapters with fixed, documented column sets (staffing, sales, play).
They are not vendor adapters: the slice ships no vendor-specific mapping
because no real sample export exists yet.  Every adapter applies the privacy
rule first, then validates every row, and either yields the complete set of
events or a whole-batch rejection with safe row numbers and reason codes.
Nothing is partially admitted.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping

from .contracts import (
    BatchContext,
    Correction,
    CorrectionKind,
    EventType,
    OperationalContextError,
    OperationalEvent,
    SourceSystem,
    load_timezone,
    parse_source_timestamp,
    utc_text,
)
from .privacy import RowError, check_headers, is_code, is_count, is_identifier

STAFFING_ADAPTER_ID = "csv-staffing"
SALES_ADAPTER_ID = "csv-sales"
PLAY_ADAPTER_ID = "csv-play"
ADAPTER_VERSION = "0.1.0"

_MAX_ROWS = 100_000


@dataclass(frozen=True, slots=True)
class BatchRejection:
    """The whole batch is refused; nothing from it may be published."""

    batch: BatchContext
    errors: tuple[RowError, ...]
    rows_seen: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "import_batch_id": self.batch.import_batch_id,
            "rows_seen": self.rows_seen,
            "error_count": len(self.errors),
            "errors": [error.to_dict() for error in self.errors],
        }


@dataclass(frozen=True, slots=True)
class AdapterResult:
    """Every event of one accepted file plus adapter diagnostics (counts only)."""

    batch: BatchContext
    events: tuple[OperationalEvent, ...]
    rows_seen: int
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


RowBuilder = Callable[[int, Mapping[str, str], BatchContext, "_Context"], list[OperationalEvent]]


class _Context:
    def __init__(self, batch: BatchContext) -> None:
        self.batch = batch
        self.zone = load_timezone(batch.profile.source_timezone)
        self.errors: list[RowError] = []
        self.unmapped_skus = 0

    def fail(self, row: int, column: str | None, reason: str) -> None:
        self.errors.append(RowError(row, column, reason))

    def timestamp(self, row: int, values: Mapping[str, str], column: str, *, required: bool) -> datetime | None:
        raw = values.get(column, "")
        if raw is None or raw.strip() == "":
            if required:
                self.fail(row, column, "missing_value")
            return None
        try:
            return parse_source_timestamp(raw, self.zone)
        except OperationalContextError:
            self.fail(row, column, "invalid_timestamp")
            return None

    def identifier(self, row: int, values: Mapping[str, str], column: str, *, required: bool) -> str | None:
        raw = values.get(column, "")
        if raw is None or raw.strip() == "":
            if required:
                self.fail(row, column, "missing_value")
            return None
        if not is_identifier(raw):
            self.fail(row, column, "invalid_identifier")
            return None
        return raw

    def code(self, row: int, values: Mapping[str, str], column: str, allowed: frozenset[str], *, required: bool) -> str | None:
        raw = values.get(column, "")
        if raw is None or raw.strip() == "":
            if required:
                self.fail(row, column, "missing_value")
            return None
        if not is_code(raw):
            self.fail(row, column, "invalid_code")
            return None
        if raw not in allowed:
            self.fail(row, column, "unknown_code")
            return None
        return raw

    def count(self, row: int, values: Mapping[str, str], column: str, *, required: bool) -> int | None:
        raw = values.get(column, "")
        if raw is None or raw.strip() == "":
            if required:
                self.fail(row, column, "missing_value")
            return None
        if not is_count(raw):
            self.fail(row, column, "invalid_count")
            return None
        return int(raw)


def _run(text: str, batch: BatchContext, *, expected_source: SourceSystem, required: frozenset[str], optional: frozenset[str], build: RowBuilder) -> AdapterResult | BatchRejection:
    if batch.profile.source_system is not expected_source:
        raise OperationalContextError("profile_source_mismatch", f"profile is for {batch.profile.source_system}, adapter is {expected_source}")
    context = _Context(batch)
    if not isinstance(text, str):
        return BatchRejection(batch, (RowError(None, None, "not_text"),), 0)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        headers = next(reader)
    except StopIteration:
        return BatchRejection(batch, (RowError(None, None, "empty_file"),), 0)
    except csv.Error:
        return BatchRejection(batch, (RowError(None, None, "malformed_csv"),), 0)
    headers = [header.strip() for header in headers]
    header_errors = check_headers(headers, required, optional)
    if header_errors:
        return BatchRejection(batch, tuple(header_errors), 0)
    events: list[OperationalEvent] = []
    seen_ids: dict[str, str] = {}
    rows = 0
    try:
        for index, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue
            rows += 1
            if rows > _MAX_ROWS:
                context.fail(index, None, "too_many_rows")
                break
            if len(row) != len(headers):
                context.fail(index, None, "column_count_mismatch")
                continue
            values = {header: cell.strip() for header, cell in zip(headers, row)}
            for event in build(index, values, batch, context):
                digest = event.content_digest
                previous = seen_ids.get(event.event_id)
                if previous is not None and previous != digest:
                    context.fail(index, None, "conflicting_duplicate_row")
                    continue
                if previous is not None:
                    continue  # an exact duplicate row within one file is idempotent
                seen_ids[event.event_id] = digest
                events.append(event)
    except csv.Error:
        context.fail(None, None, "malformed_csv")
    if context.errors:
        return BatchRejection(batch, tuple(context.errors), rows)
    return AdapterResult(batch, tuple(events), rows, {"rows": rows, "events": len(events), "unmapped_skus": context.unmapped_skus})


def _provenance(batch: BatchContext, row: int) -> dict[str, Any]:
    return {
        "adapter_id": batch.profile.adapter_id,
        "adapter_version": batch.profile.adapter_version,
        "source_file_digest": batch.source_file_digest,
        "row_number": row,
        "profile_digest": batch.profile.profile_digest,
    }


def _revision(correction_time: datetime | None) -> str:
    return "" if correction_time is None else utc_text(correction_time)


def _event(batch: BatchContext, row: int, *, record_id: str, event_type: EventType, occurred_at: datetime, status: str, payload: Mapping[str, Any], correction_time: datetime | None, correction: Correction | None = None) -> OperationalEvent:
    if correction_time is not None and correction is None:
        correction = Correction(CorrectionKind.REPLACEMENT, target_event_id=None, source_correction_time=correction_time)
    return OperationalEvent(
        site_id=batch.admission.site_id,
        source_system=batch.profile.source_system,
        source_record_id=record_id,
        source_revision_key=_revision(correction_time),
        event_type=event_type,
        occurred_at=occurred_at,
        received_at=batch.received_at,
        import_batch_id=batch.import_batch_id,
        source_timezone=batch.profile.source_timezone,
        source_status=status,
        payload=dict(payload),
        provenance=_provenance(batch, row),
        correction=correction,
    )


# ---------------------------------------------------------------------------
# Staffing: planned shifts, attendance, shift changes
# ---------------------------------------------------------------------------

STAFFING_REQUIRED = frozenset({"record_id", "record_type", "staff_ref", "occurred_at"})
STAFFING_OPTIONAL = frozenset({"role_code", "shift_start", "shift_end", "shift_record_id", "request_record_id", "correction_time"})
_STAFFING_TYPES = frozenset(
    {"SHIFT_SCHEDULED", "SHIFT_CANCELLED", "CLOCK_IN", "CLOCK_OUT", "SHIFT_CHANGE_REQUESTED", "SHIFT_CHANGE_APPROVED", "SHIFT_CHANGE_REJECTED", "ABSENCE_RECORDED"}
)


def _build_staffing(row: int, values: Mapping[str, str], batch: BatchContext, ctx: _Context) -> list[OperationalEvent]:
    record_id = ctx.identifier(row, values, "record_id", required=True)
    record_type = ctx.code(row, values, "record_type", _STAFFING_TYPES, required=True)
    staff_ref = ctx.identifier(row, values, "staff_ref", required=True)
    occurred_at = ctx.timestamp(row, values, "occurred_at", required=True)
    correction_time = ctx.timestamp(row, values, "correction_time", required=False)
    if record_type is None or record_id is None or staff_ref is None or occurred_at is None:
        return []
    event_type = EventType(record_type)
    needs_interval = event_type in (EventType.SHIFT_SCHEDULED, EventType.SHIFT_CHANGE_APPROVED)
    shift_start = ctx.timestamp(row, values, "shift_start", required=needs_interval)
    shift_end = ctx.timestamp(row, values, "shift_end", required=needs_interval)
    if shift_start is not None and shift_end is not None and shift_end <= shift_start:
        ctx.fail(row, "shift_end", "interval_inverted")
        return []
    role_code = None
    if values.get("role_code", "").strip():
        role_code = ctx.code(row, values, "role_code", frozenset({values["role_code"]}) if is_code(values["role_code"]) else frozenset(), required=False)
    if event_type is EventType.SHIFT_SCHEDULED and role_code is None:
        ctx.fail(row, "role_code", "missing_value")
    needs_shift = event_type in (EventType.SHIFT_CANCELLED, EventType.ABSENCE_RECORDED, EventType.SHIFT_CHANGE_REQUESTED, EventType.SHIFT_CHANGE_APPROVED)
    shift_record_id = ctx.identifier(row, values, "shift_record_id", required=needs_shift)
    needs_request = event_type in (EventType.SHIFT_CHANGE_REQUESTED, EventType.SHIFT_CHANGE_APPROVED, EventType.SHIFT_CHANGE_REJECTED)
    request_record_id = ctx.identifier(row, values, "request_record_id", required=needs_request)
    if ctx.errors and ctx.errors[-1].row_number == row:
        return []
    payload = {
        "staff_ref": staff_ref,
        "role_code": role_code,
        "shift_start": None if shift_start is None else utc_text(shift_start),
        "shift_end": None if shift_end is None else utc_text(shift_end),
        "shift_record_id": shift_record_id if event_type is not EventType.SHIFT_SCHEDULED else record_id,
        "request_record_id": request_record_id,
        # Only an approval changes an effective schedule; a request never does.
        "schedule_effect": "replaces_interval" if event_type is EventType.SHIFT_CHANGE_APPROVED else "none",
    }
    return [_event(batch, row, record_id=record_id, event_type=event_type, occurred_at=occurred_at, status=record_type, payload=payload, correction_time=correction_time)]


def parse_staffing_csv(text: str, batch: BatchContext) -> AdapterResult | BatchRejection:
    return _run(text, batch, expected_source=SourceSystem.STAFFING, required=STAFFING_REQUIRED, optional=STAFFING_OPTIONAL, build=_build_staffing)


# ---------------------------------------------------------------------------
# Sales: captured sales and reversals, ball units via the declared mapping
# ---------------------------------------------------------------------------

SALES_REQUIRED = frozenset({"transaction_id", "transaction_time", "sku", "quantity", "status"})
SALES_OPTIONAL = frozenset({"correction_time", "original_transaction_id", "refunded_quantity"})
_SALE_STATUSES = frozenset({"CAPTURED", "VOIDED", "REFUNDED"})


def _build_sales(row: int, values: Mapping[str, str], batch: BatchContext, ctx: _Context) -> list[OperationalEvent]:
    transaction_id = ctx.identifier(row, values, "transaction_id", required=True)
    transaction_time = ctx.timestamp(row, values, "transaction_time", required=True)
    sku = ctx.identifier(row, values, "sku", required=True)
    quantity = ctx.count(row, values, "quantity", required=True)
    status = ctx.code(row, values, "status", _SALE_STATUSES, required=True)
    correction_time = ctx.timestamp(row, values, "correction_time", required=False)
    if None in (transaction_id, transaction_time, sku, quantity, status):
        return []
    if quantity == 0:
        ctx.fail(row, "quantity", "invalid_count")
        return []
    units = batch.profile.sku_ball_units.get(sku)
    if units is None:
        ctx.unmapped_skus += 1
    mapped = {"mapped_ball_units_per_unit": units, "mapped_ball_units": None if units is None else units * quantity}
    if status == "CAPTURED":
        if values.get("original_transaction_id", "").strip() or values.get("refunded_quantity", "").strip():
            ctx.fail(row, "original_transaction_id", "unexpected_value")
            return []
        payload = {"sku": sku, "quantity": quantity, "transaction_time": utc_text(transaction_time), **mapped}
        return [_event(batch, row, record_id=transaction_id, event_type=EventType.SALE_CAPTURED, occurred_at=transaction_time, status=status, payload=payload, correction_time=correction_time)]
    original = ctx.identifier(row, values, "original_transaction_id", required=True)
    refunded = ctx.count(row, values, "refunded_quantity", required=(status == "REFUNDED"))
    if original is None or (status == "REFUNDED" and refunded is None):
        return []
    reversed_quantity = quantity if refunded is None else refunded
    if reversed_quantity == 0 or reversed_quantity > quantity:
        ctx.fail(row, "refunded_quantity", "invalid_count")
        return []
    payload = {
        "sku": sku,
        "quantity": quantity,
        "reversed_quantity": reversed_quantity,
        "transaction_time": utc_text(transaction_time),
        "original_transaction_id": original,
        "mapped_ball_units_per_unit": units,
        "mapped_reversed_ball_units": None if units is None else units * reversed_quantity,
    }
    correction = Correction(CorrectionKind.REVERSAL, target_event_id=None, source_correction_time=correction_time, original_record_id=original)
    return [_event(batch, row, record_id=transaction_id, event_type=EventType.SALE_REVERSED, occurred_at=transaction_time, status=status, payload=payload, correction_time=correction_time, correction=correction)]


def parse_sales_csv(text: str, batch: BatchContext) -> AdapterResult | BatchRejection:
    return _run(text, batch, expected_source=SourceSystem.SALES, required=SALES_REQUIRED, optional=SALES_OPTIONAL, build=_build_sales)


# ---------------------------------------------------------------------------
# Play: bookings, starts, finishes, cancellations, player counts
# ---------------------------------------------------------------------------

PLAY_REQUIRED = frozenset({"record_id", "record_type", "session_ref", "occurred_at"})
PLAY_OPTIONAL = frozenset({"scheduled_start", "actual_start", "actual_finish", "player_count", "actual_player_count", "activity_type", "correction_time"})
_PLAY_TYPES = frozenset({"SESSION_BOOKED", "SESSION_STARTED", "SESSION_FINISHED", "SESSION_CANCELLED", "PLAYER_COUNT_UPDATED"})


def _build_play(row: int, values: Mapping[str, str], batch: BatchContext, ctx: _Context) -> list[OperationalEvent]:
    record_id = ctx.identifier(row, values, "record_id", required=True)
    record_type = ctx.code(row, values, "record_type", _PLAY_TYPES, required=True)
    session_ref = ctx.identifier(row, values, "session_ref", required=True)
    occurred_at = ctx.timestamp(row, values, "occurred_at", required=True)
    correction_time = ctx.timestamp(row, values, "correction_time", required=False)
    if None in (record_id, record_type, session_ref, occurred_at):
        return []
    event_type = EventType(record_type)
    scheduled_start = ctx.timestamp(row, values, "scheduled_start", required=(event_type is EventType.SESSION_BOOKED))
    actual_start = ctx.timestamp(row, values, "actual_start", required=(event_type is EventType.SESSION_STARTED))
    actual_finish = ctx.timestamp(row, values, "actual_finish", required=(event_type is EventType.SESSION_FINISHED))
    player_count = ctx.count(row, values, "player_count", required=(event_type is EventType.SESSION_BOOKED))
    actual_player_count = ctx.count(row, values, "actual_player_count", required=(event_type is EventType.PLAYER_COUNT_UPDATED))
    activity_type = None
    if values.get("activity_type", "").strip():
        activity_type = ctx.code(row, values, "activity_type", frozenset({values["activity_type"]}) if is_code(values["activity_type"]) else frozenset(), required=False)
    if ctx.errors and ctx.errors[-1].row_number == row:
        return []
    payload = {
        "session_ref": session_ref,
        "scheduled_start": None if scheduled_start is None else utc_text(scheduled_start),
        "actual_start": None if actual_start is None else utc_text(actual_start),
        "actual_finish": None if actual_finish is None else utc_text(actual_finish),
        "player_count": player_count,
        "actual_player_count": actual_player_count,
        "activity_type": activity_type,
    }
    return [_event(batch, row, record_id=record_id, event_type=event_type, occurred_at=occurred_at, status=record_type, payload=payload, correction_time=correction_time)]


def parse_play_csv(text: str, batch: BatchContext) -> AdapterResult | BatchRejection:
    return _run(text, batch, expected_source=SourceSystem.PLAY, required=PLAY_REQUIRED, optional=PLAY_OPTIONAL, build=_build_play)


ADAPTERS: Mapping[SourceSystem, Callable[[str, BatchContext], AdapterResult | BatchRejection]] = {
    SourceSystem.STAFFING: parse_staffing_csv,
    SourceSystem.SALES: parse_sales_csv,
    SourceSystem.PLAY: parse_play_csv,
}


def parse_csv(text: str, batch: BatchContext) -> AdapterResult | BatchRejection:
    """Dispatch on the profile's source system."""

    return ADAPTERS[batch.profile.source_system](text, batch)


__all__ = [
    "ADAPTERS",
    "ADAPTER_VERSION",
    "PLAY_ADAPTER_ID",
    "PLAY_OPTIONAL",
    "PLAY_REQUIRED",
    "SALES_ADAPTER_ID",
    "SALES_OPTIONAL",
    "SALES_REQUIRED",
    "STAFFING_ADAPTER_ID",
    "STAFFING_OPTIONAL",
    "STAFFING_REQUIRED",
    "AdapterResult",
    "BatchRejection",
    "parse_csv",
    "parse_play_csv",
    "parse_sales_csv",
    "parse_staffing_csv",
]
