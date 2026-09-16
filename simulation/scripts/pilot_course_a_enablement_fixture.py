"""Pilot Course A — Multi-Workflow Enablement fixture (composition root).

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

Every value in this module is synthetic.  No physical facility, device,
or robot is connected; no Modbus, serial, MQTT, Kafka, OPC-UA, ROS 2,
Nav2, vendor SDK, camera, socket, or cloud call happens anywhere in this
path; and nothing here can command a robot, an actuator, or an
emergency stop.

This is a **composition root**, deliberately outside
``nxt_workflow_enablement``.  The enablement package is pure
deterministic readiness evaluation and must not import the runtime,
adapter, or policy packages; gathering adapter evidence from the real
edge adapter kit and turning a READY launch plan into the existing
fixture-backed Site Runtime and Agent Runtime is this module's job.

The commissioned manifest is the existing synthetic Pilot Course A
manifest with a dedicated enablement deployment identity, so the one
shared site serves Range Operations, Grounds Condition Intelligence,
and Player Caddy Experience evaluations without duplicating any
commissioning truth.
"""

from __future__ import annotations

import copy
import sys
from datetime import datetime
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_commissioning import (  # noqa: E402
    CommissionedSite,
    LegacySiteConfigContext,
    Provenance,
    project_legacy_site_config,
)
from nxt_telemetry.observations import SiteConfig  # noqa: E402

from nxt_agent_runtime import (  # noqa: E402
    AgentRuntime,
    EvaluationJournal,
    JsonEvaluationCheckpointStore,
    JsonlSnapshotPublisher,
)
from nxt_pilot_ops.ledger import JsonlEventLedger  # noqa: E402
from nxt_site_runtime.checkpoints import JsonCheckpointStore  # noqa: E402
from nxt_site_runtime.ports import ObservationSource  # noqa: E402

from nxt_workflow_enablement import (  # noqa: E402
    AdapterCompositionEvidence,
    CourseModelEvidence,
    EnablementContext,
    EnablementReport,
    OutputLocationPlan,
    PilotSiteEvaluation,
    RANGE_OPS_REQUIREMENTS_VERSION,
    RANGE_OPS_WORKFLOW_ID,
    RangeOpsEvidence,
    RangeOpsLaunchPlan,
    RangeOpsRuntimeDeclaration,
    RuntimeMode,
    SharedSiteExpectation,
    TransportMode,
    WorkflowEnablementError,
    evaluate_pilot_site,
    pilot_workflow_registry,
)

from scripts.pilot_course_a_edge_fixture import (  # noqa: E402
    CALIBRATION_ID_LOAD_CELL,
    DISCLAIMER,
    FACILITY_SYSTEM_CHANNELS,
    OPEN_MINUTE,
    CLOSE_MINUTE,
    FORECAST_BUCKET_MINUTES,
    PILOT_CYCLES,
    ROBOT_IDS,
    SENSOR_DISPENSER_COUNT,
    SENSOR_DISPENSER_SENSED,
    SENSOR_STATION_BUFFER,
    SENSOR_STATION_DOCKED,
    SENSOR_STATION_OPEN,
    SENSOR_WASHER_WIP,
    SENSOR_ZONE_OPEN,
    SITE_ID,
    TOTAL_BALLS,
    adapter_kit,
    commissioned_site_payload,
    pilot_observation_source,
)

DEPLOYMENT_ID = "pilot-a-enablement-v0"
ENABLEMENT_SCENARIO_NAME = "pilot-course-a-multi-workflow-enablement"

# Deterministic scenario/config time for the enablement report: the
# 17:30 calm-evening evaluation point.  Never a wall clock.
ENABLEMENT_T_S = 63000.0

SIMULATION_MIDNIGHT_ISO = "2026-08-08T00:00:00+00:00"

# The bounded Shadow Mode storyline: the calm 17:30 cycle and the 18:30
# spike cycle, plus headroom for the exhaustion outcome.
ENABLEMENT_CYCLES = PILOT_CYCLES[:2]
MAX_SHADOW_CYCLES = 4

# Runtime evidence layout, relative to one workflow's evidence root.
EVIDENCE_RELATIVE_PATHS = (
    "checkpoints/evaluation",
    "checkpoints/site",
    "evaluations.jsonl",
    "ledger.jsonl",
    "snapshots.jsonl",
)

# The commissioned channel the broken candidate manifest omits.
BROKEN_CHANNEL = "inventory.dispenser.count"

# The adapter-local calibration sentinel for commissioned discrete and
# fleet inputs whose calibration status is not_required.  Plain data:
# the enablement layer treats it as an opaque declared identity.
_NOT_REQUIRED_PROFILE_CALIBRATION = "calibration:not-required"


def enablement_manifest_payload() -> dict:
    """The shared Pilot Course A manifest, enablement deployment identity."""
    payload = commissioned_site_payload()
    payload["deployment_id"] = DEPLOYMENT_ID
    return payload


def broken_manifest_payload() -> dict:
    """A commissioning-valid candidate whose RangeOps coverage is broken.

    Dropping the dispenser-count binding leaves the shared site valid --
    commissioning does not force complete workflow coverage -- but Range
    Operations cannot cover its required channel set, so its readiness
    gate must fail while Grounds and Player Caddy results are untouched.
    """
    payload = enablement_manifest_payload()
    payload["sensor_bindings"] = [
        binding
        for binding in payload["sensor_bindings"]
        if binding["channel"] != BROKEN_CHANNEL
    ]
    return payload


def enablement_site(payload: dict | None = None) -> CommissionedSite:
    return CommissionedSite.from_dict(
        copy.deepcopy(
            payload if payload is not None else enablement_manifest_payload()
        )
    )


def enablement_site_config(site: CommissionedSite) -> SiteConfig:
    """Project the shared manifest into the existing SiteConfig contract."""
    context = LegacySiteConfigContext(
        scenario_name=ENABLEMENT_SCENARIO_NAME,
        seed=0,
        total_balls=TOTAL_BALLS,
        staff_capacity=3,
        wet_ground_speed_multiplier=1.0,
        open_minute=OPEN_MINUTE,
        close_minute=CLOSE_MINUTE,
        forecast_bucket_minutes=FORECAST_BUCKET_MINUTES,
        provenance=Provenance.from_dict(
            {
                "source_type": "operator_input",
                "source_id": "pilot-a-enablement-context-001",
                "captured_at": "2026-08-08T09:30:00+08:00",
                "captured_by": "site-operator@nxt.example",
                "evidence_uri": (
                    "urn:nxt:evidence:pilot-a-enablement-context-001"
                ),
                "notes": (
                    "Synthetic consumer context for the multi-workflow "
                    "enablement scenario"
                ),
            }
        ),
    )
    return SiteConfig(**project_legacy_site_config(site, context))


def _profile_calibration_pairs() -> tuple[tuple[str, str], ...]:
    """The adapter-local profile calibration identities, as plain data."""
    pairs = [
        (SENSOR_DISPENSER_COUNT, CALIBRATION_ID_LOAD_CELL),
        (SENSOR_DISPENSER_SENSED, CALIBRATION_ID_LOAD_CELL),
        (SENSOR_WASHER_WIP, CALIBRATION_ID_LOAD_CELL),
        (SENSOR_STATION_BUFFER, CALIBRATION_ID_LOAD_CELL),
        (SENSOR_STATION_OPEN, _NOT_REQUIRED_PROFILE_CALIBRATION),
        (SENSOR_STATION_DOCKED, _NOT_REQUIRED_PROFILE_CALIBRATION),
        (SENSOR_ZONE_OPEN, _NOT_REQUIRED_PROFILE_CALIBRATION),
    ]
    pairs.extend(
        (f"fleet-{robot_id}", _NOT_REQUIRED_PROFILE_CALIBRATION)
        for robot_id in ROBOT_IDS
    )
    return tuple(sorted(pairs))


def adapter_composition_evidence(payload: dict) -> AdapterCompositionEvidence:
    """Compose the real edge adapter kit and report the outcome as data.

    Any fail-closed error from the canonical owners (commissioning
    validation, projection, or adapter-kit composition) becomes a
    composed=False evidence record carrying the owner's own message.
    """
    try:
        site = enablement_site(payload)
        kit = adapter_kit(site)
        channels = tuple(sorted(kit.bindings.channels))
    except Exception as exc:
        return AdapterCompositionEvidence(
            composed=False,
            error=f"{type(exc).__name__}: {exc}",
            adapter_channels=(),
            profile_calibration_ids=(),
        )
    return AdapterCompositionEvidence(
        composed=True,
        error=None,
        adapter_channels=channels,
        profile_calibration_ids=_profile_calibration_pairs(),
    )


def declared_fixture_channels() -> tuple[str, ...]:
    """The canonical channels no V0 adapter family can produce.

    These are the explicitly SIMULATION-typed facility-system inputs the
    existing edge fixture supplies; declaring them here is a coverage
    claim the readiness evaluation checks against the requirement set.
    """
    return tuple(sorted(FACILITY_SYSTEM_CHANNELS))


def shared_site_expectation() -> SharedSiteExpectation:
    return SharedSiteExpectation(
        site_id=SITE_ID, deployment_id=DEPLOYMENT_ID
    )


def runtime_evidence_root_is_empty(evidence_root: Path) -> bool:
    """Check the real filesystem for the declared collision-safety fact.

    ``OutputLocationPlan.root_is_empty`` is declared evidence inside the
    package (which never touches a filesystem); the composition root is
    responsible for making the declaration true.  This helper is that
    check.  It only observes: the path is never created, deleted, or
    mutated here.  A root that exists but is not a directory is a
    collision, and a filesystem error that prevents proving collision
    safety is treated the same way -- the declaration becomes False and
    readiness fails closed instead of raising.
    """
    try:
        if not evidence_root.exists():
            return True
        if not evidence_root.is_dir():
            return False
        return not any(evidence_root.iterdir())
    except OSError:
        return False


def enablement_context(*, root_is_empty: bool = True) -> EnablementContext:
    return EnablementContext(
        scenario_name=ENABLEMENT_SCENARIO_NAME,
        scenario_t_s=ENABLEMENT_T_S,
        transport_mode=TransportMode.FIXTURE_ONLY.value,
        physical_execution_reachable=False,
        output_locations=OutputLocationPlan(
            relative_paths=EVIDENCE_RELATIVE_PATHS,
            root_is_empty=root_is_empty,
        ),
    )


def runtime_declaration() -> RangeOpsRuntimeDeclaration:
    return RangeOpsRuntimeDeclaration(
        runtime_mode=RuntimeMode.SHADOW.value,
        simulation_midnight_iso=SIMULATION_MIDNIGHT_ISO,
        clean_sensed_valid=True,
        policy_id="ball-availability-guardian",
        policy_version="0.1.0",
        max_cycles=MAX_SHADOW_CYCLES,
    )


def range_ops_evidence(payload: dict) -> RangeOpsEvidence:
    return RangeOpsEvidence(
        requirements_version=RANGE_OPS_REQUIREMENTS_VERSION,
        adapter=adapter_composition_evidence(payload),
        declared_fixture_channels=declared_fixture_channels(),
        runtime=runtime_declaration(),
    )


def evaluate_enablement(
    payload: dict,
    *,
    root_is_empty: bool = True,
    course_model_evidence: CourseModelEvidence | None = None,
) -> tuple[PilotSiteEvaluation, EnablementReport]:
    """Evaluate the shared site and all three workflows, then report.

    ``course_model_evidence`` is optional declared Course Model
    evidence (plain data derived by a composition root from a
    validated Course World Model); only the two course workflows
    consume it.
    """
    context = enablement_context(root_is_empty=root_is_empty)
    registry = pilot_workflow_registry()
    evaluation = evaluate_pilot_site(
        payload,
        expectation=shared_site_expectation(),
        context=context,
        range_ops_evidence=range_ops_evidence(payload),
        registry=registry,
        course_model_evidence=course_model_evidence,
    )
    report = EnablementReport.create(
        shared=evaluation.shared,
        workflows=evaluation.workflows,
        context=context,
        registry=registry,
    )
    return evaluation, report


def assemble_range_ops_runtime(
    plan: RangeOpsLaunchPlan,
    site: CommissionedSite,
    evidence_root: Path,
    *,
    specs=ENABLEMENT_CYCLES,
    consumed_cycles: int = 0,
    first_sequence_number: int = 0,
    source=None,
    runtime_sink=None,
    site_config: SiteConfig | None = None,
) -> AgentRuntime:
    """Bounded composition factory: a READY plan becomes the existing loop.

    Trust boundary: ``RangeOpsLaunchPlan`` is plain public data, not an
    unforgeable capability, so this factory cannot prove a plan came from
    ``plan_range_ops_launch``.  The composition root is the trusted
    boundary -- it must obtain the plan from the planner, which fails
    closed for every NOT_READY workflow.  The factory revalidates every
    structural claim it can (plan type, workflow identity, site and
    deployment identity, fixture-only transport, Shadow-Mode posture)
    and refuses anything else, but a caller that hand-builds a plan is
    bypassing readiness evaluation and owns that violation.  The resume
    parameters mirror the underlying fixture source: a restarted
    composition must not replay from the beginning.

    ``source`` lets another composition root supply an already-resumed
    fixture ``ObservationSource`` (``specs``/``consumed_cycles``/
    ``first_sequence_number`` are then that source's responsibility);
    ``runtime_sink`` threads a best-effort visibility sink into the
    existing runtime.  Neither parameter changes plan validation.
    """
    if not isinstance(plan, RangeOpsLaunchPlan):
        raise WorkflowEnablementError(
            "runtime assembly requires a RangeOpsLaunchPlan; refusing to "
            "assemble without one"
        )
    if plan.workflow_id != RANGE_OPS_WORKFLOW_ID:
        raise WorkflowEnablementError(
            f"workflow {plan.workflow_id!r} has no v0 runtime"
        )
    if (plan.site_id, plan.deployment_id) != (
        site.site_id,
        site.deployment_id,
    ):
        raise WorkflowEnablementError(
            "launch plan identity does not match the commissioned site"
        )
    if plan.transport_mode != TransportMode.FIXTURE_ONLY.value:
        raise WorkflowEnablementError(
            "runtime assembly is fixture-only in v0"
        )
    if plan.runtime_mode != RuntimeMode.SHADOW.value:
        raise WorkflowEnablementError(
            "runtime assembly is Shadow Mode only in v0"
        )
    if plan.evidence_paths != EVIDENCE_RELATIVE_PATHS:
        raise WorkflowEnablementError(
            "the plan declares a different evidence layout than this "
            f"factory creates: {plan.evidence_paths!r} vs "
            f"{EVIDENCE_RELATIVE_PATHS!r}"
        )
    if site_config is not None and not isinstance(site_config, SiteConfig):
        raise WorkflowEnablementError(
            "site_config must be a SiteConfig projected from the same "
            "commissioned site; the composition root owns that consistency"
        )
    if source is not None:
        if not isinstance(source, ObservationSource):
            raise WorkflowEnablementError(
                "source must implement the runtime ObservationSource "
                "protocol (peek-until-ack, reject with sequence reuse)"
            )
        if (
            specs is not ENABLEMENT_CYCLES
            or consumed_cycles != 0
            or first_sequence_number != 0
        ):
            raise WorkflowEnablementError(
                "source= supersedes specs/consumed_cycles/"
                "first_sequence_number; resume the supplied source "
                "itself instead of passing resume parameters here"
            )
    else:
        source = pilot_observation_source(
            site,
            specs=specs,
            consumed_cycles=consumed_cycles,
            first_sequence_number=first_sequence_number,
        )
    return AgentRuntime(
        site_id=plan.site_id,
        deployment_id=plan.deployment_id,
        site_config=(
            enablement_site_config(site) if site_config is None else site_config
        ),
        observation_source=source,
        publisher=JsonlSnapshotPublisher(evidence_root / "snapshots.jsonl"),
        ledger=JsonlEventLedger(evidence_root / "ledger.jsonl"),
        journal=EvaluationJournal(evidence_root / "evaluations.jsonl"),
        simulation_midnight=datetime.fromisoformat(
            plan.simulation_midnight_iso
        ),
        clean_sensed_valid=plan.clean_sensed_valid,
        site_checkpoint_store=JsonCheckpointStore(
            evidence_root / "checkpoints" / "site"
        ),
        evaluation_checkpoint_store=JsonEvaluationCheckpointStore(
            evidence_root / "checkpoints" / "evaluation"
        ),
        runtime_sink=runtime_sink,
    )


__all__ = [
    "BROKEN_CHANNEL",
    "DEPLOYMENT_ID",
    "DISCLAIMER",
    "ENABLEMENT_CYCLES",
    "ENABLEMENT_SCENARIO_NAME",
    "ENABLEMENT_T_S",
    "EVIDENCE_RELATIVE_PATHS",
    "MAX_SHADOW_CYCLES",
    "SIMULATION_MIDNIGHT_ISO",
    "SITE_ID",
    "adapter_composition_evidence",
    "assemble_range_ops_runtime",
    "broken_manifest_payload",
    "declared_fixture_channels",
    "enablement_context",
    "enablement_manifest_payload",
    "enablement_site",
    "enablement_site_config",
    "evaluate_enablement",
    "range_ops_evidence",
    "runtime_declaration",
    "runtime_evidence_root_is_empty",
    "shared_site_expectation",
]
