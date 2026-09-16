import { describe, expect, it } from "vitest";

import { createActionRunner } from "../lib/actions";

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("createActionRunner", () => {
  it("keeps busy true through action AND refresh, in order", async () => {
    const events: string[] = [];
    const action = deferred();
    const refresh = deferred();
    const run = createActionRunner(
      (busy) => events.push(`busy:${busy}`),
      () => {
        events.push("refresh:start");
        return refresh.promise;
      },
    );

    const running = run(() => {
      events.push("action:start");
      return action.promise;
    });
    await Promise.resolve();
    expect(events).toEqual(["busy:true", "action:start"]);

    action.resolve();
    await Promise.resolve();
    await Promise.resolve();
    // the action settled but the refresh has not: busy must still be true
    expect(events).toContain("refresh:start");
    expect(events).not.toContain("busy:false");

    refresh.resolve();
    await running;
    expect(events[events.length - 1]).toBe("busy:false");
  });

  it("ignores repeated invocations while an operation is in flight", async () => {
    let actionCalls = 0;
    const action = deferred();
    const refresh = deferred();
    const run = createActionRunner(
      () => {},
      () => refresh.promise,
    );

    const first = run(() => {
      actionCalls += 1;
      return action.promise;
    });
    // repeated clicks while the action is pending…
    await run(() => {
      actionCalls += 1;
      return Promise.resolve();
    });
    action.resolve();
    await Promise.resolve();
    await Promise.resolve();
    // …and while the refresh is still pending
    await run(() => {
      actionCalls += 1;
      return Promise.resolve();
    });
    refresh.resolve();
    await first;
    expect(actionCalls).toBe(1);

    // once settled, the runner accepts the next action
    const secondRefresh = deferred();
    const runner2 = createActionRunner(
      () => {},
      () => secondRefresh.promise,
    );
    secondRefresh.resolve();
    await runner2(() => {
      actionCalls += 1;
      return Promise.resolve();
    });
    expect(actionCalls).toBe(2);
  });

  it("still refreshes and clears busy when the action rejects", async () => {
    const events: string[] = [];
    const run = createActionRunner(
      (busy) => events.push(`busy:${busy}`),
      async () => {
        events.push("refresh");
      },
    );
    await expect(
      run(() => Promise.reject(new Error("advance_refused"))),
    ).rejects.toThrow("advance_refused");
    expect(events).toEqual(["busy:true", "refresh", "busy:false"]);
  });
});
