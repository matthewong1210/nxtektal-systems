import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import {
  ReplayStory,
  deriveStory,
  parseJsonLines,
  type JsonObject,
  type SourcedRecord,
} from "../app/ReplayStory";

function records(name: string, values: JsonObject[]): SourcedRecord[] {
  return parseJsonLines(name, values.map((value) => JSON.stringify(value)).join("\n"))
    .records;
}

function eventsOnlyStory() {
  return deriveStory(
    new Map([
      [
        "events.jsonl",
        records("events.jsonl", [
          { t_s: 21600, kind: "robot_failed", payload: { robot_id: "R3" } },
        ]),
      ],
    ]),
    undefined,
    ["events.jsonl"],
    ["Optional capture artifacts are missing."],
  );
}

function manyAdvisoriesStory() {
  return deriveStory(
    new Map([
      [
        "events.jsonl",
        records("events.jsonl", [
          { t_s: 21600, kind: "robot_failed", payload: { robot_id: "R3" } },
        ]),
      ],
      [
        "briefings.jsonl",
        records("briefings.jsonl", [
          {
            t_s: 21600,
            recommendations: Array.from({ length: 101 }, (_, index) => ({
              rule_id: `rule_${index}`,
              action: `Action ${index}`,
              affected_resources: [`robot:R${index}`],
            })),
          },
        ]),
      ],
    ]),
    undefined,
    ["events.jsonl", "briefings.jsonl"],
  );
}

describe("recovered replay shell", () => {
  test("renders the read-only simulation-reference experience", () => {
    const html = renderToStaticMarkup(<ReplayStory />);

    expect(html).toContain("Operational replay");
    expect(html).toContain("Story mode");
    expect(html).toContain("Simulation results");
    expect(html).toContain("Presentation layer only");
    expect(html).toContain("Reference source");
    expect(html).toContain("Read only");
    expect(html).not.toContain("live facility performance");
  });

  test("renders recommendation, task, and outcome gaps without positive claims", () => {
    const story = eventsOnlyStory();
    const recommendation = renderToStaticMarkup(
      <ReplayStory initialStory={story} initialStageIndex={2} />,
    );
    const task = renderToStaticMarkup(
      <ReplayStory initialStory={story} initialStageIndex={3} />,
    );
    const outcome = renderToStaticMarkup(
      <ReplayStory initialStory={story} initialStageIndex={4} />,
    );

    expect(recommendation).toContain("Unconfirmed");
    expect(recommendation).not.toContain("Recommended action");
    expect(recommendation).not.toContain("Risk detected");
    expect(recommendation).not.toContain("Action recommended");
    expect(task).toContain('aria-label="Task evidence gap"');
    expect(task).not.toContain("related_event_recorded");
    expect(task).not.toContain('class="done"');
    expect(outcome).toContain("cannot state a terminal outcome");
    expect(outcome).not.toContain("Continuity is visible");
  });

  test("labels reference-layout fallback and omits fabricated robot markers", () => {
    const html = renderToStaticMarkup(
      <ReplayStory initialStory={eventsOnlyStory()} initialStageIndex={0} />,
    );

    expect(html).toContain("Reference layout context · selected layout missing");
    expect(html).not.toContain('aria-label="R1 recorded"');
    expect(html).not.toContain('aria-label="R2 recorded"');
    expect(html).not.toContain('aria-label="R3 failed"');
  });

  test("discloses a bounded evidence view without hiding the total", () => {
    const html = renderToStaticMarkup(
      <ReplayStory
        initialStory={manyAdvisoriesStory()}
        initialStageIndex={1}
        initialEvidenceOpen
      />,
    );

    expect(html).toContain("Showing the first 100 of 101 records");
    expect(html.match(/class="evidence-record"/g)).toHaveLength(100);
    expect(html).toContain("101 records");
  });
});
