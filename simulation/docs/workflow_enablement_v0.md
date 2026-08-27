# Pilot Site Workflow Enablement V0

`nxt_workflow_enablement` is the deterministic readiness layer between one
shared commissioned site and the multiple workflows that will operate on it.
It answers exactly one question per registered workflow: *may this workflow's
existing fixture-backed composition be assembled for this site right now* —
and it answers it independently per workflow, with a content-addressed,
byte-stable report as evidence.

Everything in this document describes synthetic, fixture-backed behavior.
SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

## What this is not

- **Not commissioning truth.** The validated, immutable `CommissionedSite`
  manifest remains the only owner of static site/deployment facts. This layer
  consumes the canonical constructor, validation, canonical serialization,
  and one-way projections; it re-implements no channel, unit, sensor-type,
  duplicate, or provenance rule, and it never writes a physical fact back.
- **Not the Site Runtime.** Ordered input validation, telemetry assembly
  invocation, the publication-quality gate, the snapshot envelope, and
  checkpoint/recovery keep their existing owner. Readiness evaluation happens
  *before* any of that exists and produces none of it.
- **Not the Agent Runtime.** The evaluation lifecycle over published state —
  deferred acknowledgement, evaluation checkpoint, journal, pending manager
  decisions — keeps its existing owner. This layer only decides whether that
  composition may be built at all, and hands a composition root pure
  launch-plan data.
- **Not a decision engine.** Readiness verdicts gate assembly; they are not
  operational advice, they consume no `FacilityState`, and they can never
  rank, merge, or replace facility or Shadow Ops recommendations.

## Ownership map

| Fact | Owner |
|---|---|
| Workflow identity registration (the three pilot workflow IDs) | `nxt_workflow_enablement.identity` |
| Versioned per-workflow requirement definitions | `nxt_workflow_enablement.requirements` |
| Shared-site gate evaluation and manifest digest | `nxt_workflow_enablement.evaluation` over commissioning's own validation and canonical bytes |
| Independent per-workflow readiness verdicts | `nxt_workflow_enablement.evaluation` |
| Deterministic enablement report and its content-derived `report_id` | `nxt_workflow_enablement.report` |
| Fixture-only launch-plan data for a READY workflow | `nxt_workflow_enablement.launch` |
| Static site/deployment truth, channels, units, calibration identity | `nxt_commissioning` (unchanged) |
| Adapter conversion, coverage, and diagnostics | the edge adapter kit (unchanged); its outcome reaches this layer as declared plain-data evidence |
| Runtime construction from a READY plan | composition roots under `simulation/scripts/` (`pilot_course_a_enablement_fixture.py`) |

## Package placement

The layer is a package, not a script, because it owns persistent versioned
contracts: workflow IDs safe for checkpoint/report identity, requirement-set
versions, the report schema (`nxt-workflow-enablement/report/v0`), and the
launch-plan shape. It is a *leaf*: its only first-party import is
`nxt_commissioning`'s public surface (the same consumption tier as the Site
Runtime's setup-only seam). The Site Agent service shell
(`nxt_site_agent`) is its only designated in-package consumer, using the
public surface to verify enablement reports and launch plans at service
launch; no other package may import it. Adapter
and runtime facts arrive as declared, typed plain data
(`AdapterCompositionEvidence`, `RangeOpsEvidence`), gathered by composition
roots from the canonical owners' public APIs — the same pattern the edge
adapter kit uses for the commissioning projection. Turning a READY plan into
the existing `ObservationSource` → pipeline → runtime composition stays in
`simulation/scripts/`, because only the designated runtime composition layer
may import those packages.

## Workflow identity

V0 registers exactly three workflow identities:

| Workflow ID | Requirements version | V0 status |
|---|---|---|
| `range.closed_loop_collection_handoff` | `range.closed_loop_collection_handoff/requirements/v1` | Fully evaluated; READY with the deterministic pilot fixture |
| `course.grounds_condition_intelligence` | `course.grounds_condition_intelligence/requirements/v1` | Registered prerequisite scaffold; always NOT_READY |
| `course.player_caddy_experience` | `course.player_caddy_experience/requirements/v1` | Registered prerequisite scaffold; always NOT_READY |

Workflow IDs are validated against a closed shape, registered exactly once,
independent of display labels, and pinned by literal-string tests so an
accidental rename breaks the suite. **Registration is not implementation**:
an unknown workflow ID in a registry fails evaluation visibly, and a
registered-but-unimplemented workflow can never acquire a runtime.

## Shared-site gates versus workflow-specific gates

Shared-site gates run once for the one commissioned site every workflow
shares:

1. `manifest_valid` — the canonical commissioning constructor accepted the
   manifest (assets, sensors, channels, units, calibration, duplicates, and
   provenance are all commissioning-owned rules; a wrong unit or duplicate
   identity fails here, before any workflow is evaluated);
2. `site_identity_match` / `deployment_identity_match` — the manifest carries
   the expected `(site_id, deployment_id)`;
3. `manifest_digest_stable` — `sha256` over commissioning's canonical
   manifest bytes (`dumps_manifest`), recorded as `sha256:<hex>`;
4. `output_locations_collision_safe` — evidence paths are relative and
   collision-free and the declared evidence root is empty (evidence streams
   are append-only);
5. `physical_execution_unreachable` — a context claiming physical execution
   reachability fails closed; no such path exists in V0;
6. `transport_fixture_only` — the declared transport is `FIXTURE_ONLY`; no
   live device connectivity is implied by any verdict.

A shared-site failure makes **every** workflow NOT_READY with the explicit
failure `shared_site_invalid`. A workflow-specific failure affects only its
own workflow: evaluators receive the shared result plus their own declared
evidence and nothing else, so readiness can neither leak nor block across
workflows, and one workflow's satisfied requirement can never satisfy
another's.

## Range Operations readiness (requirements v1)

| Requirement | Evaluated against |
|---|---|
| `commissioned_range_equipment` | ≥1 dispenser, exactly one washer and charging station (the legacy `SiteConfig` projection precondition), ≥1 collection station, robot, and zone |
| `adapter_conversion_profiles` | the real edge adapter kit composed over `project_telemetry_adapter_config` (declared outcome; a composition error fails closed with the owner's message), and every claimed adapter channel is backed by a commissioned binding — a fabricated channel claim cannot fill coverage |
| `calibration_profile_match` | every commissioned calibrated sensor has a declared adapter-profile calibration identity that matches the commissioned one — an empty or partial declaration fails, never passes vacuously |
| `complete_channel_coverage` | the requirement channel templates × the site's zone/station/robot IDs are fully covered by adapter channels plus declared non-adapter fixture inputs |
| `no_unsupported_channel_claims` | declared fixture channels neither duplicate adapter coverage, nor impersonate a commissioned binding, nor claim channels outside the requirement set |
| `runtime_configuration` | declared runtime mode is `SHADOW`, the simulation epoch is a timezone-aware calendar midnight, and the run is bounded |

The channel templates deliberately re-declare which canonical channels the
workflow needs — that declaration is this layer's own fact — but they are
pinned to the real pipeline by a two-directional parity test
(`tests/workflow_enablement/test_channel_parity.py`): the pilot fixture frame
must carry exactly the declared set, the real assembler must accept it
cleanly, and dropping any single declared channel must be reported missing.
Drift in either direction fails the suite.

When every requirement is satisfied the verdict is
`READY_FOR_FIXTURE_SHADOW_MODE` and `plan_range_ops_launch` issues a
`RangeOpsLaunchPlan` — pure data (identity, transport, mode, bound, evidence
paths). When any requirement fails, the verdict is `NOT_READY` and the
planner raises, so the honest composition path produces no Site Runtime,
Agent Runtime, `FacilityState`, `PolicyEvaluation`, `NO_ACTION`,
`Recommendation`, `DecisionTrace`, or pending manager decision for the
workflow — proven on disk by the demo, which keys runtime evidence
directories by workflow ID.

The plan is plain public data, **not an unforgeable capability**: the
factory in the composition root revalidates every structural claim it can
(plan type, workflow identity, site/deployment identity, FIXTURE_ONLY
transport, SHADOW posture) but cannot prove a plan's provenance. The
composition root is the trusted boundary and must obtain plans from
`plan_range_ops_launch`; a caller that hand-builds a plan is bypassing
readiness evaluation and owns that violation.

## Why Range Operations is not blocked by future Course workflows

Readiness is evaluated per workflow over per-workflow requirements. Grounds
Condition Intelligence and Player Caddy Experience contribute nothing to the
Range Operations requirement set and cannot subtract from it; their
prerequisite scaffolds are evaluated afterwards, from the same shared-site
result, and their permanent V0 NOT_READY verdicts carry no veto. The
isolation is tested both ways: a broken Grounds prerequisite list cannot make
Range Operations NOT_READY, and a broken Range Operations evidence set leaves
the Grounds and Player Caddy results byte-identical.

## Grounds Condition Intelligence scaffold

Registered, never implemented in V0. Its requirement matrix distinguishes
three honest classes:

- **missing** — no canonical owner exists anywhere in the repository yet:
  `course_model_version`, `course_coordinate_reference`, `map_version`,
  `cart_node_identity`;
- **unsupported_in_v0** — a commissioning vocabulary exists but cannot
  express the fact today: `cart_pose_binding` and `camera_device_binding`
  (the closed canonical channel vocabulary has no pose or camera channel),
  `camera_intrinsic_calibration_reference` /
  `camera_extrinsic_calibration_reference` (calibration records carry
  identity and validity only), `camera_to_cart_transform`,
  `timestamp_sync_profile`, `inspection_zone_definition`;
- **deferred** — deliberately out of scope until their own contracts are
  designed: `inspection_coverage_contract`, `condition_observation_contract`,
  `condition_issue_registry_contract`, `maintenance_briefing_policy`,
  `human_review_workflow`, `repair_verification_semantics`.

No Course World Model, camera, coverage record, condition observation, issue
registry, or briefing object is created anywhere — the scaffold is a list of
absences, not placeholders.

## Player Caddy Experience scaffold

Registered, never implemented in V0, and never able to block the other
workflows. Missing: `course_world_model_map_query`,
`player_consent_privacy_policy`, `pseudonymous_player_identity`,
`ball_found_event`, `deterministic_landing_model_owner`,
`player_recommendation_owner`, `session_retention_deletion_policy`.
Unsupported in V0: `cart_pose`,
`launch_monitor_adapter_or_manual_fallback`. Deferred:
`caddy_session_contract`, `session_event_contract`. No session state, player
identity, or player-facing recommendation object exists.

## Deterministic report

`EnablementReport` (`nxt-workflow-enablement/report/v0`) serializes through
commissioning's canonical JSON (sorted keys, compact separators,
`allow_nan=False`) plus a trailing newline. It carries the disclaimer, the
site/deployment identity, the manifest digest, the declared scenario name and
scenario time (never a wall clock), the transport mode,
`physical_execution_reachable` (the declared value — any claim of
reachability fails the shared gate, so no READY verdict can accompany a
`true` here), every shared gate result,
the registered workflow IDs, and one section per workflow with its verdict,
requirement matrix split into satisfied/missing/unsupported_in_v0/deferred,
failures, and runtime-assembly eligibility. The summary lists ready and
not-ready workflow IDs without erasing the individual results. `report_id`
is `wer_` plus a SHA-256 digest over the canonical payload without the ID
field — deterministic **content addressing, not an authenticity proof**.
`verify_report_payload` fails closed on a foreign or missing schema, on a
workflow-section set that does not match the registered IDs, and on any
altered byte, but it proves only payload/ID consistency; it says nothing
about who produced the report, and a consumer that needs provenance must
obtain the report from a trusted composition root. Identical inputs produce
byte-identical reports across processes and `PYTHONHASHSEED` values.

The report is commissioning and workflow-readiness **evidence**. It is not
`FacilityState`, telemetry truth, a Course World Model, player session
state, Condition Issue truth, policy output, or execution truth, and it is
never an input to a live loop.

## Physical execution boundary

Transport is `FIXTURE_ONLY` and runtime posture is `SHADOW`, both enforced as
gates and re-checked by the launch planner and the composition factory. The
package bans (mechanically, in
`tests/workflow_enablement/test_architecture.py`) every runtime, simulator,
transport, field-bus, network, filesystem, process, wall-clock, and
randomness import, every execution token, and every canonical-contract
redefinition. A READY verdict authorizes exactly one thing: a bounded,
fixture-backed, advisory Shadow Mode composition assembled by a composition
root. It implies no live device connectivity, no deployed gateway, no
customer data, no robot execution, and no safety certification.

## Deterministic Pilot fixture and demo

`scripts/pilot_course_a_enablement_fixture.py` reuses the existing synthetic
Pilot Course A manifest under the enablement deployment identity
(`pilot-course-a` / `pilot-a-enablement-v0`) and composes evidence from the
real canonical owners. `scripts/pilot_site_workflow_enablement_demo.py` runs
the bounded storyline:

1. a candidate manifest missing the dispenser-count binding —
   commissioning-valid, but Range Operations reports NOT_READY with the
   exact uncovered channel while Grounds and Player Caddy results are
   independent scaffold verdicts, and the launch planner refuses;
2. the corrected manifest — Range Operations becomes
   `READY_FOR_FIXTURE_SHADOW_MODE`; the other two workflows stay NOT_READY
   with their exact prerequisite matrices;
3. the READY plan assembles the existing fixture-backed Site Runtime and
   Agent Runtime for Range Operations only and runs the bounded evening
   storyline (calm `NO_ACTION`, spike `RECOMMEND operator_intervention`,
   source exhaustion), leaving one pending manager decision;
4. a restart recomposition resumes explicitly and reproduces identical
   identities, proving stable IDs across composition lifetimes.

```bash
uv run --no-sync python -B scripts/pilot_site_workflow_enablement_demo.py \
  --out reports/workflow-enablement
```

The demo refuses a non-empty evidence directory, writes only under
`--out/<site_id>/<deployment_id>`, keys runtime evidence by workflow ID (so
the absence of Grounds/Player Caddy evidence is directly visible), and
produces byte-identical stdout and artifacts across repeat runs and across
`PYTHONHASHSEED` values.

## Current limitations and next seams

- Grounds Condition Intelligence needs, in order: a versioned Course World
  Model owner (model identity, map version, coordinate alignment), cart-node
  commissioning (asset + pose/camera channel vocabulary extensions — a
  commissioning schema change with its own review), calibration-reference
  contracts for intrinsics/extrinsics, a time-sync profile contract, and only
  then the Inspection Coverage / Condition Observation / Issue Registry /
  Maintenance Briefing contracts. Each addition slots into the existing
  requirement matrix by flipping a prerequisite from missing/unsupported to
  satisfied — the Range Operations contracts do not change.
- Player Caddy Experience additionally needs consent/privacy and retention
  policy contracts plus named owners for the landing model and player-facing
  recommendations (a new advisory-ownership decision, not an extension of the
  existing manager-advisory engines).
- The readiness evaluation consumes declared evidence gathered by
  composition roots from canonical owners; it does not re-run adapter
  conversion itself. The package cross-checks every claim it can against
  the validated commissioned site: an adapter-channel claim the site does
  not bind fails closed, every commissioned calibrated sensor must have a
  declared profile calibration identity that matches the commissioned one
  (an empty or partial declaration fails, never passes vacuously), and a
  declared fixture channel may neither duplicate adapter coverage,
  impersonate a commissioned binding, nor exceed the requirement set. What
  remains declared trust — the `composed` flag, adapter-local raw-only
  profile identities, and `root_is_empty` — must be made true by the
  composition root (the demo proves `root_is_empty` against the real
  filesystem via `runtime_evidence_root_is_empty`). No untrusted serialized
  input can reach a verdict: evaluation inputs are typed constructor-
  validated objects, not parsed documents.
- V0 registers exactly the three pilot workflows; adding a fourth workflow
  requires a new evaluator, requirement set, and architecture review — the
  registry deliberately rejects unknown IDs instead of accepting arbitrary
  names.

## Verification

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/workflow_enablement
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/commissioning
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider  # full suite
```

plus the architecture/import/safety subset and the packaging/distribution
checks listed in `docs/CI.md`.
