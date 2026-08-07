"""Benchmark configuration: what to run, and how to judge it.

``KPI_DIRECTIONS`` is the single source of truth for which way each metric
points; every ranking and failure rule derives from it so a new KPI cannot
silently be ranked the wrong way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from nxt_range_ops.scenarios.generators import SCENARIO_GENERATORS

# The four baselines shipped with nxt_range_ops (validated against the
# make_baseline registry by the test suite).
BASELINE_POLICY_NAMES: tuple[str, ...] = (
    "random_valid",
    "inventory_threshold",
    "nearest_available_robot",
    "demand_forecast_dispatch",
)

# +1 = higher is better, -1 = lower is better. Covers every KPI reported by
# nxt_range_ops.evaluation.harness.episode_kpis plus the episode reward.
KPI_DIRECTIONS: dict[str, int] = {
    "service_availability": 1,
    "stockout_minutes": -1,
    "human_interventions": -1,
    "balls_processed": 1,
    "energy_wh": -1,
    "empty_travel_s": -1,
    "robot_utilization": 1,
    "handoff_queue_s": -1,
    "safe_recovery_rate": 1,
    "estimated_operating_cost": -1,
    "reward_total": 1,
}

# The KPIs that decide rankings; the rest are reported but not ranked on.
PRIMARY_KPIS: tuple[str, ...] = (
    "service_availability",
    "stockout_minutes",
    "human_interventions",
    "estimated_operating_cost",
)


@dataclass(frozen=True)
class FailureThresholds:
    """When a (policy, scenario) cell counts as a failure."""

    availability_floor: float = 0.95
    max_stockout_minutes_worst_seed: float = 60.0
    max_mean_human_interventions: float = 5.0


@dataclass(frozen=True)
class BenchmarkConfig:
    policies: tuple[str, ...] = BASELINE_POLICY_NAMES
    scenarios: tuple[str, ...] = tuple(sorted(SCENARIO_GENERATORS))
    fixed_seeds: tuple[int, ...] = (101, 202, 303)
    n_random_seeds: int = 7
    seed_pool_seed: int = 7
    bootstrap_resamples: int = 2000
    bootstrap_seed: int = 13
    thresholds: FailureThresholds = field(default_factory=FailureThresholds)


def resolve_seeds(config: BenchmarkConfig) -> list[int]:
    """Fixed seeds plus a deterministic, non-colliding random pool."""
    seeds = [int(s) for s in config.fixed_seeds]
    seen = set(seeds)
    if len(seen) != len(seeds):
        raise ValueError(
            "duplicate values in fixed_seeds would double-count episodes "
            "in the paired analysis"
        )
    rng = np.random.default_rng(config.seed_pool_seed)
    while len(seeds) < len(config.fixed_seeds) + config.n_random_seeds:
        candidate = int(rng.integers(0, 2**31 - 1))
        if candidate not in seen:
            seen.add(candidate)
            seeds.append(candidate)
    return seeds
