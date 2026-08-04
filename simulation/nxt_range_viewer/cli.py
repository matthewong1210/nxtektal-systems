"""CLI: export viewer replay data for one (scenario, policy, seed) episode.

Example:
    python -m nxt_range_viewer --out demo/ \\
        --scenario demand_spike --policy demand_forecast_dispatch --seed 101 \\
        --benchmark-report reports/range_agent_e1/report.json
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence

from nxt_range_ops.scenarios.generators import SCENARIO_GENERATORS, make_scenario

from nxt_range_viewer.export import export_replay
from nxt_range_viewer.replay import DEMO_EVENT_KINDS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nxt-range-viewer-export",
        description="Export deterministic replay data for the YC demo viewer.",
    )
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument(
        "--scenario", required=True, choices=sorted(SCENARIO_GENERATORS)
    )
    parser.add_argument(
        "--policy", required=True, help="baseline policy name (e.g. random_valid)"
    )
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument(
        "--benchmark-report",
        type=Path,
        default=None,
        help="existing nxt_range_agent report.json to reuse for benchmark.json",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="include hidden simulator state (continuous robot positions) "
        "under each frame's 'debug' key",
    )
    parser.add_argument(
        "--all-events",
        action="store_true",
        help="export the full event log instead of the demo-callout subset",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    paths = export_replay(
        args.out,
        make_scenario(args.scenario),
        args.policy,
        seed=args.seed,
        benchmark_report=args.benchmark_report,
        include_debug=args.debug,
        event_kinds=None if args.all_events else DEMO_EVENT_KINDS,
    )
    for key in sorted(paths):
        print(f"{key}: {paths[key]}")
    return 0
