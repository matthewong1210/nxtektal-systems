# FacilityState — Site OS foundation, design spec

**Date:** 2026-08-07
**Status:** Proposed — awaiting approval before any implementation
**Milestone:** Facility State Model for a driving range. "The unit of autonomy is the site, not the robot."

## Context

`nxt_range_ops` (Phase 0.5) already simulates the whole facility: `RangeSimulation` owns a
conservation-checked `BallLedger` (per-location counts: dispenser / washer / zone:* / robot:* /
station:*), robot fleet, zones, stations, a charger `simpy.Resource`, staff as
`self._human_staff = simpy.Resource(capacity=staff_count)`, a frozen day-demand forecast, and
noisy/delayed sensor views. Per-entity frozen snapshots exist in `core/entities.py`, and
`RangeSimulation.state_summary()` (sim.py:436) is an untyped dict aggregation of most of it.

So this milestone is an **aggregation-and-derivation problem, not a new-simulation problem**.
What is missing:

1. A typed, frozen, unified `FacilityState` object (the Site OS contract).
2. A public read for staff availability (currently no accessor).
3. Derived answers: stockout forecast, operational-state classification, availability advisories.

## Constraints (verified against the codebase)

- **Replay determinism is sacred.** One seed spawns 5 named RNG streams; seed + action sequence
  replays to a byte-identical event log (tested). **Hazard verified first-hand:**
  `sensed_zone_counts()` (sim.py:371) and `sensed_battery_frac()` (sim.py:378) draw from
  `_rng_sensors` per call. Any snapshot builder that calls them consumes RNG and silently
  diverges every subsequent trajectory. `sensed_dispenser_count()` (sim.py:348) is a pure
  buffer read and is safe.
- **Protection tests:** downstream layers must leave upstream trees byte-identical
  (`nxt_range_ops`, `nxt_range_agent`, `nxt_range_viewer`).
- **Vocabulary boundary:** `nxt_range_ops` may import only `nxt_sim.interfaces.types` and
  `nxt_sim.config.models`; `nxt_sim` must never mention downstream packages.
- **Benchmark coupling:** `nxt_range_agent` hard-codes 10 KPI keys, `RangeOpsEnv(scenario)`
  constructor, `run_episode` result shape, and auto-includes `sorted(SCENARIO_GENERATORS)` —
  so **no new scenario generators** in this milestone (they would silently change the E1
  default grid) and no KPI/env signature changes.
- **E1 KPI parity:** shipped demo bundle replays the live sim; any dynamics or RNG-order
  change breaks parity.

## Decision: one new sibling package, two additive file edits

Follows the established repo pattern (new `nxt_*` package consuming upstream read-only, with
its own `tests/<name>/` including architecture + protection tests). Three candidate designs
were drafted and adversarially reviewed; this is the winning synthesis (clean-boundary base
with three grafts from the Site-OS-forward variant).

### New files

| File | Purpose |
|---|---|
| `nxt_facility/__init__.py` | Public surface: `FacilityState`, `build_facility_state`, `OperationalState`, `estimate_stockout`, `classify_state`, `recommend_actions` |
| `nxt_facility/state.py` | Frozen dataclasses only — **no simpy/gym/numpy imports** — so the same object can later be built from live telemetry, not just the sim. Reuses `RobotStateSnapshot` / `ZoneStateSnapshot` / `StationStateSnapshot` verbatim. `to_dict()` with sorted keys. |
| `nxt_facility/build.py` | `build_facility_state(sim: RangeSimulation) -> FacilityState`. Pure read of the existing public query surface. **Module-docstring + test-enforced ban** on `sensed_zone_counts()` / `sensed_battery_frac()` (RNG-drawing). Uses RNG-free `sensed_dispenser_count()` for the operator view. |
| `nxt_facility/analysis.py` | Pure functions of `FacilityState` only (state in → answers out; no sim access). This is the Site OS seam: same functions run against a live sim, a serialized snapshot, or future real telemetry. |
| `scripts/facility_report.py` | ~60-line runnable proof: run any named scenario for N minutes, print the four founder answers. |
| `tests/facility/test_facility_state.py` | Conservation (inventory groups sum to `total_balls`), determinism of built state, field pinning. |
| `tests/facility/test_analysis.py` | Stockout walk, ops-mode rules, recommendation determinism + directive-name alignment with `ActionCatalog`. |
| `tests/facility/test_protection.py` | (a) SHA-256 tree byte-identity of `nxt_range_ops`/`nxt_range_agent`/`nxt_range_viewer` after building states all episode; (b) import rules: `nxt_facility` imports only `nxt_range_ops`, never `nxt_sim`; (c) no upstream file contains the string `nxt_facility`; (d) **event-log byte-identity: an instrumented run (snapshot every control interval) equals an uninstrumented run** — the RNG-purity guarantee. |
| `docs/facility_state.md` | Contract doc: field groups, estimate semantics, placeholder policy. |

### Modified files (entire upstream diff)

1. `nxt_range_ops/core/sim.py` — one ~6-line additive accessor in the existing
   "Public state queries" section:
   `staff_summary() -> (capacity, busy, queued)` from the `_human_staff` Resource.
   No RNG, no mutation, no ordering change. Staff is the only facility dimension with no
   public read today. Charger occupancy needs no accessor (derived from
   `activity == CHARGING` + existing `charger_queue_length()`).
2. `pyproject.toml` — register `nxt_facility` in the wheel packages list. No new dependencies
   (stdlib dataclasses only).

Nothing changes in `nxt_sim`, env, actions, rewards, scenarios, recording, viewer, or demo.

## FacilityState shape (field groups)

- **meta** — `t_s`, `minute_of_day`, `facility_open`, `scenario_name`, `seed`, `simulator_version`
- **ball_flow** — `total_balls`, `clean_available` (dispenser, ledger truth), `clean_sensed`
  (delayed RNG-free reading), `in_wash`, `dirty_buffered` (per-station + sum), `field_balls`
  (per-zone + sum), `in_transit` (robot payloads), `conserved: bool`
- **washer** — `throughput_bpm`, `batch_size`, `wip`
- **demand** — `forecast_window` buckets, `bucket_minutes`, `minutes_to_close`,
  `served_total` + `stockout_minutes` (from `OpsMetrics` — pure read; gives the classifier
  stockout *history*, not just the instant)
- **fleet** — tuple of `RobotStateSnapshot` + derived counts (operable / inoperative /
  charging / awaiting_human)
- **charging** — `slots`, `in_use` (derived), `queue_length`
- **zones** — tuple of `ZoneStateSnapshot` + `landing_weight`, open/closed counts
- **stations** — tuple of `StationStateSnapshot`
- **staff** — `capacity`, `busy`, `queued` (the saturation signal)
- **environment** — `wet_ground_speed_multiplier`, active zone closures / station outages
  derived from config windows vs `minute_of_day`. (Terrain has no dynamic sim state today;
  we surface what exists rather than invent state.)

## The founder's four questions

1. **How many clean balls are available?** `state.ball_flow.clean_available` (exact,
   conservation-checked) and `clean_sensed` (what an operator display would show).
2. **When will stockout happen?** `estimate_stockout(state)`: deterministic mass-balance walk
   over the frozen forecast buckets — `clean(t+1) = clean(t) − forecast_demand +
   min(washer_rate·bucket, dirty supply remaining)`; first bucket ≤ 0 is the ETA, else `None`.
   Pure arithmetic, no RNG. **Labeled an estimate** — the forecast is deliberately biased in
   `demand_forecast_error` scenarios.
3. **What operational state is the facility in?** `classify_state(state) → OperationalState`
   enum `{CLOSED, NOMINAL, STRAINED, CRITICAL, STOCKOUT}` via ordered threshold rules
   (stockout ETA vs horizon, ongoing `stockout_minutes`, fleet health fraction, station/staff
   availability). Thresholds are module constants, placeholder-tagged per house provenance style.
4. **What actions could improve availability?** `recommend_actions(state)` → ranked advisory
   records phrased in the existing directive vocabulary (`assign_collection`,
   `send_to_handoff`, `send_to_charge`, `request_human_assistance`) with rationale strings.
   **Advisory only** — never constructs or applies `Directive` objects; `apply_directive` +
   `SafetyShield` remain the sole control path.

## Why nothing breaks

- **Determinism/replay:** builder is RNG-free by construction; protection test (d) proves an
  instrumented run's event log is byte-identical to an uninstrumented one.
- **E1 parity / benchmark / viewer / demo:** zero changes to dynamics, KPI keys, schemas,
  scenario registry, env or `run_episode` signatures.
- **Boundaries:** new one-way rule `nxt_range_ops ⟂ nxt_facility`, mirroring the existing
  `nxt_sim` string test, enforced in `tests/facility/test_protection.py`.

## Deferred (YAGNI)

Schema version string / `from_dict()` round-trip (add when a second consumer exists);
FacilityState in `RangeOpsEnv` observations (invites parity breakage, zero milestone value);
dynamic terrain/weather; staff shifts/scheduling; per-station chargers; sensor-fusion belief
state; closed-loop recommendation execution (that is an agent — explicitly out of scope);
multi-agent anything; RL training; viewer/demo export of facility frames.

## Known risks

- `staff_summary()` edits the most protected file in the stack — keep the diff to exactly
  that accessor and re-run the byte-for-byte replay test before merge.
- The stockout ETA inherits forecast bias by design; documentation must prevent it being
  presented as ground truth (claims discipline, per YC-demo policy).
- Future contributors adding `sensed_zone_counts()` to the builder would silently break
  replay — protection test (d) is the permanent guard.
