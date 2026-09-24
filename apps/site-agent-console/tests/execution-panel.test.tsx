/**
 * Static renders of the read-only collection execution panel over the
 * frozen SIMULATION contract examples (see execution-fixtures.ts). These
 * tests prove copy and state separation; they are not a live integration.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CollectionExecutionView, type ExecutionReadView } from "../components/execution/CollectionExecutionPanel";
import { exampleSnapshot, pausedSnapshot, pendingSnapshot, runningSnapshot } from "./execution-fixtures";

const noop = () => {};
const view = (data: ExecutionReadView["data"], patch: Partial<ExecutionReadView> = {}): ExecutionReadView => ({
  data, loading: false, error: null, unavailable: false, invalid: false, lastReadAtMs: 1_000, nowMs: 6_000, ...patch,
});
const render = (v: ExecutionReadView, nowMs = 6_000) => renderToStaticMarkup(<CollectionExecutionView view={{ ...v, nowMs }} onRetry={noop} />);
const clean = (html: string) => {
  expect(html).not.toContain("undefined");
  expect(html).not.toContain(">null<");
  expect(html).not.toContain("NaN");
  return html;
};

describe("session strip: connection freshness, simulation clock and session state stay separate", () => {
  it("shows identity, simulation time, ACTIVE state and a fresh read without implying execution authority", () => {
    const html = clean(render(view(exampleSnapshot("success"))));
    expect(html).toContain("SIMULATION");
    expect(html).toContain("READ ONLY");
    expect(html).toContain("fixture-series");
    expect(html).toContain("fixture-session-v3");
    expect(html).toContain("round-001");
    expect(html).toContain("SESSION ACTIVE");
    expect(html).toContain("t = 660 s");
    expect(html).toContain("2026-09-16T00:11:00 UTC");
    expect(html).toContain("2026-09-23T01:00:00 UTC");
    expect(html).toContain("READ FRESH");
    expect(html).toContain("5 s ago");
    expect(html).toContain("grants no execution authority");
    expect(html).not.toContain("SESSION PAUSED");
  });

  it("marks a PAUSED session on the simulation clock while the service read is fresh", () => {
    const html = clean(render(view(pausedSnapshot())));
    expect(html).toContain("SESSION PAUSED");
    expect(html).toContain("frozen at t = 660 s");
    expect(html).toContain("READ FRESH");
    expect(html).not.toContain("SESSION ACTIVE");
  });

  it("keeps the last valid snapshot and labels the read STALE with its age when a later read fails", () => {
    const html = clean(render(view(exampleSnapshot("success"), { error: "fetch failed", lastReadAtMs: 1_000 }), 31_000));
    expect(html).toContain("READ STALE");
    expect(html).toContain("fetch failed");
    expect(html).toContain("read 30 s ago");
    expect(html).toContain("SESSION ACTIVE"); // last valid snapshot still shown, unchanged
    expect(html).toContain("Retry execution read");
  });

  it("explains a contract check failure as a read failure and never renders the rejected response", () => {
    const html = clean(render(view(exampleSnapshot("success"), { error: "Collection execution evidence is incomplete or inconsistent.", invalid: true })));
    expect(html).toContain("READ STALE");
    expect(html).toContain("did not pass the collection-execution contract check");
    expect(html).toContain("was not rendered");
  });

  it("is OFFLINE with the contract message when the first read is rejected, with no cards", () => {
    const html = clean(render(view(null, { error: "Collection execution evidence is incomplete or inconsistent.", invalid: true, lastReadAtMs: null })));
    expect(html).toContain("OFFLINE");
    expect(html).toContain("did not pass the collection-execution contract check");
    expect(html).not.toContain("exec-record");
  });

  it("says UNAVAILABLE when the route is missing and does not call it a live integration", () => {
    const html = clean(render(view(null, { unavailable: true, error: "This service does not provide collection executions.", lastReadAtMs: null })));
    expect(html).toContain("UNAVAILABLE");
    expect(html).toContain("not connected");
    expect(html).not.toContain("exec-record");
  });
});

describe("evidence ladder: admission, durable request, device acceptance, start, collection, unloading", () => {
  it("renders a bound task without a durable request as bound only", () => {
    const html = clean(render(view(exampleSnapshot("identity-conflict"))));
    expect(html).toContain("Bound, no durable execution request yet");
    expect(html).toContain("task_555555555555555555555555");
    expect(html).not.toContain("SUCCEEDED");
    expect(html).not.toContain("PENDING");
  });

  it("shows the admission time only as a lower bound and the durable request separately", () => {
    const html = clean(render(view(exampleSnapshot("success"))));
    expect(html).toContain("1 · Task admitted");
    expect(html).toContain("Admitted (lower bound) at 2026-09-16T00:01:00 UTC");
    expect(html).toContain("not the start time");
    expect(html).toContain("2 · Execution request durable");
    expect(html).toContain("Durable request #1");
    expect(html).toContain("attempt-001");
    expect(html).toContain("3 · Device acceptance");
    expect(html).toContain("Accepted by the simulated device");
  });

  it("keeps a PENDING request as accepted but not started, with both quantities as not yet evidenced", () => {
    const html = clean(render(view(pendingSnapshot())));
    expect(html).toContain("PENDING");
    expect(html).toContain("Not started yet · waiting for a policy slot");
    expect(html).toContain("Eligible from t = 60 s");
    expect(html).toContain("latest start before t = 300 s");
    expect(html).toContain("not a stop time");
    expect(html).toContain("No collection evidence yet");
    expect(html).toContain("No unloading evidence yet");
    expect(html).not.toContain("0 balls");
    expect(html).not.toContain("Did not occur");
  });

  it("does not call a started attempt collecting while it is still travelling", () => {
    const html = clean(render(view(runningSnapshot("TRAVEL_TO_COLLECTION"))));
    expect(html).toContain("RUNNING");
    expect(html).toContain("Started at t = 120 s");
    expect(html).toContain("may still be travelling");
    expect(html).toContain("Travelling to the zone");
    expect(html).toContain("Not yet at the zone");
    expect(html).not.toContain("Collecting now");
    expect(html).not.toContain("balls · ledger-backed");
  });

  it("reports collecting without a quantity until the assignment ends", () => {
    const html = clean(render(view(runningSnapshot("COLLECTING"))));
    expect(html).toContain("Collecting now");
    expect(html).toContain("quantity is reported when the assignment ends");
    expect(html).not.toContain("0 balls");
  });

  it("labels balls on the robot as a milestone, never as completion", () => {
    const html = clean(render(view(runningSnapshot("RAW_COLLECTED_TO_ROBOT"))));
    expect(html).toContain("Balls are on the robot");
    expect(html).toContain("milestone, not success");
    expect(html).not.toContain("SUCCEEDED");
  });
});

describe("results: success only when the contract allows it, quantities in three states, downstream stages independent", () => {
  it("shows success with both ledger quantities and keeps washing, supply and manager outcomes independent", () => {
    const html = clean(render(view(exampleSnapshot("success"))));
    expect(html).toContain("SUCCEEDED");
    expect(html).toMatch(/Collected into <span class="mono">R1<\/span>/);
    expect(html).toMatch(/Unloaded to <span class="mono">H1<\/span>/);
    expect((html.match(/44 balls · ledger-backed/g) ?? []).length).toBe(2);
    expect(html).toContain("All collected balls were unloaded to the bound station");
    expect(html).toContain("success_display_allowed");
    expect(html).toContain("Washing, supply and manager-recorded outcomes are independent");
    expect(html).toContain("Ended at t = 660 s");
    expect(html).toContain("execution deadline t = 780 s");
  });

  it("shows a PARTIAL attempt with the collected quantity and an unload that the contract proves did not occur", () => {
    const html = clean(render(view(exampleSnapshot("partial-preempted"))));
    expect(html).toContain("PARTIAL");
    expect(html).toContain("12 balls · ledger-backed");
    expect(html).toContain("Did not occur (NOT_REACHED, contract evidence)");
    expect(html).toContain("redirected the leased robot");
    expect(html).toContain("milestone, not completion");
    expect(html).toContain("unknown:partial_execution");
    expect(html).not.toContain("SUCCEEDED");
  });

  it("shows a MISSED request that never started, with both milestones proven not reached", () => {
    const html = clean(render(view(exampleSnapshot("policy-missed"))));
    expect(html).toContain("MISSED");
    expect(html).toContain("Did not start (contract evidence)");
    expect(html).toContain("never yielded a Wait slot");
    expect((html.match(/Did not occur \(NOT_REACHED, contract evidence\)/g) ?? []).length).toBe(2);
    expect(html).toContain("unknown:policy_slot_missed");
    expect(html).not.toContain("0 balls");
  });

  it("shows a safety REJECTED start with its rejected tick and no invented quantity", () => {
    const html = clean(render(view(exampleSnapshot("safety-rejected"))));
    expect(html).toContain("REJECTED");
    expect(html).toContain("final safety check rejected");
    expect(html).toContain("Arbitration trace (1 tick)");
    expect(html).toContain("unknown:safety_rejected");
    expect(html).toContain("Did not start (contract evidence)");
  });

  it("keeps a restart INCONCLUSIVE outcome unknown: null quantities, device protection, no recovery control", () => {
    const html = clean(render(view(exampleSnapshot("restart-unknown"))));
    expect(html).toContain("INCONCLUSIVE");
    expect((html.match(/Unknown · evidence incomplete or conflicting \(INCOMPLETE\)/g) ?? []).length).toBe(2);
    expect(html).toContain("DEVICE PROTECTED");
    expect(html).toContain("RESTART_UNKNOWN");
    expect(html).toContain("new authorization blocked");
    expect(html).toContain("outcome is unknown");
    expect(html).not.toContain("0 balls");
    expect(html).not.toMatch(/<button[^>]*>(Recover|Retry the request|Resume)/);
  });

  it("presents a terminal conflict as INCONCLUSIVE with both Edge claims and never as success, even with complete quantities", () => {
    const html = clean(render(view(exampleSnapshot("terminal-conflict"))));
    expect(html).toContain("INCONCLUSIVE");
    expect(html).toContain("EVIDENCE CONFLICT");
    expect(html).toContain("terminal claims SUCCEEDED, FAILED");
    expect(html).toContain("verification CONFLICT");
    expect((html.match(/44 balls · ledger-backed/g) ?? []).length).toBe(2);
    expect(html).toContain("does not establish success");
    expect(html).not.toMatch(/badge-ok">SUCCEEDED/);
  });

  it("renders one card for a replayed duplicate request and exposes its running continuation in the trace", () => {
    const html = clean(render(view(exampleSnapshot("duplicate-request"))));
    expect((html.match(/class="dispatch-record exec-record"/g) ?? []).length).toBe(1);
    expect(html).toContain("RUNNING_CONTINUATION");
    expect(html).toContain("Arbitration trace (2 ticks)");
  });

  it("labels the robot/zone mapping as a synthetic fixture mapping and never as a physical alias", () => {
    const html = clean(render(view(exampleSnapshot("success"))));
    expect(html).toContain("picker-01");
    expect(html).toContain("NEAR_LEFT");
    expect(html).toContain("synthetic fixture mapping");
    expect(html).toContain("reconciliation NOT_PERFORMED");
  });
});

describe("presentation helpers never add judgement of their own", () => {
  it("refuses to paint SUCCEEDED green when the contract flag is not set, and never invents zero", async () => {
    const { stateBadge, quantityText } = await import("../components/execution/shared");
    const success = exampleSnapshot("success").executions[0];
    expect(stateBadge(success)).toEqual({ label: "SUCCEEDED", tone: "ok" });
    // Hand-built (not parser-admitted) record: the helper still must not show success.
    expect(stateBadge({ ...success, success_display_allowed: false })).toEqual({ label: "SUCCESS NOT DISPLAYABLE", tone: "warn" });
    expect(stateBadge({ ...success, state: "PARTIAL" })).toEqual({ label: "PARTIAL", tone: "warn" });
    const raw = success.raw_quantity;
    expect(quantityText({ ...raw, status: "COMPLETE", balls: 0 }).label).toBe("0 balls · ledger-backed (COMPLETE)"); // zero only with complete proof
    expect(quantityText({ ...raw, status: "NOT_REACHED", balls: null }).label).toContain("Did not occur");
    expect(quantityText({ ...raw, status: "INCOMPLETE", balls: null }).label).toContain("Unknown");
    expect(quantityText({ ...raw, status: "INCOMPLETE", balls: null }).label).not.toContain("0 balls");
  });
});
