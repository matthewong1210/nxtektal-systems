"""layout.json must carry the static facility geometry a viewer needs to
draw the map — straight from the scenario config, with provenance preserved."""

from __future__ import annotations

import json

from nxt_range_ops.evaluation.harness import DISCLAIMER
from nxt_range_viewer.layout import build_layout


def test_layout_matches_scenario_geometry(short_scenario):
    layout = build_layout(short_scenario)

    assert layout["scenario"] == short_scenario.name
    assert layout["hours"] == {
        "open_minute": short_scenario.hours.open_minute,
        "close_minute": short_scenario.hours.close_minute,
    }
    assert layout["dispenser"] == {
        "x_m": short_scenario.dispenser_position.x_m,
        "y_m": short_scenario.dispenser_position.y_m,
    }
    assert layout["charger"]["position"]["x_m"] == short_scenario.charger.position.x_m
    assert layout["charger"]["slots"] == short_scenario.charger.slots

    zones = {z["zone_id"]: z for z in layout["zones"]}
    assert set(zones) == set(short_scenario.zone_ids)
    for cfg in short_scenario.zones:
        assert zones[cfg.zone_id]["position"] == {
            "x_m": cfg.position.x_m,
            "y_m": cfg.position.y_m,
        }
        assert zones[cfg.zone_id]["landing_weight"] == cfg.landing_weight

    stations = {s["station_id"]: s for s in layout["stations"]}
    assert set(stations) == set(short_scenario.station_ids)
    for cfg in short_scenario.stations:
        assert stations[cfg.station_id]["dock_slots"] == cfg.dock_slots
        assert (
            stations[cfg.station_id]["buffer_capacity_balls"]
            == cfg.buffer_capacity_balls
        )

    robots = {r["robot_id"]: r for r in layout["robots"]}
    assert set(robots) == set(short_scenario.robot_ids)
    for cfg in short_scenario.robots:
        assert robots[cfg.robot_id]["payload_capacity_balls"] == cfg.payload_capacity_balls

    assert layout["total_balls"] == short_scenario.total_balls


def test_layout_carries_disclaimer_and_serializes(short_scenario):
    layout = build_layout(short_scenario)
    assert layout["disclaimer"] == DISCLAIMER
    json.dumps(layout)  # no PhysicalParam or other non-JSON leakage
