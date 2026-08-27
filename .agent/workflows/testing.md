# Testing workflow

## Python simulation and Site OS

The Python lock intentionally includes every declared optional dependency,
including `twin` (`usd-core==26.8`) and the script-confined `edge-gateway`
MQTT client. Provision the complete environment while requiring the lock to
remain current:

```bash
cd simulation
uv sync --locked --all-extras
```

Run a focused package while iterating:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/<package>
```

Examples of `<package>` are `range_ops`, `facility`, `memory`, `telemetry`,
`twin`, `pilot_ops`, `commissioning`, `site_runtime`, `agent_runtime`,
`edge_observation`, and `workflow_enablement`.
Root Phase 0 tests live directly under `tests/` and should be selected by file.

Run the architecture suite after any package-boundary or contract change:

```bash
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
  tests/edge_observation/test_architecture.py \
  tests/edge_gateway_live_input/test_architecture.py \
  tests/workflow_enablement/test_architecture.py \
  tests/test_state_machine.py \
  tests/test_retry_recovery.py \
  tests/test_unload_retry.py \
  tests/test_emergency_stop.py
```

For changes to merged Commissioning, Site Runtime, Agent Runtime, the Edge
Observation adapter kit, or Workflow Enablement, run the entire relevant
package suites in addition to the architecture/safety subset:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/commissioning \
  tests/site_runtime \
  tests/agent_runtime \
  tests/edge_observation \
  tests/workflow_enablement
```

Run the full suite before handing off a Python production/contract change:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider
uv run --no-sync python -B scripts/validate_configs.py
```

Build the package when packaging/export membership changes:

```bash
build_dir="$(mktemp -d)"
uv build --out-dir "$build_dir"
```

Inspect the temporary output, then remove that exact temporary directory when
safe. Do not build into the repository.

## Required test properties by change type

| Change | Expected evidence |
|---|---|
| Read-only downstream observer | RNG-state equality and trajectory/event/metric/observation parity |
| Serializer, ID, or ledger | Canonical bytes, stable ordering, duplicate/truncation/edit rejection, replay verification |
| Alternate input/capture path | Equality or explicit drift comparison against the canonical path |
| Package boundary | AST/import-block/no-mention guard and a negative control when the mechanism is new |
| FacilityState schema | All downstream adapters, serializers, twin mapping, and drift guards reviewed/tested |
| Decision recommendation | Determinism, provenance, unavailable-data behavior, and proof no execution surface is introduced |
| Simulator control | SafetyShield admission/rejection, deterministic replay, conservation, and downstream full suite |
| Robot interface/controller/adapter | Timeouts, invalid sequencing, bounded retry/recovery, safe retract, latched e-stop, no post-e-stop motion, adapter/controller separation |
| Commissioning contract/projection | Strict schema/provenance/immutability, canonical conflict-safe storage, one-way detached projections, forbidden-import guards, downstream integration review |
| Site Runtime orchestration | Input/freshness and quality rejection; exact FacilityState/AssemblyReport retention; deterministic envelope ID; strict sequence/replay; checkpoint recovery and idempotent publication; setup-only commissioning seam; no duplicate domain contracts, policy, or execution imports |
| Agent Runtime composition | Rejected input never reaches policy; one evaluation outcome per admitted envelope; deterministic evaluation/trace/recommendation IDs; restart/replay idempotency and divergence fail-closed; workflow legality and recommendation immutability; byte-identical evidence; boundary guards including no execution/network/wall-clock surface |
| AI/LLM integration | Proof outputs remain advisory; static/import tests prevent direct directive, robot-interface, adapter, ROS, actuator, or e-stop access |
| Edge observation adapter | Calibration identity/unit/range/timestamp fail-closed behavior; explicit MISSING instead of an optimistic default; unmapped raw fields reported; deterministic observation identity; at-least-once feed semantics; boundary guards proving no transport, network, robot, actuator, or e-stop surface |
| Physical/config value | Provenance and placeholder census/validation |
| Bug fix | A regression test that fails for the reproduced defect |

## ROI engine

From `nxtektal-roi-engine/`:

```bash
npm ci
npm run typecheck
npm test
npm run build
```

Formula semantics are locked. Test exact formula IDs, evidence governance,
worked examples, decimal precision, and backward recomputability when a new
model version is intentionally introduced.

## Operational Replay web app

Use Node.js `>=22.13.0` as required by the package manifest. From
`apps/operational-replay/`:

```bash
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm run smoke
npm audit --omit=dev
```

The tests must cover deterministic parsing, malformed and missing artifacts,
advice/task/outcome separation, simulation/reference labeling, no invented
motion, source-file mapping, forbidden imports, and machine-specific paths.

## Documentation and agent infrastructure

At minimum run the hygiene workflow, validate skill metadata, verify internal
links, and confirm only documentation/agent paths changed. Production suites
are optional for documentation-only changes unless the documentation asserts a
command or contract that needs execution evidence.

The repository CI policy helpers and complete repository verifier run from the
repository root:

```bash
case "$(uv --version)" in
  "uv 0.11.29"*) ;;
  *) exit 1 ;;
esac
uv python install 3.13.14
test "$(uv run --no-project --python 3.13.14 python -c \
  'import platform; print(platform.python_version())')" = "3.13.14"
uv run --no-project --python 3.13.14 python -B \
  -m unittest discover -s .github/scripts -p 'test_*.py' -v
uv run --no-project --python 3.13.14 python -B \
  .github/scripts/verify_repository.py
ci_diff_base="$(git merge-base origin/main HEAD)"
git diff --check "$ci_diff_base"...HEAD --
git diff --check HEAD --
```

The stable GitHub Actions checks, pinned tool versions, exact local equivalents,
locked all-extras coverage, ROI audit policy, and replay verification path are
documented in [`docs/CI.md`](../../docs/CI.md).

## Reporting results

- Name the command and the observed pass/fail/skip count.
- Distinguish focused, full, build, manual, and hygiene checks.
- State why a check was not run.
- Never copy historical PR test totals as the result of the current change.
- Never call a local command a CI result; only an observed GitHub Actions job
  run is CI evidence.
