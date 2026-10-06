"""Projection semantics: the four labels never blur, freshness follows coverage end."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import nxt_operational_context as oc

from .conftest import AS_OF, RECEIVED, fixture_text, import_fixture_set, import_text, snapshot

STAFF_HEADER = "record_id,record_type,staff_ref,occurred_at,role_code,shift_start,shift_end,shift_record_id,request_record_id,correction_time\n"
PLAY_HEADER = "record_id,record_type,session_ref,occurred_at,scheduled_start,actual_start,actual_finish,player_count,actual_player_count,activity_type,correction_time\n"


def _staffing_snapshot(admission, profiles, rows: str, as_of: datetime = AS_OF, exported=None) -> dict:
    records: list[dict] = []
    import_text(records, admission, profiles[oc.SourceSystem.STAFFING], STAFF_HEADER + rows, exported=exported or as_of)
    return snapshot(records, admission, profiles, as_of)["staffing"]


def test_fixture_day_staffing_at_18_30_local(admission, profiles) -> None:
    records: list[dict] = []
    import_fixture_set(records, admission, profiles)
    staffing = snapshot(records, admission, profiles)["staffing"]
    assert staffing["status"] == "ok"
    assert staffing["operating_day"]["date"] == "2026-08-08" and staffing["operating_day"]["timezone"] == "Asia/Shanghai"
    # Seven planned shifts minus one cancellation.
    assert staffing["scheduled_today"] == {"value": 6, "label": "PLANNED", "basis": staffing["scheduled_today"]["basis"], "source_status": "ok", "reason": None}
    # 18:30 local: W-004 (approved 13:00-21:00), W-005 (14:00-22:00), W-006 (14:00-22:00).
    assert staffing["scheduled_now"]["value"] == 3 and staffing["scheduled_now"]["label"] == "PLANNED"
    # Clocked in and not out at 18:30: W-004 and W-006 (W-001/W-002 clocked out, W-005 never clocked in).
    assert staffing["confirmed_present_now"]["value"] == 2 and staffing["confirmed_present_now"]["label"] == "DERIVED"
    assert staffing["presence_unknown_now"]["value"] == 1  # W-005: scheduled, no clock event, not recorded absent
    assert staffing["present_unscheduled_now"]["value"] == 0
    assert staffing["confirmed_absent_today"]["value"] == 1 and staffing["confirmed_absent_today"]["label"] == "SOURCE_RECORDED"
    assert staffing["approved_shift_changes_today"]["value"] == 0  # the approval was recorded on the 7th
    assert staffing["pending_change_requests"]["value"] == 1 and staffing["pending_change_requests"]["label"] == "SOURCE_RECORDED"
    # W-001 07:52-12:01 (249) and 12:46-16:03 (197); W-002 08:04-16:10 (486).
    assert staffing["worked_intervals_today"]["value"] == {"count": 3, "total_minutes": 932.0}
    assert staffing["open_clock_ins"]["value"] == 2
    assert staffing["next_material_change"]["value"] == {"at_utc": "2026-08-08T13:00:00.000000Z", "kind": "shift_end", "count": 1}
    roles = {row["role_code"]: row for row in staffing["by_role"]}
    assert roles["RANGE_OPS"] == {"role_code": "RANGE_OPS", "scheduled_now": 2, "present_now": 1, "unknown_now": 1, "absent_now": 0, "label": "DERIVED"}
    assert roles["FRONT_DESK"]["present_now"] == 1
    # No value, key or basis claims availability; the notes say so in words.
    assert "availab" not in oc.canonical_json({k: v for k, v in staffing.items() if k != "notes"}).lower()


def test_scheduled_is_not_present_and_clocked_in_is_not_available(admission, profiles) -> None:
    rows = "S-1,SHIFT_SCHEDULED,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,2026-08-08T08:00:00+08:00,2026-08-08T20:00:00+08:00,,,\n"
    staffing = _staffing_snapshot(admission, profiles, rows)
    assert staffing["scheduled_now"]["value"] == 1
    assert staffing["confirmed_present_now"]["value"] == 0
    assert staffing["presence_unknown_now"]["value"] == 1  # unknown, never absent
    assert staffing["confirmed_absent_today"]["value"] == 0
    rows += "C-1,CLOCK_IN,W-1,2026-08-08T08:02:00+08:00,,,,S-1,,\n"
    staffing = _staffing_snapshot(admission, profiles, rows)
    assert staffing["confirmed_present_now"]["value"] == 1 and staffing["presence_unknown_now"]["value"] == 0
    assert "availab" not in oc.canonical_json({k: v for k, v in staffing.items() if k != "notes"}).lower()
    assert "Clocked-in is not available" in staffing["notes"][0]


def test_requested_changes_never_alter_coverage_but_approved_ones_do(admission, profiles) -> None:
    base = "S-1,SHIFT_SCHEDULED,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,2026-08-08T08:00:00+08:00,2026-08-08T12:00:00+08:00,,,\n"
    request = base + "R-1,SHIFT_CHANGE_REQUESTED,W-1,2026-08-07T09:00:00+08:00,,2026-08-08T08:00:00+08:00,2026-08-08T20:00:00+08:00,S-1,REQ-1,\n"
    staffing = _staffing_snapshot(admission, profiles, request)  # 18:30 local is outside 08:00-12:00
    assert staffing["scheduled_now"]["value"] == 0 and staffing["pending_change_requests"]["value"] == 1
    approved = request + "A-1,SHIFT_CHANGE_APPROVED,W-1,2026-08-07T10:00:00+08:00,,2026-08-08T08:00:00+08:00,2026-08-08T20:00:00+08:00,S-1,REQ-1,\n"
    staffing = _staffing_snapshot(admission, profiles, approved)
    assert staffing["scheduled_now"]["value"] == 1 and staffing["pending_change_requests"]["value"] == 0
    rejected = request + "J-1,SHIFT_CHANGE_REJECTED,W-1,2026-08-07T10:00:00+08:00,,,,,REQ-1,\n"
    staffing = _staffing_snapshot(admission, profiles, rejected)
    assert staffing["scheduled_now"]["value"] == 0 and staffing["pending_change_requests"]["value"] == 0


def test_missing_clock_out_yields_an_open_interval_and_no_duration(admission, profiles) -> None:
    rows = (
        "S-1,SHIFT_SCHEDULED,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,2026-08-08T08:00:00+08:00,2026-08-08T20:00:00+08:00,,,\n"
        "C-1,CLOCK_IN,W-1,2026-08-08T08:00:00+08:00,,,,S-1,,\n"
    )
    staffing = _staffing_snapshot(admission, profiles, rows)
    assert staffing["worked_intervals_today"]["value"] == {"count": 0, "total_minutes": 0.0}
    assert staffing["open_clock_ins"]["value"] == 1
    rows += "O-1,CLOCK_OUT,W-1,2026-08-08T12:30:00+08:00,,,,S-1,,\n"
    staffing = _staffing_snapshot(admission, profiles, rows)
    assert staffing["worked_intervals_today"]["value"] == {"count": 1, "total_minutes": 270.0}
    assert staffing["confirmed_present_now"]["value"] == 0  # clocked out before 18:30


def test_fixture_day_operations_at_18_30_local(admission, profiles) -> None:
    records: list[dict] = []
    import_fixture_set(records, admission, profiles)
    operations = snapshot(records, admission, profiles)["operations"]
    sales, play = operations["sales"], operations["play"]
    # Captured mapped units at or before 18:30 local (TX-0018 at 18:33 is after as_of):
    # S:(1+3+2+4+2)=12*50=600, M:(2+1+3+2+2)=10*100=1000, L:(1+2+1+3)=7*150=1050 => 2650;
    # void of TX-0006 (M x1 = 100) and refund of 1 M (100) => 2450.
    assert sales["ball_units_sold_today"] == {"value": 2450, "label": "DERIVED", "basis": sales["ball_units_sold_today"]["basis"], "source_status": "ok", "reason": None}
    assert sales["transactions_today"]["value"] == 15 and sales["transactions_today"]["label"] == "SOURCE_RECORDED"
    assert sales["reversals_today"]["value"] == 2
    assert sales["unmapped_sku_transactions_today"]["value"] == 1
    assert sales["unmatched_reversals"]["value"] == 0
    window = sales["recent_window"]["value"]
    # (17:30, 18:30] local: TX-0015 (2 S), TX-0016 (3 L), TX-0017 (2 M); TX-0014 at exactly 17:30 is outside.
    assert window["window_s"] == 3600 and window["transactions"] == 3 and window["ball_units"] == 2 * 50 + 3 * 150 + 2 * 100
    assert window["window_fully_covered"] is True
    assert "dispens" not in oc.canonical_json({k: v for k, v in sales.items() if k != "notes"}).lower()
    assert play["booked_sessions_today"]["value"] == 7 and play["booked_sessions_today"]["label"] == "PLANNED"  # 8 bookings minus 1 cancellation
    assert play["booked_players_today"]["value"] == 21
    assert play["started_sessions_today"]["value"] == 4 and play["started_sessions_today"]["label"] == "SOURCE_RECORDED"
    assert play["active_sessions_now"]["value"] == 2 and play["active_sessions_now"]["label"] == "DERIVED"
    assert play["active_players_booked"] == {"value": 6, "label": "PLANNED", "basis": play["active_players_booked"]["basis"], "source_status": "ok", "reason": None}
    assert play["active_players_confirmed"]["label"] == "UNKNOWN"  # only one of two active sessions has an actual count
    assert play["completed_sessions_today"]["value"] == {"count": 2, "mean_minutes": 70.5, "min_minutes": 67.0, "max_minutes": 74.0}
    assert play["sessions_missing_finish"]["value"] == 2
    assert play["upcoming_booked_players"]["value"] == 9


def test_booked_is_not_started_and_missing_finish_means_unknown_duration(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.PLAY]
    records: list[dict] = []
    booked = PLAY_HEADER + "B-1,SESSION_BOOKED,SES-1,2026-08-07T12:00:00+08:00,2026-08-08T17:00:00+08:00,,,4,,RANGE,\n"
    import_text(records, admission, profile, booked, exported=AS_OF)
    play = snapshot(records, admission, profiles)["operations"]["play"]
    assert play["booked_sessions_today"]["value"] == 1 and play["started_sessions_today"]["value"] == 0 and play["active_sessions_now"]["value"] == 0
    started = booked + "S-1,SESSION_STARTED,SES-1,2026-08-08T17:05:00+08:00,,2026-08-08T17:05:00+08:00,,,,,\n"
    records = []
    import_text(records, admission, profile, started, exported=AS_OF)
    play = snapshot(records, admission, profiles)["operations"]["play"]
    assert play["active_sessions_now"]["value"] == 1
    assert play["active_players_booked"]["value"] == 4 and play["active_players_booked"]["label"] == "PLANNED"
    assert play["active_players_confirmed"]["label"] == "UNKNOWN"
    assert play["completed_sessions_today"]["value"]["count"] == 0 and play["sessions_missing_finish"]["value"] == 1
    counted = started + "P-1,PLAYER_COUNT_UPDATED,SES-1,2026-08-08T17:10:00+08:00,,,,,3,,\n"
    records = []
    import_text(records, admission, profile, counted, exported=AS_OF)
    play = snapshot(records, admission, profiles)["operations"]["play"]
    assert play["active_players_confirmed"] == {"value": 3, "label": "DERIVED", "basis": play["active_players_confirmed"]["basis"], "source_status": "ok", "reason": None}


def test_stale_source_is_never_ok_and_marks_every_value(admission, profiles) -> None:
    records: list[dict] = []
    import_fixture_set(records, admission, profiles)
    late = AS_OF + timedelta(hours=3)  # coverage end 18:40 local is now 2h50 old; staffing stale after 2h, sales/play after 1h
    snap = snapshot(records, admission, profiles, late)
    assert snap["status"] == "stale"
    assert {s["status"] for s in snap["sources"].values()} == {"stale"}
    for section in (snap["staffing"], snap["operations"]["sales"], snap["operations"]["play"]):
        assert section["status"] == "stale"
        for key, item in section.items():
            if isinstance(item, dict) and "label" in item:
                assert item["source_status"] == "stale", key
    assert "ok" not in oc.canonical_json(snap["sources"]).replace("\"ok\"", "")
    assert [e["code"] for e in snap["exceptions"]] == ["source_stale", "source_stale", "source_stale"]


def test_freshness_ages_the_declared_export_time_not_the_import_time(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    records: list[dict] = []
    header = "transaction_id,transaction_time,sku,quantity,status,correction_time,original_transaction_id,refunded_quantity\n"
    old_export = AS_OF - timedelta(hours=2)
    import_text(records, admission, profile, header + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,2,CAPTURED,,,\n", received=AS_OF - timedelta(minutes=1), exported=old_export)
    source = snapshot(records, admission, profiles)["sources"]["sales"]
    assert source["status"] == "stale" and source["coverage_basis"] == "declared_export_time"
    assert source["age_s"] == 7200.0
    records = []
    import_text(records, admission, profile, header + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,2,CAPTURED,,,\n", received=AS_OF - timedelta(days=3), exported=AS_OF - timedelta(minutes=10))
    source = snapshot(records, admission, profiles)["sources"]["sales"]
    assert source["status"] == "ok" and source["age_s"] == 600.0


def test_missing_source_yields_unknown_values_with_reasons_never_zero(admission, profiles) -> None:
    records: list[dict] = []
    import_text(records, admission, profiles[oc.SourceSystem.SALES], fixture_text("sales_2026-08-08.csv"))
    snap = snapshot(records, admission, profiles)
    assert snap["status"] == "missing"
    staffing, play = snap["staffing"], snap["operations"]["play"]
    for section in (staffing, play):
        assert section["status"] == "missing"
        for key, item in section.items():
            if isinstance(item, dict) and "label" in item:
                assert item == {"value": None, "label": "UNKNOWN", "basis": item["basis"], "source_status": "missing", "reason": item["reason"]}, key
                assert item["reason"]
    assert staffing["by_role"] == []
    assert snap["operations"]["sales"]["status"] == "ok"
    assert [e["code"] for e in snap["exceptions"]] == ["source_missing", "source_missing"]


def test_a_legitimate_zero_is_zero_not_unknown(admission, profiles) -> None:
    rows = "S-1,SHIFT_SCHEDULED,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,2026-08-08T08:00:00+08:00,2026-08-08T12:00:00+08:00,,,\n"
    staffing = _staffing_snapshot(admission, profiles, rows)
    assert staffing["scheduled_now"] == {"value": 0, "label": "PLANNED", "basis": staffing["scheduled_now"]["basis"], "source_status": "ok", "reason": None}
    assert staffing["present_unscheduled_now"]["value"] == 0


def test_recent_window_is_unknown_when_coverage_ends_before_the_window(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    header = "transaction_id,transaction_time,sku,quantity,status,correction_time,original_transaction_id,refunded_quantity\n"
    records: list[dict] = []
    import_text(records, admission, profile, header + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,2,CAPTURED,,,\n", exported=datetime(2026, 8, 8, 1, 30, tzinfo=timezone.utc))
    sales = snapshot(records, admission, profiles)["operations"]["sales"]
    assert sales["recent_window"]["label"] == "UNKNOWN" and "coverage ends before" in sales["recent_window"]["reason"]
    assert sales["ball_units_sold_today"]["value"] == 200  # the day total still counts what was recorded


def test_snapshot_identity_fields_and_vocabulary(admission, profiles) -> None:
    records: list[dict] = []
    import_fixture_set(records, admission, profiles)
    snap = snapshot(records, admission, profiles)
    assert snap["schema"] == "nxt-operational-context/context/v1" and snap["owner"] == "nxt_operational_context"
    assert snap["as_of"] == "2026-08-08T10:30:00.000000Z" and snap["as_of_basis"] == "FIXTURE_DECLARED"
    assert snap["label_vocabulary"] == ["PLANNED", "SOURCE_RECORDED", "DERIVED", "UNKNOWN"]
    assert snap["manifest_digest"] == admission.manifest_digest
    text = oc.canonical_json(snap).lower()
    for word in ("inventory", "dispensed", "measured", "revenue", "payroll", "forecast"):
        assert word not in text, word
