# Phase 4A — Synthetic Telemetry Layer, architecture proposal

**Date:** 2026-08-07 · **Status:** Approved with founder adjustments (reflected below)
**Builds on:** PR #9 (FacilityState) · PR #10 (decisions) · PR #11 (memory)
**Target ladder:** **Observation Layer** → FacilityState → Decision Rules → Operational
Memory → Digital Twin. Observations are an *input* layer — FacilityState remains the only
operational truth contract, its semantics untouched.

**Founder adjustments:** (1) the contract is generalized from `Reading` to **`Observation`**
— the input layer covers hardware sensors, simulation, external systems (POS, tee sheet,
weather APIs), and human inputs, so every observation carries `source_type`
(SENSOR / SIMULATION / EXTERNAL_SYSTEM / HUMAN) and `source_id` as separate fields; the
synthetic bank honestly emits `source_type=SIMULATION` (never claiming to be a sensor).
(2) `AssemblyReport` additionally surfaces **consistency issues** (e.g. ball-flow sums
disagreeing with site total) so the system knows when it lacks reliable information.
(3) No transport infrastructure of any kind (no MQTT/Kafka/OPC-UA/cloud/streaming) — the
contract boundary comes first.

Three designs were drafted (minimal-swap / deployment-forward / purity-audit) and
adversarially critiqued against the shipped code; this is the winning hybrid.

## The key question, answered

*"If tomorrow we replace simulation inputs with real sensors, what lets the same
FacilityState and decision system keep operating?"* — A **reading contract + assembler**
pair, proven by a **parity test**: with all imperfection knobs at zero, a FacilityState
assembled purely from synthetic telemetry equals `build_facility_state(sim)` field-for-field
across a whole simulated day. Real deployment then swaps the synthetic bank for hardware
adapters emitting the same `Reading` contract; nothing downstream changes.

## Package: new sibling `nxt_telemetry` (zero changes to nxt_range_ops / nxt_facility / nxt_memory)

| File | Role | Imports |
|---|---|---|
| `observations.py` | **Contract**: `Observation`, `ObservationFrame`, `SiteConfig`, `UpstreamInputs`, `SENSED_FIELD_PREFIXES` whitelist | stdlib only; subprocess-guarded |
| `bank.py` | `SyntheticSensorBank(sim, config)` — samples sim truth; dropout applies to measurements (so held data ages into STALE), noise is keyed per-measurement (one measurement, one value) | designated sim-side (like `build.py`); numpy allowed |
| `assemble.py` | `assemble_from_observations(frame, site, upstream, previous=None) -> (FacilityState, AssemblyReport)` | designated sim-side (needs the snapshot classes, whose package init pulls the simulator) |
| `tests/telemetry/…` | see test plan | — |

## Reading contract

```python
class ObservationStatus(str, Enum): OK; STALE; MISSING   # MISSING is a real Observation, never an absent key
class SourceType(str, Enum): SENSOR; SIMULATION; EXTERNAL_SYSTEM; HUMAN

@dataclass(frozen=True)
class Observation:
    channel: str          # "<family>.<asset_id>.<measure>"
    value: float | int | str | bool | None   # None iff MISSING (union carries robot activity strings)
    sample_timestamp_s: float     # sim time the quantity was measured
    available_timestamp_s: float  # sim time it became visible (two timestamps are locked —
                                  # conflating them corrupts staleness math forever)
    status: ObservationStatus
    source_type: SourceType       # synthetic bank always emits SIMULATION
    source_id: str                # "synthetic.loadcell" now; "loadcell:<serial>" later
    calibration_id: str   # "cal:placeholder:v0" now; real cert id later (unrecoverable if absent)
    confidence: float     # 0..1, placeholder heuristic
    observation_id        # derived: f"{channel}:o{seq:06d}" — no uuid, no wall-clock
```

**Channel families map 1:1 to the five future real sources:**
`inventory.dispenser.count`, `inventory.station.<sid>.buffer_balls` (load cells) ·
`scan.zone.<zid>.balls` (facility scanning) ·
`robot.<rid>.{battery_frac, payload_balls, activity, location, destination, assigned_zone,
health, estop_latched, awaiting_human}` (fleet telemetry) ·
`env.site.*` (**reserved namespace only** — the sim has no dynamic weather; nothing synthetic
is faked for it) · plus facility systems: `wash.washer.wip`, `staff.site.{busy, queued}`.
Canonical units at the contract boundary (balls, 0–1 fractions, seconds); adapters convert,
contracts never do.

## Synthetic bank: imperfection knobs + isolated RNG

Per-channel-family `ChannelImperfection` (every field tagged `source: placeholder`):
`noise_rel_sd`, `noise_abs_sd`, `calibration_bias_rel`, `delay_s`, `dropout_prob`,
`cadence_s` — all defaulting to 0 (perfect/instant), so the zero-config bank copies truth
with **no arithmetic** (exact parity, no float drift). Delay is a bank-owned ring buffer;
dropout emits `MISSING` readings with confidence 0; past-freshness readings emit `STALE`
with decayed confidence.

**RNG isolation (the load-bearing decision):** *stateless per-reading derivation, no shared
generator at all* —

```python
rng = default_rng(SeedSequence([sim_seed, TELEMETRY_TAG, sha256(channel)[:8], reading_seq]))
```

Zero draws from the sim's five spawned streams; reading N of channel C is a pure function of
that tuple, so **adding, removing, or reordering channels can never shift any other
channel's noise** — the structural fix for the exact coupling class that made
`sensed_zone_counts()` unsafe in Phase 1 — and any reading is recomputable offline without
replaying an episode. The bank never calls the sim's RNG-drawing sensed accessors (static
AST ban extended: `sensed_zone_counts`, `sensed_battery_frac`, `_rng_*`, `spawn`).

## Assembler: field sourcing (no second source of truth)

`SENSED_FIELD_PREFIXES` — a frozen whitelist in `observations.py`, imported by the assembler, the docs,
and the tests as the *single* statement of which FacilityState fields come from sensors:
ball-flow counts, all snapshot fields, fleet/charging derivations, staff busy/queued,
`meta.t_s`. Everything else is explicit non-sensor input:

- **`SiteConfig` (static)**: `total_balls`, washer throughput/batch, charger slots, staff
  capacity, `wet_ground_speed_multiplier`, zone/station identity, operating hours
  (→ `facility_open`, `minutes_to_close`), scenario name, seed.
- **`UpstreamInputs` (services)**: demand forecast buckets and demand history
  (`demand_balls_total/served`, `stockout_minutes`, `service_availability`) — a
  POS/forecasting service in deployment; `sim.forecast_window()`/`sim.metrics` in sim.

`conserved` stays the derived property it is: under sensor noise it may honestly read
`False` — in deployment that is a consistency alarm, not a broken invariant.
`clean_sensed` is sourced from the same RNG-free delayed buffer read `build.py` uses
(`sensed_dispenser_count()`), making parity on that field exact by construction.
Missing channels: the assembler backfills last-known-good (or the site-config prior at
t=0) so FacilityState stays total; the missingness itself is explicit in
`AssemblyReport.missing_channels` / `stale_channels` — never a `state.py` change.

**In simulation, `build_facility_state(sim)` remains canonical for the live loop; the
assembled state is the deployment-path rehearsal.** The parity test is what keeps the two
from drifting silently.

**Confidence semantics:** `Reading.confidence` (continuous sensor trust) maps at the
assembly boundary onto Phase 2's provenance grades: OK@1.0 ≙ HIGH; degraded/stale readings
ground the LOW grade that `decisions.Confidence` explicitly reserves "for future
sensed-only rules". `AssemblyReport` carries per-group grades; `decisions.py` is unchanged
this milestone.

**Relationship to the sim's `SensorConfig` path:** `_sensor_proc`/`sensed_*` are the RL
agent's partial observability *inside* the env loop, RNG-coupled such that E1 replay is
byte-identical. The telemetry bank is an *outboard* observer with independently derived
randomness modeling deployment sensing. They deliberately coexist; unification (teaching
the env to consume TelemetryFrames without perturbing `_rng_sensors` draw order) is
deferred until after the RL milestones.

## Test plan (`tests/telemetry/`)

1. **Contract purity** — subprocess with simpy/gym/numpy/pyarrow blocked imports
   `readings.py`.
2. **Neutrality** — full episode sampling every channel every step vs never: byte-identical
   event log, metrics, obs digest; all five sim RNG streams unmoved.
3. **Parity** — knobs at 0, `scenario.sensors` noise zeroed (explicit precondition):
   assembled state `==` built state, dataclass equality, swept across a whole day.
   Knobs on: `to_dict()` diff is a **subset** of `SENSED_FIELDS` (subset, not equality —
   noisy values can coincide with truth).
4. **Order-independence** — adding a channel to the config changes no other channel's
   readings (the SeedSequence scheme's proof).
5. **Byte-reproducibility** — same (seed, config) → identical serialized frames; readings
   recomputable offline as pure functions.
6. **Provenance** — sorted-keys serialization; AST bans (`time`/`datetime`/`uuid`/`random`,
   sensed accessors, `_rng_*`); no upstream file mentions `nxt_telemetry`; placeholder tags
   present; MISSING readings are explicit entries.

## YAGNI (deferred)

Weather/env dynamics (namespace reserved, nothing faked); sensor fusion / Kalman filtering;
per-sensor health models; streaming/async transport; unit registry; memory-store wiring
(frames are serialization-ready; recording them is a later, one-line driver concern);
SensorConfig/env unification; scan pose/geometry payloads; config-file loaders; real
hardware adapters (the contract is their spec, not their implementation).

## Risks

- The assembler's snapshot construction imports `nxt_range_ops.core.entities`, whose
  package init pulls the simulator — hence assembler is designated sim-side, and only
  `readings.py` claims stdlib purity. A future truly-pure deployment assembler would need
  the snapshot classes' import path decoupled (deferred; noted, not needed for the swap
  proof).
- SeedSequence semantics must be pinned by the reproducibility test so a numpy upgrade
  can't silently re-key the noise.
- Parity depends on the documented `scenario.sensors`-zeroed precondition; the test states
  it loudly so nobody "fixes" a red test by loosening equality.
