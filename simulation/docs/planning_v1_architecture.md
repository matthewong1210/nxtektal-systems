# Human-led planning v1 architecture gate

Decision: **Proceed** within existing owners; user explicitly authorized a
local simulation confirmation bridge. No physical execution path is approved.
Base: `4f1fa803e5ea1f9dc5f955e85823a436c3bc86bc`, local integrated but
unmerged Pilot Dispatch V0. Implementation branch: `codex/predictive-operations-v1`.
Original checkout/main/Claude work remain untouched. This is a `simulation/`
and shared-contract/documentation change. Console implementation is Claude's.

Read current source/exports/tests for Facility decisions, Shadow Guardian and
projection, manager queue, Edge schedules/journal, and Pilot Dispatch root.
Searched existing packages and local branch history for forecast, stockout,
schedule, approval and outcome ownership. No other planning implementation was
present at the fixed baseline. Remote PR status is not inferred from local refs.

| Fact/behavior | Single owner | Contract and persistence |
|---|---|---|
| Cold-start hypotheses, input revisions, scenario evidence, human amendment, actual results | `nxt_pilot_ops` | versioned plain JSON, canonical digest; append-only planning records |
| Stockout arithmetic | existing `nxt_pilot_ops.projection.project_stockout_minutes` | reused for baseline, safety threshold and conditional supply |
| Planning API transport | `nxt_site_agent` | injected `/api/v1/planning` callback, no task import |
| Human confirmation to rehearsal schedule association | `simulation/scripts/` composition root | durable confirmation intent plus deterministic existing schedule identity |
| Dated simulation intent, admission, task/device status | `nxt_edge_task` | existing anchored journal and schedule/task-created linkage |
| Physical static facts | `nxt_commissioning` | unchanged immutable manifest/projection |
| Live state/inventory | `RangeSimulation`/`BallLedger`, downstream `FacilityState` | unchanged; planning/results never write live truth |

Use the existing Edge journal mechanism as an injected plain-data evidence port;
planning owns interpretation of its versioned payloads. Edge accepts registered
opaque composition evidence kinds and never imports the planner. No new package,
second runtime, third decision engine or duplicate facility rule. The policy
intentionally differs from Guardian's operational snapshot because cold-start
hypotheses have no historical served-demand or runtime availability facts. Do
not silently reconcile their recommendations. Reuse stockout projection and
existing canonical serialization and content digests, not frontend formulas.

All time is explicit UTC supplied by the root. No RNG/hidden clock in policy.
Unknown/stale evidence is visible and blocks confirmation/admission. Strict
schema and source tags preserve estimates versus observations versus results.
Immutable input and plan versions retain before/after, reason, actor and expiry.
A confirmation binds one exact version and frozen schedule payload. Recovery
reuses that payload; due-time checks happen before existing task admission.
Retries use request IDs; lost write acknowledgements return unknown, not failure.
No already admitted task is silently altered by a new input or plan.

V0 acceptance remains unchanged in meaning. New v1 records are additive and
unknown versions fail closed. Old journals remain readable; v1 readers retain
v1 rules and original serialized evidence for replay. No production publisher,
robot, ROS, actuator, e-stop or physical device route is added. Text operator
names are attribution only. The old fixture deployment/time remains separately
labelled; new planning uses the rehearsal context, not fixture state.

Verification: stage-1 post-commit and ambiguous-write fault injection; strict
schema/examples; missingness/freshness, deterministic scenario/projection parity,
restricted region, full wash/supply delay, revision impact and provenance;
confirmation duplicate/conflict/concurrency/crash/restart, due invalidation,
immutable dispatched linkage, four independent result stages; existing
architecture/safety/package/full suites, config validation and local Mosquitto.
Frontend integration and browser acceptance wait for Claude's final commit.

Implementation detail: only the composition root's journal instance registers
planning record kinds (`EDGE_RECORD_KINDS | PLANNING_RECORD_KINDS`); Edge's own
kind vocabulary remains unchanged. The generic schedule v2 binding is an opaque
confirmation ID. Before startup transport/republication and at each tick, the
root verifies deterministic planning replay and frozen schedule/task identity.
No callback reads a file while inside the journal lock. Human operator text is
retained verbatim as evidence; a stable internal token satisfies the legacy
simulated TaskRequest issuer vocabulary without pretending to authenticate it.

## Task admission time read projection (2026-09-17)

Decision: **Proceed** within the existing composition/read boundary. Base:
`21bb263b26e5740e5c92893f729ee05491fb5390`, reviewed local console fix;
branch `codex/planning-task-admission-time-v1`, independent clean worktree.
Search of the planning contract, projection, journal, schedule derivation and
local branches identifies `PlanningOperations._task_link()` as the existing
verified association owner. No new admission calculation or store is needed.

Edge's immutable `TASK_CREATED.recorded_at_utc` owns the rehearsal admission
time. The root reads a single verified journal prefix and reuses `_task_link()`
to project nullable `task_created_at_utc` with `task_id` into GET snapshot
confirmations. Shadow Ops still owns the original confirmation; its payload,
canonical bytes, IDs, journal records and hashes are unchanged. POST and
request-ID recovery receipts continue returning only the durable record.

This is an additive optional read field in `nxt-planning/v1`: new snapshots
emit it, old absent fields normalize to null in the updated console parser.
Old strict parsers require a coordinated update. No record migration or
recalculation occurs. An absent admission stays null; a mismatched association
still makes the entire read unavailable. Never substitute due, confirmation or
current time. Replay projects the same recorded timestamp independent of the
read clock, including its original precision. It is only a lower bound for
actual stage starts, never evidence that any collection or later stage began.

The existing root imports are sufficient. No reverse dependency, new runtime,
policy, RNG, clock, authentication or execution authority is added. Frontend
changes are limited to the planning wire type/parser and their tests; scheduler
health, controllers and components remain Claude's responsibility. The separate
whole-course-sim-v2 and background experiments are outside this change; no
18-hole simulation or physical execution connection is introduced.

Verification plan: no-admission lifecycle cases, delayed admission distinct
from plan/confirmation/read times, malformed linkage fail-closed reads, unchanged
journal/anchor bytes and receipts, restart replay, strict schema/examples and
legacy parser compatibility; focused packages, architecture/safety suite, full
Python suite/config validation, and console typecheck/lint/tests/build/smoke.
