import { describe, expect, it } from "vitest";

import {
  API_SCHEMA,
  createClient,
  ManagerApiError,
  type FetchLike,
} from "../lib/api";
import { formatAge, formatBalls, scenarioClock } from "../lib/format";

function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("manager API client", () => {
  it("unwraps a schema-checked envelope", async () => {
    const fetchImpl: FetchLike = async () =>
      jsonResponse(200, {
        schema: API_SCHEMA,
        disclaimer: "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA",
        data: { service_state: "serving" },
      });
    const client = createClient(fetchImpl);
    const health = await client.health();
    expect(health.service_state).toBe("serving");
  });

  it("rejects a foreign schema", async () => {
    const fetchImpl: FetchLike = async () =>
      jsonResponse(200, { schema: "other/v9", disclaimer: "", data: {} });
    const client = createClient(fetchImpl);
    await expect(client.health()).rejects.toMatchObject({
      code: "schema_mismatch",
    });
  });

  it("surfaces service error codes and statuses", async () => {
    const fetchImpl: FetchLike = async () =>
      jsonResponse(409, {
        schema: API_SCHEMA,
        disclaimer: "",
        error: { code: "advance_refused", detail: "exhausted" },
      });
    const client = createClient(fetchImpl);
    try {
      await client.advance();
      expect.unreachable("advance must reject");
    } catch (error) {
      expect(error).toBeInstanceOf(ManagerApiError);
      expect((error as ManagerApiError).code).toBe("advance_refused");
      expect((error as ManagerApiError).status).toBe(409);
    }
  });

  it("posts manager responses to the versioned recommendation path", async () => {
    const calls: { input: string; init?: RequestInit }[] = [];
    const fetchImpl: FetchLike = async (input, init) => {
      calls.push({ input, init });
      return jsonResponse(200, {
        schema: API_SCHEMA,
        disclaimer: "",
        data: { case_status: "accepted" },
      });
    };
    const client = createClient(fetchImpl);
    await client.respond("rec_abc", "accept", {
      operator_id: "mgr-01",
      reason_code: "ok",
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].input).toBe("/api/v0/recommendations/rec_abc/accept");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      operator_id: "mgr-01",
      reason_code: "ok",
    });
  });

  it("maps non-JSON responses to a readable error", async () => {
    const fetchImpl: FetchLike = async () =>
      new Response("<html>proxy error</html>", { status: 502 });
    const client = createClient(fetchImpl);
    await expect(client.state()).rejects.toMatchObject({
      code: "unreadable_response",
      status: 502,
    });
  });

  it("maps a null JSON payload to the typed error, not a TypeError", async () => {
    const fetchImpl: FetchLike = async () => jsonResponse(200, null);
    const client = createClient(fetchImpl);
    await expect(client.health()).rejects.toBeInstanceOf(ManagerApiError);
    await expect(client.health()).rejects.toMatchObject({
      code: "unreadable_response",
      status: 200,
    });
  });

  it("maps null payloads on error statuses to the typed error too", async () => {
    const fetchImpl: FetchLike = async () => jsonResponse(500, null);
    const client = createClient(fetchImpl);
    await expect(client.advance()).rejects.toMatchObject({
      code: "unreadable_response",
      status: 500,
    });
  });

  it("maps primitive and array payloads to the typed error", async () => {
    for (const payload of [42, "ok", true, []]) {
      const fetchImpl: FetchLike = async () => jsonResponse(200, payload);
      const client = createClient(fetchImpl);
      await expect(client.state()).rejects.toMatchObject({
        code: "unreadable_response",
      });
    }
  });
});

describe("formatting helpers", () => {
  it("renders scenario clocks deterministically", () => {
    expect(scenarioClock(63000)).toBe("17:30");
    expect(scenarioClock(0)).toBe("00:00");
    expect(scenarioClock(null)).toBe("—");
    expect(scenarioClock(-1)).toBe("—");
  });

  it("renders ages without inventing freshness", () => {
    expect(formatAge(5)).toBe("5s");
    expect(formatAge(3605)).toBe("1h 0m");
    expect(formatAge(null)).toBe("unknown");
  });

  it("renders ball counts without inventing zeros", () => {
    expect(formatBalls(2400)).toBe("2,400");
    expect(formatBalls(null)).toBe("—");
  });
});
