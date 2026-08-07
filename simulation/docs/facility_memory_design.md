# Phase 3 — Operational Memory, design spec

**Date:** 2026-08-07 · **Status:** Approved with founder adjustments (reflected below):
site identity fields (`site_id`, `deployment_id`) for future multi-site deployments; outcomes
carry a measured operational-impact block (availability, stockout, labor, energy, safety) —
never a binary success/failure; strictly observational (no causal attribution, no automatic
rule updates, no self-modifying policies, no RL loops, no vector memory).
**Builds on:** PR #9 (FacilityState) and PR #10 (decision rules + briefing)
**Recon note:** [facility_memory_recon.md](facility_memory_recon.md)

Ladder extension: FacilityState → Decision Rules → Recommendation → Human/Agent Interface →
**Operational Memory** (this phase) → *future: human-mediated improvement loop*.

Three designs were drafted (minimal-store / future-consumer / audit-provenance) and
adversarially critiqued; this is the winning hybrid: the minimal single-row chassis, the
future-consumer's capture-time fields and queries, and the audit lens reduced to a status
field and a write-time integrity check.

## Files (all new — zero changes to `nxt_range_ops` or `nxt_facility`)

| File | Role | Import rules |
|---|---|---|
| `nxt_memory/__init__.py` | exports | — |
| `nxt_memory/records.py` | **Contract**: `EpisodeMeta`, `HumanVerdict`, `MemoryWindow` frozen dataclasses + `iter_windows()` / `load_meta()` readers | stdlib + `nxt_facility` types only; subprocess-guarded |
| `nxt_memory/recorder.py` | `EpisodeMemoryRecorder`: append-only JSONL writer, one line per window, flushed per call; `finalize()` writes `episode.meta.json` | stdlib only — accepts plain dicts, never sim objects |
| `nxt_memory/harvest.py` | `harvest_events(event_log, since_index)`, `snapshot_metrics(metrics)` — the only sim-importing module (mirrors `build.py`'s privileged position) | may import `nxt_range_ops`; pure reads only |
| `nxt_memory/queries.py` | **Contract**: pure functions over stored dicts — `rule_acceptance_rates`, `stockout_eta_calibration` (with censored counts), `outcome_deltas_by_decision` (carries a fixed non-causal caveat string) | stdlib only |
| `scripts/facility_memory_demo.py` | Drives a scenario with a scripted-verdict harness, records an episode, prints the three query outputs | may import everything |
| `tests/memory/…` | See test plan | — |
| `pyproject.toml` | register `nxt_memory` in the wheel | — |

## The record: one `MemoryWindow` per decision cycle

`EpisodeMeta` carries site identity for future multi-site deployments: `site_id` and
`deployment_id` are required driver-supplied strings, and the store nests by them:
`reports/operational_memory/<site_id>/<deployment_id>/<episode_id>/`.

```python
MemoryWindow(
  episode_id: str,            # f"{scenario}-seed{seed}" (EpisodeLogger convention)
  seq: int,                   # window index; record_id = f"{episode_id}:w{seq:06d}"
  t_start_s: float, t_end_s: float,        # sim time only — never wall clock
  status: str,                # "complete" | "truncated_by_episode_end"
  observation: dict,          # FacilityState.to_dict() verbatim
                              # + capture-time derived values the human saw:
                              #   operational_state, stockout_eta_minutes,
                              #   stockout_limited_by, indicators
  decision: dict,             # recommendations: [Recommendation.to_dict(), ...] verbatim
                              # rule_params: exact recommend() kwargs used
                              # human: {decided_by, verdicts: [HumanVerdict...]}
  execution: dict,            # events: EventLog.since(cursor) for the window, verbatim
                              # event_cursor_start / event_cursor_end
  outcome: dict,              # metrics_before / metrics_after (OpsMetrics.to_dict())
                              # deltas: per-key after − before
                              # impact: curated operational dimensions (founder adj. 2):
                              #   availability_change, stockout_minutes,
                              #   labor {interventions requested/completed},
                              #   energy_wh, safety {estops, hard_failures,
                              #   unsafe_rejections} — measured only, no
                              #   success/failure flag exists in the schema
)
```

Design decisions locked by the critique:

- **Single-row window** (decision + outcome in one atomically written line): dissolves the
  two-phase linkage problem — an episode ending mid-window cannot orphan a decision; the
  final row is honestly marked `truncated_by_episode_end` by the driver at finalize.
- **Verdicts link by tuple index** into the recommendation list (the `(urgency, rule_id,
  affected_resources)` sort is deterministic and unique), with a **rule_id echo** on each
  verdict checked at write time — index linkage with an integrity guard, no synthetic keys.
- **Capture-time derived fields are mandatory**: analysis thresholds are placeholders and
  will drift; the prediction the human saw must be data, with the stored raw state as the
  recompute-audit fallback (linkage test recomputes and compares under pinned code).
- **Human verdicts are recorded inputs** from the driver — the layer never generates them.
- **No attribution anywhere**: the schema has no score, reward, or causal field; queries
  that condition outcomes on verdicts emit a fixed "observational, confounded — not causal"
  caveat string in their result.
- **Ids and time**: derived ids only (scenario, seed, seq); sim-time timestamps; no
  uuid/wall-clock anywhere in the package (statically scanned).
- **Store**: `reports/operational_memory/<episode_id>/windows.jsonl` + `episode.meta.json`
  (meta + n_windows + sha-free — the audit property is append-only + byte-reproducibility,
  not manifest hashing). Stdlib JSON only; no pyarrow in v1.

## Recorder API

```python
rec = EpisodeMemoryRecorder(out_dir, meta: EpisodeMeta)
rec.record_window(window: MemoryWindow)   # validates seq monotonic + rule_id echoes,
                                          # appends one sorted-keys JSON line, flushes
rec.finalize(status_of_last_window=...)   # writes episode.meta.json (+ disclaimer)
```

The driver (demo script, tests, future UI) owns the loop: builds `FacilityState`, calls
`recommend()`, collects verdicts, tracks its own event-log cursor, snapshots metrics via
`harvest.py`, and hands plain dicts to the recorder.

## Test plan (`tests/memory/`)

1. **Contract purity** — subprocess with simpy/gymnasium/numpy/pyarrow blocked imports
   `records.py` + `queries.py` and runs `iter_windows` on a fixture file.
2. **Byte-reproducibility** — two full recorded episodes, same (scenario, seed, scripted
   verdicts) → `windows.jsonl` and `episode.meta.json` byte-identical.
3. **Trajectory neutrality** — recorded episode vs bare episode: event log, metrics, obs
   digest, and all five RNG stream states byte-identical.
4. **No-feedback boundary** — string-scan `nxt_range_ops/` **and** `nxt_facility/` for
   `nxt_memory` (must be absent); `nxt_memory` imports `nxt_range_ops` only in `harvest.py`.
5. **No wall-clock/uuid** — static AST scan of `nxt_memory/` for `time`/`datetime`/`uuid`.
6. **Event completeness** — concatenated window events equal the full episode event log
   (guards the driver-cursor off-by-one risk).
7. **Round-trip + integrity** — window → line → `iter_windows` → equality; non-monotonic
   seq rejected; wrong rule_id echo rejected; section-shape test (decision contains no
   event kinds; execution contains no recommendation keys).
8. **Capture-vs-recompute linkage** — stored derived observation fields equal recomputation
   from the stored state under current code.
9. **Query goldens** — hand-built window fixtures with known acceptance rates, a censored
   calibration case (stockout predicted, episode ends first), and delta aggregation;
   caveat string asserted present.

## YAGNI (deferred, deliberately)

Parquet/sqlite; cross-episode catalog/index; schema migration tooling beyond the version
string; manifest hashing; per-decision horizons and settlement machinery; state digests;
report renderer (queries make it a trivial consumer when a human asks); store compaction;
any read path visible to the sim or decision layer — **excluded permanently in v1, not
just deferred**; rule scoring, weighting, or auto-tuning of any kind.

## Risks

- Window-event harvest depends on driver cursor discipline — covered by test 6.
- Full state per window grows the file linearly (~few KB/window; a 16-h day at 60 s cadence
  ≈ 960 windows ≈ small MBs) — acceptable for v1; compaction is a deferred concern.
- Stored capture-time predictions inherit forecast bias by design — the calibration query
  is exactly the tool that will quantify it; docs must keep calling them estimates.
