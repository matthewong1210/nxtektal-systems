#!/usr/bin/env python3
"""Run a docking parameter sweep and write JSON + CSV reports.

Usage (from simulation/):
    uv run python scripts/run_docking_sweep.py
    uv run python scripts/run_docking_sweep.py --sweep configs/scenarios/docking_error_sweep.yaml
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from nxt_sim.config.loader import ConfigError  # noqa: E402
from nxt_sim.scenarios.sweep import run_sweep  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sweep",
        default=str(SIM_ROOT / "configs" / "scenarios" / "docking_error_sweep.yaml"),
        help="sweep YAML file",
    )
    parser.add_argument(
        "--out", default=None, help="report output directory (default: reports/<name>_<stamp>/)"
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        payload = run_sweep(
            args.sweep,
            output_dir=args.out or (SIM_ROOT / "reports" / f"sweep_{stamp}"),
            progress=lambda msg: print(f"  {msg}"),
        )
    except ConfigError as exc:
        print(f"CONFIG ERROR:\n{exc}", file=sys.stderr)
        return 2

    s = payload["summary"]
    print(f"Sweep '{payload['sweep']}' — {s['runs']} runs")
    print(f"  docking success rate        : {s['docking_success_rate']}")
    print(f"  full-cycle success rate     : {s['cycle_success_rate']}")
    print(f"  max tolerated lateral error : {s['max_tolerated_lateral_error_m']} m")
    print(f"  max tolerated yaw error     : {s['max_tolerated_yaw_error_deg']} deg")
    print(f"  collisions (total/runs)     : {s['collision_count_total']}/{s['runs_with_collisions']}")
    print(f"  min clearance overall       : {s['min_clearance_m_overall']} m")
    print(f"  mean docking time           : {s['mean_docking_completion_time_s']} s")
    print(f"  mean unloading time         : {s['mean_unloading_cycle_time_s']} s")
    print(f"  recovery rate               : {s['failed_docking_recovery_rate']}")
    print(f"  emergency stops             : {s['emergency_stops']}")
    print(f"  min stability margin        : {s['min_stability_margin_overall']}")
    print(f"  failure reasons             : {s['failure_reason_histogram']}")
    print(f"  validity                    : {payload['validity_note']}")
    print(f"Reports: {payload['report_json']}\n         {payload['report_csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
