# NXTektal ROI Calculation Engine — Implementation Plan & Design

Date: 2026-07-16
Source spec: `NXTektal_ROI_Calculation_Engine_Spec_v1.0_CN.docx (external source document; location not versioned)` ("Formula Lock v1.0", 2026-07-15)
Status: approved to build (user: "build it"); formulas are LOCKED — this document covers implementation choices only, never formula semantics.

## Goal

A deterministic TypeScript calculation engine implementing all 87 canonical formulas (F-T, F-L, F-C, F-E, F-B, F-R, F-S, F-P, F-A, F-M, F-Q, F-G) exactly as specified, with:

- Pure calculation functions, decimal arithmetic (`decimal.js`), full precision until presentation.
- Formula traces (`formula_id`, `entity_id`, inputs, result) on every computation.
- Evidence-carrying inputs (source, status, low/base/high, timestamps) and candidate-vs-confirmed gating.
- Conservative / Expected / High Performance scenarios (F-G01 direction-aware), one-at-a-time sensitivity, evidence-quality / completeness / confidence scores.
- Section 8 anti-double-counting and validation rules as hard errors or warnings.
- Section 14's 22 acceptance tests plus the Section 11 worked example as regression tests.
- Quick Estimate and Full Assessment as two adapters over the SAME engine.
- No invented product defaults: performance/benchmark/pricing defaults live in versioned config data files, empty by default.

## Stack

- TypeScript (strict), Node ≥ 20, ESM.
- `decimal.js` — money and rate arithmetic (precision 34, ROUND_HALF_EVEN), deterministic.
- `zod` — input validation schema (units, ranges, required-by-mode, missing≠0).
- `vitest` — unit + acceptance tests.
- No UI, no DB, no AI extraction in this package — engine only, per spec 15.1 (business logic separate from UI; web/iPad/report/API all call this package).

## Module structure

```
nxtektal-roi-engine/
├── package.json / tsconfig.json / vitest.config.ts
├── src/
│   ├── types/
│   │   ├── inputs.ts          # Section 5 variable dictionary as canonical types
│   │   ├── evidence.ts        # EvidenceValue: low/base/high, status, source, direction
│   │   ├── outputs.ts         # results, trace entries, warnings, confidence
│   │   └── index.ts
│   ├── schema/
│   │   └── snapshot.ts        # zod schema for assessment input snapshot
│   ├── engine/
│   │   ├── decimal.ts         # Decimal config, CLAMP, safe division (null on 0), helpers
│   │   ├── trace.ts           # TraceCollector
│   │   ├── time.ts            # F-T01..F-T04
│   │   ├── labor.ts           # F-L01..F-L12
│   │   ├── capacity.ts        # F-C01..F-C09
│   │   ├── equipment.ts       # F-E01..F-E06
│   │   ├── ballLoss.ts        # F-B01..F-B04
│   │   ├── revenue.ts         # F-R01..F-R08
│   │   ├── risk.ts            # F-S01..F-S03
│   │   ├── newCosts.ts        # F-P01..F-P08
│   │   ├── aggregate.ts       # F-A01..F-A12
│   │   ├── multiyear.ts       # F-M01..F-M10 (yearly recomputation, NPV, IRR, payback, ROI, BCR)
│   │   ├── pricing.ts         # F-Q01..F-Q05
│   │   ├── scenario.ts        # F-G01 scenario value selection + input registry
│   │   ├── sensitivity.ts     # F-G02..F-G06
│   │   ├── validate.ts        # Section 8 hard rules + warnings
│   │   └── calculate.ts       # calculateAssessment(snapshot, scenario) — Section 12 pseudocode
│   ├── adapters/
│   │   └── quickEstimate.ts   # Section 9 Quick→Full field mapping (same engine)
│   ├── config/
│   │   └── defaults.v1.json   # versioned data file — intentionally empty of product numbers
│   └── index.ts
├── tests/
│   ├── acceptance.test.ts     # Section 14 — all 22 tests
│   ├── section11-example.test.ts  # worked example regression (exact figures)
│   ├── time.test.ts / labor.test.ts / capacity.test.ts / ...unit tests per family
│   └── quickEstimate.test.ts
├── examples/
│   ├── section11-input.json
│   └── section11-output.json
└── docs/
    ├── api-contract.md
    └── AMBIGUITIES.md         # ambiguity register, keyed by formula ID
```

## Key design decisions

1. **Missing ≠ 0**: optional numeric inputs are `number | null | undefined` in the snapshot; the resolver produces `Decimal | null`. Formula functions receive explicit nulls and follow the spec's incomplete/skip semantics; nothing silently coerces null→0. Explicit `0` means confirmed-none.
2. **Evidence-wrapped inputs**: any scenario-varied field may be given as `{ value_base, value_low?, value_high?, scenario_direction?, input_status, source_type, ... }` or as a plain number (treated as base-only, `explicit_only`, status per field metadata). The scenario resolver (F-G01) flattens to per-scenario plain values and registers each evidence field in an input registry used by sensitivity (F-G02) and scoring (F-G04/G05).
3. **Traces**: every formula function pushes `{ formula_id, entity_id, inputs, result }` into a TraceCollector owned by the calculation run. Output includes the full trace.
4. **Determinism**: Decimal precision/rounding fixed at module load; no `Math.random`, no date-dependent logic inside formulas; same snapshot + model_version ⇒ identical output (test-enforced).
5. **Candidate gating**: the zod schema PARSES all input statuses (candidate values are legitimate data at rest); the CALCULATION layer refuses them — `candidate`, `rejected`, and `superseded` values throw `InputRegistryError` during scenario resolution, so only `confirmed` and `estimated_allowed` inputs reach formulas. Preliminary estimates count estimated_allowed and report X confirmed / Y estimated.
6. **F-L08 vs F-L09**: selected exclusively by `cash_realization_method`; validator errors if both factor sets are supplied inconsistently or method missing on a current_task.
7. **Multi-year (F-M01)**: every component is recomputed per year t with year-t rates: wage growth → loaded rates; equipment inflation → equipment cash costs; ball inflation → landed cost; basket price growth → price_per_basket; demand growth → baskets/balls; energy inflation → electricity rate; maintenance growth → maintenance costs; vendor escalation → vendor fees; `deployment_ramp_by_year` scales savings-side components. Growth compounds as (1+rate)^(t-1) from year 1. Interpretations that the spec does not pin down are logged in `docs/AMBIGUITIES.md` by formula ID (per Section 16 rule 13: report, don't reinterpret silently).
8. **Payback (F-M06)**: monthly cash flows = annual/12 within each year, t0 investment at month 0; `payback_month = first m with cumulative ≥ 0`; `not achieved within analysis horizon` otherwise. The approximate fallback is labeled `approximate`.
9. **IRR (F-M05)**: bisection over annual core cash flows; null + explanation when no sign change / no root.
10. **Null-not-infinity**: all ratio/unit-cost formulas return null on zero denominators (F-A08, F-A10, F-A11, F-A12, F-M08).
11. **Quick Estimate**: an adapter that builds a Full snapshot (regular collection, special recovery, optional unloading tasks; aggregate equipment component flagged estimated_allowed; labor-disposition answer maps to cash-realization inputs exactly per Section 9.2). No second formula path.
12. **Scenario monotonicity**: after computing all three scenarios, if Conservative core net benefit > Expected (or Expected > High Performance), emit `scenario_monotonicity_warning` with the offending components.

## Testing strategy

- Section 14 acceptance tests (22) — written against the public `calculateAssessment` API before/alongside implementation (TDD at the module level).
- Section 11 worked example — exact expected figures ($33,156.75 core net benefit, $99,864 current cost, $78,407.25 post cost, 21.49% reduction, $9.51/$7.47 per-1000-balls, CapEx variant: $49,156.75 core, ~17.1-month payback, ~$116,343 NPV@10%, ~115.6% 5-year simple ROI).
- Determinism test: run twice on the same snapshot, deep-equal outputs.
- Unit tests per formula family for branch coverage (overrides, missing inputs, clamps, negative economics).

## Delivery

- Feature branch `feature/nxtektal-roi-engine` in the Jarvis AI Agent repo (per established PR workflow; no direct main pushes).
- Engine lives at `nxtektal-roi-engine/` as a standalone npm package (movable to its own repo later without changes).
