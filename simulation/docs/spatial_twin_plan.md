# Digital Twin / Spatial Intelligence Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `nxt_range_twin` — a projection-only digital twin layer that captures a deterministic FacilityState stream, authors a two-layer USD scene from it, and synchronizes the existing Streamlit viewer with the existing manager-briefing layer.

**Architecture:** Three phases in the approved priority order: (A) deterministic `facility-state-stream/v1` artifact captured at script tier by re-running an episode exactly as `nxt_range_viewer.replay` does; (B) a stdlib+`pxr`-only USD builder consuming only serialized artifacts (never sim imports); (C) a briefing side panel in the Streamlit viewer scrubbing a precomputed `briefings.jsonl` sidecar. Spec: `simulation/docs/spatial_twin_design.md` (approved).

**Tech Stack:** Python ≥3.11 (repo venv is 3.13.14), `usd-core==26.8` (optional extra), pytest, existing `nxt_range_ops` / `nxt_facility` / `nxt_range_viewer` / `nxt_range_demo` packages (all frozen — read-only).

## Global Constraints

- Working dir for all commands: `simulation (source location not versioned)`; run tests with the project venv (`.venv/bin/python -m pytest`).
- Branch: `feature/digital-twin-phase0` (stacked on `feature/facility-memory`). Commit style: `feat(twin): …`, `test(twin): …`, `docs: …`.
- `nxt_range_twin/` package modules import **stdlib + `pxr` only** — never `nxt_range_ops`, `nxt_sim`, `nxt_facility`, `nxt_memory`, `nxt_range_viewer`, simpy, gymnasium, numpy, pydantic. Only `scripts/facility_twin_capture.py` (script tier) may import simulation packages.
- Upstream packages are untouched: no file under `nxt_range_ops/`, `nxt_sim/`, `nxt_facility/`, `nxt_memory/`, `nxt_range_viewer/` may be modified, and none may ever contain the string `nxt_range_twin`. `nxt_range_demo` is modified ONLY by Task 10 (additive panel).
- Determinism: no `time`/`datetime`/`uuid` imports anywhere in new code; sim-time only; JSON with `sort_keys=True`; identical inputs must produce byte-identical outputs.
- No interpolation of robot positions (held samples only). No recommendations inside twin artifacts (`reports/digital_twin/…`); the briefings sidecar lives under `reports/demo/…`.
- All invented geometry carries `nxt:provenance = "placeholder"` customData; the layout DISCLAIMER rides in `customLayerData` verbatim.
- Schema tags: stream `"nxt-range-twin/facility-state-stream/v1"`, stage `"nxt-range-twin/stage/v1"`. Identity: `episode_id = f"{scenario}-seed{seed}"`, default `site_id="sim-baseline"`, `deployment_id="dev"`.
- Positioning language in docs/strings: "digital twin / spatial intelligence layer for managed outdoor facilities" — never "general outdoor world model".

---

## Phase A — deterministic FacilityState stream

### Task 1: Package skeleton + stream module (stdlib-only)

**Files:**
- Create: `nxt_range_twin/__init__.py`
- Create: `nxt_range_twin/stream.py`
- Test: `tests/twin/__init__.py` (empty), `tests/twin/test_stream.py`

**Interfaces:**
- Produces: `STREAM_SCHEMA: str`, `dump_json_line(record: dict) -> str`, `dump_json(record: dict) -> str`, `write_jsonl(path, records) -> int`, `read_jsonl(path) -> list[dict]`, `validate_stream_meta(meta: dict) -> None` (raises `ValueError` on missing keys), `REQUIRED_META_KEYS: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/twin/test_stream.py
"""Stream schema and JSONL IO for the twin's file contracts."""
import json
from pathlib import Path

import pytest

from nxt_range_twin.stream import (
    REQUIRED_META_KEYS,
    STREAM_SCHEMA,
    dump_json,
    dump_json_line,
    read_jsonl,
    validate_stream_meta,
    write_jsonl,
)


def _meta() -> dict:
    return {
        "schema": STREAM_SCHEMA,
        "site_id": "sim-baseline",
        "deployment_id": "dev",
        "episode_id": "normal_weekday-seed7",
        "scenario_name": "normal_weekday",
        "seed": 7,
        "policy": "inventory_threshold",
        "policy_version": "0",
        "control_interval_s": 60.0,
        "every_steps": 1,
        "n_records": 3,
        "simulator_version": "x",
        "git_commit": None,
        "disclaimer": "placeholder",
    }


def test_schema_tag_and_meta_validation():
    assert STREAM_SCHEMA == "nxt-range-twin/facility-state-stream/v1"
    validate_stream_meta(_meta())  # must not raise
    broken = _meta()
    del broken["site_id"]
    with pytest.raises(ValueError, match="site_id"):
        validate_stream_meta(broken)


def test_jsonl_roundtrip_sorted_and_byte_stable(tmp_path: Path):
    records = [{"b": 2, "a": 1}, {"z": [3, 2], "a": {"y": 1, "x": 0}}]
    p = tmp_path / "s.jsonl"
    n = write_jsonl(p, records)
    assert n == 2
    line0 = p.read_text().splitlines()[0]
    assert line0 == '{"a":1,"b":2}'  # sorted keys, compact separators
    assert read_jsonl(p) == [json.loads(dump_json_line(r)) for r in records]
    # byte stability: writing again produces identical bytes
    before = p.read_bytes()
    write_jsonl(p, records)
    assert p.read_bytes() == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/twin/test_stream.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nxt_range_twin'`

- [ ] **Step 3: Write minimal implementation**

```python
# nxt_range_twin/__init__.py
"""Digital twin / spatial intelligence layer for managed outdoor facilities.

Projection-only: consumes serialized FacilityState streams and layout
artifacts, authors USD. Never a source of truth. See
docs/spatial_twin_design.md.
"""

TWIN_VERSION = "0.1.0"
```

```python
# nxt_range_twin/stream.py
"""facility-state-stream/v1 — the twin's dynamic input contract.

stdlib only. One FacilityState.to_dict() per JSONL line; sidecar meta file
carries identity. Sorted keys and compact separators make every artifact
byte-reproducible.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

STREAM_SCHEMA = "nxt-range-twin/facility-state-stream/v1"

REQUIRED_META_KEYS = frozenset(
    {
        "schema", "site_id", "deployment_id", "episode_id", "scenario_name",
        "seed", "policy", "policy_version", "control_interval_s",
        "every_steps", "n_records", "simulator_version", "git_commit",
        "disclaimer",
    }
)


def dump_json_line(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)


def dump_json(record: dict) -> str:
    return json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"


def write_jsonl(path: str | Path, records: Iterable[dict]) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(dump_json_line(record) + "\n")
            count += 1
    return count


def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def validate_stream_meta(meta: dict) -> None:
    missing = sorted(REQUIRED_META_KEYS - meta.keys())
    if missing:
        raise ValueError(f"stream meta missing keys: {missing}")
    if meta["schema"] != STREAM_SCHEMA:
        raise ValueError(f"unexpected schema {meta['schema']!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/twin/test_stream.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add nxt_range_twin/__init__.py nxt_range_twin/stream.py tests/twin/
git commit -m "feat(twin): package skeleton and facility-state-stream/v1 JSONL contract"
```

### Task 2: Capture script — the deterministic stream producer

**Files:**
- Create: `scripts/facility_twin_capture.py`
- Test: `tests/twin/test_capture.py`

**Interfaces:**
- Consumes: `nxt_range_twin.stream` (Task 1); frozen upstream APIs verified in-repo: `make_scenario(name)`, `RangeOpsEnv(scenario)`, `env.catalog`, `env.reset(seed=seed)`, `env.step(action)`, `env.sim`, `make_baseline(name, scenario, catalog, seed=seed)` (names: `random_valid`, `inventory_threshold`, `nearest_available_robot`, `demand_forecast_dispatch`), `build_facility_state(sim)`, `recommend(state)`, `render_briefing(state, recs)`, `build_layout(scenario)`, `sim.events.to_dicts()`, `DISCLAIMER` from `nxt_range_ops.evaluation.harness`, `current_git_commit` from `nxt_range_ops.recording.episode_logger`, `SIMULATOR_VERSION` (check exact constant name in `nxt_range_ops/__init__.py`; the viewer's `export.py` stamps it — copy that import).
- Produces: `capture_episode(scenario: str, policy: str, seed: int, every_steps: int, site_id: str, deployment_id: str, twin_root: Path, demo_root: Path) -> Path` returning the episode dir; artifacts `layout.json`, `facility_states.jsonl`, `events.jsonl`, `stream.meta.json` under `<twin_root>/<site_id>/<deployment_id>/<episode_id>/`, and `briefings.jsonl` under `<demo_root>/<episode_id>/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/twin/test_capture.py
"""Capture produces a complete, byte-reproducible artifact set."""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from facility_twin_capture import capture_episode  # noqa: E402

pytestmark = pytest.mark.slow  # full-episode re-runs


def _run(tmp_path: Path, tag: str) -> Path:
    return capture_episode(
        scenario="handoff_station_outage",
        policy="inventory_threshold",
        seed=7,
        every_steps=1,
        site_id="sim-baseline",
        deployment_id="dev",
        twin_root=tmp_path / tag / "digital_twin",
        demo_root=tmp_path / tag / "demo",
    )


def test_capture_writes_complete_artifact_set(tmp_path: Path):
    episode_dir = _run(tmp_path, "a")
    assert episode_dir.name == "handoff_station_outage-seed7"
    for name in ("layout.json", "facility_states.jsonl", "events.jsonl", "stream.meta.json"):
        assert (episode_dir / name).exists(), name
    meta = json.loads((episode_dir / "stream.meta.json").read_text())
    states = (episode_dir / "facility_states.jsonl").read_text().splitlines()
    # initial snapshot + one per control step
    assert meta["n_records"] == len(states)
    first = json.loads(states[0])
    assert first["meta"]["scenario_name"] == "handoff_station_outage"
    assert first["ball_flow"]["conserved"] is True
    # briefings sidecar lives OUTSIDE the twin store
    sidecar = tmp_path / "a" / "demo" / "handoff_station_outage-seed7" / "briefings.jsonl"
    assert sidecar.exists()
    brief0 = json.loads(sidecar.read_text().splitlines()[0])
    assert set(brief0) == {"seq", "t_s", "briefing", "recommendations"}
    assert "digital_twin" not in str(sidecar)


def test_capture_is_byte_reproducible(tmp_path: Path):
    dir_a = _run(tmp_path, "a")
    dir_b = _run(tmp_path, "b")
    for name in ("layout.json", "facility_states.jsonl", "events.jsonl", "stream.meta.json"):
        assert (dir_a / name).read_bytes() == (dir_b / name).read_bytes(), name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/twin/test_capture.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'facility_twin_capture'`

- [ ] **Step 3: Write the capture script**

Reference for the loop shape: `nxt_range_viewer/replay.py::replay_episode` (mirror it exactly — same env, policy factory, seeding). Reference for path bootstrapping: `scripts/facility_briefing_demo.py` header.

```python
# scripts/facility_twin_capture.py
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
from nxt_range_ops import __version__ as SIMULATOR_VERSION


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
```

Implementation notes for the executor:
- If `from nxt_range_ops import __version__` fails, use whatever `nxt_range_viewer/export.py` imports for `simulator_version` — copy that exact import.
- If `current_git_commit()` takes arguments, mirror the call in `nxt_range_ops/recording/episode_logger.py` usage.
- `scenario_obj.episode.control_interval_s` — verify attribute path in `nxt_range_ops/config/models.py` (the env steps with it in `range_ops_env.py:127`); adjust if it lives elsewhere on the scenario.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/twin/test_capture.py -v`
Expected: 2 passed (slow — two full episode re-runs per test; ~minutes total is normal)

- [ ] **Step 5: Commit**

```bash
git add scripts/facility_twin_capture.py tests/twin/test_capture.py
git commit -m "feat(twin): deterministic FacilityState stream capture at script tier"
```

### Task 3: Phase A guard tests — neutrality, cross-harness agreement, static scans

**Files:**
- Test: `tests/twin/test_guards_stream.py`

**Interfaces:**
- Consumes: `capture_episode` (Task 2), `nxt_range_viewer.replay.replay_episode`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/twin/test_guards_stream.py
"""Guards: capture changes nothing, agrees with the viewer harness, stays pure."""
import ast
import json
import sys
from pathlib import Path

import pytest

SIM_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SIM_ROOT / "scripts"))

from facility_twin_capture import capture_episode  # noqa: E402
from nxt_range_viewer.replay import replay_episode  # noqa: E402

pytestmark = pytest.mark.slow

ARGS = dict(scenario="handoff_station_outage", policy="inventory_threshold", seed=7)


def test_capture_matches_viewer_replay_events_and_length(tmp_path: Path):
    """Trajectory neutrality + cross-harness consistency in one assertion set.

    replay_episode never calls build_facility_state; capture calls it every
    step. Identical event logs prove capture is trajectory- and RNG-neutral
    at capture cadence, and that both harnesses re-run the same episode.
    """
    episode_dir = capture_episode(
        **ARGS, every_steps=1, site_id="s", deployment_id="d",
        twin_root=tmp_path / "twin", demo_root=tmp_path / "demo",
    )
    captured_events = [
        json.loads(line)
        for line in (episode_dir / "events.jsonl").read_text().splitlines()
    ]
    result = replay_episode(ARGS["scenario"], ARGS["policy"], ARGS["seed"], event_kinds=None)
    assert captured_events == result.events
    states = (episode_dir / "facility_states.jsonl").read_text().splitlines()
    assert len(states) == result.n_steps + 1  # initial + one per control step


def test_capture_agrees_with_viewer_frames_on_shared_fields(tmp_path: Path):
    """episode.json frames vs facility_states.jsonl: shared per-entity fields agree."""
    episode_dir = capture_episode(
        **ARGS, every_steps=1, site_id="s", deployment_id="d",
        twin_root=tmp_path / "twin", demo_root=tmp_path / "demo",
    )
    states = [
        json.loads(line)
        for line in (episode_dir / "facility_states.jsonl").read_text().splitlines()
    ]
    result = replay_episode(ARGS["scenario"], ARGS["policy"], ARGS["seed"], event_kinds=None)
    # state[k+1] is the snapshot after control step k+1 == frame[k]
    for frame, state in zip(result.frames, states[1:]):
        frame_zone_balls = {z["zone_id"]: z["balls"] for z in frame["zones"]}
        state_zone_balls = {z["zone_id"]: z["balls"] for z in state["zones"]}
        assert frame_zone_balls == state_zone_balls
        frame_robot_loc = {r["robot_id"]: r["location"] for r in frame["robots"]}
        state_robot_loc = {r["robot_id"]: r["location"] for r in state["robots"]}
        assert frame_robot_loc == state_robot_loc


BANNED_IMPORTS = {"time", "datetime", "uuid"}


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_no_wallclock_or_uuid_in_twin_code():
    files = list((SIM_ROOT / "nxt_range_twin").glob("*.py"))
    files.append(SIM_ROOT / "scripts" / "facility_twin_capture.py")
    for path in files:
        assert not (_imports_of(path) & BANNED_IMPORTS), path


def test_capture_script_never_calls_rng_drawing_accessors():
    source = (SIM_ROOT / "scripts" / "facility_twin_capture.py").read_text()
    assert "sensed_zone_counts" not in source
    assert "sensed_battery_frac" not in source


def test_no_upstream_file_mentions_twin():
    """Mirror of test_no_upstream_file_mentions_nxt_facility, extended set."""
    upstream = ["nxt_sim", "nxt_range_ops", "nxt_facility", "nxt_memory",
                "nxt_range_viewer", "nxt_range_agent"]
    for package in upstream:
        for path in (SIM_ROOT / package).rglob("*.py"):
            assert "nxt_range_twin" not in path.read_text(), path
```

- [ ] **Step 2: Run tests to verify current state**

Run: `.venv/bin/python -m pytest tests/twin/test_guards_stream.py -v`
Expected: all 5 PASS immediately if Task 2 was implemented correctly. If the events comparison fails, the capture loop deviates from the replay recipe — fix the loop (never the upstream); if the shared-fields zip fails on alignment, re-check the state-after-step-k ↔ frame-k pairing.

- [ ] **Step 3: Commit**

```bash
git add tests/twin/test_guards_stream.py
git commit -m "test(twin): trajectory-neutrality, cross-harness, and purity guards for capture"
```

---

## Phase B — USD generation

### Task 4: usd-core dependency + pxr byte-determinism spike (release blocker)

**Files:**
- Modify: `pyproject.toml` (the `[project.optional-dependencies]` table — add alongside existing `range-ops`/`demo` extras)
- Test: `tests/twin/test_usd_determinism.py`

**Interfaces:**
- Produces: `twin` optional extra; the repo-wide skip pattern `pxr = pytest.importorskip("pxr")` for USD tests.

- [ ] **Step 1: Add the extra**

In `pyproject.toml`, add to `[project.optional-dependencies]`:

```toml
twin = ["usd-core==26.8"]
```

- [ ] **Step 2: Install**

Run: `uv pip install -e ".[twin]" --python .venv/bin/python`
Expected: `usd-core==26.8` installed (verified resolvable on this Mac / Python 3.13.14).

- [ ] **Step 3: Write the spike test**

```python
# tests/twin/test_usd_determinism.py
"""pxr text serialization must be byte-deterministic — RELEASE BLOCKER.

Byte identity is the house's proof that the twin adds no information
(design §4.3). If this test fails on a usd-core upgrade, the pin stays.
"""
from pathlib import Path

import pytest

pxr = pytest.importorskip("pxr")
from pxr import Sdf, Usd, UsdGeom  # noqa: E402


def _author(path: Path) -> None:
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    cube = UsdGeom.Cube.Define(stage, "/World/Marker")
    attr = cube.GetPrim().CreateAttribute("nxt:probe", Sdf.ValueTypeNames.Int)
    for t, v in ((0.0, 1), (60.0, 2), (120.0, 2)):
        attr.Set(v, t)
    stage.GetRootLayer().customLayerData = {"schema": "nxt-range-twin/stage/v1"}
    stage.GetRootLayer().Save()


def test_usda_text_serialization_is_byte_deterministic(tmp_path: Path):
    _author(tmp_path / "a.usda")
    _author(tmp_path / "b.usda")
    assert (tmp_path / "a.usda").read_bytes() == (tmp_path / "b.usda").read_bytes()
```

- [ ] **Step 4: Run and gate**

Run: `.venv/bin/python -m pytest tests/twin/test_usd_determinism.py -v`
Expected: PASS. **If it fails, STOP the phase and report — byte-determinism is a release blocker per the approved design, not a waivable nice-to-have.**

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/twin/test_usd_determinism.py
git commit -m "feat(twin): usd-core==26.8 extra with byte-determinism release-blocker spike"
```

### Task 5: Placement — node grammar and co-location offsets (pure math)

**Files:**
- Create: `nxt_range_twin/placement.py`
- Test: `tests/twin/test_placement.py`

**Interfaces:**
- Consumes: layout dict shape from `nxt_range_viewer/layout.py::build_layout` (keys: `dispenser: {x_m,y_m}`, `charger: {position, slots}`, `zones: [{zone_id, position, …}]`, `stations: [{station_id, position, …}]`).
- Produces: `build_layout_index(layout: dict) -> dict[str, tuple[float, float]]` (keys `"dispenser"`, `"charger"`, `"zone:<id>"`, `"station:<id>"`), `resolve_location(node: str, index: dict) -> tuple[float, float]` (raises `KeyError` on unknown node), `robot_offset(robot_id: str, all_ids: tuple[str, ...]) -> tuple[float, float]` (deterministic ring by sorted rank, radius `CROWD_RING_RADIUS_M = 2.5`), constants `ZONE_RADIUS_M = 8.0`, `STATION_HALF_M = 3.0`, `TERRAIN_MARGIN_M = 20.0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/twin/test_placement.py
import math

import pytest

from nxt_range_twin.placement import (
    CROWD_RING_RADIUS_M,
    build_layout_index,
    resolve_location,
    robot_offset,
)

LAYOUT = {
    "dispenser": {"x_m": 0.0, "y_m": 0.0},
    "charger": {"position": {"x_m": 5.0, "y_m": -30.0}, "slots": 2},
    "zones": [{"zone_id": "Z1", "position": {"x_m": 40.0, "y_m": -25.0}}],
    "stations": [{"station_id": "H1", "position": {"x_m": 10.0, "y_m": -20.0}}],
}


def test_every_location_grammar_form_resolves():
    index = build_layout_index(LAYOUT)
    assert resolve_location("dispenser", index) == (0.0, 0.0)
    assert resolve_location("charger", index) == (5.0, -30.0)
    assert resolve_location("zone:Z1", index) == (40.0, -25.0)
    assert resolve_location("station:H1", index) == (10.0, -20.0)
    with pytest.raises(KeyError):
        resolve_location("zone:NOPE", index)
    with pytest.raises(KeyError):
        resolve_location("teleporter", index)


def test_robot_offsets_are_deterministic_and_disjoint():
    ids = ("R1", "R2", "R3")
    offsets = [robot_offset(r, ids) for r in ids]
    assert offsets == [robot_offset(r, ids) for r in ids]  # deterministic
    assert len(set(offsets)) == 3  # disjoint
    for dx, dy in offsets:
        assert math.hypot(dx, dy) == pytest.approx(CROWD_RING_RADIUS_M)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/twin/test_placement.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError`

- [ ] **Step 3: Implement**

```python
# nxt_range_twin/placement.py
"""Discrete node ids -> site-frame coordinates. Pure math, stdlib only.

All footprint constants are invented presentation values, provenance
"placeholder" — no entity in the contract has extents (design §1).
"""
from __future__ import annotations

import math

ZONE_RADIUS_M = 8.0
STATION_HALF_M = 3.0
TERRAIN_MARGIN_M = 20.0
CROWD_RING_RADIUS_M = 2.5


def build_layout_index(layout: dict) -> dict[str, tuple[float, float]]:
    def point(p: dict) -> tuple[float, float]:
        return (float(p["x_m"]), float(p["y_m"]))

    index = {
        "dispenser": point(layout["dispenser"]),
        "charger": point(layout["charger"]["position"]),
    }
    for zone in layout["zones"]:
        index[f"zone:{zone['zone_id']}"] = point(zone["position"])
    for station in layout["stations"]:
        index[f"station:{station['station_id']}"] = point(station["position"])
    return index


def resolve_location(node: str, index: dict[str, tuple[float, float]]) -> tuple[float, float]:
    if node not in index:
        raise KeyError(f"unknown location node {node!r}")
    return index[node]


def robot_offset(robot_id: str, all_ids: tuple[str, ...]) -> tuple[float, float]:
    ordered = sorted(all_ids)
    rank = ordered.index(robot_id)
    angle = 2.0 * math.pi * rank / max(1, len(ordered))
    return (
        CROWD_RING_RADIUS_M * math.cos(angle),
        CROWD_RING_RADIUS_M * math.sin(angle),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/twin/test_placement.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add nxt_range_twin/placement.py tests/twin/test_placement.py
git commit -m "feat(twin): node-grammar placement and deterministic co-location offsets"
```

### Task 6: Mapping — the checked derivation table

**Files:**
- Create: `nxt_range_twin/mapping.py`
- Test: `tests/twin/test_mapping.py`

**Interfaces:**
- Consumes: `FacilityState.to_dict()` shape (see `nxt_facility/state.py::to_dict` — top-level keys exactly: `meta, ball_flow, washer, demand, fleet, charging, staff, environment, robots, zones, stations`), `placement` (Task 5).
- Produces: `Opinion = tuple[str, str, str, object]` (prim_path, attr_name, sdf_type, value); `frame_opinions(state: dict, index: dict, robot_ids: tuple[str, ...]) -> list[Opinion]` — sorted by (prim_path, attr), raises `ValueError` on unknown top-level or per-entity keys; `EMITTED_ATTRS: frozenset[str]` (every `nxt:` attr name the twin may author, used by the Task 9 derivation audit); `SNAPSHOT_IGNORED_KEYS: frozenset[str]`; `HEALTH_COLORS: dict[str, tuple[float, float, float]]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/twin/test_mapping.py
import pytest

from nxt_range_twin.mapping import EMITTED_ATTRS, frame_opinions
from nxt_range_twin.placement import build_layout_index
from tests.twin.fixtures import LAYOUT, STATE  # Step 2 creates fixtures


def test_opinions_sorted_deterministic_and_traceable():
    index = build_layout_index(LAYOUT)
    ops = frame_opinions(STATE, index, ("R1", "R2"))
    assert ops == sorted(ops, key=lambda o: (o[0], o[1]))
    assert ops == frame_opinions(STATE, index, ("R1", "R2"))
    for _, attr, _, _ in ops:
        if attr.startswith("nxt:"):
            assert attr in EMITTED_ATTRS, attr


def test_robot_translate_uses_node_anchor_plus_offset():
    index = build_layout_index(LAYOUT)
    ops = frame_opinions(STATE, index, ("R1", "R2"))
    translates = {o[0]: o[3] for o in ops if o[1] == "xformOp:translate"}
    x, y, z = translates["/World/Site/Robots/R1"]
    assert z == 0.0
    # R1 at "zone:Z1" anchor (40,-25) plus its deterministic ring offset
    assert (x, y) != (40.0, -25.0)
    assert abs(x - 40.0) <= 2.5 and abs(y + 25.0) <= 2.5


def test_unknown_keys_fail_loud_both_directions():
    index = build_layout_index(LAYOUT)
    drifted = dict(STATE)
    drifted["new_group"] = {"x": 1}
    with pytest.raises(ValueError, match="new_group"):
        frame_opinions(drifted, index, ("R1", "R2"))
    drifted2 = dict(STATE)
    drifted2["robots"] = [dict(STATE["robots"][0], surprise=1)] + STATE["robots"][1:]
    with pytest.raises(ValueError, match="surprise"):
        frame_opinions(drifted2, index, ("R1", "R2"))
```

- [ ] **Step 2: Create the shared fixture module**

Hand-write a minimal-but-complete fixture matching the real contract exactly (field names verified against `nxt_facility/state.py:36-162` and `nxt_range_ops/core/entities.py:67-132`):

```python
# tests/twin/fixtures.py
"""Hand-built fixtures matching the real contracts key-for-key."""

LAYOUT = {
    "schema": "nxt-range-viewer/layout/v1",
    "disclaimer": "placeholder disclaimer",
    "coordinate_frame": {"units": "meters", "origin": "dispenser"},
    "dispenser": {"x_m": 0.0, "y_m": 0.0},
    "charger": {"position": {"x_m": 5.0, "y_m": -30.0}, "slots": 2},
    "zones": [
        {"zone_id": "Z1", "position": {"x_m": 40.0, "y_m": -25.0}, "landing_weight": 3,
         "closure_windows": []},
        {"zone_id": "Z2", "position": {"x_m": 70.0, "y_m": -10.0}, "landing_weight": 5,
         "closure_windows": []},
    ],
    "stations": [
        {"station_id": "H1", "position": {"x_m": 10.0, "y_m": -20.0}, "dock_slots": 2,
         "buffer_capacity_balls": 2500, "outage_windows": []},
    ],
    "robots": [
        {"robot_id": "R1", "payload_capacity_balls": 600, "initial_battery_frac": 1.0},
        {"robot_id": "R2", "payload_capacity_balls": 600, "initial_battery_frac": 1.0},
    ],
}

STATE = {
    "meta": {"t_s": 60.0, "minute_of_day": 361.0, "facility_open": True,
             "scenario_name": "fixture", "seed": 7},
    "ball_flow": {"total_balls": 100, "clean_available": 60, "clean_sensed": 58.5,
                  "in_wash": 10, "dirty_buffered": {"H1": 10}, "on_field": {"Z1": 12, "Z2": 3},
                  "in_transit": {"R1": 5, "R2": 0}, "conserved": True},
    "washer": {"throughput_balls_per_minute": 40.0, "batch_size_balls": 200, "wip": 10},
    "demand": {"forecast_balls_per_minute": [1.0, 2.0], "forecast_bucket_minutes": 60,
               "minutes_to_close": 900.0, "demand_balls_total": 40, "demand_balls_served": 38,
               "stockout_minutes": 0.0, "service_availability": 1.0},
    "fleet": {"total": 2, "operable": 2, "inoperative": 0, "charging": 0, "awaiting_human": 0},
    "charging": {"slots": 2, "in_use": 0, "queue_length": 0},
    "staff": {"capacity": 1, "busy": 0, "queued_requests": 0},
    "environment": {"wet_ground_speed_multiplier": 1.0, "zones_open": 2, "zones_total": 2,
                    "stations_open": 1, "stations_total": 1},
    "robots": [
        {"robot_id": "R1", "activity": "traveling", "health": "ok", "battery_frac": 0.9,
         "payload_balls": 5, "payload_capacity_balls": 600, "location": "zone:Z1",
         "destination": "station:H1", "assigned_zone": "Z1", "estop_latched": False,
         "awaiting_human": False},
        {"robot_id": "R2", "activity": "idle", "health": "ok", "battery_frac": 1.0,
         "payload_balls": 0, "payload_capacity_balls": 600, "location": "dispenser",
         "destination": None, "assigned_zone": None, "estop_latched": False,
         "awaiting_human": False},
    ],
    "zones": [
        {"zone_id": "Z1", "balls": 12, "is_open": True, "robots_present": 1},
        {"zone_id": "Z2", "balls": 3, "is_open": True, "robots_present": 0},
    ],
    "stations": [
        {"station_id": "H1", "is_open": True, "docked": 0, "queue_length": 0,
         "buffer_balls": 10, "buffer_capacity_balls": 2500},
    ],
}
```

Import note: the tests use `from tests.twin.fixtures import …`. If that fails under the repo's pytest configuration (depends on whether `tests/` is a package), check how `tests/facility/` imports shared helpers and match it — same-directory `from fixtures import …` is the fallback. Apply the chosen form consistently in Tasks 6, 8, and 9.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/twin/test_mapping.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement mapping.py**

The full mapping table, per design §2. Single-source rule: per-entity snapshot sections are authoritative for per-entity attrs; `ball_flow` per-entity dicts (`dirty_buffered`, `on_field`, `in_transit`) are NOT re-authored (they duplicate station/zone/robot fields); `ball_flow` aggregates land on `/World/Ops`.

```python
# nxt_range_twin/mapping.py
"""FacilityState dict -> USD opinions. The checked derivation table.

Every emitted nxt: attribute traces to a contract field; unknown input keys
abort; the Task 9 derivation audit walks built stages against EMITTED_ATTRS.
stdlib only — values are plain Python; pxr types are applied in overlay.py.
"""
from __future__ import annotations

from nxt_range_twin.placement import resolve_location, robot_offset

Opinion = tuple[str, str, str, object]  # (prim_path, attr, sdf_type, value)

SITE = "/World/Site"
OPS = "/World/Ops"

# Top-level state groups the mapping consumes or deliberately ignores.
CONSUMED_GROUPS = frozenset(
    {"meta", "ball_flow", "washer", "demand", "fleet", "charging", "staff",
     "environment", "robots", "zones", "stations"}
)
SNAPSHOT_IGNORED_KEYS: frozenset[str] = frozenset()  # nothing ignored in v1

ROBOT_KEYS = frozenset(
    {"robot_id", "activity", "health", "battery_frac", "payload_balls",
     "payload_capacity_balls", "location", "destination", "assigned_zone",
     "estop_latched", "awaiting_human"}
)
ZONE_KEYS = frozenset({"zone_id", "balls", "is_open", "robots_present"})
STATION_KEYS = frozenset(
    {"station_id", "is_open", "docked", "queue_length", "buffer_balls",
     "buffer_capacity_balls"}
)

HEALTH_COLORS = {
    "ok": (0.20, 0.75, 0.30),
    "degraded": (0.95, 0.75, 0.10),
    "failed": (0.90, 0.15, 0.15),
}
ESTOP_COLOR = (0.85, 0.10, 0.85)
AWAITING_COLOR = (0.15, 0.35, 0.95)

EMITTED_ATTRS = frozenset(
    {
        # /World/Ops (facility scoreboard)
        "nxt:t_s", "nxt:minute_of_day", "nxt:facility_open",
        "nxt:balls_total", "nxt:balls_conserved", "nxt:in_wash",
        "nxt:minutes_to_close", "nxt:demand_balls_total", "nxt:demand_balls_served",
        "nxt:stockout_minutes", "nxt:service_availability",
        "nxt:fleet_total", "nxt:fleet_operable", "nxt:fleet_inoperative",
        "nxt:fleet_charging", "nxt:fleet_awaiting_human",
        "nxt:wet_ground_speed_multiplier", "nxt:zones_open", "nxt:zones_total",
        "nxt:stations_open", "nxt:stations_total",
        # dispenser
        "nxt:clean_available", "nxt:clean_sensed",
        # aspatial washer / staff
        "nxt:wip", "nxt:throughput_balls_per_minute", "nxt:batch_size_balls",
        "nxt:staff_capacity", "nxt:staff_busy", "nxt:staff_queued_requests",
        # charger
        "nxt:slots", "nxt:in_use", "nxt:queue_length",
        # zones
        "nxt:balls", "nxt:is_open", "nxt:robots_present",
        # stations
        "nxt:docked", "nxt:buffer_balls", "nxt:buffer_capacity_balls",
        # robots
        "nxt:activity", "nxt:health", "nxt:battery_frac", "nxt:payload_balls",
        "nxt:payload_capacity_balls", "nxt:location", "nxt:destination",
        "nxt:assigned_zone", "nxt:estop_latched", "nxt:awaiting_human",
        # static (base layer)
        "nxt:landing_weight", "nxt:dock_slots", "nxt:provenance",
    }
)


def _check_keys(record: dict, allowed: frozenset, label: str) -> None:
    unknown = sorted(set(record) - allowed - SNAPSHOT_IGNORED_KEYS)
    if unknown:
        raise ValueError(f"unknown {label} keys (contract drift?): {unknown}")


def _robot_color(robot: dict) -> tuple[float, float, float]:
    if robot["estop_latched"]:
        return ESTOP_COLOR
    if robot["awaiting_human"]:
        return AWAITING_COLOR
    return HEALTH_COLORS.get(robot["health"], HEALTH_COLORS["degraded"])


def frame_opinions(
    state: dict,
    index: dict[str, tuple[float, float]],
    robot_ids: tuple[str, ...],
) -> list[Opinion]:
    _check_keys(state, CONSUMED_GROUPS, "state group")
    ops: list[Opinion] = []

    meta, flow = state["meta"], state["ball_flow"]
    demand, fleet = state["demand"], state["fleet"]
    env, staff = state["environment"], state["staff"]

    ops += [
        (OPS, "nxt:t_s", "double", float(meta["t_s"])),
        (OPS, "nxt:minute_of_day", "double", float(meta["minute_of_day"])),
        (OPS, "nxt:facility_open", "bool", bool(meta["facility_open"])),
        (OPS, "nxt:balls_total", "int", int(flow["total_balls"])),
        (OPS, "nxt:balls_conserved", "bool", bool(flow["conserved"])),
        (OPS, "nxt:in_wash", "int", int(flow["in_wash"])),
        (OPS, "nxt:minutes_to_close", "double", float(demand["minutes_to_close"])),
        (OPS, "nxt:demand_balls_total", "int", int(demand["demand_balls_total"])),
        (OPS, "nxt:demand_balls_served", "int", int(demand["demand_balls_served"])),
        (OPS, "nxt:stockout_minutes", "double", float(demand["stockout_minutes"])),
        (OPS, "nxt:service_availability", "double", float(demand["service_availability"])),
        (OPS, "nxt:fleet_total", "int", int(fleet["total"])),
        (OPS, "nxt:fleet_operable", "int", int(fleet["operable"])),
        (OPS, "nxt:fleet_inoperative", "int", int(fleet["inoperative"])),
        (OPS, "nxt:fleet_charging", "int", int(fleet["charging"])),
        (OPS, "nxt:fleet_awaiting_human", "int", int(fleet["awaiting_human"])),
        (OPS, "nxt:wet_ground_speed_multiplier", "double",
         float(env["wet_ground_speed_multiplier"])),
        (OPS, "nxt:zones_open", "int", int(env["zones_open"])),
        (OPS, "nxt:zones_total", "int", int(env["zones_total"])),
        (OPS, "nxt:stations_open", "int", int(env["stations_open"])),
        (OPS, "nxt:stations_total", "int", int(env["stations_total"])),
        (f"{SITE}/Dispenser", "nxt:clean_available", "int", int(flow["clean_available"])),
        (f"{SITE}/Dispenser", "nxt:clean_sensed", "double", float(flow["clean_sensed"])),
        (f"{SITE}/Aspatial/Washer", "nxt:wip", "int", int(state["washer"]["wip"])),
        (f"{SITE}/Aspatial/Staff", "nxt:staff_capacity", "int", int(staff["capacity"])),
        (f"{SITE}/Aspatial/Staff", "nxt:staff_busy", "int", int(staff["busy"])),
        (f"{SITE}/Aspatial/Staff", "nxt:staff_queued_requests", "int",
         int(staff["queued_requests"])),
        (f"{SITE}/Charger", "nxt:in_use", "int", int(state["charging"]["in_use"])),
        (f"{SITE}/Charger", "nxt:queue_length", "int", int(state["charging"]["queue_length"])),
    ]

    for zone in state["zones"]:
        _check_keys(zone, ZONE_KEYS, "zone")
        prim = f"{SITE}/Zones/{zone['zone_id']}"
        ops += [
            (prim, "nxt:balls", "int", int(zone["balls"])),
            (prim, "nxt:is_open", "bool", bool(zone["is_open"])),
            (prim, "nxt:robots_present", "int", int(zone["robots_present"])),
        ]

    for station in state["stations"]:
        _check_keys(station, STATION_KEYS, "station")
        prim = f"{SITE}/Stations/{station['station_id']}"
        ops += [
            (prim, "nxt:is_open", "bool", bool(station["is_open"])),
            (prim, "nxt:docked", "int", int(station["docked"])),
            (prim, "nxt:queue_length", "int", int(station["queue_length"])),
            (prim, "nxt:buffer_balls", "int", int(station["buffer_balls"])),
        ]

    for robot in state["robots"]:
        _check_keys(robot, ROBOT_KEYS, "robot")
        prim = f"{SITE}/Robots/{robot['robot_id']}"
        anchor = resolve_location(robot["location"], index)
        dx, dy = robot_offset(robot["robot_id"], robot_ids)
        ops += [
            (prim, "xformOp:translate", "double3",
             (anchor[0] + dx, anchor[1] + dy, 0.0)),
            (prim, "primvars:displayColor", "color3f[]", [_robot_color(robot)]),
            (prim, "nxt:activity", "token", str(robot["activity"])),
            (prim, "nxt:health", "token", str(robot["health"])),
            (prim, "nxt:battery_frac", "double", float(robot["battery_frac"])),
            (prim, "nxt:payload_balls", "int", int(robot["payload_balls"])),
            (prim, "nxt:location", "token", str(robot["location"])),
            (prim, "nxt:destination", "token", str(robot["destination"] or "")),
            (prim, "nxt:assigned_zone", "token", str(robot["assigned_zone"] or "")),
            (prim, "nxt:estop_latched", "bool", bool(robot["estop_latched"])),
            (prim, "nxt:awaiting_human", "bool", bool(robot["awaiting_human"])),
        ]

    ops.sort(key=lambda o: (o[0], o[1]))
    return ops
```

Note: `washer.throughput_balls_per_minute` / `batch_size_balls` and per-robot `payload_capacity_balls` are authored ONCE from the first snapshot by `overlay.py` (Task 8) — they are contract fields but constant; do not re-author per frame. `demand.forecast_balls_per_minute` / `forecast_bucket_minutes` are consumed by `_check_keys` (part of `demand`) but not emitted in v1 — the frozen forecast lives in the briefings sidecar's narration; do NOT add them to `EMITTED_ATTRS`.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/twin/test_mapping.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add nxt_range_twin/mapping.py tests/twin/fixtures.py tests/twin/test_mapping.py
git commit -m "feat(twin): checked FacilityState->USD derivation table with fail-loud drift guards"
```

### Task 7: Static base layer

**Files:**
- Create: `nxt_range_twin/base.py`
- Test: `tests/twin/test_base.py`

**Interfaces:**
- Consumes: `placement` constants and `build_layout_index` (Task 5); layout dict.
- Produces: `build_base_layer(layout: dict, meta: dict, out_path: Path) -> None` authoring `range_base.usda`: `/World` (Xform, defaultPrim), `/World/Site/{Terrain, Dispenser, Zones/<id>, Stations/<id>, Charger, Aspatial/{Washer,Staff}}`, `/World/Ops` (Scope), `/World/Env/{SunLight, MainCamera}`; upAxis=Z, metersPerUnit=1; every invented-geometry prim carries customData `{"nxt:provenance": "placeholder"}`; `customLayerData` = `{"schema": "nxt-range-twin/stage/v1", "twin_version", "disclaimer", "site_id", "deployment_id", "episode_id", "scenario_name", "seed"}`; static attrs `nxt:landing_weight` (zones), `nxt:dock_slots`, `nxt:buffer_capacity_balls` (stations), `nxt:slots` (charger), `nxt:throughput_balls_per_minute`, `nxt:batch_size_balls` (washer, from `meta["washer_static"]` — passed by the CLI from the first snapshot).

- [ ] **Step 1: Write the failing test**

```python
# tests/twin/test_base.py
from pathlib import Path

import pytest

pxr = pytest.importorskip("pxr")
from pxr import Usd, UsdGeom  # noqa: E402

from nxt_range_twin.base import build_base_layer  # noqa: E402
from tests.twin.fixtures import LAYOUT  # noqa: E402

META = {
    "schema_stage": "nxt-range-twin/stage/v1",
    "site_id": "s", "deployment_id": "d", "episode_id": "fixture-seed7",
    "scenario_name": "fixture", "seed": 7, "disclaimer": "placeholder disclaimer",
    "washer_static": {"throughput_balls_per_minute": 40.0, "batch_size_balls": 200},
}


def _build(tmp_path: Path) -> Usd.Stage:
    out = tmp_path / "range_base.usda"
    build_base_layer(LAYOUT, META, out)
    return Usd.Stage.Open(str(out))


def test_stage_metadata_and_prim_tree(tmp_path: Path):
    stage = _build(tmp_path)
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    assert stage.GetDefaultPrim().GetPath().pathString == "/World"
    for path in ("/World/Site/Terrain", "/World/Site/Dispenser",
                 "/World/Site/Zones/Z1", "/World/Site/Zones/Z2",
                 "/World/Site/Stations/H1", "/World/Site/Charger",
                 "/World/Site/Aspatial/Washer", "/World/Site/Aspatial/Staff",
                 "/World/Ops", "/World/Env/SunLight", "/World/Env/MainCamera"):
        assert stage.GetPrimAtPath(path).IsValid(), path


def test_geometry_matches_layout_and_aspatial_has_no_transform(tmp_path: Path):
    stage = _build(tmp_path)
    z1 = UsdGeom.Xformable(stage.GetPrimAtPath("/World/Site/Zones/Z1"))
    translate = z1.GetOrderedXformOps()[0].Get()
    assert (translate[0], translate[1]) == (40.0, -25.0)
    washer = stage.GetPrimAtPath("/World/Site/Aspatial/Washer")
    assert not UsdGeom.Xformable(washer).GetOrderedXformOps()  # aspatial: no transform
    assert washer.GetAttribute("nxt:throughput_balls_per_minute").Get() == 40.0


def test_provenance_and_disclaimer(tmp_path: Path):
    stage = _build(tmp_path)
    layer_data = stage.GetRootLayer().customLayerData
    assert layer_data["disclaimer"] == "placeholder disclaimer"
    assert layer_data["schema"] == "nxt-range-twin/stage/v1"
    terrain = stage.GetPrimAtPath("/World/Site/Terrain")
    assert terrain.GetCustomDataByKey("nxt:provenance") == "placeholder"
    zone = stage.GetPrimAtPath("/World/Site/Zones/Z1")
    assert zone.GetCustomDataByKey("nxt:provenance") == "placeholder"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/twin/test_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nxt_range_twin.base'`

- [ ] **Step 3: Implement base.py**

Implementation guidance (write real code, this sketch pins the API usage):

```python
# nxt_range_twin/base.py
"""Static site layer (range_base.usda) authored once from layout.json."""
from __future__ import annotations

from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux

from nxt_range_twin import TWIN_VERSION
from nxt_range_twin.placement import (
    STATION_HALF_M,
    TERRAIN_MARGIN_M,
    ZONE_RADIUS_M,
    build_layout_index,
)

_PLACEHOLDER = "placeholder"


def _mark_placeholder(prim: Usd.Prim) -> None:
    prim.SetCustomDataByKey("nxt:provenance", _PLACEHOLDER)


def _xform_at(stage: Usd.Stage, path: str, x: float, y: float) -> Usd.Prim:
    xform = UsdGeom.Xform.Define(stage, path)
    xform.AddTranslateOp().Set(Gf.Vec3d(x, y, 0.0))
    return xform.GetPrim()


def build_base_layer(layout: dict, meta: dict, out_path: Path) -> None:
    stage = Usd.Stage.CreateNew(str(out_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.Xform.Define(stage, "/World/Site")
    UsdGeom.Scope.Define(stage, "/World/Ops")

    index = build_layout_index(layout)

    # Terrain: flat plane bounding all anchors + margin (invented).
    xs = [p[0] for p in index.values()]
    ys = [p[1] for p in index.values()]
    terrain = UsdGeom.Cube.Define(stage, "/World/Site/Terrain")
    # scale a unit cube into a thin ground slab centred on the layout bounds
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    sx = (max(xs) - min(xs)) / 2.0 + TERRAIN_MARGIN_M
    sy = (max(ys) - min(ys)) / 2.0 + TERRAIN_MARGIN_M
    terrain.AddTranslateOp().Set(Gf.Vec3d(cx, cy, -0.05))
    terrain.AddScaleOp().Set(Gf.Vec3f(sx, sy, 0.05))
    _mark_placeholder(terrain.GetPrim())

    # Dispenser (+ fill-scaled BallPile authored by overlay), zones, stations, charger.
    _mark_placeholder(_xform_at(stage, "/World/Site/Dispenser", *index["dispenser"]))
    for zone in layout["zones"]:
        prim = _xform_at(stage, f"/World/Site/Zones/{zone['zone_id']}",
                         *index[f"zone:{zone['zone_id']}"])
        disc = UsdGeom.Cylinder.Define(stage, prim.GetPath().AppendChild("Extent"))
        disc.GetRadiusAttr().Set(ZONE_RADIUS_M)
        disc.GetHeightAttr().Set(0.1)
        disc.GetAxisAttr().Set(UsdGeom.Tokens.z)
        _mark_placeholder(prim)
        _mark_placeholder(disc.GetPrim())
        attr = prim.CreateAttribute("nxt:landing_weight", Sdf.ValueTypeNames.Int)
        attr.Set(int(zone["landing_weight"]))
    for station in layout["stations"]:
        prim = _xform_at(stage, f"/World/Site/Stations/{station['station_id']}",
                         *index[f"station:{station['station_id']}"])
        pad = UsdGeom.Cube.Define(stage, prim.GetPath().AppendChild("Pad"))
        pad.AddScaleOp().Set(Gf.Vec3f(STATION_HALF_M, STATION_HALF_M, 0.5))
        _mark_placeholder(prim)
        _mark_placeholder(pad.GetPrim())
        prim.CreateAttribute("nxt:dock_slots", Sdf.ValueTypeNames.Int).Set(
            int(station["dock_slots"]))
        prim.CreateAttribute("nxt:buffer_capacity_balls", Sdf.ValueTypeNames.Int).Set(
            int(station["buffer_capacity_balls"]))
    charger = _xform_at(stage, "/World/Site/Charger", *index["charger"])
    _mark_placeholder(charger)
    charger.CreateAttribute("nxt:slots", Sdf.ValueTypeNames.Int).Set(
        int(layout["charger"]["slots"]))

    # Aspatial: attributes, NO transform (design §1 — washer/staff aspatial).
    aspatial = UsdGeom.Scope.Define(stage, "/World/Site/Aspatial")
    washer = stage.DefinePrim("/World/Site/Aspatial/Washer")
    washer.CreateAttribute("nxt:throughput_balls_per_minute",
                           Sdf.ValueTypeNames.Double).Set(
        float(meta["washer_static"]["throughput_balls_per_minute"]))
    washer.CreateAttribute("nxt:batch_size_balls", Sdf.ValueTypeNames.Int).Set(
        int(meta["washer_static"]["batch_size_balls"]))
    stage.DefinePrim("/World/Site/Aspatial/Staff")

    # One light + one camera so a remote render works out of the box.
    UsdLux.DistantLight.Define(stage, "/World/Env/SunLight")
    cam = UsdGeom.Camera.Define(stage, "/World/Env/MainCamera")
    cam.AddTranslateOp().Set(Gf.Vec3d(120.0, -140.0, 90.0))
    _mark_placeholder(cam.GetPrim())

    stage.GetRootLayer().customLayerData = {
        "schema": "nxt-range-twin/stage/v1",
        "twin_version": TWIN_VERSION,
        "disclaimer": str(meta["disclaimer"]),
        "site_id": str(meta["site_id"]),
        "deployment_id": str(meta["deployment_id"]),
        "episode_id": str(meta["episode_id"]),
        "scenario_name": str(meta["scenario_name"]),
        "seed": int(meta["seed"]),
    }
    world.GetPrim().SetDocumentation(str(meta["disclaimer"]))
    stage.GetRootLayer().Save()
```

If any pxr API name differs (e.g. `AddTranslateOp` availability on `Cube`), consult the installed `usd-core` — `python -c "from pxr import UsdGeom; help(UsdGeom.Cylinder)"` — and adjust; the tests define the required behavior.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/twin/test_base.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add nxt_range_twin/base.py tests/twin/test_base.py
git commit -m "feat(twin): static base layer with provenance-tagged placeholder geometry"
```

### Task 8: Episode overlay layer + CLI

**Files:**
- Create: `nxt_range_twin/overlay.py`, `nxt_range_twin/__main__.py`
- Test: `tests/twin/test_overlay.py`

**Interfaces:**
- Consumes: `frame_opinions`/`EMITTED_ATTRS` (Task 6), `build_base_layer` (Task 7), `stream.read_jsonl`/`validate_stream_meta` (Task 1).
- Produces: `build_episode_layer(layout: dict, states: list[dict], meta: dict, out_dir: Path) -> Path` writing `range_base.usda` + `episode.usda` (+ empty `estimates.usda`) into `out_dir`, returning the `episode.usda` path. `episode.usda`: `subLayers = [./estimates.usda, ./range_base.usda]`, `startTimeCode = states[0].meta.t_s`, `endTimeCode = states[-1].meta.t_s`, `timeCodesPerSecond = 600`, `framesPerSecond = 600`. Held-translate rule: author each robot translate at every sample time `t_i`; when the value CHANGES at `t_{i+1}`, also author the old value at `t_{i+1} - 1.0` (1 sim-second — a visually instant teleport at 600 tcps, no interpolated glide). Scalar `nxt:` attrs are authored at every sample (ints/bools/tokens hold naturally). CLI: `python -m nxt_range_twin --episode-dir <dir>` reads `layout.json` + `facility_states.jsonl` + `stream.meta.json` from an episode dir and writes `usd/` beside them.

- [ ] **Step 1: Write the failing test**

```python
# tests/twin/test_overlay.py
from pathlib import Path

import pytest

pxr = pytest.importorskip("pxr")
from pxr import Usd  # noqa: E402

from nxt_range_twin.overlay import build_episode_layer  # noqa: E402
from tests.twin.fixtures import LAYOUT, STATE  # noqa: E402

META = {
    "site_id": "s", "deployment_id": "d", "episode_id": "fixture-seed7",
    "scenario_name": "fixture", "seed": 7, "disclaimer": "placeholder disclaimer",
}


def _three_states() -> list[dict]:
    import copy
    s0 = copy.deepcopy(STATE)
    s0["meta"]["t_s"] = 0.0
    s0["robots"][0]["location"] = "dispenser"
    s1 = copy.deepcopy(STATE)   # R1 at zone:Z1, t=60
    s2 = copy.deepcopy(STATE)
    s2["meta"]["t_s"] = 120.0   # R1 still at zone:Z1
    return [s0, s1, s2]


def _open(tmp_path: Path) -> Usd.Stage:
    path = build_episode_layer(LAYOUT, _three_states(), META, tmp_path)
    return Usd.Stage.Open(str(path))


def test_stage_composes_with_time_range_and_tcps(tmp_path: Path):
    stage = _open(tmp_path)
    assert stage.GetStartTimeCode() == 0.0
    assert stage.GetEndTimeCode() == 120.0
    assert stage.GetTimeCodesPerSecond() == 600.0
    assert stage.GetPrimAtPath("/World/Site/Zones/Z1").IsValid()  # base composed


def test_held_translate_no_interpolated_glide(tmp_path: Path):
    stage = _open(tmp_path)
    attr = stage.GetPrimAtPath("/World/Site/Robots/R1").GetAttribute("xformOp:translate")
    at_0 = attr.Get(0.0)
    at_59 = attr.Get(59.0)   # 1 tick before the change lands
    at_60 = attr.Get(60.0)
    # held until t=59 (old-value sample at 60-1), teleport by t=60
    assert at_59 == at_0
    assert at_60 != at_0


def test_timesampled_scalars_and_byte_reproducible_build(tmp_path: Path):
    path_a = build_episode_layer(LAYOUT, _three_states(), META, tmp_path / "a")
    path_b = build_episode_layer(LAYOUT, _three_states(), META, tmp_path / "b")
    assert path_a.read_bytes() == path_b.read_bytes()
    assert (path_a.parent / "range_base.usda").read_bytes() == \
        (path_b.parent / "range_base.usda").read_bytes()
    stage = Usd.Stage.Open(str(path_a))
    balls = stage.GetPrimAtPath("/World/Site/Zones/Z1").GetAttribute("nxt:balls")
    assert balls.Get(60.0) == 12


def test_estimates_layer_exists_and_is_empty(tmp_path: Path):
    """Reserved growth seam — must stay empty in v1 (design §3)."""
    path = build_episode_layer(LAYOUT, _three_states(), META, tmp_path)
    from pxr import Sdf
    est = Sdf.Layer.FindOrOpen(str(path.parent / "estimates.usda"))
    assert est is not None
    assert not est.rootPrims  # zero prims
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/twin/test_overlay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nxt_range_twin.overlay'`

- [ ] **Step 3: Implement overlay.py + __main__.py**

`overlay.py` responsibilities (implement fully):
1. `build_base_layer(layout, {**meta, "washer_static": {...from states[0]["washer"]...}}, out_dir / "range_base.usda")`.
2. Create `estimates.usda` via `Sdf.Layer.CreateNew` with a one-line comment (`layer.comment = "reserved: nxt:est:* estimates — empty in v1 by design"`), zero prims, save.
3. Create `episode.usda` with `Usd.Stage.CreateNew`; set `subLayerPaths = ["./estimates.usda", "./range_base.usda"]` on its root layer; set start/end time codes from `states[0]/[-1]["meta"]["t_s"]`, `SetTimeCodesPerSecond(600)`, `SetFramesPerSecond(600)`.
4. For each state: `ops = frame_opinions(state, index, robot_ids)` (robot_ids from `layout["robots"]`, sorted). For each opinion: define `over` prims lazily (`stage.OverridePrim(prim_path)` — robots get `OverridePrim` too; their transforms exist only in the overlay), create the attribute with the mapped `Sdf.ValueTypeNames` entry, `attr.Set(value, time_code=t_s)`.
5. Held-translate rule from the Interfaces block: track previous translate per robot; on change at `t_i`, also author previous value at `t_i - 1.0` BEFORE the new sample.
6. Dispenser ball-pile affordance: author `xformOp:scale` on `/World/Site/Dispenser/BallPile` (define the `Cylinder` in `base.py` with radius 2.0, height 1.0, placeholder-tagged) as `(1, 1, max(0.02, clean_available / max(1, total_balls)))` per sample — the one inventory→geometry affordance.
7. Copy the base-layer `customLayerData` onto the episode root layer, plus `"n_records": len(states)` and, when present in `meta`, `"input_sha256"` (the manifest hashes stamped by `__main__.py`).
8. Deterministic ordering: iterate states in order, opinions pre-sorted by `frame_opinions`; never touch wall-clock.

`__main__.py`:

```python
# nxt_range_twin/__main__.py
"""CLI: build USD from a captured episode dir (layout.json + facility_states.jsonl)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from nxt_range_twin.overlay import build_episode_layer
from nxt_range_twin.stream import read_jsonl, validate_stream_meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="default: <episode-dir>/usd")
    args = parser.parse_args()
    layout = json.loads((args.episode_dir / "layout.json").read_text())
    meta = json.loads((args.episode_dir / "stream.meta.json").read_text())
    validate_stream_meta(meta)
    states = read_jsonl(args.episode_dir / "facility_states.jsonl")
    # Manifest input hashes (design §4.4): hand-patched artifacts are detectable
    # because rebuilt customLayerData hashes stop matching the inputs.
    meta = dict(meta)
    meta["input_sha256"] = {
        name: hashlib.sha256((args.episode_dir / name).read_bytes()).hexdigest()
        for name in ("layout.json", "facility_states.jsonl")
    }
    out_dir = args.out or (args.episode_dir / "usd")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(build_episode_layer(layout, states, meta, out_dir))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/twin/test_overlay.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add nxt_range_twin/overlay.py nxt_range_twin/__main__.py tests/twin/test_overlay.py
git commit -m "feat(twin): timesampled episode overlay with held teleports and CLI"
```

### Task 9: Package guard tests — boundaries and derivation audit

**Files:**
- Test: `tests/twin/test_guards_package.py`

**Interfaces:**
- Consumes: everything above; the blocked-import subprocess pattern from `tests/facility/test_state.py::test_contract_importable_without_simulation_stack` (copy its blocker mechanism).

- [ ] **Step 1: Write the tests**

```python
# tests/twin/test_guards_package.py
"""Boundary and derivation guards for the twin package."""
import subprocess
import sys
from pathlib import Path

import pytest

SIM_ROOT = Path(__file__).resolve().parents[2]
TWIN_MODULES = ["nxt_range_twin", "nxt_range_twin.stream", "nxt_range_twin.placement",
                "nxt_range_twin.mapping"]
USD_MODULES = ["nxt_range_twin.base", "nxt_range_twin.overlay"]
SIM_PACKAGES = ["nxt_range_ops", "nxt_sim", "nxt_facility", "nxt_memory",
                "nxt_range_viewer", "nxt_range_agent"]
SIM_LIBS = ["simpy", "gymnasium", "numpy", "pydantic", "pyarrow", "streamlit"]

# NOTE: find_spec, not find_module — find_module was removed in Python 3.12,
# so a find_module-based blocker silently blocks nothing on the repo's 3.13.
# If tests/facility/test_state.py already has a working blocker, reuse it.
BLOCKER = (
    "import sys\n"
    "class _Block:\n"
    "    def __init__(self, names): self.names = set(names)\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name.split('.')[0] in self.names:\n"
    "            raise ImportError(f'blocked: {{name}}')\n"
    "        return None\n"
    "sys.meta_path.insert(0, _Block({blocked!r}))\n"
    "import {module}\n"
)


def _import_with_blocked(module: str, blocked: list[str]) -> subprocess.CompletedProcess:
    code = BLOCKER.format(blocked=blocked, module=module)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=SIM_ROOT, capture_output=True, text=True
    )


@pytest.mark.parametrize("module", TWIN_MODULES)
def test_pure_modules_import_with_sim_and_pxr_blocked(module):
    result = _import_with_blocked(module, SIM_PACKAGES + SIM_LIBS + ["pxr"])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", USD_MODULES)
def test_usd_modules_import_with_sim_blocked(module):
    pytest.importorskip("pxr")
    result = _import_with_blocked(module, SIM_PACKAGES + SIM_LIBS)
    assert result.returncode == 0, result.stderr


def test_twin_source_never_mentions_sim_packages():
    for path in (SIM_ROOT / "nxt_range_twin").glob("*.py"):
        text = path.read_text()
        for package in SIM_PACKAGES:
            assert package not in text, f"{path} mentions {package}"


def test_derivation_audit_every_nxt_attr_is_in_the_table(tmp_path):
    """Walk a built stage: every authored nxt: attr must trace to EMITTED_ATTRS."""
    pxr = pytest.importorskip("pxr")
    from pxr import Usd
    from nxt_range_twin.mapping import EMITTED_ATTRS
    from nxt_range_twin.overlay import build_episode_layer
    from tests.twin.fixtures import LAYOUT, STATE

    meta = {"site_id": "s", "deployment_id": "d", "episode_id": "fixture-seed7",
            "scenario_name": "fixture", "seed": 7, "disclaimer": "x"}
    path = build_episode_layer(LAYOUT, [STATE], meta, tmp_path)
    stage = Usd.Stage.Open(str(path))
    for prim in stage.Traverse():
        for attr in prim.GetAttributes():
            name = attr.GetName()
            if name.startswith("nxt:"):
                assert name in EMITTED_ATTRS, f"{prim.GetPath()}.{name} not in mapping table"


def test_no_physics_schemas_applied(tmp_path):
    pxr = pytest.importorskip("pxr")
    from pxr import Usd
    from nxt_range_twin.overlay import build_episode_layer
    from tests.twin.fixtures import LAYOUT, STATE

    meta = {"site_id": "s", "deployment_id": "d", "episode_id": "fixture-seed7",
            "scenario_name": "fixture", "seed": 7, "disclaimer": "x"}
    path = build_episode_layer(LAYOUT, [STATE], meta, tmp_path)
    text = path.read_text() + (path.parent / "range_base.usda").read_text()
    assert "Physics" not in text  # no UsdPhysics schema names anywhere


def test_base_layer_geometry_equals_layout_positions(tmp_path):
    """Cross-artifact equality: layout.json is the only geometry derivation path."""
    pxr = pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    from nxt_range_twin.base import build_base_layer
    from tests.twin.fixtures import LAYOUT

    meta = {"site_id": "s", "deployment_id": "d", "episode_id": "e",
            "scenario_name": "fixture", "seed": 7, "disclaimer": "x",
            "washer_static": {"throughput_balls_per_minute": 40.0,
                              "batch_size_balls": 200}}
    out = tmp_path / "range_base.usda"
    build_base_layer(LAYOUT, meta, out)
    stage = Usd.Stage.Open(str(out))
    for zone in LAYOUT["zones"]:
        prim = stage.GetPrimAtPath(f"/World/Site/Zones/{zone['zone_id']}")
        t = UsdGeom.Xformable(prim).GetOrderedXformOps()[0].Get()
        assert (t[0], t[1]) == (zone["position"]["x_m"], zone["position"]["y_m"])
```

- [ ] **Step 2: Run and fix until green**

Run: `.venv/bin/python -m pytest tests/twin/test_guards_package.py -v`
Expected: all pass. The derivation audit failing means `overlay.py`/`base.py` author an attr missing from `EMITTED_ATTRS` — add it to the table (with its source field named in a comment) or stop authoring it; never weaken the test.

- [ ] **Step 3: Run the whole twin suite + existing suites (regression check)**

Run: `.venv/bin/python -m pytest tests/twin tests/facility tests/memory -q`
Expected: all pass; upstream suites unaffected.

- [ ] **Step 4: Commit**

```bash
git add tests/twin/test_guards_package.py
git commit -m "test(twin): import-boundary, derivation-audit, and cross-artifact guards"
```

---

## Phase C — synchronized visualization

### Task 10: Briefing panel in the Streamlit viewer

**Files:**
- Create: `nxt_range_demo/briefing_panel.py`
- Modify: `nxt_range_demo/app.py` (additive only — one sidebar/section call; find the frame-scrub slider and render the panel below it)
- Test: `tests/twin/test_briefing_panel.py`

**Interfaces:**
- Consumes: `briefings.jsonl` sidecar records `{"seq": int, "t_s": float, "briefing": str, "recommendations": [dict]}` (Task 2).
- Produces: `load_briefings(path: Path) -> list[dict]` (sorted by `t_s`), `briefing_for_time(briefings: list[dict], t_s: float) -> dict | None` (latest record with `record["t_s"] <= t_s`; `None` before the first), `render_panel(st, briefings, t_s) -> None` (pure Streamlit calls; match by `t_s`, never by frame index — capture holds n_steps+1 records vs n_steps frames).

- [ ] **Step 1: Write the failing test**

```python
# tests/twin/test_briefing_panel.py
"""Panel data logic is pure and testable without streamlit."""
from pathlib import Path

from nxt_range_demo.briefing_panel import briefing_for_time, load_briefings


def _write(tmp_path: Path) -> Path:
    lines = [
        '{"briefing":"b0","recommendations":[],"seq":0,"t_s":0.0}',
        '{"briefing":"b1","recommendations":[{"rule_id":"r"}],"seq":1,"t_s":60.0}',
        '{"briefing":"b2","recommendations":[],"seq":2,"t_s":120.0}',
    ]
    p = tmp_path / "briefings.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_load_and_lookup_by_time(tmp_path: Path):
    briefings = load_briefings(_write(tmp_path))
    assert [b["seq"] for b in briefings] == [0, 1, 2]
    assert briefing_for_time(briefings, 0.0)["briefing"] == "b0"
    assert briefing_for_time(briefings, 59.9)["briefing"] == "b0"
    assert briefing_for_time(briefings, 60.0)["briefing"] == "b1"
    assert briefing_for_time(briefings, 999.0)["briefing"] == "b2"
    assert briefing_for_time(briefings, -1.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/twin/test_briefing_panel.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# nxt_range_demo/briefing_panel.py
"""Manager-briefing side panel: scrub-synced narration of the same state.

Reads the demo-tier briefings.jsonl sidecar (precomputed at capture time by
scripts/facility_twin_capture.py — the decision layer runs at capture, never
in the viewer). Matches by sim time t_s, never frame index.
"""
from __future__ import annotations

import bisect
import json
from pathlib import Path
from typing import Optional


def load_briefings(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    return sorted(records, key=lambda r: r["t_s"])


def briefing_for_time(briefings: list[dict], t_s: float) -> Optional[dict]:
    times = [r["t_s"] for r in briefings]
    idx = bisect.bisect_right(times, t_s) - 1
    return briefings[idx] if idx >= 0 else None


def render_panel(st, briefings: list[dict], t_s: float) -> None:
    record = briefing_for_time(briefings, t_s)
    if record is None:
        st.caption("No briefing yet at this time.")
        return
    st.subheader("Manager briefing (deterministic)")
    st.text(record["briefing"])
    for rec in record["recommendations"]:
        st.markdown(f"- **{rec.get('urgency', '?')}** · {rec.get('rule_id', '?')}: "
                    f"{rec.get('action', rec.get('rationale', ''))}")
```

Then in `nxt_range_demo/app.py`: locate the frame-scrub control (the slider driving frame selection) and, where the current frame's `t_s` is known, add:

```python
from nxt_range_demo.briefing_panel import load_briefings, render_panel

# after episode loading — sidecar path derived from the episode dir; feature
# is silently absent when no sidecar exists (viewer behavior unchanged):
briefings_path = episode_dir / "briefings.jsonl"  # adjust to app's path vars
briefings = load_briefings(briefings_path) if briefings_path.exists() else []

# after the scrub slider, where current_frame["t_s"] is in hand:
if briefings:
    render_panel(st, briefings, float(current_frame["t_s"]))
```

The executor must adapt the two integration points to `app.py`'s actual variable names (read the file first); the panel must be additive — every existing viewer behavior unchanged when no sidecar is present. Optionally accept a `--briefings` path / sidebar file picker consistent with how `app.py` already locates `episode.json`.

- [ ] **Step 4: Run test + existing demo tests**

Run: `.venv/bin/python -m pytest tests/twin/test_briefing_panel.py -q && .venv/bin/python -m pytest tests -q -k "demo or viewer"`
Expected: panel test passes; existing viewer/demo tests unaffected.

- [ ] **Step 5: Commit**

```bash
git add nxt_range_demo/briefing_panel.py nxt_range_demo/app.py tests/twin/test_briefing_panel.py
git commit -m "feat(twin): scrub-synced manager-briefing panel in the Streamlit viewer"
```

### Task 11: End-to-end demo assembly + docs

**Files:**
- Create: `scripts/twin_demo.sh`
- Modify: `docs/spatial_twin_design.md` (append a short "Status" line), `docs/architecture.md` (one paragraph adding the twin layer to the package map)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the demo driver**

```bash
#!/usr/bin/env bash
# scripts/twin_demo.sh — capture -> USD -> viewer, one command.
set -euo pipefail
cd "$(dirname "$0")/.."

SCENARIO="${1:-handoff_station_outage}"
SEED="${2:-7}"

.venv/bin/python scripts/facility_twin_capture.py \
  --scenario "$SCENARIO" --seed "$SEED"

EPISODE="reports/digital_twin/sim-baseline/dev/${SCENARIO}-seed${SEED}"
.venv/bin/python -m nxt_range_twin --episode-dir "$EPISODE"

echo "USD stage: ${EPISODE}/usd/episode.usda"
echo "Validate:  .venv/bin/usdchecker ${EPISODE}/usd/episode.usda"
echo "Viewer:    (launch the nxt_range_demo Streamlit app per its README;"
echo "            briefings sidecar: reports/demo/${SCENARIO}-seed${SEED}/briefings.jsonl)"
```

- [ ] **Step 2: Run it end to end**

Run: `bash scripts/twin_demo.sh handoff_station_outage 7`
Expected: prints the episode.usda path. Then run `.venv/bin/usdchecker reports/digital_twin/sim-baseline/dev/handoff_station_outage-seed7/usd/episode.usda` (usdchecker ships with the usd-core wheel; if the binary is absent, `python -c "from pxr import Usd; assert Usd.Stage.Open('<path>')"` is the fallback validation) — expected: `Success!` / no errors. Also run the robot_failure scenario once (`bash scripts/twin_demo.sh robot_failure 7`) — both disruption candidates must capture cleanly; the demo rehearsal picks between them.

- [ ] **Step 3: Launch the viewer and verify the sync visually**

Start the Streamlit app per `nxt_range_demo`'s existing run instructions against the exported episode; confirm: scrubbing moves the map AND the briefing panel together; the disruption (station outage / robot failure) appears in both; no regression with a sidecar-less episode.

- [ ] **Step 4: Docs**

Append to `docs/spatial_twin_design.md`: `Status: implemented through Phase C (Tasks 1–11); hero 3D render deferred pending remote GPU (W1 book-or-cut).` Add to `docs/architecture.md` package map: one paragraph — `nxt_range_twin`: digital twin / spatial intelligence layer for managed outdoor facilities; projection-only consumer of `facility-state-stream/v1` + `layout.json`; stdlib+pxr; guard-tested boundaries (mirror the phrasing of the nxt_memory entry).

- [ ] **Step 5: Final full-suite run + commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: entire suite green.

```bash
git add scripts/twin_demo.sh docs/spatial_twin_design.md docs/architecture.md
git commit -m "feat(twin): end-to-end demo driver and architecture docs"
```

---

## Deferred (named seams, NOT in this plan)

- Hero Omniverse render (`demo_hero.mp4`): remote Linux+NVIDIA ovrtx; W1 book-or-cut gate; never blocks the milestone floor.
- Live mode: `time_code=None` seam reserved in the design; zero live code ships.
- `estimates.usda` content, `SpatialSummary` contract, USD→ScenarioConfig importer, real-sensor adapters: growth seams per design §5.
