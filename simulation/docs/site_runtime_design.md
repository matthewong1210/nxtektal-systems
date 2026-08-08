# Site Runtime v0 architecture placement review

## Decision

`nxt_site_runtime` owns the hot-path coordination between normalized physical
observations and existing downstream consumers:

```
physical adapters -> ObservationFrame -> validate -> existing assembler
                  -> AssemblyReport quality gate -> FacilityState envelope
                  -> idempotent publisher -> decisions / Shadow Ops / memory / twin
```

This is an orchestration responsibility because it spans source scheduling,
site-level ordering, failure handling, recovery, and publication.  None of
those concerns changes the meaning of an observation or of facility state.

## Why the existing packages do not own it

- `nxt_commissioning` is the physical deployment source of truth.  Composition
  copies `site_id`, `deployment_id`, and an existing SiteConfig projection from
  one commissioned site into a runtime instance; commissioning does not run the
  observation loop.
- `nxt_telemetry` owns `Observation`, `ObservationFrame`,
  `assemble_from_observations`, and `AssemblyReport`.  Runtime calls those
  contracts unchanged and defines no alternate assembler. The callable seam
  exists only for composition and fault testing.
- `nxt_facility` owns the canonical frozen `FacilityState`.  Runtime holds and
  publishes that exact object; identity and delivery metadata stay in the
  envelope rather than changing the state schema.
- `nxt_range_ops` / `RangeSimulation` remain simulation truth.  Runtime neither
  imports simulator APIs nor writes simulator state.
- decision rules and `nxt_pilot_ops` remain downstream judgment/trust layers.
  Runtime never recommends, approves, or executes an action.
- `nxt_memory` remains append-only evidence, and `nxt_range_twin` remains a USD
  projection.  Both receive published state through adapters outside runtime.

## Quality and failure behavior

Runtime v0 always rejects stale or missing input, so assembler fallback values
cannot be published as current truth. The configurable gate rejects excessive
consistency issues, effective confidence below 0.8, and provenance grades
outside `high`/`medium`. Effective confidence is the minimum of the existing
assembly confidence and upstream-input confidence. All decisions are mechanical
evaluations of existing contracts and provenance metadata. Invalid identity,
frame shape/timing, sequence gaps, assembly errors, and quality failures raise
a typed `SiteRuntimeError` before publication and do not advance the successful
checkpoint.

## Determinism and recovery

The envelope ID is SHA-256 over canonical JSON containing immutable identity,
observation time, site-level sequence, the exact dataclass fields of
FacilityState and AssemblyReport, runtime quality, and sorted structured
observation/upstream references. Identity hashing never uses their rounded
display serializers. The ID is snapshotted once during envelope construction;
there is no wall-clock, UUID, or randomness.

The source port is at-least-once: `observe()` peeks the same immutable batch
until runtime calls `acknowledge()` after a completed checkpoint. Terminal
validation/quality rejections at the next expected publish position call
`reject()`, which discards the bad physical input while reusing its publish
sequence for the next frame. Sequence gaps, pending replays, and completed
replay mismatches are retained recovery incidents: runtime never discards or
resequences them automatically. `UpstreamInputs` and their structured
provenance are bound into the batch, so a retry cannot silently read newer
POS/forecast values.

Checkpoint transitions use atomic compare-and-save:

```
last successful N -> pending N+1/envelope-id -> publish -> successful N+1
                            |
                            +-- crash/uncertain result: replay only the exact
                                pending sequence + envelope-id
```

Publishers are required to use the envelope ID as their idempotency key.  A
completed sequence replay is acknowledged without republishing only when its
content hashes identically; a pending sequence cannot be replaced by different
content; deployment identity scopes every checkpoint. The first accepted
sequence establishes the source's starting offset, after which ordering is
strictly contiguous. Pending and completed replays verify the saved envelope
identity but do not re-authorize already accepted content under a newly changed
quality policy.

The JSON checkpoint store uses an identity-scoped inter-process lock, atomic
replacement, and file plus directory synchronization on POSIX systems. The
in-memory store exists for tests and single-process composition only.

## Installation boundary

The runtime contracts and pipeline class are importable without the simulation
or USD stack. Successful processing always enforces the existing canonical
`AssemblyReport`; that contract currently lives beside an assembler which
reuses simulator entity vocabulary. Install the existing `range-ops` extra for
processing, including successful injected-assembler composition. This is
compatibility plumbing, not simulator ownership by runtime. An injected fault
seam can still fail before the default assembler is resolved, which keeps
assembly failures typed in a minimal installation.
