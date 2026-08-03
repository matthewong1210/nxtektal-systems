# Range Operations Training Environment (Phase 0.5)

`nxt_range_ops` is a fast, simulator-independent **operational digital twin**
of a golf driving range: a discrete-event simulation (SimPy) of whole
operating days in which a centralized **Range Operations Agent** is trained
and evaluated. It is not photorealistic, needs no Isaac Sim, runs headless,
and simulates a 16-hour operating day in ~0.2 s (≈300,000× real time).

> **Disclaimer (inherited placeholder policy).** Every physical and economic
> quantity is a `source: placeholder` parameter. Results exercise the
> decision problem and the data pipeline; they must **never** be presented as
> real facility performance. The evaluation report counts the placeholders in
> use (26 in the base scenario) and embeds this disclaimer.

## The ball-flow loop

```
dispenser inventory ── customer demand ──> range zones (Z1..Z6)
      ^                                        │ robot collection
      │                                        v
   washing/processing <── handoff/washer queue <── robot payload
```

Ball movement is an **integer ledger** (`core/ledger.py`) with one location
per ball at all times; `RangeSimulation.advance()` re-verifies conservation
after every control interval and the suite tests it across scenarios.

## What is modeled

1. Facility operating hours (episode = one operating day, 06:00–22:00).
2. Time-varying customer demand (piecewise Poisson rates).
3. Demand-forecast uncertainty (bias + per-bucket noise; spikes unforecast).
4. Multiple collection zones with landing weights.
5. Ball accumulation by zone.
6. Robot location, task, battery, payload, capacity, and health.
7. Travel, collection, handoff, unloading, and charging durations (via the
   `SkillOutcomeModel`).
8. Handoff-station dock slots, waiting-queue caps, and buffer capacity.
9. Washer throughput (batch process pulling from station buffers).
10. Charger slots with queueing.
11. Robot failures (hard + degraded operation) from per-robot MTBF.
12. Temporary zone closures.
13. Human intervention (staffed response/fix processes; the only path that
    clears failures and latched e-stops).
14. Safety restrictions (battery reserve, zone occupancy caps, latched
    emergency stop — a Phase 0 concept: no software reset).
15. Sensor noise and delayed state updates (the agent only ever sees sensed
    state; the exact ledger feeds invariants, logs, and evaluation).

## Environment (`nxt_range_ops.env.RangeOpsEnv`)

Gymnasium-compatible: `reset(seed=...) -> (obs, info)`,
`step(action) -> (obs, reward, terminated, truncated, info)`.

* **Observation space** — `spaces.Dict` of time-of-day, sensed dispenser
  fraction, washer WIP, demand forecast buckets, per-zone sensed ball counts
  and open flags, per-robot battery/payload/activity/health/assignment, and
  station/charger queue state.
* **Action space** — flat `Discrete` catalog: `wait`,
  `assign_collection(robot, zone)`, `send_to_handoff(robot)`,
  `send_to_charge(robot)`, `reassign_robot(robot, zone)`,
  `pause_robot(robot)`, `resume_robot(robot)`,
  `request_human_assistance(robot, reason)`. The vocabulary is closed —
  wheel speeds, steering, actuator commands, and emergency stops **cannot be
  expressed** by any policy.
* **Action masks** — `env.action_masks()` and `info["action_mask"]`, derived
  from the same `SafetyShield` used at execution time.
* **SafetyShield (non-bypassable)** — `RangeSimulation.apply_directive()`
  re-validates internally on every call, so even code that bypasses the env
  cannot bypass the shield. Battery reserve, zone closures/occupancy,
  station capacity, and the latched e-stop are hard constraints (rejected
  actions never execute); the `unsafe_action_rejection` reward component
  only *reports* rejections.
* **Reward decomposition** — `info["reward_components"]` always carries the
  ten components (service availability, stockout duration, balls processed,
  human intervention, energy, empty travel, robot idle, handoff congestion,
  task switching, unsafe-action rejection); the scalar reward is exactly
  their sum, with weights in `RewardWeights`.
* **Termination** — `terminated` at facility close (`day_complete`);
  `truncated` at the `max_steps` cap. `info["termination_reason"]` says
  which.
* **Determinism** — one seed spawns named RNG streams (demand, skills,
  failures, sensors, forecast); fleet iteration is sorted; no wall-clock
  values enter the sim. Seed + action sequence replays to an identical
  event log (tested byte-for-byte).

## SkillOutcomeModel

Every physical skill execution (travel, collect cycle, dock, unload, charge
connect) is a sampled outcome: `success`, `duration_s`, `energy_wh`,
`human_intervention_required`, `failure_reason` (reusing Phase 0's
`FailureReason` vocabulary), and `resulting_health`.

* `MockSkillOutcomeModel` — Phase 0.5 default, placeholder distributions.
* `IsaacSkillOutcomeModel` — documented Phase 1 stub: fit outcome tables
  from physics-backed `nxt_sim` handoff runs (offline), then serve samples.
* `EmpiricalSkillOutcomeModel` — documented Phase 2 stub: fit from logged
  real-facility operations data.

## Integration boundary with Phase 0

`nxt_range_ops` imports **only** `nxt_sim.interfaces.types` (enums) and
`nxt_sim.config.models` (StrictModel / PhysicalParam provenance). It never
imports Phase 0 adapters, controllers, or the mock world model, and no Phase
0 file changed. An architecture test enforces both directions.

## Baselines, scenarios, evaluation

* **Baselines** — `random_valid`, `inventory_threshold`,
  `nearest_available_robot`, `demand_forecast_dispatch`.
* **Scenarios** (`scenarios/generators.py`) — `normal_weekday`,
  `weekend_peak`, `demand_spike`, `robot_failure`,
  `handoff_station_outage`, `charger_congestion`,
  `repeated_docking_failure`, `wet_ground`, `demand_forecast_error`,
  `noisy_inventory_sensor`.
* **Decision logs** — one Parquet row per decision transition (episode id,
  simulator version, policy version, git commit, seed, simulated timestamp,
  observation, valid-action mask, action, reward components, task result,
  next state, termination reason) plus a JSON episode summary; both
  reproducible byte-for-byte from (scenario, policy, seed).
* **Evaluation harness** — `evaluation/harness.py` compares policies over
  fixed + randomized seeds on stockout minutes, service availability, human
  interventions, balls processed, energy, empty travel, robot utilization,
  handoff queue time, safe-recovery rate, and estimated operating cost
  (placeholder economics).

```bash
uv sync --extra range-ops
.venv/bin/python scripts/run_range_ops_eval.py --scenarios normal_weekday \
    --out reports/range_ops --log-episodes
.venv/bin/python -m pytest tests/range_ops -q
```

## Training frameworks (recommended, deliberately not installed)

Phase 0.5 adds **no RL framework dependency** — the environment and
baselines stand alone. When training begins, the recommended starting point
for this observation/action structure (Dict observations, flat Discrete
actions, hard invalid-action masks) is **Maskable PPO from `sb3-contrib`**
(Stable-Baselines3): it consumes `action_masks()` natively, handles Dict
observation spaces via its MultiInputPolicy, and is the lowest-integration-
cost way to respect the SafetyShield during exploration. Scale-up path:
RLlib's PPO with action masking (distributed rollouts over scenario
mixtures). The Parquet decision logs are already shaped for offline RL
(e.g. d3rlpy CQL/IQL) once real-ops data exists.
