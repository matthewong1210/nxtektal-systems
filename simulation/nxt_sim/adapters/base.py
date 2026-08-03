"""Adapter registry.

Scenario configs select a backend via ``scenario.adapter``; the rest of the
stack (controller, metrics, sweeps, reports) is backend-agnostic. Swapping the
mock for Isaac Sim or the physical robot is a one-line config change once the
corresponding adapter exists.
"""

from __future__ import annotations

from nxt_sim.config.models import ScenarioBundle
from nxt_sim.interfaces.robot_task_interface import RobotTaskInterface


class AdapterNotAvailableError(RuntimeError):
    """Raised when a configured backend is not implemented/installed."""


def create_adapter(bundle: ScenarioBundle) -> RobotTaskInterface:
    kind = bundle.scenario.adapter
    if kind == "mock":
        from nxt_sim.adapters.mock.mock_adapter import MockSimulationAdapter

        return MockSimulationAdapter(bundle)
    if kind == "isaac_sim":
        from nxt_sim.adapters.isaac_sim.isaac_adapter import IsaacSimAdapter

        return IsaacSimAdapter(bundle)
    if kind == "ros2":
        from nxt_sim.adapters.ros2.ros2_adapter import Ros2Adapter

        return Ros2Adapter(bundle)
    raise AdapterNotAvailableError(f"unknown adapter kind: {kind!r}")
