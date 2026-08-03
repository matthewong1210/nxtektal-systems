"""The SafetyShield: hard validation of every directive before execution.

The shield is *non-bypassable by construction*: it is owned by
:class:`~nxt_range_ops.core.sim.RangeSimulation`, and
``RangeSimulation.apply_directive`` re-validates internally on every call —
the Gymnasium env, the baselines, and any custom caller all pass through the
same check. Safety rules are hard constraints (rejected directives never
reach the fleet), not reward penalties; the reward's
``unsafe_action_rejection`` component merely *reports* rejections.

Enforced invariants:

* minimum battery reserve before any new collection assignment;
* no commands into closed zones, and per-zone robot occupancy caps;
* handoff-station capacity (open station with a free queue slot required);
* inoperative robots (failed / emergency-stopped / awaiting human) accept
  nothing but a human-assistance request;
* the emergency stop is latched: no policy action can clear it — only the
  simulated human intervention path;
* impossible requests (unknown ids, no payload to hand off, resume of a
  robot that is not paused, ...) are rejected as invalid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
from nxt_range_ops.core.entities import RobotActivity

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime coupling
    from nxt_range_ops.core.sim import RangeSimulation


@dataclass(frozen=True)
class ShieldDecision:
    allowed: bool
    directive: Directive
    reason: str = ""

    @staticmethod
    def ok(directive: Directive) -> "ShieldDecision":
        return ShieldDecision(allowed=True, directive=directive)

    @staticmethod
    def reject(directive: Directive, reason: str) -> "ShieldDecision":
        return ShieldDecision(allowed=False, directive=directive, reason=reason)


class SafetyShield:
    """Stateless validator over the simulation's current true state."""

    def __init__(self, sim: "RangeSimulation"):
        self._sim = sim

    def check(self, directive: Directive) -> ShieldDecision:
        sim = self._sim
        if isinstance(directive, Wait):
            return ShieldDecision.ok(directive)

        robot = sim.robot_or_none(directive.robot_id)
        if robot is None:
            return ShieldDecision.reject(directive, "unknown robot id")

        if isinstance(directive, RequestHumanAssistance):
            if robot.awaiting_human:
                return ShieldDecision.reject(
                    directive, "robot already has a pending human-assistance request"
                )
            return ShieldDecision.ok(directive)

        # Latched e-stop / hard failure / pending human: nothing else is legal.
        if robot.estop_latched:
            return ShieldDecision.reject(
                directive, "emergency stop is latched; requires human reset"
            )
        if robot.activity is RobotActivity.FAILED or robot.awaiting_human:
            return ShieldDecision.reject(
                directive, "robot is failed/awaiting human; request assistance instead"
            )

        if isinstance(directive, ResumeRobot):
            if robot.activity is not RobotActivity.PAUSED:
                return ShieldDecision.reject(directive, "robot is not paused")
            return ShieldDecision.ok(directive)

        if robot.activity is RobotActivity.PAUSED:
            return ShieldDecision.reject(
                directive, "robot is paused; resume it first"
            )

        if isinstance(directive, PauseRobot):
            return ShieldDecision.ok(directive)

        if isinstance(directive, (AssignCollection, ReassignRobot)):
            return self._check_collection(directive, robot)

        if isinstance(directive, SendToHandoff):
            return self._check_handoff(directive, robot)

        if isinstance(directive, SendToCharge):
            if robot.activity in (
                RobotActivity.CHARGING,
                RobotActivity.QUEUED_CHARGER,
            ):
                return ShieldDecision.reject(directive, "robot is already charging/queued")
            if robot.battery_frac >= sim.scenario.charger.charge_target_frac:
                return ShieldDecision.reject(directive, "battery already at charge target")
            return ShieldDecision.ok(directive)

        return ShieldDecision.reject(directive, f"unknown directive {type(directive).__name__}")

    def _check_collection(self, directive, robot) -> ShieldDecision:
        sim = self._sim
        zone = sim.zone_or_none(directive.zone_id)
        if zone is None:
            return ShieldDecision.reject(directive, "unknown zone id")
        if not zone.is_open:
            return ShieldDecision.reject(directive, "zone is closed")
        if sim.facility_closed:
            return ShieldDecision.reject(
                directive, "facility is closed; no new collection assignments"
            )
        if robot.battery_frac <= sim.scenario.safety.min_battery_reserve_frac:
            return ShieldDecision.reject(
                directive, "battery below minimum reserve; charge first"
            )
        if robot.payload_balls >= robot.payload_capacity_balls:
            return ShieldDecision.reject(directive, "payload full; hand off first")

        occupancy = sim.zone_commitments(directive.zone_id, exclude_robot=robot.robot_id)
        if occupancy >= sim.scenario.safety.max_robots_per_zone:
            return ShieldDecision.reject(
                directive, "zone at max robot occupancy (safety restriction)"
            )

        if isinstance(directive, AssignCollection):
            if robot.activity not in (
                RobotActivity.IDLE,
                RobotActivity.CHARGING,
                RobotActivity.QUEUED_CHARGER,
            ):
                return ShieldDecision.reject(
                    directive,
                    "assign_collection requires an idle or charging robot; "
                    "use reassign_robot for active robots",
                )
        else:  # ReassignRobot
            if robot.activity not in (RobotActivity.TRAVELING, RobotActivity.COLLECTING):
                return ShieldDecision.reject(
                    directive, "reassign_robot requires an actively collecting robot"
                )
            if robot.assigned_zone == directive.zone_id:
                return ShieldDecision.reject(
                    directive, "robot is already assigned to that zone"
                )
        return ShieldDecision.ok(directive)

    def _check_handoff(self, directive: SendToHandoff, robot) -> ShieldDecision:
        sim = self._sim
        if robot.payload_balls <= 0:
            return ShieldDecision.reject(directive, "no payload to hand off")
        if robot.activity not in (
            RobotActivity.IDLE,
            RobotActivity.TRAVELING,
            RobotActivity.COLLECTING,
            RobotActivity.CHARGING,
            RobotActivity.QUEUED_CHARGER,
        ):
            return ShieldDecision.reject(
                directive, f"cannot hand off from activity {robot.activity.value}"
            )
        if directive.station_id is not None:
            station = sim.station_or_none(directive.station_id)
            if station is None:
                return ShieldDecision.reject(directive, "unknown station id")
            if not sim.station_accepts(directive.station_id):
                return ShieldDecision.reject(
                    directive, "station closed or its queue is at capacity"
                )
            return ShieldDecision.ok(directive)
        if sim.best_open_station() is None:
            return ShieldDecision.reject(
                directive, "no open handoff station with queue capacity"
            )
        return ShieldDecision.ok(directive)
