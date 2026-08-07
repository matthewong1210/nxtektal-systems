"""Evaluation harness output and the Phase 0 integration boundary."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from nxt_range_ops.config.models import placeholder_census
from nxt_range_ops.evaluation.harness import evaluate_policies
from nxt_range_ops.scenarios.generators import make_scenario

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "nxt_range_ops"

# The only Phase 0 modules the operational simulator may import: the pure
# vocabulary. Adapters, controllers, scenarios, metrics, reporting are all
# off-limits so Phase 0.5 never couples to the mock adapter implementation.
ALLOWED_NXT_SIM_MODULES = {
    "nxt_sim.interfaces.types",
    "nxt_sim.config.models",
}


def _imported_nxt_sim_modules() -> set[str]:
    found: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "nxt_sim" or node.module.startswith("nxt_sim."):
                    found.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "nxt_sim" or alias.name.startswith("nxt_sim."):
                        found.add(alias.name)
    return found


def test_integration_boundary_only_pure_phase0_modules():
    assert _imported_nxt_sim_modules() <= ALLOWED_NXT_SIM_MODULES


def test_phase0_package_untouched_by_range_ops():
    # nxt_sim must not know Phase 0.5 exists.
    nxt_sim_root = PACKAGE_ROOT.parent / "nxt_sim"
    offenders = [
        p
        for p in nxt_sim_root.rglob("*.py")
        if "nxt_range_ops" in p.read_text()
    ]
    assert offenders == []


def test_placeholder_census_counts_scenario_params():
    census = placeholder_census(make_scenario("normal_weekday"))
    assert len(census) >= 15
    assert any("washer" in p for p in census)
    assert any("charge_rate" in p for p in census)


def test_evaluation_harness_compares_policies(tmp_path):
    report = evaluate_policies(
        policy_names=["random_valid", "inventory_threshold"],
        scenario_names=["normal_weekday"],
        fixed_seeds=[61],
        n_random_seeds=1,
        out_dir=tmp_path,
        log_episodes=False,
    )
    assert "must NOT be presented" in report["disclaimer"]
    assert len(report["seeds"]) == 2
    assert report["placeholder_parameter_count"] > 0
    assert len(report["comparisons"]) == 2
    for row in report["comparisons"]:
        for kpi in (
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
        ):
            assert kpi in row["kpis"]

    saved = json.loads((tmp_path / "evaluation_report.json").read_text())
    assert saved["comparisons"]
    markdown = (tmp_path / "evaluation_report.md").read_text()
    assert "| scenario | policy |" in markdown
