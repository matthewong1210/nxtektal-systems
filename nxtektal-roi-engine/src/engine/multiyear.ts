/**
 * F-M01..F-M10 — multi-year cash flows, NPV, IRR, payback, ROI, BCR.
 *
 * F-M01: every component is RECOMPUTED per year with year-t rates and the
 * deployment ramp — never year-1 × single growth factor. Year scaling
 * interpretations that the spec does not pin down are listed in
 * docs/AMBIGUITIES.md under F-M01.
 */

import { Decimal, ONE, ZERO, safeDiv } from "./decimal.js";
import type { TraceCollector, WarningCollector } from "./trace.js";
import type { ResolvedGrowth, ResolvedSnapshot } from "./resolve.js";

/** Per-year component results the multi-year model needs (from the year pipeline). */
export interface YearComponents {
  core_annual_customer_net_benefit: Decimal;
  direct_gross_cash_savings: Decimal;
  total_revenue_recovery_value: Decimal;
  customer_incremental_operating_cost: Decimal;
  annual_vendor_recurring_fee: Decimal;
  avoided_replacement_capex: Decimal;
  salvage_cash_flow: Decimal;
  system_replacement_capex: Decimal;
}

export interface MultiYearComputation {
  analysis_years: number;
  core_cash_flow_by_year: Decimal[]; // index 0 = t0
  core_net_benefit_by_year: (Decimal | null)[]; // index 0 = null (t0 has no recurring benefit)
  cumulative_core_net_benefit: Decimal;
  npv: Decimal | null;
  irr: Decimal | null;
  irr_note: string | null;
  payback_month: number | null;
  payback_note: string | null;
  approximate_payback_months: Decimal | null;
  simple_roi: Decimal | null;
  bcr: Decimal | null;
  first_year_cash_roi: Decimal | null;
}

/** Growth factor (1+rate)^(t-1): year 1 uses base values. */
export function growthFactor(rate: Decimal, t: number): Decimal {
  return ONE.add(rate).pow(t - 1);
}

/** Deployment ramp for year t (default 1 when not provided). */
export function rampForYear(growth: ResolvedGrowth, t: number): Decimal {
  const v = growth.deployment_ramp_by_year[t - 1];
  return v ?? ONE;
}

export function systemReplacementCapexForYear(growth: ResolvedGrowth, t: number): Decimal {
  return growth.system_replacement_capex_by_year[t - 1] ?? ZERO;
}

export function computeMultiYear(
  resolved: ResolvedSnapshot,
  initialInvestment: Decimal,
  yearComponents: (t: number) => YearComponents,
  trace: TraceCollector,
  warnings: WarningCollector,
): MultiYearComputation {
  const N = resolved.site.analysis_years;
  const discountRate = resolved.site.discount_rate;

  // F-M02 — year 0.
  const flows: Decimal[] = [initialInvestment.neg()];
  trace.add("F-M02", null, [initialInvestment], flows[0]!);

  const coreByYear: (Decimal | null)[] = [null];
  let totalBenefits = ZERO;
  let totalRecurringCosts = ZERO;
  let pvBenefits = ZERO;
  let pvCosts = initialInvestment;

  const years: YearComponents[] = [];
  for (let t = 1; t <= N; t++) {
    const c = yearComponents(t);
    years.push(c);
    // F-M03 — core cash flow for year t.
    const flow = c.core_annual_customer_net_benefit
      .add(c.avoided_replacement_capex)
      .add(c.salvage_cash_flow)
      .sub(c.system_replacement_capex);
    flows.push(flow);
    coreByYear.push(c.core_annual_customer_net_benefit);
    trace.add("F-M03", `year_${t}`, [c.core_annual_customer_net_benefit, c.avoided_replacement_capex, c.salvage_cash_flow, c.system_replacement_capex], flow);

    // F-M08 accumulators (benefits/costs definitions shared with F-M09).
    const benefits_t = c.direct_gross_cash_savings
      .add(c.total_revenue_recovery_value)
      .add(c.avoided_replacement_capex)
      .add(c.salvage_cash_flow);
    const costs_t = c.customer_incremental_operating_cost
      .add(c.annual_vendor_recurring_fee)
      .add(c.system_replacement_capex);
    totalBenefits = totalBenefits.add(benefits_t);
    totalRecurringCosts = totalRecurringCosts.add(costs_t);

    if (discountRate !== null) {
      const df = ONE.add(discountRate).pow(t);
      pvBenefits = pvBenefits.add(benefits_t.div(df));
      pvCosts = pvCosts.add(costs_t.div(df));
    }
  }

  // F-M04 — NPV (only when a discount rate exists).
  let npv: Decimal | null = null;
  if (discountRate !== null) {
    npv = ZERO;
    for (let t = 0; t <= N; t++) {
      npv = npv.add(flows[t]!.div(ONE.add(discountRate).pow(t)));
    }
    trace.add("F-M04", null, [discountRate, N], npv);
  }

  // F-M05 — IRR by bisection; null with a note when undefined.
  const { irr, note: irrNote } = solveIrr(flows);
  if (irr !== null) trace.add("F-M05", null, [N], irr);

  // F-M06 — exact payback in months from monthly cash flows
  // (each year's core cash flow spread annual/12 across its months).
  let paybackMonth: number | null = null;
  let paybackNote: string | null = null;
  let cumulative = flows[0]!;
  if (cumulative.gte(ZERO)) {
    paybackMonth = 0;
  } else {
    outer: for (let t = 1; t <= N; t++) {
      const monthly = flows[t]!.div(12);
      for (let m = 1; m <= 12; m++) {
        cumulative = cumulative.add(monthly);
        if (cumulative.gte(ZERO)) {
          paybackMonth = (t - 1) * 12 + m;
          break outer;
        }
      }
    }
    if (paybackMonth === null) {
      paybackNote = "not achieved within analysis horizon";
      warnings.add("payback_not_achieved", null, "Cumulative core cash flow never turns positive within the analysis horizon; no payback month is reported (§8.6).");
    }
  }
  if (paybackMonth !== null) trace.add("F-M06", null, [N], paybackMonth);

  // Approximate fallback (labelled) using stable year-1 recurring net.
  let approxPayback: Decimal | null = null;
  const year1 = coreByYear[1];
  if (year1 && year1.gt(ZERO) && initialInvestment.gt(ZERO)) {
    approxPayback = initialInvestment.div(year1.div(12));
    warnings.add("approximate_payback", null, `approximate_payback_months (${approxPayback.toFixed(1)}) uses initial_investment / (annual_recurring_net/12); labelled approximate per F-M06.`);
  }

  // F-M07 — cumulative core net benefit including t0.
  let cumulativeNet = ZERO;
  for (const f of flows) cumulativeNet = cumulativeNet.add(f);
  trace.add("F-M07", null, [N], cumulativeNet);

  // F-M08 — N-year simple ROI (null when total costs are 0).
  const totalCosts = initialInvestment.add(totalRecurringCosts);
  const simpleRoi = totalCosts.isZero()
    ? null
    : totalBenefits.sub(totalCosts).div(totalCosts);
  if (simpleRoi !== null) trace.add("F-M08", null, [totalBenefits, totalCosts], simpleRoi);

  // F-M09 — BCR on present values (needs a discount rate).
  let bcr: Decimal | null = null;
  if (discountRate !== null) {
    bcr = safeDiv(pvBenefits, pvCosts);
    if (bcr !== null) trace.add("F-M09", null, [pvBenefits, pvCosts], bcr);
  }

  // F-M10 — first-year cash ROI (suppressed when initial investment is 0).
  let firstYearRoi: Decimal | null = null;
  if (!initialInvestment.isZero() && year1 !== null && year1 !== undefined) {
    firstYearRoi = year1.sub(initialInvestment).div(initialInvestment);
    trace.add("F-M10", null, [year1, initialInvestment], firstYearRoi);
  }

  return {
    analysis_years: N,
    core_cash_flow_by_year: flows,
    core_net_benefit_by_year: coreByYear,
    cumulative_core_net_benefit: cumulativeNet,
    npv,
    irr,
    irr_note: irrNote,
    payback_month: paybackMonth,
    payback_note: paybackNote,
    approximate_payback_months: approxPayback,
    simple_roi: simpleRoi,
    bcr,
    first_year_cash_roi: firstYearRoi,
  };
}

/** F-M05 — numeric root of Σ flows/(1+r)^t = 0, bisection on [-0.9999, 10]. */
function solveIrr(flows: Decimal[]): { irr: Decimal | null; note: string | null } {
  const hasNegative = flows.some((f) => f.lt(ZERO));
  const hasPositive = flows.some((f) => f.gt(ZERO));
  if (!hasNegative || !hasPositive) {
    return { irr: null, note: "IRR undefined: cash flows do not change sign." };
  }
  const npvAt = (r: Decimal): Decimal => {
    let v = ZERO;
    for (let t = 0; t < flows.length; t++) {
      v = v.add(flows[t]!.div(ONE.add(r).pow(t)));
    }
    return v;
  };
  let lo = new Decimal("-0.9999");
  let hi = new Decimal("10");
  let fLo = npvAt(lo);
  const fHi = npvAt(hi);
  if (fLo.mul(fHi).gt(ZERO)) {
    return { irr: null, note: "IRR undefined: no sign change of NPV on [-99.99%, 1000%]." };
  }
  for (let i = 0; i < 200; i++) {
    const mid = lo.add(hi).div(2);
    const fMid = npvAt(mid);
    if (fMid.abs().lt(new Decimal("1e-10"))) return { irr: mid, note: null };
    if (fLo.mul(fMid).lte(ZERO)) {
      hi = mid;
    } else {
      lo = mid;
      fLo = fMid;
    }
  }
  return { irr: lo.add(hi).div(2), note: null };
}
