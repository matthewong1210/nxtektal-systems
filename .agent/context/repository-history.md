# Audit baseline and recent repository history

This file records the evidence used to create the operating layer. It is a
dated orientation aid, not a substitute for checking current Git/GitHub state.

Commit identities and pull-request links below refer to the source repository
and are retained verbatim as historical provenance. They do not describe refs
or pull requests created in this standalone repository.

## Current merged baseline before investor documentation refresh: 2026-08-09

- Exact `main` and `origin/main` audited:
  `192292735221e503915f286627dc64f001942881`.
- AI Engineering Operating System PR #23 merged at that commit, versioning
  `AGENTS.md`, the Claude Code entry point, `.agent/` context/workflows/skills,
  and `docs/AGENT_OPERATING_MANUAL.md` with the repository.
- The product merge-train baseline below remains the implementation baseline
  for Shadow Ops, Commissioning, and Site Runtime; PR #23 added governance and
  did not change their production contracts.

## Product merge-train baseline: 2026-08-09

- Exact `main` and `origin/main` audited:
  `b055c9472737feb923c6ac48fad44a5b7e43333c`.
- Shadow Ops PR #19 merged at
  `e84c5016a19d1d4aec0b4b183164c08bba5b164e`.
- Commissioning PR #20 merged at
  `89e93f6a8ea0cd469d6da907321eafe30318fa49`.
- Site Runtime PR #22 merged at
  `b055c9472737feb923c6ac48fad44a5b7e43333c`.
- The three packages coexist in the merged Python distribution and their
  boundary suites are part of the current repository.

## Initial pre-merge audit: 2026-08-09

- Checkout audited: `feature/shadow-ops-v0` at `a24f1a7`.
- `main` and `origin/main`: `eb51c8a`, merged PR #14.
- Shadow Ops was committed on the audited feature branch but not yet merged to
  `main` at that initial audit moment.
- A separate sibling commissioning branch, `feature/commissioning-v0` at
  `260df33` (draft PR #20), implemented `nxt_commissioning`; it was not present
  in that checkout or merged architecture. This remains historical evidence,
  not current status; the finalization baseline above supersedes it.
- The pre-existing untracked `docs/CODEX_ARCHITECTURE_AUDIT.md` contained useful
  recon but stale Shadow Ops status and proposed packages. It was not treated
  as repository authority and was not modified.

## Instructions found before this layer

- No repository `AGENTS.md` or `CLAUDE.md` existed.
- `.claude/skills/3d-asset-generator/SKILL.md` covered only procedural 3D asset
  creation; it did not define engineering architecture.
- `.claude/launch.json` contained local launch configuration, including a
  machine-specific path outside this repository.
- No `CODEOWNERS`, `OWNERS`, or repository-local GitHub Actions workflow was
  present.

## Verified merged PR sequence

| PR | Merged (UTC) | Established responsibility |
|---|---|---|
| [#5](https://github.com/matthewong1210/jarvis-ai-agent/pull/5) | 2026-08-03 | Virtual Handoff Lab and backend-independent robot task seam |
| [#6](https://github.com/matthewong1210/jarvis-ai-agent/pull/6) | 2026-08-07 | Whole-range `RangeSimulation`, directive path, and `SafetyShield` |
| [#7](https://github.com/matthewong1210/jarvis-ai-agent/pull/7) | 2026-08-07 | Reproducible range-agent benchmark layer |
| [#8](https://github.com/matthewong1210/jarvis-ai-agent/pull/8) | 2026-08-07 | Deterministic replay exporter and presentation layer |
| [#9](https://github.com/matthewong1210/jarvis-ai-agent/pull/9) | 2026-08-07 | Frozen `FacilityState` downstream contract |
| [#10](https://github.com/matthewong1210/jarvis-ai-agent/pull/10) | 2026-08-07 | Advisory facility decisions and manager briefing |
| [#11](https://github.com/matthewong1210/jarvis-ai-agent/pull/11) | 2026-08-07 | Append-only, no-feedback operational memory |
| [#12](https://github.com/matthewong1210/jarvis-ai-agent/pull/12) | 2026-08-07 | Observation input contract and FacilityState assembly parity |
| [#14](https://github.com/matthewong1210/jarvis-ai-agent/pull/14) | 2026-08-08 | File-coupled FacilityState-to-USD projection layer |
| [#19](https://github.com/matthewong1210/jarvis-ai-agent/pull/19) | 2026-08-09 | Shadow Ops adapter, policy evaluation/trace, human workflow, and ledger (`e84c5016a19d1d4aec0b4b183164c08bba5b164e`) |
| [#20](https://github.com/matthewong1210/jarvis-ai-agent/pull/20) | 2026-08-09 | Static physical-facility manifest, validation/storage, one-way projections, and setup contract (`89e93f6a8ea0cd469d6da907321eafe30318fa49`) |
| [#22](https://github.com/matthewong1210/jarvis-ai-agent/pull/22) | 2026-08-09 | Orchestration-only Site Runtime, deterministic FacilityState envelope, quality gate, checkpoints/recovery, and state-publication ports (`b055c9472737feb923c6ac48fad44a5b7e43333c`) |
| [#23](https://github.com/matthewong1210/jarvis-ai-agent/pull/23) | 2026-08-09 | AI engineering operating system: repository truth, package, safety, testing, review, and agent/human workflow governance (`192292735221e503915f286627dc64f001942881`) |

ROI precedent:

- [#1](https://github.com/matthewong1210/jarvis-ai-agent/pull/1) introduced
  the formula-lock ROI engine.
- [#2](https://github.com/matthewong1210/jarvis-ai-agent/pull/2) landed review
  fixes that missed the first merge. Do not merge before verified fixes are in
  the actual branch tip.

## Repeated engineering patterns

- Recon and approved design precede implementation for new layers.
- New capability usually lands as a downstream sibling with minimal or zero
  upstream change.
- Boundary tests are first-class: AST import scans, blocked-import subprocesses,
  no-upstream-mention checks, protected-tree hashes, and negative controls.
- Read-only layers prove RNG and trajectory neutrality against an uninstrumented
  run.
- Determinism and byte reproducibility are treated as contract properties.
- Alternate paths and artifacts receive parity/drift tests instead of being
  trusted by convention.
- Missing or malformed facts fail loudly; placeholders and estimates retain
  explicit provenance/disclaimers.
- Changes commonly use small `docs`, `feat`, `test`, and final `fix` commits.
- Stacked feature branches have been used for phase ladders; parent merge state
  and retargeting must be verified before integration.
- Portable repo-relative documentation is preferred; PR #14 removed a
  machine-specific absolute path from a plan.

## Evidence caveat

Merged PR bodies reported growing local test totals and adversarial-review
passes, but GitHub showed no formal human review records and no test/build CI
checks for the audited merges. The only recorded check was CodeRabbit, often
skipped or disabled. Treat PR-body counts as historical claims, not CI
attestations. Future agents must report exact commands and observed results.

## Documentation status caveats

- Before the investor-facing documentation refresh, the source repository's
  root `README.md` described the historical Jarvis prototype rather than the
  full repository. That prototype is not part of this standalone extraction;
  the current root README describes the retained NXTektal surfaces.
- `simulation/docs/architecture.md` is primarily the micro handoff view and is
  not a complete Site OS package map.
- Several `*_recon.md`, `*_design.md`, and `*_plan.md` files retain historical
  "proposed", "no code", or unchecked-plan language after implementation.
- An earlier `simulation/README.md` said "ball flow is not simulated" while
  `nxt_range_ops` already simulated conserved integer inventory. The current
  wording distinguishes that inventory model from unimplemented granular
  physical flow, friction, bridging, and jamming.
- `FacilityState` is downstream truth, not mutable runtime truth.
- Shadow Ops, Commissioning, and Site Runtime are merged-main truth at the
  finalization baseline. Their earlier draft/sibling status is historical only.
- Commissioning's `project_site_config()` is static-only and not the current
  `SiteConfig` constructor shape; `project_legacy_site_config()` supplies the
  compatibility shape with explicit non-commissioned context, and
  `bind_commissioned_site()` uses it at Site Runtime setup.
- `nxt_site_runtime`, `FacilitySnapshotEnvelope`, the
  `nxt-site-runtime/facility-snapshot/v1` schema, ports, checkpoints, tests, and
  package registration are implemented. Concrete physical observation adapters,
  production state publishers/sinks, live hardware/vendor delivery, a
  long-running real-site service, physical command admission, and autonomous
  actuator execution remain unimplemented.
