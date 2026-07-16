/** F-G01 scenario value selection + input registry for sensitivity/evidence scoring. */

import { D, Decimal } from "./decimal.js";
import {
  type NumericInput,
  type Scenario,
  type InputStatus,
  type SourceType,
  isEvidenceValue,
} from "../types/evidence.js";

export interface RegisteredInput {
  path: string;
  value_base: number;
  value_low: number | null;
  value_high: number | null;
  input_status: InputStatus;
  source_type: SourceType;
  has_range: boolean;
}

export class InputRegistryError extends Error {}

/**
 * Resolves NumericInput fields to Decimal|null for one scenario and records
 * evidence metadata. `overrides` (path → value) supports one-at-a-time
 * sensitivity recomputation (F-G02).
 */
export class ScenarioResolver {
  readonly scenario: Scenario;
  readonly registry = new Map<string, RegisteredInput>();
  /** Every requested path → whether a usable value was present (F-G05 completeness). */
  readonly requested = new Map<string, boolean>();
  private overrides: Map<string, number>;

  constructor(scenario: Scenario, overrides?: Map<string, number>) {
    this.scenario = scenario;
    this.overrides = overrides ?? new Map();
  }

  /**
   * Resolve an input for this resolver's scenario. Returns null for missing
   * (missing ≠ 0 — spec §4). Candidate/rejected/superseded values are refused
   * from formal calculation (spec §7.3, §10.2, §15.2).
   */
  resolve(path: string, input: NumericInput): Decimal | null {
    const override = this.overrides.get(path);

    if (input === null || input === undefined) {
      this.requested.set(path, false);
      return null;
    }
    this.requested.set(path, true);

    if (typeof input === "number") {
      this.registry.set(path, {
        path,
        value_base: input,
        value_low: null,
        value_high: null,
        input_status: "confirmed",
        source_type: "unknown",
        has_range: false,
      });
      return D(override ?? input);
    }

    if (!isEvidenceValue(input)) return null;

    const status: InputStatus = input.input_status ?? "confirmed";
    if (status === "candidate" || status === "rejected" || status === "superseded") {
      throw new InputRegistryError(
        `Input "${path}" has status "${status}" and cannot enter a formal calculation. ` +
          `Confirm it or mark it estimated_allowed first (spec §7.3/§10.2).`,
      );
    }

    this.registry.set(path, {
      path,
      value_base: input.value_base,
      value_low: input.value_low ?? null,
      value_high: input.value_high ?? null,
      input_status: status,
      source_type: input.source_type ?? "unknown",
      has_range: input.value_low != null || input.value_high != null,
    });

    if (override !== undefined) return D(override);

    // F-G01 — explicit scenario override has top priority.
    const explicit = input.scenario_overrides?.[this.scenario];
    if (explicit !== undefined && explicit !== null) return D(explicit);

    const direction = input.scenario_direction ?? "explicit_only";
    if (this.scenario === "expected") return D(input.value_base);

    if (direction === "higher_increases_benefit") {
      if (this.scenario === "conservative") return D(input.value_low ?? input.value_base);
      return D(input.value_high ?? input.value_base); // high_performance
    }
    if (direction === "higher_increases_cost") {
      if (this.scenario === "conservative") return D(input.value_high ?? input.value_base);
      return D(input.value_low ?? input.value_base); // high_performance
    }

    // explicit_only without an explicit override: fall back to base.
    // A range without direction cannot be auto-assigned to scenarios (F-G01).
    return D(input.value_base);
  }

  /** Resolve with a default when the input is missing. Use ONLY where the spec names a default. */
  resolveWithDefault(path: string, input: NumericInput, specDefault: number): Decimal {
    const v = this.resolve(path, input);
    return v === null ? D(specDefault) : v;
  }
}
