"""Mock adapter world-model behavior: guides, geometry, stability, clearance."""

from __future__ import annotations

import pytest

from nxt_sim.adapters.mock.mock_adapter import MockSimulationAdapter
from nxt_sim.config.loader import build_bundle
from nxt_sim.interfaces.types import FailureReason
from nxt_sim.scenarios.runner import run_scenario_raw


def _no_retries(raw_bundle: dict) -> dict:
    raw_bundle["safety"]["max_docking_retries"] = 0
    return raw_bundle


def test_nominal_run_succeeds_with_expected_clearance(raw_bundle):
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is True
    assert record["collision_count"] == 0
    # (inlet 0.8 - basket 0.5)/2 = 0.15 m, zero residual error.
    assert abs(record["min_clearance_m"] - 0.15) < 1e-9
    assert record["contact_force_available"] is False
    assert record["peak_contact_force_n"] is None
    assert record["docking_completion_time_s"] > 0
    assert record["unloading_cycle_time_s"] > 0
    assert record["total_cycle_time_s"] > record["docking_completion_time_s"]


def test_nominal_phase_times_match_deterministic_model(raw_bundle):
    """Pin the docking/unloading phase-time composition against the exact
    durations the deterministic test bundle implies (speed 1 m/s, lift
    0.15 m/s, dump 30 deg/s, dwell 4 s, verify 0.5 s, align 3 s, creep
    0.1 m/s, guide depth 0.35 m)."""
    import math

    import pytest

    record = run_scenario_raw(raw_bundle)
    navigate = math.hypot(6.0 - 1.0, 5.0 - 1.0)  # start -> staging at 1 m/s
    approach = max(0.2, 2.0 - 0.35) + 3.0  # staging -> station minus guide, + align
    dock = 0.35 / 0.1  # guide depth at creep speed, zero residual error
    verify = 0.5
    expected_docking = navigate + approach + dock + verify
    lift = 1.0 / 0.15
    dump = 60.0 / 30.0 + 4.0
    lower = 1.0 / 0.15 + 60.0 / 30.0
    expected_unloading = lift + dump + verify + lower
    assert record["docking_completion_time_s"] == pytest.approx(expected_docking, abs=1e-4)
    assert record["unloading_cycle_time_s"] == pytest.approx(expected_unloading, abs=1e-4)
    step_total = sum(s["duration_s"] for s in record["steps"])
    assert record["total_cycle_time_s"] == pytest.approx(step_total, abs=1e-4)


def test_lateral_error_beyond_guide_capture_is_collision(raw_bundle):
    raw_bundle = _no_retries(raw_bundle)
    raw_bundle["scenario"]["initial_error"]["lateral_m"] = 0.6  # residual 0.3 > 0.25
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is False
    assert record["failure_reason"] == "collision"
    assert record["collision_count"] == 1


def test_post_guide_lateral_error_exceeded_without_collision(raw_bundle):
    raw_bundle = _no_retries(raw_bundle)
    # Guide captures (residual 0.1 <= 0.25) but does not correct at all.
    raw_bundle["equipment"]["docking_guide"]["lateral_correction_factor"] = 1.0
    raw_bundle["scenario"]["initial_error"]["lateral_m"] = 0.2  # residual 0.1 > tol 0.05
    record = run_scenario_raw(raw_bundle)
    assert record["failure_reason"] == "lateral_error_exceeded"
    assert record["collision_count"] == 0


def test_yaw_error_beyond_capture(raw_bundle):
    raw_bundle = _no_retries(raw_bundle)
    raw_bundle["scenario"]["initial_error"]["yaw_deg"] = 16.0  # residual 8 > capture 6
    record = run_scenario_raw(raw_bundle)
    assert record["failure_reason"] == "yaw_error_exceeded"
    assert record["collision_count"] == 0  # within 2x capture: halted, no impact


def test_gross_yaw_error_also_collides(raw_bundle):
    raw_bundle = _no_retries(raw_bundle)
    raw_bundle["scenario"]["initial_error"]["yaw_deg"] = 26.0  # residual 13 > 2x capture
    record = run_scenario_raw(raw_bundle)
    assert record["failure_reason"] == "yaw_error_exceeded"
    assert record["collision_count"] == 1


def test_no_guide_narrows_the_capture_envelope(raw_bundle):
    raw_bundle = _no_retries(raw_bundle)
    raw_bundle["equipment"]["docking_guide"]["guide_type"] = "none"
    raw_bundle["scenario"]["initial_error"]["lateral_m"] = 0.4  # residual 0.2 > half-gap 0.15
    record = run_scenario_raw(raw_bundle)
    assert record["failure_reason"] == "collision"

    raw_bundle2 = _no_retries(raw_bundle)
    raw_bundle2["scenario"]["initial_error"]["lateral_m"] = 0.08  # residual 0.04 <= 0.05
    record2 = run_scenario_raw(raw_bundle2)
    assert record2["success"] is True


def test_inlet_narrower_than_basket_is_geometry_incompatible(raw_bundle):
    raw_bundle = _no_retries(raw_bundle)
    raw_bundle["equipment"]["inlet_width_m"] = 0.5  # basket 0.5 + 2x0.03 clearance
    record = run_scenario_raw(raw_bundle)
    assert record["failure_reason"] == "geometry_incompatible"


def test_inlet_above_lift_travel_is_geometry_incompatible(raw_bundle):
    raw_bundle = _no_retries(raw_bundle)
    raw_bundle["equipment"]["inlet_height_m"] = 1.5  # travel 1.2
    record = run_scenario_raw(raw_bundle)
    assert record["failure_reason"] == "geometry_incompatible"


def test_lift_target_beyond_travel_fails_and_retracts(raw_bundle):
    raw_bundle["scenario"]["lift_target_height_m"] = 1.5  # travel 1.2
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is False
    assert record["failure_reason"] == "lift_failed"
    steps = [s["step"] for s in record["steps"]]
    assert "retract_lower" in steps and "retract_undock" in steps
    assert any("exceeds" in w or "LIFT_FAILED" in w for w in record["validation_warnings"])


def test_slope_raises_tipping_risk(raw_bundle):
    raw_bundle["scenario"]["facility"]["ground_slope_deg"] = {
        "value": 12.0, "source": "placeholder", "note": "test",
    }
    raw_bundle["safety"]["tipping_margin_threshold"] = 0.5
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is False
    assert record["failure_reason"] == "tipping_risk"
    assert record["tipping_risk_indicator"] < 0.5
    assert record["stability_model"] == "static_margin_heuristic_v0"
    assert any("slope" in w for w in record["validation_warnings"])


def test_dump_angle_beyond_mechanism_limit(raw_bundle):
    raw_bundle["scenario"]["dump_angle_deg"] = 150.0  # max 120
    record = run_scenario_raw(raw_bundle)
    assert record["failure_reason"] == "dump_failed"


def test_undock_interlock_requires_stowed_basket(bundle):
    adapter = MockSimulationAdapter(bundle)
    adapter.docked = True
    adapter.lift_height_m = 0.5
    result = adapter.undock(timeout_s=20.0)
    assert not result.ok
    assert result.failure_reason is FailureReason.UNDOCK_FAILED
    assert "interlock" in result.detail


def test_out_of_order_calls_return_invalid_state(bundle):
    adapter = MockSimulationAdapter(bundle)
    assert adapter.lift(0.5, 30.0).failure_reason is FailureReason.INVALID_STATE
    assert adapter.dump(60.0, 30.0).failure_reason is FailureReason.INVALID_STATE
    assert adapter.undock(20.0).failure_reason is FailureReason.INVALID_STATE
    assert adapter.verify_docking(10.0).failure_reason is FailureReason.INVALID_STATE


def test_determinism_same_seed_same_record(raw_bundle):
    import copy

    a = run_scenario_raw(copy.deepcopy(raw_bundle))
    b = run_scenario_raw(copy.deepcopy(raw_bundle))
    assert a == b


def _with_noise(raw_bundle: dict) -> dict:
    """Non-vacuous determinism setup: real sensor noise + a real error."""
    mock = raw_bundle["scenario"]["mock"]
    mock["approach_noise_lateral_std_m"] = 0.05
    mock["approach_noise_longitudinal_std_m"] = 0.05
    mock["approach_noise_yaw_std_deg"] = 1.0
    raw_bundle["scenario"]["initial_error"]["lateral_m"] = 0.2
    raw_bundle["scenario"]["initial_error"]["yaw_deg"] = 4.0
    return raw_bundle


def test_seeded_rng_same_seed_reproducible_with_noise(raw_bundle):
    import copy

    noisy = _with_noise(raw_bundle)
    a = run_scenario_raw(copy.deepcopy(noisy), seed=123)
    b = run_scenario_raw(copy.deepcopy(noisy), seed=123)
    assert a == b
    # Noise actually flowed: the residual is not exactly the noise-free value.
    assert a["residual_lateral_error_m"] != 0.2 * 0.5 * 0.2


def test_seeded_rng_different_seeds_diverge(raw_bundle):
    import copy

    noisy = _with_noise(raw_bundle)
    a = run_scenario_raw(copy.deepcopy(noisy), seed=1)
    b = run_scenario_raw(copy.deepcopy(noisy), seed=2)
    assert a["residual_lateral_error_m"] != b["residual_lateral_error_m"]


def test_verify_docking_times_out_when_sensor_check_exceeds_budget(raw_bundle):
    raw_bundle["scenario"]["mock"]["verify_duration_s"] = 5.0
    raw_bundle["safety"]["step_timeouts_s"] = {"verify_dock_s": 1.0}
    raw_bundle["safety"]["max_docking_retries"] = 0
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is False
    assert record["failure_reason"] == "step_timeout"
    verify_steps = [s for s in record["steps"] if s["step"] == "verify_docking"]
    assert verify_steps[0]["status"] == "timeout"
    assert verify_steps[0]["duration_s"] == 1.0


def test_isaac_and_ros2_adapters_raise_with_guidance(raw_bundle):
    from nxt_sim.adapters.base import AdapterNotAvailableError, create_adapter

    for kind in ("isaac_sim", "ros2"):
        raw = dict(raw_bundle)
        raw["scenario"] = dict(raw_bundle["scenario"], adapter=kind)
        bundle = build_bundle(raw)
        with pytest.raises(AdapterNotAvailableError, match="not implemented"):
            create_adapter(bundle)
