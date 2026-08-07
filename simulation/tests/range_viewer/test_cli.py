"""CLI: `python -m nxt_range_viewer` writes the export artifacts."""

from __future__ import annotations

import json

import pytest

from nxt_range_viewer import cli


@pytest.fixture(autouse=True)
def _short_scenarios(monkeypatch, short_scenario_factory):
    """Shrink the operating day so CLI tests run in milliseconds."""
    monkeypatch.setattr(cli, "make_scenario", short_scenario_factory)


def test_cli_exports_episode_and_layout(tmp_path, capsys):
    exit_code = cli.main(
        [
            "--out",
            str(tmp_path / "demo"),
            "--scenario",
            "normal_weekday",
            "--policy",
            "inventory_threshold",
            "--seed",
            "101",
        ]
    )
    assert exit_code == 0
    assert (tmp_path / "demo" / "layout.json").exists()
    assert (tmp_path / "demo" / "episode.json").exists()
    out = capsys.readouterr().out
    assert "episode.json" in out


def test_cli_includes_benchmark_when_report_given(tmp_path, mini_report):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(mini_report), encoding="utf-8")
    exit_code = cli.main(
        [
            "--out",
            str(tmp_path / "demo"),
            "--scenario",
            "normal_weekday",
            "--policy",
            "random_valid",
            "--seed",
            "202",
            "--benchmark-report",
            str(report),
        ]
    )
    assert exit_code == 0
    benchmark = json.loads(
        (tmp_path / "demo" / "benchmark.json").read_text(encoding="utf-8")
    )
    assert benchmark["rankings"] == mini_report["rankings"]


def test_cli_debug_and_all_events_flags(tmp_path):
    common = [
        "--scenario",
        "normal_weekday",
        "--policy",
        "random_valid",
        "--seed",
        "101",
    ]
    assert cli.main(["--out", str(tmp_path / "plain"), *common]) == 0
    assert (
        cli.main(["--out", str(tmp_path / "full"), *common, "--debug", "--all-events"])
        == 0
    )
    plain = json.loads((tmp_path / "plain" / "episode.json").read_text(encoding="utf-8"))
    full = json.loads((tmp_path / "full" / "episode.json").read_text(encoding="utf-8"))

    assert all("debug" not in frame for frame in plain["frames"])
    assert all("debug" in frame for frame in full["frames"])
    assert plain["meta"]["includes_debug_state"] is False
    assert full["meta"]["includes_debug_state"] is True
    assert plain["meta"]["event_kinds"] is not None
    assert full["meta"]["event_kinds"] is None
    assert len(full["events"]) > len(plain["events"])


def test_cli_rejects_unknown_scenario(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--out",
                str(tmp_path / "demo"),
                "--scenario",
                "not_a_scenario",
                "--policy",
                "random_valid",
                "--seed",
                "1",
            ]
        )
