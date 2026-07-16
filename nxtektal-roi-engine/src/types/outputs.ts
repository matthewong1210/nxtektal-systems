/** Output types — spec §13 output JSON, §15.3 report layer. */

import type { TraceEntry, EngineWarning } from "../engine/trace.js";
import type { Scenario } from "./evidence.js";

export interface LaborTaskResult {
  task_id: string;
  occurrences_year: number | null;
  annual_task_person_hours: number | null;
  current_task_activity_cost: number | null;
  effective_automation_rate: number | null;
  technical_hours_removed: number | null;
  residual_task_person_hours: number | null;
  technical_labor_value_released: number | null;
  cash_labor_savings: number | null;
  cash_saved_hours: number | null;
  released_capacity_hours: number | null;
  released_capacity_value: number | null;
  incomplete: boolean;
}

export interface CapacityResult {
  annual_balls_processed: number | null;
  avg_daily_ball_demand: number | null;
  peak_daily_ball_demand: number | null;
  target_daily_ball_throughput: number | null;
  operational_collection_rate_bph: number | null;
  operational_daily_capacity_per_robot: number | null;
  required_robot_count: number | null;
  robot_count: number | null;
  daily_capacity_fit: number | null;
  peak_window_capacity_fit: number | null;
  capacity_fit: number | null;
  robot_utilization: number | null;
  annual_robot_scheduled_hours: number | null;
}

export interface CoreOutputs {
  current_direct_operating_cash_cost: number | null;
  post_direct_operating_cash_cost: number | null;
  direct_gross_cash_savings: number | null;
  net_direct_cash_savings_after_vendor: number | null;
  total_revenue_recovery_value: number | null;
  customer_incremental_operating_cost: number | null;
  annual_vendor_recurring_fee: number | null;
  base_vendor_recurring_fee: number | null;
  performance_fee: number | null;
  initial_customer_investment: number | null;
  core_value_pre_vendor_fee: number | null;
  core_annual_customer_net_benefit: number | null;
  expanded_annual_customer_value: number | null;
  risk_reduction_value: number | null;
  released_capacity_value_total: number | null;
  released_capacity_hours_total: number | null;
  technical_hours_removed_total: number | null;
  cash_saved_hours_total: number | null;
  cash_labor_savings_total: number | null;
  equipment_cash_savings: number | null;
  ball_replacement_cash_savings: number | null;
  refund_and_credit_cash_savings: number | null;
  recovered_contribution_margin: number | null;
  incremental_sales_margin: number | null;
  direct_cost_reduction_rate: number | null;
  current_daily_direct_cost: number | null;
  post_daily_direct_cost: number | null;
  current_monthly_direct_cost: number | null;
  post_monthly_direct_cost: number | null;
  monthly_core_net_benefit: number | null;
  current_cost_per_1000_balls: number | null;
  post_cost_per_1000_balls: number | null;
  net_benefit_per_1000_balls: number | null;
  current_cost_per_basket: number | null;
  post_cost_per_basket: number | null;
  vendor_value_capture_rate: number | null;
  customer_value_retention_rate: number | null;
  technical_fte_released: number | null;
  cash_fte_avoided: number | null;
}

export interface MultiYearOutputs {
  analysis_years: number;
  /** Index 0 = year 0 (t0). */
  core_cash_flow_by_year: (number | null)[];
  core_net_benefit_by_year: (number | null)[];
  cumulative_core_net_benefit: number | null;
  npv: number | null;
  irr: number | null;
  irr_note: string | null;
  payback_month: number | null;
  payback_note: string | null;
  approximate_payback_months: number | null;
  simple_roi: number | null;
  bcr: number | null;
  first_year_cash_roi: number | null;
}

export interface PricingOutputs {
  break_even_annual_vendor_fee: number | null;
  max_vendor_fee_for_target_savings: number | null;
  annual_vendor_fee_by_value_share: number | null;
  max_hardware_purchase_price: number | null;
  max_fixed_monthly_fee: number | null;
}

export interface SensitivityEntry {
  input_path: string;
  value_low: number | null;
  value_high: number | null;
  impact_delta: number;
  impact_weight: number;
  source_type: string;
  input_status: string;
}

export interface ConfidenceOutputs {
  evidence_quality_score: number | null;
  data_completeness_score: number | null;
  overall_model_confidence: number | null;
  grade: "A" | "B" | "C" | "D" | null;
  confirmed_input_count: number;
  estimated_input_count: number;
}

export interface ScenarioResult {
  model_version: string;
  assessment_id: string;
  scenario: Scenario;
  currency: string;
  outputs: CoreOutputs;
  capacity: CapacityResult;
  labor_tasks: LaborTaskResult[];
  multi_year: MultiYearOutputs | null;
  pricing: PricingOutputs;
  warnings: EngineWarning[];
  formula_trace: TraceEntry[];
  /** Full-precision decimal strings of core outputs, for determinism/audit. */
  raw: Record<string, string | null>;
}

export interface AssessmentResult {
  model_version: string;
  assessment_id: string;
  currency: string;
  scenarios: Record<Scenario, ScenarioResult>;
  sensitivity: SensitivityEntry[];
  confidence: ConfidenceOutputs;
  cross_scenario_warnings: EngineWarning[];
}
