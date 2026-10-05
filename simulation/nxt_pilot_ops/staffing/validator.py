"""Pure deterministic validation for already-decoded staffing patches.

Provider wire decoding belongs to the projection boundary.  This module only
accepts closed immutable domain values, rehydrates request-scoped aliases, and
checks a proposed complete advisory schedule against a frozen staffing basis.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from ..serialization import stable_digest, to_primitive
from .contracts import (
    CODE_PATTERN,
    HEX_DIGEST_PATTERN,
    IDENTIFIER_PATTERN,
    MINUTE_PATTERN,
    AddOperation,
    Assignment,
    AssignmentRule,
    AvailabilityWindow,
    BasisSnapshot,
    CoverageGap,
    CoverageWindow,
    RemoveOperation,
    StaffingBasis,
    StaffingError,
    StaffingException,
    Worker,
)
from .time import resolve_local_minute, utc_text


REJECTION_CODES = frozenset(
    {
        "UNKNOWN_ROLE_AREA",
        "INELIGIBLE_ROLE_AREA",
        "MISSING_REQUIRED_SKILL",
        "INVALID_INTERVAL",
        "INVALID_MINUTE_PRECISION",
        "OFFSET_TIMEZONE_MISMATCH",
        "OUTSIDE_SERVICE_DATE",
        "OUTSIDE_AVAILABILITY",
        "OVERLAPS_EXCEPTION",
        "OVERLAPPING_ASSIGNMENTS",
        "MAX_DAILY_MINUTES_EXCEEDED",
        "COVERAGE_GAP",
        "PROMPT_TEMPLATE_VERSION_MISMATCH",
    }
)

def _candidate_error(detail: str) -> StaffingError:
    return StaffingError("invalid_candidate_set", detail)


def _evidence_error(detail: str) -> StaffingError:
    return StaffingError("staffing_invalid_evidence", detail)


def _safe_text(value: object, *, maximum: int) -> bool:
    return (
        type(value) is str
        and len(value) <= maximum
        and not any(
            unicodedata.category(character).startswith("C") for character in value
        )
    )


def _aware(value: object) -> bool:
    if type(value) is not datetime or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except StaffingError:
        raise
    except Exception:
        return False


def _utc_or_none(value: object) -> datetime | None:
    if not _aware(value):
        return None
    try:
        return value.astimezone(timezone.utc)
    except StaffingError:
        raise
    except Exception:
        return None


def _utc(value: object) -> datetime:
    normalized = _utc_or_none(value)
    if normalized is None:
        raise _evidence_error("invalid timestamp")
    return normalized


def _whole_minute(value: datetime) -> bool:
    return value.second == 0 and value.microsecond == 0


def _assignment_sort_key(
    item: Assignment,
) -> tuple[datetime, datetime, str, str]:
    return (item.start_at, item.end_at, item.staff_id, item.assignment_id)


def _assignment_utc_sort_key(
    item: Assignment,
) -> tuple[datetime, datetime, str, str]:
    return (_utc(item.start_at), _utc(item.end_at), item.staff_id, item.assignment_id)


def _gap_sort_key(
    item: CoverageGap,
) -> tuple[str, str, datetime, datetime, int, int]:
    return (
        item.role_code,
        item.area_code,
        _utc(item.start_at),
        _utc(item.end_at),
        item.required,
        item.actual,
    )


def _validate_operation_shape_impl(operation: object) -> None:
    if type(operation) is RemoveOperation:
        if (
            type(operation.operation) is not str
            or operation.operation != "REMOVE"
            or type(operation.assignment_alias) is not str
            or not operation.assignment_alias
        ):
            raise _candidate_error("REMOVE operation")
        return
    if type(operation) is AddOperation:
        if type(operation.operation) is not str or operation.operation != "ADD":
            raise _candidate_error("ADD operation")
        if type(operation.worker_alias) is not str or not operation.worker_alias:
            raise _candidate_error("worker alias")
        if (
            type(operation.role_code) is not str
            or CODE_PATTERN.fullmatch(operation.role_code) is None
            or type(operation.area_code) is not str
            or CODE_PATTERN.fullmatch(operation.area_code) is None
        ):
            raise _candidate_error("canonical role/area code")
        if not _aware(operation.start_at) or not _aware(operation.end_at):
            raise _candidate_error("aware ADD timestamps")
        return
    raise _candidate_error("operation type")


def _validate_operation_shape(operation: object) -> None:
    try:
        _validate_operation_shape_impl(operation)
    except StaffingError:
        raise
    except Exception:
        raise _candidate_error("operation shape") from None


@dataclass(frozen=True, slots=True)
class CandidatePatch:
    candidate_index: Literal[1, 2]
    operations: tuple[RemoveOperation | AddOperation, ...]
    rationale: str
    operational_warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.candidate_index) is not int or self.candidate_index not in (1, 2):
            raise _candidate_error("candidate_index")
        if type(self.operations) is not tuple:
            raise _candidate_error("operations")
        for operation in self.operations:
            _validate_operation_shape(operation)
        if not _safe_text(self.rationale, maximum=280):
            raise _candidate_error("rationale")
        if (
            type(self.operational_warnings) is not tuple
            or len(self.operational_warnings) > 5
            or any(
                not _safe_text(item, maximum=200)
                for item in self.operational_warnings
            )
        ):
            raise _candidate_error("operational_warnings")


def _validate_gap(gap: object) -> CoverageGap:
    if type(gap) is not CoverageGap:
        raise _evidence_error("coverage gap type")
    if (
        type(gap.role_code) is not str
        or CODE_PATTERN.fullmatch(gap.role_code) is None
        or type(gap.area_code) is not str
        or CODE_PATTERN.fullmatch(gap.area_code) is None
    ):
        raise _evidence_error("coverage gap code")
    start_utc = _utc_or_none(gap.start_at)
    end_utc = _utc_or_none(gap.end_at)
    if (
        start_utc is None
        or end_utc is None
        or not _whole_minute(gap.start_at)
        or not _whole_minute(gap.end_at)
        or end_utc <= start_utc
    ):
        raise _evidence_error("coverage gap interval")
    if (
        type(gap.required) is not int
        or gap.required < 1
        or type(gap.actual) is not int
        or gap.actual < 0
        or gap.actual >= gap.required
    ):
        raise _evidence_error("coverage gap counts")
    return gap


def _canonical_assignment_tuple(value: object) -> tuple[Assignment, ...]:
    if type(value) is not tuple or any(type(item) is not Assignment for item in value):
        raise _evidence_error("materialized schedule")
    if any(
        type(item.assignment_id) is not str
        or IDENTIFIER_PATTERN.fullmatch(item.assignment_id) is None
        or type(item.staff_id) is not str
        or IDENTIFIER_PATTERN.fullmatch(item.staff_id) is None
        for item in value
    ):
        raise _evidence_error("materialized schedule")
    if len({item.assignment_id for item in value}) != len(value):
        raise _evidence_error("duplicate assignment id")
    instant_ordered = tuple(sorted(value, key=_assignment_utc_sort_key))
    try:
        ordered = tuple(sorted(value, key=_assignment_sort_key))
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error("materialized schedule") from None
    if len(value) != len(ordered) or any(
        actual is not expected for actual, expected in zip(value, ordered)
    ):
        raise _evidence_error("noncanonical materialized schedule")
    previous_by_staff: dict[str, Assignment] = {}
    for item in instant_ordered:
        previous = previous_by_staff.get(item.staff_id)
        if previous is not None and _utc(item.start_at) < _utc(previous.end_at):
            raise _evidence_error("overlapping materialized schedule")
        previous_by_staff[item.staff_id] = item
    return value


@dataclass(frozen=True, slots=True)
class CandidateValidation:
    candidate_index: int
    valid: bool
    rejection_codes: tuple[str, ...]
    coverage_gaps: tuple[CoverageGap, ...]
    materialized_schedule: tuple[Assignment, ...] | None
    materialized_schedule_digest: str | None

    def __post_init__(self) -> None:
        if type(self.candidate_index) is not int or self.candidate_index not in (1, 2):
            raise _evidence_error("candidate_index")
        if type(self.valid) is not bool:
            raise _evidence_error("valid")
        if (
            type(self.rejection_codes) is not tuple
            or any(
                type(item) is not str or item not in REJECTION_CODES
                for item in self.rejection_codes
            )
            or self.rejection_codes
            != tuple(sorted(set(self.rejection_codes)))
        ):
            raise _evidence_error("rejection_codes")
        if type(self.coverage_gaps) is not tuple:
            raise _evidence_error("coverage_gaps")
        gaps = tuple(_validate_gap(item) for item in self.coverage_gaps)
        ordered_gaps = tuple(
            sorted(gaps, key=_gap_sort_key)
        )
        if len(gaps) != len(ordered_gaps) or any(
            actual is not expected
            for actual, expected in zip(gaps, ordered_gaps)
        ):
            raise _evidence_error("coverage gap order")

        if self.valid:
            if self.rejection_codes or gaps:
                raise _evidence_error("valid result coherence")
            schedule = _canonical_assignment_tuple(self.materialized_schedule)
            digest = self.materialized_schedule_digest
            if (
                type(digest) is not str
                or HEX_DIGEST_PATTERN.fullmatch(digest) is None
                or digest != schedule_digest(schedule)
            ):
                raise _evidence_error("materialized schedule digest")
            return

        if (
            not self.rejection_codes
            or self.materialized_schedule is not None
            or self.materialized_schedule_digest is not None
        ):
            raise _evidence_error("invalid result coherence")
        has_coverage_code = self.rejection_codes == ("COVERAGE_GAP",)
        if bool(gaps) != has_coverage_code:
            raise _evidence_error("coverage gap coherence")


def validate_candidate_patch(candidate: object) -> CandidatePatch:
    if type(candidate) is not CandidatePatch:
        raise _candidate_error("candidate type")
    if type(candidate.candidate_index) is not int or candidate.candidate_index not in (1, 2):
        raise _candidate_error("candidate_index")
    if type(candidate.operations) is not tuple:
        raise _candidate_error("operations")
    for operation in candidate.operations:
        _validate_operation_shape(operation)
    if not _safe_text(candidate.rationale, maximum=280):
        raise _candidate_error("rationale")
    if (
        type(candidate.operational_warnings) is not tuple
        or len(candidate.operational_warnings) > 5
        or any(
            not _safe_text(item, maximum=200)
            for item in candidate.operational_warnings
        )
    ):
        raise _candidate_error("operational_warnings")
    return candidate


def _basis_interval(
    start_at: object, end_at: object, field: str
) -> tuple[datetime, datetime]:
    start_utc = _utc_or_none(start_at)
    end_utc = _utc_or_none(end_at)
    if (
        start_utc is None
        or end_utc is None
        or not _whole_minute(start_at)
        or not _whole_minute(end_at)
        or end_utc <= start_utc
    ):
        raise _evidence_error(field)
    return start_utc, end_utc


def _site_date(value: datetime, zone: ZoneInfo, field: str) -> date:
    normalized = _utc(value)
    try:
        return normalized.astimezone(zone).date()
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error(field) from None


def _validate_basis_shape_impl(basis: object) -> BasisSnapshot:
    if type(basis) is not BasisSnapshot or type(basis.basis) is not StaffingBasis:
        raise _evidence_error("basis snapshot")
    state = basis.basis
    if type(state.service_date) is not date:
        raise _evidence_error("basis service date")
    if any(
        type(value) is not int or value < minimum
        for value, minimum in (
            (state.roster_revision, 1),
            (state.exception_set_revision, 0),
            (state.effective_plan_revision, 0),
        )
    ):
        raise _evidence_error("basis revisions")
    if any(
        type(value) is not str or HEX_DIGEST_PATTERN.fullmatch(value) is None
        for value in (
            state.roster_digest,
            state.exception_set_digest,
            state.effective_plan_digest,
            state.basis_digest,
        )
    ):
        raise _evidence_error("basis digests")
    if type(basis.site_timezone) is not str or not basis.site_timezone:
        raise _evidence_error("site timezone")
    try:
        zone = ZoneInfo(basis.site_timezone)
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error("site timezone") from None
    expected = (
        (basis.workers, Worker, "workers"),
        (basis.assignments, Assignment, "assignments"),
        (basis.exceptions, StaffingException, "exceptions"),
        (basis.availability, AvailabilityWindow, "availability"),
        (basis.assignment_rules, AssignmentRule, "assignment rules"),
        (basis.coverage, CoverageWindow, "coverage"),
    )
    for values, item_type, field in expected:
        if type(values) is not tuple or any(type(item) is not item_type for item in values):
            raise _evidence_error(field)
    if (
        type(basis.prompt_template_version) is not str
        or not basis.prompt_template_version
        or not _safe_text(basis.prompt_template_version, maximum=128)
    ):
        raise _evidence_error("prompt template version")

    workers: dict[str, Worker] = {}
    for worker in basis.workers:
        if (
            type(worker.staff_id) is not str
            or IDENTIFIER_PATTERN.fullmatch(worker.staff_id) is None
            or type(worker.display_name) is not str
            or not worker.display_name.strip()
            or not _safe_text(worker.display_name, maximum=100)
            or type(worker.max_daily_minutes) is not int
            or not 1 <= worker.max_daily_minutes <= 1440
            or type(worker.skill_codes) is not tuple
            or any(
                type(code) is not str or CODE_PATTERN.fullmatch(code) is None
                for code in worker.skill_codes
            )
            or worker.skill_codes != tuple(sorted(set(worker.skill_codes)))
            or type(worker.eligibility) is not tuple
        ):
            raise _evidence_error("worker")
        eligibility: list[tuple[str, str]] = []
        for pair in worker.eligibility:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or type(pair[0]) is not str
                or CODE_PATTERN.fullmatch(pair[0]) is None
                or type(pair[1]) is not str
                or CODE_PATTERN.fullmatch(pair[1]) is None
            ):
                raise _evidence_error("worker eligibility")
            eligibility.append((pair[0], pair[1]))
        if tuple(eligibility) != tuple(sorted(set(eligibility))):
            raise _evidence_error("worker eligibility")
        if worker.staff_id in workers:
            raise _evidence_error("duplicate basis worker")
        workers[worker.staff_id] = worker

    rules: dict[tuple[str, str], AssignmentRule] = {}
    for rule in basis.assignment_rules:
        key = (rule.role_code, rule.area_code)
        if (
            type(rule.role_code) is not str
            or CODE_PATTERN.fullmatch(rule.role_code) is None
            or type(rule.area_code) is not str
            or CODE_PATTERN.fullmatch(rule.area_code) is None
            or type(rule.required_skill_codes) is not tuple
            or any(
                type(code) is not str or CODE_PATTERN.fullmatch(code) is None
                for code in rule.required_skill_codes
            )
            or rule.required_skill_codes
            != tuple(sorted(set(rule.required_skill_codes)))
            or key in rules
        ):
            raise _evidence_error("assignment rule")
        rules[key] = rule
    for worker in workers.values():
        if any(pair not in rules for pair in worker.eligibility):
            raise _evidence_error("worker eligibility rule")

    availability_by_staff: dict[str, list[tuple[datetime, datetime]]] = {}
    for row in basis.availability:
        if (
            type(row.staff_id) is not str
            or row.staff_id not in workers
            or type(row.weekday) is not int
            or row.weekday != state.service_date.weekday()
            or type(row.start_local) is not str
            or MINUTE_PATTERN.fullmatch(row.start_local) is None
            or type(row.end_local) is not str
            or MINUTE_PATTERN.fullmatch(row.end_local) is None
            or row.start_local >= row.end_local
        ):
            raise _evidence_error("availability")
        try:
            start_at = resolve_local_minute(
                state.service_date, row.start_local, basis.site_timezone
            )
            end_at = resolve_local_minute(
                state.service_date, row.end_local, basis.site_timezone
            )
        except StaffingError:
            raise _evidence_error("availability interval") from None
        except Exception:
            raise _evidence_error("availability interval") from None
        start_utc, end_utc = _basis_interval(
            start_at, end_at, "availability interval"
        )
        availability_by_staff.setdefault(row.staff_id, []).append(
            (start_utc, end_utc)
        )
    for intervals in availability_by_staff.values():
        ordered = sorted(intervals)
        if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
            raise _evidence_error("overlapping availability")

    assignment_ids: set[str] = set()
    minutes_by_staff: dict[str, int] = {}
    for assignment in basis.assignments:
        key = (assignment.role_code, assignment.area_code)
        if (
            type(assignment.assignment_id) is not str
            or IDENTIFIER_PATTERN.fullmatch(assignment.assignment_id) is None
            or assignment.assignment_id in assignment_ids
            or type(assignment.staff_id) is not str
            or assignment.staff_id not in workers
            or type(assignment.role_code) is not str
            or CODE_PATTERN.fullmatch(assignment.role_code) is None
            or type(assignment.area_code) is not str
            or CODE_PATTERN.fullmatch(assignment.area_code) is None
            or key not in rules
        ):
            raise _evidence_error("assignment")
        start_utc, end_utc = _basis_interval(
            assignment.start_at, assignment.end_at, "assignment interval"
        )
        if (
            _site_date(assignment.start_at, zone, "assignment interval")
            != state.service_date
            or _site_date(assignment.end_at, zone, "assignment interval")
            != state.service_date
        ):
            raise _evidence_error("assignment service date")
        worker = workers[assignment.staff_id]
        rule = rules[key]
        if key not in worker.eligibility or not set(
            rule.required_skill_codes
        ).issubset(worker.skill_codes):
            raise _evidence_error("assignment qualification")
        if not any(
            available_start <= start_utc and end_utc <= available_end
            for available_start, available_end in availability_by_staff.get(
                assignment.staff_id, ()
            )
        ):
            raise _evidence_error("assignment availability")
        assignment_ids.add(assignment.assignment_id)
        minutes_by_staff[assignment.staff_id] = minutes_by_staff.get(
            assignment.staff_id, 0
        ) + int((end_utc - start_utc).total_seconds() // 60)
    for staff_id, minutes in minutes_by_staff.items():
        if minutes > workers[staff_id].max_daily_minutes:
            raise _evidence_error("assignment daily limit")
    _canonical_assignment_tuple(basis.assignments)

    previous_exception_by_staff: dict[str, tuple[datetime, datetime]] = {}
    ordered_exceptions: list[
        tuple[str, datetime, datetime, str, StaffingException]
    ] = []
    for exception in basis.exceptions:
        if (
            type(exception.exception_id) is not str
            or IDENTIFIER_PATTERN.fullmatch(exception.exception_id) is None
            or type(exception.service_date) is not date
            or exception.service_date != state.service_date
            or type(exception.staff_id) is not str
            or exception.staff_id not in workers
            or type(exception.kind) is not str
            or exception.kind not in {"LEAVE", "LATE", "EARLY_DEPARTURE", "UNAVAILABLE"}
            or (
                exception.note is not None
                and not _safe_text(exception.note, maximum=500)
            )
        ):
            raise _evidence_error("exception")
        start_utc, end_utc = _basis_interval(
            exception.unavailable_start,
            exception.unavailable_end,
            "exception interval",
        )
        if (
            _site_date(exception.unavailable_start, zone, "exception interval")
            != state.service_date
            or _site_date(exception.unavailable_end, zone, "exception interval")
            != state.service_date
        ):
            raise _evidence_error("exception service date")
        ordered_exceptions.append(
            (
                exception.staff_id,
                start_utc,
                end_utc,
                exception.exception_id,
                exception,
            )
        )
    sorted_exceptions = tuple(
        item[4] for item in sorted(ordered_exceptions, key=lambda item: item[:4])
    )
    if len(sorted_exceptions) != len(basis.exceptions) or any(
        actual is not expected
        for actual, expected in zip(basis.exceptions, sorted_exceptions)
    ):
        raise _evidence_error("exception order")
    for staff_id, start_utc, end_utc, _, _ in sorted(
        ordered_exceptions, key=lambda item: item[:4]
    ):
        previous = previous_exception_by_staff.get(staff_id)
        if previous is not None and start_utc < previous[1]:
            raise _evidence_error("overlapping exceptions")
        previous_exception_by_staff[staff_id] = (start_utc, end_utc)
    for assignment in basis.assignments:
        for exception in basis.exceptions:
            if assignment.staff_id == exception.staff_id and _overlaps(
                assignment.start_at,
                assignment.end_at,
                exception.unavailable_start,
                exception.unavailable_end,
            ):
                raise _evidence_error("assignment overlaps exception")

    for requirement in basis.coverage:
        key = (requirement.role_code, requirement.area_code)
        if (
            type(requirement.role_code) is not str
            or CODE_PATTERN.fullmatch(requirement.role_code) is None
            or type(requirement.area_code) is not str
            or CODE_PATTERN.fullmatch(requirement.area_code) is None
            or key not in rules
            or type(requirement.minimum_staff) is not int
            or not 1 <= requirement.minimum_staff <= 10_000
        ):
            raise _evidence_error("coverage")
        _basis_interval(
            requirement.start_at, requirement.end_at, "coverage interval"
        )
        if (
            _site_date(requirement.start_at, zone, "coverage interval")
            != state.service_date
            or _site_date(requirement.end_at, zone, "coverage interval")
            != state.service_date
        ):
            raise _evidence_error("coverage service date")

    return basis


def _validate_basis_shape(basis: object) -> BasisSnapshot:
    try:
        return _validate_basis_shape_impl(basis)
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error("basis snapshot") from None


def _validated_alias_pairs(value: object, field: str) -> tuple[tuple[str, str], ...]:
    if type(value) is not tuple:
        raise _evidence_error(field)
    pairs: list[tuple[str, str]] = []
    for item in value:
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or not item[0]
            or type(item[1]) is not str
            or not item[1]
        ):
            raise _evidence_error(field)
        pairs.append((item[0], item[1]))
    aliases = tuple(item[0] for item in pairs)
    targets = tuple(item[1] for item in pairs)
    if len(set(aliases)) != len(aliases) or len(set(targets)) != len(targets):
        raise _evidence_error(f"{field} uniqueness")
    return tuple(pairs)


def validate_alias_maps(
    basis: BasisSnapshot,
    worker_alias_to_staff_id: tuple[tuple[str, str], ...],
    assignment_alias_to_assignment_id: tuple[tuple[str, str], ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """Validate local alias evidence before performing either dict conversion."""

    basis = _validate_basis_shape(basis)
    worker_pairs = _validated_alias_pairs(
        worker_alias_to_staff_id, "worker alias map"
    )
    assignment_pairs = _validated_alias_pairs(
        assignment_alias_to_assignment_id, "assignment alias map"
    )
    worker_aliases = {item[0] for item in worker_pairs}
    assignment_aliases = {item[0] for item in assignment_pairs}
    if worker_aliases & assignment_aliases:
        raise _evidence_error("alias namespace collision")
    if tuple(item[1] for item in worker_pairs) != tuple(
        item.staff_id for item in basis.workers
    ):
        raise _evidence_error("worker alias targets")
    if tuple(item[1] for item in assignment_pairs) != tuple(
        item.assignment_id for item in basis.assignments
    ):
        raise _evidence_error("assignment alias targets")
    return dict(worker_pairs), dict(assignment_pairs)


def _canonical_schedule_snapshot(
    assignments: tuple[Assignment, ...],
) -> tuple[
    tuple[Assignment, ...],
    tuple[tuple[datetime, datetime], ...],
]:
    if type(assignments) is not tuple or any(
        type(item) is not Assignment for item in assignments
    ):
        raise _evidence_error("schedule")
    if any(
        type(item.assignment_id) is not str
        or IDENTIFIER_PATTERN.fullmatch(item.assignment_id) is None
        or type(item.staff_id) is not str
        or IDENTIFIER_PATTERN.fullmatch(item.staff_id) is None
        for item in assignments
    ):
        raise _evidence_error("schedule")
    if len({item.assignment_id for item in assignments}) != len(assignments):
        raise _evidence_error("duplicate assignment id")
    instants: dict[str, tuple[datetime, datetime]] = {}
    for item in assignments:
        instants[item.assignment_id] = (_utc(item.start_at), _utc(item.end_at))
    try:
        ordered = tuple(sorted(assignments, key=_assignment_sort_key))
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error("schedule") from None
    return ordered, tuple(instants[item.assignment_id] for item in ordered)


def canonical_schedule(assignments: tuple[Assignment, ...]) -> tuple[Assignment, ...]:
    ordered, _ = _canonical_schedule_snapshot(assignments)
    return ordered


def schedule_digest(assignments: tuple[Assignment, ...]) -> str:
    try:
        ordered, instants = _canonical_schedule_snapshot(assignments)
        normalized = tuple(
            {
                "assignment_id": item.assignment_id,
                "staff_id": item.staff_id,
                "role_code": item.role_code,
                "area_code": item.area_code,
                "start_at": start_at,
                "end_at": end_at,
            }
            for item, (start_at, end_at) in zip(ordered, instants)
        )
        return stable_digest(to_primitive(normalized))
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error("schedule digest") from None


def assignment_from_candidate(
    operation: AddOperation, basis: BasisSnapshot, staff_id: str
) -> Assignment:
    _validate_basis_shape(basis)
    _validate_operation_shape(operation)
    if (
        type(staff_id) is not str
        or IDENTIFIER_PATTERN.fullmatch(staff_id) is None
        or staff_id not in {item.staff_id for item in basis.workers}
    ):
        raise _candidate_error("staff_id")
    start_utc = _utc_or_none(operation.start_at)
    end_utc = _utc_or_none(operation.end_at)
    if start_utc is None or end_utc is None or end_utc <= start_utc:
        raise _candidate_error("candidate assignment interval")
    assignment_id = "assignment_" + stable_digest(
        {
            "schema": "nxt-staffing-candidate-assignment-id/v1",
            "basis_digest": basis.basis.basis_digest,
            "service_date": basis.basis.service_date,
            "staff_id": staff_id,
            "role_code": operation.role_code,
            "area_code": operation.area_code,
            "start_at": utc_text(start_utc),
            "end_at": utc_text(end_utc),
        }
    )[:24]
    return Assignment(
        assignment_id,
        staff_id,
        operation.role_code,
        operation.area_code,
        start_utc,
        end_utc,
    )


def in_site_zone(value: datetime, timezone_name: str) -> bool:
    try:
        if not _aware(value) or type(timezone_name) is not str:
            return False
        zone = ZoneInfo(timezone_name)
        normalized = _utc_or_none(value)
        if normalized is None:
            return False
        local = normalized.astimezone(zone)
        return (
            local.utcoffset() == value.utcoffset()
            and local.replace(tzinfo=None) == value.replace(tzinfo=None)
        )
    except StaffingError:
        raise
    except Exception:
        return False


def basis_knows_role_area(
    basis: BasisSnapshot, role_code: str, area_code: str
) -> bool:
    return any(
        item.role_code == role_code and item.area_code == area_code
        for item in basis.assignment_rules
    )


def basis_is_eligible(
    basis: BasisSnapshot, staff_id: str, role_code: str, area_code: str
) -> bool:
    worker = next(
        (item for item in basis.workers if item.staff_id == staff_id), None
    )
    return worker is not None and (role_code, area_code) in worker.eligibility


def basis_has_required_skills(
    basis: BasisSnapshot, staff_id: str, role_code: str, area_code: str
) -> bool:
    worker = next(
        (item for item in basis.workers if item.staff_id == staff_id), None
    )
    rule = next(
        (
            item
            for item in basis.assignment_rules
            if item.role_code == role_code and item.area_code == area_code
        ),
        None,
    )
    return (
        worker is not None
        and rule is not None
        and set(rule.required_skill_codes).issubset(worker.skill_codes)
    )


def basis_is_available(
    basis: BasisSnapshot, operation: AddOperation, staff_id: str
) -> bool:
    for row in basis.availability:
        if (
            row.staff_id != staff_id
            or row.weekday != basis.basis.service_date.weekday()
        ):
            continue
        try:
            start = resolve_local_minute(
                basis.basis.service_date, row.start_local, basis.site_timezone
            )
            end = resolve_local_minute(
                basis.basis.service_date, row.end_local, basis.site_timezone
            )
        except StaffingError:
            continue
        if _utc(start) <= _utc(operation.start_at) and _utc(operation.end_at) <= _utc(end):
            return True
    return False


def _overlaps(
    left_start: datetime,
    left_end: datetime,
    right_start: datetime,
    right_end: datetime,
) -> bool:
    return _utc(left_start) < _utc(right_end) and _utc(right_start) < _utc(left_end)


def basis_overlaps_exception(
    basis: BasisSnapshot, staff_id: str, operation: AddOperation
) -> bool:
    return any(
        item.staff_id == staff_id
        and _overlaps(
            operation.start_at,
            operation.end_at,
            item.unavailable_start,
            item.unavailable_end,
        )
        for item in basis.exceptions
    )


def basis_overlaps_existing(
    staff_id: str,
    operation: AddOperation,
    working: dict[str, Assignment],
) -> bool:
    return any(
        item.staff_id == staff_id
        and _overlaps(
            operation.start_at, operation.end_at, item.start_at, item.end_at
        )
        for item in working.values()
    )


def _elapsed_minutes(start_at: datetime, end_at: datetime) -> int:
    return int((_utc(end_at) - _utc(start_at)).total_seconds() // 60)


def basis_exceeds_daily_limit(
    basis: BasisSnapshot,
    staff_id: str,
    operation: AddOperation,
    working: dict[str, Assignment],
) -> bool:
    worker = next(
        (item for item in basis.workers if item.staff_id == staff_id), None
    )
    if worker is None:
        return True
    current = sum(
        _elapsed_minutes(item.start_at, item.end_at)
        for item in working.values()
        if item.staff_id == staff_id
    )
    return current + _elapsed_minutes(
        operation.start_at, operation.end_at
    ) > worker.max_daily_minutes


def validate_removes(
    removes: tuple[RemoveOperation, ...],
    assignment_ids: dict[str, str],
    baseline: dict[str, Assignment],
) -> None:
    aliases: set[str] = set()
    for operation in removes:
        alias = operation.assignment_alias
        if alias in aliases:
            raise _candidate_error("duplicate REMOVE")
        aliases.add(alias)
        target = assignment_ids.get(alias)
        if target is None or target not in baseline:
            raise _candidate_error("unknown assignment alias")


def _validate_candidate_alias_references(
    candidate: CandidatePatch,
    worker_ids: dict[str, str],
    assignment_ids: dict[str, str],
    baseline: dict[str, Assignment],
) -> tuple[tuple[RemoveOperation, ...], tuple[AddOperation, ...]]:
    removes = tuple(
        item for item in candidate.operations if type(item) is RemoveOperation
    )
    adds = tuple(item for item in candidate.operations if type(item) is AddOperation)
    validate_removes(removes, assignment_ids, baseline)
    if any(item.worker_alias not in worker_ids for item in adds):
        raise _candidate_error("unknown worker alias")
    return removes, adds


def validate_adds(
    adds: tuple[AddOperation, ...],
    worker_ids: dict[str, str],
    remaining: dict[str, Assignment],
    basis: BasisSnapshot,
) -> tuple[list[str], tuple[Assignment, ...]]:
    codes: list[str] = []
    accepted: list[Assignment] = []
    working = dict(remaining)
    try:
        site_zone = ZoneInfo(basis.site_timezone)
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error("site timezone") from None

    for operation in adds:
        staff_id = worker_ids.get(operation.worker_alias)
        if staff_id is None:
            raise _candidate_error("unknown worker alias")
        if not _whole_minute(operation.start_at) or not _whole_minute(operation.end_at):
            codes.append("INVALID_MINUTE_PRECISION")
            continue
        start_utc = _utc_or_none(operation.start_at)
        end_utc = _utc_or_none(operation.end_at)
        if start_utc is None or end_utc is None:
            codes.append("OFFSET_TIMEZONE_MISMATCH")
            continue
        if end_utc <= start_utc:
            codes.append("INVALID_INTERVAL")
            continue
        if not in_site_zone(operation.start_at, basis.site_timezone) or not in_site_zone(
            operation.end_at, basis.site_timezone
        ):
            codes.append("OFFSET_TIMEZONE_MISMATCH")
            continue
        try:
            start_date = start_utc.astimezone(site_zone).date()
            end_date = end_utc.astimezone(site_zone).date()
        except StaffingError:
            raise
        except Exception:
            codes.append("OFFSET_TIMEZONE_MISMATCH")
            continue
        if start_date != basis.basis.service_date or end_date != basis.basis.service_date:
            codes.append("OUTSIDE_SERVICE_DATE")
            continue
        if not basis_knows_role_area(
            basis, operation.role_code, operation.area_code
        ):
            codes.append("UNKNOWN_ROLE_AREA")
            continue
        if not basis_is_eligible(
            basis, staff_id, operation.role_code, operation.area_code
        ):
            codes.append("INELIGIBLE_ROLE_AREA")
            continue
        if not basis_has_required_skills(
            basis, staff_id, operation.role_code, operation.area_code
        ):
            codes.append("MISSING_REQUIRED_SKILL")
            continue
        if not basis_is_available(basis, operation, staff_id):
            codes.append("OUTSIDE_AVAILABILITY")
            continue
        if basis_overlaps_exception(basis, staff_id, operation):
            codes.append("OVERLAPS_EXCEPTION")
            continue
        if basis_overlaps_existing(staff_id, operation, working):
            codes.append("OVERLAPPING_ASSIGNMENTS")
            continue
        if basis_exceeds_daily_limit(basis, staff_id, operation, working):
            codes.append("MAX_DAILY_MINUTES_EXCEEDED")
            continue
        assignment = assignment_from_candidate(operation, basis, staff_id)
        accepted.append(assignment)
        working[assignment.assignment_id] = assignment
    return codes, tuple(accepted)


def apply_patch_operations(
    remaining: dict[str, Assignment], accepted_adds: tuple[Assignment, ...]
) -> tuple[Assignment, ...]:
    result = dict(remaining)
    for item in accepted_adds:
        result[item.assignment_id] = item
    return canonical_schedule(tuple(result.values()))


def _boundary_interval(item: object) -> tuple[datetime, datetime] | None:
    if type(item) in (Assignment, CoverageWindow):
        return item.start_at, item.end_at
    if type(item) is StaffingException:
        return item.unavailable_start, item.unavailable_end
    return None


def coverage_gaps(
    schedule: tuple[Assignment, ...], basis: BasisSnapshot
) -> tuple[CoverageGap, ...]:
    if type(schedule) is not tuple or any(
        type(item) is not Assignment for item in schedule
    ):
        raise _evidence_error("coverage schedule")
    _validate_basis_shape(basis)
    schedule_intervals: list[tuple[Assignment, datetime, datetime]] = []
    for item in schedule:
        if (
            type(item.staff_id) is not str
            or type(item.role_code) is not str
            or CODE_PATTERN.fullmatch(item.role_code) is None
            or type(item.area_code) is not str
            or CODE_PATTERN.fullmatch(item.area_code) is None
        ):
            raise _evidence_error("coverage schedule")
        start_utc, end_utc = _basis_interval(
            item.start_at, item.end_at, "coverage schedule interval"
        )
        schedule_intervals.append((item, start_utc, end_utc))
    gaps: list[CoverageGap] = []
    boundary_sources: tuple[object, ...] = (
        tuple(schedule) + tuple(basis.exceptions) + tuple(basis.coverage)
    )
    for requirement in basis.coverage:
        requirement_start = _utc(requirement.start_at)
        requirement_end = _utc(requirement.end_at)
        boundaries = {requirement_start, requirement_end}
        for item in boundary_sources:
            interval = _boundary_interval(item)
            if interval is None:
                continue
            item_start, item_end = interval
            item_start_utc = _utc(item_start)
            item_end_utc = _utc(item_end)
            if (
                item_start_utc < requirement_end
                and requirement_start < item_end_utc
            ):
                boundaries.add(max(requirement_start, item_start_utc))
                boundaries.add(min(requirement_end, item_end_utc))
        ordered = tuple(sorted(boundaries))
        for start_at, end_at in zip(ordered, ordered[1:]):
            if end_at <= start_at:
                continue
            actual = len(
                {
                    item.staff_id
                    for item, item_start, item_end in schedule_intervals
                    if item.role_code == requirement.role_code
                    and item.area_code == requirement.area_code
                    and item_start <= start_at
                    and end_at <= item_end
                }
            )
            if actual < requirement.minimum_staff:
                gaps.append(
                    CoverageGap(
                        requirement.role_code,
                        requirement.area_code,
                        start_at,
                        end_at,
                        requirement.minimum_staff,
                        actual,
                    )
                )
    return tuple(
        sorted(gaps, key=_gap_sort_key)
    )


def validate_candidate(
    basis: BasisSnapshot,
    candidate: CandidatePatch,
    *,
    worker_alias_to_staff_id: tuple[tuple[str, str], ...],
    assignment_alias_to_assignment_id: tuple[tuple[str, str], ...],
    prompt_template_version: str,
) -> CandidateValidation:
    worker_ids, assignment_ids = validate_alias_maps(
        basis,
        worker_alias_to_staff_id,
        assignment_alias_to_assignment_id,
    )
    candidate = validate_candidate_patch(candidate)
    baseline = {item.assignment_id: item for item in basis.assignments}
    removes, adds = _validate_candidate_alias_references(
        candidate, worker_ids, assignment_ids, baseline
    )
    if prompt_template_version != basis.prompt_template_version:
        return CandidateValidation(
            candidate.candidate_index,
            False,
            ("PROMPT_TEMPLATE_VERSION_MISMATCH",),
            (),
            None,
            None,
        )

    removed_ids = {assignment_ids[item.assignment_alias] for item in removes}
    remaining = {
        key: value for key, value in baseline.items() if key not in removed_ids
    }
    codes, accepted = validate_adds(adds, worker_ids, remaining, basis)
    if codes:
        return CandidateValidation(
            candidate.candidate_index,
            False,
            tuple(sorted(set(codes))),
            (),
            None,
            None,
        )
    schedule = apply_patch_operations(remaining, accepted)
    gaps = coverage_gaps(schedule, basis)
    if gaps:
        return CandidateValidation(
            candidate.candidate_index,
            False,
            ("COVERAGE_GAP",),
            gaps,
            None,
            None,
        )
    return CandidateValidation(
        candidate.candidate_index,
        True,
        (),
        (),
        schedule,
        schedule_digest(schedule),
    )


def _candidate_tuple(value: object) -> tuple[CandidatePatch, ...]:
    if type(value) is not tuple or any(type(item) is not CandidatePatch for item in value):
        raise _candidate_error("candidate tuple")
    for item in value:
        validate_candidate_patch(item)
    return value


def validate_candidates(
    basis: BasisSnapshot,
    candidates: tuple[CandidatePatch, ...],
    *,
    worker_alias_to_staff_id: tuple[tuple[str, str], ...],
    assignment_alias_to_assignment_id: tuple[tuple[str, str], ...],
    prompt_template_version: str,
) -> tuple[CandidateValidation, ...]:
    validate_alias_maps(
        basis,
        worker_alias_to_staff_id,
        assignment_alias_to_assignment_id,
    )
    candidates = _candidate_tuple(candidates)
    indexes = tuple(item.candidate_index for item in candidates)
    if indexes not in ((), (1,), (1, 2)):
        raise _candidate_error("indices must be contiguous and ordered")
    return tuple(
        validate_candidate(
            basis,
            candidate,
            worker_alias_to_staff_id=worker_alias_to_staff_id,
            assignment_alias_to_assignment_id=assignment_alias_to_assignment_id,
            prompt_template_version=prompt_template_version,
        )
        for candidate in candidates
    )
