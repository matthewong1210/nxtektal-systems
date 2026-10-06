"""Stable staffing evidence and bounded regional model composition.

This is the sole composition point between the staffing domain owner and the
provider-neutral model gateway.  It exposes advisory data only; it has no robot,
schedule-execution, notification, payroll, or attendance integration.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import threading
from types import MappingProxyType
from typing import Any, Literal, TypeVar
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from nxt_model_gateway import (
    AttemptObserverError,
    AttemptRecord,
    AttemptStarted,
    DeploymentRegion,
    FailureCode,
    GatewayContractError,
    GenerationMessage,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    MessageRole,
    ModelGateway,
    Provider,
    ProviderConfig,
    RoutePolicy,
    RouteReadiness,
    RouteReadinessStatus,
    stable_digest,
)
from nxt_model_gateway.anthropic import AnthropicAdapter
from nxt_model_gateway.kimi import KimiAdapter
from nxt_model_gateway.openai import OpenAIAdapter
from nxt_model_gateway.transport import StdlibHttpsTransport
from nxt_pilot_ops.staffing.contracts import (
    AssignmentProjection,
    AttemptFinishedEvidence,
    AttemptRouteEvidence,
    AttemptStartedEvidence,
    CandidateOperationProjection,
    CandidateProjection,
    CommittedReceipt,
    ConflictReceipt,
    CoverageGap,
    DateProjection,
    DuplicateReceipt,
    ExceptionCommittedProjection,
    ExceptionProjection,
    GenerationCommittedProjection,
    GenerationProjectionView,
    GenerationRouteEvidence,
    ManagerCommittedProjection,
    ManagerResponseProjection,
    ReceiptResult,
    RequestProjection,
    ResultEvidence,
    RosterCommittedProjection,
    StaffingError,
)
from nxt_pilot_ops.staffing.ledger import StaffingLedger
from nxt_pilot_ops.staffing.operations import StaffingOperations
from nxt_pilot_ops.staffing.prompt import PROMPT_TEMPLATE_VERSION
from nxt_pilot_ops.staffing.projection import (
    GenerationProjection,
    STAFFING_SUGGESTION_OUTPUT_SCHEMA,
    canonical_generation_input,
)
from nxt_site_agent import SiteAgentError


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_PUBLIC_ITEMS = 4096
_REJECTION_CODES = frozenset(
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
_FALLBACK_CODES = frozenset(
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
_SECURITY_CODES = frozenset(
    {
        "RESPONSE_TOO_LARGE",
        "UNSUPPORTED_CONTENT_ENCODING",
        "REDIRECT_REFUSED",
        "ENDPOINT_NOT_ALLOWED",
        "TLS_VERIFICATION_FAILED",
    }
)
_NONRETRY_CODES = frozenset(
    {
        "DEADLINE_EXHAUSTED",
        "PROVIDER_REFUSED",
        "MALFORMED_PROVIDER_RESPONSE",
        "SCHEMA_MISMATCH",
        "AUTHENTICATION_FAILED",
        "PERMISSION_DENIED",
        "MODEL_NOT_FOUND",
        "INVALID_PROVIDER_REQUEST",
        "PROVIDER_CLIENT_ERROR",
    }
)
_LOCAL_ATTEMPT_FORBIDDEN = frozenset(
    {
        "INPUT_TOO_LARGE",
        "BACKUP_UNCONFIGURED",
        "PROVIDER_UNCONFIGURED",
        "invalid_provider_shape",
        "provider_sensitive_key",
        "invalid_provider_timestamp",
        "invalid_candidate_set",
    }
)
_DOMAIN_INVALID_OUTPUT_CODES = frozenset(
    {
        "invalid_provider_shape",
        "provider_sensitive_key",
        "invalid_provider_timestamp",
        "invalid_candidate_set",
    }
)
_FAILURE_STATE = {
    **{code: "UNAVAILABLE" for code in _FALLBACK_CODES},
    "DEADLINE_EXHAUSTED": "UNAVAILABLE",
    "BACKUP_UNCONFIGURED": "UNAVAILABLE",
    "PROVIDER_REFUSED": "REFUSED",
    "MALFORMED_PROVIDER_RESPONSE": "INVALID_RESPONSE",
    "SCHEMA_MISMATCH": "INVALID_RESPONSE",
    **{code: "INVALID_RESPONSE" for code in _DOMAIN_INVALID_OUTPUT_CODES},
    "PROVIDER_CLIENT_ERROR": "PROVIDER_ERROR",
    "INPUT_TOO_LARGE": "CONFIGURATION_ERROR",
    "AUTHENTICATION_FAILED": "CONFIGURATION_ERROR",
    "PERMISSION_DENIED": "CONFIGURATION_ERROR",
    "MODEL_NOT_FOUND": "CONFIGURATION_ERROR",
    "INVALID_PROVIDER_REQUEST": "CONFIGURATION_ERROR",
    "PROVIDER_UNCONFIGURED": "CONFIGURATION_ERROR",
    **{code: "SECURITY_ERROR" for code in _SECURITY_CODES},
}
_OPERATION_KINDS = frozenset(
    {
        "roster-import",
        "exception-record",
        "exception-cancel",
        "exception-correct",
        "suggestion-generate",
        "manager-response",
    }
)
_EXCEPTION_CANCEL_BODY_KEYS = frozenset(
    {
        "schema",
        "request_id",
        "expected_exception_set_revision",
        "operator",
        "note",
    }
)
_EXCEPTION_CORRECT_BODY_KEYS = frozenset(
    {
        "schema",
        "request_id",
        "expected_exception_set_revision",
        "operator",
        "replacement",
    }
)
_T = TypeVar("_T")


class StaffingProjectionError(RuntimeError):
    """Verified local evidence cannot be represented by the frozen wire API."""


class StaffingShutdownError(RuntimeError):
    """Shutdown has not completed and the wrapper must be retained and retried."""


def _projection(detail: str) -> StaffingProjectionError:
    return StaffingProjectionError(detail)


def _required(value: _T | None, field_name: str) -> _T:
    if value is None:
        raise _projection(f"missing projection field: {field_name}")
    return value


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise _projection(f"invalid identifier projection: {field_name}")
    return value


def _code(value: object, field_name: str) -> str:
    if type(value) is not str or _CODE.fullmatch(value) is None:
        raise _projection(f"invalid code projection: {field_name}")
    return value


def _digest(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise _projection(f"invalid digest projection: {field_name}")
    return value


def _integer(value: object, field_name: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise _projection(f"invalid integer projection: {field_name}")
    return value


def _plain_text(
    value: object,
    field_name: str,
    *,
    maximum: int,
    minimum: int = 0,
    optional: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not minimum <= len(value) <= maximum:
        raise _projection(f"invalid text projection: {field_name}")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise _projection(f"invalid text projection: {field_name}")
    return value


def _operator(value: object, field_name: str = "operator") -> str:
    result = _plain_text(value, field_name, minimum=1, maximum=128)
    assert result is not None
    if not result.strip() or result.lstrip(" \t").startswith(("=", "+", "@", "-")):
        raise _projection(f"invalid operator projection: {field_name}")
    return result


def _display_name(value: object, field_name: str = "display_name") -> str:
    result = _plain_text(value, field_name, minimum=1, maximum=100)
    assert result is not None
    if not result.strip() or result.lstrip(" \t").startswith(("=", "+", "@", "-")):
        raise _projection(f"invalid display name projection: {field_name}")
    return result


def _ascii(value: object, field_name: str, maximum: int, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or not value.strip()
        or any(not 32 <= ord(character) <= 126 for character in value)
    ):
        raise _projection(f"invalid ASCII projection: {field_name}")
    return value


def resolve_staffing_root(
    state_root: Path,
    *,
    site_id: str,
    deployment_id: str,
    volatile_out: Path,
) -> Path:
    """Resolve one stable deployment root without accepting path-like identities."""

    for value in (site_id, deployment_id):
        if (
            type(value) is not str
            or not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
        ):
            raise ValueError("staffing identity is not one safe path segment")
    base = Path(state_root).absolute()
    volatile = Path(volatile_out).absolute()
    target = base / site_id / deployment_id / "staffing-v1"
    resolved_base = base.resolve(strict=False)
    resolved_volatile = volatile.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    if not resolved_target.is_relative_to(resolved_base):
        raise ValueError("staffing root escapes configured state root")
    if resolved_target == resolved_volatile or resolved_target.is_relative_to(
        resolved_volatile
    ):
        raise ValueError("stable staffing root cannot live below volatile --out")
    cursor = Path(target.anchor)
    for part in target.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError("stable staffing root cannot contain a symlink")
    return resolved_target


@dataclass(frozen=True, slots=True)
class StaffingProviderSettings:
    region: DeploymentRegion | None
    language: Literal["zh-CN", "en"]
    kimi_model: str | None
    openai_model: str | None
    anthropic_model: str | None
    moonshot_api_key: str | None = field(repr=False)
    openai_api_key: str | None = field(repr=False)
    anthropic_api_key: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.region is not None and not isinstance(self.region, DeploymentRegion):
            raise ValueError("invalid staffing deployment region")
        if self.language not in {"zh-CN", "en"}:
            raise ValueError("staffing language must be zh-CN or en")


def load_provider_settings(
    env: Mapping[str, str],
    *,
    region: str | None,
    language: str,
    kimi_model: str | None,
    openai_model: str | None,
    anthropic_model: str | None,
) -> StaffingProviderSettings:
    if language not in {"zh-CN", "en"}:
        raise ValueError("staffing language must be zh-CN or en")
    try:
        parsed_region = None if region is None else DeploymentRegion(region)
    except ValueError:
        raise ValueError("staffing region must be CN or GLOBAL") from None

    def clean(value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()

    return StaffingProviderSettings(
        region=parsed_region,
        language=language,
        kimi_model=clean(kimi_model),
        openai_model=clean(openai_model),
        anthropic_model=clean(anthropic_model),
        moonshot_api_key=clean(env.get("MOONSHOT_API_KEY")),
        openai_api_key=clean(env.get("OPENAI_API_KEY")),
        anthropic_api_key=clean(env.get("ANTHROPIC_API_KEY")),
    )


@dataclass(frozen=True, slots=True)
class ConfiguredGateway:
    gateway: ModelGateway
    route: RoutePolicy | None
    readiness: RouteReadiness | None


def configured_gateway(
    settings: StaffingProviderSettings, *, monotonic: Callable[[], float]
) -> ConfiguredGateway:
    transport = StdlibHttpsTransport(monotonic=monotonic)
    kimi = (
        KimiAdapter(
            config=ProviderConfig(
                Provider.KIMI, settings.kimi_model, settings.moonshot_api_key
            ),
            transport=transport,
        )
        if settings.region is DeploymentRegion.CN
        and settings.kimi_model is not None
        and settings.moonshot_api_key is not None
        else None
    )
    openai = (
        OpenAIAdapter(
            config=ProviderConfig(
                Provider.OPENAI, settings.openai_model, settings.openai_api_key
            ),
            transport=transport,
        )
        if settings.region is DeploymentRegion.GLOBAL
        and settings.openai_model is not None
        and settings.openai_api_key is not None
        else None
    )
    anthropic = (
        AnthropicAdapter(
            config=ProviderConfig(
                Provider.ANTHROPIC,
                settings.anthropic_model,
                settings.anthropic_api_key,
            ),
            transport=transport,
        )
        if settings.region is DeploymentRegion.GLOBAL
        and settings.anthropic_model is not None
        and settings.anthropic_api_key is not None
        else None
    )
    gateway = ModelGateway(
        kimi=kimi,
        openai=openai,
        anthropic=anthropic,
        monotonic=monotonic,
    )
    if settings.region is None:
        return ConfiguredGateway(gateway, None, None)
    route = RoutePolicy(settings.region)
    return ConfiguredGateway(gateway, route, gateway.readiness(route))


def generation_route_evidence(
    configured: ConfiguredGateway,
    settings: StaffingProviderSettings,
) -> GenerationRouteEvidence | None:
    if configured.route is None or configured.readiness is None:
        return None
    primary = (
        Provider.KIMI
        if configured.route.region is DeploymentRegion.CN
        else Provider.OPENAI
    )
    primary_model = (
        settings.kimi_model if primary is Provider.KIMI else settings.openai_model
    )
    if primary_model is None:
        return None
    readiness = configured.readiness
    primary_provider = readiness.primary_provider
    backup_provider = readiness.backup_provider
    return GenerationRouteEvidence(
        region=configured.route.region.value,
        readiness=readiness.status.value,
        primary_provider=(
            None if primary_provider is None else primary_provider.value
        ),
        primary_model_id=(primary_model if primary_provider is not None else None),
        backup_provider=(None if backup_provider is None else backup_provider.value),
        backup_model_id=(
            settings.anthropic_model
            if backup_provider is Provider.ANTHROPIC
            else None
        ),
    )


def generation_capability(configured: ConfiguredGateway) -> dict[str, object]:
    if configured.route is None or configured.readiness is None:
        return {
            "status": "UNAVAILABLE",
            "region": None,
            "primary_provider": None,
            "backup_provider": None,
            "failure_code": "PROVIDER_UNCONFIGURED",
        }
    readiness = configured.readiness
    result = {
        "status": readiness.status.value,
        "region": readiness.region.value,
        "primary_provider": (
            None
            if readiness.primary_provider is None
            else readiness.primary_provider.value
        ),
        "backup_provider": (
            None if readiness.backup_provider is None else readiness.backup_provider.value
        ),
        "failure_code": (
            None if readiness.failure_code is None else readiness.failure_code.value
        ),
    }
    legal = {
        ("READY", "CN", "KIMI", None, None),
        ("UNAVAILABLE", "CN", "KIMI", None, "PROVIDER_UNCONFIGURED"),
        ("READY", "GLOBAL", "OPENAI", "ANTHROPIC", None),
        (
            "DEGRADED_BACKUP_UNCONFIGURED",
            "GLOBAL",
            "OPENAI",
            None,
            "BACKUP_UNCONFIGURED",
        ),
        ("UNAVAILABLE", "GLOBAL", "OPENAI", None, "PROVIDER_UNCONFIGURED"),
        (
            "UNAVAILABLE",
            "GLOBAL",
            "OPENAI",
            "ANTHROPIC",
            "PROVIDER_UNCONFIGURED",
        ),
    }
    if tuple(result.values()) not in legal:
        raise _projection("gateway readiness tuple is not a public capability")
    return result


def _attempt_tuple(index: int, provider: str, region: str, role: str, route_id: str) -> None:
    legal = {
        (0, "KIMI", "CN", "PRIMARY", "cn-kimi-v1"),
        (0, "OPENAI", "GLOBAL", "PRIMARY", "global-openai-v1"),
        (1, "ANTHROPIC", "GLOBAL", "BACKUP", "global-anthropic-v1"),
    }
    if (index, provider, region, role, route_id) not in legal:
        raise _projection("provider attempt route is not allowed")


def attempt_route(
    attempt: AttemptStarted | AttemptRecord, *, region: DeploymentRegion
) -> AttemptRouteEvidence:
    if not isinstance(region, DeploymentRegion):
        raise _projection("provider attempt region is invalid")
    role = "PRIMARY" if attempt.attempt_index == 0 else "BACKUP"
    route_id = f"{region.value.lower()}-{attempt.provider.value.lower()}-v1"
    _attempt_tuple(
        attempt.attempt_index,
        attempt.provider.value,
        region.value,
        role,
        route_id,
    )
    _ascii(attempt.model_id, "model_id", 128)
    return AttemptRouteEvidence(
        provider=attempt.provider.value,
        region=region.value,
        route_role=role,
        route_id=route_id,
        model_id=attempt.model_id,
    )


def started_evidence(
    attempt: AttemptStarted, *, region: DeploymentRegion
) -> AttemptStartedEvidence:
    if type(attempt) is not AttemptStarted:
        raise _projection("attempt start has invalid type")
    route = attempt_route(attempt, region=region)
    cap = 15.0 if region is DeploymentRegion.CN else (12.0 if attempt.attempt_index == 0 else 8.0)
    if type(attempt.timeout_s) not in (int, float) or not 0 < attempt.timeout_s <= cap:
        raise _projection("attempt timeout exceeds route cap")
    return AttemptStartedEvidence(
        request_id=attempt.request_id,
        attempt_index=attempt.attempt_index,
        route=route,
        input_digest=attempt.input_digest,
        timeout_s=attempt.timeout_s,
    )


def _validate_finished_disposition(attempt: AttemptRecord) -> None:
    if attempt.status is GenerationStatus.SUCCEEDED:
        expected_reason = {
            Provider.KIMI: "stop",
            Provider.OPENAI: "completed",
            Provider.ANTHROPIC: "tool_use",
        }[attempt.provider]
        if (
            attempt.failure_code is not None
            or attempt.retryable
            or attempt.security_failure
            or attempt.finish_reason != expected_reason
            or attempt.output_digest is None
        ):
            raise _projection("successful provider attempt disposition is invalid")
        if attempt.usage is not None and (
            attempt.usage.input_tokens > 1_000_000
            or attempt.usage.output_tokens > 1_000_000
        ):
            raise _projection("provider token evidence exceeds domain bound")
        return
    code = None if attempt.failure_code is None else attempt.failure_code.value
    if code is None or code in _LOCAL_ATTEMPT_FORBIDDEN:
        raise _projection("provider attempt failure code is invalid")
    expected_state = _FAILURE_STATE.get(code)
    expected_retryable = code in _FALLBACK_CODES
    expected_security = code in _SECURITY_CODES
    if (
        expected_state != attempt.status.value
        or attempt.retryable is not expected_retryable
        or attempt.security_failure is not expected_security
        or attempt.provider_request_id is not None
        or attempt.finish_reason is not None
        or attempt.output_digest is not None
        or attempt.usage is not None
    ):
        raise _projection("failed provider attempt disposition is invalid")


def finished_evidence(
    attempt: AttemptRecord, *, region: DeploymentRegion
) -> AttemptFinishedEvidence:
    if type(attempt) is not AttemptRecord:
        raise _projection("attempt finish has invalid type")
    route = attempt_route(attempt, region=region)
    _validate_finished_disposition(attempt)
    return AttemptFinishedEvidence(
        request_id=attempt.request_id,
        attempt_index=attempt.attempt_index,
        route=route,
        status=attempt.status.value,
        failure_code=(
            None if attempt.failure_code is None else attempt.failure_code.value
        ),
        retryable=attempt.retryable,
        security_failure=attempt.security_failure,
        provider_request_id=attempt.provider_request_id,
        finish_reason=attempt.finish_reason,
        output_digest=attempt.output_digest,
        input_tokens=(
            None if attempt.usage is None else attempt.usage.input_tokens
        ),
        output_tokens=(
            None if attempt.usage is None else attempt.usage.output_tokens
        ),
    )


def _validate_result_shape(result: GenerationResult, *, region: DeploymentRegion) -> None:
    if type(result) is not GenerationResult:
        raise _projection("gateway result has invalid type")
    attempts = result.attempts
    if len(attempts) > 2 or tuple(row.attempt_index for row in attempts) != tuple(range(len(attempts))):
        raise _projection("gateway result attempt order is invalid")
    for row in attempts:
        finished_evidence(row, region=region)
        if row.request_id != result.request_id or row.input_digest != result.input_digest:
            raise _projection("gateway result attempt identity is invalid")
    if len(attempts) == 2:
        primary = attempts[0]
        if (
            region is not DeploymentRegion.GLOBAL
            or primary.provider is not Provider.OPENAI
            or primary.failure_code is None
            or primary.failure_code.value not in _FALLBACK_CODES
        ):
            raise _projection("gateway fallback sequence is invalid")
    if (
        region is DeploymentRegion.GLOBAL
        and len(attempts) == 1
        and result.selected_provider is Provider.OPENAI
        and attempts[0].failure_code is not None
        and attempts[0].failure_code.value in _FALLBACK_CODES
    ):
        raise _projection("global fallback sequence is incomplete")
    local_code = (
        result.failure_code
        in {
            FailureCode.INPUT_TOO_LARGE,
            FailureCode.BACKUP_UNCONFIGURED,
            FailureCode.PROVIDER_UNCONFIGURED,
            FailureCode.DEADLINE_EXHAUSTED,
        }
        if result.failure_code is not None
        else False
    )
    local_result = local_code and result.selected_provider is None
    if result.status is GenerationStatus.SUCCEEDED:
        if not attempts or result.selected_provider is None:
            raise _projection("successful gateway result lacks a finished attempt")
    elif local_result:
        if any(
            value is not None
            for value in (
                result.selected_provider,
                result.selected_model_id,
                result.provider_request_id,
                result.finish_reason,
                result.usage,
            )
        ):
            raise _projection("local gateway result has provider metadata")
        if result.output is not None or result.output_digest is not None:
            raise _projection("local gateway result has provider output")
        if result.failure_code is FailureCode.PROVIDER_UNCONFIGURED and attempts:
            raise _projection("unconfigured gateway result has provider attempts")
        if result.failure_code is FailureCode.BACKUP_UNCONFIGURED:
            if len(attempts) != 1 or (
                attempts[0].provider is not Provider.OPENAI
                or attempts[0].failure_code is None
                or attempts[0].failure_code.value not in _FALLBACK_CODES
            ):
                raise _projection("backup-unconfigured gateway result is invalid")
        if result.failure_code in {
            FailureCode.INPUT_TOO_LARGE,
            FailureCode.DEADLINE_EXHAUSTED,
        } and (
            len(attempts) > 1
            or (
                attempts
                and (
                    attempts[0].provider is not Provider.OPENAI
                    or attempts[0].failure_code is None
                    or attempts[0].failure_code.value not in _FALLBACK_CODES
                )
            )
        ):
            raise _projection("local gateway result attempt sequence is invalid")
    else:
        if not attempts or result.selected_provider is None:
            raise _projection("provider gateway result lacks a finished attempt")
        code = None if result.failure_code is None else result.failure_code.value
        if code is None or _FAILURE_STATE.get(code) != result.status.value:
            raise _projection("gateway result failure disposition is invalid")
        if attempts[-1].failure_code is not result.failure_code:
            raise _projection("gateway result failure does not match final attempt")
    if result.selected_provider is not None:
        last = attempts[-1]
        if (
            result.selected_provider is not last.provider
            or result.selected_model_id != last.model_id
            or result.provider_request_id != last.provider_request_id
            or result.finish_reason != last.finish_reason
            or result.usage != last.usage
            or result.output_digest != last.output_digest
        ):
            raise _projection("gateway result metadata or digest does not match final attempt")


def result_evidence(
    result: GenerationResult,
    *,
    region: DeploymentRegion,
    candidate_count: int,
) -> ResultEvidence:
    _validate_result_shape(result, region=region)
    _integer(candidate_count, "candidate_count", maximum=2)
    return ResultEvidence(
        request_id=result.request_id,
        status=result.status.value,
        failure_code=(
            None if result.failure_code is None else result.failure_code.value
        ),
        selected_provider=(
            None
            if result.selected_provider is None
            else result.selected_provider.value
        ),
        selected_model_id=result.selected_model_id,
        provider_request_id=result.provider_request_id,
        finish_reason=result.finish_reason,
        input_digest=result.input_digest,
        output_digest=result.output_digest,
        attempts=tuple(
            finished_evidence(attempt, region=region)
            for attempt in result.attempts
        ),
        candidate_count=candidate_count,
        bounded_summary=None,
    )


class LedgerAttemptObserver:
    """Synchronously persist every provider boundary before returning."""

    def __init__(
        self,
        *,
        owner: StaffingOperations,
        generation_id: str,
        route: RoutePolicy,
        clock: Callable[[], datetime],
    ) -> None:
        self._owner = owner
        self._generation_id = generation_id
        self._route = route
        self._clock = clock
        self._started: dict[int, AttemptStarted] = {}
        self._finished: set[int] = set()

    def started(self, attempt: AttemptStarted) -> None:
        if (
            attempt.request_id != self._generation_id
            or attempt.attempt_index != len(self._started)
            or attempt.attempt_index in self._started
        ):
            raise _projection("provider attempt start order is invalid")
        evidence = started_evidence(attempt, region=self._route.region)
        self._owner.record_attempt_started(
            self._generation_id,
            evidence,
            recorded_at=self._clock(),
        )
        self._started[attempt.attempt_index] = attempt

    def finished(self, attempt: AttemptRecord) -> None:
        begin = self._started.get(attempt.attempt_index)
        if (
            attempt.request_id != self._generation_id
            or begin is None
            or attempt.attempt_index in self._finished
            or begin.request_id != attempt.request_id
            or begin.provider is not attempt.provider
            or begin.model_id != attempt.model_id
            or begin.input_digest != attempt.input_digest
        ):
            raise _projection("provider attempt finish does not pair with start")
        evidence = finished_evidence(attempt, region=self._route.region)
        self._owner.record_attempt_finished(
            self._generation_id,
            evidence,
            recorded_at=self._clock(),
        )
        self._finished.add(attempt.attempt_index)


def offset_time(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None:
        raise _projection("projection timestamp is naive")
    try:
        offset = value.utcoffset()
    except Exception:
        raise _projection("projection timestamp offset is invalid") from None
    if (
        offset is None
        or value.second != 0
        or value.microsecond != 0
        or offset.total_seconds() % 60 != 0
    ):
        raise _projection("projection timestamp is not whole-minute canonical")
    rendered = value.isoformat(timespec="seconds")
    if offset == timedelta(0):
        if not rendered.endswith(("+00:00", "-00:00")):
            raise _projection("zero-offset timestamp is not canonical")
        return rendered[:-6] + "Z"
    if rendered.endswith(("+00:00", "-00:00")):
        raise _projection("signed zero offset is forbidden")
    return rendered


def utc_time(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None:
        raise _projection("audit timestamp is not UTC")
    try:
        if value.utcoffset() != timedelta(0):
            raise _projection("audit timestamp is not UTC")
    except Exception:
        raise _projection("audit timestamp is not UTC") from None
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def assignment_to_wire(item: AssignmentProjection) -> dict[str, object]:
    if type(item) is not AssignmentProjection:
        raise _projection("assignment projection has invalid type")
    return {
        "assignment_id": _identifier(item.assignment_id, "assignment_id"),
        "staff_id": _identifier(item.staff_id, "staff_id"),
        "display_name": _display_name(item.display_name),
        "role_code": _code(item.role_code, "role_code"),
        "area_code": _code(item.area_code, "area_code"),
        "start_at": offset_time(item.start_at),
        "end_at": offset_time(item.end_at),
    }


def exception_to_wire(item: ExceptionProjection) -> dict[str, object]:
    if type(item) is not ExceptionProjection or item.kind not in {
        "LEAVE",
        "LATE",
        "EARLY_DEPARTURE",
        "UNAVAILABLE",
    }:
        raise _projection("exception projection is invalid")
    return {
        "exception_id": _identifier(item.exception_id, "exception_id"),
        "staff_id": _identifier(item.staff_id, "staff_id"),
        "display_name": _display_name(item.display_name),
        "kind": item.kind,
        "unavailable_start_at": offset_time(item.unavailable_start),
        "unavailable_end_at": offset_time(item.unavailable_end),
        "note": _plain_text(item.note, "note", maximum=500, optional=True),
        "active": True,
    }


def coverage_gap_to_wire(item: CoverageGap) -> dict[str, object]:
    if type(item) is not CoverageGap:
        raise _projection("coverage gap projection has invalid type")
    required = _integer(item.required, "required_count", minimum=1, maximum=10_000)
    actual = _integer(item.actual, "assigned_count", maximum=9_999)
    if actual >= required:
        raise _projection("coverage gap assigned count is not below required count")
    return {
        "role_code": _code(item.role_code, "role_code"),
        "area_code": _code(item.area_code, "area_code"),
        "start_at": offset_time(item.start_at),
        "end_at": offset_time(item.end_at),
        "required_count": required,
        "assigned_count": actual,
        "rejection_code": "COVERAGE_GAP",
    }


def candidate_operation_to_wire(
    item: CandidateOperationProjection,
) -> dict[str, object]:
    if type(item) is not CandidateOperationProjection:
        raise _projection("candidate operation projection has invalid type")
    common = {
        "staff_id": _identifier(
            _required(item.staff_id, "operation.staff_id"), "operation.staff_id"
        ),
        "display_name": _display_name(
            _required(item.display_name, "operation.display_name"),
            "operation.display_name",
        ),
        "role_code": _code(
            _required(item.role_code, "operation.role_code"), "operation.role_code"
        ),
        "area_code": _code(
            _required(item.area_code, "operation.area_code"), "operation.area_code"
        ),
        "start_at": offset_time(_required(item.start_at, "operation.start_at")),
        "end_at": offset_time(_required(item.end_at, "operation.end_at")),
    }
    if item.operation == "REMOVE":
        return {
            "operation": "REMOVE",
            "assignment_id": _identifier(
                _required(item.assignment_id, "operation.assignment_id"),
                "operation.assignment_id",
            ),
            **common,
        }
    if item.operation == "ADD" and item.assignment_id is None:
        return {"operation": "ADD", **common}
    raise _projection("candidate operation branch is invalid")


def candidate_to_wire(item: CandidateProjection) -> dict[str, object]:
    if type(item) is not CandidateProjection:
        raise _projection("candidate projection has invalid type")
    index = _integer(item.candidate_index, "candidate_index", minimum=1, maximum=2)
    if type(item.valid) is not bool or type(item.operations) is not tuple or len(item.operations) > 32:
        raise _projection("candidate projection shape is invalid")
    rationale = _plain_text(item.rationale, "rationale", maximum=280)
    if type(item.operational_warnings) is not tuple or len(item.operational_warnings) > 5:
        raise _projection("candidate warning sequence is invalid")
    warnings = [
        _plain_text(value, "operational_warning", maximum=200)
        for value in item.operational_warnings
    ]
    if type(item.coverage_gaps) is not tuple or len(item.coverage_gaps) > 4096:
        raise _projection("candidate coverage gap sequence is invalid")
    gaps = [coverage_gap_to_wire(value) for value in item.coverage_gaps]
    if type(item.rejection_codes) is not tuple:
        raise _projection("candidate rejection sequence is invalid")
    rejection_codes = list(item.rejection_codes)
    if (
        len(rejection_codes) != len(set(rejection_codes))
        or any(value not in _REJECTION_CODES for value in rejection_codes)
    ):
        raise _projection("candidate rejection code is invalid")
    if item.valid:
        if gaps or rejection_codes or item.materialized_schedule is None:
            raise _projection("valid candidate branch is inconsistent")
        schedule_digest = _digest(
            item.materialized_schedule_digest, "candidate.schedule_digest"
        )
    else:
        if item.materialized_schedule is not None or item.materialized_schedule_digest is not None:
            raise _projection("rejected candidate has a materialized schedule")
        schedule_digest = None
        if gaps:
            if rejection_codes != ["COVERAGE_GAP"]:
                raise _projection("coverage rejection branch is inconsistent")
        elif not 1 <= len(rejection_codes) <= 13 or "COVERAGE_GAP" in rejection_codes:
            raise _projection("noncoverage rejection branch is inconsistent")
    return {
        "candidate_index": index,
        "status": "VALID" if item.valid else "REJECTED",
        "operations": [candidate_operation_to_wire(value) for value in item.operations],
        "rationale": rationale,
        "operational_warnings": warnings,
        "coverage_gaps": gaps,
        "schedule_digest": schedule_digest,
        "rejection_codes": rejection_codes,
    }


def _validate_finished_projection(item: AttemptFinishedEvidence) -> None:
    route = item.route
    _attempt_tuple(
        item.attempt_index,
        route.provider,
        route.region,
        route.route_role,
        route.route_id,
    )
    _ascii(route.model_id, "attempt.model_id", 128)
    if item.failure_code is None:
        expected_reason = {
            "KIMI": "stop",
            "OPENAI": "completed",
            "ANTHROPIC": "tool_use",
        }[route.provider]
        if (
            item.status != "SUCCEEDED"
            or item.retryable
            or item.security_failure
            or item.finish_reason != expected_reason
            or item.output_digest is None
        ):
            raise _projection("successful attempt projection is inconsistent")
        _digest(item.output_digest, "attempt.output_digest")
        if (item.input_tokens is None) != (item.output_tokens is None):
            raise _projection("attempt token pair is incomplete")
        if item.input_tokens is not None:
            _integer(item.input_tokens, "attempt.input_tokens", maximum=1_000_000)
            _integer(item.output_tokens, "attempt.output_tokens", maximum=1_000_000)
    else:
        code = item.failure_code
        if code in _LOCAL_ATTEMPT_FORBIDDEN or code not in _FAILURE_STATE:
            raise _projection("attempt projection failure code is invalid")
        if (
            item.status != _FAILURE_STATE[code]
            or item.retryable is not (code in _FALLBACK_CODES)
            or item.security_failure is not (code in _SECURITY_CODES)
            or any(
                value is not None
                for value in (
                    item.provider_request_id,
                    item.finish_reason,
                    item.output_digest,
                    item.input_tokens,
                    item.output_tokens,
                )
            )
        ):
            raise _projection("failed attempt projection is inconsistent")


def provenance_to_wire(
    started: Sequence[AttemptStartedEvidence],
    finished: Sequence[AttemptFinishedEvidence],
) -> list[dict[str, object]]:
    if type(started) not in (tuple, list) or type(finished) not in (tuple, list):
        raise _projection("provider attempt evidence is not an ordered sequence")
    if any(type(item) is not AttemptStartedEvidence for item in started) or any(
        type(item) is not AttemptFinishedEvidence for item in finished
    ):
        raise _projection("provider attempt evidence has invalid type")
    starts = {item.attempt_index: item for item in started}
    finishes = {item.attempt_index: item for item in finished}
    if len(starts) != len(started) or len(finishes) != len(finished):
        raise _projection("duplicate provider attempt index")
    if [item.attempt_index for item in started] != list(range(len(started))):
        raise _projection("provider attempt starts are out of order")
    if [item.attempt_index for item in finished] != list(range(len(finished))):
        raise _projection("provider attempt finishes are out of order")
    if set(finishes) != set(range(len(finishes))):
        raise _projection("finished provider attempt indexes are not contiguous")
    if set(starts) not in ({0}, {0, 1}, set()):
        raise _projection("started provider attempt indexes are not contiguous")
    common_digest: str | None = None
    common_request: str | None = None
    for index in sorted(starts):
        begin = starts[index]
        if type(begin) is not AttemptStartedEvidence:
            raise _projection("attempt start projection has invalid type")
        _attempt_tuple(
            begin.attempt_index,
            begin.route.provider,
            begin.route.region,
            begin.route.route_role,
            begin.route.route_id,
        )
        _identifier(begin.request_id, "attempt.request_id")
        _digest(begin.input_digest, "attempt.input_digest")
        timeout_cap = (
            15.0
            if begin.route.region == "CN"
            else (12.0 if begin.attempt_index == 0 else 8.0)
        )
        if (
            type(begin.timeout_s) not in (int, float)
            or not 0 < begin.timeout_s <= timeout_cap
        ):
            raise _projection("attempt timeout exceeds route cap")
        if common_digest is None:
            common_digest = begin.input_digest
            common_request = begin.request_id
        elif begin.input_digest != common_digest:
            raise _projection("provider attempts do not share one input digest")
        elif begin.request_id != common_request:
            raise _projection("provider attempts do not share one request")
    rows: list[dict[str, object]] = []
    for index in sorted(finishes):
        end = finishes[index]
        begin = starts.get(index)
        if (
            type(end) is not AttemptFinishedEvidence
            or begin is None
            or begin.request_id != end.request_id
            or begin.route != end.route
        ):
            raise _projection("provider attempt evidence does not pair")
        _validate_finished_projection(end)
        rows.append(
            {
                "attempt_index": index,
                "provider": begin.route.provider,
                "region": begin.route.region,
                "route_role": begin.route.route_role,
                "route_id": begin.route.route_id,
                "model_id": begin.route.model_id,
                "failure_code": end.failure_code,
                "retryable": end.retryable,
                "security_failure": end.security_failure,
                "provider_request_id": end.provider_request_id,
                "finish_reason": end.finish_reason,
                "input_digest": begin.input_digest,
                "output_digest": end.output_digest,
                "input_tokens": end.input_tokens,
                "output_tokens": end.output_tokens,
            }
        )
    if 1 in starts and (not rows or not _fallback_row(rows[0])):
        raise _projection("backup attempt lacks a fallback-eligible primary finish")
    return rows


def _fallback_row(row: Mapping[str, object]) -> bool:
    return (
        row["attempt_index"] == 0
        and row["provider"] == "OPENAI"
        and row["failure_code"] in _FALLBACK_CODES
    )


def _successful_terminal(rows: Sequence[Mapping[str, object]]) -> bool:
    return bool(rows) and rows[-1]["failure_code"] is None and (
        len(rows) == 1 or (len(rows) == 2 and _fallback_row(rows[0]))
    )


def _validate_terminal_provenance(
    state: str,
    failure_code: str | None,
    rows: Sequence[Mapping[str, object]],
    *,
    route_region: str,
    route_readiness: str,
) -> None:
    if state in {"SUCCEEDED", "NO_VALID_SUGGESTION"} or failure_code in _DOMAIN_INVALID_OUTPUT_CODES:
        if not _successful_terminal(rows):
            raise _projection("terminal success provenance is inconsistent")
        return
    if failure_code == "PROVIDER_UNCONFIGURED":
        if rows:
            raise _projection("unconfigured terminal must have zero attempts")
        return
    if failure_code == "INPUT_TOO_LARGE":
        local_after_primary = (
            len(rows) == 1
            and _fallback_row(rows[0])
            and route_region == "GLOBAL"
            and route_readiness == "READY"
        )
        if rows and not local_after_primary:
            raise _projection("local terminal provenance is inconsistent")
        return
    if failure_code == "DEADLINE_EXHAUSTED":
        local_after_primary = len(rows) == 1 and _fallback_row(rows[0])
        provider_terminal = bool(rows) and rows[-1]["failure_code"] == failure_code and (
            len(rows) == 1 or (len(rows) == 2 and _fallback_row(rows[0]))
        )
        if rows and not (local_after_primary or provider_terminal):
            raise _projection("deadline terminal provenance is inconsistent")
        return
    if failure_code == "BACKUP_UNCONFIGURED":
        if len(rows) != 1 or not _fallback_row(rows[0]):
            raise _projection("backup-unconfigured provenance is inconsistent")
        return
    if len(rows) == 1 and _fallback_row(rows[0]):
        raise _projection("global fallback terminal is incomplete")
    if failure_code not in _FAILURE_STATE or not rows:
        raise _projection("gateway terminal provenance is incomplete")
    if rows[-1]["failure_code"] != failure_code:
        raise _projection("terminal failure does not match final attempt")
    if len(rows) == 2 and not _fallback_row(rows[0]):
        raise _projection("fallback provenance sequence is invalid")
    if len(rows) not in {1, 2}:
        raise _projection("terminal provenance count is invalid")


def _validate_generation_route(item: GenerationProjectionView) -> None:
    row = (
        item.route_readiness,
        item.route_region,
        item.primary_provider,
        item.backup_provider,
    )
    legal = {
        ("READY", "CN", "KIMI", None),
        ("UNAVAILABLE", "CN", "KIMI", None),
        ("READY", "GLOBAL", "OPENAI", "ANTHROPIC"),
        ("DEGRADED_BACKUP_UNCONFIGURED", "GLOBAL", "OPENAI", None),
        ("UNAVAILABLE", "GLOBAL", "OPENAI", None),
        ("UNAVAILABLE", "GLOBAL", "OPENAI", "ANTHROPIC"),
    }
    if row not in legal:
        raise _projection("generation route tuple is invalid")
    if (item.primary_provider is None) != (item.primary_model_id is None) or (
        item.backup_provider is None
    ) != (item.backup_model_id is None):
        raise _projection("generation provider/model tuple is invalid")
    _ascii(item.primary_model_id, "generation.primary_model_id", 128)
    _ascii(
        item.backup_model_id,
        "generation.backup_model_id",
        128,
        optional=True,
    )


def _validate_generation_attempt_routes(item: GenerationProjectionView) -> None:
    for value in (*item.started_attempts, *item.finished_attempts):
        if value.request_id != item.generation_id:
            raise _projection("attempt does not match generation identity")
        expected = (
            (item.primary_provider, item.primary_model_id)
            if value.attempt_index == 0
            else (item.backup_provider, item.backup_model_id)
        )
        if (
            value.route.region != item.route_region
            or (value.route.provider, value.route.model_id) != expected
        ):
            raise _projection("attempt does not match reserved route")


def generation_to_wire(
    item: GenerationProjectionView,
    *,
    manager_response: dict[str, object] | None = None,
) -> dict[str, object]:
    if type(item) is not GenerationProjectionView:
        raise _projection("generation projection has invalid type")
    _identifier(item.generation_id, "generation_id")
    _identifier(item.request_id, "generation.request_id")
    _identifier(item.operation_event_id, "generation.operation_event_id")
    if type(item.service_date) is not date or len(item.basis_revisions) != 3:
        raise _projection("generation basis is incomplete")
    revisions = tuple(
        _integer(value, "generation.basis_revision")
        for value in item.basis_revisions
    )
    _digest(item.basis_digest, "generation.basis_digest")
    if item.retry_of is not None:
        _identifier(item.retry_of, "generation.retry_of")
    _validate_generation_route(item)
    _validate_generation_attempt_routes(item)
    candidates = [candidate_to_wire(value) for value in item.candidates]
    if len(candidates) > 2 or [row["candidate_index"] for row in candidates] != list(
        range(1, len(candidates) + 1)
    ):
        raise _projection("generation candidate sequence is invalid")
    gaps = [gap for row in candidates for gap in row["coverage_gaps"]]
    if len(gaps) > _MAX_PUBLIC_ITEMS:
        raise _projection("generation coverage gap maximum exceeded")
    provenance = provenance_to_wire(item.started_attempts, item.finished_attempts)
    state = item.lifecycle_state
    failure_code = item.failure_code
    terminal_states = {
        "SUCCEEDED",
        "NO_VALID_SUGGESTION",
        "UNAVAILABLE",
        "REFUSED",
        "INVALID_RESPONSE",
        "PROVIDER_ERROR",
        "CONFIGURATION_ERROR",
        "SECURITY_ERROR",
    }
    if state in terminal_states and {
        value.attempt_index for value in item.started_attempts
    } != {value.attempt_index for value in item.finished_attempts}:
        raise _projection("terminal provider attempt evidence is incomplete")
    if item.route_readiness == "UNAVAILABLE" and (
        item.started_attempts
        or item.finished_attempts
        or state not in {"RESERVED", "RESULT_UNKNOWN", "CONFIGURATION_ERROR"}
        or (
            state == "CONFIGURATION_ERROR"
            and failure_code != "PROVIDER_UNCONFIGURED"
        )
    ):
        raise _projection("unavailable route has impossible generation evidence")
    if state == "RESERVED":
        if item.started_attempts or item.finished_attempts or candidates or failure_code is not None:
            raise _projection("reserved generation projection is inconsistent")
    elif state == "IN_PROGRESS":
        if not item.started_attempts or candidates or failure_code is not None:
            raise _projection("in-progress generation projection is inconsistent")
    elif state == "RESULT_UNKNOWN":
        if candidates or failure_code not in {None, "RESULT_UNKNOWN"}:
            raise _projection("result-unknown generation projection is inconsistent")
        failure_code = "RESULT_UNKNOWN"
    elif state == "SUCCEEDED":
        if not candidates or not any(row["status"] == "VALID" for row in candidates) or failure_code is not None:
            raise _projection("successful generation projection is inconsistent")
        _validate_terminal_provenance(
            state,
            None,
            provenance,
            route_region=item.route_region,
            route_readiness=item.route_readiness,
        )
    elif state == "NO_VALID_SUGGESTION":
        if any(row["status"] != "REJECTED" for row in candidates) or failure_code not in {
            None,
            "NO_VALID_SUGGESTION",
        }:
            raise _projection("no-valid-suggestion projection is inconsistent")
        failure_code = "NO_VALID_SUGGESTION"
        _validate_terminal_provenance(
            state,
            failure_code,
            provenance,
            route_region=item.route_region,
            route_readiness=item.route_readiness,
        )
    elif state in {
        "UNAVAILABLE",
        "REFUSED",
        "INVALID_RESPONSE",
        "PROVIDER_ERROR",
        "CONFIGURATION_ERROR",
        "SECURITY_ERROR",
    }:
        if candidates or failure_code is None or _FAILURE_STATE.get(failure_code) != state:
            raise _projection("generation terminal/failure tuple is inconsistent")
        _validate_terminal_provenance(
            state,
            failure_code,
            provenance,
            route_region=item.route_region,
            route_readiness=item.route_readiness,
        )
    else:
        raise _projection("generation lifecycle is not a wire state")
    if manager_response is not None and state != "SUCCEEDED":
        raise _projection("manager response is attached to a nonsuccess generation")
    roster, exception_set, effective_plan = revisions
    return {
        "suggestion_id": item.generation_id,
        "request_id": item.request_id,
        "operation_id": item.operation_event_id,
        "state": state,
        "basis": {
            "roster": roster,
            "exception_set": exception_set,
            "effective_plan": effective_plan,
            "digest": item.basis_digest,
        },
        "retry_of": item.retry_of,
        "candidates": candidates,
        "coverage_gaps": gaps,
        "provenance": provenance,
        "failure_code": failure_code,
        "manager_response": manager_response,
    }


def _effective_plan_to_wire(
    revision: int | None,
    status: str,
    schedule_digest: str | None,
    schedule: Sequence[AssignmentProjection] | None,
) -> dict[str, object] | None:
    if status == "NO_PLAN":
        if revision not in {None, 0} or schedule is not None:
            raise _projection("NO_PLAN projection is inconsistent")
        return None
    if status not in {"CURRENT", "REVIEW_REQUIRED"}:
        raise _projection("effective plan status is invalid")
    if revision is None or schedule_digest is None or schedule is None:
        raise _projection("effective plan projection is incomplete")
    if len(schedule) > _MAX_PUBLIC_ITEMS:
        raise _projection("effective plan assignment maximum exceeded")
    return {
        "revision": _integer(revision, "effective_plan.revision", minimum=1),
        "status": status,
        "schedule_digest": _digest(
            schedule_digest, "effective_plan.schedule_digest"
        ),
        "assignments": [assignment_to_wire(value) for value in schedule],
    }


def manager_response_to_wire(item: ManagerResponseProjection) -> dict[str, object]:
    if type(item) is not ManagerResponseProjection:
        raise _projection("manager response projection has invalid type")
    _identifier(item.generation_id, "manager.generation_id")
    operator = _operator(item.operator)
    note = _plain_text(item.note, "manager.note", maximum=500, optional=True)
    if item.decision == "REJECT":
        if (
            item.reason_code
            not in {"MANUAL_HANDLING", "INSUFFICIENT_CONTEXT", "OTHER"}
            or item.effective_plan_revision is not None
            or item.schedule_digest is not None
            or item.effective_schedule is not None
        ):
            raise _projection("rejected manager response is inconsistent")
        effective = None
    elif item.decision in {"ACCEPT", "MODIFY"}:
        expected_reason = (
            "APPROVED" if item.decision == "ACCEPT" else "APPROVED_WITH_CHANGES"
        )
        if item.reason_code != expected_reason:
            raise _projection("manager response reason is inconsistent")
        effective = _effective_plan_to_wire(
            item.effective_plan_revision,
            "CURRENT",
            item.schedule_digest,
            item.effective_schedule,
        )
    else:
        raise _projection("manager response decision is invalid")
    return {
        "response_kind": item.decision,
        "reason_code": item.reason_code,
        "operator": operator,
        "note": note,
        "effective_plan": effective,
    }


def project_date_to_wire(
    projection: DateProjection,
    *,
    site_id: str,
    deployment_id: str,
    site_timezone: str,
) -> dict[str, object]:
    if type(projection) is not DateProjection:
        raise _projection("date projection has invalid type")
    if (
        projection.site_id,
        projection.deployment_id,
        projection.site_timezone,
    ) != (site_id, deployment_id, site_timezone):
        raise _projection("date projection identity does not match runtime")
    _identifier(site_id, "site_id")
    _identifier(deployment_id, "deployment_id")
    if type(projection.service_date) is not date:
        raise _projection("service date projection is invalid")
    for field_name, values in (
        ("assignment", projection.assignments),
        ("exception", projection.exceptions),
        ("generation", projection.generations),
        ("manager response", projection.manager_responses),
    ):
        if type(values) is not tuple or len(values) > _MAX_PUBLIC_ITEMS:
            raise _projection(f"date {field_name} maximum exceeded")
    exception_revision = _integer(
        projection.exception_set_revision, "exception_set_revision"
    )
    for name in ("worker_count", "assignment_count", "coverage_count"):
        _integer(getattr(projection, name), name)
    if projection.roster_revision is None:
        if any(
            value is not None
            for value in (
                projection.roster_digest,
                projection.roster_effective_from,
                projection.roster_effective_until,
            )
        ) or any(
            (
                projection.worker_count,
                projection.assignment_count,
                projection.coverage_count,
                len(projection.assignments),
                len(projection.exceptions),
            )
        ):
            raise _projection("empty roster projection is inconsistent")
        roster = None
        roster_revision = 0
    else:
        roster_revision = _integer(
            projection.roster_revision, "roster.revision", minimum=1
        )
        effective_from = _required(
            projection.roster_effective_from, "roster.effective_from"
        )
        if type(effective_from) is not date or (
            projection.roster_effective_until is not None
            and type(projection.roster_effective_until) is not date
        ):
            raise _projection("roster date projection is invalid")
        _digest(projection.roster_digest, "roster.digest")
        roster = {
            "revision": roster_revision,
            "effective_from_local_date": effective_from.isoformat(),
            "effective_until_local_date": (
                None
                if projection.roster_effective_until is None
                else projection.roster_effective_until.isoformat()
            ),
            "worker_count": projection.worker_count,
            "assignment_count": projection.assignment_count,
            "coverage_rule_count": projection.coverage_count,
        }
    effective = _effective_plan_to_wire(
        projection.effective_plan.revision,
        projection.effective_plan.status,
        projection.effective_plan.schedule_digest,
        projection.effective_plan_schedule,
    )
    responses: dict[str, ManagerResponseProjection] = {}
    for response in projection.manager_responses:
        if response.generation_id in responses:
            raise _projection("multiple manager responses for one generation")
        responses[response.generation_id] = response
    generations = []
    for value in projection.generations:
        if value.service_date != projection.service_date:
            raise _projection("generation service date does not match snapshot")
        response = responses.pop(value.generation_id, None)
        generations.append(
            generation_to_wire(
                value,
                manager_response=(
                    None if response is None else manager_response_to_wire(response)
                ),
            )
        )
    if responses:
        raise _projection("manager response has no generation projection")
    if any(
        value.service_date != projection.service_date
        for value in projection.exceptions
    ):
        raise _projection("exception service date does not match snapshot")
    return {
        "service_date": projection.service_date.isoformat(),
        "revisions": {
            "roster": roster_revision,
            "exception_set": exception_revision,
            "effective_plan": _integer(
                projection.effective_plan.revision, "effective_plan.revision"
            ),
        },
        "roster": roster,
        "assignments": [assignment_to_wire(value) for value in projection.assignments],
        "active_exceptions": [
            exception_to_wire(value) for value in projection.exceptions
        ],
        "effective_plan": effective,
        "generations": generations,
    }


def with_runtime_context(
    projection: DateProjection,
    configured: ConfiguredGateway,
    *,
    now: datetime,
    site_id: str,
    deployment_id: str,
    site_timezone: str,
) -> dict[str, Any]:
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise _projection("server clock is not timezone-aware")
    return {
        **project_date_to_wire(
            projection,
            site_id=site_id,
            deployment_id=deployment_id,
            site_timezone=site_timezone,
        ),
        "schema": "nxt-staffing/v1",
        "environment": "SIMULATION",
        "mode": "STAFFING_ADVISORY_ONLY",
        "server_time_utc": utc_time(now.astimezone(timezone.utc)),
        "context": {
            "site_id": site_id,
            "deployment_id": deployment_id,
            "site_timezone": site_timezone,
        },
        "generation_capability": generation_capability(configured),
    }


def _generation_record(item: GenerationProjectionView) -> dict[str, object]:
    wire = generation_to_wire(item)
    common = {
        "suggestion_id": wire["suggestion_id"],
        "service_date": item.service_date.isoformat(),
        "basis": wire["basis"],
        "retry_of": wire["retry_of"],
        "provenance": wire["provenance"],
    }
    state = wire["state"]
    if state == "RESERVED":
        return {"record_kind": "GENERATION_RESERVED", **common}
    if state == "IN_PROGRESS":
        return {"record_kind": "GENERATION_IN_PROGRESS", **common}
    if state == "RESULT_UNKNOWN":
        return {
            "record_kind": "GENERATION_INTERRUPTED",
            **common,
            "failure_code": "RESULT_UNKNOWN",
        }
    if state == "SUCCEEDED":
        return {
            "record_kind": "SUGGESTION_ISSUED",
            **common,
            "candidates": wire["candidates"],
            "coverage_gaps": wire["coverage_gaps"],
            "failure_code": None,
        }
    if state in {
        "NO_VALID_SUGGESTION",
        "UNAVAILABLE",
        "REFUSED",
        "INVALID_RESPONSE",
        "PROVIDER_ERROR",
        "CONFIGURATION_ERROR",
        "SECURITY_ERROR",
    }:
        return {
            "record_kind": "SUGGESTION_UNAVAILABLE",
            **common,
            "candidates": wire["candidates"],
            "coverage_gaps": wire["coverage_gaps"],
            "failure_code": _required(
                wire["failure_code"], "generation.failure_code"
            ),
        }
    raise _projection("generation lifecycle is not a receipt state")


def _receipt(
    projection: RequestProjection,
    disposition: str,
    operation_id: str,
    state: str,
    record: dict[str, object],
) -> dict[str, object]:
    if disposition not in {"created", "duplicate"}:
        raise _projection("receipt disposition is invalid")
    return {
        "schema": "nxt-staffing/v1",
        "disposition": disposition,
        "operation_kind": projection.operation_kind,
        "request_id": _identifier(projection.request_id, "request_id"),
        "operation_id": _identifier(operation_id, "operation_id"),
        "state": state,
        "record": record,
    }


def _validate_request_generation_evidence(
    projection: RequestProjection, generation: GenerationProjectionView
) -> None:
    if (
        projection.generation_id != generation.generation_id
        or projection.started_attempts != generation.started_attempts
        or projection.attempt_evidence != generation.finished_attempts
        or projection.candidates != generation.candidates
    ):
        raise _projection("request/generation evidence does not agree")
    _validate_generation_attempt_routes(generation)
    state = generation.lifecycle_state
    result = projection.result_evidence
    if state in {"RESERVED", "IN_PROGRESS", "RESULT_UNKNOWN"}:
        if result is not None:
            raise _projection("nonterminal request has terminal result evidence")
        return
    if type(result) is not ResultEvidence or result.request_id != generation.generation_id:
        raise _projection("terminal request lacks result evidence")
    if result.attempts != generation.finished_attempts:
        raise _projection("terminal result attempts do not match provenance")
    if generation.started_attempts and any(
        value.input_digest != result.input_digest
        for value in generation.started_attempts
    ):
        raise _projection("terminal result input digest does not match provenance")
    local_result = result.selected_provider is None and result.failure_code in {
        "INPUT_TOO_LARGE",
        "BACKUP_UNCONFIGURED",
        "PROVIDER_UNCONFIGURED",
        "DEADLINE_EXHAUSTED",
    }
    if result.failure_code == "DEADLINE_EXHAUSTED":
        provider_deadline = bool(result.attempts) and (
            result.attempts[-1].failure_code == "DEADLINE_EXHAUSTED"
        )
        if local_result == provider_deadline:
            raise _projection("deadline result does not match terminal provenance")
    if result.attempts and not local_result:
        final = result.attempts[-1]
        if result.selected_provider != final.route.provider or result.selected_model_id != final.route.model_id:
            raise _projection("terminal result selection does not match provenance")
        if (
            result.provider_request_id != final.provider_request_id
            or result.finish_reason != final.finish_reason
            or result.output_digest != final.output_digest
        ):
            raise _projection("terminal result metadata does not match provenance")
    elif any(
        value is not None
        for value in (
            result.selected_provider,
            result.selected_model_id,
            result.provider_request_id,
            result.finish_reason,
            result.output_digest,
        )
    ):
        raise _projection("zero-attempt result has provider metadata")
    expected_status = (
        "SUCCEEDED"
        if state in {"SUCCEEDED", "NO_VALID_SUGGESTION"}
        or generation.failure_code in _DOMAIN_INVALID_OUTPUT_CODES
        else state
    )
    if result.status != expected_status:
        raise _projection("terminal result status does not match lifecycle")
    if expected_status == "SUCCEEDED":
        if result.failure_code is not None:
            raise _projection("successful result has a failure code")
    elif result.failure_code != generation.failure_code:
        raise _projection("terminal result failure does not match lifecycle")
    if result.bounded_summary is not None:
        raise _projection("terminal result contains unsupported summary")
    if result.status != "SUCCEEDED" and result.candidate_count != 0:
        raise _projection("failed terminal result has a candidate_count")
    if state in {"SUCCEEDED", "NO_VALID_SUGGESTION"} and (
        result.candidate_count != len(generation.candidates)
    ):
        raise _projection("terminal result candidate_count does not match candidates")


def project_request_to_wire(
    projection: RequestProjection, *, disposition: str
) -> dict[str, object]:
    if type(projection) is not RequestProjection:
        raise _projection("request projection has invalid type")
    committed = projection.committed_record
    if (
        committed is None
        or committed.event_id not in projection.event_ids
        or len(projection.event_ids) != len(set(projection.event_ids))
    ):
        raise _projection("request projection lacks its committed event")
    utc_time(committed.occurred_at_utc)
    _digest(committed.request_digest, "request_digest")
    if projection.operation_kind == "roster-import" and type(committed) is RosterCommittedProjection:
        if projection.lifecycle_state != "COMMITTED":
            raise _projection("roster lifecycle is not committed")
        record = {
            "record_kind": "ROSTER_IMPORTED",
            "roster_revision": _integer(
                committed.roster_revision, "roster_revision", minimum=1
            ),
            "roster_digest": _digest(committed.roster_digest, "roster_digest"),
            "imported_at_utc": utc_time(committed.occurred_at_utc),
            "worker_count": _integer(
                committed.worker_count, "worker_count", minimum=1
            ),
            "assignment_count": _integer(
                committed.assignment_count, "assignment_count"
            ),
            "coverage_rule_count": _integer(
                committed.coverage_count, "coverage_count", minimum=1
            ),
        }
        return _receipt(projection, disposition, committed.event_id, "COMMITTED", record)
    if projection.operation_kind in {
        "exception-record",
        "exception-cancel",
        "exception-correct",
    } and type(committed) is ExceptionCommittedProjection:
        expected_event = {
            "exception-record": "exception_recorded",
            "exception-cancel": "exception_cancelled",
            "exception-correct": "exception_corrected",
        }[projection.operation_kind]
        if projection.lifecycle_state != "COMMITTED" or committed.event_type != expected_event:
            raise _projection("exception operation/event mismatch")
        revision = _integer(
            committed.exception_set_revision,
            "exception_set_revision",
            minimum=1,
        )
        if projection.operation_kind == "exception-record":
            if (
                committed.replacement is not None
                or committed.exception is None
                or committed.exception.exception_id != committed.exception_id
                or committed.exception_digest is None
            ):
                raise _projection("recorded exception identity is inconsistent")
            record = {
                "record_kind": "EXCEPTION_RECORDED",
                "exception": exception_to_wire(
                    _required(committed.exception, "exception-record.exception")
                ),
                "exception_set_revision": revision,
                "recorded_at_utc": utc_time(committed.occurred_at_utc),
            }
        elif projection.operation_kind == "exception-cancel":
            if committed.exception is not None or committed.replacement is not None:
                raise _projection("cancelled exception has replacement evidence")
            record = {
                "record_kind": "EXCEPTION_CANCELLED",
                "exception_id": _identifier(
                    committed.exception_id, "exception_id"
                ),
                "exception_set_revision": revision,
                "cancelled_at_utc": utc_time(committed.occurred_at_utc),
            }
        else:
            if (
                committed.exception is not None
                or committed.replacement is None
                or committed.replacement.exception_id != committed.exception_id
                or committed.exception_digest is None
            ):
                raise _projection("corrected exception has old active evidence")
            record = {
                "record_kind": "EXCEPTION_CORRECTED",
                "replaced_exception_id": _identifier(
                    committed.exception_id, "exception_id"
                ),
                "replacement": exception_to_wire(
                    _required(committed.replacement, "exception-correct.replacement")
                ),
                "exception_set_revision": revision,
                "corrected_at_utc": utc_time(committed.occurred_at_utc),
            }
        return _receipt(projection, disposition, committed.event_id, "COMMITTED", record)
    if projection.operation_kind == "suggestion-generate" and type(committed) is GenerationCommittedProjection:
        generation = projection.generation
        if type(generation) is not GenerationProjectionView or projection.lifecycle_state != generation.lifecycle_state:
            raise _projection("generation request projection is incomplete")
        if (
            committed.event_id != generation.operation_event_id
            or committed.generation_id != generation.generation_id
            or committed.service_date != generation.service_date
            or committed.basis_revisions != generation.basis_revisions
            or committed.basis_digest != generation.basis_digest
            or committed.retry_of != generation.retry_of
            or committed.route_region != generation.route_region
            or committed.route_readiness != generation.route_readiness
            or committed.primary_provider != generation.primary_provider
            or committed.backup_provider != generation.backup_provider
            or projection.request_id != generation.request_id
        ):
            raise _projection("generation reservation/projection mismatch")
        _validate_request_generation_evidence(projection, generation)
        return _receipt(
            projection,
            disposition,
            committed.event_id,
            generation.lifecycle_state,
            _generation_record(generation),
        )
    if projection.operation_kind == "manager-response" and type(committed) is ManagerCommittedProjection:
        manager = projection.manager_response
        if projection.lifecycle_state != "COMMITTED" or type(manager) is not ManagerResponseProjection:
            raise _projection("manager response projection is incomplete")
        if (
            manager.generation_id,
            manager.decision,
            manager.reason_code,
            manager.operator,
            manager.note,
            manager.effective_plan_revision,
            manager.schedule_digest,
            manager.effective_schedule,
        ) != (
            committed.generation_id,
            committed.decision,
            committed.reason_code,
            committed.operator,
            committed.note,
            committed.effective_plan_revision,
            committed.schedule_digest,
            committed.effective_schedule,
        ):
            raise _projection("manager record/projection mismatch")
        summary = manager_response_to_wire(manager)
        record = {
            "record_kind": "MANAGER_RESPONSE_COMMITTED",
            "suggestion_id": _identifier(
                committed.generation_id, "manager.generation_id"
            ),
            "response_kind": committed.decision,
            "reason_code": committed.reason_code,
            "operator": _operator(committed.operator),
            "note": _plain_text(
                committed.note, "manager.note", maximum=500, optional=True
            ),
            "effective_plan": summary["effective_plan"],
            "committed_at_utc": utc_time(committed.occurred_at_utc),
        }
        return _receipt(projection, disposition, committed.event_id, "COMMITTED", record)
    raise _projection("operation kind and committed record do not match")


def parse_service_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise SiteAgentError(
            "staffing_invalid_request", "service date is invalid"
        ) from None
    if parsed.isoformat() != value:
        raise SiteAgentError(
            "staffing_invalid_request", "service date is invalid"
        )
    return parsed


def _decode_segment(value: str) -> str:
    try:
        decoded = unquote(value, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, UnicodeEncodeError):
        raise SiteAgentError(
            "staffing_invalid_request", "request path is invalid"
        ) from None
    if not decoded or "/" in decoded or "\\" in decoded:
        raise SiteAgentError(
            "staffing_invalid_request", "request path is invalid"
        )
    return decoded


def _path_identifier(value: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None:
        raise SiteAgentError(
            "staffing_invalid_request", "request path is invalid"
        )
    return value


def with_matching_path_id(
    payload: dict[str, Any],
    field_name: str,
    path_value: str,
    *,
    expected_body_keys: frozenset[str],
) -> dict[str, Any]:
    if (
        type(payload) is not dict
        or set(payload) != expected_body_keys
        or field_name in payload
    ):
        raise SiteAgentError(
            "staffing_invalid_request", "request body is invalid"
        )
    detached = deepcopy(payload)
    detached[field_name] = path_value
    return detached


_CONFLICT_TO_HTTP_ERROR = {
    "IDEMPOTENCY_CONFLICT": (
        "staffing_conflict",
        "request_id is already bound to different content",
    ),
    "STALE_REQUEST": (
        "staffing_conflict",
        "request_id is already bound to different content",
    ),
    "INVALID_TRANSITION": (
        "staffing_conflict",
        "request_id is already bound to different content",
    ),
    "STALE_SUGGESTION": (
        "staffing_stale_suggestion",
        "staffing basis has changed",
    ),
}
_INVALID_INPUT_CODES = frozenset(
    {
        "staffing_invalid_request",
        "staffing_invalid_roster",
        "staffing_unknown_field",
        "staffing_identity_mismatch",
        "invalid_exception_time",
        "unknown_staff_or_shift",
    }
)


def staffing_error_for_conflict(conflict: ConflictReceipt) -> SiteAgentError:
    mapped = _CONFLICT_TO_HTTP_ERROR.get(conflict.code)
    if mapped is None:
        return SiteAgentError(
            "staffing_unavailable", "staffing evidence is unavailable"
        )
    return SiteAgentError(*mapped)


def public_domain_error(error: StaffingError) -> SiteAgentError:
    if error.code == "REQUEST_NOT_FOUND":
        return SiteAgentError(
            "staffing_request_not_found",
            "no committed request in verified evidence",
        )
    if error.code in {"staffing_roster_not_found", "UNKNOWN_GENERATION"}:
        return SiteAgentError(
            "staffing_not_found", "staffing resource was not found"
        )
    if error.code in _INVALID_INPUT_CODES:
        return SiteAgentError(
            "staffing_invalid_request", "request body is invalid"
        )
    return SiteAgentError(
        "staffing_unavailable", "staffing evidence is unavailable"
    )


def to_wire_receipt(
    owner: StaffingOperations, result: ReceiptResult
) -> dict[str, object]:
    if isinstance(result, ConflictReceipt):
        raise staffing_error_for_conflict(result)
    if isinstance(result, CommittedReceipt):
        disposition = "created"
    elif isinstance(result, DuplicateReceipt):
        disposition = "duplicate"
    else:
        raise _projection("unknown domain receipt variant")
    receipt = result.receipt
    projection = owner.request_projection(
        receipt.operation_kind, receipt.request_id
    )
    if (
        projection.committed_record is None
        or projection.committed_record.event_id != receipt.event_id
    ):
        raise _projection("receipt event does not match replay projection")
    wire = project_request_to_wire(projection, disposition=disposition)
    if (
        wire["operation_kind"] != receipt.operation_kind
        or wire["request_id"] != receipt.request_id
        or wire["operation_id"] != receipt.event_id
    ):
        raise _projection("wire receipt identity does not match domain receipt")
    return wire


class StaffingRouteAdapter:
    def __init__(
        self,
        *,
        owner: StaffingOperations,
        worker: "BoundedGenerationWorker",
        configured: ConfiguredGateway,
        site_id: str,
        deployment_id: str,
        site_timezone: str,
        audit_clock: Callable[[], datetime],
        zone: ZoneInfo | None = None,
    ) -> None:
        self._owner = owner
        self._worker = worker
        self._configured = configured
        self._site_id = site_id
        self._deployment_id = deployment_id
        self._site_timezone = site_timezone
        self._zone = ZoneInfo(site_timezone) if zone is None else zone
        self._audit_clock = audit_clock

    def dispatch(
        self, method: str, path: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if type(path) is not str or not path.startswith("/"):
            raise SiteAgentError(
                "staffing_not_found", "staffing resource was not found"
            )
        raw_parts = tuple(path.split("/")[1:])
        try:
            parts = tuple(_decode_segment(part) for part in raw_parts)
        except SiteAgentError:
            raise
        now = self._audit_clock()
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
            raise _projection("audit clock is not timezone-aware")
        if method == "GET" and parts == ("api", "v1", "staffing"):
            service_date = now.astimezone(self._zone).date()
            projection = self._owner.date_projection(service_date)
            if (
                type(projection) is not DateProjection
                or projection.service_date != service_date
            ):
                raise _projection("date lookup identity does not match projection")
            return with_runtime_context(
                projection,
                self._configured,
                now=now,
                site_id=self._site_id,
                deployment_id=self._deployment_id,
                site_timezone=self._site_timezone,
            )
        if (
            method == "GET"
            and len(parts) == 5
            and parts[:4] == ("api", "v1", "staffing", "dates")
        ):
            service_date = parse_service_date(parts[4])
            projection = self._owner.date_projection(service_date)
            if (
                type(projection) is not DateProjection
                or projection.service_date != service_date
            ):
                raise _projection("date lookup identity does not match projection")
            return with_runtime_context(
                projection,
                self._configured,
                now=now,
                site_id=self._site_id,
                deployment_id=self._deployment_id,
                site_timezone=self._site_timezone,
            )
        if (
            method == "GET"
            and len(parts) == 6
            and parts[:4] == ("api", "v1", "staffing", "requests")
        ):
            if parts[4] not in _OPERATION_KINDS:
                raise SiteAgentError(
                    "staffing_invalid_request", "request body is invalid"
                )
            request_id = _path_identifier(parts[5])
            projection = self._owner.request_projection(parts[4], request_id)
            if (
                type(projection) is not RequestProjection
                or projection.operation_kind != parts[4]
                or projection.request_id != request_id
            ):
                raise _projection("request lookup identity does not match projection")
            return project_request_to_wire(
                projection,
                disposition="duplicate",
            )
        if method == "POST" and parts == (
            "api",
            "v1",
            "staffing",
            "roster-imports",
        ):
            return to_wire_receipt(
                self._owner,
                self._owner.import_roster(payload, recorded_at=now),
            )
        if method == "POST" and parts == (
            "api",
            "v1",
            "staffing",
            "exceptions",
        ):
            return to_wire_receipt(
                self._owner,
                self._owner.record_exception(payload, recorded_at=now),
            )
        if (
            method == "POST"
            and len(parts) == 6
            and parts[:4] == ("api", "v1", "staffing", "exceptions")
            and parts[5] in {"cancel", "correct"}
        ):
            path_id = _path_identifier(parts[4])
            expected_keys = (
                _EXCEPTION_CANCEL_BODY_KEYS
                if parts[5] == "cancel"
                else _EXCEPTION_CORRECT_BODY_KEYS
            )
            body = with_matching_path_id(
                payload,
                "exception_id",
                path_id,
                expected_body_keys=expected_keys,
            )
            operation = (
                self._owner.cancel_exception
                if parts[5] == "cancel"
                else self._owner.correct_exception
            )
            return to_wire_receipt(
                self._owner, operation(body, recorded_at=now)
            )
        if method == "POST" and parts == (
            "api",
            "v1",
            "staffing",
            "suggestions",
        ):
            return self._worker.submit(payload)
        if (
            method == "POST"
            and len(parts) == 6
            and parts[:4] == ("api", "v1", "staffing", "suggestions")
            and parts[5] in {"accept", "modify", "reject"}
        ):
            if "suggestion_id" in payload or "generation_id" in payload:
                raise SiteAgentError(
                    "staffing_invalid_request", "request body is invalid"
                )
            decision = {
                "accept": "ACCEPT",
                "modify": "MODIFY",
                "reject": "REJECT",
            }[parts[5]]
            if payload.get("kind") != decision:
                raise SiteAgentError(
                    "staffing_invalid_request", "request body is invalid"
                )
            return to_wire_receipt(
                self._owner,
                self._owner.commit_manager_response(
                    payload,
                    suggestion_id=_path_identifier(parts[4]),
                    recorded_at=now,
                ),
            )
        raise SiteAgentError(
            "staffing_not_found", "staffing resource was not found"
        )


@dataclass(frozen=True, slots=True)
class GenerationWorkItem:
    request_id: str
    operation_id: str
    generation_id: str


def candidate_count_hint(output: object | None) -> int:
    try:
        if output is None or type(output) not in (dict, MappingProxyType):
            return 0
        candidates = output.get("candidates")
        if type(candidates) not in (list, tuple) or len(candidates) > 2:
            return 0
        return len(candidates)
    except Exception:
        return 0


class BoundedGenerationWorker:
    MAX_ACTIVE = 1
    MAX_WAITING = 4
    JOIN_TIMEOUT_S = 25.0

    def __init__(
        self,
        *,
        owner: StaffingOperations,
        configured: ConfiguredGateway,
        settings: StaffingProviderSettings,
        audit_clock: Callable[[], datetime],
        nonce_factory: Callable[[], bytes],
        start: bool = True,
    ) -> None:
        self._owner = owner
        self._configured = configured
        self._settings = settings
        self._audit_clock = audit_clock
        self._nonce_factory = nonce_factory
        self._condition = threading.Condition()
        self._waiting: deque[GenerationWorkItem] = deque()
        self._active: GenerationWorkItem | None = None
        self._closing = False
        self._failed_closed = False
        self._close_interrupt_admitted: str | None = None
        self._nonce_digests: set[bytes] = set()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="staffing-generation",
            daemon=True,
        )
        self._started = False
        if start:
            self.start()

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            if self._closing:
                raise StaffingShutdownError("staffing shutdown incomplete")
            self._thread.start()
            self._started = True

    def submit(self, request: Mapping[str, object]) -> dict[str, object]:
        return self._admit_and_reserve(request)

    def _mark_failed_closed(self) -> None:
        with self._condition:
            self._mark_failed_closed_locked()

    def _mark_failed_closed_locked(self) -> None:
        self._failed_closed = True
        self._condition.notify_all()

    def _fresh_nonce(self) -> bytes:
        try:
            nonce = self._nonce_factory()
        except Exception:
            self._mark_failed_closed_locked()
            raise SiteAgentError(
                "staffing_unavailable", "staffing evidence is unavailable"
            ) from None
        if type(nonce) is not bytes or len(nonce) != 32:
            self._mark_failed_closed_locked()
            raise SiteAgentError(
                "staffing_unavailable", "staffing evidence is unavailable"
            )
        digest = hashlib.sha256(b"staffing-nonce-reuse-v1\0" + nonce).digest()
        if digest in self._nonce_digests:
            self._mark_failed_closed_locked()
            raise SiteAgentError(
                "staffing_unavailable", "staffing evidence is unavailable"
            )
        self._nonce_digests.add(digest)
        return nonce

    def _admit_and_reserve(
        self, request: Mapping[str, object]
    ) -> dict[str, object]:
        with self._condition:
            if self._failed_closed or self._closing:
                raise SiteAgentError(
                    "staffing_unavailable", "staffing evidence is unavailable"
                )
            prior = self._owner.probe_request("suggestion-generate", request)
            if isinstance(prior, DuplicateReceipt):
                return to_wire_receipt(self._owner, prior)
            if isinstance(prior, ConflictReceipt):
                raise staffing_error_for_conflict(prior)
            route_evidence = generation_route_evidence(
                self._configured, self._settings
            )
            if route_evidence is None:
                raise SiteAgentError(
                    "staffing_unavailable", "staffing evidence is unavailable"
                )
            occupied = (1 if self._active is not None else 0) + len(
                self._waiting
            )
            if occupied >= self.MAX_ACTIVE + self.MAX_WAITING:
                raise SiteAgentError(
                    "staffing_busy", "generation capacity is full"
                )
            nonce = self._fresh_nonce()
            result = self._owner.reserve_generation(
                request,
                alias_nonce=nonce,
                route_evidence=route_evidence,
                prompt_template_version=PROMPT_TEMPLATE_VERSION,
                language=self._settings.language,
                recorded_at=self._audit_clock(),
            )
            created = isinstance(result, CommittedReceipt)
            try:
                receipt = to_wire_receipt(self._owner, result)
                disposition = receipt["disposition"]
                if disposition == "duplicate":
                    if created:
                        raise _projection(
                            "created reservation projected as a duplicate"
                        )
                    return receipt
                if not created or disposition != "created":
                    raise _projection("reservation receipt disposition is invalid")
                record = receipt["record"]
                if type(record) is not dict:
                    raise _projection("reservation receipt record is invalid")
                item = GenerationWorkItem(
                    request_id=str(receipt["request_id"]),
                    operation_id=str(receipt["operation_id"]),
                    generation_id=str(record["suggestion_id"]),
                )
                if self._active is None:
                    self._active = item
                else:
                    self._waiting.append(item)
                self._condition.notify()
                return receipt
            except BaseException:
                if created:
                    self._mark_failed_closed_locked()
                raise

    def _is_unconfigured_route(self) -> bool:
        readiness = self._configured.readiness
        return bool(
            readiness is not None
            and readiness.status is RouteReadinessStatus.UNAVAILABLE
            and readiness.failure_code is FailureCode.PROVIDER_UNCONFIGURED
        )

    def _commit_local_failure(
        self,
        item: GenerationWorkItem,
        work: GenerationProjection,
        code: FailureCode,
    ) -> None:
        evidence = ResultEvidence(
            request_id=item.generation_id,
            status="CONFIGURATION_ERROR",
            failure_code=code.value,
            selected_provider=None,
            selected_model_id=None,
            provider_request_id=None,
            finish_reason=None,
            input_digest=work.input_digest,
            output_digest=None,
            attempts=(),
            candidate_count=0,
            bounded_summary=None,
        )
        self._owner.commit_generation_result(
            item.generation_id,
            evidence,
            None,
            recorded_at=self._audit_clock(),
        )

    @staticmethod
    def _canonical_request(
        item: GenerationWorkItem, work: GenerationProjection, region: DeploymentRegion
    ) -> tuple[GenerationRequest | None, bool]:
        semantic = canonical_generation_input(
            work.basis_snapshot,
            work.provider_payload,
            PROMPT_TEMPLATE_VERSION,
        )
        if not isinstance(semantic, Mapping) or set(semantic) != {
            "template_version",
            "messages",
            "output_schema",
            "max_output_tokens",
        }:
            raise _projection("canonical generation input shape drifted")
        if stable_digest(semantic) != work.input_digest:
            raise _projection("canonical generation input digest mismatch")
        if (
            semantic["template_version"] != PROMPT_TEMPLATE_VERSION
            or semantic["max_output_tokens"] != 2048
            or stable_digest(semantic["output_schema"])
            != stable_digest(STAFFING_SUGGESTION_OUTPUT_SCHEMA)
            or type(semantic["messages"]) is not tuple
        ):
            raise _projection("canonical generation input contract drifted")
        if not 1 <= len(semantic["messages"]) <= 64:
            raise _projection("canonical generation message count drifted")
        validated_rows: list[tuple[MessageRole, str]] = []
        oversized = False
        for row in semantic["messages"]:
            if not isinstance(row, Mapping) or set(row) != {"role", "content"}:
                raise _projection("canonical generation message shape drifted")
            role = row["role"]
            content = row["content"]
            if type(role) is not str or type(content) is not str or not content:
                raise _projection("canonical generation message value drifted")
            try:
                message_role = MessageRole(role)
                content.encode("utf-8")
            except (ValueError, UnicodeError):
                raise _projection("canonical generation message value drifted") from None
            if len(content) > 32768:
                oversized = True
            validated_rows.append((message_role, content))
        if oversized:
            return None, True
        messages = [
            GenerationMessage(role=role, content=content)
            for role, content in validated_rows
        ]
        request = GenerationRequest(
            request_id=item.generation_id,
            template_version=PROMPT_TEMPLATE_VERSION,
            messages=tuple(messages),
            output_schema=STAFFING_SUGGESTION_OUTPUT_SCHEMA,
            max_output_tokens=2048,
            deadline_budget_s=(
                15.0 if region is DeploymentRegion.CN else 20.0
            ),
        )
        if request.canonical_input_digest != work.input_digest:
            raise _projection("gateway/domain canonical digest mismatch")
        return request, False

    def _execute(self, item: GenerationWorkItem) -> None:
        try:
            work = self._owner.generation_work(item.generation_id)
            if self._is_unconfigured_route():
                self._commit_local_failure(
                    item, work, FailureCode.PROVIDER_UNCONFIGURED
                )
                return
            route = self._configured.route
            if route is None:
                raise _projection("configured route is missing")
            generation_request, oversized = self._canonical_request(
                item, work, route.region
            )
            if oversized:
                self._commit_local_failure(
                    item, work, FailureCode.INPUT_TOO_LARGE
                )
                return
        except Exception:
            self._mark_failed_closed()
            return
        except BaseException:
            self._mark_failed_closed()
            raise
        assert generation_request is not None
        observer = LedgerAttemptObserver(
            owner=self._owner,
            generation_id=item.generation_id,
            route=route,
            clock=self._audit_clock,
        )
        try:
            result = self._configured.gateway.generate(
                generation_request,
                route,
                observer=observer,
            )
        except AttemptObserverError:
            try:
                self._owner.interrupt_generation(
                    item.generation_id,
                    recorded_at=self._audit_clock(),
                )
            except Exception:
                self._mark_failed_closed()
            except BaseException:
                self._mark_failed_closed()
                raise
            return
        except Exception:
            self._mark_failed_closed()
            return
        except BaseException:
            self._mark_failed_closed()
            raise
        try:
            count = candidate_count_hint(result.output)
            evidence = result_evidence(
                result,
                region=route.region,
                candidate_count=count,
            )
            self._owner.commit_generation_result(
                item.generation_id,
                evidence,
                result.output,
                recorded_at=self._audit_clock(),
            )
        except Exception:
            self._mark_failed_closed()
        except BaseException:
            self._mark_failed_closed()
            raise

    def _thread_main(self) -> None:
        try:
            self._run()
        except BaseException:
            self._mark_failed_closed()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._active is not None
                    or self._closing
                    or self._failed_closed
                )
                if self._active is None and (
                    self._closing or self._failed_closed
                ):
                    return
                item = self._active
            assert item is not None
            try:
                self._execute(item)
            finally:
                with self._condition:
                    if self._active == item:
                        self._active = None
                    if (
                        not self._closing
                        and not self._failed_closed
                        and self._waiting
                    ):
                        self._active = self._waiting.popleft()
                    self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closing = True
            detached = tuple(self._waiting)
            self._waiting.clear()
            self._condition.notify_all()
        fatal_error: BaseException | None = None
        for item in detached:
            with self._condition:
                if self._failed_closed:
                    break
                self._close_interrupt_admitted = item.generation_id
            try:
                self._owner.interrupt_generation(
                    item.generation_id,
                    recorded_at=self._audit_clock(),
                )
            except Exception:
                self._mark_failed_closed()
                break
            except BaseException as error:
                self._mark_failed_closed()
                fatal_error = error
                break
            finally:
                with self._condition:
                    if self._close_interrupt_admitted == item.generation_id:
                        self._close_interrupt_admitted = None
                    self._condition.notify_all()
        if self._started:
            self._thread.join(timeout=self.JOIN_TIMEOUT_S)
        if self._started and self._thread.is_alive():
            self._mark_failed_closed()
            raise StaffingShutdownError(
                "staffing shutdown incomplete"
            ) from None
        if fatal_error is not None:
            raise fatal_error


class StaffingApiOperations:
    def __init__(
        self,
        *,
        router: StaffingRouteAdapter,
        worker: BoundedGenerationWorker,
        ledger: StaffingLedger,
    ) -> None:
        self._router = router
        self._worker = worker
        self._ledger = ledger
        self._closed = False
        self._ledger_closed = False
        self._close_lock = threading.Lock()

    def route(
        self, method: str, path: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if self._closed:
            raise SiteAgentError(
                "staffing_unavailable", "staffing evidence is unavailable"
            )
        try:
            return self._router.dispatch(method, path, deepcopy(payload))
        except SiteAgentError:
            raise
        except StaffingError as error:
            raise public_domain_error(error) from None
        except Exception:
            raise SiteAgentError(
                "staffing_unavailable", "staffing evidence is unavailable"
            ) from None

    def close(self) -> None:
        with self._close_lock:
            if self._ledger_closed:
                return
            self._closed = True
            self._worker.close()
            try:
                self._ledger.close()
            except Exception:
                raise StaffingShutdownError(
                    "staffing shutdown incomplete"
                ) from None
            self._ledger_closed = True


def build_staffing_operations(
    state_root: Path,
    *,
    site_id: str,
    deployment_id: str,
    site_timezone: str,
    volatile_out: Path,
    settings: StaffingProviderSettings,
    audit_clock: Callable[[], datetime],
    monotonic: Callable[[], float],
    nonce_factory: Callable[[], bytes],
) -> StaffingApiOperations:
    root = resolve_staffing_root(
        state_root,
        site_id=site_id,
        deployment_id=deployment_id,
        volatile_out=volatile_out,
    )
    ledger: StaffingLedger | None = None
    worker: BoundedGenerationWorker | None = None
    try:
        ledger = StaffingLedger(
            root,
            site_id=site_id,
            deployment_id=deployment_id,
        )
        owner = StaffingOperations(
            ledger,
            site_id=site_id,
            deployment_id=deployment_id,
            site_timezone=site_timezone,
        )
        zone = ZoneInfo(site_timezone)
        configured = configured_gateway(settings, monotonic=monotonic)
        owner.recover_interrupted_generations(recorded_at=audit_clock())
        worker = BoundedGenerationWorker(
            owner=owner,
            configured=configured,
            settings=settings,
            audit_clock=audit_clock,
            nonce_factory=nonce_factory,
            start=False,
        )
        router = StaffingRouteAdapter(
            owner=owner,
            worker=worker,
            configured=configured,
            site_id=site_id,
            deployment_id=deployment_id,
            site_timezone=site_timezone,
            audit_clock=audit_clock,
            zone=zone,
        )
        api = StaffingApiOperations(
            router=router,
            worker=worker,
            ledger=ledger,
        )
        worker.start()
        return api
    except BaseException:
        if worker is not None:
            try:
                worker.close()
            except BaseException:
                raise StaffingShutdownError(
                    "staffing shutdown incomplete"
                ) from None
        if ledger is not None:
            try:
                ledger.close()
            except BaseException:
                raise StaffingShutdownError(
                    "staffing shutdown incomplete"
                ) from None
        raise


__all__ = (
    "BoundedGenerationWorker",
    "ConfiguredGateway",
    "GenerationWorkItem",
    "LedgerAttemptObserver",
    "StaffingApiOperations",
    "StaffingProjectionError",
    "StaffingProviderSettings",
    "StaffingRouteAdapter",
    "StaffingShutdownError",
    "build_staffing_operations",
    "configured_gateway",
    "generation_capability",
    "generation_route_evidence",
    "load_provider_settings",
    "project_date_to_wire",
    "project_request_to_wire",
    "resolve_staffing_root",
)
