"""Launch-plan data: only a READY Range Operations workflow is plannable.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import dataclasses

import pytest

from nxt_workflow_enablement import (
    GROUNDS_WORKFLOW_ID,
    PLAYER_CADDY_WORKFLOW_ID,
    RANGE_OPS_WORKFLOW_ID,
    ReadinessVerdict,
    RuntimeMode,
    TransportMode,
    WorkflowEnablementError,
    evaluate_pilot_site,
    pilot_workflow_registry,
    plan_range_ops_launch,
)

from tests.workflow_enablement.conftest import make_range_ops_evidence


def evaluate(payload, expectation, context, evidence):
    return evaluate_pilot_site(
        payload,
        expectation=expectation,
        context=context,
        range_ops_evidence=evidence,
        registry=pilot_workflow_registry(),
    )


def readiness_for(evaluation, workflow_id):
    return next(
        item
        for item in evaluation.workflows
        if item.workflow_id == workflow_id
    )


class TestReadyLaunchPlan:
    def test_a_ready_range_ops_workflow_yields_a_fixture_only_shadow_plan(
        self, payload, expectation, context, range_ops_evidence
    ):
        evaluation = evaluate(
            payload, expectation, context, range_ops_evidence
        )
        plan = plan_range_ops_launch(
            readiness=readiness_for(evaluation, RANGE_OPS_WORKFLOW_ID),
            shared=evaluation.shared,
            context=context,
            evidence=range_ops_evidence,
        )
        assert plan.workflow_id == RANGE_OPS_WORKFLOW_ID
        assert plan.site_id == expectation.site_id
        assert plan.deployment_id == expectation.deployment_id
        assert plan.transport_mode == TransportMode.FIXTURE_ONLY.value
        assert plan.runtime_mode == RuntimeMode.SHADOW.value
        assert plan.max_cycles >= 1
        assert plan.evidence_paths == context.output_locations.relative_paths

    def test_the_plan_is_frozen_pure_data(
        self, payload, expectation, context, range_ops_evidence
    ):
        evaluation = evaluate(
            payload, expectation, context, range_ops_evidence
        )
        plan = plan_range_ops_launch(
            readiness=readiness_for(evaluation, RANGE_OPS_WORKFLOW_ID),
            shared=evaluation.shared,
            context=context,
            evidence=range_ops_evidence,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            plan.max_cycles = 999  # type: ignore[misc]


class TestFailClosedLaunchPlanning:
    def test_a_not_ready_range_ops_workflow_cannot_be_planned(
        self, payload, expectation, context, range_ops_evidence
    ):
        broken = dataclasses.replace(
            range_ops_evidence, declared_fixture_channels=()
        )
        evaluation = evaluate(payload, expectation, context, broken)
        readiness = readiness_for(evaluation, RANGE_OPS_WORKFLOW_ID)
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        with pytest.raises(WorkflowEnablementError, match="NOT_READY"):
            plan_range_ops_launch(
                readiness=readiness,
                shared=evaluation.shared,
                context=context,
                evidence=broken,
            )

    @pytest.mark.parametrize(
        "workflow_id", [GROUNDS_WORKFLOW_ID, PLAYER_CADDY_WORKFLOW_ID]
    )
    def test_grounds_and_caddy_have_no_v0_runtime_to_plan(
        self, payload, expectation, context, range_ops_evidence, workflow_id
    ):
        evaluation = evaluate(
            payload, expectation, context, range_ops_evidence
        )
        with pytest.raises(WorkflowEnablementError, match="no v0 runtime"):
            plan_range_ops_launch(
                readiness=readiness_for(evaluation, workflow_id),
                shared=evaluation.shared,
                context=context,
                evidence=range_ops_evidence,
            )

    def test_a_tampered_ready_verdict_without_eligibility_is_refused(
        self, payload, expectation, context, range_ops_evidence
    ):
        evaluation = evaluate(
            payload, expectation, context, range_ops_evidence
        )
        readiness = dataclasses.replace(
            readiness_for(evaluation, RANGE_OPS_WORKFLOW_ID),
            runtime_assembly_eligible=False,
        )
        with pytest.raises(WorkflowEnablementError):
            plan_range_ops_launch(
                readiness=readiness,
                shared=evaluation.shared,
                context=context,
                evidence=range_ops_evidence,
            )
