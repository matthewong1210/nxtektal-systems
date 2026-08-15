#!/usr/bin/env node

/**
 * Production-browser verification for the Edge Gateway demo.
 *
 * This script intentionally has no npm browser-driver dependency. It drives an
 * installed Chrome/Chromium executable over the DevTools protocol using only
 * Node.js >=22 built-ins. Build the app first, then either let this script own a
 * loopback `next start` process or pass an already-running local/hosted origin:
 *
 *   node tests/edge-gateway-demo/browser-verify.mjs \
 *     --output-dir "$(mktemp -d)"
 *   node tests/edge-gateway-demo/browser-verify.mjs \
 *     --base-url http://127.0.0.1:3000 \
 *     --output-dir "$(mktemp -d)"
 *
 * Captures are review evidence, never repository assets. The output directory
 * must be outside the repository and empty so a run cannot overwrite evidence.
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { access, mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import { createServer } from "node:net";
import { spawn } from "node:child_process";
import { delimiter, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = resolve(SCRIPT_DIR, "..", "..");
const REPOSITORY_ROOT = resolve(APP_ROOT, "..", "..");
const DEMO_PATH = "/edge-gateway-demo";
const READY_TIMEOUT_MS = 45_000;
const PAGE_TIMEOUT_MS = 30_000;
const SERVER_STOP_TIMEOUT_MS = 5_000;
const BROWSER_STOP_TIMEOUT_MS = 5_000;

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
  { width: 1024, height: 768 },
  { width: 768, height: 1024 },
  { width: 680, height: 800 },
  { width: 390, height: 844 },
  { width: 375, height: 667 },
];

const SCENES = [
  "Installed System",
  "Exploded Gateway",
  "Operational Flow",
  "Scale the Fleet",
  "Software Update",
  "Safety Architecture",
];

const PRIMARY_CONTROLS = [
  "Perspective",
  "Orthographic",
  "Front",
  "Side",
  "Top",
  "Isometric",
  "Reset Camera",
  "Show power",
  "Show network",
  "Show telemetry",
  "Show safety",
];

const TIMELINE_CONTROLS = [
  "Installed Gateway overview",
  "Open the conceptual enclosure",
  "Identify conceptual Gateway components",
  "Illustrative simulated operating flow",
  "Record manager workflow evidence; issue no command",
  "Show separate RangeOps replay; do not infer causality",
  "Conceptual fleet onboarding with unchanged Gateway identity",
  "Conceptual signed update and health-check success",
  "Conceptual failed health check and automatic rollback",
  "Independent local safety path",
  "An updatable on-site operating layer for autonomous golf facilities",
];

function usage() {
  return `Usage:
  node tests/edge-gateway-demo/browser-verify.mjs --output-dir <outside-repo-dir>
  node tests/edge-gateway-demo/browser-verify.mjs --base-url <url> --output-dir <outside-repo-dir>

Options:
  --base-url <url>       Test an existing local or hosted production server.
  --output-dir <path>    Required empty directory outside this repository.
  --browser-path <path>  Chrome/Chromium executable (or set BROWSER_PATH).
  --help                 Show this help.
`;
}

function parseArguments(argv) {
  const options = { baseUrl: undefined, browserPath: undefined, outputDir: undefined };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help") {
      process.stdout.write(usage());
      process.exit(0);
    }
    if (!["--base-url", "--browser-path", "--output-dir"].includes(argument)) {
      throw new Error(`unknown argument: ${argument}\n${usage()}`);
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`${argument} requires a value`);
    }
    index += 1;
    if (argument === "--base-url") options.baseUrl = value;
    if (argument === "--browser-path") options.browserPath = value;
    if (argument === "--output-dir") options.outputDir = value;
  }
  if (!options.outputDir) {
    throw new Error(`--output-dir is required\n${usage()}`);
  }
  return options;
}

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

function pathIsWithin(parent, candidate) {
  const pathFromParent = relative(parent, candidate);
  return pathFromParent === "" || (!pathFromParent.startsWith(`..${sep}`) && pathFromParent !== "..");
}

async function prepareOutputDirectory(value) {
  const outputDirectory = resolve(value);
  if (pathIsWithin(REPOSITORY_ROOT, outputDirectory)) {
    throw new Error(`--output-dir must be outside the repository: ${outputDirectory}`);
  }
  await mkdir(outputDirectory, { recursive: true });
  const existing = await readdir(outputDirectory);
  if (existing.length > 0) {
    throw new Error(`--output-dir must be empty: ${outputDirectory}`);
  }
  return outputDirectory;
}

async function selectLoopbackPort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      assert(address && typeof address === "object");
      server.close((error) => (error ? reject(error) : resolvePort(address.port)));
    });
  });
}

function observeProcess(child, label) {
  let output = "";
  let exitStatus;
  let resolveExit;
  const exited = new Promise((resolveStatus) => {
    resolveExit = resolveStatus;
  });
  child.stdout?.setEncoding("utf8");
  child.stderr?.setEncoding("utf8");
  const append = (chunk) => {
    output = `${output}${chunk}`.slice(-64 * 1024);
  };
  child.stdout?.on("data", append);
  child.stderr?.on("data", append);
  child.once("error", (error) => {
    exitStatus = { error, code: null, signal: null };
    resolveExit(exitStatus);
  });
  child.once("exit", (code, signal) => {
    exitStatus = { error: undefined, code, signal };
    resolveExit(exitStatus);
  });
  return {
    child,
    exited,
    get exitStatus() {
      return exitStatus;
    },
    get output() {
      return output;
    },
    label,
  };
}

async function stopProcess(observed, timeoutMs) {
  if (!observed || observed.exitStatus) return;
  const child = observed.child;
  const signal = (name) => {
    if (process.platform !== "win32" && Number.isInteger(child.pid)) {
      try {
        process.kill(-child.pid, name);
        return;
      } catch (error) {
        if (error.code === "ESRCH") return;
        throw error;
      }
    }
    child.kill(name);
  };
  signal("SIGTERM");
  const terminated = await Promise.race([
    observed.exited.then(() => true),
    delay(timeoutMs).then(() => false),
  ]);
  if (terminated) return;
  signal("SIGKILL");
  const killed = await Promise.race([
    observed.exited.then(() => true),
    delay(2_000).then(() => false),
  ]);
  if (!killed) throw new Error(`${observed.label} did not stop after SIGKILL`);
}

async function waitForHttp(url, observed, timeoutMs = READY_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    if (observed?.exitStatus) {
      const { code, signal, error } = observed.exitStatus;
      throw new Error(
        `${observed.label} exited before readiness (${error?.message ?? `code ${code}, signal ${signal ?? "none"}`})\n${observed.output}`,
      );
    }
    try {
      const response = await fetch(url, { redirect: "error", signal: AbortSignal.timeout(2_000) });
      if (response.ok) return response;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await delay(150);
  }
  throw new Error(`timed out waiting for ${url}: ${lastError?.message ?? "no response"}`);
}

async function startProductionServer() {
  await access(join(APP_ROOT, ".next", "BUILD_ID"), fsConstants.R_OK).catch(() => {
    throw new Error("production build missing; run `npm run build` before browser verification");
  });
  const port = await selectLoopbackPort();
  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const child = spawn(
    npmCommand,
    ["run", "start", "--", "--hostname", "127.0.0.1", "--port", String(port)],
    {
      cwd: APP_ROOT,
      detached: process.platform !== "win32",
      env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  const observed = observeProcess(child, "production server");
  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    await waitForHttp(`${baseUrl}/`, observed);
  } catch (error) {
    await stopProcess(observed, SERVER_STOP_TIMEOUT_MS);
    throw error;
  }
  return { baseUrl, observed };
}

async function executableExists(candidate) {
  if (!candidate) return false;
  if (!candidate.includes(sep) && !isAbsolute(candidate)) {
    for (const directory of (process.env.PATH ?? "").split(delimiter)) {
      const resolvedCandidate = join(directory, candidate);
      try {
        await access(resolvedCandidate, fsConstants.X_OK);
        return resolvedCandidate;
      } catch {
        // Continue searching PATH.
      }
    }
    return false;
  }
  try {
    await access(candidate, fsConstants.X_OK);
    return candidate;
  } catch {
    return false;
  }
}

async function findBrowser(explicitPath) {
  const candidates = [
    explicitPath,
    process.env.BROWSER_PATH,
    process.env.CHROME_PATH,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
  ];
  for (const candidate of candidates) {
    const found = await executableExists(candidate);
    if (found) return found;
  }
  throw new Error(
    "Chrome/Chromium was not found. Install it or pass --browser-path / set BROWSER_PATH.",
  );
}

async function startBrowser(browserPath, extraArguments = []) {
  const debuggingPort = await selectLoopbackPort();
  const profileDirectory = await mkdtemp(join(tmpdir(), "nxtektal-edge-browser-"));
  const args = [
    "--headless=new",
    `--remote-debugging-port=${debuggingPort}`,
    "--remote-debugging-address=127.0.0.1",
    `--user-data-dir=${profileDirectory}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-sync",
    "--metrics-recording-only",
    "--mute-audio",
    "--remote-allow-origins=*",
    "--window-size=1440,900",
    ...(process.getuid?.() === 0 ? ["--no-sandbox"] : []),
    ...extraArguments,
    "about:blank",
  ];
  const child = spawn(browserPath, args, {
    detached: process.platform !== "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });
  const observed = observeProcess(child, "browser");
  const endpoint = `http://127.0.0.1:${debuggingPort}`;
  try {
    await waitForHttp(`${endpoint}/json/version`, observed);
  } catch (error) {
    await stopProcess(observed, BROWSER_STOP_TIMEOUT_MS);
    await rm(profileDirectory, { recursive: true, force: true });
    throw error;
  }
  return { endpoint, observed, profileDirectory };
}

async function stopBrowser(browser) {
  if (!browser) return;
  let stopError;
  try {
    await stopProcess(browser.observed, BROWSER_STOP_TIMEOUT_MS);
  } catch (error) {
    stopError = error;
  }
  await rm(browser.profileDirectory, { recursive: true, force: true });
  if (stopError) throw stopError;
}

class DevToolsPage {
  constructor(endpoint, webSocket, targetId) {
    this.endpoint = endpoint;
    this.webSocket = webSocket;
    this.targetId = targetId;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    this.consoleErrors = [];
    this.pageErrors = [];
    this.logErrors = [];
    this.requests = [];
    this.responses = [];
    this.inflight = new Set();
  }

  static async create(endpoint) {
    const response = await fetch(`${endpoint}/json/new?${encodeURIComponent("about:blank")}`, {
      method: "PUT",
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) throw new Error(`could not create browser target: HTTP ${response.status}`);
    const target = await response.json();
    const webSocket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolveOpen, rejectOpen) => {
      const timer = setTimeout(() => rejectOpen(new Error("DevTools socket timed out")), 5_000);
      webSocket.addEventListener("open", () => {
        clearTimeout(timer);
        resolveOpen();
      }, { once: true });
      webSocket.addEventListener("error", () => {
        clearTimeout(timer);
        rejectOpen(new Error("DevTools socket failed to open"));
      }, { once: true });
    });
    const page = new DevToolsPage(endpoint, webSocket, target.id);
    webSocket.addEventListener("message", (event) => page.handleMessage(event.data));
    webSocket.addEventListener("close", () => page.rejectPending(new Error("DevTools socket closed")));
    await Promise.all([
      page.send("Page.enable"),
      page.send("Runtime.enable"),
      page.send("Log.enable"),
      page.send("Network.enable"),
      page.send("Performance.enable"),
    ]);
    return page;
  }

  handleMessage(data) {
    const message = JSON.parse(typeof data === "string" ? data : data.toString());
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) {
        pending.reject(new Error(`${pending.method}: ${message.error.message}`));
      } else {
        pending.resolve(message.result ?? {});
      }
      return;
    }
    if (!message.method) return;
    this.recordEvent(message.method, message.params ?? {});
    const callbacks = this.listeners.get(message.method) ?? [];
    for (const callback of callbacks) callback(message.params ?? {});
  }

  recordEvent(method, params) {
    if (method === "Runtime.consoleAPICalled" && ["error", "assert"].includes(params.type)) {
      this.consoleErrors.push(params.args.map((argument) => argument.value ?? argument.description).join(" "));
    }
    if (method === "Runtime.exceptionThrown") {
      this.pageErrors.push(params.exceptionDetails?.exception?.description ?? params.exceptionDetails?.text ?? "exception");
    }
    if (method === "Log.entryAdded" && params.entry?.level === "error") {
      this.logErrors.push({ text: params.entry.text, url: params.entry.url ?? "" });
    }
    if (method === "Network.requestWillBeSent") {
      this.requests.push({
        requestId: params.requestId,
        type: params.type,
        url: params.request?.url,
      });
      if (!params.request?.url?.startsWith("data:")) this.inflight.add(params.requestId);
    }
    if (method === "Network.responseReceived") {
      this.responses.push({
        requestId: params.requestId,
        status: params.response?.status,
        type: params.type,
        url: params.response?.url,
      });
    }
    if (method === "Network.loadingFinished" || method === "Network.loadingFailed") {
      this.inflight.delete(params.requestId);
    }
  }

  rejectPending(error) {
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
  }

  send(method, params = {}) {
    const id = this.nextId;
    this.nextId += 1;
    return new Promise((resolveResult, rejectResult) => {
      this.pending.set(id, { method, resolve: resolveResult, reject: rejectResult });
      this.webSocket.send(JSON.stringify({ id, method, params }));
    });
  }

  waitForEvent(method, timeoutMs = PAGE_TIMEOUT_MS) {
    return new Promise((resolveEvent, rejectEvent) => {
      const timer = setTimeout(() => {
        this.listeners.set(method, (this.listeners.get(method) ?? []).filter((item) => item !== onEvent));
        rejectEvent(new Error(`timed out waiting for ${method}`));
      }, timeoutMs);
      const onEvent = (params) => {
        clearTimeout(timer);
        this.listeners.set(method, (this.listeners.get(method) ?? []).filter((item) => item !== onEvent));
        resolveEvent(params);
      };
      this.listeners.set(method, [...(this.listeners.get(method) ?? []), onEvent]);
    });
  }

  async setViewport({ width, height }) {
    await this.send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: width <= 480,
      screenWidth: width,
      screenHeight: height,
    });
  }

  async navigate(url) {
    this.consoleErrors = [];
    this.pageErrors = [];
    this.logErrors = [];
    this.requests = [];
    this.responses = [];
    this.inflight.clear();
    const loaded = this.waitForEvent("Page.loadEventFired");
    const result = await this.send("Page.navigate", { url });
    if (result.errorText) throw new Error(`navigation failed: ${result.errorText}`);
    await loaded;
    await this.waitForNetworkIdle();
  }

  async waitForNetworkIdle(timeoutMs = 10_000, quietMs = 400) {
    const deadline = Date.now() + timeoutMs;
    let quietSince;
    while (Date.now() < deadline) {
      if (this.inflight.size === 0) {
        quietSince ??= Date.now();
        if (Date.now() - quietSince >= quietMs) return;
      } else {
        quietSince = undefined;
      }
      await delay(50);
    }
    throw new Error(`network did not become idle (${this.inflight.size} requests pending)`);
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) {
      throw new Error(
        result.exceptionDetails.exception?.description ?? result.exceptionDetails.text ?? "page evaluation failed",
      );
    }
    return result.result?.value;
  }

  async screenshot(path) {
    const result = await this.send("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: false,
    });
    const bytes = Buffer.from(result.data, "base64");
    await writeFile(path, bytes);
    return bytes;
  }

  async screenshotBytes() {
    const result = await this.send("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: false,
    });
    return Buffer.from(result.data, "base64");
  }

  async close() {
    try {
      await fetch(`${this.endpoint}/json/close/${this.targetId}`, { method: "GET" });
    } catch {
      // The browser process owns final cleanup.
    }
    this.webSocket.close();
  }
}

function normalizeLabel(value) {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}

function hasLabel(values, expected) {
  const needle = normalizeLabel(expected);
  return values.some((value) => normalizeLabel(value).includes(needle));
}

function assertLabels(values, expectedValues, context) {
  const missing = expectedValues.filter((expected) => !hasLabel(values, expected));
  assert.deepEqual(missing, [], `${context} missing accessible controls: ${missing.join(", ")}`);
}

async function assertTimelineAccessibility(page, context) {
  await page.send("Accessibility.enable");
  const tree = await page.send("Accessibility.getFullAXTree");
  const buttonNames = tree.nodes
    .filter((node) => node.role?.value === "button" && !node.ignored)
    .map((node) => node.name?.value ?? "")
    .filter(Boolean);
  assertLabels(buttonNames, TIMELINE_CONTROLS, context);
}

async function collectDomAudit(page) {
  return page.evaluate(`(() => {
    const visible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" &&
        Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
    };
    const accessibleName = (element) =>
      element.getAttribute("aria-label") ||
      element.getAttribute("title") ||
      element.labels?.[0]?.textContent ||
      element.textContent ||
      "";
    const controls = Array.from(document.querySelectorAll(
      "button, input, select, [role=button], [role=tab], [role=switch]",
    )).filter(visible);
    const focusableWithoutName = controls
      .filter((element) => !accessibleName(element).trim())
      .map((element) => element.outerHTML.slice(0, 160));
    const fallbackSelectors = [
      "[data-testid=webgl-fallback]",
      "[data-webgl-fallback]",
      ".webgl-fallback",
      "[role=img][aria-label*=\\"system diagram\\" i]",
    ];
    const fallback = fallbackSelectors
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .find(visible);
    const bodyText = document.body?.innerText || "";
    return {
      bodyText,
      controlNames: controls.map((element) => accessibleName(element).trim()).filter(Boolean),
      focusableWithoutName,
      hasCanvas: Boolean(Array.from(document.querySelectorAll("canvas")).find(visible)),
      hasFallback: Boolean(fallback),
      hasErrorOverlay: Boolean(document.querySelector(
        "[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay",
      )),
      hasHorizontalOverflow:
        Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0) >
        window.innerWidth + 1,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      title: document.title,
      landmarks: {
        main: document.querySelectorAll("main").length,
        nav: document.querySelectorAll("nav, [role=navigation]").length,
      },
      partsListVisible: Boolean(Array.from(document.querySelectorAll("ul, ol, [role=list]"))
        .find((element) => visible(element) && /part|component/i.test(
          accessibleName(element) || element.closest('details')?.querySelector('summary')?.textContent || '',
        ))),
    };
  })()`);
}

async function waitForDemoRenderer(page, timeoutMs = 8_000) {
  const deadline = Date.now() + timeoutMs;
  let state;
  while (Date.now() < deadline) {
    state = await page.evaluate(`(() => ({
      canvas: Boolean(document.querySelector('canvas')),
      fallback: Boolean(document.querySelector('[data-testid=webgl-fallback]')),
      loading: document.body?.innerText?.includes('Checking WebGL') ||
        document.body?.innerText?.includes('Preparing conceptual Edge Gateway model'),
    }))()`);
    if ((state.canvas || state.fallback) && !state.loading) return state;
    await delay(100);
  }
  throw new Error(`demo renderer did not settle: ${JSON.stringify(state)}`);
}

function assertNoBrowserErrors(page, context) {
  // The recovered app has no cleared favicon. Chrome requests /favicon.ico on
  // its own and the unchanged normal route returns 404; do not turn that known
  // branding-asset absence into a demo runtime failure. Every other log error
  // remains fatal.
  const unexpectedLogErrors = page.logErrors.filter((entry) => {
    try {
      return new URL(entry.url).pathname !== "/favicon.ico";
    } catch {
      return true;
    }
  });
  assert.deepEqual(page.consoleErrors, [], `${context} console errors:\n${page.consoleErrors.join("\n")}`);
  assert.deepEqual(page.pageErrors, [], `${context} page errors:\n${page.pageErrors.join("\n")}`);
  assert.deepEqual(
    unexpectedLogErrors,
    [],
    `${context} browser log errors:\n${unexpectedLogErrors.map((entry) => `${entry.text} ${entry.url}`).join("\n")}`,
  );
}

function assertNetworkBoundary(page, baseUrl, context) {
  const origin = new URL(baseUrl).origin;
  const external = page.requests
    .map((request) => request.url)
    .filter(Boolean)
    .filter((url) => /^(?:https?|wss?):/.test(url))
    .filter((url) => new URL(url).origin !== origin);
  assert.deepEqual([...new Set(external)], [], `${context} made external requests`);
  const documents = page.responses.filter((response) => response.type === "Document");
  assert.ok(documents.length >= 1, `${context} had no document response`);
  assert.ok(documents.every((response) => response.status >= 200 && response.status < 400), `${context} document failed`);
}

function assertDemoAudit(audit, context, { requireCanvas = true } = {}) {
  assert.ok(audit.bodyText.trim().length > 200, `${context} rendered insufficient content`);
  assert.equal(audit.hasErrorOverlay, false, `${context} showed a framework error overlay`);
  assert.equal(audit.hasHorizontalOverflow, false, `${context} has horizontal overflow`);
  assert.ok(audit.landmarks.main >= 1, `${context} has no main landmark`);
  assert.ok(audit.landmarks.nav >= 1, `${context} has no navigation landmark`);
  assert.deepEqual(audit.focusableWithoutName, [], `${context} has unnamed interactive controls`);
  assert.match(audit.bodyText, /CONCEPTUAL SYSTEM VISUALIZATION\s*[—-]\s*NOT FOR FABRICATION/i);
  assertLabels(audit.controlNames, SCENES, context);
  assertLabels(audit.controlNames, PRIMARY_CONTROLS, context);
  if (requireCanvas) assert.equal(audit.hasCanvas, true, `${context} has no visible WebGL canvas`);
}

async function assertBoundingRectVisible(page, target, context, expectedText) {
  const locator = typeof target === "string" ? { selector: target, requiredTexts: [] } : target;
  const result = await page.evaluate(`(() => {
    const locator = ${JSON.stringify(locator)};
    const root = document.querySelector(locator.selector);
    const normalized = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const requiredTexts = locator.requiredTexts.map(normalized);
    const candidates = root ? [root, ...root.querySelectorAll('*')] : [];
    const element = requiredTexts.length === 0
      ? root
      : candidates
        .filter((candidate) => requiredTexts.every((text) => normalized(candidate.textContent).includes(text)))
        .sort((left, right) => (left.textContent?.length || 0) - (right.textContent?.length || 0))[0];
    if (!element) return { found: false };
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    let intersection = {
      left: Math.max(0, rect.left),
      top: Math.max(0, rect.top),
      right: Math.min(innerWidth, rect.right),
      bottom: Math.min(innerHeight, rect.bottom),
    };
    for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
      const ancestorStyle = getComputedStyle(ancestor);
      const ancestorRect = ancestor.getBoundingClientRect();
      if (/(?:auto|clip|hidden|scroll)/.test(ancestorStyle.overflowX)) {
        intersection.left = Math.max(intersection.left, ancestorRect.left);
        intersection.right = Math.min(intersection.right, ancestorRect.right);
      }
      if (/(?:auto|clip|hidden|scroll)/.test(ancestorStyle.overflowY)) {
        intersection.top = Math.max(intersection.top, ancestorRect.top);
        intersection.bottom = Math.min(intersection.bottom, ancestorRect.bottom);
      }
    }
    const width = Math.max(0, intersection.right - intersection.left);
    const height = Math.max(0, intersection.bottom - intersection.top);
    const area = rect.width * rect.height;
    return {
      found: true,
      text: element.textContent?.replace(/\\s+/g, ' ').trim() || '',
      rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height },
      viewport: { width: innerWidth, height: innerHeight },
      display: style.display,
      visibility: style.visibility,
      opacity: Number(style.opacity),
      visibleAreaRatio: area > 0 ? (width * height) / area : 0,
    };
  })()`);
  const detail = JSON.stringify(result);
  assert.equal(result.found, true, `${context} was not found (${JSON.stringify(locator)})`);
  assert.ok(result.rect.width > 0 && result.rect.height > 0, `${context} has no bounding area: ${detail}`);
  assert.notEqual(result.display, "none", `${context} is display:none: ${detail}`);
  assert.notEqual(result.visibility, "hidden", `${context} is visibility:hidden: ${detail}`);
  assert.ok(result.opacity > 0, `${context} is transparent: ${detail}`);
  assert.ok(result.rect.left >= -0.5 && result.rect.top >= -0.5, `${context} begins outside the viewport: ${detail}`);
  assert.ok(
    result.rect.right <= result.viewport.width + 0.5 && result.rect.bottom <= result.viewport.height + 0.5,
    `${context} extends outside the viewport: ${detail}`,
  );
  assert.ok(result.visibleAreaRatio >= 0.999, `${context} is clipped by an ancestor: ${detail}`);
  if (expectedText) assert.match(result.text, expectedText, `${context} rendered the wrong evidence state`);
  return result;
}

function actionExpression(labels, actionBody) {
  return `(() => {
    const labels = ${JSON.stringify(labels.map(normalizeLabel))};
    const visible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const name = (element) => (
      element.getAttribute("aria-label") || element.getAttribute("title") ||
      element.labels?.[0]?.textContent || element.textContent || ""
    ).toLowerCase().replace(/\\s+/g, " ").trim();
    const element = Array.from(document.querySelectorAll(
      "button, input, select, [role=button], [role=tab], [role=switch]",
    )).find((candidate) => visible(candidate) && labels.some((label) => name(candidate).includes(label)));
    if (!element) return { found: false, available: Array.from(document.querySelectorAll("button, input, select"))
      .filter(visible).map(name).filter(Boolean) };
    const disabled = element.matches(':disabled') || element.getAttribute('aria-disabled') === 'true';
    if (disabled) return { found: true, disabled, name: name(element) };
    ${actionBody}
    return { found: true, disabled, name: name(element) };
  })()`;
}

async function clickControl(page, labels, context) {
  const result = await page.evaluate(actionExpression(labels, "element.click();"));
  assert.equal(result.found, true, `${context}: none of ${labels.join(" / ")} found; available: ${result.available?.join(", ")}`);
  assert.equal(result.disabled, false, `${context}: ${result.name} is disabled`);
  await delay(250);
  assertNoBrowserErrors(page, context);
  return result;
}

async function clickControlIfPresent(page, labels, context) {
  const result = await page.evaluate(actionExpression(labels, "element.click();"));
  if (!result.found) return false;
  assert.equal(result.disabled, false, `${context}: ${result.name} is disabled`);
  await delay(250);
  assertNoBrowserErrors(page, context);
  return true;
}

async function setRange(page, labels, value, context) {
  const result = await page.evaluate(`(() => {
    const labels = ${JSON.stringify(labels.map(normalizeLabel))};
    const inputs = Array.from(document.querySelectorAll('input[type="range"]'));
    const name = (element) => (
      element.getAttribute('aria-label') || element.labels?.[0]?.textContent || ''
    ).toLowerCase().replace(/\\s+/g, ' ').trim();
    const element = inputs.find((candidate) => labels.some((label) => name(candidate).includes(label)));
    if (!element) return { found: false, available: inputs.map(name) };
    const disabled = element.disabled || element.getAttribute('aria-disabled') === 'true';
    if (disabled) return { found: true, disabled, name: name(element) };
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(
      element,
      ${JSON.stringify(String(value))},
    );
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
    return { found: true, disabled, name: name(element) };
  })()`);
  assert.equal(result.found, true, `${context}: explode range not found`);
  assert.equal(result.disabled, false, `${context}: explode range is disabled`);
  await delay(250);
}

async function capture(page, outputDirectory, filename) {
  const path = join(outputDirectory, filename);
  await page.screenshot(path);
  return path;
}

async function presentationState(page) {
  return page.evaluate(`(() => {
    const selected = Array.from(document.querySelectorAll('[aria-selected=true], [aria-current=step], [data-active=true]'))
      .map((element) => element.getAttribute('aria-label') || element.textContent || '')
      .join('|');
    const progress = Array.from(document.querySelectorAll('progress, [role=progressbar], input[type=range]'))
      .map((element) => element.getAttribute('aria-valuenow') || element.value || element.getAttribute('value') || '')
      .join('|');
    const timeline = document.querySelector(
      '[data-presentation-time], [data-presentation-progress], [class*=timelineProgress]',
    );
    const timecode = Array.from(document.querySelectorAll('strong'))
      .map((element) => element.textContent?.trim() || '')
      .find((text) => /^\\d{2}:\\d{2}$/.test(text)) || '';
    return {
      selected,
      progress,
      timeline: timeline?.textContent || timeline?.getAttribute('data-presentation-progress') || timeline?.style?.width || '',
      timecode,
    };
  })()`);
}

async function collectPerformance(page) {
  const [metrics, browserTiming] = await Promise.all([
    page.send("Performance.getMetrics"),
    page.evaluate(`(() => {
      const navigation = performance.getEntriesByType('navigation')[0];
      const resources = performance.getEntriesByType('resource');
      const scripts = resources.filter((entry) => entry.initiatorType === 'script');
      const longTasks = performance.getEntriesByType('longtask');
      return {
        navigation: navigation ? {
          domContentLoadedMs: navigation.domContentLoadedEventEnd,
          loadEventMs: navigation.loadEventEnd,
          responseEndMs: navigation.responseEnd,
          transferSize: navigation.transferSize,
          encodedBodySize: navigation.encodedBodySize,
        } : null,
        resources: resources.length,
        scripts: scripts.length,
        scriptTransferBytes: scripts.reduce((sum, entry) => sum + (entry.transferSize || 0), 0),
        scriptEncodedBytes: scripts.reduce((sum, entry) => sum + (entry.encodedBodySize || 0), 0),
        scriptDecodedBytes: scripts.reduce((sum, entry) => sum + (entry.decodedBodySize || 0), 0),
        longTaskCount: longTasks.length,
        longTaskDurationMs: longTasks.reduce((sum, entry) => sum + entry.duration, 0),
        canvases: document.querySelectorAll('canvas').length,
        devicePixelRatio: window.devicePixelRatio,
      };
    })()`),
  ]);
  return {
    ...browserTiming,
    cdp: Object.fromEntries((metrics.metrics ?? []).map((metric) => [metric.name, metric.value])),
  };
}

async function exercisePointerInputs(page) {
  const canvas = await page.evaluate(`(() => {
    const element = document.querySelector('canvas');
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  })()`);
  assert.ok(canvas && canvas.width > 40 && canvas.height > 40, "3D canvas is not interactable");
  const x = canvas.x + canvas.width / 2;
  const y = canvas.y + canvas.height / 2;
  const before = createHash("sha256").update(await page.screenshotBytes()).digest("hex");
  await page.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 });
  await page.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x + 90, y: y + 35, button: "left", buttons: 1 });
  await page.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: x + 90, y: y + 35, button: "left", buttons: 0, clickCount: 1 });
  await delay(250);
  const after = createHash("sha256").update(await page.screenshotBytes()).digest("hex");
  assert.notEqual(after, before, "mouse orbit produced no visible change");

  await page.send("Input.dispatchMouseEvent", { type: "mouseWheel", x, y, deltaX: 0, deltaY: -140 });
  await page.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "right", buttons: 2, clickCount: 1 });
  await page.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x + 45, y: y + 20, button: "right", buttons: 2 });
  await page.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: x + 45, y: y + 20, button: "right", buttons: 0, clickCount: 1 });

  await page.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  await page.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y, radiusX: 4, radiusY: 4, force: 1, id: 1 }] });
  await page.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x + 55, y: y + 25, radiusX: 4, radiusY: 4, force: 1, id: 1 }] });
  await page.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await page.send("Emulation.setTouchEmulationEnabled", { enabled: false });

  await page.send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 });
  await page.send("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 });
  assertNoBrowserErrors(page, "pointer, touch, and keyboard input");
}

async function verifyResponsiveRoute(endpoint, baseUrl, outputDirectory, report) {
  const page = await DevToolsPage.create(endpoint);
  try {
    for (const viewport of VIEWPORTS) {
      await page.setViewport(viewport);
      await page.navigate(`${baseUrl}${DEMO_PATH}`);
      await waitForDemoRenderer(page);
      const context = `${viewport.width}x${viewport.height}`;
      const audit = await collectDomAudit(page);
      assert.deepEqual(audit.viewport, viewport, `${context} viewport override drifted`);
      assertDemoAudit(audit, context);
      if ([680, 390, 375].includes(viewport.width)) {
        await page.evaluate(`document.querySelector('section[aria-label="Presentation timeline"]')?.scrollIntoView({ block: "end" })`);
        await assertTimelineAccessibility(page, `${context} accessibility tree`);
        await assertBoundingRectVisible(
          page,
          'button[aria-label="Installed Gateway overview"]',
          `${context} first presentation timeline control`,
        );
        await assertBoundingRectVisible(
          page,
          'button[aria-label="An updatable on-site operating layer for autonomous golf facilities"]',
          `${context} final presentation timeline control`,
        );
        await page.evaluate("scrollTo(0, 0)");
      }
      assertNetworkBoundary(page, baseUrl, context);
      assertNoBrowserErrors(page, context);
      await capture(page, outputDirectory, `responsive-${viewport.width}x${viewport.height}.png`);
      report.viewports.push({ ...viewport, overflow: audit.hasHorizontalOverflow, controls: audit.controlNames.length });
    }
  } finally {
    await page.close();
  }
}

async function verifyBaselineCodeSplit(endpoint, baseUrl, report) {
  const page = await DevToolsPage.create(endpoint);
  try {
    await page.setViewport({ width: 1440, height: 900 });
    await page.navigate(`${baseUrl}/`);
    const audit = await collectDomAudit(page);
    assert.match(audit.bodyText, /Operational replay/i);
    assert.equal(audit.hasCanvas, false, "normal Operational Replay route rendered a 3D canvas");
    assert.equal(/Installed System|Exploded Gateway/.test(audit.bodyText), false, "normal route rendered demo UI");
    const demoRouteRequests = page.requests
      .map((request) => request.url)
      .filter((url) => /edge-gateway-demo/i.test(url ?? ""));
    assert.deepEqual(demoRouteRequests, [], "normal route requested the Edge Gateway route bundle");
    assertNetworkBoundary(page, baseUrl, "normal Operational Replay route");
    assertNoBrowserErrors(page, "normal Operational Replay route");
    report.baseline = { requests: page.requests.length, resources: page.responses.length };
  } finally {
    await page.close();
  }
}

async function verifyInteractiveStory(endpoint, baseUrl, outputDirectory, report) {
  const page = await DevToolsPage.create(endpoint);
  try {
    await page.setViewport({ width: 1440, height: 900 });
    await page.navigate(`${baseUrl}${DEMO_PATH}`);
    await waitForDemoRenderer(page);
    await assertBoundingRectVisible(
      page,
      'section[aria-label="Presentation timeline"]',
      "1440x900 presentation timeline",
      /Installed gateway overview.*independent local safety path/i,
    );
    await assertBoundingRectVisible(
      page,
      "footer",
      "1440x900 release footer",
      /SIMULATED PILOT SCENARIO.*NO MANUFACTURING.*CERTIFICATION.*DEPLOYMENT CLAIM/i,
    );
    await assertBoundingRectVisible(
      page,
      'button[data-interface="ground-bond"]',
      "1440x900 installed protective-earth bond label",
      /PE bond/i,
    );
    await assertBoundingRectVisible(
      page,
      'button[data-interface="existing-washer"]',
      "1440x900 Existing Washer interface button",
      /^Existing Washer$/i,
    );
    await clickControl(page, ["Existing Washer"], "installed Existing Washer interface");
    const interfaceText = (await collectDomAudit(page)).bodyText;
    assert.match(interfaceText, /Not a surveyed fact/i, "Existing Washer inspector omitted commissioning status");
    assert.match(interfaceText, /Control\s+Unavailable/i, "Existing Washer inspector omitted control status");
    await clickControl(page, ["Close installation interface inspector"], "close Existing Washer inspector");
    const installationStatuses = await page.evaluate(`Array.from(document.querySelectorAll('[class*=statusList] li')).map((row) => ({
      label: row.querySelector('span')?.textContent?.trim() || '',
      status: row.getAttribute('data-status') || '',
      dotColor: getComputedStyle(row.querySelector('i')).backgroundColor,
    }))`);
    const installationStatusByLabel = Object.fromEntries(
      installationStatuses.map((status) => [status.label, status]),
    );
    assert.equal(installationStatusByLabel["Agent Runtime V1"]?.status, "implemented");
    assert.equal(installationStatusByLabel["Power + normal I/O"]?.status, "conceptual");
    assert.equal(installationStatusByLabel["Cloud sync"]?.status, "unimplemented");
    assert.notEqual(
      installationStatusByLabel["Power + normal I/O"]?.dotColor,
      installationStatusByLabel["Agent Runtime V1"]?.dotColor,
      "conceptual installation row used the implemented green status signal",
    );
    assert.notEqual(
      installationStatusByLabel["Cloud sync"]?.dotColor,
      installationStatusByLabel["Agent Runtime V1"]?.dotColor,
      "unimplemented cloud sync row used the implemented green status signal",
    );
    await capture(page, outputDirectory, "01-installed-gateway.png");
    await exercisePointerInputs(page);
    for (const camera of ["Perspective", "Orthographic", "Front", "Side", "Top", "Isometric", "Reset Camera"]) {
      await clickControl(page, [camera], `camera control ${camera}`);
    }
    await clickControl(page, ["Perspective"], "restore perspective projection");
    await clickControl(page, ["Reset Camera"], "restore installed camera");
    for (const layer of ["Show power", "Show network", "Show telemetry", "Show safety"]) {
      await clickControl(page, [layer], `layer control ${layer}`);
      await clickControl(page, [layer], `restore layer ${layer}`);
    }
    await clickControlIfPresent(page, ["Close component inspector"], "show scene controls");
    for (const control of ["Transparent enclosure", "Cutaway", "Show dimensions", "Show labels"]) {
      await clickControl(page, [control], `toggle ${control}`);
      await clickControl(page, [control], `restore ${control}`);
    }
    await clickControl(page, ["Open enclosure"], "open enclosure");
    await capture(page, outputDirectory, "02-open-enclosure.png");
    await clickControl(page, ["Exploded Gateway"], "exploded scene");
    await setRange(page, ["Explode"], 100, "exploded scene");
    await capture(page, outputDirectory, "03-exploded-view.png");
    await clickControl(page, ["Fanless Edge Computer"], "select Edge Computer");
    await capture(page, outputDirectory, "04-selected-edge-computer.png");
    await clickControl(page, ["Operational Flow"], "operational flow scene");
    await assertBoundingRectVisible(
      page,
      '[aria-label="Operational flow 3D site labels"]',
      "1440x900 operational-flow site labels",
      /Dispenser sensor.*Washer.*Picker R1.*Picker R2.*Carrier C1.*Universal Handoff H1.*NXTektal Cloud.*Manager tablet/is,
    );
    await capture(page, outputDirectory, "05-operational-data-flow.png");
    const scenarioText = (await collectDomAudit(page)).bodyText;
    assert.match(scenarioText, /SIMULATED PILOT SCENARIO\s*[—-]\s*NOT LIVE CUSTOMER DATA/i);
    assert.match(scenarioText, /ILLUSTRATIVE STORYBOARD.*NOT AGENT RUNTIME FIXTURE OUTPUT/is);
    assert.match(
      scenarioText,
      /IMPLEMENTED\s*[·-]\s*SYNTHETIC \/ FIXTURE INPUTS.*Site Runtime validates input.*telemetry-owned assembly.*quality-gates the exact state\/report envelope.*Agent Runtime invokes Shadow Ops evaluation.*separate lifecycle evidence.*manager workflow record/is,
    );
    assert.match(
      scenarioText,
      /Checkpoint\/recovery and read-only runtime status exist.*Physical telemetry adapters.*physical command admission.*robot execution.*safety installation remain unimplemented/is,
    );
    assert.equal(
      await page.evaluate(`Boolean(document.querySelector('[data-evidence-card="rangeops-replay"]'))`),
      false,
      "separate RangeOps replay evidence appeared before the manager workflow step",
    );
    await clickControl(page, ["Record manager response"], "manager response recording");
    await capture(page, outputDirectory, "06-manager-response-no-command.png");
    const managerResponseText = (await collectDomAudit(page)).bodyText;
    assert.match(managerResponseText, /ACCEPT recorded.*no command/is);
    assert.equal(
      await page.evaluate(`Boolean(document.querySelector('[data-evidence-card="rangeops-replay"]'))`),
      false,
      "recording manager acceptance also revealed the separate replay evidence",
    );
    await clickControl(page, ["Next step"], "separate replay step");
    await capture(page, outputDirectory, "07-advisory-stop-and-separate-replay.png");
    assert.match((await collectDomAudit(page)).bodyText, /SEPARATE RANGEOPS REPLAY.*SafetyShield.*not caused by any manager response/is);
    await clickControl(page, ["Scale the Fleet"], "fleet scene");
    await clickControlIfPresent(page, ["Close component inspector"], "show fleet controls");
    await clickControl(page, ["Add Picker"], "add Picker");
    await assertBoundingRectVisible(
      page,
      '[data-evidence-card="fleet"]',
      "1440x900 fleet evidence card",
      /SAME GATEWAY.*1 DEVICES.*Concept Picker 01.*certificate enrollment.*capability assignment.*Adapter loading.*physical device onboarding/is,
    );
    await capture(page, outputDirectory, "08-fleet-expansion.png");
    const fleetText = (await collectDomAudit(page)).bodyText;
    assert.match(fleetText, /Same Gateway\s*[—-]\s*new device registration and Adapter/i);
    assert.match(fleetText, /certificate enrollment.*capability assignment.*Adapter loading.*physical device onboarding/is);
    assert.match(fleetText, /collect.*navigate.*report_payload.*report_battery/is);
    await clickControl(page, ["Software Update"], "software update scene");
    await clickControl(page, ["Run update"], "software update start");
    await assertBoundingRectVisible(
      page,
      '[data-evidence-card="update"]',
      "1440x900 successful-update evidence card",
      /CONCEPTUAL OTA SEQUENCE.*HEALTHY.*0\.3\.2.*production update delivery is not implemented/is,
    );
    await capture(page, outputDirectory, "09-ota-update.png");
    await clickControl(page, ["Simulate Failed Health Check"], "failed health check");
    await assertBoundingRectVisible(
      page,
      '[data-evidence-card="update"]',
      "1440x900 rollback evidence card",
      /ROLLBACK COMPLETE.*0\.3\.2.*Failed health check.*retained version 0\.3\.2 restored.*rollback report recorded/is,
    );
    await capture(page, outputDirectory, "10-rollback.png");
    assert.match((await collectDomAudit(page)).bodyText, /rollback|retained version .* restored/i);
    await clickControl(page, ["Safety Architecture"], "safety scene");
    await capture(page, outputDirectory, "11-safety-architecture.png");
    const safetyText = (await collectDomAudit(page)).bodyText;
    assert.match(safetyText, /The Agent cannot bypass local safety/i);
    assert.match(safetyText, /Emergency Stop/i);
    assert.match(safetyText, /manager workflow record.*STOP.*Future physical admission.*NOT IMPLEMENTED/is);
    assert.match(safetyText, /No installed or certified integration/i);
    assertNoBrowserErrors(page, "interactive story");
    report.performance = await collectPerformance(page);
  } finally {
    await page.close();
  }
}

async function verifyPresentation(endpoint, baseUrl, report) {
  const page = await DevToolsPage.create(endpoint);
  try {
    await page.setViewport({ width: 1440, height: 900 });
    await page.navigate(`${baseUrl}${DEMO_PATH}?presentation=1`);
    await waitForDemoRenderer(page);
    const audit = await collectDomAudit(page);
    assertDemoAudit(audit, "presentation mode");
    assertLabels(
      audit.controlNames,
      ["Pause presentation", "Restart presentation", "Next step", "Previous step"],
      "presentation mode",
    );
    const before = await presentationState(page);
    await delay(1_500);
    const advancing = await presentationState(page);
    assert.notDeepEqual(advancing, before, "automatic presentation did not advance");
    await clickControl(page, ["Pause presentation"], "pause presentation");
    const paused = await presentationState(page);
    await delay(1_000);
    const stillPaused = await presentationState(page);
    assert.deepEqual(stillPaused, paused, "presentation advanced while paused");
    await clickControl(page, ["Restart presentation"], "restart presentation");
    await clickControl(page, ["Next step"], "manual presentation step");
    const stepped = await presentationState(page);
    assert.notDeepEqual(stepped, paused, "manual presentation step did not change state");

    await clickControl(
      page,
      ["Record manager workflow evidence; issue no command"],
      "manager workflow presentation cue",
    );
    const managerCueText = (await collectDomAudit(page)).bodyText;
    assert.match(managerCueText, /ACCEPT recorded.*no command/is);
    assert.equal(
      await page.evaluate(`Boolean(document.querySelector('[data-evidence-card="rangeops-replay"]'))`),
      false,
      "manager presentation cue also revealed the separate RangeOps replay",
    );
    await clickControl(
      page,
      ["Show separate RangeOps replay; do not infer causality"],
      "separate RangeOps presentation cue",
    );
    assert.equal(
      await page.evaluate(`Boolean(document.querySelector('[data-evidence-card="rangeops-replay"]'))`),
      true,
      "separate RangeOps presentation cue did not reveal replay evidence",
    );
    assert.match(
      (await collectDomAudit(page)).bodyText,
      /SEPARATE RANGEOPS REPLAY.*not caused by any manager response/is,
    );

    await clickControl(page, ["Restart presentation"], "reset presentation before direct replay jump");
    await clickControl(
      page,
      ["Show separate RangeOps replay; do not infer causality"],
      "direct separate RangeOps presentation jump",
    );
    const directJump = await page.evaluate(`(() => {
      const managerStep = document.querySelector('[data-flow-step="06"]');
      return {
        managerState: managerStep?.getAttribute('data-flow-state') || '',
        replayVisible: Boolean(document.querySelector('[data-evidence-card="rangeops-replay"]')),
        bodyText: document.body.innerText,
      };
    })()`);
    assert.equal(directJump.managerState, "pending", "direct replay jump painted an unrecorded manager response complete");
    assert.equal(directJump.replayVisible, true, "direct replay jump omitted independent evidence");
    assert.match(directJump.bodyText, /Record response\s*[·-]\s*ACCEPT/i);
    assert.match(directJump.bodyText, /not caused by any manager response/i);

    await clickControl(page, ["Play presentation"], "resume presentation before tab freeze");

    let lifecycleSupported = true;
    try {
      await page.send("Page.setWebLifecycleState", { state: "frozen" });
      const frozen = await presentationState(page);
      await delay(1_000);
      const stillFrozen = await presentationState(page);
      assert.deepEqual(stillFrozen, frozen, "presentation advanced while tab was frozen");
      await page.send("Page.setWebLifecycleState", { state: "active" });
    } catch (error) {
      lifecycleSupported = false;
      throw new Error(`tab hide/resume verification unavailable: ${error.message}`);
    }
    assertNetworkBoundary(page, baseUrl, "presentation mode");
    assertNoBrowserErrors(page, "presentation mode");
    report.presentation = {
      automaticAdvance: true,
      pause: true,
      restart: true,
      manualStep: true,
      managerCueNoReplay: true,
      separateReplayCue: true,
      directReplayJumpNoncausal: true,
      lifecycleSupported,
    };
  } finally {
    await page.close();
  }
}

async function verifyReducedMotion(endpoint, baseUrl, report) {
  const page = await DevToolsPage.create(endpoint);
  try {
    await page.send("Emulation.setEmulatedMedia", {
      features: [{ name: "prefers-reduced-motion", value: "reduce" }],
    });
    await page.setViewport({ width: 1024, height: 768 });
    await page.navigate(`${baseUrl}${DEMO_PATH}?presentation=1`);
    await waitForDemoRenderer(page);
    const result = await page.evaluate(`(() => {
      const names = Array.from(document.querySelectorAll('button, input, [role=switch]')).map((element) =>
        element.getAttribute('aria-label') || element.textContent || '');
      return {
        mediaMatches: matchMedia('(prefers-reduced-motion: reduce)').matches,
        hasControl: names.some((name) => /reduced motion/i.test(name)),
      };
    })()`);
    assert.equal(result.mediaMatches, true, "browser did not emulate reduced motion");
    assert.equal(result.hasControl, true, "demo exposes no accessible reduced-motion control");
    assertNetworkBoundary(page, baseUrl, "reduced-motion mode");
    assertNoBrowserErrors(page, "reduced-motion mode");
    report.reducedMotion = result;
  } finally {
    await page.close();
  }
}

async function verifyWebglFallback(browserPath, baseUrl, outputDirectory, report) {
  const browser = await startBrowser(browserPath, [
    "--disable-webgl",
    "--disable-gpu",
    "--disable-software-rasterizer",
  ]);
  try {
    const page = await DevToolsPage.create(browser.endpoint);
    try {
      await page.setViewport({ width: 390, height: 844 });
      await page.navigate(`${baseUrl}${DEMO_PATH}`);
      await waitForDemoRenderer(page);
      const webglAvailable = await page.evaluate(`Boolean(
        document.createElement('canvas').getContext('webgl2') ||
        document.createElement('canvas').getContext('webgl')
      )`);
      assert.equal(webglAvailable, false, "WebGL-disable launch arguments did not take effect");
      const audit = await collectDomAudit(page);
      assertDemoAudit(audit, "WebGL fallback", { requireCanvas: false });
      assert.equal(audit.hasFallback, true, "WebGL fallback system diagram is not visible");
      assert.equal(audit.partsListVisible, true, "WebGL fallback parts list is not visible or labeled");
      for (const component of ["Edge Computer", "LTE Router", "Remote I/O", "UPS", "Ethernet Switch"]) {
        assert.match(audit.bodyText, new RegExp(component.replace("/", "\\/"), "i"));
      }
      assert.match(audit.bodyText, /Edge Gateway system architecture/i);
      assert.match(audit.bodyText, /FacilityState.*AssemblyReport/is);
      assert.match(audit.bodyText, /Agent Runtime lifecycle evidence.*checkpoint \/ recovery.*read-only diagnostics/is);
      assert.match(audit.bodyText, /does not run or connect to the Python runtime/is);
      assert.match(audit.bodyText, /Physical telemetry adapters.*device enrollment.*production OTA.*physical command admission.*robot execution.*safety installation remain unimplemented/is);
      assert.match(audit.bodyText, /Manager acceptance does not cause the separate RangeOps replay/is);
      assert.match(audit.bodyText, /Manager.*no command issued/is);
      assertNetworkBoundary(page, baseUrl, "WebGL fallback");
      assertNoBrowserErrors(page, "WebGL fallback");
      await capture(page, outputDirectory, "12-mobile-fallback.png");
      report.webglFallback = { webglAvailable, visible: audit.hasFallback, partsList: audit.partsListVisible };
    } finally {
      await page.close();
    }
  } finally {
    await stopBrowser(browser);
  }
}

async function readBrowserVersion(browserPath) {
  return new Promise((resolveVersion) => {
    const child = spawn(browserPath, ["--version"], { stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { output += chunk; });
    child.stderr.on("data", (chunk) => { output += chunk; });
    child.once("close", () => resolveVersion(output.trim()));
    child.once("error", () => resolveVersion("unknown"));
  });
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const outputDirectory = await prepareOutputDirectory(options.outputDir);
  const browserPath = await findBrowser(options.browserPath);
  let server;
  let browser;
  const report = {
    schema: "nxtektal-edge-gateway-browser-verification/v1",
    baseUrl: undefined,
    browser: await readBrowserVersion(browserPath),
    browserPath,
    node: process.version,
    startedProductionServer: false,
    viewports: [],
  };

  try {
    let baseUrl;
    if (options.baseUrl) {
      const supplied = new URL(options.baseUrl);
      if (!["http:", "https:"].includes(supplied.protocol)) {
        throw new Error("--base-url must use http or https");
      }
      supplied.pathname = supplied.pathname.replace(/\/$/, "");
      supplied.search = "";
      supplied.hash = "";
      baseUrl = supplied.href.replace(/\/$/, "");
      await waitForHttp(`${baseUrl}/`);
    } else {
      server = await startProductionServer();
      baseUrl = server.baseUrl;
      report.startedProductionServer = true;
    }
    report.baseUrl = baseUrl;

    browser = await startBrowser(browserPath);
    await verifyBaselineCodeSplit(browser.endpoint, baseUrl, report);
    await verifyResponsiveRoute(browser.endpoint, baseUrl, outputDirectory, report);
    await verifyInteractiveStory(browser.endpoint, baseUrl, outputDirectory, report);
    await verifyPresentation(browser.endpoint, baseUrl, report);
    await verifyReducedMotion(browser.endpoint, baseUrl, report);
    await stopBrowser(browser);
    browser = undefined;
    await verifyWebglFallback(browserPath, baseUrl, outputDirectory, report);
    report.result = "passed";
    await writeFile(join(outputDirectory, "browser-verification.json"), `${JSON.stringify(report, null, 2)}\n`);
    process.stdout.write(`Edge Gateway browser verification passed. Evidence: ${outputDirectory}\n`);
  } catch (error) {
    report.result = "failed";
    report.error = error instanceof Error ? error.stack ?? error.message : String(error);
    await writeFile(join(outputDirectory, "browser-verification.json"), `${JSON.stringify(report, null, 2)}\n`).catch(() => {});
    throw error;
  } finally {
    await stopBrowser(browser);
    await stopProcess(server?.observed, SERVER_STOP_TIMEOUT_MS);
  }
}

await main();
