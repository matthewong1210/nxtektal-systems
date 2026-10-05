/**
 * Read-only helpers that pick what the planning panel shows. They select
 * and label server records; they compute no stockout, ranking, or
 * authorization. The service re-checks every rule on confirmation.
 */

import type { ConfirmationRecord, InputRecord, OutcomeRecord, PlanRecord, Stage } from "./planning";
import { compareUtc } from "./site-time";

/** Newest first by real generation instant (mixed whole-second and
 * fractional text compare correctly), then higher version of the same plan,
 * then plan ID, so the order is stable whatever the array order. */
export function comparePlansNewestFirst(a: PlanRecord, b: PlanRecord): number {
  const byInstant = compareUtc(b.generated_at_utc, a.generated_at_utc);
  if (byInstant !== 0) return byInstant;
  if (a.plan_id === b.plan_id) return b.version - a.version;
  return a.plan_id < b.plan_id ? -1 : 1;
}

/** The most recently generated plan version is the manager's focus. */
export function focusPlan(plans: PlanRecord[]): PlanRecord | null {
  if (plans.length === 0) return null;
  return [...plans].sort(comparePlansNewestFirst)[0];
}

export function latestVersion(plans: PlanRecord[], planId: string): number {
  return plans.filter((plan) => plan.plan_id === planId).reduce((max, plan) => Math.max(max, plan.version), 0);
}

export function previousVersion(plans: PlanRecord[], plan: PlanRecord): PlanRecord | null {
  return plans.find((item) => item.plan_id === plan.plan_id && item.version === plan.version - 1) ?? null;
}

export function confirmationFor(confirmations: ConfirmationRecord[], plan: PlanRecord): ConfirmationRecord | null {
  return confirmations.find((item) => item.plan_id === plan.plan_id && item.plan_version === plan.version) ?? null;
}

export const effectiveStatus = (plan: PlanRecord) => plan.current_status ?? plan.status;

/** Explains, in the manager's terms, why the confirm control is withheld.
 * `null` means the console has no reason to withhold it; the service still
 * decides. */
export function confirmationBlocker(
  plan: PlanRecord,
  plans: PlanRecord[],
  latestInput: InputRecord | null,
): string | null {
  const status = effectiveStatus(plan);
  if (status === "CONFIRMED") return "This version is already confirmed.";
  if (status === "INVALIDATED") {
    return "The operating input changed after this plan was generated. Request a new plan or apply an adjustment under the current input.";
  }
  if (status === "EXPIRED") return "The plan or its input validity has elapsed. Request a new plan.";
  if (status === "INFEASIBLE") {
    return "The selected zone, robot and start time cannot cover the high-demand horizon. Choose an eligible candidate or revise the input.";
  }
  if (status === "MISSING_DATA") {
    return `Required evidence is missing (${plan.missing.join(", ") || "see the plan"}). Complete the operating input first.`;
  }
  if (plan.selection === null) return "The plan has no selectable candidate.";
  if (plan.version !== latestVersion(plans, plan.plan_id)) return "A newer version of this plan exists. Confirm the latest version.";
  if (latestInput === null || plan.input_revision !== latestInput.revision) {
    return `This plan was generated from input revision ${plan.input_revision}; the current revision is ${latestInput?.revision ?? "unknown"}.`;
  }
  return null;
}

/** The latest record per stage: a record superseded by another is hidden
 * behind its correction. Stages without a record stay unknown. */
export function latestOutcomes(outcomes: OutcomeRecord[], confirmationId: string): Partial<Record<Stage, OutcomeRecord>> {
  const mine = outcomes.filter((item) => item.request.confirmation_id === confirmationId);
  const superseded = new Set(mine.map((item) => item.request.supersedes_outcome_id).filter((id): id is string => id !== null));
  const latest: Partial<Record<Stage, OutcomeRecord>> = {};
  for (const item of mine) {
    if (superseded.has(item.outcome_id)) continue;
    const current = latest[item.request.stage];
    if (!current || compareUtc(current.recorded_at_utc, item.recorded_at_utc) <= 0) latest[item.request.stage] = item;
  }
  return latest;
}

export function outcomeHistory(outcomes: OutcomeRecord[], confirmationId: string, stage: Stage): OutcomeRecord[] {
  return outcomes
    .filter((item) => item.request.confirmation_id === confirmationId && item.request.stage === stage)
    .sort((a, b) => compareUtc(a.recorded_at_utc, b.recorded_at_utc));
}
