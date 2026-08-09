# Review workflow and checklist

Review findings-first. Inspect the actual diff and call out concrete failures
with file/line evidence before summaries. Do not infer approval from an authored
PR description, bot comment, or historical test count.

## 1. Scope and repository state

- Does the diff contain only the requested surface?
- Are pre-existing worktree changes preserved and distinguished?
- Is the branch/stack base understood?
- Are merged/current-checkout, unmerged-branch, approved-design, and
  proposed/future components labeled separately rather than unioned?
- Are generated files, caches, reports, build artifacts, secrets, or machine
  paths absent?

## 2. Truth ownership

- Is `RangeSimulation` still the only live mutable simulation truth?
- Is ball accounting still owned by `BallLedger`?
- Is `FacilityState` still frozen and canonical downstream?
- For physical deployment, do static facts remain owned by the commissioned
  manifest, rather than a scenario, `SiteConfig`, observation, viewer/layout
  file, or USD?
- Are observations, estimates, memory, recommendations, viewer frames, and USD
  labeled and used according to their truth tier?
- Does every deployment-path `FacilityState` retain its separate
  `AssemblyReport`/quality context instead of presenting backfill as measured?
- Does Site Runtime keep the exact `FacilityState` rather than defining another
  state model or assembler, and retain source, quality, envelope, sequence,
  checkpoint, recovery, and idempotency evidence?
- Does missing/default/backfill behavior match the owning contract, with
  missingness and provenance surfaced instead of masquerading as measured
  data?

## 3. Dependency direction

- Does every new import follow [the package map](../context/package-map.md)?
- Is upstream still unaware of downstream packages?
- Within downstream Site OS packages, is coupling isolated to a designated
  builder/harvest/assembler/adapter, Site Runtime pipeline/setup seam, or
  composition-root script? Do benchmark and viewer tools use only the public
  `nxt_range_ops` APIs allowed by the package map?
- Are pure contracts still importable without optional simulation, UI, or USD
  dependencies?
- Does a new boundary have a mechanical guard rather than prose alone?
- Did the author search manifests, package trees, schemas, stores, rule IDs,
  tests, and open branches before adding a package or engine?
- Does any new package have a distinct owner/lifecycle and an approved place in
  the dependency graph, or does it duplicate an existing responsibility?

## 4. Advice, execution, and safety

- Do facility and Shadow Ops outputs remain advisory and immutable?
- Is broad FacilityState-derived manager advice owned by
  `nxt_facility.decisions`, while policy-specific trust/trace/evaluation and
  workflow remain in `nxt_pilot_ops`?
- Has a recommendation been duplicated across those surfaces or moved into a
  third engine without a named semantic owner and tested reuse/parity/divergence
  contract?
- Does any UI/runtime preserve the owner and evidence of the existing non-parity
  ball-availability outputs instead of silently merging, ranking, or resolving
  them without an approved composition contract?
- Is there any directive, ROS, command-bus, actuator, motion, charging, or
  e-stop call in advisory code?
- Can an LLM, generative agent, tool call, UI, or Site Runtime component directly
  reach `RangeSimulation.apply_directive()`, `RobotTaskInterface`, an adapter,
  ROS, an actuator, or e-stop API?
- Is Site Runtime's `QualityGate` still publication-data-quality admission only,
  rather than recommendation policy, physical command admission, or robot
  safety authorization?
- Do simulator actions still pass through `RangeSimulation.apply_directive()`
  and `SafetyShield`?
- Does handoff execution remain behind `RobotTaskInterface` without controller
  knowledge of adapters?
- Is `RobotTaskInterface` being misrepresented as a whole-site collector
  dispatch API or physical command gateway?
- Are hard timeouts, classified invalid sequencing, bounded retries, safe
  retract, externally reset e-stop latching, and no-motion-after-e-stop intact?
- Are physical-backend claims honest about current stubs?

## 5. Determinism, integrity, and replay

- Can read-only instrumentation change RNG state, event ordering, metrics,
  observations, or rewards?
- Are clocks, UUIDs, random sources, ordering, float serialization, newlines,
  and hashes explicit and stable where required?
- Do canonical/append-only readers reject duplicate keys, truncation, drift,
  illegal transitions, and unknown versions?
- Are projection and alternate-path outputs compared to the canonical source?
- Would old episodes, model versions, and artifacts remain interpretable?

## 6. Product honesty

- Are placeholders, estimates, synthetic observations, and non-causal results
  disclosed?
- Does the change avoid claims of validated physical performance, production
  telemetry, or deployed robot control?
- Does the twin refrain from inventing geometry, motion, physics, or facts?
- Do claims state that Commissioning, Shadow Ops, and Site Runtime are merged
  while concrete physical telemetry adapters/transports, live hardware/vendor
  integrations, production publishers/sinks, site-level physical command
  admission, autonomous actuator execution, live Omniverse/Nucleus delivery,
  and production real-site deployment remain absent?
- Does ROI code reuse the canonical engine rather than duplicating formulas?

## 7. Tests and documentation

- Does each defect fix have a regression test?
- Do tests exercise the failure mode rather than a tautology or vacuous mock?
- Are optional extras installed so relevant tests execute instead of skip?
- Did focused, boundary, full, config, and build checks run as required?
- Do docs name the contract, source owner, non-goals, limitations, and exact
  verification evidence?
- When the pre-implementation architecture gate was triggered, is its
  proceed/reshape/pause decision recorded and reflected in the implementation?
- Are status and test claims observed facts rather than copied PR prose?

## Severity guide

- **Critical:** creates/bypasses truth or safety ownership, introduces command
  execution from advisory code, corrupts audit/replay integrity, or fabricates
  operational facts.
- **High:** reverses a package boundary, duplicates a semantic owner, relies on
  a falsely integrated/absent branch, breaks determinism/compatibility, or
  weakens fail-closed behavior.
- **Medium:** incomplete validation, misleading documentation, unguarded
  coupling, or material missing tests.
- **Low:** localized maintainability/documentation issue with no current
  contract or behavior impact.

If there are no actionable findings, say so and list residual unverified risks
or skipped checks. Do not manufacture findings to populate a review.
