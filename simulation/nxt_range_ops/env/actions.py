"""Flattened discrete action catalog for the Range Operations Agent.

The agent's whole vocabulary is fleet-level: wait, assign/reassign
collection, send to handoff, send to charge, pause/resume, request human
assistance. There is deliberately no action that expresses wheel speeds,
steering, actuator commands, or an emergency stop — those cannot be encoded,
so no policy can emit them.

The catalog ordering is deterministic (sorted robot/zone ids, enum order),
so an action index means the same thing across runs of the same scenario.
"""

from __future__ import annotations

from dataclasses import dataclass

from nxt_range_ops.config.models import RangeOpsScenario
from nxt_range_ops.core.directives import (
    AssignCollection,
    Directive,
    PauseRobot,
    ReassignRobot,
    RequestHumanAssistance,
    ResumeRobot,
    SendToCharge,
    SendToHandoff,
    Wait,
)
from nxt_range_ops.core.entities import HumanAssistReason


@dataclass(frozen=True)
class ActionSpec:
    index: int
    name: str
    directive: Directive


class ActionCatalog:
    def __init__(self, scenario: RangeOpsScenario):
        robots = sorted(scenario.robot_ids)
        zones = sorted(scenario.zone_ids)
        specs: list[ActionSpec] = []

        def add(name: str, directive: Directive) -> None:
            specs.append(ActionSpec(index=len(specs), name=name, directive=directive))

        add("wait", Wait())
        for r in robots:
            for z in zones:
                add(f"assign_collection({r},{z})", AssignCollection(robot_id=r, zone_id=z))
        for r in robots:
            add(f"send_to_handoff({r})", SendToHandoff(robot_id=r))
        for r in robots:
            add(f"send_to_charge({r})", SendToCharge(robot_id=r))
        for r in robots:
            for z in zones:
                add(f"reassign_robot({r},{z})", ReassignRobot(robot_id=r, zone_id=z))
        for r in robots:
            add(f"pause_robot({r})", PauseRobot(robot_id=r))
        for r in robots:
            add(f"resume_robot({r})", ResumeRobot(robot_id=r))
        for r in robots:
            for reason in HumanAssistReason:
                add(
                    f"request_human_assistance({r},{reason.value})",
                    RequestHumanAssistance(robot_id=r, reason=reason),
                )
        self._specs = specs
        self._by_name = {s.name: s for s in specs}

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def specs(self) -> list[ActionSpec]:
        return list(self._specs)

    def decode(self, index: int) -> Directive:
        if not 0 <= int(index) < len(self._specs):
            raise ValueError(f"action index {index} out of range 0..{len(self._specs) - 1}")
        return self._specs[int(index)].directive

    def name_of(self, index: int) -> str:
        return self._specs[int(index)].name

    def index_of(self, name: str) -> int:
        return self._by_name[name].index
