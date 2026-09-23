// @vitest-environment happy-dom
/**
 * Mounted interaction tests for the read-only collection execution panel
 * inside PilotOperations. The service is a scripted fetch over the frozen
 * SIMULATION contract examples; nothing here is a live route or a real
 * integration run.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { COLLECTION_EXECUTIONS_POLL_MS } from "../components/execution/CollectionExecutionPanel";
import { PilotOperations } from "../components/PilotOperations";
import { API_SCHEMA, DISCLAIMER } from "../lib/api";
import type { ConfirmationRecord, InputRecord, OutcomeRecord, PlanRecord, PlanningSnapshot } from "../lib/planning";
import type { TaskOpsSnapshot } from "../lib/task-ops";
import { exampleData, withRound } from "./execution-fixtures";
import { taskOpsFixture } from "./task-ops-fixtures";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PLANNING_EXAMPLES = join(import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts", "planning-v1", "examples");
type Exchange = { request: { method: string; path: string; schema_ref?: string }; response: { body: { data?: Record<string, unknown> } } };
const planningSuccess = (JSON.parse(readFileSync(join(PLANNING_EXAMPLES, "success.json"), "utf-8")) as { exchanges: Exchange[] }).exchanges;
const record = <T,>(definition: string): T =>
  planningSuccess.find((e) => e.request.schema_ref === `#/$defs/${definition}`)!.response.body.data!.record as T;
const emptyPlanning = planningSuccess[0].response.body.data as unknown as PlanningSnapshot;
const inputRecord = record<InputRecord>("InputRequest");
const plan = { ...record<PlanRecord>("PlanRequest"), current_status: "CONFIRMED" as const };
const confirmation: ConfirmationRecord = { ...record<ConfirmationRecord>("ConfirmationRequest"), schedule_status: "DISPATCHED", task_id: "task_example_001" };

type ExecMode = "ok" | "network" | "missing" | "invalid" | "hang";

function scriptedService() {
  const state = {
    executions: exampleData("success"),
    execMode: "ok" as ExecMode,
    hung: [] as ((response: Response) => void)[],
    reads: [] as string[],
    requests: [] as { method: string; path: string }[],
    outcomes: [] as OutcomeRecord[],
    planningPostMode: "ok" as "ok" | "network",
  };
  const envelope = (status: number, payload: unknown) =>
    new Response(JSON.stringify({ schema: API_SCHEMA, disclaimer: DISCLAIMER, ...(status < 400 ? { data: payload } : { error: payload }) }), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const taskOps = (): TaskOpsSnapshot => taskOpsFixture({ scheduler: { state: "RUNNING", detail: null } });
  const planning = (): PlanningSnapshot => ({ ...emptyPlanning, latest_input: inputRecord, plans: [plan], confirmations: [confirmation], outcomes: [...state.outcomes] });
  const fetchImpl = async (input: string, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? "GET";
    state.requests.push({ method, path: input });
    if (method === "GET") state.reads.push(input);
    if (method === "GET" && input === "/api/v0/task-ops") return envelope(200, taskOps());
    if (method === "GET" && input === "/api/v1/planning") return envelope(200, planning());
    if (method === "GET" && input.startsWith("/api/v1/planning/requests/")) {
      return envelope(404, { code: "planning_request_not_found", detail: "no committed request in verified evidence" });
    }
    if (method === "POST" && input === "/api/v1/planning/outcomes") {
      if (state.planningPostMode === "network") throw new TypeError("fetch failed");
      return envelope(503, { code: "planning_unavailable", detail: "not exercised by this test" });
    }
    if (input === "/api/v1/collection-executions") {
      if (method !== "GET") return envelope(405, { code: "collection_execution_invalid_request", detail: "read-only namespace" });
      if (state.execMode === "network") throw new TypeError("fetch failed");
      if (state.execMode === "missing") return envelope(404, { code: "collection_execution_not_found", detail: "route not connected" });
      if (state.execMode === "hang") return new Promise<Response>((resolve) => { state.hung.push(resolve); });
      if (state.execMode === "invalid") {
        const bad = structuredClone(state.executions) as { executions: { state: string }[] };
        bad.executions[0].state = "COMPLETED"; // not in the frozen enum
        return envelope(200, bad);
      }
      return envelope(200, state.executions);
    }
    return envelope(404, { code: "not_found", detail: `unknown ${method} ${input}` });
  };
  const releaseHung = () => {
    for (const resolve of state.hung.splice(0)) resolve(envelope(200, state.executions));
  };
  return { state, fetchImpl, releaseHung };
}

let root: Root;
let container: HTMLDivElement;
const text = () => container.textContent ?? "";
const panel = () => container.querySelector('section[aria-label="Collection execution · simulated V3 session"]');
const panelText = () => panel()?.textContent ?? "";
const buttonNamed = (label: string) => [...container.querySelectorAll("button")].find((b) => b.textContent === label);
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
const execReads = (service: ReturnType<typeof scriptedService>) => service.state.reads.filter((r) => r === "/api/v1/collection-executions");

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

describe("read-only collection execution panel mounted in PilotOperations", () => {
  it("reads the route once on mount, polls it, and never writes to it", async () => {
    const service = scriptedService();
    await mount(service);
    expect(panel()).not.toBeNull();
    expect(panelText()).toContain("SUCCEEDED");
    expect(panelText()).toContain("SESSION ACTIVE");
    expect(execReads(service)).toHaveLength(1);
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(execReads(service)).toHaveLength(2);
    expect(service.state.requests.filter((r) => r.path === "/api/v1/collection-executions" && r.method !== "GET")).toHaveLength(0);
    // no control on the panel can start, retry, recover or stop an execution
    const labels = [...panel()!.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["Retry execution read"]);
  });

  it("keeps the last valid snapshot as STALE with its age when a poll fails, and recovers on the next success", async () => {
    const service = scriptedService();
    await mount(service);
    service.state.execMode = "network";
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("READ STALE");
    expect(panelText()).toContain("fetch failed");
    expect(panelText()).toMatch(/read \d+ s ago/);
    expect(panelText()).toContain("SUCCEEDED"); // last valid snapshot, unchanged
    expect(panelText()).toContain("SESSION ACTIVE");
    service.state.execMode = "ok";
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("READ FRESH");
    expect(panelText()).not.toContain("READ STALE");
  });

  it("routes an unknown enum through the strict parser's failure path: stale last snapshot, nothing invalid rendered", async () => {
    const service = scriptedService();
    await mount(service);
    service.state.execMode = "invalid";
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("READ STALE");
    expect(panelText()).toContain("did not pass the collection-execution contract check");
    expect(panelText()).not.toContain("COMPLETED");
    expect(panelText()).toContain("SUCCEEDED"); // the previously valid record, not the rejected one
  });

  it("is OFFLINE with the contract message when the first read is already invalid, and shows no card", async () => {
    const service = scriptedService();
    service.state.execMode = "invalid";
    await mount(service);
    expect(panelText()).toContain("OFFLINE");
    expect(panelText()).toContain("did not pass the collection-execution contract check");
    expect(panel()!.querySelectorAll(".exec-record")).toHaveLength(0);
    expect(panelText()).not.toContain("COMPLETED");
  });

  it("reports UNAVAILABLE when the route is missing while the planning and task panels keep working", async () => {
    const service = scriptedService();
    service.state.execMode = "missing";
    await mount(service);
    expect(panelText()).toContain("UNAVAILABLE");
    expect(panelText()).toContain("not connected");
    expect(panelText()).not.toContain("remains below marked stale");
    expect(panel()!.querySelectorAll(".exec-record")).toHaveLength(0);
    expect(text()).toContain("SCHEDULER RUNNING");
    expect(buttonNamed("Record UNLOADED")?.disabled).toBe(false);
  });

  it("keeps the last valid snapshot marked stale when the route disappears after a successful read", async () => {
    const service = scriptedService();
    await mount(service);
    service.state.execMode = "missing";
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("UNAVAILABLE");
    expect(panelText()).toContain("remains below marked stale");
    expect(panelText()).toContain("READ STALE");
    expect(panel()!.querySelectorAll(".exec-record")).toHaveLength(1);
  });

  it("issues one in-flight read at a time and resumes after a hung read is released", async () => {
    const service = scriptedService();
    await mount(service);
    service.state.execMode = "hang";
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(service.state.hung).toHaveLength(1);
    await tick(2 * COLLECTION_EXECUTIONS_POLL_MS);
    expect(service.state.hung).toHaveLength(1); // no overlapping read while one is waiting
    service.state.execMode = "ok";
    service.releaseHung();
    await tick(50);
    expect(panelText()).toContain("READ FRESH");
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(execReads(service).length).toBeGreaterThanOrEqual(3);
  });

  it("stops reading on unmount", async () => {
    const service = scriptedService();
    await mount(service);
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    const before = execReads(service).length;
    await act(async () => {
      root.unmount();
    });
    await vi.advanceTimersByTimeAsync(3 * COLLECTION_EXECUTIONS_POLL_MS);
    expect(execReads(service)).toHaveLength(before);
    root = createRoot(container); // afterEach unmounts this empty root
  });

  it("resets only its own display state on a round change and leaves a pending planning UNKNOWN request intact", async () => {
    const service = scriptedService();
    await mount(service);
    // a planning write whose response is lost: the planning panel holds UNKNOWN with its request ID and draft
    await click("Record UNLOADED");
    await setValue(input("-UNLOADED-quantity"), "480");
    await setValue(input("-UNLOADED-source-ref"), "tab-a-count");
    await setValue(input("-UNLOADED-operator"), "operator-a");
    await setValue(input("-UNLOADED-started"), "2026-09-16T16:30");
    await setValue(input("-UNLOADED-completed"), "2026-09-16T16:31");
    await setValue(input("-UNLOADED-reason"), "Interaction test.");
    service.state.planningPostMode = "network";
    await click("Save UNLOADED result");
    await tick(100);
    expect(text()).toContain("UNKNOWN OUTCOME");
    const requestId = /outcomes-[0-9a-f-]{36}/.exec(text())?.[0];
    expect(requestId).toBeDefined();
    // open a details element inside the execution card: display state owned by the execution panel
    const details = panel()!.querySelector<HTMLDetailsElement>("details.exec-trace");
    expect(details).not.toBeNull();
    details!.open = true;
    expect(panelText()).toContain("round-001");
    // the next poll returns the next round of the same session
    service.state.executions = withRound(exampleData("success"), "round-002", 1);
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("round-002");
    expect(panelText()).not.toContain("round-001");
    expect(panel()!.querySelector<HTMLDetailsElement>("details.exec-trace")?.open).toBe(false);
    // the planning panel's pending request, draft and recovery control are untouched
    expect(text()).toContain("UNKNOWN OUTCOME");
    expect(text()).toContain(requestId!);
    expect(input("-UNLOADED-quantity").value).toBe("480");
    expect(buttonNamed("Recover by request ID")?.disabled).toBe(false);
    expect(buttonNamed("Save UNLOADED result")?.disabled).toBe(true);
  });
});
