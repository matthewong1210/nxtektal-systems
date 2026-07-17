# Ambiguity Register — Formula Lock v1.0

Spec §16 rule 13: ambiguities must be reported by formula ID, never silently
reinterpreted. The engine implements the interpretations below; each needs
confirmation from the formula owners before model_version 1.1.

## F-L01 / F-L02 — optional wage add-ons
`payroll_burden_rate` and `fixed_benefits_per_hour` are "optional" in §5.2. When
absent, the engine computes the loaded rate WITHOUT those components (they are
additive extras), rather than marking the task incomplete. Explicitly distinct
from required fields, where missing blocks the task (missing ≠ 0).

## F-L05 / F-B02 / F-R05 — missing "recommended" multiplicative factors
`workflow_success_rate`, `adoption_rate` (F-L05), secondary loss factors (F-B02)
and stockout factors (F-R05) are "recommended", not required. When absent the
engine treats the missing factor as 1 **and emits an `assumed_factor_1`
warning** so the report shows it was not evidence-backed. Exceptions where the
engine refuses to claim benefit without evidence:
- F-L05: `coverage_rate` / `system_uptime` missing ⇒ automation not computable.
- F-B02: `loss_area_coverage` / `retrieval_success_rate` missing ⇒ reduction = 0.
- F-R05: all three factors missing ⇒ reduction = 0.

## F-E02 — fixed components with missing retirement/contractual rates
"推荐" fields missing on a fixed component ⇒ avoidable fraction 0 with an
`avoidability_assumed_0` warning (no evidence the payment can stop, §8.3),
rather than blocking the assessment.

## F-M01 — year-t scaling map
The spec requires per-year recomputation but does not enumerate which growth
rate touches which variable. The engine applies:
- `wage_growth_rate` → base wages, loaded-rate overrides, fixed benefits, shadow value/hour
- `equipment_cost_inflation_rate` → `annual_current_cash_cost` (replacement_capex kept at its planned nominal amount)
- `ball_cost_inflation_rate` → `landed_cost_per_ball`
- `basket_price_growth_rate` → `price_per_basket` only (variable cost per basket unscaled; refund/credit averages unscaled)
- `demand_growth_rate` → baskets/balls demand fields including `peak_hourly_ball_demand` (missed-basket and stockout counts unscaled)
- `energy_inflation_rate` → `electricity_rate`
- `maintenance_growth_rate` → planned maintenance, repair, consumables
- `vendor_fee_escalation_rate` → all base fee components (not the performance-fee rate)
- `deployment_ramp_by_year[t-1]` multiplies system-driven deltas: savings, revenue
  recovery, customer incremental ops, and base vendor fees. The performance fee is
  computed from the already-ramped eligible value (not ramped twice). Avoided
  replacement capex and salvage are event-timed and not ramped.

## F-M06 — monthly cash-flow construction
Year-t core cash flow is spread evenly (annual/12) across that year's months,
with the initial investment at month 0. Lumpy items (avoided capex, salvage,
system replacement capex) are therefore also spread within their year. The
`initial_investment / (annual_net/12)` fallback is emitted separately as
`approximate_payback_months`, labelled approximate.

## F-M09 — BCR cost/benefit buckets
PV(benefits) = direct gross savings + revenue recovery + avoided capex + salvage;
PV(costs) = initial investment + customer ops + vendor fees + system replacement
capex — matching the F-M08 definitions as the spec instructs.

## §8.1 — overlap group resolution mechanism
"要求人工选择主记录" is implemented as an `overlap_primary: true` flag on exactly
one task per overlap group; ≥2 tasks in a group without exactly one primary is a
hard validation error, and non-primary tasks are excluded with a warning.

## §8.4 — revenue dedup mechanism
A group carrying both missed-sale and refund data requires `dedup_resolution`
("missed_sale" | "refund" | "both_verified_distinct"). Absent ⇒ hard error
(`RevenueDedupError`), matching §14 "block or require selection".

## F-G03 / F-G05 — zero-weight inputs in confidence scoring
The locked formulas give impact-based weights (F-G03) and use those same weights
for completeness (F-G05). Consequently, inputs with no low/high range carry zero
weight whenever at least one ranged input has impact — their presence or absence
does not move the scores. This is a literal reading of the locked formulas; if
fixed inputs should contribute to completeness, that is a v1.1 formula change,
not an implementation choice.

## F-M05 — multiple-IRR cash flows
When core cash flows change sign more than once, multiple IRRs may exist. The
engine returns `irr: null` with an explanatory note (per the F-M05 "output null
and explain" rule) instead of picking one root arbitrarily. Roots outside the
search range [-99.99%, 1000%] are likewise reported as not-found, with a note
distinguishing this from mathematical non-existence.

## F-Q05 — annual_variable_vendor_fees derivation
Derived as total recurring fee minus (platform fee × 12 + annual fixed service
fee); i.e. per-ball, per-robot, per-hour and performance fees count as variable.
Per-robot monthly fees are treated as variable (they scale with fleet size); if
the business wants them fixed, provide `annual_variable_vendor_fees` explicitly
in a future revision.
