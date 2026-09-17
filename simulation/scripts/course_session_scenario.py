"""Compile finite synthetic camera/course/range session inputs, without execution.

This composition root owns immutable scenario assumptions only. It samples
visitors and *requested* shots; only RangeSimulation/BallLedger may turn served
requests into conserved balls. Weather, soils and defects are evaluator inputs,
not measurements, observations, forecasts or admitted work. A consumer must not
give their future/hidden contents to a policy or a pixel-only detector.
"""

from __future__ import annotations

from copy import deepcopy
import math

import numpy as np

from nxt_range_ops.config.models import RangeOpsScenario
from nxt_range_ops.scenarios.generators import make_scenario
from scripts.course_18_hole_fixture import checkpoint_catalog

SCHEMA = "nxt-course-session-scenario/v1"
COMPILER_VERSION = "course-session-scenario-v1.0"
SOURCE = "SYNTHETIC_ASSUMPTION"
OPEN_MINUTE = 480
CLOSE_MINUTE = 1080
CART_COUNT = 16
MAX_REQUESTED_SHOTS = 250_000
MAX_ACTIVE_VISITORS = 500
YARD_M = 0.9144
REGIMES = ("dry", "morning_rain", "afternoon_rain", "rain_then_surge", "surprise_surge")
DEFAULT_CONFIG = {"seed": 17092026, "days": 3, "staff_count": 2,
                  "initial_stock": 6000, "demand_scale": 1.0}
DEFAULT_ASSUMPTIONS = {
    "rain_intensity_mm_h": 8.0,
    "rain_arrival_multiplier": 0.45,
    "rain_rebound_multiplier": 1.5,
    "arrival_rate_per_minute": 0.35,
    "stay_mean_minutes": 55.0,
    "shots_per_minute": 1.0,
    "driver_total_reference_yd": 225.0,
    "range_ball_distance_multiplier": 0.92,
    "driver_roll_fraction": 0.10,
    "wind_distance_fraction_per_mps": 0.008,
    "rain_carry_fraction_per_mm_h": 0.002,
    "wet_roll_reduction": 0.80,
    "wind_pause_mps": 14.0,
    "soil_drainage_multiplier": 1.0,
    "cart_speed_mps": 2.5,
    "cart_dwell_minutes": 3,
    "camera_interval_minutes": 20,
    "camera_height_m": 1.8,
    "camera_fov_deg": 68.0,
    "gps_accuracy_m": 3.0,
    "camera_blur_probability": 0.10,
    "camera_occlusion_probability": 0.05,
    "camera_outage_probability_per_day": 0.06,
}
# Every weight/factor below is an explicit scenario assumption, not a sampled
# demographic finding. Total-distance reference is only used to anchor DRIVER.
ABILITY_PROFILES = (
    {"id": "developing", "weight": 0.30, "distance_factor": 0.78, "lateral_sd_fraction": 0.19},
    {"id": "recreational", "weight": 0.50, "distance_factor": 1.0, "lateral_sd_fraction": 0.13},
    {"id": "experienced", "weight": 0.20, "distance_factor": 1.15, "lateral_sd_fraction": 0.09},
)
CLUB_PROFILES = (
    {"id": "WEDGE", "weight": 0.20, "distance_factor": 0.34, "roll_factor": 0.10},
    {"id": "SHORT_IRON", "weight": 0.20, "distance_factor": 0.49, "roll_factor": 0.25},
    {"id": "MID_IRON", "weight": 0.25, "distance_factor": 0.64, "roll_factor": 0.40},
    {"id": "LONG_IRON", "weight": 0.10, "distance_factor": 0.76, "roll_factor": 0.65},
    {"id": "WOOD", "weight": 0.10, "distance_factor": 0.88, "roll_factor": 0.85},
    {"id": "DRIVER", "weight": 0.15, "distance_factor": 1.0, "roll_factor": 1.0},
)
TASK_DURATIONS = {"INSPECT": 10, "REPHOTOGRAPH": 8, "REPAIR_DIVOT": 15,
                  "RAKE_BUNKER": 20, "CLEAR_DEBRIS": 15}
# capacity, infiltration, drainage, surface runoff: pedagogical water buckets.
SOILS = {"fairway": (18.0, 15.0, 2.0, 4.0),
         "bunker": (10.0, 18.0, 3.0, 3.0),
         "green": (14.0, 20.0, 5.0, 5.0)}


def _number(value: object, key: str, low: float, high: float) -> float:
    if type(value) not in (int, float) or not low <= value <= high:
        raise ValueError(f"{key} must be a finite number in {low}..{high}")
    return float(value)


def _config(config: dict | None) -> tuple[dict, dict, list[str]]:
    if config is None:
        config = {}
    if type(config) is not dict or set(config) - (set(DEFAULT_CONFIG) | {"assumptions", "weather_regimes"}):
        raise ValueError("unknown session config keys or non-dict config")
    values = {**DEFAULT_CONFIG, **{key: val for key, val in config.items() if key in DEFAULT_CONFIG}}
    for key, low, high in (("seed", 0, 2**32 - 1), ("days", 1, 7),
                           ("staff_count", 1, 12), ("initial_stock", 0, 8000)):
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise ValueError(f"{key} must be an integer in {low}..{high}")
    values["demand_scale"] = _number(values["demand_scale"], "demand_scale", 0, 5)
    overrides = config.get("assumptions", {})
    if type(overrides) is not dict or set(overrides) - (set(DEFAULT_ASSUMPTIONS) | {"ability_profiles", "club_profiles"}):
        raise ValueError("unknown session assumptions or non-dict assumptions")
    assumptions = deepcopy({**DEFAULT_ASSUMPTIONS, "ability_profiles": list(ABILITY_PROFILES),
                            "club_profiles": list(CLUB_PROFILES), **overrides})
    bounds = {
        "rain_intensity_mm_h": (0, 50), "rain_arrival_multiplier": (0, 1),
        "rain_rebound_multiplier": (1, 3), "arrival_rate_per_minute": (0, 2),
        "stay_mean_minutes": (15, 120), "shots_per_minute": (0.2, 3),
        "driver_total_reference_yd": (100, 350), "range_ball_distance_multiplier": (0.5, 1),
        "driver_roll_fraction": (0, 0.3), "wind_distance_fraction_per_mps": (0, 0.03),
        "rain_carry_fraction_per_mm_h": (0, 0.01), "wet_roll_reduction": (0, 1),
        "wind_pause_mps": (8, 30), "soil_drainage_multiplier": (0.25, 4),
        "cart_speed_mps": (0.5, 5), "camera_height_m": (1, 3),
        "camera_fov_deg": (30, 100), "gps_accuracy_m": (0, 10),
        "camera_blur_probability": (0, 1), "camera_occlusion_probability": (0, 1),
        "camera_outage_probability_per_day": (0, 1),
    }
    for key, (low, high) in bounds.items():
        assumptions[key] = _number(assumptions[key], key, low, high)
    for key, low, high in (("cart_dwell_minutes", 1, 10), ("camera_interval_minutes", 10, 120)):
        if type(assumptions[key]) is not int or not low <= assumptions[key] <= high:
            raise ValueError(f"{key} must be an integer in {low}..{high}")
    if assumptions["camera_blur_probability"] + assumptions["camera_occlusion_probability"] > 1:
        raise ValueError("blur and occlusion probabilities must sum to at most one")
    for key, profiles, coefficient, factor_bounds in (
        ("ability_profiles", ABILITY_PROFILES, "lateral_sd_fraction", (0.02, 0.5)),
        ("club_profiles", CLUB_PROFILES, "roll_factor", (0.0, 1.5)),
    ):
        custom = assumptions[key]
        if (type(custom) is not list or len(custom) != len(profiles)
                or any(type(row) is not dict or set(row) != {"id", "weight", "distance_factor", coefficient}
                       for row in custom)
                or [row["id"] for row in custom] != [row["id"] for row in profiles]):
            raise ValueError(f"{key} must retain the exact ordered profile IDs and fields")
        for row in custom:
            row["weight"] = _number(row["weight"], f"{key}.weight", 0.0, 1.0)
            row["distance_factor"] = _number(row["distance_factor"], f"{key}.distance_factor", 0.1, 1.5)
            row[coefficient] = _number(row[coefficient], f"{key}.{coefficient}", *factor_bounds)
        if not math.isclose(sum(row["weight"] for row in custom), 1.0, rel_tol=0, abs_tol=1e-9):
            raise ValueError(f"{key} weights must sum to one")
    regimes = config.get("weather_regimes")
    if regimes is not None and (type(regimes) is not list or len(regimes) != values["days"]
                                or any(type(item) is not str or item not in REGIMES for item in regimes)):
        raise ValueError("weather_regimes must contain one known regime per day")
    return values, assumptions, list(regimes) if regimes is not None else []


def _weather(values: dict, a: dict, regimes: list[str], rng: np.random.Generator) -> list[dict]:
    end = (values["days"] - 1) * 1440 + CLOSE_MINUTE
    weather = []
    wind = 4.0
    for day in range(values["days"]):
        # Choose later days only on reaching them; extending the horizon does
        # not alter the already-compiled weather prefix.
        regime = regimes[day] if regimes else (
            ("rain_then_surge", "surprise_surge")[day] if day < 2 else str(rng.choice(REGIMES)))
        intensity = a["rain_intensity_mm_h"]
        gale_start = int(rng.choice([660, 840, 960])) if rng.random() < 0.18 else -1
        for local in range(0, 1440, 30):
            minute = day * 1440 + local
            if minute < OPEN_MINUTE or minute >= end:
                continue
            raining = (regime in ("morning_rain", "rain_then_surge") and OPEN_MINUTE <= local < 780
                       or regime == "afternoon_rain" and 780 <= local < CLOSE_MINUTE)
            intensity = 0.8 * intensity + 0.2 * a["rain_intensity_mm_h"] * float(rng.uniform(0.65, 1.35))
            wind = 0.8 * wind + 0.2 * float(rng.uniform(1, 9))
            speed = float(rng.uniform(15, 19)) if local == gale_start else wind
            rain = intensity if raining else 0.0
            daylight = max(0.0, math.sin(math.pi * (local - 360) / 840)) if 360 <= local <= 1200 else 0.0
            cloud = 0.88 if rain else float(rng.uniform(0.15, 0.65))
            weather.append({"minute": minute, "end_minute": min(minute + 30, end), "day": day,
                            "regime": regime, "rain_mm_h": round(rain, 4), "wind_mps": round(speed, 4),
                            "wind_direction_rad": round(float(rng.uniform(-math.pi, math.pi)), 6),
                            "temperature_c": round(18 + 7 * daylight - rain * 0.08, 3),
                            "cloud_cover": round(cloud, 4), "sunlight": round(daylight * (1 - cloud * 0.65), 4),
                            "play_paused": speed >= a["wind_pause_mps"], "source_kind": SOURCE})
    return weather


def _weather_at(weather: list[dict], minute: int) -> dict:
    return weather[(minute - OPEN_MINUTE) // 30]


def _bucket(state: list[float], soil: tuple[float, ...], rain: float, drying: float) -> None:
    capacity, infiltration, drainage, runoff = soil
    state[1] += rain / 60
    transfer = min(state[1], capacity - state[0], infiltration / 60)
    state[0] = max(0.0, state[0] + transfer - drainage * drying / 60)
    state[1] = max(0.0, state[1] - transfer - runoff * drying / 60)


def _windows(minutes: list[int]) -> list[dict]:
    windows = []
    for minute in minutes:
        if windows and windows[-1]["end_minute"] == minute:
            windows[-1]["end_minute"] += 1
        else:
            windows.append({"start_minute": minute, "end_minute": minute + 1})
    return windows


def _surfaces(catalog: list[dict], weather: list[dict], end: int, a: dict,
              rng: np.random.Generator) -> tuple[list[dict], list[dict], dict, list[float]]:
    soil = {cp["id"]: [SOILS[cp["surface_type"]][0] * 0.25, 0.0] for cp in catalog}
    # Different drainage in the near/middle/far bands; no fabricated field units.
    zone_groups = {f"{distance}_{side}": (14.0 + 6 * index, 12.0 + 3 * index, 2.0 + 2 * index, 4.0)
                   for index, distance in enumerate(("NEAR", "MID", "FAR")) for side in ("LEFT", "CENTER", "RIGHT")}
    zone_soil = {key: [group[0] * 0.25, 0.0] for key, group in zone_groups.items()}
    blocked = {key: [] for key in zone_groups}
    snapshots, events, range_wetness = [], [], []
    water_start = {}
    for minute in range(OPEN_MINUTE, end):
        w = _weather_at(weather, minute)
        open_now = OPEN_MINUTE <= minute % 1440 < CLOSE_MINUTE
        wet = []
        for key, state in zone_soil.items():
            wet.append(state[0] / zone_groups[key][0])
            if state[0] / zone_groups[key][0] >= 0.90 or state[1] >= 0.5 or w["play_paused"]:
                blocked[key].append(minute)
            _bucket(state, zone_groups[key], w["rain_mm_h"], a["soil_drainage_multiplier"])
        range_wetness.append(float(sum(wet) / len(wet)))
        for cp in catalog:
            state, kind = soil[cp["id"]], cp["surface_type"]
            moisture = state[0] / SOILS[kind][0]
            if state[1] >= 0.5 and cp["id"] not in water_start:
                water_start[cp["id"]] = minute
            if state[1] < 0.5 and cp["id"] in water_start:
                events.append(_event(cp, "STANDING_WATER", water_start.pop(cp["id"]), minute, rng,
                                     radius=float(rng.uniform(1, 3)), subtype="surface_water_bucket"))
            if minute % 30 == 0:
                snapshots.append({"minute": minute, "checkpoint_id": cp["id"], "hole_number": cp["hole_number"],
                                  "surface_type": kind, "moisture_index": round(moisture, 6),
                                  "grass_firmness_index": round(1 - 0.75 * moisture, 6) if kind != "bunker" else None,
                                  # Moderate moisture can improve modeled workability;
                                  # saturation reduces it. This is not universal sand physics.
                                  "sand_workability_index": round(max(0, 1 - abs(moisture - 0.35) / 0.70), 6) if kind == "bunker" else None,
                                  "surface_water_mm": round(state[1], 6), "wetness": round(moisture, 6),
                                  "sunlight": w["sunlight"], "cloud_cover": w["cloud_cover"], "source_kind": SOURCE})
            # Defects can recur; they persist absent later completion-derived
            # rendering. No calendar-time repair success is invented here.
            if open_now and minute % 15 == 0:
                chance = (0.035 if kind == "fairway" else 0.025) * (1 + moisture * 0.6)
                if rng.random() < chance:
                    condition = {"fairway": "DIVOT", "bunker": "BUNKER_SURFACE", "green": "DEBRIS"}[kind]
                    radius = float(rng.uniform(0.04, 0.13)) if condition == "DIVOT" else float(rng.uniform(0.25, 0.9))
                    events.append(_event(cp, condition, minute, end, rng, radius=radius,
                                         subtype={"DIVOT": "single_divot", "BUNKER_SURFACE": "footprint_cluster",
                                                  "DEBRIS": "leaves_or_litter"}[condition]))
                if kind != "green" and rng.random() < 0.005 * (1 + w["wind_mps"] / 5):
                    events.append(_event(cp, "DEBRIS", minute, end, rng,
                                         radius=float(rng.uniform(0.25, 0.7)), subtype="fallen_branch_or_litter"))
            _bucket(state, SOILS[kind], w["rain_mm_h"], a["soil_drainage_multiplier"])
    by_id = {cp["id"]: cp for cp in catalog}
    for cp_id, start in water_start.items():
        events.append(_event(by_id[cp_id], "STANDING_WATER", start, end, rng,
                             radius=float(rng.uniform(1, 3)), subtype="surface_water_bucket"))
    events.sort(key=lambda row: (row["start_minute"], row["checkpoint_id"], row["condition"], row["x_m"], row["y_m"]))
    for index, event in enumerate(events):
        event["event_id"] = f"course-event-{index:06d}"
    return snapshots, events, {key: _windows(times) for key, times in blocked.items()}, range_wetness


def _event(cp: dict, condition: str, start: int, end: int, rng: np.random.Generator,
           *, radius: float, subtype: str) -> dict:
    return {"event_id": "pending", "checkpoint_id": cp["id"], "hole_number": cp["hole_number"],
            "condition": condition, "start_minute": start, "end_minute": end,
            "x_m": round(float(cp["x_m"]) + float(rng.uniform(-4, 4)), 4),
            "y_m": round(float(cp["y_m"]) + float(rng.uniform(-3, 3)), 4),
            "radius_m": round(radius, 4), "subtype": subtype, "source_kind": SOURCE}


def _arrival_profile(local: int) -> float:
    return 0.7 if local < 600 else 1.0 if local < 780 else 1.2 if local < 1020 else 0.5


def _shot_zone(forward_m: float, lateral_m: float) -> str:
    distance = "NEAR" if forward_m < 90 else "MID" if forward_m < 165 else "FAR"
    side = "LEFT" if lateral_m < -12 else "RIGHT" if lateral_m > 12 else "CENTER"
    return f"{distance}_{side}"


def _traffic(values: dict, a: dict, weather: list[dict], wetness: list[float], end: int,
             arrival_rng: np.random.Generator, shot_rng: np.random.Generator,
             group_rng: np.random.Generator) -> tuple[list, list, list]:
    visitors, traffic, shots_by_minute, incidents = [], [], [], []
    next_id, requested_total = 0, 0
    abilities = [p["weight"] for p in a["ability_profiles"]]
    club_weights = [p["weight"] for p in a["club_profiles"]]
    group_minute = {day: day * 1440 + 840 + int(group_rng.integers(0, 60))
                    for day in range(values["days"])}
    for minute in range(OPEN_MINUTE, end):
        day, local = divmod(minute, 1440)
        w = _weather_at(weather, minute)
        active = OPEN_MINUTE <= local < CLOSE_MINUTE
        departing = sum(v["leave_minute"] <= minute for v in visitors)
        visitors = [v for v in visitors if v["leave_minute"] > minute]
        rate = a["arrival_rate_per_minute"] * values["demand_scale"] * _arrival_profile(local) if active else 0.0
        if w["rain_mm_h"] > 0:
            rate *= a["rain_arrival_multiplier"]
        elif w["regime"] == "rain_then_surge" and 780 <= local < 900:
            rate *= a["rain_rebound_multiplier"]
        if w["play_paused"] or local >= CLOSE_MINUTE - 15:
            rate = 0.0
        arrivals = int(arrival_rng.poisson(rate))
        group = 0
        if (active and not w["play_paused"] and minute == group_minute[day]
                and w["regime"] in ("rain_then_surge", "surprise_surge") and values["demand_scale"] > 0):
            group = int(arrival_rng.integers(10, 25) * values["demand_scale"])
            arrivals += group
            incidents.append({"kind": "UNEXPECTED_RANGE_GROUP", "start_minute": minute,
                              "end_minute": minute + 1, "visitors": group,
                              "severity": "INFO", "source_kind": SOURCE})
        if len(visitors) + arrivals > MAX_ACTIVE_VISITORS:
            raise ValueError("session compile budget exceeded: more than 500 active visitors")
        for _ in range(arrivals):
            next_id += 1
            ability = int(arrival_rng.choice(len(a["ability_profiles"]), p=abilities))
            stay = int(np.clip(arrival_rng.normal(a["stay_mean_minutes"], 12), 15, 150))
            visitors.append({"id": f"visitor-{next_id:06d}", "ability": ability,
                             "arrived_minute": minute, "leave_minute": min(minute + stay, day * 1440 + CLOSE_MINUTE),
                             "next_shot": float(minute) + float(arrival_rng.uniform(1, 4)),
                             "cadence_factor": float(arrival_rng.uniform(0.7, 1.3)),
                             "club_index": None, "club_shots_left": 0})
        shots = []
        if active and not w["play_paused"]:
            for visitor in visitors:
                # At most six attempts per minute by construction of cadence.
                while visitor["next_shot"] < minute + 1:
                    requested_total += 1
                    if requested_total > MAX_REQUESTED_SHOTS:
                        raise ValueError("session compile budget exceeded: more than 250000 requested shots")
                    profile = a["ability_profiles"][visitor["ability"]]
                    if visitor["club_shots_left"] == 0:
                        visitor["club_index"] = int(shot_rng.choice(len(a["club_profiles"]), p=club_weights))
                        visitor["club_shots_left"] = int(shot_rng.integers(5, 16))
                    visitor["club_shots_left"] -= 1
                    club = a["club_profiles"][visitor["club_index"]]
                    total_reference = a["driver_total_reference_yd"] * YARD_M * profile["distance_factor"] * club["distance_factor"]
                    roll_fraction = a["driver_roll_fraction"] * club["roll_factor"]
                    # 225 yd anchor is total, not carry. Range-ball, wind,
                    # rain and roll modifications are separate assumptions.
                    carry = total_reference * (1 - roll_fraction) * a["range_ball_distance_multiplier"]
                    carry *= float(np.clip(shot_rng.normal(1, 0.11), 0.15, 1.4))
                    carry *= max(0.5, 1 + w["wind_mps"] * math.cos(w["wind_direction_rad"]) * a["wind_distance_fraction_per_mps"])
                    carry *= max(0.5, 1 - w["rain_mm_h"] * a["rain_carry_fraction_per_mm_h"])
                    roll = total_reference * roll_fraction * a["range_ball_distance_multiplier"] * (1 - a["wet_roll_reduction"] * wetness[minute - OPEN_MINUTE])
                    total = carry + roll
                    lateral = float(shot_rng.normal(0, max(1, carry * profile["lateral_sd_fraction"])))
                    lateral += w["wind_mps"] * math.sin(w["wind_direction_rad"]) * carry * 0.007
                    zone = _shot_zone(total, lateral)
                    shots.append({"request_id": f"shot-{minute}-{len(shots):04d}", "minute": minute,
                                  "visitor_id": visitor["id"], "ability_profile": profile["id"], "club": club["id"],
                                  "carry_m": round(carry, 4), "roll_m": round(roll, 4),
                                  "landing_x_m": round(total, 4), "landing_y_m": round(lateral, 4),
                                  "zone_id": zone, "source_kind": SOURCE})
                    visitor["next_shot"] += max(0.20, float(shot_rng.lognormal(0, 0.25)) /
                                                (a["shots_per_minute"] * visitor["cadence_factor"]))
        else:
            for visitor in visitors:
                # Paused shots are skipped, not released as an impossible
                # accumulated burst on resumption.
                visitor["next_shot"] = max(visitor["next_shot"], minute + 1)
        shots_by_minute.append(shots)
        traffic.append({"minute": minute, "arrivals": arrivals, "departures": departing,
                        "active_visitors": len(visitors), "requested_balls": len(shots),
                        "arrival_rate": round(rate, 6), "unexpected_group_visitors": group,
                        "play_paused": bool(active and w["play_paused"]), "source_kind": SOURCE})
    return traffic, shots_by_minute, incidents


def _route(catalog: list[dict], speed: float, dwell: int) -> tuple[list[dict], float]:
    """Closed route on declared per-hole paths plus explicit assumed connectors."""
    nodes = []
    for number in range(1, 19):
        points = [cp for cp in catalog if cp["hole_number"] == number]
        fairway, bunker, green = points
        origin_x, origin_y = float(fairway["cart_x_m"]) - 120, float(fairway["cart_y_m"]) - 25
        nodes += [{"x": origin_x + 20, "y": origin_y + 25, "cp": None},
                  {"x": fairway["cart_x_m"], "y": fairway["cart_y_m"], "cp": fairway},
                  {"x": bunker["cart_x_m"], "y": bunker["cart_y_m"], "cp": bunker},
                  {"x": origin_x + 270, "y": origin_y + 25, "cp": None},
                  {"x": green["cart_x_m"], "y": green["cart_y_m"], "cp": green},
                  {"x": origin_x + 270, "y": origin_y + 110, "cp": None}]
    segments, elapsed = [], 0.0
    for index, node in enumerate(nodes):
        next_node = nodes[(index + 1) % len(nodes)]
        if node["cp"] is not None:
            segments.append({"start": elapsed, "end": elapsed + dwell, "a": node, "b": node, "cp": node["cp"]})
            elapsed += dwell
        distance = math.hypot(next_node["x"] - node["x"], next_node["y"] - node["y"])
        minutes = distance / (speed * 60)
        segments.append({"start": elapsed, "end": elapsed + minutes, "a": node, "b": next_node, "cp": None})
        elapsed += minutes
    return segments, elapsed


def _cart_pose(segments: list[dict], cycle: float, progress: float) -> tuple[float, float, float, dict | None]:
    phase = progress % cycle
    for segment in segments:
        if segment["start"] <= phase < segment["end"]:
            frac = (phase - segment["start"]) / (segment["end"] - segment["start"])
            start, finish = segment["a"], segment["b"]
            return (float(start["x"] + frac * (finish["x"] - start["x"])),
                    float(start["y"] + frac * (finish["y"] - start["y"])),
                    math.atan2(finish["y"] - start["y"], finish["x"] - start["x"]), segment["cp"])
    raise AssertionError("route phase escaped compiled segments")


def _carts(catalog: list[dict], weather: list[dict], end: int, a: dict,
           rng: np.random.Generator, outage_rng: np.random.Generator) -> tuple[list, list, list, list]:
    segments, cycle = _route(catalog, a["cart_speed_mps"], a["cart_dwell_minutes"])
    routes, schedule, incidents, checkpoints = [], [], [], []
    progress = [cycle * index / CART_COUNT for index in range(CART_COUNT)]
    last_photo = [-a["camera_interval_minutes"]] * CART_COUNT
    outages = {}
    for day in range((end - CLOSE_MINUTE) // 1440 + 1):
        for index in range(CART_COUNT):
            if outage_rng.random() < a["camera_outage_probability_per_day"]:
                start = day * 1440 + int(outage_rng.integers(OPEN_MINUTE, CLOSE_MINUTE - 60))
                outages[(day, index)] = (start, min(day * 1440 + CLOSE_MINUTE, start + int(outage_rng.integers(30, 91))))
                incidents.append({"kind": "CAMERA_OFFLINE", "cart_id": f"CART-{index + 1:02d}",
                                  "start_minute": outages[(day, index)][0], "end_minute": outages[(day, index)][1],
                                  "severity": "WARNING", "source_kind": SOURCE})
    for minute in range(OPEN_MINUTE, end + 1):
        local = minute % 1440
        open_now = minute < end and OPEN_MINUTE <= local < CLOSE_MINUTE
        paused = _weather_at(weather, min(minute, end - 1))["play_paused"]
        for index in range(CART_COUNT):
            x, y, heading, cp = _cart_pose(segments, cycle, progress[index])
            cart_id = f"CART-{index + 1:02d}"
            online = not (outages.get((minute // 1440, index), (end + 1, end + 1))[0] <= minute <
                          outages.get((minute // 1440, index), (end + 1, end + 1))[1])
            routes.append({"minute": minute, "cart_id": cart_id, "x_m": round(x, 6), "y_m": round(y, 6),
                           "heading_rad": round(heading, 6), "checkpoint_id": cp["id"] if cp else None,
                           "state": "PARKED" if not open_now or paused else "DWELLING" if cp else "MOVING",
                           "camera_online": online})
            if open_now and not paused and cp and minute - last_photo[index] >= a["camera_interval_minutes"]:
                last_photo[index] = minute
                error_angle, error_radius = float(rng.uniform(-math.pi, math.pi)), a["gps_accuracy_m"] * float(rng.random() ** 0.5)
                quality = float(rng.random())
                blur = 5.0 if quality < a["camera_blur_probability"] else 0.0
                occlusion = 0.7 if a["camera_blur_probability"] <= quality < a["camera_blur_probability"] + a["camera_occlusion_probability"] else 0.0
                schedule.append({"minute": minute, "cart_id": cart_id, "checkpoint_id": cp["id"],
                                 "cart_x_m": round(x + error_radius * math.cos(error_angle), 6),
                                 "cart_y_m": round(y + error_radius * math.sin(error_angle), 6),
                                 "true_cart_x_m": round(x, 6), "true_cart_y_m": round(y, 6),
                                 "position_accuracy_m": max(0.1, a["gps_accuracy_m"]), "camera_online": online,
                                 "camera": {"x_m": x, "y_m": y, "z_m": a["camera_height_m"],
                                            "target_x_m": cp["x_m"], "target_y_m": cp["y_m"],
                                            "fov_deg": a["camera_fov_deg"], "motion_blur_px": blur,
                                            "lens_occlusion": occlusion}, "source_kind": SOURCE})
            if open_now and not paused:
                progress[index] += 1
    for segment in segments:
        checkpoints.append({"x_m": segment["a"]["x"], "y_m": segment["a"]["y"],
                            "checkpoint_id": segment["cp"]["id"] if segment["cp"] else None})
    return routes, schedule, incidents, checkpoints


def compile_session(config: dict | None = None) -> dict:
    """Return detached finite session inputs, with no runtime or file writes.

    Minutes are absolute since day-zero midnight; demand/landing/shot lists are
    indexed from 08:00 day zero and contain explicit empty overnight entries.
    No reset, repair success, observed issue or staff admission is generated.
    Resource config changes preserve the external weather/visitor/camera stream.
    """
    values, a, regimes = _config(config)
    end = (values["days"] - 1) * 1440 + CLOSE_MINUTE
    streams = [np.random.default_rng(s) for s in np.random.SeedSequence(values["seed"]).spawn(7)]
    weather = _weather(values, a, regimes, streams[0])
    catalog = checkpoint_catalog()
    snapshots, events, blocks, wetness = _surfaces(catalog, weather, end, a, streams[1])
    traffic, shots, incidents = _traffic(values, a, weather, wetness, end, streams[2], streams[3], streams[6])
    routes, schedule, camera_incidents, route_waypoints = _carts(catalog, weather, end, a, streams[4], streams[5])
    incidents += camera_incidents
    for row in weather:
        if row["play_paused"] and OPEN_MINUTE <= row["minute"] % 1440 < CLOSE_MINUTE:
            incidents.append({"kind": "STRONG_WIND_PLAY_PAUSE", "start_minute": row["minute"],
                              "end_minute": row["end_minute"], "severity": "WARNING", "source_kind": SOURCE})
    raw = make_scenario("normal_weekday").model_dump()
    raw.update(name="whole_course_camera_range_session_v2",
               description="SIMULATION: continuous requested-shot and camera/course scenario; all uncalibrated assumptions",
               hours={"open_minute": OPEN_MINUTE, "close_minute": CLOSE_MINUTE},
               initial_dispenser_frac=values["initial_stock"] / raw["total_balls"])
    raw["human_ops"]["staff_count"] = values["staff_count"]
    raw["episode"].update(control_interval_s=60.0, max_steps=end - OPEN_MINUTE + 1)
    raw["zones"] = [{"zone_id": f"{distance}_{side}", "position": {"x_m": x, "y_m": y}, "landing_weight": 1.0,
                     "closure_windows": []}
                    for distance, x in (("NEAR", 55.0), ("MID", 125.0), ("FAR", 205.0))
                    for side, y in (("LEFT", -30.0), ("CENTER", 0.0), ("RIGHT", 30.0))]
    # Only a generic expected daily demand profile enters scenario forecasts.
    # No exact visitor list, surge timing or future weather is disclosed.
    raw["demand"].update(windows=[{"window": {"start_minute": start, "end_minute": finish},
                                   "balls_per_minute": a["arrival_rate_per_minute"] * a["stay_mean_minutes"] *
                                   a["shots_per_minute"] * values["demand_scale"] * _arrival_profile(start)}
                                  for start, finish in ((480, 600), (600, 780), (780, 1020), (1020, 1080))], spikes=[])
    scenario = RangeOpsScenario.model_validate(raw)
    slots = [{"job_id": f"d{day}-{cp['id']}-{kind.lower()}", "hole_number": cp["hole_number"],
              "checkpoint_id": cp["id"], "duration_minutes": duration, "task_kind": kind}
             for day in range(values["days"]) for cp in catalog for kind, duration in TASK_DURATIONS.items()]
    return {"schema": SCHEMA, "compiler_version": COMPILER_VERSION, "environment": "SIMULATION",
            "config": {**values, "assumptions": deepcopy(a), "weather_regimes": [weather_row["regime"] for weather_row in weather
                                                                                      if weather_row["minute"] % 1440 == OPEN_MINUTE]},
            "scenario": scenario,
            "session_inputs": {"schema": "nxt-course-range-session/v1", "environment": "SIMULATION", "days": values["days"],
                               "demand_by_minute": [len(row) for row in shots],
                               "landing_zones_by_minute": [[shot["zone_id"] for shot in row] for row in shots],
                               "collection_blocks": blocks, "staff_job_slots": slots},
            "weather": weather, "traffic": traffic, "shots": shots, "course_events": events,
            "surface_snapshots": snapshots, "catalog": catalog, "cart_routes": routes,
            "camera_schedule": schedule, "cart_route_waypoints": route_waypoints,
            "operational_events": sorted(incidents, key=lambda row: (row["start_minute"], row["kind"], row.get("cart_id", ""))),
            "assumptions": {"source_kind": SOURCE, "parameters": deepcopy(a),
                            "ability_profiles": deepcopy(a["ability_profiles"]), "club_profiles": deepcopy(a["club_profiles"]),
                            "driver_reference": {"total_distance_yd": a["driver_total_reference_yd"],
                                "source_url": "https://www.arccosgolf.com/blogs/community/launch-it-like-a-firework-3-driving-distance-insights-you-should-see",
                                "scope": "approximate driver total-distance context for adult male amateur tracked users; not carry or all-population mean; synthetic profiles uncalibrated"},
                            "range_boundaries": {"forward_m": [90, 165], "lateral_m": [-12, 12],
                                                 "tail_handling": "all forward/lateral tails assigned to open outer zones; no claimed surveyed boundary"},
                            "camera_route": "16 carts phase-staggered on per-hole paths and assumed connecting corridors; targeted camera heading; pause in place overnight; point checks only",
                            "material_model": "uncalibrated water buckets; turf firmness index; sand workability peaks at moderate moisture and falls at saturation; no universal sticky-sand assertion",
                            "defect_effects": "event inputs persist; root may derive a repair effect only from completed runtime work, and later usable image must verify",
                            "operational_scope": "visitors, rain/rebound/group demand, wind play pause, camera outages, divots, bunker disturbance, surface water and debris; no exhaustive incident coverage",
                            "shot_admission": "requested hypothetical landings only; BallLedger admits only the served request prefix for each minute"}}
