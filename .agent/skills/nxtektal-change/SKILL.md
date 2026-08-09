---
name: nxtektal-change
description: Execute architecture-safe changes in the NXTektal repository. Use for implementation, refactoring, bug fixes, contract or schema changes, package additions, tests, scripts, and architecture documentation across the simulation/Site OS stack, ROI engine, or root Jarvis surface.
---

# Change NXTektal safely

## Establish context

1. Read `../../../AGENTS.md` and `../../../docs/AGENT_OPERATING_MANUAL.md`.
2. Read `../../context/source-of-truth.md` and
   `../../context/package-map.md`; read `../../context/deployment.md` for
   physical-site, telemetry, commissioning, runtime, or robot work.
3. Follow `../../workflows/change.md`. Run
   `../../workflows/architecture-review.md` before a new package/runtime/engine,
   cross-package contract, robotics/control change, or AI-to-execution path.
4. Use
   `../../workflows/testing.md` for verification.
5. Inspect the branch, dirty worktree, relevant public APIs, guards, stable
   docs, and recent history before editing.

## Route the task

- Route mutable **simulation-runtime** behavior to `nxt_range_ops`; no physical
  Site Runtime is implemented.
- Route immutable physical onboarding/static facts to commissioning only on a
  branch that contains `nxt_commissioning`; never infer them from sim config.
- Route canonical downstream state to `nxt_facility` without turning it into a
  mutable runtime.
- Route observation evidence/assembly to `nxt_telemetry`.
- Treat future Site Runtime work as unimplemented cross-contract orchestration,
  not a state, policy, projection, or execution owner.
- Route historical evidence to `nxt_memory` without feedback.
- Route viewer replay/export work to `nxt_range_viewer`, using public
  `nxt_range_ops` APIs without runtime ownership or hidden simulator facts.
- Route USD twin work to `nxt_range_twin` without simulation-package imports or
  novel facts.
- Route broad deterministic FacilityState manager advice to
  `nxt_facility.decisions`; route policy trust/trace/evaluation/workflow to
  `nxt_pilot_ops` without commands.
- Route micro handoff task sequencing/execution through `HandoffController` and
  `RobotTaskInterface`, preserving timeout/recovery/e-stop contracts. No
  site-level physical collector-dispatch contract exists.
- Route ROI formulas exclusively through the versioned ROI engine.
- Keep root Jarvis, Python simulation, and ROI changes independent unless an
  existing contract explicitly connects them.

## Preserve the architecture

Name the truth owner, inputs, outputs, consumers, and forbidden reverse
dependency in the plan. Reject any design that:

- creates a second live facility truth;
- duplicates an existing package, decision engine, policy, schema, or store;
- uses memory, recommendations, viewer frames, or USD as runtime input;
- lets advisory code call execution APIs;
- lets an LLM, generative agent, or tool call reach directives,
  `RobotTaskInterface`, adapters, ROS, actuators, or e-stop APIs directly;
- bypasses `SafetyShield`;
- hides cross-layer coupling in a convenience import;
- lets missing/default/backfill behavior masquerade as measured fact or erases
  provenance; or
- changes a stable/versioned contract without migration, replay, and drift
  handling.

Prefer a pure contract plus an explicit adapter. Reuse existing guard patterns
and composition-root scripts. Preserve deterministic ordering, RNG neutrality,
canonical serialization, and content-derived identifiers where established.
Search both decision surfaces before adding advice; when overlap is unavoidable,
name one semantic owner and define tested reuse, parity, or intentional
divergence.

## Verify and hand off

Run focused tests, the relevant boundary/parity guards, and the full surface
suite required by `../../workflows/testing.md`. Then review with
`../../workflows/review.md` and run `../../workflows/hygiene.md`.

Report exact commands and observed results. Name skips, limitations, branch
dependencies, and unrelated pre-existing worktree changes without implying
they passed or were modified.
