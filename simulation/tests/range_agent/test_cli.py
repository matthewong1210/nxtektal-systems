"""CLI: end-to-end on a real (single-episode) benchmark, argument validation."""

from __future__ import annotations

import json

import pytest

from nxt_range_agent.benchmark.cli import main


def test_cli_runs_minimal_real_benchmark(tmp_path):
    out = tmp_path / "bench"
    code = main(
        [
            "--out",
            str(out),
            "--scenarios",
            "normal_weekday",
            "--policies",
            "random_valid",
            "--fixed-seeds",
            "101",
            "--n-random-seeds",
            "0",
        ]
    )
    assert code == 0
    report = json.loads((out / "report.json").read_text())
    assert report["rankings"]["overall"][0]["policy"] == "random_valid"
    assert (out / "episodes.parquet").exists()
    assert (out / "report.md").exists()


def test_cli_rejects_unknown_policy(tmp_path):
    with pytest.raises(SystemExit):
        main(["--out", str(tmp_path), "--policies", "not_a_policy"])


def test_cli_rejects_unknown_scenario(tmp_path):
    with pytest.raises(SystemExit):
        main(["--out", str(tmp_path), "--scenarios", "not_a_scenario"])
