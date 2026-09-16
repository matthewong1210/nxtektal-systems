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
