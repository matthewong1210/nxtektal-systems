"""Shared fixtures: a deterministic in-memory bundle and a scripted spy robot.

The test bundle mirrors the shipped placeholder configs but zeroes the mock
sensor noise and sets probabilities to 1.0, so threshold assertions are exact
and runs are fully deterministic regardless of seed.
"""

from __future__ import annotations

import copy
from typing import Callable, Optional

import pytest

from nxt_sim.config.loader import build_bundle
from nxt_sim.interfaces.robot_task_interface import RobotTaskInterface
from nxt_sim.interfaces.types import FailureReason, Pose2D, TaskResult, TaskStatus


def make_raw_bundle() -> dict:
    return {
        "scenario": {
            "name": "test_scenario",
            "adapter": "mock",
            "robot_config": "in-memory",
            "equipment_config": "in-memory",
            "safety_config": "in-memory",
            "facility": {
                "zone_length_m": 10.0,
                "zone_width_m": 10.0,
                "ground_slope_deg": {"value": 1.0, "source": "placeholder", "note": "test"},
            },
            "start_pose": {"x_m": 1.0, "y_m": 1.0, "yaw_deg": 0.0},
            "staging_pose": {"x_m": 6.0, "y_m": 5.0, "yaw_deg": 0.0},
            "station_pose": {"x_m": 8.0, "y_m": 5.0, "yaw_deg": 0.0},
            "charge_pose": {"x_m": 1.0, "y_m": 9.0, "yaw_deg": 90.0},
            "initial_error": {"lateral_m": 0.0, "longitudinal_m": 0.0, "yaw_deg": 0.0},
            "payload_kg": {"value": 25.0, "source": "placeholder", "note": "test"},
            "lift_target_height_m": {"value": 1.0, "source": "placeholder", "note": "test"},
            "dump_angle_deg": {"value": 60.0, "source": "placeholder", "note": "test"},
            "mock": {
                "random_seed": 42,
                "marker_alignment_gain": 0.5,
                "approach_noise_lateral_std_m": 0.0,
                "approach_noise_longitudinal_std_m": 0.0,
                "approach_noise_yaw_std_deg": 0.0,
                "unload_success_probability_at_required_angle": 1.0,
                "unload_success_probability_below_required_angle": 0.0,
                "recovery_success_probability": 1.0,
            },
        },
        "robot": {
            "name": "test_chassis",
            "chassis_length_m": 0.9,
            "chassis_width_m": 0.7,
            "chassis_height_m": 0.4,
            "wheelbase_m": 0.6,
            "track_width_m": 0.55,
            "chassis_mass_kg": 60.0,
            "max_payload_kg": 80.0,
            "com_height_unloaded_m": 0.25,
            "max_speed_mps": 1.0,
            "max_decel_mps2": 2.0,
            "drive_type": "differential",
        },
        "mechanism": {
            "mechanism_type": "lift_and_tilt",
            "basket_length_m": 0.6,
            "basket_width_m": 0.5,
            "basket_depth_m": 0.3,
            "basket_empty_mass_kg": 8.0,
            "basket_full_mass_kg": 38.0,
            "lift_travel_m": 1.2,
            "lift_speed_mps": 0.15,
            "max_dump_angle_deg": 120.0,
            "dump_speed_deg_per_s": 30.0,
            "com_height_lifted_m": 0.9,
        },
        "equipment": {
            "manufacturer": "TEST",
            "model": "TEST_WASHER",
            "inlet_height_m": 0.9,
            "inlet_width_m": 0.8,
            "inlet_depth_m": 0.4,
            "preferred_approach_direction": "front",
            "required_dump_angle_deg": 45.0,
            "minimum_clearance_m": 0.03,
            "maximum_lateral_error_m": 0.05,
            "maximum_yaw_error_deg": 5.0,
            "docking_guide": {
                "guide_type": "funnel",
                "capture_half_width_m": 0.25,
                "capture_yaw_deg": 6.0,
                "guide_depth_m": 0.35,
                "lateral_correction_factor": 0.2,
            },
            "safety_zone": {"front_clearance_m": 1.5, "side_clearance_m": 0.8},
            "unloading_cycle": {"required_dwell_s": 4.0, "max_cycle_s": 120.0},
        },
        "safety": {
            "emergency_stop_distance_m": 0.5,
            "max_ground_slope_deg": 5.0,
            "tipping_margin_threshold": 0.25,
            "max_docking_retries": 2,
            "max_dump_retries": 1,
        },
    }


@pytest.fixture
def raw_bundle() -> dict:
    return copy.deepcopy(make_raw_bundle())


@pytest.fixture
def bundle(raw_bundle):
    return build_bundle(raw_bundle)


def ok(duration: float = 1.0, **data) -> TaskResult:
    return TaskResult(TaskStatus.SUCCESS, duration_s=duration, data=data)


def fail(reason: FailureReason = FailureReason.NONE, duration: float = 1.0) -> TaskResult:
    return TaskResult(TaskStatus.FAILURE, failure_reason=reason, duration_s=duration)


class SpyRobot(RobotTaskInterface):
    """Scripted RobotTaskInterface: records calls, pops queued results.

    ``script`` maps a step name to a list of TaskResults consumed in order;
    when exhausted (or absent) the step succeeds with duration 1.0.
    ``on_call`` is invoked with the step name before each result is returned.
    """

    def __init__(
        self,
        script: Optional[dict[str, list[TaskResult]]] = None,
        on_call: Optional[Callable[[str], None]] = None,
    ):
        self.calls: list[str] = []
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.on_call = on_call
        self.estop_latched = False

    def _next(self, step: str) -> TaskResult:
        self.calls.append(step)
        if self.on_call is not None:
            self.on_call(step)
        queued = self.script.get(step)
        if queued:
            return queued.pop(0)
        return ok()

    def navigate_to_pose(self, pose: Pose2D, timeout_s: float) -> TaskResult:
        return self._next("navigate_to_pose")

    def approach_handoff_station(self, station_id: str, timeout_s: float) -> TaskResult:
        return self._next("approach_handoff_station")

    def dock(self, timeout_s: float) -> TaskResult:
        return self._next("dock")

    def verify_docking(self, timeout_s: float) -> TaskResult:
        return self._next("verify_docking")

    def lift(self, target_height_m: float, timeout_s: float) -> TaskResult:
        return self._next("lift")

    def dump(self, target_angle_deg: float, timeout_s: float) -> TaskResult:
        return self._next("dump")

    def verify_unloading(self, timeout_s: float) -> TaskResult:
        return self._next("verify_unloading")

    def lower(self, timeout_s: float) -> TaskResult:
        return self._next("lower")

    def undock(self, timeout_s: float) -> TaskResult:
        return self._next("undock")

    def return_to_charge(self, timeout_s: float) -> TaskResult:
        return self._next("return_to_charge")

    def emergency_stop(self) -> TaskResult:
        self.calls.append("emergency_stop")
        self.estop_latched = True
        return ok(0.05)

    def recover_from_failed_docking(self, timeout_s: float) -> TaskResult:
        return self._next("recover_from_failed_docking")
