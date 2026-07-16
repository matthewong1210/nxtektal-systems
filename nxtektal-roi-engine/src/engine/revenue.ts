/** F-R01..F-R08 — stockout, refund and revenue recovery (contribution margin only). */

import { Decimal, ONE, ZERO, clamp01 } from "./decimal.js";
import type { TraceCollector, WarningCollector } from "./trace.js";
import type { ResolvedRevenueGroup } from "./resolve.js";

export class RevenueDedupError extends Error {}

export interface RevenueComputation {
  current_unmade_sale_margin_loss: Decimal;
  current_refund_cash_loss: Decimal;
  current_service_credit_cost: Decimal;
  recovered_contribution_margin: Decimal;
  refund_and_credit_cash_savings: Decimal;
  incremental_sales_margin: Decimal;
  total_revenue_recovery_value: Decimal;
}

/** F-R02 — contribution margin per basket (never gross revenue — §8.4). */
export function contributionMarginPerBasket(
  g: ResolvedRevenueGroup,
  trace: TraceCollector,
  warnings: WarningCollector,
): Decimal | null {
  if (g.price_per_basket === null) return null;
  if (g.contribution_margin_rate_override !== null) {
    const v = g.price_per_basket.mul(clamp01(g.contribution_margin_rate_override));
    trace.add("F-R02", g.revenue_event_group_id, [g.price_per_basket, g.contribution_margin_rate_override], v);
    return v;
  }
  if (g.variable_cost_per_basket === null) {
    warnings.add(
      "missing_input",
      g.revenue_event_group_id,
      `variable_cost_per_basket missing for group "${g.revenue_event_group_id}" and no margin-rate override; margin cannot be computed (gross revenue must NOT be used).`,
    );
    return null;
  }
  const v = g.price_per_basket.sub(g.variable_cost_per_basket);
  trace.add("F-R02", g.revenue_event_group_id, [g.price_per_basket, g.variable_cost_per_basket], v);
  return v;
}

/** F-R05 — effective stockout reduction. */
export function effectiveStockoutReduction(
  g: ResolvedRevenueGroup,
  trace: TraceCollector,
  warnings: WarningCollector,
): Decimal {
  if (g.stockout_reduction_override !== null) {
    const v = clamp01(g.stockout_reduction_override);
    trace.add("F-R05", g.revenue_event_group_id, [g.stockout_reduction_override], v);
    return v;
  }
  const factors: [string, Decimal | null][] = [
    ["collection_reliability_factor", g.collection_reliability_factor],
    ["inventory_visibility_factor", g.inventory_visibility_factor],
    ["operational_response_factor", g.operational_response_factor],
  ];
  if (factors.every(([, f]) => f === null)) {
    warnings.add(
      "missing_input",
      g.revenue_event_group_id,
      `No stockout-reduction factors for group "${g.revenue_event_group_id}"; reduction treated as 0 (no evidence).`,
    );
    return ZERO;
  }
  let v = ONE;
  for (const [name, f] of factors) {
    if (f === null) {
      warnings.add("assumed_factor_1", g.revenue_event_group_id, `${name} missing for "${g.revenue_event_group_id}"; factor treated as 1 and flagged.`);
      continue;
    }
    v = v.mul(clamp01(f));
  }
  v = clamp01(v);
  trace.add("F-R05", g.revenue_event_group_id, factors.map(([, f]) => f), v);
  return v;
}

export function computeRevenue(
  groups: ResolvedRevenueGroup[],
  trace: TraceCollector,
  warnings: WarningCollector,
): RevenueComputation {
  let unmadeMarginLoss = ZERO;
  let refundLoss = ZERO;
  let creditCost = ZERO;
  let recoveredMargin = ZERO;
  let refundSavings = ZERO;
  let incrementalMargin = ZERO;

  for (const g of groups) {
    const id = g.revenue_event_group_id;

    // F-R01 — annual missed baskets.
    let missedBaskets: Decimal | null = null;
    if (g.annual_missed_baskets_override !== null) {
      missedBaskets = g.annual_missed_baskets_override;
      trace.add("F-R01", id, [g.annual_missed_baskets_override], missedBaskets);
    } else if (
      g.stockout_events_per_year !== null &&
      g.affected_customers_per_event !== null &&
      g.missed_baskets_per_customer !== null
    ) {
      missedBaskets = g.stockout_events_per_year
        .mul(g.affected_customers_per_event)
        .mul(g.missed_baskets_per_customer);
      trace.add(
        "F-R01",
        id,
        [g.stockout_events_per_year, g.affected_customers_per_event, g.missed_baskets_per_customer],
        missedBaskets,
      );
    }

    const hasMissedSale = missedBaskets !== null && !missedBaskets.isZero();
    const hasRefund =
      (g.annual_refund_count !== null && !g.annual_refund_count.isZero()) ||
      (g.annual_service_credit_count !== null && !g.annual_service_credit_count.isZero());

    // §8.4 / §14 "Revenue dedup": same event group marked as BOTH missed sale
    // and refund must be blocked until a human chooses.
    if (hasMissedSale && hasRefund && g.dedup_resolution === null) {
      throw new RevenueDedupError(
        `Revenue event group "${id}" is marked as both a missed sale and a refund. ` +
          `The same transaction must not be counted twice — set dedup_resolution to ` +
          `"missed_sale", "refund", or "both_verified_distinct" (§8.4).`,
      );
    }
    const countMissed =
      hasMissedSale && (!hasRefund || g.dedup_resolution === "missed_sale" || g.dedup_resolution === "both_verified_distinct");
    const countRefund =
      hasRefund && (!hasMissedSale || g.dedup_resolution === "refund" || g.dedup_resolution === "both_verified_distinct");

    const reduction = effectiveStockoutReduction(g, trace, warnings);
    const margin = contributionMarginPerBasket(g, trace, warnings);

    if (countMissed && missedBaskets !== null && margin !== null) {
      // F-R03 — current unmade-sale margin loss (opportunity, not direct cost).
      const loss = missedBaskets.mul(margin);
      unmadeMarginLoss = unmadeMarginLoss.add(loss);
      trace.add("F-R03", id, [missedBaskets, margin], loss);

      // F-R06 — recovered contribution margin.
      const recovered = loss.mul(reduction);
      recoveredMargin = recoveredMargin.add(recovered);
      trace.add("F-R06", id, [loss, reduction], recovered);
    }

    if (countRefund) {
      // F-R04 — refund & service credit cash losses.
      const refunds =
        g.annual_refund_count !== null && g.average_net_refund_cost !== null
          ? g.annual_refund_count.mul(g.average_net_refund_cost)
          : ZERO;
      const credits =
        g.annual_service_credit_count !== null && g.average_service_credit_cost !== null
          ? g.annual_service_credit_count.mul(g.average_service_credit_cost)
          : ZERO;
      refundLoss = refundLoss.add(refunds);
      creditCost = creditCost.add(credits);
      trace.add("F-R04", id, [g.annual_refund_count, g.average_net_refund_cost, g.annual_service_credit_count, g.average_service_credit_cost], refunds.add(credits));

      // F-R06 — refund/credit savings (direct cash savings bucket).
      const saved = refunds.add(credits).mul(reduction);
      refundSavings = refundSavings.add(saved);
      trace.add("F-R06", id, [refunds.add(credits), reduction], saved);
    }

    // F-R07 — incremental non-overlapping sales margin.
    if (g.incremental_baskets_not_already_counted !== null && margin !== null) {
      const inc = g.incremental_baskets_not_already_counted.mul(margin);
      incrementalMargin = incrementalMargin.add(inc);
      trace.add("F-R07", id, [g.incremental_baskets_not_already_counted, margin], inc);
    }
  }

  // F-R08 — total revenue recovery (margin only; refunds live in direct savings).
  const total = recoveredMargin.add(incrementalMargin);
  trace.add("F-R08", null, [recoveredMargin, incrementalMargin], total);

  return {
    current_unmade_sale_margin_loss: unmadeMarginLoss,
    current_refund_cash_loss: refundLoss,
    current_service_credit_cost: creditCost,
    recovered_contribution_margin: recoveredMargin,
    refund_and_credit_cash_savings: refundSavings,
    incremental_sales_margin: incrementalMargin,
    total_revenue_recovery_value: total,
  };
}
