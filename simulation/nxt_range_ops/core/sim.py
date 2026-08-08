"""The discrete-event operational simulator (SimPy).

Models one operating day of the full ball-flow loop:

    dispenser -> customer demand -> range zones -> robot collection
    -> robot payload -> handoff/washer queue -> washing -> dispenser

with facility hours, time-varying stochastic demand, forecast uncertainty,
zone closures, robot battery/payload/health, station and charger capacity
with queueing, washer throughput, robot failures, latched emergency stops,
human intervention, and noisy/delayed sensors.

Determinism: all randomness flows through named ``numpy`` generators spawned
from one seed; simultaneous SimPy events resolve in scheduling order, and all
fleet iteration is in sorted-id order — so a seed plus an action sequence
replays to an identical event log.

Simulated time is seconds since midnight. The episode starts at facility
open and ends (terminated) at facility close.
"""

from __future__ import annotations

from typing import Generator, Optional

import numpy as np
import simpy

from nxt_range_ops.config.models import (
    Point2D,
    RangeOpsScenario,
    ZoneConfig,
)
from nxt_range_ops.core import ledger as ledger_mod
from nxt_range_ops.core.directives import (
    AssignCollection,
    Directive,
    PauseRobot,
    ReassignRobot,
    RequestHumanAssistance,
    ResumeRobot,
    SendToCharge,
    SendToHandoff,
    Wait,
    directive_to_dict,
)
from nxt_range_ops.core.entities import (
    HumanAssistReason,
    RobotActivity,
    RobotHealth,
    RobotStateSnapshot,
    StationStateSnapshot,
    ZoneStateSnapshot,
)
from nxt_range_ops.core.events import EventKind, EventLog
from nxt_range_ops.core.ledger import BallLedger
from nxt_range_ops.core.metrics import OpsMetrics
from nxt_range_ops.core.safety import SafetyShield, ShieldDecision
from nxt_range_ops.core.skills import (
    MockSkillOutcomeModel,
    SkillOutcomeModel,
    SkillRequest,
    SkillType,
)

_QUEUE_ACTIVITIES = {RobotActivity.QUEUED_HANDOFF: "handoff", RobotActivity.QUEUED_CHARGER: "charger"}
_IDLE_DRAIN_ACTIVITIES = frozenset(
    {
        RobotActivity.IDLE,
        RobotActivity.PAUSED,
        RobotActivity.QUEUED_HANDOFF,
        RobotActivity.QUEUED_CHARGER,
        RobotActivity.AWAITING_HUMAN,
    }
)


class _Robot:
    """Internal mutable robot state. External views use RobotStateSnapshot."""

    def __init__(self, cfg, start: Point2D):
        self.cfg = cfg
        self.robot_id: str = cfg.robot_id
        self.activity = RobotActivity.IDLE
        self.health = RobotHealth.OK
        self.battery_wh = cfg.battery_capacity_wh.value * cfg.initial_battery_frac
        self.payload_balls = 0
        self.pos = Point2D(x_m=start.x_m, y_m=start.y_m)
        self.location_label = "charger"
        self.destination_label: Optional[str] = None
        self.assigned_zone: Optional[str] = None
        self.estop_latched = False
        self.awaiting_human = False
        self.failure_note = ""
        self.task_proc: Optional[simpy.Process] = None
        # In-flight travel bookkeeping for mid-travel interruption:
        # (start_pos, dest_pos, t0, duration_s, energy_wh_total)
        self.travel: Optional[tuple[Point2D, Point2D, float, float, float]] = None
        self.charging_since: Optional[float] = None
        self.last_activity_change_s = 0.0

    @property
    def battery_frac(self) -> float:
        return self.battery_wh / self.cfg.battery_capacity_wh.value

    @property
    def payload_capacity(self) -> int:
        return self.cfg.payload_capacity_balls


def _merge_windows(windows) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent time windows into disjoint spans.

    Each zone/station toggles a single is_open flag, so overlapping raw
    windows would race (an inner window's end would reopen mid-closure).
    Disjoint spans make the unconditional open/close toggles correct.
    """
    spans = sorted((w.start_minute, w.end_minute) for w in windows)
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


class _Zone:
    def __init__(self, cfg: ZoneConfig):
        self.cfg = cfg
        self.zone_id = cfg.zone_id
        self.is_open = True


class _Station:
    def __init__(self, cfg, resource: simpy.Resource):
        self.cfg = cfg
        self.station_id = cfg.station_id
        self.is_open = True
        self.resource = resource


class RangeSimulation:
    """One operating day of range operations, driven by fleet directives."""

    def __init__(
        self,
        scenario: RangeOpsScenario,
        seed: int,
        skill_model: Optional[SkillOutcomeModel] = None,
    ):
        self.scenario = scenario
        self.seed = int(seed)
        master = np.random.default_rng(self.seed)
        (
            self._rng_demand,
            self._rng_skills,
            self._rng_failures,
            self._rng_sensors,
            self._rng_forecast,
        ) = master.spawn(5)

        self.env = simpy.Environment(initial_time=scenario.hours.open_seconds)
        self.events = EventLog()
        self.metrics = OpsMetrics()
        self.shield = SafetyShield(self)

        self._robots: dict[str, _Robot] = {}
        for rc in sorted(scenario.robots, key=lambda r: r.robot_id):
            robot = _Robot(rc, scenario.charger.position)
            robot.last_activity_change_s = self.now
            self._robots[rc.robot_id] = robot

        self._zones: dict[str, _Zone] = {
            z.zone_id: _Zone(z) for z in sorted(scenario.zones, key=lambda z: z.zone_id)
        }
        self._stations: dict[str, _Station] = {}
        for sc in sorted(scenario.stations, key=lambda s: s.station_id):
            self._stations[sc.station_id] = _Station(
                sc, simpy.Resource(self.env, capacity=sc.dock_slots)
            )
        self._charger = simpy.Resource(self.env, capacity=scenario.charger.slots)
        self._human_staff = simpy.Resource(self.env, capacity=scenario.human_ops.staff_count)

        self.skill_model: SkillOutcomeModel = skill_model or MockSkillOutcomeModel(
            scenario.skills,
            {r.robot_id: r.speed_mps.value for r in scenario.robots},
        )

        self.ledger = self._build_ledger()
        self._forecast_buckets = self._generate_forecast()
        self._inventory_readings: list[tuple[float, float]] = []
        self._facility_closed_emitted = False

        self._start_background_processes()
        self.events.emit(
            self.now,
            EventKind.EPISODE_START,
            scenario=scenario.name,
            seed=self.seed,
            total_balls=self.ledger.total,
        )

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _build_ledger(self) -> BallLedger:
        s = self.scenario
        locations = (
            [ledger_mod.DISPENSER, ledger_mod.WASHER]
            + [ledger_mod.zone_loc(z) for z in s.zone_ids]
            + [ledger_mod.robot_loc(r) for r in s.robot_ids]
            + [ledger_mod.station_loc(st) for st in s.station_ids]
        )
        dispenser = int(round(s.total_balls * s.initial_dispenser_frac))
        remainder = s.total_balls - dispenser
        weights = np.array([z.landing_weight for z in s.zones], dtype=float)
        weights = weights / weights.sum()
        initial = {ledger_mod.DISPENSER: dispenser}
        zone_counts = np.floor(weights * remainder).astype(int)
        # Put rounding leftovers in the first zone (deterministic).
        zone_counts[0] += remainder - int(zone_counts.sum())
        for zc, n in zip(s.zones, zone_counts):
            initial[ledger_mod.zone_loc(zc.zone_id)] = int(n)
        return BallLedger(locations, initial)

    def _generate_forecast(self) -> list[float]:
        """Per-bucket forecast rates for the whole day, frozen at reset.

        Spikes are deliberately absent from the forecast: they are the
        unforecast component. Bias and per-bucket noise model systematic and
        random forecast error.
        """
        d = self.scenario.demand
        h = self.scenario.hours
        bucket_min = d.forecast_bucket_minutes
        buckets = []
        minute = h.open_minute
        while minute < h.close_minute:
            base = d.base_rate_at(minute + bucket_min / 2.0)
            noise = 1.0 + self._rng_forecast.normal(0.0, d.forecast_noise_sd)
            buckets.append(max(0.0, base * d.forecast_bias * max(0.0, noise)))
            minute += bucket_min
        return buckets

    def _start_background_processes(self) -> None:
        # Closures/outages start first so windows already active at facility
        # open take effect before the first demand draw of the day.
        for zone_id in sorted(self._zones):
            zone = self._zones[zone_id]
            for start_min, end_min in _merge_windows(zone.cfg.closure_windows):
                if end_min * 60.0 <= self.now:
                    continue  # window already over before facility open
                self.env.process(self._zone_closure_proc(zone, start_min, end_min))
        for station_id in sorted(self._stations):
            station = self._stations[station_id]
            for start_min, end_min in _merge_windows(station.cfg.outage_windows):
                if end_min * 60.0 <= self.now:
                    continue
                self.env.process(self._station_outage_proc(station, start_min, end_min))
        self.env.process(self._demand_proc())
        self.env.process(self._washer_proc())
        self.env.process(self._sensor_proc())
        for robot_id in sorted(self._robots):
            self.env.process(self._failure_proc(self._robots[robot_id]))

    # ------------------------------------------------------------------
    # Public state queries (used by shield, env, policies, logging)
    # ------------------------------------------------------------------

    @property
    def now(self) -> float:
        return float(self.env.now)

    @property
    def minute_of_day(self) -> float:
        return self.now / 60.0

    @property
    def facility_open(self) -> bool:
        h = self.scenario.hours
        return h.open_seconds <= self.now < h.close_seconds

    @property
    def facility_closed(self) -> bool:
        return self.now >= self.scenario.hours.close_seconds

    def robot_or_none(self, robot_id: str) -> Optional[RobotStateSnapshot]:
        robot = self._robots.get(robot_id)
        return None if robot is None else self._snapshot_robot(robot)

    def zone_or_none(self, zone_id: str) -> Optional[ZoneStateSnapshot]:
        zone = self._zones.get(zone_id)
        return None if zone is None else self._snapshot_zone(zone)

    def station_or_none(self, station_id: str) -> Optional[StationStateSnapshot]:
        station = self._stations.get(station_id)
        return None if station is None else self._snapshot_station(station)

    def robot_snapshots(self) -> list[RobotStateSnapshot]:
        return [self._snapshot_robot(self._robots[r]) for r in sorted(self._robots)]

    def zone_snapshots(self) -> list[ZoneStateSnapshot]:
        return [self._snapshot_zone(self._zones[z]) for z in sorted(self._zones)]

    def station_snapshots(self) -> list[StationStateSnapshot]:
        return [self._snapshot_station(self._stations[s]) for s in sorted(self._stations)]

    def zone_commitments(self, zone_id: str, exclude_robot: str = "") -> int:
        """Robots currently assigned/heading to a zone (occupancy safety cap)."""
        return sum(
            1
            for rid in sorted(self._robots)
            if rid != exclude_robot and self._robots[rid].assigned_zone == zone_id
        )

    def station_accepts(self, station_id: str) -> bool:
        """A station accepts a robot if it is open and has either a free dock
        slot or room in the waiting queue (max_queue_length caps waiters)."""
        station = self._stations[station_id]
        if not station.is_open:
            return False
        if station.resource.count < station.resource.capacity:
            return True
        return len(station.resource.queue) < station.cfg.max_queue_length

    def best_open_station(self) -> Optional[str]:
        """Deterministic best station: shortest queue, then id order."""
        candidates = [
            (len(self._stations[s].resource.queue), s)
            for s in sorted(self._stations)
            if self.station_accepts(s)
        ]
        if not candidates:
            return None
        return min(candidates)[1]

    def charger_queue_length(self) -> int:
        return len(self._charger.queue)

    def staff_summary(self) -> tuple[int, int, int]:
        """(capacity, busy, queued) view of the human staff pool. Pure read, no RNG."""
        return (
            self.scenario.human_ops.staff_count,
            self._human_staff.count,
            len(self._human_staff.queue),
        )

    def washer_wip(self) -> int:
        return self.ledger.count(ledger_mod.WASHER)

    def dispenser_count(self) -> int:
        return self.ledger.count(ledger_mod.DISPENSER)

    def sensed_dispenser_count(self) -> float:
        """Delayed, noisy inventory reading (the only view the agent gets)."""
        cutoff = self.now - self.scenario.sensors.inventory_delay_s
        reading = None
        for t, value in reversed(self._inventory_readings):
            if t <= cutoff:
                reading = value
                break
        if reading is None:
            # Nothing old enough yet (start of day): fall back to the oldest
            # retained *sensed* reading — never leak the true count.
            reading = (
                self._inventory_readings[0][1]
                if self._inventory_readings
                else 0.0
            )
        return reading

    def sensed_zone_counts(self) -> dict[str, float]:
        sd = self.scenario.sensors.zone_count_noise_rel_sd
        out: dict[str, float] = {}
        for zone_id in sorted(self._zones):
            true = float(self.ledger.count(ledger_mod.zone_loc(zone_id)))
            noise = self._rng_sensors.normal(0.0, sd) if sd > 0 else 0.0
            out[zone_id] = max(0.0, true * (1.0 + noise))
        return out

    def sensed_battery_frac(self, robot_id: str) -> float:
        sd = self.scenario.sensors.battery_noise_abs_sd
        true = self._robots[robot_id].battery_frac
        noise = self._rng_sensors.normal(0.0, sd) if sd > 0 else 0.0
        return float(np.clip(true + noise, 0.0, 1.0))

    def forecast_window(self) -> list[float]:
        """Forecast rates (balls/min) for the next H buckets from now."""
        d = self.scenario.demand
        h = self.scenario.hours
        bucket_min = d.forecast_bucket_minutes
        n_out = max(1, d.forecast_horizon_minutes // bucket_min)
        idx = int(max(0.0, self.minute_of_day - h.open_minute) // bucket_min)
        window = self._forecast_buckets[idx : idx + n_out]
        return window + [0.0] * (n_out - len(window))

    def zone_position(self, zone_id: str) -> Point2D:
        return self._zones[zone_id].cfg.position

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def _snapshot_robot(self, robot: _Robot) -> RobotStateSnapshot:
        return RobotStateSnapshot(
            robot_id=robot.robot_id,
            activity=robot.activity,
            health=robot.health,
            battery_frac=robot.battery_frac,
            payload_balls=robot.payload_balls,
            payload_capacity_balls=robot.payload_capacity,
            location=robot.location_label,
            destination=robot.destination_label,
            assigned_zone=robot.assigned_zone,
            estop_latched=robot.estop_latched,
            awaiting_human=robot.awaiting_human,
        )

    def _snapshot_zone(self, zone: _Zone) -> ZoneStateSnapshot:
        present = sum(
            1
            for rid in sorted(self._robots)
            if self._robots[rid].location_label == ledger_mod.zone_loc(zone.zone_id)
        )
        return ZoneStateSnapshot(
            zone_id=zone.zone_id,
            balls=self.ledger.count(ledger_mod.zone_loc(zone.zone_id)),
            is_open=zone.is_open,
            robots_present=present,
        )

    def _snapshot_station(self, station: _Station) -> StationStateSnapshot:
        return StationStateSnapshot(
            station_id=station.station_id,
            is_open=station.is_open,
            docked=station.resource.count,
            queue_length=len(station.resource.queue),
            buffer_balls=self.ledger.count(ledger_mod.station_loc(station.station_id)),
            buffer_capacity_balls=station.cfg.buffer_capacity_balls,
        )

    def state_summary(self) -> dict:
        return {
            "t_s": self.now,
            "dispenser": self.dispenser_count(),
            "washer_wip": self.washer_wip(),
            "robots": [r.to_dict() for r in self.robot_snapshots()],
            "zones": [z.to_dict() for z in self.zone_snapshots()],
            "stations": [s.to_dict() for s in self.station_snapshots()],
            "charger_queue": self.charger_queue_length(),
            "ledger": self.ledger.counts(),
        }

    # ------------------------------------------------------------------
    # Control interface
    # ------------------------------------------------------------------

    def apply_directive(self, directive: Directive) -> ShieldDecision:
        """Validate through the SafetyShield and, if allowed, execute.

        This is the ONLY entry point for fleet control, and it always
        re-validates — the shield cannot be bypassed by calling the
        simulation directly.
        """
        decision = self.shield.check(directive)
        if not decision.allowed:
            self.metrics.unsafe_rejections += 1
            self.events.emit(
                self.now,
                EventKind.DIRECTIVE_REJECTED,
                directive=directive_to_dict(directive),
                reason=decision.reason,
            )
            return decision

        self.events.emit(
            self.now, EventKind.DIRECTIVE_APPLIED, directive=directive_to_dict(directive)
        )
        if isinstance(directive, Wait):
            return decision

        robot = self._robots[directive.robot_id]
        if isinstance(directive, AssignCollection):
            # Commit the zone immediately so occupancy caps see it before the
            # task process first runs.
            robot.assigned_zone = directive.zone_id
            self._start_task(robot, self._task_collect(robot, directive.zone_id))
        elif isinstance(directive, ReassignRobot):
            self.metrics.task_switches += 1
            self.events.emit(
                self.now,
                EventKind.TASK_SWITCHED,
                robot_id=robot.robot_id,
                from_zone=robot.assigned_zone,
                to_zone=directive.zone_id,
            )
            robot.assigned_zone = directive.zone_id
            self._start_task(robot, self._task_collect(robot, directive.zone_id))
        elif isinstance(directive, SendToHandoff):
            station_id = directive.station_id or self.best_open_station()
            self._start_task(robot, self._task_handoff(robot, station_id))
        elif isinstance(directive, SendToCharge):
            self._start_task(robot, self._task_charge(robot))
        elif isinstance(directive, PauseRobot):
            self._interrupt_task(robot, "pause")
            self._set_activity(robot, RobotActivity.PAUSED)
            robot.assigned_zone = None
            robot.destination_label = None
            self.events.emit(self.now, EventKind.ROBOT_PAUSED, robot_id=robot.robot_id)
        elif isinstance(directive, ResumeRobot):
            self._set_activity(robot, RobotActivity.IDLE)
            self.events.emit(self.now, EventKind.ROBOT_RESUMED, robot_id=robot.robot_id)
        elif isinstance(directive, RequestHumanAssistance):
            robot.awaiting_human = True
            self.metrics.human_interventions_requested += 1
            self.events.emit(
                self.now,
                EventKind.HUMAN_REQUESTED,
                robot_id=robot.robot_id,
                reason=directive.reason.value,
            )
            if robot.activity not in (
                RobotActivity.FAILED,
                RobotActivity.EMERGENCY_STOPPED,
            ):
                self._interrupt_task(robot, "human_hold")
                self._set_activity(robot, RobotActivity.AWAITING_HUMAN)
                robot.assigned_zone = None
                robot.destination_label = None
            self.env.process(self._human_assist_proc(robot, directive.reason))
        return decision

    def advance(self, dt_s: float) -> None:
        """Advance simulated time by ``dt_s`` seconds and re-check invariants."""
        target = self.now + dt_s
        self.env.run(until=target)
        # simpy stops slightly early if no event sits exactly at target;
        # run(until=t) guarantees now == t, so nothing more to do here.
        self._flush_battery_and_activity_accounting()
        if self.facility_closed and not self._facility_closed_emitted:
            self._facility_closed_emitted = True
            self.events.emit(self.now, EventKind.FACILITY_CLOSED)
        self.ledger.assert_conserved()
        self._assert_battery_bounds()

    def _assert_battery_bounds(self) -> None:
        for rid in sorted(self._robots):
            robot = self._robots[rid]
            cap = robot.cfg.battery_capacity_wh.value
            if not (-1e-6 <= robot.battery_wh <= cap + 1e-6):
                raise AssertionError(
                    f"battery out of bounds for {rid}: {robot.battery_wh} Wh (cap {cap})"
                )
            robot.battery_wh = float(np.clip(robot.battery_wh, 0.0, cap))

    # ------------------------------------------------------------------
    # Internal task machinery
    # ------------------------------------------------------------------

    def _start_task(self, robot: _Robot, task_gen: Generator) -> None:
        self._interrupt_task(robot, "redirect")
        proc: Optional[simpy.Process] = None

        def _wrapper() -> Generator:
            try:
                yield from task_gen
            except simpy.Interrupt as interrupt:
                self._on_task_interrupt(robot, str(interrupt.cause))
            finally:
                if robot.task_proc is proc:
                    robot.task_proc = None

        proc = self.env.process(_wrapper())
        robot.task_proc = proc

    def _interrupt_task(self, robot: _Robot, cause: str) -> None:
        proc = robot.task_proc
        if proc is None:
            return
        # A SimPy process may not interrupt itself; when a task calls into a
        # path that fails its own robot, the task returns right afterwards.
        if proc.is_alive and proc is not self.env.active_process:
            proc.interrupt(cause)
            robot.task_proc = None

    def _on_task_interrupt(self, robot: _Robot, cause: str) -> None:
        self._settle_partial_travel(robot)
        self._settle_partial_charge(robot)
        if cause in ("redirect", "pause", "human_hold"):
            return  # new state already set by apply_directive
        if cause == "failure":
            return  # state set by the failure process
        if cause == "estop":
            return  # state set by the e-stop path
        if cause in ("zone_closed", "station_outage"):
            robot.assigned_zone = None
            robot.destination_label = None
            self._set_activity(robot, RobotActivity.IDLE)
            return
        self._set_activity(robot, RobotActivity.IDLE)

    def _settle_partial_travel(self, robot: _Robot) -> None:
        if robot.travel is None:
            return
        start, dest, t0, duration, energy = robot.travel
        frac = 0.0 if duration <= 0 else float(np.clip((self.now - t0) / duration, 0.0, 1.0))
        robot.pos = Point2D(
            x_m=start.x_m + (dest.x_m - start.x_m) * frac,
            y_m=start.y_m + (dest.y_m - start.y_m) * frac,
        )
        self._drain(robot, energy * frac)
        elapsed = (self.now - t0) if duration > 0 else 0.0
        if robot.payload_balls == 0:
            self.metrics.empty_travel_s += elapsed
        else:
            self.metrics.loaded_travel_s += elapsed
        robot.travel = None
        robot.location_label = "transit"

    def _settle_partial_charge(self, robot: _Robot) -> None:
        if robot.charging_since is None:
            return
        rate_w = self.scenario.charger.charge_rate_w.value
        gained = rate_w * (self.now - robot.charging_since) / 3600.0
        cap = robot.cfg.battery_capacity_wh.value
        robot.battery_wh = min(cap, robot.battery_wh + gained)
        robot.charging_since = None

    def _set_activity(self, robot: _Robot, activity: RobotActivity) -> None:
        self._account_activity(robot)
        robot.activity = activity

    def _account_activity(self, robot: _Robot) -> None:
        """Accumulate time/idle-drain for the current activity up to now."""
        elapsed = self.now - robot.last_activity_change_s
        robot.last_activity_change_s = self.now
        if elapsed <= 0:
            return
        if robot.activity is RobotActivity.IDLE and self.facility_open:
            self.metrics.idle_robot_s += elapsed
        if robot.activity is RobotActivity.QUEUED_HANDOFF:
            self.metrics.handoff_queue_s += elapsed
        if robot.activity is RobotActivity.QUEUED_CHARGER:
            self.metrics.charger_queue_s += elapsed
        if robot.activity in (
            RobotActivity.TRAVELING,
            RobotActivity.COLLECTING,
            RobotActivity.UNLOADING,
            RobotActivity.CHARGING,
        ):
            self.metrics.active_robot_s += elapsed
        if robot.activity in _IDLE_DRAIN_ACTIVITIES:
            self._drain(robot, robot.cfg.idle_power_w.value * elapsed / 3600.0)

    def _prorate_inflight_travel(self, robot: _Robot) -> None:
        """Book the elapsed part of an in-flight travel leg and re-base it.

        Keeps battery, energy, and travel-second metrics current at every
        control-interval boundary, so (a) the SafetyShield never sees a stale
        battery for a mid-travel robot and (b) legs still in flight at
        episode end are not silently dropped from the metrics. The remainder
        of the leg is booked at completion or interrupt as before.
        """
        if robot.travel is None:
            return
        start, dest, t0, duration, energy = robot.travel
        elapsed = self.now - t0
        if duration <= 0 or elapsed <= 0:
            return
        frac = min(1.0, elapsed / duration)
        self._drain(robot, energy * frac)
        if robot.payload_balls == 0:
            self.metrics.empty_travel_s += elapsed
        else:
            self.metrics.loaded_travel_s += elapsed
        new_pos = Point2D(
            x_m=start.x_m + (dest.x_m - start.x_m) * frac,
            y_m=start.y_m + (dest.y_m - start.y_m) * frac,
        )
        robot.pos = new_pos
        robot.travel = (
            new_pos,
            dest,
            self.now,
            duration - elapsed,
            energy * (1.0 - frac),
        )

    def _flush_battery_and_activity_accounting(self) -> None:
        for rid in sorted(self._robots):
            robot = self._robots[rid]
            self._account_activity(robot)
            self._prorate_inflight_travel(robot)
            if robot.charging_since is not None:
                self._settle_partial_charge(robot)
                robot.charging_since = self.now
            self._check_battery_floor(robot)

    def _drain(self, robot: _Robot, energy_wh: float) -> None:
        if energy_wh <= 0:
            return
        drained = min(energy_wh, robot.battery_wh)
        robot.battery_wh -= drained
        self.metrics.energy_wh += drained

    def _check_battery_floor(self, robot: _Robot) -> None:
        floor = self.scenario.safety.hard_battery_floor_frac
        if robot.battery_frac > floor:
            return
        if robot.activity in (
            RobotActivity.FAILED,
            RobotActivity.EMERGENCY_STOPPED,
            RobotActivity.AWAITING_HUMAN,
            RobotActivity.CHARGING,
        ):
            return
        self._interrupt_task(robot, "failure")
        self._set_activity(robot, RobotActivity.FAILED)
        robot.health = RobotHealth.FAILED
        robot.assigned_zone = None
        robot.destination_label = None
        robot.failure_note = "battery depleted below hard floor"
        self.metrics.hard_failures += 1
        self.events.emit(
            self.now,
            EventKind.BATTERY_DEPLETED,
            robot_id=robot.robot_id,
            battery_frac=round(robot.battery_frac, 4),
        )

    def _fail_robot(self, robot: _Robot, note: str, human_required: bool = True) -> None:
        self._interrupt_task(robot, "failure")
        self._set_activity(robot, RobotActivity.FAILED)
        robot.health = RobotHealth.FAILED
        robot.assigned_zone = None
        robot.destination_label = None
        robot.failure_note = note
        self.metrics.hard_failures += 1
        self.events.emit(
            self.now,
            EventKind.ROBOT_FAILED,
            robot_id=robot.robot_id,
            note=note,
            human_required=human_required,
        )

    def _latch_estop(self, robot: _Robot, note: str) -> None:
        """Latched emergency stop — Phase 0 concept: no software reset.

        Only the human-intervention path clears it. The learning policy has
        no action that can trigger OR clear an e-stop.
        """
        self._interrupt_task(robot, "estop")
        self._set_activity(robot, RobotActivity.EMERGENCY_STOPPED)
        robot.estop_latched = True
        robot.assigned_zone = None
        robot.destination_label = None
        robot.failure_note = note
        self.metrics.estops += 1
        self.events.emit(
            self.now, EventKind.EMERGENCY_STOP, robot_id=robot.robot_id, note=note
        )

    # ------------------------------------------------------------------
    # Robot task generators
    # ------------------------------------------------------------------

    def _sample_skill(self, request: SkillRequest):
        return self.skill_model.sample(request, self._rng_skills)

    def _skill_request(self, robot: _Robot, skill: SkillType, **kwargs) -> SkillRequest:
        return SkillRequest(
            skill=skill,
            robot_id=robot.robot_id,
            robot_health=robot.health,
            battery_frac=robot.battery_frac,
            payload_balls=robot.payload_balls,
            **kwargs,
        )

    def _do_travel(self, robot: _Robot, dest: Point2D, dest_label: str) -> Generator:
        """Travel leg; yields True into the caller via StopIteration value."""
        origin = robot.pos  # kept for failure positioning: pro-rating
        # rebases robot.travel, so the tuple's start drifts from the leg origin
        distance = robot.pos.distance_to(dest)
        outcome = self._sample_skill(
            self._skill_request(
                robot,
                SkillType.TRAVEL,
                distance_m=distance,
                speed_multiplier=self.scenario.skills.wet_ground_speed_multiplier,
            )
        )
        self._set_activity(robot, RobotActivity.TRAVELING)
        robot.destination_label = dest_label
        robot.location_label = "transit"
        robot.travel = (robot.pos, dest, self.now, outcome.duration_s, outcome.energy_wh)
        self.events.emit(
            self.now,
            EventKind.TRAVEL_STARTED,
            robot_id=robot.robot_id,
            dest=dest_label,
            distance_m=round(distance, 2),
            duration_s=round(outcome.duration_s, 2),
        )
        yield self.env.timeout(outcome.duration_s)
        _, _, _, duration, energy = robot.travel
        robot.travel = None
        self._drain(robot, energy)
        if robot.payload_balls == 0:
            self.metrics.empty_travel_s += duration
        else:
            self.metrics.loaded_travel_s += duration
        if not outcome.success:
            # Stranded partway: the completed fraction is relative to the
            # whole leg, so interpolate from the original origin.
            frac = float(outcome.data.get("completed_fraction", 1.0))
            robot.pos = Point2D(
                x_m=origin.x_m + (dest.x_m - origin.x_m) * frac,
                y_m=origin.y_m + (dest.y_m - origin.y_m) * frac,
            )
            robot.location_label = "transit"
            robot.destination_label = None
            self._fail_robot(robot, f"travel failure en route to {dest_label}")
            return False
        robot.pos = dest
        robot.location_label = dest_label
        robot.destination_label = None
        self.events.emit(
            self.now, EventKind.TRAVEL_COMPLETED, robot_id=robot.robot_id, dest=dest_label
        )
        self._check_battery_floor(robot)
        return robot.activity is not RobotActivity.FAILED

    def _task_collect(self, robot: _Robot, zone_id: str) -> Generator:
        robot.assigned_zone = zone_id
        zone = self._zones[zone_id]
        ok = yield from self._do_travel(
            robot, zone.cfg.position, ledger_mod.zone_loc(zone_id)
        )
        if not ok or robot.activity is RobotActivity.FAILED:
            return
        self._set_activity(robot, RobotActivity.COLLECTING)
        zone_key = ledger_mod.zone_loc(zone_id)
        robot_key = ledger_mod.robot_loc(robot.robot_id)
        while (
            zone.is_open
            and self.ledger.count(zone_key) > 0
            and robot.payload_balls < robot.payload_capacity
            and robot.battery_frac > self.scenario.safety.hard_battery_floor_frac
        ):
            n = min(
                self.scenario.skills.collect_cycle_balls,
                self.ledger.count(zone_key),
                robot.payload_capacity - robot.payload_balls,
            )
            outcome = self._sample_skill(
                self._skill_request(
                    robot, SkillType.COLLECT_CYCLE, n_balls=n, zone_id=zone_id
                )
            )
            yield self.env.timeout(outcome.duration_s)
            self._drain(robot, outcome.energy_wh)
            if not outcome.success:
                self._fail_robot(robot, f"collection failure in {zone_id}")
                return
            moved = self.ledger.move(zone_key, robot_key, n)
            robot.payload_balls += moved
            self.metrics.balls_collected += moved
            self.events.emit(
                self.now,
                EventKind.COLLECT_CYCLE,
                robot_id=robot.robot_id,
                zone_id=zone_id,
                balls=moved,
                payload=robot.payload_balls,
            )
            if robot.health is RobotHealth.FAILED:
                return
        robot.assigned_zone = None
        self._set_activity(robot, RobotActivity.IDLE)
        self.events.emit(
            self.now,
            EventKind.COLLECTION_DONE,
            robot_id=robot.robot_id,
            zone_id=zone_id,
            payload=robot.payload_balls,
        )

    def _task_handoff(self, robot: _Robot, station_id: Optional[str]) -> Generator:
        if station_id is None or station_id not in self._stations:
            self._set_activity(robot, RobotActivity.IDLE)
            return
        station = self._stations[station_id]
        robot.assigned_zone = None
        ok = yield from self._do_travel(
            robot, station.cfg.position, ledger_mod.station_loc(station_id)
        )
        if not ok or robot.activity is RobotActivity.FAILED:
            return
        if not self.station_accepts(station_id):
            # Station closed or filled up while we were en route: the
            # capacity cap is hard, so the robot backs off instead of piling
            # onto the queue.
            self._set_activity(robot, RobotActivity.IDLE)
            return
        self._set_activity(robot, RobotActivity.QUEUED_HANDOFF)
        self.events.emit(
            self.now,
            EventKind.HANDOFF_ENQUEUED,
            robot_id=robot.robot_id,
            station_id=station_id,
            queue_length=len(station.resource.queue),
        )
        with station.resource.request() as slot:
            yield slot
            # Docking/unloading is active work, not queue waiting: switch off
            # QUEUED_HANDOFF here so queue-time metrics cover only the wait
            # and idle drain does not double-count dock energy.
            self._set_activity(robot, RobotActivity.UNLOADING)
            attempts = 0
            while True:
                attempts += 1
                self.metrics.dock_attempts += 1
                self.events.emit(
                    self.now,
                    EventKind.DOCK_ATTEMPT,
                    robot_id=robot.robot_id,
                    station_id=station_id,
                    attempt=attempts,
                )
                outcome = self._sample_skill(
                    self._skill_request(robot, SkillType.DOCK, station_id=station_id)
                )
                yield self.env.timeout(outcome.duration_s)
                self._drain(robot, outcome.energy_wh)
                if outcome.success:
                    break
                self.metrics.dock_failures += 1
                self.events.emit(
                    self.now,
                    EventKind.DOCK_FAILED,
                    robot_id=robot.robot_id,
                    station_id=station_id,
                    attempt=attempts,
                )
                if (
                    self.scenario.safety.dock_incident_estop_prob > 0
                    and self._rng_skills.random()
                    < self.scenario.safety.dock_incident_estop_prob
                ):
                    self._latch_estop(robot, f"docking incident at {station_id}")
                    return
                if attempts > self.scenario.skills.max_dock_retries:
                    # Mirrors Phase 0 RECOVERY_EXHAUSTED: give up, need human.
                    self._fail_robot(
                        robot, f"docking retries exhausted at {station_id}"
                    )
                    return
            robot_key = ledger_mod.robot_loc(robot.robot_id)
            station_key = ledger_mod.station_loc(station_id)
            while robot.payload_balls > 0:
                free = station.cfg.buffer_capacity_balls - self.ledger.count(station_key)
                if free <= 0:
                    # Buffer full: blocked on washer throughput — hold the
                    # dock slot (congestion is a real consequence).
                    yield self.env.timeout(15.0)
                    continue
                n = min(robot.payload_balls, free)
                outcome = self._sample_skill(
                    self._skill_request(
                        robot, SkillType.UNLOAD, n_balls=n, station_id=station_id
                    )
                )
                yield self.env.timeout(outcome.duration_s)
                self._drain(robot, outcome.energy_wh)
                # Re-check free space after the yield: another robot may have
                # unloaded into this buffer meanwhile, and the capacity cap
                # is a hard constraint. Any remainder loops back around.
                free_now = station.cfg.buffer_capacity_balls - self.ledger.count(
                    station_key
                )
                moved = self.ledger.move(robot_key, station_key, min(n, max(0, free_now)))
                robot.payload_balls -= moved
                if moved > 0:
                    self.events.emit(
                        self.now,
                        EventKind.UNLOADED,
                        robot_id=robot.robot_id,
                        station_id=station_id,
                        balls=moved,
                    )
        self._set_activity(robot, RobotActivity.IDLE)
        self._check_battery_floor(robot)

    def _task_charge(self, robot: _Robot) -> Generator:
        robot.assigned_zone = None
        ok = yield from self._do_travel(robot, self.scenario.charger.position, "charger")
        if not ok or robot.activity is RobotActivity.FAILED:
            return
        self._set_activity(robot, RobotActivity.QUEUED_CHARGER)
        with self._charger.request() as slot:
            yield slot
            # Connecting is active work, not queue waiting: leave
            # QUEUED_CHARGER (and its queue-time metric) at slot acquisition.
            self._set_activity(robot, RobotActivity.CHARGING)
            outcome = self._sample_skill(
                self._skill_request(robot, SkillType.CHARGE_CONNECT)
            )
            yield self.env.timeout(outcome.duration_s)
            cap = robot.cfg.battery_capacity_wh.value
            target_wh = self.scenario.charger.charge_target_frac * cap
            rate_w = self.scenario.charger.charge_rate_w.value
            deficit_wh = max(0.0, target_wh - robot.battery_wh)
            duration_s = deficit_wh / rate_w * 3600.0
            robot.charging_since = self.now
            self.events.emit(
                self.now,
                EventKind.CHARGE_STARTED,
                robot_id=robot.robot_id,
                battery_frac=round(robot.battery_frac, 4),
                duration_s=round(duration_s, 1),
            )
            yield self.env.timeout(duration_s)
            robot.charging_since = None
            robot.battery_wh = target_wh
            self.events.emit(
                self.now,
                EventKind.CHARGE_DONE,
                robot_id=robot.robot_id,
                battery_frac=round(robot.battery_frac, 4),
            )
        self._set_activity(robot, RobotActivity.IDLE)

    # ------------------------------------------------------------------
    # Background processes
    # ------------------------------------------------------------------

    def _demand_proc(self) -> Generator:
        d = self.scenario.demand
        h = self.scenario.hours
        while self.now < h.close_seconds:
            minute = self.minute_of_day
            rate = d.true_rate_at(minute)
            self.metrics.open_minutes_elapsed += 1.0
            open_zones = [z for z in sorted(self._zones) if self._zones[z].is_open]
            if rate > 0 and open_zones:
                # With every zone closed, customers cannot hit balls at all:
                # that is not dispenser stockout, so no demand is drawn.
                requested = int(self._rng_demand.poisson(rate))
            else:
                requested = 0
            if requested > 0:
                available = self.dispenser_count()
                served = min(requested, available)
                self.metrics.demand_balls_total += requested
                if served > 0:
                    weights = np.array(
                        [self._zones[z].cfg.landing_weight for z in open_zones],
                        dtype=float,
                    )
                    weights = weights / weights.sum()
                    landed = self._rng_demand.multinomial(served, weights)
                    for zone_id, n in zip(open_zones, landed):
                        if n > 0:
                            self.ledger.move(
                                ledger_mod.DISPENSER, ledger_mod.zone_loc(zone_id), int(n)
                            )
                    self.metrics.demand_balls_served += served
                    self.events.emit(
                        self.now,
                        EventKind.DEMAND_SERVED,
                        requested=requested,
                        served=served,
                        by_zone={z: int(n) for z, n in zip(open_zones, landed) if n > 0},
                    )
                if served < requested:
                    self.metrics.stockout_minutes += 1.0
                    self.events.emit(
                        self.now,
                        EventKind.STOCKOUT,
                        requested=requested,
                        served=served,
                        dispenser=self.dispenser_count(),
                    )
            yield self.env.timeout(60.0)

    def _washer_proc(self) -> Generator:
        w = self.scenario.washer
        rate_per_s = max(w.balls_per_minute.value, 0.01) / 60.0
        while True:
            buffered = [
                (s, self.ledger.count(ledger_mod.station_loc(s)))
                for s in sorted(self._stations)
            ]
            total = sum(n for _, n in buffered)
            if total <= 0:
                yield self.env.timeout(30.0)
                continue
            batch = min(w.batch_size_balls, total)
            remaining = batch
            pulled: dict[str, int] = {}
            for station_id, available in buffered:
                if remaining <= 0:
                    break
                take = min(available, remaining)
                if take > 0:
                    self.ledger.move(
                        ledger_mod.station_loc(station_id), ledger_mod.WASHER, take
                    )
                    pulled[station_id] = take
                    remaining -= take
            self.events.emit(
                self.now, EventKind.WASH_BATCH_STARTED, balls=batch, from_stations=pulled
            )
            yield self.env.timeout(batch / rate_per_s)
            self.ledger.move(ledger_mod.WASHER, ledger_mod.DISPENSER, batch)
            self.metrics.balls_processed += batch
            self.events.emit(self.now, EventKind.WASH_BATCH_DONE, balls=batch)

    def _sensor_proc(self) -> Generator:
        sensors = self.scenario.sensors
        while True:
            true = float(self.dispenser_count())
            sd = sensors.inventory_noise_rel_sd
            noise = self._rng_sensors.normal(0.0, sd) if sd > 0 else 0.0
            self._inventory_readings.append((self.now, max(0.0, true * (1.0 + noise))))
            if len(self._inventory_readings) > 500:
                del self._inventory_readings[:250]
            yield self.env.timeout(sensors.update_period_s)

    def _failure_proc(self, robot: _Robot) -> Generator:
        mtbf_s = robot.cfg.mean_time_between_failures_h.value * 3600.0
        while True:
            yield self.env.timeout(float(self._rng_failures.exponential(mtbf_s)))
            if robot.activity in (
                RobotActivity.FAILED,
                RobotActivity.EMERGENCY_STOPPED,
                RobotActivity.AWAITING_HUMAN,
            ):
                continue
            if self._rng_failures.random() < robot.cfg.degraded_fraction:
                if robot.health is RobotHealth.OK:
                    robot.health = RobotHealth.DEGRADED
                    self.events.emit(
                        self.now, EventKind.ROBOT_DEGRADED, robot_id=robot.robot_id
                    )
            else:
                self._fail_robot(robot, "spontaneous hardware failure")

    def _zone_closure_proc(
        self, zone: _Zone, start_minute: int, end_minute: int
    ) -> Generator:
        # A window already active at episode start closes the zone
        # synchronously (before any demand draw); otherwise wait for it.
        # self.now counts seconds since *midnight* (the env starts at
        # open_seconds), so window minutes-of-day convert with a bare *60.
        delay = max(0.0, start_minute * 60.0 - self.now)
        if delay > 0:
            yield self.env.timeout(delay)
        zone.is_open = False
        self.events.emit(self.now, EventKind.ZONE_CLOSED, zone_id=zone.zone_id)
        for rid in sorted(self._robots):
            robot = self._robots[rid]
            if robot.assigned_zone == zone.zone_id:
                self._interrupt_task(robot, "zone_closed")
        yield self.env.timeout(max(0.0, end_minute * 60.0 - self.now))
        zone.is_open = True
        self.events.emit(self.now, EventKind.ZONE_REOPENED, zone_id=zone.zone_id)

    def _station_outage_proc(
        self, station: _Station, start_minute: int, end_minute: int
    ) -> Generator:
        # Same clock convention as _zone_closure_proc: minutes-of-day vs a
        # seconds-since-midnight self.now.
        delay = max(0.0, start_minute * 60.0 - self.now)
        if delay > 0:
            yield self.env.timeout(delay)
        station.is_open = False
        self.events.emit(
            self.now, EventKind.STATION_OUTAGE, station_id=station.station_id
        )
        for rid in sorted(self._robots):
            robot = self._robots[rid]
            if robot.activity in (
                RobotActivity.QUEUED_HANDOFF,
                RobotActivity.UNLOADING,
            ) and robot.location_label == ledger_mod.station_loc(station.station_id):
                self._interrupt_task(robot, "station_outage")
                self._set_activity(robot, RobotActivity.IDLE)
        yield self.env.timeout(max(0.0, end_minute * 60.0 - self.now))
        station.is_open = True
        self.events.emit(
            self.now, EventKind.STATION_RESTORED, station_id=station.station_id
        )

    def _human_assist_proc(self, robot: _Robot, reason: HumanAssistReason) -> Generator:
        h = self.scenario.human_ops
        with self._human_staff.request() as staff:
            yield staff
            yield self.env.timeout(h.response_delay_s.value)
            self.events.emit(
                self.now,
                EventKind.HUMAN_ARRIVED,
                robot_id=robot.robot_id,
                reason=reason.value,
            )
            yield self.env.timeout(h.fix_duration_s.value)
        was_incident = (
            robot.health is RobotHealth.FAILED
            or robot.estop_latched
            or robot.activity is RobotActivity.FAILED
        )
        robot.health = RobotHealth.OK
        robot.estop_latched = False
        robot.awaiting_human = False
        robot.failure_note = ""
        cap = robot.cfg.battery_capacity_wh.value
        reserve_wh = self.scenario.safety.min_battery_reserve_frac * cap
        battery_restored = robot.battery_wh < reserve_wh
        if battery_restored:
            # Field battery swap / manual top-up to the minimum reserve —
            # without this a depleted robot would re-fail at the very next
            # accounting flush and livelock on human requests forever.
            robot.battery_wh = reserve_wh
        self._interrupt_task(robot, "redirect")
        self._set_activity(robot, RobotActivity.IDLE)
        robot.assigned_zone = None
        robot.destination_label = None
        self.metrics.human_interventions_completed += 1
        if was_incident:
            self.metrics.safe_recoveries += 1
        self.events.emit(
            self.now,
            EventKind.HUMAN_DONE,
            robot_id=robot.robot_id,
            reason=reason.value,
            recovered=was_incident,
            battery_restored=battery_restored,
        )
        self.events.emit(self.now, EventKind.ROBOT_RECOVERED, robot_id=robot.robot_id)
