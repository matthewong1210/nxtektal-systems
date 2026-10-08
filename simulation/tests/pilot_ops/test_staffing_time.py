"""Minute-resolution tests for staffing service days."""

from datetime import date, datetime, timezone

import pytest

from nxt_pilot_ops.staffing.contracts import StaffingError
from nxt_pilot_ops.staffing.time import resolve_local_minute, utc_text


def test_resolve_local_minute_returns_the_unique_utc_instant():
    assert resolve_local_minute(date(2026, 10, 5), "09:30", "Asia/Shanghai") == datetime(
        2026, 10, 5, 1, 30, tzinfo=timezone.utc
    )


def test_staffing_utc_text_is_normalized_and_always_has_fixed_microseconds():
    assert utc_text(datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc)) == (
        "2026-10-05T09:30:00.000000Z"
    )
    assert utc_text(
        datetime.fromisoformat("2026-10-05T17:30:00.123456+08:00")
    ) == "2026-10-05T09:30:00.123456Z"
    with pytest.raises(StaffingError):
        utc_text(datetime(2026, 10, 5, 9, 30))


@pytest.mark.parametrize(
    ("service_date", "minute", "zone"),
    [
        (date(2026, 3, 8), "02:30", "America/New_York"),
        (date(2026, 11, 1), "01:30", "America/New_York"),
        (date(2026, 10, 5), "24:00", "Asia/Shanghai"),
        (date(2026, 10, 5), "09:30:00", "Asia/Shanghai"),
        (date(2026, 10, 5), "09:30:00.1", "Asia/Shanghai"),
        (date(2026, 10, 5), "9:30", "Asia/Shanghai"),
        (date(2026, 10, 5), "09:60", "Asia/Shanghai"),
        (date(2026, 10, 5), "09:30", "Not/A_Zone"),
    ],
)
def test_resolve_local_minute_fails_closed_for_invalid_or_nonunique_minutes(
    service_date, minute, zone
):
    with pytest.raises(StaffingError) as error:
        resolve_local_minute(service_date, minute, zone)
    assert error.value.code == "staffing_invalid_roster"
    assert error.value.detail in {
        "minute must be HH:MM within service date",
        "ambiguous or nonexistent local minute",
        "site_timezone",
    }


@pytest.mark.parametrize("bad", [datetime(2026, 10, 5), "2026-10-05", None, True])
def test_resolve_local_minute_requires_an_exact_date(bad):
    with pytest.raises(StaffingError, match="staffing_invalid_roster"):
        resolve_local_minute(bad, "09:30", "Asia/Shanghai")


def test_local_wall_instants_enumerates_fall_back_repeats_and_spring_forward_gaps() -> None:
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from nxt_pilot_ops.staffing.time import local_wall_instants

    new_york = ZoneInfo("America/New_York")
    assert local_wall_instants(datetime(2026, 11, 1, 1, 30), new_york) == (
        datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc),
    )
    assert local_wall_instants(datetime(2026, 11, 1, 0, 30), new_york) == (
        datetime(2026, 11, 1, 4, 30, tzinfo=timezone.utc),
    )
    assert local_wall_instants(datetime(2026, 3, 8, 2, 30), new_york) == ()
    assert local_wall_instants(datetime(2026, 10, 5, 9, 0), ZoneInfo("Asia/Shanghai")) == (
        datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(StaffingError):
        local_wall_instants(datetime(2026, 11, 1, 1, 30, tzinfo=timezone.utc), new_york)
