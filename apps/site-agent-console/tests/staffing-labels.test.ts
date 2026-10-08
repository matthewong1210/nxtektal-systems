import { describe, expect, it } from "vitest";

import { formatSiteRange } from "../lib/site-time";
import type { CandidateProjection, GenerationProjection } from "../lib/staffing";
import {
  actionabilityLabel,
  candidateIsActionable,
  candidateStatusLabel,
  exceptionKindLabel,
  generationIsHistorical,
  planStatusLabel,
  reasonCodeLabel,
  responseKindLabel,
} from "../lib/staffing-labels";
import { describeStaffingFailure } from "../components/staffing/rejections";
import { ManagerApiError } from "../lib/api";

function candidate(patch: Partial<CandidateProjection>): CandidateProjection {
  return {
    candidate_index: 1,
    status: "VALID",
    operations: [],
    rationale: "worker_00112233445566778899aabb covers",
    operational_warnings: [],
    coverage_gaps: [],
    schedule_digest: "e".repeat(64),
    rejection_codes: [],
    rationale_local: "Operator Two covers",
    operational_warnings_local: [],
    action_window_end_at: "2026-10-08T12:00:00Z",
    actionability: "CURRENT",
    ...patch,
  } as CandidateProjection;
}

describe("site-local interval rendering", () => {
  it("renders a UTC wire interval in the site timezone, one date for a same-day shift", () => {
    expect(formatSiteRange("2026-10-08T08:00:00Z", "2026-10-08T12:00:00Z", "Asia/Shanghai")).toBe("2026-10-08 16:00–20:00");
    expect(formatSiteRange("2026-10-08T01:00:00Z", "2026-10-08T09:00:00Z", "Asia/Shanghai")).toBe("2026-10-08 09:00–17:00");
  });

  it("accepts signed offsets and shows both dates when the interval crosses site midnight", () => {
    expect(formatSiteRange("2026-10-08T16:00:00+08:00", "2026-10-08T20:00:00+08:00", "Asia/Shanghai")).toBe("2026-10-08 16:00–20:00");
    expect(formatSiteRange("2026-10-08T14:00:00Z", "2026-10-08T18:00:00Z", "Asia/Shanghai")).toBe("2026-10-08 22:00 – 2026-10-09 02:00");
  });

  it("never prints a bare Z clock for a site in another zone", () => {
    const rendered = formatSiteRange("2026-10-08T08:00:00Z", "2026-10-08T12:00:00Z", "Asia/Shanghai");
    expect(rendered).not.toMatch(/Z/);
    expect(rendered).not.toContain("08:00");
  });
});

describe("Chinese labels keep the contract value available", () => {
  it("labels every closed manager result, plan, candidate, and exception value", () => {
    expect(responseKindLabel("ACCEPT")).toBe("已接受");
    expect(responseKindLabel("MODIFY")).toBe("修改后接受");
    expect(responseKindLabel("REJECT")).toBe("已拒绝");
    expect(reasonCodeLabel("APPROVED")).toBe("经理批准");
    expect(reasonCodeLabel("APPROVED_WITH_CHANGES")).toBe("经理修改后批准");
    expect(reasonCodeLabel("MANUAL_HANDLING")).toBe("需要人工处理");
    expect(reasonCodeLabel("INSUFFICIENT_CONTEXT")).toBe("信息不足");
    expect(reasonCodeLabel("OTHER")).toBe("其他");
    expect(planStatusLabel("CURRENT")).toBe("当前有效");
    expect(planStatusLabel("REVIEW_REQUIRED")).toBe("需重新审阅");
    expect(candidateStatusLabel("VALID")).toBe("通过校验");
    expect(candidateStatusLabel("REJECTED")).toBe("未通过校验");
    expect(actionabilityLabel("CURRENT")).toBe("可采用");
    expect(actionabilityLabel("EXPIRED")).toBe("班次已结束（历史记录）");
    expect(exceptionKindLabel("LEAVE")).toBe("请假");
    expect(exceptionKindLabel("EARLY_DEPARTURE")).toBe("早退");
  });

  it("echoes an unknown value instead of inventing a label", () => {
    expect(responseKindLabel("WITHDRAWN" as never)).toBe("WITHDRAWN");
    expect(planStatusLabel("SUPERSEDED" as never)).toBe("SUPERSEDED");
  });

  it("treats only a valid, unexpired candidate as actionable and an all-expired generation as historical", () => {
    expect(candidateIsActionable(candidate({}))).toBe(true);
    expect(candidateIsActionable(candidate({ actionability: "EXPIRED" }))).toBe(false);
    expect(candidateIsActionable(candidate({ status: "REJECTED", schedule_digest: null, rejection_codes: ["OUTSIDE_AVAILABILITY"] } as Partial<CandidateProjection>))).toBe(false);

    const base = { suggestion_id: "g", request_id: "r", operation_id: "o", basis: { roster: 1, exception_set: 1, effective_plan: 0, digest: "d".repeat(64) }, retry_of: null, state: "SUCCEEDED", coverage_gaps: [], provenance: [], failure_code: null, manager_response: null } as const;
    const historical = { ...base, candidates: [candidate({ actionability: "EXPIRED" }), candidate({ candidate_index: 2, actionability: "EXPIRED" })] } as unknown as GenerationProjection;
    const mixed = { ...base, candidates: [candidate({ actionability: "EXPIRED" }), candidate({ candidate_index: 2 })] } as unknown as GenerationProjection;
    const empty = { ...base, candidates: [] } as unknown as GenerationProjection;
    expect(generationIsHistorical(historical)).toBe(true);
    expect(generationIsHistorical(mixed)).toBe(false);
    expect(generationIsHistorical(empty)).toBe(false);
  });

  it("explains an expired-suggestion refusal by code without echoing server detail", () => {
    const copy = describeStaffingFailure(new ManagerApiError(409, { code: "staffing_suggestion_expired", detail: "staff-002 secret detail" }));
    expect(copy).toContain("班次已经结束");
    expect(copy).toContain("未保存任何变更");
    expect(copy).not.toContain("staff-002");
  });
});

describe("daylight-saving fall-back ranges", () => {
  const NY = "America/New_York";

  it("marks both ends with their offsets when the interval crosses the fall-back or a wall time repeats", () => {
    expect(formatSiteRange("2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z", NY)).toBe("2026-11-01 01:30 UTC-04:00–01:30 UTC-05:00");
    expect(formatSiteRange("2026-11-01T04:30:00Z", "2026-11-01T05:30:00Z", NY)).toBe("2026-11-01 00:30 UTC-04:00–01:30 UTC-04:00");
    expect(formatSiteRange("2026-11-01T06:30:00Z", "2026-11-01T07:30:00Z", NY)).toBe("2026-11-01 01:30 UTC-05:00–02:30 UTC-05:00");
    expect(formatSiteRange("2026-11-01T02:00:00Z", "2026-11-01T07:00:00Z", NY)).toBe("2026-10-31 22:00 UTC-04:00 – 2026-11-01 02:00 UTC-05:00");
  });

  it("marks a spring-forward interval whose offsets differ and leaves ordinary intervals unchanged", () => {
    expect(formatSiteRange("2026-03-08T06:30:00Z", "2026-03-08T07:30:00Z", NY)).toBe("2026-03-08 01:30 UTC-05:00–03:30 UTC-04:00");
    expect(formatSiteRange("2026-11-01T07:30:00Z", "2026-11-01T12:30:00Z", NY)).toBe("2026-11-01 02:30–07:30");
    expect(formatSiteRange("2026-10-08T08:00:00Z", "2026-10-08T12:00:00Z", "Asia/Shanghai")).toBe("2026-10-08 16:00–20:00");
  });
});
