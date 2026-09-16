import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PlanningView, type PlanningActions } from "../components/PlanningPanel";
import type { ConfirmationRecord, InputRecord, PlanRecord, PlanningSnapshot } from "../lib/planning";
import { initialPlanningView, type PlanningView as ViewState } from "../lib/planning-state";

const EXAMPLES = join(import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts", "planning-v1", "examples");
type Exchange = {
  request: { method: string; path: string; schema_ref?: string; body?: unknown };
  response: { body: { data?: Record<string, unknown> } };
};
const load = (name: string) =>
  (JSON.parse(readFileSync(join(EXAMPLES, `${name}.json`), "utf-8")) as { exchanges: Exchange[] }).exchanges;
const success = load("success");
const missing = load("missing-data");
const emptySnapshot = success[0].response.body.data as unknown as PlanningSnapshot;
const fullSnapshot = success[success.length - 1].response.body.data as unknown as PlanningSnapshot;
const record = <T,>(exchanges: Exchange[], definition: string): T =>
  exchanges.find((e) => e.request.schema_ref === `#/$defs/${definition}`)!.response.body.data!.record as T;
const readyPlan = record<PlanRecord>(success, "PlanRequest");
const missingPlan = record<PlanRecord>(missing, "PlanRequest");
const inputRecord = record<InputRecord>(success, "InputRequest");
const confirmationRecord = record<ConfirmationRecord>(success, "ConfirmationRequest");

const actions: PlanningActions = {
  refresh: async () => {},
  saveInput: async () => {},
  requestPlan: async () => {},
  confirmPlan: async () => {},
  recordOutcome: async () => {},
  recover: async () => {},
  acknowledgeWrite: () => {},
};

const view = (patch: Partial<ViewState> = {}): ViewState => ({
  ...initialPlanningView(),
  data: fullSnapshot,
  loading: false,
  ...patch,
});
const render = (state: ViewState) => renderToStaticMarkup(<PlanningView view={state} actions={actions} />);
const button = (html: string, label: string) => html.match(new RegExp(`<button[^>]*>${label}</button>`))?.[0] ?? "";

describe("planning panel", () => {
  it("shows an honest cold start: context only, every evidence unknown, nothing prefilled", () => {
    const html = render(view({ data: emptySnapshot }));
    for (const text of ["MANUAL-LED", "SIMULATION", "pilot-course-a", "pilot-a-edge-task-sim-v0", "Asia/Shanghai", "Z1", "picker-01"]) {
      expect(html).toContain(text);
    }
    expect(html).toContain("No operating input has been recorded");
    expect(html).toContain("No plan has been requested");
    expect(html).toContain("No confirmation");
    expect(html).not.toContain("value=\"0\"");
    expect(html).not.toContain(">0 balls<");
    expect(button(html, "Ask for the system suggestion")).toContain("disabled");
  });

  it("labels each recorded evidence field with its value, source kind, observation time and validity in site time", () => {
    const html = render(view({ data: { ...emptySnapshot, latest_input: inputRecord } }));
    expect(html).toContain("Revision 1");
    expect(html).toContain("600");
    expect(html).toContain("MEASURED");
    expect(html).toContain("opening-clean-bin-count-001");
    expect(html).toContain("MANUAL ESTIMATE");
    expect(html).toContain("2026-09-16 16:00"); // 08:00Z observed, site time
    expect(html).toContain("2026-09-16 17:30"); // evidence valid until
    expect(html).toContain("low 5, 5, 5, 5, 5, 5");
    expect(html).toContain("travel 2");
    expect(html).toContain("wash 3");
    expect(html).not.toContain("probability");
    expect(button(html, "Ask for the system suggestion")).not.toContain("disabled");
  });

  it("shows a missing-data plan with its missing list and refuses confirmation", () => {
    const missingInput = record<InputRecord>(missing, "InputRequest");
    const html = render(view({ data: { ...emptySnapshot, latest_input: missingInput, plans: [{ ...missingPlan, current_status: "MISSING_DATA" }] } }));
    expect(html).toContain("MISSING_DATA");
    expect(html).toContain("inventory_clean_balls");
    expect(html).toContain("no zero inventory or default demand was invented");
    expect(html).toContain("cannot be confirmed");
    expect(html).not.toMatch(/<button[^>]*>Confirm plan/);
  });

  it("renders a READY plan: three manual scenarios, candidates with deadlines, and the original versus adjusted selection", () => {
    const html = render(view({ data: { ...emptySnapshot, latest_input: inputRecord, plans: [{ ...readyPlan, current_status: "READY" }] } }));
    expect(html).toContain("READY");
    expect(html).toContain(">low<");
    expect(html).toContain(">typical<");
    expect(html).toContain(">high<");
    expect(html).toContain("2026-09-16 17:00"); // typical baseline stockout 09:00Z
    expect(html).toContain("2026-09-16 16:40"); // typical safety stock 08:40Z
    expect(html).toContain("No shortage within the supplied horizon");
    expect(html).toContain("-300"); // high baseline end balls, signed
    expect(html).toContain("Manual demand assumptions");
    expect(html).toContain("eligible");
    expect(html).toContain("2026-09-16 16:09"); // latest start 08:09:40Z
    expect(html).toContain("2026-09-16 16:20"); // supply at
    expect(html).toContain("400");
    expect(html).toContain("500");
    expect(html).toContain("System suggestion");
    expect(html).toContain("2026-09-16 16:01"); // system start
    expect(html).toContain("2026-09-16 16:05"); // manual start
    expect(html).toContain("Delay departure four minutes");
    expect(button(html, "Confirm plan version 1")).not.toContain("disabled");
    expect(html).toContain("Restore the system suggestion");
    expect(html).not.toMatch(/P\d\d\b|probability interval|confidence interval|% likely/i);
  });

  for (const status of ["INFEASIBLE", "EXPIRED", "INVALIDATED"] as const) {
    it(`refuses to confirm a ${status} plan and says why`, () => {
      const html = render(view({ data: { ...emptySnapshot, latest_input: inputRecord, plans: [{ ...readyPlan, status: status === "INVALIDATED" ? "READY" : status, current_status: status }] } }));
      expect(html).toContain(status);
      expect(html).not.toMatch(/<button[^>]*>Confirm plan/);
      expect(html).toContain("cannot be confirmed");
    });
  }

  it("shows a confirmed plan with its schedule and task links, immutable, and all four result stages", () => {
    const html = render(view());
    expect(html).toContain("CONFIRMED");
    expect(html).toContain("confirmation_example_001");
    expect(html).toContain("schedule_example_001");
    expect(html).toContain("DISPATCHED");
    expect(html).toContain("task_example_001");
    expect(html).toContain("can no longer be revised");
    expect(html).not.toMatch(/<button[^>]*>Confirm plan/);
    for (const stage of ["COLLECTED", "UNLOADED", "WASHED", "SUPPLIED"]) expect(html).toContain(stage);
    expect(html).toContain("520");
    expect(html).toContain("470");
    expect(html).toContain("work-log-001-supplied");
    expect(html).toContain("does not change the clean inventory");
    expect(html).toContain("Correct this stage");
    expect(html).toContain("outcome_example_004");
  });

  it("keeps unrecorded stages unknown and never derives a quantity from task success", () => {
    const html = render(view({ data: { ...fullSnapshot, outcomes: [] } }));
    expect(html.match(/Not recorded/g)?.length).toBe(4);
    expect(html).not.toContain("520");
    expect(html).toContain("A successful task does not tell you how many balls were collected");
    expect(html).toMatch(/<button[^>]*>Record COLLECTED<\/button>/);
  });

  it("disables every planning write while the view is stale and says so", () => {
    const html = render(view({ error: "fetch failed" }));
    expect(html).toContain("STALE");
    expect(html).toContain("fetch failed");
    expect(html).toContain("Planning changes are disabled");
    expect(html).not.toMatch(/<button(?![^>]*disabled)[^>]*>(Record [A-Z]+|Correct this stage|Save (a )?revision|Ask for the system suggestion|Apply adjustment|Restore the system suggestion|Confirm plan[^<]*)<\/button>/);
  });

  it("holds an unknown write with its request ID, offers recovery, and blocks other writes", () => {
    const html = render(view({ write: { status: "unknown", kind: "confirmations", requestId: "confirmations-abc", body: {} as never, detail: "planning_result_unknown: no receipt", recovering: false } }));
    expect(html).toContain("UNKNOWN OUTCOME");
    expect(html).toContain("confirmations-abc");
    expect(html).toContain("not retried");
    expect(button(html, "Recover by request ID")).not.toContain("disabled");
    expect(html).not.toMatch(/<button(?![^>]*disabled)[^>]*>Record [A-Z]+<\/button>/);
  });

  it("distinguishes a saved write with a stale view from an unknown outcome", () => {
    const receipt = { schema: "nxt-planning/v1", disposition: "created", request_id: "confirmations-abc", record: confirmationRecord } as const;
    const html = render(view({ error: "fetch failed", write: { status: "committed", kind: "confirmations", requestId: "confirmations-abc", receipt: receipt as never } }));
    expect(html).toContain("SAVED");
    expect(html).toContain("confirmation_example_001");
    expect(html).toContain("view could not be refreshed");
    expect(html).not.toContain("UNKNOWN OUTCOME");
  });

  it("reports a duplicate receipt under its original committed request ID", () => {
    const receipt = { schema: "nxt-planning/v1", disposition: "duplicate", request_id: "demo-confirm-001", record: confirmationRecord } as const;
    const html = render(view({ write: { status: "committed", kind: "confirmations", requestId: "confirmations-new", receipt: receipt as never } }));
    expect(html).toContain("already recorded");
    expect(html).toContain("demo-confirm-001");
    expect(html).toContain("confirmations-new");
  });

  it("explains a contract refusal by code without guessing and lets the manager dismiss it", () => {
    const html = render(view({ write: { status: "rejected", kind: "plans", requestId: "plans-1", code: "planning_conflict", detail: "plan requires the current input revision" } }));
    expect(html).toContain("REFUSED");
    expect(html).toContain("planning_conflict");
    expect(html).toContain("plan requires the current input revision");
    expect(button(html, "Dismiss")).not.toContain("disabled");
  });

  it("explains an older service without the planning route instead of showing stale data", () => {
    const html = render({ ...initialPlanningView(), loading: false, unavailable: true, error: "not_found: unknown API path" });
    expect(html).toContain("not available on this service");
    expect(html).not.toContain("STALE");
  });

  it("never renders literal null or undefined", () => {
    for (const state of [view(), view({ data: emptySnapshot }), view({ data: { ...fullSnapshot, outcomes: [] } })]) {
      const html = render(state);
      expect(html).not.toContain(">null<");
      expect(html).not.toContain("undefined");
    }
  });
});
