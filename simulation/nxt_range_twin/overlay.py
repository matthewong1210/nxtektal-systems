"""Timesampled episode overlay: episode.usda (+ estimates.usda) over range_base.usda.

Composes over the static base layer. Every dynamic ``nxt:`` attribute and every
robot transform is authored here, one TimeSample per FacilityState record, at
``timeCode = t_s``. Held-sample teleports only — no interpolated glide (design
§2 of docs/spatial_twin_design.md): at 60 s cadence, lerped motion would assert
speeds nobody measured. A changed robot translate is re-stamped with its OLD
value one sim-second before the new sample lands, so USD's default linear
interpolation between samples never has anything real to interpolate across —
the visible result is an instant teleport, not a glide.

stdlib + pxr only. Deterministic: states are walked in order, opinions arrive
pre-sorted from ``mapping.frame_opinions``, no wall-clock is ever read.
"""
from __future__ import annotations

from pathlib import Path

from pxr import Gf, Sdf, Usd

from nxt_range_twin.base import build_base_layer
from nxt_range_twin.mapping import frame_opinions
from nxt_range_twin.placement import build_layout_index

TIME_CODES_PER_SECOND = 600.0
FRAMES_PER_SECOND = 600.0
TELEPORT_LEAD_S = 1.0  # honest-teleport hold window: old value re-stamped this long before the new one

ESTIMATES_COMMENT = "reserved: nxt:est:* estimates — empty in v1 by design"

# The checked mapping table (mapping.py) hands back plain-Python values tagged
# with a small type vocabulary; this is the one place those tags become pxr
# Sdf types / Gf value objects.
_SDF_TYPES = {
    "int": Sdf.ValueTypeNames.Int,
    "double": Sdf.ValueTypeNames.Double,
    "bool": Sdf.ValueTypeNames.Bool,
    "token": Sdf.ValueTypeNames.Token,
    "double3": Sdf.ValueTypeNames.Double3,
    "color3f[]": Sdf.ValueTypeNames.Color3fArray,
}


def _cast_value(sdf_type: str, value: object) -> object:
    if sdf_type == "double3":
        return Gf.Vec3d(*value)
    if sdf_type == "color3f[]":
        return [Gf.Vec3f(*channel) for channel in value]
    return value


def _ball_pile_scale(state: dict) -> Gf.Vec3f:
    flow = state["ball_flow"]
    total = int(flow["total_balls"])
    clean = int(flow["clean_available"])
    fill = max(0.02, clean / max(1, total))
    return Gf.Vec3f(1.0, 1.0, fill)


def build_episode_layer(
    layout: dict, states: list[dict], meta: dict, out_dir: Path
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Static base layer, seeded with washer_static derived from states[0].
    base_path = out_dir / "range_base.usda"
    washer0 = states[0]["washer"]
    base_meta = {
        **meta,
        "washer_static": {
            "throughput_balls_per_minute": washer0["throughput_balls_per_minute"],
            "batch_size_balls": washer0["batch_size_balls"],
        },
    }
    build_base_layer(layout, base_meta, base_path)
    base_layer_data = dict(Sdf.Layer.FindOrOpen(str(base_path)).customLayerData)

    # 2. Reserved growth seam: empty in v1 by design, guard-tested.
    estimates_path = out_dir / "estimates.usda"
    estimates_layer = Sdf.Layer.CreateNew(str(estimates_path))
    estimates_layer.comment = ESTIMATES_COMMENT
    estimates_layer.Save()

    # 3. episode.usda: subLayers estimates (stronger) over range_base (weaker).
    episode_path = out_dir / "episode.usda"
    stage = Usd.Stage.CreateNew(str(episode_path))
    root_layer = stage.GetRootLayer()
    root_layer.subLayerPaths = ["./estimates.usda", "./range_base.usda"]

    start_t = float(states[0]["meta"]["t_s"])
    end_t = float(states[-1]["meta"]["t_s"])
    stage.SetStartTimeCode(start_t)
    stage.SetEndTimeCode(end_t)
    stage.SetTimeCodesPerSecond(TIME_CODES_PER_SECOND)
    stage.SetFramesPerSecond(FRAMES_PER_SECOND)

    index = build_layout_index(layout)
    robot_ids = tuple(sorted(robot["robot_id"] for robot in layout["robots"]))

    prims: dict[str, Usd.Prim] = {}
    attr_cache: dict[tuple[str, str], Usd.Attribute] = {}

    def _prim(prim_path: str) -> Usd.Prim:
        prim = prims.get(prim_path)
        if prim is None:
            prim = stage.OverridePrim(prim_path)
            prims[prim_path] = prim
        return prim

    def _attr(prim_path: str, attr_name: str, sdf_type: str) -> Usd.Attribute:
        key = (prim_path, attr_name)
        attr = attr_cache.get(key)
        if attr is None:
            attr = _prim(prim_path).CreateAttribute(attr_name, _SDF_TYPES[sdf_type])
            attr_cache[key] = attr
        return attr

    ball_pile_path = "/World/Site/Dispenser/BallPile"
    ball_pile_scale_attr: Usd.Attribute | None = None
    prev_translate: dict[str, Gf.Vec3d] = {}

    # 4-6, 8. Walk states in order; opinions arrive pre-sorted from frame_opinions.
    for state in states:
        t_s = float(state["meta"]["t_s"])
        for prim_path, attr_name, sdf_type, value in frame_opinions(state, index, robot_ids):
            attr = _attr(prim_path, attr_name, sdf_type)
            cast = _cast_value(sdf_type, value)
            if attr_name == "xformOp:translate":
                # 5. Held-translate rule: re-stamp the OLD value one sim-second
                # before a changed sample, before authoring the new one.
                prev = prev_translate.get(prim_path)
                if prev is not None and prev != cast:
                    attr.Set(prev, t_s - TELEPORT_LEAD_S)
                attr.Set(cast, t_s)
                prev_translate[prim_path] = cast
            else:
                attr.Set(cast, t_s)

        # 6. Dispenser BallPile fill affordance — the one inventory->geometry link.
        if ball_pile_scale_attr is None:
            ball_pile_scale_attr = _prim(ball_pile_path).GetAttribute("xformOp:scale")
        ball_pile_scale_attr.Set(_ball_pile_scale(state), t_s)

    # 7. customLayerData: base layer's data, plus record count + input hashes.
    layer_data = dict(base_layer_data)
    layer_data["n_records"] = len(states)
    if "input_sha256" in meta:
        layer_data["input_sha256"] = meta["input_sha256"]
    root_layer.customLayerData = layer_data

    root_layer.Save()
    return episode_path
