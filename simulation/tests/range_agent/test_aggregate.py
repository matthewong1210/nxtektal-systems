"""Aggregation: summaries, direction-aware ranking, bootstrap, failure flags."""

from __future__ import annotations

import math

import pytest

from nxt_range_agent.benchmark.aggregate import (
    build_report,
    identify_failures,
    paired_bootstrap_diff,
    rank_policies,
    summarize,
)
from nxt_range_agent.benchmark.config import (
    BenchmarkConfig,
    FailureThresholds,
    KPI_DIRECTIONS,
)
from nxt_range_agent.benchmark.runner import BenchmarkResult, EpisodeRecord


def rec(scenario: str, policy: str, seed: int, reward: float, **kpis) -> EpisodeRecord:
    base = {name: 0.0 for name in KPI_DIRECTIONS if name != "reward_total"}
    base["service_availability"] = 1.0
    base["safe_recovery_rate"] = 1.0
    base.update(kpis)
    return EpisodeRecord(
        scenario=scenario,
        policy=policy,
        seed=seed,
        n_steps=40,
        termination_reason="day_complete",
        reward_total=reward,
        reward_components={"service_availability": reward, "stockout_duration": 0.0},
        kpis=base,
    )


def two_policy_records() -> list[EpisodeRecord]:
    """'good' strictly dominates 'bad' on every primary KPI in both scenarios."""
    records = []
    for scenario in ("s1", "s2"):
        for seed in (1, 2, 3):
            records.append(
                rec(
                    scenario,
                    "good",
                    seed,
                    reward=100.0 + seed,
                    service_availability=0.99,
                    stockout_minutes=1.0,
                    human_interventions=0.0,
                    estimated_operating_cost=50.0,
                )
            )
            records.append(
                rec(
                    scenario,
                    "bad",
                    seed,
                    reward=-50.0 - seed,
                    service_availability=0.60,
                    stockout_minutes=200.0,
                    human_interventions=6.0,
                    estimated_operating_cost=90.0,
                )
            )
    return records


def test_summarize_stats_per_scenario_policy_metric():
    records = [
        rec("s1", "p", 1, reward=10.0, stockout_minutes=4.0),
        rec("s1", "p", 2, reward=20.0, stockout_minutes=8.0),
    ]
    rows = summarize(records)
    by_key = {(r["scenario"], r["policy"], r["metric"]): r for r in rows}
    stockout = by_key[("s1", "p", "stockout_minutes")]
    assert stockout["mean"] == pytest.approx(6.0)
    assert stockout["min"] == pytest.approx(4.0)
    assert stockout["max"] == pytest.approx(8.0)
    assert stockout["std"] == pytest.approx(2.0)
    assert stockout["n"] == 2
    reward = by_key[("s1", "p", "reward_total")]
    assert reward["mean"] == pytest.approx(15.0)


def test_rank_policies_is_direction_aware():
    rankings = rank_policies(two_policy_records())
    for per_scenario in rankings["per_scenario"]:
        assert per_scenario["winner"] == "good"
        ordered = [row["policy"] for row in per_scenario["ranking"]]
        assert ordered == ["good", "bad"]
    overall = rankings["overall"]
    assert overall[0]["policy"] == "good"
    assert overall[0]["rank"] == 1
    assert overall[0]["scenario_wins"] == 2
    assert overall[1]["policy"] == "bad"


def test_rank_policies_ties_share_average_rank():
    records = []
    for policy in ("a", "b"):
        for seed in (1, 2):
            records.append(
                rec(
                    "s1",
                    policy,
                    seed,
                    reward=10.0,
                    service_availability=0.9,
                    stockout_minutes=5.0,
                    human_interventions=1.0,
                    estimated_operating_cost=10.0,
                )
            )
    rankings = rank_policies(records)
    rows = rankings["per_scenario"][0]["ranking"]
    assert [row["composite_rank"] for row in rows] == pytest.approx([1.5, 1.5])


def test_incomplete_grid_raises_actionable_error():
    # 'bad' has records for s1 only: the (s2, bad) cell is missing.
    records = [
        rec("s1", "good", 1, reward=1.0),
        rec("s1", "bad", 1, reward=0.0),
        rec("s2", "good", 1, reward=1.0),
    ]
    with pytest.raises(ValueError, match=r"missing.*s2.*bad"):
        rank_policies(records)
    with pytest.raises(ValueError, match=r"missing.*s2.*bad"):
        identify_failures(records, FailureThresholds())


def test_paired_bootstrap_diff_deterministic_and_signed():
    a = [10.0, 12.0, 11.0, 13.0, 12.5]
    b = [1.0, 2.0, 1.5, 2.5, 2.0]
    first = paired_bootstrap_diff(a, b, n_resamples=500, seed=13)
    second = paired_bootstrap_diff(a, b, n_resamples=500, seed=13)
    assert first == second
    assert first["mean_diff"] == pytest.approx(9.9)
    assert first["ci_lo"] > 0.0
    assert first["ci_hi"] >= first["ci_lo"]


def test_identify_failures_flags_bad_cells_only():
    thresholds = FailureThresholds(
        availability_floor=0.95,
        max_stockout_minutes_worst_seed=60.0,
        max_mean_human_interventions=5.0,
    )
    failures = identify_failures(two_policy_records(), thresholds)
    flagged = {(f["policy"], f["scenario"]): f["flags"] for f in failures["cells"]}
    assert ("good", "s1") not in flagged
    assert ("good", "s2") not in flagged
    for scenario in ("s1", "s2"):
        flags = flagged[("bad", scenario)]
        assert "availability_below_floor" in flags
        assert "stockout_worst_seed_exceeded" in flags
        assert "human_interventions_heavy" in flags
    worst = {row["policy"]: row for row in failures["per_policy_worst"]}
    assert worst["bad"]["service_availability"] == pytest.approx(0.60)
    # Hardest scenarios rank by what the *best* policy achieves; both scenarios
    # are solvable by 'good', so neither is unsolvable.
    for row in failures["hardest_scenarios"]:
        assert row["best_availability"] == pytest.approx(0.99)


def test_build_report_assembles_all_sections():
    cfg = BenchmarkConfig(
        policies=("good", "bad"),
        scenarios=("s1", "s2"),
        fixed_seeds=(1, 2, 3),
        n_random_seeds=0,
    )
    result = BenchmarkResult(
        config=cfg,
        seeds=[1, 2, 3],
        records=two_policy_records(),
        provenance={
            "simulator_version": "0.5.0",
            "agent_version": "0.1.0",
            "git_commit": "abc1234",
            "disclaimer": "placeholder parameters",
            "placeholder_parameter_count": 30,
            "placeholder_parameters_by_scenario": {"s1": 30, "s2": 30},
        },
    )
    report = build_report(result)
    for key in (
        "disclaimer",
        "provenance",
        "config",
        "seeds",
        "summary",
        "rankings",
        "failures",
        "reward_vs_winner",
    ):
        assert key in report, f"report missing section {key!r}"
    # Winner comparisons: one row per (scenario, non-winning policy), with a CI
    # on the paired reward difference (winner minus policy, so positive = gap).
    rows = report["reward_vs_winner"]
    assert {(r["scenario"], r["policy"]) for r in rows} == {("s1", "bad"), ("s2", "bad")}
    assert all(r["ci_lo"] > 0 for r in rows)
    assert not math.isnan(rows[0]["mean_diff"])
