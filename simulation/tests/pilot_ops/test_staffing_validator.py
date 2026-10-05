"""Deterministic validation for decoded staffing candidate patches."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest

from nxt_pilot_ops.serialization import canonical_json_bytes, stable_digest, to_primitive
from nxt_pilot_ops.staffing.contracts import (
    AddOperation,
    Assignment,
    AssignmentRule,
    CoverageGap,
    CoverageWindow,
    ExceptionRecordedPayload,
    PROMPT_TEMPLATE_VERSION,
    RemoveOperation,
    RosterImportedPayload,
    StaffingError,
    StaffingEvent,
    StaffingException,
    StaffingHistory,
)
from nxt_pilot_ops.staffing.exceptions import exception_digest, normalize_exception
from nxt_pilot_ops.staffing.plans import _validate_assignment_tuple, build_staffing_basis
from nxt_pilot_ops.staffing.roster import validate_roster_import
from nxt_pilot_ops.staffing.validator import (
    REJECTION_CODES,
    CandidatePatch,
    CandidateValidation,
    assignment_from_candidate,
    basis_exceeds_daily_limit,
    basis_has_required_skills,
    basis_is_available,
    basis_is_eligible,
    basis_knows_role_area,
    basis_overlaps_exception,
    basis_overlaps_existing,
    canonical_schedule,
    coverage_gaps,
    in_site_zone,
    schedule_digest,
    validate_alias_maps,
    validate_candidate,
    validate_candidates,
)

from .staffing_fixtures import roster_import_request


SERVICE_DATE = date(2026, 10, 5)
UTC = timezone.utc
SITE_ZONE = ZoneInfo("Asia/Shanghai")


class _ExplodingTimezone(tzinfo):
    def __init__(self, error_type: type[BaseException]) -> None:
        self._error_type = error_type

    def utcoffset(self, value):
        raise self._error_type("hostile timezone")

    def dst(self, value):
        return timedelta(0)

    def tzname(self, value):
        return "HOSTILE"


class _HostileClockError(Exception):
    pass


class _DomainErrorTimezone(tzinfo):
    def utcoffset(self, value):
        raise StaffingError("sentinel_staffing_error", "hostile timezone")

    def dst(self, value):
        return timedelta(0)

    def tzname(self, value):
        return "DOMAIN_ERROR"


class _StatefulTimezone(tzinfo):
    def __init__(self, failure: BaseException, *, fail_on: int) -> None:
        self._failure = failure
        self._fail_on = fail_on
        self.calls = 0

    def utcoffset(self, value):
        self.calls += 1
        if self.calls == self._fail_on:
            raise self._failure
        return timedelta(hours=8)

    def dst(self, value):
        return timedelta(0)

    def tzname(self, value):
        return "STATEFUL"


def _event(event_type: str, payload: object, sequence: int) -> StaffingEvent:
    return StaffingEvent(
        event_type,
        f"event-{sequence}",
        sequence,
        "pilot-course-a",
        "pilot-a-edge-task-sim-v0",
        datetime(2026, 10, 5, 0, sequence, tzinfo=UTC),
        None,
        payload,
    )


def _roster_event(roster, sequence: int = 1) -> StaffingEvent:
    return _event(
        "roster_imported",
        RosterImportedPayload(
            f"roster-{sequence}",
            "a" * 64,
            roster.revision,
            roster.roster_digest,
            roster,
            "course-manager",
            "weekly.csv",
        ),
        sequence,
    )


def _roster(payload: dict[str, object] | None = None):
    request = roster_import_request() if payload is None else payload
    return validate_roster_import(
        request,
        site_id=str(request["site_id"]),
        deployment_id=str(request["deployment_id"]),
        site_timezone=str(request["site_timezone"]),
    )


def _basis(payload: dict[str, object] | None = None):
    roster = _roster(payload)
    service_date = date.fromisoformat(str((payload or roster_import_request())["effective_from_local_date"]))
    return build_staffing_basis(
        StaffingHistory((_roster_event(roster),)), service_date
    )


def _two_worker_payload(
    *,
    second_skills: list[str] | None = None,
    second_eligibility: list[dict[str, str]] | None = None,
    second_availability: tuple[str, str] = ("08:00", "17:00"),
    second_limit: int = 480,
    second_assignment: bool = False,
) -> dict[str, object]:
    payload = roster_import_request()
    payload["workers"].append(
        {
            "staff_id": "staff-002",
            "display_name": "本地员工乙",
            "skill_codes": ["BALL_PICKING"] if second_skills is None else second_skills,
            "eligibility": (
                [{"role_code": "RANGE_ATTENDANT", "area_code": "RANGE_A"}]
                if second_eligibility is None
                else second_eligibility
            ),
            "max_daily_minutes": second_limit,
        }
    )
    payload["availability"].append(
        {
            "staff_id": "staff-002",
            "weekday": 0,
            "start_local": second_availability[0],
            "end_local": second_availability[1],
        }
    )
    if second_assignment:
        payload["regular_assignments"].append(
            {
                "staff_id": "staff-002",
                "weekday": 0,
                "role_code": "RANGE_ATTENDANT",
                "area_code": "RANGE_A",
                "start_local": "09:00",
                "end_local": "17:00",
            }
        )
    return payload


def _aliases(basis):
    return (
        tuple(
            (f"worker_{index}", worker.staff_id)
            for index, worker in enumerate(basis.workers, 1)
        ),
        tuple(
            (f"assignment_{index}", assignment.assignment_id)
            for index, assignment in enumerate(basis.assignments, 1)
        ),
    )


def _operation(
    *,
    worker_alias: str = "worker_1",
    role_code: str = "RANGE_ATTENDANT",
    area_code: str = "RANGE_A",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> AddOperation:
    return AddOperation(
        "ADD",
        worker_alias,
        role_code,
        area_code,
        start_at or datetime(2026, 10, 5, 9, tzinfo=SITE_ZONE),
        end_at or datetime(2026, 10, 5, 17, tzinfo=SITE_ZONE),
    )


def _candidate(
    basis,
    *,
    add: AddOperation | None = None,
    operations: tuple[RemoveOperation | AddOperation, ...] | None = None,
    candidate_index: int = 1,
) -> CandidatePatch:
    _, assignment_aliases = _aliases(basis)
    if operations is None:
        operations = (
            RemoveOperation("REMOVE", assignment_aliases[0][0]),
            add or _operation(),
        )
    return CandidatePatch(candidate_index, operations, "coverage preserved", ())


def _validate(basis, candidate: CandidatePatch, *, template: str = PROMPT_TEMPLATE_VERSION):
    worker_aliases, assignment_aliases = _aliases(basis)
    return validate_candidate(
        basis,
        candidate,
        worker_alias_to_staff_id=worker_aliases,
        assignment_alias_to_assignment_id=assignment_aliases,
        prompt_template_version=template,
    )


def _basis_with_late_exception():
    roster = _roster()
    exception = normalize_exception(
        {
            "schema": "nxt-staffing-exception/v1",
            "request_id": "exception-001",
            "expected_roster_revision": 1,
            "expected_exception_set_revision": 0,
            "service_date": SERVICE_DATE.isoformat(),
            "staff_id": "staff-001",
            "kind": "LATE",
            "time_local": "10:00",
            "operator": "course-manager",
            "note": None,
        },
        roster=roster,
    )
    recorded = _event(
        "exception_recorded",
        ExceptionRecordedPayload(
            "exception-request",
            "b" * 64,
            exception.exception_id,
            exception.service_date,
            1,
            exception_digest(exception),
            exception,
            "course-manager",
        ),
        2,
    )
    return build_staffing_basis(
        StaffingHistory((_roster_event(roster), recorded)), SERVICE_DATE
    )


def _dst_basis(
    service_date: date,
    timezone_name: str,
    availability: tuple[str, str],
    *,
    maximum: int,
):
    payload = roster_import_request()
    weekday = service_date.weekday()
    payload.update(
        {
            "site_timezone": timezone_name,
            "effective_from_local_date": service_date.isoformat(),
            "workers": [
                {
                    "staff_id": "staff-001",
                    "display_name": "DST worker",
                    "skill_codes": ["BALL_PICKING"],
                    "eligibility": [
                        {"role_code": "RANGE_ATTENDANT", "area_code": "RANGE_A"}
                    ],
                    "max_daily_minutes": maximum,
                }
            ],
            "availability": [
                {
                    "staff_id": "staff-001",
                    "weekday": weekday,
                    "start_local": availability[0],
                    "end_local": availability[1],
                }
            ],
            "regular_assignments": [],
            "coverage": [
                {
                    "weekday": weekday,
                    "role_code": "RANGE_ATTENDANT",
                    "area_code": "RANGE_A",
                    "start_local": "00:00",
                    "end_local": "00:01",
                    "minimum_staff": 1,
                }
            ],
        }
    )
    return _basis(payload)


def test_rejection_vocabulary_is_closed_and_exact():
    assert REJECTION_CODES == frozenset(
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


def test_candidate_records_are_frozen_slotted_and_validate_coherence():
    basis = _basis()
    candidate = _candidate(basis)
    result = _validate(basis, candidate)
    assert candidate.__slots__ == (
        "candidate_index",
        "operations",
        "rationale",
        "operational_warnings",
    )
    assert result.__slots__ == (
        "candidate_index",
        "valid",
        "rejection_codes",
        "coverage_gaps",
        "materialized_schedule",
        "materialized_schedule_digest",
    )
    assert not hasattr(candidate, "__dict__")
    with pytest.raises(FrozenInstanceError):
        candidate.rationale = "changed"
    assert result.valid is True
    assert result.rejection_codes == result.coverage_gaps == ()
    assert result.materialized_schedule_digest == schedule_digest(
        result.materialized_schedule
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda basis: CandidatePatch(True, (), "ok", ()),
        lambda basis: CandidatePatch(0, (), "ok", ()),
        lambda basis: CandidatePatch(1, [], "ok", ()),
        lambda basis: CandidatePatch(1, (object(),), "ok", ()),
        lambda basis: CandidatePatch(
            1, (RemoveOperation("ADD", "assignment_1"),), "ok", ()
        ),
        lambda basis: CandidatePatch(
            1, (replace(_operation(), operation="REMOVE"),), "ok", ()
        ),
        lambda basis: CandidatePatch(
            1, (replace(_operation(), worker_alias=1),), "ok", ()
        ),
        lambda basis: CandidatePatch(
            1, (replace(_operation(), role_code="bad"),), "ok", ()
        ),
        lambda basis: CandidatePatch(
            1, (replace(_operation(), area_code="bad"),), "ok", ()
        ),
        lambda basis: CandidatePatch(
            1,
            (replace(_operation(), start_at=datetime(2026, 10, 5, 9)),),
            "ok",
            (),
        ),
        lambda basis: CandidatePatch(1, (), 1, ()),
        lambda basis: CandidatePatch(1, (), "x" * 281, ()),
        lambda basis: CandidatePatch(1, (), "bad\n", ()),
        lambda basis: CandidatePatch(1, (), "ok", []),
        lambda basis: CandidatePatch(1, (), "ok", ("x",) * 6),
        lambda basis: CandidatePatch(1, (), "ok", ("x" * 201,)),
        lambda basis: CandidatePatch(1, (), "ok", ("bad\x00",)),
        lambda basis: CandidatePatch(1, (), "ok", (1,)),
    ],
)
def test_candidate_patch_direct_construction_fails_closed(factory):
    with pytest.raises(StaffingError) as caught:
        factory(_basis())
    assert caught.value.code == "invalid_candidate_set"


def test_candidate_text_allows_empty_and_plain_html_without_interpretation():
    candidate = CandidatePatch(1, (), "", ("<b>plain text</b>", "**also plain**"))
    assert candidate.rationale == ""
    assert candidate.operational_warnings == (
        "<b>plain text</b>",
        "**also plain**",
    )


def test_candidate_validation_direct_construction_accepts_valid_empty_schedule():
    digest = stable_digest(())
    result = CandidateValidation(2, True, (), (), (), digest)
    assert result.materialized_schedule == ()
    assert result.materialized_schedule_digest == digest


def test_candidate_validation_rejects_overlapping_materialized_schedule():
    first = Assignment(
        "assignment-first",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 9, tzinfo=SITE_ZONE),
        datetime(2026, 10, 5, 11, tzinfo=SITE_ZONE),
    )
    second = Assignment(
        "assignment-second",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
        datetime(2026, 10, 5, 12, tzinfo=SITE_ZONE),
    )
    schedule = (first, second)
    with pytest.raises(StaffingError) as caught:
        CandidateValidation(1, True, (), (), schedule, schedule_digest(schedule))
    assert caught.value.code == "staffing_invalid_evidence"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: CandidateValidation(True, True, (), (), (), stable_digest(())),
        lambda: CandidateValidation(1, 1, (), (), (), stable_digest(())),
        lambda: CandidateValidation(1, True, ("UNKNOWN_ROLE_AREA",), (), None, None),
        lambda: CandidateValidation(1, False, (), (), None, None),
        lambda: CandidateValidation(1, False, ("NOT_CLOSED",), (), None, None),
        lambda: CandidateValidation(
            1,
            False,
            ("UNKNOWN_ROLE_AREA", "OUTSIDE_AVAILABILITY"),
            (),
            None,
            None,
        ),
        lambda: CandidateValidation(
            1,
            False,
            ("UNKNOWN_ROLE_AREA", "UNKNOWN_ROLE_AREA"),
            (),
            None,
            None,
        ),
        lambda: CandidateValidation(1, False, ("COVERAGE_GAP",), (), None, None),
        lambda: CandidateValidation(
            1,
            False,
            ("UNKNOWN_ROLE_AREA",),
            (
                CoverageGap(
                    "RANGE_ATTENDANT",
                    "RANGE_A",
                    datetime(2026, 10, 5, 9, tzinfo=SITE_ZONE),
                    datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
                    1,
                    0,
                ),
            ),
            None,
            None,
        ),
        lambda: CandidateValidation(1, True, (), (), (), "f" * 64),
        lambda: CandidateValidation(1, False, ("UNKNOWN_ROLE_AREA",), (), (), stable_digest(())),
    ],
)
def test_candidate_validation_direct_construction_rejects_incoherence(factory):
    with pytest.raises(StaffingError) as caught:
        factory()
    assert caught.value.code == "staffing_invalid_evidence"


@pytest.mark.parametrize(
    "gap",
    [
        CoverageGap("bad", "RANGE_A", datetime(2026, 10, 5, 9, tzinfo=UTC), datetime(2026, 10, 5, 10, tzinfo=UTC), 1, 0),
        CoverageGap("RANGE_ATTENDANT", "bad", datetime(2026, 10, 5, 9, tzinfo=UTC), datetime(2026, 10, 5, 10, tzinfo=UTC), 1, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", datetime(2026, 10, 5, 9), datetime(2026, 10, 5, 10, tzinfo=UTC), 1, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", datetime(2026, 10, 5, 10, tzinfo=UTC), datetime(2026, 10, 5, 9, tzinfo=UTC), 1, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", datetime(2026, 10, 5, 9, 0, 1, tzinfo=UTC), datetime(2026, 10, 5, 10, tzinfo=UTC), 1, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", datetime(2026, 10, 5, 9, tzinfo=UTC), datetime(2026, 10, 5, 10, tzinfo=UTC), True, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", datetime(2026, 10, 5, 9, tzinfo=UTC), datetime(2026, 10, 5, 10, tzinfo=UTC), 0, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", datetime(2026, 10, 5, 9, tzinfo=UTC), datetime(2026, 10, 5, 10, tzinfo=UTC), 1, 1),
    ],
)
def test_candidate_validation_rejects_malformed_coverage_gap(gap):
    with pytest.raises(StaffingError) as caught:
        CandidateValidation(1, False, ("COVERAGE_GAP",), (gap,), None, None)
    assert caught.value.code == "staffing_invalid_evidence"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda workers, assignments: (list(workers), assignments),
        lambda workers, assignments: ("worker_1", assignments),
        lambda workers, assignments: ((("worker_1", "staff-001"), ["bad"]), assignments),
        lambda workers, assignments: ((("worker_1",),), assignments),
        lambda workers, assignments: (((1, "staff-001"),), assignments),
        lambda workers, assignments: ((workers[0], workers[0]), assignments),
        lambda workers, assignments: (
            (("worker_1", workers[0][1]), ("worker_2", workers[0][1])), assignments
        ),
        lambda workers, assignments: ((), assignments),
        lambda workers, assignments: (
            (("worker_1", "staff-001"), ("worker_x", "staff-extra")), assignments
        ),
        lambda workers, assignments: (workers, (("worker_1", assignments[0][1]),)),
        lambda workers, assignments: (
            workers, (("assignment_1", "assignment-missing"),)
        ),
        lambda workers, assignments: (
            workers, (("assignment_1", assignments[0][1]), ("assignment_2", assignments[0][1]))
        ),
    ],
)
def test_alias_maps_reject_malformed_duplicate_colliding_or_wrong_targets(mutate):
    basis = _basis(_two_worker_payload())
    workers, assignments = _aliases(basis)
    bad_workers, bad_assignments = mutate(workers, assignments)
    with pytest.raises(StaffingError) as caught:
        validate_alias_maps(basis, bad_workers, bad_assignments)
    assert caught.value.code == "staffing_invalid_evidence"


def test_alias_maps_accept_exact_empty_assignment_map():
    payload = _two_worker_payload()
    payload["regular_assignments"] = []
    basis = _basis(payload)
    workers, assignments = _aliases(basis)
    worker_map, assignment_map = validate_alias_maps(basis, workers, assignments)
    assert set(worker_map.values()) == {"staff-001", "staff-002"}
    assert assignment_map == {}


def test_alias_maps_bind_observationally_identical_worker_targets_by_position():
    payload = _two_worker_payload()
    payload["regular_assignments"] = []
    basis = _basis(payload)
    workers, assignments = _aliases(basis)
    first, second = basis.workers
    assert (
        first.skill_codes,
        first.eligibility,
        first.max_daily_minutes,
    ) == (
        second.skill_codes,
        second.eligibility,
        second.max_daily_minutes,
    )
    assert tuple(
        (row.weekday, row.start_local, row.end_local)
        for row in basis.availability
        if row.staff_id == first.staff_id
    ) == tuple(
        (row.weekday, row.start_local, row.end_local)
        for row in basis.availability
        if row.staff_id == second.staff_id
    )
    assert assignments == ()
    swapped = (
        (workers[0][0], workers[1][1]),
        (workers[1][0], workers[0][1]),
    )
    with pytest.raises(StaffingError) as caught:
        validate_alias_maps(basis, swapped, assignments)
    assert caught.value.code == "staffing_invalid_evidence"


def test_alias_maps_bind_assignment_targets_by_position():
    basis = _basis(_two_worker_payload(second_assignment=True))
    workers, assignments = _aliases(basis)
    assert len(assignments) == 2
    swapped = (
        (assignments[0][0], assignments[1][1]),
        (assignments[1][0], assignments[0][1]),
    )
    with pytest.raises(StaffingError) as caught:
        validate_alias_maps(basis, workers, swapped)
    assert caught.value.code == "staffing_invalid_evidence"


def test_unknown_alias_and_duplicate_remove_are_structural_candidate_failures():
    basis = _basis()
    bad_candidates = (
        CandidatePatch(1, (replace(_operation(), worker_alias="missing"),), "", ()),
        CandidatePatch(1, (RemoveOperation("REMOVE", "missing"),), "", ()),
        CandidatePatch(
            1,
            (
                RemoveOperation("REMOVE", "assignment_1"),
                RemoveOperation("REMOVE", "assignment_1"),
            ),
            "",
            (),
        ),
    )
    for candidate in bad_candidates:
        with pytest.raises(StaffingError) as caught:
            _validate(basis, candidate, template="mismatched-too")
        assert caught.value.code == "invalid_candidate_set"


def test_template_mismatch_short_circuits_all_semantic_checks():
    basis = _basis()
    invalid_semantics = replace(
        _operation(),
        role_code="UNKNOWN_ROLE",
        start_at=datetime(2026, 10, 5, 9, 0, 1, tzinfo=SITE_ZONE),
        end_at=datetime(2026, 10, 5, 8, tzinfo=SITE_ZONE),
    )
    result = _validate(basis, _candidate(basis, add=invalid_semantics), template="old/v0")
    assert result == CandidateValidation(
        1,
        False,
        ("PROMPT_TEMPLATE_VERSION_MISMATCH",),
        (),
        None,
        None,
    )


@pytest.mark.parametrize(
    ("basis_factory", "operation_factory", "code"),
    [
        (
            _basis,
            lambda: replace(
                _operation(), end_at=datetime(2026, 10, 5, 8, tzinfo=SITE_ZONE)
            ),
            "INVALID_INTERVAL",
        ),
        (
            _basis,
            lambda: replace(
                _operation(), start_at=datetime(2026, 10, 5, 9, 0, 1, tzinfo=SITE_ZONE)
            ),
            "INVALID_MINUTE_PRECISION",
        ),
        (
            _basis,
            lambda: replace(
                _operation(),
                start_at=datetime(2026, 10, 5, 9, tzinfo=timezone(timedelta(hours=9))),
                end_at=datetime(2026, 10, 5, 17, tzinfo=timezone(timedelta(hours=9))),
            ),
            "OFFSET_TIMEZONE_MISMATCH",
        ),
        (
            _basis,
            lambda: replace(
                _operation(),
                start_at=datetime(2026, 10, 4, 9, tzinfo=SITE_ZONE),
                end_at=datetime(2026, 10, 4, 17, tzinfo=SITE_ZONE),
            ),
            "OUTSIDE_SERVICE_DATE",
        ),
        (
            _basis,
            lambda: replace(_operation(), role_code="MARSHAL"),
            "UNKNOWN_ROLE_AREA",
        ),
        (
            lambda: _basis(
                _two_worker_payload(second_eligibility=[], second_skills=["BALL_PICKING"])
            ),
            lambda: replace(_operation(), worker_alias="worker_2"),
            "INELIGIBLE_ROLE_AREA",
        ),
        (
            lambda: _basis(_two_worker_payload(second_skills=[])),
            lambda: replace(_operation(), worker_alias="worker_2"),
            "MISSING_REQUIRED_SKILL",
        ),
        (
            lambda: _basis(_two_worker_payload(second_availability=("10:00", "17:00"))),
            lambda: replace(_operation(), worker_alias="worker_2"),
            "OUTSIDE_AVAILABILITY",
        ),
        (
            _basis_with_late_exception,
            lambda: replace(
                _operation(),
                start_at=datetime(2026, 10, 5, 9, tzinfo=SITE_ZONE),
                end_at=datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
            ),
            "OVERLAPS_EXCEPTION",
        ),
        (
            _basis,
            lambda: replace(
                _operation(),
                start_at=datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
                end_at=datetime(2026, 10, 5, 11, tzinfo=SITE_ZONE),
            ),
            "OVERLAPPING_ASSIGNMENTS",
        ),
        (
            lambda: _basis(_two_worker_payload(second_limit=60)),
            lambda: replace(_operation(), worker_alias="worker_2"),
            "MAX_DAILY_MINUTES_EXCEEDED",
        ),
    ],
)
def test_each_noncoverage_semantic_rejection_code(
    basis_factory, operation_factory, code
):
    basis = basis_factory()
    operation = operation_factory()
    if code == "OVERLAPPING_ASSIGNMENTS":
        candidate = CandidatePatch(1, (operation,), "", ())
    else:
        candidate = _candidate(basis, add=operation)
    result = _validate(basis, candidate)
    assert result == CandidateValidation(1, False, (code,), (), None, None)


def test_coverage_gap_is_the_only_code_and_returns_exact_unmerged_segment():
    basis = _basis()
    result = _validate(
        basis,
        CandidatePatch(1, (RemoveOperation("REMOVE", "assignment_1"),), "", ()),
    )
    assert result == CandidateValidation(
        1,
        False,
        ("COVERAGE_GAP",),
        (
            CoverageGap(
                "RANGE_ATTENDANT",
                "RANGE_A",
                datetime(2026, 10, 5, 9, tzinfo=SITE_ZONE),
                datetime(2026, 10, 5, 17, tzinfo=SITE_ZONE),
                1,
                0,
            ),
        ),
        None,
        None,
    )


def test_semantic_codes_across_adds_are_sorted_unique():
    basis = _basis(_two_worker_payload(second_availability=("10:00", "17:00")))
    candidate = CandidatePatch(
        1,
        (
            RemoveOperation("REMOVE", "assignment_1"),
            replace(_operation(), worker_alias="worker_2", role_code="MARSHAL"),
            replace(
                _operation(),
                worker_alias="worker_2",
                start_at=datetime(2026, 10, 5, 9, tzinfo=SITE_ZONE),
                end_at=datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
            ),
            replace(_operation(), worker_alias="worker_2", role_code="MARSHAL"),
        ),
        "",
        (),
    )
    assert _validate(basis, candidate).rejection_codes == (
        "OUTSIDE_AVAILABILITY",
        "UNKNOWN_ROLE_AREA",
    )


def test_all_removes_apply_before_adds_even_when_operations_are_interleaved():
    basis = _basis()
    candidate = CandidatePatch(
        1,
        (
            _operation(),
            RemoveOperation("REMOVE", "assignment_1"),
        ),
        "",
        (),
    )
    result = _validate(basis, candidate)
    assert result.valid
    assert result.materialized_schedule[0] is not basis.assignments[0]
    assert result.materialized_schedule[0].staff_id == "staff-001"


def test_unmentioned_baseline_assignment_is_reused_by_identity():
    basis = _basis(_two_worker_payload(second_assignment=True))
    worker_aliases, assignment_aliases = _aliases(basis)
    first = next(item for item in basis.assignments if item.staff_id == "staff-001")
    second = next(item for item in basis.assignments if item.staff_id == "staff-002")
    remove_alias = next(
        alias for alias, target in assignment_aliases if target == first.assignment_id
    )
    worker_alias = next(alias for alias, target in worker_aliases if target == "staff-001")
    result = validate_candidate(
        basis,
        CandidatePatch(
            1,
            (RemoveOperation("REMOVE", remove_alias), replace(_operation(), worker_alias=worker_alias)),
            "",
            (),
        ),
        worker_alias_to_staff_id=worker_aliases,
        assignment_alias_to_assignment_id=assignment_aliases,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
    )
    retained = next(item for item in result.materialized_schedule if item.staff_id == "staff-002")
    assert retained is second


def test_one_worker_may_receive_multiple_adjacent_adds():
    basis = _basis()
    candidate = CandidatePatch(
        1,
        (
            RemoveOperation("REMOVE", "assignment_1"),
            replace(
                _operation(), end_at=datetime(2026, 10, 5, 12, tzinfo=SITE_ZONE)
            ),
            replace(
                _operation(), start_at=datetime(2026, 10, 5, 12, tzinfo=SITE_ZONE)
            ),
        ),
        "",
        (),
    )
    result = _validate(basis, candidate)
    assert result.valid
    assert tuple(
        (
            item.start_at.astimezone(SITE_ZONE).hour,
            item.end_at.astimezone(SITE_ZONE).hour,
        )
        for item in result.materialized_schedule
    ) == ((9, 12), (12, 17))


def test_two_adds_for_same_worker_overlap_each_other():
    basis = _basis()
    candidate = CandidatePatch(
        1,
        (
            RemoveOperation("REMOVE", "assignment_1"),
            replace(_operation(), end_at=datetime(2026, 10, 5, 13, tzinfo=SITE_ZONE)),
            replace(_operation(), start_at=datetime(2026, 10, 5, 12, tzinfo=SITE_ZONE)),
        ),
        "",
        (),
    )
    assert _validate(basis, candidate).rejection_codes == (
        "OVERLAPPING_ASSIGNMENTS",
    )


def test_cumulative_daily_limit_uses_prior_accepted_adds():
    basis = _basis(_two_worker_payload(second_limit=90))
    candidate = CandidatePatch(
        1,
        (
            RemoveOperation("REMOVE", "assignment_1"),
            replace(
                _operation(),
                worker_alias="worker_2",
                start_at=datetime(2026, 10, 5, 9, tzinfo=SITE_ZONE),
                end_at=datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
            ),
            replace(
                _operation(),
                worker_alias="worker_2",
                start_at=datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
                end_at=datetime(2026, 10, 5, 11, tzinfo=SITE_ZONE),
            ),
        ),
        "",
        (),
    )
    assert _validate(basis, candidate).rejection_codes == (
        "MAX_DAILY_MINUTES_EXCEEDED",
    )


def test_semantic_helpers_have_exact_half_open_behavior():
    basis = _basis_with_late_exception()
    operation = replace(
        _operation(),
        start_at=datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
        end_at=datetime(2026, 10, 5, 11, tzinfo=SITE_ZONE),
    )
    assert basis_knows_role_area(basis, "RANGE_ATTENDANT", "RANGE_A")
    assert not basis_knows_role_area(basis, "MARSHAL", "RANGE_A")
    assert basis_is_eligible(basis, "staff-001", "RANGE_ATTENDANT", "RANGE_A")
    assert basis_has_required_skills(
        basis, "staff-001", "RANGE_ATTENDANT", "RANGE_A"
    )
    assert basis_is_available(basis, operation, "staff-001")
    assert not basis_overlaps_exception(basis, "staff-001", operation)
    overlapping = replace(
        operation, start_at=datetime(2026, 10, 5, 9, 59, tzinfo=SITE_ZONE)
    )
    assert basis_overlaps_exception(basis, "staff-001", overlapping)
    working = {item.assignment_id: item for item in basis.assignments}
    assert basis_overlaps_existing("staff-001", operation, working)
    assert not basis_exceeds_daily_limit(basis, "staff-001", operation, {})


def test_site_zone_round_trip_accepts_both_folds_and_rejects_nonexistent_minute():
    zone = ZoneInfo("America/New_York")
    fold_zero = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    fold_one = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    nonexistent = datetime(
        2026, 3, 8, 2, 30, tzinfo=timezone(timedelta(hours=-5))
    )
    assert fold_zero.utcoffset() != fold_one.utcoffset()
    assert in_site_zone(fold_zero, "America/New_York")
    assert in_site_zone(fold_one, "America/New_York")
    assert not in_site_zone(nonexistent, "America/New_York")


@pytest.mark.parametrize(
    "error_type",
    [TypeError, RuntimeError, KeyError, OSError, _HostileClockError],
)
def test_hostile_candidate_timezone_never_leaks_native_errors(error_type):
    hostile = _ExplodingTimezone(error_type)
    operation = AddOperation(
        "ADD",
        "worker_1",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 9, tzinfo=hostile),
        datetime(2026, 10, 5, 10, tzinfo=hostile),
    )
    with pytest.raises(StaffingError) as caught:
        CandidatePatch(1, (operation,), "", ())
    assert caught.value.code == "invalid_candidate_set"
    assert not in_site_zone(operation.start_at, "Asia/Shanghai")


@pytest.mark.parametrize(
    "error_type",
    [KeyboardInterrupt, SystemExit, GeneratorExit],
)
def test_hostile_candidate_timezone_does_not_swallow_base_exceptions(error_type):
    hostile = _ExplodingTimezone(error_type)
    timestamp = datetime(2026, 10, 5, 9, tzinfo=hostile)
    operation = AddOperation(
        "ADD",
        "worker_1",
        "RANGE_ATTENDANT",
        "RANGE_A",
        timestamp,
        datetime(2026, 10, 5, 10, tzinfo=hostile),
    )
    with pytest.raises(error_type):
        CandidatePatch(1, (operation,), "", ())
    with pytest.raises(error_type):
        in_site_zone(timestamp, "Asia/Shanghai")


def test_hostile_timezone_preserves_intentional_staffing_errors():
    hostile = _DomainErrorTimezone()
    timestamp = datetime(2026, 10, 5, 9, tzinfo=hostile)
    operation = AddOperation(
        "ADD",
        "worker_1",
        "RANGE_ATTENDANT",
        "RANGE_A",
        timestamp,
        datetime(2026, 10, 5, 10, tzinfo=hostile),
    )
    with pytest.raises(StaffingError) as candidate_error:
        CandidatePatch(1, (operation,), "", ())
    assert candidate_error.value.code == "sentinel_staffing_error"
    with pytest.raises(StaffingError) as zone_error:
        in_site_zone(timestamp, "Asia/Shanghai")
    assert zone_error.value.code == "sentinel_staffing_error"


def test_extreme_offset_underflow_is_a_stable_candidate_rejection():
    basis = _basis()
    extreme = replace(
        _operation(),
        start_at=datetime(
            1, 1, 1, 0, tzinfo=timezone(timedelta(hours=14))
        ),
        end_at=datetime(
            1, 1, 1, 1, tzinfo=timezone(timedelta(hours=14))
        ),
    )
    result = _validate(basis, _candidate(basis, add=extreme))
    assert result == CandidateValidation(
        1, False, ("OFFSET_TIMEZONE_MISMATCH",), (), None, None
    )


def test_availability_contains_both_ambiguous_folds_by_instant():
    basis = _dst_basis(
        date(2026, 11, 1), "America/New_York", ("00:00", "03:00"), maximum=300
    )
    zone = ZoneInfo("America/New_York")
    for fold in (0, 1):
        operation = AddOperation(
            "ADD",
            "worker_1",
            "RANGE_ATTENDANT",
            "RANGE_A",
            datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=fold),
            datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=fold),
        )
        assert in_site_zone(operation.start_at, basis.site_timezone)
        assert basis_is_available(basis, operation, "staff-001")


def test_daily_limit_uses_elapsed_utc_minutes_across_spring_and_fall_dst():
    spring = _dst_basis(
        date(2026, 3, 8), "America/New_York", ("01:00", "04:00"), maximum=120
    )
    spring_operation = AddOperation(
        "ADD",
        "worker_1",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 3, 8, 1, tzinfo=timezone(timedelta(hours=-5))),
        datetime(2026, 3, 8, 4, tzinfo=timezone(timedelta(hours=-4))),
    )
    assert not basis_exceeds_daily_limit(spring, "staff-001", spring_operation, {})

    fall = _dst_basis(
        date(2026, 11, 1), "America/New_York", ("00:00", "03:00"), maximum=180
    )
    fall_operation = AddOperation(
        "ADD",
        "worker_1",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 11, 1, 0, tzinfo=timezone(timedelta(hours=-4))),
        datetime(2026, 11, 1, 3, tzinfo=timezone(timedelta(hours=-5))),
    )
    assert basis_exceeds_daily_limit(fall, "staff-001", fall_operation, {})


def test_fallback_cross_fold_positive_interval_materializes_in_utc():
    basis = _dst_basis(
        date(2026, 11, 1), "America/New_York", ("00:00", "03:00"), maximum=300
    )
    zone = ZoneInfo("America/New_York")
    operation = AddOperation(
        "ADD",
        "worker_1",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0),
        datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1),
    )
    custom = replace(
        basis,
        coverage=(
            CoverageWindow(
                "RANGE_ATTENDANT",
                "RANGE_A",
                datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
                datetime(2026, 11, 1, 6, 15, tzinfo=UTC),
                1,
            ),
        ),
    )
    result = _validate(custom, CandidatePatch(1, (operation,), "", ()))
    assert result.valid
    assert result.materialized_schedule is not None
    assignment = result.materialized_schedule[0]
    assert (assignment.start_at, assignment.end_at) == (
        datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        datetime(2026, 11, 1, 6, 15, tzinfo=UTC),
    )


def test_fold_assignments_keep_raw_canonical_order_without_false_overlap():
    zone = ZoneInfo("America/New_York")
    first = Assignment(
        "assignment-fold-zero",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0),
        datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0),
    )
    second = Assignment(
        "assignment-fold-one",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1),
        datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1),
    )
    schedule = (second, first)
    assert canonical_schedule((first, second)) == schedule
    assert _validate_assignment_tuple(schedule, "fold schedule") == schedule
    assert schedule_digest(schedule) == stable_digest(schedule)
    result = CandidateValidation(
        1, True, (), (), schedule, schedule_digest(schedule)
    )
    assert result.materialized_schedule == schedule


def test_coverage_keeps_both_fallback_fold_boundaries_and_adjacent_gaps():
    basis = _dst_basis(
        date(2026, 11, 1), "America/New_York", ("00:00", "03:00"), maximum=300
    )
    zone = ZoneInfo("America/New_York")
    custom = replace(
        basis,
        assignments=(),
        exceptions=(
            StaffingException(
                "exception-fold-boundary",
                date(2026, 11, 1),
                "staff-001",
                "UNAVAILABLE",
                datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0),
                datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1),
                None,
            ),
        ),
        coverage=(
            CoverageWindow(
                "RANGE_ATTENDANT",
                "RANGE_A",
                datetime(2026, 11, 1, 0, 0, tzinfo=zone),
                datetime(2026, 11, 1, 2, 0, tzinfo=zone),
                1,
            ),
        ),
    )
    assert coverage_gaps((), custom) == (
        CoverageGap(
            "RANGE_ATTENDANT",
            "RANGE_A",
            datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
            datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
            1,
            0,
        ),
        CoverageGap(
            "RANGE_ATTENDANT",
            "RANGE_A",
            datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
            datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
            1,
            0,
        ),
        CoverageGap(
            "RANGE_ATTENDANT",
            "RANGE_A",
            datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
            datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
            1,
            0,
        ),
    )


def test_nonexistent_availability_boundary_fails_closed():
    basis = _dst_basis(
        date(2026, 3, 8), "America/New_York", ("02:30", "04:00"), maximum=120
    )
    operation = AddOperation(
        "ADD",
        "worker_1",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 3, 8, 3, tzinfo=timezone(timedelta(hours=-4))),
        datetime(2026, 3, 8, 4, tzinfo=timezone(timedelta(hours=-4))),
    )
    assert not basis_is_available(basis, operation, "staff-001")
    with pytest.raises(StaffingError) as caught:
        validate_alias_maps(basis, *_aliases(basis))
    assert caught.value.code == "staffing_invalid_evidence"


def test_ghost_availability_is_invalid_evidence_not_candidate_unavailability():
    basis = _basis()
    broken = replace(
        basis,
        availability=(
            replace(basis.availability[0], staff_id="ghost-worker"),
        ),
    )
    with pytest.raises(StaffingError) as caught:
        _validate(broken, _candidate(broken))
    assert caught.value.code == "staffing_invalid_evidence"


def test_none_exception_interval_is_invalid_evidence_not_attribute_error():
    basis = _basis()
    broken = replace(
        basis,
        exceptions=(
            StaffingException(
                "exception-broken",
                SERVICE_DATE,
                "staff-001",
                "UNAVAILABLE",
                None,
                datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
                None,
            ),
        ),
    )
    with pytest.raises(StaffingError) as caught:
        _validate(broken, _candidate(broken))
    assert caught.value.code == "staffing_invalid_evidence"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda basis: replace(
            basis,
            assignments=(replace(basis.assignments[0], role_code="MARSHAL"),),
        ),
        lambda basis: replace(
            basis,
            coverage=(
                CoverageWindow(
                    "RANGE_ATTENDANT",
                    "RANGE_A",
                    datetime(2026, 10, 6, 9, tzinfo=SITE_ZONE),
                    datetime(2026, 10, 6, 10, tzinfo=SITE_ZONE),
                    1,
                ),
            ),
        ),
    ],
)
def test_broken_assignment_or_coverage_basis_is_stable_invalid_evidence(mutate):
    broken = mutate(_basis())
    with pytest.raises(StaffingError) as caught:
        validate_alias_maps(broken, *_aliases(broken))
    assert caught.value.code == "staffing_invalid_evidence"


@pytest.mark.parametrize(
    "error_type",
    [TypeError, RuntimeError, KeyError, OSError, _HostileClockError],
)
def test_hostile_basis_timezone_is_stable_invalid_evidence(error_type):
    basis = _basis()
    hostile = _ExplodingTimezone(error_type)
    broken = replace(
        basis,
        exceptions=(
            StaffingException(
                "exception-hostile",
                SERVICE_DATE,
                "staff-001",
                "UNAVAILABLE",
                datetime(2026, 10, 5, 9, tzinfo=hostile),
                datetime(2026, 10, 5, 10, tzinfo=hostile),
                None,
            ),
        ),
    )
    with pytest.raises(StaffingError) as caught:
        validate_alias_maps(broken, *_aliases(broken))
    assert caught.value.code == "staffing_invalid_evidence"


def test_assignment_id_and_schedule_digest_have_frozen_golden_values():
    basis = _basis()
    assignment = assignment_from_candidate(_operation(), basis, "staff-001")
    assert assignment.assignment_id == "assignment_eb39a03ab98e1a62df044134"
    assert schedule_digest((assignment,)) == (
        "280f16ff1489993ed06bc516295fc05637314ce735691b69681e5738a06b0a54"
    )
    assert schedule_digest((assignment,)) == stable_digest(
        to_primitive(canonical_schedule((assignment,)))
    )


@pytest.mark.parametrize("error_type", [KeyError, OSError, RuntimeError])
def test_schedule_digest_does_not_reread_stateful_timezone_during_serialization(
    error_type,
):
    hostile = _StatefulTimezone(error_type("late timezone failure"), fail_on=7)
    assignment = Assignment(
        "assignment-stateful",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 9, tzinfo=hostile),
        datetime(2026, 10, 5, 10, tzinfo=hostile),
    )
    normalized = Assignment(
        "assignment-stateful",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 1, tzinfo=UTC),
        datetime(2026, 10, 5, 2, tzinfo=UTC),
    )
    assert schedule_digest((assignment,)) == stable_digest(to_primitive((normalized,)))


def test_candidate_validation_digest_recheck_does_not_reread_stateful_timezone():
    hostile = _StatefulTimezone(OSError("late timezone failure"), fail_on=11)
    assignment = Assignment(
        "assignment-stateful",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 9, tzinfo=hostile),
        datetime(2026, 10, 5, 10, tzinfo=hostile),
    )
    normalized = Assignment(
        "assignment-stateful",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 1, tzinfo=UTC),
        datetime(2026, 10, 5, 2, tzinfo=UTC),
    )
    digest = stable_digest(to_primitive((normalized,)))
    result = CandidateValidation(1, True, (), (), (assignment,), digest)
    assert result.materialized_schedule_digest == digest


@pytest.mark.parametrize("error_type", [KeyError, OSError, RuntimeError])
def test_schedule_digest_maps_early_timezone_failures_to_stable_evidence(error_type):
    hostile = _StatefulTimezone(error_type("early timezone failure"), fail_on=3)
    assignment = Assignment(
        "assignment-hostile",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 9, tzinfo=hostile),
        datetime(2026, 10, 5, 10, tzinfo=hostile),
    )
    with pytest.raises(StaffingError) as caught:
        schedule_digest((assignment,))
    assert caught.value.code == "staffing_invalid_evidence"


def test_schedule_digest_preserves_staffing_error_and_base_exception_boundaries():
    staffing_zone = _StatefulTimezone(
        StaffingError("sentinel_staffing_error", "timezone"), fail_on=3
    )
    staffing_assignment = Assignment(
        "assignment-domain-error",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 9, tzinfo=staffing_zone),
        datetime(2026, 10, 5, 10, tzinfo=staffing_zone),
    )
    with pytest.raises(StaffingError) as caught:
        schedule_digest((staffing_assignment,))
    assert caught.value.code == "sentinel_staffing_error"

    interrupt_zone = _StatefulTimezone(KeyboardInterrupt(), fail_on=3)
    interrupt_assignment = replace(
        staffing_assignment,
        start_at=datetime(2026, 10, 5, 9, tzinfo=interrupt_zone),
        end_at=datetime(2026, 10, 5, 10, tzinfo=interrupt_zone),
    )
    with pytest.raises(KeyboardInterrupt):
        schedule_digest((interrupt_assignment,))


def test_schedule_helpers_are_permutation_deterministic_and_empty_is_real():
    left = Assignment(
        "assignment-left",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 9, tzinfo=SITE_ZONE),
        datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
    )
    right = Assignment(
        "assignment-right",
        "staff-002",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE),
        datetime(2026, 10, 5, 11, tzinfo=SITE_ZONE),
    )
    assert canonical_schedule((right, left)) == (left, right)
    assert schedule_digest((right, left)) == schedule_digest((left, right))
    assert schedule_digest(()) == stable_digest(())


def test_alias_pair_order_is_bound_to_frozen_basis_position():
    basis = _basis(_two_worker_payload(second_assignment=True))
    workers, assignments = _aliases(basis)
    candidate = CandidatePatch(1, (), "", ())
    validate_candidate(
        basis,
        candidate,
        worker_alias_to_staff_id=workers,
        assignment_alias_to_assignment_id=assignments,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
    )
    with pytest.raises(StaffingError) as caught:
        validate_candidate(
            basis,
            candidate,
            worker_alias_to_staff_id=tuple(reversed(workers)),
            assignment_alias_to_assignment_id=tuple(reversed(assignments)),
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
        )
    assert caught.value.code == "staffing_invalid_evidence"


def test_coverage_counts_distinct_staff_and_splits_at_every_boundary_without_merging():
    basis = _basis()
    start = datetime(2026, 10, 5, 9, tzinfo=SITE_ZONE)
    ten = datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE)
    eleven = datetime(2026, 10, 5, 11, tzinfo=SITE_ZONE)
    noon = datetime(2026, 10, 5, 12, tzinfo=SITE_ZONE)
    schedule = (
        Assignment("assignment-a", "staff-001", "RANGE_ATTENDANT", "RANGE_A", start, ten),
        Assignment("assignment-b", "staff-001", "RANGE_ATTENDANT", "RANGE_A", start, ten),
    )
    exception = StaffingException(
        "exception-split",
        SERVICE_DATE,
        "staff-001",
        "UNAVAILABLE",
        ten,
        eleven,
        None,
    )
    requirements = (
        CoverageWindow("RANGE_ATTENDANT", "RANGE_A", start, noon, 2),
        CoverageWindow("MARSHAL", "RANGE_B", ten, eleven, 1),
    )
    custom = replace(
        basis,
        assignments=(),
        exceptions=(exception,),
        assignment_rules=(
            *basis.assignment_rules,
            AssignmentRule("MARSHAL", "RANGE_B", ()),
        ),
        coverage=requirements,
    )
    assert coverage_gaps(schedule, custom) == (
        CoverageGap("MARSHAL", "RANGE_B", ten, eleven, 1, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", start, ten, 2, 1),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", ten, eleven, 2, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", eleven, noon, 2, 0),
    )


def test_overlapping_coverage_requirements_remain_independent():
    basis = _basis()
    nine = datetime(2026, 10, 5, 9, tzinfo=SITE_ZONE)
    ten = datetime(2026, 10, 5, 10, tzinfo=SITE_ZONE)
    eleven = datetime(2026, 10, 5, 11, tzinfo=SITE_ZONE)
    noon = datetime(2026, 10, 5, 12, tzinfo=SITE_ZONE)
    custom = replace(
        basis,
        assignments=(),
        coverage=(
            CoverageWindow("RANGE_ATTENDANT", "RANGE_A", nine, noon, 1),
            CoverageWindow("RANGE_ATTENDANT", "RANGE_A", ten, eleven, 2),
        ),
    )
    assert coverage_gaps((), custom) == (
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", nine, ten, 1, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", ten, eleven, 1, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", ten, eleven, 2, 0),
        CoverageGap("RANGE_ATTENDANT", "RANGE_A", eleven, noon, 1, 0),
    )


def test_validation_does_not_mutate_basis_or_candidate_bytes():
    basis = _basis()
    candidate = _candidate(basis)
    before = canonical_json_bytes((basis, candidate))
    result = _validate(basis, candidate)
    after = canonical_json_bytes((basis, candidate))
    assert result.valid
    assert before == after


def test_validate_candidates_requires_exact_contiguous_tuple_and_keeps_results_independent():
    basis = _basis()
    workers, assignments = _aliases(basis)
    valid = _candidate(basis)
    invalid = CandidatePatch(
        2,
        (RemoveOperation("REMOVE", "assignment_1"),),
        "",
        (),
    )
    results = validate_candidates(
        basis,
        (valid, invalid),
        worker_alias_to_staff_id=workers,
        assignment_alias_to_assignment_id=assignments,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
    )
    assert results[0].valid
    assert results[1].rejection_codes == ("COVERAGE_GAP",)
    assert validate_candidates(
        basis,
        (),
        worker_alias_to_staff_id=workers,
        assignment_alias_to_assignment_id=assignments,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
    ) == ()
    for bad in (
        [valid],
        (invalid,),
        (invalid, valid),
        (valid, valid),
        (valid, invalid, invalid),
    ):
        with pytest.raises(StaffingError) as caught:
            validate_candidates(
                basis,
                bad,
                worker_alias_to_staff_id=workers,
                assignment_alias_to_assignment_id=assignments,
                prompt_template_version=PROMPT_TEMPLATE_VERSION,
            )
        assert caught.value.code == "invalid_candidate_set"


def test_direct_validation_accepts_candidate_two_for_manager_modify_path():
    basis = _basis()
    result = _validate(basis, _candidate(basis, candidate_index=2))
    assert result.candidate_index == 2
    assert result.valid
