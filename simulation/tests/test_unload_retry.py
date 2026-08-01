"""Dump / verify-unloading retry behavior."""

from __future__ import annotations

from nxt_sim.scenarios.runner import run_scenario_raw


def test_unload_shortfall_retries_dump_and_succeeds(raw_bundle):
    raw_bundle["scenario"]["mock"]["force_unload_shortfalls"] = 1
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is True
    assert record["dump_attempts"] == 2  # 1 + max_dump_retries


def test_unload_shortfall_exhausts_retries(raw_bundle):
    raw_bundle["scenario"]["mock"]["force_unload_shortfalls"] = 2
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is False
    assert record["failure_reason"] == "unload_incomplete"
    assert record["underlying_failure_reason"] is None  # concrete, not aggregate
    assert record["dump_attempts"] == 2
    # Safe retract still returns the basket and undocks.
    steps = [s["step"] for s in record["steps"]]
    assert "retract_lower" in steps and "retract_undock" in steps


def test_dump_below_required_angle_warns_and_fails_unload(raw_bundle):
    """Dump angle below the equipment requirement: config warns, and the mock
    ball-flow stand-in reports incomplete unloading (p=0 in the test bundle)."""
    raw_bundle["scenario"]["dump_angle_deg"] = 30.0  # required 45
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is False
    assert record["failure_reason"] == "unload_incomplete"
    assert any("required" in w for w in record["validation_warnings"])
