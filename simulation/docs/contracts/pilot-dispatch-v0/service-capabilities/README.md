# Pilot dispatch service capabilities v1

`GET /api/v0/task-ops` keeps its additive `nxt-pilot-dispatch/v0` snapshot and
may include the required-by-current-services top-level `service_capabilities`
object described by `schema.json`:

```json
{
  "schema": "nxt-pilot-dispatch/service-capabilities/v1",
  "mode": "FIXED_V3_EXECUTION",
  "operations": {
    "planning_inputs_create": "UNAVAILABLE",
    "planning_plans_create": "UNAVAILABLE",
    "planning_confirmations_create": "UNAVAILABLE",
    "planning_outcomes_create": "SUPPORTED",
    "schedules_create": "UNAVAILABLE",
    "schedules_cancel": "UNAVAILABLE",
    "notifications_acknowledge": "SUPPORTED",
    "notifications_resolve": "SUPPORTED"
  }
}
```

The declaration answers only whether that service composition installs an
operation. `SUPPORTED` is not permission for a particular request. A write
also requires a fresh `RUNNING` scheduler read, valid request content and every
record-level precondition. In particular, `notifications_resolve=SUPPORTED`
does not override a notification's `condition_active` or `can_resolve` fields.
`scheduler.state=FAILED` does not rewrite the static declaration; it makes all
writes temporarily unavailable and the server returns 503.

The frozen matrices are:

| Operation | `FIXED_V3_EXECUTION` | `LEGACY_PILOT_DISPATCH` |
|---|---|---|
| Create Planning input | `UNAVAILABLE` | `SUPPORTED` |
| Create/revise Planning plan | `UNAVAILABLE` | `SUPPORTED` |
| Create Planning confirmation | `UNAVAILABLE` | `SUPPORTED` |
| Record Planning outcome | `SUPPORTED` | `SUPPORTED` |
| Create direct schedule | `UNAVAILABLE` | `SUPPORTED` |
| Cancel pending schedule | `UNAVAILABLE` | `SUPPORTED` |
| Acknowledge notification | `SUPPORTED` | `SUPPORTED` |
| Resolve notification | `SUPPORTED` | `SUPPORTED` |

The fixed V3 service returns deterministic 409 errors for its five unsupported
write routes without changing evidence. Its outcome and notification writes
retain their existing evidence-only semantics and never start, resume or
unlock execution. The legacy service retains all existing write routes.

Historical task-ops payloads without `service_capabilities` remain readable.
Their service mode is `UNDECLARED` in the TypeScript projection and every
write is fail-closed. A browser must not infer mode or support from HTTP 200,
`transport`, the presence or absence of `runtime`, or the old
`accepts_new_*` booleans. A present but incomplete, unknown or mode-inconsistent
capability object is an invalid task-ops response.

This is a noncanonical service projection. It adds no journal record, changes
no replay bytes, and owns no Planning, schedule, notification, simulator,
device or SafetyShield semantics. The two composition roots declare the routes
they install; the browser only consumes the validated declaration.

Architecture gate decision: **PROCEED**. The existing composition roots own
their installed-route descriptions, and the existing console parser owns the
fail-closed presentation adapter. No package, mutable truth, decision engine,
runtime loop, execution path or reverse dependency is added.
