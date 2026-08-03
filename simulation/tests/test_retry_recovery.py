"""Failed docking, recovery, and retry behavior via the mock adapter."""

from __future__ import annotations

from nxt_sim.scenarios.runner import run_scenario_raw


def test_forced_dock_failure_recovers_and_succeeds(raw_bundle):
    raw_bundle["scenario"]["mock"]["force_dock_failures"] = 1
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is True
    assert record["docking_attempts"] == 2
    assert record["recovery_attempts"] == 1
    assert record["recovery_successes"] == 1
    assert record["failed_docking_recovered"] is True


def test_verify_docking_failure_also_triggers_recovery(raw_bundle):
    raw_bundle["scenario"]["mock"]["force_verify_dock_failures"] = 1
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is True
    assert record["docking_attempts"] == 2
    assert record["recovery_attempts"] == 1


def test_retries_exhausted_classification(raw_bundle):
    raw_bundle["scenario"]["mock"]["force_dock_failures"] = 99
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is False
    assert record["docking_attempts"] == 3  # 1 + max_docking_retries (2)
    assert record["recovery_attempts"] == 2
    assert record["recovery_successes"] == 2
    assert record["failure_reason"] == "recovery_exhausted"
    assert record["underlying_failure_reason"] == "lateral_error_exceeded"
    assert record["docking_success"] is False


def test_recovery_failure_aborts_cycle(raw_bundle):
    raw_bundle["scenario"]["mock"]["force_dock_failures"] = 99
    raw_bundle["scenario"]["mock"]["force_recovery_failures"] = 1
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is False
    assert record["failure_reason"] == "recovery_failed"
    assert record["underlying_failure_reason"] == "lateral_error_exceeded"
    assert record["recovery_attempts"] == 1
    assert record["recovery_successes"] == 0


def test_retries_disabled_keeps_concrete_reason(raw_bundle):
    raw_bundle["safety"]["max_docking_retries"] = 0
    raw_bundle["scenario"]["mock"]["force_dock_failures"] = 1
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is False
    assert record["docking_attempts"] == 1
    assert record["recovery_attempts"] == 0
    assert record["failure_reason"] == "lateral_error_exceeded"
    assert record["underlying_failure_reason"] is None


def test_marker_alignment_converges_across_retries(raw_bundle):
    """0.6 m initial lateral error: attempt 1 misses the guide (collision),
    recovery + re-approach halves the error again, attempt 2 docks."""
    raw_bundle["scenario"]["initial_error"]["lateral_m"] = 0.6
    record = run_scenario_raw(raw_bundle)
    assert record["success"] is True
    assert record["docking_attempts"] == 2
    assert record["collision_count"] == 1
    assert record["failed_docking_recovered"] is True
    assert abs(record["residual_lateral_error_m"]) <= 0.05
