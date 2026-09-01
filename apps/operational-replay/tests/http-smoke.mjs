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
const ANSI_ESCAPE = /\x1b\[[0-?]*[ -/]*[@-~]/g;

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
  let stopRequested = false;
  let exitedBeforeStop = false;
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
      if (!stopRequested) {
        exitedBeforeStop = true;
      }
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
    beginStop() {
      stopRequested = true;
    },
    markMissingBeforeSignal() {
      exitedBeforeStop = true;
    },
    get exitedBeforeStop() {
      return exitedBeforeStop;
    },
  };
}

function unexpectedExitError(status, phase) {
  if (!status) {
    return new Error(`server exited ${phase}`);
  }
  if (status.event === "error") {
    return new Error(`server failed ${phase}: ${status.error.message}`);
  }
  return new Error(
    `server exited ${phase} (code ${status.code ?? "null"}, ` +
      `signal ${status.signal ?? "none"})`,
  );
}

function observeInterruptions() {
  let signalName;
  let resolveInterruption;
  const interrupted = new Promise((resolveSignal) => {
    resolveInterruption = resolveSignal;
  });
  const handlers = new Map();

  for (const candidate of ["SIGINT", "SIGTERM"]) {
    const handler = () => {
      if (!signalName) {
        signalName = candidate;
        resolveInterruption(candidate);
      }
    };
    handlers.set(candidate, handler);
    process.on(candidate, handler);
  }

  return {
    interrupted,
    get signalName() {
      return signalName;
    },
    close() {
      for (const [candidate, handler] of handlers) {
        process.removeListener(candidate, handler);
      }
    },
  };
}

function interruptionError(signalName) {
  return new Error(`HTTP smoke interrupted by ${signalName}`);
}

function raceRuntime(lifecycle, interruptions, pending) {
  if (lifecycle.exitStatus) {
    return Promise.resolve({ kind: "exit", status: lifecycle.exitStatus });
  }
  if (interruptions.signalName) {
    return Promise.resolve({ kind: "interrupt", signal: interruptions.signalName });
  }
  return Promise.race([
    lifecycle.exited.then((status) => ({ kind: "exit", status })),
    interruptions.interrupted.then((signal) => ({ kind: "interrupt", signal })),
    pending.then(
      (value) => ({ kind: "value", value }),
      (error) => ({ kind: "error", error }),
    ),
  ]);
}

function childAnnouncedReady(output, url) {
  const lines = output.replace(ANSI_ESCAPE, "").replaceAll("\r", "").split("\n");
  let endpointSeen = false;

  for (const line of lines) {
    const trimmed = line.trim();
    const local = /^-\s+Local:\s+(\S+)$/.exec(trimmed);
    if (local) {
      endpointSeen = local[1].replace(/\/$/, "") === url;
      continue;
    }
    if (
      endpointSeen &&
      /^[✓✔]?\s*Ready in\s+\d+(?:\.\d+)?(?:ms|s)$/.test(trimmed)
    ) {
      return true;
    }
  }
  return false;
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

async function waitForLaunchReady({
  url,
  lifecycle,
  interruptions,
  readOutput,
  deadline,
  readyTimeoutMs,
  retryDelayMs,
}) {
  while (Date.now() < deadline) {
    if (lifecycle.exitStatus) {
      throw unexpectedExitError(lifecycle.exitStatus, "before becoming ready");
    }
    if (interruptions.signalName) {
      throw interruptionError(interruptions.signalName);
    }
    if (childAnnouncedReady(readOutput(), url)) {
      return;
    }

    const pause = await raceRuntime(
      lifecycle,
      interruptions,
      delay(Math.min(retryDelayMs, Math.max(0, deadline - Date.now()))),
    );
    if (pause.kind === "exit") {
      throw unexpectedExitError(pause.status, "before becoming ready");
    }
    if (pause.kind === "interrupt") {
      throw interruptionError(pause.signal);
    }
  }

  throw new Error(
    `server did not announce ${url} as ready within ${readyTimeoutMs}ms`,
  );
}

async function waitForReady({
  url,
  lifecycle,
  interruptions,
  fetchImpl,
  deadline,
  readyTimeoutMs,
  fetchTimeoutMs,
  retryDelayMs,
}) {
  let lastFetchError;

  while (Date.now() < deadline) {
    const remaining = deadline - Date.now();
    const attempt = fetchWithTimeout(
      url,
      Math.min(fetchTimeoutMs, remaining),
      fetchImpl,
    );
    const result = await raceRuntime(lifecycle, interruptions, attempt);

    if (result.kind === "exit") {
      throw unexpectedExitError(result.status, "before becoming ready");
    }
    if (result.kind === "interrupt") {
      throw interruptionError(result.signal);
    }
    if (result.kind === "value") {
      return result.value;
    }
    lastFetchError = result.error;

    const pause = await raceRuntime(
      lifecycle,
      interruptions,
      delay(Math.min(retryDelayMs, Math.max(0, deadline - Date.now()))),
    );
    if (pause.kind === "exit") {
      throw unexpectedExitError(pause.status, "before becoming ready");
    }
    if (pause.kind === "interrupt") {
      throw interruptionError(pause.signal);
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
  if (
    (child.exitCode !== undefined && child.exitCode !== null) ||
    (child.signalCode !== undefined && child.signalCode !== null)
  ) {
    lifecycle.markMissingBeforeSignal();
  }
  lifecycle.beginStop();
  if (lifecycle.closeStatus) {
    return lifecycle.closeStatus;
  }

  const terminateSent = signalChild(child, "SIGTERM");
  if (!terminateSent) {
    lifecycle.markMissingBeforeSignal();
  }
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

function unwrapRuntimeResult(result, phase) {
  if (result.kind === "exit") {
    throw unexpectedExitError(result.status, phase);
  }
  if (result.kind === "interrupt") {
    throw interruptionError(result.signal);
  }
  if (result.kind === "error") {
    throw result.error;
  }
  return result.value;
}

async function fetchSmokePage({
  url,
  lifecycle,
  interruptions,
  fetchImpl,
  fetchTimeoutMs,
  bodyTimeoutMs,
}) {
  const response = unwrapRuntimeResult(
    await raceRuntime(
      lifecycle,
      interruptions,
      fetchWithTimeout(url, fetchTimeoutMs, fetchImpl),
    ),
    "before route smoke completed",
  );
  const html = unwrapRuntimeResult(
    await raceRuntime(
      lifecycle,
      interruptions,
      readResponseTextWithTimeout(response, bodyTimeoutMs),
    ),
    "before route smoke completed",
  );
  return { response, html };
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
  const interruptions = observeInterruptions();
  let child;
  let lifecycle;
  let output = "";
  try {
    child = spawnServer(port);
    lifecycle = observeChild(child);
    child.stdout?.setEncoding?.("utf8");
    child.stderr?.setEncoding?.("utf8");
    child.stdout?.on("data", (chunk) => {
      output += chunk;
    });
    child.stderr?.on("data", (chunk) => {
      output += chunk;
    });
  } catch (error) {
    interruptions.close();
    throw error;
  }

  let failure;
  try {
    const baseUrl = `http://127.0.0.1:${port}`;
    const readyDeadline = Date.now() + readyTimeoutMs;
    await waitForLaunchReady({
      url: baseUrl,
      lifecycle,
      interruptions,
      readOutput: () => output,
      deadline: readyDeadline,
      readyTimeoutMs,
      retryDelayMs,
    });
    const response = await waitForReady({
      url: `${baseUrl}/`,
      lifecycle,
      interruptions,
      fetchImpl,
      deadline: readyDeadline,
      readyTimeoutMs,
      fetchTimeoutMs,
      retryDelayMs,
    });
    const bodyResult = await raceRuntime(
      lifecycle,
      interruptions,
      readResponseTextWithTimeout(response, bodyTimeoutMs),
    );
    if (bodyResult.kind === "exit") {
      throw unexpectedExitError(bodyResult.status, "before smoke completed");
    }
    if (bodyResult.kind === "interrupt") {
      throw interruptionError(bodyResult.signal);
    }
    if (bodyResult.kind === "error") {
      throw bodyResult.error;
    }
    const html = bodyResult.value;

    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
    assert.match(html, /<title>NXTektal Replay Story<\/title>/i);
    assert.match(html, /Operational replay/);
    assert.match(html, /Simulation results/);
    assert.match(html, /Presentation layer only/);

    const dispatch = await fetchSmokePage({
      url: `${baseUrl}/yc-dispatch-report`,
      lifecycle,
      interruptions,
      fetchImpl,
      fetchTimeoutMs,
      bodyTimeoutMs,
    });
    assert.equal(dispatch.response.status, 200);
    assert.match(
      dispatch.response.headers.get("content-type") ?? "",
      /^text\/html\b/i,
    );
    assert.match(
      dispatch.html,
      /<title>YC Dispatch \/ Report \| NXTektal Systems<\/title>/i,
    );
    assert.match(dispatch.html, /data-active-state="dispatch"/i);
    assert.match(dispatch.html, /data-demo-state="dispatch"/i);
    assert.match(dispatch.html, /<strong[^>]*>Dispatched<\/strong>/i);
    assert.doesNotMatch(dispatch.html, /data-demo-state="report"/i);
    assert.match(dispatch.html, /range-scanned-demo\.webp/i);
    assert.match(dispatch.html, /Site presentation schematic/i);
    assert.match(dispatch.html, /Scan-style range scene/i);
    assert.match(dispatch.html, /Presentation-only route animation/i);
    assert.match(dispatch.html, /Picker-01/i);
    assert.match(dispatch.html, /Collect range balls/i);
    assert.match(dispatch.html, /Zone A/i);
    assert.match(dispatch.html, /RGO-0828-01/i);
    assert.match(dispatch.html, /Tee line/i);
    assert.match(dispatch.html, /Return station/i);
    assert.match(dispatch.html, /Picker-01 start/i);
    assert.match(
      dispatch.html,
      /Prototype orchestration demo · supervised hardware execution/i,
    );
    assert.doesNotMatch(
      dispatch.html,
      /actual scan output|actual SLAM output|SLAM map|survey-grade map|surveyed site model|live digital twin|real-time robot tracking|autonomous navigation output|Fully autonomous|No intervention required|Autonomous mission completed/i,
    );

    const report = await fetchSmokePage({
      url: `${baseUrl}/yc-dispatch-report?state=report`,
      lifecycle,
      interruptions,
      fetchImpl,
      fetchTimeoutMs,
      bodyTimeoutMs,
    });
    assert.equal(report.response.status, 200);
    assert.match(
      report.response.headers.get("content-type") ?? "",
      /^text\/html\b/i,
    );
    assert.match(
      report.html,
      /<title>YC Dispatch \/ Report \| NXTektal Systems<\/title>/i,
    );
    assert.match(report.html, /data-active-state="report"/i);
    assert.match(report.html, /data-demo-state="report"/i);
    assert.match(report.html, /<strong[^>]*>Complete<\/strong>/i);
    assert.doesNotMatch(report.html, /data-demo-state="dispatch"/i);
    assert.match(report.html, /Mission report generated/i);
    assert.match(report.html, /Supervised prototype/i);
    assert.match(
      report.html,
      /Prototype orchestration demo · supervised hardware execution/i,
    );
    assert.doesNotMatch(
      report.html,
      /Report saved to facility operations log|Scripted presentation copy|Fully autonomous|No intervention required|Autonomous mission completed/i,
    );
    assert.doesNotMatch(
      report.html,
      /Update after field run|Placeholder|\bTBD\b|Replace this value|Mock value/i,
    );
    if (interruptions.signalName) {
      throw interruptionError(interruptions.signalName);
    }
    if (lifecycle.exitStatus) {
      throw unexpectedExitError(lifecycle.exitStatus, "before smoke completed");
    }
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
  } finally {
    interruptions.close();
  }

  if (!failure && interruptions.signalName) {
    failure = interruptionError(interruptions.signalName);
  }
  if (!failure && lifecycle.exitedBeforeStop) {
    failure = withServerOutput(
      unexpectedExitError(
        lifecycle.exitStatus ?? lifecycle.closeStatus,
        "before smoke cleanup began",
      ),
      output,
    );
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
