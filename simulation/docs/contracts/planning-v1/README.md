# Human-led planning API v1

Contract owner: Codex/backend. Consumer: `apps/site-agent-console/` (Claude).
This directory is the shared contract; the browser must not invent another
model, calculate stockouts, persist effective settings, or translate advice to
tasks. `schema.json` is normative for wire shapes; the rules below are normative
for semantics. Examples use fixed illustrative UTC times, not live site facts.

## Scope and compatibility

SIMULATION only, manual-led by default, no learned forecast or calibrated
probability interval. Operator text is attribution, never authentication or
permission. Old `/api/v0/recommendations/*/accept` remains workflow evidence
only. New confirmation is a separate explicit action for an exact plan version.
No physical integration, deployment, or automatic mode is included.

`nxt_pilot_ops` owns immutable planning inputs, pure scenario calculations,
plan versions, human amendments and actual-result evidence. It reuses
`project_stockout_minutes()` and canonical serialization/digests. The policy
is `human-led-ball-supply/v1`: intentional divergence from the Guardian and
FacilityState rules, using explicit cold-start hypotheses rather than filling
missing operational history. These outputs are never merged with the old
recommendation queue. No FacilityState schema changes or inventory writes.

`nxt_edge_task` remains owner of simulation schedules, admission and task
status. `simulation/scripts/` binds the owners through serialized records and
the existing anchored journal. Site Agent only transports an injected callback.
No sibling-package import, model, or advisory-to-device call is introduced.

V1 adds `/api/v1/planning` and leaves v0 wire shapes intact. Unknown fields,
schema versions and enum values are rejected. No automatic migration or
reinterpretation of old records. Historical records retain their rules version
and original inputs. Changed calculation semantics require a new rules version.

## Identity and time

GET context supplies authoritative rehearsal `site_id`, `deployment_id`,
`site_timezone`, allowed `zone_ids` and capable `robot_ids`. V1 binds to the
Edge rehearsal deployment, not the independent old Site Agent fixture panel.
For the checked-in demo these are `pilot-course-a`,
`pilot-a-edge-task-sim-v0`, and `Asia/Shanghai`. Zones come from admission facts;
robots must support `COLLECT_BALLS_ZONE`. Identity mismatches fail closed.
Timezone is display metadata, not a replacement for UTC timestamps. All wire
times use RFC3339 UTC with `Z`, including bucket origin, evidence, validity,
start, confirmation and results. The composition root supplies the clock.
Intervals are `[start, end)`; equality with an expiry is expired. Input horizon
starts at `effective_at_utc`, not at the time a page refreshes. No demand is
extrapolated after the last supplied bucket. Re-evaluation consumes elapsed
buckets and demand; it does not rebase old inventory to now.

## Routes and envelopes

Responses retain the Manager API envelope:
`{"schema":"nxt-site-agent/api/v0","disclaimer":"...","data":...}`.
Errors use the same envelope with `error: {code, detail}` instead of `data`.
The data schema for this surface is `nxt-planning/v1`. HTTP 200 is used for
successful creates and replays. HTTP 400 is invalid input, 404 unknown IDs,
409 stale versions/expired or infeasible plans/idempotency conflict, and 503
unreadable evidence or unknown commit outcome.

| Method and path | Request | Data |
|---|---|---|
| GET `/api/v1/planning` | none | Context, latest input, all plan versions, confirmations, outcomes |
| POST `/api/v1/planning/inputs` | `InputRequest` | MutationReceipt with input record |
| POST `/api/v1/planning/plans` | `PlanRequest` | MutationReceipt with evaluated plan |
| POST `/api/v1/planning/confirmations` | `ConfirmationRequest` | MutationReceipt with confirmation and schedule/task links |
| POST `/api/v1/planning/outcomes` | `OutcomeRequest` | MutationReceipt with result evidence |
| GET `/api/v1/planning/requests/{request_id}` | none | MutationReceipt, or 404 `planning_request_not_found` |

Every mutation has a caller-generated stable `request_id`. Preserve the exact
request bytes/values across retry (JSON key order is irrelevant). Same ID plus
same content returns `disposition: "duplicate"`; same ID with different content
returns 409 `planning_conflict`. GET by request ID is the recovery path after
a dropped connection/503. Do not generate a new ID just because the response
was lost. A 404 means not present in the currently verified journal; retrying
that same ID remains safe. Unreadable evidence is 503, never an empty success.

MutationReceipt: `schema`, `disposition` (`created` or `duplicate`),
`request_id`, `record` (the durable input/plan/confirmation/outcome). Known
commit receipts are constructed from the successful append result; later
projection failure cannot turn a known commit into a claimed rejection.

GET snapshot: `schema`, `environment: "SIMULATION"`,
`mode: "MANUAL_LED"`, `server_time_utc`, `context`, `latest_input` (null before
entry), `plans`, `confirmations`, `outcomes`. Arrays start empty. No inventory,
demand, duration or restriction is prefilled. Context contains `site_id`,
`deployment_id`, `site_timezone`, `zone_ids`, `robot_ids`.

## InputRequest and evidence

Exact fields: `schema: "nxt-planning-input/v1"`, `request_id`,
`expected_revision` (0 for first input), `site_id`, `deployment_id`,
`site_timezone`, `operator`, `reason`, `scope` (`SHIFT`, `DAY`, `ONE_TASK`),
`effective_at_utc`, `valid_until_utc`, `operating_window` (`start_at_utc`,
`end_at_utc`), `inventory_clean_balls`, `demand`, `safety_stock_balls`,
`buffer_minutes`, `operations_allowed`, `washer_available`, `zones`.

An evidence field is null when unknown, otherwise exactly:
`{value, source_kind, source_ref, observed_at_utc, valid_until_utc, unit}`.
`source_kind` is `MEASURED` or `MANUAL_ESTIMATE`. Source reference is required;
operator-supplied names do not prove a sensor reading. Times must be ordered;
future observations and non-finite numbers are rejected. Zero is accepted only
when explicitly submitted with evidence. Estimates are never relabelled as
measurements. Input validity cannot extend evidence validity.

| Field | Unit | Evidence value |
|---|---|---|
| inventory_clean_balls | `balls` | nonnegative number, supply-ready clean balls only |
| safety_stock_balls | `balls` | nonnegative target |
| buffer_minutes | `minutes` | nonnegative scheduling margin |
| operations_allowed, washer_available | `boolean` | boolean; unknown/false blocks planning |
| demand | `balls/minute` | `{bucket_minutes, low, typical, high}`; equal-length nonempty rate arrays, elementwise low <= typical <= high |

Demand estimates are manual assumptions, not statistical quantiles. Inventory
is an opening count at `effective_at_utc`; its observation time must equal that
origin. Demand buckets also start there. Weather/turf/work restrictions are
captured by dated `operations_allowed` and zone `collection_allowed` evidence;
no invented weather-demand multiplier is applied.

Each zone entry has exact fields `zone_id`, `robot_id`, `collection_allowed`,
`clean_yield_balls`, `cycle_minutes`. Last three are nullable evidence:

- `collection_allowed`: boolean unit, explicit local permission/condition.
- `clean_yield_balls`: balls unit, `{low, high}`, nonnegative integers with low <= high; estimated net
  supply-ready yield, not raw collected balls.
- `cycle_minutes`: minutes unit, `{travel, collect, return, unload, wash,
  supply}`; every stage is explicit and nonnegative. This is full replenishment
  time, never just vehicle collection time. Unload/wash/supply are manual steps
  in the current protocol rehearsal.

One entry per `(zone_id, robot_id)`. An empty zones list or nullable fields can
be saved, but yields explicit missing-data output and cannot be confirmed.
A revision is a complete replacement input document, with compare-and-swap
`expected_revision`. Persisted input adds `revision`, `input_digest`,
`recorded_at_utc`, and `changes` entries `{path, before, after}`. Corrections and
restoring defaults are new records retaining operator, reason, scope and expiry;
no sensor/history record is overwritten. Saving a revision invalidates pending
plans from older revisions; dispatched tasks and result records remain intact.

## PlanRequest and PlanRecord

Exact request fields: `schema: "nxt-planning-request/v1"`, `request_id`,
`plan_id` (null for new), `expected_plan_version` (0 for new), `input_revision`,
`operator`, `reason`, `scope`, `valid_until_utc`, `selection` (null for system
suggestion, otherwise `{zone_id, robot_id, start_at_utc}`). Revisions target
one plan ID, increase version, and require current input revision. A confirmed
plan cannot be revised. To restore the system suggestion, submit a new version
with null selection; history is retained. Confirmation of any version consumes
the plan ID permanently, even after cancellation.

PlanRecord fields: `plan_id`, `version`, `request`, `input_revision`,
`input_digest`, `rules_version`, `generated_at_utc`, `valid_until_utc`,
`status` (`READY`, `MISSING_DATA`, `EXPIRED`, `INFEASIBLE`), `missing`,
`rationale`, `system_selection`, `selection`, `adjustment` (`before`, `after`,
`operator`, `reason`, `scope`, `valid_until_utc`), `scenarios`, `candidates`.
GET may add `current_status` (`INVALIDATED`, `CONFIRMED`, or the stored status).
Stored historical calculation is never rewritten on read.

Each scenario has `level` (`low`, `typical`, `high`),
`baseline_stockout_at_utc`, `safety_stock_at_utc`,
`with_plan_stockout_at_utc`, `baseline_end_balls`, `with_plan_end_balls`.
Null stockout time means no exhaustion within the supplied horizon; it is not
infinite coverage. Quantities at horizon are signed projected balances to make
uncovered demand visible, not observed inventory. Scenarios without enough data
are omitted; `missing` identifies the absent evidence.

Each candidate has `zone_id`, `robot_id`, `eligible`, `exclusion_reasons`,
`start_at_utc`, `latest_start_at_utc`, `supply_at_utc`,
`cycle_minutes`, `clean_yield_balls` (`low`, `high`),
`protects_high_demand_horizon`. Unknown candidate computations remain null.
Only complete, fresh, allowed candidates can be selected. Candidates are ranked
by ability to avoid first shortage, then earlier full replenishment, then lower
net yield (descending), then IDs. High demand and low yield are the conservative
comparison; no probability claim. Safety-stock deadline subtracts all six
stages plus explicit buffer. A late/wash-delayed or horizon-unprotected choice
is shown with its impact and INFEASIBLE if it cannot cover the high-demand
horizon. An infeasible choice cannot be confirmed. Supplementary/manual supply
or a shorter justified horizon needs new evidence, never a fabricated plan.

## Confirmation and simulation links

Exact ConfirmationRequest: `schema: "nxt-planning-confirmation/v1"`,
`request_id`, `plan_id`, `plan_version`, `operator`.
Only a fresh READY latest version over the latest input can be confirmed.
The request records explicit human intent; it never reinterprets old acceptance.
Confirmation stores `confirmation_id`, `request`, `plan_id`, `plan_version`,
`input_revision`, `confirmed_at_utc`, `schedule_id`, and frozen `schedule`
creation payload (`robot_id`, `zone_id`, `due_at_utc`, `expires_at_utc`,
`operator`, `admission_reference`, the opaque confirmation ID). Bound schedules
use `nxt-edge-schedule/v2`; unbound legacy schedules retain v1 bytes and behavior.
Only the composition root can create a bound schedule. Read projection may add `schedule_status` and `task_id`.
A second request ID for the same exact plan version returns the existing
confirmation as duplicate with the original committed `request_id` in its
receipt; no alias record is appended. Query that original ID. If this duplicate
response is lost, retry the new ID to recover the original receipt; querying
the new ID alone returns 404 and does not authorize another schedule. A
different version conflicts. At most one schedule
per plan ID. A durable confirmation intent is recovered into the identical
content-derived schedule after restart. Unknown write outcome is queried and
retried by original request ID; a failed projection is not proof of failure.

At due time, validate version/input identity, expiry and all restrictions again
inside scheduling admission. A revised input, closed zone, expired weather/work
condition or superseded plan rejects a pending schedule. Existing device
freshness/availability/safety and task-conflict admission still apply. Missing
planning gate fails closed for linked schedules. Already dispatched tasks are
immutable and remain bound to the original plan; updating inputs is not a stop.
Cancel a still-pending schedule through the existing task-ops cancel endpoint.
Cancellation consumes the plan; create and confirm a new plan to retry.

## Actual results

Exact OutcomeRequest: `schema: "nxt-planning-outcome/v1"`, `request_id`,
`confirmation_id`, `task_id`, `operator`, `reason`, `stage` (`COLLECTED`,
`UNLOADED`, `WASHED`, `SUPPLIED`), `quantity_balls`, `started_at_utc`,
`completed_at_utc`, `source_kind` (`MEASURED` or `MANUAL_ESTIMATE`),
`source_ref`, `supersedes_outcome_id` (null for first record for that stage).
It records `ACTUAL_EXECUTION_RESULT` evidence (with measured/estimated quality),
not a suggested yield. Counts are nonnegative integers. Time must be ordered,
not future, and belong to an admitted task linked to that confirmation.
Each stage is independently unknown until recorded. A stage correction must
reference its latest record; it appends and never deletes evidence.

OutcomeRecord: `outcome_id`, `recorded_at_utc`, `evidence_kind:
"ACTUAL_EXECUTION_RESULT"`, `request`, plus plan/version/input/schedule links.
Successful task status never creates these records or increments clean inventory.
Even SUPPLIED is retained evidence, not a live inventory mutation: a later
opening-count revision must explicitly include it with a provenance reference.
Future warehouse/telemetry ingestion is a separate contract. Missing earlier
stages stay missing; no conservation equality is invented between estimates.

## Error codes and example use

`planning_invalid_request` (400), `planning_not_found` and
`planning_request_not_found` (404), `planning_conflict`, `planning_expired`,
`planning_not_ready` (409), `planning_unavailable` and
`planning_result_unknown` (503). Detail is diagnostic; branch on code, not text.
`planning_result_unknown` means no reliable commit receipt was obtained; stop
new confirmation attempts and query/retry the same ID. Success, missing,
expired, conflicting, duplicate and unknown-result examples live in `examples/`.
