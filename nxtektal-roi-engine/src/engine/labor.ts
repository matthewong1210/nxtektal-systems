/** F-L01..F-L12 — labor cost, effective automation, cash realization, capacity. */

import { Decimal, ONE, ZERO, clamp01, safeDiv } from "./decimal.js";
import type { TraceCollector, WarningCollector } from "./trace.js";
import type { ResolvedLaborTask, ResolvedSite } from "./resolve.js";
import { occurrencesYear } from "./time.js";

export interface LaborTaskComputation {
  task_id: string;
  occurrences_year: Decimal | null;
  annual_task_person_hours: Decimal | null;
  loaded_regular_rate: Decimal | null;
  loaded_overtime_rate: Decimal | null;
  current_task_activity_cost: Decimal | null;
  effective_automation_rate: Decimal | null;
  technical_hours_removed: Decimal | null;
  residual_task_person_hours: Decimal | null;
  technical_labor_value_released: Decimal | null;
  cash_labor_savings: Decimal | null;
  cash_saved_hours: Decimal | null;
  released_capacity_hours: Decimal | null;
  released_capacity_value: Decimal | null;
  incomplete: boolean;
}

/** F-L01 — loaded regular rate. Override wins; never stacked with burden (§8.1). */
export function loadedRegularRate(
  task: ResolvedLaborTask,
  trace: TraceCollector,
): Decimal | null {
  if (task.loaded_regular_rate_override !== null) {
    trace.add("F-L01", task.task_id, [task.loaded_regular_rate_override], task.loaded_regular_rate_override);
    return task.loaded_regular_rate_override;
  }
  if (task.base_wage === null) return null;
  const burden = task.payroll_burden_rate ?? ZERO; // optional add-on; absent ⇒ no burden component
  const fixedBenefits = task.fixed_benefits_per_hour ?? ZERO;
  const v = task.base_wage.mul(ONE.add(burden)).add(fixedBenefits);
  trace.add("F-L01", task.task_id, [task.base_wage, burden, fixedBenefits], v);
  return v;
}

/** F-L02 — loaded overtime rate. Fixed benefits are NOT re-multiplied. */
export function loadedOvertimeRate(
  task: ResolvedLaborTask,
  regularRate: Decimal | null,
  trace: TraceCollector,
): Decimal | null {
  if (task.loaded_overtime_rate_override !== null) {
    trace.add("F-L02", task.task_id, [task.loaded_overtime_rate_override], task.loaded_overtime_rate_override);
    return task.loaded_overtime_rate_override;
  }
  if (regularRate === null) return null;
  if (task.base_wage === null) {
    // Overtime premium needs the base wage; without it, only usable when share = 0.
    return task.overtime_share.isZero() ? regularRate : null;
  }
  const premium = task.base_wage
    .mul(task.overtime_multiplier.sub(ONE))
    .mul(ONE.add(task.payroll_tax_rate_on_overtime_premium));
  const v = regularRate.add(premium);
  trace.add(
    "F-L02",
    task.task_id,
    [regularRate, task.base_wage, task.overtime_multiplier, task.payroll_tax_rate_on_overtime_premium],
    v,
  );
  return v;
}

/** F-L05 — effective automation (multiplicative model or pilot override). */
export function effectiveAutomationRate(
  task: ResolvedLaborTask,
  capacityFit: Decimal,
  trace: TraceCollector,
  warnings: WarningCollector,
): Decimal | null {
  if (task.effective_automation_override !== null) {
    const v = clamp01(task.effective_automation_override);
    trace.add("F-L05", task.task_id, [task.effective_automation_override], v);
    return v;
  }
  if (task.coverage_rate === null || task.system_uptime === null) {
    warnings.add(
      "missing_input",
      task.task_id,
      `coverage_rate/system_uptime missing on current task "${task.task_id}"; effective automation cannot be computed (missing ≠ 0).`,
    );
    return null;
  }
  // workflow_success_rate / adoption_rate are "recommended": absent factors do
  // not participate in the product but are flagged (see docs/AMBIGUITIES.md F-L05).
  let workflow = task.workflow_success_rate;
  let adoption = task.adoption_rate;
  if (workflow === null) {
    workflow = ONE;
    warnings.add("assumed_factor_1", task.task_id, `workflow_success_rate missing on "${task.task_id}"; factor treated as 1 and flagged.`);
  }
  if (adoption === null) {
    adoption = ONE;
    warnings.add("assumed_factor_1", task.task_id, `adoption_rate missing on "${task.task_id}"; factor treated as 1 and flagged.`);
  }
  const fit = task.capacity_fit_override !== null ? clamp01(task.capacity_fit_override) : capacityFit;
  const v = clamp01(
    clamp01(task.coverage_rate)
      .mul(clamp01(task.system_uptime))
      .mul(clamp01(fit))
      .mul(clamp01(workflow))
      .mul(clamp01(adoption)),
  );
  trace.add(
    "F-L05",
    task.task_id,
    [task.coverage_rate, task.system_uptime, fit, workflow, adoption],
    v,
  );
  return v;
}

/** Compute a full current_task or new_system_task per F-L03..F-L10. */
export function computeLaborTask(
  task: ResolvedLaborTask,
  site: ResolvedSite,
  capacityFit: Decimal,
  trace: TraceCollector,
  warnings: WarningCollector,
): LaborTaskComputation {
  const incompleteResult = (occ: Decimal | null): LaborTaskComputation => ({
    task_id: task.task_id,
    occurrences_year: occ,
    annual_task_person_hours: null,
    loaded_regular_rate: null,
    loaded_overtime_rate: null,
    current_task_activity_cost: null,
    effective_automation_rate: null,
    technical_hours_removed: null,
    residual_task_person_hours: null,
    technical_labor_value_released: null,
    cash_labor_savings: null,
    cash_saved_hours: null,
    released_capacity_hours: null,
    released_capacity_value: null,
    incomplete: true,
  });

  const occ = occurrencesYear(task, site, trace, warnings);
  if (occ === null || task.duration_hours_per_occurrence === null || task.headcount === null) {
    if (occ !== null) {
      warnings.add("incomplete_task", task.task_id, `Task "${task.task_id}" is missing duration/headcount; marked incomplete.`);
    }
    return incompleteResult(occ);
  }

  // F-L03 — annual person-hours.
  const annualHours = occ.mul(task.duration_hours_per_occurrence).mul(task.headcount);
  trace.add("F-L03", task.task_id, [occ, task.duration_hours_per_occurrence, task.headcount], annualHours);

  const regularRate = loadedRegularRate(task, trace);
  if (regularRate === null) {
    warnings.add(
      "incomplete_task",
      task.task_id,
      `Task "${task.task_id}" has no usable loaded regular rate; monetary figures are null (rate-independent hours are still reported).`,
    );
    if (task.task_type === "new_system_task") {
      return { ...incompleteResult(occ), annual_task_person_hours: annualHours };
    }
    // Current task: hours-based metrics (F-L05/F-L06) do not need a wage.
    const eaNoRate = effectiveAutomationRate(task, capacityFit, trace, warnings);
    const removedNoRate = eaNoRate === null ? null : annualHours.mul(eaNoRate);
    if (removedNoRate !== null) {
      trace.add("F-L06", task.task_id, [annualHours, eaNoRate], removedNoRate);
    }
    return {
      ...incompleteResult(occ),
      annual_task_person_hours: annualHours,
      effective_automation_rate: eaNoRate,
      technical_hours_removed: removedNoRate,
      residual_task_person_hours: removedNoRate === null ? null : annualHours.sub(removedNoRate),
    };
  }
  const overtimeShare = clamp01(task.overtime_share);
  const overtimeRate = loadedOvertimeRate(task, regularRate, trace);

  // F-L04 — activity cost.
  const overtimeHours = annualHours.mul(overtimeShare);
  const regularHours = annualHours.sub(overtimeHours);
  const otRateForCost = overtimeRate ?? regularRate;
  if (overtimeRate === null && !overtimeShare.isZero()) {
    warnings.add("missing_input", task.task_id, `Overtime rate unavailable for "${task.task_id}"; overtime hours priced at the regular rate and flagged.`);
  }
  const activityCost = regularHours.mul(regularRate).add(overtimeHours.mul(otRateForCost));
  trace.add("F-L04", task.task_id, [regularHours, regularRate, overtimeHours, otRateForCost], activityCost);

  if (task.task_type === "new_system_task") {
    // New-system tasks only contribute cost (F-L11 sums activity costs).
    return {
      task_id: task.task_id,
      occurrences_year: occ,
      annual_task_person_hours: annualHours,
      loaded_regular_rate: regularRate,
      loaded_overtime_rate: overtimeRate,
      current_task_activity_cost: activityCost,
      effective_automation_rate: null,
      technical_hours_removed: null,
      residual_task_person_hours: null,
      technical_labor_value_released: null,
      cash_labor_savings: null,
      cash_saved_hours: null,
      released_capacity_hours: null,
      released_capacity_value: null,
      incomplete: false,
    };
  }

  // F-L05 — effective automation.
  const ea = effectiveAutomationRate(task, capacityFit, trace, warnings);
  if (ea === null) {
    return {
      ...incompleteResult(occ),
      annual_task_person_hours: annualHours,
      loaded_regular_rate: regularRate,
      loaded_overtime_rate: overtimeRate,
      current_task_activity_cost: activityCost,
    };
  }

  // F-L06 — technical & residual hours (rate already clamped: no negatives).
  const removed = annualHours.mul(ea);
  const residual = annualHours.sub(removed);
  trace.add("F-L06", task.task_id, [annualHours, ea], removed);

  // F-L07 — technical labor value released (guard the zero-hours division).
  let valueReleased = ZERO;
  if (!annualHours.isZero()) {
    const weightedRate = activityCost.div(annualHours);
    valueReleased = removed.mul(weightedRate);
  }
  trace.add("F-L07", task.task_id, [removed, annualHours.isZero() ? 0 : Number(activityCost.div(annualHours))], valueReleased);

  // F-L08 / F-L09 — mutually exclusive cash realization (§8.1).
  let cashSavings: Decimal | null = null;
  let cashSavedHours: Decimal = ZERO;
  if (task.cash_realization_method === "overtime_first") {
    if (task.regular_cash_realization_factor === null) {
      warnings.add("missing_input", task.task_id, `overtime_first requires regular_cash_realization_factor on "${task.task_id}".`);
    } else {
      const avoidableOt = task.avoidable_overtime_hours ?? ZERO;
      const otRate = overtimeRate ?? regularRate;
      const avoidableOtSaved = Decimal.min(removed, avoidableOt);
      const remainingRemoved = removed.sub(avoidableOtSaved);
      const regularCashSaved = remainingRemoved.mul(clamp01(task.regular_cash_realization_factor));
      cashSavings = avoidableOtSaved.mul(otRate).add(regularCashSaved.mul(regularRate));
      cashSavedHours = avoidableOtSaved.add(regularCashSaved);
      trace.add("F-L08", task.task_id, [avoidableOtSaved, otRate, regularCashSaved, regularRate], cashSavings);
    }
  } else if (task.cash_realization_method === "simple_factor") {
    if (task.simple_cash_realization_factor === null) {
      warnings.add("missing_input", task.task_id, `simple_factor requires simple_cash_realization_factor on "${task.task_id}".`);
    } else {
      const f = clamp01(task.simple_cash_realization_factor);
      cashSavings = valueReleased.mul(f);
      cashSavedHours = removed.mul(f);
      trace.add("F-L09", task.task_id, [valueReleased, f], cashSavings);
    }
  } else {
    warnings.add(
      "missing_input",
      task.task_id,
      `cash_realization_method missing on current task "${task.task_id}" (required — §5.2); cash savings cannot be computed. Technical release is NOT cash (§1.3).`,
    );
  }

  // F-L10 — released capacity hours & value (Expanded Value only).
  const releasedHours = Decimal.max(ZERO, removed.sub(cashSavedHours));
  let releasedValue = ZERO;
  if (task.shadow_value_per_hour !== null && task.redeployment_utilization !== null) {
    releasedValue = releasedHours
      .mul(task.shadow_value_per_hour)
      .mul(clamp01(task.redeployment_utilization));
  }
  trace.add("F-L10", task.task_id, [releasedHours, task.shadow_value_per_hour, task.redeployment_utilization], releasedValue);

  return {
    task_id: task.task_id,
    occurrences_year: occ,
    annual_task_person_hours: annualHours,
    loaded_regular_rate: regularRate,
    loaded_overtime_rate: overtimeRate,
    current_task_activity_cost: activityCost,
    effective_automation_rate: ea,
    technical_hours_removed: removed,
    residual_task_person_hours: residual,
    technical_labor_value_released: valueReleased,
    cash_labor_savings: cashSavings,
    cash_saved_hours: cashSavedHours,
    released_capacity_hours: releasedHours,
    released_capacity_value: releasedValue,
    // Cash realization not computable ⇒ the task's economics are incomplete (§8.1).
    incomplete: cashSavings === null,
  };
}

/** F-L12 — FTE conversions (display only; never re-added to savings — §8.1). */
export function fteConversions(
  totalTechnicalHoursRemoved: Decimal,
  totalCashSavedHours: Decimal,
  paidHoursPerFte: Decimal,
  trace: TraceCollector,
): { technical_fte_released: Decimal | null; cash_fte_avoided: Decimal | null } {
  const tech = safeDiv(totalTechnicalHoursRemoved, paidHoursPerFte);
  const cash = safeDiv(totalCashSavedHours, paidHoursPerFte);
  trace.add("F-L12", "technical_fte_released", [totalTechnicalHoursRemoved, paidHoursPerFte], tech);
  trace.add("F-L12", "cash_fte_avoided", [totalCashSavedHours, paidHoursPerFte], cash);
  return { technical_fte_released: tech, cash_fte_avoided: cash };
}
