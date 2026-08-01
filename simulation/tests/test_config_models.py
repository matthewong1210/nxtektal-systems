"""Typed config model behavior: placeholder coercion, range checks, typo safety."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nxt_sim.config.loader import build_bundle, ConfigError
from nxt_sim.config.models import (
    EquipmentProfile,
    ParamSource,
    PhysicalParam,
    PositiveParam,
    RobotGeometryConfig,
    SweepConfig,
    collect_placeholders,
)


def test_bare_scalar_becomes_implicit_placeholder():
    p = PhysicalParam.model_validate(0.5)
    assert p.value == 0.5
    assert p.source is ParamSource.PLACEHOLDER
    assert "implicit placeholder" in p.note


def test_explicit_supplier_source_is_preserved():
    p = PhysicalParam.model_validate({"value": 0.5, "source": "supplier", "note": "datasheet"})
    assert p.source is ParamSource.SUPPLIER


def test_positive_param_rejects_zero_and_negative():
    for bad in (0, -1.5):
        with pytest.raises(ValidationError, match="must be > 0"):
            PositiveParam.model_validate(bad)


def test_unknown_keys_are_rejected(raw_bundle):
    raw_bundle["robot"]["wheelbase_typo_m"] = 0.6
    with pytest.raises(ConfigError, match="wheelbase_typo_m"):
        build_bundle(raw_bundle)


def test_track_wider_than_chassis_rejected(raw_bundle):
    raw_bundle["robot"]["track_width_m"] = 0.9  # chassis_width is 0.7
    with pytest.raises(ConfigError, match="track_width_m"):
        build_bundle(raw_bundle)


def test_yaw_tolerance_over_90_rejected(raw_bundle):
    raw_bundle["equipment"]["maximum_yaw_error_deg"] = 120.0
    with pytest.raises(ConfigError, match="maximum_yaw_error_deg"):
        build_bundle(raw_bundle)


def test_negative_dimension_rejected(raw_bundle):
    raw_bundle["equipment"]["inlet_width_m"] = -0.8
    with pytest.raises(ConfigError, match="inlet_width_m"):
        build_bundle(raw_bundle)


def test_equipment_profile_schema_fields_exist():
    fields = set(EquipmentProfile.model_fields)
    expected = {
        "manufacturer", "model", "inlet_height_m", "inlet_width_m", "inlet_depth_m",
        "preferred_approach_direction", "required_dump_angle_deg", "minimum_clearance_m",
        "maximum_lateral_error_m", "maximum_yaw_error_deg", "docking_marker_pose",
        "safety_zone", "unloading_cycle", "notes",
    }
    assert expected <= fields


def test_robot_geometry_has_sensor_placement():
    assert "sensors" in RobotGeometryConfig.model_fields


def test_sweep_config_rejects_empty_axes():
    with pytest.raises(ValidationError):
        SweepConfig.model_validate({"name": "s", "base_scenario": "x.yaml", "axes": []})


def test_sweep_config_rejects_duplicate_paths():
    with pytest.raises(ValidationError, match="duplicate"):
        SweepConfig.model_validate(
            {
                "name": "s",
                "base_scenario": "x.yaml",
                "axes": [
                    {"path": "scenario.initial_error.lateral_m", "values": [0.1]},
                    {"path": "scenario.initial_error.lateral_m", "values": [0.2]},
                ],
            }
        )


def test_collect_placeholders_finds_tagged_params(bundle):
    placeholders = collect_placeholders(bundle)
    paths = {p["path"] for p in placeholders}
    assert "robot.wheelbase_m" in paths
    assert "equipment.inlet_height_m" in paths
    # Marker/sensor pose geometry has model-level provenance (value is None).
    assert "equipment.docking_marker_pose" in paths
    assert all(isinstance(p["value"], float) or p["value"] is None for p in placeholders)


def test_collect_placeholders_skips_supplier_values(raw_bundle):
    raw_bundle["robot"]["wheelbase_m"] = {"value": 0.6, "source": "supplier", "note": "datasheet"}
    bundle = build_bundle(raw_bundle)
    paths = {p["path"] for p in collect_placeholders(bundle)}
    assert "robot.wheelbase_m" not in paths
