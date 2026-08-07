"""Write the three viewer artifacts: layout.json, episode.json, benchmark.json.

Serialization is deterministic (sorted keys, fixed indentation, no
wall-clock timestamps) so the same (scenario, policy, seed) always produces
byte-identical files — matching the reproducibility discipline of the
benchmark artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, FrozenSet, Optional, Union

from nxt_range_ops import SIMULATOR_VERSION
from nxt_range_ops.config.models import RangeOpsScenario
from nxt_range_ops.evaluation.harness import DISCLAIMER
from nxt_range_ops.recording.episode_logger import current_git_commit
from nxt_range_ops.scenarios.generators import make_scenario

from nxt_range_viewer.benchmark_ref import build_benchmark_ref
from nxt_range_viewer.layout import build_layout
from nxt_range_viewer.replay import DEMO_EVENT_KINDS, replay_episode


def export_replay(
    out_dir: Union[str, Path],
    scenario: Union[str, RangeOpsScenario],
    policy: str,
    seed: int,
    benchmark_report: Optional[Union[str, Path]] = None,
    include_debug: bool = False,
    event_kinds: Optional[FrozenSet[str]] = DEMO_EVENT_KINDS,
) -> dict[str, Path]:
    """Replay one episode and write the viewer artifacts to ``out_dir``.

    Returns the written paths keyed by artifact name. ``benchmark.json`` is
    written only when ``benchmark_report`` (a path to an existing
    ``nxt_range_agent`` ``report.json``) is provided.
    """
    scenario_obj = make_scenario(scenario) if isinstance(scenario, str) else scenario
    result = replay_episode(
        scenario_obj,
        policy,
        seed=seed,
        include_debug=include_debug,
        event_kinds=event_kinds,
    )

    # Import lazily so VIEWER_VERSION lives in one place without a cycle.
    from nxt_range_viewer import VIEWER_VERSION

    meta = {
        "scenario": result.scenario,
        "policy": result.policy,
        "policy_version": result.policy_version,
        "seed": result.seed,
        "n_steps": result.n_steps,
        "termination_reason": result.termination_reason,
        "control_interval_s": scenario_obj.episode.control_interval_s,
        "simulator_version": SIMULATOR_VERSION,
        "viewer_version": VIEWER_VERSION,
        "git_commit": current_git_commit(),
        "disclaimer": DISCLAIMER,
        "reward_total": result.reward_total,
        "kpis": result.kpis,
        "event_kinds": sorted(event_kinds) if event_kinds is not None else None,
        "includes_debug_state": include_debug,
    }
    episode = {
        "schema": "nxt-range-viewer/episode/v1",
        "meta": meta,
        "initial": result.initial,
        "frames": result.frames,
        "events": result.events,
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "layout": _write_json(out / "layout.json", build_layout(scenario_obj)),
        "episode": _write_json(out / "episode.json", episode),
    }
    if benchmark_report is not None:
        report = json.loads(Path(benchmark_report).read_text(encoding="utf-8"))
        paths["benchmark"] = _write_json(
            out / "benchmark.json", build_benchmark_ref(report)
        )
    return paths


def _write_json(path: Path, data: Any) -> Path:
    # allow_nan=False: NaN/inf would serialize as invalid JSON and break the
    # viewer silently — fail loudly at export time instead.
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path
