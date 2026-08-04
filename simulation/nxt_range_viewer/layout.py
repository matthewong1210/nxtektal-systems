"""Static facility layout for the viewer map, from the scenario config.

Exports geometry and capacities only — physical parameters (speeds, power
draws, failure rates) stay out so placeholder-provenance values cannot leak
into the viewer as if they were engineering data. The disclaimer rides along
instead.
"""

from __future__ import annotations

from typing import Any

from nxt_range_ops.config.models import Point2D, RangeOpsScenario, TimeWindow
from nxt_range_ops.evaluation.harness import DISCLAIMER


def build_layout(scenario: RangeOpsScenario) -> dict[str, Any]:
    """Viewer-ready static layout for one scenario."""
    return {
        "schema": "nxt-range-viewer/layout/v1",
        "disclaimer": DISCLAIMER,
        "scenario": scenario.name,
        "description": scenario.description,
        "coordinate_frame": {"units": "meters", "origin": "dispenser"},
        "hours": {
            "open_minute": scenario.hours.open_minute,
            "close_minute": scenario.hours.close_minute,
        },
        "total_balls": scenario.total_balls,
        "initial_dispenser_frac": scenario.initial_dispenser_frac,
        "dispenser": _point(scenario.dispenser_position),
        "charger": {
            "position": _point(scenario.charger.position),
            "slots": scenario.charger.slots,
        },
        "zones": [
            {
                "zone_id": zone.zone_id,
                "position": _point(zone.position),
                "landing_weight": zone.landing_weight,
                "closure_windows": [_window(w) for w in zone.closure_windows],
            }
            for zone in scenario.zones
        ],
        "stations": [
            {
                "station_id": station.station_id,
                "position": _point(station.position),
                "dock_slots": station.dock_slots,
                "buffer_capacity_balls": station.buffer_capacity_balls,
                "outage_windows": [_window(w) for w in station.outage_windows],
            }
            for station in scenario.stations
        ],
        "robots": [
            {
                "robot_id": robot.robot_id,
                "payload_capacity_balls": robot.payload_capacity_balls,
                "initial_battery_frac": robot.initial_battery_frac,
            }
            for robot in scenario.robots
        ],
    }


def _point(point: Point2D) -> dict[str, float]:
    return {"x_m": point.x_m, "y_m": point.y_m}


def _window(window: TimeWindow) -> dict[str, int]:
    return {"start_minute": window.start_minute, "end_minute": window.end_minute}
