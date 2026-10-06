import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { API_SCHEMA, DISCLAIMER, ManagerApiError, type FetchLike } from "../lib/api";
import * as staffingRuntime from "../lib/staffing";
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

  it("surfaces all seven trusted staffing error pairs and exact manager body-too-large", async () => {
    expect(errors.schema).toBe("nxt-staffing-errors/v1");
    expect(errors.exchanges).toHaveLength(7);
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
    const body = largeRoster(3895, 2);
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
