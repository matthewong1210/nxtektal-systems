"""Command-line entry point for the E1 baseline benchmark.

Run from ``simulation/``:

    .venv/bin/python -m nxt_range_agent.benchmark --out reports/range_agent_e1
"""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

from nxt_range_agent.benchmark.aggregate import build_report
from nxt_range_agent.benchmark.config import (
    BASELINE_POLICY_NAMES,
    BenchmarkConfig,
)
from nxt_range_agent.benchmark.outputs import write_outputs
from nxt_range_agent.benchmark.runner import run_benchmark
from nxt_range_ops.scenarios.generators import SCENARIO_GENERATORS


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nxt-range-agent-benchmark",
        description="Benchmark the nxt_range_ops baseline policies (E1).",
    )
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument(
        "--policies",
        nargs="+",
        default=list(BASELINE_POLICY_NAMES),
        choices=sorted(BASELINE_POLICY_NAMES),
        metavar="POLICY",
        help=f"baselines to run (default: all; choices: {sorted(BASELINE_POLICY_NAMES)})",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=sorted(SCENARIO_GENERATORS),
        choices=sorted(SCENARIO_GENERATORS),
        metavar="SCENARIO",
        help=f"scenarios to run (default: all; choices: {sorted(SCENARIO_GENERATORS)})",
    )
    parser.add_argument(
        "--fixed-seeds", nargs="+", type=int, default=[101, 202, 303]
    )
    parser.add_argument("--n-random-seeds", type=int, default=7)
    parser.add_argument("--seed-pool-seed", type=int, default=7)
    parser.add_argument("--bootstrap-resamples", type=int, default=2000)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    config = BenchmarkConfig(
        policies=tuple(args.policies),
        scenarios=tuple(args.scenarios),
        fixed_seeds=tuple(args.fixed_seeds),
        n_random_seeds=args.n_random_seeds,
        seed_pool_seed=args.seed_pool_seed,
        bootstrap_resamples=args.bootstrap_resamples,
    )
    progress = None if args.quiet else print
    result = run_benchmark(config, progress=progress)
    report = build_report(result)
    paths = write_outputs(result, report, args.out)

    if not args.quiet:
        print()
        print(f"episodes: {len(result.records)}  seeds: {len(result.seeds)}")
        print("overall ranking:")
        for row in report["rankings"]["overall"]:
            print(
                f"  {row['rank']}. {row['policy']} "
                f"(composite {row['mean_composite_rank']:.2f}, "
                f"wins {row['scenario_wins']}, reward {row['mean_reward']:.1f})"
            )
        for key, path in sorted(paths.items()):
            print(f"  {key}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
