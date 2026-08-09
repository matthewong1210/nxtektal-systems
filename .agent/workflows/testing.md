# Testing workflow

## Python simulation and Site OS

The merged-main Python project declares `twin = ["usd-core==26.8"]`, but its
`uv.lock` does not contain that extra. Consequently,
`uv sync --locked --all-extras` fails. Do not silently regenerate and commit the
lock during an unrelated task. Provision the current environment without lock
changes, then install the already pinned USD dependency explicitly:

```bash
cd simulation
uv sync --frozen --all-extras
uv pip install --python .venv/bin/python "usd-core==26.8"
```

Treat this as a recorded dependency-hygiene gap. A dedicated dependency change
may reconcile `pyproject.toml` and `uv.lock`, after which this workflow must be
updated to a verified locked all-extras command.

Run a focused package while iterating:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/<package>
```

Examples of `<package>` are `range_ops`, `facility`, `memory`, `telemetry`,
`twin`, `pilot_ops`, `commissioning`, and `site_runtime`. Root Phase 0 tests
live directly under `tests/` and should be selected by file.

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
  tests/test_state_machine.py \
  tests/test_retry_recovery.py \
  tests/test_unload_retry.py \
  tests/test_emergency_stop.py
```

For changes to merged Commissioning or Site Runtime, run the entire relevant
package suites in addition to the architecture/safety subset:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/commissioning \
  tests/site_runtime
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
| AI/LLM integration | Proof outputs remain advisory; static/import tests prevent direct directive, robot-interface, adapter, ROS, actuator, or e-stop access |
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

## Root Jarvis prototype

No automated root test command is configured. Use the narrowest relevant
manual/server/browser smoke check and record exactly what was observed. Do not
claim repository-wide automated coverage.

## Documentation and agent infrastructure

At minimum run the hygiene workflow, validate skill metadata, verify internal
links, and confirm only documentation/agent paths changed. Production suites
are optional for documentation-only changes unless the documentation asserts a
command or contract that needs execution evidence.

## Reporting results

- Name the command and the observed pass/fail/skip count.
- Distinguish focused, full, build, manual, and hygiene checks.
- State why a check was not run.
- Never copy historical PR test totals as the result of the current change.
- Never call a local command "CI" when no repository CI job ran it.
