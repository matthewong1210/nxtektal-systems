import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, test } from "vitest";

const ROOT = process.cwd();
const PRODUCTION_ROOTS = ["app", "components", "lib"].map((directory) =>
  join(ROOT, directory),
);
const DEMO_PRODUCTION_ROOTS = [
  join(ROOT, "app", "edge-gateway-demo"),
  join(ROOT, "components", "edge-gateway-3d"),
  join(ROOT, "lib", "edge-gateway-model"),
];
const YC_DISPATCH_REPORT_ROOT = join(ROOT, "app", "yc-dispatch-report");
const FORBIDDEN_RUNTIME_IMPORT =
  /(?:^|\/)nxt_|@nxtektal\/roi-engine|nxtektal-roi-engine/;

function isForbiddenRuntimeImport(specifier: string): boolean {
  return FORBIDDEN_RUNTIME_IMPORT.test(specifier);
}

function filesUnder(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    return entry.isDirectory() ? filesUnder(path) : [path];
  });
}

describe("package and safety boundaries", () => {
  test("uses only the isolated web runtime dependency set", () => {
    const manifest = JSON.parse(
      readFileSync(join(ROOT, "package.json"), "utf8"),
    ) as { dependencies: Record<string, string> };

    expect(Object.keys(manifest.dependencies).sort()).toEqual([
      "@react-three/fiber",
      "next",
      "react",
      "react-dom",
      "three",
    ]);
    expect(manifest.dependencies).not.toHaveProperty("@react-three/drei");
  });

  test("production imports do not reach NXTektal runtimes or execution APIs", () => {
    const production = PRODUCTION_ROOTS.flatMap(filesUnder);
    for (const path of production) {
      const source = readFileSync(path, "utf8");
      const imports = Array.from(
        source.matchAll(/(?:from\s+|import\s*\()(["'])([^"']+)\1/g),
        (match) => match[2],
      );
      for (const specifier of imports) {
        expect(
          isForbiddenRuntimeImport(specifier),
          `${relative(ROOT, path)} imports ${specifier}`,
        ).toBe(false);
      }
      expect(source, relative(ROOT, path)).not.toMatch(
        /apply_directive|RobotTaskInterface|child_process|localStorage|indexedDB|EventSource|sendBeacon/,
      );
    }
  });

  test("keeps the demo browser-local with no external request or persistence surface", () => {
    for (const path of DEMO_PRODUCTION_ROOTS.flatMap(filesUnder)) {
      const source = readFileSync(path, "utf8");
      expect(source, relative(ROOT, path)).not.toMatch(
        /\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\s*\(|https?:\/\/|wss?:\/\//,
      );
    }
  });

  test("keeps the YC filming route presentation-only and configuration-owned", () => {
    const paths = filesUnder(YC_DISPATCH_REPORT_ROOT);
    const sources = paths.map((path) => readFileSync(path, "utf8"));
    const combined = sources.join("\n");

    expect(combined).not.toMatch(
      /\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\s*\(|\bEventSource\s*\(|\bsendBeacon\s*\(|https?:\/\/|wss?:\/\//,
    );
    expect(combined).not.toMatch(
      /@react-three\/fiber|(?:from\s+|import\s*\()["']three["']|ReplayStory|edge-gateway/i,
    );
    expect(combined).not.toMatch(
      /<(?:canvas|svg)\b|\brequestAnimationFrame\b|\b(?:map|route|telemetry|chart)\b/i,
    );
    expect(combined).not.toMatch(
      /No intervention required|Fully autonomous|Autonomous mission completed/i,
    );

    const configPath = join(
      YC_DISPATCH_REPORT_ROOT,
      "yc-dispatch-report.config.ts",
    );
    const nonConfigSource = paths
      .filter((path) => path !== configPath)
      .map((path) => readFileSync(path, "utf8"))
      .join("\n");
    for (const configuredValue of [
      "RGO-0828-01",
      "Picker-01",
      "Collect range balls",
      "Zone A",
      "Update after field run",
      "Supervised prototype",
    ]) {
      expect(nonConfigSource, `${configuredValue} is outside the filming config`).not.toContain(
        configuredValue,
      );
    }
  });

  test("states merged observation-adapter and runtime truth without browser coupling or execution claims", () => {
    const demo = readFileSync(
      join(ROOT, "components", "edge-gateway-3d", "EdgeGatewayDemo.tsx"),
      "utf8",
    );
    const fallback = readFileSync(
      join(ROOT, "components", "edge-gateway-3d", "WebGLFallback.tsx"),
      "utf8",
    );
    const readme = readFileSync(join(ROOT, "README.md"), "utf8");
    const provenance = readFileSync(join(ROOT, "SOURCE_PROVENANCE.md"), "utf8");
    const recording = readFileSync(
      join(ROOT, "EDGE_GATEWAY_3D_DEMO_RECORDING.md"),
      "utf8",
    );
    const manifest = readFileSync(
      join(ROOT, "lib", "edge-gateway-model", "manifest.ts"),
      "utf8",
    );
    const productTruthSurfaces = [
      demo,
      fallback,
      readme,
      provenance,
      recording,
      manifest,
    ].join("\n");

    expect(demo).toMatch(/Merged Edge Observation Adapter Kit V0 \+ Agent Runtime V1/);
    expect(demo).toMatch(
      /Observation adapters[\s\S]*Implemented, fixture-backed/i,
    );
    expect(demo).toMatch(/Live device transport[\s\S]*Not implemented/i);
    expect(demo).toMatch(/Edge Gateway deployment[\s\S]*Not implemented/i);
    expect(demo).toMatch(/Fixture composition → Site Runtime validation/);
    expect(demo).toMatch(/EdgeAdapterReport stays separate local evidence/);
    expect(demo).toMatch(
      /five simulation-only facility channels \+ upstream\/source references/,
    );
    expect(demo).toMatch(/Site Runtime quality-gates the exact envelope/);
    expect(demo).toMatch(/Shadow Ops evaluation/);
    expect(fallback).toMatch(
      /does\s+not run or\s+connect\s+to the Python runtime/,
    );
    expect(readme).toMatch(
      /Agent Runtime V1 is\s+implemented for deterministic synthetic or fixture-backed observations/,
    );
    for (const source of [readme, provenance, recording]) {
      expect(source).toMatch(
        /Transport-neutral observation\s+conversion\s+is\s+implemented\s+for\s+deterministic,\s+fixture-backed,\s+already-read\s+samples\./i,
      );
      expect(source).toMatch(
        /Live\s+physical\s+transports\s+and\s+device\s+connectivity\s+remain\s+unimplemented/i,
      );
    }
    expect(fallback).toMatch(/Observation adapters are implemented and fixture-backed/);
    expect(fallback).toMatch(
      /EdgeAdapterReport diagnostics — separate local conversion evidence/,
    );
    expect(fallback).toMatch(
      /five simulation-only facility channels \+ upstream \/ source-reference inputs/,
    );
    expect(readme).toMatch(/separate EdgeAdapterReport diagnostics/);
    expect(provenance).toMatch(/separate local `EdgeAdapterReport`/);
    expect(recording).toMatch(/diagnostic report stays separate local evidence/);
    for (const source of [readme, provenance, recording]) {
      expect(source).toMatch(/five required\s+simulation-only facility-system Observations/i);
      expect(source).toMatch(
        /upstream(?:\/|\s+)source\s+references|UpstreamInputs\s+and\s+SourceReference records/i,
      );
    }
    for (const source of [demo, fallback, readme, provenance, recording]) {
      expect(source).toMatch(/robot or actuator execution/i);
    }
    expect(manifest).toMatch(/already-read digital-I\/O samples/);
    expect(manifest).toMatch(/already-read load-cell samples into canonical Observations/);
    expect(productTruthSurfaces).not.toMatch(/\bno physical adapters?\b/i);
    expect(productTruthSurfaces).not.toMatch(
      /Physical telemetry\s+adapters(?:\/transports)?[\s\S]{0,200}remain unimplemented/i,
    );
    expect(demo).toMatch(/not caused by any manager response/);
  });

  test("isolates the 3D runtime behind the dedicated route client loader", () => {
    const home = readFileSync(join(ROOT, "app", "page.tsx"), "utf8");
    const loader = readFileSync(
      join(ROOT, "app", "edge-gateway-demo", "EdgeGatewayDemoLoader.tsx"),
      "utf8",
    );
    expect(home).not.toMatch(/edge-gateway|three|GatewayCanvas/i);
    expect(loader).toMatch(/^"use client";/);
    expect(loader).toMatch(/dynamic\s*\(/);
    expect(loader).toMatch(/ssr:\s*false/);
  });

  test("wires the same-origin model registry into the rendered 3D route", () => {
    const canvas = readFileSync(
      join(ROOT, "components", "edge-gateway-3d", "GatewayCanvas.tsx"),
      "utf8",
    );
    expect(canvas).toMatch(/resolvePartModel/);
    expect(canvas).toMatch(/GLTFLoader/);
    expect(canvas).toMatch(/ModelErrorBoundary/);
    expect(canvas).toMatch(/registeredModelLoadError/);
    expect(canvas).toMatch(/\/models\/edge-gateway\//);
  });

  test("renders named installation interfaces, protective earth, and the weighing assembly", () => {
    const canvas = readFileSync(
      join(ROOT, "components", "edge-gateway-3d", "GatewayCanvas.tsx"),
      "utf8",
    );
    const demo = readFileSync(
      join(ROOT, "components", "edge-gateway-3d", "EdgeGatewayDemo.tsx"),
      "utf8",
    );
    expect(canvas).toMatch(/INSTALLATION_INTERFACES\.map/);
    expect(canvas).toMatch(/onSelectInterface/);
    expect(canvas).toMatch(/ProceduralLoadCellAssembly/);
    expect(canvas).toMatch(/torusGeometry/);
    expect(demo).toMatch(/Conceptual installation interfaces/);
    expect(demo).toMatch(/Dispenser sensor/);
    expect(demo).toMatch(/Universal Handoff H1/);
    expect(demo).toMatch(/PE bond/);
  });

  test("routes delegate social images to the cleared shared metadata", () => {
    const layout = readFileSync(join(ROOT, "app", "layout.tsx"), "utf8");
    const page = readFileSync(
      join(ROOT, "app", "edge-gateway-demo", "page.tsx"),
      "utf8",
    );
    expect(layout).toMatch(/ROOT_METADATA/);
    expect(page).toMatch(/EDGE_GATEWAY_METADATA/);
    expect(layout).not.toMatch(/og\.png/);
    expect(page).not.toMatch(/og\.png/);
    expect(page).not.toMatch(/images:\s*\[\]/);
  });

  test("proves the runtime-import guard catches each forbidden direction", () => {
    expect(
      isForbiddenRuntimeImport("../../../simulation/nxt_facility/state"),
    ).toBe(true);
    expect(
      isForbiddenRuntimeImport("../../../simulation/nxt_agent_runtime"),
    ).toBe(true);
    expect(isForbiddenRuntimeImport("@nxtektal/roi-engine")).toBe(true);
    expect(isForbiddenRuntimeImport("next")).toBe(false);
  });

  test("keeps stream metadata outside the v1 runtime", () => {
    for (const path of PRODUCTION_ROOTS.flatMap(filesUnder)) {
      expect(readFileSync(path, "utf8"), relative(ROOT, path)).not.toMatch(
        /stream\.meta\.json|parseStreamMeta|STREAM_SCHEMA/,
      );
    }
  });

  test("accounts for every recovered committed source file", () => {
    const sourceMap = JSON.parse(
      readFileSync(join(ROOT, "SOURCE_FILE_MAP.json"), "utf8"),
    ) as { mapping: Array<{ source: string; target: string | null }> };
    const expected = [
      ".gitignore",
      ".openai/hosting.json",
      "README.md",
      "app/ReplayStory.tsx",
      "app/chatgpt-auth.ts",
      "app/globals.css",
      "app/layout.tsx",
      "app/page.tsx",
      "build/sites-vite-plugin.ts",
      "db/index.ts",
      "db/schema.ts",
      "drizzle.config.ts",
      "drizzle/meta/_journal.json",
      "eslint.config.mjs",
      "examples/d1/app/api/notes/route.ts",
      "examples/d1/db/schema.ts",
      "next.config.ts",
      "package-lock.json",
      "package.json",
      "postcss.config.mjs",
      "public/og.png",
      "tests/rendered-html.test.mjs",
      "tsconfig.json",
      "vite.config.ts",
      "worker/index.ts",
    ];

    expect(sourceMap.mapping.map((entry) => entry.source).sort()).toEqual(
      expected.sort(),
    );
    for (const entry of sourceMap.mapping) {
      const target = entry.target;
      if (target) {
        expect(() => readFileSync(join(ROOT, target))).not.toThrow();
      }
    }
  });

  test("contains no machine-specific paths or archived starter duplicates", () => {
    const textFiles = filesUnder(ROOT).filter(
      (path) =>
        !path.includes("node_modules") &&
        !path.includes(".next") &&
        !path.endsWith(".png") &&
        !path.endsWith("package-lock.json"),
    );
    for (const path of textFiles) {
      expect(readFileSync(path, "utf8"), relative(ROOT, path)).not.toMatch(
        /\/Users\/|[A-Za-z]:\\\\/,
      );
    }
    expect(filesUnder(ROOT).map((path) => relative(ROOT, path))).not.toEqual(
      expect.arrayContaining([
        "app/globals 2.css",
        "app/page 2.tsx",
        "tests/rendered-html.test 2.mjs",
      ]),
    );
  });
});
