"""Exception normalization, replay, and immutable assignment subtraction."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from nxt_pilot_ops.staffing.contracts import (
    Assignment,
    ExceptionCancelledPayload,
    ExceptionCorrectedPayload,
    ExceptionRecordedPayload,
    StaffingError,
    StaffingEvent,
)
from nxt_pilot_ops.staffing.exceptions import (
    CANCEL_KEYS,
    CORRECT_KEYS,
    EXCEPTION_KEYS,
    REPLACEMENT_KEYS,
    active_exceptions,
    apply_exceptions,
    build_replacement_exception,
    exception_digest,
    exception_set_revision,
    find_overlapping_exception,
    normalize_exception,
    normalize_exception_replacement,
    parse_exception_cancel_request,
    parse_exception_correction_request,
    require_no_overlapping_exception,
    validate_nonoverlapping_exceptions,
)
from nxt_pilot_ops.staffing.roster import validate_roster_import

from .staffing_fixtures import roster_import_request


SERVICE_DATE = date(2026, 10, 5)
UTC = timezone.utc


def _roster(payload: dict[str, object] | None = None):
    return validate_roster_import(
        roster_import_request() if payload is None else payload,
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )


def _request(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema": "nxt-staffing-exception/v1",
        "request_id": "exception-001",
        "expected_roster_revision": 1,
        "expected_exception_set_revision": 0,
        "service_date": "2026-10-05",
        "staff_id": "staff-001",
        "kind": "LATE",
        "time_local": "10:00",
        "operator": "course-manager",
        "note": None,
    }
    body.update(changes)
    return body


def _event(event_type: str, payload: object, sequence: int) -> StaffingEvent:
    return StaffingEvent(
        event_type,
        f"event-{sequence}",
        sequence,
        "pilot-course-a",
        "pilot-a-edge-task-sim-v0",
        datetime(2026, 10, 5, 0, sequence, tzinfo=UTC),
        None,
        payload,
    )


def _record(exception, sequence: int = 1, revision: int = 1) -> StaffingEvent:
    return _event(
        "exception_recorded",
        ExceptionRecordedPayload(
            f"record-{sequence}",
            "a" * 64,
            exception.exception_id,
            exception.service_date,
            revision,
            exception_digest(exception),
            exception,
            "course-manager",
        ),
        sequence,
    )


def _cancel(exception, sequence: int = 2, revision: int = 2) -> StaffingEvent:
    return _event(
        "exception_cancelled",
        ExceptionCancelledPayload(
            f"cancel-{sequence}",
            "b" * 64,
            exception.exception_id,
            revision,
            exception,
            "course-manager",
            None,
        ),
        sequence,
    )


def test_request_key_surfaces_and_strict_parsers_are_closed_and_detached():
    assert EXCEPTION_KEYS == frozenset(
        {
            "schema", "request_id", "expected_roster_revision",
            "expected_exception_set_revision", "service_date", "staff_id",
            "kind", "time_local", "operator", "note",
        }
    )
    assert CANCEL_KEYS == frozenset(
        {"schema", "request_id", "exception_id", "expected_exception_set_revision", "operator", "note"}
    )
    assert CORRECT_KEYS == frozenset(
        {"schema", "request_id", "exception_id", "expected_exception_set_revision", "operator", "replacement"}
    )
    assert REPLACEMENT_KEYS == frozenset({"kind", "time_local", "note"})
    cancel = {
        "schema": "nxt-staffing-exception-cancel/v1",
        "request_id": "cancel-001",
        "exception_id": "exception_abc",
        "expected_exception_set_revision": 1,
        "operator": "course-manager",
        "note": None,
    }
    parsed_cancel = parse_exception_cancel_request(cancel)
    cancel["operator"] = "mutated"
    assert parsed_cancel["operator"] == "course-manager"
    correction = {
        "schema": "nxt-staffing-exception-correct/v1",
        "request_id": "correct-001",
        "exception_id": "exception_abc",
        "expected_exception_set_revision": 1,
        "operator": "course-manager",
        "replacement": {"kind": "EARLY_DEPARTURE", "time_local": "15:00", "note": None},
    }
    parsed = parse_exception_correction_request(correction)
    correction["replacement"]["kind"] = "LEAVE"
    assert parsed["replacement"] == {
        "kind": "EARLY_DEPARTURE", "time_local": "15:00", "note": None
    }


@pytest.mark.parametrize(
    ("parser", "body"),
    [
        (parse_exception_cancel_request, {"schema": "bad"}),
        (parse_exception_cancel_request, {
            "schema": "nxt-staffing-exception-cancel/v1", "request_id": "x",
            "exception_id": "y", "expected_exception_set_revision": True,
            "operator": "ok", "note": None,
        }),
        (parse_exception_correction_request, {
            "schema": "nxt-staffing-exception-correct/v1", "request_id": "x",
            "exception_id": "y", "expected_exception_set_revision": 0,
            "operator": "ok", "replacement": {"kind": "LATE", "time_local": None, "note": None},
        }),
    ],
)
def test_request_parsers_fail_closed(parser, body):
    with pytest.raises(StaffingError):
        parser(body)


@pytest.mark.parametrize(
    ("kind", "time_local", "want_start", "want_end"),
    [
        ("LEAVE", None, datetime(2026, 10, 5, 1, tzinfo=UTC), datetime(2026, 10, 5, 9, tzinfo=UTC)),
        ("UNAVAILABLE", None, datetime(2026, 10, 5, 1, tzinfo=UTC), datetime(2026, 10, 5, 9, tzinfo=UTC)),
        ("LATE", "10:00", datetime(2026, 10, 5, 1, tzinfo=UTC), datetime(2026, 10, 5, 2, tzinfo=UTC)),
        ("EARLY_DEPARTURE", "15:00", datetime(2026, 10, 5, 7, tzinfo=UTC), datetime(2026, 10, 5, 9, tzinfo=UTC)),
    ],
)
def test_normalize_exception_maps_every_kind_to_a_half_open_utc_interval(
    kind, time_local, want_start, want_end
):
    result = normalize_exception(_request(kind=kind, time_local=time_local), roster=_roster())
    assert result.exception_id == "exception_c1dc5a5a4a15290d48b2c7af"
    assert (result.kind, result.unavailable_start, result.unavailable_end) == (
        kind, want_start, want_end
    )


def test_exception_digest_has_the_frozen_schema_wrapped_golden_value():
    exception = normalize_exception(_request(), roster=_roster())
    assert exception_digest(exception) == "08ecde9d312bfc48469024db738cfedac9c81896eb5bf3d17eb3a70c0065b57b"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"expected_roster_revision": 0}, "STALE_ROSTER_REVISION"),
        ({"expected_roster_revision": True}, "staffing_invalid_roster"),
        ({"expected_exception_set_revision": -1}, "staffing_invalid_roster"),
        ({"staff_id": "missing"}, "unknown_staff_or_shift"),
        ({"kind": "LEAVE", "time_local": "10:00"}, "invalid_exception_time"),
        ({"kind": "LATE", "time_local": None}, "invalid_exception_time"),
        ({"kind": "LATE", "time_local": "09:00"}, "invalid_exception_time"),
        ({"kind": "EARLY_DEPARTURE", "time_local": "17:00"}, "invalid_exception_time"),
        ({"time_local": "10:00:00"}, "invalid_exception_time"),
        ({"service_date": "2026-10-04"}, "staffing_roster_not_found"),
        ({"operator": "bad\noperator"}, "staffing_invalid_roster"),
        ({"note": "x" * 501}, "staffing_invalid_roster"),
    ],
)
def test_normalize_exception_rejects_invalid_or_stale_input(changes, code):
    with pytest.raises(StaffingError) as caught:
        normalize_exception(_request(**changes), roster=_roster())
    assert caught.value.code == code


def test_normalize_exception_rejects_unknown_fields_and_non_plain_objects():
    bad = _request(extra=True)
    with pytest.raises(StaffingError):
        normalize_exception(bad, roster=_roster())
    class Mapping(dict):
        pass
    with pytest.raises(StaffingError):
        normalize_exception(Mapping(_request()), roster=_roster())


def test_correction_preserves_nested_identity_date_and_staff():
    previous = normalize_exception(_request(), roster=_roster())
    body = parse_exception_correction_request({
        "schema": "nxt-staffing-exception-correct/v1",
        "request_id": "correct-001",
        "exception_id": previous.exception_id,
        "expected_exception_set_revision": 1,
        "operator": "course-manager",
        "replacement": {"kind": "EARLY_DEPARTURE", "time_local": "15:00", "note": "changed"},
    })
    replacement = normalize_exception_replacement(body, previous=previous, roster=_roster())
    assert (replacement.exception_id, replacement.service_date, replacement.staff_id) == (
        previous.exception_id, previous.service_date, previous.staff_id
    )
    assert replacement.kind == "EARLY_DEPARTURE"
    assert replacement.note == "changed"
    assert build_replacement_exception(
        previous, body["replacement"], start=replacement.unavailable_start,
        end=replacement.unavailable_end,
    ) == replacement


def test_global_replay_projects_correction_and_cancellation_without_deleting_history():
    previous = normalize_exception(_request(), roster=_roster())
    replacement = replace(
        previous, kind="EARLY_DEPARTURE",
        unavailable_start=datetime(2026, 10, 5, 7, tzinfo=UTC),
        unavailable_end=datetime(2026, 10, 5, 9, tzinfo=UTC),
    )
    corrected = _event(
        "exception_corrected",
        ExceptionCorrectedPayload(
            "correct-1", "c" * 64, previous.exception_id, replacement, 2,
            exception_digest(replacement), previous, "course-manager",
        ),
        2,
    )
    events = (_record(previous), corrected, _cancel(replacement, 3, 3))
    assert active_exceptions(events[:2], SERVICE_DATE) == (replacement,)
    assert active_exceptions(events, SERVICE_DATE) == ()
    assert exception_set_revision(events, SERVICE_DATE) == 3
    assert len(events) == 3


def test_global_replay_rejects_descending_event_sequences():
    exception = normalize_exception(_request(), roster=_roster())
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        active_exceptions(
            (_record(exception, sequence=2), _cancel(exception, sequence=1, revision=2)),
            SERVICE_DATE,
        )


def test_replay_fails_closed_for_corrupt_identity_digest_transition_and_other_date():
    exception = normalize_exception(_request(), roster=_roster())
    corrupt_payload = replace(_record(exception).payload, exception_digest="0" * 64)
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        active_exceptions((_event("exception_recorded", corrupt_payload, 1),), SERVICE_DATE)
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        active_exceptions((_cancel(exception, 1, 1),), SERVICE_DATE)
    other = replace(exception, service_date=date(2026, 10, 12))
    corrupt_other = replace(_record(other).payload, service_date=SERVICE_DATE)
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        active_exceptions((_event("exception_recorded", corrupt_other, 1),), SERVICE_DATE)


@pytest.mark.parametrize(
    "operation",
    [
        lambda: active_exceptions(True, SERVICE_DATE),
        lambda: exception_set_revision(True, SERVICE_DATE),
        lambda: validate_nonoverlapping_exceptions(True),
        lambda: apply_exceptions(True, ()),
        lambda: apply_exceptions((), True),
    ],
)
def test_public_sequence_inputs_never_leak_raw_type_errors(operation):
    with pytest.raises(StaffingError):
        operation()


def test_overlap_rejects_but_half_open_adjacency_and_canonical_order_are_valid():
    first = normalize_exception(_request(request_id="a", kind="LATE", time_local="10:00"), roster=_roster())
    adjacent = replace(
        first, exception_id="exception-b", kind="EARLY_DEPARTURE",
        unavailable_start=first.unavailable_end,
        unavailable_end=datetime(2026, 10, 5, 3, tzinfo=UTC),
    )
    assert validate_nonoverlapping_exceptions((adjacent, first)) == (first, adjacent)
    overlapping = replace(adjacent, exception_id="exception-c", unavailable_start=datetime(2026, 10, 5, 1, 30, tzinfo=UTC))
    with pytest.raises(StaffingError) as caught:
        validate_nonoverlapping_exceptions((first, overlapping))
    assert caught.value.code == "invalid_exception_time"


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (datetime(2026, 10, 5, 1, tzinfo=UTC), datetime(2026, 10, 5, 9, tzinfo=UTC), ()),
        (datetime(2026, 10, 5, 1, tzinfo=UTC), datetime(2026, 10, 5, 3, tzinfo=UTC), ((3, 9),)),
        (datetime(2026, 10, 5, 7, tzinfo=UTC), datetime(2026, 10, 5, 9, tzinfo=UTC), ((1, 7),)),
        (datetime(2026, 10, 5, 3, tzinfo=UTC), datetime(2026, 10, 5, 7, tzinfo=UTC), ((1, 3), (7, 9))),
        (datetime(2026, 10, 5, 9, tzinfo=UTC), datetime(2026, 10, 5, 10, tzinfo=UTC), ((1, 9),)),
    ],
)
def test_apply_exceptions_subtracts_full_head_tail_middle_and_adjacency(start, end, expected):
    assignment = Assignment(
        "assignment-source", "staff-001", "RANGE_ATTENDANT", "RANGE_A",
        datetime(2026, 10, 5, 1, tzinfo=UTC), datetime(2026, 10, 5, 9, tzinfo=UTC),
    )
    base = normalize_exception(_request(kind="LEAVE", time_local=None), roster=_roster())
    exception = replace(base, unavailable_start=start, unavailable_end=end)
    source = (assignment,)
    result = apply_exceptions(source, (exception,))
    assert tuple((item.start_at.hour, item.end_at.hour) for item in result) == expected
    assert source == (assignment,)
    for item in result:
        if (item.start_at, item.end_at) != (assignment.start_at, assignment.end_at):
            assert item.assignment_id != assignment.assignment_id


def _leave(request_id: str, staff_id: str = "staff-001"):
    return normalize_exception(
        _request(request_id=request_id, staff_id=staff_id, kind="LEAVE", time_local=None),
        roster=_roster(),
    )


def test_find_overlapping_exception_detects_identical_and_partial_intervals():
    first = _leave("leave-a")
    duplicate = _leave("leave-b")
    assert duplicate.exception_id != first.exception_id
    assert find_overlapping_exception((first,), duplicate) == first
    late = normalize_exception(
        _request(request_id="late-c", kind="LATE", time_local="10:00"), roster=_roster()
    )
    assert find_overlapping_exception((first,), late) == first
    with pytest.raises(StaffingError) as caught:
        require_no_overlapping_exception((first,), duplicate)
    assert caught.value.code == "OVERLAPPING_EXCEPTION"
    assert "staff-001" not in caught.value.detail


def test_find_overlapping_exception_allows_adjacency_other_workers_and_self_replacement():
    first = normalize_exception(
        _request(request_id="a", kind="LATE", time_local="10:00"), roster=_roster()
    )
    adjacent = replace(
        first,
        exception_id="exception-b",
        kind="EARLY_DEPARTURE",
        unavailable_start=first.unavailable_end,
        unavailable_end=datetime(2026, 10, 5, 3, tzinfo=UTC),
    )
    assert find_overlapping_exception((first,), adjacent) is None
    other_worker = replace(first, exception_id="exception-c", staff_id="staff-002")
    assert find_overlapping_exception((first,), other_worker) is None
    other_date = replace(
        first,
        exception_id="exception-d",
        service_date=date(2026, 10, 6),
        unavailable_start=first.unavailable_start + timedelta(days=1),
        unavailable_end=first.unavailable_end + timedelta(days=1),
    )
    assert find_overlapping_exception((first,), other_date) is None
    # A correction keeps its identity and may replace its own interval.
    replacement = replace(
        first,
        unavailable_end=datetime(2026, 10, 5, 2, 30, tzinfo=UTC),
    )
    assert find_overlapping_exception((first,), replacement) is None
    require_no_overlapping_exception((first,), replacement)


def test_require_no_overlapping_exception_validates_both_inputs_closed():
    first = _leave("leave-a")
    with pytest.raises(StaffingError) as caught:
        require_no_overlapping_exception((first,), "not-an-exception")  # type: ignore[arg-type]
    assert caught.value.code == "invalid_exception_time"
    with pytest.raises(StaffingError) as caught:
        require_no_overlapping_exception(("corrupt",), first)  # type: ignore[arg-type]
    assert caught.value.code == "staffing_invalid_evidence"
