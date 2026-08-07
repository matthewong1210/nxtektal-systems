# FacilityState — the Site OS state contract

**Package:** `nxt_facility` · **Status:** v0 (first milestone, 2026-08-07)
**Principle:** the unit of autonomy is the site, not the robot.

`FacilityState` is a typed, frozen, read-only projection over the validated
`nxt_range_ops` simulator. It duplicates no simulation state: every field is
copied scalar data captured at one instant. The simulator remains the single
source of truth; this object is what future agents, policies, sensors, and
dashboards consume.

```python
from nxt_facility import (
    build_facility_state, estimate_stockout, classify_state, derive_indicators,
)

state = build_facility_state(sim)          # RangeSimulation -> FacilityState
state.ball_flow.clean_available            # Q1: clean balls available (ledger truth)
estimate_stockout(state).eta_minutes       # Q2: projected stockout ETA (or None)
classify_state(state)                      # Q3: CLOSED/NOMINAL/STRAINED/CRITICAL/STOCKOUT
derive_indicators(state)                   # normalized facility health indicators
```

## Field groups

| Group | Contents | Source |
|---|---|---|
| `meta` | `t_s`, `minute_of_day`, `facility_open`, `scenario_name`, `seed` | sim clock/config |
| `ball_flow` | `total_balls`, `clean_available`, `clean_sensed`, `in_wash`, `dirty_buffered` (per station), `on_field` (per zone), `in_transit` (per robot), `conserved` | `BallLedger` (positional truth), named flows |
| `washer` | `throughput_balls_per_minute`, `batch_size_balls`, `wip` | config + ledger |
| `demand` | `forecast_balls_per_minute` buckets, `forecast_bucket_minutes`, `minutes_to_close`, `demand_balls_total/served`, `stockout_minutes`, `service_availability` | frozen day forecast + `OpsMetrics` |
| `fleet` | `total`, `operable`, `inoperative`, `charging`, `awaiting_human` | derived from robot snapshots |
| `charging` | `slots`, `in_use`, `queue_length` | config + snapshots + queue read |
| `staff` | `capacity`, `busy`, `queued_requests` | `RangeSimulation.staff_summary()` |
| `environment` | `wet_ground_speed_multiplier`, zones/stations open vs total | config + open flags (no dynamic terrain model exists yet) |
| `robots` / `zones` / `stations` | the existing frozen per-entity snapshots, verbatim | `core/entities.py` |

`clean_sensed` is the delayed, RNG-free operator reading (what an inventory
display would show); `clean_available` is ledger truth.

## Semantics and caveats

- **`estimate_stockout` is an estimate, not ground truth.** It is a
  deterministic expected-value walk over the snapshot's *frozen day
  forecast*, which is deliberately biased/noisy in some scenarios
  (`demand_forecast_error`). Washable supply = `in_wash + dirty_buffered +
  in_transit`; on-field balls are excluded because their arrival depends on
  collection decisions this layer does not model. Within a bucket the washer
  runs at its average feasible rate. Never present the ETA as validated
  facility performance.
- **`classify_state` thresholds are placeholders** (`source: placeholder`
  per the house provenance policy): critical stockout horizon 30 min,
  strained horizon 120 min, critical fleet operable fraction 0.34. They gate
  demo classifications only.
- **Recommendations are operational outcomes, not commands** (Phase 2, below).
  The directive vocabulary + `SafetyShield` remain the sole control path.

## RNG discipline (load-bearing)

`build_facility_state` reads only pure accessors. It must **never** call
`sensed_zone_counts()` or `sensed_battery_frac()` — each draws from the
shared `_rng_sensors` stream per call, which would silently shift every
subsequent sensed observation and break byte-identical seed+action replay.
Guards in `tests/facility/test_regressions.py`:

1. RNG-state equality across all five streams before/after facility calls.
2. Byte-identical event log + final metrics for an instrumented episode
   (snapshot every step) vs an uninstrumented one.
3. Static AST ban on the two accessors in `build.py`.
4. Upstream trees (`nxt_sim`, `nxt_range_ops`, `nxt_range_agent`,
   `nxt_range_viewer`, `nxt_range_demo`) byte-identical after facility use
   and never mentioning `nxt_facility`.

## Phase 2 — decision rules and the manager briefing

Architecture ladder: FacilityState → Decision Rules → Recommendation →
Human/Agent Interface → *future execution layer (not built)*.

```python
from nxt_facility import recommend, render_briefing

recs = recommend(state)          # tuple[Recommendation, ...] — pure, deterministic
print(render_briefing(state))    # the 20-second manager briefing (computes recs itself)
```

`Recommendation` fields: `rule_id`, `urgency` (`NOW`/`SOON`/`WATCH`), `action`
(imperative operational headline), `affected_resources` (sorted ids like
`robot:R1`, `zone:Z4`, `station:H1`, `washer`), `expected_outcome`,
`confidence`, `rationale`. `to_dict()` is the agent-interface serialization;
`render_briefing` is the human interface (DO NOW / DO NEXT HOUR / KEEP AN EYE
ON tiers, all-clear line when nothing fires).

`confidence` is a deterministic evidence-provenance grade, not a probability:
HIGH = ledger/snapshot facts; MEDIUM = projections over the frozen, possibly
biased forecast. Eight rules, each pre-filtering the same conditions the
`SafetyShield` enforces (reserve floor, no duplicate assist requests, no
charge advice while charging, one robot per zone) so advice is achievable the
moment it is given. When the facility is closed, only fleet-health rules run.
Thresholds are placeholder-tagged module constants, overridable as keyword
arguments to `recommend()`.

Demo: `.venv/bin/python scripts/facility_briefing_demo.py --scenario
demand_spike --seed 42 --every-min 120`.

## Boundary rules

- `nxt_facility` imports **only** `nxt_range_ops` (never `nxt_sim`, simpy,
  gymnasium, or numpy).
- `state.py` and `analysis.py` import no simulation libraries at all, so the
  contract can later be populated from real facility telemetry.
- Nothing upstream may import or mention `nxt_facility` (test-enforced).
