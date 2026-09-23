# Collection execution v1 architecture

> **DESIGN ONLY — NOT IMPLEMENTED.** This document freezes the Phase 3A
> architecture and shared contract for review.  It does not add a session
> driver, simulator-backed task device, Manager API route, or executable
> collection path.  Those changes belong to Phase 3B after this design is
> accepted.

Base: `ffb0aa623c2e7093398b1d42a493c16411a61a70`.
Design branch: `codex/collection-execution-3a`.
Environment: `SIMULATION` only.

## Architecture review decision

Decision: **Proceed for the Phase 3A contract; implementation remains gated
for Phase 3B.** The design reuses existing fact and execution owners and adds
no package, physical command bridge, policy engine, or mutable facility truth.

The rejected shortcuts are:

1. Joining current logs by robot, zone, or timestamps.  Existing collection
   events have no task identity and cannot support causal attribution.
2. Treating `MockRobotDevice` `SUCCEEDED` as execution.  The mock has no
   physics, ball counts, positions, or ledger access.
3. Starting a separate one-task replay.  That result would be counterfactual,
   not an execution in the selected whole-course session.
4. Reusing or rewriting a V2 experiment root.  V2 inputs and replay digests are
   frozen evidence and remain readable without migration.

The approved direction is a new V3 execution session and a
simulator-backed Edge task device.  The device is a composition adapter; it
does not become a second simulation, scheduler, safety gate, or ball ledger.

## Scope and first closed loop

One confirmed planning task may produce one bounded V3 execution attempt in
one explicitly bound session and round.  The first execution milestone is
`RAW_COLLECTED_TO_ROBOT`: actual simulated balls have moved from the assigned
zone into the assigned robot through `BallLedger.move()`.

That milestone is **not task success**.  Overall success additionally requires
all balls attributed to the attempt to move from that robot into the bound
handoff station.  Washing and supply-ready inventory remain later independent
stages:

```text
zone --RAW_COLLECTED_TO_ROBOT--> robot
robot --UNLOADED_TO_STATION----> handoff station       Phase 3 first closed loop
station --WASHED---------------> washer/dispenser      later, separately evidenced
dispenser --SUPPLIED-----------> available inventory   later, separately evidenced
```

Raw collection must never be copied into unloaded, washed, supplied, clean
inventory, or a Planning v1 outcome.  Mixed washer batches do not currently
carry per-task lineage, so their attribution remains unknown.

## Existing owners remain authoritative

| Fact or behavior | Owner | V3 use |
|---|---|---|
| Planning inputs, plan versions, confirmation and human outcome evidence | `nxt_pilot_ops` | Reuse unchanged Planning v1 records and confirmation workflow. |
| Dated schedule, current device admission and Edge task lifecycle | `nxt_edge_task` / `ScheduleService` | Reuse unchanged Edge v1 admission and task identity with the V3 simulated-runtime clock. |
| Cross-owner verification | `PlanningOperations` composition root | Verify confirmation → frozen schedule → exact `TASK_CREATED` before making an execution request. |
| Mutable simulated robot/zone/station state | `RangeSimulation` | Remains the only live simulation truth. |
| Conserved ball location and count | `BallLedger` | Sole quantity authority. |
| Action vocabulary | `ActionCatalog` | External work selects an existing action by deterministic name/index. |
| Final simulator admission | `SafetyShield` through `RangeSimulation.apply_directive()` | Cannot be bypassed by the task device, API, UI, or planning evidence. |
| Session advancement and deterministic prefix replay | the single whole-course session driver | The only component allowed to call `RangeOpsEnv.step()`. |
| Cross-owner binding, durable request input, arbitration evidence and result projection | new `simulation/scripts/` composition code | Composition only; no new package or truth store. |
| Manager API transport | `nxt_site_agent.api` | Future injected read-only callback; no simulator import. |
| Browser presentation | `apps/site-agent-console` | Strictly parse and display; never calculate admission or quantities. |

No LLM, advice engine, browser component, Site Runtime component, or task
transport may call `apply_directive()`, `RobotTaskInterface`, an adapter, ROS,
an actuator, or e-stop API.  This contract is not a physical execution bridge.

## Version and compatibility boundary

- Existing `nxt-whole-course-session/v2` roots, reports, cursors, paused
  experiments, actions, events and replay digests are unchanged.
- Execution requires a new `nxt-whole-course-session/v3` root.  A V2 root is
  never upgraded in place and cannot accept an execution request.
- V3 begins in a new experiment directory with its own immutable config,
  binding manifest, request journal and cursor.
- Planning v1, Edge Task v1, schedule v2 and Course Ops v1 bytes and meanings
  remain unchanged.
- Collection execution uses the shared contracts in
  `simulation/docs/contracts/collection-execution-v1/`.  Unknown versions fail
  closed.
- A V3 report may later produce a separate Course Ops version, but this
  contract does not change the saved Course Ops v1 projection.

## Identity and binding

The following namespaces remain distinct and are all required:

- Planning: `plan_id`, `plan_version`, `confirmation_id`.
- Edge: `schedule_id`, `task_id` (`task_` plus 24 lowercase hex), the separate
  64-hex `task_content_digest`, target
  `incarnation`, Edge `robot_id` and commissioned `zone_id`.
- Session: `series_id`, `session_id`, `round_id`, `round_index`,
  `engine_digest`, `config_digest`.
- Runtime: `runtime_robot_id`, `runtime_zone_id`, `handoff_station_id` and a
  simulator-owned `assignment_id`.
- Bridge: caller `request_id`, content-derived `binding_id` and
  content-derived `execution_id`.

The immutable binding manifest explicitly maps the two existing identity
spaces, for example `picker-01/Z1` to `R1/NEAR_LEFT`.  That example mapping is
a labelled synthetic fixture, not a physical site fact or inferred alias.  A
binding includes both source digests and the exact session/round; it is invalid
for another round even if names happen to match.  V1 records
`handoff_binding_mode=SOLE_SCENARIO_STATION_V1`; the scenario must have exactly
one station and its ID must equal `handoff_station_id`.

Planning v1 is not given a new `binding_id` or session field.  Its existing
confirmation still freezes the schedule, and `PlanningOperations` still proves
`confirmation -> schedule -> TASK_CREATED`.  Only after that exact Edge task
exists does the V3 composition root create the immutable downstream binding
that adds session, round and runtime identities.  The task device may not emit
`ACCEPTED` until it can verify that full chain and durably record the execution
request.  Thus the existing confirmation path is reused and session-bound
before execution acceptance without pretending that an old confirmation
record contains a future bridge identifier.

The binding also freezes `bound_at_sim_t_s` and its exact derived
`bound_at_utc = session_epoch_utc + bound_at_sim_t_s`.  This time is no earlier
than the verified `TASK_CREATED` evidence and precedes the execution request.
Cycle evidence must satisfy
`observed_at_utc <= bound_at_utc <= valid_until_utc`; checking freshness only at
the session epoch or later request read is nonconforming.

`binding_id` is the SHA-256 digest of the canonical binding body.
`execution_id` is derived from the exact Edge task, V3 session/round and
binding.  Paths, process IDs and wall-clock receipt times are excluded from
both identities.  Missing, stale, duplicated or conflicting identities reject
the request before any action is selected.

The execution start gate requires the runtime robot to be the mapped robot,
idle, in the bound session and round, with zero pre-existing payload.  The
empty-payload condition is an attribution prerequisite: otherwise a later
unload cannot prove which balls belong to this task.  It is not a replacement
for Edge admission or `SafetyShield`.

## Clocks and health

Two clocks have separate owners and must not be substituted:

### Simulation/business clock

The V3 session freezes `session_epoch_utc`.  Behavioral UTC is exactly:

```text
session_epoch_utc + sim_t_s
```

Schedule `due_at_utc` and `expires_at_utc` are converted once to
`eligible_sim_t_s` and `latest_start_sim_t_s`, recorded in the durable request,
and never rebased on restart.  A request may start only while
`eligible_sim_t_s <= now_sim_t_s < latest_start_sim_t_s`.

Task expiry is only the latest-start boundary.  It is not the execution stop
time.  Site timezone is input/display context and does not change the mapping.

### Wall clock

The existing browser scheduler-health reading remains fresh for 15 wall-clock
seconds.  `Date.now()` continues to own that read-health expiry.  HTTP
generated/read times and process wall-time chunk budgets cannot change task
ordering, policy choice, simulator events or results.

A fresh scheduler-service reading and a `PAUSED` simulation are different
facts.  A paused session may have a healthy service connection while its
simulation clock and execution remain stopped.  The future UI must show both
states.

`PlanningOperations`, `ScheduleService`, the Edge gateway and the
simulator-backed device all receive the same projected simulation UTC.  Edge
heartbeat receipt age is therefore simulated-runtime freshness: advancing wall
time while the session is paused cannot age, admit or miss an Edge task;
advancing simulation time can.  This preserves the existing one-clock
`ScheduleService` contract and does not mix wall timestamps into its journal.
The separate 15-second browser/service-read health below remains wall-clock
freshness and may become stale independently while Edge/session state stays
`PAUSED`.

The existing manual schedule form currently rejects dates against wall time.
The confirmed execution path continues through Planning v1 and its bound
schedule; it adds no browser execution POST.  If the legacy schedule form is
reused for a V3 fixture, its injected comparison clock must be the fresh
server-projected simulation UTC.  The 15-second health check must still use
wall time.  Mixing these clocks is a contract failure.

## Explicit execution limit

Every binding has a required positive integer `max_execution_s`.  It has no
default and is not derived from schedule expiry.  V1 derives it from the exact
confirmed Planning input revision for the selected robot/zone:

```text
closed_loop_minutes = travel + collect + return + unload
max_execution_s = ceil(closed_loop_minutes * 60 / control_interval_s)
                  * control_interval_s
```

The four values come from the retained `cycle_minutes` evidence; wash and
supply are deliberately excluded.  The binding records the input record ID,
evidence `source_kind`, `source_ref`, observation/validity times, the four
values and the derivation rule.  Missing or stale cycle evidence blocks the
binding instead of receiving a fallback.  In the frozen success example the
four values are `2 + 5 + 2 + 2` minutes, the control interval is 60 seconds,
and `max_execution_s` is exactly 660.

At actual start:

```text
execution_deadline_sim_t_s = started_sim_t_s + max_execution_s
```

The attempt may start only if that deadline is within `session_end_sim_t_s`.
Otherwise it becomes `MISSED` with `INSUFFICIENT_SESSION_HORIZON` without an
action.  Phase 3B must make the simulator-owned bounded assignment terminate at
the deadline and emit terminal evidence; merely stopping attribution while the
robot continues is not conforming.  No new task may use that device while a
late/orphaned runtime activity or unresolved protection remains.

## Wait-only non-preemptive arbitration v1

`WAIT_ONLY_NON_PREEMPTIVE_V1` preserves the original policy as follows:

1. `JointDispatchPolicy.act()` is called exactly once on its original visible
   inputs at every control tick.
2. The original proposal and selected action are both persisted.
3. A pending execution action may replace only the original `Wait` action.
   It never replaces a non-`Wait` proposal.
4. V1 permits multiple `PENDING` requests but holds one execution lease per
   session/round, so at most one attempt is `RUNNING`.  A running attempt that
   needs its next bounded directive has priority over every new start when the
   original proposal is `Wait`; that evidence uses selection
   `RUNNING_CONTINUATION` and is bounded by the execution deadline, not the
   already-consumed latest-start boundary.
5. Only when there is no running continuation candidate are eligible pending
   starts ordered by `(latest_start_sim_t_s, eligible_sim_t_s, execution_id)`.
   Every candidate must satisfy
   `eligible_sim_t_s <= now_sim_t_s < latest_start_sim_t_s`; only the first can
   consume that `Wait` slot, recorded as selection `WAIT_SLOT`.
6. When `now_sim_t_s >= latest_start_sim_t_s`, a request that never received a
   slot becomes terminal `MISSED`; no directive is issued later.
7. While an execution runs, original policy actions for other robots or staff
   remain selected unchanged.  The simulator may continue the collection
   process concurrently during the step.
8. An original policy action that exactly advances this execution is selected
   unchanged and recorded as `ORIGINAL_POLICY_CONVERGED`.
9. Existing `ActionCatalog` handoff entries decode to
   `SendToHandoff(robot_id, station_id=None)`; an action name or index therefore
   does not prove a station.  Collection Execution v1 accepts only a scenario
   with exactly one handoff station, and the binding must name that station.
   Any zero/multiple-station topology or mismatched ID rejects before Edge
   `ACCEPTED`.  The action evidence keeps a null target; the correlated
   `UNLOADED`/ledger evidence records the actual destination.  A later
   multi-station design requires a versioned station-specific action contract.
10. A different original non-`Wait` action for the leased robot remains the
   selected action.  It explicitly preempts the execution.  The execution
   becomes `PARTIAL`, `FAILED`, or `INCONCLUSIVE` according to complete
   evidence; it is never silently reassigned.
11. If the original proposal is `Wait` and the bounded task needs its next
   collection/handoff action, the execution proposal may fill that slot.
12. The selected action is still decoded by `ActionCatalog`, passed once to
    `RangeOpsEnv.step()`, and checked by `SafetyShield` inside
    `apply_directive()`.  A mask or earlier admission check is not the final
    decision.

This is intentionally not a generic multi-policy resolver and does not change
the original policy's ranking.  Any future mode that can displace a non-`Wait`
proposal requires a new version and architecture review.

## State, milestones and terminal semantics

`PENDING` and `RUNNING` are non-terminal.  Terminal states are `SUCCEEDED`,
`PARTIAL`, `REJECTED`, `MISSED`, `FAILED` and `INCONCLUSIVE`.

The lifecycle stages are:

```text
WAITING_FOR_POLICY_SLOT
  -> TRAVEL_TO_COLLECTION
  -> COLLECTING
  -> RAW_COLLECTED_TO_ROBOT
  -> TRAVEL_TO_UNLOAD
  -> UNLOADING
  -> UNLOADED_TO_STATION
  -> TERMINAL
```

An evidence quantity has one of three states:

- `NOT_REACHED`: the milestone did not occur; `balls` is null.
- `COMPLETE`: the exact ledger-backed quantity is known; `balls` is a
  non-negative integer and source event references are complete.
- `INCOMPLETE`: evidence has a gap or conflict; `balls` is null, never zero.

Zero is allowed only with complete evidence proving no transfer.  An absent,
truncated or conflicting event sequence cannot be normalized to zero.

Overall `SUCCEEDED` is allowed only when all of these are true:

1. start admission and the first collection assignment were accepted;
2. raw collection evidence is `COMPLETE` and `raw_collected_balls > 0`;
3. unload evidence is `COMPLETE` and
   `unloaded_balls == raw_collected_balls`;
4. the unload moved those task-attributed balls into the bound station;
5. the terminal time is no later than `execution_deadline_sim_t_s`;
6. ledger conservation and robot-payload/ledger parity pass; and
7. no missing event, terminal conflict, session/round drift, incarnation
   mismatch, or unresolved device protection exists.

The sole success reason in v1 is `UNLOADED_ALL_COLLECTED_BALLS`.

Collection exit reasons are explicit and do not alone imply task success:

| Exit reason | Collection meaning | Overall handling |
|---|---|---|
| `ROBOT_PAYLOAD_FULL` | Normal finite raw-collection boundary | Continue to handoff; success only after equal unload. |
| `ZONE_EMPTY` | Zone is empty at the checked simulation instant | If positive raw quantity exists, attempt unload and normally finish `PARTIAL`; zero complete quantity is `FAILED`. |
| `COLLECTION_ACCESS_BLOCKED` | Access/closure check failed before another move | Positive known quantity may be unloaded but overall result is `PARTIAL`; otherwise `FAILED`. |
| `POLICY_PREEMPTED` | Original non-`Wait` action selected for the leased robot | Apply original action and terminalize explicitly; never claim success. |
| `LOW_BATTERY` | Runtime reached its collection battery floor | Attempt only actions selected by arbitration/SafetyShield; result is partial/failed unless the full success condition was already met. |
| `ROBOT_FAULT` | Runtime robot failed | Protect device; positive complete quantity is partial, zero complete is failed, incomplete evidence is inconclusive. |
| `ESTOP_LATCHED` | Simulator e-stop is latched | No motion follows e-stop; protect device and require existing external reset semantics. |
| `HUMAN_ASSISTANCE_REQUIRED` | Existing policy/runtime requires a person | Terminalize partial/failed/inconclusive and retain protection; it is not browser-resumable. |
| `EXECUTION_TIMEOUT` | Explicit execution deadline reached | Same evidence rule; never synthesize remaining collection or unload. |
| `SESSION_ENDED` | Session ended unexpectedly despite start-horizon gate | Same evidence rule and fail closed. |

If collection ends with a positive complete quantity for a non-success reason,
the driver may still unload within the same deadline when the original policy
or a `Wait` slot permits it.  Completing that unload proves where the partial
quantity went, but the overall status remains `PARTIAL` because the collection
stage ended abnormally.

## Result attribution

Phase 3B must give every accepted bounded collection assignment a stable
simulator-owned `assignment_id` and propagate it through V3-only collection and
unload events.  V2 event bytes remain unchanged.

For one attempt:

- raw quantity is the sum of `COLLECT_CYCLE.balls` with that assignment ID;
- every value is the actual return from `BallLedger.move(zone, robot, n)`;
- unload quantity is the sum of task-correlated `UNLOADED.balls` from that
  robot to the bound station;
- the task starts with zero robot payload, so unload attribution cannot include
  older work;
- event range, event digest, assignment terminal and ledger conservation are
  retained as evidence.

`DIRECTIVE_APPLIED`, travel events, `COLLECTION_DONE`, global metric deltas,
final inventory deltas, Course Ops snapshot differences and Edge v1 task
terminal messages are insufficient quantity evidence by themselves.

The read projection may show simulator evidence alongside Planning outcomes,
but it must not overwrite, average, deduplicate, prefer or reconcile them.
Planning v1 outcomes remain `MEASURED` or `MANUAL_ESTIMATE`; no automatic
outcome is created.

## Durable request and replay

The simulator-backed task device accepts only an exact, verified Edge
`TaskRequest/v1` that is linked through `PlanningOperations` to the current
confirmation and bound schedule.  Before publishing Edge `ACCEPTED`, it must
durably append the collection execution request under the V3 session lock.

The request log uses canonical JSON, continuous sequence numbers, content
digests, fsync and a high-water anchor.  As with the existing Edge journal, it
does not claim protection against a consistent rollback of both file and
anchor.

Idempotency rules are:

- same `request_id`, kind and canonical body: return the original receipt;
- same `request_id` with different kind or content: conflict;
- different request ID for the same Edge task/session/binding: return the
  original execution receipt without creating an alias or another attempt;
- one `execution_id` has at most one `attempt_id` in v1; there are no hidden
  retries;
- an unknown append/response result is `collection_execution_result_unknown`;
  the caller queries or retries the original request ID;
- no action occurs until the durable request can be read back and verified.

V3 restart reconstructs the simulation from immutable base inputs, then
replays the exact execution-request prefix and action decisions to the cursor.
The replay digest includes the binding, request-log high-water identity,
policy identity, arbiter version, original proposal, selected action,
assignment events and terminal evidence.  A mismatch fails closed.

### Per-tick commit and cross-process handoff

The session driver owns a V3 append-only tick journal under the same session
lock.  For each control tick it must use this order:

1. append and fsync one `action_prepared` record containing the previous
   committed cursor and digest, request-log high water, policy/arbiter
   identities, original and selected actions, and any execution/assignment
   identity;
2. call `RangeOpsEnv.step()` once in that live process;
3. collect the resulting simulator events and state digest, then append and
   fsync the matching `action_committed` record, including a deterministic
   Edge-event outbox; and
4. publish the disposable state/report cursor last.

The simulator-backed device never calls the environment.  It consumes only a
committed outbox entry, persists the exact Edge event in its existing device
journal, and then publishes it.  Stable `(execution_id, tick_sequence,
event_kind)` identity makes redelivery byte-identical.

The crash decision table is fixed:

| Last durable boundary | Recovery |
|---|---|
| Before `action_prepared` | No action occurred; recompute the tick. |
| Intent fsynced, no commit/outbox | Rebuild the committed prefix, apply that exact intent, verify its deterministic post-digest, then commit; if verification is impossible or differs, mark `INCONCLUSIVE` and protect the device. |
| Commit fsynced, cursor absent/stale | Rebuild through the commit and repair only the disposable cursor; do not execute again. |
| Commit fsynced, Edge event not confirmed | Device republishes the same persisted Edge event bytes/sequence; no new attempt or simulator action. |
| Edge evidence without a matching committed tick | Treat as an evidence conflict: `INCONCLUSIVE`, authorization blocked. |

No Edge progress or terminal may be published from a prepared record alone.
Before replaying an uncommitted prepared action, recovery re-verifies the same
incarnation and nonterminal Edge authorization.  A real device-process restart
still follows the existing incarnation/restart rules below and cancels that
replay; deterministic driver recovery cannot downgrade the restart to ordinary
chunk recovery or reauthorize its old task.

Normal chunk replay is not a device restart: it emits no new Edge request,
acceptance, progress or terminal and performs no second logical execution.

## Edge v1 mapping and device protection

The Edge contract remains a transport/task lifecycle.  It does not acquire
ball quantity semantics.

| V3 fact | Edge v1 projection |
|---|---|
| Verified `TASK_CREATED`, device has not durably accepted | `CREATED` |
| Durable V3 request, waiting for policy slot | `ACCEPTED`; V3 remains `PENDING` |
| Actual admitted runtime assignment | `PROGRESS phase=collecting`; Edge becomes `RUNNING` |
| `RAW_COLLECTED_TO_ROBOT` | `PROGRESS phase=raw_collected`; still `RUNNING`, no quantity in Edge event |
| travelling/unloading | `PROGRESS phase=returning` / `unloading`; still `RUNNING` |
| V3 `SUCCEEDED` | Edge `SUCCEEDED` only after complete equal unload evidence |
| V3 `FAILED` | Edge `FAILED` |
| V3 `PARTIAL` with complete evidence | Edge `FAILED` with normalized v1 reason `unknown:partial_execution`; never `SUCCEEDED` |
| V3 `INCONCLUSIVE` or unknown result | Edge `INCONCLUSIVE` |
| V3 `REJECTED`, or a miss detected before Edge acceptance | Edge `REJECTED` with the preserved reason |
| V3 `REJECTED` after Edge `ACCEPTED` (for example final `SafetyShield` rejection) | Edge `FAILED` with normalized v1 reason `unknown:safety_rejected`; the exact shield reason remains in V3 evidence, and Edge v1 does not permit `ACCEPTED -> REJECTED` |
| V3 `MISSED` after Edge `ACCEPTED` while waiting for a policy slot | Edge `FAILED` with normalized reason `unknown:policy_slot_missed`; Edge v1 does not permit `ACCEPTED -> REJECTED` |
| Conflicting Edge terminals/replay | Effective V3 projection becomes `INCONCLUSIVE`; authorization stays blocked and success is not displayed |

The GET projection retains both the V3 result and Edge verification fields so
lossy mapping remains visible.  It does not derive V3 quantities from Edge
progress or terminal events.

The simulator-backed device reports Edge status from the current bound runtime
observation.  It may report `available` only when the session is active, the
mapped robot is idle, empty, not paused, not faulted, not awaiting a human and
not e-stopped.  Session pause reports `paused` while transport can remain
ONLINE.  Fault, e-stop, result conflict, incarnation regression, late/orphaned
runtime activity and restart-unknown outcome keep the device protected; a
successful HTTP read or session replay does not clear them.

| Recovery event | Required behavior |
|---|---|
| Ordinary V3 chunk replay, same session/round/incarnation | Recompute the same prefix and digest; emit no new Edge evidence and do not execute again. |
| Duplicate Edge task/request | Replay the durable request/receipt and existing Edge history; no new attempt. |
| Device process restart, same incarnation, completed task | Replay the persisted terminal only. |
| Device restart after acceptance but before actual start | Existing Edge rule: `FAILED` (`not_started_after_restart`); no execution. |
| Device restart after actual start without persisted terminal | Existing Edge rule: `INCONCLUSIVE` (`interrupted_execution_unknown_outcome`); do not resume or reauthorize old work. |
| New incarnation or lost/rolled-back device journal | Reject old target authorization, flag session regression and require new explicit authorization. |
| Terminal conflict or conflicting replay | Preserve all evidence, set effective V3 status to `INCONCLUSIVE` with Edge verification resolution `CONFLICT`, block authorization; never choose a winning success. |

## Shared wire contract and Manager API

`simulation/docs/contracts/collection-execution-v1/schema.json` owns these wire
shapes:

- `nxt-collection-execution-binding/v1`;
- `nxt-collection-execution-request/v1`;
- `nxt-collection-execution-request-receipt/v1`;
- `nxt-collection-executions/v1` read snapshot;
- existing Manager API success/error envelope examples.

The future Manager API surface is read-only:

- `GET /api/v1/collection-executions`;
- `GET /api/v1/collection-executions/requests/{request_id}` for recovery;
- every POST/PUT/PATCH/DELETE in that namespace returns 405.

No browser endpoint creates, retries or controls execution.  Planning
confirmation and due-time task admission continue through the existing
Planning v1/Edge route.  The downstream V3 binding then attaches the verified
confirmation/schedule/task chain to one session and round before the task
device can accept or execute it; Planning v1 does not name that later binding.

The TypeScript client is strict, same-origin, `cache: "no-store"`, GET-only and
uses the existing Manager API envelope.  It rejects unknown fields, foreign
environments, invalid clocks, duplicate identities, cross-linked records,
illegal status/milestone combinations and terminal conflicts presented as
success.

## File ownership

### Phase 3A — this delivery

Codex owns and may change only the shared design/contract surfaces:

- `simulation/docs/collection_execution_v1_architecture.md`;
- `simulation/docs/contracts/collection-execution-v1/**`;
- `simulation/tests/pilot_ops/test_collection_execution_wire_contract.py`;
- `apps/site-agent-console/lib/collection-executions.ts`;
- `apps/site-agent-console/tests/collection-executions-contract.test.ts`;
- the route allowlist assertion in
  `apps/site-agent-console/tests/boundaries.test.ts`;
- this phase's implementation plan.

No React component, CSS, interaction test, API server route, session driver,
simulator, policy, Edge package or experiment is changed in Phase 3A.

### Phase 3B — planned, not implemented

Codex backend ownership:

- V3-only assignment/terminal evidence in `nxt_range_ops`;
- `simulation/scripts/course_collection_execution.py` for binding, persistence,
  idempotency, arbitration and projection;
- `simulation/scripts/course_session_task_device.py` for the simulator-backed
  Edge device;
- V3 session/run/series composition changes under `simulation/scripts/`;
- injected read-only transport in `nxt_site_agent.api`;
- Python behavioral, replay, safety, conservation and API tests.

Claude frontend ownership after contract freeze:

- React execution components;
- CSS and visual states;
- component and interaction tests.

Codex continues to own schema, examples, TypeScript wire types/parser/client
and contract tests.  The two owners do not edit the same file concurrently.

## Phase 3B acceptance cases frozen by this design

1. **Normal, policy preserved:** original policy returns `Wait` for collection,
   so the execution action fills the slot; later original policy itself
   proposes the matching handoff and is selected unchanged.  Correlated raw
   and unload quantities are equal and positive; terminal is `SUCCEEDED`.
2. **No opportunity:** original policy remains non-`Wait` through every
   eligible tick.  No external action is selected and the request becomes
   `MISSED` at the exclusive latest-start boundary.
3. **Deterministic pending order:** multiple eligible requests are selected by
   `(latest_start, eligible, execution_id)` independent of arrival/container
   order.  A running continuation beats every new start, and the single-running
   lease makes multiple continuation candidates invalid.
4. **Safety rejection:** Edge admission may exist, but current SafetyShield
   rejection produces V3 `REJECTED` and post-acceptance Edge `FAILED`, with no
   assignment start and no ledger move.
5. **Full payload then unload:** `ROBOT_PAYLOAD_FULL` ends collection; only the
   later equal station unload makes the overall task successful.
6. **Temporary empty zone:** zero complete raw quantity fails; positive
   complete quantity may be unloaded but remains partial.
7. **Access closes at boundary:** no ball moves after the exact access check;
   any prior known quantity remains partial and may be unloaded.
8. **Policy preemption:** a different original action for the leased robot wins
   and produces an explicit partial/failed terminal; no silent reassignment.
9. **Low battery, fault, e-stop, assistance:** exact reason and device
   protection remain visible; no post-e-stop motion and no success mapping.
10. **Execution timeout:** exact deadline is enforced independently from task
    expiry; unknown evidence stays null and protected.
11. **Duplicate/unknown request:** same content executes once; different
    content conflicts; unknown response is recovered only with the original ID.
12. **Chunk recovery:** deterministic prefix replay produces the same digest
    and no second Edge lifecycle or ball movement.
13. **Device restart/incarnation:** existing Edge failure/inconclusive and
    incarnation rules are preserved; old authorization never re-executes.
14. **Terminal conflict:** effective display is conflict/inconclusive even if
    one terminal said success; new authorization is blocked.
15. **Quantity integrity:** assignment events reconcile with BallLedger,
    robot-payload parity and conservation.  Missing evidence yields null, not
    zero.
16. **Stage separation:** raw, unloaded, washed, supplied and Planning outcome
    facts remain distinct and no downstream inventory is inferred.
17. **Clock separation:** business deadlines use simulation time; the browser's
    15-second service freshness uses wall time; PAUSED and stale/offline are
    independently rendered.
18. **Schedule form reuse:** a simulated date is checked against the bound
    simulation UTC, not the browser wall date, while write health still needs a
    fresh wall-clock scheduler reading.
19. **Bound handoff:** a zero/multiple-station scenario or wrong station ID is
    rejected before acceptance.  In the one-station V1 topology, only
    correlated unload evidence naming that bound station completes the task.
20. **Crash boundaries:** every intent/step/commit/outbox boundary follows the
    fixed recovery table; replay never emits a second lifecycle or logical ball
    move, and unverifiable prefixes become protected `INCONCLUSIVE`.

## Explicitly unimplemented after Phase 3A

- V3 session format, root creation and replay engine;
- bounded simulator assignment IDs and terminal events;
- simulator-backed Edge task device and runtime-derived RobotStatus;
- request journal, arbiter and result projector;
- Manager API collection-executions routes;
- React component, styling and interaction behavior;
- automatic Planning outcome writes;
- washed/supplied per-task lineage;
- any physical robot, hardware, ROS, actuator, production publisher or live
  site integration.

The schema, examples, TypeScript parser/client and their contract tests are the
only executable artifacts in Phase 3A.  They validate and reject data; they do
not advance `env.step()` or grant execution authority.
