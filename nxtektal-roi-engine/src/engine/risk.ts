/** F-S01..F-S03 — safety & risk (Expanded Value only, never Core ROI). */

import { Decimal, ONE, ZERO, clamp01 } from "./decimal.js";
import type { TraceCollector } from "./trace.js";
import type { ResolvedRiskItem, ResolvedSystemCosts } from "./resolve.js";

export interface RiskComputation {
  current_expected_risk_cost: Decimal;
  post_expected_risk_cost: Decimal;
  risk_reduction_value: Decimal;
}

export function computeRisk(
  items: ResolvedRiskItem[],
  systemCosts: ResolvedSystemCosts,
  trace: TraceCollector,
): RiskComputation {
  let current = ZERO;
  let post = ZERO;

  for (const k of items) {
    if (k.annual_incident_frequency === null || k.average_cost_per_incident === null) {
      // No history/insurance data ⇒ this incident type contributes nothing (F-S01 rule).
      continue;
    }
    const cost = k.annual_incident_frequency.mul(k.average_cost_per_incident);
    current = current.add(cost);
    const reduction = k.risk_reduction_rate !== null ? clamp01(k.risk_reduction_rate) : ZERO;
    post = post.add(cost.mul(ONE.sub(reduction)));
  }
  // F-S02 — new system risk must be explicitly added.
  post = post.add(systemCosts.new_system_expected_risk_cost);

  trace.add("F-S01", null, [items.length], current);
  trace.add("F-S02", null, [systemCosts.new_system_expected_risk_cost], post);

  // F-S03
  const value = current.sub(post);
  trace.add("F-S03", null, [current, post], value);

  return {
    current_expected_risk_cost: current,
    post_expected_risk_cost: post,
    risk_reduction_value: value,
  };
}
