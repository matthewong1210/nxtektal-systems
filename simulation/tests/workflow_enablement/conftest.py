"""Shared fixtures for Workflow Enablement V0 tests.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The tests drive the real commissioning seam: the shared manifest is the
existing synthetic Pilot Course A payload (with the enablement
deployment identity), and adapter evidence comes from composing the real
edge adapter kit over the real telemetry-adapter projection.
"""

from __future__ import annotations

import copy

import pytest

from nxt_workflow_enablement import (
    AdapterCompositionEvidence,
    EnablementContext,
    OutputLocationPlan,
    RANGE_OPS_REQUIREMENTS_VERSION,
    RangeOpsEvidence,
    RangeOpsRuntimeDeclaration,
    RuntimeMode,
    SharedSiteExpectation,
    TransportMode,
)

from scripts.pilot_course_a_enablement_fixture import (
    DEPLOYMENT_ID,
    ENABLEMENT_SCENARIO_NAME,
    ENABLEMENT_T_S,
    EVIDENCE_RELATIVE_PATHS,
    MAX_SHADOW_CYCLES,
    SIMULATION_MIDNIGHT_ISO,
    SITE_ID,
    adapter_composition_evidence,
    declared_fixture_channels,
    enablement_manifest_payload,
)


@pytest.fixture(scope="session")
def valid_payload() -> dict:
    return enablement_manifest_payload()


@pytest.fixture
def payload(valid_payload) -> dict:
    return copy.deepcopy(valid_payload)


@pytest.fixture(scope="session")
def expectation() -> SharedSiteExpectation:
    return SharedSiteExpectation(
        site_id=SITE_ID, deployment_id=DEPLOYMENT_ID
    )


def make_context(**changes) -> EnablementContext:
    values = {
        "scenario_name": ENABLEMENT_SCENARIO_NAME,
        "scenario_t_s": ENABLEMENT_T_S,
        "transport_mode": TransportMode.FIXTURE_ONLY.value,
        "physical_execution_reachable": False,
        "output_locations": OutputLocationPlan(
            relative_paths=EVIDENCE_RELATIVE_PATHS, root_is_empty=True
        ),
    }
    values.update(changes)
    return EnablementContext(**values)


@pytest.fixture
def context() -> EnablementContext:
    return make_context()


def make_runtime_declaration(**changes) -> RangeOpsRuntimeDeclaration:
    values = {
        "runtime_mode": RuntimeMode.SHADOW.value,
        "simulation_midnight_iso": SIMULATION_MIDNIGHT_ISO,
        "clean_sensed_valid": True,
        "policy_id": "ball-availability-guardian",
        "policy_version": "0.1.0",
        "max_cycles": MAX_SHADOW_CYCLES,
    }
    values.update(changes)
    return RangeOpsRuntimeDeclaration(**values)


def make_range_ops_evidence(payload_dict: dict, **changes) -> RangeOpsEvidence:
    values = {
        "requirements_version": RANGE_OPS_REQUIREMENTS_VERSION,
        "adapter": adapter_composition_evidence(payload_dict),
        "declared_fixture_channels": declared_fixture_channels(),
        "runtime": make_runtime_declaration(),
    }
    values.update(changes)
    return RangeOpsEvidence(**values)


@pytest.fixture
def range_ops_evidence(payload) -> RangeOpsEvidence:
    return make_range_ops_evidence(payload)


def make_adapter_evidence(**changes) -> AdapterCompositionEvidence:
    values = {
        "composed": True,
        "error": None,
        "adapter_channels": ("inventory.dispenser.count",),
        "profile_calibration_ids": (),
    }
    values.update(changes)
    return AdapterCompositionEvidence(**values)
