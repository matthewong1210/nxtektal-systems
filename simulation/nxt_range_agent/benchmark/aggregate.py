"""Aggregation: KPI summaries, direction-aware rankings, failure detection.

Rankings use average-rank-on-ties over the primary KPIs, with mean episode
reward only as a tie-break — so a policy cannot win by reward alone while
losing on every operational KPI. The paired bootstrap compares policies on
the *same* seeds, which is what the paired seed design in the runner buys.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable, Sequence

import numpy as np

from nxt_range_agent.benchmark.config import (
    FailureThresholds,
    KPI_DIRECTIONS,
    PRIMARY_KPIS,
)
from nxt_range_agent.benchmark.runner import BenchmarkResult, EpisodeRecord


def _metric_value(record: EpisodeRecord, metric: str) -> float:
    if metric == "reward_total":
        return record.reward_total
    return record.kpis[metric]


def _ordered_unique(values: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for v in values:
        seen.setdefault(v)
    return list(seen)


def _group(records: list[EpisodeRecord]) -> dict[tuple[str, str], list[EpisodeRecord]]:
    groups: dict[tuple[str, str], list[EpisodeRecord]] = {}
    for r in records:
        groups.setdefault((r.scenario, r.policy), []).append(r)
    return groups


def _require_full_grid(
    groups: dict[tuple[str, str], list[EpisodeRecord]],
    scenarios: list[str],
    policies: list[str],
) -> None:
    missing = [(s, p) for s in scenarios for p in policies if (s, p) not in groups]
    if missing:
        raise ValueError(f"incomplete benchmark grid, missing cells: {missing}")


def _average_ranks(scores: dict[str, float]) -> dict[str, float]:
    """Rank 1 = best (highest score); exact ties share the average rank."""
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    ranks: dict[str, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j < len(ordered) and ordered[j][1] == ordered[i][1]:
            j += 1
        avg = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[ordered[k][0]] = avg
        i = j
    return ranks


def summarize(records: list[EpisodeRecord]) -> list[dict[str, Any]]:
    """mean/std/min/max per (scenario, policy, metric), population std."""
    rows: list[dict[str, Any]] = []
    groups = _group(records)
    for (scenario, policy), group in groups.items():
        for metric in KPI_DIRECTIONS:
            values = np.array([_metric_value(r, metric) for r in group], dtype=float)
            rows.append(
                {
                    "scenario": scenario,
                    "policy": policy,
                    "metric": metric,
                    "mean": float(values.mean()),
                    "std": float(values.std()),
                    "min": float(values.min()),
                    "max": float(values.max()),
                    "n": int(values.size),
                }
            )
    return rows


def rank_policies(
    records: list[EpisodeRecord], primary_kpis: Sequence[str] = PRIMARY_KPIS
) -> dict[str, Any]:
    """Per-scenario and overall rankings over the primary KPIs."""
    scenarios = _ordered_unique(r.scenario for r in records)
    policies = _ordered_unique(r.policy for r in records)
    groups = _group(records)
    _require_full_grid(groups, scenarios, policies)

    per_scenario: list[dict[str, Any]] = []
    composite_by_policy: dict[str, list[float]] = {p: [] for p in policies}
    reward_by_policy: dict[str, list[float]] = {p: [] for p in policies}
    wins: dict[str, int] = {p: 0 for p in policies}

    for scenario in scenarios:
        means = {
            policy: {
                metric: float(
                    np.mean(
                        [_metric_value(r, metric) for r in groups[(scenario, policy)]]
                    )
                )
                for metric in KPI_DIRECTIONS
            }
            for policy in policies
        }
        kpi_ranks: dict[str, dict[str, float]] = {}
        for metric in primary_kpis:
            direction = KPI_DIRECTIONS[metric]
            kpi_ranks[metric] = _average_ranks(
                {p: direction * means[p][metric] for p in policies}
            )
        composite = {
            p: float(np.mean([kpi_ranks[m][p] for m in primary_kpis])) for p in policies
        }
        ranking = sorted(
            (
                {
                    "policy": p,
                    "composite_rank": composite[p],
                    "reward_mean": means[p]["reward_total"],
                    "kpi_ranks": {m: kpi_ranks[m][p] for m in primary_kpis},
                    "kpi_means": {m: means[p][m] for m in primary_kpis},
                }
                for p in policies
            ),
            key=lambda row: (row["composite_rank"], -row["reward_mean"], row["policy"]),
        )
        winner = ranking[0]["policy"]
        wins[winner] += 1
        per_scenario.append(
            {"scenario": scenario, "winner": winner, "ranking": ranking}
        )
        for p in policies:
            composite_by_policy[p].append(composite[p])
            reward_by_policy[p].append(means[p]["reward_total"])

    overall_rows = sorted(
        (
            {
                "policy": p,
                "mean_composite_rank": float(np.mean(composite_by_policy[p])),
                "scenario_wins": wins[p],
                "mean_reward": float(np.mean(reward_by_policy[p])),
            }
            for p in policies
        ),
        key=lambda row: (
            row["mean_composite_rank"],
            -row["scenario_wins"],
            -row["mean_reward"],
            row["policy"],
        ),
    )
    for i, row in enumerate(overall_rows):
        if i > 0 and (
            row["mean_composite_rank"]
            == overall_rows[i - 1]["mean_composite_rank"]
        ):
            row["rank"] = overall_rows[i - 1]["rank"]
        else:
            row["rank"] = i + 1

    return {"per_scenario": per_scenario, "overall": overall_rows}


def paired_bootstrap_diff(
    a: Sequence[float], b: Sequence[float], n_resamples: int = 2000, seed: int = 13
) -> dict[str, float]:
    """95% bootstrap CI of mean(a - b) over paired samples. Deterministic."""
    a_arr = np.asarray(a, dtype=float)
    b_arr = np.asarray(b, dtype=float)
    if a_arr.shape != b_arr.shape or a_arr.size == 0:
        raise ValueError("paired samples must be same-length and non-empty")
    diffs = a_arr - b_arr
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, diffs.size, size=(int(n_resamples), diffs.size))
    resampled_means = diffs[idx].mean(axis=1)
    return {
        "mean_diff": float(diffs.mean()),
        "ci_lo": float(np.percentile(resampled_means, 2.5)),
        "ci_hi": float(np.percentile(resampled_means, 97.5)),
        "n": int(diffs.size),
        "n_resamples": int(n_resamples),
    }


def identify_failures(
    records: list[EpisodeRecord], thresholds: FailureThresholds = FailureThresholds()
) -> dict[str, Any]:
    """Flag failing (policy, scenario) cells and locate the hard scenarios."""
    groups = _group(records)
    scenarios = _ordered_unique(r.scenario for r in records)
    policies = _ordered_unique(r.policy for r in records)
    _require_full_grid(groups, scenarios, policies)

    cells: list[dict[str, Any]] = []
    mean_avail: dict[tuple[str, str], float] = {}
    for (scenario, policy), group in groups.items():
        avail = float(np.mean([r.kpis["service_availability"] for r in group]))
        mean_avail[(scenario, policy)] = avail
        worst_stockout = float(np.max([r.kpis["stockout_minutes"] for r in group]))
        interventions = float(np.mean([r.kpis["human_interventions"] for r in group]))
        flags: list[str] = []
        if avail < thresholds.availability_floor:
            flags.append("availability_below_floor")
        if worst_stockout > thresholds.max_stockout_minutes_worst_seed:
            flags.append("stockout_worst_seed_exceeded")
        if interventions > thresholds.max_mean_human_interventions:
            flags.append("human_interventions_heavy")
        if flags:
            cells.append(
                {
                    "policy": policy,
                    "scenario": scenario,
                    "flags": flags,
                    "mean_service_availability": avail,
                    "worst_seed_stockout_minutes": worst_stockout,
                    "mean_human_interventions": interventions,
                }
            )

    per_policy_worst = []
    for policy in policies:
        scenario = min(scenarios, key=lambda s: (mean_avail[(s, policy)], s))
        per_policy_worst.append(
            {
                "policy": policy,
                "scenario": scenario,
                "service_availability": mean_avail[(scenario, policy)],
            }
        )

    hardest = []
    for scenario in scenarios:
        best_policy = max(policies, key=lambda p: (mean_avail[(scenario, p)], p))
        hardest.append(
            {
                "scenario": scenario,
                "best_policy": best_policy,
                "best_availability": mean_avail[(scenario, best_policy)],
            }
        )
    hardest.sort(key=lambda row: (row["best_availability"], row["scenario"]))

    return {
        "cells": cells,
        "per_policy_worst": per_policy_worst,
        "hardest_scenarios": hardest,
    }


def build_report(result: BenchmarkResult) -> dict[str, Any]:
    """Assemble the complete benchmark report (JSON-serializable)."""
    records = result.records
    rankings = rank_policies(records)
    groups = _group(records)
    seed_order = {seed: i for i, seed in enumerate(result.seeds)}

    def rewards_in_seed_order(scenario: str, policy: str) -> list[float]:
        group = sorted(groups[(scenario, policy)], key=lambda r: seed_order[r.seed])
        return [r.reward_total for r in group]

    reward_vs_winner: list[dict[str, Any]] = []
    for entry in rankings["per_scenario"]:
        scenario, winner = entry["scenario"], entry["winner"]
        winner_rewards = rewards_in_seed_order(scenario, winner)
        for row in entry["ranking"]:
            policy = row["policy"]
            if policy == winner:
                continue
            stats = paired_bootstrap_diff(
                winner_rewards,
                rewards_in_seed_order(scenario, policy),
                n_resamples=result.config.bootstrap_resamples,
                seed=result.config.bootstrap_seed,
            )
            reward_vs_winner.append(
                {"scenario": scenario, "policy": policy, "winner": winner, **stats}
            )

    return {
        "disclaimer": result.provenance["disclaimer"],
        "provenance": result.provenance,
        "config": asdict(result.config),
        "seeds": result.seeds,
        "summary": summarize(records),
        "rankings": rankings,
        "failures": identify_failures(records, result.config.thresholds),
        "reward_vs_winner": reward_vs_winner,
    }
