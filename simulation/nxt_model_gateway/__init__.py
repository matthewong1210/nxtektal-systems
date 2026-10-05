"""Provider-neutral model generation boundary."""

from .contracts import (
    AttemptObserver,
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
    Provider,
    ProviderConfig,
    TokenUsage,
)
from .serialization import canonical_json, decode_validated_json, stable_digest

__all__ = [
    "DeploymentRegion", "FailureCode", "GatewayContractError", "GenerationMessage",
    "GenerationRequest", "GenerationResult", "GenerationStatus", "MessageRole",
    "AttemptObserver", "AttemptObserverError", "AttemptRecord", "AttemptStarted",
    "Provider", "ProviderConfig", "TokenUsage", "canonical_json",
    "decode_validated_json", "stable_digest",
]

from .adapters import AdapterOutcome, PreparedRequest, ProviderAdapter
from .kimi import KimiAdapter
from .openai import OpenAIAdapter
from .anthropic import AnthropicAdapter

__all__ += [
    "AdapterOutcome", "PreparedRequest", "ProviderAdapter", "KimiAdapter",
    "OpenAIAdapter", "AnthropicAdapter",
]

from .routing import ModelGateway, RoutePolicy, RouteReadiness, RouteReadinessStatus

__all__ += ["ModelGateway", "RoutePolicy", "RouteReadiness", "RouteReadinessStatus"]
