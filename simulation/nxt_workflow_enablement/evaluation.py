"""Shared-site gates and independent per-workflow readiness evaluation.

The commissioning package remains the sole owner of manifest validity:
this module never re-implements a channel, unit, sensor-type, duplicate,
or provenance rule.  It calls the canonical constructor, records the
canonical failure, and layers only workflow-enablement facts on top --
identity expectations, evidence-location safety, transport posture, and
per-workflow requirement satisfaction.

Isolation is structural: each workflow evaluator receives the shared
result plus that workflow's own declared evidence and nothing else, so
one workflow's prerequisites can neither block nor satisfy another's.
A shared-site failure is the only thing that makes every workflow
NOT_READY.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from nxt_commissioning import (
    AssetType,
    CalibrationStatus,
    CommissionedSite,
    dumps_manifest,
)

from .evidence import (
    EnablementContext,
    RangeOpsEvidence,
    RuntimeMode,
    SharedSiteExpectation,
    TransportMode,
)
from .identity import (
    GROUNDS_WORKFLOW_ID,
    PLAYER_CADDY_WORKFLOW_ID,
    RANGE_OPS_WORKFLOW_ID,
    WorkflowDefinition,
    WorkflowEnablementError,
    WorkflowRegistry,
)
from .requirements import (
    GROUNDS_REQUIREMENTS_VERSION,
    PLAYER_CADDY_REQUIREMENTS_VERSION,
    RANGE_OPS_REQUIREMENTS_VERSION,
    RequirementResult,
    RequirementStatus,
    grounds_prerequisites,
    player_caddy_prerequisites,
    required_range_ops_channels,
)

SHARED_SITE_INVALID = "shared_site_invalid"

_NOT_EVALUATED = "not evaluated: the shared manifest is invalid"


class SharedSiteVerdict(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"


class ReadinessVerdict(StrEnum):
    READY_FOR_FIXTURE_SHADOW_MODE = "READY_FOR_FIXTURE_SHADOW_MODE"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True, slots=True)
class GateResult:
    gate_id: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class SharedSiteResult:
    """The shared commissioned-site verdict every workflow starts from."""

    verdict: SharedSiteVerdict
    site_id: str | None
    deployment_id: str | None
    manifest_digest: str | None
    gates: tuple[GateResult, ...]
    failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkflowReadiness:
    """One workflow's independent readiness result."""

    workflow_id: str
    requirements_version: str
    verdict: ReadinessVerdict
    requirements: tuple[RequirementResult, ...]
    failures: tuple[str, ...]
    runtime_assembly_eligible: bool


@dataclass(frozen=True, slots=True)
class PilotSiteEvaluation:
    """Aggregate result: the validated site, shared gates, all workflows."""

    site: CommissionedSite | None
    shared: SharedSiteResult
    workflows: tuple[WorkflowReadiness, ...]


def evaluate_shared_site(
    manifest_payload: Mapping[str, Any],
    *,
    expectation: SharedSiteExpectation,
    context: EnablementContext,
) -> tuple[CommissionedSite | None, SharedSiteResult]:
    """Validate the one shared commissioned site for all workflows."""
    if not isinstance(expectation, SharedSiteExpectation):
        raise WorkflowEnablementError(
            "expectation must be a SharedSiteExpectation"
        )
    if not isinstance(context, EnablementContext):
        raise WorkflowEnablementError(
            "context must be an EnablementContext"
        )
    if not isinstance(manifest_payload, Mapping):
        raise WorkflowEnablementError(
            "manifest_payload must be a mapping"
        )

    gates: list[GateResult] = []
    failures: list[str] = []

    site: CommissionedSite | None = None
    try:
        site = CommissionedSite.from_dict(
            copy.deepcopy(dict(manifest_payload))
        )
    except Exception as exc:  # commissioning owns the failure detail
        gates.append(
            GateResult(
                gate_id="manifest_valid",
                passed=False,
                detail=f"{type(exc).__name__}: {exc}",
            )
        )
        failures.append("manifest_invalid")
    else:
        gates.append(
            GateResult(
                gate_id="manifest_valid",
                passed=True,
                detail=(
                    "the canonical commissioning constructor validated the "
                    "manifest (assets, sensors, channels, units, "
                    "calibration, duplicates, provenance)"
                ),
            )
        )

    manifest_digest: str | None = None
    if site is None:
        for gate_id in (
            "site_identity_match",
            "deployment_identity_match",
            "manifest_digest_stable",
        ):
            gates.append(
                GateResult(gate_id=gate_id, passed=False, detail=_NOT_EVALUATED)
            )
    else:
        site_matches = site.site_id == expectation.site_id
        gates.append(
            GateResult(
                gate_id="site_identity_match",
                passed=site_matches,
                detail=(
                    f"manifest site_id {site.site_id!r} vs expected "
                    f"{expectation.site_id!r}"
                ),
            )
        )
        if not site_matches:
            failures.append("site_identity_mismatch")
        deployment_matches = (
            site.deployment_id == expectation.deployment_id
        )
        gates.append(
            GateResult(
                gate_id="deployment_identity_match",
                passed=deployment_matches,
                detail=(
                    f"manifest deployment_id {site.deployment_id!r} vs "
                    f"expected {expectation.deployment_id!r}"
                ),
            )
        )
        if not deployment_matches:
            failures.append("deployment_identity_mismatch")
        manifest_digest = "sha256:" + hashlib.sha256(
            dumps_manifest(site).encode("utf-8")
        ).hexdigest()
        gates.append(
            GateResult(
                gate_id="manifest_digest_stable",
                passed=True,
                detail=manifest_digest,
            )
        )

    output_safe = context.output_locations.root_is_empty
    gates.append(
        GateResult(
            gate_id="output_locations_collision_safe",
            passed=output_safe,
            detail=(
                "declared evidence root is empty and every path is a safe "
                "relative path"
                if output_safe
                else "the declared evidence root is not empty; evidence "
                "streams are append-only and a reused root is a collision"
            ),
        )
    )
    if not output_safe:
        failures.append("output_location_collision")

    execution_unreachable = not context.physical_execution_reachable
    gates.append(
        GateResult(
            gate_id="physical_execution_unreachable",
            passed=execution_unreachable,
            detail=(
                "physical execution is declared unreachable; no robot, "
                "actuator, or emergency-stop control path exists in v0"
                if execution_unreachable
                else "the context claims physical execution is reachable; "
                "no such path exists in v0 and the claim fails closed"
            ),
        )
    )
    if not execution_unreachable:
        failures.append("physical_execution_claimed")

    transport_ok = context.transport_mode == TransportMode.FIXTURE_ONLY.value
    gates.append(
        GateResult(
            gate_id="transport_fixture_only",
            passed=transport_ok,
            detail=(
                f"declared transport mode {context.transport_mode!r}; v0 "
                f"supports only {TransportMode.FIXTURE_ONLY.value!r} with "
                "no live device connectivity"
            ),
        )
    )
    if not transport_ok:
        failures.append("transport_not_fixture_only")

    verdict = (
        SharedSiteVerdict.VALID
        if not failures and site is not None
        else SharedSiteVerdict.INVALID
    )
    result = SharedSiteResult(
        verdict=verdict,
        site_id=None if site is None else site.site_id,
        deployment_id=None if site is None else site.deployment_id,
        manifest_digest=manifest_digest,
        gates=tuple(gates),
        failures=tuple(failures),
    )
    return site, result


def _shared_site_blocked(
    definition: WorkflowDefinition,
    requirements: tuple[RequirementResult, ...],
) -> WorkflowReadiness:
    return WorkflowReadiness(
        workflow_id=definition.workflow_id,
        requirements_version=definition.requirements_version,
        verdict=ReadinessVerdict.NOT_READY,
        requirements=requirements,
        failures=(SHARED_SITE_INVALID,),
        runtime_assembly_eligible=False,
    )


def _parse_simulation_midnight(value: str) -> str | None:
    """Return a failure detail, or None when the epoch is valid."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        return f"simulation midnight is not ISO-8601: {exc}"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return "simulation midnight must be timezone-aware"
    if (parsed.hour, parsed.minute, parsed.second, parsed.microsecond) != (
        0,
        0,
        0,
        0,
    ):
        return "simulation midnight must be an exact calendar midnight"
    return None


def evaluate_range_ops(
    *,
    site: CommissionedSite | None,
    shared: SharedSiteResult,
    evidence: RangeOpsEvidence,
    definition: WorkflowDefinition,
) -> WorkflowReadiness:
    """Evaluate every Range Operations v1 gate independently."""
    if definition.workflow_id != RANGE_OPS_WORKFLOW_ID:
        raise WorkflowEnablementError(
            f"the Range Operations evaluator received workflow "
            f"{definition.workflow_id!r}"
        )
    if evidence.requirements_version != definition.requirements_version:
        raise WorkflowEnablementError(
            "range operations requirements version mismatch: evidence "
            f"declares {evidence.requirements_version!r}, the registry "
            f"requires {definition.requirements_version!r}"
        )
    if definition.requirements_version != RANGE_OPS_REQUIREMENTS_VERSION:
        raise WorkflowEnablementError(
            "range operations requirements version mismatch: this "
            f"evaluator implements {RANGE_OPS_REQUIREMENTS_VERSION!r}, the "
            f"registry requires {definition.requirements_version!r}"
        )

    if shared.verdict is not SharedSiteVerdict.VALID or site is None:
        return _shared_site_blocked(
            definition,
            (
                RequirementResult(
                    requirement_id="shared_site_valid",
                    status=RequirementStatus.MISSING,
                    detail=_NOT_EVALUATED,
                ),
            ),
        )

    results: list[RequirementResult] = []

    def add(requirement_id: str, satisfied: bool, detail: str) -> None:
        results.append(
            RequirementResult(
                requirement_id=requirement_id,
                status=(
                    RequirementStatus.SATISFIED
                    if satisfied
                    else RequirementStatus.MISSING
                ),
                detail=detail,
            )
        )

    # 1. Commissioned range equipment topology.
    equipment_by_type: dict[AssetType, int] = {}
    for asset in site.equipment_assets:
        equipment_by_type[asset.asset_type] = (
            equipment_by_type.get(asset.asset_type, 0) + 1
        )
    zone_ids = tuple(
        sorted(
            zone.zone_id for zone in site.spatial_reference.zone_definitions
        )
    )
    station_ids = tuple(
        sorted(
            asset.asset_id
            for asset in site.equipment_assets
            if asset.asset_type is AssetType.COLLECTION_STATION
        )
    )
    robot_ids = tuple(sorted(robot.robot_id for robot in site.robot_assets))
    topology_issues: list[str] = []
    if equipment_by_type.get(AssetType.DISPENSER, 0) < 1:
        topology_issues.append("no commissioned dispenser")
    if equipment_by_type.get(AssetType.WASHER, 0) != 1:
        topology_issues.append(
            f"expected exactly one commissioned washer, found "
            f"{equipment_by_type.get(AssetType.WASHER, 0)}"
        )
    if equipment_by_type.get(AssetType.CHARGING_STATION, 0) != 1:
        topology_issues.append(
            f"expected exactly one commissioned charging station, found "
            f"{equipment_by_type.get(AssetType.CHARGING_STATION, 0)}"
        )
    if not station_ids:
        topology_issues.append("no commissioned collection station")
    if not robot_ids:
        topology_issues.append("no commissioned robot")
    if not zone_ids:
        topology_issues.append("no commissioned zone")
    add(
        "commissioned_range_equipment",
        not topology_issues,
        (
            "dispenser, washer, charging station, collection station, "
            "robot, and zone topology is commissioned"
            if not topology_issues
            else "; ".join(topology_issues)
        ),
    )

    # 2. Adapter conversion profiles composed by the canonical owner.
    add(
        "adapter_conversion_profiles",
        evidence.adapter.composed,
        (
            "the edge adapter kit composed over the commissioned "
            "telemetry-adapter projection"
            if evidence.adapter.composed
            else f"adapter composition failed closed: {evidence.adapter.error}"
        ),
    )

    # 3. Adapter profile calibration identities match commissioned truth.
    calibrated_by_sensor = {
        binding.sensor_id: binding.calibration.calibration_id
        for binding in site.sensor_bindings
        if binding.calibration.status is CalibrationStatus.CALIBRATED
    }
    calibration_issues = tuple(
        sorted(
            f"profile for {sensor_id!r} declares calibration "
            f"{profile_calibration!r}, the commissioned binding requires "
            f"{calibrated_by_sensor[sensor_id]!r}"
            for sensor_id, profile_calibration in (
                evidence.adapter.profile_calibration_ids
            )
            if sensor_id in calibrated_by_sensor
            and profile_calibration != calibrated_by_sensor[sensor_id]
        )
    )
    add(
        "calibration_profile_match",
        not calibration_issues,
        (
            "every adapter profile bound to a calibrated sensor declares "
            "the commissioned calibration identity"
            if not calibration_issues
            else "; ".join(calibration_issues)
        ),
    )

    # 4. Complete channel coverage: adapters plus declared fixture inputs.
    adapter_channels = frozenset(evidence.adapter.adapter_channels)
    declared_channels = frozenset(evidence.declared_fixture_channels)
    if zone_ids and station_ids and robot_ids:
        required = required_range_ops_channels(
            zone_ids=zone_ids,
            station_ids=station_ids,
            robot_ids=robot_ids,
        )
    else:
        required = ()
    covered = adapter_channels | declared_channels
    missing_channels = tuple(
        sorted(channel for channel in required if channel not in covered)
    )
    if not required:
        add(
            "complete_channel_coverage",
            False,
            "the required channel set cannot be derived: the commissioned "
            "topology lacks a zone, collection station, or robot",
        )
    else:
        add(
            "complete_channel_coverage",
            not missing_channels,
            (
                f"all {len(required)} required canonical channels are "
                "covered by adapter bindings or declared fixture inputs"
                if not missing_channels
                else "uncovered required channels: "
                + ", ".join(missing_channels)
            ),
        )

    # 5. No channel is claimed beyond what the site supports.
    overlap = tuple(sorted(adapter_channels & declared_channels))
    beyond_requirements = tuple(
        sorted(declared_channels - frozenset(required))
    )
    claim_issues: list[str] = []
    if overlap:
        claim_issues.append(
            "declared fixture channels duplicate adapter coverage: "
            + ", ".join(overlap)
        )
    if beyond_requirements:
        claim_issues.append(
            "declared fixture channels are outside the v1 requirement "
            "set: " + ", ".join(beyond_requirements)
        )
    add(
        "no_unsupported_channel_claims",
        not claim_issues,
        (
            "every declared fixture channel fills exactly one uncovered "
            "requirement"
            if not claim_issues
            else "; ".join(claim_issues)
        ),
    )

    # 6. Declared runtime configuration is a valid Shadow Mode posture.
    runtime_issues: list[str] = []
    if evidence.runtime.runtime_mode != RuntimeMode.SHADOW.value:
        runtime_issues.append(
            f"runtime mode {evidence.runtime.runtime_mode!r} is not "
            f"{RuntimeMode.SHADOW.value!r}; v0 permits Shadow Mode only"
        )
    midnight_issue = _parse_simulation_midnight(
        evidence.runtime.simulation_midnight_iso
    )
    if midnight_issue is not None:
        runtime_issues.append(midnight_issue)
    add(
        "runtime_configuration",
        not runtime_issues,
        (
            "declared Shadow Mode configuration is complete: policy "
            f"{evidence.runtime.policy_id!r} "
            f"{evidence.runtime.policy_version!r}, bounded to "
            f"{evidence.runtime.max_cycles} cycles"
            if not runtime_issues
            else "; ".join(runtime_issues)
        ),
    )

    requirements = tuple(results)
    all_satisfied = all(
        item.status is RequirementStatus.SATISFIED for item in requirements
    )
    verdict = (
        ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE
        if all_satisfied
        else ReadinessVerdict.NOT_READY
    )
    return WorkflowReadiness(
        workflow_id=definition.workflow_id,
        requirements_version=definition.requirements_version,
        verdict=verdict,
        requirements=requirements,
        failures=(
            ()
            if all_satisfied
            else tuple(
                f"requirement_not_satisfied:{item.requirement_id}"
                for item in requirements
                if item.status is not RequirementStatus.SATISFIED
            )
        ),
        runtime_assembly_eligible=all_satisfied,
    )


def _evaluate_prerequisite_scaffold(
    *,
    shared: SharedSiteResult,
    definition: WorkflowDefinition,
    expected_version: str,
    prerequisites: tuple[RequirementResult, ...],
) -> WorkflowReadiness:
    if definition.requirements_version != expected_version:
        raise WorkflowEnablementError(
            f"{definition.workflow_id} requirements version mismatch: "
            f"this evaluator implements {expected_version!r}, the registry "
            f"requires {definition.requirements_version!r}"
        )
    failures = (
        (SHARED_SITE_INVALID,)
        if shared.verdict is not SharedSiteVerdict.VALID
        else ()
    )
    return WorkflowReadiness(
        workflow_id=definition.workflow_id,
        requirements_version=definition.requirements_version,
        verdict=ReadinessVerdict.NOT_READY,
        requirements=prerequisites,
        failures=failures
        + tuple(
            f"requirement_not_satisfied:{item.requirement_id}"
            for item in prerequisites
            if item.status is not RequirementStatus.SATISFIED
        ),
        runtime_assembly_eligible=False,
    )


def evaluate_grounds(
    *, shared: SharedSiteResult, definition: WorkflowDefinition
) -> WorkflowReadiness:
    """Grounds Condition Intelligence: prerequisite scaffold only in v0."""
    return _evaluate_prerequisite_scaffold(
        shared=shared,
        definition=definition,
        expected_version=GROUNDS_REQUIREMENTS_VERSION,
        prerequisites=grounds_prerequisites(),
    )


def evaluate_player_caddy(
    *, shared: SharedSiteResult, definition: WorkflowDefinition
) -> WorkflowReadiness:
    """Player Caddy Experience: prerequisite scaffold only in v0."""
    return _evaluate_prerequisite_scaffold(
        shared=shared,
        definition=definition,
        expected_version=PLAYER_CADDY_REQUIREMENTS_VERSION,
        prerequisites=player_caddy_prerequisites(),
    )


def evaluate_workflows(
    *,
    registry: WorkflowRegistry,
    shared: SharedSiteResult,
    site: CommissionedSite | None,
    range_ops_evidence: RangeOpsEvidence,
) -> tuple[WorkflowReadiness, ...]:
    """Evaluate every registered workflow independently, in identity order."""
    if not isinstance(registry, WorkflowRegistry):
        raise WorkflowEnablementError("registry must be a WorkflowRegistry")
    readiness: list[WorkflowReadiness] = []
    for workflow_id in registry.workflow_ids:
        definition = registry.definition(workflow_id)
        if workflow_id == RANGE_OPS_WORKFLOW_ID:
            readiness.append(
                evaluate_range_ops(
                    site=site,
                    shared=shared,
                    evidence=range_ops_evidence,
                    definition=definition,
                )
            )
        elif workflow_id == GROUNDS_WORKFLOW_ID:
            readiness.append(
                evaluate_grounds(shared=shared, definition=definition)
            )
        elif workflow_id == PLAYER_CADDY_WORKFLOW_ID:
            readiness.append(
                evaluate_player_caddy(shared=shared, definition=definition)
            )
        else:
            raise WorkflowEnablementError(
                f"no v0 evaluator exists for workflow {workflow_id!r}; "
                "registering a workflow never implies it is implemented"
            )
    return tuple(readiness)


def evaluate_pilot_site(
    manifest_payload: Mapping[str, Any],
    *,
    expectation: SharedSiteExpectation,
    context: EnablementContext,
    range_ops_evidence: RangeOpsEvidence,
    registry: WorkflowRegistry,
) -> PilotSiteEvaluation:
    """One shared-site validation plus every registered workflow verdict."""
    site, shared = evaluate_shared_site(
        manifest_payload, expectation=expectation, context=context
    )
    workflows = evaluate_workflows(
        registry=registry,
        shared=shared,
        site=site,
        range_ops_evidence=range_ops_evidence,
    )
    return PilotSiteEvaluation(
        site=site if shared.verdict is SharedSiteVerdict.VALID else None,
        shared=shared,
        workflows=workflows,
    )
