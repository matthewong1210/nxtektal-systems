"""Decomposed reward: every component is computed and reported separately.

The scalar the env returns is only the weighted sum; ``info`` always carries
the full per-component breakdown so training and evaluation can inspect,
re-weight, or ablate components offline without re-running episodes.
"""

from __future__ import annotations

from nxt_range_ops.config.models import RewardWeights
from nxt_range_ops.core.metrics import OpsMetrics

REWARD_COMPONENT_NAMES = (
    "service_availability",
    "stockout_duration",
    "balls_processed",
    "human_intervention",
    "energy",
    "empty_travel",
    "robot_idle",
    "handoff_congestion",
    "task_switching",
    "unsafe_action_rejection",
)


def compute_reward(
    prev: OpsMetrics, cur: OpsMetrics, weights: RewardWeights
) -> tuple[float, dict[str, float]]:
    """Reward for one control interval, from cumulative-metric deltas."""
    d_open_min = cur.open_minutes_elapsed - prev.open_minutes_elapsed
    d_stockout_min = cur.stockout_minutes - prev.stockout_minutes
    if d_open_min > 0:
        availability_frac = max(0.0, 1.0 - d_stockout_min / d_open_min)
    else:
        availability_frac = 0.0

    components = {
        # Accrued per open minute so the credit scales with the control
        # interval (and is naturally zero once the facility is closed).
        "service_availability": weights.service_availability
        * availability_frac
        * d_open_min,
        "stockout_duration": weights.stockout_per_minute * d_stockout_min,
        "balls_processed": weights.per_ball_processed
        * (cur.balls_processed - prev.balls_processed),
        "human_intervention": weights.per_human_intervention
        * (cur.human_interventions_requested - prev.human_interventions_requested),
        "energy": weights.per_wh_energy * (cur.energy_wh - prev.energy_wh),
        "empty_travel": weights.per_empty_travel_s
        * (cur.empty_travel_s - prev.empty_travel_s),
        "robot_idle": weights.per_idle_robot_s * (cur.idle_robot_s - prev.idle_robot_s),
        "handoff_congestion": weights.per_handoff_queue_s
        * (cur.handoff_queue_s - prev.handoff_queue_s),
        "task_switching": weights.per_task_switch
        * (cur.task_switches - prev.task_switches),
        "unsafe_action_rejection": weights.per_unsafe_action_rejection
        * (cur.unsafe_rejections - prev.unsafe_rejections),
    }
    total = float(sum(components.values()))
    return total, {k: float(v) for k, v in components.items()}
