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

import { COLLECTION_EXECUTIONS_POLL_MS, EXECUTION_READ_EXPIRY_MS } from "../components/execution/CollectionExecutionPanel";
import { PilotOperations } from "../components/PilotOperations";
import { API_SCHEMA, DISCLAIMER } from "../lib/api";
import type { ConfirmationRecord, InputRecord, OutcomeRecord, PlanRecord, PlanningSnapshot } from "../lib/planning";
import { localTimeToUtc, type TaskOpsSnapshot } from "../lib/task-ops";
import { continuousTwoTaskPendingData, continuousTwoTaskWitness, continuousTwoTaskWitnessData, endedData, exampleData, humanAssistanceData, withRound, witnessData } from "./execution-fixtures";
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
    paused: false,
    schedules: [] as Record<string, unknown>[],
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
    if (method === "POST" && input === "/api/v0/task-ops/schedules") {
      state.schedules.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return envelope(200, { recorded: true });
    }
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
      if (state.paused) {
        const paused = structuredClone(state.executions) as { session_state: string };
        paused.session_state = "PAUSED";
        return envelope(200, paused);
      }
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

describe("read health expires on its own 15-second wall clock, independent of errors and of the simulation clock", () => {
  const readBadge = () => [...panel()!.querySelectorAll(".exec-strip-row .badge")].map((b) => b.textContent).find((t) => t === "READ FRESH" || t === "READ STALE");

  it("flips READ FRESH to READ STALE at the boundary while a read keeps waiting without error, and re-arms on the next success", async () => {
    const service = scriptedService();
    await mount(service);
    expect(readBadge()).toBe("READ FRESH");
    service.state.execMode = "hang"; // the poll at +5 s never answers
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(service.state.hung).toHaveLength(1);
    await tick(EXECUTION_READ_EXPIRY_MS - COLLECTION_EXECUTIONS_POLL_MS - 100); // just before 15 s since the last success
    expect(readBadge()).toBe("READ FRESH");
    await tick(200); // past the boundary: no error, no new read, still must flip
    expect(readBadge()).toBe("READ STALE");
    expect(panelText()).toContain("older than 15 s");
    expect(panelText()).not.toContain("fetch failed");
    expect(panelText()).toContain("SUCCEEDED"); // the last valid snapshot stays on screen
    service.state.execMode = "ok";
    service.releaseHung(); // the waiting read finally succeeds: timing restarts
    await tick(50);
    expect(readBadge()).toBe("READ FRESH");
    expect(panelText()).not.toContain("older than 15 s");
    await tick(EXECUTION_READ_EXPIRY_MS - 100); // fresh again for a full period from the new success
    expect(readBadge()).toBe("READ FRESH");
  });

  it("keeps the old snapshot with the expiry hint when reads fail past the boundary", async () => {
    const service = scriptedService();
    await mount(service);
    service.state.execMode = "network";
    await tick(EXECUTION_READ_EXPIRY_MS + 200);
    expect(readBadge()).toBe("READ STALE");
    expect(panelText()).toContain("fetch failed");
    expect(panelText()).toContain("older than 15 s");
    expect(panelText()).toContain("SUCCEEDED");
  });

  it("does not freeze the read-health clock while the simulation is PAUSED", async () => {
    const service = scriptedService();
    service.state.paused = true;
    await mount(service);
    expect(panelText()).toContain("SESSION PAUSED");
    expect(readBadge()).toBe("READ FRESH");
    service.state.execMode = "hang";
    await tick(EXECUTION_READ_EXPIRY_MS + 200);
    expect(panelText()).toContain("SESSION PAUSED");
    expect(readBadge()).toBe("READ STALE");
  });

  it("clears its expiry timer on unmount and re-arms from the remounted instance's own read", async () => {
    const service = scriptedService();
    await mount(service);
    service.state.execMode = "hang";
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    await act(async () => {
      root.unmount();
    });
    service.state.execMode = "ok";
    await tick(8_000 - COLLECTION_EXECUTIONS_POLL_MS - 50); // remount 8 s after the first mount
    root = createRoot(container);
    await act(async () => {
      root.render(<PilotOperations />);
    });
    await tick(50);
    expect(readBadge()).toBe("READ FRESH");
    service.state.execMode = "hang"; // keep the remounted instance's later reads waiting
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    await tick(EXECUTION_READ_EXPIRY_MS - 8_000 - COLLECTION_EXECUTIONS_POLL_MS + 100); // the first instance's boundary passes
    expect(readBadge()).toBe("READ FRESH");
    await tick(8_000); // the remounted instance's own boundary
    expect(readBadge()).toBe("READ STALE");
  });
});

describe("3C acceptance: safety rejection, human assistance, PAUSED and ENDED arrive through polling", () => {
  it("renders each service-reported state in turn without any browser control appearing", async () => {
    const service = scriptedService();
    service.state.executions = exampleData("safety-rejected");
    await mount(service);
    expect(panelText()).toContain("REJECTED");
    expect(panelText()).toContain("final safety check rejected");
    expect(panelText()).toContain("Did not start (contract evidence)");
    service.state.executions = humanAssistanceData();
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("HUMAN_ASSISTANCE_REQUIRED");
    expect(panelText()).toContain("DEVICE PROTECTED");
    expect(panelText()).toContain("not resumable from the browser");
    service.state.paused = true;
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("SESSION PAUSED");
    expect(panelText()).toContain("READ FRESH"); // service connection is healthy while the simulation is paused
    service.state.paused = false;
    service.state.executions = endedData();
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("SESSION ENDED");
    expect(panelText()).toContain("SUCCEEDED");
    const labels = [...panel()!.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["Retry execution read"]);
    expect(service.state.requests.filter((r) => r.path.startsWith("/api/v1/collection-executions") && r.method !== "GET")).toHaveLength(0);
  });
});

describe("3C acceptance: the schedule form uses the shared simulation clock while the execution read is fresh", () => {
  const localInput = (instantMs: number) => {
    const d = new Date(instantMs);
    const two = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}T${two(d.getHours())}:${two(d.getMinutes())}`;
  };
  const fillSchedule = async (local: string) => {
    await setValue(container.querySelector<HTMLInputElement>("#dispatch-time")!, local);
    await setValue(container.querySelector<HTMLInputElement>("#dispatch-operator")!, "operator-a");
  };

  it("accepts a 2026 simulation date that the 2026-09-24 wall clock would reject, and posts the simulation-derived UTC", async () => {
    const service = scriptedService();
    service.state.executions = witnessData(); // simulation time 2026-09-16T08:30:00Z, wall clock far later
    await mount(service);
    expect(text()).toContain("Simulation time 2026-09-16 08:30:00 UTC · session ACTIVE");
    const simulationNow = Date.parse("2026-09-16T08:30:00Z");
    const local = localInput(simulationNow + 60_000);
    await fillSchedule(local);
    expect(text()).toContain("compared with simulation time 2026-09-16 08:30:00 UTC, not the wall clock");
    await click("Schedule simulated collection");
    await tick(100);
    expect(service.state.schedules).toHaveLength(1);
    expect(service.state.schedules[0]).toMatchObject({ robot_id: "picker-01", zone_id: "Z1", operator: "operator-a", due_at_utc: localTimeToUtc(local) });
    expect(service.state.schedules[0].due_at_utc).toBe(new Date(simulationNow + 60_000).toISOString());
    expect(text()).toContain("Schedule recorded");
  });

  it("disables scheduling when the simulation clock reading goes stale, and refuses a directly dispatched submit", async () => {
    const service = scriptedService();
    service.state.executions = witnessData();
    await mount(service);
    await fillSchedule(localInput(Date.parse("2026-09-16T08:31:00Z")));
    service.state.execMode = "hang";
    await tick(EXECUTION_READ_EXPIRY_MS + 200);
    expect(text()).toContain("Changes are disabled. The simulation clock reading is not fresh");
    expect(document.getElementById("dispatch-robot")?.hasAttribute("disabled")).toBe(true);
    const form = container.querySelector<HTMLFormElement>("form.dispatch-form");
    await act(async () => {
      form!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(service.state.schedules).toHaveLength(0);
    service.state.execMode = "ok";
    service.releaseHung();
    await tick(50);
    expect(text()).not.toContain("Changes are disabled. The simulation clock reading is not fresh");
    expect(document.getElementById("dispatch-robot")?.hasAttribute("disabled")).toBe(false);
  });

  it("keeps the simulation clock stale and refuses new schedules when the route answers 404 after a successful read, then recovers", async () => {
    const service = scriptedService();
    service.state.executions = witnessData();
    await mount(service);
    expect(text()).toContain("compared with simulation time 2026-09-16 08:30:00 UTC");
    const simulationNow = Date.parse("2026-09-16T08:30:00Z");
    const local = localInput(simulationNow + 60_000);
    await fillSchedule(local);
    service.state.execMode = "missing"; // the route disappears after a successful V3 read
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("UNAVAILABLE");
    expect(panelText()).toContain("remains below marked stale");
    expect(panel()!.querySelectorAll(".exec-record")).toHaveLength(1); // old snapshot kept
    expect(text()).toContain("Changes are disabled. The simulation clock reading is not fresh");
    expect(text()).toContain("404");
    expect(text()).not.toContain("compared with simulation time");
    expect(document.getElementById("dispatch-robot")?.hasAttribute("disabled")).toBe(true);
    const form = container.querySelector<HTMLFormElement>("form.dispatch-form");
    await act(async () => {
      form!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(service.state.schedules).toHaveLength(0); // never falls back to the wall clock
    service.state.execMode = "ok";
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("READ FRESH");
    expect(panelText()).not.toContain("UNAVAILABLE");
    expect(text()).toContain("compared with simulation time 2026-09-16 08:30:00 UTC");
    expect(document.getElementById("dispatch-robot")?.hasAttribute("disabled")).toBe(false);
    expect(container.querySelector<HTMLInputElement>("#dispatch-time")!.value).toBe(local); // draft kept through the outage
    await click("Schedule simulated collection");
    await tick(100);
    expect(service.state.schedules).toHaveLength(1);
    expect(service.state.schedules[0].due_at_utc).toBe(new Date(simulationNow + 60_000).toISOString());
  });

  it("falls back to the legacy wall-clock rule when the service has no execution route", async () => {
    const service = scriptedService();
    service.state.execMode = "missing";
    await mount(service);
    expect(panelText()).toContain("UNAVAILABLE");
    expect(text()).not.toContain("compared with simulation time");
    await fillSchedule(localInput(Date.parse("2026-09-16T08:31:00Z")));
    await click("Schedule simulated collection");
    await tick(50);
    expect(text()).toContain("Choose a start time in the future.");
    expect(service.state.schedules).toHaveLength(0);
  });

  it("keeps a pending planning UNKNOWN request intact while the execution read expires and recovers", async () => {
    const service = scriptedService();
    service.state.executions = witnessData();
    await mount(service);
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
    const requestId = /outcomes-[0-9a-f-]{36}/.exec(text())?.[0];
    expect(requestId).toBeDefined();
    service.state.execMode = "hang";
    await tick(EXECUTION_READ_EXPIRY_MS + 200);
    expect(panelText()).toContain("READ STALE");
    service.state.execMode = "ok";
    service.releaseHung();
    await tick(50);
    expect(panelText()).toContain("READ FRESH");
    expect(text()).toContain("UNKNOWN OUTCOME");
    expect(text()).toContain(requestId!);
    expect(input("-UNLOADED-quantity").value).toBe("480");
    expect(buttonNamed("Recover by request ID")?.disabled).toBe(false);
  });
});

describe("continuous V4: two executions arrive through polling in service order and the panel stays read-only", () => {
  const cards = () => [...panel()!.querySelectorAll<HTMLElement>('[data-testid="execution-record-card"]')];
  const cardIds = () => cards().map((card) => card.getAttribute("data-execution-id"));
  const cardById = (executionId: string) => {
    const card = cards().find((item) => item.getAttribute("data-execution-id") === executionId);
    if (!card) throw new Error(`no card for ${executionId}`);
    return card;
  };
  const TERMINAL_ID = "75ac1b88002c03ecdec352ed27d9c306893ac4c29674c891eac1ddf96808f215";
  const SECOND_ID = "125688930584b23999ff42f630c60f77f7daef28019dfb65c033db17151a3a24";
  const routeRequests = (service: ReturnType<typeof scriptedService>) => service.state.requests.filter((r) => r.path.startsWith("/api/v1/collection-executions"));

  it("keeps the terminal card's displayed evidence unchanged while the other card moves from PENDING to RUNNING, and the session stays ACTIVE", async () => {
    const service = scriptedService();
    const expectedOrder = continuousTwoTaskWitness().executions.map((row) => row.execution_id);
    service.state.executions = continuousTwoTaskPendingData();
    await mount(service);
    expect(cardIds()).toEqual(expectedOrder);
    expect(cardById(SECOND_ID).textContent).toContain("PENDING");
    expect(cardById(SECOND_ID).textContent).toContain("Not started yet · waiting for a policy slot");
    expect(cardById(TERMINAL_ID).textContent).toContain("SUCCEEDED");
    expect(cardById(TERMINAL_ID).textContent).toContain("600 balls · ledger-backed (COMPLETE)");
    const terminalBefore = cardById(TERMINAL_ID).innerHTML;
    expect(panelText()).toContain("SESSION ACTIVE");
    expect(panelText()).not.toContain("SESSION ENDED");
    expect(execReads(service)).toHaveLength(1);

    service.state.executions = continuousTwoTaskWitnessData(); // the next poll: the second task has started
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(execReads(service)).toHaveLength(2);
    expect(cardIds()).toEqual(expectedOrder); // service order, never re-sorted by state or time
    expect(cardById(TERMINAL_ID).innerHTML).toBe(terminalBefore);
    expect(cardById(SECOND_ID).textContent).toContain("RUNNING");
    expect(cardById(SECOND_ID).textContent).toContain("Started at t = 31200 s");
    expect(cardById(SECOND_ID).textContent).toContain("296 balls · ledger-backed (COMPLETE)");
    expect(cardById(SECOND_ID).textContent).not.toContain("PENDING");
    expect(panelText()).toContain("SESSION ACTIVE");
    expect(panelText()).toContain("t = 31800 s");
    expect(panelText()).not.toContain("SESSION ENDED");
    expect(panelText()).not.toContain("The session has ended");
    // Only serial GETs of the read-only route; no execution mutation of any kind was attempted.
    expect(routeRequests(service).map((r) => `${r.method} ${r.path}`)).toEqual(["GET /api/v1/collection-executions", "GET /api/v1/collection-executions"]);
    expect(service.state.requests.filter((r) => r.method !== "GET")).toHaveLength(0);
    expect([...panel()!.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Retry execution read"]);
  });

  it("polls the two-task session on one serial 5 s loop, keeps the last two-card snapshot through a failed read, and stops on unmount", async () => {
    const service = scriptedService();
    const expectedOrder = continuousTwoTaskWitness().executions.map((row) => row.execution_id);
    service.state.executions = continuousTwoTaskWitnessData();
    await mount(service);
    expect(cardIds()).toEqual(expectedOrder);
    expect(execReads(service)).toHaveLength(1);
    service.state.execMode = "hang";
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(service.state.hung).toHaveLength(1);
    await tick(2 * COLLECTION_EXECUTIONS_POLL_MS);
    expect(service.state.hung).toHaveLength(1); // one in-flight read at a time
    service.state.execMode = "ok";
    service.releaseHung();
    await tick(50);
    expect(panelText()).toContain("READ FRESH");
    service.state.execMode = "network";
    await tick(COLLECTION_EXECUTIONS_POLL_MS + 50);
    expect(panelText()).toContain("READ STALE");
    expect(panelText()).toContain("fetch failed");
    expect(cardIds()).toEqual(expectedOrder); // the last successful two-card snapshot is retained
    expect(cardById(SECOND_ID).textContent).toContain("RUNNING");
    expect(panelText()).toContain("SESSION ACTIVE");
    expect(routeRequests(service).every((r) => r.method === "GET")).toBe(true);
    const before = execReads(service).length;
    await act(async () => {
      root.unmount();
    });
    await vi.advanceTimersByTimeAsync(3 * COLLECTION_EXECUTIONS_POLL_MS);
    expect(execReads(service)).toHaveLength(before);
    root = createRoot(container); // afterEach unmounts this empty root
  });
});
