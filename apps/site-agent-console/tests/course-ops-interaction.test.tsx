// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CourseOperationsPanel } from "../components/CourseOperationsPanel";
import { API_SCHEMA, DISCLAIMER } from "../lib/api";
import type { CourseOpsSnapshot } from "../lib/course-ops";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function snapshot(round = "round-0000000001"): CourseOpsSnapshot {
  const checkpoints: CourseOpsSnapshot["course"]["checkpoints"] = Array.from({ length: 54 }, (_, index) => ({
    checkpoint_id: `cp-h${String(Math.floor(index / 3) + 1).padStart(2, "0")}-${["fairway", "bunker", "green"][index % 3]}`, hole_number: Math.floor(index / 3) + 1,
    feature_id: `hole-${String(Math.floor(index / 3) + 1).padStart(2, "0")}-${["fairway", "bunker", "green"][index % 3]}`, surface_type: ["fairway", "bunker", "green"][index % 3],
    x_m: (index % 9) * 100, y_m: Math.floor(index / 9) * 100,
    coverage_status: index === 0 ? "USABLE" : "UNOBSERVED", last_observed_minute: index === 0 ? 60 : null,
  }));
  return {
    schema: "nxt-course-ops/v1", environment: "SIMULATION", generated_at_utc: "2026-09-18T02:00:00Z",
    source: { kind: "SAVED_SIMULATION_REPORT", round_id: round, index: Number(round.slice(6)), seed: 7, series_id: "c".repeat(64), session_id: "d".repeat(64), engine_digest: "e".repeat(64), status: "PAUSED", parent_paused: true, child_paused: false, published_at_utc: null, report_sha256: "a".repeat(64), live: false },
    clock: { simulated_minute: 120, simulated_at_utc: "2026-09-17T10:00:00Z" },
    course: { site_id: "synthetic-course-18", deployment_id: "synthetic-course-18-monitoring-v0", map_revision: null, geometry: null, hole_count: 18, cart_count: 16, checkpoints },
    carts: [{ cart_id: "CART-01", frame_id: "frame-000001", minute: 60, x_m: 5, y_m: 6, accuracy_m: 3 }],
    frames: [{ frame_id: "frame-000001", cart_id: "CART-01", checkpoint_id: "cp-h01-fairway", minute: 60, image_url: `/api/v1/course-ops/media/${round}/frame-000001.png?sha256=${"b".repeat(64)}`, image_sha256: "b".repeat(64), width: 384, height: 240, quality: "USABLE", score_calibration: "NOT_CALIBRATED", detections: [{ condition: "PONDING", score: 0.72, bbox: [12, 24, 92, 104] }] }],
    observations: [{ observation_id: "observation-1", checkpoint_id: "cp-h01-fairway", frame_id: "frame-000001", condition: "PONDING", quality: "USABLE", captured_at_utc: "2026-09-17T09:00:00Z", detection_score: 0.72 }],
    cases: [{ case_id: "case-1", checkpoint_id: "cp-h01-fairway", condition_kind: "PONDING", status: "OPEN", priority: "HIGH", opened_at_utc: "2026-09-17T09:00:00Z", updated_at_utc: "2026-09-17T09:00:00Z", latest_task_id: "task-1" }],
    tasks: [{ task_id: "task-1", case_id: "case-1", task_kind: "INSPECT", status: "ASSIGNED", resource_id: "STAFF-01", resource_kind: "HUMAN", assigned_at_utc: "2026-09-17T09:00:00Z", started_at_utc: null, completed_at_utc: null }],
    staff_jobs: [{ job_id: "job-1", checkpoint_id: "cp-h01-fairway", task_kind: "BUNKER_REPAIR", status: "QUEUED", started_at_s: null, completed_at_s: null }],
    range: { status: "UNKNOWN", observed_minute: null, inventory_fraction: null, zones: [], robots: [], staff: null },
    weather: { status: "UNKNOWN" },
  };
}

function service() {
  const state = { data: snapshot(), mode: "ok" as "ok" | "missing" | "error" | "hang", reads: 0, signals: [] as AbortSignal[] };
  const response = (status: number, data: unknown) => new Response(JSON.stringify({ schema: API_SCHEMA, disclaimer: DISCLAIMER, ...(status === 200 ? { data } : { error: data }) }), { status, headers: { "Content-Type": "application/json" } });
  const fetchImpl = async (path: string, init?: RequestInit) => {
    expect(path).toBe("/api/v1/course-ops");
    expect(init?.method ?? "GET").toBe("GET");
    state.reads += 1;
    if (init?.signal) state.signals.push(init.signal);
    if (state.mode === "missing") return response(404, { code: "not_found", detail: "not connected" });
    if (state.mode === "error") throw new TypeError("connection interrupted");
    if (state.mode === "hang") return new Promise<Response>((_, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    });
    return response(200, state.data);
  };
  return { state, fetchImpl };
}

let root: Root | null;
let container: HTMLDivElement;
const text = () => container.textContent ?? "";
const tick = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const click = async (label: string) => {
  const button = container.querySelector<HTMLElement>(`[aria-label="${label}"]`);
  if (!button) throw new Error(`Missing control: ${label}`);
  await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
};
async function mount(current: ReturnType<typeof service>) {
  vi.stubGlobal("fetch", current.fetchImpl);
  root = createRoot(container);
  await act(async () => { root!.render(<CourseOperationsPanel />); });
  await tick(10);
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-09-18T02:00:00Z"));
  root = null; container = document.createElement("div"); document.body.appendChild(container);
});
afterEach(async () => {
  await act(async () => root?.unmount()); container.remove(); vi.unstubAllGlobals(); vi.useRealTimers();
});

describe("mounted whole-course observations", () => {
  it("distinguishes a paused saved report from real-time readings and keeps unobserved data unknown", async () => {
    await mount(service());
    expect(text()).toContain("Saved simulation report");
    expect(text()).toContain("Paused");
    expect(text()).toContain("Publication time unknown");
    expect(text()).toContain("Simulation time");
    expect(text()).toContain("API read time");
    expect(text()).toContain("53 unobserved");
    expect(text()).toContain("1 / 16 carts observed");
    expect(text()).toContain("Weather unknown");
    expect(text()).toContain("Range observations unavailable");
    expect(container.querySelectorAll("[data-checkpoint-id]")).toHaveLength(54);
    expect(container.querySelectorAll("form")).toHaveLength(0);
  });

  it("selects checkpoint and cart evidence with candidate boxes, age, and uncalibrated scores", async () => {
    await mount(service());
    await click("Inspect cp-h01-fairway");
    expect(container.querySelector("img")?.getAttribute("src")).toContain("round-0000000001/frame-000001.png");
    expect(container.querySelectorAll("[data-detection-box]")).toHaveLength(1);
    expect(text()).toContain("Not calibrated");
    expect(text()).toContain("0.72");
    expect(text()).toContain("case-1");
    expect(text()).toContain("QUEUED");
    await click("Inspect CART-01");
    expect(text()).toContain("±3 m");
    expect(text()).toContain("60 simulation minutes old");
    await click("Inspect cp-h01-bunker");
    expect(container.querySelector("img")).toBeNull();
    expect(text()).toContain("No captured image for this observation point");
  });

  it("keeps a failed refresh visibly stale, then clears the old selection when a new round arrives", async () => {
    const current = service(); await mount(current); await click("Inspect cp-h01-fairway");
    current.state.mode = "error"; await tick(5010);
    expect(text()).toContain("Refresh failed");
    expect(text()).toContain("Last successful view");
    expect(container.querySelector("img")?.getAttribute("src")).toContain("round-0000000001");
    current.state.mode = "ok"; current.state.data = snapshot("round-0000000002"); await tick(5010);
    expect(text()).not.toContain("Refresh failed");
    expect(container.querySelector("img")).toBeNull();
    expect(text()).toContain("Choose an observation point or cart");
    await click("Inspect cp-h01-fairway");
    expect(container.querySelector("img")?.getAttribute("src")).toContain("round-0000000002");
    expect(container.innerHTML).not.toContain("round-0000000001/frame-000001.png");
  });

  it("handles an older service without course-ops and recovers on a later successful read", async () => {
    const current = service(); current.state.mode = "missing"; await mount(current);
    expect(text()).toContain("Whole-course data is not connected");
    expect(container.querySelector("img")).toBeNull();
    current.state.mode = "ok"; await tick(5010);
    expect(text()).toContain("Saved simulation report");
    expect(text()).not.toContain("Whole-course data is not connected");
  });

  it("clears selection when another experiment reuses the same round and frame names", async () => {
    const current = service(); await mount(current); await click("Inspect cp-h01-fairway");
    expect(container.querySelector("img")).not.toBeNull();
    current.state.data.source.series_id = "f".repeat(64);
    current.state.data.source.session_id = "a".repeat(64);
    await tick(5010);
    expect(container.querySelector("img")).toBeNull();
    expect(text()).toContain("Choose an observation point or cart");
  });

  it("presents observed range resources, including unknown zone counts and human assistance", async () => {
    const current = service();
    current.state.data.range = { status: "OBSERVED", observed_minute: 120, inventory_fraction: 0.45,
      zones: [{ zone_id: "Z1", balls: 120, is_open: true }, { zone_id: "Z2", balls: null, is_open: null }],
      robots: [{ robot_id: "ROBOT-1", activity: "WAITING", health: "OK", location: "Z1", battery_fraction: 0.6, payload_balls: 80, awaiting_human: true }], staff: { capacity: 4, busy: 2, queued: 1 } };
    await mount(current);
    expect(text()).toContain("45%"); expect(text()).toContain("2 / 4 busy · 1 queued");
    expect(text()).toContain("Human help requested"); expect(text()).toContain("80 balls");
    const row = [...container.querySelectorAll("tr")].find((item) => item.textContent?.startsWith("Z2"));
    expect(row?.textContent).toBe("Z2UnknownUnknown");
    expect(text()).not.toContain("Range observations unavailable");
  });

  it("makes keyboard selection work and reports a missing image without showing a substitute", async () => {
    await mount(service());
    const point = container.querySelector<SVGGElement>('[aria-label="Inspect cp-h01-fairway"]')!;
    await act(async () => point.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    const image = container.querySelector("img")!;
    expect(image).not.toBeNull();
    await act(async () => image.dispatchEvent(new Event("error")));
    expect(container.querySelector("img")).toBeNull();
    expect(text()).toContain("Image unavailable for this saved frame");
    expect(text()).toContain("No replacement image is shown");
    const retry = [...container.querySelectorAll("button")].find((button) => button.textContent === "Retry image")!;
    expect(retry).toBeDefined();
    await act(async () => retry.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(container.querySelector("img")?.getAttribute("src")).toContain("sha256=");
    expect(text()).not.toContain("Image unavailable for this saved frame");
  });

  it("offers a native observation selector without relying on small map targets", async () => {
    await mount(service());
    const select = container.querySelector<HTMLSelectElement>('[aria-label="Choose observation point"]');
    expect(select).not.toBeNull();
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(select, "cp-h01-fairway");
      select!.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(container.querySelector("img")).not.toBeNull();
  });

  it("aborts an in-flight read and stops polling on unmount, then starts a single new reader on remount", async () => {
    const current = service(); current.state.mode = "hang"; await mount(current);
    await tick(5000); expect(current.state.reads).toBe(1);
    await act(async () => { root!.unmount(); root = null; });
    expect(current.state.signals[0].aborted).toBe(true);
    await tick(20000); expect(current.state.reads).toBe(1);
    current.state.mode = "ok"; await mount(current);
    expect(current.state.reads).toBe(2);
    await tick(5010); expect(current.state.reads).toBe(3);
  });
});
