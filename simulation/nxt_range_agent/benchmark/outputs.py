"""Output writers: JSON report, Parquet episodes, tidy CSVs, Markdown tables.

Everything written here is byte-reproducible from (config, code): no
wall-clock values, fixed row ordering, sorted JSON keys, "\n" line endings.
Parquet needs pyarrow (a declared range-ops extra); without it the episode
table falls back to JSON Lines, mirroring the Phase 0.5 episode logger.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from nxt_range_agent.benchmark.config import KPI_DIRECTIONS, PRIMARY_KPIS
from nxt_range_agent.benchmark.runner import BenchmarkResult, EpisodeRecord


def _episode_row(record: EpisodeRecord) -> dict[str, Any]:
    row: dict[str, Any] = {
        "scenario": record.scenario,
        "policy": record.policy,
        "seed": record.seed,
        "n_steps": record.n_steps,
        "termination_reason": record.termination_reason or "",
        "reward_total": record.reward_total,
    }
    for name, value in record.kpis.items():
        row[f"kpi_{name}"] = value
    for name, value in record.reward_components.items():
        row[f"reward_component_{name}"] = value
    return row


def _write_episode_table(records: list[EpisodeRecord], out: Path) -> tuple[str, Path]:
    rows = [_episode_row(r) for r in records]
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = out / "episodes.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path)
        return "episodes_parquet", path
    except ImportError:  # pragma: no cover - pyarrow is installed in this env
        path = out / "episodes.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        return "episodes_jsonl", path


def _write_kpi_long_csv(records: list[EpisodeRecord], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["scenario", "policy", "seed", "metric", "value"])
        for record in records:
            for metric in KPI_DIRECTIONS:
                value = (
                    record.reward_total
                    if metric == "reward_total"
                    else record.kpis[metric]
                )
                writer.writerow(
                    [record.scenario, record.policy, record.seed, metric, repr(value)]
                )


def _write_summary_csv(summary_rows: list[dict[str, Any]], path: Path) -> None:
    fields = ["scenario", "policy", "metric", "mean", "std", "min", "max", "n"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in summary_rows:
            writer.writerow({k: row[k] for k in fields})


def render_markdown(report: dict[str, Any]) -> str:
    prov = report["provenance"]
    lines = [
        "# Range Ops baseline benchmark (Experiment E1)",
        "",
        f"> {report['disclaimer']}",
        "",
        f"Simulator {prov['simulator_version']} / agent {prov['agent_version']} "
        f"@ {prov['git_commit']} — {len(report['seeds'])} paired seeds — "
        f"{prov['placeholder_parameter_count']} placeholder parameters in use.",
        "",
        "## Overall policy ranking",
        "",
        "Ranked by mean composite rank over the primary KPIs "
        f"({', '.join(PRIMARY_KPIS)}); reward is a tie-break only.",
        "",
        "| rank | policy | mean composite rank | scenario wins | mean reward |",
        "|---|---|---|---|---|",
    ]
    for row in report["rankings"]["overall"]:
        lines.append(
            f"| {row['rank']} | {row['policy']} | {row['mean_composite_rank']:.2f} "
            f"| {row['scenario_wins']} | {row['mean_reward']:.1f} |"
        )

    lines += [
        "",
        "## Per-scenario KPI comparison",
        "",
        "Mean over seeds; best composite rank listed first per scenario.",
        "",
        "| scenario | policy | availability | stockout min | interventions "
        "| est. cost | reward |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in report["rankings"]["per_scenario"]:
        for row in entry["ranking"]:
            k = row["kpi_means"]
            lines.append(
                f"| {entry['scenario']} | {row['policy']} "
                f"| {k['service_availability']:.3f} "
                f"| {k['stockout_minutes']:.1f} "
                f"| {k['human_interventions']:.1f} "
                f"| {k['estimated_operating_cost']:.2f} "
                f"| {row['reward_mean']:.1f} |"
            )

    lines += [
        "",
        "## Reward gap to scenario winner (paired bootstrap, 95% CI)",
        "",
        "| scenario | winner | policy | mean gap | CI low | CI high |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["reward_vs_winner"]:
        lines.append(
            f"| {row['scenario']} | {row['winner']} | {row['policy']} "
            f"| {row['mean_diff']:.1f} | {row['ci_lo']:.1f} | {row['ci_hi']:.1f} |"
        )

    failures = report["failures"]
    lines += ["", "## Failure scenarios", ""]
    if failures["cells"]:
        lines += [
            "| policy | scenario | flags | mean availability "
            "| worst-seed stockout min | mean interventions |",
            "|---|---|---|---|---|---|",
        ]
        for cell in failures["cells"]:
            lines.append(
                f"| {cell['policy']} | {cell['scenario']} "
                f"| {', '.join(cell['flags'])} "
                f"| {cell['mean_service_availability']:.3f} "
                f"| {cell['worst_seed_stockout_minutes']:.1f} "
                f"| {cell['mean_human_interventions']:.1f} |"
            )
    else:
        lines.append("No (policy, scenario) cell crossed a failure threshold.")

    lines += [
        "",
        "### Hardest scenarios (by best achievable availability)",
        "",
        "| scenario | best policy | best availability |",
        "|---|---|---|",
    ]
    for row in failures["hardest_scenarios"]:
        lines.append(
            f"| {row['scenario']} | {row['best_policy']} "
            f"| {row['best_availability']:.3f} |"
        )

    lines += [
        "",
        "### Worst scenario per policy",
        "",
        "| policy | worst scenario | availability |",
        "|---|---|---|",
    ]
    for row in failures["per_policy_worst"]:
        lines.append(
            f"| {row['policy']} | {row['scenario']} "
            f"| {row['service_availability']:.3f} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_outputs(
    result: BenchmarkResult, report: dict[str, Any], out_dir: str | Path
) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    report_json = out / "report.json"
    report_json.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    paths["report_json"] = report_json

    report_md = out / "report.md"
    report_md.write_text(render_markdown(report), encoding="utf-8")
    paths["report_md"] = report_md

    key, path = _write_episode_table(result.records, out)
    paths[key] = path

    kpi_long = out / "kpi_long.csv"
    _write_kpi_long_csv(result.records, kpi_long)
    paths["kpi_long_csv"] = kpi_long

    summary_csv = out / "summary.csv"
    _write_summary_csv(report["summary"], summary_csv)
    paths["summary_csv"] = summary_csv
    return paths
