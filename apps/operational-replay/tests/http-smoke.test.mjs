import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { createServer } from "node:http";
import { PassThrough } from "node:stream";
import { describe, expect, test } from "vitest";

import {
  fetchWithTimeout,
  observeChild,
  readResponseTextWithTimeout,
  runHttpSmoke,
  stopChild,
} from "./http-smoke.mjs";

const delay = (milliseconds) =>
  new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));

const ROOT_SMOKE_HTML =
  "<title>NXTektal Replay Story</title> Operational replay " +
  "Simulation results Presentation layer only";

function smokeHtmlForUrl(url) {
  const requested = new URL(url);
  if (requested.pathname !== "/yc-dispatch-report") {
    return ROOT_SMOKE_HTML;
  }
  if (requested.searchParams.get("state") === "report") {
    return (
      "<title>YC Dispatch / Report | NXTektal Systems</title>" +
      '<main data-active-state="report"><section data-demo-state="report">' +
      "<strong>Complete</strong> Supervised prototype " +
      "Scripted presentation copy · no live log write</section></main>"
    );
  }
  return (
    "<title>YC Dispatch / Report | NXTektal Systems</title>" +
    '<main data-active-state="dispatch"><section data-demo-state="dispatch">' +
    "<strong>Dispatched</strong> " +
    "Prototype orchestration demo · supervised hardware execution</section></main>"
  );
}

function smokeResponse(url) {
  return new Response(smokeHtmlForUrl(url), {
    status: 200,
    headers: { "content-type": "text/html" },
  });
}

function processIsAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (error.code === "ESRCH") {
      return false;
    }
    throw error;
  }
}

async function waitForProcessExit(pid, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!processIsAlive(pid)) {
      return true;
    }
    await delay(10);
  }
  return !processIsAlive(pid);
}

function readFirstLine(stream, timeoutMs) {
  return new Promise((resolveLine, reject) => {
    let buffered = "";
    const timer = setTimeout(() => finish(reject, new Error("stdout line timed out")), timeoutMs);
    const finish = (callback, value) => {
      clearTimeout(timer);
      stream.removeListener("data", onData);
      stream.removeListener("error", onError);
      callback(value);
    };
    const onData = (chunk) => {
      buffered += chunk;
      const newline = buffered.indexOf("\n");
      if (newline !== -1) {
        finish(resolveLine, buffered.slice(0, newline));
      }
    };
    const onError = (error) => finish(reject, error);
    stream.on("data", onData);
    stream.on("error", onError);
  });
}

function waitForClose(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({ code: child.exitCode, signal: child.signalCode });
  }
  return Promise.race([
    new Promise((resolveClose) => {
      child.once("close", (code, signal) => resolveClose({ code, signal }));
    }),
    delay(timeoutMs).then(() => {
      throw new Error("child close timed out");
    }),
  ]);
}

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

  test("rejects an unrelated listener when the launched child owns no endpoint", async () => {
    const html =
      "<title>NXTektal Replay Story</title> Operational replay " +
      "Simulation results Presentation layer only";
    const unrelated = createServer((_request, response) => {
      response.writeHead(200, { "content-type": "text/html" });
      response.end(html);
    });
    await new Promise((resolveListen, reject) => {
      unrelated.once("error", reject);
      unrelated.listen(0, "127.0.0.1", resolveListen);
    });
    const address = unrelated.address();
    expect(address && typeof address === "object").toBe(true);
    const port = address.port;
    let launched;

    try {
      await expect(
        runHttpSmoke({
          port,
          spawnServer: () => {
            launched = spawn(
              process.execPath,
              [
                "-e",
                `console.log("- Local: http://127.0.0.1:${port + 1}"); ` +
                  'console.log("Ready in 1ms"); setTimeout(() => {}, 30_000)',
              ],
              { stdio: ["ignore", "pipe", "pipe"] },
            );
            return launched;
          },
          readyTimeoutMs: 100,
          fetchTimeoutMs: 50,
          retryDelayMs: 5,
          terminateTimeoutMs: 1_000,
          killTimeoutMs: 1_000,
          signalChild: (child, signal) => child.kill(signal),
          write: () => {},
        }),
      ).rejects.toThrow(
        new RegExp(`did not announce http://127\\.0\\.0\\.1:${port} as ready`),
      );
      expect(launched.signalCode).toBe("SIGTERM");
    } finally {
      if (launched?.exitCode === null && launched.signalCode === null) {
        launched.kill("SIGKILL");
        await waitForClose(launched, 1_000);
      }
      await new Promise((resolveClose, reject) => {
        unrelated.close((error) => (error ? reject(error) : resolveClose()));
      });
    }
  });

  test("decodes a readiness marker split across UTF-8 pipe chunks", async () => {
    let child;

    const smoke = runHttpSmoke({
      port: 54321,
      spawnServer: (port) => {
        child = new EventEmitter();
        child.stdout = new PassThrough();
        child.stderr = new PassThrough();
        child.exitCode = null;
        child.signalCode = null;
        queueMicrotask(() => {
          const ready = Buffer.from(
            `- Local: http://127.0.0.1:${port}\n✓ Ready in 1ms\n`,
          );
          const markerOffset = ready.indexOf(Buffer.from("✓"));
          child.stdout.write(ready.subarray(0, markerOffset + 1));
          child.stdout.write(ready.subarray(markerOffset + 1));
        });
        return child;
      },
      fetchImpl: async (url) => smokeResponse(url),
      readyTimeoutMs: 1_000,
      fetchTimeoutMs: 500,
      bodyTimeoutMs: 500,
      retryDelayMs: 5,
      terminateTimeoutMs: 1_000,
      killTimeoutMs: 1_000,
      signalChild: (_child, signal) => {
        queueMicrotask(() => {
          child.signalCode = signal;
          child.emit("exit", null, signal);
          child.emit("close", null, signal);
        });
        return true;
      },
      write: () => {},
    });

    await expect(smoke).resolves.toBeUndefined();
  });

  test("fails when the launched child exits during the response body", async () => {
    const encoder = new TextEncoder();
    const html =
      "<title>NXTektal Replay Story</title> Operational replay " +
      "Simulation results Presentation layer only";
    let launched;

    const smoke = runHttpSmoke({
      port: 54321,
      spawnServer: (port) => {
        launched = spawn(
          process.execPath,
          [
            "-e",
            `console.log("- Local: http://127.0.0.1:${port}"); ` +
              'console.log("Ready in 1ms"); setTimeout(() => process.exit(17), 100)',
          ],
          { stdio: ["ignore", "pipe", "pipe"] },
        );
        return launched;
      },
      fetchImpl: async () =>
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode(html.slice(0, 20)));
              setTimeout(() => {
                controller.enqueue(encoder.encode(html.slice(20)));
                controller.close();
              }, 300);
            },
          }),
          { status: 200, headers: { "content-type": "text/html" } },
        ),
      readyTimeoutMs: 1_000,
      fetchTimeoutMs: 500,
      bodyTimeoutMs: 1_000,
      retryDelayMs: 5,
      terminateTimeoutMs: 1_000,
      killTimeoutMs: 1_000,
      signalChild: (child, signal) => child.kill(signal),
      write: () => {},
    });

    await expect(smoke).rejects.toThrow(
      /exited before smoke completed \(code 17, signal none\)/,
    );
    expect(launched.exitCode).toBe(17);
  });

  test.skipIf(process.platform === "win32")(
    "cleans the detached server tree when the smoke receives SIGTERM",
    async () => {
      const smokeUrl = new URL("./http-smoke.mjs", import.meta.url).href;
      const wrapperSource = `
        import { spawn } from "node:child_process";
        import { runHttpSmoke } from ${JSON.stringify(smokeUrl)};
        await runHttpSmoke({
          port: 54321,
          spawnServer: () => {
            const child = spawn(
              process.execPath,
              ["-e", "setInterval(() => {}, 1000)"],
              { detached: true, stdio: "ignore" },
            );
            console.log(child.pid);
            return child;
          },
          fetchImpl: () => new Promise(() => {}),
          readyTimeoutMs: 30_000,
          fetchTimeoutMs: 30_000,
          terminateTimeoutMs: 1_000,
          killTimeoutMs: 1_000,
          write: () => {},
        });
      `;
      const wrapper = spawn(
        process.execPath,
        ["--input-type=module", "-e", wrapperSource],
        { stdio: ["ignore", "pipe", "pipe"] },
      );
      let probePid;
      let stderr = "";
      wrapper.stderr.on("data", (chunk) => {
        stderr += chunk;
      });

      try {
        probePid = Number.parseInt(await readFirstLine(wrapper.stdout, 2_000), 10);
        expect(Number.isInteger(probePid)).toBe(true);
        expect(processIsAlive(probePid)).toBe(true);

        wrapper.kill("SIGTERM");
        const status = await waitForClose(wrapper, 5_000);
        expect(status.code).not.toBe(0);
        expect(stderr).toMatch(/HTTP smoke interrupted by SIGTERM/);
        expect(await waitForProcessExit(probePid, 2_000)).toBe(true);
      } finally {
        if (wrapper.exitCode === null && wrapper.signalCode === null) {
          wrapper.kill("SIGKILL");
          await waitForClose(wrapper, 1_000).catch(() => {});
        }
        if (Number.isInteger(probePid) && processIsAlive(probePid)) {
          try {
            process.kill(-probePid, "SIGKILL");
          } catch (error) {
            if (error.code !== "ESRCH") {
              throw error;
            }
          }
          if (!(await waitForProcessExit(probePid, 1_000))) {
            throw new Error("failed to clean detached signal-test child");
          }
        }
      }
    },
  );

  test("fails if SIGTERM arrives while successful smoke cleanup is waiting", async () => {
    let child;

    const smoke = runHttpSmoke({
      port: 54321,
      spawnServer: (port) => {
        child = new EventEmitter();
        child.stdout = new EventEmitter();
        child.stderr = new EventEmitter();
        child.exitCode = null;
        child.signalCode = null;
        queueMicrotask(() => {
          child.stdout.emit(
            "data",
            `- Local: http://127.0.0.1:${port}\nReady in 1ms\n`,
          );
        });
        return child;
      },
      fetchImpl: async (url) => smokeResponse(url),
      readyTimeoutMs: 1_000,
      fetchTimeoutMs: 500,
      bodyTimeoutMs: 500,
      retryDelayMs: 5,
      terminateTimeoutMs: 1_000,
      killTimeoutMs: 1_000,
      signalChild: (_child, signal) => {
        expect(signal).toBe("SIGTERM");
        process.emit("SIGTERM");
        setTimeout(() => {
          child.signalCode = signal;
          child.emit("exit", null, signal);
          child.emit("close", null, signal);
        }, 10);
        return true;
      },
      write: () => {},
    });

    await expect(smoke).rejects.toThrow(/HTTP smoke interrupted by SIGTERM/);
  });

  test("rejects a recorded child exit before its lifecycle event is delivered", async () => {
    let child;

    const smoke = runHttpSmoke({
      port: 54321,
      spawnServer: (port) => {
        child = new EventEmitter();
        child.stdout = new EventEmitter();
        child.stderr = new EventEmitter();
        child.exitCode = 17;
        child.signalCode = null;
        queueMicrotask(() => {
          child.stdout.emit(
            "data",
            `- Local: http://127.0.0.1:${port}\nReady in 1ms\n`,
          );
        });
        return child;
      },
      fetchImpl: async (url) => smokeResponse(url),
      readyTimeoutMs: 1_000,
      fetchTimeoutMs: 500,
      bodyTimeoutMs: 500,
      retryDelayMs: 5,
      terminateTimeoutMs: 1_000,
      killTimeoutMs: 1_000,
      signalChild: (_child, signal) => {
        queueMicrotask(() => child.emit("close", 17, null));
        return signal === "SIGTERM";
      },
      write: () => {},
    });

    await expect(smoke).rejects.toThrow(
      /exited before smoke cleanup began \(code 17, signal none\)/,
    );
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
