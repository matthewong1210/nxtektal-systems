# Deployment architecture and maturity

This file separates binding architectural roles from implementation status.
Recheck the current branch and merged history before making a release claim.

## Merged-main baseline: 2026-08-09

The baseline for this operating layer is `main` at
`b055c9472737feb923c6ac48fad44a5b7e43333c`.

| Boundary | Status at baseline | What is actually present |
|---|---|---|
| Observation contract and assembler | Merged | `Observation`, `ObservationFrame`, `SiteConfig`, `UpstreamInputs`, `assemble_from_observations()`, and `AssemblyReport`; only a synthetic producer exists |
| Shadow Ops | Merged by PR #19 (`e84c5016a19d1d4aec0b4b183164c08bba5b164e`) | FacilityState adaptation, named-policy evaluation, trace, human workflow, and ledger; no live runner or command bridge |
| Commissioning | Merged by PR #20 (`89e93f6a8ea0cd469d6da907321eafe30318fa49`) | Immutable `CommissionedSite`, strict validation/storage, one-way static projections, and setup-only Site Runtime binding |
| Site Runtime | Merged by PR #22 (`b055c9472737feb923c6ac48fad44a5b7e43333c`) | Orchestration library, envelope schema, input/quality gates, source/publisher ports, checkpoints, recovery, and idempotent publication coordination |
| Physical telemetry adapters and transport | Not implemented | No hardware, POS, weather, fleet, Modbus, serial, MQTT, Kafka, OPC-UA, ROS 2, or vendor source/transport implementation; the edge adapter kit converts already-read synthetic samples and opens no connection |
| Physical state publisher or consumer delivery | Not implemented | `StatePublisher` and `RuntimeSink` are protocols/test seams; there is no live decision, memory, twin, or external-system delivery service |
| Physical robot execution | Not implemented | Mock adapter works; Isaac Sim and ROS 2 adapters raise unavailable errors; no site-level physical command admission exists |
| Live twin delivery and real-site deployment | Not implemented | No live Omniverse/Nucleus delivery, production site service, or real-site performance evidence |

Merged library contracts are not proof of deployed physical integrations. Keep
protocols, test doubles, and deterministic rehearsal separate from live-service
claims.

Added after that baseline (verify merge status against the current branch):
`nxt_agent_runtime`, the deterministic composition/lifecycle library over Site
Runtime and Shadow Ops — deferred-acknowledgement cycles, a separate evaluation
checkpoint, an append-only evaluation journal, a pending manager-decision view,
a local JSONL snapshot publisher, and read-only status. It runs against
synthetic/fixture sources only; it is not a service scheduler, a production
publisher, or live delivery.

Also added after that baseline (verify merge status against the current
branch): `nxt_edge_observation`, the Edge Observation Adapter Kit V0 — a
conversion leaf that turns already-read load-cell, digital-I/O, and
robot-status samples into canonical `Observation` objects using commissioned
bindings, plus separate adapter diagnostics and the in-process source-side
at-least-once delivery cursor (peek / acknowledge / reject with sequence
reuse; sequence validation stays with Site Runtime). It is transport-neutral and
fixture-backed: it opens no connection, drives no device, writes no register,
and has no robot, actuator, or emergency-stop surface. It is not a physical
telemetry adapter and does not change the "Not implemented" row above.

Also added after that baseline (verify merge status against the current
branch): `nxt_workflow_enablement`, Pilot Site Workflow Enablement V0 — a
deterministic readiness layer that registers the three pilot workflow
identities, evaluates one shared validated `CommissionedSite` plus declared
plain-data evidence per workflow, and emits a content-addressed enablement
report with fixture-only launch-plan data for the READY Range Operations
workflow. Grounds Condition Intelligence and Player Caddy Experience are
registered but unimplemented and always NOT_READY in V0; registration never
implies a course model, camera, inspection, or player capability exists. It
is evaluation-only: no transport, no device, no runtime construction, and no
change to the "Not implemented" row above.

Also added after that baseline (verify merge status against the current
branch): `nxt_site_agent` plus `apps/site-agent-console`, the Pilot Site
Agent Service V0 — a local, loopback-only, fixture-backed application
boundary that verifies a READY enablement report, drives the existing
Agent Runtime one bounded cycle at a time, persists the fixture source
resume cursor, projects existing evidence through a versioned local
Manager API, and serves a static Manager Console. Manager acceptance
remains workflow evidence only. It is not a production or cloud
service: no authentication, no public exposure, no physical sensor,
transport, or device connection, no robot/actuator command path, and
no change to the "Not implemented" rows above.

## Static truth versus dynamic evidence

For a physical facility, commissioning owns **what exists and how it is
configured**. A validated, immutable `CommissionedSite` manifest is
authoritative for:

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

Commissioning projections are deterministic, disposable, and one-way:

```text
CommissionedSite
    -> static site-configuration projection
    -> commissioned digital-twin layout projection
    -> telemetry-adapter binding/calibration projection
```

`project_site_config()` is a static JSON-ready projection, not the current
`SiteConfig` constructor shape. `project_legacy_site_config()` requires an
explicit `LegacySiteConfigContext` for non-commissioned simulation/service
inputs. `bind_commissioned_site()` uses that existing compatibility projection
once at setup to produce `RuntimeSiteBinding(site_id, deployment_id,
SiteConfig)`. It does not make commissioning a runtime loop or authorize
invented context.

## Observation assembly and Site Runtime flow

Telemetry continues to own the reusable assembly primitive:

```text
ObservationFrame + SiteConfig + UpstreamInputs
    + optional previous FacilityState
    -> assemble_from_observations()
    -> FacilityState + AssemblyReport
```

Site Runtime v0 wraps that existing contract without redefining it:

```text
CommissionedSite + explicit LegacySiteConfigContext
    -> bind_commissioned_site() -> RuntimeSiteBinding

abstract ObservationSource
    -> SequencedObservationFrame
       (ObservationFrame + UpstreamInputs + source references + sequence)
    -> SiteRuntimePipeline input/freshness validation
    -> existing assemble_from_observations(frame, site_config, upstream)
    -> exact FacilityState + AssemblyReport
    -> mechanical QualityGate
    -> nxt-site-runtime/facility-snapshot/v1 envelope
    -> checkpoint / recovery / idempotent StatePublisher
    -> source acknowledgement
```

Runtime v0 deliberately does not pass a previous `FacilityState` to the
assembler. It rejects missing or stale required input before publication rather
than presenting prior/default backfill as current physical truth. Its quality
gate admits **state publication** based on input and assembly quality; it is not
operational policy, site-level physical command admission, or a robot safety
gate. `StatePublisher` publishes state envelopes, not commands. `RuntimeSink`
is best-effort visibility, not control.

`ObservationFrame` remains input evidence, not facility truth. Preserve source,
sample/availability times, status, calibration, confidence, sequence,
missingness, consistency, and provenance. `FacilityState` remains the exact
canonical downstream state object inside the envelope; `AssemblyReport` and
runtime-quality metadata remain separate evidence. In simulation,
`RangeSimulation` and `BallLedger` remain mutable runtime truth.

## Site Runtime ownership limits

`nxt_site_runtime` owns orchestration metadata and behavior: site-level input
ordering, validation, mechanical publication-quality admission, deterministic
envelope identity, checkpoint/recovery, idempotent publication coordination,
and source acknowledgement/rejection. It must not:

- create a second assembler, mutable facility model, or replacement for
  `FacilityState`;
- own commissioning facts, telemetry semantics, recommendation semantics, USD
  state, memory, or robot behavior;
- reach into `RangeSimulation` or use viewer/USD/memory/recommendations as input
  truth;
- duplicate `nxt_facility` or `nxt_pilot_ops` decision logic, aggregate their
  recommendations silently, or become a third decision engine;
- turn `AssemblyReport` uncertainty into silent defaults; or
- call directives, `RobotTaskInterface`, adapters, ROS, actuators, or e-stop
  APIs, directly or through an LLM/tool call.

The merged package defines source, state-publisher, and visibility-sink
protocols; deterministic sequence/replay/idempotency behavior; an envelope; and
in-memory/JSON checkpoints. It does **not** implement concrete physical sources,
hardware/vendor transports, a production publisher/fan-out, an external
long-running service scheduler, live site operations, physical command
authorization/admission, actuator execution, or hardware acknowledgement.
Those boundaries require separate recon, design, and architecture approval.

The contract surface imports without the simulator or USD stack. Successful
processing currently reaches the existing telemetry assembler, whose entity
compatibility requires the `range-ops` extra. That packaging detail does not
give Site Runtime ownership of simulation truth.

## Placement rules for deployment work

| Change | Owner/status |
|---|---|
| Surveyed/static physical site fact or calibration | `nxt_commissioning` |
| Observation value, source metadata, or assembly quality | `nxt_telemetry` |
| Raw device payload conversion into a canonical observation, its diagnostics, and the source-side at-least-once delivery cursor | `nxt_edge_observation` (no transport, sequence validation, state, or command) |
| Cross-workflow commissioning readiness: workflow identity registration, requirement definitions, independent readiness verdicts, enablement report, launch-plan data | `nxt_workflow_enablement` (evaluation only; no runtime construction, state, policy, or execution) |
| Local service lifecycle, Manager API projection transport, fixture source-cursor persistence, service diagnostics | `nxt_site_agent` (noncanonical application shell; loopback-only; no state, policy, workflow, or execution semantics) |
| Canonical point-in-time operational state | `nxt_facility.state.FacilityState` |
| Input sequencing, quality gate, state envelope, checkpoint/recovery, or state publication coordination | `nxt_site_runtime` |
| Continuous evaluation lifecycle, evaluation checkpoint/journal, pending-decision view, runtime status | `nxt_agent_runtime` |
| Broad state-derived manager advice | `nxt_facility.decisions` |
| Policy trust, trace, evaluation, human workflow, or ledger | `nxt_pilot_ops` |
| Facility visualization | `nxt_range_twin` projection from declared layout/state contracts |
| Micro handoff task execution | `HandoffController` / `RobotTaskInterface` / selected adapter |
| Concrete physical telemetry source/transport/publisher | Not implemented; requires approved integration design |
| Physical site-level collector dispatch/command admission | Not implemented; no existing API or owner |

Before changing any row, run the
[pre-implementation architecture review](../workflows/architecture-review.md).
