# Package responsibility and dependency map

No human ownership metadata exists. The "owner" column names the code surface
responsible for a contract or behavior.

## Repository-level surfaces

| Path | Responsibility | Verification |
|---|---|---|
| `simulation/` | Python virtual handoff, operations runtime, and Site OS layers | Pytest, config validation, optional wheel build |
| `nxtektal-roi-engine/` | Deterministic formula-lock ROI engine | Typecheck, Vitest, package build |
| `apps/operational-replay/` | Read-only browser storytelling over exported replay artifacts | Typecheck, lint, Vitest, Next.js build, HTTP smoke, production audit |

Root documentation and `.agent/` govern all implementation surfaces without
owning production behavior or creating cross-surface runtime coupling.

## Python packages and tools

| Package/tool | Owns | Allowed first-party coupling | Forbidden/important boundary |
|---|---|---|---|
| `nxt_sim` | Micro handoff vocabulary, task controller, metrics, configs, backend adapters | Internal modules only | Not a whole-site collector-dispatch API; controllers/interfaces/metrics do not import adapters; adapters do not import controllers/scenarios |
| `nxt_range_ops` | `RangeSimulation`, `BallLedger`, directives, `SafetyShield`, Gym env, scenarios, evaluation | Only `nxt_sim.interfaces.types` and `nxt_sim.config.models` from `nxt_sim` | Must not depend on backend adapters or downstream Site OS packages |
| `nxt_range_agent` | Reproducible benchmark orchestration and reports | `nxt_range_ops` | Must not import `nxt_sim` directly; currently a documented rule rather than a comprehensive static guard |
| `nxt_facility` | Frozen `FacilityState`, pure analysis, broad deterministic manager recommendations, briefing | `build.py` may read `nxt_range_ops`; package-local imports | No `nxt_sim` or heavy sim libraries; upstream packages must not mention it |
| `nxt_memory` | Immutable memory records, append-only writer, observational queries | Only `harvest.py` may import `nxt_range_ops` | No live-loop feedback; core modules are self/stdlib only |
| `nxt_telemetry` | Observation contract, synthetic bank, state assembler, quality report | Only `bank.py`/`assemble.py` may use `nxt_range_ops` and `nxt_facility` | Other modules pure; no simulator RNG/private access or hidden clocks |
| `nxt_range_viewer` | Deterministic replay and exported public frames/layout | Direct `nxt_range_ops`; benchmark references via artifact data | Read-only presentation/export; do not turn viewer frames into policy input |
| `nxt_range_demo` | Streamlit presentation over exported bundles | Viewer bundle contract | No direct runtime ownership or simulator mutation |
| `nxt_range_twin` | State/layout file validation and USD layers | No production `nxt_*` imports; file contracts; `pxr` in USD modules | Projection only; no simulator imports, physics claims, or upstream feedback |
| `nxt_pilot_ops` | Named-policy evaluation, decision traces/trust evidence, immutable human workflow, hash-chained ledger | Only `adapters/` may import `nxt_facility.state`; core uses self/stdlib | No commands, ROS, actuator, motion, charging, or e-stop surface; not a second broad manager-rules package |
| `nxt_commissioning` | Immutable physical site/deployment identity, surveyed spatial facts, declared assets/capabilities/safety limits, sensor binding/calibration, provenance, canonical manifest storage, one-way static projections | Self/stdlib only | No live observations/state/tasks/demand; no runtime/downstream imports; projections are disposable and never write back to the manifest |
| `nxt_site_runtime` | Input ordering/validation, publication-quality gate, deterministic FacilityState/AssemblyReport envelope, checkpoints/recovery, idempotent state publication coordination | Hot path: `nxt_facility.state` and `nxt_telemetry` observation/assembly contracts; setup-only `composition.py` lazily uses commissioning's existing projection | Orchestration only; no duplicate state/assembler/policy/projection/execution; no simulator, Shadow Ops, memory, twin, viewer, robot, ROS, or actuator imports; only the designated `nxt_agent_runtime` composition layer may import the runtime — no other consumer or upstream package may |
| `nxt_agent_runtime` | Deterministic runtime lifecycle, deferred source acknowledgement, separate evaluation checkpoint, append-only evaluation journal (durable `NO_ACTION` evidence), pending manager-decision view with non-persistent deferral metadata, local snapshot publisher, read-only health/status | Public `nxt_site_runtime` pipeline/envelope/checkpoint/port surfaces, public `nxt_pilot_ops` adapters/guardian/contracts/workflow/ledger/serialization, `nxt_telemetry.observations` typing | Composition/lifecycle only; owns no observation, state, policy, recommendation, trace, workflow, memory, or execution semantics; no simulator, commissioning, memory, twin, viewer, robot, ROS, actuator, network, wall-clock, or UUID surface; no upstream or existing package may import it |
| `nxt_edge_observation` | Raw device payload normalization, commissioned-binding/calibration application, device-data validation, canonical Observation production, adapter conversion diagnostics, and the at-least-once raw-batch delivery cursor (peek/acknowledge/reject-with-sequence-reuse) that is the source side of the `ObservationSource` port | Public `nxt_telemetry.observations` only; the commissioning telemetry-adapter-config projection is consumed as plain data | Pure conversion plus the source-side delivery cursor; owns no observation semantics, state, assembly, sequence validation, quality admission, checkpointing, policy, or execution; no transport, network, process, robotics, wall-clock, or randomness import; must not import `nxt_site_runtime` — composing `ObservationSource`/`SequencedObservationFrame` belongs to a composition root; no existing package may import it |
| `nxt_workflow_enablement` | Workflow identity registration, versioned per-workflow requirement definitions, shared-site gate evaluation over validated commissioned truth, independent per-workflow readiness verdicts, the deterministic enablement report, and fixture-only launch-plan data | `nxt_commissioning` public contracts/validation/projections only; adapter and runtime facts arrive as declared plain-data evidence from composition roots | Readiness gating only; owns no observation, state, assembly, policy, recommendation, trace, workflow-case, memory, or execution semantics; registration never implies implementation; a NOT_READY workflow gets no runtime or evidence; no transport, network, filesystem, process, robotics, wall-clock, or randomness import; no existing package may import it; assembling a READY plan belongs to composition roots |
| `nxt_course_world_model` | Immutable, versioned Course World Model spatial truth: the course-local ENU frame bound to the commissioned coordinate reference, the finite elevation surface, semantic course features (holes, playing surfaces, cart paths, restricted areas), controlled map revisions with content addressing, canonical serialization, and the pure read-only Map Query Service including the narrow trajectory/terrain intersection | `nxt_commissioning` public contracts only (identity, spatial reference, provenance, canonical JSON); consumers receive serialized models or plain-data evidence via composition roots | Spatial truth and queries only; owns no commissioning, observation, state, readiness, policy, projection, memory, or execution semantics; no raw LAS/LAZ or point-cloud ingestion; not a route planner, navigation stack, or geofence enforcer; a restricted-area answer is information, never a command; no transport, network, filesystem, process, robotics, wall-clock, or randomness import; no existing package may import it; deriving readiness evidence and composing with other layers belongs to composition roots |
| `simulation/scripts/` | Composition roots for demos, capture, validation, and evaluation | May compose public package APIs | Do not use scripts to justify reverse imports in core packages |

`simulation/pyproject.toml` ships `nxt_sim`,
`nxt_range_ops`, `nxt_facility`, `nxt_memory`, `nxt_telemetry`,
`nxt_range_twin`, `nxt_pilot_ops`, `nxt_commissioning`,
`nxt_site_runtime`, `nxt_agent_runtime`, `nxt_edge_observation`,
`nxt_workflow_enablement`, and `nxt_course_world_model` in the wheel.
`nxt_range_agent`, `nxt_range_viewer`, and
`nxt_range_demo` are repository-local tools. Site Runtime's contract surface is
importable without simulation/USD dependencies, while successful processing
uses the existing telemetry assembler and currently needs the `range-ops`
compatibility extra. This does not confer simulation ownership.

`apps/operational-replay` is an isolated Node application. It may consume
existing artifact files through browser APIs, but it imports no Python or ROI
implementation and owns no replay generation, state, recommendation,
orchestration, projection, command, or execution semantics. No upstream package
may depend on it.

## Feature-placement and duplication gate

Use the responsibility tables above and
[architecture-review.md](../workflows/architecture-review.md) before choosing a
directory. Search existing source, exports, manifests, tests, rule IDs, open
branches, and both decision surfaces before adding a package or engine.

- Broad pure FacilityState-derived manager advice belongs in
  `nxt_facility.decisions`.
- Policy-specific trust, trace, evaluation, workflow, and ledger behavior
  belongs in `nxt_pilot_ops`.
- Presentation/composition may display both; it does not become a third policy
  owner or silently reconcile conflicting recommendations.
- Current ball-availability advice intentionally diverges: facility rules use
  the v1 state/supply model, while the Guardian applies a richer traced policy
  and fails closed on facts the native adapter cannot supply. The surfaces are
  not parity-locked, and no aggregator/conflict resolver exists.
- A new package requires a distinct fact class/lifecycle, an allowed dependency
  position, proof that no existing owner fits, explicit architecture approval,
  manifest/package-map updates, and a mechanical boundary guard.

## Contract-change routing

| If changing | Read first | Minimum focused tests |
|---|---|---|
| Handoff interface/controller/safety | `simulation/docs/architecture.md`, `robot_task_interface.py`, `handoff_state_machine.py` | `tests/test_architecture.py`, `test_state_machine.py`, `test_retry_recovery.py`, `test_unload_retry.py`, `test_emergency_stop.py`, plus affected adapter/scenario tests |
| `RangeSimulation`, ledger, directives, or safety | `simulation/docs/range_ops.md`, core source | Entire `tests/range_ops` plus downstream full suite |
| `FacilityState` or builder | `simulation/docs/facility_state.md`, telemetry/twin/Shadow adapters | Entire `tests/facility` plus telemetry, twin, pilot adapter and full suite |
| Facility recommendations | `facility_state.md`, `facility_decisions_design.md` as rationale | Facility decision/briefing/regression tests and no-command review |
| Memory contracts | `facility_memory_design.md` plus current code | Entire `tests/memory` and no-feedback guards |
| Observation contract/assembly | `facility_telemetry_design.md` plus current code | Entire `tests/telemetry`, facility parity, full suite |
| Commissioning manifest/projection | `commissioning_v0.md`, commissioning contracts/guards, [deployment.md](deployment.md) | Entire `tests/commissioning`, Site Runtime composition, telemetry/twin integration review, architecture suite, full suite |
| Site Runtime orchestration/envelope/checkpoint/publication contract | `site_runtime_design.md`, runtime contracts/guards, [deployment.md](deployment.md) | Entire `tests/site_runtime`, commissioning composition, telemetry assembly/quality, agent-runtime composition, architecture/safety suite, full suite |
| Agent Runtime lifecycle/evaluation checkpoint/journal/queue contract | `agent_runtime_v1.md`, runtime contracts/guards, [deployment.md](deployment.md) | Entire `tests/agent_runtime`, Site Runtime and Shadow Ops suites, architecture/safety suite, full suite |
| Edge observation adapters, raw sample shapes, or adapter diagnostics | `edge_observation_v0.md`, adapter contracts/guards, commissioning channel vocabulary | Entire `tests/edge_observation`, commissioning and telemetry suites, Site Runtime and Agent Runtime suites, architecture/safety suite, full suite |
| Workflow registry, requirement definitions, readiness verdicts, enablement report, or launch plan | `workflow_enablement_v0.md`, enablement contracts/guards, [deployment.md](deployment.md) | Entire `tests/workflow_enablement`, commissioning suite, architecture/safety suite, full suite |
| Course World Model identity, frame, elevation, semantic geometry, serialization/digest, revision semantics, or map queries | `course_world_model_v0.md`, model contracts/guards, commissioning spatial contracts | Entire `tests/course_world_model`, workflow-enablement and commissioning suites, architecture/safety suite, full suite |
| Facility-state stream or twin mapping | `spatial_twin_design.md`, stream/mapping source | Entire `tests/twin`, viewer/capture parity, full suite with `twin` extra |
| Shadow snapshot/trace/workflow | `shadow_ops_v0.md`, adapter and contracts | Entire `tests/pilot_ops`, boundary guards, full suite |
| ROI formula/API | ROI README, API contract, `AMBIGUITIES.md`, formula-lock spec | Typecheck, all Vitest tests, build |

Contract changes have a large fan-out. Prefer additive versioned envelopes or
adapters over silently changing a stable payload. When a schema change is truly
required, name migration, replay, drift, serialization, and downstream impact
in the plan.
