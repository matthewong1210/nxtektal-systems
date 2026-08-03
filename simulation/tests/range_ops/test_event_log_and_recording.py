"""Event-log reproducibility and the Parquet/JSON recording pipeline."""

from __future__ import annotations

import json

from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.evaluation.harness import run_episode
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.recording.episode_logger import EpisodeLogger
from nxt_range_ops.scenarios.generators import make_scenario


def _record(tmp_path, episode_id: str, seed: int):
    scenario = make_scenario("noisy_inventory_sensor")
    env = RangeOpsEnv(scenario)
    policy = make_baseline("inventory_threshold", scenario, env.catalog, seed=seed)
    logger = EpisodeLogger(
        tmp_path,
        episode_id=episode_id,
        scenario_name=scenario.name,
        policy_name=policy.name,
        policy_version=policy.version,
        seed=seed,
    )
    result = run_episode(env, policy, seed=seed, logger=logger)
    return env, logger, result


def test_decision_log_rows_carry_required_fields(tmp_path):
    env, logger, result = _record(tmp_path, "ep-fields", seed=41)
    assert logger.n_rows == result.n_steps > 0
    row = logger.rows()[0]
    for field in (
        "episode_id",
        "simulator_version",
        "policy_name",
        "policy_version",
        "git_commit",
        "seed",
        "t_s",
        "observation_json",
        "action_mask_json",
        "action",
        "action_name",
        "reward_total",
        "reward_components_json",
        "task_result_json",
        "next_state_json",
        "termination_reason",
    ):
        assert field in row, field
    obs = json.loads(row["observation_json"])
    assert "zone_balls" in obs and "robot_battery" in obs
    mask = json.loads(row["action_mask_json"])
    assert set(mask) <= {0, 1}
    components = json.loads(row["reward_components_json"])
    assert "stockout_duration" in components


def test_parquet_dataset_and_json_summary_written(tmp_path):
    env, logger, result = _record(tmp_path, "ep-files", seed=43)
    dataset = tmp_path / "ep-files.parquet"
    summary = tmp_path / "ep-files.summary.json"
    assert dataset.exists() and summary.exists()

    import pyarrow.parquet as pq

    table = pq.read_table(dataset)
    assert table.num_rows == result.n_steps
    assert "observation_json" in table.column_names

    payload = json.loads(summary.read_text())
    assert payload["seed"] == 43
    assert payload["termination_reason"] == "day_complete"
    assert "does NOT represent" in payload["disclaimer"]
    assert payload["final_metrics"]["balls_processed"] >= 0


def test_recorded_rows_are_reproducible_from_seed(tmp_path):
    _, logger_a, _ = _record(tmp_path / "a", "ep-repro", seed=47)
    _, logger_b, _ = _record(tmp_path / "b", "ep-repro", seed=47)
    assert logger_a.rows() == logger_b.rows()


def test_event_log_reproducible_and_ordered():
    scenario = make_scenario("robot_failure")
    env_a, env_b = RangeOpsEnv(scenario), RangeOpsEnv(scenario)
    from tests.range_ops.conftest import run_policy_episode

    run_policy_episode(env_a, "nearest_available_robot", seed=53)
    run_policy_episode(env_b, "nearest_available_robot", seed=53)
    log_a, log_b = env_a.sim.events.to_dicts(), env_b.sim.events.to_dicts()
    assert log_a == log_b
    times = [e["t_s"] for e in log_a]
    assert times == sorted(times)
    kinds = {e["kind"] for e in log_a}
    assert "episode_start" in kinds and "facility_closed" in kinds
