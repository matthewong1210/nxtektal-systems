"""Regional provider orchestration over the neutral adapter contracts."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn

from .adapters import AdapterOutcome, ProviderAdapter
from .contracts import (
    AttemptObserver, AttemptObserverError, AttemptRecord, AttemptStarted, DeploymentRegion, FailureCode,
    GatewayContractError, GenerationRequest, GenerationResult, GenerationStatus, Provider,
)


GLOBAL_FALLBACK_CODES = frozenset({
    FailureCode.DNS_FAILURE,
    FailureCode.CONNECT_TIMEOUT,
    FailureCode.CONNECT_FAILED,
    FailureCode.READ_TIMEOUT,
    FailureCode.CONNECTION_INTERRUPTED,
    FailureCode.HTTP_TIMEOUT,
    FailureCode.RATE_LIMITED,
    FailureCode.PROVIDER_UNAVAILABLE,
})


class RouteReadinessStatus(StrEnum):
    READY = "READY"
    DEGRADED_BACKUP_UNCONFIGURED = "DEGRADED_BACKUP_UNCONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    region: DeploymentRegion

    def __post_init__(self) -> None:
        if not isinstance(self.region, DeploymentRegion):
            raise GatewayContractError(FailureCode.PROVIDER_UNCONFIGURED,
                                       "invalid deployment region")


@dataclass(frozen=True, slots=True)
class RouteReadiness:
    region: DeploymentRegion
    status: RouteReadinessStatus
    primary_provider: Provider | None
    backup_provider: Provider | None
    failure_code: FailureCode | None


class ModelGateway:
    __slots__ = ("_kimi", "_openai", "_anthropic", "_monotonic")

    def __init__(self, *, kimi: ProviderAdapter | None = None,
                 openai: ProviderAdapter | None = None,
                 anthropic: ProviderAdapter | None = None,
                 monotonic: Callable[[], float]):
        for adapter, provider in (
            (kimi, Provider.KIMI), (openai, Provider.OPENAI),
            (anthropic, Provider.ANTHROPIC),
        ):
            if adapter is not None and adapter.provider is not provider:
                raise GatewayContractError(
                    FailureCode.PROVIDER_UNCONFIGURED,
                    "adapter provider does not match configured slot",
                )
        self._kimi = kimi
        self._openai = openai
        self._anthropic = anthropic
        self._monotonic = monotonic

    def readiness(self, route: RoutePolicy) -> RouteReadiness:
        if route.region is DeploymentRegion.CN:
            configured = self._kimi is not None
            return RouteReadiness(
                route.region,
                RouteReadinessStatus.READY if configured else RouteReadinessStatus.UNAVAILABLE,
                Provider.KIMI, None,
                None if configured else FailureCode.PROVIDER_UNCONFIGURED,
            )
        backup = Provider.ANTHROPIC if self._anthropic is not None else None
        if self._openai is None:
            return RouteReadiness(route.region, RouteReadinessStatus.UNAVAILABLE,
                                  Provider.OPENAI, backup, FailureCode.PROVIDER_UNCONFIGURED)
        return RouteReadiness(
            route.region,
            RouteReadinessStatus.READY if backup else RouteReadinessStatus.DEGRADED_BACKUP_UNCONFIGURED,
            Provider.OPENAI, backup, None if backup else FailureCode.BACKUP_UNCONFIGURED,
        )

    def generate(self, request: GenerationRequest, route: RoutePolicy, *,
                 observer: AttemptObserver) -> GenerationResult:
        if observer is None:
            raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST,
                                       "attempt observer is required")
        started = self._monotonic()
        is_cn = route.region is DeploymentRegion.CN
        absolute_deadline = started + min(request.deadline_budget_s, 15.0 if is_cn else 20.0)
        completed: list[AttemptRecord] = []
        adapter = self._kimi if is_cn else self._openai
        if adapter is None:
            return _local_result(request, completed, GenerationStatus.CONFIGURATION_ERROR,
                                 FailureCode.PROVIDER_UNCONFIGURED)
        adapters = (adapter,) if is_cn else (adapter, self._anthropic)
        for index, adapter in enumerate(adapters):
            if absolute_deadline - self._monotonic() <= 0:
                return _local_result(request, completed, GenerationStatus.UNAVAILABLE,
                                     FailureCode.DEADLINE_EXHAUSTED)
            if adapter is None:
                return _local_result(request, completed, GenerationStatus.UNAVAILABLE,
                                     FailureCode.BACKUP_UNCONFIGURED)
            try:
                prepared = adapter.prepare(request)
            except GatewayContractError as error:
                if error.code is not FailureCode.INPUT_TOO_LARGE:
                    raise
                return _local_result(request, completed, GenerationStatus.CONFIGURATION_ERROR,
                                     FailureCode.INPUT_TOO_LARGE)
            if (prepared.request_id != request.request_id
                    or prepared.provider is not adapter.provider
                    or prepared.model_id != adapter.model_id
                    or prepared.input_digest != request.canonical_input_digest):
                raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST,
                                           "prepared request identity does not match route")
            remaining = absolute_deadline - self._monotonic()
            if remaining <= 0:
                return _local_result(request, completed, GenerationStatus.UNAVAILABLE,
                                     FailureCode.DEADLINE_EXHAUSTED)
            cap = 15.0 if is_cn else (12.0 if index == 0 else 8.0)
            timeout = min(remaining, cap)
            attempt_started = AttemptStarted(
                request.request_id, index, prepared.provider, prepared.model_id,
                prepared.input_digest, timeout,
            )
            observer_failed = False
            try:
                observer.started(attempt_started)
            except Exception:
                observer_failed = True
            if observer_failed:
                _raise_observer_error(
                    request.request_id, index, prepared.provider, "started",
                )
            # The persisted timeout approves an upper bound. Observer time counts
            # against the overall budget; the network gets only what remains.
            before_send = self._monotonic()
            send_timeout = min(timeout, absolute_deadline - before_send)
            if send_timeout <= 0:
                outcome = _timeout_outcome(request, FailureCode.DEADLINE_EXHAUSTED)
            else:
                attempt_deadline = before_send + send_timeout
                outcome = adapter.send(prepared, timeout_s=send_timeout)
                returned = self._monotonic()
                if outcome.input_digest != request.canonical_input_digest:
                    raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST,
                                               "outcome input digest does not match request")
                if returned > absolute_deadline:
                    outcome = _timeout_outcome(request, FailureCode.DEADLINE_EXHAUSTED)
                elif returned > attempt_deadline:
                    outcome = _timeout_outcome(request, FailureCode.READ_TIMEOUT)
            record = AttemptRecord(
                request.request_id, index, prepared.provider, prepared.model_id, outcome.status,
                outcome.failure_code, outcome.retryable, outcome.security_failure,
                outcome.provider_request_id, outcome.finish_reason, outcome.usage,
                outcome.input_digest, outcome.output_digest,
            )
            observer_failed = False
            try:
                observer.finished(record)
            except Exception:
                observer_failed = True
            if observer_failed:
                _raise_observer_error(
                    request.request_id, index, prepared.provider, "finished",
                )
            completed.append(record)
            if is_cn or index == 1 or outcome.failure_code not in GLOBAL_FALLBACK_CODES:
                return _outcome_result(request, completed, outcome)
        raise AssertionError("route must terminate after its final provider")


def _raise_observer_error(request_id: str, index: int, provider: Provider,
                          phase: str) -> NoReturn:
    try:
        raise AttemptObserverError(request_id, index, provider, phase) from None
    except AttemptObserverError as safe_error:
        # Python attaches even a caller's active exception on the initial raise.
        # Clear it after attachment; bare re-raise preserves the cleared context.
        object.__setattr__(safe_error, "__context__", None)
        raise


def _timeout_outcome(request: GenerationRequest, code: FailureCode) -> AdapterOutcome:
    return AdapterOutcome(
        GenerationStatus.UNAVAILABLE, None, code, None, None, None,
        request.canonical_input_digest, None, code is FailureCode.READ_TIMEOUT, False,
    )


def _local_result(request: GenerationRequest, completed: list[AttemptRecord],
                  status: GenerationStatus, code: FailureCode) -> GenerationResult:
    return GenerationResult(
        request.request_id, status, None, code, None, None, None, None, None,
        request.canonical_input_digest, None, tuple(completed),
    )


def _outcome_result(request: GenerationRequest, completed: list[AttemptRecord],
                    outcome: AdapterOutcome) -> GenerationResult:
    last = completed[-1]
    return GenerationResult(
        request.request_id, outcome.status, outcome.decoded_json, outcome.failure_code,
        last.provider, last.model_id, outcome.provider_request_id, outcome.finish_reason,
        outcome.usage, request.canonical_input_digest, outcome.output_digest, tuple(completed),
    )
