/** Deterministic calculation pipeline — spec §3 order, §12 pseudocode. */

import { Decimal, ONE, ZERO, toMoney, toRate, toRaw } from "./decimal.js";
import { TraceCollector, WarningCollector, type EngineWarning } from "./trace.js";
import { resolveSnapshot, type ResolvedSnapshot, type ResolvedLaborTask } from "./resolve.js";
import { computeCapacity } from "./capacity.js";
import { computeLaborTask, fteConversions, type LaborTaskComputation } from "./labor.js";
import { computeEquipment } from "./equipment.js";
import { computeBallLoss } from "./ballLoss.js";
import { computeRevenue } from "./revenue.js";
import { computeRisk } from "./risk.js";
import { computeCustomerOps, computeVendorFee, computeInitialInvestment } from "./newCosts.js";
import { computeAggregate } from "./aggregate.js";
import { computeMultiYear, rampForYear, systemReplacementCapexForYear, type YearComponents } from "./multiyear.js";
import { scaleResolvedForYear } from "./yearScale.js";
import { computePricing } from "./pricing.js";
import { validateResolved } from "./validate.js";
import type { AssessmentSnapshot } from "../types/inputs.js";
import type { Scenario } from "../types/evidence.js";
import type {
  AssessmentResult,
  CoreOutputs,
  ScenarioResult,
} from "../types/outputs.js";
import { computeSensitivityAndConfidence, type CoreRun } from "./sensitivity.js";

interface PipelineTotals {
  currentLaborActivityCost: Decimal;
  cashLaborSavings: Decimal;
  technicalHoursRemoved: Decimal;
  cashSavedHours: Decimal;
  releasedCapacityHours: Decimal;
  releasedCapacityValue: Decimal;
  newSystemLaborCost: Decimal;
  laborResults: LaborTaskComputation[];
}

function runLaborPipeline(
  resolved: ResolvedSnapshot,
  countedCurrentTasks: ResolvedLaborTask[],
  capacityFit: Decimal,
  trace: TraceCollector,
  warnings: WarningCollector,
): PipelineTotals {
  let activity = ZERO;
  let cash = ZERO;
  let removed = ZERO;
  let cashHours = ZERO;
  let capHours = ZERO;
  let capValue = ZERO;
  const results: LaborTaskComputation[] = [];

  for (const task of countedCurrentTasks) {
    const r = computeLaborTask(task, resolved.site, capacityFit, trace, warnings);
    results.push(r);
    if (r.current_task_activity_cost !== null) activity = activity.add(r.current_task_activity_cost);
    if (r.cash_labor_savings !== null) cash = cash.add(r.cash_labor_savings);
    if (r.technical_hours_removed !== null) removed = removed.add(r.technical_hours_removed);
    if (r.cash_saved_hours !== null) cashHours = cashHours.add(r.cash_saved_hours);
    if (r.released_capacity_hours !== null) capHours = capHours.add(r.released_capacity_hours);
    if (r.released_capacity_value !== null) capValue = capValue.add(r.released_capacity_value);
  }

  // F-L11 — new-system labor cost.
  let newLabor = ZERO;
  for (const task of resolved.new_system_tasks) {
    const r = computeLaborTask(task, resolved.site, capacityFit, trace, warnings);
    results.push(r);
    if (r.current_task_activity_cost !== null) newLabor = newLabor.add(r.current_task_activity_cost);
  }
  trace.add("F-L11", null, [resolved.new_system_tasks.length], newLabor);

  return {
    currentLaborActivityCost: activity,
    cashLaborSavings: cash,
    technicalHoursRemoved: removed,
    cashSavedHours: cashHours,
    releasedCapacityHours: capHours,
    releasedCapacityValue: capValue,
    newSystemLaborCost: newLabor,
    laborResults: results,
  };
}

/** Recompute every component for one analysis year (F-M01), with deployment ramp. */
function computeYearComponents(
  baseResolved: ResolvedSnapshot,
  t: number,
  trace: TraceCollector,
  warnings: WarningCollector,
): YearComponents {
  const resolved = scaleResolvedForYear(baseResolved, t);
  const ramp = rampForYear(resolved.growth, t);
  const { countedCurrentTasks } = validateResolved(resolved, warnings);

  const capacity = computeCapacity(resolved.site, resolved.system, trace, warnings);
  const labor = runLaborPipeline(resolved, countedCurrentTasks, capacity.final_fit, trace, warnings);
  const equipment = computeEquipment(resolved.equipment_components, trace, warnings);
  const ball = computeBallLoss(resolved.ball_loss_causes, trace, warnings);
  const revenue = computeRevenue(resolved.revenue_event_groups, trace, warnings);
  const customerOps = computeCustomerOps(
    resolved.system_costs,
    resolved.site,
    capacity.robot_count,
    labor.newSystemLaborCost,
    trace,
  );

  // Deployment ramp scales system-driven deltas (savings, new costs, fees).
  const grossSavings = labor.cashLaborSavings
    .add(equipment.equipment_cash_savings)
    .add(ball.ball_replacement_cash_savings)
    .add(revenue.refund_and_credit_cash_savings)
    .add(resolved.system_costs.other_direct_cash_savings)
    .mul(ramp);
  const revenueRecovery = revenue.total_revenue_recovery_value.mul(ramp);
  const ops = customerOps.customer_incremental_operating_cost.mul(ramp);

  const eligiblePreFee = grossSavings.add(revenueRecovery).sub(ops);
  const vendorFee = computeVendorFee(
    resolved.pricing,
    capacity.robot_count,
    capacity.annual_balls_processed,
    capacity.annual_robot_scheduled_hours,
    eligiblePreFee,
    trace,
    warnings,
  );
  const fee = vendorFee.base_vendor_recurring_fee.mul(ramp).add(vendorFee.performance_fee);

  const core = grossSavings.add(revenueRecovery).sub(ops).sub(fee);

  return {
    core_annual_customer_net_benefit: core,
    direct_gross_cash_savings: grossSavings,
    total_revenue_recovery_value: revenueRecovery,
    customer_incremental_operating_cost: ops,
    annual_vendor_recurring_fee: fee,
    avoided_replacement_capex: equipment.avoidedReplacementCapex(t),
    salvage_cash_flow: equipment.salvageCashFlow(t),
    system_replacement_capex: systemReplacementCapexForYear(resolved.growth, t),
  };
}

/** Calculate a single scenario. Deterministic: same snapshot + version ⇒ same output. */
export function calculateScenario(
  snapshot: AssessmentSnapshot,
  scenario: Scenario,
  overrides?: Map<string, number>,
): ScenarioResult {
  const trace = new TraceCollector();
  const warnings = new WarningCollector();

  const resolved = resolveSnapshot(snapshot, scenario, overrides);
  const { countedCurrentTasks } = validateResolved(resolved, warnings);

  // §3 steps 3–8 — year-1 component pipeline.
  const capacity = computeCapacity(resolved.site, resolved.system, trace, warnings);
  const labor = runLaborPipeline(resolved, countedCurrentTasks, capacity.final_fit, trace, warnings);
  const equipment = computeEquipment(resolved.equipment_components, trace, warnings);
  const ball = computeBallLoss(resolved.ball_loss_causes, trace, warnings);
  const revenue = computeRevenue(resolved.revenue_event_groups, trace, warnings);
  const risk = computeRisk(resolved.risk_items, resolved.system_costs, trace);
  const customerOps = computeCustomerOps(
    resolved.system_costs,
    resolved.site,
    capacity.robot_count,
    labor.newSystemLaborCost,
    trace,
  );

  const ramp1 = rampForYear(resolved.growth, 1);
  const cashLabor = labor.cashLaborSavings.mul(ramp1);
  const equipSavings = equipment.equipment_cash_savings.mul(ramp1);
  const ballSavings = ball.ball_replacement_cash_savings.mul(ramp1);
  const refundSavings = revenue.refund_and_credit_cash_savings.mul(ramp1);
  const otherSavings = resolved.system_costs.other_direct_cash_savings.mul(ramp1);
  const revenueRecovery = revenue.total_revenue_recovery_value.mul(ramp1);
  const ops = customerOps.customer_incremental_operating_cost.mul(ramp1);

  // F-P06 — performance fee base is PRE-FEE eligible value (non-circular).
  const eligiblePreFee = cashLabor
    .add(equipSavings)
    .add(ballSavings)
    .add(refundSavings)
    .add(otherSavings)
    .add(revenueRecovery)
    .sub(ops);
  const vendorFee = computeVendorFee(
    resolved.pricing,
    capacity.robot_count,
    capacity.annual_balls_processed,
    capacity.annual_robot_scheduled_hours,
    eligiblePreFee,
    trace,
    warnings,
  );
  const totalFee = vendorFee.base_vendor_recurring_fee.mul(ramp1).add(vendorFee.performance_fee);

  // §3 step 9 — aggregate.
  const aggregate = computeAggregate(
    {
      current_labor_activity_cost_total: labor.currentLaborActivityCost,
      current_equipment_cash_cost: equipment.current_equipment_cash_cost,
      current_ball_loss_cost: ball.current_ball_loss_cost,
      current_refund_cash_loss: revenue.current_refund_cash_loss,
      current_service_credit_cost: revenue.current_service_credit_cost,
      current_other_direct_cash_cost: resolved.system_costs.current_other_direct_cash_cost,
      cash_labor_savings_total: cashLabor,
      equipment_cash_savings: equipSavings,
      ball_replacement_cash_savings: ballSavings,
      refund_and_credit_cash_savings: refundSavings,
      other_direct_cash_savings: otherSavings,
      total_revenue_recovery_value: revenueRecovery,
      customer_incremental_operating_cost: ops,
      annual_vendor_recurring_fee: totalFee,
      risk_reduction_value: risk.risk_reduction_value,
      released_capacity_value_total: labor.releasedCapacityValue.mul(ramp1),
      include_risk_in_expanded_value: resolved.system_costs.include_risk_in_expanded_value,
      operating_days_per_year: resolved.site.operating_days_per_year,
      annual_balls_processed: capacity.annual_balls_processed,
      annual_baskets_sold: resolved.site.annual_baskets_sold,
    },
    trace,
  );

  // §3 step 10 — multi-year cash flows and metrics (Core only — no risk/capacity).
  const initialInvestment = computeInitialInvestment(resolved.pricing, trace, warnings);
  const multiYear = computeMultiYear(
    resolved,
    initialInvestment,
    (t) =>
      t === 1
        ? {
            core_annual_customer_net_benefit: aggregate.core_annual_customer_net_benefit,
            direct_gross_cash_savings: aggregate.direct_gross_cash_savings,
            total_revenue_recovery_value: revenueRecovery,
            customer_incremental_operating_cost: ops,
            annual_vendor_recurring_fee: totalFee,
            avoided_replacement_capex: equipment.avoidedReplacementCapex(1),
            salvage_cash_flow: equipment.salvageCashFlow(1),
            system_replacement_capex: systemReplacementCapexForYear(resolved.growth, 1),
          }
        : computeYearComponents(resolved, t, trace, warnings),
    trace,
    warnings,
  );

  // Pricing guardrails (F-Q). Variable vendor fees = non-fixed annual components.
  const variableFees = vendorFee.annual_vendor_recurring_fee.sub(
    (resolved.pricing.monthly_platform_fee ?? ZERO).mul(12)
      .add((resolved.pricing.annual_fixed_service_fee ?? ZERO)),
  );
  const pricingCaps = computePricing(
    resolved.pricing,
    aggregate.core_value_pre_vendor_fee,
    aggregate.core_annual_customer_net_benefit,
    Decimal.max(ZERO, variableFees),
    trace,
  );

  // Year-1 hour totals carry the same deployment ramp as their value counterparts.
  const techHoursRamped = labor.technicalHoursRemoved.mul(ramp1);
  const cashHoursRamped = labor.cashSavedHours.mul(ramp1);
  const fte = fteConversions(
    techHoursRamped,
    cashHoursRamped,
    resolved.site.annual_paid_hours_per_fte,
    trace,
  );

  const outputs: CoreOutputs = {
    current_direct_operating_cash_cost: toMoney(aggregate.current_direct_operating_cash_cost),
    post_direct_operating_cash_cost: toMoney(aggregate.post_direct_operating_cash_cost),
    direct_gross_cash_savings: toMoney(aggregate.direct_gross_cash_savings),
    net_direct_cash_savings_after_vendor: toMoney(aggregate.net_direct_cash_savings_after_vendor),
    total_revenue_recovery_value: toMoney(revenueRecovery),
    customer_incremental_operating_cost: toMoney(ops),
    annual_vendor_recurring_fee: toMoney(totalFee),
    base_vendor_recurring_fee: toMoney(vendorFee.base_vendor_recurring_fee),
    performance_fee: toMoney(vendorFee.performance_fee),
    initial_customer_investment: toMoney(initialInvestment),
    core_value_pre_vendor_fee: toMoney(aggregate.core_value_pre_vendor_fee),
    core_annual_customer_net_benefit: toMoney(aggregate.core_annual_customer_net_benefit),
    expanded_annual_customer_value: toMoney(aggregate.expanded_annual_customer_value),
    risk_reduction_value: toMoney(risk.risk_reduction_value),
    released_capacity_value_total: toMoney(labor.releasedCapacityValue.mul(ramp1)),
    released_capacity_hours_total: toRate(labor.releasedCapacityHours.mul(ramp1), 2),
    technical_hours_removed_total: toRate(techHoursRamped, 2),
    cash_saved_hours_total: toRate(cashHoursRamped, 2),
    cash_labor_savings_total: toMoney(cashLabor),
    equipment_cash_savings: toMoney(equipSavings),
    ball_replacement_cash_savings: toMoney(ballSavings),
    refund_and_credit_cash_savings: toMoney(refundSavings),
    recovered_contribution_margin: toMoney(revenue.recovered_contribution_margin.mul(ramp1)),
    incremental_sales_margin: toMoney(revenue.incremental_sales_margin.mul(ramp1)),
    direct_cost_reduction_rate: toRate(aggregate.direct_cost_reduction_rate),
    current_daily_direct_cost: toMoney(aggregate.current_daily_direct_cost),
    post_daily_direct_cost: toMoney(aggregate.post_daily_direct_cost),
    current_monthly_direct_cost: toMoney(aggregate.current_monthly_direct_cost),
    post_monthly_direct_cost: toMoney(aggregate.post_monthly_direct_cost),
    monthly_core_net_benefit: toMoney(aggregate.monthly_core_net_benefit),
    current_cost_per_1000_balls: toMoney(aggregate.current_cost_per_1000_balls),
    post_cost_per_1000_balls: toMoney(aggregate.post_cost_per_1000_balls),
    net_benefit_per_1000_balls: toMoney(aggregate.net_benefit_per_1000_balls),
    current_cost_per_basket: toMoney(aggregate.current_cost_per_basket),
    post_cost_per_basket: toMoney(aggregate.post_cost_per_basket),
    vendor_value_capture_rate: toRate(aggregate.vendor_value_capture_rate),
    customer_value_retention_rate: toRate(aggregate.customer_value_retention_rate),
    technical_fte_released: toRate(fte.technical_fte_released, 3),
    cash_fte_avoided: toRate(fte.cash_fte_avoided, 3),
  };

  return {
    model_version: snapshot.model_version,
    assessment_id: snapshot.assessment_id,
    scenario,
    currency: resolved.site.currency,
    outputs,
    capacity: {
      annual_balls_processed: toRate(capacity.annual_balls_processed, 0),
      avg_daily_ball_demand: toRate(capacity.avg_daily_ball_demand, 2),
      peak_daily_ball_demand: toRate(capacity.peak_daily_ball_demand, 2),
      target_daily_ball_throughput: toRate(capacity.target_daily_ball_throughput, 2),
      operational_collection_rate_bph: toRate(capacity.operational_collection_rate_bph, 2),
      operational_daily_capacity_per_robot: toRate(capacity.operational_daily_capacity_per_robot, 2),
      required_robot_count: toRate(capacity.required_robot_count, 0),
      robot_count: toRate(capacity.robot_count, 0),
      daily_capacity_fit: toRate(capacity.daily_capacity_fit),
      peak_window_capacity_fit: toRate(capacity.peak_window_capacity_fit),
      capacity_fit: toRate(capacity.final_fit),
      robot_utilization: toRate(capacity.robot_utilization),
      annual_robot_scheduled_hours: toRate(capacity.annual_robot_scheduled_hours, 2),
    },
    labor_tasks: labor.laborResults.map((r) => ({
      task_id: r.task_id,
      occurrences_year: toRate(r.occurrences_year, 2),
      annual_task_person_hours: toRate(r.annual_task_person_hours, 2),
      current_task_activity_cost: toMoney(r.current_task_activity_cost),
      effective_automation_rate: toRate(r.effective_automation_rate, 6),
      technical_hours_removed: toRate(r.technical_hours_removed, 2),
      residual_task_person_hours: toRate(r.residual_task_person_hours, 2),
      technical_labor_value_released: toMoney(r.technical_labor_value_released),
      cash_labor_savings: toMoney(r.cash_labor_savings),
      cash_saved_hours: toRate(r.cash_saved_hours, 2),
      released_capacity_hours: toRate(r.released_capacity_hours, 2),
      released_capacity_value: toMoney(r.released_capacity_value),
      incomplete: r.incomplete,
    })),
    multi_year: {
      analysis_years: multiYear.analysis_years,
      core_cash_flow_by_year: multiYear.core_cash_flow_by_year.map((f) => toMoney(f)),
      core_net_benefit_by_year: multiYear.core_net_benefit_by_year.map((f) => toMoney(f)),
      cumulative_core_net_benefit: toMoney(multiYear.cumulative_core_net_benefit),
      npv: toMoney(multiYear.npv),
      irr: toRate(multiYear.irr, 6),
      irr_note: multiYear.irr_note,
      payback_month: multiYear.payback_month,
      payback_note: multiYear.payback_note,
      approximate_payback_months: toRate(multiYear.approximate_payback_months, 1),
      simple_roi: toRate(multiYear.simple_roi),
      bcr: toRate(multiYear.bcr),
      first_year_cash_roi: toRate(multiYear.first_year_cash_roi),
    },
    pricing: {
      break_even_annual_vendor_fee: toMoney(pricingCaps.break_even_annual_vendor_fee),
      max_vendor_fee_for_target_savings: toMoney(pricingCaps.max_vendor_fee_for_target_savings),
      annual_vendor_fee_by_value_share: toMoney(pricingCaps.annual_vendor_fee_by_value_share),
      max_hardware_purchase_price: toMoney(pricingCaps.max_hardware_purchase_price),
      max_fixed_monthly_fee: toMoney(pricingCaps.max_fixed_monthly_fee),
    },
    warnings: warnings.warnings,
    formula_trace: trace.entries,
    raw: {
      current_direct_operating_cash_cost: toRaw(aggregate.current_direct_operating_cash_cost),
      post_direct_operating_cash_cost: toRaw(aggregate.post_direct_operating_cash_cost),
      direct_gross_cash_savings: toRaw(aggregate.direct_gross_cash_savings),
      net_direct_cash_savings_after_vendor: toRaw(aggregate.net_direct_cash_savings_after_vendor),
      core_value_pre_vendor_fee: toRaw(aggregate.core_value_pre_vendor_fee),
      core_annual_customer_net_benefit: toRaw(aggregate.core_annual_customer_net_benefit),
      expanded_annual_customer_value: toRaw(aggregate.expanded_annual_customer_value),
      cash_labor_savings_total: toRaw(cashLabor),
      total_revenue_recovery_value: toRaw(revenueRecovery),
    },
  };
}

/**
 * Lightweight expected-scenario core value (F-A06) used by sensitivity (F-G02).
 * Skips presentation, multi-year and pricing — component math is identical.
 */
export function computeCoreValue(
  snapshot: AssessmentSnapshot,
  scenario: Scenario,
  overrides?: Map<string, number>,
): CoreRun {
  const trace = new TraceCollector();
  const warnings = new WarningCollector();
  const resolved = resolveSnapshot(snapshot, scenario, overrides);
  const { countedCurrentTasks } = validateResolved(resolved, warnings);
  const capacity = computeCapacity(resolved.site, resolved.system, trace, warnings);
  const labor = runLaborPipeline(resolved, countedCurrentTasks, capacity.final_fit, trace, warnings);
  const equipment = computeEquipment(resolved.equipment_components, trace, warnings);
  const ball = computeBallLoss(resolved.ball_loss_causes, trace, warnings);
  const revenue = computeRevenue(resolved.revenue_event_groups, trace, warnings);
  const customerOps = computeCustomerOps(
    resolved.system_costs,
    resolved.site,
    capacity.robot_count,
    labor.newSystemLaborCost,
    trace,
  );
  const ramp1 = rampForYear(resolved.growth, 1);
  const grossSavings = labor.cashLaborSavings
    .add(equipment.equipment_cash_savings)
    .add(ball.ball_replacement_cash_savings)
    .add(revenue.refund_and_credit_cash_savings)
    .add(resolved.system_costs.other_direct_cash_savings)
    .mul(ramp1);
  const revenueRecovery = revenue.total_revenue_recovery_value.mul(ramp1);
  const ops = customerOps.customer_incremental_operating_cost.mul(ramp1);
  const eligiblePreFee = grossSavings.add(revenueRecovery).sub(ops);
  const vendorFee = computeVendorFee(
    resolved.pricing,
    capacity.robot_count,
    capacity.annual_balls_processed,
    capacity.annual_robot_scheduled_hours,
    eligiblePreFee,
    trace,
    warnings,
  );
  const fee = vendorFee.base_vendor_recurring_fee.mul(ramp1).add(vendorFee.performance_fee);
  const core = grossSavings.add(revenueRecovery).sub(ops).sub(fee);
  return { core, resolver: resolved.resolver };
}

/** Calculate all three scenarios + sensitivity + confidence (F-G02..F-G06, §7). */
export function calculateAssessment(snapshot: AssessmentSnapshot): AssessmentResult {
  const scenarios: Record<Scenario, ScenarioResult> = {
    conservative: calculateScenario(snapshot, "conservative"),
    expected: calculateScenario(snapshot, "expected"),
    high_performance: calculateScenario(snapshot, "high_performance"),
  };

  // §8.6 — scenario monotonicity check on core net benefit.
  const crossWarnings: EngineWarning[] = [];
  const cons = scenarios.conservative.outputs.core_annual_customer_net_benefit;
  const exp = scenarios.expected.outputs.core_annual_customer_net_benefit;
  const high = scenarios.high_performance.outputs.core_annual_customer_net_benefit;
  if (cons !== null && exp !== null && cons > exp) {
    crossWarnings.push({
      code: "scenario_monotonicity_warning",
      entity_id: null,
      message: `Conservative core net benefit (${cons}) exceeds Expected (${exp}); check scenario_direction settings on ranged inputs (§8.6).`,
    });
  }
  if (exp !== null && high !== null && exp > high) {
    crossWarnings.push({
      code: "scenario_monotonicity_warning",
      entity_id: null,
      message: `Expected core net benefit (${exp}) exceeds High Performance (${high}); check scenario_direction settings on ranged inputs (§8.6).`,
    });
  }

  const { sensitivity, confidence } = computeSensitivityAndConfidence((overrides) =>
    computeCoreValue(snapshot, "expected", overrides),
  );

  return {
    model_version: snapshot.model_version,
    assessment_id: snapshot.assessment_id,
    currency: snapshot.site.currency,
    scenarios,
    sensitivity,
    confidence,
    cross_scenario_warnings: crossWarnings,
  };
}
