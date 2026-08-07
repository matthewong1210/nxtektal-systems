"""Discrete node ids -> site-frame coordinates. Pure math, stdlib only.

All footprint constants are invented presentation values, provenance
"placeholder" — no entity in the contract has extents (design §1).
"""
from __future__ import annotations

import math

ZONE_RADIUS_M = 8.0
STATION_HALF_M = 3.0
TERRAIN_MARGIN_M = 20.0
CROWD_RING_RADIUS_M = 2.5


def build_layout_index(layout: dict) -> dict[str, tuple[float, float]]:
    def point(p: dict) -> tuple[float, float]:
        return (float(p["x_m"]), float(p["y_m"]))

    index = {
        "dispenser": point(layout["dispenser"]),
        "charger": point(layout["charger"]["position"]),
    }
    for zone in layout["zones"]:
        index[f"zone:{zone['zone_id']}"] = point(zone["position"])
    for station in layout["stations"]:
        index[f"station:{station['station_id']}"] = point(station["position"])
    return index


def resolve_location(node: str, index: dict[str, tuple[float, float]]) -> tuple[float, float]:
    if node not in index:
        raise KeyError(f"unknown location node {node!r}")
    return index[node]


def robot_offset(robot_id: str, all_ids: tuple[str, ...]) -> tuple[float, float]:
    ordered = sorted(all_ids)
    rank = ordered.index(robot_id)
    angle = 2.0 * math.pi * rank / max(1, len(ordered))
    return (
        CROWD_RING_RADIUS_M * math.cos(angle),
        CROWD_RING_RADIUS_M * math.sin(angle),
    )
