import { afterEach, describe, expect, it, vi } from "vitest";
import { ManagerApiError } from "../lib/api";
import { canMutateTaskOps, createTaskOpsPoller, type TaskOpsView } from "../lib/task-ops";
import { taskOpsFixture } from "./task-ops-fixtures";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (value: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
afterEach(() => { vi.useRealTimers(); });

describe("independent task polling", () => {
  it("polls every two seconds after settling and never overlaps reads", async () => {
    vi.useFakeTimers();
    const waiting = deferred<ReturnType<typeof taskOpsFixture>>();
    const read = vi.fn(() => waiting.promise);
    const controller = createTaskOpsPoller(read, vi.fn());
    controller.start();
    void controller.refresh();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(read).toHaveBeenCalledTimes(1);
    waiting.resolve(taskOpsFixture());
    await flush();
    await vi.advanceTimersByTimeAsync(1_999);
    expect(read).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(read).toHaveBeenCalledTimes(2);
    controller.stop();
  });
  it("ignores a pre-action read and keeps changes disabled until the post-action refresh", async () => {
    vi.useFakeTimers();
    const obsolete = deferred<ReturnType<typeof taskOpsFixture>>();
    const fresh = deferred<ReturnType<typeof taskOpsFixture>>();
    const read = vi.fn().mockResolvedValueOnce(taskOpsFixture()).mockImplementationOnce(() => obsolete.promise).mockImplementationOnce(() => fresh.promise);
    const published: TaskOpsView[] = [];
    const controller = createTaskOpsPoller(read, (view) => published.push(view));
    controller.start(); await flush();
    void controller.refresh();
    const mutate = controller.mutate(async () => {});
    obsolete.resolve(taskOpsFixture({ server_time_utc: "2026-09-16T12:01:00Z" }));
    await flush();
    expect(published.some((view) => view.data?.server_time_utc === "2026-09-16T12:01:00Z")).toBe(false);
    expect(published.at(-1)?.busy).toBe(true);
    await expect(controller.mutate(async () => {})).rejects.toThrow("healthy");
    fresh.resolve(taskOpsFixture({ server_time_utc: "2026-09-16T12:02:00Z" }));
    await mutate;
    expect(published.at(-1)?.data?.server_time_utc).toBe("2026-09-16T12:02:00Z");
    expect(published.at(-1)?.busy).toBe(false);
    controller.stop();
  });
  it("keeps the last good view visibly stale, blocks mutations, and clears errors on recovery", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockResolvedValueOnce(taskOpsFixture()).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(taskOpsFixture());
    const publish = vi.fn();
    const controller = createTaskOpsPoller(read, publish);
    controller.start(); await flush();
    await controller.refresh();
    let view = publish.mock.lastCall![0] as TaskOpsView;
    expect(view.data).not.toBeNull(); expect(view.error).toContain("offline");
    expect(canMutateTaskOps(view)).toBe(false);
    const action = vi.fn();
    await expect(controller.mutate(action)).rejects.toThrow(); expect(action).not.toHaveBeenCalled();
    await controller.refresh();
    view = publish.mock.lastCall![0] as TaskOpsView;
    expect(view.error).toBeNull(); expect(canMutateTaskOps(view)).toBe(true);
    controller.stop();
  });
  it("does not convert a saved mutation into a retry when only its refresh fails", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockResolvedValueOnce(taskOpsFixture()).mockRejectedValueOnce(new Error("offline"));
    const publish = vi.fn();
    const controller = createTaskOpsPoller(read, publish);
    controller.start(); await flush();
    await expect(controller.mutate(async () => ({ schedule_id: "saved" }))).resolves.toBeUndefined();
    expect(publish.mock.lastCall![0].error).toContain("offline");
    controller.stop();
  });
  it("surfaces legacy unavailability and scheduler failure without allowing changes", async () => {
    vi.useFakeTimers();
    const publish = vi.fn();
    const read = vi.fn().mockRejectedValueOnce(new ManagerApiError(404, { code: "unavailable", detail: "demo" })).mockResolvedValueOnce(taskOpsFixture({ scheduler: { state: "FAILED", detail: "journal unavailable" } }));
    const controller = createTaskOpsPoller(read, publish);
    controller.start(); await flush();
    expect(publish.mock.lastCall![0].unavailable).toBe(true);
    await controller.refresh();
    expect(publish.mock.lastCall![0].unavailable).toBe(false);
    expect(canMutateTaskOps(publish.mock.lastCall![0])).toBe(false);
    controller.stop();
  });
  it("stops publishing and polling after unmount", async () => {
    vi.useFakeTimers();
    const waiting = deferred<ReturnType<typeof taskOpsFixture>>();
    const read = vi.fn(() => waiting.promise);
    const publish = vi.fn();
    const controller = createTaskOpsPoller(read, publish);
    controller.start(); controller.stop();
    const before = publish.mock.calls.length;
    waiting.resolve(taskOpsFixture()); await flush();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(publish).toHaveBeenCalledTimes(before); expect(read).toHaveBeenCalledTimes(1);
  });
});
