"""Workflow identity: deterministic, versioned, label-independent IDs.

A workflow ID is report/checkpoint identity.  It is validated against a
closed shape, registered exactly once, and never derived from a display
label, so renaming a label can never rename evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WORKFLOW_REGISTRY_SCHEMA = "nxt-workflow-enablement/workflow-registry/v0"

# The three first-version pilot workflow identities.  These literals are
# mechanical rename guards: tests assert them character for character.
RANGE_OPS_WORKFLOW_ID = "range.closed_loop_collection_handoff"
GROUNDS_WORKFLOW_ID = "course.grounds_condition_intelligence"
PLAYER_CADDY_WORKFLOW_ID = "course.player_caddy_experience"

# Sorted identity order: every registry, report, and evidence view uses
# this order so authoring order can never change published bytes.
PILOT_WORKFLOW_IDS = tuple(
    sorted(
        (
            RANGE_OPS_WORKFLOW_ID,
            GROUNDS_WORKFLOW_ID,
            PLAYER_CADDY_WORKFLOW_ID,
        )
    )
)

_WORKFLOW_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class WorkflowEnablementError(ValueError):
    """A fail-closed workflow-enablement contract violation."""


def _require_non_blank(name: str, value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise WorkflowEnablementError(
            f"{name} must be a non-blank trimmed string"
        )
    return value


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    """One registered workflow: identity, label, and requirement version.

    ``workflow_id`` is the only identity.  ``display_label`` is
    presentation only and must never appear in identity-bearing payloads.
    """

    workflow_id: str
    display_label: str
    requirements_version: str

    def __post_init__(self) -> None:
        _require_non_blank("workflow_id", self.workflow_id)
        if not _WORKFLOW_ID_PATTERN.fullmatch(self.workflow_id):
            raise WorkflowEnablementError(
                f"workflow_id {self.workflow_id!r} must match "
                "<namespace>.<name> with lowercase letters, digits, and "
                "underscores"
            )
        _require_non_blank("display_label", self.display_label)
        _require_non_blank(
            "requirements_version", self.requirements_version
        )


class WorkflowRegistry:
    """A closed, duplicate-free registry of workflow definitions."""

    def __init__(self, definitions: tuple[WorkflowDefinition, ...]) -> None:
        if not isinstance(definitions, tuple) or not definitions:
            raise WorkflowEnablementError(
                "a workflow registry requires a non-empty tuple of "
                "workflow definitions"
            )
        by_id: dict[str, WorkflowDefinition] = {}
        for definition in definitions:
            if not isinstance(definition, WorkflowDefinition):
                raise WorkflowEnablementError(
                    "registry entries must be WorkflowDefinition instances"
                )
            if definition.workflow_id in by_id:
                raise WorkflowEnablementError(
                    f"duplicate workflow {definition.workflow_id!r}; a "
                    "workflow identity is registered exactly once"
                )
            by_id[definition.workflow_id] = definition
        self._by_id = dict(sorted(by_id.items()))

    @property
    def workflow_ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    def definition(self, workflow_id: str) -> WorkflowDefinition:
        try:
            return self._by_id[workflow_id]
        except KeyError:
            raise WorkflowEnablementError(
                f"unknown workflow {workflow_id!r}; registered workflows: "
                f"{', '.join(self._by_id)}"
            ) from None

    def __contains__(self, workflow_id: object) -> bool:
        return workflow_id in self._by_id
