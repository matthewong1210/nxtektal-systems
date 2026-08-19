"""Range Operations readiness gates over the shared commissioned site.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import dataclasses

import pytest

from nxt_workflow_enablement import (
    RANGE_OPS_WORKFLOW_ID,
    ReadinessVerdict,
    RequirementStatus,
    WorkflowEnablementError,
    evaluate_pilot_site,
    pilot_workflow_registry,
)

from tests.workflow_enablement.conftest import (
    make_adapter_evidence,
    make_range_ops_evidence,
    make_runtime_declaration,
)


def evaluate(payload, expectation, context, evidence):
    return evaluate_pilot_site(
        payload,
        expectation=expectation,
        context=context,
        range_ops_evidence=evidence,
        registry=pilot_workflow_registry(),
    )


def range_ops_of(evaluation):
    return next(
        readiness
        for readiness in evaluation.workflows
        if readiness.workflow_id == RANGE_OPS_WORKFLOW_ID
    )


def requirement_status(readiness, requirement_id):
    return {
        item.requirement_id: item.status for item in readiness.requirements
    }[requirement_id]


class TestReadyPath:
    def test_the_complete_pilot_fixture_is_ready_for_fixture_shadow_mode(
        self, payload, expectation, context, range_ops_evidence
    ):
        evaluation = evaluate(payload, expectation, context, range_ops_evidence)
        readiness = range_ops_of(evaluation)
        assert readiness.verdict is ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE
        assert readiness.runtime_assembly_eligible is True
        assert readiness.failures == ()
        assert all(
            item.status is RequirementStatus.SATISFIED
            for item in readiness.requirements
        )

    def test_verdicts_are_deterministic_across_repeat_evaluation(
        self, payload, expectation, context, range_ops_evidence
    ):
        first = evaluate(payload, expectation, context, range_ops_evidence)
        second = evaluate(payload, expectation, context, range_ops_evidence)
        assert [
            (item.workflow_id, item.verdict, item.requirements)
            for item in first.workflows
        ] == [
            (item.workflow_id, item.verdict, item.requirements)
            for item in second.workflows
        ]


class TestRangeOpsGateFailures:
    def test_missing_dispenser_binding_is_not_ready(
        self, payload, expectation, context
    ):
        payload["sensor_bindings"] = [
            binding
            for binding in payload["sensor_bindings"]
            if binding["channel"] != "inventory.dispenser.count"
        ]
        evidence = make_range_ops_evidence(payload)
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert readiness.runtime_assembly_eligible is False
        assert (
            requirement_status(readiness, "complete_channel_coverage")
            is RequirementStatus.MISSING
        )

    def test_adapter_composition_error_is_not_ready(
        self, payload, expectation, context
    ):
        evidence = make_range_ops_evidence(
            payload,
            adapter=make_adapter_evidence(
                composed=False,
                error="duplicate load_cell profile for 'sensor-lc-dispenser-count'",
                adapter_channels=(),
            ),
        )
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert (
            requirement_status(readiness, "adapter_conversion_profiles")
            is RequirementStatus.MISSING
        )

    def test_calibration_identity_mismatch_is_not_ready(
        self, payload, expectation, context, range_ops_evidence
    ):
        adapter = range_ops_evidence.adapter
        mismatched = dataclasses.replace(
            adapter,
            profile_calibration_ids=tuple(
                (sensor_id, "CAL-LC-WRONG-2020")
                if calibration_id.startswith("CAL-")
                else (sensor_id, calibration_id)
                for sensor_id, calibration_id in adapter.profile_calibration_ids
            ),
        )
        evidence = dataclasses.replace(range_ops_evidence, adapter=mismatched)
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert (
            requirement_status(readiness, "calibration_profile_match")
            is RequirementStatus.MISSING
        )

    def test_missing_non_adapter_fixture_input_is_not_ready(
        self, payload, expectation, context, range_ops_evidence
    ):
        evidence = dataclasses.replace(
            range_ops_evidence,
            declared_fixture_channels=tuple(
                channel
                for channel in range_ops_evidence.declared_fixture_channels
                if channel != "scan.zone.Z1.balls"
            ),
        )
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert (
            requirement_status(readiness, "complete_channel_coverage")
            is RequirementStatus.MISSING
        )

    def test_fixture_channel_claiming_adapter_coverage_is_not_ready(
        self, payload, expectation, context, range_ops_evidence
    ):
        # Claiming a channel the adapter kit already produces is a
        # duplicate-coverage claim, not extra safety.
        evidence = dataclasses.replace(
            range_ops_evidence,
            declared_fixture_channels=(
                range_ops_evidence.declared_fixture_channels
                + ("inventory.dispenser.count",)
            ),
        )
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert (
            requirement_status(readiness, "no_unsupported_channel_claims")
            is RequirementStatus.MISSING
        )

    def test_fixture_channel_outside_the_requirement_set_is_not_ready(
        self, payload, expectation, context, range_ops_evidence
    ):
        evidence = dataclasses.replace(
            range_ops_evidence,
            declared_fixture_channels=(
                range_ops_evidence.declared_fixture_channels
                + ("scan.zone.Z9.balls",)
            ),
        )
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert (
            requirement_status(readiness, "no_unsupported_channel_claims")
            is RequirementStatus.MISSING
        )

    def test_non_shadow_runtime_mode_is_not_ready(
        self, payload, expectation, context, range_ops_evidence
    ):
        evidence = dataclasses.replace(
            range_ops_evidence,
            runtime=make_runtime_declaration(runtime_mode="LIVE"),
        )
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert (
            requirement_status(readiness, "runtime_configuration")
            is RequirementStatus.MISSING
        )

    def test_non_midnight_simulation_epoch_is_not_ready(
        self, payload, expectation, context, range_ops_evidence
    ):
        evidence = dataclasses.replace(
            range_ops_evidence,
            runtime=make_runtime_declaration(
                simulation_midnight_iso="2026-08-08T01:00:00+00:00"
            ),
        )
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert (
            requirement_status(readiness, "runtime_configuration")
            is RequirementStatus.MISSING
        )

    def test_missing_washer_equipment_is_not_ready(
        self, payload, expectation, context
    ):
        payload["equipment_assets"] = [
            asset
            for asset in payload["equipment_assets"]
            if asset["asset_type"] != "washer"
        ]
        payload["sensor_bindings"] = [
            binding
            for binding in payload["sensor_bindings"]
            if binding["channel"] != "wash.washer.wip"
        ]
        evidence = make_range_ops_evidence(payload)
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert (
            requirement_status(readiness, "commissioned_range_equipment")
            is RequirementStatus.MISSING
        )

    def test_requirements_version_mismatch_fails_visibly(
        self, payload, expectation, context, range_ops_evidence
    ):
        evidence = dataclasses.replace(
            range_ops_evidence,
            requirements_version="range.closed_loop_collection_handoff/requirements/v999",
        )
        with pytest.raises(
            WorkflowEnablementError, match="requirements version"
        ):
            evaluate(payload, expectation, context, evidence)


class TestCommissioningOwnedGates:
    """Wrong units/sensor types are commissioning-owned validation.

    The shared site fails closed at manifest construction, which makes
    every workflow NOT_READY -- there is no path where a wrong unit
    reaches a runtime.
    """

    def test_wrong_unit_invalidates_the_shared_site(
        self, payload, expectation, context
    ):
        for binding in payload["sensor_bindings"]:
            if binding["channel"] == "inventory.dispenser.count":
                binding["units"] = "kg"
        evidence = make_range_ops_evidence(payload)
        evaluation = evaluate(payload, expectation, context, evidence)
        assert all(
            readiness.verdict is ReadinessVerdict.NOT_READY
            for readiness in evaluation.workflows
        )

    def test_wrong_sensor_type_invalidates_the_shared_site(
        self, payload, expectation, context
    ):
        for binding in payload["sensor_bindings"]:
            if binding["channel"] == "zone.Z1.is_open":
                binding["sensor_type"] = "load_cell"
        evidence = make_range_ops_evidence(payload)
        evaluation = evaluate(payload, expectation, context, evidence)
        assert all(
            readiness.verdict is ReadinessVerdict.NOT_READY
            for readiness in evaluation.workflows
        )
