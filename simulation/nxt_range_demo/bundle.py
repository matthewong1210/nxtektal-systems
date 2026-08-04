"""Bundle loading and replay geometry — pure stdlib, no Streamlit imports.

The viewer animates symbolic robot locations ("charger", "zone:Z1",
"transit") onto the 2D facility map. The simulator moves robots linearly
between nodes, so linear interpolation across each contiguous transit run is
visually faithful to the underlying model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

Point = tuple[float, float]

_SCHEMA_PREFIXES = {
    "layout.json": "nxt-range-viewer/layout/",
    "episode.json": "nxt-range-viewer/episode/",
    "benchmark.json": "nxt-range-viewer/benchmark/",
}

_ACTION_RE = re.compile(r"^(\w+)(?:\((.*)\))?$")


@dataclass(frozen=True)
class DemoBundle:
    """One exported demo bundle, parsed."""

    path: Path
    layout: dict[str, Any]
    episode: dict[str, Any]
    benchmark: Optional[dict[str, Any]]


def load_bundle(bundle_dir: Union[str, Path]) -> DemoBundle:
    """Load layout.json + episode.json (required) and benchmark.json (optional)."""
    root = Path(bundle_dir)
    layout = _load_checked(root / "layout.json")
    episode = _load_checked(root / "episode.json")
    benchmark_path = root / "benchmark.json"
    benchmark = _load_checked(benchmark_path) if benchmark_path.exists() else None
    return DemoBundle(path=root, layout=layout, episode=episode, benchmark=benchmark)


def _load_checked(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = _SCHEMA_PREFIXES[path.name]
    schema = data.get("schema", "")
    if not isinstance(schema, str) or not schema.startswith(expected):
        raise ValueError(
            f"{path.name} has unexpected schema {schema!r}; expected a "
            f"'{expected}*' bundle exported by nxt_range_viewer"
        )
    return data


def node_positions(layout: dict[str, Any]) -> dict[str, Point]:
    """Map every symbolic location label to its (x_m, y_m) coordinate."""
    positions: dict[str, Point] = {
        "dispenser": _point(layout["dispenser"]),
        "charger": _point(layout["charger"]["position"]),
    }
    for zone in layout["zones"]:
        positions[f"zone:{zone['zone_id']}"] = _point(zone["position"])
    for station in layout["stations"]:
        positions[f"station:{station['station_id']}"] = _point(station["position"])
    return positions


def _point(raw: dict[str, float]) -> Point:
    return (float(raw["x_m"]), float(raw["y_m"]))


def compute_robot_tracks(
    episode: dict[str, Any], positions: dict[str, Point]
) -> list[dict[str, Point]]:
    """Per-frame (x, y) for every robot, interpolating transit runs linearly.

    A robot in transit for L consecutive frames toward the same destination
    is placed at fractions 1/(L+1) ... L/(L+1) of the leg, arriving exactly
    at the destination on the first non-transit frame — matching the sim's
    own linear travel model. A transit run that never arrives (episode end,
    or a mid-travel reassignment) ends strictly between the two nodes and
    the next run continues from that point.
    """
    frames = episode["frames"]
    fallback = positions.get("dispenser", (0.0, 0.0))
    tracks: list[dict[str, Point]] = [{} for _ in frames]

    for robot in episode["initial"]["robots"]:
        rid = robot["robot_id"]
        sequence = []
        for frame in frames:
            state = next(r for r in frame["robots"] if r["robot_id"] == rid)
            sequence.append((state["location"], state.get("destination")))

        current = positions.get(robot["location"], fallback)
        i = 0
        while i < len(sequence):
            location, destination = sequence[i]
            if location != "transit":
                current = positions.get(location, fallback)
                tracks[i][rid] = current
                i += 1
                continue

            j = i
            while (
                j + 1 < len(sequence)
                and sequence[j + 1][0] == "transit"
                and sequence[j + 1][1] == destination
            ):
                j += 1
            target = positions.get(destination) if destination else None
            if target is None:
                for k in range(i, j + 1):
                    tracks[k][rid] = current
            else:
                run = j - i + 1
                (sx, sy), (tx, ty) = current, target
                for k in range(i, j + 1):
                    fraction = (k - i + 1) / (run + 1)
                    tracks[k][rid] = (
                        sx + (tx - sx) * fraction,
                        sy + (ty - sy) * fraction,
                    )
                current = tracks[j][rid]
            i = j + 1

    return tracks


def parse_action(name: str) -> tuple[str, list[str]]:
    """Split an action name like ``assign_collection(R1,Z3)`` into verb + args."""
    match = _ACTION_RE.match(name)
    if match is None:
        return (name, [])
    verb, raw_args = match.group(1), match.group(2)
    if not raw_args:
        return (verb, [])
    return (verb, [arg.strip() for arg in raw_args.split(",")])


def sim_clock(t_s: float) -> str:
    """Seconds since midnight -> ``HH:MM``."""
    minutes = int(t_s) // 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
