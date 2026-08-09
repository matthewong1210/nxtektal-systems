---
name: nxtektal-review
description: Review NXTektal repository changes for architectural consistency, truth ownership, dependency direction, determinism, safety, provenance, test evidence, and honest scope. Use for diffs, pull requests, design reviews, contract reviews, and pre-merge or pre-handoff audits.
---

# Review a NXTektal change

## Load the review contract

1. Read `../../../AGENTS.md`.
2. Read `../../context/source-of-truth.md`,
   `../../context/architecture.md`, and `../../context/package-map.md`; read
   `../../context/deployment.md` for physical-site/runtime/robot work.
3. Use the full checklist in `../../workflows/review.md`.
4. For new packages, engines, runtimes, contracts, or control paths, compare the
   proposal with `../../workflows/architecture-review.md`.
5. Inspect the actual branch/diff and relevant source/tests; do not review only
   the PR description or intended design.

## Review in risk order

Check first for:

1. New or bypassed sources of truth.
2. Incorrect maturity claims: Shadow Ops, Commissioning, and Site Runtime are
   merged, while physical adapters/live integrations/deployment are absent.
3. Site Runtime taking observation, assembler, state, policy, projection,
   command-admission, or execution ownership, or losing assembly-quality,
   envelope, sequence, checkpoint, recovery, or idempotency evidence.
4. Duplicate packages, decision engines, policies, schemas, or stores.
5. Advisory/LLM-to-execution or safety-boundary violations.
6. Reverse imports and hidden cross-package coupling.
7. Determinism, replay, canonical-byte, hash-chain, or provenance regressions.
8. Schema/version compatibility and fail-closed behavior.
9. Missing focused, boundary, parity, full-suite, or build evidence.
10. Misleading physical-performance, telemetry, robot-control, causal, review,
   or CI claims.

Distinguish mechanically enforced rules from conventions. When a documented
boundary lacks a guard, evaluate both the current violation risk and whether a
new regression guard belongs in scope.

## Produce findings-first output

For every actionable finding, include severity, a tight file/line location or
proposal section/quoted claim, the violated contract, the concrete failure
mode, and the smallest safe correction. Do not manufacture findings.

After findings, summarize tested evidence, skipped checks, residual risks, and
the pre-existing worktree state. Historical PR test counts and bot comments are
context only; accept only commands/results actually observed for the change.
