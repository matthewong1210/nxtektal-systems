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


def _four_states() -> list[dict]:
    import copy
    s0 = copy.deepcopy(STATE)
    s0["meta"]["t_s"] = 0.0
    s0["robots"][0]["location"] = "dispenser"
    s1 = copy.deepcopy(STATE)   # R1 at zone:Z1, t=60
    s2 = copy.deepcopy(STATE)
    s2["meta"]["t_s"] = 120.0
    s2["robots"][0]["location"] = "transit"
    s2["robots"][0]["destination"] = "station:H1"
    s2["robots"][0]["activity"] = "traveling"
    s3 = copy.deepcopy(STATE)
    s3["meta"]["t_s"] = 180.0
    s3["robots"][0]["location"] = "station:H1"
    s3["robots"][0]["activity"] = "docking"
    return [s0, s1, s2, s3]


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


def test_transit_frames_hold_last_position_on_composed_stage(tmp_path: Path):
    """Regression guard: transit frames hold position from prior location anchor."""
    path = build_episode_layer(LAYOUT, _four_states(), META, tmp_path)
    stage = Usd.Stage.Open(str(path))
    attr = stage.GetPrimAtPath("/World/Site/Robots/R1").GetAttribute("xformOp:translate")

    # (b) value at t=120 equals value at t=60 (held through the transit frame)
    at_60 = attr.Get(60.0)
    at_120 = attr.Get(120.0)
    assert at_120 == at_60, "transit frame should hold position from zone:Z1 anchor"

    # (c) value at t=180 differs from t=120 (arrival re-stamp)
    at_180 = attr.Get(180.0)
    assert at_180 != at_120, "arrival at station:H1 should update position"

    # (d) value at t=179 equals value at t=120 (held until the teleport lead)
    at_179 = attr.Get(179.0)
    assert at_179 == at_120, "position held until t=180-1.0"

    # (e) nxt:location token at t=120 is "transit"
    loc_attr = stage.GetPrimAtPath("/World/Site/Robots/R1").GetAttribute("nxt:location")
    loc_at_120 = loc_attr.Get(120.0)
    assert str(loc_at_120) == "transit", f"expected 'transit' at t=120, got {loc_at_120}"
