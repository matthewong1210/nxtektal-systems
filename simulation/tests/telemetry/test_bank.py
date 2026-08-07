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
    # an IMPERFECT bank — the perfect path never constructs a generator,
    # so only noisy/dropout sampling actually exercises RNG isolation
    bank = SyntheticSensorBank(
        sim,
        TelemetryConfig(
            families={
                "inventory": ChannelImperfection(noise_rel_sd=0.05, dropout_prob=0.3),
                "scan": ChannelImperfection(delay_s=60.0, cadence_s=30.0),
            }
        ),
    )
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


def test_noisy_family_unshifted_by_noise_on_an_earlier_family(weekday):
    """Kills the shared-generator mutant: 'inventory' sorts before 'scan',
    so with one shared stream, inventory draws would shift every scan
    value. The stateless per-measurement keying makes scan values
    identical whether or not inventory is also noisy."""

    def scan_values(config):
        sim = RangeSimulation(weekday, seed=77)
        bank = SyntheticSensorBank(sim, config)
        sim.advance(1800.0)
        return {
            c: o.value
            for c, o in bank.sample().by_channel().items()
            if c.startswith("scan.")
        }

    scan_only = scan_values(
        TelemetryConfig(families={"scan": ChannelImperfection(noise_rel_sd=0.2)})
    )
    both = scan_values(
        TelemetryConfig(
            families={
                "inventory": ChannelImperfection(noise_rel_sd=0.2, dropout_prob=0.5),
                "scan": ChannelImperfection(noise_rel_sd=0.2),
            }
        )
    )
    assert scan_only == both


def test_held_measurement_keeps_one_noisy_value(sim, weekday):
    """One measurement, one value: a delayed/held sample re-served on later
    calls must render the identical noisy value (two-timestamp contract)."""
    bank = SyntheticSensorBank(
        sim,
        TelemetryConfig(
            families={"inventory": ChannelImperfection(noise_rel_sd=0.05, cadence_s=600.0)}
        ),
    )
    first = bank.sample().by_channel()["inventory.dispenser.count"]
    repeats = [
        bank.sample().by_channel()["inventory.dispenser.count"] for _ in range(3)
    ]
    for repeat in repeats:
        assert repeat.sample_timestamp_s == first.sample_timestamp_s
        assert repeat.value == first.value


def test_toggling_dropout_never_shifts_noise(weekday):
    """Dropout and noise draw from independent keyed streams."""

    def dispenser_value(dropout_prob):
        sim = RangeSimulation(weekday, seed=13)
        bank = SyntheticSensorBank(
            sim,
            TelemetryConfig(
                families={
                    "inventory": ChannelImperfection(
                        noise_rel_sd=0.05, dropout_prob=dropout_prob
                    )
                }
            ),
        )
        sim.advance(600.0)
        return bank.sample().by_channel()["inventory.dispenser.count"].value

    assert dispenser_value(0.0) == dispenser_value(1e-12)


def test_large_delay_eventually_delivers(sim):
    """delay_s beyond any fixed history cap must still deliver once data
    ages in — the trim is by visibility, never a row-count cap."""
    bank = SyntheticSensorBank(
        sim,
        TelemetryConfig(families={"wash": ChannelImperfection(delay_s=700.0)}),
    )
    truth_at_start = sim.washer_wip()
    for _ in range(700):  # sample every sim-second
        bank.sample()
        sim.advance(1.0)
    delivered = bank.sample().by_channel()["wash.washer.wip"]
    assert delivered.status is ObservationStatus.OK
    assert delivered.value == truth_at_start


def test_stale_emitted_when_measurements_are_dropped(weekday):
    """STALE end-to-end: cadence 60s, heavy dropout — once a delivered
    measurement's age exceeds delay + 2*cadence, status must be STALE.
    Dropout applies to measurements, so held data ages honestly."""
    sim = RangeSimulation(weekday, seed=5)
    bank = SyntheticSensorBank(
        sim,
        TelemetryConfig(
            families={"wash": ChannelImperfection(cadence_s=60.0, dropout_prob=0.9)}
        ),
    )
    statuses = []
    for _ in range(40):
        statuses.append(bank.sample().by_channel()["wash.washer.wip"].status)
        sim.advance(60.0)
    assert ObservationStatus.STALE in statuses


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
