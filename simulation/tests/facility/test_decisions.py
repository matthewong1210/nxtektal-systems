"""Per-rule tests for the Phase 2 decision layer (pure, no simulator).

Every rule gets a trigger case and a non-trigger case, on states built so
ONLY the condition under test can fire the rule (Phase 1 review lesson:
no vacuous passes via a co-firing branch).
"""

from __future__ import annotations

import dataclasses
import json

from nxt_range_ops.core.entities import (
    RobotActivity,
    RobotHealth,
    RobotStateSnapshot,
    StationStateSnapshot,
    ZoneStateSnapshot,
)

from nxt_facility.analysis import estimate_stockout
from nxt_facility.decisions import (
    ACTIVE_CHARGING_ACTIVITIES,
    DISPATCHABLE_ACTIVITY,
    Confidence,
    Recommendation,
    Urgency,
    recommend,
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

# ---------------------------------------------------------------------------
# Snapshot-rich state factory (ball_flow kept consistent with snapshots,
# mirroring what build_facility_state produces)
# ---------------------------------------------------------------------------


def robot(
    rid: str = "R1",
    *,
    activity: str = "idle",
    health: str = "ok",
    battery: float = 0.80,
    payload: int = 0,
    capacity: int = 200,
    awaiting: bool = False,
    estop: bool = False,
) -> RobotStateSnapshot:
    return RobotStateSnapshot(
        robot_id=rid,
        activity=RobotActivity(activity),
        health=RobotHealth(health),
        battery_frac=battery,
        payload_balls=payload,
        payload_capacity_balls=capacity,
        location="charger",
        destination=None,
        assigned_zone=None,
        estop_latched=estop,
        awaiting_human=awaiting,
    )


def zone(zid: str = "Z1", *, balls: int = 100, is_open: bool = True, present: int = 0):
    return ZoneStateSnapshot(
        zone_id=zid, balls=balls, is_open=is_open, robots_present=present
    )


def station(
    sid: str = "H1",
    *,
    is_open: bool = True,
    docked: int = 0,
    queue: int = 0,
    buffer: int = 100,
    cap: int = 1000,
):
    return StationStateSnapshot(
        station_id=sid,
        is_open=is_open,
        docked=docked,
        queue_length=queue,
        buffer_balls=buffer,
        buffer_capacity_balls=cap,
    )


def make_state(
    *,
    robots=(robot(),),
    zones=(zone(),),
    stations=(station(),),
    clean: int = 5000,
    in_wash: int = 0,
    wash_bpm: float = 40.0,
    forecast: tuple[float, ...] = (2.0, 2.0, 2.0, 2.0),
    bucket_min: int = 30,
    facility_open: bool = True,
    staff: StaffState | None = None,
    charger_slots: int = 2,
    charger_queue: int = 0,
) -> FacilityState:
    dirty_buffered = tuple((s.station_id, s.buffer_balls) for s in stations)
    on_field = tuple((z.zone_id, z.balls) for z in zones)
    in_transit = tuple((r.robot_id, r.payload_balls) for r in robots)
    total = (
        clean
        + in_wash
        + sum(n for _, n in dirty_buffered)
        + sum(n for _, n in on_field)
        + sum(n for _, n in in_transit)
    )
    inoperative = sum(
        1
        for r in robots
        if r.activity.value in ("failed", "emergency_stopped", "awaiting_human")
    )
    charging = sum(1 for r in robots if r.activity.value == "charging")
    return FacilityState(
        meta=FacilityMeta(
            t_s=36000.0,
            minute_of_day=600.0,
            facility_open=facility_open,
            scenario_name="unit",
            seed=1,
        ),
        ball_flow=BallFlow(
            total_balls=total,
            clean_available=clean,
            clean_sensed=float(clean),
            in_wash=in_wash,
            dirty_buffered=dirty_buffered,
            on_field=on_field,
            in_transit=in_transit,
        ),
        washer=WasherState(
            throughput_balls_per_minute=wash_bpm, batch_size_balls=200, wip=in_wash
        ),
        demand=DemandState(
            forecast_balls_per_minute=forecast,
            forecast_bucket_minutes=bucket_min,
            minutes_to_close=720.0,
            demand_balls_total=100,
            demand_balls_served=100,
            stockout_minutes=0.0,
            service_availability=1.0,
        ),
        fleet=FleetSummary(
            total=len(robots),
            operable=len(robots) - inoperative,
            inoperative=inoperative,
            charging=charging,
            awaiting_human=sum(1 for r in robots if r.awaiting_human),
        ),
        charging=ChargingState(
            slots=charger_slots, in_use=charging, queue_length=charger_queue
        ),
        staff=staff or StaffState(capacity=1, busy=0, queued_requests=0),
        environment=EnvironmentState(
            wet_ground_speed_multiplier=1.0,
            zones_open=sum(1 for z in zones if z.is_open),
            zones_total=len(zones),
            stations_open=sum(1 for s in stations if s.is_open),
            stations_total=len(stations),
        ),
        robots=tuple(robots),
        zones=tuple(zones),
        stations=tuple(stations),
    )


def by_rule(recs, rule_id):
    return [r for r in recs if r.rule_id == rule_id]


# ---------------------------------------------------------------------------
# Shape, determinism, ordering
# ---------------------------------------------------------------------------


def test_recommendations_are_frozen_sorted_and_serializable():
    state = make_state(
        robots=(robot("R1", battery=0.10), robot("R2", health="failed")),
    )
    recs = recommend(state)
    assert recs == recommend(state)  # deterministic
    ranks = [list(Urgency).index(r.urgency) for r in recs]
    assert ranks == sorted(ranks)
    for rec in recs:
        assert isinstance(rec, Recommendation)
        assert rec.affected_resources == tuple(sorted(rec.affected_resources))
        json.dumps(rec.to_dict())
        with_field = dataclasses.fields(rec)
        assert {f.name for f in with_field} >= {
            "rule_id",
            "urgency",
            "action",
            "affected_resources",
            "expected_outcome",
            "confidence",
            "rationale",
        }


def test_all_quiet_state_yields_no_recommendations():
    assert recommend(make_state(zones=(zone(balls=10),))) == ()


# ---------------------------------------------------------------------------
# R1 stockout_in_progress
# ---------------------------------------------------------------------------


def test_stockout_in_progress_fires_at_zero_clean():
    state = make_state(clean=0, in_wash=300, zones=(zone(balls=10),))
    recs = by_rule(recommend(state), "stockout_in_progress")
    assert len(recs) == 1
    rec = recs[0]
    assert rec.urgency is Urgency.NOW
    assert rec.confidence is Confidence.HIGH
    assert "dispenser" in rec.affected_resources
    assert "300" in rec.rationale  # washable supply count appears


def test_stockout_in_progress_silent_with_stock():
    assert by_rule(recommend(make_state(clean=100, zones=(zone(balls=10),))), "stockout_in_progress") == []


# ---------------------------------------------------------------------------
# R2 stockout_dirty_supply (dispatch)
# ---------------------------------------------------------------------------


def test_supply_limited_stockout_dispatches_one_robot_per_zone():
    state = make_state(
        clean=200,
        wash_bpm=40.0,
        forecast=(10.0, 10.0, 10.0, 10.0),
        robots=(robot("R1"), robot("R2")),
        zones=(zone("Z1", balls=400), zone("Z2", balls=300), zone("Z3", balls=20)),
        stations=(station(buffer=0),),  # no washable supply -> eta exactly 20 min
    )
    est = estimate_stockout(state)
    assert est.limited_by == "dirty_supply" and est.eta_minutes <= 30
    recs = by_rule(recommend(state), "stockout_dirty_supply")
    assert len(recs) == 2  # two eligible robots, two worthwhile zones
    assert all(r.urgency is Urgency.NOW for r in recs)
    assert all(r.confidence is Confidence.MEDIUM for r in recs)  # forecast-derived
    paired_zones = {res for r in recs for res in r.affected_resources if res.startswith("zone:")}
    assert paired_zones == {"zone:Z1", "zone:Z2"}  # one robot per zone, Z3 below floor


def test_supply_limited_stockout_without_free_robots_still_warns():
    state = make_state(
        clean=200,
        forecast=(10.0, 10.0, 10.0, 10.0),
        robots=(robot("R1", activity="collecting"),),
        zones=(zone("Z1", balls=400),),
    )
    recs = by_rule(recommend(state), "stockout_dirty_supply")
    assert len(recs) == 1
    assert "washer" in recs[0].affected_resources


def test_low_battery_robot_never_dispatched():
    state = make_state(
        clean=200,
        forecast=(10.0, 10.0, 10.0, 10.0),
        robots=(robot("R1", battery=0.16),),  # inside reserve+margin band
        zones=(zone("Z1", balls=400),),
    )
    for rec in by_rule(recommend(state), "stockout_dirty_supply"):
        assert "robot:R1" not in rec.affected_resources


# ---------------------------------------------------------------------------
# R3 stockout_demand_bound
# ---------------------------------------------------------------------------


def test_demand_bound_stockout_recommends_monitoring_not_dispatch():
    state = make_state(
        clean=300,
        in_wash=1_000_000,
        wash_bpm=40.0,
        forecast=(50.0, 50.0, 50.0, 50.0),  # demand-limited, eta 30
        robots=(robot("R1"),),
        zones=(zone("Z1", balls=400),),
    )
    est = estimate_stockout(state)
    assert est.limited_by == "demand"
    recs = recommend(state)
    assert by_rule(recs, "stockout_dirty_supply") == []
    demand_recs = by_rule(recs, "stockout_demand_bound")
    assert len(demand_recs) == 1
    assert demand_recs[0].confidence is Confidence.MEDIUM
    assert "washer" in demand_recs[0].affected_resources
    assert "40" in demand_recs[0].rationale  # washer rate cited


# ---------------------------------------------------------------------------
# R4 battery_reserve
# ---------------------------------------------------------------------------


def test_battery_reserve_fires_within_margin_band():
    state = make_state(robots=(robot("R1", battery=0.18),), zones=(zone(balls=10),))
    recs = by_rule(recommend(state), "battery_reserve")
    assert len(recs) == 1
    assert recs[0].urgency is Urgency.NOW
    assert recs[0].confidence is Confidence.HIGH
    assert recs[0].affected_resources == ("charger", "robot:R1")


def test_battery_reserve_ignores_charging_and_healthy_robots():
    state = make_state(
        robots=(
            robot("R1", battery=0.10, activity="charging"),
            robot("R2", battery=0.10, activity="queued_charger"),
            robot("R3", battery=0.50),
        ),
        zones=(zone(balls=10),),
    )
    assert by_rule(recommend(state), "battery_reserve") == []


# ---------------------------------------------------------------------------
# R5 robot_down / assist_backlog
# ---------------------------------------------------------------------------


def test_new_failure_requests_assistance_now():
    state = make_state(robots=(robot("R1", health="failed"),), zones=(zone(balls=10),))
    recs = by_rule(recommend(state), "robot_down")
    assert len(recs) == 1
    assert recs[0].urgency is Urgency.NOW
    assert "staff" in recs[0].affected_resources


def test_pending_assist_becomes_backlog_watch_not_duplicate():
    state = make_state(
        robots=(robot("R1", activity="awaiting_human", awaiting=True),),
        zones=(zone(balls=10),),
        staff=StaffState(capacity=1, busy=1, queued_requests=1),
    )
    recs = recommend(state)
    assert by_rule(recs, "robot_down") == []  # no duplicate request advice
    backlog = by_rule(recs, "assist_backlog")
    assert len(backlog) == 1
    assert backlog[0].urgency is Urgency.WATCH


# ---------------------------------------------------------------------------
# R6 idle_capacity
# ---------------------------------------------------------------------------


def test_idle_robot_sent_to_worthwhile_zone():
    state = make_state(
        robots=(robot("R1"),),
        zones=(zone("Z1", balls=400), zone("Z2", balls=30)),
    )
    recs = by_rule(recommend(state), "idle_capacity")
    assert len(recs) == 1
    assert recs[0].urgency is Urgency.SOON
    assert recs[0].confidence is Confidence.HIGH
    assert set(recs[0].affected_resources) == {"robot:R1", "zone:Z1"}


def test_idle_capacity_suppressed_when_stockout_dispatch_fired():
    state = make_state(
        clean=200,
        forecast=(10.0, 10.0, 10.0, 10.0),
        robots=(robot("R1"),),
        zones=(zone("Z1", balls=400),),
    )
    recs = recommend(state)
    assert len(by_rule(recs, "stockout_dirty_supply")) == 1
    assert by_rule(recs, "idle_capacity") == []


def test_closed_zone_and_small_zone_not_recommended():
    state = make_state(
        robots=(robot("R1"),),
        zones=(zone("Z1", balls=800, is_open=False), zone("Z2", balls=30)),
    )
    assert by_rule(recommend(state), "idle_capacity") == []


# ---------------------------------------------------------------------------
# R7 payload_stranded
# ---------------------------------------------------------------------------


def test_full_idle_robot_directed_to_emptiest_station():
    state = make_state(
        robots=(robot("R1", payload=190),),
        zones=(zone(balls=10),),
        stations=(station("H1", buffer=900), station("H2", buffer=100)),
    )
    recs = by_rule(recommend(state), "payload_stranded")
    assert len(recs) == 1
    assert "station:H2" in recs[0].affected_resources
    assert recs[0].confidence is Confidence.HIGH


def test_partial_payload_not_stranded():
    state = make_state(robots=(robot("R1", payload=100),), zones=(zone(balls=10),))
    assert by_rule(recommend(state), "payload_stranded") == []


# ---------------------------------------------------------------------------
# R8 station_buffer_pressure
# ---------------------------------------------------------------------------


def test_near_full_buffer_watched_with_alternative_named():
    state = make_state(
        zones=(zone(balls=10),),
        stations=(station("H1", buffer=950), station("H2", buffer=100)),
    )
    recs = by_rule(recommend(state), "station_buffer_pressure")
    assert len(recs) == 1
    assert recs[0].urgency is Urgency.WATCH
    assert "station:H1" in recs[0].affected_resources
    assert "H2" in recs[0].rationale  # alternative named


def test_buffer_below_threshold_silent():
    state = make_state(zones=(zone(balls=10),), stations=(station("H1", buffer=500),))
    assert by_rule(recommend(state), "station_buffer_pressure") == []


# ---------------------------------------------------------------------------
# Closed facility: only fleet-health rules apply
# ---------------------------------------------------------------------------


def test_closed_facility_limits_rules_to_fleet_health():
    state = make_state(
        facility_open=False,
        clean=0,
        robots=(robot("R1", battery=0.10), robot("R2", health="failed")),
        zones=(zone("Z1", balls=800),),
    )
    recs = recommend(state)
    fired = {r.rule_id for r in recs}
    assert fired == {"battery_reserve", "robot_down"}


# ---------------------------------------------------------------------------
# Vocabulary pins
# ---------------------------------------------------------------------------


def test_activity_literals_match_real_enums():
    activity_values = {a.value for a in RobotActivity}
    assert DISPATCHABLE_ACTIVITY in activity_values
    assert ACTIVE_CHARGING_ACTIVITIES <= activity_values


def test_affected_resources_reference_real_entities():
    state = make_state(
        clean=0,
        robots=(robot("R1", battery=0.10), robot("R2", health="failed")),
        zones=(zone("Z1", balls=400),),
        stations=(station("H1", buffer=950), station("H2", buffer=10)),
    )
    valid = (
        {"dispenser", "washer", "charger", "staff"}
        | {f"robot:{r.robot_id}" for r in state.robots}
        | {f"zone:{z.zone_id}" for z in state.zones}
        | {f"station:{s.station_id}" for s in state.stations}
    )
    for rec in recommend(state):
        assert set(rec.affected_resources) <= valid, rec.rule_id
