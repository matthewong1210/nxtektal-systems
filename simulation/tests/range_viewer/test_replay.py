"""Replay determinism and benchmark-fidelity tests.

The replay layer must reproduce a benchmark episode exactly — same policy
decisions, same simulator trajectory, same KPIs — because the demo viewer's
credibility rests on the animation matching the published numbers.
"""

from __future__ import annotations

import json

import pytest

from nxt_range_agent.benchmark.config import BenchmarkConfig
from nxt_range_agent.benchmark.runner import run_benchmark
from nxt_range_viewer.replay import DEMO_EVENT_KINDS, replay_episode

FRAME_KEYS = {
    "step",
    "t_s",
    "action",
    "reward",
    "robots",
    "zones",
    "stations",
    "inventory",
    "charger_queue",
    "kpi",
}

ROBOT_KEYS = {
    "robot_id",
    "activity",
    "health",
    "battery_frac",
    "payload_balls",
    "payload_capacity_balls",
    "location",
    "destination",
    "assigned_zone",
    "estop_latched",
    "awaiting_human",
}


def test_replay_returns_one_frame_per_step(short_scenario):
    result = replay_episode(short_scenario, "inventory_threshold", seed=101)
    assert result.n_steps > 0
    assert len(result.frames) == result.n_steps
    assert [f["step"] for f in result.frames] == list(range(1, result.n_steps + 1))
    assert result.termination_reason == "day_complete"


def test_same_seed_produces_identical_frames(short_scenario):
    a = replay_episode(short_scenario, "random_valid", seed=101)
    b = replay_episode(short_scenario, "random_valid", seed=101)
    assert a.frames == b.frames
    assert a.events == b.events
    assert a.kpis == b.kpis
    assert json.dumps(a.frames, sort_keys=True) == json.dumps(b.frames, sort_keys=True)


def test_different_seeds_diverge(short_scenario):
    a = replay_episode(short_scenario, "random_valid", seed=101)
    b = replay_episode(short_scenario, "random_valid", seed=202)
    assert a.frames != b.frames


# random_valid consumes the policy rng every step, so it also proves the
# replay seeds the policy exactly as the benchmark does — a deterministic
# policy alone would hide agent-seed miswiring.
@pytest.mark.parametrize("policy", ["inventory_threshold", "random_valid"])
def test_replay_kpis_match_benchmark(short_scenario_factory, policy):
    config = BenchmarkConfig(
        policies=(policy,),
        scenarios=("normal_weekday",),
        fixed_seeds=(101,),
        n_random_seeds=0,
    )
    benchmark = run_benchmark(config, scenario_factory=short_scenario_factory)
    record = benchmark.records[0]

    result = replay_episode(short_scenario_factory("normal_weekday"), policy, seed=101)
    assert result.kpis == record.kpis
    assert result.reward_total == record.reward_total
    assert result.n_steps == record.n_steps
    assert result.termination_reason == record.termination_reason


def test_frames_contain_required_fields_and_nothing_hidden(short_scenario):
    result = replay_episode(short_scenario, "inventory_threshold", seed=101)
    frame = result.frames[0]
    assert set(frame) == FRAME_KEYS
    assert set(frame["robots"][0]) == ROBOT_KEYS
    assert set(frame["zones"][0]) == {"zone_id", "balls", "is_open", "robots_present"}
    assert set(frame["stations"][0]) == {
        "station_id",
        "is_open",
        "docked",
        "queue_length",
        "buffer_balls",
        "buffer_capacity_balls",
    }
    assert set(frame["inventory"]) == {"dispenser_balls", "washer_wip_frac"}
    assert set(frame["action"]) == {"index", "name", "shield_allowed", "shield_reason"}
    assert set(frame["reward"]) == {"total", "components"}


def test_debug_state_only_when_requested(short_scenario):
    plain = replay_episode(short_scenario, "inventory_threshold", seed=101)
    assert all("debug" not in f for f in plain.frames)

    debug = replay_episode(short_scenario, "inventory_threshold", seed=101, include_debug=True)
    assert all("debug" in f for f in debug.frames)
    positions = debug.frames[0]["debug"]["robot_positions"]
    assert set(positions) == set(short_scenario.robot_ids)
    for pos in positions.values():
        assert set(pos) == {"x_m", "y_m"}
    # Debug capture must not perturb the trajectory.
    stripped = [{k: v for k, v in f.items() if k != "debug"} for f in debug.frames]
    assert stripped == plain.frames


def test_events_filtered_to_demo_kinds_by_default(short_scenario):
    result = replay_episode(short_scenario, "random_valid", seed=101)
    assert all(set(e) == {"t_s", "kind", "payload"} for e in result.events)
    assert all(e["kind"] in DEMO_EVENT_KINDS for e in result.events)

    everything = replay_episode(short_scenario, "random_valid", seed=101, event_kinds=None)
    assert len(everything.events) >= len(result.events)
    assert {e["kind"] for e in everything.events} - DEMO_EVENT_KINDS


def test_full_day_replay_has_960_frames():
    result = replay_episode("normal_weekday", "demand_forecast_dispatch", seed=101)
    assert result.n_steps == 960
    assert len(result.frames) == 960
    assert result.termination_reason == "day_complete"


def test_unknown_policy_rejected(short_scenario):
    with pytest.raises(KeyError):
        replay_episode(short_scenario, "no_such_policy", seed=101)
