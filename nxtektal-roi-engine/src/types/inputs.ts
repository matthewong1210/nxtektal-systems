/** Input snapshot types — spec §5 variable data dictionary. Canonical names preserved. */

import type { NumericInput, Scenario } from "./evidence.js";

export type TaxTreatment = "cash_inclusive" | "recoverable_excluded";

/** §5.1 Assessment & site variables. */
export interface SiteInputs {
  site_id: string;
  assessment_date: string; // ISO date
  currency: string; // ISO 4217
  analysis_years?: number | null; // Full required; default 5
  discount_rate?: NumericInput; // no default; NPV omitted when missing
  operating_days_per_year: NumericInput;
  active_weeks_per_year?: NumericInput; // Quick default 52
  operating_hours_per_day: NumericInput;
  annual_baskets_sold?: NumericInput;
  avg_baskets_per_day?: NumericInput;
  peak_baskets_per_day?: NumericInput;
  avg_balls_per_basket?: NumericInput;
  annual_balls_processed_override?: NumericInput;
  peak_daily_balls_override?: NumericInput;
  safety_buffer_rate?: NumericInput;
  annual_paid_hours_per_fte?: NumericInput; // default 2080, display only
  tax_treatment: TaxTreatment;
}

export type TaskType = "current_task" | "new_system_task";

export type FrequencyBasis =
  | "per_day"
  | "per_week"
  | "per_month"
  | "per_year"
  | "interval_hours"
  | "per_event";

export type CashRealizationMethod = "overtime_first" | "simple_factor";

/** §5.2 Labor task variables. */
export interface LaborTaskInputs {
  task_id: string;
  task_name?: string;
  task_type: TaskType;
  overlap_group?: string | null;
  /** Chosen primary record within an overlap_group (§8.1: human must pick one). */
  overlap_primary?: boolean;
  frequency_basis: FrequencyBasis;
  frequency_value: NumericInput;
  cycles_per_day_override?: NumericInput;
  occurrences_per_year_override?: NumericInput;
  annual_event_count?: NumericInput;
  occurrences_per_event?: NumericInput; // per_event default 1
  duration_hours_per_occurrence: NumericInput;
  headcount: NumericInput;
  base_wage?: NumericInput;
  loaded_regular_rate_override?: NumericInput;
  payroll_burden_rate?: NumericInput;
  fixed_benefits_per_hour?: NumericInput;
  overtime_share?: NumericInput; // default 0
  overtime_multiplier?: NumericInput; // default 1.5
  payroll_tax_rate_on_overtime_premium?: NumericInput; // default 0
  loaded_overtime_rate_override?: NumericInput;
  avoidable_overtime_hours?: NumericInput;
  coverage_rate?: NumericInput; // current_task required/estimated
  system_uptime?: NumericInput; // current_task required/scenario value
  capacity_fit_override?: NumericInput; // task-level override of computed capacity_fit
  workflow_success_rate?: NumericInput;
  adoption_rate?: NumericInput;
  effective_automation_override?: NumericInput;
  cash_realization_method?: CashRealizationMethod; // required per current_task
  regular_cash_realization_factor?: NumericInput; // overtime_first required
  simple_cash_realization_factor?: NumericInput; // simple_factor required
  redeployment_utilization?: NumericInput;
  shadow_value_per_hour?: NumericInput;
}

/** §5.3 Demand & robot capacity variables. */
export interface SystemInputs {
  nominal_collection_rate_bph?: NumericInput;
  scheduled_robot_hours_per_day?: NumericInput;
  route_efficiency?: NumericInput;
  terrain_efficiency?: NumericInput;
  ball_density_efficiency?: NumericInput;
  productive_time_fraction?: NumericInput;
  design_uptime?: NumericInput;
  actual_uptime?: NumericInput;
  robot_count?: NumericInput; // required or computed via F-C04
  peak_hourly_ball_demand?: NumericInput;
  replenishment_window_hours?: NumericInput;
  starting_buffer_balls?: NumericInput;
  minimum_buffer_balls?: NumericInput;
  peak_window_collection_hours_per_robot?: NumericInput;
  capacity_fit_override?: NumericInput;
}

export type ComponentType =
  | "variable"
  | "fixed_contractual"
  | "periodic"
  | "replacement_capex";

/** §5.4 Existing equipment variables (one row per asset × cost component). */
export interface EquipmentComponentInputs {
  asset_id: string;
  cost_component_id: string;
  component_type: ComponentType;
  annual_current_cash_cost: NumericInput;
  usage_reduction_rate?: NumericInput; // variable required
  retirement_fraction?: NumericInput;
  contractual_avoidability_rate?: NumericInput;
  avoidability_override?: NumericInput;
  replacement_capex?: NumericInput;
  replacement_year?: number | null;
  replacement_avoidance_rate?: NumericInput;
  salvage_value?: NumericInput;
  salvage_year?: number | null;
}

/** §5.5 Ball loss variables (one row per loss cause). */
export interface BallLossCauseInputs {
  loss_cause_id: string;
  annual_current_lost_balls: NumericInput;
  landed_cost_per_ball: NumericInput;
  loss_area_coverage?: NumericInput;
  retrieval_success_rate?: NumericInput;
  loss_capacity_fit?: NumericInput;
  loss_uptime?: NumericInput;
  loss_adoption_rate?: NumericInput;
  loss_reduction_override?: NumericInput;
  new_system_damage_balls?: NumericInput; // default 0
}

/** §5.6 Stockout, refund & revenue variables (one row per event group). */
export interface RevenueEventGroupInputs {
  revenue_event_group_id: string;
  stockout_events_per_year?: NumericInput;
  affected_customers_per_event?: NumericInput;
  missed_baskets_per_customer?: NumericInput;
  annual_missed_baskets_override?: NumericInput;
  price_per_basket?: NumericInput; // required when group carries missed/incremental baskets
  variable_cost_per_basket?: NumericInput;
  contribution_margin_rate_override?: NumericInput;
  annual_refund_count?: NumericInput;
  average_net_refund_cost?: NumericInput;
  annual_service_credit_count?: NumericInput;
  average_service_credit_cost?: NumericInput;
  collection_reliability_factor?: NumericInput;
  inventory_visibility_factor?: NumericInput;
  operational_response_factor?: NumericInput;
  stockout_reduction_override?: NumericInput;
  incremental_baskets_not_already_counted?: NumericInput;
  /**
   * §8.4 / §14 "Revenue dedup": when a group carries BOTH missed-sale and refund
   * data, a human must choose which one counts. Absent → hard validation error.
   */
  dedup_resolution?: "missed_sale" | "refund" | "both_verified_distinct";
}

/** §5.7 Safety & risk variables. */
export interface RiskItemInputs {
  incident_type_id: string;
  annual_incident_frequency?: NumericInput;
  average_cost_per_incident?: NumericInput;
  risk_reduction_rate?: NumericInput;
}

/** §5.8 New-system cost components borne by the customer. */
export interface SystemCostInputs {
  energy_kwh_per_robot_day?: NumericInput;
  electricity_rate?: NumericInput;
  monthly_connectivity_cost?: NumericInput;
  annual_planned_maintenance_cost?: NumericInput;
  annual_expected_repair_cost?: NumericInput;
  annual_consumables_cost?: NumericInput;
  annual_incremental_insurance_cost?: NumericInput;
  annual_other_customer_ops_cost?: NumericInput;
  current_other_direct_cash_cost?: NumericInput; // default 0
  other_direct_cash_savings?: NumericInput; // default 0
  /** included_in_vendor_fee per component (§5.8): true ⇒ excluded from customer ops. */
  included_in_vendor_fee?: {
    energy?: boolean;
    connectivity?: boolean;
    maintenance?: boolean;
    repair?: boolean;
    consumables?: boolean;
    insurance?: boolean;
    other?: boolean;
  };
  new_system_expected_risk_cost?: NumericInput; // default 0 (§5.7)
  include_risk_in_expanded_value?: boolean; // default true
}

/** §5.8 Pricing / vendor fee structure. */
export interface PricingInputs {
  monthly_platform_fee?: NumericInput;
  monthly_fee_per_robot?: NumericInput;
  fee_per_ball?: NumericInput;
  fee_per_robot_hour?: NumericInput;
  annual_fixed_service_fee?: NumericInput;
  performance_fee_rate?: NumericInput;
  performance_fee_min?: NumericInput;
  performance_fee_cap?: NumericInput;
  hardware_purchase_price?: NumericInput;
  installation_cost?: NumericInput;
  site_preparation_cost?: NumericInput;
  integration_cost?: NumericInput;
  training_cost?: NumericInput;
  shipping_and_tax_cost?: NumericInput;
  initial_contingency_cost?: NumericInput;
  rebate_or_grant?: NumericInput;
  trade_in_proceeds?: NumericInput;
  target_customer_annual_net_benefit?: NumericInput;
  vendor_target_capture_rate?: NumericInput;
  target_payback_months?: NumericInput;
}

/** §5.8 Multi-year growth / escalation rates (Full mode, all optional). */
export interface GrowthInputs {
  vendor_fee_escalation_rate?: NumericInput;
  wage_growth_rate?: NumericInput;
  equipment_cost_inflation_rate?: NumericInput;
  ball_cost_inflation_rate?: NumericInput;
  basket_price_growth_rate?: NumericInput;
  demand_growth_rate?: NumericInput;
  energy_inflation_rate?: NumericInput;
  maintenance_growth_rate?: NumericInput;
  deployment_ramp_by_year?: (number | null)[];
  system_replacement_capex_by_year?: (number | null)[];
}

export type AssessmentMode = "quick_estimate" | "full_assessment";

/** Full immutable input snapshot for one assessment (spec §13, §15.1). */
export interface AssessmentSnapshot {
  model_version: string; // must be "1.0"
  assessment_id: string;
  mode?: AssessmentMode;
  site: SiteInputs;
  system?: SystemInputs;
  labor_tasks: LaborTaskInputs[];
  equipment_components?: EquipmentComponentInputs[];
  ball_loss_causes?: BallLossCauseInputs[];
  revenue_event_groups?: RevenueEventGroupInputs[];
  risk_items?: RiskItemInputs[];
  system_costs?: SystemCostInputs;
  pricing?: PricingInputs;
  growth?: GrowthInputs;
}

export type { Scenario };
