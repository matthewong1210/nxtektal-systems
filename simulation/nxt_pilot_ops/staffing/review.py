"""Manager-review semantics for stored candidates.

Two deterministic, clock-free readings of one stored candidate:

* a local explanation that replaces the pseudonymous ``worker_<hex>`` and
  ``assignment_<hex>`` tokens a provider wrote into ``rationale`` or an
  operational warning with the local display name or shift label the
  reservation's alias maps bind them to.  The raw provider text is kept
  beside it; nothing here rewrites ledger evidence or sends a name,
  staff ID, or assignment ID to a provider; and
* the candidate's action window: the earliest end of any shift the
  candidate adds or removes.  Past it the candidate is no longer a current
  action, because at least one affected shift has ended.  A candidate
  without any timed operation keeps the whole service date open and closes
  at local midnight of the next day in the site timezone.

The composition root compares the window against its own audit clock to
label a candidate ``EXPIRED`` on the public wire, and the manager-response
admission compares it against the injected ``recorded_at`` before an
ACCEPT or MODIFY may append.  For a MODIFY the stored candidate's window is
checked first, so editing cannot revive an expired suggestion; the edited
operations are then held to the same rule.  Replay of an already committed
response does not re-apply the rule; the ledger remains the record of what
was accepted.

Shift labels render site-local minutes.  When an interval crosses a UTC
offset change, or an endpoint's wall time is repeated in the site zone (a
daylight-saving fall-back), each endpoint carries its own ``UTC±HH:MM``
suffix so two different instants never read as the same clock time.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import (
    AddOperation,
    Assignment,
    RemoveOperation,
    StaffingError,
    Worker,
)
from .time import local_wall_instants

ALIAS_TOKEN_PATTERN = re.compile(r"(?<![0-9A-Za-z_])(?:worker|assignment)_[0-9a-f]{24}(?![0-9A-Za-z_])")


def _zone(site_timezone: object) -> ZoneInfo:
    if type(site_timezone) is not str:
        raise StaffingError("staffing_invalid_evidence", "site_timezone")
    try:
        return ZoneInfo(site_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise StaffingError("staffing_invalid_evidence", "site_timezone") from None


def _is_repeated_wall_time(local: datetime) -> bool:
    """True when the site-local wall minute names two instants (fall-back)."""

    zone = local.tzinfo
    if not isinstance(zone, ZoneInfo):
        raise StaffingError("staffing_invalid_evidence", "site zone")
    return len(local_wall_instants(local.replace(tzinfo=None), zone)) > 1


def _offset_suffix(local: datetime) -> str:
    offset = local.utcoffset()
    if offset is None:
        raise StaffingError("staffing_invalid_evidence", "site offset")
    total = int(offset.total_seconds())
    if total % 60:
        raise StaffingError("staffing_invalid_evidence", "site offset")
    sign = "-" if total < 0 else "+"
    minutes = abs(total) // 60
    return f" UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _local_clock(local: datetime, *, with_offset: bool) -> str:
    text = f"{local.hour:02d}:{local.minute:02d}"
    return text + (_offset_suffix(local) if with_offset else "")


def assignment_label(
    assignment: Assignment, display_name: str, site_timezone: str
) -> str:
    """Render one shift as ``name (ROLE/AREA HH:MM–HH:MM)`` in site time.

    Each endpoint carries a ``UTC±HH:MM`` suffix when the interval crosses a
    UTC offset change or when either wall time is repeated in the site zone,
    so a fall-back hour never reads as ``01:30–01:30``.
    """

    zone = _zone(site_timezone)
    start = assignment.start_at.astimezone(zone)
    end = assignment.end_at.astimezone(zone)
    with_offset = (
        start.utcoffset() != end.utcoffset()
        or _is_repeated_wall_time(start)
        or _is_repeated_wall_time(end)
    )
    start_text = _local_clock(start, with_offset=with_offset)
    end_text = _local_clock(end, with_offset=with_offset)
    if start.date() == end.date():
        window = f"{start_text}–{end_text}"
    else:
        window = (
            f"{start.date().isoformat()} {start_text}–"
            f"{end.date().isoformat()} {end_text}"
        )
    return (
        f"{display_name} ({assignment.role_code}/{assignment.area_code} {window})"
    )


def local_alias_labels(
    *,
    workers: Sequence[Worker],
    assignments: Sequence[Assignment],
    worker_alias_to_staff_id: Sequence[tuple[str, str]],
    assignment_alias_to_assignment_id: Sequence[tuple[str, str]],
    site_timezone: str,
) -> dict[str, str]:
    """Map every reserved alias to the local label a supervisor can read."""

    names = {worker.staff_id: worker.display_name for worker in workers}
    by_id = {item.assignment_id: item for item in assignments}
    labels: dict[str, str] = {}
    for alias, staff_id in worker_alias_to_staff_id:
        name = names.get(staff_id)
        if name is not None:
            labels[alias] = name
    for alias, assignment_id in assignment_alias_to_assignment_id:
        assignment = by_id.get(assignment_id)
        if assignment is None:
            continue
        name = names.get(assignment.staff_id)
        if name is None:
            continue
        labels[alias] = assignment_label(assignment, name, site_timezone)
    return labels


def render_local_text(text: str, labels: Mapping[str, str]) -> str:
    """Replace reserved alias tokens; an unknown alias is left as written."""

    if type(text) is not str:
        raise StaffingError("staffing_invalid_evidence", "provider text")
    return ALIAS_TOKEN_PATTERN.sub(
        lambda match: labels.get(match.group(0), match.group(0)), text
    )


def end_of_service_day(service_date: date, site_timezone: str) -> datetime:
    """Local midnight after the service date, as a UTC instant."""

    if type(service_date) is not date:
        raise StaffingError("staffing_invalid_evidence", "service_date")
    zone = _zone(site_timezone)
    next_day = service_date + timedelta(days=1)
    local = datetime.combine(next_day, time(0, 0)).replace(tzinfo=zone, fold=0)
    return local.astimezone(timezone.utc)


def action_window_end(
    operations: Sequence[RemoveOperation | AddOperation],
    *,
    assignment_end_by_alias: Mapping[str, datetime],
    service_date: date,
    site_timezone: str,
) -> datetime:
    """Earliest end of any shift the candidate adds or removes, in UTC.

    Past this instant the candidate is no longer a current action, because
    at least one affected shift has ended.  A REMOVE whose alias no longer
    resolves contributes nothing; a candidate with no timed operation stays
    open until the end of its service date.
    """

    ends: list[datetime] = []
    for item in operations:
        if type(item) is AddOperation:
            ends.append(item.end_at)
        elif type(item) is RemoveOperation:
            end = assignment_end_by_alias.get(item.assignment_alias)
            if end is not None:
                ends.append(end)
        else:
            raise StaffingError("staffing_invalid_evidence", "candidate operation")
    for value in ends:
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise StaffingError("staffing_invalid_evidence", "operation end")
    if ends:
        return min(value.astimezone(timezone.utc) for value in ends)
    return end_of_service_day(service_date, site_timezone)


def is_expired(window_end: datetime, at: datetime) -> bool:
    """True once the audit instant reaches the (half-open) window end."""

    for value in (window_end, at):
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise StaffingError("staffing_invalid_evidence", "window instant")
    return at.astimezone(timezone.utc) >= window_end.astimezone(timezone.utc)


__all__ = [
    "ALIAS_TOKEN_PATTERN",
    "action_window_end",
    "assignment_label",
    "end_of_service_day",
    "is_expired",
    "local_alias_labels",
    "render_local_text",
]
