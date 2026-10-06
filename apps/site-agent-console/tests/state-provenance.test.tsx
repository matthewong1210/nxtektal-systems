/**
 * Behavior-parity regressions for the state panel: provenance is published
 * but unverified unless the grade is recognized, each channel's source kind
 * comes from its own reference, the reading verdict is the console's own
 * worst-of over the channels, and a legitimate zero stays zero.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { provenanceLabel, readingVerdict, sourceTypeBadge, StatePanel } from "../components/StatePanel";
import { sampleState } from "./fixtures";

const panel = (state = sampleState()) => renderToStaticMarkup(<StatePanel state={state} />);

describe("provenance grade", () => {
  it("is published but unverified when missing, absent or unrecognized", () => {
    expect(provenanceLabel(null)).toBe("Published but unverified");
    expect(provenanceLabel(undefined)).toBe("Published but unverified");
    expect(provenanceLabel("")).toBe("Published but unverified");
    expect(provenanceLabel("verified")).toBe("Published but unverified");
    expect(provenanceLabel("HIGH")).toBe("Published but unverified");
  });

  it("shows only the grades the assembler is known to emit", () => {
    expect(provenanceLabel("high")).toBe("high");
    expect(provenanceLabel("medium")).toBe("medium");
    expect(provenanceLabel("low")).toBe("low");
  });

  it("renders the unverified label when the report is absent from the state", () => {
    const state = sampleState({ quality: { assembly_report: null, runtime_quality: null } });
    expect(panel(state)).toContain("Published but unverified");
  });
});

describe("row-level source kind", () => {
  it("labels each channel from its own source reference, independent of any fixture-mode flag", () => {
    const state = sampleState();
    state.dispenser!.count_source!.source_type = "simulation";
    state.dispenser!.sensed_source = { ...state.dispenser!.count_source!, channel: "inventory.dispenser.sensed", source_type: "sensor" };
    const html = panel(state);
    expect(html).toContain("SIMULATED");
    expect(html).toContain("SENSOR");
    expect(html).toContain("inventory.dispenser.sensed");
  });

  it("labels a sensor-bound channel as fixture data while the service replays a fixture, and still honors a row that says simulation", () => {
    const state = sampleState();
    state.dispenser!.count_source!.source_type = "sensor";
    const live = renderToStaticMarkup(<StatePanel state={state} fixtureSource={false} />);
    expect(live).toContain(">SENSOR<");
    const fixture = renderToStaticMarkup(<StatePanel state={state} fixtureSource />);
    expect(fixture).toContain("SENSOR BINDING · FIXTURE DATA");
    expect(fixture).not.toContain(">SENSOR<");
    state.dispenser!.count_source!.source_type = "simulation";
    expect(renderToStaticMarkup(<StatePanel state={state} fixtureSource={false} />)).toContain("SIMULATED");
    expect(sourceTypeBadge({ ...state.dispenser!.count_source!, source_type: "sensor" }, true).tone).toBe("sim");
  });

  it("reports an absent or unrecognized source kind as unknown rather than guessing", () => {
    expect(sourceTypeBadge(null).label).toBe("SOURCE TYPE UNKNOWN");
    expect(sourceTypeBadge({ ...sampleState().dispenser!.count_source!, source_type: "robot" }).label).toBe("SOURCE TYPE UNKNOWN");
    expect(panel()).toContain("SOURCE TYPE UNKNOWN"); // the sample carries no source_type
  });
});

describe("reading verdict", () => {
  it("is the console's own worst-of verdict, never better than the service aggregate", () => {
    const state = sampleState();
    state.dispenser!.sensed_source = { ...state.dispenser!.count_source!, channel: "inventory.dispenser.sensed", status: "stale" };
    expect(readingVerdict(state.dispenser!, state.quality!.assembly_report).status).toBe("stale");
    const html = panel(state);
    expect(html).toContain("reading STALE");
    expect(html).toContain("service aggregate: OK");
    expect(html).not.toContain("reading OK");
  });

  it("honors the assembly report's channel lists over an ok aggregate", () => {
    const state = sampleState();
    state.quality!.assembly_report!.missing_channels = ["inventory.dispenser.count"];
    expect(readingVerdict(state.dispenser!, state.quality!.assembly_report).status).toBe("missing");
    expect(panel(state)).toContain("reading MISSING");
  });

  it("agrees with the aggregate when every channel is ok and shows no aggregate note", () => {
    const html = panel();
    expect(html).toContain("reading OK");
    expect(html).not.toContain("service aggregate");
  });

  it("keeps a legitimate zero as zero and never as unknown", () => {
    const state = sampleState();
    state.dispenser!.clean_available_balls = 0;
    state.dispenser!.clean_sensed_balls = 0;
    const html = panel(state);
    expect(html).toContain('<span class="inventory-number mono">0</span>');
    expect(html).toContain("reading OK");
    expect(html).not.toContain(">—<");
  });

  it("renders an unknown value as a dash, not zero, even with an ok source", () => {
    const state = sampleState();
    state.dispenser!.clean_available_balls = null;
    const html = panel(state);
    expect(html).toContain('<span class="inventory-number mono">—</span>');
    expect(html).not.toContain('<span class="inventory-number mono">0</span>');
  });
});
