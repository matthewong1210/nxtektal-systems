# Task admission time read-contract delivery (2026-09-17)

Base: `21bb263b26e5740e5c92893f729ee05491fb5390`.
Branch: `codex/planning-task-admission-time-v1`, isolated worktree.
This is local validation, not CI, deployment or physical execution evidence.
The [architecture gate](planning_v1_architecture.md#task-admission-time-read-projection-2026-09-17)
is **Proceed** within the existing owners.

## Change and consumer handoff

- `scripts/planning_operations.py` projects both task ID and admission time
  through existing `_task_link()` over the same verified journal prefix.
- [Contract](contracts/planning-v1/README.md#task-admission-time-in-the-read-projection),
  [schema](contracts/planning-v1/schema.json), and
  [success example](contracts/planning-v1/examples/success.json) define the
  optional nullable UTC field on the confirmation read projection.
- [Console type/parser](../../apps/site-agent-console/lib/planning.ts) and its
  contract tests accept absent legacy fields as null and preserve supplied UTC
  precision. Only these two frontend files change; no controllers or components.
- Backend admission-time tests, HTTP flow assertions and schema tests cover
  the lifecycle, integrity and compatibility behavior described below.

Claude should read `snapshot.confirmations[].task_created_at_utc` after
`parsePlanningSnapshot()`. The new server always emits null or verified Edge
`TASK_CREATED.recorded_at_utc`; missing fields from older servers become null.
Neither a known `task_id` nor a confirmed/due time supplies a fallback. Mutation
and request-ID recovery receipts intentionally omit this read field. Consumers
that reject extra fields must upgrade with the shared schema.

Display a non-null value as the earliest possible start of actual stages. Do
not use it to auto-fill/save collection, unloading, washing or supply start
times, infer stage duration or ball counts. Independent result evidence remains
required. A bad task/schedule association makes the entire snapshot unavailable
with `planning_unavailable`; null is not an integrity-error recovery value.

The separate whole-course-sim-v2, existing background experiments and Claude's
scheduler health work remain outside this patch. No 18-hole execution bridge,
new authentication or physical connection is included.

## Observed focused and boundary checks

Python environment: `uv 0.11.29`, CPython `3.13.14`, installed with
`uv sync --locked --all-extras` (57 packages, including USD and MQTT).
Run Python commands from `simulation/`; loopback/Mosquitto checks used local
socket permission. Restricted commands used a temporary `UV_CACHE_DIR`.

| Command | Observed result |
| --- | --- |
| `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent/test_planning_composition.py tests/site_agent/test_planning_failures.py tests/pilot_ops/test_planning_wire_contract.py` before implementation | 61 passed, 3.45s |
| `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent/test_planning_admission_time.py` before implementation | 8 expected failures (absent field), 3 passed, 0.39s |
| `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent/test_planning_admission_time.py tests/pilot_ops/test_planning_wire_contract.py` after implementation | 51 passed, 0.46s |
| `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent tests/pilot_ops tests/edge_task/test_schedules.py tests/edge_task/test_inbox.py` | 625 passed, 21.04s |
| Exact architecture/safety command in [testing workflow](../../.agent/workflows/testing.md#python-simulation-and-site-os), unchanged file list | 207 passed, 41.84s |
| `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider` | 2325 passed, no skips, 176.61s; includes all-extras and real local Mosquitto wire tests |
| `uv run --no-sync python -B scripts/validate_configs.py` | 0 errors, 0 warnings |

New regression coverage includes an intent with no schedule; pending,
cancelled, rejected and missed schedules; actual admission delayed beyond the
due and confirmation times; microseconds retained after later reads; null and
admitted replay after restart; wrong task ID, zone and issued time; unchanged
journal and high-water-anchor bytes on reads; original confirmation payloads,
IDs and recovery/duplicate receipts; no inferred outcomes. Existing HTTP flow
also checks null before admission and the actual journal time after admission.

## Console and repository verification

Node `v25.8.2`; commands from `apps/site-agent-console/`:

| Command | Observed result |
| --- | --- |
| `npm ci --offline --no-audit` | 384 packages installed from unchanged lockfile/local cache |
| `npm run typecheck` | Passed |
| `npm run lint` | 0 errors; 2 pre-existing unused-argument warnings in `tests/task-ops.test.ts:47` |
| `npm test` | 16 files, 161 tests passed |
| `NEXT_TELEMETRY_DISABLED=1 npm run build` | Static export passed |
| `npm run smoke` | Loopback HTTP smoke passed |

The frontend worker also ran `npm test -- tests/planning-contract.test.ts`
(13 passed) and typecheck. Root independently ran the complete console suite
above after the shared example changed.

From repository root:

- `uv run --no-project --python 3.13.14 python -B -m unittest discover -s .github/scripts -p 'test_*.py' -v`:
  111 passed in 4.629s.
- `uv run --no-project --python 3.13.14 python -B .github/scripts/verify_repository.py`:
  passed local links/anchors, skills, conflict markers, secret/machine-path,
  artifact and dependency-boundary checks.

Independent diff review using the repository review workflow found no
actionable issues; it did not duplicate the full test run. No dependency,
package export or packaging membership changed. External `npm audit` and a
mounted browser interaction pass were not rerun: this patch only adds a read
field/parser, retains the existing lockfiles, and does not change the UI flow.
The offline install did not send the dependency tree for external auditing.

## Changed files and hygiene

Backend and tests:

- `simulation/scripts/planning_operations.py`
- `simulation/tests/site_agent/test_planning_admission_time.py`
- `simulation/tests/site_agent/test_planning_composition.py`
- `simulation/tests/pilot_ops/test_planning_wire_contract.py`

Shared contract, examples and architecture/validation evidence:

- `simulation/docs/contracts/planning-v1/README.md`
- `simulation/docs/contracts/planning-v1/schema.json`
- `simulation/docs/contracts/planning-v1/examples/success.json`
- `simulation/docs/planning_v1_architecture.md`
- `simulation/docs/planning_task_admission_time_validation.md`

Frontend type/parser only:

- `apps/site-agent-console/lib/planning.ts`
- `apps/site-agent-console/tests/planning-contract.test.ts`

Hygiene used `git status --short --branch`, `git diff --check`,
`git diff --stat`, `git diff --name-status`, and
`git ls-files --others --exclude-standard`. Both new text files also passed
`git diff --no-index --check /dev/null <file>`; no whitespace errors or
unrelated changes. Repository verification passed with the new validation
document included (642 tracked/nonignored paths, 75 Markdown files).
