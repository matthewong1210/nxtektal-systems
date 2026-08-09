# Audit baseline and recent repository history

This file records the evidence used to create the operating layer. It is a
dated orientation aid, not a substitute for checking current Git/GitHub state.

## Audit baseline: 2026-08-09

- Checkout audited: `feature/shadow-ops-v0` at `a24f1a7`.
- `main` and `origin/main`: `eb51c8a`, merged PR #14.
- Shadow Ops was committed on the audited feature branch but not merged to
  `main` at audit time.
- A separate sibling commissioning branch, `feature/commissioning-v0` at
  `260df33` (draft PR #20), implemented `nxt_commissioning`; it was not present
  in the current checkout or merged architecture. The second-pass operating
  layer records its inspected boundary and exact unmerged status rather than
  pretending the two feature branches are integrated.
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

## Open feature status at second pass

| PR | Base/head | Status | Architectural evidence, not merged truth |
|---|---|---|---|
| [#19](https://github.com/matthewong1210/jarvis-ai-agent/pull/19) | `main` <- `feature/shadow-ops-v0` | Open draft at `a24f1a7` | Shadow Ops adapter, policy evaluation/trace, workflow, and ledger |
| [#20](https://github.com/matthewong1210/jarvis-ai-agent/pull/20) | `main` <- `feature/commissioning-v0` | Open draft at `260df33` | Static physical-facility manifest, validation/storage, and one-way projections |

Both branches start from `eb51c8a`; neither contains the other's package. Any
integration must reconcile `simulation/pyproject.toml` package registration and
verify both package/boundary suites.

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

- Root `README.md` describes Jarvis, not the full repository.
- `simulation/docs/architecture.md` is primarily the micro handoff view and is
  not a complete Site OS package map.
- Several `*_recon.md`, `*_design.md`, and `*_plan.md` files retain historical
  "proposed", "no code", or unchecked-plan language after implementation.
- `simulation/README.md` saying "ball flow is not simulated" refers to granular
  physical flow/jamming; `nxt_range_ops` does simulate conserved integer ball
  inventory.
- `FacilityState` is downstream truth, not mutable runtime truth.
- Shadow Ops was current-branch truth but not merged-main truth at the audit
  date. Recheck before describing release status.
- Commissioning was sibling-branch truth but neither current-checkout nor
  merged-main truth. Its `project_site_config()` is static-only and not the
  current `SiteConfig` constructor shape; only `project_legacy_site_config()`
  supplies that compatibility shape with explicit non-commissioned context.
- `nxt_site_runtime`, `FacilitySnapshotEnvelope`, physical observation adapters,
  and live delivery/sinks existed only as proposals in the untracked audit. No
  corresponding tracked package, schema, registration, or tests existed in any
  audited branch.
