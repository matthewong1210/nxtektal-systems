"""Emergency-stop behavior via the mock adapter's obstacle model."""

from __future__ import annotations

from nxt_sim.scenarios.runner import run_scenario_raw


def _with_obstacle(raw_bundle: dict, distance: float) -> dict:
    raw_bundle["scenario"]["obstacle"] = {
        "distance_along_path_m": distance,
        "description": "test obstacle",
    }
    return raw_bundle


def test_obstacle_triggers_latching_estop(raw_bundle):
    record = run_scenario_raw(_with_obstacle(raw_bundle, 3.0))
    assert record["success"] is False
    assert record["final_state"] == "emergency_stopped"
    assert record["failure_reason"] == "emergency_stop"
    assert record["emergency_stop_triggered"] is True
    # v=1.0 m/s, a=2.0 m/s^2 -> stopping distance v^2/2a = 0.25 m
    assert record["emergency_stop_trigger_distance_m"] == 3.0 - 2.5  # trigger 0.5 m
    assert abs(record["emergency_stop_stopping_distance_m"] - 0.25) < 1e-9
    assert abs(record["emergency_stop_final_clearance_m"] - 0.25) < 1e-9
    assert record["collision_count"] == 0  # stopped clear of the obstacle


def test_estop_distance_too_short_causes_collision(raw_bundle):
    raw_bundle = _with_obstacle(raw_bundle, 3.0)
    raw_bundle["safety"]["emergency_stop_distance_m"] = 0.2
    record = run_scenario_raw(raw_bundle)
    assert record["emergency_stop_triggered"] is True
    # clearance = 3.0 - 2.8 - 0.25 = -0.05 -> impact
    assert record["emergency_stop_final_clearance_m"] < 0
    assert record["collision_count"] == 1
    # The scenario validator warned that stopping distance >= trigger distance.
    assert any("cannot stop" in w for w in record["validation_warnings"])


def test_estop_latch_blocks_all_further_motion(raw_bundle):
    record = run_scenario_raw(_with_obstacle(raw_bundle, 3.0))
    # The e-stop fired during navigate_to_pose; nothing else was commanded.
    steps = [s["step"] for s in record["steps"]]
    assert steps == ["navigate_to_pose"]
    assert record["steps"][0]["status"] == "emergency_stopped"
    # No retract, no recovery after an e-stop.
    assert not any(s.startswith("retract_") for s in steps)


def test_adapter_estop_latch_inhibits_every_motion_command(bundle):
    """The adapter-level latch itself (not just the controller's behavior):
    once latched, every motion-producing call must refuse to move."""
    from nxt_sim.adapters.mock.mock_adapter import MockSimulationAdapter
    from nxt_sim.interfaces.types import FailureReason, Pose2D, TaskStatus

    adapter = MockSimulationAdapter(bundle)
    assert adapter.emergency_stop().ok
    motions = [
        lambda: adapter.navigate_to_pose(Pose2D(2.0, 2.0, 0.0), 60.0),
        lambda: adapter.approach_handoff_station("x", 60.0),
        lambda: adapter.dock(30.0),
        lambda: adapter.lift(0.5, 30.0),
        lambda: adapter.dump(60.0, 30.0),
        lambda: adapter.lower(30.0),
        lambda: adapter.undock(20.0),
        lambda: adapter.return_to_charge(120.0),
        lambda: adapter.recover_from_failed_docking(30.0),
    ]
    for motion in motions:
        result = motion()
        assert result.status is TaskStatus.EMERGENCY_STOPPED
        assert result.failure_reason is FailureReason.EMERGENCY_STOP


def test_obstacle_estop_respects_timeout_budget(raw_bundle):
    """If the trigger point is not reachable within the step budget, the
    drive must TIMEOUT (hard-budget contract), not report an e-stop from a
    position the robot never reached."""
    raw_bundle = _with_obstacle(raw_bundle, 3.0)
    raw_bundle["safety"]["step_timeouts_s"] = {"navigate_s": 1.0}  # 1 m of travel
    record = run_scenario_raw(raw_bundle)
    assert record["emergency_stop_triggered"] is False
    assert record["steps"][0]["step"] == "navigate_to_pose"
    assert record["steps"][0]["status"] == "timeout"
    assert record["failure_reason"] == "step_timeout"


def test_estop_distance_is_sweepable(raw_bundle):
    """The e-stop trigger distance sweeps like any other bundle path."""
    raw_bundle = _with_obstacle(raw_bundle, 3.0)
    record = run_scenario_raw(
        raw_bundle, overrides={"safety.emergency_stop_distance_m.value": 1.0}
    )
    assert record["emergency_stop_trigger_distance_m"] == 1.0
    assert abs(record["emergency_stop_final_clearance_m"] - 0.75) < 1e-9
