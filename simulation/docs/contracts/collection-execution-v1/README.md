# Collection execution v1 wire contract

**DESIGN ONLY — execution is not implemented.** These synthetic SIMULATION
assets freeze Phase 3A data and rejection rules. They add no session driver,
device, API route, simulator behavior or browser execution capability. The
[architecture](../../collection_execution_v1_architecture.md) is normative
alongside this schema and its relational conformance tests.

## Owners and compatibility

Planning v1 owns input revisions, plan versions, confirmation and separate
human outcomes. Edge v1 owns schedules, admission and transport lifecycle.
PlanningOperations verifies their exact cross-links. RangeSimulation owns
mutable runtime truth; BallLedger alone owns conserved quantities.
The future single V3 whole-course driver owns advancement; scripts compose
binding, durable requests and read projections. SafetyShield remains final
admission through apply_directive. The browser validates and displays.

V2 roots, bytes and replay digests remain immutable; execution requires a new
V3 root. Planning v1, Edge v1, Course Ops v1 and schedule v2 are unchanged.
No LLM, advisory package, Site Runtime, transport or browser gains execution,
actuator, ROS or e-stop authority. No physical integration is supplied.

## Bodies and strict validation

schema.json uses Draft 2020-12, ID
`urn:nxtektal:collection-execution:v1`, and the top-level union of Binding,
ExecutionRequest, RequestReceipt, ExecutionSnapshot, ExecutionRecord,
QuantityEvidence, SuccessEnvelope and ErrorEnvelope. Every object rejects
unknown and missing fields. IDs are bounded; digests are lowercase SHA-256.
UTC values require calendar-valid dates, terminal Z and at most six fractional
digits. Numbers must be finite, nonnegative and no greater than JavaScript's
safe integer maximum; integer quantities reject booleans and fractions.
JSON readers must reject duplicate keys and nonfinite JSON values.

Portable JSON Schema describes shape and local conditions. Consumers must
also enforce the relational rules below; schema validation alone does not
prove hashes, cross-links, time arithmetic, quantity attribution or admission.
The Python fixture oracle tests those rules; the TypeScript parser enforces
them on read. Neither replaces Phase 3B runtime verification.

Each example has a description, an ordered `bodies` list of
`{schema_ref, body}` declarations and a complete `snapshot` declaration.
Bodies describe internal durable exchanges, not browser POST endpoints.
All published bodies validate both their definition and the top-level union.
Repeated bodies in duplicate-request.json describe replay of the same durable
receipt. Snapshots are filtered task projections: pending action candidates
may refer to other session tasks outside the returned record set.

## Identity and immutable evidence

Binding includes site/deployment and commissioned source digest, Planning
plan/version/confirmation, schedule, exact Edge task/content digest and target
incarnation, session series/round/index/config/engine digests, the epoch and
runtime robot/zone/station mapping. The mapping
`picker-01/Z1 -> R1/NEAR_LEFT -> H1` is a synthetic fixture, never an
inferred physical alias. No identity may silently cross a binding or round.

Edge task_id preserves the existing derive_task_id format: task_ followed by
24 lowercase hex characters. task_content_digest is the separate full
64-character lowercase SHA-256 of the complete Edge task body; these fields
are not interchangeable. Source task facts remain illustrative in these
fixtures, while binding, execution and request hashes are recomputed from
their exact published canonical bodies.

Every v1 binding has handoff_binding_mode=SOLE_SCENARIO_STATION_V1. Before
Edge ACCEPTED, the Phase 3B composition must verify the frozen scenario has
exactly one handoff station and that its ID equals handoff_station_id. The
existing one-station scenario and these fixtures use H1; zero stations,
multiple stations or a mismatched H1 binding reject before acceptance. The
mode is a contract restriction, not a claim that this topology check is
implemented. Multi-station execution needs a separately versioned contract.

Production binding_id is SHA-256 of the canonical binding body excluding
binding_id. execution_id hashes the canonical object containing task_id,
task_content_digest, session_id, round_id and binding_id. Canonical JSON is
UTF-8, sorted object keys, compact separators, no nonfinite values; paths,
process IDs and receipt wall times never enter either identity. Published
source/event/config/engine hashes are illustrative fixture evidence and do
not attest to an implemented replay or real source artifacts.

Within a snapshot, binding IDs, request IDs, receipt request IDs, execution
IDs and attempt IDs are unique. Every request references an included binding;
every receipt and record references its original included request. Shared
task/session/round/incarnation/runtime/timing fields agree exactly. No two
requests may represent the same task/session/binding. V1 has one attempt per
execution and no hidden retries. Missing or conflicting identities fail closed.
Request, receipt and execution references are checked in both directions;
orphan receipts, missing execution records and duplicate receipt execution or
attempt identities are invalid. The original request ID, binding ID,
execution ID and attempt ID agree across each linked group.

The exact creation order is existing Planning confirmation/schedule ->
verified TASK_CREATED -> downstream V3 binding -> durable execution request
-> Edge ACCEPTED -> action. Planning v1 receives no new session or binding
field, and confirmation is not required to reference a future Edge task or
binding before due-time admission. The V3 composition verifies the complete
chain after TASK_CREATED and before it accepts execution.

## Clocks and explicit maximum

Simulation/business UTC is session_epoch_utc + sim_t_s. The frozen epoch is
2026-09-16T00:00:00Z. due_at_utc and expires_at_utc convert once into eligible
and exclusive latest-start seconds and never rebase at restart. Start obeys
eligible <= start < latest_start. Expiry is not an execution deadline.

The binding retains exact Planning input record/revision, source_kind,
source_ref, observation/validity timestamps and travel, collect, return and
unload cycle minutes. Evidence must be fresh at binding. Required positive
max_execution_s has no fallback:

```text
ceil((travel + collect + return + unload) * 60 / control_interval_s)
    * control_interval_s
```

Binding freezes bound_at_sim_t_s, its exact projected bound_at_utc, and the
verified task_created_at_utc source timestamp. Require bound_at_utc =
session_epoch_utc + bound_at_sim_t_s, task_created_at_utc <= bound_at_utc and
observed_at_utc <= bound_at_utc <= valid_until_utc. Binding time cannot be in
the snapshot's future or later than an action/start attributed to it. Evidence
fresh at the session epoch but stale when bound is invalid. The fixture's
TASK_CREATED and binding both occur at 60 seconds, before its 120-second start.
The binding is immutable input to the subsequent durable request; wall-clock
read timestamps do not determine this order.

The fixture's 2 + 5 + 2 + 2 minutes at a 60-second interval yields exactly 660
seconds. Wash and supply are excluded. Deadline equals actual start + maximum
and must fit within session_end_sim_t_s, otherwise MISSED with
INSUFFICIENT_SESSION_HORIZON and no action. Terminal cannot precede start;
success cannot occur after deadline. Phase 3B must stop the runtime assignment
at its deadline, not merely stop attribution.

server_time_utc is wall-clock read metadata; browser service freshness remains
15 wall seconds. It never controls task ordering or outcome. ACTIVE/PAUSED/
ENDED session state is independent of service freshness. A future reused
schedule form compares dates with fresh simulation UTC but checks write health
against wall time.

The same projected simulation UTC drives PlanningOperations, ScheduleService,
the Edge gateway and simulated device lifecycle, including task expiry and
Edge heartbeat receipt age, and business execution. Pausing simulation time
therefore also pauses those lifecycle ages. Wall time only drives the outer
15-second service-read health and process chunk budget; stale service reads
remain distinct from paused simulation/Edge state. Preserve the current
single-clock ScheduleService API: this design adds no second clock argument
and mixes no wall timestamps into its lifecycle journal.

## Wait-only arbitration evidence

WAIT_ONLY_NON_PREEMPTIVE_V1 calls the unchanged JointDispatchPolicy once per
tick. Persist original_action and selected_action; action.name is the existing
directive class name, index is the bound ActionCatalog index, and robot_id
retains the runtime robot. AssignCollection target_id is its runtime zone.
The current generic SendToHandoff(robot_id) has target_id null: the catalog
does not contain a station-specific action or station-specific index. These
are evidence, not a new command API. The synthetic example uses a one-robot,
one-zone catalog: Wait 0, AssignCollection 1, SendToHandoff 2, SendToCharge 3.

A pending proposal fills only an original Wait. Multiple requests may be
PENDING, but v1 permits a single RUNNING execution lease per session/round.
A running attempt's needed continuation wins a Wait slot before any pending
start; original non-Wait proposals still remain unchanged. Only when no
running continuation needs that slot are eligible pending candidates sorted
by (latest_start_sim_t_s, eligible_sim_t_s, execution_id); only the first can
consume that slot. The success example exercises all three tie-break keys.
Every listed candidate must satisfy eligible <= action.sim_t_s < latest_start;
future and expired requests do not belong in eligible_pending. The fixture
starts at 120 seconds so every displayed candidate is eligible; its deadline
is 780 seconds (120 + 660). A recorded actual start requires exactly one
WAIT_SLOT AssignCollection at that tick for the bound robot and zone, with
SafetyShield ACCEPTED. A rejected action cannot prove assignment start.
WAIT_SLOT is reserved for a new pending start. RUNNING_CONTINUATION records
an original Wait filled by the already running attempt's next collection or
handoff action; it is checked against that attempt's start and execution
deadline, not its expired latest-start boundary. Its selected robot/zone or
generic handoff action must belong to the running attempt. eligible_pending
may retain other eligible candidates to show that continuation outranked them;
the running execution itself is not a pending candidate. No new start may take
a held running lease, including a tick when that lease needs no directive.
The duplicate-request example includes a continuation at 480 seconds, after
latest-start 300 but before deadline 780, ahead of an eligible pending task.
The success example independently retains unchanged original-policy handoff.
At latest_start an unstarted request is MISSED without a late directive.
Non-Wait original actions remain unchanged. A matching handoff is recorded as
ORIGINAL_POLICY_CONVERGED; a different action for the leased robot explicitly
preempts. Other robot/staff actions remain unchanged. Only selected actions
pass once through ActionCatalog, RangeOpsEnv.step and final SafetyShield.
Safety rejection does not create an accepted assignment or ledger movement.
In Phase 3B the pre-acceptance sole-station gate makes the generic handoff
destination unambiguous: the simulator's station is the bound H1 in this
scenario. Quantity destination evidence remains separate, and a complete
unload must name that bound station. This contract does not claim a new
station-specific catalog entry or a deployed topology/admission gate.

## Lifecycle and quantities

Record.state is exactly PENDING, RUNNING, SUCCEEDED, PARTIAL, REJECTED, MISSED,
FAILED or INCONCLUSIVE. Nonterminal records have null terminal time/reason;
terminal records use stage TERMINAL. Stage milestones progress from
WAITING_FOR_POLICY_SLOT through travel, collection, RAW_COLLECTED_TO_ROBOT,
return, unloading and UNLOADED_TO_STATION.

Every quantity explicitly names its milestone and
RANGE_SIMULATION_BALL_LEDGER provenance:

- NOT_REACHED has null balls: the milestone did not occur.
- COMPLETE has integer balls, assignment ID, event IDs and digest.
- INCOMPLETE has null balls: evidence is missing or conflicting.

destination_id names the bound runtime robot for RAW_COLLECTED_TO_ROBOT and
the bound station for UNLOADED_TO_STATION. For COMPLETE evidence it identifies
the proven ledger destination. For NOT_REACHED or INCOMPLETE it only identifies
the destination whose quantity is unavailable; it does not assert a transfer.

Zero requires complete evidence proving zero transfer. Raw balls sum actual
assignment-correlated COLLECT_CYCLE ledger moves from zone to robot; unload
sums task-correlated UNLOADED moves from that robot into the bound station.
The runtime must start idle with zero prior payload. Keep event range/digest,
assignment terminal, ledger conservation and payload parity. Directive,
travel, aggregate deltas and Edge lifecycle messages are insufficient.

SUCCEEDED requires admitted start and assignment, positive complete raw,
complete equal unload into the bound station, timely terminal, conservation,
payload parity, complete event sequence and terminal, no identity/replay/event
conflict and no unresolved protection. Its only reason is
UNLOADED_ALL_COLLECTED_BALLS. success_display_allowed is true only for that
verified success. Raw collection alone never implies success.
Successful runtime event start/end sequences and digest must be present, with
start sequence <= end sequence. Edge acceptance must also be true. A PARTIAL
record cannot hide INCOMPLETE unload evidence or an incomplete runtime event
sequence; those gaps require INCONCLUSIVE instead.

ROBOT_PAYLOAD_FULL normally proceeds to unload. ZONE_EMPTY,
COLLECTION_ACCESS_BLOCKED, POLICY_PREEMPTED, LOW_BATTERY, ROBOT_FAULT,
ESTOP_LATCHED, HUMAN_ASSISTANCE_REQUIRED, EXECUTION_TIMEOUT and SESSION_ENDED
remain explicit collection exits. Positive complete abnormal collection is
PARTIAL even if later unloaded; zero complete is FAILED and evidence gaps are
INCONCLUSIVE. No post-e-stop motion is allowed; protection needs existing
external-reset semantics. Unknown counts are never replaced with zero.

No raw or unload quantity is copied to washed, supplied, clean inventory or
Planning outcomes. Planning MEASURED/MANUAL_ESTIMATE outcomes remain separate.
reconciliation is always NOT_PERFORMED; this projection never reconciles owners.

## Edge projection, conflict and restart

Edge keeps transport/task semantics and carries no quantity authority.
Durable pending maps to ACCEPTED; actual accepted assignment maps to RUNNING,
with collecting/raw_collected/returning/unloading progress. V3 SUCCEEDED maps
to Edge SUCCEEDED only after complete equal unload. V3 PARTIAL maps to FAILED
with unknown:partial_execution. FAILED maps to FAILED, INCONCLUSIVE maps to
INCONCLUSIVE. Misses/rejections map to REJECTED only before Edge acceptance.
After acceptance (edge_evidence.accepted=true), a policy-slot miss or final
SafetyShield rejection maps to FAILED with its reason; Edge v1 does not allow
ACCEPTED -> REJECTED. The V3 state still retains MISSED or REJECTED respectively.
Edge reasons preserve the existing closed Edge v1 vocabulary. New reason
tokens use its unknown: prefix, including unknown:policy_slot_missed and
unknown:safety_rejected; the V3 reason remains its own enum.

Conflicting terminals preserve all claims in terminal_states. Edge
effective_state becomes CONFLICT while record.state becomes INCONCLUSIVE.
success_display_allowed is false; device protection and authorization block
remain set. A prior success never wins reconciliation. The terminal-conflict
example retains complete simulator quantities as separate evidence.
Any differing terminal claims force this conflict state even if an untrusted
payload clears its conflict flag or claims success. Without conflict, terminal
claims must agree with effective_state. result_verification is VERIFIED,
UNVERIFIED or CONFLICT; verified is true exactly for VERIFIED. CONFLICT agrees
with effective_state CONFLICT, V3 INCONCLUSIVE, success display disabled and
protected/blocked authorization. A conclusive terminal has matching terminal
claims and VERIFIED evidence. A restart-unknown outcome is UNVERIFIED, even
when its transport message is readable; verified does not assert transport
availability or clear device protection.

Ordinary chunk replay emits no new Edge lifecycle and no new attempt.
Completed same-incarnation device restart replays only its persisted terminal.
Restart after acceptance before start yields FAILED/not_started_after_restart.
Restart after start without persisted terminal yields INCONCLUSIVE/
interrupted_execution_unknown_outcome and no resume or old authorization.
A new incarnation/lost or rolled-back journal rejects old authorization and
requires explicit new authorization; regression stays protected. Device fault,
e-stop, human assistance, unresolved conflicts and late/orphaned runtime work
also protect the device. HTTP success or replay does not clear protection.
Availability requires active session, mapped idle empty robot, no pause/fault/
human/e-stop/protection. Session pause can coexist with ONLINE transport.

## Durable request idempotency and read-only API

Before Edge ACCEPTED, append under the session lock with continuous sequence,
canonical content digest, fsync and high-water anchor; verify readback before
any action. The receipt preserves original request, execution, attempt,
sequence and high-water identity. Consistent rollback of journal plus anchor
is outside this mechanism's guarantee.

Same request ID/kind/content returns the original receipt. Same ID with
different kind/content conflicts. Another ID for the same task/session/binding
also returns the original receipt, creating no alias or attempt. Unknown
append/response gives collection_execution_result_unknown; query or retry the
original ID. Exact replay includes bindings, high-water identity, policy,
arbiter, original/selected actions, assignment events and terminals; mismatch
fails closed.

The per-tick transaction is also DESIGN ONLY. Under the session lock,
ACTION_PREPARED means append and fsync tick_intent containing the previous
committed cursor/digest, request-log high water, policy/arbiter identities,
original/selected action and execution/assignment identity. Then call
RangeOpsEnv.step once. ACTION_COMMITTED means append and fsync tick_commit
with resulting events/state digest and a deterministic Edge-event outbox.
Publish the disposable state/report cursor last. The simulated device reads
only committed outbox entries, persists exact Edge event bytes in its own
journal, then publishes. It never calls env.step.

Before a durable intent, recompute without claiming an action occurred. With
an intent but no commit, rebuild the committed prefix and replay the exact
intent; inability to verify its deterministic result fails closed as
INCONCLUSIVE with device protection. With a durable commit but missing cursor,
repair the disposable cursor without another logical execution. An unconfirmed
committed Edge event is redelivered with its same bytes and sequence, never a
new attempt. Edge evidence lacking a matching committed tick is a conflict
and blocks authorization. An intent alone cannot publish progress or terminal
evidence. These crash rules do not downgrade actual device-process restart
rules to ordinary chunk replay and do not implement a journal in Phase 3A.

Future GET /api/v1/collection-executions reads snapshots.
GET /api/v1/collection-executions/requests/{request_id} recovers receipts.
Every POST/PUT/PATCH/DELETE in that namespace is 405. No browser endpoint
creates, retries or controls execution. The existing Planning confirmation and
schedule are unchanged. After due-time admission creates verified TASK_CREATED,
the downstream V3 binding is created and its execution request is durably
recorded; only then may Edge ACCEPTED and an execution action follow.

The existing nxt-site-agent/api/v0 success envelope is schema/disclaimer/data;
error is schema/disclaimer/error with code/detail. The client is same-origin,
GET-only and cache: no-store. These routes, device, request journal, V3 session,
runtime assignment evidence and React components remain Phase 3B work.

## Verification

From simulation:

```sh
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/pilot_ops/test_collection_execution_wire_contract.py
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/pilot_ops
```

The eight examples cover closed-loop success, policy miss, preempted partial,
safety rejection, restart unknown, identity conflict, duplicate requests and
terminal conflict. Negative controls cover strict structure, clocks, finite
numbers, quantity nullability, cross-links, duplicate identities, limit
derivation, ordering, success prerequisites and lossy Edge mapping.
