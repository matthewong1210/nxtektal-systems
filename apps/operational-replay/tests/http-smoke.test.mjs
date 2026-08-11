import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { describe, expect, test } from "vitest";

import {
  fetchWithTimeout,
  observeChild,
  readResponseTextWithTimeout,
  runHttpSmoke,
  stopChild,
} from "./http-smoke.mjs";

describe("HTTP smoke lifecycle", () => {
  test("fails promptly when the server exits before readiness", async () => {
    let server;
    const smoke = runHttpSmoke({
      spawnServer: () => {
        server = spawn(process.execPath, ["-e", "process.exit(17)"], {
          stdio: ["ignore", "pipe", "pipe"],
        });
        return server;
      },
      readyTimeoutMs: 5_000,
      fetchTimeoutMs: 500,
      retryDelayMs: 10,
      terminateTimeoutMs: 100,
      killTimeoutMs: 100,
      signalChild: (child, signal) => child.kill(signal),
      write: () => {},
    });

    const result = await Promise.race([
      smoke.then(
        () => ({ kind: "resolved" }),
        (error) => ({ kind: "rejected", error }),
      ),
      new Promise((resolveTimeout) =>
        setTimeout(() => resolveTimeout({ kind: "timed-out" }), 1_000),
      ),
    ]);

    expect(result.kind).toBe("rejected");
    expect(result.error).toHaveProperty(
      "message",
      expect.stringMatching(/exited before becoming ready \(code 17, signal none\)/),
    );
    expect(server.exitCode).toBe(17);
  });

  test("bounds fetch and response-body reads with timeout signals", async () => {
    let fetchSignal;
    let redirectMode;
    const stalledFetch = fetchWithTimeout(
      "http://127.0.0.1/",
      20,
      (_url, { redirect, signal }) => {
        redirectMode = redirect;
        fetchSignal = signal;
        return new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(signal.reason), {
            once: true,
          });
        });
      },
    );

    await expect(stalledFetch).rejects.toHaveProperty("name", "TimeoutError");
    expect(redirectMode).toBe("error");
    expect(fetchSignal).toBeInstanceOf(AbortSignal);
    expect(fetchSignal.aborted).toBe(true);

    let cancelReason;
    const response = new Response(
      new ReadableStream({
        cancel(reason) {
          cancelReason = reason;
        },
      }),
    );
    await expect(readResponseTextWithTimeout(response, 20)).rejects.toHaveProperty(
      "name",
      "TimeoutError",
    );
    expect(cancelReason).toHaveProperty("name", "TimeoutError");
  });

  test("escalates cleanup and waits for close after SIGKILL", async () => {
    const child = new EventEmitter();
    const signals = [];
    const lifecycle = observeChild(child);
    const signalChild = (_child, signal) => {
      signals.push(signal);
      if (signal === "SIGKILL") {
        queueMicrotask(() => {
          child.emit("exit", null, signal);
          child.emit("close", null, signal);
        });
      }
      return true;
    };

    const status = await stopChild(child, lifecycle, {
      terminateTimeoutMs: 10,
      killTimeoutMs: 100,
      signalChild,
    });

    expect(signals).toEqual(["SIGTERM", "SIGKILL"]);
    expect(status).toEqual({ event: "close", code: null, signal: "SIGKILL" });
    expect(lifecycle.closeStatus).toEqual(status);
  });
});
