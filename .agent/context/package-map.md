# Package responsibility and dependency map

No human ownership metadata exists. The "owner" column names the code surface
responsible for a contract or behavior.

## Repository-level surfaces

| Path | Responsibility | Verification |
|---|---|---|
| Root Jarvis files | Dashboard, voice loop, landing page, and 3D assets | No automated root test suite; use scoped manual checks |
| `simulation/` | Python virtual handoff, operations runtime, and Site OS layers | Pytest, config validation, optional wheel build |
| `nxtektal-roi-engine/` | Deterministic formula-lock ROI engine | Typecheck, Vitest, package build |

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
| `simulation/scripts/` | Composition roots for demos, capture, validation, and evaluation | May compose public package APIs | Do not use scripts to justify reverse imports in core packages |

On the audited Shadow Ops checkout, `simulation/pyproject.toml` ships `nxt_sim`,
`nxt_range_ops`, `nxt_facility`, `nxt_memory`, `nxt_telemetry`,
`nxt_range_twin`, and `nxt_pilot_ops` in the wheel. `nxt_range_agent`,
`nxt_range_viewer`, and `nxt_range_demo` are repository-local tools. Recheck the
manifest on another branch.

## Branch-reviewed and future deployment boundaries

These must not be silently unioned with the table above:

| Boundary | Status at 2026-08-09 | Owns | Forbidden/important boundary |
|---|---|---|---|
| `nxt_commissioning` | Implemented on sibling `feature/commissioning-v0` / draft PR #20; absent from `main` and this checkout | Immutable physical site/deployment identity, surveyed spatial facts, assets/capabilities/safety constraints, sensor bindings/calibration, provenance, canonical manifest storage, one-way projections | No live observations/state/tasks/demand; stdlib-only; no downstream/runtime imports; projections remain disposable and do not mutate the manifest |
| Future Site Runtime | Conceptual boundary only; no package or approved package name | Cross-contract scheduling and fan-out around commissioning, observation assembly, FacilityState, and quality evidence | Owns no facts, state schema, policy, projection, or execution; no simulator internals, decision duplication, or robot/LLM command path |

The commissioning and Shadow Ops feature branches are siblings from `main`, not
one integrated branch. Reconcile package registration and run both boundary
suites when they are intentionally integrated.

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
| Commissioning manifest/projection, on a branch that contains it | `commissioning_v0.md`, commissioning contracts/guards, [deployment.md](deployment.md) | Entire `tests/commissioning`, telemetry/twin integration review, architecture suite, full suite |
| Facility-state stream or twin mapping | `spatial_twin_design.md`, stream/mapping source | Entire `tests/twin`, viewer/capture parity, full suite with `twin` extra |
| Shadow snapshot/trace/workflow | `shadow_ops_v0.md`, adapter and contracts | Entire `tests/pilot_ops`, boundary guards, full suite |
| ROI formula/API | ROI README, API contract, `AMBIGUITIES.md`, formula-lock spec | Typecheck, all Vitest tests, build |

Contract changes have a large fan-out. Prefer additive versioned envelopes or
adapters over silently changing a stable payload. When a schema change is truly
required, name migration, replay, drift, serialization, and downstream impact
in the plan.
