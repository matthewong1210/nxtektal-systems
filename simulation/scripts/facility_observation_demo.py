"""Run the Site OS stack on synthetic observations instead of sim reads.

The Phase 4A proof in action: an imperfect synthetic sensor bank (noise,
calibration bias, delay, dropout) feeds the assembler; the SAME decision
layer then briefs the manager from the assembled FacilityState, with the
AssemblyReport saying how much to trust it.

Usage (from the simulation/ directory):
    .venv/bin/python scripts/facility_observation_demo.py \
        --scenario demand_spike --seed 42 --every-min 240
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

from nxt_facility import recommend, render_briefing  # noqa: E402
from nxt_telemetry.assemble import assemble_from_observations  # noqa: E402
from nxt_telemetry.bank import (  # noqa: E402
    ChannelImperfection,
    SyntheticSensorBank,
    TelemetryConfig,
    site_config_from_scenario,
    upstream_from_sim,
)

# Demo imperfections (source: placeholder — not calibrated to hardware)
DEMO_CONFIG = TelemetryConfig(
    families={
        "inventory": ChannelImperfection(noise_rel_sd=0.03, delay_s=60.0),
        "scan": ChannelImperfection(noise_rel_sd=0.15, cadence_s=300.0, dropout_prob=0.05),
        "robot": ChannelImperfection(dropout_prob=0.02),
    }
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", default="normal_weekday", choices=sorted(SCENARIO_GENERATORS)
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--policy", default="inventory_threshold")
    parser.add_argument("--every-min", type=int, default=240)
    args = parser.parse_args()

    scenario = make_scenario(args.scenario)
    env = RangeOpsEnv(scenario)
    policy = make_baseline(args.policy, scenario, env.catalog, seed=args.seed)
    obs, info = env.reset(seed=args.seed)
    policy.reset()

    bank = SyntheticSensorBank(env.sim, DEMO_CONFIG)
    site = site_config_from_scenario(scenario, seed=args.seed)
    previous = None

    control_min = scenario.episode.control_interval_s / 60.0
    steps_between = max(1, round(args.every_min / control_min))

    def show():
        nonlocal previous
        state, report = assemble_from_observations(
            bank.sample(), site, upstream_from_sim(env.sim), previous=previous
        )
        previous = state
        print(render_briefing(state, recommend(state)))
        print(
            f"[assembly] grade={report.provenance_grade} "
            f"confidence={report.overall_confidence:.2f} "
            f"missing={len(report.missing_channels)} "
            f"stale={len(report.stale_channels)} "
            f"issues={len(report.consistency_issues)}"
        )
        for issue in report.consistency_issues[:3]:
            print(f"[assembly]   ! {issue}")
        print("=" * 78)

    show()
    step = 0
    while True:
        action = policy.act(obs, info)
        obs, _, terminated, truncated, info = env.step(action)
        step += 1
        if step % steps_between == 0:
            show()
        if terminated or truncated:
            show()
            break


if __name__ == "__main__":
    main()
