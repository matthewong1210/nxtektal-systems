"""ObservationFrame -> Site Runtime -> FacilityState -> existing consumer."""

from __future__ import annotations

from datetime import datetime, timezone

from nxt_facility import Confidence, FacilityState, Urgency, recommend
from nxt_pilot_ops.adapters import FacilityStateAdapterContext, adapt_facility_state
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario
from nxt_telemetry.bank import (
    SyntheticSensorBank,
    site_config_from_scenario,
    upstream_from_sim,
)

from nxt_site_runtime import (
    InMemoryCheckpointStore,
    SequencedObservationFrame,
    SiteRuntimePipeline,
)

from .conftest import (
    CapturingPublisher,
    CapturingSink,
    ReplayableObservationSource,
    upstream_reference,
)


def test_observation_to_published_state_to_decision_consumer():
    scenario = make_scenario("normal_weekday").model_copy(
        update={"initial_dispenser_frac": 0.0}
    )
    sim = RangeSimulation(scenario, seed=7)
    frame = SyntheticSensorBank(sim).sample()
    upstream = upstream_from_sim(sim)
    publisher = CapturingPublisher()
    sink = CapturingSink()
    checkpoints = InMemoryCheckpointStore()
    batch = SequencedObservationFrame(
        sequence_number=0,
        frame=frame,
        upstream=upstream,
        upstream_source_references=(upstream_reference(frame),),
    )
    source = ReplayableObservationSource([batch])
    runtime = SiteRuntimePipeline(
        site_id="site-range-01",
        deployment_id="deployment-a",
        site_config=site_config_from_scenario(scenario, seed=7),
        observation_source=source,
        publisher=publisher,
        checkpoint_store=checkpoints,
        runtime_sink=sink,
    )

    envelope = runtime.run_once()

    assert publisher.published == [envelope]
    assert source.acknowledged == [0]
    assert sink.published == [envelope]
    assert isinstance(envelope.facility_state, FacilityState)
    assert envelope.facility_state.meta.t_s == frame.t_s == 21600.0
    assert envelope.facility_state.ball_flow.clean_available == 0
    assert envelope.facility_state.ball_flow.conserved is True
    assert envelope.assembly_report.missing_channels == ()
    assert envelope.assembly_report.stale_channels == ()
    assert envelope.assembly_report.consistency_issues == ()
    assert envelope.assembly_report.overall_confidence == 1.0
    assert envelope.assembly_report.provenance_grade == "high"

    recommendations = recommend(envelope.facility_state)
    stockout = [
        recommendation
        for recommendation in recommendations
        if recommendation.rule_id == "stockout_in_progress"
    ]
    assert len(stockout) == 1
    assert stockout[0].urgency is Urgency.NOW
    assert stockout[0].confidence is Confidence.HIGH
    assert {"dispenser", "washer"} <= set(stockout[0].affected_resources)

    shadow_snapshot = adapt_facility_state(
        envelope.facility_state,
        context=FacilityStateAdapterContext(
            site_id=envelope.site_id,
            deployment_id=envelope.deployment_id,
            simulation_midnight=datetime(2026, 8, 8, tzinfo=timezone.utc),
            source_sequence=envelope.sequence_number,
            source_ref=envelope.payload_reference,
            clean_sensed_valid=True,
        ),
    )
    assert shadow_snapshot.site_id == envelope.site_id
    assert shadow_snapshot.deployment_id == envelope.deployment_id
    assert shadow_snapshot.source_sequence == envelope.sequence_number
    assert shadow_snapshot.observed_at == datetime(
        2026, 8, 8, 6, 0, tzinfo=timezone.utc
    )

    checkpoint = checkpoints.load("site-range-01", "deployment-a")
    assert checkpoint.last_successful_sequence == 0
    assert checkpoint.last_envelope_id == envelope.envelope_id
    assert checkpoint.has_pending_publish is False
