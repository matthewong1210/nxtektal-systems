# FacilityState Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose a stable, typed Site OS state contract (`FacilityState`) as a read-only projection over the validated `nxt_range_ops` simulator, plus pure operational metrics, without perturbing any existing behavior.

**Architecture:** New sibling package `nxt_facility` (state contract + builder + pure analysis) consuming `nxt_range_ops` read-only; the entire upstream diff is one RNG-free `staff_summary()` accessor in `sim.py`. Regression tests prove RNG-stream neutrality, trajectory neutrality, and upstream byte-identity.

**Tech Stack:** Python 3.13 (`simulation/.venv`), stdlib dataclasses only in the new package's contract modules; pytest.

## Global Constraints (from approved design + user directives)

- FacilityState duplicates NO simulation state — copied scalars captured at one instant; sim remains the single source of truth.
- Zero changes to: RL observation space, SafetyShield, Directive flow, SkillOutcomeModel, random streams, event ordering, scenario registry, KPI keys, `run_episode`.
- Builder must NEVER call `sensed_zone_counts()` / `sensed_battery_frac()` (each draws from `_rng_sensors` — sim.py:371/378). `sensed_dispenser_count()` (pure buffer read) is allowed.
- NO agents, LLM calls, planners/recommenders, new scenarios, Omniverse, RL changes this milestone.
- `state.py` / `analysis.py` import no simpy/gymnasium/numpy (telemetry-ready contract).
- Thresholds in analysis are placeholder-tagged per house provenance policy.
- Branch `feature/facility-state` off `main` (repo rule: feature branch + PR, never direct to main).
- Test runner: `cd simulation && .venv/bin/python -m pytest`.

---

### Task 1: Branch + docs commit

- [ ] `git checkout -b feature/facility-state main` (untracked docs carry over)
- [ ] Commit `simulation/docs/facility_state_recon.md`, `facility_state_design.md`, `facility_state_plan.md` — `docs: facility state recon + approved design + plan`

### Task 2: `staff_summary()` accessor (the entire upstream diff)

**Files:** Modify `nxt_range_ops/core/sim.py` (after `charger_queue_length`, ~line 340); Test `tests/facility/test_staff_accessor.py` (+ `tests/facility/__init__.py`, `tests/facility/conftest.py`)

**Produces:** `RangeSimulation.staff_summary() -> tuple[int, int, int]` — `(capacity, busy, queued)`.

- [ ] conftest:

```python
import pytest
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario

RNG_STREAMS = ("_rng_demand", "_rng_skills", "_rng_failures", "_rng_sensors", "_rng_forecast")

@pytest.fixture
def weekday():
    return make_scenario("normal_weekday")

@pytest.fixture
def sim(weekday) -> RangeSimulation:
    return RangeSimulation(weekday, seed=123)

def rng_states(sim):
    return {name: getattr(sim, name).bit_generator.state for name in RNG_STREAMS}
```

- [ ] Failing test: fresh sim → `staff_summary() == (scenario.human_ops.staff_count, 0, 0)`; call twice → identical; `rng_states` unchanged across calls. Run: expect `AttributeError`.
- [ ] Implement:

```python
    def staff_summary(self) -> tuple[int, int, int]:
        """(capacity, busy, queued) view of the human staff pool. Pure read, no RNG."""
        return (
            self.scenario.human_ops.staff_count,
            self._human_staff.count,
            len(self._human_staff.queue),
        )
```

- [ ] Pass, then commit `feat(range-ops): add staff_summary() public read accessor`

### Task 3: `nxt_facility/state.py` — the typed contract

**Files:** Create `nxt_facility/__init__.py` (empty for now), `nxt_facility/state.py`; Test `tests/facility/test_state.py`

**Produces:** frozen dataclasses `FacilityMeta`, `BallFlow` (with `dirty_buffered_total`/`on_field_total`/`in_transit_total`/`conserved` properties), `WasherState`, `DemandState`, `FleetSummary`, `ChargingState`, `StaffState`, `EnvironmentState`, `FacilityState` (with `to_dict()`), type alias `Counts = tuple[tuple[str, int], ...]`. Reuses `RobotStateSnapshot`/`ZoneStateSnapshot`/`StationStateSnapshot` verbatim as members. Field groups per design doc §"FacilityState shape".

- [ ] Failing tests: frozen-ness (`FrozenInstanceError` on attribute set), `conserved` true/false arithmetic, `to_dict()` JSON-serializable and deterministic (two identical constructions → identical `json.dumps(..., sort_keys=True)`), AST scan: `state.py` (and later `analysis.py`) contain no `import simpy/gymnasium/numpy`.
- [ ] Implement; pass; commit `feat(facility): typed FacilityState contract dataclasses`

### Task 4: `nxt_facility/build.py` — snapshot builder

**Files:** Create `nxt_facility/build.py`; Test `tests/facility/test_builder.py`

**Consumes:** `staff_summary()` (Task 2), state classes (Task 3). **Produces:** `build_facility_state(sim: RangeSimulation) -> FacilityState`.

Reads ONLY: `now`, `minute_of_day`, `facility_open`, `scenario`, `seed`, `ledger.counts()`, `dispenser_count()`, `washer_wip()`, `sensed_dispenser_count()`, `robot_snapshots()`, `zone_snapshots()`, `station_snapshots()`, `charger_queue_length()`, `staff_summary()`, `forecast_window()`, `metrics` fields. Module docstring states the sensed-accessor ban and why.

- [ ] Failing tests (fresh sim + mid-episode sim after `advance()`): `ball_flow.clean_available == sim.dispenser_count()`; `in_wash == sim.washer_wip()`; per-station/zone/robot counts match `sim.ledger.counts()`; `conserved is True`; `staff == StaffState(*sim.staff_summary())`; `fleet.operable + fleet.inoperative == fleet.total`; `charging.in_use == #robots with activity CHARGING`; demand fields match `sim.metrics`; two consecutive builds (no advance between) → identical `to_dict()`.
- [ ] Implement; pass; commit `feat(facility): RNG-free facility snapshot builder`

### Task 5: `nxt_facility/analysis.py` — pure operational metrics

**Files:** Create `nxt_facility/analysis.py`; finalize `nxt_facility/__init__.py` exports; Test `tests/facility/test_analysis.py`

**Produces:** `OperationalState` enum `{CLOSED, NOMINAL, STRAINED, CRITICAL, STOCKOUT}`; `StockoutEstimate(eta_minutes: float | None, horizon_minutes: float, limited_by: str)`; `estimate_stockout(state)` — deterministic mass-balance walk over forecast buckets, washable supply = `in_wash + dirty_buffered_total + in_transit_total` (documented: excludes on-field balls; expected-value projection over the frozen, possibly biased forecast); `classify_state(state, stockout=None)` — ordered threshold rules; `FacilityIndicators` + `derive_indicators(state)` (clean_frac, washable_supply_frac, fleet_operable_frac, stations_open_frac, zones_open_frac, staff_utilization, service_availability, demand_fill_rate). Placeholder-tagged module constants: `CRITICAL_STOCKOUT_HORIZON_MIN = 30.0`, `STRAINED_STOCKOUT_HORIZON_MIN = 120.0`, `CRITICAL_FLEET_OPERABLE_FRAC = 0.34`.

- [ ] Failing tests with hand-built `FacilityState` fixtures (no sim): exact-arithmetic stockout cases (deficit crossing mid-bucket → interpolated eta; wash ≥ demand → None; supply-limited → `limited_by="dirty_supply"`; `clean_available == 0` → eta 0.0); every `classify_state` branch (closed / stockout / critical-by-eta / critical-by-fleet / strained-by-station / strained-by-staff / nominal); indicator arithmetic incl. zero-demand guard.
- [ ] Implement; pass; commit `feat(facility): stockout estimate, ops-state classification, indicators`

### Task 6: Regression & protection tests

**Files:** Test `tests/facility/test_regressions.py`

- [ ] **RNG neutrality:** mid-episode sim; `rng_states` deep-equal before/after `build_facility_state` + `estimate_stockout` + `classify_state` + `derive_indicators`.
- [ ] **Trajectory neutrality:** two `RangeOpsEnv` runs, same seed + scripted action sequence (reuse `tests/range_ops/conftest.run_policy_episode` pattern with `inventory_threshold` baseline); run B builds a FacilityState after every step; assert `json.dumps(env.sim.events.to_dicts())` byte-identical and final `metrics.to_dict()` identical.
- [ ] **Static ban:** AST of `build.py` — no `Call` whose attribute name is `sensed_zone_counts`/`sensed_battery_frac`.
- [ ] **Upstream byte-identity:** SHA-256 tree digest of `nxt_range_ops`, `nxt_range_agent`, `nxt_sim` (py files) before/after an instrumented episode → identical.
- [ ] **One-way boundary:** no `.py` under `nxt_range_ops`/`nxt_sim`/`nxt_range_agent` contains `nxt_facility`; `nxt_facility` modules import from `nxt_range_ops` only (never `nxt_sim`, never gym/simpy; numpy allowed nowhere in the package).
- [ ] Pass; commit `test(facility): RNG/trajectory neutrality + boundary protection`

### Task 7: Packaging, contract doc, full-suite verification

- [ ] `pyproject.toml`: wheel packages += `nxt_facility`.
- [ ] `docs/facility_state.md`: contract doc — field groups, semantics, estimate caveats (forecast bias), placeholder policy, RNG discipline.
- [ ] Full suite: `.venv/bin/python -m pytest -q` → everything green (existing E1/determinism tests untouched and passing = byte-identical guarantee).
- [ ] Commit `feat(facility): package registration + Site OS contract doc`

### Task 8: Adversarial review + PR

- [ ] Multi-agent adversarial review of the branch diff (correctness, RNG discipline, boundary, YAGNI); fix confirmed findings.
- [ ] Push branch, open PR to `main` (repo PR workflow; CodeRabbit reviews per project convention).
