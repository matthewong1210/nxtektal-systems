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
the replay UI, styles, page shell, metadata, lockfile, and preview asset were
relocated under `apps/operational-replay/`. Text files were adapted in place
for the canonical repository.

The Sites-specific hosting identity, Cloudflare/vinext worker, optional
database example, authentication helper, and unused migration scaffold were
excluded. The app now uses the standard Next.js runtime and has no runtime
dependency on the private Sites repository or the historical legacy source
repository. The social preview image remains byte-identical to the recovered
commit.

`SOURCE_FILE_MAP.json` accounts for every file in the recovered 25-file commit.
Three untracked Finder-style duplicate files in the supplied checkout were not
part of that commit and were not imported.

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
