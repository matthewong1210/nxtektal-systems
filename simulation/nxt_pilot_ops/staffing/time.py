"""Pure staffing time normalization with explicit service-day timezones."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import MINUTE_PATTERN, StaffingError


def utc_text(value: datetime) -> str:
    """Return one canonical UTC representation with fixed microseconds."""

    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise StaffingError("staffing_invalid_roster", "timestamp must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def local_wall_instants(naive: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    """Return the sorted unique UTC instants that render as ``naive`` in ``zone``.

    One instant for an ordinary wall time, two across a fall-back repeat, none
    for a spring-forward gap.  Both folds are tried and kept only when the
    round trip reproduces the naive value exactly.
    """

    if type(naive) is not datetime or naive.tzinfo is not None:
        raise StaffingError("staffing_invalid_roster", "naive wall time")
    candidates: set[datetime] = set()
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(utc)
    return tuple(sorted(candidates))


def resolve_local_minute(service_date: date, text: str, timezone_name: str) -> datetime:
    """Resolve one local wall-clock minute only when it names one UTC instant."""

    if type(service_date) is not date:
        raise StaffingError("staffing_invalid_roster", "service_date")
    if type(text) is not str or MINUTE_PATTERN.fullmatch(text) is None:
        raise StaffingError(
            "staffing_invalid_roster", "minute must be HH:MM within service date"
        )
    if type(timezone_name) is not str:
        raise StaffingError("staffing_invalid_roster", "site_timezone")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise StaffingError("staffing_invalid_roster", "site_timezone") from None

    hour, minute = (int(item) for item in text.split(":"))
    local = datetime.combine(service_date, time(hour, minute))
    unique = local_wall_instants(local, zone)
    if len(unique) != 1:
        raise StaffingError(
            "staffing_invalid_roster", "ambiguous or nonexistent local minute"
        )
    return unique[0]
