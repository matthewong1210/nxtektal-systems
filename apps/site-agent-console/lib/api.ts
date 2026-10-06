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
/** The one coherent Supervisor Snapshot served additively on the v0 transport. */
export const SUPERVISOR_SNAPSHOT_SCHEMA = "nxt-site-agent/supervisor-snapshot/v1";

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
    sensed_reading_age_s: number | null;
    reading_status: string | null;
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
  cycles: { admitted: number; rejected: number };
  counts: {
    no_action: number;
    pending_review: number;
    manager_decisions: number;
  };
  timeline: BriefingEntry[];
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

// ---------------------------------------------------------------------------
// Supervisor Snapshot: business operational context beside the five projections
// ---------------------------------------------------------------------------

/** The four evidence labels the operational-context owner emits. They are
 * never merged: a plan is not a record, a record is not a derivation, and
 * UNKNOWN always carries null and a reason, never a zero. */
export type EvidenceLabel = "PLANNED" | "SOURCE_RECORDED" | "DERIVED" | "UNKNOWN";

export interface ContextValue<T = unknown> {
  value: T | null;
  label: EvidenceLabel;
  basis: string;
  /** Freshness of the source this value came from: ok, stale or missing. */
  source_status: string;
  reason: string | null;
}

/** A section the service could not produce: no clock, no reader, or an
 * unreadable journal. Nothing partial is ever presented in its place. */
export interface UnavailableSection {
  status: "unavailable";
  code: string;
  detail: string;
}

export interface RoleCoverage {
  role_code: string;
  scheduled_now: number;
  present_now: number;
  unknown_now: number;
  absent_now: number;
  label: EvidenceLabel;
}

export interface OperatingDay {
  date: string;
  timezone: string;
  start_utc: string;
  end_utc: string;
}

export interface StaffingSection {
  status: string;
  operating_day: OperatingDay;
  scheduled_today: ContextValue<number>;
  scheduled_now: ContextValue<number>;
  confirmed_present_now: ContextValue<number>;
  confirmed_absent_today: ContextValue<number>;
  presence_unknown_now: ContextValue<number>;
  present_unscheduled_now: ContextValue<number>;
  present_scheduled_now?: ContextValue<number>;
  by_role: RoleCoverage[];
  worked_intervals_today: ContextValue<{ count: number; total_minutes: number }>;
  open_clock_ins: ContextValue<number>;
  next_material_change: ContextValue<{ at_utc: string; kind: string; count: number }>;
  approved_shift_changes_today: ContextValue<number>;
  pending_change_requests: ContextValue<number>;
  notes: string[];
}

export interface SalesSection {
  status: string;
  ball_units_sold_today: ContextValue<number>;
  transactions_today: ContextValue<number>;
  reversals_today: ContextValue<number>;
  unmapped_sku_transactions_today: ContextValue<number>;
  unmatched_reversals: ContextValue<number>;
  recent_window: ContextValue<{
    window_s: number;
    ball_units: number;
    transactions: number;
    window_fully_covered: boolean;
    coverage_end: string | null;
  }>;
  notes: string[];
}

export interface PlaySection {
  status: string;
  booked_sessions_today: ContextValue<number>;
  booked_players_today: ContextValue<number>;
  upcoming_booked_players: ContextValue<number>;
  started_sessions_today: ContextValue<number>;
  active_sessions_now: ContextValue<number>;
  active_players_booked: ContextValue<number>;
  active_players_confirmed: ContextValue<number>;
  completed_sessions_today: ContextValue<{
    count: number;
    mean_minutes: number | null;
    min_minutes: number | null;
    max_minutes: number | null;
  }>;
  sessions_missing_finish: ContextValue<number>;
  notes: string[];
}

export interface OperationsSection {
  status: string;
  sales: SalesSection;
  play: PlaySection;
}

export interface SourceFreshness {
  status: string;
  coverage_end: string | null;
  coverage_basis: string | null;
  age_s: number | null;
  stale_after_s: number | null;
  accepted_batches: number;
  rejected_batches: number;
  duplicate_batches: number;
  last_rejection: {
    import_batch_id: string | null;
    recorded_at: string | null;
    error_count: number | null;
    errors: { row_number: number | null; column: string | null; reason: string }[];
  } | null;
  reason: string | null;
}

export interface UnknownField {
  value: null;
  label: "UNKNOWN";
  reason: string;
}

export interface PhysicalStoreValue {
  value: number;
  label: string;
  physical: boolean;
  source_type: string;
  service_source: string;
  source_status: string | null;
  reason: null;
}

export interface SupervisorSnapshot {
  snapshot_schema: string;
  identity: {
    site_id: string;
    deployment_id: string;
    workflow_id: string;
    mode_label: string;
    fixture_mode: boolean;
    disclaimer: string;
    run_directory: string;
  };
  health: Health;
  state: StateProjection;
  recommendations: Recommendation[];
  briefing: Briefing;
  fixture: FixtureInfo;
  staffing: StaffingSection | UnavailableSection;
  operations: OperationsSection | UnavailableSection;
  operating_day: OperatingDay | null;
  context_sources: Record<string, SourceFreshness>;
  physical_stores: {
    clean_balls_in_dispenser: PhysicalStoreValue | UnknownField;
    clean_ball_weight_kg: UnknownField;
    awaiting_wash_balls: UnknownField;
    note: string;
  };
  machines: Record<string, UnknownField>;
  /** Reserved for a separately gated advisory slice; always empty here. */
  coverage_recommendations: unknown[];
  exceptions: { code: string; source_system: string | null; detail: string | null }[];
  data_quality: {
    context: {
      status: string;
      code: string | null;
      detail: string | null;
      as_of: string | null;
      as_of_basis: string | null;
      clock_declared: boolean;
      reader_declared: boolean;
    };
    facility: {
      state_available: boolean;
      missing_channels: string[];
      stale_channels: string[];
      service_state: string | null;
      degraded: boolean;
    };
  };
  generation: {
    snapshot_id: string;
    generated_at: string | null;
    clock_basis: string | null;
    scenario_now: string | null;
    run_directory: string;
  };
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
  // A null, primitive, or array payload has no envelope fields to read;
  // it must surface as the typed error, not an uncaught TypeError.
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ManagerApiError(response.status, {
      code: "unreadable_response",
      detail:
        `the service returned a non-object JSON payload (${response.status})`,
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
    supervisorSnapshot: () => get<SupervisorSnapshot>("/api/v0/supervisor-snapshot"),
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
