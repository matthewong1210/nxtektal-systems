"""Regression guards: the facility layer must be invisible to the simulator.

Four guarantees, per the approved design:
1. Building a FacilityState consumes no RNG from any of the five streams.
2. An instrumented episode (snapshot every step) produces a byte-identical
   event log and final metrics vs an uninstrumented one.
3. The builder never calls the RNG-drawing sensed accessors (static scan).
4. The upstream packages are byte-identical after facility use, and no
   upstream file mentions nxt_facility (one-way boundary).
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from nxt_range_ops.config.models import OperatingHoursConfig
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.scenarios.generators import make_scenario

from nxt_facility.analysis import classify_state, derive_indicators, estimate_stockout
from nxt_facility.build import build_facility_state

from .conftest import rng_states

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_PACKAGES = (
    "nxt_sim",
    "nxt_range_ops",
    "nxt_range_agent",
    "nxt_range_viewer",
    "nxt_range_demo",
)

BANNED_ACCESSORS = {"sensed_zone_counts", "sensed_battery_frac"}


def short_day_scenario():
    """Two-hour operating day for fast full-episode runs."""
    scenario = make_scenario("normal_weekday")
    return scenario.model_copy(
        update={"hours": OperatingHoursConfig(open_minute=360, close_minute=480)}
    )


def run_episode(scenario, seed: int, instrumented: bool):
    """One full baseline-driven episode; optionally snapshot every step."""
    env = RangeOpsEnv(scenario)
    policy = make_baseline("inventory_threshold", scenario, env.catalog, seed=seed)
    obs, info = env.reset(seed=seed)
    policy.reset()
    if instrumented:
        state = build_facility_state(env.sim)
        estimate_stockout(state)
        classify_state(state)
        derive_indicators(state)
    while True:
        action = policy.act(obs, info)
        obs, reward, terminated, truncated, info = env.step(action)
        if instrumented:
            state = build_facility_state(env.sim)
            estimate_stockout(state)
            classify_state(state)
            derive_indicators(state)
        if terminated or truncated:
            event_log = json.dumps(env.sim.events.to_dicts(), sort_keys=True)
            final_metrics = json.dumps(env.sim.metrics.to_dict(), sort_keys=True)
            return event_log, final_metrics


def tree_digest(package: str) -> str:
    root = SIMULATION_ROOT / package
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 1. RNG-stream neutrality
# ---------------------------------------------------------------------------


def test_facility_layer_consumes_no_rng(sim):
    sim.advance(3600.0)  # one busy hour so all streams are live
    before = rng_states(sim)
    state = build_facility_state(sim)
    estimate_stockout(state)
    classify_state(state)
    derive_indicators(state)
    state.to_dict()
    assert rng_states(sim) == before


# ---------------------------------------------------------------------------
# 2. Trajectory neutrality (byte-identical event log + metrics)
# ---------------------------------------------------------------------------


def test_snapshots_do_not_change_trajectory():
    scenario = short_day_scenario()
    plain_events, plain_metrics = run_episode(scenario, seed=2026, instrumented=False)
    instr_events, instr_metrics = run_episode(scenario, seed=2026, instrumented=True)
    assert instr_events == plain_events
    assert instr_metrics == plain_metrics


# ---------------------------------------------------------------------------
# 3. Static ban on RNG-drawing accessors in the builder
# ---------------------------------------------------------------------------


def test_builder_never_calls_rng_drawing_accessors():
    source = (SIMULATION_ROOT / "nxt_facility" / "build.py").read_text()
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (called & BANNED_ACCESSORS), (
        f"build.py calls RNG-drawing accessor(s) {called & BANNED_ACCESSORS}; "
        "this silently breaks byte-identical replay (see module docstring)"
    )


# ---------------------------------------------------------------------------
# 4. One-way boundary and upstream byte-identity
# ---------------------------------------------------------------------------


def test_upstream_trees_untouched_by_facility_use():
    before = {pkg: tree_digest(pkg) for pkg in UPSTREAM_PACKAGES}
    run_episode(short_day_scenario(), seed=7, instrumented=True)
    after = {pkg: tree_digest(pkg) for pkg in UPSTREAM_PACKAGES}
    assert after == before


def test_no_upstream_file_mentions_nxt_facility():
    offenders = []
    for pkg in UPSTREAM_PACKAGES:
        for path in (SIMULATION_ROOT / pkg).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "nxt_facility" in path.read_text():
                offenders.append(str(path))
    assert not offenders, f"upstream files reference nxt_facility: {offenders}"


def test_facility_package_imports_only_range_ops():
    allowed_first_party = {"nxt_range_ops", "nxt_facility"}
    banned = {"nxt_sim", "simpy", "gymnasium", "numpy", "pandas"}
    for path in (SIMULATION_ROOT / "nxt_facility").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            roots = set()
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            assert not (roots & banned), f"{path.name} imports banned: {roots & banned}"
            first_party = {r for r in roots if r.startswith("nxt_")}
            assert first_party <= allowed_first_party, (
                f"{path.name} imports outside the boundary: {first_party}"
            )
