"""Output writers: files exist, are readable, viz-ready, and reproducible."""

from __future__ import annotations

import csv
import json

import pyarrow.parquet as pq

from nxt_range_agent.benchmark.aggregate import build_report
from nxt_range_agent.benchmark.config import BenchmarkConfig, KPI_DIRECTIONS
from nxt_range_agent.benchmark.outputs import write_outputs
from nxt_range_agent.benchmark.runner import run_benchmark

TINY = dict(
    policies=("random_valid", "inventory_threshold"),
    scenarios=("normal_weekday",),
    fixed_seeds=(11, 22),
    n_random_seeds=0,
)


def _run_and_write(tmp_dir, short_scenario_factory):
    result = run_benchmark(
        BenchmarkConfig(**TINY), scenario_factory=short_scenario_factory
    )
    report = build_report(result)
    paths = write_outputs(result, report, tmp_dir)
    return result, report, paths


def test_all_output_files_written(tmp_path, short_scenario_factory):
    _, _, paths = _run_and_write(tmp_path, short_scenario_factory)
    for key in ("report_json", "report_md", "episodes_parquet", "kpi_long_csv", "summary_csv"):
        assert key in paths
        assert paths[key].exists(), f"{key} not written"


def test_parquet_has_one_row_per_episode_with_flat_columns(
    tmp_path, short_scenario_factory
):
    result, _, paths = _run_and_write(tmp_path, short_scenario_factory)
    table = pq.read_table(paths["episodes_parquet"])
    assert table.num_rows == len(result.records)
    cols = set(table.column_names)
    for expected in (
        "scenario",
        "policy",
        "seed",
        "n_steps",
        "termination_reason",
        "reward_total",
        "kpi_service_availability",
        "kpi_estimated_operating_cost",
        "reward_component_stockout_duration",
    ):
        assert expected in cols, f"missing parquet column {expected!r}"


def test_report_json_round_trips(tmp_path, short_scenario_factory):
    _, report, paths = _run_and_write(tmp_path, short_scenario_factory)
    loaded = json.loads(paths["report_json"].read_text())
    assert loaded == json.loads(json.dumps(report))


def test_kpi_long_csv_is_tidy(tmp_path, short_scenario_factory):
    result, _, paths = _run_and_write(tmp_path, short_scenario_factory)
    with paths["kpi_long_csv"].open() as fh:
        rows = list(csv.DictReader(fh))
    assert set(rows[0]) == {"scenario", "policy", "seed", "metric", "value"}
    # one row per episode per metric (all KPIs + reward_total)
    assert len(rows) == len(result.records) * len(KPI_DIRECTIONS)
    float(rows[0]["value"])  # values must parse as numbers


def test_outputs_are_reproducible(tmp_path, short_scenario_factory):
    _, _, first = _run_and_write(tmp_path / "a", short_scenario_factory)
    _, _, second = _run_and_write(tmp_path / "b", short_scenario_factory)
    for key in ("report_json", "report_md", "kpi_long_csv", "summary_csv"):
        assert first[key].read_bytes() == second[key].read_bytes(), key
    assert pq.read_table(first["episodes_parquet"]).equals(
        pq.read_table(second["episodes_parquet"])
    )


def test_report_md_names_policies_and_disclaimer(tmp_path, short_scenario_factory):
    _, _, paths = _run_and_write(tmp_path, short_scenario_factory)
    text = paths["report_md"].read_text()
    assert "placeholder" in text.lower()
    for policy in TINY["policies"]:
        assert policy in text
    assert "ranking" in text.lower()
