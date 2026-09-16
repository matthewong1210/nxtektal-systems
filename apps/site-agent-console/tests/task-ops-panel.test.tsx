import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DispatchView } from "../components/DispatchPanel";
import { INITIAL_TASK_OPS_VIEW, type TaskOpsView } from "../lib/task-ops";
import { taskOpsFixture } from "./task-ops-fixtures";
const actions = { schedule: async () => {}, cancel: async () => {}, respond: async () => {}, refresh: async () => {} };
const view = (patch: Partial<TaskOpsView> = {}): TaskOpsView => ({ ...INITIAL_TASK_OPS_VIEW, data: taskOpsFixture(), loading: false, ...patch });
const render = (input: TaskOpsView) => renderToStaticMarkup(<DispatchView view={input} actions={actions} />);

describe("pilot task panel", () => {
  it("shows honest simulated task evidence and local notification boundaries", () => {
    const html = render(view());
    for (const text of ["SIMULATION", "No physical robot or CE82A is connected", "Local notification inbox", "Email, text messages and remote alerts are not connected", "BLOCKED_AWAITING_HUMAN", "progress_window_elapsed", "Not reported", "Cancel schedule", "browser local time", "Task validity after start"]) expect(html).toContain(text);
    expect(html).not.toContain("undefined"); expect(html).not.toContain(">null<");
  });
  it("uses current receipt freshness rather than the journal's old ONLINE state", () => {
    const html = render(view());
    expect(html).toContain(">OFFLINE<"); expect(html).not.toContain(">ONLINE<");
    expect(html).toContain("90s");
  });
  it("offers cancellation only while a schedule is pending", () => {
    const sample = taskOpsFixture();
    const html = render(view({ data: { ...sample, schedules: [{ ...sample.schedules[0], status: "DISPATCHED", task_id: "task-1" }] } }));
    expect(html).not.toContain("Cancel schedule");
    expect(html).toContain("DISPATCHED");
  });
  it("does not offer recovery when resolving a notification", () => {
    const html = render(view());
    expect(html).toContain("condition is still active");
    expect(html).toContain("never unlocks or restarts a robot");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Resolve notification<\/button>/);
  });
  it("keeps a stale snapshot visible and all mutations disabled", () => {
    const html = render(view({ error: "Network unavailable" }));
    expect(html).toContain("STALE"); expect(html).toContain("schedule-1");
    expect(html).toMatch(/<select[^>]*disabled=""/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Acknowledge<\/button>/);
    expect(html).toContain("Changes are disabled");
  });
  it("explains an old standalone demo without hiding the fixture console", () => {
    const html = render({ ...INITIAL_TASK_OPS_VIEW, loading: false, unavailable: true, error: "404" });
    expect(html).toContain("fixture console remains available below");
    expect(html).not.toContain("Schedule simulated collection");
  });
  it("labels no evidence without fabricating a task or zero result", () => {
    const html = render(view({ data: taskOpsFixture({ tasks: {}, schedules: [], notifications: [] }) }));
    expect(html).toContain("No task evidence yet");
    expect(html).toContain("No notifications recorded");
    expect(html).toContain("No schedules yet");
  });
});
