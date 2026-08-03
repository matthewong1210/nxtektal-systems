"""NXTektal Virtual Handoff Lab v0.1.

Simulator-independent validation environment for autonomous golf-ball
collection robots: docking, lifting, dumping, collision avoidance, and
equipment compatibility testing.

Layering (dependencies point downward only):

    scenarios / scripts        (orchestration, sweeps, reporting)
        |
    controllers                (robot TASK logic - simulator independent)
        |
    interfaces                 (RobotTaskInterface, shared types)
        ^
    adapters                   (mock / isaac_sim / ros2 - SIMULATION or
                                hardware specific; implement interfaces)

Robot task logic (controllers/) must never import from adapters/.
"""

__version__ = "0.1.0"
