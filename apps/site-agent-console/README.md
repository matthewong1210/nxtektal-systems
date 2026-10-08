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
from the validated `nxt-pilot-dispatch/service-capabilities/v1` or `/v2` block; a
missing block is `UNDECLARED` and grants no write. The explicit
`LEGACY_PILOT_DISPATCH` mode retains the legacy wall-clock scheduling rule,
while `FIXED_V3_EXECUTION` identifies the simulator-backed session.
Each of the eight declared write operations gates its own control and
handler: a withheld operation is disabled with the declared reason and a
directly dispatched submit is refused before any request; `UNDECLARED` keeps
records readable and withholds every write; SUPPORTED still passes the shared
15-second health gate, request validation, busy state and record-level
preconditions such as a notification's `can_resolve`.
Continuous collection v4 adds the `nxt-pilot-dispatch/service-capabilities/v2`
declaration with its single mode `CONTINUOUS_V3_EXECUTION`, shown as
`CONTINUOUS V3 SESSION`. Its frozen v2 matrix installs the four Planning writes
(operating input, plan, confirmation, outcome), pending schedule cancellation
and notification acknowledgement and resolution, and withholds only direct
schedule creation: a confirmed Planning plan creates the bound schedule, so the
schedule form stays disabled with exactly that reason while the cancellation
form of a pending schedule remains available. Under this mode the execution
panel shows every execution record of the session as its own card in the
service's array order — never re-sorted by state, time or priority — so a
SUCCEEDED history record and a RUNNING or PENDING record appear together, and a
terminal record never marks the session ENDED; the session state comes only
from the snapshot. The panel stays entirely read-only: its only control
re-reads the projection, and it adds no start, stop, rerun or recover action
and no execution POST. A declared SUPPORTED operation still passes the shared
15-second scheduler health gate, the busy state, request validation and each
record's own conditions, and a Planning request with an unknown outcome keeps
its request ID, body and draft through capability, health and freshness changes
and is recovered GET-first by that ID. The tests read the backend-generated
two-task witness
`simulation/tests/fixtures/continuous-collection-v4/two-task-active.json`
directly through the same strict parser.
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
outcome keeps its ID and content and can always be queried by that ID. If the
query proves it absent, the identical request is replayed only when its original
operation is still installed and the latest scheduler health allows writes;
otherwise it stays UNKNOWN with the blocker explained. A recovered receipt is
never treated as evidence that the scheduler is running. Nothing in the console
restarts the runner.

## Staffing advisory pilot

The staffing panel is a local, advisory-only evidence surface. It imports a
reviewed roster, records same-day staff exceptions, shows model-generated
coverage candidates, and records the manager's accept, modify or reject
response. A proposal and its manager response are not an HR schedule, labor
compliance proof, employee notification, robot task or execution command. Every
separate operational action remains the manager's responsibility. The manager
label is self-declared attribution, not authenticated identity.

Staffing is enabled only when the integrated continuous-collection service is
started with `--staffing-state-root`. The service stores its durable ledger at
exactly
`<state-root>/<site_id>/<deployment_id>/staffing-v1/`. Choose an absolute,
access-controlled location on durable local storage, separate from `--out`.
Changing or initializing `--out`, restarting the Site Agent, and fixture reset
must not be used to remove or replace this stable staffing evidence. Omitting
`--staffing-state-root` disables staffing while leaving the existing service
available. The full launch, shutdown and backup procedure is in the
[Staffing Advisory Pilot Runbook](../../simulation/docs/staffing_advisory_pilot_runbook.md).

Provider routing is deployment configuration, never a browser setting:

- `--staffing-region CN` uses Kimi only, configured with `--kimi-model` and the
  `MOONSHOT_API_KEY` process environment variable. It has no cross-region
  fallback.
- `--staffing-region GLOBAL` uses OpenAI as primary, configured with
  `--openai-model` and `OPENAI_API_KEY`. An approved Anthropic backup can be
  configured with `--anthropic-model` and `ANTHROPIC_API_KEY`; it is considered
  only for the gateway's bounded fallback cases.
- `--staffing-language {zh-CN,en}` selects the fixed prompt language and
  defaults to `zh-CN`. The console cannot change provider, model, endpoint,
  prompt, timeout, token cap or fallback policy.

Provider configuration is optional for local work: roster import, exception
recording and correction, reads, and manager evidence remain usable without a
working model route. Advisory generation is bounded to one active request plus
four waiting requests per site; it does not block those local operations.

Use the checked-in [CSV template](public/staffing-roster-template.csv). The
browser accepts at most 512 KiB of strict UTF-8 RFC 4180 CSV, validates the
closed row grammar, shows a preview, and sends nothing until the operator
confirms it. The normalized JSON request is capped at 1 MiB before fetch, and
the service revalidates the complete request; browser checks are convenience,
not authority.

Every staffing time (exceptions, adjustments, coverage gaps, the service
clock) is rendered in the deployment's site timezone from the snapshot context;
the exact wire instant stays on the element for audit. Candidate text shown to
the supervisor is the service's local reading (`rationale_local`,
`operational_warnings_local`) in which provider aliases are already replaced by
display names and shift labels; the raw provider text stays available in a
collapsed audit view, and the console never sees alias maps. A candidate whose
shifts have ended arrives as `actionability: EXPIRED` and is shown under a
historical label with no accept, modify, or reject control; the service refuses
such a response with `staffing_suggestion_expired`. Manager result, plan, and
candidate statuses carry Chinese labels beside their unchanged contract values.

Every write receives a new request ID immediately before submission. If a
response is lost, the console first recovers the same operation by
`GET /api/v1/staffing/requests/{operation-kind}/{request-id}`; a busy response
may retry the identical body under that same ID. `RESULT_UNKNOWN` is different:
after explicit manager approval, generation retry uses a fresh request ID and
sets `retry_of` to the earlier `suggestion_id`, never to its request ID. The old
and new receipts remain separate evidence.

The integrated service binds to loopback and has no authentication. Run it only
on a controlled operator computer; do not proxy, tunnel or publish it to a LAN
or the internet. The local ledger, API, console and backups contain real
identity and free text even though provider-bound data is minimized and
pseudonymized, so the stable root and backups require access control and
encryption.

`happy-dom` is a development-only dependency used by
`tests/planning-interaction.test.tsx` to mount the real panels and drive the
real pollers under fake timers; it ships nothing to the static export.

A minimal, decision-first Manager Console for the local fixture-backed
Pilot Site Agent service. The console is a static Next.js export served
same-origin by the Python service. Successful static responses enforce
`Content-Security-Policy: connect-src 'self'`; source boundary tests remain
defense in depth rather than the runtime network boundary. The console
consumes only the versioned local
Manager API (`/api/v0/`, `/api/v1/planning`, `/api/v1/course-ops`, the
read-only `/api/v1/collection-executions`, and `/api/v1/staffing`),
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
`/api/v1/collection-executions`, and advisory-only `/api/v1/staffing`
contracts. It also freezes the three exact production dependencies and applies
the stricter persistence, external-URL and command-vocabulary checks directly
to the staffing panel, staffing inputs, CSV parser, action builder, manager
label guard and same-origin staffing client.
