# Whole-course monitoring rehearsal V0

Architecture gate: **Proceed**, confined to existing observation/evidence owners
and script composition. This is a SIMULATION rehearsal, not a physical grounds
workflow enablement. Ground-maintenance readiness remains `NOT_READY`.

Implementation branch: `feature/course-monitoring-simulation-v0`, based on local
unmerged planning backend `6175a94` and merged local console fix `fa0af1e`.
These are local implementation dependencies, not claims about upstream main.
The original checkout and both parallel workspaces are unchanged.

## Ownership and boundaries

| Fact or behavior | Owner | Contract |
| --- | --- | --- |
| Synthetic declared site | `nxt_commissioning` | Existing immutable `CommissionedSite` |
| Synthetic 18-hole layout | `nxt_course_world_model` | Existing validated immutable model; no real survey claim |
| Captured condition evidence | `nxt_telemetry.course_condition` | `nxt-course-condition-observation/v0`, strict SIMULATION/synthetic markers, explicit quality, separate cart and target positions |
| Review, priority, assignment, completion and verification evidence | `nxt_pilot_ops.grounds` | Versioned event journal and deterministic replay; no execution authority |
| Fixed scene episodes, sample schedule, demonstration actors and explicit clock | `scripts/course_monitoring_demo.py` | Finite synthetic test inputs; no live facility runtime or state assembler |
| Timeline and manager report | `scripts/course_monitoring_report.py` | `nxt-course-monitoring-report/v0`, read-only derived projection |

Duplication search covered existing Course World Model, commissioning, telemetry,
Facility advice, Shadow Ops, Edge task, readiness and pilot/planning roots.
No new package, policy engine, robot protocol, FacilityState schema, or runtime
is introduced. Ground observations do not enter the range-state assembler;
case evidence never becomes mutable facility truth. The existing ball workflow
and `RangeSimulation`/`BallLedger` are unchanged. Scripts pass plain validated
data between owners; grounds imports neither telemetry nor map implementation.

## Semantics

The map contains 18 holes and 54 declared inspection **points**, not an optical
coverage model. A cart location is not a defect location. Supplied target
coordinates and scores are synthetic and uncalibrated; no camera projection,
computer-vision model, surveyed GPS accuracy, real images or physical connector
exists. The episode specifies changing scene conditions independently of work
completion. It does not establish physical repair effectiveness or detectability.

Unknown, unusable and stale points stay explicit. A clear observation excludes
only its `inspected_condition` at that point/time, not every possible defect or
an entire hole. The report denominator is 54 checkpoints, never course area.
Green checkpoints intentionally include blind spots. Frames expose capture time
and recency; hidden synthetic scene inputs are not passed to the work-order owner.

Human workflow is candidate -> confirmed -> assigned -> in progress -> awaiting
verification -> verified, with dismissal and reopening. Demonstration actions
are attributed to `scripted-demo-operator`; they are not real human approvals.
Manager overrides accept explicit priority, deadline, review and assignment.
Only capable synthetic resources can receive rehearsed work. Divot repair is
human-only; a synthetic bunker robot is an assumption, not a YAWIN product
capability. CE82A ball collection does not imply bunker or turf repair support.
INSPECT does not remove standing water. Completion never proves repair: later,
independent usable evidence of the same point and inspected condition plus an
explicit reviewer decision is required. There is no robot command or actuator.

## Versioning, time and persistence

The new v0 schemas are additive; old planning/range journals are untouched.
Unknown versions/identities and invalid evidence fail closed. All domain time is
explicit UTC; deterministic scenario seed and clock live only in scripts.
Observation IDs use canonical serialized content. Workflow events preserve the
actor, reason and audit chain; replay validates the chain and legal transitions.
An exported journal is verifiable relative to its included head, not externally
anchored against replacement or rollback. Reports are disposable projections.

The CLI runs a finite accelerated day by default; real-time pacing is optional
and finite. Closing the report does not pause or restart any operational service:
the HTML replays saved frames only. No background daemon is installed.

## Verification

Require observation/geometry validation, duplicate/tamper/replay checks,
capability and legal workflow transitions, independent reinspection, explicit
unknown coverage, deterministic end-to-end artifacts, safe HTML escaping,
package-boundary tests, full Python suite and config/repository validation.
Real camera accuracy and robot operation require a separate field pilot.

## Run a saved day

From `simulation/`, with the repository's locked environment installed:

```bash
uv run --no-sync python -B -m scripts.course_monitoring_demo --out /tmp/course-day
```

The default covers eight synthetic hours in five-minute samples. Set
`--duration-minutes 1440` for a 24-hour scenario. `--realtime-speed 1` instead
paces that finite scenario against real elapsed time; it does not install a
daemon. The explicit start must be no earlier than the fixture map effective
date. Night-time camera usability and realistic traffic are not modeled.

Output includes offline `report.html`, printable `report.md`, `report.json`,
`observations.jsonl`, immutable site/map exports, the scripted `scenario.json`,
and a replay-validated `grounds_state.json`. A run writes one completed bundle;
there is no mid-run checkpoint or crash-resume promise. HTML controls replay
saved frames and filters only; they do not approve or dispatch work.

To rehearse a manager's choices, supply `--manager-actions /tmp/actions.json`.
The file is an exact list of actions. This example confirms a photographed
candidate, changes its priority and deadline, then assigns the human crew:

```json
[
  {"minute": 5, "checkpoint_id": "cp-h01-fairway", "operator": "manager-example", "action": "confirm", "reason": "Reviewed the synthetic example"},
  {"minute": 5, "checkpoint_id": "cp-h01-fairway", "operator": "manager-example", "action": "adjust", "reason": "Prioritize opening event", "priority": "URGENT", "deadline_at_utc": "2026-09-16T01:00:00Z"},
  {"minute": 10, "checkpoint_id": "cp-h01-fairway", "operator": "manager-example", "action": "assign", "reason": "Crew available", "resource_id": "grounds-crew"}
]
```

Use `--no-scripted-review` with this example so automatic demonstration
assignment does not precede its explicit assignment. Each action requires its
exact fields; mismatched fields and nonexistent/ambiguous active cases fail
loudly. `adjust` requires both `priority` and `deadline_at_utc` (which may be
null). Review actions are `confirm` or `dismiss`. Available example resources
are `grounds-crew` and `bunker-demo-robot`. Attribution strings do not establish
authenticated identities. A real manager screen/API and authenticated review
remain future work. The domain workflow supports explicit verification; this
first CLI action file does not yet expose that operation.

At each tick, due synthetic task completions release resources first; samples
are recorded; manual actions precede scripted verification and assignment;
new assignments start last. A photo at the exact completion timestamp cannot
verify that task. Future photos may do so. Demonstration assignment orders
confirmed cases by manual priority, deadline and stable ID, with capacity one
per resource. This is a scripted test actor, not an autonomous scheduling policy.
