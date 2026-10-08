import {
  API_SCHEMA,
  DISCLAIMER,
  ManagerApiError,
  type FetchLike,
} from "./api";

export const STAFFING_SCHEMA = "nxt-staffing/v1" as const;

export type OperationKind =
  | "roster-import"
  | "exception-record"
  | "exception-cancel"
  | "exception-correct"
  | "suggestion-generate"
  | "manager-response";

export type OperationState =
  | "COMMITTED"
  | "RESERVED"
  | "IN_PROGRESS"
  | "RESULT_UNKNOWN"
  | "SUCCEEDED"
  | "NO_VALID_SUGGESTION"
  | "UNAVAILABLE"
  | "REFUSED"
  | "INVALID_RESPONSE"
  | "PROVIDER_ERROR"
  | "CONFIGURATION_ERROR"
  | "SECURITY_ERROR";

export interface RevisionVector {
  roster: number;
  exception_set: number;
  effective_plan: number;
}

interface Basis extends RevisionVector {
  digest: string;
}

interface EligibilityInput {
  role_code: string;
  area_code: string;
}

type Weekday = 0 | 1 | 2 | 3 | 4 | 5 | 6;

interface WorkerInput {
  staff_id: string;
  display_name: string;
  skill_codes: string[];
  eligibility: EligibilityInput[];
  max_daily_minutes: number;
}

interface AvailabilityInput {
  staff_id: string;
  weekday: Weekday;
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
  weekday: Weekday;
  role_code: string;
  area_code: string;
  start_local: string;
  end_local: string;
}

interface CoverageInput {
  weekday: Weekday;
  role_code: string;
  area_code: string;
  start_local: string;
  end_local: string;
  minimum_staff: number;
}

export interface RosterImportRequest {
  schema: "nxt-staffing-roster-import/v1";
  request_id: string;
  operator: string;
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

type ExceptionKind = "LEAVE" | "LATE" | "EARLY_DEPARTURE" | "UNAVAILABLE";

interface ExceptionRecordCommon {
  schema: "nxt-staffing-exception/v1";
  request_id: string;
  operator: string;
  service_date: string;
  expected_roster_revision: number;
  expected_exception_set_revision: number;
  staff_id: string;
  note: string | null;
}

export type ExceptionRecordRequest =
  | (ExceptionRecordCommon & { kind: "LEAVE" | "UNAVAILABLE"; time_local: null })
  | (ExceptionRecordCommon & { kind: "LATE" | "EARLY_DEPARTURE"; time_local: string });

export interface ExceptionCancelRequest {
  schema: "nxt-staffing-exception-cancel/v1";
  request_id: string;
  operator: string;
  expected_exception_set_revision: number;
  note: string | null;
}

export type ExceptionReplacementInput =
  | { kind: "LEAVE" | "UNAVAILABLE"; time_local: null; note: string | null }
  | { kind: "LATE" | "EARLY_DEPARTURE"; time_local: string; note: string | null };

export interface ExceptionCorrectRequest {
  schema: "nxt-staffing-exception-correct/v1";
  request_id: string;
  operator: string;
  expected_exception_set_revision: number;
  replacement: ExceptionReplacementInput;
}

export interface SuggestionGenerateRequest {
  schema: "nxt-staffing-suggestion-generate/v1";
  request_id: string;
  operator: string;
  service_date: string;
  expected_revisions: RevisionVector;
  retry_of: string | null;
}

type ManagerPatchRemove = { operation: "REMOVE"; assignment_id: string };
type ManagerPatchAdd = {
  operation: "ADD";
  staff_id: string;
  role_code: string;
  area_code: string;
  start_at: string;
  end_at: string;
};
export type ManagerPatchOperation = ManagerPatchRemove | ManagerPatchAdd;

interface ManagerResponseCommon {
  schema: "nxt-staffing-manager-response/v1";
  request_id: string;
  operator: string;
  expected_revisions: RevisionVector;
  note: string | null;
}

export type ManagerAcceptRequest = ManagerResponseCommon & {
  kind: "ACCEPT";
  candidate_index: 1 | 2;
  edited_operations: null;
  reason_code: "APPROVED";
};

export type ManagerModifyRequest = ManagerResponseCommon & {
  kind: "MODIFY";
  candidate_index: 1 | 2;
  edited_operations: ManagerPatchOperation[];
  reason_code: "APPROVED_WITH_CHANGES";
};

export type ManagerRejectRequest = ManagerResponseCommon & {
  kind: "REJECT";
  candidate_index: null;
  edited_operations: null;
  reason_code: "MANUAL_HANDLING" | "INSUFFICIENT_CONTEXT" | "OTHER";
};

export type ManagerResponseRequest =
  | ManagerAcceptRequest
  | ManagerModifyRequest
  | ManagerRejectRequest;

export type StaffingRequestBody =
  | RosterImportRequest
  | ExceptionRecordRequest
  | ExceptionCancelRequest
  | ExceptionCorrectRequest
  | SuggestionGenerateRequest
  | ManagerResponseRequest;

export type StaffingMutation =
  | { operationKind: "roster-import"; body: RosterImportRequest }
  | { operationKind: "exception-record"; body: ExceptionRecordRequest }
  | { operationKind: "exception-cancel"; targetId: string; body: ExceptionCancelRequest }
  | { operationKind: "exception-correct"; targetId: string; body: ExceptionCorrectRequest }
  | { operationKind: "suggestion-generate"; body: SuggestionGenerateRequest }
  | { operationKind: "manager-response"; targetId: string; body: ManagerResponseRequest };

export interface AssignmentProjection {
  assignment_id: string;
  staff_id: string;
  display_name: string;
  role_code: string;
  area_code: string;
  start_at: string;
  end_at: string;
}

export interface ExceptionProjection {
  exception_id: string;
  staff_id: string;
  display_name: string;
  kind: ExceptionKind;
  unavailable_start_at: string;
  unavailable_end_at: string;
  note: string | null;
  active: boolean;
}

export interface CoverageGap {
  role_code: string;
  area_code: string;
  start_at: string;
  end_at: string;
  required_count: number;
  assigned_count: number;
  rejection_code: "COVERAGE_GAP";
}

type CandidateRemoveProjection = {
  operation: "REMOVE";
  assignment_id: string;
  staff_id: string;
  display_name: string;
  role_code: string;
  area_code: string;
  start_at: string;
  end_at: string;
};

type CandidateAddProjection = {
  operation: "ADD";
  staff_id: string;
  display_name: string;
  role_code: string;
  area_code: string;
  start_at: string;
  end_at: string;
};

export type CandidateOperationProjection = CandidateRemoveProjection | CandidateAddProjection;

type RejectionCode =
  | "UNKNOWN_ROLE_AREA"
  | "INELIGIBLE_ROLE_AREA"
  | "MISSING_REQUIRED_SKILL"
  | "INVALID_INTERVAL"
  | "INVALID_MINUTE_PRECISION"
  | "OFFSET_TIMEZONE_MISMATCH"
  | "OUTSIDE_SERVICE_DATE"
  | "OUTSIDE_AVAILABILITY"
  | "OVERLAPS_EXCEPTION"
  | "OVERLAPPING_ASSIGNMENTS"
  | "MAX_DAILY_MINUTES_EXCEEDED"
  | "COVERAGE_GAP"
  | "PROMPT_TEMPLATE_VERSION_MISMATCH";

type NonCoverageRejectionCode = Exclude<RejectionCode, "COVERAGE_GAP">;

export type CandidateActionability = "CURRENT" | "EXPIRED";

interface CandidateCommon {
  candidate_index: 1 | 2;
  operations: CandidateOperationProjection[];
  rationale: string;
  operational_warnings: string[];
  /** Provider text with reserved aliases replaced by local labels; the raw
   * `rationale` and `operational_warnings` stay beside it for audit. */
  rationale_local: string;
  operational_warnings_local: string[];
  /** UTC instant after which every shift this candidate adds or removes has
   * ended; `actionability` is the service's own reading of its clock. */
  action_window_end_at: string;
  actionability: CandidateActionability;
}

type CandidateValidProjection = CandidateCommon & {
  status: "VALID";
  coverage_gaps: [];
  schedule_digest: string;
  rejection_codes: [];
};

type CandidateRejectedCoverageProjection = CandidateCommon & {
  status: "REJECTED";
  coverage_gaps: [CoverageGap, ...CoverageGap[]];
  schedule_digest: null;
  rejection_codes: ["COVERAGE_GAP"];
};

type CandidateRejectedOtherProjection = CandidateCommon & {
  status: "REJECTED";
  coverage_gaps: [];
  schedule_digest: null;
  rejection_codes: [NonCoverageRejectionCode, ...NonCoverageRejectionCode[]];
};

type CandidateRejectedProjection = CandidateRejectedCoverageProjection | CandidateRejectedOtherProjection;

export type CandidateProjection =
  | CandidateValidProjection
  | CandidateRejectedProjection;

type FallbackFailureCode =
  | "DNS_FAILURE"
  | "CONNECT_TIMEOUT"
  | "CONNECT_FAILED"
  | "READ_TIMEOUT"
  | "CONNECTION_INTERRUPTED"
  | "HTTP_TIMEOUT"
  | "RATE_LIMITED"
  | "PROVIDER_UNAVAILABLE";

type NonFallbackFailureCode =
  | "DEADLINE_EXHAUSTED"
  | "PROVIDER_REFUSED"
  | "MALFORMED_PROVIDER_RESPONSE"
  | "SCHEMA_MISMATCH"
  | "AUTHENTICATION_FAILED"
  | "PERMISSION_DENIED"
  | "MODEL_NOT_FOUND"
  | "INVALID_PROVIDER_REQUEST"
  | "PROVIDER_CLIENT_ERROR";

type SecurityFailureCode =
  | "RESPONSE_TOO_LARGE"
  | "UNSUPPORTED_CONTENT_ENCODING"
  | "REDIRECT_REFUSED"
  | "ENDPOINT_NOT_ALLOWED"
  | "TLS_VERIFICATION_FAILED";

type GatewayFailureCode = FallbackFailureCode | NonFallbackFailureCode | SecurityFailureCode;

type InvalidResponseFailureCode =
  | "MALFORMED_PROVIDER_RESPONSE"
  | "SCHEMA_MISMATCH"
  | "invalid_provider_shape"
  | "provider_sensitive_key"
  | "invalid_provider_timestamp"
  | "invalid_candidate_set";

type ConfigurationFailureCode =
  | "INPUT_TOO_LARGE"
  | "AUTHENTICATION_FAILED"
  | "PERMISSION_DENIED"
  | "MODEL_NOT_FOUND"
  | "INVALID_PROVIDER_REQUEST"
  | "PROVIDER_UNCONFIGURED";

type UnavailableFailureCode =
  | FallbackFailureCode
  | "DEADLINE_EXHAUSTED"
  | "BACKUP_UNCONFIGURED";

type GenerationFailureCode =
  | "NO_VALID_SUGGESTION"
  | GatewayFailureCode
  | "BACKUP_UNCONFIGURED"
  | "invalid_provider_shape"
  | "provider_sensitive_key"
  | "invalid_provider_timestamp"
  | "invalid_candidate_set"
  | "INPUT_TOO_LARGE"
  | "PROVIDER_UNCONFIGURED";

export interface ProviderProvenance {
  attempt_index: 0 | 1;
  provider: "KIMI" | "OPENAI" | "ANTHROPIC";
  region: "CN" | "GLOBAL";
  route_role: "PRIMARY" | "BACKUP";
  route_id: "cn-kimi-v1" | "global-openai-v1" | "global-anthropic-v1";
  model_id: string;
  failure_code: GatewayFailureCode | null;
  retryable: boolean;
  security_failure: boolean;
  provider_request_id: string | null;
  finish_reason: string | null;
  input_digest: string;
  output_digest: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
}

export interface EffectivePlanProjection {
  revision: number;
  status: "CURRENT" | "REVIEW_REQUIRED";
  schedule_digest: string;
  assignments: AssignmentProjection[];
}

type CommittedEffectivePlanProjection = EffectivePlanProjection & { status: "CURRENT" };

export type ManagerResponseSummary =
  | {
      response_kind: "ACCEPT";
      reason_code: "APPROVED";
      operator: string;
      note: string | null;
      effective_plan: CommittedEffectivePlanProjection;
    }
  | {
      response_kind: "MODIFY";
      reason_code: "APPROVED_WITH_CHANGES";
      operator: string;
      note: string | null;
      effective_plan: CommittedEffectivePlanProjection;
    }
  | {
      response_kind: "REJECT";
      reason_code: "MANUAL_HANDLING" | "INSUFFICIENT_CONTEXT" | "OTHER";
      operator: string;
      note: string | null;
      effective_plan: null;
    };

export type GenerationCapability =
  | { status: "UNAVAILABLE"; region: null; primary_provider: null; backup_provider: null; failure_code: "PROVIDER_UNCONFIGURED" }
  | { status: "READY"; region: "CN"; primary_provider: "KIMI"; backup_provider: null; failure_code: null }
  | { status: "UNAVAILABLE"; region: "CN"; primary_provider: "KIMI"; backup_provider: null; failure_code: "PROVIDER_UNCONFIGURED" }
  | { status: "READY"; region: "GLOBAL"; primary_provider: "OPENAI"; backup_provider: "ANTHROPIC"; failure_code: null }
  | { status: "DEGRADED_BACKUP_UNCONFIGURED"; region: "GLOBAL"; primary_provider: "OPENAI"; backup_provider: null; failure_code: "BACKUP_UNCONFIGURED" }
  | { status: "UNAVAILABLE"; region: "GLOBAL"; primary_provider: "OPENAI"; backup_provider: "ANTHROPIC" | null; failure_code: "PROVIDER_UNCONFIGURED" };

interface GenerationIdentity {
  suggestion_id: string;
  request_id: string;
  operation_id: string;
  basis: Basis;
  retry_of: string | null;
}

export type GenerationProjection = GenerationIdentity & (
  | {
      state: "RESERVED";
      candidates: [];
      coverage_gaps: [];
      provenance: [];
      failure_code: null;
      manager_response: null;
    }
  | {
      state: "IN_PROGRESS";
      candidates: [];
      coverage_gaps: [];
      provenance: ProviderProvenance[];
      failure_code: null;
      manager_response: null;
    }
  | {
      state: "RESULT_UNKNOWN";
      candidates: [];
      coverage_gaps: [];
      provenance: ProviderProvenance[];
      failure_code: "RESULT_UNKNOWN";
      manager_response: null;
    }
  | {
      state: "SUCCEEDED";
      candidates: [CandidateProjection, ...CandidateProjection[]];
      coverage_gaps: CoverageGap[];
      provenance: ProviderProvenance[];
      failure_code: null;
      manager_response: ManagerResponseSummary | null;
    }
  | {
      state: "NO_VALID_SUGGESTION";
      candidates: CandidateRejectedProjection[];
      coverage_gaps: CoverageGap[];
      provenance: ProviderProvenance[];
      failure_code: "NO_VALID_SUGGESTION";
      manager_response: null;
    }
  | {
      state: "UNAVAILABLE";
      candidates: [];
      coverage_gaps: [];
      provenance: ProviderProvenance[];
      failure_code: UnavailableFailureCode;
      manager_response: null;
    }
  | {
      state: "REFUSED";
      candidates: [];
      coverage_gaps: [];
      provenance: ProviderProvenance[];
      failure_code: "PROVIDER_REFUSED";
      manager_response: null;
    }
  | {
      state: "INVALID_RESPONSE";
      candidates: [];
      coverage_gaps: [];
      provenance: ProviderProvenance[];
      failure_code: InvalidResponseFailureCode;
      manager_response: null;
    }
  | {
      state: "PROVIDER_ERROR";
      candidates: [];
      coverage_gaps: [];
      provenance: ProviderProvenance[];
      failure_code: "PROVIDER_CLIENT_ERROR";
      manager_response: null;
    }
  | {
      state: "CONFIGURATION_ERROR";
      candidates: [];
      coverage_gaps: [];
      provenance: ProviderProvenance[];
      failure_code: ConfigurationFailureCode;
      manager_response: null;
    }
  | {
      state: "SECURITY_ERROR";
      candidates: [];
      coverage_gaps: [];
      provenance: ProviderProvenance[];
      failure_code: SecurityFailureCode;
      manager_response: null;
    }
);

interface RosterProjection {
  revision: number;
  effective_from_local_date: string;
  effective_until_local_date: string | null;
  worker_count: number;
  assignment_count: number;
  coverage_rule_count: number;
}

interface StaffingContext {
  site_id: string;
  deployment_id: string;
  site_timezone: string;
}

export interface StaffingDateSnapshot {
  schema: typeof STAFFING_SCHEMA;
  environment: "SIMULATION";
  mode: "STAFFING_ADVISORY_ONLY";
  server_time_utc: string;
  service_date: string;
  context: StaffingContext;
  generation_capability: GenerationCapability;
  revisions: RevisionVector;
  roster: RosterProjection | null;
  assignments: AssignmentProjection[];
  active_exceptions: ExceptionProjection[];
  effective_plan: EffectivePlanProjection | null;
  generations: GenerationProjection[];
}

export interface RosterImportedRecord {
  record_kind: "ROSTER_IMPORTED";
  roster_revision: number;
  roster_digest: string;
  imported_at_utc: string;
  worker_count: number;
  assignment_count: number;
  coverage_rule_count: number;
}

export interface ExceptionRecordedRecord {
  record_kind: "EXCEPTION_RECORDED";
  exception: ExceptionProjection;
  exception_set_revision: number;
  recorded_at_utc: string;
}

export interface ExceptionCancelledRecord {
  record_kind: "EXCEPTION_CANCELLED";
  exception_id: string;
  exception_set_revision: number;
  cancelled_at_utc: string;
}

export interface ExceptionCorrectedRecord {
  record_kind: "EXCEPTION_CORRECTED";
  replaced_exception_id: string;
  replacement: ExceptionProjection;
  exception_set_revision: number;
  corrected_at_utc: string;
}

export interface GenerationReservedRecord {
  record_kind: "GENERATION_RESERVED";
  suggestion_id: string;
  service_date: string;
  basis: Basis;
  retry_of: string | null;
  provenance: [];
}

export interface GenerationInProgressRecord {
  record_kind: "GENERATION_IN_PROGRESS";
  suggestion_id: string;
  service_date: string;
  basis: Basis;
  retry_of: string | null;
  provenance: ProviderProvenance[];
}

export interface GenerationInterruptedRecord {
  record_kind: "GENERATION_INTERRUPTED";
  suggestion_id: string;
  service_date: string;
  basis: Basis;
  retry_of: string | null;
  provenance: ProviderProvenance[];
  failure_code: "RESULT_UNKNOWN";
}

export interface SuggestionIssuedRecord {
  record_kind: "SUGGESTION_ISSUED";
  suggestion_id: string;
  service_date: string;
  basis: Basis;
  retry_of: string | null;
  provenance: ProviderProvenance[];
  candidates: CandidateProjection[];
  coverage_gaps: CoverageGap[];
  failure_code: null;
}

export interface SuggestionUnavailableRecord {
  record_kind: "SUGGESTION_UNAVAILABLE";
  suggestion_id: string;
  service_date: string;
  basis: Basis;
  retry_of: string | null;
  provenance: ProviderProvenance[];
  candidates: CandidateProjection[];
  coverage_gaps: CoverageGap[];
  failure_code: GenerationFailureCode;
}

export type ManagerResponseCommittedRecord =
  | {
      record_kind: "MANAGER_RESPONSE_COMMITTED";
      suggestion_id: string;
      response_kind: "ACCEPT";
      reason_code: "APPROVED";
      operator: string;
      note: string | null;
      effective_plan: CommittedEffectivePlanProjection;
      committed_at_utc: string;
    }
  | {
      record_kind: "MANAGER_RESPONSE_COMMITTED";
      suggestion_id: string;
      response_kind: "MODIFY";
      reason_code: "APPROVED_WITH_CHANGES";
      operator: string;
      note: string | null;
      effective_plan: CommittedEffectivePlanProjection;
      committed_at_utc: string;
    }
  | {
      record_kind: "MANAGER_RESPONSE_COMMITTED";
      suggestion_id: string;
      response_kind: "REJECT";
      reason_code: "MANUAL_HANDLING" | "INSUFFICIENT_CONTEXT" | "OTHER";
      operator: string;
      note: string | null;
      effective_plan: null;
      committed_at_utc: string;
    };

type StaffingRecord =
  | RosterImportedRecord
  | ExceptionRecordedRecord
  | ExceptionCancelledRecord
  | ExceptionCorrectedRecord
  | GenerationReservedRecord
  | GenerationInProgressRecord
  | GenerationInterruptedRecord
  | SuggestionIssuedRecord
  | SuggestionUnavailableRecord
  | ManagerResponseCommittedRecord;

interface ReceiptCommon {
  schema: typeof STAFFING_SCHEMA;
  disposition: "created" | "duplicate";
  request_id: string;
  operation_id: string;
}

export type StaffingReceipt = ReceiptCommon & (
  | { operation_kind: "roster-import"; state: "COMMITTED"; record: RosterImportedRecord }
  | { operation_kind: "exception-record"; state: "COMMITTED"; record: ExceptionRecordedRecord }
  | { operation_kind: "exception-cancel"; state: "COMMITTED"; record: ExceptionCancelledRecord }
  | { operation_kind: "exception-correct"; state: "COMMITTED"; record: ExceptionCorrectedRecord }
  | { operation_kind: "suggestion-generate"; state: "RESERVED"; record: GenerationReservedRecord }
  | { operation_kind: "suggestion-generate"; state: "IN_PROGRESS"; record: GenerationInProgressRecord }
  | { operation_kind: "suggestion-generate"; state: "RESULT_UNKNOWN"; record: GenerationInterruptedRecord }
  | { operation_kind: "suggestion-generate"; state: "SUCCEEDED"; record: SuggestionIssuedRecord }
  | { operation_kind: "suggestion-generate"; state: "NO_VALID_SUGGESTION"; record: SuggestionUnavailableRecord & { failure_code: "NO_VALID_SUGGESTION" } }
  | { operation_kind: "suggestion-generate"; state: "UNAVAILABLE"; record: SuggestionUnavailableRecord & { failure_code: UnavailableFailureCode } }
  | { operation_kind: "suggestion-generate"; state: "REFUSED"; record: SuggestionUnavailableRecord & { failure_code: "PROVIDER_REFUSED" } }
  | { operation_kind: "suggestion-generate"; state: "INVALID_RESPONSE"; record: SuggestionUnavailableRecord & { failure_code: InvalidResponseFailureCode } }
  | { operation_kind: "suggestion-generate"; state: "PROVIDER_ERROR"; record: SuggestionUnavailableRecord & { failure_code: "PROVIDER_CLIENT_ERROR" } }
  | { operation_kind: "suggestion-generate"; state: "CONFIGURATION_ERROR"; record: SuggestionUnavailableRecord & { failure_code: ConfigurationFailureCode } }
  | { operation_kind: "suggestion-generate"; state: "SECURITY_ERROR"; record: SuggestionUnavailableRecord & { failure_code: SecurityFailureCode } }
  | { operation_kind: "manager-response"; state: "COMMITTED"; record: ManagerResponseCommittedRecord }
);

export interface StaffingClient {
  current(): Promise<StaffingDateSnapshot>;
  date(serviceDate: string): Promise<StaffingDateSnapshot>;
  submit(mutation: StaffingMutation): Promise<StaffingReceipt>;
  lookup(operationKind: OperationKind, requestId: string): Promise<StaffingReceipt>;
  lookupMutation(mutation: StaffingMutation): Promise<StaffingReceipt>;
}

class ValidationFailure extends Error {}

const SNAPSHOT_KEYS = [
  "schema", "environment", "mode", "server_time_utc", "service_date", "context",
  "generation_capability", "revisions", "roster", "assignments", "active_exceptions",
  "effective_plan", "generations",
] as const;
const GENERATION_KEYS = [
  "suggestion_id", "request_id", "operation_id", "state", "basis", "retry_of",
  "candidates", "coverage_gaps", "provenance", "failure_code", "manager_response",
] as const;
const PROVENANCE_KEYS = [
  "attempt_index", "provider", "region", "route_role", "route_id", "model_id",
  "failure_code", "retryable", "security_failure", "provider_request_id", "finish_reason",
  "input_digest", "output_digest", "input_tokens", "output_tokens",
] as const;

function ok(condition: unknown): asserts condition {
  if (!condition) throw new ValidationFailure();
}

function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  ok(value !== null && typeof value === "object" && !Array.isArray(value));
  const prototype = Object.getPrototypeOf(value);
  ok(prototype === Object.prototype || prototype === null);
  const ownKeys = Reflect.ownKeys(value);
  ok(ownKeys.every((key) => typeof key === "string"));
  const actual = (ownKeys as string[]).sort();
  const expected = [...keys].sort();
  ok(actual.length === expected.length && actual.every((key, index) => key === expected[index]));
  for (const key of actual) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    ok(descriptor !== undefined && descriptor.enumerable && "value" in descriptor);
  }
  return value as Record<string, unknown>;
}

function array(value: unknown, minimum: number, maximum: number): unknown[] {
  ok(Array.isArray(value) && value.length >= minimum && value.length <= maximum);
  ok(Object.keys(value).length === value.length);
  ok(Reflect.ownKeys(value).every((key) => key === "length" || (typeof key === "string" && /^(?:0|[1-9]\d*)$/.test(key))));
  for (let index = 0; index < value.length; index += 1) ok(index in value);
  return value;
}

function literal<T extends string | number | boolean | null>(value: unknown, expected: T): T {
  ok(value === expected);
  return expected;
}

function oneOf<T extends string | number>(value: unknown, choices: readonly T[]): T {
  ok(choices.includes(value as T));
  return value as T;
}

function boolean(value: unknown): boolean {
  ok(typeof value === "boolean");
  return value;
}

function integer(value: unknown, minimum: number, maximum = Number.MAX_SAFE_INTEGER): number {
  ok(typeof value === "number" && Number.isFinite(value) && Number.isSafeInteger(value));
  ok(value >= minimum && value <= maximum);
  return value;
}

function hasUnpairedSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return true;
    }
  }
  return false;
}

function scalarLength(value: string): number {
  return Array.from(value).length;
}

function safeText(value: unknown, minimum: number, maximum: number, nonblank = false): string {
  ok(typeof value === "string" && !hasUnpairedSurrogate(value));
  const length = scalarLength(value);
  ok(length >= minimum && length <= maximum);
  for (const character of value) {
    const point = character.codePointAt(0) ?? 0;
    ok(!(point <= 0x1f || (point >= 0x7f && point <= 0x9f)));
  }
  if (nonblank) ok(/\S/u.test(value));
  return value;
}

function spreadsheetSafeText(value: unknown, minimum: number, maximum: number): string {
  const text = safeText(value, minimum, maximum, true);
  ok(!/^[ \t]*[=+@-]/.test(text));
  return text;
}

function identifier(value: unknown): string {
  ok(typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value));
  return value;
}

function code(value: unknown): string {
  ok(typeof value === "string" && /^[A-Z][A-Z0-9_]{0,31}$/.test(value));
  return value;
}

function digest(value: unknown): string {
  ok(typeof value === "string" && /^[0-9a-f]{64}$/.test(value));
  return value;
}

function nullable<T>(value: unknown, validator: (candidate: unknown) => T): T | null {
  return value === null ? null : validator(value);
}

function daysInMonth(year: number, month: number): number {
  if (month === 2) return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

function localDate(value: unknown): string {
  ok(typeof value === "string");
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  ok(match);
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  ok(year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= daysInMonth(year, month));
  return value;
}

function localMinute(value: unknown): string {
  ok(typeof value === "string" && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value));
  return value;
}

function auditTimestamp(value: unknown): string {
  ok(typeof value === "string");
  const match = /^(\d{4}-\d{2}-\d{2})T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{6}Z$/.exec(value);
  ok(match);
  localDate(match[1]);
  return value;
}

function offsetTimestamp(value: unknown): string {
  ok(typeof value === "string");
  const match = /^(\d{4}-\d{2}-\d{2})T(?:[01]\d|2[0-3]):[0-5]\d:00(?:Z|[+-](?!00:00)(?:[01]\d|2[0-3]):[0-5]\d)$/.exec(value);
  ok(match);
  localDate(match[1]);
  return value;
}

function ascii(value: unknown, maximum: number): string {
  ok(typeof value === "string" && value.length >= 1 && value.length <= maximum);
  ok(/\S/.test(value) && /^[\x20-\x7e]+$/.test(value));
  return value;
}

function nullableNote(value: unknown): string | null {
  return nullable(value, (item) => safeText(item, 0, 500));
}

function revisionVector(value: unknown): RevisionVector {
  const item = record(value, ["roster", "exception_set", "effective_plan"]);
  integer(item.roster, 0);
  integer(item.exception_set, 0);
  integer(item.effective_plan, 0);
  return value as RevisionVector;
}

function basis(value: unknown): Basis {
  const item = record(value, ["roster", "exception_set", "effective_plan", "digest"]);
  integer(item.roster, 0);
  integer(item.exception_set, 0);
  integer(item.effective_plan, 0);
  digest(item.digest);
  return value as Basis;
}

function uniqueStrings(values: unknown, minimum: number, maximum: number, validator: (value: unknown) => string): string[] {
  const items = array(values, minimum, maximum).map(validator);
  ok(new Set(items).size === items.length);
  return items;
}

function eligibility(value: unknown): EligibilityInput {
  const item = record(value, ["role_code", "area_code"]);
  code(item.role_code);
  code(item.area_code);
  return value as EligibilityInput;
}

function worker(value: unknown): WorkerInput {
  const item = record(value, ["staff_id", "display_name", "skill_codes", "eligibility", "max_daily_minutes"]);
  identifier(item.staff_id);
  spreadsheetSafeText(item.display_name, 1, 100);
  uniqueStrings(item.skill_codes, 0, 4096, code);
  const eligible = array(item.eligibility, 0, 4096).map(eligibility);
  ok(new Set(eligible.map((entry) => `${entry.role_code}\u0000${entry.area_code}`)).size === eligible.length);
  integer(item.max_daily_minutes, 1, 1440);
  return value as WorkerInput;
}

function availability(value: unknown): AvailabilityInput {
  const item = record(value, ["staff_id", "weekday", "start_local", "end_local"]);
  identifier(item.staff_id);
  integer(item.weekday, 0, 6);
  localMinute(item.start_local);
  localMinute(item.end_local);
  return value as AvailabilityInput;
}

function assignmentRule(value: unknown): AssignmentRuleInput {
  const item = record(value, ["role_code", "area_code", "required_skill_codes"]);
  code(item.role_code);
  code(item.area_code);
  uniqueStrings(item.required_skill_codes, 0, 4096, code);
  return value as AssignmentRuleInput;
}

function regularAssignment(value: unknown): RegularAssignmentInput {
  const item = record(value, ["staff_id", "weekday", "role_code", "area_code", "start_local", "end_local"]);
  identifier(item.staff_id);
  integer(item.weekday, 0, 6);
  code(item.role_code);
  code(item.area_code);
  localMinute(item.start_local);
  localMinute(item.end_local);
  return value as RegularAssignmentInput;
}

function coverageInput(value: unknown): CoverageInput {
  const item = record(value, ["weekday", "role_code", "area_code", "start_local", "end_local", "minimum_staff"]);
  integer(item.weekday, 0, 6);
  code(item.role_code);
  code(item.area_code);
  localMinute(item.start_local);
  localMinute(item.end_local);
  integer(item.minimum_staff, 1, 10000);
  return value as CoverageInput;
}

function validateRosterRequest(value: unknown): RosterImportRequest {
  const keys = [
    "schema", "request_id", "operator", "expected_roster_revision", "site_id", "deployment_id",
    "site_timezone", "effective_from_local_date", "effective_until_local_date", "source_ref",
    "workers", "availability", "assignment_rules", "regular_assignments", "coverage",
  ];
  const item = record(value, keys);
  literal(item.schema, "nxt-staffing-roster-import/v1");
  identifier(item.request_id);
  spreadsheetSafeText(item.operator, 1, 128);
  integer(item.expected_roster_revision, 0);
  identifier(item.site_id);
  identifier(item.deployment_id);
  safeText(item.site_timezone, 1, 128, true);
  localDate(item.effective_from_local_date);
  nullable(item.effective_until_local_date, localDate);
  spreadsheetSafeText(item.source_ref, 1, 256);
  array(item.workers, 1, 4096).forEach(worker);
  array(item.availability, 0, 4096).forEach(availability);
  array(item.assignment_rules, 1, 4096).forEach(assignmentRule);
  array(item.regular_assignments, 0, 4096).forEach(regularAssignment);
  array(item.coverage, 1, 4096).forEach(coverageInput);
  return value as RosterImportRequest;
}

function validateExceptionRecordRequest(value: unknown): ExceptionRecordRequest {
  const item = record(value, [
    "schema", "request_id", "operator", "service_date", "expected_roster_revision",
    "expected_exception_set_revision", "staff_id", "kind", "time_local", "note",
  ]);
  literal(item.schema, "nxt-staffing-exception/v1");
  identifier(item.request_id);
  spreadsheetSafeText(item.operator, 1, 128);
  localDate(item.service_date);
  integer(item.expected_roster_revision, 0);
  integer(item.expected_exception_set_revision, 0);
  identifier(item.staff_id);
  const kind = oneOf(item.kind, ["LEAVE", "LATE", "EARLY_DEPARTURE", "UNAVAILABLE"] as const);
  if (kind === "LEAVE" || kind === "UNAVAILABLE") literal(item.time_local, null);
  else localMinute(item.time_local);
  nullableNote(item.note);
  return value as ExceptionRecordRequest;
}

function validateExceptionCancelRequest(value: unknown): ExceptionCancelRequest {
  const item = record(value, ["schema", "request_id", "operator", "expected_exception_set_revision", "note"]);
  literal(item.schema, "nxt-staffing-exception-cancel/v1");
  identifier(item.request_id);
  spreadsheetSafeText(item.operator, 1, 128);
  integer(item.expected_exception_set_revision, 0);
  nullableNote(item.note);
  return value as ExceptionCancelRequest;
}

function exceptionReplacement(value: unknown): ExceptionReplacementInput {
  const item = record(value, ["kind", "time_local", "note"]);
  const kind = oneOf(item.kind, ["LEAVE", "LATE", "EARLY_DEPARTURE", "UNAVAILABLE"] as const);
  if (kind === "LEAVE" || kind === "UNAVAILABLE") literal(item.time_local, null);
  else localMinute(item.time_local);
  nullableNote(item.note);
  return value as ExceptionReplacementInput;
}

function validateExceptionCorrectRequest(value: unknown): ExceptionCorrectRequest {
  const item = record(value, ["schema", "request_id", "operator", "expected_exception_set_revision", "replacement"]);
  literal(item.schema, "nxt-staffing-exception-correct/v1");
  identifier(item.request_id);
  spreadsheetSafeText(item.operator, 1, 128);
  integer(item.expected_exception_set_revision, 0);
  exceptionReplacement(item.replacement);
  return value as ExceptionCorrectRequest;
}

function validateSuggestionRequest(value: unknown): SuggestionGenerateRequest {
  const item = record(value, ["schema", "request_id", "operator", "service_date", "expected_revisions", "retry_of"]);
  literal(item.schema, "nxt-staffing-suggestion-generate/v1");
  identifier(item.request_id);
  spreadsheetSafeText(item.operator, 1, 128);
  localDate(item.service_date);
  revisionVector(item.expected_revisions);
  nullable(item.retry_of, identifier);
  return value as SuggestionGenerateRequest;
}

function managerPatch(value: unknown): ManagerPatchOperation {
  ok(value !== null && typeof value === "object" && !Array.isArray(value));
  const operation = (value as Record<string, unknown>).operation;
  if (operation === "REMOVE") {
    const item = record(value, ["operation", "assignment_id"]);
    identifier(item.assignment_id);
  } else if (operation === "ADD") {
    const item = record(value, ["operation", "staff_id", "role_code", "area_code", "start_at", "end_at"]);
    identifier(item.staff_id);
    code(item.role_code);
    code(item.area_code);
    offsetTimestamp(item.start_at);
    offsetTimestamp(item.end_at);
  } else throw new ValidationFailure();
  return value as ManagerPatchOperation;
}

function validateManagerRequest(value: unknown): ManagerResponseRequest {
  const item = record(value, [
    "schema", "request_id", "operator", "kind", "expected_revisions", "candidate_index",
    "edited_operations", "reason_code", "note",
  ]);
  literal(item.schema, "nxt-staffing-manager-response/v1");
  identifier(item.request_id);
  spreadsheetSafeText(item.operator, 1, 128);
  revisionVector(item.expected_revisions);
  nullableNote(item.note);
  if (item.kind === "ACCEPT") {
    oneOf(item.candidate_index, [1, 2] as const);
    literal(item.edited_operations, null);
    literal(item.reason_code, "APPROVED");
  } else if (item.kind === "MODIFY") {
    oneOf(item.candidate_index, [1, 2] as const);
    array(item.edited_operations, 1, 32).forEach(managerPatch);
    literal(item.reason_code, "APPROVED_WITH_CHANGES");
  } else if (item.kind === "REJECT") {
    literal(item.candidate_index, null);
    literal(item.edited_operations, null);
    oneOf(item.reason_code, ["MANUAL_HANDLING", "INSUFFICIENT_CONTEXT", "OTHER"] as const);
  } else throw new ValidationFailure();
  return value as ManagerResponseRequest;
}

function validateRequest(value: unknown): StaffingRequestBody {
  ok(value !== null && typeof value === "object" && !Array.isArray(value));
  switch ((value as Record<string, unknown>).schema) {
    case "nxt-staffing-roster-import/v1": return validateRosterRequest(value);
    case "nxt-staffing-exception/v1": return validateExceptionRecordRequest(value);
    case "nxt-staffing-exception-cancel/v1": return validateExceptionCancelRequest(value);
    case "nxt-staffing-exception-correct/v1": return validateExceptionCorrectRequest(value);
    case "nxt-staffing-suggestion-generate/v1": return validateSuggestionRequest(value);
    case "nxt-staffing-manager-response/v1": return validateManagerRequest(value);
    default: throw new ValidationFailure();
  }
}

function assignment(value: unknown): AssignmentProjection {
  const item = record(value, ["assignment_id", "staff_id", "display_name", "role_code", "area_code", "start_at", "end_at"]);
  identifier(item.assignment_id);
  identifier(item.staff_id);
  spreadsheetSafeText(item.display_name, 1, 100);
  code(item.role_code);
  code(item.area_code);
  offsetTimestamp(item.start_at);
  offsetTimestamp(item.end_at);
  return value as AssignmentProjection;
}

function exception(value: unknown): ExceptionProjection {
  const item = record(value, [
    "exception_id", "staff_id", "display_name", "kind", "unavailable_start_at",
    "unavailable_end_at", "note", "active",
  ]);
  identifier(item.exception_id);
  identifier(item.staff_id);
  spreadsheetSafeText(item.display_name, 1, 100);
  oneOf(item.kind, ["LEAVE", "LATE", "EARLY_DEPARTURE", "UNAVAILABLE"] as const);
  offsetTimestamp(item.unavailable_start_at);
  offsetTimestamp(item.unavailable_end_at);
  nullableNote(item.note);
  boolean(item.active);
  return value as ExceptionProjection;
}

function coverageGap(value: unknown): CoverageGap {
  const item = record(value, [
    "role_code", "area_code", "start_at", "end_at", "required_count", "assigned_count", "rejection_code",
  ]);
  code(item.role_code);
  code(item.area_code);
  offsetTimestamp(item.start_at);
  offsetTimestamp(item.end_at);
  const required = integer(item.required_count, 1, 10000);
  const assigned = integer(item.assigned_count, 0, 9999);
  ok(assigned < required);
  literal(item.rejection_code, "COVERAGE_GAP");
  return value as CoverageGap;
}

function candidateOperation(value: unknown): CandidateOperationProjection {
  ok(value !== null && typeof value === "object" && !Array.isArray(value));
  const operation = (value as Record<string, unknown>).operation;
  const common = operation === "REMOVE"
    ? record(value, ["operation", "assignment_id", "staff_id", "display_name", "role_code", "area_code", "start_at", "end_at"])
    : operation === "ADD"
      ? record(value, ["operation", "staff_id", "display_name", "role_code", "area_code", "start_at", "end_at"])
      : (() => { throw new ValidationFailure(); })();
  if (operation === "REMOVE") identifier(common.assignment_id);
  identifier(common.staff_id);
  spreadsheetSafeText(common.display_name, 1, 100);
  code(common.role_code);
  code(common.area_code);
  offsetTimestamp(common.start_at);
  offsetTimestamp(common.end_at);
  return value as CandidateOperationProjection;
}

const REJECTION_CODES = [
  "UNKNOWN_ROLE_AREA", "INELIGIBLE_ROLE_AREA", "MISSING_REQUIRED_SKILL", "INVALID_INTERVAL",
  "INVALID_MINUTE_PRECISION", "OFFSET_TIMEZONE_MISMATCH", "OUTSIDE_SERVICE_DATE",
  "OUTSIDE_AVAILABILITY", "OVERLAPS_EXCEPTION", "OVERLAPPING_ASSIGNMENTS",
  "MAX_DAILY_MINUTES_EXCEEDED", "COVERAGE_GAP", "PROMPT_TEMPLATE_VERSION_MISMATCH",
] as const;

function candidate(value: unknown): CandidateProjection {
  const item = record(value, [
    "candidate_index", "status", "operations", "rationale", "operational_warnings",
    "coverage_gaps", "schedule_digest", "rejection_codes",
    "rationale_local", "operational_warnings_local", "action_window_end_at", "actionability",
  ]);
  oneOf(item.candidate_index, [1, 2] as const);
  array(item.operations, 0, 32).forEach(candidateOperation);
  safeText(item.rationale, 0, 280);
  const warnings = array(item.operational_warnings, 0, 5);
  warnings.forEach((warning) => safeText(warning, 0, 200));
  safeText(item.rationale_local, 0, 2000);
  const localWarnings = array(item.operational_warnings_local, 0, 5);
  localWarnings.forEach((warning) => safeText(warning, 0, 1500));
  ok(localWarnings.length === warnings.length);
  offsetTimestamp(item.action_window_end_at);
  oneOf(item.actionability, ["CURRENT", "EXPIRED"] as const);
  if (item.status === "VALID") {
    array(item.coverage_gaps, 0, 0);
    digest(item.schedule_digest);
    array(item.rejection_codes, 0, 0);
  } else if (item.status === "REJECTED") {
    literal(item.schedule_digest, null);
    const gaps = array(item.coverage_gaps, 0, 4096);
    gaps.forEach(coverageGap);
    const rejected = array(item.rejection_codes, 1, 13).map((entry) => oneOf(entry, REJECTION_CODES));
    ok(new Set(rejected).size === rejected.length);
    if (gaps.length > 0) {
      ok(rejected.length === 1 && rejected[0] === "COVERAGE_GAP");
    } else {
      ok(!rejected.includes("COVERAGE_GAP"));
    }
  } else throw new ValidationFailure();
  return value as CandidateProjection;
}

function candidateSequence(value: unknown, minimum: number, requireValid: boolean, requireRejected: boolean): CandidateProjection[] {
  const items = array(value, minimum, 2).map(candidate);
  items.forEach((item, index) => ok(item.candidate_index === index + 1));
  if (requireValid) ok(items.some((item) => item.status === "VALID"));
  if (requireRejected) ok(items.every((item) => item.status === "REJECTED"));
  return items;
}

const FALLBACK_FAILURES = [
  "DNS_FAILURE", "CONNECT_TIMEOUT", "CONNECT_FAILED", "READ_TIMEOUT", "CONNECTION_INTERRUPTED",
  "HTTP_TIMEOUT", "RATE_LIMITED", "PROVIDER_UNAVAILABLE",
] as const;
const NONFALLBACK_FAILURES = [
  "DEADLINE_EXHAUSTED", "PROVIDER_REFUSED", "MALFORMED_PROVIDER_RESPONSE", "SCHEMA_MISMATCH",
  "AUTHENTICATION_FAILED", "PERMISSION_DENIED", "MODEL_NOT_FOUND", "INVALID_PROVIDER_REQUEST",
  "PROVIDER_CLIENT_ERROR",
] as const;
const SECURITY_FAILURES = [
  "RESPONSE_TOO_LARGE", "UNSUPPORTED_CONTENT_ENCODING", "REDIRECT_REFUSED", "ENDPOINT_NOT_ALLOWED",
  "TLS_VERIFICATION_FAILED",
] as const;
const GATEWAY_FAILURES = [...FALLBACK_FAILURES, ...NONFALLBACK_FAILURES, ...SECURITY_FAILURES] as const;

function provenance(value: unknown): ProviderProvenance {
  const item = record(value, PROVENANCE_KEYS);
  const index = oneOf(item.attempt_index, [0, 1] as const);
  const provider = oneOf(item.provider, ["KIMI", "OPENAI", "ANTHROPIC"] as const);
  oneOf(item.region, ["CN", "GLOBAL"] as const);
  oneOf(item.route_role, ["PRIMARY", "BACKUP"] as const);
  oneOf(item.route_id, ["cn-kimi-v1", "global-openai-v1", "global-anthropic-v1"] as const);
  const route = `${index}/${provider}/${item.region}/${item.route_role}/${item.route_id}`;
  ok([
    "0/KIMI/CN/PRIMARY/cn-kimi-v1",
    "0/OPENAI/GLOBAL/PRIMARY/global-openai-v1",
    "1/ANTHROPIC/GLOBAL/BACKUP/global-anthropic-v1",
  ].includes(route));
  ascii(item.model_id, 128);
  nullable(item.failure_code, (failure) => oneOf(failure, GATEWAY_FAILURES));
  boolean(item.retryable);
  boolean(item.security_failure);
  nullable(item.provider_request_id, (requestId) => ascii(requestId, 160));
  nullable(item.finish_reason, (reason) => ascii(reason, 160));
  digest(item.input_digest);
  nullable(item.output_digest, digest);
  nullable(item.input_tokens, (tokens) => integer(tokens, 0, 1_000_000));
  nullable(item.output_tokens, (tokens) => integer(tokens, 0, 1_000_000));

  if (item.failure_code === null) {
    ok(item.retryable === false && item.security_failure === false && item.output_digest !== null);
    const finish = provider === "KIMI" ? "stop" : provider === "OPENAI" ? "completed" : "tool_use";
    ok(item.finish_reason === finish);
    ok((item.input_tokens === null) === (item.output_tokens === null));
  } else {
    const fallback = FALLBACK_FAILURES.includes(item.failure_code as (typeof FALLBACK_FAILURES)[number]);
    const security = SECURITY_FAILURES.includes(item.failure_code as (typeof SECURITY_FAILURES)[number]);
    ok(item.retryable === fallback && item.security_failure === security);
    ok(item.provider_request_id === null && item.finish_reason === null && item.output_digest === null);
    ok(item.input_tokens === null && item.output_tokens === null);
  }
  return value as ProviderProvenance;
}

function isOpenAiFallback(item: ProviderProvenance): boolean {
  return item.attempt_index === 0 && item.provider === "OPENAI" &&
    FALLBACK_FAILURES.includes(item.failure_code as (typeof FALLBACK_FAILURES)[number]);
}

function provenanceSequence(value: unknown): ProviderProvenance[] {
  const items = array(value, 0, 2).map(provenance);
  if (items.length === 1) ok(items[0].attempt_index === 0);
  if (items.length === 2) ok(isOpenAiFallback(items[0]) && items[1].attempt_index === 1 && items[1].provider === "ANTHROPIC");
  if (items.length > 1) ok(items.every((item) => item.input_digest === items[0].input_digest));
  return items;
}

function successfulProvenance(value: unknown): ProviderProvenance[] {
  const items = provenanceSequence(value);
  ok(items.length >= 1 && items[items.length - 1].failure_code === null);
  if (items.length === 1) ok(items[0].attempt_index === 0);
  return items;
}

function terminalProvenance(failureCode: string, items: ProviderProvenance[]): void {
  if (failureCode === "NO_VALID_SUGGESTION" || [
    "invalid_provider_shape", "provider_sensitive_key", "invalid_provider_timestamp", "invalid_candidate_set",
  ].includes(failureCode)) {
    successfulProvenance(items);
    return;
  }
  if (failureCode === "PROVIDER_UNCONFIGURED") {
    ok(items.length === 0);
    return;
  }
  if (failureCode === "INPUT_TOO_LARGE") {
    ok(items.length === 0 || (items.length === 1 && isOpenAiFallback(items[0])));
    return;
  }
  if (failureCode === "BACKUP_UNCONFIGURED") {
    ok(items.length === 1 && isOpenAiFallback(items[0]));
    return;
  }
  if (failureCode === "DEADLINE_EXHAUSTED" && (items.length === 0 || (items.length === 1 && isOpenAiFallback(items[0])))) return;
  ok(items.length >= 1 && items[items.length - 1].failure_code === failureCode);
  if (items.length === 1) {
    ok(items[0].provider === "KIMI" || (items[0].provider === "OPENAI" && !isOpenAiFallback(items[0])));
  } else {
    ok(items.length === 2 && isOpenAiFallback(items[0]) && items[1].provider === "ANTHROPIC");
  }
}

function effectivePlan(value: unknown, committed = false): EffectivePlanProjection {
  const item = record(value, ["revision", "status", "schedule_digest", "assignments"]);
  integer(item.revision, 1);
  if (committed) literal(item.status, "CURRENT");
  else oneOf(item.status, ["CURRENT", "REVIEW_REQUIRED"] as const);
  digest(item.schedule_digest);
  array(item.assignments, 0, 4096).forEach(assignment);
  return value as EffectivePlanProjection;
}

function managerSummary(value: unknown): ManagerResponseSummary {
  const item = record(value, ["response_kind", "reason_code", "operator", "note", "effective_plan"]);
  spreadsheetSafeText(item.operator, 1, 128);
  nullableNote(item.note);
  if (item.response_kind === "ACCEPT") {
    literal(item.reason_code, "APPROVED");
    effectivePlan(item.effective_plan, true);
  } else if (item.response_kind === "MODIFY") {
    literal(item.reason_code, "APPROVED_WITH_CHANGES");
    effectivePlan(item.effective_plan, true);
  } else if (item.response_kind === "REJECT") {
    oneOf(item.reason_code, ["MANUAL_HANDLING", "INSUFFICIENT_CONTEXT", "OTHER"] as const);
    literal(item.effective_plan, null);
  } else throw new ValidationFailure();
  return value as ManagerResponseSummary;
}

const GENERATION_FAILURES = [
  "NO_VALID_SUGGESTION", ...GATEWAY_FAILURES, "BACKUP_UNCONFIGURED", "invalid_provider_shape",
  "provider_sensitive_key", "invalid_provider_timestamp", "invalid_candidate_set", "INPUT_TOO_LARGE",
  "PROVIDER_UNCONFIGURED",
] as const;

function validateUnavailableState(state: string, failure: unknown, provenanceItems: ProviderProvenance[]): void {
  const failureCode = oneOf(failure, GENERATION_FAILURES);
  const allowed: Record<string, readonly string[]> = {
    UNAVAILABLE: [...FALLBACK_FAILURES, "DEADLINE_EXHAUSTED", "BACKUP_UNCONFIGURED"],
    REFUSED: ["PROVIDER_REFUSED"],
    INVALID_RESPONSE: [
      "MALFORMED_PROVIDER_RESPONSE", "SCHEMA_MISMATCH", "invalid_provider_shape",
      "provider_sensitive_key", "invalid_provider_timestamp", "invalid_candidate_set",
    ],
    PROVIDER_ERROR: ["PROVIDER_CLIENT_ERROR"],
    CONFIGURATION_ERROR: [
      "INPUT_TOO_LARGE", "AUTHENTICATION_FAILED", "PERMISSION_DENIED", "MODEL_NOT_FOUND",
      "INVALID_PROVIDER_REQUEST", "PROVIDER_UNCONFIGURED",
    ],
    SECURITY_ERROR: [...SECURITY_FAILURES],
  };
  ok(allowed[state]?.includes(failureCode));
  if (["REFUSED", "INVALID_RESPONSE", "PROVIDER_ERROR", "SECURITY_ERROR"].includes(state)) ok(provenanceItems.length >= 1);
  terminalProvenance(failureCode, provenanceItems);
}

function generation(value: unknown): GenerationProjection {
  const item = record(value, GENERATION_KEYS);
  identifier(item.suggestion_id);
  identifier(item.request_id);
  identifier(item.operation_id);
  const state = oneOf(item.state, [
    "RESERVED", "IN_PROGRESS", "RESULT_UNKNOWN", "SUCCEEDED", "NO_VALID_SUGGESTION",
    "UNAVAILABLE", "REFUSED", "INVALID_RESPONSE", "PROVIDER_ERROR", "CONFIGURATION_ERROR", "SECURITY_ERROR",
  ] as const);
  basis(item.basis);
  nullable(item.retry_of, identifier);
  const gaps = array(item.coverage_gaps, 0, 4096);
  gaps.forEach(coverageGap);
  const provenanceItems = provenanceSequence(item.provenance);
  if (state === "RESERVED") {
    array(item.candidates, 0, 0); ok(gaps.length === 0 && provenanceItems.length === 0);
    literal(item.failure_code, null); literal(item.manager_response, null);
  } else if (state === "IN_PROGRESS") {
    array(item.candidates, 0, 0); ok(gaps.length === 0);
    literal(item.failure_code, null); literal(item.manager_response, null);
  } else if (state === "RESULT_UNKNOWN") {
    array(item.candidates, 0, 0); ok(gaps.length === 0);
    literal(item.failure_code, "RESULT_UNKNOWN"); literal(item.manager_response, null);
  } else if (state === "SUCCEEDED") {
    candidateSequence(item.candidates, 1, true, false);
    successfulProvenance(provenanceItems);
    literal(item.failure_code, null);
    nullable(item.manager_response, managerSummary);
  } else if (state === "NO_VALID_SUGGESTION") {
    candidateSequence(item.candidates, 0, false, true);
    successfulProvenance(provenanceItems);
    literal(item.failure_code, "NO_VALID_SUGGESTION"); literal(item.manager_response, null);
  } else {
    array(item.candidates, 0, 0); ok(gaps.length === 0);
    validateUnavailableState(state, item.failure_code, provenanceItems);
    literal(item.manager_response, null);
  }
  return value as GenerationProjection;
}

function generationSequence(value: unknown): GenerationProjection[] {
  const items = array(value, 0, 4096).map(generation);
  const requestIds = new Set<string>();
  const suggestionIds = new Set<string>();
  const operationIds = new Set<string>();
  const earlierBySuggestion = new Map<string, GenerationProjection>();
  const retriedParents = new Set<string>();

  for (const item of items) {
    ok(!requestIds.has(item.request_id));
    ok(!suggestionIds.has(item.suggestion_id));
    ok(!operationIds.has(item.operation_id));
    requestIds.add(item.request_id);
    suggestionIds.add(item.suggestion_id);
    operationIds.add(item.operation_id);

    if (item.retry_of !== null) {
      const parent = earlierBySuggestion.get(item.retry_of);
      ok(parent !== undefined && parent.state === "RESULT_UNKNOWN");
      ok(!retriedParents.has(item.retry_of));
      retriedParents.add(item.retry_of);
    }
    earlierBySuggestion.set(item.suggestion_id, item);
  }

  return items;
}

function generationCapability(value: unknown): GenerationCapability {
  const item = record(value, ["status", "region", "primary_provider", "backup_provider", "failure_code"]);
  const status = oneOf(item.status, ["READY", "UNAVAILABLE", "DEGRADED_BACKUP_UNCONFIGURED"] as const);
  const region = nullable(item.region, (entry) => oneOf(entry, ["CN", "GLOBAL"] as const));
  const primary = nullable(item.primary_provider, (entry) => oneOf(entry, ["KIMI", "OPENAI"] as const));
  const backup = nullable(item.backup_provider, (entry) => oneOf(entry, ["ANTHROPIC"] as const));
  const failure = nullable(item.failure_code, (entry) => oneOf(entry, ["PROVIDER_UNCONFIGURED", "BACKUP_UNCONFIGURED"] as const));
  const tuple = `${status}/${String(region)}/${String(primary)}/${String(backup)}/${String(failure)}`;
  ok([
    "UNAVAILABLE/null/null/null/PROVIDER_UNCONFIGURED",
    "READY/CN/KIMI/null/null",
    "UNAVAILABLE/CN/KIMI/null/PROVIDER_UNCONFIGURED",
    "READY/GLOBAL/OPENAI/ANTHROPIC/null",
    "DEGRADED_BACKUP_UNCONFIGURED/GLOBAL/OPENAI/null/BACKUP_UNCONFIGURED",
    "UNAVAILABLE/GLOBAL/OPENAI/ANTHROPIC/PROVIDER_UNCONFIGURED",
    "UNAVAILABLE/GLOBAL/OPENAI/null/PROVIDER_UNCONFIGURED",
  ].includes(tuple));
  return value as GenerationCapability;
}

function rosterProjection(value: unknown): RosterProjection {
  const item = record(value, [
    "revision", "effective_from_local_date", "effective_until_local_date", "worker_count",
    "assignment_count", "coverage_rule_count",
  ]);
  integer(item.revision, 1);
  localDate(item.effective_from_local_date);
  nullable(item.effective_until_local_date, localDate);
  integer(item.worker_count, 0);
  integer(item.assignment_count, 0);
  integer(item.coverage_rule_count, 0);
  return value as RosterProjection;
}

function validateSnapshot(value: unknown): StaffingDateSnapshot {
  const item = record(value, SNAPSHOT_KEYS);
  literal(item.schema, STAFFING_SCHEMA);
  literal(item.environment, "SIMULATION");
  literal(item.mode, "STAFFING_ADVISORY_ONLY");
  auditTimestamp(item.server_time_utc);
  localDate(item.service_date);
  const context = record(item.context, ["site_id", "deployment_id", "site_timezone"]);
  identifier(context.site_id);
  identifier(context.deployment_id);
  safeText(context.site_timezone, 1, 128, true);
  generationCapability(item.generation_capability);
  revisionVector(item.revisions);
  nullable(item.roster, rosterProjection);
  array(item.assignments, 0, 4096).forEach(assignment);
  array(item.active_exceptions, 0, 4096).forEach(exception);
  nullable(item.effective_plan, effectivePlan);
  generationSequence(item.generations);
  return value as StaffingDateSnapshot;
}

function rosterRecord(value: unknown): RosterImportedRecord {
  const item = record(value, [
    "record_kind", "roster_revision", "roster_digest", "imported_at_utc",
    "worker_count", "assignment_count", "coverage_rule_count",
  ]);
  literal(item.record_kind, "ROSTER_IMPORTED");
  integer(item.roster_revision, 1);
  digest(item.roster_digest);
  auditTimestamp(item.imported_at_utc);
  integer(item.worker_count, 1);
  integer(item.assignment_count, 0);
  integer(item.coverage_rule_count, 1);
  return value as RosterImportedRecord;
}

function exceptionRecordedRecord(value: unknown): ExceptionRecordedRecord {
  const item = record(value, ["record_kind", "exception", "exception_set_revision", "recorded_at_utc"]);
  literal(item.record_kind, "EXCEPTION_RECORDED");
  exception(item.exception);
  integer(item.exception_set_revision, 1);
  auditTimestamp(item.recorded_at_utc);
  return value as ExceptionRecordedRecord;
}

function exceptionCancelledRecord(value: unknown): ExceptionCancelledRecord {
  const item = record(value, ["record_kind", "exception_id", "exception_set_revision", "cancelled_at_utc"]);
  literal(item.record_kind, "EXCEPTION_CANCELLED");
  identifier(item.exception_id);
  integer(item.exception_set_revision, 1);
  auditTimestamp(item.cancelled_at_utc);
  return value as ExceptionCancelledRecord;
}

function exceptionCorrectedRecord(value: unknown): ExceptionCorrectedRecord {
  const item = record(value, ["record_kind", "replaced_exception_id", "replacement", "exception_set_revision", "corrected_at_utc"]);
  literal(item.record_kind, "EXCEPTION_CORRECTED");
  identifier(item.replaced_exception_id);
  exception(item.replacement);
  integer(item.exception_set_revision, 1);
  auditTimestamp(item.corrected_at_utc);
  return value as ExceptionCorrectedRecord;
}

function generationBaseRecord(value: unknown, keys: readonly string[]): Record<string, unknown> {
  const item = record(value, keys);
  identifier(item.suggestion_id);
  localDate(item.service_date);
  basis(item.basis);
  nullable(item.retry_of, identifier);
  return item;
}

function generationReservedRecord(value: unknown): GenerationReservedRecord {
  const item = generationBaseRecord(value, ["record_kind", "suggestion_id", "service_date", "basis", "retry_of", "provenance"]);
  literal(item.record_kind, "GENERATION_RESERVED");
  array(item.provenance, 0, 0);
  return value as GenerationReservedRecord;
}

function generationInProgressRecord(value: unknown): GenerationInProgressRecord {
  const item = generationBaseRecord(value, ["record_kind", "suggestion_id", "service_date", "basis", "retry_of", "provenance"]);
  literal(item.record_kind, "GENERATION_IN_PROGRESS");
  provenanceSequence(item.provenance);
  return value as GenerationInProgressRecord;
}

function generationInterruptedRecord(value: unknown): GenerationInterruptedRecord {
  const item = generationBaseRecord(value, [
    "record_kind", "suggestion_id", "service_date", "basis", "retry_of", "provenance", "failure_code",
  ]);
  literal(item.record_kind, "GENERATION_INTERRUPTED");
  provenanceSequence(item.provenance);
  literal(item.failure_code, "RESULT_UNKNOWN");
  return value as GenerationInterruptedRecord;
}

function suggestionIssuedRecord(value: unknown): SuggestionIssuedRecord {
  const item = generationBaseRecord(value, [
    "record_kind", "suggestion_id", "service_date", "basis", "retry_of", "provenance",
    "candidates", "coverage_gaps", "failure_code",
  ]);
  literal(item.record_kind, "SUGGESTION_ISSUED");
  successfulProvenance(item.provenance);
  candidateSequence(item.candidates, 1, true, false);
  array(item.coverage_gaps, 0, 4096).forEach(coverageGap);
  literal(item.failure_code, null);
  return value as SuggestionIssuedRecord;
}

function suggestionUnavailableRecord(value: unknown): SuggestionUnavailableRecord {
  const item = generationBaseRecord(value, [
    "record_kind", "suggestion_id", "service_date", "basis", "retry_of", "provenance",
    "candidates", "coverage_gaps", "failure_code",
  ]);
  literal(item.record_kind, "SUGGESTION_UNAVAILABLE");
  const provenanceItems = provenanceSequence(item.provenance);
  candidateSequence(item.candidates, 0, false, false);
  array(item.coverage_gaps, 0, 4096).forEach(coverageGap);
  const failureCode = oneOf(item.failure_code, GENERATION_FAILURES);
  terminalProvenance(failureCode, provenanceItems);
  return value as SuggestionUnavailableRecord;
}

function managerCommittedRecord(value: unknown): ManagerResponseCommittedRecord {
  const item = record(value, [
    "record_kind", "suggestion_id", "response_kind", "reason_code", "operator", "note",
    "effective_plan", "committed_at_utc",
  ]);
  literal(item.record_kind, "MANAGER_RESPONSE_COMMITTED");
  identifier(item.suggestion_id);
  spreadsheetSafeText(item.operator, 1, 128);
  nullableNote(item.note);
  auditTimestamp(item.committed_at_utc);
  if (item.response_kind === "ACCEPT") {
    literal(item.reason_code, "APPROVED"); effectivePlan(item.effective_plan, true);
  } else if (item.response_kind === "MODIFY") {
    literal(item.reason_code, "APPROVED_WITH_CHANGES"); effectivePlan(item.effective_plan, true);
  } else if (item.response_kind === "REJECT") {
    oneOf(item.reason_code, ["MANUAL_HANDLING", "INSUFFICIENT_CONTEXT", "OTHER"] as const);
    literal(item.effective_plan, null);
  } else throw new ValidationFailure();
  return value as ManagerResponseCommittedRecord;
}

function receiptRecord(value: unknown): StaffingRecord {
  ok(value !== null && typeof value === "object" && !Array.isArray(value));
  switch ((value as Record<string, unknown>).record_kind) {
    case "ROSTER_IMPORTED": return rosterRecord(value);
    case "EXCEPTION_RECORDED": return exceptionRecordedRecord(value);
    case "EXCEPTION_CANCELLED": return exceptionCancelledRecord(value);
    case "EXCEPTION_CORRECTED": return exceptionCorrectedRecord(value);
    case "GENERATION_RESERVED": return generationReservedRecord(value);
    case "GENERATION_IN_PROGRESS": return generationInProgressRecord(value);
    case "GENERATION_INTERRUPTED": return generationInterruptedRecord(value);
    case "SUGGESTION_ISSUED": return suggestionIssuedRecord(value);
    case "SUGGESTION_UNAVAILABLE": return suggestionUnavailableRecord(value);
    case "MANAGER_RESPONSE_COMMITTED": return managerCommittedRecord(value);
    default: throw new ValidationFailure();
  }
}

function validateReceipt(value: unknown): StaffingReceipt {
  const item = record(value, ["schema", "disposition", "operation_kind", "request_id", "operation_id", "state", "record"]);
  literal(item.schema, STAFFING_SCHEMA);
  oneOf(item.disposition, ["created", "duplicate"] as const);
  const operationKind = oneOf(item.operation_kind, [
    "roster-import", "exception-record", "exception-cancel", "exception-correct", "suggestion-generate", "manager-response",
  ] as const);
  identifier(item.request_id);
  identifier(item.operation_id);
  const state = oneOf(item.state, [
    "COMMITTED", "RESERVED", "IN_PROGRESS", "RESULT_UNKNOWN", "SUCCEEDED", "NO_VALID_SUGGESTION",
    "UNAVAILABLE", "REFUSED", "INVALID_RESPONSE", "PROVIDER_ERROR", "CONFIGURATION_ERROR", "SECURITY_ERROR",
  ] as const);
  const parsedRecord = receiptRecord(item.record);
  const triple = `${operationKind}/${state}/${parsedRecord.record_kind}`;
  const fixed = [
    "roster-import/COMMITTED/ROSTER_IMPORTED",
    "exception-record/COMMITTED/EXCEPTION_RECORDED",
    "exception-cancel/COMMITTED/EXCEPTION_CANCELLED",
    "exception-correct/COMMITTED/EXCEPTION_CORRECTED",
    "suggestion-generate/RESERVED/GENERATION_RESERVED",
    "suggestion-generate/IN_PROGRESS/GENERATION_IN_PROGRESS",
    "suggestion-generate/RESULT_UNKNOWN/GENERATION_INTERRUPTED",
    "suggestion-generate/SUCCEEDED/SUGGESTION_ISSUED",
    "manager-response/COMMITTED/MANAGER_RESPONSE_COMMITTED",
  ];
  const unavailableStates = [
    "NO_VALID_SUGGESTION", "UNAVAILABLE", "REFUSED", "INVALID_RESPONSE", "PROVIDER_ERROR", "CONFIGURATION_ERROR", "SECURITY_ERROR",
  ];
  ok(fixed.includes(triple) || (operationKind === "suggestion-generate" && unavailableStates.includes(state) && parsedRecord.record_kind === "SUGGESTION_UNAVAILABLE"));
  if (parsedRecord.record_kind === "SUGGESTION_UNAVAILABLE") {
    const provenanceItems = parsedRecord.provenance;
    if (state === "NO_VALID_SUGGESTION") {
      candidateSequence(parsedRecord.candidates, 0, false, true);
      ok(provenanceItems.length >= 1 && parsedRecord.failure_code === "NO_VALID_SUGGESTION");
    } else {
      ok(parsedRecord.candidates.length === 0 && parsedRecord.coverage_gaps.length === 0);
      validateUnavailableState(state, parsedRecord.failure_code, provenanceItems);
    }
  }
  return value as StaffingReceipt;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function parsed<T>(value: unknown, validator: (candidate: unknown) => T, status: number, codeValue: string): T {
  try {
    const detached = clone(value);
    return validator(detached);
  } catch {
    throw new ManagerApiError(status, { code: codeValue, detail: "staffing data is invalid" });
  }
}

export function parseStaffingSnapshot(value: unknown): StaffingDateSnapshot {
  return parsed(value, validateSnapshot, 200, "invalid_staffing_response");
}

export function parseStaffingReceipt(value: unknown): StaffingReceipt {
  return parsed(value, validateReceipt, 200, "invalid_staffing_response");
}

export function parseStaffingRequestBody(value: unknown): StaffingRequestBody {
  return parsed(value, validateRequest, 400, "staffing_invalid_request");
}

function invalidResponse(status: number): ManagerApiError {
  return new ManagerApiError(status, {
    code: "invalid_staffing_response",
    detail: "staffing response is invalid",
  });
}

const TRUSTED_ERRORS = new Map<string, number>([
  ["staffing_invalid_request", 400],
  ["staffing_request_not_found", 404],
  ["staffing_not_found", 404],
  ["staffing_conflict", 409],
  ["staffing_exception_overlap", 409],
  ["staffing_stale_suggestion", 409],
  ["staffing_suggestion_expired", 409],
  ["staffing_busy", 429],
  ["staffing_unavailable", 503],
  ["body_too_large", 413],
]);

function trustedError(value: unknown, status: number): ManagerApiError {
  try {
    const envelope = record(value, ["schema", "disclaimer", "error"]);
    literal(envelope.schema, API_SCHEMA);
    literal(envelope.disclaimer, DISCLAIMER);
    const error = record(envelope.error, ["code", "detail"]);
    ok(typeof error.code === "string" && TRUSTED_ERRORS.get(error.code) === status);
    safeText(error.detail, 1, 500, true);
    return new ManagerApiError(status, { code: error.code, detail: "staffing request failed" });
  } catch {
    throw invalidResponse(status);
  }
}

function parseJsonWithoutDuplicateKeys(text: string): unknown {
  let offset = 0;
  const whitespace = () => {
    while (offset < text.length && /[\u0009\u000a\u000d\u0020]/.test(text[offset])) offset += 1;
  };
  const parseString = (): string => {
    const start = offset;
    ok(text[offset] === '"');
    offset += 1;
    while (offset < text.length) {
      const character = text[offset];
      if (character === '"') {
        offset += 1;
        return JSON.parse(text.slice(start, offset)) as string;
      }
      ok(character.charCodeAt(0) >= 0x20);
      if (character === "\\") {
        offset += 1;
        ok(offset < text.length);
        if (text[offset] === "u") {
          ok(/^[0-9a-fA-F]{4}$/.test(text.slice(offset + 1, offset + 5)));
          offset += 5;
          continue;
        }
        ok('"\\/bfnrt'.includes(text[offset]));
      }
      offset += 1;
    }
    throw new ValidationFailure();
  };
  const parseValue = (): void => {
    whitespace();
    const character = text[offset];
    if (character === "{") {
      offset += 1;
      whitespace();
      const keys = new Set<string>();
      if (text[offset] === "}") {
        offset += 1;
        return;
      }
      while (offset < text.length) {
        whitespace();
        const key = parseString();
        ok(!keys.has(key));
        keys.add(key);
        whitespace();
        ok(text[offset] === ":");
        offset += 1;
        parseValue();
        whitespace();
        if (text[offset] === "}") {
          offset += 1;
          return;
        }
        ok(text[offset] === ",");
        offset += 1;
      }
      throw new ValidationFailure();
    }
    if (character === "[") {
      offset += 1;
      whitespace();
      if (text[offset] === "]") {
        offset += 1;
        return;
      }
      while (offset < text.length) {
        parseValue();
        whitespace();
        if (text[offset] === "]") {
          offset += 1;
          return;
        }
        ok(text[offset] === ",");
        offset += 1;
      }
      throw new ValidationFailure();
    }
    if (character === '"') {
      parseString();
      return;
    }
    for (const token of ["true", "false", "null"] as const) {
      if (text.startsWith(token, offset)) {
        offset += token.length;
        return;
      }
    }
    const number = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(offset));
    ok(number !== null);
    offset += number[0].length;
  };
  parseValue();
  whitespace();
  ok(offset === text.length);
  return JSON.parse(text) as unknown;
}

async function decodeEnvelope<T>(responseValue: Response, successStatus: number, validator: (value: unknown) => T): Promise<T> {
  if (responseValue.bodyUsed) throw invalidResponse(responseValue.status);
  const responseText = await responseValue.text();
  let payload: unknown;
  try {
    payload = parseJsonWithoutDuplicateKeys(responseText);
  } catch {
    throw invalidResponse(responseValue.status);
  }
  if (responseValue.status !== successStatus) {
    if (responseValue.status >= 200 && responseValue.status < 300) throw invalidResponse(responseValue.status);
    throw trustedError(payload, responseValue.status);
  }
  try {
    const envelope = record(payload, ["schema", "disclaimer", "data"]);
    literal(envelope.schema, API_SCHEMA);
    literal(envelope.disclaimer, DISCLAIMER);
    validator(envelope.data);
    return clone(envelope.data as T);
  } catch {
    throw invalidResponse(responseValue.status);
  }
}

async function requestBounded<T>(
  fetchImpl: FetchLike,
  path: string,
  init: RequestInit,
  successStatus: number,
  validator: (value: unknown) => T,
): Promise<{ data: T; status: number }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const responseValue = await fetchImpl(path, {
      ...init,
      signal: controller.signal,
      cache: "no-store",
      mode: "same-origin",
      redirect: "error",
    });
    return {
      data: await decodeEnvelope(responseValue, successStatus, validator),
      status: responseValue.status,
    };
  } finally {
    clearTimeout(timer);
  }
}

function validateMutation(value: unknown): StaffingMutation {
  ok(value !== null && typeof value === "object" && !Array.isArray(value));
  const raw = value as Record<string, unknown>;
  const withTarget = raw.operationKind === "exception-cancel" || raw.operationKind === "exception-correct" || raw.operationKind === "manager-response";
  const item = record(value, withTarget ? ["operationKind", "targetId", "body"] : ["operationKind", "body"]);
  const operationKind = oneOf(item.operationKind, [
    "roster-import", "exception-record", "exception-cancel", "exception-correct", "suggestion-generate", "manager-response",
  ] as const);
  if (withTarget) identifier(item.targetId);
  const body = validateRequest(item.body);
  const schemaByKind: Record<OperationKind, StaffingRequestBody["schema"]> = {
    "roster-import": "nxt-staffing-roster-import/v1",
    "exception-record": "nxt-staffing-exception/v1",
    "exception-cancel": "nxt-staffing-exception-cancel/v1",
    "exception-correct": "nxt-staffing-exception-correct/v1",
    "suggestion-generate": "nxt-staffing-suggestion-generate/v1",
    "manager-response": "nxt-staffing-manager-response/v1",
  };
  ok(body.schema === schemaByKind[operationKind]);
  return value as StaffingMutation;
}

function mutationPath(mutation: StaffingMutation): string {
  switch (mutation.operationKind) {
    case "roster-import": return "/api/v1/staffing/roster-imports";
    case "exception-record": return "/api/v1/staffing/exceptions";
    case "exception-cancel": return `/api/v1/staffing/exceptions/${encodeURIComponent(mutation.targetId)}/cancel`;
    case "exception-correct": return `/api/v1/staffing/exceptions/${encodeURIComponent(mutation.targetId)}/correct`;
    case "suggestion-generate": return "/api/v1/staffing/suggestions";
    case "manager-response": return `/api/v1/staffing/suggestions/${encodeURIComponent(mutation.targetId)}/${mutation.body.kind.toLowerCase()}`;
  }
}

function minuteFromOffset(value: string): string {
  return value.slice(11, 16);
}

function sameRevisions(left: RevisionVector, right: RevisionVector): boolean {
  return left.roster === right.roster && left.exception_set === right.exception_set && left.effective_plan === right.effective_plan;
}

function correlate(mutation: StaffingMutation, receipt: StaffingReceipt): void {
  ok(receipt.operation_kind === mutation.operationKind && receipt.request_id === mutation.body.request_id);
  switch (mutation.operationKind) {
    case "roster-import":
      ok(receipt.record.record_kind === "ROSTER_IMPORTED");
      ok(receipt.record.worker_count === mutation.body.workers.length);
      ok(receipt.record.assignment_count === mutation.body.regular_assignments.length);
      ok(receipt.record.coverage_rule_count === mutation.body.coverage.length);
      return;
    case "exception-record":
      ok(receipt.record.record_kind === "EXCEPTION_RECORDED");
      ok(receipt.record.exception.staff_id === mutation.body.staff_id);
      ok(receipt.record.exception.kind === mutation.body.kind && receipt.record.exception.note === mutation.body.note);
      return;
    case "exception-cancel":
      ok(receipt.record.record_kind === "EXCEPTION_CANCELLED" && receipt.record.exception_id === mutation.targetId);
      return;
    case "exception-correct": {
      ok(receipt.record.record_kind === "EXCEPTION_CORRECTED");
      const replacement = receipt.record.replacement;
      ok(receipt.record.replaced_exception_id === mutation.targetId && replacement.exception_id === mutation.targetId);
      ok(replacement.kind === mutation.body.replacement.kind && replacement.note === mutation.body.replacement.note);
      if (mutation.body.replacement.kind === "LATE") {
        ok(minuteFromOffset(replacement.unavailable_end_at) === mutation.body.replacement.time_local);
      } else if (mutation.body.replacement.kind === "EARLY_DEPARTURE") {
        ok(minuteFromOffset(replacement.unavailable_start_at) === mutation.body.replacement.time_local);
      }
      return;
    }
    case "suggestion-generate": {
      ok(receipt.record.record_kind === "GENERATION_RESERVED" ||
        receipt.record.record_kind === "GENERATION_IN_PROGRESS" ||
        receipt.record.record_kind === "GENERATION_INTERRUPTED" ||
        receipt.record.record_kind === "SUGGESTION_ISSUED" ||
        receipt.record.record_kind === "SUGGESTION_UNAVAILABLE");
      ok(receipt.record.service_date === mutation.body.service_date);
      ok(receipt.record.retry_of === mutation.body.retry_of);
      ok(sameRevisions(receipt.record.basis, mutation.body.expected_revisions));
      return;
    }
    case "manager-response":
      ok(receipt.record.record_kind === "MANAGER_RESPONSE_COMMITTED");
      ok(receipt.record.suggestion_id === mutation.targetId);
      ok(receipt.record.response_kind === mutation.body.kind);
      ok(receipt.record.reason_code === mutation.body.reason_code);
      ok(receipt.record.operator === mutation.body.operator && receipt.record.note === mutation.body.note);
      if (mutation.body.kind === "REJECT") ok(receipt.record.effective_plan === null);
      else ok(receipt.record.effective_plan !== null && receipt.record.effective_plan.status === "CURRENT");
  }
}

export function createStaffingClient(fetchImpl: FetchLike = fetch): StaffingClient {
  const get = async <T>(path: string, validator: (value: unknown) => T): Promise<T> =>
    (await requestBounded(fetchImpl, path, {}, 200, validator)).data;

  const lookup = async (operationKind: OperationKind, requestId: string): Promise<StaffingReceipt> => {
    try {
      oneOf(operationKind, [
        "roster-import", "exception-record", "exception-cancel", "exception-correct", "suggestion-generate", "manager-response",
      ] as const);
      identifier(requestId);
    } catch {
      throw new ManagerApiError(400, { code: "staffing_invalid_request", detail: "staffing request is invalid" });
    }
    const receipt = await get(
      `/api/v1/staffing/requests/${encodeURIComponent(operationKind)}/${encodeURIComponent(requestId)}`,
      validateReceipt,
    );
    if (receipt.disposition !== "duplicate" || receipt.operation_kind !== operationKind || receipt.request_id !== requestId) {
      throw invalidResponse(200);
    }
    return receipt;
  };

  return {
    current: () => get("/api/v1/staffing", validateSnapshot),
    date: async (serviceDate) => {
      try { localDate(serviceDate); } catch { throw new ManagerApiError(400, { code: "staffing_invalid_request", detail: "staffing request is invalid" }); }
      const result = await get(`/api/v1/staffing/dates/${encodeURIComponent(serviceDate)}`, validateSnapshot);
      if (result.service_date !== serviceDate) throw invalidResponse(200);
      return result;
    },
    submit: async (mutationValue) => {
      let mutation: StaffingMutation;
      try { mutation = validateMutation(clone(mutationValue)); }
      catch { throw new ManagerApiError(400, { code: "staffing_invalid_request", detail: "staffing request is invalid" }); }
      const serialized = JSON.stringify(mutation.body);
      if (new TextEncoder().encode(serialized).byteLength > 1024 * 1024) {
        throw new ManagerApiError(413, { code: "body_too_large", detail: "staffing request body is too large" });
      }
      const expectedStatus = mutation.operationKind === "suggestion-generate" ? 202 : 200;
      const responseValue = await requestBounded(fetchImpl, mutationPath(mutation), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: serialized,
      }, expectedStatus, validateReceipt);
      const receipt = responseValue.data;
      try { correlate(mutation, receipt); }
      catch { throw invalidResponse(responseValue.status); }
      return receipt;
    },
    lookup,
    lookupMutation: async (mutationValue) => {
      let mutation: StaffingMutation;
      try { mutation = validateMutation(clone(mutationValue)); }
      catch { throw new ManagerApiError(400, { code: "staffing_invalid_request", detail: "staffing request is invalid" }); }
      const receipt = await lookup(mutation.operationKind, mutation.body.request_id);
      try { correlate(mutation, receipt); }
      catch { throw invalidResponse(200); }
      return receipt;
    },
  };
}

export function newStaffingRequestId(kind: OperationKind): string {
  oneOf(kind, [
    "roster-import", "exception-record", "exception-cancel", "exception-correct", "suggestion-generate", "manager-response",
  ] as const);
  return `staffing-${kind}-${crypto.randomUUID()}`;
}
