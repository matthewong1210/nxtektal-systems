/**
 * F-M01 year scaling: produce a resolved snapshot with year-t rates so every
 * component is genuinely recomputed. Interpretive choices are recorded in
 * docs/AMBIGUITIES.md under F-M01.
 */

import { Decimal, growthFactor } from "./decimal.js";
import type { ResolvedLaborTask, ResolvedSnapshot } from "./resolve.js";

const scale = (v: Decimal | null, f: Decimal): Decimal | null => (v === null ? null : v.mul(f));

function scaleTask(t: ResolvedLaborTask, wage: Decimal): ResolvedLaborTask {
  return {
    ...t,
    base_wage: scale(t.base_wage, wage),
    loaded_regular_rate_override: scale(t.loaded_regular_rate_override, wage),
    loaded_overtime_rate_override: scale(t.loaded_overtime_rate_override, wage),
    fixed_benefits_per_hour: scale(t.fixed_benefits_per_hour, wage),
    shadow_value_per_hour: scale(t.shadow_value_per_hour, wage),
  };
}

/** Year 1 returns the snapshot unchanged (all growth factors are (1+g)^(t-1)). */
export function scaleResolvedForYear(resolved: ResolvedSnapshot, t: number): ResolvedSnapshot {
  if (t === 1) return resolved;
  const g = resolved.growth;
  const wage = growthFactor(g.wage_growth_rate, t);
  const equip = growthFactor(g.equipment_cost_inflation_rate, t);
  const ball = growthFactor(g.ball_cost_inflation_rate, t);
  const price = growthFactor(g.basket_price_growth_rate, t);
  const demand = growthFactor(g.demand_growth_rate, t);
  const energy = growthFactor(g.energy_inflation_rate, t);
  const maint = growthFactor(g.maintenance_growth_rate, t);
  const vendor = growthFactor(g.vendor_fee_escalation_rate, t);

  return {
    ...resolved,
    site: {
      ...resolved.site,
      annual_baskets_sold: scale(resolved.site.annual_baskets_sold, demand),
      avg_baskets_per_day: scale(resolved.site.avg_baskets_per_day, demand),
      peak_baskets_per_day: scale(resolved.site.peak_baskets_per_day, demand),
      annual_balls_processed_override: scale(resolved.site.annual_balls_processed_override, demand),
      peak_daily_balls_override: scale(resolved.site.peak_daily_balls_override, demand),
    },
    system: {
      ...resolved.system,
      peak_hourly_ball_demand: scale(resolved.system.peak_hourly_ball_demand, demand),
    },
    current_tasks: resolved.current_tasks.map((task) => scaleTask(task, wage)),
    new_system_tasks: resolved.new_system_tasks.map((task) => scaleTask(task, wage)),
    equipment_components: resolved.equipment_components.map((c) => ({
      ...c,
      annual_current_cash_cost: scale(c.annual_current_cash_cost, equip),
    })),
    ball_loss_causes: resolved.ball_loss_causes.map((b) => ({
      ...b,
      landed_cost_per_ball: scale(b.landed_cost_per_ball, ball),
    })),
    revenue_event_groups: resolved.revenue_event_groups.map((v) => ({
      ...v,
      price_per_basket: scale(v.price_per_basket, price),
    })),
    system_costs: {
      ...resolved.system_costs,
      electricity_rate: scale(resolved.system_costs.electricity_rate, energy),
      annual_planned_maintenance_cost: scale(resolved.system_costs.annual_planned_maintenance_cost, maint),
      annual_expected_repair_cost: scale(resolved.system_costs.annual_expected_repair_cost, maint),
      annual_consumables_cost: scale(resolved.system_costs.annual_consumables_cost, maint),
    },
    pricing: {
      ...resolved.pricing,
      monthly_platform_fee: scale(resolved.pricing.monthly_platform_fee, vendor),
      monthly_fee_per_robot: scale(resolved.pricing.monthly_fee_per_robot, vendor),
      fee_per_ball: scale(resolved.pricing.fee_per_ball, vendor),
      fee_per_robot_hour: scale(resolved.pricing.fee_per_robot_hour, vendor),
      annual_fixed_service_fee: scale(resolved.pricing.annual_fixed_service_fee, vendor),
    },
  };
}
