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

    def test_fabricated_adapter_channel_cannot_satisfy_coverage(
        self, payload, expectation, context, range_ops_evidence
    ):
        # scan.zone.Z1.balls is a required channel, but the commissioned
        # site binds no sensor to it; an adapter-evidence claim for it is
        # a fabrication and must fail closed rather than fill coverage.
        adapter = dataclasses.replace(
            range_ops_evidence.adapter,
            adapter_channels=(
                range_ops_evidence.adapter.adapter_channels
                + ("scan.zone.Z1.balls",)
            ),
        )
        evidence = dataclasses.replace(
            range_ops_evidence,
            adapter=adapter,
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
            requirement_status(readiness, "adapter_conversion_profiles")
            is RequirementStatus.MISSING
        )

    def test_empty_profile_calibration_ids_cannot_bypass_calibration(
        self, payload, expectation, context, range_ops_evidence
    ):
        evidence = dataclasses.replace(
            range_ops_evidence,
            adapter=dataclasses.replace(
                range_ops_evidence.adapter, profile_calibration_ids=()
            ),
        )
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert (
            requirement_status(readiness, "calibration_profile_match")
            is RequirementStatus.MISSING
        )

    def test_omitting_one_calibrated_sensor_profile_fails(
        self, payload, expectation, context, range_ops_evidence
    ):
        adapter = range_ops_evidence.adapter
        evidence = dataclasses.replace(
            range_ops_evidence,
            adapter=dataclasses.replace(
                adapter,
                profile_calibration_ids=tuple(
                    pair
                    for pair in adapter.profile_calibration_ids
                    if pair[0] != "sensor-lc-washer-wip"
                ),
            ),
        )
        readiness = range_ops_of(
            evaluate(payload, expectation, context, evidence)
        )
        assert readiness.verdict is ReadinessVerdict.NOT_READY
        assert (
            requirement_status(readiness, "calibration_profile_match")
            is RequirementStatus.MISSING
        )

    def test_fixture_channel_cannot_impersonate_a_commissioned_binding(
        self, payload, expectation, context, range_ops_evidence
    ):
        # Drop the washer channel from adapter evidence and declare it as
        # a fixture input instead: a fixture claim for a commissioned
        # bound channel impersonates adapter output and must fail.
        adapter = dataclasses.replace(
            range_ops_evidence.adapter,
            adapter_channels=tuple(
                channel
                for channel in range_ops_evidence.adapter.adapter_channels
                if channel != "wash.washer.wip"
            ),
        )
        evidence = dataclasses.replace(
            range_ops_evidence,
            adapter=adapter,
            declared_fixture_channels=(
                range_ops_evidence.declared_fixture_channels
                + ("wash.washer.wip",)
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


class TestAdapterEvidenceContract:
    @pytest.mark.parametrize(
        "bad_error", [RuntimeError("boom"), 7, b"bytes", "", "   "]
    )
    def test_a_failed_composition_requires_a_string_error_detail(
        self, bad_error
    ):
        # A non-string error would serialize via repr (potentially with a
        # memory address) and break byte-identical reports.
        from nxt_workflow_enablement import AdapterCompositionEvidence

        with pytest.raises(WorkflowEnablementError, match="string"):
            AdapterCompositionEvidence(
                composed=False,
                error=bad_error,
                adapter_channels=(),
                profile_calibration_ids=(),
            )

    def test_a_failed_composition_with_a_string_error_is_accepted(self):
        from nxt_workflow_enablement import AdapterCompositionEvidence

        evidence = AdapterCompositionEvidence(
            composed=False,
            error="EdgeAdapterError: duplicate profile",
            adapter_channels=(),
            profile_calibration_ids=(),
        )
        assert evidence.error == "EdgeAdapterError: duplicate profile"


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
