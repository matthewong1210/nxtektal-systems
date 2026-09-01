# NXTektal AI engineering operating manual

## Purpose

This manual is the shared operating layer for Codex, Claude Code, and human
engineers working in this repository. It shortens onboarding while protecting
the architecture already encoded in source, tests, stable docs, and merged
history.

It does not propose a new product architecture. It formalizes the repository's
current truth hierarchy, package responsibilities, dependency boundaries,
workflow, testing expectations, and review standard.

## Five-minute onboarding

1. Read root [`AGENTS.md`](../AGENTS.md).
2. Run `git status --short --branch` and identify whether the checkout is
   `main`, a feature branch, or a stacked branch.
3. Classify the task as Python `simulation/`, standalone ROI engine,
   Operational Replay web app, or docs/agent infrastructure.
4. Read the matching package source, tests, and stable docs—not only plans or
   PR prose.
5. Open the relevant files in [`.agent/context/`](../.agent/context/) and
   [`.agent/workflows/`](../.agent/workflows/).
6. Before editing, name the source of truth, downstream contract, allowed
   dependency direction, and required verification.
7. Run the [architecture review gate](../.agent/workflows/architecture-review.md)
   before adding a package/runtime/engine/contract boundary or changing robot,
   control, safety, or AI-to-execution behavior.

## Repository reality

This standalone repository has three independent implementation surfaces:

| Surface | Technology | Role |
|---|---|---|
| `simulation/` | Python 3.11+, `uv`, Hatch, pytest | NXTektal virtual handoff, range operations, and Site OS layers |
| `nxtektal-roi-engine/` | TypeScript, npm, Vitest | Formula-locked deterministic ROI model |
| `apps/operational-replay/` | TypeScript, Next.js, npm, Vitest | Read-only browser storytelling over exported replay artifacts |

The root README is the NXTektal product and investor overview. Root
documentation and `.agent/` form the shared governance layer; they do not add a
runtime surface. There is no workspace-level dependency graph connecting the
implementation surfaces. Do not add one without an explicit product requirement
and architecture review.

There is no named human ownership file. Package ownership in this manual means
responsibility for behavior and contracts.

## Product context and strategy

The repository-backed NXTektal domain is autonomous ball collection and
whole-site driving-range operations. The product principle is that the unit of
autonomy is the site, not the robot.

The owner-provided strategy is that the AI operating layer is the moat. The
codebase expresses that operating layer as trusted state, deterministic and
auditable advice, provenance, trace, evaluation, and operational memory. Robot
hardware executes; the twin projects; neither replaces the operating layer.

Current claims must remain honest:

- The simulator's physical values are placeholders unless individually marked
  supplier/measured.
- Integer facility ball inventory is simulated, but granular flow, bridging,
  friction, and jamming are not.
- Synthetic observations, real-input contracts, and a Site Runtime
  orchestration library exist; concrete physical telemetry adapters/transports,
  hardware/vendor integrations, production publishers/sinks, and a live site
  service do not.
- Shadow Ops, Commissioning, and Site Runtime are merged. Commissioning-to-
  runtime setup exists through `bind_commissioned_site()` and an explicit
  compatibility projection; this is not a live physical integration.
- Mock robot execution exists. The Isaac Sim simulation adapter and ROS 2
  physical adapter are stubs.
- Facility and Shadow Ops recommendations are advisory; no production command
  bridge exists.
- No LLM or generative agent has direct robot/actuator authority.
- Digital-twin/USD output is projection only.
- Site-level physical command admission, autonomous actuator execution, live
  Omniverse/Nucleus delivery, and production real-site deployment are not
  implemented.

Read [product context](../.agent/context/product.md) for the short version.

## Architecture contract

### Truth ladder

1. **Simulation runtime truth:** `RangeSimulation` owns the mutable SimPy
   environment, resources, robots/zones/stations, named RNG streams, forecast,
   metrics, and event log. `BallLedger` owns conserved ball counts/locations.
2. **Physical static truth:** the validated immutable
   `nxt_commissioning.CommissionedSite` owns site/deployment
   identity, surveyed layout, assets/capabilities/safety constraints, sensor
   bindings/calibration, and provenance. It contains no live values.
3. **Canonical downstream state:** `FacilityState` is a frozen point-in-time
   contract. `build_facility_state(sim)` projects simulation truth without
   consuming RNG. The telemetry assembler can produce the same contract from
   observations plus declared static/upstream inputs and returns quality
   evidence separately.
4. **Orchestration:** `nxt_site_runtime` validates and orders input, invokes the
   existing telemetry assembler, applies mechanical publication-quality rules,
   preserves the exact state/report in a deterministic envelope, and coordinates
   checkpoint/recovery and idempotent state publication. It owns no domain
   truth, advice, projection, command admission, or execution.
5. **Advisory intelligence:** `nxt_facility` recommendations and Shadow Ops
   evaluations/recommendations consume downstream state and produce advice.
   They do not command execution.
6. **Trust, trace, and learning evidence:** Shadow Ops traces/workflow ledger and
   `nxt_memory` preserve decision/outcome history. They do not feed the live
   loop or become state truth.
7. **Projection:** viewer bundles, JSONL captures, layout, briefings, reports,
   and USD stages are derived outputs. Fix/regenerate them if they drift.
8. **Execution:** simulated directives enter only through
   `RangeSimulation.apply_directive()` and `SafetyShield`. Robot task execution
   is sequenced by `HandoffController` through `RobotTaskInterface` and its
   selected adapter.

The complete matrix is in
[`.agent/context/source-of-truth.md`](../.agent/context/source-of-truth.md).

### Decision-versus-execution nuance

"Decision engines are advisory" applies to the Site OS decision surfaces:
`nxt_facility.decisions` and `nxt_pilot_ops`. `nxt_range_ops.policies` are
different: they choose directives inside the simulation training environment.
Those directives still pass through the simulator's non-bypassable
`SafetyShield` and do not form a production robot-control path.

The two advisory surfaces have different ownership. `nxt_facility.decisions`
owns broad, deterministic manager advice directly over `FacilityState`.
`nxt_pilot_ops` owns named-policy evaluation, trace/trust evidence, human
workflow, and ledger records. Before adding advice, search both. Do not create a
third decision engine or implement the same rule twice; for unavoidable overlap,
name one semantic owner and define tested reuse, parity, or intentional
divergence.

One overlap already exists and must be understood, not copied. Facility rules
use the v1 state stockout/supply model for broad manager advice. The Ball
Availability Guardian evaluates a richer traced policy; repository-native
adaptation leaves collection permission, collector capability, ETA/yield,
washer availability, and timed inbound batches unavailable, so it fails closed
rather than treating facility advice as policy evidence. These outputs are not
parity-locked. No current aggregator/conflict resolver exists; any presentation
must preserve owner, policy/rule identity, evidence, and rationale separately.

### Digital twin rule

The twin consumes the exact versioned schema
`nxt-range-twin/facility-state-stream/v1` and declared layout through file
contracts. Production twin modules import no live simulation packages. USD may
display state and authored static geometry but must not invent operational
facts, continuous motion, physics, or policy inputs. If dynamic USD state and
FacilityState disagree, USD is wrong.

### Shadow Ops rule

`nxt_pilot_ops` adapts FacilityState into an `OperationalSnapshot`, evaluates
the Ball Availability Guardian, emits deterministic recommendation/trace
records, supports immutable human workflow, and stores a hash-chained ledger.
Only its `adapters/` package may know the upstream FacilityState shape. The
policy core is self/stdlib-only and contains no robot-command surface.

The ledger is tamper-evident within its documented threat model, not externally
anchored non-repudiation. Execution request/acknowledgement objects are human
workflow records, not actuator calls.

### Deployment and telemetry rule

The current observation path is exactly:

```text
ObservationFrame + SiteConfig + UpstreamInputs + optional previous FacilityState
    -> assemble_from_observations()
    -> FacilityState + AssemblyReport
```

`SyntheticSensorBank` remains the core package producer. The script-level Edge
Gateway V0 also accepts strict local mock-MQTT load-cell input for diagnostic or
explicitly hybrid fixture rehearsal; it is not a physical device adapter,
durable source, production service, or customer telemetry integration. The
general telemetry assembler supports optional previous-state backfill, but
`SiteRuntimePipeline` calls its three-argument path and rejects missing/stale
required input before publication. `AssemblyReport` is separate quality
evidence and must accompany deployment-path use; missing/backfilled values are
not measurements.

Commissioning answers what physically exists and projects static facts one way.
`project_site_config()` is static-only and not constructor-ready for the current
`SiteConfig`; `project_legacy_site_config()` supplies that shape using explicit
non-commissioned context. `bind_commissioned_site()` uses the existing
projection once at runtime setup.

The merged `nxt_site_runtime` coordinates `SequencedObservationFrame` input,
validation, the existing assembler, the `AssemblyReport` quality gate, the exact
`FacilityState` plus report in `nxt-site-runtime/facility-snapshot/v1`,
checkpoint/recovery, and idempotent `StatePublisher` delivery. The quality gate
admits state publication based on data quality; it is not recommendation policy,
physical command admission, or robot safety authorization. `StatePublisher` and
best-effort `RuntimeSink` are state/visibility protocols, not actuator ports.

No concrete physical source/transport/publisher/sink, external long-running
site service, live hardware/vendor integration, or production real-site loop is
implemented. See the
[deployment contract](../.agent/context/deployment.md).

### Robotics and AI-control rule

Robots are the execution layer, not the decision authority. Preserve the full
handoff contract: hard task timeouts, classified invalid sequencing, bounded
docking/unload retry and recovery, safe retract after post-dock failure,
externally reset e-stop latching, and no motion after e-stop.

`RobotTaskInterface` is specifically the micro handoff task vocabulary. It does
not define whole-site physical collector dispatch, policy admission, or command
translation. No such physical site-level contract or owner exists today.

No LLM, generative agent, advisory engine, UI tool call, or Site Runtime
may directly invoke `RangeSimulation.apply_directive()`, `RobotTaskInterface`,
an adapter, ROS, an actuator, or an e-stop API. Existing simulator policies may
choose only the closed directive vocabulary through `RangeOpsEnv`; it is
revalidated by `RangeSimulation.apply_directive()` and `SafetyShield`. A
physical command bridge would require a separately approved deterministic
admission/controller boundary; none exists today.

LLMs must not participate in execution, command admission, actuator control,
e-stop handling, or safety loops. Site-level physical command admission,
autonomous actuator execution, live Omniverse/Nucleus delivery, and production
real-site deployment are explicitly outside the implemented system.

## Package responsibilities

The normative responsibility/dependency table is
[`.agent/context/package-map.md`](../.agent/context/package-map.md). In short:

- `nxt_sim` owns the micro robot task seam; `nxt_range_ops` owns the mutable
  whole-site simulation and guarded directive path.
- `nxt_facility` owns canonical downstream state and broad facility advice;
  `nxt_pilot_ops` owns Shadow Ops trust, trace, evaluation, and workflow.
- `nxt_telemetry` owns observation input/assembly; `nxt_memory` owns historical
  evidence; neither owns live truth.
- `nxt_range_viewer` independently replays `RangeOpsEnv`; `nxt_range_demo`
  presents bundles; `nxt_range_twin` projects FacilityState streams/layout into
  USD. All remain derived/read-only surfaces.
- `apps/operational-replay` presents selected exported artifacts in a standalone
  browser story. It imports no Python/ROI runtime and owns no state, advice, or
  execution behavior.
- `nxt_commissioning` owns immutable static physical facts and emits disposable
  one-way projections without importing downstream/runtime packages.
- `nxt_site_runtime` owns state orchestration metadata and behavior only; its
  hot path uses telemetry/facility contracts and its setup-only composition seam
  lazily uses commissioning's existing projection.
- `simulation/scripts/` are composition roots. The ROI engine independently
  owns its versioned formulas and traces.

## Source selection and documentation discipline

Prefer executable truth over stale narrative, while preserving intentional
versioned contracts:

1. Versioned schemas/formula rules and architecture guard tests.
2. Current source, manifests, and behavioral tests.
3. Stable contract/architecture docs.
4. Design docs for rationale.
5. Recon, plans, PR descriptions, demos, and generated artifacts for history.

Several design/recon/plan files retain pre-implementation status language. Do
not copy it into current claims without checking code and history. Likewise,
an untracked architecture audit may inform recon but is not authority.

Status words are part of correctness. Label a component as merged/current
checkout, implemented on a named unmerged branch, approved design, or
proposed/future. Never union sibling branches into one implemented architecture.

Documentation for a change should state:

- responsible package and source-of-truth scope;
- inputs, outputs, and allowed consumers;
- forbidden dependencies and non-goals;
- determinism, safety, provenance, and missing-data behavior;
- compatibility/versioning implications;
- exact verification commands and observed results;
- limitations and placeholder/estimate disclaimers.

Use repository-relative links and portable commands. Avoid machine-specific
paths in committed docs.

## Coding workflow

### Preflight

Inspect branch/worktree state, read the relevant code/tests/docs/history, and
identify unrelated changes. Never overwrite or clean them as part of a task.

### Contract definition

Name the fact/behavior, owner, inputs, outputs, consumers, reverse dependency
that must remain forbidden, compatibility surface, and verification plan.

### Placement and architecture gate

Search current packages, exports, manifests, schemas, stores, rule IDs, tests,
and open branches before choosing a new directory. Route simulation dynamics to
`nxt_range_ops`, downstream state/broad advice to `nxt_facility`, observations
to `nxt_telemetry`, policy trust/trace/workflow to `nxt_pilot_ops`, static
physical facts to `nxt_commissioning`, state orchestration to
`nxt_site_runtime`, projections to the twin/viewer, and robot task behavior to
the handoff execution seam.

A new package is allowed only when it owns a distinct fact class/lifecycle, no
existing owner fits, its dependency position is explicit, architecture approval
is recorded, manifests/package maps are updated, and a mechanical guard is
planned. Use the full
[pre-implementation review](../.agent/workflows/architecture-review.md).

### Minimal implementation

Prefer downstream siblings, pure contracts, explicit adapters, and versioned
envelopes. Avoid opportunistic refactors. Preserve missingness evidence and
provenance, use only contract-defined backfill, and protect determinism,
canonical bytes, safety admission, and old replay/model versions.

### Verification

Run focused tests first, then package and boundary suites, then the complete
surface suite. Add parity tests for alternate paths, trajectory/RNG tests for
read-only layers, integrity/replay tests for records, and regression tests for
every confirmed defect.

### Review and handoff

Review the actual final diff, run hygiene, and report exact commands/results,
skips, limitations, branch dependencies, and pre-existing dirty state.

The executable sequence is in
[`.agent/workflows/change.md`](../.agent/workflows/change.md).

## Testing expectations

Use the exact commands in
[`.agent/workflows/testing.md`](../.agent/workflows/testing.md), the single
normative testing source. It covers lock-consistent all-extras Python setup,
focused package tests, architecture guards, the full suite, config validation,
package builds, ROI typecheck/tests/build, and root documentation and
agent-infrastructure checks.

The core rule is evidence outward from the change: focused regression, package,
boundary/parity/determinism, then the full surface. Optional dependency skips
do not count as coverage.

### Tooling gaps

No Python formatter, linter, or static type checker is configured at the
merged-main baseline. The post-migration verification workflow is documented
in [`CI.md`](CI.md). Do not substitute unconfigured tools or describe local
commands as GitHub Actions evidence.

## Review checklist

Use the detailed, normative
[review workflow](../.agent/workflows/review.md). It checks scope/worktree
state, truth ownership, dependency direction, advice/execution/safety,
determinism/integrity/replay, honest product claims, test evidence, and final
hygiene. Missing/default/backfill behavior must match its owning contract and
remain visible through provenance/quality evidence.

## Recent-history lessons

Merged PRs #5–#14 established the initial one-way phase ladder: handoff seam,
whole-range runtime, benchmark/viewer, FacilityState, advisory decisions,
memory, telemetry assembly, and projection-only twin. Repeated patterns were
recon/design first, downstream siblings, guard tests, reproducibility, parity,
and explicit honest-scope disclaimers.

The merge train then landed Shadow Ops PR #19 at
`e84c5016a19d1d4aec0b4b183164c08bba5b164e`, Commissioning PR #20 at
`89e93f6a8ea0cd469d6da907321eafe30318fa49`, and Site Runtime PR #22 at
`b055c9472737feb923c6ac48fad44a5b7e43333c`. The merged packages preserve the
same one-way ownership model. Physical adapters/transports and a production
real-site loop remain absent.

AI Engineering Operating System PR #23 then merged at
`192292735221e503915f286627dc64f001942881`, versioning this manual,
`AGENTS.md`, the Claude Code entry point, and `.agent/` context, workflows, and
skills without changing production contracts.

The audit found no formal human GitHub reviews or test/build CI checks on those
merges. Test totals and adversarial-review results in PR descriptions were
author-reported local evidence. Current work must record its own commands and
results rather than inheriting those claims.

See [the dated history audit](../.agent/context/repository-history.md) for merge
links, branch-status caveats, and stale-document warnings.

## Agent skills

- Use [`nxtektal-change`](../.agent/skills/nxtektal-change/SKILL.md) for
  implementation, fixes, refactors, contracts, tests, scripts, or architecture
  documentation.
- Use [`nxtektal-review`](../.agent/skills/nxtektal-review/SKILL.md) for diff,
  PR, design, contract, pre-merge, or pre-handoff review.

The skills are concise entry points. The context and workflow files remain the
shared source so Codex, Claude Code, and humans follow the same operating model.
They live in the tool-neutral `.agent/skills/` tree requested for this
repository and are not assumed to auto-register with Codex or Claude Code.
`AGENTS.md` and `CLAUDE.md` route agents to load them explicitly; do not fork
copies into tool-specific trees that can drift.
