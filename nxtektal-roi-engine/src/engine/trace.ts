import { Decimal } from "./decimal.js";

export interface TraceEntry {
  formula_id: string;
  entity_id: string | null;
  inputs: (number | string | null)[];
  result: number | null;
  /** Full-precision decimal string of the result — audit-grade, reproduces the formula output (§15.1). */
  result_raw: string | null;
}

/** Collects formula traces for auditable output (spec §13, §15.1). */
export class TraceCollector {
  entries: TraceEntry[] = [];

  add(
    formulaId: string,
    entityId: string | null,
    inputs: (Decimal | number | string | null | undefined)[],
    result: Decimal | number | null,
  ): void {
    this.entries.push({
      formula_id: formulaId,
      entity_id: entityId,
      inputs: inputs.map((v) =>
        v === null || v === undefined ? null : v instanceof Decimal ? Number(v) : v,
      ),
      result: result === null ? null : result instanceof Decimal ? Number(result) : result,
      result_raw: result === null ? null : result.toString(),
    });
  }
}

export type WarningCode =
  | "capacity_warning"
  | "overlap_non_primary_excluded"
  | "capacity_fit_assumed_estimated"
  | "scenario_monotonicity_warning"
  | "payback_not_achieved"
  | "missing_input"
  | "assumed_factor_1"
  | "avoidability_assumed_0"
  | "negative_initial_investment"
  | "irr_not_defined"
  | "estimated_input"
  | "incomplete_task"
  | "approximate_payback";

export interface EngineWarning {
  code: WarningCode;
  entity_id: string | null;
  message: string;
}

export class WarningCollector {
  warnings: EngineWarning[] = [];
  add(code: WarningCode, entityId: string | null, message: string): void {
    this.warnings.push({ code, entity_id: entityId, message });
  }
}
