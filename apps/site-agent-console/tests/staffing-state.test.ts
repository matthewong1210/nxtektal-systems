import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ManagerApiError } from "../lib/api";
import * as staffingActionsRuntime from "../lib/staffing-actions";
import { createStaffingActions } from "../lib/staffing-actions";
import * as staffingStateRuntime from "../lib/staffing-state";
import {
  STAFFING_REQUEST_POLL_MS,
  STAFFING_SNAPSHOT_POLL_MS,
  createStaffingController,
  type ExceptionDraft,
  type StaffingController,
  type StaffingView,
} from "../lib/staffing-state";
import type {
  ExceptionRecordRequest,
  GenerationProjection,
  ManagerModifyRequest,
  ManagerResponseRequest,
  OperationKind,
  RosterImportRequest,
  StaffingClient,
  StaffingDateSnapshot,
  StaffingMutation,
  StaffingReceipt,
  StaffingRequestBody,
  SuggestionGenerateRequest,
} from "../lib/staffing";
import type { RosterImportDraft } from "../lib/staffing-csv";

const EXAMPLES_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "..",
  "simulation",
  "docs",
  "contracts",
  "staffing-v1",
  "examples",
);

type Exchange = {
  name: string;
  request?: unknown;
  body: { data: unknown };
};

const exchanges = readdirSync(EXAMPLES_DIR)
  .filter((name) => name.endsWith(".json") && name !== "errors.json")
  .flatMap((name) => {
    const document = JSON.parse(readFileSync(join(EXAMPLES_DIR, name), "utf8")) as {
      exchanges: Exchange[];
    };
    return document.exchanges;
  });

function exchange(name: string): Exchange {
  const value = exchanges.find((item) => item.name === name);
  if (!value) throw new Error(`missing staffing fixture ${name}`);
  return value;
}

function snapshot(name = "current-empty"): StaffingDateSnapshot {
  return structuredClone(exchange(name).body.data) as StaffingDateSnapshot;
}

function receipt(name: string): StaffingReceipt {
  return structuredClone(exchange(name).body.data) as StaffingReceipt;
}

function mutation(name: string): StaffingMutation {
  const item = exchange(name);
  const body = structuredClone(item.request) as StaffingRequestBody;
  switch (name) {
    case "roster-committed":
      return { operationKind: "roster-import", body: body as RosterImportRequest };
    case "exception-recorded":
      return { operationKind: "exception-record", body: body as ExceptionRecordRequest };
    case "generation-reserved":
    case "generation-in-progress":
    case "suggestion-issued":
      return { operationKind: "suggestion-generate", body: body as SuggestionGenerateRequest };
    case "manager-accepted":
    case "manager-modified":
    case "manager-rejected":
      return {
        operationKind: "manager-response",
        targetId: (item.body.data as StaffingReceipt & { record: { suggestion_id: string } }).record.suggestion_id,
        body: body as ManagerResponseRequest,
      };
    default:
      throw new Error(`unsupported mutation fixture ${name}`);
  }
}

function projectionFrom(value: StaffingReceipt): GenerationProjection {
  if (value.operation_kind !== "suggestion-generate") throw new Error("not a generation receipt");
  const common = {
    suggestion_id: value.record.suggestion_id,
    request_id: value.request_id,
    operation_id: value.operation_id,
    state: value.state,
    basis: value.record.basis,
    retry_of: value.record.retry_of,
    candidates: "candidates" in value.record ? value.record.candidates : [],
    coverage_gaps: "coverage_gaps" in value.record ? value.record.coverage_gaps : [],
    provenance: value.record.provenance,
    failure_code: "failure_code" in value.record ? value.record.failure_code : null,
    manager_response: null,
  };
  return common as GenerationProjection;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flush(): Promise<void> {
  for (let index = 0; index < 16; index += 1) await Promise.resolve();
}

type HarnessOptions = Parameters<typeof createStaffingController>[1];

function harness(options: HarnessOptions = {}) {
  const reads: ReturnType<typeof deferred<StaffingDateSnapshot>>[] = [];
  const submits: ReturnType<typeof deferred<StaffingReceipt>>[] = [];
  const lookups: ReturnType<typeof deferred<StaffingReceipt>>[] = [];
  const mutationLookups: ReturnType<typeof deferred<StaffingReceipt>>[] = [];
  const client: StaffingClient = {
    current: vi.fn(() => {
      const next = deferred<StaffingDateSnapshot>();
      reads.push(next);
      return next.promise;
    }),
    date: vi.fn(),
    submit: vi.fn(() => {
      const next = deferred<StaffingReceipt>();
      submits.push(next);
      return next.promise;
    }),
    lookup: vi.fn(() => {
      const next = deferred<StaffingReceipt>();
      lookups.push(next);
      return next.promise;
    }),
    lookupMutation: vi.fn(() => {
      const next = deferred<StaffingReceipt>();
      mutationLookups.push(next);
      return next.promise;
    }),
  };
  const controller = createStaffingController(client, {
    snapshotPollMs: 0,
    requestPollMs: 0,
    getManagerLabel: () => "经理甲",
    requestIdFactory: (kind) => `new-${kind}-request`,
    ...options,
  });
  const published: StaffingView[] = [];
  controller.subscribe((view) => published.push(view));
  const ready = async (value = snapshot()) => {
    controller.start();
    reads[0].resolve(value);
    await flush();
  };
  return {
    client,
    controller,
    reads,
    submits,
    lookups,
    mutationLookups,
    published,
    ready,
    last: () => controller.view(),
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("staffing controller surface", () => {
  it("freezes the runtime export surface", () => {
    expect(Object.keys(staffingStateRuntime).sort()).toEqual([
      "STAFFING_REQUEST_POLL_MS",
      "STAFFING_SNAPSHOT_POLL_MS",
      "createStaffingController",
      "initialStaffingView",
    ]);
    expect(Object.keys(staffingActionsRuntime)).toEqual(["createStaffingActions"]);
  });
});

describe("staffing state controller", () => {
  it("loads, polls serially, and never overlaps snapshot reads", async () => {
    vi.useFakeTimers();
    const h = harness({ snapshotPollMs: STAFFING_SNAPSHOT_POLL_MS });
    h.controller.start();
    expect(h.last().read).toMatchObject({ status: "loading", stale: false });

    await vi.advanceTimersByTimeAsync(STAFFING_SNAPSHOT_POLL_MS * 2);
    expect(h.client.current).toHaveBeenCalledTimes(1);
    h.reads[0].resolve(snapshot());
    await flush();
    expect(h.last().read).toEqual({ status: "ready", stale: false, detail: null });

    await vi.advanceTimersByTimeAsync(STAFFING_SNAPSHOT_POLL_MS - 1);
    expect(h.client.current).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.client.current).toHaveBeenCalledTimes(2);
    h.controller.stop();
  });

  it("deep-freezes one detached mutation and reuses it for lookup and the sole replay", async () => {
    const h = harness();
    await h.ready();
    const original = mutation("manager-modified");
    const pending = h.controller.submit(original);
    await flush();
    const sent = vi.mocked(h.client.submit).mock.calls[0][0];
    expect(sent).not.toBe(original);
    expect(Object.isFrozen(sent)).toBe(true);
    expect(Object.isFrozen((sent.body as ManagerModifyRequest).edited_operations)).toBe(true);

    if (original.operationKind !== "manager-response" || original.body.kind !== "MODIFY") throw new Error("fixture");
    original.targetId = "changed-target";
    original.body.edited_operations[0] = { operation: "REMOVE", assignment_id: "changed-assignment" };
    h.submits[0].reject(new TypeError("network lost"));
    await expect(pending).rejects.toThrow("network lost");
    expect(h.last().write).toMatchObject({ status: "unknown", replayedAfterNotFound: false });

    const recovery = h.controller.recover();
    await flush();
    expect(vi.mocked(h.client.lookupMutation).mock.calls[0][0]).toBe(sent);
    h.mutationLookups[0].reject(new ManagerApiError(404, {
      code: "staffing_request_not_found",
      detail: "absent",
    }));
    await flush();
    expect(h.last().write).toMatchObject({ status: "in_flight", replayedAfterNotFound: true });
    expect(vi.mocked(h.client.submit).mock.calls[1][0]).toBe(sent);
    expect((vi.mocked(h.client.submit).mock.calls[1][0] as Extract<StaffingMutation, { operationKind: "manager-response" }>).targetId)
      .not.toBe("changed-target");

    h.submits[1].reject(new TypeError("still unknown"));
    await expect(recovery).rejects.toThrow("still unknown");
    const second = h.controller.recover();
    await flush();
    h.mutationLookups[1].reject(new ManagerApiError(404, {
      code: "staffing_request_not_found",
      detail: "still absent",
    }));
    await expect(second).rejects.toThrow("staffing_request_not_found");
    expect(h.client.submit).toHaveBeenCalledTimes(2);
    h.controller.stop();
  });

  it.each([
    new TypeError("lookup offline"),
    new ManagerApiError(400, { code: "staffing_invalid_request", detail: "bad lookup" }),
    new ManagerApiError(409, { code: "staffing_conflict", detail: "ambiguous lookup" }),
    new ManagerApiError(429, { code: "staffing_busy", detail: "irrelevant to lookup" }),
    new ManagerApiError(503, { code: "staffing_unavailable", detail: "offline" }),
    new ManagerApiError(200, { code: "invalid_staffing_response", detail: "malformed" }),
  ])("keeps every non-not-found recovery failure unknown without POST replay: %#", async (cause) => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit(mutation("generation-reserved"));
    await flush();
    h.submits[0].reject(new TypeError("initial result lost"));
    await expect(pending).rejects.toThrow("initial result lost");

    const recovery = h.controller.recover();
    await flush();
    h.mutationLookups[0].reject(cause);
    await expect(recovery).rejects.toBe(cause);
    expect(h.last().write).toMatchObject({ status: "unknown", recovering: false });
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    h.controller.stop();
  });

  it("commits a fully correlated recovery receipt and keeps RESULT_UNKNOWN as a durable terminal", async () => {
    const h = harness();
    await h.ready();
    const original = mutation("generation-reserved");
    if (original.operationKind !== "suggestion-generate") throw new Error("fixture");
    original.body.request_id = "request-result-unknown";
    const pending = h.controller.submit(original);
    await flush();
    h.submits[0].reject(new TypeError("result lost"));
    await expect(pending).rejects.toThrow("result lost");

    const recovery = h.controller.recover();
    await flush();
    const interrupted = receipt("generation-interrupted");
    h.mutationLookups[0].resolve(interrupted);
    await flush();
    expect(h.last().activeGeneration).toMatchObject({
      state: "RESULT_UNKNOWN",
      suggestion_id: "generation-result-unknown",
    });
    expect(h.client.current).toHaveBeenCalledTimes(2);
    const refreshed = snapshot();
    refreshed.generations = [projectionFrom(interrupted)];
    h.reads[1].resolve(refreshed);
    await expect(recovery).resolves.toEqual(interrupted);
    expect(h.last().write).toMatchObject({ status: "committed", savedButStale: false });
    expect(h.last().activeGeneration?.state).toBe("RESULT_UNKNOWN");
    h.controller.stop();
  });

  it("returns to recoverable unknown when a resolved lookup receipt fails local correlation", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit(mutation("generation-reserved"));
    await flush();
    h.submits[0].reject(new TypeError("result lost"));
    await expect(pending).rejects.toThrow("result lost");

    const recovery = h.controller.recover();
    await flush();
    const mismatched = receipt("generation-in-progress");
    mismatched.request_id = "another-request";
    h.mutationLookups[0].resolve(mismatched);
    await expect(recovery).rejects.toMatchObject({
      status: 200,
      code: "invalid_staffing_response",
    });
    expect(h.last().write).toMatchObject({ status: "unknown", recovering: false });

    const retry = h.controller.recover();
    await flush();
    expect(h.client.lookupMutation).toHaveBeenCalledTimes(2);
    h.mutationLookups[1].reject(new TypeError("still offline"));
    await expect(retry).rejects.toThrow("still offline");
    h.controller.stop();
  });

  it.each([
    [new TypeError("offline"), "unknown"],
    [new ManagerApiError(404, { code: "staffing_request_not_found", detail: "wrong POST error" }), "unknown"],
    [new ManagerApiError(409, { code: "invalid_staffing_response", detail: "mismatched" }), "unknown"],
    [new ManagerApiError(503, { code: "staffing_unavailable", detail: "unknown commit" }), "unknown"],
    [new ManagerApiError(400, { code: "staffing_invalid_request", detail: "invalid" }), "rejected"],
    [new ManagerApiError(404, { code: "staffing_not_found", detail: "target absent" }), "rejected"],
    [new ManagerApiError(409, { code: "staffing_conflict", detail: "conflict" }), "rejected"],
    [new ManagerApiError(409, { code: "staffing_stale_suggestion", detail: "stale" }), "rejected"],
    [new ManagerApiError(413, { code: "body_too_large", detail: "large" }), "rejected"],
  ] as const)("classifies a mutation failure contextually: %#", async (cause, expected) => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit(mutation("roster-committed"));
    await flush();
    h.submits[0].reject(cause);
    await expect(pending).rejects.toBe(cause);
    expect(h.last().write?.status).toBe(expected);
    h.controller.stop();
  });

  it("uses BUSY only for generation, supports exact retry, and allows dismiss without network", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit(mutation("generation-reserved"));
    await flush();
    const sent = vi.mocked(h.client.submit).mock.calls[0][0];
    const busy = new ManagerApiError(429, { code: "staffing_busy", detail: "full" });
    h.submits[0].reject(busy);
    await expect(pending).rejects.toBe(busy);
    expect(h.last().write?.status).toBe("busy");

    const retry = h.controller.retryBusy();
    await flush();
    expect(vi.mocked(h.client.submit).mock.calls[1][0]).toBe(sent);
    h.submits[1].reject(busy);
    await expect(retry).rejects.toBe(busy);
    h.controller.acknowledgeWrite();
    expect(h.last().write).toBeNull();
    expect(h.client.submit).toHaveBeenCalledTimes(2);

    const nonGeneration = h.controller.submit(mutation("roster-committed"));
    await flush();
    h.submits[2].reject(busy);
    await expect(nonGeneration).rejects.toBe(busy);
    expect(h.last().write?.status).toBe("unknown");
    h.controller.stop();
  });

  it("preserves the used replay budget through BUSY retry and a later unknown result", async () => {
    const h = harness();
    await h.ready();
    const initial = h.controller.submit(mutation("generation-reserved"));
    await flush();
    h.submits[0].reject(new TypeError("initial result lost"));
    await expect(initial).rejects.toThrow("initial result lost");

    const recovery = h.controller.recover();
    await flush();
    h.mutationLookups[0].reject(new ManagerApiError(404, {
      code: "staffing_request_not_found",
      detail: "absent",
    }));
    await flush();
    h.submits[1].reject(new ManagerApiError(429, { code: "staffing_busy", detail: "queue full" }));
    await expect(recovery).rejects.toThrow("staffing_busy");
    expect(h.last().write).toMatchObject({ status: "busy", replayedAfterNotFound: true });

    const retry = h.controller.retryBusy();
    await flush();
    h.submits[2].reject(new TypeError("busy retry result lost"));
    await expect(retry).rejects.toThrow("busy retry result lost");
    expect(h.last().write).toMatchObject({ status: "unknown", replayedAfterNotFound: true });

    const secondRecovery = h.controller.recover();
    await flush();
    h.mutationLookups[1].reject(new ManagerApiError(404, {
      code: "staffing_request_not_found",
      detail: "still absent",
    }));
    await expect(secondRecovery).rejects.toThrow("staffing_request_not_found");
    expect(h.client.submit).toHaveBeenCalledTimes(3);
    h.controller.stop();
  });

  it("commits ordinary writes before one required refresh and returns the receipt when refresh fails", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit(mutation("exception-recorded"));
    await flush();
    const committed = receipt("exception-recorded");
    h.submits[0].resolve(committed);
    await flush();
    expect(h.last().write).toMatchObject({ status: "committed", savedButStale: false });
    expect(h.client.current).toHaveBeenCalledTimes(2);
    h.reads[1].reject(new TypeError("refresh failed"));
    await expect(pending).resolves.toEqual(committed);
    expect(h.last().write).toMatchObject({ status: "committed", savedButStale: true });
    expect(h.last().read).toMatchObject({ status: "error", stale: true });
    h.controller.stop();
  });

  it("does not issue a required refresh when a committed subscriber synchronously stops the controller", async () => {
    const h = harness();
    await h.ready();
    const stopOnCommit = h.controller.subscribe((view) => {
      if (view.write?.status === "committed") h.controller.stop();
    });
    const committed = receipt("exception-recorded");
    const pending = h.controller.submit(mutation("exception-recorded"));
    await flush();
    h.submits[0].resolve(committed);
    await expect(pending).resolves.toEqual(committed);

    expect(h.client.current).toHaveBeenCalledTimes(1);
    expect(h.last().write).toMatchObject({ status: "committed", savedButStale: true });
    expect(h.last().read).toMatchObject({ status: "error", stale: true });
    stopOnCommit();
  });

  it("does not issue a snapshot GET when a loading subscriber synchronously stops", async () => {
    const h = harness();
    h.controller.subscribe((view) => {
      if (view.read.status === "loading") h.controller.stop();
    });
    h.controller.start();
    await flush();
    expect(h.client.current).not.toHaveBeenCalled();
    expect(h.last().read.status).toBe("loading");
  });

  it("gates a timer-fired snapshot GET when its loading publication synchronously stops", async () => {
    vi.useFakeTimers();
    const h = harness({ snapshotPollMs: STAFFING_SNAPSHOT_POLL_MS });
    let loadingPublications = 0;
    h.controller.subscribe((view) => {
      if (view.read.status === "loading") {
        loadingPublications += 1;
        if (loadingPublications === 2) h.controller.stop();
      }
    });
    await h.ready();
    await vi.advanceTimersByTimeAsync(STAFFING_SNAPSHOT_POLL_MS);
    expect(h.client.current).toHaveBeenCalledTimes(1);
    expect(loadingPublications).toBe(2);
  });

  it("does not mark an unrelated committed banner stale when a generation refresh is interrupted", async () => {
    vi.useFakeTimers();
    const reserved = receipt("generation-reserved");
    const ongoingSnapshot = snapshot();
    ongoingSnapshot.generations = [projectionFrom(reserved)];
    const h = harness({
      snapshotPollMs: STAFFING_SNAPSHOT_POLL_MS,
      requestPollMs: STAFFING_REQUEST_POLL_MS,
    });
    await h.ready(ongoingSnapshot);

    const exceptionWrite = h.controller.submit(mutation("exception-recorded"));
    await flush();
    const exceptionReceipt = receipt("exception-recorded");
    h.submits[0].resolve(exceptionReceipt);
    await flush();
    const afterException = snapshot();
    afterException.generations = [projectionFrom(reserved)];
    h.reads[1].resolve(afterException);
    await expect(exceptionWrite).resolves.toEqual(exceptionReceipt);
    expect(h.last().write).toMatchObject({ status: "committed", savedButStale: false });

    await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS);
    const terminal = receipt("suggestion-issued");
    if (terminal.operation_kind !== "suggestion-generate") throw new Error("fixture");
    terminal.request_id = reserved.request_id;
    terminal.record.suggestion_id = projectionFrom(reserved).suggestion_id;
    h.lookups[0].resolve(terminal);
    await flush();
    expect(h.client.current).toHaveBeenCalledTimes(3);
    h.controller.stop();

    expect(h.last().write).toMatchObject({
      status: "committed",
      receipt: exceptionReceipt,
      savedButStale: false,
    });
    expect(h.last().read).toMatchObject({ status: "error", stale: true });
    h.reads[2].resolve(afterException);
    await flush();
  });

  it("leaves an unrelated committed banner unchanged when known-generation lookup fails", async () => {
    vi.useFakeTimers();
    const reserved = receipt("generation-reserved");
    const ongoing = snapshot();
    ongoing.generations = [projectionFrom(reserved)];
    const h = harness({
      snapshotPollMs: STAFFING_SNAPSHOT_POLL_MS,
      requestPollMs: STAFFING_REQUEST_POLL_MS,
    });
    await h.ready(ongoing);
    const exceptionWrite = h.controller.submit(mutation("exception-recorded"));
    await flush();
    const exceptionReceipt = receipt("exception-recorded");
    h.submits[0].resolve(exceptionReceipt);
    await flush();
    const refreshed = snapshot();
    refreshed.generations = [projectionFrom(reserved)];
    h.reads[1].resolve(refreshed);
    await exceptionWrite;
    const banner = h.last().write;

    await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS);
    h.lookups[0].reject(new TypeError("lookup offline"));
    await flush();
    expect(h.last().write).toBe(banner);
    expect(h.last().read).toMatchObject({ status: "error", stale: true });
    h.controller.stop();
  });

  it("polls a known generation by key and falls back to exactly one five-second snapshot read on any failure", async () => {
    vi.useFakeTimers();
    const reservedReceipt = receipt("generation-reserved");
    const initial = snapshot();
    initial.generations = [projectionFrom(reservedReceipt)];
    const h = harness({
      snapshotPollMs: STAFFING_SNAPSHOT_POLL_MS,
      requestPollMs: STAFFING_REQUEST_POLL_MS,
    });
    await h.ready(initial);
    expect(h.last().activeGeneration?.state).toBe("RESERVED");

    await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS);
    expect(h.client.lookup).toHaveBeenCalledWith("suggestion-generate", reservedReceipt.request_id);
    h.lookups[0].reject(new ManagerApiError(404, {
      code: "staffing_request_not_found",
      detail: "projection lag",
    }));
    await flush();
    expect(h.last().activeGeneration?.request_id).toBe(reservedReceipt.request_id);
    expect(h.last().read).toMatchObject({ status: "error", stale: true });
    expect(h.last().write).toBeNull();
    expect(h.client.submit).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(STAFFING_SNAPSHOT_POLL_MS - 1);
    expect(h.client.current).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.client.current).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(STAFFING_SNAPSHOT_POLL_MS * 2);
    expect(h.client.current).toHaveBeenCalledTimes(2);

    const refreshed = snapshot();
    refreshed.generations = [projectionFrom(receipt("generation-in-progress"))];
    refreshed.generations[0].request_id = reservedReceipt.request_id;
    h.reads[1].resolve(refreshed);
    await flush();
    await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS);
    expect(h.client.lookup).toHaveBeenCalledTimes(2);
    h.controller.stop();
  });

  it("lets a manager write supersede an in-flight generation lookup without late mutation or reschedule", async () => {
    vi.useFakeTimers();
    const reserved = receipt("generation-reserved");
    const initial = snapshot();
    initial.generations = [projectionFrom(reserved)];
    const h = harness({
      snapshotPollMs: STAFFING_SNAPSHOT_POLL_MS,
      requestPollMs: STAFFING_REQUEST_POLL_MS,
    });
    await h.ready(initial);
    await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS);
    expect(h.client.lookup).toHaveBeenCalledTimes(1);

    const write = h.controller.submit(mutation("exception-recorded"));
    await flush();
    const terminal = receipt("suggestion-issued");
    terminal.request_id = reserved.request_id;
    if (terminal.operation_kind !== "suggestion-generate") throw new Error("fixture");
    terminal.record.suggestion_id = projectionFrom(reserved).suggestion_id;
    h.lookups[0].resolve(terminal);
    await flush();
    expect(h.client.current).toHaveBeenCalledTimes(1);
    expect(h.last().activeGeneration?.state).toBe("RESERVED");

    h.submits[0].resolve(receipt("exception-recorded"));
    await flush();
    expect(h.client.current).toHaveBeenCalledTimes(2);
    const refreshed = snapshot();
    refreshed.generations = [projectionFrom(reserved)];
    h.reads[1].resolve(refreshed);
    await expect(write).resolves.toEqual(receipt("exception-recorded"));
    await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS - 1);
    expect(h.client.lookup).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.client.lookup).toHaveBeenCalledTimes(2);
    h.controller.stop();
  });

  it("keeps failed fallback reads serial and does not resume one-second polling for a terminal snapshot", async () => {
    vi.useFakeTimers();
    const reserved = receipt("generation-reserved");
    const reservedProjection = projectionFrom(reserved);
    const initial = snapshot();
    initial.generations = [reservedProjection];
    const h = harness({
      snapshotPollMs: STAFFING_SNAPSHOT_POLL_MS,
      requestPollMs: STAFFING_REQUEST_POLL_MS,
    });
    await h.ready(initial);

    await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS);
    h.lookups[0].reject(new TypeError("lookup failed"));
    await flush();
    await vi.advanceTimersByTimeAsync(STAFFING_SNAPSHOT_POLL_MS);
    expect(h.client.current).toHaveBeenCalledTimes(2);
    h.reads[1].reject(new TypeError("fallback failed"));
    await flush();

    await vi.advanceTimersByTimeAsync(STAFFING_SNAPSHOT_POLL_MS - 1);
    expect(h.client.current).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.client.current).toHaveBeenCalledTimes(3);
    const terminalReceipt = receipt("suggestion-issued");
    if (terminalReceipt.operation_kind !== "suggestion-generate") throw new Error("fixture");
    terminalReceipt.request_id = reserved.request_id;
    terminalReceipt.record.suggestion_id = reservedProjection.suggestion_id;
    const terminal = snapshot();
    terminal.generations = [projectionFrom(terminalReceipt)];
    h.reads[2].resolve(terminal);
    await flush();

    await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS);
    expect(h.client.lookup).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(STAFFING_SNAPSHOT_POLL_MS - STAFFING_REQUEST_POLL_MS);
    expect(h.client.current).toHaveBeenCalledTimes(4);
    h.controller.stop();
  });

  it("lets manual refresh supersede an in-flight generation lookup", async () => {
    vi.useFakeTimers();
    const reserved = receipt("generation-reserved");
    const reservedProjection = projectionFrom(reserved);
    const initial = snapshot();
    initial.generations = [reservedProjection];
    const h = harness({
      snapshotPollMs: STAFFING_SNAPSHOT_POLL_MS,
      requestPollMs: STAFFING_REQUEST_POLL_MS,
    });
    await h.ready(initial);
    await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS);

    const refresh = h.controller.refresh();
    await flush();
    expect(h.client.current).toHaveBeenCalledTimes(2);
    const lateTerminal = receipt("suggestion-issued");
    if (lateTerminal.operation_kind !== "suggestion-generate") throw new Error("fixture");
    lateTerminal.request_id = reserved.request_id;
    lateTerminal.record.suggestion_id = reservedProjection.suggestion_id;
    h.lookups[0].resolve(lateTerminal);
    await flush();
    expect(h.client.current).toHaveBeenCalledTimes(2);
    expect(h.last().activeGeneration?.state).toBe("RESERVED");

    const refreshed = snapshot();
    refreshed.generations = [reservedProjection];
    h.reads[1].resolve(refreshed);
    await refresh;
    await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS);
    expect(h.client.lookup).toHaveBeenCalledTimes(2);
    h.controller.stop();
  });

  it("lets a manager write supersede an in-flight snapshot read without accepting the late snapshot", async () => {
    const initial = snapshot();
    const h = harness();
    await h.ready(initial);
    const staleRead = h.controller.refresh();
    await flush();
    const write = h.controller.submit(mutation("exception-recorded"));
    await flush();

    const late = snapshot();
    late.server_time_utc = "2026-10-06T09:00:00.000000Z";
    h.reads[1].resolve(late);
    await expect(staleRead).rejects.toThrow(/stopped|superseded/i);
    expect(h.last().snapshot).toBe(initial);

    const committed = receipt("exception-recorded");
    h.submits[0].resolve(committed);
    await flush();
    const current = snapshot();
    current.server_time_utc = "2026-10-06T10:00:00.000000Z";
    h.reads[2].resolve(current);
    await expect(write).resolves.toEqual(committed);
    expect(h.last().snapshot).toBe(current);
    h.controller.stop();
  });

  it("preserves the used replay budget when stop interrupts the replay POST", async () => {
    const h = harness();
    await h.ready();
    const initial = h.controller.submit(mutation("generation-reserved"));
    await flush();
    h.submits[0].reject(new TypeError("initial result lost"));
    await expect(initial).rejects.toThrow("initial result lost");

    const firstRecovery = h.controller.recover();
    await flush();
    h.mutationLookups[0].reject(new ManagerApiError(404, {
      code: "staffing_request_not_found",
      detail: "absent",
    }));
    await flush();
    expect(h.client.submit).toHaveBeenCalledTimes(2);
    expect(h.last().write).toMatchObject({ status: "in_flight", replayedAfterNotFound: true });

    h.controller.stop();
    expect(h.last().write).toMatchObject({ status: "unknown", replayedAfterNotFound: true });
    h.submits[1].reject(new TypeError("replay result lost"));
    await expect(firstRecovery).rejects.toThrow("replay result lost");

    h.controller.start();
    h.reads[1].resolve(snapshot());
    await flush();
    const secondRecovery = h.controller.recover();
    await flush();
    h.mutationLookups[1].reject(new ManagerApiError(404, {
      code: "staffing_request_not_found",
      detail: "still absent",
    }));
    await expect(secondRecovery).rejects.toThrow("staffing_request_not_found");
    expect(h.client.submit).toHaveBeenCalledTimes(2);
    h.controller.stop();
  });

  it("does not issue a replay POST when a replay-state subscriber synchronously stops", async () => {
    const h = harness();
    await h.ready();
    const initial = h.controller.submit(mutation("generation-reserved"));
    await flush();
    h.submits[0].reject(new TypeError("initial result lost"));
    await expect(initial).rejects.toThrow("initial result lost");
    h.controller.subscribe((view) => {
      if (view.write?.status === "in_flight" && view.write.replayedAfterNotFound) {
        h.controller.stop();
      }
    });

    const recovery = h.controller.recover();
    await flush();
    h.mutationLookups[0].reject(new ManagerApiError(404, {
      code: "staffing_request_not_found",
      detail: "absent",
    }));
    await expect(recovery).rejects.toThrow(/stopped|superseded/i);
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    expect(h.last().write).toMatchObject({ status: "unknown", replayedAfterNotFound: true });
  });

  it("rejects a candidate snapshot with zero active-ID matches and never falls back to its final generation", async () => {
    const reservedReceipt = receipt("generation-reserved");
    const initial = snapshot();
    initial.generations = [projectionFrom(reservedReceipt)];
    const h = harness();
    await h.ready(initial);
    const previous = h.last().snapshot;

    const refresh = h.controller.refresh();
    await flush();
    const unrelated = snapshot();
    unrelated.generations = [projectionFrom(receipt("suggestion-issued"))];
    h.reads[1].resolve(unrelated);
    await expect(refresh).rejects.toThrow(/active generation/i);
    expect(h.last().snapshot).toBe(previous);
    expect(h.last().activeGeneration?.request_id).toBe(reservedReceipt.request_id);
    expect(h.last().read).toMatchObject({ status: "error", stale: true });
    h.controller.stop();
  });

  it("selects only the final oldest-to-newest generation on reload and preserves its manager response", async () => {
    const loaded = snapshot("date-after-manager-response");
    const finalGeneration = loaded.generations[0];
    const earlier = projectionFrom(receipt("generation-reserved"));
    loaded.generations = [earlier, finalGeneration];
    const h = harness();
    await h.ready(loaded);

    expect(h.last().activeGenerationRequestId).toBe(finalGeneration.request_id);
    expect(h.last().activeGeneration?.manager_response).toEqual(finalGeneration.manager_response);
    expect(h.last().activeGeneration?.manager_response).not.toBeNull();
    h.controller.stop();
  });

  it("rejects a candidate snapshot with multiple active-ID matches", async () => {
    const reservedReceipt = receipt("generation-reserved");
    const reservedProjection = projectionFrom(reservedReceipt);
    const initial = snapshot();
    initial.generations = [reservedProjection];
    const h = harness();
    await h.ready(initial);
    const previous = h.last().snapshot;

    const refresh = h.controller.refresh();
    await flush();
    const duplicate = snapshot();
    duplicate.generations = [
      reservedProjection,
      { ...projectionFrom(receipt("suggestion-issued")), request_id: reservedReceipt.request_id },
    ];
    h.reads[1].resolve(duplicate);
    await expect(refresh).rejects.toThrow(/active generation/i);
    expect(h.last().snapshot).toBe(previous);
    expect(h.last().activeGeneration?.suggestion_id).toBe(reservedProjection.suggestion_id);
    expect(h.last().read).toMatchObject({ status: "error", stale: true });
    h.controller.stop();
  });

  it("converts a stopped in-flight write to recoverable unknown and ignores its late completion after restart", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit(mutation("generation-reserved"));
    await flush();
    h.controller.stop();
    expect(h.last().write).toMatchObject({ status: "unknown", recovering: false });

    h.controller.start();
    h.submits[0].resolve(receipt("generation-reserved"));
    await expect(pending).rejects.toThrow(/stopped|superseded/i);
    expect(h.last().write?.status).toBe("unknown");
    expect(h.last().activeGeneration).toBeNull();
    h.reads[1].resolve(snapshot());
    await flush();
    h.controller.stop();
  });

  it("never issues the replay POST when recovery lookup finishes after stop", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit(mutation("generation-reserved"));
    await flush();
    h.submits[0].reject(new TypeError("lost"));
    await expect(pending).rejects.toThrow("lost");

    const recovery = h.controller.recover();
    await flush();
    h.controller.stop();
    h.controller.start();
    h.reads[1].resolve(snapshot());
    await flush();
    h.mutationLookups[0].reject(new ManagerApiError(404, {
      code: "staffing_request_not_found",
      detail: "absent",
    }));
    await expect(recovery).rejects.toThrow("staffing_request_not_found");
    expect(h.client.submit).toHaveBeenCalledTimes(1);
    expect(h.last().write).toMatchObject({ status: "unknown", recovering: false });
    h.controller.stop();
  });

  it("marks an interrupted required refresh saved-but-stale", async () => {
    const h = harness();
    await h.ready();
    const pending = h.controller.submit(mutation("exception-recorded"));
    await flush();
    h.submits[0].resolve(receipt("exception-recorded"));
    await flush();
    expect(h.client.current).toHaveBeenCalledTimes(2);
    h.controller.stop();
    expect(h.last().write).toMatchObject({ status: "committed", savedButStale: true });
    h.reads[1].resolve(snapshot());
    await expect(pending).resolves.toEqual(receipt("exception-recorded"));
    expect(h.last().write).toMatchObject({ status: "committed", savedButStale: true });
  });

  it("retries a durable RESULT_UNKNOWN generation with a fresh ID and retry_of after reload", async () => {
    const interrupted = receipt("generation-interrupted");
    if (interrupted.operation_kind !== "suggestion-generate") throw new Error("fixture");
    const initial = snapshot();
    initial.generations = [projectionFrom(interrupted)];
    const idFactory = vi.fn((kind: OperationKind) => `fresh-${kind}-request`);
    const h = harness({ getManagerLabel: () => "经理乙", requestIdFactory: idFactory });
    await h.ready(initial);
    expect(h.last().activeGeneration?.state).toBe("RESULT_UNKNOWN");

    const retry = h.controller.retryUnknownGeneration();
    await flush();
    expect(h.client.submit).toHaveBeenCalledWith({
      operationKind: "suggestion-generate",
      body: {
        schema: "nxt-staffing-suggestion-generate/v1",
        request_id: "fresh-suggestion-generate-request",
        operator: "经理乙",
        service_date: initial.service_date,
        expected_revisions: initial.revisions,
        retry_of: interrupted.record.suggestion_id,
      },
    });
    const reserved = receipt("generation-reserved");
    if (reserved.operation_kind !== "suggestion-generate") throw new Error("fixture");
    reserved.request_id = "fresh-suggestion-generate-request";
    reserved.record.retry_of = interrupted.record.suggestion_id;
    h.submits[0].resolve(reserved);
    await expect(retry).resolves.toEqual(reserved);
    expect(h.last().activeGenerationRequestId).toBe("fresh-suggestion-generate-request");
    h.controller.stop();
  });
});

describe("staffing closed actions", () => {
  function actionHarness(patch: Partial<StaffingView> = {}) {
    let current: StaffingView = {
      snapshot: snapshot(),
      read: { status: "ready", stale: false, detail: null },
      write: null,
      activeGenerationRequestId: null,
      activeGeneration: null,
      ...patch,
    };
    const submit = vi.fn<(mutation: StaffingMutation) => Promise<StaffingReceipt>>(
      async () => receipt("exception-recorded"),
    );
    const controller = {
      view: () => current,
      submit,
      refresh: vi.fn(async () => undefined),
      recover: vi.fn(async () => receipt("exception-recorded")),
      retryBusy: vi.fn(async () => receipt("generation-reserved")),
      retryUnknownGeneration: vi.fn(async () => receipt("generation-reserved")),
      acknowledgeWrite: vi.fn(),
      start: vi.fn(),
      stop: vi.fn(),
      subscribe: vi.fn(() => () => undefined),
    } as unknown as StaffingController;
    const getManagerLabel = vi.fn(() => "经理甲");
    const requestIdFactory = vi.fn((kind: OperationKind) => `request-${kind}`);
    return {
      current,
      replaceView: (next: StaffingView) => { current = next; },
      controller,
      submit,
      getManagerLabel,
      requestIdFactory,
      actions: createStaffingActions(controller, getManagerLabel, requestIdFactory),
    };
  }

  it("builds exception and manager-response bodies from current snapshot state", async () => {
    const h = actionHarness();
    await h.actions.recordException({
      staff_id: "staff-1",
      kind: "LATE",
      time_local: "09:15",
      note: null,
    });
    expect(h.submit.mock.calls[0][0]).toEqual({
      operationKind: "exception-record",
      body: {
        schema: "nxt-staffing-exception/v1",
        request_id: "request-exception-record",
        operator: "经理甲",
        service_date: h.current.snapshot?.service_date,
        expected_roster_revision: h.current.snapshot?.revisions.roster,
        expected_exception_set_revision: h.current.snapshot?.revisions.exception_set,
        staff_id: "staff-1",
        kind: "LATE",
        time_local: "09:15",
        note: null,
      },
    });

    await h.actions.acceptSuggestion("generation-1", 1, "采用一号方案");
    expect(h.submit.mock.calls[1][0]).toEqual({
      operationKind: "manager-response",
      targetId: "generation-1",
      body: {
        schema: "nxt-staffing-manager-response/v1",
        request_id: "request-manager-response",
        operator: "经理甲",
        expected_revisions: h.current.snapshot!.revisions,
        kind: "ACCEPT",
        candidate_index: 1,
        edited_operations: null,
        reason_code: "APPROVED",
        note: "采用一号方案",
      },
    });

    const edited = [{ operation: "REMOVE" as const, assignment_id: "assignment-1" }];
    await h.actions.modifySuggestion("generation-1", 1, edited, "缩短晚班");
    expect(h.submit.mock.calls[2][0]).toEqual({
      operationKind: "manager-response",
      targetId: "generation-1",
      body: {
        schema: "nxt-staffing-manager-response/v1",
        request_id: "request-manager-response",
        operator: "经理甲",
        expected_revisions: h.current.snapshot!.revisions,
        kind: "MODIFY",
        candidate_index: 1,
        edited_operations: edited,
        reason_code: "APPROVED_WITH_CHANGES",
        note: "缩短晚班",
      },
    });

    await h.actions.rejectSuggestion("generation-1", "MANUAL_HANDLING", null);
    expect(h.submit.mock.calls[3][0]).toEqual({
      operationKind: "manager-response",
      targetId: "generation-1",
      body: {
        schema: "nxt-staffing-manager-response/v1",
        request_id: "request-manager-response",
        operator: "经理甲",
        expected_revisions: h.current.snapshot!.revisions,
        kind: "REJECT",
        candidate_index: null,
        edited_operations: null,
        reason_code: "MANUAL_HANDLING",
        note: null,
      },
    });
  });

  it.each([
    { kind: "LATE", time_local: null, note: null },
    { kind: "EARLY_DEPARTURE", time_local: null, note: null },
    { kind: "LEAVE", time_local: "09:15", note: null },
    { kind: "UNAVAILABLE", time_local: "09:15", note: null },
  ])("rejects an incoherent exception branch before generating an ID: %#", async (invalid) => {
    const h = actionHarness();
    await expect(h.actions.recordException({
      staff_id: "staff-1",
      ...invalid,
    } as unknown as ExceptionDraft)).rejects.toThrow(/time/i);
    expect(h.requestIdFactory).not.toHaveBeenCalled();
    expect(h.submit).not.toHaveBeenCalled();
  });

  it("builds cancel, correction, and generation bodies without making business decisions", async () => {
    const h = actionHarness();
    await h.actions.cancelException("exception-1", "已复工");
    expect(h.submit.mock.calls[0][0]).toEqual({
      operationKind: "exception-cancel",
      targetId: "exception-1",
      body: {
        schema: "nxt-staffing-exception-cancel/v1",
        request_id: "request-exception-cancel",
        operator: "经理甲",
        expected_exception_set_revision: h.current.snapshot!.revisions.exception_set,
        note: "已复工",
      },
    });

    const replacement = { kind: "EARLY_DEPARTURE" as const, time_local: "16:30", note: null };
    await h.actions.correctException("exception-1", replacement);
    expect(h.submit.mock.calls[1][0]).toEqual({
      operationKind: "exception-correct",
      targetId: "exception-1",
      body: {
        schema: "nxt-staffing-exception-correct/v1",
        request_id: "request-exception-correct",
        operator: "经理甲",
        expected_exception_set_revision: h.current.snapshot!.revisions.exception_set,
        replacement,
      },
    });

    await h.actions.generateSuggestion();
    expect(h.submit.mock.calls[2][0]).toEqual({
      operationKind: "suggestion-generate",
      body: {
        schema: "nxt-staffing-suggestion-generate/v1",
        request_id: "request-suggestion-generate",
        operator: "经理甲",
        service_date: h.current.snapshot!.service_date,
        expected_revisions: h.current.snapshot!.revisions,
        retry_of: null,
      },
    });
  });

  it("sources roster identity from the snapshot and reads volatile manager/revisions at call time", async () => {
    const h = actionHarness();
    const base = mutation("roster-committed");
    if (base.operationKind !== "roster-import") throw new Error("fixture");
    const draftRecord = structuredClone(base.body) as unknown as Record<string, unknown>;
    for (const protectedField of [
      "request_id",
      "operator",
      "expected_roster_revision",
      "site_id",
      "deployment_id",
    ]) delete draftRecord[protectedField];
    const draft = draftRecord as unknown as RosterImportDraft;

    const nextSnapshot = structuredClone(h.current.snapshot!);
    nextSnapshot.service_date = "2026-10-07";
    nextSnapshot.revisions = { roster: 7, exception_set: 8, effective_plan: 9 };
    nextSnapshot.context = {
      site_id: "replacement-site",
      deployment_id: "replacement-deployment",
      site_timezone: "Asia/Shanghai",
    };
    h.replaceView({ ...h.current, snapshot: nextSnapshot });
    h.getManagerLabel.mockReturnValue("当班经理");
    await h.actions.importRoster(draft);
    const submitted = h.submit.mock.calls[0][0] as Extract<StaffingMutation, { operationKind: "roster-import" }>;
    expect(submitted).toEqual({
      operationKind: "roster-import",
      body: {
        ...draft,
        request_id: "request-roster-import",
        operator: "当班经理",
        expected_roster_revision: 7,
        site_id: "replacement-site",
        deployment_id: "replacement-deployment",
      },
    });
  });

  it.each([
    [{ snapshot: null }, /snapshot/i],
    [{ read: { status: "error", stale: true, detail: "offline" } }, /stale|refresh/i],
  ] as const)("refuses missing or stale state before generating an ID: %#", async (patch, message) => {
    const h = actionHarness(patch as Partial<StaffingView>);
    await expect(h.actions.generateSuggestion()).rejects.toThrow(message);
    expect(h.requestIdFactory).not.toHaveBeenCalled();
    expect(h.submit).not.toHaveBeenCalled();
  });

  it("refuses a blank manager label before generating an ID", async () => {
    const h = actionHarness();
    h.getManagerLabel.mockReturnValue("   ");
    await expect(h.actions.generateSuggestion()).rejects.toThrow(/manager/i);
    expect(h.requestIdFactory).not.toHaveBeenCalled();
    expect(h.submit).not.toHaveBeenCalled();
  });

  it("forwards lifecycle commands without synthesizing a mutation", async () => {
    const h = actionHarness();

    await expect(h.actions.refresh()).resolves.toBeUndefined();
    await expect(h.actions.recover()).resolves.toEqual(receipt("exception-recorded"));
    await expect(h.actions.retryBusy()).resolves.toEqual(receipt("generation-reserved"));
    await expect(h.actions.retryUnknownGeneration()).resolves.toEqual(
      receipt("generation-reserved"),
    );
    h.actions.acknowledgeWrite();

    expect(h.controller.refresh).toHaveBeenCalledOnce();
    expect(h.controller.recover).toHaveBeenCalledOnce();
    expect(h.controller.retryBusy).toHaveBeenCalledOnce();
    expect(h.controller.retryUnknownGeneration).toHaveBeenCalledOnce();
    expect(h.controller.acknowledgeWrite).toHaveBeenCalledOnce();
    expect(h.requestIdFactory).not.toHaveBeenCalled();
    expect(h.submit).not.toHaveBeenCalled();
  });
});
