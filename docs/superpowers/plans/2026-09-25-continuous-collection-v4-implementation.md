# Continuous Collection Execution V4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate continuous SIMULATION service that accepts multiple Planning confirmations during one finite V3 session, executes them sequentially through the existing Edge/device/simulator path, and presents strict multi-execution evidence without changing the fixed 3C service.

**Architecture:** Keep Planning, `ScheduleService`, Edge journals, the V3 execution store, `JointDispatchPolicy`, `SafetyShield`, `RangeSimulation`, and `BallLedger` as their existing owners. Add a new V4 composition root with one serialized driver loop, deterministic confirmation-to-execution materialization, a versioned capability declaration, and a durable pre-acceptance rejection bridge; keep all execution APIs read-only. The accepted architecture gate is **RESHAPE**: no new domain package, queue truth, scheduler, execution POST, or change to the fixed 3C service.

**Tech Stack:** Python 3.13, stdlib JSON/file locking/threading/HTTP, SimPy, Gymnasium, pytest, jsonschema, TypeScript, React, Vitest, Next.js static export.

**Spec:** `docs/superpowers/specs/2026-09-25-continuous-collection-v4-design.md`

**Implementation status (2026-10-01):** Tasks 1–7 are implemented and locally
verified through code head `27fcea2d4639dc695f57bd342998803967ecbd09`.
Task 8 fresh HTTP reproduction, restart reproduction, and isolated GET-purity
proof are complete; normative full-suite verification, final independent review,
documentation commit, and clean-head verification remain in progress. The branch
is local and unmerged.

| Task | Implementation commit(s) |
|---|---|
| 1 | `c11d44c3da575ed7f2a1c48275108125e3d55aca` |
| 2 | `253322991cc9a84de3409e8bc1d715e2b2e0ec99` |
| 3 | `ea779f80836428f40f61b7c8cc4d163c66a9823a` |
| 4 | `d322856271927542e81e7cc88b88f2579c25c9d0` |
| 5 | `dc1e521c4d35ec771bca83c45ab02bbf34ee0e7a` |
| 6 | `d755934cebdb423bc77e04dcae076cc4cc1ce238` |
| 7 | `907a5a1de521968bbd899e535eba1e70f32eb84d`, `a897467b2c3a1689ce6d4f14599e8f581d87473b`, `49d78729c38d3be5e57afe54c4f683ae4ec2bcc8`, `27fcea2d4639dc695f57bd342998803967ecbd09` |

## Global Constraints

- Start implementation from the docs-only plan-delivery commit named in the handoff for this file; verify its parent is exact design commit `de18e58ccce0907f7c0365c33fa6400b5069bc12`. Use a new worktree `/private/tmp/nxtektal-continuous-collection-v4-impl` on branch `codex/continuous-collection-v4`.
- Leave `simulation/scripts/course_collection_execution_demo.py` and `simulation/scripts/course_collection_execution_service.py` as the fixed 3C regression entry points; do not inherit from them to override their single-task assumptions.
- V4 covers multiple sequential tasks inside one finite V3 session. It does not add cross-session rollover, multiple robots, multiple handoff stations, physical devices, ROS, cameras, or live-course claims.
- Preserve `nxt-collection-executions/v1`, the single-`RUNNING` lease, existing busy-schedule rejection, original-policy non-`Wait` precedence, `SafetyShield` final admission, and BallLedger quantity authority.
- Keep the entire path `SIMULATION` only. An LLM, browser, recommendation, or advisory component must never call a simulator directive, device interface, robot command, e-stop reset, or protection-clear operation.
- Do not auto-write Planning outcomes or infer washing, supply, replenishment, clean inventory, or physical performance from collection/unload evidence.
- HTTP GET handlers are pure reads. They must not recover Planning, tick schedules, bind tasks, admit devices, publish events, advance simulation, or change any evidence file.
- HTTP Planning confirmation persists intent only. The single background driver owns recovery, due-time admission, binding, execution admission, outbox drain, publication, and exactly one `course_session_v3.run()` call per iteration.
- V4 must derive each internal execution request ID only from the durable binding ID. Repeated scans, UNKNOWN recovery, and process restart must reuse the same request body and ID.
- Robot incarnation is fixed by provisioning identity. An ordinary process restart increments only `boot_sequence`; before ACCEPTED, it resumes the one stable same-incarnation request/attempt. Only explicit re-provisioning or another verified different incarnation may produce `incarnation_mismatch`.
- A normal task terminal does not stop an ACTIVE session. Session `PROTECTED`, `FAILED`, or `ENDED` stops advancement; existing GET evidence remains readable.
- `PAUSED` may save future Planning intent but must not recover, dispatch, materialize, or advance simulation business time until resumed.
- Keep the capability-v1 fixed and legacy examples and emitted payloads byte-compatible. Only the new continuous service emits `nxt-pilot-dispatch/service-capabilities/v2`.
- Codex owns Python, schemas, wire examples, strict TypeScript parsers/clients, backend tests, and integrated service tests. Claude owns React/TSX copy and component/interaction tests after the Codex contract commit is available.
- Use failing tests before implementation, `apply_patch` for edits, small conventional commits, and local branches only. Do not push or merge.

## Review Focus

- A same-incarnation process restart after `TASK_CREATED` but before `ACCEPTED` must reuse one request body, ID, receipt, and attempt; it may return to ACCEPTED/PENDING and must never duplicate a ledger move. Explicit re-provisioning or another verified different incarnation must instead reuse one exact seq-0 `incarnation_mismatch` rejection and close the execution as `REJECTED/IDENTITY_CONFLICT` with zero ledger moves. Task 2 pins the projection and parser exception; Task 3 pins both rejection-journal crash boundaries.
- A confirmation that references an older input revision after a newer input has arrived must bind against its exact historical input record, not `latest_input`. Task 4 pins this with two revisions and distinct cycle evidence.
- Confirmation POST versus driver tick, and pending cancellation versus due-time dispatch, must be linearizable under the outer runtime lock. Task 6 uses synchronization barriers to prove both possible orderings and forbids duplicate or contradictory evidence.
- A second task may find fewer balls than the first task because both share one BallLedger. Task 6 requires positive ledger-backed evidence and conservation but does not force a second 600-ball success.
- An upgraded client must accept only the exact v2 continuous matrix, while an old or mismatched parser fails closed and repeated GETs remain evidence-byte invariant. Task 1 pins cross-version rejection; Task 6 hashes the evidence tree around repeated reads.

---

## File structure and ownership

| Path | Responsibility | Owner |
|---|---|---|
| `simulation/docs/contracts/pilot-dispatch-v0/service-capabilities-v2/` | Frozen v2 continuous capability schema, example, and contract notes | Codex |
| `simulation/scripts/task_ops_service_capabilities.py` | Emit the exact v1 or v2 declaration selected by explicit service mode | Codex |
| `apps/site-agent-console/lib/task-ops.ts` | Strict discriminated parser for v1 fixed/legacy and v2 continuous declarations | Codex |
| `simulation/scripts/course_collection_execution.py` | Stable binding-derived request identity, shared execution requirements, durable pre-acceptance rejection record/replay | Codex |
| `simulation/scripts/course_session_task_device.py` | Reuse, ingest, publish, and reconcile the device-owned seq-0 rejection | Codex |
| `simulation/scripts/planning_operations.py` | Optional confirmation gate and internal historical-input binding snapshot | Codex |
| `simulation/scripts/course_collection_execution_v4_service.py` | New marker, one process lock, continuous runtime, serialized driver, HTTP callback composition, CLI | Codex |
| `simulation/tests/fixtures/continuous-collection-v4/two-task-active.json` | Deterministic backend-generated multi-execution witness consumed by backend and UI tests | Codex generates; Claude reads |
| `apps/site-agent-console/components/capabilities.tsx` | Continuous-mode badge, explanation, and per-operation blocker copy | Claude |
| `apps/site-agent-console/components/execution/CollectionExecutionPanel.tsx` | Source-neutral multi-record introduction; retain service order and read-only behavior | Claude |
| `apps/site-agent-console/components/DispatchPanel.tsx` | Source-neutral schedule/task copy; existing capability gates remain authoritative | Claude |
| `simulation/docs/continuous_collection_execution_v4_runbook.md` | Exact initialize/resume/demo commands and evidence interpretation | Codex/integration owner |

### Task 1: Freeze capability V2 and extend the strict client parser

**Files:**
- Create: `simulation/docs/contracts/pilot-dispatch-v0/service-capabilities-v2/README.md`
- Create: `simulation/docs/contracts/pilot-dispatch-v0/service-capabilities-v2/schema.json`
- Create: `simulation/docs/contracts/pilot-dispatch-v0/service-capabilities-v2/examples/continuous-v3-execution.json`
- Modify: `simulation/scripts/task_ops_service_capabilities.py`
- Modify: `simulation/tests/site_agent/test_task_ops_capabilities_contract.py`
- Modify: `apps/site-agent-console/lib/task-ops.ts`
- Modify: `apps/site-agent-console/tests/task-ops.test.ts`

**Interfaces:**
- Consumes: existing `task_ops_service_capabilities(mode)` and `parseTaskOps(value)` behavior for capability v1.
- Produces: `TaskOpsServiceMode = "FIXED_V3_EXECUTION" | "LEGACY_PILOT_DISPATCH" | "CONTINUOUS_V3_EXECUTION"`; `TaskOpsServiceCapabilities` as a v1/v2 discriminated union; an exact v2 fixture used by Tasks 5-7.

- [x] **Step 1: Add failing Python contract tests without changing the v1 assertions**

Add a second contract root and these assertions to `test_task_ops_capabilities_contract.py`:

```python
V2_CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "docs/contracts/pilot-dispatch-v0/service-capabilities-v2"
)


def test_continuous_v3_capability_v2_schema_and_matrix_are_frozen():
    schema = load(V2_CONTRACT / "schema.json")
    example = load(V2_CONTRACT / "examples/continuous-v3-execution.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(example)
    assert example == {
        "schema": "nxt-pilot-dispatch/service-capabilities/v2",
        "mode": "CONTINUOUS_V3_EXECUTION",
        "operations": {
            "planning_inputs_create": "SUPPORTED",
            "planning_plans_create": "SUPPORTED",
            "planning_confirmations_create": "SUPPORTED",
            "planning_outcomes_create": "SUPPORTED",
            "schedules_create": "UNAVAILABLE",
            "schedules_cancel": "SUPPORTED",
            "notifications_acknowledge": "SUPPORTED",
            "notifications_resolve": "SUPPORTED",
        },
    }
    with pytest.raises(ValidationError):
        Draft202012Validator(load(SCHEMA)).validate(example)
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(load(EXAMPLES / "fixed-v3-execution.json"))


def test_service_producer_keeps_v1_exact_and_emits_only_the_exact_v2_mode():
    assert task_ops_service_capabilities("FIXED_V3_EXECUTION") == load(
        EXAMPLES / "fixed-v3-execution.json"
    )
    assert task_ops_service_capabilities("LEGACY_PILOT_DISPATCH") == load(
        EXAMPLES / "legacy-pilot-dispatch.json"
    )
    assert task_ops_service_capabilities("CONTINUOUS_V3_EXECUTION") == load(
        V2_CONTRACT / "examples/continuous-v3-execution.json"
    )
    with pytest.raises(ValueError, match="unknown task operations service mode"):
        task_ops_service_capabilities("AUTO_DETECT")
```

- [x] **Step 2: Add failing TypeScript cross-version tests**

Load both contract directories in `task-ops.test.ts`, then assert the only valid schema/mode combinations:

```typescript
const CAPABILITIES_V2_EXAMPLES = join(
  import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts",
  "pilot-dispatch-v0", "service-capabilities-v2", "examples",
);
const continuousCapabilities = JSON.parse(
  readFileSync(join(CAPABILITIES_V2_EXAMPLES, "continuous-v3-execution.json"), "utf-8"),
) as Record<string, unknown>;

it("validates continuous V3 v2 capabilities without opening direct schedule creation", () => {
  const parsed = parseTaskOps({ ...taskOpsFixture(), service_capabilities: continuousCapabilities });
  expect(taskOpsCapabilities(parsed).mode).toBe("CONTINUOUS_V3_EXECUTION");
  expect(taskOpsSupports(parsed, "planning_confirmations_create")).toBe(true);
  expect(taskOpsSupports(parsed, "schedules_create")).toBe(false);
  expect(taskOpsSupports(parsed, "schedules_cancel")).toBe(true);
});

it("rejects cross-version modes and mode-inconsistent matrices", () => {
  const fixedAsV2 = { ...fixedV3Capabilities, schema: "nxt-pilot-dispatch/service-capabilities/v2" };
  const continuousAsV1 = { ...continuousCapabilities, schema: "nxt-pilot-dispatch/service-capabilities/v1" };
  const openedDirectSchedule = structuredClone(continuousCapabilities);
  (openedDirectSchedule.operations as Record<string, unknown>).schedules_create = "SUPPORTED";
  for (const capabilities of [fixedAsV2, continuousAsV1, openedDirectSchedule]) {
    expect(() => parseTaskOps({ ...taskOpsFixture(), service_capabilities: capabilities })).toThrow(ManagerApiError);
  }
});
```

- [x] **Step 3: Run the focused tests and verify RED**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent/test_task_ops_capabilities_contract.py
```

Expected: failure because the v2 contract path and continuous producer mode do not exist.

Run from `apps/site-agent-console/`:

```bash
npm test -- tests/task-ops.test.ts
```

Expected: failure because `parseTaskOps` rejects v2.

- [x] **Step 4: Add the exact schema, fixture, and Python producer branch**

Write `schema.json` as the closed single-mode contract:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://nxtektal.local/contracts/pilot-dispatch-v0/service-capabilities/v2",
  "title": "Pilot dispatch service capabilities v2",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema", "mode", "operations"],
  "properties": {
    "schema": { "const": "nxt-pilot-dispatch/service-capabilities/v2" },
    "mode": { "const": "CONTINUOUS_V3_EXECUTION" },
    "operations": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "planning_inputs_create",
        "planning_plans_create",
        "planning_confirmations_create",
        "planning_outcomes_create",
        "schedules_create",
        "schedules_cancel",
        "notifications_acknowledge",
        "notifications_resolve"
      ],
      "properties": {
        "planning_inputs_create": { "const": "SUPPORTED" },
        "planning_plans_create": { "const": "SUPPORTED" },
        "planning_confirmations_create": { "const": "SUPPORTED" },
        "planning_outcomes_create": { "const": "SUPPORTED" },
        "schedules_create": { "const": "UNAVAILABLE" },
        "schedules_cancel": { "const": "SUPPORTED" },
        "notifications_acknowledge": { "const": "SUPPORTED" },
        "notifications_resolve": { "const": "SUPPORTED" }
      }
    }
  }
}
```

The example file is the exact object asserted in Step 1. Extend the producer with these declarations while preserving the old constant as a v1 alias:

```python
TASK_OPS_CAPABILITIES_V1_SCHEMA = "nxt-pilot-dispatch/service-capabilities/v1"
TASK_OPS_CAPABILITIES_V2_SCHEMA = "nxt-pilot-dispatch/service-capabilities/v2"
TASK_OPS_CAPABILITIES_SCHEMA = TASK_OPS_CAPABILITIES_V1_SCHEMA
TaskOpsServiceMode = Literal[
    "FIXED_V3_EXECUTION",
    "LEGACY_PILOT_DISPATCH",
    "CONTINUOUS_V3_EXECUTION",
]

_CONTINUOUS_V3_OPERATIONS = {
    "planning_inputs_create": "SUPPORTED",
    "planning_plans_create": "SUPPORTED",
    "planning_confirmations_create": "SUPPORTED",
    "planning_outcomes_create": "SUPPORTED",
    "schedules_create": "UNAVAILABLE",
    "schedules_cancel": "SUPPORTED",
    "notifications_acknowledge": "SUPPORTED",
    "notifications_resolve": "SUPPORTED",
}


def task_ops_service_capabilities(mode: TaskOpsServiceMode) -> dict[str, object]:
    if mode == "FIXED_V3_EXECUTION":
        schema, operations = TASK_OPS_CAPABILITIES_V1_SCHEMA, _FIXED_V3_OPERATIONS
    elif mode == "LEGACY_PILOT_DISPATCH":
        schema, operations = TASK_OPS_CAPABILITIES_V1_SCHEMA, _LEGACY_OPERATIONS
    elif mode == "CONTINUOUS_V3_EXECUTION":
        schema, operations = TASK_OPS_CAPABILITIES_V2_SCHEMA, _CONTINUOUS_V3_OPERATIONS
    else:
        raise ValueError(f"unknown task operations service mode: {mode!r}")
    return {"schema": schema, "mode": mode, "operations": dict(operations)}
```

- [x] **Step 5: Make the TypeScript capability type a strict discriminated union**

Use exact schema/mode pairing in `task-ops.ts`:

```typescript
export const TASK_OPS_CAPABILITIES_V1_SCHEMA = "nxt-pilot-dispatch/service-capabilities/v1";
export const TASK_OPS_CAPABILITIES_V2_SCHEMA = "nxt-pilot-dispatch/service-capabilities/v2";
export const TASK_OPS_CAPABILITIES_SCHEMA = TASK_OPS_CAPABILITIES_V1_SCHEMA;

type CapabilityOperations = Record<TaskOpsWriteOperation, TaskOpsCapabilityStatus>;
type V1Capabilities = {
  schema: typeof TASK_OPS_CAPABILITIES_V1_SCHEMA;
  mode: "FIXED_V3_EXECUTION" | "LEGACY_PILOT_DISPATCH";
  operations: CapabilityOperations;
};
type V2Capabilities = {
  schema: typeof TASK_OPS_CAPABILITIES_V2_SCHEMA;
  mode: "CONTINUOUS_V3_EXECUTION";
  operations: CapabilityOperations;
};
export type TaskOpsServiceCapabilities = V1Capabilities | V2Capabilities;
export type TaskOpsServiceMode = TaskOpsServiceCapabilities["mode"];
```

Add `CONTINUOUS_V3_OPERATIONS`, then make `validServiceCapabilities` select an expected matrix only for these three exact pairs. Unknown schema, cross-version mode, extra key, missing key, or matrix drift returns `false`.

- [x] **Step 6: Run focused and compatibility tests**

Run the two focused commands from Step 3. Then run from `apps/site-agent-console/`:

```bash
npm run typecheck
```

Expected: all pass, and existing v1 tests remain unchanged and green.

- [x] **Step 7: Commit the contract layer**

```bash
git add simulation/docs/contracts/pilot-dispatch-v0/service-capabilities-v2 simulation/scripts/task_ops_service_capabilities.py simulation/tests/site_agent/test_task_ops_capabilities_contract.py apps/site-agent-console/lib/task-ops.ts apps/site-agent-console/tests/task-ops.test.ts
git commit -m "feat(task-ops): define continuous service capabilities"
```

### Task 2: Persist the exact pre-acceptance identity rejection

**Files:**
- Modify: `simulation/scripts/course_collection_execution.py`
- Modify: `simulation/docs/contracts/collection-execution-v1/README.md`
- Modify: `simulation/tests/course_monitoring/test_course_collection_execution.py`
- Modify: `simulation/tests/pilot_ops/test_collection_execution_wire_contract.py`
- Modify: `apps/site-agent-console/lib/collection-executions.ts`
- Modify: `apps/site-agent-console/tests/collection-executions-contract.test.ts`

**Interfaces:**
- Consumes: a verified `JsonlJournal` `JournalRecord` whose payload contains an Edge `TaskEvent` with `event_sequence == 0`, `kind == REJECTED`, and `reason_code == "incarnation_mismatch"`.
- Produces: `CollectionExecutionStore.record_preacceptance_rejection(execution_id, edge_record, *, now_sim_t_s) -> None`; internal record kind `preacceptance_rejection`; a single exact relational-parser exception for the resulting `REJECTED/IDENTITY_CONFLICT` shape.

- [x] **Step 1: Add failing V3 store tests for projection, idempotency, and near misses**

Build the mismatch event with existing `TaskEvent` and `JsonlJournal` helpers, then assert this exact public projection:

```python
def setup_without_acceptance(api, tmp_path):
    identity, planning, edge_records = inputs(tmp_path / "binding")
    store = api.CollectionExecutionStore(tmp_path / "execution.jsonl", identity)
    binding = store.bind_confirmed_tasks(
        planning, edge_records, identity, 60
    )[0]
    request = api.make_request(
        binding,
        "request-preacceptance",
        "2026-09-16T00:01:00Z",
        "2026-09-16T00:05:00Z",
    )
    store.submit(request)
    return store, binding, request


def append_incarnation_rejection(tmp_path, request):
    event = TaskEvent(
        site_id="synthetic-site",
        deployment_id="synthetic-sim",
        simulation_env_id="fixture",
        task_id=request["task_id"],
        robot_id="picker-01",
        boot_id="fixture-incarnation-002-2",
        boot_sequence=2,
        event_sequence=0,
        kind=EventKind.REJECTED,
        reason_code="incarnation_mismatch",
        detail="request targets a prior device incarnation",
        reported_at_utc="2026-09-16T00:01:00Z",
        phase=None,
    )
    journal = JsonlJournal(tmp_path / "device.jsonl")
    return journal.append(RecordSpec(
        "task_event_persisted",
        "DEVICE",
        event.reported_at_utc,
        {"event": event.to_dict()},
    ))


def test_preacceptance_incarnation_rejection_projects_verified_identity_terminal(api, tmp_path):
    store, _binding, request = setup_without_acceptance(api, tmp_path)
    edge_record = append_incarnation_rejection(tmp_path, request)

    store.record_preacceptance_rejection(
        request["execution_id"], edge_record, now_sim_t_s=60
    )

    record = conform(store)["executions"][0]
    assert (record["state"], record["reason"], record["stage"]) == (
        "REJECTED", "IDENTITY_CONFLICT", "TERMINAL"
    )
    assert record["started_sim_t_s"] is None
    assert record["execution_deadline_sim_t_s"] is None
    assert record["terminal_sim_t_s"] == 60
    assert record["assignment_id"] is None
    assert record["actions"] == []
    assert record["raw_quantity"]["status"] == "NOT_REACHED"
    assert record["unload_quantity"]["status"] == "NOT_REACHED"
    assert record["raw_quantity"]["balls"] is None
    assert record["unload_quantity"]["balls"] is None
    assert record["edge_evidence"] == {
        "task_id": request["task_id"],
        "accepted": False,
        "verified": True,
        "effective_state": "REJECTED",
        "reason": "incarnation_mismatch",
        "terminal_states": ["REJECTED"],
        "event_ids": [edge_record.record_id],
        "result_verification": "VERIFIED",
    }
    assert record["conflicts"]["incarnation_mismatch"] is True
    assert record["device_protection"] == {
        "protected": True,
        "reasons": ["INCARNATION_MISMATCH"],
        "authorization_blocked": True,
    }
```

Add two more tests that retry the same record ID and prove journal bytes stay unchanged, then submit a different record ID or a near-miss event and prove a fail-closed exception with no append.

- [x] **Step 2: Add failing Python-oracle and TypeScript-parser tests for the one legal conflict shape**

Pin the exact exception and reject all close variants:

```typescript
function preacceptanceIdentityConflictFixture(): CollectionExecutionsSnapshot {
  const result = pending();
  const record = result.executions[0];
  Object.assign(record, {
    state: "REJECTED",
    stage: "TERMINAL",
    reason: "IDENTITY_CONFLICT",
    terminal_sim_t_s: 60,
    assignment_id: null,
    started_sim_t_s: null,
    execution_deadline_sim_t_s: null,
    actions: [],
    success_display_allowed: false,
  });
  record.edge_evidence = {
    task_id: record.task_id,
    accepted: false,
    verified: true,
    effective_state: "REJECTED",
    reason: "incarnation_mismatch",
    terminal_states: ["REJECTED"],
    event_ids: ["device-record-preacceptance"],
    result_verification: "VERIFIED",
  };
  record.device_protection = {
    protected: true,
    reasons: ["INCARNATION_MISMATCH"],
    authorization_blocked: true,
  };
  record.conflicts.incarnation_mismatch = true;
  return result;
}

function withExecution(
  source: CollectionExecutionsSnapshot,
  patch: Partial<CollectionExecutionsSnapshot["executions"][number]>,
) {
  const result = structuredClone(source);
  Object.assign(result.executions[0], patch);
  return result;
}

it("accepts the exact verified preacceptance incarnation rejection", () => {
  const snapshot = preacceptanceIdentityConflictFixture();
  expect(parseCollectionExecutions(snapshot).executions[0]).toMatchObject({
    state: "REJECTED",
    reason: "IDENTITY_CONFLICT",
    conflicts: { incarnation_mismatch: true },
    edge_evidence: { accepted: false, effective_state: "REJECTED", result_verification: "VERIFIED" },
  });
});

it("rejects contradictory preacceptance incarnation rejection evidence", () => {
  const base = preacceptanceIdentityConflictFixture();
  const variants = [
    withExecution(base, { state: "INCONCLUSIVE" }),
    withExecution(base, { started_sim_t_s: 60 }),
    withExecution(base, { assignment_id: "assignment-illegal" }),
    withExecution(base, { conflicts: { ...base.executions[0].conflicts, terminal_conflict: true } }),
  ];
  for (const value of variants) expect(() => parseCollectionExecutions(value)).toThrow();
});
```

- [x] **Step 3: Run focused tests and verify RED**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/course_monitoring/test_course_collection_execution.py tests/pilot_ops/test_collection_execution_wire_contract.py
```

Run from `apps/site-agent-console/`:

```bash
npm test -- tests/collection-executions-contract.test.ts
```

Expected: the store method is absent and both strict readers reject the approved identity-conflict shape.

- [x] **Step 4: Add the internal journal record and replay projection**

Add `"preacceptance_rejection"` to `RECORD_KINDS` and initialize `state["preacceptance_rejections"]` as an execution-ID keyed mapping in `_replay()`. Add the public method with a payload that stores only causal facts:

```python
def record_preacceptance_rejection(
    self,
    execution_id: str,
    edge_record: JournalRecord,
    *,
    now_sim_t_s: float,
) -> None:
    payload = {
        "execution_id": execution_id,
        "now_sim_t_s": now_sim_t_s,
        "edge_record": edge_record.to_dict(),
    }

    def build(records):
        state = self._replay(records)
        existing = state["preacceptance_rejections"].get(execution_id)
        if existing is not None:
            _require(existing == payload, "conflicting pre-acceptance rejection")
            return []
        _apply_preacceptance_rejection(state, payload)
        return [self._spec("preacceptance_rejection", payload, now_sim_t_s)]

    self.journal.append_via(build)
```

Implement `_apply_preacceptance_rejection` once and call it from both the write-time validation and `_replay()`. It must validate the record kind, exact event shape, binding/task/site/deployment/robot identity, differing incarnation, monotonic clock, absence of acceptance/start/assignment/action/quantity/prepared intent, and uniqueness of record ID before applying the exact projection from Step 1.

- [x] **Step 5: Add the exact conflict exception to both relational readers**

In the Python oracle and TypeScript parser, define `knownIdentityConflict` as true only when every approved field matches and `incarnation_mismatch` is the sole true conflict. Apply the existing “any conflict means INCONCLUSIVE” rule only when this predicate is false:

```typescript
const trueConflicts = Object.entries(record.conflicts)
  .filter(([, enabled]) => enabled)
  .map(([name]) => name);
const knownIdentityConflict =
  record.state === "REJECTED" &&
  record.reason === "IDENTITY_CONFLICT" &&
  record.stage === "TERMINAL" &&
  record.started_sim_t_s === null &&
  record.assignment_id === null &&
  record.actions.length === 0 &&
  record.edge_evidence.accepted === false &&
  record.edge_evidence.effective_state === "REJECTED" &&
  record.edge_evidence.reason === "incarnation_mismatch" &&
  record.edge_evidence.result_verification === "VERIFIED" &&
  trueConflicts.length === 1 &&
  trueConflicts[0] === "incarnation_mismatch";
if (trueConflicts.length > 0 && !knownIdentityConflict && record.state !== "INCONCLUSIVE") fail();
```

Keep schema version, frozen examples, and every other conflict rule unchanged. Document in the v1 README the distinction between request-time identity refusal and a durable attempt closed by verified device rejection.

- [x] **Step 6: Run focused contract tests and typecheck**

Run the commands from Step 3, then from `apps/site-agent-console/`:

```bash
npm run typecheck
```

Expected: all pass; `schema.json` is unchanged.

- [x] **Step 7: Commit the durable rejection projection**

```bash
git add simulation/scripts/course_collection_execution.py simulation/docs/contracts/collection-execution-v1/README.md simulation/tests/course_monitoring/test_course_collection_execution.py simulation/tests/pilot_ops/test_collection_execution_wire_contract.py apps/site-agent-console/lib/collection-executions.ts apps/site-agent-console/tests/collection-executions-contract.test.ts
git commit -m "fix(collection-execution): close preacceptance identity rejection"
```

### Task 3: Reconcile and publish the device-owned seq-0 rejection

**Files:**
- Modify: `simulation/scripts/course_session_task_device.py`
- Modify: `simulation/tests/edge_task/test_course_session_task_device.py`

**Interfaces:**
- Consumes: `CollectionExecutionStore.record_preacceptance_rejection(execution_id, edge_record, *, now_sim_t_s)` from Task 2; existing `RobotCore.on_request()`, `TaskEvent.from_dict()`, `JsonlJournal.read()`, and `RobotCore.publish_confirmed_spec(event, when, *, record_sequence)`.
- Produces: `SimulatorBackedTaskDevice.admit(task_request, execution_request, *, crash_hook=None)` returning the original durable receipt after either ACCEPTED or the exact terminal seq-0 rejection; startup reconciliation completes any cross-journal gap before the device reports started.

- [x] **Step 1: Add failing tests for a stale incarnation at admission**

Create a request whose frozen target incarnation explicitly differs from the current provisioned device, then call `admit` and assert both journals and the absence of simulator work. The mismatch is a deliberate stale/re-provisioned fixture condition; an ordinary process restart does not create it:

```python
def test_old_incarnation_admit_closes_seq0_rejection_without_execution(
    tmp_path, monkeypatch
):
    device_api, device, store, identity, task, request = _device_fixture(
        tmp_path,
        monkeypatch,
        task_incarnation="boot-picker-01-previous-device",
    )
    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()

    receipt = restarted.admit(task, request)
    snapshot = store.snapshot(
        server_time_utc="2026-09-16T00:02:00Z",
        session_state="ACTIVE",
    )

    assert receipt["execution_id"] == request["execution_id"]
    assert [r.record_kind for r in restarted.journal.read()].count(
        TASK_EVENT_PERSISTED
    ) == 1
    assert [r.record_kind for r in store.journal.read()].count(
        "preacceptance_rejection"
    ) == 1
    assert snapshot["executions"][0]["state"] == "REJECTED"
    assert snapshot["executions"][0]["actions"] == []
    assert snapshot["executions"][0]["raw_quantity"]["status"] == "NOT_REACHED"
```

Extend the existing `_device_fixture` return values only if the journal path is not already reachable from `device`. Keep using the real `RobotCore.on_start()` path so the current provisioning-derived incarnation and incremented `boot_sequence` remain authentic; the explicit stale target above, not `on_start()`, supplies the incarnation mismatch.

- [x] **Step 2: Add failing crash-boundary, startup, conflict, and publication tests**

Use a parametrized crash hook for the two named boundaries:

```python
@pytest.mark.parametrize(
    "boundary",
    ["after_preacceptance_device_rejection", "after_preacceptance_v3_rejection"],
)
def test_preacceptance_rejection_restart_reuses_both_journals(
    tmp_path, monkeypatch, boundary
):
    device_api, device, store, _identity, task, request = _device_fixture(
        tmp_path,
        monkeypatch,
        task_incarnation="boot-picker-01-previous-device",
    )
    restarted = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    restarted.start()
    with pytest.raises(CrashInjected, match=boundary):
        restarted.admit(task, request, crash_hook=crash_at(boundary))
    reopened = device_api.SimulatorBackedTaskDevice(
        tmp_path / "session",
        device.config,
        "picker-01",
        journal_path=device.journal.path,
        initialize=False,
    )
    reopened.start()
    before = (device.journal.path.read_bytes(), store.journal.path.read_bytes())
    reopened.admit(task, request)
    assert (device.journal.path.read_bytes(), store.journal.path.read_bytes()) == before
    assert store.replay()["executions"][request["execution_id"]]["state"] == "REJECTED"
```

Define the injected failure locally so the test does not depend on a production exception:

```python
class CrashInjected(RuntimeError):
    pass


def crash_at(expected):
    def inject(boundary, _payload):
        if boundary == expected:
            raise CrashInjected(boundary)
    return inject
```

Also add tests that two matching seq-0 records or one near-match fail closed, and that `confirm_published()` records the original device journal sequence in `confirmed_rejections`.

- [x] **Step 3: Run the device tests and verify RED**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/edge_task/test_course_session_task_device.py
```

Expected: stale-incarnation admission raises `authorization_blocked`, startup does not reconcile seq-0, and publication confirmation cannot locate the request-level event.

- [x] **Step 4: Add the exact seq-0 lookup and admission branch**

Add this private lookup contract:

```python
def _incarnation_rejection_for(
    self,
    task_request: TaskRequest,
) -> JournalRecord | None:
    matches = []
    for record in self.journal.read():
        if record.record_kind != TASK_EVENT_PERSISTED:
            continue
        event = TaskEvent.from_dict(record.payload["event"])
        if (
            event.site_id == task_request.site_id
            and event.deployment_id == task_request.deployment_id
            and event.simulation_env_id == task_request.simulation_env_id
            and event.task_id == task_request.task_id
            and event.robot_id == task_request.target_robot_id
            and event.incarnation != task_request.target_incarnation
            and event.event_sequence == 0
            and event.kind == EventKind.REJECTED
            and event.reason_code == "incarnation_mismatch"
        ):
            matches.append(record)
    if len(matches) > 1:
        raise PreconditionFailed(
            "conflicting_preacceptance_rejection",
            "multiple seq-0 incarnation rejections exist for one task",
        )
    return matches[0] if matches else None
```

Extend `admit` with `crash_hook: Callable[[str, Mapping[str, Any]], None] | None = None`. Preserve the existing order `store.submit()` first. Reuse an existing exact rejection before calling `RobotCore.on_request()`. If the resulting device record is the exact mismatch, call the Task 2 store method, fire the two crash hooks immediately after their named durable writes, and return the existing receipt. Any other no-ACCEPTED result still fails closed.

- [x] **Step 5: Reconcile seq-0 before ordinary accepted/restart/outbox evidence**

At the beginning of `_reconcile_persisted_evidence()`, inspect every unaccepted, nonterminal execution and its frozen task request. For an exact seq-0 rejection, call:

```python
admission.store.record_preacceptance_rejection(
    execution_id,
    rejection_record,
    now_sim_t_s=admission.now_sim_t_s,
)
```

Perform this pass inside the existing restart-reconciliation transaction before `start()` may return. A duplicate exact record is a no-op; missing, duplicated, or contradictory evidence raises and keeps the service unstarted.

- [x] **Step 6: Confirm request-level publication by journal record sequence**

In `confirm_published()`, branch on `event_sequence == 0`. Parse the event, locate the same V3-confirmed device record, and call:

```python
self.core.publish_confirmed_spec(
    parsed.to_dict(),
    when,
    record_sequence=record.sequence,
)
```

Keep the existing `(boot_sequence, event_sequence)` task-event confirmation path for sequences above zero.

- [x] **Step 7: Run the full rejection seam and commit**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/course_monitoring/test_course_collection_execution.py tests/edge_task/test_course_session_task_device.py tests/pilot_ops/test_collection_execution_wire_contract.py
```

Run from `apps/site-agent-console/`:

```bash
npm test -- tests/collection-executions-contract.test.ts
npm run typecheck
```

Expected: all pass and repeated restart/admit attempts leave both journals byte-identical.

```bash
git add simulation/scripts/course_session_task_device.py simulation/tests/edge_task/test_course_session_task_device.py
git commit -m "fix(edge-task): reconcile preacceptance device rejection"
```

### Task 4: Add deterministic confirmation and binding admission primitives

**Files:**
- Modify: `simulation/scripts/planning_operations.py`
- Modify: `simulation/scripts/course_collection_execution.py`
- Modify: `simulation/tests/pilot_ops/test_planning.py`
- Modify: `simulation/tests/course_monitoring/test_course_collection_execution.py`

**Interfaces:**
- Consumes: verified `PlanningHistory`, session identity, historical input records, and existing `TASK_CREATED` records.
- Produces: optional `ConfirmationGate`; `PlanningOperations.execution_binding_snapshot(now)`; `derive_execution_requirements(*, plan, input_records, session_identity, evidence_at_utc)`; `require_session_horizon(*, start_at_utc, max_execution_s, session_identity)`; single-task `bind_confirmed_task(planning_snapshot, edge_records, session_identity, now_sim_t_s, *, task_id)`; `request_id_for_binding(binding_id)`.

- [x] **Step 1: Add failing Planning tests for the optional gate and duplicate recovery**

Pin call timing and definite refusal:

```python
def test_confirmation_gate_runs_once_inside_new_confirmation_append(tmp_path):
    calls = []

    def gate(history, confirmation, now):
        calls.append((confirmation["confirmation_id"], now))

    operations = planning_operations(tmp_path, confirmation_gate=gate)
    payload = confirmed_plan_request(operations)
    first = operations.route("POST", "/api/v1/planning/confirmations", payload)
    duplicate = operations.route("POST", "/api/v1/planning/confirmations", payload)
    assert first["record"] == duplicate["record"]
    assert calls == [(first["record"]["confirmation_id"], operations.clock())]


def test_confirmation_gate_conflict_appends_no_confirmation_or_schedule(tmp_path):
    def reject(_history, _confirmation, _now):
        raise PlanningError("planning_conflict", "selection exceeds V3 session horizon")

    operations = planning_operations(tmp_path, confirmation_gate=reject)
    payload = confirmed_plan_request(operations)
    before = operations.journal.path.read_bytes()
    with pytest.raises(SiteAgentError, match="session horizon"):
        operations.route("POST", "/api/v1/planning/confirmations", payload)
    assert operations.journal.path.read_bytes() == before
```

Add local helpers `planning_operations` and `confirmed_plan_request` by extracting the existing fixture construction in this test file; they must call the real input and plan routes.

- [x] **Step 2: Add failing tests for historical inputs, exact horizon, runtime mapping, and stable IDs**

The binding test must create input revision 1, its plan and confirmation, then create revision 2 before `TASK_CREATED`. Assert the first task uses revision 1 evidence:

```python
def test_binding_uses_confirmation_historical_input_after_newer_revision(api, tmp_path):
    identity, planning, edge_records = inputs(tmp_path)
    first = deepcopy(planning["latest_input"])
    first["revision"] = 1
    first["input_digest"] = "a" * 64
    first["zones"][0]["cycle_minutes"]["value"].update(
        {"travel": 2, "collect": 5, "return": 2, "unload": 2}
    )
    second = deepcopy(first)
    second["revision"] = 2
    second["input_digest"] = "b" * 64
    second["zones"][0]["cycle_minutes"]["value"].update(
        {"travel": 3, "collect": 7, "return": 3, "unload": 3}
    )
    planning["latest_input"] = second
    planning["input_records"] = [first, second]
    planning["plans"][0]["input_revision"] = 1
    planning["plans"][0]["input_digest"] = first["input_digest"]
    requirements = api.derive_execution_requirements(
        plan=planning["plans"][0],
        input_records=planning["input_records"],
        session_identity=identity,
        evidence_at_utc=planning["confirmations"][0]["task_created_at_utc"],
    )
    assert requirements["cycle_evidence"]["input_revision"] == 1
    assert requirements["cycle_evidence"]["cycle_minutes"] == {
        "travel": 2, "collect": 5, "return": 2, "unload": 2
    }
    binding = api.bind_confirmed_task(
        planning, edge_records, identity, 60, task_id=planning["confirmations"][0]["task_id"]
    )
    assert binding["cycle_evidence"]["input_revision"] == 1
```

Add parametrized checks for `start + max_execution == session_end` accepted, one second beyond rejected, absent/duplicate runtime binding rejected, and stable request identity:

```python
def test_request_id_is_derived_only_from_binding_id(api):
    binding_id = "a" * 64
    expected = "exec_req_" + digest({
        "schema": "nxt-collection-execution-request-id/v1",
        "kind": "EXECUTE_BOUND_COLLECTION",
        "binding_id": binding_id,
    })[:24]
    assert api.request_id_for_binding(binding_id) == expected
```

- [x] **Step 3: Run focused tests and verify RED**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/pilot_ops/test_planning.py tests/course_monitoring/test_course_collection_execution.py
```

Expected: the gate, internal snapshot, shared derivation, single-task binder, and stable request-ID helper are absent.

- [x] **Step 4: Add the Planning gate inside the journal builder**

Add the optional constructor parameter without changing existing callers:

```python
ConfirmationGate = Callable[[PlanningHistory, Mapping[str, Any], datetime], None]


def __init__(
    self,
    journal: JsonlJournal,
    config,
    facts,
    clock: Callable[[], datetime],
    *,
    site_timezone: str,
    confirmation_gate: ConfirmationGate | None = None,
) -> None:
    self.confirmation_gate = confirmation_gate
```

Immediately after `prepare_confirmation` returns, and only when `response["disposition"] != "duplicate"`, call:

```python
if self.confirmation_gate is not None:
    self.confirmation_gate(history, response["record"], now)
```

Because this runs inside `append_via(build)`, deterministic gate refusal appends nothing. Duplicate request recovery skips the gate and returns the original receipt.

- [x] **Step 5: Add one-read public and internal snapshot builders**

Extract the existing projection body into `_snapshot_from(records, history, now)`. Implement:

```python
def snapshot(self, now: datetime) -> dict[str, Any]:
    records = self.journal.read()
    history = self.history(records)
    return self._snapshot_from(records, history, now)


def execution_binding_snapshot(self, now: datetime) -> dict[str, Any]:
    records = self.journal.read()
    history = self.history(records)
    result = self._snapshot_from(records, history, now)
    result["input_records"] = [
        to_primitive(record) for record in history.records(INPUT)
    ]
    return result
```

Assert the public snapshot still has no `input_records` key.

- [x] **Step 6: Extract shared requirements and add single-task binding**

Move the existing input revision/digest, runtime mapping, cycle-evidence validity, four-stage duration, and control-interval ceiling into:

```python
def derive_execution_requirements(
    *,
    plan: Mapping[str, Any],
    input_records: Sequence[Mapping[str, Any]],
    session_identity: Mapping[str, Any],
    evidence_at_utc: str,
) -> dict[str, Any]:
    selected = plan["selection"]
    sources = [
        source for source in input_records
        if source["revision"] == plan["input_revision"]
        and source["input_digest"] == plan["input_digest"]
    ]
    _require(len(sources) == 1, "plan must reference one historical input")
    source = sources[0]
    zones = [
        row for row in source["zones"]
        if (row["robot_id"], row["zone_id"])
        == (selected["robot_id"], selected["zone_id"])
    ]
    mappings = [
        row for row in session_identity["runtime_bindings"]
        if (row["robot_id"], row["zone_id"])
        == (selected["robot_id"], selected["zone_id"])
    ]
    _require(len(zones) == len(mappings) == 1, "unique explicit robot/zone binding required")
    cycle_record = zones[0].get("cycle_minutes")
    _require(
        isinstance(cycle_record, dict) and isinstance(cycle_record.get("value"), dict),
        "missing cycle evidence",
    )
    _require(
        _utc(cycle_record["observed_at_utc"])
        <= _utc(evidence_at_utc)
        <= _utc(cycle_record["valid_until_utc"]),
        "stale or future cycle evidence",
    )
    cycle = {
        name: cycle_record["value"][name]
        for name in ("travel", "collect", "return", "unload")
    }
    max_execution_s = math.ceil(
        sum(cycle.values()) * 60 / session_identity["control_interval_s"]
    ) * session_identity["control_interval_s"]
    evidence = {
        name: cycle_record[name]
        for name in ("source_kind", "source_ref", "observed_at_utc", "valid_until_utc")
    }
    evidence.update(
        input_record_id=source["request_id"],
        input_revision=source["revision"],
        cycle_minutes=cycle,
        derivation_rule="CEIL_TRAVEL_COLLECT_RETURN_UNLOAD_TO_CONTROL_INTERVAL_V1",
    )
    return {
        "runtime_binding": mappings[0],
        "cycle_evidence": evidence,
        "max_execution_s": max_execution_s,
    }
```

The function must select the one input whose revision and digest equal the plan, never infer from list order. Keep `bind_confirmed_tasks` for 3C, but implement it by repeatedly calling the new exact selector:

```python
def bind_confirmed_task(
    planning_snapshot,
    edge_records,
    session_identity,
    now_sim_t_s,
    *,
    task_id: str,
) -> dict:
    matches = [
        confirmation for confirmation in planning_snapshot["confirmations"]
        if confirmation.get("task_id") == task_id
    ]
    _require(len(matches) == 1, "task must have one confirmed planning source")
    return _binding_for_confirmation(
        planning_snapshot, edge_records, session_identity, now_sim_t_s, matches[0]
    )
```

Expose the same method on `CollectionExecutionStore`, appending only when that task has no existing binding and returning the persisted binding on an exact retry.

- [x] **Step 7: Add stable request identity and horizon validation**

Add:

```python
def request_id_for_binding(binding_id: str) -> str:
    _require(isinstance(binding_id, str) and len(binding_id) == 64, "invalid binding ID")
    return "exec_req_" + digest({
        "schema": "nxt-collection-execution-request-id/v1",
        "kind": "EXECUTE_BOUND_COLLECTION",
        "binding_id": binding_id,
    })[:24]
```

Add a pure horizon check beside the shared derivation:

```python
def require_session_horizon(
    *,
    start_at_utc: str,
    max_execution_s: float,
    session_identity: Mapping[str, Any],
) -> None:
    selected_end = _utc(start_at_utc) + timedelta(seconds=max_execution_s)
    session_end = _utc(session_identity["session_epoch_utc"]) + timedelta(
        seconds=session_identity["session_end_sim_t_s"]
    )
    _require(
        selected_end <= session_end,
        "confirmed collection cannot finish inside the active V3 session horizon",
    )
```

Equality is accepted. Task 5 maps this function and missing/ambiguous runtime-binding errors to a definite `planning_conflict` before confirmation commit.

- [x] **Step 8: Run focused tests, preserve 3C behavior, and commit**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/pilot_ops/test_planning.py tests/course_monitoring/test_course_collection_execution.py tests/course_monitoring/test_collection_execution_integration.py tests/site_agent/test_collection_execution_service.py
```

Expected: all pass, including fixed 3C.

```bash
git add simulation/scripts/planning_operations.py simulation/scripts/course_collection_execution.py simulation/tests/pilot_ops/test_planning.py simulation/tests/course_monitoring/test_course_collection_execution.py
git commit -m "feat(collection-execution): derive stable continuous admissions"
```

### Task 5: Build the continuous V4 runtime and unique driver

**Files:**
- Create: `simulation/scripts/course_collection_execution_v4_service.py`
- Create: `simulation/tests/site_agent/test_continuous_collection_execution_service.py`
- Modify: `simulation/tests/site_agent/test_architecture.py`

**Interfaces:**
- Consumes: Task 1 continuous capabilities; Task 3 device reconciliation; Task 4 gate, internal snapshot, exact binder, and request-ID helper; existing `course_session_v3.execution_admission()` and `course_session_v3.run()`.
- Produces: `ContinuousCollectionExecutionRuntime` with `start`, `start_driver`, `tick`, `close`, an optional deterministic `crash_hook`, read callbacks, Planning/task-operation routing, and a separate CLI/marker.

- [x] **Step 1: Add failing marker and fixed-service isolation tests**

Assert the new root only accepts its own marker and cannot open fixed evidence:

```python
def test_v4_marker_is_isolated_from_fixed_3c_root(tmp_path):
    fixed = tmp_path / "fixed"
    fixed_runtime = CollectionExecutionServiceRuntime(fixed, initialize=True)
    fixed_runtime.start()
    fixed_runtime.close()
    with pytest.raises(RuntimeError, match="continuous service identity"):
        ContinuousCollectionExecutionRuntime(fixed, initialize=False).start()

    runtime = ContinuousCollectionExecutionRuntime(tmp_path / "continuous", initialize=True)
    runtime.start()
    try:
        marker = json.loads((runtime.root / "continuous-service.json").read_text())
        assert marker == {
            "schema": "nxt-course-continuous-collection-service/v1",
            "environment": "SIMULATION",
        }
    finally:
        runtime.close()
```

Also assert `--initialize` rejects a nonempty directory, a second process lock fails, and `test_collection_execution_service.py` stays green.

- [x] **Step 2: Add failing classifier and one-driver tests**

Cover empty ACTIVE, all-history-terminal ACTIVE, PAUSED, PROTECTED, clean ENDED, and ENDED-with-nonterminal through a pure `classify_continuous_runtime(runtime_status, executions)` helper. Pin the done set:

```python
def test_normal_execution_terminal_does_not_stop_active_session():
    state = classify_continuous_runtime(
        {"session_state": "ACTIVE"},
        [{
            "state": "SUCCEEDED",
            "device_protection": {
                "protected": False,
                "authorization_blocked": False,
            },
        }],
    )
    assert state == "RUNNING"


def test_only_one_background_driver_can_start(tmp_path):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous", initialize=True, step_interval_s=60
    )
    runtime.start()
    try:
        assert runtime.start_driver() is True
        with pytest.raises(RuntimeError, match="already started"):
            runtime.start_driver()
    finally:
        runtime.close()
```

Use the pure helper only for the state table. Use a real V3 root and deterministic wall clock for the one-driver lifecycle test.

- [x] **Step 3: Add a failing materialization-order test**

Instrument the real owner calls and assert one iteration follows this exact trace:

```python
assert trace == [
    "classify",
    "planning.recover",
    "schedules.tick",
    "bind",
    "device.admit",
    "device.consume_committed.before",
    "publish.events.before",
    "publish.status.before",
    "course_session_v3.run",
    "device.consume_committed.after",
    "publish.events.after",
    "publish.status.after",
    "classify",
]
```

Assert the V3 admission context has exited before `device.admit`, and `course_session_v3.run()` is called exactly once.

- [x] **Step 4: Run the new service tests and verify RED**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent/test_continuous_collection_execution_service.py tests/site_agent/test_architecture.py
```

Expected: import failure because the V4 service does not exist.

- [x] **Step 5: Create the separate marker, process lock, runtime shell, and gate closure**

Define:

```python
MARKER = {
    "schema": "nxt-course-continuous-collection-service/v1",
    "environment": "SIMULATION",
}
_TERMINAL_EXECUTIONS = {
    "SUCCEEDED", "PARTIAL", "REJECTED", "MISSED", "FAILED", "INCONCLUSIVE"
}
_DRIVER_DONE = {"ENDED", "PROTECTED", "FAILED"}
```

Build `ContinuousCollectionExecutionRuntime` directly from the lower-level setup used by the fixed demo. Do not subclass either fixed class. Use `continuous-service.json`, the existing process-lock pattern, the same V3 session config, the same device/gateway/publisher owners, and `task_ops_service_capabilities("CONTINUOUS_V3_EXECUTION")`.

Accept `crash_hook: Callable[[str, Mapping[str, Any]], None] | None = None` in the runtime constructor. Pass it unchanged to `device.admit`, `device.consume_committed`, and `course_session_v3.run`; production CLI passes `None`. Do not catch an injected exception and continue the same process: the runtime must latch FAILED and require an explicit restart.

Construct `PlanningOperations` with this closure against the immutable session identity:

```python
def confirmation_gate(history, confirmation, _now):
    plan = history.plan(confirmation["plan_id"], confirmation["plan_version"])
    try:
        requirements = execution_api.derive_execution_requirements(
            plan=plan,
            input_records=history.records(INPUT),
            session_identity=session_identity,
            evidence_at_utc=confirmation["schedule"]["due_at_utc"],
        )
        execution_api.require_session_horizon(
            start_at_utc=confirmation["schedule"]["due_at_utc"],
            max_execution_s=requirements["max_execution_s"],
            session_identity=session_identity,
        )
    except execution_api.CollectionExecutionError as exc:
        raise PlanningError("planning_conflict", exc.detail) from exc
```

Pass `confirmation_gate` to `PlanningOperations`. Do not read the mutable runtime or wall clock inside this closure.

- [x] **Step 6: Implement startup reconciliation before reads become available**

Inside `start()`, keep `self.started` false until startup reconciliation completes. Private helpers validate component presence rather than calling the public `_require_started()` guard. Use a private `_read_collection_executions_unlocked()` for startup classification; public GET wrappers continue to require `started == True`.

For an ACTIVE session, use this order:

```python
self._prepare_root()
self._initialize_or_repair_v3()
self._start_device_and_reconcile()
self._start_gateway_and_publisher()
self._construct_planning_with_horizon_gate()
self._publish_status()
self._resume_bound_admissions_unlocked()
self.device.consume_committed(crash_hook=self.crash_hook)
self._publish_pending_events()
self._publish_status()
self._classify_unlocked()
self._return_read_only_if_not_running()
self.planning.recover()
self.planning.schedules.tick(self.simulation_clock())
self._materialize_new_tasks_unlocked()
self.device.consume_committed()
self._publish_pending_events()
self._publish_status()
self._classify_unlocked()
self.started = True
```

`_resume_bound_admissions_unlocked()` may only complete a binding/request that was durable before this process started; it cannot bind a new `TASK_CREATED`. For an ordinary same-incarnation process restart it resumes the one stable request/receipt/attempt and may reach ACCEPTED/PENDING. If the current device was explicitly re-provisioned or otherwise has a verified different incarnation, it generates or reuses the one seq-0 rejection required to close the stale authorization. Consuming and publishing an already committed outbox is recovery of prior causal evidence, not a new simulator action, so it completes before the state gate. `_return_read_only_if_not_running()` sets `started = True` and returns for a verified PAUSED, PROTECTED, ENDED, or classifier-derived FAILED state; those states expose GETs but do not call Planning recover, schedule tick, new-task binding, or V3 run. An integrity, storage, gateway, or reconciliation exception still closes components, keeps `started == False`, and exposes no partial snapshot.

- [x] **Step 7: Implement incremental binding and stable admission without nested V3 locks**

Use detached Planning and Edge values. First add `_resume_bound_admissions_unlocked()` to reconstruct the stable request for every existing nonterminal, unaccepted binding; collect `(TaskRequest, execution_request)` pairs under the V3 lock, then call `device.admit` only after the lock exits. This closes binding-before-request and request-before-device crashes even when the session is PAUSED.

Then add new bindings only in the active driver path:

```python
def _materialize_new_tasks_unlocked(self) -> list[dict[str, Any]]:
    planning = self.planning.execution_binding_snapshot(self.simulation_clock())
    edge_records = self.edge_journal.read()
    task_records = sorted(
        (r for r in edge_records if r.record_kind == TASK_CREATED),
        key=lambda r: (r.recorded_at_utc, r.payload["task_id"]),
    )
    admissions = []
    with course_session_v3.execution_admission(self.session_root) as admission:
        replayed = admission.store.replay()
        bindings_by_task = {
            row["task_id"]: row for row in replayed["bindings"].values()
        }
        closed_or_accepted_bindings = {
            row["binding_id"] for row in replayed["executions"].values()
            if row["edge_evidence"]["accepted"]
            or row["state"] in _TERMINAL_EXECUTIONS
        }
        for task_record in task_records:
            task = TaskRequest.from_dict(task_record.payload["request"])
            if task.task_id in bindings_by_task:
                binding = bindings_by_task[task.task_id]
            else:
                binding = admission.store.bind_confirmed_task(
                    planning,
                    edge_records,
                    admission.identity,
                    admission.now_sim_t_s,
                    task_id=task.task_id,
                )
                bindings_by_task[task.task_id] = binding
            if binding["binding_id"] in closed_or_accepted_bindings:
                continue
            request = execution_api.make_request(
                binding,
                execution_api.request_id_for_binding(binding["binding_id"]),
                task.issued_at_utc,
                task.expires_at_utc,
            )
            admissions.append((task, request))
    return [self.device.admit(task, request) for task, request in admissions]
```

The list comprehension runs after the context exits. Exact retries return original receipts. A same-incarnation pre-acceptance process restart resumes that stable admission without a second attempt; an explicit different-incarnation device closes through Task 3's exact rejection path.

- [x] **Step 8: Implement classifier and one driver iteration**

Classifier rules must be explicit. Add the pure function and have `_classify_unlocked()` read the private verified snapshot, call it, and own the stop/failure side effects:

```python
def classify_continuous_runtime(runtime, executions) -> str:
    if runtime["session_state"] == "PAUSED":
        return "PAUSED"
    if any(
        row["device_protection"]["protected"]
        or row["device_protection"]["authorization_blocked"]
        for row in executions
    ):
        return "PROTECTED"
    if runtime["session_state"] == "ENDED":
        if any(row["state"] not in _TERMINAL_EXECUTIONS for row in executions):
            raise RuntimeError("V3 session ended with nonterminal execution")
        return "ENDED"
    return "RUNNING"
```

`tick()` holds the outer `RLock`, returns immediately for PAUSED/done/failure, otherwise performs the exact Step 3 trace. Any unexpected exception latches `FAILED` and stops the thread; no action is automatically retried in the same process.

- [x] **Step 9: Add architecture guards and run the focused runtime tests**

Add the new file to `SERVICE_SCRIPTS`. Assert it imports no `MockRobotDevice`, physical transport stack, browser package, or second simulation advancement path. Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent/test_continuous_collection_execution_service.py tests/site_agent/test_collection_execution_service.py tests/site_agent/test_architecture.py
```

Expected: all pass and fixed 3C behavior is unchanged.

- [x] **Step 10: Commit the runtime foundation**

```bash
git add simulation/scripts/course_collection_execution_v4_service.py simulation/tests/site_agent/test_continuous_collection_execution_service.py simulation/tests/site_agent/test_architecture.py
git commit -m "feat(collection-execution): add continuous v4 runtime"
```

### Task 6: Install writable owner routes and prove the two-task HTTP loop

**Files:**
- Modify: `simulation/scripts/course_collection_execution_v4_service.py`
- Modify: `simulation/tests/site_agent/test_continuous_collection_execution_service.py`
- Create: `simulation/tests/fixtures/continuous-collection-v4/two-task-active.json`
- Modify: `simulation/tests/course_monitoring/test_collection_execution_acceptance.py`

**Interfaces:**
- Consumes: Task 5 runtime and existing `SiteAgentApiServer` callback injection.
- Produces: real HTTP Planning/task-ops/execution routes for the continuous mode; deterministic two-task witness; CLI `python -m scripts.course_collection_execution_v4_service`.

- [x] **Step 1: Add a failing real-HTTP two-task test**

Use the existing local `call`, `serve`, deterministic `WallClock`, and Planning request builders. The test must create the second Planning chain while the first execution is still RUNNING:

```python
TERMINAL_EXECUTIONS = {
    "SUCCEEDED", "PARTIAL", "REJECTED", "MISSED", "FAILED", "INCONCLUSIVE"
}


def execution_for_confirmation(snapshot, confirmation):
    binding = next(
        row for row in snapshot["bindings"]
        if row["confirmation_id"] == confirmation["confirmation_id"]
    )
    return next(
        row for row in snapshot["executions"]
        if row["binding_id"] == binding["binding_id"]
    )


def test_http_accepts_second_confirmation_while_first_runs_and_executes_both(
    tmp_path, launch
):
    runtime = ContinuousCollectionExecutionRuntime(
        tmp_path / "continuous", initialize=True, wall_clock=WallClock()
    )
    runtime.start()
    service, server = serve(runtime, launch, tmp_path)
    try:
        initial_total = ledger_total(runtime)
        first = post_planning_chain(
            server, revision=1, start_at_utc="2026-09-16T08:10:00Z"
        )
        advance_until(
            runtime,
            lambda snap: execution_for_confirmation(snap, first)["state"] == "RUNNING",
            maximum_ticks=20,
        )

        second = post_planning_chain(
            server, revision=2, start_at_utc="2026-09-16T08:40:00Z"
        )
        snapshot = advance_until(
            runtime,
            lambda value: len(value["executions"]) == 2 and all(
                row["state"] in TERMINAL_EXECUTIONS for row in value["executions"]
            ),
            maximum_ticks=60,
        )
        assert len(snapshot["executions"]) == 2
        first_record, second_record = snapshot["executions"]
        assert first_record["execution_id"] != second_record["execution_id"]
        assert first_record["terminal_sim_t_s"] <= second_record["started_sim_t_s"]
        assert sum(row["state"] == "RUNNING" for row in snapshot["executions"]) <= 1
        assert first_record["raw_quantity"]["balls"] > 0
        assert second_record["raw_quantity"]["balls"] > 0
        assert ledger_total(runtime) == initial_total
        assert runtime.driver_state == "RUNNING"
    finally:
        server.close()
        service.close()
        runtime.close()
```

Define `post_planning_chain` in the test file to issue actual POSTs for input, plan, and confirmation, always using returned `plan_id`, version, and request receipts; return the committed confirmation record as a dictionary. Define `advance_until` to call `runtime.tick()` no more than `maximum_ticks` and fail with the final snapshot if the predicate never becomes true. Define ledger totals from the V3 replayed runtime, not execution quantities.

- [x] **Step 2: Add failing idempotency, restart, and GET-purity assertions**

Extend the scenario to assert:

```python
assert {row["confirmation_id"] for row in planning["confirmations"]} == {
    first["confirmation_id"], second["confirmation_id"]
}
assert len({row["schedule_id"] for row in task_ops["schedules"]}) == 2
assert len({row["task_id"] for row in executions}) == 2
assert len({row["binding_id"] for row in executions}) == 2
assert len({row["request_id"] for row in executions}) == 2
for row in executions:
    assert row["request_id"] == request_id_for_binding(row["binding_id"])
```

Repeat each original POST body, call `_materialize_new_tasks_unlocked()` twice, and assert no count changes. Inject one response-lost-after-fsync error for input, plan, and confirmation in separate cases; recover each by the original Planning `request_id` GET before resending the identical body, and prove a changed body with that ID conflicts. Hash all evidence files before and after repeated task-ops, Planning, execution-list, and execution-request GETs; hashes must match. Close and reopen the runtime after the first terminal but before the second due time; the second task must finish with unchanged IDs and no second execution attempt.

- [x] **Step 3: Add failing composition crash-boundary recovery tests**

Cover every accepted-spec boundary with real journals:

```python
@pytest.mark.parametrize(
    "boundary",
    [
        "after_confirmation_append",
        "after_schedule_append",
        "after_task_created_append",
        "after_binding_append",
        "after_request_append",
    ],
)
def test_continuous_runtime_recovers_each_durable_boundary_once(tmp_path, boundary):
    root = tmp_path / boundary
    runtime = runtime_with_one_shot_crash(root, boundary)
    drive_until_injected_crash(runtime, boundary)
    recovered = ContinuousCollectionExecutionRuntime(root, initialize=False)
    recovered.start()
    drive_to_terminal(recovered, maximum_ticks=40)
    proof = causal_counts(recovered)
    assert proof["confirmations"] == 1
    assert proof["schedules"] == 1
    assert proof["tasks"] == 1
    assert proof["bindings"] == 1
    assert proof["requests"] == 1
    assert proof["attempts"] == 1
    assert proof["edge_terminals"] == 1
    assert proof["ledger_moves"] == proof["unique_ledger_moves"]
```

Implement `runtime_with_one_shot_crash` by using the runtime hook for device/V3 boundaries and a journal `append_via` wrapper that raises only after the selected Planning, schedule, task, binding, or request record has fsynced. `drive_until_injected_crash` must assert the named hook fired. The test above uses the same provisioned device identity on reopen. At the `TASK_CREATED`/binding/request pre-acceptance boundaries, assert startup reuses the exact binding-derived request ID, request body, receipt, and attempt; the immediate recovered execution may be ACCEPTED/PENDING, subsequent progress reaches one terminal, and no BallLedger move is duplicated.

Test the different-incarnation rejection boundaries separately:

```python
@pytest.mark.parametrize(
    "boundary",
    ["after_preacceptance_device_rejection", "after_preacceptance_v3_rejection"],
)
def test_explicit_reprovision_closes_preacceptance_request_once(
    tmp_path, boundary
):
    runtime = runtime_with_reprovisioned_device_and_one_shot_crash(
        tmp_path / boundary, boundary
    )
    drive_until_injected_crash(runtime, boundary)
    recovered = reopen_same_reprovisioned_device(runtime.root)
    snapshot = recovered.collection_executions()
    assert snapshot["executions"][0]["state"] == "REJECTED"
    assert snapshot["executions"][0]["reason"] == "IDENTITY_CONFLICT"
    proof = causal_counts(recovered)
    assert proof["requests"] == 1
    assert proof["seq0_incarnation_rejections"] == 1
    assert proof["ledger_moves"] == 0
```

The fixture must explicitly replace/re-provision the device so its current incarnation differs from the `TaskRequest.target_incarnation`; a normal process restart is insufficient. Reopening that replacement increments only its `boot_sequence` and must reuse the already durable seq-0 record.

Keep every post-acceptance boundary in a third group and arrange the injected crash while the execution is ACCEPTED or RUNNING:

```python
@pytest.mark.parametrize(
    "boundary",
    [
        "after_device_acceptance",
        "after_prepare",
        "after_commit",
        "after_device_append",
        "after_v3_evidence",
        "after_v3_confirm",
        "after_cursor",
    ],
)
def test_accepted_or_running_restart_fail_stops_without_duplicate_move(
    tmp_path, boundary
):
    runtime = runtime_with_one_shot_crash(tmp_path / boundary, boundary)
    drive_accepted_or_running_until_injected_crash(runtime, boundary)
    recovered = ContinuousCollectionExecutionRuntime(runtime.root, initialize=False)
    recovered.start()
    row = recovered.collection_executions()["executions"][0]
    assert row["state"] in {"FAILED", "INCONCLUSIVE"}
    assert row["device_protection"]["protected"]
    proof = causal_counts(recovered)
    assert proof["ledger_moves"] == proof["unique_ledger_moves"]
    assert proof["later_task_starts"] == 0
```

These tests retain the established accepted/running restart fail-stop rule. They must not reinterpret a stable incarnation as authority to continue an already accepted authorization after its device process restarts.

- [x] **Step 4: Add failing overlap, horizon, pause, protection, and session-end tests**

Cover these outcomes with real owners:

```text
overlapping due schedule -> ScheduleService REJECTED, no second TASK_CREATED, binding, or execution
confirmation end exactly at session end -> accepted
confirmation end one second past session end -> 409 planning_conflict, zero schedule/task/execution
missing runtime mapping -> 409 planning_conflict, zero confirmation/schedule/task/execution
PAUSED -> Planning input/plan/confirmation persist, simulation time and task count remain fixed
pending cancellation -> CANCELLED remains durable after recover/restart and never creates a task
DISPATCHED cancellation -> 409 conflict, existing task and authorization remain unchanged
SafetyShield rejection, e-stop, robot fault, human assistance, terminal conflict, or replay mismatch -> runtime PROTECTED or FAILED according to the existing execution projection, new tasks do not start, all existing GETs return evidence
ENDED with all terminal -> runtime ENDED and writes rejected
ENDED with a nonterminal -> runtime FAILED and writes rejected
collection-execution POST/PUT/PATCH/DELETE -> 405 with byte-identical evidence
ordinary task terminal -> no automatic Planning outcome, wash, supply, or inventory record
```

For the second-task low-ball case, assert actual ledger quantities and conservation. Accept its causal terminal (`SUCCEEDED`, `PARTIAL`, or `FAILED`) only when the state matches its real assignment terminal; never replace the quantity or require 600 balls.

- [x] **Step 5: Add failing synchronization-barrier tests for both route races**

Use `threading.Barrier(2)` to release two threads simultaneously. For confirmation versus tick, require exactly one durable confirmation and allow materialization either in that tick or the next. For cancellation versus dispatch, assert the only legal final states:

```python
if schedule["status"] == "CANCELLED":
    assert schedule["task_id"] is None
    assert matching_task_created(edge_records, schedule["schedule_id"]) == []
else:
    assert schedule["status"] == "DISPATCHED"
    assert len(matching_task_created(edge_records, schedule["schedule_id"])) == 1
    assert cancel_response.status == 409
assert not (
    schedule["status"] == "CANCELLED"
    and matching_task_created(edge_records, schedule["schedule_id"])
)
```

Run each race repeatedly against fresh roots. All route calls and `tick()` must acquire the same runtime `RLock`; tests may not bypass the public route methods.

- [x] **Step 6: Run the integrated service tests and verify RED**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent/test_continuous_collection_execution_service.py tests/course_monitoring/test_collection_execution_acceptance.py
```

Expected: continuous write routes, CLI callbacks, and two-task evidence are incomplete.

- [x] **Step 7: Install the exact route matrix with dynamic fail-closed gates**

`route_planning` must permit GET in every started state. POST input/plan/confirmation/outcome delegates to `PlanningOperations` while state is ACTIVE or PAUSED; PROTECTED, FAILED, and ENDED return explicit `planning_unavailable` or `planning_conflict` without mutation.

`route_task_operations` must implement:

```python
if method == "GET" and path == "/api/v0/task-ops":
    return self.task_operations_snapshot()
if method == "POST" and path == "/api/v0/task-ops/schedules":
    raise SiteAgentError(
        "task_ops_conflict",
        "direct schedule creation is not installed; confirm a Planning plan",
    )
if is_pending_schedule_cancel(path):
    return self.planning.schedules.cancel(schedule_id, body["operator"], self.simulation_clock())
if is_notification_action(path):
    return notification_handler(notification_id, body["operator"], body["note"], self.simulation_clock())
raise SiteAgentError("not_found", "unknown task operations route")
```

Before any POST delegate, block PROTECTED, FAILED, or ENDED. Preserve `ScheduleService.cancel()` pending-only behavior; DISPATCHED cancellation returns a conflict and cannot revoke Edge or simulator authorization.

- [x] **Step 8: Expose static v2 capability and dynamic runtime health**

`task_operations_snapshot()` must emit:

```python
result.update({
    "service_capabilities": task_ops_service_capabilities("CONTINUOUS_V3_EXECUTION"),
    "runtime": {
        **self.runtime_status(),
        "driver_state": self.driver_state,
        "fixed_confirmation": False,
        "accepts_new_confirmations": self.driver_state in {"RUNNING", "PAUSED"},
        "accepts_new_schedules": False,
    },
})
```

The capability matrix never changes with health. Set `scheduler.state` to `FAILED` with a concrete detail for PROTECTED, FAILED, or ENDED so existing browser health gates close. PAUSED stays a healthy read and does not claim that the business clock is moving.

- [x] **Step 9: Add API callbacks and CLI without an execution mutation route**

Return this callback set:

```python
def api_callbacks(self) -> dict[str, Callable]:
    return {
        "task_operations": self.route_task_operations,
        "planning_operations": self.route_planning,
        "collection_executions": self.collection_executions,
        "collection_execution_request": self.collection_execution_request,
        "collection_execution_parser": execution_api.parse_collection_execution_read_contract,
    }
```

Add CLI flags `--out`, `--initialize`, `--port`, `--driver-interval`, `--console`, and `--api-only`, matching the fixed service ergonomics while instantiating only the continuous runtime. Do not add any collection-execution POST callback.

- [x] **Step 10: Generate and freeze the real two-task intermediate witness**

Run the deterministic test runtime to the point where execution 1 is terminal, execution 2 is PENDING or RUNNING, and session state is ACTIVE. With fixed wall clock, write the exact `nxt-collection-executions/v1` response data to:

```text
simulation/tests/fixtures/continuous-collection-v4/two-task-active.json
```

Add a backend test that regenerates the snapshot and compares canonical JSON bytes to the file. Validate the same file with the Python schema/relations oracle. This fixture is the only multi-task witness consumed by Task 7.

- [x] **Step 11: Run backend integration and commit**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent/test_continuous_collection_execution_service.py tests/site_agent/test_collection_execution_service.py tests/course_monitoring/test_collection_execution_acceptance.py tests/course_monitoring/test_collection_execution_integration.py tests/site_agent/test_architecture.py
```

Expected: all pass, including fixed 3C, linearization, restart, protection, and GET-purity cases.

```bash
git add simulation/scripts/course_collection_execution_v4_service.py simulation/tests/site_agent/test_continuous_collection_execution_service.py simulation/tests/fixtures/continuous-collection-v4/two-task-active.json simulation/tests/course_monitoring/test_collection_execution_acceptance.py
git commit -m "feat(collection-execution): serve continuous planning tasks"
```

### Task 7: Present continuous capabilities and multi-execution history

**Files:**
- Modify: `apps/site-agent-console/components/capabilities.tsx`
- Modify: `apps/site-agent-console/components/execution/CollectionExecutionPanel.tsx`
- Modify: `apps/site-agent-console/components/DispatchPanel.tsx`
- Modify: `apps/site-agent-console/tests/execution-fixtures.ts`
- Modify: `apps/site-agent-console/tests/capabilities-panel.test.tsx`
- Modify: `apps/site-agent-console/tests/capabilities-interaction.test.tsx`
- Modify: `apps/site-agent-console/tests/execution-panel.test.tsx`
- Modify: `apps/site-agent-console/tests/execution-interaction.test.tsx`
- Modify: `apps/site-agent-console/README.md`
- Modify only if the existing layout fails at 320 px: `apps/site-agent-console/app/globals.css`

**Interfaces:**
- Consumes: Task 1 strict `TaskOpsServiceCapabilities` union and Task 6 real witness fixture.
- Produces: continuous-mode badge/copy, correct operation gates, and multi-card read-only UI tests. It does not add execution actions, sorting, or new wire parsing.

- [x] **Step 1: Add failing static capability-panel tests**

Load `continuous-v3-execution.json` through the real Task 1 parser. Assert:

```typescript
expect(screen.getByText("CONTINUOUS V3 SESSION")).toBeInTheDocument();
expect(screen.getByRole("button", { name: /Save revision/ })).toBeEnabled();
expect(screen.getByRole("button", { name: "Ask for the system suggestion" })).toBeEnabled();
expect(screen.getByRole("button", { name: /Confirm plan version/ })).toBeEnabled();
expect(screen.getByRole("button", { name: "Schedule simulated collection" })).toBeDisabled();
expect(screen.getByText(/confirmed Planning plans create bound schedules/i)).toBeInTheDocument();
expect(screen.getByRole("button", { name: "Cancel schedule" })).toBeEnabled();
```

Keep scheduler-health, form-validity, busy state, and record-level conditions in their fixtures so each assertion isolates the capability gate.

- [x] **Step 2: Add failing mounted interaction tests for all eight operations**

Use a v2 continuous declaration and assert that input, plan, confirmation, outcome, pending cancellation, notification acknowledge, and notification resolve call only their existing endpoints. For notification resolution, prove `SUPPORTED` still leaves the control disabled when `condition_active=true` or `can_resolve=false`. Force a direct schedule submit and assert zero POST. Then change scheduler health to FAILED and assert every write is blocked despite `SUPPORTED`. Preserve Planning UNKNOWN request ID, body, and draft across the capability/health transition.

- [x] **Step 3: Add failing multi-execution witness tests**

Load only `simulation/tests/fixtures/continuous-collection-v4/two-task-active.json` through `parseCollectionExecutions`. Assert service order and session state:

```typescript
const witness = continuousTwoTaskWitness();
render(<CollectionExecutionView view={readView(witness)} onRetry={onRetry} />);
const cards = screen.getAllByTestId("execution-record-card");
expect(cards.map((card) => card.getAttribute("data-execution-id"))).toEqual(
  witness.executions.map((row) => row.execution_id),
);
expect(within(cards[0]).getByText(/terminal/i)).toBeInTheDocument();
expect(within(cards[1]).getByText(/pending|running/i)).toBeInTheDocument();
expect(screen.getByText(/session active/i)).toBeInTheDocument();
expect(screen.queryByRole("button", { name: /start execution|stop execution|rerun execution/i })).not.toBeInTheDocument();
expect(screen.getByRole("button", { name: "Retry execution read" })).toBeInTheDocument();
```

If `ExecutionRecordCard` has no test ID, add only `data-testid="execution-record-card"` and `data-execution-id={record.execution_id}`; do not create UI state from these attributes.

- [x] **Step 4: Add a failing polling transition test**

Script two valid responses: first execution terminal plus second PENDING, then first unchanged plus second RUNNING or terminal. Assert the first card remains byte-equivalent in displayed evidence, the session does not become ENDED after the first terminal, and the fetch mock records only GET calls to `/api/v1/collection-executions`.

- [x] **Step 5: Run the focused UI tests and verify RED**

Run from `apps/site-agent-console/`:

```bash
npm test -- tests/capabilities-panel.test.tsx tests/capabilities-interaction.test.tsx tests/execution-panel.test.tsx tests/execution-interaction.test.tsx
```

Expected: continuous mode has no presentation case and the old singular copy fails.

- [x] **Step 6: Add continuous mode copy without changing the gate plumbing**

Add exact switch cases in `capabilities.tsx`:

```typescript
case "CONTINUOUS_V3_EXECUTION":
  return { label: "CONTINUOUS V3 SESSION", tone: "info" };
```

Use this mode text:

```text
Service mode CONTINUOUS_V3_EXECUTION: confirmed Planning plans create sequential simulated collection tasks in the active V3 session. Direct schedule creation is not installed; pending cancellation, outcome recording and notification handling remain subject to service health and each record's own conditions.
```

For only `schedules_create` in continuous mode, `capabilityBlocker` must say direct scheduling is unavailable because a confirmed Planning plan creates the bound schedule. Do not infer support from `runtime`, transport, or HTTP success.

- [x] **Step 7: Make execution and schedule copy source-neutral and plural**

Change the execution introduction to:

```text
Follow confirmed collection tasks through this simulated session: admission, durable request, device acceptance, start, collection, unloading and terminal evidence.
```

Change the Dispatch introduction to describe reviewing schedules and task progress. Keep `data.executions.map(...)` exactly in service order, retain the sole `Retry execution read` read action, and add no sort, priority, schedule-create workaround, or execution mutation.

- [x] **Step 8: Run focused and full console verification**

Run from `apps/site-agent-console/`:

```bash
npm test -- tests/capabilities-panel.test.tsx tests/capabilities-interaction.test.tsx tests/execution-panel.test.tsx tests/execution-interaction.test.tsx tests/task-ops.test.ts tests/collection-executions-contract.test.ts
npm test
npm run typecheck
npm run lint
npm run build
npm run smoke
```

Expected: all pass; lint has no new warnings. Render the witness at desktop and 320 px and verify no horizontal overflow. Change CSS only if this render fails.

- [x] **Step 9: Commit the presentation layer**

```bash
git add apps/site-agent-console/components/capabilities.tsx apps/site-agent-console/components/execution/CollectionExecutionPanel.tsx apps/site-agent-console/components/DispatchPanel.tsx apps/site-agent-console/tests/execution-fixtures.ts apps/site-agent-console/tests/capabilities-panel.test.tsx apps/site-agent-console/tests/capabilities-interaction.test.tsx apps/site-agent-console/tests/execution-panel.test.tsx apps/site-agent-console/tests/execution-interaction.test.tsx apps/site-agent-console/README.md apps/site-agent-console/app/globals.css
git commit -m "feat(console): present continuous collection sessions"
```

### Task 8: Document, reproduce, and independently review V4

**Files:**
- Create: `simulation/docs/continuous_collection_execution_v4_runbook.md`
- Modify: `docs/superpowers/specs/2026-09-25-continuous-collection-v4-design.md`
- Update: `docs/superpowers/plans/2026-09-25-continuous-collection-v4-implementation.md`

**Interfaces:**
- Consumes: the completed runtime, CLI, capability/parser, and UI commits.
- Produces: exact local reproduction instructions, verified evidence IDs/digests/counts, explicit remaining limits, and a reviewed local implementation branch.

- [x] **Step 1: Write the runbook with exact fresh and resume commands**

Document these commands from `simulation/`. `--initialize` requires a newly
empty evidence root; create logs, request bodies, and HTTP responses outside it:

```bash
PROOF_ROOT="$(mktemp -d /private/tmp/nxt-continuous-v4-proof.XXXXXX)"
PROOF_LOG="$(mktemp /private/tmp/nxt-continuous-v4-service.XXXXXX.log)"
UV_CACHE_DIR=/private/tmp/nxtektal-uv-cache \
uv run --no-sync python -B -m scripts.course_collection_execution_v4_service \
  --out "$PROOF_ROOT" --initialize --port 8774 --driver-interval 6 \
  >"$PROOF_LOG" 2>&1
```

After a clean stop, document the same command without `--initialize`. Include the API URL, expected marker schema, capability mode, how to submit two Planning chains, how to identify both request IDs, and how to distinguish raw collection, unloading, Planning outcome, wash, and supply evidence.

- [x] **Step 2: Run a fresh real HTTP reproduction and capture concrete evidence**

From an empty proof root, submit the first chain, wait for RUNNING, submit the second chain, and observe both terminals without restarting. Record in the runbook:

```text
series_id, session_id, round_id
two confirmation IDs
two schedule IDs and final schedule states
two task IDs
two binding IDs
two deterministic request IDs
two execution IDs and assignment IDs
raw and unload quantities with source event IDs
event digests and replay digest
BallLedger conservation result
driver/session state after the first and second terminal
```

Report the second task's actual causal terminal and quantity; do not normalize it to the first task's result.

- [x] **Step 3: Reproduce restart and read purity**

Stop after the first terminal and before the second due time, resume the same root, and finish the second task. Verify IDs and quantities match the uninterrupted deterministic expectation. Hash the evidence tree, issue every GET route repeatedly, hash again, and record equality in the runbook.

For the GET-purity phase, stop the normal six-second driver cleanly and restart
the same root without `--initialize` using `--driver-interval 3600`. Compute the
durable hash before the first background tick, perform two rounds of all 11 GET
routes, and compute it after each round. This isolates reads from normal ACTIVE
session writes.

- [ ] **Step 4: Run the normative backend suites**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider
uv run --no-sync python -B scripts/validate_configs.py
```

Expected: all tests pass and config validation reports zero errors and zero warnings.

- [ ] **Step 5: Run the normative console suites**

Run from `apps/site-agent-console/`:

```bash
npm test
npm run typecheck
npm run lint
npm run build
npm run smoke
npm audit --omit=dev
```

Expected: tests, typecheck, build, smoke, and audit pass; lint has no new warnings.

- [ ] **Step 6: Run repository, package, and hygiene verification**

Follow `.agent/workflows/testing.md`, `.agent/workflows/review.md`, and `.agent/workflows/hygiene.md`. At minimum run:

```bash
uv run --no-project --python 3.13.14 python -B \
  .github/scripts/verify_repository.py
git diff --check
git status --short
```

From `simulation/`, build the Python sdist and wheel outside the repository:

```bash
build_dir="$(mktemp -d)"
uv build --out-dir "$build_dir"
```

Inspect the two artifacts in that exact directory, then remove only that temporary directory. Confirm the working tree contains only the intended V4 files before the documentation commit.

- [ ] **Step 7: Request two independent reviews and resolve every finding**

Use one reviewer for architecture/safety and one for React/contract behavior. Give both reviewers the exact base `de18e58ccce0907f7c0365c33fa6400b5069bc12`, current head, approved spec, and this plan. The architecture reviewer must check owner boundaries, lock ordering, restart evidence, one-step/one-policy behavior, and GET purity. The UI reviewer must check strict v2 parsing, operation gates, multi-card order, stale-read behavior, and absence of execution writes. Apply validated findings with focused failing tests and repeat the affected verification commands.

Current record: the Task 7 contract/behavior and UI reviews examined the
`907a5a1de521968bbd899e535eba1e70f32eb84d` presentation head. Their validated
findings were closed by `a897467b2c3a1689ce6d4f14599e8f581d87473b`,
`49d78729c38d3be5e57afe54c4f683ae4ec2bcc8`, and
`27fcea2d4639dc695f57bd342998803967ecbd09`. The restart/GET-purity evidence also
received an independent read-only review with no findings. The two final Task 8
architecture/safety and React/contract reviews of the complete documentation
head are still pending, so this step remains unchecked.

- [ ] **Step 8: Update design status, plan checkboxes, and commit documentation**

Change the design status from design-only to an implementation record that names the final commits and verified scope. Keep physical robots, cameras, cross-session rollover, multiple robots/stations, automatic Planning outcomes, washing, and supply explicitly unimplemented.

```bash
git add simulation/docs/continuous_collection_execution_v4_runbook.md docs/superpowers/specs/2026-09-25-continuous-collection-v4-design.md docs/superpowers/plans/2026-09-25-continuous-collection-v4-implementation.md
git commit -m "docs(collection-execution): document continuous v4 operation"
```

- [ ] **Step 9: Perform the final clean-head verification**

Re-run the focused V4 backend test, the focused capability/execution console tests, repository verifier, and `git diff --check` after the documentation commit. Confirm `git status --short` is empty. Keep the branch local and unmerged for user review.
