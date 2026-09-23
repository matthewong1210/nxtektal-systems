import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ManagerApiError } from "../lib/api";
import { COLLECTION_EXECUTIONS_SCHEMA, createCollectionExecutionsClient, parseCollectionExecutions,
  type CollectionExecutionsSnapshot } from "../lib/collection-executions";

const examples = ["success", "policy-missed", "partial-preempted", "safety-rejected", "restart-unknown",
  "identity-conflict", "duplicate-request", "terminal-conflict"];
function envelope(name = "success") {
  return JSON.parse(readFileSync(join(import.meta.dirname,
    "../../../simulation/docs/contracts/collection-execution-v1/examples", `${name}.json`), "utf8")).snapshot.body;
}
function snapshot(name = "success"): CollectionExecutionsSnapshot { return envelope(name).data; }
function mutate(value: unknown, path: string, replacement: unknown) {
  const keys = path.split(".");
  let target = value as Record<string, unknown>;
  for (const key of keys.slice(0, -1)) target = target[key] as Record<string, unknown>;
  target[keys.at(-1)!] = replacement;
}
function pending(): CollectionExecutionsSnapshot {
  const s = snapshot("policy-missed"), r = s.executions[0];
  s.now_sim_t_s = 120; s.simulation_time_utc = "2026-09-16T00:02:00Z";
  r.state = "PENDING"; r.stage = "WAITING_FOR_POLICY_SLOT"; r.reason = null; r.terminal_sim_t_s = null;
  r.actions = []; r.edge_evidence.effective_state = "ACCEPTED"; r.edge_evidence.reason = null;
  r.edge_evidence.terminal_states = [];
  return s;
}
function running(): CollectionExecutionsSnapshot {
  const s = snapshot(), r = s.executions[0];
  r.state = "RUNNING"; r.stage = "COLLECTING"; r.reason = null; r.terminal_sim_t_s = null;
  r.success_display_allowed = false; r.actions = r.actions.slice(0, 1);
  r.edge_evidence.effective_state = "RUNNING"; r.edge_evidence.terminal_states = [];
  r.runtime_evidence.assignment_terminal = false; r.runtime_evidence.collection_exit_reason = null;
  r.raw_quantity = pending().executions[0].raw_quantity;
  r.unload_quantity = pending().executions[0].unload_quantity;
  r.raw_quantity.assignment_id = r.assignment_id; r.unload_quantity.assignment_id = r.assignment_id;
  return s;
}
function addSecond(s: CollectionExecutionsSnapshot, start?: number) {
  const b = structuredClone(s.bindings[0]), q = structuredClone(s.requests[0]);
  const receipt = structuredClone(s.receipts[0]), r = structuredClone(s.executions[0]);
  b.binding_id = "b".repeat(64); b.task_id = `task_${"b".repeat(24)}`;
  q.binding_id = b.binding_id; q.task_id = b.task_id; q.request_id = "request-002"; q.execution_id = "f".repeat(64);
  receipt.binding_id = b.binding_id; receipt.request_id = q.request_id; receipt.execution_id = q.execution_id;
  receipt.attempt_id = "attempt-002"; receipt.sequence = 2;
  Object.assign(r, { binding_id: b.binding_id, task_id: b.task_id, request_id: q.request_id,
    execution_id: q.execution_id, attempt_id: receipt.attempt_id });
  r.edge_evidence.task_id = b.task_id;
  if (r.assignment_id !== null) {
    r.assignment_id = "assignment-002";
    r.raw_quantity.assignment_id = r.assignment_id; r.unload_quantity.assignment_id = r.assignment_id;
  }
  if (start !== undefined) {
    q.eligible_sim_t_s = start; q.latest_start_sim_t_s = start + 180;
    q.due_at_utc = new Date(Date.parse(s.session_epoch_utc) + start * 1000).toISOString();
    q.expires_at_utc = new Date(Date.parse(s.session_epoch_utc) + (start + 180) * 1000).toISOString();
    r.eligible_sim_t_s = start; r.latest_start_sim_t_s = start + 180;
    r.started_sim_t_s = start; r.execution_deadline_sim_t_s = start + 660; r.terminal_sim_t_s = start + 540;
    r.actions[0].sim_t_s = start; r.actions[1].sim_t_s = start + 360;
  }
  for (const a of r.actions) a.eligible_pending = a.selection === "WAIT_SLOT"
    ? [{ execution_id: r.execution_id, eligible_sim_t_s: r.eligible_sim_t_s, latest_start_sim_t_s: r.latest_start_sim_t_s }] : [];
  s.bindings.push(b); s.requests.push(q); s.receipts.push(receipt); s.executions.push(r);
  return s;
}
function protectedExit(reason: "ROBOT_FAULT" | "ESTOP_LATCHED" | "HUMAN_ASSISTANCE_REQUIRED") {
  const s = snapshot("partial-preempted"), r = s.executions[0];
  r.actions.pop(); r.reason = reason; r.runtime_evidence.collection_exit_reason = reason;
  r.device_protection = {protected: true, authorization_blocked: true, reasons: [reason]};
  return s;
}
function notStartedAfterRestart() {
  const s = snapshot("policy-missed"), r = s.executions[0];
  r.state = "FAILED"; r.reason = "NOT_STARTED_AFTER_RESTART";
  r.edge_evidence.reason = "not_started_after_restart";
  return s;
}

describe("collection execution v1 read contract", () => {
  it.each(examples)("preserves the frozen %s snapshot exactly", (name) => {
    const input = snapshot(name), before = structuredClone(input);
    expect(parseCollectionExecutions(input)).toEqual(before);
    expect(input).toEqual(before);
    expect(parseCollectionExecutions(input).schema).toBe(COLLECTION_EXECUTIONS_SCHEMA);
  });
  it("keeps raw, unload, unknown evidence and conflict separate", () => {
    const partial = parseCollectionExecutions(snapshot("partial-preempted")).executions[0];
    expect(partial.raw_quantity.balls).toBe(12); expect(partial.unload_quantity.balls).toBeNull();
    expect(partial.edge_evidence.effective_state).toBe("FAILED");
    const conflict = parseCollectionExecutions(snapshot("terminal-conflict")).executions[0];
    expect(conflict.state).toBe("INCONCLUSIVE"); expect(conflict.success_display_allowed).toBe(false);
    expect(conflict.raw_quantity.balls).toBe(44);
  });
  it("accepts multiple pending requests and one nonterminal running lease", () => {
    expect(parseCollectionExecutions(addSecond(pending())).executions).toHaveLength(2);
    expect(parseCollectionExecutions(running()).executions[0].terminal_sim_t_s).toBeNull();
  });
  it("accepts an exact terminal-to-next-start boundary", () => {
    const s = addSecond(snapshot(), 660);
    s.now_sim_t_s = 1200; s.simulation_time_utc = "2026-09-16T00:20:00Z";
    expect(parseCollectionExecutions(s).executions).toHaveLength(2);
  });
  it.each([120, 300, 659])("rejects completed overlapping leases starting at %s", (start) => {
    const s = addSecond(snapshot(), start);
    s.now_sim_t_s = 1200; s.simulation_time_utc = "2026-09-16T00:20:00Z";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects two current running leases", () => expect(() => parseCollectionExecutions(addSecond(running()))).toThrow(ManagerApiError));
  it("keeps wall read time independent of paused simulation time", () => {
    const s = snapshot(); s.session_state = "PAUSED"; s.server_time_utc = "2029-02-01T12:00:00Z";
    expect(parseCollectionExecutions(s).now_sim_t_s).toBe(660);
  });
  it("preserves microsecond UTC precision and rejects clock drift below one millisecond", () => {
    const s = snapshot("identity-conflict"); s.now_sim_t_s = 660.000001;
    s.simulation_time_utc = "2026-09-16T00:11:00.000001Z";
    expect(parseCollectionExecutions(s).now_sim_t_s).toBe(660.000001);
    s.simulation_time_utc = "2026-09-16T00:11:00.000002Z";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects success beyond the execution deadline even when within the snapshot horizon", () => {
    const s = snapshot(); s.now_sim_t_s = 900; s.simulation_time_utc = "2026-09-16T00:15:00Z";
    s.executions[0].terminal_sim_t_s = 781;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects a start whose deadline exceeds the session horizon", () => {
    const s = snapshot(); s.session_end_sim_t_s = 700; s.bindings[0].session_end_sim_t_s = 700;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects a binding after the recorded accepted start", () => {
    const s = snapshot(); s.bindings[0].bound_at_sim_t_s = 180; s.bindings[0].bound_at_utc = "2026-09-16T00:03:00Z";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("checks cycle freshness at inclusive binding time", () => {
    const s = snapshot(); s.bindings[0].cycle_evidence.observed_at_utc = s.bindings[0].bound_at_utc;
    s.bindings[0].cycle_evidence.valid_until_utc = s.bindings[0].bound_at_utc;
    expect(parseCollectionExecutions(s).bindings[0].max_execution_s).toBe(660);
  });
  it("rejects a pending start during another execution's held lease", () => {
    const s = running(), other = addSecond(snapshot("safety-rejected"));
    s.bindings.push(other.bindings[1]); s.requests.push(other.requests[1]);
    s.receipts.push(other.receipts[1]); s.executions.push(other.executions[1]);
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["receipts.execution_id", "receipts.attempt_id", "executions.attempt_id", "executions.request_id"])("rejects duplicate %s independently", (path) => {
    const s = addSecond(pending()); const [collection, field] = path.split(".");
    const entries = (s as unknown as Record<string, Record<string, unknown>[]>)[collection];
    entries[1][field] = entries[0][field];
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects another request identity for the same task/session/binding", () => {
    const s = addSecond(pending());
    Object.assign(s.requests[1], {binding_id: s.requests[0].binding_id, task_id: s.requests[0].task_id});
    Object.assign(s.receipts[1], {binding_id: s.requests[0].binding_id});
    Object.assign(s.executions[1], {binding_id: s.requests[0].binding_id, task_id: s.requests[0].task_id});
    s.executions[1].edge_evidence.task_id = s.requests[0].task_id;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects duplicate start evidence and duplicate eligible candidates", () => {
    for (const key of ["actions", "candidates"]) {
      const s = snapshot(), actions = s.executions[0].actions;
      if (key === "actions") actions.splice(1, 0, structuredClone(actions[0]));
      else actions[0].eligible_pending.push(structuredClone(actions[0].eligible_pending[0]));
      expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
    }
  });
  it("rejects a continuation that lists its running execution as pending", () => {
    const s = snapshot("duplicate-request");
    s.executions[0].actions[1].eligible_pending[0].execution_id = s.executions[0].execution_id;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects evidence gaps relabeled as a conclusive failure", () => {
    const s = snapshot("restart-unknown"), r = s.executions[0];
    r.state = "FAILED"; r.reason = "ROBOT_FAULT"; r.conflicts.missing_events = false;
    Object.assign(r.edge_evidence, {effective_state: "FAILED", terminal_states: ["FAILED"],
      reason: "robot_faulted", verified: true, result_verification: "VERIFIED"});
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects positive raw collection relabeled as FAILED instead of PARTIAL", () => {
    const s = snapshot("partial-preempted"), r = s.executions[0]; r.state = "FAILED";
    r.edge_evidence.reason = "unknown:policy_preempted";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects partial with missing assignment terminal evidence", () => {
    const s = snapshot("partial-preempted"); s.executions[0].runtime_evidence.assignment_terminal = false;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects sparse arrays with a typed contract error", () => {
    const s = snapshot(); s.bindings = new Array(1);
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each([
    ["environment", "PHYSICAL"], ["unexpected", true], ["schema", "v2"], ["now_sim_t_s", NaN],
    ["now_sim_t_s", Infinity], ["now_sim_t_s", -1], ["now_sim_t_s", Number.MAX_SAFE_INTEGER + 1],
    ["round_index", true], ["round_index", 0.5], ["control_interval_s", 0],
    ["server_time_utc", "2026-02-30T00:00:00Z"], ["server_time_utc", "2026-09-23T24:00:00Z"],
    ["server_time_utc", "2026-09-23T01:00:00+00:00"], ["server_time_utc", "2026-09-23T01:00:00.1234567Z"],
    ["simulation_time_utc", "2026-09-23T01:00:00Z"], ["replay_digest", "X".repeat(64)],
    ["replay_digest", `${"a".repeat(64)}\n`], ["session_id", "fixture-session-v3\n"],
    ["server_time_utc", "2026-09-23T01:00:00Z\n"], ["executions.0.edge_evidence.reason", "unknown:fixture\n"],
    ["bindings.0.task_id", `task_${"5".repeat(24)}\n`],
    ["bindings.0.task_id", "5".repeat(64)], ["bindings.0.task_content_digest", `task_${"5".repeat(24)}`],
    ["bindings.0.bound_at_utc", "2026-09-16T00:00:00Z"], ["bindings.0.bound_at_sim_t_s", 700],
    ["bindings.0.task_created_at_utc", "2026-09-16T00:01:01Z"],
    ["bindings.0.cycle_evidence.observed_at_utc", "2026-09-16T00:01:01Z"],
    ["bindings.0.cycle_evidence.valid_until_utc", "2026-09-16T00:00:59Z"],
    ["bindings.0.max_execution_s", 600], ["bindings.0.handoff_binding_mode", "INFERRED"],
    ["requests.0.due_at_utc", "2026-09-23T01:00:00Z"], ["requests.0.expires_at_utc", "2026-09-16T00:04:59Z"],
    ["requests.0.eligible_sim_t_s", 300], ["receipts.0.durable", false],
    ["executions.0.state", "WASHED"], ["executions.0.stage", "SUPPLIED"], ["executions.0.reason", "DONE"],
    ["executions.0.stage", "UNLOADED_TO_STATION"], ["executions.0.terminal_sim_t_s", 119],
    ["executions.0.terminal_sim_t_s", 661], ["executions.0.execution_deadline_sim_t_s", 779],
    ["executions.0.started_sim_t_s", 300], ["executions.0.started_sim_t_s", null],
    ["executions.0.actions.0.selection", "RUNNING_CONTINUATION"],
    ["executions.0.actions.0.original_action.name", "SendToCharge"],
    ["executions.0.actions.0.selected_action.robot_id", "R2"], ["executions.0.actions.0.selected_action.target_id", "FAR"],
    ["executions.0.actions.0.safety_shield", "REJECTED"], ["executions.0.actions.0.sim_t_s", 59],
    ["executions.0.actions.0.eligible_pending.0.eligible_sim_t_s", 121],
    ["executions.0.actions.0.eligible_pending.0.latest_start_sim_t_s", 120],
    ["executions.0.actions.0.eligible_pending.1.execution_id", "0".repeat(64)],
    ["executions.0.actions.0.eligible_pending.1.eligible_sim_t_s", 0],
    ["executions.0.actions.0.eligible_pending.1.latest_start_sim_t_s", 299],
    ["executions.0.actions.1.selected_action.index", 3], ["executions.0.actions.1.selected_action.target_id", "H1"],
    ["executions.0.raw_quantity.balls", 0], ["executions.0.raw_quantity.balls", true],
    ["executions.0.raw_quantity.balls", 1.5], ["executions.0.raw_quantity.status", "INCOMPLETE"],
    ["executions.0.raw_quantity.source", "EDGE"], ["executions.0.raw_quantity.assignment_id", "other"],
    ["executions.0.raw_quantity.source_event_ids", []], ["executions.0.raw_quantity.event_digest", null],
    ["executions.0.raw_quantity.destination_id", "R2"], ["executions.0.raw_quantity.milestone", "UNLOADED_TO_STATION"],
    ["executions.0.unload_quantity.destination_id", "H2"], ["executions.0.unload_quantity.balls", 43],
    ["executions.0.runtime_evidence.event_start_sequence", 10], ["executions.0.runtime_evidence.event_digest", null],
    ["executions.0.runtime_evidence.event_start_sequence", null], ["executions.0.runtime_evidence.event_end_sequence", null],
    ["executions.0.runtime_evidence.event_sequence_complete", false], ["executions.0.runtime_evidence.start_admitted", false],
    ["executions.0.runtime_evidence.assignment_accepted", false], ["executions.0.runtime_evidence.assignment_terminal", false],
    ["executions.0.runtime_evidence.conservation_passed", false], ["executions.0.runtime_evidence.payload_parity_passed", null],
    ["executions.0.runtime_evidence.collection_exit_reason", "ZONE_EMPTY"],
    ["executions.0.edge_evidence.accepted", false], ["executions.0.edge_evidence.verified", false],
    ["executions.0.edge_evidence.result_verification", "UNVERIFIED"], ["executions.0.edge_evidence.terminal_states", []],
    ["executions.0.edge_evidence.terminal_states", ["SUCCEEDED", "FAILED"]],
    ["executions.0.device_protection.authorization_blocked", true], ["executions.0.conflicts.missing_events", true],
    ["executions.0.success_display_allowed", false], ["executions.0.reconciliation", "PERFORMED"],
  ])("rejects malformed or contradictory %s = %s", (path, value) => {
    const s = snapshot(); mutate(s, path as string, value);
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["bindings", "requests", "receipts", "executions"] as const)("rejects duplicate %s identities", (key) => {
    const s = snapshot(); (s[key] as unknown[]).push(structuredClone(s[key][0]));
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["bindings", "requests", "receipts", "executions"] as const)("rejects missing %s reverse links", (key) => {
    const s = snapshot(); s[key] = []; expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each([
    "bindings.0.session_id", "bindings.0.round_id", "bindings.0.series_id", "bindings.0.engine_digest", "bindings.0.config_digest",
    "requests.0.session_id", "requests.0.round_id", "requests.0.task_id", "requests.0.incarnation", "requests.0.binding_id",
    "requests.0.task_content_digest", "receipts.0.request_id", "receipts.0.binding_id", "receipts.0.execution_id",
    "receipts.0.attempt_id", "executions.0.request_id", "executions.0.task_id", "executions.0.incarnation",
    "executions.0.runtime_robot_id", "executions.0.runtime_zone_id", "executions.0.handoff_station_id", "executions.0.edge_evidence.task_id",
  ])("rejects cross-linked %s", (path) => {
    const s = snapshot(); mutate(s, path, path.endsWith("task_id") ? `task_${"b".repeat(24)}` : "b".repeat(64));
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each([
    ["partial-preempted", "edge_evidence.effective_state", "SUCCEEDED"],
    ["partial-preempted", "edge_evidence.reason", "unknown:wrong"],
    ["partial-preempted", "unload_quantity.status", "INCOMPLETE"],
    ["partial-preempted", "runtime_evidence.event_sequence_complete", false],
    ["partial-preempted", "unload_quantity.balls", 0],
    ["policy-missed", "edge_evidence.effective_state", "REJECTED"],
    ["policy-missed", "edge_evidence.reason", "unknown:wrong"],
    ["safety-rejected", "edge_evidence.effective_state", "REJECTED"],
    ["safety-rejected", "edge_evidence.reason", "unknown:wrong"],
    ["restart-unknown", "raw_quantity.balls", 0],
    ["restart-unknown", "edge_evidence.result_verification", "VERIFIED"],
    ["terminal-conflict", "conflicts.terminal_conflict", false],
    ["terminal-conflict", "device_protection.protected", false],
    ["terminal-conflict", "device_protection.authorization_blocked", false],
    ["terminal-conflict", "edge_evidence.result_verification", "UNVERIFIED"],
    ["terminal-conflict", "edge_evidence.effective_state", "SUCCEEDED"],
    ["terminal-conflict", "success_display_allowed", true],
    ["duplicate-request", "actions.1.selection", "WAIT_SLOT"],
    ["duplicate-request", "actions.1.sim_t_s", 780],
    ["duplicate-request", "actions.1.selected_action.robot_id", "R2"],
    ["duplicate-request", "actions.1.selected_action.target_id", "H1"],
  ])("rejects %s contradiction at %s", (name, path, value) => {
    const s = snapshot(name as string); mutate(s, `executions.0.${path}`, value);
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["PENDING", "RUNNING"])("rejects terminal fields on %s", (state) => {
    const s = state === "PENDING" ? pending() : running(); s.executions[0].terminal_sim_t_s = 120;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects unknown and missing keys at every object level", () => {
    const walk = (value: unknown, path: string[]) => {
      if (Array.isArray(value)) { value.forEach((v, i) => walk(v, [...path, String(i)])); return; }
      if (!value || typeof value !== "object") return;
      const changed = snapshot(); mutate(changed, [...path, "unexpected"].join("."), true);
      expect(() => parseCollectionExecutions(changed)).toThrow(ManagerApiError);
      for (const [key, child] of Object.entries(value)) {
        const missing = snapshot(); let target = missing as unknown as Record<string, unknown>;
        for (const part of path) target = target[part] as Record<string, unknown>;
        delete target[key]; expect(() => parseCollectionExecutions(missing)).toThrow(ManagerApiError);
        walk(child, [...path, key]);
      }
    };
    walk(snapshot(), []);
  });
});

describe("review regressions: terminal evidence semantics", () => {
  it.each([299, 300, 301])("enforces the exclusive pending latest-start at %s seconds", (now) => {
    const s = pending(); s.now_sim_t_s = now;
    s.simulation_time_utc = new Date(Date.parse(s.session_epoch_utc) + now * 1000).toISOString();
    if (now < 300) expect(parseCollectionExecutions(s).executions[0].state).toBe("PENDING");
    else expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each([779, 780, 781])("enforces the exclusive running deadline at %s seconds", (now) => {
    const s = running(); s.now_sim_t_s = now;
    s.simulation_time_utc = new Date(Date.parse(s.session_epoch_utc) + now * 1000).toISOString();
    if (now < 780) expect(parseCollectionExecutions(s).executions[0].state).toBe("RUNNING");
    else expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["PENDING", "RUNNING"])("rejects %s in an ended session before its own deadline", (state) => {
    const s = state === "PENDING" ? pending() : running(); s.session_state = "ENDED";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["PENDING", "RUNNING"])("retains %s in a paused session before its deadline", (state) => {
    const s = state === "PENDING" ? pending() : running(); s.session_state = "PAUSED";
    expect(parseCollectionExecutions(s).executions[0].state).toBe(state);
  });
  it.each([779, 780])("requires remaining pending execution horizon within session end %s", (horizon) => {
    const s = pending(); s.session_end_sim_t_s = horizon; s.bindings[0].session_end_sim_t_s = horizon;
    if (horizon === 780) expect(parseCollectionExecutions(s).executions[0].state).toBe("PENDING");
    else expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects a future-eligible pending task whose earliest possible completion exceeds the session horizon", () => {
    const s = pending(), r = s.executions[0], q = s.requests[0];
    s.session_end_sim_t_s = 900; s.bindings[0].session_end_sim_t_s = 900;
    r.eligible_sim_t_s = q.eligible_sim_t_s = 240; r.latest_start_sim_t_s = q.latest_start_sim_t_s = 360;
    q.due_at_utc = "2026-09-16T00:04:00Z"; q.expires_at_utc = "2026-09-16T00:06:00Z";
    expect(parseCollectionExecutions(s).executions[0].state).toBe("PENDING");
    s.session_end_sim_t_s = 899; s.bindings[0].session_end_sim_t_s = 899;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("accepts a verified restart-before-start failure without quantity invention", () => {
    const r = parseCollectionExecutions(notStartedAfterRestart()).executions[0];
    expect(r.started_sim_t_s).toBeNull(); expect(r.state).toBe("FAILED"); expect(r.raw_quantity.balls).toBeNull();
  });
  it.each([
    ["reason", "ZONE_EMPTY"], ["edge_evidence.reason", "unknown:other"], ["edge_evidence.accepted", false],
    ["raw_quantity.status", "INCOMPLETE"], ["runtime_evidence.assignment_terminal", true],
    ["runtime_evidence.collection_exit_reason", "ZONE_EMPTY"],
  ])("rejects restart-before-start mismatch at %s", (path, value) => {
    const s = notStartedAfterRestart(); mutate(s.executions[0], path, value);
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["v3", "edge", "both"])("rejects %s restart-before-start claims after an actual start", (source) => {
    const s = protectedExit("ROBOT_FAULT"), r = s.executions[0];
    r.state = "FAILED"; r.raw_quantity.balls = 0; r.runtime_evidence.collection_exit_reason = null;
    r.reason = source === "edge" ? "ZONE_EMPTY" : "NOT_STARTED_AFTER_RESTART";
    r.edge_evidence.reason = source === "v3" ? "unknown:other" : "not_started_after_restart";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each([
    ["reason", "EVIDENCE_INCOMPLETE"], ["edge_evidence.reason", "unknown:other"], ["edge_evidence.accepted", false],
    ["device_protection.reasons", ["ORPHANED_ACTIVITY"]], ["raw_quantity.status", "NOT_REACHED"],
    ["unload_quantity.status", "NOT_REACHED"],
  ])("rejects interrupted restart mismatch at %s", (path, value) => {
    const s = snapshot("restart-unknown"); mutate(s.executions[0], path, value);
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects interrupted V3 restart reason without a start even when Edge reason is changed", () => {
    const s = pending(), r = s.executions[0];
    r.state = "INCONCLUSIVE"; r.stage = "TERMINAL"; r.reason = "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME"; r.terminal_sim_t_s = 120;
    Object.assign(r.edge_evidence, {effective_state: "INCONCLUSIVE", terminal_states: ["INCONCLUSIVE"],
      reason: "unknown:other", verified: false, result_verification: "UNVERIFIED"});
    r.device_protection = {protected: true, authorization_blocked: true, reasons: ["RESTART_UNKNOWN"]};
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects a completed result hidden behind an interrupted V3 restart reason", () => {
    const s = snapshot("partial-preempted"), r = s.executions[0]; r.actions.pop();
    r.reason = "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME"; r.runtime_evidence.collection_exit_reason = null;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  for (const reason of ["ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED"] as const) {
    it(`accepts protected ${reason} with matching runtime evidence`, () => {
      expect(parseCollectionExecutions(protectedExit(reason)).executions[0].reason).toBe(reason);
    });
    it.each(["unprotected", "wrong-protection", "unblocked", "wrong-reason", "wrong-exit"])(`rejects ${reason} with %s`, (mutation) => {
      const s = protectedExit(reason), r = s.executions[0];
      if (mutation === "unprotected") r.device_protection = {protected: false, authorization_blocked: false, reasons: []};
      if (mutation === "wrong-protection") r.device_protection.reasons = ["ORPHANED_ACTIVITY"];
      if (mutation === "unblocked") r.device_protection.authorization_blocked = false;
      if (mutation === "wrong-reason") r.reason = "ZONE_EMPTY";
      if (mutation === "wrong-exit") r.runtime_evidence.collection_exit_reason = "ZONE_EMPTY";
      expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
    });
  }
  it("rejects a preempting action hidden inside a success", () => {
    const s = snapshot(); s.executions[0].actions[1] = snapshot("partial-preempted").executions[0].actions[1];
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["reason", "runtime_evidence.collection_exit_reason"])("rejects preemption with mismatched %s", (path) => {
    const s = snapshot("partial-preempted"); mutate(s.executions[0], path, "ZONE_EMPTY");
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("requires an actual preemption action for preemption reason and exit evidence", () => {
    const s = snapshot("partial-preempted"); s.executions[0].actions.pop();
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["reason", "runtime_evidence.collection_exit_reason"])("rejects isolated preemption claim in %s", (path) => {
    const s = snapshot("partial-preempted"), r = s.executions[0];
    r.actions.pop(); r.reason = "ZONE_EMPTY"; r.runtime_evidence.collection_exit_reason = "ZONE_EMPTY";
    mutate(r, path, "POLICY_PREEMPTED");
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["PARTIAL", "FAILED", "INCONCLUSIVE"] as const)("preserves %s preemption classified by quantity evidence", (state) => {
    const s = snapshot("partial-preempted"), r = s.executions[0]; r.state = state;
    if (state === "FAILED") { r.raw_quantity.balls = 0; r.edge_evidence.reason = "unknown:policy_preempted"; }
    if (state === "INCONCLUSIVE") {
      r.raw_quantity.status = "INCOMPLETE"; r.raw_quantity.balls = null; r.runtime_evidence.event_sequence_complete = false;
      Object.assign(r.edge_evidence, {effective_state: "INCONCLUSIVE", terminal_states: ["INCONCLUSIVE"],
        reason: "unknown:policy_preempted", verified: false, result_verification: "UNVERIFIED"});
      r.conflicts.missing_events = true;
      r.device_protection = {protected: true, authorization_blocked: true, reasons: ["ORPHANED_ACTIVITY"]};
    }
    expect(parseCollectionExecutions(s).executions[0].state).toBe(state);
  });
});

describe("second review regressions: causal evidence and conflict overlays", () => {
  it.each(["SendToCharge", "ReassignRobot", "PauseRobot", "ResumeRobot", "RequestHumanAssistance", "AssignCollection"] as const)(
    "rejects own-robot %s disguised as unchanged policy during a successful lease", (name) => {
      const s = snapshot(), a = s.executions[0].actions[1];
      a.original_action.name = name; a.selected_action.name = name; a.selection = "ORIGINAL_POLICY_UNCHANGED";
      expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
    });
  it("requires matching handoff evidence to be identified as convergence", () => {
    const s = snapshot(); s.executions[0].actions[1].selection = "ORIGINAL_POLICY_UNCHANGED";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("preserves unchanged non-Wait actions for another robot during the lease", () => {
    const s = snapshot(), a = structuredClone(s.executions[0].actions[1]);
    a.sim_t_s = 240; a.selection = "ORIGINAL_POLICY_UNCHANGED";
    a.original_action = {name: "SendToCharge", index: 7, robot_id: "R2", target_id: null};
    a.selected_action = structuredClone(a.original_action); s.executions[0].actions.splice(1, 0, a);
    expect(parseCollectionExecutions(s).executions[0].state).toBe("SUCCEEDED");
  });
  it("rejects unchanged policy disguising the action that terminates its own lease", () => {
    const s = snapshot("partial-preempted"); s.executions[0].actions[1].selection = "ORIGINAL_POLICY_UNCHANGED";
    s.executions[0].reason = "ZONE_EMPTY"; s.executions[0].runtime_evidence.collection_exit_reason = "ZONE_EMPTY";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  function overlay(s: CollectionExecutionsSnapshot, kind: "terminal" | "replay") {
    const r = s.executions[0]; r.state = "INCONCLUSIVE";
    r.reason = kind === "terminal" ? "TERMINAL_CONFLICT" : "REPLAY_MISMATCH";
    Object.assign(r.edge_evidence, {effective_state: "CONFLICT", verified: false, result_verification: "CONFLICT",
      reason: `unknown:${kind}_conflict`, terminal_states: kind === "terminal" ? ["SUCCEEDED", "FAILED"] : ["FAILED"]});
    r.conflicts.terminal_conflict = kind === "terminal"; r.conflicts.replay_mismatch = kind === "replay";
    r.device_protection.protected = true; r.device_protection.authorization_blocked = true;
    r.device_protection.reasons.push(kind === "terminal" ? "TERMINAL_CONFLICT" : "ORPHANED_ACTIVITY");
    return s;
  }
  for (const exit of ["POLICY_PREEMPTED", "ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED"] as const) {
    for (const kind of ["terminal", "replay"] as const) {
      it(`preserves ${exit} causal evidence beneath a ${kind} conflict overlay`, () => {
        const s = overlay(exit === "POLICY_PREEMPTED" ? snapshot("partial-preempted") : protectedExit(exit), kind);
        const r = parseCollectionExecutions(s).executions[0];
        expect(r.runtime_evidence.collection_exit_reason).toBe(exit); expect(r.state).toBe("INCONCLUSIVE");
        expect(r.success_display_allowed).toBe(false); expect(r.raw_quantity.balls).toBe(12);
      });
    }
    it(`rejects an unsupported conflict reason over ${exit}`, () => {
      const s = exit === "POLICY_PREEMPTED" ? snapshot("partial-preempted") : protectedExit(exit);
      s.executions[0].reason = "TERMINAL_CONFLICT";
      expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
    });
    it(`keeps the underlying ${exit} evidence required under conflict`, () => {
      const s = overlay(exit === "POLICY_PREEMPTED" ? snapshot("partial-preempted") : protectedExit(exit), "terminal");
      if (exit === "POLICY_PREEMPTED") s.executions[0].runtime_evidence.collection_exit_reason = null;
      else s.executions[0].device_protection.reasons = ["TERMINAL_CONFLICT"];
      expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
    });
  }
  it.each(["NOT_REACHED", "INCOMPLETE"] as const)("rejects started FAILED when raw evidence is %s/null", (status) => {
    const s = protectedExit("ROBOT_FAULT"), r = s.executions[0]; r.state = "FAILED";
    r.edge_evidence.reason = "robot_faulted";
    Object.assign(r.raw_quantity, {status, balls: null, source_event_ids: [], event_digest: null});
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("requires the causal preemption action even with a terminal conflict overlay", () => {
    const s = overlay(snapshot("partial-preempted"), "terminal"); s.executions[0].actions.pop();
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each([
    ["edge_evidence.effective_state", "INCONCLUSIVE"], ["edge_evidence.result_verification", "UNVERIFIED"],
    ["device_protection.authorization_blocked", false], ["conflicts.terminal_conflict", false],
    ["success_display_allowed", true],
  ])("does not let a causal exit bypass conflict validation at %s", (path, value) => {
    const s = overlay(protectedExit("ROBOT_FAULT"), "terminal"); mutate(s.executions[0], path as string, value);
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("accepts started FAILED only when complete raw evidence proves zero", () => {
    const s = protectedExit("ROBOT_FAULT"), r = s.executions[0]; r.state = "FAILED";
    r.edge_evidence.reason = "robot_faulted"; r.raw_quantity.balls = 0;
    expect(parseCollectionExecutions(s).executions[0].raw_quantity.balls).toBe(0);
  });
});

describe("final review regressions: admission and terminal limits", () => {
  function timeout(state: "PARTIAL" | "FAILED" | "INCONCLUSIVE" = "PARTIAL") {
    const s = snapshot("partial-preempted"), r = s.executions[0];
    s.now_sim_t_s = 900; s.simulation_time_utc = "2026-09-16T00:15:00Z";
    r.actions.pop(); r.state = state; r.reason = "EXECUTION_TIMEOUT";
    r.runtime_evidence.collection_exit_reason = "EXECUTION_TIMEOUT";
    r.terminal_sim_t_s = r.execution_deadline_sim_t_s;
    if (state === "FAILED") { r.raw_quantity.balls = 0; r.edge_evidence.reason = "unknown:execution_timeout"; }
    if (state === "INCONCLUSIVE") {
      Object.assign(r.raw_quantity, {status: "INCOMPLETE", balls: null, source_event_ids: [], event_digest: null});
      Object.assign(r.edge_evidence, {effective_state: "INCONCLUSIVE", terminal_states: ["INCONCLUSIVE"],
        verified: false, result_verification: "UNVERIFIED", reason: "unknown:execution_timeout"});
      r.runtime_evidence.event_sequence_complete = false; r.conflicts.missing_events = true;
      r.device_protection = {protected: true, authorization_blocked: true, reasons: ["ORPHANED_ACTIVITY"]};
    }
    return s;
  }
  function horizon(accepted = true) {
    const s = snapshot("policy-missed"), r = s.executions[0];
    s.session_end_sim_t_s = 779; s.bindings[0].session_end_sim_t_s = 779;
    r.reason = "INSUFFICIENT_SESSION_HORIZON"; r.terminal_sim_t_s = 120; r.actions = [];
    r.edge_evidence.accepted = accepted; r.edge_evidence.reason = "unknown:insufficient_session_horizon";
    r.edge_evidence.effective_state = accepted ? "FAILED" : "REJECTED";
    r.edge_evidence.terminal_states = [r.edge_evidence.effective_state];
    return s;
  }
  it.each(["PARTIAL", "FAILED", "INCONCLUSIVE"] as const)("preserves valid %s timeout classification", (state) => {
    expect(parseCollectionExecutions(timeout(state)).executions[0].state).toBe(state);
  });
  it.each(["PARTIAL", "FAILED", "INCONCLUSIVE"] as const)("requires retained Edge acceptance for started %s", (state) => {
    const s = timeout(state); s.executions[0].edge_evidence.accepted = false;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("requires assignment-terminal evidence for a conclusive started zero failure", () => {
    const s = timeout("FAILED"); s.executions[0].runtime_evidence.assignment_terminal = false;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("requires retained Edge acceptance beneath terminal conflict", () => {
    const s = snapshot("terminal-conflict"); s.executions[0].edge_evidence.accepted = false;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("allows an incomplete timeout without conclusive assignment-terminal evidence", () => {
    const s = timeout("INCONCLUSIVE"); s.executions[0].runtime_evidence.assignment_terminal = false;
    expect(parseCollectionExecutions(s).executions[0].state).toBe("INCONCLUSIVE");
  });
  it.each(["PARTIAL", "FAILED"] as const)("does not let timeout override %s quantity classification", (state) => {
    const s = timeout(state); s.executions[0].raw_quantity.balls = state === "PARTIAL" ? 0 : 12;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["success", "running"])("rejects timeout exit on %s", (name) => {
    const s = name === "success" ? snapshot() : running();
    s.executions[0].runtime_evidence.collection_exit_reason = "EXECUTION_TIMEOUT";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("keeps restart outcomes valid without fabricated assignment-terminal evidence", () => {
    for (const s of [snapshot("restart-unknown"), notStartedAfterRestart()]) {
      expect(s.executions[0].runtime_evidence.assignment_terminal).toBe(false);
      expect(parseCollectionExecutions(s)).toEqual(s);
    }
  });
  it.each([
    ["reason", "ZONE_EMPTY"], ["runtime_evidence.collection_exit_reason", "ZONE_EMPTY"],
    ["runtime_evidence.collection_exit_reason", null], ["terminal_sim_t_s", 779], ["terminal_sim_t_s", 781],
    ["reason", "TERMINAL_CONFLICT"], ["reason", "REPLAY_MISMATCH"],
  ])("rejects contradictory timeout evidence at %s=%s", (path, value) => {
    const s = timeout(); mutate(s.executions[0], path as string, value);
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects timeout on an unstarted record", () => {
    const s = snapshot("policy-missed"), r = s.executions[0];
    r.reason = "EXECUTION_TIMEOUT"; r.runtime_evidence.collection_exit_reason = "EXECUTION_TIMEOUT";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each(["terminal", "replay"] as const)("preserves timeout beneath an evidenced %s conflict", (kind) => {
    const s = timeout(), r = s.executions[0]; r.state = "INCONCLUSIVE";
    r.reason = kind === "terminal" ? "TERMINAL_CONFLICT" : "REPLAY_MISMATCH";
    r.conflicts.terminal_conflict = kind === "terminal"; r.conflicts.replay_mismatch = kind === "replay";
    Object.assign(r.edge_evidence, {effective_state: "CONFLICT", verified: false, result_verification: "CONFLICT",
      terminal_states: kind === "terminal" ? ["SUCCEEDED", "FAILED"] : ["FAILED"]});
    r.device_protection = {protected: true, authorization_blocked: true,
      reasons: [kind === "terminal" ? "TERMINAL_CONFLICT" : "ORPHANED_ACTIVITY"]};
    expect(parseCollectionExecutions(s).executions[0].runtime_evidence.collection_exit_reason).toBe("EXECUTION_TIMEOUT");
    r.terminal_sim_t_s = 779;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each([true, false])("preserves insufficient horizon with accepted=%s", (accepted) => {
    const s = horizon(accepted); expect(parseCollectionExecutions(s)).toEqual(s);
  });
  it.each([780, 900])("rejects insufficient horizon when a complete window fits at terminal evaluation with end=%s", (end) => {
    const s = horizon(); s.session_end_sim_t_s = end; s.bindings[0].session_end_sim_t_s = end;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("uses future eligibility when testing a no-start horizon", () => {
    const s = horizon(); s.session_end_sim_t_s = 899; s.bindings[0].session_end_sim_t_s = 899;
    s.requests[0].eligible_sim_t_s = 240; s.requests[0].due_at_utc = "2026-09-16T00:04:00Z";
    s.executions[0].eligible_sim_t_s = 240;
    expect(parseCollectionExecutions(s)).toEqual(s);
    s.session_end_sim_t_s = 900; s.bindings[0].session_end_sim_t_s = 900;
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it.each([
    ["state", "FAILED"], ["runtime_evidence.assignment_terminal", true],
    ["runtime_evidence.collection_exit_reason", "ZONE_EMPTY"], ["edge_evidence.reason", "unknown:other"],
    ["reason", "ZONE_EMPTY"], ["raw_quantity.status", "INCOMPLETE"], ["unload_quantity.status", "INCOMPLETE"],
  ])("rejects contradictory insufficient-horizon evidence at %s", (path, value) => {
    const s = horizon(); mutate(s.executions[0], path as string, value);
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects insufficient horizon carrying an original policy action", () => {
    const s = horizon(); s.executions[0].actions = snapshot("policy-missed").executions[0].actions.slice(0, 1);
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
  it("rejects a started partial relabeled as insufficient horizon", () => {
    const s = timeout(); s.executions[0].reason = "INSUFFICIENT_SESSION_HORIZON";
    s.executions[0].runtime_evidence.collection_exit_reason = null;
    s.executions[0].edge_evidence.reason = "unknown:insufficient_session_horizon";
    expect(() => parseCollectionExecutions(s)).toThrow(ManagerApiError);
  });
});

describe("collection execution GET-only client", () => {
  afterEach(() => vi.useRealTimers());
  it("exposes only read and sends an uncached same-origin GET", async () => {
    const fetcher = vi.fn(async () => Response.json(envelope()));
    const client = createCollectionExecutionsClient(fetcher);
    expect(Object.keys(client)).toEqual(["read"]);
    expect((await client.read()).executions[0].raw_quantity.balls).toBe(44);
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("/api/v1/collection-executions",
      { method: "GET", cache: "no-store", signal: expect.any(AbortSignal) });
  });
  it.each([null, [], {schema: "wrong", disclaimer: "fixture", data: {}},
    {...envelope(), extra: true}, {...envelope(), disclaimer: ""}, {...envelope(), error: {code: "x", detail: "x"}},
    {schema: "nxt-site-agent/api/v0", disclaimer: "fixture"}])("rejects malformed envelopes", async (payload) => {
    await expect(createCollectionExecutionsClient(async () => Response.json(payload)).read()).rejects.toBeInstanceOf(ManagerApiError);
  });
  it("preserves typed service errors and rejects unreadable JSON", async () => {
    const failure = {schema: "nxt-site-agent/api/v0", disclaimer: "fixture", error:
      {code: "collection_execution_unavailable", detail: "No execution service exists."}};
    await expect(createCollectionExecutionsClient(async () => Response.json(failure, {status: 503})).read())
      .rejects.toMatchObject({code: "collection_execution_unavailable", status: 503});
    await expect(createCollectionExecutionsClient(async () => new Response("not json")).read()).rejects.toBeInstanceOf(ManagerApiError);
  });
  it("rejects duplicate JSON keys before decoding erases them", async () => {
    const raw = JSON.stringify(envelope()).replace('"environment":"SIMULATION"', '"environment":"PHYSICAL","environ\\u006dent":"SIMULATION"');
    await expect(createCollectionExecutionsClient(async () => new Response(raw)).read()).rejects.toBeInstanceOf(ManagerApiError);
  });
  it.each(["timeout", "caller", "already-aborted"])("aborts on %s and cleans up", async (mode) => {
    vi.useFakeTimers(); const caller = new AbortController();
    if (mode === "already-aborted") caller.abort();
    const fetcher = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      const cancel = () => reject(new DOMException("Aborted", "AbortError"));
      if (init?.signal?.aborted) cancel(); else init?.signal?.addEventListener("abort", cancel, {once: true});
    }));
    const result = createCollectionExecutionsClient(fetcher).read(caller.signal);
    const assertion = expect(result).rejects.toMatchObject({name: "AbortError"});
    if (mode === "caller") caller.abort();
    if (mode === "timeout") await vi.advanceTimersByTimeAsync(8000);
    await assertion; expect(vi.getTimerCount()).toBe(0);
  });
});
