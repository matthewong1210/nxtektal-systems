#!/usr/bin/env node

import { createHash } from "node:crypto";
import {
  access,
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
} from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import { tmpdir } from "node:os";
import {
  delimiter,
  dirname,
  isAbsolute,
  join,
  resolve,
  sep,
} from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = resolve(SCRIPT_DIR, "..");
const DEFAULT_SOURCE = join(
  APP_ROOT,
  "assets",
  "social-preview",
  "operational-replay.svg",
);
const DEFAULT_OUTPUT = join(APP_ROOT, "public", "og.png");
const WIDTH = 1200;
const HEIGHT = 630;
const PNG_SIGNATURE = "89504e470d0a1a0a";
const CAPTURE_TIMEOUT_MS = 15_000;
const STOP_TIMEOUT_MS = 3_000;

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

function usage() {
  return `Usage:
  node scripts/generate-social-preview.mjs [--check]
  node scripts/generate-social-preview.mjs [--source <svg>] [--output <png>]
      [--browser-path <chrome>]

The source must be a self-contained 1200x630 SVG. The script disables browser
background networking, renders twice in isolated profiles, and accepts output
only when both PNG captures are byte-identical.
`;
}

function parseArguments(argv) {
  const options = {
    browserPath: undefined,
    check: false,
    output: DEFAULT_OUTPUT,
    source: DEFAULT_SOURCE,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help") {
      process.stdout.write(usage());
      process.exit(0);
    }
    if (argument === "--check") {
      options.check = true;
      continue;
    }
    if (!["--browser-path", "--output", "--source"].includes(argument)) {
      throw new Error(`unknown argument: ${argument}\n${usage()}`);
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`${argument} requires a value`);
    }
    index += 1;
    if (argument === "--browser-path") options.browserPath = value;
    if (argument === "--output") options.output = resolve(value);
    if (argument === "--source") options.source = resolve(value);
  }
  return options;
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

async function runProcess(command, arguments_) {
  const child = spawn(command, arguments_, {
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    output += chunk;
  });
  child.stderr.on("data", (chunk) => {
    output += chunk;
  });
  const status = await new Promise((resolveStatus, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolveStatus({ code, signal }));
  });
  if (status.code !== 0) {
    throw new Error(
      `${command} exited with code ${status.code ?? "null"} ` +
        `(signal ${status.signal ?? "none"})\n${output}`,
    );
  }
  return output.trim();
}

function validateSource(source) {
  if (!/<svg\b/i.test(source)) {
    throw new Error("social-preview source is not an SVG");
  }
  if (!/\bwidth="1200"/.test(source) || !/\bheight="630"/.test(source)) {
    throw new Error("social-preview source must declare width=1200 and height=630");
  }
  if (!/\bviewBox="0 0 1200 630"/.test(source)) {
    throw new Error("social-preview source must use viewBox 0 0 1200 630");
  }
  if (!/font-family="[^"]*system-ui/i.test(source)) {
    throw new Error("social-preview source must use the documented system-font stack");
  }
  const prohibited = [
    /<(?:image|foreignObject|script)\b/i,
    /\b(?:href|xlink:href)\s*=/i,
    /@(?:font-face|import)\b/i,
    /url\(\s*["']?(?!#)/i,
  ];
  for (const pattern of prohibited) {
    if (pattern.test(source)) {
      throw new Error(`social-preview source contains prohibited external content: ${pattern}`);
    }
  }
}

function inspectPng(bytes) {
  if (bytes.subarray(0, 8).toString("hex") !== PNG_SIGNATURE) {
    throw new Error("capture is not a PNG");
  }
  if (bytes.subarray(12, 16).toString("ascii") !== "IHDR") {
    throw new Error("capture does not begin with a PNG IHDR chunk");
  }
  const width = bytes.readUInt32BE(16);
  const height = bytes.readUInt32BE(20);
  if (width !== WIDTH || height !== HEIGHT) {
    throw new Error(`capture is ${width}x${height}; expected ${WIDTH}x${HEIGHT}`);
  }
  if (bytes.length < 20 || bytes.subarray(-8, -4).toString("ascii") !== "IEND") {
    throw new Error("capture is not a complete PNG");
  }
  return {
    height,
    mimeType: "image/png",
    sha256: createHash("sha256").update(bytes).digest("hex"),
    sizeBytes: bytes.length,
    width,
  };
}

async function capture(browserPath, sourcePath, outputPath) {
  const profileDirectory = await mkdtemp(
    join(tmpdir(), "nxtektal-social-preview-profile-"),
  );
  const arguments_ = [
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-sync",
    "--hide-scrollbars",
    "--metrics-recording-only",
    "--mute-audio",
    "--force-color-profile=srgb",
    "--force-device-scale-factor=1",
    "--font-render-hinting=none",
    "--host-resolver-rules=MAP * 0.0.0.0",
    "--run-all-compositor-stages-before-draw",
    `--user-data-dir=${profileDirectory}`,
    `--window-size=${WIDTH},${HEIGHT}`,
    `--screenshot=${outputPath}`,
    ...(process.getuid?.() === 0 ? ["--no-sandbox"] : []),
    pathToFileURL(sourcePath).href,
  ];
  const child = spawn(browserPath, arguments_, {
    detached: process.platform !== "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });
  let processOutput = "";
  let exitStatus;
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    processOutput = `${processOutput}${chunk}`.slice(-32 * 1024);
  });
  child.stderr.on("data", (chunk) => {
    processOutput = `${processOutput}${chunk}`.slice(-32 * 1024);
  });
  const exited = new Promise((resolveExit, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      exitStatus = { code, signal };
      resolveExit(exitStatus);
    });
  });
  const stop = async () => {
    if (exitStatus) return;
    const send = (signal) => {
      if (process.platform !== "win32" && Number.isInteger(child.pid)) {
        try {
          process.kill(-child.pid, signal);
          return;
        } catch (error) {
          if (error.code === "ESRCH") return;
          throw error;
        }
      }
      child.kill(signal);
    };
    send("SIGTERM");
    if (await Promise.race([exited.then(() => true), delay(STOP_TIMEOUT_MS).then(() => false)])) {
      return;
    }
    send("SIGKILL");
    if (!(await Promise.race([exited.then(() => true), delay(STOP_TIMEOUT_MS).then(() => false)]))) {
      throw new Error("Chrome did not stop after social-preview capture");
    }
  };
  let failure;
  try {
    const deadline = Date.now() + CAPTURE_TIMEOUT_MS;
    let captured = false;
    while (Date.now() < deadline) {
      try {
        inspectPng(await readFile(outputPath));
        captured = true;
        break;
      } catch (error) {
        if (exitStatus) {
          throw new Error(
            `Chrome exited before producing a complete capture ` +
              `(code ${exitStatus.code ?? "null"}, signal ${exitStatus.signal ?? "none"})\n` +
              `${processOutput}\n${error.message}`,
          );
        }
      }
      await delay(50);
    }
    if (!captured) {
      throw new Error(
        `Chrome did not produce a complete capture within ${CAPTURE_TIMEOUT_MS}ms\n${processOutput}`,
      );
    }
  } catch (error) {
    failure = error;
  } finally {
    try {
      await stop();
    } catch (stopError) {
      failure = failure
        ? new AggregateError([failure, stopError], "capture and cleanup failed")
        : stopError;
    }
    await rm(profileDirectory, { recursive: true, force: true });
  }
  if (failure) throw failure;
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const sourceBytes = await readFile(options.source);
  validateSource(sourceBytes.toString("utf8"));

  const browserPath = await findBrowser(options.browserPath);
  const temporaryDirectory = await mkdtemp(
    join(tmpdir(), "nxtektal-social-preview-capture-"),
  );
  const firstPath = join(temporaryDirectory, "capture-a.png");
  const secondPath = join(temporaryDirectory, "capture-b.png");
  try {
    await capture(browserPath, options.source, firstPath);
    await capture(browserPath, options.source, secondPath);
    const [first, second] = await Promise.all([
      readFile(firstPath),
      readFile(secondPath),
    ]);
    const details = inspectPng(first);
    if (!first.equals(second)) {
      throw new Error("two isolated social-preview captures were not byte-identical");
    }

    if (options.check) {
      const committed = await readFile(options.output);
      if (!first.equals(committed)) {
        const committedDetails = inspectPng(committed);
        throw new Error(
          "generated preview does not match the committed PNG\n" +
            `generated sha256: ${details.sha256}\n` +
            `committed sha256: ${committedDetails.sha256}`,
        );
      }
    } else {
      await mkdir(dirname(options.output), { recursive: true });
      await copyFile(firstPath, options.output);
    }

    const browserVersion = await runProcess(browserPath, ["--version"]);
    const action = options.check ? "Verified" : "Generated";
    process.stdout.write(
      `${action} ${options.output}\n` +
        `${details.width}x${details.height} ${details.mimeType} ` +
        `${details.sizeBytes} bytes\n` +
        `SHA-256 ${details.sha256}\n` +
        `Source ${options.source}\n` +
        `Renderer ${browserVersion}; Node ${process.version}\n`,
    );
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}

await main();
