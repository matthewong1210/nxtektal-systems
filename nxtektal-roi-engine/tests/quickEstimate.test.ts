/** §9 — Quick Estimate must be a field mapping onto the SAME engine, not new math. */

import { describe, expect, it } from "vitest";
import { buildQuickEstimateSnapshot, calculateScenario } from "../src/index.js";

describe("Quick Estimate adapter (§9)", () => {
  const quick = buildQuickEstimateSnapshot({
    assessment_id: "assess_quick",
    site_id: "site_quick",
    assessment_date: "2026-07-16",
    currency: "USD",
    loaded_regular_rate_override: 22,
    operating_days_per_year: 350,
    operating_hours_per_day: 12,
    regular_collection_cycles_per_day: 6,
    regular_collection_duration_hours: 1.25,
    regular_collection_headcount: 1,
    special_recovery_hours_per_week: 6,
    special_recovery_headcount: 1,
    equipment_monthly_cash_cost: 1000,
    labor_disposition: "redeploy",
    coverage_rate: 0.92,
    system_uptime: 0.95,
    workflow_success_rate: 0.98,
    adoption_rate: 0.95,
  });

  it("maps onto a Full snapshot with the same canonical tasks", () => {
    expect(quick.mode).toBe("quick_estimate");
    expect(quick.labor_tasks.map((t) => t.task_id)).toEqual([
      "regular_collection",
      "special_recovery",
    ]);
    expect(quick.site.active_weeks_per_year).toBe(52);
  });

  it("runs through the same engine with the same formula results", () => {
    const r = calculateScenario(quick, "expected");
    const regular = r.labor_tasks.find((t) => t.task_id === "regular_collection")!;
    // Identical to Full-mode math: 6 × 1.25 × 350 = 2,625 h × $22 = $57,750.
    expect(regular.annual_task_person_hours).toBe(2625);
    expect(regular.current_task_activity_cost).toBe(57750);
    expect(regular.effective_automation_rate).toBeCloseTo(0.813694, 6);
  });

  it("redeploy disposition: freed hours are capacity, not cash (§9.2)", () => {
    const r = calculateScenario(quick, "expected");
    expect(r.outputs.cash_labor_savings_total).toBe(0);
    expect(r.outputs.technical_hours_removed_total).toBeGreaterThan(0);
    expect(r.outputs.released_capacity_hours_total).toBe(
      r.outputs.technical_hours_removed_total,
    );
  });

  it("aggregate equipment enters as estimated_allowed, annualized ×12, and reaches outputs", () => {
    expect(quick.equipment_components).toHaveLength(1);
    const cost = quick.equipment_components![0]!.annual_current_cash_cost;
    expect(typeof cost).toBe("object");
    expect((cost as { input_status: string }).input_status).toBe("estimated_allowed");
    // $1,000/month → $12,000/year (§9.2).
    expect((cost as { value_base: number }).value_base).toBe(12000);
    // And it lands in the current cost: labor (57,750 + 6,864) + equipment 12,000.
    const r = calculateScenario(quick, "expected");
    expect(r.outputs.current_direct_operating_cash_cost).toBe(57750 + 6864 + 12000);
  });

  it("rejects zero or two regular-collection schedules (§9.1)", () => {
    const base = {
      assessment_id: "a3",
      site_id: "s3",
      assessment_date: "2026-07-16",
      currency: "USD",
      loaded_regular_rate_override: 22,
      operating_days_per_year: 350,
      operating_hours_per_day: 12,
      regular_collection_duration_hours: 1,
      regular_collection_headcount: 1,
      special_recovery_hours_per_week: 4,
      special_recovery_headcount: 1,
      labor_disposition: "unknown" as const,
      coverage_rate: 0.9,
      system_uptime: 0.95,
    };
    expect(() => buildQuickEstimateSnapshot(base)).toThrow(/exactly one/);
    expect(() =>
      buildQuickEstimateSnapshot({
        ...base,
        regular_collection_cycles_per_day: 6,
        regular_collection_interval_hours: 2,
      }),
    ).toThrow(/exactly one/);
  });

  it("reduce_overtime disposition maps to overtime_first with avoidable hours", () => {
    const q = buildQuickEstimateSnapshot({
      assessment_id: "a2",
      site_id: "s2",
      assessment_date: "2026-07-16",
      currency: "USD",
      base_wage: 18,
      payroll_burden_rate: 0.25,
      operating_days_per_year: 350,
      operating_hours_per_day: 12,
      regular_collection_interval_hours: 2,
      regular_collection_duration_hours: 1,
      regular_collection_headcount: 1,
      special_recovery_hours_per_week: 4,
      special_recovery_headcount: 1,
      labor_disposition: "reduce_overtime",
      avoidable_overtime_hours_per_year: 300,
      coverage_rate: 0.9,
      system_uptime: 0.95,
    });
    const task = q.labor_tasks[0]!;
    expect(task.cash_realization_method).toBe("overtime_first");
    expect(task.avoidable_overtime_hours).toBe(300);
    // interval_hours: CEILING(12 / 2) = 6 cycles/day suggested (F-T02).
    const r = calculateScenario(q, "expected");
    const regular = r.labor_tasks.find((t) => t.task_id === "regular_collection")!;
    expect(regular.occurrences_year).toBe(6 * 350);
  });
});
