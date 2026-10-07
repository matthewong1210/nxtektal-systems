"""Fixed staffing advisory prompt construction."""

from __future__ import annotations

from .contracts import (
    PROMPT_TEMPLATE_VERSION,
    SUPPORTED_PROMPT_TEMPLATE_VERSIONS,
)
from .projection import (
    GenerationProjection,
    canonical_generation_input,
    validate_generation_projection,
)


def build_prompt(
    projection: GenerationProjection,
) -> tuple[dict[str, str], dict[str, str]]:
    """Render the exact messages bound to the projection's own template version."""

    validated = validate_generation_projection(projection)
    messages = canonical_generation_input(
        validated.basis_snapshot,
        validated.provider_payload,
        validated.basis_snapshot.prompt_template_version,
    )["messages"]
    assert type(messages) is tuple
    return messages


__all__ = [
    "PROMPT_TEMPLATE_VERSION",
    "SUPPORTED_PROMPT_TEMPLATE_VERSIONS",
    "build_prompt",
]
