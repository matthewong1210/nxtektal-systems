// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PilotOperations } from "../components/PilotOperations";
import { API_SCHEMA, DISCLAIMER } from "../lib/api";
import type { ConfirmationRecord, InputRecord, OutcomeRecord, PlanRecord, PlanningSnapshot } from "../lib/planning";
import { PLANNING_POLL_MS } from "../lib/planning-state";
import { TASK_OPS_POLL_MS, type TaskOpsSnapshot } from "../lib/task-ops";
import { taskOpsFixture } from "./task-ops-fixtures";

/** Real mounted components (React DOM under happy-dom), the real pollers,
 * controllers, forms and clients; only `fetch` is a scripted service whose
 * state the test mutates between polls. Timers are faked so each poll is
 * driven explicitly. */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const EXAMPLES = join(import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts", "planning-v1", "examples");
type Exchange = { request: { method: string; path: string; schema_ref?: string }; response: { body: { data?: Record<string, unknown> } } };
const success = (JSON.parse(readFileSync(join(EXAMPLES, "success.json"), "utf-8")) as { exchanges: Exchange[] }).exchanges;
const record = <T,>(definition: string): T =>
  success.find((e) => e.request.schema_ref === `#/$defs/${definition}`)!.response.body.data!.record as T;
const emptySnapshot = success[0].response.body.data as unknown as PlanningSnapshot;
const inputRecord = record<InputRecord>("InputRequest");
const plan = { ...record<PlanRecord>("PlanRequest"), current_status: "CONFIRMED" as const };
const confirmation: ConfirmationRecord = { ...record<ConfirmationRecord>("ConfirmationRequest"), schedule_status: "DISPATCHED", task_id: "task_example_001" };
const outcomeTemplate = record<OutcomeRecord>("OutcomeRequest");

type PostMode = "ok" | "network" | "unavailable";
type TaskOpsMode = "ok" | "network" | "missing";

function scriptedService() {
  const state = {
    scheduler: { state: "RUNNING" as "RUNNING" | "FAILED", detail: null as string | null },
    outcomes: [] as OutcomeRecord[],
    postMode: "ok" as PostMode,
    taskOpsMode: "ok" as TaskOpsMode,
    reads: [] as string[],
    committed: new Map<string, OutcomeRecord>(),
    posts: [] as Record<string, unknown>[],
    counter: 0,
  };
  const envelope = (status: number, payload: unknown) =>
    new Response(JSON.stringify({ schema: API_SCHEMA, disclaimer: DISCLAIMER, ...(status < 400 ? { data: payload } : { error: payload }) }), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const taskOps = (): TaskOpsSnapshot => taskOpsFixture({ scheduler: { ...state.scheduler } });
  const planning = (): PlanningSnapshot => ({ ...emptySnapshot, latest_input: inputRecord, plans: [plan], confirmations: [confirmation], outcomes: [...state.outcomes] });
  const fetchImpl = async (input: string, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? "GET";
    if (method === "GET") state.reads.push(input);
    if (method === "GET" && input === "/api/v0/task-ops") {
      if (state.taskOpsMode === "network") throw new TypeError("fetch failed");
      if (state.taskOpsMode === "missing") return envelope(404, { code: "not_found", detail: "unknown API path" });
      return envelope(200, taskOps());
    }
    if (method === "GET" && input === "/api/v1/planning") return envelope(200, planning());
    if (method === "GET" && input.startsWith("/api/v1/planning/requests/")) {
      const id = decodeURIComponent(input.slice("/api/v1/planning/requests/".length));
      const found = state.committed.get(id);
      if (found) return envelope(200, { schema: "nxt-planning/v1", disposition: "duplicate", request_id: id, record: found });
      return envelope(404, { code: "planning_request_not_found", detail: "no committed request in verified evidence" });
    }
    if (method === "POST" && input === "/api/v1/planning/outcomes") {
      const body = JSON.parse(String(init?.body)) as OutcomeRecord["request"];
      if (state.postMode === "network") throw new TypeError("fetch failed");
      if (state.postMode === "unavailable") return envelope(503, { code: "planning_unavailable", detail: "scheduler is not running" });
      state.posts.push(body as unknown as Record<string, unknown>);
      const existing = state.committed.get(body.request_id);
      if (existing) return envelope(200, { schema: "nxt-planning/v1", disposition: "duplicate", request_id: body.request_id, record: existing });
      state.counter += 1;
      const created: OutcomeRecord = { ...outcomeTemplate, outcome_id: `outcome_test_${state.counter}`, recorded_at_utc: `2026-09-16T08:3${state.counter}:00Z`, request: body };
      state.outcomes.push(created);
      state.committed.set(body.request_id, created);
      return envelope(200, { schema: "nxt-planning/v1", disposition: "created", request_id: body.request_id, record: created });
    }
    return envelope(404, { code: "not_found", detail: `unknown ${method} ${input}` });
  };
  const addOutcome = (stage: OutcomeRecord["request"]["stage"], quantity: number, supersedes: string | null) => {
    state.counter += 1;
    const item: OutcomeRecord = {
      ...outcomeTemplate,
      outcome_id: `outcome_test_${state.counter}`,
      recorded_at_utc: `2026-09-16T08:2${state.counter}:00Z`,
      request: { ...outcomeTemplate.request, request_id: `external-${state.counter}`, stage, quantity_balls: quantity, supersedes_outcome_id: supersedes, operator: "operator-b" },
    };
    state.outcomes.push(item);
    return item;
  };
  return { state, fetchImpl, addOutcome };
}

let root: Root;
let container: HTMLDivElement;
const buttons = () => [...container.querySelectorAll("button")];
const buttonNamed = (label: string) => buttons().find((b) => b.textContent === label);
const text = () => container.textContent ?? "";
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
async function fillOutcomeForm(stage: string, quantity: string) {
  await setValue(input(`-${stage}-quantity`), quantity);
  await setValue(input(`-${stage}-source-ref`), "tab-a-count");
  await setValue(input(`-${stage}-operator`), "operator-a");
  await setValue(input(`-${stage}-started`), "2026-09-16T16:30");
  await setValue(input(`-${stage}-completed`), "2026-09-16T16:31");
  await setValue(input(`-${stage}-reason`), "Interaction test.");
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
  await tick(50); // initial task-ops and planning reads settle
}

describe("mounted planning panel with the shared task-ops poller", () => {
  it("keeps a draft, blocks saving when a poll brings a newer record, and saves only after an explicit re-review", async () => {
    const service = scriptedService();
    const first = service.addOutcome("COLLECTED", 500, null);
    await mount(service);
    expect(text()).toContain("SCHEDULER RUNNING");
    expect(text()).toContain("500 balls");

    await click("Correct this stage");
    await setValue(input("-COLLECTED-quantity"), "110");
    expect(text()).toContain(`Corrects ${first.outcome_id}`);

    const newer = service.addOutcome("COLLECTED", 505, first.outcome_id); // another operator, between polls
    await tick(PLANNING_POLL_MS + 50);
    expect(text()).toContain("505 balls");
    expect(text()).toContain("A newer record for this stage was saved while this form was open");
    expect(text()).toContain(newer.outcome_id);
    expect(input("-COLLECTED-quantity").value).toBe("110"); // draft kept
    expect(text()).toContain(`Corrects ${first.outcome_id}`); // target not moved silently
    expect(buttonNamed("Save COLLECTED result")?.disabled).toBe(true);
    expect(service.state.posts).toHaveLength(0);

    await click("Review and correct the newer record");
    expect(text()).toContain(`Corrects ${newer.outcome_id}`);
    expect(buttonNamed("Save COLLECTED result")?.disabled).toBe(false);
    expect(input("-COLLECTED-quantity").value).toBe("110");

    await fillOutcomeForm("COLLECTED", "110");
    await click("Save COLLECTED result");
    await tick(100);
    expect(service.state.posts).toHaveLength(1);
    expect(service.state.posts[0]).toMatchObject({ stage: "COLLECTED", quantity_balls: 110, supersedes_outcome_id: newer.outcome_id });
    expect(text()).toContain("110 balls");
    expect(text()).toContain("SAVED");
  });

  it("blocks new planning writes on its own while the shared reading says FAILED, and releases them when it says RUNNING again", async () => {
    const service = scriptedService();
    await mount(service);
    expect(buttonNamed("Record UNLOADED")?.disabled).toBe(false);
    service.state.scheduler = { state: "FAILED", detail: "planning_unavailable: journal unreadable" };
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER FAILED");
    expect(text()).toContain("journal unreadable");
    for (const label of ["Record UNLOADED", "Record WASHED", "Record SUPPLIED", "Ask for the system suggestion", "Save revision 2"]) {
      expect(buttonNamed(label)?.disabled, label).toBe(true);
    }
    expect(text()).toContain("Scheduler failed. Changes are disabled. planning_unavailable: journal unreadable"); // the task panel's own copy of the same reading
    // the submit-time gate, not only the disabled attribute: a form submit dispatched directly is refused before any request
    const form = container.querySelector<HTMLFormElement>("form.planning-form");
    expect(form).not.toBeNull();
    await act(async () => {
      form!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(service.state.posts).toHaveLength(0);
    service.state.scheduler = { state: "RUNNING", detail: null };
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER RUNNING");
    expect(buttonNamed("Record UNLOADED")?.disabled).toBe(false);
    expect(service.state.posts).toHaveLength(0);
  });

  it("holds an UNKNOWN request through a scheduler failure, keeps recovery available, and never lets a recovered receipt pose as scheduler health", async () => {
    const service = scriptedService();
    await mount(service);
    await click("Record UNLOADED");
    await fillOutcomeForm("UNLOADED", "480");

    service.state.postMode = "network"; // the write's response is lost
    await click("Save UNLOADED result");
    await tick(100);
    expect(text()).toContain("UNKNOWN OUTCOME");
    const requestId = /outcomes-[0-9a-f-]{36}/.exec(text())?.[0];
    expect(requestId).toBeDefined();
    expect(input("-UNLOADED-quantity").value).toBe("480"); // draft kept
    expect(buttonNamed("Save UNLOADED result")?.disabled).toBe(true);

    service.state.scheduler = { state: "FAILED", detail: "planning_result_unknown: stopped after an unreadable write" };
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER FAILED");
    expect(text()).toContain("stopped after an unreadable write");
    expect(text()).toContain("does not restart the scheduler");
    expect(buttonNamed("Recover by request ID")?.disabled).toBe(false);
    expect(buttonNamed("Record WASHED")?.disabled).toBe(true);

    service.state.postMode = "unavailable"; // the runner refuses the replay while stopped
    await click("Recover by request ID");
    await tick(100);
    expect(text()).toContain("UNKNOWN OUTCOME");
    expect(text()).toContain(requestId!);
    expect(text()).toContain("SCHEDULER FAILED");
    expect(service.state.committed.size).toBe(0);

    service.state.scheduler = { state: "RUNNING", detail: null };
    service.state.postMode = "ok";
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER RUNNING");
    await click("Recover by request ID");
    await tick(100);
    expect(text()).toContain("SAVED");
    expect(text()).toContain(requestId!);
    expect(service.state.committed.size).toBe(1);
    expect(service.state.posts.filter((p) => p.stage === "UNLOADED")).toHaveLength(1);
    expect(service.state.posts[service.state.posts.length - 1].request_id).toBe(requestId);
    expect(text()).toContain("480 balls");
  });
});

describe("one shared poller, honest stale and unavailable readings, and clean unmount", () => {
  it("mounts exactly one task-ops poller and one planning poller for both panels", async () => {
    const service = scriptedService();
    await mount(service);
    expect(service.state.reads.filter((r) => r === "/api/v0/task-ops")).toHaveLength(1);
    expect(service.state.reads.filter((r) => r === "/api/v1/planning")).toHaveLength(1);
    await tick(TASK_OPS_POLL_MS + 50);
    expect(service.state.reads.filter((r) => r === "/api/v0/task-ops")).toHaveLength(2);
    expect(service.state.reads.filter((r) => r === "/api/v1/planning")).toHaveLength(1);
    await tick(PLANNING_POLL_MS - TASK_OPS_POLL_MS);
    expect(service.state.reads.filter((r) => r === "/api/v1/planning")).toHaveLength(2);
  });

  it("goes STALE with the read error when the task service stops answering, then UNAVAILABLE when the route is gone, and recovers", async () => {
    const service = scriptedService();
    await mount(service);
    expect(buttonNamed("Record UNLOADED")?.disabled).toBe(false);
    service.state.taskOpsMode = "network";
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER STATE STALE");
    expect(text()).toContain("fetch failed");
    expect(text()).toContain("last known state was RUNNING");
    expect(buttonNamed("Record UNLOADED")?.disabled).toBe(true);
    service.state.taskOpsMode = "missing";
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("TASK SERVICE UNAVAILABLE");
    expect(buttonNamed("Record UNLOADED")?.disabled).toBe(true);
    service.state.taskOpsMode = "ok";
    await tick(TASK_OPS_POLL_MS + 50);
    expect(text()).toContain("SCHEDULER RUNNING");
    expect(buttonNamed("Record UNLOADED")?.disabled).toBe(false);
  });

  it("stops both pollers on unmount and issues no further reads", async () => {
    const service = scriptedService();
    await mount(service);
    await tick(TASK_OPS_POLL_MS + 50);
    const before = service.state.reads.length;
    await act(async () => {
      root.unmount();
    });
    await vi.advanceTimersByTimeAsync(3 * PLANNING_POLL_MS);
    expect(service.state.reads).toHaveLength(before);
    root = createRoot(container); // afterEach unmounts this empty root
  });
});
