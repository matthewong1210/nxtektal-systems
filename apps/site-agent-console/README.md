# NXTektal Site Agent Console

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The integrated dated-task rehearsal adds a task panel above the fixture panels:
single-date collection schedules, automatic task/device refresh, and a local
operator inbox. Follow the [Pilot Dispatch Console guide](../../simulation/docs/pilot_dispatch_v0.md)
to start both in one local service. The original fixture runner still works and
shows task operations as unavailable. Task creation is separate from advice
acceptance; neither runner can operate physical equipment.

Manager planning v1 adds a human-led planning panel at the top of the page.
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

A minimal, decision-first Manager Console for the local fixture-backed
Pilot Site Agent service. The console is a static Next.js export served
same-origin by the Python service; it consumes only the versioned local
Manager API (`/api/v0/`), imports no Python Site OS package, and holds
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
`/api/v1/planning` contract.
