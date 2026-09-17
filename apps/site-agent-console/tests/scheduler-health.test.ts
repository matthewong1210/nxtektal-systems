import { describe, expect, it } from "vitest";

import {
  createSchedulerHealthTracker,
  SCHEDULER_HEALTH_EXPIRY_MS,
  schedulerAllowsWrites,
  schedulerHealthReason,
  type SchedulerHealth,
} from "../lib/scheduler-health";
import { INITIAL_TASK_OPS_VIEW, type TaskOpsView } from "../lib/task-ops";
import { taskOpsFixture } from "./task-ops-fixtures";

const view = (patch: Partial<TaskOpsView> = {}): TaskOpsView => ({ ...INITIAL_TASK_OPS_VIEW, data: taskOpsFixture(), loading: false, ...patch });

function clock(start = 1_000_000) {
  let now = start;
  return { now: () => now, advance: (ms: number) => { now += ms; } };
}

describe("scheduler health from the task-ops view", () => {
  it("is unknown until the task service has answered once, and blocks writes", () => {
    const c = clock();
    const tracker = createSchedulerHealthTracker({ now: c.now });
    const health = tracker.observe({ ...INITIAL_TASK_OPS_VIEW });
    expect(health).toMatchObject({ status: "unknown", scheduler: null, observedAtUtc: null });
    expect(schedulerAllowsWrites(health, c.now())).toBe(false);
    expect(schedulerHealthReason(health, c.now())).toMatch(/not yet been confirmed/i);
  });

  it("is fresh and allows writes while the latest validated read says RUNNING", () => {
    const c = clock();
    const tracker = createSchedulerHealthTracker({ now: c.now });
    const health = tracker.observe(view());
    expect(health).toMatchObject({ status: "fresh", scheduler: { state: "RUNNING", detail: null }, observedAtUtc: taskOpsFixture().server_time_utc, error: null });
    expect(schedulerAllowsWrites(health, c.now())).toBe(true);
    expect(schedulerHealthReason(health, c.now())).toBeNull();
  });

  it("blocks writes with the runner's own detail when the scheduler is FAILED", () => {
    const c = clock();
    const tracker = createSchedulerHealthTracker({ now: c.now });
    const health = tracker.observe(view({ data: taskOpsFixture({ scheduler: { state: "FAILED", detail: "planning_result_unknown: journal unreadable" } }) }));
    expect(health.status).toBe("fresh");
    expect(schedulerAllowsWrites(health, c.now())).toBe(false);
    expect(schedulerHealthReason(health, c.now())).toContain("journal unreadable");
    expect(schedulerHealthReason(health, c.now())).toMatch(/restart/i);
  });

  it("keeps the last scheduler state but marks it stale when a read fails", () => {
    const c = clock();
    const tracker = createSchedulerHealthTracker({ now: c.now });
    tracker.observe(view());
    const health = tracker.observe(view({ error: "Failed to fetch" }));
    expect(health).toMatchObject({ status: "stale", scheduler: { state: "RUNNING" }, error: "Failed to fetch" });
    expect(schedulerAllowsWrites(health, c.now())).toBe(false);
    expect(schedulerHealthReason(health, c.now())).toContain("Failed to fetch");
  });

  it("reports an older runner without task operations as unverifiable", () => {
    const c = clock();
    const tracker = createSchedulerHealthTracker({ now: c.now });
    const health = tracker.observe({ ...INITIAL_TASK_OPS_VIEW, loading: false, unavailable: true, error: "404" });
    expect(health.status).toBe("unavailable");
    expect(schedulerAllowsWrites(health, c.now())).toBe(false);
    expect(schedulerHealthReason(health, c.now())).toMatch(/cannot be confirmed/i);
  });

  it("expires a fresh reading that has not been renewed within the expiry window", () => {
    const c = clock();
    const tracker = createSchedulerHealthTracker({ now: c.now });
    const health = tracker.observe(view());
    c.advance(SCHEDULER_HEALTH_EXPIRY_MS - 1);
    expect(schedulerAllowsWrites(health, c.now())).toBe(true);
    c.advance(2);
    expect(schedulerAllowsWrites(health, c.now())).toBe(false);
    expect(schedulerHealthReason(health, c.now())).toMatch(/older than/i);
    expect(tracker.current().status).toBe("expired");
    // a renewed successful read restores freshness
    const renewed = tracker.observe(view({ data: taskOpsFixture({ server_time_utc: "2026-09-16T12:05:00Z" }) }));
    expect(renewed.status).toBe("fresh");
    expect(schedulerAllowsWrites(renewed, c.now())).toBe(true);
  });

  it("renews the freshness stamp only when a new snapshot arrives, never on a busy toggle republish", () => {
    const c = clock();
    const tracker = createSchedulerHealthTracker({ now: c.now });
    const data = taskOpsFixture();
    const first = tracker.observe(view({ data }));
    expect(first.observedAtMs).toBe(c.now());
    c.advance(10_000);
    // the task panel starts a mutation: the poller republishes the same snapshot with busy=true, then busy=false
    expect(tracker.observe(view({ data, busy: true })).observedAtMs).toBe(first.observedAtMs);
    expect(tracker.observe(view({ data, busy: false })).observedAtMs).toBe(first.observedAtMs);
    c.advance(SCHEDULER_HEALTH_EXPIRY_MS - 10_000 + 1);
    expect(tracker.current().status).toBe("expired");
    expect(schedulerAllowsWrites(tracker.observe(view({ data, busy: false })), c.now())).toBe(false);
    // a new snapshot object (a completed read) renews it
    const renewed = tracker.observe(view({ data: taskOpsFixture({ server_time_utc: "2026-09-16T12:00:30Z" }) }));
    expect(renewed.status).toBe("fresh");
    expect(renewed.observedAtMs).toBe(c.now());
  });

  it("does not mistake a task-panel uncertain-mutation notice for a failed scheduler read", () => {
    const c = clock();
    const tracker = createSchedulerHealthTracker({ now: c.now });
    const data = taskOpsFixture();
    const fresh = tracker.observe(view({ data }));
    const notice = tracker.observe(view({ data, busy: true, error: "The request outcome is uncertain. Refreshing the service view before further actions." }));
    expect(notice).toEqual(fresh); // a mutation in progress is not a read
    // the refresh after the mutation genuinely failed: the busy=false publish still carries the error
    const failed = tracker.observe(view({ data, busy: false, error: "Failed to fetch" }));
    expect(failed).toMatchObject({ status: "stale", error: "Failed to fetch", scheduler: { state: "RUNNING" } });
  });

  it("keeps a FAILED reading as the current health until the task service says otherwise", () => {
    const c = clock();
    const tracker = createSchedulerHealthTracker({ now: c.now });
    const failed = tracker.observe(view({ data: taskOpsFixture({ scheduler: { state: "FAILED", detail: "stopped" } }) }));
    const again: SchedulerHealth = tracker.current();
    expect(again).toEqual(failed);
    expect(schedulerAllowsWrites(again, c.now())).toBe(false);
    expect(schedulerHealthReason(again, c.now())).not.toMatch(/deliberate/i);
    expect(schedulerHealthReason(again, c.now())).toMatch(/reports the scheduler as FAILED/);
  });
});
