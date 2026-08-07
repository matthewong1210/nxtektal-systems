"""Benchmark configuration: coverage of registries, KPI directions, seeds."""

from __future__ import annotations

from nxt_range_agent.benchmark.config import (
    BenchmarkConfig,
    KPI_DIRECTIONS,
    PRIMARY_KPIS,
    resolve_seeds,
)
from nxt_range_ops.core.metrics import OpsMetrics
from nxt_range_ops.env.actions import ActionCatalog
from nxt_range_ops.evaluation.harness import episode_kpis
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.scenarios.generators import SCENARIO_GENERATORS, make_scenario


def test_default_config_covers_all_scenarios_and_baselines():
    cfg = BenchmarkConfig()
    assert sorted(cfg.scenarios) == sorted(SCENARIO_GENERATORS)
    assert len(cfg.policies) == 4
    scenario = make_scenario("normal_weekday")
    catalog = ActionCatalog(scenario)
    for name in cfg.policies:
        # Every default policy name must be constructible from the registry.
        make_baseline(name, scenario, catalog, seed=0)


def test_kpi_directions_cover_every_reported_metric():
    kpis = episode_kpis(make_scenario("normal_weekday"), OpsMetrics())
    for key in kpis:
        assert key in KPI_DIRECTIONS, f"episode KPI {key!r} has no direction"
    assert "reward_total" in KPI_DIRECTIONS
    assert set(PRIMARY_KPIS) <= set(KPI_DIRECTIONS)
    assert all(d in (-1, 1) for d in KPI_DIRECTIONS.values())


def test_direction_signs_are_sane():
    assert KPI_DIRECTIONS["service_availability"] == 1
    assert KPI_DIRECTIONS["stockout_minutes"] == -1
    assert KPI_DIRECTIONS["human_interventions"] == -1
    assert KPI_DIRECTIONS["estimated_operating_cost"] == -1
    assert KPI_DIRECTIONS["reward_total"] == 1


def test_resolve_seeds_deterministic_and_unique():
    cfg = BenchmarkConfig(fixed_seeds=(101, 202, 303), n_random_seeds=7, seed_pool_seed=7)
    first = resolve_seeds(cfg)
    second = resolve_seeds(cfg)
    assert first == second
    assert len(first) == 10
    assert len(set(first)) == 10
    assert first[:3] == [101, 202, 303]


def test_resolve_seeds_varies_with_pool_seed():
    base = BenchmarkConfig(fixed_seeds=(101,), n_random_seeds=5, seed_pool_seed=7)
    other = BenchmarkConfig(fixed_seeds=(101,), n_random_seeds=5, seed_pool_seed=8)
    assert resolve_seeds(base) != resolve_seeds(other)


def test_resolve_seeds_rejects_duplicate_fixed_seeds():
    import pytest

    cfg = BenchmarkConfig(fixed_seeds=(101, 101), n_random_seeds=0)
    with pytest.raises(ValueError, match="duplicate"):
        resolve_seeds(cfg)
