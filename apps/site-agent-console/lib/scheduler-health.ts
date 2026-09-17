/**
 * Shared scheduler health for the planning panel and the task panel.
 *
 * The only source is the task service's own `scheduler.state/detail` as
 * read and validated by the existing task-ops poller (`GET
 * /api/v0/task-ops`). This module derives a freshness-labelled health
 * value from that view; it never infers scheduler health from a planning
 * read, a mutation receipt, or a recovered request. A planning write is
 * allowed only over a fresh RUNNING reading; FAILED, a failed or missing
 * read, an unavailable route, or an expired reading all block writes with
 * a specific reason. Nothing here restarts or verifies the scheduler.
 */

import type { TaskOpsView } from "./task-ops";

/** A fresh reading older than this is treated as expired until renewed. */
export const SCHEDULER_HEALTH_EXPIRY_MS = 15_000;

export type SchedulerHealthStatus = "unknown" | "fresh" | "stale" | "expired" | "unavailable";

export interface SchedulerHealth {
  status: SchedulerHealthStatus;
  /** The last validated scheduler reading, kept through a stale period. */
  scheduler: { state: "RUNNING" | "FAILED"; detail: string | null } | null;
  /** Server time of the last successful task-ops read. */
  observedAtUtc: string | null;
  /** Local monotonic-ish instant of that read, for expiry. */
  observedAtMs: number | null;
  /** The latest read failure, if any. */
  error: string | null;
}

export const UNKNOWN_SCHEDULER_HEALTH: SchedulerHealth = {
  status: "unknown",
  scheduler: null,
  observedAtUtc: null,
  observedAtMs: null,
  error: null,
};

/** Re-evaluates expiry at `nowMs`; every other status is carried through. */
export function evaluateSchedulerHealth(
  health: SchedulerHealth,
  nowMs: number,
  expiryMs = SCHEDULER_HEALTH_EXPIRY_MS,
): SchedulerHealth {
  if (health.status === "fresh" && health.observedAtMs !== null && nowMs - health.observedAtMs > expiryMs) {
    return { ...health, status: "expired" };
  }
  return health;
}

/** Writes need a fresh RUNNING reading. Pass `nowMs` to re-check expiry;
 * omit it when the health was already evaluated at publish time. */
export function schedulerAllowsWrites(health: SchedulerHealth, nowMs?: number): boolean {
  const current = nowMs === undefined ? health : evaluateSchedulerHealth(health, nowMs);
  return current.status === "fresh" && current.scheduler?.state === "RUNNING";
}

export function schedulerHealthLabel(health: SchedulerHealth): string {
  if (health.status === "unknown") return "SCHEDULER STATE UNCONFIRMED";
  if (health.status === "unavailable") return "TASK SERVICE UNAVAILABLE";
  if (health.status === "stale") return "SCHEDULER STATE STALE";
  if (health.status === "expired") return "SCHEDULER CHECK EXPIRED";
  return health.scheduler?.state === "FAILED" ? "SCHEDULER FAILED" : "SCHEDULER RUNNING";
}

/** The manager-facing reason writes are withheld, or null when allowed. */
export function schedulerHealthReason(health: SchedulerHealth, nowMs?: number): string | null {
  const current = nowMs === undefined ? health : evaluateSchedulerHealth(health, nowMs);
  switch (current.status) {
    case "unknown":
      return (
        "The scheduler state has not yet been confirmed by the task service" +
        (current.error ? ` (last attempt: ${current.error})` : "") +
        "; planning writes wait for the first successful check."
      );
    case "unavailable":
      return "This service does not provide pilot task operations, so the scheduler state cannot be confirmed; planning writes are disabled.";
    case "stale":
      return (
        `The scheduler state could not be refreshed (${current.error ?? "read failed"}); the last known state was ` +
        `${current.scheduler?.state ?? "unknown"}. Planning writes are disabled until the task service answers again.`
      );
    case "expired":
      return `The last scheduler check is older than ${SCHEDULER_HEALTH_EXPIRY_MS / 1000} seconds; planning writes are disabled until a fresh check.`;
    case "fresh":
      if (current.scheduler?.state === "FAILED") {
        return (
          `The task service reports the scheduler as FAILED (${current.scheduler.detail ?? "no detail reported"}). Planning writes are disabled until the ` +
          "service reports RUNNING again; refreshing this page or recovering a request does not change that. If the runner stopped after an " +
          "unreadable write, restart it and verify its evidence."
        );
      }
      return null;
  }
}

export interface SchedulerHealthTracker {
  /** Derive health from a task-ops view as the poller publishes it. */
  observe(view: TaskOpsView): SchedulerHealth;
  /** The last derived health, with expiry re-evaluated now. */
  current(): SchedulerHealth;
}

export function createSchedulerHealthTracker(
  options: { now?: () => number; expiryMs?: number } = {},
): SchedulerHealthTracker {
  const now = options.now ?? (() => Date.now());
  const expiryMs = options.expiryMs ?? SCHEDULER_HEALTH_EXPIRY_MS;
  let health: SchedulerHealth = UNKNOWN_SCHEDULER_HEALTH;
  // The poller creates a new snapshot object only when a read completes; a
  // republish of the same object (busy toggles, notices) is not a new reading.
  let lastData: TaskOpsView["data"] = null;
  return {
    observe(view) {
      if (view.unavailable) {
        health = { status: "unavailable", scheduler: null, observedAtUtc: null, observedAtMs: null, error: view.error };
        lastData = null;
      } else if (view.data === null) {
        health = { ...UNKNOWN_SCHEDULER_HEALTH, error: view.error };
        lastData = null;
      } else if (view.busy) {
        // A task-panel mutation in progress is not a scheduler read: its
        // uncertain-outcome notice and busy toggles leave the health untouched.
      } else if (view.error !== null) {
        health = {
          status: "stale",
          scheduler: view.data.scheduler,
          observedAtUtc: health.observedAtUtc ?? view.data.server_time_utc,
          observedAtMs: health.observedAtMs,
          error: view.error,
        };
      } else if (view.data !== lastData || health.status !== "fresh") {
        lastData = view.data;
        health = {
          status: "fresh",
          scheduler: view.data.scheduler,
          observedAtUtc: view.data.server_time_utc,
          observedAtMs: now(),
          error: null,
        };
      }
      return evaluateSchedulerHealth(health, now(), expiryMs);
    },
    current() {
      return evaluateSchedulerHealth(health, now(), expiryMs);
    },
  };
}
