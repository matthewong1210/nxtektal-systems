# NXTektal Commissioning v0

Commissioning is the authoritative bridge from a surveyed, inspected physical
facility into NXTektal OS:

```text
physical facility
    -> evidence-backed commissioning manifest
        -> static site configuration projection
        -> digital-twin layout metadata
        -> telemetry-adapter binding configuration
```

The manifest answers **what exists and how the facility is configured**. It
does not answer what is happening now. Robot battery, robot pose, payload,
inventory, tasks, demand, observations, and other live values are deliberately
absent and remain owned by downstream runtime contracts.

## Authority and provenance

`CommissionedSite` is a frozen deployment manifest. Its nested collections are
tuples, capacities and operational limits are `MeasuredValue` records, spatial
points carry point-level survey provenance, and every important declaration
carries a `Provenance` record. Supported evidence
vocabulary distinguishes manufacturer specifications, manual measurements,
operator input, survey data, sensor calibration, and imported records.

The manifest is rejected when identity, provenance, references, capability
compatibility, geometry, robot safety declarations, or sensor bindings are
invalid. Safety-relevant fields have no implicit defaults. Serialization is
strict: unknown or missing keys, duplicate JSON keys, non-finite values, and
unsupported schema versions fail loudly.

## Independent projection boundaries

The projection module is stdlib-only and imports no downstream system. Each
projection is deterministic, JSON-ready, provenance-preserving, and disposable:
the commissioned manifest remains authoritative.

- `project_site_config` emits the static site identity, topology, capacities,
  capabilities, operating constraints, and provenance needed by a future site
  configuration adapter.
- `project_legacy_site_config` emits the current `SiteConfig` constructor shape.
  Its required frozen `LegacySiteConfigContext` keeps simulation/service inputs
  such as seed and forecast cadence outside the authoritative manifest, with no
  defaults or invented values.
- `project_digital_twin_layout` emits local metric geometry and declared asset
  placement references. It refuses to guess coordinate transformations.
- `project_telemetry_adapter_config` emits physical source-to-canonical-channel
  bindings and explicit calibration evidence. It emits no readings, sample
  timestamps, observation status, or transport state; calibration status and
  evidence timestamps are preserved.

The existing telemetry site contract still contains simulation-era fields such
as a random seed, and the existing viewer layout contains runtime initialization
values. Commissioning v0 does not fabricate either. The explicit legacy context
supplies the former only at projection time; runtime initialization values remain
excluded. No consumer package is imported or modified.

## Storage

`CommissioningStore` persists canonical JSON at
`<root>/<site_id>/<deployment_id>/manifest.json`. Writes are atomic and
immutable by deployment identity: identical saves are idempotent, while a
different manifest at the same identity is rejected. A changed facility record
therefore receives a new deployment ID rather than rewriting commissioning
history.
