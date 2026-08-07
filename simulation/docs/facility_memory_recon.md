# Phase 3 recon — operational memory over the shipped Site OS stack

**Date:** 2026-08-07 · **Status:** analysis only — no code.
Companion proposal: [facility_memory_design.md](facility_memory_design.md) (awaiting approval).

Goal: an **auditable operational memory** capturing
State → Recommendation → Human Decision → Action Taken → Outcome Measured —
the structured memory that, after six months of operation, makes the next decision better.
Explicitly not: self-modification, automatic rule rewriting, LLM dependencies, agents.

## 1. What existing components can be reused?

| Component | Reused as | Where |
|---|---|---|
| `FacilityState.to_dict()` | The state-snapshot serialization, verbatim — sorted-keys, JSON-ready, pure-Python types | nxt_facility/state.py |
| `Recommendation.to_dict()` | The recommendation serialization, verbatim — this *is* the "agent interface" it was designed to be | nxt_facility/decisions.py |
| `recommend()`'s deterministic sort | Verdict linkage: `(urgency, rule_id, affected_resources)` ordering is unique across all eight rules, so a human verdict can reference a recommendation by tuple index | decisions.py |
| `estimate_stockout` / `classify_state` / `derive_indicators` | Pure functions → any stored state is retrospectively re-analyzable under any future code version (the recompute-audit property) | analysis.py |
| `EventLog.since(index)` + `EventRecord.to_dict()` | Execution harvest: what *actually happened* in a window (DIRECTIVE_APPLIED/REJECTED, STOCKOUT, ROBOT_FAILED, WASH_BATCH_DONE, …), verbatim, no interpretation | core/events.py |
| `OpsMetrics.copy()` / `.to_dict()` | Outcome measurement: before/after counters per window; deltas are the outcome record | core/metrics.py |
| `EpisodeLogger` discipline | The provenance template: rows carry episode_id / simulator_version / git_commit / seed; sorted-keys JSON; **no wall-clock values** → byte-reproducible from (scenario, seed, inputs); JSONL without pyarrow; disclaimer on every summary | recording/episode_logger.py |
| Guard-test patterns | Blocked-import subprocess, instrumented-vs-plain byte-identity, no-upstream-mention string scans, static source scans | tests/facility/ |

## 2. What new contracts are needed?

Frozen, schema-versioned dataclasses (a second consumer now exists — the store — so the
previously-deferred `schema_version` is finally justified):

- **`EpisodeMeta`** — identity + provenance: episode_id, scenario, seed, simulator_version,
  git_commit, driver name/version, schema_version, disclaimer.
- **`HumanVerdict`** — per recommendation: tuple index + rule_id echo (write-time integrity
  check), verdict `ACCEPTED / REJECTED / DEFERRED`, actor label, free-text note.
  **Recorded input only** — supplied by whatever drives the loop (demo harness, tests,
  future UI); the memory layer never generates or simulates a decision.
- **`MemoryWindow`** — one decision cycle, four structurally distinct sections
  (observation / decision / execution / outcome — see §5) in one atomically-written record.
- A small **read API** of pure functions over stored dicts (see §5, "six-month payoff").

No new contracts inside `nxt_facility` or `nxt_range_ops` — zero upstream changes.

## 3. Where should decision records live?

A **new sibling package `nxt_memory`** (not inside `nxt_facility`). The decisive argument is
mechanical enforceability of the no-feedback rule: with a sibling, the proof is one grep —
*neither `nxt_range_ops/` nor `nxt_facility/` may contain the string `nxt_memory`* — exactly
the scan pattern already protecting the other boundaries. Inside `nxt_facility`, the
write-side would sit one `from .` away from `decisions.py`: the wrong temptation gradient,
and the scan would need carve-outs. Dependency direction is strictly one-way:
`nxt_memory → nxt_facility` (contracts) and `nxt_memory.harvest → nxt_range_ops` (read-only
harvest, mirroring `build.py`'s privileged position).

On disk: `reports/operational_memory/<episode_id>/windows.jsonl` + `episode.meta.json` —
alongside the other run artifacts, outside every package tree.

## 4. Where should outcome records live?

In the **same window record** as the decision. Each `MemoryWindow` spans one control cycle:
observation and recommendations at t₀, the human verdicts, then execution events and metric
deltas over [t₀, t₁), written as **one JSONL line when the window closes**. This
single-row model dissolves the two-phase decision/outcome linkage problem: a decision and
its outcome cannot be orphaned from each other, and an episode ending mid-window is marked
honestly with `status: TRUNCATED_BY_EPISODE_END` on the final row rather than silently
dropped. Append-only, flushed per line (a crash leaves a valid prefix), never mutated
in place — that is the auditability property.

## 5. Representations

- **State snapshot** — full `FacilityState.to_dict()` verbatim, **plus** the derived values
  *as the human saw them at capture time*: operational_state, stockout ETA + limited_by,
  indicators. Rationale: analysis thresholds are placeholders and will drift; recomputation
  under future code would silently rewrite the prediction the manager actually acted on.
  The stored raw state remains the recompute-audit fallback (a test asserts stored derived
  values recompute from stored state under the pinned code).
- **Recommendation** — the `Recommendation.to_dict()` tuple verbatim, plus the exact
  `recommend()` keyword thresholds used (`rule_params`) — unrecoverable later otherwise.
- **Human acceptance/rejection** — `HumanVerdict` records as above; capture-time-or-lost.
- **Execution result** — raw event records harvested from `EventLog.since(cursor)` for the
  window, verbatim; no interpretation, no matching of events to recommendations.
- **Final operational outcome** — `OpsMetrics` before/after dicts + per-key deltas
  (stockout_minutes, demand_balls_served, balls_processed, hard_failures, …). Measured
  quantities only — **the schema cannot express "rule X caused Y"**; no scores, no
  attribution fields. Closing that loop is a later, human-mediated phase.

**The six-month payoff** (ships as pure read-only functions over stored records):
per-rule × per-urgency acceptance rates; stockout-ETA calibration (predicted ETA vs
realized STOCKOUT events, with censored-window counts); outcome deltas conditioned on
accepted-vs-rejected — labeled observational and confounded, never causal.

## 6. How are the invariants preserved?

- **Determinism** — no wall-clock, no uuid: ids derive from (scenario, seed, window seq);
  timestamps are sim-time only; sorted-keys JSON; a byte-reproducibility test drives two
  identical recorded episodes and asserts identical files. A static source scan bans
  `time`/`datetime`/`uuid` imports across `nxt_memory` (the same pattern that pins
  `build.py`'s RNG ban).
- **RNG neutrality** — the recorder consumes plain dicts; the only sim-touching module
  (`harvest.py`) wraps `EventLog.since()` and `OpsMetrics.copy()`, both pure reads. The
  instrumented-vs-plain trajectory guard is extended: an episode recorded end-to-end must
  be byte-identical (event log, metrics, obs digest, all five RNG streams) to a bare run.
- **Simulation purity / no feedback** — the live loop cannot read the store: nothing in
  `nxt_range_ops` or `nxt_facility` may mention `nxt_memory` (string-scan), and `nxt_memory`
  itself never passes data back. v1 is write-only with respect to the loop; the loop closes
  later via humans reading reports.
- **Observation / decision / execution separation** — the four sections are structurally
  distinct nested objects with a shape test (the decision section can contain no event
  kinds; the execution section no recommendation keys); verdicts come only from the driver;
  execution comes only from the event log; neither is ever inferred from the other.
