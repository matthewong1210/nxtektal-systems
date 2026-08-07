# Phase 2 — Decision Functions & Manager Briefing, design spec

**Date:** 2026-08-07 · **Status:** Approved (with two founder adjustments, reflected below)
**Builds on:** PR #9 (`nxt_facility` FacilityState contract)
**Architecture ladder:** FacilityState → Decision Rules → Recommendation → Human/Agent
Interface → *Future Execution Layer (not built here)*.
The output answers: *"If this were a real driving range, what should the manager do next?"*

**Founder adjustments:** (1) Recommendations are operational, not robot-directive-specific —
`directive_hint` is replaced by `affected_resources` / `expected_outcome` / `confidence`;
the layer describes desired operational outcomes, never execution commands. (2) Strictly
deterministic and rule-based — no LLM, no planners, no RL, no agent framework.

Hard exclusions honored: no LLM agents, no planners (no search/optimization/rollouts/
lookahead), no RL, no Omniverse. Rule-based, single-step, deterministic functions only.

Three designs were drafted (rules-minimal / manager-voice / Site-OS-contract) and
adversarially critiqued against the actual shield and sim code; this is the winning hybrid.
The critique verified every rule against `SafetyShield` so no recommendation can advise an
action the shield would reject — verified first-hand in `safety.py`.

## Files

**New** (zero `nxt_range_ops` changes, zero `state.py`/`build.py` changes):

| File | Purpose |
|---|---|
| `nxt_facility/decisions.py` | `Urgency` enum (`NOW`/`SOON`/`WATCH`), frozen `Recommendation` dataclass, placeholder-tagged threshold constants, the 8-rule catalog, `recommend(state, ...) -> tuple[Recommendation, ...]` |
| `nxt_facility/briefing.py` | `render_briefing(state, recommendations=None) -> str` — plain-text manager briefing |
| `scripts/facility_briefing_demo.py` | Runs an existing scenario under a baseline policy, prints the briefing every N sim-minutes (shows advice evolving NOMINAL → STRAINED → CRITICAL). May import the sim; contract modules may not. |
| `tests/facility/test_decisions.py`, `test_briefing.py` | Per-rule trigger + non-trigger cases, determinism, vocabulary pins, briefing content |

**Changed:** `nxt_facility/__init__.py` (exports — `decisions`/`briefing` import only
`.state`/`.analysis`, so no new lazy-loading needed), `docs/facility_state.md` (Phase 2
section), existing blocked-import subprocess test + AST scans extended to the two new
contract modules.

## Recommendation shape

```python
class Urgency(str, Enum): NOW; SOON; WATCH
class Confidence(str, Enum): HIGH; MEDIUM; LOW   # deterministic, provenance-based

@dataclass(frozen=True)
class Recommendation:
    rule_id: str                        # stable slug, e.g. "battery_reserve"
    urgency: Urgency
    action: str                         # imperative operational headline
    affected_resources: tuple[str, ...] # sorted resource ids: "robot:R1",
                                        # "zone:Z4", "station:H1", "washer",
                                        # "dispenser", "staff", "charger"
    expected_outcome: str               # what improves if the manager acts
    confidence: Confidence              # HIGH = ledger/snapshot truth;
                                        # MEDIUM = forecast-derived (inherits
                                        # forecast bias); LOW reserved
    rationale: str                      # one sentence with concrete numbers
    def to_dict(self) -> dict           # the "agent interface" serialization
```

Confidence is not a probability: it is a deterministic grade of the *evidence provenance*
per rule (ledger/snapshot facts → HIGH; projections over the frozen, possibly biased
forecast → MEDIUM), consistent with the house placeholder/provenance policy.

`recommend()` is pure over FacilityState, RNG-free, sorted by `(urgency rank, rule_id,
subjects)`. Thresholds are module constants (`source: placeholder`), overridable as keyword
arguments — **not** a new FacilityState field group (that would break every hand-built
fixture; recorded as the designated follow-up when real telemetry integration needs
config-sourced limits): `MIN_BATTERY_RESERVE_FRAC = 0.15`, `BATTERY_MARGIN_FRAC = 0.05`,
`ZONE_WORTH_COLLECTING_BALLS = 50`, `PAYLOAD_FULL_FRAC = 0.8`,
`BUFFER_NEAR_FULL_FRAC = 0.9`, `MAX_ROBOTS_PER_ZONE = 2` (mirrors the shield's
occupancy-cap default).

Robot activities are matched as string literals (`"idle"`, `"failed"`, …) because importing
`nxt_range_ops.core.entities` at runtime would drag the simulator into the contract modules
(Phase 1 lesson); a pin test asserts every literal equals the real enum value. (An earlier
draft carried a `directive_hint` field and its vocabulary pin test — both removed by the
founder's adjustment: recommendations name resources and outcomes, not directives.)

## Rule catalog (8 rules, shield-aware)

Every rule was checked against `SafetyShield` so advice is always *executable* the moment
it is given:

1. **stockout_in_progress** (NOW) — `clean_available <= 0` (the `eta == 0.0,
   limited_by == "none"` case all three original designs missed): "Stockout in progress —
   N balls washable; get payloads to stations." Fixes the shared blind spot.
2. **stockout_dirty_supply** (NOW if ETA ≤ 30 min, SOON if ≤ 120) — `limited_by ==
   "dirty_supply"`: dispatch idle, charged, non-full robots **one per zone** to the richest
   open zones **below the occupancy cap**, counting *commitments* (`assigned_zone` across
   robot snapshots, which includes robots still traveling — the shield's own accounting)
   rather than physical `robots_present`. When no pair exists, the fallback rationale names
   the actual binding side (fleet readiness vs zone availability).
3. **stockout_demand_bound** (SOON) — `limited_by == "demand"`: the washer is the
   bottleneck; collection cannot help. Honest manager-level advice: "washer at max
   X balls/min vs forecast Y — brief the front desk on a possible shortfall."
4. **battery_reserve** (NOW) — operable, not charging/queued, `battery_frac ≤ reserve +
   margin`: send to charge *before* the shield starts rejecting collection assignments at
   the reserve floor (early-warning framing; the shield's actual behavior).
5. **robot_down** (NOW / WATCH split) — `health == "failed"` or `estop_latched`, **without**
   a pending request → `request_human_assistance` (NOW). Robots already `awaiting_human` →
   staffing-triage framing only (WATCH: "2 assist requests queued, 1 of 1 staff busy") —
   never a duplicate request, which the shield rejects.
6. **idle_capacity** (SOON) — idle, `battery > reserve + margin`, `payload <
   PAYLOAD_FULL_FRAC × capacity` (shield rejects assignment for full robots), open zone with
   ≥ 50 balls: assign via the same one-per-zone allocator; robots already dispatched by
   rule 2 are excluded (no duplicate advice).
7. **payload_stranded** (SOON) — idle with `payload ≥ 0.8 × capacity`: send to the open
   station with the lowest buffer fill.
8. **station_buffer_pressure** (WATCH) — station buffer ≥ 90% capacity: name the least-full
   open alternative.

Dropped from the drafts, with reasons: *free_charger_slot* (near-dead — the sim charges
robots to exactly the target then auto-releases, so the trigger is essentially never
observable); *never-empty briefing guarantee as a test* (a quiet STRAINED state would fail
it; the header's `classify_state` verdict already carries that signal — the all-clear line
renders only when zero rules fire); *SafetyLimits as a FacilityState group* (breaks
hand-built fixtures; params-with-defaults is strictly smaller).

## Briefing format (the product)

```
FACILITY BRIEFING — normal_weekday, day minute 612 (10:12), seed 42
Status: STRAINED · Clean stock 1,840/8,000 (23%) · Projected stockout: ~47 min (supply-limited)

DO NOW
 1. Get R3 charged — R3 battery at 17%, within 5% of the 15% reserve floor; 1 of 2
    charger slots free. Outcome: Robot stays assignable — below the reserve floor,
    new collection work is blocked until it recharges.
DO NEXT HOUR
 2. Get R1 collecting in zone Z4 — projected stockout in ~47 min is
    washer-supply-limited; zone Z4 holds 3,120 balls (0 robots present) and R1 is
    idle at 82% battery. Outcome: Washable supply rises. [confidence: medium —
    forecast-derived]
KEEP AN EYE ON
 3. Relieve pressure on station H1 — buffer at 1,104/1,200 (92%); route the next
    handoff to H2 (408/1,200, queue 0). Outcome: Unloading never blocks on a full
    buffer.

Thresholds are engineering placeholders; projections use the frozen day forecast.
```

Plain text, three tiers, every line = imperative + numbers + why. Pure string formatting.

## Test plan

Per-rule trigger and non-trigger unit tests on hand-built states (each rule provably
non-vacuous — the Phase 1 review lesson: baselines chosen so *only* the condition under
test fires); allocator determinism and one-per-zone property; output ordering pinned;
enum-literal and directive-vocabulary pin tests; blocked-import subprocess test extended to
`decisions.py`/`briefing.py`; briefing test asserts header fields, tier labels, and
all-clear behavior. Existing RNG/trajectory/boundary guards run unchanged (nothing touches
the sim).

## YAGNI (deferred)

Config-file thresholds; cost/ROI weighting (the ROI engine stays a separate project);
recommendation history/dedup across snapshots; travel-time/ETA math per robot; multi-rule
conflict resolution beyond the rule-2/6 robot dedupe; JSON schema files; viewer/Streamlit
wiring; closed-loop execution of any kind.
