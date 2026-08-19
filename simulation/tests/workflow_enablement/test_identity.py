"""Workflow identity registry: stable IDs, closed registration, no renames.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import dataclasses

import pytest

from nxt_workflow_enablement import (
    GROUNDS_WORKFLOW_ID,
    PILOT_WORKFLOW_IDS,
    PLAYER_CADDY_WORKFLOW_ID,
    RANGE_OPS_WORKFLOW_ID,
    WORKFLOW_REGISTRY_SCHEMA,
    WorkflowDefinition,
    WorkflowEnablementError,
    WorkflowRegistry,
    pilot_workflow_registry,
)


class TestWorkflowIdConstants:
    def test_the_three_pilot_workflow_ids_are_exact_literals(self):
        # Mechanical rename guard: these strings are checkpoint/report
        # identity and must never drift.
        assert RANGE_OPS_WORKFLOW_ID == "range.closed_loop_collection_handoff"
        assert GROUNDS_WORKFLOW_ID == "course.grounds_condition_intelligence"
        assert PLAYER_CADDY_WORKFLOW_ID == "course.player_caddy_experience"

    def test_pilot_workflow_ids_tuple_is_exactly_the_three_sorted(self):
        # Sorted identity order keeps every registry/report view stable
        # regardless of authoring order.
        assert PILOT_WORKFLOW_IDS == (
            "course.grounds_condition_intelligence",
            "course.player_caddy_experience",
            "range.closed_loop_collection_handoff",
        )

    def test_registry_schema_is_versioned(self):
        assert WORKFLOW_REGISTRY_SCHEMA == (
            "nxt-workflow-enablement/workflow-registry/v0"
        )


class TestWorkflowDefinition:
    def test_definition_is_frozen(self):
        definition = pilot_workflow_registry().definition(RANGE_OPS_WORKFLOW_ID)
        with pytest.raises(dataclasses.FrozenInstanceError):
            definition.workflow_id = "tampered"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "bad_id",
        [
            "",
            "  ",
            "no_namespace",
            "Range.Closed",
            "range.",
            ".handoff",
            "range .closed",
            "range.closed loop",
            "range.closed-loop",
        ],
    )
    def test_invalid_workflow_id_shapes_are_rejected(self, bad_id):
        with pytest.raises(WorkflowEnablementError):
            WorkflowDefinition(
                workflow_id=bad_id,
                display_label="Label",
                requirements_version="x/requirements/v1",
            )

    def test_blank_display_label_is_rejected(self):
        with pytest.raises(WorkflowEnablementError):
            WorkflowDefinition(
                workflow_id="range.closed_loop_collection_handoff",
                display_label="   ",
                requirements_version="x/requirements/v1",
            )

    def test_blank_requirements_version_is_rejected(self):
        with pytest.raises(WorkflowEnablementError):
            WorkflowDefinition(
                workflow_id="range.closed_loop_collection_handoff",
                display_label="Range Operations",
                requirements_version="",
            )


class TestWorkflowRegistry:
    def test_pilot_registry_registers_exactly_the_three_workflows(self):
        registry = pilot_workflow_registry()
        assert registry.workflow_ids == PILOT_WORKFLOW_IDS

    def test_unknown_workflow_lookup_fails_visibly(self):
        registry = pilot_workflow_registry()
        with pytest.raises(WorkflowEnablementError, match="unknown workflow"):
            registry.definition("course.unregistered_workflow")

    def test_contains_is_identity_based(self):
        registry = pilot_workflow_registry()
        assert RANGE_OPS_WORKFLOW_ID in registry
        assert "course.unregistered_workflow" not in registry

    def test_duplicate_workflow_registration_fails_visibly(self):
        definition = WorkflowDefinition(
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            display_label="Range Operations",
            requirements_version="range/requirements/v1",
        )
        with pytest.raises(WorkflowEnablementError, match="duplicate workflow"):
            WorkflowRegistry((definition, definition))

    def test_duplicate_id_with_different_label_is_still_a_duplicate(self):
        first = WorkflowDefinition(
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            display_label="Range Operations",
            requirements_version="range/requirements/v1",
        )
        second = WorkflowDefinition(
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            display_label="A Different Label",
            requirements_version="range/requirements/v1",
        )
        with pytest.raises(WorkflowEnablementError, match="duplicate workflow"):
            WorkflowRegistry((first, second))

    def test_empty_registry_is_rejected(self):
        with pytest.raises(WorkflowEnablementError):
            WorkflowRegistry(())

    def test_display_label_does_not_change_identity(self):
        relabeled = WorkflowRegistry(
            tuple(
                dataclasses.replace(
                    pilot_workflow_registry().definition(workflow_id),
                    display_label=f"Renamed {index}",
                )
                for index, workflow_id in enumerate(PILOT_WORKFLOW_IDS)
            )
        )
        assert relabeled.workflow_ids == pilot_workflow_registry().workflow_ids

    def test_registration_order_does_not_change_the_registry_view(self):
        registry = pilot_workflow_registry()
        reversed_registry = WorkflowRegistry(
            tuple(
                registry.definition(workflow_id)
                for workflow_id in reversed(PILOT_WORKFLOW_IDS)
            )
        )
        assert reversed_registry.workflow_ids == registry.workflow_ids
