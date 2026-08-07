"""Deterministic decision rules over FacilityState.

Architecture position: FacilityState -> **Decision Rules** -> Recommendation
-> Human/Agent Interface -> future execution layer. This module emits
*desired operational outcomes*, never execution commands: no Directive
objects, no simulator access, no RNG, no lookahead. The SafetyShield and
directive vocabulary remain the sole control path, one layer below.

Rules are still written to be *achievable*: each pre-filters the same
conditions the SafetyShield enforces (battery reserve floor for new
assignments, no duplicate human-assistance requests, no charge advice for
robots already charging, per-zone crowding), so advice is actionable the
moment it is given.

Robot activities/health are matched as string literals because importing
``nxt_range_ops.core.entities`` at runtime would pull the simulator into
this contract module (see tests/facility for the pin tests that keep the
literals honest).

PLACEHOLDER PROVENANCE: every threshold below is an engineering
placeholder (source: placeholder). They gate demo recommendations only and
must never be presented as validated operating policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .analysis import (
    CRITICAL_STOCKOUT_HORIZON_MIN,
    STRAINED_STOCKOUT_HORIZON_MIN,
    StockoutEstimate,
    estimate_stockout,
)
from .state import FacilityState

# --- Placeholder thresholds (source: placeholder) --------------------------

MIN_BATTERY_RESERVE_FRAC = 0.15  # mirrors SafetyConfig default
BATTERY_MARGIN_FRAC = 0.05       # warn before the shield's reserve floor bites
ZONE_WORTH_COLLECTING_BALLS = 50
PAYLOAD_FULL_FRAC = 0.8
BUFFER_NEAR_FULL_FRAC = 0.9
MAX_ROBOTS_PER_ZONE = 2          # mirrors SafetyConfig default occupancy cap

# Activity vocabulary (string values of RobotActivity, pinned by test).
DISPATCHABLE_ACTIVITY = "idle"
ACTIVE_CHARGING_ACTIVITIES = frozenset({"charging", "queued_charger"})
_DOWN_HEALTH = "failed"


class Urgency(str, Enum):
    NOW = "now"
    SOON = "soon"
    WATCH = "watch"


class Confidence(str, Enum):
    """Deterministic evidence-provenance grade — not a probability.

    HIGH = ledger/snapshot facts; MEDIUM = projections over the frozen,
    possibly biased day forecast; LOW reserved for future sensed-only rules.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Recommendation:
    """One desired operational outcome, with the evidence behind it."""

    rule_id: str
    urgency: Urgency
    action: str
    affected_resources: tuple[str, ...]
    expected_outcome: str
    confidence: Confidence
    rationale: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "urgency": self.urgency.value,
            "action": self.action,
            "affected_resources": list(self.affected_resources),
            "expected_outcome": self.expected_outcome,
            "confidence": self.confidence.value,
            "rationale": self.rationale,
        }


def _resources(*items: str) -> tuple[str, ...]:
    return tuple(sorted(items))


def _pct(frac: float) -> str:
    return f"{frac * 100.0:.0f}%"


def _dispatchable(r, reserve_floor: float, payload_full_frac: float) -> bool:
    """Robots the shield would actually accept a collection assignment for."""
    return (
        r.activity.value == DISPATCHABLE_ACTIVITY
        and r.health.value != _DOWN_HEALTH
        and not r.awaiting_human
        and not r.estop_latched
        and r.battery_frac > reserve_floor
        and r.payload_balls < payload_full_frac * r.payload_capacity_balls
    )


def _zone_commitments(state: FacilityState, zone_id: str) -> int:
    """Robots committed to a zone — assigned to it, including in transit.

    Mirrors the shield's occupancy accounting (``zone_commitments``), which
    counts ``assigned_zone``, not physical presence: ``robots_present``
    alone would miss robots still traveling toward the zone.
    """
    return sum(1 for r in state.robots if r.assigned_zone == zone_id)


def _worthwhile_zones(state: FacilityState, floor: int, zone_cap: int):
    """Open, un-capped zones worth collecting from, richest first."""
    zones = [
        z
        for z in state.zones
        if z.is_open
        and z.balls >= floor
        and _zone_commitments(state, z.zone_id) < zone_cap
    ]
    return sorted(
        zones,
        key=lambda z: (-z.balls, _zone_commitments(state, z.zone_id), z.zone_id),
    )


def _dispatchable_robots(state, reserve_floor, payload_full_frac):
    return sorted(
        (r for r in state.robots if _dispatchable(r, reserve_floor, payload_full_frac)),
        key=lambda r: r.robot_id,
    )


def _station_fill(s) -> float:
    """Buffer fill fraction; zero-capacity buffers count as full."""
    if s.buffer_capacity_balls <= 0:
        return 1.0
    return s.buffer_balls / s.buffer_capacity_balls


def _station_preference(s) -> tuple:
    """Unload-target ordering: the shield gates on dock/queue capacity, not
    buffer fill, so an empty handoff queue outranks a low buffer. The
    snapshot lacks dock_slots/max_queue_length, so queue length is a
    preference signal, not an exact shield check."""
    return (s.queue_length, _station_fill(s), s.station_id)


def recommend(
    state: FacilityState,
    *,
    min_battery_reserve_frac: float = MIN_BATTERY_RESERVE_FRAC,
    battery_margin_frac: float = BATTERY_MARGIN_FRAC,
    zone_worth_collecting_balls: int = ZONE_WORTH_COLLECTING_BALLS,
    payload_full_frac: float = PAYLOAD_FULL_FRAC,
    buffer_near_full_frac: float = BUFFER_NEAR_FULL_FRAC,
    max_robots_per_zone: int = MAX_ROBOTS_PER_ZONE,
) -> tuple[Recommendation, ...]:
    """Pure, deterministic rule evaluation. Same state -> same tuple."""
    recs: list[Recommendation] = []
    reserve_band = min_battery_reserve_frac + battery_margin_frac
    est: Optional[StockoutEstimate] = None

    if state.meta.facility_open:
        est = estimate_stockout(state)
        recs += _rule_stockout_in_progress(state)
        supply_dispatch = _rule_stockout_dirty_supply(
            state,
            est,
            reserve_band,
            payload_full_frac,
            zone_worth_collecting_balls,
            max_robots_per_zone,
        )
        recs += supply_dispatch
        recs += _rule_stockout_demand_bound(state, est)
        if not supply_dispatch:
            recs += _rule_idle_capacity(
                state,
                reserve_band,
                payload_full_frac,
                zone_worth_collecting_balls,
                max_robots_per_zone,
            )
        recs += _rule_payload_stranded(state, payload_full_frac)

    recs += _rule_battery_reserve(state, min_battery_reserve_frac, battery_margin_frac)
    recs += _rule_robot_down(state)
    if state.meta.facility_open:
        recs += _rule_assist_backlog(state)
        recs += _rule_station_buffer_pressure(state, buffer_near_full_frac)

    order = {u: i for i, u in enumerate(Urgency)}
    recs.sort(key=lambda r: (order[r.urgency], r.rule_id, r.affected_resources))
    return tuple(recs)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _rule_stockout_in_progress(state) -> list[Recommendation]:
    flow = state.ball_flow
    if flow.clean_available > 0:
        return []
    stations_with_supply = [
        f"station:{sid}" for sid, n in flow.dirty_buffered if n > 0
    ]
    return [
        Recommendation(
            rule_id="stockout_in_progress",
            urgency=Urgency.NOW,
            action="Restore clean-ball availability — dispenser is empty",
            affected_resources=_resources("dispenser", "washer", *stations_with_supply),
            expected_outcome=(
                "Clean stock resumes as buffered and in-transit balls are washed; "
                "customer wait ends"
            ),
            confidence=Confidence.HIGH,
            rationale=(
                f"Dispenser at 0 balls; washable supply is {flow.in_wash} in wash, "
                f"{flow.dirty_buffered_total} buffered at stations, "
                f"{flow.in_transit_total} in transit"
            ),
        )
    ]


def _supply_limited(est: StockoutEstimate) -> bool:
    return (
        est.eta_minutes is not None
        and est.eta_minutes > 0
        and est.eta_minutes <= STRAINED_STOCKOUT_HORIZON_MIN
        and est.limited_by == "dirty_supply"
    )


def _blocked_side_rationale(robots, zones, zone_floor) -> str:
    """Name the actual binding constraint when no dispatch pair exists."""
    if not robots and not zones:
        return (
            "no idle, charged, non-full robot is available and no open zone "
            f"holds {zone_floor}+ balls below its crowding cap"
        )
    if not zones:
        return (
            "zone availability, not fleet readiness, is the constraint: every "
            f"open zone is below the {zone_floor}-ball collection floor or "
            "already at its crowding cap"
        )
    return "no idle, charged, non-full robot is available"


def _rule_stockout_dirty_supply(
    state, est, reserve_band, payload_full_frac, zone_floor, zone_cap
) -> list[Recommendation]:
    if not _supply_limited(est):
        return []
    urgency = (
        Urgency.NOW if est.eta_minutes <= CRITICAL_STOCKOUT_HORIZON_MIN else Urgency.SOON
    )
    flow = state.ball_flow
    eta_txt = f"~{est.eta_minutes:.0f} min"
    robots = _dispatchable_robots(state, reserve_band, payload_full_frac)
    zones = _worthwhile_zones(state, zone_floor, zone_cap)
    pairs = list(zip(robots, zones))
    if not pairs:
        return [
            Recommendation(
                rule_id="stockout_dirty_supply",
                urgency=urgency,
                action="Raise washable supply — collection is currently blocked",
                affected_resources=_resources("dispenser", "washer"),
                expected_outcome=(
                    "Projected stockout pushed out once collection can resume"
                ),
                confidence=Confidence.MEDIUM,
                rationale=(
                    f"Projected stockout in {eta_txt} is supply-limited "
                    f"({flow.in_wash + flow.dirty_buffered_total + flow.in_transit_total} "
                    f"washable vs {flow.on_field_total} on field); "
                    f"{_blocked_side_rationale(robots, zones, zone_floor)}"
                ),
            )
        ]
    recs = []
    for robot, zone in pairs:
        recs.append(
            Recommendation(
                rule_id="stockout_dirty_supply",
                urgency=urgency,
                action=f"Get {robot.robot_id} collecting in zone {zone.zone_id}",
                affected_resources=_resources(
                    f"robot:{robot.robot_id}", f"zone:{zone.zone_id}"
                ),
                expected_outcome=(
                    "Washable supply rises; projected stockout pushed beyond "
                    f"the {STRAINED_STOCKOUT_HORIZON_MIN:.0f}-min horizon"
                ),
                confidence=Confidence.MEDIUM,
                rationale=(
                    f"Projected stockout in {eta_txt} is washer-supply-limited; "
                    f"zone {zone.zone_id} holds {zone.balls} balls "
                    f"({zone.robots_present} robots present) and "
                    f"{robot.robot_id} is idle at {_pct(robot.battery_frac)} battery"
                ),
            )
        )
    return recs


def _rule_stockout_demand_bound(state, est) -> list[Recommendation]:
    if not (
        est.eta_minutes is not None
        and est.eta_minutes > 0
        and est.eta_minutes <= STRAINED_STOCKOUT_HORIZON_MIN
        and est.limited_by == "demand"
    ):
        return []
    # Urgency tracks imminence, matching the supply-limited path: the
    # customer impact of an empty dispenser in 20 minutes is the same
    # whichever constraint binds.
    urgency = (
        Urgency.NOW if est.eta_minutes <= CRITICAL_STOCKOUT_HORIZON_MIN else Urgency.SOON
    )
    peak = max(state.demand.forecast_balls_per_minute, default=0.0)
    return [
        Recommendation(
            rule_id="stockout_demand_bound",
            urgency=urgency,
            action="Prepare for a demand-driven shortfall — this stockout cannot be collected away",
            affected_resources=_resources("dispenser", "washer"),
            expected_outcome=(
                "Customer impact contained through expectation-setting; the washer, "
                "not collection, is the binding constraint for this stockout"
            ),
            confidence=Confidence.MEDIUM,
            rationale=(
                f"Projected stockout in ~{est.eta_minutes:.0f} min is demand-limited: "
                f"washer at its maximum {state.washer.throughput_balls_per_minute:.0f} "
                f"balls/min vs forecast peaks of {peak:.0f} balls/min"
            ),
        )
    ]


def _rule_battery_reserve(state, reserve, margin) -> list[Recommendation]:
    recs = []
    free_slots = max(0, state.charging.slots - state.charging.in_use)
    for r in state.robots:
        if (
            r.health.value != _DOWN_HEALTH
            and not r.awaiting_human
            and not r.estop_latched
            and r.activity.value not in ACTIVE_CHARGING_ACTIVITIES
            and r.battery_frac <= reserve + margin
        ):
            # Paused robots must be resumed before the facility will accept
            # a charge order — say so, or the advice is not actionable.
            paused = r.activity.value == "paused"
            action = (
                f"Resume {r.robot_id} and get it charged"
                if paused
                else f"Get {r.robot_id} charged"
            )
            paused_note = " (currently paused — resume it first)" if paused else ""
            recs.append(
                Recommendation(
                    rule_id="battery_reserve",
                    urgency=Urgency.NOW,
                    action=action,
                    affected_resources=_resources(f"robot:{r.robot_id}", "charger"),
                    expected_outcome=(
                        "Robot stays assignable — below the reserve floor, new "
                        "collection work is blocked until it recharges"
                    ),
                    confidence=Confidence.HIGH,
                    rationale=(
                        f"{r.robot_id} battery at {_pct(r.battery_frac)}, within "
                        f"{_pct(margin)} of the {_pct(reserve)} reserve floor"
                        f"{paused_note}; "
                        f"{free_slots} of {state.charging.slots} charger slots free"
                    ),
                )
            )
    return recs


def _rule_robot_down(state) -> list[Recommendation]:
    recs = []
    free_staff = max(0, state.staff.capacity - state.staff.busy)
    for r in state.robots:
        down = r.health.value == _DOWN_HEALTH or r.estop_latched
        if down and not r.awaiting_human:
            cause = "emergency stop latched" if r.estop_latched else "hard failure"
            recs.append(
                Recommendation(
                    rule_id="robot_down",
                    urgency=Urgency.NOW,
                    action=f"Get a technician to {r.robot_id}",
                    affected_resources=_resources(f"robot:{r.robot_id}", "staff"),
                    expected_outcome="Robot returned to service; fleet capacity restored",
                    confidence=Confidence.HIGH,
                    rationale=(
                        f"{r.robot_id} is down ({cause}) with no assistance request "
                        f"pending; {free_staff} of {state.staff.capacity} staff free"
                    ),
                )
            )
    return recs


def _rule_assist_backlog(state) -> list[Recommendation]:
    waiting = sorted(r.robot_id for r in state.robots if r.awaiting_human)
    if not waiting:
        return []
    return [
        Recommendation(
            rule_id="assist_backlog",
            urgency=Urgency.WATCH,
            action="Track the assistance backlog",
            affected_resources=_resources("staff", *(f"robot:{rid}" for rid in waiting)),
            expected_outcome="Downtime shrinks as queued assists clear",
            confidence=Confidence.HIGH,
            rationale=(
                f"{len(waiting)} robot(s) awaiting help ({', '.join(waiting)}); "
                f"{state.staff.busy} of {state.staff.capacity} staff busy, "
                f"{state.staff.queued_requests} request(s) queued"
            ),
        )
    ]


def _rule_idle_capacity(
    state, reserve_band, payload_full_frac, zone_floor, zone_cap
) -> list[Recommendation]:
    recs = []
    robots = _dispatchable_robots(state, reserve_band, payload_full_frac)
    zones = _worthwhile_zones(state, zone_floor, zone_cap)
    for robot, zone in zip(robots, zones):
        recs.append(
            Recommendation(
                rule_id="idle_capacity",
                urgency=Urgency.SOON,
                action=f"Put idle {robot.robot_id} to work in zone {zone.zone_id}",
                affected_resources=_resources(
                    f"robot:{robot.robot_id}", f"zone:{zone.zone_id}"
                ),
                expected_outcome=(
                    "Field balls return to the wash cycle instead of accumulating"
                ),
                confidence=Confidence.HIGH,
                rationale=(
                    f"{robot.robot_id} is idle at {_pct(robot.battery_frac)} battery; "
                    f"zone {zone.zone_id} holds {zone.balls} balls with "
                    f"{zone.robots_present} robots present"
                ),
            )
        )
    return recs


def _rule_payload_stranded(state, payload_full_frac) -> list[Recommendation]:
    open_stations = [s for s in state.stations if s.is_open]
    if not open_stations:
        return []
    recs = []
    for r in state.robots:
        if (
            r.activity.value == DISPATCHABLE_ACTIVITY
            and not r.awaiting_human
            and not r.estop_latched
            and r.health.value != _DOWN_HEALTH
            and r.payload_balls >= payload_full_frac * r.payload_capacity_balls
        ):
            target = min(open_stations, key=_station_preference)
            queue_note = (
                f", queue {target.queue_length}" if target.queue_length else ""
            )
            recs.append(
                Recommendation(
                    rule_id="payload_stranded",
                    urgency=Urgency.SOON,
                    action=f"Unload {r.robot_id} at station {target.station_id}",
                    affected_resources=_resources(
                        f"robot:{r.robot_id}", f"station:{target.station_id}"
                    ),
                    expected_outcome=(
                        "Payload becomes washable supply and the robot is freed "
                        "for collection"
                    ),
                    confidence=Confidence.HIGH,
                    rationale=(
                        f"{r.robot_id} is idle carrying {r.payload_balls}/"
                        f"{r.payload_capacity_balls} balls; station "
                        f"{target.station_id} buffer at {target.buffer_balls}/"
                        f"{target.buffer_capacity_balls}{queue_note}"
                    ),
                )
            )
    return recs


def _rule_station_buffer_pressure(state, near_full_frac) -> list[Recommendation]:
    recs = []
    open_stations = [s for s in state.stations if s.is_open]
    for s in open_stations:
        if s.buffer_capacity_balls > 0 and s.buffer_balls >= near_full_frac * s.buffer_capacity_balls:
            others = [o for o in open_stations if o.station_id != s.station_id]
            alt = min(others, key=_station_preference) if others else None
            alt_txt = (
                f"; route the next handoff to {alt.station_id} "
                f"({alt.buffer_balls}/{alt.buffer_capacity_balls}, "
                f"queue {alt.queue_length})"
                if alt
                else "; no alternative station is open"
            )
            recs.append(
                Recommendation(
                    rule_id="station_buffer_pressure",
                    urgency=Urgency.WATCH,
                    action=f"Relieve pressure on station {s.station_id}",
                    affected_resources=_resources(f"station:{s.station_id}"),
                    expected_outcome="Unloading never blocks on a full buffer",
                    confidence=Confidence.HIGH,
                    rationale=(
                        f"Station {s.station_id} buffer at {s.buffer_balls}/"
                        f"{s.buffer_capacity_balls} ({_pct(_station_fill(s))})"
                        f"{alt_txt}"
                    ),
                )
            )
    return recs
