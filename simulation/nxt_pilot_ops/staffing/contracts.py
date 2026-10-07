"""Frozen, provider-neutral contracts for staffing advisory evidence.

This module owns only closed values and deterministic validation helpers.  It
does not read a clock, create identifiers, perform I/O, or import a model
provider.  Values crossing an untrusted boundary are constructed by the
strict parsers at the bottom of the module.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, fields
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..serialization import stable_digest


CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
HEX_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MINUTE_PATTERN = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
ASCII_TOKEN_PATTERN = re.compile(r"^[\x20-\x7e]+$")

PROMPT_TEMPLATE_VERSION = "staffing-adjustment/v2"
SUPPORTED_PROMPT_TEMPLATE_VERSIONS = frozenset(
    {"staffing-adjustment/v1", "staffing-adjustment/v2"}
)

EVENT_TYPES = frozenset(
    {
        "roster_imported",
        "exception_recorded",
        "exception_cancelled",
        "exception_corrected",
        "generation_reserved",
        "generation_interrupted",
        "provider_attempt_started",
        "provider_attempt_finished",
        "suggestion_issued",
        "suggestion_unavailable",
        "manager_response_committed",
    }
)
OPERATION_KINDS = frozenset(
    {
        "roster-import",
        "exception-record",
        "exception-cancel",
        "exception-correct",
        "suggestion-generate",
        "manager-response",
    }
)
GENERATION_TERMINALS = frozenset(
    {
        "SUCCEEDED",
        "NO_VALID_SUGGESTION",
        "UNAVAILABLE",
        "REFUSED",
        "INVALID_RESPONSE",
        "PROVIDER_ERROR",
        "CONFIGURATION_ERROR",
        "SECURITY_ERROR",
        "RESULT_UNKNOWN",
    }
)
MANAGER_REASON_CODES = frozenset(
    {
        "APPROVED",
        "APPROVED_WITH_CHANGES",
        "MANUAL_HANDLING",
        "INSUFFICIENT_CONTEXT",
        "OTHER",
    }
)


class StaffingError(ValueError):
    """A transport-independent staffing failure with a stable public code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class ExceptionKind(StrEnum):
    LEAVE = "LEAVE"
    LATE = "LATE"
    EARLY_DEPARTURE = "EARLY_DEPARTURE"
    UNAVAILABLE = "UNAVAILABLE"


class PatchKind(StrEnum):
    REMOVE = "REMOVE"
    ADD = "ADD"


def _detach_tuple_data(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return tuple(_detach_tuple_data(item) for item in value)
    if isinstance(value, (dict, set, frozenset)):
        raise StaffingError("staffing_invalid_evidence", "closed contract value")
    return value


class _FrozenContract:
    """Detach every list/tuple recursively before a frozen record is exposed."""

    __slots__ = ()

    def __post_init__(self) -> None:
        for item in fields(self):
            detached = _detach_tuple_data(getattr(self, item.name))
            if detached is not getattr(self, item.name):
                object.__setattr__(self, item.name, detached)


def require_exact_object(
    value: object,
    keys: set[str] | frozenset[str],
    *,
    code: str = "staffing_unknown_field",
    detail: str = "exact key set required",
) -> dict[str, object]:
    """Copy an exact plain JSON object without accepting mapping subclasses."""

    if type(value) is not dict or set(value) != set(keys):
        raise StaffingError(code, detail)
    return dict(value)


def _contains_unsafe_scalar(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def validate_human_text(
    value: object,
    field: str,
    *,
    minimum: int = 1,
    maximum: int,
    formula_safe: bool = False,
) -> str:
    if (
        type(value) is not str
        or not minimum <= len(value) <= maximum
        or (minimum > 0 and not value.strip())
    ):
        raise StaffingError("staffing_invalid_roster", field)
    if _contains_unsafe_scalar(value):
        raise StaffingError("staffing_invalid_roster", field)
    if formula_safe and value.lstrip().startswith(("=", "+", "-", "@")):
        raise StaffingError("staffing_invalid_roster", field)
    return value


def validate_identifier(value: object, field: str) -> str:
    if type(value) is not str or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise StaffingError("staffing_invalid_roster", field)
    return value


def validate_code(value: object, field: str) -> str:
    if type(value) is not str or CODE_PATTERN.fullmatch(value) is None:
        raise StaffingError("staffing_invalid_roster", field)
    return value


def validate_weekday(value: object, field: str = "weekday") -> int:
    if type(value) is not int or not 0 <= value <= 6:
        raise StaffingError("staffing_invalid_roster", field)
    return value


def validate_local_minute(value: object, field: str) -> str:
    if type(value) is not str or MINUTE_PATTERN.fullmatch(value) is None:
        raise StaffingError(
            "staffing_invalid_roster", "minute must be HH:MM within service date"
        )
    return value


def parse_local_date(value: object, field: str) -> date:
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise StaffingError("staffing_invalid_roster", field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise StaffingError("staffing_invalid_roster", field) from None
    if parsed.isoformat() != value:
        raise StaffingError("staffing_invalid_roster", field)
    return parsed


def _validate_digest(value: object, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str or HEX_DIGEST_PATTERN.fullmatch(value) is None:
        raise StaffingError("staffing_invalid_evidence", field)
    return value


def _validate_ascii_text(
    value: object,
    field: str,
    *,
    maximum: int,
    optional: bool = False,
) -> str | None:
    if optional and value is None:
        return None
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or ASCII_TOKEN_PATTERN.fullmatch(value) is None
        or not value.strip()
    ):
        raise StaffingError("staffing_invalid_evidence", field)
    return value


def _validate_failure_code(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) is None:
        raise StaffingError("staffing_invalid_evidence", "failure_code")
    return value


def _validate_nonnegative_int(value: object, field: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise StaffingError("staffing_invalid_evidence", field)
    return value


def _validate_positive_number(value: object, field: str) -> float:
    if type(value) not in (int, float):
        raise StaffingError("staffing_invalid_evidence", field)
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise StaffingError("staffing_invalid_evidence", field)
    return number


def _validate_plain_text(
    value: object,
    field: str,
    *,
    maximum: int,
    minimum: int = 1,
    optional: bool = False,
) -> str | None:
    if optional and value is None:
        return None
    if (
        type(value) is not str
        or not minimum <= len(value) <= maximum
        or _contains_unsafe_scalar(value)
    ):
        raise StaffingError("staffing_invalid_evidence", field)
    return value


@dataclass(frozen=True, slots=True)
class Worker(_FrozenContract):
    staff_id: str
    display_name: str
    skill_codes: tuple[str, ...]
    eligibility: tuple[tuple[str, str], ...]
    max_daily_minutes: int

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        validate_identifier(self.staff_id, "staff_id")
        validate_human_text(
            self.display_name, "display_name", maximum=100, formula_safe=True
        )
        if type(self.skill_codes) is not tuple:
            raise StaffingError("staffing_invalid_roster", "skill_codes")
        skills = tuple(validate_code(item, "skill_codes") for item in self.skill_codes)
        if len(set(skills)) != len(skills):
            raise StaffingError("staffing_invalid_roster", "duplicate skill_codes")
        pairs: list[tuple[str, str]] = []
        if type(self.eligibility) is not tuple:
            raise StaffingError("staffing_invalid_roster", "eligibility")
        for item in self.eligibility:
            if type(item) is not tuple or len(item) != 2:
                raise StaffingError("staffing_invalid_roster", "eligibility")
            pairs.append(
                (validate_code(item[0], "role_code"), validate_code(item[1], "area_code"))
            )
        if len(set(pairs)) != len(pairs):
            raise StaffingError("staffing_invalid_roster", "duplicate eligibility")
        if type(self.max_daily_minutes) is not int or not 1 <= self.max_daily_minutes <= 1440:
            raise StaffingError("staffing_invalid_roster", "max_daily_minutes")
        object.__setattr__(self, "skill_codes", tuple(sorted(skills)))
        object.__setattr__(self, "eligibility", tuple(sorted(pairs)))

    @classmethod
    def from_mapping(cls, value: object) -> Worker:
        body = require_exact_object(
            value,
            frozenset(
                {"staff_id", "display_name", "skill_codes", "eligibility", "max_daily_minutes"}
            ),
        )
        staff_id = validate_identifier(body["staff_id"], "staff_id")
        display_name = validate_human_text(
            body["display_name"], "display_name", maximum=100, formula_safe=True
        )
        raw_skills = body["skill_codes"]
        if type(raw_skills) is not list:
            raise StaffingError("staffing_invalid_roster", "skill_codes")
        skills = tuple(validate_code(item, "skill_codes") for item in raw_skills)
        if len(set(skills)) != len(skills):
            raise StaffingError("staffing_invalid_roster", "duplicate skill_codes")
        raw_eligibility = body["eligibility"]
        if type(raw_eligibility) is not list:
            raise StaffingError("staffing_invalid_roster", "eligibility")
        eligibility_items: list[tuple[str, str]] = []
        for item in raw_eligibility:
            pair = require_exact_object(item, frozenset({"role_code", "area_code"}))
            eligibility_items.append(
                (
                    validate_code(pair["role_code"], "role_code"),
                    validate_code(pair["area_code"], "area_code"),
                )
            )
        if len(set(eligibility_items)) != len(eligibility_items):
            raise StaffingError("staffing_invalid_roster", "duplicate eligibility")
        maximum = body["max_daily_minutes"]
        if type(maximum) is not int or not 1 <= maximum <= 1440:
            raise StaffingError("staffing_invalid_roster", "max_daily_minutes")
        return cls(
            staff_id,
            display_name,
            tuple(sorted(skills)),
            tuple(sorted(eligibility_items)),
            maximum,
        )


@dataclass(frozen=True, slots=True)
class AvailabilityWindow(_FrozenContract):
    staff_id: str
    weekday: int
    start_local: str
    end_local: str

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        validate_identifier(self.staff_id, "staff_id")
        validate_weekday(self.weekday)
        validate_local_minute(self.start_local, "start_local")
        validate_local_minute(self.end_local, "end_local")
        if self.start_local >= self.end_local:
            raise StaffingError("staffing_invalid_roster", "availability interval")

    @classmethod
    def from_mapping(cls, value: object) -> AvailabilityWindow:
        body = require_exact_object(
            value, frozenset({"staff_id", "weekday", "start_local", "end_local"})
        )
        return cls(
            validate_identifier(body["staff_id"], "staff_id"),
            validate_weekday(body["weekday"]),
            validate_local_minute(body["start_local"], "start_local"),
            validate_local_minute(body["end_local"], "end_local"),
        )


@dataclass(frozen=True, slots=True)
class WeeklyAssignment(_FrozenContract):
    staff_id: str
    weekday: int
    role_code: str
    area_code: str
    start_local: str
    end_local: str

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        validate_identifier(self.staff_id, "staff_id")
        validate_weekday(self.weekday)
        validate_code(self.role_code, "role_code")
        validate_code(self.area_code, "area_code")
        validate_local_minute(self.start_local, "start_local")
        validate_local_minute(self.end_local, "end_local")
        if self.start_local >= self.end_local:
            raise StaffingError("staffing_invalid_roster", "assignment interval")

    @classmethod
    def from_mapping(cls, value: object) -> WeeklyAssignment:
        body = require_exact_object(
            value,
            frozenset(
                {"staff_id", "weekday", "role_code", "area_code", "start_local", "end_local"}
            ),
        )
        return cls(
            validate_identifier(body["staff_id"], "staff_id"),
            validate_weekday(body["weekday"]),
            validate_code(body["role_code"], "role_code"),
            validate_code(body["area_code"], "area_code"),
            validate_local_minute(body["start_local"], "start_local"),
            validate_local_minute(body["end_local"], "end_local"),
        )


@dataclass(frozen=True, slots=True)
class AssignmentRule(_FrozenContract):
    role_code: str
    area_code: str
    required_skill_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        validate_code(self.role_code, "role_code")
        validate_code(self.area_code, "area_code")
        if type(self.required_skill_codes) is not tuple:
            raise StaffingError("staffing_invalid_roster", "required_skill_codes")
        skills = tuple(
            validate_code(item, "required_skill_codes") for item in self.required_skill_codes
        )
        if len(set(skills)) != len(skills):
            raise StaffingError("staffing_invalid_roster", "duplicate required_skill_codes")
        object.__setattr__(self, "required_skill_codes", tuple(sorted(skills)))

    @classmethod
    def from_mapping(cls, value: object) -> AssignmentRule:
        body = require_exact_object(
            value, frozenset({"role_code", "area_code", "required_skill_codes"})
        )
        raw_skills = body["required_skill_codes"]
        if type(raw_skills) is not list:
            raise StaffingError("staffing_invalid_roster", "required_skill_codes")
        skills = tuple(validate_code(item, "required_skill_codes") for item in raw_skills)
        if len(set(skills)) != len(skills):
            raise StaffingError("staffing_invalid_roster", "duplicate required_skill_codes")
        return cls(
            validate_code(body["role_code"], "role_code"),
            validate_code(body["area_code"], "area_code"),
            tuple(sorted(skills)),
        )


@dataclass(frozen=True, slots=True)
class CoverageRequirement(_FrozenContract):
    weekday: int
    role_code: str
    area_code: str
    start_local: str
    end_local: str
    minimum_staff: int

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        validate_weekday(self.weekday)
        validate_code(self.role_code, "role_code")
        validate_code(self.area_code, "area_code")
        validate_local_minute(self.start_local, "start_local")
        validate_local_minute(self.end_local, "end_local")
        if self.start_local >= self.end_local:
            raise StaffingError("staffing_invalid_roster", "coverage interval")
        if type(self.minimum_staff) is not int or not 1 <= self.minimum_staff <= 10_000:
            raise StaffingError("staffing_invalid_roster", "minimum_staff")

    @classmethod
    def from_mapping(cls, value: object) -> CoverageRequirement:
        body = require_exact_object(
            value,
            frozenset(
                {"weekday", "role_code", "area_code", "start_local", "end_local", "minimum_staff"}
            ),
        )
        minimum = body["minimum_staff"]
        if type(minimum) is not int or not 1 <= minimum <= 10_000:
            raise StaffingError("staffing_invalid_roster", "minimum_staff")
        return cls(
            validate_weekday(body["weekday"]),
            validate_code(body["role_code"], "role_code"),
            validate_code(body["area_code"], "area_code"),
            validate_local_minute(body["start_local"], "start_local"),
            validate_local_minute(body["end_local"], "end_local"),
            minimum,
        )


@dataclass(frozen=True, slots=True)
class RosterRevision(_FrozenContract):
    site_id: str
    deployment_id: str
    site_timezone: str
    revision: int
    effective_from: date
    effective_until: date | None
    workers: tuple[Worker, ...]
    availability: tuple[AvailabilityWindow, ...]
    regular_assignments: tuple[WeeklyAssignment, ...]
    assignment_rules: tuple[AssignmentRule, ...]
    coverage: tuple[CoverageRequirement, ...]
    roster_digest: str

    def __post_init__(self) -> None:
        if type(self.site_timezone) is not str or not self.site_timezone:
            raise StaffingError("staffing_invalid_roster", "site_timezone")
        try:
            ZoneInfo(self.site_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise StaffingError("staffing_invalid_roster", "site_timezone") from None
        if (
            type(self.roster_digest) is not str
            or HEX_DIGEST_PATTERN.fullmatch(self.roster_digest) is None
        ):
            raise StaffingError("staffing_invalid_roster", "roster_digest")
        _FrozenContract.__post_init__(self)
        validate_identifier(self.site_id, "site_id")
        validate_identifier(self.deployment_id, "deployment_id")
        if type(self.revision) is not int or self.revision < 1:
            raise StaffingError("staffing_invalid_roster", "revision")
        if type(self.effective_from) is not date or (
            self.effective_until is not None and type(self.effective_until) is not date
        ):
            raise StaffingError("staffing_invalid_roster", "effective date range")
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise StaffingError("staffing_invalid_roster", "effective date range")
        for name, expected in (
            ("workers", Worker),
            ("availability", AvailabilityWindow),
            ("regular_assignments", WeeklyAssignment),
            ("assignment_rules", AssignmentRule),
            ("coverage", CoverageRequirement),
        ):
            values = getattr(self, name)
            if type(values) is not tuple or any(type(item) is not expected for item in values):
                raise StaffingError("staffing_invalid_roster", name)
        normalized = (
            (
                "workers",
                tuple(sorted(self.workers, key=lambda item: item.staff_id)),
            ),
            (
                "availability",
                tuple(
                    sorted(
                        self.availability,
                        key=lambda item: (
                            item.staff_id,
                            item.weekday,
                            item.start_local,
                            item.end_local,
                        ),
                    )
                ),
            ),
            (
                "regular_assignments",
                tuple(
                    sorted(
                        self.regular_assignments,
                        key=lambda item: (
                            item.staff_id,
                            item.weekday,
                            item.start_local,
                            item.end_local,
                            item.role_code,
                            item.area_code,
                        ),
                    )
                ),
            ),
            (
                "assignment_rules",
                tuple(
                    sorted(
                        self.assignment_rules,
                        key=lambda item: (item.role_code, item.area_code),
                    )
                ),
            ),
            (
                "coverage",
                tuple(
                    sorted(
                        self.coverage,
                        key=lambda item: (
                            item.weekday,
                            item.role_code,
                            item.area_code,
                            item.start_local,
                            item.end_local,
                            item.minimum_staff,
                        ),
                    )
                ),
            ),
        )
        if any(getattr(self, name) != expected for name, expected in normalized):
            raise StaffingError("staffing_invalid_roster", "noncanonical roster order")
        core = {
            "schema": "nxt-staffing-roster-import/v1",
            "site_id": self.site_id,
            "deployment_id": self.deployment_id,
            "site_timezone": self.site_timezone,
            "effective_from_local_date": self.effective_from,
            "effective_until_local_date": self.effective_until,
            "workers": self.workers,
            "availability": self.availability,
            "regular_assignments": self.regular_assignments,
            "assignment_rules": self.assignment_rules,
            "coverage": self.coverage,
        }
        if stable_digest(core) != self.roster_digest:
            raise StaffingError("staffing_invalid_roster", "roster_digest")

    @classmethod
    def from_normalized(
        cls,
        body: dict[str, object],
        workers: tuple[Worker, ...],
        availability: tuple[AvailabilityWindow, ...],
        assignment_rules: tuple[AssignmentRule, ...],
        regular_assignments: tuple[WeeklyAssignment, ...],
        coverage: tuple[CoverageRequirement, ...],
    ) -> RosterRevision:
        expected = body["expected_roster_revision"]
        if type(expected) is not int or expected < 0:
            raise StaffingError("staffing_invalid_roster", "expected_roster_revision")
        effective_from = parse_local_date(
            body["effective_from_local_date"], "effective_from_local_date"
        )
        raw_until = body["effective_until_local_date"]
        effective_until = (
            None
            if raw_until is None
            else parse_local_date(raw_until, "effective_until_local_date")
        )
        if effective_until is not None and effective_until < effective_from:
            raise StaffingError("staffing_invalid_roster", "effective date range")
        normalized_workers = tuple(sorted(tuple(workers), key=lambda item: item.staff_id))
        normalized_availability = tuple(
            sorted(
                tuple(availability),
                key=lambda item: (item.staff_id, item.weekday, item.start_local, item.end_local),
            )
        )
        normalized_assignments = tuple(
            sorted(
                tuple(regular_assignments),
                key=lambda item: (
                    item.staff_id,
                    item.weekday,
                    item.start_local,
                    item.end_local,
                    item.role_code,
                    item.area_code,
                ),
            )
        )
        normalized_rules = tuple(
            sorted(tuple(assignment_rules), key=lambda item: (item.role_code, item.area_code))
        )
        normalized_coverage = tuple(
            sorted(
                tuple(coverage),
                key=lambda item: (
                    item.weekday,
                    item.role_code,
                    item.area_code,
                    item.start_local,
                    item.end_local,
                    item.minimum_staff,
                ),
            )
        )
        core = {
            "schema": body["schema"],
            "site_id": body["site_id"],
            "deployment_id": body["deployment_id"],
            "site_timezone": body["site_timezone"],
            "effective_from_local_date": effective_from,
            "effective_until_local_date": effective_until,
            "workers": normalized_workers,
            "availability": normalized_availability,
            "regular_assignments": normalized_assignments,
            "assignment_rules": normalized_rules,
            "coverage": normalized_coverage,
        }
        return cls(
            str(body["site_id"]),
            str(body["deployment_id"]),
            str(body["site_timezone"]),
            expected + 1,
            effective_from,
            effective_until,
            normalized_workers,
            normalized_availability,
            normalized_assignments,
            normalized_rules,
            normalized_coverage,
            stable_digest(core),
        )

    def applies_to(self, service_date: date) -> bool:
        return self.effective_from <= service_date and (
            self.effective_until is None or service_date <= self.effective_until
        )


@dataclass(frozen=True, slots=True)
class Assignment(_FrozenContract):
    assignment_id: str
    staff_id: str
    role_code: str
    area_code: str
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        validate_identifier(self.assignment_id, "assignment_id")
        validate_identifier(self.staff_id, "staff_id")
        validate_code(self.role_code, "role_code")
        validate_code(self.area_code, "area_code")
        _validate_assignment_interval(self.start_at, self.end_at)

    @classmethod
    def from_weekly_row(
        cls,
        revision: RosterRevision,
        service_date: date,
        row: WeeklyAssignment,
        start_at: datetime,
        end_at: datetime,
    ) -> Assignment:
        from .time import utc_text

        seed = {
            "roster_revision": revision.revision,
            "service_date": service_date.isoformat(),
            "staff_id": row.staff_id,
            "role_code": row.role_code,
            "area_code": row.area_code,
            "start_at": utc_text(start_at),
            "end_at": utc_text(end_at),
        }
        return cls(
            "assignment_" + stable_digest(seed)[:24],
            row.staff_id,
            row.role_code,
            row.area_code,
            start_at,
            end_at,
        )

    def with_interval(self, start_at: datetime, end_at: datetime) -> Assignment:
        from .time import utc_text

        _validate_assignment_interval(start_at, end_at)
        if start_at == self.start_at and end_at == self.end_at:
            return self
        seed = {
            "source_assignment_id": self.assignment_id,
            "start_at": utc_text(start_at),
            "end_at": utc_text(end_at),
        }
        return Assignment(
            "assignment_" + stable_digest(seed)[:24],
            self.staff_id,
            self.role_code,
            self.area_code,
            start_at,
            end_at,
        )


def _validate_assignment_interval(start_at: datetime, end_at: datetime) -> None:
    if (
        type(start_at) is not datetime
        or type(end_at) is not datetime
        or start_at.tzinfo is None
        or end_at.tzinfo is None
        or start_at.utcoffset() is None
        or end_at.utcoffset() is None
        or start_at.second
        or end_at.second
        or start_at.microsecond
        or end_at.microsecond
        or end_at <= start_at
    ):
        raise StaffingError("staffing_invalid_roster", "assignment interval")


@dataclass(frozen=True, slots=True)
class CoverageWindow(_FrozenContract):
    role_code: str
    area_code: str
    start_at: datetime
    end_at: datetime
    minimum_staff: int

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        validate_code(self.role_code, "role_code")
        validate_code(self.area_code, "area_code")
        _validate_assignment_interval(self.start_at, self.end_at)
        if type(self.minimum_staff) is not int or not 1 <= self.minimum_staff <= 10_000:
            raise StaffingError("staffing_invalid_roster", "minimum_staff")


@dataclass(frozen=True, slots=True)
class ServiceDayRoster(_FrozenContract):
    service_date: date
    site_timezone: str
    assignments: tuple[Assignment, ...]
    coverage: tuple[CoverageWindow, ...]

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        if type(self.service_date) is not date:
            raise StaffingError("staffing_invalid_roster", "service_date")
        if type(self.site_timezone) is not str or not self.site_timezone:
            raise StaffingError("staffing_invalid_roster", "site_timezone")
        if type(self.assignments) is not tuple or any(
            type(item) is not Assignment for item in self.assignments
        ):
            raise StaffingError("staffing_invalid_roster", "assignments")
        if type(self.coverage) is not tuple or any(
            type(item) is not CoverageWindow for item in self.coverage
        ):
            raise StaffingError("staffing_invalid_roster", "coverage")

    @classmethod
    def build(
        cls,
        revision: RosterRevision,
        service_date: date,
        assignments: tuple[Assignment, ...],
    ) -> ServiceDayRoster:
        from .time import resolve_local_minute

        if type(service_date) is not date:
            raise StaffingError("staffing_invalid_roster", "service_date")
        detached = tuple(assignments)
        for assignment in detached:
            _validate_assignment_interval(assignment.start_at, assignment.end_at)
        ordered = tuple(
            sorted(
                detached,
                key=lambda item: (item.start_at, item.end_at, item.staff_id, item.assignment_id),
            )
        )
        by_staff: dict[str, list[Assignment]] = {}
        for assignment in ordered:
            by_staff.setdefault(assignment.staff_id, []).append(assignment)
        for rows in by_staff.values():
            for left, right in zip(rows, rows[1:]):
                if right.start_at < left.end_at:
                    raise StaffingError("staffing_invalid_roster", "overlapping assignments")
        coverage = tuple(
            CoverageWindow(
                row.role_code,
                row.area_code,
                resolve_local_minute(service_date, row.start_local, revision.site_timezone),
                resolve_local_minute(service_date, row.end_local, revision.site_timezone),
                row.minimum_staff,
            )
            for row in revision.coverage
            if row.weekday == service_date.weekday()
        )
        return cls(service_date, revision.site_timezone, ordered, coverage)

    def assignment_for_staff(self, staff_id: str) -> Assignment | None:
        matches = tuple(item for item in self.assignments if item.staff_id == staff_id)
        if len(matches) > 1:
            raise StaffingError("staffing_invalid_roster", "multiple assignments for staff")
        return matches[0] if matches else None


@dataclass(frozen=True, slots=True)
class AttemptRouteEvidence(_FrozenContract):
    provider: Literal["KIMI", "OPENAI", "ANTHROPIC"]
    region: Literal["CN", "GLOBAL"]
    route_role: Literal["PRIMARY", "BACKUP"]
    route_id: str
    model_id: str

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        if type(self.provider) is not str or self.provider not in {
            "KIMI",
            "OPENAI",
            "ANTHROPIC",
        }:
            raise StaffingError("staffing_invalid_evidence", "provider")
        if type(self.region) is not str or self.region not in {"CN", "GLOBAL"}:
            raise StaffingError("staffing_invalid_evidence", "region")
        if type(self.route_role) is not str or self.route_role not in {"PRIMARY", "BACKUP"}:
            raise StaffingError("staffing_invalid_evidence", "route_role")
        _validate_ascii_text(self.route_id, "route_id", maximum=64)
        _validate_ascii_text(self.model_id, "model_id", maximum=128)


@dataclass(frozen=True, slots=True)
class GenerationRouteEvidence(_FrozenContract):
    region: Literal["CN", "GLOBAL"]
    readiness: Literal["READY", "DEGRADED_BACKUP_UNCONFIGURED", "UNAVAILABLE"]
    primary_provider: Literal["KIMI", "OPENAI"] | None
    primary_model_id: str | None
    backup_provider: Literal["ANTHROPIC"] | None
    backup_model_id: str | None

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        if type(self.region) is not str or self.region not in {"CN", "GLOBAL"}:
            raise StaffingError("staffing_invalid_evidence", "region")
        if type(self.readiness) is not str or self.readiness not in {
            "READY",
            "DEGRADED_BACKUP_UNCONFIGURED",
            "UNAVAILABLE",
        }:
            raise StaffingError("staffing_invalid_evidence", "readiness")
        if self.primary_provider is not None and (
            type(self.primary_provider) is not str
            or self.primary_provider not in {"KIMI", "OPENAI"}
        ):
            raise StaffingError("staffing_invalid_evidence", "primary_provider")
        if self.backup_provider is not None and (
            type(self.backup_provider) is not str
            or self.backup_provider != "ANTHROPIC"
        ):
            raise StaffingError("staffing_invalid_evidence", "backup_provider")
        _validate_ascii_text(
            self.primary_model_id, "primary_model_id", maximum=128, optional=True
        )
        _validate_ascii_text(
            self.backup_model_id, "backup_model_id", maximum=128, optional=True
        )
        if (self.primary_provider is None) != (self.primary_model_id is None) or (
            self.backup_provider is None
        ) != (self.backup_model_id is None):
            raise StaffingError("staffing_invalid_evidence", "provider/model pair")


@dataclass(frozen=True, slots=True)
class StaffingException(_FrozenContract):
    exception_id: str
    service_date: date
    staff_id: str
    kind: Literal["LEAVE", "LATE", "EARLY_DEPARTURE", "UNAVAILABLE"]
    unavailable_start: datetime
    unavailable_end: datetime
    note: str | None


@dataclass(frozen=True, slots=True)
class StaffingBasis(_FrozenContract):
    service_date: date
    roster_revision: int
    exception_set_revision: int
    effective_plan_revision: int
    roster_digest: str
    exception_set_digest: str
    effective_plan_digest: str
    basis_digest: str


@dataclass(frozen=True, slots=True)
class EffectivePlanState(_FrozenContract):
    revision: int
    status: Literal["NO_PLAN", "CURRENT", "REVIEW_REQUIRED"]
    schedule_digest: str
    effective_schedule: tuple[Assignment, ...] | None
    source_sequence: int
    affected_exception_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssignmentProjection(_FrozenContract):
    assignment_id: str
    staff_id: str
    display_name: str
    role_code: str
    area_code: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class ExceptionProjection(_FrozenContract):
    exception_id: str
    service_date: date
    staff_id: str
    display_name: str
    kind: str
    unavailable_start: datetime
    unavailable_end: datetime
    note: str | None


@dataclass(frozen=True, slots=True)
class CandidateOperationProjection(_FrozenContract):
    operation: Literal["REMOVE", "ADD"]
    assignment_id: str | None
    staff_id: str | None
    display_name: str | None
    role_code: str | None
    area_code: str | None
    start_at: datetime | None
    end_at: datetime | None


@dataclass(frozen=True, slots=True)
class CoverageGap(_FrozenContract):
    role_code: str
    area_code: str
    start_at: datetime
    end_at: datetime
    required: int
    actual: int


@dataclass(frozen=True, slots=True)
class CandidateProjection(_FrozenContract):
    candidate_index: int
    valid: bool
    rationale: str
    operational_warnings: tuple[str, ...]
    rejection_codes: tuple[str, ...]
    coverage_gaps: tuple[CoverageGap, ...]
    operations: tuple[CandidateOperationProjection, ...]
    materialized_schedule: tuple[AssignmentProjection, ...] | None
    materialized_schedule_digest: str | None


@dataclass(frozen=True, slots=True)
class AttemptStartedEvidence(_FrozenContract):
    request_id: str
    attempt_index: int
    route: AttemptRouteEvidence
    input_digest: str
    timeout_s: float

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        validate_identifier(self.request_id, "request_id")
        if type(self.attempt_index) is not int or self.attempt_index not in (0, 1):
            raise StaffingError("staffing_invalid_evidence", "attempt_index")
        if type(self.route) is not AttemptRouteEvidence:
            raise StaffingError("staffing_invalid_evidence", "route")
        _validate_digest(self.input_digest, "input_digest")
        _validate_positive_number(self.timeout_s, "timeout_s")


@dataclass(frozen=True, slots=True)
class AttemptFinishedEvidence(_FrozenContract):
    request_id: str
    attempt_index: int
    route: AttemptRouteEvidence
    status: Literal[
        "SUCCEEDED",
        "UNAVAILABLE",
        "REFUSED",
        "INVALID_RESPONSE",
        "PROVIDER_ERROR",
        "CONFIGURATION_ERROR",
        "SECURITY_ERROR",
    ]
    failure_code: str | None
    retryable: bool
    security_failure: bool
    provider_request_id: str | None
    finish_reason: str | None
    output_digest: str | None
    input_tokens: int | None
    output_tokens: int | None

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        validate_identifier(self.request_id, "request_id")
        if type(self.attempt_index) is not int or self.attempt_index not in (0, 1):
            raise StaffingError("staffing_invalid_evidence", "attempt_index")
        if type(self.route) is not AttemptRouteEvidence:
            raise StaffingError("staffing_invalid_evidence", "route")
        if type(self.status) is not str or self.status not in (
            GENERATION_TERMINALS - {"RESULT_UNKNOWN", "NO_VALID_SUGGESTION"}
        ):
            raise StaffingError("staffing_invalid_evidence", "status")
        _validate_failure_code(self.failure_code)
        if type(self.retryable) is not bool or type(self.security_failure) is not bool:
            raise StaffingError("staffing_invalid_evidence", "attempt flags")
        _validate_ascii_text(
            self.provider_request_id,
            "provider_request_id",
            maximum=160,
            optional=True,
        )
        _validate_ascii_text(
            self.finish_reason, "finish_reason", maximum=160, optional=True
        )
        _validate_digest(self.output_digest, "output_digest", optional=True)
        for field in ("input_tokens", "output_tokens"):
            value = getattr(self, field)
            if value is not None:
                _validate_nonnegative_int(value, field, maximum=1_000_000)


@dataclass(frozen=True, slots=True)
class ResultEvidence(_FrozenContract):
    request_id: str
    status: Literal[
        "SUCCEEDED",
        "UNAVAILABLE",
        "REFUSED",
        "INVALID_RESPONSE",
        "PROVIDER_ERROR",
        "CONFIGURATION_ERROR",
        "SECURITY_ERROR",
        "RESULT_UNKNOWN",
    ]
    failure_code: str | None
    selected_provider: Literal["KIMI", "OPENAI", "ANTHROPIC"] | None
    selected_model_id: str | None
    provider_request_id: str | None
    finish_reason: str | None
    input_digest: str
    output_digest: str | None
    attempts: tuple[AttemptFinishedEvidence, ...]
    candidate_count: int
    bounded_summary: str | None

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        validate_identifier(self.request_id, "request_id")
        if type(self.status) is not str or self.status not in (
            GENERATION_TERMINALS - {"NO_VALID_SUGGESTION"}
        ):
            raise StaffingError("staffing_invalid_evidence", "status")
        _validate_failure_code(self.failure_code)
        if self.selected_provider is not None and (
            type(self.selected_provider) is not str
            or self.selected_provider not in {"KIMI", "OPENAI", "ANTHROPIC"}
        ):
            raise StaffingError("staffing_invalid_evidence", "selected_provider")
        _validate_ascii_text(
            self.selected_model_id, "selected_model_id", maximum=128, optional=True
        )
        if (self.selected_provider is None) != (self.selected_model_id is None):
            raise StaffingError("staffing_invalid_evidence", "selected provider/model")
        _validate_ascii_text(
            self.provider_request_id,
            "provider_request_id",
            maximum=160,
            optional=True,
        )
        _validate_ascii_text(
            self.finish_reason, "finish_reason", maximum=160, optional=True
        )
        _validate_digest(self.input_digest, "input_digest")
        _validate_digest(self.output_digest, "output_digest", optional=True)
        if type(self.attempts) is not tuple or any(
            type(item) is not AttemptFinishedEvidence for item in self.attempts
        ):
            raise StaffingError("staffing_invalid_evidence", "attempts")
        if len(self.attempts) > 2 or tuple(
            item.attempt_index for item in self.attempts
        ) != tuple(range(len(self.attempts))):
            raise StaffingError("staffing_invalid_evidence", "attempt order")
        _validate_nonnegative_int(self.candidate_count, "candidate_count", maximum=2)
        _validate_plain_text(
            self.bounded_summary,
            "bounded_summary",
            maximum=280,
            minimum=0,
            optional=True,
        )


@dataclass(frozen=True, slots=True)
class GenerationProjectionView(_FrozenContract):
    generation_id: str
    request_id: str
    operation_event_id: str
    service_date: date
    basis_revisions: tuple[int, int, int]
    basis_digest: str
    retry_of: str | None
    lifecycle_state: Literal[
        "RESERVED",
        "IN_PROGRESS",
        "RESULT_UNKNOWN",
        "SUCCEEDED",
        "NO_VALID_SUGGESTION",
        "UNAVAILABLE",
        "REFUSED",
        "INVALID_RESPONSE",
        "PROVIDER_ERROR",
        "CONFIGURATION_ERROR",
        "SECURITY_ERROR",
    ]
    failure_code: str | None
    route_region: str
    route_readiness: str
    primary_provider: str | None
    primary_model_id: str | None
    backup_provider: str | None
    backup_model_id: str | None
    started_attempts: tuple[AttemptStartedEvidence, ...]
    finished_attempts: tuple[AttemptFinishedEvidence, ...]
    candidates: tuple[CandidateProjection, ...]


@dataclass(frozen=True, slots=True)
class ManagerResponseProjection(_FrozenContract):
    generation_id: str
    decision: str
    reason_code: str
    operator: str
    note: str | None
    effective_plan_revision: int | None
    schedule_digest: str | None
    effective_schedule: tuple[AssignmentProjection, ...] | None


@dataclass(frozen=True, slots=True)
class RosterCommittedProjection(_FrozenContract):
    event_id: str
    occurred_at_utc: datetime
    request_digest: str
    operator: str
    source_ref: str
    roster_revision: int
    roster_digest: str
    roster_effective_from: date
    roster_effective_until: date | None
    worker_count: int
    assignment_count: int
    coverage_count: int


@dataclass(frozen=True, slots=True)
class ExceptionCommittedProjection(_FrozenContract):
    event_id: str
    event_type: Literal["exception_recorded", "exception_cancelled", "exception_corrected"]
    occurred_at_utc: datetime
    request_digest: str
    operator: str
    exception_id: str
    exception_set_revision: int
    exception_digest: str | None
    exception: ExceptionProjection | None
    replacement: ExceptionProjection | None
    note: str | None


@dataclass(frozen=True, slots=True)
class GenerationCommittedProjection(_FrozenContract):
    event_id: str
    occurred_at_utc: datetime
    request_digest: str
    generation_id: str
    service_date: date
    basis_revisions: tuple[int, int, int]
    basis_digest: str
    retry_of: str | None
    route_region: str
    route_readiness: str
    primary_provider: str | None
    backup_provider: str | None


@dataclass(frozen=True, slots=True)
class ManagerCommittedProjection(_FrozenContract):
    event_id: str
    occurred_at_utc: datetime
    request_digest: str
    generation_id: str
    operator: str
    note: str | None
    decision: str
    reason_code: str
    effective_plan_revision: int | None
    schedule_digest: str | None
    effective_schedule: tuple[AssignmentProjection, ...] | None


CommittedRecordProjection: TypeAlias = (
    RosterCommittedProjection
    | ExceptionCommittedProjection
    | GenerationCommittedProjection
    | ManagerCommittedProjection
)


@dataclass(frozen=True, slots=True)
class DateProjection(_FrozenContract):
    site_id: str | None
    deployment_id: str | None
    service_date: date
    site_timezone: str | None
    roster_revision: int | None
    roster_digest: str | None
    exception_set_revision: int
    roster_effective_from: date | None
    roster_effective_until: date | None
    worker_count: int
    assignment_count: int
    coverage_count: int
    effective_plan_schedule: tuple[AssignmentProjection, ...] | None
    availability: tuple[AvailabilityWindow, ...]
    assignments: tuple[AssignmentProjection, ...]
    exceptions: tuple[ExceptionProjection, ...]
    effective_plan: EffectivePlanState
    generations: tuple[GenerationProjectionView, ...]
    manager_responses: tuple[ManagerResponseProjection, ...]


@dataclass(frozen=True, slots=True)
class RequestProjection(_FrozenContract):
    operation_kind: str
    request_id: str
    event_ids: tuple[str, ...]
    generation_id: str | None
    lifecycle_state: Literal[
        "COMMITTED",
        "RESERVED",
        "IN_PROGRESS",
        "RESULT_UNKNOWN",
        "SUCCEEDED",
        "NO_VALID_SUGGESTION",
        "UNAVAILABLE",
        "REFUSED",
        "INVALID_RESPONSE",
        "PROVIDER_ERROR",
        "CONFIGURATION_ERROR",
        "SECURITY_ERROR",
    ] | None
    started_attempts: tuple[AttemptStartedEvidence, ...]
    attempt_evidence: tuple[AttemptFinishedEvidence, ...]
    result_evidence: ResultEvidence | None
    candidates: tuple[CandidateProjection, ...]
    manager_response: ManagerResponseProjection | None
    generation: GenerationProjectionView | None
    committed_record: CommittedRecordProjection | None


@dataclass(frozen=True, slots=True)
class BasisSnapshot(_FrozenContract):
    basis: StaffingBasis
    site_timezone: str
    workers: tuple[Worker, ...]
    assignments: tuple[Assignment, ...]
    exceptions: tuple[StaffingException, ...]
    availability: tuple[AvailabilityWindow, ...]
    assignment_rules: tuple[AssignmentRule, ...]
    coverage: tuple[CoverageWindow, ...]
    prompt_template_version: str


@dataclass(frozen=True, slots=True)
class ProviderWorker(_FrozenContract):
    worker_alias: str
    skill_codes: tuple[str, ...]
    eligibility: tuple[tuple[str, str], ...]
    max_daily_minutes: int


@dataclass(frozen=True, slots=True)
class ProviderAssignment(_FrozenContract):
    assignment_alias: str
    worker_alias: str
    role_code: str
    area_code: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderUnavailable(_FrozenContract):
    worker_alias: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderAvailability(_FrozenContract):
    worker_alias: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderCoverage(_FrozenContract):
    role_code: str
    area_code: str
    start_at: datetime
    end_at: datetime
    minimum_staff: int


@dataclass(frozen=True, slots=True)
class ProviderPayload(_FrozenContract):
    basis_digest: str
    workers: tuple[ProviderWorker, ...]
    assignments: tuple[ProviderAssignment, ...]
    availability: tuple[ProviderAvailability, ...]
    unavailable: tuple[ProviderUnavailable, ...]
    coverage: tuple[ProviderCoverage, ...]
    prompt_template_version: str
    language: Literal["zh-CN", "en"]

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        if type(self.language) is not str or self.language not in {"zh-CN", "en"}:
            raise StaffingError("staffing_invalid_evidence", "language")


@dataclass(frozen=True, slots=True)
class RemoveOperation(_FrozenContract):
    operation: Literal["REMOVE"]
    assignment_alias: str


@dataclass(frozen=True, slots=True)
class AddOperation(_FrozenContract):
    operation: Literal["ADD"]
    worker_alias: str
    role_code: str
    area_code: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class RosterImportedPayload(_FrozenContract):
    request_id: str
    request_digest: str
    roster_revision: int
    roster_digest: str
    roster: RosterRevision
    operator: str
    source_ref: str


@dataclass(frozen=True, slots=True)
class ExceptionRecordedPayload(_FrozenContract):
    request_id: str
    request_digest: str
    exception_id: str
    service_date: date
    exception_set_revision: int
    exception_digest: str
    exception: StaffingException
    operator: str


@dataclass(frozen=True, slots=True)
class ExceptionCancelledPayload(_FrozenContract):
    request_id: str
    request_digest: str
    exception_id: str
    exception_set_revision: int
    cancelled_exception: StaffingException
    operator: str
    note: str | None


@dataclass(frozen=True, slots=True)
class ExceptionCorrectedPayload(_FrozenContract):
    request_id: str
    request_digest: str
    exception_id: str
    replacement_exception: StaffingException
    exception_set_revision: int
    exception_digest: str
    previous_exception: StaffingException
    operator: str


@dataclass(frozen=True, slots=True)
class GenerationReservedPayload(_FrozenContract):
    request_id: str
    request_digest: str
    generation_id: str
    service_date: date
    basis_snapshot: BasisSnapshot
    provider_payload: ProviderPayload
    alias_nonce_digest: str
    worker_alias_to_staff_id: tuple[tuple[str, str], ...]
    assignment_alias_to_assignment_id: tuple[tuple[str, str], ...]
    input_digest: str
    prompt_template_version: str
    language: Literal["zh-CN", "en"]
    route: GenerationRouteEvidence
    retry_of: str | None
    operator: str

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        if type(self.language) is not str or self.language not in {"zh-CN", "en"}:
            raise StaffingError("staffing_invalid_evidence", "language")


@dataclass(frozen=True, slots=True)
class GenerationInterruptedPayload(_FrozenContract):
    request_digest: str
    generation_id: str
    reason: Literal["RESULT_UNKNOWN"]
    interrupted_at_utc: datetime


@dataclass(frozen=True, slots=True)
class ProviderAttemptStartedPayload(_FrozenContract):
    request_digest: str
    generation_id: str
    evidence: AttemptStartedEvidence


@dataclass(frozen=True, slots=True)
class ProviderAttemptFinishedPayload(_FrozenContract):
    request_digest: str
    generation_id: str
    evidence: AttemptFinishedEvidence


@dataclass(frozen=True, slots=True)
class StoredCandidate(_FrozenContract):
    candidate_index: Literal[1, 2]
    operations: tuple[RemoveOperation | AddOperation, ...]
    operation_offset_minutes: tuple[tuple[int | None, int | None], ...]
    rationale: str
    operational_warnings: tuple[str, ...]
    rejection_codes: tuple[str, ...]
    coverage_gaps: tuple[CoverageGap, ...]
    materialized_schedule: tuple[Assignment, ...] | None
    materialized_schedule_digest: str | None

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        if type(self.candidate_index) is not int or self.candidate_index not in (1, 2):
            raise StaffingError("staffing_invalid_evidence", "candidate_index")
        if (
            type(self.operations) is not tuple
            or type(self.operation_offset_minutes) is not tuple
            or len(self.operations) != len(self.operation_offset_minutes)
        ):
            raise StaffingError("staffing_invalid_evidence", "operation offsets")
        for operation, offsets in zip(self.operations, self.operation_offset_minutes):
            if type(offsets) is not tuple or len(offsets) != 2:
                raise StaffingError("staffing_invalid_evidence", "operation offsets")
            start_offset, end_offset = offsets
            if type(operation) is RemoveOperation:
                if operation.operation != "REMOVE" or offsets != (None, None):
                    raise StaffingError("staffing_invalid_evidence", "REMOVE offsets")
                continue
            if type(operation) is not AddOperation or operation.operation != "ADD":
                raise StaffingError("staffing_invalid_evidence", "candidate operation")
            if any(
                type(value) is not int or not -1439 <= value <= 1439
                for value in (start_offset, end_offset)
            ):
                raise StaffingError("staffing_invalid_evidence", "ADD offsets")


@dataclass(frozen=True, slots=True)
class SuggestionIssuedPayload(_FrozenContract):
    request_digest: str
    generation_id: str
    result: ResultEvidence
    candidates: tuple[StoredCandidate, ...]
    candidate_set_digest: str


@dataclass(frozen=True, slots=True)
class SuggestionUnavailablePayload(_FrozenContract):
    request_digest: str
    generation_id: str
    result: ResultEvidence
    candidates: tuple[StoredCandidate, ...]
    candidate_set_digest: str | None
    terminal_state: Literal[
        "NO_VALID_SUGGESTION",
        "UNAVAILABLE",
        "REFUSED",
        "INVALID_RESPONSE",
        "PROVIDER_ERROR",
        "CONFIGURATION_ERROR",
        "SECURITY_ERROR",
    ]
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class ManagerResponseCommittedPayload(_FrozenContract):
    request_id: str
    request_digest: str
    generation_id: str
    decision: Literal["ACCEPT", "MODIFY", "REJECT"]
    reason_code: Literal[
        "APPROVED",
        "APPROVED_WITH_CHANGES",
        "MANUAL_HANDLING",
        "INSUFFICIENT_CONTEXT",
        "OTHER",
    ]
    effective_schedule: tuple[Assignment, ...] | None
    schedule_digest: str | None
    effective_plan_revision: int | None
    operator: str
    note: str | None
    basis_snapshot: BasisSnapshot


@dataclass(frozen=True, slots=True)
class ManagerRemoveOperation(_FrozenContract):
    operation: Literal["REMOVE"]
    assignment_id: str


@dataclass(frozen=True, slots=True)
class ManagerAddOperation(_FrozenContract):
    operation: Literal["ADD"]
    staff_id: str
    role_code: str
    area_code: str
    start_at: datetime
    end_at: datetime


ManagerPatchOperation: TypeAlias = ManagerRemoveOperation | ManagerAddOperation


@dataclass(frozen=True, slots=True)
class RevisionVector(_FrozenContract):
    roster: int
    exception_set: int
    effective_plan: int


@dataclass(frozen=True, slots=True)
class ManagerResponseRequest(_FrozenContract):
    request_id: str
    generation_id: str
    operator: str
    kind: Literal["ACCEPT", "MODIFY", "REJECT"]
    expected_revisions: RevisionVector
    candidate_index: Literal[1, 2] | None
    edited_operations: tuple[ManagerPatchOperation, ...]
    reason_code: str
    note: str | None


EventPayload: TypeAlias = (
    RosterImportedPayload
    | ExceptionRecordedPayload
    | ExceptionCancelledPayload
    | ExceptionCorrectedPayload
    | GenerationReservedPayload
    | GenerationInterruptedPayload
    | ProviderAttemptStartedPayload
    | ProviderAttemptFinishedPayload
    | SuggestionIssuedPayload
    | SuggestionUnavailablePayload
    | ManagerResponseCommittedPayload
)


@dataclass(frozen=True, slots=True)
class StaffingEvent(_FrozenContract):
    event_type: Literal[
        "roster_imported",
        "exception_recorded",
        "exception_cancelled",
        "exception_corrected",
        "generation_reserved",
        "generation_interrupted",
        "provider_attempt_started",
        "provider_attempt_finished",
        "suggestion_issued",
        "suggestion_unavailable",
        "manager_response_committed",
    ]
    event_id: str
    sequence: int
    site_id: str
    deployment_id: str
    occurred_at_utc: datetime
    causation_id: str | None
    payload: EventPayload


@dataclass(frozen=True, slots=True)
class StaffingReceipt(_FrozenContract):
    operation_kind: Literal[
        "roster-import",
        "exception-record",
        "exception-cancel",
        "exception-correct",
        "suggestion-generate",
        "manager-response",
    ]
    request_id: str
    request_digest: str
    event_id: str
    sequence: int
    record_hash: str
    duplicate: bool

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        if type(self.operation_kind) is not str or self.operation_kind not in OPERATION_KINDS:
            raise StaffingError("staffing_invalid_evidence", "operation_kind")
        validate_identifier(self.request_id, "request_id")
        _validate_digest(self.request_digest, "request_digest")
        validate_identifier(self.event_id, "event_id")
        if type(self.sequence) is not int or self.sequence < 1:
            raise StaffingError("staffing_invalid_evidence", "sequence")
        _validate_digest(self.record_hash, "record_hash")
        if type(self.duplicate) is not bool:
            raise StaffingError("staffing_invalid_evidence", "duplicate")


@dataclass(frozen=True, slots=True)
class CommittedReceipt(_FrozenContract):
    receipt: StaffingReceipt

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        if type(self.receipt) is not StaffingReceipt or self.receipt.duplicate:
            raise StaffingError("staffing_invalid_evidence", "committed receipt duplicate flag")


@dataclass(frozen=True, slots=True)
class DuplicateReceipt(_FrozenContract):
    receipt: StaffingReceipt

    def __post_init__(self) -> None:
        _FrozenContract.__post_init__(self)
        if type(self.receipt) is not StaffingReceipt or not self.receipt.duplicate:
            raise StaffingError("staffing_invalid_evidence", "duplicate receipt duplicate flag")


@dataclass(frozen=True, slots=True)
class ConflictReceipt(_FrozenContract):
    operation_kind: str
    request_id: str
    code: Literal[
        "IDEMPOTENCY_CONFLICT",
        "STALE_REQUEST",
        "STALE_SUGGESTION",
        "INVALID_TRANSITION",
        "OVERLAPPING_EXCEPTION",
    ]


ReceiptResult: TypeAlias = CommittedReceipt | DuplicateReceipt | ConflictReceipt


@dataclass(frozen=True, slots=True)
class GenerationState(_FrozenContract):
    generation_id: str
    request_id: str
    request_digest: str
    is_terminal: bool


@dataclass(frozen=True, slots=True)
class AppendEventDecision(_FrozenContract):
    event: StaffingEvent


@dataclass(frozen=True, slots=True)
class ReturnReceiptDecision(_FrozenContract):
    receipt: StaffingReceipt


@dataclass(frozen=True, slots=True)
class ConflictDecision(_FrozenContract):
    operation_kind: str
    request_id: str
    code: Literal[
        "IDEMPOTENCY_CONFLICT",
        "STALE_REQUEST",
        "STALE_SUGGESTION",
        "INVALID_TRANSITION",
        "OVERLAPPING_EXCEPTION",
    ]


AppendDecision: TypeAlias = AppendEventDecision | ReturnReceiptDecision | ConflictDecision

EVENT_TO_OPERATION = {
    "roster_imported": "roster-import",
    "exception_recorded": "exception-record",
    "exception_cancelled": "exception-cancel",
    "exception_corrected": "exception-correct",
    "generation_reserved": "suggestion-generate",
    "manager_response_committed": "manager-response",
}


@dataclass(frozen=True, slots=True)
class StaffingHistory(_FrozenContract):
    events: tuple[StaffingEvent, ...]

    @property
    def record_count(self) -> int:
        return len(self.events)

    def find_request_event(self, operation_kind: str, request_id: str) -> StaffingEvent | None:
        for event in self.events:
            if (
                EVENT_TO_OPERATION.get(event.event_type) == operation_kind
                and getattr(event.payload, "request_id", None) == request_id
            ):
                return event
        return None

    def reservation(self, generation_id: str) -> GenerationReservedPayload:
        for event in self.events:
            if (
                event.event_type == "generation_reserved"
                and isinstance(event.payload, GenerationReservedPayload)
                and event.payload.generation_id == generation_id
            ):
                return event.payload
        raise StaffingError("UNKNOWN_GENERATION", "generation_id")

    def generation(self, generation_id: str) -> GenerationState:
        reservation = self.reservation(generation_id)
        terminal = any(
            event.event_type
            in {
                "suggestion_issued",
                "suggestion_unavailable",
                "generation_interrupted",
            }
            and getattr(event.payload, "generation_id", None) == generation_id
            for event in self.events
        )
        return GenerationState(
            generation_id, reservation.request_id, reservation.request_digest, terminal
        )


@dataclass(frozen=True, slots=True)
class VerifiedLedgerState(_FrozenContract):
    history: StaffingHistory
    receipts: tuple[StaffingReceipt, ...]
    record_count: int
    head_hash: str

    def request(
        self, operation_kind: str, request_id: str, request_digest: str
    ) -> StaffingReceipt | None:
        for receipt in self.receipts:
            if receipt.operation_kind != operation_kind or receipt.request_id != request_id:
                continue
            if receipt.request_digest != request_digest:
                raise StaffingError("IDEMPOTENCY_CONFLICT", "request_id")
            return receipt
        return None


def parse_attempt_route_evidence(value: object) -> AttemptRouteEvidence:
    body = require_exact_object(
        value,
        frozenset({"provider", "region", "route_role", "route_id", "model_id"}),
        code="staffing_invalid_evidence",
        detail="attempt route fields",
    )
    provider = body["provider"]
    region = body["region"]
    role = body["route_role"]
    if type(provider) is not str or provider not in {"KIMI", "OPENAI", "ANTHROPIC"}:
        raise StaffingError("staffing_invalid_evidence", "provider")
    if (
        type(region) is not str
        or region not in {"CN", "GLOBAL"}
        or type(role) is not str
        or role not in {"PRIMARY", "BACKUP"}
    ):
        raise StaffingError("staffing_invalid_evidence", "route")
    route_id = _validate_ascii_text(body["route_id"], "route_id", maximum=64)
    model_id = _validate_ascii_text(body["model_id"], "model_id", maximum=128)
    assert route_id is not None and model_id is not None
    return AttemptRouteEvidence(provider, region, role, route_id, model_id)


def parse_generation_route_evidence(value: object) -> GenerationRouteEvidence:
    body = require_exact_object(
        value,
        frozenset(
            {
                "region",
                "readiness",
                "primary_provider",
                "primary_model_id",
                "backup_provider",
                "backup_model_id",
            }
        ),
        code="staffing_invalid_evidence",
        detail="generation route fields",
    )
    region = body["region"]
    readiness = body["readiness"]
    primary = body["primary_provider"]
    backup = body["backup_provider"]
    if type(region) is not str or region not in {"CN", "GLOBAL"}:
        raise StaffingError("staffing_invalid_evidence", "region")
    if type(readiness) is not str or readiness not in {
        "READY",
        "DEGRADED_BACKUP_UNCONFIGURED",
        "UNAVAILABLE",
    }:
        raise StaffingError("staffing_invalid_evidence", "readiness")
    if primary is not None and (
        type(primary) is not str or primary not in {"KIMI", "OPENAI"}
    ):
        raise StaffingError("staffing_invalid_evidence", "provider")
    if backup is not None and (type(backup) is not str or backup != "ANTHROPIC"):
        raise StaffingError("staffing_invalid_evidence", "provider")
    primary_model = _validate_ascii_text(
        body["primary_model_id"], "primary_model_id", maximum=128, optional=True
    )
    backup_model = _validate_ascii_text(
        body["backup_model_id"], "backup_model_id", maximum=128, optional=True
    )
    if (primary is None) != (primary_model is None) or (backup is None) != (backup_model is None):
        raise StaffingError("staffing_invalid_evidence", "provider/model pair")
    return GenerationRouteEvidence(
        region, readiness, primary, primary_model, backup, backup_model
    )


def parse_attempt_started_evidence(value: object) -> AttemptStartedEvidence:
    body = require_exact_object(
        value,
        frozenset({"request_id", "attempt_index", "route", "input_digest", "timeout_s"}),
        code="staffing_invalid_evidence",
        detail="attempt started fields",
    )
    request_id = validate_identifier(body["request_id"], "request_id")
    index = body["attempt_index"]
    if type(index) is not int or index not in (0, 1):
        raise StaffingError("staffing_invalid_evidence", "attempt_index")
    digest = _validate_digest(body["input_digest"], "input_digest")
    assert digest is not None
    return AttemptStartedEvidence(
        request_id,
        index,
        parse_attempt_route_evidence(body["route"]),
        digest,
        _validate_positive_number(body["timeout_s"], "timeout_s"),
    )


def parse_attempt_finished_evidence(value: object) -> AttemptFinishedEvidence:
    body = require_exact_object(
        value,
        frozenset(
            {
                "request_id",
                "attempt_index",
                "route",
                "status",
                "failure_code",
                "retryable",
                "security_failure",
                "provider_request_id",
                "finish_reason",
                "output_digest",
                "input_tokens",
                "output_tokens",
            }
        ),
        code="staffing_invalid_evidence",
        detail="attempt finished fields",
    )
    request_id = validate_identifier(body["request_id"], "request_id")
    index = body["attempt_index"]
    if type(index) is not int or index not in (0, 1):
        raise StaffingError("staffing_invalid_evidence", "attempt_index")
    statuses = GENERATION_TERMINALS - {"RESULT_UNKNOWN", "NO_VALID_SUGGESTION"}
    status = body["status"]
    if type(status) is not str or status not in statuses:
        raise StaffingError("staffing_invalid_evidence", "status")
    if type(body["retryable"]) is not bool or type(body["security_failure"]) is not bool:
        raise StaffingError("staffing_invalid_evidence", "attempt flags")
    failure = _validate_failure_code(body["failure_code"])
    provider_request_id = _validate_ascii_text(
        body["provider_request_id"], "provider_request_id", maximum=160, optional=True
    )
    finish_reason = _validate_ascii_text(
        body["finish_reason"], "finish_reason", maximum=160, optional=True
    )
    output_digest = _validate_digest(body["output_digest"], "output_digest", optional=True)
    tokens: list[int | None] = []
    for field in ("input_tokens", "output_tokens"):
        raw = body[field]
        tokens.append(
            None if raw is None else _validate_nonnegative_int(raw, field, maximum=1_000_000)
        )
    return AttemptFinishedEvidence(
        request_id,
        index,
        parse_attempt_route_evidence(body["route"]),
        status,
        failure,
        body["retryable"],
        body["security_failure"],
        provider_request_id,
        finish_reason,
        output_digest,
        tokens[0],
        tokens[1],
    )


def parse_result_evidence(value: object) -> ResultEvidence:
    body = require_exact_object(
        value,
        frozenset(
            {
                "request_id",
                "status",
                "failure_code",
                "selected_provider",
                "selected_model_id",
                "provider_request_id",
                "finish_reason",
                "input_digest",
                "output_digest",
                "attempts",
                "candidate_count",
                "bounded_summary",
            }
        ),
        code="staffing_invalid_evidence",
        detail="result fields",
    )
    request_id = validate_identifier(body["request_id"], "request_id")
    status = body["status"]
    statuses = GENERATION_TERMINALS - {"NO_VALID_SUGGESTION"}
    if type(status) is not str or status not in statuses:
        raise StaffingError("staffing_invalid_evidence", "status")
    failure = _validate_failure_code(body["failure_code"])
    provider = body["selected_provider"]
    if provider is not None and (
        type(provider) is not str or provider not in {"KIMI", "OPENAI", "ANTHROPIC"}
    ):
        raise StaffingError("staffing_invalid_evidence", "selected_provider")
    model = _validate_ascii_text(
        body["selected_model_id"], "selected_model_id", maximum=128, optional=True
    )
    if (provider is None) != (model is None):
        raise StaffingError("staffing_invalid_evidence", "selected provider/model")
    provider_request_id = _validate_ascii_text(
        body["provider_request_id"], "provider_request_id", maximum=160, optional=True
    )
    finish_reason = _validate_ascii_text(
        body["finish_reason"], "finish_reason", maximum=160, optional=True
    )
    input_digest = _validate_digest(body["input_digest"], "input_digest")
    output_digest = _validate_digest(body["output_digest"], "output_digest", optional=True)
    raw_attempts = body["attempts"]
    if type(raw_attempts) is not list or len(raw_attempts) > 2:
        raise StaffingError("staffing_invalid_evidence", "attempts")
    attempts = tuple(parse_attempt_finished_evidence(item) for item in raw_attempts)
    if tuple(item.attempt_index for item in attempts) != tuple(range(len(attempts))):
        raise StaffingError("staffing_invalid_evidence", "attempt order")
    count = _validate_nonnegative_int(body["candidate_count"], "candidate_count", maximum=2)
    summary = _validate_plain_text(
        body["bounded_summary"],
        "bounded_summary",
        maximum=280,
        minimum=0,
        optional=True,
    )
    assert input_digest is not None
    return ResultEvidence(
        request_id,
        status,
        failure,
        provider,
        model,
        provider_request_id,
        finish_reason,
        input_digest,
        output_digest,
        attempts,
        count,
        summary,
    )
