"""Baseline policy interface.

Policies see exactly what a learned agent would: the observation, the info
dict (including the valid-action mask), and the static action catalog /
scenario. They must return a valid action index; the shared helpers make
"first valid directive from a ranked wish list, else wait" easy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

import numpy as np

from nxt_range_ops.config.models import Point2D, RangeOpsScenario
from nxt_range_ops.core.entities import HumanAssistReason, RobotActivity
from nxt_range_ops.env.actions import ActionCatalog

WAIT_INDEX = 0  # by catalog construction


class BaselinePolicy(ABC):
    name: str = "baseline"
    version: str = "0.5.0"

    def __init__(self, scenario: RangeOpsScenario, catalog: ActionCatalog, seed: int = 0):
        self.scenario = scenario
        self.catalog = catalog
        self.rng = np.random.default_rng(seed)
        self._positions = self._static_positions(scenario)

    @staticmethod
    def _static_positions(scenario: RangeOpsScenario) -> dict[str, Point2D]:
        positions: dict[str, Point2D] = {
            "dispenser": scenario.dispenser_position,
            "charger": scenario.charger.position,
        }
        for z in scenario.zones:
            positions[f"zone:{z.zone_id}"] = z.position
        for s in scenario.stations:
            positions[f"station:{s.station_id}"] = s.position
        return positions

    def reset(self) -> None:  # pragma: no cover - default no-op
        pass

    @abstractmethod
    def act(self, obs: dict[str, Any], info: dict[str, Any]) -> int:
        """Return a valid action index for this step."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def try_action(self, name: str, mask: np.ndarray) -> Optional[int]:
        try:
            index = self.catalog.index_of(name)
        except KeyError:
            return None
        return index if bool(mask[index]) else None

    def first_valid(self, names: list[str], mask: np.ndarray) -> int:
        for name in names:
            index = self.try_action(name, mask)
            if index is not None:
                return index
        return WAIT_INDEX

    def upkeep_wishes(self, info: dict[str, Any]) -> list[str]:
        """Fleet-keeping directives every sensible policy issues first:
        request human help for disabled robots."""
        wishes: list[str] = []
        for robot in info["robots"]:
            if robot["awaiting_human"]:
                continue
            if robot["estop_latched"]:
                wishes.append(
                    f"request_human_assistance({robot['robot_id']},"
                    f"{HumanAssistReason.EMERGENCY_STOP_RESET.value})"
                )
            elif robot["activity"] == RobotActivity.FAILED.value:
                wishes.append(
                    f"request_human_assistance({robot['robot_id']},"
                    f"{HumanAssistReason.ROBOT_FAILURE.value})"
                )
        return wishes

    def distance_from_label(self, location_label: str, dest: Point2D) -> float:
        pos = self._positions.get(location_label)
        if pos is None:  # in transit / unknown: pessimistic mid-facility guess
            pos = self.scenario.dispenser_position
        return pos.distance_to(dest)

    def ranked_zones_by_balls(self, obs: dict[str, Any]) -> list[str]:
        zone_ids = sorted(self.scenario.zone_ids)
        balls = obs["zone_balls"]
        open_flags = obs["zone_open"]
        ranked = sorted(
            (float(-balls[i]), zone_ids[i])
            for i in range(len(zone_ids))
            if open_flags[i]
        )
        return [zone_id for _, zone_id in ranked]
