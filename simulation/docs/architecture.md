# Architecture — Virtual Handoff Lab v0.1

> This document covers the robot handoff execution seam only. For the
> repository-wide separation between AI operations, the digital twin, and robot
> execution, start with [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

## Layering

```
scripts/  (CLI)                sweeps, single runs, validation, env inspection
   |
scenarios/                     runner: load -> validate -> adapt -> control -> record
   |                 \
controllers/          metrics/ + reporting/
   |  (task logic)         (instrumentation)
   v
interfaces/  <-------------------------------+
   RobotTaskInterface, TaskResult, Pose2D    |  implemented by
                                             |
adapters/   mock/        (Phase 0 - no simulator)
            isaac_sim/   (Phase 1 - physics, contact forces)
            ros2/        (Phase 2 - physical robot)
```

Hard rule (enforced by `tests/test_architecture.py`): **controllers, interfaces
and metrics never import adapters.** Simulation-specific behavior — including
the mock's noise/probability knobs (`scenario.mock`) — lives only in the
adapter layer. Task logic reads task-level config (poses, targets, timeouts,
retry budgets) and nothing else.

## The handoff state machine

```
IDLE -> NAVIGATING -> APPROACHING -> DOCKING -> VERIFYING_DOCK
                         ^                          |
                         |     failed attempt       v
                      RECOVERING <---- (retry budget left?)
                                            | no
                                            v
                                          FAILED
     ... VERIFYING_DOCK -> LIFTING -> DUMPING -> VERIFYING_UNLOAD
                                        ^             | shortfall (dump retry)
                                        +-------------+
         -> LOWERING -> UNDOCKING -> RETURNING -> COMPLETE
any state --(obstacle / external request)--> EMERGENCY_STOPPED  (latched)
post-dock failures --> safe retract (lower + undock) --> FAILED
```

Behavior contracts:

* **Timeouts**: every step has a budget (`safety.step_timeouts_s`). Adapters
  must self-report TIMEOUT; the controller additionally reclassifies any
  "success" that overran its budget (defensive contract enforcement).
* **Retries**: failed approach/dock/verify -> `recover_from_failed_docking`
  -> fresh approach, up to `max_docking_retries`. With retries disabled the
  concrete failure reason is kept; with retries the terminal classification is
  RECOVERY_EXHAUSTED + underlying reason.
* **Safe retract**: post-docking failures always attempt lower + undock so the
  robot never leaves the station with a raised basket; retract results are
  recorded but never mask the primary failure.
* **E-stop**: latching. Mid-step (adapter observes an obstacle inside the
  trigger distance) or between steps (external request). No automatic
  recovery in Phase 0.

## Determinism

The mock adapter is a discrete-event model: durations are computed from
placeholder speeds, and all randomness flows through one `random.Random(seed)`.
Same config + same seed -> byte-identical run records (tested). Sweeps derive
per-run seeds from `base_seed`, so whole sweeps are reproducible.

## Metrics

`MetricsCollector` merges the controller's step/transition stream with adapter
telemetry into one flat record per run: docking success/attempts/time,
recovery counts and rate, unloading time, collisions, min clearance, contact
force (None + `contact_force_available: false` until a physics backend exists),
tipping-risk indicator (heuristic), e-stop trigger/stopping/clearance
distances, residual alignment errors, failure classification, full traces.
Sweep summaries add success rate, max tolerated lateral/yaw error (contiguous-
from-zero definition), and a failure-reason histogram.

## Where Phase 1/2 plug in

Only two things change per backend: an adapter class implementing
`RobotTaskInterface` (+ optional `get_telemetry()`), and
`scenario.adapter: mock | isaac_sim | ros2`. Configs, controller, metrics,
sweeps, reports, and tests are reused as-is. Integration plans:
[isaac_sim_integration.md](isaac_sim_integration.md),
[ros2_integration.md](ros2_integration.md).

## Package map — sibling packages outside this layering

`nxt_range_twin`: digital twin / spatial intelligence layer for managed outdoor facilities;
projection-only consumer of `facility-state-stream/v1` + `layout.json`; stdlib+pxr;
guard-tested boundaries. See [spatial_twin_design.md](spatial_twin_design.md).
