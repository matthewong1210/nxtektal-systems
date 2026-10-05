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
    candidates: list[datetime] = []
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == local:
            candidates.append(utc)
    unique = set(candidates)
    if len(unique) != 1:
        raise StaffingError(
            "staffing_invalid_roster", "ambiguous or nonexistent local minute"
        )
    return next(iter(unique))
