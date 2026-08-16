# Edge Gateway 3D demo recording guide

This guide produces a local YC review capture of the presentation-only Edge
Gateway route. It does not authorize public deployment.

## Production run

From the repository root:

```bash
cd apps/operational-replay
npm ci
npm run build
npm run start
```

Use these local URLs:

- manual demo: `http://localhost:3000/edge-gateway-demo`
- automatic presentation:
  `http://localhost:3000/edge-gateway-demo?presentation=1`

After the production build completes, the repeatable browser check and its
uncommitted screenshots can be generated outside the repository with:

```bash
node tests/edge-gateway-demo/browser-verify.mjs --output-dir "$(mktemp -d)"
```

The route's page metadata deliberately omits the unresolved `public/og.png`.
The root asset remains untouched and continues to block public deployment.

## Capture setup

- Set the browser viewport to `1440 x 900` CSS pixels at `100%` browser zoom.
- Record the production build, not the development server.
- Use the automatic presentation URL and target a `75`-second capture. A final
  duration anywhere from `60` to `80` seconds is acceptable.
- Before recording, restart the presentation and confirm that pause, restart,
  manual stepping, reduced motion, and the no-audio experience all remain
  usable.
- Keep both disclosure labels visible whenever their relevant material is on
  screen. Do not crop them out.
- Close notifications and unrelated tabs. Do not expose local paths, account
  details, customer information, or developer tooling in the capture.

## Truthful 60-80 second narration

The following script is paced for approximately 75 seconds. Read the quoted
copy; the notes after each segment are not narration.

**0-8 seconds - Installed System**

> This conceptual, not-for-fabrication view shows the proposed NXTektal Edge
> Gateway installed beside existing range equipment.

**8-20 seconds - Open and exploded enclosure**

> Opening the enclosure reveals repository-authored approximate geometry: an
> edge computer, router, remote I/O, protected power, network switching, and
> structured terminals.

**20-40 seconds - Operational Flow**

> The implemented Edge Observation Kit converts deterministic, already-read
> load-cell, digital-I/O, and received robot-status fixture samples into
> canonical Observations. Its diagnostic report stays separate local evidence.
> Fixture composition adds required simulation-only channels and upstream
> references before Site and Agent Runtime. Live device transport is
> unimplemented, and manager acceptance does not cause the separate RangeOps
> replay.

**40-52 seconds - Scale the Fleet**

> Fleet onboarding, certificates, capabilities, and live transport-adapter
> loading are future workflow concepts. The utilization numbers are
> illustrative, not capacity measurements.

**52-64 seconds - Software Update**

> The signed update and rollback sequence is also a target operating concept,
> not a production OTA service implemented by this repository.

**64-75 seconds - Safety Architecture and overview**

> Finally, local emergency-stop hardware remains independent of the Agent and
> normal I/O. This visualization claims neither deployed integration nor safety
> certification.

Do not substitute narration that describes manager approval, admission, a
typed mission, a robot Adapter, robot motion, or a stored outcome as one
implemented causal chain. The repository owns advisory evidence and a separate
simulator replay; it does not contain a production advice-to-physical-execution
bridge.

## Silent-demo alternative

Record the same presentation URL with the microphone and system audio disabled.
The timed scene titles, inspector copy, boundary labels, and disclosures make
the presentation understandable without narration. Let the sequence complete
once without pointer movement; use pause or manual stepping only if the viewer
needs more time on a boundary label. Reduced motion may be enabled before
restart without changing the story's ordering.

## H.264 export

Export an MP4 using H.264 at the captured `1440 x 900` resolution and `30 fps`.
Use an `8 Mb/s` target video bitrate, AAC at no more than `128 kb/s` when the
narrated recording has audio, and no audio track for the silent version. At 75
seconds, those settings leave useful headroom below 100 MB. Enable web/fast
start when the recorder offers it.

Verify the final file is under `100 MB` before sharing it. If it is larger,
re-export at `6 Mb/s`; do not shorten the disclosures or remove the truth
labels to reduce size. Keep review videos outside the repository and do not
commit them.

## Screenshot capture

Capture PNG screenshots at `1440 x 900`, `100%` zoom, from the production
build. Use these review frames:

1. Installed Gateway
2. Open enclosure
3. Exploded view
4. Selected Edge Computer
5. Operational data flow
6. Recorded manager response
7. Advisory stop boundary and separately labelled simulation replay
8. Fleet expansion
9. Staged software update
10. Failed health check and rollback
11. Independent safety architecture
12. Forced WebGL-unavailable fallback at a mobile viewport

Narrow devices use reduced-quality WebGL when it is available. For the fallback
frame, use one of the tested narrow viewports and explicitly disable WebGL, then
capture the accessible text diagram and parts list. Confirm that every frame
retains the relevant conceptual or simulated disclosure. Store screenshots
outside the repository unless repository policy later requires checked-in
evidence.

## Product-truth checklist

Every recording and screenshot must preserve these exact disclosures:

- `CONCEPTUAL SYSTEM VISUALIZATION — NOT FOR FABRICATION`
- `SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA`

The capture must not imply manufacturing readiness, a final electrical design,
UL or production safety certification, live customer deployment, measured
customer savings, measured Gateway capacity, autonomous physical execution, or
an implemented safety installation. Static values and motion are illustrative.
The Agent cannot control a robot or bypass local safety through this route.
Observation adapters are implemented and fixture-backed: deterministic,
already-read load-cell and digital-I/O samples and already-received robot status
are converted into canonical Observations using commissioned bindings and
validated profiles, with explicit missing, stale, and fault diagnostics. The
bounded fixture feed has in-process at-least-once semantics, and a composition
root keeps the diagnostic report separate while adding five required
simulation-only facility-system Observations and fixture upstream/source
references before the complete frame reaches Site Runtime and Agent Runtime;
the browser does not run that path. Transport-neutral observation conversion is
implemented for deterministic, fixture-backed, already-read samples. Live
physical transports and device connectivity remain unimplemented, as do Edge
Gateway production deployment, device/certificate enrollment, production OTA,
physical command admission, robot or actuator execution, and installed or
certified safety integration.
