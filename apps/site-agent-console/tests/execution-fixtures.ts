/**
 * SIMULATION FIXTURES — frozen contract examples, not live data.
 *
 * Every snapshot here comes from the eight checked-in examples in
 * `simulation/docs/contracts/collection-execution-v1/examples` and passes
 * through the real Codex-owned parser (`lib/collection-executions.ts`).
 * They prove that the components render the frozen contract; they are not
 * evidence of a connected route, a V3 session or a real integration run.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parseCollectionExecutions, type CollectionExecutionsSnapshot, type QuantityEvidence } from "../lib/collection-executions";

export const EXAMPLE_NAMES = [
  "success",
  "policy-missed",
  "partial-preempted",
  "safety-rejected",
  "restart-unknown",
  "identity-conflict",
  "duplicate-request",
  "terminal-conflict",
] as const;
export type ExampleName = (typeof EXAMPLE_NAMES)[number];

const EXAMPLES = join(import.meta.dirname, "..", "..", "..", "simulation", "docs", "contracts", "collection-execution-v1", "examples");

/** The raw Manager API envelope of an example (`snapshot.body`), unparsed. */
export function exampleEnvelope(name: ExampleName): { schema: string; disclaimer: string; data: Record<string, unknown> } {
  return (JSON.parse(readFileSync(join(EXAMPLES, `${name}.json`), "utf8")) as { snapshot: { body: { schema: string; disclaimer: string; data: Record<string, unknown> } } }).snapshot.body;
}

/** The example data, freshly cloned, so a test may mutate it before parsing. */
export function exampleData(name: ExampleName): Record<string, unknown> {
  return structuredClone(exampleEnvelope(name).data);
}

/** The example parsed by the real strict parser. */
export function exampleSnapshot(name: ExampleName): CollectionExecutionsSnapshot {
  return parseCollectionExecutions(exampleData(name));
}

type Mutable = CollectionExecutionsSnapshot;

/** A durable, accepted request still waiting for its policy slot (derived from policy-missed). */
export function pendingSnapshot(): CollectionExecutionsSnapshot {
  const s = exampleData("policy-missed") as unknown as Mutable;
  const r = s.executions[0];
  s.now_sim_t_s = 120;
  s.simulation_time_utc = "2026-09-16T00:02:00Z";
  r.state = "PENDING";
  r.stage = "WAITING_FOR_POLICY_SLOT";
  r.reason = null;
  r.terminal_sim_t_s = null;
  r.actions = [];
  r.edge_evidence.effective_state = "ACCEPTED";
  r.edge_evidence.reason = null;
  r.edge_evidence.terminal_states = [];
  return parseCollectionExecutions(s);
}

/** A started attempt at the given non-terminal stage (derived from success). */
export function runningSnapshot(stage: "TRAVEL_TO_COLLECTION" | "COLLECTING" | "RAW_COLLECTED_TO_ROBOT" | "UNLOADING"): CollectionExecutionsSnapshot {
  const s = exampleData("success") as unknown as Mutable;
  const r = s.executions[0];
  const blank = exampleData("policy-missed") as unknown as Mutable;
  r.state = "RUNNING";
  r.stage = stage;
  r.reason = null;
  r.terminal_sim_t_s = null;
  r.success_display_allowed = false;
  r.actions = r.actions.slice(0, 1);
  r.edge_evidence.effective_state = "RUNNING";
  r.edge_evidence.terminal_states = [];
  r.runtime_evidence.assignment_terminal = false;
  r.runtime_evidence.collection_exit_reason = null;
  r.raw_quantity = { ...blank.executions[0].raw_quantity, assignment_id: r.assignment_id };
  r.unload_quantity = { ...blank.executions[0].unload_quantity, assignment_id: r.assignment_id };
  return parseCollectionExecutions(s);
}

/** The success example with its session marked PAUSED (the clock is frozen at now_sim_t_s). */
export function pausedSnapshot(): CollectionExecutionsSnapshot {
  const s = exampleData("success") as unknown as Mutable;
  s.session_state = "PAUSED";
  return parseCollectionExecutions(s);
}

/** A copy of the example data whose round identity is moved consistently to `round`. */
export function withRound(data: Record<string, unknown>, round: string, index: number): Record<string, unknown> {
  const s = structuredClone(data) as unknown as Mutable;
  s.round_id = round;
  s.round_index = index;
  for (const b of s.bindings) { b.round_id = round; b.round_index = index; }
  for (const q of s.requests) q.round_id = round;
  for (const r of s.executions) r.round_id = round;
  return s as unknown as Record<string, unknown>;
}

/** Accepted human-assistance exit on a running lease (derived from partial-preempted,
 * following the parser's own contract test recipe). Unparsed data for scripted services. */
export function humanAssistanceData(): Record<string, unknown> {
  const s = exampleData("partial-preempted") as unknown as Mutable;
  const r = s.executions[0];
  const action = r.actions[r.actions.length - 1];
  r.reason = "HUMAN_ASSISTANCE_REQUIRED";
  r.runtime_evidence.collection_exit_reason = "HUMAN_ASSISTANCE_REQUIRED";
  r.device_protection = { protected: true, authorization_blocked: true, reasons: ["HUMAN_ASSISTANCE_REQUIRED"] };
  action.selection = "ORIGINAL_POLICY_UNCHANGED";
  action.original_action.name = "RequestHumanAssistance";
  action.selected_action.name = "RequestHumanAssistance";
  return s as unknown as Record<string, unknown>;
}
export function humanAssistanceSnapshot(): CollectionExecutionsSnapshot {
  return parseCollectionExecutions(humanAssistanceData());
}

/** The success example with its session ENDED (only terminal records may remain). */
export function endedData(): Record<string, unknown> {
  const s = exampleData("success") as unknown as Mutable;
  s.session_state = "ENDED";
  return s as unknown as Record<string, unknown>;
}
export function endedSnapshot(): CollectionExecutionsSnapshot {
  return parseCollectionExecutions(endedData());
}

/** Backend-generated witness of the frozen normal loop (Codex Phase 3B, fixture
 * `collection-execution-normal-loop-v3.json`). It was produced by the Python
 * runtime and checked in; it is still a fixture, not a live service read. */
const WITNESS = join(import.meta.dirname, "..", "..", "..", "simulation", "tests", "course_monitoring", "fixtures", "collection-execution-normal-loop-v3.json");
export function witnessData(): Record<string, unknown> {
  return JSON.parse(readFileSync(WITNESS, "utf8")) as Record<string, unknown>;
}
export function witnessSnapshot(): CollectionExecutionsSnapshot {
  return parseCollectionExecutions(witnessData());
}

/** Backend-generated witness of the continuous V4 two-task loop (Task 6,
 * fixture `two-task-active.json`): one SUCCEEDED history record and one
 * RUNNING record, in the service's own array order (which is not the
 * business order). It is read directly from the checked-in file and passes
 * through the real parser; nothing is copied, rewritten, re-identified or
 * reordered here, and it is still a fixture, not a live service read. */
const CONTINUOUS_WITNESS = join(import.meta.dirname, "..", "..", "..", "simulation", "tests", "fixtures", "continuous-collection-v4", "two-task-active.json");
export function continuousTwoTaskWitnessData(): Record<string, unknown> {
  return JSON.parse(readFileSync(CONTINUOUS_WITNESS, "utf8")) as Record<string, unknown>;
}
export function continuousTwoTaskWitness(): CollectionExecutionsSnapshot {
  return parseCollectionExecutions(continuousTwoTaskWitnessData());
}

/** The same two-task session at an earlier instant, before the second task
 * started: that record is accepted but still waiting for its policy slot
 * (PENDING, no start, no assignment, no ledger evidence), the terminal
 * record is untouched, and every identity and the array order are the
 * witness's own. Derived with the parser's PENDING rules (the recipe of
 * `pendingSnapshot`); left unparsed so a scripted service can serve it. */
export function continuousTwoTaskPendingData(): Record<string, unknown> {
  const s = continuousTwoTaskWitnessData() as unknown as Mutable;
  const running = s.executions.find((record) => record.state === "RUNNING");
  if (!running) throw new Error("the two-task witness has no RUNNING record to rewind");
  // After both bindings exist (31200 s) and before the second request's latest start (31380 s).
  s.now_sim_t_s = 31260;
  s.simulation_time_utc = "2026-09-16T08:41:00Z";
  running.state = "PENDING";
  running.stage = "WAITING_FOR_POLICY_SLOT";
  running.started_sim_t_s = null;
  running.execution_deadline_sim_t_s = null;
  running.assignment_id = null;
  running.actions = [];
  running.edge_evidence.effective_state = "ACCEPTED";
  running.runtime_evidence = {
    ...running.runtime_evidence,
    start_admitted: false, assignment_accepted: false, assignment_terminal: false, collection_exit_reason: null,
    event_start_sequence: null, event_end_sequence: null, event_digest: null, conservation_passed: null, payload_parity_passed: null,
  };
  const notReached = (quantity: QuantityEvidence): QuantityEvidence =>
    ({ ...quantity, status: "NOT_REACHED", balls: null, assignment_id: null, source_event_ids: [], event_digest: null });
  running.raw_quantity = notReached(running.raw_quantity);
  running.unload_quantity = notReached(running.unload_quantity);
  return s as unknown as Record<string, unknown>;
}
