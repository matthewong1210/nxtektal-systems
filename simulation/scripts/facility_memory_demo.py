"""Record one operating day into operational memory, then query it.

Demonstrates the Phase 3 chain end to end: a baseline policy drives the
simulator; every N minutes the driver captures a FacilityState, the
recommendations, a scripted operator's verdicts (a DEMO INPUT — the memory
layer never generates decisions), the window's events, and the measured
outcome. At the end, the three read-only queries answer the six-month
question against the freshly written store.

Usage (from the simulation/ directory):
    .venv/bin/python scripts/facility_memory_demo.py \
        --scenario demand_spike --seed 42 --window-min 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from nxt_range_ops import SIMULATOR_VERSION  # noqa: E402
from nxt_range_ops.env.range_ops_env import RangeOpsEnv  # noqa: E402
from nxt_range_ops.policies.baselines import make_baseline  # noqa: E402
from nxt_range_ops.recording.episode_logger import current_git_commit  # noqa: E402
from nxt_range_ops.scenarios.generators import (  # noqa: E402
    SCENARIO_GENERATORS,
    make_scenario,
)

from nxt_facility import (  # noqa: E402
    build_facility_state,
    classify_state,
    derive_indicators,
    estimate_stockout,
    recommend,
)
from nxt_memory import (  # noqa: E402
    EpisodeMemoryRecorder,
    EpisodeMeta,
    HumanVerdict,
    MemoryWindow,
    Verdict,
    WindowStatus,
    decision_section,
    execution_section,
    iter_windows,
    observation_section,
    outcome_section,
    outcome_deltas_by_decision,
    rule_acceptance_rates,
    stockout_eta_calibration,
)
from nxt_memory.harvest import harvest_events, snapshot_metrics  # noqa: E402

VERDICT_BY_URGENCY = {
    "now": Verdict.ACCEPTED,
    "soon": Verdict.DEFERRED,
    "watch": Verdict.REJECTED,
}


def scripted_verdicts(recommendations):
    """Deterministic demo operator: accept NOW, defer SOON, reject WATCH."""
    return tuple(
        HumanVerdict(
            rec_index=index,
            rule_id=rec.rule_id,
            verdict=VERDICT_BY_URGENCY[rec.urgency.value],
            decided_by="scripted-demo-operator",
        )
        for index, rec in enumerate(recommendations)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", default="normal_weekday", choices=sorted(SCENARIO_GENERATORS)
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--policy", default="inventory_threshold")
    parser.add_argument("--window-min", type=int, default=30)
    parser.add_argument(
        "--out", default=str(SIM_ROOT / "reports" / "operational_memory")
    )
    args = parser.parse_args()

    scenario = make_scenario(args.scenario)
    env = RangeOpsEnv(scenario)
    policy = make_baseline(args.policy, scenario, env.catalog, seed=args.seed)
    obs, info = env.reset(seed=args.seed)
    policy.reset()

    recorder = EpisodeMemoryRecorder(
        args.out,
        EpisodeMeta(
            site_id="sim-demo",
            deployment_id=f"{args.scenario}-demo",
            episode_id=f"{args.scenario}-seed{args.seed}",
            scenario_name=args.scenario,
            seed=args.seed,
            simulator_version=SIMULATOR_VERSION,
            git_commit=current_git_commit(),
            driver_name="scripts.facility_memory_demo",
            driver_version="1",
        ),
    )

    control_min = scenario.episode.control_interval_s / 60.0
    steps_per_window = max(1, round(args.window_min / control_min))
    cursor = 0
    seq = 0
    pending = None

    def open_window():
        nonlocal pending
        state = build_facility_state(env.sim)
        est = estimate_stockout(state)
        recs = recommend(state)
        pending = dict(
            t_s=env.sim.now,
            observation=observation_section(
                state=state.to_dict(),
                operational_state=classify_state(state, est).value,
                stockout_eta_minutes=est.eta_minutes,
                stockout_limited_by=est.limited_by,
                indicators=derive_indicators(state).__dict__,
            ),
            decision=decision_section(
                recommendations=[r.to_dict() for r in recs],
                rule_params={},
                decided_by="scripted-demo-operator",
                verdicts=scripted_verdicts(recs),
            ),
            metrics=snapshot_metrics(env.sim.metrics),
            cursor=cursor,
        )

    def close_window(status: WindowStatus):
        nonlocal cursor, seq, pending
        events, cursor = harvest_events(env.sim.events, pending["cursor"])
        recorder.record_window(
            MemoryWindow(
                episode_id=recorder.meta.episode_id,
                seq=seq,
                t_start_s=pending["t_s"],
                t_end_s=env.sim.now,
                status=status,
                observation=pending["observation"],
                decision=pending["decision"],
                execution=execution_section(events, pending["cursor"], cursor),
                outcome=outcome_section(
                    pending["metrics"], snapshot_metrics(env.sim.metrics)
                ),
            )
        )
        seq += 1
        pending = None

    open_window()
    step = 0
    while True:
        action = policy.act(obs, info)
        obs, _, terminated, truncated, info = env.step(action)
        step += 1
        if step % steps_per_window == 0 and pending is not None:
            close_window(WindowStatus.COMPLETE)
            if not (terminated or truncated):
                open_window()
        if terminated or truncated:
            if pending is not None:
                close_window(WindowStatus.TRUNCATED)
            paths = recorder.finalize()
            break

    episode_dir = paths["windows"].parent
    windows = list(iter_windows(episode_dir))
    print(f"Recorded {len(windows)} windows -> {episode_dir}\n")
    print("=== Rule acceptance rates ===")
    print(json.dumps(rule_acceptance_rates(windows), indent=2, sort_keys=True))
    print("\n=== Stockout-ETA calibration ===")
    print(json.dumps(stockout_eta_calibration(windows), indent=2, sort_keys=True))
    print("\n=== Outcome deltas by decision (observational) ===")
    print(json.dumps(outcome_deltas_by_decision(windows), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
