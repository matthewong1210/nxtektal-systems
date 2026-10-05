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
const declaration = (name: string) =>
  JSON.parse(readFileSync(join(CONTRACTS, "pilot-dispatch-v0", "service-capabilities", "examples", `${name}.json`), "utf8")) as TaskOpsSnapshot["service_capabilities"];
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

type Mode = "fixed" | "legacy" | "undeclared";

const HEALTHY: SchedulerHealth = {
  status: "fresh",
  scheduler: { state: "RUNNING", detail: null },
  observedAtUtc: "2026-09-16T08:00:00Z",
  observedAtMs: Date.now(),
  error: null,
};
const healthSource = () => HEALTHY;

function capabilitiesFor(mode: Mode): EffectiveTaskOpsCapabilities {
  const value = taskOpsFixture() as TaskOpsSnapshot & Record<string, unknown>;
  if (mode === "undeclared") delete value.service_capabilities;
  else value.service_capabilities = declaration(mode === "fixed" ? "fixed-v3-execution" : "legacy-pilot-dispatch");
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
    notifications: taskOpsFixture().notifications,
    outcomes: [] as OutcomeRecord[],
    committed: new Map<string, OutcomeRecord>(),
    posts: [] as { path: string; body: Record<string, unknown> }[],
    attempts: [] as { path: string; body: Record<string, unknown> }[],
    reads: [] as string[],
    planningPostMode: "ok" as "ok" | "network",
    lookupGate: null as ReturnType<typeof deferred<void>> | null,
    counter: 0,
  };
  const envelope = (status: number, payload: unknown) =>
    new Response(JSON.stringify({ schema: API_SCHEMA, disclaimer: DISCLAIMER, ...(status < 400 ? { data: payload } : { error: payload }) }), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const taskOps = (): Record<string, unknown> => {
    const base = taskOpsFixture({ scheduler: { ...state.scheduler }, notifications: state.notifications }) as TaskOpsSnapshot & Record<string, unknown>;
    if (mode === "undeclared") delete base.service_capabilities;
    else base.service_capabilities = declaration(mode === "fixed" ? "fixed-v3-execution" : "legacy-pilot-dispatch");
    return base;
  };
  const planning = (): PlanningSnapshot => ({
    ...emptyPlanning,
    latest_input: inputRecord,
    plans: [planningVariant === "confirmed" ? confirmedPlan : readyPlan],
    confirmations: planningVariant === "confirmed" ? [confirmation] : [],
    outcomes: [...state.outcomes],
  });
  const fetchImpl = async (input: string, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? "GET";
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
      state.posts.push({ path: input, body });
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
const setValue = async (element: HTMLInputElement | HTMLSelectElement, value: string) => {
  const proto = element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
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
async function mountPlanning(service: ReturnType<typeof scriptedService>, capabilities: EffectiveTaskOpsCapabilities) {
  vi.stubGlobal("fetch", service.fetchImpl);
  root = createRoot(container);
  await act(async () => {
    root.render(<PlanningPanel health={HEALTHY} healthSource={healthSource} capabilities={capabilities} />);
  });
  await tick(50);
}
async function rerenderPlanning(capabilities: EffectiveTaskOpsCapabilities) {
  await act(async () => {
    root.render(<PlanningPanel health={HEALTHY} healthSource={healthSource} capabilities={capabilities} />);
  });
}

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
