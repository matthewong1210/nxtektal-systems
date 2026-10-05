"""Strict weekly roster import and deterministic service-day materialization."""

from __future__ import annotations

from datetime import date
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import (
    Assignment,
    AssignmentRule,
    AvailabilityWindow,
    CoverageRequirement,
    RosterRevision,
    ServiceDayRoster,
    StaffingError,
    WeeklyAssignment,
    Worker,
    require_exact_object,
    validate_human_text,
    validate_identifier,
)
from .time import resolve_local_minute


ROSTER_KEYS = frozenset(
    {
        "schema",
        "request_id",
        "expected_roster_revision",
        "site_id",
        "deployment_id",
        "site_timezone",
        "effective_from_local_date",
        "effective_until_local_date",
        "operator",
        "source_ref",
        "workers",
        "availability",
        "assignment_rules",
        "regular_assignments",
        "coverage",
    }
)


def parse_worker(value: object) -> Worker:
    return Worker.from_mapping(value)


def parse_availability(value: object) -> AvailabilityWindow:
    return AvailabilityWindow.from_mapping(value)


def parse_regular_assignment(value: object) -> WeeklyAssignment:
    return WeeklyAssignment.from_mapping(value)


def parse_assignment_rule(value: object) -> AssignmentRule:
    return AssignmentRule.from_mapping(value)


def parse_coverage(value: object) -> CoverageRequirement:
    return CoverageRequirement.from_mapping(value)


def _closed_array(value: object, field: str) -> list[object]:
    if type(value) is not list:
        raise StaffingError("staffing_invalid_roster", field)
    return list(value)


def _require_unique(values: Sequence[object], field: str) -> None:
    if len(set(values)) != len(values):
        raise StaffingError("staffing_invalid_roster", field)


def _validate_interval(start: str, end: str, field: str) -> None:
    if start >= end:
        raise StaffingError("staffing_invalid_roster", field)


def _validate_nonoverlap(
    rows: Sequence[tuple[object, ...]],
    *,
    grouping_length: int,
    field: str,
) -> None:
    ordered = sorted(rows)
    previous: tuple[object, ...] | None = None
    for row in ordered:
        if previous is not None and row[:grouping_length] == previous[:grouping_length]:
            previous_end = previous[grouping_length + 1]
            current_start = row[grouping_length]
            if current_start < previous_end:
                raise StaffingError("staffing_invalid_roster", field)
        previous = row


def validate_cross_references(
    workers: tuple[Worker, ...],
    availability: tuple[AvailabilityWindow, ...],
    assignments: tuple[WeeklyAssignment, ...],
    rules: tuple[AssignmentRule, ...],
    coverage: tuple[CoverageRequirement, ...],
) -> None:
    if not workers:
        raise StaffingError("staffing_invalid_roster", "workers")
    if not coverage:
        raise StaffingError("staffing_invalid_roster", "coverage")

    worker_by_id = {item.staff_id: item for item in workers}
    if len(worker_by_id) != len(workers):
        raise StaffingError("staffing_invalid_roster", "duplicate staff_id")
    rule_by_key = {(item.role_code, item.area_code): item for item in rules}
    if len(rule_by_key) != len(rules):
        raise StaffingError("staffing_invalid_roster", "duplicate assignment rule")

    for worker in workers:
        for eligible in worker.eligibility:
            if eligible not in rule_by_key:
                raise StaffingError("staffing_invalid_roster", "unknown eligibility rule")

    availability_keys: list[tuple[str, int, str, str]] = []
    availability_by_worker_day: dict[tuple[str, int], list[AvailabilityWindow]] = {}
    for row in availability:
        if row.staff_id not in worker_by_id:
            raise StaffingError("staffing_invalid_roster", "unknown availability staff")
        _validate_interval(row.start_local, row.end_local, "availability interval")
        key = (row.staff_id, row.weekday, row.start_local, row.end_local)
        availability_keys.append(key)
        availability_by_worker_day.setdefault((row.staff_id, row.weekday), []).append(row)
    _require_unique(availability_keys, "duplicate availability")
    _validate_nonoverlap(
        availability_keys, grouping_length=2, field="overlapping availability"
    )

    assignment_days: list[tuple[str, int]] = []
    assignment_values: list[tuple[str, int, str, str, str, str]] = []
    for row in assignments:
        worker = worker_by_id.get(row.staff_id)
        if worker is None:
            raise StaffingError("staffing_invalid_roster", "unknown assignment staff")
        _validate_interval(row.start_local, row.end_local, "assignment interval")
        key = (row.role_code, row.area_code)
        rule = rule_by_key.get(key)
        if rule is None:
            raise StaffingError("staffing_invalid_roster", "unknown assignment rule")
        if key not in worker.eligibility:
            raise StaffingError("staffing_invalid_roster", "ineligible baseline assignment")
        if not set(rule.required_skill_codes).issubset(worker.skill_codes):
            raise StaffingError("staffing_invalid_roster", "missing required skill")
        windows = availability_by_worker_day.get((row.staff_id, row.weekday), ())
        if not any(
            window.start_local <= row.start_local and row.end_local <= window.end_local
            for window in windows
        ):
            raise StaffingError("staffing_invalid_roster", "assignment outside availability")
        start_hour, start_minute = (int(item) for item in row.start_local.split(":"))
        end_hour, end_minute = (int(item) for item in row.end_local.split(":"))
        duration = (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute)
        if duration > worker.max_daily_minutes:
            raise StaffingError("staffing_invalid_roster", "max_daily_minutes")
        assignment_days.append((row.staff_id, row.weekday))
        assignment_values.append(
            (
                row.staff_id,
                row.weekday,
                row.role_code,
                row.area_code,
                row.start_local,
                row.end_local,
            )
        )
    _require_unique(assignment_days, "multiple shifts per weekday")
    _require_unique(assignment_values, "duplicate regular assignment")

    coverage_values: list[tuple[int, str, str, str, str, int]] = []
    coverage_intervals: list[tuple[object, ...]] = []
    for row in coverage:
        _validate_interval(row.start_local, row.end_local, "coverage interval")
        if (row.role_code, row.area_code) not in rule_by_key:
            raise StaffingError("staffing_invalid_roster", "unknown coverage rule")
        value = (
            row.weekday,
            row.role_code,
            row.area_code,
            row.start_local,
            row.end_local,
            row.minimum_staff,
        )
        coverage_values.append(value)
        coverage_intervals.append(value)
    _require_unique(coverage_values, "duplicate coverage")
    _validate_nonoverlap(
        coverage_intervals, grouping_length=3, field="overlapping coverage"
    )


def validate_roster_import(
    payload: object,
    *,
    site_id: str,
    deployment_id: str,
    site_timezone: str,
) -> RosterRevision:
    body = require_exact_object(payload, ROSTER_KEYS)
    if body["schema"] != "nxt-staffing-roster-import/v1":
        raise StaffingError("staffing_invalid_roster", "schema")
    validate_identifier(body["request_id"], "request_id")
    expected = body["expected_roster_revision"]
    if type(expected) is not int or expected < 0:
        raise StaffingError("staffing_invalid_roster", "expected_roster_revision")
    if body["site_id"] != site_id or body["deployment_id"] != deployment_id:
        raise StaffingError("staffing_identity_mismatch", "site/deployment")
    if body["site_timezone"] != site_timezone:
        raise StaffingError("staffing_invalid_roster", "site_timezone")
    if type(site_timezone) is not str:
        raise StaffingError("staffing_invalid_roster", "site_timezone")
    try:
        ZoneInfo(site_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise StaffingError("staffing_invalid_roster", "site_timezone") from None
    validate_human_text(body["operator"], "operator", maximum=128, formula_safe=True)
    validate_human_text(body["source_ref"], "source_ref", maximum=256, formula_safe=True)

    workers = tuple(parse_worker(item) for item in _closed_array(body["workers"], "workers"))
    availability = tuple(
        parse_availability(item)
        for item in _closed_array(body["availability"], "availability")
    )
    rules = tuple(
        parse_assignment_rule(item)
        for item in _closed_array(body["assignment_rules"], "assignment_rules")
    )
    assignments = tuple(
        parse_regular_assignment(item)
        for item in _closed_array(body["regular_assignments"], "regular_assignments")
    )
    coverage = tuple(parse_coverage(item) for item in _closed_array(body["coverage"], "coverage"))
    validate_cross_references(workers, availability, assignments, rules, coverage)
    return RosterRevision.from_normalized(
        body, workers, availability, rules, assignments, coverage
    )


def select_effective_roster(
    revisions: Sequence[RosterRevision], service_date: date
) -> RosterRevision:
    if type(service_date) is not date:
        raise StaffingError("staffing_invalid_roster", "service_date")
    applicable = tuple(item for item in tuple(revisions) if item.applies_to(service_date))
    if not applicable:
        raise StaffingError("staffing_roster_not_found", service_date.isoformat())
    return max(applicable, key=lambda item: item.revision)


def materialize_service_day(
    revision: RosterRevision, service_date: date
) -> ServiceDayRoster:
    if type(service_date) is not date:
        raise StaffingError("staffing_invalid_roster", "service_date")
    if not revision.applies_to(service_date):
        raise StaffingError("staffing_roster_not_found", service_date.isoformat())
    rows: list[Assignment] = []
    for row in revision.regular_assignments:
        if row.weekday != service_date.weekday():
            continue
        start = resolve_local_minute(service_date, row.start_local, revision.site_timezone)
        end = resolve_local_minute(service_date, row.end_local, revision.site_timezone)
        if end <= start:
            raise StaffingError("staffing_invalid_roster", "reversed or cross-midnight shift")
        rows.append(Assignment.from_weekly_row(revision, service_date, row, start, end))
    return ServiceDayRoster.build(revision, service_date, tuple(rows))
