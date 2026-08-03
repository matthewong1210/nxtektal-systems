# Isaac Sim integration plan (Phase 1)

Status: **not started — blocked on hardware + supplier assets.** Nothing in
Phase 0 requires Isaac Sim; this documents exactly how the mock is replaced.

## Environment requirements (none exist on the current dev machine)

The current machine is an Apple-Silicon Mac (M4): **Isaac Sim cannot run on
it** (no NVIDIA GPU; macOS unsupported). Options, in preference order:

1. **Linux workstation** — Ubuntu 22.04/24.04, NVIDIA RTX GPU (>= 8 GB VRAM,
   RTX 4080/5080+ or RTX A-series recommended), recent NVIDIA driver.
   Isaac Sim >= 4.5 installed either as the standalone download or via
   `pip install isaacsim` packages into a Python 3.10/3.11 env.
2. **Cloud instance** — AWS g5/g6e, GCP g2, or an OVX instance; same install.
3. Isaac Sim in a container (`nvcr.io/nvidia/isaac-sim`) with the NVIDIA
   Container Toolkit — good for headless parameter sweeps in CI.

Installing any of the above is a documented, deliberate step on that machine —
never an implicit side effect of this repo. `scripts/inspect_environment.py`
(read-only) verifies a machine before starting.

Skills already present in this repo's Claude environment that apply here:
`omniverse-cad-to-simready` (supplier CAD -> SimReady USD conversion,
validation, packaging) and `omniverse-realtime-viewer` (USD viewing).

## Asset pipeline (once supplier data arrives)

1. AgileX URDF -> Isaac Sim URDF importer -> articulated robot USD.
   AgileX CAD (STEP) for parts without URDF -> `omniverse-cad-to-simready`
   workflow -> SimReady USD with physics/materials.
2. Basket/lift/tilt mechanism CAD -> articulated USD (prismatic lift joint,
   revolute tilt joint) matching `HandoffMechanismConfig` joint limits.
3. Equipment drawings -> minimal inlet/collision USD per model, stored beside
   its `configs/equipment/*.yaml` profile.
4. Facility stage: 10 m x 10 m plane, slope from
   `scenario.facility.ground_slope_deg`, station pad, AprilTag prims from the
   equipment profile's `docking_marker_pose`.

## IsaacSimAdapter implementation checklist

The stub (`nxt_sim/adapters/isaac_sim/isaac_adapter.py`) documents the
method-by-method mapping. Implementation steps:

1. Build the stage from a `ScenarioBundle` (poses, slope, equipment USD,
   initial error applied to the robot spawn pose).
2. Implement `RobotTaskInterface`:
   * `navigate_to_pose` / `return_to_charge`: differential controller or Isaac
     ROS Nav2 to target prims.
   * `approach_handoff_station`: synthetic camera + AprilTag detection
     (replaces the mock's `marker_alignment_gain` abstraction with a real
     perception loop).
   * `dock`/`undock`: velocity command into/out of the physical guide mesh;
     PhysX **contact report** APIs feed collision_count, min clearance, and
     `peak_contact_force_n` (set `contact_force_available: true`).
   * `lift`/`dump`/`lower`: articulation position targets with joint-limit
     checks from the mechanism config.
   * `verify_docking`: contact sensor prims on the guide rails.
   * `verify_unloading`: count RigidBody ball prims remaining in the basket
     volume. **Still not a validated granular-flow model** — bridging/jamming
     stay physical-test risks; the sim only catches gross geometry problems.
   * `emergency_stop`: zero all targets, latch; obstacle detection via a range
     sensor prim against `safety.emergency_stop_distance_m`.
3. Implement `get_telemetry()` with the keys listed in
   `nxt_sim/interfaces/telemetry.py`.
4. Run headless (`--no-window`) for sweeps; the existing sweep runner and
   reports work unchanged (`scenario.adapter: isaac_sim`).
5. Acceptance gate: the full pytest suite must pass with the mock, and a
   nominal Isaac scenario must reproduce the mock's state sequence with
   physics-derived metrics populated.

## What Isaac adds over the mock

Real contact forces and collision geometry, marker-perception realism,
physically-derived docking tolerance envelopes, and (approximate) tipping
behavior — replacing `static_margin_heuristic_v0`. What it still does not
validate: granular ball flow at scale, weathered outdoor surfaces, real sensor
noise. Those remain physical-test items (docs/assumptions.md).
