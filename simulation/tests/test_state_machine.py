"""State machine behavior against a scripted spy robot (no mock world model)."""

from __future__ import annotations

from nxt_sim.controllers.handoff_state_machine import HandoffController, HandoffState
from nxt_sim.interfaces.types import FailureReason, TaskResult, TaskStatus

from conftest import SpyRobot, ok

NOMINAL_CALLS = [
    "navigate_to_pose",
    "approach_handoff_station",
    "dock",
    "verify_docking",
    "lift",
    "dump",
    "verify_unloading",
    "lower",
    "undock",
    "return_to_charge",
]

NOMINAL_STATES = [
    HandoffState.NAVIGATING,
    HandoffState.APPROACHING,
    HandoffState.DOCKING,
    HandoffState.VERIFYING_DOCK,
    HandoffState.LIFTING,
    HandoffState.DUMPING,
    HandoffState.VERIFYING_UNLOAD,
    HandoffState.LOWERING,
    HandoffState.UNDOCKING,
    HandoffState.RETURNING,
    HandoffState.COMPLETE,
]


def test_nominal_cycle_calls_every_interface_method_in_order(bundle):
    robot = SpyRobot()
    outcome = HandoffController(robot, bundle).run_cycle()
    assert outcome.success is True
    assert outcome.docked is True
    assert outcome.failure_reason is FailureReason.NONE
    assert robot.calls == NOMINAL_CALLS


def test_nominal_cycle_transition_sequence(bundle):
    controller = HandoffController(SpyRobot(), bundle)
    outcome = controller.run_cycle()
    assert [t.to_state for t in outcome.transitions] == NOMINAL_STATES
    assert outcome.final_state is HandoffState.COMPLETE
    assert outcome.clock_s > 0


def test_navigation_failure_classified(bundle):
    robot = SpyRobot(
        script={"navigate_to_pose": [TaskResult(TaskStatus.FAILURE)]}
    )
    outcome = HandoffController(robot, bundle).run_cycle()
    assert outcome.success is False
    assert outcome.final_state is HandoffState.FAILED
    assert outcome.failure_reason is FailureReason.NAVIGATION_FAILED  # controller default
    assert robot.calls == ["navigate_to_pose"]


def test_success_past_budget_is_reclassified_as_timeout(raw_bundle):
    """An adapter claiming success after the budget violates the contract."""
    from nxt_sim.config.loader import build_bundle

    raw_bundle["safety"]["max_docking_retries"] = 0
    bundle = build_bundle(raw_bundle)
    dock_budget = bundle.safety.step_timeouts_s.dock_s
    robot = SpyRobot(script={"dock": [ok(duration=dock_budget + 5.0)]})
    outcome = HandoffController(robot, bundle).run_cycle()
    assert outcome.success is False
    assert outcome.failure_reason is FailureReason.STEP_TIMEOUT


def test_timeout_result_keeps_step_timeout_reason(raw_bundle):
    from nxt_sim.config.loader import build_bundle

    raw_bundle["safety"]["max_docking_retries"] = 0
    bundle = build_bundle(raw_bundle)
    robot = SpyRobot(
        script={"dock": [TaskResult(TaskStatus.TIMEOUT, duration_s=30.0)]}
    )
    outcome = HandoffController(robot, bundle).run_cycle()
    assert outcome.failure_reason is FailureReason.STEP_TIMEOUT


def test_lift_failure_triggers_safe_retract(bundle):
    robot = SpyRobot(
        script={"lift": [TaskResult(TaskStatus.FAILURE, FailureReason.LIFT_FAILED)]}
    )
    outcome = HandoffController(robot, bundle).run_cycle()
    assert outcome.success is False
    assert outcome.failure_reason is FailureReason.LIFT_FAILED
    # Safe retract: lower + undock still commanded after the failure.
    lift_index = robot.calls.index("lift")
    assert robot.calls[lift_index + 1 :] == ["lower", "undock"]
    retract_steps = [name for name, _ in outcome.step_results if name.startswith("retract_")]
    assert retract_steps == ["retract_lower", "retract_undock"]


def test_retract_failure_does_not_mask_primary_reason(bundle):
    robot = SpyRobot(
        script={
            "lift": [TaskResult(TaskStatus.FAILURE, FailureReason.LIFT_FAILED)],
            "lower": [TaskResult(TaskStatus.FAILURE, FailureReason.LOWER_FAILED)],
        }
    )
    outcome = HandoffController(robot, bundle).run_cycle()
    assert outcome.failure_reason is FailureReason.LIFT_FAILED


def test_external_emergency_stop_between_steps(bundle):
    controller_ref: dict = {}

    def on_call(step: str) -> None:
        if step == "dock":
            controller_ref["c"].request_emergency_stop("test")

    robot = SpyRobot(on_call=on_call)
    controller = HandoffController(robot, bundle)
    controller_ref["c"] = controller
    outcome = controller.run_cycle()

    assert outcome.success is False
    assert outcome.final_state is HandoffState.EMERGENCY_STOPPED
    assert outcome.failure_reason is FailureReason.EMERGENCY_STOP
    assert "emergency_stop" in robot.calls
    assert "verify_docking" not in robot.calls  # nothing after the latch
    assert robot.calls.index("emergency_stop") > robot.calls.index("dock")


def test_pending_estop_honored_when_cycle_completes(bundle):
    """An e-stop requested during the LAST step must not be dropped just
    because no further step would check the flag (safety contract)."""
    controller_ref: dict = {}

    def on_call(step: str) -> None:
        if step == "return_to_charge":
            controller_ref["c"].request_emergency_stop("late request")

    robot = SpyRobot(on_call=on_call)
    controller = HandoffController(robot, bundle)
    controller_ref["c"] = controller
    outcome = controller.run_cycle()

    assert outcome.success is False
    assert outcome.final_state is HandoffState.EMERGENCY_STOPPED
    assert outcome.failure_reason is FailureReason.EMERGENCY_STOP
    assert robot.calls[-1] == "emergency_stop"
    assert robot.estop_latched is True


def test_pending_estop_honored_when_last_step_fails(bundle):
    controller_ref: dict = {}

    def on_call(step: str) -> None:
        if step == "lower":
            controller_ref["c"].request_emergency_stop("during lower")

    robot = SpyRobot(
        script={"lower": [TaskResult(TaskStatus.FAILURE, FailureReason.LOWER_FAILED)]},
        on_call=on_call,
    )
    controller = HandoffController(robot, bundle)
    controller_ref["c"] = controller
    outcome = controller.run_cycle()

    assert outcome.final_state is HandoffState.EMERGENCY_STOPPED
    assert outcome.failure_reason is FailureReason.EMERGENCY_STOP
    assert outcome.underlying_failure_reason is FailureReason.LOWER_FAILED
    assert robot.estop_latched is True


def test_estop_during_retract_aborts_retract(bundle):
    """An e-stop reported during retract_lower must stop the retract: no
    undock may be commanded after an e-stop."""
    robot = SpyRobot(
        script={
            "lift": [TaskResult(TaskStatus.FAILURE, FailureReason.LIFT_FAILED)],
            "lower": [TaskResult(TaskStatus.EMERGENCY_STOPPED, FailureReason.EMERGENCY_STOP)],
        }
    )
    outcome = HandoffController(robot, bundle).run_cycle()
    assert outcome.final_state is HandoffState.EMERGENCY_STOPPED
    assert outcome.failure_reason is FailureReason.EMERGENCY_STOP
    assert outcome.underlying_failure_reason is FailureReason.LIFT_FAILED
    assert "undock" not in robot.calls


def test_retract_failure_reports_no_underlying_reason(bundle):
    """A concrete failure with safe retract is not an aggregate
    classification: underlying_failure_reason must stay None."""
    robot = SpyRobot(
        script={"lift": [TaskResult(TaskStatus.FAILURE, FailureReason.LIFT_FAILED)]}
    )
    outcome = HandoffController(robot, bundle).run_cycle()
    assert outcome.failure_reason is FailureReason.LIFT_FAILED
    assert outcome.underlying_failure_reason is None


def test_mid_step_emergency_stop_halts_cycle(bundle):
    robot = SpyRobot(
        script={
            "approach_handoff_station": [
                TaskResult(TaskStatus.EMERGENCY_STOPPED, FailureReason.EMERGENCY_STOP)
            ]
        }
    )
    outcome = HandoffController(robot, bundle).run_cycle()
    assert outcome.final_state is HandoffState.EMERGENCY_STOPPED
    # emergency_stop is latched at the robot, and no motion follows.
    assert robot.calls[-1] == "emergency_stop"
    assert "dock" not in robot.calls
    assert "lower" not in robot.calls  # no retract after e-stop
