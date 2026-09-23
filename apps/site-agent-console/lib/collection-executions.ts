/** Strict read projection only. V3 runtime, device and API execution remain DESIGN ONLY — NOT IMPLEMENTED. */
import { API_SCHEMA, ManagerApiError, type FetchLike } from "./api";

export const COLLECTION_EXECUTIONS_SCHEMA = "nxt-collection-executions/v1";

type Validator<T> = (value: unknown) => value is T;
type Infer<V> = V extends Validator<infer T> ? T : never;
const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const text: Validator<string> = (v): v is string => typeof v === "string" && v.length > 0 && v.length <= 2048;
const id: Validator<string> = (v): v is string => typeof v === "string" && v.length <= 128 && /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(v);
const digest: Validator<string> = (v): v is string => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const taskId: Validator<string> = (v): v is string => typeof v === "string" && /^task_[a-f0-9]{24}$/.test(v);
const number: Validator<number> = (v): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= Number.MAX_SAFE_INTEGER;
const integer: Validator<number> = (v): v is number => number(v) && Number.isSafeInteger(v);
const positive: Validator<number> = (v): v is number => integer(v) && v > 0;
const boolean: Validator<boolean> = (v): v is boolean => typeof v === "boolean";
const literal = <const T extends string | boolean | null>(expected: T): Validator<T> => (v): v is T => v === expected;
const oneOf = <const T extends readonly string[]>(...values: T): Validator<T[number]> =>
  (v): v is T[number] => typeof v === "string" && values.includes(v);
const nullable = <T>(check: Validator<T>): Validator<T | null> => (v): v is T | null => v === null || check(v);
const array = <T>(check: Validator<T>, unique = false): Validator<T[]> => (v): v is T[] =>
  Array.isArray(v) && v.length <= 10000 && Array.from(v).every(check) && (!unique || new Set(v).size === v.length);
const shape = <T extends Record<string, Validator<unknown>>>(fields: T): Validator<{[K in keyof T]: Infer<T[K]>}> =>
  (v): v is {[K in keyof T]: Infer<T[K]>} => object(v) && Object.keys(v).length === Object.keys(fields).length &&
    Object.entries(fields).every(([key, check]) => Object.hasOwn(v, key) && check(v[key]));

// Date.parse alone normalizes impossible dates and truncates microseconds.
const timestamp: Validator<string> = (v): v is string => {
  if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(v) || v.startsWith("0000")) return false;
  const base = v.slice(0, 19), millis = Date.parse(`${base}Z`);
  return Number.isFinite(millis) && new Date(millis).toISOString().slice(0, 19) === base;
};
const micros = (utc: string) => BigInt(Date.parse(`${utc.slice(0, 19)}Z`)) * BigInt(1000) +
  BigInt((utc.split(".")[1]?.slice(0, -1) ?? "").padEnd(6, "0"));
const secondsMicros = (seconds: number) => BigInt(Math.floor(seconds)) * BigInt(1000000) +
  BigInt(Math.round((seconds - Math.floor(seconds)) * 1000000));
const projected = (epoch: string, seconds: number, utc: string) => micros(epoch) + secondsMicros(seconds) === micros(utc);

const cycleEvidence = shape({input_record_id: id, input_revision: positive, source_kind: oneOf("MEASURED", "MANUAL_ESTIMATE"),
  source_ref: text, observed_at_utc: timestamp, valid_until_utc: timestamp,
  cycle_minutes: shape({travel: number, collect: number, return: number, unload: number}),
  derivation_rule: literal("CEIL_TRAVEL_COLLECT_RETURN_UNLOAD_TO_CONTROL_INTERVAL_V1")});
const sessionFields = {series_id: id, session_id: id, round_id: id, round_index: integer, engine_digest: digest, config_digest: digest,
  session_epoch_utc: timestamp, control_interval_s: positive, session_end_sim_t_s: number};
const binding = shape({schema: literal("nxt-collection-execution-binding/v1"), environment: literal("SIMULATION"), binding_id: digest,
  site_id: id, deployment_id: id, commissioned_site_digest: digest, plan_id: id, plan_version: positive, confirmation_id: id,
  schedule_id: id, task_id: taskId, task_content_digest: digest, incarnation: id, robot_id: id, zone_id: id, ...sessionFields,
  runtime_robot_id: id, runtime_zone_id: id, handoff_station_id: id, max_execution_s: positive, cycle_evidence: cycleEvidence,
  handoff_binding_mode: literal("SOLE_SCENARIO_STATION_V1"), bound_at_sim_t_s: number, bound_at_utc: timestamp, task_created_at_utc: timestamp});
const request = shape({schema: literal("nxt-collection-execution-request/v1"), environment: literal("SIMULATION"),
  kind: literal("EXECUTE_BOUND_COLLECTION"), request_id: id, execution_id: digest, binding_id: digest,
  session_id: id, round_id: id, task_id: taskId, task_content_digest: digest, incarnation: id,
  due_at_utc: timestamp, expires_at_utc: timestamp, eligible_sim_t_s: number, latest_start_sim_t_s: number});
const receipt = shape({schema: literal("nxt-collection-execution-request-receipt/v1"), environment: literal("SIMULATION"),
  request_id: id, request_digest: digest, binding_id: digest, execution_id: digest, attempt_id: id, sequence: positive,
  request_log_high_water_digest: digest, durable: literal(true)});
const quantity = shape({milestone: oneOf("RAW_COLLECTED_TO_ROBOT", "UNLOADED_TO_STATION"),
  status: oneOf("NOT_REACHED", "COMPLETE", "INCOMPLETE"), balls: nullable(integer), source: literal("RANGE_SIMULATION_BALL_LEDGER"),
  assignment_id: nullable(id), source_event_ids: array(id, true), event_digest: nullable(digest), destination_id: id});
const action = shape({name: oneOf("Wait", "AssignCollection", "SendToHandoff", "SendToCharge", "PauseRobot", "ResumeRobot",
  "ReassignRobot", "RequestHumanAssistance", "AssignStaffWork"), index: integer, robot_id: nullable(id), target_id: nullable(id)});
const candidate = shape({execution_id: digest, eligible_sim_t_s: number, latest_start_sim_t_s: number});
const actionDecision = shape({sim_t_s: number, original_action: action, selected_action: action,
  selection: oneOf("WAIT_SLOT", "ORIGINAL_POLICY_UNCHANGED", "ORIGINAL_POLICY_CONVERGED", "POLICY_PREEMPTED", "RUNNING_CONTINUATION"),
  eligible_pending: array(candidate), safety_shield: oneOf("ACCEPTED", "REJECTED"), safety_reason: nullable(text)});
const collectionExitReasons = ["ROBOT_PAYLOAD_FULL", "ZONE_EMPTY", "COLLECTION_ACCESS_BLOCKED", "POLICY_PREEMPTED",
  "LOW_BATTERY", "ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED", "EXECUTION_TIMEOUT", "SESSION_ENDED"] as const;
const runtimeEvidence = shape({start_admitted: boolean, assignment_accepted: boolean, assignment_terminal: boolean,
  collection_exit_reason: nullable(oneOf(...collectionExitReasons)), event_sequence_complete: boolean,
  event_start_sequence: nullable(integer), event_end_sequence: nullable(integer), event_digest: nullable(digest),
  conservation_passed: nullable(boolean), payload_parity_passed: nullable(boolean)});
const edgeKnownReason = oneOf("robot_faulted", "estop_latched", "energy_insufficient", "capability_unavailable", "cannot_continue",
  "needs_manual_recharge", "unsafe_return_unconfirmed", "not_started_after_restart", "interrupted_execution_unknown_outcome",
  "robot_busy", "task_expired", "target_mismatch", "site_mismatch", "deployment_mismatch", "simulation_env_mismatch",
  "task_id_content_conflict", "incarnation_mismatch", "unsupported_task_type", "unsupported_schema", "invalid_request", "unknown_zone");
export type EdgeReason = Infer<typeof edgeKnownReason> | `unknown:${string}` | null;
const edgeReason: Validator<EdgeReason> = (v): v is EdgeReason => v === null || edgeKnownReason(v) ||
  (typeof v === "string" && /^unknown:[A-Za-z0-9_.:-]{1,96}$/.test(v));
const edgeEvidence = shape({accepted: boolean, task_id: taskId, verified: boolean,
  effective_state: oneOf("CREATED", "ACCEPTED", "RUNNING", "SUCCEEDED", "FAILED", "REJECTED", "INCONCLUSIVE", "CONFLICT"),
  reason: edgeReason, terminal_states: array(oneOf("SUCCEEDED", "FAILED", "REJECTED", "INCONCLUSIVE")),
  event_ids: array(id, true), result_verification: oneOf("VERIFIED", "UNVERIFIED", "CONFLICT")});
const protection = shape({protected: boolean, reasons: array(oneOf("RUNTIME_ACTIVE", "ROBOT_FAULT", "ESTOP_LATCHED",
  "HUMAN_ASSISTANCE_REQUIRED", "TERMINAL_CONFLICT", "SESSION_REGRESSION", "INCARNATION_MISMATCH", "ORPHANED_ACTIVITY", "RESTART_UNKNOWN"), true),
  authorization_blocked: boolean});
const conflicts = shape({missing_events: boolean, terminal_conflict: boolean, session_round_drift: boolean,
  incarnation_mismatch: boolean, replay_mismatch: boolean});
const record = shape({execution_id: digest, attempt_id: id, request_id: id, binding_id: digest, session_id: id, round_id: id,
  task_id: taskId, incarnation: id, runtime_robot_id: id, runtime_zone_id: id, handoff_station_id: id, assignment_id: nullable(id),
  state: oneOf("PENDING", "RUNNING", "SUCCEEDED", "PARTIAL", "REJECTED", "MISSED", "FAILED", "INCONCLUSIVE"),
  stage: oneOf("WAITING_FOR_POLICY_SLOT", "TRAVEL_TO_COLLECTION", "COLLECTING", "RAW_COLLECTED_TO_ROBOT", "TRAVEL_TO_UNLOAD",
    "UNLOADING", "UNLOADED_TO_STATION", "TERMINAL"),
  reason: nullable(oneOf("UNLOADED_ALL_COLLECTED_BALLS", "POLICY_SLOT_MISSED", "INSUFFICIENT_SESSION_HORIZON", "SAFETY_REJECTED",
    ...collectionExitReasons, "NOT_STARTED_AFTER_RESTART", "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME", "IDENTITY_CONFLICT",
    "TERMINAL_CONFLICT", "REPLAY_MISMATCH", "EVIDENCE_INCOMPLETE")),
  eligible_sim_t_s: number, latest_start_sim_t_s: number, started_sim_t_s: nullable(number), execution_deadline_sim_t_s: nullable(number),
  terminal_sim_t_s: nullable(number), max_execution_s: positive, arbiter_version: literal("WAIT_ONLY_NON_PREEMPTIVE_V1"), policy_id: id,
  actions: array(actionDecision), runtime_evidence: runtimeEvidence, edge_evidence: edgeEvidence, raw_quantity: quantity,
  unload_quantity: quantity, device_protection: protection, conflicts, reconciliation: literal("NOT_PERFORMED"), success_display_allowed: boolean});
const snapshot = shape({schema: literal(COLLECTION_EXECUTIONS_SCHEMA), environment: literal("SIMULATION"), ...sessionFields,
  session_state: oneOf("ACTIVE", "PAUSED", "ENDED"), now_sim_t_s: number, simulation_time_utc: timestamp, server_time_utc: timestamp,
  replay_digest: digest, bindings: array(binding), requests: array(request), receipts: array(receipt), executions: array(record)});

// Wire types are inferred from the same exact-key validators to prevent type/reader drift.
export type CycleEvidence = Infer<typeof cycleEvidence>;
export type Binding = Infer<typeof binding>;
export type ExecutionRequest = Infer<typeof request>;
export type RequestReceipt = Infer<typeof receipt>;
export type QuantityEvidence = Infer<typeof quantity>;
export type Action = Infer<typeof action>;
export type PendingCandidate = Infer<typeof candidate>;
export type ActionDecision = Infer<typeof actionDecision>;
export type RuntimeEvidence = Infer<typeof runtimeEvidence>;
export type EdgeEvidence = Infer<typeof edgeEvidence>;
export type DeviceProtection = Infer<typeof protection>;
export type Conflicts = Infer<typeof conflicts>;
export type ExecutionRecord = Infer<typeof record>;
export type CollectionExecutionsSnapshot = Infer<typeof snapshot>;
export type ExecutionSnapshot = CollectionExecutionsSnapshot;
export type SuccessEnvelope = {schema: typeof API_SCHEMA; disclaimer: string; data: CollectionExecutionsSnapshot | RequestReceipt};
const errorCode = oneOf("collection_execution_invalid_request", "collection_execution_not_found", "collection_execution_request_not_found",
  "collection_execution_conflict", "collection_execution_unavailable", "collection_execution_result_unknown");
const errorEnvelope = shape({schema: literal(API_SCHEMA), disclaimer: text, error: shape({code: errorCode, detail: text})});
export type ErrorEnvelope = Infer<typeof errorEnvelope>;
const successEnvelope = shape({schema: literal(API_SCHEMA), disclaimer: text,
  data: (value: unknown): value is unknown => value !== undefined});

function fail(status = 200): never {
  throw new ManagerApiError(status, {code: "invalid_collection_executions", detail: "Collection execution evidence is incomplete or inconsistent."});
}
function requireEvidence(condition: unknown): asserts condition { if (!condition) fail(); }
function unique<T, K extends keyof T>(items: T[], field: K): Map<T[K], T> {
  const indexed = new Map(items.map((item) => [item[field], item]));
  requireEvidence(indexed.size === items.length); return indexed;
}
function equalFields<A, B, K extends keyof A & keyof B>(left: A, right: B, keys: readonly K[]) {
  requireEvidence(keys.every((key) => Object.is(left[key], right[key])));
}
const sameAction = (a: Action, b: Action) => a.name === b.name && a.index === b.index && a.robot_id === b.robot_id && a.target_id === b.target_id;
const pendingOrder = (a: PendingCandidate, b: PendingCandidate) => a.latest_start_sim_t_s - b.latest_start_sim_t_s ||
  a.eligible_sim_t_s - b.eligible_sim_t_s || (a.execution_id < b.execution_id ? -1 : a.execution_id > b.execution_id ? 1 : 0);

/** Parse the data body. Digest shape and cross-links are checked; source/hash attestation remains the backend owner's responsibility. */
export function parseCollectionExecutions(value: unknown): CollectionExecutionsSnapshot {
  if (!snapshot(value)) return fail();
  const s = value;
  const bindings = unique(s.bindings, "binding_id"), requests = unique(s.requests, "request_id"), receipts = unique(s.receipts, "request_id");
  const executions = unique(s.executions, "execution_id");
  unique(s.receipts, "execution_id"); unique(s.receipts, "attempt_id"); unique(s.executions, "attempt_id"); unique(s.executions, "request_id");
  requireEvidence(requests.size === receipts.size && receipts.size === executions.size);
  requireEvidence(s.now_sim_t_s <= s.session_end_sim_t_s && projected(s.session_epoch_utc, s.now_sim_t_s, s.simulation_time_utc));
  requireEvidence(s.executions.filter((r) => r.state === "RUNNING").length <= 1);
  // A lease remains relevant in terminal history. Exact end-to-next-start adjacency is permitted.
  const started = s.executions.filter((r) => r.started_sim_t_s !== null).sort((a, b) => a.started_sim_t_s! - b.started_sim_t_s!);
  for (let i = 1; i < started.length; i++) {
    requireEvidence(started[i].started_sim_t_s !== started[i - 1].started_sim_t_s &&
      started[i].started_sim_t_s! >= (started[i - 1].terminal_sim_t_s ?? Infinity));
  }
  for (const b of s.bindings) {
    equalFields(b, s, ["series_id", "session_id", "round_id", "round_index", "engine_digest", "config_digest",
      "session_epoch_utc", "control_interval_s", "session_end_sim_t_s"]);
    const c = b.cycle_evidence, bound = micros(b.bound_at_utc);
    requireEvidence(b.bound_at_sim_t_s <= s.now_sim_t_s && projected(s.session_epoch_utc, b.bound_at_sim_t_s, b.bound_at_utc) &&
      micros(b.task_created_at_utc) <= bound && micros(c.observed_at_utc) <= bound && bound <= micros(c.valid_until_utc));
    const duration = Math.ceil(Object.values(c.cycle_minutes).reduce((sum, n) => sum + n, 0) * 60 / b.control_interval_s) * b.control_interval_s;
    requireEvidence(Number.isSafeInteger(duration) && b.max_execution_s === duration);
  }
  const tasks = new Set<string>();
  for (const q of s.requests) {
    const b = bindings.get(q.binding_id), receipt = receipts.get(q.request_id);
    requireEvidence(b && receipt && executions.has(q.execution_id));
    equalFields(q, b, ["session_id", "round_id", "task_id", "task_content_digest", "incarnation"]);
    equalFields(q, receipt, ["execution_id", "binding_id", "request_id"]);
    requireEvidence(q.eligible_sim_t_s < q.latest_start_sim_t_s && projected(s.session_epoch_utc, q.eligible_sim_t_s, q.due_at_utc) &&
      projected(s.session_epoch_utc, q.latest_start_sim_t_s, q.expires_at_utc));
    const key = JSON.stringify([q.task_id, q.session_id, q.binding_id]);
    requireEvidence(!tasks.has(key)); tasks.add(key);
  }
  for (const r of s.executions) {
    const q = requests.get(r.request_id), b = bindings.get(r.binding_id), receipt = receipts.get(r.request_id);
    requireEvidence(q && b && receipt);
    equalFields(r, q, ["execution_id", "binding_id", "eligible_sim_t_s", "latest_start_sim_t_s"]);
    equalFields(r, receipt, ["execution_id", "binding_id", "attempt_id"]);
    equalFields(r, b, ["session_id", "round_id", "task_id", "incarnation", "runtime_robot_id", "runtime_zone_id", "handoff_station_id", "max_execution_s"]);
    validateRecord(r, b, s);
  }
  return s;
}

function validateRecord(r: ExecutionRecord, b: Binding, s: CollectionExecutionsSnapshot) {
  const start = r.started_sim_t_s, end = r.terminal_sim_t_s, deadline = r.execution_deadline_sim_t_s;
  const runtime = r.runtime_evidence, edge = r.edge_evidence, raw = r.raw_quantity, unload = r.unload_quantity, p = r.device_protection;
  // A conflict projection may replace the display reason while retaining the causal runtime exit.
  const conflictOverlay = r.state === "INCONCLUSIVE" &&
    ((r.reason === "TERMINAL_CONFLICT" && r.conflicts.terminal_conflict) || (r.reason === "REPLAY_MISMATCH" && r.conflicts.replay_mismatch));
  if (start === null) {
    requireEvidence(deadline === null && r.assignment_id === null && !runtime.assignment_accepted && !runtime.start_admitted);
  } else {
    requireEvidence(r.eligible_sim_t_s <= start && start < r.latest_start_sim_t_s && b.bound_at_sim_t_s <= start && start <= s.now_sim_t_s &&
      deadline === start + r.max_execution_s && deadline <= s.session_end_sim_t_s && r.assignment_id !== null &&
      edge.accepted && runtime.assignment_accepted && runtime.start_admitted);
    const starts = r.actions.filter((a) => a.sim_t_s === start && a.selected_action.name === "AssignCollection");
    requireEvidence(starts.length === 1 && starts[0].selection === "WAIT_SLOT" && starts[0].safety_shield === "ACCEPTED");
  }
  const nonterminal = r.state === "PENDING" || r.state === "RUNNING";
  requireEvidence(nonterminal ? end === null && r.reason === null && r.stage !== "TERMINAL" :
    end !== null && end <= s.now_sim_t_s && end >= b.bound_at_sim_t_s && r.stage === "TERMINAL" && r.reason !== null && (start === null || start <= end));
  requireEvidence(r.success_display_allowed === (r.state === "SUCCEEDED") && (r.reason === "UNLOADED_ALL_COLLECTED_BALLS") === (r.state === "SUCCEEDED"));
  if (nonterminal) requireEvidence(s.session_state !== "ENDED");
  if (r.state === "PENDING") requireEvidence(start === null && r.stage === "WAITING_FOR_POLICY_SLOT" && edge.accepted &&
    edge.effective_state === "ACCEPTED" && s.now_sim_t_s < r.latest_start_sim_t_s &&
    Math.max(s.now_sim_t_s, r.eligible_sim_t_s) + r.max_execution_s <= s.session_end_sim_t_s);
  if (r.state === "RUNNING") requireEvidence(start !== null && r.stage !== "WAITING_FOR_POLICY_SLOT" && edge.accepted &&
    edge.effective_state === "RUNNING" && deadline !== null && s.now_sim_t_s < deadline);
  let previous = -1;
  for (const a of r.actions) {
    requireEvidence(previous <= a.sim_t_s && b.bound_at_sim_t_s <= a.sim_t_s && a.sim_t_s <= s.now_sim_t_s && (end === null || a.sim_t_s <= end));
    previous = a.sim_t_s;
    requireEvidence(a.safety_shield === "ACCEPTED" ? a.safety_reason === null : a.safety_reason !== null);
    for (const evidence of [a.original_action, a.selected_action]) {
      if (evidence.name === "Wait") requireEvidence(evidence.robot_id === null && evidence.target_id === null);
      if (evidence.name === "SendToHandoff") requireEvidence(evidence.robot_id !== null && evidence.target_id === null);
    }
    unique(a.eligible_pending, "execution_id");
    for (let i = 0; i < a.eligible_pending.length; i++) {
      const c = a.eligible_pending[i];
      requireEvidence(c.eligible_sim_t_s <= a.sim_t_s && a.sim_t_s < c.latest_start_sim_t_s && (i === 0 || pendingOrder(a.eligible_pending[i - 1], c) < 0));
      const included = s.executions.find((other) => other.execution_id === c.execution_id);
      if (included) {
        equalFields(c, included, ["eligible_sim_t_s", "latest_start_sim_t_s"]);
        requireEvidence((included.started_sim_t_s === null || a.sim_t_s <= included.started_sim_t_s) &&
          (included.terminal_sim_t_s === null || a.sim_t_s <= included.terminal_sim_t_s));
      }
    }
    const selected = a.selected_action;
    // Include the terminal-causing tick: an action cannot evade classification by ending its own lease.
    const duringLease = start !== null && start <= a.sim_t_s && (end === null || a.sim_t_s <= end);
    if (duringLease && a.original_action.name !== "Wait" && a.original_action.robot_id === b.runtime_robot_id) {
      requireEvidence(a.selection === (a.safety_shield === "REJECTED" ? "ORIGINAL_POLICY_UNCHANGED" :
        a.original_action.name === "SendToHandoff" ? "ORIGINAL_POLICY_CONVERGED" : "POLICY_PREEMPTED"));
    }
    if (a.selection === "WAIT_SLOT") {
      requireEvidence(a.original_action.name === "Wait" && a.eligible_pending[0]?.execution_id === r.execution_id &&
        selected.name === "AssignCollection" && selected.robot_id === b.runtime_robot_id && selected.target_id === b.runtime_zone_id &&
        (start === null || a.sim_t_s === start));
      requireEvidence(!s.executions.some((other) => other.execution_id !== r.execution_id && other.started_sim_t_s !== null &&
        other.started_sim_t_s <= a.sim_t_s && (other.terminal_sim_t_s === null || a.sim_t_s < other.terminal_sim_t_s)));
      if (a.safety_shield === "ACCEPTED") requireEvidence(start === a.sim_t_s);
    } else if (a.selection === "RUNNING_CONTINUATION") {
      requireEvidence(start !== null && deadline !== null && start < a.sim_t_s && a.sim_t_s < deadline && a.original_action.name === "Wait" &&
        (selected.name === "AssignCollection" || selected.name === "SendToHandoff") && selected.robot_id === b.runtime_robot_id &&
        selected.target_id === (selected.name === "AssignCollection" ? b.runtime_zone_id : null) &&
        a.eligible_pending.every((c) => c.execution_id !== r.execution_id));
    } else {
      requireEvidence(sameAction(a.original_action, selected));
      if (a.selection === "ORIGINAL_POLICY_CONVERGED") requireEvidence(a.safety_shield === "ACCEPTED" && start !== null && deadline !== null && start < a.sim_t_s && a.sim_t_s < deadline &&
        selected.name === "SendToHandoff" && selected.robot_id === b.runtime_robot_id && selected.target_id === null);
      if (a.selection === "POLICY_PREEMPTED") requireEvidence(a.safety_shield === "ACCEPTED" && duringLease && selected.name !== "Wait" && selected.robot_id === b.runtime_robot_id);
    }
  }
  const preempted = r.actions.some((a) => a.selection === "POLICY_PREEMPTED");
  if (preempted || r.reason === "POLICY_PREEMPTED" || runtime.collection_exit_reason === "POLICY_PREEMPTED") {
    requireEvidence(preempted && (r.reason === "POLICY_PREEMPTED" || conflictOverlay) && runtime.collection_exit_reason === "POLICY_PREEMPTED" &&
      ["PARTIAL", "FAILED", "INCONCLUSIVE"].includes(r.state));
  }
  for (const reason of ["ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED"] as const) {
    if (r.reason === reason || runtime.collection_exit_reason === reason) {
      requireEvidence((r.reason === reason || conflictOverlay) && runtime.collection_exit_reason === reason &&
        ["PARTIAL", "FAILED", "INCONCLUSIVE"].includes(r.state) && p.protected && p.authorization_blocked && p.reasons.includes(reason));
    }
  }
  if (r.reason === "EXECUTION_TIMEOUT" || runtime.collection_exit_reason === "EXECUTION_TIMEOUT") {
    requireEvidence((r.reason === "EXECUTION_TIMEOUT" || conflictOverlay) && runtime.collection_exit_reason === "EXECUTION_TIMEOUT" &&
      start !== null && end !== null && end === deadline && ["PARTIAL", "FAILED", "INCONCLUSIVE"].includes(r.state));
  }
  requireEvidence(raw.milestone === "RAW_COLLECTED_TO_ROBOT" && unload.milestone === "UNLOADED_TO_STATION" &&
    raw.destination_id === b.runtime_robot_id && unload.destination_id === b.handoff_station_id);
  for (const q of [raw, unload]) {
    requireEvidence(q.assignment_id === null || q.assignment_id === r.assignment_id);
    if (q.status === "COMPLETE") requireEvidence(q.balls !== null && q.assignment_id !== null && q.assignment_id === r.assignment_id &&
      q.source_event_ids.length > 0 && q.event_digest !== null);
    else requireEvidence(q.balls === null);
  }
  if (raw.status === "COMPLETE" && unload.status === "COMPLETE") requireEvidence(unload.balls! <= raw.balls!);
  requireEvidence((runtime.event_start_sequence === null) === (runtime.event_end_sequence === null));
  if (runtime.event_start_sequence !== null) requireEvidence(runtime.event_end_sequence! >= runtime.event_start_sequence && runtime.event_digest !== null);
  requireEvidence(edge.task_id === r.task_id && p.protected === (p.reasons.length > 0) && (!p.protected || p.authorization_blocked));
  const terminals = new Set(edge.terminal_states);
  const conflict = terminals.size > 1 || r.conflicts.terminal_conflict || r.conflicts.replay_mismatch;
  requireEvidence(edge.verified === (edge.result_verification === "VERIFIED"));
  if (conflict) {
    requireEvidence(r.state === "INCONCLUSIVE" && edge.effective_state === "CONFLICT" && edge.result_verification === "CONFLICT" &&
      !r.success_display_allowed && p.protected && p.authorization_blocked);
    if (terminals.size > 1) requireEvidence(r.conflicts.terminal_conflict && p.reasons.includes("TERMINAL_CONFLICT"));
  } else {
    requireEvidence(edge.effective_state !== "CONFLICT" && edge.result_verification !== "CONFLICT");
    requireEvidence(edge.terminal_states.every((state) => state === edge.effective_state));
  }
  if (["SUCCEEDED", "FAILED", "REJECTED"].includes(edge.effective_state))
    requireEvidence(terminals.size === 1 && edge.verified);
  if (nonterminal) requireEvidence(terminals.size === 0);
  if (r.state === "MISSED" || r.state === "REJECTED") requireEvidence(start === null && edge.effective_state === (edge.accepted ? "FAILED" : "REJECTED"));
  if (r.state === "SUCCEEDED") {
    requireEvidence(raw.status === "COMPLETE" && unload.status === "COMPLETE" && raw.balls! > 0 && raw.balls === unload.balls &&
      start !== null && end !== null && deadline !== null && end <= deadline && runtime.assignment_terminal && runtime.event_sequence_complete &&
      runtime.event_start_sequence !== null && runtime.event_end_sequence !== null && runtime.event_digest !== null &&
      runtime.conservation_passed === true && runtime.payload_parity_passed === true && runtime.collection_exit_reason === "ROBOT_PAYLOAD_FULL" &&
      !Object.values(r.conflicts).some(Boolean) && !p.protected && !p.authorization_blocked && edge.effective_state === "SUCCEEDED" && edge.verified && edge.accepted);
  } else if (r.state === "PARTIAL") {
    requireEvidence(edge.effective_state === "FAILED" && edge.reason === "unknown:partial_execution" && start !== null &&
      raw.status === "COMPLETE" && raw.balls! > 0 && unload.status !== "INCOMPLETE" && runtime.event_sequence_complete && runtime.assignment_terminal);
  } else if (r.state === "INCONCLUSIVE") {
    requireEvidence(["INCONCLUSIVE", "CONFLICT"].includes(edge.effective_state) && p.protected && (conflict || edge.result_verification === "UNVERIFIED"));
  } else if (r.state === "FAILED") requireEvidence(edge.effective_state === "FAILED" && raw.status !== "INCOMPLETE" &&
    unload.status !== "INCOMPLETE" && runtime.event_sequence_complete &&
    (start === null ? raw.balls === null : raw.status === "COMPLETE" && raw.balls === 0 && runtime.assignment_terminal));
  if (Object.values(r.conflicts).some(Boolean)) requireEvidence(r.state === "INCONCLUSIVE" && p.protected);
  if (r.reason === "POLICY_SLOT_MISSED") requireEvidence(r.state === "MISSED" && start === null && end !== null && end >= r.latest_start_sim_t_s && edge.reason === "unknown:policy_slot_missed");
  if (r.reason === "SAFETY_REJECTED") requireEvidence(r.state === "REJECTED" && start === null && edge.reason === "unknown:safety_rejected" && r.actions.some((a) => a.safety_shield === "REJECTED"));
  if (r.reason === "INSUFFICIENT_SESSION_HORIZON" || edge.reason === "unknown:insufficient_session_horizon") {
    requireEvidence(r.reason === "INSUFFICIENT_SESSION_HORIZON" && edge.reason === "unknown:insufficient_session_horizon" &&
      r.state === "MISSED" && start === null && end !== null && r.actions.length === 0 &&
      !runtime.assignment_terminal && runtime.collection_exit_reason === null && raw.status === "NOT_REACHED" && unload.status === "NOT_REACHED" &&
      Math.max(end, r.eligible_sim_t_s) + r.max_execution_s > s.session_end_sim_t_s);
  }
  if (r.reason === "NOT_STARTED_AFTER_RESTART" || edge.reason === "not_started_after_restart") {
    requireEvidence(r.reason === "NOT_STARTED_AFTER_RESTART" && edge.reason === "not_started_after_restart" &&
      r.state === "FAILED" && start === null && edge.accepted && edge.effective_state === "FAILED" && edge.verified &&
      edge.result_verification === "VERIFIED" && !runtime.assignment_terminal && runtime.collection_exit_reason === null &&
      raw.status === "NOT_REACHED" && unload.status === "NOT_REACHED");
  }
  if (r.reason === "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME" || edge.reason === "interrupted_execution_unknown_outcome") {
    requireEvidence(r.reason === "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME" && edge.reason === "interrupted_execution_unknown_outcome" &&
      r.state === "INCONCLUSIVE" && start !== null && edge.accepted && edge.effective_state === "INCONCLUSIVE" &&
      !edge.verified && edge.result_verification === "UNVERIFIED" && p.protected && p.authorization_blocked &&
      p.reasons.includes("RESTART_UNKNOWN") && raw.status === "INCOMPLETE" && unload.status === "INCOMPLETE" &&
      raw.balls === null && unload.balls === null);
  }
}

// JSON.parse checks syntax, then the token walk detects duplicate (including escaped) keys
// while preserving object scopes. Native decoding alone silently keeps the last claim.
function decodeJson(source: string): unknown {
  const value: unknown = JSON.parse(source);
  const tokens = source.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null/g) ?? [];
  const scopes: (Set<string> | null)[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token === "{") scopes.push(new Set());
    else if (token === "[") scopes.push(null);
    else if (token === "}" || token === "]") scopes.pop();
    else if (token.startsWith('"') && tokens[i + 1] === ":") {
      const key: string = JSON.parse(token), keys = scopes.at(-1);
      requireEvidence(keys && !keys.has(key)); keys.add(key);
    }
  }
  return value;
}

export function createCollectionExecutionsClient(fetchImpl: FetchLike) {
  return {async read(signal?: AbortSignal): Promise<CollectionExecutionsSnapshot> {
    const abort = new AbortController(), cancel = () => abort.abort();
    signal?.addEventListener("abort", cancel, {once: true});
    if (signal?.aborted) abort.abort();
    const timer = setTimeout(cancel, 8000);
    try {
      const response = await fetchImpl("/api/v1/collection-executions", {method: "GET", cache: "no-store", signal: abort.signal});
      let payload: unknown;
      try { payload = decodeJson(await response.text()); } catch { return fail(response.status); }
      if (!response.ok) {
        if (!errorEnvelope(payload)) return fail(response.status);
        throw new ManagerApiError(response.status, payload.error);
      }
      if (!successEnvelope(payload)) return fail(response.status);
      return parseCollectionExecutions(payload.data);
    } finally { clearTimeout(timer); signal?.removeEventListener("abort", cancel); }
  }};
}
