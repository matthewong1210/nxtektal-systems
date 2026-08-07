"""Integration guards: the memory layer must be invisible to the live loop.

1. Contract purity — records/recorder/queries importable with the
   simulation stack blocked.
2. Trajectory + RNG neutrality — a fully recorded episode is byte-identical
   to a bare one (event log, metrics, obs digest); recording twice is
   byte-identical on disk.
3. Event completeness — concatenated window events equal the full log.
4. No-feedback boundary — nothing upstream mentions nxt_memory; only
   harvest.py may import the simulator.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np

from nxt_range_ops.config.models import OperatingHoursConfig
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.scenarios.generators import make_scenario

from nxt_facility import (
    build_facility_state,
    classify_state,
    derive_indicators,
    estimate_stockout,
    recommend,
)
from nxt_memory.harvest import harvest_events, snapshot_metrics
from nxt_memory.recorder import EpisodeMemoryRecorder
from nxt_memory.records import (
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
)

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_PACKAGES = ("nxt_sim", "nxt_range_ops", "nxt_range_agent", "nxt_facility")

WINDOW_STEPS = 30  # 30 one-minute control steps per memory window


def scripted_verdicts(recommendations) -> tuple[HumanVerdict, ...]:
    """Deterministic demo-operator policy — a recorded INPUT for tests;
    the memory layer itself never generates verdicts."""
    mapping = {"now": Verdict.ACCEPTED, "soon": Verdict.DEFERRED, "watch": Verdict.REJECTED}
    return tuple(
        HumanVerdict(
            rec_index=index,
            rule_id=rec.rule_id,
            verdict=mapping[rec.urgency.value],
            decided_by="scripted-test-operator",
        )
        for index, rec in enumerate(recommendations)
    )


def short_day_scenario():
    scenario = make_scenario("normal_weekday")
    return scenario.model_copy(
        update={"hours": OperatingHoursConfig(open_minute=360, close_minute=480)}
    )


def _absorb_obs(digest, obs) -> None:
    for key in sorted(obs):
        digest.update(key.encode())
        digest.update(np.asarray(obs[key]).tobytes())


def run_episode(scenario, seed: int, memory_dir: Path | None):
    """One policy-driven episode; optionally records memory windows."""
    env = RangeOpsEnv(scenario)
    policy = make_baseline("inventory_threshold", scenario, env.catalog, seed=seed)
    obs, info = env.reset(seed=seed)
    policy.reset()
    obs_digest = hashlib.sha256()
    _absorb_obs(obs_digest, obs)

    recorder = None
    cursor = 0
    seq = 0
    window_open = False
    window_start = {}

    def open_window():
        nonlocal window_open, window_start, cursor
        state = build_facility_state(env.sim)
        est = estimate_stockout(state)
        recs = recommend(state)
        window_start = {
            "t_s": env.sim.now,
            "observation": observation_section(
                state=state.to_dict(),
                operational_state=classify_state(state, est).value,
                stockout_eta_minutes=est.eta_minutes,
                stockout_limited_by=est.limited_by,
                indicators=derive_indicators(state).__dict__,
            ),
            "decision": decision_section(
                recommendations=[r.to_dict() for r in recs],
                rule_params={},
                decided_by="scripted-test-operator",
                verdicts=scripted_verdicts(recs),
            ),
            "metrics": snapshot_metrics(env.sim.metrics),
            "cursor": cursor,
        }
        window_open = True

    def close_window(status: WindowStatus):
        nonlocal window_open, cursor, seq
        events, cursor = harvest_events(env.sim.events, window_start["cursor"])
        recorder.record_window(
            MemoryWindow(
                episode_id=recorder.meta.episode_id,
                seq=seq,
                t_start_s=window_start["t_s"],
                t_end_s=env.sim.now,
                status=status,
                observation=window_start["observation"],
                decision=window_start["decision"],
                execution=execution_section(
                    events, window_start["cursor"], cursor
                ),
                outcome=outcome_section(
                    window_start["metrics"], snapshot_metrics(env.sim.metrics)
                ),
            )
        )
        seq += 1
        window_open = False

    if memory_dir is not None:
        recorder = EpisodeMemoryRecorder(
            memory_dir,
            EpisodeMeta(
                site_id="sim-test",
                deployment_id="guard",
                episode_id=f"{scenario.name}-seed{seed}",
                scenario_name=scenario.name,
                seed=seed,
                simulator_version="0.5.0",
                git_commit="test",
                driver_name="tests.memory.test_guards",
                driver_version="1",
            ),
        )
        open_window()

    step = 0
    while True:
        action = policy.act(obs, info)
        obs, reward, terminated, truncated, info = env.step(action)
        _absorb_obs(obs_digest, obs)
        obs_digest.update(repr(float(reward)).encode())
        step += 1
        if recorder is not None and step % WINDOW_STEPS == 0:
            close_window(WindowStatus.COMPLETE)
            if not (terminated or truncated):
                open_window()
        if terminated or truncated:
            if recorder is not None:
                if window_open:
                    close_window(WindowStatus.TRUNCATED)
                recorder.finalize()
            return (
                json.dumps(env.sim.events.to_dicts(), sort_keys=True),
                json.dumps(env.sim.metrics.to_dict(), sort_keys=True),
                obs_digest.hexdigest(),
            )


# ---------------------------------------------------------------------------


def test_recorded_episode_is_trajectory_neutral_and_reproducible(tmp_path):
    scenario = short_day_scenario()
    bare = run_episode(scenario, seed=2027, memory_dir=None)
    rec_a = run_episode(scenario, seed=2027, memory_dir=tmp_path / "a")
    rec_b = run_episode(scenario, seed=2027, memory_dir=tmp_path / "b")
    assert rec_a == bare  # event log, metrics, obs digest all byte-identical
    assert rec_b == bare
    rel = f"sim-test/guard/{scenario.name}-seed2027"
    for name in ("windows.jsonl", "episode.meta.json"):
        assert (tmp_path / "a" / rel / name).read_bytes() == (
            tmp_path / "b" / rel / name
        ).read_bytes()


def test_window_events_reassemble_the_full_log(tmp_path):
    scenario = short_day_scenario()
    run_episode(scenario, seed=11, memory_dir=tmp_path)
    episode_dir = tmp_path / "sim-test" / "guard" / f"{scenario.name}-seed11"
    windows = list(iter_windows(episode_dir))
    reassembled = [e for w in windows for e in w["execution"]["events"]]
    # windows start after EPISODE_START; compare against the log minus
    # events emitted before the first window opened
    first_cursor = windows[0]["execution"]["event_cursor_start"]
    env_events = None
    # re-run bare to obtain the reference full log deterministically
    env = RangeOpsEnv(scenario)
    policy = make_baseline("inventory_threshold", scenario, env.catalog, seed=11)
    obs, info = env.reset(seed=11)
    policy.reset()
    while True:
        action = policy.act(obs, info)
        obs, _, terminated, truncated, info = env.step(action)
        if terminated or truncated:
            env_events = env.sim.events.to_dicts()
            break
    assert reassembled == env_events[first_cursor:]
    assert windows[-1]["status"] in ("complete", "truncated_by_episode_end")


def test_contract_importable_without_simulation_stack():
    probe = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class Blocker(importlib.abc.MetaPathFinder):
            BLOCKED = {"simpy", "gymnasium", "numpy", "pandas", "pyarrow"}

            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in self.BLOCKED:
                    raise ImportError("blocked: " + fullname)
                return None

        sys.meta_path.insert(0, Blocker())
        import nxt_memory.records
        import nxt_memory.recorder
        import nxt_memory.queries
        import nxt_memory
        from nxt_memory import EpisodeMemoryRecorder, rule_acceptance_rates
        print("memory-contract-ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=SIMULATION_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "memory-contract-ok" in result.stdout


def test_no_upstream_or_facility_file_mentions_nxt_memory():
    offenders = []
    for pkg in UPSTREAM_PACKAGES:
        for path in (SIMULATION_ROOT / pkg).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "nxt_memory" in path.read_text():
                offenders.append(str(path))
    assert not offenders, f"live-loop files reference nxt_memory: {offenders}"


def test_only_harvest_may_import_the_simulator():
    for path in (SIMULATION_ROOT / "nxt_memory").glob("*.py"):
        tree = ast.parse(path.read_text())
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        first_party = {r for r in roots if r.startswith("nxt_")}
        if path.name == "harvest.py":
            assert first_party <= {"nxt_range_ops", "nxt_memory"}
        else:
            assert first_party <= {"nxt_memory"}, (
                f"{path.name} imports {first_party} — only harvest.py may "
                "touch the simulator"
            )
