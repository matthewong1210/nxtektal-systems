"""Simulator-independent interfaces and shared types."""

from nxt_sim.interfaces.robot_task_interface import RobotTaskInterface
from nxt_sim.interfaces.telemetry import TelemetryProvider
from nxt_sim.interfaces.types import FailureReason, Pose2D, TaskResult, TaskStatus

__all__ = [
    "FailureReason",
    "Pose2D",
    "RobotTaskInterface",
    "TaskResult",
    "TaskStatus",
    "TelemetryProvider",
]
