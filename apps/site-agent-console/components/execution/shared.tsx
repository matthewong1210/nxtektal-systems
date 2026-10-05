/**
 * Presentation helpers for the read-only collection execution panel.
 *
 * Every map here is keyed on the frozen contract enums in
 * `lib/collection-executions.ts` (Codex-owned). Nothing is computed from
 * the evidence: labels name what the service recorded, tones assist the
 * text, and a value the parser did not admit never reaches these maps.
 */
import type { ExecutionRecord, QuantityEvidence } from "../../lib/collection-executions";
import type { Tone } from "../ui";

export type ExecutionState = ExecutionRecord["state"];
export type ExecutionStage = ExecutionRecord["stage"];
export type ExecutionReason = NonNullable<ExecutionRecord["reason"]>;

export const STATE_TONE: Record<ExecutionState, Tone> = {
  PENDING: "warn",
  RUNNING: "info",
  SUCCEEDED: "ok",
  PARTIAL: "warn",
  REJECTED: "bad",
  MISSED: "bad",
  FAILED: "bad",
  INCONCLUSIVE: "warn",
};

/** The state chip. SUCCEEDED is green only while the contract's own
 * success_display_allowed flag says so; the browser adds no judgement. */
export function stateBadge(record: ExecutionRecord): { label: string; tone: Tone } {
  if (record.state === "SUCCEEDED" && !record.success_display_allowed) return { label: "SUCCESS NOT DISPLAYABLE", tone: "warn" };
  return { label: record.state, tone: STATE_TONE[record.state] };
}

export const STAGE_LABEL: Record<ExecutionStage, string> = {
  WAITING_FOR_POLICY_SLOT: "Waiting for a policy slot",
  TRAVEL_TO_COLLECTION: "Travelling to the zone",
  COLLECTING: "Collecting",
  RAW_COLLECTED_TO_ROBOT: "Balls on the robot (milestone)",
  TRAVEL_TO_UNLOAD: "Returning to the station",
  UNLOADING: "Unloading",
  UNLOADED_TO_STATION: "Unloaded to the station",
  TERMINAL: "Ended",
};

export const REASON_TEXT: Record<ExecutionReason, string> = {
  UNLOADED_ALL_COLLECTED_BALLS: "All collected balls were unloaded to the bound station.",
  POLICY_SLOT_MISSED: "The original policy never yielded a Wait slot before the latest start.",
  INSUFFICIENT_SESSION_HORIZON: "The session did not have enough time left for a complete attempt.",
  SAFETY_REJECTED: "The simulator's final safety check rejected the start action.",
  ROBOT_PAYLOAD_FULL: "The robot payload was full (a normal collection boundary).",
  ZONE_EMPTY: "The zone was empty at the checked simulation instant.",
  COLLECTION_ACCESS_BLOCKED: "Zone access was blocked before another move.",
  POLICY_PREEMPTED: "The original policy redirected the leased robot.",
  LOW_BATTERY: "The runtime reached its collection battery floor.",
  ROBOT_FAULT: "The runtime robot faulted.",
  ESTOP_LATCHED: "The simulator emergency stop latched; no motion follows it.",
  HUMAN_ASSISTANCE_REQUIRED: "The runtime requires a person; this is not resumable from the browser.",
  EXECUTION_TIMEOUT: "The execution deadline was reached.",
  SESSION_ENDED: "The session ended during the attempt.",
  NOT_STARTED_AFTER_RESTART: "The device restarted after acceptance and before the start; nothing was executed.",
  INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME: "The device restarted after the start without a durable terminal; the outcome is unknown.",
  IDENTITY_CONFLICT: "Task, session or binding identities conflict.",
  TERMINAL_CONFLICT: "Terminal claims conflict; no result is chosen.",
  REPLAY_MISMATCH: "Deterministic replay did not reproduce the recorded prefix.",
  EVIDENCE_INCOMPLETE: "The recorded evidence is incomplete.",
};

export const PROTECTION_TEXT: Record<ExecutionRecord["device_protection"]["reasons"][number], string> = {
  RUNTIME_ACTIVE: "runtime activity still attributed to the device",
  ROBOT_FAULT: "robot fault",
  ESTOP_LATCHED: "emergency stop latched",
  HUMAN_ASSISTANCE_REQUIRED: "human assistance required",
  TERMINAL_CONFLICT: "terminal conflict",
  SESSION_REGRESSION: "session regression",
  INCARNATION_MISMATCH: "incarnation mismatch",
  ORPHANED_ACTIVITY: "orphaned runtime activity",
  RESTART_UNKNOWN: "restart with unknown outcome",
};

export const SESSION_STATE_BADGE: Record<"ACTIVE" | "PAUSED" | "ENDED", { label: string; tone: Tone }> = {
  ACTIVE: { label: "SESSION ACTIVE", tone: "info" },
  PAUSED: { label: "SESSION PAUSED", tone: "warn" },
  ENDED: { label: "SESSION ENDED", tone: "muted" },
};

/** Three quantity states, straight from the contract. Missing evidence is
 * never shown as zero, and NOT_REACHED is the contract's own proof that the
 * milestone did not occur. */
export function quantityText(quantity: QuantityEvidence): { label: string; tone: Tone } {
  switch (quantity.status) {
    case "COMPLETE":
      return { label: `${quantity.balls} balls · ledger-backed (COMPLETE)`, tone: "info" };
    case "NOT_REACHED":
      return { label: "Did not occur (NOT_REACHED, contract evidence)", tone: "muted" };
    case "INCOMPLETE":
      return { label: "Unknown · evidence incomplete or conflicting (INCOMPLETE)", tone: "warn" };
  }
}

/** Simulation seconds as the contract states them. */
export const simSeconds = (seconds: number) => `t = ${Number.isInteger(seconds) ? seconds : Number(seconds.toFixed(3))} s`;

/** RFC3339 UTC text as a readable label; no timezone conversion here
 * because the execution snapshot carries no site timezone. */
export const utcLabel = (value: string) => `${value.replace(/(\.\d+)?Z$/, "")} UTC`;

export const isTerminal = (record: ExecutionRecord) => record.state !== "PENDING" && record.state !== "RUNNING";
