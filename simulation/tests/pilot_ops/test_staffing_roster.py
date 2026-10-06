"""Closed contracts and all-or-nothing weekly roster validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from datetime import date, datetime, timezone

import pytest

from nxt_pilot_ops.serialization import canonical_json, stable_digest, to_primitive
from nxt_pilot_ops.staffing.contracts import (
    AddOperation,
    AppendEventDecision,
    Assignment,
    AssignmentProjection,
    AssignmentRule,
    AttemptFinishedEvidence,
    AttemptRouteEvidence,
    AttemptStartedEvidence,
    AvailabilityWindow,
    BasisSnapshot,
    CandidateOperationProjection,
    CandidateProjection,
    CommittedReceipt,
    ConflictDecision,
    ConflictReceipt,
    CoverageGap,
    CoverageRequirement,
    CoverageWindow,
    DateProjection,
    DuplicateReceipt,
    EffectivePlanState,
    ExceptionCancelledPayload,
    ExceptionCommittedProjection,
    ExceptionCorrectedPayload,
    ExceptionProjection,
    ExceptionRecordedPayload,
    GenerationCommittedProjection,
    GenerationInterruptedPayload,
    GenerationProjectionView,
    GenerationReservedPayload,
    GenerationRouteEvidence,
    GenerationState,
    ManagerAddOperation,
    ManagerCommittedProjection,
    ManagerRemoveOperation,
    ManagerResponseCommittedPayload,
    ManagerResponseProjection,
    ManagerResponseRequest,
    ProviderAssignment,
    ProviderAttemptFinishedPayload,
    ProviderAttemptStartedPayload,
    ProviderAvailability,
    ProviderCoverage,
    ProviderPayload,
    ProviderUnavailable,
    ProviderWorker,
    PROMPT_TEMPLATE_VERSION,
    RemoveOperation,
    RequestProjection,
    ResultEvidence,
    ReturnReceiptDecision,
    RevisionVector,
    RosterCommittedProjection,
    RosterImportedPayload,
    RosterRevision,
    ServiceDayRoster,
    StaffingBasis,
    StaffingError,
    StaffingEvent,
    StaffingException,
    StaffingHistory,
    StaffingReceipt,
    StoredCandidate,
    SuggestionIssuedPayload,
    SuggestionUnavailablePayload,
    VerifiedLedgerState,
    WeeklyAssignment,
    Worker,
    parse_attempt_finished_evidence,
    parse_attempt_route_evidence,
    parse_attempt_started_evidence,
    parse_generation_route_evidence,
    parse_result_evidence,
)
from nxt_pilot_ops.staffing.roster import (
    materialize_service_day,
    select_effective_roster,
    validate_roster_import,
)
from nxt_pilot_ops.staffing.time import utc_text as staffing_utc_text

from .staffing_fixtures import roster_import_request


FIELD_SETS = {
    AttemptRouteEvidence: "provider region route_role route_id model_id",
    GenerationRouteEvidence: "region readiness primary_provider primary_model_id backup_provider backup_model_id",
    StaffingException: "exception_id service_date staff_id kind unavailable_start unavailable_end note",
    StaffingBasis: "service_date roster_revision exception_set_revision effective_plan_revision roster_digest exception_set_digest effective_plan_digest basis_digest",
    EffectivePlanState: "revision status schedule_digest effective_schedule source_sequence affected_exception_ids",
    AssignmentProjection: "assignment_id staff_id display_name role_code area_code start_at end_at",
    ExceptionProjection: "exception_id service_date staff_id display_name kind unavailable_start unavailable_end note",
    CandidateProjection: "candidate_index valid rationale operational_warnings rejection_codes coverage_gaps operations materialized_schedule materialized_schedule_digest",
    CandidateOperationProjection: "operation assignment_id staff_id display_name role_code area_code start_at end_at",
    GenerationProjectionView: "generation_id request_id operation_event_id service_date basis_revisions basis_digest retry_of lifecycle_state failure_code route_region route_readiness primary_provider primary_model_id backup_provider backup_model_id started_attempts finished_attempts candidates",
    ManagerResponseProjection: "generation_id decision reason_code operator note effective_plan_revision schedule_digest effective_schedule",
    RosterCommittedProjection: "event_id occurred_at_utc request_digest operator source_ref roster_revision roster_digest roster_effective_from roster_effective_until worker_count assignment_count coverage_count",
    ExceptionCommittedProjection: "event_id event_type occurred_at_utc request_digest operator exception_id exception_set_revision exception_digest exception replacement note",
    GenerationCommittedProjection: "event_id occurred_at_utc request_digest generation_id service_date basis_revisions basis_digest retry_of route_region route_readiness primary_provider backup_provider",
    ManagerCommittedProjection: "event_id occurred_at_utc request_digest generation_id operator note decision reason_code effective_plan_revision schedule_digest effective_schedule",
    DateProjection: "site_id deployment_id service_date site_timezone roster_revision roster_digest exception_set_revision roster_effective_from roster_effective_until worker_count assignment_count coverage_count effective_plan_schedule availability assignments exceptions effective_plan generations manager_responses",
    RequestProjection: "operation_kind request_id event_ids generation_id lifecycle_state started_attempts attempt_evidence result_evidence candidates manager_response generation committed_record",
    CoverageWindow: "role_code area_code start_at end_at minimum_staff",
    BasisSnapshot: "basis site_timezone workers assignments exceptions availability assignment_rules coverage prompt_template_version",
    AttemptStartedEvidence: "request_id attempt_index route input_digest timeout_s",
    AttemptFinishedEvidence: "request_id attempt_index route status failure_code retryable security_failure provider_request_id finish_reason output_digest input_tokens output_tokens",
    ResultEvidence: "request_id status failure_code selected_provider selected_model_id provider_request_id finish_reason input_digest output_digest attempts candidate_count bounded_summary",
    RosterImportedPayload: "request_id request_digest roster_revision roster_digest roster operator source_ref",
    ExceptionRecordedPayload: "request_id request_digest exception_id service_date exception_set_revision exception_digest exception operator",
    ExceptionCancelledPayload: "request_id request_digest exception_id exception_set_revision cancelled_exception operator note",
    ExceptionCorrectedPayload: "request_id request_digest exception_id replacement_exception exception_set_revision exception_digest previous_exception operator",
    GenerationReservedPayload: "request_id request_digest generation_id service_date basis_snapshot provider_payload alias_nonce_digest worker_alias_to_staff_id assignment_alias_to_assignment_id input_digest prompt_template_version language route retry_of operator",
    GenerationInterruptedPayload: "request_digest generation_id reason interrupted_at_utc",
    ProviderAttemptStartedPayload: "request_digest generation_id evidence",
    ProviderAttemptFinishedPayload: "request_digest generation_id evidence",
    StoredCandidate: "candidate_index operations operation_offset_minutes rationale operational_warnings rejection_codes coverage_gaps materialized_schedule materialized_schedule_digest",
    SuggestionIssuedPayload: "request_digest generation_id result candidates candidate_set_digest",
    SuggestionUnavailablePayload: "request_digest generation_id result candidates candidate_set_digest terminal_state failure_code",
    ManagerResponseCommittedPayload: "request_id request_digest generation_id decision reason_code effective_schedule schedule_digest effective_plan_revision operator note basis_snapshot",
    ManagerRemoveOperation: "operation assignment_id",
    ManagerAddOperation: "operation staff_id role_code area_code start_at end_at",
    RevisionVector: "roster exception_set effective_plan",
    ManagerResponseRequest: "request_id generation_id operator kind expected_revisions candidate_index edited_operations reason_code note",
    ProviderWorker: "worker_alias skill_codes eligibility max_daily_minutes",
    ProviderAssignment: "assignment_alias worker_alias role_code area_code start_at end_at",
    ProviderUnavailable: "worker_alias start_at end_at",
    ProviderAvailability: "worker_alias start_at end_at",
    ProviderCoverage: "role_code area_code start_at end_at minimum_staff",
    ProviderPayload: "basis_digest workers assignments availability unavailable coverage prompt_template_version language",
    CoverageGap: "role_code area_code start_at end_at required actual",
    RemoveOperation: "operation assignment_alias",
    AddOperation: "operation worker_alias role_code area_code start_at end_at",
    StaffingEvent: "event_type event_id sequence site_id deployment_id occurred_at_utc causation_id payload",
    StaffingReceipt: "operation_kind request_id request_digest event_id sequence record_hash duplicate",
    CommittedReceipt: "receipt",
    DuplicateReceipt: "receipt",
    ConflictReceipt: "operation_kind request_id code",
    GenerationState: "generation_id request_id request_digest is_terminal",
    AppendEventDecision: "event",
    ReturnReceiptDecision: "receipt",
    ConflictDecision: "operation_kind request_id code",
    StaffingHistory: "events",
    VerifiedLedgerState: "history receipts record_count head_hash",
    Worker: "staff_id display_name skill_codes eligibility max_daily_minutes",
    AvailabilityWindow: "staff_id weekday start_local end_local",
    WeeklyAssignment: "staff_id weekday role_code area_code start_local end_local",
    AssignmentRule: "role_code area_code required_skill_codes",
    CoverageRequirement: "weekday role_code area_code start_local end_local minimum_staff",
    RosterRevision: "site_id deployment_id site_timezone revision effective_from effective_until workers availability regular_assignments assignment_rules coverage roster_digest",
    Assignment: "assignment_id staff_id role_code area_code start_at end_at",
    ServiceDayRoster: "service_date site_timezone assignments coverage",
}


def _field_names(kind: type) -> tuple[str, ...]:
    return tuple(item.name for item in fields(kind))


def test_every_cross_task_contract_is_frozen_slotted_and_exactly_shaped():
    assert len(FIELD_SETS) == 66
    assert PROMPT_TEMPLATE_VERSION == "staffing-adjustment/v1"
    for kind, expected in FIELD_SETS.items():
        assert is_dataclass(kind), kind.__name__
        assert kind.__dataclass_params__.frozen, kind.__name__
        assert _field_names(kind) == tuple(expected.split()), kind.__name__
        assert tuple(kind.__slots__) == tuple(expected.split()), kind.__name__


def test_tuple_bearing_contracts_recursively_detach_mutable_sequences():
    skills = ["BALL_PICKING"]
    eligibility = [["RANGE_ATTENDANT", "RANGE_A"]]
    worker = ProviderWorker("worker_1", skills, eligibility, 480)
    workers = [worker]
    payload = ProviderPayload(
        "a" * 64, workers, [], [], [], [], PROMPT_TEMPLATE_VERSION, "zh-CN"
    )
    events = []
    history = StaffingHistory(events)
    skills[0] = "MUTATED"
    eligibility[0][0] = "MUTATED"
    workers.clear()
    events.append("mutated")
    assert worker.skill_codes == ("BALL_PICKING",)
    assert worker.eligibility == (("RANGE_ATTENDANT", "RANGE_A"),)
    assert payload.workers == (worker,)
    assert history.events == ()


@pytest.mark.parametrize("language", ["zh", "en-US", "", None, True])
def test_provider_payload_language_is_a_closed_literal(language):
    with pytest.raises(StaffingError):
        ProviderPayload("a" * 64, (), (), (), (), (), PROMPT_TEMPLATE_VERSION, language)
    with pytest.raises(StaffingError):
        GenerationReservedPayload(
            "request-1",
            "a" * 64,
            "generation-1",
            date(2026, 10, 5),
            None,
            None,
            "b" * 64,
            (),
            (),
            "c" * 64,
            PROMPT_TEMPLATE_VERSION,
            language,
            None,
            None,
            "operator",
        )


def test_receipt_wrappers_enforce_the_inner_duplicate_flag():
    committed = StaffingReceipt(
        "roster-import",
        "request-1",
        "a" * 64,
        "event-1",
        1,
        "b" * 64,
        False,
    )
    duplicate = StaffingReceipt(
        "roster-import",
        "request-1",
        "a" * 64,
        "event-1",
        1,
        "b" * 64,
        True,
    )
    assert CommittedReceipt(committed).receipt is committed
    assert DuplicateReceipt(duplicate).receipt is duplicate
    with pytest.raises(StaffingError):
        CommittedReceipt(duplicate)
    with pytest.raises(StaffingError):
        DuplicateReceipt(committed)


def test_roster_validation_detaches_normalizes_and_freezes_every_collection():
    payload = roster_import_request()
    revision = validate_roster_import(
        payload,
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    payload["workers"][0]["skill_codes"][0] = "MUTATED"
    payload["availability"].clear()

    assert revision.revision == 1
    assert revision.workers[0].skill_codes == ("BALL_PICKING",)
    assert revision.availability
    for name in (
        "workers",
        "availability",
        "regular_assignments",
        "assignment_rules",
        "coverage",
    ):
        assert isinstance(getattr(revision, name), tuple)
    assert isinstance(revision.workers[0].eligibility, tuple)
    with pytest.raises(FrozenInstanceError):
        revision.revision = 2


def test_roster_values_reject_invalid_direct_construction():
    invalid_builders = (
        lambda: Worker("bad id", "Name", (), (), 480),
        lambda: Worker("staff-1", "Name", (), (), True),
        lambda: AvailabilityWindow("staff-1", 7, "08:00", "09:00"),
        lambda: AvailabilityWindow("staff-1", 0, "09:00", "08:00"),
        lambda: WeeklyAssignment(
            "staff-1", 0, "bad", "RANGE_A", "08:00", "09:00"
        ),
        lambda: AssignmentRule("ROLE", "AREA", ["BAD-SKILL"]),
        lambda: CoverageRequirement(0, "ROLE", "AREA", "08:00", "09:00", True),
        lambda: Assignment(
            "assignment-1",
            "staff-1",
            "ROLE",
            "AREA",
            datetime(2026, 10, 5, 8),
            datetime(2026, 10, 5, 9),
        ),
    )
    for build in invalid_builders:
        with pytest.raises(StaffingError):
            build()


@pytest.mark.parametrize("bad_timezone", ["Mars/Olympus", "", None, True, []])
def test_direct_roster_revision_requires_an_actual_iana_timezone(bad_timezone):
    revision = validate_roster_import(
        roster_import_request(),
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    with pytest.raises(StaffingError) as error:
        replace(revision, site_timezone=bad_timezone)
    assert error.value.code == "staffing_invalid_roster"


@pytest.mark.parametrize(
    "bad_digest", [None, 1, True, [], {}, "A" * 64, "a" * 63, "b" * 64]
)
def test_direct_roster_revision_rejects_non_string_or_noncanonical_digest(bad_digest):
    revision = validate_roster_import(
        roster_import_request(),
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    with pytest.raises(StaffingError) as error:
        replace(revision, roster_digest=bad_digest)
    assert error.value.code == "staffing_invalid_roster"


def test_direct_roster_revision_recomputes_digest_when_content_changes():
    revision = validate_roster_import(
        roster_import_request(),
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    changed_worker = replace(revision.workers[0], display_name="Another local name")
    with pytest.raises(StaffingError) as error:
        replace(revision, workers=(changed_worker,))
    assert error.value.code == "staffing_invalid_roster"


def test_direct_roster_revision_rejects_noncanonical_order_even_with_matching_digest():
    payload = roster_import_request()
    payload["workers"].append(
        {
            "staff_id": "staff-002",
            "display_name": "本地员工乙",
            "skill_codes": ["BALL_PICKING"],
            "eligibility": [
                {"role_code": "RANGE_ATTENDANT", "area_code": "RANGE_A"}
            ],
            "max_daily_minutes": 480,
        }
    )
    revision = validate_roster_import(
        payload,
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    reversed_workers = tuple(reversed(revision.workers))
    noncanonical_core = {
        "schema": "nxt-staffing-roster-import/v1",
        "site_id": revision.site_id,
        "deployment_id": revision.deployment_id,
        "site_timezone": revision.site_timezone,
        "effective_from_local_date": revision.effective_from,
        "effective_until_local_date": revision.effective_until,
        "workers": reversed_workers,
        "availability": revision.availability,
        "regular_assignments": revision.regular_assignments,
        "assignment_rules": revision.assignment_rules,
        "coverage": revision.coverage,
    }
    with pytest.raises(StaffingError) as error:
        replace(
            revision,
            workers=reversed_workers,
            roster_digest=stable_digest(noncanonical_core),
        )
    assert error.value.code == "staffing_invalid_roster"


def test_roster_digest_is_semantic_and_excludes_request_audit_and_cas_fields():
    first_payload = roster_import_request()
    second_payload = roster_import_request()
    second_payload.update(
        request_id="another-request",
        expected_roster_revision=8,
        operator="another-manager",
        source_ref="another-source.csv",
    )
    first = validate_roster_import(
        first_payload,
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    second = validate_roster_import(
        second_payload,
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    assert first.revision == 1
    assert second.revision == 9
    assert first.roster_digest == second.roster_digest

    expected_core = {
        "schema": "nxt-staffing-roster-import/v1",
        "site_id": first.site_id,
        "deployment_id": first.deployment_id,
        "site_timezone": first.site_timezone,
        "effective_from_local_date": first.effective_from,
        "effective_until_local_date": first.effective_until,
        "workers": first.workers,
        "availability": first.availability,
        "regular_assignments": first.regular_assignments,
        "assignment_rules": first.assignment_rules,
        "coverage": first.coverage,
    }
    assert first.roster_digest == stable_digest(expected_core)


def test_reordering_equivalent_rows_and_nested_sets_preserves_digest_and_order():
    payload = roster_import_request()
    payload["workers"][0]["skill_codes"].append("CUSTOMER_SERVICE")
    payload["workers"][0]["eligibility"].append(
        {"role_code": "RANGE_ATTENDANT", "area_code": "RANGE_B"}
    )
    payload["assignment_rules"].append(
        {
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_B",
            "required_skill_codes": ["CUSTOMER_SERVICE"],
        }
    )
    reverse = deepcopy(payload)
    reverse["workers"][0]["skill_codes"].reverse()
    reverse["workers"][0]["eligibility"].reverse()
    reverse["assignment_rules"].reverse()
    values = [
        validate_roster_import(
            item,
            site_id="pilot-course-a",
            deployment_id="pilot-a-edge-task-sim-v0",
            site_timezone="Asia/Shanghai",
        )
        for item in (payload, reverse)
    ]
    assert values[0] == values[1]
    assert values[0].roster_digest == values[1].roster_digest


def test_date_serialization_follows_datetime_without_changing_datetime_bytes():
    moment = datetime(2026, 10, 5, 1, 2, 3, 456789, tzinfo=timezone.utc)
    assert to_primitive(date(2026, 10, 5)) == "2026-10-05"
    assert canonical_json(moment) == '"2026-10-05T01:02:03.456789Z"'
    assert stable_digest(moment) == stable_digest("2026-10-05T01:02:03.456789Z")


def test_materialization_reuses_one_revision_for_d_plus_one_and_d_plus_seven():
    payload = roster_import_request()
    payload["availability"].append(
        {"staff_id": "staff-001", "weekday": 1, "start_local": "08:00", "end_local": "17:00"}
    )
    payload["regular_assignments"].append(
        {
            "staff_id": "staff-001",
            "weekday": 1,
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_A",
            "start_local": "10:00",
            "end_local": "16:00",
        }
    )
    payload["coverage"].append(
        {
            "weekday": 1,
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_A",
            "start_local": "10:00",
            "end_local": "16:00",
            "minimum_staff": 1,
        }
    )
    revision = validate_roster_import(
        payload,
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    monday = materialize_service_day(revision, date(2026, 10, 5))
    tuesday = materialize_service_day(revision, date(2026, 10, 6))
    next_monday = materialize_service_day(revision, date(2026, 10, 12))

    assert len(monday.assignments) == len(next_monday.assignments) == 1
    assert tuesday.assignments[0].start_at.hour == 2  # 10:00 Asia/Shanghai
    assert monday.assignments[0].assignment_id != next_monday.assignments[0].assignment_id
    assert len(monday.coverage) == len(next_monday.coverage) == 1


def test_service_day_assignment_lookup_and_interval_derivation_are_deterministic():
    revision = validate_roster_import(
        roster_import_request(),
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    roster = materialize_service_day(revision, date(2026, 10, 5))
    assignment = roster.assignment_for_staff("staff-001")
    assert assignment is not None
    assert assignment.assignment_id == "assignment_" + stable_digest(
        {
            "roster_revision": revision.revision,
            "service_date": "2026-10-05",
            "staff_id": "staff-001",
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_A",
            "start_at": staffing_utc_text(assignment.start_at),
            "end_at": staffing_utc_text(assignment.end_at),
        }
    )[:24]
    shortened = assignment.with_interval(
        assignment.start_at, assignment.end_at.replace(hour=8)
    )
    assert shortened.assignment_id != assignment.assignment_id
    assert shortened == assignment.with_interval(shortened.start_at, shortened.end_at)
    assert roster.assignment_for_staff("missing") is None


def test_effective_selection_uses_highest_applicable_revision():
    payload = roster_import_request()
    first = validate_roster_import(
        payload,
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    payload["expected_roster_revision"] = 1
    payload["effective_from_local_date"] = "2026-10-12"
    second = validate_roster_import(
        payload,
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    assert select_effective_roster((second, first), date(2026, 10, 5)) == first
    assert select_effective_roster((first, second), date(2026, 10, 12)) == second
    with pytest.raises(StaffingError) as error:
        select_effective_roster((second,), date(2026, 10, 5))
    assert error.value.code == "staffing_roster_not_found"


def _duplicate_staff(payload):
    payload["workers"].append(deepcopy(payload["workers"][0]))


def _duplicate_rule(payload):
    payload["assignment_rules"].append(deepcopy(payload["assignment_rules"][0]))


def _unknown_rule(payload):
    payload["regular_assignments"][0]["area_code"] = "RANGE_B"


def _ineligible(payload):
    payload["workers"][0]["eligibility"] = []


def _missing_skill(payload):
    payload["workers"][0]["skill_codes"] = []


def _invalid_timezone(payload):
    payload["site_timezone"] = "Mars/Olympus"


def _overlap_availability(payload):
    payload["availability"].append(
        {"staff_id": "staff-001", "weekday": 0, "start_local": "16:00", "end_local": "18:00"}
    )


def _overlap_coverage(payload):
    payload["coverage"].append(
        {
            "weekday": 0,
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_A",
            "start_local": "16:00",
            "end_local": "18:00",
            "minimum_staff": 1,
        }
    )


def _reversed_time(payload):
    payload["availability"][0]["end_local"] = "07:00"


def _multiple_shifts(payload):
    duplicate = deepcopy(payload["regular_assignments"][0])
    duplicate.update(start_local="08:00", end_local="09:00")
    payload["regular_assignments"].append(duplicate)


def _missing_coverage(payload):
    payload["coverage"] = []


def _unknown_top_field(payload):
    payload["timezone"] = payload["site_timezone"]


def _unknown_nested_field(payload):
    payload["workers"][0]["phone"] = "123"


def _bad_expected_revision(payload):
    payload["expected_roster_revision"] = True


@pytest.mark.parametrize(
    "mutate",
    [
        _duplicate_staff,
        _duplicate_rule,
        _unknown_rule,
        _ineligible,
        _missing_skill,
        _invalid_timezone,
        _overlap_availability,
        _overlap_coverage,
        _reversed_time,
        _multiple_shifts,
        _missing_coverage,
        _unknown_top_field,
        _unknown_nested_field,
        _bad_expected_revision,
    ],
)
def test_invalid_roster_matrix_rejects_the_entire_import(mutate):
    payload = roster_import_request()
    mutate(payload)
    with pytest.raises(StaffingError):
        validate_roster_import(
            payload,
            site_id="pilot-course-a",
            deployment_id="pilot-a-edge-task-sim-v0",
            site_timezone="Asia/Shanghai",
        )


def test_adjacent_coverage_and_availability_are_valid():
    payload = roster_import_request()
    payload["coverage"][0]["end_local"] = "13:00"
    payload["coverage"].append(
        {
            "weekday": 0,
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_A",
            "start_local": "13:00",
            "end_local": "17:00",
            "minimum_staff": 1,
        }
    )
    revision = validate_roster_import(
        payload,
        site_id="pilot-course-a",
        deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )
    assert len(revision.coverage) == 2


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "  ="])
@pytest.mark.parametrize(
    ("owner", "field"),
    [(None, "operator"), (None, "source_ref"), ("workers", "display_name")],
)
def test_formula_prefixed_csv_derived_text_is_rejected(prefix, owner, field):
    payload = roster_import_request()
    target = payload if owner is None else payload[owner][0]
    target[field] = prefix + "SUM(A1:A2)"
    with pytest.raises(StaffingError) as error:
        validate_roster_import(
            payload,
            site_id="pilot-course-a",
            deployment_id="pilot-a-edge-task-sim-v0",
            site_timezone="Asia/Shanghai",
        )
    assert error.value.code == "staffing_invalid_roster"


@pytest.mark.parametrize("bad", ["name\nnext", "name\x00", "name\ud800"])
def test_human_text_rejects_controls_and_surrogates_without_echoing_input(bad):
    payload = roster_import_request()
    payload["workers"][0]["display_name"] = bad
    with pytest.raises(StaffingError) as error:
        validate_roster_import(
            payload,
            site_id="pilot-course-a",
            deployment_id="pilot-a-edge-task-sim-v0",
            site_timezone="Asia/Shanghai",
        )
    assert error.value.code == "staffing_invalid_roster"
    assert bad not in str(error.value)


def test_bounds_identifiers_codes_effective_dates_and_identity_are_strict():
    changes = [
        ("request_id", "bad id"),
        ("operator", "x" * 129),
        ("source_ref", "x" * 257),
        ("effective_from_local_date", "2026-02-30"),
        ("effective_until_local_date", "2026-10-04"),
        ("site_id", "other"),
        ("deployment_id", "other"),
        ("site_timezone", "UTC"),
    ]
    for field, bad in changes:
        payload = roster_import_request()
        payload[field] = bad
        with pytest.raises(StaffingError):
            validate_roster_import(
                payload,
                site_id="pilot-course-a",
                deployment_id="pilot-a-edge-task-sim-v0",
                site_timezone="Asia/Shanghai",
            )
    for bad in (0, 1441, True):
        payload = roster_import_request()
        payload["workers"][0]["max_daily_minutes"] = bad
        with pytest.raises(StaffingError):
            validate_roster_import(
                payload,
                site_id="pilot-course-a",
                deployment_id="pilot-a-edge-task-sim-v0",
                site_timezone="Asia/Shanghai",
            )


def test_route_and_attempt_evidence_parsers_are_closed_bounded_and_detached():
    route_value = {
        "provider": "OPENAI",
        "region": "GLOBAL",
        "route_role": "PRIMARY",
        "route_id": "global-primary",
        "model_id": "gpt-model",
    }
    route = parse_attempt_route_evidence(route_value)
    route_value["model_id"] = "mutated"
    assert route.model_id == "gpt-model"
    generation = parse_generation_route_evidence(
        {
            "region": "GLOBAL",
            "readiness": "READY",
            "primary_provider": "OPENAI",
            "primary_model_id": "gpt-model",
            "backup_provider": "ANTHROPIC",
            "backup_model_id": "claude-model",
        }
    )
    started = parse_attempt_started_evidence(
        {
            "request_id": "request-1",
            "attempt_index": 0,
            "route": to_primitive(route),
            "input_digest": "a" * 64,
            "timeout_s": 12.0,
        }
    )
    finished_value = {
        "request_id": "request-1",
        "attempt_index": 0,
        "route": to_primitive(route),
        "status": "SUCCEEDED",
        "failure_code": None,
        "retryable": False,
        "security_failure": False,
        "provider_request_id": "provider-request",
        "finish_reason": "stop",
        "output_digest": "b" * 64,
        "input_tokens": 10,
        "output_tokens": 20,
    }
    finished = parse_attempt_finished_evidence(finished_value)
    result = parse_result_evidence(
        {
            "request_id": "request-1",
            "status": "SUCCEEDED",
            "failure_code": None,
            "selected_provider": "OPENAI",
            "selected_model_id": "gpt-model",
            "provider_request_id": "provider-request",
            "finish_reason": "stop",
            "input_digest": "a" * 64,
            "output_digest": "b" * 64,
            "attempts": [to_primitive(finished)],
            "candidate_count": 2,
            "bounded_summary": "validated output",
        }
    )
    assert generation.primary_provider == "OPENAI"
    assert started.route == route
    assert finished.route == route
    assert result.attempts == (finished,)


def test_all_five_provenance_records_enforce_direct_constructor_invariants():
    route = AttemptRouteEvidence("OPENAI", "GLOBAL", "PRIMARY", "route-1", "model-1")
    generation = GenerationRouteEvidence(
        "GLOBAL", "READY", "OPENAI", "model-1", "ANTHROPIC", "model-2"
    )
    started = AttemptStartedEvidence("request-1", 0, route, "a" * 64, 12.0)
    finished = AttemptFinishedEvidence(
        "request-1",
        0,
        route,
        "SUCCEEDED",
        None,
        False,
        False,
        "p" * 160,
        "f" * 160,
        "b" * 64,
        0,
        0,
    )
    result = ResultEvidence(
        "request-1",
        "SUCCEEDED",
        None,
        "OPENAI",
        "model-1",
        "p" * 160,
        "f" * 160,
        "a" * 64,
        "b" * 64,
        [finished],
        2,
        "bounded",
    )
    assert generation.backup_provider == "ANTHROPIC"
    assert started.timeout_s == 12.0
    assert result.attempts == (finished,)
    assert finished.input_tokens == finished.output_tokens == 0

    invalid_builders = (
        lambda: AttemptRouteEvidence("OPENAI", "GLOBAL", "PRIMARY", "røute", "model"),
        lambda: GenerationRouteEvidence("GLOBAL", "READY", "OPENAI", None, None, None),
        lambda: AttemptStartedEvidence("request-1", True, route, "a" * 64, 12.0),
        lambda: AttemptStartedEvidence("request-1", 0, route, "a" * 64, float("nan")),
        lambda: AttemptFinishedEvidence(
            "request-1", 0, route, "SUCCEEDED", "bad-code", False, False,
            None, None, "b" * 64, 1, 1,
        ),
        lambda: AttemptFinishedEvidence(
            "request-1", 0, route, "SUCCEEDED", None, False, False,
            "p" * 161, None, "b" * 64, 1, 1,
        ),
        lambda: AttemptFinishedEvidence(
            "request-1", 0, route, "SUCCEEDED", None, False, False,
            None, None, "b" * 64, True, 1,
        ),
        lambda: ResultEvidence(
            "request-1", "SUCCEEDED", None, "OPENAI", "model-1", None, None,
            "a" * 64, "b" * 64, (finished,), True, None,
        ),
    )
    for build in invalid_builders:
        with pytest.raises(StaffingError):
            build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: AttemptRouteEvidence([], "GLOBAL", "PRIMARY", "route", "model"),
        lambda: AttemptRouteEvidence("OPENAI", {}, "PRIMARY", "route", "model"),
        lambda: AttemptRouteEvidence("OPENAI", "GLOBAL", [], "route", "model"),
        lambda: GenerationRouteEvidence("GLOBAL", "READY", [], None, None, None),
        lambda: GenerationRouteEvidence("GLOBAL", "READY", None, None, {}, None),
        lambda: AttemptStartedEvidence(
            "request-1", 0, [], "a" * 64, 1.0
        ),
        lambda: AttemptFinishedEvidence(
            "request-1", 0, [], "SUCCEEDED", None, False, False,
            None, None, None, None, None,
        ),
        lambda: ResultEvidence(
            "request-1", "SUCCEEDED", None, [], None, None, None,
            "a" * 64, None, (), 0, None,
        ),
    ],
)
def test_direct_provenance_wrong_scalar_types_raise_stable_staffing_error(build):
    with pytest.raises(StaffingError):
        build()


def _valid_route_mapping():
    return {
        "provider": "OPENAI",
        "region": "GLOBAL",
        "route_role": "PRIMARY",
        "route_id": "global-primary",
        "model_id": "gpt-model",
    }


def _valid_finished_mapping():
    return {
        "request_id": "request-1",
        "attempt_index": 0,
        "route": _valid_route_mapping(),
        "status": "SUCCEEDED",
        "failure_code": None,
        "retryable": False,
        "security_failure": False,
        "provider_request_id": None,
        "finish_reason": None,
        "output_digest": "b" * 64,
        "input_tokens": 0,
        "output_tokens": 0,
    }


@pytest.mark.parametrize(
    "parse,bad_value",
    [
        (parse_attempt_route_evidence, {**_valid_route_mapping(), "provider": []}),
        (parse_attempt_route_evidence, {**_valid_route_mapping(), "region": {}}),
        (parse_attempt_route_evidence, {**_valid_route_mapping(), "route_role": []}),
        (
            parse_generation_route_evidence,
            {
                "region": [], "readiness": "READY",
                "primary_provider": "OPENAI", "primary_model_id": "model",
                "backup_provider": None, "backup_model_id": None,
            },
        ),
        (
            parse_generation_route_evidence,
            {
                "region": "GLOBAL", "readiness": {},
                "primary_provider": "OPENAI", "primary_model_id": "model",
                "backup_provider": None, "backup_model_id": None,
            },
        ),
        (
            parse_generation_route_evidence,
            {
                "region": "GLOBAL", "readiness": "READY",
                "primary_provider": [], "primary_model_id": None,
                "backup_provider": None, "backup_model_id": None,
            },
        ),
        (
            parse_attempt_started_evidence,
            {
                "request_id": "request-1", "attempt_index": 0,
                "route": {**_valid_route_mapping(), "provider": []},
                "input_digest": "a" * 64, "timeout_s": 1.0,
            },
        ),
        (parse_attempt_finished_evidence, {**_valid_finished_mapping(), "status": []}),
        (
            parse_result_evidence,
            {
                "request_id": "request-1", "status": [], "failure_code": None,
                "selected_provider": None, "selected_model_id": None,
                "provider_request_id": None, "finish_reason": None,
                "input_digest": "a" * 64, "output_digest": None,
                "attempts": [], "candidate_count": 0, "bounded_summary": None,
            },
        ),
        (
            parse_result_evidence,
            {
                "request_id": "request-1", "status": "SUCCEEDED", "failure_code": None,
                "selected_provider": {}, "selected_model_id": None,
                "provider_request_id": None, "finish_reason": None,
                "input_digest": "a" * 64, "output_digest": None,
                "attempts": [], "candidate_count": 0, "bounded_summary": None,
            },
        ),
        (
            parse_result_evidence,
            {
                "request_id": "request-1", "status": "SUCCEEDED", "failure_code": None,
                "selected_provider": None, "selected_model_id": None,
                "provider_request_id": None, "finish_reason": None,
                "input_digest": "a" * 64, "output_digest": None,
                "attempts": [{**_valid_finished_mapping(), "status": []}],
                "candidate_count": 0, "bounded_summary": None,
            },
        ),
    ],
)
def test_untrusted_provenance_parsers_never_leak_raw_type_errors(parse, bad_value):
    with pytest.raises(StaffingError):
        parse(bad_value)


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(prompt="injected"),
        lambda value: value.update(metadata={}),
        lambda value: value.update(attempt_index=2),
        lambda value: value.update(input_digest="A" * 64),
        lambda value: value.update(timeout_s=True),
        lambda value: value.update(timeout_s=0),
    ],
)
def test_attempt_parser_rejects_unknown_sensitive_or_unbounded_values(change):
    value = {
        "request_id": "request-1",
        "attempt_index": 0,
        "route": {
            "provider": "OPENAI",
            "region": "GLOBAL",
            "route_role": "PRIMARY",
            "route_id": "global-primary",
            "model_id": "gpt-model",
        },
        "input_digest": "a" * 64,
        "timeout_s": 12.0,
    }
    change(value)
    with pytest.raises(StaffingError):
        parse_attempt_started_evidence(value)


def test_result_parser_rejects_forbidden_nested_keys_and_overlong_text():
    base = {
        "request_id": "request-1",
        "status": "RESULT_UNKNOWN",
        "failure_code": "RESULT_UNKNOWN",
        "selected_provider": None,
        "selected_model_id": None,
        "provider_request_id": None,
        "finish_reason": None,
        "input_digest": "a" * 64,
        "output_digest": None,
        "attempts": [],
        "candidate_count": 0,
        "bounded_summary": None,
    }
    for field in ("prompt", "raw_response", "reasoning", "secret", "api_key", "headers", "metadata"):
        value = deepcopy(base)
        value[field] = "forbidden"
        with pytest.raises(StaffingError):
            parse_result_evidence(value)
    value = deepcopy(base)
    value["bounded_summary"] = "x" * 281
    with pytest.raises(StaffingError):
        parse_result_evidence(value)
    nested = deepcopy(base)
    nested["attempts"] = [
        {
            "request_id": "request-1",
            "attempt_index": 0,
            "route": {
                "provider": "OPENAI",
                "region": "GLOBAL",
                "route_role": "PRIMARY",
                "route_id": "global-primary",
                "model_id": "gpt-model",
                "secret": "must-not-cross",
            },
            "status": "UNAVAILABLE",
            "failure_code": "TIMEOUT",
            "retryable": True,
            "security_failure": False,
            "provider_request_id": None,
            "finish_reason": None,
            "output_digest": None,
            "input_tokens": None,
            "output_tokens": None,
        }
    ]
    with pytest.raises(StaffingError):
        parse_result_evidence(nested)
