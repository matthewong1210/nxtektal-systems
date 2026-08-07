"""Entity state vocabulary for the operational simulator.

Mirrors the Phase 0 style — explicit enums, latched emergency stop — without
importing any Phase 0 controller or adapter code. Robot *activities* here are
operational (fleet-level) states; the within-handoff micro-states
(dock/lift/dump/...) stay abstracted behind the SkillOutcomeModel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RobotActivity(str, Enum):
    IDLE = "idle"
    TRAVELING = "traveling"
    COLLECTING = "collecting"
    QUEUED_HANDOFF = "queued_handoff"
    UNLOADING = "unloading"
    QUEUED_CHARGER = "queued_charger"
    CHARGING = "charging"
    PAUSED = "paused"
    FAILED = "failed"
    EMERGENCY_STOPPED = "emergency_stopped"
    AWAITING_HUMAN = "awaiting_human"


# Activities during which a robot consumes a directive and cannot take a new
# assignment without an explicit reassign/interrupt.
ACTIVE_ACTIVITIES = frozenset(
    {
        RobotActivity.TRAVELING,
        RobotActivity.COLLECTING,
        RobotActivity.QUEUED_HANDOFF,
        RobotActivity.UNLOADING,
        RobotActivity.QUEUED_CHARGER,
        RobotActivity.CHARGING,
    }
)

# Activities from which no new work may be commanded at all.
INOPERATIVE_ACTIVITIES = frozenset(
    {
        RobotActivity.FAILED,
        RobotActivity.EMERGENCY_STOPPED,
        RobotActivity.AWAITING_HUMAN,
    }
)


class RobotHealth(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


class HumanAssistReason(str, Enum):
    ROBOT_FAILURE = "robot_failure"
    EMERGENCY_STOP_RESET = "emergency_stop_reset"
    BATTERY_DEPLETED = "battery_depleted"
    DOCKING_ISSUE = "docking_issue"
    OTHER = "other"


@dataclass(frozen=True)
class RobotStateSnapshot:
    """Immutable view of one robot for observations, logs, and policies."""

    robot_id: str
    activity: RobotActivity
    health: RobotHealth
    battery_frac: float
    payload_balls: int
    payload_capacity_balls: int
    location: str  # node id: "dispenser", "zone:<id>", "station:<id>", "charger"
    destination: Optional[str]
    assigned_zone: Optional[str]
    estop_latched: bool
    awaiting_human: bool

    def to_dict(self) -> dict:
        return {
            "robot_id": self.robot_id,
            "activity": self.activity.value,
            "health": self.health.value,
            "battery_frac": round(self.battery_frac, 6),
            "payload_balls": self.payload_balls,
            "payload_capacity_balls": self.payload_capacity_balls,
            "location": self.location,
            "destination": self.destination,
            "assigned_zone": self.assigned_zone,
            "estop_latched": self.estop_latched,
            "awaiting_human": self.awaiting_human,
        }


@dataclass(frozen=True)
class ZoneStateSnapshot:
    zone_id: str
    balls: int
    is_open: bool
    robots_present: int

    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "balls": self.balls,
            "is_open": self.is_open,
            "robots_present": self.robots_present,
        }


@dataclass(frozen=True)
class StationStateSnapshot:
    station_id: str
    is_open: bool
    docked: int
    queue_length: int
    buffer_balls: int
    buffer_capacity_balls: int

    def to_dict(self) -> dict:
        return {
            "station_id": self.station_id,
            "is_open": self.is_open,
            "docked": self.docked,
            "queue_length": self.queue_length,
            "buffer_balls": self.buffer_balls,
            "buffer_capacity_balls": self.buffer_capacity_balls,
        }
