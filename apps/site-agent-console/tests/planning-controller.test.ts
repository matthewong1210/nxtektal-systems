import { afterEach, describe, expect, it, vi } from "vitest";

import { ManagerApiError } from "../lib/api";
import type {
  ConfirmationRequest,
  MutationReceipt,
  PlanningRequestBody,
  PlanningSnapshot,
  WriteKind,
} from "../lib/planning";
import {
  canWritePlanning,
  createPlanningController,
  PLANNING_POLL_MS,
  taskOpsOperationForPlanningWrite,
  type PlanningView,
} from "../lib/planning-state";
import { SCHEDULER_HEALTH_EXPIRY_MS, type SchedulerHealth } from "../lib/scheduler-health";
import type { TaskOpsWriteOperation } from "../lib/task-ops";

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

type ControllerOptions = Parameters<typeof createPlanningController>[2] & {
  replayBlocker?: (kind: WriteKind) => string | null;
};

function harnessWith(options: ControllerOptions) {
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
  const controllerOptions = { replayBlocker: () => null, ...options };
  const controller = createPlanningController(client, (view) => published.push(view), controllerOptions);
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

  const disabledReplayCases = [
    [
      "inputs",
      { schema: "nxt-planning-input/v1", request_id: "inputs-original-id" } as PlanningRequestBody,
      "planning_inputs_create",
      "New operating input is not installed on this service",
    ],
    [
      "plans",
      { schema: "nxt-planning-request/v1", request_id: "plans-original-id" } as PlanningRequestBody,
      "planning_plans_create",
      "New or revised plan is not installed on this service",
    ],
    [
      "confirmations",
      confirmation,
      "planning_confirmations_create",
      "The task service write capabilities are unknown",
    ],
  ] satisfies [WriteKind, PlanningRequestBody, TaskOpsWriteOperation, string][];

  it.each(disabledReplayCases)(
    "keeps an UNKNOWN %s request without replay when the latest capability blocks it",
    async (kind, body, expectedOperation, blockedReason) => {
      const h = harnessWith({ health: () => HEALTHY, now: () => 1_000_000, replayBlocker: () => null });
      await h.ready();
      const pending = h.controller.submit(kind, body);
      await flush();
      h.submits[0].reject(new TypeError("fetch failed"));
      await flush();
      h.snapshots[1].resolve(snapshot());
      await expect(pending).rejects.toThrow("fetch failed");

      const recovery = h.controller.recover();
      await flush();
      expect(h.client.lookup).toHaveBeenCalledWith(body.request_id);
      h.controller.setReplayBlocker((currentKind) =>
        taskOpsOperationForPlanningWrite(currentKind) === expectedOperation ? blockedReason : null,
      ); // capability changes while the GET is waiting
      h.lookups[0].reject(new ManagerApiError(404, {
        code: "planning_request_not_found",
        detail: "no committed request",
      }));
      await flush();
      expect(h.client.submit).toHaveBeenCalledTimes(1); // original attempt only; no replay POST
      h.snapshots[2].resolve(snapshot());
      await expect(recovery).rejects.toThrow(blockedReason);

      const write = h.last().write;
      expect(write).toMatchObject({ status: "unknown", kind, requestId: body.request_id, recovering: false });
      expect(write?.status === "unknown" && write.body).toEqual(body);
      expect(write?.status === "unknown" && write.detail).toContain(blockedReason);
      h.controller.stop();
    },
  );

  it("uses a found receipt even when the current capability no longer permits that write", async () => {
    let blocker: string | null = null;
    const h = harnessWith({ health: () => HEALTHY, now: () => 1_000_000, replayBlocker: () => blocker });
    await h.ready();
    const pending = h.controller.submit("confirmations", confirmation);
    await flush();
    h.submits[0].reject(new TypeError("fetch failed"));
    await flush();
    h.snapshots[1].resolve(snapshot());
    await expect(pending).rejects.toThrow("fetch failed");

    const recovery = h.controller.recover();
    await flush();
    blocker = "Plan confirmation is not installed on this service";
    h.lookups[0].resolve(receipt(confirmation.request_id, "duplicate"));
    await flush();
    h.snapshots[2].resolve(snapshot());
    await expect(recovery).resolves.toMatchObject({ request_id: confirmation.request_id });
    expect(h.last().write).toMatchObject({ status: "committed", requestId: confirmation.request_id });
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    h.controller.stop();
  });

  it("replays a supported outcome once with the same ID and body when lookup proves it absent", async () => {
    const body = {
      schema: "nxt-planning-outcome/v1",
      request_id: "outcomes-original-id",
    } as PlanningRequestBody;
    const h = harnessWith({ health: () => HEALTHY, now: () => 1_000_000, replayBlocker: () => null });
    await h.ready();
    const pending = h.controller.submit("outcomes", body);
    await flush();
    h.submits[0].reject(new TypeError("fetch failed"));
    await flush();
    h.snapshots[1].resolve(snapshot());
    await expect(pending).rejects.toThrow("fetch failed");

    const recovery = h.controller.recover();
    await flush();
    h.lookups[0].reject(new ManagerApiError(404, {
      code: "planning_request_not_found",
      detail: "no committed request",
    }));
    await flush();
    expect(h.client.submit).toHaveBeenCalledTimes(2);
    expect(h.client.submit.mock.calls[1]).toEqual(["outcomes", body]);
    h.submits[1].resolve(receipt(body.request_id, "created"));
    await flush();
    h.snapshots[2].resolve(snapshot());
    await expect(recovery).resolves.toMatchObject({ request_id: body.request_id });
    await expect(h.controller.recover()).rejects.toThrow(/no planning change with an unknown outcome/i);
    expect(h.client.submit).toHaveBeenCalledTimes(2);
    h.controller.stop();
  });

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

  it("rechecks health after lookup, blocks an absent replay, but still recovers an existing receipt", async () => {
    const h = healthHarness(fresh("RUNNING"));
    await h.ready();
    const pending = h.controller.submit("confirmations", confirmation);
    await flush();
    h.submits[0].reject(new TypeError("fetch failed"));
    await flush();
    h.snapshots[1].resolve(snapshot());
    await expect(pending).rejects.toThrow("fetch failed");
    expect(h.last().write).toMatchObject({ status: "unknown", requestId: "confirmations-new-id" });
    // The GET starts while healthy. Health changes while that read is waiting,
    // so the not-found branch must use the latest gate before any replay POST.
    const recovery = h.controller.recover();
    await flush();
    h.setHealth(fresh("FAILED", "scheduler stopped"));
    expect(canWritePlanning(h.last())).toBe(false);
    h.lookups[0].reject(new ManagerApiError(404, { code: "planning_request_not_found", detail: "absent" }));
    await flush();
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    h.snapshots[2].resolve(snapshot());
    await expect(recovery).rejects.toThrow("scheduler stopped");
    expect(h.last().write).toMatchObject({ status: "unknown", requestId: "confirmations-new-id", recovering: false });
    expect(h.last().health.scheduler?.state).toBe("FAILED");
    // A later GET that finds the committed request is read-only recovery and
    // must succeed without either write gate becoming healthy.
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
