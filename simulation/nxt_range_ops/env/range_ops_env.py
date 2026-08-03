"""RangeOpsEnv — the centralized Gymnasium environment for range operations.

One episode is one simulated operating day. Each ``step`` applies at most one
fleet directive (validated by the non-bypassable SafetyShield inside the
simulator) and advances simulated time by one control interval. Runs headless
and far faster than real time.

Observations are built from *sensed* state (noisy, delayed) — the exact
internal ledger is never exposed to the agent, only to invariant checks,
logging, and evaluation.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from nxt_range_ops.config.models import RangeOpsScenario
from nxt_range_ops.core.entities import RobotActivity, RobotHealth
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.core.skills import SkillOutcomeModel
from nxt_range_ops.env.actions import ActionCatalog
from nxt_range_ops.env.rewards import compute_reward

_ACTIVITY_ORDER = list(RobotActivity)
_HEALTH_ORDER = list(RobotHealth)


class RangeOpsEnv(gym.Env):
    """Centralized fleet-operations environment over one operating day."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        scenario: RangeOpsScenario,
        skill_model_factory: Optional[
            Callable[[RangeOpsScenario], SkillOutcomeModel]
        ] = None,
    ):
        super().__init__()
        self.scenario = scenario
        self._skill_model_factory = skill_model_factory
        self.catalog = ActionCatalog(scenario)
        self.sim: Optional[RangeSimulation] = None
        self._episode_seed: Optional[int] = None
        self._steps = 0
        self._metrics_prev = None
        self._last_obs = None
        # Snapshot cache: filled by _build_obs, reused by _build_info so one
        # step never snapshots the fleet twice.
        self._last_zones = []
        self._last_robots = []
        self._last_stations = []

        n_zones = len(scenario.zone_ids)
        n_robots = len(scenario.robot_ids)
        n_stations = len(scenario.station_ids)
        n_forecast = max(
            1,
            scenario.demand.forecast_horizon_minutes
            // scenario.demand.forecast_bucket_minutes,
        )
        inf = np.inf
        self.observation_space = spaces.Dict(
            {
                "minute_of_day": spaces.Box(0.0, 1.0, (1,), dtype=np.float32),
                "minutes_to_close": spaces.Box(0.0, 1.0, (1,), dtype=np.float32),
                "dispenser_inventory_frac": spaces.Box(0.0, 2.0, (1,), dtype=np.float32),
                "washer_wip_frac": spaces.Box(0.0, 1.0, (1,), dtype=np.float32),
                "demand_forecast": spaces.Box(0.0, inf, (n_forecast,), dtype=np.float32),
                "zone_balls": spaces.Box(0.0, inf, (n_zones,), dtype=np.float32),
                "zone_open": spaces.MultiBinary(n_zones),
                "zone_committed_robots": spaces.Box(0.0, inf, (n_zones,), dtype=np.float32),
                "robot_battery": spaces.Box(0.0, 1.0, (n_robots,), dtype=np.float32),
                "robot_payload_frac": spaces.Box(0.0, 1.0, (n_robots,), dtype=np.float32),
                "robot_activity": spaces.MultiDiscrete(
                    [len(_ACTIVITY_ORDER)] * n_robots
                ),
                "robot_health": spaces.MultiDiscrete([len(_HEALTH_ORDER)] * n_robots),
                "robot_zone": spaces.MultiDiscrete([n_zones + 1] * n_robots),
                "station_queue": spaces.Box(0.0, inf, (n_stations,), dtype=np.float32),
                "station_buffer_frac": spaces.Box(0.0, 1.0, (n_stations,), dtype=np.float32),
                "station_open": spaces.MultiBinary(n_stations),
                "charger_queue": spaces.Box(0.0, inf, (1,), dtype=np.float32),
            }
        )
        self.action_space = spaces.Discrete(len(self.catalog))

    # ------------------------------------------------------------------
    # Gymnasium API
    # ------------------------------------------------------------------

    def reset(
        self, *, seed: Optional[int] = None, options: Optional[dict] = None
    ) -> tuple[dict, dict]:
        super().reset(seed=seed)
        if seed is None:
            # Draw a reproducible episode seed from Gymnasium's np_random so
            # even unseeded resets are replayable via info["episode_seed"].
            seed = int(self.np_random.integers(0, 2**31 - 1))
        self._episode_seed = int(seed)
        skill_model = (
            self._skill_model_factory(self.scenario)
            if self._skill_model_factory is not None
            else None
        )
        self.sim = RangeSimulation(self.scenario, self._episode_seed, skill_model)
        self._steps = 0
        self._metrics_prev = self.sim.metrics.copy()
        obs = self._build_obs()
        self._last_obs = obs
        info = self._build_info(
            action_taken=None, decision=None, reward_components=None
        )
        return obs, info

    def step(self, action: int) -> tuple[dict, float, bool, bool, dict]:
        if self.sim is None:
            raise RuntimeError("call reset() before step()")
        directive = self.catalog.decode(action)
        # Non-bypassable: apply_directive re-validates via the SafetyShield.
        decision = self.sim.apply_directive(directive)
        self.sim.advance(self.scenario.episode.control_interval_s)
        self._steps += 1

        total, components = compute_reward(
            self._metrics_prev, self.sim.metrics, self.scenario.rewards
        )
        self._metrics_prev = self.sim.metrics.copy()

        terminated = self.sim.facility_closed
        truncated = (not terminated) and self._steps >= self.scenario.episode.max_steps
        obs = self._build_obs()
        self._last_obs = obs
        info = self._build_info(
            action_taken=int(action),
            decision=decision,
            reward_components=components,
            terminated=terminated,
            truncated=truncated,
        )
        return obs, total, terminated, truncated, info

    def action_masks(self) -> np.ndarray:
        """Boolean validity mask over the flat action catalog.

        Derived from the same SafetyShield used at execution time, so the
        mask and the shield can never disagree.
        """
        if self.sim is None:
            raise RuntimeError("call reset() before action_masks()")
        mask = np.zeros(len(self.catalog), dtype=bool)
        for spec in self.catalog.specs:
            mask[spec.index] = self.sim.shield.check(spec.directive).allowed
        return mask

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_obs(self) -> dict[str, Any]:
        sim = self.sim
        s = self.scenario
        zones = sim.zone_snapshots()
        robots = sim.robot_snapshots()
        stations = sim.station_snapshots()
        self._last_zones = zones
        self._last_robots = robots
        self._last_stations = stations
        zone_index = {z: i for i, z in enumerate(sorted(s.zone_ids))}
        sensed_zones = sim.sensed_zone_counts()
        total = float(s.total_balls)
        day_len_min = (s.hours.close_minute - s.hours.open_minute) or 1

        n_forecast = self.observation_space["demand_forecast"].shape[0]
        forecast = np.asarray(sim.forecast_window(), dtype=np.float32)[:n_forecast]

        return {
            "minute_of_day": np.array(
                [sim.minute_of_day / 1440.0], dtype=np.float32
            ),
            "minutes_to_close": np.array(
                [
                    max(0.0, s.hours.close_minute - sim.minute_of_day) / day_len_min
                ],
                dtype=np.float32,
            ),
            "dispenser_inventory_frac": np.array(
                [min(2.0, sim.sensed_dispenser_count() / total)], dtype=np.float32
            ),
            "washer_wip_frac": np.array(
                [min(1.0, sim.washer_wip() / total)], dtype=np.float32
            ),
            "demand_forecast": forecast,
            "zone_balls": np.array(
                [sensed_zones[z.zone_id] for z in zones], dtype=np.float32
            ),
            "zone_open": np.array([1 if z.is_open else 0 for z in zones], dtype=np.int8),
            "zone_committed_robots": np.array(
                [sim.zone_commitments(z.zone_id) for z in zones], dtype=np.float32
            ),
            "robot_battery": np.array(
                [sim.sensed_battery_frac(r.robot_id) for r in robots], dtype=np.float32
            ),
            "robot_payload_frac": np.array(
                [r.payload_balls / r.payload_capacity_balls for r in robots],
                dtype=np.float32,
            ),
            "robot_activity": np.array(
                [_ACTIVITY_ORDER.index(r.activity) for r in robots], dtype=np.int64
            ),
            "robot_health": np.array(
                [_HEALTH_ORDER.index(r.health) for r in robots], dtype=np.int64
            ),
            "robot_zone": np.array(
                [
                    0 if r.assigned_zone is None else zone_index[r.assigned_zone] + 1
                    for r in robots
                ],
                dtype=np.int64,
            ),
            "station_queue": np.array(
                [st.queue_length for st in stations], dtype=np.float32
            ),
            "station_buffer_frac": np.array(
                [st.buffer_balls / st.buffer_capacity_balls for st in stations],
                dtype=np.float32,
            ),
            "station_open": np.array(
                [1 if st.is_open else 0 for st in stations], dtype=np.int8
            ),
            "charger_queue": np.array(
                [sim.charger_queue_length()], dtype=np.float32
            ),
        }

    def _build_info(
        self,
        action_taken: Optional[int],
        decision,
        reward_components: Optional[dict],
        terminated: bool = False,
        truncated: bool = False,
    ) -> dict[str, Any]:
        sim = self.sim
        termination_reason = None
        if terminated:
            termination_reason = "day_complete"
        elif truncated:
            termination_reason = "max_steps"
        info: dict[str, Any] = {
            "t_s": sim.now,
            "episode_seed": self._episode_seed,
            "step": self._steps,
            "action_mask": self.action_masks(),
            "metrics": sim.metrics.to_dict(),
            "true_dispenser_count": sim.dispenser_count(),
            "robots": [r.to_dict() for r in self._last_robots],
            "zones": [z.to_dict() for z in self._last_zones],
            "stations": [s.to_dict() for s in self._last_stations],
            "termination_reason": termination_reason,
        }
        if action_taken is not None:
            info["action"] = action_taken
            info["action_name"] = self.catalog.name_of(action_taken)
            info["shield"] = {
                "allowed": decision.allowed,
                "reason": decision.reason,
            }
        if reward_components is not None:
            info["reward_components"] = reward_components
            info["reward_total"] = float(sum(reward_components.values()))
        return info
