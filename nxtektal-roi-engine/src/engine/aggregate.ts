/** F-A01..F-A12 — aggregation to Core / Expanded value and unit economics. */

import { Decimal, ZERO, safeDiv } from "./decimal.js";
import type { TraceCollector } from "./trace.js";

export interface AggregateInputsBundle {
  current_labor_activity_cost_total: Decimal;
  current_equipment_cash_cost: Decimal;
  current_ball_loss_cost: Decimal;
  current_refund_cash_loss: Decimal;
  current_service_credit_cost: Decimal;
  current_other_direct_cash_cost: Decimal;
  cash_labor_savings_total: Decimal;
  equipment_cash_savings: Decimal;
  ball_replacement_cash_savings: Decimal;
  refund_and_credit_cash_savings: Decimal;
  other_direct_cash_savings: Decimal;
  total_revenue_recovery_value: Decimal;
  customer_incremental_operating_cost: Decimal;
  annual_vendor_recurring_fee: Decimal;
  risk_reduction_value: Decimal;
  released_capacity_value_total: Decimal;
  include_risk_in_expanded_value: boolean;
  operating_days_per_year: Decimal | null;
  annual_balls_processed: Decimal | null;
  annual_baskets_sold: Decimal | null;
}

export interface AggregateComputation {
  current_direct_operating_cash_cost: Decimal;
  direct_gross_cash_savings: Decimal;
  post_direct_operating_cash_cost: Decimal;
  net_direct_cash_savings_after_vendor: Decimal;
  core_value_pre_vendor_fee: Decimal;
  core_annual_customer_net_benefit: Decimal;
  expanded_annual_customer_value: Decimal;
  direct_cost_reduction_rate: Decimal | null;
  current_daily_direct_cost: Decimal | null;
  post_daily_direct_cost: Decimal | null;
  current_monthly_direct_cost: Decimal;
  post_monthly_direct_cost: Decimal;
  monthly_core_net_benefit: Decimal;
  current_cost_per_1000_balls: Decimal | null;
  post_cost_per_1000_balls: Decimal | null;
  net_benefit_per_1000_balls: Decimal | null;
  current_cost_per_basket: Decimal | null;
  post_cost_per_basket: Decimal | null;
  vendor_value_capture_rate: Decimal | null;
  customer_value_retention_rate: Decimal | null;
}

export function computeAggregate(
  b: AggregateInputsBundle,
  trace: TraceCollector,
): AggregateComputation {
  // F-A01 — current direct operating cash cost (no unmade-margin, risk, depreciation, sunk cost).
  const currentCost = b.current_labor_activity_cost_total
    .add(b.current_equipment_cash_cost)
    .add(b.current_ball_loss_cost)
    .add(b.current_refund_cash_loss)
    .add(b.current_service_credit_cost)
    .add(b.current_other_direct_cash_cost);
  trace.add("F-A01", null, [b.current_labor_activity_cost_total, b.current_equipment_cash_cost, b.current_ball_loss_cost, b.current_refund_cash_loss, b.current_service_credit_cost, b.current_other_direct_cash_cost], currentCost);

  // F-A02 — direct gross cash savings.
  const grossSavings = b.cash_labor_savings_total
    .add(b.equipment_cash_savings)
    .add(b.ball_replacement_cash_savings)
    .add(b.refund_and_credit_cash_savings)
    .add(b.other_direct_cash_savings);
  trace.add("F-A02", null, [b.cash_labor_savings_total, b.equipment_cash_savings, b.ball_replacement_cash_savings, b.refund_and_credit_cash_savings, b.other_direct_cash_savings], grossSavings);

  // F-A03 — post direct operating cash cost.
  const postCost = currentCost
    .sub(grossSavings)
    .add(b.customer_incremental_operating_cost)
    .add(b.annual_vendor_recurring_fee);
  trace.add("F-A03", null, [currentCost, grossSavings, b.customer_incremental_operating_cost, b.annual_vendor_recurring_fee], postCost);

  // F-A04 — net direct cash savings after vendor (excludes revenue recovery).
  const netDirect = currentCost.sub(postCost);
  trace.add("F-A04", null, [currentCost, postCost], netDirect);

  // F-A05 — core value pre vendor fee (price-cap & performance-fee base).
  const preVendor = grossSavings
    .add(b.total_revenue_recovery_value)
    .sub(b.customer_incremental_operating_cost);
  trace.add("F-A05", null, [grossSavings, b.total_revenue_recovery_value, b.customer_incremental_operating_cost], preVendor);

  // F-A06 — CORE annual customer net benefit (sales headline; no risk/capacity).
  const core = preVendor.sub(b.annual_vendor_recurring_fee);
  trace.add("F-A06", null, [preVendor, b.annual_vendor_recurring_fee], core);

  // F-A07 — expanded value (must be displayed separately from Core).
  const expanded = core
    .add(b.include_risk_in_expanded_value ? b.risk_reduction_value : ZERO)
    .add(b.released_capacity_value_total);
  trace.add("F-A07", null, [core, b.risk_reduction_value, b.released_capacity_value_total], expanded);

  // F-A08 — direct cost reduction rate (null denominator ⇒ null; negatives allowed).
  const reductionRate = safeDiv(netDirect, currentCost);
  trace.add("F-A08", null, [netDirect, currentCost], reductionRate);

  // F-A09 — daily/monthly figures (months are always annual/12).
  const currentDaily = b.operating_days_per_year !== null ? safeDiv(currentCost, b.operating_days_per_year) : null;
  const postDaily = b.operating_days_per_year !== null ? safeDiv(postCost, b.operating_days_per_year) : null;
  const currentMonthly = currentCost.div(12);
  const postMonthly = postCost.div(12);
  const monthlyCore = core.div(12);
  trace.add("F-A09", null, [currentCost, postCost, core], monthlyCore);

  // F-A10 — cost per 1,000 balls (same baseline denominator for comparability).
  let currentPer1000: Decimal | null = null;
  let postPer1000: Decimal | null = null;
  let netPer1000: Decimal | null = null;
  if (b.annual_balls_processed !== null && !b.annual_balls_processed.isZero()) {
    currentPer1000 = currentCost.div(b.annual_balls_processed).mul(1000);
    postPer1000 = postCost.div(b.annual_balls_processed).mul(1000);
    netPer1000 = core.div(b.annual_balls_processed).mul(1000);
    trace.add("F-A10", null, [currentCost, postCost, b.annual_balls_processed], currentPer1000);
  }

  // F-A11 — cost per basket (omitted on 0/missing denominator).
  let currentPerBasket: Decimal | null = null;
  let postPerBasket: Decimal | null = null;
  if (b.annual_baskets_sold !== null && !b.annual_baskets_sold.isZero()) {
    currentPerBasket = currentCost.div(b.annual_baskets_sold);
    postPerBasket = postCost.div(b.annual_baskets_sold);
    trace.add("F-A11", null, [currentCost, postCost, b.annual_baskets_sold], currentPerBasket);
  }

  // F-A12 — value capture / retention (null when pre-fee value ≤ 0).
  let capture: Decimal | null = null;
  let retention: Decimal | null = null;
  if (preVendor.gt(ZERO)) {
    capture = b.annual_vendor_recurring_fee.div(preVendor);
    retention = core.div(preVendor);
    trace.add("F-A12", null, [b.annual_vendor_recurring_fee, core, preVendor], capture);
  }

  return {
    current_direct_operating_cash_cost: currentCost,
    direct_gross_cash_savings: grossSavings,
    post_direct_operating_cash_cost: postCost,
    net_direct_cash_savings_after_vendor: netDirect,
    core_value_pre_vendor_fee: preVendor,
    core_annual_customer_net_benefit: core,
    expanded_annual_customer_value: expanded,
    direct_cost_reduction_rate: reductionRate,
    current_daily_direct_cost: currentDaily,
    post_daily_direct_cost: postDaily,
    current_monthly_direct_cost: currentMonthly,
    post_monthly_direct_cost: postMonthly,
    monthly_core_net_benefit: monthlyCore,
    current_cost_per_1000_balls: currentPer1000,
    post_cost_per_1000_balls: postPer1000,
    net_benefit_per_1000_balls: netPer1000,
    current_cost_per_basket: currentPerBasket,
    post_cost_per_basket: postPerBasket,
    vendor_value_capture_rate: capture,
    customer_value_retention_rate: retention,
  };
}
