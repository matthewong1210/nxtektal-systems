# Pre-implementation architecture review

Run this gate before code when a change adds or materially alters a package,
service/runtime, source-of-truth contract, decision/policy engine, cross-package
dependency, robot/control/safety path, physical adapter, or AI-to-execution
integration. For ordinary local changes, record the same answers briefly in the
plan rather than creating ceremony.

Do not begin implementation until every triggered section has a supported
answer. “Future” or “proposed” is not an implementation status.

## 1. Establish implementation status

1. Record the current branch, base, dirty state, and relevant open/stacked
   branches or PRs.
2. Read the existing package source, public exports, manifests, stable docs,
   behavioral tests, architecture guards, and recent history.
3. Label every cited component as one of:
   - merged/current checkout;
   - implemented on a named unmerged branch;
   - approved design only; or
   - proposed/untracked/future.
4. Do not route code through a component that is absent from the target branch.

## 2. Prove feature placement

Classify the feature before choosing a directory:

| Concern | Existing owner/boundary |
|---|---|
| Mutable simulation episode behavior | `nxt_range_ops` |
| Conserved simulated ball location/count | `BallLedger` |
| Canonical downstream snapshot | `nxt_facility.state` |
| Broad deterministic manager advice over FacilityState | `nxt_facility.decisions` |
| Observation contract/assembly/quality | `nxt_telemetry` |
| Raw device payload conversion into a canonical observation | `nxt_edge_observation` (conversion plus the source-side delivery cursor; no transport, sequence validation, state, or command) |
| Physical static onboarding facts | `nxt_commissioning` |
| Policy-specific trust, trace, evaluation, workflow, ledger | `nxt_pilot_ops` |
| Historical evidence | `nxt_memory` |
| Viewer replay/export | `nxt_range_viewer` |
| Browser replay storytelling | `apps/operational-replay` presentation over artifact files |
| State/layout-to-USD projection | `nxt_range_twin` |
| Micro handoff task sequencing/execution | `HandoffController` and `RobotTaskInterface` |
| Physical site-level task admission/translation | No implemented owner or contract; pause for design/approval |
| Cross-package physical-site state orchestration | `nxt_site_runtime`: ordering/input validation, existing telemetry assembly invocation, publication-quality gate, exact state/report envelope, checkpoint/recovery, idempotent state publication |
| Continuous evaluation lifecycle over Site Runtime and Shadow Ops | `nxt_agent_runtime` composition/lifecycle only |
| ROI semantics | Versioned `@nxtektal/roi-engine` |

If no row fits, stop and write the missing responsibility explicitly. Do not
choose a new package name as a substitute for deciding who owns the fact.

## 3. Run the duplication search

Before scaffolding a package, engine, policy, schema, or store:

1. Search package trees and manifests with `rg --files` and inspect
   `simulation/pyproject.toml` or the relevant package manifest.
2. Search domain nouns, proposed symbols, output fields, rule IDs, serializers,
   adapters, and tests with `rg`.
3. Inspect both `nxt_facility.decisions` and `nxt_pilot_ops` for any advisory
   change, even if the proposed vocabulary differs.
4. Inspect recent merged and open branches/PRs for concurrent ownership.
5. Prefer extending the existing owner or composing public outputs at a
   composition root.

A new package is admissible only when it has a distinct fact class or lifecycle,
a stable public contract, an allowed place in the dependency graph, and a reason
no existing owner can contain it. Record the rejected existing owners. Require
explicit architecture approval, update the package map/manifests, and add a
mechanical dependency guard before implementation is considered complete.

## 4. Resolve advisory ownership

- Extend `nxt_facility.decisions` for broad, deterministic manager advice that is
  a pure function of `FacilityState`.
- Extend `nxt_pilot_ops` for a named policy whose purpose includes explicit
  evaluation, decision trace, trust evidence, human workflow, or ledger records.
- Do not create a third facility decision engine.
- Do not independently implement the same recommendation in both existing
  engines. If established overlap must change, name one semantic owner and
  specify whether the other reuses it, is parity-locked to it, or intentionally
  diverges for a documented reason. Test that contract.
- Presentation or orchestration may display advisory outputs separately. Any
  aggregation, ranking, deduplication, conflict resolution, or composition
  requires an approved tested contract; it must not happen silently or create
  another policy owner.

Current ball-availability overlap is documented intentional divergence, not
parity: facility rules use the v1 state/supply model; the Guardian owns its
richer traced policy and fails closed on missing permission/capability/ETA/
yield/washer facts. Preserve owner/policy identity. Any component that combines,
deduplicates, ranks, or resolves those outputs requires its own approved and
tested composition contract.

## 5. Write the boundary card

Before editing, record:

- fact/behavior and single owner;
- implementation status and branch dependency;
- inputs, outputs, consumers, and persistence;
- source-of-truth tier: runtime, static manifest, observation/evidence,
  downstream state, advice, history, projection, or execution;
- allowed imports and the reverse dependency that must remain impossible;
- schema/version, migration, replay, canonical-byte, and drift implications;
- missing/stale/default/provenance behavior;
- determinism, RNG, clock, and failure semantics;
- safety/admission boundary and human authority; and
- focused, boundary, parity, integration, and full-suite verification.

Use a diagram only when it makes the data/dependency flow materially clearer.

## 6. Apply deployment and execution gates

For physical-site work, verify the
[deployment contract](../context/deployment.md): commissioning owns static facts,
telemetry owns observations/assembly evidence, FacilityState remains the
downstream contract, and `nxt_site_runtime` owns orchestration only. The runtime
must retain the exact `FacilityState` and separate `AssemblyReport`, own no
observation/state/advice/projection/execution semantics, and keep its quality
gate strictly about state-publication data quality—not physical command
admission or robot safety. Concrete physical sources/transports/publishers,
hardware/vendor integrations, live-site service operation, and production
deployment remain absent.

For robot/control work, reject a design unless it preserves:

- closed simulator directives through `RangeSimulation.apply_directive()` and
  `SafetyShield`;
- `HandoffController` sequencing through `RobotTaskInterface`;
- recognition that this seam is micro handoff only, not a site-level collector
  dispatch or command-translation API;
- hard timeouts, invalid-sequence failure, bounded retry/recovery, safe retract,
  externally reset e-stop latching, and no motion after e-stop; and
- the universal rule that an LLM/generative or advisory output cannot directly
  call simulator directive application, a robot interface, adapter, ROS,
  actuator, or e-stop API.

Any physical command bridge or AI-to-execution path is a new high-risk
architecture boundary. Pause for explicit approval; do not infer authorization
from a UI button, recommendation acceptance, tool call, or existing mock.

## 7. Record the decision

End the gate with one outcome:

- **Proceed:** an existing owner and allowed dependency path are clear.
- **Reshape:** reuse an existing owner/contract or move composition to a root.
- **Pause:** ownership, branch status, schema, safety, or duplication remains
  unresolved and needs an architecture decision.

Include the result in the implementation plan and final handoff. The post-change
[review workflow](review.md) verifies that the implementation matches it.
