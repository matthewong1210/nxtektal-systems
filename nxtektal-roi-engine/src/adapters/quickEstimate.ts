/**
 * Quick Estimate adapter — spec §9. NOT a second formula set: it maps ~10
 * field-side inputs onto a Full assessment snapshot and calls the same engine.
 */

import type { AssessmentSnapshot, PricingInputs } from "../types/inputs.js";
import type { EvidenceValue } from "../types/evidence.js";

export type LaborDisposition =
  | "reduce_overtime"
  | "cancel_shifts"
  | "avoid_hiring"
  | "redeploy"
  | "unknown";

export interface QuickEstimateInputs {
  assessment_id: string;
  site_id: string;
  assessment_date: string;
  currency: string;

  /** Loaded rate directly, or base wage + burden. */
  loaded_regular_rate_override?: number;
  base_wage?: number;
  payroll_burden_rate?: number;

  operating_days_per_year: number;
  operating_hours_per_day: number;

  /** Regular collection: cycles/day or interval hours. */
  regular_collection_cycles_per_day?: number;
  regular_collection_interval_hours?: number;
  regular_collection_duration_hours: number;
  regular_collection_headcount: number;

  /** Special recovery (per week). */
  special_recovery_hours_per_week: number;
  special_recovery_headcount: number;

  /** Optional unloading task. */
  unloading_cycles_per_day?: number;
  unloading_duration_hours?: number;
  unloading_headcount?: number;

  /** Aggregate current equipment cash cost (monthly or annual). */
  equipment_monthly_cash_cost?: number;
  equipment_annual_cash_cost?: number;

  /** What happens to freed labor (§9.2 mapping). */
  labor_disposition: LaborDisposition;
  avoidable_overtime_hours_per_year?: number;
  /**
   * Cash-realization factor for cancel_shifts / avoid_hiring, supplied by the
   * manager. No product default exists (§16 rule 11): when omitted, the
   * conservative value 0 is used — nothing counts until confirmed (§9.2).
   */
  cash_realization_factor?: number;

  /** NXTektal scenario factors & offer. */
  coverage_rate: number;
  system_uptime: number;
  workflow_success_rate?: number;
  adoption_rate?: number;
  special_recovery_coverage_rate?: number;
  pricing?: PricingInputs;
}

const estimated = (value: number): EvidenceValue => ({
  value_base: value,
  input_status: "estimated_allowed",
  source_type: "customer_reported",
});

/**
 * §9.2 labor-disposition mapping:
 * - redeploy       → regular cash realization 0 (freed hours are capacity only)
 * - reduce_overtime→ overtime_first with avoidable overtime hours
 * - cancel_shifts / avoid_hiring → caller-supplied cash_realization_factor
 *   (no product default may be invented — §16 rule 11); absent ⇒ 0, nothing
 *   counts until confirmed.
 * - unknown        → 0 (Conservative counts nothing — §9.2)
 */
function cashRealization(q: QuickEstimateInputs): {
  method: "overtime_first" | "simple_factor";
  regular_factor?: EvidenceValue | number;
  simple_factor?: EvidenceValue | number;
  avoidable_overtime_hours?: number;
} {
  switch (q.labor_disposition) {
    case "reduce_overtime":
      return {
        method: "overtime_first",
        regular_factor: estimated(0),
        avoidable_overtime_hours: q.avoidable_overtime_hours_per_year ?? 0,
      };
    case "cancel_shifts":
    case "avoid_hiring":
      return {
        method: "simple_factor",
        simple_factor: estimated(q.cash_realization_factor ?? 0),
      };
    case "redeploy":
      return { method: "simple_factor", simple_factor: 0 };
    case "unknown":
      return { method: "simple_factor", simple_factor: estimated(0) };
  }
}

export function buildQuickEstimateSnapshot(q: QuickEstimateInputs): AssessmentSnapshot {
  const hasCycles = q.regular_collection_cycles_per_day !== undefined;
  const hasInterval = q.regular_collection_interval_hours !== undefined;
  if (hasCycles === hasInterval) {
    throw new Error(
      "Quick Estimate requires exactly one of regular_collection_cycles_per_day or " +
        "regular_collection_interval_hours (§9.1); " +
        (hasCycles ? "both were supplied." : "neither was supplied."),
    );
  }
  const wage = cashRealization(q);
  const rateFields =
    q.loaded_regular_rate_override !== undefined
      ? { loaded_regular_rate_override: q.loaded_regular_rate_override }
      : { base_wage: q.base_wage, payroll_burden_rate: q.payroll_burden_rate };

  const commonAutomation = {
    coverage_rate: q.coverage_rate,
    system_uptime: q.system_uptime,
    workflow_success_rate: q.workflow_success_rate ?? null,
    adoption_rate: q.adoption_rate ?? null,
  };
  const commonCash = {
    cash_realization_method: wage.method,
    regular_cash_realization_factor: wage.regular_factor ?? null,
    simple_cash_realization_factor: wage.simple_factor ?? null,
    avoidable_overtime_hours: wage.avoidable_overtime_hours ?? null,
  };

  const snapshot: AssessmentSnapshot = {
    model_version: "1.0",
    assessment_id: q.assessment_id,
    mode: "quick_estimate",
    site: {
      site_id: q.site_id,
      assessment_date: q.assessment_date,
      currency: q.currency,
      operating_days_per_year: q.operating_days_per_year,
      operating_hours_per_day: q.operating_hours_per_day,
      active_weeks_per_year: 52, // Quick default (§5.1)
      tax_treatment: "cash_inclusive",
    },
    labor_tasks: [
      {
        task_id: "regular_collection",
        task_type: "current_task",
        ...(hasCycles
          ? {
              frequency_basis: "per_day" as const,
              frequency_value: q.regular_collection_cycles_per_day!,
            }
          : {
              frequency_basis: "interval_hours" as const,
              frequency_value: q.regular_collection_interval_hours!,
            }),
        duration_hours_per_occurrence: q.regular_collection_duration_hours,
        headcount: q.regular_collection_headcount,
        ...rateFields,
        ...commonAutomation,
        ...commonCash,
      },
      {
        task_id: "special_recovery",
        task_type: "current_task",
        frequency_basis: "per_week",
        frequency_value: 1,
        duration_hours_per_occurrence: q.special_recovery_hours_per_week,
        headcount: q.special_recovery_headcount,
        ...rateFields,
        ...commonAutomation,
        coverage_rate: q.special_recovery_coverage_rate ?? q.coverage_rate,
        ...commonCash,
      },
    ],
    pricing: q.pricing ?? {},
  };

  if (
    q.unloading_cycles_per_day !== undefined &&
    q.unloading_duration_hours !== undefined
  ) {
    snapshot.labor_tasks.push({
      task_id: "unloading",
      task_type: "current_task",
      frequency_basis: "per_day",
      frequency_value: q.unloading_cycles_per_day,
      duration_hours_per_occurrence: q.unloading_duration_hours,
      headcount: q.unloading_headcount ?? 1,
      ...rateFields,
      ...commonAutomation,
      ...commonCash,
    });
  }

  // Aggregate equipment cost → single estimated component, split later in Full mode (§9.2).
  const annualEquip =
    q.equipment_annual_cash_cost ??
    (q.equipment_monthly_cash_cost !== undefined ? q.equipment_monthly_cash_cost * 12 : undefined);
  if (annualEquip !== undefined) {
    snapshot.equipment_components = [
      {
        asset_id: "aggregate_equipment",
        cost_component_id: "aggregate",
        component_type: "variable",
        annual_current_cash_cost: estimated(annualEquip),
        usage_reduction_rate: estimated(0),
      },
    ];
  }

  return snapshot;
}
