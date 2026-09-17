"""Immutable scenario compilation: finite inputs, plausible structure and no truth leak."""

from collections import Counter, defaultdict
from copy import deepcopy
import json
import math

import pytest

from nxt_range_ops.core.session_inputs import validate_session_inputs
from scripts.course_session_scenario import (
    CART_COUNT, CLOSE_MINUTE, OPEN_MINUTE, TASK_DURATIONS, compile_session,
)


@pytest.fixture(scope="module")
def one_day():
    return compile_session({"seed": 42, "days": 1})


@pytest.fixture(scope="module")
def two_days():
    return compile_session({"seed": 42, "days": 2})


def test_contract_and_finite_point_catalog(one_day):
    assert one_day["schema"] == "nxt-course-session-scenario/v1"
    assert one_day["environment"] == "SIMULATION"
    scenario = one_day["scenario"]
    assert (scenario.hours.open_minute, scenario.hours.close_minute) == (480, 1080)
    assert len(scenario.zones) == 9
    inputs = one_day["session_inputs"]
    assert set(inputs) == {"schema", "environment", "days", "demand_by_minute", "landing_zones_by_minute",
                           "collection_blocks", "staff_job_slots"}
    assert inputs["schema"] == "nxt-course-range-session/v1"
    assert inputs["days"] == 1
    assert len(one_day["catalog"]) == 54
    assert {cp["hole_number"] for cp in one_day["catalog"]} == set(range(1, 19))
    assert len({cp["id"] for cp in one_day["catalog"]}) == 54
    assert all(cp["x_m"] != cp["cart_x_m"] or cp["y_m"] != cp["cart_y_m"] for cp in one_day["catalog"])
    json.dumps({**one_day, "scenario": scenario.model_dump(mode="json")}, allow_nan=False)
    normalized = validate_session_inputs(inputs, zone_ids=scenario.zone_ids,
                                         open_minute=OPEN_MINUTE, close_minute=CLOSE_MINUTE)
    assert normalized["demand_by_minute"] == inputs["demand_by_minute"]
    assert normalized["landing_zones_by_minute"] == inputs["landing_zones_by_minute"]
    assert normalized["collection_blocks"] == inputs["collection_blocks"]


def test_requested_shots_arrivals_and_landings_are_consistent(one_day):
    inputs = one_day["session_inputs"]
    ids, clubs, profiles = set(), Counter(), Counter()
    active = 0
    for offset, (traffic, shots, demand, zones) in enumerate(zip(
        one_day["traffic"], one_day["shots"], inputs["demand_by_minute"], inputs["landing_zones_by_minute"], strict=True,
    )):
        assert traffic["minute"] == OPEN_MINUTE + offset
        active += traffic["arrivals"] - traffic["departures"]
        assert active == traffic["active_visitors"]
        assert demand == len(shots) == len(zones) == traffic["requested_balls"]
        assert demand <= active * 6
        for shot, zone in zip(shots, zones, strict=True):
            assert shot["request_id"] not in ids
            ids.add(shot["request_id"])
            assert shot["zone_id"] == zone and zone in one_day["scenario"].zone_ids
            assert shot["carry_m"] > 0 and shot["roll_m"] >= 0
            assert shot["landing_x_m"] == pytest.approx(shot["carry_m"] + shot["roll_m"], abs=0.0002)
            assert shot["source_kind"] == "SYNTHETIC_ASSUMPTION"
            assert not {"served", "ball_id", "collected"} & set(shot)
            clubs[shot["club"]] += 1
            profiles[shot["ability_profile"]] += 1
    assert len(clubs) == 6 and len(profiles) == 3
    assert clubs["DRIVER"] < len(ids) * 0.4
    assert sum(inputs["demand_by_minute"]) == len(ids) > 1000
    assert any(row["unexpected_group_visitors"] > 0 for row in one_day["traffic"])


def test_club_practice_uses_runs_instead_of_swapping_every_shot(one_day):
    by_visitor = defaultdict(list)
    for shots in one_day["shots"]:
        for shot in shots:
            by_visitor[shot["visitor_id"]].append(shot["club"])
    assert all(len(set(clubs[:5])) == 1 for clubs in by_visitor.values() if len(clubs) >= 5)
    assert any(len(set(clubs)) > 1 for clubs in by_visitor.values())


def test_multiday_inputs_include_zero_demand_nights_without_route_reset(two_days, one_day):
    horizon = 1440 + CLOSE_MINUTE - OPEN_MINUTE
    assert len(two_days["traffic"]) == horizon
    assert len(two_days["session_inputs"]["demand_by_minute"]) == horizon
    assert two_days["scenario"].episode.max_steps >= horizon
    assert one_day["weather"] == two_days["weather"][:len(one_day["weather"])]
    assert one_day["shots"] == two_days["shots"][:600]
    assert one_day["camera_schedule"] == [row for row in two_days["camera_schedule"] if row["minute"] < CLOSE_MINUTE]
    for minute in range(CLOSE_MINUTE, 1440 + OPEN_MINUTE):
        offset = minute - OPEN_MINUTE
        assert two_days["session_inputs"]["demand_by_minute"][offset] == 0
        assert two_days["session_inputs"]["landing_zones_by_minute"][offset] == []
        assert two_days["traffic"][offset]["active_visitors"] == 0
    positions = defaultdict(dict)
    for row in two_days["cart_routes"]:
        positions[row["cart_id"]][row["minute"]] = (row["x_m"], row["y_m"])
    for route in positions.values():
        assert route[CLOSE_MINUTE] == route[1440 + OPEN_MINUTE]
    assert all(OPEN_MINUTE <= row["minute"] % 1440 < CLOSE_MINUTE for row in two_days["camera_schedule"])


def test_routes_are_continuous_and_cameras_observe_real_route_points(one_day):
    by_cart = defaultdict(list)
    poses = {}
    for row in one_day["cart_routes"]:
        by_cart[row["cart_id"]].append(row)
        poses[(row["minute"], row["cart_id"])] = row
    assert len(by_cart) == CART_COUNT
    speed = one_day["assumptions"]["parameters"]["cart_speed_mps"]
    for rows in by_cart.values():
        assert {row["state"] for row in rows} >= {"MOVING", "DWELLING"}
        for previous, current in zip(rows, rows[1:]):
            distance = math.hypot(current["x_m"] - previous["x_m"], current["y_m"] - previous["y_m"])
            assert distance <= speed * 60 + 0.000002
    seen = set()
    last_photo = {}
    for camera in one_day["camera_schedule"]:
        route = poses[(camera["minute"], camera["cart_id"])]
        seen.add(camera["checkpoint_id"])
        assert route["checkpoint_id"] == camera["checkpoint_id"]
        assert route["state"] == "DWELLING"
        assert camera["true_cart_x_m"] == route["x_m"]
        assert camera["true_cart_y_m"] == route["y_m"]
        error = math.hypot(camera["cart_x_m"] - camera["true_cart_x_m"], camera["cart_y_m"] - camera["true_cart_y_m"])
        assert error <= camera["position_accuracy_m"] + 0.000002
        pose = camera["camera"]
        assert math.hypot(pose["target_x_m"] - pose["x_m"], pose["target_y_m"] - pose["y_m"]) >= 20
        assert camera["minute"] - last_photo.get(camera["cart_id"], -20) >= 20
        last_photo[camera["cart_id"]] = camera["minute"]
    # The camera cadence can miss even declared checkpoints; routes passing
    # through them must not silently manufacture a photo or full coverage.
    assert 30 <= len(seen) <= 54
    assert seen <= {cp["id"] for cp in one_day["catalog"]}
    assert all("condition" not in row and "defects" not in row for row in one_day["camera_schedule"])


def test_weather_material_response_persists_and_water_indices_are_explicit(one_day):
    weather = {row["minute"]: row for row in one_day["weather"]}
    assert all(row["rain_mm_h"] > 0 for minute, row in weather.items() if minute < 780)
    assert all(row["rain_mm_h"] == 0 for minute, row in weather.items() if minute >= 780)
    samples = defaultdict(dict)
    for row in one_day["surface_snapshots"]:
        samples[row["checkpoint_id"]][row["minute"]] = row
        assert 0 <= row["moisture_index"] <= 1
        assert row["source_kind"] == "SYNTHETIC_ASSUMPTION"
        if row["surface_type"] == "bunker":
            assert row["grass_firmness_index"] is None
            assert 0 <= row["sand_workability_index"] <= 1
        else:
            assert row["sand_workability_index"] is None
            assert 0 <= row["grass_firmness_index"] <= 1
    fairway = samples["cp-h01-fairway"]
    assert fairway[750]["grass_firmness_index"] < fairway[480]["grass_firmness_index"]
    assert fairway[810]["moisture_index"] > fairway[480]["moisture_index"]
    assert fairway[1050]["moisture_index"] < fairway[810]["moisture_index"]
    bunker = samples["cp-h01-bunker"]
    assert bunker[510]["sand_workability_index"] > bunker[750]["sand_workability_index"]
    assert any(window["end_minute"] > 780 for windows in one_day["session_inputs"]["collection_blocks"].values() for window in windows)


def test_defects_and_declared_slots_are_not_observations_or_completed_jobs(two_days):
    assert {row["condition"] for row in two_days["course_events"]} == {"DIVOT", "BUNKER_SURFACE", "STANDING_WATER", "DEBRIS"}
    assert len({row["event_id"] for row in two_days["course_events"]}) == len(two_days["course_events"])
    for event in two_days["course_events"]:
        assert OPEN_MINUTE <= event["start_minute"] < event["end_minute"] <= 1440 + CLOSE_MINUTE
        assert event["radius_m"] > 0
        if event["condition"] == "DIVOT":
            assert event["radius_m"] <= 0.13 and event["subtype"] == "single_divot"
        assert not {"observed", "admitted", "completed", "repair_verified"} & set(event)
    slots = two_days["session_inputs"]["staff_job_slots"]
    assert len(slots) == 2 * 54 * len(TASK_DURATIONS)
    assert len({row["job_id"] for row in slots}) == len(slots)
    for slot in slots:
        assert set(slot) == {"job_id", "hole_number", "checkpoint_id", "duration_minutes", "task_kind"}
        assert slot["duration_minutes"] == TASK_DURATIONS[slot["task_kind"]]
        assert slot["job_id"].startswith(("d0-", "d1-"))
    assert not {"observations", "staff_jobs", "completed_jobs"} & set(two_days)


def test_resource_comparisons_preserve_external_inputs_and_return_detached_data(one_day):
    other = compile_session({"seed": 42, "days": 1, "staff_count": 1, "initial_stock": 1000})
    for key in ("weather", "traffic", "shots", "surface_snapshots", "course_events", "cart_routes", "camera_schedule"):
        assert other[key] == one_day[key]
    assert other["scenario"].human_ops.staff_count == 1
    assert other["scenario"].initial_dispenser_frac == 0.125
    other["catalog"][0]["id"] = "mutated"
    other["assumptions"]["club_profiles"][0]["weight"] = 99
    assert compile_session({"seed": 42, "days": 1}) == one_day
    assert compile_session({"seed": 43, "days": 1})["shots"] != one_day["shots"]


def test_forecast_hides_exact_surges_and_visitor_requests(one_day):
    scenario = one_day["scenario"]
    assert not scenario.demand.spikes
    assert len(scenario.demand.windows) == 4
    assert all(not zone.closure_windows for zone in scenario.zones)
    assert not {"traffic", "shots", "weather", "course_events", "camera_schedule"} & set(scenario.model_dump())
    assert len(set(one_day["session_inputs"]["demand_by_minute"])) > 10


def test_configurable_distance_factors_are_assumptions_not_carry_statistics(one_day):
    dry = compile_session({"seed": 5, "days": 1, "weather_regimes": ["dry"]})
    full = compile_session({"seed": 5, "days": 1, "weather_regimes": ["dry"],
                            "assumptions": {"range_ball_distance_multiplier": 1.0}})
    dry_shots = [shot for row in dry["shots"] for shot in row]
    full_shots = [shot for row in full["shots"] for shot in row]
    assert len(dry_shots) == len(full_shots)
    for reduced, normal in zip(dry_shots, full_shots):
        assert reduced["carry_m"] == pytest.approx(normal["carry_m"] * 0.92, abs=0.0001)
        assert reduced["roll_m"] == pytest.approx(normal["roll_m"] * 0.92, abs=0.0001)
    ref = one_day["assumptions"]["driver_reference"]
    assert ref["total_distance_yd"] == 225
    assert "not carry" in ref["scope"] and ref["source_url"].startswith("https://www.arccosgolf.com/")
    profiles = deepcopy(one_day["assumptions"]["club_profiles"])
    for profile in profiles:
        profile["weight"] = float(profile["id"] == "WEDGE")
    only_wedges = compile_session({"seed": 5, "days": 1, "assumptions": {"club_profiles": profiles}})
    assert {shot["club"] for row in only_wedges["shots"] for shot in row} == {"WEDGE"}


def test_zero_traffic_and_camera_outages_are_preserved():
    compiled = compile_session({"days": 1, "demand_scale": 0,
                                "assumptions": {"camera_outage_probability_per_day": 1.0}})
    assert sum(compiled["session_inputs"]["demand_by_minute"]) == 0
    assert all(row["active_visitors"] == 0 for row in compiled["traffic"])
    assert len([row for row in compiled["operational_events"] if row["kind"] == "CAMERA_OFFLINE"]) == 16
    assert any(not row["camera_online"] for row in compiled["camera_schedule"])


@pytest.mark.parametrize("config", [
    [], True, {"unknown": 1}, {"days": 0}, {"days": 8}, {"days": True},
    {"seed": -1}, {"seed": 2**32}, {"seed": False}, {"staff_count": 0},
    {"initial_stock": 8001}, {"initial_stock": 4.5}, {"demand_scale": -1},
    {"demand_scale": float("nan")}, {"demand_scale": float("inf")}, {"demand_scale": 10**400},
    {"weather_regimes": ["unknown"]}, {"days": 2, "weather_regimes": ["dry"]},
    {"assumptions": {"secret": 1}}, {"assumptions": {"rain_intensity_mm_h": True}},
    {"assumptions": {"camera_interval_minutes": 0}}, {"assumptions": {"camera_height_m": float("nan")}},
    {"assumptions": {"camera_blur_probability": 0.7, "camera_occlusion_probability": 0.4}},
    {"assumptions": {"club_profiles": []}},
])
def test_invalid_config_fails_closed(config):
    with pytest.raises(ValueError):
        compile_session(config)


def test_compile_budget_refuses_unbounded_population():
    with pytest.raises(ValueError, match="compile budget exceeded"):
        compile_session({"days": 1, "demand_scale": 5, "weather_regimes": ["dry"],
                         "assumptions": {"arrival_rate_per_minute": 2, "stay_mean_minutes": 120}})
