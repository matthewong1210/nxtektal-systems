import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const APP_ROOT = join(import.meta.dirname, "..");
const SOURCE_DIRS = ["app", "components", "lib"];

/** The console is a presentation leaf: it must never import a Python
 * Site OS package, the ROI engine, or the replay app, and it must not
 * contain any robot/actuator command vocabulary or hidden persistence. */
const FORBIDDEN_IMPORT = /(?:^|\/)nxt_|@nxtektal\/roi-engine|nxtektal-roi-engine|@nxtektal\/operational-replay/;

const FORBIDDEN_TOKENS = [
  "apply_directive",
  "RobotTaskInterface",
  "HandoffController",
  "SafetyShield",
  "send_robot_command",
  "dispatch_collector_command",
  "write_register",
  "write_coil",
  "rclpy",
  "rospy",
  "child_process",
  "localStorage",
  "sessionStorage",
  "indexedDB",
  "EventSource",
  "sendBeacon",
  "WebSocket",
];

function sourceFiles(): string[] {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const path = join(dir, entry);
      if (statSync(path).isDirectory()) {
        walk(path);
      } else if (/\.(ts|tsx|css)$/.test(entry)) {
        files.push(path);
      }
    }
  };
  for (const dir of SOURCE_DIRS) {
    walk(join(APP_ROOT, dir));
  }
  return files;
}

describe("console boundaries", () => {
  it("keeps the production dependency surface minimal and pinned", () => {
    const manifest = JSON.parse(
      readFileSync(join(APP_ROOT, "package.json"), "utf-8"),
    ) as { dependencies: Record<string, string> };
    expect(Object.keys(manifest.dependencies).sort()).toEqual([
      "next",
      "react",
      "react-dom",
    ]);
    for (const version of Object.values(manifest.dependencies)) {
      expect(version).toMatch(/^\d/);
    }
  });

  it("imports no Python package, ROI engine, or replay app", () => {
    for (const file of sourceFiles()) {
      const text = readFileSync(file, "utf-8");
      expect(FORBIDDEN_IMPORT.test(text), file).toBe(false);
    }
  });

  it("contains no execution vocabulary or hidden browser persistence", () => {
    for (const file of sourceFiles()) {
      const text = readFileSync(file, "utf-8");
      for (const token of FORBIDDEN_TOKENS) {
        expect(text.includes(token), `${file} mentions ${token}`).toBe(false);
      }
    }
  });

  it("talks only to the versioned same-origin manager API", () => {
    for (const file of sourceFiles()) {
      const text = readFileSync(file, "utf-8");
      const urls = text.match(/https?:\/\/[^\s"'`]+/g) ?? [];
      expect(urls, `${file} hardcodes a network URL: ${urls}`).toEqual([]);
      const apiPaths = text.match(/\/api\/v\d+[^\s"'`]*/g) ?? [];
      for (const path of apiPaths) {
        expect(
          path === "/api/v0" || path.startsWith("/api/v0/"),
          `${file} uses ${path}`,
        ).toBe(true);
      }
    }
  });

  it("declares the static-export output so no server runtime ships", () => {
    const config = readFileSync(join(APP_ROOT, "next.config.ts"), "utf-8");
    expect(config).toContain('output: "export"');
  });
});
