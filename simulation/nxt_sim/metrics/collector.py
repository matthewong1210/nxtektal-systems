"""Structured metric logging for handoff cycles.

The collector receives the controller's transition/step stream during the run
and merges adapter telemetry afterwards into one flat, JSON-serializable run
record. Times are computed from step durations, so the collector works with
any adapter (mock, Isaac Sim, ROS 2) without knowing its clock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional

from nxt_sim.interfaces.types import TaskResult

if TYPE_CHECKING:  # avoid a runtime controllers<->metrics import cycle
    from nxt_sim.controllers.handoff_state_machine import CycleOutcome

# Steps whose durations make up the docking phase / unloading phase.
_DOCKING_STEPS = {
    "navigate_to_pose",
    "approach_handoff_station",
    "dock",
    "verify_docking",
    "recover_from_failed_docking",
}
_UNLOADING_STEPS = {"lift", "dump", "verify_unloading", "lower"}


class MetricsCollector:
    def __init__(
        self,
        scenario_name: str,
        seed: int,
        overrides: Optional[Mapping[str, Any]] = None,
    ):
        self.scenario_name = scenario_name
        self.seed = seed
        self.overrides = dict(overrides or {})
        self.transitions: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def record_transition(self, clock_s: float, from_state: str, to_state: str, note: str = "") -> None:
        self.transitions.append(
            {"clock_s": round(clock_s, 6), "from": from_state, "to": to_state, "note": note}
        )

    def record_task(self, step: str, result: TaskResult) -> None:
        self.steps.append(
            {
                "step": step,
                "status": result.status.value,
                "failure_reason": result.failure_reason.value,
                "duration_s": round(result.duration_s, 6),
                "detail": result.detail,
                "data": dict(result.data),
            }
        )

    def record_event(self, name: str, **data: Any) -> None:
        self.events.append({"event": name, **data})

    # ------------------------------------------------------------------

    def _phase_time(self, step_names: set[str]) -> float:
        return sum(s["duration_s"] for s in self.steps if s["step"] in step_names)

    def build_record(self, outcome: "CycleOutcome", telemetry: Mapping[str, Any]) -> dict[str, Any]:
        """Merge the controller outcome and adapter telemetry into a run record.

        Every metric from the project brief appears explicitly; metrics the
        backend cannot measure are None with an availability flag rather than
        silently absent (e.g. contact force in the mock adapter).
        """
        docking_time = self._phase_time(_DOCKING_STEPS) if outcome.docked else None
        unloading_time = self._phase_time(_UNLOADING_STEPS) if outcome.success else None

        record: dict[str, Any] = {
            "scenario": self.scenario_name,
            "seed": self.seed,
            "overrides": dict(self.overrides),
            # Outcome
            "success": outcome.success,
            "final_state": outcome.final_state.value,
            "failure_reason": outcome.failure_reason.value,
            "underlying_failure_reason": (
                outcome.underlying_failure_reason.value
                if outcome.underlying_failure_reason is not None
                else None
            ),
            # Docking metrics
            "docking_success": outcome.docked,
            "docking_attempts": outcome.docking_attempts,
            "docking_completion_time_s": (
                round(docking_time, 6) if docking_time is not None else None
            ),
            # Recovery metrics
            "recovery_attempts": outcome.recovery_attempts,
            "recovery_successes": outcome.recovery_successes,
            "failed_docking_recovered": (
                outcome.docked and outcome.recovery_attempts > 0
            ),
            # Unloading metrics
            "dump_attempts": outcome.dump_attempts,
            "unloading_cycle_time_s": (
                round(unloading_time, 6) if unloading_time is not None else None
            ),
            "total_cycle_time_s": round(outcome.clock_s, 6),
            # Collision / clearance / force (from telemetry when available)
            "collision_count": telemetry.get("collision_count", 0),
            "min_clearance_m": telemetry.get("min_clearance_m"),
            "peak_contact_force_n": telemetry.get("peak_contact_force_n"),
            "contact_force_available": telemetry.get("contact_force_available", False),
            # Stability (heuristic indicator, NOT validated dynamics)
            "tipping_risk_indicator": telemetry.get("min_stability_margin"),
            "stability_model": telemetry.get("stability_model", "unavailable"),
            # Emergency stop
            "emergency_stop_triggered": telemetry.get(
                "emergency_stop_triggered",
                outcome.final_state.value == "emergency_stopped",
            ),
            "emergency_stop_trigger_distance_m": telemetry.get(
                "emergency_stop_trigger_distance_m"
            ),
            "emergency_stop_stopping_distance_m": telemetry.get(
                "emergency_stop_stopping_distance_m"
            ),
            "emergency_stop_final_clearance_m": telemetry.get(
                "emergency_stop_final_clearance_m"
            ),
            # Residual alignment errors
            "residual_lateral_error_m": telemetry.get("residual_lateral_error_m"),
            "residual_yaw_error_deg": telemetry.get("residual_yaw_error_deg"),
            # Traces
            "transitions": list(self.transitions),
            "steps": list(self.steps),
            "events": list(self.events),
        }
        return record
