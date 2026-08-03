"""Run the benchmark grid: every policy on every scenario over paired seeds.

Reuses ``nxt_range_ops.evaluation.harness.run_episode`` unchanged, so a
benchmark episode is byte-for-byte the same episode the Phase 0.5 harness
runs. Factories are injectable for testing (e.g. shortened operating days);
defaults are the real registries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from nxt_range_agent import AGENT_VERSION
from nxt_range_agent.benchmark.config import BenchmarkConfig, resolve_seeds
from nxt_range_ops import SIMULATOR_VERSION
from nxt_range_ops.config.models import RangeOpsScenario, placeholder_census
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.evaluation.harness import DISCLAIMER, run_episode
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.recording.episode_logger import current_git_commit
from nxt_range_ops.scenarios.generators import make_scenario


@dataclass(frozen=True)
class EpisodeRecord:
    """One benchmark episode, flattened to what aggregation needs."""

    scenario: str
    policy: str
    seed: int
    n_steps: int
    termination_reason: Optional[str]
    reward_total: float
    reward_components: dict[str, float]
    kpis: dict[str, float]


@dataclass(frozen=True)
class BenchmarkResult:
    config: BenchmarkConfig
    seeds: list[int]
    records: list[EpisodeRecord]
    provenance: dict[str, Any]


def run_benchmark(
    config: BenchmarkConfig,
    scenario_factory: Callable[[str], RangeOpsScenario] = make_scenario,
    policy_factory: Callable[..., Any] = make_baseline,
    progress: Optional[Callable[[str], None]] = None,
) -> BenchmarkResult:
    """Run the full grid in deterministic order (scenario, policy, seed)."""
    seeds = resolve_seeds(config)
    records: list[EpisodeRecord] = []
    census_by_scenario: dict[str, int] = {}
    for scenario_name in config.scenarios:
        scenario = scenario_factory(scenario_name)
        census_by_scenario[scenario_name] = len(placeholder_census(scenario))
        env = RangeOpsEnv(scenario)
        for policy_name in config.policies:
            for seed in seeds:
                policy = policy_factory(policy_name, scenario, env.catalog, seed=seed)
                episode = run_episode(env, policy, seed=seed)
                records.append(
                    EpisodeRecord(
                        scenario=scenario_name,
                        policy=policy_name,
                        seed=seed,
                        n_steps=episode.n_steps,
                        termination_reason=episode.termination_reason,
                        reward_total=float(episode.reward_total),
                        reward_components={
                            k: float(v)
                            for k, v in sorted(episode.reward_components.items())
                        },
                        kpis={k: float(v) for k, v in sorted(episode.kpis.items())},
                    )
                )
                if progress is not None:
                    progress(
                        f"{scenario_name} / {policy_name} / seed {seed}: "
                        f"reward {episode.reward_total:.1f}, "
                        f"availability {episode.kpis['service_availability']:.3f}"
                    )
    provenance = {
        "simulator_version": SIMULATOR_VERSION,
        "agent_version": AGENT_VERSION,
        "git_commit": current_git_commit(),
        "disclaimer": DISCLAIMER,
        "placeholder_parameters_by_scenario": census_by_scenario,
        "placeholder_parameter_count": max(census_by_scenario.values()),
    }
    return BenchmarkResult(
        config=config, seeds=seeds, records=records, provenance=provenance
    )
