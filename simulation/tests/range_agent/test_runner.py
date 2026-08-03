"""Benchmark runner: paired seeds, determinism, record contents, provenance."""

from __future__ import annotations

from nxt_range_agent.benchmark.config import BenchmarkConfig
from nxt_range_agent.benchmark.runner import run_benchmark

TINY = dict(
    policies=("random_valid", "inventory_threshold"),
    scenarios=("normal_weekday", "wet_ground"),
    fixed_seeds=(11, 22),
    n_random_seeds=0,
)


def test_one_record_per_policy_scenario_seed(short_scenario_factory):
    cfg = BenchmarkConfig(**TINY)
    result = run_benchmark(cfg, scenario_factory=short_scenario_factory)
    assert len(result.records) == 2 * 2 * 2
    seeds_by_pair: dict[tuple[str, str], list[int]] = {}
    for rec in result.records:
        seeds_by_pair.setdefault((rec.scenario, rec.policy), []).append(rec.seed)
    # Paired design: every (scenario, policy) cell sees the identical seed list.
    assert all(seeds == [11, 22] for seeds in seeds_by_pair.values())


def test_run_benchmark_is_deterministic(short_scenario_factory):
    cfg = BenchmarkConfig(**TINY)
    first = run_benchmark(cfg, scenario_factory=short_scenario_factory)
    second = run_benchmark(cfg, scenario_factory=short_scenario_factory)
    assert [r.reward_total for r in first.records] == [
        r.reward_total for r in second.records
    ]
    assert [r.kpis for r in first.records] == [r.kpis for r in second.records]
    assert first.seeds == second.seeds


def test_records_carry_kpis_components_and_termination(short_scenario_factory):
    cfg = BenchmarkConfig(
        policies=("random_valid",),
        scenarios=("normal_weekday",),
        fixed_seeds=(11,),
        n_random_seeds=0,
    )
    result = run_benchmark(cfg, scenario_factory=short_scenario_factory)
    (rec,) = result.records
    assert rec.n_steps > 0
    assert rec.scenario == "normal_weekday"
    assert rec.policy == "random_valid"
    assert rec.seed == 11
    assert "service_availability" in rec.kpis
    assert "estimated_operating_cost" in rec.kpis
    assert "stockout_duration" in rec.reward_components
    assert rec.termination_reason in ("day_complete", "max_steps")


def test_provenance_recorded(short_scenario_factory):
    result = run_benchmark(
        BenchmarkConfig(**TINY), scenario_factory=short_scenario_factory
    )
    prov = result.provenance
    assert prov["simulator_version"]
    assert prov["agent_version"]
    assert prov["git_commit"]
    assert "placeholder" in prov["disclaimer"].lower()
    assert prov["placeholder_parameter_count"] > 0
    assert set(prov["placeholder_parameters_by_scenario"]) == set(TINY["scenarios"])
