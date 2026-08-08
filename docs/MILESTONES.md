# Product milestones

Status snapshot: **2026-08-09**, based on exact `main` commit
`192292735221e503915f286627dc64f001942881` and the linked GitHub pull
requests. An open branch is evidence of work in progress, not merged product
capability.

## Merged on `main`

| Milestone | What it proves | Evidence |
|---|---|---|
| ROI engine v1 | Reproducible facility economics with formula traces and explicit evidence inputs | PRs [#1](https://github.com/matthewong1210/jarvis-ai-agent/pull/1) and [#2](https://github.com/matthewong1210/jarvis-ai-agent/pull/2), `nxtektal-roi-engine/` |
| Virtual Handoff Lab | Backend-independent robot task sequencing, timeouts, retries, safe recovery, and latched e-stop behavior | PR [#5](https://github.com/matthewong1210/jarvis-ai-agent/pull/5), `nxt_sim` |
| Range Operations environment | Reproducible whole-site simulation, conserved ball inventory, guarded directive vocabulary, scenarios, and baseline policies | PR [#6](https://github.com/matthewong1210/jarvis-ai-agent/pull/6), `nxt_range_ops` |
| Benchmark and replay | Seeded policy comparison plus a read-only operating-day demo whose artifacts can be reproduced | PRs [#7](https://github.com/matthewong1210/jarvis-ai-agent/pull/7) and [#8](https://github.com/matthewong1210/jarvis-ai-agent/pull/8) |
| Site OS state | One frozen downstream state contract over the facility, rather than robot-by-robot state silos | PR [#9](https://github.com/matthewong1210/jarvis-ai-agent/pull/9), `FacilityState` |
| Operational intelligence | Deterministic facility advice, manager briefing, and append-only historical evidence | PRs [#10](https://github.com/matthewong1210/jarvis-ai-agent/pull/10) and [#11](https://github.com/matthewong1210/jarvis-ai-agent/pull/11) |
| Observation boundary | Provenance-bearing observations can assemble the same downstream state contract with separate quality evidence | PR [#12](https://github.com/matthewong1210/jarvis-ai-agent/pull/12), `nxt_telemetry` |
| Digital Twin Phase 0 | The same FacilityState stream can produce a deterministic, site-identified USD projection without becoming a second truth store | PR [#14](https://github.com/matthewong1210/jarvis-ai-agent/pull/14), `nxt_range_twin` |
| Shadow Ops v0.1 | Named-policy evaluation, decision trace, trust evidence, immutable human workflow, and a tamper-evident ledger remain advisory and downstream | PR [#19](https://github.com/matthewong1210/jarvis-ai-agent/pull/19), `nxt_pilot_ops` |
| Facility commissioning | An immutable, provenance-bearing `CommissionedSite` owns static physical-site facts and emits deterministic one-way projections | PR [#20](https://github.com/matthewong1210/jarvis-ai-agent/pull/20), `nxt_commissioning` |
| Site Runtime v0 | Sequenced input validation, existing telemetry assembly, publication-quality admission, exact state/report envelopes, checkpoint/recovery, and idempotent state-publication coordination | PR [#22](https://github.com/matthewong1210/jarvis-ai-agent/pull/22), `nxt_site_runtime` |
| AI Engineering Operating System | Repository-wide truth ownership, package boundaries, safety rules, testing workflows, and agent/human operating guidance are versioned with the codebase | PR [#23](https://github.com/matthewong1210/jarvis-ai-agent/pull/23), `AGENTS.md`, `.agent/`, `docs/AGENT_OPERATING_MANUAL.md` |

Together these milestones establish the current thesis: NXTektal can model a
facility as a coordinated operating system, preserve trusted static and dynamic
state boundaries, derive auditable operational advice, orchestrate quality-
gated state publication, and project the same state spatially while keeping
physical execution separate.

## Open presentation work, not merged capability

| Workstream | Status | Boundary |
|---|---|---|
| Four-panel Site OS demo | Open PR [#13](https://github.com/matthewong1210/jarvis-ai-agent/pull/13) | Presentation only; does not create a new runtime or state owner |

## Next product gates

These are not implemented on `main`:

1. **Real-site inputs:** physical telemetry adapters, transport, calibration,
   scheduling, and production clock semantics.
2. **Vendor and delivery integrations:** real robot/facility vendor adapters,
   concrete production state publishers/sinks, and a continuously running site
   service around the merged orchestration library.
3. **Execution admission:** a deterministic, safety-reviewed bridge from
   human-approved operational intent to physical site tasks. No AI or LLM may
   call robot interfaces directly.
4. **Autonomous physical execution:** automatic robot task execution may exist
   only downstream of approved command admission and hardware safety; Site
   Runtime does not provide either boundary.
5. **Live twin delivery:** production Omniverse/Nucleus publication and
   rendering without promoting USD to operational truth.
6. **Field validation:** measured supplier/site parameters, real operating data,
   and customer evidence replacing placeholder simulation assumptions.

## Claims that are not yet supported

- deployed autonomous operation at a customer facility;
- production telemetry, physical sensor connectivity, vendor integration, or a
  continuously running Site Runtime service;
- autonomous collector dispatch from native `FacilityState`, which lacks ETA,
  yield, capabilities, collection permission, current demand, and live washer
  availability;
- trained-policy superiority or causal claims from the demo baseline;
- validated robot, throughput, cost, or ROI performance from simulation; or
- production Isaac Sim, ROS 2, site-level robot command integration, automatic
  robot execution, live Omniverse/Nucleus delivery, or LLM participation in
  execution and safety loops.
