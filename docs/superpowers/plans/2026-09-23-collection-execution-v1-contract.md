# Collection Execution V1 Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Phase 3A architecture, strict versioned wire contracts, frozen normal/failure examples, TypeScript types/parser/GET-only client, and contract tests without implementing execution.

**Architecture:** Preserve Planning v1, Edge Task v1, V2 sessions and all old experiments. The new contract describes a future V3 simulator-backed task device and read-only result projection; it adds no API route, session driver, simulator event, task device, React component or execution call.

**Tech Stack:** Markdown, JSON Schema Draft 2020-12, Python 3.13/jsonschema/pytest, TypeScript, Vitest.

**Spec:** `simulation/docs/collection_execution_v1_architecture.md`

## Global Constraints

- Base is exactly `ffb0aa623c2e7093398b1d42a493c16411a61a70`; work only on `codex/collection-execution-3a`.
- Phase 3A is contract-only: no `env.step()`, simulator, policy, Edge package, API server, React, CSS or interaction implementation.
- Mark backend/runtime/UI execution as `DESIGN ONLY — NOT IMPLEMENTED`.
- Preserve every V2 artifact and paused/old experiment; add no migration or backfill.
- Overall `SUCCEEDED` requires positive ledger-backed raw collection and equal complete unload into the bound station before the explicit deadline.
- `RAW_COLLECTED_TO_ROBOT` is a milestone, never unloaded/washed/supplied/inventory or a Planning v1 outcome.
- Arbitration is `WAIT_ONLY_NON_PREEMPTIVE_V1`: an external proposal replaces only `Wait`; pending requests order by `(latest_start_sim_t_s, eligible_sim_t_s, execution_id)`.
- V1 has one session-wide running lease: a running continuation wins a `Wait` slot before a new start; multiple pending requests remain ordered deterministically.
- `max_execution_s` is the rounded-up control-interval multiple of confirmed `travel + collect + return + unload`; the frozen success value is 660 seconds.
- PENDING/RUNNING are non-terminal. Missing/conflicting quantity evidence is null/`INCOMPLETE`, never zero.
- PARTIAL never maps to Edge SUCCEEDED; terminal conflict is effective INCONCLUSIVE/CONFLICT and blocks success.
- Simulation time owns business/execution deadlines. Existing 15-second browser health uses wall time; session PAUSED is independent.
- The browser contract is GET-only and cannot confirm, start, retry, pause, resume or otherwise control execution.
- Existing Planning confirmation remains the only confirmation path. The downstream V3 binding names and verifies it; Planning v1 does not gain or name a future binding ID.
- V3 injects projected simulation UTC through Planning/Edge scheduling and device lifecycle; only outer service-read health uses the 15-second wall clock.
- Phase 3B must add the intent/step/commit/outbox crash protocol; it is not implemented in 3A.
- `MockRobotDevice` evidence is forbidden as quantity or execution-result evidence.
- Codex owns schema, examples, TypeScript types/parser/client and contract tests; no component/style/interaction files change.
- Do not push or merge.

---

### Task 1: Freeze the shared schema, semantics and examples

**Files:**
- Create: `simulation/docs/contracts/collection-execution-v1/README.md`
- Create: `simulation/docs/contracts/collection-execution-v1/schema.json`
- Create: `simulation/docs/contracts/collection-execution-v1/examples/success.json`
- Create: `simulation/docs/contracts/collection-execution-v1/examples/policy-missed.json`
- Create: `simulation/docs/contracts/collection-execution-v1/examples/partial-preempted.json`
- Create: `simulation/docs/contracts/collection-execution-v1/examples/safety-rejected.json`
- Create: `simulation/docs/contracts/collection-execution-v1/examples/restart-unknown.json`
- Create: `simulation/docs/contracts/collection-execution-v1/examples/identity-conflict.json`
- Create: `simulation/docs/contracts/collection-execution-v1/examples/duplicate-request.json`
- Create: `simulation/docs/contracts/collection-execution-v1/examples/terminal-conflict.json`
- Create: `simulation/tests/pilot_ops/test_collection_execution_wire_contract.py`

**Interfaces:**
- Consumes: the architecture spec, Planning v1 identifiers, Edge v1 lifecycle vocabulary and existing Manager API envelope.
- Produces: JSON Schema definitions named `Binding`, `ExecutionRequest`, `RequestReceipt`, `ExecutionSnapshot`, `ExecutionRecord`, `QuantityEvidence`, `SuccessEnvelope`, and `ErrorEnvelope`; examples consumed verbatim by Task 2.

- [ ] **Step 1: Write the failing Python contract test**

Use `Draft202012Validator`, a calendar-valid UTC `FormatChecker`, and this literal manifest:

```python
EXPECTED_EXAMPLES = {
    "success.json", "policy-missed.json", "partial-preempted.json",
    "safety-rejected.json", "restart-unknown.json",
    "identity-conflict.json", "duplicate-request.json",
    "terminal-conflict.json",
}
```

Validate every declared body by its `schema_ref` and the schema top-level union. Mutation tests cover unknown/missing fields, bool-as-int, non-UTC or invalid dates, foreign environment, bad digests/IDs, nonfinite clocks, illegal status/reason, quantity nullability, duplicate identities, cross-links, time ordering, wrong 660-second derivation, success without equal unload, PARTIAL→Edge SUCCEEDED, and conflict displayed as success.

- [ ] **Step 2: Verify RED**

Run:

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/pilot_ops/test_collection_execution_wire_contract.py
```

Expected: failure naming missing contract assets, not a syntax/import error.

- [ ] **Step 3: Add the normative README and schema**

README repeats owners, identity, clocks, wait-only ordering, 660-second example limit, idempotency, success/exits, Edge/restart mapping, quantity provenance, read-only API and design-only status. Schema uses `$id: "urn:nxtektal:collection-execution:v1"`, exact objects, bounded fields, lowercase SHA-256, strict UTC-Z timestamps and finite non-negative simulation seconds.

Freeze these constants:

```text
nxt-collection-execution-binding/v1
nxt-collection-execution-request/v1
nxt-collection-execution-request-receipt/v1
nxt-collection-executions/v1
SIMULATION
WAIT_ONLY_NON_PREEMPTIVE_V1
RAW_COLLECTED_TO_ROBOT
UNLOADED_TO_STATION
RANGE_SIMULATION_BALL_LEDGER
```

`ExecutionRecord.state` is exactly `PENDING|RUNNING|SUCCEEDED|PARTIAL|REJECTED|MISSED|FAILED|INCONCLUSIVE`. Quantity evidence is exactly `NOT_REACHED|null`, `COMPLETE|integer`, or `INCOMPLETE|null`. Include all source/binding/session/round/task identities, simulated timing/explicit maximum, original and selected actions, runtime/Edge evidence, device protection, raw/unload quantities and conflict/reconciliation flags.

- [ ] **Step 4: Add all eight frozen examples**

Each example contains a description, optional request/receipt or error bodies with `schema_ref`, and a complete Manager API snapshot envelope. Use `picker-01/Z1 → R1/NEAR_LEFT → H1` in the single-station V1 topology, epoch `2026-09-16T00:00:00Z`, a 60-second control interval and 660-second maximum. Success uses a `Wait` slot, an unchanged original-policy handoff, 44 raw balls, 44 unloaded balls and `UNLOADED_ALL_COLLECTED_BALLS`. Failures never invent quantities.

- [ ] **Step 5: Run focused and package tests green**

Run the Step 2 command, then the same pytest command against `tests/pilot_ops`. Expected: all pass without new skips.

- [ ] **Step 6: Commit Task 1**

```bash
git add simulation/docs/contracts/collection-execution-v1 simulation/tests/pilot_ops/test_collection_execution_wire_contract.py
git commit -m "docs(collection-execution): freeze v1 wire contract"
```

---

### Task 2: Add strict TypeScript types, parser and GET-only client

**Files:**
- Create: `apps/site-agent-console/lib/collection-executions.ts`
- Create: `apps/site-agent-console/tests/collection-executions-contract.test.ts`
- Modify: `apps/site-agent-console/tests/boundaries.test.ts`

**Interfaces:**
- Consumes: Task 1 `ExecutionSnapshot` examples without renaming/defaulting fields.
- Produces: `COLLECTION_EXECUTIONS_SCHEMA`, exported wire types, `parseCollectionExecutions(value)`, and `createCollectionExecutionsClient(fetchImpl)` with only `read(signal?)`.

- [ ] **Step 1: Write the failing Vitest contract test**

Load Task 1 examples directly. Import the missing parser/client. Assert snapshots parse, milestone/status separation remains exact, unknown quantities stay null, terminal conflict is INCONCLUSIVE, and the client exposes only `read`. Add mutation cases for unknown fields, PHYSICAL, NaN/Infinity, invalid UTC/calendar values, duplicate execution/task IDs, cross-session/task links, bad state/stage/reason, illegal timestamp/state coherence, quantity status/nullability, invalid success, PARTIAL→SUCCEEDED Edge mapping, conflict-as-success, and wall/simulation clock mixing.

- [ ] **Step 2: Verify RED**

Run `npm test -- tests/collection-executions-contract.test.ts` from `apps/site-agent-console`. Expected: missing `../lib/collection-executions`.

- [ ] **Step 3: Implement exact types/parser and GET-only client**

Follow `lib/course-ops.ts` strict `shape()` style, reject unknown fields, then validate relational invariants. Preserve nulls and calculate no quantities/actions/admission/defaults. `read(signal?)` calls `/api/v1/collection-executions` using GET, `cache: "no-store"`, existing envelope validation and the existing eight-second abort pattern. Expose no write method.

- [ ] **Step 4: Extend the boundary assertion**

Allow only the exact read-only collection-executions route while keeping execution verbs, simulator vocabulary, Python imports, browser persistence and components forbidden.

- [ ] **Step 5: Run focused/full console verification**

Run:

```bash
npm test -- tests/collection-executions-contract.test.ts tests/boundaries.test.ts
npm run typecheck
npm run lint
npm test
```

Expected: every command passes.

- [ ] **Step 6: Commit Task 2**

```bash
git add apps/site-agent-console/lib/collection-executions.ts apps/site-agent-console/tests/collection-executions-contract.test.ts apps/site-agent-console/tests/boundaries.test.ts
git commit -m "feat(console): parse collection execution v1"
```

---

### Task 3: Verify the complete Phase 3A delivery

**Files:**
- Modify only if verification exposes inconsistency: files owned above or the architecture spec.

**Interfaces:**
- Consumes: all prior commits.
- Produces: a reviewable Phase 3A branch with exact test evidence and no Phase 3B implementation.

- [ ] **Step 1: Check scope**

Inspect `git diff --name-status ffb0aa623c2e7093398b1d42a493c16411a61a70...HEAD`; only the architecture/plan, contract/examples/tests, TypeScript parser/client and boundary assertion may appear.

- [ ] **Step 2: Run Python verification**

Run the new contract test, all `tests/pilot_ops`, the normative architecture subset, the full Python suite and `scripts/validate_configs.py` exactly as specified in `.agent/workflows/testing.md`.

- [ ] **Step 3: Run console verification**

Run `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`, `npm run smoke`, and separately `npm audit --omit=dev` from `apps/site-agent-console`.

- [ ] **Step 4: Run repository hygiene**

Run the repository verifier and both diff checks required by `.agent/workflows/testing.md`, then follow `.agent/workflows/review.md` and `.agent/workflows/hygiene.md`. Record every skip and limitation.

- [ ] **Step 5: Final review and handoff**

Review the branch against the spec and original task brief. Confirm prominently that simulator/device/API/component behavior is still `DESIGN ONLY — NOT IMPLEMENTED`. Do not push, merge or begin Phase 3B.
