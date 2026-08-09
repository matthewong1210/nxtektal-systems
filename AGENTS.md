# NXTektal Systems engineering instructions

These instructions apply to the entire repository. A more specific `AGENTS.md`
may add tighter rules for its subtree, but it must not weaken the architecture
invariants below.

## Start here

Before editing:

1. Read [`docs/AGENT_OPERATING_MANUAL.md`](docs/AGENT_OPERATING_MANUAL.md).
2. Inspect `git status --short --branch`, the current branch, and the relevant
   package's source, tests, and stable docs. Preserve unrelated worktree changes.
3. Classify the request as root Jarvis, `simulation/`, ROI engine, or
   documentation/agent infrastructure. These are independent surfaces.
4. Read the matching files under [`.agent/context/`](.agent/context/) and
   [`.agent/workflows/`](.agent/workflows/).
5. State the owner of every fact you will read or write. If ownership is
   ambiguous, stop and resolve it before adding another state model.
6. Run the pre-implementation gate in
   [`.agent/workflows/architecture-review.md`](.agent/workflows/architecture-review.md)
   before adding a package, runtime, decision engine, cross-package contract,
   robotics/control path, or AI-to-execution integration.

There is no `CODEOWNERS` file. "Ownership" in this repository means
architectural responsibility, not an inferred person or team.

## Repository scopes

- Root HTML, JavaScript, assets, and `scripts/jarvis_server.mjs` form the
  independent Jarvis prototype described by the root `README.md`.
- `simulation/` is the Python NXTektal simulation and Site OS stack.
- `nxtektal-roi-engine/` is an independent deterministic TypeScript formula
  engine. It does not depend on the Python stack.
- Cross these boundaries only through an already documented contract. Do not
  create a monorepo-wide dependency merely for convenience.

## Non-negotiable architecture

1. `RangeSimulation` owns live mutable simulation-runtime truth.
   `BallLedger` is authoritative for conserved ball location/counts.
2. `FacilityState` is the frozen canonical downstream state contract. It is a
   projection of runtime or observation inputs, not a second mutable runtime.
3. Digital-twin and USD operational state is a `FacilityState`-derived
   projection; declared layout/assets are separate static projection inputs.
   Neither is truth. If dynamic output disagrees with `FacilityState`,
   regenerate or fix it; never feed twin/USD output upstream as truth.
4. Viewer artifacts come from an independent deterministic `RangeOpsEnv`
   replay through public APIs. They are still read-only projections, but
   `FacilityState` is not their direct source contract.
5. Physical-facility onboarding and static site facts belong to commissioning.
   Where `nxt_commissioning` is present, its validated, immutable
   `CommissionedSite` manifest is authoritative for site/deployment identity,
   surveyed layout, assets, capabilities, safety limits, sensor bindings, and
   their provenance. Scenarios, `SiteConfig`, telemetry, and USD are not
   competing physical-static truth stores. At the 2026-08-09 audit this package
   existed on draft PR #20, not on `main` or this checkout; verify branch status.
6. The current observation path is `ObservationFrame` plus `SiteConfig`,
   `UpstreamInputs`, and optional previous `FacilityState` through
   `assemble_from_observations()`, producing `FacilityState` plus
   `AssemblyReport`. A future Site Runtime is an orchestration boundary around
   these contracts, not an implemented package, new state model, decision
   engine, or command path. Do not create it without an approved design.
7. Site-level decision outputs from `nxt_facility` and `nxt_pilot_ops` are
   advisory. They must not invoke directives, robots, ROS, actuators, charging,
   motion planning, or emergency-stop APIs.
8. `nxt_facility.decisions` owns broad deterministic manager advice directly
   over `FacilityState`. `nxt_pilot_ops` owns policy-specific decision trust,
   trace, evaluation, human workflow, and ledger evidence. Search both before
   adding advisory behavior. Do not create a third decision engine or duplicate
   a rule across both; name one semantic owner and an explicit reuse,
   parity, or intentional-divergence contract when overlap is unavoidable.
   Existing ball-availability overlap is intentional non-parity: facility rules
   advise from the v1 state, while the Guardian traces a stricter policy and
   fails closed on unavailable permission/capability/ETA/yield/washer facts.
   Keep their outputs identified by owner; no conflict resolver exists.
9. Shadow Ops (`nxt_pilot_ops`) is the decision trust, trace, evaluation, human
   workflow, and tamper-evident record layer. It is not an execution layer.
10. Robots and their adapters are the execution layer behind
    `HandoffController` and `RobotTaskInterface`. Preserve hard task timeouts,
    bounded retry/recovery, safe retract, the externally reset e-stop latch, and
    the rule that no motion follows e-stop. This interface covers the micro
    handoff cycle; it is not a site-level collector-dispatch API or physical
    command gateway. Physical Isaac Sim and ROS 2 adapters are currently stubs;
    do not claim deployed robot execution.
11. The AI operating layer—the trusted state, advisory, trace, and evaluation
   system—is the strategic moat. Preserve its boundaries instead of collapsing
   it into the simulator, twin, or robot adapter.
12. The sole simulated fleet-control path is the closed directive vocabulary
    through `RangeSimulation.apply_directive()` and its non-bypassable
    `SafetyShield`. `nxt_range_ops.policies` control only the simulator; they are
    not production robot command engines.
13. No LLM, generative agent, advisory engine, or future AI package may call
    `RangeSimulation.apply_directive()`, `RobotTaskInterface`, an adapter, ROS,
    or an actuator directly. LLM output is advisory only. Any physical execution
    integration requires a separately reviewed deterministic
    admission/controller boundary that preserves robot and hardware safety; no
    such production bridge exists today.
14. Operational memory is append-only historical evidence and must not feed the
    live loop. Recommendation and workflow ledgers do not become state truth.
15. Do not invent physical facts, demand, capabilities, ETAs, defaults, or
    provenance. Preserve missingness and fail closed where the repository does.

See [`.agent/context/source-of-truth.md`](.agent/context/source-of-truth.md) for
the complete truth matrix and [`.agent/context/package-map.md`](.agent/context/package-map.md)
for allowed dependency directions.

## Source precedence

Use, in order:

1. Versioned contracts, formula-lock rules, and executable architecture guards.
2. Current source, manifests, and behavioral tests.
3. Stable package documentation such as `simulation/docs/facility_state.md`,
   `simulation/docs/range_ops.md`, `simulation/docs/spatial_twin_design.md`, and
   the current-branch `simulation/docs/shadow_ops_v0.md` or
   `simulation/docs/commissioning_v0.md` when those packages are present.
4. Design documents for rationale.
5. Recon files, plans, PR descriptions, and generated artifacts for historical
   evidence only.

If sources at the same or higher tier conflict, do not silently choose one.
Report the conflict and either reconcile it within scope or request a decision.
An untracked document is never repository authority by itself.

## Dependency rules

- Keep `nxt_sim` controllers/interfaces independent of adapters. Adapters must
  not import controller or scenario logic.
- Let `nxt_range_ops` import only `nxt_sim.interfaces.types` and
  `nxt_sim.config.models` from Phase 0.
- Keep upstream packages unaware of `nxt_facility`, `nxt_memory`,
  `nxt_telemetry`, `nxt_range_twin`, and `nxt_pilot_ops` as required by their
  guard tests.
- Within downstream Site OS state/evidence/advisory packages, only designated
  seams may touch upstream implementation: `nxt_facility.build`,
  `nxt_memory.harvest`, `nxt_telemetry.bank`/`assemble`, and
  `nxt_pilot_ops.adapters`. Repository-local benchmark/viewer tools may consume
  the public `nxt_range_ops` APIs described in the package map.
- Keep `nxt_range_twin` coupled through serialized state/layout contracts, not
  Python imports. `pxr` belongs only in USD-authoring modules.
- Where `nxt_commissioning` exists, keep it stdlib-only and independent of
  runtime/downstream packages. Consumers receive deterministic one-way
  projections; they do not write physical facts back from `SiteConfig`, a
  scenario, telemetry, or USD.
- Treat `simulation/scripts/` as composition roots, not as permission to move
  orchestration into core packages.
- Do not duplicate ROI formulas outside `@nxtektal/roi-engine`; semantic formula
  changes require a new `model_version` and recomputability of prior versions.

## Change workflow

Follow [`.agent/workflows/change.md`](.agent/workflows/change.md):

1. Recon the current contract, tests, and recent history.
2. Search existing packages, contracts, decision rules, and open branches for
   an existing owner; run the architecture-review gate when triggered.
3. Define the change boundary, inputs, outputs, truth owner, and non-goals.
4. Make the smallest coherent change without opportunistic refactors.
5. Add focused behavioral and boundary tests. Fixes require regression tests.
6. Run focused checks, then the required full suite.
7. Review the diff against [`.agent/workflows/review.md`](.agent/workflows/review.md).
8. Run [`.agent/workflows/hygiene.md`](.agent/workflows/hygiene.md) and report
   exact commands, results, skips, and remaining risks.

Use conventional commit vocabulary when commits are requested: `docs:`,
`feat(scope):`, `test(scope):`, and `fix(scope):`. Do not merge or publish
unless the user explicitly asks.

## Testing baseline

Use the exact, normative commands in
[`.agent/workflows/testing.md`](.agent/workflows/testing.md). Python production
changes require a complete all-extras environment, focused/boundary checks, the
full suite, and config validation. ROI changes require typecheck, tests, and a
build. The workflow also records the current `uv.lock`/`twin`-extra gap so an
agent does not silently change the lock or skip USD coverage.

No Python formatter, linter, or type checker and no repository-local CI
workflow are currently configured; never claim those checks ran unless
configuration is added and the commands actually pass. The root Jarvis
prototype has no automated test command.

## Review standard

Reject or revise a change that:

- creates another mutable facility truth or bypasses `FacilityState`;
- treats a scenario, `SiteConfig`, telemetry, or USD as physical commissioning
  truth, or presents an unmerged/future deployment boundary as implemented;
- uses viewer/USD/memory/recommendation data as live input truth;
- creates a duplicate package, decision engine, or recommendation rule without
  a named semantic owner and approved boundary;
- lets advisory code call an execution surface or bypasses `SafetyShield`;
- lets an LLM or generative agent directly call a robot interface, adapter, ROS,
  actuator, or emergency-stop API;
- reverses a guarded dependency or hides cross-package coupling;
- consumes simulator RNG or changes a deterministic trajectory from a
  read-only layer;
- weakens canonical serialization, provenance, append-only behavior, or
  fail-loud schema checks;
- presents placeholders, projections, observations, or non-causal analytics as
  validated physical truth;
- changes a contract without explicit versioning, migration, drift, and replay
  consideration; or
- reports test/CI/review evidence that was not actually observed.

## Knowledge map

- Product and repository scope: [`.agent/context/product.md`](.agent/context/product.md)
- Truth ownership: [`.agent/context/source-of-truth.md`](.agent/context/source-of-truth.md)
- Architecture: [`.agent/context/architecture.md`](.agent/context/architecture.md)
- Deployment maturity and flow: [`.agent/context/deployment.md`](.agent/context/deployment.md)
- Package responsibilities: [`.agent/context/package-map.md`](.agent/context/package-map.md)
- Audit and merged-PR history: [`.agent/context/repository-history.md`](.agent/context/repository-history.md)
- Pre-implementation architecture gate: [`.agent/workflows/architecture-review.md`](.agent/workflows/architecture-review.md)
- Human operating manual: [`docs/AGENT_OPERATING_MANUAL.md`](docs/AGENT_OPERATING_MANUAL.md)
- Safe-change skill: [`.agent/skills/nxtektal-change/SKILL.md`](.agent/skills/nxtektal-change/SKILL.md)
- Review skill: [`.agent/skills/nxtektal-review/SKILL.md`](.agent/skills/nxtektal-review/SKILL.md)
