"""Strict staffing exception normalization and immutable replay projection."""

from __future__ import annotations

from datetime import date, datetime
from typing import Sequence

from .contracts import (
    ExceptionCancelledPayload,
    ExceptionCorrectedPayload,
    ExceptionKind,
    ExceptionRecordedPayload,
    RosterRevision,
    StaffingError,
    StaffingEvent,
    StaffingException,
    Assignment,
    parse_local_date,
    require_exact_object,
    stable_digest,
    validate_human_text,
    validate_identifier,
    validate_local_minute,
)
from .roster import materialize_service_day
from .time import resolve_local_minute


EXCEPTION_KEYS = frozenset(
    {
        "schema",
        "request_id",
        "expected_roster_revision",
        "expected_exception_set_revision",
        "service_date",
        "staff_id",
        "kind",
        "time_local",
        "operator",
        "note",
    }
)
CANCEL_KEYS = frozenset(
    {
        "schema",
        "request_id",
        "exception_id",
        "expected_exception_set_revision",
        "operator",
        "note",
    }
)
CORRECT_KEYS = frozenset(
    {
        "schema",
        "request_id",
        "exception_id",
        "expected_exception_set_revision",
        "operator",
        "replacement",
    }
)
REPLACEMENT_KEYS = frozenset({"kind", "time_local", "note"})

_WHOLE_SHIFT_KINDS = frozenset({ExceptionKind.LEAVE.value, ExceptionKind.UNAVAILABLE.value})
_POINT_KINDS = frozenset(
    {ExceptionKind.LATE.value, ExceptionKind.EARLY_DEPARTURE.value}
)
_KINDS = _WHOLE_SHIFT_KINDS | _POINT_KINDS


def _closed_sequence(value: object, field: str, *, code: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise StaffingError(code, field)
    try:
        return tuple(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise StaffingError(code, field) from None


def _validate_nonnegative(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise StaffingError("staffing_invalid_roster", field)
    return value


def _validate_note(value: object) -> str | None:
    if value is None:
        return None
    return validate_human_text(value, "note", minimum=0, maximum=500)


def _validate_operator(value: object) -> str:
    return validate_human_text(value, "operator", maximum=128, formula_safe=True)


def _parse_kind(value: object) -> str:
    if type(value) is not str or value not in _KINDS:
        raise StaffingError("staffing_invalid_roster", "kind")
    return value


def _validate_replacement(value: object) -> dict[str, object]:
    replacement = require_exact_object(value, REPLACEMENT_KEYS)
    kind = _parse_kind(replacement["kind"])
    point = replacement["time_local"]
    if kind in _WHOLE_SHIFT_KINDS:
        if point is not None:
            raise StaffingError("invalid_exception_time", "whole-shift kind")
    else:
        try:
            validate_local_minute(point, "time_local")
        except StaffingError:
            raise StaffingError("invalid_exception_time", "time_local") from None
    replacement["kind"] = kind
    replacement["note"] = _validate_note(replacement["note"])
    return replacement


def parse_exception_cancel_request(payload: object) -> dict[str, object]:
    """Parse one cancellation body without comparing mutable ledger state."""

    body = require_exact_object(payload, CANCEL_KEYS)
    if body["schema"] != "nxt-staffing-exception-cancel/v1":
        raise StaffingError("staffing_invalid_roster", "schema")
    body["request_id"] = validate_identifier(body["request_id"], "request_id")
    body["exception_id"] = validate_identifier(body["exception_id"], "exception_id")
    body["expected_exception_set_revision"] = _validate_nonnegative(
        body["expected_exception_set_revision"], "expected_exception_set_revision"
    )
    body["operator"] = _validate_operator(body["operator"])
    body["note"] = _validate_note(body["note"])
    return body


def parse_exception_correction_request(payload: object) -> dict[str, object]:
    """Parse and detach one correction body without doing its CAS check."""

    body = require_exact_object(payload, CORRECT_KEYS)
    if body["schema"] != "nxt-staffing-exception-correct/v1":
        raise StaffingError("staffing_invalid_roster", "schema")
    body["request_id"] = validate_identifier(body["request_id"], "request_id")
    body["exception_id"] = validate_identifier(body["exception_id"], "exception_id")
    body["expected_exception_set_revision"] = _validate_nonnegative(
        body["expected_exception_set_revision"], "expected_exception_set_revision"
    )
    body["operator"] = _validate_operator(body["operator"])
    body["replacement"] = _validate_replacement(body["replacement"])
    return body


def _validate_interval(start: object, end: object, *, code: str) -> tuple[datetime, datetime]:
    if (
        type(start) is not datetime
        or type(end) is not datetime
        or start.tzinfo is None
        or end.tzinfo is None
        or start.utcoffset() is None
        or end.utcoffset() is None
        or start.second != 0
        or end.second != 0
        or start.microsecond != 0
        or end.microsecond != 0
        or end <= start
    ):
        raise StaffingError(code, "exception interval")
    return start, end


def _validate_exception(
    exception: object, *, code: str = "staffing_invalid_evidence"
) -> StaffingException:
    if type(exception) is not StaffingException:
        raise StaffingError(code, "exception")
    try:
        validate_identifier(exception.exception_id, "exception_id")
        validate_identifier(exception.staff_id, "staff_id")
        kind = _parse_kind(exception.kind)
        note = _validate_note(exception.note)
    except StaffingError:
        raise StaffingError(code, "exception fields") from None
    if type(exception.service_date) is not date:
        raise StaffingError(code, "exception service_date")
    _validate_interval(exception.unavailable_start, exception.unavailable_end, code=code)
    if kind != exception.kind or note != exception.note:
        raise StaffingError(code, "exception fields")
    return exception


def build_staffing_exception(
    body: dict[str, object], *, start: datetime, end: datetime
) -> StaffingException:
    """Build a normalized exception with its request-derived stable identity."""

    _validate_interval(start, end, code="invalid_exception_time")
    if type(body) is not dict:
        raise StaffingError("staffing_invalid_roster", "exception body")
    request_id = validate_identifier(body.get("request_id"), "request_id")
    service_date = body.get("service_date")
    if type(service_date) is not date:
        raise StaffingError("staffing_invalid_roster", "service_date")
    staff_id = validate_identifier(body.get("staff_id"), "staff_id")
    kind = _parse_kind(body.get("kind"))
    note = _validate_note(body.get("note"))
    exception_id = "exception_" + stable_digest(
        {"schema": "nxt-staffing-exception-id/v1", "request_id": request_id}
    )[:24]
    result = StaffingException(exception_id, service_date, staff_id, kind, start, end, note)
    return _validate_exception(result)


def build_replacement_exception(
    previous: StaffingException,
    replacement: dict[str, object],
    *,
    start: datetime,
    end: datetime,
) -> StaffingException:
    """Build a correction while preserving the original nested identity."""

    previous = _validate_exception(previous)
    normalized = _validate_replacement(replacement)
    _validate_interval(start, end, code="invalid_exception_time")
    result = StaffingException(
        previous.exception_id,
        previous.service_date,
        previous.staff_id,
        normalized["kind"],
        start,
        end,
        normalized["note"],
    )
    return _validate_exception(result)


def exception_digest(exception: StaffingException) -> str:
    exception = _validate_exception(exception)
    return stable_digest(
        {"schema": "nxt-staffing-exception-digest/v1", "exception": exception}
    )


def _normalize_interval(
    *,
    service_date: date,
    kind: str,
    time_local: object,
    timezone_name: str,
    shift: Assignment,
) -> tuple[datetime, datetime]:
    if kind in _WHOLE_SHIFT_KINDS:
        if time_local is not None:
            raise StaffingError("invalid_exception_time", "whole-shift kind")
        return shift.start_at, shift.end_at
    if type(time_local) is not str:
        raise StaffingError("invalid_exception_time", "time_local")
    try:
        point = resolve_local_minute(service_date, time_local, timezone_name)
    except StaffingError:
        raise StaffingError("invalid_exception_time", "time_local") from None
    if not shift.start_at < point < shift.end_at:
        raise StaffingError("invalid_exception_time", "outside shift")
    return (
        (shift.start_at, point)
        if kind == ExceptionKind.LATE.value
        else (point, shift.end_at)
    )


def normalize_exception(payload: object, *, roster: RosterRevision) -> StaffingException:
    """Normalize one exact exception request against a selected roster revision."""

    body = require_exact_object(payload, EXCEPTION_KEYS)
    if type(roster) is not RosterRevision:
        raise StaffingError("staffing_invalid_roster", "roster")
    if body["schema"] != "nxt-staffing-exception/v1":
        raise StaffingError("staffing_invalid_roster", "schema")
    body["request_id"] = validate_identifier(body["request_id"], "request_id")
    expected_roster = _validate_nonnegative(
        body["expected_roster_revision"], "expected_roster_revision"
    )
    _validate_nonnegative(
        body["expected_exception_set_revision"], "expected_exception_set_revision"
    )
    service_date = parse_local_date(body["service_date"], "service_date")
    body["staff_id"] = validate_identifier(body["staff_id"], "staff_id")
    kind = _parse_kind(body["kind"])
    _validate_operator(body["operator"])
    body["note"] = _validate_note(body["note"])
    if expected_roster != roster.revision:
        raise StaffingError("STALE_ROSTER_REVISION", "expected_roster_revision")
    day = materialize_service_day(roster, service_date)
    shift = day.assignment_for_staff(body["staff_id"])
    if shift is None:
        raise StaffingError("unknown_staff_or_shift", str(body["staff_id"]))
    start, end = _normalize_interval(
        service_date=service_date,
        kind=kind,
        time_local=body["time_local"],
        timezone_name=roster.site_timezone,
        shift=shift,
    )
    normalized = dict(body)
    normalized["service_date"] = service_date
    normalized["kind"] = kind
    return build_staffing_exception(normalized, start=start, end=end)


def normalize_exception_replacement(
    body: dict[str, object], *, previous: StaffingException, roster: RosterRevision
) -> StaffingException:
    """Validate a parsed correction and preserve the prior exception identity."""

    parsed = parse_exception_correction_request(body)
    previous = _validate_exception(previous)
    if parsed["exception_id"] != previous.exception_id:
        raise StaffingError("staffing_invalid_evidence", "correction target identity")
    if type(roster) is not RosterRevision:
        raise StaffingError("staffing_invalid_roster", "roster")
    day = materialize_service_day(roster, previous.service_date)
    shift = day.assignment_for_staff(previous.staff_id)
    if shift is None:
        raise StaffingError("unknown_staff_or_shift", previous.staff_id)
    replacement = parsed["replacement"]
    start, end = _normalize_interval(
        service_date=previous.service_date,
        kind=replacement["kind"],
        time_local=replacement["time_local"],
        timezone_name=roster.site_timezone,
        shift=shift,
    )
    return build_replacement_exception(previous, replacement, start=start, end=end)


def validate_nonoverlapping_exceptions(
    exceptions: Sequence[StaffingException], *, replay: bool = False
) -> tuple[StaffingException, ...]:
    """Validate and return the one canonical active-exception order."""

    code = "staffing_invalid_evidence" if replay else "invalid_exception_time"
    items = _closed_sequence(exceptions, "exceptions", code=code)
    ordered = tuple(
        sorted(
            (_validate_exception(item, code=code) for item in items),
            key=lambda item: (
                item.staff_id,
                item.unavailable_start,
                item.unavailable_end,
                item.exception_id,
            ),
        )
    )
    previous_by_key: dict[tuple[date, str], StaffingException] = {}
    for item in ordered:
        key = (item.service_date, item.staff_id)
        previous = previous_by_key.get(key)
        if previous is not None and item.unavailable_start < previous.unavailable_end:
            raise StaffingError(code, "overlapping active exceptions")
        previous_by_key[key] = item
    return ordered


def find_overlapping_exception(
    active: Sequence[StaffingException], candidate: StaffingException
) -> StaffingException | None:
    """Return the first active exception whose half-open interval intersects.

    An active record that shares the candidate's identity is skipped so a
    correction may replace its own previous interval. The active set is
    validated and canonically ordered first, so the answer is deterministic.
    """

    candidate = _validate_exception(candidate, code="invalid_exception_time")
    for item in validate_nonoverlapping_exceptions(active, replay=True):
        if item.exception_id == candidate.exception_id:
            continue
        if (
            item.service_date != candidate.service_date
            or item.staff_id != candidate.staff_id
        ):
            continue
        if (
            candidate.unavailable_start < item.unavailable_end
            and item.unavailable_start < candidate.unavailable_end
        ):
            return item
    return None


def require_no_overlapping_exception(
    active: Sequence[StaffingException], candidate: StaffingException
) -> None:
    """Refuse a record or correction that would overlap an active exception.

    The closed ``OVERLAPPING_EXCEPTION`` code is a business conflict: the
    original record stays untouched and the caller may cancel or correct it,
    or submit a non-overlapping interval, under a new request ID.
    """

    if find_overlapping_exception(active, candidate) is not None:
        raise StaffingError("OVERLAPPING_EXCEPTION", "overlapping active exception")


def _validate_event_basics(event: object) -> StaffingEvent:
    if type(event) is not StaffingEvent:
        raise StaffingError("staffing_invalid_evidence", "event")
    if type(event.sequence) is not int or event.sequence < 1:
        raise StaffingError("staffing_invalid_evidence", "event sequence")
    return event


def _validate_revision(value: object, expected: int) -> None:
    if type(value) is not int or value != expected:
        raise StaffingError("staffing_invalid_evidence", "exception_set_revision")


def _replay_exception_state(
    events: Sequence[StaffingEvent],
) -> tuple[dict[str, StaffingException], dict[date, int]]:
    visible: dict[str, StaffingException] = {}
    revisions: dict[date, int] = {}
    previous_sequence = 0
    for raw_event in _closed_sequence(
        events, "events", code="staffing_invalid_evidence"
    ):
        event = _validate_event_basics(raw_event)
        if event.sequence <= previous_sequence:
            raise StaffingError("staffing_invalid_evidence", "event sequence order")
        previous_sequence = event.sequence
        if event.event_type == "exception_recorded":
            payload = event.payload
            if type(payload) is not ExceptionRecordedPayload:
                raise StaffingError("staffing_invalid_evidence", "record payload")
            exception = _validate_exception(payload.exception)
            if (
                payload.exception_id != exception.exception_id
                or payload.service_date != exception.service_date
                or payload.exception_digest != exception_digest(exception)
                or exception.exception_id in visible
            ):
                raise StaffingError("staffing_invalid_evidence", "record coherence")
            next_revision = revisions.get(exception.service_date, 0) + 1
            _validate_revision(payload.exception_set_revision, next_revision)
            revisions[exception.service_date] = next_revision
            visible[exception.exception_id] = exception
        elif event.event_type == "exception_corrected":
            payload = event.payload
            if type(payload) is not ExceptionCorrectedPayload:
                raise StaffingError("staffing_invalid_evidence", "correction payload")
            previous = _validate_exception(payload.previous_exception)
            replacement = _validate_exception(payload.replacement_exception)
            current = visible.get(payload.exception_id)
            if (
                current is None
                or current != previous
                or payload.exception_id != previous.exception_id
                or replacement.exception_id != previous.exception_id
                or replacement.service_date != previous.service_date
                or replacement.staff_id != previous.staff_id
                or payload.exception_digest != exception_digest(replacement)
            ):
                raise StaffingError("staffing_invalid_evidence", "correction coherence")
            next_revision = revisions.get(previous.service_date, 0) + 1
            _validate_revision(payload.exception_set_revision, next_revision)
            revisions[previous.service_date] = next_revision
            visible[payload.exception_id] = replacement
        elif event.event_type == "exception_cancelled":
            payload = event.payload
            if type(payload) is not ExceptionCancelledPayload:
                raise StaffingError("staffing_invalid_evidence", "cancellation payload")
            cancelled = _validate_exception(payload.cancelled_exception)
            current = visible.get(payload.exception_id)
            if (
                current is None
                or current != cancelled
                or payload.exception_id != cancelled.exception_id
            ):
                raise StaffingError("staffing_invalid_evidence", "cancellation coherence")
            next_revision = revisions.get(cancelled.service_date, 0) + 1
            _validate_revision(payload.exception_set_revision, next_revision)
            revisions[cancelled.service_date] = next_revision
            del visible[payload.exception_id]
        else:
            continue
        validate_nonoverlapping_exceptions(tuple(visible.values()), replay=True)
    return visible, revisions


def active_exceptions(
    events: Sequence[StaffingEvent], service_date: date
) -> tuple[StaffingException, ...]:
    if type(service_date) is not date:
        raise StaffingError("staffing_invalid_evidence", "service_date")
    visible, _ = _replay_exception_state(events)
    return validate_nonoverlapping_exceptions(
        tuple(item for item in visible.values() if item.service_date == service_date),
        replay=True,
    )


def exception_set_revision(events: Sequence[StaffingEvent], service_date: date) -> int:
    if type(service_date) is not date:
        raise StaffingError("staffing_invalid_evidence", "service_date")
    _, revisions = _replay_exception_state(events)
    return revisions.get(service_date, 0)


def apply_exceptions(
    assignments: Sequence[Assignment], exceptions: Sequence[StaffingException]
) -> tuple[Assignment, ...]:
    """Subtract active half-open intervals without mutating either input."""

    source = _closed_sequence(
        assignments, "assignments", code="staffing_invalid_evidence"
    )
    if any(type(item) is not Assignment for item in source):
        raise StaffingError("staffing_invalid_evidence", "assignments")
    unavailable = validate_nonoverlapping_exceptions(exceptions)
    result: list[Assignment] = []
    for assignment in source:
        fragments = ((assignment.start_at, assignment.end_at),)
        for exception in unavailable:
            if exception.staff_id != assignment.staff_id:
                continue
            next_fragments: list[tuple[datetime, datetime]] = []
            for start, end in fragments:
                if exception.unavailable_end <= start or end <= exception.unavailable_start:
                    next_fragments.append((start, end))
                    continue
                if start < exception.unavailable_start:
                    next_fragments.append((start, exception.unavailable_start))
                if exception.unavailable_end < end:
                    next_fragments.append((exception.unavailable_end, end))
            fragments = tuple(next_fragments)
        result.extend(
            assignment.with_interval(start, end)
            for start, end in fragments
            if start < end
        )
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.start_at,
                item.end_at,
                item.staff_id,
                item.assignment_id,
            ),
        )
    )
