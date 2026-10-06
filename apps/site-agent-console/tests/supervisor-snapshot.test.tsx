/**
 * The Supervisor Snapshot read and the two compact sections it feeds.
 * The frozen payload comes from the real service; these tests prove the
 * split is lossless, the labels stay separate, and every honest state
 * (pending, unavailable, stale, missing, unknown) renders as words.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { OperationsTodayPanel } from "../components/OperationsTodayPanel";
import { StaffingTodayPanel } from "../components/StaffingTodayPanel";
import { createClient, ManagerApiError, SUPERVISOR_SNAPSHOT_SCHEMA, type OperationsSection, type StaffingSection, type UnavailableSection } from "../lib/api";
import { readConsole } from "../lib/console";
import { joinSnapshot, splitSnapshot } from "../lib/snapshot";
import { sampleSupervisorSnapshot } from "./fixtures";

const snapshot = () => sampleSupervisorSnapshot();
const staffingOf = (s = snapshot()) => s.staffing as StaffingSection;
const operationsOf = (s = snapshot()) => s.operations as OperationsSection;
const clean = (html: string) => {
  expect(html).not.toContain("undefined");
  expect(html).not.toContain(">null<");
  expect(html).not.toContain("NaN");
  return html;
};

describe("splitSnapshot", () => {
  it("is lossless and hands the existing panels their projections unchanged", () => {
    const s = snapshot();
    const parts = splitSnapshot(s);
    expect(parts.health).toEqual(s.health);
    expect(parts.state).toEqual(s.state);
    expect(parts.recommendations).toEqual(s.recommendations);
    expect(parts.briefing).toEqual(s.briefing);
    expect(parts.fixture).toEqual(s.fixture);
    expect(parts.supervisor.staffing).toEqual(s.staffing);
    expect(joinSnapshot(parts)).toEqual(s);
    expect(s.snapshot_schema).toBe(SUPERVISOR_SNAPSHOT_SCHEMA);
    expect(s.coverage_recommendations).toEqual([]);
  });

  it("refuses a foreign snapshot schema or a missing projection instead of reading it partially", () => {
    expect(() => splitSnapshot({ ...snapshot(), snapshot_schema: "nxt-site-agent/supervisor-snapshot/v2" })).toThrow(ManagerApiError);
    const broken = { ...snapshot() } as Record<string, unknown>;
    delete broken.state;
    expect(() => splitSnapshot(broken as never)).toThrow(/state/);
  });

  it("reads exactly one path through the typed client", async () => {
    const urls: string[] = [];
    const client = createClient(async (url) => {
      urls.push(url);
      return new Response(JSON.stringify({ schema: "nxt-site-agent/api/v0", disclaimer: "x", data: snapshot() }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const data = await readConsole(client);
    expect(urls).toEqual(["/api/v0/supervisor-snapshot"]);
    expect(data.supervisor.generation.snapshot_id).toMatch(/^svs_[0-9a-f]{24}$/);
  });
});

describe("Staffing today", () => {
  const render = (staffing: StaffingSection | { status: "unavailable"; code: string; detail: string }, s = snapshot()) =>
    clean(renderToStaticMarkup(<StaffingTodayPanel staffing={staffing} source={s.context_sources.staffing} generation={s.generation} />));

  it("labels planned, recorded, derived and unknown values separately and never says available", () => {
    const html = render(staffingOf());
    expect(html).toContain("Staffing today");
    expect(html).toContain("STAFFING SOURCE OK");
    expect(html).toContain("Operating day 2026-08-08 (Asia/Shanghai)");
    expect(html).toContain("18:30 Asia/Shanghai");
    expect(html).toContain("declared fixture clock");
    expect(html).toMatch(/Scheduled now<\/dt><dd><span class="mono context-value">3<\/span> <span class="badge badge-info">PLANNED<\/span>/);
    expect(html).toMatch(/Clocked in now<\/dt><dd><span class="mono context-value">2<\/span> <span class="badge badge-muted">DERIVED<\/span>/);
    expect(html).toMatch(/Recorded absent today<\/dt><dd><span class="mono context-value">1<\/span> <span class="badge badge-info">RECORDED<\/span>/);
    expect(html).toContain("Presence unknown now");
    expect(html).toContain("Scheduled is not present. Clocked-in is not available. Requested is not approved.");
    expect(html).toContain("RANGE_OPS");
    expect(html).toContain("shift end ×1 at 21:00 Asia/Shanghai");
    expect(html).toContain("3 closed · 932 min");
    expect(html).toContain("coverage to 2026-08-08T10:40:00Z (declared_export_time)");
    expect(html.toLowerCase()).not.toMatch(/available now|availability/);
  });

  it("marks a stale source in words on the badge and on every row, with no green badge", () => {
    const s = snapshot();
    const staffing = staffingOf(s);
    const stale: StaffingSection = {
      ...staffing,
      status: "stale",
      scheduled_now: { ...staffing.scheduled_now, source_status: "stale" },
      confirmed_present_now: { ...staffing.confirmed_present_now, source_status: "stale" },
    };
    const html = render(stale, { ...s, context_sources: { ...s.context_sources, staffing: { ...s.context_sources.staffing, status: "stale", age_s: 9000, reason: "coverage end is 9000 s old; stale after 7200 s" } } });
    expect(html).toContain("STAFFING SOURCE STALE");
    expect(html).not.toContain("badge-ok");
    expect(html.match(/source stale/g)?.length).toBeGreaterThanOrEqual(2);
    expect(html).toContain("9000 s old");
  });

  it("renders a missing source as unknown values with reasons, never as zeros", () => {
    const s = snapshot();
    const staffing = staffingOf(s);
    const unknown = (basis: string, reason: string) => ({ value: null, label: "UNKNOWN" as const, basis, source_status: "missing", reason });
    const missing: StaffingSection = {
      ...staffing,
      status: "missing",
      scheduled_today: unknown("planned shifts", "staffing source missing"),
      scheduled_now: unknown("planned shifts now", "staffing source missing"),
      confirmed_present_now: unknown("clock-ins", "staffing source missing"),
      presence_unknown_now: unknown("unknown", "staffing source missing"),
      confirmed_absent_today: unknown("absences", "staffing source missing"),
      present_unscheduled_now: unknown("unscheduled", "staffing source missing"),
      next_material_change: unknown("next", "staffing source missing"),
      approved_shift_changes_today: unknown("approved", "staffing source missing"),
      pending_change_requests: unknown("pending", "staffing source missing"),
      worked_intervals_today: unknown("worked", "staffing source missing"),
      open_clock_ins: unknown("open", "staffing source missing"),
      by_role: [],
    };
    const html = render(missing, { ...s, context_sources: {} });
    expect(html).toContain("NO STAFFING SOURCE");
    expect(html).not.toContain("badge-ok");
    expect((html.match(/>UNKNOWN</g) ?? []).length).toBe(11);
    expect(html).not.toMatch(/context-value">0</);
    expect(html).toContain("no source declared");
  });

  it("shows an unavailable context as words and nothing partial", () => {
    const html = render({ status: "unavailable", code: "journal_unreadable", detail: "context_journal.jsonl: last record is not newline-terminated (truncated write)" });
    expect(html).toContain("UNAVAILABLE");
    expect(html).toContain("journal_unreadable");
    expect(html).toContain("Nothing partial is shown; no value here is a zero.");
    expect(html).not.toContain("Scheduled now");
    expect(html).not.toContain("badge-ok");
  });
});

describe("Operations today", () => {
  const render = (s = snapshot(), operations: OperationsSection | UnavailableSection = operationsOf(s)) =>
    clean(renderToStaticMarkup(<OperationsTodayPanel operations={operations} sources={s.context_sources} physicalStores={s.physical_stores} machines={s.machines} />));

  it("shows sold ball units as recorded entitlement evidence, never as dispensed balls or inventory", () => {
    const html = render();
    expect(html).toContain("Operations today");
    expect(html).toContain("OPERATIONS SOURCE OK");
    expect(html).toMatch(/Ball units sold today<\/dt><dd><span class="mono context-value">2,450<\/span> <span class="badge badge-muted">DERIVED<\/span>/);
    expect(html).toMatch(/Transactions today<\/dt><dd><span class="mono context-value">15<\/span> <span class="badge badge-info">RECORDED<\/span>/);
    expect(html).toContain("750 ball units · 3 transactions · last 60 min");
    expect(html).toContain("Sold ball units are entitlement evidence only");
    const business = html.slice(0, html.indexOf("Physical stores and machines"));
    expect(business.toLowerCase()).not.toContain("inventory");
    expect(business.toLowerCase()).not.toContain("dispensed");
  });

  it("keeps booked, started and confirmed players apart and leaves unrecorded durations unknown", () => {
    const html = render();
    expect(html).toMatch(/Booked sessions today<\/dt><dd><span class="mono context-value">7<\/span> <span class="badge badge-info">PLANNED<\/span>/);
    expect(html).toMatch(/Started sessions today<\/dt><dd><span class="mono context-value">4<\/span> <span class="badge badge-info">RECORDED<\/span>/);
    expect(html).toMatch(/Active players \(booked headcount\)<\/dt><dd><span class="mono context-value">6<\/span> <span class="badge badge-info">PLANNED<\/span>/);
    expect(html).toMatch(/Active players \(recorded count\)<\/dt><dd><span class="mono context-value">—<\/span> <span class="badge badge-warn">UNKNOWN<\/span>/);
    expect(html).toContain("no actual player count recorded for every active session");
    expect(html).toContain("2 completed · mean 70.5 min (67 min to 74 min)");
    expect(html).toContain("Booked is not started. Started is not finished.");
  });

  it("states physical stores and machines as unknown or as fixture facility state, never as live inventory", () => {
    const html = render();
    expect(html).toContain("Physical stores and machines");
    expect(html).toContain("FIXTURE FACILITY STATE");
    expect(html).toContain("channel sensor · service source fixture · not physical");
    expect((html.match(/>UNKNOWN</g) ?? []).length).toBeGreaterThanOrEqual(6); // weight, awaiting wash, three machines, and the recorded player count
    expect(html).toContain("no weight sensor is connected in this slice");
    expect(html).toContain("business records say nothing about physical ball stores");
  });

  it("shows an unavailable context in words while the unknown physical facts stay visible", () => {
    const s = snapshot();
    const html = render(s, { status: "unavailable", code: "clock_undeclared", detail: "no clock declared" });
    expect(html).toContain("UNAVAILABLE");
    expect(html).toContain("clock_undeclared");
    expect(html).not.toContain("Ball units sold today");
    expect(html).toContain("Physical stores and machines");
    expect(html).not.toContain("badge-ok");
  });
});
