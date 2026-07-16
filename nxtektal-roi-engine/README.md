# NXTektal ROI Calculation Engine

Deterministic TypeScript implementation of **NXTektal_ROI_Calculation_Engine_Spec v1.0
(Formula Lock)** — all 87 canonical formulas (F-T, F-L, F-C, F-E, F-B, F-R, F-S,
F-P, F-A, F-M, F-Q, F-G), exactly as locked. Same inputs + same model_version ⇒
identical outputs.

## What this package is

- **Pure calculation engine** — no UI, no DB, no AI extraction. Web/iPad/report/API
  layers all call this one package (spec §15.1).
- **Decimal arithmetic** (`decimal.js`, precision 34) — full precision until the
  presentation layer; `raw.*` carries audit-grade full-precision strings.
- **Formula traces** — every output includes `{formula_id, entity_id, inputs, result}`.
- **Evidence-carrying inputs** — every value can carry source, status,
  low/base/high range and scenario direction. `candidate` values are refused
  from formal calculation (§10.2).
- **Core vs Expanded separation** — risk and released-capacity value never touch
  core net benefit, payback, NPV, or pricing caps.
- **Two frontends, one engine** — Full Assessment snapshots and the §9
  Quick Estimate adapter share every formula.
- **No invented defaults** — product performance/benchmark/pricing defaults live
  in `src/config/defaults.v1.json` (versioned data, intentionally empty).

## Usage

```ts
import { assessmentSnapshotSchema, calculateAssessment } from "@nxtektal/roi-engine";

const snapshot = assessmentSnapshotSchema.parse(inputJson);
const result = calculateAssessment(snapshot);
result.scenarios.expected.outputs.core_annual_customer_net_benefit;
result.sensitivity;      // F-G02 ranked missing-data priorities
result.confidence;       // F-G04..06 evidence quality / completeness / grade
```

See [docs/api-contract.md](docs/api-contract.md) and
[examples/section11-input.json](examples/section11-input.json).

## Development

```bash
npm install
npm run typecheck
npm test          # 42 tests: §14 acceptance (22) + §11 worked example + Quick Estimate + governance
```

## Governance

- Formulas are **locked**: any semantic change requires a new `model_version`
  and must keep old versions recomputable (§15.1). The engine hard-rejects
  snapshots with an unsupported `model_version`.
- Interpretations the spec leaves open are recorded in
  [docs/AMBIGUITIES.md](docs/AMBIGUITIES.md) **by formula ID** — review before v1.1.
