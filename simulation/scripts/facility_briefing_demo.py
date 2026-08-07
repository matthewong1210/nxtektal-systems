"""Print manager briefings over one simulated operating day.

Demonstrates the Phase 2 chain end to end: the simulator advances under a
baseline policy, and at a fixed cadence we build a FacilityState snapshot
and render the briefing — watching advice evolve as the facility drifts
between NOMINAL, STRAINED, and CRITICAL.

Usage (from the simulation/ directory):
    .venv/bin/python scripts/facility_briefing_demo.py \
        --scenario demand_spike --seed 42 --every-min 120

This script may import the simulator (it is a demo, not a contract
module); the decision layer itself never does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from nxt_range_ops.env.range_ops_env import RangeOpsEnv  # noqa: E402
from nxt_range_ops.policies.baselines import make_baseline  # noqa: E402
from nxt_range_ops.scenarios.generators import (  # noqa: E402
    SCENARIO_GENERATORS,
    make_scenario,
)

from nxt_facility import build_facility_state, recommend, render_briefing  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", default="normal_weekday", choices=sorted(SCENARIO_GENERATORS)
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--policy", default="inventory_threshold")
    parser.add_argument(
        "--every-min",
        type=int,
        default=120,
        help="simulated minutes between briefings",
    )
    args = parser.parse_args()

    scenario = make_scenario(args.scenario)
    env = RangeOpsEnv(scenario)
    policy = make_baseline(args.policy, scenario, env.catalog, seed=args.seed)
    obs, info = env.reset(seed=args.seed)
    policy.reset()

    control_min = scenario.episode.control_interval_s / 60.0
    steps_between = max(1, round(args.every_min / control_min))

    def print_briefing() -> None:
        state = build_facility_state(env.sim)
        print(render_briefing(state, recommend(state)))
        print("=" * 78)

    print_briefing()
    step = 0
    while True:
        action = policy.act(obs, info)
        obs, _, terminated, truncated, info = env.step(action)
        step += 1
        if step % steps_between == 0:
            print_briefing()
        if terminated or truncated:
            print_briefing()
            break


if __name__ == "__main__":
    main()
