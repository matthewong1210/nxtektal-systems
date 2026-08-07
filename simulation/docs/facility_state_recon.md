# Facility State recon — mapping the existing state model

**Date:** 2026-08-07 · **Status:** analysis only — no refactoring, no new modules.
Companion proposal: [facility_state_design.md](facility_state_design.md) (awaiting approval).

Goal: evolve the validated Phase 0.5 simulator into a facility-level layer ("the unit of
autonomy is the site, not the robot") without disturbing what is already tested and shipped.

## 1. What existing state already maps to a FacilityState concept

| FacilityState concept | Existing representation | Where |
|---|---|---|
| Clean ball inventory | `BallLedger` count at `"dispenser"`; true read `dispenser_count()`; delayed sensed read `sensed_dispenser_count()` (RNG-free buffer read) | ledger.py, sim.py:345/348 |
| Dirty ball inventory | Positional, not named: `"station:<id>"` buffers (dirty awaiting wash), `"zone:<id>"` (on field), `"robot:<id>"` (in transit) | ledger.py:19–32 |
| Washer throughput | `WasherConfig.balls_per_minute` + `batch_size_balls`; `_washer_proc` moves station→washer→dispenser; WIP = ledger `"washer"` via `washer_wip()` | models.py:149, sim.py:1082/342 |
| Dispenser demand | `DemandConfig` (Poisson windows, spikes, biased forecast); `_demand_proc`; frozen day forecast `_forecast_buckets` + `forecast_window()` | models.py:85, sim.py:1033/228/381 |
| Robot fleet status | Internal mutable `_Robot` → public frozen `RobotStateSnapshot` via `robot_snapshots()` (activity, health, battery, payload, location, e-stop, awaiting-human) | sim.py:78, entities.py:68 |
| Charging stations | `_charger = simpy.Resource(slots)`; `charger_queue_length()`; occupancy derivable from `activity == CHARGING` | sim.py:182/339 |
| Terrain / environment | Static config only: zone positions, `wet_ground_speed_multiplier`, zone closure / station outage windows. No dynamic terrain state exists. | models.py, sim.py:247 |
| Operational zones | `_Zone` (cfg + `is_open`) → `ZoneStateSnapshot`; ball counts live in the ledger | sim.py:128, entities.py:100 |
| Staff availability | `_human_staff = simpy.Resource(capacity=staff_count)` — **exists but has no public read accessor** | sim.py:183 |
| Unified state object | `state_summary()` — an *untyped dict* (t_s, dispenser, washer_wip, robots, zones, stations, charger_queue, ledger). The embryo of FacilityState; no contract, no pinned consumers. | sim.py:436 |
| Facility event stream | `EventLog` / frozen `EventRecord`; 35 `EventKind`s already speak facility vocabulary (STOCKOUT, WASH_BATCH_*, STATION_OUTAGE, ZONE_CLOSED, HUMAN_*, FACILITY_CLOSED) — ground truth for byte-identical replay | events.py |
| KPIs / history | `OpsMetrics` accumulator (`metrics.to_dict()`): served demand, stockout minutes, interventions, energy | metrics.py |

Ball conservation is already invariant-checked: `BallLedger.move()` is the only mutation and
`assert_conserved()` runs after every `advance()`.

## 2. What is missing for Site OS

1. **A typed, unified FacilityState.** `state_summary()` is an ad-hoc dict — no types, no
   docs, no schema, nothing downstream is pinned to it. Site OS needs one frozen, documented
   object that agents/sensors/dashboards consume.
2. **Staff availability read.** The resource exists; capacity/busy/queued is unreadable from
   outside. The only facility dimension with no public query.
3. **Explicit clean/dirty semantics.** Today "dirty" is positional (which ledger location a
   ball sits in). A facility layer should name the flows: clean_available / in_wash /
   dirty_buffered / on_field / in_transit.
4. **Derived operational answers.** Nothing computes stockout ETA, an operational-state
   classification, or availability recommendations. These are pure functions over a snapshot
   — they don't need new simulation.
5. **Facility-level aggregates.** Fleet health counts, active closures/outages *now*
   (derivable from config windows vs `minute_of_day`), charger occupancy.
6. **A sim-independent state home.** The state object should be importable without
   simpy/gymnasium so a future consumer (live telemetry, dashboard, forecaster) can build or
   read it outside the simulator.
7. Dynamic terrain/weather state — genuinely absent, and fine to defer; surface the existing
   static values rather than invent state.

**Replay constraint discovered during recon (load-bearing):** `sensed_zone_counts()`
(sim.py:371) and `sensed_battery_frac()` (sim.py:378) draw from the shared `_rng_sensors`
stream *per call*. Any facility-snapshot builder that calls them consumes RNG and silently
diverges every subsequent trajectory — breaking byte-identical replay and E1 parity.
`sensed_dispenser_count()` is a pure buffer read and safe. Whatever builds FacilityState
must ban those two accessors and prove neutrality with an instrumented-vs-uninstrumented
event-log byte-identity test.

## 3. Extend vs keep robot-specific

**Extend into the facility layer (additive only):**
- `state_summary()` → typed `FacilityState` (keep the dict method untouched for back-compat).
- A small public staff accessor (capacity / busy / queued) — the one genuine gap.
- `OpsMetrics` exposure into the facility view (pure read; gives stockout *history*, not just
  the instant).
- The `EventKind` vocabulary — already facility-level; reuse as-is, never fork it.
- Per-entity snapshots (`RobotStateSnapshot`, `ZoneStateSnapshot`, `StationStateSnapshot`) —
  reuse verbatim as FacilityState members.

**Keep robot-specific (do not lift into FacilityState):**
- `_Robot` internals — `task_proc`, in-flight `travel` tuple, `charging_since`: SimPy
  bookkeeping, not facility state.
- `SkillOutcomeModel` / `SkillModelConfig` — micro-outcome models; this is the Phase 1
  Isaac-fitting seam and must stay behind its interface.
- `Directive` + `SafetyShield` control path — control, not state; `apply_directive()` stays
  the sole entry point. A facility layer may *recommend* in this vocabulary but never execute.
- `RangeOpsEnv` observation vector — an RL-specific normalized view. It should eventually be
  *a consumer* of facility state, not the definition of it. Untouched this milestone.
- Per-robot failure processes and RNG streams.

## Bottom line

The validated simulator already **is** the facility model — ledger, washer, demand, fleet,
chargers, staff, zones, events. The milestone is aggregation + derivation, not new
simulation: one typed object, one 6-line staff accessor as the entire upstream edit, and
three pure functions (stockout ETA, ops-state classification, availability advisories).
The smallest-change proposal with file placement, tests, and risks is in
[facility_state_design.md](facility_state_design.md) — no code or folders created pending
approval.
