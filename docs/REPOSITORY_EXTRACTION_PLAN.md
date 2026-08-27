# NXTektal Systems repository extraction plan

## Status and decision record

This is an architecture and migration plan only. It does not perform an
extraction, create a source tag, initialize a destination branch, or change
either repository's visibility.

| Item | Recorded state |
|---|---|
| Audit date | 2026-08-10 |
| Source repository | `matthewong1210/jarvis-ai-agent` |
| Audited source `main` | `3e2925ea4918615f09aba5e5fc717173dde80b9a` |
| Target repository | `matthewong1210/nxtektal-systems` |
| Target state | Private and empty; no refs may be created during planning |
| Extraction method | `git filter-repo` in a disposable mirror clone |
| Extraction source tag | Not created; name and commit require final approval |
| Path policy | Preserve existing paths in the first extraction |

The source repository must never be rewritten or force-pushed. Every history
rewrite described below occurs only in a disposable mirror. The target stays
empty until the plan is approved, the source baseline is frozen, and all
pre-push gates pass.

## Executive recommendation

Create the first NXTektal baseline from a reviewed merge commit on source
`main`, using an allowlisted path filter. Include the complete `simulation/`
and `nxtektal-roi-engine/` trees, the NXTektal product documentation, and the
AI engineering governance layer. Exclude the independent Jarvis prototype.

Do not rename packages, flatten directories, split Python distributions, or
move documentation during the history extraction. Those changes would combine
a history rewrite with a structural migration and make provenance, package,
link, and rollback failures much harder to diagnose. Normalize names and
structure only in later, ordinary commits in the target repository.

## Merged source baseline

The audited source `main` contains the completed NXTektal product and governance
merge train through investor-facing documentation and the TimeWindow semantics
pin:

| Pull request | Milestone | Merge commit |
|---|---|---|
| #14 | Digital Twin Phase 0 | `eb51c8a678b13a0ceaa89477be94e20d737f27bf` |
| #19 | Shadow Ops | `e84c5016a19d1d4aec0b4b183164c08bba5b164e` |
| #20 | Commissioning | `89e93f6a8ea0cd469d6da907321eafe30318fa49` |
| #22 | Site Runtime | `b055c9472737feb923c6ac48fad44a5b7e43333c` |
| #23 | AI Engineering Operating System | `192292735221e503915f286627dc64f001942881` |
| #21 | Investor-facing repository documentation | `c7abc1330033764d17964be2011887b79c1966ea` |
| #15 | TimeWindow minute-of-day semantics pin | `3e2925ea4918615f09aba5e5fc717173dde80b9a` |

These commits establish merged-main truth. Earlier plans and PR descriptions
that call Shadow Ops, Commissioning, Site Runtime, governance, or investor
documentation unmerged, or that omit the merged TimeWindow semantics pin, are
historical evidence only.

The eventual source tag should point to the merge commit that includes this
reviewed extraction plan. That commit does not exist yet, so
`3e2925ea4918615f09aba5e5fc717173dde80b9a` is the audit baseline, not an
authorization to tag or extract.

## PR #24 decision

**Decision C: close or supersede PR #24 because its revision is obsolete.**

PR #24, `docs: add repository extraction plan`, is an open draft from
`codex/repository-extraction-plan` at
`f06085886f5bedcc837e0ed4a6f368fddc0f66b8`. It adds only
`docs/REPOSITORY_EXTRACTION_PLAN.md`, but it is based directly on the old
Digital Twin Phase 0 baseline and is 19 source-main commits behind this audit.
It describes Shadow Ops, Commissioning, Site Runtime, governance, and investor
documentation as draft, uncommitted, or untracked. Those status claims are no
longer safe extraction authority.

PR #24 does not add production code or demo code. It does not duplicate PR #21
at the file level and does not duplicate viewer or demo functionality. Its
useful ownership and filtering analysis is incorporated into this replacement.
The stale PR commit should not be included in extraction source v1; the reviewed
replacement document should be included after it merges. Closing PR #24 is a
separate later action and is not performed by this plan.

## Product and architecture boundary

The extraction must keep the product layers distinct:

- **AI operations** owns trusted downstream state, observation quality,
  deterministic advice, decision trace, human workflow evidence, operational
  memory, commissioning facts, and orchestration.
- **Digital twin and viewer output** are downstream spatial or presentation
  projections. USD, viewer frames, and reports never become operational truth.
- **Robot execution** stays behind the simulator safety path and the
  backend-independent handoff interface. Physical robot execution and
  site-level physical command admission are not implemented.

The extracted history must not imply physical telemetry adapters, live vendor
integrations, automatic robot execution from Site Runtime, live
Omniverse/Nucleus delivery, production real-site deployment, or LLM
participation in execution or safety loops.

## Authoritative path ownership map for baseline v1

This map is authoritative for the first extraction. The ambiguous table records
the current include/exclude disposition and the approval still required; no row
may change silently before the source tag.

### Include: NXTektal Systems

| Path | Ownership and reason |
|---|---|
| `simulation/` | Complete NXTektal Python project: packages, tests, configs, scripts, docs, demo/viewer, placeholder assets, lockfile, and build metadata |
| `nxtektal-roi-engine/` | Independent NXTektal TypeScript ROI package, tests, examples, docs, and lockfile |
| `README.md` | Current NXTektal product and investor overview |
| `docs/ARCHITECTURE.md` | Current product architecture and truth boundaries |
| `docs/DEMO.md` | Verified simulation-first investor demo path |
| `docs/MILESTONES.md` | Merged, open, and future product status |
| `docs/AGENT_OPERATING_MANUAL.md` | NXTektal engineering governance manual |
| `docs/REPOSITORY_EXTRACTION_PLAN.md` | Reviewed extraction decision and audit record |
| `docs/superpowers/specs/2026-07-16-nxtektal-roi-engine-design.md` | ROI engine design history |
| `AGENTS.md` | Repository-wide NXTektal engineering contract |
| `CLAUDE.md` | Claude Code entry point into the NXTektal governance contract |
| `.agent/` | NXTektal context, workflows, review rules, and skills |
| `.gitignore` | Shared repository hygiene rules; any Jarvis-only entries are harmless and may be cleaned later |

Keeping `simulation/` whole is deliberate. It preserves every current Python
package and its boundary tests, including `nxt_sim`, `nxt_range_ops`,
`nxt_range_agent`, `nxt_facility`, `nxt_memory`, `nxt_telemetry`,
`nxt_range_viewer`, `nxt_range_demo`, `nxt_range_twin`, `nxt_pilot_ops`,
`nxt_commissioning`, and `nxt_site_runtime`, plus future site-runtime work that
lands under the same reviewed tree before the source tag.

### Exclude: Jarvis AI Agent

| Path | Ownership and reason |
|---|---|
| `dashboard.html` | Jarvis dashboard |
| `index.html` | Jarvis landing-page experiment |
| `css/`, `js/` | Jarvis browser presentation |
| `assets/` | Root Jarvis 3D and image assets; distinct from `simulation/assets/` |
| `jarvis_data.js` | Jarvis dashboard data |
| `jarvis_ack.mp3`, `jarvis_brief.mp3`, `jarvis_brief.txt` | Jarvis voice artifacts |
| `scripts/` | Root Jarvis voice/server scripts; distinct from `simulation/scripts/` |
| `Start Jarvis.command` | Jarvis macOS launcher |
| `generate_assets.py` | Root Jarvis asset-generation helper |
| `docs/JARVIS_PROTOTYPE.md` | Legacy Jarvis documentation |
| `.claude/launch.json` | Jarvis launch entries and a machine-specific external path |

No NXTektal Python or ROI build imports these paths. They remain in the source
repository with their full history.

### Shared, excluded, or deferred

| Path or ref | Current decision for baseline v1 | Required follow-up |
|---|---|---|
| `.claude/skills/3d-asset-generator/` | Exclude | It is generic tooling currently colocated with Jarvis assets and has no current NXTektal contract; migrate later only after an ownership and portability review |
| `.mcp.json` | Exclude | It configures a generic UI MCP server and is not required by either NXTektal package build |
| Historical `README.md` blobs | Include the current path, keep target private | The current blob is NXTektal, but earlier blobs describe Jarvis; approve that retained history before tagging and review it again before any public release |
| Open PR #3 | Defer; exclude from extraction source v1 | Revisit the ROI Quick Estimate UI after its customer-facing economic semantics, portable tooling scope, and current UI/build verification are resolved |
| Open PR #4 | Defer; exclude from extraction source v1 | Revisit the ROI specification council review after its unresolved version contract, formula ambiguities, and review findings are resolved |
| Open PR #13 | Defer; exclude from extraction source v1 | Revisit the Site OS Demo after deciding how its distinct panels fit the merged viewer/demo, package map, Site Runtime, Commissioning, and Shadow Ops boundaries |
| Open draft PR #24 | Supersede; exclude stale commit | Use the refreshed plan instead; close only as a separate authorized action |
| Untracked files in any checkout | Exclude | Only committed objects reachable from the approved source tag are eligible |

PR #15 is no longer an open-branch decision: its TimeWindow semantics pin is
included through merge commit
`3e2925ea4918615f09aba5e5fc717173dde80b9a`. PRs #3, #4, and #13 are
intentionally deferred from extraction source v1, and PR #24 remains
superseded.

The baseline rule is simple: an open branch is not silently unioned into the
extraction. If an open NXTektal PR is required for v1, it must first be reviewed
and merged into source `main`, after which the source commit and path audit must
be refreshed before tagging.

## Dependency and breakage audit

### Code and package dependencies

- `simulation/pyproject.toml` and `simulation/uv.lock` are self-contained under
  the included Python tree. All first-party imports stay inside `simulation/`.
- `nxtektal-roi-engine/package.json`, its lockfile, TypeScript configs, source,
  and tests are self-contained under the included ROI tree.
- The Python and ROI surfaces are independent; no root workspace manifest or
  build step couples them.
- The root Jarvis prototype does not provide a runtime or build dependency to
  either included package surface.
- The source repository currently has no repository-local GitHub Actions
  workflow, `CODEOWNERS`, or root monorepo build manifest to migrate.

Because the entire two package trees are retained, internal imports such as
viewer-to-range-ops, telemetry-to-facility, the designated Site Runtime seams,
and composition-root script imports remain resolvable. Their architectural
direction must still be revalidated after filtering.

### Documentation links and context that will need a later commit

The first extraction intentionally preserves files byte-for-byte except for
history filtering. Exactly four relative links in the raw filtered tree will
initially point at the excluded legacy Jarvis document, and additional
governance files retain mixed-repository context:

| Included file | Post-extraction issue |
|---|---|
| `README.md` | Links to `docs/JARVIS_PROTOTYPE.md` |
| `docs/ARCHITECTURE.md` | Links to `docs/JARVIS_PROTOTYPE.md` |
| `AGENTS.md` | Describes and links the root Jarvis surface |
| `docs/AGENT_OPERATING_MANUAL.md` | Describes and links the root Jarvis surface |
| `.agent/context/product.md` | No broken link; lists the root Jarvis prototype as a repository surface |
| `.agent/context/package-map.md` | No broken link; lists root Jarvis responsibility and verification |
| `.agent/context/repository-history.md` | No broken link; retains mixed-repository historical context |
| `.agent/skills/nxtektal-change/SKILL.md` | No broken link; its scope and routing still include root Jarvis |
| `.agent/workflows/testing.md` | No broken link; retains a root Jarvis test section |

After the raw filtered baseline is verified, make one ordinary,
documentation-only target commit that removes the four broken Jarvis links,
labels source-repository PR links as historical provenance, and rewrites
repository-scope guidance for the standalone target. The normalization
allowlist is `README.md`, `AGENTS.md`, `docs/ARCHITECTURE.md`,
`docs/AGENT_OPERATING_MANUAL.md`, `docs/MILESTONES.md`,
`.agent/context/product.md`, `.agent/context/package-map.md`,
`.agent/context/repository-history.md`,
`.agent/skills/nxtektal-change/SKILL.md`, and
`.agent/workflows/testing.md`. Preserve source PR URLs as provenance; do not
retarget them to nonexistent destination PR numbers.

Keep the rewritten source tag on the raw filtered commit and make target
`main` a descendant containing the cleanup. Do not perform that cleanup inside
the path filter. Links within `simulation/`, the ROI engine, and the retained
root NXTektal docs otherwise remain within the allowlist.

The included ROI design history currently contains one user-home shorthand
path. A tip-only documentation edit would leave that value reachable in older
blobs. The disposable rewrite must therefore apply the single reviewed
content-replacement rule described below across filtered history, while
preserving the document path. Record every changed blob. Do not carry a
machine-specific path into an accepted target.

## Why `git filter-repo`

Use `git filter-repo`, not `git subtree split` or `git filter-branch`.

- The desired history is the union of two directories, selected root files,
  selected documentation, and a dot-directory. `git subtree split` is designed
  around one prefix and would require synthetic recombination for this map.
- `git filter-repo` provides explicit path allowlisting, commit mapping, empty
  commit pruning, and repeatable operation in a disposable clone.
- `git filter-branch` is slower, easier to misuse, and not recommended for a
  new migration.

Original commit IDs necessarily change when excluded paths are removed. History
preservation means retaining the relevant commit graph, authorship, dates,
messages, merges where reachable, and an auditable old-to-new commit map—not
pretending rewritten commits keep their source SHA.

The topology, mapping, replacement, and signature behavior used here is
documented in the
[official `git-filter-repo` manual](https://github.com/newren/git-filter-repo/blob/master/Documentation/git-filter-repo.txt).

## Exact extraction procedure

The following is the approved strategy, but it must not be executed until the
remaining decisions are resolved and the source tag is authorized.

### 1. Freeze and record the source

1. Merge the reviewed replacement plan into source `main` using the normal
   merge-commit convention.
2. Resolve or explicitly defer every open NXTektal PR listed above.
3. Re-run the ownership, secret, link, package, and test gates on exact source
   `main`.
4. Record the approved source commit in the migration record.
5. Create an annotated tag such as `nxtektal-extraction-source-v1` only after
   explicit authorization. Record the tag object ID and peeled commit ID.

The tag is a source-side audit marker, not an instruction to rewrite the source.

### 2. Create a preservation mirror and offline rollback bundle

Run from a neutral directory, not from an existing checkout:

```bash
set -euo pipefail

source_url="git@github.com:matthewong1210/jarvis-ai-agent.git"
target_url="git@github.com:matthewong1210/nxtektal-systems.git"
source_tag="nxtektal-extraction-source-v1"
approved_source_commit="<approved-source-main-commit>"
migration_root="$(mktemp -d)"
preservation_mirror="$migration_root/jarvis-ai-agent-source.git"
filtered_repo="$migration_root/nxtektal-systems-filtered.git"
filter_repo_version="2.47.0"
filter_repo_wheel_sha256="2cd04929b9024e83e65db571cbe36aec65ead0cb5f9ec5abe42158654af5ad83"
filter_repo_requirements="$migration_root/filter-repo-requirements.txt"
filter_repo_venv="$migration_root/filter-repo-venv"

printf 'git-filter-repo==%s --hash=sha256:%s\n' \
  "$filter_repo_version" "$filter_repo_wheel_sha256" \
  > "$filter_repo_requirements"
python3 -m venv "$filter_repo_venv"
"$filter_repo_venv/bin/python" -m pip install \
  --require-hashes --only-binary=:all: --no-deps \
  -r "$filter_repo_requirements"
filter_repo="$filter_repo_venv/bin/git-filter-repo"
test "$("$filter_repo_venv/bin/python" -c \
  'import importlib.metadata; print(importlib.metadata.version("git-filter-repo"))')" = \
  "$filter_repo_version"
"$filter_repo" --version > "$migration_root/filter-repo-version.txt"
shasum -a 256 "$filter_repo" > "$migration_root/filter-repo-executable.sha256"

git clone --mirror "$source_url" "$preservation_mirror"
cd "$preservation_mirror"

test "$(git rev-parse "$source_tag^{commit}")" = "$approved_source_commit"

allowlisted_paths=(
  .agent .gitignore AGENTS.md CLAUDE.md README.md
  docs/AGENT_OPERATING_MANUAL.md docs/ARCHITECTURE.md docs/DEMO.md
  docs/MILESTONES.md docs/REPOSITORY_EXTRACTION_PLAN.md
  docs/superpowers/specs/2026-07-16-nxtektal-roi-engine-design.md
  nxtektal-roi-engine simulation
)
source_tree_manifest="$migration_root/source-allowlisted-tree.tsv"
git ls-tree -r "$source_tag^{tree}" -- "${allowlisted_paths[@]}" \
  > "$source_tree_manifest"
test -s "$source_tree_manifest"

roi_design="docs/superpowers/specs/2026-07-16-nxtektal-roi-engine-design.md"
source_spec_reference="$(git show "$source_tag:$roi_design" | \
  sed -n 's/^Source spec: `\([^`]*\)`.*/\1/p')"
test "$(printf '%s\n' "$source_spec_reference" | wc -l | tr -d ' ')" = "1"
test "$(basename "$source_spec_reference")" = \
  "NXTektal_ROI_Calculation_Engine_Spec_v1.0_CN.docx"
replacement_rules="$migration_root/history-replacements.txt"
printf 'literal:%s==>%s\n' "$source_spec_reference" \
  "NXTektal_ROI_Calculation_Engine_Spec_v1.0_CN.docx (external source document; location not versioned)" \
  > "$replacement_rules"
unset source_spec_reference

git fsck --full
git bundle create "$migration_root/jarvis-ai-agent-before-filter.bundle" --all
git bundle verify "$migration_root/jarvis-ai-agent-before-filter.bundle"

# Disable the source push URL before any local rewrite work.
git remote set-url --push origin "disabled://source-push-prohibited"
test "$(git remote get-url --push origin)" = \
  "disabled://source-push-prohibited"

# Derive a second bare repository containing only approved main and tag refs.
git init --bare "$filtered_repo"
git -C "$filtered_repo" fetch --no-tags "$preservation_mirror" \
  refs/heads/main:refs/heads/main \
  "refs/tags/$source_tag:refs/tags/$source_tag"
git -C "$filtered_repo" symbolic-ref HEAD refs/heads/main

test "$(git -C "$filtered_repo" rev-parse refs/heads/main)" = \
  "$approved_source_commit"
test "$(git -C "$filtered_repo" rev-parse "$source_tag^{commit}")" = \
  "$approved_source_commit"
test -z "$(git -C "$filtered_repo" remote)"
```

Version 2.47.0 is pinned to the published wheel hash recorded by the
[PyPI release](https://pypi.org/project/git-filter-repo/2.47.0/). Stop if the
package hash/version check, tag check, path-reference derivation, `git fsck`, or
bundle verification fails. Store the requirements, executable checksum,
replacement rule, bundle, and bundle checksum in an approved private migration
archive; do not commit them to either repository.

Keep the preservation mirror's object and ref database unchanged. All filtering
occurs in `nxtektal-systems-filtered.git`, which has no source remote and
contains no open feature-branch ref.

### 3. Filter only the approved paths

From the second disposable bare repository:

```bash
cd "$filtered_repo"

"$filter_repo" --force \
  --no-ff \
  --prune-empty auto \
  --prune-degenerate auto \
  --replace-text "$replacement_rules" \
  --path .agent/ \
  --path .gitignore \
  --path AGENTS.md \
  --path CLAUDE.md \
  --path README.md \
  --path docs/AGENT_OPERATING_MANUAL.md \
  --path docs/ARCHITECTURE.md \
  --path docs/DEMO.md \
  --path docs/MILESTONES.md \
  --path docs/REPOSITORY_EXTRACTION_PLAN.md \
  --path docs/superpowers/specs/2026-07-16-nxtektal-roi-engine-design.md \
  --path nxtektal-roi-engine/ \
  --path simulation/
```

Do not add `--path-rename`. Do not move `simulation/` contents to the root. Do
not rename `nxt_*` packages or the ROI package during this operation. Do not
run the filter in the source checkout. Do not use `git push --mirror`,
`git push --all`, or any force push.

`git filter-repo` writes an old-to-new commit map under its metadata directory.
The explicit `--no-ff` policy preserves first-parent topology when it would
otherwise become an ancestor of another merge parent. Copy the `commit-map`,
`ref-map`, and any `suboptimal-issues` report with the exact command, tool
version and executable checksum, source tag, source commit, tree IDs, operator,
timestamp, signature inventory, and validation results into the private
migration archive. Rewriting changes commit and tag objects, so source commit
and tag signatures do not survive as valid signatures; preserve their source
verification separately and create any target attestation only after review.

All seven milestone merge commits are mandatory preservation gates:

```bash
commit_map="filter-repo/commit-map"
ref_map="filter-repo/ref-map"
zero_sha="0000000000000000000000000000000000000000"
test -f "$commit_map"
test -f "$ref_map"

for source_sha in \
  eb51c8a678b13a0ceaa89477be94e20d737f27bf \
  e84c5016a19d1d4aec0b4b183164c08bba5b164e \
  89e93f6a8ea0cd469d6da907321eafe30318fa49 \
  b055c9472737feb923c6ac48fad44a5b7e43333c \
  192292735221e503915f286627dc64f001942881 \
  c7abc1330033764d17964be2011887b79c1966ea \
  3e2925ea4918615f09aba5e5fc717173dde80b9a; do
  filtered_sha="$(awk -v old="$source_sha" '$1 == old {print $2}' "$commit_map")"
  test -n "$filtered_sha"
  test "$filtered_sha" != "$zero_sha"
  git cat-file -e "$filtered_sha^{commit}"
done

if test -s filter-repo/suboptimal-issues; then
  printf 'STOP: review filter-repo/suboptimal-issues before proceeding\n' >&2
  exit 1
fi
```

A missing, zero, or invalid mapping is a stop condition, not an explainable
exception.

### 4. Verify and normalize locally before adding the destination remote

At minimum:

```bash
git fsck --full
test "$(git rev-parse --is-bare-repository)" = "true"
git show-ref
git ls-tree -r --name-only refs/heads/main
git log --graph --decorate --oneline refs/heads/main
```

Confirm the tree contains only the include map, the source tag maps to raw
filtered `main`, and no excluded Jarvis path is reachable from the refs intended
for publication. Prove selected current-tree contents were not lost or altered
except for the one approved history replacement:

```bash
raw_tree_manifest="$migration_root/raw-filtered-tree.tsv"
git ls-tree -r "$source_tag^{tree}" > "$raw_tree_manifest"

awk -F '\t' -v path="$roi_design" '$2 != path' \
  "$source_tree_manifest" > "$migration_root/source-stable-tree.tsv"
awk -F '\t' -v path="$roi_design" '$2 != path' \
  "$raw_tree_manifest" > "$migration_root/filtered-stable-tree.tsv"
cmp "$migration_root/source-stable-tree.tsv" \
  "$migration_root/filtered-stable-tree.tsv"

test -n "$(awk -F '\t' -v path="$roi_design" '$2 == path' \
  "$raw_tree_manifest")"
if git show "$source_tag:$roi_design" | grep -q 'Downloads/'; then
  printf 'STOP: machine-specific source-spec location remains\n' >&2
  exit 1
fi
```

Record the source and filtered blob IDs for the intentionally transformed ROI
design file and verify its path and mode are unchanged. Scan all reachable
history to prove the replaced machine path is gone.

Record the raw filtered tag commit and tree. Then attach a non-bare worktree to
filtered `main` and make the separate documentation-only normalization commit
described in the link audit. It may remove stale Jarvis context, but it must
stay within the ten-file normalization allowlist and must not rename, flatten,
or change any production/package file. The history replacement already handles
the ROI design path. Record the normalized target-main commit and tree
separately. The rewritten extraction tag must remain on the raw filtered
commit; normalized target `main` must be its descendant, and `git diff
--name-only "$source_tag"..main` must be a subset of that allowlist.

Run the complete tree, history, link, fence, secret, private-data,
machine-path, package, build, test, demo, and hygiene gates against normalized
`main`. The pre-normalization link scan should find only the four recorded
Jarvis-document links; the final scan must find none.

### 5. Push only after a final empty-target check and explicit approval

Before adding a destination remote:

```bash
target_repo="matthewong1210/nxtektal-systems"
test "$(gh repo view "$target_repo" --json nameWithOwner \
  --jq '.nameWithOwner')" = "$target_repo"
test "$(gh repo view "$target_repo" --json isPrivate \
  --jq '.isPrivate')" = "true"
test "$(gh repo view "$target_repo" --json isEmpty \
  --jq '.isEmpty')" = "true"
target_refs="$(git ls-remote "$target_url")"
test -z "$target_refs"
git remote add destination "$target_url"
```

After explicit approval, atomically push only normalized target `main` and the
one rewritten extraction tag:

```bash
git push --atomic destination \
  refs/heads/main:refs/heads/main \
  "refs/tags/$source_tag:refs/tags/$source_tag"
```

Never push any other filtered source branch or tag by default. Re-run the exact
identity, privacy, emptiness, and all-ref checks immediately before the atomic
push. If any check changes, stop and investigate; do not overwrite or
force-update it.

## Post-extraction validation gates

Run all gates in a fresh clone of the private target, not only in the filtered
mirror. Preserve exact command output in the migration record.

### Tree, history, and hygiene

- Verify the extraction tag resolves to the recorded raw filtered commit/tree,
  `origin/main` resolves to the separately recorded normalized commit/tree, the
  tag is an ancestor of `origin/main`, and their diff stays inside the
  normalization allowlist.
- Run `git fsck --full` and inspect the complete first-parent and graph history.
- Require every milestone source SHA to map to the recorded nonzero filtered
  commit in the archived `filter-repo` commit map.
- Run `git log --follow` on representative files from `simulation/`, the ROI
  engine, root docs, and `.agent/`.
- Enforce the include map against `git ls-tree -r --name-only`; reject every
  excluded Jarvis path, unexpected symlink, generated report, cache, build
  product, and editor artifact.
- Record the four expected raw-tree link failures, then require `git diff
  --check`, Markdown fence validation, and zero relative Markdown link failures
  on normalized target `main`.
- Confirm a clean worktree after every validation phase.

### Python package, architecture, and demo

From `simulation/`, require the manifest and lock to agree and install every
declared extra:

```bash
uv lock --check
uv sync --locked --all-extras
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider
uv run --no-sync python -B scripts/validate_configs.py

python_build_dir="$(mktemp -d)"
uv build --out-dir "$python_build_dir"
```

Inspect the wheel contents and prove all declared packages are present. Keep
the checked lock unchanged throughout extraction verification.

Then run the verified simulation-first replay path in `docs/DEMO.md`: generate
the benchmark, capture the deterministic facility-state/briefing artifacts,
export the viewer bundle for `demand_spike`, policy
`demand_forecast_dispatch`, seed `101`, and smoke the Streamlit app. Compare
replay artifacts and provenance to the pre-extraction baseline. Optional USD
output must be validated only as a downstream projection.

### ROI package

From `nxtektal-roi-engine/`:

```bash
npm ci
npm run typecheck
npm test
npm run build
```

Inspect the package output and confirm formula IDs, examples, and lockfile are
unchanged by extraction.

### Secrets, private data, and history

- Scan the filtered working tree and all reachable history with two independent
  secret scanners when available, with redacted output.
- Search for credentials, tokens, private keys, connection strings, customer
  names, customer site identifiers, personal contact data, raw telemetry,
  commissioning manifests, and absolute machine paths.
- Review binary inventory and large objects separately; text-only scanners do
  not establish that binaries are safe.
- Confirm no GitHub Actions secrets, deployment keys, environment values, or
  local configuration were copied manually.
- Treat any finding as a stop condition. Remediate in the disposable filtered
  history, regenerate the commit map and checksums, and repeat every gate. Never
  rewrite the source repository to fix the target history.
- If a credential or active secret is found, history rewriting is not
  remediation by itself. Revoke or rotate it, follow the incident process, and
  verify downstream exposure while leaving source history untouched.

## GitHub collaboration metadata migration record

Normal Git history extraction does **not** preserve GitHub pull request and
issue numbers, PR state, reviews, review threads, issue or review comments,
check runs, status contexts, Actions logs, labels, milestones, projects,
branch-protection settings, repository settings, releases, release assets,
security alerts, environments, secrets, or deployment history.

Before any target push, create a separate, private migration record containing:

1. Source repository, source tag, source commit, filter command, filter tool
   version, and old-to-new commit map.
2. A manifest of every NXTektal-relevant PR and issue with source URL, number,
   title, state, author, base/head refs, merge commit, labels, milestone, and
   destination disposition.
3. Separate exports or counts for PR reviews, review comments, issue comments,
   commit comments, check suites/runs, and status contexts.
4. A record of releases, branch protections, repository rules, Actions
   workflows, environments, and required checks that must be recreated.
5. A mapping from source PRs and issues to any manually recreated target issue,
   discussion, release note, or provenance document.

Keep raw GitHub exports in an access-controlled archive because comments and
review payloads can contain personal or private data. Commit only a reviewed,
redacted migration manifest to the target. Do not pretend recreated issues or
PRs retain original authorship, timestamps, approvals, or check attestations;
link back to the preserved private source when access policy permits.

## Proposed target structure

The first filtered commit should retain this layout:

```text
nxtektal-systems/
├── .agent/
├── .gitignore
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── docs/
│   ├── AGENT_OPERATING_MANUAL.md
│   ├── ARCHITECTURE.md
│   ├── DEMO.md
│   ├── MILESTONES.md
│   ├── REPOSITORY_EXTRACTION_PLAN.md
│   └── superpowers/specs/
├── nxtektal-roi-engine/
└── simulation/
    ├── configs/
    ├── docs/
    ├── nxt_*/
    ├── scripts/
    ├── tests/
    ├── pyproject.toml
    └── uv.lock
```

After extraction verification, ordinary follow-up commits may add:

- `.github/workflows/docs.yml` for Markdown links, fences, secret/path scans,
  and hygiene;
- `.github/workflows/python.yml` for supported Python versions, architecture
  guards, full tests, config validation, and wheel inspection;
- `.github/workflows/roi.yml` for Node install, typecheck, tests, and build;
- `CODEOWNERS`, repository rules, dependency updates, and a security policy;
- a redacted GitHub metadata migration manifest; and
- documentation cleanup for the standalone repository context.

Do not flatten `simulation/` or rename packages in those first verification
commits. Any later package-layout redesign needs its own architecture proposal,
import migration, compatibility plan, release/version policy, and test evidence.

## CI and branch-protection requirements

Before treating the target as authoritative:

- require pull requests and a merge-commit policy consistent with the source;
- protect `main` from force pushes and deletion;
- require docs/link/fence/hygiene checks;
- require the Python full suite, architecture guards, config validation, and
  package build with the USD dependency gap handled explicitly;
- require ROI typecheck, tests, and build;
- enable secret scanning, dependency review, and automated dependency updates
  where the private plan permits;
- define release/tag permissions and require approval for production-facing
  adapters or execution boundaries; and
- record which checks are new target checks rather than inherited source CI.

The source currently has no repository-local CI workflow, so historical local
test claims must not be relabeled as CI attestations.

## Public/private boundary recommendation

Keep `matthewong1210/nxtektal-systems` private through extraction and validation.
Do not change visibility as part of history migration.

A later public-release review may consider publishing sanitized architecture,
schemas, deterministic examples, selected simulation tooling, or the ROI
package. Keep private by default:

- customer and facility identities, commissioned manifests, surveyed geometry,
  sensor bindings, raw or derived telemetry, and deployment configuration;
- vendor contracts, credentials, network details, device identifiers, private
  pricing, and unreleased hardware integration;
- incident, safety, human-workflow, and operational ledger records; and
- private GitHub review/comment/check exports and migration archives.

Resolve licensing and provenance before any public release, including the lack
of a repository license and the rights for binary demo/visual assets.

Public extraction should be a separate, reviewed publication process, not a
visibility toggle on the full private operating-system repository.

## Rollback and verification procedure

### Before target push

Rollback is deletion of the disposable filtered repository and a fresh
derivation from the unchanged preservation mirror, verified source tag, and
pre-filter bundle. The source repository and empty target are unchanged. Any
mismatch in paths, commit mapping, secrets, tests, links, or package outputs
blocks the push.

### After an authorized initial target push

1. Freeze further target writes and keep the repository private.
2. Record the unexpected refs and object IDs; clone them into a diagnostic
   archive.
3. Compare target refs with the approved filtered refs and migration record.
4. If only normalized target `main` is wrong and the raw filtered tag passed all
   safety gates, use the recorded pushed-main SHA as an exact lease and roll
   target `main` back to the verified raw filtered commit:

   ```bash
   pushed_target_main="<recorded-normalized-main-sha>"
   raw_filtered_commit="<recorded-raw-filtered-sha>"
   git update-ref refs/heads/rollback-main "$raw_filtered_commit"
   git push \
     --force-with-lease="refs/heads/main:$pushed_target_main" \
     destination refs/heads/rollback-main:refs/heads/main
   ```

   This is an explicitly authorized target-only rollback. Temporarily relax
   target branch protection if required, then restore it. The prohibition on
   force-pushing the source repository remains absolute.
5. If unsafe history reached the target, moving `main` is insufficient. Revoke
   or rotate credentials, follow incident handling, and obtain owner approval
   either to change the default branch and delete the exact migration refs or
   to delete the target and recreate it as a private empty repository. GitHub
   may reject deletion of its default branch, so do not promise a generic ref
   deletion.
6. Verify the target's identity, privacy, and complete advertised ref set.
7. Rebuild from the source tag in a new disposable repository, repeat all
   gates, and require a second approval before retrying.

### Final acceptance

The migration is accepted only when a fresh target clone matches the recorded
normalized target-main commit/tree, the extraction tag peels to the separately
recorded raw filtered commit/tree and is its ancestor, all seven milestone
mappings are nonzero and valid, all package/test/build/demo/link/secret/hygiene
gates pass, GitHub metadata limitations are recorded, branch protections are
active, and the source repository remains unchanged.

## Unresolved decisions before extraction

1. Approve and merge this replacement plan, then record its exact source merge
   commit.
2. Approve the source tag name and creation at the final reviewed source commit.
3. Approve the final treatment of `.claude/skills/3d-asset-generator/` and
   `.mcp.json`; the current baseline decision is exclusion.
4. Define the access-controlled location, retention policy, and redaction rules
   for the GitHub metadata export, pre-filter bundle, commit map, and logs.
5. Approve the one recorded all-history content-replacement rule for the
   machine-specific shorthand in the included ROI design document.
6. Approve retention of the mixed Jarvis history on the current `README.md`
   path in the private target; review again before any public release.
7. Confirm license strategy and binary demo/visual-asset provenance before any
   public release.
8. Approve target-only rollback authority, branch protection, CI policy,
   maintainers, and the later public/private publication boundary.

Until these decisions are recorded, do not create the source tag, run the
filter, add a target remote to a filtered clone, push any target ref, initialize
the target, or change either repository's visibility.
