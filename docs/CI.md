# CI and local verification

The repository CI workflow is
[`verification.yml`](../.github/workflows/verification.yml). It runs for pull
requests targeting `main`, pushes to `main`, and manual dispatches. The workflow
has only `contents: read` permission, does not persist checkout credentials,
uses no repository secrets, deployment steps, publishing steps, dependency
caches, or repository/package/release writes, and cancels superseded runs for
the same pull request or ref. It uploads only the npm audit evidence described
below as a retained GitHub Actions artifact. Every action is pinned to an
immutable commit SHA.

CI pins Python 3.13.14, uv 0.11.29, Node.js 24.14.1 with its bundled npm
11.11.0 for the ROI engine, and Node.js 22.23.2 for Operational Replay. These
are CI tooling choices within the versions supported by the existing projects;
they do not change any dependency manifest or lockfile.

## Stable checks

| Required-check candidate | Responsibility |
|---|---|
| `docs-hygiene` | Tests the CI policy helpers; checks whitespace in the committed event change set; verifies local Markdown links and anchors, fence balance, repository skill metadata, conflict markers, likely credentials, machine paths, excluded legacy paths, generated/cache/build artifacts, unexpected symlinks and submodules, and forbidden dependencies; proves the checkout was not mutated. External URLs are not fetched. |
| `python-verification` | Installs every Python extra with the recorded USD workaround; runs the focused Site Runtime, Shadow Ops, Commissioning, architecture/import/safety, and complete suites; validates configs; compiles sources; builds and inspects the wheel/sdist; installs the wheel in isolation; and runs dependency checks. |
| `roi-verification` | Installs the locked npm graph, typechecks, tests, and builds the formula-locked ROI engine; requires zero production vulnerabilities and applies the accepted development-advisory ratchet. |
| `operational-replay-verification` | Installs the independent locked Operational Replay graph under Node.js 22.23.2, then typechecks, lints, tests, builds, live-smokes the HTTP surface, and requires zero production dependency vulnerabilities. |
| `replay-demo-verification` | Runs focused benchmark/viewer/demo/twin tests, two complete 400-episode benchmarks, two viewer exports, two state/briefing captures, two USD builds, byte-compares each pair, and live-smokes Streamlit health and HTTP responses. |

Job names are intentionally explicit and stable. Renaming one changes the
GitHub required-check context and must be coordinated with repository rules.

## Exact local equivalents

Run these commands from a clean candidate checkout. Generated distributions,
audit reports, and replay artifacts go to a temporary directory rather than the
repository. Package-native ignored dependency/build directories remain inside
their package, and every successful job finishes by proving that tracked and
nonignored checkout state did not change.

First install uv 0.11.29 and use a trusted toolchain manager that can select the
two exact Node.js versions below. The local path deliberately fails closed when
tool versions differ, and uv acquires the exact Python patch release used by
CI:

```bash
case "$(uv --version)" in
  "uv 0.11.29"*) ;;
  *) exit 1 ;;
esac
uv python install 3.13.14
test "$(uv run --no-project --python 3.13.14 python -c \
  'import platform; print(platform.python_version())')" = "3.13.14"
```

### Documentation and hygiene

```bash
uv run --no-project --python 3.13.14 python -B \
  -m unittest discover -s .github/scripts -p 'test_*.py' -v
uv run --no-project --python 3.13.14 python -B \
  .github/scripts/verify_repository.py
ci_diff_base="$(git merge-base origin/main HEAD)"
git diff --check "$ci_diff_base"...HEAD --
git diff --check HEAD --
git diff --exit-code HEAD --
test -z "$(git ls-files --others --exclude-standard)"
```

### Python

The current `uv.lock` omits the declared `twin` extra. Preserve the lockfile:

```bash
cd simulation
uv sync --python 3.13.14 --frozen --all-extras
uv pip install --python .venv/bin/python "usd-core==26.8"
uv run --no-sync python -B - <<'PY'
from importlib.metadata import version
from pxr import Usd
from streamlit.testing.v1 import AppTest

assert version("usd-core") == "26.8"
print("optional coverage imports passed:", Usd.__name__, AppTest.__name__)
PY
uv pip check --python .venv/bin/python
```

Every later project invocation uses `--no-sync` so uv does not remove the
manually installed USD package:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_runtime
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/pilot_ops
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/commissioning
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/test_architecture.py \
  tests/range_ops/test_eval_and_architecture.py \
  tests/facility/test_state.py \
  tests/facility/test_regressions.py \
  tests/memory/test_guards.py \
  tests/telemetry/test_guards.py \
  tests/twin/test_guards_package.py \
  tests/twin/test_guards_stream.py \
  tests/range_viewer/test_protection.py \
  tests/range_demo/test_protection.py \
  tests/pilot_ops/test_boundaries.py \
  tests/commissioning/test_guards.py \
  tests/site_runtime/test_architecture.py \
  tests/site_runtime/test_rejection.py \
  tests/agent_runtime/test_architecture.py \
  tests/test_state_machine.py \
  tests/test_retry_recovery.py \
  tests/test_unload_retry.py \
  tests/test_emergency_stop.py
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider
uv run --no-sync python -B scripts/validate_configs.py
```

Compile, build, inspect, and install in isolation:

```bash
ci_tmp="$(mktemp -d)"
PYTHONPYCACHEPREFIX="$ci_tmp/python-bytecode" \
uv run --no-sync python -m compileall -q -f \
  nxt_sim nxt_range_ops nxt_range_agent nxt_facility nxt_memory \
  nxt_telemetry nxt_range_viewer nxt_range_demo nxt_range_twin \
  nxt_pilot_ops nxt_commissioning nxt_site_runtime nxt_agent_runtime \
  scripts ../.github/scripts

python_dist_dir="$ci_tmp/python-dist"
mkdir -p "$python_dist_dir"
uv build --out-dir "$python_dist_dir"
uv run --no-sync python -B \
  ../.github/scripts/verify_python_distribution.py "$python_dist_dir"

uv export --frozen --no-dev --no-emit-project --no-hashes \
  --output-file "$ci_tmp/nxt-sim-constraints.txt"
uv venv --python 3.13.14 "$ci_tmp/nxt-sim-isolated"
set -- "$python_dist_dir"/*.whl
test "$#" -eq 1
wheel_path="$1"
uv pip install --python "$ci_tmp/nxt-sim-isolated/bin/python" \
  --constraints "$ci_tmp/nxt-sim-constraints.txt" "$wheel_path"
(
  cd "$ci_tmp"
  "$ci_tmp/nxt-sim-isolated/bin/python" -I - <<'PY'
from importlib import import_module
from importlib.metadata import version
from importlib.util import find_spec

shipped = (
    "nxt_sim", "nxt_range_ops", "nxt_facility", "nxt_memory",
    "nxt_telemetry", "nxt_range_twin", "nxt_pilot_ops",
    "nxt_commissioning", "nxt_site_runtime", "nxt_agent_runtime",
)
repository_only = ("nxt_range_agent", "nxt_range_viewer", "nxt_range_demo")
for name in shipped:
    import_module(name)
for name in repository_only:
    assert find_spec(name) is None, f"repository-only package installed: {name}"
print("isolated wheel imports passed:", version("nxt-sim"))
PY
)
uv pip check --python "$ci_tmp/nxt-sim-isolated/bin/python"
cd ..
git diff --check HEAD --
git diff --exit-code HEAD --
test -z "$(git ls-files --others --exclude-standard)"
```

Do not substitute `uv lock --check`: it currently fails because of the known
`twin`-extra gap. Repairing `uv.lock` is a separate dependency change.

### ROI

```bash
test "$(node --version)" = "v24.14.1"
test "$(npm --version)" = "11.11.0"
cd nxtektal-roi-engine
ci_tmp="$(mktemp -d)"
npm ci
npm run typecheck
npm test
npm run build -- --outDir "$ci_tmp/roi-dist"

ci_audit_dir="$ci_tmp/roi-audit"
mkdir -p "$ci_audit_dir"
set +e
npm audit --omit=dev --json > "$ci_audit_dir/production.json"
production_status=$?
npm audit --json > "$ci_audit_dir/development.json"
development_status=$?
set -e
uv run --no-project --python 3.13.14 python -B \
  ../.github/scripts/verify_npm_audit.py \
  production "$ci_audit_dir/production.json"
test "$production_status" -eq 0
case "$development_status" in
  0|1) ;;
  *) exit "$development_status" ;;
esac
uv run --no-project --python 3.13.14 python -B \
  ../.github/scripts/verify_npm_audit.py \
  development "$ci_audit_dir/development.json"
cd ..
git diff --check HEAD --
git diff --exit-code HEAD --
test -z "$(git ls-files --others --exclude-standard)"
```

CI retains both npm audit JSON reports and exit codes as a downloadable artifact
for 14 days. Production dependencies must remain at zero vulnerabilities. The
unchanged private-repository development baseline temporarily accepts these
advisories:

| Advisory | Maximum accepted severity |
|---|---|
| `GHSA-67mh-4wv8-2f99` | moderate |
| `GHSA-fxqj-rqcc-2cmp` | moderate |
| `GHSA-4w7w-66w2-5vf9` | moderate |
| `GHSA-v6wh-96g9-6wx3` | moderate |
| `GHSA-2v37-7h3g-55p8` | high |
| `GHSA-fx2h-pf6j-xcff` | high |
| `GHSA-5xrq-8626-4rwp` | critical |

A new advisory, an unclassified audit result, or a severity increase fails.
An advisory that disappears does not fail. The development graph is also capped
at 0 info, 0 low, 4 moderate, 2 high, and 1 critical nodes; another affected
wrapper node, a replacement package node, an unexpected advisory relationship,
or a node severity above its reachable accepted advisory ceiling fails. The
accepted baseline lives in
[`verify_npm_audit.py`](../.github/scripts/verify_npm_audit.py); remove resolved
entries in a later dependency-hygiene change.

The dependency remediation remains a separate concern tracked in
[issue #2](https://github.com/matthewong1210/nxtektal-systems/issues/2). This CI
foundation does not update ROI dependencies. The raw development audit's
expected nonzero exit does not fail an unchanged baseline; the committed
policy verifier fails only new, substituted, expanded, or more severe debt.

### Operational Replay

Select the exact Node.js 22 runtime used by CI, then consume the committed
application scripts without introducing a root JavaScript workspace or coupling
to the Python/ROI dependency graphs. The application manifest supports
Node.js `>=22.13.0`; CI selects 22.23.2, the Node 22 security release current
when this workflow was configured, because it also satisfies the locked Linux
optional tooling engine range.

```bash
test "$(node --version)" = "v22.23.2"
export NEXT_TELEMETRY_DISABLED=1
cd apps/operational-replay
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm run smoke
npm audit --omit=dev
cd ../..
git diff --check HEAD --
git diff --exit-code HEAD --
test -z "$(git ls-files --others --exclude-standard)"
```

The production audit must remain at zero vulnerabilities. The build and HTTP
smoke validate only the checked-out read-only application. CI disables Next.js
telemetry and gives the smoke step a two-minute outer timeout; the job uploads
no artifact and performs no hosting or deployment.

Public deployment remains blocked by
[issue #4](https://github.com/matthewong1210/nxtektal-systems/issues/4) until
`og.png` is cleared or replaced. This validation workflow neither clears that
asset nor creates a public deployment.

### Replay and demo

Use the Python environment from the Python setup above, then run:

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/range_agent tests/range_viewer tests/range_demo \
  tests/twin/test_capture.py tests/twin/test_usd_determinism.py

ci_tmp="$(mktemp -d)"
replay_root="$ci_tmp/replay-demo"
mkdir -p "$replay_root"
for run_id in a b; do
  uv run --no-sync python -B -m nxt_range_agent.benchmark \
    --out "$replay_root/benchmark-$run_id" --quiet
done
diff -qr "$replay_root/benchmark-a" "$replay_root/benchmark-b"
uv run --no-sync python -B - \
  "$replay_root/benchmark-a/episodes.parquet" <<'PY'
import sys

import pyarrow.parquet as pq

rows = pq.read_metadata(sys.argv[1]).num_rows
assert rows == 400, f"expected 400 benchmark episodes, found {rows}"
print("benchmark episodes:", rows)
PY

for run_id in a b; do
  uv run --no-sync python -B -m nxt_range_viewer \
    --out "$replay_root/viewer-$run_id" \
    --scenario demand_spike --policy demand_forecast_dispatch --seed 101 \
    --benchmark-report "$replay_root/benchmark-$run_id/report.json"
  uv run --no-sync python -B scripts/facility_twin_capture.py \
    --scenario demand_spike --policy demand_forecast_dispatch --seed 101 \
    --every-steps 1 --site-id sim-baseline --deployment-id ci \
    --twin-root "$replay_root/capture-$run_id/twin" \
    --demo-root "$replay_root/capture-$run_id/demo"
done
diff -qr "$replay_root/viewer-a" "$replay_root/viewer-b"
diff -qr "$replay_root/capture-a" "$replay_root/capture-b"

episode_a="$replay_root/capture-a/twin/sim-baseline/ci/demand_spike-seed101"
episode_b="$replay_root/capture-b/twin/sim-baseline/ci/demand_spike-seed101"
uv run --no-sync python -B -m nxt_range_twin \
  --episode-dir "$episode_a" --out "$replay_root/usd-a"
uv run --no-sync python -B -m nxt_range_twin \
  --episode-dir "$episode_b" --out "$replay_root/usd-b"
diff -qr "$replay_root/usd-a" "$replay_root/usd-b"
uv run --no-sync python -B ../.github/scripts/smoke_streamlit.py \
  "$replay_root/viewer-a"
cd ..
git diff --check HEAD --
git diff --exit-code HEAD --
test -z "$(git ls-files --others --exclude-standard)"
```

On the baseline commit, two complete 400-episode runs measured 76.49s and
76.31s locally and all benchmark outputs were byte-identical. Because that cost
is reasonable, pull requests run the full benchmark twice; CI does not weaken
the matrix to a subset. Same-commit byte identity is not a golden comparison
against `main`.

## Failure and architecture behavior

Steps use normal fail-fast behavior; there is no `continue-on-error`. The ROI
audit commands capture npm's expected nonzero development-audit status, then a
separate policy step decides whether it is acceptable. Operational Replay's
typecheck, lint, tests, build, HTTP smoke, and production audit fail directly.
A missing optional USD or Streamlit dependency fails through explicit imports,
CLI execution, and live HTTP smoke instead of being hidden by a skipped test.

The Python job runs every existing architecture and safety guard. A robust
generic reachability guard from future LLM/agent code to execution surfaces, or
a machine-readable registry for duplicate advisory ownership and silent
aggregation, does not exist today. Adding broad text searches would be brittle,
so this PR adds no such guard and changes no production code. A future guard
should first define an explicit dependency/ownership contract, then test that
contract mechanically.

The workflow creates check results and the explicitly retained npm audit
artifact; it does not write repository contents, packages, releases,
deployments, or Site state. Branch protection and repository rules are GitHub
settings outside this repository, and have not been mechanically configured or
verified by this change. Do not claim `main` is protected until those settings
require the stable checks above.
