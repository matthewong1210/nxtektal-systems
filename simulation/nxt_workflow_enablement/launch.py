"""Fixture-only Shadow Mode launch-plan data for a READY workflow.

A launch plan is pure data.  Turning it into a running composition is
composition-root work; this module only proves eligibility and fails
closed for anything that is not a READY Range Operations workflow.
Grounds Condition Intelligence and Player Caddy Experience have no v0
runtime, so no plan can exist for them.
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
)
from .identity import RANGE_OPS_WORKFLOW_ID, WorkflowEnablementError


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
    if not isinstance(evidence, RangeOpsEvidence):
        raise WorkflowEnablementError(
            "evidence must be a RangeOpsEvidence"
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
