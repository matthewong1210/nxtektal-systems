"""Roster-scoped effective plan selection and deterministic basis snapshots."""

from __future__ import annotations

import re
from datetime import date
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..serialization import stable_digest
from .contracts import (
    Assignment,
    AssignmentRule,
    AvailabilityWindow,
    BasisSnapshot,
    CoverageWindow,
    EffectivePlanState,
    ExceptionCancelledPayload,
    ExceptionCorrectedPayload,
    ManagerResponseCommittedPayload,
    PROMPT_TEMPLATE_VERSION,
    SUPPORTED_PROMPT_TEMPLATE_VERSIONS,
    RosterImportedPayload,
    RosterRevision,
    ServiceDayRoster,
    StaffingBasis,
    StaffingError,
    StaffingEvent,
    StaffingException,
    StaffingHistory,
    Worker,
)
from .exceptions import (
    active_exceptions,
    apply_exceptions,
    exception_digest,
    exception_set_revision,
    validate_nonoverlapping_exceptions,
)
from .roster import materialize_service_day, select_effective_roster


_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _invalid(detail: str) -> StaffingError:
    return StaffingError("staffing_invalid_evidence", detail)


def _history_events(history: object) -> tuple[StaffingEvent, ...]:
    if type(history) is not StaffingHistory or type(history.events) is not tuple:
        raise _invalid("history events")
    if any(type(event) is not StaffingEvent for event in history.events):
        raise _invalid("history event")
    previous_sequence = 0
    for event in history.events:
        if type(event.sequence) is not int or event.sequence <= previous_sequence:
            raise _invalid("history sequence order")
        previous_sequence = event.sequence
    return history.events


def _is_digest(value: object) -> bool:
    return type(value) is str and _HEX_DIGEST.fullmatch(value) is not None


def _validate_assignment_tuple(value: object, field: str) -> tuple[Assignment, ...]:
    if type(value) is not tuple or any(type(item) is not Assignment for item in value):
        raise _invalid(field)
    ordered = tuple(
        sorted(
            value,
            key=lambda item: (
                item.start_at,
                item.end_at,
                item.staff_id,
                item.assignment_id,
            ),
        )
    )
    if value != ordered:
        raise _invalid(f"{field} canonical order")
    by_staff: dict[str, Assignment] = {}
    for item in ordered:
        previous = by_staff.get(item.staff_id)
        if previous is not None and item.start_at < previous.end_at:
            raise _invalid(f"{field} overlap")
        by_staff[item.staff_id] = item
    return value


def _validate_plan_state(plan: object) -> EffectivePlanState:
    if type(plan) is not EffectivePlanState:
        raise _invalid("effective plan")
    if type(plan.revision) is not int or plan.revision < 0:
        raise _invalid("effective plan revision")
    if type(plan.status) is not str or plan.status not in {
        "NO_PLAN",
        "CURRENT",
        "REVIEW_REQUIRED",
    }:
        raise _invalid("effective plan status")
    if not _is_digest(plan.schedule_digest):
        raise _invalid("schedule_digest")
    if type(plan.source_sequence) is not int or plan.source_sequence < 0:
        raise _invalid("source_sequence")
    if type(plan.affected_exception_ids) is not tuple or any(
        type(item) is not str or not item for item in plan.affected_exception_ids
    ):
        raise _invalid("affected_exception_ids")
    if tuple(sorted(set(plan.affected_exception_ids))) != plan.affected_exception_ids:
        raise _invalid("affected_exception_ids")
    if plan.status == "NO_PLAN":
        if (
            plan.effective_schedule is not None
            or plan.source_sequence != 0
            or plan.affected_exception_ids
            or plan.schedule_digest != stable_digest(())
        ):
            raise _invalid("NO_PLAN coherence")
        return plan
    if plan.revision < 1 or plan.source_sequence < 1:
        raise _invalid("effective plan source")
    schedule = _validate_assignment_tuple(plan.effective_schedule, "effective_schedule")
    if stable_digest(schedule) != plan.schedule_digest:
        raise _invalid("schedule_digest")
    if plan.status == "CURRENT" and plan.affected_exception_ids:
        raise _invalid("CURRENT affected exceptions")
    if plan.status == "REVIEW_REQUIRED" and not plan.affected_exception_ids:
        raise _invalid("REVIEW_REQUIRED affected exceptions")
    return plan


def _validate_basis_shape(basis: object) -> StaffingBasis:
    if type(basis) is not StaffingBasis or type(basis.service_date) is not date:
        raise _invalid("basis")
    if any(
        type(value) is not int or value < minimum
        for value, minimum in (
            (basis.roster_revision, 1),
            (basis.exception_set_revision, 0),
            (basis.effective_plan_revision, 0),
        )
    ):
        raise _invalid("basis revisions")
    if not all(
        _is_digest(value)
        for value in (
            basis.roster_digest,
            basis.exception_set_digest,
            basis.effective_plan_digest,
            basis.basis_digest,
        )
    ):
        raise _invalid("basis digests")
    return basis


def _validate_snapshot_shape(snapshot: object) -> BasisSnapshot:
    if type(snapshot) is not BasisSnapshot:
        raise _invalid("basis_snapshot")
    _validate_basis_shape(snapshot.basis)
    if type(snapshot.site_timezone) is not str or not snapshot.site_timezone:
        raise _invalid("site_timezone")
    expected_types = (
        (snapshot.workers, Worker, "workers"),
        (snapshot.assignments, Assignment, "assignments"),
        (snapshot.exceptions, StaffingException, "exceptions"),
        (snapshot.availability, AvailabilityWindow, "availability"),
        (snapshot.assignment_rules, AssignmentRule, "assignment_rules"),
        (snapshot.coverage, CoverageWindow, "coverage"),
    )
    for values, expected, field in expected_types:
        if type(values) is not tuple or any(type(item) is not expected for item in values):
            raise _invalid(field)
    _validate_assignment_tuple(snapshot.assignments, "snapshot assignments")
    ordered_exceptions = validate_nonoverlapping_exceptions(
        snapshot.exceptions, replay=True
    )
    if ordered_exceptions != snapshot.exceptions:
        raise _invalid("snapshot exceptions canonical order")
    for item in snapshot.exceptions:
        exception_digest(item)
    expected_exception_digest = stable_digest(
        {
            "schema": "nxt-staffing-exception-set/v1",
            "service_date": snapshot.basis.service_date,
            "exceptions": snapshot.exceptions,
        }
    )
    if snapshot.basis.exception_set_digest != expected_exception_digest:
        raise _invalid("snapshot exception_set_digest")
    expected_basis_digest = stable_digest(
        {
            "schema": "nxt-staffing-basis/v1",
            "service_date": snapshot.basis.service_date,
            "roster_revision": snapshot.basis.roster_revision,
            "exception_set_revision": snapshot.basis.exception_set_revision,
            "effective_plan_revision": snapshot.basis.effective_plan_revision,
            "roster_digest": snapshot.basis.roster_digest,
            "exception_set_digest": snapshot.basis.exception_set_digest,
            "effective_plan_digest": snapshot.basis.effective_plan_digest,
        }
    )
    if snapshot.basis.basis_digest != expected_basis_digest:
        raise _invalid("snapshot basis_digest")
    if snapshot.prompt_template_version not in SUPPORTED_PROMPT_TEMPLATE_VERSIONS:
        raise _invalid("prompt_template_version")
    return snapshot


def roster_revisions(history: StaffingHistory) -> tuple[RosterRevision, ...]:
    """Extract only coherent committed roster revisions from verified history."""

    events = _history_events(history)
    revisions: list[RosterRevision] = []
    last_revision = 0
    for event in events:
        if event.event_type != "roster_imported":
            continue
        payload = event.payload
        if type(payload) is not RosterImportedPayload or type(payload.roster) is not RosterRevision:
            raise _invalid("roster payload")
        roster = payload.roster
        if (
            type(payload.roster_revision) is not int
            or type(payload.roster_digest) is not str
            or payload.roster_revision != roster.revision
            or payload.roster_digest != roster.roster_digest
            or event.site_id != roster.site_id
            or event.deployment_id != roster.deployment_id
            or roster.revision <= last_revision
        ):
            raise _invalid("roster payload coherence")
        revisions.append(roster)
        last_revision = roster.revision
    return tuple(revisions)


def _validate_manager_record(
    event: StaffingEvent,
) -> tuple[ManagerResponseCommittedPayload, date] | None:
    if type(event.sequence) is not int or event.sequence < 1:
        raise _invalid("manager source_sequence")
    payload = event.payload
    if type(payload) is not ManagerResponseCommittedPayload:
        raise _invalid("manager response payload")
    snapshot = _validate_snapshot_shape(payload.basis_snapshot)
    service_date = snapshot.basis.service_date
    if type(payload.decision) is not str or payload.decision not in {
        "ACCEPT",
        "MODIFY",
        "REJECT",
    }:
        raise _invalid("manager decision")
    if payload.decision == "REJECT":
        if any(
            value is not None
            for value in (
                payload.effective_schedule,
                payload.schedule_digest,
                payload.effective_plan_revision,
            )
        ):
            raise _invalid("rejected response schedule")
        return None
    schedule = _validate_assignment_tuple(payload.effective_schedule, "effective_schedule")
    if (
        type(payload.effective_plan_revision) is not int
        or payload.effective_plan_revision < 1
        or not _is_digest(payload.schedule_digest)
        or payload.schedule_digest != stable_digest(schedule)
    ):
        raise _invalid("accepted response schedule")
    try:
        zone = ZoneInfo(snapshot.site_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise _invalid("accepted schedule timezone") from None
    for assignment in schedule:
        if (
            assignment.start_at.astimezone(zone).date() != service_date
            or assignment.end_at.astimezone(zone).date() != service_date
        ):
            raise _invalid("accepted schedule service_date")
    return payload, service_date


def _plan_from_accepted_records(
    accepted: Sequence[tuple[StaffingEvent, ManagerResponseCommittedPayload]],
    events: tuple[StaffingEvent, ...],
    service_date: date,
    roster_revision: int,
) -> EffectivePlanState:
    records = tuple(accepted)
    if not records:
        return _validate_plan_state(
            EffectivePlanState(0, "NO_PLAN", stable_digest(()), None, 0, ())
        )
    source_event, latest = records[-1]
    latest_revision = latest.effective_plan_revision
    if latest.basis_snapshot.basis.roster_revision != roster_revision:
        return _validate_plan_state(
            EffectivePlanState(
                latest_revision, "NO_PLAN", stable_digest(()), None, 0, ()
            )
        )
    planned_ids = {item.exception_id for item in latest.basis_snapshot.exceptions}
    affected: set[str] = set()
    for event in events:
        if event.sequence <= source_event.sequence:
            continue
        payload = event.payload
        if event.event_type == "exception_cancelled":
            if type(payload) is not ExceptionCancelledPayload:
                raise _invalid("cancellation payload")
            target = payload.exception_id
        elif event.event_type == "exception_corrected":
            if type(payload) is not ExceptionCorrectedPayload:
                raise _invalid("correction payload")
            target = payload.exception_id
        else:
            continue
        if target in planned_ids:
            affected.add(target)
    affected_ids = tuple(sorted(affected))
    return _validate_plan_state(
        EffectivePlanState(
            latest_revision,
            "REVIEW_REQUIRED" if affected_ids else "CURRENT",
            latest.schedule_digest,
            latest.effective_schedule,
            source_event.sequence,
            affected_ids,
        )
    )


def _snapshot_from_state(
    *,
    roster: RosterRevision,
    service_date: date,
    events: tuple[StaffingEvent, ...],
    plan: EffectivePlanState,
    prompt_template_version: str = PROMPT_TEMPLATE_VERSION,
) -> BasisSnapshot:
    day = materialize_service_day(roster, service_date)
    active = active_exceptions(events, service_date)
    active = validate_active_exceptions_for_roster(active, day)
    baseline = (
        plan.effective_schedule
        if plan.effective_schedule is not None
        else day.assignments
    )
    assignments = apply_exceptions(baseline, active)
    basis = compose_staffing_basis(
        service_date,
        roster,
        active,
        plan,
        exception_set_revision(events, service_date),
    )
    availability = tuple(
        row for row in roster.availability if row.weekday == service_date.weekday()
    )
    return _validate_snapshot_shape(
        BasisSnapshot(
            basis,
            roster.site_timezone,
            tuple(roster.workers),
            tuple(assignments),
            tuple(active),
            availability,
            tuple(roster.assignment_rules),
            tuple(day.coverage),
            prompt_template_version,
        )
    )


def _expected_snapshot_before_event(
    events: tuple[StaffingEvent, ...],
    service_date: date,
    accepted: Sequence[tuple[StaffingEvent, ManagerResponseCommittedPayload]],
    *,
    prompt_template_version: str = PROMPT_TEMPLATE_VERSION,
) -> BasisSnapshot:
    prefix_history = StaffingHistory(events)
    try:
        roster = select_effective_roster(
            roster_revisions(prefix_history), service_date
        )
        # Validate the global exception projection before selecting review state.
        active_exceptions(events, service_date)
        plan = _plan_from_accepted_records(
            accepted, events, service_date, roster.revision
        )
        return _snapshot_from_state(
            roster=roster,
            service_date=service_date,
            events=events,
            plan=plan,
            prompt_template_version=prompt_template_version,
        )
    except StaffingError as exc:
        if exc.code == "staffing_invalid_evidence":
            raise
        raise _invalid("manager basis history") from None


def _validated_accepted_records(
    history: StaffingHistory,
) -> tuple[tuple[StaffingEvent, ManagerResponseCommittedPayload], ...]:
    """Validate manager snapshots against prior history without recursion."""

    events = _history_events(history)
    accepted_by_date: dict[
        date, list[tuple[StaffingEvent, ManagerResponseCommittedPayload]]
    ] = {}
    accepted_all: list[tuple[StaffingEvent, ManagerResponseCommittedPayload]] = []
    previous_source_sequence = 0
    for index, event in enumerate(events):
        if event.event_type != "manager_response_committed":
            continue
        validated = _validate_manager_record(event)
        snapshot = event.payload.basis_snapshot
        record_date = snapshot.basis.service_date
        prior_for_date = accepted_by_date.get(record_date, [])
        expected_snapshot = _expected_snapshot_before_event(
            events[:index],
            record_date,
            prior_for_date,
            prompt_template_version=snapshot.prompt_template_version,
        )
        if snapshot != expected_snapshot:
            raise _invalid("manager basis snapshot does not match prior history")
        if validated is None:
            continue
        payload, _ = validated
        previous_revision = (
            prior_for_date[-1][1].effective_plan_revision
            if prior_for_date
            else 0
        )
        if payload.effective_plan_revision != previous_revision + 1:
            raise _invalid("nonconsecutive effective_plan_revision")
        if event.sequence <= previous_source_sequence:
            raise _invalid("manager source_sequence")
        accepted_by_date.setdefault(record_date, []).append((event, payload))
        accepted_all.append((event, payload))
        previous_source_sequence = event.sequence
    return tuple(accepted_all)


def effective_plan_state(
    history: StaffingHistory, service_date: date, roster_revision: int
) -> EffectivePlanState:
    """Select the last accepted plan, scoped strictly to the selected roster."""

    if type(service_date) is not date:
        raise _invalid("effective plan input")
    if type(roster_revision) is not int or roster_revision < 1:
        raise _invalid("roster_revision")
    events = _history_events(history)
    active_exceptions(events, service_date)
    accepted = tuple(
        record
        for record in _validated_accepted_records(history)
        if record[1].basis_snapshot.basis.service_date == service_date
    )
    return _plan_from_accepted_records(
        accepted, events, service_date, roster_revision
    )


def compose_staffing_basis(
    service_date: date,
    roster: RosterRevision,
    active: Sequence[StaffingException],
    plan: EffectivePlanState,
    exception_set_revision: int,
) -> StaffingBasis:
    """Compose all three revision axes using their frozen digest recipes."""

    if type(service_date) is not date or type(roster) is not RosterRevision:
        raise _invalid("basis input")
    if not roster.applies_to(service_date):
        raise _invalid("roster service_date")
    if type(exception_set_revision) is not int or exception_set_revision < 0:
        raise _invalid("exception_set_revision")
    ordered_active = validate_nonoverlapping_exceptions(active, replay=True)
    if any(item.service_date != service_date for item in ordered_active):
        raise _invalid("active exception service_date")
    plan = _validate_plan_state(plan)
    exception_set_digest = stable_digest(
        {
            "schema": "nxt-staffing-exception-set/v1",
            "service_date": service_date,
            "exceptions": ordered_active,
        }
    )
    effective_plan_digest = stable_digest(
        {
            "schema": "nxt-staffing-effective-plan/v1",
            "service_date": service_date,
            "revision": plan.revision,
            "status": plan.status,
            "schedule_digest": plan.schedule_digest,
            "effective_schedule": plan.effective_schedule,
            "source_sequence": plan.source_sequence,
            "affected_exception_ids": plan.affected_exception_ids,
        }
    )
    core = {
        "schema": "nxt-staffing-basis/v1",
        "service_date": service_date,
        "roster_revision": roster.revision,
        "exception_set_revision": exception_set_revision,
        "effective_plan_revision": plan.revision,
        "roster_digest": roster.roster_digest,
        "exception_set_digest": exception_set_digest,
        "effective_plan_digest": effective_plan_digest,
    }
    result = StaffingBasis(
        service_date,
        roster.revision,
        exception_set_revision,
        plan.revision,
        roster.roster_digest,
        exception_set_digest,
        effective_plan_digest,
        stable_digest(core),
    )
    return _validate_basis_shape(result)


def validate_active_exceptions_for_roster(
    active: Sequence[StaffingException], day: ServiceDayRoster
) -> tuple[StaffingException, ...]:
    """Fail closed when a roster replacement invalidates an active exception."""

    if type(day) is not ServiceDayRoster:
        raise _invalid("service day roster")
    ordered = validate_nonoverlapping_exceptions(active, replay=True)
    for exception in ordered:
        if exception.service_date != day.service_date:
            raise _invalid("active exception service_date")
        shift = day.assignment_for_staff(exception.staff_id)
        if shift is None:
            raise StaffingError("unknown_staff_or_shift", exception.staff_id)
        if not (
            shift.start_at <= exception.unavailable_start
            and exception.unavailable_end <= shift.end_at
        ):
            raise StaffingError("invalid_exception_time", "outside selected roster shift")
    return ordered


def build_staffing_basis(
    history: StaffingHistory,
    service_date: date,
    *,
    prompt_template_version: str = PROMPT_TEMPLATE_VERSION,
) -> BasisSnapshot:
    """Build the immutable model-advisory baseline for one service date.

    ``prompt_template_version`` is the current template for a new reservation
    and the stored template when replay rebuilds a historical snapshot; any
    other value fails closed.
    """

    if type(service_date) is not date:
        raise _invalid("basis input")
    if (
        type(prompt_template_version) is not str
        or prompt_template_version not in SUPPORTED_PROMPT_TEMPLATE_VERSIONS
    ):
        raise _invalid("prompt_template_version")
    events = _history_events(history)
    roster = select_effective_roster(roster_revisions(history), service_date)
    plan = effective_plan_state(history, service_date, roster.revision)
    return _snapshot_from_state(
        roster=roster,
        service_date=service_date,
        events=events,
        plan=plan,
        prompt_template_version=prompt_template_version,
    )
