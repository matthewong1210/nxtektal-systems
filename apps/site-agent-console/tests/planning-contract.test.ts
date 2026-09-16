import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { API_SCHEMA, ManagerApiError, type FetchLike } from "../lib/api";
import {
  createPlanningClient,
  newRequestId,
  parsePlanningSnapshot,
  parseReceipt,
  PLANNING_SCHEMA,
  type MutationReceipt,
  type PlanningSnapshot,
} from "../lib/planning";

/** The console consumes the frozen contract checked into the repository:
 * every example exchange must round-trip through the typed client. */
const EXAMPLES_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "..",
  "simulation",
  "docs",
  "contracts",
  "planning-v1",
  "examples",
);

type Exchange = {
  request: { method: string; path: string; schema_ref?: string; body?: unknown };
  response: { http_status: number; body: { data?: unknown; error?: { code: string; detail: string } } };
};

const examples: Record<string, Exchange[]> = Object.fromEntries(
  readdirSync(EXAMPLES_DIR)
    .filter((name) => name.endsWith(".json"))
    .map((name) => [
      name.replace(/\.json$/, ""),
      (JSON.parse(readFileSync(join(EXAMPLES_DIR, name), "utf-8")) as { exchanges: Exchange[] }).exchanges,
    ]),
);

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function scripted(exchange: Exchange) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return jsonResponse(exchange.response.http_status, exchange.response.body);
  };
  return { calls, client: createPlanningClient(fetchImpl) };
}

describe("planning v1 contract examples", () => {
  it("loads all six frozen example files", () => {
    expect(Object.keys(examples).sort()).toEqual([
      "conflict",
      "duplicate-confirmation",
      "expired",
      "missing-data",
      "success",
      "unknown-result",
    ]);
  });

  it("parses the empty cold-start snapshot and the completed snapshot", () => {
    const gets = examples.success.filter((e) => e.request.method === "GET" && e.request.path === "/api/v1/planning");
    expect(gets).toHaveLength(2);
    const empty = parsePlanningSnapshot(gets[0].response.body.data);
    expect(empty.schema).toBe(PLANNING_SCHEMA);
    expect(empty.latest_input).toBeNull();
    expect(empty.plans).toEqual([]);
    expect(empty.context).toEqual({
      site_id: "pilot-course-a",
      deployment_id: "pilot-a-edge-task-sim-v0",
      site_timezone: "Asia/Shanghai",
      zone_ids: ["Z1"],
      robot_ids: ["picker-01"],
    });
    const full = parsePlanningSnapshot(gets[1].response.body.data);
    expect(full.latest_input?.revision).toBe(1);
    expect(full.plans[0].current_status).toBe("CONFIRMED");
    expect(full.plans[0].scenarios.map((s) => s.level)).toEqual(["low", "typical", "high"]);
    expect(full.confirmations[0].task_id).toBe("task_example_001");
    expect(full.outcomes.map((o) => o.request.stage)).toEqual(["COLLECTED", "UNLOADED", "WASHED", "SUPPLIED"]);
  });

  it("fails closed on a foreign schema, mode, or environment", () => {
    const data = examples.success[0].response.body.data as Record<string, unknown>;
    for (const patch of [{ schema: "nxt-planning/v2" }, { environment: "PHYSICAL" }, { mode: "AUTOMATIC" }, { context: null }, { plans: null }]) {
      expect(() => parsePlanningSnapshot({ ...data, ...patch })).toThrow(ManagerApiError);
    }
  });

  it("parses every successful mutation receipt in the examples", () => {
    let receipts = 0;
    for (const exchanges of Object.values(examples)) {
      for (const exchange of exchanges) {
        if (exchange.response.http_status !== 200 || exchange.request.path === "/api/v1/planning") continue;
        const receipt = parseReceipt(exchange.response.body.data);
        expect(["created", "duplicate"]).toContain(receipt.disposition);
        expect(receipt.request_id.length).toBeGreaterThan(0);
        receipts += 1;
      }
    }
    expect(receipts).toBeGreaterThanOrEqual(12);
  });

  it("submits each POST example to its route and returns the typed receipt", async () => {
    for (const exchanges of Object.values(examples)) {
      for (const exchange of exchanges) {
        if (exchange.request.method !== "POST" || exchange.response.http_status !== 200) continue;
        const kind = exchange.request.path.replace("/api/v1/planning/", "") as "inputs" | "plans" | "confirmations" | "outcomes";
        const { calls, client } = scripted(exchange);
        const receipt: MutationReceipt = await client.submit(kind, exchange.request.body as never);
        expect(calls[0]).toMatchObject({ url: exchange.request.path, method: "POST", body: exchange.request.body });
        expect(receipt).toEqual(exchange.response.body.data);
      }
    }
  });

  it("surfaces every example error as a typed ManagerApiError with its contract code", async () => {
    const seen = new Set<string>();
    for (const exchanges of Object.values(examples)) {
      for (const exchange of exchanges) {
        if (exchange.response.http_status === 200) continue;
        const kind = exchange.request.path.replace("/api/v1/planning/", "") as "inputs" | "plans" | "confirmations" | "outcomes";
        const { client } = scripted(exchange);
        const error = await client.submit(kind, exchange.request.body as never).catch((cause: unknown) => cause);
        expect(error).toBeInstanceOf(ManagerApiError);
        expect((error as ManagerApiError).code).toBe(exchange.response.body.error?.code);
        expect((error as ManagerApiError).status).toBe(exchange.response.http_status);
        seen.add((error as ManagerApiError).code);
      }
    }
    expect([...seen].sort()).toEqual(["planning_conflict", "planning_expired", "planning_not_ready", "planning_result_unknown"]);
  });

  it("looks a request up by ID as a single encoded path segment", async () => {
    const lookup = examples["unknown-result"][1];
    const { calls, client } = scripted(lookup);
    const receipt = await client.lookup("demo-confirm-001");
    expect(calls[0]).toMatchObject({ url: "/api/v1/planning/requests/demo-confirm-001", method: "GET" });
    expect(receipt.disposition).toBe("duplicate");
    expect(receipt.request_id).toBe("demo-confirm-001");
    const { calls: encoded } = scripted(lookup);
    const client2 = createPlanningClient(async (url, init) => {
      encoded.push({ url, method: init?.method ?? "GET", body: undefined });
      return jsonResponse(200, lookup.response.body);
    });
    await client2.lookup("odd/id with space?&#");
    expect(encoded[0].url).toBe("/api/v1/planning/requests/odd%2Fid%20with%20space%3F%26%23");
  });

  it("reads the snapshot through the Manager API envelope and rejects foreign envelopes", async () => {
    const snapshot = examples.success[0];
    const { calls, client } = scripted(snapshot);
    const data: PlanningSnapshot = await client.snapshot();
    expect(calls[0]).toMatchObject({ url: "/api/v1/planning", method: "GET" });
    expect(data.server_time_utc).toBe("2026-09-16T08:00:00Z");
    const foreign = createPlanningClient(async () => jsonResponse(200, { schema: "other/v9", disclaimer: "x", data: {} }));
    await expect(foreign.snapshot()).rejects.toMatchObject({ code: "schema_mismatch" });
    const notFound = createPlanningClient(async () =>
      jsonResponse(404, { schema: API_SCHEMA, disclaimer: "x", error: { code: "not_found", detail: "unknown API path" } }),
    );
    await expect(notFound.snapshot()).rejects.toMatchObject({ code: "not_found", status: 404 });
  });

  it("generates one distinct stable request ID per call, prefixed by kind", () => {
    const a = newRequestId("confirmations");
    const b = newRequestId("confirmations");
    expect(a).toMatch(/^confirmations-[0-9a-f-]{36}$/);
    expect(a).not.toBe(b);
  });
});
