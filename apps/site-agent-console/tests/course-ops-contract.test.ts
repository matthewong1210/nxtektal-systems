import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { ManagerApiError, type FetchLike } from "../lib/api";
import { createCourseOpsClient, parseCourseOps, type CourseOpsSnapshot } from "../lib/course-ops";
const example = (name = "paused-legacy") => JSON.parse(readFileSync(join(import.meta.dirname,
  "../../../simulation/docs/contracts/course-ops-v1/examples", `${name}.json`), "utf8"));
describe("read-only saved course contract", () => {
  it("parses both frozen examples without creating missing range observations", () => {
    const legacy = parseCourseOps(example().data);
    expect(legacy.range).toEqual({status: "UNKNOWN", observed_minute: null, inventory_fraction: null, zones: [], robots: [], staff: null});
    expect(legacy.source.live).toBe(false); expect(legacy.source.published_at_utc).toBeNull();
    expect(legacy.course.checkpoints).toHaveLength(54);
    expect(parseCourseOps(example("observed").data).range.robots).toHaveLength(1);
  });
  it.each([
    (s: CourseOpsSnapshot) => { s.environment = "PHYSICAL" as "SIMULATION"; },
    (s: CourseOpsSnapshot) => { s.source.published_at_utc = s.generated_at_utc as unknown as null; },
    (s: CourseOpsSnapshot) => { s.source.series_id = ""; },
    (s: CourseOpsSnapshot) => { s.clock.simulated_minute = Number.NaN; },
    (s: CourseOpsSnapshot) => { s.frames[0].image_url = "https://other.test/image.png"; },
    (s: CourseOpsSnapshot) => { s.frames[0].image_url = "/api/v1/course-ops/media/round-0000000001/frame-000000.png?sha256=" + s.frames[0].image_sha256; },
    (s: CourseOpsSnapshot) => { s.frames[0].image_url = s.frames[0].image_url.split("?")[0]; },
    (s: CourseOpsSnapshot) => { s.frames[0].minute = s.clock.simulated_minute + 1; },
    (s: CourseOpsSnapshot) => { s.frames[0].detections = [{condition: "DIVOT", score: .5, bbox: [0, 0, 5000, 10]}]; },
    (s: CourseOpsSnapshot) => { s.carts[0].cart_id = "CART-16"; },
    (s: CourseOpsSnapshot) => { s.range.inventory_fraction = .5; },
    (s: CourseOpsSnapshot) => { s.course.checkpoints[0].x_m = Infinity; },
    (s: CourseOpsSnapshot) => { (s as unknown as Record<string, unknown>).compiled = {}; },
  ])("rejects malformed, cross-source or hidden fields", (mutate) => {
    const value = example().data as CourseOpsSnapshot; mutate(value);
    expect(() => parseCourseOps(value)).toThrow(ManagerApiError);
  });
  it("only issues a noncached GET through the existing envelope", async () => {
    const fetchImpl = vi.fn<FetchLike>(async () => new Response(JSON.stringify(example())));
    const client = createCourseOpsClient(fetchImpl);
    await expect(client.read()).resolves.toMatchObject({schema: "nxt-course-ops/v1"});
    expect(Object.keys(client)).toEqual(["read"]);
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/course-ops", expect.objectContaining({method: "GET", cache: "no-store"}));
  });
  it("passes cancellation and removes its timeout after settling", async () => {
    vi.useFakeTimers();
    try {
      let seen: AbortSignal | null = null;
      const fetchImpl: FetchLike = async (_, init) => {
        seen = init?.signal as AbortSignal;
        return new Promise((_, reject) => seen?.addEventListener("abort", () => reject(new Error("aborted"))));
      };
      const control = new AbortController();
      const request = createCourseOpsClient(fetchImpl).read(control.signal);
      const rejected = expect(request).rejects.toThrow("aborted"); control.abort(); await rejected;
      expect((seen as AbortSignal | null)?.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0);
    } finally {vi.useRealTimers();}
  });
  it("preserves 503 error meaning without inventing defaults", async () => {
    const fetchImpl: FetchLike = async () => new Response(JSON.stringify({schema: "nxt-site-agent/api/v0", disclaimer: "SIMULATION", error: {code: "course_ops_unavailable", detail: "Changing snapshot"}}), {status: 503});
    await expect(createCourseOpsClient(fetchImpl).read()).rejects.toMatchObject({status: 503, code: "course_ops_unavailable"});
  });
});
