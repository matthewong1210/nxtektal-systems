# Source-of-truth rules

Truth is scoped. The phrase "source of truth" is invalid unless it names the
fact class and runtime boundary.

## Truth matrix

| Fact class | Authority | What it is not |
|---|---|---|
| Mutable whole-site simulation state | `nxt_range_ops.core.sim.RangeSimulation` | `FacilityState`, viewer frames, memory, or USD |
| Ball positions/counts during simulation | `RangeSimulation.ledger` / `BallLedger`, mutated through `move()` | Sensed inventory, charts, or recommendations |
| Simulated control admission | `RangeSimulation.apply_directive()` plus `SafetyShield` | A direct policy-to-robot call |
| Canonical downstream operational snapshot | Frozen `nxt_facility.state.FacilityState` | A mutable store or simulation engine |
| Simulation-to-state projection | `nxt_facility.build.build_facility_state()` using RNG-neutral public reads | A new dynamics implementation |
| Observation-to-state assembly | `nxt_telemetry.assemble_from_observations()` returning `FacilityState` plus `AssemblyReport` | A second downstream state schema or proof of real telemetry |
| Observation evidence | `ObservationFrame`, source/timing/provenance fields, and `AssemblyReport` | Ground truth without assembly and quality context |
| Physical deployment static facts | Validated immutable `nxt_commissioning.CommissionedSite` manifest | `RangeOpsScenario`, `SiteConfig`, observations, viewer/layout files, or USD |
| Site Runtime orchestration | `nxt_site_runtime` sequence/input validation, publication-quality gate, deterministic envelope/checkpoint/recovery, and idempotent state publication coordination | Observation semantics, a second assembler/state model, advice, projection, physical command admission, or execution |
| Agent Runtime evaluation lifecycle | `nxt_agent_runtime` deferred-acknowledgement cycle ordering, separate evaluation checkpoint, append-only evaluation journal (including durable `NO_ACTION` evidence), pending manager-decision view, read-only status | State, observation, policy, recommendation, trace, or workflow semantics; a second publication checkpoint; a decision engine; a command surface; facility truth |
| Raw-device-to-canonical-observation conversion and its diagnostics | `nxt_edge_observation` adapters, adapter-local device profiles, and `EdgeAdapterReport` | Observation semantics, a second telemetry envelope, facility truth, a channel registry, a commissioned fact, or any transport/command surface |
| Multi-workflow commissioning readiness | `nxt_workflow_enablement` workflow registry, versioned requirement definitions, independent per-workflow verdicts, deterministic enablement report, and fixture-only launch-plan data | Commissioning truth, facility state, telemetry, policy output, workflow-case records, proof a registered capability exists, live device connectivity, or physical execution |
| Versioned course spatial truth (course-local frame, elevation surface, semantic course features, map revisions) and deterministic map queries | Immutable `nxt_course_world_model.CourseWorldModel` plus its read-only `MapQueryService`, bound to commissioned identity and validated with `validate_model_against_site` | Commissioned static truth, facility state, telemetry, a live map, twin/USD output, readiness verdicts, a route planner, a navigation stack, a landing model, or any command surface |
| Site Agent service shell | `nxt_site_agent` service lifecycle state, versioned loopback Manager API projections, fixture source-cursor persistence, and noncanonical service diagnostics | Facility truth, observation semantics, policy or workflow semantics, a second evidence store, wall-clock canonical times, an authentication story, or any physical command surface |
| Facility advice | `nxt_facility.decisions.Recommendation` | Directive or execution acknowledgement |
| Shadow decision evaluation | `nxt_pilot_ops` snapshot, evaluation, trace, and recommendation | Command, actuator, safety shield, or live state |
| Human/execution workflow evidence | Shadow Ops immutable workflow records and hash-chained ledger | Proof the physical act occurred beyond the recorded acknowledgement |
| Operational history | `nxt_memory` append-only windows and queries | Live policy input or causal evidence |
| Viewer replay/output | `nxt_range_viewer` deterministic `RangeOpsEnv` replay and exported public observation/info/events | FacilityState stream, input truth, or operational authority |
| Dynamic twin output | FacilityState-stream/layout-derived USD artifacts | Input truth or operational authority |
| Static twin geometry on the current simulated path | Declared `layout.json` and provenance-tagged assets | Surveyed physical geometry unless explicitly supplied as such |
| Micro handoff task execution seam | `HandoffController` sequencing through `RobotTaskInterface` and a selected adapter | Site-level dispatch, decision truth, or an LLM tool surface |
| ROI formulas and outputs | Versioned `@nxtektal/roi-engine` model and formula traces | A second implementation in UI/API code |
| Physical/economic parameters | Each value's explicit measured/supplier/placeholder evidence | An untagged default or inferred fact |

## Required interpretations

### RangeSimulation versus FacilityState

`RangeSimulation` is authoritative for live mutable state during a simulation
episode. `FacilityState` is authoritative as the shape passed downstream. A
downstream consumer must not reach back into simulator internals to fill gaps,
and `FacilityState` must not acquire mutation methods to become a runtime.

The alternate telemetry assembler exists to prove that observation inputs can
produce the same downstream contract. It does not demote `RangeSimulation`
during simulation or create a competing state representation.

### Commissioning versus Site Runtime

Commissioning owns static physical-facility declarations, not live state. A
validated `CommissionedSite` is immutable by `(site_id, deployment_id)` and
projects one way into disposable configuration/layout/binding data.
`bind_commissioned_site()` uses the existing legacy `SiteConfig` projection and
explicit non-commissioned context once during runtime setup; it does not move
live values or orchestration into commissioning.

The merged Site Runtime is orchestration only. It coordinates commissioned
identity/configuration, sequenced observation batches, the existing telemetry
assembler, publication-quality admission, the exact `FacilityState` and
`AssemblyReport` in `nxt-site-runtime/facility-snapshot/v1`, checkpoint/recovery,
and idempotent state publication. It owns none of the underlying facts or
semantics. Its `QualityGate` admits a state envelope based on data quality; it
is not decision policy, physical command admission, or a robot safety gate.
`StatePublisher` and `RuntimeSink` are state/visibility ports, never actuator
ports. See [deployment.md](deployment.md).

### Site Runtime versus Agent Runtime

`nxt_agent_runtime` is the designated composition/lifecycle layer over Site
Runtime and Shadow Ops. It drives the existing pipeline through its public
API, defers the observation-source acknowledgement until the evaluation
lifecycle for the published envelope completes, and records exactly one
runtime evaluation outcome per admitted envelope in its append-only journal.
Its evaluation checkpoint is a separate fact class from the Site Runtime
publication checkpoint and never carries publication, policy, or
human-workflow semantics. The journal and status reference canonical IDs
(`envelope_id`, `trace_id`, `recommendation_id`, ledger `event_id`); they are
evidence and diagnostics, never a second `FacilityState` or policy source of
truth, and never live-loop input. A rejected Site Runtime input produces no
evaluation, no `NO_ACTION` record, no checkpoint advance, and no pending
decision.

### Truth versus observation

Keep `clean_available` accounting truth distinct from `clean_sensed` evidence.
Do not add, average, or silently substitute them. Preserve timestamps,
availability, missingness, staleness, confidence, and provenance.

The current assembly boundary is exactly:

```text
ObservationFrame + SiteConfig + UpstreamInputs + optional previous FacilityState
    -> FacilityState + AssemblyReport
```

`SiteConfig` is a consumer input shape, not physical commissioning authority.
`AssemblyReport` is separate from the state and must accompany deployment-path
use so a backfilled/default value is never reported as a measurement.

Site Runtime v0 calls the three-argument assembly path without a previous
`FacilityState` and rejects missing/stale required input before publication. It
then keeps the exact state and report together with runtime quality and source
references in its deterministic envelope. This wrapper does not change
telemetry ownership of assembly or FacilityState ownership of the state schema.

### Truth versus projection

Viewer frames, briefings, benchmark reports, JSONL captures, and USD stages are
regenerable outputs. When they disagree with their inputs, the output is wrong.
Never patch a projection by hand to establish an operational fact.

### Advice versus execution

Facility and Shadow Ops recommendations are immutable advisory records. Human
acceptance, modification, execution request, execution acknowledgement, and
outcome records are workflow evidence, not a hidden command bus. No current
package translates them into physical commands.

`nxt_range_ops.policies` are a separate simulator-only control mechanism. They
select a closed directive vocabulary that is revalidated inside
`RangeSimulation`; they do not establish a production execution architecture.

`nxt_facility.decisions` owns broad, deterministic FacilityState-derived manager
advice. `nxt_pilot_ops` owns policy-specific evaluation, decision trace, trust,
human workflow, and ledger evidence. Search both before adding a recommendation.
Do not create a third engine or silently implement the same semantics twice.

The existing ball-availability overlap is intentional divergence, not parity.
Facility rules use the v1 `FacilityState` stockout/supply model to give broad
manager advice. The Ball Availability Guardian evaluates its richer
`OperationalSnapshot` policy and records complete trace/trust evidence. The
repository-native adapter deliberately leaves collection permission, collector
capability, ETA/yield, washer availability, and timed inbound batches
unavailable; the Guardian fails closed rather than treating facility advice as
policy evidence. No current component reconciles, ranks, or deduplicates the two
outputs. A presentation must keep owner, policy/rule ID, evidence, and rationale
distinct until an approved composition/conflict contract exists.

No LLM, generative agent, advisory policy, or Site Runtime component has execution
authority. It must not directly invoke `RangeSimulation.apply_directive()`,
`RobotTaskInterface`, adapters, ROS, actuators, or emergency-stop APIs. Existing
simulator policies use the closed directive vocabulary through `RangeOpsEnv` and
`SafetyShield`; a physical command boundary does not exist in the repository.

Concrete physical observation sources/transports, live hardware/vendor
integrations, production state publishers/consumer sinks, site-level physical
command admission, autonomous actuator execution, live Omniverse/Nucleus
delivery, and production real-site deployment are not implemented. The Edge
Observation Adapter Kit V0 converts already-read synthetic samples through the
existing Observation contract; it is fixture-backed and adds no transport,
device connection, register write, or command path. Protocols
and test doubles do not satisfy those boundaries, and LLMs must not participate
in execution, command admission, actuator control, e-stop handling, or safety
loops.

## Evidence and provenance policy

- Never let a missing measurement masquerade as a measured zero. The telemetry
  assembler may use its documented prior/default backfill—including zero—only
  while surfacing missingness, provenance, and consistency in `AssemblyReport`.
- Never invent a capability, ETA, current demand, route permission, washer
  availability, geometry, or sensor validity.
- Keep estimates labeled as estimates and projections labeled as projections.
- Retain placeholder/supplier/measured provenance on physical parameters.
- Fail loudly on unknown enums, drifted schemas, malformed canonical records,
  or unsupported model versions where the owning contract requires rejection.
  Telemetry assembly may return a non-conserving snapshot only with an explicit
  consistency issue; downstream policy must not treat that report as clean
  evidence.
- Do not use wall-clock time, random UUIDs, or unscoped RNG in deterministic
  contracts where repository guards prohibit them.
- Treat observational memory queries as non-causal.

## Document authority

When documentation conflicts, use this order:

1. Versioned schemas/formula rules and executable architecture guards.
2. Current code, manifests, and behavioral tests.
3. Stable contract/architecture docs.
4. Approved design rationale.
5. Recon, implementation plans, PR prose, demos, and generated artifacts.

Do not use the order to hide a contradiction. Record it and reconcile the
documentation in the same change when that is safely in scope.
