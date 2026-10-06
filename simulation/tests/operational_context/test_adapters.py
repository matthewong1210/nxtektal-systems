"""CSV adapters: synthetic fixtures parse; invalid input rejects the whole batch safely."""

from __future__ import annotations

import pytest

import nxt_operational_context as oc

from .conftest import fixture_text, make_batch

STAFF_HEADER = "record_id,record_type,staff_ref,occurred_at,role_code,shift_start,shift_end,shift_record_id,request_record_id,correction_time\n"
SALES_HEADER = "transaction_id,transaction_time,sku,quantity,status,correction_time,original_transaction_id,refunded_quantity\n"
PLAY_HEADER = "record_id,record_type,session_ref,occurred_at,scheduled_start,actual_start,actual_finish,player_count,actual_player_count,activity_type,correction_time\n"


def _parse(admission, profile, text):
    return oc.parse_csv(text, make_batch(admission, profile, text))


def test_fixture_files_parse_into_the_expected_events(admission, profiles) -> None:
    staffing = _parse(admission, profiles[oc.SourceSystem.STAFFING], fixture_text("staffing_2026-08-08.csv"))
    sales = _parse(admission, profiles[oc.SourceSystem.SALES], fixture_text("sales_2026-08-08.csv"))
    play = _parse(admission, profiles[oc.SourceSystem.PLAY], fixture_text("play_2026-08-08.csv"))
    assert isinstance(staffing, oc.AdapterResult) and len(staffing.events) == 20
    assert isinstance(sales, oc.AdapterResult) and len(sales.events) == 18
    assert isinstance(play, oc.AdapterResult) and len(play.events) == 16
    assert sales.diagnostics["unmapped_skus"] == 1
    types = {str(e.event_type) for e in staffing.events}
    assert types == {"SHIFT_SCHEDULED", "SHIFT_CANCELLED", "CLOCK_IN", "CLOCK_OUT", "SHIFT_CHANGE_REQUESTED", "SHIFT_CHANGE_APPROVED", "ABSENCE_RECORDED"}
    # Every stored instant is UTC text; the +08:00 source offsets are gone.
    for event in staffing.events + sales.events + play.events:
        assert oc.utc_text(event.occurred_at).endswith("Z")
        for key, value in event.payload.items():
            if isinstance(value, str) and value[:4] == "2026":
                assert value.endswith("Z"), key


def test_a_forbidden_header_rejects_the_whole_batch_before_any_row_is_read(admission, profiles) -> None:
    result = _parse(admission, profiles[oc.SourceSystem.STAFFING], fixture_text("rejected/staffing_forbidden_header.csv"))
    assert isinstance(result, oc.BatchRejection)
    assert result.rows_seen == 0
    assert [(e.column, e.reason) for e in result.errors] == [("employee_name", "forbidden_column")]
    assert "REDACTED" not in oc.canonical_json(result.to_dict())


@pytest.mark.parametrize(
    "header",
    ["Employee Name", "phone_number", "home-address", "hourly_wage", "payroll_id", "medical_note", "date_of_birth", "comments", "PerformanceRating"],
)
def test_every_forbidden_header_family_is_refused(admission, profiles, header) -> None:
    text = STAFF_HEADER.rstrip("\n") + f",{header}\n"
    result = _parse(admission, profiles[oc.SourceSystem.STAFFING], text)
    assert isinstance(result, oc.BatchRejection)
    assert any(e.reason == "forbidden_column" and e.column == header for e in result.errors)


def test_unexpected_missing_and_duplicate_columns_reject_the_batch(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    extra = SALES_HEADER.rstrip("\n") + ",register_mood\n"
    assert [(e.column, e.reason) for e in _parse(admission, profile, extra).errors] == [("register_mood", "unexpected_column")]
    missing = "transaction_id,transaction_time,sku,quantity\n"
    assert {e.reason for e in _parse(admission, profile, missing).errors} == {"missing_column"}
    duplicate = SALES_HEADER.rstrip("\n") + ",sku\n"
    assert any(e.reason == "duplicate_column" for e in _parse(admission, profile, duplicate).errors)
    assert isinstance(_parse(admission, profile, ""), oc.BatchRejection)


def test_one_bad_row_rejects_the_whole_batch_with_its_row_number_and_no_value(admission, profiles) -> None:
    result = _parse(admission, profiles[oc.SourceSystem.SALES], fixture_text("rejected/sales_bad_row.csv"))
    assert isinstance(result, oc.BatchRejection)
    assert result.rows_seen == 3
    assert [e.to_dict() for e in result.errors] == [{"row_number": 3, "column": "quantity", "reason": "invalid_count"}]
    assert "REDACTED-77" not in oc.canonical_json(result.to_dict())


def test_staffing_row_rules(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.STAFFING]
    rows = {
        "missing interval": "S-1,SHIFT_SCHEDULED,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,,,,,\n",
        "inverted interval": "S-1,SHIFT_SCHEDULED,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,2026-08-08T16:00:00+08:00,2026-08-08T08:00:00+08:00,,,\n",
        "unknown type": "S-1,SHIFT_PERFORMANCE,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,,,,,\n",
        "name as ref": "S-1,CLOCK_IN,John Smith,2026-08-08T09:00:00+08:00,,,,,,\n",
        "approval without interval": "A-1,SHIFT_CHANGE_APPROVED,W-1,2026-08-07T09:00:00+08:00,,,,S-1,REQ-1,\n",
        "cancel without shift": "C-1,SHIFT_CANCELLED,W-1,2026-08-07T09:00:00+08:00,,,,,,\n",
        "bad timestamp": "S-1,CLOCK_IN,W-1,yesterday,,,,,,\n",
    }
    for label, row in rows.items():
        result = _parse(admission, profile, STAFF_HEADER + row)
        assert isinstance(result, oc.BatchRejection), label
        assert all(e.row_number == 2 for e in result.errors), label
    good = STAFF_HEADER + "S-1,SHIFT_SCHEDULED,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,2026-08-08T08:00:00+08:00,2026-08-08T16:00:00+08:00,,,\n"
    result = _parse(admission, profile, good)
    assert isinstance(result, oc.AdapterResult)
    event = result.events[0]
    assert event.payload["schedule_effect"] == "none"
    assert event.payload["shift_record_id"] == "S-1"
    assert event.payload["shift_start"] == "2026-08-08T00:00:00.000000Z"


def test_naive_source_timestamps_use_the_declared_source_timezone(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.STAFFING]
    text = STAFF_HEADER + "S-1,CLOCK_IN,W-1,2026-08-08 09:00:00,,,,,,\n"
    result = _parse(admission, profile, text)
    assert isinstance(result, oc.AdapterResult)
    assert oc.utc_text(result.events[0].occurred_at) == "2026-08-08T01:00:00.000000Z"


def test_sales_row_rules_and_mapping(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    captured = SALES_HEADER + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,2,CAPTURED,,,\n"
    result = _parse(admission, profile, captured)
    assert isinstance(result, oc.AdapterResult)
    event = result.events[0]
    assert event.event_type is oc.EventType.SALE_CAPTURED
    assert event.payload["mapped_ball_units"] == 200 and event.payload["quantity"] == 2
    unmapped = _parse(admission, profile, SALES_HEADER + "T-2,2026-08-08T09:00:00+08:00,GLOVE-01,1,CAPTURED,,,\n")
    assert unmapped.events[0].payload["mapped_ball_units"] is None
    refund = _parse(admission, profile, SALES_HEADER + "T-3,2026-08-08T10:00:00+08:00,BUCKET-M,2,REFUNDED,,T-1,1\n")
    reversal = refund.events[0]
    assert reversal.event_type is oc.EventType.SALE_REVERSED
    assert reversal.correction.kind is oc.CorrectionKind.REVERSAL and reversal.correction.original_record_id == "T-1"
    assert reversal.payload["reversed_quantity"] == 1 and reversal.payload["mapped_reversed_ball_units"] == 100
    void = _parse(admission, profile, SALES_HEADER + "T-4,2026-08-08T10:00:00+08:00,BUCKET-M,2,VOIDED,,T-1,\n")
    assert void.events[0].payload["reversed_quantity"] == 2
    for bad in (
        "T-5,2026-08-08T10:00:00+08:00,BUCKET-M,2,REFUNDED,,T-1,\n",  # refund needs a quantity
        "T-6,2026-08-08T10:00:00+08:00,BUCKET-M,2,REFUNDED,,T-1,3\n",  # over-reversal
        "T-7,2026-08-08T10:00:00+08:00,BUCKET-M,2,VOIDED,,,\n",  # reversal needs its original
        "T-8,2026-08-08T10:00:00+08:00,BUCKET-M,0,CAPTURED,,,\n",  # zero quantity
        "T-9,2026-08-08T10:00:00+08:00,BUCKET-M,1,CAPTURED,,T-1,\n",  # a capture carries no original
        "T-10,2026-08-08T10:00:00+08:00,BUCKET-M,1,SOLD,,,\n",  # unknown status
    ):
        assert isinstance(_parse(admission, profile, SALES_HEADER + bad), oc.BatchRejection), bad


def test_play_row_rules(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.PLAY]
    booked = PLAY_HEADER + "B-1,SESSION_BOOKED,SES-1,2026-08-07T12:00:00+08:00,2026-08-08T09:00:00+08:00,,,4,,RANGE,\n"
    result = _parse(admission, profile, booked)
    assert isinstance(result, oc.AdapterResult)
    assert result.events[0].payload["player_count"] == 4 and result.events[0].payload["actual_start"] is None
    for bad in (
        "B-2,SESSION_BOOKED,SES-1,2026-08-07T12:00:00+08:00,,,,4,,RANGE,\n",  # booking needs a scheduled start
        "B-3,SESSION_STARTED,SES-1,2026-08-08T09:00:00+08:00,,,,,,,\n",  # start needs actual_start
        "B-4,PLAYER_COUNT_UPDATED,SES-1,2026-08-08T09:00:00+08:00,,,,,,,\n",  # count update needs a count
        "B-5,SESSION_BOOKED,SES-1,2026-08-07T12:00:00+08:00,2026-08-08T09:00:00+08:00,,,four,,RANGE,\n",
    ):
        assert isinstance(_parse(admission, profile, PLAY_HEADER + bad), oc.BatchRejection), bad


def test_exact_duplicate_rows_are_idempotent_but_conflicting_duplicates_reject(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    row = "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,2,CAPTURED,,,\n"
    result = _parse(admission, profile, SALES_HEADER + row + row)
    assert isinstance(result, oc.AdapterResult) and len(result.events) == 1 and result.rows_seen == 2
    conflicting = _parse(admission, profile, SALES_HEADER + row + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,3,CAPTURED,,,\n")
    assert isinstance(conflicting, oc.BatchRejection)
    assert [(e.row_number, e.reason) for e in conflicting.errors] == [(3, "conflicting_duplicate_row")]


def test_profile_and_adapter_must_agree_on_the_source(admission, profiles) -> None:
    with pytest.raises(oc.OperationalContextError, match="profile_source_mismatch"):
        oc.parse_sales_csv(SALES_HEADER, make_batch(admission, profiles[oc.SourceSystem.PLAY], SALES_HEADER))
