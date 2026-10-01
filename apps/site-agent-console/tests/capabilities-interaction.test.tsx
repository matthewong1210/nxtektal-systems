// @vitest-environment happy-dom
/**
 * Mounted capability gating in PilotOperations over a scripted service. The
 * declaration comes from the frozen service-capabilities examples through
 * the real parser; SUPPORTED never bypasses health, validation, busy or
 * record-level preconditions. Not a live integration.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PilotOperations } from "../components/PilotOperations";
import { PlanningPanel } from "../components/PlanningPanel";
import { API_SCHEMA, DISCLAIMER } from "../lib/api";
import type { ConfirmationRecord, InputRecord, OutcomeRecord, PlanRecord, PlanningSnapshot } from "../lib/planning";
import type { SchedulerHealth } from "../lib/scheduler-health";
import {
  parseTaskOps,
  taskOpsCapabilities,
  TASK_OPS_POLL_MS,
  type EffectiveTaskOpsCapabilities,
  type TaskOpsSnapshot,
} from "../lib/task-ops";
import { taskOpsFixture } from "./task-ops-fixtures";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const CONTRACTS = join(import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts");
type Mode = "fixed" | "legacy" | "continuous" | "undeclared";
/** Frozen declarations: v1 fixed/legacy and the v2 continuous matrix, each admitted by the real parser. */
const DECLARATION_FILE: Record<Exclude<Mode, "undeclared">, string[]> = {
  fixed: ["service-capabilities", "examples", "fixed-v3-execution.json"],
  legacy: ["service-capabilities", "examples", "legacy-pilot-dispatch.json"],
  continuous: ["service-capabilities-v2", "examples", "continuous-v3-execution.json"],
};
const declarationFor = (mode: Exclude<Mode, "undeclared">) =>
  JSON.parse(readFileSync(join(CONTRACTS, "pilot-dispatch-v0", ...DECLARATION_FILE[mode]), "utf8")) as TaskOpsSnapshot["service_capabilities"];
const CONTINUOUS_SCHEDULE_REASON = "Direct scheduling is unavailable because a confirmed Planning plan creates the bound schedule.";
type Exchange = { request: { method: string; path: string; schema_ref?: string }; response: { body: { data?: Record<string, unknown> } } };
const planningSuccess = (JSON.parse(readFileSync(join(CONTRACTS, "planning-v1", "examples", "success.json"), "utf-8")) as { exchanges: Exchange[] }).exchanges;
const record = <T,>(definition: string): T =>
  planningSuccess.find((e) => e.request.schema_ref === `#/$defs/${definition}`)!.response.body.data!.record as T;
const emptyPlanning = planningSuccess[0].response.body.data as unknown as PlanningSnapshot;
const inputRecord = record<InputRecord>("InputRequest");
const readyPlan = record<PlanRecord>("PlanRequest");
const confirmedPlan = { ...readyPlan, current_status: "CONFIRMED" as const };
const confirmation: ConfirmationRecord = { ...record<ConfirmationRecord>("ConfirmationRequest"), schedule_status: "DISPATCHED", task_id: "task_example_001" };
const outcomeTemplate = record<OutcomeRecord>("OutcomeRequest");

const HEALTHY: SchedulerHealth = {
  status: "fresh",
  scheduler: { state: "RUNNING", detail: null },
  observedAtUtc: "2026-09-16T08:00:00Z",
  observedAtMs: Date.now(),
  error: null,
};
const healthSource = () => HEALTHY;
const freshHealth = (): SchedulerHealth => ({ ...HEALTHY, observedAtMs: Date.now() });
const failedHealth = (): SchedulerHealth => ({ ...HEALTHY, scheduler: { state: "FAILED", detail: "journal unreadable" }, observedAtMs: Date.now() });

function capabilitiesFor(mode: Mode): EffectiveTaskOpsCapabilities {
  const value = taskOpsFixture() as TaskOpsSnapshot & Record<string, unknown>;
  if (mode === "undeclared") delete value.service_capabilities;
  else value.service_capabilities = declarationFor(mode);
  return taskOpsCapabilities(parseTaskOps(value));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function scriptedService(mode: Mode, planningVariant: "confirmed" | "confirmable" = "confirmed") {
  const state = {
    scheduler: { state: "RUNNING" as "RUNNING" | "FAILED", detail: null as string | null },
    schedules: taskOpsFixture().schedules,
    notifications: taskOpsFixture().notifications,
    outcomes: [] as OutcomeRecord[],
    committed: new Map<string, OutcomeRecord>(),
    posts: [] as { path: string; body: Record<string, unknown> }[],
    attempts: [] as { path: string; body: Record<string, unknown> }[],
    reads: [] as string[],
    planningPostMode: "ok" as "ok" | "network",
    planningPostGate: null as ReturnType<typeof deferred<void>> | null,
    lookupGate: null as ReturnType<typeof deferred<void>> | null,
    taskPostGate: null as ReturnType<typeof deferred<void>> | null,
    counter: 0,
    /** Set by a confirmation POST: the planning view then carries the confirmation and its schedule. */
    confirmed: false,
    /** Every request in arrival order, to prove GET-before-POST recovery ordering. */
    log: [] as { method: string; path: string }[],
  };
  const envelope = (status: number, payload: unknown) =>
    new Response(JSON.stringify({ schema: API_SCHEMA, disclaimer: DISCLAIMER, ...(status < 400 ? { data: payload } : { error: payload }) }), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const taskOps = (): Record<string, unknown> => {
    const base = taskOpsFixture({ scheduler: { ...state.scheduler }, schedules: state.schedules, notifications: state.notifications }) as TaskOpsSnapshot & Record<string, unknown>;
    if (mode === "undeclared") delete base.service_capabilities;
    else base.service_capabilities = declarationFor(mode);
    return base;
  };
  const planning = (): PlanningSnapshot => {
    const confirmed = planningVariant === "confirmed" || state.confirmed;
    return {
      ...emptyPlanning,
      latest_input: inputRecord,
      plans: [confirmed ? confirmedPlan : readyPlan],
      confirmations: confirmed ? [confirmation] : [],
      outcomes: [...state.outcomes],
    };
  };
  const receipt = (body: Record<string, unknown>, record: unknown) =>
    envelope(200, { schema: "nxt-planning/v1", disposition: "created", request_id: body.request_id, record });
  const fetchImpl = async (input: string, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? "GET";
    state.log.push({ method, path: input });
    if (method === "GET") state.reads.push(input);
    if (method === "GET" && input === "/api/v0/task-ops") return envelope(200, taskOps());
    if (method === "GET" && input === "/api/v1/planning") return envelope(200, planning());
    if (method === "GET" && input === "/api/v1/collection-executions") return envelope(404, { code: "collection_execution_not_found", detail: "no execution route in this test" });
    if (method === "GET" && input.startsWith("/api/v1/planning/requests/")) {
      if (state.lookupGate !== null) await state.lookupGate.promise;
      const id = decodeURIComponent(input.slice("/api/v1/planning/requests/".length));
      const found = state.committed.get(id);
      if (found) return envelope(200, { schema: "nxt-planning/v1", disposition: "duplicate", request_id: id, record: found });
      return envelope(404, { code: "planning_request_not_found", detail: "no committed request in verified evidence" });
    }
    if (method === "POST") {
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      state.attempts.push({ path: input, body });
      if (input === "/api/v1/planning/outcomes" && state.planningPostMode === "network") throw new TypeError("fetch failed");
      if (input.startsWith("/api/v1/planning/") && state.planningPostGate !== null) await state.planningPostGate.promise;
      if (input.startsWith("/api/v0/task-ops/") && state.taskPostGate !== null) await state.taskPostGate.promise;
      state.posts.push({ path: input, body });
      if (input === "/api/v1/planning/inputs") return receipt(body, { ...inputRecord, revision: inputRecord.revision + 1, request_id: body.request_id });
      if (input === "/api/v1/planning/plans") return receipt(body, { ...readyPlan, request: body as unknown as PlanRecord["request"] });
      if (input === "/api/v1/planning/confirmations") {
        state.confirmed = true;
        return receipt(body, { ...confirmation, request: body as unknown as ConfirmationRecord["request"] });
      }
      if (input === "/api/v1/planning/outcomes") {
        const request = body as unknown as OutcomeRecord["request"];
        const existing = state.committed.get(request.request_id);
        if (existing) return envelope(200, { schema: "nxt-planning/v1", disposition: "duplicate", request_id: request.request_id, record: existing });
        state.counter += 1;
        const created: OutcomeRecord = { ...outcomeTemplate, outcome_id: `outcome_test_${state.counter}`, recorded_at_utc: `2026-09-16T08:3${state.counter}:00Z`, request };
        state.outcomes.push(created);
        state.committed.set(request.request_id, created);
        return envelope(200, { schema: "nxt-planning/v1", disposition: "created", request_id: request.request_id, record: created });
      }
      return envelope(200, { recorded: true });
    }
    return envelope(404, { code: "not_found", detail: `unknown ${method} ${input}` });
  };
  return { state, fetchImpl };
}

let root: Root;
let container: HTMLDivElement;
const text = () => container.textContent ?? "";
const buttons = () => [...container.querySelectorAll("button")];
const buttonNamed = (label: string) => buttons().find((b) => b.textContent === label);
const tick = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
const click = async (label: string) => {
  const button = buttonNamed(label);
  if (!button) throw new Error(`no button "${label}"`);
  await act(async () => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(10);
  });
};
const setValue = async (element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) => {
  const proto = element instanceof HTMLSelectElement ? HTMLSelectElement.prototype
    : element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(element, value);
  await act(async () => {
    element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
  });
};
const input = (suffix: string) => container.querySelector<HTMLInputElement>(`input[id$="${suffix}"]`)!;
const submit = async (form: HTMLFormElement | null) => {
  expect(form).not.toBeNull();
  await act(async () => {
    form!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.advanceTimersByTimeAsync(50);
  });
};
const postsTo = (service: ReturnType<typeof scriptedService>, path: string) => service.state.posts.filter((p) => p.path === path || p.path.startsWith(path));
async function fillOutcomeForm(stage: string, quantity: string) {
  await setValue(input(`-${stage}-quantity`), quantity);
  await setValue(input(`-${stage}-source-ref`), "tab-a-count");
  await setValue(input(`-${stage}-operator`), "operator-a");
  await setValue(input(`-${stage}-started`), "2026-09-16T16:30");
  await setValue(input(`-${stage}-completed`), "2026-09-16T16:31");
  await setValue(input(`-${stage}-reason`), "Interaction test.");
}
async function fillNotification() {
  await setValue(input("notification-1-operator"), "operator-a");
  await setValue(input("notification-1-note"), "Handled in test.");
}

beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement("div");
  document.body.appendChild(container);
});
afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  container.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
async function mount(service: ReturnType<typeof scriptedService>) {
  vi.stubGlobal("fetch", service.fetchImpl);
  root = createRoot(container);
  await act(async () => {
    root.render(<PilotOperations />);
  });
  await tick(50);
}
async function mountPlanning(service: ReturnType<typeof scriptedService>, capabilities: EffectiveTaskOpsCapabilities, health = HEALTHY, source = healthSource) {
  vi.stubGlobal("fetch", service.fetchImpl);
  root = createRoot(container);
  await act(async () => {
    root.render(<PlanningPanel health={health} healthSource={source} capabilities={capabilities} />);
  });
  await tick(50);
}
async function rerenderPlanning(capabilities: EffectiveTaskOpsCapabilities, health = HEALTHY, source = healthSource) {
  await act(async () => {
    root.render(<PlanningPanel health={health} healthSource={source} capabilities={capabilities} />);
  });
}
const outcomeAttempts = (service: ReturnType<typeof scriptedService>) => service.state.attempts.filter((attempt) => attempt.path === "/api/v1/planning/outcomes");
/** The only POST since the last check was to `path`; then clear the record. */
const onlyPost = (service: ReturnType<typeof scriptedService>, path: string) => {
  expect(service.state.posts.map((post) => post.path)).toEqual([path]);
  service.state.posts = [];
};
const newPlanForm = () =>
  [...container.querySelectorAll<HTMLFormElement>("form")].find((form) => [...form.querySelectorAll("button")].some((b) => b.textContent === "Ask for the system suggestion")) ?? null;
const scheduleForm = () => container.querySelector<HTMLFormElement>("form.dispatch-form:not(.planning-form)");
const schedulePosts = (service: ReturnType<typeof scriptedService>) => service.state.attempts.filter((attempt) => attempt.path === "/api/v0/task-ops/schedules");

describe("fixed V3 service: five write entries withheld with the preset-demo reason, evidence-only writes kept", () => {
  it("disables inputs, plans, confirmation, schedules and cancellation, and refuses their direct submits without a POST", async () => {
    const service = scriptedService("fixed", "confirmable");
    await mount(service);
    expect(text()).toContain("PRESET SINGLE-TASK DEMO");
    expect(text()).toContain("preset single-task demonstration");
    expect(buttonNamed("Save revision 2")?.disabled).toBe(true);
    expect(buttonNamed("Ask for the system suggestion")?.disabled).toBe(true);
    expect(buttonNamed("Confirm plan version 1")?.disabled).toBe(true);
    expect(buttonNamed("Schedule simulated collection")?.disabled).toBe(true);
    expect(document.getElementById("dispatch-robot")?.hasAttribute("disabled")).toBe(true);
    expect(buttonNamed("Cancel schedule")).toBeUndefined();
    expect(text()).toContain("New operating input is not installed on this service");
    expect(text()).toContain("Plan confirmation is not installed on this service");
    expect(text()).toContain("Direct schedule creation is not installed on this service");
    expect(text()).toContain("Schedule cancellation is not installed on this service");
    await submit(container.querySelector<HTMLFormElement>("form.planning-form")); // the operating input form
    await submit(container.querySelector<HTMLFormElement>("form.dispatch-form:not(.planning-form)")); // the schedule form
    expect(service.state.posts).toHaveLength(0);
  });

  it("keeps outcome recording available under health, and posts one outcome", async () => {
    const service = scriptedService("fixed");
    await mount(service);
    expect(buttonNamed("Record UNLOADED")?.disabled).toBe(false);
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");
    await click("Save UNLOADED result");
    await tick(100);
    expect(postsTo(service, "/api/v1/planning/outcomes")).toHaveLength(1);
    expect(text()).toContain("SAVED");
    expect(text()).toContain("480 balls");
  });

  it("acknowledges a notification while resolution still obeys the record's can_resolve and condition", async () => {
    const service = scriptedService("fixed");
    await mount(service);
    await fillNotification();
    expect(buttonNamed("Acknowledge")?.disabled).toBe(false);
    expect(buttonNamed("Resolve notification")?.disabled).toBe(true); // can_resolve=false, condition active
    await click("Acknowledge");
    await tick(100);
    expect(postsTo(service, "/api/v0/task-ops/notifications/notification-1/acknowledge")).toHaveLength(1);
    service.state.notifications = [{ ...taskOpsFixture().notifications[0], status: "ACKNOWLEDGED", acknowledged_by: "operator-a", condition_active: false, can_resolve: true }];
    await tick(TASK_OPS_POLL_MS + 50);
    await fillNotification();
    expect(buttonNamed("Resolve notification")?.disabled).toBe(false);
    await click("Resolve notification");
    await tick(100);
    expect(postsTo(service, "/api/v0/task-ops/notifications/notification-1/resolve")).toHaveLength(1);
  });

  it("SUPPORTED does not replace the shared health check: a FAILED scheduler withholds outcome recording and its direct submit", async () => {
    const service = scriptedService("fixed");
    await mount(service);
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");
    service.state.scheduler = { state: "FAILED", detail: "journal unreadable" };
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER FAILED");
    expect(buttonNamed("Save UNLOADED result")?.disabled).toBe(true);
    await submit([...container.querySelectorAll<HTMLFormElement>("form.planning-form")].at(-1)!);
    expect(postsTo(service, "/api/v1/planning/outcomes")).toHaveLength(0);
    await fillNotification();
    expect(buttonNamed("Acknowledge")?.disabled).toBe(true);
  });

  it("keeps a planning UNKNOWN request, its draft and the read-only recovery entry, and recovers by the original ID", async () => {
    const service = scriptedService("fixed");
    await mount(service);
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");
    service.state.planningPostMode = "network";
    await click("Save UNLOADED result");
    await tick(100);
    expect(text()).toContain("UNKNOWN OUTCOME");
    const requestId = /outcomes-[0-9a-f-]{36}/.exec(text())?.[0];
    expect(requestId).toBeDefined();
    await tick(3 * TASK_OPS_POLL_MS);
    expect(text()).toContain("UNKNOWN OUTCOME");
    expect(text()).toContain(requestId!);
    expect(input("-UNLOADED-quantity").value).toBe("480");
    expect(buttonNamed("Recover by request ID")?.disabled).toBe(false);
    expect(text()).toContain("PRESET SINGLE-TASK DEMO");
    service.state.planningPostMode = "ok";
    await click("Recover by request ID");
    await tick(100);
    expect(text()).toContain("SAVED");
    expect(service.state.committed.size).toBe(1);
    expect(service.state.counter).toBe(1);
    expect(postsTo(service, "/api/v1/planning/outcomes").at(-1)?.body.request_id).toBe(requestId);
  });

  it("keeps UNKNOWN and the draft without replay when the latest capability becomes undeclared", async () => {
    const service = scriptedService("fixed");
    await mountPlanning(service, capabilitiesFor("fixed"));
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");
    service.state.planningPostMode = "network";
    await click("Save UNLOADED result");
    await tick(100);
    const requestId = /outcomes-[0-9a-f-]{36}/.exec(text())?.[0];
    expect(requestId).toBeDefined();
    expect(text()).toContain("UNKNOWN OUTCOME");
    expect(service.state.attempts.filter((attempt) => attempt.path === "/api/v1/planning/outcomes")).toHaveLength(1);

    service.state.planningPostMode = "ok";
    service.state.lookupGate = deferred<void>();
    await click("Recover by request ID");
    expect(service.state.reads.some((path) => path.startsWith("/api/v1/planning/requests/"))).toBe(true);

    await rerenderPlanning(capabilitiesFor("undeclared")); // capability changes while the request-ID GET is waiting
    expect(text()).toContain("WRITES UNDECLARED");
    await act(async () => {
      service.state.lookupGate!.resolve();
      await vi.advanceTimersByTimeAsync(100);
    });

    expect(text()).toContain("UNKNOWN OUTCOME");
    expect(text()).toContain(requestId!);
    expect(text()).toContain("declares no write capabilities (UNDECLARED)");
    expect(input("-UNLOADED-quantity").value).toBe("480");
    expect(service.state.attempts.filter((attempt) => attempt.path === "/api/v1/planning/outcomes")).toHaveLength(1);
    expect(service.state.committed.size).toBe(0);
  });
});

describe("explicit legacy service keeps the legacy write routes", () => {
  it("enables inputs, plans, confirmation, schedules and cancellation", async () => {
    const service = scriptedService("legacy", "confirmable");
    await mount(service);
    expect(text()).toContain("LEGACY PILOT DISPATCH");
    expect(text()).not.toContain("not installed on this service");
    expect(buttonNamed("Save revision 2")?.disabled).toBe(false);
    expect(buttonNamed("Ask for the system suggestion")?.disabled).toBe(false);
    expect(buttonNamed("Confirm plan version 1")?.disabled).toBe(false);
    expect(document.getElementById("dispatch-robot")?.hasAttribute("disabled")).toBe(false);
    expect(buttonNamed("Cancel schedule")).toBeDefined();
    expect(buttonNamed("Record UNLOADED")).toBeUndefined(); // no confirmation in this variant: the existing rule shows no stage card
  });
});

describe("undeclared service: readable, every write withheld", () => {
  it("shows records, withholds all eight operations with the UNDECLARED reason, and refuses direct submits", async () => {
    const service = scriptedService("undeclared", "confirmable");
    await mount(service);
    expect(text()).toContain("WRITES UNDECLARED");
    expect(text()).toContain("declares no write capabilities (UNDECLARED)");
    expect(text()).toContain("schedule-1");
    expect(text()).toContain("BLOCKED_AWAITING_HUMAN");
    expect(text()).toContain("plan_example_001");
    expect(buttonNamed("Save revision 2")?.disabled).toBe(true);
    expect(buttonNamed("Ask for the system suggestion")?.disabled).toBe(true);
    expect(buttonNamed("Confirm plan version 1")?.disabled).toBe(true);
    expect(buttonNamed("Schedule simulated collection")?.disabled).toBe(true);
    expect(buttonNamed("Cancel schedule")).toBeUndefined();
    await fillNotification();
    expect(buttonNamed("Acknowledge")?.disabled).toBe(true);
    expect(buttonNamed("Resolve notification")?.disabled).toBe(true);
    await submit(container.querySelector<HTMLFormElement>("form.planning-form"));
    await submit(container.querySelector<HTMLFormElement>("form.dispatch-form:not(.planning-form)"));
    expect(service.state.posts).toHaveLength(0);
  });

  it("withholds outcome recording too when the service has a confirmation but no declaration", async () => {
    const service = scriptedService("undeclared");
    await mount(service);
    expect(buttonNamed("Record UNLOADED")?.disabled).toBe(true);
    expect(text()).toContain("outcome recording is disabled while records stay readable");
  });
});

describe("continuous V3 service (capability v2): Planning confirmation creates the bound schedule; every installed write keeps its own endpoint and gates", () => {
  it("uses source-neutral copy when a continuous session has no schedule records", async () => {
    const service = scriptedService("continuous");
    service.state.schedules = [];
    await mount(service);
    expect(text()).toContain("No schedule records yet.");
    expect(text()).not.toContain("Add one dated collection task above.");
    expect(text()).toContain(CONTINUOUS_SCHEDULE_REASON);
  });

  it("shows the continuous mode, enables Planning, pending cancellation and acknowledgement, and withholds only direct scheduling with the Planning reason", async () => {
    const service = scriptedService("continuous", "confirmable");
    await mount(service);
    expect(text()).toContain("CONTINUOUS V3 SESSION");
    expect(text()).toContain("Service mode CONTINUOUS_V3_EXECUTION: confirmed Planning plans create sequential simulated collection tasks in the active V3 session.");
    expect(text()).not.toContain("preset single-task");
    expect(text()).not.toContain("not installed on this service");
    expect(buttonNamed("Save revision 2")?.disabled).toBe(false);
    expect(buttonNamed("Ask for the system suggestion")?.disabled).toBe(false);
    expect(buttonNamed("Confirm plan version 1")?.disabled).toBe(false);
    expect(buttonNamed("Schedule simulated collection")?.disabled).toBe(true);
    expect(document.getElementById("dispatch-robot")?.hasAttribute("disabled")).toBe(true);
    expect(text()).toContain(CONTINUOUS_SCHEDULE_REASON);
    await setValue(input("schedule-1-cancel"), "operator-a");
    expect(buttonNamed("Cancel schedule")?.disabled).toBe(false);
    await fillNotification();
    expect(buttonNamed("Acknowledge")?.disabled).toBe(false);
    expect(buttonNamed("Resolve notification")?.disabled).toBe(true); // SUPPORTED, but can_resolve=false and the condition is active
    await submit(scheduleForm()); // a forced direct schedule submit reaches no endpoint
    expect(schedulePosts(service)).toHaveLength(0);
    expect(service.state.posts).toHaveLength(0);
  });

  it("input, plan, confirmation, outcome, pending cancellation, acknowledge and resolve each call only their own endpoint; direct scheduling posts nothing", async () => {
    const service = scriptedService("continuous", "confirmable");
    await mount(service);
    await click("Save revision 2");
    await tick(100);
    onlyPost(service, "/api/v1/planning/inputs");
    expect(text()).toContain("SAVED");
    const planForm = newPlanForm();
    expect(planForm).not.toBeNull();
    await setValue(planForm!.querySelector<HTMLInputElement>('input[id$="-operator"]')!, "operator-a");
    await setValue(planForm!.querySelector<HTMLInputElement | HTMLTextAreaElement>('[id$="-reason"]')!, "Interaction test.");
    await click("Ask for the system suggestion");
    await tick(100);
    onlyPost(service, "/api/v1/planning/plans");
    await setValue(container.querySelector<HTMLInputElement>("#confirm-operator")!, "operator-a");
    await click("Confirm plan version 1");
    await tick(100);
    onlyPost(service, "/api/v1/planning/confirmations");
    expect(text()).toContain("CONFIRMED"); // the refreshed view carries the confirmation and its bound schedule
    expect(buttonNamed("Record UNLOADED")?.disabled).toBe(false);
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");
    await click("Save UNLOADED result");
    await tick(100);
    onlyPost(service, "/api/v1/planning/outcomes");
    await setValue(input("schedule-1-cancel"), "operator-a");
    await click("Cancel schedule");
    await tick(100);
    onlyPost(service, "/api/v0/task-ops/schedules/schedule-1/cancel");
    await fillNotification();
    await click("Acknowledge");
    await tick(100);
    onlyPost(service, "/api/v0/task-ops/notifications/notification-1/acknowledge");
    service.state.notifications = [{ ...taskOpsFixture().notifications[0], status: "ACKNOWLEDGED", acknowledged_by: "operator-a", condition_active: false, can_resolve: true }];
    await tick(TASK_OPS_POLL_MS + 50);
    await fillNotification();
    expect(buttonNamed("Resolve notification")?.disabled).toBe(false);
    await click("Resolve notification");
    await tick(100);
    onlyPost(service, "/api/v0/task-ops/notifications/notification-1/resolve");
    expect(buttonNamed("Schedule simulated collection")?.disabled).toBe(true);
    await submit(scheduleForm());
    expect(service.state.posts).toHaveLength(0);
    expect(schedulePosts(service)).toHaveLength(0);
  });

  it("notification resolution keeps its record-level conditions under SUPPORTED and, once permitted, calls only the resolve endpoint", async () => {
    const service = scriptedService("continuous");
    await mount(service);
    await fillNotification();
    expect(buttonNamed("Resolve notification")?.disabled).toBe(true); // condition active, can_resolve=false
    service.state.notifications = [{ ...taskOpsFixture().notifications[0], condition_active: true, can_resolve: true }];
    await tick(TASK_OPS_POLL_MS + 50);
    expect(buttonNamed("Resolve notification")?.disabled).toBe(true); // the condition is still active
    service.state.notifications = [{ ...taskOpsFixture().notifications[0], condition_active: false, can_resolve: false }];
    await tick(TASK_OPS_POLL_MS + 50);
    expect(buttonNamed("Resolve notification")?.disabled).toBe(true); // the service has not permitted resolution
    service.state.notifications = [{ ...taskOpsFixture().notifications[0], condition_active: false, can_resolve: true }];
    await tick(TASK_OPS_POLL_MS + 50);
    await fillNotification();
    expect(buttonNamed("Resolve notification")?.disabled).toBe(false);
    await click("Resolve notification");
    await tick(100);
    expect(service.state.posts.map((post) => post.path)).toEqual(["/api/v0/task-ops/notifications/notification-1/resolve"]);
  });

  it("health alone gates ready input, plan, confirmation, cancellation and acknowledgement controls, then RUNNING releases them", async () => {
    const service = scriptedService("continuous", "confirmable");
    await mount(service);
    await setValue(input("schedule-1-cancel"), "operator-a");
    await fillNotification();
    const ready = ["Save revision 2", "Ask for the system suggestion", "Confirm plan version 1", "Cancel schedule", "Acknowledge"];
    for (const label of ready) expect(buttonNamed(label)?.disabled, `${label} before FAILED`).toBe(false);

    service.state.scheduler = { state: "FAILED", detail: "journal unreadable" };
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER FAILED");
    expect(text()).toContain("CONTINUOUS V3 SESSION");
    for (const label of ready) expect(buttonNamed(label)?.disabled, `${label} while FAILED`).toBe(true);
    await submit(container.querySelector<HTMLFormElement>("form.planning-form"));
    await submit(newPlanForm());
    await submit(container.querySelector<HTMLFormElement>("form.dispatch-cancel"));
    for (const label of ["Confirm plan version 1", "Acknowledge"]) {
      await act(async () => {
        buttonNamed(label)!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await vi.advanceTimersByTimeAsync(10);
      });
    }
    expect(service.state.attempts).toHaveLength(0);

    service.state.scheduler = { state: "RUNNING", detail: null };
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER RUNNING");
    for (const label of ready) expect(buttonNamed(label)?.disabled, `${label} after RUNNING`).toBe(false);
  });

  it("health alone gates a ready outcome and resolvable notification while FAILED, then RUNNING releases both", async () => {
    const service = scriptedService("continuous");
    service.state.notifications = [{
      ...taskOpsFixture().notifications[0],
      condition_active: false,
      can_resolve: true,
    }];
    await mount(service);
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");
    await fillNotification();
    expect(buttonNamed("Save UNLOADED result")?.disabled).toBe(false);
    expect(buttonNamed("Resolve notification")?.disabled).toBe(false);
    expect(buttonNamed("Acknowledge")?.disabled).toBe(false);
    service.state.scheduler = { state: "FAILED", detail: "journal unreadable" };
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER FAILED");
    expect(text()).toContain("CONTINUOUS V3 SESSION"); // the declaration itself did not change
    for (const label of ["Save UNLOADED result", "Acknowledge", "Resolve notification"]) {
      expect(buttonNamed(label)?.disabled, label).toBe(true);
    }
    await submit([...container.querySelectorAll<HTMLFormElement>("form.planning-form")].at(-1)!);
    for (const label of ["Acknowledge", "Resolve notification"]) {
      await act(async () => {
        buttonNamed(label)!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await vi.advanceTimersByTimeAsync(50);
      });
    }
    expect(service.state.attempts).toHaveLength(0);
    service.state.scheduler = { state: "RUNNING", detail: null };
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER RUNNING");
    expect(buttonNamed("Save UNLOADED result")?.disabled).toBe(false);
    expect(buttonNamed("Acknowledge")?.disabled).toBe(false);
    expect(buttonNamed("Resolve notification")?.disabled).toBe(false);
    expect(service.state.attempts).toHaveLength(0);
  });

  it("planning busy alone gates every ready installed planning write until the pending mutation settles", async () => {
    const service = scriptedService("continuous");
    service.state.planningPostGate = deferred<void>();
    await mount(service);
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");
    for (const label of ["Save revision 2", "Ask for the system suggestion", "Save UNLOADED result"]) {
      expect(buttonNamed(label)?.disabled, `${label} before busy`).toBe(false);
    }

    await click("Save revision 2");
    expect(service.state.attempts.map((attempt) => attempt.path)).toEqual(["/api/v1/planning/inputs"]);
    expect(buttonNamed("Saving…")?.disabled).toBe(true);
    for (const label of ["Ask for the system suggestion", "Save UNLOADED result"]) {
      expect(buttonNamed(label)?.disabled, `${label} while busy`).toBe(true);
    }
    for (const label of ["Ask for the system suggestion", "Save UNLOADED result"]) {
      await act(async () => {
        buttonNamed(label)!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await vi.advanceTimersByTimeAsync(10);
      });
    }
    expect(service.state.attempts.map((attempt) => attempt.path)).toEqual(["/api/v1/planning/inputs"]);

    const gate = service.state.planningPostGate;
    service.state.planningPostGate = null;
    await act(async () => {
      gate!.resolve();
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(text()).toContain("SAVED");
    for (const label of ["Ask for the system suggestion", "Save UNLOADED result"]) {
      expect(buttonNamed(label)?.disabled, `${label} after busy`).toBe(false);
    }
  });

  it("task-ops busy alone gates every ready installed task write until the pending mutation settles", async () => {
    const service = scriptedService("continuous");
    service.state.notifications = [{
      ...taskOpsFixture().notifications[0],
      condition_active: false,
      can_resolve: true,
    }];
    service.state.taskPostGate = deferred<void>();
    await mount(service);
    await setValue(input("schedule-1-cancel"), "operator-a");
    await fillNotification();
    for (const label of ["Cancel schedule", "Acknowledge", "Resolve notification"]) {
      expect(buttonNamed(label)?.disabled, `${label} before busy`).toBe(false);
    }

    await click("Cancel schedule");
    expect(service.state.attempts.map((attempt) => attempt.path)).toEqual(["/api/v0/task-ops/schedules/schedule-1/cancel"]);
    for (const label of ["Cancel schedule", "Acknowledge", "Resolve notification"]) {
      expect(buttonNamed(label)?.disabled, `${label} while busy`).toBe(true);
    }
    for (const label of ["Acknowledge", "Resolve notification"]) {
      await act(async () => {
        buttonNamed(label)!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await vi.advanceTimersByTimeAsync(10);
      });
    }
    expect(service.state.attempts.map((attempt) => attempt.path)).toEqual(["/api/v0/task-ops/schedules/schedule-1/cancel"]);

    const gate = service.state.taskPostGate;
    service.state.taskPostGate = null;
    await act(async () => {
      gate!.resolve();
      await vi.advanceTimersByTimeAsync(100);
    });
    for (const label of ["Cancel schedule", "Acknowledge", "Resolve notification"]) {
      expect(buttonNamed(label)?.disabled, `${label} after busy`).toBe(false);
    }
  });
});

describe("continuous V3 service: Planning UNKNOWN recovery is GET-first and honours capability and health changes before any replay", () => {
  it("keeps the UNKNOWN request, ID and draft across polls, looks the ID up first, and replays the identical body only after planning_request_not_found", async () => {
    const service = scriptedService("continuous");
    await mount(service);
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");
    service.state.planningPostMode = "network";
    await click("Save UNLOADED result");
    await tick(100);
    expect(text()).toContain("UNKNOWN OUTCOME");
    const requestId = /outcomes-[0-9a-f-]{36}/.exec(text())?.[0];
    expect(requestId).toBeDefined();
    expect(outcomeAttempts(service)).toHaveLength(1);
    expect(outcomeAttempts(service)[0].body.request_id).toBe(requestId);
    await tick(3 * TASK_OPS_POLL_MS); // capability and health polls neither remount nor replay on their own
    expect(text()).toContain("UNKNOWN OUTCOME");
    expect(text()).toContain(requestId!);
    expect(text()).toContain("CONTINUOUS V3 SESSION");
    expect(input("-UNLOADED-quantity").value).toBe("480");
    expect(outcomeAttempts(service)).toHaveLength(1);
    expect(buttonNamed("Record WASHED")?.disabled).toBe(true); // other writes wait for the recovery
    service.state.planningPostMode = "ok";
    const logStart = service.state.log.length;
    await click("Recover by request ID");
    await tick(100);
    const recovery = service.state.log.slice(logStart).map((entry) => `${entry.method} ${entry.path}`);
    expect(recovery[0]).toBe(`GET /api/v1/planning/requests/${encodeURIComponent(requestId!)}`);
    expect(recovery.indexOf("POST /api/v1/planning/outcomes")).toBeGreaterThan(0);
    expect(text()).toContain("SAVED");
    expect(outcomeAttempts(service)).toHaveLength(2);
    expect(outcomeAttempts(service)[1].body).toEqual(outcomeAttempts(service)[0].body); // same ID, same content
    expect(service.state.committed.size).toBe(1);
    expect(service.state.counter).toBe(1);
  });

  it("a scheduler failure that arrives while the request-ID GET is waiting blocks the replay and keeps UNKNOWN; the same-ID replay goes once health returns", async () => {
    const service = scriptedService("continuous");
    let health = freshHealth();
    const source = () => health;
    await mountPlanning(service, capabilitiesFor("continuous"), health, source);
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");
    service.state.planningPostMode = "network";
    await click("Save UNLOADED result");
    await tick(100);
    const requestId = /outcomes-[0-9a-f-]{36}/.exec(text())?.[0];
    expect(requestId).toBeDefined();
    expect(outcomeAttempts(service)).toHaveLength(1);
    service.state.planningPostMode = "ok";
    service.state.lookupGate = deferred<void>();
    await click("Recover by request ID");
    expect(service.state.reads.some((path) => path.startsWith("/api/v1/planning/requests/"))).toBe(true);
    health = failedHealth(); // the shared reading fails while the GET is waiting
    await rerenderPlanning(capabilitiesFor("continuous"), health, source);
    expect(text()).toContain("SCHEDULER FAILED");
    await act(async () => {
      service.state.lookupGate!.resolve();
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(text()).toContain("UNKNOWN OUTCOME");
    expect(text()).toContain(requestId!);
    expect(text()).toContain("Replay was not sent");
    expect(text()).toContain("CONTINUOUS V3 SESSION");
    expect(input("-UNLOADED-quantity").value).toBe("480");
    expect(outcomeAttempts(service)).toHaveLength(1);
    expect(service.state.committed.size).toBe(0);
    health = freshHealth();
    await rerenderPlanning(capabilitiesFor("continuous"), health, source);
    service.state.lookupGate = null;
    await click("Recover by request ID");
    await tick(100);
    expect(text()).toContain("SAVED");
    expect(outcomeAttempts(service)).toHaveLength(2);
    expect(outcomeAttempts(service)[1].body).toEqual(outcomeAttempts(service)[0].body);
    expect(outcomeAttempts(service)[1].body.request_id).toBe(requestId);
  });

  it("a capability change to UNDECLARED while the GET is waiting blocks the replay, keeps UNKNOWN with its ID and draft, and does not remount the panel", async () => {
    const service = scriptedService("continuous");
    await mountPlanning(service, capabilitiesFor("continuous"));
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");
    service.state.planningPostMode = "network";
    await click("Save UNLOADED result");
    await tick(100);
    const requestId = /outcomes-[0-9a-f-]{36}/.exec(text())?.[0];
    expect(requestId).toBeDefined();
    expect(text()).toContain("CONTINUOUS V3 SESSION");
    service.state.planningPostMode = "ok";
    service.state.lookupGate = deferred<void>();
    await click("Recover by request ID");
    await rerenderPlanning(capabilitiesFor("undeclared")); // capability changes while the request-ID GET is waiting
    expect(text()).toContain("WRITES UNDECLARED");
    await act(async () => {
      service.state.lookupGate!.resolve();
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(text()).toContain("UNKNOWN OUTCOME");
    expect(text()).toContain(requestId!);
    expect(text()).toContain("declares no write capabilities (UNDECLARED)");
    expect(input("-UNLOADED-quantity").value).toBe("480");
    expect(outcomeAttempts(service)).toHaveLength(1);
    expect(service.state.committed.size).toBe(0);
    await rerenderPlanning(capabilitiesFor("continuous")); // the operation is installed again: the same ID and body may be replayed
    service.state.lookupGate = null;
    await click("Recover by request ID");
    await tick(100);
    expect(text()).toContain("SAVED");
    expect(outcomeAttempts(service)).toHaveLength(2);
    expect(outcomeAttempts(service)[1].body).toEqual(outcomeAttempts(service)[0].body);
  });
});
