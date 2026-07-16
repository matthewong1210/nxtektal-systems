/** F-G02..F-G06 — one-at-a-time sensitivity, evidence quality, completeness, confidence. */

import { Decimal, ZERO } from "./decimal.js";
import type { ScenarioResolver } from "./scenario.js";
import type { SourceType } from "../types/evidence.js";
import type { ConfidenceOutputs, SensitivityEntry } from "../types/outputs.js";

/** F-G04 — locked source score table. */
export const SOURCE_SCORES: Record<SourceType, number> = {
  verified_document: 1.0,
  system_export: 1.0,
  direct_measurement: 0.9,
  customer_reported: 0.75,
  observed_informal: 0.65,
  benchmark: 0.4,
  unknown: 0.0,
};

export interface CoreRun {
  core: Decimal | null;
  resolver: ScenarioResolver;
}

export type CoreRunner = (overrides?: Map<string, number>) => CoreRun;

export function computeSensitivityAndConfidence(runCore: CoreRunner): {
  sensitivity: SensitivityEntry[];
  confidence: ConfidenceOutputs;
} {
  const baseline = runCore();
  const registry = baseline.resolver.registry;
  const requested = baseline.resolver.requested;

  // F-G02 — one-at-a-time impact: |core(high) − core(low)| with others at Expected.
  const impacts = new Map<string, Decimal>();
  for (const [path, entry] of registry) {
    if (!entry.has_range) continue;
    const low = entry.value_low ?? entry.value_base;
    const high = entry.value_high ?? entry.value_base;
    const coreLow = runCore(new Map([[path, low]])).core;
    const coreHigh = runCore(new Map([[path, high]])).core;
    if (coreLow === null || coreHigh === null) continue;
    impacts.set(path, coreHigh.sub(coreLow).abs());
  }

  // Scored inputs = every requested path (present or missing).
  const scoredPaths = [...requested.keys()];
  let totalImpact = ZERO;
  for (const v of impacts.values()) totalImpact = totalImpact.add(v);

  // F-G03 — weights sum to 1 (equal weights when no impacts).
  const weights = new Map<string, Decimal>();
  if (totalImpact.gt(ZERO)) {
    for (const path of scoredPaths) {
      weights.set(path, (impacts.get(path) ?? ZERO).div(totalImpact));
    }
  } else {
    const equal = scoredPaths.length > 0 ? new Decimal(1).div(scoredPaths.length) : ZERO;
    for (const path of scoredPaths) weights.set(path, equal);
  }

  // F-G04 / F-G05 — evidence quality and completeness.
  let quality = ZERO;
  let completeness = ZERO;
  let confirmedCount = 0;
  let estimatedCount = 0;
  for (const path of scoredPaths) {
    const w = weights.get(path) ?? ZERO;
    const present = requested.get(path) ?? false;
    const entry = registry.get(path);
    if (present && entry) {
      quality = quality.add(w.mul(SOURCE_SCORES[entry.source_type]));
      completeness = completeness.add(w);
      if (entry.input_status === "confirmed") confirmedCount++;
      if (entry.input_status === "estimated_allowed") estimatedCount++;
    }
  }

  // F-G06 — overall confidence and internal grade (not a probability guarantee).
  const overall = quality.mul(completeness);
  const overallNum = Number(overall.toDecimalPlaces(4));
  const grade = overallNum >= 0.85 ? "A" : overallNum >= 0.7 ? "B" : overallNum >= 0.5 ? "C" : "D";

  const sensitivity: SensitivityEntry[] = [...impacts.entries()]
    .map(([path, impact]) => {
      const entry = registry.get(path)!;
      return {
        input_path: path,
        value_low: entry.value_low,
        value_high: entry.value_high,
        impact_delta: Number(impact.toDecimalPlaces(2)),
        impact_weight: Number((weights.get(path) ?? ZERO).toDecimalPlaces(4)),
        source_type: entry.source_type,
        input_status: entry.input_status,
      };
    })
    .sort((a, b) => b.impact_delta - a.impact_delta);

  return {
    sensitivity,
    confidence: {
      evidence_quality_score: Number(quality.toDecimalPlaces(4)),
      data_completeness_score: Number(completeness.toDecimalPlaces(4)),
      overall_model_confidence: overallNum,
      grade,
      confirmed_input_count: confirmedCount,
      estimated_input_count: estimatedCount,
    },
  };
}
