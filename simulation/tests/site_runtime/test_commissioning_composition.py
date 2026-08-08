"""CommissionedSite -> Site Runtime -> canonical state composition."""

from __future__ import annotations

from dataclasses import dataclass, field

from nxt_commissioning import LegacySiteConfigContext
from nxt_facility import FacilityState, recommend
from nxt_site_runtime import (
    InMemoryCheckpointStore,
    SequencedObservationFrame,
    SiteRuntimePipeline,
    SourceReference,
    bind_commissioned_site,
)
from nxt_telemetry.observations import (
    Observation,
    ObservationFrame,
    ObservationStatus,
    SourceType,
    UpstreamInputs,
)


@dataclass
class _Publisher:
    published: list = field(default_factory=list)

    def publish(self, envelope) -> None:
        self.published.append(envelope)


def _observation(channel: str, value, *, t_s: float) -> Observation:
    return Observation(
        channel=channel,
        value=value,
        sample_timestamp_s=t_s,
        available_timestamp_s=t_s,
        status=ObservationStatus.OK,
        source_type=SourceType.SENSOR,
        source_id=f"commissioned.{channel}",
        calibration_id="commissioning-test-calibration",
        confidence=1.0,
    )


def test_commissioned_identity_and_projection_drive_runtime(commissioned_site):
    context = LegacySiteConfigContext(
        scenario_name="commissioned-runtime",
        seed=7,
        total_balls=8000,
        staff_capacity=2,
        wet_ground_speed_multiplier=0.8,
        open_minute=360,
        close_minute=1320,
        forecast_bucket_minutes=30,
        provenance=commissioned_site.provenance,
    )
    binding = bind_commissioned_site(commissioned_site, context)
    assert binding.site_id == commissioned_site.site_id
    assert binding.deployment_id == commissioned_site.deployment_id
    assert binding.site_config.robot_ids == ("robot-01",)
    assert binding.site_config.zone_ids == ("zone-east",)

    t_s = 8 * 60 * 60.0
    values = {
        "robot.robot-01.activity": "idle",
        "robot.robot-01.health": "ok",
        "robot.robot-01.destination": "",
        "robot.robot-01.assigned_zone": "",
        "robot.robot-01.battery_frac": 0.8,
        "robot.robot-01.payload_balls": 0,
        "robot.robot-01.location": "charger",
        "robot.robot-01.estop_latched": False,
        "robot.robot-01.awaiting_human": False,
        "scan.zone.zone-east.balls": 0,
        "zone.zone-east.is_open": True,
        "inventory.dispenser.count": 8000,
        "inventory.dispenser.sensed": 8000.0,
        "wash.washer.wip": 0,
        "charger.site.queue_length": 0,
        "staff.site.busy": 0,
        "staff.site.queued": 0,
    }
    frame = ObservationFrame(
        t_s=t_s,
        observations=tuple(
            _observation(channel, value, t_s=t_s)
            for channel, value in values.items()
        ),
    )
    upstream = UpstreamInputs(
        forecast_balls_per_minute=(0.0,),
        demand_balls_total=0,
        demand_balls_served=0,
        stockout_minutes=0.0,
        service_availability=1.0,
    )
    upstream_reference = SourceReference(
        source_type="external_system",
        source_id="pos.commissioned-site",
        channel="upstream.site",
        reference_id="pos.commissioned-site:o000000",
        sample_timestamp_s=t_s,
        available_timestamp_s=t_s,
        confidence=1.0,
        status="ok",
        calibration_id="not-applicable",
    )
    publisher = _Publisher()
    checkpoints = InMemoryCheckpointStore()
    runtime = SiteRuntimePipeline(
        site_id=binding.site_id,
        deployment_id=binding.deployment_id,
        site_config=binding.site_config,
        publisher=publisher,
        checkpoint_store=checkpoints,
    )

    envelope = runtime.process(
        SequencedObservationFrame(
            sequence_number=0,
            frame=frame,
            upstream=upstream,
            upstream_source_references=(upstream_reference,),
        )
    )

    assert publisher.published == [envelope]
    assert isinstance(envelope.facility_state, FacilityState)
    assert envelope.site_id == commissioned_site.site_id
    assert envelope.deployment_id == commissioned_site.deployment_id
    assert envelope.facility_state.meta.scenario_name == "commissioned-runtime"
    assert envelope.facility_state.ball_flow.conserved is True
    assert envelope.assembly_report.missing_channels == ()
    assert envelope.assembly_report.provenance_grade == "high"
    assert isinstance(recommend(envelope.facility_state), tuple)
    assert checkpoints.load(
        commissioned_site.site_id, commissioned_site.deployment_id
    ).last_successful_sequence == 0
