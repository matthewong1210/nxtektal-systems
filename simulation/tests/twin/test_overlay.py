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
