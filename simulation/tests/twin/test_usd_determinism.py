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
