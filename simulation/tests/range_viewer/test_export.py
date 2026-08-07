"""Export layer: three deterministic JSON artifacts, disclaimer everywhere."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nxt_range_ops.evaluation.harness import DISCLAIMER
from nxt_range_viewer.benchmark_ref import build_benchmark_ref
from nxt_range_viewer.export import export_replay


@pytest.fixture
def report_path(tmp_path, mini_report) -> Path:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(mini_report, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_export_writes_three_files(tmp_path, short_scenario, report_path):
    out = tmp_path / "out"
    paths = export_replay(
        out,
        short_scenario,
        "inventory_threshold",
        seed=101,
        benchmark_report=report_path,
    )
    assert set(paths) == {"layout", "episode", "benchmark"}
    for path in paths.values():
        assert path.exists()
        json.loads(path.read_text(encoding="utf-8"))
    assert paths["layout"].name == "layout.json"
    assert paths["episode"].name == "episode.json"
    assert paths["benchmark"].name == "benchmark.json"


def test_export_without_benchmark_report_skips_file(tmp_path, short_scenario):
    paths = export_replay(tmp_path / "out", short_scenario, "inventory_threshold", seed=101)
    assert set(paths) == {"layout", "episode"}
    assert not (tmp_path / "out" / "benchmark.json").exists()


def test_export_bytes_deterministic(tmp_path, short_scenario, report_path):
    kwargs = dict(seed=101, benchmark_report=report_path)
    a = export_replay(tmp_path / "a", short_scenario, "inventory_threshold", **kwargs)
    b = export_replay(tmp_path / "b", short_scenario, "inventory_threshold", **kwargs)
    for key in a:
        assert a[key].read_bytes() == b[key].read_bytes(), key


def test_episode_json_structure(tmp_path, short_scenario):
    paths = export_replay(tmp_path / "out", short_scenario, "inventory_threshold", seed=101)
    episode = json.loads(paths["episode"].read_text(encoding="utf-8"))

    assert set(episode) == {"schema", "meta", "initial", "frames", "events"}
    meta = episode["meta"]
    assert meta["scenario"] == short_scenario.name
    assert meta["policy"] == "inventory_threshold"
    assert meta["seed"] == 101
    assert meta["disclaimer"] == DISCLAIMER
    assert meta["control_interval_s"] == short_scenario.episode.control_interval_s
    assert meta["n_steps"] == len(episode["frames"])
    assert meta["termination_reason"] == "day_complete"
    assert set(meta["kpis"]) == {
        "stockout_minutes",
        "service_availability",
        "human_interventions",
        "balls_processed",
        "energy_wh",
        "empty_travel_s",
        "robot_utilization",
        "handoff_queue_s",
        "safe_recovery_rate",
        "estimated_operating_cost",
    }
    assert "simulator_version" in meta
    assert "viewer_version" in meta
    # The initial block is the pre-action reset state: no action was taken yet.
    assert "action" not in episode["initial"]
    assert episode["initial"]["t_s"] == short_scenario.hours.open_minute * 60.0


def test_benchmark_ref_reuses_rankings_verbatim(mini_report):
    ref = build_benchmark_ref(mini_report)
    assert ref["rankings"] == mini_report["rankings"]
    assert ref["disclaimer"] == DISCLAIMER
    assert ref["provenance"] == mini_report["provenance"]
    assert ref["seeds"] == mini_report["seeds"]
    assert ref["policies"] == mini_report["config"]["policies"]
    assert ref["scenarios"] == mini_report["config"]["scenarios"]


def test_benchmark_ref_rejects_reports_without_rankings(mini_report):
    del mini_report["rankings"]
    with pytest.raises(ValueError, match="rankings"):
        build_benchmark_ref(mini_report)


def test_benchmark_ref_rejects_config_missing_policies(mini_report):
    del mini_report["config"]["policies"]
    with pytest.raises(ValueError, match="policies"):
        build_benchmark_ref(mini_report)


def test_export_rejects_non_finite_report_values(tmp_path, short_scenario, mini_report):
    # json.loads accepts NaN, so a hand-edited report could smuggle one in;
    # the exporter must fail loudly rather than write invalid JSON.
    mini_report["rankings"]["overall"][0]["mean_reward"] = float("nan")
    report = tmp_path / "report.json"
    report.write_text(json.dumps(mini_report), encoding="utf-8")
    with pytest.raises(ValueError):
        export_replay(
            tmp_path / "out",
            short_scenario,
            "inventory_threshold",
            seed=101,
            benchmark_report=report,
        )


def test_disclaimer_preserved_in_every_artifact(tmp_path, short_scenario, report_path):
    paths = export_replay(
        tmp_path / "out",
        short_scenario,
        "inventory_threshold",
        seed=101,
        benchmark_report=report_path,
    )
    for key, path in paths.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        blob = json.dumps(data)
        assert DISCLAIMER in blob, f"disclaimer missing from {key}"
