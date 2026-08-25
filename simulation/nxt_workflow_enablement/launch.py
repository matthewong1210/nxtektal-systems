"""Fixture-only Shadow Mode launch-plan data for a READY workflow.

A launch plan is pure public data, **not an unforgeable capability**:
nothing here can prove a plan instance came from
:func:`plan_range_ops_launch`.  The planner is the only honest issuer --
it fails closed for every NOT_READY workflow -- and the composition
root is the trusted boundary that must obtain plans from it.  What the
contract does enforce mechanically is structure: a plan whose fields
are not a coherent fixture-only Shadow Mode posture cannot be
constructed at all.  Grounds Condition Intelligence and Player Caddy
Experience have no v0 runtime, so no plan can exist for them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .evaluation import (
    ReadinessVerdict,
    SharedSiteResult,
    SharedSiteVerdict,
    WorkflowReadiness,
)
from .evidence import (
    EnablementContext,
    RangeOpsEvidence,
    RuntimeMode,
    TransportMode,
    simulation_midnight_issue,
    validate_relative_evidence_paths,
)
from .identity import (
    RANGE_OPS_WORKFLOW_ID,
    WorkflowEnablementError,
    _require_non_blank,
)


@dataclass(frozen=True, slots=True)
class RangeOpsLaunchPlan:
    """Everything a composition root may act on -- and nothing else."""

    workflow_id: str
    site_id: str
    deployment_id: str
    transport_mode: str
    runtime_mode: str
    max_cycles: int
    simulation_midnight_iso: str
    clean_sensed_valid: bool
    evidence_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_blank("workflow_id", self.workflow_id)
        _require_non_blank("site_id", self.site_id)
        _require_non_blank("deployment_id", self.deployment_id)
        _require_non_blank("transport_mode", self.transport_mode)
        _require_non_blank("runtime_mode", self.runtime_mode)
        if (
            isinstance(self.max_cycles, bool)
            or not isinstance(self.max_cycles, int)
            or self.max_cycles < 1
        ):
            raise WorkflowEnablementError(
                "max_cycles must be a positive integer"
            )
        _require_non_blank(
            "simulation_midnight_iso", self.simulation_midnight_iso
        )
        midnight_issue = simulation_midnight_issue(
            self.simulation_midnight_iso
        )
        if midnight_issue is not None:
            raise WorkflowEnablementError(midnight_issue)
        if type(self.clean_sensed_valid) is not bool:
            raise WorkflowEnablementError(
                "clean_sensed_valid must be a boolean"
            )
        validate_relative_evidence_paths(self.evidence_paths)


def plan_range_ops_launch(
    *,
    readiness: WorkflowReadiness,
    shared: SharedSiteResult,
    context: EnablementContext,
    evidence: RangeOpsEvidence,
) -> RangeOpsLaunchPlan:
    """Derive the bounded fixture-only Shadow Mode plan, or fail closed."""
    if not isinstance(readiness, WorkflowReadiness):
        raise WorkflowEnablementError(
            "readiness must be a WorkflowReadiness"
        )
    if readiness.workflow_id != RANGE_OPS_WORKFLOW_ID:
        raise WorkflowEnablementError(
            f"workflow {readiness.workflow_id!r} has no v0 runtime; only "
            "the Range Operations workflow may be assembled"
        )
    if readiness.verdict is not ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE:
        raise WorkflowEnablementError(
            f"workflow {readiness.workflow_id!r} is NOT_READY; no runtime, "
            "state, evaluation, or evidence may be produced for it"
        )
    if readiness.runtime_assembly_eligible is not True:
        raise WorkflowEnablementError(
            "readiness is not runtime-assembly eligible; refusing to plan"
        )
    if not isinstance(evidence, RangeOpsEvidence):
        raise WorkflowEnablementError(
            "evidence must be a RangeOpsEvidence"
        )
    if readiness.requirements_version != evidence.requirements_version:
        raise WorkflowEnablementError(
            "launch planning requirements version disagreement: readiness "
            f"declares {readiness.requirements_version!r}, evidence "
            f"declares {evidence.requirements_version!r}"
        )
    if (
        not isinstance(shared, SharedSiteResult)
        or shared.verdict is not SharedSiteVerdict.VALID
        or shared.site_id is None
        or shared.deployment_id is None
    ):
        raise WorkflowEnablementError(
            "a launch plan requires a VALID shared commissioned site"
        )
    if not isinstance(context, EnablementContext):
        raise WorkflowEnablementError(
            "context must be an EnablementContext"
        )
    if context.transport_mode != TransportMode.FIXTURE_ONLY.value:
        raise WorkflowEnablementError(
            "a launch plan is fixture-only; transport "
            f"{context.transport_mode!r} is not plannable"
        )
    if evidence.runtime.runtime_mode != RuntimeMode.SHADOW.value:
        raise WorkflowEnablementError(
            "a launch plan is Shadow Mode only; runtime mode "
            f"{evidence.runtime.runtime_mode!r} is not plannable"
        )
    return RangeOpsLaunchPlan(
        workflow_id=readiness.workflow_id,
        site_id=shared.site_id,
        deployment_id=shared.deployment_id,
        transport_mode=TransportMode.FIXTURE_ONLY.value,
        runtime_mode=RuntimeMode.SHADOW.value,
        max_cycles=evidence.runtime.max_cycles,
        simulation_midnight_iso=evidence.runtime.simulation_midnight_iso,
        clean_sensed_valid=evidence.runtime.clean_sensed_valid,
        evidence_paths=context.output_locations.relative_paths,
    )
