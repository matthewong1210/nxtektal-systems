# Course World Model V0

`nxt_course_world_model` is the immutable, versioned spatial-truth layer for
one commissioned outdoor facility: a compact Course World Model derived from
*processed* course-scan products, plus the pure, deterministic, read-only Map
Query Service over it. It is the shared spatial baseline the course workflows
(and, later, cart localization, inspection coverage, condition geolocation,
maintenance routing, landing-point queries, and player map context) all bind
to by exact model identity and version.

Everything in this repository is synthetic.
SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA. No real course was
scanned, no survey accuracy was validated, and no live cart, camera, launch
radar, robot, or customer deployment exists.

The product principle is: map deeply, inspect continuously, maintain
proactively. The engineering meaning of "map once" is narrower and exact:
establish the spatial baseline once, create immutable controlled revisions
when the course changes, and bind every spatial observation and query to an
exact model identity and version.

## What this is not

- **Not `CommissionedSite`.** Commissioning remains the sole owner of
  site/deployment identity, the surveyed coordinate reference system, the
  facility origin, zone definitions, assets, sensor bindings, calibration,
  and their provenance. The Course World Model *references* that identity
  (site, deployment, CRS, origin, commissioned zone IDs) and
  `validate_model_against_site` fails closed on any mismatch; it duplicates
  no commissioned fact and never writes one back. The two lifecycles also
  differ: a changed physical declaration receives a new deployment identity,
  while a course re-scan produces a new immutable `model_version` for the
  same deployment.
- **Not `FacilityState`, telemetry, or a live observation stream.** The
  model is slow-changing static truth; it carries no live value, no
  observation, and no assembly quality, and it is never published through
  the state envelope. Do not put the model, a point cloud, or a mesh into
  `FacilityState` or an agent prompt.
- **Not the digital twin.** The twin remains a downstream projection and is
  never a source of spatial truth. The twin's approved "no separate
  world-model database" rule governs its *dynamic* world model (the
  FacilityState stream × declared layout × time); this package owns a
  different fact class — static, versioned, scan-derived spatial truth — and
  the twin may later consume its serialized model exactly like `layout.json`:
  as a declared static projection input, regenerable and never authoritative.
- **Not workflow readiness.** Requirement definitions, verdicts, and the
  enablement report keep their existing owner. This package never imports
  the readiness layer and the readiness layer never imports this package:
  composition roots derive plain-data Course Model evidence and hand it over.
- **Not a scan pipeline.** No LAS/LAZ or point-cloud parsing, no
  photogrammetry, no orthomosaic processing, no drone SDK, no SLAM, no
  map-building daemon, no remote map download. Raw survey artifacts stay
  outside the repository and outside runtime; the model references processed
  sources by stable URI, digest, type, capture identifier, pipeline
  identifier, and commissioned-style provenance only.
- **Not a route planner, navigation stack, geofence enforcer, physics
  engine, or command surface.** A restricted-area answer is spatial
  information only. Nothing here can reach a cart, robot, actuator, ROS, or
  emergency-stop API, and the architecture guard bans every such import and
  token mechanically.

## Architecture placement decision (recorded)

The pre-implementation gate evaluated five placements:

- **A — extend `nxt_commissioning`:** rejected. Commissioning is stdlib-only
  static deployment truth, immutable per `(site_id, deployment_id)`; folding
  in dense elevation data, golf semantics, and queryable functions would
  change the manifest schema (stored-manifest migration, canonical bytes)
  and conflate two lifecycles (deployment commissioning vs map revisions).
  It would also push a closed golf vocabulary into the deliberately
  free-form commissioned `zone_type`.
- **B — a new canonical leaf package:** accepted, as part of E. The model is
  a distinct fact class (versioned scan-derived spatial truth) with a
  distinct lifecycle (controlled map revisions), a stable public contract,
  an allowed dependency position (leaf over `nxt_commissioning`, the same
  consumption tier as the readiness layer), and a mechanical boundary guard.
- **C — extend `nxt_range_twin`:** rejected. The twin is projection-only by
  approved design ("geometry to facts, never facts to geometry"; if the twin
  and its inputs disagree, the twin is wrong). Making it own canonical
  spatial truth would invert that rule, and its optional `pxr` gating is the
  wrong home for a pure contract.
- **D — scripts-only composition root:** rejected for the contract. A
  persistent versioned schema, digest/revision semantics, and a reusable
  query API with a failure taxonomy need a package and a dependency guard —
  the same reasoning that made the edge adapter kit and the readiness layer
  packages. Accepted for fixtures, evidence derivation, file IO, and demos.
- **E — small canonical spatial package plus scripts-level composition:**
  **chosen.** `nxt_course_world_model` owns the contracts and queries;
  `scripts/pilot_course_a_course_model_fixture.py` and
  `scripts/course_world_model_demo.py` own composition, evidence
  derivation, and files.

Gate outcome: **Proceed.**

## Ownership map

| Fact | Owner |
|---|---|
| Site/deployment identity, surveyed CRS, facility origin, commissioned zones, provenance vocabulary, canonical JSON idiom | `nxt_commissioning` (unchanged; imported public surface) |
| Course-local frame declaration and its commissioned-CRS binding | `nxt_course_world_model.frame` |
| Elevation surface, bilinear/slope queries | `nxt_course_world_model.elevation` |
| Deterministic 2D geometry rules (rings, polylines, containment, overlap) | `nxt_course_world_model.geometry` |
| Semantic course vocabulary (holes, surfaces, cart paths, restricted areas) and scan-source references | `nxt_course_world_model.features` |
| Model identity, bounds, validation, canonical serialization, content digest, revision semantics, site binding | `nxt_course_world_model.model` |
| Read-only Map Query Service, query statuses, trajectory/terrain intersection | `nxt_course_world_model.query` |
| Course Model *evidence* shape, map-requirement versions, readiness verdicts | the workflow-enablement layer (unchanged owner; new v2 requirement sets) |
| Evidence derivation, fixtures, file IO, demo | `simulation/scripts/` composition roots |

## Coordinate frame

One explicit course-local frame per model (`CourseCoordinateFrame`):

- right-handed local ENU: X = east, Y = north, Z = up;
- distance unit: metres on every axis;
- origin: the commissioned facility origin, recorded in the commissioned
  CRS's own coordinates (`origin_crs_x/y/z`) so alignment is checkable;
- commissioned CRS binding: kind (`epsg` or `local_cartesian`), identifier
  (for example `EPSG:32651`), horizontal/vertical units (must be metres),
  and axes (exactly `east/north/up` or `x/y/z`) — the same identity gate the
  commissioning twin-layout projection applies;
- vertical basis: an explicit declaration. The V0 pilot declares local
  height in metres above the commissioned facility origin elevation, not an
  orthometric datum;
- model bounds: the closed course-local rectangle the elevation grid covers.

Course-local coordinates relate to the commissioned CRS by translation only
(the house rule: no geographic transform is guessed). Rotation/grid-
convergence handling, device frames, and cart-pose transforms are future
contracts and are deliberately absent. NaN, infinities, boolean-typed
numbers, non-metre units, malformed EPSG identifiers, missing origins, and
unsupported axes are rejected at construction.

## Elevation surface

A regular finite node grid (`ElevationGrid`), the smallest representation
that supports deterministic local queries:

- `origin_x/origin_y`, positive finite `cell_size_m`, `n_rows`/`n_cols`
  (each at least 2), and exactly `n_rows * n_cols` finite heights;
- canonical row-major ordering from the south-west node: row 0 is
  southernmost, column 0 westernmost;
- coverage is the closed rectangle to `origin + (n - 1) * cell_size` per
  axis; the maximum edges belong to the last cell;
- bilinear interpolation inside a cell; slope is the analytic gradient of
  the deterministically selected bilinear patch, so cell-edge behavior is
  defined, not hidden;
- out-of-coverage queries fail loudly; there is no extrapolation;
- `resolution_m` (the cell size) is declared metadata reported by every
  elevation/slope query. Resolution and any accuracy notes are declared
  evidence, never verified physical performance.

## Semantic geometry

Closed vocabularies, deterministic rules:

- **Primary surfaces** (`tee`, `fairway`, `rough`, `green`, `bunker`,
  `water`): simple implicitly closed polygon rings; at least three
  vertices; no duplicate consecutive vertices; no explicit closure (the
  last vertex must not repeat the first); no self-intersection; non-zero
  area; either winding; every vertex inside model bounds. Primary surface
  interiors are mutually exclusive — shared edges are legal, shared
  interior area fails validation, and the interior-overlap decision is
  complete for simple rings: proper edge crossings, strict vertex
  containment, collinear shared sub-segments with both interiors on the
  same side, and exact partition-midpoint containment are all evaluated
  in exact rational arithmetic, so witness-evading concave or collinear
  overlaps cannot pass. Rings are closed point sets, so a
  boundary point is contained; a point on a shared boundary resolves by
  the fixed tie-break (green, tee, bunker, water, fairway, rough; then
  lexicographic feature ID).
- **Holes**: identity (`hole_id`, unique positive `hole_number`) plus a
  boundary ring; hole interiors must not overlap. A feature that
  declares a hole must be geometrically consistent with it: every
  vertex inside the hole's closed boundary and no edge properly
  crossing it, so a declared attribution can never contradict the
  geometric hole context a query reports.
- **Overlays**: cart paths (a polyline centerline with positive finite
  width) and restricted zones (`no_go`, `maintenance_only`) may coexist
  with any primary surface; queries report the overlay alongside the
  primary classification instead of resolving it away. A restricted zone
  may reference a commissioned zone ID; the referenced zone must exist in
  the validated site, and `validate_model_against_site` also reconciles
  it spatially — the commissioned polygon, translated into the course
  frame by the same subtraction-only re-origining the commissioning
  twin-layout projection uses, must share area with the model's
  restricted polygon, so a reference cannot point at one zone while
  drawing another. There is no second zone registry.
- **Hazards** are exactly the `bunker` and `water` surfaces — no separate
  hazard store.
- Feature identifiers are unique across holes, surfaces, cart paths, and
  restricted zones. Exact-arithmetic predicates (`Decimal` over the string
  form of each coordinate) back the orientation/intersection/area rules.
- Inspection-permitted areas are deliberately absent in V0: commissioned
  zones still carry no inspection semantics
  (`inspection_zone_definition` remains unsupported), and adding a
  half-defined overlay would imply Inspection Coverage progress that does
  not exist.

## Model identity, versioning, and digest

`CourseWorldModel` is frozen and self-verifying:

- identity: `course_model_id`, `model_version`, optional
  `supersedes_version` (never itself), timezone-aware `effective_from`,
  `site_id`, `deployment_id`;
- `display_name` is presentation only: it is serialized but excluded from
  the content digest, so relabeling can never change identity;
- `content_digest` is `sha256:` over the canonical JSON of the identity
  payload; the constructor recomputes and rejects any mismatch, so a model
  object with drifted bytes cannot exist. `verify_model_payload` performs
  the same check on a raw payload. **A digest proves content consistency
  only — it is not a signature and proves nothing about authorship,
  surveying accuracy, or authenticity**; provenance requires obtaining the
  model from a trusted composition root;
- serialization: `dumps_model` emits canonical JSON (sorted keys, compact
  separators, `allow_nan=False`) plus one trailing newline, byte-identical
  across processes and `PYTHONHASHSEED` values; collections are canonically
  sorted at construction, and every numeric contract value is stored as a
  float, so authoring order and int-versus-float spelling of the same
  value can never change bytes or the digest;
- numeric contract bounds: every coordinate, height, and length must stay
  within one gigametre (`±1e9 m`) and the grid cell size at or above one
  micrometre, so interpolation, slope, distance, and clearance arithmetic
  is provably overflow-free and a finite-but-absurd value can never turn
  into a silently wrong answer;
- revisions: models are immutable; a course change produces a new
  `model_version` whose `supersedes_version` names the current version.
  `validate_revision` fails closed on identity drift (model ID, site,
  deployment), coordinate-frame drift (re-referencing needs its own future
  revision semantics), an unchanged version, a wrong supersession target,
  and a non-increasing `effective_from`. `require_consistent_content`
  rejects two models claiming one version with different digests;
- scan provenance: at least one `ScanSourceReference` (stable URI, source
  type, capture ID, pipeline ID, `sha256` digest of the processed product,
  commissioned-style `Provenance`). Raw scan bytes never enter the package
  or the repository.

## Map Query Service

`MapQueryService(model)` is pure and read-only: no mutation, no file, no
network, no clock, no randomness. Malformed questions (non-finite or
boolean inputs, invalid radii, malformed trajectories, frame mismatches)
raise `CourseModelQueryError`; valid questions with negative answers return
explicit statuses (`out_of_bounds`, `unclassified`, `no_hole`,
`no_intersection`, `unprovable`) — never an optimistic value. Every result
carries a compact `ModelRef` (model ID, version, content digest, frame ID,
resolution) so a stale or foreign map can never answer silently, and never
the model's own geometry payload.

| Query | Returns |
|---|---|
| `get_elevation(x, y)` | interpolated elevation, or an explicit out-of-bounds status |
| `get_surface(x, y)` | deterministic primary classification, feature and hole context, cart-path and restricted overlays |
| `get_slope(x, y)` | `dz/dx`, `dz/dy`, magnitude, grade percent, downhill aspect (degrees clockwise from north; `None` on flat ground), resolution |
| `get_hole_context(x, y)` | containing hole (never inferred outside every boundary), primary surface, deterministic distances to that hole's green and tee where they exist |
| `get_nearby_hazards(x, y, radius_m)` | bunker/water hits within a positive finite radius, sorted by (distance, feature ID) |
| `is_restricted(x, y)` | restricted or not, with matched zone IDs, categories, and commissioned zone references — spatial information only |
| `intersect_trajectory_with_terrain(samples, frame_id=...)` | the first terrain crossing of an already-computed trajectory |

### Trajectory boundary

The trajectory query only intersects an externally supplied, ordered,
finite sample sequence (strictly increasing `t_s`, declared frame equal to
the model frame) with the terrain surface. Terrain is bilinear per grid
cell, so the clearance along each straight segment between consecutive
samples is piecewise quadratic; the query splits every segment at its
grid-line crossings and solves each piece analytically, which finds a
crossing *inside* a segment even when both sample endpoints are above
terrain (no tunneling through a ridge) and always reports the first
contact. The straight segments themselves remain the caller's sampling
of the flight path — the query never invents curvature between samples.
It rejects fewer than two samples, non-finite or out-of-contract-bound
values, unordered times, frame mismatches, a start outside bounds or
at/below terrain (ambiguous), and a trajectory entirely outside the
model; a track that leaves the modeled area while still airborne is
`unprovable` and a track that stays strictly above terrain to its end is
`no_intersection` — an intersection is never fabricated. Shot physics — aerodynamics, drag, Magnus force, wind,
launch-monitor interpretation, bounce/roll, landing prediction — is
explicitly not implemented here; that is the later Shot Intelligence
Simulation milestone, and `deterministic_landing_model_owner` remains an
unowned prerequisite.

## Workflow-enablement integration

Direction (unchanged owners, no new imports between packages):

```text
CourseWorldModel (validated, site-bound)
    -> composition root derives plain-data CourseModelEvidence
    -> workflow-enablement evaluators cross-check it against the
       validated commissioned site
    -> Grounds map prerequisites and the Player Caddy map-query
       prerequisite become SATISFIED
    -> both course workflows remain NOT_READY on everything else
    -> Range Operations readiness is byte-identical either way
```

`CourseModelEvidence` carries the model identity, content digest, frame
and CRS identity, the commissioned-origin claim, the supported query
kinds, and the resolution. In-package cross-checks reject evidence naming
another site, another deployment, a different CRS identity, or a different
facility origin. The required query kinds are pinned two-directionally
against the real query-service surface by
`tests/workflow_enablement/test_map_query_parity.py`.

Because requirements v1 defined the map prerequisites as *definitionally
missing* ("no Course World Model owner exists anywhere in the
repository"), making them evidence-evaluable is a semantic change, so the
two course workflows moved explicitly to
`course.grounds_condition_intelligence/requirements/v2` and
`course.player_caddy_experience/requirements/v2`. Evaluators pin the
version they implement and fail closed on any other, so a stale v1
registry can never be evaluated under v2 semantics silently. Range
Operations is untouched at `requirements/v1`.

### Why Grounds and Player Caddy remain NOT_READY

A map alone is not a workflow. Grounds Condition Intelligence still lacks
cart-node identity, cart-pose and camera bindings, camera
intrinsic/extrinsic calibration references, the camera-to-cart transform,
a time-sync profile, inspection-zone semantics, and every deferred
inspection/condition/briefing/review contract. Player Caddy Experience
still lacks cart pose, session contracts, consent/privacy and retention
policies, pseudonymous identity, the launch-monitor adapter, the
ball-found event, and named owners for the landing model and player-facing
recommendations. Course workflows have no runtime at all:
`runtime_assembly_eligible` is structurally false, the launch planner
refuses them, and no state, evaluation, or evidence directory can exist
for them.

## Pilot fixture and demo

`scripts/pilot_course_a_course_model_fixture.py` builds **Pilot Course A —
Synthetic Hole 7 Spatial Baseline** for the shared enablement deployment
(`pilot-course-a` / `pilot-a-enablement-v0`): one hole boundary, a tee,
fairway, two rough patches, green, greenside bunker, water pond, a cart
path, two restricted zones (one referencing commissioned zone `Z1`), a
non-flat planar-sloped elevation grid covering 300 m x 200 m (31 x 21
nodes at 10 m spacing),
synthetic scan provenance, and the commissioned EPSG:32651 frame binding.

```bash
uv run --no-sync python -B scripts/course_world_model_demo.py \
  --out reports/course-world-model
```

The demo rejects an invalid model, builds and site-binds the valid one,
demonstrates canonical serialization, digest verification and tamper
refusal, runs every query kind, evaluates workflow enablement before and
after Course Model evidence, asserts in-process that the Range Operations
section is byte-identical in both reports, writes `course_model.json`,
`query_results.json`, `workflow_enablement_before.json`,
`workflow_enablement_after.json`, and `course_world_model_demo.json`
under `--out/<site_id>/<deployment_id>`, refuses non-empty, file-valued,
or unprovable output roots, and produces byte-identical stdout and
artifacts across repeat runs and documented `PYTHONHASHSEED` values.

## Next seams (not implemented)

- **Cart Pose and Camera Calibration:** commissioning schema extensions
  with their own reviews — cart-node assets, pose/camera channels in the
  closed telemetry vocabulary, intrinsic/extrinsic calibration reference
  contracts, a camera-to-cart rigid transform, and a time-sync profile.
  Future device poses transform into the course frame through those
  contracts; this package only fixes the target frame they must land in.
- **Shot Intelligence Simulation:** a separately reviewed owner for
  launch-monitor interpretation and a deterministic landing model. It may
  *consume* `intersect_trajectory_with_terrain` and the semantic surfaces;
  nothing about ball flight lives in this package.
- **Twin consumption:** the serialized model can become a declared static
  input to the USD projection (real terrain instead of the placeholder
  plane) without changing twin ownership rules.

## Known V0 limitations

- One synthetic hole; no claim of a complete course, and course-boundary
  containment of features is not enforced (bounds containment and
  declared-hole containment are).
- The elevation surface is a synthetic plane-based fixture; accuracy and
  resolution are declared metadata, not measured performance.
- Cart-path corridors are validated by centerline vertices; a corridor
  half-width may touch the model boundary.
- `vertical_basis` is a validated free-form declaration, not a closed
  vocabulary; a future datum/extrinsics consumer that must machine-check
  vertical compatibility will need a closed vocabulary revision.
- `TrajectoryIntersectionResult` carries the spatial contact only; a
  time-of-impact field is an additive future extension for the Shot
  Intelligence consumer.
- The map-query parity pin requires exact equality between the required
  and offered query-kind sets, so an additive future query kind is a
  deliberate coordinated change with the readiness layer (a drift alarm,
  chosen over silent divergence).
- Evidence fields the commissioned site cannot falsify (model identity,
  content digest, frame ID, resolution, supported queries) remain
  declared trust made true by the composition root — see the
  workflow-enablement contract.
- Revisions validate pairwise (`current`, `candidate`); a persistent
  revision-chain store is future work.
- No device-frame transforms, no live updates, no map distribution, no
  multi-resolution tiles.

## Verification

From `simulation/` with the all-extras environment:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/course_world_model
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/workflow_enablement
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/commissioning
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider  # full suite
uv run --no-sync python -B scripts/validate_configs.py
```

plus the architecture/import/safety subset and the packaging/distribution
checks in `docs/CI.md`. The architecture guard for this package is
`tests/course_world_model/test_architecture.py`; the demo determinism guard
is `tests/course_world_model/test_demo_script.py`.
