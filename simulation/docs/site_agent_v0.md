# Pilot Site Agent Service V0

`nxt_site_agent` is the local, fixture-backed application boundary that
turns the existing Site OS composition into a usable product surface: a
readiness-gated service around the existing Agent Runtime, a versioned
loopback-only Manager API, and a browser Manager Console served
same-origin from a static export.

Everything in this document describes synthetic, fixture-backed
behavior. SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The end-to-end path is exactly the existing one, now reachable from a
browser:

```text
fixture dispenser reading (already-read raw samples)
    -> existing edge adapter conversion -> canonical Observation
    -> existing ObservationSource semantics (peek / ack / reject)
    -> existing Site Runtime admission, assembly, quality gate
    -> FacilitySnapshotEnvelope (exact FacilityState + AssemblyReport)
    -> existing Agent Runtime -> existing Shadow Ops evaluation
    -> NO_ACTION or evidence-backed recommendation
    -> Manager Console -> existing accept / reject / modify workflow
    -> existing ledger, journal, checkpoints
    -> restart -> deterministic recovery
```

## What this is not

- **Not a second runtime, state, or decision owner.** Observation
  admission, assembly, the publication-quality gate, the snapshot
  envelope, checkpoints, and acknowledgement keep their existing state
  orchestration owner; the evaluation lifecycle keeps `nxt_agent_runtime`;
  policy, recommendations, traces, human workflow, and the ledger keep
  `nxt_pilot_ops`; readiness keeps `nxt_workflow_enablement`. The
  service invokes their public surfaces and re-models none of them.
- **Not a sensor integration.** No physical device is connected. The
  fixture source stands exactly where a future transport reader will
  stand; see
  [`cto_hopper_sensor_integration.md`](cto_hopper_sensor_integration.md).
- **Not a production service.** Loopback-only, no authentication, no
  deployment path, no cloud, no multi-site operation.
- **Not an execution surface.** No endpoint, projection, manager
  action, or browser control can create a robot command, schedule a
  task, write a register or GPIO, control a washer or handoff, invoke
  ROS or Nav2, or touch an emergency stop. Manager acceptance is human
  workflow evidence only. No LLM participates anywhere.

## Architecture decision (recorded gate outcome: Proceed)

The missing responsibility was named explicitly: *a long-running local
application/service boundary* that loads a commissioned site, verifies
workflow enablement, composes the approved fixture source, drives the
existing Agent Runtime, exposes noncanonical projections and health,
transports existing manager workflow responses, and preserves
restart/recovery. No existing placement row covers it: Site Runtime is
orchestration-only and guard-tested to have no network surface, Agent
Runtime is composition/lifecycle-only with the same network ban,
Shadow Ops owns decision semantics, and `simulation/scripts/` are
per-invocation composition roots without guards or wheels.

Shapes evaluated:

- **A. Narrow application package + composition-root scripts —
  chosen.** The boundary owns persistent versioned contracts (the
  Manager API `nxt-site-agent/api/v0`, the service-state and
  service-events file schemas) and needs a mechanical guard, which is
  the same reason `nxt_agent_runtime` and `nxt_workflow_enablement`
  became packages. Fixture composition (adapter kit, feed, enablement
  evaluation, runtime factory) stays in `scripts/site_agent_fixture.py`
  and `scripts/site_agent_demo.py`, because only composition roots may
  assemble across those packages.
- **B. Scripts-only service — rejected**: persistent versioned
  contracts without guards, tests-as-afterthought, and no wheel
  membership.
- **C. Standalone service outside the surfaces — rejected**: it would
  create a fourth implementation surface and new CI plumbing for code
  that is inherently part of the Python Site OS composition.
- **Extending `nxt_agent_runtime` — rejected**: its guard bans every
  network import by design, and lifecycle-vs-transport is exactly the
  boundary worth keeping.
- **Extending `apps/operational-replay` — rejected**: Operational
  Replay remains a read-only replay/presentation leaf; a live-service
  console is a different application, added as
  `apps/site-agent-console/`.

## Ownership map

| Fact | Owner | Service's relationship |
|---|---|---|
| Raw-sample conversion, canonical Observations, source cursor semantics | edge adapter kit | composed in scripts; diagnostics reach the service as plain data |
| Admission, assembly, quality gate, envelope, publication checkpoint | state orchestration layer | driven only through the existing Agent Runtime |
| Evaluation lifecycle, journal, evaluation checkpoint, decision queue | `nxt_agent_runtime` | driven via `run_once()`/`recover()`/`status()`/`queue` |
| Policy, recommendation, trace, workflow records, ledger | `nxt_pilot_ops` | records read for projection; responses recorded via the existing queue operations |
| Workflow readiness, enablement report, launch plan | `nxt_workflow_enablement` | verified at launch; NOT_READY refuses launch |
| Service lifecycle state, Manager API v0, projections, briefing | `nxt_site_agent` | owned (noncanonical) |
| Fixture source resume cursor persistence (`source_cursor.json`) | `nxt_site_agent` | owned (noncanonical service metadata; the analogue of a transport reader's resume hook) |
| Service diagnostics stream (`service_events.jsonl`) | `nxt_site_agent` | owned (noncanonical, append-only, never a live input) |
| Manager Console UI | `apps/site-agent-console` | consumes only the Manager API |

## Launch, resume, and evidence layout

One runs directory holds numbered runs; each run holds one
`(site, deployment)` identity:

```text
<out>/run-001/pilot-course-a/pilot-a-site-agent-v0/
  workflow_enablement_report.ready.json     # canonical report bytes
  range.closed_loop_collection_handoff/     # canonical evidence root
    checkpoints/site/…  checkpoints/evaluation/…
    evaluations.jsonl  ledger.jsonl  snapshots.jsonl
  service/                                  # noncanonical service metadata
    launch.json  source_cursor.json  service_events.jsonl
```

- **Fresh launch** requires a provably empty workflow evidence root.
  The composition seam evaluates enablement against that exact root,
  the planner fails closed for NOT_READY, and the service re-verifies
  everything it can: report bytes (`verify_report_payload`), READY
  verdict and runtime-assembly eligibility, identity agreement across
  report/plan/storage, `FIXTURE_ONLY` transport, and `SHADOW` posture.
  Only then are the report, launch record, and zero cursor written and
  the runtime composed.
- **Resume** never re-declares root emptiness. It revalidates the
  stored report bytes, rebuilds the plan from the launch record,
  cross-checks the report identity, recomposes the source at the
  persisted cursor, and runs the runtime's own `recover()` — which
  fails closed on foreign identity or divergent evidence.
- **Collision** — a non-empty root without valid service records, a
  file-valued root, or an unreadable root — refuses launch.

The source cursor is written atomically after every cycle. Losing the
latest write leaves it behind by at most one resolved cycle, and
redelivery is idempotent: an acknowledged cycle replays as
`replay_skipped`, a rejected cycle re-rejects identically. A cursor
that cannot be written makes a future restart unsafe, so the service
fails closed instead of advancing further.

## Service behavior

`SiteAgentService` is deterministic and lock-serialized. It advances
the runtime exactly one bounded cycle per fixture advance, records a
noncanonical cycle event (including rejection codes and adapter
diagnostics captured by the composition), persists the cursor, and
refuses advances once the source is exhausted or the plan's declared
`max_cycles` bound is reached. Wall clock is never read: reading age,
`responded_at`, and every briefing time use observation/scenario time,
so identical action sequences produce byte-identical canonical
evidence across runs (`tests/site_agent/test_service.py` proves it).

Failure behavior is conservative and visible: a missing sample is
explicit MISSING evidence, never zero inventory; a stale reading is
never relabeled fresh; a rejected cycle produces no policy evaluation
and remains visible in exceptions; runtime fail-closed incidents put
the service into FAILED (read-only API still serves diagnostics); a
broken evidence store surfaces as FAILED at recovery; and an API or
browser error can never mutate canonical evidence.

## Manager API v0 (`nxt-site-agent/api/v0`)

Loopback-only (`127.0.0.1`/`localhost`; anything else is refused at
construction). Every response carries the schema and the fixture
disclaimer. No cross-origin headers exist: the console is served
same-origin by the service.

| Endpoint | Meaning |
|---|---|
| `GET /api/v0/health` | Noncanonical service + runtime diagnostics: service state, identity, readiness verdict, source cursor/exhaustion, sequences, pending count, last failure |
| `GET /api/v0/state` | Read-only projection of the latest published envelope: dispenser inventory, sensed reading, per-channel source status, reading age (scenario time), assembly/runtime quality; explicit no-data shape before any publication |
| `GET /api/v0/evaluations` | Existing evaluation records; NO_ACTION embeds its canonical trace, RECOMMEND records surface the ledger-stored trace |
| `GET /api/v0/recommendations` | The existing manager decision queue joined with ledger trace/recommendation/response payloads; owner identity preserved, nothing ranked or reconciled |
| `GET /api/v0/briefing` | The Shift Briefing projection (below) |
| `GET /api/v0/demo` | Fixture-only metadata: declared cycle catalog, cursor, next cycle, control availability |
| `POST /api/v0/recommendations/{id}/accept\|reject\|modify` | Transport for the existing workflow operations; `operator_id` and `reason_code` required, `responded_at` defaults to scenario now; ledger legality decides (unknown id → 404, illegal transition → 409) |
| `POST /api/v0/demo/advance\|restart\|reset` | Fixture-only controls: one bounded cycle; recompose from persisted evidence and cursor; fresh launch into the next empty run directory |

Manager responses are recorded through the existing
`ManagerDecisionQueue` operations, so response identity, immutability,
single-response legality, and the hash-chained ledger are unchanged.
`responded_at` uses scenario/observation time (the latest delivered
observation), never a wall clock, keeping evidence deterministic and
`execute_before` comparisons meaningful.

## Shift Briefing projection

Read-only and noncanonical. It summarizes only facts already present
in published envelopes, evaluation records, decision traces,
recommendation/workflow records, runtime status, and the service's own
diagnostics stream, tagging every entry `OBSERVED`, `DETECTED`,
`RECOMMENDED`, `MANAGER_DECISION`, `MISSING`, `STALE`, `SERVICE`, or
`SIMULATED`. It is not a policy engine, it never ranks or reconciles
recommendations, and it invents no outcomes. Every briefing carries
the fixture disclaimer.

## Fixture storyline

`scripts/site_agent_fixture.py` composes the existing Pilot Course A
manifest under the dedicated deployment `pilot-a-site-agent-v0` and
declares six cycles through the real adapter conversion path:

| Cycle | Story | Outcome |
|---|---|---|
| 0 | 17:30 calm inventory | admitted seq 0 → `NO_ACTION` with full trace |
| 1 | 18:30 evening spike | admitted seq 1 → `RECOMMEND operator_intervention` (fail-closed exclusions, no fabricated dispatch) |
| 2 | 19:00 dispenser load cell silent | explicit MISSING observations → rejected `insufficient_data_quality`; never zero inventory |
| 3 | 19:30 stale robot heartbeat | rejected `stale_observation` |
| 4 | 19:30 uncalibrated dispenser reading | `calibration_missing` diagnostics → rejected `insufficient_data_quality` |
| 5 | 19:30 corrected redelivery | admitted at the reused seq 2 → `RECOMMEND operator_intervention`; evaluated once, no duplicate evidence |

Rejected cycles reuse their sequence exactly as the at-least-once
source contract requires. Restart works at every point: after
publication before evaluation and after evidence before
acknowledgement recover idempotently through the existing runtime, and
a service restart resumes from the persisted cursor
(`tests/site_agent/test_service.py` covers the stale-cursor windows).

## Running it

Build the console once, then run the one service command:

```bash
cd apps/site-agent-console && npm ci && npm run build && cd ../..
```

```bash
cd simulation
uv run --no-sync python -B scripts/site_agent_demo.py \
  --out reports/site-agent --port 8765 \
  --console ../apps/site-agent-console/out
```

Open `http://127.0.0.1:8765/`. Useful flags: `--advance N`
(deterministically pre-run N cycles), `--fresh` (force the next empty
run directory), `--broken` (show the honest NOT_READY refusal),
`--no-serve` (headless evidence generation). Stop with Ctrl+C; the
service stops gracefully and a rerun of the same command resumes.

## Security and deployment boundary

Local fixture use only. The server binds loopback and refuses anything
else; there is no authentication, so it is not safe for public or
facility-network exposure; production authentication/enrollment is a
separate gate. No Vercel or cloud deployment exists, no credentials
are involved, and no CORS headers are emitted.

## Physical execution boundary

`tests/site_agent/test_architecture.py` mechanically enforces the
boundary: a stdlib-import whitelist (no socket/urllib/subprocess/os/
time/uuid/random), exactly three approved first-party surfaces
(`nxt_agent_runtime`, `nxt_pilot_ops`, `nxt_workflow_enablement`),
execution-token and LLM-pattern bans, no-mention rules for every other
first-party package, a reverse guard that no existing package imports
the service, transport/robot import bans on the composition scripts,
and an import probe that loads the package with the simulator/robot
stacks blocked. The console's `tests/boundaries.test.ts` bans Python
imports, robot-command vocabulary, hidden browser persistence, and any
API surface outside `/api/v0/`. An observed `estop_latched` value in
canonical evidence remains read-only telemetry.

## Current limitations and next seams

- Fixture-backed only: no physical sensor, transport, or live facility
  connection; no production authentication; no public deployment; no
  autonomous dispatch; no customer performance evidence; no LLM in any
  decision or safety loop.
- One workflow (`range.closed_loop_collection_handoff`) and one
  primary live-state concept (dispenser inventory). Grounds Condition
  Intelligence and Player Caddy Experience remain registered,
  independently gated, and NOT_READY, and acquire no service, state,
  or evidence.
- POSIX-only, single site, single process, manual/fixture-advance
  cadence (a scheduler would live in this application boundary, not in
  the runtime).
- The exact next seam for real input is the source composition in
  `scripts/site_agent_fixture.py`: replace the fixture feed with a
  transport reader that emits the same raw-sample shapes and the same
  peek/acknowledge/reject-with-sequence-reuse cursor with a resume
  hook, then compose it through the same
  `assemble_range_ops_runtime(..., source=...)` call. The service,
  API, console, workflow gating, and recovery do not change. See
  [`cto_hopper_sensor_integration.md`](cto_hopper_sensor_integration.md);
  that transport remains a separately reviewed high-risk boundary.

## Verification

From `simulation/` with the all-extras environment:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider  # full suite
uv run --no-sync python -B scripts/validate_configs.py
```

From `apps/site-agent-console/`: `npm ci`, `npm run typecheck`,
`npm run lint`, `npm test`, `npm run build`, `npm run smoke`,
`npm audit --omit=dev`.
