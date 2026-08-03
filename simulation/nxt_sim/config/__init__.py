"""Typed configuration models, YAML loading, and validation."""

from nxt_sim.config.loader import (
    ConfigError,
    apply_overrides,
    build_bundle,
    load_raw_bundle,
    load_scenario_bundle,
    load_sweep_config,
)
from nxt_sim.config.models import (
    DockingGuideConfig,
    EquipmentProfile,
    HandoffMechanismConfig,
    MockBehaviorConfig,
    ParamSource,
    PhysicalParam,
    RobotGeometryConfig,
    SafetyConfig,
    ScenarioBundle,
    ScenarioConfig,
    SweepAxis,
    SweepConfig,
    collect_placeholders,
)
from nxt_sim.config.validation import Issue, Severity, cross_validate, format_issues

__all__ = [
    "ConfigError",
    "DockingGuideConfig",
    "EquipmentProfile",
    "HandoffMechanismConfig",
    "Issue",
    "MockBehaviorConfig",
    "ParamSource",
    "PhysicalParam",
    "RobotGeometryConfig",
    "SafetyConfig",
    "ScenarioBundle",
    "ScenarioConfig",
    "Severity",
    "SweepAxis",
    "SweepConfig",
    "apply_overrides",
    "build_bundle",
    "collect_placeholders",
    "cross_validate",
    "format_issues",
    "load_raw_bundle",
    "load_scenario_bundle",
    "load_sweep_config",
]
