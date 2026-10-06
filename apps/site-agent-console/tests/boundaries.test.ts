import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const APP_ROOT = join(import.meta.dirname, "..");
const SOURCE_DIRS = ["app", "components", "lib"];
const STAFFING_API_ROOT = "/api/v1/staffing";
const expectedProductionDependencies = {
  next: "16.3.8",
  react: "19.2.6",
  "react-dom": "19.2.6",
};
const ALLOWED_FETCH_FACTORIES = new Map([
  ["createClient", "../lib/api"],
  ["createCourseOpsClient", "../lib/course-ops"],
  ["createPlanningClient", "../lib/planning"],
  ["createStaffingClient", "../lib/staffing"],
  ["createTaskOpsClient", "../lib/task-ops"],
  ["createCollectionExecutionsClient", "../../lib/collection-executions"],
]);

function isAllowedStaffingApiPath(path: string): boolean {
  return (
    path === STAFFING_API_ROOT ||
    path.startsWith(`${STAFFING_API_ROOT}/`)
  );
}

function isAllowedManagerApiPath(path: string): boolean {
  return (
    path === "/api/v0" ||
    path.startsWith("/api/v0/") ||
    path === "/api/v1/collection-executions" ||
    path === "/api/v1/collection-executions/requests/${encodeURIComponent(requestId)}" ||
    path === "/api/v1/course-ops" ||
    path.startsWith("/api/v1/course-ops/") ||
    path === "/api/v1/planning" ||
    path.startsWith("/api/v1/planning/") ||
    path.startsWith("/api/v1/planning$") ||
    isAllowedStaffingApiPath(path)
  );
}

function productionDependencies(): Record<string, string> {
  const manifest = JSON.parse(
    readFileSync(join(APP_ROOT, "package.json"), "utf-8"),
  ) as { dependencies: Record<string, string> };
  return manifest.dependencies;
}

function staffingProductionFiles(): string[] {
  // Apply the broad advisory vocabulary guard to the staffing presentation,
  // input and network seams. The controller remains under the repository-wide
  // exact-token guard; its internal `executePost` name means "perform this HTTP
  // request" and is not an execution-authority surface.
  const componentDirectory = join(APP_ROOT, "components", "staffing");
  const nestedComponents = readdirSync(componentDirectory)
    .filter((entry) => entry.endsWith(".tsx"))
    .sort()
    .map((entry) => join(componentDirectory, entry));
  return [
    join(APP_ROOT, "components", "StaffingPanel.tsx"),
    ...nestedComponents,
    join(APP_ROOT, "lib", "staffing.ts"),
    join(APP_ROOT, "lib", "staffing-actions.ts"),
    join(APP_ROOT, "lib", "staffing-csv.ts"),
    join(APP_ROOT, "lib", "staffing-guards.ts"),
  ];
}

function staffingProductionSource(): string {
  return staffingProductionFiles()
    .map((file) => readFileSync(file, "utf-8"))
    .join("\n");
}

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
  "XMLHttpRequest",
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

function unexpectedFetchCapabilities(text: string, fileName = "fixture.ts"): string[] {
  if (!/\.tsx?$/.test(fileName)) {
    return [];
  }
  const source = ts.createSourceFile(
    fileName,
    text,
    ts.ScriptTarget.Latest,
    true,
    fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  // A fetch capability is trusted only as the direct, sole adapter argument
  // of an exact Manager API client factory imported from its production
  // module. Keeping every other use of both names closed also rejects aliases
  // and proves that a factory call is not resolving to a local shadow.
  const importedFactories = new Map<string, ts.ImportSpecifier>();
  for (const statement of source.statements) {
    if (
      !ts.isImportDeclaration(statement) ||
      !ts.isStringLiteral(statement.moduleSpecifier) ||
      statement.importClause?.isTypeOnly ||
      !statement.importClause?.namedBindings ||
      !ts.isNamedImports(statement.importClause.namedBindings)
    ) {
      continue;
    }
    for (const binding of statement.importClause.namedBindings.elements) {
      const exportedName = binding.propertyName?.text ?? binding.name.text;
      if (
        !binding.isTypeOnly &&
        binding.propertyName === undefined &&
        binding.name.text === exportedName &&
        ALLOWED_FETCH_FACTORIES.get(exportedName) === statement.moduleSpecifier.text
      ) {
        importedFactories.set(exportedName, binding);
      }
    }
  }

  const isAllowedFactoryAdapter = (call: ts.CallExpression): boolean => {
    if (!ts.isIdentifier(call.expression) || call.expression.text !== "fetch") {
      return false;
    }
    const adapter = call.parent;
    if (
      !ts.isArrowFunction(adapter) ||
      adapter.body !== call ||
      adapter.parameters.length !== 2 ||
      call.arguments.length !== 2
    ) {
      return false;
    }
    const [inputParameter, initParameter] = adapter.parameters;
    const [inputArgument, initArgument] = call.arguments;
    if (
      !ts.isIdentifier(inputParameter.name) ||
      inputParameter.name.text !== "input" ||
      !ts.isIdentifier(initParameter.name) ||
      initParameter.name.text !== "init" ||
      !ts.isIdentifier(inputArgument) ||
      inputArgument.text !== "input" ||
      !ts.isIdentifier(initArgument) ||
      initArgument.text !== "init"
    ) {
      return false;
    }
    const factoryCall = adapter.parent;
    return (
      ts.isCallExpression(factoryCall) &&
      factoryCall.arguments.length === 1 &&
      factoryCall.arguments[0] === adapter &&
      ts.isIdentifier(factoryCall.expression) &&
      importedFactories.has(factoryCall.expression.text)
    );
  };
  const isAllowedFetchIdentifier = (identifier: ts.Identifier): boolean => {
    const call = identifier.parent;
    if (
      ts.isCallExpression(call) &&
      call.expression === identifier &&
      isAllowedFactoryAdapter(call)
    ) {
      return true;
    }
    const parameter = identifier.parent;
    const factory = parameter.parent;
    return (
      fileName === join(APP_ROOT, "lib", "staffing.ts") &&
      ts.isParameter(parameter) &&
      parameter.initializer === identifier &&
      ts.isIdentifier(parameter.name) &&
      parameter.name.text === "fetchImpl" &&
      ts.isFunctionDeclaration(factory) &&
      factory.parent === source &&
      factory.parameters.length === 1 &&
      factory.parameters[0] === parameter &&
      factory.name?.text === "createStaffingClient" &&
      factory.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword) === true
    );
  };
  const isAllowedFactoryIdentifier = (identifier: ts.Identifier): boolean => {
    const importedFactory = importedFactories.get(identifier.text);
    if (importedFactory?.name === identifier) {
      return true;
    }
    const factoryCall = identifier.parent;
    if (
      !ts.isCallExpression(factoryCall) ||
      factoryCall.expression !== identifier ||
      factoryCall.arguments.length !== 1
    ) {
      return false;
    }
    const adapter = factoryCall.arguments[0];
    return (
      ts.isArrowFunction(adapter) &&
      ts.isCallExpression(adapter.body) &&
      isAllowedFactoryAdapter(adapter.body)
    );
  };
  const unexpected: string[] = [];
  const visit = (node: ts.Node) => {
    const isUnexpectedFetchIdentifier =
      ts.isIdentifier(node) &&
      node.text === "fetch" &&
      !isAllowedFetchIdentifier(node);
    const isUnexpectedBrowserGlobal =
      ts.isIdentifier(node) &&
      (node.text === "globalThis" || node.text === "window" || node.text === "self");
    const isUnexpectedFactoryIdentifier =
      ts.isIdentifier(node) &&
      importedFactories.has(node.text) &&
      !isAllowedFactoryIdentifier(node);
    if (
      isUnexpectedFetchIdentifier ||
      isUnexpectedBrowserGlobal ||
      isUnexpectedFactoryIdentifier
    ) {
      const { line, character } = source.getLineAndCharacterOfPosition(node.getStart(source));
      unexpected.push(`${line + 1}:${character + 1}`);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return unexpected;
}

describe("console boundaries", () => {
  it("rejects sibling prefixes that only resemble the staffing API root", () => {
    expect(isAllowedStaffingApiPath(STAFFING_API_ROOT)).toBe(true);
    expect(isAllowedStaffingApiPath(`${STAFFING_API_ROOT}/dates/2026-10-07`)).toBe(true);

    expect(isAllowedStaffingApiPath(`${STAFFING_API_ROOT}-admin`)).toBe(false);
    expect(isAllowedStaffingApiPath(`${STAFFING_API_ROOT}evil`)).toBe(false);
    expect(isAllowedStaffingApiPath(`${STAFFING_API_ROOT}\${path}`)).toBe(false);
    expect(isAllowedStaffingApiPath(`${STAFFING_API_ROOT}$path`)).toBe(false);
    expect(isAllowedStaffingApiPath("/api/v2/staffing")).toBe(false);
    expect(isAllowedStaffingApiPath("https://example.test/api/v1/staffing")).toBe(false);
  });

  it("keeps the production dependency surface minimal and pinned", () => {
    expect(productionDependencies()).toEqual(expectedProductionDependencies);
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

  it("keeps every dedicated staffing source transient, same-origin, and advisory-only", () => {
    const staffingSource = staffingProductionSource();
    expect(staffingSource).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    expect(staffingSource).not.toMatch(/https?:\/\//);
    expect(staffingSource).not.toMatch(/robot|dispatch|execute|notify/i);
  });

  it.each([
    [
      "a protocol-relative URL assembled from variables",
      `
        const host = getHost();
        const input = "/" + "/" + host + "/collect";
        fetch(input, init);
      `,
    ],
    [
      "an HTTP URL assembled from string fragments",
      `
        const input = "ht" + "tp://" + getHost() + "/collect";
        fetch(input, init);
      `,
    ],
  ])("rejects %s", (_description, source) => {
    expect(unexpectedFetchCapabilities(source)).not.toEqual([]);
  });

  it.each([
    ["a direct alias", "const request = fetch; request(input, init);"],
    ["Function.call", "fetch.call(globalThis, input, init);"],
    ["Function.apply", "fetch.apply(globalThis, [input, init]);"],
    [
      "a bound global property",
      "const request = globalThis.fetch.bind(globalThis); request(input, init);",
    ],
    [
      "a bracket-accessed global property",
      'const request = globalThis["fetch"]; request(input, init);',
    ],
    [
      "a computed global property",
      'const request = globalThis["fe" + "tch"]; request(input, init);',
    ],
    [
      "a reflected global property",
      'const request = Reflect.get(globalThis, "fetch"); request(input, init);',
    ],
  ])("rejects fetch capability escape through %s", (_description, source) => {
    expect(unexpectedFetchCapabilities(source)).not.toEqual([]);
  });

  it("rejects a locally shadowed approved factory", () => {
    const source = `
      import { createClient } from "../lib/api";
      function bypass() {
        const createClient = (adapter: unknown) => adapter;
        createClient((input, init) => fetch(input, init));
      }
    `;
    expect(unexpectedFetchCapabilities(source)).not.toEqual([]);
  });

  it("talks only to the versioned same-origin manager API", () => {
    // v0 is the Manager API; v1 permits only the exact frozen planning,
    // course-ops, collection-execution, and staffing advisory contracts.
    for (const file of sourceFiles()) {
      const text = readFileSync(file, "utf-8");
      const urls = text.match(/https?:\/\/[^\s"'`]+/g) ?? [];
      expect(urls, `${file} hardcodes a network URL: ${urls}`).toEqual([]);
      expect(text, `${file} contains a protocol-relative URL`).not.toMatch(
        /["'`]\/\/(?!\/)/,
      );
      expect(text, `${file} constructs a URL object`).not.toMatch(
        /\b(?:new\s+URL|URL)\s*\(/,
      );
      const unexpectedFetches = unexpectedFetchCapabilities(text, file);
      expect(
        unexpectedFetches,
        `${file} contains a fetch capability outside the closed API clients`,
      ).toEqual([]);
      const apiPaths = text.match(/\/api\/v\d+[^\s"'`]*/g) ?? [];
      for (const path of apiPaths) {
        expect(isAllowedManagerApiPath(path), `${file} uses ${path}`).toBe(true);
      }
    }
  });

  it("declares the static-export output so no server runtime ships", () => {
    const config = readFileSync(join(APP_ROOT, "next.config.ts"), "utf-8");
    expect(config).toContain('output: "export"');
  });
});
