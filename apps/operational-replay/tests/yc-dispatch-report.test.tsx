import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import YcDispatchReportPage, {
  metadata,
} from "../app/yc-dispatch-report/page";
import { ycDemoMission } from "../app/yc-dispatch-report/yc-dispatch-report.config";
import { parseYcDemoQuery } from "../app/yc-dispatch-report/yc-dispatch-report.query";

type SearchParams = Record<string, string | string[] | undefined>;

function visibleText(markup: string): string {
  return markup
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replaceAll("&amp;", "&")
    .replaceAll("&quot;", '"')
    .replaceAll("&#x27;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replace(/\s+/g, " ")
    .trim();
}

async function renderPage(searchParams: SearchParams = {}): Promise<string> {
  const page = await YcDispatchReportPage({
    searchParams: Promise.resolve(searchParams),
  });
  return renderToStaticMarkup(page);
}

function expectPresentationOnly(markup: string): void {
  const text = visibleText(markup);

  expect(markup).not.toMatch(/<(?:canvas|svg|nav|aside)\b/i);
  expect(markup).not.toMatch(
    /aria-label="[^"]*(?:map|route|chart|telemetry|robot animation)[^"]*"/i,
  );
  expect(text).not.toMatch(
    /No intervention required|Fully autonomous|Autonomous mission completed/i,
  );
  expect(text).not.toMatch(/\bautonom(?:ous|y)\b/i);
  expect(text).not.toMatch(/\b(?:loading|planning|countdown|ready to dispatch)\b/i);
  expect(text).not.toMatch(
    /Update after field run|Placeholder|\bTBD\b|Replace this value|Mock value/i,
  );
  expect(text).toContain(
    "Prototype orchestration demo · supervised hardware execution",
  );
}

describe("YC Dispatch / Report configuration and query contract", () => {
  test("keeps every field-run value in one neutral filming configuration", () => {
    expect(Object.keys(ycDemoMission).sort()).toEqual(
      [
        "autoplayDelayMs",
        "ballsCollected",
        "collectionPasses",
        "completionPercentage",
        "executionMode",
        "facilityName",
        "missionId",
        "robotName",
        "runtime",
        "taskName",
        "zoneName",
      ].sort(),
    );
    expect(ycDemoMission).toMatchObject({
      missionId: "RGO-0828-01",
      robotName: "Picker-01",
      taskName: "Collect range balls",
      zoneName: "Zone A",
      runtime: "—",
      ballsCollected: "—",
      collectionPasses: "—",
      completionPercentage: 100,
      executionMode: "Supervised prototype",
      autoplayDelayMs: 12_000,
    });
    expect(ycDemoMission.facilityName).toBeUndefined();
  });

  test("defaults safely and accepts only the documented state and autoplay flag", () => {
    expect(parseYcDemoQuery({})).toEqual({
      initialState: "dispatch",
      autoplay: false,
      autoplayDelayMs: ycDemoMission.autoplayDelayMs,
    });
    expect(parseYcDemoQuery({ state: "dispatch" }).initialState).toBe(
      "dispatch",
    );
    expect(parseYcDemoQuery({ state: "report" }).initialState).toBe("report");
    expect(parseYcDemoQuery({ state: "unknown" }).initialState).toBe(
      "dispatch",
    );
    expect(parseYcDemoQuery({ autoplay: "1" }).autoplay).toBe(true);
    expect(parseYcDemoQuery({ autoplay: "true" }).autoplay).toBe(false);
  });

  test("uses a valid supplied delay and fails invalid values back to configuration", () => {
    expect(parseYcDemoQuery({ delay: "12000" }).autoplayDelayMs).toBe(12_000);
    expect(parseYcDemoQuery({ delay: "0" }).autoplayDelayMs).toBe(0);
    expect(parseYcDemoQuery({ delay: "600001" }).autoplayDelayMs).toBe(
      600_000,
    );

    for (const delay of ["-1", "1.5", "NaN", "9007199254740992"]) {
      expect(parseYcDemoQuery({ delay }).autoplayDelayMs).toBe(
        ycDemoMission.autoplayDelayMs,
      );
    }

    expect(
      parseYcDemoQuery({
        state: ["report", "dispatch"],
        autoplay: ["1", "0"],
        delay: ["250", "12000"],
      }),
    ).toEqual({
      initialState: "report",
      autoplay: true,
      autoplayDelayMs: 250,
    });
  });
});

describe("YC Dispatch / Report server presentation", () => {
  test("does not inherit the unrelated Operational Replay social story", () => {
    expect(metadata).toMatchObject({
      title: "YC Dispatch / Report | NXTektal Systems",
      description: expect.stringContaining("presentation-only"),
      openGraph: {
        title: "YC Dispatch / Report | NXTektal Systems",
        images: [],
      },
      twitter: {
        card: "summary",
        title: "YC Dispatch / Report | NXTektal Systems",
        images: [],
      },
    });
  });

  test("renders Dispatch directly by default with no intermediate product state", async () => {
    const markup = await renderPage();
    const text = visibleText(markup);

    expect(markup).toContain('data-active-state="dispatch"');
    expect(markup).toContain('data-demo-state="dispatch"');
    expect(markup).not.toContain('data-demo-state="report"');
    expect(markup.match(/data-demo-state=/g)).toHaveLength(1);
    expect(text).toContain("NXTektal Systems RangeOps Agent");
    expect(text).toContain("Mission Dispatched");
    expect(text).toContain("Mission assigned to Picker-01");
    expect(text).toContain("Robot Picker-01");
    expect(text).toContain("Task Collect range balls");
    expect(text).toContain("Zone Zone A");
    expect(text).toContain("Mission ID RGO-0828-01");
    expect(text).not.toContain("Mission Complete");
    expectPresentationOnly(markup);
  });

  test("renders Report directly from the URL without a Dispatch markup flash", async () => {
    const markup = await renderPage({ state: "report" });
    const text = visibleText(markup);

    expect(markup).toContain('data-active-state="report"');
    expect(markup).toContain('data-demo-state="report"');
    expect(markup).not.toContain('data-demo-state="dispatch"');
    expect(markup.match(/data-demo-state=/g)).toHaveLength(1);
    expect(text).toContain("Mission Complete");
    expect(text).toContain("Picker-01 / Zone A / RGO-0828-01");
    expect(text).toContain("Runtime —");
    expect(text).toContain("Balls collected —");
    expect(text).toContain("Collection passes —");
    expect(text).toMatch(/Completion 100\s*%/);
    expect(text).toContain("Mission execution recorded");
    expect(text).toContain("Mission report generated");
    expect(text).toContain("Execution mode Supervised prototype");
    expect(text).toContain(
      "Prototype orchestration demo · supervised hardware execution",
    );
    expect(text).not.toContain("Report saved to facility operations log");
    expect(text).not.toContain("Scripted presentation copy");
    expect(text).not.toContain("Fully autonomous");
    expect(text).not.toContain("No intervention required");
    expect(text).not.toContain("Autonomous mission completed");
    expect(text).not.toContain("Mission Dispatched");
    expectPresentationOnly(markup);
  });
});
