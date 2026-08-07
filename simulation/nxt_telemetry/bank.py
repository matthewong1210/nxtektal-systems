"""Synthetic sensor bank: emits Observations that behave like future
real sensors, sampled from simulation truth.

Designated sim-side module (like ``nxt_facility.build``): may import the
simulator; reads only the RNG-free query surface (``ledger.counts()``,
``*_snapshots()``, ``dispenser_count()``, ``washer_wip()``,
``sensed_dispenser_count()``, ``staff_summary()``,
``charger_queue_length()``) — never the RNG-drawing sensed accessors.

RNG ISOLATION (load-bearing): every observation's imperfection draws come
from a fresh generator keyed statelessly by
``SeedSequence([site_seed, TELEMETRY_TAG, sha256(channel), seq])`` — zero
draws from the simulator's five spawned streams, and no shared generator,
so adding/removing/reordering channels can never shift another channel's
noise (the structural fix for the Phase 1 sensed-accessor coupling class).
Byte-identical sim replay is preserved by construction and guard-tested.

All imperfection parameters are engineering placeholders
(source: placeholder) — not calibrated against real hardware.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from nxt_range_ops.core.sim import RangeSimulation

from .observations import (
    Observation,
    ObservationFrame,
    ObservationStatus,
    SiteConfig,
    SourceType,
    UpstreamInputs,
)

TELEMETRY_TAG = 0x4E58545445  # "NXTTE" — frozen; re-keying breaks stored noise

# Confidence placeholders (source: placeholder)
_CONFIDENCE = {
    ObservationStatus.OK: 1.0,
    ObservationStatus.STALE: 0.5,
    ObservationStatus.MISSING: 0.0,
}

_SOURCE_ID_BY_FAMILY = {
    "inventory": "synthetic.loadcell",
    "scan": "synthetic.scanrig",
    "robot": "synthetic.fleet_api",
    "wash": "synthetic.facility_system",
    "staff": "synthetic.facility_system",
    "charger": "synthetic.facility_system",
    "zone": "synthetic.facility_system",
    "station": "synthetic.facility_system",
}

CALIBRATION_ID = "cal:placeholder:v0"


@dataclass(frozen=True)
class ChannelImperfection:
    """Per-family synthetic imperfection knobs (all source: placeholder).

    Defaults are all zero: a default bank copies truth with no arithmetic,
    which is what makes the parity guarantee exact."""

    noise_rel_sd: float = 0.0
    noise_abs_sd: float = 0.0
    calibration_bias_rel: float = 0.0
    delay_s: float = 0.0
    dropout_prob: float = 0.0
    cadence_s: float = 0.0

    @property
    def is_perfect(self) -> bool:
        return (
            self.noise_rel_sd == 0.0
            and self.noise_abs_sd == 0.0
            and self.calibration_bias_rel == 0.0
            and self.delay_s == 0.0
            and self.dropout_prob == 0.0
            and self.cadence_s == 0.0
        )


@dataclass(frozen=True)
class TelemetryConfig:
    """Family name -> imperfection. Unlisted families are perfect."""

    families: dict[str, ChannelImperfection] = field(default_factory=dict)

    def for_channel(self, channel: str) -> ChannelImperfection:
        return self.families.get(channel.split(".", 1)[0], _PERFECT)


_PERFECT = ChannelImperfection()


def _channel_key(channel: str) -> int:
    return int.from_bytes(hashlib.sha256(channel.encode()).digest()[:8], "big")


# Distinct purpose tags keep the dropout and noise streams fully
# independent: toggling dropout can never shift a noise realization.
_PURPOSE_DROPOUT = 0
_PURPOSE_NOISE = 1


def _keyed_rng(seed: int, channel: str, seq: int, purpose: int) -> np.random.Generator:
    return np.random.default_rng(
        np.random.SeedSequence(
            [seed, TELEMETRY_TAG, _channel_key(channel), seq, purpose]
        )
    )


def _refresh_dropped(seed: int, channel: str, refresh_seq: int, prob: float) -> bool:
    """Pure predicate: did transmission of this measurement fail?

    Dropout applies to the MEASUREMENT (refresh), not the delivery: a
    dropped measurement never enters history, so consumers see aging data
    (STALE) or, with nothing visible yet, MISSING — matching how real
    sensor links fail."""
    if prob <= 0:
        return False
    return bool(
        _keyed_rng(seed, channel, refresh_seq, _PURPOSE_DROPOUT).random() < prob
    )


class SyntheticSensorBank:
    """Samples simulation truth into Observation frames."""

    def __init__(self, sim: RangeSimulation, config: TelemetryConfig | None = None):
        self._sim = sim
        self._config = config or TelemetryConfig()
        self._seq: dict[str, int] = {}          # delivery counter (observation_id)
        self._refresh_seq: dict[str, int] = {}  # measurement counter (noise identity)
        # per-channel history of (t_sample, value, refresh_seq)
        self._history: dict[str, list[tuple[float, object, int]]] = {}

    # -- truth gathering (RNG-free reads only) --------------------------

    def _truth(self) -> dict[str, object]:
        sim = self._sim
        values: dict[str, object] = {
            "inventory.dispenser.count": sim.dispenser_count(),
            "inventory.dispenser.sensed": float(sim.sensed_dispenser_count()),
            "wash.washer.wip": sim.washer_wip(),
            "charger.site.queue_length": sim.charger_queue_length(),
        }
        capacity, busy, queued = sim.staff_summary()
        values["staff.site.busy"] = busy
        values["staff.site.queued"] = queued
        _ = capacity  # static; lives in SiteConfig, not a channel
        for zone in sim.zone_snapshots():
            values[f"scan.zone.{zone.zone_id}.balls"] = zone.balls
            values[f"zone.{zone.zone_id}.is_open"] = zone.is_open
        for station in sim.station_snapshots():
            values[f"inventory.station.{station.station_id}.buffer_balls"] = (
                station.buffer_balls
            )
            values[f"station.{station.station_id}.is_open"] = station.is_open
            values[f"station.{station.station_id}.docked"] = station.docked
            values[f"station.{station.station_id}.queue_length"] = station.queue_length
        for robot in sim.robot_snapshots():
            rid = robot.robot_id
            values[f"robot.{rid}.battery_frac"] = robot.battery_frac
            values[f"robot.{rid}.payload_balls"] = robot.payload_balls
            values[f"robot.{rid}.activity"] = robot.activity.value
            values[f"robot.{rid}.health"] = robot.health.value
            values[f"robot.{rid}.location"] = robot.location
            # None is not an observation value; empty string round-trips
            values[f"robot.{rid}.destination"] = robot.destination or ""
            values[f"robot.{rid}.assigned_zone"] = robot.assigned_zone or ""
            values[f"robot.{rid}.estop_latched"] = robot.estop_latched
            values[f"robot.{rid}.awaiting_human"] = robot.awaiting_human
        return values

    # -- sampling --------------------------------------------------------

    def sample(self) -> ObservationFrame:
        now = self._sim.now
        truth = self._truth()
        observations = []
        for channel in sorted(truth):
            observations.append(self._observe(channel, truth[channel], now))
        return ObservationFrame(t_s=now, observations=tuple(observations))

    def _observe(self, channel: str, raw: object, now: float) -> Observation:
        imperfection = self._config.for_channel(channel)
        seq = self._seq.get(channel, 0)
        self._seq[channel] = seq + 1
        family_source = _SOURCE_ID_BY_FAMILY.get(channel.split(".", 1)[0], "synthetic")

        def build(value, status, t_sample):
            return Observation(
                channel=channel,
                value=value,
                sample_timestamp_s=t_sample,
                available_timestamp_s=now,
                status=status,
                source_type=SourceType.SIMULATION,
                source_id=family_source,
                calibration_id=CALIBRATION_ID,
                confidence=_CONFIDENCE[status],
                seq=seq,
            )

        if imperfection.is_perfect:
            # No arithmetic at all: exact truth copy (the parity path).
            return build(raw, ObservationStatus.OK, now)

        # -- measurement (refresh): cadence gates it, dropout can lose it --
        history = self._history.setdefault(channel, [])
        due = (
            imperfection.cadence_s <= 0
            or not history
            or now - history[-1][0] >= imperfection.cadence_s
        )
        if due:
            refresh_seq = self._refresh_seq.get(channel, 0)
            self._refresh_seq[channel] = refresh_seq + 1
            if not _refresh_dropped(
                self._sim.seed, channel, refresh_seq, imperfection.dropout_prob
            ):
                history.append((now, raw, refresh_seq))

        # -- delivery: newest measurement old enough to be visible ---------
        visible_index = None
        for index in range(len(history) - 1, -1, -1):
            if history[index][0] <= now - imperfection.delay_s:
                visible_index = index
                break
        if visible_index is None:
            return build(None, ObservationStatus.MISSING, now)
        # trim by VISIBILITY, never a fixed cap: everything older than the
        # currently visible measurement can never be served again
        if visible_index > 0:
            del history[:visible_index]
        t_sample, value, measurement_seq = history[0]

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            adjusted = float(value) * (1.0 + imperfection.calibration_bias_rel)
            if imperfection.noise_rel_sd > 0 or imperfection.noise_abs_sd > 0:
                # keyed by the MEASUREMENT, not the delivery: a held sample
                # re-served later renders the identical noisy value (the
                # two-timestamp contract: one measurement, one value)
                rng = _keyed_rng(
                    self._sim.seed, channel, measurement_seq, _PURPOSE_NOISE
                )
                if imperfection.noise_rel_sd > 0:
                    adjusted *= 1.0 + rng.normal(0.0, imperfection.noise_rel_sd)
                if imperfection.noise_abs_sd > 0:
                    adjusted += rng.normal(0.0, imperfection.noise_abs_sd)
            value = float(adjusted)

        # staleness (placeholder rule): fresh data ages between delay and
        # delay + cadence; anything older means a missed reporting cycle
        stale = (
            imperfection.cadence_s > 0
            and now - t_sample > imperfection.delay_s + 2 * imperfection.cadence_s
        )
        status = ObservationStatus.STALE if stale else ObservationStatus.OK
        return build(value, status, t_sample)


# ---------------------------------------------------------------------------
# Non-sensor input helpers (sim-side mirrors of deployment services)
# ---------------------------------------------------------------------------


def site_config_from_scenario(scenario, seed: int) -> SiteConfig:
    return SiteConfig(
        scenario_name=scenario.name,
        seed=int(seed),
        total_balls=scenario.total_balls,
        washer_throughput_bpm=scenario.washer.balls_per_minute.value,
        washer_batch_size=scenario.washer.batch_size_balls,
        charger_slots=scenario.charger.slots,
        staff_capacity=scenario.human_ops.staff_count,
        wet_ground_speed_multiplier=scenario.skills.wet_ground_speed_multiplier,
        open_minute=scenario.hours.open_minute,
        close_minute=scenario.hours.close_minute,
        forecast_bucket_minutes=scenario.demand.forecast_bucket_minutes,
        # sorted: entity ordering must match build_facility_state's lexical
        # ordering regardless of how the commissioning record lists ids
        zone_ids=tuple(sorted(scenario.zone_ids)),
        station_ids=tuple(sorted(scenario.station_ids)),
        robot_ids=tuple(sorted(scenario.robot_ids)),
        robot_payload_capacity=tuple(
            (r.robot_id, r.payload_capacity_balls)
            for r in sorted(scenario.robots, key=lambda r: r.robot_id)
        ),
        station_buffer_capacity=tuple(
            (s.station_id, s.buffer_capacity_balls)
            for s in sorted(scenario.stations, key=lambda s: s.station_id)
        ),
    )


def upstream_from_sim(sim: RangeSimulation) -> UpstreamInputs:
    metrics = sim.metrics
    return UpstreamInputs(
        forecast_balls_per_minute=tuple(float(x) for x in sim.forecast_window()),
        demand_balls_total=metrics.demand_balls_total,
        demand_balls_served=metrics.demand_balls_served,
        stockout_minutes=metrics.stockout_minutes,
        service_availability=metrics.service_availability,
    )
