/** F-B01..F-B04 — ball attrition deltas (per loss cause; negative savings allowed). */

import { Decimal, ONE, ZERO, clamp01 } from "./decimal.js";
import type { TraceCollector, WarningCollector } from "./trace.js";
import type { ResolvedBallLossCause } from "./resolve.js";

export interface BallLossComputation {
  current_ball_loss_cost: Decimal;
  post_lost_balls_total: Decimal;
  ball_replacement_cash_savings: Decimal;
}

/** F-B02 — effective loss reduction per cause. */
export function effectiveLossReduction(
  b: ResolvedBallLossCause,
  trace: TraceCollector,
  warnings: WarningCollector,
): Decimal {
  if (b.loss_reduction_override !== null) {
    const v = clamp01(b.loss_reduction_override);
    trace.add("F-B02", b.loss_cause_id, [b.loss_reduction_override], v);
    return v;
  }
  const factors: [string, Decimal | null][] = [
    ["loss_area_coverage", b.loss_area_coverage],
    ["retrieval_success_rate", b.retrieval_success_rate],
    ["loss_capacity_fit", b.loss_capacity_fit],
    ["loss_uptime", b.loss_uptime],
    ["loss_adoption_rate", b.loss_adoption_rate],
  ];
  // Primary drivers (coverage, retrieval) missing ⇒ no claimable improvement
  // for this cause. Secondary factors missing ⇒ treated as 1 and flagged.
  if (b.loss_area_coverage === null || b.retrieval_success_rate === null) {
    warnings.add(
      "missing_input",
      b.loss_cause_id,
      `loss_area_coverage/retrieval_success_rate missing for cause "${b.loss_cause_id}"; loss reduction treated as 0 (no evidence of improvement).`,
    );
    return ZERO;
  }
  let v = ONE;
  for (const [name, f] of factors) {
    if (f === null) {
      warnings.add("assumed_factor_1", b.loss_cause_id, `${name} missing for "${b.loss_cause_id}"; factor treated as 1 and flagged.`);
      continue;
    }
    v = v.mul(clamp01(f));
  }
  v = clamp01(v);
  trace.add("F-B02", b.loss_cause_id, factors.map(([, f]) => f), v);
  return v;
}

export function computeBallLoss(
  causes: ResolvedBallLossCause[],
  trace: TraceCollector,
  warnings: WarningCollector,
): BallLossComputation {
  let currentCost = ZERO;
  let postBalls = ZERO;
  let savings = ZERO;

  for (const b of causes) {
    if (b.annual_current_lost_balls === null || b.landed_cost_per_ball === null) {
      warnings.add(
        "missing_input",
        b.loss_cause_id,
        `annual_current_lost_balls/landed_cost_per_ball missing for cause "${b.loss_cause_id}"; excluded and reported missing (not zero).`,
      );
      continue;
    }
    // F-B01
    const causeCost = b.annual_current_lost_balls.mul(b.landed_cost_per_ball);
    currentCost = currentCost.add(causeCost);
    trace.add("F-B01", b.loss_cause_id, [b.annual_current_lost_balls, b.landed_cost_per_ball], causeCost);

    // F-B03 — post balls may EXCEED current (new system damage); never clamped.
    const reduction = effectiveLossReduction(b, trace, warnings);
    const post = b.annual_current_lost_balls
      .mul(ONE.sub(reduction))
      .add(b.new_system_damage_balls);
    postBalls = postBalls.add(post);
    trace.add("F-B03", b.loss_cause_id, [b.annual_current_lost_balls, reduction, b.new_system_damage_balls], post);

    // F-B04 — savings can be negative; do NOT clamp (§4, F-B04 rule).
    const causeSavings = b.annual_current_lost_balls.sub(post).mul(b.landed_cost_per_ball);
    savings = savings.add(causeSavings);
    trace.add("F-B04", b.loss_cause_id, [b.annual_current_lost_balls, post, b.landed_cost_per_ball], causeSavings);
  }

  return {
    current_ball_loss_cost: currentCost,
    post_lost_balls_total: postBalls,
    ball_replacement_cash_savings: savings,
  };
}
