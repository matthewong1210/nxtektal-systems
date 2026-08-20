"""Independent per-workflow readiness: no cross-workflow blocking or leaking.

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
    ReadinessVerdict,
    RequirementStatus,
    WorkflowDefinition,
    WorkflowEnablementError,
    WorkflowRegistry,
    evaluate_pilot_site,
    pilot_workflow_registry,
)

from tests.workflow_enablement.conftest import make_range_ops_evidence


def evaluate(payload, expectation, context, evidence, registry=None):
    return evaluate_pilot_site(
        payload,
        expectation=expectation,
        context=context,
        range_ops_evidence=evidence,
        registry=registry or pilot_workflow_registry(),
    )


def by_workflow(evaluation):
    return {item.workflow_id: item for item in evaluation.workflows}


class TestIndependentReadiness:
    def test_range_ops_is_ready_while_grounds_and_caddy_are_not(
        self, payload, expectation, context, range_ops_evidence
    ):
        workflows = by_workflow(
            evaluate(payload, expectation, context, range_ops_evidence)
        )
        assert (
            workflows[RANGE_OPS_WORKFLOW_ID].verdict
            is ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE
        )
        assert (
            workflows[GROUNDS_WORKFLOW_ID].verdict
            is ReadinessVerdict.NOT_READY
        )
        assert (
            workflows[PLAYER_CADDY_WORKFLOW_ID].verdict
            is ReadinessVerdict.NOT_READY
        )

    def test_evaluation_covers_exactly_the_registered_workflows(
        self, payload, expectation, context, range_ops_evidence
    ):
        evaluation = evaluate(
            payload, expectation, context, range_ops_evidence
        )
        assert tuple(
            item.workflow_id for item in evaluation.workflows
        ) == PILOT_WORKFLOW_IDS

    def test_grounds_missing_prerequisites_do_not_affect_range_ops(
        self, payload, expectation, context, range_ops_evidence
    ):
        workflows = by_workflow(
            evaluate(payload, expectation, context, range_ops_evidence)
        )
        grounds = workflows[GROUNDS_WORKFLOW_ID]
        assert any(
            item.status is not RequirementStatus.SATISFIED
            for item in grounds.requirements
        )
        assert (
            workflows[RANGE_OPS_WORKFLOW_ID].verdict
            is ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE
        )

    def test_caddy_missing_prerequisites_do_not_affect_the_other_workflows(
        self, payload, expectation, context, range_ops_evidence
    ):
        workflows = by_workflow(
            evaluate(payload, expectation, context, range_ops_evidence)
        )
        caddy = workflows[PLAYER_CADDY_WORKFLOW_ID]
        assert caddy.verdict is ReadinessVerdict.NOT_READY
        assert (
            workflows[RANGE_OPS_WORKFLOW_ID].verdict
            is ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE
        )
        # Grounds is evaluated on its own prerequisites, not on caddy's.
        grounds_ids = {
            item.requirement_id
            for item in workflows[GROUNDS_WORKFLOW_ID].requirements
        }
        caddy_ids = {item.requirement_id for item in caddy.requirements}
        assert "caddy_session_contract" not in grounds_ids
        assert "inspection_coverage_contract" not in caddy_ids

    def test_a_range_ops_failure_does_not_change_grounds_or_caddy_results(
        self, payload, expectation, context, range_ops_evidence
    ):
        ready = evaluate(payload, expectation, context, range_ops_evidence)
        broken_evidence = dataclasses.replace(
            range_ops_evidence, declared_fixture_channels=()
        )
        broken = evaluate(payload, expectation, context, broken_evidence)
        for workflow_id in (GROUNDS_WORKFLOW_ID, PLAYER_CADDY_WORKFLOW_ID):
            assert (
                by_workflow(ready)[workflow_id].requirements
                == by_workflow(broken)[workflow_id].requirements
            )
            assert (
                by_workflow(broken)[workflow_id].verdict
                is ReadinessVerdict.NOT_READY
            )
        assert (
            by_workflow(broken)[RANGE_OPS_WORKFLOW_ID].verdict
            is ReadinessVerdict.NOT_READY
        )

    def test_range_ops_satisfaction_cannot_satisfy_another_workflow(
        self, payload, expectation, context, range_ops_evidence
    ):
        workflows = by_workflow(
            evaluate(payload, expectation, context, range_ops_evidence)
        )
        for workflow_id in (GROUNDS_WORKFLOW_ID, PLAYER_CADDY_WORKFLOW_ID):
            readiness = workflows[workflow_id]
            assert readiness.runtime_assembly_eligible is False
            assert all(
                item.status is not RequirementStatus.SATISFIED
                for item in readiness.requirements
            )


class TestSharedSiteFailureBlocksEverything:
    def test_an_invalid_shared_site_makes_all_workflows_not_ready(
        self, payload, expectation, context
    ):
        payload["site_id"] = "tampered site id"  # whitespace: invalid
        evidence = make_range_ops_evidence(payload)
        evaluation = evaluate(payload, expectation, context, evidence)
        assert all(
            item.verdict is ReadinessVerdict.NOT_READY
            for item in evaluation.workflows
        )
        assert all(
            "shared_site_invalid" in item.failures
            for item in evaluation.workflows
        )


class TestRegistryDiscipline:
    def test_an_unknown_workflow_registration_fails_visibly(
        self, payload, expectation, context, range_ops_evidence
    ):
        registry = WorkflowRegistry(
            (
                WorkflowDefinition(
                    workflow_id="course.unregistered_workflow",
                    display_label="Unregistered",
                    requirements_version="course.unregistered_workflow/requirements/v1",
                ),
            )
        )
        with pytest.raises(WorkflowEnablementError, match="no v0 evaluator"):
            evaluate(
                payload, expectation, context, range_ops_evidence, registry
            )

    def test_display_label_changes_do_not_change_verdicts(
        self, payload, expectation, context, range_ops_evidence
    ):
        relabeled = WorkflowRegistry(
            tuple(
                dataclasses.replace(
                    pilot_workflow_registry().definition(workflow_id),
                    display_label=f"Renamed {index}",
                )
                for index, workflow_id in enumerate(PILOT_WORKFLOW_IDS)
            )
        )
        baseline = evaluate(
            payload, expectation, context, range_ops_evidence
        )
        renamed = evaluate(
            payload, expectation, context, range_ops_evidence, relabeled
        )
        assert [
            (item.workflow_id, item.verdict) for item in baseline.workflows
        ] == [(item.workflow_id, item.verdict) for item in renamed.workflows]
