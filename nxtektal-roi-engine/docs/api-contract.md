# NXTektal ROI Engine — API Contract (model_version 1.0)

The engine is a pure, deterministic calculation package. Web, iPad, report, and
API layers must all call this package — never re-implement formulas (spec §15.1).

## TypeScript API

```ts
import {
  calculateAssessment,   // all 3 scenarios + sensitivity + confidence
  calculateScenario,     // one scenario
  buildQuickEstimateSnapshot, // §9 Quick Estimate → Full snapshot mapping
  assessmentSnapshotSchema,   // zod input validation
} from "@nxtektal/roi-engine";

const parsed = assessmentSnapshotSchema.parse(rawJson); // shape/unit validation
const result = calculateAssessment(parsed);             // deterministic
```

### Errors (hard validation — spec §8, §10.2, §15.2)

| Error | Meaning |
|---|---|
| `zod.ZodError` | Snapshot shape/type/enum violation |
| `EngineValidationError` | model_version mismatch, ambiguous overlap_group, inconsistent cash-realization method |
| `RevenueDedupError` | Event group marked both missed sale and refund without `dedup_resolution` |
| `InputRegistryError` | `candidate` / `rejected` / `superseded` value submitted to a formal calculation |

## Suggested HTTP binding

```
POST /v1/assessments/{assessment_id}/calculate
  body:  AssessmentSnapshot        (schema: assessmentSnapshotSchema; model_version "1.0")
  200:   AssessmentResult          (three scenarios, sensitivity, confidence)
  422:   { error, formula_id?, entity_id? }   (hard validation failures above)

POST /v1/quick-estimate
  body:  QuickEstimateInputs       (§9 first-screen fields)
  200:   AssessmentResult          (same engine; inputs marked estimated_allowed)
```

## Contract guarantees

1. **Determinism** — identical snapshot + `model_version` ⇒ bitwise-identical numeric output (`raw.*` full-precision strings prove it; test-enforced).
2. **Formula traces** — every response carries `formula_trace[]` entries `{formula_id, entity_id, inputs, result}`.
3. **Missing ≠ 0** — `null` inputs stay unknown; results affected by missing data carry `missing_input` warnings, never silent zeros.
4. **Negative economics allowed** — net benefits are never clamped for presentation.
5. **Null over infinity** — zero-denominator ratios/unit costs are `null`.
6. **Core vs Expanded separation** — `core_annual_customer_net_benefit` never contains risk or released-capacity value; `expanded_annual_customer_value` is reported separately.
7. **Versioning** — the engine refuses snapshots whose `model_version` it does not implement; formula changes ship as a new model_version, old results stay recomputable.

## Output shape (abridged — see `examples/section11-output.json`)

```jsonc
{
  "model_version": "1.0",
  "scenario": "expected",
  "currency": "USD",
  "outputs": { "core_annual_customer_net_benefit": 33156.75, /* … F-A outputs … */ },
  "capacity": { "capacity_fit": 1, /* … F-C outputs … */ },
  "labor_tasks": [ /* per-task F-L outputs incl. technical vs cash vs capacity hours */ ],
  "multi_year": { "npv": null, "payback_month": 0, /* … F-M outputs … */ },
  "pricing": { "break_even_annual_vendor_fee": 65566.75, /* … F-Q caps … */ },
  "warnings": [ /* coded warnings (§8) */ ],
  "raw": { /* full-precision decimal strings for audit/determinism */ },
  "formula_trace": [ { "formula_id": "F-L05", "entity_id": "regular_collection", "inputs": [0.92, 0.95, 1, 0.98, 0.95], "result": 0.813694 } ]
}
```
