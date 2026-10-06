// @vitest-environment happy-dom
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { StaffingView as StaffingViewComponent } from "../components/StaffingPanel";
import type { StaffingActions } from "../lib/staffing-actions";
import {
  type CandidateProjection,
  type GenerationCapability,
  type GenerationProjection,
  type StaffingDateSnapshot,
  type StaffingReceipt,
} from "../lib/staffing";
import {
  initialStaffingView,
  type StaffingView as StaffingViewState,
} from "../lib/staffing-state";

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

type Exchange = {
  name: string;
  request?: unknown;
  body: { data: unknown };
};

const exchanges = readdirSync(EXAMPLES_DIR)
  .filter((name) => name.endsWith(".json") && name !== "errors.json")
  .flatMap((name) => {
    const document = JSON.parse(readFileSync(join(EXAMPLES_DIR, name), "utf8")) as {
      exchanges: Exchange[];
    };
    return document.exchanges;
  });

function exchange(name: string): Exchange {
  const found = exchanges.find((item) => item.name === name);
  if (!found) throw new Error(`missing staffing fixture ${name}`);
  return found;
}

function snapshot(name = "date-after-manager-response"): StaffingDateSnapshot {
  return structuredClone(exchange(name).body.data) as StaffingDateSnapshot;
}

function receipt(name: string): StaffingReceipt {
  return structuredClone(exchange(name).body.data) as StaffingReceipt;
}

function generation(name = "suggestion-issued"): GenerationProjection {
  const value = receipt(name);
  if (value.operation_kind !== "suggestion-generate") throw new Error("not a generation fixture");
  return {
    suggestion_id: value.record.suggestion_id,
    request_id: value.request_id,
    operation_id: value.operation_id,
    state: value.state,
    basis: structuredClone(value.record.basis),
    retry_of: value.record.retry_of,
    candidates: "candidates" in value.record ? structuredClone(value.record.candidates) : [],
    coverage_gaps: "coverage_gaps" in value.record
      ? structuredClone(value.record.coverage_gaps)
      : [],
    provenance: structuredClone(value.record.provenance),
    failure_code: "failure_code" in value.record ? value.record.failure_code : null,
    manager_response: null,
  } as GenerationProjection;
}

function readySnapshot(active: GenerationProjection | null = null): StaffingDateSnapshot {
  const value = snapshot();
  value.generation_capability = {
    status: "READY",
    region: "CN",
    primary_provider: "KIMI",
    backup_provider: null,
    failure_code: null,
  };
  value.effective_plan = null;
  if (active === null) {
    value.revisions = { roster: 1, exception_set: 3, effective_plan: 0 };
    value.generations = [];
  } else {
    value.revisions = {
      roster: active.basis.roster,
      exception_set: active.basis.exception_set,
      effective_plan: active.basis.effective_plan,
    };
    value.generations = [structuredClone(active)];
  }
  return value;
}

function state(
  data: StaffingDateSnapshot | null,
  patch: Partial<StaffingViewState> = {},
): StaffingViewState {
  const activeGeneration = data?.generations.at(-1) ?? null;
  return {
    ...initialStaffingView(),
    snapshot: data,
    read: data === null
      ? { status: "loading", stale: false, detail: null }
      : { status: "ready", stale: false, detail: null },
    activeGenerationRequestId: activeGeneration?.request_id ?? null,
    activeGeneration,
    ...patch,
  };
}

function actions(): StaffingActions {
  const committed = receipt("exception-recorded");
  return {
    refresh: vi.fn(async () => undefined),
    importRoster: vi.fn(async () => committed),
    recordException: vi.fn(async () => committed),
    cancelException: vi.fn(async () => committed),
    correctException: vi.fn(async () => committed),
    generateSuggestion: vi.fn(async () => receipt("generation-reserved")),
    acceptSuggestion: vi.fn(async () => receipt("manager-accepted")),
    modifySuggestion: vi.fn(async () => receipt("manager-modified")),
    rejectSuggestion: vi.fn(async () => receipt("manager-rejected")),
    recover: vi.fn(async () => committed),
    retryBusy: vi.fn(async () => receipt("generation-reserved")),
    retryUnknownGeneration: vi.fn(async () => receipt("generation-reserved")),
    acknowledgeWrite: vi.fn(),
  };
}

function renderView(
  view: StaffingViewState,
  managerLabel = "当班经理",
): { host: HTMLDivElement; html: string } {
  const html = renderToStaticMarkup(
    <StaffingViewComponent
      view={view}
      actions={actions()}
      managerLabel={managerLabel}
      onManagerLabelChange={() => undefined}
    />,
  );
  const host = document.createElement("div");
  host.innerHTML = html;
  return { host, html };
}

function button(host: ParentNode, pattern: RegExp): HTMLButtonElement | undefined {
  return [...host.querySelectorAll<HTMLButtonElement>("button")]
    .find((candidate) => pattern.test(candidate.textContent ?? ""));
}

function candidate(host: ParentNode, index: 1 | 2): HTMLElement {
  const card = host.querySelector<HTMLElement>(`[aria-label="排班建议 ${index}"]`);
  if (!card) throw new Error(`missing candidate ${index}`);
  return card;
}

describe("compact staffing panel", () => {
  it("renders a cold start as an importable empty roster, not invented staffing data", () => {
    const { host, html } = renderView(state(snapshot("current-empty")));

    expect(html).toContain("人员异常与排班建议");
    expect(html).toContain("2026-10-06");
    expect(html).toContain("Asia/Shanghai");
    expect(html).toContain("尚未导入人员表");
    expect(html).not.toMatch(/0\s*名员工|0\s*个班次/);
    expect(host.querySelector('a[href="/staffing-roster-template.csv"][download]')).not.toBeNull();
    expect(host.querySelector('input[type="file"][accept=".csv,text/csv"]')).not.toBeNull();
    expect(button(host, /生成.*建议/)?.disabled).toBe(true);
  });

  it("shows the factual service-day rail and an honest zero-exception state", () => {
    const data = readySnapshot();
    data.active_exceptions = [];
    const { host, html } = renderView(state(data));

    const rail = host.querySelector<HTMLElement>('[role="region"][aria-label="服务日与人员表状态"]');
    expect(rail).not.toBeNull();
    expect([...rail!.querySelectorAll("dt")].map((item) => item.textContent)).toEqual([
      "服务日",
      "时区",
      "员工",
      "常规班次",
      "版本",
    ]);
    expect([...rail!.querySelectorAll("dd")].map((item) => item.textContent)).toEqual([
      "2026-10-06",
      "Asia/Shanghai",
      "2",
      "1",
      "1",
    ]);
    expect(html).toContain("2026-10-06");
    expect(html).toContain("Asia/Shanghai");
    expect(html).toContain("2");
    expect(html).toContain("1");
    expect(html).toMatch(/暂无.*异常/);
    expect(html).not.toMatch(/请先录入|需要补录/);
  });

  it("alerts when an effective plan needs review after staffing facts change", () => {
    const accepted = snapshot("date-after-manager-response");
    if (accepted.effective_plan === null) throw new Error("fixture must contain an effective plan");
    const data = readySnapshot();
    data.effective_plan = {
      ...structuredClone(accepted.effective_plan),
      status: "REVIEW_REQUIRED",
    };
    const { host } = renderView(state(data));

    const plan = host.querySelector<HTMLElement>('[role="alert"][aria-label="有效排班状态"]');
    expect(plan).not.toBeNull();
    expect(plan?.textContent).toContain("REVIEW_REQUIRED");
    expect(plan?.textContent).toContain("排班版本 1");
    expect(plan?.textContent).toContain("需要经理重新审阅");
  });

  for (const fixtureName of ["generation-reserved", "generation-in-progress"] as const) {
    it(`keeps exception entry available while ${fixtureName} prevents duplicate generation`, () => {
      const active = generation(fixtureName);
      const { host, html } = renderView(state(readySnapshot(active)));

      expect(html).toContain(active.state);
      expect(button(host, /生成.*建议/)?.disabled).toBe(true);
      expect(button(host, /记录.*异常/)?.disabled).toBe(false);
      expect(button(host, /刷新/)?.disabled).toBe(false);
    });
  }

  const capabilities: Array<{
    name: string;
    value: GenerationCapability;
    allowed: boolean;
    degraded: boolean;
  }> = [
    {
      name: "unconfigured without region",
      value: { status: "UNAVAILABLE", region: null, primary_provider: null, backup_provider: null, failure_code: "PROVIDER_UNCONFIGURED" },
      allowed: false,
      degraded: false,
    },
    {
      name: "CN ready",
      value: { status: "READY", region: "CN", primary_provider: "KIMI", backup_provider: null, failure_code: null },
      allowed: true,
      degraded: false,
    },
    {
      name: "CN unavailable",
      value: { status: "UNAVAILABLE", region: "CN", primary_provider: "KIMI", backup_provider: null, failure_code: "PROVIDER_UNCONFIGURED" },
      allowed: false,
      degraded: false,
    },
    {
      name: "GLOBAL ready",
      value: { status: "READY", region: "GLOBAL", primary_provider: "OPENAI", backup_provider: "ANTHROPIC", failure_code: null },
      allowed: true,
      degraded: false,
    },
    {
      name: "GLOBAL degraded",
      value: { status: "DEGRADED_BACKUP_UNCONFIGURED", region: "GLOBAL", primary_provider: "OPENAI", backup_provider: null, failure_code: "BACKUP_UNCONFIGURED" },
      allowed: true,
      degraded: true,
    },
    {
      name: "GLOBAL unavailable",
      value: { status: "UNAVAILABLE", region: "GLOBAL", primary_provider: "OPENAI", backup_provider: "ANTHROPIC", failure_code: "PROVIDER_UNCONFIGURED" },
      allowed: false,
      degraded: false,
    },
  ];

  it.each(capabilities)("freezes generation capability: $name", ({ value, allowed, degraded }) => {
    const active = generation();
    const data = readySnapshot(active);
    data.generation_capability = value;
    const { host, html } = renderView(state(data));

    expect(button(host, /生成.*建议/)?.disabled).toBe(!allowed);
    expect(button(host, /记录.*异常/)?.disabled).toBe(false);
    expect(button(candidate(host, 1), /接受.*建议/)?.disabled).toBe(false);
    if (degraded) {
      expect(html).toContain("BACKUP_UNCONFIGURED");
      expect(host.querySelector('[role="status"], [role="alert"]')).not.toBeNull();
    }
  });

  it.each([
    ["no-valid-suggestion", "NO_VALID_SUGGESTION"],
    ["unavailable", "UNAVAILABLE"],
    ["refused", "REFUSED"],
    ["invalid-response", "INVALID_RESPONSE"],
    ["provider-error", "PROVIDER_ERROR"],
    ["configuration-error", "CONFIGURATION_ERROR"],
    ["security-error", "SECURITY_ERROR"],
    ["generation-interrupted", "RESULT_UNKNOWN"],
  ] as const)("renders the durable %s terminal without claiming a schedule change", (fixtureName, expectedState) => {
    const active = generation(fixtureName);
    const { html } = renderView(state(readySnapshot(active)));

    expect(html).toContain(expectedState);
    if (active.failure_code !== null) expect(html).toContain(active.failure_code);
    expect(html).not.toMatch(/自动排班|身份已验证|劳动法合规|已执行/);
  });

  it("keeps every candidate auditable with its disclaimer and provider/model provenance", () => {
    const active = generation();
    const rejected = generation("no-valid-suggestion").candidates[0] as CandidateProjection;
    const valid = {
      ...structuredClone(active.candidates[0]),
      operational_warnings: ["交接窗口需要经理现场确认"],
    } as CandidateProjection;
    active.candidates = [valid, { ...structuredClone(rejected), candidate_index: 2 } as CandidateProjection];
    active.coverage_gaps = structuredClone(rejected.coverage_gaps);
    const { host, html } = renderView(state(readySnapshot(active)));

    expect(html.match(/AI 建议，需经理确认/g) ?? []).toHaveLength(2);
    expect(html.match(/KIMI/g) ?? []).toHaveLength(2);
    expect(html.match(/moonshot-v1-128k/g) ?? []).toHaveLength(2);
    expect(host.querySelector('[role="region"][aria-label="覆盖缺口"]')).not.toBeNull();
    expect(host.querySelector('[role="region"][aria-label="运营提示"]')).not.toBeNull();
    expect(button(candidate(host, 1), /接受.*建议/)?.getAttribute("aria-label")).toBe("接受排班建议 1");
    expect(button(candidate(host, 1), /修改.*建议/)?.getAttribute("aria-label")).toBe("修改排班建议 1");
    expect(button(candidate(host, 2), /接受.*建议/)).toBeUndefined();
    expect(button(candidate(host, 2), /修改.*建议/)).toBeUndefined();
    expect(candidate(host, 1).textContent).not.toContain("拒绝此建议");
    expect(candidate(host, 2).textContent).not.toContain("拒绝此建议");
    expect(
      [...host.querySelectorAll("button")]
        .filter((item) => item.textContent?.includes("拒绝本次全部建议")),
    ).toHaveLength(1);
    expect(html).not.toMatch(/自动排班|身份已验证|劳动法合规|已执行/);
  });

  it("renders a durable manager response from the refreshed projection and hides all response controls", () => {
    const data = snapshot("date-after-manager-response");
    const active = data.generations[0];
    const { host, html } = renderView(state(data, {
      activeGenerationRequestId: active.request_id,
      activeGeneration: active,
      write: null,
    }));

    expect(html).toContain("ACCEPT");
    expect(html).toContain("APPROVED");
    expect(html).toContain("manager-1");
    expect(html).toContain("采用一号方案");
    expect(html).toMatch(/有效排班.*1|版本.*1/);
    const generationStatus = host.querySelector<HTMLElement>('[aria-label="排班建议状态"]');
    expect(generationStatus?.textContent).toContain("经理处理结果已保存");
    expect(generationStatus?.textContent).not.toContain("需经理确认");
    expect(button(host, /接受.*建议/)).toBeUndefined();
    expect(button(host, /修改.*建议/)).toBeUndefined();
    expect(button(host, /拒绝.*建议/)).toBeUndefined();
  });

  it("shows response controls only when all three basis revisions equal the fresh snapshot", () => {
    const active = generation();
    const matching = readySnapshot(active);
    expect(button(candidate(renderView(state(matching)).host, 1), /接受.*建议/)).toBeDefined();

    for (const key of ["roster", "exception_set", "effective_plan"] as const) {
      const changed = readySnapshot(active);
      changed.revisions[key] += 1;
      const card = candidate(renderView(state(changed)).host, 1);
      expect(button(card, /接受.*建议/), key).toBeUndefined();
      expect(button(card, /修改.*建议/), key).toBeUndefined();
      expect(button(renderView(state(changed)).host, /拒绝本次全部建议/), key).toBeUndefined();
    }
  });

  it("does not offer MODIFY for a valid candidate with no closed patch rows", () => {
    const active = generation();
    active.candidates = [{
      ...structuredClone(active.candidates[0]),
      operations: [],
    } as CandidateProjection];
    const card = candidate(renderView(state(readySnapshot(active))).host, 1);

    expect(button(card, /接受.*建议/)).toBeDefined();
    expect(button(card, /修改.*建议/)).toBeUndefined();
    expect(button(renderView(state(readySnapshot(active))).host, /拒绝本次全部建议/)).toBeDefined();
  });

  it("announces initial loading, stale read failure, and saved-but-stale as different states", () => {
    const loading = renderView(state(null));
    expect(loading.host.querySelector('[role="status"][aria-live]')).not.toBeNull();

    const data = readySnapshot();
    const stale = renderView(state(data, {
      read: { status: "error", stale: true, detail: "staffing read failed" },
    }));
    expect(stale.html).toContain("staffing read failed");
    expect(stale.host.querySelector('[role="alert"]')).not.toBeNull();
    expect(button(stale.host, /记录.*异常/)?.disabled).toBe(true);

    const savedButStale = renderView(state(data, {
      read: { status: "error", stale: true, detail: "refresh failed" },
      write: {
        status: "committed",
        requestId: "request-exception-1",
        receipt: receipt("exception-recorded"),
        savedButStale: true,
      },
    }));
    expect(savedButStale.html).toMatch(/已保存|SAVED/);
    expect(savedButStale.html).toContain("refresh failed");
    expect(savedButStale.html).not.toMatch(/结果未知|UNKNOWN OUTCOME/);
    expect(button(savedButStale.host, /刷新/)).toBeDefined();
  });
});
