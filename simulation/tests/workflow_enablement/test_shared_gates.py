"""Shared-site gate evaluation: one commissioned site, fail-closed gates.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import dataclasses

import pytest

from nxt_workflow_enablement import (
    OutputLocationPlan,
    SharedSiteExpectation,
    SharedSiteVerdict,
    WorkflowEnablementError,
    evaluate_shared_site,
)

from tests.workflow_enablement.conftest import make_context


def gate_by_id(result):
    return {gate.gate_id: gate for gate in result.gates}


class TestValidSharedSite:
    def test_valid_site_and_deployment_pass_every_gate(
        self, payload, expectation, context
    ):
        site, result = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert site is not None
        assert result.verdict is SharedSiteVerdict.VALID
        assert result.failures == ()
        assert all(gate.passed for gate in result.gates)
        assert result.site_id == expectation.site_id
        assert result.deployment_id == expectation.deployment_id

    def test_manifest_digest_is_stable_and_content_derived(
        self, payload, expectation, context
    ):
        _, first = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        _, second = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert first.manifest_digest == second.manifest_digest
        assert first.manifest_digest is not None
        assert first.manifest_digest.startswith("sha256:")

    def test_manifest_digest_changes_when_the_manifest_changes(
        self, payload, expectation, context
    ):
        _, baseline = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        payload["facility_name"] = "NXT Pilot Course A (revised)"
        _, revised = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert revised.manifest_digest != baseline.manifest_digest

    def test_authoring_order_does_not_change_the_digest(
        self, payload, expectation, context
    ):
        _, baseline = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        payload["sensor_bindings"] = list(
            reversed(payload["sensor_bindings"])
        )
        _, reordered = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert reordered.manifest_digest == baseline.manifest_digest


class TestSharedSiteFailures:
    def test_site_identity_mismatch_is_invalid(self, payload, context):
        expectation = SharedSiteExpectation(
            site_id="some-other-site", deployment_id=payload["deployment_id"]
        )
        _, result = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert result.verdict is SharedSiteVerdict.INVALID
        assert not gate_by_id(result)["site_identity_match"].passed

    def test_deployment_identity_mismatch_is_invalid(self, payload, context):
        expectation = SharedSiteExpectation(
            site_id=payload["site_id"], deployment_id="other-deployment"
        )
        _, result = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert result.verdict is SharedSiteVerdict.INVALID
        assert not gate_by_id(result)["deployment_identity_match"].passed

    def test_invalid_manifest_fails_the_manifest_gate(
        self, payload, expectation, context
    ):
        payload["schema"] = "not-a-commissioning-schema"
        site, result = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert site is None
        assert result.verdict is SharedSiteVerdict.INVALID
        assert not gate_by_id(result)["manifest_valid"].passed
        assert result.manifest_digest is None

    def test_unknown_asset_reference_is_invalid(
        self, payload, expectation, context
    ):
        payload["sensor_bindings"][0]["associated_asset_id"] = "NO-SUCH-ASSET"
        site, result = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert site is None
        assert result.verdict is SharedSiteVerdict.INVALID

    def test_duplicate_sensor_identity_is_invalid(
        self, payload, expectation, context
    ):
        payload["sensor_bindings"].append(
            dict(payload["sensor_bindings"][0])
        )
        site, result = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert site is None
        assert result.verdict is SharedSiteVerdict.INVALID

    def test_non_empty_output_root_fails_the_collision_gate(
        self, payload, expectation
    ):
        context = make_context(
            output_locations=OutputLocationPlan(
                relative_paths=("snapshots.jsonl",), root_is_empty=False
            )
        )
        _, result = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert result.verdict is SharedSiteVerdict.INVALID
        assert not gate_by_id(result)["output_locations_collision_safe"].passed

    def test_claimed_physical_execution_reachability_is_invalid(
        self, payload, expectation
    ):
        context = make_context(physical_execution_reachable=True)
        _, result = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert result.verdict is SharedSiteVerdict.INVALID
        assert not gate_by_id(result)["physical_execution_unreachable"].passed

    def test_non_fixture_transport_is_invalid(self, payload, expectation):
        context = make_context(transport_mode="LIVE_MODBUS")
        _, result = evaluate_shared_site(
            payload, expectation=expectation, context=context
        )
        assert result.verdict is SharedSiteVerdict.INVALID
        assert not gate_by_id(result)["transport_fixture_only"].passed


class TestOutputLocationPlan:
    @pytest.mark.parametrize(
        "bad_path",
        ["", "  ", "/absolute.jsonl", "../escape.jsonl", "a/../b.jsonl"],
    )
    def test_unsafe_relative_paths_are_rejected(self, bad_path):
        with pytest.raises(WorkflowEnablementError):
            OutputLocationPlan(
                relative_paths=(bad_path,), root_is_empty=True
            )

    def test_duplicate_relative_paths_are_rejected(self):
        with pytest.raises(WorkflowEnablementError):
            OutputLocationPlan(
                relative_paths=("a.jsonl", "a.jsonl"), root_is_empty=True
            )

    def test_plan_is_frozen(self):
        plan = OutputLocationPlan(
            relative_paths=("a.jsonl",), root_is_empty=True
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            plan.root_is_empty = False  # type: ignore[misc]
