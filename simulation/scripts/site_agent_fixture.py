"""Pilot Course A — Site Agent Service fixture (composition root).

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

Every value in this module is synthetic.  No physical facility, device,
or robot is connected; no Modbus, serial, MQTT, Kafka, OPC-UA, ROS 2,
Nav2, vendor SDK, camera, socket, or cloud call happens anywhere in
this path; and nothing here can command a robot, an actuator, or an
emergency stop.

This composition root wires the existing Pilot Course A edge fixture,
the existing enablement evaluation, and the existing Agent Runtime
into the ``nxt_site_agent`` service seam.  It owns the service's
deterministic six-cycle storyline:

    0  17:30 calm inventory                  -> admitted, NO_ACTION
    1  18:30 evening spike                   -> admitted, RECOMMEND
    2  19:00 dispenser load cell silent      -> rejected (missing input)
    3  19:30 stale robot heartbeat           -> rejected (stale)
    4  19:30 uncalibrated dispenser reading  -> rejected (quality)
    5  19:30 corrected redelivery            -> admitted, RECOMMEND

Rejected cycles reuse their publication sequence exactly as the
at-least-once source contract requires, so the corrected redelivery
publishes at the sequence the bad readings could not fill.
"""

from __future__ import annotations

import copy
import dataclasses
import sys
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
from nxt_edge_observation import FixtureRawSampleFeed  # noqa: E402
from nxt_telemetry.observations import SiteConfig  # noqa: E402

from nxt_site_agent import (  # noqa: E402
    ComposedRuntime,
    CompositionSeam,
    LaunchMaterials,
    SourceCursor,
)
from nxt_workflow_enablement import (  # noqa: E402
    EnablementContext,
    EnablementReport,
    OutputLocationPlan,
    RANGE_OPS_REQUIREMENTS_VERSION,
    RANGE_OPS_WORKFLOW_ID,
    RangeOpsEvidence,
    RangeOpsLaunchPlan,
    RangeOpsRuntimeDeclaration,
    RuntimeMode,
    SharedSiteExpectation,
    TransportMode,
    canonical_report_json,
    evaluate_pilot_site,
    pilot_workflow_registry,
    plan_range_ops_launch,
)

from scripts.pilot_course_a_edge_fixture import (  # noqa: E402
    CycleSpec,
    DISCLAIMER,
    EdgeObservationSource,
    PILOT_CYCLES,
    SENSOR_DISPENSER_COUNT,
    SENSOR_DISPENSER_SENSED,
    SITE_ID,
    TOTAL_BALLS,
    OPEN_MINUTE,
    CLOSE_MINUTE,
    FORECAST_BUCKET_MINUTES,
    adapter_kit,
    commissioned_site_payload,
    raw_batch,
)
from scripts.pilot_course_a_enablement_fixture import (  # noqa: E402
    BROKEN_CHANNEL,
    EVIDENCE_RELATIVE_PATHS,
    SIMULATION_MIDNIGHT_ISO,
    adapter_composition_evidence,
    assemble_range_ops_runtime,
    declared_fixture_channels,
    runtime_evidence_root_is_empty,
)

DEPLOYMENT_ID = "pilot-a-site-agent-v0"
SERVICE_SCENARIO_NAME = "pilot-course-a-site-agent-service"

# Deterministic scenario time for the enablement report: the 17:30
# calm-evening evaluation point.  Never a wall clock.
SERVICE_ENABLEMENT_T_S = 63000.0

MISSING_T_S = 68400.0  # 19:00

#: Bounded-run declaration: six declared batches, plus headroom for the
#: explicit source-exhaustion outcome and one retried cycle.
MAX_SERVICE_CYCLES = 8

_MISSING_VARIANT = "missing_dispenser"

#: The service storyline.  Cycles 0-1 are the existing calm/spike pair;
#: cycle 2 is a silent dispenser load cell (both dispenser samples
#: absent from the batch); cycles 3-5 renumber the existing stale,
#: uncalibrated, and corrected-redelivery cycles.
SERVICE_CYCLES: tuple[CycleSpec, ...] = (
    PILOT_CYCLES[0],
    PILOT_CYCLES[1],
    dataclasses.replace(
        PILOT_CYCLES[1],
        cycle_index=2,
        t_s=MISSING_T_S,
        label="19:00 dispenser load cell silent",
        variant=_MISSING_VARIANT,
    ),
    dataclasses.replace(PILOT_CYCLES[2], cycle_index=3),
    dataclasses.replace(PILOT_CYCLES[3], cycle_index=4),
    dataclasses.replace(PILOT_CYCLES[4], cycle_index=5),
)


def service_manifest_payload() -> dict:
    """The shared Pilot Course A manifest, service deployment identity."""
    payload = commissioned_site_payload()
    payload["deployment_id"] = DEPLOYMENT_ID
    return payload


def broken_service_manifest_payload() -> dict:
    """A commissioning-valid candidate whose RangeOps coverage is broken."""
    payload = service_manifest_payload()
    payload["sensor_bindings"] = [
        binding
        for binding in payload["sensor_bindings"]
        if binding["channel"] != BROKEN_CHANNEL
    ]
    return payload


def service_site(payload: dict | None = None) -> CommissionedSite:
    return CommissionedSite.from_dict(
        copy.deepcopy(
            payload if payload is not None else service_manifest_payload()
        )
    )


def service_site_config(site: CommissionedSite) -> SiteConfig:
    context = LegacySiteConfigContext(
        scenario_name=SERVICE_SCENARIO_NAME,
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
                "source_id": "pilot-a-site-agent-context-001",
                "captured_at": "2026-08-08T09:30:00+08:00",
                "captured_by": "site-operator@nxt.example",
                "evidence_uri": (
                    "urn:nxt:evidence:pilot-a-site-agent-context-001"
                ),
                "notes": (
                    "Synthetic consumer context for the Site Agent service "
                    "scenario"
                ),
            }
        ),
    )
    return SiteConfig(**project_legacy_site_config(site, context))


def service_raw_batch(spec: CycleSpec):
    """Build one raw batch, honouring the silent-dispenser variant.

    The silent-dispenser cycle removes both dispenser load-cell samples
    from an otherwise nominal batch: the commissioned device delivered
    nothing, so the adapter kit must reconcile the silence into
    explicit MISSING observations rather than an absent key or a zero.
    """
    if spec.variant != _MISSING_VARIANT:
        return raw_batch(spec)
    nominal = dataclasses.replace(spec, variant="nominal")
    batch = raw_batch(nominal)
    silent = (SENSOR_DISPENSER_COUNT, SENSOR_DISPENSER_SENSED)
    return dataclasses.replace(
        batch,
        load_cells=tuple(
            sample
            for sample in batch.load_cells
            if sample.sensor_id not in silent
        ),
    )


def service_observation_source(
    site: CommissionedSite,
    *,
    consumed_cycles: int = 0,
    first_sequence_number: int = 0,
) -> EdgeObservationSource:
    """Compose the service storyline over the existing edge fixture path."""
    for name, value in (
        ("consumed_cycles", consumed_cycles),
        ("first_sequence_number", first_sequence_number),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if consumed_cycles > len(SERVICE_CYCLES):
        raise ValueError(
            f"cannot resume after {consumed_cycles} cycles: the service "
            f"fixture declares only {len(SERVICE_CYCLES)}"
        )
    return EdgeObservationSource(
        feed=FixtureRawSampleFeed(
            tuple(
                service_raw_batch(spec)
                for spec in SERVICE_CYCLES[consumed_cycles:]
            ),
            first_sequence_number=first_sequence_number,
        ),
        kit=adapter_kit(site),
        specs=SERVICE_CYCLES,
    )


def service_shared_expectation() -> SharedSiteExpectation:
    return SharedSiteExpectation(site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)


def service_enablement_context(
    *, root_is_empty: bool = True
) -> EnablementContext:
    return EnablementContext(
        scenario_name=SERVICE_SCENARIO_NAME,
        scenario_t_s=SERVICE_ENABLEMENT_T_S,
        transport_mode=TransportMode.FIXTURE_ONLY.value,
        physical_execution_reachable=False,
        output_locations=OutputLocationPlan(
            relative_paths=EVIDENCE_RELATIVE_PATHS,
            root_is_empty=root_is_empty,
        ),
    )


def service_runtime_declaration() -> RangeOpsRuntimeDeclaration:
    return RangeOpsRuntimeDeclaration(
        runtime_mode=RuntimeMode.SHADOW.value,
        simulation_midnight_iso=SIMULATION_MIDNIGHT_ISO,
        clean_sensed_valid=True,
        policy_id="ball-availability-guardian",
        policy_version="0.1.0",
        max_cycles=MAX_SERVICE_CYCLES,
    )


def service_range_ops_evidence(payload: dict) -> RangeOpsEvidence:
    return RangeOpsEvidence(
        requirements_version=RANGE_OPS_REQUIREMENTS_VERSION,
        adapter=adapter_composition_evidence(payload),
        declared_fixture_channels=declared_fixture_channels(),
        runtime=service_runtime_declaration(),
    )


def evaluate_service_enablement(
    payload: dict, *, root_is_empty: bool = True
):
    """Evaluate the shared site and all workflows for the service identity."""
    context = service_enablement_context(root_is_empty=root_is_empty)
    registry = pilot_workflow_registry()
    evaluation = evaluate_pilot_site(
        payload,
        expectation=service_shared_expectation(),
        context=context,
        range_ops_evidence=service_range_ops_evidence(payload),
        registry=registry,
    )
    report = EnablementReport.create(
        shared=evaluation.shared,
        workflows=evaluation.workflows,
        context=context,
        registry=registry,
    )
    return evaluation, report


def service_launch_materials(
    workflow_evidence_root: Path, *, payload: dict | None = None
) -> LaunchMaterials:
    """Fresh-launch readiness evidence for one prospective evidence root.

    ``root_is_empty`` is proven against the real filesystem for the
    exact root the service intends to use; the planner then fails
    closed for any NOT_READY verdict, so these materials only exist
    for a launchable configuration.
    """
    resolved_payload = (
        service_manifest_payload() if payload is None else payload
    )
    root_is_empty = runtime_evidence_root_is_empty(
        Path(workflow_evidence_root)
    )
    evaluation, report = evaluate_service_enablement(
        resolved_payload, root_is_empty=root_is_empty
    )
    range_ops = next(
        item
        for item in evaluation.workflows
        if item.workflow_id == RANGE_OPS_WORKFLOW_ID
    )
    plan = plan_range_ops_launch(
        readiness=range_ops,
        shared=evaluation.shared,
        context=service_enablement_context(root_is_empty=root_is_empty),
        evidence=service_range_ops_evidence(resolved_payload),
    )
    return LaunchMaterials(
        plan=plan, report_canonical_json=canonical_report_json(report)
    )


def _scenario_time_label(t_s: float) -> str:
    minutes = int(t_s // 60)
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def service_cycle_catalog() -> tuple[dict, ...]:
    """Fixture-only presentation data describing the declared storyline."""
    return tuple(
        {
            "cycle_index": spec.cycle_index,
            "label": spec.label,
            "scenario_t_s": spec.t_s,
            "scenario_time": _scenario_time_label(spec.t_s),
            "variant": spec.variant,
            "source": "SIMULATED",
        }
        for spec in SERVICE_CYCLES
    )


def service_composer(
    plan: RangeOpsLaunchPlan,
    workflow_evidence_root,
    cursor: SourceCursor,
    runtime_sink,
    *,
    payload: dict | None = None,
) -> ComposedRuntime:
    """Assemble one resumable service runtime at the given cursor."""
    site = service_site(payload)
    source = service_observation_source(
        site,
        consumed_cycles=cursor.consumed_cycles,
        first_sequence_number=cursor.next_sequence_number,
    )
    runtime = assemble_range_ops_runtime(
        plan,
        site,
        Path(workflow_evidence_root),
        source=source,
        runtime_sink=runtime_sink,
        site_config=service_site_config(site),
    )
    base_consumed = cursor.consumed_cycles
    base_sequence = cursor.next_sequence_number

    def read_cursor() -> SourceCursor:
        acknowledged = len(source.feed.acknowledged)
        rejected = len(source.feed.rejected)
        return SourceCursor(
            consumed_cycles=base_consumed + acknowledged + rejected,
            next_sequence_number=base_sequence + acknowledged,
        )

    def adapter_reports() -> dict[int, dict]:
        return {
            cycle_index: report.to_dict()
            for cycle_index, report in sorted(source.reports.items())
        }

    return ComposedRuntime(
        runtime=runtime, cursor=read_cursor, adapter_reports=adapter_reports
    )


def service_composition_seam(
    *, payload_provider=service_manifest_payload
) -> CompositionSeam:
    """The full composition seam the service consumes.

    ``payload_provider`` exists so tests can compose the broken
    manifest and prove the NOT_READY refusal path end to end.
    """

    def materials_for(workflow_evidence_root) -> LaunchMaterials:
        return service_launch_materials(
            Path(workflow_evidence_root), payload=payload_provider()
        )

    def composer(plan, workflow_evidence_root, cursor, runtime_sink):
        return service_composer(
            plan,
            workflow_evidence_root,
            cursor,
            runtime_sink,
            payload=payload_provider(),
        )

    return CompositionSeam(
        composer=composer,
        materials_for=materials_for,
        cycle_catalog=service_cycle_catalog(),
    )


__all__ = [
    "DEPLOYMENT_ID",
    "DISCLAIMER",
    "MAX_SERVICE_CYCLES",
    "SERVICE_CYCLES",
    "SERVICE_ENABLEMENT_T_S",
    "SERVICE_SCENARIO_NAME",
    "SITE_ID",
    "broken_service_manifest_payload",
    "evaluate_service_enablement",
    "service_composer",
    "service_composition_seam",
    "service_cycle_catalog",
    "service_enablement_context",
    "service_launch_materials",
    "service_manifest_payload",
    "service_observation_source",
    "service_range_ops_evidence",
    "service_raw_batch",
    "service_runtime_declaration",
    "service_shared_expectation",
    "service_site",
    "service_site_config",
]
