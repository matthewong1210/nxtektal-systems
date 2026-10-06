/**
 * Behavior-parity regressions for the read-only execution panel: empty and
 * binding-only snapshots stay visible as last-known information, the
 * admitted-and-bound count is reported, a binding without a result says so,
 * and HTTP 503 collection_execution_unavailable is service-side
 * unavailability with the route connected.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CollectionExecutionView, type ExecutionReadView } from "../components/execution/CollectionExecutionPanel";
import { exampleSnapshot } from "./execution-fixtures";

describe("execution panel last-known states and binding counts", () => {
  const view = (snapshot: ExecutionReadView["data"], patch: Partial<ExecutionReadView> = {}): ExecutionReadView => ({
    data: snapshot, loading: false, error: null, unavailable: false, serviceUnavailable: false, invalid: false, lastReadAtMs: 1_000, nowMs: 6_000, ...patch,
  });
  const render = (v: ExecutionReadView) => renderToStaticMarkup(<CollectionExecutionView view={v} onRetry={() => {}} />);

  it("keeps an empty snapshot visible as last-known information when a later read fails", () => {
    const empty = { ...exampleSnapshot("success"), bindings: [], requests: [], receipts: [], executions: [] };
    const html = render(view(empty, { error: "fetch failed" }));
    expect(html).toContain("READ STALE");
    expect(html).toContain("No execution record or binding exists in this session yet.");
    expect(html).toContain("last-known state of the session");
    expect(html).toContain("0 admitted and bound tasks");
  });

  it("reports the admitted and bound task count and says when no execution result exists", () => {
    const withResult = render(view(exampleSnapshot("success")));
    expect(withResult).toContain("1 admitted and bound task · 1 with an execution record · 0 bound without any execution result");
    const bindingOnly = render(view(exampleSnapshot("identity-conflict")));
    expect(bindingOnly).toContain("No execution result has been recorded for this task");
    expect(bindingOnly).toMatch(/\d+ bound without any execution result/);
    expect(bindingOnly).not.toContain("SUCCEEDED");
  });

  it("explains HTTP 503 collection_execution_unavailable as service-side unavailability with the route connected", () => {
    const none = render(view(null, { serviceUnavailable: true, error: "collection_execution_unavailable: store unavailable", lastReadAtMs: null }));
    expect(none).toContain("UNAVAILABLE");
    expect(none).toContain("route is connected");
    expect(none).toContain("No execution evidence is available");
    expect(none).not.toContain("not connected");
    const retained = render(view(exampleSnapshot("success"), { serviceUnavailable: true, error: "collection_execution_unavailable: store unavailable" }));
    expect(retained).toContain("remains below marked stale");
    expect(retained).toContain("READ STALE");
    expect(retained).toContain("SUCCEEDED");
  });
});
