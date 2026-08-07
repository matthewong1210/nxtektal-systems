"""range_base.usda authoring: static site layer built once from layout.json."""
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
