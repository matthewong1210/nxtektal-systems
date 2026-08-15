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

Agent Runtime V1 is merged and implemented for deterministic synthetic or
fixture-backed observations. It composes Site Runtime's quality-gated
publication and invokes Shadow Ops evaluation; its separate
evaluation-lifecycle evidence supports checkpoint/recovery and its status
surface is read-only diagnostics. Shadow Ops retains the later manager
workflow. This browser app neither imports nor runs it. Physical telemetry
adapters, production OTA, device enrollment, physical command admission, robot
execution, and an installed physical safety integration remain unimplemented.
The user-authored dispatch storyboard is not presented as Agent Runtime fixture
output.

The app preserves observed state, risk output, recommendation, simulated task
evidence, and recorded simulation outcome as separate stages. It does not infer
physical execution or causality from temporal proximity.
