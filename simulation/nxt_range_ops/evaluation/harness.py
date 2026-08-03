"""Evaluation harness: compare policies over fixed and randomized seeds.

Reports, per (policy, scenario): stockout minutes, service availability,
human interventions, balls processed, energy, empty travel, robot
utilization, handoff queue time, safe-recovery rate, and an estimated
operating cost — all computed from placeholder-provenance parameters, so the
report carries an explicit disclaimer and a placeholder census.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

from nxt_range_ops import SIMULATOR_VERSION
from nxt_range_ops.config.models import RangeOpsScenario, placeholder_census
from nxt_range_ops.core.metrics import OpsMetrics
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.policies.base import BaselinePolicy
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.recording.episode_logger import EpisodeLogger, current_git_commit
from nxt_range_ops.scenarios.generators import make_scenario

DISCLAIMER = (
    "All results are produced from placeholder-provenance parameters. They "
    "exercise the decision problem and the pipeline only and must NOT be "
    "presented as real facility performance."
)


@dataclass
class EpisodeResult:
    scenario: str
    policy: str
    seed: int
    n_steps: int
    reward_total: float
    reward_components: dict[str, float]
    termination_reason: Optional[str]
    metrics: OpsMetrics
    kpis: dict[str, float] = field(default_factory=dict)


def episode_kpis(scenario: RangeOpsScenario, metrics: OpsMetrics) -> dict[str, float]:
    day_h = (scenario.hours.close_minute - scenario.hours.open_minute) / 60.0
    n_robots = len(scenario.robot_ids)
    robot_seconds = max(1.0, day_h * 3600.0 * n_robots)
    energy_kwh = metrics.energy_wh / 1000.0
    cost = (
        energy_kwh * scenario.economics.energy_cost_per_kwh.value
        + metrics.human_interventions_requested
        * scenario.economics.cost_per_human_intervention.value
        + day_h * n_robots * scenario.economics.robot_hour_cost.value
    )
    return {
        "stockout_minutes": metrics.stockout_minutes,
        "service_availability": metrics.service_availability,
        "human_interventions": float(metrics.human_interventions_requested),
        "balls_processed": float(metrics.balls_processed),
        "energy_wh": metrics.energy_wh,
        "empty_travel_s": metrics.empty_travel_s,
        "robot_utilization": metrics.active_robot_s / robot_seconds,
        "handoff_queue_s": metrics.handoff_queue_s,
        "safe_recovery_rate": metrics.safe_recovery_rate,
        "estimated_operating_cost": cost,
    }


def run_episode(
    env: RangeOpsEnv,
    policy: BaselinePolicy,
    seed: int,
    logger: Optional[EpisodeLogger] = None,
) -> EpisodeResult:
    obs, info = env.reset(seed=seed)
    policy.reset()
    reward_total = 0.0
    components_sum: dict[str, float] = {}
    termination_reason: Optional[str] = None
    step = 0
    while True:
        mask = info["action_mask"]
        action = policy.act(obs, info)
        next_obs, reward, terminated, truncated, next_info = env.step(action)
        reward_total += float(reward)
        for key, value in next_info["reward_components"].items():
            components_sum[key] = components_sum.get(key, 0.0) + float(value)
        if logger is not None:
            logger.log_step(
                step=step,
                t_s=info["t_s"],
                observation=obs,
                action_mask=mask,
                action=action,
                action_name=next_info["action_name"],
                task_result={
                    "shield": next_info["shield"],
                    "action_name": next_info["action_name"],
                },
                reward_total=float(reward),
                reward_components=next_info["reward_components"],
                next_state={
                    "t_s": next_info["t_s"],
                    "true_dispenser_count": next_info["true_dispenser_count"],
                    "robots": next_info["robots"],
                    "zones": next_info["zones"],
                    "stations": next_info["stations"],
                },
                terminated=terminated,
                truncated=truncated,
                termination_reason=next_info["termination_reason"],
            )
        obs, info = next_obs, next_info
        step += 1
        if terminated or truncated:
            termination_reason = next_info["termination_reason"]
            break
    metrics = env.sim.metrics.copy()
    result = EpisodeResult(
        scenario=env.scenario.name,
        policy=policy.name,
        seed=seed,
        n_steps=step,
        reward_total=reward_total,
        reward_components=components_sum,
        termination_reason=termination_reason,
        metrics=metrics,
        kpis=episode_kpis(env.scenario, metrics),
    )
    if logger is not None:
        logger.finalize(metrics.to_dict(), termination_reason)
    return result


def evaluate_policies(
    policy_names: Iterable[str],
    scenario_names: Iterable[str],
    fixed_seeds: Iterable[int] = (101, 202, 303),
    n_random_seeds: int = 2,
    seed_pool_seed: int = 7,
    out_dir: Optional[str | Path] = None,
    log_episodes: bool = False,
) -> dict[str, Any]:
    """Run every policy on every scenario over fixed + randomized seeds."""
    rng = np.random.default_rng(seed_pool_seed)
    random_seeds = [int(s) for s in rng.integers(0, 2**31 - 1, size=n_random_seeds)]
    seeds = list(fixed_seeds) + random_seeds

    results: list[EpisodeResult] = []
    for scenario_name in scenario_names:
        scenario = make_scenario(scenario_name)
        env = RangeOpsEnv(scenario)
        for policy_name in policy_names:
            for seed in seeds:
                policy = make_baseline(policy_name, scenario, env.catalog, seed=seed)
                logger = None
                if log_episodes and out_dir is not None:
                    logger = EpisodeLogger(
                        Path(out_dir) / "episodes",
                        episode_id=f"{scenario_name}-{policy_name}-seed{seed}",
                        scenario_name=scenario_name,
                        policy_name=policy_name,
                        policy_version=policy.version,
                        seed=seed,
                    )
                results.append(run_episode(env, policy, seed=seed, logger=logger))

    report = _aggregate(results, seeds)
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "evaluation_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True)
        )
        (out / "evaluation_report.md").write_text(render_markdown(report))
    return report


def _aggregate(results: list[EpisodeResult], seeds: list[int]) -> dict[str, Any]:
    by_pair: dict[tuple[str, str], list[EpisodeResult]] = {}
    for r in results:
        by_pair.setdefault((r.scenario, r.policy), []).append(r)

    comparisons: list[dict[str, Any]] = []
    for (scenario, policy), rs in sorted(by_pair.items()):
        kpi_stats: dict[str, dict[str, float]] = {}
        for key in rs[0].kpis:
            values = np.array([r.kpis[key] for r in rs], dtype=float)
            kpi_stats[key] = {
                "mean": float(values.mean()),
                "std": float(values.std()),
                "min": float(values.min()),
                "max": float(values.max()),
            }
        comparisons.append(
            {
                "scenario": scenario,
                "policy": policy,
                "episodes": len(rs),
                "mean_reward": float(np.mean([r.reward_total for r in rs])),
                "kpis": kpi_stats,
            }
        )

    census_params: set[str] = set()
    for scenario_name in dict.fromkeys(r.scenario for r in results):
        census_params.update(
            f"{scenario_name}:{p}"
            for p in placeholder_census(make_scenario(scenario_name))
        )
    return {
        "disclaimer": DISCLAIMER,
        "simulator_version": SIMULATOR_VERSION,
        "git_commit": current_git_commit(),
        "seeds": seeds,
        "placeholder_parameter_count": len(census_params),
        "comparisons": comparisons,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Range Ops policy evaluation",
        "",
        f"> {report['disclaimer']}",
        "",
        f"Simulator {report['simulator_version']} @ {report['git_commit']} — "
        f"seeds {report['seeds']} — "
        f"{report['placeholder_parameter_count']} placeholder parameters in use.",
        "",
        "| scenario | policy | reward | stockout min | availability | "
        "interventions | balls processed | energy Wh | utilization | est. cost |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["comparisons"]:
        k = row["kpis"]
        lines.append(
            f"| {row['scenario']} | {row['policy']} | {row['mean_reward']:.1f} "
            f"| {k['stockout_minutes']['mean']:.1f} "
            f"| {k['service_availability']['mean']:.3f} "
            f"| {k['human_interventions']['mean']:.1f} "
            f"| {k['balls_processed']['mean']:.0f} "
            f"| {k['energy_wh']['mean']:.0f} "
            f"| {k['robot_utilization']['mean']:.2f} "
            f"| {k['estimated_operating_cost']['mean']:.2f} |"
        )
    lines.append("")
    return "\n".join(lines)
