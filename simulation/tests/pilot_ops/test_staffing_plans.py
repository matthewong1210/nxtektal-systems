"""Roster-scoped effective plan selection and staffing basis construction."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from nxt_pilot_ops.serialization import stable_digest
from nxt_pilot_ops.staffing.contracts import (
    BasisSnapshot,
    EffectivePlanState,
    ExceptionCancelledPayload,
    ExceptionRecordedPayload,
    ManagerResponseCommittedPayload,
    PROMPT_TEMPLATE_VERSION,
    RosterImportedPayload,
    StaffingError,
    StaffingEvent,
    StaffingHistory,
)
from nxt_pilot_ops.staffing.exceptions import exception_digest, normalize_exception
from nxt_pilot_ops.staffing.plans import (
    build_staffing_basis,
    compose_staffing_basis,
    effective_plan_state,
    roster_revisions,
    validate_active_exceptions_for_roster,
)
from nxt_pilot_ops.staffing.roster import materialize_service_day, validate_roster_import

from .staffing_fixtures import roster_import_request


SERVICE_DATE = date(2026, 10, 5)
UTC = timezone.utc


def _roster(payload: dict[str, object] | None = None):
    return validate_roster_import(
        roster_import_request() if payload is None else payload,
        site_id="pilot-course-a", deployment_id="pilot-a-edge-task-sim-v0",
        site_timezone="Asia/Shanghai",
    )


def _event(event_type: str, payload: object, sequence: int) -> StaffingEvent:
    return StaffingEvent(
        event_type, f"event-{sequence}", sequence, "pilot-course-a",
        "pilot-a-edge-task-sim-v0", datetime(2026, 10, 5, 0, sequence, tzinfo=UTC),
        None, payload,
    )


def _roster_event(roster, sequence: int = 1) -> StaffingEvent:
    return _event(
        "roster_imported",
        RosterImportedPayload(
            f"roster-{sequence}", "a" * 64, roster.revision, roster.roster_digest,
            roster, "course-manager", "weekly.csv",
        ), sequence,
    )


def _exception_request(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "nxt-staffing-exception/v1", "request_id": "exception-001",
        "expected_roster_revision": 1, "expected_exception_set_revision": 0,
        "service_date": "2026-10-05", "staff_id": "staff-001", "kind": "LATE",
        "time_local": "10:00", "operator": "course-manager", "note": None,
    }
    value.update(changes)
    return value


def _record(exception, sequence: int, revision: int) -> StaffingEvent:
    return _event(
        "exception_recorded",
        ExceptionRecordedPayload(
            f"record-{sequence}", "b" * 64, exception.exception_id,
            exception.service_date, revision, exception_digest(exception), exception,
            "course-manager",
        ), sequence,
    )


def _accepted(history: StaffingHistory, schedule: tuple, sequence: int, revision: int, *, decision: str = "ACCEPT") -> StaffingEvent:
    snapshot = build_staffing_basis(history, SERVICE_DATE)
    return _event(
        "manager_response_committed",
        ManagerResponseCommittedPayload(
            f"manager-{sequence}", "c" * 64, f"generation-{sequence}", decision,
            "APPROVED" if decision == "ACCEPT" else "APPROVED_WITH_CHANGES",
            schedule, stable_digest(schedule), revision, "course-manager", None, snapshot,
        ), sequence,
    )


def test_empty_history_has_exact_no_plan_state():
    state = effective_plan_state(StaffingHistory(()), SERVICE_DATE, 1)
    assert state == EffectivePlanState(
        0, "NO_PLAN", "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        None, 0, (),
    )


def test_compose_basis_uses_exact_component_recipes_and_is_permutation_independent():
    roster = _roster()
    late = normalize_exception(_exception_request(), roster=roster)
    early = replace(
        late, exception_id="exception-second", kind="EARLY_DEPARTURE",
        unavailable_start=datetime(2026, 10, 5, 7, tzinfo=UTC),
        unavailable_end=datetime(2026, 10, 5, 9, tzinfo=UTC),
    )
    plan = EffectivePlanState(0, "NO_PLAN", stable_digest(()), None, 0, ())
    left = compose_staffing_basis(SERVICE_DATE, roster, (early, late), plan, 2)
    right = compose_staffing_basis(SERVICE_DATE, roster, (late, early), plan, 2)
    assert left == right
    expected_exception = stable_digest({
        "schema": "nxt-staffing-exception-set/v1", "service_date": SERVICE_DATE,
        "exceptions": (late, early),
    })
    expected_plan = stable_digest({
        "schema": "nxt-staffing-effective-plan/v1", "service_date": SERVICE_DATE,
        "revision": 0, "status": "NO_PLAN", "schedule_digest": stable_digest(()),
        "effective_schedule": None, "source_sequence": 0, "affected_exception_ids": (),
    })
    assert (left.exception_set_digest, left.effective_plan_digest) == (
        expected_exception, expected_plan
    )
    assert left.basis_digest == stable_digest({
        "schema": "nxt-staffing-basis/v1", "service_date": SERVICE_DATE,
        "roster_revision": 1, "exception_set_revision": 2,
        "effective_plan_revision": 0, "roster_digest": roster.roster_digest,
        "exception_set_digest": expected_exception, "effective_plan_digest": expected_plan,
    })


def test_build_basis_filters_weekday_and_detaches_complete_snapshot():
    roster = _roster()
    snapshot = build_staffing_basis(StaffingHistory((_roster_event(roster),)), SERVICE_DATE)
    day = materialize_service_day(roster, SERVICE_DATE)
    assert snapshot.site_timezone == "Asia/Shanghai"
    assert snapshot.assignments == day.assignments
    assert snapshot.availability == roster.availability
    assert snapshot.coverage == day.coverage
    assert snapshot.prompt_template_version == PROMPT_TEMPLATE_VERSION
    assert all(type(value) is tuple for value in (
        snapshot.workers, snapshot.assignments, snapshot.exceptions,
        snapshot.availability, snapshot.assignment_rules, snapshot.coverage,
    ))


def test_accepted_schedule_is_next_baseline_and_new_exception_overlays_it():
    roster = _roster()
    base = StaffingHistory((_roster_event(roster),))
    day = materialize_service_day(roster, SERVICE_DATE)
    shortened = (day.assignments[0].with_interval(
        datetime(2026, 10, 5, 2, tzinfo=UTC), datetime(2026, 10, 5, 9, tzinfo=UTC)
    ),)
    manager = _accepted(base, shortened, 2, 1)
    late = normalize_exception(
        _exception_request(request_id="exception-2", time_local="11:00"), roster=roster
    )
    history = StaffingHistory((base.events[0], manager, _record(late, 3, 1)))
    state = effective_plan_state(history, SERVICE_DATE, 1)
    snapshot = build_staffing_basis(history, SERVICE_DATE)
    assert (state.status, state.effective_schedule) == ("CURRENT", shortened)
    assert snapshot.assignments[0].start_at == datetime(2026, 10, 5, 3, tzinfo=UTC)
    assert snapshot.basis.effective_plan_revision == 1


def test_complete_empty_schedule_remains_baseline_instead_of_falling_back():
    roster = _roster()
    base = StaffingHistory((_roster_event(roster),))
    manager = _accepted(base, (), 2, 1)
    snapshot = build_staffing_basis(StaffingHistory((*base.events, manager)), SERVICE_DATE)
    assert snapshot.assignments == ()
    assert effective_plan_state(
        StaffingHistory((*base.events, manager)), SERVICE_DATE, 1
    ).effective_schedule == ()


def test_cancel_of_planned_exception_marks_review_without_restoring_schedule():
    roster = _roster()
    exception = normalize_exception(_exception_request(), roster=roster)
    record = _record(exception, 2, 1)
    before = StaffingHistory((_roster_event(roster), record))
    schedule = build_staffing_basis(before, SERVICE_DATE).assignments
    manager = _accepted(before, schedule, 3, 1)
    cancel = _event(
        "exception_cancelled",
        ExceptionCancelledPayload(
            "cancel-1", "d" * 64, exception.exception_id, 2, exception,
            "course-manager", None,
        ), 4,
    )
    history = StaffingHistory((*before.events, manager, cancel))
    state = effective_plan_state(history, SERVICE_DATE, 1)
    assert state.status == "REVIEW_REQUIRED"
    assert state.effective_schedule == schedule
    assert state.affected_exception_ids == (exception.exception_id,)
    assert build_staffing_basis(history, SERVICE_DATE).assignments == schedule


def test_latest_plan_only_and_roster_replacement_retains_monotonic_revision():
    roster = _roster()
    base = StaffingHistory((_roster_event(roster),))
    first = _accepted(base, materialize_service_day(roster, SERVICE_DATE).assignments, 2, 1)
    payload = roster_import_request()
    payload["request_id"] = "roster-2"
    payload["expected_roster_revision"] = 1
    replacement = _roster(payload)
    replaced = StaffingHistory((*base.events, first, _roster_event(replacement, 3)))
    state = effective_plan_state(replaced, SERVICE_DATE, 2)
    assert state == EffectivePlanState(1, "NO_PLAN", stable_digest(()), None, 0, ())
    assert build_staffing_basis(replaced, SERVICE_DATE).basis.effective_plan_revision == 1
    second = _accepted(replaced, materialize_service_day(replacement, SERVICE_DATE).assignments, 4, 2)
    history = StaffingHistory((*replaced.events, second))
    assert effective_plan_state(history, SERVICE_DATE, 2).revision == 2


def test_future_roster_does_not_change_earlier_service_date_selection():
    first = _roster()
    payload = roster_import_request()
    payload["request_id"] = "roster-future"
    payload["expected_roster_revision"] = 1
    payload["effective_from_local_date"] = "2026-10-12"
    future = _roster(payload)
    history = StaffingHistory((_roster_event(first), _roster_event(future, 2)))
    assert build_staffing_basis(history, SERVICE_DATE).basis.roster_revision == 1
    assert roster_revisions(history) == (first, future)


def test_active_exception_incompatible_with_replacement_roster_fails_closed():
    roster = _roster()
    exception = normalize_exception(_exception_request(), roster=roster)
    payload = roster_import_request()
    payload["request_id"] = "roster-2"
    payload["expected_roster_revision"] = 1
    payload["regular_assignments"] = []
    replacement = _roster(payload)
    history = StaffingHistory((
        _roster_event(roster), _record(exception, 2, 1), _roster_event(replacement, 3)
    ))
    with pytest.raises(StaffingError) as caught:
        build_staffing_basis(history, SERVICE_DATE)
    assert caught.value.code == "unknown_staff_or_shift"


def test_changed_shift_that_no_longer_contains_exception_fails_closed():
    roster = _roster()
    exception = normalize_exception(_exception_request(time_local="10:00"), roster=roster)
    day = materialize_service_day(roster, SERVICE_DATE)
    shortened = replace(day, assignments=(day.assignments[0].with_interval(
        datetime(2026, 10, 5, 1, 30, tzinfo=UTC), day.assignments[0].end_at
    ),))
    with pytest.raises(StaffingError) as caught:
        validate_active_exceptions_for_roster((exception,), shortened)
    assert caught.value.code == "invalid_exception_time"


@pytest.mark.parametrize(
    "state",
    [
        EffectivePlanState(-1, "NO_PLAN", stable_digest(()), None, 0, ()),
        EffectivePlanState(0, "CURRENT", stable_digest(()), (), 1, ()),
        EffectivePlanState(1, "CURRENT", "0" * 64, (), 1, ()),
        EffectivePlanState(1, "REVIEW_REQUIRED", stable_digest(()), (), 1, ()),
    ],
)
def test_compose_basis_rejects_semantically_invalid_plan_states(state):
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        compose_staffing_basis(SERVICE_DATE, _roster(), (), state, 0)


def test_invalid_accepted_schedule_digest_or_nonmonotonic_revision_fails_closed():
    roster = _roster()
    base = StaffingHistory((_roster_event(roster),))
    schedule = materialize_service_day(roster, SERVICE_DATE).assignments
    first = _accepted(base, schedule, 2, 1)
    corrupt = replace(first, payload=replace(first.payload, schedule_digest="0" * 64))
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        effective_plan_state(StaffingHistory((*base.events, corrupt)), SERVICE_DATE, 1)
    second = _accepted(StaffingHistory((*base.events, first)), schedule, 3, 1)
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        effective_plan_state(StaffingHistory((*base.events, first, second)), SERVICE_DATE, 1)


def test_accepted_plan_revision_must_increment_by_exactly_one_per_date():
    roster = _roster()
    base = StaffingHistory((_roster_event(roster),))
    schedule = materialize_service_day(roster, SERVICE_DATE).assignments
    first = _accepted(base, schedule, 2, 1)
    prior = StaffingHistory((*base.events, first))
    skipped = _accepted(prior, schedule, 3, 3)
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        effective_plan_state(StaffingHistory((*prior.events, skipped)), SERVICE_DATE, 1)


def test_manager_plan_projection_rejects_descending_history_sequences():
    roster = _roster()
    base = StaffingHistory((_roster_event(roster, sequence=2),))
    schedule = materialize_service_day(roster, SERVICE_DATE).assignments
    manager = _accepted(base, schedule, 1, 1)
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        effective_plan_state(StaffingHistory((*base.events, manager)), SERVICE_DATE, 1)


def test_accepted_snapshot_revalidates_exception_and_basis_digest_formulas():
    roster = _roster()
    base = StaffingHistory((_roster_event(roster),))
    schedule = materialize_service_day(roster, SERVICE_DATE).assignments
    manager = _accepted(base, schedule, 2, 1)
    snapshot = manager.payload.basis_snapshot
    corrupt_snapshot = replace(
        snapshot, basis=replace(snapshot.basis, basis_digest="0" * 64)
    )
    corrupt = replace(manager, payload=replace(manager.payload, basis_snapshot=corrupt_snapshot))
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        effective_plan_state(StaffingHistory((*base.events, corrupt)), SERVICE_DATE, 1)


def test_accepted_snapshot_effective_plan_digest_is_bound_to_prior_history():
    roster = _roster()
    base = StaffingHistory((_roster_event(roster),))
    schedule = materialize_service_day(roster, SERVICE_DATE).assignments
    manager = _accepted(base, schedule, 2, 1)
    snapshot = manager.payload.basis_snapshot
    changed_effective_digest = "f" * 64
    changed_basis_digest = stable_digest({
        "schema": "nxt-staffing-basis/v1",
        "service_date": snapshot.basis.service_date,
        "roster_revision": snapshot.basis.roster_revision,
        "exception_set_revision": snapshot.basis.exception_set_revision,
        "effective_plan_revision": snapshot.basis.effective_plan_revision,
        "roster_digest": snapshot.basis.roster_digest,
        "exception_set_digest": snapshot.basis.exception_set_digest,
        "effective_plan_digest": changed_effective_digest,
    })
    corrupt_basis = replace(
        snapshot.basis,
        effective_plan_digest=changed_effective_digest,
        basis_digest=changed_basis_digest,
    )
    corrupt = replace(
        manager,
        payload=replace(
            manager.payload,
            basis_snapshot=replace(snapshot, basis=corrupt_basis),
        ),
    )
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        effective_plan_state(StaffingHistory((*base.events, corrupt)), SERVICE_DATE, 1)


def test_accepted_schedule_must_project_into_snapshot_service_date_and_timezone():
    roster = _roster()
    base = StaffingHistory((_roster_event(roster),))
    original = materialize_service_day(roster, SERVICE_DATE).assignments[0]
    next_day = (
        original.with_interval(
            original.start_at + timedelta(days=1),
            original.end_at + timedelta(days=1),
        ),
    )
    manager = _accepted(base, next_day, 2, 1)
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        effective_plan_state(StaffingHistory((*base.events, manager)), SERVICE_DATE, 1)


def test_roster_projection_rejects_boolean_outer_revision_even_when_equal_to_one():
    roster = _roster()
    event = _roster_event(roster)
    corrupt = replace(event, payload=replace(event.payload, roster_revision=True))
    with pytest.raises(StaffingError, match="staffing_invalid_evidence"):
        roster_revisions(StaffingHistory((corrupt,)))


@pytest.mark.parametrize(
    "operation",
    [
        lambda: roster_revisions(StaffingHistory(True)),
        lambda: effective_plan_state(StaffingHistory(True), SERVICE_DATE, 1),
        lambda: build_staffing_basis(StaffingHistory(True), SERVICE_DATE),
        lambda: compose_staffing_basis(
            SERVICE_DATE,
            _roster(),
            True,
            EffectivePlanState(0, "NO_PLAN", stable_digest(()), None, 0, ()),
            0,
        ),
        lambda: validate_active_exceptions_for_roster(
            True, materialize_service_day(_roster(), SERVICE_DATE)
        ),
    ],
)
def test_public_history_and_sequence_inputs_never_leak_raw_type_errors(operation):
    with pytest.raises(StaffingError):
        operation()


def test_domain_modules_keep_the_frozen_import_boundary():
    root = Path(__file__).parents[2] / "nxt_pilot_ops" / "staffing"
    allowed = {
        "exceptions.py": {"contracts", "time", "roster", "serialization"},
        "plans.py": {"contracts", "exceptions", "roster", "serialization"},
    }
    forbidden = {"nxt_model_gateway", "nxt_site_agent", "nxt_site_runtime", "nxt_sim", "requests", "httpx", "urllib"}
    for filename, local_allowed in allowed.items():
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, (ast.ImportFrom,))
        } | {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        }
        assert not any(any(part in item for part in forbidden) for item in imported)
        relative = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level and node.module
        }
        assert relative <= local_allowed
