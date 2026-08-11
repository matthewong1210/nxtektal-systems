import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, test } from "vitest";

const ROOT = process.cwd();
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
      "next",
      "react",
      "react-dom",
    ]);
  });

  test("production imports do not reach NXTektal runtimes or execution APIs", () => {
    const production = filesUnder(join(ROOT, "app"));
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
        /apply_directive|RobotTaskInterface|child_process|localStorage|indexedDB/,
      );
    }
  });

  test("proves the runtime-import guard catches each forbidden direction", () => {
    expect(
      isForbiddenRuntimeImport("../../../simulation/nxt_facility/state"),
    ).toBe(true);
    expect(isForbiddenRuntimeImport("@nxtektal/roi-engine")).toBe(true);
    expect(isForbiddenRuntimeImport("next")).toBe(false);
  });

  test("keeps stream metadata outside the v1 runtime", () => {
    for (const path of filesUnder(join(ROOT, "app"))) {
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

  test("retains the recovered preview asset byte-for-byte", () => {
    const bytes = readFileSync(join(ROOT, "public", "og.png"));
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(
      "340b52286f97ae5fd8ea80bd60333dfdc2548f43a4201f0de03602c83f3c2606",
    );
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
