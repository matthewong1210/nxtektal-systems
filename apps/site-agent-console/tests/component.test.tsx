import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BriefingPanel } from "../components/BriefingPanel";
import { ExceptionsPanel } from "../components/ExceptionsPanel";
import { FixtureControls } from "../components/FixtureControls";
import { RecommendationsPanel } from "../components/RecommendationsPanel";
import { StatePanel } from "../components/StatePanel";
import { StatusBar } from "../components/StatusBar";
import {
  sampleBriefing,
  sampleFixture,
  sampleHealth,
  sampleRecommendation,
  sampleState,
} from "./fixtures";

const noop = async () => {};

describe("StatusBar", () => {
  it("shows a healthy service with its identity and readiness", () => {
    const html = renderToStaticMarkup(<StatusBar health={sampleHealth()} />);
    expect(html).toContain("SERVING");
    expect(html).toContain("pilot-course-a");
    expect(html).toContain("range.closed_loop_collection_handoff");
    expect(html).toContain("READY_FOR_FIXTURE_SHADOW_MODE");
    expect(html).toContain("fixture-backed Shadow Mode");
    expect(html).toContain("run-001");
  });

  it("labels degraded and failed states in text, not color alone", () => {
    const degraded = renderToStaticMarkup(
      <StatusBar health={sampleHealth({ degraded: true })} />,
    );
    expect(degraded).toContain("DEGRADED");
    const failed = renderToStaticMarkup(
      <StatusBar
        health={sampleHealth({
          service_state: "failed",
          degraded: true,
          last_failure_code: "journal_divergence",
          last_failure_detail: "journal record diverged",
        })}
      />,
    );
    expect(failed).toContain("FAILED");
    expect(failed).toContain("journal_divergence");
  });
});

describe("StatePanel", () => {
  it("shows the dispenser inventory with freshness and confidence", () => {
    const html = renderToStaticMarkup(<StatePanel state={sampleState()} />);
    expect(html).toContain("2,400");
    expect(html).toContain("clean balls in dispenser");
    expect(html).toContain("reading");
    expect(html).toContain("OK");
    expect(html).toContain("18:30");
    expect(html).toContain("100%");
    expect(html).toContain("CAL-LC-PILOTA-2026");
  });

  it("renders the explicit no-data state, never a zero", () => {
    const html = renderToStaticMarkup(
      <StatePanel
        state={sampleState({
          available: false,
          envelope: null,
          dispenser: null,
          quality: null,
          reason: "no snapshot envelope has been published yet",
        })}
      />,
    );
    expect(html).toContain("No admitted facility state");
    expect(html).toContain("not zero inventory");
    expect(html).not.toContain(">0<");
  });

  it("labels missing and stale channels explicitly", () => {
    const state = sampleState();
    const html = renderToStaticMarkup(
      <StatePanel
        state={{
          ...state,
          quality: {
            assembly_report: {
              missing_channels: ["inventory.dispenser.count"],
              stale_channels: ["robot.R2.activity"],
              consistency_issues: [],
              overall_confidence: 0.4,
              provenance_grade: "low",
            },
            runtime_quality: state.quality!.runtime_quality,
          },
        }}
      />,
    );
    expect(html).toContain("MISSING");
    expect(html).toContain("STALE");
    expect(html).toContain("inventory.dispenser.count");
    expect(html).toContain("robot.R2.activity");
  });
});

describe("RecommendationsPanel", () => {
  it("shows a pending recommendation with evidence and manager controls", () => {
    const html = renderToStaticMarkup(
      <RecommendationsPanel
        recommendations={[sampleRecommendation()]}
        onRespond={noop}
        busy={false}
      />,
    );
    expect(html).toContain("PENDING");
    expect(html).toContain("operator_intervention");
    expect(html).toContain("Recommend operator intervention");
    expect(html).toContain("collection permission unavailable");
    expect(html).toContain("missing_replenishment_eta");
    expect(html).toContain("Accept");
    expect(html).toContain("Reject");
    expect(html).toContain("does not command any robot");
  });

  it("shows a recorded manager decision without response controls", () => {
    const html = renderToStaticMarkup(
      <RecommendationsPanel
        recommendations={[
          sampleRecommendation({
            case_status: "accepted",
            response_kind: "accept",
            manager_response: {
              kind: "accept",
              operator_id: "mgr-demo-01",
              reason_code: "staffing_available",
              note: "Will refill manually.",
              responded_at: "2026-08-08T19:30:00.000000Z",
            },
          }),
        ]}
        onRespond={noop}
        busy={false}
      />,
    );
    expect(html).toContain("ACCEPTED");
    expect(html).toContain("MANAGER DECISION");
    expect(html).toContain("mgr-demo-01");
    expect(html).not.toContain("Operator ID");
  });

  it("renders an honest empty queue", () => {
    const html = renderToStaticMarkup(
      <RecommendationsPanel
        recommendations={[]}
        onRespond={noop}
        busy={false}
      />,
    );
    expect(html).toContain("QUEUE EMPTY");
    expect(html).toContain("No recommendations");
  });
});

describe("BriefingPanel", () => {
  it("shows the disclaimer, tags, and unresolved items", () => {
    const html = renderToStaticMarkup(
      <BriefingPanel briefing={sampleBriefing()} />,
    );
    expect(html).toContain("SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA");
    expect(html).toContain("OBSERVED");
    expect(html).toContain("MISSING");
    expect(html).toContain("2 admitted");
    expect(html).toContain("1");
    expect(html).toContain("Unresolved");
  });
});

describe("ExceptionsPanel", () => {
  it("lists rejected cycles with their failure codes", () => {
    const html = renderToStaticMarkup(
      <ExceptionsPanel exceptions={sampleBriefing().exceptions} />,
    );
    expect(html).toContain("1 OPEN");
    expect(html).toContain("insufficient_data_quality");
    expect(html).toContain("19:00 dispenser load cell silent");
  });

  it("shows an explicit empty state", () => {
    const html = renderToStaticMarkup(<ExceptionsPanel exceptions={[]} />);
    expect(html).toContain("NONE");
  });

  it("never renders literal null when failure_code is absent", () => {
    const html = renderToStaticMarkup(
      <ExceptionsPanel
        exceptions={[
          {
            kind: "rejected_cycle",
            tag: "MISSING",
            failure_code: null,
            detail: null,
            scenario_time: null,
            cycle_label: null,
          },
        ]}
      />,
    );
    expect(html).not.toContain("null");
    expect(html).not.toContain("undefined");
    expect(html).toContain("unknown failure");
    expect(html).toContain("fixture cycle");
  });

  it("never renders literal undefined when channel is absent", () => {
    const html = renderToStaticMarkup(
      <ExceptionsPanel
        exceptions={[
          { kind: "missing_channel", tag: "MISSING" },
          { kind: "stale_channel", tag: "STALE" },
        ]}
      />,
    );
    expect(html).not.toContain("null");
    expect(html).not.toContain("undefined");
    expect(html.match(/unknown channel/g)).toHaveLength(2);
  });

  it("preserves the existing text when metadata is present", () => {
    const html = renderToStaticMarkup(
      <ExceptionsPanel exceptions={sampleBriefing().exceptions} />,
    );
    expect(html).toContain("insufficient_data_quality");
    expect(html).not.toContain("unknown failure");
  });
});

describe("FixtureControls", () => {
  it("is visually and semantically separated as simulated controls", () => {
    const html = renderToStaticMarkup(
      <FixtureControls
        fixture={sampleFixture()}
        onAdvance={noop}
        onRestart={noop}
        onReset={noop}
        busy={false}
      />,
    );
    expect(html).toContain("SIMULATED — NOT MANAGER ACTIONS");
    expect(html).toContain("panel-fixture");
    expect(html).toContain("Advance one cycle");
    expect(html).toContain("Restart / recover");
    expect(html).toContain("Reset to a new evidence directory");
  });

  it("renders nothing outside fixture mode", () => {
    const html = renderToStaticMarkup(
      <FixtureControls
        fixture={sampleFixture({ fixture_mode: false })}
        onAdvance={noop}
        onRestart={noop}
        onReset={noop}
        busy={false}
      />,
    );
    expect(html).toBe("");
  });

  it("disables every fixture control while an operation is busy", () => {
    // With createActionRunner keeping busy=true until the refresh
    // settles, disabled buttons make a repeated click inert for the
    // whole action→refresh window.
    const html = renderToStaticMarkup(
      <FixtureControls
        fixture={sampleFixture()}
        onAdvance={noop}
        onRestart={noop}
        onReset={noop}
        busy={true}
      />,
    );
    expect(html.match(/disabled=""/g)?.length ?? 0).toBeGreaterThanOrEqual(3);
  });

  it("disables controls the service refuses", () => {
    const html = renderToStaticMarkup(
      <FixtureControls
        fixture={sampleFixture({
          controls: { advance: false, restart: true, reset: true },
          next_cycle: null,
        })}
        onAdvance={noop}
        onRestart={noop}
        onReset={noop}
        busy={false}
      />,
    );
    expect(html).toContain("disabled");
    expect(html).toContain("storyline complete");
  });
});
