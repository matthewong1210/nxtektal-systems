# Human-led planning v1 validation record

Base: `4f1fa803e5ea1f9dc5f955e85823a436c3bc86bc`.
All results below are local observed results, not CI or field evidence.
Commands run from `simulation/` unless stated otherwise. The environment was
provisioned with `uv sync --locked --all-extras` (including USD and MQTT).
`UV_CACHE_DIR=/private/tmp/nxtektal-uv-cache` was supplied locally because the
host's default cache is outside the sandbox. Loopback/MQTT tests require host
permission; an initial sandbox-only run had socket permission errors, then the
same suite passed with local test socket access.

## Stage 1: manager response commit receipt

- `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent`
  — **126 passed** in 15.81s.
- `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider`
  — **1815 passed**, no skips, in 211.58s.
- `uv run --no-sync python -B scripts/validate_configs.py`
  — **0 errors, 0 warnings**, all configuration inputs passed.
- Regression negative control temporarily loaded the baseline `respond`
  implementation against the new post-commit queue fault test: **1 expected
  failure** at the reproduced post-commit read; working files were unchanged.
- `git diff --check` — clean. Changes confined to service/API, their tests,
  and supporting documentation. Existing unrelated worktrees were untouched.

Faults cover accept/reject/modify commits followed by queue/journal/ledger
read failure, disappeared projection entry, reliable ledger receipt, duplicate
responses after recovery, and append failures before/after durability. HTTP
coverage distinguishes successful-but-unavailable projection, conflict, and
unknown result. Manager acceptance still does not create a task.

## Stage 2: shared wire contract

- `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/pilot_ops/test_planning_wire_contract.py tests/pilot_ops/test_boundaries.py tests/site_agent/test_architecture.py`
  — **46 passed** in 0.32s (31 schema/example checks plus architecture guards).
- Every request/response in all six JSON example files validates against the
  strict Draft 2020-12 schema; malformed units, source tags, unknown keys,
  missing keys, booleans masquerading as numbers and UTC precision are rejected.
- Architecture gate: `planning_v1_architecture.md`, **Proceed** using existing
  owners and explicit human-confirmed SIMULATION composition only.
- The contract commit contains docs/schema/examples/tests only; backend
  implementation follows separately. Content IDs in examples are illustrative;
  consumers use server-returned IDs. No frontend code or dependency changed.

## Stage 3: pure planning and evidence workflow

- `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/pilot_ops`
  — **325 passed** in 1.05s.
- The exact architecture/safety subset in `.agent/workflows/testing.md`
  — **205 passed** in 5.88s; two additional planning purity/negative-control
  checks subsequently passed with the package suite above.
- Independent reviews produced regression fixes for nested null evidence,
  boolean/number retry confusion, malformed duplicate requests, deterministic
  semantic replay, and exact confirmation windows. All are covered by focused
  tests; five plan-result shapes also validate against the shared schema.
- Input and plan CAS, original/new amendment history, restore-as-new-version,
  no fabricated missing data, all six replenishment stages, allowed-zone
  ranking, expired evidence, low/typical/high demand and stockout projection
  parity are covered. No frontend calculations or runtime state writes added.
- This commit is the pure policy/evidence layer. Transport, persistence
  composition and simulated task/result integration follow in stage 4.

## Stage 4: local simulation bridge and actual results

- `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider`
  — **2045 passed**, **no skips**, in 163.79s. Includes all relevant package
  suites, architecture/safety guards and the real local Mosquitto wire tests.
- `uv run --no-sync python -B scripts/validate_configs.py`
  — **0 errors, 0 warnings**.
- Independent Edge verification:
  `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/edge_task`
  — **250 passed**, no skips, in 116.92s.
- Independent bridge fault review:
  `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent/test_planning_failures.py`
  — **20 passed** in 1.91s. Covered encoded request IDs, invalid UTF-8,
  human confirmation delay, actual admission time, ambiguous durable writes,
  restart uniqueness, malformed-but-byte-valid evidence, task/schedule identity,
  and verification before transport publication or device startup.
- Real-clock CLI smoke:
  `uv run --no-sync python -B /private/tmp/nxtektal-planning-cli-smoke.py`
  — **PASS**. The script launched `scripts/pilot_dispatch_demo.py --api-only`
  against a new temporary evidence directory, used actual loopback HTTP,
  submitted input/plan/duplicate confirmation, observed one successful task,
  explicitly entered SUPPLIED, verified unchanged input inventory, and shut
  down the process. Exactly one confirmation, schedule and task were observed.
  The durable automated counterpart is
  `tests/site_agent/test_planning_composition.py`; all four result stages are
  exercised there. The smoke script/output are temporary validation artifacts,
  not production code or evidence of a physical ball process.
- `uv build --out-dir /private/tmp/nxtektal-planning-build`
  — source distribution and wheel built; ZIP inspection confirmed all three
  new planning modules are included. Initial restricted-cache build could not
  resolve Hatchling due to sandbox DNS; the standard cache build above passed.
- From repository root:
  `uv run --no-project --python 3.13.14 python -B -m unittest discover -s .github/scripts -p 'test_*.py' -v`
  — **111 passed** in 4.599s. Initial sandbox run had six socket permission
  errors; authorized loopback rerun passed. `uv --version` was **0.11.29**.
- `uv run --no-project --python 3.13.14 python -B .github/scripts/verify_repository.py`
  — **passed**: 597 tracked/nonignored paths, 72 Markdown files, local links,
  anchors, skills, conflict markers, secrets/machine paths, generated artifacts,
  submodules and dependency boundaries.

Final hygiene commands, from the repository root:

```bash
git status --short --branch
git diff --check
git diff --stat
git diff --name-status
git ls-files --others --exclude-standard
git diff --cached --check
```

New text files also received `git diff --no-index --check /dev/null <file>`.
No whitespace errors; no apps implementation changed. Baseline checkout,
pre-existing main modifications and Claude's working directories were untouched.
No push, deployment, real-device connection or main merge was performed.

## Remaining integration and limits

Claude's final commit was not supplied during backend implementation. Its code
has not been imported or independently reviewed; frontend typecheck/tests/build
and combined browser acceptance remain pending that handoff. The user-facing
interface must consume the schema frozen in stage 2. Later documentation
clarifies UTC start-window semantics, original-ID duplicate receipts, and the
internal attribution token; it does not add a second wire shape.

This is a local, unauthenticated SIMULATION rehearsal. Operator names do not
establish permission. Human counts/estimates are evidence, not live truth;
results never directly increment facility inventory. No learned demand model,
hardware bridge, production deployment, remote notification, automatic mode or
throughput/long-history performance claim is included. Existing v0 acceptance
continues to mean human workflow evidence only.
