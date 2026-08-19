"""Deterministic, JSON-ready commissioning and workflow-enablement report.

The report is readiness *evidence*: stable identities, digests, gate
results, and per-workflow requirement outcomes.  It copies no canonical
domain object -- facility state, telemetry, policy output, and workflow
records keep their existing owners -- and it is never an input to any
live loop.  Identical inputs serialize to byte-identical canonical JSON,
and ``report_id`` is content-derived so a tampered payload fails
verification.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from nxt_commissioning import canonical_projection_json

from .evaluation import (
    SharedSiteResult,
    SharedSiteVerdict,
    ReadinessVerdict,
    WorkflowReadiness,
)
from .evidence import EnablementContext
from .identity import WorkflowEnablementError, WorkflowRegistry
from .requirements import RequirementStatus

ENABLEMENT_REPORT_SCHEMA = "nxt-workflow-enablement/report/v0"
SIMULATED_SCENARIO_DISCLAIMER = (
    "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA"
)


def _workflow_section(readiness: WorkflowReadiness) -> dict[str, Any]:
    by_status: dict[RequirementStatus, list[str]] = {
        status: [] for status in RequirementStatus
    }
    for item in readiness.requirements:
        by_status[item.status].append(item.requirement_id)
    return {
        "requirements_version": readiness.requirements_version,
        "verdict": readiness.verdict.value,
        "runtime_assembly_eligible": readiness.runtime_assembly_eligible,
        "requirements": [
            {
                "requirement_id": item.requirement_id,
                "status": item.status.value,
                "detail": item.detail,
            }
            for item in sorted(
                readiness.requirements,
                key=lambda item: item.requirement_id,
            )
        ],
        "satisfied": sorted(by_status[RequirementStatus.SATISFIED]),
        "missing": sorted(by_status[RequirementStatus.MISSING]),
        "unsupported_in_v0": sorted(
            by_status[RequirementStatus.UNSUPPORTED_IN_V0]
        ),
        "deferred": sorted(by_status[RequirementStatus.DEFERRED]),
        "failures": list(readiness.failures),
    }


def _payload_digest(payload: Mapping[str, Any]) -> str:
    identity = {
        key: value for key, value in payload.items() if key != "report_id"
    }
    return "wer_" + hashlib.sha256(
        canonical_projection_json(identity).encode("utf-8")
    ).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class EnablementReport:
    """A frozen, content-addressed enablement report."""

    report_id: str
    shared: SharedSiteResult
    workflows: tuple[WorkflowReadiness, ...]
    context: EnablementContext
    registered_workflow_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        shared: SharedSiteResult,
        workflows: tuple[WorkflowReadiness, ...],
        context: EnablementContext,
        registry: WorkflowRegistry,
    ) -> "EnablementReport":
        if not isinstance(shared, SharedSiteResult):
            raise WorkflowEnablementError(
                "shared must be a SharedSiteResult"
            )
        if not isinstance(context, EnablementContext):
            raise WorkflowEnablementError(
                "context must be an EnablementContext"
            )
        if not isinstance(registry, WorkflowRegistry):
            raise WorkflowEnablementError(
                "registry must be a WorkflowRegistry"
            )
        if not isinstance(workflows, tuple) or not workflows:
            raise WorkflowEnablementError(
                "workflows must be a non-empty tuple"
            )
        workflow_ids = tuple(item.workflow_id for item in workflows)
        if workflow_ids != registry.workflow_ids:
            raise WorkflowEnablementError(
                "the report must cover exactly the registered workflows "
                f"in identity order; got {workflow_ids!r}, registered "
                f"{registry.workflow_ids!r}"
            )
        draft = cls(
            report_id="",
            shared=shared,
            workflows=workflows,
            context=context,
            registered_workflow_ids=registry.workflow_ids,
        )
        report_id = _payload_digest(draft._identity_payload())
        return cls(
            report_id=report_id,
            shared=shared,
            workflows=workflows,
            context=context,
            registered_workflow_ids=registry.workflow_ids,
        )

    def _identity_payload(self) -> dict[str, Any]:
        ready = [
            item.workflow_id
            for item in self.workflows
            if item.verdict
            is ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE
        ]
        not_ready = [
            item.workflow_id
            for item in self.workflows
            if item.verdict is ReadinessVerdict.NOT_READY
        ]
        return {
            "schema": ENABLEMENT_REPORT_SCHEMA,
            "disclaimer": SIMULATED_SCENARIO_DISCLAIMER,
            "site_id": self.shared.site_id,
            "deployment_id": self.shared.deployment_id,
            "manifest_digest": self.shared.manifest_digest,
            "scenario": {
                "name": self.context.scenario_name,
                "t_s": self.context.scenario_t_s,
            },
            "transport_mode": self.context.transport_mode,
            "physical_execution_reachable": (
                self.context.physical_execution_reachable
            ),
            "evidence_paths": list(
                self.context.output_locations.relative_paths
            ),
            "shared_site": {
                "verdict": self.shared.verdict.value,
                "gates": [
                    {
                        "gate_id": gate.gate_id,
                        "passed": gate.passed,
                        "detail": gate.detail,
                    }
                    for gate in self.shared.gates
                ],
                "failures": list(self.shared.failures),
            },
            "registered_workflow_ids": list(self.registered_workflow_ids),
            "workflows": {
                item.workflow_id: _workflow_section(item)
                for item in self.workflows
            },
            "summary": {
                "shared_site_verdict": self.shared.verdict.value,
                "ready_workflow_ids": ready,
                "not_ready_workflow_ids": not_ready,
            },
        }

    def to_payload(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["report_id"] = self.report_id
        return payload


def canonical_report_json(report: EnablementReport) -> str:
    """Canonical bytes for the report: sorted keys, compact, newline."""
    if not isinstance(report, EnablementReport):
        raise WorkflowEnablementError(
            "report must be an EnablementReport"
        )
    return canonical_projection_json(report.to_payload()) + "\n"


def verify_report_payload(payload: Mapping[str, Any]) -> None:
    """Fail closed when a serialized report's content-derived id is wrong."""
    if not isinstance(payload, Mapping):
        raise WorkflowEnablementError("payload must be a mapping")
    declared = payload.get("report_id")
    if type(declared) is not str or not declared:
        raise WorkflowEnablementError(
            "payload carries no report_id to verify"
        )
    expected = _payload_digest(payload)
    if declared != expected:
        raise WorkflowEnablementError(
            f"report_id {declared!r} does not match the payload digest "
            f"{expected!r}; the report was altered after issuance"
        )
