"""Skill outcome models: how long tasks take and how they can go wrong.

The operational simulator never simulates robot dynamics. Every physical
skill execution — travel, a collection cycle, a full dock-and-unload handoff,
a charge connection — is abstracted as a stochastic *outcome*: success,
duration, energy, whether a human had to step in, the failure reason, and the
robot's resulting health.

This is the integration seam with Phase 0 and beyond:

* :class:`MockSkillOutcomeModel` (this phase) draws outcomes from configured
  placeholder distributions.
* :class:`IsaacSkillOutcomeModel` (documented stub) will derive outcome
  distributions from physics-backed Phase 1 runs of the ``nxt_sim`` handoff
  state machine via the Isaac adapter — by importing *that* package's public
  entry points at that time, never the other way around.
* :class:`EmpiricalSkillOutcomeModel` (documented stub) will fit outcome
  distributions from logged real-facility operations data.

Failure vocabulary reuses Phase 0's ``FailureReason`` where a matching
concept exists (e.g. ``DOCK_FAILED``), keeping later log joins trivial.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import numpy as np

from nxt_sim.interfaces.types import FailureReason

from nxt_range_ops.config.models import SkillModelConfig
from nxt_range_ops.core.entities import RobotHealth


class SkillType(str, Enum):
    TRAVEL = "travel"
    COLLECT_CYCLE = "collect_cycle"
    DOCK = "dock"
    UNLOAD = "unload"
    CHARGE_CONNECT = "charge_connect"


@dataclass(frozen=True)
class SkillRequest:
    """Everything an outcome model may condition on for one skill execution."""

    skill: SkillType
    robot_id: str
    robot_health: RobotHealth
    battery_frac: float
    payload_balls: int
    distance_m: float = 0.0
    n_balls: int = 0
    station_id: Optional[str] = None
    zone_id: Optional[str] = None
    speed_multiplier: float = 1.0  # scenario modifiers, e.g. wet ground
    failure_prob_multiplier: float = 1.0  # scenario modifiers, e.g. bad dock guide


@dataclass(frozen=True)
class SkillOutcome:
    """The sampled result of executing one skill."""

    success: bool
    duration_s: float
    energy_wh: float
    human_intervention_required: bool = False
    failure_reason: FailureReason = FailureReason.NONE
    resulting_health: RobotHealth = RobotHealth.OK
    data: dict[str, Any] = field(default_factory=dict)


class SkillOutcomeModel(ABC):
    """Interface between the operational simulator and skill-level reality."""

    @abstractmethod
    def sample(self, request: SkillRequest, rng: np.random.Generator) -> SkillOutcome:
        """Sample the outcome of one skill execution.

        Implementations must be pure functions of (request, rng draws) so a
        seeded episode replays deterministically.
        """


class MockSkillOutcomeModel(SkillOutcomeModel):
    """Placeholder-distribution outcome model (Phase 0.5 default).

    All parameters are placeholder-provenance values from
    :class:`SkillModelConfig`. Outcomes exercise the decision problem, not
    real robot performance.
    """

    def __init__(self, config: SkillModelConfig, robot_speed_mps: dict[str, float]):
        self._cfg = config
        self._speed = dict(robot_speed_mps)

    def _jitter(self, base_s: float, rng: np.random.Generator) -> float:
        sd = self._cfg.duration_jitter_rel_sd
        if sd <= 0.0 or base_s <= 0.0:
            return max(base_s, 0.0)
        return float(base_s * max(0.1, rng.normal(1.0, sd)))

    def _speed_for(self, request: SkillRequest) -> float:
        # Ground-condition modifiers (e.g. wet turf) arrive via the request —
        # the simulator owns scenario conditions, the model owns robot physics.
        speed = self._speed[request.robot_id]
        speed *= request.speed_multiplier
        if request.robot_health is RobotHealth.DEGRADED:
            speed *= self._cfg.degraded_speed_multiplier
        return max(speed, 0.05)

    def sample(self, request: SkillRequest, rng: np.random.Generator) -> SkillOutcome:
        cfg = self._cfg
        health = (
            RobotHealth.DEGRADED
            if request.robot_health is RobotHealth.DEGRADED
            else RobotHealth.OK
        )
        if request.skill is SkillType.TRAVEL:
            duration = self._jitter(request.distance_m / self._speed_for(request), rng)
            energy = cfg.travel_power_w.value * duration / 3600.0
            failed = rng.random() < cfg.travel_failure_prob * request.failure_prob_multiplier
            if failed:
                frac = float(rng.uniform(0.1, 0.9))
                return SkillOutcome(
                    success=False,
                    duration_s=duration * frac,
                    energy_wh=energy * frac,
                    failure_reason=FailureReason.NAVIGATION_FAILED,
                    resulting_health=RobotHealth.FAILED,
                    human_intervention_required=True,
                    data={"completed_fraction": frac},
                )
            return SkillOutcome(
                success=True, duration_s=duration, energy_wh=energy, resulting_health=health
            )

        if request.skill is SkillType.COLLECT_CYCLE:
            duration = self._jitter(cfg.collect_cycle_s.value, rng)
            if request.robot_health is RobotHealth.DEGRADED:
                duration /= cfg.degraded_speed_multiplier
            energy = cfg.collect_power_w.value * duration / 3600.0
            failed = rng.random() < cfg.collect_failure_prob * request.failure_prob_multiplier
            if failed:
                return SkillOutcome(
                    success=False,
                    duration_s=duration,
                    energy_wh=energy,
                    failure_reason=FailureReason.RECOVERY_FAILED,
                    resulting_health=RobotHealth.FAILED,
                    human_intervention_required=True,
                )
            return SkillOutcome(
                success=True,
                duration_s=duration,
                energy_wh=energy,
                resulting_health=health,
                data={"balls_collected": request.n_balls},
            )

        if request.skill is SkillType.DOCK:
            duration = self._jitter(cfg.dock_attempt_s.value, rng)
            energy = cfg.dock_power_w.value * duration / 3600.0
            failed = rng.random() < cfg.dock_failure_prob * request.failure_prob_multiplier
            if failed:
                return SkillOutcome(
                    success=False,
                    duration_s=duration,
                    energy_wh=energy,
                    failure_reason=FailureReason.DOCK_FAILED,
                    resulting_health=health,
                )
            return SkillOutcome(
                success=True, duration_s=duration, energy_wh=energy, resulting_health=health
            )

        if request.skill is SkillType.UNLOAD:
            rate = max(cfg.unload_balls_per_s.value, 0.1)
            duration = self._jitter(request.n_balls / rate, rng)
            energy = cfg.unload_power_w.value * duration / 3600.0
            return SkillOutcome(
                success=True, duration_s=duration, energy_wh=energy, resulting_health=health
            )

        if request.skill is SkillType.CHARGE_CONNECT:
            duration = self._jitter(cfg.dock_attempt_s.value * 0.5, rng)
            return SkillOutcome(
                success=True, duration_s=duration, energy_wh=0.0, resulting_health=health
            )

        raise ValueError(f"unknown skill type: {request.skill!r}")


class IsaacSkillOutcomeModel(SkillOutcomeModel):
    """FUTURE (Phase 1) — physics-backed outcome model. Not implemented.

    Planned design: run batches of the Phase 0 handoff cycle through the
    ``nxt_sim`` Isaac Sim adapter offline, fit conditional distributions of
    (success, duration, energy, failure reason) per skill and condition
    (health, payload, approach geometry), and serve samples from those fitted
    tables here. This keeps the operational simulator fast (no live physics in
    the loop) while grounding outcome statistics in physics. Integration
    direction: this class will *consume* ``nxt_sim`` public entry points;
    ``nxt_sim`` never imports this package.
    """

    def __init__(self) -> None:
        raise NotImplementedError(
            "IsaacSkillOutcomeModel is a documented Phase 1 stub; "
            "use MockSkillOutcomeModel for Phase 0.5"
        )

    def sample(self, request: SkillRequest, rng: np.random.Generator) -> SkillOutcome:
        raise NotImplementedError


class EmpiricalSkillOutcomeModel(SkillOutcomeModel):
    """FUTURE (Phase 2) — outcome model fitted from real-facility logs.

    Planned design: ingest the decision/event logs recorded by this package's
    Parquet pipeline from pilot deployments, fit per-skill outcome
    distributions (e.g. kernel or parametric survival models for durations,
    logistic models for failure probabilities conditioned on health, weather,
    and payload), and sample from them here. Provenance moves from
    ``placeholder`` to ``measured`` at that point.
    """

    def __init__(self) -> None:
        raise NotImplementedError(
            "EmpiricalSkillOutcomeModel is a documented Phase 2 stub; "
            "use MockSkillOutcomeModel for Phase 0.5"
        )

    def sample(self, request: SkillRequest, rng: np.random.Generator) -> SkillOutcome:
        raise NotImplementedError
