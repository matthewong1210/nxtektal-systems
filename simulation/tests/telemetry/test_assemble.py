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


def test_imperfect_assembly_differs_only_in_sensed_fields(sim, weekday):
    sim.advance(3600.0)
    bank = SyntheticSensorBank(
        sim,
        TelemetryConfig(
            families={
                "inventory": ChannelImperfection(noise_rel_sd=0.05),
                "scan": ChannelImperfection(noise_rel_sd=0.2),
            }
        ),
    )
    site = site_config_from_scenario(weekday, seed=321)
    assembled, _ = assemble_from_observations(
        bank.sample(), site, upstream_from_sim(sim)
    )
    built = build_facility_state(sim)
    diff = sensed_diff_paths(assembled.to_dict(), built.to_dict())
    assert diff  # noise did something
    for path in diff:
        assert any(
            path.startswith(prefix) for prefix in SENSED_FIELD_PREFIXES
        ), f"non-sensed field drifted: {path}"


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
