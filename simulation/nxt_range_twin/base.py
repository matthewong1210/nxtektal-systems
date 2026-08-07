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
    UsdGeom.Scope.Define(stage, "/World/Site/Aspatial")
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
