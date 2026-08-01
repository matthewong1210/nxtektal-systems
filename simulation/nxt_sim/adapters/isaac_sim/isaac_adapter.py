"""IsaacSimAdapter — Phase 1 placeholder.

Planned mapping of RobotTaskInterface onto Isaac Sim (see
docs/isaac_sim_integration.md for details and prerequisites):

=============================  ===============================================
Interface method               Isaac Sim implementation sketch
=============================  ===============================================
navigate_to_pose               differential controller / Nav2 (Isaac ROS) to a
                               target prim pose in the 10 m x 10 m zone stage
approach_handoff_station       AprilTag detection (synthetic camera) + visual
                               servo toward the station marker prim
dock                           physics drive into the guide mesh; PhysX
                               contact reports feed collision/force metrics
verify_docking                 contact sensor prims on the guide rails
lift / dump / lower            articulation joint position targets on the
                               imported basket mechanism (from supplier URDF)
verify_unloading               ball-count heuristic (RigidBody spheres in the
                               basket volume) — still NOT a validated ball-flow
                               model; physical testing remains required
undock                         reverse drive until contact sensors release
return_to_charge               same navigation stack as navigate_to_pose
emergency_stop                 zero all joint/wheel targets, latch a flag
recover_from_failed_docking    scripted back-off trajectory + re-localization
=============================  ===============================================

The adapter must implement get_telemetry() and populate
peak_contact_force_n / contact_force_available=True from PhysX contact
reports, replacing the mock's None.
"""

from __future__ import annotations

from nxt_sim.adapters.base import AdapterNotAvailableError
from nxt_sim.config.models import ScenarioBundle


class IsaacSimAdapter:
    """Not yet implemented; instantiating raises with actionable guidance."""

    def __init__(self, bundle: ScenarioBundle):
        raise AdapterNotAvailableError(
            "IsaacSimAdapter is a Phase 1 deliverable and is not implemented yet.\n"
            "Requirements (none are present on this machine as of Phase 0):\n"
            "  - NVIDIA RTX GPU + driver (Isaac Sim does not run on Apple Silicon)\n"
            "  - Isaac Sim >= 4.5 on Linux or Windows (pip package 'isaacsim' or\n"
            "    standalone install), or a remote/cloud workstation\n"
            "  - Supplier CAD/URDF for the AgileX chassis and the basket mechanism\n"
            "See simulation/docs/isaac_sim_integration.md for the step-by-step plan."
        )
