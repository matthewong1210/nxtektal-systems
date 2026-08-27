"""Service-shell contracts for the Pilot Site Agent application boundary.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

``nxt_site_agent`` is the local, fixture-backed application boundary
around the existing Agent Runtime.  Everything this module defines is
noncanonical service metadata: service lifecycle states, the versioned
local Manager API identity, the service-state file schemas, and the
plain-data seam a composition root uses to hand the service an already
assembled, readiness-gated runtime.

Nothing here is facility truth, observation truth, policy truth, or
workflow truth.  The canonical owners are unchanged: the state
orchestration layer owns admission/publication, ``nxt_agent_runtime``
owns the evaluation lifecycle, and ``nxt_pilot_ops`` owns decisions,
traces, workflow records, and the ledger.  The service only projects
their outputs and transports existing manager workflow operations.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nxt_agent_runtime import AgentRuntime
from nxt_workflow_enablement import RangeOpsLaunchPlan

API_SCHEMA_VERSION = "nxt-site-agent/api/v0"
SERVICE_STATE_SCHEMA = "nxt-site-agent/service-state/v0"
SERVICE_EVENTS_SCHEMA = "nxt-site-agent/service-events/v0"

DISCLAIMER = "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA"
SERVICE_MODE_LABEL = "fixture-backed Shadow Mode"

#: Hosts the service is allowed to bind.  The V0 service is local-only
#: by contract: it has no authentication, so exposing it beyond
#: loopback is refused at construction rather than warned about.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


class ServiceState(StrEnum):
    """Noncanonical service lifecycle state.

    Distinct from the runtime's own state: the service can be SERVING
    while the composed runtime is stopped between fixture advances.
    """

    CREATED = "created"
    SERVING = "serving"
    STOPPED = "stopped"
    FAILED = "failed"


class SiteAgentError(RuntimeError):
    """Service-boundary failure with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class LaunchRefusedError(SiteAgentError):
    """The service refused to launch; nothing was composed or written."""


@dataclass(frozen=True, slots=True)
class SourceCursor:
    """Resume position of the fixture observation source.

    This is the service-persisted analogue of the resume hook a real
    transport reader must offer.  ``consumed_cycles`` counts every
    delivered batch the source resolved (acknowledged or terminally
    rejected); ``next_sequence_number`` is the next publication
    position.  They are independent facts: a rejected batch consumes a
    cycle without advancing the sequence.
    """

    consumed_cycles: int
    next_sequence_number: int

    def __post_init__(self) -> None:
        for name, value in (
            ("consumed_cycles", self.consumed_cycles),
            ("next_sequence_number", self.next_sequence_number),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise SiteAgentError(
                    "invalid_cursor", f"{name} must be an integer"
                )
            if value < 0:
                raise SiteAgentError(
                    "invalid_cursor", f"{name} must be non-negative"
                )

    def to_dict(self) -> dict[str, int]:
        return {
            "consumed_cycles": self.consumed_cycles,
            "next_sequence_number": self.next_sequence_number,
        }


@dataclass(frozen=True, slots=True)
class ComposedRuntime:
    """One composed, resumable runtime instance plus its read models.

    ``cursor`` reads the live source resume position; the service
    persists it after every cycle.  ``adapter_reports`` reads the
    conversion diagnostics the composition captured per fixture cycle,
    as plain dictionaries — presentation-only evidence that never
    feeds the loop.
    """

    runtime: AgentRuntime
    cursor: Callable[[], SourceCursor]
    adapter_reports: Callable[[], Mapping[int, Mapping[str, Any]]]

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, AgentRuntime):
            raise SiteAgentError(
                "invalid_composition",
                "composed runtime must be an AgentRuntime",
            )


#: Builds one runtime for the service.  Arguments, in order: the
#: verified launch plan, the workflow evidence root, the source resume
#: cursor, and the service's runtime sink (best-effort visibility
#: only).  The composer must thread all four into the composition it
#: assembles and must resume the fixture source at the cursor.
RuntimeComposer = Callable[
    [RangeOpsLaunchPlan, Any, SourceCursor, object], ComposedRuntime
]


@dataclass(frozen=True, slots=True)
class LaunchMaterials:
    """Readiness evidence for one fresh launch into one empty root.

    The composition root remains the trusted boundary for producing
    the plan and report through the existing enablement planner; the
    service re-verifies every structural claim it can (report bytes,
    READY verdict, identity, fixture-only transport, Shadow posture)
    and refuses to launch otherwise.  A NOT_READY evaluation never
    yields a plan, so refusing composition roots surface the planner's
    own failure instead of these materials.
    """

    plan: RangeOpsLaunchPlan
    report_canonical_json: str

    def __post_init__(self) -> None:
        if not isinstance(self.plan, RangeOpsLaunchPlan):
            raise SiteAgentError(
                "invalid_materials",
                "launch materials require a RangeOpsLaunchPlan",
            )
        if (
            not isinstance(self.report_canonical_json, str)
            or not self.report_canonical_json.strip()
        ):
            raise SiteAgentError(
                "invalid_materials",
                "launch materials require the canonical report JSON",
            )


#: Produces fresh-launch materials for a prospective workflow evidence
#: root.  The composition root proves evidence-root emptiness against
#: the real filesystem for that exact path before declaring it, so a
#: fresh launch and a fixture reset both re-run honest readiness
#: evaluation.  Resume never calls this: a resumed root is legitimately
#: non-empty, so resume revalidates the persisted plan and report bytes
#: instead of re-declaring emptiness.
MaterialsFactory = Callable[[Any], LaunchMaterials]


@dataclass(frozen=True, slots=True)
class CompositionSeam:
    """Process-lifetime composition callables the service depends on.

    ``materials_for`` evaluates readiness for a fresh empty root;
    ``composer`` assembles one resumable runtime at a cursor; and
    ``cycle_catalog`` is fixture-only, clearly simulated presentation
    data describing the declared storyline cycles — never evidence,
    never an evaluation input.
    """

    composer: RuntimeComposer
    materials_for: MaterialsFactory
    cycle_catalog: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)


__all__ = [
    "API_SCHEMA_VERSION",
    "DISCLAIMER",
    "LOOPBACK_HOSTS",
    "SERVICE_EVENTS_SCHEMA",
    "SERVICE_MODE_LABEL",
    "SERVICE_STATE_SCHEMA",
    "ComposedRuntime",
    "CompositionSeam",
    "LaunchMaterials",
    "LaunchRefusedError",
    "MaterialsFactory",
    "RuntimeComposer",
    "ServiceState",
    "SiteAgentError",
    "SourceCursor",
]
