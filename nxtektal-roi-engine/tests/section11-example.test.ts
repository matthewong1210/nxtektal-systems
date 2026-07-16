/** Spec §11 worked example — exact regression figures (RaaS + CapEx variants). */

import { describe, expect, it } from "vitest";
import { calculateScenario } from "../src/index.js";
import { section11Snapshot } from "./helpers.js";

describe("Section 11 worked example (RaaS)", () => {
  const result = calculateScenario(section11Snapshot(), "expected");
  const o = result.outputs;

  it("per-task activity costs match §11.2", () => {
    const byId = Object.fromEntries(result.labor_tasks.map((t) => [t.task_id, t]));
    expect(byId.regular_collection!.annual_task_person_hours).toBe(2625);
    expect(byId.regular_collection!.current_task_activity_cost).toBe(57750);
    expect(byId.unloading!.current_task_activity_cost).toBe(11550);
    expect(byId.special_recovery!.current_task_activity_cost).toBe(6864);
  });

  it("effective automation rates match §11.2", () => {
    const byId = Object.fromEntries(result.labor_tasks.map((t) => [t.task_id, t]));
    expect(byId.regular_collection!.effective_automation_rate).toBeCloseTo(0.813694, 6);
    expect(byId.unloading!.effective_automation_rate).toBeCloseTo(0.866761, 6);
    expect(byId.special_recovery!.effective_automation_rate).toBeCloseTo(0.2842875, 6);
  });

  it("cash labor savings match §11.2", () => {
    const byId = Object.fromEntries(result.labor_tasks.map((t) => [t.task_id, t]));
    expect(byId.regular_collection!.cash_labor_savings).toBeCloseTo(32893.58, 2);
    expect(byId.unloading!.cash_labor_savings).toBeCloseTo(7007.76, 2);
    expect(byId.special_recovery!.cash_labor_savings).toBeCloseTo(585.4, 1);
    expect(o.cash_labor_savings_total).toBeCloseTo(40486.75, 2);
  });

  it("equipment / ball / refund savings match §11.2", () => {
    expect(o.equipment_cash_savings).toBe(9480);
    expect(o.ball_replacement_cash_savings).toBe(2400);
    expect(o.refund_and_credit_cash_savings).toBe(1500);
    expect(o.direct_gross_cash_savings).toBeCloseTo(53866.75, 2);
  });

  it("revenue recovery and core net benefit match §11.2", () => {
    expect(o.recovered_contribution_margin).toBe(11700);
    expect(o.net_direct_cash_savings_after_vendor).toBeCloseTo(21456.75, 2);
    expect(o.core_annual_customer_net_benefit).toBeCloseTo(33156.75, 2);
  });

  it("cost bridge matches §11.2", () => {
    expect(o.current_direct_operating_cash_cost).toBe(99864);
    expect(o.post_direct_operating_cash_cost).toBeCloseTo(78407.25, 2);
    expect(o.direct_cost_reduction_rate).toBeCloseTo(0.2149, 4);
    expect(o.monthly_core_net_benefit).toBeCloseTo(2763.06, 2);
    expect(o.current_cost_per_1000_balls).toBeCloseTo(9.51, 2);
    expect(o.post_cost_per_1000_balls).toBeCloseTo(7.47, 2);
  });
});

describe("Section 11 CapEx variant", () => {
  const snapshot = section11Snapshot();
  snapshot.site.discount_rate = 0.1;
  snapshot.pricing = {
    hardware_purchase_price: 70000,
    annual_fixed_service_fee: 8000,
  };
  const result = calculateScenario(snapshot, "expected");
  const o = result.outputs;
  const m = result.multi_year!;

  it("core net benefit is $49,156.75", () => {
    expect(o.core_annual_customer_net_benefit).toBeCloseTo(49156.75, 2);
  });

  it("payback ≈ 17.1 months (exact month 18)", () => {
    expect(m.payback_month).toBe(18);
    expect(m.approximate_payback_months).toBeCloseTo(17.1, 1);
  });

  it("5-year NPV at 10% ≈ $116,343", () => {
    expect(m.npv).toBeCloseTo(116342.75, 0);
  });

  it("5-year simple ROI ≈ 115.6%", () => {
    expect(m.simple_roi).toBeCloseTo(1.1561, 3);
  });

  it("IRR exists and is plausible for these flows", () => {
    expect(m.irr).not.toBeNull();
    expect(m.irr!).toBeGreaterThan(0.6);
    expect(m.irr!).toBeLessThan(0.75);
  });

  it("BCR > 1 consistent with positive NPV", () => {
    expect(m.bcr).not.toBeNull();
    expect(m.bcr!).toBeGreaterThan(1);
  });
});
