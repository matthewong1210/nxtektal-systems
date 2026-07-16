/** Section 8 cross-field validation — hard errors and warnings. */

import type { WarningCollector } from "./trace.js";
import type { ResolvedSnapshot, ResolvedLaborTask } from "./resolve.js";

export class EngineValidationError extends Error {}

export const SUPPORTED_MODEL_VERSION = "1.0";

/**
 * Hard rules that must block calculation, plus warnings.
 * Returns the current tasks that actually count (overlap groups deduplicated).
 */
export function validateResolved(
  resolved: ResolvedSnapshot,
  warnings: WarningCollector,
): { countedCurrentTasks: ResolvedLaborTask[] } {
  if (resolved.model_version !== SUPPORTED_MODEL_VERSION) {
    throw new EngineValidationError(
      `model_version "${resolved.model_version}" is not supported by this engine build (locked to ${SUPPORTED_MODEL_VERSION}). ` +
        `Formula changes require a new model_version — never silent replacement (§15.1).`,
    );
  }

  // §8.1 — the same person-hours may belong to only one atomic task.
  const groups = new Map<string, ResolvedLaborTask[]>();
  for (const t of resolved.current_tasks) {
    if (t.overlap_group !== null) {
      const list = groups.get(t.overlap_group) ?? [];
      list.push(t);
      groups.set(t.overlap_group, list);
    }
  }
  const excluded = new Set<string>();
  for (const [group, tasks] of groups) {
    if (tasks.length < 2) continue;
    const primaries = tasks.filter((t) => t.overlap_primary);
    if (primaries.length !== 1) {
      throw new EngineValidationError(
        `overlap_group "${group}" contains ${tasks.length} current tasks (${tasks
          .map((t) => t.task_id)
          .join(", ")}) but ${primaries.length} primary record(s). A human must choose exactly one ` +
          `primary record so the same person-hours are not double counted (§8.1).`,
      );
    }
    for (const t of tasks) {
      if (!t.overlap_primary) {
        excluded.add(t.task_id);
        warnings.add(
          "missing_input",
          t.task_id,
          `Task "${t.task_id}" excluded from totals: overlap_group "${group}" is counted through primary record "${primaries[0]!.task_id}" (§8.1).`,
        );
      }
    }
  }

  // §8.1 — cash realization method must exist and its factor must match.
  for (const t of resolved.current_tasks) {
    if (excluded.has(t.task_id)) continue;
    if (
      t.cash_realization_method === "overtime_first" &&
      t.simple_cash_realization_factor !== null &&
      t.regular_cash_realization_factor === null
    ) {
      throw new EngineValidationError(
        `Task "${t.task_id}": cash_realization_method is overtime_first but only simple_cash_realization_factor was supplied. ` +
          `F-L08 and F-L09 are mutually exclusive — supply regular_cash_realization_factor or switch the method (§8.1).`,
      );
    }
    if (
      t.cash_realization_method === "simple_factor" &&
      t.regular_cash_realization_factor !== null &&
      t.simple_cash_realization_factor === null
    ) {
      throw new EngineValidationError(
        `Task "${t.task_id}": cash_realization_method is simple_factor but only regular_cash_realization_factor was supplied (§8.1).`,
      );
    }
  }

  return {
    countedCurrentTasks: resolved.current_tasks.filter((t) => !excluded.has(t.task_id)),
  };
}
