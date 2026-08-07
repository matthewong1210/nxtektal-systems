"""Boundary and derivation guards for the twin package."""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SIM_ROOT = Path(__file__).resolve().parents[2]
TWIN_MODULES = ["nxt_range_twin", "nxt_range_twin.stream", "nxt_range_twin.placement",
                "nxt_range_twin.mapping"]
USD_MODULES = ["nxt_range_twin.base", "nxt_range_twin.overlay"]
SIM_PACKAGES = ["nxt_range_ops", "nxt_sim", "nxt_facility", "nxt_memory",
                "nxt_range_viewer", "nxt_range_agent"]
SIM_LIBS = ["simpy", "gymnasium", "numpy", "pydantic", "pyarrow", "streamlit"]

# NOTE: importlib.abc.MetaPathFinder + find_spec, not find_module — find_module
# was removed in Python 3.12, so a find_module-based blocker would silently
# block nothing on the repo's 3.13. Mechanism copied from
# tests/facility/test_state.py::test_contract_importable_without_simulation_stack,
# which already carries this fix from adversarial review.
BLOCKER_TEMPLATE = """
import importlib.abc
import sys

class Blocker(importlib.abc.MetaPathFinder):
    BLOCKED = {blocked!r}

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.BLOCKED:
            raise ImportError("blocked by guard test: " + fullname)
        return None

sys.meta_path.insert(0, Blocker())
import {module}
print("guard-ok")
"""


def _import_with_blocked(module: str, blocked: list[str]) -> subprocess.CompletedProcess:
    code = textwrap.dedent(BLOCKER_TEMPLATE.format(blocked=set(blocked), module=module))
    return subprocess.run(
        [sys.executable, "-c", code], cwd=SIM_ROOT, capture_output=True, text=True
    )


@pytest.mark.parametrize("module", TWIN_MODULES)
def test_pure_modules_import_with_sim_and_pxr_blocked(module):
    result = _import_with_blocked(module, SIM_PACKAGES + SIM_LIBS + ["pxr"])
    assert result.returncode == 0, result.stderr
    assert "guard-ok" in result.stdout


@pytest.mark.parametrize("module", USD_MODULES)
def test_usd_modules_import_with_sim_blocked(module):
    pytest.importorskip("pxr")
    result = _import_with_blocked(module, SIM_PACKAGES + SIM_LIBS)
    assert result.returncode == 0, result.stderr
    assert "guard-ok" in result.stdout


def test_twin_source_never_mentions_sim_packages():
    for path in (SIM_ROOT / "nxt_range_twin").glob("*.py"):
        text = path.read_text()
        for package in SIM_PACKAGES:
            assert package not in text, f"{path} mentions {package}"


def test_derivation_audit_every_nxt_attr_is_in_the_table(tmp_path):
    """Walk a built stage: every authored nxt: attr must trace to EMITTED_ATTRS."""
    pytest.importorskip("pxr")
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
    pytest.importorskip("pxr")
    from nxt_range_twin.overlay import build_episode_layer
    from tests.twin.fixtures import LAYOUT, STATE

    meta = {"site_id": "s", "deployment_id": "d", "episode_id": "fixture-seed7",
            "scenario_name": "fixture", "seed": 7, "disclaimer": "x"}
    path = build_episode_layer(LAYOUT, [STATE], meta, tmp_path)
    text = path.read_text() + (path.parent / "range_base.usda").read_text()
    assert "Physics" not in text  # no UsdPhysics schema names anywhere


def test_base_layer_geometry_equals_layout_positions(tmp_path):
    """Cross-artifact equality: layout.json is the only geometry derivation path."""
    pytest.importorskip("pxr")
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
