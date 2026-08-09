# Architecture invariants

## Site-level data flow

```mermaid
flowchart LR
    Scenario["Scenario + seed"] --> Runtime["RangeSimulation\nmutable simulation truth"]
    Scenario --> Viewer["nxt_range_viewer\nindependent RangeOpsEnv replay"]
    Viewer --> ViewerOutput["Public frames + events\nprojection"]
    SimPolicy["nxt_range_ops policy"] --> Directive["Directive + SafetyShield"]
    Directive --> Runtime
    Runtime --> State["FacilityState\ncanonical downstream state"]
    Observations["ObservationFrame + static/upstream inputs"] --> Assembler["Telemetry assembler + AssemblyReport"]
    Assembler --> State
    Assembler --> Quality["AssemblyReport\nquality evidence"]
    State --> Advice["nxt_facility recommendations\nadvisory"]
    State --> ShadowAdapter["nxt_pilot_ops adapter"]
    ShadowAdapter --> Snapshot["OperationalSnapshot"]
    Snapshot --> Shadow["Policy evaluation + recommendation\ntrace + trust evidence"]
    State --> Memory["Operational memory\nhistorical evidence"]
    State --> Stream["nxt-range-twin/facility-state-stream/v1"]
    Stream --> Twin["USD twin\nprojection only"]
    Advice --> Human["Human / agent interface"]
    Shadow --> Human
```

Arrows describe allowed data flow, not permission for every downstream package
to import its upstream. File contracts and composition-root scripts preserve
separation where direct imports are forbidden.

## Robot handoff execution seam

```mermaid
flowchart LR
    Controller["HandoffController\ntask + safety sequencing"] --> Interface["RobotTaskInterface"]
    Interface --> Mock["Mock adapter\nimplemented"]
    Interface -.-> Isaac["Isaac Sim adapter\nstub"]
    Interface -.-> ROS["ROS 2 adapter\nstub"]
```

The controller must not know which backend implements the interface. The
current repository validates the mock pipeline; it does not contain physical
robot execution. `RobotTaskInterface` is the micro handoff vocabulary
(navigation, docking, lift/dump, unload verification, retract/return, and
e-stop), not a whole-site collector-dispatch contract.

No LLM, generative agent, Site Runtime, facility recommendation, or Shadow Ops
component may connect directly to this seam. A future physical integration
requires a separately reviewed deterministic admission/controller boundary;
that boundary is not implemented.

## Physical deployment flow and status

The deployment roles are stable even though their implementations have
different maturity:

```mermaid
flowchart LR
    Physical["Surveyed / inspected physical facility"] -.-> Commissioning["CommissionedSite\nstatic physical truth; draft PR #20"]
    Commissioning -.-> Static["One-way static projections"]
    Static -.-> SiteConfig["SiteConfig compatibility input\nexplicit non-static context required"]
    Source["Synthetic producer today\nphysical adapters absent"] --> Frame["ObservationFrame"]
    Frame --> Assemble["assemble_from_observations"]
    SiteConfig --> Assemble
    Upstream["UpstreamInputs"] --> Assemble
    Previous["optional previous FacilityState"] --> Assemble
    Assemble --> Facility["FacilityState"]
    Assemble --> Report["AssemblyReport"]
    Future["Future Site Runtime\norchestration only; not implemented"] -.-> Assemble
```

Dashed edges are not current-checkout integrations. `project_site_config()` on
the commissioning branch is static-only and not the current `SiteConfig`
constructor shape; `project_legacy_site_config()` requires explicit
`LegacySiteConfigContext`. See [deployment.md](deployment.md) before changing
this boundary.

## Core principles

### One-way extension

Add a new layer as a downstream consumer whenever possible. Do not make stable
upstream runtime packages import presentation, memory, state, telemetry, twin,
or Shadow Ops packages. Keep any necessary privileged upstream read explicit,
small, pure, and guard-tested.

### Pure contracts, privileged adapters

Keep value objects and policy contracts importable without the heavy
simulation stack. Within downstream Site OS packages, concentrate unavoidable
coupling in named seams:

- `nxt_facility.build`
- `nxt_memory.harvest`
- `nxt_telemetry.bank` and `nxt_telemetry.assemble`
- `nxt_pilot_ops.adapters`
- `simulation/scripts/` for cross-package orchestration

Repository-local benchmark and viewer tools are separate consumers of public
`nxt_range_ops` APIs; their permitted coupling is recorded in
[package-map.md](package-map.md).

### Read-only means trajectory-neutral

A downstream observer must not consume simulator RNG, mutate resources, shift
event ordering, or change observation/reward sequences. Existing tests compare
instrumented and uninstrumented episodes byte-for-byte. Preserve that pattern.

### Determinism is a contract

Same inputs and seed must produce stable decisions, traces, identifiers,
records, reports, and artifacts where the package claims determinism. Prefer
canonical serialization, stable ordering, content-derived IDs, explicit time,
and fail-loud schema checks.

### Safety cannot be advisory-only at execution

Advice may pre-filter for feasibility, but execution safety remains inside the
execution layer. Never bypass `SafetyShield` in the macro simulator or adapter
safety contracts in the handoff layer.

For handoff execution, preserve the complete contract, not only the interface
type:

- motion/task calls obey hard timeouts and invalid ordering returns a classified
  failure rather than escaping task control;
- docking and unloading retries are bounded;
- post-docking failure attempts safe lower/undock retraction;
- emergency stop latches until an external reset and no further motion follows
  an e-stop; and
- `HandoffController` remains backend-independent while adapters remain free of
  controller/scenario logic.

## Guarded boundaries

| Boundary | Mechanical authority |
|---|---|
| `nxt_sim` controller/interface/adapter separation | `simulation/tests/test_architecture.py` |
| `nxt_range_ops` pure Phase 0 imports only | `simulation/tests/range_ops/test_eval_and_architecture.py` |
| Facility one-way imports and RNG/trajectory neutrality | `simulation/tests/facility/test_state.py`, `test_regressions.py` |
| Memory purity and no live feedback | `simulation/tests/memory/test_guards.py` |
| Telemetry purity, designated seams, and RNG discipline | `simulation/tests/telemetry/test_guards.py` |
| Twin import isolation, derivation checks, no feedback | `simulation/tests/twin/test_guards_package.py`, `test_guards_stream.py` |
| Shadow Ops adapter-only upstream access and no command surface | `simulation/tests/pilot_ops/test_boundaries.py` |
| Viewer/demo protected upstream trees | `simulation/tests/range_viewer/test_protection.py`, `simulation/tests/range_demo/test_protection.py` |
| Handoff timeout, state-machine, retry/recovery, unload, and e-stop behavior | `simulation/tests/test_state_machine.py`, `test_retry_recovery.py`, `test_unload_retry.py`, `test_emergency_stop.py` |

The `nxt_range_agent` no-direct-`nxt_sim` rule and some viewer/demo presentation
rules are documented but not exhaustively static-tested. Treat them as binding
and add a guard when changing that boundary.

Draft PR #20 adds `simulation/tests/commissioning/test_guards.py`. It guards the
commissioning package from several runtime/downstream imports and checks some
reverse imports, but does not exhaustively cover every telemetry/runtime
direction. Describe that boundary as partially guard-tested until the guard is
expanded and merged.

## Forbidden dependency outcomes

- Simulator or robot packages importing FacilityState, memory, telemetry, twin,
  demo, or Shadow Ops.
- Facility/Shadow Ops advisory code importing directives or execution APIs.
- A third facility decision engine, or duplicate recommendation semantics across
  `nxt_facility` and `nxt_pilot_ops` without a named owner and parity/divergence
  contract.
- Twin code importing live simulation packages or adding novel operational
  facts/physics.
- Physical static facts being reconstructed from `SiteConfig`, a simulation
  scenario, telemetry, viewer/layout output, or USD instead of commissioning.
- A future Site Runtime owning state, policy, projection, or robot execution
  rather than orchestrating existing public contracts.
- An LLM/generative agent calling `RangeSimulation.apply_directive()`,
  `RobotTaskInterface`, adapters, ROS, actuators, or e-stop APIs directly.
- Memory queries affecting current policy or runtime state.
- Core Shadow Ops code importing FacilityState; only adapters may translate it.
- UI/API/report code duplicating ROI formulas.
- Convenience imports that make optional presentation/USD dependencies required
  by pure contracts.

See [package-map.md](package-map.md) for responsibility-by-package details.
