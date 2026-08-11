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

## Deployment

The canonical repository has no root JavaScript workspace and this migration
adds no host-specific deployment binding. Configure a host's project/root
directory as `apps/operational-replay`, install with `npm ci`, build with
`npm run build`, and serve with `npm run start`. Set
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

```bash
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm run smoke
npm audit --omit=dev
```

See [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md) for the recovered source
identity and deterministic import map.
