"""Assemble a FacilityState from an ObservationFrame.

The deployment path: real adapters will emit the same Observation
contract, and this assembler — unchanged — will keep producing the same
FacilityState the decision and memory layers consume. In simulation,
``build_facility_state(sim)`` remains canonical for the live loop; this
module is the rehearsal, and the parity test
(tests/telemetry/test_assemble.py) is what prevents silent drift.

Designated sim-side module: it instantiates the per-entity snapshot
classes, whose package import pulls the simulator. FacilityState
semantics are untouched — this file only *populates* the existing
contract.

The assembler is total: missing channels are backfilled from the
``previous`` state (or conservative defaults) so a FacilityState always
exists, and the missingness is surfaced explicitly in the
AssemblyReport — the system always knows when it does not have reliable
information.
"""

from __future__ import annotations

from dataclasses import dataclass

from nxt_range_ops.core.entities import (
    INOPERATIVE_ACTIVITIES,
    RobotActivity,
    RobotHealth,
    RobotStateSnapshot,
    StationStateSnapshot,
    ZoneStateSnapshot,
)

from nxt_facility.state import (
    BallFlow,
    ChargingState,
    DemandState,
    EnvironmentState,
    FacilityMeta,
    FacilityState,
    FleetSummary,
    StaffState,
    WasherState,
)

from .observations import (
    Observation,
    ObservationFrame,
    ObservationStatus,
    SiteConfig,
    UpstreamInputs,
)

# Provenance-grade thresholds (source: placeholder)
_GRADE_HIGH = "high"
_GRADE_MEDIUM = "medium"
_GRADE_LOW = "low"


@dataclass(frozen=True)
class AssemblyReport:
    """What the assembled state is worth: the uncertainty surface."""

    missing_channels: tuple[str, ...]
    stale_channels: tuple[str, ...]
    consistency_issues: tuple[str, ...]
    overall_confidence: float
    provenance_grade: str

    def to_dict(self) -> dict:
        return {
            "missing_channels": list(self.missing_channels),
            "stale_channels": list(self.stale_channels),
            "consistency_issues": list(self.consistency_issues),
            "overall_confidence": round(self.overall_confidence, 6),
            "provenance_grade": self.provenance_grade,
        }


class _ChannelView:
    """Bookkeeping wrapper: fetch channel values, track gaps and staleness."""

    def __init__(self, frame: ObservationFrame, previous: FacilityState | None):
        self._by_channel = frame.by_channel()
        self._previous = previous
        self.missing: list[str] = []
        self.stale: list[str] = []
        self.issues: list[str] = []
        self.confidences: list[float] = []

    def get(self, channel: str, fallback, *, previous_value=None):
        observation: Observation | None = self._by_channel.get(channel)
        if observation is None or observation.status is ObservationStatus.MISSING:
            self.missing.append(channel)
            # a gap is a zero-confidence input by definition
            self.confidences.append(0.0)
            if previous_value is not None:
                return previous_value
            return fallback
        if observation.status is ObservationStatus.STALE:
            self.stale.append(channel)
        self.confidences.append(observation.confidence)
        return observation.value

    def count(self, channel: str, *, previous_value=None) -> int:
        raw = self.get(channel, 0, previous_value=previous_value)
        value = int(round(float(raw)))
        if value < 0:
            self.issues.append(f"{channel}: negative reading {raw!r} clamped to 0")
            return 0
        return value

    def frac(self, channel: str, *, previous_value=None) -> float:
        raw = float(self.get(channel, 0.0, previous_value=previous_value))
        if raw < 0.0 or raw > 1.0:
            self.issues.append(f"{channel}: fraction {raw!r} clamped to [0, 1]")
        return min(1.0, max(0.0, raw))


def assemble_from_observations(
    frame: ObservationFrame,
    site: SiteConfig,
    upstream: UpstreamInputs,
    *,
    previous: FacilityState | None = None,
) -> tuple[FacilityState, AssemblyReport]:
    view = _ChannelView(frame, previous)
    prev = previous

    # -- robots ---------------------------------------------------------
    payload_capacity = dict(site.robot_payload_capacity)
    robots = []
    for rid in site.robot_ids:
        prev_robot = prev.robot_or_none(rid) if prev else None

        def rget(field_name: str, fallback, caster=lambda v: v):
            previous_value = (
                getattr(prev_robot, field_name) if prev_robot is not None else None
            )
            raw = view.get(
                f"robot.{rid}.{field_name}", fallback, previous_value=previous_value
            )
            return caster(raw)

        activity_raw = rget("activity", "idle", str)
        health_raw = rget("health", "ok", str)
        destination = rget("destination", "", str)
        assigned = rget("assigned_zone", "", str)
        robots.append(
            RobotStateSnapshot(
                robot_id=rid,
                activity=RobotActivity(activity_raw or "idle"),
                health=RobotHealth(health_raw or "ok"),
                battery_frac=view.frac(
                    f"robot.{rid}.battery_frac",
                    previous_value=(
                        prev_robot.battery_frac if prev_robot is not None else None
                    ),
                ),
                payload_balls=view.count(
                    f"robot.{rid}.payload_balls",
                    previous_value=(
                        prev_robot.payload_balls if prev_robot is not None else None
                    ),
                ),
                payload_capacity_balls=payload_capacity[rid],
                location=rget("location", "charger", str) or "charger",
                destination=(destination or None),
                assigned_zone=(assigned or None),
                estop_latched=bool(rget("estop_latched", False)),
                awaiting_human=bool(rget("awaiting_human", False)),
            )
        )
    robots = tuple(robots)

    # -- zones (robots_present derived from robot locations) -------------
    prev_zone_balls = dict(prev.ball_flow.on_field) if prev else {}
    zones = []
    for zid in site.zone_ids:
        balls = view.count(
            f"scan.zone.{zid}.balls", previous_value=prev_zone_balls.get(zid)
        )
        is_open = bool(view.get(f"zone.{zid}.is_open", True))
        present = sum(1 for r in robots if r.location == f"zone:{zid}")
        zones.append(
            ZoneStateSnapshot(
                zone_id=zid, balls=balls, is_open=is_open, robots_present=present
            )
        )
    zones = tuple(zones)

    # -- stations ---------------------------------------------------------
    buffer_capacity = dict(site.station_buffer_capacity)
    prev_buffers = dict(prev.ball_flow.dirty_buffered) if prev else {}
    stations = []
    for sid in site.station_ids:
        stations.append(
            StationStateSnapshot(
                station_id=sid,
                is_open=bool(view.get(f"station.{sid}.is_open", True)),
                docked=view.count(f"station.{sid}.docked"),
                queue_length=view.count(f"station.{sid}.queue_length"),
                buffer_balls=view.count(
                    f"inventory.station.{sid}.buffer_balls",
                    previous_value=prev_buffers.get(sid),
                ),
                buffer_capacity_balls=buffer_capacity[sid],
            )
        )
    stations = tuple(stations)

    # -- ball flow --------------------------------------------------------
    clean_available = view.count(
        "inventory.dispenser.count",
        previous_value=(prev.ball_flow.clean_available if prev else None),
    )
    clean_sensed = float(
        view.get(
            "inventory.dispenser.sensed",
            float(clean_available),
            previous_value=(prev.ball_flow.clean_sensed if prev else None),
        )
    )
    in_wash = view.count(
        "wash.washer.wip", previous_value=(prev.ball_flow.in_wash if prev else None)
    )
    ball_flow = BallFlow(
        total_balls=site.total_balls,
        clean_available=clean_available,
        clean_sensed=clean_sensed,
        in_wash=in_wash,
        dirty_buffered=tuple((s.station_id, s.buffer_balls) for s in stations),
        on_field=tuple((z.zone_id, z.balls) for z in zones),
        in_transit=tuple((r.robot_id, r.payload_balls) for r in robots),
    )
    if not ball_flow.conserved:
        accounted = (
            ball_flow.clean_available
            + ball_flow.in_wash
            + ball_flow.dirty_buffered_total
            + ball_flow.on_field_total
            + ball_flow.in_transit_total
        )
        view.issues.append(
            f"ball flow not conserved: accounted {accounted} != site total "
            f"{site.total_balls} (sensor disagreement or loss)"
        )

    # -- aggregates (same derivations build_facility_state uses) ----------
    inoperative = sum(1 for r in robots if r.activity in INOPERATIVE_ACTIVITIES)
    charging = sum(1 for r in robots if r.activity is RobotActivity.CHARGING)
    minute_of_day = frame.t_s / 60.0
    facility_open = (
        site.open_minute * 60.0 <= frame.t_s < site.close_minute * 60.0
    )

    state = FacilityState(
        meta=FacilityMeta(
            t_s=frame.t_s,
            minute_of_day=minute_of_day,
            facility_open=facility_open,
            scenario_name=site.scenario_name,
            seed=site.seed,
        ),
        ball_flow=ball_flow,
        washer=WasherState(
            throughput_balls_per_minute=site.washer_throughput_bpm,
            batch_size_balls=site.washer_batch_size,
            wip=ball_flow.in_wash,
        ),
        demand=DemandState(
            forecast_balls_per_minute=upstream.forecast_balls_per_minute,
            forecast_bucket_minutes=site.forecast_bucket_minutes,
            minutes_to_close=max(0.0, site.close_minute - minute_of_day),
            demand_balls_total=upstream.demand_balls_total,
            demand_balls_served=upstream.demand_balls_served,
            stockout_minutes=upstream.stockout_minutes,
            service_availability=upstream.service_availability,
        ),
        fleet=FleetSummary(
            total=len(robots),
            operable=len(robots) - inoperative,
            inoperative=inoperative,
            charging=charging,
            awaiting_human=sum(1 for r in robots if r.awaiting_human),
        ),
        charging=ChargingState(
            slots=site.charger_slots,
            in_use=charging,
            queue_length=view.count("charger.site.queue_length"),
        ),
        staff=StaffState(
            capacity=site.staff_capacity,
            busy=view.count("staff.site.busy"),
            queued_requests=view.count("staff.site.queued"),
        ),
        environment=EnvironmentState(
            wet_ground_speed_multiplier=site.wet_ground_speed_multiplier,
            zones_open=sum(1 for z in zones if z.is_open),
            zones_total=len(zones),
            stations_open=sum(1 for s in stations if s.is_open),
            stations_total=len(stations),
        ),
        robots=robots,
        zones=zones,
        stations=stations,
    )

    missing = tuple(sorted(set(view.missing)))
    stale = tuple(sorted(set(view.stale)))
    issues = tuple(view.issues)
    overall = min(view.confidences) if view.confidences else 0.0
    if not missing and not stale and not issues and overall >= 1.0:
        grade = _GRADE_HIGH
    elif not missing:
        grade = _GRADE_MEDIUM
    else:
        grade = _GRADE_MEDIUM if len(missing) < 3 else _GRADE_LOW
    report = AssemblyReport(
        missing_channels=missing,
        stale_channels=stale,
        consistency_issues=issues,
        overall_confidence=overall,
        provenance_grade=grade,
    )
    return state, report
