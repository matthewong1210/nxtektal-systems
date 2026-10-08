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

  it("introduces the panel as following confirmed collection tasks (plural, source-neutral) and never as one preset task", () => {
    const html = clean(render(view(exampleSnapshot("success"))));
    expect(html).toContain("Follow confirmed collection tasks through this simulated session: admission, durable request, device acceptance, start, collection, unloading and terminal evidence.");
    expect(html).not.toContain("Follow one confirmed collection task");
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

describe("3C acceptance renders: human assistance, ENDED, and the backend-generated witness", () => {
  it("shows an accepted human-assistance exit as a protected non-success with no browser control", async () => {
    const { humanAssistanceSnapshot } = await import("./execution-fixtures");
    const html = clean(render(view(humanAssistanceSnapshot())));
    expect(html).toContain("PARTIAL");
    expect(html).toContain("HUMAN_ASSISTANCE_REQUIRED");
    expect(html).toContain("requires a person");
    expect(html).toContain("not resumable from the browser");
    expect(html).toContain("DEVICE PROTECTED");
    expect(html).toContain("human assistance required");
    expect(html).toContain("new authorization blocked");
    expect(html).toContain("RequestHumanAssistance");
    expect(html).toContain("ORIGINAL_POLICY_UNCHANGED");
    expect(html).toContain("12 balls · ledger-backed");
    expect(html).not.toMatch(/badge-ok">SUCCEEDED/);
    expect(html).not.toMatch(/<button[^>]*>(Resume|Release|Clear protection|Acknowledge)/);
  });

  it("marks an ENDED session while keeping its terminal record", async () => {
    const { endedSnapshot } = await import("./execution-fixtures");
    const html = clean(render(view(endedSnapshot())));
    expect(html).toContain("SESSION ENDED");
    expect(html).toContain("The session has ended");
    expect(html).toContain("SUCCEEDED");
    expect(html).not.toContain("SESSION ACTIVE");
  });

  it("renders the backend-generated normal-loop witness (a checked-in fixture, not a live read)", async () => {
    const { witnessSnapshot } = await import("./execution-fixtures");
    const html = clean(render(view(witnessSnapshot())));
    expect(html).toContain("collection-execution-session-v3");
    expect(html).toContain("task_b32398701c03d4a1fb2a0106");
    expect(html).toContain("SUCCEEDED");
    expect((html.match(/600 balls · ledger-backed/g) ?? []).length).toBe(2);
    expect(html).toContain("Admitted (lower bound) at 2026-09-16T08:10:00 UTC");
    expect(html).toContain("Started at t = 29400 s");
    expect(html).toContain("Ended at t = 30118.034 s");
    expect(html).toContain("execution deadline t = 30600 s");
    expect(html).toContain("2035-01-02T03:04:05 UTC"); // the wall-clock service read, shown apart from simulation time
    expect(html).toContain("t = 30600 s"); // simulation now
    expect(html).toContain("Arbitration trace (2 ticks)");
    expect(html).toContain("ORIGINAL_POLICY_CONVERGED");
  });
});

describe("task panel: source-neutral device copy and the shared simulation clock", () => {
  const actions = { schedule: async () => {}, cancel: async () => {}, respond: async () => {}, refresh: async () => {} };
  it("names the transport as reported and never infers a device kind from it", async () => {
    const { DispatchView } = await import("../components/DispatchPanel");
    const { INITIAL_TASK_OPS_VIEW } = await import("../lib/task-ops");
    const { taskOpsFixture } = await import("./task-ops-fixtures");
    const html = clean(renderToStaticMarkup(<DispatchView view={{ ...INITIAL_TASK_OPS_VIEW, data: taskOpsFixture(), loading: false }} actions={actions} />));
    expect(html).toContain("Transport in_memory");
    expect(html).toContain("Review collection schedules, follow task progress, and record staff handling in one place.");
    expect(html).not.toContain("Schedule a collection, follow its progress");
    expect(html).toContain("not inferred here");
    expect(html).toContain("No physical robot or CE82A is connected");
    expect(html).not.toContain("protocol double");
    expect(html).not.toContain("Protocol-double");
    expect(html).not.toContain("Mock");
    const mqtt = renderToStaticMarkup(<DispatchView view={{ ...INITIAL_TASK_OPS_VIEW, data: taskOpsFixture({ transport: "mqtt" }), loading: false }} actions={actions} />);
    expect(mqtt).toContain("Transport mqtt");
    expect(mqtt).not.toContain("protocol double");
  });

  it("compares schedule dates with a fresh simulation clock and disables scheduling on a stale one", async () => {
    const { DispatchView } = await import("../components/DispatchPanel");
    const { INITIAL_TASK_OPS_VIEW } = await import("../lib/task-ops");
    const { taskOpsFixture } = await import("./task-ops-fixtures");
    const base = { ...INITIAL_TASK_OPS_VIEW, data: taskOpsFixture(), loading: false };
    const fresh = clean(renderToStaticMarkup(<DispatchView view={base} actions={actions} simulationClock={{ status: "fresh", nowMs: Date.parse("2026-09-16T08:30:00Z"), utc: "2026-09-16T08:30:00Z", sessionState: "ACTIVE" }} />));
    expect(fresh).toContain("Simulation time 2026-09-16 08:30:00 UTC · session ACTIVE");
    expect(fresh).toContain("compared with simulation time 2026-09-16 08:30:00 UTC, not the wall clock");
    expect(fresh).not.toMatch(/id="dispatch-robot"[^>]*disabled/);
    const stale = clean(renderToStaticMarkup(<DispatchView view={base} actions={actions} simulationClock={{ status: "stale", detail: "the last successful collection execution read is older than 15 s" }} />));
    expect(stale).toContain("Changes are disabled. The simulation clock reading is not fresh (the last successful collection execution read is older than 15 s)");
    expect(stale).toMatch(/id="dispatch-robot"[^>]*disabled/);
    expect(stale).toContain("scheduling is disabled");
  });
});

describe("simulationClockFor never falls back to the wall clock once a V3 snapshot has been seen", () => {
  it("is stale on a 404 after a successful read, unavailable only when no snapshot was ever read", async () => {
    const { simulationClockFor } = await import("../components/execution/CollectionExecutionPanel");
    const data = exampleSnapshot("success");
    const seen = view(data, { unavailable: true, error: "collection_execution_not_found: route not connected", lastReadAtMs: 1_000, nowMs: 6_000 });
    const clock = simulationClockFor(seen);
    expect(clock.status).toBe("stale");
    expect(clock.status === "stale" ? clock.detail : "").toContain("404");
    expect(simulationClockFor(view(null, { unavailable: true, error: "collection_execution_not_found: route not connected", lastReadAtMs: null }))).toEqual({ status: "unavailable" });
    expect(simulationClockFor(view(data)).status).toBe("fresh");
    expect(simulationClockFor(view(data, { nowMs: 31_000 })).status).toBe("stale");
    expect(simulationClockFor(view(data, { error: "fetch failed" })).status).toBe("stale");
  });
});

describe("continuous V4: the backend-generated two-task witness, read directly and rendered in service order", () => {
  const CARD_ATTRIBUTE = /data-testid="execution-record-card"[^>]*data-execution-id="([a-f0-9]{64})"/g;
  const cardOrder = (html: string) => [...html.matchAll(CARD_ATTRIBUTE)].map((match) => match[1]);
  const cardHtml = (html: string, executionId: string) => {
    const start = html.indexOf(`data-execution-id="${executionId}"`);
    expect(start, executionId).toBeGreaterThan(-1);
    const next = html.indexOf('data-testid="execution-record-card"', start + 1);
    return html.slice(start, next === -1 ? undefined : next);
  };
  const RUNNING_ID = "de1a3919375ff71cea2ea040353a4f8c9ac98a22027c742011c496007d109be1";
  const SUCCEEDED_ID = "77be62473676a232bf89b4953d89b8ae7b042a88f06fe9978e5f274a94ce6dfc";

  it("passes the frozen witness through the real parser with its exact replay digest, ACTIVE session, clock and two records", async () => {
    const { continuousTwoTaskWitness } = await import("./execution-fixtures");
    const witness = continuousTwoTaskWitness();
    expect(witness.replay_digest).toBe("7a1b19b91968c1106d869a13724a1711770beb08a0e972e188718828af259c5f");
    expect(witness.session_state).toBe("ACTIVE");
    expect(witness.now_sim_t_s).toBe(31800);
    expect(witness.session_id).toBe("collection-execution-session-v3");
    // Preserve the service array order; do not sort records by lifecycle state.
    expect(witness.executions.map((row) => [row.execution_id, row.state, row.stage, row.raw_quantity.balls, row.unload_quantity.balls])).toEqual([
      [SUCCEEDED_ID, "SUCCEEDED", "TERMINAL", 600, 600],
      [RUNNING_ID, "RUNNING", "RAW_COLLECTED_TO_ROBOT", 296, 0],
    ]);
  });

  it("renders one locatable card per execution in exactly the witness's array order, with RUNNING and SUCCEEDED on screen together", async () => {
    const { continuousTwoTaskWitness } = await import("./execution-fixtures");
    const witness = continuousTwoTaskWitness();
    const html = clean(render(view(witness)));
    expect(cardOrder(html)).toEqual(witness.executions.map((row) => row.execution_id));
    expect(cardOrder(html)).toEqual([SUCCEEDED_ID, RUNNING_ID]);
    expect((html.match(/class="dispatch-record exec-record"/g) ?? []).length).toBe(2);
    const running = cardHtml(html, RUNNING_ID);
    expect(running).toContain("task_51b158a10d9deba0b1aae545");
    expect(running).toMatch(/badge-info">RUNNING</);
    expect(running).toContain("Balls on the robot (milestone)");
    expect(running).toContain("296 balls · ledger-backed (COMPLETE)");
    expect(running).toContain("0 balls · ledger-backed (COMPLETE)");
    expect(running).toContain("Started at t = 31200 s");
    expect(running).toContain("does not establish success");
    expect(running).not.toContain("SUCCEEDED");
    const succeeded = cardHtml(html, SUCCEEDED_ID);
    expect(succeeded).toContain("task_e67abab7fe5666824af72300");
    expect(succeeded).toMatch(/badge-ok">SUCCEEDED</);
    expect((succeeded.match(/600 balls · ledger-backed/g) ?? []).length).toBe(2);
    expect(succeeded).toContain("Ended at t = 30118.034 s");
    expect(succeeded).toContain("success_display_allowed");
    expect(succeeded).not.toContain("RUNNING");
  });

  it("keeps the session ACTIVE from the snapshot: one terminal record never ends the session, and the only control re-reads", async () => {
    const { continuousTwoTaskWitness } = await import("./execution-fixtures");
    const html = clean(render(view(continuousTwoTaskWitness())));
    expect(html).toContain("SESSION ACTIVE");
    expect(html).toContain("t = 31800 s");
    expect(html).toContain("2026-09-16T08:50:00 UTC");
    expect(html).not.toContain("SESSION ENDED");
    expect(html).not.toContain("The session has ended");
    const buttons = [...html.matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((match) => match[1]);
    expect(buttons).toEqual(["Retry execution read"]);
    expect(html).not.toMatch(/<button[^>]*>[^<]*(start|stop|rerun|recover|resume|cancel)[^<]*<\/button>/i);
    expect(html).not.toContain("Follow one confirmed collection task");
  });

  it("parses the rewound PENDING instant of the same session with the same identities and order, still ACTIVE", async () => {
    const { continuousTwoTaskPendingData, continuousTwoTaskWitness } = await import("./execution-fixtures");
    const { parseCollectionExecutions } = await import("../lib/collection-executions");
    const earlier = parseCollectionExecutions(continuousTwoTaskPendingData());
    const witness = continuousTwoTaskWitness();
    expect(earlier.executions.map((row) => row.execution_id)).toEqual(witness.executions.map((row) => row.execution_id));
    expect(earlier.executions.map((row) => row.state)).toEqual(["SUCCEEDED", "PENDING"]);
    expect(earlier.session_state).toBe("ACTIVE");
    expect(earlier.now_sim_t_s).toBe(31260);
    expect(earlier.executions[0]).toEqual(witness.executions[0]); // the terminal record is byte-for-byte the witness's
    const html = clean(render(view(earlier)));
    expect(cardOrder(html)).toEqual(witness.executions.map((row) => row.execution_id));
    expect(cardHtml(html, RUNNING_ID)).toContain("Not started yet · waiting for a policy slot");
    expect(cardHtml(html, RUNNING_ID)).not.toContain("balls · ledger-backed");
    expect(html).toContain("SESSION ACTIVE");
  });
});
