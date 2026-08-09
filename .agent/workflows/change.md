# Change workflow

Use this workflow for product code, contracts, tests, scripts, or architecture
documentation. Scale the depth to the risk, but do not skip truth ownership or
verification.

## 1. Preflight

1. Read `AGENTS.md` and the relevant context files.
2. Run `git status --short --branch`; identify the branch base and preserve
   unrelated tracked/untracked work.
3. Identify the repository surface and package responsibility.
4. Read its stable docs, public API, nearest tests, architecture guards, and
   recent commits/PRs touching the same boundary.
5. Search existing packages, exported symbols, schemas, stores, rule IDs, and
   open/sibling branches for an existing implementation or owner before naming
   a new package or engine.
6. Record assumptions and explicit non-goals. Do not use proposed/untracked
   architecture as implemented truth.

## 2. Define the change contract

Before editing, answer:

- What fact or behavior changes?
- Which package owns it?
- What are the inputs and outputs?
- Is the output runtime truth, downstream state, advice, history, or projection?
- For a physical site, is the fact commissioned static configuration,
  observation evidence, assembly quality, downstream state, orchestration, or
  execution?
- Which packages consume it?
- Which dependency direction must remain impossible?
- Does it alter a versioned contract, deterministic bytes, replay, RNG order,
  safety, provenance, or formula lock?
- What focused, boundary, parity, and full-suite evidence will demonstrate the
  result?
- Does advisory behavior already exist in either `nxt_facility.decisions` or
  `nxt_pilot_ops`, and which one is the single semantic owner?

Run [architecture-review.md](architecture-review.md) before implementation when
the change adds or alters a package, runtime/service, source-of-truth contract,
decision/policy engine, cross-package dependency, robot/control/safety path,
physical adapter, or AI-to-execution integration.

If a new mutable owner, duplicate package/engine, reverse import, physical
command path, or LLM-to-actuator path appears necessary, stop. Recheck the
architecture and obtain explicit approval before implementation.

## 3. Plan minimally

- Prefer a downstream adapter or versioned envelope over modifying stable
  runtime/state contracts.
- Keep pure contracts separate from privileged adapters.
- Name migration and compatibility behavior for any schema/version change.
- Use existing serializers, fixtures, scripts, and guard-test patterns.
- Avoid unrelated cleanup and do not modify generated artifacts to mask drift.
- Prefer extending an existing owner. A new package requires proof that no
  existing responsibility fits, a distinct lifecycle/contract, an allowed DAG
  position, package-map/manifest updates, and a mechanical boundary guard.

For a new architecture layer, follow the repository precedent: recon, design,
explicit non-goals/risks, approval, then implementation. Do not create new
architecture merely to satisfy a local coding convenience.

## 4. Implement in a narrow slice

- Make one coherent layer change at a time.
- Preserve deterministic ordering, explicit time, canonical bytes, and
  content-derived identifiers where already required.
- Preserve missingness evidence and provenance. Use only contract-defined
  backfill/default behavior, surface it in the quality report, and fail closed
  where the owning boundary requires rejection.
- Keep advice separate from commands and execution safety.
- Keep LLM/generative output advisory. It must not directly call simulator
  directives, `RobotTaskInterface`, adapters, ROS, actuators, or e-stop APIs.
- For deployment-path state, preserve `AssemblyReport` beside
  `FacilityState`; do not relabel backfilled/default values as measurements.
- Add or update docs with the contract, boundaries, limitations, and exact
  verification commands.

## 5. Test outward from the change

1. Run the nearest focused test or add a failing regression first.
2. Run the package suite.
3. Run relevant architecture, no-feedback, parity, and determinism guards.
4. Run the full surface suite from [testing.md](testing.md).
5. Run config validation or package builds where applicable.

Do not rely on a skipped optional dependency for coverage. Use all extras for
the Python full suite, and report skipped tests explicitly.

## 6. Review and hygiene

- Review the final diff using [review.md](review.md), not only the files you
  intended to change.
- Convert confirmed defects into regression tests.
- Run [hygiene.md](hygiene.md).
- Confirm no secrets, machine paths, caches, reports, build products, or
  unrelated files entered the diff.

## 7. Handoff

Lead with the outcome. Report:

- changed files and the architecture boundary preserved;
- exact test/build/hygiene commands and observed results;
- skipped or unavailable checks without implying they passed;
- remaining limitations, risks, and any branch/PR dependency;
- whether the worktree still contains unrelated pre-existing changes.
