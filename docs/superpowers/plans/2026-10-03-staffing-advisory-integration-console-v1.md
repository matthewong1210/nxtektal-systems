# Staffing Advisory Integration and Console V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compose the staffing owner and regional model gateway behind an optional Site Agent callback, provide bounded asynchronous generation with honest recovery, and add a compact Manager Console workflow for one-time CSV roster import, minimal exception entry, AI suggestions, and manager response.

**Architecture:** `simulation/scripts/staffing_operations.py` is the only layer that imports both `nxt_pilot_ops.staffing` and `nxt_model_gateway`. It resolves stable local evidence, loads deployment-only provider configuration, adapts the fixed prompt, persists attempt boundaries through the gateway observer, and runs one bounded worker without holding service/domain locks during HTTPS. `nxt_site_agent` remains a transport shell with an optional plain-data callback. The static Console uses only the same-origin `/api/v1/staffing` contract and owns no staffing rules, secrets, provider choice, or persistence.

**Tech Stack:** Python 3.11+ stdlib threads/condition/secrets/environment composition, existing Site Agent HTTP server, pytest, JSON Schema Draft 2020-12, TypeScript 5.9, React 19, Next.js static export, Vitest/Happy DOM, dependency-free RFC 4180 CSV parsing.

**Spec:** `docs/superpowers/specs/2026-10-03-regional-ai-staffing-advisory-gateway-v1-design.md`

## Global Constraints

- Execute after the gateway and staffing-domain plans are green. Start from the integration plan-delivery commit named in the handoff and verify design commit `86adac433ee746abb09f5e80b45bc663d6775448` plus baseline `907a5a1de521968bbd899e535eba1e70f32eb84d` remain ancestors.
- This plan closes two explicit design necessities that the suggested route table omitted: `GET /api/v1/staffing` supplies the composition-clock-derived current service day, and `POST /api/v1/staffing/exceptions/{id}/correct` realizes the approved atomic correction event. Document both as contract closures, not scope expansion.
- Preserve loopback-only, same-origin, unauthenticated fixture posture. Do not add LAN/public binding, auth claims, identity verification, mobile access, cloud state, HR/payroll/attendance/access-control writes, notifications, Planning confirmations, Edge tasks, simulator directives, or robot actions.
- The stable evidence path is exactly `<state-root>/<site_id>/<deployment_id>/staffing-v1/`, where `<state-root>` is the value supplied by `--staffing-state-root`. It must never derive from `--out`, live below `--out/site-agent`, or be deleted/touched by `--initialize`, fixture reset, Site Agent restart, or a new runtime run.
- `--staffing-state-root` is optional for backward compatibility. When absent, staffing routes return a stable unavailable response and all existing service behavior stays unchanged. When present, local roster/exception/read/manager operations work even if region, model, or API key configuration is missing.
- Region is explicit `CN` or `GLOBAL`; model IDs are explicit CLI deployment settings; keys come only from `MOONSHOT_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` inside the composition root. No endpoint override exists. Secrets never enter repr/stdout/API/ledger/browser/error details.
- Use `--staffing-language {zh-CN,en}` as deployment configuration, default `zh-CN`; the Manager UI cannot alter prompt, template, provider, model, endpoint, deadline, token cap, or fallback policy.
- Add no Console production dependency. The CSV parser is local, uses fatal UTF-8 decoding with BOM preservation, is RFC 4180-compatible, accepts one leading UTF-8 BOM only, caps raw CSV at 512 KiB and normalized JSON at 1 MiB, and treats browser validation as convenience only.
- Site Agent keeps the existing 64 KiB body limit for every route except exact `POST /api/v1/staffing/roster-imports`, which is capped at 1 MiB. Oversize/unsupported framing closes keep-alive exactly as today.
- Site Agent recognizes only the frozen staffing namespace/path/method shapes, strips query as today, frames allowlisted errors, and never imports/mentions provider or staffing implementations. Unexpected callback exceptions become a generic `staffing_unavailable` without `str(exc)`.
- `POST /api/v1/staffing/suggestions` returns HTTP 202 after durable reservation (including same-ID recovery of an accepted reservation); other successful staffing reads/writes return 200. `BUSY` is a definite pre-reservation 429 and writes no event.
- Bounded generation is exactly one active plus four waiting per site. Duplicate lookup occurs before capacity admission. Worker HTTPS never holds admission, staffing-ledger, Site Agent, continuous-runtime, or global locks.
- On startup, mark every nonterminal generation `RESULT_UNKNOWN` before accepting new work; never enqueue/replay it. On shutdown, do not begin another queued network call; mark waiting reservations interrupted. A retry requires a new request ID and `retry_of`.
- The gateway attempt observer synchronously fsyncs `provider_attempt_started` before transport and `provider_attempt_finished` after transport. Observer failure stops routing/fallback and leaves the generation recoverably unknown.
- The Console gets current `service_date` only from `GET /api/v1/staffing`; it never trusts the browser clock. It uses no localStorage/sessionStorage/indexedDB and rebuilds from API state after refresh.
- Keep one volatile panel-level `Manager label（仅作归属记录）` input because there is no authentication. It is shared by all mutations, is outside the minimal exception fieldset, and is not persisted by the browser. Do not fabricate an operator value.
- Use failing tests first, `apply_patch`, small conventional commits, and local branches only. Do not push, merge, or create a PR.

## Review Focus

- Prove shared wire contract compatibility in Python and TypeScript, exact success/error envelopes, current-date authority, correction route, 202 reservation, request recovery, and every operation state.
- Prove Site Agent body limits, method/path allowlist, Host/Origin protection, query stripping, generic callback redaction, optional callback, and no effect on existing routes.
- Prove capacity and idempotency atomically: duplicate succeeds when full, sixth new ID is 429 with unchanged ledger, provider blocking does not block health/read/exception writes, and no lock spans HTTPS.
- Prove attempt fsync order, all restart crash states, terminal-response-loss recovery, old-ID no-resend, new-ID `retry_of`, provider configuration degradation, close behavior, and stable-root survival/isolation.
- Prove the CSV grammar/normalization, formula-prefix defense, all-or-nothing UI feedback, no production dependency, and server revalidation.
- Prove Console distinctions among transport-unknown, saved-but-stale, BUSY, RESERVED/IN_PROGRESS, RESULT_UNKNOWN, terminal failure, no valid suggestion, stale response, and committed response.
- Prove the minimal exception fieldset remains employee/type/conditional time/optional note/submit, all AI cards carry “AI 建议，需经理确认”, coverage gaps are domain facts separate from warnings, and no chat/provider settings/execution control appears.
- Prove only the composition script imports both new owners, while Site Agent retains its LLM/provider pattern ban and Console retains same-origin/no-persistence/no-command guards.

---

### Task 1: Freeze the shared staffing HTTP contract and examples

**Files:**
- Create: `simulation/docs/contracts/staffing-v1/README.md`
- Create: `simulation/docs/contracts/staffing-v1/schema.json`
- Create: `simulation/docs/contracts/staffing-v1/examples/cold-start.json`
- Create: `simulation/docs/contracts/staffing-v1/examples/roster-import.json`
- Create: `simulation/docs/contracts/staffing-v1/examples/exception-correction.json`
- Create: `simulation/docs/contracts/staffing-v1/examples/generation-success.json`
- Create: `simulation/docs/contracts/staffing-v1/examples/generation-unavailable.json`
- Create: `simulation/docs/contracts/staffing-v1/examples/result-unknown.json`
- Create: `simulation/docs/contracts/staffing-v1/examples/manager-response.json`
- Create: `simulation/docs/contracts/staffing-v1/examples/errors.json`
- Create: `simulation/tests/pilot_ops/test_staffing_wire_contract.py`

**Interfaces:**
- Consumes: the staffing-domain request/projection types and existing Manager API v0 envelope.
- Produces: one versioned cross-language contract for all routes, requests, receipts, snapshots, capability/readiness, candidates, gaps, and operation states.

- [ ] **Step 1: Add a failing test for the exact contract inventory**

Create `test_schema_is_valid_and_contract_inventory_is_exact`. It must fail until the schema and all eight example files exist:

```python
CONTRACT = Path(__file__).resolve().parents[2] / "docs/contracts/staffing-v1"
SCHEMA = json.loads((CONTRACT / "schema.json").read_text(encoding="utf-8"))
EXPECTED_EXAMPLES = {
    "cold-start.json", "roster-import.json", "exception-correction.json",
    "generation-success.json", "generation-unavailable.json",
    "result-unknown.json", "manager-response.json", "errors.json",
}

def test_schema_is_valid_and_contract_inventory_is_exact():
    Draft202012Validator.check_schema(SCHEMA)
    assert {path.name for path in (CONTRACT / "examples").glob("*.json")} == EXPECTED_EXAMPLES
```

- [ ] **Step 2: Add a failing route-table test**

Every example file is a closed container with a top-level `schema` and `exchanges` array. Every exchange has exactly `name`, `method`, `route_template`, optional `request`, `http_status`, and `body`; success/error envelopes live directly in `body`. Flatten all eight files before asserting the unique method/template pairs:

```python
def all_exchanges():
    for path in sorted((CONTRACT / "examples").glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert set(document) == {"schema", "exchanges"}
        for exchange in document["exchanges"]:
            assert set(exchange) in (
                {"name", "method", "route_template", "http_status", "body"},
                {"name", "method", "route_template", "request", "http_status", "body"},
            )
            yield path.name, exchange

EXPECTED_ROUTES = {
    ("GET", "/api/v1/staffing"),
    ("GET", "/api/v1/staffing/dates/{service_date}"),
    ("GET", "/api/v1/staffing/requests/{operation_kind}/{request_id}"),
    ("POST", "/api/v1/staffing/roster-imports"),
    ("POST", "/api/v1/staffing/exceptions"),
    ("POST", "/api/v1/staffing/exceptions/{exception_id}/cancel"),
    ("POST", "/api/v1/staffing/exceptions/{exception_id}/correct"),
    ("POST", "/api/v1/staffing/suggestions"),
    ("POST", "/api/v1/staffing/suggestions/{suggestion_id}/accept"),
    ("POST", "/api/v1/staffing/suggestions/{suggestion_id}/modify"),
    ("POST", "/api/v1/staffing/suggestions/{suggestion_id}/reject"),
}

def test_example_route_inventory_is_exact():
    actual = {(row["method"], row["route_template"]) for _, row in all_exchanges()}
    assert actual == EXPECTED_ROUTES
```

- [ ] **Step 3: Define every shared read type before writing the schema**

Use these closed shapes in `README.md`, `schema.json`, and later in `lib/staffing.ts`; no named type below may remain implicit:

```typescript
type ProviderName = "KIMI" | "OPENAI" | "ANTHROPIC";
type ManagerResponseKind = "ACCEPT" | "MODIFY" | "REJECT";
type ManagerReasonCode =
  | "APPROVED" | "APPROVED_WITH_CHANGES" | "MANUAL_HANDLING"
  | "INSUFFICIENT_CONTEXT" | "OTHER";
type OperationKind =
  | "roster-import"
  | "exception-record"
  | "exception-cancel"
  | "exception-correct"
  | "suggestion-generate"
  | "manager-response";
type OperationState =
  | "COMMITTED" | "RESERVED" | "IN_PROGRESS" | "RESULT_UNKNOWN"
  | "SUCCEEDED" | "NO_VALID_SUGGESTION" | "UNAVAILABLE" | "REFUSED"
  | "INVALID_RESPONSE" | "PROVIDER_ERROR" | "CONFIGURATION_ERROR"
  | "SECURITY_ERROR";
type TerminalGenerationState = Exclude<
  OperationState,
  "COMMITTED" | "RESERVED" | "IN_PROGRESS"
>;
type UnavailableGenerationState = Exclude<
  TerminalGenerationState,
  "SUCCEEDED" | "RESULT_UNKNOWN"
>;

interface RevisionVector {
  roster: number;
  exception_set: number;
  effective_plan: number;
}
interface GenerationCapability {
  status: "READY" | "DEGRADED_BACKUP_UNCONFIGURED" | "UNAVAILABLE";
  region: "CN" | "GLOBAL" | null;
  primary_provider: "KIMI" | "OPENAI" | null;
  backup_provider: "ANTHROPIC" | null;
  failure_code: string | null;
}
interface AssignmentProjection {
  assignment_id: string;
  staff_id: string;
  display_name: string;
  role_code: string;
  area_code: string;
  start_at: string;
  end_at: string;
}
interface ExceptionProjection {
  exception_id: string;
  staff_id: string;
  display_name: string;
  kind: "LEAVE" | "LATE" | "EARLY_DEPARTURE" | "UNAVAILABLE";
  unavailable_start_at: string;
  unavailable_end_at: string;
  note: string | null;
  active: boolean;
}
interface CoverageGap {
  role_code: string;
  area_code: string;
  start_at: string;
  end_at: string;
  required_count: number;
  assigned_count: number;
  rejection_code: string;
}
type CandidateOperationProjection =
  | { operation: "REMOVE"; assignment_id: string; staff_id: string; display_name: string;
      role_code: string; area_code: string; start_at: string; end_at: string }
  | { operation: "ADD"; staff_id: string; display_name: string; role_code: string;
      area_code: string; start_at: string; end_at: string };
type ManagerPatchOperation =
  | { operation: "REMOVE"; assignment_id: string }
  | { operation: "ADD"; staff_id: string; role_code: string; area_code: string;
      start_at: string; end_at: string };
interface ProviderProvenance {
  attempt_index: 0 | 1;
  provider: ProviderName;
  region: "CN" | "GLOBAL";
  route_role: "PRIMARY" | "BACKUP";
  route_id: string;
  model_id: string;
  failure_code: string | null;
  retryable: boolean;
  security_failure: boolean;
  provider_request_id: string | null;
  finish_reason: string | null;
  input_digest: string;
  output_digest: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
}
interface CandidateProjection {
  candidate_index: 1 | 2;
  status: "VALID" | "REJECTED";
  operations: CandidateOperationProjection[];
  rationale: string;
  operational_warnings: string[];
  coverage_gaps: CoverageGap[];
  schedule_digest: string | null;
  rejection_codes: string[];
}
interface RosterProjection {
  revision: number;
  effective_from_local_date: string;
  effective_until_local_date: string | null;
  worker_count: number;
  assignment_count: number;
  coverage_rule_count: number;
}
interface EffectivePlanProjection {
  revision: number;
  status: "CURRENT" | "REVIEW_REQUIRED";
  schedule_digest: string;
  assignments: AssignmentProjection[];
}
interface ManagerResponseSummary {
  response_kind: ManagerResponseKind;
  reason_code: ManagerReasonCode;
  operator: string;
  note: string | null;
  effective_plan: EffectivePlanProjection | null;
}
interface GenerationProjection {
  suggestion_id: string;
  request_id: string;
  operation_id: string;
  state: Exclude<OperationState, "COMMITTED">;
  basis: RevisionVector & { digest: string };
  retry_of: string | null;
  candidates: CandidateProjection[];
  coverage_gaps: CoverageGap[];
  provenance: ProviderProvenance[];
  failure_code: string | null;
  manager_response: ManagerResponseSummary | null;
}
interface EligibilityInput { role_code: string; area_code: string }
interface WorkerInput {
  staff_id: string;
  display_name: string;
  skill_codes: string[];
  eligibility: EligibilityInput[];
  max_daily_minutes: number;
}
interface AvailabilityInput {
  staff_id: string;
  weekday: 0 | 1 | 2 | 3 | 4 | 5 | 6;
  start_local: string;
  end_local: string;
}
interface AssignmentRuleInput {
  role_code: string;
  area_code: string;
  required_skill_codes: string[];
}
interface RegularAssignmentInput {
  staff_id: string;
  weekday: 0 | 1 | 2 | 3 | 4 | 5 | 6;
  role_code: string;
  area_code: string;
  start_local: string;
  end_local: string;
}
interface CoverageInput {
  weekday: 0 | 1 | 2 | 3 | 4 | 5 | 6;
  role_code: string;
  area_code: string;
  start_local: string;
  end_local: string;
  minimum_staff: number;
}
interface StaffingDateSnapshot {
  schema: "nxt-staffing/v1";
  environment: "SIMULATION";
  mode: "STAFFING_ADVISORY_ONLY";
  server_time_utc: string;
  service_date: string;
  context: { site_id: string; deployment_id: string; site_timezone: string };
  generation_capability: GenerationCapability;
  revisions: RevisionVector;
  roster: RosterProjection | null;
  assignments: AssignmentProjection[];
  active_exceptions: ExceptionProjection[];
  effective_plan: EffectivePlanProjection | null;
  generations: GenerationProjection[];
}
```

Provider worker/assignment aliases and alias maps never cross this wire contract. The domain projection restores local IDs and display names for `CandidateOperationProjection`; a manager modification submits the smaller `ManagerPatchOperation`, and the server resolves and revalidates it against the current basis.
`GenerationProjection.manager_response` is null until a manager response commits and then remains present after refresh. There can be at most one response for a suggestion. ACCEPT/MODIFY require a non-null effective plan; REJECT requires null. The schema closes `ManagerResponseSummary` and its nested plan independently.
The generation snapshot schema also branches on `state`: RESERVED, IN_PROGRESS, and SUCCEEDED require `failure_code: null`; RESULT_UNKNOWN requires the literal `RESULT_UNKNOWN`; every unavailable terminal requires a bounded nonempty failure code.

The public `suggestion_id` is the domain `generation_id` serialized unchanged; it is named for the approved HTTP route only. `operation_id` is the committing domain receipt's `event_id`. A generation retry therefore places the old `suggestion_id`/`generation_id` in `retry_of`, never the old receipt event ID.

- [ ] **Step 4: Define every request in one closed request union**

Freeze these request interfaces with `additionalProperties: false`; timestamp fields retain the domain plan's exact RFC3339 profiles: zero UTC offset is uppercase `Z`, every nonzero offset remains signed `±HH:MM`, and signed zero `+00:00` or `-00:00` is rejected. Date fields use `YYYY-MM-DD`, and every string/array gets the limits from the domain plan; no other timestamp profile changes.

```typescript
interface RequestBase { request_id: string; operator: string }
interface RosterImportRequest extends RequestBase {
  schema: "nxt-staffing-roster-import/v1";
  expected_roster_revision: number;
  site_id: string;
  deployment_id: string;
  site_timezone: string;
  effective_from_local_date: string;
  effective_until_local_date: string | null;
  source_ref: string;
  workers: WorkerInput[];
  availability: AvailabilityInput[];
  assignment_rules: AssignmentRuleInput[];
  regular_assignments: RegularAssignmentInput[];
  coverage: CoverageInput[];
}
interface ExceptionRecordRequest extends RequestBase {
  schema: "nxt-staffing-exception/v1";
  service_date: string;
  expected_roster_revision: number;
  expected_exception_set_revision: number;
  staff_id: string;
  kind: "LEAVE" | "LATE" | "EARLY_DEPARTURE" | "UNAVAILABLE";
  time_local: string | null;
  note: string | null;
}
interface ExceptionReplacementInput {
  kind: "LEAVE" | "LATE" | "EARLY_DEPARTURE" | "UNAVAILABLE";
  time_local: string | null;
  note: string | null;
}
interface ExceptionCancelRequest extends RequestBase {
  schema: "nxt-staffing-exception-cancel/v1";
  expected_exception_set_revision: number;
  note: string | null;
}
interface ExceptionCorrectRequest extends RequestBase {
  schema: "nxt-staffing-exception-correct/v1";
  expected_exception_set_revision: number;
  replacement: ExceptionReplacementInput;
}
interface SuggestionGenerateRequest extends RequestBase {
  schema: "nxt-staffing-suggestion-generate/v1";
  service_date: string;
  expected_revisions: RevisionVector;
  retry_of: string | null;
}
interface ManagerResponseRequest extends RequestBase {
  schema: "nxt-staffing-manager-response/v1";
  kind: ManagerResponseKind;
  expected_revisions: RevisionVector;
  candidate_index: 1 | 2 | null;
  edited_operations: ManagerPatchOperation[] | null;
  reason_code: ManagerReasonCode;
  note: string | null;
}
type StaffingRequestBody =
  | RosterImportRequest | ExceptionRecordRequest | ExceptionCancelRequest
  | ExceptionCorrectRequest | SuggestionGenerateRequest | ManagerResponseRequest;
```

Copy these exact fields into `$defs`; apply the domain plan's canonical-code, identifier, minute, count, and array limits rather than replacing any nested item with a free-form object.
Express exception record/replacement as branches: LEAVE/UNAVAILABLE require `time_local: null`, while LATE/EARLY_DEPARTURE require strict minute `HH:MM`; correction inherits staff/service date from the path-addressed prior exception and cannot move it to another employee/day. The server/domain still derives the unavailable UTC interval and rejects staff without an applicable assignment.
Express `ManagerResponseRequest` as three schema branches: ACCEPT fixes `candidate_index` to 1 or 2, `edited_operations` to null, and `reason_code` to `APPROVED`; MODIFY fixes a candidate index, requires 1..32 `ManagerPatchOperation` items, and fixes `APPROVED_WITH_CHANGES`; REJECT fixes candidate/edits to null and permits only `MANUAL_HANDLING`, `INSUFFICIENT_CONTEXT`, or `OTHER`.

- [ ] **Step 5: Replace the open receipt record with a discriminated closed union**

Use one common envelope and closed operation/state branches. Runtime parsing must dispatch on both `operation_kind` and `state`, then validate the matching `record_kind`; no open-ended record map is allowed:

```typescript
interface ReceiptBase<K extends OperationKind, S extends OperationState, R> {
  schema: "nxt-staffing/v1";
  disposition: "created" | "duplicate";
  operation_kind: K;
  request_id: string;
  operation_id: string;
  state: S;
  record: R;
}
interface RosterImportedRecord {
  record_kind: "ROSTER_IMPORTED";
  roster_revision: number;
  roster_digest: string;
  imported_at_utc: string;
  worker_count: number;
  assignment_count: number;
  coverage_rule_count: number;
}
interface ExceptionRecordedRecord {
  record_kind: "EXCEPTION_RECORDED";
  exception: ExceptionProjection;
  exception_set_revision: number;
  recorded_at_utc: string;
}
interface ExceptionCancelledRecord {
  record_kind: "EXCEPTION_CANCELLED";
  exception_id: string;
  exception_set_revision: number;
  cancelled_at_utc: string;
}
interface ExceptionCorrectedRecord {
  record_kind: "EXCEPTION_CORRECTED";
  replaced_exception_id: string;
  replacement: ExceptionProjection;
  exception_set_revision: number;
  corrected_at_utc: string;
}
interface GenerationRecordBase {
  suggestion_id: string;
  service_date: string;
  basis: RevisionVector & { digest: string };
  retry_of: string | null;
  provenance: ProviderProvenance[];
}
interface GenerationReservedRecord extends GenerationRecordBase {
  record_kind: "GENERATION_RESERVED";
}
interface GenerationInProgressRecord extends GenerationRecordBase {
  record_kind: "GENERATION_IN_PROGRESS";
}
interface GenerationInterruptedRecord extends GenerationRecordBase {
  record_kind: "GENERATION_INTERRUPTED";
  failure_code: "RESULT_UNKNOWN";
}
interface SuggestionIssuedRecord extends GenerationRecordBase {
  record_kind: "SUGGESTION_ISSUED";
  candidates: CandidateProjection[];
  coverage_gaps: CoverageGap[];
  failure_code: null;
}
interface SuggestionUnavailableRecord extends GenerationRecordBase {
  record_kind: "SUGGESTION_UNAVAILABLE";
  candidates: CandidateProjection[];
  coverage_gaps: CoverageGap[];
  failure_code: string;
}
interface ManagerResponseCommittedRecord {
  record_kind: "MANAGER_RESPONSE_COMMITTED";
  suggestion_id: string;
  response_kind: ManagerResponseKind;
  reason_code: ManagerReasonCode;
  note: string | null;
  effective_plan: EffectivePlanProjection | null;
  committed_at_utc: string;
}
type StaffingReceipt =
  | ReceiptBase<"roster-import", "COMMITTED", RosterImportedRecord>
  | ReceiptBase<"exception-record", "COMMITTED", ExceptionRecordedRecord>
  | ReceiptBase<"exception-cancel", "COMMITTED", ExceptionCancelledRecord>
  | ReceiptBase<"exception-correct", "COMMITTED", ExceptionCorrectedRecord>
  | ReceiptBase<"suggestion-generate", "RESERVED", GenerationReservedRecord>
  | ReceiptBase<"suggestion-generate", "IN_PROGRESS", GenerationInProgressRecord>
  | ReceiptBase<"suggestion-generate", "RESULT_UNKNOWN", GenerationInterruptedRecord>
  | ReceiptBase<"suggestion-generate", "SUCCEEDED", SuggestionIssuedRecord>
  | ReceiptBase<"suggestion-generate", UnavailableGenerationState, SuggestionUnavailableRecord>
  | ReceiptBase<"manager-response", "COMMITTED", ManagerResponseCommittedRecord>;
```

In JSON Schema, express this with `oneOf` branches containing `const` values for `operation_kind`, `state`, and `record.record_kind`. Freeze the domain-to-wire projection explicitly:

| Domain replay authority | `operation_kind` | wire `state` | `record_kind` |
|---|---|---|---|
| `roster_imported` | `roster-import` | `COMMITTED` | `ROSTER_IMPORTED` |
| `exception_recorded` | `exception-record` | `COMMITTED` | `EXCEPTION_RECORDED` |
| `exception_cancelled` | `exception-cancel` | `COMMITTED` | `EXCEPTION_CANCELLED` |
| `exception_corrected` | `exception-correct` | `COMMITTED` | `EXCEPTION_CORRECTED` |
| `generation_reserved`, no attempt boundary | `suggestion-generate` | `RESERVED` | `GENERATION_RESERVED` |
| `generation_reserved` plus nonterminal attempt boundary | `suggestion-generate` | `IN_PROGRESS` | `GENERATION_IN_PROGRESS` |
| `generation_interrupted` | `suggestion-generate` | `RESULT_UNKNOWN` | `GENERATION_INTERRUPTED` |
| `suggestion_issued` | `suggestion-generate` | `SUCCEEDED` | `SUGGESTION_ISSUED` |
| `suggestion_unavailable` | `suggestion-generate` | terminal state other than `SUCCEEDED` | `SUGGESTION_UNAVAILABLE` |
| `manager_response_committed` | `manager-response` | `COMMITTED` | `MANAGER_RESPONSE_COMMITTED` |

Composition-owned `project_request_to_wire()` is the sole adapter for this table; the domain `request_projection()` returns only its closed local projection and never imports or manufactures HTTP fields. The composition adapter copies internal `generation_id` to public `suggestion_id`, copies the domain receipt `event_id` to `operation_id`, and rejects an impossible replay tuple rather than manufacturing a fallback record. A recovery GET always uses `disposition: "duplicate"`; a business mutation response supplies that field from its `CommittedReceipt`/`DuplicateReceipt` result. Provider-attempt, suggestion-terminal, and interruption writes instead return the ledger-owned `EventCommit`; worker/observer code may ignore that success value, never passes it to `to_wire_receipt`, and still treats any raised validation, integrity, or I/O error as fail-closed.
For every candidate, `project_request_to_wire` and `project_date_to_wire` map each domain `CoverageGap` exactly as follows: `required` to `required_count`, `actual` to `assigned_count`, and the constant string `COVERAGE_GAP` to `rejection_code`. The JSON Schema permits only that literal rejection code. Add a projection test that asserts this complete mapping and rejects a missing, renamed, or invented gap field.
For provenance, pair each internal `AttemptStartedEvidence` with its matching `AttemptFinishedEvidence` by attempt index, then emit one `ProviderProvenance`; `input_digest` comes from the start record and the bounded failure/token fields come from the finish record. The internal attempt status is used only to derive the generation state and terminal record and is not copied into public provenance. An unmatched start changes the generation state to `IN_PROGRESS` but is not serialized as a fake finished provenance row. Alias maps, nonce digests, timeout values, prompts, and raw outputs remain private.

- [ ] **Step 6: Add failing Python tests for every receipt branch**

For each branch above, validate one matching fixture and prove these cross-pairs fail:

```python
EXPECTED_RECEIPT_TRIPLES = {
    ("roster-import", "COMMITTED", "ROSTER_IMPORTED"),
    ("exception-record", "COMMITTED", "EXCEPTION_RECORDED"),
    ("exception-cancel", "COMMITTED", "EXCEPTION_CANCELLED"),
    ("exception-correct", "COMMITTED", "EXCEPTION_CORRECTED"),
    ("suggestion-generate", "RESERVED", "GENERATION_RESERVED"),
    ("suggestion-generate", "IN_PROGRESS", "GENERATION_IN_PROGRESS"),
    ("suggestion-generate", "RESULT_UNKNOWN", "GENERATION_INTERRUPTED"),
    ("suggestion-generate", "SUCCEEDED", "SUGGESTION_ISSUED"),
    *(("suggestion-generate", state, "SUGGESTION_UNAVAILABLE") for state in (
        "NO_VALID_SUGGESTION", "UNAVAILABLE", "REFUSED", "INVALID_RESPONSE",
        "PROVIDER_ERROR", "CONFIGURATION_ERROR", "SECURITY_ERROR",
    )),
    ("manager-response", "COMMITTED", "MANAGER_RESPONSE_COMMITTED"),
}

def test_fixture_receipt_triples_are_exact():
    actual = set()
    for filename, exchange in all_exchanges():
        data = exchange["body"].get("data")
        if filename != "errors.json" and "operation_kind" in data:
            actual.add((data["operation_kind"], data["state"], data["record"]["record_kind"]))
    assert actual == EXPECTED_RECEIPT_TRIPLES

@pytest.mark.parametrize(("operation_kind", "state", "record_kind"), [
    ("roster-import", "COMMITTED", "GENERATION_RESERVED"),
    ("suggestion-generate", "RESERVED", "SUGGESTION_ISSUED"),
    ("suggestion-generate", "RESULT_UNKNOWN", "GENERATION_IN_PROGRESS"),
    ("manager-response", "SUCCEEDED", "MANAGER_RESPONSE_COMMITTED"),
])
def test_receipt_rejects_mismatched_operation_state_and_record(
    operation_kind, state, record_kind
):
    malformed = copy.deepcopy(valid_receipt("roster-import"))
    malformed.update(operation_kind=operation_kind, state=state)
    malformed["record"]["record_kind"] = record_kind
    with pytest.raises(ValidationError):
        validator("#/$defs/StaffingReceipt").validate(malformed)
```

- [ ] **Step 7: Add `errors.json` with fixed status/envelope exchanges**

Use the existing Manager API error envelope exactly. Write this complete fixture so 400/404/409/429/503 all have literal examples and every staffing code has one fixed status:

```json
{
  "schema": "nxt-staffing-errors/v1",
  "exchanges": [
    {
      "name": "invalid-request",
      "route_template": "/api/v1/staffing/exceptions",
      "method": "POST",
      "http_status": 400,
      "body": {
        "schema": "nxt-site-agent/api/v0",
        "disclaimer": "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA",
        "error": {"code": "staffing_invalid_request", "detail": "request body is invalid"}
      }
    },
    {
      "name": "request-not-found",
      "route_template": "/api/v1/staffing/requests/{operation_kind}/{request_id}",
      "method": "GET",
      "http_status": 404,
      "body": {
        "schema": "nxt-site-agent/api/v0",
        "disclaimer": "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA",
        "error": {"code": "staffing_request_not_found", "detail": "no committed request in verified evidence"}
      }
    },
    {
      "name": "resource-not-found",
      "route_template": "/api/v1/staffing/dates/{service_date}",
      "method": "GET",
      "http_status": 404,
      "body": {
        "schema": "nxt-site-agent/api/v0",
        "disclaimer": "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA",
        "error": {"code": "staffing_not_found", "detail": "staffing resource was not found"}
      }
    },
    {
      "name": "idempotency-conflict",
      "route_template": "/api/v1/staffing/roster-imports",
      "method": "POST",
      "http_status": 409,
      "body": {
        "schema": "nxt-site-agent/api/v0",
        "disclaimer": "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA",
        "error": {"code": "staffing_conflict", "detail": "request_id is already bound to different content"}
      }
    },
    {
      "name": "stale-suggestion",
      "route_template": "/api/v1/staffing/suggestions/{suggestion_id}/accept",
      "method": "POST",
      "http_status": 409,
      "body": {
        "schema": "nxt-site-agent/api/v0",
        "disclaimer": "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA",
        "error": {"code": "staffing_stale_suggestion", "detail": "staffing basis has changed"}
      }
    },
    {
      "name": "generation-busy",
      "route_template": "/api/v1/staffing/suggestions",
      "method": "POST",
      "http_status": 429,
      "body": {
        "schema": "nxt-site-agent/api/v0",
        "disclaimer": "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA",
        "error": {"code": "staffing_busy", "detail": "generation capacity is full"}
      }
    },
    {
      "name": "staffing-unavailable",
      "route_template": "/api/v1/staffing",
      "method": "GET",
      "http_status": 503,
      "body": {
        "schema": "nxt-site-agent/api/v0",
        "disclaimer": "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA",
        "error": {"code": "staffing_unavailable", "detail": "staffing evidence is unavailable"}
      }
    }
  ]
}
```

Validate the fixture in Python with a literal map, not a family/range assertion:

```python
EXPECTED_ERROR_STATUS = {
    "staffing_invalid_request": 400,
    "staffing_not_found": 404,
    "staffing_request_not_found": 404,
    "staffing_conflict": 409,
    "staffing_stale_suggestion": 409,
    "staffing_busy": 429,
    "staffing_unavailable": 503,
}

def test_error_examples_have_fixed_envelopes_and_statuses():
    document = load_example("errors.json")
    assert document["schema"] == "nxt-staffing-errors/v1"
    exchanges = document["exchanges"]
    assert {row["body"]["error"]["code"] for row in exchanges} == set(EXPECTED_ERROR_STATUS)
    for row in exchanges:
        assert set(row) == {"name", "method", "route_template", "http_status", "body"}
        validator("#/$defs/ErrorEnvelope").validate(row["body"])
        assert row["http_status"] == EXPECTED_ERROR_STATUS[row["body"]["error"]["code"]]
```

- [ ] **Step 8: Write the closed schema and README, then run the focused test**

The success envelope remains:

```json
{"schema":"nxt-site-agent/api/v0","disclaimer":"SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA","data":{}}
```

Use `additionalProperties: false` on every container, exchange, request, response, and nested contract object, `maxItems: 2` for candidates, and `maxItems: 32` for patch operations. `BUSY` appears only in `ErrorEnvelope`, never in `OperationState`.

Freeze the seven non-error fixture inventories so every approved route and receipt branch has concrete data rather than prose-only examples:

```python
EXPECTED_SUCCESS_EXCHANGES = {
    "cold-start.json": {"current-empty", "date-empty"},
    "roster-import.json": {"roster-committed"},
    "exception-correction.json": {
        "exception-recorded", "exception-cancelled", "exception-corrected",
    },
    "generation-success.json": {
        "generation-reserved", "generation-in-progress", "suggestion-issued",
    },
    "generation-unavailable.json": {
        "no-valid-suggestion", "unavailable", "refused", "invalid-response",
        "provider-error", "configuration-error", "security-error",
    },
    "result-unknown.json": {"generation-interrupted"},
    "manager-response.json": {
        "manager-accepted", "manager-modified", "manager-rejected",
        "date-after-manager-response",
    },
}
REQUEST_DEF_BY_ROUTE = {
    "/api/v1/staffing/roster-imports": "RosterImportRequest",
    "/api/v1/staffing/exceptions": "ExceptionRecordRequest",
    "/api/v1/staffing/exceptions/{exception_id}/cancel": "ExceptionCancelRequest",
    "/api/v1/staffing/exceptions/{exception_id}/correct": "ExceptionCorrectRequest",
    "/api/v1/staffing/suggestions": "SuggestionGenerateRequest",
    "/api/v1/staffing/suggestions/{suggestion_id}/accept": "ManagerResponseRequest",
    "/api/v1/staffing/suggestions/{suggestion_id}/modify": "ManagerResponseRequest",
    "/api/v1/staffing/suggestions/{suggestion_id}/reject": "ManagerResponseRequest",
}

def test_success_examples_are_closed_and_validate_by_branch():
    for filename, expected_names in EXPECTED_SUCCESS_EXCHANGES.items():
        document = load_example(filename)
        assert document["schema"] == "nxt-staffing-exchanges/v1"
        assert {row["name"] for row in document["exchanges"]} == expected_names
        for row in document["exchanges"]:
            assert row["http_status"] in {200, 202}
            if row["method"] == "POST":
                request_def = REQUEST_DEF_BY_ROUTE[row["route_template"]]
                validator(f"#/$defs/{request_def}").validate(row["request"])
            else:
                assert "request" not in row
            validator("#/$defs/SuccessEnvelope").validate(row["body"])
            data = row["body"]["data"]
            target = (
                "#/$defs/StaffingReceipt"
                if "operation_kind" in data else "#/$defs/StaffingDateSnapshot"
            )
            validator(target).validate(data)
```

In `README.md`, list for every exchange its request schema (or `none` for GET), response `$defs` target, literal operation/state/record triple when it is a receipt, and expected HTTP status. `cold-start.json` supplies root/date snapshots; `exception-correction.json` supplies record/cancel/correct; `manager-response.json` supplies accept/modify/reject plus a refreshed date snapshot whose matching generation has non-null `manager_response`. Every other generation snapshot sets `manager_response` explicitly to null. The fixture data must use the authoritative roster shape: top-level `site_timezone`, `workers`, `availability`, `assignment_rules`, `regular_assignments`, and `coverage`.

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/pilot_ops/test_staffing_wire_contract.py
```

Expected after schema/examples are written: all example bodies validate and all intentional operation/state/record mismatches fail validation.

- [ ] **Step 9: Commit the shared contract**

```bash
git add simulation/docs/contracts/staffing-v1 \
  simulation/tests/pilot_ops/test_staffing_wire_contract.py
git commit -m "feat(staffing-api): freeze advisory wire contract"
```

### Task 2: Add the optional Site Agent staffing transport seam

**Files:**
- Modify: `simulation/nxt_site_agent/api.py:60-102,279-456,602-759`
- Create: `simulation/tests/site_agent/test_staffing_api.py`

**Interfaces:**
- Consumes: optional `Callable[[method, path, body], dict]` from composition.
- Produces: allowlisted loopback HTTP framing only; no domain/provider interpretation.

- [ ] **Step 1: Scaffold a recording callback fixture and fixed error map test**

Start a normal `SiteAgentService` with a recording callback and parameterize the fixed map:

```python
STAFFING_STATUS_BY_CODE = {
    "staffing_invalid_request": 400,
    "staffing_not_found": 404,
    "staffing_request_not_found": 404,
    "staffing_conflict": 409,
    "staffing_stale_suggestion": 409,
    "staffing_busy": 429,
    "staffing_unavailable": 503,
}
```

- [ ] **Step 2: Add one failing parameterized route/status test**

Use the Task 1 route table as parameters. Assert each callback receives `(method, stripped_path, body)` exactly once, root/date/request GET return 200, exact suggestion POST returns 202, and every other POST returns 200:

```python
@pytest.mark.parametrize(("method", "path", "expected_status"), STAFFING_ROUTES)
def test_staffing_routes_delegate_once(served_staffing, method, path, expected_status):
    status, payload = request(served_staffing.server, method, path + "?ignored=1", {})
    assert status == expected_status
    assert served_staffing.calls == [(method, path, {} if method == "GET" else {})]
    assert payload["data"]["schema"] == "nxt-staffing/v1"
```

- [ ] **Step 3: Add failing unknown-route, method, optional-callback, and redaction tests**

Assert unknown shapes and PUT/PATCH/DELETE do not call the callback. No callback yields 503 `staffing_unavailable`; callback `RuntimeError("secret-body")` yields the same generic envelope and neither response nor captured stderr includes `secret-body`.

- [ ] **Step 4: Add failing per-route size tests**

Assert 65,537-byte ordinary exception fails 413 and closes the connection; a valid roster JSON between 64 KiB and 1 MiB reaches the callback; 1 MiB + 1 fails 413 before JSON parsing. For every staffing POST, duplicate JSON keys and `NaN`/`Infinity` fail before the callback. Re-run existing Host/Origin/chunked/GET-body/PUT/PATCH/DELETE tests against staffing paths to prove no weakening.

- [ ] **Step 5: Add failing strict-JSON and existing-security regression tests**

Send invalid UTF-8, raw `{"request_id":"a","request_id":"b"}`, `{"minutes":NaN}`, `{"minutes":Infinity}`, and a valid non-object JSON root; assert each returns 400 `staffing_invalid_request` with zero callback calls. Parameterize foreign Host, foreign Origin, chunked POST, and GET-with-body using one staffing path each. Roster/body overflow keeps the existing HTTP 413 `body_too_large` envelope and connection-close behavior; it is intentionally not a staffing-domain validation error.

- [ ] **Step 6: Run focused tests and verify RED**

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/site_agent/test_staffing_api.py tests/site_agent/test_api.py
```

- [ ] **Step 7: Add the callback to `_Server` and `SiteAgentApiServer`**

Add the optional keyword to both constructors and store it without importing any new owner:

```python
staffing_operations: Callable[
    [str, str, dict[str, Any]], dict[str, Any]
] | None = None
```

Add only `staffing_*` strings; do not mention model/provider/prompt terms in `nxt_site_agent`.

- [ ] **Step 8: Implement exact path recognition**

Add `_is_staffing_route(method, path)` that recognizes only Task 1 shapes by segment count and terminal action names, without decoding IDs/dates. Return `staffing_not_found` for an unknown shape inside the namespace and `method_not_allowed` for a known shape with the wrong mutation method.

In existing `_serve_other_mutation_method` (`api.py:426-456`), preserve the current Host/Origin checks, then detect the staffing namespace and return the v0 `method_not_allowed` envelope with HTTP 405 before the generic 501 branch. Assert PUT/PATCH/DELETE never invoke `staffing_operations`.

- [ ] **Step 9: Implement redacted callback routing**

`_route_staffing` calls the callback, verifies it returned a dictionary, rethrows only `SiteAgentError` whose code is in the staffing allowlist, and turns every other exception into:

```python
SiteAgentError("staffing_unavailable", "staffing evidence is unavailable")
```

Never include `type(exc)` or `str(exc)`.

- [ ] **Step 10: Make body size explicit per route**

Change `_read_body(self, *, max_bytes: int = _MAX_BODY_BYTES, strict_json: bool = False)`. Before reading a POST body, choose `_MAX_ROSTER_BODY_BYTES = 1_048_576` only for exact `/api/v1/staffing/roster-imports`; all other paths use 65,536. Set `strict_json=True` for the entire staffing namespace and use this explicit decoder behavior:

```python
def reject_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result

payload = json.loads(
    raw.decode("utf-8"),
    object_pairs_hook=reject_pairs,
    parse_constant=lambda value: (_ for _ in ()).throw(
        ValueError(f"non-finite JSON constant: {value}")
    ),
)
```

Catch Unicode decode, duplicate-key, non-finite, JSON syntax, and non-object-root failures at the staffing route boundary and raise `SiteAgentError("staffing_invalid_request", "request body is invalid")`. Preserve legacy parsing/error codes for existing routes and preserve the existing `body_too_large` 413 path before JSON decoding.

- [ ] **Step 11: Return 202 only for exact suggestion reservation**

For exact `/api/v1/staffing/suggestions`, wrap callback data and send 202; all other staffing successes use 200. Do not inspect domain state.

- [ ] **Step 12: Run GREEN and commit**

```bash
git add simulation/nxt_site_agent/api.py simulation/tests/site_agent/test_staffing_api.py
git commit -m "feat(site-agent): expose optional staffing routes"
```

### Task 3: Compose stable evidence, provider settings, and bounded generation

**Files:**
- Create: `simulation/scripts/staffing_operations.py`
- Create: `simulation/tests/site_agent/test_staffing_composition.py`

**Interfaces:**
- Consumes: resolved state-root base, identity/timezone, CLI model/region/language settings, three environment keys, injected audit/monotonic clocks and nonce factory; the explicit deep import `nxt_pilot_ops.staffing.operations.StaffingOperations` with `import_roster`, `record_exception`, `cancel_exception`, `correct_exception`, `commit_manager_response`, `probe_request(operation_kind, payload)`, `reserve_generation(payload, *, alias_nonce, route_evidence, prompt_template_version, language, recorded_at)`, `generation_work(generation_id)`, `record_attempt_started`, `record_attempt_finished`, `commit_generation_result`, `interrupt_generation`, `recover_interrupted_generations`, `date_projection`, and `request_projection`; closed domain `AssignmentProjection`, `ExceptionProjection`, `CandidateOperationProjection`, `CandidateProjection`, `ManagerResponseProjection`, `DateProjection`, `RequestProjection`, `GenerationProjectionView`, `RosterCommittedProjection`, `ExceptionCommittedProjection`, `GenerationCommittedProjection`, `ManagerCommittedProjection`, `CommittedReceipt`, `DuplicateReceipt`, and `ConflictReceipt`; domain `canonical_generation_input`/`STAFFING_SUGGESTION_OUTPUT_SCHEMA`; and model gateway. Task 7 deliberately exports `StaffingOperations` from `operations.__all__` while leaving `staffing/__init__.py` narrow; composition must not rely on an undeclared package-root re-export.
- Produces: one callback object with independent local lifecycle, one active/four waiting operations, durable attempt boundaries, date/request routes, and a redacted failure callback.

**Stability preflight amendment (authoritative over the older snippets below):**

- Task 1's frozen schema and semantic matrices are the final wire authority. Audit UTC and `server_time_utc` use exactly six fractional digits and uppercase `Z`; operational assignment/exception timestamps use whole-minute seconds (`:00`) and either uppercase `Z` or a nonzero whole-minute offset. Projection rejects nonzero seconds, microseconds, signed-zero text, and non-whole-minute offsets rather than normalizing them silently.
- Manager receipt projection compares `operator` in `ManagerResponseProjection` and `ManagerCommittedProjection` and emits the committed `operator`. It also keeps every existing generation/reservation field-equality check.
- `coverage_gap_to_wire` requires exact non-bool integers with `1 <= required <= 10000` and `0 <= actual < required`. Provenance requires unique attempt indexes, exact route/request pairing, legal route order, and one common started `input_digest`; request terminal evidence, when present, must use that same digest. Candidate, generation terminal, capability, and provenance tuples must satisfy the complete Task 1 matrix, not only the abbreviated examples below.
- Cancel/correct request bodies are closed Task 1 objects. Any body `exception_id`, even when equal to the path value, is rejected; only after closed-body validation may the decoded path identifier be added to a detached domain payload. Path segments are decoded exactly once. Manager route identifiers remain separate keyword arguments and are never accepted in the body.
- On a `READY` or `DEGRADED_BACKUP_UNCONFIGURED` route, a valid staffing basis whose canonical system or user message exceeds the gateway's 32,768-character `GenerationMessage` limit is an expected zero-attempt `CONFIGURATION_ERROR/INPUT_TOO_LARGE` terminal. An `UNAVAILABLE` route remains the zero-attempt `CONFIGURATION_ERROR/PROVIDER_UNCONFIGURED` branch. Neither expected branch fail-closes the worker, and the next queued item still runs. Other request-construction drift, schema drift, or canonical-digest mismatch remains fail-closed. Add exact 32,768/32,769 boundary coverage.
- Gateway token usage above the domain evidence maximum of 1,000,000 per input/output counter is never clamped. Evidence construction fails synchronously, gateway raises `AttemptObserverError`, and the worker records `RESULT_UNKNOWN` through the normal interruption path while remaining healthy; test the boundary for all built-in provider result shapes.
- The composition wrapper owns the ledger descriptor lifecycle. Close is idempotent and ordered: stop admission, detach/interrupt healthy waiting work, wait for the worker, then close the ledger. A 25-second join timeout is an explicit shutdown failure, not successful closure: the ledger remains open, the wrapper stays unavailable to new routes, the caller must retain it and retry close after the worker exits, and no upper layer may claim teardown completed. Constructor failures close any ledger whose worker never started; once a worker exists, cleanup follows the same ordered shutdown rule. Cover blocked transport, blocked observer, blocked terminal commit, retry-after-timeout, repeated close, and file-descriptor release.
- Startup recovery completes before routes become reachable and before a worker starts. Recovery only appends `RESULT_UNKNOWN`; it never resends a provider request. `StaffingOperations` remains a deep import and no package-root export is added.

- [ ] **Step 1: Add a failing exact-root test**

Pin:

```python
def resolve_staffing_root(state_root: Path, *, site_id: str,
                          deployment_id: str, volatile_out: Path) -> Path:
    for value in (site_id, deployment_id):
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("staffing identity is not one safe path segment")
    base = state_root.resolve(strict=False)
    volatile = volatile_out.resolve(strict=False)
    target = (base / site_id / deployment_id / "staffing-v1").resolve(strict=False)
    if not target.is_relative_to(base):
        raise ValueError("staffing root escapes configured state root")
    if target == volatile or target.is_relative_to(volatile):
        raise ValueError("stable staffing root cannot live below volatile --out")
    return target

@dataclass(frozen=True, slots=True)
class StaffingProviderSettings:
    region: DeploymentRegion | None
    language: Literal["zh-CN", "en"]
    kimi_model: str | None
    openai_model: str | None
    anthropic_model: str | None
    moonshot_api_key: str | None = field(repr=False)
    openai_api_key: str | None = field(repr=False)
    anthropic_api_key: str | None = field(repr=False)
```

Assert the final path exactly appends identity/`staffing-v1`, rejects a path inside `volatile_out`, rejects symlink escape/identity path separators, and never includes a key in repr/error.

- [ ] **Step 2: Add a failing secret-safe provider-settings test**

Construct settings with sentinel keys and assert `repr(settings)`, `str(settings)`, raised configuration errors, stdout, and stderr contain none of the sentinels. Parameterize CN/GLOBAL readiness: missing region/primary model/key is `UNAVAILABLE`; GLOBAL with OpenAI but no Anthropic is `DEGRADED_BACKUP_UNCONFIGURED`.
For every configuration, assert `generation_route_evidence` is absent when region/primary model is absent; otherwise assert `(primary_provider is None) == (primary_model_id is None)` and `(backup_provider is None) == (backup_model_id is None)`. GLOBAL without a fully configured Anthropic adapter must serialize both backup fields as null.

- [ ] **Step 3: Add failing root/current-date route tests**

With injected UTC clock and `Asia/Shanghai`, assert `GET /api/v1/staffing` uses the local date across UTC midnight; `/dates/YYYY-MM-DD` returns that exact date; invalid dates/IDs/operation kinds fail with stable errors. Add all POSTs including correction. The route adapter injects path IDs into a detached payload and rejects conflicting body IDs.

- [ ] **Step 4: Add failing explicit domain-to-wire projection tests**

Construct the closed domain DTOs directly (do not use dictionaries) and exercise every receipt triple from Task 1. The generation factory keeps the reservation event ID stable while varying only the replayed lifecycle:

```python
NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)
WIRE_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] /
     "docs/contracts/staffing-v1/schema.json").read_text(encoding="utf-8")
)

def validator(ref: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": ref, "$defs": WIRE_SCHEMA["$defs"]})

TERMINAL_FAILURES = {
    "NO_VALID_SUGGESTION": "NO_VALID_SUGGESTION",
    "UNAVAILABLE": "DEADLINE_EXHAUSTED",
    "REFUSED": "PROVIDER_REFUSED",
    "INVALID_RESPONSE": "SCHEMA_MISMATCH",
    "PROVIDER_ERROR": "PROVIDER_CLIENT_ERROR",
    "CONFIGURATION_ERROR": "PROVIDER_UNCONFIGURED",
    "SECURITY_ERROR": "TLS_VERIFICATION_FAILED",
}

def generation_view(state: str) -> GenerationProjectionView:
    candidate = CandidateProjection(
        candidate_index=1, valid=True, rationale="coverage restored",
        operational_warnings=(), rejection_codes=(), coverage_gaps=(),
        operations=(CandidateOperationProjection(
            operation="ADD", assignment_id=None, staff_id="staff-1",
            display_name="Operator One", role_code="BALL_PICKUP",
            area_code="NORTH", start_at=NOW, end_at=NOW + timedelta(hours=1),
        ),), materialized_schedule=(), materialized_schedule_digest="c" * 64,
    )
    return GenerationProjectionView(
        generation_id="generation-1", request_id="request-1",
        operation_event_id="event-reserved-1", service_date=date(2026, 10, 4),
        basis_revisions=(3, 4, 5), basis_digest="a" * 64, retry_of=None,
        lifecycle_state=state, failure_code=TERMINAL_FAILURES.get(state),
        route_region="GLOBAL", route_readiness="READY",
        primary_provider="OPENAI", primary_model_id="gpt-model",
        backup_provider="ANTHROPIC", backup_model_id="claude-model",
        started_attempts=(), finished_attempts=(),
        candidates=(candidate,) if state == "SUCCEEDED" else (),
    )

def generation_request(state: str) -> RequestProjection:
    generation = generation_view(state)
    committed = GenerationCommittedProjection(
        event_id="event-reserved-1", occurred_at_utc=NOW,
        request_digest="b" * 64, generation_id="generation-1",
        service_date=date(2026, 10, 4), basis_revisions=(3, 4, 5),
        basis_digest="a" * 64, retry_of=None, route_region="GLOBAL",
        route_readiness="READY", primary_provider="OPENAI",
        backup_provider="ANTHROPIC",
    )
    return RequestProjection(
        operation_kind="suggestion-generate", request_id="request-1",
        event_ids=("event-reserved-1",), generation_id="generation-1",
        lifecycle_state=state, started_attempts=(), attempt_evidence=(),
        result_evidence=None, candidates=(), manager_response=None,
        generation=generation, committed_record=committed,
    )

@pytest.mark.parametrize("state", [
    "RESERVED", "IN_PROGRESS", "RESULT_UNKNOWN", "SUCCEEDED",
    *TERMINAL_FAILURES,
])
def test_generation_projection_emits_exact_closed_branch(state):
    wire = project_request_to_wire(generation_request(state), disposition="duplicate")
    validator("#/$defs/StaffingReceipt").validate(wire)
    assert wire["operation_id"] == "event-reserved-1"
    assert wire["state"] == state
    assert all("status" not in row for row in wire["record"]["provenance"])

def test_coverage_gap_mapping_is_literal_and_complete():
    gap = CoverageGap("BALL_PICKUP", "NORTH", NOW, NOW + timedelta(hours=1), 2, 1)
    assert coverage_gap_to_wire(gap) == {
        "role_code": "BALL_PICKUP", "area_code": "NORTH",
        "start_at": "2026-10-03T00:00:00Z",
        "end_at": "2026-10-03T01:00:00Z",
        "required_count": 2, "assigned_count": 1,
        "rejection_code": "COVERAGE_GAP",
    }

def test_date_projection_joins_one_durable_manager_response_by_generation():
    projection = DateProjection(
        site_id="site-1", deployment_id="deployment-1",
        service_date=date(2026, 10, 4), site_timezone="Asia/Shanghai",
        roster_revision=3, roster_digest="d" * 64, exception_set_revision=4,
        roster_effective_from=date(2026, 10, 1), roster_effective_until=None,
        worker_count=1, assignment_count=1, coverage_count=1,
        effective_plan_schedule=(), availability=(), assignments=(), exceptions=(),
        effective_plan=EffectivePlanState(1, "CURRENT", "e" * 64, (), 9, ()),
        generations=(generation_view("SUCCEEDED"),),
        manager_responses=(ManagerResponseProjection(
            generation_id="generation-1", decision="ACCEPT",
            reason_code="APPROVED", operator="经理甲", note="采用一号方案",
            effective_plan_revision=1, schedule_digest="e" * 64,
            effective_schedule=(),
        ),),
    )
    wire = project_date_to_wire(
        projection, site_id="site-1", deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    assert wire["generations"][0]["manager_response"] == {
        "response_kind": "ACCEPT", "reason_code": "APPROVED",
        "operator": "经理甲", "note": "采用一号方案",
        "effective_plan": {
            "revision": 1, "status": "CURRENT", "schedule_digest": "e" * 64,
            "assignments": [],
        },
    }
```

Add literal `RosterCommittedProjection`, all three `ExceptionCommittedProjection` event types, and `ManagerCommittedProjection` cases to the same table and assert their operation/state/record triples equal `EXPECTED_RECEIPT_TRIPLES`. Add negative cases for a mismatched committed-record class, mismatched operation event ID, a finished attempt without its start, duplicate attempt indexes, missing required ADD/REMOVE local fields, non-UTC audit timestamps, wrong site/deployment/timezone, nullable non-`NO_VALID_SUGGESTION` terminal failure code, duplicate manager responses for one generation, a response whose generation is absent, and any alias/nonce/timeout/raw-provider field appearing in serialized output. Each must raise `StaffingProjectionError`; the public route converts it to generic `staffing_unavailable`.

Freeze `offset_time` independently with exact expected strings: a UTC value emits uppercase `Z`, never `+00:00` or `-00:00`; `Asia/Shanghai` remains `+08:00`; and the two `America/New_York` fall-back instants retain their distinct `-04:00` and `-05:00` suffixes. This change applies only to the zero-offset branch of `offset_time`; audit `utc_time`, server time, model-facing time, local-minute, and every other timestamp profile remain unchanged.

Add the integration half of the domain plan's restart → projection → API → unchanged MODIFY test. Parameterize a real reopened staffing owner and public routes for a UTC site, `Asia/Shanghai`, and both New York fall-back folds. Read the candidate through both date and request APIs, require exact `Z|+08:00|-04:00|-05:00` ADD timestamp suffixes, copy the manager-edit fields and timestamp strings unchanged into `POST /api/v1/staffing/suggestions/{suggestion_id}/modify`, and require an accepted committed response with the same UTC instants and schedule digest and no `OFFSET_TIMEZONE_MISMATCH`. In particular, the UTC-site request must pass the manager parser as uppercase `Z`; add negative controls showing signed-zero `+00:00` and `-00:00` are never emitted and remain rejected on manager input.

- [ ] **Step 5: Add a failing exact-route dispatch test**

Use a fake domain owner with one method per operation. Assert every POST dispatches once, URL-decodes each path segment exactly once, cancel/correct body/path ID disagreement returns `staffing_invalid_request`, and an unknown operation kind never reaches `request_projection`. For accept/modify/reject, assert the decoded path value is passed only as the `suggestion_id=` keyword; request bodies containing hidden `suggestion_id` or `generation_id` fail closed as unknown fields with HTTP 400.

- [ ] **Step 6: Add a failing 1+4 capacity test**

Block a fake provider. Admit one active plus four waiting distinct IDs. The sixth distinct ID returns `staffing_busy` and ledger bytes/count are unchanged. A duplicate of any of the five returns its durable receipt even while full and does not consume another slot. While blocked, complete `GET /api/v1/staffing`, date GET, exception POST, and an independent Site Agent health GET within a short test deadline.

- [ ] **Step 7: Add a failing no-network-lock test**

Block `ModelGateway.generate` with a `threading.Event`. From a second executor submit date GET, request GET, exception POST, and `/api/v0/health`; assert all four futures complete before releasing the gateway event. Inspect the owner test double and assert no domain method remains entered while `generate` is blocked.

- [ ] **Step 8: Add failing attempt-order tests**

The fake gateway records call order; at `transport` entry, reopen the verified internal ledger/history and assert `provider_attempt_started` is durable. Do not infer STARTED from the public API projection, which exposes only bounded finished-attempt provenance. At adapter return, assert `provider_attempt_finished` precedes terminal:

```python
def transport(_request):
    history = reopen_ledger().read()
    assert history.events[-1].event_type == "provider_attempt_started"
    order.append("transport")
    return gateway_success()

assert order == ["attempt_started", "transport", "attempt_finished", "terminal"]
```

- [ ] **Step 9: Add failing restart and explicit-retry tests**

Reopen from each nonterminal state (`RESERVED`, attempt started, attempt finished), build operations, and assert it appends `generation_interrupted`, reports `RESULT_UNKNOWN`, and makes zero gateway calls. Submitting the old request ID is duplicate recovery; only a new request ID plus `retry_of=old_generation_id` produces one new call.

Also make the fake observer append fail at `started` and `finished`. Assert `AttemptObserverError` stops the route/fallback, the worker calls `owner.interrupt_generation(generation_id, recorded_at=clock())`, the request projects `RESULT_UNKNOWN`, and no terminal suggestion event is written. If that interruption append also fails, mark the generation worker failed closed; later generation submissions return `staffing_unavailable`, while startup recovery remains the only retry path.

Return a schema-valid candidate containing an unknown worker/assignment alias and assert `commit_generation_result` writes terminal `INVALID_RESPONSE` rather than raising or leaving the generation nonterminal. Drive provider-specific successful envelopes through the real `KimiAdapter`, `OpenAIAdapter`, and `AnthropicAdapter` with bounded fake transports for two additional portable-schema-valid cases: one candidate with 4097 empty warnings, and candidate/operation arrays whose decoded tree exceeds 4096 occurrences while the complete HTTP response remains at or below 524,288 bytes. Each adapter must first return a successful decoded output; composition must then durably commit `INVALID_RESPONSE/invalid_provider_shape`, keep the worker healthy, and run the next queued generation. It must never surface `staffing_invalid_evidence`, enter failed-close, or leave the reservation nonterminal. This proves the 524,289-occurrence result-evidence ceiling dominates the transport limit while Task 4's later 4096-occurrence bound remains an ordinary provider-wire rejection. Separately inject ledger/integrity I/O failures from `generation_work` and `commit_generation_result`: the worker must become failed closed, make no later queued provider call, leave any nonterminal reservation for startup recovery, and expose only generic `staffing_unavailable`. Finally, force a mismatch between `GenerationRequest.canonical_input_digest` and `generation_work().input_digest`; assert zero provider calls and the same failed-closed behavior.

- [ ] **Step 10: Run composition tests and verify RED**

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/site_agent/test_staffing_composition.py
```

- [ ] **Step 11: Implement provider settings loading**

Expose this exact loader and construct only the adapters allowed by region/config:

```python
def load_provider_settings(
    env: Mapping[str, str], *, region: str | None, language: str,
    kimi_model: str | None, openai_model: str | None,
    anthropic_model: str | None,
) -> StaffingProviderSettings:
    if language not in {"zh-CN", "en"}:
        raise ValueError("staffing language must be zh-CN or en")
    parsed_region = None if region is None else DeploymentRegion(region)
    def clean(value: str | None) -> str | None:
        return None if value is None or not value.strip() else value.strip()
    return StaffingProviderSettings(
        region=parsed_region,
        language=language,
        kimi_model=clean(kimi_model),
        openai_model=clean(openai_model),
        anthropic_model=clean(anthropic_model),
        moonshot_api_key=clean(env.get("MOONSHOT_API_KEY")),
        openai_api_key=clean(env.get("OPENAI_API_KEY")),
        anthropic_api_key=clean(env.get("ANTHROPIC_API_KEY")),
    )
```

Do not accept endpoints. Construct only secret-safe `ProviderConfig` values for configured adapters.

- [ ] **Step 12: Implement gateway construction without endpoint inputs**

Build `KimiAdapter`, `OpenAIAdapter`, and `AnthropicAdapter` only from their fixed package endpoints, `ProviderConfig`, and one shared private transport implementation. Do not add an endpoint parameter:

```python
@dataclass(frozen=True, slots=True)
class ConfiguredGateway:
    gateway: ModelGateway
    route: RoutePolicy | None
    readiness: RouteReadiness | None

def configured_gateway(
    settings: StaffingProviderSettings,
    *,
    monotonic: Callable[[], float],
) -> ConfiguredGateway:
    transport = StdlibHttpsTransport(monotonic=monotonic)
    kimi = (
        KimiAdapter(
            config=ProviderConfig(Provider.KIMI, settings.kimi_model,
                                  settings.moonshot_api_key),
            transport=transport,
        )
        if settings.region is DeploymentRegion.CN
        and settings.kimi_model is not None
        and settings.moonshot_api_key is not None else None
    )
    openai = (
        OpenAIAdapter(
            config=ProviderConfig(Provider.OPENAI, settings.openai_model,
                                  settings.openai_api_key),
            transport=transport,
        )
        if settings.region is DeploymentRegion.GLOBAL
        and settings.openai_model is not None
        and settings.openai_api_key is not None else None
    )
    anthropic = (
        AnthropicAdapter(
            config=ProviderConfig(Provider.ANTHROPIC, settings.anthropic_model,
                                  settings.anthropic_api_key),
            transport=transport,
        )
        if settings.region is DeploymentRegion.GLOBAL
        and settings.anthropic_model is not None
        and settings.anthropic_api_key is not None else None
    )
    gateway = ModelGateway(
        kimi=kimi, openai=openai, anthropic=anthropic, monotonic=monotonic,
    )
    if settings.region is None:
        return ConfiguredGateway(gateway, None, None)
    route = RoutePolicy(settings.region)
    return ConfiguredGateway(gateway, route, gateway.readiness(route))
```

Import `StdlibHttpsTransport` from its gateway-owned transport module; do not re-export or copy it. Import `canonical_generation_input` and `STAFFING_SUGGESTION_OUTPUT_SCHEMA` directly from `nxt_pilot_ops.staffing.projection`. Use the helper to map the frozen staffing projection to `GenerationMessage`/`GenerationRequest`, set `max_output_tokens=2048`, `deadline_budget_s=15.0` for CN or `20.0` for GLOBAL, and use only the returned `RoutePolicy`.

Freeze the domain reservation route with this mapper; the domain computes and stores the provider `input_digest` inside the same locked reservation builder that materializes the basis and aliases:

```python
def generation_route_evidence(
    configured: ConfiguredGateway,
    settings: StaffingProviderSettings,
) -> GenerationRouteEvidence | None:
    if configured.route is None or configured.readiness is None:
        return None
    primary = Provider.KIMI if configured.route.region is DeploymentRegion.CN else Provider.OPENAI
    primary_model = settings.kimi_model if primary is Provider.KIMI else settings.openai_model
    if primary_model is None:
        return None
    readiness = configured.readiness
    primary_provider = readiness.primary_provider
    backup_provider = readiness.backup_provider
    return GenerationRouteEvidence(
        region=configured.route.region.value,
        readiness=readiness.status.value,
        primary_provider=(
            None if primary_provider is None else primary_provider.value
        ),
        primary_model_id=(primary_model if primary_provider is not None else None),
        backup_provider=(
            None if backup_provider is None else backup_provider.value
        ),
        backup_model_id=(
            settings.anthropic_model
            if backup_provider is Provider.ANTHROPIC else None
        ),
    )
```

A missing region or primary model yields no `GenerationRouteEvidence` and makes generation return `staffing_unavailable` before reservation; local reads/writes remain available. A known route/model with a missing primary key has `UNAVAILABLE` readiness, may reserve, and then commits `CONFIGURATION_ERROR/PROVIDER_UNCONFIGURED` without transport.

- [ ] **Step 13: Implement `LedgerAttemptObserver`**

Map gateway events to the domain's frozen evidence types and synchronously append before returning. Staffing composition intentionally narrows the low-level adapter protocol to the repository's canonical failure disposition table: all three built-in adapters satisfy it, and any future/custom adapter must do the same. A protocol-constructible outcome with a mismatched status/retry/security disposition, a successful KIMI/OpenAI/ANTHROPIC finish reason other than exact `stop`/`completed`/`tool_use` (including null), an orchestration-local `INPUT_TOO_LARGE`/`BACKUP_UNCONFIGURED`/`PROVIDER_UNCONFIGURED` attempt code, an impossible readiness/provider/model shape, or a timeout above the route cap is local integrity failure. The domain rejects that start/finish append; `ModelGateway` surfaces `AttemptObserverError`; the worker uses its existing at-most-once path to append durable `generation_interrupted/RESULT_UNKNOWN` and never writes a suggestion terminal for that call. Tests use only conforming fakes for normal paths and deliberately nonconforming disposition and bad/null-success-reason fakes to prove this exact interruption path. Fallback eligibility is still derived from the exact failure code, never from caller text.

```python
def attempt_route(
    attempt: AttemptStarted | AttemptRecord, *, region: DeploymentRegion,
) -> AttemptRouteEvidence:
    return AttemptRouteEvidence(
        provider=attempt.provider.value,
        region=region.value,
        route_role="PRIMARY" if attempt.attempt_index == 0 else "BACKUP",
        route_id=f"{region.value.lower()}-{attempt.provider.value.lower()}-v1",
        model_id=attempt.model_id,
    )

def started_evidence(
    attempt: AttemptStarted, *, region: DeploymentRegion,
) -> AttemptStartedEvidence:
    return AttemptStartedEvidence(
        request_id=attempt.request_id,
        attempt_index=attempt.attempt_index,
        route=attempt_route(attempt, region=region),
        input_digest=attempt.input_digest,
        timeout_s=attempt.timeout_s,
    )

def finished_evidence(
    attempt: AttemptRecord, *, region: DeploymentRegion,
) -> AttemptFinishedEvidence:
    return AttemptFinishedEvidence(
        request_id=attempt.request_id,
        attempt_index=attempt.attempt_index,
        route=attempt_route(attempt, region=region),
        status=attempt.status.value,
        failure_code=None if attempt.failure_code is None else attempt.failure_code.value,
        retryable=attempt.retryable,
        security_failure=attempt.security_failure,
        provider_request_id=attempt.provider_request_id,
        finish_reason=attempt.finish_reason,
        output_digest=attempt.output_digest,
        input_tokens=None if attempt.usage is None else attempt.usage.input_tokens,
        output_tokens=None if attempt.usage is None else attempt.usage.output_tokens,
    )

def result_evidence(
    result: GenerationResult, *, region: DeploymentRegion,
    candidate_count: int,
) -> ResultEvidence:
    return ResultEvidence(
        request_id=result.request_id,
        status=result.status.value,
        failure_code=None if result.failure_code is None else result.failure_code.value,
        selected_provider=(
            None if result.selected_provider is None else result.selected_provider.value
        ),
        selected_model_id=result.selected_model_id,
        provider_request_id=result.provider_request_id,
        finish_reason=result.finish_reason,
        input_digest=result.input_digest,
        output_digest=result.output_digest,
        attempts=tuple(
            finished_evidence(attempt, region=region) for attempt in result.attempts
        ),
        candidate_count=candidate_count,
        bounded_summary=None,
    )

class LedgerAttemptObserver:
    def __init__(self, *, owner: StaffingOperations,
                 generation_id: str, route: RoutePolicy,
                 clock: Callable[[], datetime]) -> None:
        self._owner = owner
        self._generation_id = generation_id
        self._route = route
        self._clock = clock

    def started(self, attempt: AttemptStarted) -> None:
        self._owner.record_attempt_started(
            self._generation_id,
            started_evidence(attempt, region=self._route.region),
            recorded_at=self._clock(),
        )

    def finished(self, attempt: AttemptRecord) -> None:
        self._owner.record_attempt_finished(
            self._generation_id,
            finished_evidence(attempt, region=self._route.region),
            recorded_at=self._clock(),
        )
```

Neither mapper may carry raw messages, decoded output, secrets, endpoint, response body, latency, or exception text.

`record_attempt_started`, `record_attempt_finished`, and `commit_generation_result` return `EventCommit`; `interrupt_generation` returns `EventCommit | ConflictReceipt`. `LedgerAttemptObserver` and the generation worker intentionally ignore successful `EventCommit` values. These internal lifecycle results are not `ReceiptResult`, are never converted to HTTP operation records, and cannot be mistaken for a second `suggestion-generate` request.

- [ ] **Step 14: Implement the explicit domain-to-wire projection primitives**

Keep this adapter in the composition root. It accepts only the closed, local-ID domain projection DTOs and constructs new dictionaries field by field:

```python
from collections.abc import Sequence
from typing import TypeVar

from nxt_pilot_ops.staffing.contracts import (
    AssignmentProjection, AttemptFinishedEvidence, AttemptRouteEvidence,
    AttemptStartedEvidence,
    CandidateOperationProjection, CandidateProjection, CommittedReceipt,
    ConflictReceipt, CoverageGap, DateProjection, DuplicateReceipt,
    ExceptionCommittedProjection, ExceptionProjection,
    GenerationCommittedProjection, GenerationProjectionView,
    GenerationRouteEvidence,
    ManagerCommittedProjection, ManagerResponseProjection,
    ReceiptResult, RequestProjection, ResultEvidence,
    RosterCommittedProjection, StaffingError,
)
from nxt_pilot_ops.staffing.ledger import StaffingLedger
from nxt_pilot_ops.staffing.operations import StaffingOperations
from nxt_pilot_ops.staffing.prompt import PROMPT_TEMPLATE_VERSION
from nxt_pilot_ops.staffing.projection import (
    GenerationProjection, STAFFING_SUGGESTION_OUTPUT_SCHEMA,
    canonical_generation_input,
)

class StaffingProjectionError(RuntimeError):
    """A verified domain projection violates the frozen public contract."""

_T = TypeVar("_T")

def _required(value: _T | None, field: str) -> _T:
    if value is None:
        raise StaffingProjectionError(f"missing projection field: {field}")
    return value

def offset_time(value: datetime) -> str:
    offset = value.utcoffset() if value.tzinfo is not None else None
    if offset is None:
        raise StaffingProjectionError("projection timestamp is naive")
    rendered = value.isoformat()
    if offset == timedelta(0):
        if not rendered.endswith(("+00:00", "-00:00")):
            raise StaffingProjectionError("zero-offset timestamp is not canonical")
        return rendered[:-6] + "Z"
    if rendered.endswith(("+00:00", "-00:00")):
        raise StaffingProjectionError("signed zero offset is forbidden")
    return rendered

def utc_time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise StaffingProjectionError("audit timestamp is not UTC")
    return value.isoformat().replace("+00:00", "Z")

def assignment_to_wire(item: AssignmentProjection) -> dict[str, object]:
    return {
        "assignment_id": item.assignment_id, "staff_id": item.staff_id,
        "display_name": item.display_name, "role_code": item.role_code,
        "area_code": item.area_code, "start_at": offset_time(item.start_at),
        "end_at": offset_time(item.end_at),
    }

def exception_to_wire(item: ExceptionProjection) -> dict[str, object]:
    return {
        "exception_id": item.exception_id, "staff_id": item.staff_id,
        "display_name": item.display_name, "kind": item.kind,
        "unavailable_start_at": offset_time(item.unavailable_start),
        "unavailable_end_at": offset_time(item.unavailable_end),
        "note": item.note, "active": True,
    }

def coverage_gap_to_wire(item: CoverageGap) -> dict[str, object]:
    return {
        "role_code": item.role_code, "area_code": item.area_code,
        "start_at": offset_time(item.start_at), "end_at": offset_time(item.end_at),
        "required_count": item.required, "assigned_count": item.actual,
        "rejection_code": "COVERAGE_GAP",
    }

def candidate_operation_to_wire(
    item: CandidateOperationProjection,
) -> dict[str, object]:
    common = {
        "staff_id": _required(item.staff_id, "operation.staff_id"),
        "display_name": _required(item.display_name, "operation.display_name"),
        "role_code": _required(item.role_code, "operation.role_code"),
        "area_code": _required(item.area_code, "operation.area_code"),
        "start_at": offset_time(_required(item.start_at, "operation.start_at")),
        "end_at": offset_time(_required(item.end_at, "operation.end_at")),
    }
    if item.operation == "REMOVE":
        return {
            "operation": "REMOVE",
            "assignment_id": _required(item.assignment_id, "operation.assignment_id"),
            **common,
        }
    if item.operation == "ADD" and item.assignment_id is None:
        return {"operation": "ADD", **common}
    raise StaffingProjectionError("invalid candidate operation projection")

def candidate_to_wire(item: CandidateProjection) -> dict[str, object]:
    return {
        "candidate_index": item.candidate_index,
        "status": "VALID" if item.valid else "REJECTED",
        "operations": [candidate_operation_to_wire(value) for value in item.operations],
        "rationale": item.rationale,
        "operational_warnings": list(item.operational_warnings),
        "coverage_gaps": [coverage_gap_to_wire(value) for value in item.coverage_gaps],
        "schedule_digest": item.materialized_schedule_digest,
        "rejection_codes": list(item.rejection_codes),
    }

def provenance_to_wire(
    started: Sequence[AttemptStartedEvidence],
    finished: Sequence[AttemptFinishedEvidence],
) -> list[dict[str, object]]:
    starts = {item.attempt_index: item for item in started}
    finishes = {item.attempt_index: item for item in finished}
    if len(starts) != len(started) or len(finishes) != len(finished):
        raise StaffingProjectionError("duplicate provider attempt index")
    rows: list[dict[str, object]] = []
    for index in sorted(finishes):
        end = finishes[index]
        begin = starts.get(index)
        if begin is None or begin.request_id != end.request_id or begin.route != end.route:
            raise StaffingProjectionError("provider attempt evidence does not pair")
        rows.append({
            "attempt_index": index, "provider": begin.route.provider,
            "region": begin.route.region, "route_role": begin.route.route_role,
            "route_id": begin.route.route_id, "model_id": begin.route.model_id,
            "failure_code": end.failure_code, "retryable": end.retryable,
            "security_failure": end.security_failure,
            "provider_request_id": end.provider_request_id,
            "finish_reason": end.finish_reason, "input_digest": begin.input_digest,
            "output_digest": end.output_digest, "input_tokens": end.input_tokens,
            "output_tokens": end.output_tokens,
        })
    return rows

def generation_to_wire(
    item: GenerationProjectionView,
    *, manager_response: dict[str, object] | None = None,
) -> dict[str, object]:
    if len(item.basis_revisions) != 3:
        raise StaffingProjectionError("generation basis revisions are incomplete")
    candidates = [candidate_to_wire(value) for value in item.candidates]
    gaps = [coverage_gap_to_wire(gap) for value in item.candidates
            for gap in value.coverage_gaps]
    if item.lifecycle_state in {"RESERVED", "IN_PROGRESS", "RESULT_UNKNOWN"} and candidates:
        raise StaffingProjectionError("nonterminal/unknown generation has candidates")
    if item.lifecycle_state == "SUCCEEDED" and not candidates:
        raise StaffingProjectionError("successful generation has no candidate")
    failure_code = item.failure_code
    if item.lifecycle_state == "RESULT_UNKNOWN":
        if failure_code not in {None, "RESULT_UNKNOWN"}:
            raise StaffingProjectionError("result-unknown failure code is inconsistent")
        failure_code = "RESULT_UNKNOWN"
    elif item.lifecycle_state == "NO_VALID_SUGGESTION":
        if failure_code not in {None, "NO_VALID_SUGGESTION"}:
            raise StaffingProjectionError("no-valid-suggestion failure code is inconsistent")
        failure_code = "NO_VALID_SUGGESTION"
    elif item.lifecycle_state in {
        "UNAVAILABLE", "REFUSED", "INVALID_RESPONSE", "PROVIDER_ERROR",
        "CONFIGURATION_ERROR", "SECURITY_ERROR",
    }:
        failure_code = _required(failure_code, "generation.failure_code")
    elif failure_code is not None:
        raise StaffingProjectionError("unexpected generation failure code")
    roster, exception_set, effective_plan = item.basis_revisions
    return {
        "suggestion_id": item.generation_id, "request_id": item.request_id,
        "operation_id": item.operation_event_id, "state": item.lifecycle_state,
        "basis": {
            "roster": roster, "exception_set": exception_set,
            "effective_plan": effective_plan, "digest": item.basis_digest,
        },
        "retry_of": item.retry_of, "candidates": candidates,
        "coverage_gaps": gaps,
        "provenance": provenance_to_wire(item.started_attempts, item.finished_attempts),
        "failure_code": failure_code,
        "manager_response": manager_response,
    }
```

`_required` is only an invariant assertion over already-validated domain DTOs; its fixed field labels are not returned to the client. It must not accept a dictionary, introspect `__dict__`, call `asdict`, or serialize domain fields wholesale. An unmatched STARTED attempt may make lifecycle `IN_PROGRESS`, but only paired finished attempts enter public provenance.

- [ ] **Step 15: Implement the closed date projection and manager-response join**

```python
def _effective_plan_to_wire(
    revision: int | None, status: str, schedule_digest: str | None,
    schedule: Sequence[AssignmentProjection] | None,
) -> dict[str, object] | None:
    if status == "NO_PLAN":
        if revision not in {None, 0} or schedule is not None:
            raise StaffingProjectionError("NO_PLAN projection is inconsistent")
        return None
    if status not in {"CURRENT", "REVIEW_REQUIRED"}:
        raise StaffingProjectionError("effective plan status is invalid")
    if revision is None or schedule_digest is None or schedule is None:
        raise StaffingProjectionError("effective plan projection is incomplete")
    return {
        "revision": revision, "status": status,
        "schedule_digest": schedule_digest,
        "assignments": [assignment_to_wire(value) for value in schedule],
    }

def manager_response_to_wire(
    item: ManagerResponseProjection,
) -> dict[str, object]:
    if item.decision == "REJECT":
        if (
            item.reason_code not in {"MANUAL_HANDLING", "INSUFFICIENT_CONTEXT", "OTHER"}
            or item.effective_plan_revision is not None
            or item.schedule_digest is not None
            or item.effective_schedule is not None
        ):
            raise StaffingProjectionError("rejected manager response is inconsistent")
        effective = _effective_plan_to_wire(None, "NO_PLAN", None, None)
    elif item.decision in {"ACCEPT", "MODIFY"}:
        expected_reason = "APPROVED" if item.decision == "ACCEPT" else "APPROVED_WITH_CHANGES"
        if item.reason_code != expected_reason:
            raise StaffingProjectionError("manager response reason is inconsistent")
        effective = _effective_plan_to_wire(
            item.effective_plan_revision, "CURRENT",
            item.schedule_digest, item.effective_schedule,
        )
    else:
        raise StaffingProjectionError("manager response decision is invalid")
    return {
        "response_kind": item.decision, "reason_code": item.reason_code,
        "operator": item.operator, "note": item.note,
        "effective_plan": effective,
    }

def project_date_to_wire(
    projection: DateProjection, *, site_id: str,
    deployment_id: str, site_timezone: str,
) -> dict[str, object]:
    if (projection.site_id, projection.deployment_id, projection.site_timezone) != (
        site_id, deployment_id, site_timezone,
    ):
        raise StaffingProjectionError("date projection identity does not match runtime")
    if projection.roster_revision is None:
        if any(value is not None for value in (
            projection.roster_digest, projection.roster_effective_from,
            projection.roster_effective_until,
        )) or any((projection.worker_count, projection.assignment_count,
                   projection.coverage_count, len(projection.assignments),
                   len(projection.exceptions))):
            raise StaffingProjectionError("empty roster projection is inconsistent")
        roster = None
    else:
        roster = {
            "revision": projection.roster_revision,
            "effective_from_local_date": _required(
                projection.roster_effective_from, "roster.effective_from",
            ).isoformat(),
            "effective_until_local_date": (
                None if projection.roster_effective_until is None
                else projection.roster_effective_until.isoformat()
            ),
            "worker_count": projection.worker_count,
            "assignment_count": projection.assignment_count,
            "coverage_rule_count": projection.coverage_count,
        }
        _required(projection.roster_digest, "roster.digest")
    effective = _effective_plan_to_wire(
        projection.effective_plan.revision, projection.effective_plan.status,
        projection.effective_plan.schedule_digest,
        projection.effective_plan_schedule,
    )
    responses: dict[str, ManagerResponseProjection] = {}
    for response in projection.manager_responses:
        if response.generation_id in responses:
            raise StaffingProjectionError("multiple manager responses for one generation")
        responses[response.generation_id] = response
    generations = [
        generation_to_wire(
            value,
            manager_response=(
                None if value.generation_id not in responses
                else manager_response_to_wire(responses.pop(value.generation_id))
            ),
        )
        for value in projection.generations
    ]
    if responses:
        raise StaffingProjectionError("manager response has no generation projection")
    return {
        "service_date": projection.service_date.isoformat(),
        "revisions": {
            "roster": projection.roster_revision or 0,
            "exception_set": projection.exception_set_revision,
            "effective_plan": projection.effective_plan.revision,
        },
        "roster": roster,
        "assignments": [assignment_to_wire(value) for value in projection.assignments],
        "active_exceptions": [exception_to_wire(value) for value in projection.exceptions],
        "effective_plan": effective,
        "generations": generations,
    }
```

- [ ] **Step 16: Implement every closed request-receipt projection**

```python
def _generation_record(item: GenerationProjectionView) -> dict[str, object]:
    wire = generation_to_wire(item)
    common = {
        "suggestion_id": wire["suggestion_id"],
        "service_date": item.service_date.isoformat(),
        "basis": wire["basis"], "retry_of": wire["retry_of"],
        "provenance": wire["provenance"],
    }
    state = wire["state"]
    if state == "RESERVED":
        return {"record_kind": "GENERATION_RESERVED", **common}
    if state == "IN_PROGRESS":
        return {"record_kind": "GENERATION_IN_PROGRESS", **common}
    if state == "RESULT_UNKNOWN":
        return {
            "record_kind": "GENERATION_INTERRUPTED", **common,
            "failure_code": "RESULT_UNKNOWN",
        }
    if state == "SUCCEEDED":
        return {
            "record_kind": "SUGGESTION_ISSUED", **common,
            "candidates": wire["candidates"],
            "coverage_gaps": wire["coverage_gaps"], "failure_code": None,
        }
    if state in {
        "NO_VALID_SUGGESTION", "UNAVAILABLE", "REFUSED", "INVALID_RESPONSE",
        "PROVIDER_ERROR", "CONFIGURATION_ERROR", "SECURITY_ERROR",
    }:
        return {
            "record_kind": "SUGGESTION_UNAVAILABLE", **common,
            "candidates": wire["candidates"],
            "coverage_gaps": wire["coverage_gaps"],
            "failure_code": _required(wire["failure_code"], "generation.failure_code"),
        }
    raise StaffingProjectionError("generation lifecycle is not a wire state")

def _receipt(
    projection: RequestProjection, disposition: str, operation_id: str,
    state: str, record: dict[str, object],
) -> dict[str, object]:
    if disposition not in {"created", "duplicate"}:
        raise StaffingProjectionError("receipt disposition is invalid")
    return {
        "schema": "nxt-staffing/v1", "disposition": disposition,
        "operation_kind": projection.operation_kind,
        "request_id": projection.request_id, "operation_id": operation_id,
        "state": state, "record": record,
    }

def project_request_to_wire(
    projection: RequestProjection, *, disposition: str,
) -> dict[str, object]:
    committed = projection.committed_record
    if committed is None or committed.event_id not in projection.event_ids:
        raise StaffingProjectionError("request projection lacks its committed event")
    utc_time(committed.occurred_at_utc)
    if projection.operation_kind == "roster-import" and isinstance(
        committed, RosterCommittedProjection,
    ):
        if projection.lifecycle_state != "COMMITTED":
            raise StaffingProjectionError("roster lifecycle is not committed")
        record = {
            "record_kind": "ROSTER_IMPORTED",
            "roster_revision": committed.roster_revision,
            "roster_digest": committed.roster_digest,
            "imported_at_utc": utc_time(committed.occurred_at_utc),
            "worker_count": committed.worker_count,
            "assignment_count": committed.assignment_count,
            "coverage_rule_count": committed.coverage_count,
        }
        return _receipt(projection, disposition, committed.event_id, "COMMITTED", record)
    if projection.operation_kind in {
        "exception-record", "exception-cancel", "exception-correct",
    } and isinstance(committed, ExceptionCommittedProjection):
        expected_event = {
            "exception-record": "exception_recorded",
            "exception-cancel": "exception_cancelled",
            "exception-correct": "exception_corrected",
        }[projection.operation_kind]
        if projection.lifecycle_state != "COMMITTED" or committed.event_type != expected_event:
            raise StaffingProjectionError("exception operation/event mismatch")
        if projection.operation_kind == "exception-record":
            exception = _required(
                committed.exception, "exception-record.exception",
            )
            record = {
                "record_kind": "EXCEPTION_RECORDED",
                "exception": exception_to_wire(exception),
                "exception_set_revision": committed.exception_set_revision,
                "recorded_at_utc": utc_time(committed.occurred_at_utc),
            }
        elif projection.operation_kind == "exception-cancel":
            record = {
                "record_kind": "EXCEPTION_CANCELLED",
                "exception_id": committed.exception_id,
                "exception_set_revision": committed.exception_set_revision,
                "cancelled_at_utc": utc_time(committed.occurred_at_utc),
            }
        else:
            replacement = _required(
                committed.replacement, "exception-correct.replacement",
            )
            record = {
                "record_kind": "EXCEPTION_CORRECTED",
                "replaced_exception_id": committed.exception_id,
                "replacement": exception_to_wire(replacement),
                "exception_set_revision": committed.exception_set_revision,
                "corrected_at_utc": utc_time(committed.occurred_at_utc),
            }
        return _receipt(projection, disposition, committed.event_id, "COMMITTED", record)
    if projection.operation_kind == "suggestion-generate" and isinstance(
        committed, GenerationCommittedProjection,
    ):
        generation = projection.generation
        if generation is None or projection.lifecycle_state != generation.lifecycle_state:
            raise StaffingProjectionError("generation request projection is incomplete")
        if (
            committed.event_id != generation.operation_event_id
            or committed.generation_id != generation.generation_id
            or committed.service_date != generation.service_date
            or committed.basis_revisions != generation.basis_revisions
            or committed.basis_digest != generation.basis_digest
            or committed.retry_of != generation.retry_of
            or committed.route_region != generation.route_region
            or committed.route_readiness != generation.route_readiness
            or committed.primary_provider != generation.primary_provider
            or committed.backup_provider != generation.backup_provider
            or projection.request_id != generation.request_id
        ):
            raise StaffingProjectionError("generation reservation/projection mismatch")
        return _receipt(
            projection, disposition, committed.event_id,
            generation.lifecycle_state, _generation_record(generation),
        )
    if projection.operation_kind == "manager-response" and isinstance(
        committed, ManagerCommittedProjection,
    ):
        manager = projection.manager_response
        if projection.lifecycle_state != "COMMITTED" or manager is None:
            raise StaffingProjectionError("manager response projection is incomplete")
        if (
            manager.generation_id, manager.decision, manager.reason_code, manager.note,
            manager.effective_plan_revision, manager.schedule_digest,
            manager.effective_schedule,
        ) != (
            committed.generation_id, committed.decision, committed.reason_code,
            committed.note, committed.effective_plan_revision,
            committed.schedule_digest, committed.effective_schedule,
        ):
            raise StaffingProjectionError("manager record/projection mismatch")
        summary = manager_response_to_wire(manager)
        record = {
            "record_kind": "MANAGER_RESPONSE_COMMITTED",
            "suggestion_id": committed.generation_id,
            "response_kind": committed.decision,
            "reason_code": committed.reason_code, "note": committed.note,
            "effective_plan": summary["effective_plan"],
            "committed_at_utc": utc_time(committed.occurred_at_utc),
        }
        return _receipt(projection, disposition, committed.event_id, "COMMITTED", record)
    raise StaffingProjectionError("operation kind and committed record do not match")
```

- [ ] **Step 17: Implement capability projection, receipt replay, and exact routing**

```python
def generation_capability(configured: ConfiguredGateway) -> dict[str, object]:
    if configured.route is None or configured.readiness is None:
        return {
            "status": "UNAVAILABLE", "region": None,
            "primary_provider": None, "backup_provider": None,
            "failure_code": "PROVIDER_UNCONFIGURED",
        }
    readiness = configured.readiness
    return {
        "status": readiness.status.value,
        "region": readiness.region.value,
        "primary_provider": (
            None if readiness.primary_provider is None
            else readiness.primary_provider.value
        ),
        "backup_provider": (
            None if readiness.backup_provider is None
            else readiness.backup_provider.value
        ),
        "failure_code": (
            None if readiness.failure_code is None else readiness.failure_code.value
        ),
    }

def with_runtime_context(
    projection: DateProjection, configured: ConfiguredGateway,
    *, now: datetime, site_id: str, deployment_id: str, site_timezone: str,
) -> dict[str, Any]:
    server_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        **project_date_to_wire(
            projection, site_id=site_id, deployment_id=deployment_id,
            site_timezone=site_timezone,
        ),
        "schema": "nxt-staffing/v1",
        "environment": "SIMULATION",
        "mode": "STAFFING_ADVISORY_ONLY",
        "server_time_utc": server_time,
        "context": {
            "site_id": site_id,
            "deployment_id": deployment_id,
            "site_timezone": site_timezone,
        },
        "generation_capability": generation_capability(configured),
    }

def parse_service_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise SiteAgentError(
            "staffing_invalid_request", "service date is invalid",
        ) from None
    if parsed.isoformat() != value:
        raise SiteAgentError("staffing_invalid_request", "service date is invalid")
    return parsed

OPERATION_KINDS = frozenset({
    "roster-import", "exception-record", "exception-cancel",
    "exception-correct", "suggestion-generate", "manager-response",
})

def with_matching_path_id(
    payload: dict[str, Any], field: str, path_value: str,
) -> dict[str, Any]:
    detached = deepcopy(payload)
    supplied = detached.get(field)
    if supplied is not None and supplied != path_value:
        raise SiteAgentError(
            "staffing_invalid_request", "request body does not match route",
        )
    detached[field] = path_value
    return detached

CONFLICT_TO_HTTP_ERROR = {
    "IDEMPOTENCY_CONFLICT": (
        "staffing_conflict", "request_id is already bound to different content",
    ),
    "STALE_REQUEST": (
        "staffing_conflict", "staffing request conflicts with current revisions",
    ),
    "INVALID_TRANSITION": (
        "staffing_conflict", "staffing operation is no longer valid",
    ),
    "STALE_SUGGESTION": (
        "staffing_stale_suggestion", "staffing basis has changed",
    ),
}
INVALID_INPUT_CODES = frozenset({
    "staffing_invalid_request", "staffing_invalid_roster", "staffing_unknown_field",
    "staffing_identity_mismatch", "invalid_exception_time",
    "unknown_staff_or_shift",
})

def staffing_error_for_conflict(conflict: ConflictReceipt) -> SiteAgentError:
    code, detail = CONFLICT_TO_HTTP_ERROR[conflict.code]
    return SiteAgentError(code, detail)

def public_domain_error(error: StaffingError) -> SiteAgentError:
    if error.code == "REQUEST_NOT_FOUND":
        return SiteAgentError(
            "staffing_request_not_found", "no committed request in verified evidence",
        )
    if error.code in {"staffing_roster_not_found", "UNKNOWN_GENERATION"}:
        return SiteAgentError("staffing_not_found", "staffing resource was not found")
    if error.code in INVALID_INPUT_CODES:
        return SiteAgentError("staffing_invalid_request", "request body is invalid")
    return SiteAgentError("staffing_unavailable", "staffing evidence is unavailable")

def to_wire_receipt(
    owner: StaffingOperations, result: ReceiptResult,
) -> dict[str, object]:
    if isinstance(result, ConflictReceipt):
        raise staffing_error_for_conflict(result)
    if isinstance(result, CommittedReceipt):
        disposition = "created"
    elif isinstance(result, DuplicateReceipt):
        disposition = "duplicate"
    else:
        raise StaffingProjectionError("unknown domain receipt variant")
    receipt = result.receipt
    projection = owner.request_projection(
        receipt.operation_kind, receipt.request_id,
    )
    if (
        projection.committed_record is None
        or projection.committed_record.event_id != receipt.event_id
    ):
        raise StaffingProjectionError("receipt event does not match replay projection")
    wire = project_request_to_wire(projection, disposition=disposition)
    if (
        wire["operation_kind"] != receipt.operation_kind
        or wire["request_id"] != receipt.request_id
        or wire["operation_id"] != receipt.event_id
    ):
        raise StaffingProjectionError("wire receipt identity does not match domain receipt")
    return wire

class StaffingRouteAdapter:
    def __init__(self, *, owner: StaffingOperations,
                 worker: "BoundedGenerationWorker",
                 configured: ConfiguredGateway, site_id: str,
                 deployment_id: str, site_timezone: str,
                 audit_clock: Callable[[], datetime]) -> None:
        self._owner = owner
        self._worker = worker
        self._configured = configured
        self._site_id = site_id
        self._deployment_id = deployment_id
        self._site_timezone = site_timezone
        self._zone = ZoneInfo(site_timezone)
        self._audit_clock = audit_clock

    def dispatch(self, method: str, path: str,
                 payload: dict[str, Any]) -> dict[str, Any]:
        if not path.startswith("/"):
            raise SiteAgentError("staffing_not_found", "staffing resource was not found")
        parts = tuple(unquote(part) for part in path.split("/")[1:])
        now = self._audit_clock()
        if method == "GET" and parts == ("api", "v1", "staffing"):
            return with_runtime_context(
                self._owner.date_projection(now.astimezone(self._zone).date()),
                self._configured, now=now, site_id=self._site_id,
                deployment_id=self._deployment_id,
                site_timezone=self._site_timezone,
            )
        if method == "GET" and parts[:4] == ("api", "v1", "staffing", "dates") and len(parts) == 5:
            return with_runtime_context(
                self._owner.date_projection(parse_service_date(parts[4])),
                self._configured, now=now, site_id=self._site_id,
                deployment_id=self._deployment_id,
                site_timezone=self._site_timezone,
            )
        if method == "GET" and parts[:4] == ("api", "v1", "staffing", "requests") and len(parts) == 6:
            if parts[4] not in OPERATION_KINDS:
                raise SiteAgentError(
                    "staffing_invalid_request", "operation kind is invalid",
                )
            return project_request_to_wire(
                self._owner.request_projection(parts[4], parts[5]),
                disposition="duplicate",
            )
        if method == "POST" and parts == ("api", "v1", "staffing", "roster-imports"):
            return to_wire_receipt(
                self._owner, self._owner.import_roster(payload, recorded_at=now)
            )
        if method == "POST" and parts == ("api", "v1", "staffing", "exceptions"):
            return to_wire_receipt(
                self._owner, self._owner.record_exception(payload, recorded_at=now)
            )
        if method == "POST" and len(parts) == 6 and parts[:4] == ("api", "v1", "staffing", "exceptions"):
            body = with_matching_path_id(payload, "exception_id", parts[4])
            operation = {
                "cancel": self._owner.cancel_exception,
                "correct": self._owner.correct_exception,
            }.get(parts[5])
            if operation is not None:
                return to_wire_receipt(
                    self._owner, operation(body, recorded_at=now)
                )
        if method == "POST" and parts == ("api", "v1", "staffing", "suggestions"):
            return self._worker.submit(payload)
        if method == "POST" and len(parts) == 6 and parts[:4] == ("api", "v1", "staffing", "suggestions"):
            decision = {"accept": "ACCEPT", "modify": "MODIFY", "reject": "REJECT"}.get(parts[5])
            if decision is not None:
                if payload.get("kind") != decision:
                    raise SiteAgentError(
                        "staffing_invalid_request", "response kind does not match path",
                    )
                return to_wire_receipt(
                    self._owner,
                    self._owner.commit_manager_response(
                        payload, suggestion_id=parts[4], recorded_at=now,
                    ),
                )
        raise SiteAgentError("staffing_not_found", "staffing resource was not found")

class StaffingApiOperations:
    def __init__(self, *, router: StaffingRouteAdapter,
                 worker: "BoundedGenerationWorker") -> None:
        self._router = router
        self._worker = worker
        self._closed = False

    def route(self, method: str, path: str,
              payload: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise SiteAgentError("staffing_unavailable", "staffing operations are closed")
        try:
            return self._router.dispatch(method, path, deepcopy(payload))
        except SiteAgentError:
            raise
        except StaffingError as error:
            raise public_domain_error(error) from None
        except Exception:
            raise SiteAgentError(
                "staffing_unavailable", "staffing evidence is unavailable",
            ) from None

    def close(self) -> None:
        self._closed = True
        self._worker.close()

def build_staffing_operations(state_root: Path, *, site_id: str,
                              deployment_id: str, site_timezone: str,
                              volatile_out: Path,
                              settings: StaffingProviderSettings,
                              audit_clock: Callable[[], datetime],
                              monotonic: Callable[[], float],
                              nonce_factory: Callable[[], bytes]
                              ) -> StaffingApiOperations:
    root = resolve_staffing_root(
        state_root, site_id=site_id, deployment_id=deployment_id,
        volatile_out=volatile_out,
    )
    ledger = StaffingLedger(root, site_id=site_id, deployment_id=deployment_id)
    owner = StaffingOperations(
        ledger, site_id=site_id, deployment_id=deployment_id,
        site_timezone=site_timezone,
    )
    owner.recover_interrupted_generations(recorded_at=audit_clock())
    configured = configured_gateway(settings, monotonic=monotonic)
    worker = BoundedGenerationWorker(
        owner=owner, configured=configured, settings=settings,
        audit_clock=audit_clock, nonce_factory=nonce_factory,
    )
    router = StaffingRouteAdapter(
        owner=owner, worker=worker, configured=configured,
        site_id=site_id, deployment_id=deployment_id,
        site_timezone=site_timezone, audit_clock=audit_clock,
    )
    return StaffingApiOperations(
        router=router, worker=worker,
    )
```

`nonce_factory` is an explicit deterministic-test seam, not a deployment setting and never comes from CLI/environment input. Unit tests may inject fixed values; the production continuous-service caller must pass exactly `lambda: secrets.token_bytes(32)` for every newly admitted reservation. No time-, counter-, request-, configuration-, or `random`-derived nonce is allowed. Spy tests require the production factory to request 32 bytes on every call, return distinct bytes across two new reservations, and keep both the nonce and its local reuse-detection digest out of provider transports, stdout, stderr, and public projections.

`with_matching_path_id` is used only for cancel/correct: it copies the payload, inserts a missing exception ID, and raises `staffing_invalid_request` if a present value disagrees. Manager response bodies remain the exact closed Task 1 wire shape; the adapter passes the decoded route value separately to `commit_manager_response(payload, suggestion_id=parts[4], recorded_at=now)`. `to_wire_receipt(owner, result)` uses `staffing_error_for_conflict` for every `ConflictReceipt`; for committed/duplicate results it calls `project_request_to_wire(owner.request_projection(receipt.operation_kind, receipt.request_id), disposition=disposition)` where `disposition` is derived only from the result variant, then validates the Task 1 triple before returning. Neither `project_date_to_wire` nor `project_request_to_wire` passes a domain mapping through wholesale. Add a table-driven test proving all four conflict codes and every listed domain error produce the literal Task 1 code/status, and an unknown/internal/integrity exception produces only generic unavailable. At construction, open the owner, append interruptions for all nonterminals, then start the worker. No mapping uses `str(error)` or an internal detail.

- [ ] **Step 18: Define the bounded worker interface and admission record**

The queue stores identifiers only. The worker reloads the immutable domain projection with `owner.generation_work(generation_id)` after reservation; it never keeps a pre-reservation basis that could race an exception or roster commit.

```python
@dataclass(frozen=True, slots=True)
class GenerationWorkItem:
    request_id: str
    operation_id: str
    generation_id: str

class BoundedGenerationWorker:
    MAX_ACTIVE = 1
    MAX_WAITING = 4

    def __init__(self, *, owner: StaffingOperations,
                 configured: ConfiguredGateway,
                 settings: StaffingProviderSettings,
                 audit_clock: Callable[[], datetime],
                 nonce_factory: Callable[[], bytes]) -> None:
        self._owner = owner
        self._configured = configured
        self._settings = settings
        self._audit_clock = audit_clock
        self._nonce_factory = nonce_factory
        self._condition = threading.Condition()
        self._waiting: deque[GenerationWorkItem] = deque()
        self._active: GenerationWorkItem | None = None
        self._closing = False
        self._failed_closed = False
        self._thread = threading.Thread(
            target=self._run, name="staffing-generation", daemon=True,
        )
        self._thread.start()

    def submit(self, request: Mapping[str, object]) -> dict[str, object]:
        return self._admit_and_reserve(request)

    def _mark_failed_closed(self) -> None:
        with self._condition:
            self._failed_closed = True
            self._condition.notify_all()

    def close(self) -> None:
        self._stop_and_join()
```

Do not add a generic executor whose queue is unbounded.

- [ ] **Step 19: Implement duplicate-first admission and exact capacity**

Use one admission mutex/condition, one active reservation slot, and a deque of maximum four waiting reservations. Implement the method called by `submit()`; under admission lock, call the domain's verified read-only probe before checking capacity and reserve/enqueue atomically with respect to local capacity:

```python
def _admit_and_reserve(
    self, request: Mapping[str, object],
) -> dict[str, object]:
    with self._condition:
        if self._failed_closed or self._closing:
            raise SiteAgentError(
                "staffing_unavailable", "staffing generation is unavailable",
            )
        prior = self._owner.probe_request("suggestion-generate", request)
        if isinstance(prior, DuplicateReceipt):
            return to_wire_receipt(self._owner, prior)
        if isinstance(prior, ConflictReceipt):
            raise staffing_error_for_conflict(prior)
        route_evidence = generation_route_evidence(self._configured, self._settings)
        if route_evidence is None:
            raise SiteAgentError(
                "staffing_unavailable", "staffing generation is unavailable",
            )
        occupied = (1 if self._active is not None else 0) + len(self._waiting)
        if occupied >= self.MAX_ACTIVE + self.MAX_WAITING:
            raise SiteAgentError("staffing_busy", "generation capacity is full")
        result = self._owner.reserve_generation(
            request, alias_nonce=self._nonce_factory(),
            route_evidence=route_evidence,
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
            language=self._settings.language, recorded_at=self._audit_clock(),
        )
        receipt = to_wire_receipt(self._owner, result)
        if receipt["disposition"] == "duplicate":
            return receipt
        item = GenerationWorkItem(
            request_id=receipt["request_id"],
            operation_id=receipt["operation_id"],
            generation_id=receipt["record"]["suggestion_id"],
        )
        if self._active is None:
            self._active = item
        else:
            self._waiting.append(item)
        self._condition.notify()
        return receipt
```

`probe_request(operation_kind, payload)` verifies replay and compares the canonical payload digest, returning duplicate/conflict/none without writing.

The domain reservation builder repeats idempotency/CAS to close the probe/write race, then performs basis materialization, alias projection, digest calculation, and `generation_reserved` append under its ledger lock. Enqueue only a newly created reservation; a concurrent duplicate returned by reserve also returns immediately without another queue item. Place the new item active/waiting, notify, and return the durable receipt. Any reserve failure leaves the queue unchanged.

- [ ] **Step 20: Implement worker execution outside all admission/domain locks**

Take one immutable `GenerationWorkItem`, release the condition, and implement the worker method below outside every admission/domain lock:

```python
from types import MappingProxyType

def candidate_count_hint(output: object | None) -> int:
    try:
        if output is None:
            return 0
        if type(output) not in (dict, MappingProxyType):
            return 0
        candidates = output.get("candidates")
        if type(candidates) not in (list, tuple) or len(candidates) > 2:
            return 0
        return len(candidates)
    except Exception:
        # MappingProxyType may proxy a hostile custom mapping. The hint is not
        # authoritative and must be total; never include exception text.
        return 0

def _execute(self, item: GenerationWorkItem) -> None:
    try:
        work = self._owner.generation_work(item.generation_id)
        if self._is_unconfigured_route():
            self._commit_unconfigured(item, work)
            return
        canonical_input = canonical_generation_input(
            work.basis_snapshot, work.provider_payload, PROMPT_TEMPLATE_VERSION,
        )
        messages = tuple(
            GenerationMessage(role=MessageRole(row["role"]), content=row["content"])
            for row in canonical_input["messages"]
        )
        generation_request = GenerationRequest(
            request_id=item.generation_id,
            template_version=PROMPT_TEMPLATE_VERSION,
            messages=messages,
            output_schema=STAFFING_SUGGESTION_OUTPUT_SCHEMA,
            max_output_tokens=2048,
            deadline_budget_s=(
                15.0 if self._configured.route.region is DeploymentRegion.CN else 20.0
            ),
        )
    except Exception:
        self._mark_failed_closed()
        return
    if generation_request.canonical_input_digest != work.input_digest:
        self._mark_failed_closed()
        return
    observer = LedgerAttemptObserver(
        owner=self._owner, generation_id=item.generation_id,
        route=self._configured.route, clock=self._audit_clock,
    )
    try:
        result = self._configured.gateway.generate(
            generation_request, self._configured.route, observer=observer,
        )
    except AttemptObserverError:
        try:
            self._owner.interrupt_generation(
                item.generation_id, recorded_at=self._audit_clock(),
            )
        except Exception:
            self._mark_failed_closed()
        return
    except Exception:
        self._mark_failed_closed()
        return
    try:
        candidate_count = candidate_count_hint(result.output)
        self._owner.commit_generation_result(
            item.generation_id,
            result_evidence(
                result, region=self._configured.route.region,
                candidate_count=candidate_count,
            ),
            result.output, recorded_at=self._audit_clock(),
        )
    except Exception:
        self._mark_failed_closed()
```

The worker reaches this block only when `configured.route` is non-null. Wrap `generation_work`, provider-wire primitive/prompt/request construction, the digest equality check, and the zero-attempt configuration terminal in the same fail-closed boundary: any unexpected construction or ledger/integrity exception performs no outbound call and stops queue promotion. `canonical_generation_input` receives both the frozen basis and provider payload so its user JSON includes the service date/IANA timezone and renders every model-facing time in the site zone with its explicit offset; composition must not serialize `provider_payload` directly through the generic UTC ledger serializer. `STAFFING_SUGGESTION_OUTPUT_SCHEMA` is the immutable portable structural mapping exported by the domain projection module; composition never copies or relaxes it. On the three built-in adapter path, structural-schema extra/forbidden fields, wrong types, missing fields, and candidate-index enum failures are rejected before this worker receives output and commit as gateway `INVALID_RESPONSE/SCHEMA_MISMATCH`. Operation allowlisting/case normalization/branch coherence, candidate/operation counts, text/code bounds, timestamp lexical rules, duplicate/non-contiguous indexes, and alias/set failures intentionally pass the common provider schema and are classified by `commit_generation_result` with their frozen lowercase domain codes. `candidate_count_hint` is a total, non-authoritative defense for direct/post-gateway corruption or a custom successful adapter result: invalid arrays or any ordinary exception from a `MappingProxyType`-wrapped custom backing mapping yield zero with no exception text; it is only copied into evidence and never authorizes a candidate. The domain owner performs one authoritative bounded detach, verifies digest and hint against that fresh tree, and uses only the same tree for decoding and terminal construction, so any earlier hint read that observed different state fails evidence validation rather than binding A's digest to B's candidate. `BaseException` is not swallowed. `interrupt_generation` may append exactly one `generation_interrupted` only for a nonterminal generation after `AttemptObserverError`; it never replaces a terminal outcome. `_mark_failed_closed` stores no exception text and makes future generation admission raise generic `staffing_unavailable`. `result_evidence` copies only frozen gateway fields into the domain `ResultEvidence`, always sets `bounded_summary=None`, and never carries messages, raw body, headers, secrets, exception text, or endpoint. Reacquire the condition only to clear active capacity and choose the next item; when failed closed, `_run` must not promote another waiting item.

- [ ] **Step 21: Implement bounded shutdown**

On `close()`, under the condition set `_closing`, detach all waiting work into a tuple, and notify. In the normal healthy state, release the condition before calling `owner.interrupt_generation` once for each detached generation. In failed-closed state, attempt no more ledger writes: leave active/waiting reservations nonterminal for startup recovery. Allow the already-active call to finish and join the daemon worker with a 25-second ceiling. `_run` checks both `_closing` and `_failed_closed` before promoting a waiting item, and its per-item `finally` clears `_active`/notifies even when mapping, observer, or commit fails. Never begin the next queued call after close or failed-close begins.

```python
def _run(self) -> None:
    while True:
        with self._condition:
            self._condition.wait_for(
                lambda: self._active is not None or self._closing or self._failed_closed,
            )
            if self._active is None and (self._closing or self._failed_closed):
                return
            item = self._active
        assert item is not None
        try:
            self._execute(item)
        finally:
            with self._condition:
                if self._active == item:
                    self._active = None
                if not self._closing and not self._failed_closed and self._waiting:
                    self._active = self._waiting.popleft()
                self._condition.notify_all()

def _stop_and_join(self) -> None:
    with self._condition:
        self._closing = True
        detached = tuple(self._waiting)
        self._waiting.clear()
        failed_closed = self._failed_closed
        self._condition.notify_all()
    if not failed_closed:
        for item in detached:
            try:
                self._owner.interrupt_generation(
                    item.generation_id, recorded_at=self._audit_clock(),
                )
            except Exception:
                self._mark_failed_closed()
                break
    self._thread.join(timeout=25.0)
    if self._thread.is_alive():
        self._mark_failed_closed()
```

- [ ] **Step 22: Implement missing-configuration terminal and response-loss recovery**

With region and primary model present but the primary key absent, reserve normally. When readiness is `UNAVAILABLE/PROVIDER_UNCONFIGURED`, before building messages commit this zero-attempt result without transport:

```python
def _is_unconfigured_route(self) -> bool:
    readiness = self._configured.readiness
    return bool(
        readiness is not None
        and readiness.status is RouteReadinessStatus.UNAVAILABLE
        and readiness.failure_code is FailureCode.PROVIDER_UNCONFIGURED
    )

def _commit_unconfigured(
    self, item: GenerationWorkItem, work: GenerationProjection,
) -> None:
    evidence = ResultEvidence(
        request_id=item.generation_id,
        status="CONFIGURATION_ERROR",
        failure_code="PROVIDER_UNCONFIGURED",
        selected_provider=None,
        selected_model_id=None,
        provider_request_id=None,
        finish_reason=None,
        input_digest=work.input_digest,
        output_digest=None,
        attempts=(),
        candidate_count=0,
        bounded_summary=None,
    )
    try:
        self._owner.commit_generation_result(
            item.generation_id, evidence, None, recorded_at=self._audit_clock(),
        )
    except Exception:
        self._mark_failed_closed()
```

With region or primary model absent, reject generation pre-reservation as `staffing_unavailable` and leave ledger bytes unchanged. Inject HTTP response loss after terminal fsync and recover the receipt through request GET. Assert logs/exceptions/projections exclude keys, prompt bodies, raw provider bodies, alias maps, and latency. Run the focused suite.

- [ ] **Step 23: Run GREEN and commit composition**

```bash
git add simulation/scripts/staffing_operations.py \
  simulation/tests/site_agent/test_staffing_composition.py
git commit -m "feat(staffing): compose bounded regional generation"
```

### Task 4: Wire staffing into the continuous local service without coupling lifecycles

**Files:**
- Modify: `simulation/scripts/course_collection_execution_v4_service.py:40-57,954-1061`
- Modify: `simulation/tests/site_agent/test_continuous_collection_execution_service.py:865-905,2750-2846`
- Modify: `simulation/tests/site_agent/test_architecture.py:16-151,204-287`
- Create: `simulation/docs/staffing_advisory_pilot_runbook.md`

**Interfaces:**
- Consumes: optional deployment CLI/config, stable state root, current Site Agent and Continuous V4 lifecycles.
- Produces: one integrated local service where staffing failure cannot prevent or lock existing capabilities.

- [ ] **Step 1: Add failing CLI and lifecycle-order tests**

Add parser expectations for:

```text
--staffing-state-root PATH
--staffing-region {CN,GLOBAL}
--staffing-language {zh-CN,en}   # default zh-CN
--kimi-model MODEL_ID
--openai-model MODEL_ID
--anthropic-model MODEL_ID
```

Add the arguments at the existing parser block with no environment-derived defaults:

```python
parser.add_argument("--staffing-state-root", type=Path)
parser.add_argument("--staffing-region", choices=("CN", "GLOBAL"))
parser.add_argument("--staffing-language", choices=("zh-CN", "en"), default="zh-CN")
parser.add_argument("--kimi-model")
parser.add_argument("--openai-model")
parser.add_argument("--anthropic-model")
```

Only `load_provider_settings(os.environ, region=args.staffing_region, language=args.staffing_language, kimi_model=args.kimi_model, openai_model=args.openai_model, anthropic_model=args.anthropic_model)` reads `MOONSHOT_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY`; CLI help/startup JSON never renders their values. Supplying region/model flags without `--staffing-state-root` is accepted but inert so legacy startup remains compatible.

Using spies, assert startup order is runtime, Site Agent, staffing construction/recovery, API server, continuous driver. Shutdown is server, staffing, continuous runtime, Site Agent. Staffing is not returned from `ContinuousCollectionExecutionRuntime.api_callbacks()`.

Use this exact expected event sequence in the existing CLI test:

```python
assert events == [
    "runtime.start", "service.launch", "staffing.build", "runtime.callbacks",
    "server.init", "server.start", "runtime.start_driver", "wait",
    "server.shutdown", "staffing.close", "runtime.close", "service.stop",
]
```

- [ ] **Step 2: Add failing isolation and survival tests**

Import a roster into a stable root, run `--initialize` with two different `--out` roots, use Site Agent reset, and prove both runs see the same staffing revision while their run evidence remains separate. Corrupt staffing ledger/anchor: base health, planning, task ops, and collection execution still start/read; staffing routes return generic unavailable. Missing provider config still permits roster/exception/read.

- [ ] **Step 3: Run service tests and verify RED**

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/site_agent/test_continuous_collection_execution_service.py \
  tests/site_agent/test_architecture.py
```

- [ ] **Step 4: Add optional construction and safe-failure callback**

After `runtime.start()` and `SiteAgentService.launch()`, if state root is present, resolve/open `StaffingApiOperations`. Import stdlib `secrets` in this composition script and construct staffing with `nonce_factory=lambda: secrets.token_bytes(32)`; this production argument is not caller-configurable. Catch only construction/integrity failures, discard original details from outward paths, and install a callback that always raises `SiteAgentError("staffing_unavailable", "staffing evidence is unavailable")`. If no state root, pass no callback.

Merge independently:

```python
callbacks = runtime.api_callbacks()
if staffing_callback is not None:
    callbacks["staffing_operations"] = staffing_callback
server = SiteAgentApiServer(
    service,
    port=args.port,
    console_dir=None if args.api_only else args.console,
    **callbacks,
)
```

- [ ] **Step 5: Implement exact shutdown and stdout redaction**

Stop server first, then staffing, runtime, service. The startup JSON may report `staffing: enabled|unavailable|disabled` and readiness category but never root contents beyond operator-supplied path, provider key, prompt, or model response. Test with sentinel key strings captured from stdout/stderr.

- [ ] **Step 6: Extend architecture guards for the unique composition point**

Keep `nxt_model_gateway` banned from `nxt_site_agent` and every package. Prove only `simulation/scripts/staffing_operations.py` contains both `nxt_model_gateway` and `nxt_pilot_ops.staffing`. Give this script a dedicated guard allowing composition-only `os`/`secrets` but rejecting direct `http.client`, `ssl`, provider SDKs, Edge/simulator/robot execution imports, command tokens, and endpoint literals. Existing V4 script guard remains unchanged.

- [ ] **Step 7: Write the pilot runbook and run GREEN**

Document initial roster import, stable-path ownership/permissions, CN/GLOBAL environment/CLI examples that reference shell variables such as `$OPENAI_API_KEY` without assigning secret values, degraded/manual behavior, request recovery, result unknown/new retry, shutdown, backup/retention, and the loopback/no-auth limitation. Run Step 3 plus `tests/site_agent`.

- [ ] **Step 8: Commit service wiring**

```bash
git add simulation/scripts/course_collection_execution_v4_service.py \
  simulation/tests/site_agent simulation/docs/staffing_advisory_pilot_runbook.md
git commit -m "feat(staffing): wire advisory service lifecycle"
```

### Task 5: Implement the strict Console client and dependency-free CSV normalization

**Files:**
- Create: `apps/site-agent-console/lib/staffing.ts`
- Create: `apps/site-agent-console/lib/staffing-csv.ts`
- Create: `apps/site-agent-console/public/staffing-roster-template.csv`
- Create: `apps/site-agent-console/tests/staffing-contract.test.ts`
- Create: `apps/site-agent-console/tests/staffing-csv.test.ts`

**Interfaces:**
- Consumes: Task 1 schema/examples and one local CSV file.
- Produces: strict TypeScript API types/parsers/client and exact normalized roster request; no domain validation, external URL, dependency, or browser persistence.

- [ ] **Step 1: Add failing cross-language success-contract tests**

Load every exchange from the seven `nxt-staffing-exchanges/v1` files with `readFileSync`; pass `exchange.body.data` to `parseStaffingSnapshot` or `parseStaffingReceipt` according to the presence of `operation_kind`. Assert foreign container/envelope schema, extra top-level/nested keys, invalid operation state, missing revision, malformed candidate/gap, provider alias fields, and wrong environment/mode throw `ManagerApiError`. From `manager-response.json` parse `date-after-manager-response` and assert the matching generation retains the closed response summary after a fresh read:

```typescript
const refreshed = parseStaffingSnapshot(exchange("date-after-manager-response").body.data);
expect(refreshed.generations[0].manager_response).toEqual({
  response_kind: "ACCEPT", reason_code: "APPROVED", operator: "经理甲",
  note: "采用一号方案",
  effective_plan: expect.objectContaining({ revision: 1, status: "CURRENT" }),
});
```

Verify client paths are URL-encoded and root bootstrap calls exact `/api/v1/staffing`.

- [ ] **Step 2: Add failing TypeScript validation for `errors.json`**

Load every error exchange and send it through `createStaffingClient` with a scripted fetch. Assert the exact status/code pairs frozen in Task 1 and the fixed Manager API envelope:

```typescript
expect(errors.schema).toBe("nxt-staffing-errors/v1");
for (const exchange of errors.exchanges) {
  expect(Object.keys(exchange).sort()).toEqual([
    "body", "http_status", "method", "name", "route_template",
  ]);
  const client = createStaffingClient(async () =>
    new Response(JSON.stringify(exchange.body), {
      status: exchange.http_status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  await expect(client.current()).rejects.toMatchObject({
    status: exchange.http_status,
    code: exchange.body.error.code,
  });
}
```

Also mutate schema, disclaimer, error code, and status independently and assert the parser fails closed rather than remapping them.

- [ ] **Step 3: Freeze the CSV header and row grammar**

Use one exact header:

```csv
record_type,timezone,effective_from_local_date,effective_until_local_date,staff_id,display_name,skill_codes,eligibility,max_daily_minutes,weekday,role_code,area_code,start_local,end_local,required_skill_codes,minimum_staff
```

Allowed rows and populated fields:

```text
META: timezone, effective_from_local_date, optional effective_until_local_date; exactly one row
WORKER: staff_id, display_name, skill_codes (`|`), eligibility (`ROLE@AREA|ROLE@AREA`), max_daily_minutes
AVAILABILITY: staff_id, weekday 0..6, start_local, end_local
ASSIGNMENT_RULE: role_code, area_code, required_skill_codes (`|`)
REGULAR_ASSIGNMENT: staff_id, weekday, role_code, area_code, start_local, end_local
COVERAGE: weekday, role_code, area_code, start_local, end_local, minimum_staff
```

Every field not listed for that row type must be empty. `operator` comes from the volatile panel label; `source_ref` is the selected filename.

- [ ] **Step 4: Add failing RFC 4180, UTF-8, BOM, and formula tests**

Cover quoted commas/newlines, doubled quotes, CRLF/LF, final newline/no final newline, one leading BOM, misplaced BOM, fatal invalid UTF-8, duplicate/missing/unknown header, duplicate worker, unknown row type, absent required row class, extra populated cell, list parsing, raw >512 KiB, normalized JSON >1 MiB, every cell whose left-trimmed nonempty content starts `=`, `+`, `-`, or `@`, and a formula-prefixed selected filename before it can become `source_ref`.

- [ ] **Step 5: Run client/CSV tests and verify RED**

```bash
cd apps/site-agent-console
npm test -- tests/staffing-contract.test.ts tests/staffing-csv.test.ts
```

- [ ] **Step 6: Implement closed runtime receipt parsing**

Port the exact Task 1 unions into `staffing.ts`. First validate common receipt keys, then switch on `operation_kind`; for suggestion generation, switch again on `state` and require the corresponding `record_kind`. Exhaustiveness must be compiler-checked:

```typescript
const unreachable = (value: never): never => {
  throw new ManagerApiError(200, {
    code: "invalid_staffing_receipt",
    detail: `unsupported receipt branch: ${String(value)}`,
  });
};
```

Do not cast an arbitrary record object to the union after checking only common fields.

- [ ] **Step 7: Implement typed snapshot parsing**

Validate every field the UI reads: revisions, assignments, active exceptions, effective plan, generation capability, generation states, candidate patch discriminators, gaps, provenance, nullable manager-response summary, and all array bounds. A non-null response must be one closed object: ACCEPT/APPROVED and MODIFY/APPROVED_WITH_CHANGES require a non-null effective plan, while REJECT plus one of its three reason codes requires null. Reject unknown fields in every request, receipt, snapshot, envelope, and nested contract object; this mirrors Task 1's closed Python schema exactly.

- [ ] **Step 8: Implement the same-origin client and exact path union**

In `staffing.ts`, export closed request/projection/receipt types, exact runtime validators, `newStaffingRequestId(kind)` using `crypto.randomUUID()`, and:

```typescript
type StaffingMutationPath =
  | "/roster-imports"
  | "/exceptions"
  | `/exceptions/${string}/cancel`
  | `/exceptions/${string}/correct`
  | "/suggestions"
  | `/suggestions/${string}/accept`
  | `/suggestions/${string}/modify`
  | `/suggestions/${string}/reject`;

interface StaffingClient {
  current(): Promise<StaffingDateSnapshot>;
  date(serviceDate: string): Promise<StaffingDateSnapshot>;
  submit(path: StaffingMutationPath, body: StaffingRequestBody): Promise<StaffingReceipt>;
  lookup(operationKind: OperationKind, requestId: string): Promise<StaffingReceipt>;
}
```

Use same-origin fetch, `cache: "no-store"`, 8-second abort, v0 envelope validation, typed errors, and no configurable base URL in production calls.

- [ ] **Step 9: Implement fatal UTF-8/BOM handling and RFC 4180 tokenization**

Decode bytes with `new TextDecoder("utf-8", { fatal: true, ignoreBOM: true })` so a BOM remains observable, strip exactly one first code-point BOM, reject any later BOM, and parse RFC 4180 with an explicit `FIELD | QUOTED | AFTER_QUOTE` state enum. Emit a row only on CRLF/LF outside quotes and reject a quote in an unquoted field or non-delimiter after a closing quote.

- [ ] **Step 10: Implement row grammar and exact roster normalization**

Validate header/row grammar, split list columns on `|` with duplicate rejection, resolve worker/rule references, and build the exact `RosterImportRequest` arrays from Task 1. Compute the serialized UTF-8 body length with `new TextEncoder().encode(JSON.stringify(body)).byteLength` and reject over 1 MiB before fetch. Browser validation checks only syntax/references it can know and does not claim eligibility, skills, DST, or coverage correctness.

- [ ] **Step 11: Add the downloadable template, run GREEN, and verify no dependency drift**

The template contains one META row and illustrative rows for every type with fictional data. Run:

```bash
cd apps/site-agent-console
npm run typecheck
npm test -- tests/staffing-contract.test.ts tests/staffing-csv.test.ts tests/boundaries.test.ts
git diff -- package.json package-lock.json
```

Expected: no production dependency change.

- [ ] **Step 12: Commit client and CSV support**

```bash
git add apps/site-agent-console/lib/staffing.ts \
  apps/site-agent-console/lib/staffing-csv.ts \
  apps/site-agent-console/public/staffing-roster-template.csv \
  apps/site-agent-console/tests/staffing-contract.test.ts \
  apps/site-agent-console/tests/staffing-csv.test.ts
git commit -m "feat(console): parse staffing roster and api"
```

### Task 6: Implement independent staffing request, polling, and recovery state

**Files:**
- Create: `apps/site-agent-console/lib/staffing-state.ts`
- Create: `apps/site-agent-console/lib/staffing-actions.ts`
- Create: `apps/site-agent-console/tests/staffing-state.test.ts`

**Interfaces:**
- Consumes: strict client, caller-generated request IDs, current server snapshot, and volatile manager label.
- Produces: independent read/write/active-generation view, safe same-ID recovery, explicit retry-of flow, and no scheduler coupling.

- [ ] **Step 1: Freeze the view/write state machine in failing tests**

Use:

```typescript
interface StaffingMutationAttempt {
  operationKind: OperationKind;
  requestId: string;
  path: StaffingMutationPath;
  body: StaffingRequestBody;
}
type StaffingWriteState =
  | ({ status: "in_flight" } & StaffingMutationAttempt)
  | ({ status: "unknown"; detail: string; recovering: boolean;
       replayedAfterNotFound: boolean } & StaffingMutationAttempt)
  | ({ status: "busy"; detail: string } & StaffingMutationAttempt)
  | { status: "committed"; requestId: string; receipt: StaffingReceipt; savedButStale: boolean }
  | { status: "rejected"; operationKind: OperationKind; requestId: string;
      code: string; detail: string };

interface StaffingView {
  snapshot: StaffingDateSnapshot | null;
  read: {
    status: "idle" | "loading" | "ready" | "error";
    stale: boolean;
    detail: string | null;
  };
  write: StaffingWriteState | null;
  activeGenerationRequestId: string | null;
  activeGeneration: GenerationProjection | null;
}

interface ExceptionDraft {
  staff_id: string;
  kind: "LEAVE" | "LATE" | "EARLY_DEPARTURE" | "UNAVAILABLE";
  time_local: string | null;
  note: string | null;
}
type RosterImportDraft = Omit<
  RosterImportRequest,
  "request_id" | "operator" | "expected_roster_revision" |
  "site_id" | "deployment_id"
>;
```

- [ ] **Step 2: Add failing tests for mutation ambiguity and recovery**

Only an ambiguous transport abort/timeout or mutation 5xx for which a durable commit cannot be ruled out makes the write `unknown`; fixed 400/409/429 responses are definite rejections/busy states. Recovery GET with any receipt commits the write; a `RESULT_UNKNOWN` receipt therefore becomes the durable terminal active-generation state, never another transport-unknown error. Explicit `staffing_request_not_found` permits one replay of the identical path/body/ID; any other lookup failure remains unknown. A committed mutation whose refresh fails is `savedButStale`, not unknown. Unmount suppresses late publish.

- [ ] **Step 3: Add failing BUSY and generation-state tests**

429 `staffing_busy` is definitely uncommitted and retains same ID/body for an explicit retry. RESERVED/IN_PROGRESS causes only request GET polling at 1 second and never repeats POST. Terminal `RESULT_UNKNOWN` stops polling and offers explicit new-ID `retry_of`; terminal success/failure refreshes snapshot. Start a new controller from the refreshed manager-response fixture with no prior in-memory write state and assert `activeGeneration.manager_response` remains committed and renderable. Normal snapshot polling is 5 seconds and never overlaps an active read/write/request poll.

- [ ] **Step 4: Run controller tests and verify RED**

```bash
cd apps/site-agent-console
npm test -- tests/staffing-state.test.ts
```

- [ ] **Step 5: Implement controller generation tags, timers, and safe recovery**

Follow the existing planning controller’s generation-tagged publish and mutation semantics, but do not consume task scheduler health/capabilities. Expose:

```typescript
interface StaffingController {
  view(): StaffingView;
  subscribe(listener: (view: StaffingView) => void): () => void;
  start(): void;
  stop(): void;
  refresh(): Promise<void>;
  submit(operationKind: OperationKind, path: StaffingMutationPath,
         body: StaffingRequestBody): Promise<StaffingReceipt>;
  recover(): Promise<StaffingReceipt>;
  retryBusy(): Promise<StaffingReceipt>;
  retryUnknownGeneration(): Promise<StaffingReceipt>;
  acknowledgeWrite(): void;
}
```

`retryUnknownGeneration()` requires the active terminal `RESULT_UNKNOWN` receipt, creates a fresh suggestion request ID, copies the current server service date/revisions, and sets `retry_of` to that receipt's `suggestion_id`/domain `generation_id`; it never reuses the old request ID. `acknowledgeWrite()` only clears a committed/rejected banner and performs no network request.

- [ ] **Step 6: Implement mutation submission and same-ID recovery**

Keep the exact `path`, operation kind, request ID, and a deep-cloned/frozen `body` in `StaffingMutationAttempt` so later form edits cannot change recovery bytes. On ambiguous fetch failure, publish `unknown`. `recover()` first calls request GET; a receipt commits. Only explicit `staffing_request_not_found` may replay the byte-equivalent body once, tracked by `replayedAfterNotFound`; a second not-found remains unknown. A mutation receipt commits before calling `refresh()`, so a failed refresh produces `savedButStale: true`.

- [ ] **Step 7: Implement non-overlapping polling and explicit generation retry**

Use one timer registry and one generation counter. The 5-second snapshot poll does not start while a read, write, or 1-second request poll is active. RESERVED/IN_PROGRESS schedules request GET only. Every terminal state stops request polling. RESULT_UNKNOWN stores the terminal receipt until the manager invokes `retryUnknownGeneration`; no timer or lifecycle hook retries it. On every successful snapshot refresh, select the matching generation by `activeGenerationRequestId` without stripping its nullable `manager_response`; if there is no in-memory ID, retain the most recent generation carrying a committed manager response so reload does not erase the durable outcome.

- [ ] **Step 8: Implement closed action builders without business decisions**

Expose this complete UI-facing interface:

```typescript
interface StaffingActions {
  refresh(): Promise<void>;
  importRoster(draft: RosterImportDraft): Promise<StaffingReceipt>;
  recordException(draft: ExceptionDraft): Promise<StaffingReceipt>;
  cancelException(exceptionId: string, note: string | null): Promise<StaffingReceipt>;
  correctException(exceptionId: string,
                   replacement: ExceptionReplacementInput): Promise<StaffingReceipt>;
  generateSuggestion(): Promise<StaffingReceipt>;
  acceptSuggestion(suggestionId: string, candidateIndex: 1 | 2,
                   note: string | null): Promise<StaffingReceipt>;
  modifySuggestion(suggestionId: string, candidateIndex: 1 | 2,
                   operations: ManagerPatchOperation[],
                   note: string | null): Promise<StaffingReceipt>;
  rejectSuggestion(suggestionId: string,
                   reasonCode: "MANUAL_HANDLING" | "INSUFFICIENT_CONTEXT" | "OTHER",
                   note: string | null
                   ): Promise<StaffingReceipt>;
  recover(): Promise<StaffingReceipt>;
  retryBusy(): Promise<StaffingReceipt>;
  retryUnknownGeneration(): Promise<StaffingReceipt>;
  acknowledgeWrite(): void;
}
```

`staffing-actions.ts` maps the CSV draft, exception form, cancel/correct, generate, accept, edited modify patch, reject, recovery, and retry to Task 1 bodies using the current server revisions. Roster import gets `site_id` and `deployment_id` only from `snapshot.context`, never from CSV/browser configuration. Accept, modify, and reject copy the caller's optional `note` into the closed manager-response body; the note remains in local evidence/API/UI and never enters provider input. It generates the request ID immediately before submit and reads a nonblank volatile manager label through an injected getter. It refuses to fabricate a missing label/snapshot and does not validate staffing eligibility or coverage. Pin representative bodies in tests:

```typescript
expect(actions.recordException({
  staff_id: "staff-1", kind: "LATE", time_local: "09:15", note: null,
})).resolves.toMatchObject({ operation_kind: "exception-record" });
expect(submitted.body).toMatchObject({
  schema: "nxt-staffing-exception/v1",
  service_date: snapshot.service_date,
  expected_roster_revision: snapshot.revisions.roster,
  expected_exception_set_revision: snapshot.revisions.exception_set,
  operator: "经理甲",
});

await actions.acceptSuggestion("generation-1", 1, "采用一号方案");
expect(submitted.body).toMatchObject({
  kind: "ACCEPT", candidate_index: 1,
  edited_operations: null, reason_code: "APPROVED", note: "采用一号方案",
});
const edited: ManagerPatchOperation[] = [
  { operation: "REMOVE", assignment_id: "assignment-1" },
];
await actions.modifySuggestion("generation-1", 1, edited, "缩短晚班");
expect(submitted.body).toMatchObject({
  kind: "MODIFY", edited_operations: edited,
  reason_code: "APPROVED_WITH_CHANGES", note: "缩短晚班",
});
await actions.rejectSuggestion("generation-1", "MANUAL_HANDLING", null);
expect(submitted.body).toMatchObject({
  kind: "REJECT", candidate_index: null, edited_operations: null,
  reason_code: "MANUAL_HANDLING", note: null,
});
```

- [ ] **Step 9: Run GREEN and commit**

```bash
git add apps/site-agent-console/lib/staffing-state.ts \
  apps/site-agent-console/lib/staffing-actions.ts \
  apps/site-agent-console/tests/staffing-state.test.ts
git commit -m "feat(console): recover staffing advisory operations"
```

### Task 7: Build the compact staffing panel and human-response workflow

**Files:**
- Create: `apps/site-agent-console/components/StaffingPanel.tsx`
- Create: `apps/site-agent-console/components/staffing/RosterImport.tsx`
- Create: `apps/site-agent-console/components/staffing/ExceptionForm.tsx`
- Create: `apps/site-agent-console/components/staffing/GenerationStatus.tsx`
- Create: `apps/site-agent-console/components/staffing/CandidateCards.tsx`
- Create: `apps/site-agent-console/components/staffing/CandidateEditor.tsx`
- Modify: `apps/site-agent-console/components/PilotOperations.tsx:1-36`
- Modify: `apps/site-agent-console/app/globals.css:740-789`
- Create: `apps/site-agent-console/tests/staffing-panel.test.tsx`
- Create: `apps/site-agent-console/tests/staffing-interaction.test.tsx`

**Interfaces:**
- Consumes: independent controller/actions and server-validated snapshot/receipts.
- Produces: one compact manager surface with no hidden persistence, provider controls, chat, or execution action.

- [ ] **Step 1: Add failing base and in-progress render tests**

Render no roster, normal day/zero exception, RESERVED, and IN_PROGRESS. Assert no-roster exposes the template/import control, zero-exception does not demand data entry, and both generation states disable duplicate generation while keeping local reads and exception entry available.

- [ ] **Step 2: Add failing terminal-state render tests**

Render one candidate, two candidates, one rejected candidate filtered from actions, no valid suggestion with validator gaps, provider unavailable, configuration error, RESULT_UNKNOWN, REVIEW_REQUIRED, stale failure, and the parsed `date-after-manager-response` refresh fixture. Assert the refreshed generation renders its committed response kind, reason, note, operator, and effective-plan revision without depending on in-memory write state. Assert every candidate view contains exact copy `AI 建议，需经理确认` plus provider/model provenance; no view says automatic schedule, verified identity, labor-law compliant, or executed.

```typescript
expect(screen.getAllByText("AI 建议，需经理确认")).toHaveLength(validCandidates);
expect(screen.getByRole("region", { name: "覆盖缺口" })).toBeVisible();
expect(screen.getByRole("region", { name: "运营提示" })).toBeVisible();
```

- [ ] **Step 3: Add failing minimal-form interaction tests**

With the panel-level manager label already entered, record LEAVE/UNAVAILABLE using employee → type → submit, and LATE/EARLY using employee → type → time → submit; note remains optional. Count the interactions and assert every exception path stays within five selections/clicks. Assert the exception fieldset contains only employee, type, conditional time, optional note, submit. After durable exception commit, generating a suggestion is a separate explicit click.

- [ ] **Step 4: Add failing CSV and recovery tests**

Assert the template link/download, parser errors before POST, server validation errors, saved-but-stale banner, transport-unknown recovery, BUSY same-ID retry, and RESULT_UNKNOWN fresh-ID retry. Capture calls and prove RESERVED/IN_PROGRESS polling invokes only GET request recovery, never a second suggestion POST.

- [ ] **Step 5: Add failing manager-response tests**

Assert accept sends the exact candidate index; modify strips display fields and sends only `ManagerPatchOperation`; reject requires one of the three rejection reason codes. For accept, modify, and reject, assert an optional response note of at most 500 Unicode scalars is copied only into the local manager-response body, 501 scalars is rejected before fetch, and the note never appears in provider material. Stale suggestion and durable committed receipts render distinctly. Coverage gaps render separately from operational warnings. Render all strings as React text; never use `dangerouslySetInnerHTML` or Markdown parsing.

- [ ] **Step 6: Run component tests and verify RED**

```bash
cd apps/site-agent-console
npm test -- tests/staffing-panel.test.tsx tests/staffing-interaction.test.tsx
```

- [ ] **Step 7: Implement the panel and one volatile attribution input**

Use these closed component boundaries:

```typescript
interface StaffingPanelProps {
  client?: StaffingClient;
}
interface CandidateEditorProps {
  candidate: CandidateProjection;
  disabled: boolean;
  onCancel(): void;
  onSubmit(operations: ManagerPatchOperation[], note: string | null): Promise<void>;
}
```

`StaffingPanel` creates one client/controller/actions bundle with `useRef`, subscribes in an effect, starts it once, and stops/unsubscribes on cleanup. It holds `managerLabel` only in component state and renders it above roster/exception/generation sections with “仅作归属记录，非身份认证”. Empty label disables mutations with local explanatory text; reads remain available. The optional client exists only for tests; production constructs `createStaffingClient(fetch)` and has no URL/provider configuration prop.

- [ ] **Step 8: Implement roster import**

`RosterImport` reads an `ArrayBuffer`, calls the strict parser, previews counts/timezone/effective date, and submits a complete replacement revision only after confirmation:

```typescript
const bytes = await file.arrayBuffer();
const draft = parseStaffingRosterCsv(bytes, file.name);
setPreview({
  workers: draft.workers.length,
  assignments: draft.regular_assignments.length,
  timezone: draft.site_timezone,
  effectiveFrom: draft.effective_from_local_date,
});
await actions.importRoster(draft);
```

- [ ] **Step 9: Implement the minimal exception form**

`ExceptionForm` derives staff choices and current service date only from snapshot; time appears only for LATE/EARLY. Its fieldset contains employee, type, conditional time, optional note, and submit—nothing else. It never asks for request IDs, revisions, provider, prompt, or model.

- [ ] **Step 10: Implement generation status and candidate cards**

Display every operation state and explicit manual fallback. Candidate cards list REMOVE/ADD adjustments using server-restored local IDs/names, rationale, warnings, and separate gaps. Accept submits the exact candidate index. Rejected candidates remain auditable but have no accept/modify controls.

- [ ] **Step 11: Implement structured modify and reject**

Modify opens structured rows for local assignment/staff ID, role, area, start, and end. The response workflow also exposes one optional note field shared by accept/modify/reject; normalize blank input to null, enforce the 500-scalar browser convenience limit with `Array.from(note).length`, submit it only to the same-origin manager-response route, and never include it in later provider input. Strip read-only display names when producing the closed request union:

```typescript
const editable: ManagerPatchOperation[] = candidate.operations.map((operation) =>
  operation.operation === "REMOVE"
    ? { operation: "REMOVE", assignment_id: operation.assignment_id }
    : { operation: "ADD", staff_id: operation.staff_id,
        role_code: operation.role_code, area_code: operation.area_code,
        start_at: operation.start_at, end_at: operation.end_at },
);
```

Reject requires a reason code; its note remains optional. No button invokes Planning, task ops, collection execution, notification, or robot actions.

- [ ] **Step 12: Mount independently and add accessible responsive styling**

Import `StaffingPanel` and mount `<div className="dispatch-shell"><StaffingPanel /></div>` as the first child of the existing fragment in `PilotOperations`, before the Planning shell. Do not pass scheduler health, task capabilities, simulation clock, or runtime locks. In `globals.css:740-789`, append staffing-prefixed classes using existing tokens, labeled controls, keyboard-visible focus, `aria-live` for operation state, candidate headings, and compact desktop/mobile stacking.

- [ ] **Step 13: Run GREEN and commit**

```bash
git add apps/site-agent-console/components/StaffingPanel.tsx \
  apps/site-agent-console/components/staffing \
  apps/site-agent-console/components/PilotOperations.tsx \
  apps/site-agent-console/app/globals.css \
  apps/site-agent-console/tests/staffing-panel.test.tsx \
  apps/site-agent-console/tests/staffing-interaction.test.tsx
git commit -m "feat(console): add staffing exception advisory panel"
```

### Task 8: Close cross-layer guards, privacy, outage, and full verification

**Files:**
- Modify: `apps/site-agent-console/tests/boundaries.test.ts:1-111`
- Modify: `apps/site-agent-console/README.md:90-202`
- Modify: `simulation/tests/conftest.py`
- Modify: `simulation/tests/course_monitoring/test_collection_execution_acceptance.py`
- Modify: `simulation/tests/site_agent/test_continuous_collection_execution_service.py`
- Modify: `simulation/tests/site_agent/test_architecture.py:16-151,204-287`
- Modify: `.agent/context/package-map.md:18-40`
- Modify: `.agent/context/deployment.md:96-116,236-252`
- Modify: `.agent/context/product.md:24-58,109-114`
- Modify: `.agent/workflows/testing.md:15-82,96-121,159-177`
- Modify: `docs/CI.md:15-31,97-153,305-332`
- Modify: `.github/workflows/verification.yml:123-192,401-447`
- Modify: `simulation/tests/course_monitoring/fixtures/collection-execution-normal-loop-v3.json`
- Modify: `simulation/tests/fixtures/continuous-collection-v4/two-task-active.json`
- Modify: `simulation/docs/collection_execution_v3_runbook.md:205-209,436-487`

**Interfaces:**
- Consumes: the complete gateway + staffing domain + integration + Console path.
- Produces: mechanically enforced dependency/privacy/non-execution boundaries and recorded end-to-end verification.

- [ ] **Step 1: Tighten Console architecture guards**

Allow only same-origin `/api/v1/staffing` and subpaths in addition to existing APIs. Retain external-URL, hidden persistence, command vocabulary, and exact production dependency guards. Add direct assertions:

```typescript
expect(staffingProductionSource).not.toMatch(/localStorage|sessionStorage|indexedDB/);
expect(staffingProductionSource).not.toMatch(/https?:\/\//);
expect(staffingProductionSource).not.toMatch(/robot|dispatch|execute|notify/i);
expect(productionDependencies()).toEqual(expectedProductionDependencies);
```

- [ ] **Step 2: Tighten Python owner/import guards**

Prove only `simulation/scripts/staffing_operations.py` imports both owners, Site Agent still contains none of its existing LLM/provider/generative patterns, no package imports the gateway, and the staffing script has no direct HTTP/robot/Edge execution surface:

```python
dual_owner_importers = {
    path for path in python_files()
    if "nxt_model_gateway" in path.read_text()
    and "nxt_pilot_ops.staffing" in path.read_text()
}
assert dual_owner_importers == {ROOT / "scripts/staffing_operations.py"}
```

- [ ] **Step 3: Add integrated privacy tests**

Use sentinel staff ID `PRIVATE-STAFF-9`, name `PRIVATE-NAME-9`, note `PRIVATE-NOTE-9`, source ref `PRIVATE-SOURCE-9.csv`, and three distinct sentinel API keys. Capture fake transport requests, ledger, API JSON, stdout/stderr, exception text, and Console fixtures. Assert provider requests contain only aliases/codes/times/constraints and omit the local basis, roster, exception-set, and effective-plan digest values; assert keys/raw provider body/reasoning appear nowhere, and real identity/free text appears only in the authorized local ledger/API/UI surfaces, never provider material. Confirm the same local digests remain present where required for reservation/replay integrity without crossing the transport seam. Spy on the production `secrets.token_bytes` seam across two new reservations: both calls request exactly 32 bytes, return distinct nonce values, the values themselves are never persisted, and neither they nor their local nonce digests appear in transport, stdout/stderr, public API, or Console fixtures.

- [ ] **Step 4: Add provider outage and hang isolation tests**

Simulate all configured providers unavailable and one blocked call. Assert an exception remains committed, manual UI remains usable, health/existing APIs respond before the provider event is released, and CN makes exactly one Kimi attempt with zero OpenAI/Anthropic calls.

- [ ] **Step 5: Add integrated concurrency tests**

Race two identical suggestion POSTs and assert one reservation/provider call plus duplicate receipt; then race accept versus new exception, two manager responses, and correction versus cancellation. Assert one valid atomic winner, stable conflict/stale codes, and no half response/plan/correction event.

- [ ] **Step 6: Add crash and shutdown matrix tests**

Parameterize reservation fsync, attempt-start fsync, outbound-before-finish, attempt-finish fsync, terminal fsync, HTTP response loss, and shutdown with four waiting items. Assert no old-ID resend, waiting shutdown items become `RESULT_UNKNOWN` without transport, and terminal-fsynced outcomes recover through request GET.

- [ ] **Step 7: Regenerate and freeze the final cross-cutting V3 witnesses**

Register an explicit, default-false `--regenerate-v3-witnesses` pytest option in root `tests/conftest.py`; normal tests and CI never pass it and remain read-only. The updater may target only the two exact regular JSON files and the exact regular runbook file, must reject symlinks or missing/duplicate runbook markers, and must replace through same-directory temporary files plus `os.replace` rather than truncate a witness in place.

The existing `normal_loop` session fixture remains the owner of the normal-loop witness: build a fresh `CourseCollectionExecutionDemo(..., initialize=True, wall_clock=WallClock())`, call `run(advance=2)`, validate the complete snapshot through the existing wire/relations/invariant checks, and only under the explicit option write `json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"`. It must then perform the same byte-for-byte assertion and twenty-case matrix. The two-task owner remains the existing real loopback HTTP workflow with fixed `WallClock`, two Planning chains, and the frozen `advance_until` terminal/nonterminal/ACTIVE condition; after all state and relation checks, the explicit option atomically writes the complete canonical response data and the test still performs its byte-for-byte assertion. Neither updater may replace only `engine_digest` or bypass an owning workflow.

Add unique generated-block markers around the runbook's `Expected fixed identity and result` table. Derive and atomically rewrite only that block from the freshly generated normal-loop snapshot, receipt, binding, execution, quantities, and digests; replace the old hand-maintained regeneration-date sentence with the exact command below. Keep separately dated loopback/tree-hash evidence explicitly historical unless that owning service evidence was rerun. Add a default-read-only `test_runbook_witness_facts_match_normal_loop_fixture`; it must parse the marked block, fail on missing/duplicate markers, and prove every generated fact equals the complete fixture-derived values.

After every Python/composition/API/UI source change is stable, run the authoritative whole-artifact updater (loopback bind permission is required):

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  --regenerate-v3-witnesses \
  tests/course_monitoring/test_collection_execution_acceptance.py::test_frozen_acceptance_case \
  tests/course_monitoring/test_collection_execution_acceptance.py::test_runbook_witness_facts_match_normal_loop_fixture \
  tests/site_agent/test_continuous_collection_execution_service.py::test_two_task_active_fixture_regenerates_from_exact_http_data
```

Immediately prove the regenerated artifacts in ordinary read-only mode; no ignore, deselection, or update flag is permitted:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/course_monitoring/test_collection_execution_acceptance.py \
  tests/site_agent/test_continuous_collection_execution_service.py::test_two_task_active_fixture_regenerates_from_exact_http_data
```

Any later review fix touching `nxt_*` or a script covered by the V3 engine fingerprint returns to this step before final verification.

- [ ] **Step 8: Run the focused Python integration matrix**

```bash
cd simulation
uv sync --locked --all-extras
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/model_gateway \
  tests/pilot_ops/test_staffing_wire_contract.py \
  tests/site_agent/test_staffing_api.py \
  tests/site_agent/test_staffing_composition.py \
  tests/site_agent/test_continuous_collection_execution_service.py \
  tests/site_agent/test_architecture.py
```

- [ ] **Step 9: Run the complete Console verification**

```bash
cd apps/site-agent-console
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm run smoke
npm audit --omit=dev
```

- [ ] **Step 10: Update operator/developer docs and CI commands**

Update the listed README/context/testing/CI files with the exact stable root, loopback/no-auth limitation, CLI flags, environment variable names only, 1+4 queue, request recovery/new retry, CSV limits, focused commands, witness-update/read-only verification commands, and ownership graph. Add the Task 1/2/3 focused tests plus Console typecheck/test/build to the existing verification workflow blocks; preserve current triggers and permissions. In the existing isolated-wheel job's `python -I` block, retain the top-level `nxt_pilot_ops` import and add this exact persistent oracle after the `shipped` loop; mirror the same behavior in `.agent/workflows/testing.md` and `docs/CI.md`:

```python
staffing = import_module("nxt_pilot_ops.staffing")
staffing_operations = import_module("nxt_pilot_ops.staffing.operations")
assert staffing_operations.__all__ == ("StaffingOperations",)
assert staffing_operations.StaffingOperations.__module__ == (
    "nxt_pilot_ops.staffing.operations"
)
assert "StaffingOperations" not in getattr(staffing, "__all__", ())
assert not hasattr(staffing, "StaffingOperations")
```

- [ ] **Step 11: Run full Python, package, config, and hygiene verification**

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider
uv run --no-sync python -B scripts/validate_configs.py
build_dir="$(mktemp -d)"
uv build --out-dir "$build_dir"
uv run --no-sync python -B ../.github/scripts/verify_python_distribution.py "$build_dir"
cd ..
git diff --check
```

- [ ] **Step 12: Scan for secrets, unfinished work, forbidden dependency, and route drift**

```bash
rg -n "TO[D]O|T[B]D|FIX[M]E|Not[I]mplemented" simulation/nxt_model_gateway \
  simulation/nxt_pilot_ops/staffing simulation/scripts/staffing_operations.py \
  apps/site-agent-console/{lib,components}
rg -n "MOONSHOT_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|Authorization: Bearer|x-api-key" \
  simulation apps/site-agent-console
rg -n "localStorage|sessionStorage|indexedDB|WebSocket|EventSource|sendBeacon" \
  apps/site-agent-console/{app,components,lib}
```

Review matches manually: environment variable names and header-name fixture assertions are allowed; values, logging, storage, and external browser networking are not.

- [ ] **Step 13: Self-review all Review Focus items and request independent review**

Map every Review Focus bullet to named tests and current-head results. Use `superpowers:requesting-code-review`; ask the reviewer to trace one exception from CSV/roster through generation/acceptance/restart and prove there is no execution path or secret/private-data leak. Fix confirmed findings with regression tests first.

- [ ] **Step 14: Commit final guard/docs corrections if needed**

```bash
git add apps/site-agent-console \
  simulation/tests/conftest.py \
  simulation/tests/course_monitoring/test_collection_execution_acceptance.py \
  simulation/tests/course_monitoring/fixtures/collection-execution-normal-loop-v3.json \
  simulation/tests/site_agent \
  simulation/tests/fixtures/continuous-collection-v4/two-task-active.json \
  simulation/docs/collection_execution_v3_runbook.md \
  .agent docs/CI.md .github/workflows/verification.yml
git commit -m "test(staffing): close advisory integration verification"
git diff --exit-code HEAD --
test -z "$(git ls-files --others --exclude-standard)"
```

Skip this commit when the tree is clean.
