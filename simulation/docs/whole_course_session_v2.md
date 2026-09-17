# Whole-course camera and continuous range rehearsal V2

Architecture gate: **Proceed**, confined to SIMULATION and the existing owners.
Base: `7b4a9b2`; isolated branch `feature/whole-course-sim-v2`. The frozen V1
background checkout and Claude's planning frontend remain independent.

## Boundary card

| Fact or behavior | Owner and contract |
| --- | --- |
| Static 18-hole geometry and 54 checkpoints | Existing immutable course fixture/map; synthetic, not a survey |
| Seeded weather, material assumptions, cart routes, visitors and requested shots | Immutable session scenario compiled in `scripts/`; bounded horizon and explicit assumptions |
| Conserved range balls, fleet activity, shared staff occupancy and admitted work | Existing `RangeSimulation`, `BallLedger`, `SafetyShield`; opt-in multi-day session, legacy single-day behavior unchanged |
| Rendered camera RGB and reference masks | Offline projection of scenario and current simulated completion evidence; renderer-owned evaluation labels, never detector inputs |
| Visual detection | Separate image-only baseline in scripts; synthetic-domain pixel processing, not a trained/field-validated model |
| Camera pose and image-to-ground projection | Explicit synthetic camera model/calibration; cart position and target position remain distinct |
| Observations | Existing `nxt_telemetry.course_condition` with synthetic evidence references; only supported v0 conditions enter that contract |
| Work release and simulation actions | Observation-derived jobs admitted into predeclared bounded job slots in RangeSimulation; policies choose existing guarded directives; no advisory/LLM direct execution |
| Other course incidents | Explicit synthetic event/report records and human inspection requests, not fabricated robot capabilities |
| Continuation | Composition-root immutable inputs plus deterministic prefix replay and atomic verified cursor; never reconstruct SimPy from a partial state summary |
| Reports and media | Derived artifacts; unavailable/stale regions stay unknown; checkpoint coverage is not area coverage |

Session mode separates daily opening from final session termination. Inventory,
queues, batteries and unfinished work remain in the same runtime across midnight.
Replay resumes a bounded multi-day session without daily reset. A configured
horizon/disk/time budget eventually pauses rather than claiming infinite storage.
Repeated background wakes advance bounded chunks; app/host availability applies.

## Run and inspect

From `simulation/`, use the locked Python environment with `range-ops` and
`course-camera` extras (or the existing all-extras environment). The in-repo
scripts are composition tools, not shipped wheel modules.

```bash
uv sync --locked --all-extras
uv run --no-sync python -B -m scripts.course_session_series run --state-dir /tmp/nxt-course-series
uv run --no-sync python -B -m scripts.course_session_series status --state-dir /tmp/nxt-course-series
uv run --no-sync python -B -m scripts.course_session_series pause --state-dir /tmp/nxt-course-series
uv run --no-sync python -B -m scripts.course_session_series resume --state-dir /tmp/nxt-course-series
```

One call advances at most 720 simulated minutes, within a 240-second soft
execution budget. The default round covers three days, 08:00–18:00 each day,
with real simulated overnight continuation. A later call starts a new seed
after a completed round. **Different rounds are independent experiments and
reset state.** The scheduler is a Codex thread heartbeat, not an OS daemon;
the app/host must be available. A parent pause is checked between chunks; the
individual child session also checks its own control file during advancement.

Open the series `index.html`, then its current round's `report.html` beside the
`media/` directory. This is an interactive saved report, refreshed by successful
chunks. Play/slider controls replay saved routes; they do not run the simulation.
`report.json` contains the full output evidence envelope. `compiled.json` and
`reference/` contain **evaluator-only truth** and must never become policy input.

Each round has a 512 MiB cap; the series has a 1536 MiB cap. It keeps at most
three completed rounds' detailed media, compacting older verified details into
summary/hash records. At most 100 summaries remain, with an accumulated history
digest. Current incomplete evidence is never automatically deleted. A changed
source/configuration/dependency, failed checksum or exhausted current-round
budget stops advancement; do not bypass these checks or edit existing config.

To choose assumptions, supply `--config` on a **new empty series directory**,
using the series `DEFAULT_CONFIG` shape. Its `session_config` allows staff count,
initial dispenser stock, demand scale and an `assumptions` object. Supported
assumptions include rain intensity/arrival reduction/rebound, arrival rate and
stay length, shot cadence, skill/club proportions, range-ball distance factor,
soil drainage, camera cadence/height/FOV/GPS error and picture-quality/outage
probabilities. The compiler validates explicit bounds and ordered profile IDs.
New weather/soil assumptions require a new experiment; historical evidence is
never rewritten to agree with changed parameters.

## Modeled scope and practical limits

- Eighteen holes, three observation checkpoints each, sixteen continuously
  routed carts with dwell periods, reported GPS error and assumed camera aiming.
  Coverage of 54 points is not coverage of every square metre of the course.
- Rain timing/intensity, wind, daylight/clouds, delayed drainage and drying;
  simple water buckets drive grass firmness/sand workability indices and wet
  collection restrictions. They are scenario hypotheses, not measured soil.
- Single divots, disturbed bunker surfaces, pooled water, litter/branches;
  camera blur/occlusion/outage, wind play pauses, visitor surges and stockouts.
  Existing runtime scenarios cover robot battery/collection/unload/washing,
  charging, failures/recovery and their shared human capacity.
- Visitors stay for bounded periods and practice runs with six club classes.
  Carry and roll are separate; requested shots enter the ledger only if served.
  Initial field inventory is explicitly pre-existing stock, not fabricated
  historical shots. Landing samples are historical endpoints, not current
  per-ball positions after collection (the existing ledger resolves zones).
- The driver-only distance anchor is approximately 225 yards **total distance**
  from [Arccos's player sample](https://www.arccosgolf.com/blogs/community/launch-it-like-a-firework-3-driving-distance-insights-you-should-see).
  It is not every player's distance or every practice shot's distance. Other
  distributions and weather responses are configurable assumptions.
- CPU-generated 384×240 PNG frames use textured flat-ground perspective. The
  pixel detector is a fixed synthetic-material baseline. No photorealism,
  trained recognition model, real video feed or real course accuracy is claimed.
  Class-presence evaluation is separate from object-localization accuracy.
- Native observations enter the existing GroundsWorkflow and release bounded
  runtime work slots. An explicit simulated operator confirms them. Staff work
  is selected through the existing policy/action-mask/SafetyShield path. Debris
  uses a separate simulated alert/task because the frozen v0 condition contract
  does not support that class.
- Repair effects in subsequent rendering are assumptions. Verification requires
  later usable pixels plus original-region/uncertainty coverage and minimum
  target scale. A distant view or no detection alone cannot close a case.
  Ordinary camera cadence can leave work awaiting verification. REPHOTOGRAPH
  models staff preparation/request time; it currently does not reroute a cart
  or force a new camera capture. An INSPECT completion never drains water.
- New V2 rounds gather evidence under a fixed dispatch policy. Running them
  repeatedly does **not** train a neural model or automatically improve policy.
  The separate frozen V1 finite-candidate learning experiment remains separate.

Photo truth and detector outputs are physically separate artifacts. Detection
accepts pixels only, not scene condition, hidden coordinates, masks or seeds.
Static camera calibration may project a detected image position to the map.
Only observed evidence can release an inspection/maintenance job; the policy
does not receive future demand, weather events, hidden defects or exact inventory.
Any modeled maintenance effect is explicitly an assumed synthetic response and
does not establish successful repair until a later usable image is inspected.

No production device/transport, physical task admission, paid model API, model
training or automatic promotion to production is introduced. Grass firmness and
sand workability are configurable indices, not inferred physical measurements;
moisture does not universally imply sticky or softer sand. Player/club/shot
distributions are explicit assumptions with source context, not personal facts.

Verification: legacy trajectory parity; midnight/no-reset and replay parity;
ball conservation and served-shot identity; staff capacity/duplicate observation
admission; hidden-input separation; pixel-dependent detection, camera geometry,
blur/occlusion and independent recapture; lock/config/source integrity and budget
limits; focused tests, full Python suite, config validation and repository hygiene.
