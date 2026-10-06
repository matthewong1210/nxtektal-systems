"""Idempotent re-import, append-only corrections, conflicts, and deterministic replay."""

from __future__ import annotations

from datetime import timedelta

import nxt_operational_context as oc

from .conftest import AS_OF, RECEIVED, fixture_text, import_fixture_set, import_text, snapshot

SALES_HEADER = "transaction_id,transaction_time,sku,quantity,status,correction_time,original_transaction_id,refunded_quantity\n"
STAFF_HEADER = "record_id,record_type,staff_ref,occurred_at,role_code,shift_start,shift_end,shift_record_id,request_record_id,correction_time\n"


def test_re_importing_the_same_file_records_a_duplicate_batch_and_no_event(admission, profiles) -> None:
    records: list[dict] = []
    first = import_fixture_set(records, admission, profiles)
    assert [d.outcome for d in first] == ["committed"] * 3
    before = snapshot(records, admission, profiles)
    event_count = len(oc.recorded_events(records))
    again = import_fixture_set(records, admission, profiles)
    assert [d.outcome for d in again] == ["duplicate_batch"] * 3
    assert [[s.record_kind for s in d.specs] for d in again] == [["import_batch_duplicate"]] * 3
    assert len(oc.recorded_events(records)) == event_count
    after = snapshot(records, admission, profiles)
    for section in ("staffing", "operations", "operating_day", "status", "event_count"):
        assert oc.canonical_json(after[section]) == oc.canonical_json(before[section])
    assert after["sources"]["sales"]["duplicate_batches"] == 1 and before["sources"]["sales"]["duplicate_batches"] == 0


def test_a_re_export_with_new_rows_records_only_the_new_events(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    records: list[dict] = []
    first = SALES_HEADER + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,2,CAPTURED,,,\n"
    import_text(records, admission, profile, first, name="a.csv")
    second = first + "T-2,2026-08-08T09:30:00+08:00,BUCKET-S,1,CAPTURED,,,\n"
    decision = import_text(records, admission, profile, second, name="b.csv")
    assert decision.outcome == "committed"
    assert decision.events_recorded == 1 and decision.events_duplicate == 1
    assert len(oc.recorded_events(records)) == 2


def test_same_record_id_with_different_content_is_a_conflict_that_rejects_the_batch(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    records: list[dict] = []
    import_text(records, admission, profile, SALES_HEADER + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,2,CAPTURED,,,\n", name="a.csv")
    conflicting = SALES_HEADER + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,3,CAPTURED,,,\nT-9,2026-08-08T09:10:00+08:00,BUCKET-S,1,CAPTURED,,,\n"
    decision = import_text(records, admission, profile, conflicting, name="b.csv")
    assert decision.outcome == "rejected"
    assert [e.to_dict() for e in decision.errors] == [{"row_number": 2, "column": None, "reason": "event_conflict"}]
    assert len(oc.recorded_events(records)) == 1  # T-9 was not admitted either
    assert records[-1]["record_kind"] == "import_batch_rejected"


def test_a_revision_supersedes_the_head_and_keeps_the_original(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.STAFFING]
    records: list[dict] = []
    original = STAFF_HEADER + "S-1,SHIFT_SCHEDULED,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,2026-08-08T08:00:00+08:00,2026-08-08T16:00:00+08:00,,,\n"
    import_text(records, admission, profile, original, name="a.csv")
    revised = STAFF_HEADER + "S-1,SHIFT_SCHEDULED,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,2026-08-08T10:00:00+08:00,2026-08-08T18:00:00+08:00,,,2026-08-07T12:00:00+08:00\n"
    decision = import_text(records, admission, profile, revised, name="b.csv")
    assert decision.outcome == "committed" and decision.events_recorded == 1
    events = oc.recorded_events(records)
    assert len(events) == 2
    head = oc.head_events(events)
    assert len(head) == 1 and head[0].payload["shift_start"] == "2026-08-08T02:00:00.000000Z"
    assert head[0].correction.kind is oc.CorrectionKind.REPLACEMENT
    assert head[0].correction.target_event_id == events[0].event_id
    # A stale revision (older correction time than the head) never supersedes it.
    stale = STAFF_HEADER + "S-1,SHIFT_SCHEDULED,W-1,2026-08-01T09:00:00+08:00,RANGE_OPS,2026-08-08T09:00:00+08:00,2026-08-08T17:00:00+08:00,,,2026-08-06T12:00:00+08:00\n"
    import_text(records, admission, profile, stale, name="c.csv")
    head_again = oc.head_events(oc.recorded_events(records))
    assert head_again[0].payload["shift_start"] == "2026-08-08T02:00:00.000000Z"
    assert len(oc.recorded_events(records)) == 3


def test_a_refund_adjusts_the_total_and_history_keeps_the_sale(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    records: list[dict] = []
    import_text(records, admission, profile, SALES_HEADER + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,2,CAPTURED,,,\n", name="a.csv")
    assert snapshot(records, admission, profiles)["operations"]["sales"]["ball_units_sold_today"]["value"] == 200
    import_text(records, admission, profile, SALES_HEADER + "T-2,2026-08-08T10:00:00+08:00,BUCKET-M,2,REFUNDED,,T-1,1\n", name="b.csv")
    events = oc.recorded_events(records)
    assert [str(e.event_type) for e in events] == ["SALE_CAPTURED", "SALE_REVERSED"]
    assert events[1].correction.target_event_id == events[0].event_id
    sales = snapshot(records, admission, profiles)["operations"]["sales"]
    assert sales["ball_units_sold_today"]["value"] == 100
    assert sales["transactions_today"]["value"] == 1 and sales["reversals_today"]["value"] == 1


def test_a_reversal_of_an_unknown_sale_is_recorded_but_never_subtracted(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    records: list[dict] = []
    import_text(records, admission, profile, SALES_HEADER + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,2,CAPTURED,,,\nT-2,2026-08-08T10:00:00+08:00,BUCKET-M,1,VOIDED,,T-404,\n", name="a.csv")
    events = oc.recorded_events(records)
    assert events[1].correction.target_event_id is None
    sales = snapshot(records, admission, profiles)["operations"]["sales"]
    assert sales["ball_units_sold_today"]["value"] == 200
    assert sales["unmatched_reversals"]["value"] == 1
    snap = snapshot(records, admission, profiles)
    assert any(e["code"] == "unmatched_reversals" for e in snap["exceptions"])


def test_rejected_batches_are_recorded_safely_and_change_nothing(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    records: list[dict] = []
    import_text(records, admission, profile, SALES_HEADER + "T-1,2026-08-08T09:00:00+08:00,BUCKET-M,2,CAPTURED,,,\n", name="a.csv")
    before = snapshot(records, admission, profiles)
    decision = import_text(records, admission, profile, SALES_HEADER + "T-2,2026-08-08T09:30:00+08:00,BUCKET-M,secret-two,CAPTURED,,,\n", name="b.csv")
    assert decision.outcome == "rejected"
    rejection = records[-1]
    assert rejection["record_kind"] == "import_batch_rejected"
    assert rejection["payload"]["errors"] == [{"row_number": 2, "column": "quantity", "reason": "invalid_count"}]
    assert "secret" not in oc.canonical_json(rejection)
    after = snapshot(records, admission, profiles)
    for section in ("staffing", "operations"):
        assert oc.canonical_json(after[section]) == oc.canonical_json(before[section])
    assert after["sources"]["sales"]["rejected_batches"] == 1
    assert after["sources"]["sales"]["last_rejection"]["error_count"] == 1
    assert any(e["code"] == "import_batches_rejected" for e in after["exceptions"])


def test_replay_is_deterministic_regardless_of_import_order(admission, profiles) -> None:
    forward: list[dict] = []
    import_fixture_set(forward, admission, profiles)
    backward: list[dict] = []
    for name, system in (("play_2026-08-08.csv", oc.SourceSystem.PLAY), ("sales_2026-08-08.csv", oc.SourceSystem.SALES), ("staffing_2026-08-08.csv", oc.SourceSystem.STAFFING)):
        import_text(backward, admission, profiles[system], fixture_text(name), name=name, received=RECEIVED + timedelta(minutes=5))
    one = snapshot(forward, admission, profiles)
    two = snapshot(backward, admission, profiles)
    for section in ("staffing", "operations", "operating_day", "status"):
        assert oc.canonical_json(one[section]) == oc.canonical_json(two[section])
    assert oc.canonical_json(snapshot(forward, admission, profiles)) == oc.canonical_json(snapshot(forward, admission, profiles))


def test_every_projected_value_is_traceable_to_recorded_events(admission, profiles) -> None:
    records: list[dict] = []
    import_fixture_set(records, admission, profiles)
    events = oc.recorded_events(records)
    batches = oc.recorded_batches(records)
    batch_ids = {b["import_batch_id"] for b in batches}
    assert all(e.import_batch_id in batch_ids for e in events)
    assert all(e.provenance["row_number"] >= 2 and e.provenance["source_file_digest"].startswith("sha256:") for e in events)
    assert all(e.to_dict()["event_id"] == e.event_id for e in events)
    snap = snapshot(records, admission, profiles)
    assert snap["event_count"] == len(events) == 54
