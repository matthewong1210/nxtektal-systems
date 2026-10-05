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
