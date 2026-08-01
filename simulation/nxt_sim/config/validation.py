"""Cross-field scenario validation.

Field-level nonsense (negative dimensions, out-of-range angles, unknown keys)
is rejected by the pydantic models themselves. This module adds bundle-level
checks. Severity policy:

* ERROR   — the scenario cannot produce a meaningful run (poses outside the
            zone, broken references). Runs are refused.
* WARNING — physically infeasible or inconsistent, but intentionally runnable:
            sweeps must be able to explore incompatible equipment and produce
            failure classifications (GEOMETRY_INCOMPATIBLE, DUMP_FAILED, ...).
* INFO    — advisory (placeholder inventory size, guide/tolerance relations).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from nxt_sim.config.models import ScenarioBundle, collect_placeholders


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.value.upper()}] {self.path}: {self.message}"


def format_issues(issues: list[Issue]) -> str:
    return "\n".join(str(i) for i in issues)


def cross_validate(bundle: ScenarioBundle) -> list[Issue]:
    issues: list[Issue] = []
    sc, robot, mech, equip, safety = (
        bundle.scenario,
        bundle.robot,
        bundle.mechanism,
        bundle.equipment,
        bundle.safety,
    )

    def err(path: str, msg: str) -> None:
        issues.append(Issue(Severity.ERROR, path, msg))

    def warn(path: str, msg: str) -> None:
        issues.append(Issue(Severity.WARNING, path, msg))

    def info(path: str, msg: str) -> None:
        issues.append(Issue(Severity.INFO, path, msg))

    # --- poses must be inside the handoff zone (ERROR) ---
    zone_l, zone_w = sc.facility.zone_length_m, sc.facility.zone_width_m
    for label, pose in (
        ("scenario.start_pose", sc.start_pose),
        ("scenario.staging_pose", sc.staging_pose),
        ("scenario.station_pose", sc.station_pose),
        ("scenario.charge_pose", sc.charge_pose),
    ):
        if not (0.0 <= pose.x_m <= zone_l and 0.0 <= pose.y_m <= zone_w):
            err(
                label,
                f"pose ({pose.x_m}, {pose.y_m}) is outside the "
                f"{zone_l} m x {zone_w} m handoff zone",
            )

    # --- mechanism vs scenario (WARNING: runnable, will fail at runtime) ---
    if sc.lift_target_height_m.value > mech.lift_travel_m.value:
        warn(
            "scenario.lift_target_height_m",
            f"target {sc.lift_target_height_m.value} m exceeds lift travel "
            f"{mech.lift_travel_m.value} m — run will fail with LIFT_FAILED",
        )
    if sc.dump_angle_deg.value > mech.max_dump_angle_deg.value:
        warn(
            "scenario.dump_angle_deg",
            f"{sc.dump_angle_deg.value} deg exceeds mechanism maximum "
            f"{mech.max_dump_angle_deg.value} deg — run will fail with DUMP_FAILED",
        )

    # --- equipment vs mechanism geometry (WARNING) ---
    if sc.dump_angle_deg.value < equip.required_dump_angle_deg.value:
        warn(
            "scenario.dump_angle_deg",
            f"{sc.dump_angle_deg.value} deg is below the equipment's required "
            f"{equip.required_dump_angle_deg.value} deg — unloading likely incomplete",
        )
    required_width = mech.basket_width_m.value + 2 * equip.minimum_clearance_m.value
    if required_width > equip.inlet_width_m.value:
        warn(
            "equipment.inlet_width_m",
            f"basket width + 2x clearance ({required_width:.3f} m) exceeds inlet width "
            f"({equip.inlet_width_m.value} m) — docking will fail with GEOMETRY_INCOMPATIBLE",
        )
    if equip.inlet_height_m.value > mech.lift_travel_m.value:
        warn(
            "equipment.inlet_height_m",
            f"inlet at {equip.inlet_height_m.value} m is above the lift travel "
            f"({mech.lift_travel_m.value} m) — docking will fail with GEOMETRY_INCOMPATIBLE",
        )
    if sc.lift_target_height_m.value < equip.inlet_height_m.value:
        warn(
            "scenario.lift_target_height_m",
            f"lift target {sc.lift_target_height_m.value} m is below the inlet height "
            f"({equip.inlet_height_m.value} m) — dump may miss the inlet",
        )

    # --- payload / slope / safety (WARNING) ---
    if sc.payload_kg.value > robot.max_payload_kg.value:
        warn(
            "scenario.payload_kg",
            f"payload {sc.payload_kg.value} kg exceeds max payload "
            f"{robot.max_payload_kg.value} kg (placeholder limits)",
        )
    slope = sc.facility.ground_slope_deg.value
    if slope > safety.max_ground_slope_deg.value:
        warn(
            "scenario.facility.ground_slope_deg",
            f"slope {slope} deg exceeds the safety limit "
            f"{safety.max_ground_slope_deg.value} deg",
        )

    # --- e-stop physics sanity (WARNING) ---
    v, a = robot.max_speed_mps.value, robot.max_decel_mps2.value
    stopping = v * v / (2 * a)
    trigger = safety.emergency_stop_distance_m.value
    if stopping >= trigger:
        warn(
            "safety.emergency_stop_distance_m",
            f"stopping distance at max speed ({stopping:.2f} m = v^2/2a) is not "
            f"shorter than the e-stop trigger distance ({trigger} m) — the robot "
            f"cannot stop before an obstacle",
        )

    # --- advisory (INFO) ---
    # The guide is the binding constraint when its capture envelope is smaller
    # than the error the post-guide tolerance could still absorb: the guide
    # rejects raw error > capture, while the equipment tolerance rejects
    # residual * correction_factor > max_error, i.e. raw error > max/factor.
    guide = equip.docking_guide
    if guide.guide_type != "none":
        factor = guide.lateral_correction_factor
        lat_absorbable = (
            equip.maximum_lateral_error_m.value / factor if factor > 0 else math.inf
        )
        if guide.capture_half_width_m.value < lat_absorbable:
            info(
                "equipment.docking_guide.capture_half_width_m",
                "guide capture envelope is the binding lateral docking constraint "
                f"(capture {guide.capture_half_width_m.value} m < "
                f"{lat_absorbable:.3f} m the post-guide tolerance could absorb)",
            )
        yaw_absorbable = (
            equip.maximum_yaw_error_deg.value / factor if factor > 0 else math.inf
        )
        if guide.capture_yaw_deg.value < yaw_absorbable:
            info(
                "equipment.docking_guide.capture_yaw_deg",
                "guide capture envelope is the binding yaw docking constraint "
                f"(capture {guide.capture_yaw_deg.value} deg < "
                f"{yaw_absorbable:.1f} deg the post-guide tolerance could absorb)",
            )
    if math.isclose(slope, 0.0):
        info("scenario.facility.ground_slope_deg", "flat ground assumed (placeholder)")

    placeholders = collect_placeholders(bundle)
    info(
        "bundle",
        f"{len(placeholders)} physical parameters are placeholders — results are "
        "pipeline-validation only, not engineering data",
    )
    return issues
