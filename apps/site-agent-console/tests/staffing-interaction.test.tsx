// @vitest-environment happy-dom
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { useState } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  StaffingPanel,
  StaffingView as StaffingViewComponent,
} from "../components/StaffingPanel";
import type { StaffingActions } from "../lib/staffing-actions";
import {
  type CandidateProjection,
  type GenerationProjection,
  type StaffingClient,
  type StaffingDateSnapshot,
  type StaffingMutation,
  type StaffingReceipt,
  type SuggestionGenerateRequest,
} from "../lib/staffing";
import {
  STAFFING_REQUEST_POLL_MS,
  STAFFING_SNAPSHOT_POLL_MS,
  initialStaffingView,
  type StaffingView as StaffingViewState,
} from "../lib/staffing-state";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
  value.revisions = active === null
    ? { roster: 1, exception_set: 3, effective_plan: 0 }
    : {
        roster: active.basis.roster,
        exception_set: active.basis.exception_set,
        effective_plan: active.basis.effective_plan,
      };
  value.generations = active === null ? [] : [structuredClone(active)];
  return value;
}

function view(
  data = readySnapshot(),
  patch: Partial<StaffingViewState> = {},
): StaffingViewState {
  const activeGeneration = data.generations.at(-1) ?? null;
  return {
    ...initialStaffingView(),
    snapshot: data,
    read: { status: "ready", stale: false, detail: null },
    activeGenerationRequestId: activeGeneration?.request_id ?? null,
    activeGeneration,
    ...patch,
  };
}

function actionSpies() {
  const committed = receipt("exception-recorded");
  return {
    refresh: vi.fn(async () => undefined),
    importRoster: vi.fn<StaffingActions["importRoster"]>(async () => committed),
    recordException: vi.fn<StaffingActions["recordException"]>(async () => committed),
    cancelException: vi.fn<StaffingActions["cancelException"]>(async () => committed),
    correctException: vi.fn<StaffingActions["correctException"]>(async () => committed),
    generateSuggestion: vi.fn(async () => receipt("generation-reserved")),
    acceptSuggestion: vi.fn<StaffingActions["acceptSuggestion"]>(
      async () => receipt("manager-accepted"),
    ),
    modifySuggestion: vi.fn<StaffingActions["modifySuggestion"]>(
      async () => receipt("manager-modified"),
    ),
    rejectSuggestion: vi.fn<StaffingActions["rejectSuggestion"]>(
      async () => receipt("manager-rejected"),
    ),
    recover: vi.fn(async () => committed),
    retryBusy: vi.fn(async () => receipt("generation-reserved")),
    retryUnknownGeneration: vi.fn(async () => receipt("generation-reserved")),
    acknowledgeWrite: vi.fn(),
  } satisfies StaffingActions;
}

function ControlledView({
  value,
  actions,
  initialManager = "当班经理",
}: {
  value: StaffingViewState;
  actions: StaffingActions;
  initialManager?: string;
}) {
  const [managerLabel, setManagerLabel] = useState(initialManager);
  return (
    <StaffingViewComponent
      view={value}
      actions={actions}
      managerLabel={managerLabel}
      onManagerLabelChange={setManagerLabel}
    />
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let root: Root | undefined;
let container: HTMLDivElement;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = undefined;
  container.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function flush(): Promise<void> {
  await act(async () => {
    for (let index = 0; index < 16; index += 1) await Promise.resolve();
  });
}

async function mount(element: React.ReactNode): Promise<void> {
  root = createRoot(container);
  await act(async () => {
    root!.render(element);
  });
  await flush();
}

function buttons(scope: ParentNode = container): HTMLButtonElement[] {
  return [...scope.querySelectorAll<HTMLButtonElement>("button")];
}

function button(pattern: RegExp, scope: ParentNode = container): HTMLButtonElement {
  const found = buttons(scope).find((candidate) => pattern.test(candidate.textContent ?? ""));
  if (!found) throw new Error(`missing button ${pattern}`);
  return found;
}

async function click(pattern: RegExp, scope: ParentNode = container): Promise<void> {
  const target = button(pattern, scope);
  await act(async () => {
    target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
  });
  await flush();
}

async function setValue(
  element: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement,
  next: string,
): Promise<void> {
  const prototype = element instanceof HTMLSelectElement
    ? HTMLSelectElement.prototype
    : element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, next);
  await act(async () => {
    element.dispatchEvent(new Event(
      element instanceof HTMLSelectElement ? "change" : "input",
      { bubbles: true },
    ));
  });
}

function field<T extends Element>(scope: ParentNode, selector: string): T {
  const found = scope.querySelector<T>(selector);
  if (!found) throw new Error(`missing field ${selector}`);
  return found;
}

async function selectFile(input: HTMLInputElement, text: string, name: string): Promise<void> {
  const file = new File([text], name, { type: "text/csv" });
  const transfer = new DataTransfer();
  transfer.items.add(file);
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "files")!.set!.call(
    input,
    transfer.files,
  );
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}

const ROSTER_CSV = readFileSync(
  join(import.meta.dirname, "..", "public", "staffing-roster-template.csv"),
  "utf8",
);

function suggestionMutation(): StaffingMutation {
  return {
    operationKind: "suggestion-generate",
    body: structuredClone(exchange("generation-reserved").request) as SuggestionGenerateRequest,
  };
}

function responseFor(mutation: StaffingMutation): StaffingReceipt {
  let fixtureName: string;
  switch (mutation.operationKind) {
    case "roster-import": fixtureName = "roster-committed"; break;
    case "exception-record": fixtureName = "exception-recorded"; break;
    case "exception-cancel": fixtureName = "exception-cancelled"; break;
    case "exception-correct": fixtureName = "exception-corrected"; break;
    case "suggestion-generate": fixtureName = "generation-reserved"; break;
    case "manager-response":
      fixtureName = mutation.body.kind === "ACCEPT"
        ? "manager-accepted"
        : mutation.body.kind === "MODIFY"
          ? "manager-modified"
          : "manager-rejected";
      break;
  }
  const value = receipt(fixtureName);
  return { ...value, request_id: mutation.body.request_id } as StaffingReceipt;
}

function client(overrides: Partial<StaffingClient> = {}): StaffingClient {
  return {
    current: vi.fn(async () => readySnapshot()),
    date: vi.fn(async () => readySnapshot()),
    submit: vi.fn(async (mutation) => responseFor(mutation)),
    lookup: vi.fn(async () => receipt("generation-in-progress")),
    lookupMutation: vi.fn(async (mutation) => responseFor(mutation)),
    ...overrides,
  };
}

describe("minimal exception entry", () => {
  it("derives one employee choice per regular-roster worker and keeps the form to five controls", async () => {
    const data = readySnapshot();
    data.assignments.push({
      ...structuredClone(data.assignments[0]),
      assignment_id: "assignment-001-late",
      start_at: "2026-10-06T17:00:00+08:00",
      end_at: "2026-10-06T18:00:00+08:00",
    });
    const calls = actionSpies();
    await mount(<ControlledView value={view(data)} actions={calls} />);

    const form = field<HTMLFieldSetElement>(container, 'fieldset[aria-label="记录人员异常"]');
    const employee = field<HTMLSelectElement>(form, 'select[name="staff_id"]');
    expect([...employee.options].filter((option) => option.value === "staff-001")).toHaveLength(1);
    expect(form.querySelectorAll("select, input, textarea, button")).toHaveLength(4);
    expect(form.querySelector('[name="time_local"]')).toBeNull();

    await setValue(field<HTMLSelectElement>(form, 'select[name="kind"]'), "LATE");
    expect(field<HTMLInputElement>(form, 'input[name="time_local"]')).not.toBeNull();
    expect(form.querySelectorAll("select, input, textarea, button")).toHaveLength(5);
    expect(form.textContent).not.toMatch(/请求编号|版本号|供应商|模型/);
  });

  it.each([
    ["LEAVE", null],
    ["UNAVAILABLE", null],
    ["LATE", "10:30"],
    ["EARLY_DEPARTURE", "15:30"],
  ] as const)("submits %s with only its conditionally required time and a null blank note", async (kind, time) => {
    const calls = actionSpies();
    await mount(<ControlledView value={view()} actions={calls} />);
    const form = field<HTMLFieldSetElement>(container, 'fieldset[aria-label="记录人员异常"]');

    await setValue(field<HTMLSelectElement>(form, 'select[name="staff_id"]'), "staff-001");
    await setValue(field<HTMLSelectElement>(form, 'select[name="kind"]'), kind);
    if (time !== null) {
      await setValue(field<HTMLInputElement>(form, 'input[name="time_local"]'), time);
    }
    await click(/记录.*异常/, form);

    expect(calls.recordException).toHaveBeenCalledWith({
      staff_id: "staff-001",
      kind,
      time_local: time,
      note: null,
    });
    expect(calls.generateSuggestion).not.toHaveBeenCalled();

    await click(/生成.*建议/);
    expect(calls.generateSuggestion).toHaveBeenCalledOnce();
  });

  it("rejects a 501-scalar note locally before calling an action", async () => {
    const calls = actionSpies();
    await mount(<ControlledView value={view()} actions={calls} />);
    const form = field<HTMLFieldSetElement>(container, 'fieldset[aria-label="记录人员异常"]');

    await setValue(field<HTMLSelectElement>(form, 'select[name="staff_id"]'), "staff-001");
    await setValue(field<HTMLSelectElement>(form, 'select[name="kind"]'), "LEAVE");
    await setValue(field<HTMLTextAreaElement>(form, 'textarea[name="note"]'), "😀".repeat(501));
    const submit = button(/记录.*异常/, form);
    expect(submit.disabled).toBe(true);
    expect(form.textContent).toMatch(/500|过长/);
    expect(calls.recordException).not.toHaveBeenCalled();
  });
});

describe("two-stage roster import", () => {
  it("previews a valid CSV without writing and submits the complete replacement only after confirmation", async () => {
    const calls = actionSpies();
    await mount(<ControlledView value={view()} actions={calls} />);
    const region = field<HTMLElement>(container, '[role="region"][aria-label="导入人员表"]');
    const input = field<HTMLInputElement>(region, 'input[type="file"]');

    await selectFile(input, ROSTER_CSV, "manager-roster.csv");
    expect(calls.importRoster).not.toHaveBeenCalled();
    expect(region.textContent).toContain("Asia/Shanghai");
    expect(region.textContent).toContain("2026-10-06");
    expect(region.textContent).toMatch(/1\s*名|员工.*1/);
    expect(region.textContent).toMatch(/1\s*个|班次.*1/);

    await click(/确认.*导入/, region);
    expect(calls.importRoster).toHaveBeenCalledOnce();
    expect(calls.importRoster.mock.calls[0][0]).toMatchObject({
      schema: "nxt-staffing-roster-import/v1",
      site_timezone: "Asia/Shanghai",
      effective_from_local_date: "2026-10-06",
      source_ref: "manager-roster.csv",
      workers: [{ staff_id: "staff-demo-001" }],
      regular_assignments: [{ staff_id: "staff-demo-001" }],
    });
  });

  it("clears an older valid draft when a later selected CSV fails parsing", async () => {
    const calls = actionSpies();
    await mount(<ControlledView value={view()} actions={calls} />);
    const region = field<HTMLElement>(container, '[role="region"][aria-label="导入人员表"]');
    const input = field<HTMLInputElement>(region, 'input[type="file"]');

    await selectFile(input, ROSTER_CSV, "valid.csv");
    expect(button(/确认.*导入/, region).disabled).toBe(false);
    await selectFile(input, "not,a,staffing,roster\n", "broken.csv");

    expect(calls.importRoster).not.toHaveBeenCalled();
    expect(region.textContent).toMatch(/CSV.*无效|CSV is invalid/i);
    const staleConfirm = buttons(region).find((candidate) => /确认.*导入/.test(candidate.textContent ?? ""));
    expect(staleConfirm === undefined || staleConfirm.disabled).toBe(true);
  });
});

describe("manager attribution and local gates", () => {
  it("uses the latest volatile label and rejects invalid labels before an ID or request is created", async () => {
    const service = client();
    const uuid = vi.spyOn(globalThis.crypto, "randomUUID");
    await mount(<StaffingPanel client={service} />);
    uuid.mockClear();
    const manager = field<HTMLInputElement>(container, 'input[name="manager_label"]');

    await setValue(manager, " =PRIVATE_FORMULA");
    expect(button(/生成.*建议/).disabled).toBe(true);
    expect(service.submit).not.toHaveBeenCalled();
    expect(uuid).not.toHaveBeenCalled();

    await setValue(manager, "经".repeat(129));
    expect(button(/生成.*建议/).disabled).toBe(true);
    expect(uuid).not.toHaveBeenCalled();

    await setValue(manager, "晚班经理");
    expect(button(/生成.*建议/).disabled).toBe(false);
    await click(/生成.*建议/);
    expect(service.submit).toHaveBeenCalledOnce();
    const sent = vi.mocked(service.submit).mock.calls[0][0];
    expect(sent.operationKind).toBe("suggestion-generate");
    expect(sent.body.operator).toBe("晚班经理");
    expect(uuid).toHaveBeenCalledOnce();
  });

  it("keeps reads visible while a blank manager label disables every mutation", async () => {
    const calls = actionSpies();
    await mount(<ControlledView value={view()} actions={calls} initialManager="" />);

    expect(container.textContent).toContain("仅作归属记录，非身份认证");
    expect(button(/刷新/).disabled).toBe(false);
    expect(button(/记录.*异常/).disabled).toBe(true);
    expect(button(/生成.*建议/).disabled).toBe(true);
  });
});

describe("candidate response workflow", () => {
  function candidateView(active = generation()): StaffingViewState {
    return view(readySnapshot(active), {
      activeGenerationRequestId: active.request_id,
      activeGeneration: active,
    });
  }

  function card(index: 1 | 2 = 1): HTMLElement {
    return field<HTMLElement>(container, `[aria-label="排班建议 ${index}"]`);
  }

  it("accepts the exact candidate index and normalizes a blank response note to null", async () => {
    const calls = actionSpies();
    await mount(<ControlledView value={candidateView()} actions={calls} />);
    expect(button(/接受.*建议/, card()).getAttribute("aria-label")).toBe("接受排班建议 1");
    await click(/接受.*建议/, card());

    expect(calls.acceptSuggestion).toHaveBeenCalledWith("generation-success-1", 1, null);
  });

  it("blocks a 501-scalar response note before calling the response action", async () => {
    const calls = actionSpies();
    await mount(<ControlledView value={candidateView()} actions={calls} />);
    const note = field<HTMLTextAreaElement>(card(), 'textarea[name="manager_note"]');
    await setValue(note, "😀".repeat(501));

    expect(button(/接受.*建议/, card()).disabled).toBe(true);
    expect(card().textContent).toMatch(/500|过长/);
    expect(calls.acceptSuggestion).not.toHaveBeenCalled();
    expect(calls.modifySuggestion).not.toHaveBeenCalled();
    expect(calls.rejectSuggestion).not.toHaveBeenCalled();
  });

  it("submits MODIFY as the closed patch union without display fields", async () => {
    const active = generation();
    if (active.state !== "SUCCEEDED") throw new Error("fixture must succeed");
    const add = structuredClone(active.candidates[0].operations[0]);
    active.candidates[0] = {
      ...active.candidates[0],
      operations: [
        {
          operation: "REMOVE",
          assignment_id: "assignment-001",
          staff_id: "staff-001",
          display_name: "Operator One",
          role_code: "RANGE_ATTENDANT",
          area_code: "RANGE_A",
          start_at: "2026-10-06T09:00:00+08:00",
          end_at: "2026-10-06T17:00:00+08:00",
        },
        add,
      ],
    } as CandidateProjection;
    const calls = actionSpies();
    await mount(<ControlledView value={candidateView(active)} actions={calls} />);

    expect(button(/修改.*建议/, card()).getAttribute("aria-label")).toBe("修改排班建议 1");
    await click(/修改.*建议/, card());
    const editor = field<HTMLElement>(card(), '[aria-label="修改排班建议 1"]');
    expect(document.activeElement).toBe(editor.querySelector("input"));
    await click(/^取消$/, editor);
    expect(document.activeElement).toBe(button(/修改.*建议/, card()));

    await click(/修改.*建议/, card());
    const reopenedEditor = field<HTMLElement>(card(), '[aria-label="修改排班建议 1"]');
    await click(/提交.*修改/, reopenedEditor);

    expect(calls.modifySuggestion).toHaveBeenCalledWith(
      "generation-success-1",
      1,
      [
        { operation: "REMOVE", assignment_id: "assignment-001" },
        {
          operation: "ADD",
          staff_id: "staff-002",
          role_code: "RANGE_ATTENDANT",
          area_code: "RANGE_A",
          start_at: "2026-10-06T10:30:00+08:00",
          end_at: "2026-10-06T17:00:00+08:00",
        },
      ],
      null,
    );
    const operations = calls.modifySuggestion.mock.calls[0][2];
    expect(Object.keys(operations[0]).sort()).toEqual(["assignment_id", "operation"]);
    expect(Object.keys(operations[1]).sort()).toEqual([
      "area_code",
      "end_at",
      "operation",
      "role_code",
      "staff_id",
      "start_at",
    ]);
  });

  it.each([
    ["a missing offset", "2026-10-06T10:30:00"],
    ["a forbidden signed-zero offset", "2026-10-06T10:30:00+00:00"],
  ])("blocks MODIFY with %s before calling the response action", async (_label, startAt) => {
    const calls = actionSpies();
    await mount(<ControlledView value={candidateView()} actions={calls} />);
    await click(/修改.*建议/, card());
    const editor = field<HTMLElement>(card(), '[aria-label="修改排班建议 1"]');

    await setValue(field<HTMLInputElement>(editor, 'input[name="start_at"]'), startAt);

    expect(button(/提交.*修改/, editor).disabled).toBe(true);
    expect(editor.textContent).toMatch(/时区偏移/);
    expect(calls.modifySuggestion).not.toHaveBeenCalled();
  });

  it("rejects the whole suggestion set once, with its own bounded note and focus return", async () => {
    const calls = actionSpies();
    await mount(<ControlledView value={candidateView()} actions={calls} />);
    expect(card().textContent).not.toContain("拒绝此建议");
    expect(buttons().filter((item) => item.textContent?.includes("拒绝本次全部建议"))).toHaveLength(1);

    await click(/拒绝本次全部建议/);
    const form = field<HTMLElement>(container, '[aria-label="拒绝本次全部建议"]');
    const reason = field<HTMLSelectElement>(form, 'select[name="reason_code"]');
    expect(document.activeElement).toBe(reason);
    expect([...reason.options].map((option) => option.value)).toEqual([
      "",
      "MANUAL_HANDLING",
      "INSUFFICIENT_CONTEXT",
      "OTHER",
    ]);
    expect(form.textContent).toMatch(/全部.*候选|所有.*候选/);
    expect(button(/确认.*拒绝/, form).disabled).toBe(true);

    await setValue(reason, "INSUFFICIENT_CONTEXT");
    await setValue(field<HTMLTextAreaElement>(form, 'textarea[name="manager_note"]'), "😀".repeat(501));
    expect(button(/确认.*拒绝/, form).disabled).toBe(true);
    expect(calls.rejectSuggestion).not.toHaveBeenCalled();

    await click(/^取消$/, form);
    expect(document.activeElement).toBe(button(/拒绝本次全部建议/));

    await click(/拒绝本次全部建议/);
    const reopened = field<HTMLElement>(container, '[aria-label="拒绝本次全部建议"]');
    await setValue(field<HTMLSelectElement>(reopened, 'select[name="reason_code"]'), "INSUFFICIENT_CONTEXT");
    await click(/确认.*拒绝/, reopened);
    expect(calls.rejectSuggestion).toHaveBeenCalledWith(
      "generation-success-1",
      "INSUFFICIENT_CONTEXT",
      null,
    );
  });
});

describe("recovery controls map to one closed action", () => {
  it("maps unknown, busy, saved-stale, refused, and RESULT_UNKNOWN without synthesizing a POST", async () => {
    const cases: Array<{
      value: StaffingViewState;
      button: RegExp;
      called: keyof Pick<
        StaffingActions,
        "recover" | "retryBusy" | "refresh" | "acknowledgeWrite" | "retryUnknownGeneration"
      >;
    }> = [
      {
        value: view(readySnapshot(), {
          write: {
            status: "unknown",
            ...{ mutation: suggestionMutation(), replayedAfterNotFound: false },
            detail: "response lost",
            recovering: false,
          },
        }),
        button: /恢复|查询.*结果/,
        called: "recover",
      },
      {
        value: view(readySnapshot(), {
          write: {
            status: "busy",
            ...{ mutation: suggestionMutation(), replayedAfterNotFound: false },
            detail: "queue full",
          },
        }),
        button: /同一.*请求|再次提交|重试/,
        called: "retryBusy",
      },
      {
        value: view(readySnapshot(), {
          read: { status: "error", stale: true, detail: "refresh failed" },
          write: {
            status: "committed",
            requestId: "request-exception-1",
            receipt: receipt("exception-recorded"),
            savedButStale: true,
          },
        }),
        button: /刷新/,
        called: "refresh",
      },
      {
        value: view(readySnapshot(), {
          write: {
            status: "rejected",
            operationKind: "exception-record",
            requestId: "request-refused",
            code: "staffing_conflict",
            detail: "revision conflict",
          },
        }),
        button: /知道了|关闭|确认/,
        called: "acknowledgeWrite",
      },
      {
        value: (() => {
          const active = generation("generation-interrupted");
          return view(readySnapshot(active), {
            activeGenerationRequestId: active.request_id,
            activeGeneration: active,
          });
        })(),
        button: /新请求.*重试|重试.*新请求/,
        called: "retryUnknownGeneration",
      },
    ];

    for (const item of cases) {
      const calls = actionSpies();
      await act(async () => {
        root?.unmount();
      });
      root = undefined;
      container.replaceChildren();
      await mount(<ControlledView value={item.value} actions={calls} />);
      await click(item.button);
      expect(calls[item.called]).toHaveBeenCalledOnce();
      const otherCalls = [
        calls.recover,
        calls.retryBusy,
        calls.refresh,
        calls.acknowledgeWrite,
        calls.retryUnknownGeneration,
      ].filter((candidate) => candidate !== calls[item.called]);
      expect(otherCalls.every((candidate) => candidate.mock.calls.length === 0)).toBe(true);
    }
  });
});

describe("panel lifecycle", () => {
  it("makes a late initial read inert after unmount and gives a remount exactly one poller", async () => {
    vi.useFakeTimers();
    const first = deferred<StaffingDateSnapshot>();
    const service = client();
    vi.mocked(service.current)
      .mockImplementationOnce(() => first.promise)
      .mockImplementation(async () => readySnapshot());

    await mount(<StaffingPanel client={service} />);
    expect(service.current).toHaveBeenCalledOnce();
    await act(async () => {
      root!.unmount();
    });
    root = undefined;
    first.resolve(readySnapshot());
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3 * STAFFING_SNAPSHOT_POLL_MS);
    });
    expect(service.current).toHaveBeenCalledOnce();

    container.replaceChildren();
    await mount(<StaffingPanel client={service} />);
    expect(service.current).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STAFFING_SNAPSHOT_POLL_MS - 1);
    });
    expect(service.current).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(service.current).toHaveBeenCalledTimes(3);
  });

  it("polls an ongoing generation by request lookup and never sends a second generation write", async () => {
    vi.useFakeTimers();
    const active = generation("generation-reserved");
    const lookup = deferred<StaffingReceipt>();
    const service = client({
      current: vi.fn(async () => readySnapshot(active)),
      lookup: vi.fn(() => lookup.promise),
    });
    await mount(<StaffingPanel client={service} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(STAFFING_REQUEST_POLL_MS);
    });
    expect(service.lookup).toHaveBeenCalledWith("suggestion-generate", active.request_id);
    expect(service.submit).not.toHaveBeenCalled();
    await act(async () => {
      root!.unmount();
    });
    root = undefined;
    lookup.resolve(receipt("generation-in-progress"));
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3 * STAFFING_REQUEST_POLL_MS);
    });
    expect(service.lookup).toHaveBeenCalledOnce();
    expect(service.submit).not.toHaveBeenCalled();
  });
});
