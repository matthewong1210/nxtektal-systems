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
import { parseCollectionExecutions, type CollectionExecutionsSnapshot } from "../lib/collection-executions";

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
