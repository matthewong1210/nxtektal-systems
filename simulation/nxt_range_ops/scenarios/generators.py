"""Scenario generators: named operating-day configurations.

Every quantity is a placeholder-provenance value (see the Phase 0 placeholder
policy): scenarios exercise the decision problem and the pipeline, and their
results must never be quoted as real facility performance.
"""

from __future__ import annotations

from typing import Callable

from nxt_range_ops.config.models import (
    ChargerConfig,
    DemandConfig,
    DemandSpike,
    DemandWindow,
    EconomicsConfig,
    HandoffStationConfig,
    HumanOpsConfig,
    OperatingHoursConfig,
    P,
    Point2D,
    RangeOpsScenario,
    RobotConfig,
    SafetyConfig,
    SensorConfig,
    SkillModelConfig,
    TimeWindow,
    WasherConfig,
    ZoneConfig,
)


def _base_demand_windows(scale: float = 1.0) -> list[DemandWindow]:
    profile = [
        (360, 540, 8.0),  # 06:00-09:00 warmup
        (540, 720, 15.0),  # 09:00-12:00 morning
        (720, 1020, 20.0),  # 12:00-17:00 afternoon
        (1020, 1260, 30.0),  # 17:00-21:00 evening peak
        (1260, 1320, 10.0),  # 21:00-22:00 wind-down
    ]
    return [
        DemandWindow(
            window=TimeWindow(start_minute=a, end_minute=b),
            balls_per_minute=rate * scale,
        )
        for a, b, rate in profile
    ]


def _base_zones() -> list[ZoneConfig]:
    layout = [
        ("Z1", 40.0, -25.0, 3.0),
        ("Z2", 70.0, -10.0, 5.0),
        ("Z3", 100.0, 0.0, 6.0),
        ("Z4", 130.0, 10.0, 4.0),
        ("Z5", 170.0, 0.0, 2.0),
        ("Z6", 210.0, -15.0, 1.0),
    ]
    return [
        ZoneConfig(zone_id=z, position=Point2D(x_m=x, y_m=y), landing_weight=w)
        for z, x, y, w in layout
    ]


def _base_robots(n: int = 3, mtbf_h: float = 40.0) -> list[RobotConfig]:
    return [
        RobotConfig(
            robot_id=f"R{i + 1}",
            payload_capacity_balls=600,
            battery_capacity_wh=P(1000.0, "placeholder battery pack"),
            speed_mps=P(1.5, "placeholder cruise speed"),
            idle_power_w=P(20.0, "placeholder idle electronics draw"),
            mean_time_between_failures_h=P(mtbf_h, "placeholder MTBF"),
        )
        for i in range(n)
    ]


def _base_skills(**overrides) -> SkillModelConfig:
    base = dict(
        collect_cycle_s=P(30.0, "placeholder pickup sweep cycle"),
        collect_cycle_balls=40,
        collect_power_w=P(200.0, "placeholder collection power"),
        travel_power_w=P(150.0, "placeholder drive power"),
        dock_attempt_s=P(45.0, "placeholder docking attempt"),
        dock_failure_prob=0.05,
        max_dock_retries=3,
        dock_power_w=P(80.0, "placeholder docking power"),
        unload_balls_per_s=P(15.0, "placeholder unload rate"),
        unload_power_w=P(120.0, "placeholder lift/dump power"),
    )
    base.update(overrides)
    return SkillModelConfig(**base)


def _base(name: str, description: str, **overrides) -> RangeOpsScenario:
    fields = dict(
        name=name,
        description=description,
        total_balls=8000,
        initial_dispenser_frac=0.9,
        dispenser_position=Point2D(x_m=0.0, y_m=0.0),
        hours=OperatingHoursConfig(open_minute=360, close_minute=1320),
        demand=DemandConfig(windows=_base_demand_windows()),
        zones=_base_zones(),
        robots=_base_robots(),
        stations=[
            HandoffStationConfig(
                station_id="H1",
                position=Point2D(x_m=10.0, y_m=-20.0),
                dock_slots=2,
                max_queue_length=4,
                buffer_capacity_balls=2500,
            )
        ],
        washer=WasherConfig(balls_per_minute=P(40.0, "placeholder washer throughput")),
        charger=ChargerConfig(
            position=Point2D(x_m=5.0, y_m=-30.0),
            slots=2,
            charge_rate_w=P(500.0, "placeholder charge rate"),
        ),
        sensors=SensorConfig(),
        skills=_base_skills(),
        human_ops=HumanOpsConfig(
            response_delay_s=P(240.0, "placeholder staff response"),
            fix_duration_s=P(420.0, "placeholder fix duration"),
        ),
        economics=EconomicsConfig(
            energy_cost_per_kwh=P(0.30, "placeholder tariff"),
            cost_per_human_intervention=P(18.0, "placeholder labor cost per callout"),
            robot_hour_cost=P(2.5, "placeholder amortized robot cost"),
        ),
    )
    fields.update(overrides)
    return RangeOpsScenario(**fields)


# ----------------------------------------------------------------------
# The ten named scenarios
# ----------------------------------------------------------------------


def normal_weekday() -> RangeOpsScenario:
    return _base("normal_weekday", "Baseline weekday demand profile.")


def weekend_peak() -> RangeOpsScenario:
    return _base(
        "weekend_peak",
        "Sustained high weekend demand (~1.6x weekday).",
        demand=DemandConfig(windows=_base_demand_windows(scale=1.6)),
    )


def demand_spike() -> RangeOpsScenario:
    return _base(
        "demand_spike",
        "Unforecast walk-in group event mid-afternoon (2.5x for 90 min).",
        demand=DemandConfig(
            windows=_base_demand_windows(),
            spikes=[
                DemandSpike(
                    window=TimeWindow(start_minute=840, end_minute=930),
                    multiplier=2.5,
                )
            ],
        ),
    )


def robot_failure() -> RangeOpsScenario:
    return _base(
        "robot_failure",
        "Unreliable fleet: hardware failures expected within the day.",
        robots=_base_robots(mtbf_h=5.0),
    )


def handoff_station_outage() -> RangeOpsScenario:
    return _base(
        "handoff_station_outage",
        "The only handoff station is down 12:00-14:00.",
        stations=[
            HandoffStationConfig(
                station_id="H1",
                position=Point2D(x_m=10.0, y_m=-20.0),
                dock_slots=2,
                max_queue_length=4,
                buffer_capacity_balls=2500,
                outage_windows=[TimeWindow(start_minute=720, end_minute=840)],
            )
        ],
    )


def charger_congestion() -> RangeOpsScenario:
    return _base(
        "charger_congestion",
        "Single slow charge slot: charging becomes a contended resource.",
        charger=ChargerConfig(
            position=Point2D(x_m=5.0, y_m=-30.0),
            slots=1,
            charge_rate_w=P(300.0, "placeholder degraded charge rate"),
        ),
        robots=[
            RobotConfig(
                robot_id=f"R{i + 1}",
                payload_capacity_balls=600,
                battery_capacity_wh=P(600.0, "placeholder smaller pack"),
                speed_mps=P(1.5, "placeholder cruise speed"),
                idle_power_w=P(20.0, "placeholder idle electronics draw"),
                mean_time_between_failures_h=P(40.0, "placeholder MTBF"),
            )
            for i in range(3)
        ],
    )


def repeated_docking_failure() -> RangeOpsScenario:
    return _base(
        "repeated_docking_failure",
        "Degraded docking guides: frequent dock failures, occasional e-stop.",
        skills=_base_skills(dock_failure_prob=0.35, max_dock_retries=2),
        safety=SafetyConfig(dock_incident_estop_prob=0.05),
    )


def wet_ground() -> RangeOpsScenario:
    return _base(
        "wet_ground",
        "Rain-softened turf: all ground travel slowed to 60% speed.",
        skills=_base_skills(wet_ground_speed_multiplier=0.6),
    )


def demand_forecast_error() -> RangeOpsScenario:
    return _base(
        "demand_forecast_error",
        "Badly biased and noisy demand forecast (underestimates by ~40%).",
        demand=DemandConfig(
            windows=_base_demand_windows(),
            forecast_bias=0.6,
            forecast_noise_sd=0.5,
        ),
    )


def noisy_inventory_sensor() -> RangeOpsScenario:
    return _base(
        "noisy_inventory_sensor",
        "Inventory sensing delayed 10 min with 15% noise, slow updates.",
        sensors=SensorConfig(
            inventory_delay_s=600.0,
            inventory_noise_rel_sd=0.15,
            update_period_s=120.0,
        ),
    )


SCENARIO_GENERATORS: dict[str, Callable[[], RangeOpsScenario]] = {
    "normal_weekday": normal_weekday,
    "weekend_peak": weekend_peak,
    "demand_spike": demand_spike,
    "robot_failure": robot_failure,
    "handoff_station_outage": handoff_station_outage,
    "charger_congestion": charger_congestion,
    "repeated_docking_failure": repeated_docking_failure,
    "wet_ground": wet_ground,
    "demand_forecast_error": demand_forecast_error,
    "noisy_inventory_sensor": noisy_inventory_sensor,
}


def make_scenario(name: str) -> RangeOpsScenario:
    if name not in SCENARIO_GENERATORS:
        raise KeyError(
            f"unknown scenario {name!r}; choose from {sorted(SCENARIO_GENERATORS)}"
        )
    return SCENARIO_GENERATORS[name]()
