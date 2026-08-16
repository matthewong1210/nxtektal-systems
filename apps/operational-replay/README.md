# NXTektal Operational Replay

A read-only browser storytelling layer over NXTektal simulation replay
artifacts. The recovered experience is preserved as a standalone Next.js app,
while artifact generation and operational truth remain in the Python stack.

This app is presentation only. It does not import NXTektal runtime packages,
mutate `FacilityState`, run recommendation logic, issue directives or robot
commands, upload selected files, or claim physical-facility results.

## Run locally

Prerequisite: Node.js `>=22.13.0` and npm.

```bash
npm ci
npm run dev
```

Open `http://localhost:3000` and select replay artifacts with **Load
artifacts**.

Set `NEXT_PUBLIC_REPLAY_SITE_URL` to the deployed origin when validating social
metadata outside local development. It defaults to `http://localhost:3000`.

## Edge Gateway 3D demo

The separately code-split CAD-style presentation is available at:

- `http://localhost:3000/edge-gateway-demo`
- `http://localhost:3000/edge-gateway-demo?presentation=1` for the deterministic
  approximately 75-second presentation

The route is a browser-local, read-only presentation. Its geometry, requested
demand-spike storyboard, fleet utilization, update sequence, and safety diagram
are repository-authored illustrative content; they are not live customer data,
manufacturing CAD, measured capacity, certification evidence, or an interface
to the Python runtime. In particular, the route does not import, run, or connect
to `nxt_agent_runtime`. The clearly separate replay panel consumes a compact,
checked-in excerpt generated from the deterministic `RangeOpsEnv` viewer export
for `normal_weekday`, `inventory_threshold`, seed `101`, at repository commit
`f5ae9e1`. The route cannot mutate that simulation or control a robot, and it
has no external API or LLM dependency.

The architecture decision for the requested operational story is **RESHAPE
THEN PROCEED**. The demo may explain this canonical advisory boundary:

```text
deterministic fixture raw-sample feed (in-process at-least-once cursor)
  + commissioned binding projection + validated adapter-local profiles
  -> Edge Observation Adapter Kit V0 transport-neutral conversion
     |-> separate EdgeAdapterReport diagnostics (local conversion evidence)
     \-> canonical adapter Observations
         + five required simulation-only facility-system Observations
         + fixture UpstreamInputs and SourceReference records
  -> composition root creates the complete sequenced ObservationFrame
  -> Site Runtime ordered input validation, then telemetry-owned assembly
  -> exact FacilityState plus a separate AssemblyReport
  -> Site Runtime publication quality gate and exact admitted envelope
  -> Agent Runtime V1 invokes existing Shadow Ops evaluation / DecisionTrace
  -> Agent Runtime evaluation-lifecycle evidence, checkpoint / recovery,
     and read-only diagnostic status
  -> recorded Shadow Ops manager-workflow response
  -> stop; no command is issued
```

It may then show a separate simulation replay path:

```text
RangeOps policy -> closed Directive -> SafetyShield -> RangeSimulation
  -> recorded states and events -> read-only presentation
```

### Truth boundary and conceptual limits

The diagram names architectural boundaries; it does not mean that the browser
constructs or executes their canonical runtime objects. Agent Runtime V1 is
implemented for deterministic synthetic or fixture-backed observations. Site
Runtime owns input validation and quality-gated publication of the exact frozen
`FacilityState` plus its separate `AssemblyReport`; Agent Runtime composes that
public pipeline with the existing Shadow Ops adapter and Guardian, records one
evaluation outcome per admitted envelope, and provides separate evaluation
checkpoint/recovery, a pending manager-decision view, and read-only runtime
status. Shadow Ops still owns policy, recommendation, trace, and human-workflow
semantics. Broad `nxt_facility` manager advice remains a separate advisory
surface; Agent Runtime neither owns nor reconciles it with Guardian output. The
demo does not merge, rank, reconcile, execute, import, or run any of those
Python contracts.

Current implementation status is split deliberately by layer:

- **Observation adapters — Implemented, fixture-backed.** Transport-neutral
  conversion accepts deterministic, already-read load-cell and digital-I/O
  samples plus already-received robot status. It consumes commissioned binding
  projections, validates calibration identity and adapter profiles, and emits
  canonical `Observation` values with explicit missing, stale, fault, rejected,
  and unmapped diagnostics. The bounded fixture feed provides in-process
  at-least-once delivery semantics.
- **Runtime integration — Implemented for the deterministic fixture path.** A
  composition root combines the adapter Observations with five required
  simulation-only facility-system Observations and fixture upstream/source
  references, then carries the complete frame through Site Runtime and Agent
  Runtime without making the adapter package depend on either runtime. The
  `EdgeAdapterReport` remains separate local conversion evidence. This is not a
  continuously running production site service.
- **Live device transport and Gateway deployment — Not implemented.** Live
  Modbus, serial, MQTT, OPC-UA, and vendor-SDK readers; real facility device
  connectivity; Edge Gateway production deployment; device/certificate
  enrollment; hot transport-adapter loading; and production OTA are absent.
- **Control and safety — Not implemented.** There is no physical command
  admission, robot or actuator execution, or installed or certified safety
  integration.

Transport-neutral observation conversion is implemented for deterministic,
fixture-backed, already-read samples. Live physical transports and device
connectivity remain unimplemented.

The two paths are not a causal implemented chain. In particular, a recorded
manager accept response is not physical command admission; the repository has
no implemented site-level typed-mission bridge from advice to robots. The
existing `RobotTaskInterface` covers only a micro handoff cycle, its mock is a
test double, and the Isaac Sim and ROS 2 adapters are stubs. Robot movement in
the demo is therefore illustrative replay, not an outcome caused by approval.
Operational Memory is append-only observational evidence and does not prove
that advice caused a later outcome.

The checked-in replay excerpt records three exact frames from a 960-step
`nxt-range-viewer/episode/v1` export, including the closed directive, its
`SafetyShield` verdict, and recorded robot state. Its source episode SHA-256 is
embedded in the excerpt. The full 3.1 MB episode is deliberately not bundled
into this route; regenerate it and the compact projection with:

```bash
cd simulation
python -m nxt_range_viewer --out <temporary-output> \
  --scenario normal_weekday --policy inventory_threshold --seed 101
cd ../apps/operational-replay
node scripts/build-edge-gateway-replay-excerpt.mjs \
  <temporary-output>/episode.json \
  lib/edge-gateway-model/fixtures/normal-weekday-inventory-threshold-seed-101.json
```

The exporter disclaimer remains in the checked-in projection: its parameters
have placeholder provenance and are not real facility performance.

Live physical transports and device connectivity, device and certificate
enrollment, hot transport-adapter loading, production OTA activation/rollback,
Edge Gateway production deployment, production publishers or service
scheduling, physical command admission, robot or actuator execution, and the
depicted independent physical safety installation remain unimplemented. Static
site facts remain owned by a validated `CommissionedSite`, not by the scene,
telemetry, or `FacilityState`.

The route always distinguishes the two required disclosures:

- `CONCEPTUAL SYSTEM VISUALIZATION — NOT FOR FABRICATION`
- `SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA`

See [EDGE_GATEWAY_3D_DEMO_RECORDING.md](EDGE_GATEWAY_3D_DEMO_RECORDING.md) for
the YC capture procedure and
[public/models/edge-gateway/README.md](public/models/edge-gateway/README.md) for
asset provenance and the future CAD replacement path.

### Model replacement pipeline

There are no external model or cleared logo assets in the first version; the
header therefore uses text branding only. The route uses
repository-authored procedural geometry whose approximate dimensions and
stable component IDs live in its model manifest. A cleared future asset follows
this offline path:

```text
STEP / SolidWorks / Fusion 360
  -> approved Blender or CAD export workflow
  -> optimized GLB
  -> model-registry entry with the existing component ID and verified scale
```

The live renderer accepts an explicitly registered, same-origin `.glb` or
`.gltf`, verifies stable ID, declared meter scale, manifest dimensions, and
loaded bounds, and constrains dependent resources to the same model directory.
An absent registered asset may use the documented procedural representation.
A declared but malformed or wrongly scaled asset fails visibly and is never
silently replaced with unrelated geometry. Source STEP files must not be
committed unless repository policy and licensing explicitly permit them.

## Deployment

Public deployment remains blocked by
[canonical issue #4](https://github.com/matthewong1210/nxtektal-systems/issues/4)
until `public/og.png` is cleared or replaced. The repository CI workflow is
validation-only and does not deploy this application.

The Edge Gateway route's page metadata deliberately omits `public/og.png` and
does not add another social-image asset. That omission does not resolve or
authorize the existing root asset: issue #4 remains the deployment blocker,
and this demo has not been publicly deployed.

The canonical repository has no root JavaScript workspace and this migration
adds no host-specific deployment binding. After the blocker is resolved,
configure a host's project/root directory as `apps/operational-replay`, install
with `npm ci`, build with `npm run build`, and serve with `npm run start`. Set
`NEXT_PUBLIC_REPLAY_SITE_URL` to the public origin so social metadata resolves
against the deployed site. The historical Sites/Cloudflare project identity is
intentionally not reused.

## Artifact input

The accepted inputs are the read-only files produced around
`simulation/scripts/facility_twin_capture.py`, not the separate
`nxt_range_viewer` bundle (`episode.json`, `layout.json`, and optional
`benchmark.json`). `events.jsonl` is required and must contain at least one
record with non-negative `t_s`, a non-empty event `kind`, and an object
`payload`. The following capture files are optional:

- `facility_states.jsonl`, containing canonical `FacilityState` snapshots, for
  discrete robot locations and terminal simulation outcomes;
- `layout.json` with schema `nxt-range-viewer/layout/v1`, for static map
  geometry;
- `briefings.jsonl`, or a recommendation-named JSONL/TXT file using the same
  timed recommendations-array shape, for advisory output.

`stream.meta.json` is outside the v1 input contract and is rejected as an
unsupported filename.

Files are read in the browser and retained only in page memory. Selecting
`events.jsonl` starts a new selection; later file-picker selections are added
in memory. This lets users choose the three supported files from the capture directory,
then add `briefings.jsonl` from its separate demo directory without copying or
modifying either artifact store. Files are sorted by normalized filename using
locale-independent code-point order; JSONL
records are sorted by recorded simulation time, source name, and original line
number. Syntax-valid records that do not contain the fields consumed by their
named artifact adapter are skipped with a visible warning. Duplicate
filenames, unknown layout schemas, event/state/layout identity mismatches,
files larger than 10 MiB, and selections larger than 30 MiB are
rejected. A selection may contain at most 16 files and 100,000 total records;
each JSONL artifact may contain at most 50,000 nonblank records, each record or
single-object JSON artifact may be at most 256 KiB, and a state,
recommendation, or layout record may contain at most 10,000 consumed nested
items. Limit violations reject the selection with a visible error rather than
silently truncating evidence.

Scenario and seed fields in selected events, states, and layout are cross-checked
when present. They are not signatures and do not cryptographically prove that
all browser-selected files came from one episode. Policy identity remains
explicitly unverified.

Missing optional artifacts remain visible as evidence gaps. The app does not
invent recommendations, task completion, terminal metrics, continuous robot
motion, or a link from advice to an event. Multiple advisory outputs remain
separate; the app has no association, ranking, deduplication, composition, or
conflict-resolution contract. When `layout.json` is absent, the recovered
reference geometry is labeled as context and receives no artifact-backed
markers.

The built-in story is explicitly labeled as a recovered simulation-reference
transcript. Original artifact files are not embedded, and the transcript is not
live AI output or physical-pilot evidence.

## Verification

Run the exact application checks from `apps/operational-replay`:

```bash
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm run smoke
npm audit --omit=dev
node tests/edge-gateway-demo/browser-verify.mjs --output-dir "$(mktemp -d)"
```

The browser verifier runs the production build in installed Chrome, covers all
seven required viewports plus the forced non-WebGL fallback, and writes 19
review screenshots (12 story frames and seven responsive captures) only to the
supplied empty directory outside the repository. To verify an already running
build, add `--base-url <origin>`.

See [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md) for the recovered source
identity and deterministic import map.
