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

import numpy as np
import pytest

from nxt_range_ops.config.models import OperatingHoursConfig
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.scenarios.generators import make_scenario

from nxt_facility.analysis import classify_state, derive_indicators, estimate_stockout
from nxt_facility.briefing import render_briefing
from nxt_facility.build import build_facility_state
from nxt_facility.decisions import recommend

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


def short_day_scenario(base: str = "normal_weekday"):
    """Two-hour operating day for fast full-episode runs.

    ``noisy_inventory_sensor`` matters as a base: its inventory_delay_s
    exceeds the control interval, so observation-only side effects (e.g. a
    builder mutating _inventory_readings) surface in the obs digest there
    while staying invisible under normal_weekday's short delay.
    """
    scenario = make_scenario(base)
    return scenario.model_copy(
        update={"hours": OperatingHoursConfig(open_minute=360, close_minute=480)}
    )


def _absorb_obs(digest, obs) -> None:
    for key in sorted(obs):
        digest.update(key.encode())
        digest.update(np.asarray(obs[key]).tobytes())


def run_episode(scenario, seed: int, instrumented: bool):
    """One full baseline-driven episode; optionally snapshot every step.

    Returns byte-level digests of the event log, final metrics, and the
    full observation+reward sequence.
    """
    env = RangeOpsEnv(scenario)
    policy = make_baseline("inventory_threshold", scenario, env.catalog, seed=seed)
    obs, info = env.reset(seed=seed)
    policy.reset()
    obs_digest = hashlib.sha256()
    _absorb_obs(obs_digest, obs)

    def facility_layer(sim) -> None:
        """Everything the facility layer offers, exercised every step."""
        state = build_facility_state(sim)
        estimate_stockout(state)
        classify_state(state)
        derive_indicators(state)
        render_briefing(state, recommend(state))

    if instrumented:
        facility_layer(env.sim)
    while True:
        action = policy.act(obs, info)
        obs, reward, terminated, truncated, info = env.step(action)
        _absorb_obs(obs_digest, obs)
        obs_digest.update(repr(float(reward)).encode())
        if instrumented:
            facility_layer(env.sim)
        if terminated or truncated:
            event_log = json.dumps(env.sim.events.to_dicts(), sort_keys=True)
            final_metrics = json.dumps(env.sim.metrics.to_dict(), sort_keys=True)
            return event_log, final_metrics, obs_digest.hexdigest()


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
    render_briefing(state, recommend(state))
    state.to_dict()
    assert rng_states(sim) == before


# ---------------------------------------------------------------------------
# 2. Trajectory neutrality (byte-identical event log + metrics)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base", ["normal_weekday", "noisy_inventory_sensor"])
def test_snapshots_do_not_change_trajectory(base):
    scenario = short_day_scenario(base)
    plain = run_episode(scenario, seed=2026, instrumented=False)
    instrumented = run_episode(scenario, seed=2026, instrumented=True)
    assert instrumented[0] == plain[0]  # event log, byte-identical
    assert instrumented[1] == plain[1]  # final metrics
    assert instrumented[2] == plain[2]  # full obs+reward sequence


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
