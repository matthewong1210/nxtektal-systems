"""The high-level robot task interface.

This is the single contract shared by:

* the mock adapter (Phase 0, no simulator required),
* the Isaac Sim adapter (Phase 1, physics-backed), and
* the ROS 2 adapter for the physical robot (Phase 2).

Task logic (controllers/) programs exclusively against this interface, so the
same handoff state machine runs unchanged against any backend.

Contract for implementations
----------------------------
* Calls are blocking and return a TaskResult when the task finishes, fails,
  or exceeds its budget.
* ``timeout_s`` is a hard budget. Implementations must return a result with
  ``TaskStatus.TIMEOUT`` rather than run past it (simulated time for
  simulators, wall-clock for hardware).
* After :meth:`emergency_stop`, every motion-producing call must return
  ``TaskStatus.EMERGENCY_STOPPED`` until the platform is externally reset.
  Phase 0 provides no software reset by design.
* Implementations must not raise for out-of-order calls (for example
  ``lift`` before ``dock``); they return ``FAILURE`` with
  ``FailureReason.INVALID_STATE`` so the task layer can classify and recover.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nxt_sim.interfaces.types import Pose2D, TaskResult


class RobotTaskInterface(ABC):
    """Simulator- and hardware-independent high-level task interface."""

    @abstractmethod
    def navigate_to_pose(self, pose: Pose2D, timeout_s: float) -> TaskResult:
        """Drive to a pose in the handoff-zone frame (coarse navigation)."""

    @abstractmethod
    def approach_handoff_station(self, station_id: str, timeout_s: float) -> TaskResult:
        """Approach the station and perform marker-assisted final alignment.

        Ends at the docking start point with best-effort reduction of
        lateral, longitudinal, and yaw error (e.g. AprilTag servoing).
        """

    @abstractmethod
    def dock(self, timeout_s: float) -> TaskResult:
        """Perform the final docking motion into the handoff station.

        May rely on passive guides (funnels, rails) and contact switches.
        """

    @abstractmethod
    def verify_docking(self, timeout_s: float) -> TaskResult:
        """Confirm a valid docked state (contact switches / short-range sensors)."""

    @abstractmethod
    def lift(self, target_height_m: float, timeout_s: float) -> TaskResult:
        """Raise the basket/platform to the target height above ground."""

    @abstractmethod
    def dump(self, target_angle_deg: float, timeout_s: float) -> TaskResult:
        """Tilt the basket to the target dump angle and hold for the dwell."""

    @abstractmethod
    def verify_unloading(self, timeout_s: float) -> TaskResult:
        """Check that the basket emptied (load cell / vision / configured model).

        NOTE: granular ball flow, bridging, and jamming are NOT validated in
        simulation; see docs/assumptions.md. This call reports whatever the
        backend can observe.
        """

    @abstractmethod
    def lower(self, timeout_s: float) -> TaskResult:
        """Return the basket to the stowed (lowered, level) position."""

    @abstractmethod
    def undock(self, timeout_s: float) -> TaskResult:
        """Reverse out of the docking guide to the undocked state."""

    @abstractmethod
    def return_to_charge(self, timeout_s: float) -> TaskResult:
        """Navigate back to the charge pose."""

    @abstractmethod
    def emergency_stop(self) -> TaskResult:
        """Latch an emergency stop immediately. Must always be safe to call.

        Returns SUCCESS when the stop is latched. Subsequent motion commands
        must fail with EMERGENCY_STOPPED until the platform is reset outside
        this interface.
        """

    @abstractmethod
    def recover_from_failed_docking(self, timeout_s: float) -> TaskResult:
        """Back off to a safe standoff after a failed docking attempt.

        Leaves the robot ready for a fresh approach_handoff_station() call.
        """
