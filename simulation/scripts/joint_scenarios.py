"""Pure compilation of finite, explicitly synthetic range/course episodes.

The returned timelines are immutable *scenario inputs by convention*, not a
second live facility state. RangeSimulation owns execution/personnel occupancy
and BallLedger owns balls. No workflow result is an input to this compiler.
Course frames contain evaluator-only truth and sampled synthetic observations;
only released inspection labor enters the simulator's closed staff-job contract.
All weather/soil/customer response numbers are uncalibrated assumptions. There
is no real camera, turf diagnosis, repair-effect model, or physical readiness.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

import numpy as np

from nxt_range_ops.config.models import RangeOpsScenario
from nxt_range_ops.scenarios.generators import make_scenario
from nxt_telemetry.course_condition import build_observation
from scripts.course_18_hole_fixture import build_fixture, checkpoint_catalog

SCHEMA = "nxt-joint-episode/v0"
COMPILER_VERSION = "joint-scenarios-v0.1"
REGIMES = ("dry", "morning_rain", "afternoon_rain", "rain_then_surge", "surprise_surge")
SOURCE = "SYNTHETIC_ASSUMPTION"
DEFAULT_ASSUMPTIONS = {
    "open_minute": 480,
    "duration_minutes": 480,
    "rain_drop_fraction": 0.55,
    "rain_intensity_mm_h": 8.0,
    "surge_multiplier": 2.5,
    "demand_scale": 1.0,
    "initial_stock_balls": 6000,
    "staff_count": 2,
    "robot_failure_rate_per_hour": 0.025,
    "soil_drying_multiplier": 1.0,
    "inspection_duration_minutes": 20,
    "inspection_sla_minutes": 120,
    "observation_blur_probability": 0.10,
    "observation_occlusion_probability": 0.05,
    "unobserved_green_count": 6,
}
# capacity, infiltration limit, subsurface drainage, surface drainage (mm, mm/h)
# These are pedagogical buckets, not a hydrological or agronomic calibration.
SOIL_GROUPS = {
    "fairway": (10.0, 12.0, 2.0, 1.0),
    "bunker": (6.0, 10.0, 3.0, 1.5),
    "green": (8.0, 14.0, 4.0, 2.0),
}


def _digest(body: object) -> str:
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _assumptions(overrides: dict | None) -> dict:
    if overrides is not None and type(overrides) is not dict:
        raise ValueError("assumptions must be a plain dict or None")
    overrides = {} if overrides is None else overrides
    if set(overrides) - set(DEFAULT_ASSUMPTIONS):
        raise ValueError("unknown assumption keys")
    values = {**DEFAULT_ASSUMPTIONS, **overrides}
    integers = {
        "open_minute": (0, 1380), "duration_minutes": (60, 960),
        "initial_stock_balls": (0, 8000), "staff_count": (1, 12),
        "inspection_duration_minutes": (1, 120), "inspection_sla_minutes": (1, 960),
        "unobserved_green_count": (0, 18),
    }
    numbers = {
        "rain_drop_fraction": (0.0, 1.0), "rain_intensity_mm_h": (0.0, 50.0),
        "surge_multiplier": (1.0, 10.0), "demand_scale": (0.0, 10.0),
        "robot_failure_rate_per_hour": (0.000001, 2.0),
        "soil_drying_multiplier": (0.25, 4.0),
        "observation_blur_probability": (0.0, 1.0),
        "observation_occlusion_probability": (0.0, 1.0),
    }
    for key, (low, high) in integers.items():
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise ValueError(f"{key} must be an integer in {low}..{high}")
    for key, (low, high) in numbers.items():
        value = values[key]
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise ValueError(f"{key} must be finite in {low}..{high}")
        values[key] = float(value)
    if values["open_minute"] % 30 or values["duration_minutes"] % 60:
        raise ValueError("opening must align to 30 minutes and duration to 60 minutes")
    if values["open_minute"] + values["duration_minutes"] > 1440:
        raise ValueError("episode must fit within one UTC day")
    if values["observation_blur_probability"] + values["observation_occlusion_probability"] > 1:
        raise ValueError("blur and occlusion probabilities must sum to at most one")
    return values


def _weather(regime: str, values: dict, rng: np.random.Generator) -> list[float]:
    """One coherent half-day storm, with correlated half-hour intensity."""
    duration = values["duration_minutes"]
    split = duration // 2
    multiplier = 1.0
    rain = []
    for offset in range(duration):
        if offset % 30 == 0:
            multiplier = 0.75 * multiplier + 0.25 * float(rng.uniform(0.65, 1.35))
        raining = (
            regime in ("morning_rain", "rain_then_surge") and offset < split
        ) or (regime == "afternoon_rain" and offset >= split)
        rain.append(round(values["rain_intensity_mm_h"] * multiplier, 6) if raining else 0.0)
    return rain


def _advance_bucket(state: list[float], group: tuple[float, ...], rain: float, drying: float) -> None:
    """Advance an assumed bucket one minute; stored water persists after rain."""
    capacity, infiltration, drainage, runoff = group
    soil, surface = state
    surface += rain / 60
    transfer = min(surface, max(0.0, capacity - soil), infiltration / 60)
    soil += transfer
    surface -= transfer
    state[:] = [max(0.0, soil - drainage * drying / 60),
                max(0.0, surface - runoff * drying / 60)]


def _blocked_windows(minutes: list[int]) -> list[dict]:
    windows: list[dict] = []
    for minute in minutes:
        if windows and windows[-1]["end_minute"] == minute:
            windows[-1]["end_minute"] += 1
        else:
            windows.append({"start_minute": minute, "end_minute": minute + 1})
    return windows


def build_joint_episode(seed: int, regime: str, assumptions: dict | None = None) -> dict:
    """Build one detached 8-hour default episode, without any runtime/I/O.

    Absolute minute fields are UTC minutes since midnight. Demand samples are
    indexed from opening. Frames are sampled every five minutes, before that
    minute's rainfall is integrated, and have an explicit closing snapshot.
    Storm timing is part of the demand forecast; walk-in surges remain spikes
    outside that forecast. Staff demand is one initial inspection/rephotography
    per sampled checkpoint per day, not a repair or a continuously renewed job.
    Changing staff/stock/failure assumptions never changes exogenous demand.
    """
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be an integer in 0..2**32-1")
    if type(regime) is not str or regime not in REGIMES:
        raise ValueError(f"regime must be one of {REGIMES}")
    values = _assumptions(assumptions)
    scene_keys = ("open_minute", "duration_minutes", "rain_intensity_mm_h", "soil_drying_multiplier",
                  "observation_blur_probability", "observation_occlusion_probability", "unobserved_green_count")
    scene_id = _digest({"compiler": COMPILER_VERSION, "seed": seed, "regime": regime,
                        "scene_assumptions": {key: values[key] for key in scene_keys}})[:16]
    streams = np.random.SeedSequence(seed).spawn(4)
    weather_rng, demand_rng, quality_rng, wear_rng = [np.random.default_rng(s) for s in streams]
    rain = _weather(regime, values, weather_rng)
    opening = values["open_minute"]
    duration = values["duration_minutes"]
    closing = opening + duration
    base = make_scenario("normal_weekday")
    raw = base.model_dump()
    raw.update(name=f"joint_{regime}", description="SIMULATION: synthetic weather/customer/course assumptions",
               hours={"open_minute": opening, "close_minute": closing},
               initial_dispenser_frac=values["initial_stock_balls"] / base.total_balls)
    raw["human_ops"]["staff_count"] = values["staff_count"]
    raw["episode"].update(control_interval_s=60.0, max_steps=duration + 1)
    for robot in raw["robots"]:
        robot["mean_time_between_failures_h"].update(
            value=1 / values["robot_failure_rate_per_hour"],
            note="synthetic assumption: reciprocal robot failure rate; not measured")
    windows = []
    for offset in range(duration):
        rate = base.demand.base_rate_at(opening + offset) * values["demand_scale"]
        if rain[offset] > 0:
            rate *= 1 - values["rain_drop_fraction"]
        if windows and windows[-1]["balls_per_minute"] == rate:
            windows[-1]["window"]["end_minute"] += 1
        else:
            windows.append({"window": {"start_minute": opening + offset, "end_minute": opening + offset + 1},
                            "balls_per_minute": rate})
    spikes = []
    if regime in ("rain_then_surge", "surprise_surge"):
        surge_start = opening + (duration * 2 // 3 // 30) * 30
        spikes.append({"window": {"start_minute": surge_start,
                                   "end_minute": min(closing, surge_start + max(30, duration // 4))},
                       "multiplier": values["surge_multiplier"]})
    raw["demand"].update(windows=windows, spikes=spikes)
    scenario = RangeOpsScenario.model_validate(raw)
    demand = [int(demand_rng.poisson(scenario.demand.true_rate_at(minute)))
              for minute in range(opening, closing)]

    site, model = build_fixture()
    catalog = checkpoint_catalog()
    # A deterministic spatial subset remains unobserved; geometry still has all 18 holes.
    omitted = {cp["id"] for cp in catalog if cp["surface_type"] == "green"
               and cp["hole_number"] > 18 - values["unobserved_green_count"]}
    sampled_catalog = [cp for cp in catalog if cp["id"] not in omitted]
    soil = {cp["id"]: [SOIL_GROUPS[cp["surface_type"]][0] * 0.2, 0.0] for cp in catalog}
    wear = {cp["id"]: bool(wear_rng.random() < 0.12) for cp in catalog}
    range_groups = {zone.zone_id: (8.0 + index * 2, 12.0 + index * 2,
                                  2.0 + index * 0.8, 1.5)
                    for index, zone in enumerate(scenario.zones)}
    range_soil = {key: [group[0] * 0.2, 0.0] for key, group in range_groups.items()}
    blocked = {zone.zone_id: [] for zone in scenario.zones}
    frames, jobs, sources = [], [], []
    job_points = set()
    midnight = datetime(2026, 9, 16, tzinfo=timezone.utc)
    for offset in range(duration + 1):
        minute = opening + offset
        # Access is a collection restriction only; customer play stays open.
        if offset < duration:
            for zone_id, state in range_soil.items():
                if state[0] / range_groups[zone_id][0] >= 0.9 or state[1] >= 0.5:
                    blocked[zone_id].append(minute)
        if offset % 5 == 0:
            soil_rows, observations, released = [], [], []
            conditions = {}
            for cp in catalog:
                state, kind = soil[cp["id"]], cp["surface_type"]
                condition = "STANDING_WATER" if state[1] >= 0.5 else (
                    {"fairway": "DIVOT", "bunker": "BUNKER_SURFACE", "green": "CLEAR"}[kind]
                    if wear[cp["id"]] else "CLEAR")
                conditions[cp["id"]] = condition
                soil_rows.append({"checkpoint_id": cp["id"], "hole_number": cp["hole_number"],
                                  "soil_group": kind, "soil_moisture_index": round(state[0] / SOIL_GROUPS[kind][0], 6),
                                  "surface_water_mm": round(state[1], 6), "condition": condition,
                                  "source_kind": SOURCE})
            # Saved closing snapshot is not another observation/admission opportunity.
            if offset < duration:
                for cart_index in range(3):
                    cp = sampled_catalog[((offset // 5) * 3 + cart_index) % len(sampled_catalog)]
                    quality_roll = quality_rng.random()
                    quality = "BLURRED" if quality_roll < values["observation_blur_probability"] else (
                        "OCCLUDED" if quality_roll < values["observation_blur_probability"] + values["observation_occlusion_probability"]
                        else "USABLE")
                    condition = conditions[cp["id"]]
                    inspected = condition if condition != "CLEAR" else {
                        "fairway": "DIVOT", "bunker": "BUNKER_SURFACE", "green": "STANDING_WATER"
                    }[cp["surface_type"]]
                    at = (midnight + timedelta(minutes=minute)).isoformat(timespec="seconds").replace("+00:00", "Z")
                    frame_id = f"joint-{scene_id}-{minute}-{cart_index}"
                    obs = build_observation(
                        site_id=site.site_id, deployment_id=site.deployment_id, map_revision=model.content_digest,
                        frame_id=frame_id, checkpoint_id=cp["id"], hole_number=cp["hole_number"],
                        feature_id=cp["feature_id"], cart_id=f"synthetic-survey-cart-{cart_index + 1}",
                        camera_id=f"synthetic-camera-{cart_index + 1}", captured_at_utc=at,
                        condition=condition, inspected_condition=inspected, quality=quality,
                        cart_position={"x_m": cp["cart_x_m"], "y_m": cp["cart_y_m"], "accuracy_m": 3.0},
                        target_position={"x_m": cp["x_m"], "y_m": cp["y_m"], "accuracy_m": 5.0},
                        detection_score=0.7, evidence_ref=f"synthetic:{frame_id}")
                    observations.append(obs)
                    if cp["id"] not in job_points and (quality != "USABLE" or condition != "CLEAR"):
                        job_points.add(cp["id"])
                        job_id = f"inspect-{scene_id}-{cp['id']}"
                        jobs.append({"job_id": job_id, "available_minute": minute,
                                     "duration_minutes": values["inspection_duration_minutes"],
                                     "deadline_minute": min(closing, minute + values["inspection_sla_minutes"]),
                                     "hole_number": cp["hole_number"], "checkpoint_id": cp["id"]})
                        released.append(job_id)
                        sources.append({"job_id": job_id, "observation_id": obs["observation_id"],
                                        "reason": "REPHOTOGRAPH" if quality != "USABLE" else "INSPECT_CANDIDATE",
                                        "source_kind": SOURCE})
            frames.append({"minute": minute, "elapsed_minute": offset,
                           "weather": {"rain_mm_per_hour": rain[offset] if offset < duration else 0.0,
                                       "source_kind": SOURCE},
                           "soil": soil_rows, "observations": observations, "released_job_ids": released})
        if offset < duration:
            for cp in catalog:
                _advance_bucket(soil[cp["id"]], SOIL_GROUPS[cp["surface_type"]], rain[offset], values["soil_drying_multiplier"])
            for zone_id, state in range_soil.items():
                _advance_bucket(state, range_groups[zone_id], rain[offset], values["soil_drying_multiplier"])
    joint_inputs = {"schema": "nxt-joint-scenario-inputs/v0", "environment": "SIMULATION",
                    "demand_by_minute": demand,
                    "collection_blocks": {key: _blocked_windows(value) for key, value in blocked.items()},
                    "staff_jobs": jobs}
    metadata = {
        "compiler_version": COMPILER_VERSION, "source_kind": SOURCE,
        "scenario_date_utc": "2026-09-16",
        "assumptions": deepcopy(values), "overridden_assumptions": sorted(assumptions or {}),
        "seed_streams": {"weather": 0, "demand": 1, "observation_quality": 2, "wear": 3},
        "course_identity": {"site_id": site.site_id, "deployment_id": site.deployment_id,
                            "map_revision": model.content_digest, "coordinate_frame_id": model.frame.frame_id},
        "course_checkpoints": catalog, "unobserved_checkpoint_ids": sorted(omitted),
        "soil_assumptions": {"source_kind": SOURCE, "groups": deepcopy(SOIL_GROUPS),
                             "initial_soil_fraction": 0.2, "standing_water_threshold_mm": 0.5,
                             "collection_soil_fraction_threshold": 0.9},
        "job_sources": sources, "demand_digest": _digest(demand),
        "disclaimers": [
            "All weather, soil, demand, image quality and labor durations are synthetic assumptions, not measured or calibrated.",
            "Rain timing is preforecast; spikes are unforecast walk-ins. Customer demand is balls, not a count of visitors.",
            "Course truth/future frames are evaluation-only. Clear observations cover one checkpoint/category/time, not an entire hole.",
            "At most one initial inspection/rephotography job per sampled checkpoint per day; completion is labor only, never repair evidence.",
            "Independent days reset inventory and resources; they do not claim continuous cross-day facility state.",
        ],
    }
    metadata["episode_digest"] = _digest({"compiler": COMPILER_VERSION, "seed": seed, "regime": regime,
                                          "assumptions": values, "scenario": scenario.model_dump(mode="json"),
                                          "joint_inputs": joint_inputs, "course_frames": frames})
    return {"schema": SCHEMA, "environment": "SIMULATION", "seed": seed, "regime": regime,
            "scenario": scenario, "joint_inputs": joint_inputs, "course_frames": frames, "metadata": metadata}
