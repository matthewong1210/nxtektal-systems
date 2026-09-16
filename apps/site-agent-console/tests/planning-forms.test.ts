import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  buildConfirmationRequest,
  buildInputRequest,
  buildOutcomeRequest,
  buildPlanRequest,
  draftFromInput,
  emptyInputDraft,
  type InputDraft,
} from "../lib/planning-forms";
import type { InputRecord, InputRequest, OutcomeRequest, PlanRequest } from "../lib/planning";
import { formatSiteTime, siteTimeToUtc, utcToSiteInput } from "../lib/site-time";

const SUCCESS = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts", "planning-v1", "examples", "success.json"),
    "utf-8",
  ),
) as { exchanges: { request: { schema_ref?: string; body?: unknown }; response: { body: { data?: { record?: unknown } } } }[] };

const body = <T>(definition: string): T =>
  SUCCESS.exchanges.find((e) => e.request.schema_ref === `#/$defs/${definition}`)!.request.body as T;
const record = <T>(definition: string): T =>
  SUCCESS.exchanges.find((e) => e.request.schema_ref === `#/$defs/${definition}`)!.response.body.data!.record as T;

const CONTEXT = {
  site_id: "pilot-course-a",
  deployment_id: "pilot-a-edge-task-sim-v0",
  site_timezone: "Asia/Shanghai",
  zone_ids: ["Z1"],
  robot_ids: ["picker-01"],
};

/** 08:00Z is 16:00 in Asia/Shanghai; the example evidence is valid until 09:30Z (17:30). */
const evidence = (sourceKind: "MEASURED" | "MANUAL_ESTIMATE", sourceRef: string) => ({
  known: true,
  sourceKind,
  sourceRef,
  observedAt: "2026-09-16T16:00",
  validUntil: "2026-09-16T17:30",
});

function successDraft(): InputDraft {
  return {
    operator: "course-manager",
    reason: "Opening count and explicitly estimated demand; no historical export available.",
    scope: "SHIFT",
    effectiveAt: "2026-09-16T16:00",
    validUntil: "2026-09-16T17:15",
    windowStart: "2026-09-16T16:00",
    windowEnd: "2026-09-16T17:15",
    inventory: { ...evidence("MEASURED", "opening-clean-bin-count-001"), value: "600" },
    demand: {
      ...evidence("MANUAL_ESTIMATE", "manager-demand-assumptions-001"),
      bucketMinutes: "10",
      low: "5, 5, 5, 5, 5, 5",
      typical: "10 10 10 10 10 10",
      high: "15,15,15,15,15,15",
    },
    safetyStock: { ...evidence("MANUAL_ESTIMATE", "manager-safety-target-001"), value: "200" },
    bufferMinutes: { ...evidence("MANUAL_ESTIMATE", "manager-timing-margin-001"), value: "2" },
    operationsAllowed: { ...evidence("MANUAL_ESTIMATE", "operator-work-window-check-001"), value: "true" },
    washerAvailable: { ...evidence("MANUAL_ESTIMATE", "operator-washer-check-001"), value: "true" },
    zones: [
      {
        zoneId: "Z1",
        robotId: "picker-01",
        collectionAllowed: { ...evidence("MANUAL_ESTIMATE", "operator-zone-Z1-check-001"), value: "true" },
        cleanYield: { ...evidence("MANUAL_ESTIMATE", "operator-zone-Z1-net-yield-estimate-001"), low: "400", high: "500" },
        cycleMinutes: {
          ...evidence("MANUAL_ESTIMATE", "operator-six-stage-duration-estimate-001"),
          travel: "2",
          collect: "5",
          return: "2",
          unload: "2",
          wash: "3",
          supply: "1",
        },
      },
    ],
  };
}

describe("site time conversion", () => {
  it("converts site-local wall clock to UTC and back, without DST and with it", () => {
    expect(siteTimeToUtc("2026-09-16T16:00", "Asia/Shanghai")).toBe("2026-09-16T08:00:00Z");
    expect(utcToSiteInput("2026-09-16T08:00:00Z", "Asia/Shanghai")).toBe("2026-09-16T16:00");
    expect(siteTimeToUtc("2026-07-04T09:30", "America/New_York")).toBe("2026-07-04T13:30:00Z");
    expect(siteTimeToUtc("2026-01-04T09:30", "America/New_York")).toBe("2026-01-04T14:30:00Z");
    expect(utcToSiteInput("2026-07-04T13:30:00Z", "America/New_York")).toBe("2026-07-04T09:30");
    expect(formatSiteTime("2026-09-16T08:05:00Z", "Asia/Shanghai")).toBe("2026-09-16 16:05");
    expect(formatSiteTime(null, "Asia/Shanghai")).toBe("—");
  });

  it("rejects incomplete local times", () => {
    expect(() => siteTimeToUtc("", "Asia/Shanghai")).toThrow(/date and time/);
    expect(() => siteTimeToUtc("2026-13-40T25:00", "Asia/Shanghai")).toThrow();
  });
});

describe("input request builder", () => {
  it("reproduces the frozen success InputRequest byte for byte from a manager draft", () => {
    const built = buildInputRequest(successDraft(), { requestId: "demo-input-001", expectedRevision: 0, context: CONTEXT });
    expect(built).toEqual(body<InputRequest>("InputRequest"));
  });

  it("saves unknown evidence as null and never invents zeros", () => {
    const draft = successDraft();
    draft.inventory.known = false;
    draft.demand.known = false;
    draft.zones = [];
    const built = buildInputRequest(draft, { requestId: "missing-input-001", expectedRevision: 0, context: CONTEXT });
    expect(built.inventory_clean_balls).toBeNull();
    expect(built.demand).toBeNull();
    expect(built.zones).toEqual([]);
    expect(built.safety_stock_balls?.value).toBe(200);
  });

  it("refuses known evidence without a source reference or with malformed numbers", () => {
    const missingRef = successDraft();
    missingRef.inventory.sourceRef = " ";
    expect(() => buildInputRequest(missingRef, { requestId: "x", expectedRevision: 0, context: CONTEXT })).toThrow(/source reference/i);
    const negative = successDraft();
    negative.safetyStock.value = "-1";
    expect(() => buildInputRequest(negative, { requestId: "x", expectedRevision: 0, context: CONTEXT })).toThrow(/safety stock/i);
    const ragged = successDraft();
    ragged.demand.high = "15,15";
    expect(() => buildInputRequest(ragged, { requestId: "x", expectedRevision: 0, context: CONTEXT })).toThrow(/same number of buckets/i);
    const inverted = successDraft();
    inverted.demand.low = "20,20,20,20,20,20";
    expect(() => buildInputRequest(inverted, { requestId: "x", expectedRevision: 0, context: CONTEXT })).toThrow(/low.*typical.*high/i);
    const yieldRange = successDraft();
    yieldRange.zones[0].cleanYield.low = "600";
    expect(() => buildInputRequest(yieldRange, { requestId: "x", expectedRevision: 0, context: CONTEXT })).toThrow(/yield/i);
    const noReason = successDraft();
    noReason.reason = "";
    expect(() => buildInputRequest(noReason, { requestId: "x", expectedRevision: 0, context: CONTEXT })).toThrow(/reason/i);
    const foreignZone = successDraft();
    foreignZone.zones[0].zoneId = "Z9";
    expect(() => buildInputRequest(foreignZone, { requestId: "x", expectedRevision: 0, context: CONTEXT })).toThrow(/zone/i);
  });

  it("starts empty: no value, source, or time is prefilled", () => {
    const draft = emptyInputDraft();
    expect(draft.inventory.known).toBe(false);
    expect(draft.inventory.value).toBe("");
    expect(draft.demand.low).toBe("");
    expect(draft.zones).toEqual([]);
    expect(draft.effectiveAt).toBe("");
  });

  it("rebuilds a draft from the persisted record so a revision starts from the current input", () => {
    const persisted = record<InputRecord>("InputRequest");
    const draft = draftFromInput(persisted, "Asia/Shanghai");
    expect(draft.effectiveAt).toBe("2026-09-16T16:00");
    expect(draft.inventory).toMatchObject({ known: true, value: "600", sourceKind: "MEASURED" });
    expect(draft.demand.typical).toBe("10, 10, 10, 10, 10, 10");
    expect(draft.zones[0].cycleMinutes.wash).toBe("3");
    const rebuilt = buildInputRequest(draft, { requestId: "demo-input-001", expectedRevision: 0, context: CONTEXT });
    expect(rebuilt).toEqual(body<InputRequest>("InputRequest"));
  });
});

describe("plan, confirmation, and outcome builders", () => {
  it("reproduces the frozen PlanRequest with a manual selection", () => {
    const built = buildPlanRequest(
      {
        operator: "course-manager",
        reason: "Delay departure four minutes to complete the local zone check.",
        scope: "ONE_TASK",
        validUntil: "2026-09-16T17:10",
        selection: { zoneId: "Z1", robotId: "picker-01", startAt: "2026-09-16T16:05" },
      },
      { requestId: "demo-plan-001", planId: null, expectedPlanVersion: 0, inputRevision: 1, timeZone: "Asia/Shanghai" },
    );
    expect(built).toEqual(body<PlanRequest>("PlanRequest"));
  });

  it("submits a null selection to ask for or restore the system suggestion", () => {
    const built = buildPlanRequest(
      { operator: "mgr", reason: "restore", scope: "SHIFT", validUntil: "2026-09-16T17:10", selection: null },
      { requestId: "r", planId: "plan_x", expectedPlanVersion: 2, inputRevision: 3, timeZone: "Asia/Shanghai" },
    );
    expect(built).toMatchObject({ plan_id: "plan_x", expected_plan_version: 2, input_revision: 3, selection: null });
  });

  it("reproduces the frozen ConfirmationRequest for an exact version", () => {
    expect(
      buildConfirmationRequest({ requestId: "demo-confirm-001", planId: "plan_example_001", planVersion: 1, operator: "course-manager" }),
    ).toEqual(body("ConfirmationRequest"));
    expect(() => buildConfirmationRequest({ requestId: "r", planId: "p", planVersion: 1, operator: "  " })).toThrow(/operator/i);
  });

  it("reproduces the frozen COLLECTED OutcomeRequest and requires explicit evidence", () => {
    const built = buildOutcomeRequest(
      {
        operator: "course-operator",
        reason: "Recorded independently after this stage completed.",
        stage: "COLLECTED",
        quantity: "520",
        startedAt: "2026-09-16T16:07",
        completedAt: "2026-09-16T16:12",
        sourceKind: "MEASURED",
        sourceRef: "work-log-001-collected",
      },
      {
        requestId: "demo-outcome-001",
        confirmationId: "confirmation_example_001",
        taskId: "task_example_001",
        supersedesOutcomeId: null,
        timeZone: "Asia/Shanghai",
      },
    );
    expect(built).toEqual(body<OutcomeRequest>("OutcomeRequest"));
    expect(() =>
      buildOutcomeRequest(
        { operator: "o", reason: "r", stage: "WASHED", quantity: "12.5", startedAt: "2026-09-16T16:07", completedAt: "2026-09-16T16:12", sourceKind: "MEASURED", sourceRef: "x" },
        { requestId: "r", confirmationId: "c", taskId: "t", supersedesOutcomeId: null, timeZone: "Asia/Shanghai" },
      ),
    ).toThrow(/whole number/i);
    expect(() =>
      buildOutcomeRequest(
        { operator: "o", reason: "r", stage: "WASHED", quantity: "12", startedAt: "2026-09-16T16:12", completedAt: "2026-09-16T16:07", sourceKind: "MEASURED", sourceRef: "x" },
        { requestId: "r", confirmationId: "c", taskId: "t", supersedesOutcomeId: null, timeZone: "Asia/Shanghai" },
      ),
    ).toThrow(/before/i);
  });
});
