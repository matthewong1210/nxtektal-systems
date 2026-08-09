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
3. Classify the task as root Jarvis, Python `simulation/`, standalone ROI
   engine, or docs/agent infrastructure.
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

This is a heterogeneous repository with three independent surfaces:

| Surface | Technology | Role |
|---|---|---|
| Root Jarvis prototype | HTML, browser JavaScript, dependency-free Node server, assets | Personal command-center/voice prototype |
| `simulation/` | Python 3.11+, `uv`, Hatch, pytest | NXTektal virtual handoff, range operations, and Site OS layers |
| `nxtektal-roi-engine/` | TypeScript, npm, Vitest | Formula-locked deterministic ROI model |

The root README documents Jarvis and is not a monorepo architecture guide.
There is no workspace-level dependency graph connecting these three surfaces.
Do not add one without an explicit product requirement and architecture review.

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
- Synthetic observation and real-input contracts exist; a real production
  telemetry runtime does not.
- Commissioning is implemented on sibling draft PR #20, not in `main` or the
  audited Shadow checkout; no commissioning-to-runtime integration exists.
- Site Runtime is a future orchestration boundary, not an implemented package.
- Mock robot execution exists. Isaac Sim and ROS 2 physical adapters are stubs.
- Facility and Shadow Ops recommendations are advisory; no production command
  bridge exists.
- No LLM or generative agent has direct robot/actuator authority.
- Digital-twin/USD output is projection only.

Read [product context](../.agent/context/product.md) for the short version.

## Architecture contract

### Truth ladder

1. **Simulation runtime truth:** `RangeSimulation` owns the mutable SimPy
   environment, resources, robots/zones/stations, named RNG streams, forecast,
   metrics, and event log. `BallLedger` owns conserved ball counts/locations.
2. **Physical static truth:** where the unmerged commissioning package is
   present, its validated immutable `CommissionedSite` owns site/deployment
   identity, surveyed layout, assets/capabilities/safety constraints, sensor
   bindings/calibration, and provenance. It contains no live values.
3. **Canonical downstream state:** `FacilityState` is a frozen point-in-time
   contract. `build_facility_state(sim)` projects simulation truth without
   consuming RNG. The telemetry assembler can produce the same contract from
   observations plus declared static/upstream inputs and returns quality
   evidence separately.
4. **Advisory intelligence:** `nxt_facility` recommendations and Shadow Ops
   evaluations/recommendations consume downstream state and produce advice.
   They do not command execution.
5. **Trust, trace, and learning evidence:** Shadow Ops traces/workflow ledger and
   `nxt_memory` preserve decision/outcome history. They do not feed the live
   loop or become state truth.
6. **Projection:** viewer bundles, JSONL captures, layout, briefings, reports,
   and USD stages are derived outputs. Fix/regenerate them if they drift.
7. **Execution:** simulated directives enter only through
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

`SyntheticSensorBank` is the only implemented producer. Times are simulation
seconds, `SiteConfig` remains simulation-centric, and no physical adapters,
transport, scheduler, or production loop exists. `AssemblyReport` is separate
quality evidence and must accompany deployment-path use; missing/backfilled
values are not measurements.

Commissioning answers what physically exists and projects static facts one way.
Its draft-branch `project_site_config()` is static-only and not constructor-ready
for the current `SiteConfig`; only `project_legacy_site_config()` supplies that
shape, using explicit non-commissioned context.

The future Site Runtime may orchestrate selection of one commissioned
deployment, observation assembly, quality preservation, and downstream fan-out.
It owns no new state model, policy, projection, or execution behavior, and its
package name/design is not approved merely because the boundary is named. See
the [deployment contract](../.agent/context/deployment.md).

### Robotics and AI-control rule

Robots are the execution layer, not the decision authority. Preserve the full
handoff contract: hard task timeouts, classified invalid sequencing, bounded
docking/unload retry and recovery, safe retract after post-dock failure,
externally reset e-stop latching, and no motion after e-stop.

`RobotTaskInterface` is specifically the micro handoff task vocabulary. It does
not define whole-site physical collector dispatch, policy admission, or command
translation. No such physical site-level contract or owner exists today.

No LLM, generative agent, advisory engine, UI tool call, or future Site Runtime
may directly invoke `RangeSimulation.apply_directive()`, `RobotTaskInterface`,
an adapter, ROS, an actuator, or an e-stop API. Existing simulator policies may
choose only the closed directive vocabulary through `RangeOpsEnv`; it is
revalidated by `RangeSimulation.apply_directive()` and `SafetyShield`. A
physical command bridge would require a separately approved deterministic
admission/controller boundary; none exists today.

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
- `simulation/scripts/` are composition roots. The ROI engine independently
  owns its versioned formulas and traces.
- On a branch that contains it, `nxt_commissioning` owns immutable static
  physical facts and emits disposable one-way projections without importing
  downstream/runtime packages. Future Site Runtime remains orchestration only.

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
physical facts to commissioning where present, projections to the twin/viewer,
and robot task behavior to the handoff execution seam.

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
normative testing source. It covers all-extras Python setup, the current
`uv.lock`/`twin`-extra gap, focused package tests, architecture guards, the full
suite, config validation, package builds, ROI typecheck/tests/build, and root
Jarvis manual-check expectations.

The core rule is evidence outward from the change: focused regression, package,
boundary/parity/determinism, then the full surface. Optional dependency skips
do not count as coverage.

### Tooling gaps

No Python formatter, linter, or static type checker is configured in the
audited checkout. No repository-local GitHub Actions workflow was present. The
root Jarvis prototype has no automated test command. Report these as gaps; do
not substitute unconfigured tools and imply repository endorsement.

## Review checklist

Use the detailed, normative
[review workflow](../.agent/workflows/review.md). It checks scope/worktree
state, truth ownership, dependency direction, advice/execution/safety,
determinism/integrity/replay, honest product claims, test evidence, and final
hygiene. Missing/default/backfill behavior must match its owning contract and
remain visible through provenance/quality evidence.

## Recent-history lessons

Merged PRs #5–#14 established the current one-way phase ladder: handoff seam,
whole-range runtime, benchmark/viewer, FacilityState, advisory decisions,
memory, telemetry assembly, and projection-only twin. Repeated patterns were
recon/design first, downstream siblings, guard tests, reproducibility, parity,
and explicit honest-scope disclaimers.

At the second-pass audit, Shadow Ops PR #19 and commissioning PR #20 were open
draft sibling branches from the same `main` commit. Neither was merged and
neither branch contained the other package. Site Runtime and physical telemetry
adapters remained proposals only.

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
