/** Spec §14 — the 22 minimum acceptance tests, in spec order. */

import { describe, expect, it } from "vitest";
import {
  calculateAssessment,
  calculateScenario,
  RevenueDedupError,
  EngineValidationError,
} from "../src/index.js";
import { baseSnapshot, simpleTask, section11Snapshot } from "./helpers.js";

describe("§14 acceptance tests", () => {
  it("1. Zero automation: any automation factor 0 ⇒ zero release and zero labor savings", () => {
    const r = calculateScenario(
      baseSnapshot({ labor_tasks: [simpleTask({ coverage_rate: 0 })] }),
      "expected",
    );
    const t = r.labor_tasks[0]!;
    expect(t.technical_hours_removed).toBe(0);
    expect(t.cash_labor_savings).toBe(0);
    expect(r.outputs.cash_labor_savings_total).toBe(0);
  });

  it("2. No cash realization: EA=100%, factor=0 ⇒ cash 0, capacity = all removed hours", () => {
    const r = calculateScenario(
      baseSnapshot({ labor_tasks: [simpleTask({ simple_cash_realization_factor: 0 })] }),
      "expected",
    );
    const t = r.labor_tasks[0]!;
    expect(t.technical_hours_removed).toBe(1000);
    expect(t.cash_labor_savings).toBe(0);
    expect(t.released_capacity_hours).toBe(1000);
  });

  it("3. Full cash realization: factor=1, no overtime ⇒ cash = technical value released", () => {
    const r = calculateScenario(baseSnapshot({ labor_tasks: [simpleTask()] }), "expected");
    const t = r.labor_tasks[0]!;
    expect(t.cash_labor_savings).toBe(t.technical_labor_value_released);
    expect(t.cash_labor_savings).toBe(20000);
  });

  it("4. Overtime precedence: avoidable OT saved at OT rate first, regular factor on the rest", () => {
    const r = calculateScenario(
      baseSnapshot({
        labor_tasks: [
          simpleTask({
            loaded_regular_rate_override: undefined,
            base_wage: 20,
            overtime_share: 0.2,
            overtime_multiplier: 1.5,
            effective_automation_override: 0.5,
            cash_realization_method: "overtime_first",
            regular_cash_realization_factor: 0.5,
            simple_cash_realization_factor: undefined, // F-L08/F-L09 are mutually exclusive
            avoidable_overtime_hours: 100,
          }),
        ],
      }),
      "expected",
    );
    const t = r.labor_tasks[0]!;
    // removed = 500h; OT first: min(500,100)=100h × $30 = 3000; remaining 400h × 0.5 = 200h × $20 = 4000.
    expect(t.technical_hours_removed).toBe(500);
    expect(t.cash_labor_savings).toBe(7000);
    expect(t.cash_saved_hours).toBe(300);
  });

  it("5. Capacity shortfall: fleet at 60% of demand ⇒ capacity_fit 0.60, never 100% automation", () => {
    const r = calculateScenario(
      baseSnapshot({
        site: {
          ...baseSnapshot().site,
          peak_daily_balls_override: 10000,
          safety_buffer_rate: 0,
        },
        system: {
          nominal_collection_rate_bph: 1000,
          route_efficiency: 1,
          terrain_efficiency: 1,
          ball_density_efficiency: 1,
          productive_time_fraction: 1,
          scheduled_robot_hours_per_day: 6,
          robot_count: 1,
          actual_uptime: 1,
          design_uptime: 1,
        },
        labor_tasks: [simpleTask({ capacity_fit_override: undefined })],
      }),
      "expected",
    );
    expect(r.capacity.capacity_fit).toBeCloseTo(0.6, 6);
    expect(r.labor_tasks[0]!.effective_automation_rate).toBeCloseTo(0.6, 6);
    expect(r.warnings.some((w) => w.code === "capacity_warning")).toBe(true);
  });

  it("6. No uptime duplication: uptime .90 → .80 changes automation by exactly 8/9 once", () => {
    // Capacity is deliberately UNDER-provisioned (fit 0.6 < 1) so a duplicated
    // uptime multiplication inside capacity_fit could not hide behind saturation.
    const run = (uptime: number) =>
      calculateScenario(
        baseSnapshot({
          site: { ...baseSnapshot().site, peak_daily_balls_override: 10000, safety_buffer_rate: 0 },
          system: {
            nominal_collection_rate_bph: 1000,
            route_efficiency: 1,
            terrain_efficiency: 1,
            ball_density_efficiency: 1,
            productive_time_fraction: 1,
            scheduled_robot_hours_per_day: 6,
            robot_count: 1,
            actual_uptime: uptime,
            design_uptime: 1,
          },
          labor_tasks: [simpleTask({ system_uptime: uptime, capacity_fit_override: undefined })],
        }),
        "expected",
      );
    const at90 = run(0.9);
    const at80 = run(0.8);
    // capacity_fit excludes actual uptime, so it must be identical (and < 1)...
    expect(at90.capacity.capacity_fit).toBeCloseTo(0.6, 6);
    expect(at80.capacity.capacity_fit).toBe(at90.capacity.capacity_fit);
    // ...and effective automation scales by exactly 0.8/0.9 — applied once, in F-L05.
    const ratio =
      at80.labor_tasks[0]!.effective_automation_rate! /
      at90.labor_tasks[0]!.effective_automation_rate!;
    expect(ratio).toBeCloseTo(0.8 / 0.9, 6);
  });

  it("7. Retained asset: fixed cost with retirement_fraction 0 saves nothing", () => {
    const r = calculateScenario(
      baseSnapshot({
        labor_tasks: [],
        equipment_components: [
          {
            asset_id: "cart",
            cost_component_id: "lease",
            component_type: "fixed_contractual",
            annual_current_cash_cost: 12000,
            retirement_fraction: 0,
            contractual_avoidability_rate: 1,
          },
        ],
      }),
      "expected",
    );
    expect(r.outputs.equipment_cash_savings).toBe(0);
  });

  it("8. Variable fuel: usage_reduction 80% saves only the fuel component", () => {
    const r = calculateScenario(
      baseSnapshot({
        labor_tasks: [],
        equipment_components: [
          {
            asset_id: "cart",
            cost_component_id: "fuel",
            component_type: "variable",
            annual_current_cash_cost: 7200,
            usage_reduction_rate: 0.8,
          },
          {
            asset_id: "cart",
            cost_component_id: "insurance",
            component_type: "fixed_contractual",
            annual_current_cash_cost: 1500,
            retirement_fraction: 0,
            contractual_avoidability_rate: 0,
          },
        ],
      }),
      "expected",
    );
    expect(r.outputs.equipment_cash_savings).toBeCloseTo(5760, 2);
  });

  it("9. Sunk cost exclusion: historical purchase price never enters annual cash cost", () => {
    const r = calculateScenario(
      baseSnapshot({
        labor_tasks: [],
        equipment_components: [
          {
            asset_id: "cart",
            cost_component_id: "fuel",
            component_type: "variable",
            annual_current_cash_cost: 7200,
            usage_reduction_rate: 0.5,
            replacement_capex: 50000, // planned FUTURE replacement — not an annual cost
            replacement_year: 3,
            replacement_avoidance_rate: 1,
          },
        ],
      }),
      "expected",
    );
    // Current annual cash cost contains only the $7,200 fuel — no purchase price.
    expect(r.outputs.current_direct_operating_cash_cost).toBe(7200);
  });

  it("10. Replacement timing: avoided replacement capex appears exactly once, in its planned year", () => {
    const snapshot = baseSnapshot({
      site: { ...baseSnapshot().site, analysis_years: 5 },
      labor_tasks: [],
      equipment_components: [
        {
          asset_id: "cart",
          cost_component_id: "unit",
          component_type: "variable",
          annual_current_cash_cost: 0,
          usage_reduction_rate: 0,
          replacement_capex: 50000,
          replacement_year: 3,
          replacement_avoidance_rate: 1,
        },
      ],
    });
    const m = calculateScenario(snapshot, "expected").multi_year!;
    expect(m.core_cash_flow_by_year[3]).toBe(50000);
    expect(m.core_cash_flow_by_year[2]).toBe(0);
    expect(m.core_cash_flow_by_year[4]).toBe(0);
  });

  it("11. Revenue dedup: a group marked both missed sale and refund is blocked", () => {
    const snapshot = baseSnapshot({
      labor_tasks: [],
      revenue_event_groups: [
        {
          revenue_event_group_id: "g1",
          annual_missed_baskets_override: 100,
          price_per_basket: 14,
          variable_cost_per_basket: 1,
          annual_refund_count: 5,
          average_net_refund_cost: 50,
          stockout_reduction_override: 0.5,
        },
      ],
    });
    expect(() => calculateScenario(snapshot, "expected")).toThrow(RevenueDedupError);
  });

  it("12. Contribution margin: recovery uses price − variable cost, never gross price", () => {
    const r = calculateScenario(
      baseSnapshot({
        labor_tasks: [],
        revenue_event_groups: [
          {
            revenue_event_group_id: "g1",
            annual_missed_baskets_override: 100,
            price_per_basket: 14,
            variable_cost_per_basket: 1,
            stockout_reduction_override: 1,
          },
        ],
      }),
      "expected",
    );
    expect(r.outputs.recovered_contribution_margin).toBe(1300); // 100 × (14 − 1), NOT 1400
  });

  it("13. Vendor inclusion: maintenance included in vendor fee never re-enters customer ops", () => {
    const r = calculateScenario(
      baseSnapshot({
        labor_tasks: [],
        system_costs: {
          annual_planned_maintenance_cost: 5000,
          included_in_vendor_fee: { maintenance: true },
        },
      }),
      "expected",
    );
    expect(r.outputs.customer_incremental_operating_cost).toBe(0);
  });

  it("14. Negative economics: vendor fee above pre-fee value yields a negative net benefit", () => {
    const r = calculateScenario(
      baseSnapshot({
        labor_tasks: [simpleTask()],
        pricing: { annual_fixed_service_fee: 100000 },
      }),
      "expected",
    );
    expect(r.outputs.core_annual_customer_net_benefit).toBeLessThan(0);
    expect(r.outputs.core_annual_customer_net_benefit).toBe(20000 - 100000);
  });

  it("15. Missing vs zero: an unknown fuel cost is reported missing, never confirmed-zero", () => {
    const unknown = calculateScenario(
      baseSnapshot({
        labor_tasks: [],
        equipment_components: [
          {
            asset_id: "cart",
            cost_component_id: "fuel",
            component_type: "variable",
            annual_current_cash_cost: null, // UNKNOWN
            usage_reduction_rate: 0.9,
          },
        ],
      }),
      "expected",
    );
    expect(
      unknown.warnings.some(
        (w) => w.code === "missing_input" && w.message.includes("NOT a confirmed zero"),
      ),
    ).toBe(true);

    const confirmedZero = calculateScenario(
      baseSnapshot({
        labor_tasks: [],
        equipment_components: [
          {
            asset_id: "cart",
            cost_component_id: "fuel",
            component_type: "variable",
            annual_current_cash_cost: 0, // confirmed none
            usage_reduction_rate: 0.9,
          },
        ],
      }),
      "expected",
    );
    expect(
      confirmedZero.warnings.some(
        (w) => w.code === "missing_input" && w.entity_id === "cart.fuel",
      ),
    ).toBe(false);
  });

  it("16. Rounding: intermediate full precision retained; display rounds at presentation", () => {
    const r = calculateScenario(section11Snapshot(), "expected");
    expect(r.outputs.direct_gross_cash_savings).toBe(53866.75); // display, 2dp
    expect(r.raw.direct_gross_cash_savings).toMatch(/^53866\.747/); // full precision
  });

  it("17. Determinism: identical snapshot + model version ⇒ identical output", () => {
    const a = calculateAssessment(section11Snapshot());
    const b = calculateAssessment(section11Snapshot());
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("18. Scenario monotonicity: Conservative > Expected produces a warning", () => {
    const result = calculateAssessment(
      baseSnapshot({
        labor_tasks: [
          simpleTask({
            coverage_rate: {
              value_base: 0.5,
              value_low: 0.3,
              value_high: 0.9,
              // Deliberately mis-declared direction: conservative picks the HIGH value.
              scenario_direction: "higher_increases_cost",
            },
          }),
        ],
      }),
    );
    expect(
      result.cross_scenario_warnings.some((w) => w.code === "scenario_monotonicity_warning"),
    ).toBe(true);
  });

  it("19. No payback: cumulative cash flow never positive ⇒ no fake month", () => {
    const m = calculateScenario(
      baseSnapshot({
        labor_tasks: [simpleTask()],
        pricing: { annual_fixed_service_fee: 100000, hardware_purchase_price: 10000 },
      }),
      "expected",
    ).multi_year!;
    expect(m.payback_month).toBeNull();
    expect(m.payback_note).toBe("not achieved within analysis horizon");
  });

  it("20. Zero denominator: annual balls = 0 ⇒ per-1000 cost is null, not infinity", () => {
    const r = calculateScenario(
      baseSnapshot({
        site: { ...baseSnapshot().site, annual_balls_processed_override: 0 },
        labor_tasks: [simpleTask()],
      }),
      "expected",
    );
    expect(r.outputs.current_cost_per_1000_balls).toBeNull();
    expect(r.outputs.post_cost_per_1000_balls).toBeNull();
  });

  it("21. Performance-fee non-circularity: fee = rate × pre-fee eligible value", () => {
    const r = calculateScenario(
      baseSnapshot({
        labor_tasks: [simpleTask()], // $20,000 eligible pre-fee value
        pricing: { performance_fee_rate: 0.2 },
      }),
      "expected",
    );
    // 0.2 × 20,000 = 4,000 — NOT 0.2 × (20,000 − fee).
    expect(r.outputs.performance_fee).toBe(4000);
    expect(r.outputs.core_annual_customer_net_benefit).toBe(16000);
  });

  it("22. Core vs Expanded: risk and released capacity never enter core payback/NPV", () => {
    const snapshot = baseSnapshot({
      site: { ...baseSnapshot().site, analysis_years: 5, discount_rate: 0.1 },
      labor_tasks: [
        simpleTask({
          simple_cash_realization_factor: 0.5,
          shadow_value_per_hour: 15,
          redeployment_utilization: 1,
        }),
      ],
      risk_items: [
        {
          incident_type_id: "struck_by_ball",
          annual_incident_frequency: 2,
          average_cost_per_incident: 5000,
          risk_reduction_rate: 0.5,
        },
      ],
      pricing: { hardware_purchase_price: 10000 },
    });
    const r = calculateScenario(snapshot, "expected");
    const core = r.outputs.core_annual_customer_net_benefit!;
    const expanded = r.outputs.expanded_annual_customer_value!;
    // Expanded = core + risk (5,000) + capacity (500h × $15 = 7,500).
    expect(expanded - core).toBeCloseTo(12500, 2);
    // Core cash flows and NPV are built from CORE only.
    expect(r.multi_year!.core_net_benefit_by_year[1]).toBe(core);
    const annuity = (1 - 1.1 ** -5) / 0.1;
    expect(r.multi_year!.npv).toBeCloseTo(-10000 + core * annuity, 0);
  });
});

describe("governance gates beyond §14", () => {
  it("rejects candidate values from formal calculation (§10.2/§15.2)", () => {
    expect(() =>
      calculateScenario(
        baseSnapshot({
          labor_tasks: [
            simpleTask({
              coverage_rate: { value_base: 0.9, input_status: "candidate" },
            }),
          ],
        }),
        "expected",
      ),
    ).toThrow(/candidate/);
  });

  it("rejects unsupported model versions (§15.1)", () => {
    const snapshot = baseSnapshot();
    (snapshot as { model_version: string }).model_version = "0.9";
    expect(() => calculateScenario(snapshot, "expected")).toThrow(EngineValidationError);
  });

  it("blocks ambiguous overlap groups until a primary is chosen (§8.1)", () => {
    const snapshot = baseSnapshot({
      labor_tasks: [
        simpleTask({ task_id: "a", overlap_group: "g" }),
        simpleTask({ task_id: "b", overlap_group: "g" }),
      ],
    });
    expect(() => calculateScenario(snapshot, "expected")).toThrow(/overlap_group/);

    const withPrimary = baseSnapshot({
      labor_tasks: [
        simpleTask({ task_id: "a", overlap_group: "g", overlap_primary: true }),
        simpleTask({ task_id: "b", overlap_group: "g" }),
      ],
    });
    const r = calculateScenario(withPrimary, "expected");
    // Only the primary's 1000h × $20 counts once.
    expect(r.outputs.current_direct_operating_cash_cost).toBe(20000);
  });
});
