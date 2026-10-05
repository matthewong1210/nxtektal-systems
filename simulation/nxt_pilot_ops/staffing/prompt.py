"""Fixed staffing advisory prompt construction."""

from __future__ import annotations

from .contracts import PROMPT_TEMPLATE_VERSION
from .projection import (
    GenerationProjection,
    canonical_generation_input,
    validate_generation_projection,
)


def build_prompt(
    projection: GenerationProjection,
) -> tuple[dict[str, str], dict[str, str]]:
    validated = validate_generation_projection(projection)
    messages = canonical_generation_input(
        validated.basis_snapshot,
        validated.provider_payload,
        PROMPT_TEMPLATE_VERSION,
    )["messages"]
    assert type(messages) is tuple
    return messages


__all__ = ["PROMPT_TEMPLATE_VERSION", "build_prompt"]
