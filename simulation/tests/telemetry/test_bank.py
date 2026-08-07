"""SyntheticSensorBank: truth-faithful when perfect, honest when imperfect."""

from __future__ import annotations

import json

from nxt_range_ops.core.sim import RangeSimulation

from nxt_telemetry.bank import (
    ChannelImperfection,
    SyntheticSensorBank,
    TelemetryConfig,
    site_config_from_scenario,
    upstream_from_sim,
)
from nxt_telemetry.observations import ObservationStatus, SourceType

from .conftest import rng_states


def perfect_bank(sim) -> SyntheticSensorBank:
    return SyntheticSensorBank(sim)


def test_perfect_bank_mirrors_sim_truth_exactly(sim):
    sim.advance(3600.0)
    frame = perfect_bank(sim).sample()
    by = frame.by_channel()
    counts = sim.ledger.counts()
    assert by["inventory.dispenser.count"].value == sim.dispenser_count()
    assert by["inventory.dispenser.sensed"].value == sim.sensed_dispenser_count()
    assert by["wash.washer.wip"].value == sim.washer_wip()
    for zone in sim.zone_snapshots():
        assert by[f"scan.zone.{zone.zone_id}.balls"].value == zone.balls
        assert by[f"zone.{zone.zone_id}.is_open"].value == zone.is_open
    for station in sim.station_snapshots():
        assert by[f"inventory.station.{station.station_id}.buffer_balls"].value == station.buffer_balls
        assert by[f"station.{station.station_id}.docked"].value == station.docked
    for robot in sim.robot_snapshots():
        assert by[f"robot.{robot.robot_id}.battery_frac"].value == robot.battery_frac
        assert by[f"robot.{robot.robot_id}.activity"].value == robot.activity.value
    capacity, busy, queued = sim.staff_summary()
    assert by["staff.site.busy"].value == busy
    assert by["staff.site.queued"].value == queued
    assert by["charger.site.queue_length"].value == sim.charger_queue_length()
    _ = counts
    for observation in frame.observations:
        assert observation.status is ObservationStatus.OK
        assert observation.source_type is SourceType.SIMULATION  # never claims SENSOR
        assert observation.confidence == 1.0


def test_sampling_consumes_no_sim_rng(sim):
    sim.advance(1800.0)
    before = rng_states(sim)
    bank = perfect_bank(sim)
    for _ in range(5):
        bank.sample()
    assert rng_states(sim) == before


def test_frames_are_byte_reproducible_across_identical_runs(weekday):
    frames = []
    for _ in range(2):
        sim = RangeSimulation(weekday, seed=99)
        bank = SyntheticSensorBank(
            sim,
            TelemetryConfig(
                families={"inventory": ChannelImperfection(noise_rel_sd=0.05)}
            ),
        )
        sim.advance(1800.0)
        frames.append(json.dumps(bank.sample().to_dict(), sort_keys=True))
    assert frames[0] == frames[1]


def test_noise_on_one_family_never_shifts_another(weekday):
    def run(config):
        sim = RangeSimulation(weekday, seed=55)
        bank = SyntheticSensorBank(sim, config)
        sim.advance(1800.0)
        return bank.sample().by_channel()

    noisy_scan = run(
        TelemetryConfig(families={"scan": ChannelImperfection(noise_rel_sd=0.2)})
    )
    quiet = run(TelemetryConfig())
    # scan channels differ; every non-scan channel is byte-identical —
    # per-observation SeedSequence keying means adding noise (or channels)
    # in one family can never shift another family's draws.
    assert any(
        noisy_scan[c].value != quiet[c].value
        for c in noisy_scan
        if c.startswith("scan.")
    )
    for channel in quiet:
        if not channel.startswith("scan."):
            assert noisy_scan[channel].to_dict() == quiet[channel].to_dict()


def test_calibration_bias_applies_without_noise(sim):
    bank = SyntheticSensorBank(
        sim,
        TelemetryConfig(
            families={"inventory": ChannelImperfection(calibration_bias_rel=0.10)}
        ),
    )
    frame = bank.sample()
    reading = frame.by_channel()["inventory.dispenser.count"]
    assert reading.value == sim.dispenser_count() * 1.10


def test_full_dropout_yields_explicit_missing(sim):
    bank = SyntheticSensorBank(
        sim,
        TelemetryConfig(families={"scan": ChannelImperfection(dropout_prob=1.0)}),
    )
    frame = bank.sample()
    scans = [o for o in frame.observations if o.channel.startswith("scan.")]
    assert scans
    for observation in scans:
        assert observation.status is ObservationStatus.MISSING
        assert observation.value is None
        assert observation.confidence == 0.0


def test_delay_returns_older_samples(sim):
    bank = SyntheticSensorBank(
        sim,
        TelemetryConfig(families={"inventory": ChannelImperfection(delay_s=120.0)}),
    )
    first = bank.sample().by_channel()["inventory.dispenser.count"]
    assert first.status is ObservationStatus.MISSING  # nothing old enough yet
    truth_at_start = sim.dispenser_count()
    sim.advance(60.0)
    bank.sample()
    sim.advance(60.0)
    delayed = bank.sample().by_channel()["inventory.dispenser.count"]
    assert delayed.status is ObservationStatus.OK
    assert delayed.value == truth_at_start
    assert delayed.sample_timestamp_s == delayed.available_timestamp_s - 120.0


def test_seq_advances_per_sample_even_without_draws(sim):
    bank = perfect_bank(sim)
    a = bank.sample().by_channel()["wash.washer.wip"]
    b = bank.sample().by_channel()["wash.washer.wip"]
    assert (a.seq, b.seq) == (0, 1)
    assert a.observation_id != b.observation_id


def test_site_config_and_upstream_helpers_mirror_scenario(sim, weekday):
    site = site_config_from_scenario(weekday, seed=321)
    assert site.total_balls == weekday.total_balls
    assert site.zone_ids == tuple(weekday.zone_ids)
    assert dict(site.robot_payload_capacity)["R1"] == weekday.robots[0].payload_capacity_balls
    upstream = upstream_from_sim(sim)
    assert upstream.forecast_balls_per_minute == tuple(sim.forecast_window())
    assert upstream.demand_balls_total == sim.metrics.demand_balls_total
