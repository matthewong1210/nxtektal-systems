"""Core simulator-independent types shared by task logic and adapters.

Everything in this module must remain free of simulator, ROS, and vendor
dependencies: it is the vocabulary shared by the simulated robot, the future
physical robot, and the task-level state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    """Terminal status of a single high-level task call."""

    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    EMERGENCY_STOPPED = "emergency_stopped"


class FailureReason(str, Enum):
    """Failure classification used for metrics and reporting.

    Adapters should set the most specific reason they can determine; the
    controller fills in a step-appropriate default when an adapter reports a
    bare FAILURE.
    """

    NONE = "none"
    NAVIGATION_FAILED = "navigation_failed"
    APPROACH_FAILED = "approach_failed"
    DOCK_FAILED = "dock_failed"
    LATERAL_ERROR_EXCEEDED = "lateral_error_exceeded"
    YAW_ERROR_EXCEEDED = "yaw_error_exceeded"
    GEOMETRY_INCOMPATIBLE = "geometry_incompatible"
    DOCK_VERIFY_FAILED = "dock_verify_failed"
    LIFT_FAILED = "lift_failed"
    DUMP_FAILED = "dump_failed"
    UNLOAD_INCOMPLETE = "unload_incomplete"
    LOWER_FAILED = "lower_failed"
    UNDOCK_FAILED = "undock_failed"
    COLLISION = "collision"
    EMERGENCY_STOP = "emergency_stop"
    TIPPING_RISK = "tipping_risk"
    STEP_TIMEOUT = "step_timeout"
    RECOVERY_FAILED = "recovery_failed"
    RECOVERY_EXHAUSTED = "recovery_exhausted"
    INVALID_STATE = "invalid_state"


@dataclass(frozen=True)
class Pose2D:
    """Planar pose in the handoff-zone frame. Yaw in degrees, CCW positive."""

    x_m: float
    y_m: float
    yaw_deg: float


@dataclass
class TaskResult:
    """Outcome of one blocking high-level task call.

    duration_s is simulated time for simulation adapters and wall-clock time
    for hardware adapters. data carries adapter-specific extras (residual
    errors, contact forces when available, ...) and must contain only
    JSON-serializable values.
    """

    status: TaskStatus
    failure_reason: FailureReason = FailureReason.NONE
    duration_s: float = 0.0
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is TaskStatus.SUCCESS
