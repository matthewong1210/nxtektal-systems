# Collection Execution V3 integration runbook

Status: Phase 3B backend and Phase 3C isolated console integration are
implemented on the local, unmerged `codex/collection-execution-3c` branch.
This is a deterministic **SIMULATION-only** rehearsal.  It is not a physical
robot path, live site integration, production service, or claim of field
performance.

The frozen architecture and contract remain in
[`collection_execution_v1_architecture.md`](collection_execution_v1_architecture.md).
This runbook covers the Phase 3B durable execution backend and the Phase 3C
single-process read-only Manager API/console integration.

## Ownership and safety boundary

- `RangeSimulation` owns mutable simulated robot, zone and station state.
- `BallLedger` is the sole authority for transferred ball quantities.
- The unchanged `JointDispatchPolicy-v1` proposes the original action once per
  tick.  `WAIT_ONLY_NON_PREEMPTIVE_V1` may replace only `Wait`.
- `SafetyShield` inside `RangeSimulation.apply_directive()` remains final
  simulator admission.
- The V3 composition roots own binding, durable request/tick evidence,
  deterministic replay and read projection.  They do not become another
  simulator, policy, ledger or physical controller.
- The Edge device is simulator-backed protocol evidence.  It is not
  `MockRobotDevice`, physical robot telemetry or proof that a physical act
  occurred.
- The Manager API and TypeScript client are GET-only readers.  A browser, LLM,
  Site Runtime component or advisory record cannot advance the simulator or
  reach a robot, adapter, ROS, actuator or e-stop API.

## Prerequisite

Provision the locked all-extras environment as required by the repository
testing workflow, then run commands from `simulation/`:

```bash
cd simulation
uv sync --locked --all-extras
```

The commands below use `uv run --no-sync` so they cannot silently update the
environment or lock file.  Build the already-integrated static console once:

```bash
cd ../apps/site-agent-console
npm ci
npm run build
cd ../../simulation
```

## Start the integrated service

The Phase 3C service has one supported scope: one seeded Planning
confirmation and its one V3 execution.  One composition root owns the same V3
session, simulator-backed task device, Edge journal, Planning records and
in-memory transport.  Exactly one background driver advances it; every HTTP
GET is read-only, appends no evidence and never advances beyond the durable
committed prefix.  A verified causal replay may reconstruct that prefix in
memory.

Use a fresh empty evidence directory for the first start:

```bash
SERVICE_ROOT="$(mktemp -d /tmp/nxt-collection-execution-service.XXXXXX)"
uv run --no-sync python -B -m scripts.course_collection_execution_service \
  --out "$SERVICE_ROOT" \
  --initialize \
  --port 8767
```

The command prints its bound URL.  With the shown port, open
`http://127.0.0.1:8767/`.  At the default six-second driver cadence the same
durable session is observable as `PENDING`, then `RUNNING` with positive raw
collection evidence, then `SUCCEEDED` with an equal positive unload quantity.
The API remains online after the driver completes, protects, ends or
fail-stops.  The HTTP surface is started before the driver is armed, so the
initial `PENDING` state is available before any live tick.

To restart the same durable device/session after stopping the process, run the
same command with the same `SERVICE_ROOT` and omit `--initialize`.  This is a
real device-process restart: existing incarnation and unknown-outcome rules
run before any residual outbox reconciliation.  It is not ordinary committed
prefix replay, and an old authorization is never re-executed.

This fixed service explicitly rejects new Planning inputs, plans and
confirmations and new schedules.  It cannot acknowledge a request it has no
execution path to fulfill.  Existing evidence-only Planning outcome recording
and local notification acknowledgement/resolution keep their prior semantics;
neither unlocks a protected device nor starts execution.

Every task-ops snapshot identifies this composition as
`FIXED_V3_EXECUTION` through the versioned
[`service_capabilities`](contracts/pilot-dispatch-v0/service-capabilities/README.md)
block. It independently marks Planning input/plan/confirmation creation and
schedule creation/cancellation `UNAVAILABLE`, while Planning outcome recording
and notification acknowledgement/resolution are `SUPPORTED`. The latter still
requires scheduler health and the notification's own preconditions. Neither
HTTP success, `transport=in_memory`, the `runtime` object nor an omitted field
grants a write. Unsupported routes continue to return deterministic 409 errors;
a fail-stopped service returns 503 for every write while preserving the static
capability declaration on GET.

Business deadlines, schedule lifecycle and execution use projected simulation
UTC.  The console's 15-second read-health expiry and `server_time_utc` use wall
UTC even while simulation is paused.  A driver exception or unexpected
nonterminal session end stops all further advancement, retains durable
evidence and makes disallowed writes fail closed.  If failure occurs after a
V3 tick commit but before matching Edge evidence is durable, the strict
collection GET returns 503 instead of inventing a reconciled result.  Task Ops
remains readable with `scheduler.state=FAILED`; the console retains its last
successful execution snapshot as stale.  The original request-id receipt
remains independently recoverable because it was durable before Edge
acceptance.  Explicit process restart performs the existing device-first
recovery without another live tick.

The saved `nxt-course-ops/v1` panel backed by frozen
`nxt-whole-course-session/v2` evidence is not wired to this V3 runtime.  If
separately configured, it remains an independent read-only observation with
unrelated session identity and must not be presented as this execution's
course view.

## Initialize a fresh deterministic run

`--out` has no default.  `--initialize` requires a new empty directory.  Keep
the captured stdout outside that directory because shell redirection creates a
file before the runner checks that the evidence root is empty.

```bash
DEMO_ROOT="$(mktemp -d /tmp/nxt-collection-execution-v3.XXXXXX)"
INITIAL_OUTPUT="$(mktemp /tmp/nxt-collection-execution-initialize.XXXXXX.json)"
uv run --no-sync python -B -m scripts.course_collection_execution_demo \
  --out "$DEMO_ROOT" \
  --initialize \
  --advance 2 \
  --no-serve > "$INITIAL_OUTPUT"
```

The runner warms the immutable V3 session to the scheduled 08:10 simulation
instant, creates the verified Planning/Edge/binding chain, and advances two
bounded live execution ticks.  It prints one JSON object to stdout.

This optional `jq` view selects the causal evidence without treating stdout as
the durable store:

```bash
jq '{
  receipt: .request_receipt,
  session: {
    series_id: .collection_executions.series_id,
    session_id: .collection_executions.session_id,
    round_id: .collection_executions.round_id,
    now_sim_t_s: .collection_executions.now_sim_t_s,
    simulation_time_utc: .collection_executions.simulation_time_utc,
    replay_digest: .collection_executions.replay_digest
  },
  execution: (.collection_executions.executions[0] | {
    request_id, execution_id, attempt_id, assignment_id, task_id,
    state, stage, reason, actions, raw_quantity, unload_quantity,
    runtime_evidence, edge_evidence
  }),
  planning_outcomes: .planning.outcomes
}' "$INITIAL_OUTPUT"
```

## Resume and verify the durable prefix

There is no `--resume` flag.  Resume uses the same `--out`, omits
`--initialize`, and preserves the existing identity and journals.  Use
`--advance 0` to reconstruct and read the committed prefix without another
live simulation tick:

```bash
RESUME_OUTPUT="$(mktemp /tmp/nxt-collection-execution-resume.XXXXXX.json)"
uv run --no-sync python -B -m scripts.course_collection_execution_demo \
  --out "$DEMO_ROOT" \
  --advance 0 \
  --no-serve > "$RESUME_OUTPUT"
```

Compare the causal identity, quantities and replay digest:

```bash
jq '{
  receipt: (.request_receipt | {
    request_id, binding_id, execution_id, attempt_id
  }),
  snapshot: {
    replay_digest: .collection_executions.replay_digest,
    now_sim_t_s: .collection_executions.now_sim_t_s,
    state: .collection_executions.executions[0].state,
    raw_balls: .collection_executions.executions[0].raw_quantity.balls,
    unload_balls: .collection_executions.executions[0].unload_quantity.balls,
    edge_state: .collection_executions.executions[0].edge_evidence.effective_state
  },
  planning_outcomes: .planning.outcomes
}' "$RESUME_OUTPUT"
```

Do not pass `--initialize` to an existing evidence root.  The runner refuses to
replace that evidence.

## Expected fixed identity and result

The deterministic fixture produces these values.  Paths, process IDs and
wall-clock read time are excluded from the causal identities. The canonical
witness and fixed identities were regenerated on 2026-09-26 after the paused
recovery and lifecycle-precedence fix changed the source-based engine
fingerprint; the fixed 3C policy, actions and 600-ball result remain unchanged.

| Field | Expected value |
|---|---|
| `site_id` | `pilot-course-a` |
| `deployment_id` | `pilot-a-edge-task-sim-v0` |
| `series_id` | `collection-execution-series-v3` |
| `session_id` | `collection-execution-session-v3` |
| `round_id` | `collection-execution-round-v3` |
| `request_id` | `collection-demo-execution-request-001` |
| `task_id` | `task_b32398701c03d4a1fb2a0106` |
| `engine_digest` | `b3e31f4904a3e58713474a87c879be3480f5ece6bc6b5bd6302311baabba0d29` |
| `plan_id` | `plan-ada810c8a8f069f7fddbcb7d` |
| `binding_id` | `ba7a5c30b5e76a4a3ef365269a9de3544bdd063f2c54c2dda300be9eef8922f9` |
| `execution_id` | `b7ca35768dce624e48e16de3768a2f9a49aa42f54eff8239273b09a3ec6d806a` |
| `attempt_id` | `attempt-b7ca35768dce624e48e16de3768a2f9a49aa42f54eff8239273b09a3ec6d806a` |
| `assignment_id` | `assignment-713bdc41fc512419d45f4f684ab86e094166463c5722da3d9091ab288a94e6b5` |
| request digest | `7f17324e747f3f1a49fbfec56fc9b8b246e79182b0caa2bdbbe4b5df54a9cd7b` |
| request high-water digest | `b8a37f7c70cf8856bc2e3a1527c60cb1ec2facda75a5b15d641adfa15d2f6e9e` |
| `policy_id` | `JointDispatchPolicy-v1` |
| `arbiter_version` | `WAIT_ONLY_NON_PREEMPTIVE_V1` |
| final `state` / `reason` | `SUCCEEDED` / `UNLOADED_ALL_COLLECTED_BALLS` |
| `raw_quantity.balls` | `600` to runtime robot `R1` |
| `unload_quantity.balls` | `600` to bound station `H1` |
| final assignment `event_digest` | `b628d26337220e4c96833351b9009f1f91568e44bb419081c419baeece4bede9` |
| final `replay_digest` | `f1bd715bb1143fd8270939aa94171196f08510e44d9695fc665adc8bdadee2ad` |

The contract has no `run_id`, `state_id` or `strategy_id` fields.  Do not
invent aliases for them: the real session identity is the
`series_id`/`session_id`/`round_id` tuple, `state` is an execution status, and
the strategy evidence is the explicit `policy_id` plus `arbiter_version`.
`server_time_utc` is intentionally the wall-clock read time and therefore is
not byte-stable across CLI invocations; compare the causal IDs, quantities and
digests above instead of claiming the complete stdout object is fixed.

The observed causal timeline is:

| Evidence | Observed value |
|---|---|
| Start | sequence 1, `ASSIGNMENT_STARTED` at `29400.0` |
| Raw transfer | sequences 2-16, fifteen ledger moves of 40 balls, total 600 |
| Collection exit | sequence 17, `ROBOT_PAYLOAD_FULL` at `29889.465614749795` |
| Unload | sequence 18, 600 balls from `robot:R1` to `station:H1` at `30118.0336438065` |
| Terminal | sequence 19, `UNLOADED_ALL_COLLECTED_BALLS` |
| Integrity | `event_sequence_complete=true`, `conservation_passed=true`, `payload_parity_passed=true` |
| Edge result | `effective_state=SUCCEEDED`, `result_verification=VERIFIED`, one `SUCCEEDED` terminal |
| Planning results | `planning.outcomes=[]`; no outcome was synthesized |

The start event ID is
`assignment-event-1029d48f558cd223990027787ab58f13561df0c62f1d2de0bded0d902f0635e2`.
The collection-exit event ID is
`assignment-event-e729c6caa1958e7ba86fcca85b9d912475d804eb9ce040b9284bff135ecadccf`.
The unload event ID is
`assignment-event-b2d3229ae19d274d66b405d2e730d4164a8555adc9e7a2e1d006635257085672`;
the terminal event ID is
`assignment-event-9aac5838e82188589ed792d5724c2d932d5ad5dc350eb64f433ea3754bcf53fd`.
The read projection retains all fifteen raw source event IDs and the six Edge
record IDs rather than collapsing the evidence to the 600-ball summary.
Those Edge record IDs in the observed normal loop are:

```text
rec_1c2fa2aed779de9fe9ba5215
rec_4a9d45f9de8bb044c8f9e7c5
rec_4f0e7960fdbb03d02f762db1
rec_6a350192bc19830abdd936de
rec_8f3bb8e191900c2bd926ccc6
rec_fbf9d67c03833c163d455189
```

### Wait-only proof

The normal result contains exactly two execution action decisions:

1. At `29400.0`, the original policy proposed `Wait`.  The arbiter selected
   `AssignCollection(R1, NEAR_LEFT)` with `selection=WAIT_SLOT`; the final
   shield accepted it.
2. At `30000.0`, the original policy proposed the matching generic
   `SendToHandoff(R1, target_id=null)`.  It was selected unchanged with
   `selection=ORIGINAL_POLICY_CONVERGED`; correlated ledger evidence records
   the actual bound destination `H1`.

Thus the external request filled only a `Wait` slot.  It did not replace or
rerank an original non-`Wait` policy action.

## Durable output layout

The initialized root contains:

| Path below `$DEMO_ROOT` | Purpose |
|---|---|
| `demo.json` | SIMULATION demo marker; required for resume |
| `.collection-execution.lock` | outer composition process lock |
| `edge/edge_task_journal.jsonl` | Planning, schedule and Edge-side task evidence |
| `edge/edge_task_journal.jsonl.hwm` | Edge journal high-water anchor |
| `device/picker-01/robot_task_journal.jsonl` | simulator-backed device decisions, progress and terminal evidence |
| `device/picker-01/robot_task_journal.jsonl.hwm` | device journal high-water anchor |
| `session-v3/config.json` | immutable V3 input configuration |
| `session-v3/compiled.json` | deterministic compiled scenario/session inputs |
| `session-v3/identity.json` | frozen series/session/round and digest identity |
| `session-v3/control.json` | explicit session pause control |
| `session-v3/state.json` | disposable published cursor/status; never the journal authority |
| `session-v3/collection-execution.jsonl` | binding, request, receipt, prepare/commit/outbox/cursor and Edge verification evidence |
| `session-v3/collection-execution.jsonl.hwm` | execution journal high-water anchor |
| `session-v3/.session.lock` | V3 session lock |

The `.jsonl` journals and their verified prefixes are durable evidence.  Do
not hand-edit them, reuse a V2 root, or treat `state.json` as a replacement for
the journals.

## Replay is not a device restart

Within one initialized `--advance 2` process, each bounded
`course_session_v3.run()` call reconstructs the committed prefix before the
next live tick.  That ordinary chunk replay does not call device `on_start()`,
emit a second Edge lifecycle or repeat a logical ball move.

Starting the outer demo command again is a real device-process restart.  The
restart order depends on the last durable simulator boundary and never turns a
stale authorization into an ordinary replay:

- `PREPARED_NO_COMMIT`: the device reconciles first and cancels the old
  authorization before any causal simulator replay.  The saved intent is not
  executed after that restart.
- `COMMITTED_CURSOR_STALE`: the driver causally verifies the already committed
  prefix and repairs only the disposable cursor; then the device performs its
  restart reconciliation.  No new prepare, commit or simulator step occurs.
- Other structurally valid states use the normal device restart path.  An
  ordinary in-process chunk replay is never classified as a device restart.
- If restart protection leaves a causally reconstructed simulator assignment
  without a terminal reason, the session refuses every new live tick before
  policy selection, prepare or `env.step()`.  There is no implicit cancellation
  or unowned handoff in V3.

After that ordering is established, the existing Edge restart result remains:

| Durable device state at restart | Required result |
|---|---|
| Completed task | replay the persisted terminal only; no new execution |
| Accepted, not actually started | Edge `FAILED` with `not_started_after_restart`; do not execute |
| Started, no persisted terminal | Edge `INCONCLUSIVE` with `interrupted_execution_unknown_outcome`; do not resume or reauthorize |
| New incarnation or missing/rolled-back device journal | reject old authorization and flag session regression |

The normal completed resume command above preserves the same request,
binding, execution, attempt, quantities and replay digest.

## Read-only API and TypeScript handoff

`scripts.course_collection_execution_service` injects these same-instance
readers into `SiteAgentApiServer`:

- `collection_executions: Callable[[], dict]`;
- `collection_execution_request: Callable[[str], dict]`; and
- `collection_execution_parser=parse_collection_execution_read_contract`.

The API handler receives only callbacks, not a second runtime, device or
journal.  The implemented routes are:

- `GET /api/v1/collection-executions`;
- `GET /api/v1/collection-executions/requests/{request_id}`; and
- `POST`, `PUT`, `PATCH` and `DELETE` on either namespace return 405.

Unknown request IDs retain the frozen not-found error; absent, failed or
malformed readers fail closed as `collection_execution_unavailable`.  A GET
validates the strict contract and cannot tick or advance the simulation.

In `apps/site-agent-console/lib/collection-executions.ts`:

- `createCollectionExecutionsClient(fetch).read(signal?)` performs an
  uncached same-origin GET and strict snapshot parse;
- `readRequest(requestId, signal?)` validates and percent-encodes the exact
  request ID, then strictly parses the recovered receipt; and
- `collectionExecutionSimulationNow(snapshot)` returns the simulation/business
  instant in JavaScript epoch milliseconds.  It may be used as the comparison
  clock for a simulation schedule form; it is not the wall-clock 15-second
  service-health clock.

In `apps/site-agent-console/lib/task-ops.ts`, `parseTaskOps()` validates the
separate service-capability schema and all eight operation values.
`taskOpsCapabilities()`, `taskOpsSupports()` and `canPerformTaskOps()` are the
shared React handoff. They preserve explicit V3 versus legacy mode and combine
an operation only with the existing dynamic health gate; a historical payload
without a declaration becomes `UNDECLARED` with all writes unavailable. Claude
owns the component wiring and must not infer support from transport, HTTP
success, V3 read availability or missing fields.

### Claude/React ownership

The Claude-owned React components, CSS, visual states and interaction tests
are integrated unchanged from the accepted frontend commits.  They preserve
these rules:

- display the backend `state`, `stage`, reasons, quantities, source IDs,
  protection and Edge verification as evidence; do not recompute them;
- use `collectionExecutionSimulationNow()` only for the simulation/business
  date comparison and keep scheduler read health on wall time;
- show `PAUSED` independently from stale/offline service health;
- do not infer authorization or permission from `SUCCEEDED`, `PENDING`, Edge
  availability, a recommendation, Planning confirmation, IDs or the absence of
  a protection reason;
- do not add confirm/start/retry/pause/resume buttons or mutation calls in the
  collection-execution namespace; and
- do not infer washed, supplied, clean-inventory or Planning-outcome facts from
  raw/unloaded quantities.

## Phase 3C loopback service evidence

A final loopback run on 2026-09-24 used one fresh evidence root, one service
process and one 30-second background driver.  Three GETs against that same
process and `collection-execution-session-v3` observed:

| Read | `now_sim_t_s` | State / stage | Raw balls | Unloaded balls |
|---:|---:|---|---:|---:|
| Before first tick | `29400.0` | `PENDING` / `WAITING_FOR_POLICY_SLOT` | `null` | `null` |
| After first tick | `30000.0` | `RUNNING` / `RAW_COLLECTED_TO_ROBOT` | `600` | `0` |
| After second tick | `30600.0` | `SUCCEEDED` / `TERMINAL` | `600` | `600` |

The terminal read reported source `RANGE_SIMULATION_BALL_LEDGER`, reason
`UNLOADED_ALL_COLLECTED_BALLS`, `success_display_allowed=true`, and exactly one
durable request receipt.  It did not report wash, supply, inventory or
Planning outcome completion.  The same process returned a strict
`nxt-pilot-dispatch/v0` task projection with `environment=SIMULATION`,
`transport=in_memory`, `runtime.driver_state=COMPLETED`, the task
`state=SUCCEEDED`, and the device's journal-receipt-based ONLINE observation.
Its `server_time_utc` was wall time; the execution reads above retained the
simulation clock.

The durable execution tree hash, excluding process locks and the independent
Site Agent fixture root, was
`ca0b622a3cf14b2b89eaf28eae5ca48667485dc03ae79634965372260e64d982`
both before and after another collection GET.  That is direct service evidence
that the read appended no execution evidence and did not advance the session.

A browser run against another single-process root visibly followed the same
`PENDING` to `RUNNING` to `SUCCEEDED` sequence.  The panel showed 600 raw / 0
unloaded at the milestone and 600 / 600 at terminal success, with the
simulation-only and read-only labels intact.  The browser reported no warning
or error logs and no framework error overlay.  The independent Whole-course
panel explicitly rendered its unconnected/no-data state instead of presenting
V2 evidence as this V3 session.

The loopback integration tests in
`tests/site_agent/test_collection_execution_service.py` additionally exercise
the actual `SiteAgentApiServer` callbacks for paused simulation with advancing
wall health, unexpected session end, pre-commit and post-commit driver
failure, uncertain Planning/notification evidence writes, real SafetyShield
rejection, human-assistance protection, and a running-device restart that
becomes unknown without re-executing the old authorization.  These are
isolated simulation-service tests, not physical-device evidence.

## Frozen 20-case acceptance matrix

The executable manifest is
`tests/course_monitoring/test_collection_execution_acceptance.py`; the normal
loop is generated from a fresh runtime and compared byte-for-byte with
`tests/course_monitoring/fixtures/collection-execution-normal-loop-v3.json`.
The shared TypeScript parser consumes that runtime witness in
`apps/site-agent-console/tests/collection-execution-acceptance.test.ts`.

| # | Frozen case | Executable evidence and expected result |
|---:|---|---|
| 01 | Normal, policy preserved | Fresh closed loop; `Wait` is replaced once, original handoff is unchanged, 600 raw equals 600 unload, terminal `SUCCEEDED`. |
| 02 | No opportunity | Non-`Wait` policy remains selected through the exclusive boundary; request becomes `MISSED` without assignment or move. |
| 03 | Deterministic pending order | Ordering is `(latest_start, eligible, execution_id)`; running continuation wins; multiple continuations fail closed. |
| 04 | Safety rejection | Final shield rejection produces V3 `REJECTED`, post-acceptance Edge `FAILED`, and no assignment/ledger move. |
| 05 | Full payload then unload | `ROBOT_PAYLOAD_FULL` is only a collection exit; equal bound-station unload is required for success. |
| 06 | Temporary empty zone | Complete zero raw fails; a positive complete partial quantity may unload but remains `PARTIAL`. |
| 07 | Access closes at boundary | No move occurs after the access boundary; prior complete quantity remains partial and may unload. |
| 08 | Policy redirection/preemption | Original non-`Wait` action wins and yields an explicit non-success terminal; no silent reassignment. |
| 09 | Low battery, fault, e-stop, assistance | Exact exit/protection survives; e-stop has no later movement; none maps to success. |
| 10 | Execution timeout | Exact independent deadline is enforced; incomplete quantity remains null and protected. |
| 11 | Duplicate/unknown request | Same content returns the durable receipt once; conflicting content fails; original ID is required for recovery. |
| 12 | Chunk recovery | Prefix reconstruction preserves digest, movement and lifecycle cardinality. |
| 13 | Device restart/incarnation | Existing FAILED/INCONCLUSIVE/incarnation rules hold and old authorization cannot execute again. |
| 14 | Terminal conflict | Effective result becomes protected `INCONCLUSIVE`/conflict even when one terminal says success. |
| 15 | Quantity integrity | Assignment events reconcile with `BallLedger`, conservation and payload parity; missing evidence is null, not zero. |
| 16 | Stage separation | Raw, unload, wash, supply and Planning outcome remain distinct; no downstream inventory is inferred. |
| 17 | Clock separation | Simulation deadlines and wall-clock 15-second health remain independent; the React read-health timer expires on wall time even when simulation is paused. |
| 18 | Schedule form reuse | Date comparison uses the shared V3 simulation UTC while write health remains wall-clock based and fail-closed. |
| 19 | Bound handoff | Zero/multiple/wrong station rejects before acceptance; only correlated unload to `H1` completes the fixture. |
| 20 | Crash boundaries | Before-prepare, prepared-only, committed/cursor-stale and outbox-unconfirmed recovery avoid duplicate lifecycle/movement; unverifiable replay protects as `INCONCLUSIVE`. |

Focused evidence observed while writing this runbook:

```text
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/course_monitoring/test_collection_execution_acceptance.py
21 passed in 12.35s

npm test -- tests/collection-execution-acceptance.test.ts \
  tests/collection-executions-contract.test.ts
2 test files passed; 341 tests passed
```

Final Phase 3C delivery verification observed:

- service integration tests: 14 passed in 63.79 seconds;
- frozen Python acceptance manifest: 21 passed in 12.35 seconds;
- normative architecture/safety subset: 210 passed in 6.89 seconds;
- full Python suite: 3069 passed in 320.53 seconds;
- `scripts/validate_configs.py`: 0 errors / 0 warnings;
- console typecheck passed;
- console lint: 0 errors / 2 existing unused-parameter warnings;
- console tests: 24 files / 607 tests passed;
- focused console contract tests: 2 files / 341 tests passed;
- console production build and loopback HTTP smoke passed;
- real browser check passed through `PENDING`, `RUNNING` and `SUCCEEDED` with
  no browser warning/error logs or framework error overlay;
- `npm ci` audited 392 packages and reported 0 vulnerabilities; the separate
  required `npm audit --omit=dev` command was not run because permission to
  contact the external registry audit service was denied;
- Python source distribution and wheel build passed;
- repository policy unit tests: 111 passed; and
- repository verifier: passed across 712 tracked/nonignored paths and 84
  Markdown files.

## Implemented and still unimplemented

Implemented on this local integration branch:

- V3-only assignment IDs and immutable simulator event evidence;
- ledger-backed raw collection and bound-station unload attribution;
- wait-only arbitration around the original policy and final SafetyShield;
- durable binding/request/receipt, prepare/commit/outbox/cursor protocol,
  replay digest and crash recovery;
- simulator-backed Edge lifecycle/protection and restart mapping;
- the bounded Planning-confirmation-to-verified-Edge-terminal demo;
- the single-runtime service composition, sole background driver and strict
  `nxt-pilot-dispatch/v0` projection;
- strict read-only Manager API injection routes;
- strict TypeScript types, parser, `read()`/`readRequest()` client and
- simulation-clock helper;
- Claude-owned React/CSS presentation, independent 15-second wall-clock read
  health and component/interaction tests; and
- executable backend coverage for all 20 frozen acceptance cases.

Not implemented:

- any physical robot, live device, hardware/vendor transport, ROS, actuator,
  production publisher, physical task admission or real-site deployment;
- arbitrary new confirmations or schedules in the fixed Phase 3C service;
- a Course Ops projection bound to the V3 execution session;
- automatic Planning outcome writes;
- wash results, supply results or per-task washed/supplied lineage;
- clean/supply inventory replenishment inferred from raw or unload evidence;
- generic multi-station handoff selection; and
- authority for a browser, LLM or advisory system to execute or clear safety
  protection.
