# Digital Twin / Spatial Intelligence Layer — Phase 0 Design (approved)

Status: **approved 2026-08-07** with founder adjustments (§0). Implementation plan: `spatial_twin_plan.md`.
Produced by the standard milestone protocol: recon (16 read agents across two panels), three-lens
design panel, two adversarial reviewers; both reviewers converged on this merged design.

Positioning note (external): this layer is described as a **digital twin / spatial intelligence
layer for managed outdoor facilities** — not a general outdoor world model. v1 makes no claim
beyond the driving-range facility domain.

---

## 0. Approved founder adjustments (binding)

1. **Projection principle:** the Digital Twin is a projection of FacilityState, never a source
   of truth.
2. **No separate world-model database.** The v1 spatial world model IS:
   FacilityState stream + geometry + site identity + time.
3. **Boundaries preserved:** no new physics engine; no new simulator; no inferred facts from
   visualization; no recommendations stored in twin artifacts; no hand-authored operational state.
4. **External positioning:** digital twin / spatial intelligence layer for managed outdoor
   facilities — never framed as a general outdoor world model.
5. **Implementation priority:** (1) deterministic FacilityState stream artifact →
   (2) USD generation from that artifact → (3) synchronized visualization.
6. **YC demo priority:** optimize for *"an AI system understands and explains the state of a
   physical facility"* — not visual realism. The briefing/explanation surface outranks the
   3D hero render.

Core principle, restated as a truth rule: **the unit of autonomy is the site, not the robot —
therefore the unit of truth is the site's state contract, not any rendering of it.** In the target
ladder (Physical World → Sensors/Simulation → Facility State → Spatial World Model → Operational
Intelligence → Execution), the v1 twin is a read-only leaf hanging off Facility State; the arrow
into Operational Intelligence is a named seam (§5), not a v1 feature.

## 1. Entity inventory

Governing rule: **the twin may add geometry to facts, never facts to geometry.** If FacilityState
or `layout.json` carries the fact, the twin shows it; invented visuals are tagged
`source: placeholder`; if neither, the twin renders nothing (no decorative fiction).

| Entity | Data exists today | Placeholder in v1 | Deferred |
|---|---|---|---|
| Terrain | Nothing — only `Point2D`, no extents/elevation | One flat ground plane bounding the layout | Survey/GIS ground, netting, tee line |
| Zones Z1–Z6 | Positions (layout.json), `balls`, `is_open`, `robots_present`; ZONE_CLOSED/REOPENED in event vocabulary | Zone extent discs (no radius exists anywhere) | Real polygons from survey |
| Robots R1–R3 | Discrete node `location`, `destination`, `activity`, `health`, `battery_frac`, payload, estop/awaiting flags | Proxy meshes; node-anchor placement + deterministic co-location offsets. Real episodes also emit the discrete location `transit` (mid-travel); this is rendered by holding the last node position — no invented kinematics. | Continuous pose (contract has none; `debug.robot_positions` stays a labeled debug channel); real CAD/SimReady assets |
| Dispenser | Position (0,0); `clean_available` AND `clean_sensed` — two attrs, never merged | Proxy mesh + fill-scaled ball pile | Real asset |
| Station H1 / Charger | Positions, docks/slots, queues, buffer; outage events | Proxy meshes | Dock geometry stays in nxt_sim's handoff lab (pure-vocabulary boundary) |
| Washer | Throughput/batch/`wip` — **aspatial by contract** (no position exists) | **Nothing spatial.** Non-transformed data prim under `/Aspatial`; `position: null` is schema-legal. Any on-stage washer visual is look-layer set dressing only | Real placement from survey |
| Staff | `capacity`/`busy`/`queued` (resource pool; no positions) | Nothing spatial — no walking avatars until a staff position model exists upstream | Staff localization |
| Environment | Closures/outages: real boolean state + transition events. Wet ground: `environment.wet_ground_speed_multiplier` (already in FacilityState) | State-driven display changes; wet-ground badge/tint | Real weather model (upstream first) |
| Ops/KPI facts | Demand group, `service_availability`, `stockout_minutes`, staff, fleet, conservation flag | Numeric attrs on non-geometric `/Ops` scope | — |

Permanent non-goals: individual balls are never rendered (counts only); the twin never becomes a
data lake.

## 2. FacilityState → USD mapping

Sibling package `nxt_range_twin`; builder core stdlib + `pxr` only (`usd-core==26.8` pinned,
verified installable under the repo's Python 3.13.14 venv on this Mac — authoring/validation is
local; rendering is not: the pip wheel excludes usdview/imaging and Kit/ovrtx require
Linux+NVIDIA). Two layers: static `range_base.usda` + timesampled `episode.usda`. `nxt:`-namespaced
attributes. Honest held-sample teleports — **no interpolation** (at 60 s cadence, lerped motion
asserts speeds nobody measured). `timeCode = t_s`, `timeCodesPerSecond = 600` (a 16 h day scrubs
in ~96 s). No live code in v1; `time_code=None` seam reserved.

1. **Stream as first-class artifact.** No time-ordered FacilityState stream exists today. The
   capture script (script tier) re-runs an episode deterministically
   (`RangeOpsEnv + make_baseline + reset(seed)`) calling the RNG-neutral, guard-tested
   `build_facility_state(sim)` once per control step → `facility_states.jsonl`
   (schema `facility-state-stream/v1`, one `FacilityState.to_dict()` per line, sorted keys),
   plus `events.jsonl` drained via `EventLog.since()`. The USD builder consumes ONLY
   `layout.json` + these artifacts — file-contract coupling, stronger than an import boundary.
2. **Checked mapping table.** `mapping.py` enumerates every emitted attr ← source field. Drift
   fails loud in both directions: unknown input keys abort; a derivation-audit guard walks the
   built stage and fails any `nxt:` attr not in the table.
3. **Identity mirrors nxt_memory.** `customLayerData` carries EpisodeMeta vocabulary (site_id,
   deployment_id, episode_id = `{scenario}-seed{seed}`, simulator_version, git_commit,
   schema_version), input-file SHA256s, mapping version, and the layout DISCLAIMER verbatim.
   Store: `reports/digital_twin/<site_id>/<deployment_id>/<episode_id>/`.
4. **Aspatial scope** for washer and staff: attributes, no transform.
5. **Cosmetics quarantined.** Derived display (closure hatch, health color) computes from contract
   fields at build time; a separate look layer holds prettiness and may never carry `nxt:` attrs.

Prim sketch:

```
/World
  /Site
    /Terrain                (placeholder plane)
    /Zones/Z1..Z6           (disc extent placeholder; nxt:balls, nxt:is_open, nxt:robots_present)
    /Robots/R1..R3          (translate timesampled to node anchors, held; nxt:activity, nxt:health,
                             nxt:battery_frac, nxt:payload_balls, nxt:estop_latched, nxt:awaiting_human)
    /Stations/H1            (nxt:is_open, nxt:docked, nxt:queue_length, nxt:buffer_balls)
    /Charger                (nxt:slots, nxt:in_use, nxt:queue_length)
    /Dispenser              (nxt:clean_available, nxt:clean_sensed)
    /Aspatial/{Washer,Staff}  (attributes, no transform)
  /Ops                      (demand, service_availability, minutes_to_close, wet_ground, fleet)
```

## 3. Truth tiers

Four fact classes, one owner each:

1. **Simulation truth** — nxt_range_ops runtime: SimPy state, five RNG streams, true pre-sensing
   counts, `robot.travel` tuples, EventLog. Three sanctioned exits only: FacilityState, the
   EventLog, and the viewer's labeled debug channel.
2. **Contract snapshot** — nxt_facility: FacilityState, the single source of dynamic truth for
   everything below, including facts about sensing (`clean_sensed` is a recorded reading).
3. **World-model state** — nxt_range_twin. **v1 conclusion (approved): it IS exactly
   {FacilityState stream} × {declared geometry} × {site identity}, with time as a first-class
   axis — zero novel facts.** What makes it a layer, not a file format: it is time-ordered (first
   persisted FacilityState series anywhere), spatially anchored, and site-identified.
4. **Presentation** — proxy geometry, extents, colors, cameras. Lossy is fine; additive is
   forbidden.

**Growth seam:** a reserved, empty-in-v1 `estimates.usda` layer (`nxt:est:*`) with a fixed
admission rule — an estimated fact enters only as a pure, versioned function of (stream, layout,
events) or a new declared sensor input with its own provenance record; hand-authored state is
inadmissible. A v1 guard test enforces emptiness.

**Negative space (guard-testable):** the world model may never contain interpolated positions or
invented kinematics; results of RNG-drawing calls (`sensed_zone_counts()`,
`sensed_battery_frac()`); wall-clock timestamps or uuids; scores/rewards/success flags (memory's
no-score rule extends — the twin describes, never grades); recommendations, directives, or
verdicts (a second record of decisions is forbidden); any mutation of its inputs.

**Ladder honesty:** decision rules keep reading FacilityState directly and must never read USD.
The Facility State → World Model → Intelligence arrow is realized later only via a typed frozen
`SpatialSummary` contract — never `pxr` imports in nxt_facility. Real perception, when it exists,
enters upstream: it feeds FacilityState's sensed fields through the existing
SensorConfig/`clean_sensed` slot, not a parallel store.

## 4. No second source of truth

1. One-way, file-coupled flow: sim → {layout.json, facility_states.jsonl, events.jsonl} → USD.
2. No-feedback rule extended: upstream packages never mention `nxt_range_twin` (string-scan
   test); the twin's read path is excluded for the decision layer in v1.
3. Regenerate, never mutate: artifacts are build products; byte-reproducibility is the standing
   proof the twin adds no information. pxr text-serialization determinism is a **release blocker**
   if it fails (W1 spike).
4. **Conflict rule:** if the twin and FacilityState ever disagree, the twin is wrong by
   definition. Remedy: delete-and-rebuild; hand-patching is detectable (manifest input hashes).
5. Geometry has one derivation path, enforced as a cross-artifact equality guard test
   (layout.json vs the twin's static layer) — a test, not a sideways import of viewer code.
6. Cross-harness consistency guard: `episode.json` frames must agree with
   `facility_states.jsonl` on shared fields for the same (scenario, seed).
7. Provenance everywhere; RNG discipline inherited verbatim at script tier.

## 5. Growth seams (no v1 code)

- **Synthetic scenario generation:** the twin is fully data-driven off layout.json — generated
  layouts render with zero twin changes. Future USD→ScenarioConfig importer is design-time
  authoring: **up and over, never sideways** — authored USD enters the ladder at the top as
  config, never promoted to state.
- **Real sensor ingestion:** the seam is `facility-state-stream/v1` itself — a real site's
  telemetry adapter emits the same records; the builder does not change. Live view rides the
  reserved `time_code=None` seam.
- **Real facility deployment:** identity already solved (driver-supplied site_id/deployment_id,
  memory's convention); per-site `range_base.usda` replaces placeholder geometry when survey data
  exists.
- **Prediction of operational outcomes:** twin timecodes and MemoryWindow sequences share
  sim-time + identity keys → offline joinability with Phase 3 history, reusing memory's impact
  vocabulary verbatim; never read by the live loop. **Branch-and-simulate is explicitly not
  promised:** the sim initializes only from config+seed; no state→sim-init path exists.

## 6. YC-demo milestone

Claim proven (per adjustment 6): **"An AI system understands and explains the state of a physical
facility."** Understand/explain = the existing deterministic briefing/recommendation layer
narrating state — boring, deterministic, auditable is the pitch. Visualize = projections of one
state stream. Visual realism is explicitly not the goal.

- **Never-cut floor:** scrubbing Streamlit 2D viewer + synced plain-English briefing panel on a
  disruption episode, deterministic to the byte. This alone meets the milestone.
- **Disruption episode:** station outage + robot failure from existing generators. (Verified: no
  generator sets `closure_windows`; ZONE_CLOSED never fires today. A zone-closure beat, if ever
  wanted, is a script-tier external ScenarioConfig — never a new generator in nxt_range_ops.)
- **Briefing-sync mechanism (decided):** the capture script precomputes `recommend()` +
  `render_briefing()` per control step → demo-tier `briefings.jsonl` sidecar stored OUTSIDE
  `reports/digital_twin/` (recommendations never enter twin artifacts), stamped with git_commit,
  regenerate-only. `FacilityState.from_dict` is rejected — an unsanctioned extension of the
  frozen contract.
- **Hero 3D render (optional, deprioritized):** pre-rendered Omniverse video needs remote
  Linux+NVIDIA (ovrtx); W1 book-or-cut gate. On-stage caption is "same state stream, same
  timestamps" — never "frame-for-frame identical".
- **Motion honesty:** 60 s held-sample cadence is narrated ("state snapshots every minute — an
  ops view, not a video game"). A 10 s-interval variant is a DIFFERENT operating day (6× decision
  cadence), default off.

Status: implemented through Phase C (Tasks 1–11); hero 3D render deferred pending remote GPU (W1 book-or-cut).
