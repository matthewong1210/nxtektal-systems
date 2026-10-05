"""Immutable provider-neutral generation contracts."""

from dataclasses import dataclass, field
from enum import StrEnum
import math
import re
from collections.abc import Mapping
from typing import Protocol


class Provider(StrEnum):
    KIMI = "KIMI"
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"


class DeploymentRegion(StrEnum):
    CN = "CN"
    GLOBAL = "GLOBAL"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class GenerationStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    UNAVAILABLE = "UNAVAILABLE"
    REFUSED = "REFUSED"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    SECURITY_ERROR = "SECURITY_ERROR"


class FailureCode(StrEnum):
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    UNSUPPORTED_CONTENT_ENCODING = "UNSUPPORTED_CONTENT_ENCODING"
    REDIRECT_REFUSED = "REDIRECT_REFUSED"
    ENDPOINT_NOT_ALLOWED = "ENDPOINT_NOT_ALLOWED"
    TLS_VERIFICATION_FAILED = "TLS_VERIFICATION_FAILED"
    DNS_FAILURE = "DNS_FAILURE"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    CONNECT_FAILED = "CONNECT_FAILED"
    READ_TIMEOUT = "READ_TIMEOUT"
    CONNECTION_INTERRUPTED = "CONNECTION_INTERRUPTED"
    HTTP_TIMEOUT = "HTTP_TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    INVALID_PROVIDER_REQUEST = "INVALID_PROVIDER_REQUEST"
    PROVIDER_CLIENT_ERROR = "PROVIDER_CLIENT_ERROR"
    PROVIDER_REFUSED = "PROVIDER_REFUSED"
    MALFORMED_PROVIDER_RESPONSE = "MALFORMED_PROVIDER_RESPONSE"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    BACKUP_UNCONFIGURED = "BACKUP_UNCONFIGURED"
    DEADLINE_EXHAUSTED = "DEADLINE_EXHAUSTED"
    PROVIDER_UNCONFIGURED = "PROVIDER_UNCONFIGURED"


@dataclass(frozen=True, slots=True)
class GatewayContractError(ValueError):
    code: FailureCode
    detail: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.code, FailureCode):
            raise ValueError("invalid contract failure code")
        if type(self.detail) is not str or not self.detail.strip() or len(self.detail) > 160:
            raise ValueError("invalid contract failure detail")

    def __str__(self) -> str:
        return f"{self.code.value}: {self.detail}"


def _invalid(detail: str) -> GatewayContractError:
    # Call sites supply repository-authored constants, never provider/user text.
    return GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, detail)


def _ascii_text(value: object, limit: int) -> bool:
    return (
        type(value) is str and 1 <= len(value) <= limit and bool(value.strip())
        and all(32 <= ord(character) <= 126 for character in value)
    )


def _request_id(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None:
        raise _invalid("invalid request identifier")


def _digest(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise _invalid("invalid content digest")


def _index(value: object) -> None:
    if type(value) is not int or value < 0:
        raise _invalid("invalid attempt index")


def _positive_time(value: object) -> None:
    if type(value) not in (int, float) or value <= 0:
        raise _invalid("invalid time budget")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise _invalid("invalid time budget")


def _provider_model(provider: object, model_id: object) -> None:
    if not isinstance(provider, Provider) or not _ascii_text(model_id, 128):
        raise _invalid("invalid provider or model identifier")


def _optional_metadata(provider_request_id: object, finish_reason: object, usage: object) -> None:
    for value in (provider_request_id, finish_reason):
        if value is not None and not _ascii_text(value, 160):
            raise _invalid("invalid provider metadata")
    if usage is not None and not isinstance(usage, TokenUsage):
        raise _invalid("invalid token usage")


def _outcome(status: object, failure_code: object, output_digest: object) -> None:
    if not isinstance(status, GenerationStatus):
        raise _invalid("invalid generation status")
    if status is GenerationStatus.SUCCEEDED:
        if failure_code is not None:
            raise _invalid("successful outcome cannot have a failure code")
        _digest(output_digest)
    elif not isinstance(failure_code, FailureCode) or output_digest is not None:
        raise _invalid("failed outcome requires a failure code and no output digest")


@dataclass(frozen=True, slots=True)
class GenerationMessage:
    role: MessageRole
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.role, MessageRole):
            raise _invalid("invalid message role")
        if type(self.content) is not str or not 1 <= len(self.content) <= 32768:
            raise _invalid("invalid message content length")
        try:
            self.content.encode("utf-8")
        except UnicodeError:
            raise _invalid("message content must be valid Unicode") from None


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider: Provider
    model_id: str
    api_key: str = field(repr=False)

    def __post_init__(self) -> None:
        _provider_model(self.provider, self.model_id)
        if not _ascii_text(self.api_key, 4096):
            raise _invalid("invalid provider credential")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in (
            self.input_tokens, self.output_tokens, self.total_tokens,
        )):
            raise _invalid("token counts must be nonnegative integers")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise _invalid("token total does not match component counts")


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    request_id: str
    template_version: str
    messages: tuple[GenerationMessage, ...] = field(repr=False)
    output_schema: Mapping[str, object] = field(repr=False)
    max_output_tokens: int
    deadline_budget_s: float
    canonical_input_digest: str = field(init=False)

    def __post_init__(self) -> None:
        from .serialization import (
            _canonical_tree, _freeze_json, _validate_local_object_schema, stable_digest,
        )

        _request_id(self.request_id)
        if not _ascii_text(self.template_version, 64):
            raise _invalid("invalid template version")
        if type(self.messages) is not tuple or not 1 <= len(self.messages) <= 64:
            raise _invalid("messages must be a tuple of one to sixty-four messages")
        if any(not isinstance(message, GenerationMessage) for message in self.messages):
            raise _invalid("invalid generation message")
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 16384:
            raise _invalid("invalid maximum output token count")
        _positive_time(self.deadline_budget_s)
        _validate_local_object_schema(self.output_schema)
        schema_copy = _canonical_tree(self.output_schema)
        normalized_messages = tuple(message for message in self.messages)
        semantic_payload = {
            "template_version": self.template_version,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in normalized_messages
            ],
            "output_schema": schema_copy,
            "max_output_tokens": self.max_output_tokens,
        }
        object.__setattr__(self, "canonical_input_digest", stable_digest(semantic_payload))
        object.__setattr__(self, "messages", normalized_messages)
        object.__setattr__(self, "output_schema", _freeze_json(schema_copy))


@dataclass(frozen=True, slots=True)
class AttemptStarted:
    request_id: str
    attempt_index: int
    provider: Provider
    model_id: str
    input_digest: str
    timeout_s: float

    def __post_init__(self) -> None:
        _request_id(self.request_id)
        _index(self.attempt_index)
        _provider_model(self.provider, self.model_id)
        _digest(self.input_digest)
        _positive_time(self.timeout_s)


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    request_id: str
    attempt_index: int
    provider: Provider
    model_id: str
    status: GenerationStatus
    failure_code: FailureCode | None
    retryable: bool
    security_failure: bool
    provider_request_id: str | None
    finish_reason: str | None
    usage: TokenUsage | None
    input_digest: str
    output_digest: str | None

    def __post_init__(self) -> None:
        _request_id(self.request_id)
        _index(self.attempt_index)
        _provider_model(self.provider, self.model_id)
        _digest(self.input_digest)
        _outcome(self.status, self.failure_code, self.output_digest)
        if type(self.retryable) is not bool or type(self.security_failure) is not bool:
            raise _invalid("attempt flags must be booleans")
        _optional_metadata(self.provider_request_id, self.finish_reason, self.usage)


@dataclass(frozen=True, slots=True)
class AttemptObserverError(Exception):
    request_id: str
    attempt_index: int
    provider: Provider
    phase: str

    def __post_init__(self) -> None:
        _request_id(self.request_id)
        _index(self.attempt_index)
        if not isinstance(self.provider, Provider) or self.phase not in ("started", "finished"):
            raise _invalid("invalid attempt observer context")
        # Suppress the original observer exception when raised inside its handler.
        object.__setattr__(self, "__suppress_context__", True)

    def __str__(self) -> str:
        return (
            f"attempt observer failed: provider={self.provider.value} "
            f"index={self.attempt_index} phase={self.phase}"
        )


@dataclass(frozen=True, slots=True)
class GenerationResult:
    request_id: str
    status: GenerationStatus
    output: Mapping[str, object] | None = field(repr=False)
    failure_code: FailureCode | None
    selected_provider: Provider | None
    selected_model_id: str | None
    provider_request_id: str | None
    finish_reason: str | None
    usage: TokenUsage | None
    input_digest: str
    output_digest: str | None
    attempts: tuple[AttemptRecord, ...]

    def __post_init__(self) -> None:
        from .serialization import _freeze_json, stable_digest

        _request_id(self.request_id)
        _digest(self.input_digest)
        _outcome(self.status, self.failure_code, self.output_digest)
        _optional_metadata(self.provider_request_id, self.finish_reason, self.usage)
        if self.selected_provider is None:
            if self.selected_model_id is not None:
                raise _invalid("selected provider and model must agree")
        else:
            _provider_model(self.selected_provider, self.selected_model_id)
        if type(self.attempts) is not tuple:
            raise _invalid("attempts must be an ordered tuple")
        for index, attempt in enumerate(self.attempts):
            if not isinstance(attempt, AttemptRecord):
                raise _invalid("invalid attempt record")
            if (attempt.attempt_index != index or attempt.request_id != self.request_id
                    or attempt.input_digest != self.input_digest):
                raise _invalid("attempt identity or order does not match result")
        if self.attempts and self.selected_provider is not None:
            last = self.attempts[-1]
            if last.provider != self.selected_provider or last.model_id != self.selected_model_id:
                raise _invalid("selected provider or model does not match final attempt")
        if self.status is GenerationStatus.SUCCEEDED:
            if not isinstance(self.output, Mapping) or self.selected_provider is None:
                raise _invalid("successful result requires output and provider")
            frozen_output = _freeze_json(self.output)
            if stable_digest(frozen_output) != self.output_digest:
                raise _invalid("output digest does not match output")
            if self.attempts:
                last = self.attempts[-1]
                if (last.status is not GenerationStatus.SUCCEEDED
                        or last.output_digest != self.output_digest
                        or last.provider_request_id != self.provider_request_id
                        or last.finish_reason != self.finish_reason or last.usage != self.usage):
                    raise _invalid("successful result does not match final attempt")
            object.__setattr__(self, "output", frozen_output)
        elif self.output is not None:
            raise _invalid("failed result cannot include output")


class AttemptObserver(Protocol):
    def started(self, attempt: AttemptStarted) -> None:
        raise AssertionError("protocol method executed")

    def finished(self, attempt: AttemptRecord) -> None:
        raise AssertionError("protocol method executed")
