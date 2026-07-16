/** Evidence & version metadata — spec §5.9, §7. */

export type InputStatus =
  | "candidate"
  | "confirmed"
  | "estimated_allowed"
  | "rejected"
  | "superseded";

export type SourceType =
  | "verified_document"
  | "system_export"
  | "direct_measurement"
  | "customer_reported"
  | "observed_informal"
  | "benchmark"
  | "unknown";

export type ScenarioDirection =
  | "higher_increases_benefit"
  | "higher_increases_cost"
  | "explicit_only";

export type Scenario = "conservative" | "expected" | "high_performance";

export const SCENARIOS: Scenario[] = ["conservative", "expected", "high_performance"];

/**
 * A numeric input carrying evidence metadata and an optional low/base/high range.
 * Plain numbers are accepted anywhere an EvidenceValue is (treated as base-only,
 * status "confirmed", source "unknown" unless field metadata says otherwise).
 */
export interface EvidenceValue {
  value_base: number;
  value_low?: number | null;
  value_high?: number | null;
  scenario_direction?: ScenarioDirection;
  /** Explicit per-scenario overrides — F-G01 gives these top priority. */
  scenario_overrides?: Partial<Record<Scenario, number>>;
  input_status?: InputStatus;
  source_type?: SourceType;
  source_reference?: string;
  source_quote?: string;
  confirmed_by?: string;
  meeting_id?: string;
  captured_at?: string;
  input_value_id?: string;
  valid_from?: string | null;
  valid_to?: string | null;
}

/**
 * Missing ≠ 0 (spec §4): `null`/`undefined` mean unknown; `0` means confirmed none.
 */
export type NumericInput = number | EvidenceValue | null | undefined;

export function isEvidenceValue(v: NumericInput): v is EvidenceValue {
  return typeof v === "object" && v !== null && "value_base" in v;
}
