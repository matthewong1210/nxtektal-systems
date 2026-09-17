import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { OutcomeRecord, PlanRecord } from "../lib/planning";
import { focusPlan, latestOutcomes, outcomeHistory } from "../lib/planning-view";

const SUCCESS = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts", "planning-v1", "examples", "success.json"), "utf-8"),
) as { exchanges: { request: { schema_ref?: string }; response: { body: { data?: { record?: unknown } } } }[] };
const basePlan = SUCCESS.exchanges.find((e) => e.request.schema_ref === "#/$defs/PlanRequest")!.response.body.data!.record as PlanRecord;
const baseOutcome = SUCCESS.exchanges.find((e) => e.request.schema_ref === "#/$defs/OutcomeRequest")!.response.body.data!.record as OutcomeRecord;

const plan = (planId: string, version: number, generatedAt: string): PlanRecord => ({
  ...basePlan,
  plan_id: planId,
  version,
  generated_at_utc: generatedAt,
});
const key = (item: PlanRecord | null) => (item ? `${item.plan_id}:v${item.version}` : null);

describe("focusPlan ordering", () => {
  it("prefers the later real instant even when the earlier text sorts higher", () => {
    const v1 = plan("plan_a", 1, "2026-09-17T08:00:00Z");
    const v2 = plan("plan_a", 2, "2026-09-17T08:00:00.500000Z");
    expect(key(focusPlan([v1, v2]))).toBe("plan_a:v2");
    expect(key(focusPlan([v2, v1]))).toBe("plan_a:v2");
  });

  it("distinguishes microseconds within the same second", () => {
    const older = plan("plan_a", 1, "2026-09-17T08:00:00.000001Z");
    const newer = plan("plan_b", 1, "2026-09-17T08:00:00.000002Z");
    expect(key(focusPlan([newer, older]))).toBe("plan_b:v1");
    expect(key(focusPlan([older, newer]))).toBe("plan_b:v1");
  });

  it("breaks an exact tie by higher version of the same plan, then by plan ID, regardless of input order", () => {
    const a1 = plan("plan_a", 1, "2026-09-17T08:00:00Z");
    const a2 = plan("plan_a", 2, "2026-09-17T08:00:00.000000Z");
    expect(key(focusPlan([a1, a2]))).toBe("plan_a:v2");
    expect(key(focusPlan([a2, a1]))).toBe("plan_a:v2");
    const b1 = plan("plan_b", 1, "2026-09-17T08:00:00Z");
    const first = key(focusPlan([a1, b1]));
    expect(first).toBe(key(focusPlan([b1, a1])));
    expect(first).toBe("plan_a:v1");
  });

  it("returns null for no plans", () => {
    expect(focusPlan([])).toBeNull();
  });
});

describe("outcome ordering", () => {
  const outcome = (id: string, recordedAt: string, supersedes: string | null = null): OutcomeRecord => ({
    ...baseOutcome,
    outcome_id: id,
    recorded_at_utc: recordedAt,
    request: { ...baseOutcome.request, supersedes_outcome_id: supersedes },
  });

  it("picks the later record by real instant when two unsuperseded records share a second", () => {
    const early = outcome("o_early", "2026-09-16T08:12:00.250000Z");
    const late = outcome("o_late", "2026-09-16T08:12:00Z"); // text sorts after ".250000Z" but is earlier
    const latest = latestOutcomes([late, early], baseOutcome.request.confirmation_id);
    expect(latest.COLLECTED?.outcome_id).toBe("o_early");
  });

  it("orders history by real instant with mixed precision", () => {
    const a = outcome("o_a", "2026-09-16T08:12:00.900000Z");
    const b = outcome("o_b", "2026-09-16T08:12:01Z", "o_a");
    const c = outcome("o_c", "2026-09-16T08:12:00Z");
    expect(outcomeHistory([a, b, c], baseOutcome.request.confirmation_id, "COLLECTED").map((item) => item.outcome_id)).toEqual(["o_c", "o_a", "o_b"]);
  });
});
