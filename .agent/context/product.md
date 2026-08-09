# Product and repository context

## Product frame

NXTektal's repository-backed domain is autonomous golf-ball collection and
whole-site driving-range operations. The durable product principle is:

> The unit of autonomy is the site, not the robot.

The current simulation stack models the site-level loop from demand and ball
inventory through collection, handoff, washing, charging, staffing, safety, and
evaluation. The micro handoff lab separately validates robot task sequencing
behind a backend-independent interface.

The owner-provided strategy for this repository is that the AI operating layer
is the moat. In repository terms, that means trusted operational state,
advisory decisions, provenance, traceability, evaluation, and human learning
around the site. It does not mean merging the simulator, twin, decision logic,
and robot adapters into one package.

## Repository surfaces

| Surface | Purpose | Coupling status |
|---|---|---|
| Root Jarvis prototype | Static dashboard, voice loop, and landing-page assets | Independent of NXTektal Python and ROI packages |
| `simulation/` | Virtual handoff lab, whole-range runtime, Site OS state/advisory/evidence/projection layers | Python project managed with `uv` |
| `nxtektal-roi-engine/` | Deterministic, formula-locked driving-range ROI calculations | Independent TypeScript package |

Do not infer a dependency merely because these surfaces share a repository.

## Product-layer vocabulary

- **Simulation runtime:** `RangeSimulation` and its ledger, resources, entities,
  events, metrics, forecast, and RNG streams.
- **Physical commissioning:** immutable, provenance-bearing static facility facts
  in `CommissionedSite` where the unmerged commissioning branch is present.
  Commissioning answers what exists, not what is happening now.
- **Canonical downstream state:** immutable `FacilityState` snapshots.
- **Decision support:** deterministic `nxt_facility` recommendations and the
  manager briefing. These are broad FacilityState-derived, advisory outputs.
- **Shadow Ops:** `nxt_pilot_ops` decision trust, trace, evaluation, human
  workflow, and tamper-evident ledger around named policies. It is advisory and
  downstream, not a duplicate general decision engine.
- **Site Runtime:** future physical-site orchestration around commissioning,
  observations, state assembly, and downstream fan-out. It is not implemented
  and would own no competing state, policy, projection, or execution logic.
- **Digital twin:** serialized FacilityState/layout to USD projection. It is a
  view of truth, never the owner of truth.
- **Execution:** simulated directives behind `SafetyShield`, and robot tasks
  behind `RobotTaskInterface`. Physical ROS 2 and Isaac Sim backends are stubs.
- **Operational memory:** append-only historical evidence used for offline,
  explicitly non-causal analysis.

## Honest-scope rules

- Physical values in the current simulator are placeholder-tagged. Outputs
  validate software pipelines, not robot or facility design.
- Whole-site integer ball inventory is simulated. Granular physical flow,
  friction, bridging, and jamming are not.
- The repository has observation contracts and synthetic sensors, but no real
  production telemetry transport/runtime.
- Facility commissioning is implemented only on sibling draft PR #20 at the
  audit date; it is not integrated with the current Shadow checkout or `main`.
- It has no production robot command bridge, validated operating policy, or
  real-site performance evidence.
- No LLM or generative agent has actuator authority. Do not introduce a direct
  model/tool-call path to `RobotTaskInterface`, adapters, ROS, or actuators.
- The spatial twin is intentionally limited by declared layout and state. Never
  invent continuous motion, geometry, sensor position, or physics.
- ROI calculations are deterministic model outputs, not permission to invent
  missing evidence or defaults.

## Stable starting sources

- `simulation/README.md`
- `simulation/docs/range_ops.md`
- `simulation/docs/facility_state.md`
- `simulation/docs/spatial_twin_design.md`
- `simulation/docs/shadow_ops_v0.md` on branches that contain `nxt_pilot_ops`
- `simulation/docs/commissioning_v0.md` on branches that contain
  `nxt_commissioning`
- `nxtektal-roi-engine/README.md`
- `nxtektal-roi-engine/docs/api-contract.md`

Use [source-of-truth.md](source-of-truth.md) before making claims that cross
layers, and [deployment.md](deployment.md) for implementation-status and
physical-site boundaries.
