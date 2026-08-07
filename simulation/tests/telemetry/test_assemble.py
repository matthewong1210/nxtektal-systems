"""The replacement guarantee: telemetry-assembled FacilityState == built.

Founder adjustment 4: with perfect synthetic telemetry (no noise, no
delay, no dropout) the assembled state must equal build_facility_state(sim)
field-by-field over a full deterministic episode — test-enforced here.
"""

from __future__ import annotations

from nxt_range_ops.config.models import OperatingHoursConfig
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.scenarios.generators import make_scenario

from nxt_facility.build import build_facility_state
from nxt_telemetry.assemble import assemble_from_observations
from nxt_telemetry.bank import (
    ChannelImperfection,
    SyntheticSensorBank,
    TelemetryConfig,
    site_config_from_scenario,
    upstream_from_sim,
)
from nxt_telemetry.observations import SENSED_FIELD_PREFIXES, sensed_diff_paths


def short_day_scenario():
    scenario = make_scenario("normal_weekday")
    return scenario.model_copy(
        update={"hours": OperatingHoursConfig(open_minute=360, close_minute=480)}
    )


def test_parity_over_a_full_deterministic_episode():
    """THE swap proof. Perfect bank, whole policy-driven day, exact equality."""
    scenario = short_day_scenario()
    seed = 4242
    env = RangeOpsEnv(scenario)
    policy = make_baseline("inventory_threshold", scenario, env.catalog, seed=seed)
    obs, info = env.reset(seed=seed)
    policy.reset()
    bank = SyntheticSensorBank(env.sim)
    site = site_config_from_scenario(scenario, seed=seed)

    def assert_parity():
        frame = bank.sample()
        assembled, report = assemble_from_observations(
            frame, site, upstream_from_sim(env.sim)
        )
        built = build_facility_state(env.sim)
        assert assembled == built  # frozen-dataclass equality, every field
        assert report.missing_channels == ()
        assert report.stale_channels == ()
        assert report.consistency_issues == ()
        assert report.provenance_grade == "high"

    assert_parity()
    while True:
        action = policy.act(obs, info)
        obs, _, terminated, truncated, info = env.step(action)
        assert_parity()
        if terminated or truncated:
            break


def test_parity_survives_non_lexical_entity_ids():
    """Guards the ordering trap: with >=10 robots, config order (R1..R12)
    differs from lexical order (R1, R10, R11, R12, R2, ...) — parity must
    hold anyway, not by coincidence of single-digit ids."""
    from nxt_range_ops.scenarios.generators import _base_robots

    scenario = short_day_scenario().model_copy(update={"robots": _base_robots(12)})
    seed = 909
    env = RangeOpsEnv(scenario)
    policy = make_baseline("inventory_threshold", scenario, env.catalog, seed=seed)
    obs, info = env.reset(seed=seed)
    policy.reset()
    bank = SyntheticSensorBank(env.sim)
    site = site_config_from_scenario(scenario, seed=seed)
    for _ in range(10):
        action = policy.act(obs, info)
        obs, _, terminated, truncated, info = env.step(action)
        assembled, report = assemble_from_observations(
            bank.sample(), site, upstream_from_sim(env.sim)
        )
        assert assembled == build_facility_state(env.sim)
        assert report.provenance_grade == "high"
        if terminated or truncated:
            break


def test_imperfect_assembly_differs_only_in_sensed_fields(sim, weekday):
    sim.advance(3600.0)
    baseline, _ = assemble_from_observations(
        SyntheticSensorBank(sim).sample(),
        site_config_from_scenario(weekday, seed=321),
        upstream_from_sim(sim),
    )
    # every family imperfect — the confinement guarantee must hold across
    # the full derivation fan-out (fleet counts, robots_present, zones_open)
    bank = SyntheticSensorBank(
        sim,
        TelemetryConfig(
            families={
                "inventory": ChannelImperfection(noise_rel_sd=0.05),
                "scan": ChannelImperfection(noise_rel_sd=0.2),
                "robot": ChannelImperfection(dropout_prob=0.5),
                "station": ChannelImperfection(dropout_prob=0.5),
                "zone": ChannelImperfection(dropout_prob=0.5),
                "staff": ChannelImperfection(dropout_prob=1.0),
                "wash": ChannelImperfection(noise_abs_sd=30.0),
                "charger": ChannelImperfection(dropout_prob=1.0),
            }
        ),
    )
    site = site_config_from_scenario(weekday, seed=321)
    assembled, _ = assemble_from_observations(
        bank.sample(), site, upstream_from_sim(sim), previous=baseline
    )
    built = build_facility_state(sim)
    diff = sensed_diff_paths(assembled.to_dict(), built.to_dict())
    assert diff  # imperfection did something
    for path in diff:
        assert any(
            path.startswith(prefix) for prefix in SENSED_FIELD_PREFIXES
        ), f"non-sensed field drifted: {path}"


def test_robot_dropout_with_previous_state_never_crashes(sim, weekday):
    """Regression for the enum-backfill crash: MISSING robot channels
    backfilled from a previous state (whose activity/health are enum
    members) must reconstruct valid snapshots, chained over many frames."""
    site = site_config_from_scenario(weekday, seed=321)
    previous, _ = assemble_from_observations(
        SyntheticSensorBank(sim).sample(), site, upstream_from_sim(sim)
    )
    bank = SyntheticSensorBank(
        sim, TelemetryConfig(families={"robot": ChannelImperfection(dropout_prob=0.3)})
    )
    for _ in range(30):
        sim.advance(60.0)
        previous, _ = assemble_from_observations(
            bank.sample(), site, upstream_from_sim(sim), previous=previous
        )
    assert previous.fleet.total == len(site.robot_ids)


def test_closed_zone_stays_closed_through_dropout(sim, weekday):
    """A dropout must never silently flip a closed zone open: last-known
    state is the conservative backfill."""
    import dataclasses

    from nxt_range_ops.core.entities import ZoneStateSnapshot

    site = site_config_from_scenario(weekday, seed=321)
    previous, _ = assemble_from_observations(
        SyntheticSensorBank(sim).sample(), site, upstream_from_sim(sim)
    )
    closed_zone = dataclasses.replace(previous.zones[0], is_open=False)
    previous = dataclasses.replace(
        previous, zones=(closed_zone,) + previous.zones[1:]
    )
    bank = SyntheticSensorBank(
        sim, TelemetryConfig(families={"zone": ChannelImperfection(dropout_prob=1.0)})
    )
    state, report = assemble_from_observations(
        bank.sample(), site, upstream_from_sim(sim), previous=previous
    )
    assert isinstance(state.zones[0], ZoneStateSnapshot)
    assert state.zones[0].is_open is False  # not fabricated open
    assert state.environment.zones_open == previous.environment.zones_open - 1 or (
        state.environment.zones_open
        == sum(1 for z in state.zones if z.is_open)
    )
    assert any(c.startswith("zone.") for c in report.missing_channels)


def test_stale_observations_capped_and_reported(sim, weekday):
    """Assembler does its own staleness math: an OK-labeled observation
    far older than the frame is treated as stale."""
    import dataclasses as _dc

    from nxt_telemetry.observations import ObservationFrame

    site = site_config_from_scenario(weekday, seed=321)
    sim.advance(7200.0)
    frame = SyntheticSensorBank(sim).sample()
    aged = tuple(
        _dc.replace(
            o,
            sample_timestamp_s=o.sample_timestamp_s - 3600.0,
        )
        if o.channel == "inventory.dispenser.count"
        else o
        for o in frame.observations
    )
    state, report = assemble_from_observations(
        ObservationFrame(t_s=frame.t_s, observations=aged),
        site,
        upstream_from_sim(sim),
    )
    assert "inventory.dispenser.count" in report.stale_channels
    assert report.overall_confidence <= 0.5
    assert report.provenance_grade != "high"


def test_missing_channels_backfill_and_are_reported(sim, weekday):
    sim.advance(1800.0)
    site = site_config_from_scenario(weekday, seed=321)
    perfect_frame = SyntheticSensorBank(sim).sample()
    baseline_state, _ = assemble_from_observations(
        perfect_frame, site, upstream_from_sim(sim)
    )
    dropped_bank = SyntheticSensorBank(
        sim, TelemetryConfig(families={"scan": ChannelImperfection(dropout_prob=1.0)})
    )
    state, report = assemble_from_observations(
        dropped_bank.sample(),
        site,
        upstream_from_sim(sim),
        previous=baseline_state,
    )
    assert any(c.startswith("scan.") for c in report.missing_channels)
    # backfilled from previous: zone balls survive the dropout
    assert state.ball_flow.on_field == baseline_state.ball_flow.on_field
    assert report.provenance_grade in ("medium", "low")
    assert report.overall_confidence < 1.0


def test_consistency_issue_reported_when_counts_disagree(sim, weekday):
    sim.advance(1800.0)
    site = site_config_from_scenario(weekday, seed=321)
    biased = SyntheticSensorBank(
        sim,
        TelemetryConfig(
            families={"inventory": ChannelImperfection(calibration_bias_rel=0.2)}
        ),
    )
    state, report = assemble_from_observations(
        biased.sample(), site, upstream_from_sim(sim)
    )
    assert state.ball_flow.conserved is False  # honest consistency alarm
    assert any("conserv" in issue for issue in report.consistency_issues)


def test_negative_readings_clamped_with_issue(sim, weekday):
    sim.advance(600.0)
    site = site_config_from_scenario(weekday, seed=321)
    wild = SyntheticSensorBank(
        sim,
        TelemetryConfig(
            families={"staff": ChannelImperfection(noise_abs_sd=500.0)}
        ),
    )
    state, report = assemble_from_observations(
        wild.sample(), site, upstream_from_sim(sim)
    )
    assert state.staff.busy >= 0
    assert state.staff.queued_requests >= 0
    # the clamp must be REPORTED, not silent
    assert any("clamped" in issue for issue in report.consistency_issues)
