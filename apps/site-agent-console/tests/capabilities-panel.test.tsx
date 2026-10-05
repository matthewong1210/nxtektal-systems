/** Static renders of the capability gating over the validated declaration. */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { capabilityBlocker, serviceModeText } from "../components/capabilities";
import { DispatchView } from "../components/DispatchPanel";
import { INITIAL_TASK_OPS_VIEW, parseTaskOps, taskOpsCapabilities, type EffectiveTaskOpsCapabilities, type TaskOpsSnapshot } from "../lib/task-ops";
import { taskOpsFixture } from "./task-ops-fixtures";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const EXAMPLES = join(import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts", "pilot-dispatch-v0", "service-capabilities", "examples");
const declaration = (name: string) => JSON.parse(readFileSync(join(EXAMPLES, `${name}.json`), "utf8")) as TaskOpsSnapshot["service_capabilities"];
const fixed = () => parseTaskOps({ ...taskOpsFixture(), service_capabilities: declaration("fixed-v3-execution") });
const legacy = () => parseTaskOps({ ...taskOpsFixture(), service_capabilities: declaration("legacy-pilot-dispatch") });
const undeclared = () => {
  const historical = taskOpsFixture() as TaskOpsSnapshot & Record<string, unknown>;
  delete historical.service_capabilities;
  return parseTaskOps(historical);
};
const actions = { schedule: async () => {}, cancel: async () => {}, respond: async () => {}, refresh: async () => {} };
const render = (data: TaskOpsSnapshot, capabilities: EffectiveTaskOpsCapabilities | null | undefined) =>
  renderToStaticMarkup(<DispatchView view={{ ...INITIAL_TASK_OPS_VIEW, data, loading: false }} actions={actions} capabilities={capabilities} />);

describe("capability reasons come only from the validated declaration", () => {
  it("explains each mode without inferring support", () => {
    const f = taskOpsCapabilities(fixed());
    expect(capabilityBlocker(f, "schedules_create")).toContain("preset single-task demonstration (FIXED_V3_EXECUTION)");
    expect(capabilityBlocker(f, "planning_outcomes_create")).toBeNull();
    expect(capabilityBlocker(f, "notifications_resolve")).toBeNull();
    const l = taskOpsCapabilities(legacy());
    expect(capabilityBlocker(l, "schedules_create")).toBeNull();
    const u = taskOpsCapabilities(undeclared());
    expect(capabilityBlocker(u, "planning_outcomes_create")).toContain("declares no write capabilities (UNDECLARED)");
    expect(capabilityBlocker(null, "notifications_acknowledge")).toContain("write capabilities are unknown");
    expect(capabilityBlocker(undefined, "schedules_create")).toBeNull(); // standalone render: no gating wired
    expect(serviceModeText(f)).toContain("preset single-task demonstration");
    expect(serviceModeText(u)).toContain("never inferred from HTTP success, the transport or other fields");
  });
});

describe("task panel under each declared mode", () => {
  it("fixed V3: schedules and cancellations are withheld with the reason, notifications keep their own preconditions", () => {
    const data = fixed();
    const html = render(data, taskOpsCapabilities(data));
    expect(html).toContain("PRESET SINGLE-TASK DEMO");
    expect(html).toContain("preset single-task demonstration");
    expect(html).toMatch(/id="dispatch-robot"[^>]*disabled/);
    expect(html).toContain("Direct schedule creation is not installed on this service");
    expect(html).not.toContain("Cancel schedule");
    expect(html).toContain("Schedule cancellation is not installed on this service");
    expect(html).toContain("Acknowledge"); // notifications_acknowledge is SUPPORTED; readiness still needs operator and note
    expect(html).toContain("Resolving this notification never unlocks or restarts a robot");
  });

  it("legacy: the legacy write routes stay installed", () => {
    const data = legacy();
    const html = render(data, taskOpsCapabilities(data));
    expect(html).toContain("LEGACY PILOT DISPATCH");
    expect(html).not.toMatch(/id="dispatch-robot"[^>]*disabled/);
    expect(html).toContain("Cancel schedule");
    expect(html).not.toContain("not installed on this service");
  });

  it("undeclared: records stay readable and every write is withheld", () => {
    const data = undeclared();
    const html = render(data, taskOpsCapabilities(data));
    expect(html).toContain("WRITES UNDECLARED");
    expect(html).toContain("schedule-1"); // readable
    expect(html).toContain("BLOCKED_AWAITING_HUMAN");
    expect(html).toMatch(/id="dispatch-robot"[^>]*disabled/);
    expect(html).not.toContain("Cancel schedule");
    expect(html).toContain("declares no write capabilities (UNDECLARED)");
  });

  it("unknown (no successful read yet) withholds writes; standalone renders without a wired reading keep the old behaviour", () => {
    const data = legacy();
    expect(render(data, null)).toContain("CAPABILITIES UNKNOWN");
    expect(render(data, null)).toMatch(/id="dispatch-robot"[^>]*disabled/);
    const standalone = render(data, undefined);
    expect(standalone).not.toContain("CAPABILITIES UNKNOWN");
    expect(standalone).not.toMatch(/id="dispatch-robot"[^>]*disabled/);
  });
});
