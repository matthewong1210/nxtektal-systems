# Collection Execution V3 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing verified Planning confirmation and admitted Edge task drive one durable V3 simulation session through actual collection and handoff, then expose strict read-only execution evidence.

**Architecture:** Keep Planning v1, Edge Task v1, `JointDispatchPolicy`, `SafetyShield`, `BallLedger`, V2 sessions, and old experiments as their existing owners. Add a V3-only assignment-evidence side stream to `nxt_range_ops`; compose binding, wait-only arbitration, intent/step/commit/outbox persistence, replay, the simulator-backed Edge device, and result projection under `simulation/scripts/`; inject only a read callback into `nxt_site_agent.api`. The accepted Phase 3A gate result is **Proceed** and is not reopened.

**Tech Stack:** Python 3.13, SimPy, Gymnasium, stdlib JSON/file locking, existing `nxt_edge_task` journals and contracts, pytest/jsonschema, TypeScript, Vitest.

**Spec:** `simulation/docs/collection_execution_v1_architecture.md`

## Global Constraints

- Base is exactly `064b90456557acf78b769dfb12f3bf2ccf3cdf17`; work only in `/private/tmp/nxtektal-collection-execution-3b` on `codex/collection-execution-3b`.
- Preserve V2 and every old experiment byte-for-byte at their persisted contract boundaries. Do not migrate, restore, overwrite, or resume them.
- `RangeSimulation` and `BallLedger` own live movement facts and quantities. Assignment lineage is an independent V3 evidence stream; it never mutates ledger counts or legacy event payloads.
- Call `JointDispatchPolicy.act()` exactly once and `RangeOpsEnv.step()` exactly once per committed tick. A V3 action may replace only `Wait`; every original non-`Wait` action remains selected. `SafetyShield` is always the final admission authority.
- The one running continuation outranks new starts. Pending order is exactly `(latest_start_sim_t_s, eligible_sim_t_s, execution_id)`; latest start is exclusive.
- Simulation time owns Planning, Schedule, request, execution, and deadline decisions. Wall time owns only process budgets and the browser's 15-second service-read health.
- `SUCCEEDED` requires actual assignment-tagged, complete, positive `RAW_COLLECTED_TO_ROBOT` evidence and equal complete `UNLOADED_TO_STATION` evidence at the bound station before the exact deadline, plus conservation, robot-payload parity, verified Edge terminal, and no conflict/protection.
- `MockRobotDevice`, inventory deltas, legacy metrics, Planning outcomes, and prepared results are never quantity or success evidence. Do not write Planning outcomes, wash results, supply results, or inventory replenishment.
- A normal chunk replay is deterministic prefix reconstruction. A real Edge device restart applies the existing incarnation/unknown-outcome rules and never reuses old authorization.
- PENDING/RUNNING are non-terminal. Incomplete or conflicting evidence keeps quantities `null`, never zero. PARTIAL never maps to Edge SUCCEEDED.
- Codex owns schema/examples, TypeScript wire types/parser/client, API contracts, and backend tests. Claude owns React, CSS, and component/interaction tests; do not edit Claude's worktree or infer permissions in UI code.
- Use failing tests first for every task. Use `apply_patch` for edits. Make local conventional commits only; do not push or merge.

---

### Task 1: Add V3-only simulator assignment evidence

**Files:**
- Create: `simulation/nxt_range_ops/core/assignment_evidence.py`
- Modify: `simulation/nxt_range_ops/core/sim.py`
- Modify: `simulation/nxt_range_ops/env/range_ops_env.py`
- Create: `simulation/tests/range_ops/test_collection_execution_assignment.py`
- Modify only for byte-invariance coverage: `simulation/tests/range_ops/test_event_log_and_recording.py`

**Interfaces:**
- `RangeOpsEnv(..., collection_assignment_evidence=False)` leaves V2 behavior unchanged.
- `arm_collection_assignment(execution_id, robot_id, zone_id, handoff_station_id, execution_deadline_sim_t_s)` records a one-shot candidate; only a final-shield-accepted matching `AssignCollection` consumes it.
- `collection_assignment_snapshot(execution_id)` returns detached deterministic evidence with simulator-owned assignment/event IDs.

- [x] Write failing tests for accepted collection/handoff, final shield rejection, full/empty/access-closed exits, policy preemption, battery/fault/e-stop/assistance, exact timeout/session end, wrong station, conservation, deterministic replay, and V3-disabled legacy bytes.
- [x] Verify RED with `tests/range_ops/test_collection_execution_assignment.py`.
- [x] Add an immutable independent event log containing `ASSIGNMENT_STARTED`, `RAW_COLLECTED_TO_ROBOT`, `COLLECTION_EXIT`, `UNLOADED_TO_STATION`, and `ASSIGNMENT_TERMINAL`; derive IDs without RNG.
- [x] Emit transfer quantities only from the actual return value of `BallLedger.move()`. Keep legacy `EventLog`, action catalog/order, observations, and ledger API unchanged.
- [x] Interrupt the active assignment at the exact SimPy execution deadline and terminalize once for every frozen exit reason. Prove no motion after e-stop and no move after the access boundary.
- [x] Run the focused range-ops tests and commit `feat(range-ops): record collection assignment evidence`.

---

### Task 2: Implement durable V3 binding, request, arbitration, and projection

**Files:**
- Create: `simulation/scripts/course_collection_execution.py`
- Create: `simulation/tests/course_monitoring/test_course_collection_execution.py`

**Interfaces:**
- `CollectionExecutionStore` appends canonical binding/request/receipt/prepared/committed/outbox/cursor records with fsync and replays only verified prefixes.
- `bind_confirmed_tasks(planning_snapshot, edge_records, session_identity, now_sim_t_s)` binds only verified `TASK_CREATED` evidence to the frozen one-station topology.
- `submit(request)` is content-idempotent and returns the original receipt; an identity/content conflict fails closed.
- `arbitrate(original_action, now_sim_t_s, runtime_view)` returns the selected catalog action and frozen reason while preserving non-`Wait` policy actions.
- `snapshot()` and `request_result(request_id)` produce the exact Phase 3A schema without advancing the simulator.

- [x] Write failing tests for identity/binding freshness, single-station validation, duplicate/unknown/conflicting requests, deterministic pending order, running-continuation priority, exclusive latest-start miss, action decisions, null unknown evidence, Edge mapping, and terminal conflict.
- [x] Verify RED.
- [x] Implement canonical content IDs and the fixed per-tick `prepared -> one live step -> committed + Edge outbox -> cursor` protocol using durable records and verified replay.
- [x] Implement the frozen success predicate and all status/reason/Edge/protection mappings. Keep raw/unloaded/washed/supplied/Planning outcome stages separate.
- [x] Validate every public snapshot against `simulation/docs/contracts/collection-execution-v1/schema.json` in tests.
- [x] Run focused tests and commit `feat(collection-execution): persist v3 requests and results`.

---

### Task 3: Add the V3 session/run/series driver

**Files:**
- Create: `simulation/scripts/course_session_v3.py`
- Create: `simulation/scripts/course_session_series_v3.py`
- Create: `simulation/tests/course_monitoring/test_course_session_v3.py`

**Interfaces:**
- `V3Session` owns one explicit `session_id`, `series_id`, `round_id`, epoch, control interval, and finite session end; it does not derive identity from a resumed mutable directory.
- `advance()` performs one policy call, wait-only arbitration, durable prepare, one `env.step()`, durable commit/outbox, and cursor-last.
- `run(root, config)` performs deterministic verified prefix replay before live continuation and refuses unknown/torn/inconsistent prefixes.
- `course_session_series_v3.run()` creates bounded V3 rounds without changing V2 roots or schemas.

- [x] Write failing tests for V3 identity, finite endpoints, one-policy/one-step behavior, no-op preservation of non-`Wait`, PAUSED versus healthy state, exact simulation UTC, replay digest equality, and all four crash boundaries.
- [x] Verify RED.
- [x] Implement V3 session composition by reusing stable V2 helpers without editing V2 persisted schemas or replay code.
- [x] Ensure replay reconstructs state through public `env.step()` calls and never emits a second logical transfer or Edge lifecycle.
- [x] Run focused V2 and V3 course-session tests and commit `feat(course-session): add replayable v3 execution driver`.

---

### Task 4: Add the simulator-backed Edge task device

**Files:**
- Modify: `simulation/nxt_edge_task/executor.py`
- Create: `simulation/scripts/course_session_task_device.py`
- Create: `simulation/tests/edge_task/test_course_session_task_device.py`
- Modify only for regression coverage: `simulation/tests/edge_task/test_recovery_edge.py`

**Interfaces:**
- Additive `simulator_backed` executor behavior advertises existing configured task types but never auto-advances or auto-succeeds in `RobotCore.tick()`.
- `SimulatorBackedTaskDevice` consumes only durable V3 committed outbox events and persists Edge ACCEPTED/PROGRESS/terminal events through `RobotCore`/`JsonlJournal`.
- Restart continues to use `RobotCore.on_start()`: accepted/not-started becomes FAILED, started/no-terminal becomes INCONCLUSIVE, a new incarnation rejects old authorization.

- [x] Write failing tests proving no Mock path, no terminal without committed simulator evidence, exact PARTIAL/REJECTED/MISSED/FAILED/INCONCLUSIVE mappings, outbox idempotency, protection, and restart/incarnation behavior.
- [x] Verify RED.
- [x] Add the minimal external-committed-event seam to the Edge owner and implement the device adapter at the composition root.
- [x] Prove ordinary chunk replay does not invoke `on_start()` while an actual device restart does, and old authorization never executes.
- [x] Run all Edge tests and commit `feat(edge-task): bridge committed simulator execution evidence`.

---

### Task 5: Compose Planning through the real closed loop

**Files:**
- Create: `simulation/scripts/course_collection_execution_demo.py`
- Create: `simulation/tests/course_monitoring/test_collection_execution_integration.py`
- Modify only if injection is required: `simulation/scripts/pilot_dispatch_demo.py`

**Interfaces:**
- The composition uses one injected simulation clock for `PlanningOperations`, `ScheduleService`, Edge admission/device status, V3 request binding, and session execution.
- Existing Planning confirmation is unchanged. After `TASK_CREATED`, the binder durably creates the V3 request and sends it through the simulator-backed device.
- `--out`, `--initialize`, `--advance`, and `--no-serve` provide a bounded reproducible local SIMULATION runner.

- [x] Write the failing end-to-end test: verified Planning confirmation -> due schedule -> `TASK_CREATED` -> V3 request -> original-policy Wait slot -> actual BallLedger collection -> original-policy matching handoff -> verified Edge terminal -> V3 SUCCEEDED.
- [x] Assert raw and unload are equal, positive, assignment-tagged actual ledger movements; generic handoff target remains null; no Planning outcome, wash, supply, or inventory inference is written.
- [x] Verify RED, implement the smallest composition, then run the test green.
- [x] Add integration cases for schedule-form simulation UTC and independent wall-clock service freshness.
- [x] Commit `feat(collection-execution): run planning tasks in v3 simulation`.

---

### Task 6: Expose strict read-only execution APIs and client methods

**Files:**
- Modify: `simulation/nxt_site_agent/api.py`
- Create: `simulation/tests/site_agent/test_collection_executions_api.py`
- Modify: `apps/site-agent-console/lib/collection-executions.ts`
- Modify: `apps/site-agent-console/tests/collection-executions-contract.test.ts`
- Modify if required: `apps/site-agent-console/tests/boundaries.test.ts`

**Interfaces:**
- Inject callbacks `collection_executions: Callable[[], dict]` and `collection_execution_request: Callable[[str], dict]`; callbacks are readers only and receive no runtime/session/filesystem object.
- GET `/api/v1/collection-executions` and GET `/api/v1/collection-executions/requests/{request_id}` return existing Manager envelopes.
- POST/PUT/PATCH/DELETE on either path return 405; unavailable readers return `collection_execution_unavailable`; unknown IDs return the frozen not-found error.
- TypeScript client exposes `read(signal?)` and `readRequest(requestId, signal?)`, both uncached GET-only methods with strict response parsing.

- [x] Write failing Python and Vitest route/client tests, including percent-decoding, invalid IDs, malformed envelopes, no callback, callback failure, all mutation verbs, and proof a GET cannot tick/advance.
- [x] Verify RED.
- [x] Implement only the injected read routes and strict client method; change no React/CSS/component file.
- [x] Run focused Site Agent and console contract tests and commit `feat(site-agent): expose collection execution evidence`.

---

### Task 7: Freeze all 20 acceptance cases as executable tests

**Files:**
- Create: `simulation/tests/course_monitoring/test_collection_execution_acceptance.py`
- Modify only when runtime-generated evidence requires a compatible clarification: `simulation/docs/contracts/collection-execution-v1/schema.json`
- Modify only in lockstep with schema: `simulation/docs/contracts/collection-execution-v1/examples/*.json`
- Modify only in lockstep with schema: `simulation/tests/pilot_ops/test_collection_execution_wire_contract.py`
- Modify only in lockstep with schema: `apps/site-agent-console/lib/collection-executions.ts`
- Modify only in lockstep with schema: `apps/site-agent-console/tests/collection-executions-contract.test.ts`

**Interfaces:**
- One parametrized acceptance manifest names and asserts cases 01-20 from the accepted architecture.
- Generated snapshots must pass the existing Python relational oracle and the TypeScript parser; fixtures cannot replace runtime evidence in the normal-loop case.

- [x] Add a failing manifest that proves every case number/name is present once.
- [x] Implement and pass cases: normal policy-preserved; no opportunity; pending order; safety rejection; full payload; temporary empty; access boundary; preemption; low battery/fault/e-stop/assistance; timeout; duplicate/unknown; chunk replay; device restart; terminal conflict; quantity integrity; stage separation; clocks; schedule form; bound handoff; crash boundaries.
- [x] Run schema/example/TS parity after any compatible contract adjustment. List every incompatible requirement separately instead of silently changing it.
- [x] Commit `test(collection-execution): cover frozen v3 acceptance cases`.

---

### Task 8: Document reproducibility and verify the backend delivery

**Files:**
- Create: `simulation/docs/collection_execution_v3_runbook.md`
- Modify: `simulation/docs/collection_execution_v1_architecture.md`
- Update this plan's checkboxes as tasks complete.

**Interfaces:**
- The runbook gives exact initialize/resume commands, the explicit no-serve limitation, output paths, expected session/request IDs, normal-loop evidence fields, restart versus replay procedure, and Claude integration contract.
- The architecture status distinguishes implemented backend from React/UI and every physical/live integration that remains unimplemented.

- [x] Run focused suites after each task, then the normative full Python suite and `scripts/validate_configs.py` from `simulation/`.
- [x] Run console `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`, `npm run smoke`, and `npm audit --omit=dev`.
- [x] Run the repository verifier and diff/hygiene checks from `.agent/workflows/testing.md`, `.agent/workflows/review.md`, and `.agent/workflows/hygiene.md`.
- [x] Execute the documented closed loop from a fresh output directory and capture actual IDs, quantities, event IDs/digest, Edge terminal, conservation/payload parity, and replay digest.
- [x] Review against all 20 cases and the user scope. Explicitly list backend implemented items, Claude-owned/unmerged UI work, and unimplemented physical/Planning-outcome/wash/supply paths.
- [x] Commit `docs(collection-execution): document v3 backend operation`; keep the branch local and unmerged.

Task 8 evidence observed by the root agent:

- fresh initialize plus same-root `--advance 0` resume preserved the fixed
  request/binding/execution/attempt identities, 600 raw, 600 unload, verified
  Edge `SUCCEEDED`, conservation/payload parity, event digest and replay digest;
- `tests/course_monitoring/test_collection_execution_acceptance.py`: 21 passed;
- focused console acceptance plus collection-execution contract tests: 2 files,
  341 tests passed;
- full Python suite: 3054 passed; config validation: 0 errors / 0 warnings;
- console: typecheck passed, lint reported 0 errors / 2 warnings, 563 tests
  passed, build and smoke passed, and production audit reported 0
  vulnerabilities; and
- repository policy unit tests: 111 passed; Python package build passed;
- repository verifier: passed across 702 tracked/nonignored paths and 83
  Markdown files; and
- final tracked/untracked inventory and whitespace checks passed with only the
  sixteen current-delivery source/test/document paths present before commit.
