import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import YcDispatchReportPage, {
  metadata,
} from "../app/yc-dispatch-report/page";
import {
  buildPresentationRoutePath,
  scannedRangeScene,
} from "../app/yc-dispatch-report/scanned-range-scene.config";
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

  expect(markup).not.toMatch(/<(?:canvas|nav|aside)\b/i);
  expect(markup).not.toMatch(
    /aria-label="[^"]*(?:chart|telemetry|live tracking)[^"]*"/i,
  );
  expect(text).not.toMatch(
    /No intervention required|Fully autonomous|Autonomous mission completed/i,
  );
  expect(text).not.toMatch(/\bautonom(?:ous|y)\b/i);
  expect(text).not.toMatch(
    /actual scan output|actual SLAM output|SLAM map|survey-grade map|surveyed site model|live digital twin|real-time robot tracking|autonomous navigation output|live robot position/i,
  );
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

  test("keeps the scan-style presentation geometry normalized and configuration-owned", () => {
    expect(Object.keys(scannedRangeScene).sort()).toEqual(
      [
        "animationDurationMs",
        "backgroundAlt",
        "backgroundImage",
        "facilityLabel",
        "mapLabel",
        "objectPosition",
        "returnStation",
        "robotStart",
        "routePoints",
        "sceneLabel",
        "teeLine",
        "zoneA",
      ].sort(),
    );
    expect(scannedRangeScene.backgroundImage).toBe(
      "/yc-site-schematic/range-grass-scan-demo.webp",
    );
    expect(scannedRangeScene.animationDurationMs).toBe(11_000);

    const points = [
      scannedRangeScene.objectPosition,
      scannedRangeScene.facilityLabel.position,
      scannedRangeScene.sceneLabel.position,
      scannedRangeScene.mapLabel.position,
      scannedRangeScene.teeLine.position,
      scannedRangeScene.zoneA.position,
      scannedRangeScene.returnStation.position,
      scannedRangeScene.robotStart.position,
      ...scannedRangeScene.routePoints,
    ];
    for (const point of points) {
      expect(Number.isFinite(point.x)).toBe(true);
      expect(Number.isFinite(point.y)).toBe(true);
      expect(point.x).toBeGreaterThanOrEqual(0);
      expect(point.x).toBeLessThanOrEqual(1);
      expect(point.y).toBeGreaterThanOrEqual(0);
      expect(point.y).toBeLessThanOrEqual(1);
    }

    expect(scannedRangeScene.routePoints[0]).toEqual(
      scannedRangeScene.robotStart.position,
    );
    expect(buildPresentationRoutePath()).toMatch(/^M 230 328\.5 C /);
    expect(buildPresentationRoutePath()).toBe(buildPresentationRoutePath());
  });

  test("ships only the sanitized WebP scene asset without embedded metadata chunks", () => {
    const asset = readFileSync(
      join(
        process.cwd(),
        "public",
        "yc-site-schematic",
        "range-grass-scan-demo.webp",
      ),
    );

    expect(asset.subarray(0, 4).toString("ascii")).toBe("RIFF");
    expect(asset.subarray(8, 12).toString("ascii")).toBe("WEBP");
    expect(asset.includes(Buffer.from("EXIF", "ascii"))).toBe(false);
    expect(asset.includes(Buffer.from("XMP ", "ascii"))).toBe(false);
    expect(asset.includes(Buffer.from("ICCP", "ascii"))).toBe(false);
  });

  test("provides a static robot marker when reduced motion is requested", () => {
    const css = readFileSync(
      join(
        process.cwd(),
        "app",
        "yc-dispatch-report",
        "ScannedRangeScene.module.css",
      ),
      "utf8",
    );

    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*\.animatedRobot\s*\{[\s\S]*display:\s*none[\s\S]*\.staticRobot\s*\{[\s\S]*display:\s*block/i,
    );
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
    expect(text).toContain("Robot Picker-01");
    expect(text).toContain("Task Collect range balls");
    expect(text).toContain("Zone Zone A");
    expect(text).toContain("Mission ID RGO-0828-01");
    expect(text).toContain("Tee line");
    expect(text).toContain("Zone A");
    expect(text).toContain("Return station");
    expect(text).toContain("Picker-01 start");
    expect(text).toContain("Site presentation schematic");
    expect(text).toContain("Scan-style range scene");
    expect(text).toContain("Presentation-only route animation");
    expect(markup).toContain('data-scene-kind="site-presentation-schematic"');
    expect(markup).toContain('data-animation-kind="presentation-only-route"');
    expect(markup).toContain('data-robot-marker="Picker-01"');
    expect(markup).toContain(
      'data-robot-marker="Picker-01-reduced-motion"',
    );
    expect(markup).toContain("range-grass-scan-demo.webp");
    expect(markup).toMatch(/<svg\b/i);
    expect(markup).toContain("<animateMotion");
    expect(markup).toContain('dur="11000ms"');
    expect(markup).toContain(`d="${buildPresentationRoutePath()}"`);
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
