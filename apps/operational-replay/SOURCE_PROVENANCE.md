# Source provenance

## Recovered identity

- Original commit: `810580ae016b0c3c7820a035b795e8cb49331814`
- Original subject: `Build read-only replay storytelling layer`
- Original tree: `2bc0089846b7644f18b322ca2079dda11ae497ca`
- Supplied source: local ChatGPT Sites artifact checkout with no configured Git
  remote
- Hosted visual reference: `https://nxt-replay-story.mtxw.chatgpt.site`

This migration verified the supplied commit, subject, and tree directly. It did
not rely on historical bundle or archive checksums that were not present beside
the supplied checkout.

## Import and adaptation method

The standalone root commit was not cherry-picked and no unrelated Git history
was merged. Its clean source was inspected from the verified commit tree, then
the replay UI, styles, page shell, metadata, lockfile, and original preview
asset were relocated under `apps/operational-replay/`. Text files were adapted
in place for the canonical repository.

The Sites-specific hosting identity, Cloudflare/vinext worker, optional
database example, authentication helper, and unused migration scaffold were
excluded. The app now uses the standard Next.js runtime and has no runtime
dependency on the private Sites repository or the historical legacy source
repository. The recovered social preview was initially imported byte-for-byte,
then retired and replaced as recorded below.

`SOURCE_FILE_MAP.json` accounts for every file in the recovered 25-file commit.
Three untracked Finder-style duplicate files in the supplied checkout were not
part of that commit and were not imported.

## Public social-preview replacement

Issue [#4](https://github.com/matthewong1210/nxtektal-systems/issues/4)
tracked the lack of public/commercial-use clearance for the recovered
`public/og.png`. Embedded metadata was not treated as rights clearance.

### Retired recovered asset

- Path: `public/og.png`
- Dimensions: `1659 × 948`
- Size: `1,679,582` bytes
- SHA-256: `340b52286f97ae5fd8ea80bd60333dfdc2548f43a4201f0de03602c83f3c2606`
- Disposition: replaced in full. The recovered bytes are not present in the
  current application tree and are not an input to the replacement generator.
  The hash remains here solely as disposition evidence and in Git history.

### Active repository-authored asset

- Source artwork:
  `assets/social-preview/operational-replay.svg`
- Capture implementation:
  `scripts/generate-social-preview.mjs`
- Repository design references:
  `lib/edge-gateway-model/manifest.ts` and
  `components/edge-gateway-3d/GatewayCanvas.tsx`
- Output: `public/og.png`
- Dimensions: `1200 × 630`
- MIME: `image/png`
- Size: `310,600` bytes
- SHA-256: `9d715678dd5910471591e7c45bd2ca7c9d88178355d0829e655b641e10d844eb`
- External content/runtime dependencies: none

The SVG and capture script are original NXTektal repository work authored for
issue #4 under project direction. The enclosure line art uses only the
repository's own conceptual procedural-geometry vocabulary and palette; it is
not manufacturing CAD or physical truth. The source contains inline SVG
primitives and NXTektal text branding only. It contains no recovered-image
pixels, generative-image output, stock or scraped imagery, customer data,
external logo, model, texture, linked resource, data URI, font file, or
third-party content. Text uses the local system-font stack, and no font program
is embedded or redistributed. On that original-work and no-external-content
basis, the replacement is designated for NXTektal repository, public-preview,
and commercial use.

Generate the PNG from the application directory with:

```bash
npm run preview:generate
```

The script uses Node.js built-ins and a locally installed Chrome/Chromium. It
validates the self-contained `1200 × 630` SVG, disables browser background
networking, captures at device scale `1` in two isolated profiles, and refuses
the output unless both PNG byte streams match. The committed capture used
Node.js `22.23.2` and Google Chrome `151.0.7922.169` on macOS `26.2` (`arm64`).
System-font rasterization can vary across operating systems, so the committed
SVG is the reviewable artwork authority and the PNG hash above is the active
byte identity. The PNG is self-contained and requires no runtime or external
request to render.

## YC scan-style presentation asset

- Output: `public/yc-site-schematic/range-scanned-demo.webp`
- Dimensions: `1448 × 1086` (4:3)
- MIME: `image/webp`
- Size: `214,174` bytes
- SHA-256: `b70cbdc6e93502eead3d150162f86c9d2836a504fea6c07daf806d9ce27e6e05`
- Source: one user-supplied driving-range photograph used as the edit target;
  the untouched source photograph is not committed
- Transformation: project-directed generative style transfer into a dark,
  lightly abstracted scan-style range scene
- Metadata: the WebP was encoded with metadata disabled; ICCP, EXIF, XMP, GPS,
  camera-make, and camera-model fields are absent
- External runtime dependencies: none

This image is a fixed presentation derivative, not actual scan output, surveyed
geometry, SLAM output, a live digital twin, live tracking, or autonomous
navigation data. The route overlay and marker motion are separately authored
browser presentation elements and do not establish physical robot position or
execution evidence.

## Architecture status

This app owns presentation and deterministic parsing only. Its inputs are
read-only projection artifacts. `RangeSimulation`, `BallLedger`,
`FacilityState`, telemetry assembly, Site Runtime, facility advice, Shadow Ops,
Agent Runtime, the twin, and execution interfaces retain their existing owners
and contracts.

The Edge Observation Adapter Kit V0 is merged. Transport-neutral observation
conversion is implemented for deterministic, fixture-backed, already-read
samples. It covers already-read load-cell and digital-I/O samples plus
already-received robot status. It consumes commissioned binding projections,
validates calibration identity and adapter profiles, emits canonical
`Observation` values plus a separate local `EdgeAdapterReport`, and provides
bounded in-process at-least-once fixture-feed semantics. A composition root adds
five required simulation-only facility-system Observations and fixture
upstream/source references before integrating the complete frame with Site
Runtime and Agent Runtime; the report does not enter that frame, and this browser
app neither imports nor runs those Python packages.

Agent Runtime V1 composes Site Runtime's quality-gated publication and invokes
Shadow Ops evaluation; its separate evaluation-lifecycle evidence supports
checkpoint/recovery and its status surface is read-only diagnostics. Shadow Ops
retains the later manager workflow. Live physical transports and device
connectivity remain unimplemented. Edge Gateway production deployment,
device/certificate enrollment, production OTA, physical command admission,
robot or actuator execution, and an installed or certified physical safety
integration also remain unimplemented. The user-authored dispatch storyboard is
not presented as adapter, Site Runtime, or Agent Runtime fixture output.

The app preserves observed state, risk output, recommendation, simulated task
evidence, and recorded simulation outcome as separate stages. It does not infer
physical execution or causality from temporal proximity.
