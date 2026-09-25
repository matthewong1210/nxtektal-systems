/**
 * Presentation of the validated service-capability declaration.
 *
 * The declaration comes only from the Codex-owned parser
 * (`taskOpsCapabilities` in lib/task-ops.ts). These helpers turn it into
 * per-operation reasons and never infer support from HTTP success, the
 * transport, runtime fields or the absence of a declaration. SUPPORTED is
 * not permission: the scheduler health gate, request validation, busy
 * state and record-level preconditions still apply on top.
 */
import type { EffectiveTaskOpsCapabilities, TaskOpsWriteOperation } from "../lib/task-ops";
import { Badge, type Tone } from "./ui";

/** `undefined` = no task-ops reading is wired (standalone render, no gating);
 * `null` = a reading is wired but nothing has been read yet (unknown, fail closed). */
export type CapabilityInput = EffectiveTaskOpsCapabilities | null | undefined;

export const OPERATION_LABEL: Record<TaskOpsWriteOperation, string> = {
  planning_inputs_create: "New operating input",
  planning_plans_create: "New or revised plan",
  planning_confirmations_create: "Plan confirmation",
  planning_outcomes_create: "Outcome recording",
  schedules_create: "Direct schedule creation",
  schedules_cancel: "Schedule cancellation",
  notifications_acknowledge: "Notification acknowledgement",
  notifications_resolve: "Notification resolution",
};

/** Why an operation's control is withheld by the service declaration, or
 * null when the declaration installs it (other gates still apply). */
export function capabilityBlocker(capabilities: CapabilityInput, operation: TaskOpsWriteOperation): string | null {
  if (capabilities === undefined) return null;
  const label = OPERATION_LABEL[operation];
  if (capabilities === null) return `The task service has not been read successfully, so write capabilities are unknown; ${label.toLowerCase()} is disabled.`;
  if (capabilities.mode === "UNDECLARED") return `This service declares no write capabilities (UNDECLARED); ${label.toLowerCase()} is disabled while records stay readable.`;
  if (capabilities.operations[operation] === "UNAVAILABLE") {
    if (capabilities.mode === "FIXED_V3_EXECUTION") {
      return `${label} is not installed on this service: it runs a preset single-task demonstration (FIXED_V3_EXECUTION) whose one planning confirmation and execution are fixed.`;
    }
    return `${label} is not installed on this service (${capabilities.mode}).`;
  }
  return null;
}

export function serviceModeBadge(capabilities: CapabilityInput): { label: string; tone: Tone } | null {
  if (capabilities === undefined) return null;
  if (capabilities === null) return { label: "CAPABILITIES UNKNOWN", tone: "warn" };
  switch (capabilities.mode) {
    case "FIXED_V3_EXECUTION":
      return { label: "PRESET SINGLE-TASK DEMO", tone: "info" };
    case "LEGACY_PILOT_DISPATCH":
      return { label: "LEGACY PILOT DISPATCH", tone: "muted" };
    case "CONTINUOUS_V3_EXECUTION":
      return { label: "CONTINUOUS V3 SESSION", tone: "info" };
    case "UNDECLARED":
      return { label: "WRITES UNDECLARED", tone: "warn" };
  }
}

export function serviceModeText(capabilities: CapabilityInput): string | null {
  if (capabilities === undefined) return null;
  if (capabilities === null) return "Write capabilities are unknown until the task service is read successfully; every write is disabled.";
  switch (capabilities.mode) {
    case "FIXED_V3_EXECUTION":
      return "Service mode FIXED_V3_EXECUTION: a preset single-task demonstration. New inputs, plans, confirmations, schedules and cancellations are not installed; outcome recording and notification handling stay available as evidence-only writes, still subject to the scheduler health check and each record's own preconditions.";
    case "LEGACY_PILOT_DISPATCH":
      return "Service mode LEGACY_PILOT_DISPATCH: the legacy pilot dispatch rehearsal with all write routes installed; the legacy rules apply.";
    case "CONTINUOUS_V3_EXECUTION":
      return "Service mode CONTINUOUS_V3_EXECUTION: confirmed Planning plans create sequential simulated collection tasks in the active V3 session. Direct schedule creation is not installed; pending cancellation, outcome recording and notification handling remain subject to service health and each record's own conditions.";
    case "UNDECLARED":
      return "This service declares no write capabilities (UNDECLARED). Records remain readable and every write is disabled; support is never inferred from HTTP success, the transport or other fields.";
  }
}

export function ServiceModeBadge({ capabilities }: { capabilities: CapabilityInput }) {
  const badge = serviceModeBadge(capabilities);
  return badge ? <Badge tone={badge.tone}>{badge.label}</Badge> : null;
}

/** One line under a control group that the declaration withholds. */
export function CapabilityNote({ capabilities, operation }: { capabilities: CapabilityInput; operation: TaskOpsWriteOperation }) {
  if (capabilities === undefined) return null;
  const blocker = capabilityBlocker(capabilities, operation);
  if (blocker === null) return null;
  const tone: Tone = capabilities === null || capabilities.mode === "UNDECLARED" ? "warn" : "muted";
  const label = capabilities === null ? "UNKNOWN" : capabilities.mode === "UNDECLARED" ? "UNDECLARED" : "NOT INSTALLED";
  return (
    <p className="fineprint capability-note" role="note">
      <Badge tone={tone}>{label}</Badge> {blocker}
    </p>
  );
}
