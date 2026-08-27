"""Shared-site gates and independent per-workflow readiness evaluation.

The commissioning package remains the sole owner of manifest validity:
this module never re-implements a channel, unit, sensor-type, duplicate,
or provenance rule.  It calls the canonical constructor, records the
canonical failure, and layers only workflow-enablement facts on top --
identity expectations, evidence-location safety, transport posture, and
per-workflow requirement satisfaction.

Isolation is structural: each workflow evaluator receives the shared
result plus the declared evidence its own requirement set references
and nothing else, so one workflow's prerequisites can neither block
nor satisfy another's.  Declared Course Model evidence is shared
*spatial* evidence referenced only by the two course workflows'
requirement sets; the Range Operations evaluator never receives it.
A shared-site failure is the only thing that makes every workflow
NOT_READY.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from nxt_commissioning import (
    AssetType,
    CalibrationStatus,
    CommissionedSite,
    dumps_manifest,
)

from .evidence import (
    CourseModelEvidence,
    EnablementContext,
    RangeOpsEvidence,
    RuntimeMode,
    SharedSiteExpectation,
    TransportMode,
    simulation_midnight_issue,
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
    GROUNDS_MAP_PREREQUISITE_IDS,
    GROUNDS_REQUIREMENTS_VERSION,
    PLAYER_CADDY_REQUIREMENTS_VERSION,
    RANGE_OPS_REQUIREMENTS_VERSION,
    REQUIRED_MAP_QUERY_KINDS,
    RequirementResult,
    RequirementStatus,
    grounds_prerequisites,
    player_caddy_prerequisites,
    required_range_ops_channels,
)

SHARED_SITE_INVALID = "shared_site_invalid"

_MANIFEST_NOT_EVALUATED = "not evaluated: the shared manifest is invalid"
_SHARED_SITE_NOT_EVALUATED = (
    "not evaluated: the shared site failed its gates"
)


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
                GateResult(
                    gate_id=gate_id,
                    passed=False,
                    detail=_MANIFEST_NOT_EVALUATED,
                )
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
                    detail=_SHARED_SITE_NOT_EVALUATED,
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

    # 2. Adapter conversion profiles composed by the canonical owner,
    # and every claimed adapter channel backed by a commissioned binding.
    # Evidence is declared plain data, so an adapter-channel claim the
    # validated site does not bind is a fabrication and fails closed
    # instead of filling coverage.
    bound_channels = frozenset(
        binding.channel for binding in site.sensor_bindings
    )
    unbacked_claims = tuple(
        sorted(
            channel
            for channel in evidence.adapter.adapter_channels
            if channel not in bound_channels
        )
    )
    adapter_issues: list[str] = []
    if not evidence.adapter.composed:
        adapter_issues.append(
            f"adapter composition failed closed: {evidence.adapter.error}"
        )
    if unbacked_claims:
        adapter_issues.append(
            "adapter evidence claims channels the commissioned site does "
            "not bind: " + ", ".join(unbacked_claims)
        )
    add(
        "adapter_conversion_profiles",
        not adapter_issues,
        (
            "the edge adapter kit composed over the commissioned "
            "telemetry-adapter projection and every claimed channel is a "
            "commissioned binding"
            if not adapter_issues
            else "; ".join(adapter_issues)
        ),
    )

    # 3. Adapter profile calibration identities match commissioned truth,
    # and every commissioned CALIBRATED binding has a declared profile
    # identity -- an empty or partial declaration cannot pass vacuously.
    calibrated_by_sensor = {
        binding.sensor_id: binding.calibration.calibration_id
        for binding in site.sensor_bindings
        if binding.calibration.status is CalibrationStatus.CALIBRATED
    }
    declared_by_sensor = dict(evidence.adapter.profile_calibration_ids)
    calibration_issues = [
        f"profile for {sensor_id!r} declares calibration "
        f"{declared_by_sensor[sensor_id]!r}, the commissioned binding "
        f"requires {commissioned_calibration!r}"
        for sensor_id, commissioned_calibration in sorted(
            calibrated_by_sensor.items()
        )
        if sensor_id in declared_by_sensor
        and declared_by_sensor[sensor_id] != commissioned_calibration
    ]
    calibration_issues.extend(
        f"no adapter profile calibration identity is declared for the "
        f"commissioned calibrated sensor {sensor_id!r}"
        for sensor_id in sorted(calibrated_by_sensor)
        if sensor_id not in declared_by_sensor
    )
    add(
        "calibration_profile_match",
        not calibration_issues,
        (
            "every commissioned calibrated sensor has an adapter profile "
            "declaring the commissioned calibration identity"
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

    # 5. No channel is claimed beyond what the site supports, and a
    # declared fixture input can never impersonate a commissioned
    # binding's adapter output.
    overlap = tuple(sorted(adapter_channels & declared_channels))
    beyond_requirements = tuple(
        sorted(declared_channels - frozenset(required))
    )
    impersonated = tuple(sorted(declared_channels & bound_channels))
    claim_issues: list[str] = []
    if overlap:
        claim_issues.append(
            "declared fixture channels duplicate adapter coverage: "
            + ", ".join(overlap)
        )
    if impersonated:
        claim_issues.append(
            "declared fixture channels impersonate commissioned adapter "
            "bindings: " + ", ".join(impersonated)
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
    midnight_issue = simulation_midnight_issue(
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


def _require_course_workflow_inputs(
    *,
    definition: WorkflowDefinition,
    expected_version: str,
    course_model: CourseModelEvidence | None,
) -> None:
    if definition.requirements_version != expected_version:
        raise WorkflowEnablementError(
            f"{definition.workflow_id} requirements version mismatch: "
            f"this evaluator implements {expected_version!r}, the registry "
            f"requires {definition.requirements_version!r}"
        )
    if course_model is not None and not isinstance(
        course_model, CourseModelEvidence
    ):
        raise WorkflowEnablementError(
            "course_model must be a CourseModelEvidence or None; untyped "
            "serialized input can never reach a verdict"
        )


def _course_workflow_readiness(
    *,
    shared: SharedSiteResult,
    definition: WorkflowDefinition,
    prerequisites: tuple[RequirementResult, ...],
) -> WorkflowReadiness:
    # No course workflow has any implemented runtime, so assembly
    # eligibility is structurally false regardless of prerequisites.
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


def _course_model_binding_issues(
    site: CommissionedSite, course_model: CourseModelEvidence
) -> tuple[str, ...]:
    """Cross-check declared Course Model evidence against the valid site.

    Evidence is declared plain data, so every claim the validated
    commissioned site can falsify is checked here: site identity,
    deployment identity, the coordinate-reference identity, and the
    commissioned facility origin the course frame claims to sit on.
    """
    issues: list[str] = []
    if course_model.site_id != site.site_id:
        issues.append(
            f"evidence names site {course_model.site_id!r}, the shared "
            f"commissioned site is {site.site_id!r}"
        )
    if course_model.deployment_id != site.deployment_id:
        issues.append(
            f"evidence names deployment {course_model.deployment_id!r}, "
            f"the shared commissioned deployment is {site.deployment_id!r}"
        )
    commissioned = site.spatial_reference.coordinate_system
    if course_model.crs_kind != commissioned.kind.value:
        issues.append(
            f"evidence coordinate kind {course_model.crs_kind!r} does not "
            f"match the commissioned kind {commissioned.kind.value!r}"
        )
    if course_model.crs_identifier != commissioned.identifier:
        issues.append(
            f"evidence coordinate reference "
            f"{course_model.crs_identifier!r} does not match the "
            f"commissioned reference {commissioned.identifier!r}"
        )
    if course_model.crs_horizontal_unit != commissioned.horizontal_unit:
        issues.append(
            f"evidence horizontal unit "
            f"{course_model.crs_horizontal_unit!r} does not match the "
            f"commissioned unit {commissioned.horizontal_unit!r}"
        )
    if course_model.crs_vertical_unit != commissioned.vertical_unit:
        issues.append(
            f"evidence vertical unit {course_model.crs_vertical_unit!r} "
            f"does not match the commissioned unit "
            f"{commissioned.vertical_unit!r}"
        )
    if course_model.crs_axes != tuple(commissioned.axes):
        issues.append(
            f"evidence axes {course_model.crs_axes!r} do not match the "
            f"commissioned axes {tuple(commissioned.axes)!r}"
        )
    origin = site.spatial_reference.facility_origin
    if (
        course_model.origin_crs_x != origin.x
        or course_model.origin_crs_y != origin.y
        or course_model.origin_crs_z != origin.z
    ):
        issues.append(
            "evidence frame origin "
            f"({course_model.origin_crs_x!r}, "
            f"{course_model.origin_crs_y!r}, "
            f"{course_model.origin_crs_z!r}) does not match the "
            f"commissioned facility origin ({origin.x!r}, {origin.y!r}, "
            f"{origin.z!r})"
        )
    return tuple(issues)


def evaluate_grounds(
    *,
    shared: SharedSiteResult,
    definition: WorkflowDefinition,
    site: CommissionedSite | None = None,
    course_model: CourseModelEvidence | None = None,
) -> WorkflowReadiness:
    """Grounds Condition Intelligence: map prerequisites are evidence-
    evaluable in requirements v2; everything else stays a scaffold."""
    _require_course_workflow_inputs(
        definition=definition,
        expected_version=GROUNDS_REQUIREMENTS_VERSION,
        course_model=course_model,
    )
    baseline = grounds_prerequisites()
    if shared.verdict is not SharedSiteVerdict.VALID or site is None:
        return _course_workflow_readiness(
            shared=shared, definition=definition, prerequisites=baseline
        )
    if course_model is None:
        return _course_workflow_readiness(
            shared=shared, definition=definition, prerequisites=baseline
        )
    binding_issues = _course_model_binding_issues(site, course_model)
    if binding_issues:
        detail = (
            "declared Course Model evidence does not bind to this "
            "commissioned site: " + "; ".join(binding_issues)
        )
        overrides = {
            requirement_id: RequirementResult(
                requirement_id=requirement_id,
                status=RequirementStatus.MISSING,
                detail=detail,
            )
            for requirement_id in GROUNDS_MAP_PREREQUISITE_IDS
        }
    else:
        overrides = {
            "course_model_version": RequirementResult(
                requirement_id="course_model_version",
                status=RequirementStatus.SATISFIED,
                detail=(
                    f"Course World Model {course_model.course_model_id!r} "
                    f"version {course_model.model_version!r} is declared "
                    "for this commissioned site and deployment"
                ),
            ),
            "course_coordinate_reference": RequirementResult(
                requirement_id="course_coordinate_reference",
                status=RequirementStatus.SATISFIED,
                detail=(
                    f"course frame {course_model.frame_id!r} declares the "
                    "commissioned coordinate reference "
                    f"{course_model.crs_identifier!r} "
                    f"({course_model.crs_kind}, "
                    f"{course_model.crs_horizontal_unit}/"
                    f"{course_model.crs_vertical_unit}) at the "
                    "commissioned facility origin"
                ),
            ),
            "map_version": RequirementResult(
                requirement_id="map_version",
                status=RequirementStatus.SATISFIED,
                detail=(
                    f"map identity {course_model.course_model_id!r} "
                    f"{course_model.model_version!r} with content digest "
                    f"{course_model.content_digest} at declared resolution "
                    f"{course_model.resolution_m} m"
                ),
            ),
        }
    prerequisites = tuple(
        overrides.get(item.requirement_id, item) for item in baseline
    )
    return _course_workflow_readiness(
        shared=shared, definition=definition, prerequisites=prerequisites
    )


def evaluate_player_caddy(
    *,
    shared: SharedSiteResult,
    definition: WorkflowDefinition,
    site: CommissionedSite | None = None,
    course_model: CourseModelEvidence | None = None,
) -> WorkflowReadiness:
    """Player Caddy Experience: the map-query prerequisite is evidence-
    evaluable in requirements v2; everything else stays a scaffold."""
    _require_course_workflow_inputs(
        definition=definition,
        expected_version=PLAYER_CADDY_REQUIREMENTS_VERSION,
        course_model=course_model,
    )
    baseline = player_caddy_prerequisites()
    if shared.verdict is not SharedSiteVerdict.VALID or site is None:
        return _course_workflow_readiness(
            shared=shared, definition=definition, prerequisites=baseline
        )
    if course_model is None:
        return _course_workflow_readiness(
            shared=shared, definition=definition, prerequisites=baseline
        )
    binding_issues = _course_model_binding_issues(site, course_model)
    missing_kinds = tuple(
        kind
        for kind in REQUIRED_MAP_QUERY_KINDS
        if kind not in course_model.supported_queries
    )
    if binding_issues:
        override = RequirementResult(
            requirement_id="course_world_model_map_query",
            status=RequirementStatus.MISSING,
            detail=(
                "declared Course Model evidence does not bind to this "
                "commissioned site: " + "; ".join(binding_issues)
            ),
        )
    elif missing_kinds:
        override = RequirementResult(
            requirement_id="course_world_model_map_query",
            status=RequirementStatus.MISSING,
            detail=(
                "the declared map-query surface does not support every "
                "required query kind; missing: " + ", ".join(missing_kinds)
            ),
        )
    else:
        override = RequirementResult(
            requirement_id="course_world_model_map_query",
            status=RequirementStatus.SATISFIED,
            detail=(
                "a deterministic map-query surface over Course World "
                f"Model {course_model.course_model_id!r} "
                f"{course_model.model_version!r} declares every required "
                "query kind: " + ", ".join(REQUIRED_MAP_QUERY_KINDS)
            ),
        )
    prerequisites = tuple(
        override if item.requirement_id == override.requirement_id else item
        for item in baseline
    )
    return _course_workflow_readiness(
        shared=shared, definition=definition, prerequisites=prerequisites
    )


def evaluate_workflows(
    *,
    registry: WorkflowRegistry,
    shared: SharedSiteResult,
    site: CommissionedSite | None,
    range_ops_evidence: RangeOpsEvidence,
    course_model_evidence: CourseModelEvidence | None = None,
) -> tuple[WorkflowReadiness, ...]:
    """Evaluate every registered workflow independently, in identity order.

    ``course_model_evidence`` is shared spatial evidence: only the two
    course workflows consume it, and the Range Operations evaluator
    never receives it, so a Course Model can neither lend readiness to
    nor subtract readiness from Range Operations.
    """
    if not isinstance(registry, WorkflowRegistry):
        raise WorkflowEnablementError("registry must be a WorkflowRegistry")
    if course_model_evidence is not None and not isinstance(
        course_model_evidence, CourseModelEvidence
    ):
        raise WorkflowEnablementError(
            "course_model_evidence must be a CourseModelEvidence or None; "
            "untyped serialized input can never reach a verdict"
        )
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
                evaluate_grounds(
                    shared=shared,
                    definition=definition,
                    site=site,
                    course_model=course_model_evidence,
                )
            )
        elif workflow_id == PLAYER_CADDY_WORKFLOW_ID:
            readiness.append(
                evaluate_player_caddy(
                    shared=shared,
                    definition=definition,
                    site=site,
                    course_model=course_model_evidence,
                )
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
    course_model_evidence: CourseModelEvidence | None = None,
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
        course_model_evidence=course_model_evidence,
    )
    return PilotSiteEvaluation(
        site=site if shared.verdict is SharedSiteVerdict.VALID else None,
        shared=shared,
        workflows=workflows,
    )
