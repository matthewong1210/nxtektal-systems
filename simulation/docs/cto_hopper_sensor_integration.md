# CTO Hopper-Sensor Integration Seam

This document fixes the handoff boundary between the physical
hopper-detection program under separate development and the Site OS,
and records the current product truth about both sides. Nothing here
is implemented against a real device in this repository: the Pilot
Site Agent Service V0 is fixture-backed, no physical device connected,
and connecting one remains a separately reviewed, high-risk
architecture boundary per
[`.agent/context/deployment.md`](../../.agent/context/deployment.md).

## The actual prototype (product truth)

The CTO prototype (repository `stevenguo-stack/NXTektal-Sensor-Prototype`)
is **not a load cell**. It is:

- a **Heltec Wireless Tracker** development board running Arduino
  firmware;
- an **STMicroelectronics VL53L4CX** **Time-of-Flight** ranging sensor
  on I2C (SDA/SCL plus XSHUT), read through the ST multi-ranging API.

Per measurement cycle the current firmware obtains a multi-ranging
result and prints, over the serial monitor as human-readable text:

- `NumberOfObjectsFound` — the number of returns in the measurement
  (the VL53L4CX can report several objects per cycle);
- per object, a **distance in millimeters** (`RangeMilliMeter`, the
  `distance_mm` fact) and the ST **`RangeStatus`** measurement-status
  code;
- a **temporary simulated low-level threshold**: a hard-coded
  `LOW_BALL_THRESHOLD_MM` compare that prints a low-ball warning when
  the measured distance reaches it (a larger distance to the ball
  surface means an emptier hopper). This is a demo aid inside the
  firmware, not a calibrated product fact and not a Site OS input.

The current serial output is unframed diagnostic text. It carries no
device identity, no firmware version, no sequence numbers, no uptime
or timestamps, no checksums, and no calibration identity yet.

## Compatibility truth: this is not a load-cell input

The Site Agent fixture path exercises the existing **load-cell**
adapter family: `LoadCellSample` / `LoadCellProfile` are
**mass-specific** contracts — a raw mass reading in a declared mass
unit (`kg`/`g`), an adapter-local tare, a mass-per-ball coefficient,
and a capacity bound. A Time-of-Flight distance is none of those
things:

- The prototype's output is **not directly compatible with
  LoadCellSample**. Do not reinterpret `distance_mm` as a load-cell
  mass, and do not disguise the ToF sensor as a load cell by feeding
  distance through mass-shaped fields — that would launder a ranging
  measurement into a fabricated mass provenance.
- No ranging/ToF adapter family exists in the edge adapter kit today;
  the V0 families are load-cell, digital-I/O, and robot-status.
- The current Site Agent fixture therefore remains **load-cell-shaped**
  by design: it demonstrates the service, admission, evaluation,
  workflow, and recovery path over the adapter family that exists.

What that means for reuse:

- **Reusable as built**: the Site Agent application/service boundary,
  the versioned Manager API, the Manager Console, workflow-enablement
  gating, evidence stores, and restart/recovery. None of these encode
  the sensor family; they consume canonical observations, envelopes,
  and evaluations.
- **Not yet reusable for this sensor**: the raw-to-canonical
  conversion layer. Integrating the VL53L4CX requires a new
  **ranging adapter family** (and its raw-sample contract) plus a
  transport reader, each through its own **separate architecture
  review**. Neither exists in this repository, and this document makes
  no decision about their design.

## The three layers of the future integration

The seam is three separately owned layers. Only layer A exists today,
in prototype form, outside this repository.

### A. Firmware / wire message (CTO program)

A production-ready wire message must carry, explicitly framed rather
than as free-form serial text:

- device identity (which commissioned sensor this is) and firmware
  version;
- a monotonic message sequence and device uptime, so loss, reordering,
  and resets are detectable;
- the measurement itself: `distance_mm` per detected object,
  `RangeStatus` per object, and `objects_found`
  (`NumberOfObjectsFound`);
- an overall sensor status (ok / fault / not-ready), so silence and
  failure are distinguishable;
- the calibration identity the device asserts, so the Site OS can
  check it against the commissioned binding.

The firmware's simulated low-level threshold is a bench aid; the wire
message should report measurements and status, not derived
low-ball verdicts.

### B. Future transport reader (composition-root side)

A serial (or other transport) reader that decodes the framed wire
message and owns delivery: framing and corruption handling, a
**durable cursor** with resume (the same
peek / acknowledge / reject-with-sequence-reuse and
one-decision-per-peek semantics the runtime port documents — an
in-memory cursor that restarts at zero deadlocks against the retained
invalid-sequence incident), site-time timestamping of when readings
were taken and became available, and redelivery of unacknowledged
batches. It lives outside every existing package, exactly like the
fixture feed's composition root, and requires its own architecture
review before it is built.

### C. Future Site OS ranging adapter (edge adapter kit side)

A new ranging adapter family that validates the commissioned sensor
identity and calibration identity against the commissioned site,
preserves the raw ranging evidence (distances, `RangeStatus` values,
object count) in its diagnostics rather than discarding it, fails
closed on unknown identities, calibration mismatches, out-of-range or
non-finite values, and silence (explicit MISSING, never zero), and
produces only an approved canonical inventory or level representation.

## The open representation decision (not made here)

Which canonical representation the ranging adapter should produce is
an unresolved design question that must go through its own **separate
architecture review**, with commissioning and policy owners at the
table. The candidate shapes include:

- mapping calibrated ToF distance into an **estimated ball count**
  through a provenance-bearing calibration model;
- producing a **coarse level/threshold observation** (for example a
  low/ok level fact with its own canonical channel and unit, which
  today has no canonical destination and would need a reviewed
  vocabulary addition);
- or introducing **another bounded representation** entirely.

This correction makes none of those decisions. In particular, do not
assume distance-to-ball-count conversion is linear or simple: hopper
geometry, sensor placement, ball surface variation across the cone of
view, multiple returns per measurement, occlusion, and the calibration
model itself all affect the mapping, and any conversion coefficients
must carry their own provenance rather than being invented.

## Ownership summary

| Concern | Owner |
|---|---|
| Firmware and the framed wire message (layer A) | the CTO sensor program, outside this repository |
| Physical transport, framing, durable cursor, resume (layer B) | a future transport reader, outside every existing package; separate architecture review required |
| Raw-to-canonical ranging conversion, calibration/identity validation, diagnostics (layer C) | a future ranging adapter family in the edge adapter kit; separate architecture review required |
| Quality admission, sequencing, publication | the state orchestration layer via the existing pipeline, unchanged |
| Policy and manager recommendations | `nxt_pilot_ops` (Guardian, trace, workflow, ledger) via `nxt_agent_runtime`, unchanged |
| Service lifecycle, Manager API, Manager Console | `nxt_site_agent` and `apps/site-agent-console`, reusable unchanged |

Upstream inputs (POS/forecast facts) and the canonical channels no
edge adapter family covers are supplied by the composition root, not
by the sensor program.

## What the sensor program must never do

The sensor program must not construct `FacilityState`; issue
recommendations; write to the Shadow Ops ledger; call the Agent
Runtime, the Site Agent service, or their stores directly; call
robots, actuators, ROS, Nav2, or any emergency-stop surface; treat a
missing reading as zero; report its bench threshold as a calibrated
fact; invent calibration; or silently change units. It emits framed
raw ranging measurements at the wire boundary and nothing else.
