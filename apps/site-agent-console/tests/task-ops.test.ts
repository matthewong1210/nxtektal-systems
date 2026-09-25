import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { API_SCHEMA, ManagerApiError } from "../lib/api";
import {
  TASK_OPS_WRITE_OPERATIONS,
  buildSchedule,
  canPerformTaskOps,
  createTaskOpsClient,
  humanResponse,
  isAmbiguousMutation,
  localTimeToUtc,
  parseTaskOps,
  taskOpsCapabilities,
  taskOpsSupports,
  type TaskOpsView,
} from "../lib/task-ops";
import { taskOpsFixture } from "./task-ops-fixtures";

const envelope = (data: unknown, status = 200) => new Response(JSON.stringify({ schema: API_SCHEMA, disclaimer: "SIMULATION", data }), { status });
const CAPABILITIES_EXAMPLES = join(
  import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts",
  "pilot-dispatch-v0", "service-capabilities", "examples",
);
const CAPABILITIES_V2_EXAMPLES = join(
  import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts",
  "pilot-dispatch-v0", "service-capabilities-v2", "examples",
);
const capabilityExample = (name: string): unknown =>
  JSON.parse(readFileSync(join(CAPABILITIES_EXAMPLES, `${name}.json`), "utf-8"));
const continuousCapabilities = JSON.parse(
  readFileSync(join(CAPABILITIES_V2_EXAMPLES, "continuous-v3-execution.json"), "utf-8"),
) as Record<string, unknown>;
const originalTz = process.env.TZ;
afterEach(() => { if (originalTz === undefined) delete process.env.TZ; else process.env.TZ = originalTz; });

describe("task operations contracts", () => {
  const fixedV3Capabilities = capabilityExample("fixed-v3-execution") as Record<string, unknown>;
  const legacyCapabilities = capabilityExample("legacy-pilot-dispatch") as Record<string, unknown>;

  it("accepts additive evidence fields and preserves absent results", () => {
    const payload = { ...taskOpsFixture(), future_field: true };
    expect(parseTaskOps(payload).tasks["task-1"].result_verification).toBeNull();
  });
  it("refuses live/unknown schemas, missingness and malformed collections before enabling changes", () => {
    for (const patch of [{ environment: "LIVE" }, { schema: "future/v9" }, { schedules: null },
      { tasks: [] }, { scheduler: { state: "OK", detail: null } }, { available_robots: [true] },
      { server_time_utc: "yesterday" }, { notifications: [{ can_resolve: true }] }]) {
      expect(() => parseTaskOps({ ...taskOpsFixture(), ...patch })).toThrow(ManagerApiError);
    }
  });
  it("refuses unknown schedule states and missing device read-time freshness", () => {
    const sample = taskOpsFixture();
    expect(() => parseTaskOps({ ...sample, schedules: [{ ...sample.schedules[0], status: "NEW" }] })).toThrow();
    expect(() => parseTaskOps({ ...sample, devices: { "picker-01": { robot_id: "picker-01" } } })).toThrow();
  });
  it("validates fixed V3 capabilities per operation without treating notifications as globally unavailable", () => {
    const parsed = parseTaskOps({ ...taskOpsFixture(), service_capabilities: fixedV3Capabilities });
    expect(taskOpsCapabilities(parsed)).toEqual({ declared: true, ...fixedV3Capabilities });
    expect(taskOpsSupports(parsed, "planning_confirmations_create")).toBe(false);
    expect(taskOpsSupports(parsed, "schedules_create")).toBe(false);
    expect(taskOpsSupports(parsed, "planning_outcomes_create")).toBe(true);
    expect(taskOpsSupports(parsed, "notifications_acknowledge")).toBe(true);
    expect(taskOpsSupports(parsed, "notifications_resolve")).toBe(true);
  });
  it("keeps the explicit legacy mode distinct and preserves its installed write routes", () => {
    const parsed = parseTaskOps({ ...taskOpsFixture(), service_capabilities: legacyCapabilities });
    expect(taskOpsCapabilities(parsed)).toEqual({ declared: true, ...legacyCapabilities });
    for (const operation of TASK_OPS_WRITE_OPERATIONS) expect(taskOpsSupports(parsed, operation)).toBe(true);
  });
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
  it("keeps an undeclared historical payload readable but grants no write capability", () => {
    const historical = taskOpsFixture() as TaskOpsView["data"] & Record<string, unknown>;
    delete historical.service_capabilities;
    const parsed = parseTaskOps({
      ...historical,
      transport: "mqtt",
      runtime: { fixed_confirmation: false, accepts_new_confirmations: true, accepts_new_schedules: true },
    });
    const capabilities = taskOpsCapabilities(parsed);
    expect(capabilities.mode).toBe("UNDECLARED");
    expect(capabilities.declared).toBe(false);
    for (const operation of TASK_OPS_WRITE_OPERATIONS) expect(taskOpsSupports(parsed, operation)).toBe(false);
  });
  it("fails loudly when a declared capability block is partial, unknown or malformed", () => {
    const incomplete = structuredClone(fixedV3Capabilities) as Record<string, unknown>;
    delete (incomplete.operations as Record<string, unknown>).schedules_cancel;
    const wrongStatus = structuredClone(fixedV3Capabilities);
    (wrongStatus.operations as Record<string, unknown>).schedules_create = true;
    const inconsistent = structuredClone(fixedV3Capabilities);
    (inconsistent.operations as Record<string, unknown>).schedules_create = "SUPPORTED";
    const extraOperation = structuredClone(fixedV3Capabilities);
    (extraOperation.operations as Record<string, unknown>).invented_write = "SUPPORTED";
    for (const service_capabilities of [
      incomplete,
      wrongStatus,
      inconsistent,
      extraOperation,
      { ...fixedV3Capabilities, schema: "nxt-pilot-dispatch/service-capabilities/v2" },
      { ...fixedV3Capabilities, mode: "AUTO_DETECT" },
    ]) {
      expect(() => parseTaskOps({ ...taskOpsFixture(), service_capabilities })).toThrow(ManagerApiError);
    }
  });
  it("combines explicit per-operation support with view health instead of transport success", () => {
    const snapshot = parseTaskOps({ ...taskOpsFixture(), service_capabilities: fixedV3Capabilities });
    const healthy: TaskOpsView = { data: snapshot, loading: false, busy: false, error: null, unavailable: false };
    expect(canPerformTaskOps(healthy, "schedules_create")).toBe(false);
    expect(canPerformTaskOps(healthy, "notifications_acknowledge")).toBe(true);
    expect(canPerformTaskOps({ ...healthy, busy: true }, "notifications_acknowledge")).toBe(false);
    expect(canPerformTaskOps({ ...healthy, data: { ...snapshot, scheduler: { state: "FAILED", detail: "stopped" } } }, "notifications_acknowledge")).toBe(false);
  });
  it("uses only the same-origin endpoint and unwraps checked snapshots", async () => {
    const fetcher = vi.fn(async () => envelope(taskOpsFixture()));
    expect((await createTaskOpsClient(fetcher).read()).environment).toBe("SIMULATION");
    expect(fetcher).toHaveBeenCalledWith("/api/v0/task-ops", expect.objectContaining({ method: "GET", cache: "no-store" }));
  });
  it("recognizes a legacy 404 even when the static demo returns HTML", async () => {
    const client = createTaskOpsClient(async () => new Response("Not found", { status: 404 }));
    await expect(client.read()).rejects.toMatchObject({ status: 404, code: "task_ops_unavailable" });
  });
  it("encodes record IDs and requires trimmed operator and note for human handling", async () => {
    const fetcher = vi.fn(async () => envelope({ status: "ACKNOWLEDGED" }));
    await createTaskOpsClient(fetcher).respond("notification/1", "acknowledge", { operator: " staff ", note: " Checking picker " });
    expect(fetcher).toHaveBeenCalledWith("/api/v0/task-ops/notifications/notification%2F1/acknowledge", expect.objectContaining({
      method: "POST", body: JSON.stringify({ operator: "staff", note: "Checking picker" }),
    }));
    expect(() => humanResponse("staff", " ")).toThrow();
    expect(() => humanResponse(" ", "Checked" )).toThrow();
  });
  it("does not regenerate or add fields to a schedule payload", async () => {
    const input = { robot_id: "picker-01", zone_id: "Z1", operator: "staff", due_at_utc: "2026-09-16T13:00:00.000Z", expires_at_utc: "2026-09-16T13:10:00.000Z" };
    const fetcher = vi.fn(async (_input: string, _init?: RequestInit) => envelope({ schedule_id: "schedule-1" }));
    const client = createTaskOpsClient(fetcher);
    await client.schedule(input);
    await client.schedule(input);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v0/task-ops/schedules");
    expect(fetcher.mock.calls[0][1]?.body).toBe(JSON.stringify(input));
    expect(fetcher.mock.calls[1][1]?.body).toBe(fetcher.mock.calls[0][1]?.body);
  });
  it("classifies failed delivery separately from a definite rejection", () => {
    expect(isAmbiguousMutation(new TypeError("Failed to fetch"))).toBe(true);
    expect(isAmbiguousMutation(new ManagerApiError(503, { code: "unavailable", detail: "" }))).toBe(true);
    expect(isAmbiguousMutation(new ManagerApiError(200, { code: "bad_json", detail: "" }))).toBe(true);
    expect(isAmbiguousMutation(new ManagerApiError(409, { code: "refused", detail: "" }))).toBe(false);
  });
});

describe("dated local schedule", () => {
  it("converts Asia/Shanghai input to UTC and computes an explicit expiry", () => {
    process.env.TZ = "Asia/Shanghai";
    expect(buildSchedule({ robot: "picker-01", zone: "Z1", operator: " staff ", localTime: "2026-09-16T20:05", validityMinutes: "10" }, Date.parse("2026-09-16T12:00:00Z"))).toEqual({
      robot_id: "picker-01", zone_id: "Z1", operator: "staff", due_at_utc: "2026-09-16T12:05:00.000Z", expires_at_utc: "2026-09-16T12:15:00.000Z",
    });
  });
  it("uses the selected date's timezone offset, including seasonal changes", () => {
    process.env.TZ = "America/New_York";
    expect(localTimeToUtc("2026-01-15T09:00")).toBe("2026-01-15T14:00:00.000Z");
    expect(localTimeToUtc("2026-07-15T09:00")).toBe("2026-07-15T13:00:00.000Z");
    expect(() => localTimeToUtc("2026-03-08T02:30")).toThrow("does not exist");
  });
  it("rejects invalid calendar dates, past times, missing identity and non-integer validity", () => {
    process.env.TZ = "UTC";
    expect(() => localTimeToUtc("2026-02-30T10:00")).toThrow();
    expect(() => localTimeToUtc("2026-09-16")).toThrow();
    const valid = { robot: "picker-01", zone: "Z1", operator: "staff", localTime: "2026-09-16T13:00", validityMinutes: "10" };
    for (const patch of [{ operator: " " }, { robot: "" }, { validityMinutes: "0" }, { validityMinutes: "1.5" }, { validityMinutes: "NaN" }, { validityMinutes: "1441" }]) {
      expect(() => buildSchedule({ ...valid, ...patch }, 0)).toThrow();
    }
    expect(() => buildSchedule(valid, Date.parse("2026-09-17T00:00:00Z"))).toThrow("future");
  });
});
