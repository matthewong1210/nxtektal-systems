/**
 * Behavior-parity regressions for the Manager API view: a stale view shows no
 * green badge in any section and says so in text, last-known values stay
 * visible, and the prerendered page is the pending state.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ConsoleScreen } from "../components/ConsoleScreen";
import { initialConsoleView, type ConsoleView } from "../lib/actions";
import type { ConsoleActions, ConsoleData } from "../lib/console";
import { sampleBriefing, sampleFixture, sampleHealth, sampleRecommendation, sampleState } from "./fixtures";

const noop = async () => {};
const actions: ConsoleActions = { refresh: noop, respond: noop, advance: noop, restart: noop, reset: noop };
const data = (): ConsoleData => ({
  health: sampleHealth(),
  state: sampleState(),
  recommendations: [sampleRecommendation()],
  briefing: sampleBriefing(),
  fixture: sampleFixture(),
});
const screen = (view: ConsoleView<ConsoleData>) => renderToStaticMarkup(<ConsoleScreen view={view} actions={actions} />);

describe("stale Manager API view", () => {
  it("shows green badges only while the view is current", () => {
    const fresh = screen({ data: data(), loading: false, busy: false, error: null, notice: null });
    expect(fresh).toContain("badge-ok");
    expect(fresh).toContain(">SERVING<");
    expect(fresh).not.toContain("stale view");
  });

  it("downgrades every green badge in every section and says so in text, while keeping the last-known values", () => {
    const stale = screen({ data: data(), loading: false, busy: false, error: "fetch failed", notice: null });
    expect(stale).not.toContain("badge-ok");
    expect(stale).toContain("STALE");
    expect(stale).toContain("SERVING · stale view");
    expect(stale).toContain("reading OK · stale view");
    expect(stale).toContain("READY_FOR_FIXTURE_SHADOW_MODE · stale view");
    expect(stale).toContain("2,400"); // last-known inventory remains visible
    expect(stale).toContain("Showing the last successful view");
  });

  it("keeps a recognized provenance grade and the unverified label unchanged under a stale view", () => {
    const state = sampleState();
    state.quality!.assembly_report!.provenance_grade = "weird";
    const stale = screen({ data: { ...data(), state }, loading: false, busy: false, error: "fetch failed", notice: null });
    expect(stale).toContain("Published but unverified");
    const fresh = screen({ data: { ...data(), state }, loading: false, busy: false, error: null, notice: null });
    expect(fresh).toContain("Published but unverified");
  });
});

describe("pending state, including the prerendered page", () => {
  it("renders the initial view as loading with no projection, no inventory and no green badge", () => {
    const html = screen(initialConsoleView<ConsoleData>());
    expect(html).toContain("Loading the Site Agent projections");
    expect(html).not.toContain("badge-ok");
    expect(html).not.toContain("clean balls in dispenser");
    expect(html).not.toContain("Service Unreachable");
  });

  it("keeps a read that has started but not completed in the loading state", () => {
    const html = screen({ data: null, loading: true, busy: false, error: null, notice: null });
    expect(html).toContain("Loading the Site Agent projections");
    expect(html).not.toContain("badge-ok");
  });
});
