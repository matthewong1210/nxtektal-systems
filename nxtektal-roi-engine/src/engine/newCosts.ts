/** F-P01..F-P08 — new-system customer costs, vendor fees, initial investment. */

import { Decimal, ZERO, clamp01 } from "./decimal.js";
import type { TraceCollector, WarningCollector } from "./trace.js";
import type { ResolvedPricing, ResolvedSite, ResolvedSystemCosts } from "./resolve.js";

export interface CustomerOpsComputation {
  annual_energy_cost: Decimal;
  annual_connectivity_cost: Decimal;
  annual_customer_maintenance_cost: Decimal;
  annual_other_incremental_cost: Decimal;
  customer_incremental_operating_cost: Decimal;
}

/** F-P01..F-P04 — customer-borne incremental operating cost, with vendor-fee inclusion filtering. */
export function computeCustomerOps(
  sc: ResolvedSystemCosts,
  site: ResolvedSite,
  robotCount: Decimal | null,
  newSystemLaborCost: Decimal,
  trace: TraceCollector,
): CustomerOpsComputation {
  // F-P01 — energy (excluded when included in vendor fee).
  let energy = ZERO;
  if (
    !sc.included_in_vendor_fee.energy &&
    robotCount !== null &&
    sc.energy_kwh_per_robot_day !== null &&
    sc.electricity_rate !== null &&
    site.operating_days_per_year !== null
  ) {
    energy = robotCount
      .mul(sc.energy_kwh_per_robot_day)
      .mul(site.operating_days_per_year)
      .mul(sc.electricity_rate);
  }
  trace.add("F-P01", null, [robotCount, sc.energy_kwh_per_robot_day, sc.electricity_rate], energy);

  // F-P02 — connectivity.
  let connectivity = ZERO;
  if (!sc.included_in_vendor_fee.connectivity && sc.monthly_connectivity_cost !== null) {
    connectivity = sc.monthly_connectivity_cost.mul(12);
  }
  trace.add("F-P02", null, [sc.monthly_connectivity_cost], connectivity);

  // F-P03 — maintenance & other incremental costs (filtered per component).
  let maintenance = ZERO;
  if (!sc.included_in_vendor_fee.maintenance && sc.annual_planned_maintenance_cost !== null) {
    maintenance = maintenance.add(sc.annual_planned_maintenance_cost);
  }
  if (!sc.included_in_vendor_fee.repair && sc.annual_expected_repair_cost !== null) {
    maintenance = maintenance.add(sc.annual_expected_repair_cost);
  }
  if (!sc.included_in_vendor_fee.consumables && sc.annual_consumables_cost !== null) {
    maintenance = maintenance.add(sc.annual_consumables_cost);
  }
  let other = ZERO;
  if (!sc.included_in_vendor_fee.insurance && sc.annual_incremental_insurance_cost !== null) {
    other = other.add(sc.annual_incremental_insurance_cost);
  }
  if (!sc.included_in_vendor_fee.other && sc.annual_other_customer_ops_cost !== null) {
    other = other.add(sc.annual_other_customer_ops_cost);
  }
  trace.add("F-P03", null, [maintenance, other], maintenance.add(other));

  // F-P04
  const total = newSystemLaborCost.add(energy).add(connectivity).add(maintenance).add(other);
  trace.add("F-P04", null, [newSystemLaborCost, energy, connectivity, maintenance, other], total);

  return {
    annual_energy_cost: energy,
    annual_connectivity_cost: connectivity,
    annual_customer_maintenance_cost: maintenance,
    annual_other_incremental_cost: other,
    customer_incremental_operating_cost: total,
  };
}

export interface VendorFeeComputation {
  base_vendor_recurring_fee: Decimal;
  performance_fee: Decimal;
  annual_vendor_recurring_fee: Decimal;
}

/** F-P05..F-P07 — vendor recurring fees. Performance fee uses PRE-FEE value (non-circular). */
export function computeVendorFee(
  pricing: ResolvedPricing,
  robotCount: Decimal | null,
  annualBallsProcessed: Decimal | null,
  annualRobotScheduledHours: Decimal | null,
  eligibleValuePreFee: Decimal,
  trace: TraceCollector,
  warnings: WarningCollector,
): VendorFeeComputation {
  // F-P05
  let base = ZERO;
  if (pricing.monthly_platform_fee !== null) base = base.add(pricing.monthly_platform_fee.mul(12));
  if (pricing.monthly_fee_per_robot !== null) {
    if (robotCount !== null) {
      base = base.add(pricing.monthly_fee_per_robot.mul(robotCount).mul(12));
    } else {
      warnings.add("missing_input", null, "monthly_fee_per_robot set but robot_count unknown; per-robot fee omitted and flagged.");
    }
  }
  if (pricing.fee_per_ball !== null) {
    if (annualBallsProcessed !== null) {
      base = base.add(pricing.fee_per_ball.mul(annualBallsProcessed));
    } else {
      warnings.add("missing_input", null, "fee_per_ball set but annual balls processed unknown; per-ball fee omitted and flagged.");
    }
  }
  if (pricing.fee_per_robot_hour !== null) {
    if (annualRobotScheduledHours !== null) {
      base = base.add(pricing.fee_per_robot_hour.mul(annualRobotScheduledHours));
    } else {
      warnings.add("missing_input", null, "fee_per_robot_hour set but scheduled robot hours unknown; per-hour fee omitted and flagged.");
    }
  }
  if (pricing.annual_fixed_service_fee !== null) base = base.add(pricing.annual_fixed_service_fee);
  trace.add("F-P05", null, [pricing.monthly_platform_fee, pricing.monthly_fee_per_robot, pricing.fee_per_ball, pricing.fee_per_robot_hour, pricing.annual_fixed_service_fee], base);

  // F-P06 — performance fee on MAX(0, pre-fee eligible value); min default 0, cap default +inf.
  let performance = ZERO;
  if (pricing.performance_fee_rate !== null) {
    const raw = clamp01(pricing.performance_fee_rate).mul(Decimal.max(ZERO, eligibleValuePreFee));
    const floored = Decimal.max(pricing.performance_fee_min ?? ZERO, raw);
    performance = pricing.performance_fee_cap !== null
      ? Decimal.min(pricing.performance_fee_cap, floored)
      : floored;
    trace.add("F-P06", null, [eligibleValuePreFee, pricing.performance_fee_rate, pricing.performance_fee_min, pricing.performance_fee_cap], performance);
  }

  // F-P07 — CapEx purchase price is NOT part of recurring fees.
  const total = base.add(performance);
  trace.add("F-P07", null, [base, performance], total);

  return {
    base_vendor_recurring_fee: base,
    performance_fee: performance,
    annual_vendor_recurring_fee: total,
  };
}

/** F-P08 — initial customer investment (t0). May be 0; negative kept & flagged for review. */
export function computeInitialInvestment(
  pricing: ResolvedPricing,
  trace: TraceCollector,
  warnings: WarningCollector,
): Decimal {
  const add = (x: Decimal | null) => x ?? ZERO;
  const v = add(pricing.hardware_purchase_price)
    .add(add(pricing.installation_cost))
    .add(add(pricing.site_preparation_cost))
    .add(add(pricing.integration_cost))
    .add(add(pricing.training_cost))
    .add(add(pricing.shipping_and_tax_cost))
    .add(add(pricing.initial_contingency_cost))
    .sub(add(pricing.rebate_or_grant))
    .sub(add(pricing.trade_in_proceeds));
  trace.add("F-P08", null, [pricing.hardware_purchase_price, pricing.installation_cost, pricing.rebate_or_grant, pricing.trade_in_proceeds], v);
  if (v.lt(ZERO)) {
    warnings.add("negative_initial_investment", null, `Initial customer investment is negative (${v}); value kept as-is and flagged for review (F-P08).`);
  }
  return v;
}
