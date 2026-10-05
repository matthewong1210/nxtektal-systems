import { describe, expect, it, vi } from "vitest";

import { ManagerApiError } from "../lib/api";
import {
  canMutateConsole,
  createConsoleController,
  type ConsoleView,
} from "../lib/actions";

function deferred<T = void>() {
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

type Snapshot = { tag: string };

/** A controller over a scripted read: each call to `read` hands the test a
 * deferred it can settle in any order, which is how late responses are
 * reproduced. */
function harness() {
  const reads: ReturnType<typeof deferred<Snapshot>>[] = [];
  const published: ConsoleView<Snapshot>[] = [];
  const read = vi.fn(() => {
    const next = deferred<Snapshot>();
    reads.push(next);
    return next.promise;
  });
  const controller = createConsoleController<Snapshot>(read, (view) =>
    published.push(view),
  );
  const last = () => published[published.length - 1];
  return { reads, published, read, controller, last };
}

describe("createConsoleController", () => {
  it("starts loading, commits the first read, and enables changes", async () => {
    const h = harness();
    h.controller.start();
    expect(h.last()).toMatchObject({ data: null, loading: true, busy: false, error: null });
    expect(canMutateConsole(h.last())).toBe(false);
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    expect(h.last()).toMatchObject({ data: { tag: "initial" }, loading: false, error: null });
    expect(canMutateConsole(h.last())).toBe(true);
    h.controller.stop();
  });

  it("keeps busy true through the action AND its refresh, in order", async () => {
    const h = harness();
    h.controller.start();
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    const events: string[] = [];
    const action = deferred();
    const running = h.controller.mutate(() => {
      events.push("action:start");
      return action.promise;
    });
    await flush();
    expect(h.last().busy).toBe(true);
    expect(events).toEqual(["action:start"]);
    expect(h.read).toHaveBeenCalledTimes(1);
    action.resolve();
    await flush();
    // the action settled but the refresh has not: busy must still be true
    expect(h.read).toHaveBeenCalledTimes(2);
    expect(h.last().busy).toBe(true);
    h.reads[1].resolve({ tag: "after-action" });
    await running;
    expect(h.last()).toMatchObject({ busy: false, data: { tag: "after-action" }, error: null });
    h.controller.stop();
  });

  it("ignores repeated mutate calls while an operation is in flight", async () => {
    const h = harness();
    h.controller.start();
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    let actionCalls = 0;
    const action = deferred();
    const first = h.controller.mutate(() => {
      actionCalls += 1;
      return action.promise;
    });
    // repeated clicks while the action is pending…
    await h.controller.mutate(async () => {
      actionCalls += 1;
    });
    action.resolve();
    await flush();
    // …and while the refresh is still pending
    await h.controller.mutate(async () => {
      actionCalls += 1;
    });
    h.reads[1].resolve({ tag: "after-action" });
    await first;
    expect(actionCalls).toBe(1);
    // once settled, the controller accepts the next action
    const second = h.controller.mutate(async () => {
      actionCalls += 1;
    });
    await flush();
    h.reads[2].resolve({ tag: "after-second" });
    await second;
    expect(actionCalls).toBe(2);
    h.controller.stop();
  });

  it("still refreshes, clears busy, and rethrows when the action rejects", async () => {
    const h = harness();
    h.controller.start();
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    const running = h.controller.mutate(() =>
      Promise.reject(new ManagerApiError(409, { code: "advance_refused", detail: "exhausted" })),
    );
    await flush();
    expect(h.read).toHaveBeenCalledTimes(2);
    h.reads[1].resolve({ tag: "after-refusal" });
    await expect(running).rejects.toThrow("advance_refused");
    expect(h.last()).toMatchObject({ busy: false, data: { tag: "after-refusal" }, notice: null });
    h.controller.stop();
  });

  it("lets only the current generation commit: a late pre-action read cannot overwrite the post-action view", async () => {
    const h = harness();
    h.controller.start();
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    void h.controller.refresh(); // manual refresh A starts and stays slow
    await flush();
    expect(h.read).toHaveBeenCalledTimes(2);
    const running = h.controller.mutate(async () => {});
    await flush();
    expect(h.read).toHaveBeenCalledTimes(3); // post-action refresh B
    h.reads[2].resolve({ tag: "after-action" });
    await running;
    expect(h.last().data).toEqual({ tag: "after-action" });
    h.reads[1].resolve({ tag: "pre-action" }); // A arrives late
    await flush();
    expect(h.last().data).toEqual({ tag: "after-action" });
    expect(h.last()).toMatchObject({ loading: false, busy: false, error: null });
    h.controller.stop();
  });

  it("does not let a late failure of an obsolete read mark the current view stale", async () => {
    const h = harness();
    h.controller.start();
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    void h.controller.refresh();
    await flush();
    const running = h.controller.mutate(async () => {});
    await flush();
    h.reads[2].resolve({ tag: "after-action" });
    await running;
    h.reads[1].reject(new Error("obsolete read failed"));
    await flush();
    expect(h.last()).toMatchObject({ error: null, data: { tag: "after-action" } });
    expect(canMutateConsole(h.last())).toBe(true);
    h.controller.stop();
  });

  it("does not let a late success of an obsolete read restore an actionable view after the current read failed", async () => {
    const h = harness();
    h.controller.start();
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    void h.controller.refresh();
    await flush();
    const running = h.controller.mutate(async () => {});
    await flush();
    h.reads[2].reject(new Error("post-action refresh failed"));
    await running;
    expect(h.last().error).toContain("post-action refresh failed");
    h.reads[1].resolve({ tag: "pre-action" });
    await flush();
    expect(h.last().error).toContain("post-action refresh failed");
    expect(h.last().data).toEqual({ tag: "initial" });
    expect(canMutateConsole(h.last())).toBe(false);
    h.controller.stop();
  });

  it("keeps the last good view visibly stale, refuses changes, and recovers on the next successful read", async () => {
    const h = harness();
    h.controller.start();
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    const failing = h.controller.refresh();
    h.reads[1].reject(new Error("offline"));
    await failing;
    expect(h.last()).toMatchObject({ data: { tag: "initial" }, loading: false });
    expect(h.last().error).toContain("offline");
    expect(canMutateConsole(h.last())).toBe(false);
    const action = vi.fn(async () => {});
    await expect(h.controller.mutate(action)).rejects.toThrow(/refresh/);
    expect(action).not.toHaveBeenCalled();
    const recovering = h.controller.refresh();
    h.reads[2].resolve({ tag: "recovered" });
    await recovering;
    expect(h.last()).toMatchObject({ data: { tag: "recovered" }, error: null });
    expect(canMutateConsole(h.last())).toBe(true);
    h.controller.stop();
  });

  it("does not convert a saved change into a retry when only its refresh fails", async () => {
    const h = harness();
    h.controller.start();
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    const action = vi.fn(async () => ({ case_status: "accepted" }));
    const running = h.controller.mutate(action);
    await flush();
    h.reads[1].reject(new Error("offline after save"));
    await expect(running).resolves.toBeUndefined();
    expect(action).toHaveBeenCalledTimes(1);
    expect(h.last()).toMatchObject({ busy: false, data: { tag: "initial" } });
    expect(h.last().error).toContain("offline after save");
    expect(canMutateConsole(h.last())).toBe(false);
    h.controller.stop();
  });

  it("flags an uncertain outcome without claiming failure, then refreshes", async () => {
    const h = harness();
    h.controller.start();
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    const running = h.controller.mutate(() => Promise.reject(new TypeError("fetch failed")));
    await flush();
    expect(h.last().notice).toMatch(/uncertain/i);
    expect(h.last().busy).toBe(true);
    h.reads[1].resolve({ tag: "refreshed-truth" });
    await expect(running).rejects.toThrow("fetch failed");
    expect(h.last()).toMatchObject({ busy: false, data: { tag: "refreshed-truth" }, error: null });
    expect(h.last().notice).toMatch(/uncertain/i);
    // a 5xx envelope is equally uncertain; a 4xx refusal is definite
    const server = h.controller.mutate(() =>
      Promise.reject(new ManagerApiError(500, { code: "internal_error", detail: "boom" })),
    );
    await flush();
    expect(h.last().notice).toMatch(/uncertain/i);
    h.reads[2].resolve({ tag: "after-500" });
    await expect(server).rejects.toThrow("internal_error");
    const refused = h.controller.mutate(() =>
      Promise.reject(new ManagerApiError(400, { code: "invalid_request", detail: "bad" })),
    );
    await flush();
    expect(h.last().notice).toBeNull();
    h.reads[3].resolve({ tag: "after-400" });
    await expect(refused).rejects.toThrow("invalid_request");
    h.controller.stop();
  });

  it("coalesces overlapping reads and issues no extra read while busy", async () => {
    const h = harness();
    h.controller.start();
    void h.controller.refresh();
    void h.controller.refresh();
    expect(h.read).toHaveBeenCalledTimes(1);
    h.reads[0].resolve({ tag: "initial" });
    await flush();
    const action = deferred();
    const running = h.controller.mutate(() => action.promise);
    await flush();
    void h.controller.refresh();
    await flush();
    expect(h.read).toHaveBeenCalledTimes(1);
    action.resolve();
    await flush();
    expect(h.read).toHaveBeenCalledTimes(2);
    h.reads[1].resolve({ tag: "after-action" });
    await running;
    h.controller.stop();
  });

  it("stops publishing after unmount, drops late responses, and refuses later changes", async () => {
    const h = harness();
    h.controller.start();
    const before = h.published.length;
    h.controller.stop();
    h.reads[0].resolve({ tag: "late" });
    await flush();
    expect(h.published).toHaveLength(before);
    await expect(h.controller.mutate(async () => {})).rejects.toThrow(/not connected/);
    await h.controller.refresh();
    expect(h.read).toHaveBeenCalledTimes(1);
  });
});
