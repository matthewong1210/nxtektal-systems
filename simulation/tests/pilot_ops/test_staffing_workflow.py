"""Closed staffing event parsing, transitions, and deterministic replay."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from nxt_pilot_ops.serialization import canonical_json_bytes, stable_digest, to_primitive
from nxt_pilot_ops.staffing.contracts import (
    PROMPT_TEMPLATE_VERSION,
    AddOperation,
    AttemptFinishedEvidence,
    AttemptRouteEvidence,
    AttemptStartedEvidence,
    EVENT_TYPES,
    ExceptionCancelledPayload,
    ExceptionCorrectedPayload,
    ExceptionRecordedPayload,
    GENERATION_TERMINALS,
    GenerationInterruptedPayload,
    GenerationReservedPayload,
    GenerationRouteEvidence,
    ManagerResponseCommittedPayload,
    ProviderAttemptFinishedPayload,
    ProviderAttemptStartedPayload,
    RemoveOperation,
    ResultEvidence,
    RosterImportedPayload,
    StaffingError,
    StaffingEvent,
    StaffingException,
    StaffingHistory,
    StoredCandidate,
    SuggestionIssuedPayload,
    SuggestionUnavailablePayload,
)
from nxt_pilot_ops.staffing.exceptions import exception_digest
from nxt_pilot_ops.staffing.plans import build_staffing_basis
from nxt_pilot_ops.staffing.projection import project_generation_request
from nxt_pilot_ops.staffing.roster import validate_roster_import
from nxt_pilot_ops.staffing.validator import CandidatePatch, CandidateValidation, validate_candidates
from nxt_pilot_ops.staffing.workflow import (
    EVENT_PAYLOAD_TYPES,
    candidate_patch_for_revalidation,
    event_from_record,
    parse_event,
    replay_staffing,
    scan_and_detach_event_tree,
    staffing_event_id,
    staffing_generation_id,
    stored_candidate_from_validation,
    transition,
    validate_replayable_result_evidence,
)

from .staffing_fixtures import roster_import_request


UTC = timezone.utc
SITE_ID = "pilot-course-a"
DEPLOYMENT_ID = "pilot-a-edge-task-sim-v0"
NOW = datetime(2026, 10, 5, 0, 0, 0, 123456, tzinfo=UTC)


def _error(code: str, function, *args, **kwargs) -> StaffingError:
    with pytest.raises(StaffingError) as raised:
        function(*args, **kwargs)
    assert raised.value.code == code
    return raised.value


def _roster_payload() -> RosterImportedPayload:
    request = roster_import_request()
    roster = validate_roster_import(
        request,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_timezone="Asia/Shanghai",
    )
    return RosterImportedPayload(
        "roster-request-1",
        stable_digest(request),
        roster.revision,
        roster.roster_digest,
        roster,
        "course-manager",
        "weekly.csv",
    )


def _event(event_type: str, payload: object, sequence: int, *, cause: str | None = None,
           occurred_at: datetime | None = None) -> StaffingEvent:
    timestamp = NOW + timedelta(microseconds=sequence) if occurred_at is None else occurred_at
    return StaffingEvent(
        event_type,
        staffing_event_id(
            event_type,
            sequence,
            SITE_ID,
            DEPLOYMENT_ID,
            timestamp,
            cause,
            payload,
        ),
        sequence,
        SITE_ID,
        DEPLOYMENT_ID,
        timestamp,
        cause,
        payload,
    )


def _roster_history() -> StaffingHistory:
    return transition(StaffingHistory(()), _event("roster_imported", _roster_payload(), 1))


def _reservation(history: StaffingHistory, *, request_id: str = "generation-request-1",
                 request_digest: str | None = None, nonce: bytes = b"0123456789abcdef"):
    digest = request_digest or stable_digest({"request_id": request_id})
    basis = build_staffing_basis(history, date(2026, 10, 5))
    projection = project_generation_request(
        basis,
        alias_nonce=nonce,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        language="zh-CN",
    )
    generation_id = staffing_generation_id(SITE_ID, DEPLOYMENT_ID, request_id, digest)
    payload = GenerationReservedPayload(
        request_id,
        digest,
        generation_id,
        date(2026, 10, 5),
        projection.basis_snapshot,
        projection.provider_payload,
        projection.alias_nonce_digest,
        projection.worker_alias_to_staff_id,
        projection.assignment_alias_to_assignment_id,
        projection.input_digest,
        PROMPT_TEMPLATE_VERSION,
        "zh-CN",
        GenerationRouteEvidence("CN", "READY", "KIMI", "kimi-k2", None, None),
        None,
        "course-manager",
    )
    return payload, _event("generation_reserved", payload, history.record_count + 1)


def _issued_history() -> tuple[StaffingHistory, GenerationReservedPayload]:
    history = _roster_history()
    reservation, reserved = _reservation(history)
    history = transition(history, reserved)
    route = AttemptRouteEvidence("KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2")
    started = AttemptStartedEvidence(
        reservation.generation_id, 0, route, reservation.input_digest, 10.0
    )
    history = transition(history, _event(
        "provider_attempt_started",
        ProviderAttemptStartedPayload(reservation.request_digest, reservation.generation_id, started),
        history.record_count + 1,
        cause=reservation.generation_id,
    ))
    finished = AttemptFinishedEvidence(
        reservation.generation_id, 0, route, "SUCCEEDED", None, False, False,
        None, "stop", "9" * 64, None, None,
    )
    history = transition(history, _event(
        "provider_attempt_finished",
        ProviderAttemptFinishedPayload(reservation.request_digest, reservation.generation_id,
                                       finished),
        history.record_count + 1,
        cause=reservation.generation_id,
    ))
    result = ResultEvidence(
        reservation.generation_id, "SUCCEEDED", None, "KIMI", "kimi-k2", None,
        "stop", reservation.input_digest, "9" * 64, (finished,), 1, None,
    )
    patch = CandidatePatch(1, (), "safe", ())
    validation = validate_candidates(
        reservation.basis_snapshot,
        (patch,),
        worker_alias_to_staff_id=reservation.worker_alias_to_staff_id,
        assignment_alias_to_assignment_id=reservation.assignment_alias_to_assignment_id,
        prompt_template_version=reservation.prompt_template_version,
    )[0]
    candidate = stored_candidate_from_validation(patch, validation)
    issued = SuggestionIssuedPayload(
        reservation.request_digest,
        reservation.generation_id,
        result,
        (candidate,),
        stable_digest(to_primitive((candidate,))),
    )
    history = transition(history, _event(
        "suggestion_issued", issued, history.record_count + 1,
        cause=reservation.generation_id,
    ))
    return history, reservation


def test_dispatch_is_total_and_manager_response_is_not_a_generation_terminal():
    assert frozenset(EVENT_PAYLOAD_TYPES) == EVENT_TYPES

    reservation, reserved = _reservation(_roster_history())
    manager = ManagerResponseCommittedPayload(
        "manager-request-1",
        "a" * 64,
        reservation.generation_id,
        "REJECT",
        "OTHER",
        None,
        None,
        None,
        "manager",
        None,
        reservation.basis_snapshot,
    )
    history = StaffingHistory((reserved, _event(
        "manager_response_committed", manager, reserved.sequence + 1,
        cause=reservation.generation_id,
    )))
    assert not history.generation(reservation.generation_id).is_terminal


def test_stored_candidate_offsets_are_aligned_and_restore_fixed_offsets():
    operation = AddOperation(
        "ADD",
        "worker_abc",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 5, 9, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 10, 5, 10, tzinfo=timezone(timedelta(hours=8))),
    )
    candidate = CandidatePatch(1, (operation,), "safe", ())
    validation = CandidateValidation(1, False, ("INVALID_INTERVAL",), (), None, None)
    stored = stored_candidate_from_validation(candidate, validation)
    assert stored.operation_offset_minutes == ((480, 480),)
    assert to_primitive(stored.operations[0])["start_at"] == "2026-10-05T01:00:00.000000Z"
    restored = candidate_patch_for_revalidation(stored)
    assert restored.operations[0].start_at.utcoffset() == timedelta(hours=8)
    assert restored.operations[0].start_at.astimezone(UTC) == operation.start_at.astimezone(UTC)

    _error(
        "staffing_invalid_evidence",
        StoredCandidate,
        1,
        (RemoveOperation("REMOVE", "assignment_abc"),),
        ((0, 0),),
        "safe",
        (),
        ("INVALID_INTERVAL",),
        (),
        None,
        None,
    )
    oversized = StoredCandidate(
        1,
        tuple(RemoveOperation("REMOVE", f"assignment_{index}") for index in range(33)),
        tuple((None, None) for _ in range(33)),
        "safe",
        (),
        ("INVALID_INTERVAL",),
        (),
        None,
        None,
    )
    _error("staffing_invalid_evidence", candidate_patch_for_revalidation, oversized)


def test_event_and_generation_ids_are_content_derived_and_parse_round_trips_exactly():
    payload = _roster_payload()
    event = _event("roster_imported", payload, 1)
    primitive = to_primitive(event)
    parsed = parse_event(primitive, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)
    assert canonical_json_bytes(to_primitive(parsed)) == canonical_json_bytes(primitive)
    assert event_from_record(primitive, parsed.payload) == parsed

    changed = dict(primitive)
    changed["sequence"] = 2
    _error("staffing_invalid_event", parse_event, changed, site_id=SITE_ID,
           deployment_id=DEPLOYMENT_ID)
    mismatched_body = dict(primitive)
    mismatched_body["payload"] = {"different": True}
    _error("staffing_invalid_event", event_from_record, mismatched_body, parsed.payload)
    assert staffing_generation_id(SITE_ID, DEPLOYMENT_ID, "r1", "a" * 64) != staffing_generation_id(
        SITE_ID, DEPLOYMENT_ID, "r2", "a" * 64
    )


def test_parser_rejects_foreign_identity_and_sensitive_key_before_unknown_shape():
    primitive = to_primitive(_event("roster_imported", _roster_payload(), 1))
    foreign = dict(primitive)
    foreign["site_id"] = "foreign-site"
    _error("staffing_identity_mismatch", parse_event, foreign, site_id=SITE_ID,
           deployment_id=DEPLOYMENT_ID)

    hostile = dict(primitive)
    hostile["unknown"] = True
    hostile["payload"] = dict(hostile["payload"])
    hostile["payload"]["metadata"] = "do not retain"
    error = _error("staffing_invalid_evidence", parse_event, hostile, site_id=SITE_ID,
                   deployment_id=DEPLOYMENT_ID)
    assert "do not retain" not in str(error)


@pytest.mark.parametrize("failure", ("cycle", "depth"))
@pytest.mark.parametrize("context", ("root", "payload"))
@pytest.mark.parametrize("sensitive_first", (False, True))
def test_sensitive_key_precedes_saved_tree_failures_in_every_key_order(
    failure: str, context: str, sensitive_first: bool
):
    branch: dict[str, object] = {}
    if failure == "cycle":
        branch["loop"] = branch
    else:
        cursor = branch
        for _ in range(34):
            child: dict[str, object] = {}
            cursor["next"] = child
            cursor = child
    container: dict[str, object] = {}
    ordered_items = (
        (("metadata", "do not retain"), ("branch", branch))
        if sensitive_first
        else (("branch", branch), ("metadata", "do not retain"))
    )
    for key, value in ordered_items:
        container[key] = value
    value = {"payload": container} if context == "payload" else container
    expected = "staffing_invalid_evidence" if context == "payload" else "staffing_invalid_event"
    error = _error(expected, scan_and_detach_event_tree, value)
    assert error.detail == "forbidden event field"
    assert "do not retain" not in str(error)


def test_transition_enforces_exact_sequence_identity_and_replay_prefixes():
    roster = _event("roster_imported", _roster_payload(), 1)
    history = transition(StaffingHistory(()), roster)
    assert replay_staffing((roster,), site_id=SITE_ID, deployment_id=DEPLOYMENT_ID) == history
    _error("staffing_invalid_event", transition, history, replace(roster, sequence=3))
    foreign_time = NOW + timedelta(microseconds=2)
    foreign = StaffingEvent(
        "roster_imported",
        staffing_event_id(
            "roster_imported",
            2,
            "foreign-site",
            DEPLOYMENT_ID,
            foreign_time,
            None,
            roster.payload,
        ),
        2,
        "foreign-site",
        DEPLOYMENT_ID,
        foreign_time,
        None,
        roster.payload,
    )
    _error("staffing_identity_mismatch", transition, history, foreign)


def test_malformed_identity_is_invalid_event_before_foreign_identity_classification():
    history = _roster_history()
    reservation, reserved = _reservation(history)
    malformed_reserved = replace(reserved, site_id="")
    _error("staffing_invalid_event", transition, history, malformed_reserved)

    roster = _event("roster_imported", _roster_payload(), 1)
    malformed_roster = replace(roster, site_id="")
    _error(
        "staffing_invalid_event",
        replay_staffing,
        (malformed_roster,),
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
    )

    foreign = StaffingEvent(
        roster.event_type,
        staffing_event_id(
            roster.event_type,
            roster.sequence,
            "foreign-site",
            DEPLOYMENT_ID,
            roster.occurred_at_utc,
            roster.causation_id,
            roster.payload,
        ),
        roster.sequence,
        "foreign-site",
        DEPLOYMENT_ID,
        roster.occurred_at_utc,
        roster.causation_id,
        roster.payload,
    )
    _error(
        "staffing_identity_mismatch",
        replay_staffing,
        (foreign,),
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
    )


def test_direct_event_id_requires_exact_string_without_equality_hooks():
    class EventIdSubclass(str):
        pass

    base = _event("roster_imported", _roster_payload(), 1)
    subclassed = replace(base, event_id=EventIdSubclass(base.event_id))
    _error("staffing_invalid_event", transition, StaffingHistory(()), subclassed)

    class EqualityTrap:
        def __init__(self) -> None:
            self.calls = 0

        def __eq__(self, other: object) -> bool:
            self.calls += 1
            return True

        def __ne__(self, other: object) -> bool:
            self.calls += 1
            return False

    trap = EqualityTrap()
    hooked = replace(base, event_id=trap)
    _error("staffing_invalid_event", transition, StaffingHistory(()), hooked)
    assert trap.calls == 0


@pytest.mark.parametrize("source_ref", ("x" * 257, "=formula", "has\u0000control"))
def test_direct_roster_transition_revalidates_source_ref(source_ref: str):
    payload = replace(_roster_payload(), source_ref=source_ref)
    _error(
        "staffing_invalid_evidence",
        transition,
        StaffingHistory(()),
        _event("roster_imported", payload, 1),
    )


@pytest.mark.parametrize("revision", (True, 1.0))
def test_direct_roster_transition_requires_exact_integer_revision(revision: object):
    payload = replace(_roster_payload(), roster_revision=revision)
    _error(
        "staffing_invalid_evidence",
        transition,
        StaffingHistory(()),
        _event("roster_imported", payload, 1),
    )


def test_started_timeout_requires_exact_float_and_route_binding():
    history = _roster_history()
    reservation, reserved = _reservation(history)
    history = transition(history, reserved)
    route = AttemptRouteEvidence("KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2")
    bad = AttemptStartedEvidence(
        reservation.generation_id, 0, route, reservation.input_digest, 1
    )
    bad_payload = ProviderAttemptStartedPayload(
        reservation.request_digest, reservation.generation_id, bad
    )
    _error(
        "staffing_invalid_evidence",
        transition,
        history,
        _event("provider_attempt_started", bad_payload, history.record_count + 1,
               cause=reservation.generation_id),
    )

    good = replace(bad, timeout_s=15.0)
    started = _event(
        "provider_attempt_started",
        ProviderAttemptStartedPayload(reservation.request_digest, reservation.generation_id, good),
        history.record_count + 1,
        cause=reservation.generation_id,
    )
    history = transition(history, started)
    raw = to_primitive(started)
    raw["payload"]["evidence"]["timeout_s"] = 15
    raw["event_id"] = staffing_event_id(
        raw["event_type"], raw["sequence"], raw["site_id"], raw["deployment_id"],
        started.occurred_at_utc, raw["causation_id"], raw["payload"],
    )
    _error("staffing_invalid_evidence", parse_event, raw, site_id=SITE_ID,
           deployment_id=DEPLOYMENT_ID)


@pytest.mark.parametrize(
    ("status", "failure_code", "retryable", "security_failure", "accepted"),
    (
        ("SUCCEEDED", None, False, False, False),
        ("INVALID_RESPONSE", "SCHEMA_MISMATCH", False, False, False),
        ("SECURITY_ERROR", "TLS_VERIFICATION_FAILED", False, True, False),
        ("UNAVAILABLE", "DEADLINE_EXHAUSTED", False, False, False),
        ("UNAVAILABLE", "DNS_FAILURE", True, False, True),
    ),
)
def test_global_backup_start_requires_fallback_eligible_primary_failure(
    status: str,
    failure_code: str | None,
    retryable: bool,
    security_failure: bool,
    accepted: bool,
):
    history = _roster_history()
    reservation, _ = _reservation(history)
    reservation = replace(
        reservation,
        route=GenerationRouteEvidence(
            "GLOBAL", "READY", "OPENAI", "gpt-primary", "ANTHROPIC", "claude-backup"
        ),
    )
    history = transition(
        history,
        _event("generation_reserved", reservation, history.record_count + 1),
    )
    primary_route = AttemptRouteEvidence(
        "OPENAI", "GLOBAL", "PRIMARY", "global-openai-v1", "gpt-primary"
    )
    history = transition(
        history,
        _event(
            "provider_attempt_started",
            ProviderAttemptStartedPayload(
                reservation.request_digest,
                reservation.generation_id,
                AttemptStartedEvidence(
                    reservation.generation_id,
                    0,
                    primary_route,
                    reservation.input_digest,
                    12.0,
                ),
            ),
            history.record_count + 1,
            cause=reservation.generation_id,
        ),
    )
    succeeded = status == "SUCCEEDED"
    finish = AttemptFinishedEvidence(
        reservation.generation_id,
        0,
        primary_route,
        status,
        failure_code,
        retryable,
        security_failure,
        "provider-request" if succeeded else None,
        "completed" if succeeded else None,
        "a" * 64 if succeeded else None,
        None,
        None,
    )
    history = transition(
        history,
        _event(
            "provider_attempt_finished",
            ProviderAttemptFinishedPayload(
                reservation.request_digest, reservation.generation_id, finish
            ),
            history.record_count + 1,
            cause=reservation.generation_id,
        ),
    )
    backup = _event(
        "provider_attempt_started",
        ProviderAttemptStartedPayload(
            reservation.request_digest,
            reservation.generation_id,
            AttemptStartedEvidence(
                reservation.generation_id,
                1,
                AttemptRouteEvidence(
                    "ANTHROPIC",
                    "GLOBAL",
                    "BACKUP",
                    "global-anthropic-v1",
                    "claude-backup",
                ),
                reservation.input_digest,
                8.0,
            ),
        ),
        history.record_count + 1,
        cause=reservation.generation_id,
    )
    if accepted:
        assert transition(history, backup).events[-1] == backup
    else:
        _error("staffing_invalid_event", transition, history, backup)


def test_result_evidence_must_match_persisted_attempts_and_gateway_matrix():
    history = _roster_history()
    reservation, reserved = _reservation(history)
    history = transition(history, reserved)
    route = AttemptRouteEvidence("KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2")
    started_evidence = AttemptStartedEvidence(
        reservation.generation_id, 0, route, reservation.input_digest, 15.0
    )
    history = transition(history, _event(
        "provider_attempt_started",
        ProviderAttemptStartedPayload(reservation.request_digest, reservation.generation_id,
                                      started_evidence),
        history.record_count + 1,
        cause=reservation.generation_id,
    ))
    finished_evidence = AttemptFinishedEvidence(
        reservation.generation_id,
        0,
        route,
        "SUCCEEDED",
        None,
        False,
        False,
        "provider-request",
        "stop",
        "b" * 64,
        10,
        5,
    )
    history = transition(history, _event(
        "provider_attempt_finished",
        ProviderAttemptFinishedPayload(reservation.request_digest, reservation.generation_id,
                                       finished_evidence),
        history.record_count + 1,
        cause=reservation.generation_id,
    ))
    from nxt_pilot_ops.staffing.contracts import ResultEvidence

    result = ResultEvidence(
        reservation.generation_id,
        "SUCCEEDED",
        None,
        "KIMI",
        "kimi-k2",
        "provider-request",
        "stop",
        reservation.input_digest,
        "b" * 64,
        (finished_evidence,),
        1,
        None,
    )
    assert validate_replayable_result_evidence(
        history, reservation, result, reservation.generation_id, "SUCCEEDED", None
    ) is result
    _error(
        "staffing_invalid_evidence",
        validate_replayable_result_evidence,
        history,
        reservation,
        replace(result, bounded_summary="raw text"),
        reservation.generation_id,
        "SUCCEEDED",
        None,
    )


def test_interruption_is_the_only_terminal_allowed_with_an_unmatched_start():
    history = _roster_history()
    reservation, reserved = _reservation(history)
    history = transition(history, reserved)
    route = AttemptRouteEvidence("KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2")
    started = AttemptStartedEvidence(
        reservation.generation_id, 0, route, reservation.input_digest, 10.0
    )
    history = transition(history, _event(
        "provider_attempt_started",
        ProviderAttemptStartedPayload(reservation.request_digest, reservation.generation_id, started),
        history.record_count + 1,
        cause=reservation.generation_id,
    ))
    interrupted_at = NOW + timedelta(microseconds=history.record_count + 1)
    interrupted = _event(
        "generation_interrupted",
        GenerationInterruptedPayload(
            reservation.request_digest,
            reservation.generation_id,
            "RESULT_UNKNOWN",
            interrupted_at,
        ),
        history.record_count + 1,
        cause=reservation.generation_id,
        occurred_at=interrupted_at,
    )
    history = transition(history, interrupted)
    assert history.generation(reservation.generation_id).is_terminal
    _error("staffing_invalid_event", transition, history, interrupted)


def test_direct_interruption_requires_exact_utc_timestamp_without_equality_hooks():
    history = _roster_history()
    reservation, reserved = _reservation(history)
    history = transition(history, reserved)
    occurred = NOW + timedelta(microseconds=history.record_count + 1)
    offset_payload = GenerationInterruptedPayload(
        reservation.request_digest,
        reservation.generation_id,
        "RESULT_UNKNOWN",
        occurred.astimezone(timezone(timedelta(hours=1))),
    )
    _error(
        "staffing_invalid_event",
        transition,
        history,
        _event(
            "generation_interrupted",
            offset_payload,
            history.record_count + 1,
            cause=reservation.generation_id,
            occurred_at=occurred,
        ),
    )

    class EqualityTrapDateTime(datetime):
        calls = 0

        def __eq__(self, other: object) -> bool:
            type(self).calls += 1
            return True

    trapped_time = EqualityTrapDateTime(
        occurred.year,
        occurred.month,
        occurred.day,
        occurred.hour,
        occurred.minute,
        occurred.second,
        occurred.microsecond,
        tzinfo=UTC,
    )
    trapped_payload = replace(offset_payload, interrupted_at_utc=trapped_time)
    _error(
        "staffing_invalid_event",
        transition,
        history,
        _event(
            "generation_interrupted",
            trapped_payload,
            history.record_count + 1,
            cause=reservation.generation_id,
            occurred_at=occurred,
        ),
    )
    assert EqualityTrapDateTime.calls == 0


def test_event_from_record_rejects_offset_equivalent_interruption_timestamp():
    reservation, _ = _reservation(_roster_history())
    occurred = NOW + timedelta(microseconds=3)
    payload = GenerationInterruptedPayload(
        reservation.request_digest,
        reservation.generation_id,
        "RESULT_UNKNOWN",
        occurred.astimezone(timezone(timedelta(hours=1))),
    )
    event = _event(
        "generation_interrupted",
        payload,
        3,
        cause=reservation.generation_id,
        occurred_at=occurred,
    )
    _error("staffing_invalid_event", event_from_record, to_primitive(event), payload)


def test_event_from_record_rejects_datetime_subclass_without_equality_hooks():
    class EqualityTrapDateTime(datetime):
        calls = 0

        def __eq__(self, other: object) -> bool:
            type(self).calls += 1
            return True

        def __ne__(self, other: object) -> bool:
            type(self).calls += 1
            return False

    reservation, _ = _reservation(_roster_history())
    occurred = NOW + timedelta(microseconds=3)
    trapped_time = EqualityTrapDateTime(
        occurred.year,
        occurred.month,
        occurred.day,
        occurred.hour,
        occurred.minute,
        occurred.second,
        occurred.microsecond,
        tzinfo=UTC,
    )
    payload = GenerationInterruptedPayload(
        reservation.request_digest,
        reservation.generation_id,
        "RESULT_UNKNOWN",
        trapped_time,
    )
    event = _event(
        "generation_interrupted",
        payload,
        3,
        cause=reservation.generation_id,
        occurred_at=occurred,
    )
    _error("staffing_invalid_event", event_from_record, to_primitive(event), payload)
    assert EqualityTrapDateTime.calls == 0


def test_raw_terminal_parser_rejects_intrinsic_count_digest_and_summary_drift():
    history = _roster_history()
    reservation, reserved = _reservation(history)
    history = transition(history, reserved)
    route = AttemptRouteEvidence("KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2")
    started = AttemptStartedEvidence(
        reservation.generation_id, 0, route, reservation.input_digest, 10.0
    )
    history = transition(history, _event(
        "provider_attempt_started",
        ProviderAttemptStartedPayload(reservation.request_digest, reservation.generation_id, started),
        history.record_count + 1,
        cause=reservation.generation_id,
    ))
    finished = AttemptFinishedEvidence(
        reservation.generation_id, 0, route, "SUCCEEDED", None, False, False,
        None, "stop", "b" * 64, None, None,
    )
    history = transition(history, _event(
        "provider_attempt_finished",
        ProviderAttemptFinishedPayload(reservation.request_digest, reservation.generation_id, finished),
        history.record_count + 1,
        cause=reservation.generation_id,
    ))
    from nxt_pilot_ops.staffing.contracts import ResultEvidence

    result = ResultEvidence(
        reservation.generation_id, "SUCCEEDED", None, "KIMI", "kimi-k2", None,
        "stop", reservation.input_digest, "b" * 64, (finished,), 1, None,
    )
    patch = CandidatePatch(1, (), "safe", ())
    validation = validate_candidates(
        reservation.basis_snapshot,
        (patch,),
        worker_alias_to_staff_id=reservation.worker_alias_to_staff_id,
        assignment_alias_to_assignment_id=reservation.assignment_alias_to_assignment_id,
        prompt_template_version=reservation.prompt_template_version,
    )[0]
    candidate = stored_candidate_from_validation(patch, validation)
    payload = SuggestionIssuedPayload(
        reservation.request_digest,
        reservation.generation_id,
        result,
        (candidate,),
        stable_digest(to_primitive((candidate,))),
    )
    terminal = _event(
        "suggestion_issued", payload, history.record_count + 1,
        cause=reservation.generation_id,
    )
    raw = to_primitive(terminal)
    raw["payload"]["result"]["bounded_summary"] = "must not persist"
    raw["event_id"] = staffing_event_id(
        raw["event_type"], raw["sequence"], raw["site_id"], raw["deployment_id"],
        terminal.occurred_at_utc, raw["causation_id"], raw["payload"],
    )
    _error("staffing_invalid_evidence", parse_event, raw, site_id=SITE_ID,
           deployment_id=DEPLOYMENT_ID)

    raw = to_primitive(terminal)
    raw["payload"]["candidate_set_digest"] = "c" * 64
    raw["event_id"] = staffing_event_id(
        raw["event_type"], raw["sequence"], raw["site_id"], raw["deployment_id"],
        terminal.occurred_at_utc, raw["causation_id"], raw["payload"],
    )
    _error("staffing_invalid_evidence", parse_event, raw, site_id=SITE_ID,
           deployment_id=DEPLOYMENT_ID)


def test_unavailable_reservation_cannot_start_an_attempt():
    history = _roster_history()
    reservation, _ = _reservation(history)
    reservation = replace(
        reservation,
        route=GenerationRouteEvidence("CN", "UNAVAILABLE", "KIMI", "kimi-k2", None, None),
    )
    reserved = _event("generation_reserved", reservation, history.record_count + 1)
    history = transition(history, reserved)
    route = AttemptRouteEvidence("KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2")
    started = AttemptStartedEvidence(
        reservation.generation_id, 0, route, reservation.input_digest, 10.0
    )
    _error(
        "staffing_invalid_evidence",
        transition,
        history,
        _event(
            "provider_attempt_started",
            ProviderAttemptStartedPayload(
                reservation.request_digest, reservation.generation_id, started
            ),
            history.record_count + 1,
            cause=reservation.generation_id,
        ),
    )


def test_result_helper_binds_the_exact_persisted_reservation():
    history = _roster_history()
    reservation, reserved = _reservation(history)
    history = transition(history, reserved)
    from nxt_pilot_ops.staffing.contracts import ResultEvidence

    result = ResultEvidence(
        reservation.generation_id,
        "CONFIGURATION_ERROR",
        "PROVIDER_UNCONFIGURED",
        None,
        None,
        None,
        None,
        reservation.input_digest,
        None,
        (),
        0,
        None,
    )
    forged = replace(
        reservation,
        route=GenerationRouteEvidence("CN", "UNAVAILABLE", "KIMI", "other-model", None, None),
    )
    _error(
        "staffing_invalid_evidence",
        validate_replayable_result_evidence,
        history,
        forged,
        result,
        forged.generation_id,
        "CONFIGURATION_ERROR",
        "PROVIDER_UNCONFIGURED",
    )


def test_outer_canonical_time_error_precedes_payload_shape_error():
    raw = to_primitive(_event("roster_imported", _roster_payload(), 1))
    raw["occurred_at_utc"] = "2026-10-05T00:00:00Z"
    raw["payload"] = {"broken": True}
    _error("staffing_invalid_event", parse_event, raw, site_id=SITE_ID,
           deployment_id=DEPLOYMENT_ID)


def _rebind_raw_event_id(raw: dict[str, object], occurred_at: datetime) -> None:
    raw["event_id"] = staffing_event_id(
        raw["event_type"],
        raw["sequence"],
        raw["site_id"],
        raw["deployment_id"],
        occurred_at,
        raw["causation_id"],
        raw["payload"],
    )


def test_raw_payload_parsers_reject_self_coherence_mutations():
    roster_event = _event("roster_imported", _roster_payload(), 1)
    raw = to_primitive(roster_event)
    raw["payload"]["roster_revision"] = 2
    _rebind_raw_event_id(raw, roster_event.occurred_at_utc)
    _error("staffing_invalid_evidence", parse_event, raw, site_id=SITE_ID,
           deployment_id=DEPLOYMENT_ID)


def test_all_eleven_event_payloads_round_trip_as_exact_canonical_values():
    roster_payload = _roster_payload()
    roster_event = _event("roster_imported", roster_payload, 1)
    history = transition(StaffingHistory(()), roster_event)
    reservation, reserved = _reservation(history)
    assignment = reservation.basis_snapshot.assignments[0]
    exception = StaffingException(
        "exception_demo",
        reservation.service_date,
        assignment.staff_id,
        "LEAVE",
        assignment.start_at,
        assignment.end_at,
        None,
    )
    replacement = replace(exception, kind="UNAVAILABLE", note="updated")
    exception_record = ExceptionRecordedPayload(
        "exception-record-1",
        "1" * 64,
        exception.exception_id,
        exception.service_date,
        1,
        exception_digest(exception),
        exception,
        "manager",
    )
    exception_cancel = ExceptionCancelledPayload(
        "exception-cancel-1",
        "2" * 64,
        exception.exception_id,
        2,
        exception,
        "manager",
        "cancelled",
    )
    exception_correct = ExceptionCorrectedPayload(
        "exception-correct-1",
        "3" * 64,
        exception.exception_id,
        replacement,
        2,
        exception_digest(replacement),
        exception,
        "manager",
    )
    route = AttemptRouteEvidence("KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2")
    started_evidence = AttemptStartedEvidence(
        reservation.generation_id, 0, route, reservation.input_digest, 10.0
    )
    finished_evidence = AttemptFinishedEvidence(
        reservation.generation_id, 0, route, "SUCCEEDED", None, False, False,
        None, "stop", "4" * 64, None, None,
    )
    from nxt_pilot_ops.staffing.contracts import ResultEvidence

    result = ResultEvidence(
        reservation.generation_id, "SUCCEEDED", None, "KIMI", "kimi-k2", None,
        "stop", reservation.input_digest, "4" * 64, (finished_evidence,), 1, None,
    )
    patch = CandidatePatch(1, (), "safe", ())
    validation = validate_candidates(
        reservation.basis_snapshot,
        (patch,),
        worker_alias_to_staff_id=reservation.worker_alias_to_staff_id,
        assignment_alias_to_assignment_id=reservation.assignment_alias_to_assignment_id,
        prompt_template_version=reservation.prompt_template_version,
    )[0]
    candidate = stored_candidate_from_validation(patch, validation)
    issued = SuggestionIssuedPayload(
        reservation.request_digest,
        reservation.generation_id,
        result,
        (candidate,),
        stable_digest(to_primitive((candidate,))),
    )
    unavailable = SuggestionUnavailablePayload(
        reservation.request_digest,
        reservation.generation_id,
        replace(result, candidate_count=0),
        (),
        None,
        "INVALID_RESPONSE",
        "invalid_provider_shape",
    )
    manager = ManagerResponseCommittedPayload(
        "manager-request-1",
        "5" * 64,
        reservation.generation_id,
        "REJECT",
        "OTHER",
        None,
        None,
        None,
        "manager",
        "handled locally",
        reservation.basis_snapshot,
    )
    interrupted_at = NOW + timedelta(microseconds=6)
    events = (
        roster_event,
        _event("exception_recorded", exception_record, 2),
        _event("exception_cancelled", exception_cancel, 3, cause=exception.exception_id),
        _event("exception_corrected", exception_correct, 4, cause=exception.exception_id),
        reserved,
        _event(
            "generation_interrupted",
            GenerationInterruptedPayload(
                reservation.request_digest,
                reservation.generation_id,
                "RESULT_UNKNOWN",
                interrupted_at,
            ),
            6,
            cause=reservation.generation_id,
            occurred_at=interrupted_at,
        ),
        _event(
            "provider_attempt_started",
            ProviderAttemptStartedPayload(
                reservation.request_digest, reservation.generation_id, started_evidence
            ),
            7,
            cause=reservation.generation_id,
        ),
        _event(
            "provider_attempt_finished",
            ProviderAttemptFinishedPayload(
                reservation.request_digest, reservation.generation_id, finished_evidence
            ),
            8,
            cause=reservation.generation_id,
        ),
        _event("suggestion_issued", issued, 9, cause=reservation.generation_id),
        _event("suggestion_unavailable", unavailable, 10, cause=reservation.generation_id),
        _event("manager_response_committed", manager, 11, cause=reservation.generation_id),
    )
    assert {item.event_type for item in events} == EVENT_TYPES
    for event in events:
        primitive = to_primitive(event)
        parsed = parse_event(primitive, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)
        assert canonical_json_bytes(to_primitive(parsed)) == canonical_json_bytes(primitive)
        broken = to_primitive(event)
        broken["payload"]["unknown"] = True
        _error("staffing_invalid_evidence", parse_event, broken, site_id=SITE_ID,
               deployment_id=DEPLOYMENT_ID)

    history = _roster_history()
    reservation, reserved = _reservation(history)
    raw = to_primitive(reserved)
    raw["payload"]["route"]["readiness"] = "DEGRADED_BACKUP_UNCONFIGURED"
    _rebind_raw_event_id(raw, reserved.occurred_at_utc)
    _error("staffing_invalid_evidence", parse_event, raw, site_id=SITE_ID,
           deployment_id=DEPLOYMENT_ID)


def test_issued_suggestion_then_one_manager_response_is_the_terminal_lifecycle():
    history = _roster_history()
    reservation, reserved = _reservation(history)
    history = transition(history, reserved)
    route = AttemptRouteEvidence("KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2")
    started = AttemptStartedEvidence(
        reservation.generation_id, 0, route, reservation.input_digest, 10.0
    )
    history = transition(history, _event(
        "provider_attempt_started",
        ProviderAttemptStartedPayload(reservation.request_digest, reservation.generation_id, started),
        history.record_count + 1,
        cause=reservation.generation_id,
    ))
    finished = AttemptFinishedEvidence(
        reservation.generation_id, 0, route, "SUCCEEDED", None, False, False,
        None, "stop", "6" * 64, None, None,
    )
    history = transition(history, _event(
        "provider_attempt_finished",
        ProviderAttemptFinishedPayload(reservation.request_digest, reservation.generation_id, finished),
        history.record_count + 1,
        cause=reservation.generation_id,
    ))
    from nxt_pilot_ops.staffing.contracts import ResultEvidence

    result = ResultEvidence(
        reservation.generation_id, "SUCCEEDED", None, "KIMI", "kimi-k2", None,
        "stop", reservation.input_digest, "6" * 64, (finished,), 1, None,
    )
    patch = CandidatePatch(1, (), "safe", ())
    validation = validate_candidates(
        reservation.basis_snapshot,
        (patch,),
        worker_alias_to_staff_id=reservation.worker_alias_to_staff_id,
        assignment_alias_to_assignment_id=reservation.assignment_alias_to_assignment_id,
        prompt_template_version=reservation.prompt_template_version,
    )[0]
    candidate = stored_candidate_from_validation(patch, validation)
    issued = SuggestionIssuedPayload(
        reservation.request_digest,
        reservation.generation_id,
        result,
        (candidate,),
        stable_digest(to_primitive((candidate,))),
    )
    history = transition(history, _event(
        "suggestion_issued", issued, history.record_count + 1,
        cause=reservation.generation_id,
    ))
    assert history.generation(reservation.generation_id).is_terminal

    manager = ManagerResponseCommittedPayload(
        "manager-request-unique",
        "7" * 64,
        reservation.generation_id,
        "REJECT",
        "OTHER",
        None,
        None,
        None,
        "manager",
        None,
        reservation.basis_snapshot,
    )
    response = _event(
        "manager_response_committed", manager, history.record_count + 1,
        cause=reservation.generation_id,
    )
    history = transition(history, response)
    assert history.generation(reservation.generation_id).is_terminal
    second = replace(
        response,
        event_id=staffing_event_id(
            "manager_response_committed",
            history.record_count + 1,
            SITE_ID,
            DEPLOYMENT_ID,
            response.occurred_at_utc,
            reservation.generation_id,
            replace(manager, request_id="manager-request-second", request_digest="8" * 64),
        ),
        sequence=history.record_count + 1,
        payload=replace(manager, request_id="manager-request-second", request_digest="8" * 64),
    )
    _error("staffing_invalid_event", transition, history, second)


@pytest.mark.parametrize(
    ("effective_from", "use_current_basis"),
    (("2026-10-05", True), ("2026-10-06", False)),
)
def test_manager_response_rejects_any_roster_import_after_reservation(
    effective_from: str, use_current_basis: bool
):
    history, reservation = _issued_history()
    request = roster_import_request()
    request["request_id"] = f"roster-{effective_from}"
    request["expected_roster_revision"] = 1
    request["effective_from_local_date"] = effective_from
    request["source_ref"] = f"weekly-{effective_from}.csv"
    roster = validate_roster_import(
        request,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_timezone="Asia/Shanghai",
    )
    roster_payload = RosterImportedPayload(
        request["request_id"],
        stable_digest(request),
        roster.revision,
        roster.roster_digest,
        roster,
        "course-manager",
        request["source_ref"],
    )
    history = transition(
        history,
        _event("roster_imported", roster_payload, history.record_count + 1),
    )
    basis_snapshot = (
        build_staffing_basis(history, reservation.service_date)
        if use_current_basis
        else reservation.basis_snapshot
    )
    manager = ManagerResponseCommittedPayload(
        "manager-after-roster",
        "8" * 64,
        reservation.generation_id,
        "REJECT",
        "OTHER",
        None,
        None,
        None,
        "manager",
        None,
        basis_snapshot,
    )
    _error(
        "staffing_invalid_evidence",
        transition,
        history,
        _event(
            "manager_response_committed",
            manager,
            history.record_count + 1,
            cause=reservation.generation_id,
        ),
    )


def test_raw_attempt_payload_binds_evidence_request_to_generation():
    history = _roster_history()
    reservation, reserved = _reservation(history)
    history = transition(history, reserved)
    route = AttemptRouteEvidence("KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2")
    started = _event(
        "provider_attempt_started",
        ProviderAttemptStartedPayload(
            reservation.request_digest,
            reservation.generation_id,
            AttemptStartedEvidence(
                reservation.generation_id, 0, route, reservation.input_digest, 10.0
            ),
        ),
        history.record_count + 1,
        cause=reservation.generation_id,
    )
    raw = to_primitive(started)
    raw["payload"]["evidence"]["request_id"] = "another-generation"
    _rebind_raw_event_id(raw, started.occurred_at_utc)
    _error("staffing_invalid_evidence", parse_event, raw, site_id=SITE_ID,
           deployment_id=DEPLOYMENT_ID)
