# Pilot Dispatch Console V0

SIMULATION ONLY — local protocol doubles, no physical device or customer data.

## Run the console

From the repository root, build the existing static console:

```bash
cd apps/site-agent-console
npm ci
npm run build
cd ../../simulation
uv sync --locked --all-extras
uv run --no-sync python -B scripts/pilot_dispatch_demo.py \
  --out reports/pilot-dispatch-v0 --initialize --port 8766
```

Open `http://127.0.0.1:8766`. Enter a future date/time, an operator identifier,
and a task validity window. The task section refreshes every two seconds. The
facility/advice panels below remain separate, manually advanced fixture data.
The task-created status does not imply robot acceptance; inspect the task result.

Stop with Ctrl-C. Restart with the same command **without `--initialize`** to
recover evidence. Initialization refuses an existing directory. Do not delete
evidence to recover a task; missing or corrupt device records require investigation.
To rehearse assistance in a separate new directory, use
`--behavior help_needs_manual_recharge` at first launch. The local inbox can
record acknowledgement; active uncertainty cannot be resolved by a note.

This integration adds six Edge journal record kinds. The wire messages are
unchanged, and older readers deliberately reject the new journal kinds. Keep
this rehearsal evidence separate from earlier PR A evidence; no downgrade or
migration of evidence is claimed.

## Architecture decision: PROCEED

The user authorized the first software loop on 2026-09-16: schedule a collection
task, observe its progress, surface exceptions, and record human handling.
This slice composes PR #12 (`4b86d9f`, Site Agent service/console) and PR #16
(`0c23f90`, Edge Task), on main `2fbccb0`, in an isolated integration branch.
Those upstream PRs remain unmerged; this is not evidence of a field deployment.

The duplication search found existing owners for each responsibility. Extend
`nxt_edge_task` for dated rehearsal schedules and local task notifications, reuse
its locked, anchored journal, and reuse its task admission and protocol doubles.
Do not create another facility policy, mutable facility state, or core package.
The console remains a projection; `nxt_site_agent` remains independent of the
task package. A composition-root callback adds the narrowly scoped
`/api/v0/task-ops` routes behind the existing loopback/origin/body protections.
Recommendation acceptance continues to record advice workflow only.

## Boundary card

| Fact or behavior | Owner |
|---|---|
| Fixture facility state and recommendations | Existing Site Runtime / Agent Runtime / Shadow Ops |
| Simulated device/task evidence and admission | Existing `nxt_edge_task` |
| One-time dated schedule and human inbox records | `nxt_edge_task`, in the same Edge journal |
| Clock, loop, process lock and protocol transport composition | `scripts/pilot_dispatch_demo.py` |
| Versioned HTTP envelope and loopback protection | Existing `nxt_site_agent.api` |
| Display, form draft and last successful refresh | `apps/site-agent-console`; no browser persistence |

Neither sibling package imports the other. The new runner is the only integration
composition root; no recommendation, memory, map or fixture observation produces
a task. The wire environment remains the single value `SIMULATION`.

Schedules store UTC instants and expire explicitly. They are not a recurring
calendar. Before the due instant they cannot create a task. At the due instant
the same journal lock protects schedule lookup, current device freshness,
existing admission, and task creation. The task-created record itself carries
the schedule identity, so a crash between lines cannot create a second task.
Expired or rejected schedules are visible; neither is silently rescheduled.
Cancelling an unissued schedule does not stop a running task.

Notifications are a durable local operator inbox derived from task evidence.
Acknowledging or recording a resolution cannot alter robot/task truth, clear
authorization gates, or restart an uncertain task. Conditions and operator
records remain separate. No email, SMS or other external recipient is configured.

The protocol runner uses the existing in-memory transport double by default.
The original real local Mosquitto integration suite remains required evidence
for the unchanged wire path. All times in domain code are caller supplied;
the clock and background loop live only in the composition script. Storage or
loop failure stops scheduling and becomes an API/UI error. A process lock
prevents two runner instances sharing evidence.

## Verification contract

Exercise future/due/expired schedules, duplicate HTTP requests, restart and
torn-write idempotency, stale/offline/busy admission, cancellation races,
exception acknowledgement/resolution boundaries, loop failure, and same-origin
HTTP protections. Run the existing full Python suite (all extras), real local
Mosquitto tests, console tests/typecheck/lint/build/smoke, and browser flow.
Results are recorded in the handoff, never inferred from earlier PR totals.

## Field integration still required

CE82A needs the vendor's dated interface specification and device access. Verify
task identity/idempotency, supported actions, status/result evidence, network
loss behavior, local protection, cloud dependency, software versions, and the
actual unloading/energy workflow before implementing a physical adapter.
Carrier-01 remains standby only. This slice does not implement two-robot handoff,
automatic unloading/charging, a physical cancellation command, field networking,
authentication, a hardware watchdog, or production operation.

## Human-led planning v1 extension

The same runner now injects a separate `/api/v1/planning` callback. See the
[shared contract](contracts/planning-v1/README.md) and
[architecture gate](planning_v1_architecture.md). Launch command and loopback
scope are unchanged. Before the console is integrated, start the API alone:

```bash
uv run --no-sync python -B scripts/pilot_dispatch_demo.py --out /tmp/planning-demo --initialize --api-only
```

Use a new empty evidence directory for first launch; restart with the same
`--out` and omit `--initialize`. GET starts with null input and empty arrays: no fixture
inventory or demand is used as manual planning evidence. Planning identity and
timezone are bound to the validated task rehearsal manifest; the old state and
advice panels remain a separately labelled fixture deployment.

A durable explicit confirmation binds one version to one frozen schedule. Tick
recovers unfinished materialization before transport work; due-time admission
uses a gate over the same locked journal prefix. `nxt-edge-schedule/v2` adds
only the opaque admission reference. Missing gate fails closed. Legacy unbound
v1 bytes remain unchanged; older software does not understand new records and
must not be used to run an evidence directory after its first v1 planning write.
Use a new evidence directory to run the old demo; do not delete history.

The existing `/api/v0/task-ops/schedules` endpoint cannot forge a bound schedule.
Cancellation remains the existing pending-only operation. It never stops an
admitted task or resets confirmation uniqueness. Confirmation is recorded before
materialization; a temporary null schedule status is pending linkage, not a
failed confirmation. After unknown response, query/retry the same request ID.

COLLECTED, UNLOADED, WASHED and SUPPLIED each require explicit quantity/source/
time evidence. The picker double does not perform a ball process or automatically
provide those quantities. No task completion can add clean stock.
