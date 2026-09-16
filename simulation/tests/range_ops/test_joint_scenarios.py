"""Joint-input compiler boundaries: reproducible exogenous inputs, no execution."""

from copy import deepcopy
from datetime import datetime
import json

import pytest

from nxt_range_ops.scenarios.generators import make_scenario
from nxt_range_ops.core.joint_inputs import validate_joint_inputs
from nxt_telemetry.course_condition import validate_observation
from scripts.joint_scenarios import REGIMES, build_joint_episode


@pytest.mark.parametrize("regime", REGIMES)
def test_episode_contract_is_finite_synthetic_and_core_compatible(regime):
    episode = build_joint_episode(3, regime)
    assert set(episode) == {"schema", "environment", "seed", "regime", "scenario", "joint_inputs", "course_frames", "metadata"}
    assert episode["schema"] == "nxt-joint-episode/v0"
    assert episode["environment"] == "SIMULATION"
    scenario, inputs = episode["scenario"], episode["joint_inputs"]
    assert (scenario.hours.open_minute, scenario.hours.close_minute) == (480, 960)
    assert set(inputs) == {"schema", "environment", "demand_by_minute", "collection_blocks", "staff_jobs"}
    assert inputs["schema"] == "nxt-joint-scenario-inputs/v0"
    assert len(inputs["demand_by_minute"]) == 480
    assert all(type(n) is int and n >= 0 for n in inputs["demand_by_minute"])
    assert set(inputs["collection_blocks"]) == {zone.zone_id for zone in scenario.zones}
    for windows in inputs["collection_blocks"].values():
        assert all(480 <= w["start_minute"] < w["end_minute"] <= 960 for w in windows)
        assert all(a["end_minute"] < b["start_minute"] for a, b in zip(windows, windows[1:]))
    assert all(not zone.closure_windows for zone in scenario.zones), "Collection restrictions must not close customer play"
    admitted = validate_joint_inputs(inputs, zone_ids=scenario.zone_ids,
                                     open_minute=480, close_minute=960)
    assert admitted["demand_by_minute"] == inputs["demand_by_minute"]
    assert admitted["collection_blocks"] == inputs["collection_blocks"]
    jobs = inputs["staff_jobs"]
    assert len({job["job_id"] for job in jobs}) == len(jobs)
    for job in jobs:
        assert set(job) == {"job_id", "available_minute", "duration_minutes", "deadline_minute", "hole_number", "checkpoint_id"}
        assert 480 <= job["available_minute"] < 960
        assert job["available_minute"] <= job["deadline_minute"] <= 960
        assert job["duration_minutes"] > 0
        assert 1 <= job["hole_number"] <= 18
    assert episode["metadata"]["source_kind"] == "SYNTHETIC_ASSUMPTION"
    assert len(episode["metadata"]["episode_digest"]) == 64
    # The boundary consists of plain portable JSON except the requested validated scenario object.
    json.dumps({**episode, "scenario": scenario.model_dump(mode="json")}, allow_nan=False)


def test_deterministic_detached_results_and_resource_changes_do_not_change_demand():
    first = build_joint_episode(17, "rain_then_surge")
    repeated = build_joint_episode(17, "rain_then_surge")
    assert first == repeated
    original = deepcopy(repeated)
    first["joint_inputs"]["demand_by_minute"][0] = -1
    first["metadata"]["assumptions"]["staff_count"] = 99
    assert build_joint_episode(17, "rain_then_surge") == original
    adjusted = build_joint_episode(17, "rain_then_surge", {
        "staff_count": 1, "initial_stock_balls": 1000, "robot_failure_rate_per_hour": 0.5,
    })
    assert adjusted["joint_inputs"]["demand_by_minute"] == original["joint_inputs"]["demand_by_minute"]
    assert adjusted["metadata"]["demand_digest"] == original["metadata"]["demand_digest"]
    assert adjusted["metadata"]["episode_digest"] != original["metadata"]["episode_digest"]
    assert build_joint_episode(18, "rain_then_surge")["metadata"]["demand_digest"] != original["metadata"]["demand_digest"]


def test_rain_base_demand_is_forecast_but_surprise_is_only_a_spike():
    dry = build_joint_episode(5, "dry")["scenario"]
    rain = build_joint_episode(5, "morning_rain")["scenario"]
    surge = build_joint_episode(5, "surprise_surge")["scenario"]
    combined = build_joint_episode(5, "rain_then_surge")["scenario"]
    for minute in range(480, 960):
        assert rain.demand.base_rate_at(minute) == pytest.approx(
            dry.demand.base_rate_at(minute) * (0.45 if minute < 720 else 1))
        assert surge.demand.base_rate_at(minute) == dry.demand.base_rate_at(minute)
        assert combined.demand.base_rate_at(minute) == rain.demand.base_rate_at(minute)
    assert not dry.demand.spikes and not rain.demand.spikes
    assert surge.demand.true_rate_at(800) == 2.5 * surge.demand.base_rate_at(800)
    windows = rain.demand.windows
    assert windows[0].window.start_minute == 480 and windows[-1].window.end_minute == 960
    assert all(a.window.end_minute == b.window.start_minute for a, b in zip(windows, windows[1:]))
    assert all(w.window.start_minute % 30 == w.window.end_minute % 30 == 0 for w in windows)


def test_wet_state_has_memory_and_sampling_does_not_make_unobserved_points_clear():
    episode = build_joint_episode(3, "morning_rain")
    frames = episode["course_frames"]
    assert [frame["minute"] for frame in frames] == list(range(480, 961, 5))
    assert not frames[-1]["observations"] and not frames[-1]["released_job_ids"]
    after_rain = next(frame for frame in frames if frame["minute"] == 750)
    assert after_rain["weather"]["rain_mm_per_hour"] == 0
    assert any(row["surface_water_mm"] > 0.5 for row in after_rain["soil"])
    assert any(w["end_minute"] > 720 for windows in episode["joint_inputs"]["collection_blocks"].values() for w in windows)
    assert any(w["end_minute"] < 960 for windows in episode["joint_inputs"]["collection_blocks"].values() for w in windows)
    assert all(len(frame["soil"]) == 54 for frame in frames)
    assert {row["hole_number"] for row in frames[0]["soil"]} == set(range(1, 19))
    observed = {obs["checkpoint_id"] for frame in frames for obs in frame["observations"]}
    omitted = set(episode["metadata"]["unobserved_checkpoint_ids"])
    assert len(omitted) == 6 and len(observed) == 48 and not observed & omitted
    assert any(row["condition"] == "STANDING_WATER" and row["checkpoint_id"] in omitted
               for frame in frames for row in frame["soil"])
    assert not omitted & {job["checkpoint_id"] for job in episode["joint_inputs"]["staff_jobs"]}


def test_every_inspection_has_prior_sample_evidence_and_valid_observation_identity():
    episode = build_joint_episode(3, "afternoon_rain")
    sources = {row["job_id"]: row for row in episode["metadata"]["job_sources"]}
    observations = {}
    for frame in episode["course_frames"]:
        for obs in frame["observations"]:
            assert validate_observation(obs) == obs
            assert obs["cart_position"] != obs["target_position"]
            assert obs["evidence_ref"].startswith("synthetic:")
            assert obs["site_id"] == episode["metadata"]["course_identity"]["site_id"]
            observations[obs["observation_id"]] = (frame["minute"], obs)
    assert {obs["quality"] for _, obs in observations.values()} == {"USABLE", "BLURRED", "OCCLUDED"}
    for job in episode["joint_inputs"]["staff_jobs"]:
        minute, obs = observations[sources[job["job_id"]]["observation_id"]]
        assert minute == job["available_minute"]
        assert obs["checkpoint_id"] == job["checkpoint_id"]
        captured = datetime.fromisoformat(obs["captured_at_utc"].replace("Z", "+00:00"))
        assert captured.hour * 60 + captured.minute == minute
        assert obs["quality"] != "USABLE" or obs["condition"] != "CLEAR"
        assert sources[job["job_id"]]["reason"] == (
            "REPHOTOGRAPH" if obs["quality"] != "USABLE" else "INSPECT_CANDIDATE")
    # Staffing never writes a 'repair succeeded' signal back to the exogenous truth.
    alternate = build_joint_episode(3, "afternoon_rain", {"staff_count": 6, "inspection_duration_minutes": 1})
    assert alternate["course_frames"] == episode["course_frames"]
    different_scene = build_joint_episode(3, "afternoon_rain", {"rain_intensity_mm_h": 4})
    assert different_scene["course_frames"][0]["observations"][0]["frame_id"] != episode["course_frames"][0]["observations"][0]["frame_id"]


def test_adjustments_remain_explicit_and_preserve_base_defaults():
    before = make_scenario("normal_weekday").model_dump()
    overrides = {"duration_minutes": 120, "open_minute": 600, "initial_stock_balls": 1234,
                 "demand_scale": 0, "staff_count": 3, "robot_failure_rate_per_hour": 0.2}
    episode = build_joint_episode(0, "dry", overrides)
    assert len(episode["joint_inputs"]["demand_by_minute"]) == 120
    assert sum(episode["joint_inputs"]["demand_by_minute"]) == 0
    assert episode["scenario"].initial_dispenser_frac * 8000 == pytest.approx(1234)
    assert episode["scenario"].human_ops.staff_count == 3
    assert all(robot.mean_time_between_failures_h.value == 5 for robot in episode["scenario"].robots)
    assert episode["metadata"]["overridden_assumptions"] == sorted(overrides)
    assert make_scenario("normal_weekday").model_dump() == before


@pytest.mark.parametrize("assumptions", [
    False, [], "", {"secret": 1}, {"staff_count": True}, {"staff_count": 0},
    {"initial_stock_balls": 8001}, {"initial_stock_balls": 1.5},
    {"rain_drop_fraction": float("nan")}, {"rain_intensity_mm_h": float("inf")},
    {"rain_intensity_mm_h": 10**400},
    {"rain_drop_fraction": True}, {"rain_drop_fraction": 1.1},
    {"robot_failure_rate_per_hour": 0}, {"duration_minutes": 0},
    {"duration_minutes": 90}, {"open_minute": 481}, {"open_minute": 1200},
    {"observation_blur_probability": 0.6, "observation_occlusion_probability": 0.5},
])
def test_invalid_manager_assumptions_fail_closed(assumptions):
    with pytest.raises(ValueError):
        build_joint_episode(1, "dry", assumptions)


@pytest.mark.parametrize("seed,regime", [(True, "dry"), (-1, "dry"), (2**32, "dry"), (1.0, "dry"), (1, "other"), (1, [])])
def test_invalid_identity_fails_closed(seed, regime):
    with pytest.raises(ValueError):
        build_joint_episode(seed, regime)
