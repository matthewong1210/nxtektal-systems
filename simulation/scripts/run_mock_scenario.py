#!/usr/bin/env python3
"""Run one handoff scenario against the mock adapter and write a JSON record.

Usage (from simulation/):
    uv run python scripts/run_mock_scenario.py
    uv run python scripts/run_mock_scenario.py --scenario configs/scenarios/emergency_stop_demo.yaml
    uv run python scripts/run_mock_scenario.py --seed 7 --override scenario.initial_error.lateral_m=0.4
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from nxt_sim.config.loader import ConfigError  # noqa: E402
from nxt_sim.config.models import collect_placeholders  # noqa: E402
from nxt_sim.config.loader import load_scenario_bundle  # noqa: E402
from nxt_sim.reporting.report import write_json_report  # noqa: E402
from nxt_sim.scenarios.runner import run_scenario_file  # noqa: E402


def _parse_override(text: str) -> tuple[str, object]:
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"override must be PATH=VALUE, got {text!r}")
    path, raw = text.split("=", 1)
    value: object
    try:
        value = int(raw)
    except ValueError:
        try:
            value = float(raw)
        except ValueError:
            value = raw
    return path.strip(), value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        default=str(SIM_ROOT / "configs" / "scenarios" / "nominal_docking.yaml"),
        help="scenario YAML file",
    )
    parser.add_argument("--seed", type=int, default=None, help="override the mock random seed")
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        type=_parse_override,
        metavar="PATH=VALUE",
        help="dotted-path override, repeatable (e.g. scenario.initial_error.lateral_m=0.4)",
    )
    parser.add_argument(
        "--out", default=str(SIM_ROOT / "reports"), help="report output directory"
    )
    args = parser.parse_args()

    overrides = dict(args.override)
    try:
        record = run_scenario_file(args.scenario, overrides=overrides, seed=args.seed)
        bundle = load_scenario_bundle(args.scenario, overrides=overrides)
    except ConfigError as exc:
        print(f"CONFIG ERROR:\n{exc}", file=sys.stderr)
        return 2

    placeholders = collect_placeholders(bundle)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = write_json_report(
        {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "validity_note": (
                f"{len(placeholders)} physical parameters are PLACEHOLDERS — this run "
                "validates the pipeline, not the design. Ball flow/bridging/jamming "
                "require physical testing."
            ),
            "placeholder_parameters": placeholders,
            "run": record,
        },
        Path(args.out) / f"{record['scenario']}_{stamp}.json",
    )

    print(f"Scenario   : {record['scenario']}  (seed {record['seed']})")
    print(f"Result     : {'SUCCESS' if record['success'] else 'FAILURE'}"
          f"  final_state={record['final_state']}  reason={record['failure_reason']}")
    print(f"Docking    : success={record['docking_success']} attempts={record['docking_attempts']}"
          f" time={record['docking_completion_time_s']} s"
          f" recoveries={record['recovery_successes']}/{record['recovery_attempts']}")
    print(f"Unloading  : dump_attempts={record['dump_attempts']}"
          f" time={record['unloading_cycle_time_s']} s")
    print(f"Total time : {record['total_cycle_time_s']} s")
    print(f"Collisions : {record['collision_count']}   min clearance: {record['min_clearance_m']} m")
    print(f"Stability  : min margin {record['tipping_risk_indicator']}"
          f" ({record['stability_model']})")
    if record["emergency_stop_triggered"]:
        print(f"E-STOP     : trigger={record['emergency_stop_trigger_distance_m']} m"
              f" stopping={record['emergency_stop_stopping_distance_m']} m"
              f" clearance={record['emergency_stop_final_clearance_m']} m")
    if record["validation_warnings"]:
        print("Warnings   :")
        for w in record["validation_warnings"]:
            print(f"  - {w}")
    print(f"Placeholders in use: {len(placeholders)} (results are pipeline-validation only)")
    print(f"Report     : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
