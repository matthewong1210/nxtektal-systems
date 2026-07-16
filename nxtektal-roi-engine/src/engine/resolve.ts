/**
 * Snapshot resolution: raw AssessmentSnapshot → ResolvedSnapshot of Decimal|null
 * for one scenario, via the F-G01 ScenarioResolver. All formula modules operate
 * on resolved values only. Missing stays null (missing ≠ 0, spec §4).
 */

import { Decimal } from "./decimal.js";
import { ScenarioResolver } from "./scenario.js";
import type { Scenario } from "../types/evidence.js";
import type {
  AssessmentSnapshot,
  LaborTaskInputs,
  CashRealizationMethod,
  ComponentType,
  FrequencyBasis,
  TaskType,
} from "../types/inputs.js";

export interface ResolvedSite {
  currency: string;
  analysis_years: number;
  discount_rate: Decimal | null;
  operating_days_per_year: Decimal | null;
  active_weeks_per_year: Decimal;
  operating_hours_per_day: Decimal | null;
  annual_baskets_sold: Decimal | null;
  avg_baskets_per_day: Decimal | null;
  peak_baskets_per_day: Decimal | null;
  avg_balls_per_basket: Decimal | null;
  annual_balls_processed_override: Decimal | null;
  peak_daily_balls_override: Decimal | null;
  safety_buffer_rate: Decimal;
  annual_paid_hours_per_fte: Decimal;
}

export interface ResolvedLaborTask {
  task_id: string;
  task_type: TaskType;
  overlap_group: string | null;
  overlap_primary: boolean;
  frequency_basis: FrequencyBasis;
  frequency_value: Decimal | null;
  cycles_per_day_override: Decimal | null;
  occurrences_per_year_override: Decimal | null;
  annual_event_count: Decimal | null;
  occurrences_per_event: Decimal;
  duration_hours_per_occurrence: Decimal | null;
  headcount: Decimal | null;
  base_wage: Decimal | null;
  loaded_regular_rate_override: Decimal | null;
  payroll_burden_rate: Decimal | null;
  fixed_benefits_per_hour: Decimal | null;
  overtime_share: Decimal;
  overtime_multiplier: Decimal;
  payroll_tax_rate_on_overtime_premium: Decimal;
  loaded_overtime_rate_override: Decimal | null;
  avoidable_overtime_hours: Decimal | null;
  coverage_rate: Decimal | null;
  system_uptime: Decimal | null;
  capacity_fit_override: Decimal | null;
  workflow_success_rate: Decimal | null;
  adoption_rate: Decimal | null;
  effective_automation_override: Decimal | null;
  cash_realization_method: CashRealizationMethod | null;
  regular_cash_realization_factor: Decimal | null;
  simple_cash_realization_factor: Decimal | null;
  redeployment_utilization: Decimal | null;
  shadow_value_per_hour: Decimal | null;
}

export interface ResolvedSystem {
  nominal_collection_rate_bph: Decimal | null;
  scheduled_robot_hours_per_day: Decimal | null;
  route_efficiency: Decimal | null;
  terrain_efficiency: Decimal | null;
  ball_density_efficiency: Decimal | null;
  productive_time_fraction: Decimal | null;
  design_uptime: Decimal | null;
  actual_uptime: Decimal | null;
  robot_count: Decimal | null;
  peak_hourly_ball_demand: Decimal | null;
  replenishment_window_hours: Decimal | null;
  starting_buffer_balls: Decimal | null;
  minimum_buffer_balls: Decimal | null;
  peak_window_collection_hours_per_robot: Decimal | null;
  capacity_fit_override: Decimal | null;
}

export interface ResolvedEquipmentComponent {
  asset_id: string;
  cost_component_id: string;
  component_type: ComponentType;
  annual_current_cash_cost: Decimal | null;
  usage_reduction_rate: Decimal | null;
  retirement_fraction: Decimal | null;
  contractual_avoidability_rate: Decimal | null;
  avoidability_override: Decimal | null;
  replacement_capex: Decimal | null;
  replacement_year: number | null;
  replacement_avoidance_rate: Decimal | null;
  salvage_value: Decimal | null;
  salvage_year: number | null;
}

export interface ResolvedBallLossCause {
  loss_cause_id: string;
  annual_current_lost_balls: Decimal | null;
  landed_cost_per_ball: Decimal | null;
  loss_area_coverage: Decimal | null;
  retrieval_success_rate: Decimal | null;
  loss_capacity_fit: Decimal | null;
  loss_uptime: Decimal | null;
  loss_adoption_rate: Decimal | null;
  loss_reduction_override: Decimal | null;
  new_system_damage_balls: Decimal;
}

export interface ResolvedRevenueGroup {
  revenue_event_group_id: string;
  stockout_events_per_year: Decimal | null;
  affected_customers_per_event: Decimal | null;
  missed_baskets_per_customer: Decimal | null;
  annual_missed_baskets_override: Decimal | null;
  price_per_basket: Decimal | null;
  variable_cost_per_basket: Decimal | null;
  contribution_margin_rate_override: Decimal | null;
  annual_refund_count: Decimal | null;
  average_net_refund_cost: Decimal | null;
  annual_service_credit_count: Decimal | null;
  average_service_credit_cost: Decimal | null;
  collection_reliability_factor: Decimal | null;
  inventory_visibility_factor: Decimal | null;
  operational_response_factor: Decimal | null;
  stockout_reduction_override: Decimal | null;
  incremental_baskets_not_already_counted: Decimal | null;
  dedup_resolution: "missed_sale" | "refund" | "both_verified_distinct" | null;
}

export interface ResolvedRiskItem {
  incident_type_id: string;
  annual_incident_frequency: Decimal | null;
  average_cost_per_incident: Decimal | null;
  risk_reduction_rate: Decimal | null;
}

export interface ResolvedSystemCosts {
  energy_kwh_per_robot_day: Decimal | null;
  electricity_rate: Decimal | null;
  monthly_connectivity_cost: Decimal | null;
  annual_planned_maintenance_cost: Decimal | null;
  annual_expected_repair_cost: Decimal | null;
  annual_consumables_cost: Decimal | null;
  annual_incremental_insurance_cost: Decimal | null;
  annual_other_customer_ops_cost: Decimal | null;
  current_other_direct_cash_cost: Decimal;
  other_direct_cash_savings: Decimal;
  included_in_vendor_fee: {
    energy: boolean;
    connectivity: boolean;
    maintenance: boolean;
    repair: boolean;
    consumables: boolean;
    insurance: boolean;
    other: boolean;
  };
  new_system_expected_risk_cost: Decimal;
  include_risk_in_expanded_value: boolean;
}

export interface ResolvedPricing {
  monthly_platform_fee: Decimal | null;
  monthly_fee_per_robot: Decimal | null;
  fee_per_ball: Decimal | null;
  fee_per_robot_hour: Decimal | null;
  annual_fixed_service_fee: Decimal | null;
  performance_fee_rate: Decimal | null;
  performance_fee_min: Decimal | null;
  performance_fee_cap: Decimal | null;
  hardware_purchase_price: Decimal | null;
  installation_cost: Decimal | null;
  site_preparation_cost: Decimal | null;
  integration_cost: Decimal | null;
  training_cost: Decimal | null;
  shipping_and_tax_cost: Decimal | null;
  initial_contingency_cost: Decimal | null;
  rebate_or_grant: Decimal | null;
  trade_in_proceeds: Decimal | null;
  target_customer_annual_net_benefit: Decimal | null;
  vendor_target_capture_rate: Decimal | null;
  target_payback_months: Decimal | null;
}

export interface ResolvedGrowth {
  vendor_fee_escalation_rate: Decimal;
  wage_growth_rate: Decimal;
  equipment_cost_inflation_rate: Decimal;
  ball_cost_inflation_rate: Decimal;
  basket_price_growth_rate: Decimal;
  demand_growth_rate: Decimal;
  energy_inflation_rate: Decimal;
  maintenance_growth_rate: Decimal;
  deployment_ramp_by_year: (Decimal | null)[];
  system_replacement_capex_by_year: (Decimal | null)[];
}

export interface ResolvedSnapshot {
  model_version: string;
  assessment_id: string;
  scenario: Scenario;
  site: ResolvedSite;
  system: ResolvedSystem;
  current_tasks: ResolvedLaborTask[];
  new_system_tasks: ResolvedLaborTask[];
  equipment_components: ResolvedEquipmentComponent[];
  ball_loss_causes: ResolvedBallLossCause[];
  revenue_event_groups: ResolvedRevenueGroup[];
  risk_items: ResolvedRiskItem[];
  system_costs: ResolvedSystemCosts;
  pricing: ResolvedPricing;
  growth: ResolvedGrowth;
  resolver: ScenarioResolver;
}

function resolveLaborTask(r: ScenarioResolver, t: LaborTaskInputs): ResolvedLaborTask {
  const p = (f: string) => `labor_tasks[${t.task_id}].${f}`;
  return {
    task_id: t.task_id,
    task_type: t.task_type,
    overlap_group: t.overlap_group ?? null,
    overlap_primary: t.overlap_primary ?? false,
    frequency_basis: t.frequency_basis,
    frequency_value: r.resolve(p("frequency_value"), t.frequency_value),
    cycles_per_day_override: r.resolve(p("cycles_per_day_override"), t.cycles_per_day_override),
    occurrences_per_year_override: r.resolve(
      p("occurrences_per_year_override"),
      t.occurrences_per_year_override,
    ),
    annual_event_count: r.resolve(p("annual_event_count"), t.annual_event_count),
    occurrences_per_event: r.resolveWithDefault(
      p("occurrences_per_event"),
      t.occurrences_per_event,
      1, // spec §5.2: per_event default 1
    ),
    duration_hours_per_occurrence: r.resolve(
      p("duration_hours_per_occurrence"),
      t.duration_hours_per_occurrence,
    ),
    headcount: r.resolve(p("headcount"), t.headcount),
    base_wage: r.resolve(p("base_wage"), t.base_wage),
    loaded_regular_rate_override: r.resolve(
      p("loaded_regular_rate_override"),
      t.loaded_regular_rate_override,
    ),
    payroll_burden_rate: r.resolve(p("payroll_burden_rate"), t.payroll_burden_rate),
    fixed_benefits_per_hour: r.resolve(p("fixed_benefits_per_hour"), t.fixed_benefits_per_hour),
    overtime_share: r.resolveWithDefault(p("overtime_share"), t.overtime_share, 0),
    overtime_multiplier: r.resolveWithDefault(p("overtime_multiplier"), t.overtime_multiplier, 1.5),
    payroll_tax_rate_on_overtime_premium: r.resolveWithDefault(
      p("payroll_tax_rate_on_overtime_premium"),
      t.payroll_tax_rate_on_overtime_premium,
      0,
    ),
    loaded_overtime_rate_override: r.resolve(
      p("loaded_overtime_rate_override"),
      t.loaded_overtime_rate_override,
    ),
    avoidable_overtime_hours: r.resolve(p("avoidable_overtime_hours"), t.avoidable_overtime_hours),
    coverage_rate: r.resolve(p("coverage_rate"), t.coverage_rate),
    system_uptime: r.resolve(p("system_uptime"), t.system_uptime),
    capacity_fit_override: r.resolve(p("capacity_fit_override"), t.capacity_fit_override),
    workflow_success_rate: r.resolve(p("workflow_success_rate"), t.workflow_success_rate),
    adoption_rate: r.resolve(p("adoption_rate"), t.adoption_rate),
    effective_automation_override: r.resolve(
      p("effective_automation_override"),
      t.effective_automation_override,
    ),
    cash_realization_method: t.cash_realization_method ?? null,
    regular_cash_realization_factor: r.resolve(
      p("regular_cash_realization_factor"),
      t.regular_cash_realization_factor,
    ),
    simple_cash_realization_factor: r.resolve(
      p("simple_cash_realization_factor"),
      t.simple_cash_realization_factor,
    ),
    redeployment_utilization: r.resolve(p("redeployment_utilization"), t.redeployment_utilization),
    shadow_value_per_hour: r.resolve(p("shadow_value_per_hour"), t.shadow_value_per_hour),
  };
}

export function resolveSnapshot(
  snapshot: AssessmentSnapshot,
  scenario: Scenario,
  overrides?: Map<string, number>,
): ResolvedSnapshot {
  const r = new ScenarioResolver(scenario, overrides);
  const s = snapshot.site;
  const sys = snapshot.system ?? {};
  const sc = snapshot.system_costs ?? {};
  const pr = snapshot.pricing ?? {};
  const g = snapshot.growth ?? {};

  const site: ResolvedSite = {
    currency: s.currency,
    analysis_years: s.analysis_years ?? 5, // spec §5.1: Full default 5
    discount_rate: r.resolve("site.discount_rate", s.discount_rate),
    operating_days_per_year: r.resolve("site.operating_days_per_year", s.operating_days_per_year),
    active_weeks_per_year: r.resolveWithDefault(
      "site.active_weeks_per_year",
      s.active_weeks_per_year,
      52, // spec §5.1: Quick default 52
    ),
    operating_hours_per_day: r.resolve("site.operating_hours_per_day", s.operating_hours_per_day),
    annual_baskets_sold: r.resolve("site.annual_baskets_sold", s.annual_baskets_sold),
    avg_baskets_per_day: r.resolve("site.avg_baskets_per_day", s.avg_baskets_per_day),
    peak_baskets_per_day: r.resolve("site.peak_baskets_per_day", s.peak_baskets_per_day),
    avg_balls_per_basket: r.resolve("site.avg_balls_per_basket", s.avg_balls_per_basket),
    annual_balls_processed_override: r.resolve(
      "site.annual_balls_processed_override",
      s.annual_balls_processed_override,
    ),
    peak_daily_balls_override: r.resolve(
      "site.peak_daily_balls_override",
      s.peak_daily_balls_override,
    ),
    safety_buffer_rate: r.resolveWithDefault("site.safety_buffer_rate", s.safety_buffer_rate, 0),
    annual_paid_hours_per_fte: r.resolveWithDefault(
      "site.annual_paid_hours_per_fte",
      s.annual_paid_hours_per_fte,
      2080, // spec §5.1: display-only default
    ),
  };

  const system: ResolvedSystem = {
    nominal_collection_rate_bph: r.resolve(
      "system.nominal_collection_rate_bph",
      sys.nominal_collection_rate_bph,
    ),
    scheduled_robot_hours_per_day: r.resolve(
      "system.scheduled_robot_hours_per_day",
      sys.scheduled_robot_hours_per_day,
    ),
    route_efficiency: r.resolve("system.route_efficiency", sys.route_efficiency),
    terrain_efficiency: r.resolve("system.terrain_efficiency", sys.terrain_efficiency),
    ball_density_efficiency: r.resolve(
      "system.ball_density_efficiency",
      sys.ball_density_efficiency,
    ),
    productive_time_fraction: r.resolve(
      "system.productive_time_fraction",
      sys.productive_time_fraction,
    ),
    design_uptime: r.resolve("system.design_uptime", sys.design_uptime),
    actual_uptime: r.resolve("system.actual_uptime", sys.actual_uptime),
    robot_count: r.resolve("system.robot_count", sys.robot_count),
    peak_hourly_ball_demand: r.resolve(
      "system.peak_hourly_ball_demand",
      sys.peak_hourly_ball_demand,
    ),
    replenishment_window_hours: r.resolve(
      "system.replenishment_window_hours",
      sys.replenishment_window_hours,
    ),
    starting_buffer_balls: r.resolve("system.starting_buffer_balls", sys.starting_buffer_balls),
    minimum_buffer_balls: r.resolve("system.minimum_buffer_balls", sys.minimum_buffer_balls),
    peak_window_collection_hours_per_robot: r.resolve(
      "system.peak_window_collection_hours_per_robot",
      sys.peak_window_collection_hours_per_robot,
    ),
    capacity_fit_override: r.resolve("system.capacity_fit_override", sys.capacity_fit_override),
  };

  const tasks = snapshot.labor_tasks.map((t) => resolveLaborTask(r, t));

  const equipment_components: ResolvedEquipmentComponent[] = (
    snapshot.equipment_components ?? []
  ).map((e) => {
    const p = (f: string) => `equipment[${e.asset_id}.${e.cost_component_id}].${f}`;
    return {
      asset_id: e.asset_id,
      cost_component_id: e.cost_component_id,
      component_type: e.component_type,
      annual_current_cash_cost: r.resolve(p("annual_current_cash_cost"), e.annual_current_cash_cost),
      usage_reduction_rate: r.resolve(p("usage_reduction_rate"), e.usage_reduction_rate),
      retirement_fraction: r.resolve(p("retirement_fraction"), e.retirement_fraction),
      contractual_avoidability_rate: r.resolve(
        p("contractual_avoidability_rate"),
        e.contractual_avoidability_rate,
      ),
      avoidability_override: r.resolve(p("avoidability_override"), e.avoidability_override),
      replacement_capex: r.resolve(p("replacement_capex"), e.replacement_capex),
      replacement_year: e.replacement_year ?? null,
      replacement_avoidance_rate: r.resolve(
        p("replacement_avoidance_rate"),
        e.replacement_avoidance_rate,
      ),
      salvage_value: r.resolve(p("salvage_value"), e.salvage_value),
      salvage_year: e.salvage_year ?? null,
    };
  });

  const ball_loss_causes: ResolvedBallLossCause[] = (snapshot.ball_loss_causes ?? []).map((b) => {
    const p = (f: string) => `ball_loss[${b.loss_cause_id}].${f}`;
    return {
      loss_cause_id: b.loss_cause_id,
      annual_current_lost_balls: r.resolve(
        p("annual_current_lost_balls"),
        b.annual_current_lost_balls,
      ),
      landed_cost_per_ball: r.resolve(p("landed_cost_per_ball"), b.landed_cost_per_ball),
      loss_area_coverage: r.resolve(p("loss_area_coverage"), b.loss_area_coverage),
      retrieval_success_rate: r.resolve(p("retrieval_success_rate"), b.retrieval_success_rate),
      loss_capacity_fit: r.resolve(p("loss_capacity_fit"), b.loss_capacity_fit),
      loss_uptime: r.resolve(p("loss_uptime"), b.loss_uptime),
      loss_adoption_rate: r.resolve(p("loss_adoption_rate"), b.loss_adoption_rate),
      loss_reduction_override: r.resolve(p("loss_reduction_override"), b.loss_reduction_override),
      new_system_damage_balls: r.resolveWithDefault(
        p("new_system_damage_balls"),
        b.new_system_damage_balls,
        0, // spec §5.5: default 0
      ),
    };
  });

  const revenue_event_groups: ResolvedRevenueGroup[] = (snapshot.revenue_event_groups ?? []).map(
    (v) => {
      const p = (f: string) => `revenue[${v.revenue_event_group_id}].${f}`;
      return {
        revenue_event_group_id: v.revenue_event_group_id,
        stockout_events_per_year: r.resolve(
          p("stockout_events_per_year"),
          v.stockout_events_per_year,
        ),
        affected_customers_per_event: r.resolve(
          p("affected_customers_per_event"),
          v.affected_customers_per_event,
        ),
        missed_baskets_per_customer: r.resolve(
          p("missed_baskets_per_customer"),
          v.missed_baskets_per_customer,
        ),
        annual_missed_baskets_override: r.resolve(
          p("annual_missed_baskets_override"),
          v.annual_missed_baskets_override,
        ),
        price_per_basket: r.resolve(p("price_per_basket"), v.price_per_basket),
        variable_cost_per_basket: r.resolve(
          p("variable_cost_per_basket"),
          v.variable_cost_per_basket,
        ),
        contribution_margin_rate_override: r.resolve(
          p("contribution_margin_rate_override"),
          v.contribution_margin_rate_override,
        ),
        annual_refund_count: r.resolve(p("annual_refund_count"), v.annual_refund_count),
        average_net_refund_cost: r.resolve(p("average_net_refund_cost"), v.average_net_refund_cost),
        annual_service_credit_count: r.resolve(
          p("annual_service_credit_count"),
          v.annual_service_credit_count,
        ),
        average_service_credit_cost: r.resolve(
          p("average_service_credit_cost"),
          v.average_service_credit_cost,
        ),
        collection_reliability_factor: r.resolve(
          p("collection_reliability_factor"),
          v.collection_reliability_factor,
        ),
        inventory_visibility_factor: r.resolve(
          p("inventory_visibility_factor"),
          v.inventory_visibility_factor,
        ),
        operational_response_factor: r.resolve(
          p("operational_response_factor"),
          v.operational_response_factor,
        ),
        stockout_reduction_override: r.resolve(
          p("stockout_reduction_override"),
          v.stockout_reduction_override,
        ),
        incremental_baskets_not_already_counted: r.resolve(
          p("incremental_baskets_not_already_counted"),
          v.incremental_baskets_not_already_counted,
        ),
        dedup_resolution: v.dedup_resolution ?? null,
      };
    },
  );

  const risk_items: ResolvedRiskItem[] = (snapshot.risk_items ?? []).map((k) => {
    const p = (f: string) => `risk[${k.incident_type_id}].${f}`;
    return {
      incident_type_id: k.incident_type_id,
      annual_incident_frequency: r.resolve(
        p("annual_incident_frequency"),
        k.annual_incident_frequency,
      ),
      average_cost_per_incident: r.resolve(
        p("average_cost_per_incident"),
        k.average_cost_per_incident,
      ),
      risk_reduction_rate: r.resolve(p("risk_reduction_rate"), k.risk_reduction_rate),
    };
  });

  const system_costs: ResolvedSystemCosts = {
    energy_kwh_per_robot_day: r.resolve(
      "system_costs.energy_kwh_per_robot_day",
      sc.energy_kwh_per_robot_day,
    ),
    electricity_rate: r.resolve("system_costs.electricity_rate", sc.electricity_rate),
    monthly_connectivity_cost: r.resolve(
      "system_costs.monthly_connectivity_cost",
      sc.monthly_connectivity_cost,
    ),
    annual_planned_maintenance_cost: r.resolve(
      "system_costs.annual_planned_maintenance_cost",
      sc.annual_planned_maintenance_cost,
    ),
    annual_expected_repair_cost: r.resolve(
      "system_costs.annual_expected_repair_cost",
      sc.annual_expected_repair_cost,
    ),
    annual_consumables_cost: r.resolve(
      "system_costs.annual_consumables_cost",
      sc.annual_consumables_cost,
    ),
    annual_incremental_insurance_cost: r.resolve(
      "system_costs.annual_incremental_insurance_cost",
      sc.annual_incremental_insurance_cost,
    ),
    annual_other_customer_ops_cost: r.resolve(
      "system_costs.annual_other_customer_ops_cost",
      sc.annual_other_customer_ops_cost,
    ),
    current_other_direct_cash_cost: r.resolveWithDefault(
      "system_costs.current_other_direct_cash_cost",
      sc.current_other_direct_cash_cost,
      0, // spec §5.8: default 0
    ),
    other_direct_cash_savings: r.resolveWithDefault(
      "system_costs.other_direct_cash_savings",
      sc.other_direct_cash_savings,
      0, // spec §5.8: default 0
    ),
    included_in_vendor_fee: {
      energy: sc.included_in_vendor_fee?.energy ?? false,
      connectivity: sc.included_in_vendor_fee?.connectivity ?? false,
      maintenance: sc.included_in_vendor_fee?.maintenance ?? false,
      repair: sc.included_in_vendor_fee?.repair ?? false,
      consumables: sc.included_in_vendor_fee?.consumables ?? false,
      insurance: sc.included_in_vendor_fee?.insurance ?? false,
      other: sc.included_in_vendor_fee?.other ?? false,
    },
    new_system_expected_risk_cost: r.resolveWithDefault(
      "system_costs.new_system_expected_risk_cost",
      sc.new_system_expected_risk_cost,
      0, // spec §5.7: default 0
    ),
    include_risk_in_expanded_value: sc.include_risk_in_expanded_value ?? true,
  };

  const pricing: ResolvedPricing = {
    monthly_platform_fee: r.resolve("pricing.monthly_platform_fee", pr.monthly_platform_fee),
    monthly_fee_per_robot: r.resolve("pricing.monthly_fee_per_robot", pr.monthly_fee_per_robot),
    fee_per_ball: r.resolve("pricing.fee_per_ball", pr.fee_per_ball),
    fee_per_robot_hour: r.resolve("pricing.fee_per_robot_hour", pr.fee_per_robot_hour),
    annual_fixed_service_fee: r.resolve(
      "pricing.annual_fixed_service_fee",
      pr.annual_fixed_service_fee,
    ),
    performance_fee_rate: r.resolve("pricing.performance_fee_rate", pr.performance_fee_rate),
    performance_fee_min: r.resolve("pricing.performance_fee_min", pr.performance_fee_min),
    performance_fee_cap: r.resolve("pricing.performance_fee_cap", pr.performance_fee_cap),
    hardware_purchase_price: r.resolve(
      "pricing.hardware_purchase_price",
      pr.hardware_purchase_price,
    ),
    installation_cost: r.resolve("pricing.installation_cost", pr.installation_cost),
    site_preparation_cost: r.resolve("pricing.site_preparation_cost", pr.site_preparation_cost),
    integration_cost: r.resolve("pricing.integration_cost", pr.integration_cost),
    training_cost: r.resolve("pricing.training_cost", pr.training_cost),
    shipping_and_tax_cost: r.resolve("pricing.shipping_and_tax_cost", pr.shipping_and_tax_cost),
    initial_contingency_cost: r.resolve(
      "pricing.initial_contingency_cost",
      pr.initial_contingency_cost,
    ),
    rebate_or_grant: r.resolve("pricing.rebate_or_grant", pr.rebate_or_grant),
    trade_in_proceeds: r.resolve("pricing.trade_in_proceeds", pr.trade_in_proceeds),
    target_customer_annual_net_benefit: r.resolve(
      "pricing.target_customer_annual_net_benefit",
      pr.target_customer_annual_net_benefit,
    ),
    vendor_target_capture_rate: r.resolve(
      "pricing.vendor_target_capture_rate",
      pr.vendor_target_capture_rate,
    ),
    target_payback_months: r.resolve("pricing.target_payback_months", pr.target_payback_months),
  };

  const growth: ResolvedGrowth = {
    vendor_fee_escalation_rate: r.resolveWithDefault(
      "growth.vendor_fee_escalation_rate",
      g.vendor_fee_escalation_rate,
      0,
    ),
    wage_growth_rate: r.resolveWithDefault("growth.wage_growth_rate", g.wage_growth_rate, 0),
    equipment_cost_inflation_rate: r.resolveWithDefault(
      "growth.equipment_cost_inflation_rate",
      g.equipment_cost_inflation_rate,
      0,
    ),
    ball_cost_inflation_rate: r.resolveWithDefault(
      "growth.ball_cost_inflation_rate",
      g.ball_cost_inflation_rate,
      0,
    ),
    basket_price_growth_rate: r.resolveWithDefault(
      "growth.basket_price_growth_rate",
      g.basket_price_growth_rate,
      0,
    ),
    demand_growth_rate: r.resolveWithDefault("growth.demand_growth_rate", g.demand_growth_rate, 0),
    energy_inflation_rate: r.resolveWithDefault(
      "growth.energy_inflation_rate",
      g.energy_inflation_rate,
      0,
    ),
    maintenance_growth_rate: r.resolveWithDefault(
      "growth.maintenance_growth_rate",
      g.maintenance_growth_rate,
      0,
    ),
    deployment_ramp_by_year: (g.deployment_ramp_by_year ?? []).map((x) =>
      x == null ? null : new Decimal(x),
    ),
    system_replacement_capex_by_year: (g.system_replacement_capex_by_year ?? []).map((x) =>
      x == null ? null : new Decimal(x),
    ),
  };

  return {
    model_version: snapshot.model_version,
    assessment_id: snapshot.assessment_id,
    scenario,
    site,
    system,
    current_tasks: tasks.filter((t) => t.task_type === "current_task"),
    new_system_tasks: tasks.filter((t) => t.task_type === "new_system_task"),
    equipment_components,
    ball_loss_causes,
    revenue_event_groups,
    risk_items,
    system_costs,
    pricing,
    growth,
    resolver: r,
  };
}
