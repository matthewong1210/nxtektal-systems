"""Finite SIMULATION candidates over the existing inventory-threshold policy.

This module owns simulator choices only. Inputs are detached current observations;
neither a joint input timeline nor the evaluator's true metrics reaches ``act``.
Staff completion means inspection labor, never verified repair of a course defect.
"""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping, Iterator
from typing import Any

import numpy as np

from nxt_range_ops.config.models import RangeOpsScenario
from nxt_range_ops.env.actions import ActionCatalog
from nxt_range_ops.policies.base import WAIT_INDEX
from nxt_range_ops.policies.baselines import InventoryThresholdPolicy

CANDIDATE_SCHEMA = "nxt-joint-policy-candidate/v0"


class VisibleActionMask(Mapping[int, bool]):
    """Only currently admitted indices; missing indices read as false.

    The full-day catalog size and the trailing unavailable staff slots are not
    exposed. This is an immutable view for the frozen policies, not a Python
    sandbox for arbitrary external policy code.
    """

    __slots__ = ("_allowed",)

    def __init__(self, raw):
        indices = (index for index, allowed in raw.items() if allowed) if isinstance(raw, Mapping) else np.flatnonzero(raw)
        self._allowed = frozenset(int(index) for index in indices)

    def __getitem__(self, key: int) -> bool:
        return key in self._allowed

    def __iter__(self) -> Iterator[int]:
        return iter(sorted(self._allowed))

    def __len__(self) -> int:
        return len(self._allowed)


class VisibleActionCatalog:
    """Name lookup facade: no specs, length or enumeration of future jobs."""

    __slots__ = ("_lookup", "_staff_names")
    _FLEET_PREFIXES = ("assign_collection(", "send_to_handoff(", "send_to_charge(",
                       "reassign_robot(", "pause_robot(", "resume_robot(",
                       "request_human_assistance(")

    def __init__(self, catalog):
        self._lookup = catalog.index_of
        self._staff_names = frozenset()

    def observe_jobs(self, jobs: list[dict]) -> None:
        self._staff_names = frozenset(f"assign_staff_work({job['job_id']})" for job in jobs)

    def index_of(self, name: str) -> int:
        if name == "wait" or name.startswith(self._FLEET_PREFIXES) or name in self._staff_names:
            return self._lookup(name)
        raise KeyError(name)


def candidate_catalog() -> list[dict]:
    """Frozen search space; reserve cover is minutes, not a safety threshold."""
    values = (
        ("baseline", 0.60, 0.85, 0.10, 0.0, False),
        ("balanced", 0.70, 0.80, 0.10, 30.0, False),
        ("reserve", 0.80, 0.75, 0.15, 60.0, False),
        ("responsive", 0.70, 0.75, 0.10, 30.0, True),
        ("early_collection", 0.85, 0.85, 0.10, 45.0, True),
        ("long_collection", 0.65, 0.95, 0.10, 20.0, True),
    )
    keys = ("candidate_id", "low_inventory_frac", "payload_handoff_frac",
            "battery_margin", "staff_reserve_cover", "use_inventory_trend")
    return [{"schema": CANDIDATE_SCHEMA, **dict(zip(keys, row))} for row in values]


def validate_candidate(candidate: dict) -> dict:
    """Only exact versioned candidates are accepted; no safety/config injection."""
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be a catalog object")
    for allowed in candidate_catalog():
        if candidate.get("candidate_id") == allowed["candidate_id"]:
            if set(candidate) != set(allowed):
                break
            if any(type(candidate[key]) is not type(value) or candidate[key] != value
                   for key, value in allowed.items()):
                break
            return deepcopy(allowed)
    raise ValueError("candidate must exactly match the frozen finite catalog")


def policy_scenario(scenario: RangeOpsScenario) -> RangeOpsScenario:
    """Retain static layout/capacity, remove episode labels and hidden timelines.

    Demand is observed through the published forecast, not configuration windows.
    This is a detached policy configuration, never used to construct the runtime.
    """
    data = scenario.model_dump(mode="json")
    data["name"], data["description"] = "policy-visible-layout", ""
    data["demand"]["spikes"] = []
    data["demand"]["windows"] = [{"window": {
        "start_minute": scenario.hours.open_minute,
        "end_minute": scenario.hours.close_minute,
    }, "balls_per_minute": 0.0}]
    for zone in data["zones"]:
        zone["closure_windows"] = []
    for station in data["stations"]:
        station["outage_windows"] = []
    return RangeOpsScenario.model_validate(data)


_OBS_KEYS = (
    "minute_of_day", "minutes_to_close", "dispenser_inventory_frac",
    "washer_wip_frac", "demand_forecast", "zone_balls", "zone_open",
    "zone_committed_robots", "robot_battery", "robot_payload_frac",
    "robot_activity", "robot_health", "robot_zone", "station_queue",
    "station_buffer_frac", "station_open", "charger_queue",
)
_ROBOT_KEYS = ("robot_id", "activity", "health", "awaiting_human",
               "estop_latched", "payload_capacity_balls", "location")
_JOB_KEYS = ("job_id", "available_minute", "duration_minutes", "deadline_minute",
             "hole_number", "checkpoint_id", "status", "assigned_at_s",
             "started_at_s", "completed_at_s")


def policy_inputs(obs: dict, info: dict) -> tuple[dict, dict]:
    """Whitelist, detach, and replace true robot battery with sensed observations.

    Unknown future fields are absent by default. Action mask may encode admission,
    but unavailable staff jobs and their release/deadline details remain hidden.
    """
    visible = {key: deepcopy(obs[key]) for key in _OBS_KEYS if key in obs}
    clean: dict[str, Any] = {
        "t_s": float(info["t_s"]),
        "step": int(info["step"]),
        "action_mask": VisibleActionMask(info["action_mask"]),
        "robots": [],
    }
    # The environment's robot arrays and snapshots both use sorted robot IDs.
    for index, robot in enumerate(info["robots"]):
        item = {key: deepcopy(robot[key]) for key in _ROBOT_KEYS if key in robot}
        item["battery_frac"] = float(visible["robot_battery"][index])
        item["payload_balls"] = (float(visible["robot_payload_frac"][index])
                                 * item["payload_capacity_balls"])
        clean["robots"].append(item)
    joint = info.get("joint_ops")
    if joint is not None:
        staff = joint["staff"]
        jobs = [
            {key: deepcopy(job[key]) for key in _JOB_KEYS if key in job}
            for job in joint["staff_jobs"]
            if job["available_minute"] <= clean["t_s"] / 60
        ]
        clean["joint_ops"] = {
            "staff": {key: int(staff[key]) for key in ("capacity", "busy", "queued")},
            "staff_jobs": jobs,
            "collection_access": deepcopy(joint.get("collection_access", {})),
        }
    return visible, clean


class JointDispatchPolicy(InventoryThresholdPolicy):
    """Threshold collection plus observed-pressure staff reservation.

    Robot recovery and threshold fleet upkeep take precedence over staff jobs.
    An optional trend uses two past sensed inventories only; it does not peek at
    shocks, served demand, weather timelines, reward or ground-truth inventory.
    """

    version = "joint-dispatch/v0"

    def __init__(self, scenario: RangeOpsScenario, catalog: ActionCatalog,
                 candidate: dict, seed: int = 0):
        self.candidate = validate_candidate(candidate)
        super().__init__(policy_scenario(scenario), VisibleActionCatalog(catalog), seed,
                         low_inventory_frac=self.candidate["low_inventory_frac"],
                         payload_handoff_frac=self.candidate["payload_handoff_frac"],
                         battery_margin=self.candidate["battery_margin"])
        self.name = self.candidate["candidate_id"]
        self._previous_inventory: tuple[float, float] | None = None

    def reset(self) -> None:
        self._previous_inventory = None
        self.catalog.observe_jobs([])

    def act(self, obs: dict[str, Any], info: dict[str, Any]) -> int:
        obs, info = policy_inputs(obs, info)
        self.catalog.observe_jobs(info.get("joint_ops", {}).get("staff_jobs", []))
        now = info["t_s"]
        sensed = float(obs["dispenser_inventory_frac"][0])
        trend_per_minute = 0.0
        if self._previous_inventory is not None:
            before_time, before_inventory = self._previous_inventory
            if now > before_time:
                trend_per_minute = max(0.0, (before_inventory - sensed)
                                       * self.scenario.total_balls * 60
                                       / (now - before_time))
        self._previous_inventory = (now, sensed)
        if self.candidate["use_inventory_trend"]:
            horizon = min(30.0, float(self.scenario.demand.forecast_bucket_minutes))
            anticipated = max(0.0, sensed - trend_per_minute * horizon
                              / self.scenario.total_balls)
            fleet_obs = dict(obs)
            fleet_obs["dispenser_inventory_frac"] = np.asarray([anticipated])
        else:
            fleet_obs = obs
        # Reuse the existing threshold policy's choices and mask checks.
        fleet_action = super().act(fleet_obs, info)
        if fleet_action != WAIT_INDEX:
            return fleet_action
        joint = info.get("joint_ops")
        if joint is None:
            return WAIT_INDEX
        staff = joint["staff"]
        if staff["queued"] or staff["busy"] >= staff["capacity"]:
            return WAIT_INDEX
        # Even an already-requested recovery takes precedence over new labor.
        if any(robot["awaiting_human"] or robot["estop_latched"]
               or robot["activity"] == "failed" for robot in info["robots"]):
            return WAIT_INDEX
        forecast = np.asarray(obs["demand_forecast"], dtype=float)
        expected_rate = max(0.0, float(np.mean(forecast))) if forecast.size else 0.0
        if self.candidate["use_inventory_trend"]:
            expected_rate = max(expected_rate, trend_per_minute)
        clean_balls = max(0.0, sensed + float(obs["washer_wip_frac"][0])) * self.scenario.total_balls
        cover = clean_balls / expected_rate if expected_rate > 0 else float("inf")
        if cover < self.candidate["staff_reserve_cover"]:
            return WAIT_INDEX
        jobs = sorted((job for job in joint["staff_jobs"] if job["status"] == "PENDING"),
                      key=lambda job: (job["deadline_minute"], job["available_minute"], job["job_id"]))
        return self.first_valid([f"assign_staff_work({job['job_id']})" for job in jobs],
                                info["action_mask"])
