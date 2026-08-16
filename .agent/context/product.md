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
| `simulation/` | Virtual handoff lab, whole-range runtime, Site OS state/advisory/evidence/projection layers | Python project managed with `uv` |
| `nxtektal-roi-engine/` | Deterministic, formula-locked driving-range ROI calculations | Independent TypeScript package |
| `apps/operational-replay/` | Browser storytelling over exported simulation replay evidence | Independent read-only Next.js app |

These implementation surfaces share governance and documentation, not a
runtime dependency.

## Product-layer vocabulary

- **Simulation runtime:** `RangeSimulation` and its ledger, resources, entities,
  events, metrics, forecast, and RNG streams.
- **Physical commissioning:** immutable, provenance-bearing static facility facts
  in the merged `nxt_commissioning.CommissionedSite` contract. Commissioning
  answers what exists, not what is happening now.
- **Canonical downstream state:** immutable `FacilityState` snapshots.
- **Decision support:** deterministic `nxt_facility` recommendations and the
  manager briefing. These are broad FacilityState-derived, advisory outputs.
- **Shadow Ops:** `nxt_pilot_ops` decision trust, trace, evaluation, human
  workflow, and tamper-evident ledger around named policies. It is advisory and
  downstream, not a duplicate general decision engine.
- **Site Runtime:** merged `nxt_site_runtime` orchestration around commissioned
  identity/configuration, sequenced observations, the existing telemetry
  assembler, publication-quality admission, exact FacilityState/AssemblyReport
  envelopes, checkpoints, recovery, and idempotent state publication. It owns no
  competing observation, state, policy, projection, or execution logic.
- **Digital twin:** serialized FacilityState/layout to USD projection. It is a
  view of truth, never the owner of truth.
- **Execution:** simulated directives behind `SafetyShield`, and robot tasks
  behind `RobotTaskInterface`. The Isaac Sim simulation and ROS 2 physical
  backends are stubs.
- **Operational memory:** append-only historical evidence used for offline,
  explicitly non-causal analysis.

## Honest-scope rules

- Physical values in the current simulator are placeholder-tagged. Outputs
  validate software pipelines, not robot or facility design.
- Whole-site integer ball inventory is simulated. Granular physical flow,
  friction, bridging, and jamming are not.
- The repository has observation contracts, synthetic sensors, and a merged
  orchestration library, but no concrete physical telemetry adapter/transport,
  hardware/vendor integration, production state publisher, or live site
  service.
- Commissioning, Shadow Ops, and Site Runtime are merged. Commissioning-to-
  runtime setup uses `bind_commissioned_site()` and the existing explicit
  `project_legacy_site_config()` compatibility projection; this is not a live
  physical integration.
- It has no site-level physical command-admission bridge, autonomous physical
  actuator execution, live Omniverse/Nucleus delivery, validated operating
  policy, production real-site deployment, or real-site performance evidence.
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
- `simulation/docs/shadow_ops_v0.md`
- `simulation/docs/commissioning_v0.md`
- `simulation/docs/site_runtime_design.md`
- `simulation/docs/agent_runtime_v1.md`
- `simulation/docs/edge_observation_v0.md`
- `nxtektal-roi-engine/README.md`
- `nxtektal-roi-engine/docs/api-contract.md`

Use [source-of-truth.md](source-of-truth.md) before making claims that cross
layers, and [deployment.md](deployment.md) for implementation-status and
physical-site boundaries.
