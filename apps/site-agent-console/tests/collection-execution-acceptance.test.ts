import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  collectionExecutionSimulationNow,
  parseCollectionExecutions,
} from "../lib/collection-executions";
import {
  createSchedulerHealthTracker,
  SCHEDULER_HEALTH_EXPIRY_MS,
  schedulerAllowsWrites,
} from "../lib/scheduler-health";
import { buildSchedule, parseTaskOps } from "../lib/task-ops";
import { taskOpsFixture } from "./task-ops-fixtures";

const originalTz = process.env.TZ;
afterEach(() => {
  if (originalTz === undefined) delete process.env.TZ;
  else process.env.TZ = originalTz;
});

function executionFixture() {
  const path = join(
    import.meta.dirname,
    "../../../simulation/tests/course_monitoring/fixtures/collection-execution-normal-loop-v3.json",
  );
  return JSON.parse(readFileSync(path, "utf8"));
}

describe("V3 collection execution cross-language acceptance", () => {
  it("01 parses the runtime-compatible successful execution without changing causal evidence", () => {
    const input = executionFixture();
    const parsed = parseCollectionExecutions(input);

    expect(parsed).toEqual(input);
    expect(parsed.executions[0]).toMatchObject({
      state: "SUCCEEDED",
      raw_quantity: { balls: 600, milestone: "RAW_COLLECTED_TO_ROBOT" },
      unload_quantity: { balls: 600, milestone: "UNLOADED_TO_STATION" },
    });
    expect(parsed.executions[0].actions.map((action) => action.selection)).toEqual([
      "WAIT_SLOT",
      "ORIGINAL_POLICY_CONVERGED",
    ]);
  });

  it("17 expires wall-clock health independently while preserving PAUSED simulation time", () => {
    const executionInput = executionFixture();
    executionInput.session_state = "PAUSED";
    const execution = parseCollectionExecutions(executionInput);
    const data = parseTaskOps(taskOpsFixture({ server_time_utc: "2035-01-02T03:04:05Z" }));
    let wallNow = Date.parse(data.server_time_utc);
    const tracker = createSchedulerHealthTracker({ now: () => wallNow });

    const fresh = tracker.observe({
      data, loading: false, busy: false, error: null, unavailable: false,
    });
    expect(schedulerAllowsWrites(fresh, wallNow)).toBe(true);
    wallNow += SCHEDULER_HEALTH_EXPIRY_MS + 1;
    expect(tracker.current().status).toBe("expired");
    expect(schedulerAllowsWrites(tracker.current(), wallNow)).toBe(false);
    const unavailable = tracker.observe({
      data: null, loading: false, busy: false, error: "offline", unavailable: true,
    });
    expect(unavailable.status).toBe("unavailable");
    expect(execution.session_state).toBe("PAUSED");
    expect(execution.simulation_time_utc).toBe("2026-09-16T08:30:00Z");
    expect(collectionExecutionSimulationNow(execution)).toBe(
      Date.parse("2026-09-16T08:30:00Z"),
    );
  });

  it("18 builds a 2026 schedule from simulation time even when the wall clock is 2035", () => {
    process.env.TZ = "UTC";
    const fields = {
      robot: "picker-01",
      zone: "Z1",
      localTime: "2026-09-16T08:31",
      validityMinutes: "1",
      operator: " acceptance ",
    };
    const execution = parseCollectionExecutions(executionFixture());
    const simulationNow = collectionExecutionSimulationNow(execution);

    expect(buildSchedule(fields, simulationNow)).toEqual({
      robot_id: "picker-01",
      zone_id: "Z1",
      due_at_utc: "2026-09-16T08:31:00.000Z",
      expires_at_utc: "2026-09-16T08:32:00.000Z",
      operator: "acceptance",
    });
    expect(() => buildSchedule(fields, Date.parse("2035-01-02T03:04:05Z"))).toThrow(
      "Choose a start time in the future.",
    );
  });
});
