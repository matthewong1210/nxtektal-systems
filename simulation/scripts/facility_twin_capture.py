"""Capture a deterministic FacilityState stream for the digital twin.

Script tier: may import simulation packages. Re-runs one episode exactly as
nxt_range_viewer.replay does (same env, policy, seeding — the same episode
the benchmark ran), calling the RNG-neutral build_facility_state() once per
control step. Twin artifacts land under reports/digital_twin/; the briefing
sidecar (decision-layer output) lands under reports/demo/ — recommendations
never enter twin artifacts.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from nxt_facility.build import build_facility_state
from nxt_facility.briefing import render_briefing
from nxt_facility.decisions import recommend
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.evaluation.harness import DISCLAIMER
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.recording.episode_logger import current_git_commit
from nxt_range_ops.scenarios.generators import make_scenario
from nxt_range_twin.stream import (
    STREAM_SCHEMA,
    dump_json,
    validate_stream_meta,
    write_jsonl,
)
from nxt_range_viewer.layout import build_layout

# Match the constant the viewer exporter stamps (see nxt_range_viewer/export.py).
from nxt_range_ops import SIMULATOR_VERSION


def capture_episode(
    scenario: str,
    policy: str,
    seed: int,
    every_steps: int,
    site_id: str,
    deployment_id: str,
    twin_root: Path,
    demo_root: Path,
) -> Path:
    scenario_obj = make_scenario(scenario)
    env = RangeOpsEnv(scenario_obj)
    agent = make_baseline(policy, scenario_obj, env.catalog, seed=seed)

    obs, info = env.reset(seed=seed)
    agent.reset()

    episode_id = f"{scenario_obj.name}-seed{seed}"
    episode_dir = twin_root / site_id / deployment_id / episode_id
    episode_dir.mkdir(parents=True, exist_ok=True)
    demo_dir = demo_root / episode_id
    demo_dir.mkdir(parents=True, exist_ok=True)

    states: list[dict] = []
    briefings: list[dict] = []

    def snapshot(seq: int) -> None:
        state = build_facility_state(env.sim)
        states.append(state.to_dict())
        recs = recommend(state)
        briefings.append(
            {
                "seq": seq,
                "t_s": state.meta.t_s,
                "briefing": render_briefing(state, recs),
                "recommendations": [r.to_dict() for r in recs],
            }
        )

    snapshot(0)  # initial state, t=0
    step = 0
    while True:
        action = agent.act(obs, info)
        obs, reward, terminated, truncated, info = env.step(action)
        step += 1
        if step % every_steps == 0:
            snapshot(len(states))
        if terminated or truncated:
            break

    write_jsonl(episode_dir / "facility_states.jsonl", states)
    write_jsonl(episode_dir / "events.jsonl", env.sim.events.to_dicts())
    (episode_dir / "layout.json").write_text(dump_json(build_layout(scenario_obj)))
    write_jsonl(demo_dir / "briefings.jsonl", briefings)

    meta = {
        "schema": STREAM_SCHEMA,
        "site_id": site_id,
        "deployment_id": deployment_id,
        "episode_id": episode_id,
        "scenario_name": scenario_obj.name,
        "seed": int(seed),
        "policy": agent.name,
        "policy_version": agent.version,
        "control_interval_s": float(scenario_obj.episode.control_interval_s),
        "every_steps": int(every_steps),
        "n_records": len(states),
        "simulator_version": SIMULATOR_VERSION,
        "git_commit": current_git_commit(),
        "disclaimer": DISCLAIMER,
    }
    validate_stream_meta(meta)
    (episode_dir / "stream.meta.json").write_text(dump_json(meta))
    return episode_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="handoff_station_outage")
    parser.add_argument("--policy", default="inventory_threshold")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--every-steps", type=int, default=1)
    parser.add_argument("--site-id", default="sim-baseline")
    parser.add_argument("--deployment-id", default="dev")
    parser.add_argument("--twin-root", type=Path, default=_ROOT / "reports" / "digital_twin")
    parser.add_argument("--demo-root", type=Path, default=_ROOT / "reports" / "demo")
    args = parser.parse_args()
    out = capture_episode(
        args.scenario, args.policy, args.seed, args.every_steps,
        args.site_id, args.deployment_id, args.twin_root, args.demo_root,
    )
    print(out)


if __name__ == "__main__":
    main()
