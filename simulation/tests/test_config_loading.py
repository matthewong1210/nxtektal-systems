"""Shipped YAML configs load and validate; loader errors are clear; overrides work."""

from __future__ import annotations

from pathlib import Path

import pytest

from nxt_sim.config.loader import (
    ConfigError,
    apply_overrides,
    build_bundle,
    load_raw_bundle,
    load_scenario_bundle,
    load_sweep_config,
)
from nxt_sim.config.validation import Severity, cross_validate

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
SCENARIOS = sorted((CONFIGS / "scenarios").glob("*.yaml"))


@pytest.mark.parametrize(
    "path",
    [p for p in SCENARIOS if p.name != "docking_error_sweep.yaml"],
    ids=lambda p: p.name,
)
def test_shipped_scenarios_load_without_errors(path):
    bundle = load_scenario_bundle(path)
    errors = [i for i in cross_validate(bundle) if i.severity is Severity.ERROR]
    assert errors == [], f"{path.name}: {errors}"


def test_shipped_sweep_config_loads_and_paths_apply():
    cfg, base = load_sweep_config(CONFIGS / "scenarios" / "docking_error_sweep.yaml")
    raw = load_raw_bundle(base)
    point = {axis.path: axis.values[0] for axis in cfg.axes}
    bundle = build_bundle(apply_overrides(raw, {**cfg.overrides, **point}))
    assert bundle.scenario.initial_error.lateral_m == cfg.axes[0].values[0]
    assert bundle.safety.max_docking_retries == 0


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_scenario_bundle(tmp_path / "nope.yaml")


def test_missing_section_is_a_clear_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("{}\n")
    with pytest.raises(ConfigError, match="missing required top-level section 'scenario:'"):
        load_scenario_bundle(bad)


def test_wrong_section_name_is_a_clear_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not_a_scenario: {}\n")
    with pytest.raises(ConfigError, match="unexpected top-level section"):
        load_scenario_bundle(bad)


def test_missing_reference_key_is_a_clear_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("scenario:\n  name: x\n")
    with pytest.raises(ConfigError, match="robot_config"):
        load_scenario_bundle(bad)


def test_pose_outside_zone_is_an_error(raw_bundle):
    from nxt_sim.scenarios.runner import run_scenario_raw

    raw_bundle["scenario"]["station_pose"] = {"x_m": 25.0, "y_m": 5.0, "yaw_deg": 0.0}
    with pytest.raises(ConfigError, match="outside"):
        run_scenario_raw(raw_bundle)


def test_apply_overrides_deep_copy_and_physical_param(raw_bundle):
    # Bare-scalar param: the scalar is replaced (coerced at validation time).
    out = apply_overrides(raw_bundle, {"equipment.inlet_height_m": 0.75})
    assert out["equipment"]["inlet_height_m"] == 0.75
    assert raw_bundle["equipment"]["inlet_height_m"] == 0.9  # original untouched
    # Mapping-form param: only .value is replaced, provenance is re-tagged.
    out2 = apply_overrides(raw_bundle, {"scenario.payload_kg": 30.0})
    assert out2["scenario"]["payload_kg"]["value"] == 30.0
    assert "overridden" in out2["scenario"]["payload_kg"]["note"]
    # Bare-scalar param addressed via '.value' is promoted to mapping form.
    out3 = apply_overrides(raw_bundle, {"equipment.inlet_height_m.value": 0.75})
    assert out3["equipment"]["inlet_height_m"]["value"] == 0.75


def test_apply_overrides_dotted_value_path(raw_bundle):
    out = apply_overrides(raw_bundle, {"scenario.initial_error.lateral_m": 0.4})
    assert out["scenario"]["initial_error"]["lateral_m"] == 0.4


def test_apply_overrides_typo_fails_at_validation(raw_bundle):
    out = apply_overrides(raw_bundle, {"scenario.initial_eror.lateral_m": 0.4})
    with pytest.raises(ConfigError, match="initial_eror"):
        build_bundle(out)


def test_apply_overrides_scalar_descent_is_clear(raw_bundle):
    with pytest.raises(ConfigError, match="scalar"):
        apply_overrides(raw_bundle, {"scenario.name.sub.key": 1})


def test_file_reference_overrides_are_rejected(raw_bundle):
    """Overriding scenario.*_config after resolution would be a silent no-op
    (the data is already inlined) — it must be an explicit error."""
    for path in ("scenario.robot_config", "scenario.equipment_config", "scenario.safety_config"):
        with pytest.raises(ConfigError, match="cannot be overridden"):
            apply_overrides(raw_bundle, {path: "other.yaml"})


def test_misplaced_top_level_section_is_rejected(tmp_path):
    """A section at the wrong nesting level (e.g. top-level 'mock:') must not
    be silently dropped."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "scenario:\n  name: x\n  robot_config: r.yaml\n"
        "  equipment_config: e.yaml\n  safety_config: s.yaml\n"
        "mock:\n  random_seed: 1\n"
    )
    with pytest.raises(ConfigError, match="unexpected top-level section.*mock"):
        load_raw_bundle(bad)


def test_warning_scenarios_still_run(raw_bundle):
    """Infeasible-but-runnable configs are warnings, not errors (sweepable)."""
    from nxt_sim.scenarios.runner import run_scenario_raw

    raw_bundle["equipment"]["inlet_width_m"] = 0.4  # basket 0.5 cannot fit
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is False
    assert record["failure_reason"] == "recovery_exhausted"
    assert record["underlying_failure_reason"] == "geometry_incompatible"
    assert any("GEOMETRY_INCOMPATIBLE" in w for w in record["validation_warnings"])
