"""Guards: the observation layer must be invisible to the simulator and
honest about its boundaries."""

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

from nxt_telemetry.assemble import assemble_from_observations
from nxt_telemetry.bank import (
    ChannelImperfection,
    SyntheticSensorBank,
    TelemetryConfig,
    site_config_from_scenario,
    upstream_from_sim,
)

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_PACKAGES = (
    "nxt_sim",
    "nxt_range_ops",
    "nxt_range_agent",
    "nxt_facility",
    "nxt_memory",
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


def run_episode(scenario, seed: int, instrumented: bool):
    env = RangeOpsEnv(scenario)
    policy = make_baseline("inventory_threshold", scenario, env.catalog, seed=seed)
    obs, info = env.reset(seed=seed)
    policy.reset()
    obs_digest = hashlib.sha256()
    _absorb_obs(obs_digest, obs)
    bank = None
    if instrumented:
        # imperfections ON so noise draws happen every step — the strictest
        # neutrality case (an imperfect bank must still not touch sim RNG)
        bank = SyntheticSensorBank(
            env.sim,
            TelemetryConfig(
                families={
                    "inventory": ChannelImperfection(noise_rel_sd=0.05, dropout_prob=0.1),
                    "scan": ChannelImperfection(delay_s=120.0, cadence_s=60.0),
                }
            ),
        )
        site = site_config_from_scenario(scenario, seed=seed)
        assemble_from_observations(bank.sample(), site, upstream_from_sim(env.sim))
    while True:
        action = policy.act(obs, info)
        obs, reward, terminated, truncated, info = env.step(action)
        _absorb_obs(obs_digest, obs)
        obs_digest.update(repr(float(reward)).encode())
        if instrumented:
            assemble_from_observations(
                bank.sample(), site, upstream_from_sim(env.sim)
            )
        if terminated or truncated:
            return (
                json.dumps(env.sim.events.to_dicts(), sort_keys=True),
                json.dumps(env.sim.metrics.to_dict(), sort_keys=True),
                obs_digest.hexdigest(),
            )


def test_observation_layer_is_trajectory_neutral():
    scenario = short_day_scenario()
    bare = run_episode(scenario, seed=808, instrumented=False)
    instrumented = run_episode(scenario, seed=808, instrumented=True)
    assert instrumented == bare


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
        import nxt_telemetry.observations
        import nxt_telemetry
        from nxt_telemetry import Observation, ObservationFrame, SiteConfig
        print("observation-contract-ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=SIMULATION_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "observation-contract-ok" in result.stdout


def test_no_upstream_file_mentions_nxt_telemetry():
    offenders = []
    for pkg in UPSTREAM_PACKAGES:
        for path in (SIMULATION_ROOT / pkg).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "nxt_telemetry" in path.read_text():
                offenders.append(str(path))
    assert not offenders, f"upstream files reference nxt_telemetry: {offenders}"


def test_import_discipline_within_telemetry_package():
    """Only bank.py and assemble.py may import the simulator; the sim's
    RNG-drawing sensed accessors and stream internals are banned
    everywhere in the package."""
    banned_calls = {"sensed_zone_counts", "sensed_battery_frac", "spawn"}
    for path in (SIMULATION_ROOT / "nxt_telemetry").glob("*.py"):
        tree = ast.parse(path.read_text())
        roots = set()
        called_attrs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called_attrs.add(node.func.attr)
            elif isinstance(node, ast.Attribute) and node.attr.startswith("_rng"):
                raise AssertionError(f"{path.name} touches sim RNG stream {node.attr}")
        assert not (called_attrs & banned_calls), (
            f"{path.name} calls banned accessor(s): {called_attrs & banned_calls}"
        )
        first_party = {r for r in roots if r.startswith("nxt_")}
        if path.name in ("bank.py", "assemble.py"):
            assert first_party <= {"nxt_range_ops", "nxt_facility", "nxt_telemetry"}
        else:
            assert first_party <= {"nxt_telemetry"}, (
                f"{path.name} imports {first_party} — only bank/assemble may "
                "touch the simulator"
            )


def test_no_wall_clock_or_uuid_in_telemetry_package():
    banned = {"time", "datetime", "uuid", "random", "secrets"}
    for path in (SIMULATION_ROOT / "nxt_telemetry").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            roots = set()
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            # numpy.random is allowed (stateless SeedSequence scheme);
            # stdlib random/clocks are not
            assert not (roots & banned), f"{path.name} imports banned: {roots & banned}"
