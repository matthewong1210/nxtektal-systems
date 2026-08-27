# CTO Hopper-Sensor Integration Seam

This document fixes the exact handoff boundary between the physical
hopper-detection program under separate development and the Site OS.
Nothing here is implemented against a real device in this repository:
the Pilot Site Agent Service V0 is fixture-backed, and connecting a
physical sensor remains a separately reviewed, high-risk architecture
boundary per [`.agent/context/deployment.md`](../../.agent/context/deployment.md).

The design goal of the seam: when the physical program arrives, only
the *source composition* changes. The adapter conversion, Site
Runtime, Agent Runtime, Shadow Ops, the Site Agent service, the
Manager API, the console, recovery, and every piece of evidence stay
exactly as they are.

## 1. The contract the sensor program targets

Produce, or be translated into, the **existing raw-sample contract**
`nxt_edge_observation.LoadCellSample` (already-read device readings;
one sample per commissioned sensor per cycle), grouped into the
existing `RawSampleBatch` with a deterministic `cycle_index` and
`frame_t_s`. Do **not** define a second canonical `HopperSample`
type: the load-cell raw contract already expresses identity, timing,
raw value/unit, device status, calibration identity, and a diagnostic
code. If the wire protocol needs its own DTO, keep it transport-local,
decode it in the reader, and document it as transport plumbing — it is
not a canonical Site OS fact and must never leak past the reader.

## 2. Required identity fields

`sensor_id` must equal the commissioned binding's sensor identity for
the dispenser channels (Pilot Course A: `sensor-lc-dispenser-count`
and `sensor-lc-dispenser-sensed`, bound to `inventory.dispenser.count`
/ `inventory.dispenser.sensed` on asset `DISP1`). Unknown identities
are rejected by the adapter (`unknown_source` / `identity_mismatch`);
the sensor program must not invent identities or channels.

## 3. Required timestamps

`RawSampleTiming(sample_timestamp_s, available_timestamp_s)` in
**site/scenario time seconds, not wall clock**, with
`available_timestamp_s >= sample_timestamp_s` and both no later than
the batch's `frame_t_s`. Future or inconsistent timestamps are
rejected (`future_sample`, `inconsistent_timestamps`); readings older
than the declared staleness horizon become STALE and are then refused
publication by the existing quality gate.

## 4. Measurement value and unit

`raw_value` is the raw measurement (for a load cell, gross mass) in
`raw_unit` matching the adapter profile's declared raw unit (fixture:
`kg`). The mass→count conversion coefficients (tare, mass-per-ball,
capacity) are adapter-local profile facts with their own provenance —
the sensor program must not convert to ball counts itself and must
never silently change units (`unsupported_unit` fails closed).

## 5. Device status / missing / fault representation

`device_status` is `"ok"` or an explicit fault/missing label
(`device_fault`, `device_reported_missing` rejections). A device that
has nothing to report for a cycle must **omit the sample** — the
adapter reconciles silence into an explicit MISSING observation with a
`no_sample` rejection. A missing reading is never zero: never send a
fabricated `0`.

## 6. Calibration identity and provenance

`calibration_id` must equal the commissioned binding's calibration
identity (fixture: `CAL-LC-PILOTA-2026`). No identity, or a different
one, fails closed (`calibration_missing` / `calibration_mismatch`) and
the cycle is rejected before publication — the V0 fixture demonstrates
exactly this. The sensor program must not invent calibration; new
calibration is a commissioning change with its own provenance.

## 7. Sequence and redelivery expectations at the source boundary

The reader replaces the fixture feed and must implement the same
at-least-once cursor the runtime port documents: `peek()` returns the
current batch unchanged until a decision; `acknowledge(n)` advances
exactly once; `reject(n, reason)` discards the bad input but **reuses
its sequence number** so publication stays contiguous; one decision
per peek; explicit exhaustion; and a **restart resume hook**
(delivered-count plus next sequence — the Site Agent service persists
exactly this cursor for the fixture and expects the same from a real
reader). An in-memory cursor that restarts at zero deadlocks against
the retained `invalid_sequence` incident.

## 8–11. Who owns what

| Concern | Owner |
|---|---|
| **Physical transport** (bus, polling, wire decoding) | the future transport reader, outside every existing package; requires its own architecture review before it is built |
| **Raw-to-canonical conversion** (binding, calibration, validation, MISSING semantics, diagnostics) | the edge adapter kit (`nxt_edge_observation`), unchanged |
| **Quality admission, sequencing, publication** | the state orchestration layer via the existing pipeline, unchanged |
| **Policy and manager recommendations** | `nxt_pilot_ops` (Guardian, trace, workflow, ledger) via `nxt_agent_runtime`, unchanged |

Upstream inputs (POS/forecast facts) and the canonical channels no
edge adapter family covers are supplied by the composition root, not
by the sensor program; a pilot deployment needs a real upstream
provider or an explicit declared absence for those channels.

## 12. What the sensor program must never call

The sensor program must not construct `FacilityState`; issue
recommendations; write to the Shadow Ops ledger; call the Agent
Runtime, the Site Agent service, or their stores directly; call
robots, actuators, ROS, Nav2, or any emergency-stop surface; treat a
missing reading as zero; invent calibration; or silently change
units. It emits raw samples at the source boundary and nothing else.
