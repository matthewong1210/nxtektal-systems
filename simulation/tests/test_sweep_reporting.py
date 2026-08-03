"""Parameter sweeps and machine-readable reports (JSON + CSV)."""

from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import yaml

from nxt_sim.config.models import SweepAxis, SweepConfig
from nxt_sim.scenarios.sweep import expand_grid, run_sweep, run_sweep_config, summarize_sweep

from conftest import make_raw_bundle


def _write_config_files(tmp_path: Path, raw: dict) -> Path:
    """Materialize an in-memory bundle as the on-disk file layout."""
    (tmp_path / "robot.yaml").write_text(
        yaml.safe_dump({"robot_geometry": raw["robot"], "handoff_mechanism": raw["mechanism"]})
    )
    (tmp_path / "equipment.yaml").write_text(
        yaml.safe_dump({"equipment_profile": raw["equipment"]})
    )
    (tmp_path / "safety.yaml").write_text(yaml.safe_dump({"safety": raw["safety"]}))
    scenario = copy.deepcopy(raw["scenario"])
    scenario["robot_config"] = "robot.yaml"
    scenario["equipment_config"] = "equipment.yaml"
    scenario["safety_config"] = "safety.yaml"
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(yaml.safe_dump({"scenario": scenario}))
    return scenario_path


def _sweep_cfg() -> SweepConfig:
    return SweepConfig(
        name="test_sweep",
        base_scenario="scenario.yaml",
        overrides={"safety.max_docking_retries": 0},
        axes=[
            SweepAxis(path="scenario.initial_error.lateral_m", values=[-0.8, 0.0, 0.8]),
        ],
        repeats_per_point=1,
        base_seed=5,
    )


def test_expand_grid_is_cartesian():
    grid = expand_grid(
        [
            SweepAxis(path="a.b", values=[1.0, 2.0]),
            SweepAxis(path="c.d", values=[3.0, 4.0, 5.0]),
        ]
    )
    assert len(grid) == 6
    assert {"a.b": 1.0, "c.d": 3.0} in grid


def test_sweep_summary_and_tolerance_cliff(tmp_path):
    scenario_path = _write_config_files(tmp_path, make_raw_bundle())
    payload = run_sweep_config(_sweep_cfg(), scenario_path)
    summary = payload["summary"]

    assert summary["runs"] == 3
    # +-0.8 m -> residual 0.4 m misses the 0.25 m guide capture: collision.
    assert summary["docking_success_rate"] == round(1 / 3, 4)
    assert summary["cycle_success_rate"] == round(1 / 3, 4)
    assert summary["max_tolerated_lateral_error_m"] == 0.0
    assert summary["collision_count_total"] == 2
    assert summary["failure_reason_histogram"] == {"collision": 2}
    assert summary["contact_force_available"] is False
    center = [r for r in payload["runs"] if r["overrides"]["scenario.initial_error.lateral_m"] == 0.0]
    assert center[0]["success"] is True
    assert payload["placeholder_parameters"], "placeholder inventory must be disclosed"
    assert "PLACEHOLDER" in payload["validity_note"]


def test_sweep_reports_written_and_parseable(tmp_path):
    scenario_path = _write_config_files(tmp_path, make_raw_bundle())
    sweep_yaml = {
        "sweep": {
            "name": "file_sweep",
            "base_scenario": "scenario.yaml",
            "overrides": {"safety.max_docking_retries": 0},
            "axes": [
                {"path": "scenario.initial_error.lateral_m", "values": [-0.8, 0.0, 0.8]},
            ],
            "repeats_per_point": 1,
            "base_seed": 5,
        }
    }
    sweep_path = tmp_path / "sweep.yaml"
    sweep_path.write_text(yaml.safe_dump(sweep_yaml))
    out_dir = tmp_path / "reports"

    payload = run_sweep(sweep_path, output_dir=out_dir)

    report = json.loads(Path(payload["report_json"]).read_text())
    assert report["summary"]["runs"] == 3
    assert len(report["runs"]) == 3
    assert report["runs"][0]["transitions"], "JSON keeps full traces"

    with Path(payload["report_csv"]).open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3
    assert "success" in rows[0]
    assert "override:scenario.initial_error.lateral_m" in rows[0]
    assert "override:safety.max_docking_retries" in rows[0]
    assert {row["success"] for row in rows} == {"True", "False"}


def test_multi_axis_marginal_tolerance(tmp_path):
    """Tolerance along one axis is measured with other axes at their most
    benign grid point — extreme yaw must not mask the lateral tolerance."""
    scenario_path = _write_config_files(tmp_path, make_raw_bundle())
    cfg = SweepConfig(
        name="marginal",
        base_scenario="scenario.yaml",
        overrides={"safety.max_docking_retries": 0},
        axes=[
            SweepAxis(path="scenario.initial_error.lateral_m", values=[-0.8, -0.4, 0.0, 0.4, 0.8]),
            SweepAxis(path="scenario.initial_error.yaw_deg", values=[-16.0, 0.0, 16.0]),
        ],
        repeats_per_point=1,
        base_seed=5,
    )
    summary = run_sweep_config(cfg, scenario_path)["summary"]
    # At yaw=0: lateral 0.4 -> residual 0.2 captured by the guide; 0.8 collides.
    assert summary["max_tolerated_lateral_error_m"] == 0.4
    # At lateral=0: yaw 16 -> residual 8 deg exceeds the 6 deg capture.
    assert summary["max_tolerated_yaw_error_deg"] == 0.0
    assert 0 < summary["docking_success_rate"] < 1


def test_sweep_is_deterministic(tmp_path):
    scenario_path = _write_config_files(tmp_path, make_raw_bundle())
    a = run_sweep_config(_sweep_cfg(), scenario_path)
    b = run_sweep_config(_sweep_cfg(), scenario_path)
    assert a["runs"] == b["runs"]
    assert a["summary"] == b["summary"]


def test_docking_metrics_not_corrupted_by_post_docking_failures(tmp_path):
    """A sweep over dump angle: docking succeeds in every run, but too-shallow
    angles fail unloading. The DOCKING success rate and error tolerances must
    reflect docking, with cycle success reported separately."""
    scenario_path = _write_config_files(tmp_path, make_raw_bundle())
    cfg = SweepConfig(
        name="dump_sweep",
        base_scenario="scenario.yaml",
        axes=[SweepAxis(path="scenario.dump_angle_deg", values=[30.0, 60.0])],
        repeats_per_point=1,
        base_seed=5,
    )
    payload = run_sweep_config(cfg, scenario_path)
    summary = payload["summary"]
    assert summary["docking_success_rate"] == 1.0  # every run docked
    assert summary["cycle_success_rate"] == 0.5  # 30 deg fails unloading
    assert summary["failure_reason_histogram"] == {"unload_incomplete": 1}
    # The failed run's record keeps a clean classification (no duplicate
    # underlying reason on the safe-retract path).
    failed = [r for r in payload["runs"] if not r["success"]][0]
    assert failed["docking_success"] is True
    assert failed["underlying_failure_reason"] is None


def test_summarize_handles_empty_and_recovery_rates():
    summary = summarize_sweep(
        [
            {
                "success": True, "docking_success": True,
                "docking_completion_time_s": 10.0,
                "unloading_cycle_time_s": 20.0, "collision_count": 0,
                "min_clearance_m": 0.1, "recovery_attempts": 2, "recovery_successes": 1,
                "emergency_stop_triggered": False, "tipping_risk_indicator": 0.9,
                "contact_force_available": False, "failure_reason": "none", "overrides": {},
            },
        ]
    )
    assert summary["docking_success_rate"] == 1.0
    assert summary["cycle_success_rate"] == 1.0
    assert summary["failed_docking_recovery_rate"] == 0.5
    assert summary["failure_reason_histogram"] == {}

    empty = summarize_sweep([])
    assert empty["runs"] == 0
    assert empty["docking_success_rate"] is None
    assert empty["cycle_success_rate"] is None
    assert empty["min_clearance_m_overall"] is None
    assert empty["failed_docking_recovery_rate"] is None
    assert empty["failure_reason_histogram"] == {}
