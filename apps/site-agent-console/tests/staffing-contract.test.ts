import { createHash } from "node:crypto";
import { cpSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { API_SCHEMA, DISCLAIMER, ManagerApiError, type FetchLike } from "../lib/api";
import * as staffingRuntime from "../lib/staffing";
import { STAFFING_CONTRACT_REVISION } from "../lib/staffing-revision";
import {
  STAFFING_SCHEMA,
  createStaffingClient,
  newStaffingRequestId,
  parseStaffingReceipt,
  parseStaffingRequestBody,
  parseStaffingSnapshot,
  type AssignmentProjection,
  type CandidateOperationProjection,
  type CandidateProjection,
  type CoverageGap,
  type EffectivePlanProjection,
  type ExceptionCancelRequest,
  type ExceptionCancelledRecord,
  type ExceptionCorrectRequest,
  type ExceptionCorrectedRecord,
  type ExceptionProjection,
  type ExceptionRecordedRecord,
  type ExceptionRecordRequest,
  type ExceptionReplacementInput,
  type GenerationCapability,
  type GenerationInProgressRecord,
  type GenerationInterruptedRecord,
  type GenerationProjection,
  type GenerationReservedRecord,
  type ManagerAcceptRequest,
  type ManagerModifyRequest,
  type ManagerPatchOperation,
  type ManagerRejectRequest,
  type ManagerResponseCommittedRecord,
  type ManagerResponseRequest,
  type ManagerResponseSummary,
  type OperationKind,
  type OperationState,
  type ProviderProvenance,
  type RevisionVector,
  type RosterImportedRecord,
  type RosterImportRequest,
  type StaffingClient,
  type StaffingDateSnapshot,
  type StaffingMutation,
  type StaffingReceipt,
  type StaffingRequestBody,
  type SuggestionGenerateRequest,
  type SuggestionIssuedRecord,
  type SuggestionUnavailableRecord,
} from "../lib/staffing";

const EXAMPLES_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "..",
  "simulation",
  "docs",
  "contracts",
  "staffing-v1",
  "examples",
);

type SuccessExchange = {
  name: string;
  method: "GET" | "POST";
  route_template: string;
  request?: unknown;
  http_status: number;
  body: { schema: string; disclaimer: string; data: unknown };
};

type ErrorExchange = Omit<SuccessExchange, "body"> & {
  body: { schema: string; disclaimer: string; error: { code: string; detail: string } };
};

const successDocuments = readdirSync(EXAMPLES_DIR)
  .filter((name) => name.endsWith(".json") && name !== "errors.json")
  .sort()
  .map((name) => ({
    name,
    document: JSON.parse(readFileSync(join(EXAMPLES_DIR, name), "utf-8")) as {
      schema: string;
      exchanges: SuccessExchange[];
    },
  }));

const errors = JSON.parse(
  readFileSync(join(EXAMPLES_DIR, "errors.json"), "utf-8"),
) as { schema: string; exchanges: ErrorExchange[] };

const response = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const exchange = (name: string): SuccessExchange => {
  for (const { document } of successDocuments) {
    const found = document.exchanges.find((item) => item.name === name);
    if (found) return found;
  }
  throw new Error(`missing fixture ${name}`);
};

function mutationFor(item: SuccessExchange): StaffingMutation {
  const body = structuredClone(item.request) as StaffingRequestBody;
  switch (item.name) {
    case "roster-committed":
      return { operationKind: "roster-import", body: body as RosterImportRequest };
    case "exception-recorded":
      return { operationKind: "exception-record", body: body as ExceptionRecordRequest };
    case "exception-cancelled":
      return {
        operationKind: "exception-cancel",
        targetId: (item.body.data as StaffingReceipt & { record: ExceptionCancelledRecord }).record.exception_id,
        body: body as ExceptionCancelRequest,
      };
    case "exception-corrected":
      return {
        operationKind: "exception-correct",
        targetId: (item.body.data as StaffingReceipt & { record: ExceptionCorrectedRecord }).record.replaced_exception_id,
        body: body as ExceptionCorrectRequest,
      };
    case "generation-reserved":
    case "generation-in-progress":
    case "suggestion-issued":
    case "no-valid-suggestion":
    case "unavailable":
    case "refused":
    case "invalid-response":
    case "provider-error":
    case "configuration-error":
    case "security-error":
      return { operationKind: "suggestion-generate", body: body as SuggestionGenerateRequest };
    case "manager-accepted":
    case "manager-modified":
    case "manager-rejected":
      return {
        operationKind: "manager-response",
        targetId: (item.body.data as StaffingReceipt & { record: ManagerResponseCommittedRecord }).record.suggestion_id,
        body: body as ManagerResponseRequest,
      };
    default:
      throw new Error(`not a mutation fixture: ${item.name}`);
  }
}

function scripted(status: number, body: unknown) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({ url, init });
    return response(status, body);
  };
  return { calls, client: createStaffingClient(fetchImpl) };
}

function largeRoster(workerCount: number, sourceRefLength: number): RosterImportRequest {
  const body = structuredClone(exchange("roster-committed").request) as RosterImportRequest;
  body.source_ref = "s".repeat(sourceRefLength);
  body.workers = Array.from({ length: workerCount }, (_, index) => ({
    staff_id: `staff-${String(index).padStart(4, "0")}`,
    display_name: "X".repeat(100),
    skill_codes: ["BALL_PICKING"],
    eligibility: [{ role_code: "RANGE_ATTENDANT", area_code: "RANGE_A" }],
    max_daily_minutes: 480,
  }));
  return body;
}

function retryChainSnapshot(): StaffingDateSnapshot {
  const snapshot = structuredClone(
    exchange("date-after-manager-response").body.data,
  ) as StaffingDateSnapshot;
  const source = snapshot.generations[0];
  const parent = {
    ...structuredClone(source),
    suggestion_id: "generation-result-unknown-parent",
    request_id: "request-result-unknown-parent",
    operation_id: "staffing-event-result-unknown-parent",
    state: "RESULT_UNKNOWN",
    retry_of: null,
    candidates: [],
    coverage_gaps: [],
    provenance: [],
    failure_code: "RESULT_UNKNOWN",
    manager_response: null,
  } as GenerationProjection;
  const child = {
    ...structuredClone(source),
    suggestion_id: "generation-retry-child",
    request_id: "request-retry-child",
    operation_id: "staffing-event-retry-child",
    retry_of: parent.suggestion_id,
    manager_response: null,
  } as GenerationProjection;
  snapshot.generations = [parent, child];
  return snapshot;
}

// Compile-time surface lock: removing or changing any named exported type must fail typecheck.
type FrozenTypeSurface = [
  StaffingClient,
  OperationKind,
  OperationState,
  StaffingMutation,
  RevisionVector,
  StaffingDateSnapshot,
  GenerationCapability,
  GenerationProjection,
  CandidateProjection,
  CandidateOperationProjection,
  CoverageGap,
  AssignmentProjection,
  ExceptionProjection,
  EffectivePlanProjection,
  ManagerResponseSummary,
  ProviderProvenance,
  RosterImportRequest,
  ExceptionRecordRequest,
  ExceptionCancelRequest,
  ExceptionCorrectRequest,
  SuggestionGenerateRequest,
  ManagerAcceptRequest,
  ManagerModifyRequest,
  ManagerRejectRequest,
  ManagerResponseRequest,
  ExceptionReplacementInput,
  ManagerPatchOperation,
  StaffingRequestBody,
  StaffingReceipt,
  RosterImportedRecord,
  ExceptionRecordedRecord,
  ExceptionCancelledRecord,
  ExceptionCorrectedRecord,
  GenerationReservedRecord,
  GenerationInProgressRecord,
  GenerationInterruptedRecord,
  SuggestionIssuedRecord,
  SuggestionUnavailableRecord,
  ManagerResponseCommittedRecord,
];

// Lockstep witness: identical to STAFFING_CONTRACT_FINGERPRINT in
// simulation/tests/pilot_ops/test_staffing_wire_contract.py. Both pins move in
// the same commit as the schema or any frozen example; see the contract README,
// "Versioning and lockstep rollout".
const REPOSITORY_ROOT = join(EXAMPLES_DIR, "..", "..", "..", "..", "..");
const LOCKSTEP_MANIFEST = join(REPOSITORY_ROOT, "simulation", "docs", "contracts", "staffing-v1", "lockstep.json");
const PYTHON_REVISION_MODULE = join(REPOSITORY_ROOT, "simulation", "scripts", "staffing_contract_revision.py");
const STAFFING_CONTRACT_FINGERPRINT = "979c879acf34061f909002d4cf3022fc1bf08fe5a5e51da93cb3984d6f9d0583";
const CANDIDATE_KEYS = [
  "action_window_end_at", "actionability", "candidate_index", "coverage_gaps", "operational_warnings",
  "operational_warnings_local", "operations", "rationale", "rationale_local", "rejection_codes",
  "schedule_digest", "status",
];

/** The same manifest-and-digest rule as `compute_contract_revision()` in
 * `simulation/scripts/staffing_contract_revision.py`: directories are walked
 * recursively (dotfiles and `__pycache__` skipped, regular files with a listed
 * suffix only), the listed dependencies join the sources, CRLF is folded to LF,
 * and the revision is the SHA-256 over one `<path> <sha256>` line per covered
 * file in path order. */
function foldCrlf(raw: Buffer): Buffer {
  return Buffer.from(raw.toString("latin1").replace(/\r\n/g, "\n"), "latin1");
}

function sourceBytes(relativePath: string): Buffer {
  return foldCrlf(readFileSync(join(REPOSITORY_ROOT, relativePath)));
}

type LockstepManifest = {
  schema: string;
  contract_revision: string;
  sources: { files: string[]; directories: Array<{ path: string; suffixes: string[] }> };
  dependencies: { files: string[] };
  boundaries: Array<{ path: string; reason: string; names: string[] }>;
};

function lockstepManifest(): LockstepManifest {
  const manifest = JSON.parse(readFileSync(LOCKSTEP_MANIFEST, "utf-8")) as LockstepManifest;
  expect(manifest.schema).toBe("nxt-staffing-lockstep/v1");
  expect(Object.keys(manifest).sort()).toEqual(["boundaries", "contract_revision", "dependencies", "schema", "sources"]);
  return manifest;
}

/** SHA-256 over schema.json then every example in name order, CRLF folded. */
function contractFingerprint(contractDir: string): string {
  const hash = createHash("sha256");
  hash.update(foldCrlf(readFileSync(join(contractDir, "schema.json"))));
  for (const name of readdirSync(join(contractDir, "examples")).filter((entry) => entry.endsWith(".json")).sort()) {
    hash.update(foldCrlf(readFileSync(join(contractDir, "examples", name))));
  }
  return hash.digest("hex");
}

const CONSOLE_PREFIX = "apps/site-agent-console/";
const CONSOLE_DIR = join(REPOSITORY_ROOT, "apps", "site-agent-console");
const CONSOLE_STATEMENT = /\b(?:import|export)\b([^;"'`]*?)\bfrom\s+["']([^"']+)["']/g;
const CONSOLE_SIDE_EFFECT = /(?:^|[;\s])import\s+["']([^"']+)["']/g;
const CONSOLE_DYNAMIC = /\b(import|require)\(\s*["']([^"']+)["']\s*\)/g;

type ConsoleEdge = { target: string; names: Set<string> };

/** `compilerOptions.paths` of the console tsconfig as (prefix, directory) pairs. */
function consoleAliases(): Array<[string, string]> {
  const document = JSON.parse(readFileSync(join(CONSOLE_DIR, "tsconfig.json"), "utf-8")) as { compilerOptions?: { paths?: Record<string, string[]> } };
  return Object.entries(document.compilerOptions?.paths ?? {}).map(([pattern, targets]) => {
    expect(pattern.endsWith("/*") && targets.length === 1 && targets[0].endsWith("/*"), pattern).toBe(true);
    return [pattern.slice(0, -1), resolve(CONSOLE_DIR, targets[0].slice(0, -2))];
  });
}

/** The names an import or export clause takes: `{ a, b as c, type D }` gives
 * a, b, D; a default import gives "default"; `* as ns` or `*` gives "*". */
function clauseNames(rawClause: string): Set<string> {
  let clause = rawClause.trim().replace(/^type\s+/, "");
  const names = new Set<string>();
  const braces = /\{([^}]*)\}/.exec(clause);
  if (braces) {
    for (const item of braces[1].split(",")) {
      const trimmed = item.trim().replace(/^type\s+/, "");
      if (trimmed) names.add(trimmed.split(/\s+as\s+/)[0].trim());
    }
    clause = clause.slice(0, braces.index) + clause.slice(braces.index + braces[0].length);
  }
  for (const item of clause.split(",")) {
    const trimmed = item.trim();
    if (!trimmed) continue;
    names.add(trimmed.startsWith("*") ? "*" : "default");
  }
  return names;
}

/** Edges of a console source: relative specifiers and tsconfig aliases from
 * import/export-from statements (any number per line), side-effect imports and
 * dynamic `import()`; `require` is refused (the console side of
 * `dependency_problems()` in the Python module). */
function consoleEdges(relativePath: string): ConsoleEdge[] {
  const text = readFileSync(join(REPOSITORY_ROOT, relativePath), "utf-8");
  const aliases = consoleAliases();
  const resolveSpecifier = (specifier: string): string | null => {
    let base: string | null = null;
    if (specifier.startsWith(".")) base = resolve(dirname(join(REPOSITORY_ROOT, relativePath)), specifier);
    else {
      const alias = aliases.find(([prefix]) => specifier.startsWith(prefix));
      if (alias) base = resolve(alias[1], specifier.slice(alias[0].length));
    }
    if (base === null) return null;
    const candidates = [`${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")];
    const resolved = candidates.find((candidate) => { try { return statSync(candidate).isFile(); } catch { return false; } });
    if (resolved === undefined) throw new Error(`unresolved first-party import ${specifier} in ${relativePath}`);
    return relative(REPOSITORY_ROOT, resolved).split("\\").join("/");
  };
  const edges: ConsoleEdge[] = [];
  for (const match of text.matchAll(CONSOLE_STATEMENT)) {
    const target = resolveSpecifier(match[2]);
    if (target !== null) edges.push({ target, names: clauseNames(match[1]) });
  }
  for (const match of text.matchAll(CONSOLE_SIDE_EFFECT)) {
    const target = resolveSpecifier(match[1]);
    if (target !== null) edges.push({ target, names: new Set() });
  }
  for (const match of text.matchAll(CONSOLE_DYNAMIC)) {
    if (match[1] === "require") throw new Error(`dynamic import in ${relativePath}: require cannot be analysed`);
    const target = resolveSpecifier(match[2]);
    if (target !== null) edges.push({ target, names: new Set(["*"]) });
  }
  return edges;
}

function walkSources(relativeDir: string, suffixes: string[], found: Set<string>): void {
  for (const entry of readdirSync(join(REPOSITORY_ROOT, relativeDir), { withFileTypes: true })) {
    if (entry.name.startsWith(".") || entry.name === "__pycache__") continue;
    const relative = `${relativeDir}/${entry.name}`;
    if (entry.isSymbolicLink()) throw new Error(`symbolic link inside a covered directory: ${relative}`);
    if (entry.isDirectory()) walkSources(relative, suffixes, found);
    else if (entry.isFile() && suffixes.some((suffix) => entry.name.endsWith(suffix))) found.add(relative);
  }
}

function manifestSources(manifest: LockstepManifest): string[] {
  const found = new Set<string>(manifest.sources.files);
  for (const entry of manifest.sources.directories) walkSources(entry.path, entry.suffixes, found);
  return [...found].sort();
}

function computeContractRevision(): { revision: string; sources: string[]; covered: string[] } {
  const manifest = lockstepManifest();
  const sources = manifestSources(manifest);
  const covered = [...new Set([...sources, ...manifest.dependencies.files])].sort();
  const lines = covered.map((relativePath) => `${relativePath} ${createHash("sha256").update(sourceBytes(relativePath)).digest("hex")}\n`);
  return { revision: createHash("sha256").update(lines.join("")).digest("hex"), sources, covered };
}

function withContractRevision<T>(value: T, revision: string | undefined): T {
  const copy = structuredClone(value) as unknown as Record<string, unknown>;
  if (revision === undefined) delete copy["contract_revision"];
  else copy["contract_revision"] = revision;
  return copy as unknown as T;
}

const FOREIGN_REVISION = "f".repeat(64);

describe("staffing v1 lockstep guards", () => {
  const schemaDocument = JSON.parse(readFileSync(join(EXAMPLES_DIR, "..", "schema.json"), "utf-8")) as {
    $defs: Record<string, { additionalProperties?: boolean; properties?: Record<string, unknown>; required?: string[] }>;
  };

  it("pins the contract revision recomputed over the lockstep source set, identically to the service", () => {
    const { revision, sources, covered } = computeContractRevision();
    expect(STAFFING_CONTRACT_REVISION).toMatch(/^[0-9a-f]{64}$/);
    expect(STAFFING_CONTRACT_REVISION).toBe(revision);
    const manifest = JSON.parse(readFileSync(LOCKSTEP_MANIFEST, "utf-8")) as { contract_revision: string };
    expect(manifest.contract_revision).toBe(revision);
    const pythonPin = /^STAFFING_CONTRACT_REVISION = "([0-9a-f]{64})"$/m.exec(readFileSync(PYTHON_REVISION_MODULE, "utf-8"));
    expect(pythonPin?.[1]).toBe(revision);
    // The decoder, controller, actions, labels, time helpers, and components
    // are inside the set; the pin module and every test are outside it.
    for (const required of [
      "apps/site-agent-console/lib/staffing.ts",
      "apps/site-agent-console/lib/staffing-state.ts",
      "apps/site-agent-console/lib/staffing-actions.ts",
      "apps/site-agent-console/lib/staffing-labels.ts",
      "apps/site-agent-console/lib/site-time.ts",
      "apps/site-agent-console/components/StaffingPanel.tsx",
      "apps/site-agent-console/components/staffing/CandidateCards.tsx",
      "simulation/scripts/staffing_operations.py",
      "simulation/nxt_site_agent/api.py",
      "simulation/nxt_pilot_ops/staffing/operations.py",
      "simulation/docs/contracts/staffing-v1/schema.json",
    ]) expect(sources, required).toContain(required);
    // Staffing-facing dependencies enter the digest as well: the shared
    // transport and UI primitives here, canonical serialization, the provider
    // gateway, and the Manager API constants on the service side.
    for (const required of [
      "apps/site-agent-console/lib/api.ts",
      "apps/site-agent-console/components/ui.tsx",
      "simulation/nxt_pilot_ops/serialization.py",
      "simulation/nxt_model_gateway/serialization.py",
      "simulation/nxt_model_gateway/routing.py",
      "simulation/nxt_site_agent/contracts.py",
    ]) {
      expect(covered, required).toContain(required);
      expect(sources, required).not.toContain(required);
    }
    for (const excluded of covered) {
      expect(excluded).not.toBe("apps/site-agent-console/lib/staffing-revision.ts");
      expect(excluded).not.toBe("simulation/scripts/staffing_contract_revision.py");
      expect(excluded.includes("/tests/")).toBe(false);
      expect(excluded.includes("/examples/")).toBe(false);
    }
  });

  it("classifies every first-party import the console's covered staffing modules reach and every name taken through a boundary (the console side of the dependency guard)", () => {
    const manifest = lockstepManifest();
    const sources = manifestSources(manifest).filter((item) => item.startsWith(CONSOLE_PREFIX));
    const dependencies = manifest.dependencies.files.filter((item) => item.startsWith(CONSOLE_PREFIX));
    const boundaries = manifest.boundaries.filter((item) => item.path.startsWith(CONSOLE_PREFIX));
    const classified = new Map<string, string>();
    for (const [category, items] of [["source", sources], ["dependency", dependencies], ["boundary", boundaries.map((item) => item.path)]] as const) {
      for (const item of items) {
        expect(classified.has(item), `${item} listed twice`).toBe(false);
        classified.set(item, category);
      }
    }
    const declared = new Map(boundaries.map((item) => [item.path, new Set(item.names)]));
    const reached = new Set<string>();
    const taken = new Map<string, Set<string>>();
    const seen = new Set<string>();
    const stack = [...sources];
    while (stack.length > 0) {
      const current = stack.pop() as string;
      if (seen.has(current)) continue;
      seen.add(current);
      for (const edge of consoleEdges(current)) {
        const category = classified.get(edge.target);
        expect(category, `${edge.target} imported by ${current} is not classified in lockstep.json`).toBeDefined();
        reached.add(edge.target);
        if (category === "boundary") {
          const allowed = declared.get(edge.target) ?? new Set<string>();
          const names = taken.get(edge.target) ?? new Set<string>();
          for (const name of edge.names) {
            names.add(name);
            expect(allowed.has("*") || allowed.has(name), `${current} takes undeclared name ${name} from boundary ${edge.target}`).toBe(true);
          }
          taken.set(edge.target, names);
        } else if (!seen.has(edge.target)) stack.push(edge.target);
      }
    }
    for (const item of [...dependencies, ...boundaries.map((entry) => entry.path)]) expect(reached.has(item), `${item} is listed but nothing covered imports it`).toBe(true);
    expect(reached.has("apps/site-agent-console/lib/api.ts")).toBe(true);
    expect(reached.has("apps/site-agent-console/components/ui.tsx")).toBe(true);
    expect(classified.get("apps/site-agent-console/lib/staffing-revision.ts")).toBe("boundary");
    expect(taken.get("apps/site-agent-console/lib/staffing-revision.ts")).toEqual(new Set(["STAFFING_CONTRACT_REVISION"]));
    expect(consoleAliases().map(([prefix]) => prefix)).toEqual(["@/"]);
  });

  it("parses import clauses the way the service-side walker does", () => {
    expect(clauseNames("{ a, b as c, type D }")).toEqual(new Set(["a", "b", "D"]));
    expect(clauseNames("type { X }")).toEqual(new Set(["X"]));
    expect(clauseNames("Default, { a }")).toEqual(new Set(["default", "a"]));
    expect(clauseNames("* as ns")).toEqual(new Set(["*"]));
    expect(clauseNames("*")).toEqual(new Set(["*"]));
    const statements = [...'import { a } from "./x"; import { b } from "./y";\nexport * from "./z";'.matchAll(CONSOLE_STATEMENT)].map((match) => match[2]);
    expect(statements).toEqual(["./x", "./y", "./z"]);
  });

  it("fingerprints the frozen files with CRLF folded to LF and nothing else forgiven", () => {
    const contractDir = join(EXAMPLES_DIR, "..");
    expect(contractFingerprint(contractDir)).toBe(STAFFING_CONTRACT_FINGERPRINT);
    const scratch = mkdtempSync(join(tmpdir(), "staffing-contract-crlf-"));
    try {
      cpSync(contractDir, scratch, { recursive: true });
      const files = ["schema.json", ...readdirSync(join(scratch, "examples")).filter((name) => name.endsWith(".json")).map((name) => join("examples", name))];
      for (const name of files) {
        const path = join(scratch, name);
        writeFileSync(path, Buffer.from(readFileSync(path).toString("latin1").replace(/\r\n/g, "\n").replace(/\n/g, "\r\n"), "latin1"));
        expect(readFileSync(path).includes("\r\n")).toBe(true);
      }
      expect(contractFingerprint(scratch)).toBe(STAFFING_CONTRACT_FINGERPRINT);
      const coldStart = join(scratch, "examples", "cold-start.json");
      writeFileSync(coldStart, readFileSync(coldStart, "utf-8").replace("SIMULATION", "SIMULATED"));
      expect(contractFingerprint(scratch)).not.toBe(STAFFING_CONTRACT_FINGERPRINT);
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  });

  it("carries the pinned revision in every frozen request, snapshot, and receipt", () => {
    const seen: string[] = [];
    const visit = (value: unknown): void => {
      if (Array.isArray(value)) { value.forEach(visit); return; }
      if (value && typeof value === "object") {
        const item = value as Record<string, unknown>;
        if (typeof item.contract_revision === "string") seen.push(item.contract_revision);
        Object.values(item).forEach(visit);
      }
    };
    for (const { document } of successDocuments) {
      for (const item of document.exchanges) {
        visit(item.body.data);
        if (item.method === "POST") visit(item.request);
        expect((item.body.data as Record<string, unknown>).contract_revision).toBe(STAFFING_CONTRACT_REVISION);
        if (item.method === "POST") expect((item.request as Record<string, unknown>).contract_revision).toBe(STAFFING_CONTRACT_REVISION);
      }
    }
    expect(seen.length).toBe(38);
    expect(new Set(seen)).toEqual(new Set([STAFFING_CONTRACT_REVISION]));
    expect(readFileSync(join(EXAMPLES_DIR, "errors.json"), "utf-8").includes("contract_revision")).toBe(false);
    for (const name of ["StaffingDateSnapshot", "RosterImportRequest", "ExceptionRecordWholeShift", "ExceptionRecordPoint",
      "ExceptionCancelRequest", "ExceptionCorrectRequest", "SuggestionGenerateRequest", "ManagerAcceptRequest",
      "ManagerModifyRequest", "ManagerRejectRequest", "ReceiptRoster", "ReceiptExceptionRecord", "ReceiptExceptionCancel",
      "ReceiptExceptionCorrect", "ReceiptGenerationReserved", "ReceiptGenerationInProgress", "ReceiptGenerationInterrupted",
      "ReceiptSuggestionIssued", "ReceiptSuggestionUnavailable", "ReceiptManagerResponse"]) {
      const definition = schemaDocument.$defs[name];
      expect(definition.properties?.contract_revision, name).toEqual({ $ref: "#/$defs/Digest" });
      expect(definition.required, name).toContain("contract_revision");
    }
  });

  it("rejects a snapshot or receipt from a service at another revision, or at none, as staffing_contract_mismatch", async () => {
    const snapshot = exchange("date-after-manager-response").body.data as StaffingDateSnapshot;
    const receipt = exchange("suggestion-issued").body.data as StaffingReceipt;
    for (const revision of [undefined, FOREIGN_REVISION, "", 42] as const) {
      const label = `revision ${String(revision)}`;
      expect(() => parseStaffingSnapshot(withContractRevision(snapshot, revision as string | undefined)), label)
        .toThrow(expect.objectContaining({ code: "staffing_contract_mismatch", status: 200 }));
      expect(() => parseStaffingReceipt(withContractRevision(receipt, revision as string | undefined)), label)
        .toThrow(expect.objectContaining({ code: "staffing_contract_mismatch", status: 200 }));
    }
    // An older service (built before the field existed) answers the cold-start
    // snapshot without it: the console refuses at the first read, before any write.
    const older = scripted(200, { schema: API_SCHEMA, disclaimer: DISCLAIMER, data: withContractRevision(exchange("current-empty").body.data, undefined) });
    await expect(older.client.current()).rejects.toMatchObject({ status: 200, code: "staffing_contract_mismatch" });
    const foreign = scripted(202, { schema: API_SCHEMA, disclaimer: DISCLAIMER, data: withContractRevision(receipt, FOREIGN_REVISION) });
    await expect(foreign.client.submit(mutationFor(exchange("suggestion-issued")))).rejects.toMatchObject({ status: 202, code: "staffing_contract_mismatch" });
    // Only the revision is special: any other unknown or missing key stays generic invalid data,
    // which is also how an older console treats this checkout's documents.
    const extra = { ...structuredClone(snapshot), review_note: "unknown" } as unknown;
    expect(() => parseStaffingSnapshot(extra)).toThrow(expect.objectContaining({ code: "invalid_staffing_response" }));
    const missingOther = structuredClone(snapshot) as unknown as Record<string, unknown>;
    delete missingOther["server_time_utc"];
    expect(() => parseStaffingSnapshot(missingOther)).toThrow(expect.objectContaining({ code: "invalid_staffing_response" }));
    // The service's own refusal of a write from another checkout is a trusted, definite rejection.
    const refused = scripted(409, { schema: API_SCHEMA, disclaimer: DISCLAIMER, error: { code: "staffing_contract_mismatch", detail: "console and service contract revisions differ" } });
    await expect(refused.client.submit(mutationFor(exchange("roster-committed")))).rejects.toMatchObject({ status: 409, code: "staffing_contract_mismatch" });
  });

  it("sends the pinned revision on every request and never a body at another revision", async () => {
    for (const { document } of successDocuments) {
      for (const item of document.exchanges) {
        if (item.method !== "POST") continue;
        const mutation = mutationFor(item);
        expect((mutation.body as unknown as Record<string, unknown>)["contract_revision"]).toBe(STAFFING_CONTRACT_REVISION);
        expect(parseStaffingRequestBody(mutation.body)).toEqual(mutation.body);
        for (const revision of [undefined, FOREIGN_REVISION]) {
          const altered = { ...mutation, body: withContractRevision(mutation.body, revision) } as StaffingMutation;
          expect(() => parseStaffingRequestBody(altered.body), item.name).toThrow(expect.objectContaining({ code: "staffing_invalid_request" }));
          const { calls, client } = scripted(item.http_status, item.body);
          await expect(client.submit(altered), item.name).rejects.toMatchObject({ status: 400, code: "staffing_invalid_request" });
          expect(calls).toHaveLength(0);
        }
      }
    }
  });

  it("pins the same contract fingerprint as the Python contract suite", () => {
    expect(contractFingerprint(join(EXAMPLES_DIR, ".."))).toBe(STAFFING_CONTRACT_FINGERPRINT);
  });

  it("binds the decoder's closed candidate key set to every candidate shape in schema.json and every example", () => {
    for (const name of ["CandidateValidProjection", "CandidateRejectedCoverageProjection", "CandidateRejectedOtherProjection"]) {
      const definition = schemaDocument.$defs[name];
      expect(definition.additionalProperties).toBe(false);
      expect(Object.keys(definition.properties ?? {}).sort()).toEqual(CANDIDATE_KEYS);
      expect([...(definition.required ?? [])].sort()).toEqual(CANDIDATE_KEYS);
    }
    const seen: string[][] = [];
    const visit = (value: unknown): void => {
      if (Array.isArray(value)) { value.forEach(visit); return; }
      if (value && typeof value === "object") {
        const record = value as Record<string, unknown>;
        if (Array.isArray(record.candidates)) {
          for (const candidate of record.candidates) seen.push(Object.keys(candidate as object).sort());
        }
        Object.values(record).forEach(visit);
      }
    };
    for (const { document } of successDocuments) visit(document);
    expect(seen.length).toBeGreaterThan(0);
    for (const keys of seen) expect(keys).toEqual(CANDIDATE_KEYS);

    const issued = structuredClone(exchange("suggestion-issued").body.data) as StaffingReceipt & { record: SuggestionIssuedRecord };
    const extra = structuredClone(issued);
    (extra.record.candidates[0] as unknown as Record<string, unknown>).review_note = "unknown";
    expect(() => parseStaffingReceipt(extra)).toThrow(expect.objectContaining({ code: "invalid_staffing_response" }));
    for (const key of CANDIDATE_KEYS) {
      const missing = structuredClone(issued);
      delete (missing.record.candidates[0] as unknown as Record<string, unknown>)[key];
      expect(() => parseStaffingReceipt(missing), key).toThrow(expect.objectContaining({ code: "invalid_staffing_response" }));
    }
  });

  it("accepts the frozen error inventory plus the documented body_too_large transport error and nothing beside them", async () => {
    const inventory = errors.exchanges.map((item) => item.body.error.code).sort();
    expect(inventory).toHaveLength(10);
    expect(inventory).toContain("staffing_contract_mismatch");
    expect(inventory).toEqual((schemaDocument.$defs.ErrorBody.properties?.code as { enum: string[] }).enum.slice().sort());
    for (const item of errors.exchanges) {
      await expect(scripted(item.http_status, item.body).client.current()).rejects.toMatchObject({ code: item.body.error.code });
    }
    const tooLarge = scripted(413, { ...errors.exchanges[0].body, error: { code: "body_too_large", detail: "request body exceeds limit" } }).client;
    await expect(tooLarge.current()).rejects.toMatchObject({ status: 413, code: "body_too_large" });
    for (const [status, code] of [[409, "staffing_unknown_code"], [413, "staffing_invalid_request"], [200, "body_too_large"]] as const) {
      const foreign = scripted(status, { ...errors.exchanges[0].body, error: { code, detail: "foreign pair" } }).client;
      await expect(foreign.current(), `${status}:${code}`).rejects.toMatchObject({ status, code: "invalid_staffing_response" });
    }
  });
});

describe("staffing v1 frozen wire contract", () => {
  it("freezes the exact runtime export surface", () => {
    expect(Object.keys(staffingRuntime).sort()).toEqual([
      "STAFFING_SCHEMA",
      "createStaffingClient",
      "newStaffingRequestId",
      "parseStaffingReceipt",
      "parseStaffingRequestBody",
      "parseStaffingSnapshot",
    ]);
    expectTypeOf<FrozenTypeSurface>().toBeArray();
    expectTypeOf<RosterImportRequest["availability"][number]["weekday"]>()
      .toEqualTypeOf<0 | 1 | 2 | 3 | 4 | 5 | 6>();
    expectTypeOf<RosterImportRequest["regular_assignments"][number]["weekday"]>()
      .toEqualTypeOf<0 | 1 | 2 | 3 | 4 | 5 | 6>();
    expectTypeOf<RosterImportRequest["coverage"][number]["weekday"]>()
      .toEqualTypeOf<0 | 1 | 2 | 3 | 4 | 5 | 6>();
  });

  it("loads all seven success documents and independently checks their closed containers", () => {
    expect(successDocuments.map(({ name }) => name)).toEqual([
      "cold-start.json",
      "exception-correction.json",
      "generation-success.json",
      "generation-unavailable.json",
      "manager-response.json",
      "result-unknown.json",
      "roster-import.json",
    ]);
    for (const { document } of successDocuments) {
      expect(Object.keys(document).sort()).toEqual(["exchanges", "schema"]);
      expect(document.schema).toBe("nxt-staffing-exchanges/v1");
      for (const item of document.exchanges) {
        expect(Object.keys(item).sort()).toEqual(
          item.method === "POST"
            ? ["body", "http_status", "method", "name", "request", "route_template"]
            : ["body", "http_status", "method", "name", "route_template"],
        );
        expect(Object.keys(item.body).sort()).toEqual(["data", "disclaimer", "schema"]);
        expect(item.body.schema).toBe(API_SCHEMA);
        expect(item.body.disclaimer).toBe(DISCLAIMER);
      }
    }
  });

  it("parses every frozen snapshot and all sixteen distinct receipt triples", () => {
    const triples = new Set<string>();
    for (const { document } of successDocuments) {
      for (const item of document.exchanges) {
        const data = item.body.data as Record<string, unknown>;
        if ("operation_kind" in data) {
          const receipt = parseStaffingReceipt(data);
          triples.add(`${receipt.operation_kind}/${receipt.state}/${receipt.record.record_kind}`);
        } else {
          expect(parseStaffingSnapshot(data)).toEqual(data);
        }
      }
    }
    expect(triples.size).toBe(16);
  });

  it("preserves the durable closed manager response after a fresh dated read", () => {
    const accepted = exchange("manager-accepted");
    const acceptedRequest = accepted.request as ManagerAcceptRequest;
    const refreshed = parseStaffingSnapshot(exchange("date-after-manager-response").body.data);
    expect(refreshed.generations[0].manager_response).toEqual({
      response_kind: acceptedRequest.kind,
      reason_code: acceptedRequest.reason_code,
      operator: acceptedRequest.operator,
      note: acceptedRequest.note,
      effective_plan: expect.objectContaining({ revision: 1, status: "CURRENT" }),
    });
  });

  it("rejects foreign markers, unknown nested keys, unsafe integers and malformed times", () => {
    const baseline = structuredClone(exchange("date-after-manager-response").body.data) as StaffingDateSnapshot;
    const mutations: Array<(value: StaffingDateSnapshot & Record<string, unknown>) => void> = [
      (value) => { value.schema = "nxt-staffing/v2" as typeof STAFFING_SCHEMA; },
      (value) => { value.environment = "PHYSICAL" as "SIMULATION"; },
      (value) => { value.mode = "AUTOMATIC" as "STAFFING_ADVISORY_ONLY"; },
      (value) => { value.server_time_utc = "2026-10-06T01:00:00Z"; },
      (value) => { value.service_date = "2026-02-30"; },
      (value) => { value.revisions.roster = Number.MAX_SAFE_INTEGER + 1; },
      (value) => { (value.context as unknown as Record<string, unknown>).provider_alias = "private"; },
      (value) => { (value.generations[0] as unknown as Record<string, unknown>).alias_nonce_digest = "private"; },
      (value) => { (value.generations[0] as unknown as { state: string }).state = "UNKNOWN"; },
      (value) => {
        (value.generation_capability as unknown as Record<string, unknown>).status = ["READY"];
      },
      (value) => { delete (value as unknown as Record<string, unknown>).revisions; },
    ];
    for (const mutate of mutations) {
      const value = structuredClone(baseline) as StaffingDateSnapshot & Record<string, unknown>;
      mutate(value);
      expect(() => parseStaffingSnapshot(value)).toThrow(ManagerApiError);
    }
  });

  it.each(["request_id", "suggestion_id", "operation_id"] as const)(
    "rejects duplicate generation %s values",
    (identityKey) => {
      const snapshot = structuredClone(
        exchange("date-after-manager-response").body.data,
      ) as StaffingDateSnapshot;
      const first = snapshot.generations[0];
      const second = {
        ...structuredClone(first),
        request_id: "request-generation-second",
        suggestion_id: "generation-second",
        operation_id: "staffing-event-generation-second",
        manager_response: null,
      } as GenerationProjection;
      second[identityKey] = first[identityKey];
      snapshot.generations.push(second);

      expect(() => parseStaffingSnapshot(snapshot)).toThrow(ManagerApiError);
    },
  );

  it("accepts a retry that names one earlier RESULT_UNKNOWN generation", () => {
    const snapshot = retryChainSnapshot();
    expect(parseStaffingSnapshot(snapshot)).toEqual(snapshot);
  });

  it("rejects a retry whose parent is missing, later, or not RESULT_UNKNOWN", () => {
    const missing = retryChainSnapshot();
    missing.generations[1].retry_of = "generation-missing-parent";

    const later = retryChainSnapshot();
    later.generations.reverse();

    const nonUnknown = retryChainSnapshot();
    nonUnknown.generations[0] = {
      ...structuredClone(nonUnknown.generations[1]),
      suggestion_id: "generation-result-unknown-parent",
      request_id: "request-non-unknown-parent",
      operation_id: "staffing-event-non-unknown-parent",
      retry_of: null,
    } as GenerationProjection;

    for (const snapshot of [missing, later, nonUnknown]) {
      expect(() => parseStaffingSnapshot(snapshot)).toThrow(ManagerApiError);
    }
  });

  it("rejects two retries that name the same RESULT_UNKNOWN parent", () => {
    const snapshot = retryChainSnapshot();
    const secondRetry = {
      ...structuredClone(snapshot.generations[1]),
      suggestion_id: "generation-retry-child-2",
      request_id: "request-retry-child-2",
      operation_id: "staffing-event-retry-child-2",
    } as GenerationProjection;
    snapshot.generations.push(secondRetry);

    expect(() => parseStaffingSnapshot(snapshot)).toThrow(ManagerApiError);
  });

  it("rejects malformed candidate, coverage and provenance semantics", () => {
    const issued = structuredClone(exchange("suggestion-issued").body.data) as StaffingReceipt & {
      record: SuggestionIssuedRecord;
    };
    const invalidValues: unknown[] = [];
    const gap = structuredClone(issued);
    gap.record.coverage_gaps = [{
      role_code: "RANGE_ATTENDANT",
      area_code: "RANGE_A",
      start_at: "2026-10-06T09:00:00+08:00",
      end_at: "2026-10-06T10:00:00+08:00",
      required_count: 1,
      assigned_count: 1,
      rejection_code: "COVERAGE_GAP",
    }];
    invalidValues.push(gap);
    const index = structuredClone(issued);
    index.record.candidates[0].candidate_index = 2;
    invalidValues.push(index);
    const digest = structuredClone(issued);
    digest.record.provenance[0].input_digest = "F".repeat(64);
    invalidValues.push(digest);
    const alias = structuredClone(issued);
    (alias.record.provenance[0] as unknown as Record<string, unknown>).worker_alias = "W1";
    invalidValues.push(alias);
    const sparse = structuredClone(issued);
    delete sparse.record.candidates[0];
    invalidValues.push(sparse);
    for (const value of invalidValues) expect(() => parseStaffingReceipt(value)).toThrow(ManagerApiError);
  });

  it("requires the closed review fields on every candidate without loosening the raw text bounds", () => {
    const issued = structuredClone(exchange("suggestion-issued").body.data) as StaffingReceipt & {
      record: SuggestionIssuedRecord;
    };
    const baseline = issued.record.candidates[0];
    expect(baseline.rationale).toMatch(/worker_[0-9a-f]{24}/);
    expect(baseline.rationale_local).not.toMatch(/worker_|assignment_/);
    expect(baseline.actionability).toBe("CURRENT");
    expect(parseStaffingReceipt(issued)).toEqual(issued);

    const mutations: Array<(candidate: Record<string, unknown>) => void> = [
      (candidate) => { delete candidate.actionability; },
      (candidate) => { delete candidate.action_window_end_at; },
      (candidate) => { delete candidate.rationale_local; },
      (candidate) => { delete candidate.operational_warnings_local; },
      (candidate) => { candidate.actionability = "HISTORICAL"; },
      (candidate) => { candidate.actionability = "expired"; },
      (candidate) => { candidate.action_window_end_at = "2026-10-06T17:00:00+00:00"; },
      (candidate) => { candidate.action_window_end_at = "2026-10-06T17:00:30Z"; },
      (candidate) => { candidate.rationale_local = "x".repeat(2001); },
      (candidate) => { candidate.rationale_local = "line\nbreak"; },
      (candidate) => { candidate.operational_warnings_local = ["unmatched"]; },
      (candidate) => { candidate.rationale = "y".repeat(281); },
      (candidate) => { candidate.review_note = "unknown field"; },
    ];
    for (const [index, mutate] of mutations.entries()) {
      const value = structuredClone(issued);
      mutate(value.record.candidates[0] as unknown as Record<string, unknown>);
      expect(() => parseStaffingReceipt(value), `mutation ${index}`).toThrow(ManagerApiError);
    }
  });

  it("requires every row in a two-attempt provenance sequence to share one input digest", () => {
    const receipt = structuredClone(exchange("suggestion-issued").body.data) as StaffingReceipt & {
      record: SuggestionIssuedRecord;
    };
    const successful = receipt.record.provenance[0];
    receipt.record.provenance = [
      {
        ...successful,
        attempt_index: 0,
        provider: "OPENAI",
        region: "GLOBAL",
        route_role: "PRIMARY",
        route_id: "global-openai-v1",
        model_id: "gpt-example",
        failure_code: "RATE_LIMITED",
        retryable: true,
        provider_request_id: null,
        finish_reason: null,
        output_digest: null,
        input_tokens: null,
        output_tokens: null,
      },
      {
        ...successful,
        attempt_index: 1,
        provider: "ANTHROPIC",
        region: "GLOBAL",
        route_role: "BACKUP",
        route_id: "global-anthropic-v1",
        model_id: "claude-example",
        finish_reason: "tool_use",
      },
    ];
    expect(parseStaffingReceipt(receipt)).toEqual(receipt);
    receipt.record.provenance[1].input_digest = "e".repeat(64);
    expect(() => parseStaffingReceipt(receipt)).toThrow(ManagerApiError);
  });

  it("validates every POST request fixture before accepting it", () => {
    let count = 0;
    for (const { document } of successDocuments) {
      for (const item of document.exchanges) {
        if (item.method !== "POST") continue;
        expect(parseStaffingRequestBody(item.request)).toEqual(item.request);
        count += 1;
      }
    }
    expect(count).toBe(17);
    const bad = structuredClone(exchange("exception-recorded").request) as Record<string, unknown>;
    bad.extra = "no";
    expect(() => parseStaffingRequestBody(bad)).toThrow(ManagerApiError);
  });
});

describe("staffing same-origin client", () => {
  it("reads exact root and an encoded dated route with no-store and an abort signal", async () => {
    const current = exchange("current-empty");
    const first = scripted(200, current.body);
    await expect(first.client.current()).resolves.toEqual(current.body.data);
    expect(first.calls[0].url).toBe("/api/v1/staffing");
    expect(first.calls[0].init).toMatchObject({
      cache: "no-store",
      mode: "same-origin",
      redirect: "error",
    });
    expect(first.calls[0].init?.signal).toBeInstanceOf(AbortSignal);

    const dated = exchange("date-empty");
    const second = scripted(200, dated.body);
    await expect(second.client.date("2026-10-07")).resolves.toEqual(dated.body.data);
    expect(second.calls[0].url).toBe("/api/v1/staffing/dates/2026-10-07");
  });

  it("keeps the eight-second abort active while the response body is being read", async () => {
    vi.useFakeTimers();
    let abortReason: unknown;
    try {
      const fetchImpl: FetchLike = async (_url, init) => new Response(
        new ReadableStream({
          start(controller) {
            init?.signal?.addEventListener("abort", () => {
              abortReason = init.signal?.reason;
              controller.error(abortReason);
            }, { once: true });
          },
        }),
        { status: 200 },
      );
      const pending = createStaffingClient(fetchImpl).current()
        .then(() => ({ error: undefined as unknown }), (error: unknown) => ({ error }));
      await vi.advanceTimersByTimeAsync(8000);
      const { error } = await pending;
      expect(error).toBe(abortReason);
      expect(error).toMatchObject({ name: "AbortError" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("submits every frozen mutation and derives every route internally", async () => {
    for (const { document } of successDocuments) {
      for (const item of document.exchanges) {
        if (item.method !== "POST") continue;
        const { calls, client } = scripted(item.http_status, item.body);
        await expect(client.submit(mutationFor(item))).resolves.toEqual(item.body.data);
        expect(calls[0].init?.method).toBe("POST");
        expect(calls[0].init?.cache).toBe("no-store");
        expect(calls[0].init?.headers).toEqual({ "Content-Type": "application/json" });
        expect(JSON.parse(String(calls[0].init?.body))).toEqual(item.request);
      }
    }
  });

  it("encodes raw target and lookup identifiers exactly once", async () => {
    const manager = exchange("manager-accepted");
    const submit = scripted(200, {
      ...manager.body,
      data: {
        ...(manager.body.data as object),
        record: { ...((manager.body.data as StaffingReceipt).record as object), suggestion_id: "odd:id" },
      },
    });
    await submit.client.submit({
      operationKind: "manager-response",
      targetId: "odd:id",
      body: manager.request as ManagerAcceptRequest,
    });
    expect(submit.calls[0].url).toBe("/api/v1/staffing/suggestions/odd%3Aid/accept");

    const interrupted = exchange("generation-interrupted");
    const lookup = scripted(200, {
      ...interrupted.body,
      data: { ...(interrupted.body.data as object), request_id: "request:id" },
    });
    await lookup.client.lookup("suggestion-generate", "request:id");
    expect(lookup.calls[0].url).toBe(
      "/api/v1/staffing/requests/suggestion-generate/request%3Aid",
    );
  });

  it("requires lookup disposition and both lookup correlation values", async () => {
    const interrupted = exchange("generation-interrupted");
    for (const patch of [
      { disposition: "created" },
      { operation_kind: "roster-import" },
      { request_id: "different" },
    ]) {
      const client = scripted(200, {
        ...interrupted.body,
        data: { ...(interrupted.body.data as object), ...patch },
      }).client;
      await expect(client.lookup("suggestion-generate", "request-generate-crash-1")).rejects.toMatchObject({
        status: 200,
        code: "invalid_staffing_response",
      });
    }
  });

  it("recovers a mutation through the lookup route and fully correlates its receipt", async () => {
    const interrupted = exchange("generation-interrupted");
    const body = {
      ...(structuredClone(exchange("generation-reserved").request) as SuggestionGenerateRequest),
      request_id: "request-result-unknown",
    };
    const mutation: StaffingMutation = { operationKind: "suggestion-generate", body };
    const valid = scripted(200, interrupted.body);

    await expect(valid.client.lookupMutation(mutation)).resolves.toEqual(interrupted.body.data);
    expect(valid.calls[0].url).toBe(
      "/api/v1/staffing/requests/suggestion-generate/request-result-unknown",
    );

    const mismatched = structuredClone(interrupted.body);
    (mismatched.data as StaffingReceipt & { record: GenerationInterruptedRecord })
      .record.service_date = "2026-10-07";
    await expect(scripted(200, mismatched).client.lookupMutation(mutation)).rejects.toMatchObject({
      status: 200,
      code: "invalid_staffing_response",
    });
  });

  it("validates and detaches a mutation before lookup recovery", async () => {
    const interrupted = exchange("generation-interrupted");
    const body = {
      ...(structuredClone(exchange("generation-reserved").request) as SuggestionGenerateRequest),
      request_id: "request-result-unknown",
    };
    const mutation: StaffingMutation = { operationKind: "suggestion-generate", body };
    let release: ((value: Response) => void) | undefined;
    const fetchImpl: FetchLike = () => new Promise((resolve) => { release = resolve; });
    const client = createStaffingClient(fetchImpl);
    const pending = client.lookupMutation(mutation);
    mutation.body.service_date = "2026-10-07";
    release?.(response(200, interrupted.body));

    await expect(pending).resolves.toEqual(interrupted.body.data);

    const invalid = structuredClone(mutation) as StaffingMutation & Record<string, unknown>;
    invalid.extra = "private";
    const noFetch = vi.fn<FetchLike>();
    await expect(createStaffingClient(noFetch).lookupMutation(invalid)).rejects.toMatchObject({
      status: 400,
      code: "staffing_invalid_request",
    });
    expect(noFetch).not.toHaveBeenCalled();
  });

  it("correlates mutation operation, request and route-specific record fields", async () => {
    const roster = exchange("roster-committed");
    const mutation = mutationFor(roster);
    const badBodies = [
      { request_id: "different" },
      { operation_kind: "exception-record" },
      { record: { ...((roster.body.data as StaffingReceipt).record as object), worker_count: 999 } },
    ];
    for (const patch of badBodies) {
      const data = { ...(roster.body.data as object), ...patch };
      const client = scripted(200, { ...roster.body, data }).client;
      await expect(client.submit(mutation)).rejects.toMatchObject({ status: 200, code: "invalid_staffing_response" });
    }
  });

  it("correlates every exception, generation and manager record with its exact request", async () => {
    const cases: Array<{ fixture: string; mutate: (data: Record<string, unknown>) => void }> = [
      {
        fixture: "exception-recorded",
        mutate: (data) => {
          const record = data.record as { exception: { staff_id: string } };
          record.exception.staff_id = "staff-999";
        },
      },
      {
        fixture: "exception-cancelled",
        mutate: (data) => {
          (data.record as { exception_id: string }).exception_id = "exception-999";
        },
      },
      {
        fixture: "exception-corrected",
        mutate: (data) => {
          (data.record as { replacement: { unavailable_start_at: string } }).replacement.unavailable_start_at =
            "2026-10-06T15:31:00+08:00";
        },
      },
      {
        fixture: "generation-reserved",
        mutate: (data) => {
          (data.record as { basis: { roster: number } }).basis.roster += 1;
        },
      },
      {
        fixture: "manager-accepted",
        mutate: (data) => {
          (data.record as { operator: string }).operator = "another-manager";
        },
      },
    ];
    for (const item of cases) {
      const source = exchange(item.fixture);
      const data = structuredClone(source.body.data) as Record<string, unknown>;
      item.mutate(data);
      const client = scripted(source.http_status, { ...source.body, data }).client;
      await expect(client.submit(mutationFor(source))).rejects.toMatchObject({
        status: source.http_status,
        code: "invalid_staffing_response",
      });
    }
  });

  it("accepts only 202 for generation and only 200 for every other success", async () => {
    const generation = exchange("generation-reserved");
    await expect(scripted(200, generation.body).client.submit(mutationFor(generation))).rejects.toMatchObject({
      status: 200,
      code: "invalid_staffing_response",
    });
    const roster = exchange("roster-committed");
    await expect(scripted(202, roster.body).client.submit(mutationFor(roster))).rejects.toMatchObject({
      status: 202,
      code: "invalid_staffing_response",
    });
  });

  it("rejects an invalid local mutation as a no-fetch 400", async () => {
    const fetchImpl = vi.fn<FetchLike>();
    const client = createStaffingClient(fetchImpl);
    const mutation = mutationFor(exchange("exception-recorded"));
    (mutation.body as unknown as Record<string, unknown>).extra = "private-value";
    const error = await client.submit(mutation).catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ManagerApiError);
    expect(error).toMatchObject({ status: 400, code: "staffing_invalid_request" });
    expect(String(error)).not.toContain("private-value");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("validates the detached request value that is actually serialized", async () => {
    const source = exchange("roster-committed");
    const body = structuredClone(source.request) as RosterImportRequest;
    const acceptedWorker = body.workers[0];
    let reads = 0;
    const statefulWorkers: RosterImportRequest["workers"] = [];
    Object.defineProperty(statefulWorkers, "0", {
      enumerable: true,
      get() {
        reads += 1;
        return reads === 1
          ? acceptedWorker
          : { ...acceptedWorker, staff_id: "invalid/path" };
      },
    });
    statefulWorkers.length = 1;
    body.workers = statefulWorkers;

    const { calls, client } = scripted(200, source.body);
    await expect(client.submit({ operationKind: "roster-import", body })).resolves.toEqual(source.body.data);
    expect(reads).toBe(1);
    expect(JSON.parse(String(calls[0].init?.body)).workers).toEqual([acceptedWorker]);
  });

  it("rejects invalid raw path segments locally without performing fetch", async () => {
    const fetchImpl = vi.fn<FetchLike>();
    const client = createStaffingClient(fetchImpl);
    const cancel = mutationFor(exchange("exception-cancelled"));
    expect(cancel.operationKind).toBe("exception-cancel");
    if (cancel.operationKind !== "exception-cancel") throw new Error("fixture mismatch");
    await expect(client.submit({ ...cancel, targetId: "bad/id" })).rejects.toMatchObject({
      status: 400,
      code: "staffing_invalid_request",
    });
    await expect(client.date("2026-02-30")).rejects.toMatchObject({
      status: 400,
      code: "staffing_invalid_request",
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("surfaces all ten trusted staffing error pairs and exact manager body-too-large", async () => {
    expect(errors.schema).toBe("nxt-staffing-errors/v1");
    expect(errors.exchanges).toHaveLength(10);
    expect(errors.exchanges.map((item) => item.body.error.code)).toContain("staffing_exception_overlap");
    expect(errors.exchanges.map((item) => item.body.error.code)).toContain("staffing_suggestion_expired");
    expect(errors.exchanges.map((item) => item.body.error.code)).toContain("staffing_contract_mismatch");
    for (const item of errors.exchanges) {
      expect(Object.keys(item).sort()).toEqual(["body", "http_status", "method", "name", "route_template"]);
      const client = scripted(item.http_status, item.body).client;
      await expect(client.current()).rejects.toMatchObject({
        status: item.http_status,
        code: item.body.error.code,
      });
    }
    const tooLarge = scripted(413, {
      schema: API_SCHEMA,
      disclaimer: DISCLAIMER,
      error: { code: "body_too_large", detail: "request body exceeds limit" },
    }).client;
    await expect(tooLarge.current()).rejects.toMatchObject({ status: 413, code: "body_too_large" });
  });

  it("fails closed on mutated error envelopes and preserves their actual status", async () => {
    const valid = errors.exchanges[0].body;
    const bodies = [
      { ...valid, schema: "foreign/v1" },
      { ...valid, disclaimer: "secret" },
      { ...valid, error: { ...valid.error, code: "unknown" } },
      { ...valid, extra: true },
    ];
    for (const body of bodies) {
      await expect(scripted(400, body).client.current()).rejects.toMatchObject({
        status: 400,
        code: "invalid_staffing_response",
      });
    }
    await expect(scripted(409, valid).client.current()).rejects.toMatchObject({
      status: 409,
      code: "invalid_staffing_response",
    });
  });

  it("never echoes a trusted server error detail", async () => {
    const body = {
      ...errors.exchanges[0].body,
      error: { code: "staffing_invalid_request", detail: "SECRET-NAME-NOTE-API-KEY" },
    };
    const error = await scripted(400, body).client.current().catch((cause: unknown) => cause);
    expect(error).toMatchObject({ status: 400, code: "staffing_invalid_request" });
    expect(String(error)).not.toContain("SECRET-NAME-NOTE-API-KEY");
  });

  it("rejects literal and escaped duplicate JSON keys before envelope decoding", async () => {
    const valid = JSON.stringify(exchange("current-empty").body);
    const duplicate = valid.replace(
      `{"schema":"${API_SCHEMA}"`,
      `{"schema":"${API_SCHEMA}","schema":"${API_SCHEMA}"`,
    );
    const escapedDuplicate = valid.replace(
      `{"schema":"${API_SCHEMA}"`,
      `{"schema":"${API_SCHEMA}","sch\\u0065ma":"${API_SCHEMA}"`,
    );
    for (const text of [duplicate, escapedDuplicate]) {
      const client = createStaffingClient(async () => new Response(text, { status: 200 }));
      await expect(client.current()).rejects.toMatchObject({ status: 200, code: "invalid_staffing_response" });
    }
  });

  it.each([200, 202, 413, 503])("preserves status %i for unreadable or malformed responses", async (status) => {
    const unreadable = createStaffingClient(async () => new Response("not json", { status }));
    await expect(unreadable.current()).rejects.toMatchObject({ status, code: "invalid_staffing_response" });
    const malformed = createStaffingClient(async () => response(status, { raw_model_output: "sensitive" }));
    const error = await malformed.current().catch((cause: unknown) => cause);
    expect(error).toMatchObject({ status, code: "invalid_staffing_response" });
    expect(String(error)).not.toContain("sensitive");
  });

  it("maps an already-consumed response body to a status-preserving invalid response", async () => {
    const consumed = response(202, exchange("generation-reserved").body);
    await consumed.text();
    const error = await createStaffingClient(async () => consumed)
      .current()
      .catch((cause: unknown) => cause);
    expect(error).toMatchObject({ status: 202, code: "invalid_staffing_response" });
  });

  it("rejects a complete valid serialized request over 1 MiB before fetch", async () => {
    const base = largeRoster(4096, 1);
    const fetchImpl = vi.fn<FetchLike>();
    const error = await createStaffingClient(fetchImpl)
      .submit({ operationKind: "roster-import", body: base })
      .catch((cause: unknown) => cause);
    expect(new TextEncoder().encode(JSON.stringify(base)).byteLength).toBeGreaterThan(1024 * 1024);
    expect(error).toMatchObject({ status: 413, code: "body_too_large" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("allows and sends the exact serialized 1 MiB boundary", async () => {
    const body = largeRoster(3894, 184);
    const serialized = JSON.stringify(body);
    expect(new TextEncoder().encode(serialized).byteLength).toBe(1024 * 1024);
    const source = exchange("roster-committed");
    const envelope = structuredClone(source.body);
    const data = envelope.data as StaffingReceipt & { record: RosterImportedRecord };
    data.record.worker_count = body.workers.length;
    const { calls, client } = scripted(200, envelope);
    await expect(client.submit({ operationKind: "roster-import", body })).resolves.toEqual(envelope.data);
    expect(calls).toHaveLength(1);
    expect(calls[0].init?.body).toBe(serialized);
  });

  it("generates closed request identifiers using the requested operation kind", () => {
    const id = newStaffingRequestId("exception-record");
    expect(id).toMatch(/^staffing-exception-record-[0-9a-f-]{36}$/);
    expect(parseStaffingRequestBody({
      ...(exchange("exception-recorded").request as object),
      request_id: id,
    })).toMatchObject({ request_id: id });
  });
});
