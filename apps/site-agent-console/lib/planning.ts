/**
 * Typed client and wire types for the human-led planning API v1.
 *
 * Shapes follow `simulation/docs/contracts/planning-v1/schema.json`; the
 * README there defines the semantics. The console transports these
 * records and renders them. It computes no stockout, ranks no candidate,
 * persists no effective setting, and never turns advice into a task.
 */

import { API_SCHEMA, ManagerApiError, type FetchLike } from "./api";

export const PLANNING_SCHEMA = "nxt-planning/v1";
export const INPUT_SCHEMA = "nxt-planning-input/v1";
export const PLAN_REQUEST_SCHEMA = "nxt-planning-request/v1";
export const CONFIRMATION_SCHEMA = "nxt-planning-confirmation/v1";
export const OUTCOME_SCHEMA = "nxt-planning-outcome/v1";
export const RULES_VERSION = "human-led-ball-supply/v1";

export type SourceKind = "MEASURED" | "MANUAL_ESTIMATE";
export type Scope = "SHIFT" | "DAY" | "ONE_TASK";
export type Stage = "COLLECTED" | "UNLOADED" | "WASHED" | "SUPPLIED";
export type ScenarioLevel = "low" | "typical" | "high";
export type PlanStatus = "READY" | "MISSING_DATA" | "EXPIRED" | "INFEASIBLE";
export type PlanCurrentStatus = PlanStatus | "INVALIDATED" | "CONFIRMED";
export type WriteKind = "inputs" | "plans" | "confirmations" | "outcomes";

export const STAGES: Stage[] = ["COLLECTED", "UNLOADED", "WASHED", "SUPPLIED"];
export const SCOPES: Scope[] = ["SHIFT", "DAY", "ONE_TASK"];

export interface Evidence<V, U extends string> {
  value: V;
  source_kind: SourceKind;
  source_ref: string;
  observed_at_utc: string;
  valid_until_utc: string;
  unit: U;
}

export interface DemandRates {
  bucket_minutes: number;
  low: number[];
  typical: number[];
  high: number[];
}

export interface YieldRange {
  low: number;
  high: number;
}

export interface CycleStages {
  travel: number;
  collect: number;
  return: number;
  unload: number;
  wash: number;
  supply: number;
}

export type BallsEvidence = Evidence<number, "balls">;
export type MinutesEvidence = Evidence<number, "minutes">;
export type BooleanEvidence = Evidence<boolean, "boolean">;
export type DemandEvidence = Evidence<DemandRates, "balls/minute">;
export type YieldEvidence = Evidence<YieldRange, "balls">;
export type CycleEvidence = Evidence<CycleStages, "minutes">;

export interface ZoneInput {
  zone_id: string;
  robot_id: string;
  collection_allowed: BooleanEvidence | null;
  clean_yield_balls: YieldEvidence | null;
  cycle_minutes: CycleEvidence | null;
}

export interface OperatingWindow {
  start_at_utc: string;
  end_at_utc: string;
}

export interface InputRequest {
  schema: typeof INPUT_SCHEMA;
  request_id: string;
  expected_revision: number;
  site_id: string;
  deployment_id: string;
  site_timezone: string;
  operator: string;
  reason: string;
  scope: Scope;
  effective_at_utc: string;
  valid_until_utc: string;
  operating_window: OperatingWindow;
  inventory_clean_balls: BallsEvidence | null;
  demand: DemandEvidence | null;
  safety_stock_balls: BallsEvidence | null;
  buffer_minutes: MinutesEvidence | null;
  operations_allowed: BooleanEvidence | null;
  washer_available: BooleanEvidence | null;
  zones: ZoneInput[];
}

export interface Change {
  path: string;
  before: unknown;
  after: unknown;
}

export interface InputRecord extends InputRequest {
  revision: number;
  input_digest: string;
  recorded_at_utc: string;
  changes: Change[];
}

export interface Selection {
  zone_id: string;
  robot_id: string;
  start_at_utc: string;
}

export interface PlanRequest {
  schema: typeof PLAN_REQUEST_SCHEMA;
  request_id: string;
  plan_id: string | null;
  expected_plan_version: number;
  input_revision: number;
  operator: string;
  reason: string;
  scope: Scope;
  valid_until_utc: string;
  selection: Selection | null;
}

export interface Adjustment {
  before: Selection | null;
  after: Selection | null;
  operator: string;
  reason: string;
  scope: Scope;
  valid_until_utc: string;
}

export interface Scenario {
  level: ScenarioLevel;
  baseline_stockout_at_utc: string | null;
  safety_stock_at_utc: string | null;
  with_plan_stockout_at_utc: string | null;
  baseline_end_balls: number;
  with_plan_end_balls: number;
}

export interface Candidate {
  zone_id: string;
  robot_id: string;
  eligible: boolean;
  exclusion_reasons: string[];
  start_at_utc: string | null;
  latest_start_at_utc: string | null;
  supply_at_utc: string | null;
  cycle_minutes: number | null;
  clean_yield_balls: YieldRange | null;
  protects_high_demand_horizon: boolean | null;
}

export interface PlanRecord {
  plan_id: string;
  version: number;
  request: PlanRequest;
  input_revision: number;
  input_digest: string;
  rules_version: typeof RULES_VERSION;
  generated_at_utc: string;
  valid_until_utc: string;
  status: PlanStatus;
  missing: string[];
  rationale: string[];
  system_selection: Selection | null;
  selection: Selection | null;
  adjustment: Adjustment;
  scenarios: Scenario[];
  candidates: Candidate[];
  current_status?: PlanCurrentStatus;
}

export interface ConfirmationRequest {
  schema: typeof CONFIRMATION_SCHEMA;
  request_id: string;
  plan_id: string;
  plan_version: number;
  operator: string;
}

export interface ScheduleCreationPayload {
  robot_id: string;
  zone_id: string;
  due_at_utc: string;
  expires_at_utc: string;
  operator: string;
  admission_reference: string;
}

export type ScheduleStatus = "SCHEDULED" | "DISPATCHED" | "CANCELLED" | "REJECTED" | "MISSED";

export interface ConfirmationRecord {
  confirmation_id: string;
  request: ConfirmationRequest;
  plan_id: string;
  plan_version: number;
  input_revision: number;
  confirmed_at_utc: string;
  schedule_id: string;
  schedule: ScheduleCreationPayload;
  schedule_status?: ScheduleStatus | null;
  task_id?: string | null;
  /** Verified Edge TASK_CREATED time: a lower bound on stage start, not stage-start
   * evidence. Snapshot parsing maps legacy absence to null; durable receipts omit it.
   * Never substitute a planned, confirmed, or current time. */
  task_created_at_utc?: string | null;
}

export interface OutcomeRequest {
  schema: typeof OUTCOME_SCHEMA;
  request_id: string;
  confirmation_id: string;
  task_id: string;
  operator: string;
  reason: string;
  stage: Stage;
  quantity_balls: number;
  started_at_utc: string;
  completed_at_utc: string;
  source_kind: SourceKind;
  source_ref: string;
  supersedes_outcome_id: string | null;
}

export interface OutcomeRecord {
  outcome_id: string;
  recorded_at_utc: string;
  evidence_kind: "ACTUAL_EXECUTION_RESULT";
  request: OutcomeRequest;
  plan_id: string;
  plan_version: number;
  input_revision: number;
  schedule_id: string;
}

export interface PlanningContext {
  site_id: string;
  deployment_id: string;
  site_timezone: string;
  zone_ids: string[];
  robot_ids: string[];
}

export interface PlanningSnapshot {
  schema: typeof PLANNING_SCHEMA;
  environment: "SIMULATION";
  mode: "MANUAL_LED";
  server_time_utc: string;
  context: PlanningContext;
  latest_input: InputRecord | null;
  plans: PlanRecord[];
  confirmations: ConfirmationRecord[];
  outcomes: OutcomeRecord[];
}

export type PlanningRecord = InputRecord | PlanRecord | ConfirmationRecord | OutcomeRecord;
export type PlanningRequestBody = InputRequest | PlanRequest | ConfirmationRequest | OutcomeRequest;

export interface MutationReceipt<R extends PlanningRecord = PlanningRecord> {
  schema: typeof PLANNING_SCHEMA;
  disposition: "created" | "duplicate";
  request_id: string;
  record: R;
}

export const PLANNING_ERROR_CODES = [
  "planning_invalid_request",
  "planning_not_found",
  "planning_request_not_found",
  "planning_conflict",
  "planning_expired",
  "planning_not_ready",
  "planning_unavailable",
  "planning_result_unknown",
] as const;

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text);
const timestamp = (value: unknown): value is string =>
  typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value);
const admissionTimestamp = (value: unknown): value is string => {
  if (!timestamp(value) || Number(value.slice(0, 4)) === 0) return false;
  const parsed = new Date(value);
  // Check calendar validity without replacing or rounding the original UTC text.
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 19) === value.slice(0, 19);
};
const integer = (value: unknown, minimum: number): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= minimum;

const invalid = (detail: string) =>
  new ManagerApiError(200, { code: "invalid_planning_snapshot", detail });

/** Validate the fields the UI reads. Unknown additive fields stay compatible;
 * a foreign schema, mode, or environment fails closed. Server-provided values
 * stay intact; only absent legacy task admission time is normalized to null. */
export function parsePlanningSnapshot(value: unknown): PlanningSnapshot {
  if (!object(value)) throw invalid("the planning view is not an object");
  if (value.schema !== PLANNING_SCHEMA) throw invalid(`expected ${PLANNING_SCHEMA}, got ${String(value.schema)}`);
  if (value.environment !== "SIMULATION") throw invalid("the planning view is not a SIMULATION view");
  if (value.mode !== "MANUAL_LED") throw invalid("the planning view is not in MANUAL_LED mode");
  if (!timestamp(value.server_time_utc)) throw invalid("server_time_utc is not RFC3339 UTC");
  const context = value.context;
  if (
    !object(context) ||
    !text(context.site_id) ||
    !text(context.deployment_id) ||
    !text(context.site_timezone) ||
    !strings(context.zone_ids) ||
    !strings(context.robot_ids)
  ) {
    throw invalid("the planning context is incomplete");
  }
  const latest = value.latest_input;
  if (latest !== null && !(object(latest) && integer(latest.revision, 1) && text(latest.request_id))) {
    throw invalid("latest_input is neither null nor a persisted input record");
  }
  if (!Array.isArray(value.plans) || !Array.isArray(value.confirmations) || !Array.isArray(value.outcomes)) {
    throw invalid("plans, confirmations and outcomes must be arrays");
  }
  for (const plan of value.plans) {
    if (
      !object(plan) ||
      !text(plan.plan_id) ||
      !integer(plan.version, 1) ||
      !text(plan.status) ||
      !Array.isArray(plan.scenarios) ||
      !Array.isArray(plan.candidates) ||
      !Array.isArray(plan.missing) ||
      !Array.isArray(plan.rationale) ||
      !object(plan.adjustment) ||
      !timestamp(plan.valid_until_utc)
    ) {
      throw invalid("a plan record is incomplete");
    }
  }
  const confirmations = value.confirmations.map((confirmation) => {
    if (
      !object(confirmation) ||
      !text(confirmation.confirmation_id) ||
      !text(confirmation.plan_id) ||
      !integer(confirmation.plan_version, 1) ||
      !text(confirmation.schedule_id) ||
      !object(confirmation.schedule) ||
      !object(confirmation.request)
    ) {
      throw invalid("a confirmation record is incomplete");
    }
    if (
      "task_created_at_utc" in confirmation &&
      confirmation.task_created_at_utc !== null &&
      !admissionTimestamp(confirmation.task_created_at_utc)
    ) {
      throw invalid("task_created_at_utc is neither null nor RFC3339 UTC");
    }
    return { ...confirmation, task_created_at_utc: confirmation.task_created_at_utc ?? null };
  });
  for (const outcome of value.outcomes) {
    if (!object(outcome) || !text(outcome.outcome_id) || !object(outcome.request) || !text(outcome.request.stage)) {
      throw invalid("an outcome record is incomplete");
    }
  }
  return { ...value, confirmations } as unknown as PlanningSnapshot;
}

export function parseReceipt(value: unknown): MutationReceipt {
  if (
    !object(value) ||
    value.schema !== PLANNING_SCHEMA ||
    (value.disposition !== "created" && value.disposition !== "duplicate") ||
    !text(value.request_id) ||
    !object(value.record)
  ) {
    throw new ManagerApiError(200, {
      code: "invalid_planning_receipt",
      detail: "the service returned an unsupported planning receipt",
    });
  }
  return value as unknown as MutationReceipt;
}

/** One caller-generated stable ID per write attempt; retained verbatim
 * across recovery of that same write. */
export function newRequestId(kind: WriteKind): string {
  return `${kind}-${crypto.randomUUID()}`;
}

export interface PlanningClient {
  snapshot(): Promise<PlanningSnapshot>;
  submit(kind: WriteKind, body: PlanningRequestBody): Promise<MutationReceipt>;
  lookup(requestId: string): Promise<MutationReceipt>;
}

export function createPlanningClient(fetchImpl: FetchLike): PlanningClient {
  async function request(path: string, body?: unknown): Promise<unknown> {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 8_000);
    try {
      const response = await fetchImpl(`/api/v1/planning${path}`, {
        method: body === undefined ? "GET" : "POST",
        cache: "no-store",
        signal: abort.signal,
        ...(body === undefined
          ? {}
          : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
      });
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        throw new ManagerApiError(response.status, {
          code: "unreadable_response",
          detail: "The planning service response could not be read.",
        });
      }
      if (!object(payload) || payload.schema !== API_SCHEMA || !text(payload.disclaimer)) {
        throw new ManagerApiError(response.status, {
          code: "schema_mismatch",
          detail: "Unexpected Manager API envelope.",
        });
      }
      if (!response.ok) {
        const error = object(payload.error) ? payload.error : {};
        throw new ManagerApiError(response.status, {
          code: text(error.code) ? error.code : "planning_error",
          detail: text(error.detail) ? error.detail : `The request failed (${response.status}).`,
        });
      }
      if (!("data" in payload)) {
        throw new ManagerApiError(response.status, {
          code: "unreadable_response",
          detail: "The planning service response has no data.",
        });
      }
      return payload.data;
    } finally {
      clearTimeout(timer);
    }
  }
  return {
    snapshot: async () => parsePlanningSnapshot(await request("")),
    submit: async (kind, body) => parseReceipt(await request(`/${kind}`, body)),
    lookup: async (requestId) => parseReceipt(await request(`/requests/${encodeURIComponent(requestId)}`)),
  };
}
