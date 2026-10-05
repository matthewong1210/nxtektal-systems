import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { correctionTarget, OutcomeForm } from "../components/planning/OutcomeSection";
import type { ConfirmationRecord, OutcomeRecord } from "../lib/planning";

const SUCCESS = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts", "planning-v1", "examples", "success.json"), "utf-8"),
) as { exchanges: { request: { schema_ref?: string }; response: { body: { data?: { record?: unknown } } } }[] };
const find = <T,>(definition: string): T =>
  SUCCESS.exchanges.find((e) => e.request.schema_ref === `#/$defs/${definition}`)!.response.body.data!.record as T;
const confirmation = { ...find<ConfirmationRecord>("ConfirmationRequest"), task_id: "task_example_001", schedule_status: "DISPATCHED" as const };
const o1 = find<OutcomeRecord>("OutcomeRequest");
const o2: OutcomeRecord = {
  ...o1,
  outcome_id: "outcome_example_002",
  recorded_at_utc: "2026-09-16T08:13:00Z",
  request: { ...o1.request, request_id: "demo-outcome-002", quantity_balls: 505, supersedes_outcome_id: o1.outcome_id },
};

const noop = async () => {};
const render = (target: string | null, latest: OutcomeRecord | undefined) =>
  renderToStaticMarkup(
    <OutcomeForm confirmation={confirmation} stage="COLLECTED" target={target} latest={latest} disabled={false} onRecord={noop} onRetarget={() => {}} onClose={() => {}} />,
  );
const saveButton = (html: string) => html.match(/<button[^>]*>Save COLLECTED result<\/button>/)?.[0] ?? "";

describe("correction target is frozen when the form opens", () => {
  it("reports no divergence while the record the form was opened on is still the latest", () => {
    expect(correctionTarget(o1.outcome_id, o1)).toEqual({ supersedes: o1.outcome_id, diverged: false, newer: null });
    expect(correctionTarget(null, undefined)).toEqual({ supersedes: null, diverged: false, newer: null });
  });

  it("flags a newer record that arrived after the form opened, without retargeting the draft", () => {
    expect(correctionTarget(o1.outcome_id, o2)).toEqual({ supersedes: o1.outcome_id, diverged: true, newer: o2 });
    expect(correctionTarget(null, o2)).toEqual({ supersedes: null, diverged: true, newer: o2 });
  });

  it("renders the frozen target and an enabled save while nothing newer exists", () => {
    const html = render(o1.outcome_id, o1);
    expect(html).toContain(`Corrects <span class="mono">${o1.outcome_id}</span>`);
    expect(saveButton(html)).not.toContain("disabled");
    expect(html).not.toContain("newer record");
  });

  it("blocks saving over an unreviewed newer record and offers an explicit retarget", () => {
    const html = render(o1.outcome_id, o2);
    expect(html).toContain("newer record");
    expect(html).toContain("outcome_example_002");
    expect(html).toContain("505");
    expect(html).toContain(`Corrects <span class="mono">${o1.outcome_id}</span>`); // frozen, not silently moved
    expect(saveButton(html)).toContain("disabled");
    expect(html).toMatch(/<button[^>]*>Review and correct the newer record<\/button>/);
  });

  it("blocks a first-record form when someone else recorded the stage meanwhile", () => {
    const html = render(null, o2);
    expect(html).toContain("newer record");
    expect(saveButton(html)).toContain("disabled");
  });
});
