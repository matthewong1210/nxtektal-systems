# Pilot dispatch service capabilities v2

`GET /api/v0/task-ops` may declare the closed continuous V3 service mode with
the `service_capabilities` object in `schema.json`:

```json
{
  "schema": "nxt-pilot-dispatch/service-capabilities/v2",
  "mode": "CONTINUOUS_V3_EXECUTION",
  "operations": {
    "planning_inputs_create": "SUPPORTED",
    "planning_plans_create": "SUPPORTED",
    "planning_confirmations_create": "SUPPORTED",
    "planning_outcomes_create": "SUPPORTED",
    "schedules_create": "UNAVAILABLE",
    "schedules_cancel": "SUPPORTED",
    "notifications_acknowledge": "SUPPORTED",
    "notifications_resolve": "SUPPORTED"
  }
}
```

This version has exactly one schema/mode/matrix pairing. It does not revise,
reinterpret, or accept any v1 pairing. `SUPPORTED` declares only that the
service composition installs an operation; scheduler health, request validity,
and record-level preconditions remain separate gates.

The unavailable `schedules_create` operation preserves continuous V3's closed
planning-confirmation path. The supported cancellation and notification writes
retain their existing evidence-only semantics and do not start, resume, or
unlock execution.

This is a noncanonical service projection. It adds no journal record, changes
no replay bytes, and owns no Planning, schedule, notification, simulator,
device, or SafetyShield semantics.
