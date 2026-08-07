"""Cumulative operational metrics tracked by the simulator.

The env computes per-step deltas by snapshotting before each control
interval; the evaluation harness reads the end-of-episode totals.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace


@dataclass
class OpsMetrics:
    demand_balls_total: int = 0
    demand_balls_served: int = 0
    stockout_minutes: float = 0.0
    open_minutes_elapsed: float = 0.0
    balls_collected: int = 0
    balls_processed: int = 0  # washed and returned to the dispenser
    energy_wh: float = 0.0
    empty_travel_s: float = 0.0
    loaded_travel_s: float = 0.0
    idle_robot_s: float = 0.0
    handoff_queue_s: float = 0.0
    charger_queue_s: float = 0.0
    active_robot_s: float = 0.0
    human_interventions_requested: int = 0
    human_interventions_completed: int = 0
    hard_failures: int = 0
    estops: int = 0
    safe_recoveries: int = 0
    task_switches: int = 0
    unsafe_rejections: int = 0
    dock_attempts: int = 0
    dock_failures: int = 0

    def copy(self) -> "OpsMetrics":
        return replace(self)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def service_availability(self) -> float:
        if self.open_minutes_elapsed <= 0:
            return 1.0
        return max(0.0, 1.0 - self.stockout_minutes / self.open_minutes_elapsed)

    @property
    def demand_fill_rate(self) -> float:
        if self.demand_balls_total <= 0:
            return 1.0
        return self.demand_balls_served / self.demand_balls_total

    @property
    def safe_recovery_rate(self) -> float:
        incidents = self.hard_failures + self.estops
        if incidents <= 0:
            return 1.0
        return self.safe_recoveries / incidents
