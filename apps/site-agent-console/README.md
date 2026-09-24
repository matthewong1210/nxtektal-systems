# NXTektal Site Agent Console

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

Collection execution v1 adds a read-only execution panel between the planning
panel and the task panel. It consumes only the GET-only
[`collection-execution-v1` contract](../../simulation/docs/contracts/collection-execution-v1/README.md)
(`GET /api/v1/collection-executions`) through the Codex-owned strict parser in
`lib/collection-executions.ts`, and shows each bound task's admission lower
bound, durable request, device acceptance, simulated job start (which may
still be travel), collection and unloading, with both ledger quantities,
exit reasons, device protection and Edge verification. The browser adds no
button that starts, retries, recovers or stops an execution. Success is shown
only when the contract's `success_display_allowed` flag is set; balls on the
robot are a milestone, never completion; washing, supply and manager-recorded
outcomes stay independent. Missing evidence stays unknown (`INCOMPLETE`),
`NOT_REACHED` is the contract's own proof that a milestone did not occur, and
zero is never invented. A response the parser rejects is a read failure: the
last valid snapshot stays on screen marked stale with its age. The session
state (`ACTIVE`/`PAUSED`/`ENDED`) and simulation clock are shown apart from the
wall-clock service read, and a successful read grants no execution authority.
A round change resets only this panel's own display state; the planning
panel's pending requests and recovery state are untouched. The read health
expires on its own 15-second wall clock even while a read keeps waiting or the
session is PAUSED. While that read is fresh, the task panel's schedule form
compares dates with the shared simulation clock
(`collectionExecutionSimulationNow`) instead of the wall clock; a stale
reading disables scheduling. Service mode and write availability come only
from the validated `nxt-pilot-dispatch/service-capabilities/v1` block; a
missing block is `UNDECLARED` and grants no write. The explicit
`LEGACY_PILOT_DISPATCH` mode retains the legacy wall-clock scheduling rule,
while `FIXED_V3_EXECUTION` identifies the simulator-backed session.
The task panel names its device source only as the service reports it and
does not infer a device kind from the transport. The integrated V3 service
serves the static console and the read-only execution route from one process;
its tests also retain the frozen SIMULATION examples and backend-generated
normal-loop witness fixture.

Whole-course monitoring v1 adds a read-only panel above the pilot controls.
It shows 54 inspection points across 18 synthetic holes, the last observed
positions of up to 16 carts, saved synthetic camera images/detections, condition
cases and recorded work. New reports also expose the driving range's existing
observations of ball counts, robots and staff. Older reports leave these fields
UNKNOWN. The map is a checkpoint schematic, not surveyed course geometry.

The panel consumes only the
[`course-ops-v1` contract](../../simulation/docs/contracts/course-ops-v1/README.md).
It distinguishes saved simulation time, source pause flags and API read time;
successful polling never makes a saved frame live. Missing/broken reads keep
the last snapshot marked stale, and unavailable images have an explicit retry.
The pilot controls below run whatever task composition the service provides
(a protocol rehearsal or the simulator-backed V3 execution session); they
never act on this saved V2 report.

The integrated dated-task rehearsal adds a task panel above the fixture panels:
single-date collection schedules, automatic task/device refresh, and a local
operator inbox. Follow the [Pilot Dispatch Console guide](../../simulation/docs/pilot_dispatch_v0.md)
to start both in one local service. The original fixture runner still works and
shows task operations as unavailable. Task creation is separate from advice
acceptance; neither runner can operate physical equipment.

Manager planning v1 adds a human-led planning panel above the task panel.
It consumes only the frozen contract in
[`simulation/docs/contracts/planning-v1`](../../simulation/docs/contracts/planning-v1/README.md)
(`GET /api/v1/planning`, the four `POST` writes and `GET ./requests/{id}`):
the manager records operating input with per-field evidence (measured or
manual estimate, source, observation time, validity), asks the service for
three manual demand scenarios and a suggested plan, adjusts zone, robot and
start time as new plan versions, confirms one exact version (which creates one
simulated schedule through the existing task admission) and records the four
result stages independently. The browser computes no stockout or ranking,
persists no effective setting, generates one stable request ID per write,
recovers an unknown outcome only by that ID, and never turns advice into a
task. Times are entered and shown in the site timezone from the planning
context and transported as UTC. Operator names are attribution, not
authentication.

The planning panel and the task panel share one scheduler reading. The task
panel's existing poller (`GET /api/v0/task-ops`, validated by the same parser)
is mounted once in `components/PilotOperations.tsx`; its `scheduler.state/detail`
becomes a freshness-labelled health value (`lib/scheduler-health.ts`) that the
task panel displays and the planning controller uses as its write gate. A
planning write is allowed only over a fresh RUNNING reading; FAILED, a failed
read, an unverifiable route or a reading older than 15 seconds block new
planning writes with the runner's own reason. A request with an unknown
outcome keeps its ID and content and can still be queried and replayed by that
ID; a recovered receipt is never treated as evidence that the scheduler is
running. Nothing in the console restarts the runner.

`happy-dom` is a development-only dependency used by
`tests/planning-interaction.test.tsx` to mount the real panels and drive the
real pollers under fake timers; it ships nothing to the static export.

A minimal, decision-first Manager Console for the local fixture-backed
Pilot Site Agent service. The console is a static Next.js export served
same-origin by the Python service; it consumes only the versioned local
Manager API (`/api/v0/`, `/api/v1/planning` and `/api/v1/course-ops`),
imports no Python Site OS package, and holds
no authoritative state — after any refresh or restart it reconstructs
its entire view from the API and the service's persisted evidence.

The default screen answers, in order: is the Site Agent running, is the
workflow ready, is the data fresh and trustworthy, what is the current
dispenser inventory, is there an exception, is there a recommendation
waiting for review and why, what has the manager decided, and what
happened during the shift. Fixture controls are visually and
semantically separated from real manager decisions and exist only in
fixture mode.

## Run locally

Build the static export once, then run the Python service pointing at
it (see the service documentation in
[`simulation/docs/site_agent_v0.md`](../../simulation/docs/site_agent_v0.md)):

```bash
npm ci
npm run build
```

```bash
cd ../../simulation
uv run --no-sync python -B scripts/site_agent_demo.py \
  --out reports/site-agent --port 8765 \
  --console ../apps/site-agent-console/out
```

Then open `http://127.0.0.1:8765/`. `npm run dev` serves the UI shell
alone for styling work; without the local service it shows the honest
"Service Unreachable" state, which is itself a supported screen.

To attach an existing whole-course V2 series, build the same console export,
then run from `simulation/` with the path of that saved series:

```bash
uv run --no-sync python -B scripts/pilot_dispatch_demo.py \
  --out reports/course-console --initialize --port 8766 \
  --course-series reports/whole-course-series
```

`--out` must be a new, separate rehearsal directory on first launch. Restart
with the same arguments but omit `--initialize`. `--course-series` reads saved
artifacts only; it does not create, resume or modify the selected experiment.
The console is served at `http://127.0.0.1:8766/`. Omit `--course-series` to keep
the original runner behavior; the monitoring panel then reports unavailable.

## Security boundary

Local fixture use only. The service binds loopback, has no
authentication, and must not be exposed to a facility network or the
public internet. The console can neither create a robot command nor
reach any physical execution surface; manager acceptance is recorded as
human workflow evidence in the existing ledger and nothing more.

## Verification

```bash
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm run smoke
npm audit --omit=dev
```

`tests/boundaries.test.ts` mechanically forbids Python/ROI/replay
imports, robot-command vocabulary, hidden browser persistence, hardcoded
network URLs, and any API path outside `/api/v0/` and the frozen
`/api/v1/planning`, read-only `/api/v1/course-ops` and read-only
`/api/v1/collection-executions` contracts.
