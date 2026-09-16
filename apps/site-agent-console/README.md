# NXTektal Site Agent Console

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

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
network URLs, and any API path outside `/api/v0/`.
