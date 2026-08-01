"""Ros2Adapter — Phase 2 placeholder for the PHYSICAL robot.

Planned mapping (see docs/ros2_integration.md):

=============================  ===============================================
Interface method               ROS 2 implementation sketch
=============================  ===============================================
navigate_to_pose               Nav2 NavigateToPose action
approach_handoff_station       custom Approach action (AprilTag servoing node)
dock / undock                  custom Dock/Undock actions over the AgileX
                               chassis SDK velocity interface + contact GPIO
verify_docking                 contact-switch topic + marker pose check
lift / dump / lower            actuator driver service/action (mechanism TBD)
verify_unloading               load cell / vision service
return_to_charge               Nav2 NavigateToPose to the charge pose
emergency_stop                 safety-rated e-stop channel (hardware) + twist
                               mux override; software latch mirrors state
recover_from_failed_docking    scripted back-off behavior (behavior tree node)
=============================  ===============================================

The same HandoffController runs unchanged on the robot computer: only this
adapter changes between simulation and hardware.
"""

from __future__ import annotations

from nxt_sim.adapters.base import AdapterNotAvailableError
from nxt_sim.config.models import ScenarioBundle


class Ros2Adapter:
    """Not yet implemented; instantiating raises with actionable guidance."""

    def __init__(self, bundle: ScenarioBundle):
        raise AdapterNotAvailableError(
            "Ros2Adapter is a Phase 2 deliverable and is not implemented yet.\n"
            "Requirements (none are present on this machine as of Phase 0):\n"
            "  - ROS 2 (Humble/Jazzy) on the robot computer or a Linux dev machine\n"
            "  - AgileX chassis ROS 2 driver / SDK documentation\n"
            "  - Actuator drivers for the basket lift/tilt mechanism\n"
            "See simulation/docs/ros2_integration.md for the step-by-step plan."
        )
