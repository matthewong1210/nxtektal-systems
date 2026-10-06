import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ConsoleScreen } from "../components/ConsoleScreen";
import { createConsoleController, type ConsoleView } from "../lib/actions";
import { API_SCHEMA, createClient, DISCLAIMER, type FetchLike } from "../lib/api";
import { createConsoleActions, readConsole, type ConsoleData } from "../lib/console";
import { sampleConsoleData, sampleRecommendation, sampleSnapshotFor } from "./fixtures";

/** These tests drive the exact composition the page mounts — the typed
 * client over fetch, `readConsole`, the console controller, the page's
 * action bindings, and the rendered screen — with a scripted fetch whose
 * responses the test settles in any order. Only the React effect that
 * mounts the controller (`app/page.tsx`) is outside this harness. */

/** The page reads one coherent snapshot; the five projections arrive inside it. */
const READ_PATHS = ["/api/v0/supervisor-snapshot"];

type Pending = {
  url: string;
  method: string;
  body: unknown;
  settle: (response: Response) => void;
  fail: (cause: unknown) => void;
};

const envelope = (status: number, payload: unknown) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

function snapshot(patch: Partial<ConsoleData> = {}): ConsoleData {
  return sampleConsoleData(patch);
}

const accepted = () =>
  snapshot({
    recommendations: [
      sampleRecommendation({
        case_status: "accepted",
        response_kind: "accept",
        manager_response: {
          kind: "accept",
          operator_id: "mgr-01",
          reason_code: "staffing_available",
          note: null,
          responded_at: "2026-08-08T19:30:00.000000Z",
        },
      }),
    ],
  });

function scriptedService() {
  const pending: Pending[] = [];
  const fetchImpl: FetchLike = (url, init) =>
    new Promise<Response>((settle, fail) => {
      pending.push({
        url,
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
        settle,
        fail,
      });
    });
  const reads = () =>
    pending.filter((item) => item.method === "GET" && READ_PATHS.includes(item.url));
  const posts = () => pending.filter((item) => item.method === "POST");
  const take = (item: Pending) => pending.splice(pending.indexOf(item), 1)[0];
  const readGroup = (which: "first" | "last") => {
    const group = reads();
    expect(group.length).toBeGreaterThanOrEqual(READ_PATHS.length);
    const slice = which === "first" ? group.slice(0, READ_PATHS.length) : group.slice(-READ_PATHS.length);
    return slice.map(take);
  };
  return {
    pending,
    fetchImpl,
    reads,
    posts,
    async answerRead(data: ConsoleData, which: "first" | "last" = "first") {
      for (const item of readGroup(which)) {
        item.settle(envelope(200, { schema: API_SCHEMA, disclaimer: DISCLAIMER, data: sampleSnapshotFor(data) }));
      }
      await tick();
    },
    async failRead(message: string, which: "first" | "last" = "first") {
      for (const item of readGroup(which)) item.fail(new TypeError(message));
      await tick();
    },
    async answerPost(data: unknown) {
      const [post] = posts();
      expect(post).toBeDefined();
      take(post).settle(envelope(200, { schema: API_SCHEMA, disclaimer: DISCLAIMER, data }));
      await tick();
    },
    async failPost(message: string) {
      const [post] = posts();
      expect(post).toBeDefined();
      take(post).fail(new TypeError(message));
      await tick();
    },
    async refusePost(status: number, code: string, detail: string) {
      const [post] = posts();
      expect(post).toBeDefined();
      take(post).settle(envelope(status, { schema: API_SCHEMA, disclaimer: DISCLAIMER, error: { code, detail } }));
      await tick();
    },
  };
}

function mountConsole() {
  const service = scriptedService();
  const client = createClient(service.fetchImpl);
  const published: ConsoleView<ConsoleData>[] = [];
  const controller = createConsoleController(() => readConsole(client), (view) => published.push(view));
  const actions = createConsoleActions(client, () => controller);
  const view = () => published[published.length - 1];
  const html = () => renderToStaticMarkup(<ConsoleScreen view={view()} actions={actions} />);
  controller.start();
  return { service, controller, actions, published, view, html };
}

const accept = (actions: ReturnType<typeof createConsoleActions>) =>
  actions.respond(sampleRecommendation().recommendation_id, "accept", {
    operator_id: "mgr-01",
    reason_code: "staffing_available",
  });

const recId = sampleRecommendation().recommendation_id;
const operatorInput = (html: string) =>
  html.match(new RegExp(`<input[^>]*id="${recId}-operator"[^>]*>`))?.[0] ?? "";
const button = (html: string, label: string) =>
  html.match(new RegExp(`<button[^>]*>${label}</button>`))?.[0] ?? "";

describe("manager console consistency", () => {
  it("loads once, then renders the panels with changes enabled", async () => {
    const c = mountConsole();
    expect(c.html()).toContain("Loading the Site Agent projections");
    expect(c.service.reads()).toHaveLength(READ_PATHS.length);
    await c.service.answerRead(snapshot());
    const html = c.html();
    expect(html).toContain("Recommendations Awaiting Review");
    expect(html).toContain("PENDING");
    expect(operatorInput(html)).not.toContain("disabled");
    expect(button(html, "Modify…")).not.toContain("disabled");
    expect(button(html, "Advance one cycle")).not.toContain("disabled");
    expect(button(html, "Refresh")).not.toContain("disabled");
    expect(html).not.toContain("STALE");
    c.controller.stop();
  });

  it("never lets a late pre-decision read overwrite an accepted decision", async () => {
    const c = mountConsole();
    await c.service.answerRead(snapshot());
    void c.actions.refresh(); // manual refresh A: slow, still pending
    await tick();
    expect(c.service.reads()).toHaveLength(READ_PATHS.length);
    expect(button(c.html(), "Loading…")).toContain("disabled");
    const decision = accept(c.actions);
    await tick();
    expect(c.service.posts().map((item) => item.url)).toEqual([
      `/api/v0/recommendations/${recId}/accept`,
    ]);
    await c.service.answerPost(accepted().recommendations[0]);
    expect(c.service.reads()).toHaveLength(READ_PATHS.length * 2); // A + post-action B
    await c.service.answerRead(accepted(), "last"); // B lands first
    await decision;
    expect(c.html()).toContain("ACCEPTED");
    expect(c.html()).not.toContain("Operator ID");
    await c.service.answerRead(snapshot(), "first"); // A lands late with the pre-decision queue
    const html = c.html();
    expect(html).toContain("ACCEPTED");
    expect(html).not.toContain("Operator ID");
    expect(c.view()).toMatchObject({ loading: false, busy: false, error: null });
    expect(button(html, "Refresh")).not.toContain("disabled");
    c.controller.stop();
  });

  it("keeps a failed refresh visibly stale with every change disabled until a read succeeds", async () => {
    const c = mountConsole();
    await c.service.answerRead(snapshot());
    const refresh = c.actions.refresh();
    await c.service.failRead("fetch failed");
    await refresh;
    let html = c.html();
    expect(html).toContain("STALE");
    expect(html).toContain("fetch failed");
    expect(html).toContain("2,400"); // last good inventory stays visible
    expect(html).toMatch(/[Cc]hanges are disabled/);
    expect(operatorInput(html)).toContain("disabled");
    expect(button(html, "Modify…")).toContain("disabled");
    expect(button(html, "Advance one cycle")).toContain("disabled");
    expect(button(html, "Reset to a new evidence directory")).toContain("disabled");
    expect(button(html, "Refresh")).not.toContain("disabled");
    await expect(accept(c.actions)).rejects.toThrow(/refresh/);
    await expect(c.actions.advance()).rejects.toThrow(/refresh/);
    expect(c.service.posts()).toHaveLength(0);
    const recovery = c.actions.refresh();
    await c.service.answerRead(snapshot());
    await recovery;
    html = c.html();
    expect(html).not.toContain("STALE");
    expect(operatorInput(html)).not.toContain("disabled");
    expect(button(html, "Advance one cycle")).not.toContain("disabled");
    c.controller.stop();
  });

  it("sends one request for repeated clicks and keeps Refresh disabled until the post-action view is in place", async () => {
    const c = mountConsole();
    await c.service.answerRead(snapshot());
    const first = accept(c.actions);
    const second = accept(c.actions);
    const third = c.actions.advance();
    await tick();
    expect(c.service.posts()).toHaveLength(1);
    expect(button(c.html(), "Loading…") || button(c.html(), "Refresh")).toContain("disabled");
    expect(operatorInput(c.html())).toContain("disabled");
    await c.service.answerPost(accepted().recommendations[0]);
    expect(c.service.posts()).toHaveLength(0);
    expect(c.view().busy).toBe(true);
    await c.service.answerRead(accepted());
    await Promise.all([first, second, third]);
    expect(c.view().busy).toBe(false);
    expect(button(c.html(), "Refresh")).not.toContain("disabled");
    expect(c.html()).toContain("ACCEPTED");
    c.controller.stop();
  });

  it("keeps a saved decision saved when only its refresh fails: no retry, stale view, changes disabled", async () => {
    const c = mountConsole();
    await c.service.answerRead(snapshot());
    const decision = accept(c.actions);
    await tick();
    await c.service.answerPost(accepted().recommendations[0]);
    await c.service.failRead("connection reset");
    await expect(decision).resolves.toBeUndefined();
    expect(c.service.posts()).toHaveLength(0);
    const html = c.html();
    expect(html).toContain("STALE");
    expect(html).toContain("connection reset");
    expect(html).toContain("PENDING"); // last good view, honestly labelled stale
    expect(operatorInput(html)).toContain("disabled");
    expect(c.view()).toMatchObject({ busy: false, loading: false });
    c.controller.stop();
  });

  it("reports an uncertain decision outcome instead of a failure, then shows the refreshed truth", async () => {
    const c = mountConsole();
    await c.service.answerRead(snapshot());
    const decision = accept(c.actions);
    decision.catch(() => undefined); // asserted below, after the refresh settles
    await tick();
    await c.service.failPost("fetch failed");
    expect(c.html()).toContain("UNCERTAIN");
    expect(c.view().busy).toBe(true);
    await c.service.answerRead(accepted());
    await expect(decision).rejects.toThrow("fetch failed");
    const html = c.html();
    expect(html).toContain("UNCERTAIN");
    expect(html).toContain("ACCEPTED");
    expect(html).not.toContain("STALE");
    expect(c.service.posts()).toHaveLength(0);
    c.controller.stop();
  });

  it("treats a definite service refusal as a refusal, refreshes, and shows no uncertainty", async () => {
    const c = mountConsole();
    await c.service.answerRead(snapshot());
    const decision = accept(c.actions);
    decision.catch(() => undefined); // asserted below, after the refresh settles
    await tick();
    await c.service.refusePost(400, "invalid_request", "duplicate event_id");
    await c.service.answerRead(snapshot());
    await expect(decision).rejects.toThrow("invalid_request");
    expect(c.html()).not.toContain("UNCERTAIN");
    expect(c.view()).toMatchObject({ busy: false, error: null });
    c.controller.stop();
  });

  it("shows the unreachable screen on a failed first read and recovers through Retry", async () => {
    const c = mountConsole();
    await c.service.failRead("ECONNREFUSED");
    let html = c.html();
    expect(html).toContain("Service Unreachable");
    expect(html).toContain("ECONNREFUSED");
    expect(html).toContain("Retry");
    const retry = c.actions.refresh();
    await c.service.answerRead(snapshot());
    await retry;
    html = c.html();
    expect(html).not.toContain("Service Unreachable");
    expect(html).toContain("Recommendations Awaiting Review");
    c.controller.stop();
  });

  it("drops responses that arrive after unmount and publishes nothing more", async () => {
    const c = mountConsole();
    await c.service.answerRead(snapshot());
    const decision = accept(c.actions);
    await tick();
    const before = c.published.length;
    c.controller.stop();
    await c.service.answerPost(accepted().recommendations[0]);
    await decision.catch(() => undefined);
    if (c.service.reads().length) await c.service.answerRead(accepted(), "last");
    expect(c.published).toHaveLength(before);
    await expect(c.actions.refresh()).resolves.toBeUndefined();
    await expect(accept(c.actions)).rejects.toThrow(/not connected/);
  });
});
