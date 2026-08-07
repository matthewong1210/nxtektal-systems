"""Fleet-level directives — the only vocabulary the agent may speak.

Directives are deliberately *operational*: assign, redirect, pause, resume,
charge, request help. There is no wheel-speed, steering, actuator, or
emergency-stop directive, so no learning policy can ever express one. The
emergency stop exists inside the simulator as a latched safety event (Phase 0
concept) that only human intervention clears.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from nxt_range_ops.core.entities import HumanAssistReason


@dataclass(frozen=True)
class Wait:
    """Take no fleet action this decision step."""


@dataclass(frozen=True)
class AssignCollection:
    robot_id: str
    zone_id: str


@dataclass(frozen=True)
class SendToHandoff:
    robot_id: str
    station_id: Optional[str] = None  # None = simulator picks best open station


@dataclass(frozen=True)
class SendToCharge:
    robot_id: str


@dataclass(frozen=True)
class ReassignRobot:
    robot_id: str
    zone_id: str


@dataclass(frozen=True)
class PauseRobot:
    robot_id: str


@dataclass(frozen=True)
class ResumeRobot:
    robot_id: str


@dataclass(frozen=True)
class RequestHumanAssistance:
    robot_id: str
    reason: HumanAssistReason


Directive = Union[
    Wait,
    AssignCollection,
    SendToHandoff,
    SendToCharge,
    ReassignRobot,
    PauseRobot,
    ResumeRobot,
    RequestHumanAssistance,
]


def directive_to_dict(d: Directive) -> dict:
    out: dict = {"type": type(d).__name__}
    for name, value in vars(d).items():
        out[name] = value.value if isinstance(value, HumanAssistReason) else value
    return out
