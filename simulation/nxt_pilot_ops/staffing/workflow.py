"""Pure, closed staffing event parsing, transition validation, and replay."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Sequence

from ..serialization import canonical_json_bytes, stable_digest, to_primitive
from .contracts import (
    CODE_PATTERN,
    EVENT_TO_OPERATION,
    EVENT_TYPES,
    GENERATION_TERMINALS,
    HEX_DIGEST_PATTERN,
    IDENTIFIER_PATTERN,
    MANAGER_REASON_CODES,
    PROMPT_TEMPLATE_VERSION,
    AddOperation,
    Assignment,
    AssignmentRule,
    AttemptFinishedEvidence,
    AttemptRouteEvidence,
    AttemptStartedEvidence,
    AvailabilityWindow,
    BasisSnapshot,
    CoverageGap,
    CoverageRequirement,
    CoverageWindow,
    ExceptionCancelledPayload,
    ExceptionCorrectedPayload,
    ExceptionRecordedPayload,
    GenerationInterruptedPayload,
    GenerationReservedPayload,
    GenerationRouteEvidence,
    ManagerResponseCommittedPayload,
    ProviderAssignment,
    ProviderAttemptFinishedPayload,
    ProviderAttemptStartedPayload,
    ProviderAvailability,
    ProviderCoverage,
    ProviderPayload,
    ProviderUnavailable,
    ProviderWorker,
    RemoveOperation,
    ResultEvidence,
    RosterImportedPayload,
    RosterRevision,
    StaffingBasis,
    StaffingError,
    StaffingEvent,
    StaffingException,
    StaffingHistory,
    StoredCandidate,
    SuggestionIssuedPayload,
    SuggestionUnavailablePayload,
    WeeklyAssignment,
    Worker,
    parse_attempt_finished_evidence,
    parse_attempt_started_evidence,
    parse_generation_route_evidence,
    parse_result_evidence,
)
from .exceptions import exception_digest
from .plans import _validate_snapshot_shape, build_staffing_basis, effective_plan_state
from .projection import GenerationProjection, validate_generation_projection
from .time import utc_text
from .validator import (
    REJECTION_CODES,
    CandidatePatch,
    CandidateValidation,
    canonical_schedule,
    validate_candidates,
)


EVENT_RECORD_KEYS = frozenset(
    {
        "event_type",
        "event_id",
        "sequence",
        "site_id",
        "deployment_id",
        "occurred_at_utc",
        "causation_id",
        "payload",
    }
)
FORBIDDEN_EVENT_KEYS = frozenset(
    {"prompt", "raw_response", "reasoning", "secret", "api_key", "headers", "metadata"}
)
MAX_EVENT_DEPTH = 32
MAX_EVENT_OCCURRENCES = 524_289

EVENT_PAYLOAD_TYPES = {
    "roster_imported": RosterImportedPayload,
    "exception_recorded": ExceptionRecordedPayload,
    "exception_cancelled": ExceptionCancelledPayload,
    "exception_corrected": ExceptionCorrectedPayload,
    "generation_reserved": GenerationReservedPayload,
    "generation_interrupted": GenerationInterruptedPayload,
    "provider_attempt_started": ProviderAttemptStartedPayload,
    "provider_attempt_finished": ProviderAttemptFinishedPayload,
    "suggestion_issued": SuggestionIssuedPayload,
    "suggestion_unavailable": SuggestionUnavailablePayload,
    "manager_response_committed": ManagerResponseCommittedPayload,
}

GATEWAY_FAILURE_CODES = frozenset(
    {
        "INPUT_TOO_LARGE",
        "RESPONSE_TOO_LARGE",
        "UNSUPPORTED_CONTENT_ENCODING",
        "REDIRECT_REFUSED",
        "ENDPOINT_NOT_ALLOWED",
        "TLS_VERIFICATION_FAILED",
        "DNS_FAILURE",
        "CONNECT_TIMEOUT",
        "CONNECT_FAILED",
        "READ_TIMEOUT",
        "CONNECTION_INTERRUPTED",
        "HTTP_TIMEOUT",
        "RATE_LIMITED",
        "PROVIDER_UNAVAILABLE",
        "AUTHENTICATION_FAILED",
        "PERMISSION_DENIED",
        "MODEL_NOT_FOUND",
        "INVALID_PROVIDER_REQUEST",
        "PROVIDER_CLIENT_ERROR",
        "PROVIDER_REFUSED",
        "MALFORMED_PROVIDER_RESPONSE",
        "SCHEMA_MISMATCH",
        "BACKUP_UNCONFIGURED",
        "DEADLINE_EXHAUSTED",
        "PROVIDER_UNCONFIGURED",
    }
)
FALLBACK_ELIGIBLE_CODES = frozenset(
    {
        "DNS_FAILURE",
        "CONNECT_TIMEOUT",
        "CONNECT_FAILED",
        "READ_TIMEOUT",
        "CONNECTION_INTERRUPTED",
        "HTTP_TIMEOUT",
        "RATE_LIMITED",
        "PROVIDER_UNAVAILABLE",
    }
)
LOCAL_FAILURE_CODES = frozenset(
    {"INPUT_TOO_LARGE", "BACKUP_UNCONFIGURED", "PROVIDER_UNCONFIGURED"}
)
PROVIDER_WIRE_FAILURE_CODES = frozenset(
    {
        "invalid_provider_shape",
        "provider_sensitive_key",
        "invalid_provider_timestamp",
        "invalid_candidate_set",
    }
)

_CANONICAL_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)


def _event_error(detail: str) -> StaffingError:
    return StaffingError("staffing_invalid_event", detail)


def _evidence_error(detail: str) -> StaffingError:
    return StaffingError("staffing_invalid_evidence", detail)


def _identity_error() -> StaffingError:
    return StaffingError("staffing_identity_mismatch", "event")


def _safe_unicode(value: object, *, minimum: int, maximum: int) -> bool:
    return (
        type(value) is str
        and minimum <= len(value) <= maximum
        and (minimum == 0 or bool(value.strip()))
        and not any(unicodedata.category(character).startswith("C") for character in value)
    )


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise _evidence_error(field)
    return value


def _digest(value: object, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str or HEX_DIGEST_PATTERN.fullmatch(value) is None:
        raise _evidence_error(field)
    return value


def _code(value: object, field: str) -> str:
    if type(value) is not str or CODE_PATTERN.fullmatch(value) is None:
        raise _evidence_error(field)
    return value


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise _evidence_error(field)
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise _evidence_error(field)
    return value


def _human_text(
    value: object,
    field: str,
    *,
    minimum: int = 1,
    maximum: int,
    formula_safe: bool = False,
) -> str:
    if not _safe_unicode(value, minimum=minimum, maximum=maximum):
        raise _evidence_error(field)
    assert type(value) is str
    if formula_safe and value.lstrip().startswith(("=", "+", "-", "@")):
        raise _evidence_error(field)
    return value


def _optional_human_text(value: object, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _human_text(value, field, minimum=0, maximum=maximum)


def _exact_object(value: object, keys: frozenset[str], detail: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != set(keys):
        raise _evidence_error(detail)
    return dict(value)


def _array(value: object, detail: str, *, maximum: int | None = None) -> list[object]:
    if type(value) is not list or (maximum is not None and len(value) > maximum):
        raise _evidence_error(detail)
    return list(value)


def _canonical_date(value: object, field: str) -> date:
    if type(value) is not str or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise _evidence_error(field)
    try:
        parsed = date.fromisoformat(value)
    except Exception:
        raise _evidence_error(field) from None
    if parsed.isoformat() != value:
        raise _evidence_error(field)
    return parsed


def _canonical_datetime(value: object, field: str) -> datetime:
    if type(value) is not str or _CANONICAL_UTC_PATTERN.fullmatch(value) is None:
        raise _evidence_error(field)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except Exception:
        raise _evidence_error(field) from None
    try:
        if utc_text(parsed) != value:
            raise _evidence_error(field)
    except Exception:
        raise _evidence_error(field) from None
    return parsed


def _canonical_bytes(value: object) -> bytes:
    try:
        return canonical_json_bytes(to_primitive(value))
    except Exception:
        raise _evidence_error("canonical value") from None


def _require_canonical(source: object, parsed: object, detail: str) -> object:
    try:
        if _canonical_bytes(source) != _canonical_bytes(parsed):
            raise _evidence_error(detail)
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error(detail) from None
    return parsed


def scan_and_detach_event_tree(value: object) -> object:
    """Detach one exact JSON tree, scanning sensitive keys before shape checks."""

    occurrences = 0
    ancestors: set[int] = set()
    invalid_event = False
    invalid_evidence = False
    root_sensitive = False
    payload_sensitive = False

    def mark_invalid(in_payload: bool) -> object:
        nonlocal invalid_event, invalid_evidence
        if in_payload:
            invalid_evidence = True
        else:
            invalid_event = True
        return None

    def visit(item: object, depth: int, *, in_payload: bool) -> object:
        nonlocal occurrences, root_sensitive, payload_sensitive
        occurrences += 1
        if occurrences > MAX_EVENT_OCCURRENCES:
            if in_payload:
                raise _evidence_error("event tree")
            raise _event_error("event tree")
        if type(item) in (dict, list):
            if type(item) is dict:
                for key in item:
                    if type(key) is not str or key not in FORBIDDEN_EVENT_KEYS:
                        continue
                    key_in_payload = in_payload or (depth == 0 and key == "payload")
                    if key_in_payload:
                        payload_sensitive = True
                    else:
                        root_sensitive = True
            if depth > MAX_EVENT_DEPTH:
                return mark_invalid(in_payload)
            identity = id(item)
            if identity in ancestors:
                return mark_invalid(in_payload)
            ancestors.add(identity)
            try:
                if type(item) is dict:
                    output: dict[str, object] = {}
                    valid_keys = True
                    for key, child in item.items():
                        if type(key) is not str:
                            valid_keys = False
                            visit(child, depth + 1, in_payload=in_payload)
                            continue
                        child_in_payload = in_payload or (depth == 0 and key == "payload")
                        output[key] = visit(
                            child, depth + 1, in_payload=child_in_payload
                        )
                    if not valid_keys:
                        mark_invalid(in_payload)
                    return output
                return [
                    visit(child, depth + 1, in_payload=in_payload) for child in item
                ]
            finally:
                ancestors.remove(identity)
        if item is None or type(item) in (bool, int):
            return item
        if type(item) is float:
            if not math.isfinite(item):
                return mark_invalid(in_payload)
            return item
        if type(item) is str:
            try:
                item.encode("utf-8")
            except Exception:
                return mark_invalid(in_payload)
            return item
        return mark_invalid(in_payload)

    detached = visit(value, 0, in_payload=False)
    if payload_sensitive:
        raise _evidence_error("forbidden event field")
    if root_sensitive:
        raise _event_error("forbidden event field")
    if invalid_event:
        raise _event_error("event tree")
    if invalid_evidence:
        raise _evidence_error("event tree")
    return detached


def _parse_pair_array(value: object, detail: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for raw_pair in _array(value, detail):
        if type(raw_pair) is not list or len(raw_pair) != 2:
            raise _evidence_error(detail)
        left, right = raw_pair
        if type(left) is not str or type(right) is not str:
            raise _evidence_error(detail)
        result.append((left, right))
    return tuple(result)


def _parse_code_array(value: object, detail: str) -> tuple[str, ...]:
    return tuple(_code(item, detail) for item in _array(value, detail))


def _parse_worker(value: object) -> Worker:
    keys = frozenset({item.name for item in fields(Worker)})
    body = _exact_object(value, keys, "worker")
    eligibility = _parse_pair_array(body["eligibility"], "eligibility")
    for role, area in eligibility:
        _code(role, "role_code")
        _code(area, "area_code")
    worker = Worker(
        _identifier(body["staff_id"], "staff_id"),
        _human_text(body["display_name"], "display_name", maximum=100, formula_safe=True),
        _parse_code_array(body["skill_codes"], "skill_codes"),
        eligibility,
        _positive_int(body["max_daily_minutes"], "max_daily_minutes"),
    )
    return _require_canonical(value, worker, "worker")  # type: ignore[return-value]


def _parse_availability(value: object) -> AvailabilityWindow:
    body = _exact_object(
        value, frozenset({item.name for item in fields(AvailabilityWindow)}), "availability"
    )
    item = AvailabilityWindow(
        _identifier(body["staff_id"], "staff_id"),
        _nonnegative_int(body["weekday"], "weekday"),
        _human_text(body["start_local"], "start_local", maximum=5),
        _human_text(body["end_local"], "end_local", maximum=5),
    )
    return _require_canonical(value, item, "availability")  # type: ignore[return-value]


def _parse_weekly_assignment(value: object) -> WeeklyAssignment:
    body = _exact_object(
        value,
        frozenset({item.name for item in fields(WeeklyAssignment)}),
        "weekly assignment",
    )
    item = WeeklyAssignment(
        _identifier(body["staff_id"], "staff_id"),
        _nonnegative_int(body["weekday"], "weekday"),
        _code(body["role_code"], "role_code"),
        _code(body["area_code"], "area_code"),
        _human_text(body["start_local"], "start_local", maximum=5),
        _human_text(body["end_local"], "end_local", maximum=5),
    )
    return _require_canonical(value, item, "weekly assignment")  # type: ignore[return-value]


def _parse_assignment_rule(value: object) -> AssignmentRule:
    body = _exact_object(
        value, frozenset({item.name for item in fields(AssignmentRule)}), "assignment rule"
    )
    item = AssignmentRule(
        _code(body["role_code"], "role_code"),
        _code(body["area_code"], "area_code"),
        _parse_code_array(body["required_skill_codes"], "required_skill_codes"),
    )
    return _require_canonical(value, item, "assignment rule")  # type: ignore[return-value]


def _parse_coverage_requirement(value: object) -> CoverageRequirement:
    body = _exact_object(
        value,
        frozenset({item.name for item in fields(CoverageRequirement)}),
        "coverage requirement",
    )
    item = CoverageRequirement(
        _nonnegative_int(body["weekday"], "weekday"),
        _code(body["role_code"], "role_code"),
        _code(body["area_code"], "area_code"),
        _human_text(body["start_local"], "start_local", maximum=5),
        _human_text(body["end_local"], "end_local", maximum=5),
        _positive_int(body["minimum_staff"], "minimum_staff"),
    )
    return _require_canonical(value, item, "coverage requirement")  # type: ignore[return-value]


def _parse_roster(value: object) -> RosterRevision:
    body = _exact_object(
        value, frozenset({item.name for item in fields(RosterRevision)}), "roster"
    )
    roster = RosterRevision(
        _identifier(body["site_id"], "site_id"),
        _identifier(body["deployment_id"], "deployment_id"),
        _human_text(body["site_timezone"], "site_timezone", maximum=128),
        _positive_int(body["revision"], "revision"),
        _canonical_date(body["effective_from"], "effective_from"),
        None
        if body["effective_until"] is None
        else _canonical_date(body["effective_until"], "effective_until"),
        tuple(_parse_worker(item) for item in _array(body["workers"], "workers")),
        tuple(
            _parse_availability(item)
            for item in _array(body["availability"], "availability")
        ),
        tuple(
            _parse_weekly_assignment(item)
            for item in _array(body["regular_assignments"], "regular_assignments")
        ),
        tuple(
            _parse_assignment_rule(item)
            for item in _array(body["assignment_rules"], "assignment_rules")
        ),
        tuple(
            _parse_coverage_requirement(item)
            for item in _array(body["coverage"], "coverage")
        ),
        _digest(body["roster_digest"], "roster_digest"),
    )
    return _require_canonical(value, roster, "roster")  # type: ignore[return-value]


def _parse_assignment(value: object) -> Assignment:
    body = _exact_object(
        value, frozenset({item.name for item in fields(Assignment)}), "assignment"
    )
    item = Assignment(
        _identifier(body["assignment_id"], "assignment_id"),
        _identifier(body["staff_id"], "staff_id"),
        _code(body["role_code"], "role_code"),
        _code(body["area_code"], "area_code"),
        _canonical_datetime(body["start_at"], "start_at"),
        _canonical_datetime(body["end_at"], "end_at"),
    )
    return _require_canonical(value, item, "assignment")  # type: ignore[return-value]


def _parse_coverage_window(value: object) -> CoverageWindow:
    body = _exact_object(
        value, frozenset({item.name for item in fields(CoverageWindow)}), "coverage window"
    )
    item = CoverageWindow(
        _code(body["role_code"], "role_code"),
        _code(body["area_code"], "area_code"),
        _canonical_datetime(body["start_at"], "start_at"),
        _canonical_datetime(body["end_at"], "end_at"),
        _positive_int(body["minimum_staff"], "minimum_staff"),
    )
    return _require_canonical(value, item, "coverage window")  # type: ignore[return-value]


def _parse_exception(value: object) -> StaffingException:
    body = _exact_object(
        value, frozenset({item.name for item in fields(StaffingException)}), "exception"
    )
    kind = body["kind"]
    if type(kind) is not str or kind not in {"LEAVE", "LATE", "EARLY_DEPARTURE", "UNAVAILABLE"}:
        raise _evidence_error("exception kind")
    item = StaffingException(
        _identifier(body["exception_id"], "exception_id"),
        _canonical_date(body["service_date"], "service_date"),
        _identifier(body["staff_id"], "staff_id"),
        kind,
        _canonical_datetime(body["unavailable_start"], "unavailable_start"),
        _canonical_datetime(body["unavailable_end"], "unavailable_end"),
        _optional_human_text(body["note"], "note", maximum=500),
    )
    try:
        exception_digest(item)
    except Exception:
        raise _evidence_error("exception") from None
    return _require_canonical(value, item, "exception")  # type: ignore[return-value]


def _parse_staffing_basis(value: object) -> StaffingBasis:
    body = _exact_object(
        value, frozenset({item.name for item in fields(StaffingBasis)}), "staffing basis"
    )
    basis = StaffingBasis(
        _canonical_date(body["service_date"], "service_date"),
        _positive_int(body["roster_revision"], "roster_revision"),
        _nonnegative_int(body["exception_set_revision"], "exception_set_revision"),
        _nonnegative_int(body["effective_plan_revision"], "effective_plan_revision"),
        _digest(body["roster_digest"], "roster_digest"),
        _digest(body["exception_set_digest"], "exception_set_digest"),
        _digest(body["effective_plan_digest"], "effective_plan_digest"),
        _digest(body["basis_digest"], "basis_digest"),
    )
    return _require_canonical(value, basis, "staffing basis")  # type: ignore[return-value]


def _parse_basis_snapshot(value: object) -> BasisSnapshot:
    body = _exact_object(
        value, frozenset({item.name for item in fields(BasisSnapshot)}), "basis snapshot"
    )
    snapshot = BasisSnapshot(
        _parse_staffing_basis(body["basis"]),
        _human_text(body["site_timezone"], "site_timezone", maximum=128),
        tuple(_parse_worker(item) for item in _array(body["workers"], "workers")),
        tuple(
            _parse_assignment(item) for item in _array(body["assignments"], "assignments")
        ),
        tuple(_parse_exception(item) for item in _array(body["exceptions"], "exceptions")),
        tuple(
            _parse_availability(item)
            for item in _array(body["availability"], "availability")
        ),
        tuple(
            _parse_assignment_rule(item)
            for item in _array(body["assignment_rules"], "assignment_rules")
        ),
        tuple(
            _parse_coverage_window(item)
            for item in _array(body["coverage"], "coverage")
        ),
        _human_text(
            body["prompt_template_version"], "prompt_template_version", maximum=64
        ),
    )
    try:
        _validate_snapshot_shape(snapshot)
    except Exception:
        raise _evidence_error("basis snapshot") from None
    return _require_canonical(value, snapshot, "basis snapshot")  # type: ignore[return-value]


def _parse_provider_worker(value: object) -> ProviderWorker:
    body = _exact_object(
        value, frozenset({item.name for item in fields(ProviderWorker)}), "provider worker"
    )
    eligibility = _parse_pair_array(body["eligibility"], "eligibility")
    for role, area in eligibility:
        _code(role, "role_code")
        _code(area, "area_code")
    item = ProviderWorker(
        _identifier(body["worker_alias"], "worker_alias"),
        _parse_code_array(body["skill_codes"], "skill_codes"),
        eligibility,
        _positive_int(body["max_daily_minutes"], "max_daily_minutes"),
    )
    return _require_canonical(value, item, "provider worker")  # type: ignore[return-value]


def _parse_provider_assignment(value: object) -> ProviderAssignment:
    body = _exact_object(
        value,
        frozenset({item.name for item in fields(ProviderAssignment)}),
        "provider assignment",
    )
    item = ProviderAssignment(
        _identifier(body["assignment_alias"], "assignment_alias"),
        _identifier(body["worker_alias"], "worker_alias"),
        _code(body["role_code"], "role_code"),
        _code(body["area_code"], "area_code"),
        _canonical_datetime(body["start_at"], "start_at"),
        _canonical_datetime(body["end_at"], "end_at"),
    )
    return _require_canonical(value, item, "provider assignment")  # type: ignore[return-value]


def _parse_provider_interval(value: object, kind: type) -> object:
    body = _exact_object(
        value, frozenset({item.name for item in fields(kind)}), "provider interval"
    )
    item = kind(
        _identifier(body["worker_alias"], "worker_alias"),
        _canonical_datetime(body["start_at"], "start_at"),
        _canonical_datetime(body["end_at"], "end_at"),
    )
    return _require_canonical(value, item, "provider interval")


def _parse_provider_coverage(value: object) -> ProviderCoverage:
    body = _exact_object(
        value, frozenset({item.name for item in fields(ProviderCoverage)}), "provider coverage"
    )
    item = ProviderCoverage(
        _code(body["role_code"], "role_code"),
        _code(body["area_code"], "area_code"),
        _canonical_datetime(body["start_at"], "start_at"),
        _canonical_datetime(body["end_at"], "end_at"),
        _positive_int(body["minimum_staff"], "minimum_staff"),
    )
    return _require_canonical(value, item, "provider coverage")  # type: ignore[return-value]


def _parse_provider_payload(value: object) -> ProviderPayload:
    body = _exact_object(
        value, frozenset({item.name for item in fields(ProviderPayload)}), "provider payload"
    )
    language = body["language"]
    if type(language) is not str or language not in {"zh-CN", "en"}:
        raise _evidence_error("language")
    payload = ProviderPayload(
        _digest(body["basis_digest"], "basis_digest"),
        tuple(
            _parse_provider_worker(item) for item in _array(body["workers"], "workers")
        ),
        tuple(
            _parse_provider_assignment(item)
            for item in _array(body["assignments"], "assignments")
        ),
        tuple(
            _parse_provider_interval(item, ProviderAvailability)
            for item in _array(body["availability"], "availability")
        ),
        tuple(
            _parse_provider_interval(item, ProviderUnavailable)
            for item in _array(body["unavailable"], "unavailable")
        ),
        tuple(
            _parse_provider_coverage(item)
            for item in _array(body["coverage"], "coverage")
        ),
        _human_text(
            body["prompt_template_version"], "prompt_template_version", maximum=64
        ),
        language,
    )
    return _require_canonical(value, payload, "provider payload")  # type: ignore[return-value]


def _parse_operation(value: object) -> RemoveOperation | AddOperation:
    if type(value) is not dict:
        raise _evidence_error("candidate operation")
    operation = value.get("operation")
    if operation == "REMOVE":
        body = _exact_object(
            value, frozenset({"operation", "assignment_alias"}), "REMOVE operation"
        )
        item: RemoveOperation | AddOperation = RemoveOperation(
            "REMOVE", _identifier(body["assignment_alias"], "assignment_alias")
        )
    elif operation == "ADD":
        body = _exact_object(
            value,
            frozenset(
                {"operation", "worker_alias", "role_code", "area_code", "start_at", "end_at"}
            ),
            "ADD operation",
        )
        item = AddOperation(
            "ADD",
            _identifier(body["worker_alias"], "worker_alias"),
            _code(body["role_code"], "role_code"),
            _code(body["area_code"], "area_code"),
            _canonical_datetime(body["start_at"], "start_at"),
            _canonical_datetime(body["end_at"], "end_at"),
        )
    else:
        raise _evidence_error("candidate operation")
    return _require_canonical(value, item, "candidate operation")  # type: ignore[return-value]


def _parse_coverage_gap(value: object) -> CoverageGap:
    body = _exact_object(
        value, frozenset({item.name for item in fields(CoverageGap)}), "coverage gap"
    )
    item = CoverageGap(
        _code(body["role_code"], "role_code"),
        _code(body["area_code"], "area_code"),
        _canonical_datetime(body["start_at"], "start_at"),
        _canonical_datetime(body["end_at"], "end_at"),
        _positive_int(body["required"], "required"),
        _nonnegative_int(body["actual"], "actual"),
    )
    return _require_canonical(value, item, "coverage gap")  # type: ignore[return-value]


def _parse_stored_candidate(value: object) -> StoredCandidate:
    body = _exact_object(
        value, frozenset({item.name for item in fields(StoredCandidate)}), "stored candidate"
    )
    operations = tuple(
        _parse_operation(item)
        for item in _array(body["operations"], "operations", maximum=32)
    )
    raw_offsets = _array(body["operation_offset_minutes"], "operation offsets")
    offsets: list[tuple[int | None, int | None]] = []
    for item in raw_offsets:
        if type(item) is not list or len(item) != 2:
            raise _evidence_error("operation offsets")
        start, end = item
        if start is None and end is None:
            offsets.append((None, None))
        elif (
            type(start) is int
            and type(end) is int
            and -1439 <= start <= 1439
            and -1439 <= end <= 1439
        ):
            offsets.append((start, end))
        else:
            raise _evidence_error("operation offsets")
    rationale = _human_text(body["rationale"], "rationale", minimum=0, maximum=280)
    warnings = tuple(
        _human_text(item, "operational_warnings", minimum=0, maximum=200)
        for item in _array(
            body["operational_warnings"], "operational_warnings", maximum=5
        )
    )
    rejection_codes = tuple(
        item
        for item in _array(body["rejection_codes"], "rejection_codes")
        if type(item) is str
    )
    if len(rejection_codes) != len(body["rejection_codes"]):
        raise _evidence_error("rejection_codes")
    gaps = tuple(
        _parse_coverage_gap(item)
        for item in _array(body["coverage_gaps"], "coverage_gaps")
    )
    schedule = (
        None
        if body["materialized_schedule"] is None
        else tuple(
            _parse_assignment(item)
            for item in _array(body["materialized_schedule"], "materialized_schedule")
        )
    )
    candidate = StoredCandidate(
        body["candidate_index"],
        operations,
        tuple(offsets),
        rationale,
        warnings,
        rejection_codes,
        gaps,
        schedule,
        _digest(
            body["materialized_schedule_digest"],
            "materialized_schedule_digest",
            optional=True,
        ),
    )
    try:
        CandidateValidation(
            candidate.candidate_index,
            not candidate.rejection_codes,
            candidate.rejection_codes,
            candidate.coverage_gaps,
            candidate.materialized_schedule,
            candidate.materialized_schedule_digest,
        )
        candidate_patch_for_revalidation(candidate)
    except Exception:
        raise _evidence_error("stored candidate") from None
    return _require_canonical(value, candidate, "stored candidate")  # type: ignore[return-value]


def _parse_route(value: object) -> GenerationRouteEvidence:
    try:
        route = parse_generation_route_evidence(value)
    except Exception:
        raise _evidence_error("generation route") from None
    return _require_canonical(value, route, "generation route")  # type: ignore[return-value]


def _parse_started(value: object) -> AttemptStartedEvidence:
    if type(value) is not dict:
        raise _evidence_error("attempt started")
    timeout = value.get("timeout_s")
    if type(timeout) is not float or not math.isfinite(timeout) or timeout <= 0:
        raise _evidence_error("timeout_s")
    try:
        result = parse_attempt_started_evidence(value)
    except Exception:
        raise _evidence_error("attempt started") from None
    return _require_canonical(value, result, "attempt started")  # type: ignore[return-value]


def _parse_finished(value: object) -> AttemptFinishedEvidence:
    try:
        result = parse_attempt_finished_evidence(value)
        _validate_replayable_attempt_shape(result)
    except Exception:
        raise _evidence_error("attempt finished") from None
    return _require_canonical(value, result, "attempt finished")  # type: ignore[return-value]


def _parse_result(value: object) -> ResultEvidence:
    try:
        result = parse_result_evidence(value)
        for attempt in result.attempts:
            _validate_replayable_attempt_shape(attempt)
        _validate_result_intrinsic(result)
    except Exception:
        raise _evidence_error("result evidence") from None
    return _require_canonical(value, result, "result evidence")  # type: ignore[return-value]


def _payload_body(value: object, kind: type, detail: str) -> dict[str, object]:
    return _exact_object(value, frozenset({item.name for item in fields(kind)}), detail)


def _parse_roster_payload(value: object) -> RosterImportedPayload:
    body = _payload_body(value, RosterImportedPayload, "roster payload")
    payload = RosterImportedPayload(
        _identifier(body["request_id"], "request_id"),
        _digest(body["request_digest"], "request_digest"),
        _positive_int(body["roster_revision"], "roster_revision"),
        _digest(body["roster_digest"], "roster_digest"),
        _parse_roster(body["roster"]),
        _human_text(body["operator"], "operator", maximum=128, formula_safe=True),
        _human_text(body["source_ref"], "source_ref", maximum=256, formula_safe=True),
    )
    if (
        payload.roster_revision != payload.roster.revision
        or payload.roster_digest != payload.roster.roster_digest
    ):
        raise _evidence_error("roster payload")
    return _require_canonical(value, payload, "roster payload")  # type: ignore[return-value]


def _parse_exception_recorded(value: object) -> ExceptionRecordedPayload:
    body = _payload_body(value, ExceptionRecordedPayload, "exception recorded payload")
    payload = ExceptionRecordedPayload(
        _identifier(body["request_id"], "request_id"),
        _digest(body["request_digest"], "request_digest"),
        _identifier(body["exception_id"], "exception_id"),
        _canonical_date(body["service_date"], "service_date"),
        _positive_int(body["exception_set_revision"], "exception_set_revision"),
        _digest(body["exception_digest"], "exception_digest"),
        _parse_exception(body["exception"]),
        _human_text(body["operator"], "operator", maximum=128, formula_safe=True),
    )
    if (
        payload.exception_id != payload.exception.exception_id
        or payload.service_date != payload.exception.service_date
        or payload.exception_digest != exception_digest(payload.exception)
    ):
        raise _evidence_error("exception recorded payload")
    return _require_canonical(value, payload, "exception recorded payload")  # type: ignore[return-value]


def _parse_exception_cancelled(value: object) -> ExceptionCancelledPayload:
    body = _payload_body(value, ExceptionCancelledPayload, "exception cancelled payload")
    payload = ExceptionCancelledPayload(
        _identifier(body["request_id"], "request_id"),
        _digest(body["request_digest"], "request_digest"),
        _identifier(body["exception_id"], "exception_id"),
        _positive_int(body["exception_set_revision"], "exception_set_revision"),
        _parse_exception(body["cancelled_exception"]),
        _human_text(body["operator"], "operator", maximum=128, formula_safe=True),
        _optional_human_text(body["note"], "note", maximum=500),
    )
    if payload.exception_id != payload.cancelled_exception.exception_id:
        raise _evidence_error("exception cancelled payload")
    return _require_canonical(value, payload, "exception cancelled payload")  # type: ignore[return-value]


def _parse_exception_corrected(value: object) -> ExceptionCorrectedPayload:
    body = _payload_body(value, ExceptionCorrectedPayload, "exception corrected payload")
    payload = ExceptionCorrectedPayload(
        _identifier(body["request_id"], "request_id"),
        _digest(body["request_digest"], "request_digest"),
        _identifier(body["exception_id"], "exception_id"),
        _parse_exception(body["replacement_exception"]),
        _positive_int(body["exception_set_revision"], "exception_set_revision"),
        _digest(body["exception_digest"], "exception_digest"),
        _parse_exception(body["previous_exception"]),
        _human_text(body["operator"], "operator", maximum=128, formula_safe=True),
    )
    if (
        payload.exception_id != payload.previous_exception.exception_id
        or payload.replacement_exception.exception_id != payload.exception_id
        or payload.replacement_exception.service_date
        != payload.previous_exception.service_date
        or payload.replacement_exception.staff_id != payload.previous_exception.staff_id
        or payload.exception_digest != exception_digest(payload.replacement_exception)
    ):
        raise _evidence_error("exception corrected payload")
    return _require_canonical(value, payload, "exception corrected payload")  # type: ignore[return-value]


def _parse_generation_reserved(value: object) -> GenerationReservedPayload:
    body = _payload_body(value, GenerationReservedPayload, "generation reservation")
    language = body["language"]
    if type(language) is not str or language not in {"zh-CN", "en"}:
        raise _evidence_error("language")
    retry_of = body["retry_of"]
    if retry_of is not None:
        retry_of = _identifier(retry_of, "retry_of")
    payload = GenerationReservedPayload(
        _identifier(body["request_id"], "request_id"),
        _digest(body["request_digest"], "request_digest"),
        _identifier(body["generation_id"], "generation_id"),
        _canonical_date(body["service_date"], "service_date"),
        _parse_basis_snapshot(body["basis_snapshot"]),
        _parse_provider_payload(body["provider_payload"]),
        _digest(body["alias_nonce_digest"], "alias_nonce_digest"),
        _parse_pair_array(body["worker_alias_to_staff_id"], "worker alias map"),
        _parse_pair_array(
            body["assignment_alias_to_assignment_id"], "assignment alias map"
        ),
        _digest(body["input_digest"], "input_digest"),
        _human_text(
            body["prompt_template_version"], "prompt_template_version", maximum=64
        ),
        language,
        _parse_route(body["route"]),
        retry_of,
        _human_text(body["operator"], "operator", maximum=128, formula_safe=True),
    )
    if payload.service_date != payload.basis_snapshot.basis.service_date:
        raise _evidence_error("reservation service_date")
    try:
        validate_generation_projection(
            GenerationProjection(
                payload.basis_snapshot,
                payload.provider_payload,
                payload.worker_alias_to_staff_id,
                payload.assignment_alias_to_assignment_id,
                payload.alias_nonce_digest,
                payload.input_digest,
            ),
            outer_prompt_template_version=payload.prompt_template_version,
            outer_language=payload.language,
        )
        _validate_generation_route(payload.route)
    except Exception:
        raise _evidence_error("generation projection") from None
    return _require_canonical(value, payload, "generation reservation")  # type: ignore[return-value]


def _parse_generation_interrupted(value: object) -> GenerationInterruptedPayload:
    body = _payload_body(value, GenerationInterruptedPayload, "generation interruption")
    if body["reason"] != "RESULT_UNKNOWN":
        raise _evidence_error("interruption reason")
    payload = GenerationInterruptedPayload(
        _digest(body["request_digest"], "request_digest"),
        _identifier(body["generation_id"], "generation_id"),
        "RESULT_UNKNOWN",
        _canonical_datetime(body["interrupted_at_utc"], "interrupted_at_utc"),
    )
    return _require_canonical(value, payload, "generation interruption")  # type: ignore[return-value]


def _parse_attempt_started_payload(value: object) -> ProviderAttemptStartedPayload:
    body = _payload_body(value, ProviderAttemptStartedPayload, "attempt started payload")
    payload = ProviderAttemptStartedPayload(
        _digest(body["request_digest"], "request_digest"),
        _identifier(body["generation_id"], "generation_id"),
        _parse_started(body["evidence"]),
    )
    if payload.evidence.request_id != payload.generation_id:
        raise _evidence_error("attempt started identity")
    return _require_canonical(value, payload, "attempt started payload")  # type: ignore[return-value]


def _parse_attempt_finished_payload(value: object) -> ProviderAttemptFinishedPayload:
    body = _payload_body(value, ProviderAttemptFinishedPayload, "attempt finished payload")
    payload = ProviderAttemptFinishedPayload(
        _digest(body["request_digest"], "request_digest"),
        _identifier(body["generation_id"], "generation_id"),
        _parse_finished(body["evidence"]),
    )
    if payload.evidence.request_id != payload.generation_id:
        raise _evidence_error("attempt finished identity")
    return _require_canonical(value, payload, "attempt finished payload")  # type: ignore[return-value]


def _parse_suggestion_issued(value: object) -> SuggestionIssuedPayload:
    body = _payload_body(value, SuggestionIssuedPayload, "suggestion issued payload")
    payload = SuggestionIssuedPayload(
        _digest(body["request_digest"], "request_digest"),
        _identifier(body["generation_id"], "generation_id"),
        _parse_result(body["result"]),
        tuple(
            _parse_stored_candidate(item)
            for item in _array(body["candidates"], "candidates", maximum=2)
        ),
        _digest(body["candidate_set_digest"], "candidate_set_digest"),
    )
    if payload.result.request_id != payload.generation_id:
        raise _evidence_error("suggestion result identity")
    _validate_terminal_candidate_shape(payload)
    return _require_canonical(value, payload, "suggestion issued payload")  # type: ignore[return-value]


def _parse_suggestion_unavailable(value: object) -> SuggestionUnavailablePayload:
    body = _payload_body(value, SuggestionUnavailablePayload, "suggestion unavailable payload")
    terminal = body["terminal_state"]
    if type(terminal) is not str or terminal not in GENERATION_TERMINALS - {
        "SUCCEEDED",
        "RESULT_UNKNOWN",
    }:
        raise _evidence_error("terminal_state")
    failure = body["failure_code"]
    if failure is not None and type(failure) is not str:
        raise _evidence_error("failure_code")
    payload = SuggestionUnavailablePayload(
        _digest(body["request_digest"], "request_digest"),
        _identifier(body["generation_id"], "generation_id"),
        _parse_result(body["result"]),
        tuple(
            _parse_stored_candidate(item)
            for item in _array(body["candidates"], "candidates", maximum=2)
        ),
        _digest(body["candidate_set_digest"], "candidate_set_digest", optional=True),
        terminal,
        failure,
    )
    if payload.result.request_id != payload.generation_id:
        raise _evidence_error("suggestion result identity")
    _validate_unavailable_failure_pair(payload)
    _validate_terminal_candidate_shape(payload)
    return _require_canonical(value, payload, "suggestion unavailable payload")  # type: ignore[return-value]


def _parse_manager_response(value: object) -> ManagerResponseCommittedPayload:
    body = _payload_body(value, ManagerResponseCommittedPayload, "manager response payload")
    decision = body["decision"]
    reason = body["reason_code"]
    if type(decision) is not str or decision not in {"ACCEPT", "MODIFY", "REJECT"}:
        raise _evidence_error("manager decision")
    if type(reason) is not str or reason not in MANAGER_REASON_CODES:
        raise _evidence_error("manager reason")
    schedule = (
        None
        if body["effective_schedule"] is None
        else tuple(
            _parse_assignment(item)
            for item in _array(body["effective_schedule"], "effective_schedule")
        )
    )
    revision = body["effective_plan_revision"]
    if revision is not None:
        revision = _positive_int(revision, "effective_plan_revision")
    payload = ManagerResponseCommittedPayload(
        _identifier(body["request_id"], "request_id"),
        _digest(body["request_digest"], "request_digest"),
        _identifier(body["generation_id"], "generation_id"),
        decision,
        reason,
        schedule,
        _digest(body["schedule_digest"], "schedule_digest", optional=True),
        revision,
        _human_text(body["operator"], "operator", maximum=128, formula_safe=True),
        _optional_human_text(body["note"], "note", maximum=500),
        _parse_basis_snapshot(body["basis_snapshot"]),
    )
    _validate_manager_payload(payload)
    return _require_canonical(value, payload, "manager response payload")  # type: ignore[return-value]


EVENT_PAYLOAD_PARSERS: dict[str, Callable[[object], object]] = {
    "roster_imported": _parse_roster_payload,
    "exception_recorded": _parse_exception_recorded,
    "exception_cancelled": _parse_exception_cancelled,
    "exception_corrected": _parse_exception_corrected,
    "generation_reserved": _parse_generation_reserved,
    "generation_interrupted": _parse_generation_interrupted,
    "provider_attempt_started": _parse_attempt_started_payload,
    "provider_attempt_finished": _parse_attempt_finished_payload,
    "suggestion_issued": _parse_suggestion_issued,
    "suggestion_unavailable": _parse_suggestion_unavailable,
    "manager_response_committed": _parse_manager_response,
}


def staffing_generation_id(
    site_id: str, deployment_id: str, request_id: str, request_digest: str
) -> str:
    try:
        _identifier(site_id, "site_id")
        _identifier(deployment_id, "deployment_id")
        _identifier(request_id, "request_id")
        _digest(request_digest, "request_digest")
        return "generation_" + stable_digest(
            {
                "schema": "nxt-staffing-generation-id/v1",
                "site_id": site_id,
                "deployment_id": deployment_id,
                "request_id": request_id,
                "request_digest": request_digest,
            }
        )[:24]
    except Exception:
        raise _evidence_error("generation identity") from None


def staffing_event_id(
    event_type: str,
    sequence: int,
    site_id: str,
    deployment_id: str,
    occurred_at_utc: datetime,
    causation_id: str | None,
    payload: object,
) -> str:
    try:
        if type(event_type) is not str or event_type not in EVENT_TYPES:
            raise _event_error("event_type")
        if type(sequence) is not int or sequence < 1:
            raise _event_error("sequence")
        _identifier(site_id, "site_id")
        _identifier(deployment_id, "deployment_id")
        if causation_id is not None:
            _identifier(causation_id, "causation_id")
        if type(occurred_at_utc) is not datetime or occurred_at_utc.tzinfo is None:
            raise _event_error("occurred_at_utc")
        if occurred_at_utc.utcoffset() != timedelta(0):
            raise _event_error("occurred_at_utc")
        timestamp = utc_text(occurred_at_utc)
        return "staffing_event_" + stable_digest(
            {
                "schema": "nxt-staffing-event-id/v1",
                "event_type": event_type,
                "sequence": sequence,
                "site_id": site_id,
                "deployment_id": deployment_id,
                "occurred_at_utc": timestamp,
                "causation_id": causation_id,
                "payload": payload,
            }
        )[:24]
    except StaffingError as error:
        if error.code == "staffing_invalid_event":
            raise
        raise _event_error("event identity") from None
    except Exception:
        raise _event_error("event identity") from None


def _expected_causation(event_type: str, payload: object) -> str | None:
    if event_type in {"roster_imported", "exception_recorded", "generation_reserved"}:
        return None
    if event_type in {"exception_cancelled", "exception_corrected"}:
        return getattr(payload, "exception_id", None)
    return getattr(payload, "generation_id", None)


def _validate_interruption_timestamp(interrupted: object, occurred: object) -> None:
    try:
        if (
            type(interrupted) is not datetime
            or interrupted.tzinfo is None
            or type(occurred) is not datetime
            or occurred.tzinfo is None
        ):
            raise _event_error("interrupted_at_utc")
        interrupted_offset = interrupted.utcoffset()
        occurred_offset = occurred.utcoffset()
        if (
            type(interrupted_offset) is not timedelta
            or interrupted_offset.days != 0
            or interrupted_offset.seconds != 0
            or interrupted_offset.microseconds != 0
            or type(occurred_offset) is not timedelta
            or occurred_offset.days != 0
            or occurred_offset.seconds != 0
            or occurred_offset.microseconds != 0
            or (
                interrupted.year,
                interrupted.month,
                interrupted.day,
                interrupted.hour,
                interrupted.minute,
                interrupted.second,
                interrupted.microsecond,
            )
            != (
                occurred.year,
                occurred.month,
                occurred.day,
                occurred.hour,
                occurred.minute,
                occurred.second,
                occurred.microsecond,
            )
        ):
            raise _event_error("interrupted_at_utc")
    except StaffingError:
        raise
    except Exception:
        raise _event_error("interrupted_at_utc") from None


def event_from_record(body: object, payload: object) -> StaffingEvent:
    try:
        record = _exact_object(body, EVENT_RECORD_KEYS, "event fields")
        event_type = record["event_type"]
        if type(event_type) is not str or event_type not in EVENT_TYPES:
            raise _event_error("event_type")
        expected_type = EVENT_PAYLOAD_TYPES[event_type]
        if type(payload) is not expected_type:
            raise _event_error("payload type")
        if not _same_value(record["payload"], payload):
            raise _event_error("payload value")
        sequence = record["sequence"]
        if type(sequence) is not int or sequence < 1:
            raise _event_error("sequence")
        site_id = record["site_id"]
        deployment_id = record["deployment_id"]
        if type(site_id) is not str or IDENTIFIER_PATTERN.fullmatch(site_id) is None:
            raise _event_error("site_id")
        if (
            type(deployment_id) is not str
            or IDENTIFIER_PATTERN.fullmatch(deployment_id) is None
        ):
            raise _event_error("deployment_id")
        occurred = record["occurred_at_utc"]
        if type(occurred) is str:
            try:
                occurred = _canonical_datetime(occurred, "occurred_at_utc")
            except StaffingError:
                raise _event_error("occurred_at_utc") from None
        if (
            type(occurred) is not datetime
            or occurred.tzinfo is None
            or occurred.utcoffset() != timedelta(0)
        ):
            raise _event_error("occurred_at_utc")
        cause = record["causation_id"]
        if cause is not None and (
            type(cause) is not str or IDENTIFIER_PATTERN.fullmatch(cause) is None
        ):
            raise _event_error("causation_id")
        if cause != _expected_causation(event_type, payload):
            raise _event_error("causation_id")
        if event_type == "generation_interrupted":
            _validate_interruption_timestamp(payload.interrupted_at_utc, occurred)
        expected_id = staffing_event_id(
            event_type, sequence, site_id, deployment_id, occurred, cause, payload
        )
        if type(record["event_id"]) is not str or record["event_id"] != expected_id:
            raise _event_error("event_id")
        return StaffingEvent(
            event_type,
            expected_id,
            sequence,
            site_id,
            deployment_id,
            occurred,
            cause,
            payload,
        )
    except StaffingError as error:
        if error.code == "staffing_invalid_event":
            raise
        raise _event_error("event record") from None
    except Exception:
        raise _event_error("event record") from None


def parse_event(value: object, *, site_id: str, deployment_id: str) -> StaffingEvent:
    try:
        detached = scan_and_detach_event_tree(value)
    except StaffingError:
        raise
    except Exception:
        raise _event_error("event tree") from None
    try:
        body = _exact_object(detached, EVENT_RECORD_KEYS, "event fields")
    except StaffingError:
        raise _event_error("event fields") from None
    event_type = body.get("event_type")
    if type(event_type) is not str or event_type not in EVENT_TYPES:
        raise _event_error("event_type")
    if type(body.get("event_id")) is not str or IDENTIFIER_PATTERN.fullmatch(
        body["event_id"]
    ) is None:
        raise _event_error("event_id")
    if type(body.get("sequence")) is not int or body["sequence"] < 1:
        raise _event_error("sequence")
    if body.get("site_id") != site_id or body.get("deployment_id") != deployment_id:
        if (
            type(body.get("site_id")) is str
            and IDENTIFIER_PATTERN.fullmatch(body["site_id"]) is not None
            and type(body.get("deployment_id")) is str
            and IDENTIFIER_PATTERN.fullmatch(body["deployment_id"]) is not None
        ):
            raise _identity_error()
        raise _event_error("event identity")
    try:
        body["occurred_at_utc"] = _canonical_datetime(
            body["occurred_at_utc"], "occurred_at_utc"
        )
    except StaffingError:
        raise _event_error("occurred_at_utc") from None
    cause = body.get("causation_id")
    if cause is not None and (
        type(cause) is not str or IDENTIFIER_PATTERN.fullmatch(cause) is None
    ):
        raise _event_error("causation_id")
    try:
        payload = EVENT_PAYLOAD_PARSERS[event_type](body["payload"])
    except StaffingError:
        raise _evidence_error("event payload") from None
    except Exception:
        raise _evidence_error("event payload") from None
    return event_from_record(body, payload)


def _offset_minutes(value: datetime) -> int:
    try:
        offset = value.utcoffset()
        if offset is None or offset.microseconds != 0:
            raise ValueError
        seconds = offset.days * 86_400 + offset.seconds
        if seconds % 60:
            raise ValueError
        minutes = seconds // 60
        if not -1439 <= minutes <= 1439:
            raise ValueError
        return minutes
    except Exception:
        raise _evidence_error("candidate timestamp offset") from None


def stored_candidate_from_validation(
    candidate: CandidatePatch, validation: CandidateValidation
) -> StoredCandidate:
    try:
        if type(candidate) is not CandidatePatch or type(validation) is not CandidateValidation:
            raise ValueError
        if candidate.candidate_index != validation.candidate_index:
            raise ValueError
        offsets: list[tuple[int | None, int | None]] = []
        for operation in candidate.operations:
            if type(operation) is RemoveOperation:
                offsets.append((None, None))
            elif type(operation) is AddOperation:
                offsets.append(
                    (_offset_minutes(operation.start_at), _offset_minutes(operation.end_at))
                )
            else:
                raise ValueError
        return StoredCandidate(
            candidate.candidate_index,
            tuple(candidate.operations),
            tuple(offsets),
            candidate.rationale,
            tuple(candidate.operational_warnings),
            tuple(validation.rejection_codes),
            tuple(validation.coverage_gaps),
            validation.materialized_schedule,
            validation.materialized_schedule_digest,
        )
    except Exception:
        raise _evidence_error("stored candidate") from None


def candidate_patch_for_revalidation(candidate: StoredCandidate) -> CandidatePatch:
    try:
        if type(candidate) is not StoredCandidate:
            raise ValueError
        operations: list[RemoveOperation | AddOperation] = []
        if (
            len(candidate.operations) > 32
            or len(candidate.operations) != len(candidate.operation_offset_minutes)
        ):
            raise ValueError
        for operation, offsets in zip(
            candidate.operations, candidate.operation_offset_minutes
        ):
            if type(operation) is RemoveOperation and offsets == (None, None):
                operations.append(RemoveOperation("REMOVE", operation.assignment_alias))
                continue
            if (
                type(operation) is not AddOperation
                or type(offsets) is not tuple
                or len(offsets) != 2
                or any(
                    type(item) is not int or not -1439 <= item <= 1439
                    for item in offsets
                )
            ):
                raise ValueError
            start_zone = timezone(timedelta(minutes=offsets[0]))
            end_zone = timezone(timedelta(minutes=offsets[1]))
            start = operation.start_at.astimezone(timezone.utc).astimezone(start_zone)
            end = operation.end_at.astimezone(timezone.utc).astimezone(end_zone)
            operations.append(
                AddOperation(
                    "ADD",
                    operation.worker_alias,
                    operation.role_code,
                    operation.area_code,
                    start,
                    end,
                )
            )
        return CandidatePatch(
            candidate.candidate_index,
            tuple(operations),
            candidate.rationale,
            tuple(candidate.operational_warnings),
        )
    except Exception:
        raise _evidence_error("stored candidate") from None


def _validate_replayable_attempt_shape(
    evidence: AttemptFinishedEvidence,
) -> AttemptFinishedEvidence:
    if type(evidence) is not AttemptFinishedEvidence:
        raise _evidence_error("attempt finished")
    if evidence.status == "SUCCEEDED":
        finish_reasons = {
            "KIMI": "stop",
            "OPENAI": "completed",
            "ANTHROPIC": "tool_use",
        }
        if (
            evidence.failure_code is not None
            or evidence.retryable
            or evidence.security_failure
            or type(evidence.output_digest) is not str
            or HEX_DIGEST_PATTERN.fullmatch(evidence.output_digest) is None
            or evidence.finish_reason != finish_reasons[evidence.route.provider]
            or ((evidence.input_tokens is None) != (evidence.output_tokens is None))
        ):
            raise _evidence_error("successful attempt")
        return evidence
    code = evidence.failure_code
    if type(code) is not str or code not in GATEWAY_FAILURE_CODES or code in LOCAL_FAILURE_CODES:
        raise _evidence_error("attempt failure code")
    disposition: tuple[str, bool, bool]
    if code in FALLBACK_ELIGIBLE_CODES:
        disposition = ("UNAVAILABLE", True, False)
    elif code == "DEADLINE_EXHAUSTED":
        disposition = ("UNAVAILABLE", False, False)
    elif code == "PROVIDER_REFUSED":
        disposition = ("REFUSED", False, False)
    elif code in {"MALFORMED_PROVIDER_RESPONSE", "SCHEMA_MISMATCH"}:
        disposition = ("INVALID_RESPONSE", False, False)
    elif code in {
        "AUTHENTICATION_FAILED",
        "PERMISSION_DENIED",
        "MODEL_NOT_FOUND",
        "INVALID_PROVIDER_REQUEST",
    }:
        disposition = ("CONFIGURATION_ERROR", False, False)
    elif code in {
        "RESPONSE_TOO_LARGE",
        "UNSUPPORTED_CONTENT_ENCODING",
        "REDIRECT_REFUSED",
        "ENDPOINT_NOT_ALLOWED",
        "TLS_VERIFICATION_FAILED",
    }:
        disposition = ("SECURITY_ERROR", False, True)
    elif code == "PROVIDER_CLIENT_ERROR":
        disposition = ("PROVIDER_ERROR", False, False)
    else:
        raise _evidence_error("attempt failure code")
    if (
        (evidence.status, evidence.retryable, evidence.security_failure) != disposition
        or any(
            item is not None
            for item in (
                evidence.provider_request_id,
                evidence.finish_reason,
                evidence.output_digest,
                evidence.input_tokens,
                evidence.output_tokens,
            )
        )
    ):
        raise _evidence_error("attempt disposition")
    return evidence


def _validate_result_intrinsic(result: ResultEvidence) -> None:
    if type(result) is not ResultEvidence or result.bounded_summary is not None:
        raise _evidence_error("result evidence")
    for index, attempt in enumerate(result.attempts):
        _validate_replayable_attempt_shape(attempt)
        if attempt.request_id != result.request_id or attempt.attempt_index != index:
            raise _evidence_error("result attempts")
    if result.selected_provider is None:
        if (
            result.selected_model_id is not None
            or result.provider_request_id is not None
            or result.finish_reason is not None
            or result.output_digest is not None
            or result.status == "SUCCEEDED"
            or result.candidate_count != 0
            or result.failure_code not in GATEWAY_FAILURE_CODES
        ):
            raise _evidence_error("local result")
        return
    if not result.attempts:
        raise _evidence_error("selected result")
    final = result.attempts[-1]
    if (
        result.selected_provider != final.route.provider
        or result.selected_model_id != final.route.model_id
        or result.status != final.status
        or result.failure_code != final.failure_code
        or result.provider_request_id != final.provider_request_id
        or result.finish_reason != final.finish_reason
        or result.output_digest != final.output_digest
    ):
        raise _evidence_error("selected result")
    if result.status == "SUCCEEDED":
        if result.failure_code is not None or result.output_digest is None:
            raise _evidence_error("successful result")
    elif result.candidate_count != 0:
        raise _evidence_error("failed candidate count")


def _validate_generation_route(route: GenerationRouteEvidence) -> None:
    if type(route) is not GenerationRouteEvidence:
        raise _evidence_error("generation route")
    if route.region == "CN":
        if (
            route.readiness not in {"READY", "UNAVAILABLE"}
            or route.primary_provider != "KIMI"
            or route.primary_model_id is None
            or route.backup_provider is not None
            or route.backup_model_id is not None
        ):
            raise _evidence_error("generation route")
        return
    if (
        route.region != "GLOBAL"
        or route.primary_provider != "OPENAI"
        or route.primary_model_id is None
        or ((route.backup_provider is None) != (route.backup_model_id is None))
        or route.backup_provider not in {None, "ANTHROPIC"}
    ):
        raise _evidence_error("generation route")
    has_backup = route.backup_provider == "ANTHROPIC"
    if (
        (route.readiness == "READY" and not has_backup)
        or (route.readiness == "DEGRADED_BACKUP_UNCONFIGURED" and has_backup)
        or route.readiness not in {
            "READY",
            "DEGRADED_BACKUP_UNCONFIGURED",
            "UNAVAILABLE",
        }
    ):
        raise _evidence_error("generation route")


def _expected_attempt_route(
    reservation: GenerationReservedPayload, index: int
) -> tuple[AttemptRouteEvidence, float]:
    route = reservation.route
    _validate_generation_route(route)
    if route.readiness == "UNAVAILABLE":
        raise _evidence_error("unavailable route")
    if index == 0:
        provider = "KIMI" if route.region == "CN" else "OPENAI"
        model = route.primary_model_id
        cap = 15.0 if route.region == "CN" else 12.0
        role = "PRIMARY"
    elif index == 1 and route.region == "GLOBAL" and route.readiness == "READY":
        provider = "ANTHROPIC"
        model = route.backup_model_id
        cap = 8.0
        role = "BACKUP"
    else:
        raise _evidence_error("attempt index")
    if model is None:
        raise _evidence_error("attempt model")
    return (
        AttemptRouteEvidence(
            provider,
            route.region,
            role,
            f"{route.region.lower()}-{provider.lower()}-v1",
            model,
        ),
        cap,
    )


def _same_value(left: object, right: object) -> bool:
    try:
        return _canonical_bytes(left) == _canonical_bytes(right)
    except StaffingError:
        return False


def _generation_attempt_evidence(
    history: StaffingHistory, generation_id: str
) -> tuple[tuple[AttemptStartedEvidence, ...], tuple[AttemptFinishedEvidence, ...]]:
    starts: list[AttemptStartedEvidence] = []
    finishes: list[AttemptFinishedEvidence] = []
    for event in history.events:
        payload = event.payload
        if getattr(payload, "generation_id", None) != generation_id:
            continue
        if event.event_type == "provider_attempt_started":
            if type(payload) is not ProviderAttemptStartedPayload:
                raise _evidence_error("attempt payload")
            starts.append(payload.evidence)
        elif event.event_type == "provider_attempt_finished":
            if type(payload) is not ProviderAttemptFinishedPayload:
                raise _evidence_error("attempt payload")
            finishes.append(payload.evidence)
    return tuple(starts), tuple(finishes)


def _validate_attempt_pairing(
    reservation: GenerationReservedPayload,
    starts: tuple[AttemptStartedEvidence, ...],
    finishes: tuple[AttemptFinishedEvidence, ...],
    *,
    require_complete: bool,
) -> None:
    if require_complete and len(starts) != len(finishes):
        raise _evidence_error("unfinished attempt")
    if len(finishes) > len(starts) or len(starts) > len(finishes) + 1:
        raise _evidence_error("attempt order")
    for index, start in enumerate(starts):
        expected_route, cap = _expected_attempt_route(reservation, index)
        if (
            type(start) is not AttemptStartedEvidence
            or type(start.timeout_s) is not float
            or not math.isfinite(start.timeout_s)
            or not 0 < start.timeout_s <= cap
            or start.attempt_index != index
            or start.request_id != reservation.generation_id
            or start.input_digest != reservation.input_digest
            or not _same_value(start.route, expected_route)
        ):
            raise _evidence_error("attempt start")
    for index, finish in enumerate(finishes):
        _validate_replayable_attempt_shape(finish)
        start = starts[index]
        if (
            finish.attempt_index != index
            or finish.request_id != reservation.generation_id
            or not _same_value(finish.route, start.route)
        ):
            raise _evidence_error("attempt finish")


def _validate_selected_result(
    reservation: GenerationReservedPayload, result: ResultEvidence
) -> None:
    attempts = result.attempts
    if result.selected_provider is not None:
        if not attempts:
            raise _evidence_error("selected result")
        final = attempts[-1]
        if (
            result.selected_provider != final.route.provider
            or result.selected_model_id != final.route.model_id
            or result.status != final.status
            or result.failure_code != final.failure_code
            or result.provider_request_id != final.provider_request_id
            or result.finish_reason != final.finish_reason
            or result.output_digest != final.output_digest
        ):
            raise _evidence_error("selected result")
        if result.selected_provider == "KIMI":
            valid = (
                reservation.route.region == "CN"
                and reservation.route.readiness == "READY"
                and len(attempts) == 1
            )
        elif result.selected_provider == "OPENAI":
            valid = (
                reservation.route.region == "GLOBAL"
                and reservation.route.readiness
                in {"READY", "DEGRADED_BACKUP_UNCONFIGURED"}
                and len(attempts) == 1
                and final.failure_code not in FALLBACK_ELIGIBLE_CODES
            )
        else:
            valid = (
                result.selected_provider == "ANTHROPIC"
                and reservation.route.region == "GLOBAL"
                and reservation.route.readiness == "READY"
                and len(attempts) == 2
                and attempts[0].failure_code in FALLBACK_ELIGIBLE_CODES
            )
        if not valid:
            raise _evidence_error("selected route")
        if result.status == "SUCCEEDED":
            if result.failure_code is not None or result.output_digest is None:
                raise _evidence_error("successful result")
        elif result.candidate_count != 0:
            raise _evidence_error("failed candidate count")
        return

    if any(
        item is not None
        for item in (
            result.selected_model_id,
            result.provider_request_id,
            result.finish_reason,
            result.output_digest,
        )
    ) or result.status == "SUCCEEDED" or result.candidate_count != 0:
        raise _evidence_error("local result")
    attempts_count = len(attempts)
    if attempts_count == 1:
        first = attempts[0]
        if (
            reservation.route.region != "GLOBAL"
            or first.route.provider != "OPENAI"
            or first.failure_code not in FALLBACK_ELIGIBLE_CODES
        ):
            raise _evidence_error("local result attempt")
    elif attempts_count != 0:
        raise _evidence_error("local result attempts")
    cell = (result.status, result.failure_code, attempts_count)
    route = reservation.route
    allowed = False
    if cell == ("CONFIGURATION_ERROR", "PROVIDER_UNCONFIGURED", 0):
        allowed = route.readiness == "UNAVAILABLE"
    elif cell == ("CONFIGURATION_ERROR", "INPUT_TOO_LARGE", 0):
        allowed = route.readiness in {"READY", "DEGRADED_BACKUP_UNCONFIGURED"}
    elif cell == ("CONFIGURATION_ERROR", "INPUT_TOO_LARGE", 1):
        allowed = route.region == "GLOBAL" and route.readiness == "READY"
    elif cell == ("UNAVAILABLE", "DEADLINE_EXHAUSTED", 0):
        allowed = route.readiness in {"READY", "DEGRADED_BACKUP_UNCONFIGURED"}
    elif cell == ("UNAVAILABLE", "DEADLINE_EXHAUSTED", 1):
        allowed = route.region == "GLOBAL" and route.readiness in {
            "READY",
            "DEGRADED_BACKUP_UNCONFIGURED",
        }
    elif cell == ("UNAVAILABLE", "BACKUP_UNCONFIGURED", 1):
        allowed = (
            route.region == "GLOBAL"
            and route.readiness == "DEGRADED_BACKUP_UNCONFIGURED"
            and route.backup_model_id is None
        )
    if not allowed:
        raise _evidence_error("local result")


def validate_replayable_result_evidence(
    history: StaffingHistory,
    reservation: GenerationReservedPayload,
    result: ResultEvidence,
    generation_id: str,
    terminal_state: str | None,
    failure_code: str | None,
) -> ResultEvidence:
    try:
        if (
            type(history) is not StaffingHistory
            or type(reservation) is not GenerationReservedPayload
            or type(result) is not ResultEvidence
            or generation_id != reservation.generation_id
            or result.request_id != generation_id
            or result.input_digest != reservation.input_digest
            or result.bounded_summary is not None
        ):
            raise _evidence_error("result identity")
        try:
            persisted_reservation = history.reservation(generation_id)
        except StaffingError:
            raise _evidence_error("result reservation") from None
        if not _same_value(persisted_reservation, reservation):
            raise _evidence_error("result reservation")
        _validate_result_intrinsic(result)
        _validate_generation_route(reservation.route)
        starts, finishes = _generation_attempt_evidence(history, generation_id)
        _validate_attempt_pairing(reservation, starts, finishes, require_complete=True)
        if len(result.attempts) != len(finishes) or any(
            not _same_value(left, right)
            for left, right in zip(result.attempts, finishes)
        ):
            raise _evidence_error("result attempts")
        _validate_selected_result(reservation, result)
        if result.status == "SUCCEEDED":
            if terminal_state not in {"SUCCEEDED", "NO_VALID_SUGGESTION", "INVALID_RESPONSE"}:
                raise _evidence_error("terminal state")
            if terminal_state == "SUCCEEDED" and failure_code is not None:
                raise _evidence_error("terminal failure")
            if terminal_state == "NO_VALID_SUGGESTION" and failure_code != "NO_VALID_SUGGESTION":
                raise _evidence_error("terminal failure")
            if terminal_state == "INVALID_RESPONSE" and failure_code not in PROVIDER_WIRE_FAILURE_CODES:
                raise _evidence_error("terminal failure")
        elif (
            result.status == "RESULT_UNKNOWN"
            or terminal_state != result.status
            or failure_code != result.failure_code
            or failure_code not in GATEWAY_FAILURE_CODES
        ):
            raise _evidence_error("terminal result")
        return result
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error("result evidence") from None


def _validate_unavailable_failure_pair(payload: SuggestionUnavailablePayload) -> None:
    result = payload.result
    if type(result) is not ResultEvidence:
        raise _evidence_error("result evidence")
    if result.status == "SUCCEEDED":
        if payload.terminal_state == "INVALID_RESPONSE":
            if payload.failure_code not in PROVIDER_WIRE_FAILURE_CODES:
                raise _evidence_error("provider failure code")
        elif payload.terminal_state == "NO_VALID_SUGGESTION":
            if payload.failure_code != "NO_VALID_SUGGESTION":
                raise _evidence_error("no-valid failure code")
        else:
            raise _evidence_error("successful unavailable result")
    elif (
        payload.terminal_state != result.status
        or payload.failure_code != result.failure_code
        or payload.failure_code not in GATEWAY_FAILURE_CODES
    ):
        raise _evidence_error("gateway failure pair")


def _validate_terminal_candidate_shape(
    payload: SuggestionIssuedPayload | SuggestionUnavailablePayload,
) -> None:
    candidates = payload.candidates
    if type(candidates) is not tuple or len(candidates) > 2:
        raise _evidence_error("candidates")
    indexes = tuple(
        candidate.candidate_index
        for candidate in candidates
        if type(candidate) is StoredCandidate
    )
    if len(indexes) != len(candidates) or indexes not in ((), (1,), (1, 2)):
        raise _evidence_error("candidate indexes")
    validity = tuple(not candidate.rejection_codes for candidate in candidates)
    if type(payload) is SuggestionIssuedPayload:
        if (
            payload.result.status != "SUCCEEDED"
            or len(candidates) != payload.result.candidate_count
            or not any(validity)
            or payload.candidate_set_digest
            != stable_digest(to_primitive(candidates))
        ):
            raise _evidence_error("suggestion candidates")
        return
    if payload.terminal_state == "NO_VALID_SUGGESTION":
        expected_digest = stable_digest(to_primitive(candidates)) if candidates else None
        if (
            payload.result.status != "SUCCEEDED"
            or len(candidates) != payload.result.candidate_count
            or any(validity)
            or payload.candidate_set_digest != expected_digest
        ):
            raise _evidence_error("no valid candidates")
    elif payload.terminal_state == "INVALID_RESPONSE" and payload.result.status == "SUCCEEDED":
        if candidates or payload.candidate_set_digest is not None:
            raise _evidence_error("invalid response candidates")
    elif (
        candidates
        or payload.candidate_set_digest is not None
        or payload.result.candidate_count != 0
    ):
        raise _evidence_error("gateway failure candidates")


def _validate_manager_payload(payload: ManagerResponseCommittedPayload) -> None:
    if payload.decision == "ACCEPT":
        valid_reason = payload.reason_code == "APPROVED"
    elif payload.decision == "MODIFY":
        valid_reason = payload.reason_code == "APPROVED_WITH_CHANGES"
    else:
        valid_reason = payload.reason_code in {
            "MANUAL_HANDLING",
            "INSUFFICIENT_CONTEXT",
            "OTHER",
        }
    if not valid_reason:
        raise _evidence_error("manager reason")
    if payload.decision == "REJECT":
        if any(
            item is not None
            for item in (
                payload.effective_schedule,
                payload.schedule_digest,
                payload.effective_plan_revision,
            )
        ):
            raise _evidence_error("rejected plan")
        return
    if (
        type(payload.effective_schedule) is not tuple
        or type(payload.effective_plan_revision) is not int
        or payload.effective_plan_revision < 1
        or payload.schedule_digest != stable_digest(payload.effective_schedule)
    ):
        raise _evidence_error("effective plan")
    try:
        if canonical_schedule(payload.effective_schedule) != payload.effective_schedule:
            raise _evidence_error("effective schedule order")
    except Exception:
        raise _evidence_error("effective schedule") from None


def _terminal_events(history: StaffingHistory, generation_id: str) -> tuple[StaffingEvent, ...]:
    return tuple(
        event
        for event in history.events
        if event.event_type
        in {"suggestion_issued", "suggestion_unavailable", "generation_interrupted"}
        and getattr(event.payload, "generation_id", None) == generation_id
    )


def _validate_candidates_for_terminal(
    reservation: GenerationReservedPayload,
    candidates: tuple[StoredCandidate, ...],
) -> tuple[bool, ...]:
    if type(candidates) is not tuple or len(candidates) > 2:
        raise _evidence_error("candidates")
    indexes = tuple(item.candidate_index for item in candidates if type(item) is StoredCandidate)
    if len(indexes) != len(candidates) or indexes not in ((), (1,), (1, 2)):
        raise _evidence_error("candidate indexes")
    try:
        patches = tuple(candidate_patch_for_revalidation(item) for item in candidates)
        validations = validate_candidates(
            reservation.basis_snapshot,
            patches,
            worker_alias_to_staff_id=reservation.worker_alias_to_staff_id,
            assignment_alias_to_assignment_id=reservation.assignment_alias_to_assignment_id,
            prompt_template_version=reservation.prompt_template_version,
        )
        rebuilt = tuple(
            stored_candidate_from_validation(candidate, validation)
            for candidate, validation in zip(patches, validations)
        )
        if len(rebuilt) != len(candidates) or any(
            not _same_value(left, right) for left, right in zip(rebuilt, candidates)
        ):
            raise _evidence_error("candidate revalidation")
        return tuple(validation.valid for validation in validations)
    except Exception:
        raise _evidence_error("candidate revalidation") from None


def _validate_terminal_payload(
    history: StaffingHistory,
    reservation: GenerationReservedPayload,
    payload: SuggestionIssuedPayload | SuggestionUnavailablePayload,
) -> None:
    if payload.request_digest != reservation.request_digest:
        raise _evidence_error("request_digest")
    if payload.generation_id != reservation.generation_id:
        raise _evidence_error("generation_id")
    if type(payload) is SuggestionIssuedPayload:
        validate_replayable_result_evidence(
            history,
            reservation,
            payload.result,
            payload.generation_id,
            "SUCCEEDED",
            None,
        )
        validity = _validate_candidates_for_terminal(reservation, payload.candidates)
        if (
            payload.result.status != "SUCCEEDED"
            or len(payload.candidates) != payload.result.candidate_count
            or not any(validity)
            or payload.candidate_set_digest != stable_digest(to_primitive(payload.candidates))
        ):
            raise _evidence_error("suggestion candidates")
        return
    _validate_unavailable_failure_pair(payload)
    validate_replayable_result_evidence(
        history,
        reservation,
        payload.result,
        payload.generation_id,
        payload.terminal_state,
        payload.failure_code,
    )
    if payload.terminal_state == "NO_VALID_SUGGESTION":
        validity = _validate_candidates_for_terminal(reservation, payload.candidates)
        expected_digest = (
            stable_digest(to_primitive(payload.candidates)) if payload.candidates else None
        )
        if (
            payload.result.status != "SUCCEEDED"
            or len(payload.candidates) != payload.result.candidate_count
            or any(validity)
            or payload.candidate_set_digest != expected_digest
        ):
            raise _evidence_error("no valid candidates")
    elif payload.terminal_state == "INVALID_RESPONSE" and payload.result.status == "SUCCEEDED":
        if payload.candidates or payload.candidate_set_digest is not None:
            raise _evidence_error("invalid response candidates")
    elif (
        payload.candidates
        or payload.candidate_set_digest is not None
        or payload.result.candidate_count != 0
    ):
        raise _evidence_error("gateway failure candidates")


def _validate_payload_common(event: StaffingEvent) -> None:
    payload = event.payload
    expected_type = EVENT_PAYLOAD_TYPES[event.event_type]
    if type(payload) is not expected_type:
        raise _event_error("payload type")
    expected_cause = _expected_causation(event.event_type, payload)
    if event.causation_id != expected_cause:
        raise _event_error("causation_id")
    if event.event_type == "generation_interrupted":
        _validate_interruption_timestamp(
            payload.interrupted_at_utc, event.occurred_at_utc
        )


def _validate_direct_event(event: StaffingEvent) -> None:
    if type(event.event_type) is not str or event.event_type not in EVENT_TYPES:
        raise _event_error("event_type")
    if (
        type(event.event_id) is not str
        or IDENTIFIER_PATTERN.fullmatch(event.event_id) is None
    ):
        raise _event_error("event_id")
    if type(event.sequence) is not int or event.sequence < 1:
        raise _event_error("sequence")
    if (
        type(event.site_id) is not str
        or IDENTIFIER_PATTERN.fullmatch(event.site_id) is None
        or type(event.deployment_id) is not str
        or IDENTIFIER_PATTERN.fullmatch(event.deployment_id) is None
    ):
        raise _event_error("event identity")
    if event.causation_id is not None and (
        type(event.causation_id) is not str
        or IDENTIFIER_PATTERN.fullmatch(event.causation_id) is None
    ):
        raise _event_error("causation_id")
    _validate_payload_common(event)
    expected = staffing_event_id(
        event.event_type,
        event.sequence,
        event.site_id,
        event.deployment_id,
        event.occurred_at_utc,
        event.causation_id,
        event.payload,
    )
    if event.event_id != expected:
        raise _event_error("event_id")


def _validate_audit_fields(payload: object) -> None:
    if hasattr(payload, "request_digest"):
        _digest(getattr(payload, "request_digest"), "request_digest")
    if hasattr(payload, "request_id"):
        _identifier(getattr(payload, "request_id"), "request_id")
    if hasattr(payload, "operator"):
        _human_text(
            getattr(payload, "operator"), "operator", maximum=128, formula_safe=True
        )
    if hasattr(payload, "note"):
        _optional_human_text(getattr(payload, "note"), "note", maximum=500)


def _validate_roster_transition(history: StaffingHistory, event: StaffingEvent) -> None:
    payload = event.payload
    assert type(payload) is RosterImportedPayload
    _validate_audit_fields(payload)
    _human_text(payload.source_ref, "source_ref", maximum=256, formula_safe=True)
    if (
        type(payload.roster) is not RosterRevision
        or type(payload.roster_revision) is not int
        or payload.roster_revision < 1
        or payload.roster.site_id != event.site_id
        or payload.roster.deployment_id != event.deployment_id
        or payload.roster_revision != payload.roster.revision
        or payload.roster_digest != payload.roster.roster_digest
    ):
        raise _evidence_error("roster payload")
    previous = tuple(
        item.payload.roster_revision
        for item in history.events
        if item.event_type == "roster_imported"
        and type(item.payload) is RosterImportedPayload
    )
    if payload.roster_revision != len(previous) + 1:
        raise _evidence_error("roster_revision")


def _validate_exception_transition(history: StaffingHistory, event: StaffingEvent) -> None:
    payload = event.payload
    _validate_audit_fields(payload)
    try:
        from .exceptions import active_exceptions

        dates: set[date] = set()
        if type(payload) is ExceptionRecordedPayload:
            dates.add(payload.service_date)
            if (
                payload.exception_id != payload.exception.exception_id
                or payload.service_date != payload.exception.service_date
                or payload.exception_digest != exception_digest(payload.exception)
            ):
                raise _evidence_error("exception record")
        elif type(payload) is ExceptionCancelledPayload:
            dates.add(payload.cancelled_exception.service_date)
        elif type(payload) is ExceptionCorrectedPayload:
            dates.add(payload.previous_exception.service_date)
        active_exceptions(history.events + (event,), next(iter(dates)))
    except StaffingError as error:
        if error.code == "staffing_invalid_evidence":
            raise
        raise _evidence_error("exception transition") from None
    except Exception:
        raise _evidence_error("exception transition") from None


def _validate_reservation_transition(history: StaffingHistory, event: StaffingEvent) -> None:
    payload = event.payload
    assert type(payload) is GenerationReservedPayload
    _validate_audit_fields(payload)
    expected_id = staffing_generation_id(
        event.site_id,
        event.deployment_id,
        payload.request_id,
        payload.request_digest,
    )
    if payload.generation_id != expected_id or payload.service_date != payload.basis_snapshot.basis.service_date:
        raise _evidence_error("generation identity")
    if any(
        item.event_type == "generation_reserved"
        and (
            item.payload.generation_id == payload.generation_id
            or item.payload.alias_nonce_digest == payload.alias_nonce_digest
        )
        for item in history.events
    ):
        raise _event_error("duplicate generation")
    try:
        projection = GenerationProjection(
            payload.basis_snapshot,
            payload.provider_payload,
            payload.worker_alias_to_staff_id,
            payload.assignment_alias_to_assignment_id,
            payload.alias_nonce_digest,
            payload.input_digest,
        )
        validate_generation_projection(
            projection,
            outer_prompt_template_version=payload.prompt_template_version,
            outer_language=payload.language,
        )
        expected_basis = build_staffing_basis(history, payload.service_date)
        if not _same_value(expected_basis, payload.basis_snapshot):
            raise _evidence_error("reservation basis")
        _validate_generation_route(payload.route)
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error("generation reservation") from None
    if payload.retry_of is not None:
        if payload.retry_of == payload.generation_id:
            raise _event_error("retry self-reference")
        try:
            history.reservation(payload.retry_of)
        except StaffingError:
            raise _event_error("retry target") from None
        terminals = _terminal_events(history, payload.retry_of)
        if (
            len(terminals) != 1
            or terminals[0].event_type != "generation_interrupted"
            or terminals[0].payload.reason != "RESULT_UNKNOWN"
            or any(
                item.event_type == "generation_reserved"
                and item.payload.retry_of == payload.retry_of
                for item in history.events
            )
        ):
            raise _event_error("retry target")


def _reservation_for_event(
    history: StaffingHistory, event: StaffingEvent
) -> GenerationReservedPayload:
    generation_id = getattr(event.payload, "generation_id", None)
    if type(generation_id) is not str:
        raise _event_error("generation_id")
    try:
        reservation = history.reservation(generation_id)
    except StaffingError:
        raise _event_error("unknown generation") from None
    if (
        event.event_type != "manager_response_committed"
        and getattr(event.payload, "request_digest", None) != reservation.request_digest
    ):
        raise _evidence_error("request_digest")
    return reservation


def _validate_attempt_transition(
    history: StaffingHistory,
    event: StaffingEvent,
    reservation: GenerationReservedPayload,
) -> None:
    if _terminal_events(history, reservation.generation_id):
        raise _event_error("attempt after terminal")
    starts, finishes = _generation_attempt_evidence(history, reservation.generation_id)
    _validate_attempt_pairing(reservation, starts, finishes, require_complete=False)
    if event.event_type == "provider_attempt_started":
        evidence = event.payload.evidence
        if len(starts) != len(finishes):
            raise _event_error("attempt already started")
        expected_index = len(starts)
        if expected_index == 1 and finishes[0].failure_code not in FALLBACK_ELIGIBLE_CODES:
            raise _event_error("backup not eligible")
        expected_route, cap = _expected_attempt_route(reservation, expected_index)
        if (
            type(evidence) is not AttemptStartedEvidence
            or type(evidence.timeout_s) is not float
            or not math.isfinite(evidence.timeout_s)
            or not 0 < evidence.timeout_s <= cap
            or evidence.request_id != reservation.generation_id
            or evidence.attempt_index != expected_index
            or evidence.input_digest != reservation.input_digest
            or not _same_value(evidence.route, expected_route)
        ):
            raise _evidence_error("attempt start")
        return
    evidence = event.payload.evidence
    if len(starts) != len(finishes) + 1:
        raise _event_error("finish before start")
    _validate_replayable_attempt_shape(evidence)
    expected = starts[len(finishes)]
    if (
        evidence.request_id != expected.request_id
        or evidence.attempt_index != expected.attempt_index
        or not _same_value(evidence.route, expected.route)
    ):
        raise _evidence_error("attempt finish")


def _validate_manager_transition(
    history: StaffingHistory,
    event: StaffingEvent,
    reservation: GenerationReservedPayload,
) -> None:
    payload = event.payload
    assert type(payload) is ManagerResponseCommittedPayload
    _validate_audit_fields(payload)
    _validate_manager_payload(payload)
    issued = tuple(
        item
        for item in history.events
        if item.event_type == "suggestion_issued"
        and getattr(item.payload, "generation_id", None) == reservation.generation_id
    )
    responses = tuple(
        item
        for item in history.events
        if item.event_type == "manager_response_committed"
        and getattr(item.payload, "generation_id", None) == reservation.generation_id
    )
    if len(issued) != 1 or responses:
        raise _event_error("manager response lifecycle")
    try:
        reserved_events = tuple(
            item
            for item in history.events
            if item.event_type == "generation_reserved"
            and getattr(item.payload, "generation_id", None) == reservation.generation_id
        )
        if len(reserved_events) != 1 or any(
            item.event_type == "roster_imported"
            and item.sequence > reserved_events[0].sequence
            for item in history.events
        ):
            raise _evidence_error("manager basis")
        current_basis = build_staffing_basis(
            history, reservation.basis_snapshot.basis.service_date
        )
        if (
            not _same_value(payload.basis_snapshot, reservation.basis_snapshot)
            or not _same_value(current_basis, payload.basis_snapshot)
        ):
            raise _evidence_error("manager basis")
        if payload.decision in {"ACCEPT", "MODIFY"}:
            prior = tuple(
                item.payload
                for item in history.events
                if item.event_type == "manager_response_committed"
                and type(item.payload) is ManagerResponseCommittedPayload
                and item.payload.decision in {"ACCEPT", "MODIFY"}
                and item.payload.basis_snapshot.basis.service_date
                == payload.basis_snapshot.basis.service_date
            )
            if payload.effective_plan_revision != len(prior) + 1:
                raise _evidence_error("effective_plan_revision")
        effective_plan_state(
            StaffingHistory(history.events + (event,)),
            payload.basis_snapshot.basis.service_date,
            payload.basis_snapshot.basis.roster_revision,
        )
    except StaffingError:
        raise
    except Exception:
        raise _evidence_error("manager response") from None


def transition(history: StaffingHistory, event: StaffingEvent) -> StaffingHistory:
    if type(history) is not StaffingHistory or type(history.events) is not tuple:
        raise _event_error("history")
    if type(event) is not StaffingEvent:
        raise _event_error("event")
    if event.sequence != history.record_count + 1:
        raise _event_error("sequence")
    if any(type(item) is not StaffingEvent for item in history.events):
        raise _event_error("history")
    _validate_direct_event(event)
    if history.events and (
        event.site_id != history.events[0].site_id
        or event.deployment_id != history.events[0].deployment_id
    ):
        raise _identity_error()
    if any(item.event_id == event.event_id for item in history.events):
        raise _event_error("duplicate event_id")
    _validate_audit_fields(event.payload)
    operation_kind = EVENT_TO_OPERATION.get(event.event_type)
    request_id = getattr(event.payload, "request_id", None)
    if (
        operation_kind is not None
        and type(request_id) is str
        and history.find_request_event(operation_kind, request_id) is not None
    ):
        raise _event_error("duplicate request")

    if event.event_type == "roster_imported":
        _validate_roster_transition(history, event)
    elif event.event_type in {
        "exception_recorded",
        "exception_cancelled",
        "exception_corrected",
    }:
        _validate_exception_transition(history, event)
    elif event.event_type == "generation_reserved":
        _validate_reservation_transition(history, event)
    else:
        reservation = _reservation_for_event(history, event)
        if event.event_type in {"provider_attempt_started", "provider_attempt_finished"}:
            _validate_attempt_transition(history, event, reservation)
        elif event.event_type in {"suggestion_issued", "suggestion_unavailable"}:
            if _terminal_events(history, reservation.generation_id):
                raise _event_error("duplicate generation terminal")
            starts, finishes = _generation_attempt_evidence(
                history, reservation.generation_id
            )
            _validate_attempt_pairing(
                reservation, starts, finishes, require_complete=True
            )
            _validate_terminal_payload(history, reservation, event.payload)
        elif event.event_type == "generation_interrupted":
            if _terminal_events(history, reservation.generation_id):
                raise _event_error("duplicate generation terminal")
            if event.payload.reason != "RESULT_UNKNOWN":
                raise _evidence_error("interruption reason")
        elif event.event_type == "manager_response_committed":
            _validate_manager_transition(history, event, reservation)
        else:
            raise _event_error("event_type")
    return StaffingHistory(history.events + (event,))


def replay_staffing(
    events: Sequence[StaffingEvent], *, site_id: str, deployment_id: str
) -> StaffingHistory:
    if isinstance(events, (str, bytes, bytearray)):
        raise _event_error("events")
    try:
        detached = tuple(events)
    except Exception:
        raise _event_error("events") from None
    history = StaffingHistory(())
    for event in detached:
        if type(event) is not StaffingEvent:
            raise _event_error("event")
        _validate_direct_event(event)
        if event.site_id != site_id or event.deployment_id != deployment_id:
            raise _identity_error()
        history = transition(history, event)
    return history


__all__ = [
    "EVENT_PAYLOAD_TYPES",
    "EVENT_RECORD_KEYS",
    "candidate_patch_for_revalidation",
    "event_from_record",
    "parse_event",
    "replay_staffing",
    "scan_and_detach_event_tree",
    "staffing_event_id",
    "staffing_generation_id",
    "stored_candidate_from_validation",
    "transition",
    "validate_replayable_result_evidence",
]
