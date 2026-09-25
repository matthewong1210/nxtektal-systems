/**
 * Request-state controller for the manager planning panel.
 *
 * It composes the Phase 1 console controller (generation-tagged reads,
 * busy through change→refresh→commit, STALE on a failed current read,
 * UNCERTAIN on ambiguous outcomes, nothing published after unmount) with
 * the planning contract's write rules:
 *
 * - Every write carries one caller-generated `request_id`, generated once
 *   per attempt and kept verbatim for recovery of that same attempt.
 * - A transport failure or 503 leaves the write UNKNOWN. New writes are
 *   refused until it is recovered: GET by the original request ID, and on
 *   404 replay the identical request (same ID, same content) only if its
 *   current capability and scheduler-health gates allow. A fresh ID is never
 *   minted merely because a response was lost.
 * - A receipt whose `request_id` differs from the one sent (a duplicate
 *   answered with the original committed ID) keeps that original identity
 *   for later lookups; the sent ID never becomes an alias.
 * - A committed write whose refresh fails is reported as saved-but-stale,
 *   distinct from an unknown outcome.
 * - The view polls the service while idle so schedule and task links
 *   appear after confirmation; the poll never overlaps a read or a write.
 */

import {
  canMutateConsole,
  createConsoleController,
  initialConsoleView,
  type ConsoleController,
  type ConsoleView,
} from "./actions";
import { ManagerApiError } from "./api";
import type { MutationReceipt, PlanningClient, PlanningRequestBody, PlanningSnapshot, WriteKind } from "./planning";
import {
  evaluateSchedulerHealth,
  schedulerAllowsWrites,
  schedulerHealthReason,
  UNKNOWN_SCHEDULER_HEALTH,
  type SchedulerHealth,
} from "./scheduler-health";
import { isAmbiguousMutation, type TaskOpsWriteOperation } from "./task-ops";

export const PLANNING_POLL_MS = 5_000;

const TASK_OPS_OPERATION_FOR_WRITE: Record<WriteKind, TaskOpsWriteOperation> = {
  inputs: "planning_inputs_create",
  plans: "planning_plans_create",
  confirmations: "planning_confirmations_create",
  outcomes: "planning_outcomes_create",
};

/** Exhaustive bridge from the planning route vocabulary to the validated
 * service-capability operation checked immediately before a recovery replay. */
export const taskOpsOperationForPlanningWrite = (kind: WriteKind): TaskOpsWriteOperation =>
  TASK_OPS_OPERATION_FOR_WRITE[kind];

export type WriteState =
  | { status: "in_flight"; kind: WriteKind; requestId: string }
  | {
      status: "unknown";
      kind: WriteKind;
      requestId: string;
      body: PlanningRequestBody;
      detail: string;
      recovering: boolean;
    }
  | { status: "committed"; kind: WriteKind; requestId: string; receipt: MutationReceipt }
  | { status: "rejected"; kind: WriteKind; requestId: string; code: string; detail: string };

export interface PlanningView extends ConsoleView<PlanningSnapshot> {
  /** The most recent write attempt of this mounted panel, if any. */
  write: WriteState | null;
  /** The service answered 404 for the planning route: an older runner. */
  unavailable: boolean;
  /** Shared scheduler health from the task-ops poller, expiry evaluated at publish time. */
  health: SchedulerHealth;
}

export interface PlanningController {
  start(): void;
  stop(): void;
  refresh(): Promise<void>;
  submit(kind: WriteKind, body: PlanningRequestBody): Promise<MutationReceipt>;
  recover(): Promise<MutationReceipt>;
  acknowledgeWrite(): void;
  /** Re-publish the view after the shared scheduler health changed. */
  notifyHealth(): void;
  /** Replace the shared health source (a prop may change); republishes. */
  setHealthSource(source: () => SchedulerHealth): void;
  /** Replace the replay capability gate without recreating in-flight state. */
  setReplayBlocker(source: (kind: WriteKind) => string | null): void;
}

export function initialPlanningView(): PlanningView {
  return { ...initialConsoleView<PlanningSnapshot>(), write: null, unavailable: false, health: UNKNOWN_SCHEDULER_HEALTH };
}

/** Writes need a current, idle view, no write with an unknown outcome, and a
 * fresh RUNNING scheduler reading (evaluated when the view was published). */
export const canWritePlanning = (view: PlanningView): boolean =>
  canMutateConsole(view) &&
  !view.unavailable &&
  (view.write === null || view.write.status === "committed" || view.write.status === "rejected") &&
  schedulerAllowsWrites(view.health);

const describe = (cause: unknown): string =>
  cause instanceof Error ? cause.message : String(cause);
const codeOf = (cause: unknown): string =>
  cause instanceof ManagerApiError ? cause.code : "transport_error";

export function createPlanningController(
  client: PlanningClient,
  publish: (view: PlanningView) => void,
  options: {
    pollMs?: number;
    health?: () => SchedulerHealth;
    now?: () => number;
    /** Returns the latest static-capability reason that forbids replaying this
     * write, or null when the operation is installed. Absence fails closed. */
    replayBlocker?: (kind: WriteKind) => string | null;
  } = {},
): PlanningController {
  const pollMs = options.pollMs ?? PLANNING_POLL_MS;
  const now = options.now ?? (() => Date.now());
  let healthSource: () => SchedulerHealth = options.health ?? (() => UNKNOWN_SCHEDULER_HEALTH);
  const healthNow = (): SchedulerHealth => evaluateSchedulerHealth(healthSource(), now());
  let replayBlocker = options.replayBlocker ?? (() =>
    "The task service write capabilities are unknown, so the original request cannot be replayed.");
  let active = false;
  let base: ConsoleView<PlanningSnapshot> = initialConsoleView<PlanningSnapshot>();
  let write: WriteState | null = null;
  let unavailable = false;
  let lastReadFailure: unknown = null;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const view = (): PlanningView => ({ ...base, write, unavailable, health: healthNow() });
  // Expiry is owned by the shared health source (the task-ops hook): it calls
  // notifyHealth() at the expiry instant so both panels flip together. Every
  // publish and every submit still re-evaluates expiry against now().
  const emit = () => {
    if (active) publish(view());
  };

  const read = async (): Promise<PlanningSnapshot> => {
    try {
      return await client.snapshot();
    } catch (cause) {
      lastReadFailure = cause;
      throw cause;
    }
  };

  const schedulePoll = () => {
    clearTimeout(timer);
    if (active && pollMs > 0 && !base.busy && !base.loading) {
      timer = setTimeout(() => {
        void inner.refresh();
      }, pollMs);
    }
  };

  const inner: ConsoleController = createConsoleController<PlanningSnapshot>(read, (next) => {
    base = next;
    if (next.error === null) unavailable = false;
    else unavailable = lastReadFailure instanceof ManagerApiError && lastReadFailure.status === 404;
    emit();
    schedulePoll();
  });

  const finish = (kind: WriteKind, requestId: string, cause: unknown, body?: PlanningRequestBody): never => {
    if (isAmbiguousMutation(cause) && body !== undefined) {
      write = { status: "unknown", kind, requestId, body, detail: describe(cause), recovering: false };
    } else {
      write = { status: "rejected", kind, requestId, code: codeOf(cause), detail: describe(cause) };
    }
    throw cause;
  };

  return {
    start() {
      active = true;
      inner.start();
    },
    stop() {
      active = false;
      clearTimeout(timer);
      inner.stop();
    },
    async refresh() {
      if (!active) return;
      clearTimeout(timer);
      await inner.refresh();
    },
    async submit(kind, body) {
      if (!active) throw new Error("The planning view is not connected.");
      const current = view();
      if (current.busy || current.write?.status === "in_flight") {
        throw new Error("A planning change is already in flight; wait for it to finish.");
      }
      if (current.write?.status === "unknown") {
        throw new Error(
          "The last change has an unknown outcome. Recover it by its request ID before making another change.",
        );
      }
      if (!schedulerAllowsWrites(current.health)) {
        throw new Error(`Planning writes are disabled: ${schedulerHealthReason(current.health)}`);
      }
      if (!canWritePlanning(current)) {
        throw new Error("Planning changes are disabled until the planning view is refreshed successfully.");
      }
      const requestId = body.request_id;
      write = { status: "in_flight", kind, requestId };
      emit();
      clearTimeout(timer);
      let receipt: MutationReceipt | undefined;
      try {
        await inner.mutate(async () => {
          try {
            receipt = await client.submit(kind, body);
            write = { status: "committed", kind, requestId, receipt };
          } catch (cause) {
            finish(kind, requestId, cause, body);
          }
        });
      } finally {
        emit();
      }
      if (receipt === undefined) throw new Error("The planning change did not run.");
      return receipt;
    },
    async recover() {
      if (!active) throw new Error("The planning view is not connected.");
      const pending = write;
      if (pending === null || pending.status !== "unknown") {
        throw new Error("There is no planning change with an unknown outcome to recover.");
      }
      if (base.busy) throw new Error("A planning change is already in flight; wait for it to finish.");
      write = { ...pending, recovering: true };
      emit();
      clearTimeout(timer);
      let receipt: MutationReceipt | undefined;
      let blockedReplay: string | null = null;
      try {
        await inner.mutate(
          async () => {
            let found: MutationReceipt | undefined;
            try {
              found = await client.lookup(pending.requestId);
            } catch (cause) {
              if (!(cause instanceof ManagerApiError && cause.code === "planning_request_not_found")) {
                // Any other lookup failure (a runner without the route, a
                // malformed-ID 400, a 503) says nothing about the original
                // request: it stays unknown with its ID and content.
                write = { ...pending, recovering: false, detail: `lookup failed: ${describe(cause)}` };
                throw cause;
              }
            }
            if (found === undefined) {
              // Explicitly absent from the verified journal: replaying the
              // identical request is safe only while the original operation is
              // still installed and the latest shared scheduler reading allows
              // writes. Both gates are evaluated here, after the GET, so a
              // found record remains readable and changes while that GET was
              // waiting are honored before any POST.
              const capabilityReason = replayBlocker(pending.kind);
              const currentHealth = healthNow();
              const healthReason = schedulerAllowsWrites(currentHealth)
                ? null
                : schedulerHealthReason(currentHealth) ?? "The scheduler write health does not allow planning changes.";
              const reason = capabilityReason ?? healthReason;
              if (reason !== null) {
                blockedReplay = `Replay was not sent: ${reason}`;
                write = { ...pending, recovering: false, detail: blockedReplay };
                return;
              }
              // Only this same-ID, same-body replay may answer for the request:
              // a definite refusal rejects it, an ambiguous failure keeps it unknown.
              try {
                found = await client.submit(pending.kind, pending.body);
              } catch (cause) {
                finish(pending.kind, pending.requestId, cause, pending.body);
              }
            }
            receipt = found;
            write = { status: "committed", kind: pending.kind, requestId: pending.requestId, receipt: found as MutationReceipt };
          },
          { requireCurrent: false },
        );
      } finally {
        emit();
      }
      if (blockedReplay !== null) throw new Error(blockedReplay);
      if (receipt === undefined) throw new Error("The recovery did not run.");
      return receipt;
    },
    acknowledgeWrite() {
      if (write !== null && (write.status === "committed" || write.status === "rejected")) {
        write = null;
        emit();
      }
    },
    notifyHealth() {
      emit();
    },
    setHealthSource(source) {
      healthSource = source;
      emit();
    },
    setReplayBlocker(source) {
      replayBlocker = source;
    },
  };
}
