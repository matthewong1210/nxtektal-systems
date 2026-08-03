"""The four required baseline policies.

All baselines issue at most one directive per control step (the env's action
budget), always consult the valid-action mask, and fall back to ``wait``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from nxt_range_ops.config.models import RangeOpsScenario
from nxt_range_ops.core.entities import RobotActivity
from nxt_range_ops.env.actions import ActionCatalog
from nxt_range_ops.policies.base import WAIT_INDEX, BaselinePolicy

_ASSIGNABLE = (RobotActivity.IDLE.value,)


class RandomValidActionPolicy(BaselinePolicy):
    """Uniform choice over the currently valid actions (mask-respecting)."""

    name = "random_valid"

    def act(self, obs: dict[str, Any], info: dict[str, Any]) -> int:
        valid = np.flatnonzero(info["action_mask"])
        if len(valid) == 0:  # cannot happen: wait is always valid
            return WAIT_INDEX
        return int(self.rng.choice(valid))


class InventoryThresholdPolicy(BaselinePolicy):
    """Reactive rules keyed on dispenser inventory and robot thresholds.

    * disabled robot -> request human;
    * payload nearly full -> hand off;
    * battery near reserve -> charge;
    * dispenser below threshold -> put idle robots on the fullest open zones;
    * otherwise wait.
    """

    name = "inventory_threshold"

    def __init__(
        self,
        scenario: RangeOpsScenario,
        catalog: ActionCatalog,
        seed: int = 0,
        low_inventory_frac: float = 0.6,
        payload_handoff_frac: float = 0.85,
        battery_margin: float = 0.10,
    ):
        super().__init__(scenario, catalog, seed)
        self.low_inventory_frac = low_inventory_frac
        self.payload_handoff_frac = payload_handoff_frac
        self.battery_margin = battery_margin

    def act(self, obs: dict[str, Any], info: dict[str, Any]) -> int:
        mask = info["action_mask"]
        wishes = self.upkeep_wishes(info)
        reserve = self.scenario.safety.min_battery_reserve_frac
        for robot in info["robots"]:
            rid = robot["robot_id"]
            payload_frac = robot["payload_balls"] / robot["payload_capacity_balls"]
            if payload_frac >= self.payload_handoff_frac:
                wishes.append(f"send_to_handoff({rid})")
            if (
                robot["battery_frac"] <= reserve + self.battery_margin
                and robot["activity"] != RobotActivity.CHARGING.value
            ):
                if robot["payload_balls"] > 0:
                    wishes.append(f"send_to_handoff({rid})")
                wishes.append(f"send_to_charge({rid})")
        inventory_low = float(obs["dispenser_inventory_frac"][0]) < (
            self.low_inventory_frac * self.scenario.initial_dispenser_frac
        )
        if inventory_low:
            zones = self.ranked_zones_by_balls(obs)
            for robot in info["robots"]:
                if robot["activity"] in _ASSIGNABLE:
                    for zone_id in zones:
                        wishes.append(
                            f"assign_collection({robot['robot_id']},{zone_id})"
                        )
        return self.first_valid(wishes, mask)


class NearestAvailableRobotPolicy(BaselinePolicy):
    """Continuously assigns the nearest available robot to the fullest zone,
    with the same handoff/charge upkeep as the threshold policy."""

    name = "nearest_available_robot"

    def act(self, obs: dict[str, Any], info: dict[str, Any]) -> int:
        mask = info["action_mask"]
        wishes = self.upkeep_wishes(info)
        reserve = self.scenario.safety.min_battery_reserve_frac
        for robot in info["robots"]:
            rid = robot["robot_id"]
            if robot["payload_balls"] >= robot["payload_capacity_balls"]:
                wishes.append(f"send_to_handoff({rid})")
            if robot["battery_frac"] <= reserve + 0.05:
                wishes.append(f"send_to_charge({rid})")
        for zone_id in self.ranked_zones_by_balls(obs):
            dest = self._positions[f"zone:{zone_id}"]
            idle = [
                r
                for r in info["robots"]
                if r["activity"] in _ASSIGNABLE
                and r["battery_frac"] > reserve
            ]
            for robot in sorted(
                idle,
                key=lambda r: (self.distance_from_label(r["location"], dest), r["robot_id"]),
            ):
                wishes.append(f"assign_collection({robot['robot_id']},{zone_id})")
        return self.first_valid(wishes, mask)


class DemandForecastDispatchPolicy(BaselinePolicy):
    """Pre-positions collection effort against the demand forecast.

    Compares forecast consumption over the horizon with the sensed clean-ball
    pipeline (dispenser + washer WIP); staffs collection proportionally to the
    shortfall, prefers fuller zones, and hands off earlier when demand is
    coming so the washer stays fed.
    """

    name = "demand_forecast_dispatch"

    def act(self, obs: dict[str, Any], info: dict[str, Any]) -> int:
        mask = info["action_mask"]
        wishes = self.upkeep_wishes(info)
        s = self.scenario
        reserve = s.safety.min_battery_reserve_frac

        bucket_min = s.demand.forecast_bucket_minutes
        forecast_balls = float(np.sum(obs["demand_forecast"])) * bucket_min
        pipeline_balls = (
            float(obs["dispenser_inventory_frac"][0]) + float(obs["washer_wip_frac"][0])
        ) * s.total_balls
        shortfall = forecast_balls - pipeline_balls
        n_robots = len(s.robot_ids)
        want_collecting = int(
            np.clip(np.ceil(shortfall / max(1.0, 0.5 * s.total_balls) * n_robots), 0, n_robots)
        )
        if shortfall > 0:
            want_collecting = max(want_collecting, 1)

        payload_handoff_frac = 0.6 if shortfall > 0 else 0.95
        collecting = 0
        for robot in info["robots"]:
            rid = robot["robot_id"]
            payload_frac = robot["payload_balls"] / robot["payload_capacity_balls"]
            if robot["activity"] in (
                RobotActivity.TRAVELING.value,
                RobotActivity.COLLECTING.value,
            ):
                collecting += 1
            if payload_frac >= payload_handoff_frac:
                wishes.append(f"send_to_handoff({rid})")
            if robot["battery_frac"] <= reserve + 0.08:
                if robot["payload_balls"] > 0:
                    wishes.append(f"send_to_handoff({rid})")
                wishes.append(f"send_to_charge({rid})")

        if collecting < want_collecting:
            zones = self.ranked_zones_by_balls(obs)
            for robot in info["robots"]:
                if robot["activity"] in _ASSIGNABLE:
                    for zone_id in zones:
                        wishes.append(
                            f"assign_collection({robot['robot_id']},{zone_id})"
                        )
        return self.first_valid(wishes, mask)


def make_baseline(
    name: str, scenario: RangeOpsScenario, catalog: ActionCatalog, seed: int = 0
) -> BaselinePolicy:
    registry = {
        RandomValidActionPolicy.name: RandomValidActionPolicy,
        InventoryThresholdPolicy.name: InventoryThresholdPolicy,
        NearestAvailableRobotPolicy.name: NearestAvailableRobotPolicy,
        DemandForecastDispatchPolicy.name: DemandForecastDispatchPolicy,
    }
    if name not in registry:
        raise KeyError(f"unknown baseline {name!r}; choose from {sorted(registry)}")
    return registry[name](scenario, catalog, seed=seed)
