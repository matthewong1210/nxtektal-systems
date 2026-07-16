/** F-Q01..F-Q05 — pricing guardrails (theoretical caps, not recommended prices). */

import { Decimal, ZERO } from "./decimal.js";
import type { TraceCollector } from "./trace.js";
import type { ResolvedPricing } from "./resolve.js";

export interface PricingComputation {
  break_even_annual_vendor_fee: Decimal;
  max_vendor_fee_for_target_savings: Decimal | null;
  annual_vendor_fee_by_value_share: Decimal | null;
  max_hardware_purchase_price: Decimal | null;
  max_fixed_monthly_fee: Decimal | null;
}

export function computePricing(
  pricing: ResolvedPricing,
  coreValuePreVendorFee: Decimal,
  coreAnnualCustomerNetBenefit: Decimal,
  annualVariableVendorFees: Decimal,
  trace: TraceCollector,
): PricingComputation {
  // F-Q01 — fee that drives customer core net benefit to zero (theoretical ceiling).
  const breakEven = Decimal.max(ZERO, coreValuePreVendorFee);
  trace.add("F-Q01", null, [coreValuePreVendorFee], breakEven);

  // F-Q02 — cap that preserves a target customer net benefit.
  let maxForTarget: Decimal | null = null;
  if (pricing.target_customer_annual_net_benefit !== null) {
    maxForTarget = Decimal.max(
      ZERO,
      coreValuePreVendorFee.sub(pricing.target_customer_annual_net_benefit),
    );
    trace.add("F-Q02", null, [coreValuePreVendorFee, pricing.target_customer_annual_net_benefit], maxForTarget);
  }

  // F-Q03 — value-share pricing.
  let byValueShare: Decimal | null = null;
  if (pricing.vendor_target_capture_rate !== null) {
    byValueShare = Decimal.max(ZERO, coreValuePreVendorFee.mul(pricing.vendor_target_capture_rate));
    trace.add("F-Q03", null, [coreValuePreVendorFee, pricing.vendor_target_capture_rate], byValueShare);
  }

  // F-Q04 — max hardware price for a target payback (static fallback formula).
  let maxHardware: Decimal | null = null;
  if (pricing.target_payback_months !== null) {
    // recurring core net benefit is computed with hardware excluded from t0 by definition
    const maxTotalInitial = Decimal.max(
      ZERO,
      coreAnnualCustomerNetBenefit.mul(pricing.target_payback_months).div(12),
    );
    const z = (x: Decimal | null) => x ?? ZERO;
    maxHardware = Decimal.max(
      ZERO,
      maxTotalInitial
        .sub(z(pricing.installation_cost))
        .sub(z(pricing.site_preparation_cost))
        .sub(z(pricing.integration_cost))
        .sub(z(pricing.training_cost))
        .sub(z(pricing.shipping_and_tax_cost))
        .sub(z(pricing.initial_contingency_cost))
        .add(z(pricing.rebate_or_grant))
        .add(z(pricing.trade_in_proceeds)),
    );
    trace.add("F-Q04", null, [coreAnnualCustomerNetBenefit, pricing.target_payback_months, maxTotalInitial], maxHardware);
  }

  // F-Q05 — RaaS fixed monthly fee cap.
  let maxMonthly: Decimal | null = null;
  if (pricing.target_customer_annual_net_benefit !== null) {
    maxMonthly = Decimal.max(
      ZERO,
      coreValuePreVendorFee
        .sub(pricing.target_customer_annual_net_benefit)
        .sub(annualVariableVendorFees)
        .div(12),
    );
    trace.add("F-Q05", null, [coreValuePreVendorFee, pricing.target_customer_annual_net_benefit, annualVariableVendorFees], maxMonthly);
  }

  return {
    break_even_annual_vendor_fee: breakEven,
    max_vendor_fee_for_target_savings: maxForTarget,
    annual_vendor_fee_by_value_share: byValueShare,
    max_hardware_purchase_price: maxHardware,
    max_fixed_monthly_fee: maxMonthly,
  };
}
