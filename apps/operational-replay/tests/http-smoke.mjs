import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const READY_TIMEOUT_MS = 30_000;
const FETCH_TIMEOUT_MS = 2_000;
const BODY_TIMEOUT_MS = 5_000;
const RETRY_DELAY_MS = 200;
const TERMINATE_TIMEOUT_MS = 5_000;
const KILL_TIMEOUT_MS = 2_000;

function timeoutMilliseconds(value) {
  return Math.max(1, Math.ceil(value));
}

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

export async function selectLoopbackPort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      assert(address && typeof address === "object");
      const selected = address.port;
      server.close((error) =>
        error ? reject(error) : resolvePort(selected),
      );
    });
  });
}

function spawnReplayServer(port) {
  return spawn(
    process.platform === "win32" ? "npm.cmd" : "npm",
    ["run", "start", "--", "--hostname", "127.0.0.1", "--port", String(port)],
    {
      detached: process.platform !== "win32",
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
}

export function observeChild(child) {
  let exitStatus;
  let closeStatus;
  let resolveExit;
  let resolveClose;
  const exited = new Promise((resolveStatus) => {
    resolveExit = resolveStatus;
  });
  const closed = new Promise((resolveStatus) => {
    resolveClose = resolveStatus;
  });

  const settleExit = (status) => {
    if (!exitStatus) {
      exitStatus = status;
      resolveExit(status);
    }
  };
  const settleClose = (status) => {
    if (!closeStatus) {
      closeStatus = status;
      resolveClose(status);
    }
  };

  child.once("error", (error) => {
    const status = { event: "error", error };
    settleExit(status);
    settleClose(status);
  });
  child.once("exit", (code, signal) => {
    settleExit({ event: "exit", code, signal });
  });
  child.once("close", (code, signal) => {
    settleClose({ event: "close", code, signal });
  });

  return {
    exited,
    closed,
    get exitStatus() {
      return exitStatus;
    },
    get closeStatus() {
      return closeStatus;
    },
  };
}

function exitBeforeReadyError(status) {
  if (status.event === "error") {
    return new Error(`server failed to start: ${status.error.message}`);
  }
  return new Error(
    `server exited before becoming ready (code ${status.code ?? "null"}, ` +
      `signal ${status.signal ?? "none"})`,
  );
}

function raceChildExit(lifecycle, pending) {
  if (lifecycle.exitStatus) {
    return Promise.resolve({ kind: "exit", status: lifecycle.exitStatus });
  }
  return Promise.race([
    lifecycle.exited.then((status) => ({ kind: "exit", status })),
    pending.then(
      (value) => ({ kind: "value", value }),
      (error) => ({ kind: "error", error }),
    ),
  ]);
}

export function fetchWithTimeout(url, timeoutMs, fetchImpl = globalThis.fetch) {
  return fetchImpl(url, {
    redirect: "error",
    signal: AbortSignal.timeout(timeoutMilliseconds(timeoutMs)),
  });
}

function readChunkWithSignal(reader, signal) {
  if (signal.aborted) {
    return Promise.reject(signal.reason);
  }

  return new Promise((resolveChunk, reject) => {
    let settled = false;
    const finish = (callback, value) => {
      if (settled) {
        return;
      }
      settled = true;
      signal.removeEventListener("abort", onAbort);
      callback(value);
    };
    const onAbort = () => {
      void reader.cancel(signal.reason).catch(() => {});
      finish(reject, signal.reason);
    };

    signal.addEventListener("abort", onAbort, { once: true });
    reader.read().then(
      (chunk) => finish(resolveChunk, chunk),
      (error) => finish(reject, error),
    );
  });
}

export async function readResponseTextWithTimeout(response, timeoutMs) {
  if (!response.body) {
    return "";
  }

  const signal = AbortSignal.timeout(timeoutMilliseconds(timeoutMs));
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = "";

  try {
    while (true) {
      const { done, value } = await readChunkWithSignal(reader, signal);
      if (done) {
        return text + decoder.decode();
      }
      text += decoder.decode(value, { stream: true });
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // A timed-out read owns the lock until cancellation reaches the stream.
    }
  }
}

async function waitForReady({
  url,
  lifecycle,
  fetchImpl,
  readyTimeoutMs,
  fetchTimeoutMs,
  retryDelayMs,
}) {
  const deadline = Date.now() + readyTimeoutMs;
  let lastFetchError;

  while (Date.now() < deadline) {
    const remaining = deadline - Date.now();
    const attempt = fetchWithTimeout(
      url,
      Math.min(fetchTimeoutMs, remaining),
      fetchImpl,
    );
    const result = await raceChildExit(lifecycle, attempt);

    if (result.kind === "exit") {
      throw exitBeforeReadyError(result.status);
    }
    if (result.kind === "value") {
      return result.value;
    }
    lastFetchError = result.error;

    const pause = await raceChildExit(
      lifecycle,
      delay(Math.min(retryDelayMs, Math.max(0, deadline - Date.now()))),
    );
    if (pause.kind === "exit") {
      throw exitBeforeReadyError(pause.status);
    }
  }

  const detail = lastFetchError ? `: ${lastFetchError.message}` : "";
  throw new Error(`server did not become ready within ${readyTimeoutMs}ms${detail}`);
}

function signalChildTree(child, signal) {
  if (process.platform !== "win32" && Number.isInteger(child.pid)) {
    try {
      process.kill(-child.pid, signal);
      return true;
    } catch (error) {
      if (error.code === "ESRCH") {
        return false;
      }
      throw error;
    }
  }
  return child.kill(signal);
}

async function settleWithin(promise, timeoutMs) {
  let timer;
  const timeout = new Promise((resolveTimeout) => {
    timer = setTimeout(() => resolveTimeout({ timedOut: true }), timeoutMs);
  });
  try {
    return await Promise.race([
      promise.then((value) => ({ timedOut: false, value })),
      timeout,
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export async function stopChild(
  child,
  lifecycle,
  {
    terminateTimeoutMs = TERMINATE_TIMEOUT_MS,
    killTimeoutMs = KILL_TIMEOUT_MS,
    signalChild = signalChildTree,
  } = {},
) {
  if (lifecycle.closeStatus) {
    return lifecycle.closeStatus;
  }

  signalChild(child, "SIGTERM");
  const terminated = await settleWithin(lifecycle.closed, terminateTimeoutMs);
  if (!terminated.timedOut) {
    return terminated.value;
  }

  signalChild(child, "SIGKILL");
  const killed = await settleWithin(lifecycle.closed, killTimeoutMs);
  if (killed.timedOut) {
    throw new Error("server did not close after SIGKILL");
  }
  return killed.value;
}

function withServerOutput(error, output) {
  if (!output.trim()) {
    return error;
  }
  return new Error(`${error.message}\n${output}`, { cause: error });
}

export async function runHttpSmoke({
  port: requestedPort,
  spawnServer = spawnReplayServer,
  fetchImpl = globalThis.fetch,
  write = (message) => process.stdout.write(message),
  readyTimeoutMs = READY_TIMEOUT_MS,
  fetchTimeoutMs = FETCH_TIMEOUT_MS,
  bodyTimeoutMs = BODY_TIMEOUT_MS,
  retryDelayMs = RETRY_DELAY_MS,
  terminateTimeoutMs = TERMINATE_TIMEOUT_MS,
  killTimeoutMs = KILL_TIMEOUT_MS,
  signalChild = signalChildTree,
} = {}) {
  const port = requestedPort ?? (await selectLoopbackPort());
  const child = spawnServer(port);
  const lifecycle = observeChild(child);
  let output = "";
  child.stdout?.on("data", (chunk) => {
    output += chunk;
  });
  child.stderr?.on("data", (chunk) => {
    output += chunk;
  });

  let failure;
  try {
    const response = await waitForReady({
      url: `http://127.0.0.1:${port}/`,
      lifecycle,
      fetchImpl,
      readyTimeoutMs,
      fetchTimeoutMs,
      retryDelayMs,
    });
    const html = await readResponseTextWithTimeout(response, bodyTimeoutMs);

    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
    assert.match(html, /<title>NXTektal Replay Story<\/title>/i);
    assert.match(html, /Operational replay/);
    assert.match(html, /Simulation results/);
    assert.match(html, /Presentation layer only/);
  } catch (error) {
    failure = withServerOutput(error, output);
  }

  try {
    await stopChild(child, lifecycle, {
      terminateTimeoutMs,
      killTimeoutMs,
      signalChild,
    });
  } catch (cleanupError) {
    failure = failure
      ? new AggregateError([failure, cleanupError], "HTTP smoke and cleanup failed")
      : cleanupError;
  }

  if (failure) {
    throw failure;
  }
  write(`HTTP smoke passed on 127.0.0.1:${port}\n`);
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  await runHttpSmoke();
}
