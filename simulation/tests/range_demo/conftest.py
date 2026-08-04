"""Fixtures: a tiny synthetic demo bundle exercising every viewer code path.

Synthetic rather than exporter-generated so these tests stay dependency-free
(no simulator import) and cover edge cases (transit at episode end, mid-travel
reassignment) that a real short episode may not produce on demand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DISCLAIMER = (
    "All results are produced from placeholder-provenance parameters. They "
    "exercise the decision problem and the pipeline only and must NOT be "
    "presented as real facility performance."
)


def _robot(rid, activity, location, destination=None, battery=0.9, payload=0):
    return {
        "robot_id": rid,
        "activity": activity,
        "health": "ok",
        "battery_frac": battery,
        "payload_balls": payload,
        "payload_capacity_balls": 600,
        "location": location,
        "destination": destination,
        "assigned_zone": None,
        "estop_latched": False,
        "awaiting_human": False,
    }


def _frame(step, t_s, robots, action="wait", allowed=True, stockout=0.0):
    return {
        "step": step,
        "t_s": t_s,
        "robots": robots,
        "zones": [
            {"zone_id": "Z1", "balls": 40 + step, "is_open": True, "robots_present": 0},
            {"zone_id": "Z2", "balls": 15, "is_open": True, "robots_present": 0},
        ],
        "stations": [
            {
                "station_id": "H1",
                "is_open": True,
                "docked": 0,
                "queue_length": 0,
                "buffer_balls": 10 * step,
                "buffer_capacity_balls": 2000,
            }
        ],
        "inventory": {"dispenser_balls": 3000 - 5 * step, "washer_wip_frac": 0.01},
        "charger_queue": 0,
        "action": {
            "index": 0 if action == "wait" else 1,
            "name": action,
            "shield_allowed": allowed,
            "shield_reason": "ok" if allowed else "battery below reserve",
        },
        "reward": {"total": 1.0, "components": {"service_availability": 1.0}},
        "kpi": {
            "stockout_minutes": stockout,
            "balls_collected": 10 * step,
            "balls_processed": 5 * step,
            "human_interventions": 0,
            "energy_wh": 3.5 * step,
            "demand_balls_total": 20 * step,
            "demand_balls_served": 20 * step,
        },
    }


@pytest.fixture
def bundle_dir(tmp_path) -> Path:
    layout = {
        "schema": "nxt-range-viewer/layout/v1",
        "disclaimer": DISCLAIMER,
        "scenario": "demo_scenario",
        "description": "synthetic test scenario",
        "coordinate_frame": {"units": "meters", "origin": "dispenser"},
        "hours": {"open_minute": 360, "close_minute": 1320},
        "total_balls": 4000,
        "initial_dispenser_frac": 0.9,
        "dispenser": {"x_m": 0.0, "y_m": 0.0},
        "charger": {"position": {"x_m": -10.0, "y_m": 5.0}, "slots": 2},
        "zones": [
            {
                "zone_id": "Z1",
                "position": {"x_m": 100.0, "y_m": 0.0},
                "landing_weight": 5.0,
                "closure_windows": [],
            },
            {
                "zone_id": "Z2",
                "position": {"x_m": 50.0, "y_m": 20.0},
                "landing_weight": 2.0,
                "closure_windows": [],
            },
        ],
        "stations": [
            {
                "station_id": "H1",
                "position": {"x_m": -5.0, "y_m": -10.0},
                "dock_slots": 1,
                "buffer_capacity_balls": 2000,
                "outage_windows": [],
            }
        ],
        "robots": [
            {"robot_id": "R1", "payload_capacity_balls": 600, "initial_battery_frac": 1.0},
            {"robot_id": "R2", "payload_capacity_balls": 600, "initial_battery_frac": 1.0},
        ],
    }

    # R1: charger -> (transit x2) -> Z1 -> transit-until-end (toward H1).
    # R2: parked at charger throughout.
    frames = [
        _frame(
            1,
            21660.0,
            [
                _robot("R1", "traveling", "transit", "zone:Z1"),
                _robot("R2", "idle", "charger"),
            ],
            action="assign_collection(R1,Z1)",
        ),
        _frame(
            2,
            21720.0,
            [
                _robot("R1", "traveling", "transit", "zone:Z1"),
                _robot("R2", "idle", "charger"),
            ],
        ),
        _frame(
            3,
            21780.0,
            [
                _robot("R1", "collecting", "zone:Z1"),
                _robot("R2", "idle", "charger"),
            ],
            stockout=2.0,
        ),
        _frame(
            4,
            21840.0,
            [
                _robot("R1", "traveling", "transit", "station:H1", payload=300),
                _robot("R2", "idle", "charger"),
            ],
            action="send_to_charge(R2)",
            allowed=False,
        ),
    ]
    episode = {
        "schema": "nxt-range-viewer/episode/v1",
        "meta": {
            "scenario": "demo_scenario",
            "policy": "inventory_threshold",
            "policy_version": "0.5.0",
            "seed": 101,
            "n_steps": 4,
            "termination_reason": "day_complete",
            "control_interval_s": 60.0,
            "simulator_version": "0.5.0",
            "viewer_version": "0.1.0",
            "git_commit": "abc1234",
            "disclaimer": DISCLAIMER,
            "reward_total": 4.0,
            "kpis": {
                "stockout_minutes": 2.0,
                "service_availability": 0.998,
                "human_interventions": 0.0,
                "balls_processed": 20.0,
                "energy_wh": 14.0,
                "empty_travel_s": 120.0,
                "robot_utilization": 0.5,
                "handoff_queue_s": 0.0,
                "safe_recovery_rate": 1.0,
                "estimated_operating_cost": 55.0,
            },
            "event_kinds": ["stockout"],
            "includes_debug_state": False,
        },
        "initial": {
            "t_s": 21600.0,
            "robots": [_robot("R1", "idle", "charger"), _robot("R2", "idle", "charger")],
            "zones": [
                {"zone_id": "Z1", "balls": 40, "is_open": True, "robots_present": 0},
                {"zone_id": "Z2", "balls": 15, "is_open": True, "robots_present": 0},
            ],
            "stations": [
                {
                    "station_id": "H1",
                    "is_open": True,
                    "docked": 0,
                    "queue_length": 0,
                    "buffer_balls": 0,
                    "buffer_capacity_balls": 2000,
                }
            ],
            "inventory": {"dispenser_balls": 3000, "washer_wip_frac": 0.0},
            "charger_queue": 0,
        },
        "frames": frames,
        "events": [{"t_s": 21750.0, "kind": "stockout", "payload": {}}],
    }
    benchmark = {
        "schema": "nxt-range-viewer/benchmark/v1",
        "disclaimer": DISCLAIMER,
        "provenance": {"simulator_version": "0.5.0", "git_commit": "abc1234"},
        "seeds": [101],
        "policies": ["inventory_threshold"],
        "scenarios": ["demo_scenario"],
        "rankings": {
            "overall": [
                {
                    "rank": 1,
                    "policy": "inventory_threshold",
                    "mean_composite_rank": 1.0,
                    "scenario_wins": 1,
                    "mean_reward": 4.0,
                }
            ],
            "per_scenario": [],
        },
    }

    out = tmp_path / "bundle"
    out.mkdir()
    (out / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    (out / "episode.json").write_text(json.dumps(episode), encoding="utf-8")
    (out / "benchmark.json").write_text(json.dumps(benchmark), encoding="utf-8")
    return out
