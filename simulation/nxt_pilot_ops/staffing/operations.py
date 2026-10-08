"""Ledger-backed staffing advisory operations and read projections.

This module is the only domain layer that joins strict request parsing, pure
workflow replay, and the durable staffing ledger.  It remains advisory-only:
manager responses persist an effective staffing plan but never issue commands.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Callable, NoReturn, Sequence

from ..serialization import stable_digest, to_primitive
from .contracts import (
    AddOperation,
    AppendEventDecision,
    Assignment,
    AssignmentProjection,
    AttemptFinishedEvidence,
    AttemptStartedEvidence,
    CandidateOperationProjection,
    CandidateProjection,
    ConflictDecision,
    ConflictReceipt,
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
    MANAGER_REASON_CODES,
    ManagerCommittedProjection,
    ManagerAddOperation,
    ManagerResponseCommittedPayload,
    ManagerResponseProjection,
    ManagerRemoveOperation,
    ManagerResponseRequest,
    ProviderAttemptFinishedPayload,
    ProviderAttemptStartedPayload,
    ReceiptResult,
    RemoveOperation,
    RequestProjection,
    ResultEvidence,
    ReturnReceiptDecision,
    RevisionVector,
    RosterCommittedProjection,
    RosterImportedPayload,
    StaffingError,
    StaffingEvent,
    StaffingException,
    StaffingHistory,
    StoredCandidate,
    SuggestionIssuedPayload,
    SuggestionUnavailablePayload,
    VerifiedLedgerState,
    Worker,
    parse_local_date,
    require_exact_object,
    validate_code,
    validate_human_text,
    validate_identifier,
)
from .exceptions import (
    active_exceptions,
    require_no_overlapping_exception,
    exception_digest,
    exception_set_revision,
    normalize_exception,
    normalize_exception_replacement,
    parse_exception_cancel_request,
    parse_exception_correction_request,
)
from .ledger import EventCommit, StaffingLedger
from .plans import build_staffing_basis, effective_plan_state, roster_revisions
from .review import (
    action_window_end,
    is_expired,
    local_alias_labels,
    render_local_text,
)
from .projection import (
    GenerationProjection,
    decode_provider_candidates,
    project_generation_request,
    validate_generation_projection,
)
from .roster import (
    materialize_service_day,
    select_effective_roster,
    validate_roster_import,
)
from .validator import (
    CandidatePatch,
    canonical_schedule,
    schedule_digest,
    validate_alias_maps,
    validate_candidate,
    validate_candidates,
)
from .workflow import (
    candidate_patch_for_revalidation,
    staffing_event_id,
    staffing_generation_id,
    stored_candidate_from_validation,
    validate_replayable_result_evidence,
)


MAX_REQUEST_DEPTH = 32
MAX_REQUEST_OCCURRENCES = 524_289
MIN_REQUEST_INTEGER = -(2**63)
MAX_REQUEST_INTEGER = (2**63) - 1


class _InvalidRequestTree(Exception):
    """Private sentinel replaced at the public request boundary."""


def _detach_request_body(value: object, *, code: str) -> dict[str, object]:
    """Copy one exact JSON object without invoking user-controlled hooks."""

    occurrences = 0
    ancestors: set[int] = set()

    def fail() -> NoReturn:
        raise _InvalidRequestTree

    def visit(item: object, depth: int) -> object:
        nonlocal occurrences
        occurrences += 1
        if occurrences > MAX_REQUEST_OCCURRENCES:
            fail()
        if type(item) in (dict, list):
            if depth > MAX_REQUEST_DEPTH:
                fail()
            identity = id(item)
            if identity in ancestors:
                fail()
            ancestors.add(identity)
            try:
                if type(item) is dict:
                    output: dict[str, object] = {}
                    for key, child in item.items():
                        if type(key) is not str:
                            fail()
                        try:
                            key.encode("utf-8")
                        except UnicodeError:
                            fail()
                        output[key] = visit(child, depth + 1)
                    return output
                return [visit(child, depth + 1) for child in item]
            finally:
                ancestors.remove(identity)
        if item is None or type(item) is bool:
            return item
        if type(item) is int:
            if not MIN_REQUEST_INTEGER <= item <= MAX_REQUEST_INTEGER:
                fail()
            return item
        if type(item) is float:
            if not math.isfinite(item):
                fail()
            return item
        if type(item) is str:
            try:
                item.encode("utf-8")
            except UnicodeError:
                fail()
            return item
        fail()

    try:
        detached = visit(value, 0)
        if type(detached) is not dict:
            fail()
        return detached
    except Exception:
        raise StaffingError(code, "request body") from None


def closed_request_id(value: object, *, code: str) -> str:
    if type(value) is not dict:
        raise StaffingError(code, "request body")
    try:
        return validate_identifier(value.get("request_id"), "request_id")
    except StaffingError:
        raise StaffingError(code, "request_id") from None


GENERATION_REQUEST_KEYS = frozenset(
    {
        "schema",
        "request_id",
        "operator",
        "service_date",
        "expected_revisions",
        "retry_of",
    }
)


def parse_generation_request(value: object) -> dict[str, object]:
    detached = _detach_request_body(value, code="staffing_invalid_request")
    body = require_exact_object(
        detached,
        GENERATION_REQUEST_KEYS,
        code="staffing_invalid_request",
        detail="generation request fields",
    )
    if body["schema"] != "nxt-staffing-suggestion-generate/v1":
        raise StaffingError("staffing_invalid_request", "schema")
    try:
        request_id = validate_identifier(body["request_id"], "request_id")
        operator = validate_human_text(
            body["operator"], "operator", maximum=128, formula_safe=True
        )
        service_date = parse_local_date(body["service_date"], "service_date")
        retry_of = (
            None
            if body["retry_of"] is None
            else validate_identifier(body["retry_of"], "retry_of")
        )
    except StaffingError:
        raise StaffingError("staffing_invalid_request", "generation request") from None
    revisions = require_exact_object(
        body["expected_revisions"],
        {"roster", "exception_set", "effective_plan"},
        code="staffing_invalid_request",
        detail="expected_revisions",
    )
    if any(type(item) is not int or item < 0 for item in revisions.values()):
        raise StaffingError("staffing_invalid_request", "expected_revisions")
    return {
        "schema": body["schema"],
        "request_id": request_id,
        "operator": operator,
        "service_date": service_date.isoformat(),
        "expected_revisions": {
            "roster": revisions["roster"],
            "exception_set": revisions["exception_set"],
            "effective_plan": revisions["effective_plan"],
        },
        "retry_of": retry_of,
    }


def _request_digest(
    kind: str,
    detached_body: dict[str, object],
    *,
    suggestion_id: str | None = None,
) -> str:
    if type(detached_body) is not dict:
        raise StaffingError("staffing_invalid_evidence", "request body")
    if kind == "manager-response":
        if suggestion_id is None:
            raise StaffingError("staffing_invalid_evidence", "suggestion_id")
        return stable_digest({"suggestion_id": suggestion_id, "body": detached_body})
    if suggestion_id is not None:
        raise StaffingError("staffing_invalid_evidence", "request digest context")
    return stable_digest(detached_body)


def manager_identifier(value: object, field: str) -> str:
    try:
        return validate_identifier(value, field)
    except StaffingError:
        raise StaffingError("staffing_invalid_request", field) from None


def manager_code(value: object, field: str) -> str:
    try:
        return validate_code(value, field)
    except StaffingError:
        raise StaffingError("staffing_invalid_request", field) from None


def manager_human_text(
    value: object,
    field: str,
    *,
    minimum: int = 1,
    maximum: int,
    formula_safe: bool = False,
) -> str:
    try:
        return validate_human_text(
            value,
            field,
            minimum=minimum,
            maximum=maximum,
            formula_safe=formula_safe,
        )
    except StaffingError:
        raise StaffingError("staffing_invalid_request", field) from None


def parse_manager_datetime(value: object) -> datetime:
    if (
        type(value) is not str
        or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:Z|[+-](?!00:00)\d{2}:\d{2})",
            value,
        )
        is None
    ):
        raise StaffingError(
            "staffing_invalid_request", "manager timestamp must be RFC3339"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise StaffingError(
            "staffing_invalid_request", "manager timestamp must be RFC3339"
        ) from None
    if parsed.tzinfo is None or parsed.second or parsed.microsecond:
        raise StaffingError(
            "staffing_invalid_request", "manager timestamp must be aware minute precision"
        )
    return parsed


def parse_manager_response(
    value: object, *, suggestion_id: str
) -> tuple[ManagerResponseRequest, dict[str, object]]:
    detached = _detach_request_body(value, code="staffing_invalid_request")
    body = require_exact_object(
        detached,
        {
            "schema",
            "request_id",
            "operator",
            "kind",
            "expected_revisions",
            "candidate_index",
            "edited_operations",
            "reason_code",
            "note",
        },
        code="staffing_invalid_request",
        detail="manager response fields",
    )
    if body["schema"] != "nxt-staffing-manager-response/v1":
        raise StaffingError("staffing_invalid_request", "schema")
    request_id = manager_identifier(body["request_id"], "request_id")
    generation_id = manager_identifier(suggestion_id, "suggestion_id")
    operator = manager_human_text(
        body["operator"], "operator", maximum=128, formula_safe=True
    )
    note = body["note"]
    if note is not None:
        note = manager_human_text(note, "note", minimum=0, maximum=500)
    kind = body["kind"]
    reason = body["reason_code"]
    if type(kind) is not str or kind not in {"ACCEPT", "MODIFY", "REJECT"}:
        raise StaffingError("staffing_invalid_request", "unknown kind")
    if type(reason) is not str or reason not in MANAGER_REASON_CODES:
        raise StaffingError("staffing_invalid_request", "unknown reason_code")
    revisions = require_exact_object(
        body["expected_revisions"],
        {"roster", "exception_set", "effective_plan"},
        code="staffing_invalid_request",
        detail="expected_revisions",
    )
    if any(type(item) is not int or item < 0 for item in revisions.values()):
        raise StaffingError("staffing_invalid_request", "expected_revisions")
    expected = RevisionVector(
        revisions["roster"], revisions["exception_set"], revisions["effective_plan"]
    )
    raw_operations = body["edited_operations"]
    if kind in {"ACCEPT", "REJECT"}:
        if raw_operations is not None:
            raise StaffingError(
                "staffing_invalid_request", "edited_operations must be null"
            )
        raw_operations = []
    elif type(raw_operations) is not list or not 1 <= len(raw_operations) <= 32:
        raise StaffingError(
            "staffing_invalid_request", "MODIFY requires 1..32 edited operations"
        )

    operations: list[ManagerRemoveOperation | ManagerAddOperation] = []
    wire_operations: list[dict[str, object]] = []
    for raw in raw_operations:
        if type(raw) is not dict:
            raise StaffingError("staffing_invalid_request", "edited operation object")
        operation = dict(raw)
        if operation.get("operation") == "REMOVE" and set(operation) == {
            "operation",
            "assignment_id",
        }:
            assignment_id = manager_identifier(
                operation["assignment_id"], "assignment_id"
            )
            operations.append(ManagerRemoveOperation("REMOVE", assignment_id))
        elif operation.get("operation") == "ADD" and set(operation) == {
            "operation",
            "staff_id",
            "role_code",
            "area_code",
            "start_at",
            "end_at",
        }:
            operations.append(
                ManagerAddOperation(
                    "ADD",
                    manager_identifier(operation["staff_id"], "staff_id"),
                    manager_code(operation["role_code"], "role_code"),
                    manager_code(operation["area_code"], "area_code"),
                    parse_manager_datetime(operation["start_at"]),
                    parse_manager_datetime(operation["end_at"]),
                )
            )
        else:
            raise StaffingError("staffing_invalid_request", "edited_operations")
        wire_operations.append(operation)
    remove_ids = tuple(
        item.assignment_id for item in operations if item.operation == "REMOVE"
    )
    if len(remove_ids) != len(set(remove_ids)):
        raise StaffingError("staffing_invalid_request", "duplicate REMOVE")

    candidate_index = body["candidate_index"]
    if kind == "ACCEPT" and (
        type(candidate_index) is not int
        or candidate_index not in (1, 2)
        or reason != "APPROVED"
    ):
        raise StaffingError(
            "staffing_invalid_request", "ACCEPT requires candidate and APPROVED"
        )
    if kind == "MODIFY" and (
        type(candidate_index) is not int
        or candidate_index not in (1, 2)
        or reason != "APPROVED_WITH_CHANGES"
    ):
        raise StaffingError(
            "staffing_invalid_request", "MODIFY requires APPROVED_WITH_CHANGES"
        )
    if kind == "REJECT" and (
        candidate_index is not None
        or reason not in {"MANUAL_HANDLING", "INSUFFICIENT_CONTEXT", "OTHER"}
    ):
        raise StaffingError(
            "staffing_invalid_request", "REJECT reason/candidate mismatch"
        )

    request = ManagerResponseRequest(
        request_id,
        generation_id,
        operator,
        kind,
        expected,
        candidate_index,
        tuple(operations),
        reason,
        note,
    )
    canonical_body: dict[str, object] = {
        "schema": body["schema"],
        "request_id": request_id,
        "operator": operator,
        "kind": kind,
        "expected_revisions": {
            "roster": revisions["roster"],
            "exception_set": revisions["exception_set"],
            "effective_plan": revisions["effective_plan"],
        },
        "candidate_index": candidate_index,
        "edited_operations": None
        if kind in {"ACCEPT", "REJECT"}
        else wire_operations,
        "reason_code": reason,
        "note": note,
    }
    return request, canonical_body


def _utc_audit_time(value: object) -> datetime:
    try:
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(timezone.utc)
    except Exception:
        raise StaffingError("staffing_invalid_evidence", "recorded_at") from None


def _event(
    history: StaffingHistory,
    *,
    event_type: str,
    site_id: str,
    deployment_id: str,
    payload: object,
    recorded_at: datetime,
    causation_id: str | None,
) -> StaffingEvent:
    occurred = _utc_audit_time(recorded_at)
    sequence = history.record_count + 1
    event_id = staffing_event_id(
        event_type,
        sequence,
        site_id,
        deployment_id,
        occurred,
        causation_id,
        payload,
    )
    return StaffingEvent(
        event_type,
        event_id,
        sequence,
        site_id,
        deployment_id,
        occurred,
        causation_id,
        payload,
    )


def _history_dates(history: StaffingHistory) -> tuple[date, ...]:
    values: set[date] = set()
    for event in history.events:
        payload = event.payload
        for field in (
            "service_date",
            "exception",
            "cancelled_exception",
            "previous_exception",
            "replacement_exception",
        ):
            value = getattr(payload, field, None)
            if type(value) is date:
                values.add(value)
            elif isinstance(value, StaffingException):
                values.add(value.service_date)
    return tuple(sorted(values))


def _active_exception_by_id(
    history: StaffingHistory, exception_id: str
) -> StaffingException | None:
    for service_date in _history_dates(history):
        for exception in active_exceptions(history.events, service_date):
            if exception.exception_id == exception_id:
                return exception
    return None


def _exception_service_date_by_id(
    history: StaffingHistory, exception_id: str
) -> date | None:
    for event in history.events:
        payload = event.payload
        if (
            event.event_type == "exception_recorded"
            and type(payload) is ExceptionRecordedPayload
            and payload.exception_id == exception_id
        ):
            return payload.service_date
    return None


class StaffingOperations:
    """Atomic, idempotent operations over one verified staffing ledger."""

    def __init__(
        self,
        ledger: StaffingLedger,
        *,
        site_id: str,
        deployment_id: str,
        site_timezone: str,
    ) -> None:
        if not isinstance(ledger, StaffingLedger):
            raise StaffingError("staffing_invalid_evidence", "ledger")
        try:
            validate_identifier(site_id, "site_id")
            validate_identifier(deployment_id, "deployment_id")
        except StaffingError:
            raise StaffingError("staffing_invalid_evidence", "identity") from None
        if ledger.site_id != site_id or ledger.deployment_id != deployment_id:
            raise StaffingError("staffing_identity_mismatch", "site/deployment")
        if type(site_timezone) is not str:
            raise StaffingError("staffing_invalid_evidence", "site_timezone")
        self.ledger = ledger
        self.site_id = site_id
        self.deployment_id = deployment_id
        self.site_timezone = site_timezone

    def _new_event(
        self,
        history: StaffingHistory,
        event_type: str,
        payload: object,
        recorded_at: datetime,
        *,
        causation_id: str | None = None,
    ) -> StaffingEvent:
        return _event(
            history,
            event_type=event_type,
            site_id=self.site_id,
            deployment_id=self.deployment_id,
            payload=payload,
            recorded_at=recorded_at,
            causation_id=causation_id,
        )

    def _append_request(
        self,
        kind: str,
        request_id: str,
        request_digest: str,
        build: Callable[[StaffingHistory, str], StaffingEvent],
    ) -> ReceiptResult:
        def locked_builder(state: VerifiedLedgerState):
            try:
                prior = state.request(kind, request_id, request_digest)
            except StaffingError as error:
                if error.code != "IDEMPOTENCY_CONFLICT":
                    raise
                return ConflictDecision(kind, request_id, "IDEMPOTENCY_CONFLICT")
            if prior is not None:
                return ReturnReceiptDecision(prior)
            try:
                return AppendEventDecision(build(state.history, request_digest))
            except StaffingError as error:
                if error.code not in {
                    "STALE_ROSTER_REVISION",
                    "STALE_EXCEPTION_SET_REVISION",
                    "STALE_EFFECTIVE_PLAN_REVISION",
                    "PROMPT_TEMPLATE_VERSION_MISMATCH",
                    "INVALID_TRANSITION",
                    "STALE_SUGGESTION",
                    "OVERLAPPING_EXCEPTION",
                    "EXPIRED_SUGGESTION",
                }:
                    raise
                if error.code == "STALE_SUGGESTION":
                    code = "STALE_SUGGESTION"
                elif error.code == "INVALID_TRANSITION":
                    code = "INVALID_TRANSITION"
                elif error.code == "OVERLAPPING_EXCEPTION":
                    code = "OVERLAPPING_EXCEPTION"
                elif error.code == "EXPIRED_SUGGESTION":
                    code = "EXPIRED_SUGGESTION"
                else:
                    code = "STALE_REQUEST"
                return ConflictDecision(kind, request_id, code)

        return self.ledger.append_via(locked_builder)

    def import_roster(
        self, payload: object, *, recorded_at: datetime
    ) -> ReceiptResult:
        body = _detach_request_body(payload, code="staffing_invalid_roster")
        request_id = closed_request_id(body, code="staffing_invalid_roster")
        request_digest = _request_digest("roster-import", body)

        def build(history: StaffingHistory, authoritative_digest: str) -> StaffingEvent:
            current_revision = len(roster_revisions(history))
            roster = validate_roster_import(
                body,
                site_id=self.site_id,
                deployment_id=self.deployment_id,
                site_timezone=self.site_timezone,
            )
            expected = body["expected_roster_revision"]
            if expected != current_revision:
                raise StaffingError("STALE_ROSTER_REVISION", "expected_roster_revision")
            if roster.revision != current_revision + 1:
                raise StaffingError("STALE_ROSTER_REVISION", "expected_roster_revision")
            event_payload = RosterImportedPayload(
                request_id,
                authoritative_digest,
                roster.revision,
                roster.roster_digest,
                roster,
                body["operator"],
                body["source_ref"],
            )
            return self._new_event(history, "roster_imported", event_payload, recorded_at)

        return self._append_request(
            "roster-import", request_id, request_digest, build
        )

    def _roster_for_history(
        self, history: StaffingHistory, body: dict[str, object]
    ):
        try:
            service_date = parse_local_date(body.get("service_date"), "service_date")
        except StaffingError:
            raise StaffingError("staffing_invalid_roster", "service_date") from None
        return select_effective_roster(roster_revisions(history), service_date)

    def record_exception(
        self, payload: object, *, recorded_at: datetime
    ) -> ReceiptResult:
        body = _detach_request_body(payload, code="staffing_invalid_roster")
        request_id = closed_request_id(body, code="staffing_invalid_roster")
        request_digest = _request_digest("exception-record", body)

        def build(history: StaffingHistory, authoritative_digest: str) -> StaffingEvent:
            roster = self._roster_for_history(history, body)
            exception = normalize_exception(body, roster=roster)
            current_revision = exception_set_revision(
                history.events, exception.service_date
            )
            expected = body.get("expected_exception_set_revision")
            if type(expected) is int and expected != current_revision:
                raise StaffingError(
                    "STALE_EXCEPTION_SET_REVISION",
                    "expected_exception_set_revision",
                )
            require_no_overlapping_exception(
                active_exceptions(history.events, exception.service_date),
                exception,
            )
            event_payload = ExceptionRecordedPayload(
                request_id,
                authoritative_digest,
                exception.exception_id,
                exception.service_date,
                current_revision + 1,
                exception_digest(exception),
                exception,
                body["operator"],
            )
            return self._new_event(
                history, "exception_recorded", event_payload, recorded_at
            )

        return self._append_request(
            "exception-record", request_id, request_digest, build
        )

    def cancel_exception(
        self, payload: object, *, recorded_at: datetime
    ) -> ReceiptResult:
        body = parse_exception_cancel_request(
            _detach_request_body(payload, code="staffing_invalid_roster")
        )
        request_digest = _request_digest("exception-cancel", body)

        def build(history: StaffingHistory, authoritative_digest: str) -> StaffingEvent:
            service_date = _exception_service_date_by_id(
                history, body["exception_id"]
            )
            if service_date is None:
                raise StaffingError("INVALID_TRANSITION", "unknown exception_id")
            current_revision = exception_set_revision(
                history.events, service_date
            )
            if body["expected_exception_set_revision"] != current_revision:
                raise StaffingError(
                    "STALE_EXCEPTION_SET_REVISION",
                    "expected_exception_set_revision",
                )
            previous = _active_exception_by_id(history, body["exception_id"])
            if previous is None:
                raise StaffingError("INVALID_TRANSITION", "inactive exception_id")
            event_payload = ExceptionCancelledPayload(
                body["request_id"],
                authoritative_digest,
                previous.exception_id,
                current_revision + 1,
                previous,
                body["operator"],
                body["note"],
            )
            return self._new_event(
                history,
                "exception_cancelled",
                event_payload,
                recorded_at,
                causation_id=previous.exception_id,
            )

        return self._append_request(
            "exception-cancel", body["request_id"], request_digest, build
        )

    def correct_exception(
        self, payload: object, *, recorded_at: datetime
    ) -> ReceiptResult:
        body = parse_exception_correction_request(
            _detach_request_body(payload, code="staffing_invalid_roster")
        )
        request_digest = _request_digest("exception-correct", body)

        def build(history: StaffingHistory, authoritative_digest: str) -> StaffingEvent:
            service_date = _exception_service_date_by_id(
                history, body["exception_id"]
            )
            if service_date is None:
                raise StaffingError("INVALID_TRANSITION", "unknown exception_id")
            current_revision = exception_set_revision(
                history.events, service_date
            )
            if body["expected_exception_set_revision"] != current_revision:
                raise StaffingError(
                    "STALE_EXCEPTION_SET_REVISION",
                    "expected_exception_set_revision",
                )
            previous = _active_exception_by_id(history, body["exception_id"])
            if previous is None:
                raise StaffingError("INVALID_TRANSITION", "inactive exception_id")
            roster = select_effective_roster(
                roster_revisions(history), previous.service_date
            )
            replacement = normalize_exception_replacement(
                body, previous=previous, roster=roster
            )
            require_no_overlapping_exception(
                active_exceptions(history.events, previous.service_date),
                replacement,
            )
            event_payload = ExceptionCorrectedPayload(
                body["request_id"],
                authoritative_digest,
                previous.exception_id,
                replacement,
                current_revision + 1,
                exception_digest(replacement),
                previous,
                body["operator"],
            )
            return self._new_event(
                history,
                "exception_corrected",
                event_payload,
                recorded_at,
                causation_id=previous.exception_id,
            )

        return self._append_request(
            "exception-correct", body["request_id"], request_digest, build
        )

    def probe_request(
        self, operation_kind: str, payload: object
    ) -> DuplicateReceipt | ConflictReceipt | None:
        if operation_kind != "suggestion-generate":
            raise StaffingError("staffing_invalid_evidence", "probe operation_kind")
        body = parse_generation_request(payload)
        request_digest = _request_digest("suggestion-generate", body)
        return self.ledger.probe_request(
            operation_kind, body["request_id"], request_digest
        )

    def reserve_generation(
        self,
        payload: object,
        *,
        alias_nonce: bytes,
        route_evidence: GenerationRouteEvidence,
        prompt_template_version: str,
        language: str,
        recorded_at: datetime,
    ) -> ReceiptResult:
        body = parse_generation_request(payload)
        request_digest = _request_digest("suggestion-generate", body)

        def build(history: StaffingHistory, authoritative_digest: str) -> StaffingEvent:
            basis = build_staffing_basis(
                history,
                date.fromisoformat(body["service_date"]),
                prompt_template_version=prompt_template_version,
            )
            expected = body["expected_revisions"]
            if expected["roster"] != basis.basis.roster_revision:
                raise StaffingError("STALE_ROSTER_REVISION", "expected_revisions")
            if expected["exception_set"] != basis.basis.exception_set_revision:
                raise StaffingError(
                    "STALE_EXCEPTION_SET_REVISION", "expected_revisions"
                )
            if expected["effective_plan"] != basis.basis.effective_plan_revision:
                raise StaffingError(
                    "STALE_EFFECTIVE_PLAN_REVISION", "expected_revisions"
                )
            projection = project_generation_request(
                basis,
                alias_nonce=alias_nonce,
                prompt_template_version=prompt_template_version,
                language=language,
            )
            generation_id = staffing_generation_id(
                self.site_id,
                self.deployment_id,
                body["request_id"],
                authoritative_digest,
            )
            event_payload = GenerationReservedPayload(
                body["request_id"],
                authoritative_digest,
                generation_id,
                basis.basis.service_date,
                projection.basis_snapshot,
                projection.provider_payload,
                projection.alias_nonce_digest,
                projection.worker_alias_to_staff_id,
                projection.assignment_alias_to_assignment_id,
                projection.input_digest,
                projection.provider_payload.prompt_template_version,
                projection.provider_payload.language,
                route_evidence,
                body["retry_of"],
                body["operator"],
            )
            return self._new_event(
                history, "generation_reserved", event_payload, recorded_at
            )

        return self._append_request(
            "suggestion-generate", body["request_id"], request_digest, build
        )

    def _append_generation_event(
        self,
        generation_id: str,
        make_event: Callable[[StaffingHistory], StaffingEvent],
    ) -> EventCommit:
        validate_identifier(generation_id, "generation_id")

        def build(state: VerifiedLedgerState) -> AppendEventDecision:
            # Resolve under the exclusive lock so the argument cannot be used
            # merely as decoration while an event for another generation wins.
            state.history.reservation(generation_id)
            event = make_event(state.history)
            if getattr(event.payload, "generation_id", None) != generation_id:
                raise StaffingError("staffing_invalid_evidence", "generation_id")
            return AppendEventDecision(event)

        result = self.ledger.append_event_via(build)
        if type(result) is not EventCommit:
            raise StaffingError("staffing_invalid_evidence", "generation append")
        return result

    def record_attempt_started(
        self,
        generation_id: str,
        evidence: AttemptStartedEvidence,
        *,
        recorded_at: datetime,
    ) -> EventCommit:
        def make(history: StaffingHistory) -> StaffingEvent:
            if (
                type(evidence) is not AttemptStartedEvidence
                or evidence.request_id != generation_id
            ):
                raise StaffingError("staffing_invalid_evidence", "attempt generation")
            reservation = history.reservation(generation_id)
            payload = ProviderAttemptStartedPayload(
                reservation.request_digest, generation_id, evidence
            )
            return self._new_event(
                history,
                "provider_attempt_started",
                payload,
                recorded_at,
                causation_id=generation_id,
            )

        return self._append_generation_event(generation_id, make)

    def record_attempt_finished(
        self,
        generation_id: str,
        evidence: AttemptFinishedEvidence,
        *,
        recorded_at: datetime,
    ) -> EventCommit:
        def make(history: StaffingHistory) -> StaffingEvent:
            if (
                type(evidence) is not AttemptFinishedEvidence
                or evidence.request_id != generation_id
            ):
                raise StaffingError("staffing_invalid_evidence", "attempt generation")
            reservation = history.reservation(generation_id)
            payload = ProviderAttemptFinishedPayload(
                reservation.request_digest, generation_id, evidence
            )
            return self._new_event(
                history,
                "provider_attempt_finished",
                payload,
                recorded_at,
                causation_id=generation_id,
            )

        return self._append_generation_event(generation_id, make)

    def generation_work(self, generation_id: str) -> GenerationProjection:
        reservation = self.ledger.read().reservation(generation_id)
        return GenerationProjection(
            reservation.basis_snapshot,
            reservation.provider_payload,
            reservation.worker_alias_to_staff_id,
            reservation.assignment_alias_to_assignment_id,
            reservation.alias_nonce_digest,
            reservation.input_digest,
        )

    def commit_generation_result(
        self,
        generation_id: str,
        result_evidence: ResultEvidence,
        decoded_output: object | None,
        *,
        recorded_at: datetime,
    ) -> EventCommit:
        validate_identifier(generation_id, "generation_id")

        def build(state: VerifiedLedgerState) -> AppendEventDecision:
            history = state.history
            reservation = history.reservation(generation_id)
            if (
                type(result_evidence) is not ResultEvidence
                or result_evidence.request_id != generation_id
            ):
                raise StaffingError("staffing_invalid_evidence", "result generation")
            projection = GenerationProjection(
                reservation.basis_snapshot,
                reservation.provider_payload,
                reservation.worker_alias_to_staff_id,
                reservation.assignment_alias_to_assignment_id,
                reservation.alias_nonce_digest,
                reservation.input_digest,
            )
            validate_generation_projection(
                projection,
                outer_prompt_template_version=reservation.prompt_template_version,
                outer_language=reservation.language,
            )
            result, detached_output = validate_result_evidence(
                history, reservation, result_evidence, decoded_output
            )
            if result.status == "SUCCEEDED":
                try:
                    if detached_output is None:
                        raise StaffingError(
                            "staffing_invalid_evidence", "decoded_output"
                        )
                    candidates = decode_provider_candidates(detached_output, projection)
                    if result.candidate_count != len(candidates):
                        raise StaffingError(
                            "staffing_invalid_evidence", "candidate_count"
                        )
                    validations = validate_candidates(
                        reservation.basis_snapshot,
                        candidates,
                        worker_alias_to_staff_id=reservation.worker_alias_to_staff_id,
                        assignment_alias_to_assignment_id=(
                            reservation.assignment_alias_to_assignment_id
                        ),
                        prompt_template_version=reservation.prompt_template_version,
                    )
                except StaffingError as error:
                    if error.code not in {
                        "invalid_provider_shape",
                        "provider_sensitive_key",
                        "invalid_provider_timestamp",
                        "invalid_candidate_set",
                    }:
                        raise
                    payload = SuggestionUnavailablePayload(
                        reservation.request_digest,
                        generation_id,
                        result,
                        (),
                        None,
                        "INVALID_RESPONSE",
                        error.code,
                    )
                    return AppendEventDecision(
                        self._new_event(
                            history,
                            "suggestion_unavailable",
                            payload,
                            recorded_at,
                            causation_id=generation_id,
                        )
                    )
                stored_all = tuple(
                    stored_candidate_from_validation(candidate, validation)
                    for candidate, validation in zip(
                        candidates, validations, strict=True
                    )
                )
                stored_valid = tuple(
                    item
                    for item, validation in zip(stored_all, validations)
                    if validation.valid
                )
                digest = (
                    stable_digest(to_primitive(stored_all)) if stored_all else None
                )
                if stored_valid:
                    assert digest is not None
                    payload = SuggestionIssuedPayload(
                        reservation.request_digest,
                        generation_id,
                        result,
                        stored_all,
                        digest,
                    )
                    event_type = "suggestion_issued"
                else:
                    payload = SuggestionUnavailablePayload(
                        reservation.request_digest,
                        generation_id,
                        result,
                        stored_all,
                        digest,
                        "NO_VALID_SUGGESTION",
                        "NO_VALID_SUGGESTION",
                    )
                    event_type = "suggestion_unavailable"
            else:
                if detached_output is not None:
                    raise StaffingError("staffing_invalid_evidence", "decoded_output")
                payload = SuggestionUnavailablePayload(
                    reservation.request_digest,
                    generation_id,
                    result,
                    (),
                    None,
                    result.status,
                    result.failure_code,
                )
                event_type = "suggestion_unavailable"
            return AppendEventDecision(
                self._new_event(
                    history,
                    event_type,
                    payload,
                    recorded_at,
                    causation_id=generation_id,
                )
            )

        result = self.ledger.append_event_via(build)
        if type(result) is not EventCommit:
            raise StaffingError("staffing_invalid_evidence", "generation terminal")
        return result

    def interrupt_generation(
        self, generation_id: str, *, recorded_at: datetime
    ) -> EventCommit | ConflictReceipt:
        validate_identifier(generation_id, "generation_id")

        def build(
            state: VerifiedLedgerState,
        ) -> AppendEventDecision | ConflictDecision:
            generation = state.history.generation(generation_id)
            if generation.is_terminal:
                return ConflictDecision(
                    "suggestion-generate",
                    generation.request_id,
                    "INVALID_TRANSITION",
                )
            occurred = _utc_audit_time(recorded_at)
            payload = GenerationInterruptedPayload(
                generation.request_digest,
                generation_id,
                "RESULT_UNKNOWN",
                occurred,
            )
            return AppendEventDecision(
                self._new_event(
                    state.history,
                    "generation_interrupted",
                    payload,
                    occurred,
                    causation_id=generation_id,
                )
            )

        return self.ledger.append_event_via(build)

    def commit_manager_response(
        self,
        payload: object,
        *,
        suggestion_id: str,
        recorded_at: datetime,
    ) -> ReceiptResult:
        request, body = parse_manager_response(payload, suggestion_id=suggestion_id)
        request_digest = _request_digest(
            "manager-response", body, suggestion_id=suggestion_id
        )

        def build(history: StaffingHistory, authoritative_digest: str) -> StaffingEvent:
            reservations = tuple(
                event
                for event in history.events
                if event.event_type == "generation_reserved"
                and event.payload.generation_id == suggestion_id
            )
            suggestions = tuple(
                event
                for event in history.events
                if event.event_type == "suggestion_issued"
                and event.payload.generation_id == suggestion_id
            )
            responses = tuple(
                event
                for event in history.events
                if event.event_type == "manager_response_committed"
                and event.payload.generation_id == suggestion_id
            )
            if len(reservations) != 1 or len(suggestions) != 1 or responses:
                raise StaffingError(
                    "INVALID_TRANSITION", "manager response lifecycle"
                )
            reservation_event = reservations[0]
            reservation = reservation_event.payload
            if any(
                event.sequence > reservation_event.sequence
                and event.event_type == "roster_imported"
                for event in history.events
            ):
                raise StaffingError("STALE_SUGGESTION", suggestion_id)
            current = build_staffing_basis(
                history,
                reservation.service_date,
                prompt_template_version=reservation.prompt_template_version,
            )
            current_basis = current.basis
            reserved_basis = reservation.basis_snapshot.basis
            if any(
                getattr(current_basis, field) != getattr(reserved_basis, field)
                for field in (
                    "roster_revision",
                    "exception_set_revision",
                    "effective_plan_revision",
                    "roster_digest",
                    "exception_set_digest",
                    "effective_plan_digest",
                    "basis_digest",
                )
            ):
                raise StaffingError("STALE_SUGGESTION", suggestion_id)
            if request.expected_revisions.roster != current_basis.roster_revision:
                raise StaffingError("STALE_ROSTER_REVISION", "expected_revisions")
            if (
                request.expected_revisions.exception_set
                != current_basis.exception_set_revision
            ):
                raise StaffingError(
                    "STALE_EXCEPTION_SET_REVISION", "expected_revisions"
                )
            if (
                request.expected_revisions.effective_plan
                != current_basis.effective_plan_revision
            ):
                raise StaffingError(
                    "STALE_EFFECTIVE_PLAN_REVISION", "expected_revisions"
                )

            audit_at = _utc_audit_time(recorded_at)

            def window_of(operations) -> datetime:
                return action_window_end(
                    operations,
                    assignment_end_by_alias=_assignment_end_by_alias(reservation),
                    service_date=reservation.service_date,
                    site_timezone=reservation.basis_snapshot.site_timezone,
                )

            effective: tuple[Assignment, ...] | None
            if request.kind == "ACCEPT":
                candidate = stored_candidate(
                    history, suggestion_id, request.candidate_index
                )
                if candidate is None:
                    raise StaffingError(
                        "INVALID_TRANSITION", "ACCEPT requires a stored candidate"
                    )
                patch = candidate_patch_for_revalidation(candidate)
                validation = validate_candidate(
                    reservation.basis_snapshot,
                    patch,
                    worker_alias_to_staff_id=reservation.worker_alias_to_staff_id,
                    assignment_alias_to_assignment_id=(
                        reservation.assignment_alias_to_assignment_id
                    ),
                    prompt_template_version=reservation.prompt_template_version,
                )
                if (
                    not validation.valid
                    or validation.materialized_schedule is None
                    or validation.materialized_schedule_digest is None
                    or to_primitive(validation.materialized_schedule)
                    != to_primitive(candidate.materialized_schedule)
                    or validation.materialized_schedule_digest
                    != candidate.materialized_schedule_digest
                    or schedule_digest(validation.materialized_schedule)
                    != validation.materialized_schedule_digest
                ):
                    raise StaffingError(
                        "INVALID_TRANSITION", "stored candidate validation mismatch"
                    )
                effective = validation.materialized_schedule
            elif request.kind == "MODIFY":
                stored = stored_candidate(
                    history, suggestion_id, request.candidate_index
                )
                if stored is None:
                    raise StaffingError(
                        "INVALID_TRANSITION", "MODIFY requires a stored candidate"
                    )
                # Editing cannot revive an expired suggestion: the stored
                # candidate's own window is checked before the edits are
                # resolved or validated.
                if is_expired(
                    window_of(candidate_patch_for_revalidation(stored).operations),
                    audit_at,
                ):
                    raise StaffingError("EXPIRED_SUGGESTION", suggestion_id)
                patch = local_operations_to_candidate(request, reservation)
                validation = validate_candidate(
                    reservation.basis_snapshot,
                    patch,
                    worker_alias_to_staff_id=reservation.worker_alias_to_staff_id,
                    assignment_alias_to_assignment_id=(
                        reservation.assignment_alias_to_assignment_id
                    ),
                    prompt_template_version=reservation.prompt_template_version,
                )
                if not validation.valid or validation.materialized_schedule is None:
                    raise StaffingError(
                        "INVALID_TRANSITION", "MODIFY operations are not valid"
                    )
                effective = validation.materialized_schedule
            else:
                effective = None

            if request.kind in {"ACCEPT", "MODIFY"}:
                # A candidate is a current action only while every shift it
                # adds or removes is still open: the window closes at the
                # earliest affected end.  The injected audit instant is the
                # only clock; replay of an already committed response never
                # re-applies this rule.
                if is_expired(window_of(patch.operations), audit_at):
                    raise StaffingError("EXPIRED_SUGGESTION", suggestion_id)

            if effective is None:
                digest = None
                plan_revision = None
            else:
                effective = canonical_schedule(effective)
                digest = schedule_digest(effective)
                plan_revision = current_basis.effective_plan_revision + 1
            event_payload = ManagerResponseCommittedPayload(
                request.request_id,
                authoritative_digest,
                suggestion_id,
                request.kind,
                request.reason_code,
                effective,
                digest,
                plan_revision,
                request.operator,
                request.note,
                reservation.basis_snapshot,
            )
            return self._new_event(
                history,
                "manager_response_committed",
                event_payload,
                recorded_at,
                causation_id=suggestion_id,
            )

        return self._append_request(
            "manager-response", request.request_id, request_digest, build
        )

    def recover_interrupted_generations(
        self, *, recorded_at: datetime
    ) -> tuple[EventCommit | ConflictReceipt, ...]:
        history = self.ledger.read()
        generation_ids = tuple(
            event.payload.generation_id
            for event in history.events
            if event.event_type == "generation_reserved"
            and not history.generation(event.payload.generation_id).is_terminal
        )
        return tuple(
            self.interrupt_generation(generation_id, recorded_at=recorded_at)
            for generation_id in generation_ids
        )

    def date_projection(self, service_date: date) -> DateProjection:
        return build_date_projection(
            self.ledger.read(),
            service_date,
            site_id=self.site_id,
            deployment_id=self.deployment_id,
            site_timezone=self.site_timezone,
        )

    def request_projection(
        self, operation_kind: str, request_id: str
    ) -> RequestProjection:
        history = self.ledger.read()
        if history.find_request_event(operation_kind, request_id) is None:
            raise StaffingError("REQUEST_NOT_FOUND", request_id)
        return build_request_projection(history, operation_kind, request_id)


MAX_RESULT_EVIDENCE_DEPTH = 20
MAX_RESULT_EVIDENCE_OCCURRENCES = 524_289


class _InvalidResultTree(Exception):
    """Private decoded-output sentinel normalized at the evidence boundary."""


def _detach_result_output(value: object) -> dict[str, object]:
    occurrences = 0
    ancestors: set[int] = set()

    def fail() -> NoReturn:
        raise _InvalidResultTree

    def visit(item: object, depth: int) -> object:
        nonlocal occurrences
        occurrences += 1
        if occurrences > MAX_RESULT_EVIDENCE_OCCURRENCES:
            fail()
        if type(item) in (dict, MappingProxyType, list, tuple):
            if depth > MAX_RESULT_EVIDENCE_DEPTH:
                fail()
            identity = id(item)
            if identity in ancestors:
                fail()
            ancestors.add(identity)
            try:
                if type(item) in (dict, MappingProxyType):
                    result: dict[str, object] = {}
                    for key, child in item.items():
                        if type(key) is not str or key in result:
                            fail()
                        try:
                            key.encode("utf-8")
                        except UnicodeError:
                            fail()
                        result[key] = visit(child, depth + 1)
                    return result
                return [visit(child, depth + 1) for child in item]
            finally:
                ancestors.remove(identity)
        if item is None or type(item) is bool or type(item) is int:
            return item
        if type(item) is float:
            if not math.isfinite(item):
                fail()
            return item
        if type(item) is str:
            try:
                item.encode("utf-8")
            except UnicodeError:
                fail()
            return item
        fail()

    try:
        detached = visit(value, 1)
        if type(detached) is not dict:
            fail()
        return detached
    except Exception:
        raise StaffingError("staffing_invalid_evidence", "decoded_output") from None


def validate_result_evidence(
    history: StaffingHistory,
    reservation: GenerationReservedPayload,
    result: ResultEvidence,
    decoded_output: object | None,
) -> tuple[ResultEvidence, dict[str, object] | None]:
    """Bind one gateway result to persisted attempts and one output snapshot."""

    # This workflow-owned matrix is deliberately the first validation step.
    terminal_state = result.status if type(result) is ResultEvidence else None
    failure_code = result.failure_code if type(result) is ResultEvidence else None
    validate_replayable_result_evidence(
        history,
        reservation,
        result,
        reservation.generation_id,
        terminal_state,
        failure_code,
    )
    if result.status == "RESULT_UNKNOWN":
        raise StaffingError("staffing_invalid_evidence", "result status")
    if result.status != "SUCCEEDED":
        if decoded_output is not None or result.output_digest is not None:
            raise StaffingError("staffing_invalid_evidence", "decoded_output")
        if result.candidate_count != 0:
            raise StaffingError("staffing_invalid_evidence", "candidate_count")
        return result, None
    if decoded_output is None or result.output_digest is None:
        raise StaffingError("staffing_invalid_evidence", "decoded_output")
    detached = _detach_result_output(decoded_output)
    try:
        output_digest = stable_digest(detached)
        raw_candidates = detached.get("candidates")
        hint = (
            len(raw_candidates)
            if type(raw_candidates) is list and len(raw_candidates) <= 2
            else 0
        )
    except StaffingError:
        raise
    except Exception:
        raise StaffingError("staffing_invalid_evidence", "decoded_output") from None
    if result.output_digest != output_digest:
        raise StaffingError("staffing_invalid_evidence", "output_digest")
    if result.candidate_count != hint:
        raise StaffingError("staffing_invalid_evidence", "candidate_count")
    return result, detached


def stored_candidate(
    history: StaffingHistory, generation_id: str, candidate_index: int | None
) -> StoredCandidate | None:
    if candidate_index not in (1, 2):
        return None
    for event in reversed(history.events):
        if (
            event.event_type in {"suggestion_issued", "suggestion_unavailable"}
            and event.payload.generation_id == generation_id
        ):
            return next(
                (
                    item
                    for item in event.payload.candidates
                    if item.candidate_index == candidate_index
                ),
                None,
            )
    return None


def local_operations_to_candidate(
    request: ManagerResponseRequest,
    reservation: GenerationReservedPayload,
) -> CandidatePatch:
    worker_ids, assignment_ids = validate_alias_maps(
        reservation.basis_snapshot,
        reservation.worker_alias_to_staff_id,
        reservation.assignment_alias_to_assignment_id,
    )
    inverse_workers = {staff_id: alias for alias, staff_id in worker_ids.items()}
    inverse_assignments = {
        assignment_id: alias for alias, assignment_id in assignment_ids.items()
    }
    converted: list[RemoveOperation | AddOperation] = []
    for item in request.edited_operations:
        if item.operation == "REMOVE":
            if item.assignment_id not in inverse_assignments:
                raise StaffingError("INVALID_TRANSITION", "unknown assignment_id")
            converted.append(
                RemoveOperation("REMOVE", inverse_assignments[item.assignment_id])
            )
        else:
            if item.staff_id not in inverse_workers:
                raise StaffingError("INVALID_TRANSITION", "unknown staff_id")
            converted.append(
                AddOperation(
                    "ADD",
                    inverse_workers[item.staff_id],
                    item.role_code,
                    item.area_code,
                    item.start_at,
                    item.end_at,
                )
            )
    return CandidatePatch(
        request.candidate_index,
        tuple(converted),
        "manager modification",
        (),
    )


def _assignment_views(
    items: Sequence[Assignment], workers: Sequence[Worker]
) -> tuple[AssignmentProjection, ...]:
    names = {worker.staff_id: worker.display_name for worker in workers}
    return tuple(
        AssignmentProjection(
            item.assignment_id,
            item.staff_id,
            names.get(item.staff_id, ""),
            item.role_code,
            item.area_code,
            item.start_at,
            item.end_at,
        )
        for item in items
    )


def _exception_views(
    items: Sequence[StaffingException], workers: Sequence[Worker]
) -> tuple[ExceptionProjection, ...]:
    names = {worker.staff_id: worker.display_name for worker in workers}
    return tuple(
        ExceptionProjection(
            item.exception_id,
            item.service_date,
            item.staff_id,
            names.get(item.staff_id, ""),
            item.kind,
            item.unavailable_start,
            item.unavailable_end,
            item.note,
        )
        for item in items
    )


def _assignment_end_by_alias(
    reservation: GenerationReservedPayload,
) -> dict[str, datetime]:
    ends = {
        item.assignment_id: item.end_at
        for item in reservation.basis_snapshot.assignments
    }
    return {
        alias: ends[assignment_id]
        for alias, assignment_id in reservation.assignment_alias_to_assignment_id
        if assignment_id in ends
    }


def _candidate_view(
    candidate: StoredCandidate,
    reservation: GenerationReservedPayload,
    workers: Sequence[Worker],
) -> CandidateProjection:
    # The fixed-offset replay adapter is the sole public operation source.
    restored = candidate_patch_for_revalidation(candidate)
    inverse_workers = {
        alias: staff_id for alias, staff_id in reservation.worker_alias_to_staff_id
    }
    inverse_assignments = {
        alias: assignment_id
        for alias, assignment_id in reservation.assignment_alias_to_assignment_id
    }
    assignments = {
        item.assignment_id: item for item in reservation.basis_snapshot.assignments
    }
    names = {worker.staff_id: worker.display_name for worker in workers}
    operations: list[CandidateOperationProjection] = []
    for item in restored.operations:
        if item.operation == "REMOVE":
            assignment_id = inverse_assignments.get(item.assignment_alias)
            assignment = assignments.get(assignment_id)
            staff_id = None if assignment is None else assignment.staff_id
            operations.append(
                CandidateOperationProjection(
                    "REMOVE",
                    assignment_id,
                    staff_id,
                    names.get(staff_id) if staff_id is not None else None,
                    None if assignment is None else assignment.role_code,
                    None if assignment is None else assignment.area_code,
                    None if assignment is None else assignment.start_at,
                    None if assignment is None else assignment.end_at,
                )
            )
        else:
            staff_id = inverse_workers.get(item.worker_alias)
            operations.append(
                CandidateOperationProjection(
                    "ADD",
                    None,
                    staff_id,
                    names.get(staff_id) if staff_id is not None else None,
                    item.role_code,
                    item.area_code,
                    item.start_at,
                    item.end_at,
                )
            )
    labels = local_alias_labels(
        workers=workers,
        assignments=reservation.basis_snapshot.assignments,
        worker_alias_to_staff_id=reservation.worker_alias_to_staff_id,
        assignment_alias_to_assignment_id=(
            reservation.assignment_alias_to_assignment_id
        ),
        site_timezone=reservation.basis_snapshot.site_timezone,
    )
    return CandidateProjection(
        candidate.candidate_index,
        candidate.materialized_schedule is not None
        and not candidate.rejection_codes,
        candidate.rationale,
        candidate.operational_warnings,
        candidate.rejection_codes,
        candidate.coverage_gaps,
        tuple(operations),
        None
        if candidate.materialized_schedule is None
        else _assignment_views(candidate.materialized_schedule, workers),
        candidate.materialized_schedule_digest,
        render_local_text(candidate.rationale, labels),
        tuple(
            render_local_text(item, labels)
            for item in candidate.operational_warnings
        ),
        action_window_end(
            restored.operations,
            assignment_end_by_alias=_assignment_end_by_alias(reservation),
            service_date=reservation.service_date,
            site_timezone=reservation.basis_snapshot.site_timezone,
        ),
    )


def _generation_lifecycle(
    events: Sequence[StaffingEvent], generation_id: str
) -> str:
    state = "RESERVED"
    for event in sorted(
        (
            item
            for item in events
            if getattr(item.payload, "generation_id", None) == generation_id
        ),
        key=lambda item: item.sequence,
    ):
        if event.event_type in {
            "provider_attempt_started",
            "provider_attempt_finished",
        }:
            state = "IN_PROGRESS"
        elif event.event_type == "generation_interrupted":
            state = "RESULT_UNKNOWN"
        elif event.event_type == "suggestion_issued":
            state = event.payload.result.status
        elif event.event_type == "suggestion_unavailable":
            state = event.payload.terminal_state
    return state


def _generation_view(
    history: StaffingHistory, generation_id: str
) -> GenerationProjectionView:
    reservation = history.reservation(generation_id)
    workers = reservation.basis_snapshot.workers
    candidates: tuple[CandidateProjection, ...] = ()
    for event in history.events:
        if getattr(event.payload, "generation_id", None) != generation_id:
            continue
        if event.event_type in {"suggestion_issued", "suggestion_unavailable"}:
            candidates = tuple(
                _candidate_view(item, reservation, workers)
                for item in event.payload.candidates
            )
    reservation_event = next(
        event
        for event in history.events
        if event.event_type == "generation_reserved"
        and event.payload.generation_id == generation_id
    )
    result_event = next(
        (
            event
            for event in reversed(history.events)
            if event.event_type in {"suggestion_issued", "suggestion_unavailable"}
            and event.payload.generation_id == generation_id
        ),
        None,
    )
    failure_code = (
        None
        if result_event is None
        else (
            result_event.payload.result.failure_code
            if result_event.event_type == "suggestion_issued"
            else result_event.payload.failure_code
        )
    )
    route = reservation.route
    started = tuple(
        event.payload.evidence
        for event in history.events
        if event.event_type == "provider_attempt_started"
        and event.payload.generation_id == generation_id
    )
    finished = tuple(
        event.payload.evidence
        for event in history.events
        if event.event_type == "provider_attempt_finished"
        and event.payload.generation_id == generation_id
    )
    return GenerationProjectionView(
        generation_id,
        reservation.request_id,
        reservation_event.event_id,
        reservation.service_date,
        (
            reservation.basis_snapshot.basis.roster_revision,
            reservation.basis_snapshot.basis.exception_set_revision,
            reservation.basis_snapshot.basis.effective_plan_revision,
        ),
        reservation.basis_snapshot.basis.basis_digest,
        reservation.retry_of,
        _generation_lifecycle(history.events, generation_id),
        failure_code,
        route.region,
        route.readiness,
        route.primary_provider,
        route.primary_model_id,
        route.backup_provider,
        route.backup_model_id,
        started,
        finished,
        candidates,
    )


def build_date_projection(
    history: StaffingHistory,
    service_date: date,
    *,
    site_id: str | None = None,
    deployment_id: str | None = None,
    site_timezone: str | None = None,
) -> DateProjection:
    if type(history) is not StaffingHistory or type(service_date) is not date:
        raise StaffingError("staffing_invalid_evidence", "date projection")
    roster_events = tuple(
        event for event in history.events if event.event_type == "roster_imported"
    )
    if not roster_events:
        return DateProjection(
            site_id=site_id,
            deployment_id=deployment_id,
            service_date=service_date,
            site_timezone=site_timezone,
            roster_revision=None,
            roster_digest=None,
            exception_set_revision=0,
            roster_effective_from=None,
            roster_effective_until=None,
            worker_count=0,
            assignment_count=0,
            coverage_count=0,
            availability=(),
            assignments=(),
            exceptions=(),
            effective_plan=EffectivePlanState(
                0, "NO_PLAN", stable_digest(()), None, 0, ()
            ),
            effective_plan_schedule=None,
            generations=(),
            manager_responses=(),
        )
    roster = select_effective_roster(roster_revisions(history), service_date)
    roster_day = materialize_service_day(roster, service_date)
    basis = build_staffing_basis(history, service_date)
    generations = tuple(
        _generation_view(history, event.payload.generation_id)
        for event in history.events
        if event.event_type == "generation_reserved"
        and event.payload.service_date == service_date
    )
    managers = tuple(
        ManagerResponseProjection(
            event.payload.generation_id,
            event.payload.decision,
            event.payload.reason_code,
            event.payload.operator,
            event.payload.note,
            event.payload.effective_plan_revision,
            event.payload.schedule_digest,
            None
            if event.payload.effective_schedule is None
            else _assignment_views(
                event.payload.effective_schedule,
                event.payload.basis_snapshot.workers,
            ),
        )
        for event in history.events
        if event.event_type == "manager_response_committed"
        and event.payload.basis_snapshot.basis.service_date == service_date
    )
    plan = effective_plan_state(history, service_date, roster.revision)
    return DateProjection(
        site_id=roster.site_id,
        deployment_id=roster.deployment_id,
        service_date=service_date,
        site_timezone=roster.site_timezone,
        roster_revision=roster.revision,
        roster_digest=roster.roster_digest,
        exception_set_revision=basis.basis.exception_set_revision,
        roster_effective_from=roster.effective_from,
        roster_effective_until=roster.effective_until,
        worker_count=len(roster.workers),
        assignment_count=len(roster.regular_assignments),
        coverage_count=len(roster.coverage),
        availability=tuple(basis.availability),
        # The date projection keeps roster shifts available for local exception
        # entry.  The accepted advisory schedule is projected independently as
        # effective_plan_schedule; using it here would hide a removed roster
        # worker and expose an added substitute that exception validation does
        # not treat as having a regular shift.
        assignments=_assignment_views(roster_day.assignments, roster.workers),
        exceptions=_exception_views(basis.exceptions, basis.workers),
        effective_plan=plan,
        effective_plan_schedule=None
        if plan.effective_schedule is None
        else _assignment_views(plan.effective_schedule, basis.workers),
        generations=generations,
        manager_responses=managers,
    )


def build_request_projection(
    history: StaffingHistory, operation_kind: str, request_id: str
) -> RequestProjection:
    if type(history) is not StaffingHistory:
        raise StaffingError("staffing_invalid_evidence", "request projection")
    event = history.find_request_event(operation_kind, request_id)
    if event is None:
        raise StaffingError("REQUEST_NOT_FOUND", request_id)
    generation_id = getattr(event.payload, "generation_id", None)
    related = tuple(
        item
        for item in history.events
        if generation_id is not None
        and getattr(item.payload, "generation_id", None) == generation_id
    )
    reservation = (
        history.reservation(generation_id) if generation_id is not None else None
    )
    candidates: tuple[CandidateProjection, ...] = ()
    result: ResultEvidence | None = None
    manager: ManagerResponseProjection | None = None
    started = tuple(
        item.payload.evidence
        for item in related
        if item.event_type == "provider_attempt_started"
    )
    attempts = tuple(
        item.payload.evidence
        for item in related
        if item.event_type == "provider_attempt_finished"
    )
    if reservation is not None:
        for item in related:
            if item.event_type in {"suggestion_issued", "suggestion_unavailable"}:
                candidates = tuple(
                    _candidate_view(
                        candidate,
                        reservation,
                        reservation.basis_snapshot.workers,
                    )
                    for candidate in item.payload.candidates
                )
                result = item.payload.result
            elif item.event_type == "manager_response_committed":
                manager = ManagerResponseProjection(
                    item.payload.generation_id,
                    item.payload.decision,
                    item.payload.reason_code,
                    item.payload.operator,
                    item.payload.note,
                    item.payload.effective_plan_revision,
                    item.payload.schedule_digest,
                    None
                    if item.payload.effective_schedule is None
                    else _assignment_views(
                        item.payload.effective_schedule,
                        item.payload.basis_snapshot.workers,
                    ),
                )
    event_ids = tuple(dict.fromkeys(item.event_id for item in related))
    if event.event_id not in event_ids:
        event_ids = (event.event_id,) + event_ids
    lifecycle = (
        "COMMITTED"
        if operation_kind == "manager-response"
        else (
            _generation_lifecycle(related, generation_id)
            if generation_id is not None
            else "COMMITTED"
        )
    )
    payload = event.payload
    if getattr(payload, "generation_id", None) is not None:
        record_workers = history.reservation(
            payload.generation_id
        ).basis_snapshot.workers
    else:
        record_date = getattr(payload, "service_date", None)
        if record_date is None and getattr(payload, "exception", None) is not None:
            record_date = payload.exception.service_date
        if (
            record_date is None
            and getattr(payload, "replacement_exception", None) is not None
        ):
            record_date = payload.replacement_exception.service_date
        if (
            record_date is None
            and getattr(payload, "cancelled_exception", None) is not None
        ):
            record_date = payload.cancelled_exception.service_date
        record_roster = (
            select_effective_roster(roster_revisions(history), record_date)
            if record_date is not None
            else None
        )
        record_workers = () if record_roster is None else record_roster.workers

    if event.event_type == "roster_imported":
        committed = RosterCommittedProjection(
            event.event_id,
            event.occurred_at_utc,
            payload.request_digest,
            payload.operator,
            payload.source_ref,
            payload.roster_revision,
            payload.roster_digest,
            payload.roster.effective_from,
            payload.roster.effective_until,
            len(payload.roster.workers),
            len(payload.roster.regular_assignments),
            len(payload.roster.coverage),
        )
    elif event.event_type in {
        "exception_recorded",
        "exception_cancelled",
        "exception_corrected",
    }:
        committed = ExceptionCommittedProjection(
            event.event_id,
            event.event_type,
            event.occurred_at_utc,
            payload.request_digest,
            payload.operator,
            payload.exception_id,
            payload.exception_set_revision,
            getattr(payload, "exception_digest", None),
            None
            if getattr(payload, "exception", None) is None
            else _exception_views((payload.exception,), record_workers)[0],
            None
            if getattr(payload, "replacement_exception", None) is None
            else _exception_views(
                (payload.replacement_exception,), record_workers
            )[0],
            getattr(payload, "note", None),
        )
    elif event.event_type == "generation_reserved":
        committed = GenerationCommittedProjection(
            event.event_id,
            event.occurred_at_utc,
            payload.request_digest,
            payload.generation_id,
            payload.service_date,
            (
                payload.basis_snapshot.basis.roster_revision,
                payload.basis_snapshot.basis.exception_set_revision,
                payload.basis_snapshot.basis.effective_plan_revision,
            ),
            payload.basis_snapshot.basis.basis_digest,
            payload.retry_of,
            payload.route.region,
            payload.route.readiness,
            payload.route.primary_provider,
            payload.route.backup_provider,
        )
    elif event.event_type == "manager_response_committed":
        committed = ManagerCommittedProjection(
            event.event_id,
            event.occurred_at_utc,
            payload.request_digest,
            payload.generation_id,
            payload.operator,
            payload.note,
            payload.decision,
            payload.reason_code,
            payload.effective_plan_revision,
            payload.schedule_digest,
            None
            if payload.effective_schedule is None
            else _assignment_views(payload.effective_schedule, record_workers),
        )
    else:
        committed = None
    generation_view = (
        _generation_view(history, generation_id)
        if generation_id is not None
        else None
    )
    return RequestProjection(
        operation_kind,
        request_id,
        event_ids,
        generation_id,
        lifecycle,
        started,
        attempts,
        result,
        candidates,
        manager,
        generation_view,
        committed,
    )


__all__ = ("StaffingOperations",)
