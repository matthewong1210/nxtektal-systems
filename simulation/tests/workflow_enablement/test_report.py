"""Deterministic, JSON-ready enablement report with content-derived identity.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import json

from nxt_workflow_enablement import (
    ENABLEMENT_REPORT_SCHEMA,
    GROUNDS_WORKFLOW_ID,
    PILOT_WORKFLOW_IDS,
    RANGE_OPS_WORKFLOW_ID,
    SIMULATED_SCENARIO_DISCLAIMER,
    EnablementReport,
    canonical_report_json,
    evaluate_pilot_site,
    pilot_workflow_registry,
)

from tests.workflow_enablement.conftest import make_range_ops_evidence


def build_report(payload, expectation, context, evidence=None):
    evaluation = evaluate_pilot_site(
        payload,
        expectation=expectation,
        context=context,
        range_ops_evidence=(
            evidence
            if evidence is not None
            else make_range_ops_evidence(payload)
        ),
        registry=pilot_workflow_registry(),
    )
    return EnablementReport.create(
        shared=evaluation.shared,
        workflows=evaluation.workflows,
        context=context,
        registry=pilot_workflow_registry(),
    )


class TestReportShape:
    def test_payload_is_json_ready_and_schema_versioned(
        self, payload, expectation, context
    ):
        report = build_report(payload, expectation, context)
        rendered = canonical_report_json(report)
        decoded = json.loads(rendered)
        assert decoded["schema"] == ENABLEMENT_REPORT_SCHEMA
        assert decoded["disclaimer"] == SIMULATED_SCENARIO_DISCLAIMER
        assert decoded["site_id"] == expectation.site_id
        assert decoded["deployment_id"] == expectation.deployment_id
        assert decoded["transport_mode"] == "FIXTURE_ONLY"
        assert decoded["physical_execution_reachable"] is False
        assert decoded["manifest_digest"].startswith("sha256:")
        assert list(decoded["workflows"]) == list(PILOT_WORKFLOW_IDS)
        assert decoded["registered_workflow_ids"] == list(PILOT_WORKFLOW_IDS)

    def test_per_workflow_sections_split_requirements_by_status(
        self, payload, expectation, context
    ):
        decoded = json.loads(
            canonical_report_json(build_report(payload, expectation, context))
        )
        range_ops = decoded["workflows"][RANGE_OPS_WORKFLOW_ID]
        assert range_ops["verdict"] == "READY_FOR_FIXTURE_SHADOW_MODE"
        assert range_ops["missing"] == []
        assert range_ops["unsupported_in_v0"] == []
        assert range_ops["deferred"] == []
        assert range_ops["satisfied"] == sorted(range_ops["satisfied"])
        assert range_ops["runtime_assembly_eligible"] is True
        grounds = decoded["workflows"][GROUNDS_WORKFLOW_ID]
        assert grounds["verdict"] == "NOT_READY"
        assert grounds["satisfied"] == []
        assert "course_model_version" in grounds["missing"]
        assert "cart_pose_binding" in grounds["unsupported_in_v0"]
        assert "inspection_coverage_contract" in grounds["deferred"]
        assert grounds["runtime_assembly_eligible"] is False

    def test_summary_preserves_individual_workflow_results(
        self, payload, expectation, context
    ):
        decoded = json.loads(
            canonical_report_json(build_report(payload, expectation, context))
        )
        summary = decoded["summary"]
        assert summary["ready_workflow_ids"] == [RANGE_OPS_WORKFLOW_ID]
        assert sorted(summary["not_ready_workflow_ids"]) == sorted(
            workflow_id
            for workflow_id in PILOT_WORKFLOW_IDS
            if workflow_id != RANGE_OPS_WORKFLOW_ID
        )
        assert summary["shared_site_verdict"] == "VALID"


class TestReportDeterminism:
    def test_identical_inputs_produce_byte_identical_reports(
        self, payload, expectation, context
    ):
        first = canonical_report_json(
            build_report(payload, expectation, context)
        )
        second = canonical_report_json(
            build_report(payload, expectation, context)
        )
        assert first == second

    def test_report_id_is_stable_and_content_derived(
        self, payload, expectation, context
    ):
        first = build_report(payload, expectation, context)
        second = build_report(payload, expectation, context)
        assert first.report_id == second.report_id
        assert first.report_id.startswith("wer_")

    def test_different_manifests_produce_different_report_ids(
        self, payload, expectation, context
    ):
        baseline = build_report(payload, expectation, context)
        payload["facility_name"] = "NXT Pilot Course A (revised)"
        revised = build_report(payload, expectation, context)
        assert revised.report_id != baseline.report_id

    def test_report_id_matches_its_own_payload_digest(
        self, payload, expectation, context
    ):
        import pytest

        from nxt_workflow_enablement import (
            WorkflowEnablementError,
            verify_report_payload,
        )

        report = build_report(payload, expectation, context)
        decoded = json.loads(canonical_report_json(report))
        assert decoded["report_id"] == report.report_id
        verify_report_payload(decoded)
        decoded["transport_mode"] = "TAMPERED"
        with pytest.raises(WorkflowEnablementError, match="report_id"):
            verify_report_payload(decoded)
