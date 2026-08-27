/**
 * Typed client for the Pilot Site Agent Manager API (v0).
 *
 * The console consumes only this versioned, same-origin local API. It
 * imports no Python Site OS package, holds no authoritative state of
 * its own, and reconstructs its entire view from these endpoints
 * after every refresh. All payloads are noncanonical projections and
 * carry the fixture disclaimer end to end.
 */

export const API_SCHEMA = "nxt-site-agent/api/v0";
export const DISCLAIMER = "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA";

export interface SourceCursor {
  consumed_cycles: number;
  next_sequence_number: number;
}

export interface RuntimeStatus {
  runtime_state: string;
  degraded: boolean;
  cycles_completed: number;
  evaluations_completed: number;
  source_exhausted: boolean;
  last_observed_sequence: number | null;
  last_published_sequence: number | null;
  last_evaluated_sequence: number | null;
  last_verdict: string | null;
  pending_decision_count: number;
  last_observation_timestamp_s: number | null;
  last_effective_confidence: number | null;
  last_failure_code: string | null;
  last_failure_detail: string | null;
}

export interface Health {
  service_state: string;
  mode_label: string;
  fixture_mode: boolean;
  source_type: string;
  degraded: boolean;
  site_id: string;
  deployment_id: string;
  workflow_id: string;
  workflow_readiness: string;
  report_id: string | null;
  run_directory: string;
  runtime: RuntimeStatus;
  source: {
    cursor: SourceCursor | null;
    declared_cycles: number;
    exhausted: boolean;
    max_cycles: number;
  };
  pending_recommendation_count: number;
  last_failure_code: string | null;
  last_failure_detail: string | null;
  event_append_failures: number;
}

export interface SourceReference {
  channel: string;
  status: string;
  confidence: number;
  sample_timestamp_s: number;
  available_timestamp_s: number;
  calibration_id: string | null;
  source_id?: string;
  source_type?: string;
}

export interface AssemblyReport {
  missing_channels: string[];
  stale_channels: string[];
  consistency_issues: string[];
  overall_confidence: number;
  provenance_grade: string;
}

export interface StateProjection {
  available: boolean;
  reason: string | null;
  envelope: {
    envelope_id: string;
    sequence_number: number;
    observation_timestamp_s: number;
    site_id: string;
    deployment_id: string;
  } | null;
  dispenser: {
    clean_available_balls: number | null;
    clean_sensed_balls: number | null;
    count_source: SourceReference | null;
    sensed_source: SourceReference | null;
    reading_age_s: number | null;
  } | null;
  facility_meta?: {
    t_s: number | null;
    minute_of_day: number | null;
    facility_open: boolean | null;
    scenario_name: string | null;
  };
  quality: {
    assembly_report: AssemblyReport | null;
    runtime_quality: {
      assembly_confidence: number;
      upstream_confidence: number;
      effective_confidence: number;
    } | null;
  } | null;
  source_references?: SourceReference[];
}

export interface TraceSummary {
  trace_id: string | null;
  policy_id: string | null;
  policy_version: string | null;
  rationale: string[];
  missing_data_reasons: string[];
  data_completeness_score: number | null;
  selected_robot_id: string | null;
  candidates: {
    robot_id: string | null;
    eligible: boolean | null;
    exclusion_reasons: string[];
  }[];
  projected_stockout_without_action_minutes: number | null;
}

export interface Evaluation {
  evaluation_id: string;
  sequence_number: number;
  envelope_id: string;
  observation_timestamp_s: number;
  observed_at: string;
  verdict: string;
  policy_id: string;
  policy_version: string;
  trace_id: string;
  recommendation_id: string | null;
  recommendation_action: string | null;
  ledger_event_id: string | null;
  trace: TraceSummary | null;
}

export interface ManagerResponse {
  response_id?: string;
  kind: string;
  operator_id: string;
  reason_code: string;
  note: string | null;
  responded_at: string;
}

export interface Recommendation {
  recommendation_id: string;
  action: string;
  target_robot_id: string | null;
  summary: string;
  policy_id: string;
  policy_version: string;
  trace_id: string;
  issued_at: string;
  execute_before: string;
  case_status: string;
  response_kind: string | null;
  source_envelope_id: string | null;
  source_sequence: number | null;
  evaluation_id: string | null;
  recommendation: Record<string, unknown> | null;
  trace: TraceSummary | null;
  manager_response: ManagerResponse | null;
}

export interface BriefingEntry {
  tag: string;
  text: string;
  scenario_t_s: number | null;
  scenario_time: string | null;
  references: Record<string, unknown>;
}

export interface BriefingException {
  kind: string;
  tag: string;
  failure_code?: string | null;
  detail?: string | null;
  channel?: string;
  scenario_time?: string | null;
  cycle_label?: string | null;
}

export interface Briefing {
  disclaimer: string;
  identity: {
    site_id: string;
    deployment_id: string;
    workflow_id: string;
    mode_label: string;
    run_directory: string;
  };
  current_state: StateProjection;
  cycles: { admitted: number; rejected: number };
  timeline: BriefingEntry[];
  no_action_records: Evaluation[];
  pending_review: Recommendation[];
  manager_decisions: Recommendation[];
  exceptions: BriefingException[];
  unresolved: string[];
}

export interface CycleCatalogEntry {
  cycle_index: number;
  label: string;
  scenario_t_s: number;
  scenario_time: string;
  variant: string;
  source: string;
}

export interface FixtureInfo {
  fixture_mode: boolean;
  disclaimer: string;
  cycle_catalog: CycleCatalogEntry[];
  cursor: SourceCursor;
  next_cycle: CycleCatalogEntry | null;
  controls: { advance: boolean; restart: boolean; reset: boolean };
}

export interface ApiError {
  code: string;
  detail: string;
}

export class ManagerApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(status: number, error: ApiError) {
    super(`${error.code}: ${error.detail}`);
    this.code = error.code;
    this.status = status;
  }
}

type Envelope<T> = { schema: string; disclaimer: string; data: T };
type ErrorEnvelope = { schema: string; disclaimer: string; error: ApiError };

export type FetchLike = (
  input: string,
  init?: RequestInit,
) => Promise<Response>;

async function decode<T>(response: Response): Promise<T> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ManagerApiError(response.status, {
      code: "unreadable_response",
      detail: `the service returned a non-JSON response (${response.status})`,
    });
  }
  if (!response.ok) {
    const error = (payload as ErrorEnvelope).error ?? {
      code: "unknown_error",
      detail: `unexpected status ${response.status}`,
    };
    throw new ManagerApiError(response.status, error);
  }
  const envelope = payload as Envelope<T>;
  if (envelope.schema !== API_SCHEMA) {
    throw new ManagerApiError(response.status, {
      code: "schema_mismatch",
      detail: `expected ${API_SCHEMA}, got ${String(envelope.schema)}`,
    });
  }
  return envelope.data;
}

export interface RespondInput {
  operator_id: string;
  reason_code: string;
  note?: string;
  replacement_action?: string;
  replacement_robot_id?: string;
  replacement_execute_before?: string;
}

export function createClient(fetchImpl: FetchLike, base = "") {
  const get = async <T>(path: string): Promise<T> =>
    decode<T>(await fetchImpl(`${base}${path}`, { cache: "no-store" }));
  const post = async <T>(path: string, body?: unknown): Promise<T> =>
    decode<T>(
      await fetchImpl(`${base}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body ?? {}),
      }),
    );
  return {
    health: () => get<Health>("/api/v0/health"),
    state: () => get<StateProjection>("/api/v0/state"),
    evaluations: () => get<Evaluation[]>("/api/v0/evaluations"),
    recommendations: () => get<Recommendation[]>("/api/v0/recommendations"),
    briefing: () => get<Briefing>("/api/v0/briefing"),
    fixture: () => get<FixtureInfo>("/api/v0/demo"),
    respond: (recommendationId: string, kind: string, input: RespondInput) =>
      post<Recommendation>(
        `/api/v0/recommendations/${encodeURIComponent(recommendationId)}/${kind}`,
        input,
      ),
    advance: () => post<Record<string, unknown>>("/api/v0/demo/advance"),
    restart: () => post<Health>("/api/v0/demo/restart"),
    reset: () => post<Health>("/api/v0/demo/reset"),
  };
}

export type ManagerApiClient = ReturnType<typeof createClient>;
