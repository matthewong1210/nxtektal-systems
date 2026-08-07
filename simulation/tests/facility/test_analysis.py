"""Pure operational metrics over hand-built FacilityStates (no simulator)."""

from __future__ import annotations

import pytest

from nxt_facility.analysis import (
    CRITICAL_FLEET_OPERABLE_FRAC,
    CRITICAL_STOCKOUT_HORIZON_MIN,
    OperationalState,
    classify_state,
    derive_indicators,
    estimate_stockout,
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


def make_state(
    *,
    clean: int = 600,
    in_wash: int = 0,
    dirty: int = 0,
    on_field: int = 0,
    in_transit: int = 0,
    wash_bpm: float = 40.0,
    forecast: tuple[float, ...] = (5.0, 5.0, 5.0, 5.0),
    bucket_min: int = 30,
    facility_open: bool = True,
    fleet: FleetSummary | None = None,
    staff: StaffState | None = None,
    stations_open: int = 1,
    zones_open: int = 2,
    stockout_minutes: float = 0.0,
    demand_total: int = 100,
    demand_served: int = 100,
) -> FacilityState:
    total = clean + in_wash + dirty + on_field + in_transit
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
            dirty_buffered=(("H1", dirty),),
            on_field=(("Z1", on_field),),
            in_transit=(("R1", in_transit),),
        ),
        washer=WasherState(
            throughput_balls_per_minute=wash_bpm, batch_size_balls=200, wip=in_wash
        ),
        demand=DemandState(
            forecast_balls_per_minute=forecast,
            forecast_bucket_minutes=bucket_min,
            minutes_to_close=720.0,
            demand_balls_total=demand_total,
            demand_balls_served=demand_served,
            stockout_minutes=stockout_minutes,
            service_availability=0.995,
        ),
        fleet=fleet or FleetSummary(total=3, operable=3, inoperative=0, charging=0, awaiting_human=0),
        charging=ChargingState(slots=2, in_use=0, queue_length=0),
        staff=staff or StaffState(capacity=1, busy=0, queued_requests=0),
        environment=EnvironmentState(
            wet_ground_speed_multiplier=1.0,
            zones_open=zones_open,
            zones_total=2,
            stations_open=stations_open,
            stations_total=1,
        ),
        robots=(),
        zones=(),
        stations=(),
    )


# ---------------------------------------------------------------------------
# estimate_stockout
# ---------------------------------------------------------------------------


def test_stockout_when_demand_exceeds_supply_no_washing():
    # 100 clean, no dirty supply, demand 10/min -> exactly 10 minutes.
    state = make_state(clean=100, wash_bpm=40.0, forecast=(10.0, 10.0, 10.0, 10.0))
    est = estimate_stockout(state)
    assert est.eta_minutes == pytest.approx(10.0)
    assert est.limited_by == "dirty_supply"


def test_stockout_interpolates_across_buckets():
    # 400 clean, demand 10/min, no wash supply: crosses zero at t=40,
    # i.e. 10 minutes into the second 30-minute bucket.
    state = make_state(clean=400, forecast=(10.0, 10.0, 10.0, 10.0))
    est = estimate_stockout(state)
    assert est.eta_minutes == pytest.approx(40.0)


def test_no_stockout_when_washer_covers_demand():
    # Washer 40/min >= demand 10/min with ample dirty supply -> never drops.
    state = make_state(clean=100, dirty=100_000, forecast=(10.0, 10.0, 10.0, 10.0))
    est = estimate_stockout(state)
    assert est.eta_minutes is None
    assert est.limited_by == "none"


def test_wash_rate_limited_stockout():
    # Demand 50/min > washer 40/min despite infinite dirty supply:
    # net -10/min from 300 clean -> 30 minutes; limited by demand.
    state = make_state(
        clean=300, dirty=1_000_000, wash_bpm=40.0, forecast=(50.0, 50.0, 50.0, 50.0)
    )
    est = estimate_stockout(state)
    assert est.eta_minutes == pytest.approx(30.0)
    assert est.limited_by == "demand"


def test_supply_exhaustion_mid_walk():
    # 100 clean + 150 washable; demand 10/min, washer 40/min.
    # Bucket 1 (30 min): wash limited by supply average 5/min -> supply empties,
    # clean 100 + 150 - 300 = -50 -> crossing inside bucket 1.
    # Net rate = wash 5/min - demand 10/min = -5/min -> eta = 100/5 = 20 min.
    state = make_state(clean=100, dirty=150, forecast=(10.0, 10.0, 10.0, 10.0))
    est = estimate_stockout(state)
    assert est.eta_minutes == pytest.approx(20.0)
    assert est.limited_by == "dirty_supply"


def test_already_stocked_out():
    state = make_state(clean=0, forecast=(10.0, 10.0, 10.0, 10.0))
    est = estimate_stockout(state)
    assert est.eta_minutes == 0.0


def test_in_transit_counts_toward_washable_supply():
    # Same as exhaustion case but supply arrives via robot payloads.
    state = make_state(clean=100, in_transit=150, forecast=(10.0, 10.0, 10.0, 10.0))
    assert estimate_stockout(state).eta_minutes == pytest.approx(20.0)


def test_on_field_balls_do_not_count_toward_supply():
    state = make_state(clean=100, on_field=100_000, forecast=(10.0, 10.0, 10.0, 10.0))
    assert estimate_stockout(state).eta_minutes == pytest.approx(10.0)


def test_horizon_reported():
    state = make_state(forecast=(1.0, 1.0, 1.0, 1.0), bucket_min=30)
    assert estimate_stockout(state).horizon_minutes == 120.0


# ---------------------------------------------------------------------------
# classify_state
# ---------------------------------------------------------------------------


def test_classify_closed():
    assert classify_state(make_state(facility_open=False)) is OperationalState.CLOSED


def test_classify_stockout():
    assert classify_state(make_state(clean=0)) is OperationalState.STOCKOUT


def test_classify_critical_by_eta():
    # eta 10 min < CRITICAL_STOCKOUT_HORIZON_MIN (30)
    state = make_state(clean=100, forecast=(10.0, 10.0, 10.0, 10.0))
    assert estimate_stockout(state).eta_minutes < CRITICAL_STOCKOUT_HORIZON_MIN
    assert classify_state(state) is OperationalState.CRITICAL


def test_classify_critical_by_fleet():
    fleet = FleetSummary(total=3, operable=1, inoperative=2, charging=0, awaiting_human=2)
    state = make_state(fleet=fleet)
    assert 1 / 3 < CRITICAL_FLEET_OPERABLE_FRAC
    assert classify_state(state) is OperationalState.CRITICAL


def test_classify_strained_by_eta():
    # eta 100 min: beyond critical (30), within strained horizon (120).
    state = make_state(clean=1000, forecast=(10.0, 10.0, 10.0, 10.0))
    assert classify_state(state) is OperationalState.STRAINED


def test_classify_strained_by_station_closure():
    assert classify_state(make_state(stations_open=0)) is OperationalState.STRAINED


def test_classify_strained_by_staff_saturation():
    staff = StaffState(capacity=1, busy=1, queued_requests=2)
    assert classify_state(make_state(staff=staff)) is OperationalState.STRAINED


def test_classify_strained_by_inoperative_robot():
    fleet = FleetSummary(total=3, operable=2, inoperative=1, charging=0, awaiting_human=1)
    assert classify_state(make_state(fleet=fleet)) is OperationalState.STRAINED


def test_classify_nominal():
    # Plenty of clean stock, tiny demand, healthy fleet, everything open.
    state = make_state(clean=5000, dirty=1000, forecast=(2.0, 2.0, 2.0, 2.0))
    assert classify_state(state) is OperationalState.NOMINAL


# ---------------------------------------------------------------------------
# derive_indicators
# ---------------------------------------------------------------------------


def test_indicators_arithmetic():
    state = make_state(clean=600, in_wash=100, dirty=200, on_field=80, in_transit=20)
    ind = derive_indicators(state)
    assert ind.clean_frac == pytest.approx(600 / 1000)
    assert ind.washable_supply_frac == pytest.approx((100 + 200 + 20) / 1000)
    assert ind.fleet_operable_frac == pytest.approx(1.0)
    assert ind.stations_open_frac == pytest.approx(1.0)
    assert ind.zones_open_frac == pytest.approx(1.0)
    assert ind.staff_utilization == pytest.approx(0.0)
    assert ind.service_availability == pytest.approx(0.995)
    assert ind.demand_fill_rate == pytest.approx(1.0)


def test_indicators_zero_demand_guard():
    state = make_state(demand_total=0, demand_served=0)
    assert derive_indicators(state).demand_fill_rate == 1.0
