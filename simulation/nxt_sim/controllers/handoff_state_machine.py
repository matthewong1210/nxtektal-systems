"""The handoff cycle state machine.

    IDLE -> NAVIGATING -> APPROACHING -> DOCKING -> VERIFYING_DOCK
         -> LIFTING -> DUMPING -> VERIFYING_UNLOAD -> LOWERING
         -> UNDOCKING -> RETURNING -> COMPLETE

with explicit timeout, retry, recovery, and emergency-stop behavior:

* Any failed docking attempt (approach, dock, or verify_docking) triggers
  RECOVERING -> recover_from_failed_docking -> a fresh approach, up to
  ``safety.max_docking_retries`` retries. Exhaustion fails the cycle with
  RECOVERY_EXHAUSTED (the last concrete cause is kept as the underlying
  reason).
* verify_unloading failure retries the dump up to ``safety.max_dump_retries``
  times, then fails with UNLOAD_INCOMPLETE.
* Failures after docking attempt a best-effort SAFE RETRACT (lower, undock)
  so the robot never abandons the station with the basket raised.
* An emergency stop — reported by the robot mid-step, or requested externally
  via :meth:`HandoffController.request_emergency_stop` — halts the cycle
  immediately: ``emergency_stop()`` is latched and no further motion is
  commanded (a safe retract in progress is aborted too). An external request
  that arrives while the last step of a cycle is running is honored at the
  cycle exit point, so a requested e-stop is never dropped. Phase 0
  deliberately has no automatic e-stop recovery.

This module is pure task logic: it must not import from ``nxt_sim.adapters``
and knows nothing about Isaac Sim, ROS 2, or the mock world model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from nxt_sim.config.models import ScenarioBundle
from nxt_sim.interfaces.robot_task_interface import RobotTaskInterface
from nxt_sim.interfaces.types import FailureReason, Pose2D, TaskResult, TaskStatus
from nxt_sim.metrics.collector import MetricsCollector


class HandoffState(str, Enum):
    IDLE = "idle"
    NAVIGATING = "navigating"
    APPROACHING = "approaching"
    DOCKING = "docking"
    VERIFYING_DOCK = "verifying_dock"
    LIFTING = "lifting"
    DUMPING = "dumping"
    VERIFYING_UNLOAD = "verifying_unload"
    LOWERING = "lowering"
    UNDOCKING = "undocking"
    RETURNING = "returning"
    RECOVERING = "recovering"
    COMPLETE = "complete"
    FAILED = "failed"
    EMERGENCY_STOPPED = "emergency_stopped"


@dataclass(frozen=True)
class TransitionRecord:
    clock_s: float
    from_state: HandoffState
    to_state: HandoffState
    note: str = ""


@dataclass
class CycleOutcome:
    success: bool
    docked: bool
    final_state: HandoffState
    failure_reason: FailureReason
    underlying_failure_reason: Optional[FailureReason] = None
    docking_attempts: int = 0
    recovery_attempts: int = 0
    recovery_successes: int = 0
    dump_attempts: int = 0
    clock_s: float = 0.0
    transitions: list[TransitionRecord] = field(default_factory=list)
    step_results: list[tuple[str, TaskResult]] = field(default_factory=list)


class HandoffController:
    """Runs one full handoff cycle against any RobotTaskInterface."""

    def __init__(
        self,
        robot: RobotTaskInterface,
        bundle: ScenarioBundle,
        metrics: Optional[MetricsCollector] = None,
    ):
        self._robot = robot
        self._bundle = bundle
        self._metrics = metrics
        self.state: HandoffState = HandoffState.IDLE
        self.transitions: list[TransitionRecord] = []
        self.step_results: list[tuple[str, TaskResult]] = []
        self.docking_attempts = 0
        self.recovery_attempts = 0
        self.recovery_successes = 0
        self.dump_attempts = 0
        self._docked = False
        self._clock_s = 0.0
        self._estop_requested = False
        self._estop_reason_note = ""

    # ------------------------------------------------------------------
    # External control
    # ------------------------------------------------------------------

    def request_emergency_stop(self, note: str = "external request") -> None:
        """Latch an external e-stop request.

        Honored before the next step — or, if the cycle is about to end with
        no further steps (including a fully successful cycle), at the cycle
        exit point. A requested e-stop is never dropped: the platform latch
        is always commanded and the outcome always reports EMERGENCY_STOPPED.
        """
        self._estop_requested = True
        self._estop_reason_note = note

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _transition(self, to_state: HandoffState, note: str = "") -> None:
        record = TransitionRecord(self._clock_s, self.state, to_state, note)
        self.transitions.append(record)
        if self._metrics is not None:
            self._metrics.record_transition(
                record.clock_s, record.from_state.value, record.to_state.value, note
            )
        self.state = to_state

    def _call(
        self,
        step: str,
        invoke: Callable[[], TaskResult],
        timeout_s: float,
        default_reason: FailureReason,
    ) -> TaskResult:
        if self._estop_requested:
            self._robot.emergency_stop()
            result = TaskResult(
                status=TaskStatus.EMERGENCY_STOPPED,
                failure_reason=FailureReason.EMERGENCY_STOP,
                detail=f"e-stop requested before step '{step}' ({self._estop_reason_note})",
            )
        else:
            result = invoke()
            if result.status is TaskStatus.SUCCESS and result.duration_s > timeout_s + 1e-9:
                # Defensive: an adapter claiming success past its budget is a
                # contract violation; classify it as a timeout.
                result = TaskResult(
                    status=TaskStatus.TIMEOUT,
                    failure_reason=FailureReason.STEP_TIMEOUT,
                    duration_s=result.duration_s,
                    detail=f"step '{step}' exceeded its {timeout_s} s budget "
                    f"(took {result.duration_s:.2f} s)",
                )
            if result.status is TaskStatus.TIMEOUT and result.failure_reason is FailureReason.NONE:
                result.failure_reason = FailureReason.STEP_TIMEOUT
            if result.status is TaskStatus.FAILURE and result.failure_reason is FailureReason.NONE:
                result.failure_reason = default_reason
            if result.status is TaskStatus.EMERGENCY_STOPPED:
                result.failure_reason = FailureReason.EMERGENCY_STOP
        self._clock_s += result.duration_s
        self.step_results.append((step, result))
        if self._metrics is not None:
            self._metrics.record_task(step, result)
        return result

    def _outcome(
        self,
        success: bool,
        final_state: HandoffState,
        reason: FailureReason,
        underlying: Optional[FailureReason] = None,
    ) -> CycleOutcome:
        # A pending external e-stop must never be dropped, even when the cycle
        # is ending and no further step would check the flag (safety contract:
        # this same controller will drive the physical robot).
        if self._estop_requested and final_state is not HandoffState.EMERGENCY_STOPPED:
            self._robot.emergency_stop()
            self._transition(
                HandoffState.EMERGENCY_STOPPED,
                f"pending external e-stop honored at cycle exit ({self._estop_reason_note})",
            )
            if underlying is None and reason is not FailureReason.NONE:
                underlying = reason
            success = False
            final_state = HandoffState.EMERGENCY_STOPPED
            reason = FailureReason.EMERGENCY_STOP
        if underlying is reason:
            underlying = None
        return CycleOutcome(
            success=success,
            docked=self._docked,
            final_state=final_state,
            failure_reason=reason,
            underlying_failure_reason=underlying,
            docking_attempts=self.docking_attempts,
            recovery_attempts=self.recovery_attempts,
            recovery_successes=self.recovery_successes,
            dump_attempts=self.dump_attempts,
            clock_s=self._clock_s,
            transitions=list(self.transitions),
            step_results=list(self.step_results),
        )

    def _finish_estop(
        self, result: TaskResult, underlying: Optional[FailureReason] = None
    ) -> CycleOutcome:
        # Latch the platform-level e-stop (idempotent if the adapter already did).
        self._robot.emergency_stop()
        self._transition(HandoffState.EMERGENCY_STOPPED, result.detail)
        return self._outcome(
            False, HandoffState.EMERGENCY_STOPPED, FailureReason.EMERGENCY_STOP, underlying
        )

    def _finish_failure(
        self,
        result: TaskResult,
        override_reason: Optional[FailureReason] = None,
        underlying: Optional[FailureReason] = None,
    ) -> CycleOutcome:
        reason = override_reason or result.failure_reason
        if reason is FailureReason.NONE:
            reason = FailureReason.INVALID_STATE
        self._transition(HandoffState.FAILED, f"{reason.value}: {result.detail}")
        return self._outcome(
            False,
            HandoffState.FAILED,
            reason,
            underlying if underlying is not None else (
                result.failure_reason if override_reason is not None else None
            ),
        )

    def _safe_retract(self) -> Optional[TaskResult]:
        """Best-effort lower + undock after a post-docking failure.

        Results are recorded (as retract_* steps) but never override the
        primary failure — EXCEPT an emergency stop, which aborts the retract
        immediately (no further motion may be commanded) and is returned so
        the caller finishes the cycle as EMERGENCY_STOPPED.
        """
        t = self._bundle.safety.step_timeouts_s
        res = self._call(
            "retract_lower", lambda: self._robot.lower(t.lower_s), t.lower_s,
            FailureReason.LOWER_FAILED,
        )
        if res.status is TaskStatus.EMERGENCY_STOPPED:
            return res
        res = self._call(
            "retract_undock", lambda: self._robot.undock(t.undock_s), t.undock_s,
            FailureReason.UNDOCK_FAILED,
        )
        if res.status is TaskStatus.EMERGENCY_STOPPED:
            return res
        return None

    def _fail_with_retract(self, result: TaskResult) -> CycleOutcome:
        estop = self._safe_retract()
        if estop is not None:
            return self._finish_estop(estop, underlying=result.failure_reason)
        return self._finish_failure(result)

    # ------------------------------------------------------------------
    # The cycle
    # ------------------------------------------------------------------

    def run_cycle(self) -> CycleOutcome:
        sc = self._bundle.scenario
        t = self._bundle.safety.step_timeouts_s
        station_id = f"{self._bundle.equipment.manufacturer}:{self._bundle.equipment.model}"
        staging = Pose2D(sc.staging_pose.x_m, sc.staging_pose.y_m, sc.staging_pose.yaw_deg)

        # 1. Coarse navigation to the staging pose.
        self._transition(HandoffState.NAVIGATING, "navigate to handoff staging pose")
        res = self._call(
            "navigate_to_pose",
            lambda: self._robot.navigate_to_pose(staging, t.navigate_s),
            t.navigate_s,
            FailureReason.NAVIGATION_FAILED,
        )
        if res.status is TaskStatus.EMERGENCY_STOPPED:
            return self._finish_estop(res)
        if not res.ok:
            return self._finish_failure(res)

        # 2. Docking loop with recovery + retries.
        max_attempts = 1 + self._bundle.safety.max_docking_retries
        while True:
            self.docking_attempts += 1
            failed: Optional[TaskResult] = None

            self._transition(HandoffState.APPROACHING, f"attempt {self.docking_attempts}")
            res = self._call(
                "approach_handoff_station",
                lambda: self._robot.approach_handoff_station(station_id, t.approach_s),
                t.approach_s,
                FailureReason.APPROACH_FAILED,
            )
            if res.status is TaskStatus.EMERGENCY_STOPPED:
                return self._finish_estop(res)
            if not res.ok:
                failed = res

            if failed is None:
                self._transition(HandoffState.DOCKING)
                res = self._call(
                    "dock", lambda: self._robot.dock(t.dock_s), t.dock_s,
                    FailureReason.DOCK_FAILED,
                )
                if res.status is TaskStatus.EMERGENCY_STOPPED:
                    return self._finish_estop(res)
                if not res.ok:
                    failed = res

            if failed is None:
                self._transition(HandoffState.VERIFYING_DOCK)
                res = self._call(
                    "verify_docking",
                    lambda: self._robot.verify_docking(t.verify_dock_s),
                    t.verify_dock_s,
                    FailureReason.DOCK_VERIFY_FAILED,
                )
                if res.status is TaskStatus.EMERGENCY_STOPPED:
                    return self._finish_estop(res)
                if res.ok:
                    self._docked = True
                    break
                failed = res

            # A docking attempt failed.
            if self.docking_attempts >= max_attempts:
                if max_attempts == 1:
                    # Retries disabled: keep the concrete cause as the primary
                    # classification (sweep histograms stay meaningful).
                    return self._finish_failure(failed)
                return self._finish_failure(
                    failed,
                    override_reason=FailureReason.RECOVERY_EXHAUSTED,
                    underlying=failed.failure_reason,
                )
            self._transition(
                HandoffState.RECOVERING,
                f"docking attempt {self.docking_attempts} failed: {failed.failure_reason.value}",
            )
            self.recovery_attempts += 1
            rres = self._call(
                "recover_from_failed_docking",
                lambda: self._robot.recover_from_failed_docking(t.recovery_s),
                t.recovery_s,
                FailureReason.RECOVERY_FAILED,
            )
            if rres.status is TaskStatus.EMERGENCY_STOPPED:
                return self._finish_estop(rres)
            if not rres.ok:
                return self._finish_failure(
                    rres,
                    override_reason=FailureReason.RECOVERY_FAILED,
                    underlying=failed.failure_reason,
                )
            self.recovery_successes += 1

        # 3. Lift.
        self._transition(HandoffState.LIFTING)
        res = self._call(
            "lift",
            lambda: self._robot.lift(sc.lift_target_height_m.value, t.lift_s),
            t.lift_s,
            FailureReason.LIFT_FAILED,
        )
        if res.status is TaskStatus.EMERGENCY_STOPPED:
            return self._finish_estop(res)
        if not res.ok:
            return self._fail_with_retract(res)

        # 4. Dump, with verify-unloading retries.
        max_dumps = 1 + self._bundle.safety.max_dump_retries
        while True:
            self.dump_attempts += 1
            self._transition(HandoffState.DUMPING, f"dump attempt {self.dump_attempts}")
            res = self._call(
                "dump",
                lambda: self._robot.dump(sc.dump_angle_deg.value, t.dump_s),
                t.dump_s,
                FailureReason.DUMP_FAILED,
            )
            if res.status is TaskStatus.EMERGENCY_STOPPED:
                return self._finish_estop(res)
            if not res.ok:
                return self._fail_with_retract(res)

            self._transition(HandoffState.VERIFYING_UNLOAD)
            res = self._call(
                "verify_unloading",
                lambda: self._robot.verify_unloading(t.verify_unload_s),
                t.verify_unload_s,
                FailureReason.UNLOAD_INCOMPLETE,
            )
            if res.status is TaskStatus.EMERGENCY_STOPPED:
                return self._finish_estop(res)
            if res.ok:
                break
            if self.dump_attempts >= max_dumps:
                return self._fail_with_retract(res)

        # 5. Lower, undock, return.
        self._transition(HandoffState.LOWERING)
        res = self._call(
            "lower", lambda: self._robot.lower(t.lower_s), t.lower_s,
            FailureReason.LOWER_FAILED,
        )
        if res.status is TaskStatus.EMERGENCY_STOPPED:
            return self._finish_estop(res)
        if not res.ok:
            return self._finish_failure(res)

        self._transition(HandoffState.UNDOCKING)
        res = self._call(
            "undock", lambda: self._robot.undock(t.undock_s), t.undock_s,
            FailureReason.UNDOCK_FAILED,
        )
        if res.status is TaskStatus.EMERGENCY_STOPPED:
            return self._finish_estop(res)
        if not res.ok:
            return self._finish_failure(res)

        self._transition(HandoffState.RETURNING)
        res = self._call(
            "return_to_charge",
            lambda: self._robot.return_to_charge(t.return_s),
            t.return_s,
            FailureReason.NAVIGATION_FAILED,
        )
        if res.status is TaskStatus.EMERGENCY_STOPPED:
            return self._finish_estop(res)
        if not res.ok:
            return self._finish_failure(res)

        self._transition(HandoffState.COMPLETE)
        return self._outcome(True, HandoffState.COMPLETE, FailureReason.NONE)
