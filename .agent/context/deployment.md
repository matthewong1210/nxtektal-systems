# Deployment architecture and maturity

This file separates binding architectural roles from implementation status.
Recheck the branch and open PRs before claiming that a deployment component is
available.

## Audited status: 2026-08-09

| Boundary | Status at audit | What is actually present |
|---|---|---|
| Observation contract and assembler | Merged on `main` | `Observation`, `ObservationFrame`, `SiteConfig`, `UpstreamInputs`, `assemble_from_observations()`, and `AssemblyReport`; only a synthetic producer exists |
| Shadow Ops | Draft PR #19 / current audited checkout | Offline FacilityState adaptation, policy evaluation, trace, workflow, and ledger; no live runner or command bridge |
| Commissioning | Draft PR #20 at `feature/commissioning-v0` commit `260df33`; absent from `main` and the audited Shadow checkout | Immutable `CommissionedSite`, strict validation/storage, and one-way static projections; no runtime integration |
| Physical telemetry adapters and transport | Not implemented | No hardware, POS, weather, fleet, MQTT, Kafka, OPC-UA, or other live adapter/runtime loop |
| Site Runtime | Future architectural boundary only | No tracked package, service, schema, scheduler, storage envelope, or approved package name |
| Physical robot execution | Not implemented | Mock adapter works; Isaac Sim and ROS 2 adapters raise unavailable errors |

An unmerged branch is inspectable evidence, not merged/current-checkout code. A
future boundary is a placement constraint, not authorization to scaffold it.

## Static truth versus dynamic evidence

For a physical facility, commissioning owns **what exists and how it is
configured**. Where the commissioning branch/package is present, a validated,
immutable `CommissionedSite` manifest is authoritative for:

- site and deployment identity, timezone, and location metadata;
- surveyed coordinate/spatial references and zone definitions;
- equipment and robot assets, declared capabilities, capacities, operating
  constraints, and safety-relevant limits;
- sensor/channel bindings and calibration evidence; and
- provenance for every important declaration.

Commissioning does not own live battery, pose, payload, inventory, task, demand,
observation, availability, or transport state. A changed physical declaration
receives a new deployment identity; do not rewrite an existing manifest or copy
facts back from a scenario, `SiteConfig`, telemetry, viewer, or USD artifact.

The branch-reviewed commissioning projections are deterministic and one-way:

```text
CommissionedSite
    -> static site-configuration projection
    -> commissioned digital-twin layout projection
    -> telemetry-adapter binding/calibration projection
```

The static site-configuration projection is not constructor-ready for the
current `SiteConfig`, whose shape still mixes static facts with simulation and
service inputs. `project_legacy_site_config()` requires an explicit
`LegacySiteConfigContext` for those non-commissioned values. Do not claim direct
production integration or fabricate the missing context.

## Current observation-to-state flow

The merged deployment-path rehearsal is:

```text
observation producer (SyntheticSensorBank today)
    -> ObservationFrame
       + SiteConfig
       + UpstreamInputs
       + optional previous FacilityState
    -> assemble_from_observations()
    -> FacilityState + AssemblyReport
```

`ObservationFrame` is input evidence, not facility truth. Every observation
carries source type/identity, sample time, availability time, status,
calibration identity, confidence, and sequence. Current timestamps are
simulation seconds. Missing/stale/backfilled inputs remain visible in
`AssemblyReport`; a consumer must not discard that report and present the state
as clean measured truth.

`FacilityState` remains the canonical downstream state contract on both the
simulation-builder and observation-assembler paths. In simulation,
`RangeSimulation` remains live mutable truth. No production physical-runtime
truth owner or live loop exists yet.

## Future Site Runtime boundary

“Site Runtime” names a future **orchestration boundary**, not a new domain
authority or decision engine. Its architectural role is to coordinate the
existing contracts around a deployed site:

1. select one commissioned deployment/static projection;
2. receive `ObservationFrame` and explicit upstream service inputs;
3. invoke the existing observation assembler with any explicit previous state;
4. preserve `FacilityState`, `AssemblyReport`, source identity, and provenance
   together through downstream handoff; and
5. fan canonical state/evidence out to the existing advisory, Shadow Ops,
   memory/capture, and projection surfaces through public contracts.

The future boundary must not:

- create a second mutable facility model or a replacement for `FacilityState`;
- own commissioning facts, telemetry readings, recommendation semantics, USD
  state, or robot behavior;
- reach into `RangeSimulation` internals or use viewer/USD/memory as input truth;
- duplicate `nxt_facility` or `nxt_pilot_ops` decision logic;
- turn `AssemblyReport` uncertainty into silent defaults; or
- call directives, `RobotTaskInterface`, adapters, ROS, actuators, or e-stop
  APIs, whether directly or through an LLM/tool call.

Scheduling, transport, persistence/envelope shape, clock/UTC semantics,
availability/failover, adapter protocols, and any separately authorized command
gateway remain unresolved. No physical site-level collector task admission or
translation contract exists; `RobotTaskInterface` covers only the micro handoff
cycle. Identity/authorization, idempotency/anti-replay, and physical
acknowledgement semantics for any command boundary are also undefined. These
require recon, design, and explicit architecture approval before implementation.
Do not assume the package name `nxt_site_runtime` or copy the proposal tree from
an untracked audit.

## Placement rules for deployment work

| Change | Existing/future owner |
|---|---|
| Surveyed/static physical site fact or calibration | Commissioning manifest/package, when present |
| Observation value, source metadata, or assembly quality | `nxt_telemetry` |
| Canonical point-in-time operational state | `nxt_facility.state.FacilityState` |
| Broad state-derived manager advice | `nxt_facility.decisions` |
| Policy trust, trace, evaluation, human workflow, or ledger | `nxt_pilot_ops` |
| Live cross-contract scheduling/fan-out | Future Site Runtime design; not an existing package |
| Facility visualization | `nxt_range_twin` projection from declared layout/state contracts |
| Micro handoff task execution | `HandoffController` / `RobotTaskInterface` / selected adapter |
| Physical site-level collector dispatch/admission | Undefined future boundary; no existing API or owner |

Before changing any row, run the
[pre-implementation architecture review](../workflows/architecture-review.md).
