import { afterEach, describe, expect, it, vi } from "vitest";

import { ManagerApiError } from "../lib/api";
import type { ConfirmationRequest, MutationReceipt, PlanningSnapshot } from "../lib/planning";
import {
  canWritePlanning,
  createPlanningController,
  PLANNING_POLL_MS,
  type PlanningView,
} from "../lib/planning-state";
import { SCHEDULER_HEALTH_EXPIRY_MS, type SchedulerHealth } from "../lib/scheduler-health";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
const flush = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};
afterEach(() => {
  vi.useRealTimers();
});

const snapshot = (patch: Partial<PlanningSnapshot> = {}): PlanningSnapshot => ({
  schema: "nxt-planning/v1",
  environment: "SIMULATION",
  mode: "MANUAL_LED",
  server_time_utc: "2026-09-16T08:00:00Z",
  context: {
    site_id: "pilot-course-a",
    deployment_id: "pilot-a-edge-task-sim-v0",
    site_timezone: "Asia/Shanghai",
    zone_ids: ["Z1"],
    robot_ids: ["picker-01"],
  },
  latest_input: null,
  plans: [],
  confirmations: [],
  outcomes: [],
  ...patch,
});

const confirmation: ConfirmationRequest = {
  schema: "nxt-planning-confirmation/v1",
  request_id: "confirmations-new-id",
  plan_id: "plan_example_001",
  plan_version: 1,
  operator: "course-manager",
};

const receipt = (requestId: string, disposition: "created" | "duplicate" = "created"): MutationReceipt =>
  ({
    schema: "nxt-planning/v1",
    disposition,
    request_id: requestId,
    record: { confirmation_id: "confirmation_example_001", plan_id: "plan_example_001", plan_version: 1 },
  }) as unknown as MutationReceipt;

function harnessWith(options: Parameters<typeof createPlanningController>[2]) {
  const snapshots: ReturnType<typeof deferred<PlanningSnapshot>>[] = [];
  const submits: ReturnType<typeof deferred<MutationReceipt>>[] = [];
  const lookups: ReturnType<typeof deferred<MutationReceipt>>[] = [];
  const client = {
    snapshot: vi.fn(() => {
      const next = deferred<PlanningSnapshot>();
      snapshots.push(next);
      return next.promise;
    }),
    submit: vi.fn(() => {
      const next = deferred<MutationReceipt>();
      submits.push(next);
      return next.promise;
    }),
    lookup: vi.fn(() => {
      const next = deferred<MutationReceipt>();
      lookups.push(next);
      return next.promise;
    }),
  };
  const published: PlanningView[] = [];
  const controller = createPlanningController(client, (view) => published.push(view), options);
  const last = () => published[published.length - 1];
  const ready = async () => {
    controller.start();
    snapshots[0].resolve(snapshot());
    await flush();
  };
  return { client, snapshots, submits, lookups, published, controller, last, ready };
}
/** Existing scenarios run over a fresh RUNNING scheduler reading. */
const HEALTHY: SchedulerHealth = {
  status: "fresh",
  scheduler: { state: "RUNNING", detail: null },
  observedAtUtc: "2026-09-16T08:00:00Z",
  observedAtMs: 1_000_000,
  error: null,
};
const harness = () => harnessWith({ health: () => HEALTHY, now: () => 1_000_000 });

describe("planning controller", () => {
  it("loads the snapshot, enables writes, and polls without overlapping reads", async () => {
    vi.useFakeTimers();
    const h = harness();
    controllerStart: {
      h.controller.start();
      expect(h.last()).toMatchObject({ data: null, loading: true, write: null, unavailable: false });
      expect(canWritePlanning(h.last())).toBe(false);
      break controllerStart;
    }
    await vi.advanceTimersByTimeAsync(PLANNING_POLL_MS * 3);
    expect(h.client.snapshot).toHaveBeenCalledTimes(1); // still waiting: no overlap
    h.snapshots[0].resolve(snapshot());
    await flush();
    expect(h.last().data?.server_time_utc).toBe("2026-09-16T08:00:00Z");
    expect(canWritePlanning(h.last())).toBe(true);
    await vi.advanceTimersByTimeAsync(PLANNING_POLL_MS - 1);
    expect(h.client.snapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.client.snapshot).toHaveBeenCalledTimes(2);
    h.controller.stop();
  });

  it("commits a created write, keeps writes locked until the post-write refresh, then records the receipt", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit("confirmations", confirmation);
    await flush();
    expect(h.last().write).toMatchObject({ status: "in_flight", kind: "confirmations", requestId: "confirmations-new-id" });
    expect(h.last().busy).toBe(true);
    expect(canWritePlanning(h.last())).toBe(false);
    await expect(h.controller.submit("confirmations", { ...confirmation, request_id: "second" })).rejects.toThrow(/in flight/i);
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    h.submits[0].resolve(receipt("confirmations-new-id"));
    await flush();
    expect(h.last().busy).toBe(true); // refresh not committed yet
    expect(h.client.snapshot).toHaveBeenCalledTimes(2);
    h.snapshots[1].resolve(snapshot({ server_time_utc: "2026-09-16T08:02:00Z" }));
    const result = await pending;
    expect(result.disposition).toBe("created");
    expect(h.last()).toMatchObject({ busy: false, error: null });
    expect(h.last().write).toMatchObject({ status: "committed", requestId: "confirmations-new-id" });
    expect(h.last().data?.server_time_utc).toBe("2026-09-16T08:02:00Z");
    expect(canWritePlanning(h.last())).toBe(true);
    h.controller.stop();
  });

  it("keeps the original committed request ID when a duplicate receipt answers a new ID", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit("confirmations", confirmation);
    await flush();
    h.submits[0].resolve(receipt("demo-confirm-001", "duplicate"));
    await flush();
    h.snapshots[1].resolve(snapshot());
    const result = await pending;
    expect(result.request_id).toBe("demo-confirm-001");
    const write = h.last().write;
    expect(write).toMatchObject({ status: "committed", requestId: "confirmations-new-id" });
    expect(write?.status === "committed" && write.receipt.request_id).toBe("demo-confirm-001");
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    h.controller.stop();
  });

  it("holds an unknown outcome, refuses new writes, and recovers through the original request ID", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit("confirmations", confirmation);
    await flush();
    h.submits[0].reject(new ManagerApiError(503, { code: "planning_result_unknown", detail: "no reliable receipt" }));
    await flush();
    h.snapshots[1].resolve(snapshot());
    await expect(pending).rejects.toThrow("planning_result_unknown");
    expect(h.last().write).toMatchObject({ status: "unknown", requestId: "confirmations-new-id", recovering: false });
    const held = h.last().write;
    expect(held?.status === "unknown" && held.body).toEqual(confirmation);
    expect(canWritePlanning(h.last())).toBe(false);
    await expect(h.controller.submit("confirmations", { ...confirmation, request_id: "fresh" })).rejects.toThrow(/unknown/i);
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    const recovery = h.controller.recover();
    await flush();
    expect(h.last().write).toMatchObject({ status: "unknown", recovering: true });
    expect(h.client.lookup).toHaveBeenCalledWith("confirmations-new-id");
    h.lookups[0].resolve(receipt("confirmations-new-id", "duplicate"));
    await flush();
    h.snapshots[2].resolve(snapshot());
    const found = await recovery;
    expect(found.disposition).toBe("duplicate");
    expect(h.last().write).toMatchObject({ status: "committed", requestId: "confirmations-new-id" });
    expect(canWritePlanning(h.last())).toBe(true);
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    h.controller.stop();
  });

  it("replays the identical request when the lookup finds nothing, never a new ID", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit("confirmations", confirmation);
    await flush();
    h.submits[0].reject(new TypeError("fetch failed"));
    await flush();
    h.snapshots[1].resolve(snapshot());
    await expect(pending).rejects.toThrow("fetch failed");
    expect(h.last().write?.status).toBe("unknown");
    const recovery = h.controller.recover();
    await flush();
    h.lookups[0].reject(new ManagerApiError(404, { code: "planning_request_not_found", detail: "not in verified evidence" }));
    await flush();
    expect(h.client.submit).toHaveBeenCalledTimes(2);
    expect(h.client.submit.mock.calls[1]).toEqual(["confirmations", confirmation]);
    h.submits[1].resolve(receipt("demo-confirm-001", "duplicate"));
    await flush();
    h.snapshots[2].resolve(snapshot());
    const result = await recovery;
    expect(result.request_id).toBe("demo-confirm-001");
    expect(h.last().write).toMatchObject({ status: "committed", requestId: "confirmations-new-id" });
    h.controller.stop();
  });

  it("stays unknown when recovery itself cannot reach the service", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit("inputs", { schema: "nxt-planning-input/v1", request_id: "inputs-1" } as never);
    await flush();
    h.submits[0].reject(new TypeError("fetch failed"));
    await flush();
    h.snapshots[1].reject(new TypeError("fetch failed"));
    await expect(pending).rejects.toThrow("fetch failed");
    expect(h.last().write?.status).toBe("unknown");
    expect(h.last().error).toContain("fetch failed");
    const recovery = h.controller.recover();
    await flush();
    h.lookups[0].reject(new TypeError("still offline"));
    await flush();
    h.snapshots[2].reject(new TypeError("still offline"));
    await expect(recovery).rejects.toThrow("still offline");
    expect(h.last().write).toMatchObject({ status: "unknown", requestId: "inputs-1", recovering: false });
    expect(canWritePlanning(h.last())).toBe(false);
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    h.controller.stop();
  });

  it("treats a 4xx contract refusal as a definite rejection with its code", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit("plans", { schema: "nxt-planning-request/v1", request_id: "plans-1" } as never);
    await flush();
    h.submits[0].reject(new ManagerApiError(409, { code: "planning_conflict", detail: "plan requires the current input revision" }));
    await flush();
    h.snapshots[1].resolve(snapshot({ server_time_utc: "2026-09-16T08:03:00Z" }));
    await expect(pending).rejects.toMatchObject({ code: "planning_conflict" });
    expect(h.last().write).toMatchObject({ status: "rejected", code: "planning_conflict", requestId: "plans-1" });
    expect(h.last().notice).toBeNull();
    expect(canWritePlanning(h.last())).toBe(true);
    h.controller.acknowledgeWrite();
    expect(h.last().write).toBeNull();
    h.controller.stop();
  });

  it("distinguishes a saved write whose refresh failed from an unknown outcome", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit("confirmations", confirmation);
    await flush();
    h.submits[0].resolve(receipt("confirmations-new-id"));
    await flush();
    h.snapshots[1].reject(new TypeError("fetch failed"));
    await expect(pending).resolves.toMatchObject({ disposition: "created" });
    expect(h.last().write?.status).toBe("committed");
    expect(h.last().error).toContain("fetch failed");
    expect(canWritePlanning(h.last())).toBe(false);
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    h.controller.stop();
  });

  it("never lets a pre-write read overwrite the post-write snapshot", async () => {
    const h = harness();
    await h.ready();
    void h.controller.refresh();
    await flush();
    const pending = h.controller.submit("confirmations", confirmation);
    await flush();
    h.submits[0].resolve(receipt("confirmations-new-id"));
    await flush();
    expect(h.client.snapshot).toHaveBeenCalledTimes(3);
    h.snapshots[2].resolve(snapshot({ server_time_utc: "2026-09-16T08:09:00Z" }));
    await pending;
    h.snapshots[1].resolve(snapshot({ server_time_utc: "2026-09-16T08:01:00Z" }));
    await flush();
    expect(h.last().data?.server_time_utc).toBe("2026-09-16T08:09:00Z");
    h.controller.stop();
  });

  it("marks an old service without the planning route as unavailable instead of stale", async () => {
    const h = harness();
    h.controller.start();
    h.snapshots[0].reject(new ManagerApiError(404, { code: "not_found", detail: "unknown API path: /api/v1/planning" }));
    await flush();
    expect(h.last()).toMatchObject({ unavailable: true, data: null });
    expect(canWritePlanning(h.last())).toBe(false);
    const again = h.controller.refresh();
    h.snapshots[1].resolve(snapshot());
    await again;
    expect(h.last().unavailable).toBe(false);
    h.controller.stop();
  });

  it("stops publishing after unmount and refuses later writes", async () => {
    vi.useFakeTimers();
    const h = harness();
    await h.ready();
    const before = h.published.length;
    h.controller.stop();
    await vi.advanceTimersByTimeAsync(PLANNING_POLL_MS * 2);
    expect(h.client.snapshot).toHaveBeenCalledTimes(1);
    expect(h.published).toHaveLength(before);
    await expect(h.controller.submit("confirmations", confirmation)).rejects.toThrow(/not connected/);
  });
});

describe("recovery keeps UNKNOWN unless the service answers about the original request", () => {
  async function unknownWrite() {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit("confirmations", confirmation);
    await flush();
    h.submits[0].reject(new TypeError("fetch failed"));
    await flush();
    h.snapshots[1].resolve(snapshot());
    await expect(pending).rejects.toThrow("fetch failed");
    expect(h.last().write?.status).toBe("unknown");
    return h;
  }

  for (const [label, error] of [
    ["a plain 404 from a runner without the lookup route", new ManagerApiError(404, { code: "not_found", detail: "unknown API path" })],
    ["a 400 on the lookup itself", new ManagerApiError(400, { code: "planning_invalid_request", detail: "request ID path must be valid UTF-8" })],
    ["a 409 on the lookup", new ManagerApiError(409, { code: "planning_conflict", detail: "unexpected" })],
    ["a 503 on the lookup", new ManagerApiError(503, { code: "planning_unavailable", detail: "evidence unreadable" })],
  ] as const) {
    it(`keeps UNKNOWN, the original ID and the request content after ${label}`, async () => {
      const h = await unknownWrite();
      const recovery = h.controller.recover();
      await flush();
      h.lookups[0].reject(error);
      await flush();
      h.snapshots[2].resolve(snapshot());
      await expect(recovery).rejects.toThrow(error.code);
      const write = h.last().write;
      expect(write).toMatchObject({ status: "unknown", kind: "confirmations", requestId: "confirmations-new-id", recovering: false });
      expect(write?.status === "unknown" && write.body).toEqual(confirmation);
      expect(write?.status === "unknown" && write.detail).toContain(error.code);
      expect(canWritePlanning(h.last())).toBe(false);
      expect(h.client.submit).toHaveBeenCalledTimes(1); // no replay, no new ID
      await expect(h.controller.submit("confirmations", { ...confirmation, request_id: "fresh" })).rejects.toThrow(/unknown/i);
      h.controller.stop();
    });
  }

  it("treats a definite refusal of the identical replay as the answer for that request", async () => {
    const h = await unknownWrite();
    const recovery = h.controller.recover();
    await flush();
    h.lookups[0].reject(new ManagerApiError(404, { code: "planning_request_not_found", detail: "no committed request" }));
    await flush();
    expect(h.client.submit).toHaveBeenCalledTimes(2);
    expect(h.client.submit.mock.calls[1]).toEqual(["confirmations", confirmation]);
    h.submits[1].reject(new ManagerApiError(409, { code: "planning_expired", detail: "input or plan validity elapsed" }));
    await flush();
    h.snapshots[2].resolve(snapshot());
    await expect(recovery).rejects.toThrow("planning_expired");
    expect(h.last().write).toMatchObject({ status: "rejected", code: "planning_expired", requestId: "confirmations-new-id" });
    expect(canWritePlanning(h.last())).toBe(true);
    h.controller.stop();
  });

  it("keeps UNKNOWN when the identical replay itself gets no reliable receipt", async () => {
    const h = await unknownWrite();
    const recovery = h.controller.recover();
    await flush();
    h.lookups[0].reject(new ManagerApiError(404, { code: "planning_request_not_found", detail: "no committed request" }));
    await flush();
    h.submits[1].reject(new ManagerApiError(503, { code: "planning_result_unknown", detail: "no reliable receipt" }));
    await flush();
    h.snapshots[2].resolve(snapshot());
    await expect(recovery).rejects.toThrow("planning_result_unknown");
    expect(h.last().write).toMatchObject({ status: "unknown", requestId: "confirmations-new-id", recovering: false });
    const held = h.last().write;
    expect(held?.status === "unknown" && held.body).toEqual(confirmation);
    expect(canWritePlanning(h.last())).toBe(false);
    h.controller.stop();
  });
});

describe("planning writes follow the shared scheduler health", () => {
  const fresh = (state: "RUNNING" | "FAILED", detail: string | null = null): SchedulerHealth => ({
    status: "fresh",
    scheduler: { state, detail },
    observedAtUtc: "2026-09-16T08:00:00Z",
    observedAtMs: 1_000_000,
    error: null,
  });

  function healthHarness(initial: SchedulerHealth) {
    let health = initial;
    const h = harnessWith({ health: () => health, now: () => 1_000_000 });
    return { ...h, setHealth: (next: SchedulerHealth) => { health = next; h.controller.notifyHealth(); } };
  }

  it("refuses new writes with the runner's reason while the scheduler is FAILED, without sending anything", async () => {
    const h = healthHarness(fresh("FAILED", "planning_result_unknown: stopped after an unreadable write"));
    await h.ready();
    expect(h.last().health.scheduler?.state).toBe("FAILED");
    expect(canWritePlanning(h.last())).toBe(false);
    await expect(h.controller.submit("confirmations", confirmation)).rejects.toThrow(/stopped after an unreadable write/);
    expect(h.client.submit).not.toHaveBeenCalled();
    h.setHealth(fresh("RUNNING"));
    expect(canWritePlanning(h.last())).toBe(true);
    h.controller.stop();
  });

  it.each([
    ["unconfirmed", { status: "unknown", scheduler: null, observedAtUtc: null, observedAtMs: null, error: null }, /not yet been confirmed/],
    ["stale", { ...fresh("RUNNING"), status: "stale", error: "Failed to fetch" }, /could not be refreshed \(Failed to fetch\)/],
    ["unavailable", { status: "unavailable", scheduler: null, observedAtUtc: null, observedAtMs: null, error: "404" }, /does not provide pilot task operations/],
    ["already expired at publish", { ...fresh("RUNNING"), observedAtMs: 1_000_000 - SCHEDULER_HEALTH_EXPIRY_MS - 1 }, /older than 15 seconds/],
  ] as const)("blocks writes with its own reason while the scheduler reading is %s", async (_label, health, reason) => {
    const h = healthHarness(health as SchedulerHealth);
    await h.ready();
    expect(canWritePlanning(h.last())).toBe(false);
    await expect(h.controller.submit("confirmations", confirmation)).rejects.toThrow(reason);
    expect(h.client.submit).not.toHaveBeenCalled();
    h.controller.stop();
  });

  it("expires a fresh reading as the clock advances: refused at submit time, and published as expired when the shared owner notifies", async () => {
    vi.useFakeTimers();
    let t = 1_000_000;
    const health: SchedulerHealth = { ...fresh("RUNNING"), observedAtMs: 1_000_000 };
    const h = harnessWith({ health: () => health, now: () => t, pollMs: 0 }); // no planning poll and no private timer
    await h.ready();
    expect(canWritePlanning(h.last())).toBe(true);
    t += SCHEDULER_HEALTH_EXPIRY_MS + 1; // time passes; nothing republishes on its own
    await expect(h.controller.submit("confirmations", confirmation)).rejects.toThrow(/older than 15 seconds/);
    expect(h.client.submit).not.toHaveBeenCalled();
    const before = h.published.length;
    await vi.advanceTimersByTimeAsync(SCHEDULER_HEALTH_EXPIRY_MS + 5);
    expect(h.published.length).toBe(before); // the controller owns no expiry timer
    h.controller.notifyHealth(); // the shared owner's expiry timer notifies both panels
    expect(h.last().health.status).toBe("expired");
    expect(canWritePlanning(h.last())).toBe(false);
    h.controller.stop();
  });

  it("keeps the UNKNOWN request and its recovery entry while the scheduler is FAILED, and a recovered receipt does not change the health", async () => {
    const h = healthHarness(fresh("RUNNING"));
    await h.ready();
    const pending = h.controller.submit("confirmations", confirmation);
    await flush();
    h.submits[0].reject(new TypeError("fetch failed"));
    await flush();
    h.snapshots[1].resolve(snapshot());
    await expect(pending).rejects.toThrow("fetch failed");
    h.setHealth(fresh("FAILED", "scheduler stopped"));
    expect(h.last().write).toMatchObject({ status: "unknown", requestId: "confirmations-new-id" });
    expect(canWritePlanning(h.last())).toBe(false);
    // recovery stays available: the lookup is a read, the replay is judged by the service
    const recovery = h.controller.recover();
    await flush();
    h.lookups[0].reject(new ManagerApiError(404, { code: "planning_request_not_found", detail: "absent" }));
    await flush();
    h.submits[1].reject(new ManagerApiError(503, { code: "planning_unavailable", detail: "scheduler is not running" }));
    await flush();
    h.snapshots[2].resolve(snapshot());
    await expect(recovery).rejects.toThrow("planning_unavailable");
    expect(h.last().write).toMatchObject({ status: "unknown", requestId: "confirmations-new-id", recovering: false });
    expect(h.last().health.scheduler?.state).toBe("FAILED");
    // a later successful lookup recovers the receipt but still does not restart the scheduler
    const second = h.controller.recover();
    await flush();
    h.lookups[1].resolve(receipt("confirmations-new-id", "duplicate"));
    await flush();
    h.snapshots[3].resolve(snapshot());
    await second;
    expect(h.last().write?.status).toBe("committed");
    expect(h.last().health.scheduler?.state).toBe("FAILED");
    expect(canWritePlanning(h.last())).toBe(false);
    h.controller.stop();
  });
});
