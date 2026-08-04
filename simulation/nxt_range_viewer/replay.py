"""Deterministic episode replay with per-step frame capture.

Mirrors ``nxt_range_ops.evaluation.harness.run_episode`` exactly — same env,
same policy factory, same seeding — so a replayed episode is the *same*
episode the benchmark ran, step for step. The only addition is recording:
each control step yields one viewer frame built from the env's public
observation and info dict.

Exported state policy: frames carry only what the env exposes through
``step()``/``reset()`` (plus the documented ``sim.events`` ground-truth log).
Hidden simulator internals (continuous robot coordinates) are exported only
under ``include_debug=True`` and are namespaced under a ``debug`` key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Optional, Union

from nxt_range_ops.config.models import RangeOpsScenario
from nxt_range_ops.core.events import EventKind
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.evaluation.harness import episode_kpis
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.scenarios.generators import make_scenario

# Incident/operations callouts worth annotating in a demo timeline. The full
# event stream (travel, collect cycles, demand ticks, ...) is far larger and
# available via ``event_kinds=None``.
DEMO_EVENT_KINDS: FrozenSet[str] = frozenset(
    kind.value
    for kind in (
        EventKind.STOCKOUT,
        EventKind.DOCK_FAILED,
        EventKind.BATTERY_DEPLETED,
        EventKind.EMERGENCY_STOP,
        EventKind.ROBOT_FAILED,
        EventKind.ROBOT_DEGRADED,
        EventKind.ROBOT_RECOVERED,
        EventKind.HUMAN_REQUESTED,
        EventKind.HUMAN_ARRIVED,
        EventKind.HUMAN_DONE,
        EventKind.STATION_OUTAGE,
        EventKind.STATION_RESTORED,
        EventKind.ZONE_CLOSED,
        EventKind.ZONE_REOPENED,
        EventKind.FACILITY_CLOSED,
    )
)


@dataclass(frozen=True)
class ReplayResult:
    """One replayed episode, ready for export."""

    scenario: str
    policy: str
    policy_version: str
    seed: int
    n_steps: int
    termination_reason: Optional[str]
    reward_total: float
    kpis: dict[str, float]
    initial: dict[str, Any]
    frames: list[dict[str, Any]]
    events: list[dict[str, Any]]


def replay_episode(
    scenario: Union[str, RangeOpsScenario],
    policy: str,
    seed: int,
    include_debug: bool = False,
    event_kinds: Optional[FrozenSet[str]] = DEMO_EVENT_KINDS,
) -> ReplayResult:
    """Replay one (scenario, policy, seed) episode and capture viewer frames.

    ``event_kinds=None`` exports the complete event log instead of the
    demo-callout subset.
    """
    scenario_obj = make_scenario(scenario) if isinstance(scenario, str) else scenario
    env = RangeOpsEnv(scenario_obj)
    agent = make_baseline(policy, scenario_obj, env.catalog, seed=seed)

    obs, info = env.reset(seed=seed)
    agent.reset()
    initial = _state_block(obs, info)

    frames: list[dict[str, Any]] = []
    reward_total = 0.0
    termination_reason: Optional[str] = None
    while True:
        action = agent.act(obs, info)
        next_obs, reward, terminated, truncated, next_info = env.step(action)
        reward_total += float(reward)

        frame = _state_block(next_obs, next_info)
        frame["step"] = int(next_info["step"])
        frame["action"] = {
            "index": int(action),
            "name": next_info["action_name"],
            "shield_allowed": bool(next_info["shield"]["allowed"]),
            "shield_reason": str(next_info["shield"]["reason"]),
        }
        frame["reward"] = {
            "total": float(reward),
            "components": {
                key: float(value)
                for key, value in sorted(next_info["reward_components"].items())
            },
        }
        frame["kpi"] = _kpi_tick(next_info["metrics"])
        if include_debug:
            frame["debug"] = {"robot_positions": _debug_robot_positions(env)}
        frames.append(frame)

        obs, info = next_obs, next_info
        if terminated or truncated:
            termination_reason = next_info["termination_reason"]
            break

    metrics = env.sim.metrics.copy()
    events = env.sim.events.to_dicts()
    if event_kinds is not None:
        events = [record for record in events if record["kind"] in event_kinds]

    return ReplayResult(
        scenario=scenario_obj.name,
        policy=agent.name,
        policy_version=agent.version,
        seed=int(seed),
        n_steps=len(frames),
        termination_reason=termination_reason,
        reward_total=reward_total,
        kpis=episode_kpis(scenario_obj, metrics),
        initial=initial,
        frames=frames,
        events=events,
    )


def _state_block(obs: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    """Viewer-visible facility state at one instant, from public env output."""
    return {
        "t_s": float(info["t_s"]),
        "robots": [dict(robot) for robot in info["robots"]],
        "zones": [dict(zone) for zone in info["zones"]],
        "stations": [dict(station) for station in info["stations"]],
        "inventory": {
            "dispenser_balls": int(info["true_dispenser_count"]),
            "washer_wip_frac": float(obs["washer_wip_frac"][0]),
        },
        "charger_queue": int(obs["charger_queue"][0]),
    }


def _kpi_tick(metrics: dict[str, Any]) -> dict[str, Any]:
    """Running (cumulative) KPI counters for the demo ticker."""
    return {
        "stockout_minutes": float(metrics["stockout_minutes"]),
        "balls_collected": int(metrics["balls_collected"]),
        "balls_processed": int(metrics["balls_processed"]),
        "human_interventions": int(metrics["human_interventions_requested"]),
        "energy_wh": float(metrics["energy_wh"]),
        "demand_balls_total": int(metrics["demand_balls_total"]),
        "demand_balls_served": int(metrics["demand_balls_served"]),
    }


def _debug_robot_positions(env: RangeOpsEnv) -> dict[str, dict[str, float]]:
    """Continuous robot coordinates — simulator internals, debug export only."""
    robots = getattr(env.sim, "_robots", {})
    return {
        robot_id: {
            "x_m": float(robots[robot_id].pos.x_m),
            "y_m": float(robots[robot_id].pos.y_m),
        }
        for robot_id in sorted(robots)
    }
