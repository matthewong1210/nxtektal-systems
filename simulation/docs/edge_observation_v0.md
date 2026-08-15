# Edge Observation Adapter Kit V0

`nxt_edge_observation` converts **already-read** equipment and vendor-shaped
samples into the existing canonical `nxt_telemetry.observations.Observation`
boundary. It is transport-neutral, fixture-backed, and deliberately incapable
of touching a physical facility.

## What this is not

V0 does **not** connect to physical Modbus devices, serial ports, MQTT, Kafka,
OPC-UA, ROS 2, Nav2, an AgileX or other vendor SDK, robot motors, actuators,
emergency-stop circuits, live cameras, or a cloud service. There is no live
load-cell, washer, or robot telemetry, no deployed Edge Gateway, no customer-site
installation, no validated sensor accuracy, no production Modbus/MQTT/ROS
integration, no physical command admission, and no robot execution. Every
sample in this repository is synthetic and labelled
`SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA`.

The mechanical guards in `tests/edge_observation/test_architecture.py` enforce
the absence: the package may import exactly one first-party module
(`nxt_telemetry.observations`), and every transport, network, process,
robotics, wall-clock, randomness, and execution import root is banned.

## Ownership

| Fact | Owner |
|---|---|
| Physical site/deployment identity, surveyed layout, assets, capabilities, safety limits | `nxt_commissioning` (`CommissionedSite`) |
| The closed canonical channel vocabulary, each channel's canonical unit, allowed sensor types, and the asset it binds to | `nxt_commissioning.validation` |
| Calibration identity and validity | `nxt_commissioning.CalibrationInfo` |
| Load-cell/polarity/battery-unit conversion coefficients and declared vocabularies | **adapter-local device profiles** (see the contract gap below) |
| Raw device payload normalisation, binding application, device-data validation, canonical Observation production, conversion diagnostics | `nxt_edge_observation` |
| The Observation contract and state assembly | `nxt_telemetry` |
| Sequencing, publication-quality admission, envelope, checkpoints | `nxt_site_runtime` |
| Evaluation lifecycle, evaluation journal, manager-decision queue | `nxt_agent_runtime` |
| Policy evaluation, trace, trust, workflow, ledger | `nxt_pilot_ops` |

The adapter kit owns **none** of the rows above it or below it. It defines no
`Observation`, `ObservationFrame`, `FacilityState`, `AssemblyReport`,
`SequencedObservationFrame`, or any other canonical contract; a guard test
fails if such a class name is ever defined inside the package.

## Package placement

Evaluated alternatives and why they were rejected:

- **Adapters inside `nxt_telemetry`** — rejected. `nxt_telemetry` owns the
  canonical Observation contract and state assembly. Adding device- and
  vendor-shaped payloads plus a second diagnostics report there would blur that
  boundary, introduce commissioning-projection coupling into the contract
  package, and force its existing guard test to be loosened to permit
  transport-adjacent code.
- **Adapters inside `nxt_commissioning`** — rejected. Commissioning is
  stdlib-only static physical truth and must never handle runtime samples.
- **Adapters inside `nxt_site_runtime`** — rejected. The runtime is
  orchestration only and owns no observation semantics.
- **Everything in `simulation/scripts/`** — rejected for the conversion logic:
  a tested, reusable contract with its own failure taxonomy needs a package and
  a mechanical dependency guard. Accepted for the *source composition*.

The chosen shape is the hybrid: **pure conversion in a package, source
composition in a composition root.**

```text
nxt_commissioning.project_telemetry_adapter_config(site)   (one-way projection)
        +  adapter-local device profiles
                 |
                 v
        nxt_edge_observation            (pure conversion; stdlib + Observation)
                 |
                 v
   scripts/pilot_course_a_edge_fixture  (composition root: ObservationSource)
                 |
                 v
          nxt_site_runtime  ->  nxt_agent_runtime
```

`nxt_edge_observation` must not import `nxt_site_runtime`: the repository
invariant is that only the designated `nxt_agent_runtime` composition layer may
depend on the runtime. `SequencedObservationFrame` and the `ObservationSource`
protocol are therefore assembled at the composition root, which also supplies
the `UpstreamInputs` and `SourceReference` facts an edge device cannot know.
The package instead exposes `FixtureRawSampleFeed`, which owns the
at-least-once cursor (peek / acknowledge / reject / explicit exhaustion) over
raw batches with exactly the semantics the runtime port documents.

The package consumes the commissioning **projection dictionary** rather than
importing `nxt_commissioning`. That keeps the projection one-way and disposable,
keeps the package stdlib-pure, and keeps the manifest authoritative.

## Raw-to-canonical coverage matrix

`CommissionedSite` binding → canonical channel → Observation → assembler
destination → `FacilityState` field.

### Supported in V0

| Raw input | Family | Commissioned binding | Canonical channel | Observation value | Assembler consumer | FacilityState field |
|---|---|---|---|---|---|---|
| Dispenser hopper mass | load cell | `load_cell`, unit `balls`, calibrated | `inventory.dispenser.count` | int count | `_ChannelView.count` | `ball_flow.clean_available` |
| Dispenser hopper mass | load cell | `load_cell`, unit `balls`, calibrated | `inventory.dispenser.sensed` | float quantity | `_ChannelView.get` | `ball_flow.clean_sensed` |
| Washer drum mass | load cell | `load_cell`, unit `balls`, calibrated | `wash.washer.wip` | int count | `_ChannelView.count` | `ball_flow.in_wash`, `washer.wip` |
| Station buffer mass | load cell | `load_cell`, unit `balls`, calibrated | `inventory.station.<sid>.buffer_balls` | int count | `_ChannelView.count` | `ball_flow.dirty_buffered`, `stations[].buffer_balls` |
| Equipment-ready contact | digital I/O | `proximity_switch`, unit `1` | `station.<sid>.is_open` | bool | `bool(view.get(...))` | `stations[].is_open`, `environment.stations_open` |
| Handoff dock occupancy | digital I/O | `proximity_switch`, unit `count` | `station.<sid>.docked` | int 0/1 | `_ChannelView.count` | `stations[].docked` |

`station.<sid>.docked` is a **count** of docked robots. A single discrete
input can only express 0 or 1, so the digital mapping is honest **only for a
single-dock station**. A multi-dock station needs one input per dock plus an
aggregation rule; V0 does not aggregate, because summing independently
commissioned points into one canonical channel would be an unreviewed
composition contract. The Pilot Course A fixture declares one dock.

| Zone gate contact | digital I/O | `proximity_switch`, unit `1` | `zone.<zid>.is_open` | bool | `bool(view.get(...))` | `zones[].is_open`, `environment.zones_open` |
| Robot activity | robot status | `external_system`, unit `1` | `robot.<rid>.activity` | declared label | `RobotActivity(...)` | `robots[].activity`, `fleet.*` |
| Robot health | robot status | `external_system`, unit `1` | `robot.<rid>.health` | declared label | `RobotHealth(...)` | `robots[].health` |
| Robot battery | robot status | `external_system`/`battery_monitor`, unit `1` | `robot.<rid>.battery_frac` | fraction 0–1 | `_ChannelView.frac` | `robots[].battery_frac` |
| Robot payload | robot status | `external_system`, unit `balls` | `robot.<rid>.payload_balls` | int count | `_ChannelView.count` | `robots[].payload_balls`, `ball_flow.in_transit` |
| Robot location | robot status | `external_system`, unit `1` | `robot.<rid>.location` | declared label | string | `robots[].location`, `zones[].robots_present` |
| Robot destination | robot status | `external_system`, unit `1` | `robot.<rid>.destination` | label or `""` | string | `robots[].destination` |
| Robot assigned zone | robot status | `external_system`, unit `1` | `robot.<rid>.assigned_zone` | zone id or `""` | string | `robots[].assigned_zone` |
| Robot e-stop observation | robot status | `external_system`, unit `1` | `robot.<rid>.estop_latched` | bool | `bool(...)` | `robots[].estop_latched` |
| Robot awaiting-human flag | robot status | `external_system`, unit `1` | `robot.<rid>.awaiting_human` | bool | `bool(...)` | `robots[].awaiting_human`, `fleet.awaiting_human` |
| Robot heartbeat age | robot status | (timing, not a channel) | every `robot.<rid>.*` | drives `OK`/`STALE` | staleness math | quality gate rejection |

### Canonical channels with no V0 adapter family

These exist in the commissioned vocabulary but no dispenser/digital-I/O/robot
adapter can honestly produce them. The Pilot Course A fixture supplies them
separately as explicitly `SIMULATION`-typed facility-system inputs; they are
never presented as adapter output.

| Canonical channel | Why V0 cannot produce it | Next seam |
|---|---|---|
| `scan.zone.<zid>.balls` | Requires a lidar/camera/RFID scanning rig; a load cell cannot count balls on a field | a scanning-rig adapter family |
| `station.<sid>.queue_length` | A discrete input cannot express a queue depth | a station-controller/external-system adapter |
| `charger.site.queue_length` | Canonical unit is `count`, not a ball-denominated mass | a charger-controller/external-system adapter |
| `staff.site.busy` | System-of-record fact, not an edge device | a staffing/POS external-system adapter |
| `staff.site.queued` | System-of-record fact, not an edge device | a staffing/POS external-system adapter |

### Raw device fields with no canonical destination anywhere

Reported in `EdgeAdapterReport.unmapped`, never dropped and never a reason to
widen `FacilityState`.

| Raw field | Why it has no channel |
|---|---|
| `washer_running`, `washer_fault` | `WasherState` carries throughput, batch size, and WIP only; there is no washer run-state or fault channel or field |
| `basket_present` | no canonical basket concept exists |
| `lift_upper_limit`, `lift_lower_limit` | no canonical lift-position concept exists (still checked for impossible combinations) |
| `cabinet_door_closed` | no canonical door channel exists |
| load-cell `diagnostic_code` | no canonical device-diagnostic channel exists |
| robot `fault_code` | `robot.<rid>.health` is the only health destination and it is a closed enum |
| robot `position_x_m` / `position_y_m` | `robot.<rid>.location` carries a *named* location, not a metric pose |
| robot `coordinate_frame` | validated against commissioned spatial truth, but has no channel of its own |

Adding any of these to `FacilityState` is a canonical schema change and is
explicitly **not** made in V0.

## The contract gap: calibration coefficients

`nxt_commissioning.CalibrationInfo` records calibration **identity and
validity** — status, `calibration_id`, `calibrated_at`, `valid_until`, method,
provenance. It carries **no numeric scale, offset, tare, or mass-per-ball
coefficient**, and no other commissioned contract does either.

V0 therefore does **not** invent a commissioned coefficient and does **not**
hard-code a universal golf-ball weight, tare, or capacity. Coefficients are
supplied per device as an adapter-local `LoadCellProfile` /
`DigitalInputProfile` / `RobotStatusProfile` that:

- must declare its own mandatory `provenance` string;
- must carry a `calibration_id` equal to the commissioned binding's identity, and
  fails closed with `calibration_mismatch` otherwise;
- is never written back into the manifest.

The Pilot Course A fixture's constants are labelled
`SYNTHETIC FIXTURE CONSTANT — declared for Pilot Course A only; not a measured
product fact and not a commissioned value`.

**Smallest additive future extension**, not made here: a versioned, optional
`calibration_coefficients` block on `CalibrationInfo` carrying named
`MeasuredValue` entries with their own provenance. Downstream impact would be
the commissioning manifest schema and `from_dict`/`to_dict` round trip,
`validate_commissioned_site`, `project_telemetry_adapter_config`, canonical
manifest bytes and stored-manifest migration, and this adapter layer. That is a
commissioning contract change and requires its own architecture review.

## Adapter behaviour

### Honesty rules

- An untrustworthy reading becomes an explicit `Observation` with
  `status=MISSING`, `value=None`, `confidence=0.0`, plus a named
  `RejectedField`. The Observation contract's own rule is that missing data is a
  real Observation, never an absent key.
- A reading older than its declared `stale_after_s` becomes `status=STALE` with
  capped confidence and keeps its value. It is never relabelled `OK`.
- **A missing load-cell reading never becomes zero inventory.** A genuine
  zero-mass reading is a real `0` with `status=OK`; a missing, faulted,
  uncalibrated, or unit-mismatched reading is `MISSING`.
- A ball count is only ever produced for a binding whose canonical unit is
  `balls`. A load cell bound to a non-ball-denominated channel is rejected with
  `unsupported_unit`.
- Raw fields with no canonical destination are reported as unmapped, never
  silently discarded.

### Fail-closed conditions

`missing commissioned binding`, `unknown source/device ID`, `identity mismatch`,
`calibration missing`, `calibration mismatch`, `unsupported unit`,
`non-finite value` (NaN, infinity, or a bool used as a number), `invalid boolean
encoding` (any non-`bool` digital state is refused at the contract),
`value out of range` (negative net mass, above declared capacity, battery
outside its declared unit range, negative payload), `unknown value` (a label
outside the declared vocabulary, or an unknown device status),
`sample timestamp in the future`, `available timestamp after frame time`,
`available before sample` (refused at the contract), `stale sample`,
`device fault`, `device-reported missing`, `impossible equipment state`,
`coordinate frame mismatch`, `raw payload schema mismatch`, `duplicate canonical
channel`, `conflicting observations for one channel` (both rejected and the
channel demoted to `MISSING`), and `unsupported channel`.

A malformed raw sample therefore cannot become an optimistic default, cannot
produce a misleading `AVAILABLE`/healthy state, cannot advance a Site Runtime
checkpoint, cannot generate a recommendation, cannot create a misleading
`NO_ACTION` evaluation, and cannot acknowledge a frame that has not completed
the canonical lifecycle — the existing Site Runtime `FailureCode` taxonomy and
the Agent Runtime deferred acknowledgement make that decision, not the adapter.

### Physical safety

An observed `estop_latched` value is **telemetry only**. It cannot set, clear,
reset, or influence an emergency stop. Every digital point maps one-to-one to
its own commissioned binding, so no channel's value is ever derived from an
e-stop input. Ordinary remote I/O is never treated as a safety-rated e-stop
source. The package exposes no write, output, coil, register, command,
actuation, motion, navigation, or dispatch surface, and the architecture guard
fails if a function name ever suggests one.

## Adapter diagnostics versus facility truth

`EdgeAdapterReport` (`nxt-edge-observation/adapter-report/v0`) carries
`accepted`, `rejected`, and `unmapped` entries. It is **not** a telemetry
envelope and **not** a second `AssemblyReport`:

- no assembler, `FacilityState` builder, quality gate, policy, or ledger reads it;
- it never enters `FacilitySnapshotEnvelope`;
- a test asserts none of its tokens appear in the published snapshot stream.

It exists so a conversion gap is visible without inventing a canonical fact.

## At-least-once fixture source semantics

`FixtureRawSampleFeed` is a bounded in-memory cursor. It has no thread, polling
loop, filesystem watch, socket, or retry timer, and is deliberately not shaped
like a production transport.

- `peek()` returns the current batch without consuming it; repeated calls return
  an equal value, so a consumer that crashes mid-cycle sees the identical input.
- `acknowledge(n)` advances exactly once; a duplicate acknowledgement raises
  `FeedProtocolError`.
- `reject(n, reason)` discards the batch and **reuses** its sequence number, so
  published snapshot ordering stays contiguous.
- A sequence that does not match the delivered one raises `FeedProtocolError`.
- Exhaustion is explicit: `peek()` raises `FeedExhausted`, which the composition
  root translates into the runtime's `SourceExhausted`.

## Deterministic identity

Canonical Observation identity depends only on the commissioned site, the
binding and calibration, the raw sample, its timestamps, the caller's
`cycle_index`, and the adapter version. It never depends on a wall clock, UUID,
process ID, filesystem path, random value, hash randomisation, or dictionary
insertion order:

- `Observation.seq` comes from the caller's deterministic `cycle_index`, so
  `observation_id` is reproducible;
- observations are emitted sorted by channel and report entries sorted by stable
  keys;
- derived floats are rounded so identical inputs serialise to identical bytes;
- the package imports no `time`, `datetime`, `uuid`, `random`, `secrets`, or `os`
  module, and a guard test bans those roots and the corresponding call names.

No simulator RNG stream is touched: the package cannot import the simulator.

## Pilot Course A — Edge Observation Intake

`scripts/pilot_course_a_edge_fixture.py` builds a synthetic, fully provenanced
`CommissionedSite` with one dispenser, one washer, one charging station, one
collection station (the Universal Handoff), one zone, and two robots, plus 25
sensor bindings. `scripts/edge_observation_adapter_demo.py` runs the bounded
storyline:

| Cycle | Story | Site Runtime | Agent Runtime |
|---|---|---|---|
| 0 | 17:30 calibrated dispenser and equipment state | admitted, seq 0 | `no_action` |
| 1 | 18:30 evening spike has drained the dispenser | admitted, seq 1 | `recommend` → `operator_intervention` |
| 2 | 19:30 stale robot heartbeat | rejected, `stale_observation` | no evaluation |
| 3 | 19:30 uncalibrated dispenser reading | rejected, `insufficient_data_quality` | no evaluation |
| 4 | 19:30 corrected redelivery at the reused sequence 2 | admitted, seq 2 | `recommend` → `operator_intervention` |
| 5 | feed exhausted | not invoked | `source_exhausted` |

The escalation to `operator_intervention` rather than a collector dispatch is
the expected, correct result: the repository-native path deliberately leaves
collection permission, collector capability, ETA, expected yield, and live
washer availability unavailable, and the Guardian fails closed on them. The
fixture does not invent those facts to force a prettier outcome.

Run it with:

```bash
python scripts/edge_observation_adapter_demo.py --out reports/edge-observation
```

`simulation/reports/` is gitignored. The demo refuses to write into a non-empty
evidence directory, and repeated runs produce byte-identical stdout and
byte-identical evidence files.

## Next seam for a real transport

A production reader replaces exactly one object: the composition root's
`FixtureRawSampleFeed`. A real Modbus, MQTT, OPC-UA, or robot-vendor reader must

1. decode its own wire protocol and emit the same `LoadCellSample` /
   `DigitalIOSnapshot` / `RobotStatusSample` shapes;
2. implement the same `peek` / `acknowledge` / `reject` / explicit-exhaustion
   contract, including sequence reuse on rejection;
3. keep site time, not wall-clock time, in `RawSampleTiming`;
4. live outside `nxt_edge_observation`, which must stay transport-free.

Nothing downstream of the adapter kit changes. Such a transport is a new
high-risk boundary — live hardware, vendor integration, and site deployment
remain unimplemented in this repository — and requires its own architecture
review before it is built. Physical command admission and robot execution
remain out of scope entirely and are not reachable from this path.
