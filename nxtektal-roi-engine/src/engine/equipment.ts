/** F-E01..F-E06 — existing equipment deltas (future cash only; no sunk cost, no depreciation). */

import { Decimal, ZERO, clamp01 } from "./decimal.js";
import type { TraceCollector, WarningCollector } from "./trace.js";
import type { ResolvedEquipmentComponent } from "./resolve.js";

export interface EquipmentComputation {
  current_equipment_cash_cost: Decimal;
  equipment_cash_savings: Decimal;
  post_existing_equipment_cash_cost: Decimal;
  /** F-E05 — avoided replacement capex in analysis year t (once, in the planned year). */
  avoidedReplacementCapex(t: number): Decimal;
  /** F-E06 — salvage proceeds in analysis year t (once). */
  salvageCashFlow(t: number): Decimal;
}

/** F-E02 — avoidable fraction per component. */
export function avoidableFraction(
  c: ResolvedEquipmentComponent,
  trace: TraceCollector,
  warnings: WarningCollector,
): Decimal {
  const id = `${c.asset_id}.${c.cost_component_id}`;
  if (c.avoidability_override !== null) {
    const v = clamp01(c.avoidability_override);
    trace.add("F-E02", id, [c.avoidability_override], v);
    return v;
  }
  if (c.component_type === "variable") {
    if (c.usage_reduction_rate === null) {
      warnings.add("missing_input", id, `usage_reduction_rate is required for variable component "${id}"; savings treated as 0 until provided (missing ≠ 0 confirmed).`);
      return ZERO;
    }
    const v = clamp01(c.usage_reduction_rate);
    trace.add("F-E02", id, [c.usage_reduction_rate], v);
    return v;
  }
  // fixed_contractual / periodic / replacement_capex: retirement × contractual avoidability.
  if (c.retirement_fraction === null || c.contractual_avoidability_rate === null) {
    warnings.add(
      "avoidability_assumed_0",
      id,
      `retirement_fraction/contractual_avoidability_rate missing for fixed component "${id}"; avoidable fraction treated as 0 (no evidence the payment can stop — §8.3).`,
    );
    return ZERO;
  }
  const v = clamp01(c.retirement_fraction.mul(c.contractual_avoidability_rate));
  trace.add("F-E02", id, [c.retirement_fraction, c.contractual_avoidability_rate], v);
  return v;
}

export function computeEquipment(
  components: ResolvedEquipmentComponent[],
  trace: TraceCollector,
  warnings: WarningCollector,
): EquipmentComputation {
  let currentCost = ZERO;
  let savings = ZERO;
  let postCost = ZERO;

  for (const c of components) {
    const id = `${c.asset_id}.${c.cost_component_id}`;
    // F-E01 — only future cash outlays; sunk purchase price / book depreciation excluded by the input contract.
    if (c.annual_current_cash_cost === null) {
      warnings.add(
        "missing_input",
        id,
        `annual_current_cash_cost for "${id}" is UNKNOWN — excluded from totals and reported missing; this is NOT a confirmed zero (§4, §14 "Missing vs zero").`,
      );
      continue;
    }
    currentCost = currentCost.add(c.annual_current_cash_cost);
    const frac = avoidableFraction(c, trace, warnings);
    const componentSavings = c.annual_current_cash_cost.mul(frac);
    savings = savings.add(componentSavings);
    postCost = postCost.add(c.annual_current_cash_cost.mul(new Decimal(1).sub(frac)));
    trace.add("F-E03", id, [c.annual_current_cash_cost, frac], componentSavings);
  }

  trace.add("F-E01", null, [components.length], currentCost);
  trace.add("F-E04", null, [currentCost, savings], postCost);

  return {
    current_equipment_cash_cost: currentCost,
    equipment_cash_savings: savings,
    post_existing_equipment_cash_cost: postCost,
    avoidedReplacementCapex(t: number): Decimal {
      let total = ZERO;
      for (const c of components) {
        if (
          c.replacement_capex !== null &&
          c.replacement_year !== null &&
          c.replacement_year === t &&
          c.replacement_avoidance_rate !== null
        ) {
          total = total.add(c.replacement_capex.mul(clamp01(c.replacement_avoidance_rate)));
        }
      }
      if (!total.isZero()) trace.add("F-E05", `year_${t}`, [t], total);
      return total;
    },
    salvageCashFlow(t: number): Decimal {
      let total = ZERO;
      for (const c of components) {
        if (c.salvage_value !== null && c.salvage_year !== null && c.salvage_year === t) {
          total = total.add(c.salvage_value);
        }
      }
      if (!total.isZero()) trace.add("F-E06", `year_${t}`, [t], total);
      return total;
    },
  };
}
