"""NXTektal Pilot Site Workflow Enablement V0 -- readiness gating only.

One shared commissioned site serves multiple workflows.  This package
owns exactly four fact classes and no others: workflow identity
registration, versioned per-workflow requirement definitions, the
independent per-workflow readiness evaluation over already-validated
commissioned truth, and the deterministic enablement report plus the
fixture-only launch-plan data derived from it.

It owns none of:

* physical static site facts, channels, units, or calibration identity
  (the commissioning package remains that source of truth; this package
  only consumes its validated contracts and one-way projections);
* observation semantics, raw device conversion, state assembly,
  orchestration, publication, checkpointing, evaluation lifecycle,
  policy, trace, human workflow, memory, or any facility state;
* runtime construction: turning a launch plan into a running composition
  belongs to composition-root scripts, never to this package.

Readiness is evaluated separately per registered workflow.  Range
Operations may be READY_FOR_FIXTURE_SHADOW_MODE while Grounds Condition
Intelligence and Player Caddy Experience report NOT_READY with their
exact missing prerequisites; registering a prerequisite never implies
the capability exists, and a NOT_READY workflow gets no runtime, no
state, no evaluation, and no evidence.

V0 is deterministic and **fixture-backed** only.  Transport is
explicitly FIXTURE_ONLY: there is no Modbus, serial, MQTT, Kafka,
OPC-UA, ROS 2, Nav2, vendor SDK, camera, cloud, or socket path here, no
physical facility is connected, physical execution is unreachable by
construction, and no robot, actuator, motion, charging, or
emergency stop surface exists or may be added.  A READY verdict authorizes only a
bounded, fixture-backed Shadow Mode composition and never implies live
device connectivity or physical execution.

See ``simulation/docs/workflow_enablement_v0.md`` for the gate list, the
per-workflow requirement matrices, and the exact next seams.
"""

from .evaluation import (
    SHARED_SITE_INVALID,
    GateResult,
    PilotSiteEvaluation,
    ReadinessVerdict,
    SharedSiteResult,
    SharedSiteVerdict,
    WorkflowReadiness,
    evaluate_pilot_site,
    evaluate_shared_site,
    evaluate_workflows,
)
from .evidence import (
    AdapterCompositionEvidence,
    CourseModelEvidence,
    EnablementContext,
    OutputLocationPlan,
    RangeOpsEvidence,
    RangeOpsRuntimeDeclaration,
    RuntimeMode,
    SharedSiteExpectation,
    TransportMode,
)
from .identity import (
    GROUNDS_WORKFLOW_ID,
    PILOT_WORKFLOW_IDS,
    PLAYER_CADDY_WORKFLOW_ID,
    RANGE_OPS_WORKFLOW_ID,
    WORKFLOW_REGISTRY_SCHEMA,
    WorkflowDefinition,
    WorkflowEnablementError,
    WorkflowRegistry,
)
from .launch import RangeOpsLaunchPlan, plan_range_ops_launch
from .report import (
    ENABLEMENT_REPORT_SCHEMA,
    SIMULATED_SCENARIO_DISCLAIMER,
    EnablementReport,
    canonical_report_json,
    verify_report_payload,
)
from .requirements import (
    GROUNDS_MAP_PREREQUISITE_IDS,
    GROUNDS_REQUIREMENTS_VERSION,
    PLAYER_CADDY_REQUIREMENTS_VERSION,
    RANGE_OPS_REQUIREMENTS_VERSION,
    RANGE_OPS_ROBOT_FIELDS,
    RANGE_OPS_SITE_CHANNELS,
    RANGE_OPS_STATION_CHANNEL_TEMPLATES,
    RANGE_OPS_ZONE_CHANNEL_TEMPLATES,
    REQUIRED_MAP_QUERY_KINDS,
    RequirementResult,
    RequirementStatus,
    grounds_prerequisites,
    pilot_workflow_registry,
    player_caddy_prerequisites,
    required_range_ops_channels,
)

__all__ = [
    "ENABLEMENT_REPORT_SCHEMA",
    "GROUNDS_MAP_PREREQUISITE_IDS",
    "GROUNDS_REQUIREMENTS_VERSION",
    "GROUNDS_WORKFLOW_ID",
    "PILOT_WORKFLOW_IDS",
    "PLAYER_CADDY_REQUIREMENTS_VERSION",
    "PLAYER_CADDY_WORKFLOW_ID",
    "RANGE_OPS_REQUIREMENTS_VERSION",
    "RANGE_OPS_ROBOT_FIELDS",
    "RANGE_OPS_SITE_CHANNELS",
    "RANGE_OPS_STATION_CHANNEL_TEMPLATES",
    "RANGE_OPS_WORKFLOW_ID",
    "RANGE_OPS_ZONE_CHANNEL_TEMPLATES",
    "REQUIRED_MAP_QUERY_KINDS",
    "SHARED_SITE_INVALID",
    "SIMULATED_SCENARIO_DISCLAIMER",
    "WORKFLOW_REGISTRY_SCHEMA",
    "AdapterCompositionEvidence",
    "CourseModelEvidence",
    "EnablementContext",
    "EnablementReport",
    "GateResult",
    "OutputLocationPlan",
    "PilotSiteEvaluation",
    "RangeOpsEvidence",
    "RangeOpsLaunchPlan",
    "RangeOpsRuntimeDeclaration",
    "ReadinessVerdict",
    "RequirementResult",
    "RequirementStatus",
    "RuntimeMode",
    "SharedSiteExpectation",
    "SharedSiteResult",
    "SharedSiteVerdict",
    "TransportMode",
    "WorkflowDefinition",
    "WorkflowEnablementError",
    "WorkflowReadiness",
    "WorkflowRegistry",
    "canonical_report_json",
    "evaluate_pilot_site",
    "evaluate_shared_site",
    "evaluate_workflows",
    "grounds_prerequisites",
    "pilot_workflow_registry",
    "plan_range_ops_launch",
    "player_caddy_prerequisites",
    "required_range_ops_channels",
    "verify_report_payload",
]
