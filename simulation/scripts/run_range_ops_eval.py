#!/usr/bin/env python3
"""Run the Range Ops Phase 0.5 baseline evaluation harness.

Examples (from simulation/):

    .venv/bin/python scripts/run_range_ops_eval.py
    .venv/bin/python scripts/run_range_ops_eval.py \
        --scenarios normal_weekday weekend_peak demand_spike \
        --policies inventory_threshold demand_forecast_dispatch \
        --fixed-seeds 101 202 303 --random-seeds 2 \
        --out reports/range_ops --log-episodes

Runs headless and far faster than real time. All outputs carry the
placeholder disclaimer: results do not represent real facility performance.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nxt_range_ops.evaluation.harness import evaluate_policies, render_markdown
from nxt_range_ops.scenarios.generators import SCENARIO_GENERATORS

ALL_POLICIES = (
    "random_valid",
    "inventory_threshold",
    "nearest_available_robot",
    "demand_forecast_dispatch",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=["normal_weekday"],
        choices=sorted(SCENARIO_GENERATORS),
    )
    parser.add_argument(
        "--policies", nargs="+", default=list(ALL_POLICIES), choices=ALL_POLICIES
    )
    parser.add_argument("--fixed-seeds", nargs="+", type=int, default=[101, 202, 303])
    parser.add_argument("--random-seeds", type=int, default=2)
    parser.add_argument("--seed-pool-seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--log-episodes", action="store_true")
    args = parser.parse_args()

    t0 = time.time()
    report = evaluate_policies(
        policy_names=args.policies,
        scenario_names=args.scenarios,
        fixed_seeds=args.fixed_seeds,
        n_random_seeds=args.random_seeds,
        seed_pool_seed=args.seed_pool_seed,
        out_dir=args.out,
        log_episodes=args.log_episodes,
    )
    print(render_markdown(report))
    n_episodes = sum(row["episodes"] for row in report["comparisons"])
    print(f"{n_episodes} episodes in {time.time() - t0:.1f}s wall clock.")
    if args.out:
        print(f"Report written to {args.out}/evaluation_report.{{json,md}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
