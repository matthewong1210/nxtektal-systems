/** zod validation schema for assessment input snapshots — units/ranges/required-by-mode. */

import { z } from "zod";

const scenarioEnum = z.enum(["conservative", "expected", "high_performance"]);

const evidenceValue = z.object({
  value_base: z.number().finite(),
  value_low: z.number().finite().nullable().optional(),
  value_high: z.number().finite().nullable().optional(),
  scenario_direction: z
    .enum(["higher_increases_benefit", "higher_increases_cost", "explicit_only"])
    .optional(),
  scenario_overrides: z.record(scenarioEnum, z.number().finite()).optional(),
  input_status: z
    .enum(["candidate", "confirmed", "estimated_allowed", "rejected", "superseded"])
    .optional(),
  source_type: z
    .enum([
      "verified_document",
      "system_export",
      "direct_measurement",
      "customer_reported",
      "observed_informal",
      "benchmark",
      "unknown",
    ])
    .optional(),
  source_reference: z.string().optional(),
  source_quote: z.string().optional(),
  confirmed_by: z.string().optional(),
  meeting_id: z.string().optional(),
  captured_at: z.string().optional(),
  input_value_id: z.string().optional(),
  valid_from: z.string().nullable().optional(),
  valid_to: z.string().nullable().optional(),
});

/** Missing ≠ 0: null/undefined mean unknown; explicit 0 means confirmed none. */
const numericInput = z.union([z.number().finite(), evidenceValue]).nullable().optional();
const requiredNumericInput = z.union([z.number().finite(), evidenceValue]);

const laborTask = z.object({
  task_id: z.string().min(1),
  task_name: z.string().optional(),
  task_type: z.enum(["current_task", "new_system_task"]),
  overlap_group: z.string().nullable().optional(),
  overlap_primary: z.boolean().optional(),
  frequency_basis: z.enum([
    "per_day",
    "per_week",
    "per_month",
    "per_year",
    "interval_hours",
    "per_event",
  ]),
  frequency_value: requiredNumericInput,
  cycles_per_day_override: numericInput,
  occurrences_per_year_override: numericInput,
  annual_event_count: numericInput,
  occurrences_per_event: numericInput,
  duration_hours_per_occurrence: requiredNumericInput,
  headcount: requiredNumericInput,
  base_wage: numericInput,
  loaded_regular_rate_override: numericInput,
  payroll_burden_rate: numericInput,
  fixed_benefits_per_hour: numericInput,
  overtime_share: numericInput,
  overtime_multiplier: numericInput,
  payroll_tax_rate_on_overtime_premium: numericInput,
  loaded_overtime_rate_override: numericInput,
  avoidable_overtime_hours: numericInput,
  coverage_rate: numericInput,
  system_uptime: numericInput,
  capacity_fit_override: numericInput,
  workflow_success_rate: numericInput,
  adoption_rate: numericInput,
  effective_automation_override: numericInput,
  cash_realization_method: z.enum(["overtime_first", "simple_factor"]).optional(),
  regular_cash_realization_factor: numericInput,
  simple_cash_realization_factor: numericInput,
  redeployment_utilization: numericInput,
  shadow_value_per_hour: numericInput,
});

export const assessmentSnapshotSchema = z.object({
  model_version: z.literal("1.0"),
  assessment_id: z.string().min(1),
  mode: z.enum(["quick_estimate", "full_assessment"]).optional(),
  site: z.object({
    site_id: z.string().min(1),
    assessment_date: z.string().min(1),
    currency: z.string().length(3),
    analysis_years: z.number().int().positive().nullable().optional(),
    discount_rate: numericInput,
    operating_days_per_year: requiredNumericInput,
    active_weeks_per_year: numericInput,
    operating_hours_per_day: requiredNumericInput,
    annual_baskets_sold: numericInput,
    avg_baskets_per_day: numericInput,
    peak_baskets_per_day: numericInput,
    avg_balls_per_basket: numericInput,
    annual_balls_processed_override: numericInput,
    peak_daily_balls_override: numericInput,
    safety_buffer_rate: numericInput,
    annual_paid_hours_per_fte: numericInput,
    tax_treatment: z.enum(["cash_inclusive", "recoverable_excluded"]),
  }),
  system: z
    .object({
      nominal_collection_rate_bph: numericInput,
      scheduled_robot_hours_per_day: numericInput,
      route_efficiency: numericInput,
      terrain_efficiency: numericInput,
      ball_density_efficiency: numericInput,
      productive_time_fraction: numericInput,
      design_uptime: numericInput,
      actual_uptime: numericInput,
      robot_count: numericInput,
      peak_hourly_ball_demand: numericInput,
      replenishment_window_hours: numericInput,
      starting_buffer_balls: numericInput,
      minimum_buffer_balls: numericInput,
      peak_window_collection_hours_per_robot: numericInput,
      capacity_fit_override: numericInput,
    })
    .optional(),
  labor_tasks: z.array(laborTask),
  equipment_components: z
    .array(
      z.object({
        asset_id: z.string().min(1),
        cost_component_id: z.string().min(1),
        component_type: z.enum(["variable", "fixed_contractual", "periodic", "replacement_capex"]),
        annual_current_cash_cost: numericInput,
        usage_reduction_rate: numericInput,
        retirement_fraction: numericInput,
        contractual_avoidability_rate: numericInput,
        avoidability_override: numericInput,
        replacement_capex: numericInput,
        replacement_year: z.number().int().nullable().optional(),
        replacement_avoidance_rate: numericInput,
        salvage_value: numericInput,
        salvage_year: z.number().int().nullable().optional(),
      }),
    )
    .optional(),
  ball_loss_causes: z
    .array(
      z.object({
        loss_cause_id: z.string().min(1),
        annual_current_lost_balls: numericInput,
        landed_cost_per_ball: numericInput,
        loss_area_coverage: numericInput,
        retrieval_success_rate: numericInput,
        loss_capacity_fit: numericInput,
        loss_uptime: numericInput,
        loss_adoption_rate: numericInput,
        loss_reduction_override: numericInput,
        new_system_damage_balls: numericInput,
      }),
    )
    .optional(),
  revenue_event_groups: z
    .array(
      z.object({
        revenue_event_group_id: z.string().min(1),
        stockout_events_per_year: numericInput,
        affected_customers_per_event: numericInput,
        missed_baskets_per_customer: numericInput,
        annual_missed_baskets_override: numericInput,
        price_per_basket: numericInput,
        variable_cost_per_basket: numericInput,
        contribution_margin_rate_override: numericInput,
        annual_refund_count: numericInput,
        average_net_refund_cost: numericInput,
        annual_service_credit_count: numericInput,
        average_service_credit_cost: numericInput,
        collection_reliability_factor: numericInput,
        inventory_visibility_factor: numericInput,
        operational_response_factor: numericInput,
        stockout_reduction_override: numericInput,
        incremental_baskets_not_already_counted: numericInput,
        dedup_resolution: z.enum(["missed_sale", "refund", "both_verified_distinct"]).optional(),
      }),
    )
    .optional(),
  risk_items: z
    .array(
      z.object({
        incident_type_id: z.string().min(1),
        annual_incident_frequency: numericInput,
        average_cost_per_incident: numericInput,
        risk_reduction_rate: numericInput,
      }),
    )
    .optional(),
  system_costs: z
    .object({
      energy_kwh_per_robot_day: numericInput,
      electricity_rate: numericInput,
      monthly_connectivity_cost: numericInput,
      annual_planned_maintenance_cost: numericInput,
      annual_expected_repair_cost: numericInput,
      annual_consumables_cost: numericInput,
      annual_incremental_insurance_cost: numericInput,
      annual_other_customer_ops_cost: numericInput,
      current_other_direct_cash_cost: numericInput,
      other_direct_cash_savings: numericInput,
      included_in_vendor_fee: z
        .object({
          energy: z.boolean().optional(),
          connectivity: z.boolean().optional(),
          maintenance: z.boolean().optional(),
          repair: z.boolean().optional(),
          consumables: z.boolean().optional(),
          insurance: z.boolean().optional(),
          other: z.boolean().optional(),
        })
        .optional(),
      new_system_expected_risk_cost: numericInput,
      include_risk_in_expanded_value: z.boolean().optional(),
    })
    .optional(),
  pricing: z
    .object({
      monthly_platform_fee: numericInput,
      monthly_fee_per_robot: numericInput,
      fee_per_ball: numericInput,
      fee_per_robot_hour: numericInput,
      annual_fixed_service_fee: numericInput,
      performance_fee_rate: numericInput,
      performance_fee_min: numericInput,
      performance_fee_cap: numericInput,
      hardware_purchase_price: numericInput,
      installation_cost: numericInput,
      site_preparation_cost: numericInput,
      integration_cost: numericInput,
      training_cost: numericInput,
      shipping_and_tax_cost: numericInput,
      initial_contingency_cost: numericInput,
      rebate_or_grant: numericInput,
      trade_in_proceeds: numericInput,
      target_customer_annual_net_benefit: numericInput,
      vendor_target_capture_rate: numericInput,
      target_payback_months: numericInput,
    })
    .optional(),
  growth: z
    .object({
      vendor_fee_escalation_rate: numericInput,
      wage_growth_rate: numericInput,
      equipment_cost_inflation_rate: numericInput,
      ball_cost_inflation_rate: numericInput,
      basket_price_growth_rate: numericInput,
      demand_growth_rate: numericInput,
      energy_inflation_rate: numericInput,
      maintenance_growth_rate: numericInput,
      deployment_ramp_by_year: z.array(z.number().nullable()).optional(),
      system_replacement_capex_by_year: z.array(z.number().nullable()).optional(),
    })
    .optional(),
});

export type ValidatedSnapshot = z.infer<typeof assessmentSnapshotSchema>;
