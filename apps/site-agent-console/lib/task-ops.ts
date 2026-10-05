import { API_SCHEMA, ManagerApiError, type FetchLike } from "./api";

export const TASK_OPS_SCHEMA = "nxt-pilot-dispatch/v0";
export const TASK_OPS_POLL_MS = 2_000;
export const TASK_OPS_CAPABILITIES_SCHEMA = "nxt-pilot-dispatch/service-capabilities/v1";
export const TASK_OPS_WRITE_OPERATIONS = [
  "planning_inputs_create",
  "planning_plans_create",
  "planning_confirmations_create",
  "planning_outcomes_create",
  "schedules_create",
  "schedules_cancel",
  "notifications_acknowledge",
  "notifications_resolve",
] as const;

export type TaskOpsWriteOperation = (typeof TASK_OPS_WRITE_OPERATIONS)[number];
export type TaskOpsCapabilityStatus = "SUPPORTED" | "UNAVAILABLE";
export type TaskOpsServiceMode = "FIXED_V3_EXECUTION" | "LEGACY_PILOT_DISPATCH";
export interface TaskOpsServiceCapabilities {
  schema: typeof TASK_OPS_CAPABILITIES_SCHEMA;
  mode: TaskOpsServiceMode;
  operations: Record<TaskOpsWriteOperation, TaskOpsCapabilityStatus>;
}
export type EffectiveTaskOpsCapabilities =
  | ({ declared: true } & TaskOpsServiceCapabilities)
  | {
      declared: false;
      schema: null;
      mode: "UNDECLARED";
      operations: Record<TaskOpsWriteOperation, "UNAVAILABLE">;
    };

export interface Schedule {
  schedule_id: string;
  robot_id: string;
  zone_id: string;
  due_at_utc: string;
  expires_at_utc: string;
  operator: string;
  progress_window_s: number;
  status: "SCHEDULED" | "DISPATCHED" | "REJECTED" | "MISSED" | "CANCELLED";
  task_id: string | null;
  reason_code: string | null;
  detail: string | null;
}

export interface TaskNotification {
  notification_id: string;
  source_record_id: string;
  robot_id: string | null;
  task_id: string | null;
  schedule_id: string | null;
  reason_code: string;
  detail: string;
  created_at_utc: string;
  status: "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
  condition_active: boolean;
  can_resolve: boolean;
  acknowledged_by: string | null;
  resolved_by: string | null;
}

export interface TaskSummary {
  task_id: string;
  target_robot_id: string;
  zone_id: string;
  state: string;
  effective_result: string | null;
  result_verification: string | null;
  acceptance_observed: boolean;
  evidence_incomplete: boolean;
  reconciliation_required: boolean;
  reconciliation_reasons: string[];
  last_progress_at_utc: string | null;
  expires_at_utc: string;
}

export interface DeviceSummary {
  robot_id: string;
  connectivity: string;
  last_reported_availability: string | null;
  session_regression: boolean;
  as_read: {
    connectivity: string;
    status_age_s: number | null;
    basis: string;
  };
}

export interface TaskOpsSnapshot {
  schema: typeof TASK_OPS_SCHEMA;
  environment: "SIMULATION";
  server_time_utc: string;
  scheduler: { state: "RUNNING" | "FAILED"; detail: string | null };
  /** Optional only for historical payload compatibility. Missing means no
   * write capability is declared; it never means legacy full access. */
  service_capabilities?: TaskOpsServiceCapabilities;
  schedules: Schedule[];
  notifications: TaskNotification[];
  devices: Record<string, DeviceSummary>;
  tasks: Record<string, TaskSummary>;
  available_robots: string[];
  available_zones: string[];
  transport: "in_memory" | "mqtt";
}

export interface ScheduleInput {
  robot_id: string;
  zone_id: string;
  due_at_utc: string;
  expires_at_utc: string;
  operator: string;
  progress_window_s?: number;
}

export interface HumanResponse {
  operator: string;
  note: string;
}

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string";
const nullableText = (value: unknown) => value === null || text(value);
const strings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every(text);
const timestamp = (value: unknown): value is string =>
  text(value) && /Z$/.test(value) && Number.isFinite(Date.parse(value));
const exactKeys = (value: Record<string, unknown>, expected: readonly string[]) =>
  Object.keys(value).length === expected.length && expected.every((key) => key in value);

const FIXED_V3_OPERATIONS: Record<TaskOpsWriteOperation, TaskOpsCapabilityStatus> = {
  planning_inputs_create: "UNAVAILABLE",
  planning_plans_create: "UNAVAILABLE",
  planning_confirmations_create: "UNAVAILABLE",
  planning_outcomes_create: "SUPPORTED",
  schedules_create: "UNAVAILABLE",
  schedules_cancel: "UNAVAILABLE",
  notifications_acknowledge: "SUPPORTED",
  notifications_resolve: "SUPPORTED",
};
const LEGACY_OPERATIONS: Record<TaskOpsWriteOperation, TaskOpsCapabilityStatus> = Object.fromEntries(
  TASK_OPS_WRITE_OPERATIONS.map((operation) => [operation, "SUPPORTED"]),
) as Record<TaskOpsWriteOperation, TaskOpsCapabilityStatus>;
const UNDECLARED_OPERATIONS = Object.fromEntries(
  TASK_OPS_WRITE_OPERATIONS.map((operation) => [operation, "UNAVAILABLE"]),
) as Record<TaskOpsWriteOperation, "UNAVAILABLE">;
const UNDECLARED_CAPABILITIES: EffectiveTaskOpsCapabilities = {
  declared: false,
  schema: null,
  mode: "UNDECLARED",
  operations: UNDECLARED_OPERATIONS,
};

function validServiceCapabilities(value: unknown): value is TaskOpsServiceCapabilities {
  if (!object(value) || !exactKeys(value, ["schema", "mode", "operations"]) ||
      value.schema !== TASK_OPS_CAPABILITIES_SCHEMA ||
      !["FIXED_V3_EXECUTION", "LEGACY_PILOT_DISPATCH"].includes(String(value.mode))) return false;
  const operations = value.operations;
  if (!object(operations) || !exactKeys(operations, TASK_OPS_WRITE_OPERATIONS)) return false;
  const mode = value.mode as TaskOpsServiceMode;
  const expected = mode === "FIXED_V3_EXECUTION" ? FIXED_V3_OPERATIONS : LEGACY_OPERATIONS;
  return TASK_OPS_WRITE_OPERATIONS.every((operation) =>
    ["SUPPORTED", "UNAVAILABLE"].includes(String(operations[operation])) &&
    operations[operation] === expected[operation]);
}

/** Validate the fields the UI reads before enabling any form. Unknown additive
 * projection fields remain compatible; missing values never become defaults. */
export function parseTaskOps(value: unknown): TaskOpsSnapshot {
  const fail = () => { throw new ManagerApiError(200, {
    code: "invalid_task_ops", detail: "The task service returned an unsupported or incomplete simulation view.",
  }); };
  if (!object(value) || value.schema !== TASK_OPS_SCHEMA || value.environment !== "SIMULATION" ||
      !timestamp(value.server_time_utc) || !object(value.scheduler) ||
      !["RUNNING", "FAILED"].includes(String(value.scheduler.state)) ||
      !nullableText(value.scheduler.detail) || !strings(value.available_robots) ||
      !strings(value.available_zones) || !["in_memory", "mqtt"].includes(String(value.transport)) ||
      !Array.isArray(value.schedules) || !Array.isArray(value.notifications) ||
      !object(value.tasks) || !object(value.devices)) return fail();
  if ("service_capabilities" in value &&
      !validServiceCapabilities(value.service_capabilities)) return fail();
  for (const item of value.schedules) {
    if (!object(item) || !text(item.schedule_id) || !text(item.robot_id) || !text(item.zone_id) ||
        !text(item.operator) || !timestamp(item.due_at_utc) || !timestamp(item.expires_at_utc) ||
        !["SCHEDULED", "DISPATCHED", "REJECTED", "MISSED", "CANCELLED"].includes(String(item.status)) ||
        !nullableText(item.task_id) || !nullableText(item.reason_code) || !nullableText(item.detail) ||
        typeof item.progress_window_s !== "number" || !Number.isFinite(item.progress_window_s)) return fail();
  }
  for (const item of value.notifications) {
    if (!object(item) || !text(item.notification_id) || !text(item.source_record_id) ||
        !nullableText(item.robot_id) || !nullableText(item.task_id) || !nullableText(item.schedule_id) ||
        !text(item.reason_code) || !text(item.detail) || !timestamp(item.created_at_utc) ||
        !["OPEN", "ACKNOWLEDGED", "RESOLVED"].includes(String(item.status)) ||
        typeof item.condition_active !== "boolean" || typeof item.can_resolve !== "boolean" ||
        !nullableText(item.acknowledged_by) || !nullableText(item.resolved_by)) return fail();
  }
  for (const item of Object.values(value.devices)) {
    if (!object(item) || !text(item.robot_id) || !text(item.connectivity) ||
        !nullableText(item.last_reported_availability) || typeof item.session_regression !== "boolean" ||
        !object(item.as_read) || !text(item.as_read.connectivity) || !text(item.as_read.basis) ||
        !(item.as_read.status_age_s === null || (typeof item.as_read.status_age_s === "number" &&
          Number.isFinite(item.as_read.status_age_s) && item.as_read.status_age_s >= 0))) return fail();
  }
  for (const item of Object.values(value.tasks)) {
    if (!object(item) || !text(item.task_id) || !text(item.target_robot_id) || !text(item.zone_id) ||
        !text(item.state) || !nullableText(item.effective_result) || !nullableText(item.result_verification) ||
        typeof item.acceptance_observed !== "boolean" || typeof item.evidence_incomplete !== "boolean" ||
        typeof item.reconciliation_required !== "boolean" || !strings(item.reconciliation_reasons) ||
        !(item.last_progress_at_utc === null || timestamp(item.last_progress_at_utc)) ||
        !timestamp(item.expires_at_utc)) return fail();
  }
  return value as unknown as TaskOpsSnapshot;
}

export function createTaskOpsClient(fetchImpl: FetchLike) {
  async function request(path: string, body?: unknown): Promise<unknown> {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 8_000);
    try {
      const response = await fetchImpl(`/api/v0/task-ops${path}`, {
        method: body === undefined ? "GET" : "POST",
        cache: "no-store", signal: abort.signal,
        ...(body === undefined ? {} : {
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        }),
      });
      if (response.status === 404) throw new ManagerApiError(404, {
        code: "task_ops_unavailable", detail: "This service does not provide pilot task operations.",
      });
      let payload: unknown;
      try { payload = await response.json(); } catch {
        throw new ManagerApiError(response.status, { code: "unreadable_response", detail: "The task service response could not be read." });
      }
      if (!object(payload) || payload.schema !== API_SCHEMA || !text(payload.disclaimer)) {
        throw new ManagerApiError(response.status, { code: "schema_mismatch", detail: "Unexpected Manager API envelope." });
      }
      if (!response.ok) {
        const error = object(payload.error) ? payload.error : {};
        throw new ManagerApiError(response.status, {
          code: text(error.code) ? error.code : "task_ops_error",
          detail: text(error.detail) ? error.detail : `The request failed (${response.status}).`,
        });
      }
      if (!("data" in payload)) throw new ManagerApiError(response.status, {
        code: "unreadable_response", detail: "The task service response has no data.",
      });
      return payload.data;
    } finally { clearTimeout(timer); }
  }
  return {
    read: async () => parseTaskOps(await request("")),
    schedule: (input: ScheduleInput) => request("/schedules", input),
    cancel: (id: string, operator: string) => request(`/schedules/${encodeURIComponent(id)}/cancel`, { operator: operator.trim() }),
    respond: (id: string, kind: "acknowledge" | "resolve", input: HumanResponse) =>
      request(`/notifications/${encodeURIComponent(id)}/${kind}`, humanResponse(input.operator, input.note)),
  };
}

/** datetime-local intentionally uses the browser's local timezone. The round
 * trip rejects impossible calendar dates and times skipped by a DST change. */
export function localTimeToUtc(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) throw new Error("Choose a complete local start date and time.");
  const [year, month, day, hour, minute] = value.split(/[-T:]/).map(Number);
  const date = new Date(year, month - 1, day, hour, minute);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day ||
      date.getHours() !== hour || date.getMinutes() !== minute) {
    throw new Error("That local time does not exist. Check the date or daylight-saving change.");
  }
  return date.toISOString();
}

export function buildSchedule(input: {
  robot: string; zone: string; localTime: string; validityMinutes: string; operator: string;
}, now = Date.now()): ScheduleInput {
  if (!input.operator.trim()) throw new Error("Enter the operator responsible for this schedule.");
  if (!input.robot || !input.zone) throw new Error("Select an available robot and zone.");
  const due = localTimeToUtc(input.localTime);
  const duration = Number(input.validityMinutes);
  if (!Number.isInteger(duration) || duration < 1 || duration > 1440) {
    throw new Error("Task validity must be between 1 and 1440 whole minutes.");
  }
  if (Date.parse(due) <= now) throw new Error("Choose a start time in the future.");
  return { robot_id: input.robot, zone_id: input.zone, due_at_utc: due,
    expires_at_utc: new Date(Date.parse(due) + duration * 60_000).toISOString(), operator: input.operator.trim() };
}

export function humanResponse(operator: string, note: string): HumanResponse {
  if (!operator.trim() || !note.trim()) throw new Error("Enter an operator and a handling note.");
  return { operator: operator.trim(), note: note.trim() };
}

export function isAmbiguousMutation(cause: unknown): boolean {
  return !(cause instanceof ManagerApiError) || cause.status >= 500 || cause.status < 400;
}

export interface TaskOpsView {
  data: TaskOpsSnapshot | null;
  loading: boolean;
  busy: boolean;
  error: string | null;
  unavailable: boolean;
}
export const INITIAL_TASK_OPS_VIEW: TaskOpsView = {
  data: null, loading: true, busy: false, error: null, unavailable: false,
};
export const canMutateTaskOps = (view: TaskOpsView) =>
  !!view.data && !view.busy && !view.loading && !view.error && !view.unavailable && view.data.scheduler.state === "RUNNING";

/** Return a validated service declaration. Historical payloads remain readable
 * but normalize to an explicit fail-closed state; transport, runtime fields and
 * HTTP success never grant an operation. */
export function taskOpsCapabilities(snapshot: TaskOpsSnapshot): EffectiveTaskOpsCapabilities {
  return snapshot.service_capabilities === undefined
    ? UNDECLARED_CAPABILITIES
    : { declared: true, ...snapshot.service_capabilities };
}

export function taskOpsSupports(snapshot: TaskOpsSnapshot, operation: TaskOpsWriteOperation): boolean {
  return taskOpsCapabilities(snapshot).operations[operation] === "SUPPORTED";
}

/** Static service support and dynamic read/scheduler health are independent
 * gates. Record-level preconditions (for example notification.can_resolve)
 * remain an additional caller and server responsibility. */
export function canPerformTaskOps(view: TaskOpsView, operation: TaskOpsWriteOperation): boolean {
  return canMutateTaskOps(view) && view.data !== null && taskOpsSupports(view.data, operation);
}

/** One read at a time. Mutations invalidate pending reads before any network
 * work, and perform a fresh read while controls remain busy. The saved view is
 * presentation only; schedules and responses are always owned by the API. */
export function createTaskOpsPoller(read: () => Promise<TaskOpsSnapshot>, publish: (view: TaskOpsView) => void) {
  let view = { ...INITIAL_TASK_OPS_VIEW };
  let active = false;
  let generation = 0;
  let pending: Promise<void> | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const emit = (patch: Partial<TaskOpsView>) => {
    view = { ...view, ...patch };
    if (active) publish(view);
  };
  const next = () => {
    clearTimeout(timer);
    if (active && !view.busy) timer = setTimeout(() => { void refresh(); }, TASK_OPS_POLL_MS);
  };
  async function refresh(): Promise<void> {
    if (!active) return;
    if (pending) return pending;
    const ownGeneration = generation;
    pending = (async () => {
      try {
        const data = await read();
        if (active && generation === ownGeneration) emit({ data, error: null, unavailable: false, loading: false });
      } catch (cause) {
        if (active && generation === ownGeneration) emit({
          error: cause instanceof Error ? cause.message : String(cause), loading: false,
          unavailable: cause instanceof ManagerApiError && cause.status === 404,
        });
      }
    })();
    try { await pending; } finally { pending = null; next(); }
  }
  return {
    start() { active = true; emit({}); void refresh(); },
    stop() { active = false; generation += 1; clearTimeout(timer); },
    refresh,
    async mutate(operation: () => Promise<unknown>): Promise<void> {
      if (!active || !canMutateTaskOps(view)) throw new Error("Wait for a healthy task service before changing tasks.");
      generation += 1;
      clearTimeout(timer);
      emit({ busy: true });
      try {
        await operation();
        if (pending) await pending;
        await refresh();
      } catch (cause) {
        if (isAmbiguousMutation(cause)) emit({ error: "The request outcome is uncertain. Refreshing the service view before further actions." });
        if (pending) await pending;
        await refresh();
        throw cause;
      } finally { emit({ busy: false }); next(); }
    },
  };
}
