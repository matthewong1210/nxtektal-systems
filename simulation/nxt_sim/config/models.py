"""Typed configuration models for the Virtual Handoff Lab.

Placeholder policy (project constraint)
---------------------------------------
Real robot/equipment dimensions, masses, actuator specs, and sensor specs are
NOT known yet. Every physical quantity is therefore a :class:`PhysicalParam`
carrying a provenance tag (``source``). All shipped configs use
``source: placeholder`` with an explanatory note; the values exist only so the
pipeline can execute end-to-end and are NOT design targets. Reports count and
list every placeholder in use so no result can silently masquerade as
validated engineering data. Replace placeholders with ``supplier`` or
``measured`` values as they arrive (see docs/missing_inputs.md).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model: unknown keys are configuration errors (catches typos)."""

    model_config = ConfigDict(extra="forbid")


class ParamSource(str, Enum):
    PLACEHOLDER = "placeholder"
    SUPPLIER = "supplier"
    MEASURED = "measured"


class PhysicalParam(StrictModel):
    """A physical quantity with provenance.

    In YAML either a mapping ``{value: 0.5, source: placeholder, note: ...}``
    or a bare number (coerced to an *implicit placeholder*).
    """

    value: float
    source: ParamSource = ParamSource.PLACEHOLDER
    note: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_bare_scalar(cls, data: Any) -> Any:
        if isinstance(data, bool):
            raise ValueError("boolean is not a valid physical value")
        if isinstance(data, (int, float)):
            return {
                "value": float(data),
                "source": "placeholder",
                "note": "implicit placeholder (bare scalar in config)",
            }
        return data


class PositiveParam(PhysicalParam):
    @field_validator("value")
    @classmethod
    def _must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be > 0")
        return v


class NonNegativeParam(PhysicalParam):
    @field_validator("value")
    @classmethod
    def _must_be_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("must be >= 0")
        return v


class PoseXYZYaw(StrictModel):
    """3D mount/marker pose. Yaw in degrees, CCW positive."""

    x_m: float = 0.0
    y_m: float = 0.0
    z_m: float = 0.0
    yaw_deg: float = 0.0


class SensorMountConfig(StrictModel):
    """Placeholder sensor mount. The mock adapter does not simulate sensors;
    this exists so sensor placement is a first-class, sweepable config path
    for later physics-backed adapters. Carries provenance so mount geometry
    shows up in the placeholder inventory like every other physical value."""

    name: str
    sensor_type: Literal[
        "apriltag_camera",
        "contact_switch",
        "ultrasonic",
        "lidar_2d",
        "depth_camera",
        "unknown",
    ] = "unknown"
    mount: PoseXYZYaw = PoseXYZYaw()
    source: ParamSource = ParamSource.PLACEHOLDER
    note: str = ""


# --------------------------------------------------------------------------
# Robot
# --------------------------------------------------------------------------


class RobotGeometryConfig(StrictModel):
    """Chassis geometry and drive placeholders (awaiting AgileX data)."""

    name: str
    chassis_length_m: PositiveParam
    chassis_width_m: PositiveParam
    chassis_height_m: PositiveParam
    wheelbase_m: PositiveParam
    track_width_m: PositiveParam
    chassis_mass_kg: PositiveParam
    max_payload_kg: PositiveParam
    com_height_unloaded_m: PositiveParam
    max_speed_mps: PositiveParam
    max_decel_mps2: PositiveParam
    drive_type: Literal["differential", "ackermann", "omnidirectional", "unknown"] = "unknown"
    drive_type_note: str = ""
    sensors: list[SensorMountConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _geometry_consistency(self) -> "RobotGeometryConfig":
        if self.track_width_m.value > self.chassis_width_m.value:
            raise ValueError(
                f"track_width_m ({self.track_width_m.value}) cannot exceed "
                f"chassis_width_m ({self.chassis_width_m.value})"
            )
        if self.wheelbase_m.value > self.chassis_length_m.value:
            raise ValueError(
                f"wheelbase_m ({self.wheelbase_m.value}) cannot exceed "
                f"chassis_length_m ({self.chassis_length_m.value})"
            )
        return self


class HandoffMechanismConfig(StrictModel):
    """Basket + lift/tilt mechanism placeholders (custom mechanism TBD).

    Supports low-cost concepts: adjustable lifting platforms and controlled
    basket tilting. A robotic arm is intentionally NOT modeled.
    """

    mechanism_type: Literal[
        "adjustable_lift_platform",
        "basket_tilt",
        "lift_and_tilt",
        "unknown",
    ] = "unknown"
    basket_length_m: PositiveParam
    basket_width_m: PositiveParam
    basket_depth_m: PositiveParam
    basket_empty_mass_kg: PositiveParam
    basket_full_mass_kg: PositiveParam
    lift_travel_m: PositiveParam
    lift_speed_mps: PositiveParam
    max_dump_angle_deg: PositiveParam
    dump_speed_deg_per_s: PositiveParam
    com_height_lifted_m: PositiveParam

    @field_validator("max_dump_angle_deg")
    @classmethod
    def _dump_angle_range(cls, p: PositiveParam) -> PositiveParam:
        if p.value > 180:
            raise ValueError("max_dump_angle_deg must be <= 180")
        return p

    @model_validator(mode="after")
    def _mass_consistency(self) -> "HandoffMechanismConfig":
        if self.basket_full_mass_kg.value < self.basket_empty_mass_kg.value:
            raise ValueError(
                "basket_full_mass_kg cannot be smaller than basket_empty_mass_kg"
            )
        return self


# --------------------------------------------------------------------------
# Equipment (ball washer / hopper) profile
# --------------------------------------------------------------------------


class DockingMarker(StrictModel):
    """Fiducial marker for final alignment, posed relative to the inlet center.

    Carries provenance: the marker mounting pose is physical geometry and must
    appear in the placeholder inventory until the bracket design is real."""

    marker_type: Literal["apriltag", "aruco", "reflector", "none"] = "apriltag"
    family_or_id: str = ""
    pose_relative_to_inlet: PoseXYZYaw = PoseXYZYaw()
    source: ParamSource = ParamSource.PLACEHOLDER
    note: str = ""


class SafetyZoneConfig(StrictModel):
    front_clearance_m: NonNegativeParam
    side_clearance_m: NonNegativeParam
    description: str = ""


class UnloadingCycleConfig(StrictModel):
    required_dwell_s: NonNegativeParam
    agitation_required: bool = False
    max_cycle_s: PositiveParam


class DockingGuideConfig(StrictModel):
    """Passive docking guide (funnel / rails / V-guide) mounted at the station.

    ``lateral_correction_factor`` is a first-order model constant: the fraction
    of residual error remaining after the guide captures the robot. It is a
    modeling knob until real guide geometry exists.
    """

    guide_type: Literal["none", "funnel", "rails", "v_guide"] = "funnel"
    capture_half_width_m: NonNegativeParam
    capture_yaw_deg: NonNegativeParam
    guide_depth_m: NonNegativeParam
    lateral_correction_factor: float = Field(0.2, ge=0.0, le=1.0)


class EquipmentProfile(StrictModel):
    """Modular equipment compatibility schema (ball washer / hopper / etc.)."""

    manufacturer: str
    model: str
    inlet_height_m: PositiveParam
    inlet_width_m: PositiveParam
    inlet_depth_m: PositiveParam
    preferred_approach_direction: Literal["front", "left", "right", "rear"] = "front"
    required_dump_angle_deg: PositiveParam
    minimum_clearance_m: NonNegativeParam
    maximum_lateral_error_m: PositiveParam
    maximum_yaw_error_deg: PositiveParam
    docking_marker_pose: DockingMarker = DockingMarker()
    docking_guide: DockingGuideConfig
    safety_zone: SafetyZoneConfig
    unloading_cycle: UnloadingCycleConfig
    notes: str = ""

    @field_validator("required_dump_angle_deg")
    @classmethod
    def _required_dump_range(cls, p: PositiveParam) -> PositiveParam:
        if p.value > 180:
            raise ValueError("required_dump_angle_deg must be <= 180")
        return p

    @field_validator("maximum_yaw_error_deg")
    @classmethod
    def _yaw_tolerance_range(cls, p: PositiveParam) -> PositiveParam:
        if p.value > 90:
            raise ValueError("maximum_yaw_error_deg must be <= 90")
        return p


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


class StepTimeoutsConfig(StrictModel):
    """Per-step timeout budgets in seconds (engineering config, not physical data)."""

    navigate_s: float = Field(120.0, gt=0)
    approach_s: float = Field(60.0, gt=0)
    dock_s: float = Field(30.0, gt=0)
    verify_dock_s: float = Field(10.0, gt=0)
    lift_s: float = Field(30.0, gt=0)
    dump_s: float = Field(30.0, gt=0)
    verify_unload_s: float = Field(10.0, gt=0)
    lower_s: float = Field(30.0, gt=0)
    undock_s: float = Field(20.0, gt=0)
    return_s: float = Field(120.0, gt=0)
    recovery_s: float = Field(30.0, gt=0)


class SafetyConfig(StrictModel):
    emergency_stop_distance_m: PositiveParam
    max_ground_slope_deg: NonNegativeParam
    tipping_margin_threshold: float = Field(
        0.25,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum acceptable normalized static stability margin "
            "(1.0 = flat ground, 0.0 = static tip-over). Heuristic indicator, "
            "not validated dynamics."
        ),
    )
    max_docking_retries: int = Field(2, ge=0)
    max_dump_retries: int = Field(1, ge=0)
    step_timeouts_s: StepTimeoutsConfig = StepTimeoutsConfig()


# --------------------------------------------------------------------------
# Scenario
# --------------------------------------------------------------------------


class PoseConfig(StrictModel):
    x_m: float
    y_m: float
    yaw_deg: float = 0.0


class InitialErrorConfig(StrictModel):
    """Pose error at the start of the final approach (sweep dimensions)."""

    lateral_m: float = 0.0
    longitudinal_m: float = 0.0
    yaw_deg: float = 0.0


class ObstacleConfig(StrictModel):
    """A test obstacle placed on the robot's path (for e-stop scenarios)."""

    distance_along_path_m: float = Field(gt=0)
    description: str = ""


class FacilityConfig(StrictModel):
    """The handoff/docking zone. Deliberately small (~10 m x 10 m):
    this is NOT a golf-course digital twin."""

    zone_length_m: float = Field(10.0, gt=0, le=20)
    zone_width_m: float = Field(10.0, gt=0, le=20)
    ground_slope_deg: NonNegativeParam = NonNegativeParam(
        value=0.0, source=ParamSource.PLACEHOLDER, note="site slope unknown"
    )


class MockBehaviorConfig(StrictModel):
    """Knobs of the MOCK adapter only. SIMULATION-SPECIFIC by design:
    task logic never reads this section, and physics-backed adapters ignore it.

    The unload probabilities are a stand-in for granular ball flow, which
    simulation does NOT validate (bridging/jamming are physical-test risks).
    ``force_*`` fields deterministically fail the first N calls of a step —
    test hooks for retry/recovery/e-stop behavior.
    """

    random_seed: int = 42
    marker_alignment_gain: float = Field(0.5, ge=0.0, le=1.0)
    approach_noise_lateral_std_m: float = Field(0.02, ge=0.0)
    approach_noise_longitudinal_std_m: float = Field(0.02, ge=0.0)
    approach_noise_yaw_std_deg: float = Field(0.5, ge=0.0)
    alignment_duration_s: float = Field(3.0, gt=0)
    dock_creep_speed_mps: float = Field(0.1, gt=0)
    verify_duration_s: float = Field(0.5, gt=0)
    recovery_duration_s: float = Field(5.0, gt=0)
    unload_success_probability_at_required_angle: float = Field(0.98, ge=0.0, le=1.0)
    unload_success_probability_below_required_angle: float = Field(0.1, ge=0.0, le=1.0)
    recovery_success_probability: float = Field(1.0, ge=0.0, le=1.0)
    force_dock_failures: int = Field(0, ge=0)
    force_verify_dock_failures: int = Field(0, ge=0)
    force_unload_shortfalls: int = Field(0, ge=0)
    force_recovery_failures: int = Field(0, ge=0)


class ScenarioConfig(StrictModel):
    name: str = Field(min_length=1)
    description: str = ""
    adapter: Literal["mock", "isaac_sim", "ros2"] = "mock"
    robot_config: str = Field(min_length=1, description="path relative to this file")
    equipment_config: str = Field(min_length=1)
    safety_config: str = Field(min_length=1)
    facility: FacilityConfig = FacilityConfig()
    start_pose: PoseConfig
    staging_pose: PoseConfig
    station_pose: PoseConfig
    charge_pose: PoseConfig
    initial_error: InitialErrorConfig = InitialErrorConfig()
    payload_kg: NonNegativeParam
    lift_target_height_m: PositiveParam
    dump_angle_deg: PositiveParam
    obstacle: Optional[ObstacleConfig] = None
    mock: MockBehaviorConfig = MockBehaviorConfig()


class ScenarioBundle(StrictModel):
    """A fully resolved scenario: everything one run needs."""

    scenario: ScenarioConfig
    robot: RobotGeometryConfig
    mechanism: HandoffMechanismConfig
    equipment: EquipmentProfile
    safety: SafetyConfig


# --------------------------------------------------------------------------
# Parameter sweeps
# --------------------------------------------------------------------------


class SweepAxis(StrictModel):
    """One sweep dimension: a dotted path into the raw bundle dict.

    Examples: ``scenario.initial_error.lateral_m``,
    ``equipment.inlet_height_m.value``, ``safety.emergency_stop_distance_m.value``,
    ``mechanism.dump_speed_deg_per_s.value``, ``scenario.facility.ground_slope_deg.value``.
    """

    path: str = Field(min_length=1)
    values: list[float] = Field(min_length=1)


class SweepConfig(StrictModel):
    name: str = Field(min_length=1)
    base_scenario: str = Field(min_length=1, description="path relative to this file")
    overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="static overrides applied to every run (dotted paths)",
    )
    axes: list[SweepAxis] = Field(min_length=1)
    repeats_per_point: int = Field(1, ge=1)
    base_seed: int = 42

    @model_validator(mode="after")
    def _unique_axis_paths(self) -> "SweepConfig":
        paths = [a.path for a in self.axes]
        if len(paths) != len(set(paths)):
            raise ValueError(f"duplicate sweep axis paths: {paths}")
        return self


# --------------------------------------------------------------------------
# Placeholder inventory
# --------------------------------------------------------------------------


def collect_placeholders(node: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Walk a model tree and list every PhysicalParam still tagged placeholder.

    Used by reporting so every result explicitly discloses which physical
    values are placeholders rather than supplier/measured data.
    """
    out: list[dict[str, Any]] = []
    if isinstance(node, PhysicalParam):
        if node.source is ParamSource.PLACEHOLDER:
            out.append({"path": prefix, "value": node.value, "note": node.note})
        return out
    if isinstance(node, (DockingMarker, SensorMountConfig)):
        # Pose geometry with model-level provenance (no single scalar value).
        if node.source is ParamSource.PLACEHOLDER:
            out.append({"path": prefix, "value": None, "note": node.note})
        return out
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            child = getattr(node, name)
            child_prefix = f"{prefix}.{name}" if prefix else name
            out.extend(collect_placeholders(child, child_prefix))
        return out
    if isinstance(node, (list, tuple)):
        for i, child in enumerate(node):
            out.extend(collect_placeholders(child, f"{prefix}[{i}]"))
        return out
    if isinstance(node, dict):
        for key, child in node.items():
            out.extend(collect_placeholders(child, f"{prefix}.{key}" if prefix else str(key)))
    return out
