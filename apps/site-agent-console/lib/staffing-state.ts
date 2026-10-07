import { ManagerApiError } from "./api";
import {
  newStaffingRequestId,
  type GenerationProjection,
  type OperationKind,
  type StaffingClient,
  type StaffingDateSnapshot,
  type StaffingMutation,
  type StaffingReceipt,
  type SuggestionGenerateRequest,
} from "./staffing";
import { requireStaffingManagerLabel } from "./staffing-guards";

export const STAFFING_SNAPSHOT_POLL_MS = 5_000;
export const STAFFING_REQUEST_POLL_MS = 1_000;

export interface StaffingMutationAttempt {
  mutation: StaffingMutation;
  replayedAfterNotFound: boolean;
}

export type StaffingWriteState =
  | ({ status: "in_flight" } & StaffingMutationAttempt)
  | ({
      status: "unknown";
      detail: string;
      recovering: boolean;
    } & StaffingMutationAttempt)
  | ({ status: "busy"; detail: string } & StaffingMutationAttempt)
  | {
      status: "committed";
      requestId: string;
      receipt: StaffingReceipt;
      savedButStale: boolean;
    }
  | {
      status: "rejected";
      operationKind: OperationKind;
      requestId: string;
      code: string;
      detail: string;
    };

export interface StaffingView {
  snapshot: StaffingDateSnapshot | null;
  read: {
    status: "idle" | "loading" | "ready" | "error";
    stale: boolean;
    detail: string | null;
  };
  write: StaffingWriteState | null;
  activeGenerationRequestId: string | null;
  activeGeneration: GenerationProjection | null;
}

interface ExceptionDraftCommon {
  staff_id: string;
  note: string | null;
}

export type ExceptionDraft =
  | (ExceptionDraftCommon & { kind: "LEAVE" | "UNAVAILABLE"; time_local: null })
  | (ExceptionDraftCommon & { kind: "LATE" | "EARLY_DEPARTURE"; time_local: string });

export interface StaffingController {
  view(): StaffingView;
  subscribe(listener: (view: StaffingView) => void): () => void;
  start(): void;
  stop(): void;
  refresh(): Promise<void>;
  submit(mutation: StaffingMutation): Promise<StaffingReceipt>;
  recover(): Promise<StaffingReceipt>;
  retryBusy(): Promise<StaffingReceipt>;
  retryUnknownGeneration(): Promise<StaffingReceipt>;
  acknowledgeWrite(): void;
}

export interface StaffingControllerOptions {
  snapshotPollMs?: number;
  requestPollMs?: number;
  /** A stable function that reads the latest value (for React, normally a ref). */
  getManagerLabel?: () => string;
  requestIdFactory?: (kind: OperationKind) => string;
}

type Activity = "idle" | "snapshot-read" | "write" | "request-poll";
type Timer = ReturnType<typeof setTimeout>;

const ONGOING_STATES = new Set<GenerationProjection["state"]>(["RESERVED", "IN_PROGRESS"]);

const DEFINITE_REJECTIONS = new Set([
  "400:staffing_invalid_request",
  "404:staffing_not_found",
  "409:staffing_conflict",
  "409:staffing_exception_overlap",
  "409:staffing_stale_suggestion",
  "413:body_too_large",
]);

function describe(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function codeOf(cause: unknown): string {
  return cause instanceof ManagerApiError ? cause.code : "transport_error";
}

function invalidResponse(): ManagerApiError {
  return new ManagerApiError(200, {
    code: "invalid_staffing_response",
    detail: "staffing response is invalid",
  });
}

function stoppedOrSuperseded(): Error {
  return new Error("The staffing operation was stopped or superseded.");
}

function deepFreeze<T>(value: T, seen = new WeakSet<object>()): T {
  if (value === null || (typeof value !== "object" && typeof value !== "function")) return value;
  const object = value as object;
  if (seen.has(object)) return value;
  seen.add(object);
  for (const key of Reflect.ownKeys(object)) {
    deepFreeze((object as Record<PropertyKey, unknown>)[key], seen);
  }
  return Object.freeze(value);
}

function closedAttempt(mutation: StaffingMutation): StaffingMutationAttempt {
  try {
    return {
      mutation: deepFreeze(structuredClone(mutation)),
      replayedAfterNotFound: false,
    };
  } catch {
    throw new ManagerApiError(400, {
      code: "staffing_invalid_request",
      detail: "staffing request is invalid",
    });
  }
}

function classifyFailure(
  cause: unknown,
  operationKind: OperationKind,
): "busy" | "rejected" | "unknown" {
  if (!(cause instanceof ManagerApiError)) return "unknown";
  const pair = `${cause.status}:${cause.code}`;
  if (operationKind === "suggestion-generate" && pair === "429:staffing_busy") return "busy";
  return DEFINITE_REJECTIONS.has(pair) ? "rejected" : "unknown";
}

function operationIdentity(mutation: StaffingMutation): {
  operationKind: OperationKind;
  requestId: string;
} {
  return { operationKind: mutation.operationKind, requestId: mutation.body.request_id };
}

function assertReceiptIdentity(mutation: StaffingMutation, receipt: StaffingReceipt): void {
  if (
    receipt.operation_kind !== mutation.operationKind ||
    receipt.request_id !== mutation.body.request_id
  ) {
    throw invalidResponse();
  }
}

function generationFromReceipt(receipt: StaffingReceipt): GenerationProjection {
  if (receipt.operation_kind !== "suggestion-generate") throw invalidResponse();
  const record = receipt.record;
  const projection = {
    suggestion_id: record.suggestion_id,
    request_id: receipt.request_id,
    operation_id: receipt.operation_id,
    state: receipt.state,
    basis: record.basis,
    retry_of: record.retry_of,
    candidates: "candidates" in record ? record.candidates : [],
    coverage_gaps: "coverage_gaps" in record ? record.coverage_gaps : [],
    provenance: record.provenance,
    failure_code: "failure_code" in record ? record.failure_code : null,
    manager_response: null,
  };
  return structuredClone(projection) as GenerationProjection;
}

function exactRequestNotFound(cause: unknown): boolean {
  return cause instanceof ManagerApiError &&
    cause.status === 404 &&
    cause.code === "staffing_request_not_found";
}

export function initialStaffingView(): StaffingView {
  return {
    snapshot: null,
    read: { status: "idle", stale: false, detail: null },
    write: null,
    activeGenerationRequestId: null,
    activeGeneration: null,
  };
}

export function createStaffingController(
  client: StaffingClient,
  options: StaffingControllerOptions = {},
): StaffingController {
  const snapshotPollMs = options.snapshotPollMs ?? STAFFING_SNAPSHOT_POLL_MS;
  const requestPollMs = options.requestPollMs ?? STAFFING_REQUEST_POLL_MS;
  const getManagerLabel = options.getManagerLabel ?? (() => "");
  const requestIdFactory = options.requestIdFactory ?? newStaffingRequestId;

  let active = false;
  let controllerEpoch = 0;
  let activity: Activity = "idle";
  let timer: Timer | undefined;
  let requiredRefreshReceipt: StaffingReceipt | null = null;
  let snapshotValue: StaffingDateSnapshot | null = null;
  let readState: StaffingView["read"] = { status: "idle", stale: false, detail: null };
  let writeState: StaffingWriteState | null = null;
  let activeGenerationRequestId: string | null = null;
  let activeGeneration: GenerationProjection | null = null;
  let currentView = initialStaffingView();
  const listeners = new Set<(view: StaffingView) => void>();

  const rebuild = () => {
    currentView = {
      snapshot: snapshotValue,
      read: readState,
      write: writeState,
      activeGenerationRequestId,
      activeGeneration,
    };
  };

  const publish = () => {
    rebuild();
    if (!active) return;
    const published = currentView;
    for (const listener of listeners) {
      if (!active || currentView !== published) break;
      listener(published);
    }
  };

  const clearTimer = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };

  const current = (epoch: number): boolean => active && controllerEpoch === epoch;

  const begin = (next: Exclude<Activity, "idle">): number => {
    clearTimer();
    controllerEpoch += 1;
    activity = next;
    requiredRefreshReceipt = null;
    return controllerEpoch;
  };

  const setIdle = () => {
    activity = "idle";
    requiredRefreshReceipt = null;
  };

  const isOngoing = (): boolean =>
    activeGeneration !== null && ONGOING_STATES.has(activeGeneration.state);

  const schedule = (milliseconds: number, task: () => Promise<void>) => {
    clearTimer();
    if (!active || activity !== "idle" || milliseconds <= 0) return;
    const scheduledEpoch = controllerEpoch;
    timer = setTimeout(() => {
      timer = undefined;
      if (!current(scheduledEpoch) || activity !== "idle") return;
      void task().catch(() => undefined);
    }, milliseconds);
  };

  function scheduleNext(): void {
    if (!active || activity !== "idle") return;
    if (isOngoing() && !readState.stale) {
      schedule(requestPollMs, pollKnownGeneration);
    } else {
      schedule(snapshotPollMs, () => runSnapshotRead(false));
    }
  }

  const selectSnapshot = (
    candidate: StaffingDateSnapshot,
    requestIdAtStart: string | null,
  ): GenerationProjection | null => {
    if (requestIdAtStart === null) {
      return candidate.generations.length === 0
        ? null
        : candidate.generations[candidate.generations.length - 1];
    }
    const matches = candidate.generations.filter(
      (generation) => generation.request_id === requestIdAtStart,
    );
    if (matches.length !== 1) {
      throw new Error("The active generation is missing or ambiguous in the staffing snapshot.");
    }
    return matches[0];
  };

  const commitSnapshot = (
    candidate: StaffingDateSnapshot,
    requestIdAtStart: string | null,
  ) => {
    const selected = selectSnapshot(candidate, requestIdAtStart);
    snapshotValue = candidate;
    activeGeneration = selected;
    activeGenerationRequestId = selected?.request_id ?? null;
    readState = { status: "ready", stale: false, detail: null };
    if (writeState?.status === "committed" && writeState.savedButStale) {
      writeState = { ...writeState, savedButStale: false };
    }
  };

  async function runSnapshotRead(throwOnFailure: boolean): Promise<void> {
    if (!active) {
      if (throwOnFailure) throw new Error("The staffing view is not connected.");
      return;
    }
    if (activity === "write") {
      if (throwOnFailure) throw new Error("A staffing change is already in flight.");
      return;
    }
    const requestIdAtStart = activeGenerationRequestId;
    const epoch = begin("snapshot-read");
    readState = {
      status: "loading",
      stale: snapshotValue !== null && readState.stale,
      detail: null,
    };
    publish();
    if (!current(epoch)) {
      if (throwOnFailure) throw stoppedOrSuperseded();
      return;
    }
    try {
      const candidate = await client.current();
      if (!current(epoch)) {
        if (throwOnFailure) throw stoppedOrSuperseded();
        return;
      }
      commitSnapshot(candidate, requestIdAtStart);
      setIdle();
      publish();
      scheduleNext();
    } catch (cause) {
      if (!current(epoch)) {
        if (throwOnFailure) throw cause;
        return;
      }
      readState = {
        status: "error",
        stale: snapshotValue !== null,
        detail: describe(cause),
      };
      setIdle();
      publish();
      scheduleNext();
      if (throwOnFailure) throw cause;
    }
  }

  const markPostFailure = (
    cause: unknown,
    attempt: StaffingMutationAttempt,
  ) => {
    const { operationKind, requestId } = operationIdentity(attempt.mutation);
    const classification = classifyFailure(cause, operationKind);
    if (classification === "busy") {
      writeState = { status: "busy", ...attempt, detail: describe(cause) };
    } else if (classification === "rejected") {
      writeState = {
        status: "rejected",
        operationKind,
        requestId,
        code: codeOf(cause),
        detail: describe(cause),
      };
    } else {
      writeState = {
        status: "unknown",
        ...attempt,
        detail: describe(cause),
        recovering: false,
      };
    }
    setIdle();
    publish();
    scheduleNext();
  };

  const refreshAfterReceipt = async (
    receipt: StaffingReceipt,
    epoch: number,
  ): Promise<void> => {
    if (!current(epoch) || requiredRefreshReceipt !== receipt) return;
    const requestIdAtStart = activeGenerationRequestId;
    try {
      const candidate = await client.current();
      if (!current(epoch)) return;
      commitSnapshot(candidate, requestIdAtStart);
    } catch (cause) {
      if (!current(epoch)) return;
      readState = {
        status: "error",
        stale: snapshotValue !== null,
        detail: describe(cause),
      };
      if (writeState?.status === "committed" && writeState.receipt === receipt) {
        writeState = { ...writeState, savedButStale: true };
      }
    }
    if (!current(epoch)) return;
    setIdle();
    publish();
    scheduleNext();
  };

  const acceptReceipt = async (
    receipt: StaffingReceipt,
    epoch: number,
    attempt: StaffingMutationAttempt | null,
  ): Promise<StaffingReceipt> => {
    if (attempt !== null) assertReceiptIdentity(attempt.mutation, receipt);
    if (receipt.operation_kind === "suggestion-generate") {
      const projection = generationFromReceipt(receipt);
      activeGenerationRequestId = receipt.request_id;
      activeGeneration = projection;
    }

    if (attempt !== null) {
      writeState = {
        status: "committed",
        requestId: attempt.mutation.body.request_id,
        receipt,
        savedButStale: false,
      };
    } else if (
      writeState?.status === "committed" &&
      writeState.receipt.operation_kind === "suggestion-generate" &&
      writeState.requestId === receipt.request_id
    ) {
      writeState = { ...writeState, receipt, savedButStale: false };
    }

    if (receipt.operation_kind === "suggestion-generate" && ONGOING_STATES.has(receipt.state)) {
      setIdle();
      publish();
      scheduleNext();
      return receipt;
    }

    requiredRefreshReceipt = receipt;
    readState = {
      status: "loading",
      stale: snapshotValue !== null,
      detail: null,
    };
    publish();
    await refreshAfterReceipt(receipt, epoch);
    return receipt;
  };

  const executePost = async (
    attempt: StaffingMutationAttempt,
  ): Promise<StaffingReceipt> => {
    const epoch = begin("write");
    writeState = { status: "in_flight", ...attempt };
    publish();
    if (!current(epoch)) throw stoppedOrSuperseded();
    let receipt: StaffingReceipt;
    try {
      receipt = await client.submit(attempt.mutation);
      if (!current(epoch)) throw stoppedOrSuperseded();
      assertReceiptIdentity(attempt.mutation, receipt);
    } catch (cause) {
      if (current(epoch)) markPostFailure(cause, attempt);
      throw cause;
    }
    return acceptReceipt(receipt, epoch, attempt);
  };

  async function pollKnownGeneration(): Promise<void> {
    if (!active || activity !== "idle" || !isOngoing() || readState.stale) return;
    const selected = activeGeneration;
    if (selected === null) return;
    const epoch = begin("request-poll");
    try {
      const receipt = await client.lookup("suggestion-generate", selected.request_id);
      if (!current(epoch)) return;
      if (
        receipt.operation_kind !== "suggestion-generate" ||
        receipt.request_id !== selected.request_id ||
        receipt.record.suggestion_id !== selected.suggestion_id
      ) {
        throw invalidResponse();
      }
      await acceptReceipt(receipt, epoch, null);
    } catch (cause) {
      if (!current(epoch)) return;
      readState = {
        status: "error",
        stale: true,
        detail: describe(cause),
      };
      setIdle();
      publish();
      scheduleNext();
    }
  }

  const controller: StaffingController = {
    view: () => currentView,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    start() {
      if (active) return;
      active = true;
      controllerEpoch += 1;
      activity = "idle";
      void runSnapshotRead(false);
    },
    stop() {
      if (!active) return;
      const interruptedRequiredRefresh = requiredRefreshReceipt;
      active = false;
      clearTimer();
      controllerEpoch += 1;
      if (writeState?.status === "in_flight") {
        writeState = {
          status: "unknown",
          mutation: writeState.mutation,
          replayedAfterNotFound: writeState.replayedAfterNotFound,
          detail: "The staffing operation stopped before a reliable receipt was observed.",
          recovering: false,
        };
      } else if (writeState?.status === "unknown" && writeState.recovering) {
        writeState = { ...writeState, recovering: false };
      }
      if (interruptedRequiredRefresh !== null) {
        if (
          writeState?.status === "committed" &&
          writeState.receipt === interruptedRequiredRefresh
        ) {
          writeState = { ...writeState, savedButStale: true };
        }
        readState = {
          status: "error",
          stale: snapshotValue !== null,
          detail: "The staffing refresh was interrupted after the change was saved.",
        };
      }
      setIdle();
      rebuild();
    },
    refresh: () => runSnapshotRead(true),
    async submit(mutation) {
      if (!active) throw new Error("The staffing view is not connected.");
      if (activity === "write" || writeState?.status === "in_flight") {
        throw new Error("A staffing change is already in flight.");
      }
      if (writeState?.status === "unknown") {
        throw new Error("The last staffing change has an unknown outcome; recover it first.");
      }
      if (writeState?.status === "busy") {
        throw new Error("The retained staffing generation is busy; retry or dismiss it first.");
      }
      const attempt = closedAttempt(mutation);
      return executePost(attempt);
    },
    async recover() {
      if (!active) throw new Error("The staffing view is not connected.");
      if (activity === "write") throw new Error("A staffing change is already in flight.");
      const pending = writeState;
      if (pending?.status !== "unknown") {
        throw new Error("There is no staffing change with an unknown outcome to recover.");
      }
      const epoch = begin("write");
      writeState = { ...pending, recovering: true };
      publish();
      if (!current(epoch)) throw stoppedOrSuperseded();
      let found: StaffingReceipt;
      try {
        found = await client.lookupMutation(pending.mutation);
      } catch (cause) {
        if (!current(epoch)) throw cause;
        if (!exactRequestNotFound(cause) || pending.replayedAfterNotFound) {
          writeState = {
            ...pending,
            detail: `lookup failed: ${describe(cause)}`,
            recovering: false,
          };
          setIdle();
          publish();
          scheduleNext();
          throw cause;
        }

        const replayAttempt: StaffingMutationAttempt = {
          mutation: pending.mutation,
          replayedAfterNotFound: true,
        };
        writeState = { status: "in_flight", ...replayAttempt };
        publish();
        if (!current(epoch)) throw stoppedOrSuperseded();
        try {
          found = await client.submit(replayAttempt.mutation);
          if (!current(epoch)) throw stoppedOrSuperseded();
          assertReceiptIdentity(replayAttempt.mutation, found);
        } catch (replayCause) {
          if (current(epoch)) markPostFailure(replayCause, replayAttempt);
          throw replayCause;
        }
        return acceptReceipt(found, epoch, replayAttempt);
      }
      if (!current(epoch)) throw stoppedOrSuperseded();
      try {
        assertReceiptIdentity(pending.mutation, found);
      } catch (cause) {
        if (current(epoch)) {
          writeState = {
            ...pending,
            detail: `lookup failed: ${describe(cause)}`,
            recovering: false,
          };
          setIdle();
          publish();
          scheduleNext();
        }
        throw cause;
      }
      return acceptReceipt(found, epoch, pending);
    },
    async retryBusy() {
      if (!active) throw new Error("The staffing view is not connected.");
      if (activity === "write") throw new Error("A staffing change is already in flight.");
      const pending = writeState;
      if (pending?.status !== "busy") throw new Error("There is no busy staffing request to retry.");
      return executePost({
        mutation: pending.mutation,
        replayedAfterNotFound: pending.replayedAfterNotFound,
      });
    },
    async retryUnknownGeneration() {
      if (!active) throw new Error("The staffing view is not connected.");
      if (activity === "write" || writeState?.status === "in_flight") {
        throw new Error("A staffing change is already in flight.");
      }
      if (writeState?.status === "unknown" || writeState?.status === "busy") {
        throw new Error("Resolve the retained staffing write before retrying the generation.");
      }
      const selected = activeGeneration;
      const currentSnapshot = snapshotValue;
      if (selected?.state !== "RESULT_UNKNOWN") {
        throw new Error("The active staffing generation is not RESULT_UNKNOWN.");
      }
      if (currentSnapshot === null || readState.status !== "ready" || readState.stale) {
        throw new Error("Refresh the staffing snapshot before retrying the generation.");
      }
      if (currentSnapshot.roster === null) {
        throw new Error("Import a staffing roster before retrying the generation.");
      }
      if (currentSnapshot.generation_capability.status === "UNAVAILABLE") {
        throw new Error("Staffing suggestion generation is currently unavailable.");
      }
      const manager = requireStaffingManagerLabel(getManagerLabel());
      const body: SuggestionGenerateRequest = {
        schema: "nxt-staffing-suggestion-generate/v1",
        request_id: requestIdFactory("suggestion-generate"),
        operator: manager,
        service_date: currentSnapshot.service_date,
        expected_revisions: structuredClone(currentSnapshot.revisions),
        retry_of: selected.suggestion_id,
      };
      return controller.submit({ operationKind: "suggestion-generate", body });
    },
    acknowledgeWrite() {
      if (
        writeState !== null &&
        (writeState.status === "committed" ||
          writeState.status === "rejected" ||
          writeState.status === "busy")
      ) {
        writeState = null;
        publish();
      }
    },
  };

  rebuild();
  return controller;
}
