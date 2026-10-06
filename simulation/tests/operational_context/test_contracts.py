"""Identity, time, canonical JSON parity and label vocabulary."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import nxt_operational_context as oc
from nxt_edge_task.contracts import canonical_json as edge_canonical_json
from nxt_pilot_ops.serialization import canonical_json as pilot_canonical_json, stable_digest as pilot_digest

from .conftest import AS_OF, RECEIVED, make_batch


def test_canonical_json_matches_the_repository_rule() -> None:
    value = {"b": [1, 2.5, "é"], "a": {"z": None, "y": True}, "t": datetime(2026, 8, 8, 1, 2, 3, 4, tzinfo=timezone.utc), "zero": -0.0}
    assert oc.canonical_json(value) == pilot_canonical_json(value)
    assert oc.canonical_json({"b": [1, 2.5, "é"], "a": {"z": None}}) == edge_canonical_json({"b": [1, 2.5, "é"], "a": {"z": None}})
    assert oc.stable_digest(value) == pilot_digest(value)
    assert oc.canonical_json(value).endswith("}") and "\n" not in oc.canonical_json(value)


def test_utc_text_and_parse_round_trip_with_microseconds() -> None:
    instant = datetime(2026, 8, 8, 10, 30, 0, 123456, tzinfo=timezone.utc)
    text = oc.utc_text(instant)
    assert text == "2026-08-08T10:30:00.123456Z"
    assert oc.parse_utc(text) == instant
    with pytest.raises(oc.OperationalContextError):
        oc.utc_text(datetime(2026, 8, 8))


def test_operating_day_is_derived_in_the_site_timezone_from_a_utc_instant() -> None:
    # The fixture scenario origin 2026-08-08T00:00Z is 08:00 Asia/Shanghai on 2026-08-08.
    day = oc.operating_day(datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc), "Asia/Shanghai")
    assert day.date == "2026-08-08"
    assert oc.utc_text(day.start_utc) == "2026-08-07T16:00:00.000000Z"
    assert oc.utc_text(day.end_utc) == "2026-08-08T16:00:00.000000Z"
    # 23:30 UTC on the 7th is already 07:30 on the 8th locally.
    assert oc.operating_day(datetime(2026, 8, 7, 23, 30, tzinfo=timezone.utc), "Asia/Shanghai").date == "2026-08-08"
    assert oc.operating_day(datetime(2026, 8, 7, 15, 59, tzinfo=timezone.utc), "Asia/Shanghai").date == "2026-08-07"


def test_operating_day_refuses_naive_instants_and_unknown_zones() -> None:
    with pytest.raises(oc.OperationalContextError, match="naive_datetime"):
        oc.operating_day(datetime(2026, 8, 8), "Asia/Shanghai")
    with pytest.raises(oc.OperationalContextError, match="invalid_timezone"):
        oc.operating_day(AS_OF, "Mars/Olympus")


def test_identifiers_are_bounded_and_opaque() -> None:
    assert oc.ContextAdmissionFacts("site-1", "dep.1", "UTC", "sha256:" + "0" * 64).site_id == "site-1"
    for bad in ("", "has space", "a" * 65, "名字", "-leading", "john.smith@example.com"):
        with pytest.raises(oc.OperationalContextError, match="identifier_not_opaque"):
            oc.ContextAdmissionFacts(bad, "dep", "UTC", "sha256:" + "0" * 64)
    with pytest.raises(oc.OperationalContextError, match="invalid_manifest_digest"):
        oc.ContextAdmissionFacts("site", "dep", "UTC", "not-a-digest")


def test_profile_validation_and_digest_are_stable() -> None:
    profile = oc.SourceProfile(oc.SourceSystem.SALES, "csv-sales", "0.1.0", "Asia/Shanghai", 3600, sku_ball_units={"B": 1, "A": 2})
    again = oc.SourceProfile(oc.SourceSystem.SALES, "csv-sales", "0.1.0", "Asia/Shanghai", 3600, sku_ball_units={"A": 2, "B": 1})
    assert profile.profile_digest == again.profile_digest
    with pytest.raises(oc.OperationalContextError, match="invalid_profile"):
        oc.SourceProfile(oc.SourceSystem.SALES, "csv-sales", "0.1.0", "Asia/Shanghai", 0)
    with pytest.raises(oc.OperationalContextError, match="invalid_profile"):
        oc.SourceProfile(oc.SourceSystem.SALES, "csv-sales", "0.1.0", "Asia/Shanghai", 60, sku_ball_units={"A": 0})


def test_batch_identity_depends_on_file_digest_and_profile_not_on_time(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.SALES]
    one = make_batch(admission, profile, "a,b\n", received=RECEIVED)
    two = make_batch(admission, profile, "a,b\n", received=datetime(2027, 1, 1, tzinfo=timezone.utc), name="other.csv", exported=None)
    assert one.import_batch_id == two.import_batch_id
    assert one.import_batch_id.startswith("ocb_") and len(one.import_batch_id) == 28
    other_file = make_batch(admission, profile, "a,b\nc,d\n")
    assert other_file.import_batch_id != one.import_batch_id


def test_event_identity_is_stable_and_revisions_get_their_own_id(admission, profiles) -> None:
    profile = profiles[oc.SourceSystem.STAFFING]
    base = dict(
        site_id=admission.site_id, source_system=oc.SourceSystem.STAFFING, source_record_id="SH-1", event_type=oc.EventType.SHIFT_SCHEDULED,
        occurred_at=AS_OF, received_at=RECEIVED, import_batch_id="ocb_" + "0" * 24, source_timezone="Asia/Shanghai", source_status="SHIFT_SCHEDULED",
        payload={"staff_ref": "W-1"}, provenance={"row_number": 2},
    )
    original = oc.OperationalEvent(source_revision_key="", **base)
    revision = oc.OperationalEvent(source_revision_key="2026-08-08T11:00:00.000000Z", **base)
    assert original.event_id.startswith("oce_") and original.event_id != revision.event_id
    assert oc.OperationalEvent.from_dict(original.to_dict()) == original
    with pytest.raises(oc.OperationalContextError, match="event_type_not_in_source"):
        oc.OperationalEvent(source_revision_key="", **{**base, "event_type": oc.EventType.SALE_CAPTURED})
    tampered = original.to_dict()
    tampered["payload"] = {"staff_ref": "W-2"}
    with pytest.raises(oc.OperationalContextError, match="content_digest_mismatch"):
        oc.OperationalEvent.from_dict(tampered)


def test_label_vocabulary_is_exactly_four_and_never_measured() -> None:
    assert [str(label) for label in oc.EvidenceLabel] == ["PLANNED", "SOURCE_RECORDED", "DERIVED", "UNKNOWN"]
    assert "MEASURED" not in {str(label) for label in oc.EvidenceLabel}
