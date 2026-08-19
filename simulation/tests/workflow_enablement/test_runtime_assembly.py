"""READY assembles the existing runtime; NOT_READY assembles nothing.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import dataclasses

import pytest

from nxt_agent_runtime import AgentRuntime, CycleKind, FailurePolicy
from nxt_workflow_enablement import (
    RANGE_OPS_WORKFLOW_ID,
    ReadinessVerdict,
    WorkflowEnablementError,
    plan_range_ops_launch,
)

from scripts.pilot_course_a_edge_fixture import PILOT_CYCLES
from scripts.pilot_course_a_enablement_fixture import (
    MAX_SHADOW_CYCLES,
    assemble_range_ops_runtime,
    broken_manifest_payload,
    enablement_context,
    enablement_manifest_payload,
    enablement_site,
    evaluate_enablement,
    range_ops_evidence,
)


def ready_plan(payload):
    evaluation, _ = evaluate_enablement(payload)
    readiness = next(
        item
        for item in evaluation.workflows
        if item.workflow_id == RANGE_OPS_WORKFLOW_ID
    )
    return plan_range_ops_launch(
        readiness=readiness,
        shared=evaluation.shared,
        context=enablement_context(),
        evidence=range_ops_evidence(payload),
    )


class TestReadyAssembly:
    def test_ready_builds_the_existing_agent_runtime(self, tmp_path):
        payload = enablement_manifest_payload()
        plan = ready_plan(payload)
        runtime = assemble_range_ops_runtime(
            plan, enablement_site(payload), tmp_path / "range"
        )
        assert isinstance(runtime, AgentRuntime)

    def test_bounded_shadow_run_admits_and_evaluates_the_storyline(
        self, tmp_path
    ):
        payload = enablement_manifest_payload()
        plan = ready_plan(payload)
        runtime = assemble_range_ops_runtime(
            plan, enablement_site(payload), tmp_path / "range"
        )
        outcomes = runtime.run(max_cycles=MAX_SHADOW_CYCLES)
        kinds = [outcome.kind for outcome in outcomes]
        assert kinds == [
            CycleKind.EVALUATED,
            CycleKind.EVALUATED,
            CycleKind.SOURCE_EXHAUSTED,
        ]
        verdicts = [
            outcome.record.verdict.value
            for outcome in outcomes
            if outcome.record is not None
        ]
        assert verdicts == ["no_action", "recommend"]
        assert len(runtime.queue.pending()) == 1
        status = runtime.status()
        assert status.evaluations_completed == 2
        assert status.source_exhausted is True

    def test_each_admitted_envelope_is_journaled_exactly_once(
        self, tmp_path
    ):
        payload = enablement_manifest_payload()
        plan = ready_plan(payload)
        root = tmp_path / "range"
        runtime = assemble_range_ops_runtime(
            plan, enablement_site(payload), root
        )
        runtime.run(max_cycles=MAX_SHADOW_CYCLES)
        from nxt_agent_runtime import EvaluationJournal

        records = EvaluationJournal(root / "evaluations.jsonl").read()
        assert [record.sequence_number for record in records] == [0, 1]
        assert len({record.evaluation_id for record in records}) == 2

    def test_restart_recomposes_with_stable_identities(self, tmp_path):
        payload = enablement_manifest_payload()
        site = enablement_site(payload)
        plan = ready_plan(payload)
        root = tmp_path / "range"
        first_life = assemble_range_ops_runtime(plan, site, root)
        first_life.run(max_cycles=MAX_SHADOW_CYCLES)
        from nxt_agent_runtime import EvaluationJournal

        journal_before = [
            (record.sequence_number, record.evaluation_id)
            for record in EvaluationJournal(root / "evaluations.jsonl").read()
        ]

        second_life = assemble_range_ops_runtime(
            plan, site, root, consumed_cycles=2, first_sequence_number=2
        )
        report = second_life.recover()
        assert report.last_published_sequence == 1
        assert report.last_evaluated_sequence == 1
        assert report.journal_record_count == 2
        outcomes = second_life.run(max_cycles=1)
        assert [outcome.kind for outcome in outcomes] == [
            CycleKind.SOURCE_EXHAUSTED
        ]
        journal_after = [
            (record.sequence_number, record.evaluation_id)
            for record in EvaluationJournal(root / "evaluations.jsonl").read()
        ]
        assert journal_after == journal_before

    def test_rejected_frame_never_reaches_policy_evaluation(self, tmp_path):
        payload = enablement_manifest_payload()
        plan = ready_plan(payload)
        root = tmp_path / "range"
        # Cycle 3 carries an uncalibrated dispenser reading; cycle 4 is
        # the corrected redelivery at the same scenario time.
        runtime = assemble_range_ops_runtime(
            plan,
            enablement_site(payload),
            root,
            specs=(PILOT_CYCLES[3], PILOT_CYCLES[4]),
        )
        outcomes = runtime.run(
            max_cycles=MAX_SHADOW_CYCLES,
            failure_policy=FailurePolicy.CONTINUE,
        )
        kinds = [outcome.kind for outcome in outcomes]
        assert kinds[0] is CycleKind.REJECTED
        assert CycleKind.EVALUATED in kinds
        from nxt_agent_runtime import EvaluationJournal

        records = EvaluationJournal(root / "evaluations.jsonl").read()
        # Only the corrected redelivery was evaluated; the rejected
        # frame produced no journal record and reused sequence 0.
        assert [record.sequence_number for record in records] == [0]


class TestNothingAssembledWhenNotReady:
    def test_not_ready_yields_no_plan_and_no_runtime(self, tmp_path):
        payload = broken_manifest_payload()
        evaluation, _ = evaluate_enablement(payload)
        readiness = next(
            item
            for item in evaluation.workflows
            if item.workflow_id == RANGE_OPS_WORKFLOW_ID
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        with pytest.raises(WorkflowEnablementError):
            plan_range_ops_launch(
                readiness=readiness,
                shared=evaluation.shared,
                context=enablement_context(),
                evidence=range_ops_evidence(payload),
            )
        evidence_root = tmp_path / "range"
        assert not evidence_root.exists()

    @pytest.mark.parametrize("non_plan", [None, {}, "plan", 7])
    def test_the_factory_refuses_anything_but_a_launch_plan(
        self, tmp_path, non_plan
    ):
        payload = enablement_manifest_payload()
        with pytest.raises(WorkflowEnablementError, match="LaunchPlan"):
            assemble_range_ops_runtime(
                non_plan, enablement_site(payload), tmp_path / "range"
            )
        assert not (tmp_path / "range").exists()

    def test_a_plan_for_a_different_site_identity_is_refused(
        self, tmp_path
    ):
        payload = enablement_manifest_payload()
        plan = dataclasses.replace(
            ready_plan(payload), deployment_id="some-other-deployment"
        )
        with pytest.raises(WorkflowEnablementError, match="identity"):
            assemble_range_ops_runtime(
                plan, enablement_site(payload), tmp_path / "range"
            )
        assert not (tmp_path / "range").exists()
