"""MockSimulationAdapter — a deterministic, kinematic, discrete-event backend.

What it models (first-order, seeded, reproducible):

* 2D pose kinematics in the handoff zone; durations from placeholder speeds.
* Marker-assisted approach: each approach reduces the *current* alignment
  error by ``marker_alignment_gain`` plus seeded Gaussian sensor noise, so
  recovery + re-approach genuinely converges across retries.
* Passive docking guide (funnel/rails): captures the robot when the residual
  error is inside the capture envelope and shrinks it by
  ``lateral_correction_factor``; missing the envelope is a collision.
* Geometry compatibility vs the equipment profile (inlet width/height).
* A static tip-over margin heuristic for slope + raised center of mass.
* Emergency stop: an obstacle on the path triggers a latching e-stop at the
  configured trigger distance; the stopping distance is v^2/(2*a).

What it deliberately does NOT model — physical-test risks, never claimed to
be validated by this simulation:

* granular golf-ball flow, ball-to-ball friction, bridging, jamming
  (unloading completeness is a configured probability stand-in);
* contact forces (``peak_contact_force_n`` is reported as unavailable);
* real vehicle dynamics, wheel slip, suspension, terrain.

All numeric behavior derives from config placeholders; nothing here is a real
robot specification.
"""

from __future__ import annotations

import math
import random
from typing import Any, Optional

from nxt_sim.config.models import ScenarioBundle
from nxt_sim.interfaces.robot_task_interface import RobotTaskInterface
from nxt_sim.interfaces.types import FailureReason, Pose2D, TaskResult, TaskStatus

_STABILITY_MODEL = "static_margin_heuristic_v0"


class MockSimulationAdapter(RobotTaskInterface):
    def __init__(self, bundle: ScenarioBundle):
        self._bundle = bundle
        sc = bundle.scenario
        self._mock = sc.mock
        self._robot = bundle.robot
        self._mech = bundle.mechanism
        self._equip = bundle.equipment
        self._safety = bundle.safety
        self._rng = random.Random(self._mock.random_seed)

        # World state
        self.sim_time_s = 0.0
        self.pose = Pose2D(sc.start_pose.x_m, sc.start_pose.y_m, sc.start_pose.yaw_deg)
        self.docked = False
        self.lift_height_m = 0.0
        self.dump_angle_deg = 0.0
        self.estop_latched = False
        self.collision_count = 0
        self.min_clearance_m: Optional[float] = None
        self._unloaded = False
        self._estop_data: dict[str, Any] = {}

        # Alignment error state (updated by approach / dock / recovery)
        self._residual_lateral_m = sc.initial_error.lateral_m
        self._residual_longitudinal_m = sc.initial_error.longitudinal_m
        self._residual_yaw_deg = sc.initial_error.yaw_deg

        # Obstacle for e-stop scenarios: remaining distance along the path.
        self._obstacle_remaining_m: Optional[float] = (
            sc.obstacle.distance_along_path_m if sc.obstacle is not None else None
        )

        # Forced-failure budgets (test hooks)
        self._force_dock = self._mock.force_dock_failures
        self._force_verify = self._mock.force_verify_dock_failures
        self._force_unload = self._mock.force_unload_shortfalls
        self._force_recovery = self._mock.force_recovery_failures

        self.min_stability_margin = self._stability_margin(self._com_height(0.0))

    # ------------------------------------------------------------------
    # Physics-flavored helpers (heuristics on placeholder values)
    # ------------------------------------------------------------------

    def _com_height(self, lift_height_m: float) -> float:
        low = self._robot.com_height_unloaded_m.value
        high = self._mech.com_height_lifted_m.value
        travel = self._mech.lift_travel_m.value
        frac = 0.0 if travel <= 0 else min(1.0, lift_height_m / travel)
        return low + (high - low) * frac

    def _stability_margin(self, com_height_m: float) -> float:
        """Normalized static tip-over margin: 1.0 flat ground, <=0 tip-over.

        margin = (track/2 - tan(slope) * com_height) / (track/2). A coarse
        static indicator only — no dynamics, no payload shift, no dump
        reaction. Clearly labeled as such in every report.
        """
        half_track = self._robot.track_width_m.value / 2.0
        slope_rad = math.radians(self._bundle.scenario.facility.ground_slope_deg.value)
        return (half_track - math.tan(slope_rad) * com_height_m) / half_track

    def _update_stability(self, lift_height_m: float) -> float:
        margin = self._stability_margin(self._com_height(lift_height_m))
        self.min_stability_margin = min(self.min_stability_margin, margin)
        return margin

    def _track_clearance(self, clearance_m: float) -> None:
        if self.min_clearance_m is None or clearance_m < self.min_clearance_m:
            self.min_clearance_m = clearance_m

    def _blocked(self, step: str) -> Optional[TaskResult]:
        if self.estop_latched:
            return TaskResult(
                status=TaskStatus.EMERGENCY_STOPPED,
                failure_reason=FailureReason.EMERGENCY_STOP,
                detail=f"{step}: emergency stop is latched; motion inhibited",
            )
        return None

    def _distance_to(self, target: Pose2D) -> float:
        return math.hypot(target.x_m - self.pose.x_m, target.y_m - self.pose.y_m)

    def _timeout_result(self, timeout_s: float, detail: str) -> TaskResult:
        self.sim_time_s += timeout_s
        return TaskResult(
            status=TaskStatus.TIMEOUT,
            failure_reason=FailureReason.STEP_TIMEOUT,
            duration_s=timeout_s,
            detail=detail,
        )

    def _drive(self, distance_m: float, timeout_s: float, step: str) -> TaskResult:
        """Advance along the path; handles the obstacle -> e-stop sequence.

        The timeout budget is a hard limit even on the e-stop branch: if the
        robot cannot reach the trigger point within the budget, the drive
        times out having covered only the distance the budget allowed, and the
        obstacle bookkeeping is debited by that partial travel only.
        """
        speed = self._robot.max_speed_mps.value
        if self._obstacle_remaining_m is not None:
            trigger = self._safety.emergency_stop_distance_m.value
            if distance_m >= self._obstacle_remaining_m - trigger:
                decel = self._robot.max_decel_mps2.value
                stopping = speed * speed / (2.0 * decel)
                travel_before = max(0.0, self._obstacle_remaining_m - trigger)
                duration = travel_before / speed + speed / decel
                if duration > timeout_s:
                    traveled = min(speed * timeout_s, travel_before)
                    self._obstacle_remaining_m -= traveled
                    return self._timeout_result(
                        timeout_s,
                        f"{step}: could not reach the e-stop trigger point within "
                        f"the {timeout_s} s budget",
                    )
                final_clearance = self._obstacle_remaining_m - travel_before - stopping
                self.estop_latched = True
                self._estop_data = {
                    "emergency_stop_trigger_distance_m": trigger,
                    "emergency_stop_stopping_distance_m": round(stopping, 6),
                    "emergency_stop_final_clearance_m": round(final_clearance, 6),
                }
                if final_clearance < 0:
                    self.collision_count += 1
                self._track_clearance(final_clearance)
                self._obstacle_remaining_m = max(final_clearance, 0.0)
                self.sim_time_s += duration
                detail = (
                    f"obstacle detected {trigger:.2f} m ahead during {step}; "
                    f"emergency stop ({'collision' if final_clearance < 0 else 'stopped clear'})"
                )
                return TaskResult(
                    status=TaskStatus.EMERGENCY_STOPPED,
                    failure_reason=FailureReason.EMERGENCY_STOP,
                    duration_s=duration,
                    detail=detail,
                    data=dict(self._estop_data),
                )
            duration = distance_m / speed
            if duration > timeout_s:
                self._obstacle_remaining_m -= speed * timeout_s
                return self._timeout_result(
                    timeout_s,
                    f"{step}: needed {duration:.1f} s of travel, budget was {timeout_s} s",
                )
            self._obstacle_remaining_m -= distance_m
            self.sim_time_s += duration
            return TaskResult(status=TaskStatus.SUCCESS, duration_s=duration)
        duration = distance_m / speed
        if duration > timeout_s:
            return self._timeout_result(
                timeout_s,
                f"{step}: needed {duration:.1f} s of travel, budget was {timeout_s} s",
            )
        self.sim_time_s += duration
        return TaskResult(status=TaskStatus.SUCCESS, duration_s=duration)

    # ------------------------------------------------------------------
    # RobotTaskInterface
    # ------------------------------------------------------------------

    def navigate_to_pose(self, pose: Pose2D, timeout_s: float) -> TaskResult:
        blocked = self._blocked("navigate_to_pose")
        if blocked:
            return blocked
        result = self._drive(self._distance_to(pose), timeout_s, "navigate_to_pose")
        if result.ok:
            self.pose = pose
        return result

    def approach_handoff_station(self, station_id: str, timeout_s: float) -> TaskResult:
        blocked = self._blocked("approach_handoff_station")
        if blocked:
            return blocked
        sc = self._bundle.scenario
        station = Pose2D(sc.station_pose.x_m, sc.station_pose.y_m, sc.station_pose.yaw_deg)
        # Stop one guide-depth short of the station for the docking creep.
        approach_distance = max(
            0.2, self._distance_to(station) - self._equip.docking_guide.guide_depth_m.value
        )
        result = self._drive(approach_distance, timeout_s, "approach_handoff_station")
        if not result.ok:
            return result
        align_duration = self._mock.alignment_duration_s
        if result.duration_s + align_duration > timeout_s:
            self.sim_time_s += timeout_s - result.duration_s
            return TaskResult(
                status=TaskStatus.TIMEOUT,
                failure_reason=FailureReason.STEP_TIMEOUT,
                duration_s=timeout_s,
                detail="approach_handoff_station: marker alignment exceeded budget",
            )
        # Marker-assisted alignment acts on the CURRENT error state, so
        # repeated approaches (after recovery) converge.
        gain = self._mock.marker_alignment_gain
        self._residual_lateral_m = self._residual_lateral_m * (1.0 - gain) + self._rng.gauss(
            0.0, self._mock.approach_noise_lateral_std_m
        )
        self._residual_longitudinal_m = self._residual_longitudinal_m * (
            1.0 - gain
        ) + self._rng.gauss(0.0, self._mock.approach_noise_longitudinal_std_m)
        self._residual_yaw_deg = self._residual_yaw_deg * (1.0 - gain) + self._rng.gauss(
            0.0, self._mock.approach_noise_yaw_std_deg
        )
        self.sim_time_s += align_duration
        self.pose = Pose2D(station.x_m, station.y_m, station.yaw_deg)
        return TaskResult(
            status=TaskStatus.SUCCESS,
            duration_s=result.duration_s + align_duration,
            detail=f"aligned to {station_id}",
            data={
                "residual_lateral_m": round(self._residual_lateral_m, 6),
                "residual_longitudinal_m": round(self._residual_longitudinal_m, 6),
                "residual_yaw_deg": round(self._residual_yaw_deg, 6),
            },
        )

    def dock(self, timeout_s: float) -> TaskResult:
        blocked = self._blocked("dock")
        if blocked:
            return blocked
        if self.docked:
            return TaskResult(
                TaskStatus.FAILURE, FailureReason.INVALID_STATE, 0.0, "dock: already docked"
            )

        equip, mech, guide = self._equip, self._mech, self._equip.docking_guide

        # Geometry compatibility with the equipment profile.
        required_width = mech.basket_width_m.value + 2 * equip.minimum_clearance_m.value
        if required_width > equip.inlet_width_m.value:
            self.sim_time_s += 0.1
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.GEOMETRY_INCOMPATIBLE,
                0.1,
                f"dock: basket + clearance ({required_width:.3f} m) does not fit inlet "
                f"({equip.inlet_width_m.value} m)",
            )
        if equip.inlet_height_m.value > mech.lift_travel_m.value:
            self.sim_time_s += 0.1
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.GEOMETRY_INCOMPATIBLE,
                0.1,
                f"dock: inlet height {equip.inlet_height_m.value} m exceeds lift travel "
                f"{mech.lift_travel_m.value} m",
            )

        if self._force_dock > 0:
            self._force_dock -= 1
            self.sim_time_s += 1.0
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.LATERAL_ERROR_EXCEEDED,
                1.0,
                "dock: forced dock failure (mock test hook)",
            )

        lat = abs(self._residual_lateral_m)
        yaw = abs(self._residual_yaw_deg)
        half_gap = (equip.inlet_width_m.value - mech.basket_width_m.value) / 2.0

        if guide.guide_type != "none":
            capture_w = guide.capture_half_width_m.value
            capture_yaw = guide.capture_yaw_deg.value
            if lat > capture_w:
                self.collision_count += 1
                self._track_clearance(capture_w - lat)
                self.sim_time_s += 1.0
                return TaskResult(
                    TaskStatus.FAILURE,
                    FailureReason.COLLISION,
                    1.0,
                    f"dock: lateral error {lat:.3f} m missed the guide capture envelope "
                    f"(+/-{capture_w} m); contacted guide structure",
                )
            if yaw > capture_yaw:
                if yaw > 2 * capture_yaw:
                    self.collision_count += 1
                self.sim_time_s += 1.0
                return TaskResult(
                    TaskStatus.FAILURE,
                    FailureReason.YAW_ERROR_EXCEEDED,
                    1.0,
                    f"dock: yaw error {yaw:.2f} deg exceeds guide capture "
                    f"(+/-{capture_yaw} deg)",
                )
            post_lat = self._residual_lateral_m * guide.lateral_correction_factor
            post_yaw = self._residual_yaw_deg * guide.lateral_correction_factor
        else:
            if lat > half_gap:
                self.collision_count += 1
                self._track_clearance(half_gap - lat)
                self.sim_time_s += 1.0
                return TaskResult(
                    TaskStatus.FAILURE,
                    FailureReason.COLLISION,
                    1.0,
                    f"dock: lateral error {lat:.3f} m exceeds the inlet half-gap "
                    f"({half_gap:.3f} m) with no guide",
                )
            post_lat, post_yaw = self._residual_lateral_m, self._residual_yaw_deg

        if abs(post_lat) > equip.maximum_lateral_error_m.value:
            self.sim_time_s += 1.0
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.LATERAL_ERROR_EXCEEDED,
                1.0,
                f"dock: post-guide lateral error {abs(post_lat):.3f} m exceeds equipment "
                f"tolerance {equip.maximum_lateral_error_m.value} m",
            )
        if abs(post_yaw) > equip.maximum_yaw_error_deg.value:
            self.sim_time_s += 1.0
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.YAW_ERROR_EXCEEDED,
                1.0,
                f"dock: post-guide yaw error {abs(post_yaw):.2f} deg exceeds equipment "
                f"tolerance {equip.maximum_yaw_error_deg.value} deg",
            )

        creep_distance = guide.guide_depth_m.value + abs(self._residual_longitudinal_m)
        duration = creep_distance / self._mock.dock_creep_speed_mps
        if duration > timeout_s:
            self.sim_time_s += timeout_s
            return TaskResult(
                TaskStatus.TIMEOUT,
                FailureReason.STEP_TIMEOUT,
                timeout_s,
                f"dock: creep needed {duration:.1f} s, budget was {timeout_s} s",
            )
        self.sim_time_s += duration
        self._residual_lateral_m = post_lat
        self._residual_yaw_deg = post_yaw
        self._residual_longitudinal_m *= 0.1  # drive-to-contact squeezes this out
        self._track_clearance(half_gap - abs(post_lat))
        self.docked = True
        return TaskResult(
            TaskStatus.SUCCESS,
            duration_s=duration,
            detail="docked (guide capture + drive to contact)",
            data={
                "final_lateral_error_m": round(post_lat, 6),
                "final_yaw_error_deg": round(post_yaw, 6),
                "clearance_m": round(half_gap - abs(post_lat), 6),
            },
        )

    def verify_docking(self, timeout_s: float) -> TaskResult:
        if not self.docked:
            return TaskResult(
                TaskStatus.FAILURE, FailureReason.INVALID_STATE, 0.0, "verify_docking: not docked"
            )
        duration = self._mock.verify_duration_s
        if duration > timeout_s:
            return self._timeout_result(
                timeout_s, "verify_docking: sensor check exceeded budget"
            )
        self.sim_time_s += duration
        if self._force_verify > 0:
            self._force_verify -= 1
            self.docked = False  # contact lost — treat as a failed docking
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.DOCK_VERIFY_FAILED,
                duration,
                "verify_docking: contact-switch mismatch (mock test hook)",
            )
        return TaskResult(
            TaskStatus.SUCCESS,
            duration_s=duration,
            detail="contact switches closed; marker pose within tolerance",
        )

    def lift(self, target_height_m: float, timeout_s: float) -> TaskResult:
        blocked = self._blocked("lift")
        if blocked:
            return blocked
        if not self.docked:
            return TaskResult(
                TaskStatus.FAILURE, FailureReason.INVALID_STATE, 0.0, "lift: not docked"
            )
        travel = self._mech.lift_travel_m.value
        if target_height_m <= 0 or target_height_m > travel:
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.LIFT_FAILED,
                0.0,
                f"lift: target {target_height_m} m outside (0, {travel}] m travel",
            )
        duration = target_height_m / self._mech.lift_speed_mps.value
        if duration > timeout_s:
            self.sim_time_s += timeout_s
            return TaskResult(
                TaskStatus.TIMEOUT,
                FailureReason.STEP_TIMEOUT,
                timeout_s,
                f"lift: needed {duration:.1f} s, budget was {timeout_s} s",
            )
        self.sim_time_s += duration
        margin = self._update_stability(target_height_m)
        self.lift_height_m = target_height_m
        if margin < self._safety.tipping_margin_threshold:
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.TIPPING_RISK,
                duration,
                f"lift: static stability margin {margin:.2f} fell below threshold "
                f"{self._safety.tipping_margin_threshold} ({_STABILITY_MODEL})",
                data={"stability_margin": round(margin, 6)},
            )
        return TaskResult(
            TaskStatus.SUCCESS,
            duration_s=duration,
            data={"stability_margin": round(margin, 6)},
        )

    def dump(self, target_angle_deg: float, timeout_s: float) -> TaskResult:
        blocked = self._blocked("dump")
        if blocked:
            return blocked
        if not self.docked:
            return TaskResult(
                TaskStatus.FAILURE, FailureReason.INVALID_STATE, 0.0, "dump: not docked"
            )
        max_angle = self._mech.max_dump_angle_deg.value
        if target_angle_deg <= 0 or target_angle_deg > max_angle:
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.DUMP_FAILED,
                0.0,
                f"dump: target {target_angle_deg} deg outside (0, {max_angle}] deg",
            )
        dwell = self._equip.unloading_cycle.required_dwell_s.value
        duration = target_angle_deg / self._mech.dump_speed_deg_per_s.value + dwell
        if duration > timeout_s:
            self.sim_time_s += timeout_s
            return TaskResult(
                TaskStatus.TIMEOUT,
                FailureReason.STEP_TIMEOUT,
                timeout_s,
                f"dump: needed {duration:.1f} s, budget was {timeout_s} s",
            )
        self.sim_time_s += duration
        self.dump_angle_deg = target_angle_deg

        # Ball-flow STAND-IN (not physics; bridging/jamming are physical-test
        # risks — see docs/assumptions.md).
        if self._force_unload > 0:
            self._force_unload -= 1
            self._unloaded = False
            probability = 0.0
        else:
            meets_angle = target_angle_deg >= self._equip.required_dump_angle_deg.value
            probability = (
                self._mock.unload_success_probability_at_required_angle
                if meets_angle
                else self._mock.unload_success_probability_below_required_angle
            )
            self._unloaded = self._rng.random() < probability
        return TaskResult(
            TaskStatus.SUCCESS,
            duration_s=duration,
            detail=f"dumped to {target_angle_deg} deg, dwelled {dwell} s",
            data={"unload_model_probability": probability},
        )

    def verify_unloading(self, timeout_s: float) -> TaskResult:
        if not self.docked:
            return TaskResult(
                TaskStatus.FAILURE, FailureReason.INVALID_STATE, 0.0, "verify_unloading: not docked"
            )
        duration = self._mock.verify_duration_s
        if duration > timeout_s:
            return self._timeout_result(
                timeout_s, "verify_unloading: sensor check exceeded budget"
            )
        self.sim_time_s += duration
        if not self._unloaded:
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.UNLOAD_INCOMPLETE,
                duration,
                "verify_unloading: residual balls detected (mock stand-in model — real "
                "ball flow requires physical testing)",
            )
        return TaskResult(TaskStatus.SUCCESS, duration_s=duration, detail="basket empty")

    def lower(self, timeout_s: float) -> TaskResult:
        blocked = self._blocked("lower")
        if blocked:
            return blocked
        if not self.docked:
            return TaskResult(
                TaskStatus.FAILURE, FailureReason.INVALID_STATE, 0.0, "lower: not docked"
            )
        duration = (
            self.lift_height_m / self._mech.lift_speed_mps.value
            + self.dump_angle_deg / self._mech.dump_speed_deg_per_s.value
        )
        if duration > timeout_s:
            self.sim_time_s += timeout_s
            return TaskResult(
                TaskStatus.TIMEOUT,
                FailureReason.STEP_TIMEOUT,
                timeout_s,
                f"lower: needed {duration:.1f} s, budget was {timeout_s} s",
            )
        self.sim_time_s += duration
        self.lift_height_m = 0.0
        self.dump_angle_deg = 0.0
        return TaskResult(TaskStatus.SUCCESS, duration_s=duration, detail="basket stowed")

    def undock(self, timeout_s: float) -> TaskResult:
        blocked = self._blocked("undock")
        if blocked:
            return blocked
        if not self.docked:
            return TaskResult(
                TaskStatus.FAILURE, FailureReason.INVALID_STATE, 0.0, "undock: not docked"
            )
        if self.lift_height_m > 1e-6 or self.dump_angle_deg > 1e-6:
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.UNDOCK_FAILED,
                0.0,
                "undock: interlock — basket must be lowered and level before undocking",
            )
        duration = self._equip.docking_guide.guide_depth_m.value / self._mock.dock_creep_speed_mps
        if duration > timeout_s:
            self.sim_time_s += timeout_s
            return TaskResult(
                TaskStatus.TIMEOUT, FailureReason.STEP_TIMEOUT, timeout_s, "undock: over budget"
            )
        self.sim_time_s += duration
        self.docked = False
        return TaskResult(TaskStatus.SUCCESS, duration_s=duration, detail="reversed clear of guide")

    def return_to_charge(self, timeout_s: float) -> TaskResult:
        blocked = self._blocked("return_to_charge")
        if blocked:
            return blocked
        sc = self._bundle.scenario
        charge = Pose2D(sc.charge_pose.x_m, sc.charge_pose.y_m, sc.charge_pose.yaw_deg)
        result = self._drive(self._distance_to(charge), timeout_s, "return_to_charge")
        if result.ok:
            self.pose = charge
        return result

    def emergency_stop(self) -> TaskResult:
        self.estop_latched = True
        self.sim_time_s += 0.05
        return TaskResult(
            TaskStatus.SUCCESS,
            duration_s=0.05,
            detail="emergency stop latched; motion inhibited until external reset",
        )

    def recover_from_failed_docking(self, timeout_s: float) -> TaskResult:
        blocked = self._blocked("recover_from_failed_docking")
        if blocked:
            return blocked
        if self.docked:
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.INVALID_STATE,
                0.0,
                "recover_from_failed_docking: robot is docked, not in a failed-docking state",
            )
        duration = self._mock.recovery_duration_s
        if duration > timeout_s:
            return self._timeout_result(
                timeout_s, "recover_from_failed_docking: back-off maneuver exceeded budget"
            )
        self.sim_time_s += duration
        if self._force_recovery > 0:
            self._force_recovery -= 1
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.RECOVERY_FAILED,
                duration,
                "recover_from_failed_docking: forced recovery failure (mock test hook)",
            )
        if self._rng.random() >= self._mock.recovery_success_probability:
            return TaskResult(
                TaskStatus.FAILURE,
                FailureReason.RECOVERY_FAILED,
                duration,
                "recover_from_failed_docking: back-off maneuver failed (mock model)",
            )
        return TaskResult(
            TaskStatus.SUCCESS,
            duration_s=duration,
            detail="backed off to standoff; ready for a fresh approach",
        )

    # ------------------------------------------------------------------
    # Telemetry (instrumentation, consumed by the runner — never by task logic)
    # ------------------------------------------------------------------

    def get_telemetry(self) -> dict[str, Any]:
        return {
            "sim_time_s": round(self.sim_time_s, 6),
            "pose": {"x_m": self.pose.x_m, "y_m": self.pose.y_m, "yaw_deg": self.pose.yaw_deg},
            "docked": self.docked,
            "lift_height_m": self.lift_height_m,
            "dump_angle_deg": self.dump_angle_deg,
            "collision_count": self.collision_count,
            "min_clearance_m": (
                round(self.min_clearance_m, 6) if self.min_clearance_m is not None else None
            ),
            "peak_contact_force_n": None,
            "contact_force_available": False,
            "min_stability_margin": round(self.min_stability_margin, 6),
            "stability_model": _STABILITY_MODEL,
            "emergency_stop_triggered": self.estop_latched,
            **self._estop_data,
            "residual_lateral_error_m": round(self._residual_lateral_m, 6),
            "residual_yaw_error_deg": round(self._residual_yaw_deg, 6),
            "unloaded": self._unloaded,
        }
