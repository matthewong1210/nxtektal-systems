# Collection Execution V3 backend runbook

Status: implemented on the local, unmerged
`codex/collection-execution-3b` branch.  This is a deterministic
**SIMULATION-only** backend rehearsal.  It is not a physical robot path, live
site integration, production service, or claim of field performance.

The frozen architecture and contract remain in
[`collection_execution_v1_architecture.md`](collection_execution_v1_architecture.md).
This runbook covers the implemented Phase 3B backend and the read-only
handoff to the Manager API and console client.

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

The demo command itself uses `uv run --no-sync` so it cannot silently update
the environment or lock file.

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
wall-clock read time are excluded from the causal identities.

| Field | Expected value |
|---|---|
| `site_id` | `pilot-course-a` |
| `deployment_id` | `pilot-a-edge-task-sim-v0` |
| `series_id` | `collection-execution-series-v3` |
| `session_id` | `collection-execution-session-v3` |
| `round_id` | `collection-execution-round-v3` |
| `request_id` | `collection-demo-execution-request-001` |
| `task_id` | `task_b32398701c03d4a1fb2a0106` |
| `engine_digest` | `6c5b3c8406304a48ef8910a368f7fb7d24c35d2bd00c89c4981b38a400c74131` |
| `plan_id` | `plan-ada810c8a8f069f7fddbcb7d` |
| `binding_id` | `45c1c687bd766535b64471aaaaa16c5bca3be4ec4810e22bee330b8af932b183` |
| `execution_id` | `f6a8ed06ae68ffe36a941abc3b4763b17cd699e74069fac1e5e041366346d7d9` |
| `attempt_id` | `attempt-f6a8ed06ae68ffe36a941abc3b4763b17cd699e74069fac1e5e041366346d7d9` |
| `assignment_id` | `assignment-3bc3c03294671642f4825a8a5bfd619b8c85c16c338a5daf32d3764b6997665d` |
| request digest | `dfa579f944f6db85512ae674976e6b5680e52b9281c5579cdbe61cee7d04e1c6` |
| request high-water digest | `bb8d0ce02097474e90d70ab7431ca291feda44e61d1fc70c1e7d166735719318` |
| `policy_id` | `JointDispatchPolicy-v1` |
| `arbiter_version` | `WAIT_ONLY_NON_PREEMPTIVE_V1` |
| final `state` / `reason` | `SUCCEEDED` / `UNLOADED_ALL_COLLECTED_BALLS` |
| `raw_quantity.balls` | `600` to runtime robot `R1` |
| `unload_quantity.balls` | `600` to bound station `H1` |
| final assignment `event_digest` | `cad049df3f1be7e1f51270bee6ccee298407ae5afc2064ca0722f279bd84e23d` |
| final `replay_digest` | `4783b289d33daf2f3bc09563726d35edd07f82e48e417f2940ec4b32532bfc5d` |

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
`assignment-event-77f04423218f84e1781ed9b1636f968e2e0e744783060b74ecb67a3b94aae827`.
The collection-exit event ID is
`assignment-event-81515e140285cfe0cad8ec409d36c5dd5e19a7c6d3be199a6210a4cd110ad0cc`.
The unload event ID is
`assignment-event-fbc70ad71939471c37a630086834d324209242a2dadd9268ca2e038cbcff0f5d`;
the terminal event ID is
`assignment-event-085580c4f92d8f3eb906839ab5275e98475483c8b64153e30957d2cd8e269903`.
The read projection retains all fifteen raw source event IDs and the six Edge
record IDs rather than collapsing the evidence to the 600-ball summary.
Those Edge record IDs in the observed normal loop are:

```text
rec_4893376da211d23ff1da2cca
rec_7f7f498b52823deadbd22a11
rec_92669c32c41f839f9171f8a9
rec_c3a88a5882f1cfc5c8ef16db
rec_d03d5e98428c32b97e3bfc9d
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

Phase 3B implements the transport seams but deliberately does **not** add a
combined collection-execution server command.  The current bounded runner
requires `--no-serve`; omitting it is a command-line error.  Do not document
`--serve` or claim that a production/live service exists.

A composition root may inject these readers into `SiteAgentApiServer`:

- `collection_executions: Callable[[], dict]`;
- `collection_execution_request: Callable[[str], dict]`; and
- `collection_execution_parser=parse_collection_execution_read_contract`.

The server receives only reader callbacks, not a mutable session, store or
filesystem capability.  The implemented routes are:

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

### Claude/React ownership

React components, CSS, visual states and interaction tests remain
Claude-owned and are not implemented on this backend branch.  The frontend
handoff must preserve these rules:

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
| 17 | Clock separation | Simulation deadlines and wall-clock 15-second health remain independent; shared TS helper coverage exists, but React rendering remains unimplemented. |
| 18 | Schedule form reuse | Date comparison uses simulation UTC while write health remains wall-clock based; shared TS helper coverage exists, but React integration remains unimplemented. |
| 19 | Bound handoff | Zero/multiple/wrong station rejects before acceptance; only correlated unload to `H1` completes the fixture. |
| 20 | Crash boundaries | Before-prepare, prepared-only, committed/cursor-stale and outbox-unconfirmed recovery avoid duplicate lifecycle/movement; unverifiable replay protects as `INCONCLUSIVE`. |

Focused evidence observed while writing this runbook:

```text
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/course_monitoring/test_collection_execution_acceptance.py
21 passed in 11.20s

npm test -- tests/collection-execution-acceptance.test.ts \
  tests/collection-executions-contract.test.ts
2 test files passed; 341 tests passed
```

The root-agent delivery verification subsequently observed:

- full Python suite: 3054 passed in 255.45 seconds;
- `scripts/validate_configs.py`: 0 errors / 0 warnings;
- console typecheck passed;
- console lint: 0 errors / 2 warnings;
- console tests: 563 passed;
- console build and smoke passed;
- `npm audit --omit=dev`: 0 vulnerabilities;
- Python package build passed;
- repository policy unit tests: 111 passed; and
- repository verifier: passed across 702 tracked/nonignored paths and 83
  Markdown files, followed by clean whitespace and tracked/untracked scope
  checks.

## Implemented and still unimplemented

Implemented on this local backend branch:

- V3-only assignment IDs and immutable simulator event evidence;
- ledger-backed raw collection and bound-station unload attribution;
- wait-only arbitration around the original policy and final SafetyShield;
- durable binding/request/receipt, prepare/commit/outbox/cursor protocol,
  replay digest and crash recovery;
- simulator-backed Edge lifecycle/protection and restart mapping;
- the bounded Planning-confirmation-to-verified-Edge-terminal demo;
- strict read-only Manager API injection routes;
- strict TypeScript types, parser, `read()`/`readRequest()` client and
  simulation-clock helper; and
- executable backend coverage for all 20 frozen acceptance cases.

Not implemented:

- any physical robot, live device, hardware/vendor transport, ROS, actuator,
  production publisher, physical task admission or real-site deployment;
- a combined long-running collection-execution server/serve command;
- the Claude-owned React components, CSS, visual states and interaction tests;
- automatic Planning outcome writes;
- wash results, supply results or per-task washed/supplied lineage;
- clean/supply inventory replenishment inferred from raw or unload evidence;
- generic multi-station handoff selection; and
- authority for a browser, LLM or advisory system to execute or clear safety
  protection.
