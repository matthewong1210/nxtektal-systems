# Staffing advisory Manager API contract v1

This directory freezes the cross-language wire contract for the planned staffing
advisory Manager API. It is an executable design contract, not a claim that the
routes or Console already exist in this checkout. The existing staffing domain
remains a local advisory library: it does not write HR/payroll/attendance data,
notify staff, issue robot commands, or turn an accepted suggestion into physical
execution.

All objects are closed. Unknown fields fail validation. Success responses use the
existing `nxt-site-agent/api/v0` `schema`/`disclaimer`/`data` envelope; errors use
the same envelope with `error.code` and `error.detail`. The fixture disclaimer is
the literal `SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA`.

## Public model

`StaffingDateSnapshot` is the only read snapshot. It always includes the literal
`nxt-staffing/v1`, `SIMULATION`, and `STAFFING_ADVISORY_ONLY` markers, server UTC
time, service date, deployment context, provider readiness, all three revisions,
and explicit nullable roster/effective-plan values. The root route resolves the
current date in the deployment's IANA timezone; the dated route uses the path date.

The six mutation request families are `RosterImportRequest`,
`ExceptionRecordRequest`, `ExceptionCancelRequest`, `ExceptionCorrectRequest`,
`SuggestionGenerateRequest`, and `ManagerResponseRequest`. Every request includes
a caller-provided `request_id` and attribution-only `operator`. That operator is
not an authenticated identity. Correction inherits staff and service date from
the path-addressed exception; the wire request cannot move an exception to another
employee or day.

`StaffingReceipt` is dispatched on all three of `operation_kind`, `state`, and
`record.record_kind`. A recovery GET always returns `disposition: "duplicate"`.
The public `suggestion_id` is the domain generation ID unchanged, while
`operation_id` is the receipt event ID. `retry_of` names the earlier suggestion,
never an event ID. Snapshot `generations` are ordered by reservation time, oldest
first and newest last. Their request, suggestion, and operation IDs are each
unique. A non-null `retry_of` must name exactly one earlier `RESULT_UNKNOWN`
generation, and a `RESULT_UNKNOWN` generation can have at most one retry.

Candidate projections restore local staff IDs and display names for manager
review. A manager modification sends the smaller `ManagerPatchOperation` union;
the server resolves and revalidates it against the current basis. ACCEPT/MODIFY
retain a non-null `CURRENT` effective advisory plan after refresh; REJECT retains
a null plan. None of these responses represents a formal schedule or execution
truth.

Snapshot `assignments` are the regular roster shifts materialized for the
snapshot service date. They are not replaced by an accepted advisory plan and
are not reduced by active exceptions; local exception entry uses this list to
offer only employees with a regular shift. The current accepted advisory
schedule, when one exists, is projected separately as
`effective_plan.assignments`.

## Time, bounds, and privacy

Dates are `YYYY-MM-DD`; local roster minutes are strict `HH:MM`. Server and ledger
audit timestamps are UTC with exactly six fractional digits and uppercase `Z`.
Operational timestamps use uppercase `Z` for zero
offset and signed `±HH:MM` for a nonzero offset. They are whole-minute values
rendered with `:00` seconds; `+00:00` and `-00:00` are never accepted. Candidate
count is at most two, provider provenance count is at most
two, candidate and manager patch operations are at most 32, and operational
warnings are at most five. Identifier, canonical-code, text, token, count, and
array bounds are encoded in `schema.json` rather than left to prose.

Each coverage gap requires `required_count >= 1` and
`0 <= assigned_count < required_count`. JSON Schema 2020-12 has no portable
cross-property numeric comparison keyword, so the schema bounds both integers
and the planned Python/TypeScript semantic decoders must enforce the strict relation;
the executable fixture traversal pins it here. No nonstandard `$data` keyword is
used. The projector, not this schema, owns equality between top-level flattened
gaps and nested candidate gaps.

Provider worker/assignment aliases, alias maps, nonce material or digests,
prompts, raw provider output, reasoning, request headers, API keys, endpoints,
timeouts, and exception text never cross this contract. Provenance is the bounded
pairing of an internal attempt start and finish; an unmatched start can make a
generation `IN_PROGRESS` but is not serialized as fabricated finished provenance.
A two-attempt sequence is only GLOBAL OpenAI primary with a fallback-eligible
failure followed by GLOBAL Anthropic backup. Terminal success and failure rows
must agree with the terminal record outcome. Every row in one sequence must also
share one `input_digest`; JSON Schema cannot compare sibling values, so the
runtime parser/projector must enforce that equality and the fixtures test it.

## Success exchange inventory

| Fixture / exchange | Method and route | Request `$defs` | Response `$defs` | Receipt triple | HTTP |
|---|---|---|---|---|---:|
| `cold-start.json` / `current-empty` | GET `/api/v1/staffing` | none | `StaffingDateSnapshot` | — | 200 |
| `cold-start.json` / `date-empty` | GET `/api/v1/staffing/dates/{service_date}` | none | `StaffingDateSnapshot` | — | 200 |
| `roster-import.json` / `roster-committed` | POST `/api/v1/staffing/roster-imports` | `RosterImportRequest` | `StaffingReceipt` | `roster-import / COMMITTED / ROSTER_IMPORTED` | 200 |
| `exception-correction.json` / `exception-recorded` | POST `/api/v1/staffing/exceptions` | `ExceptionRecordRequest` | `StaffingReceipt` | `exception-record / COMMITTED / EXCEPTION_RECORDED` | 200 |
| `exception-correction.json` / `exception-cancelled` | POST `/api/v1/staffing/exceptions/{exception_id}/cancel` | `ExceptionCancelRequest` | `StaffingReceipt` | `exception-cancel / COMMITTED / EXCEPTION_CANCELLED` | 200 |
| `exception-correction.json` / `exception-corrected` | POST `/api/v1/staffing/exceptions/{exception_id}/correct` | `ExceptionCorrectRequest` | `StaffingReceipt` | `exception-correct / COMMITTED / EXCEPTION_CORRECTED` | 200 |
| `generation-success.json` / `generation-reserved` | POST `/api/v1/staffing/suggestions` | `SuggestionGenerateRequest` | `StaffingReceipt` | `suggestion-generate / RESERVED / GENERATION_RESERVED` | 202 |
| `generation-success.json` / `generation-in-progress` | POST `/api/v1/staffing/suggestions` | `SuggestionGenerateRequest` | `StaffingReceipt` | `suggestion-generate / IN_PROGRESS / GENERATION_IN_PROGRESS` | 202 |
| `generation-success.json` / `suggestion-issued` | POST `/api/v1/staffing/suggestions` | `SuggestionGenerateRequest` | `StaffingReceipt` | `suggestion-generate / SUCCEEDED / SUGGESTION_ISSUED` | 202 |
| `generation-unavailable.json` / `no-valid-suggestion` | POST `/api/v1/staffing/suggestions` | `SuggestionGenerateRequest` | `StaffingReceipt` | `suggestion-generate / NO_VALID_SUGGESTION / SUGGESTION_UNAVAILABLE` | 202 |
| `generation-unavailable.json` / `unavailable` | POST `/api/v1/staffing/suggestions` | `SuggestionGenerateRequest` | `StaffingReceipt` | `suggestion-generate / UNAVAILABLE / SUGGESTION_UNAVAILABLE` | 202 |
| `generation-unavailable.json` / `refused` | POST `/api/v1/staffing/suggestions` | `SuggestionGenerateRequest` | `StaffingReceipt` | `suggestion-generate / REFUSED / SUGGESTION_UNAVAILABLE` | 202 |
| `generation-unavailable.json` / `invalid-response` | POST `/api/v1/staffing/suggestions` | `SuggestionGenerateRequest` | `StaffingReceipt` | `suggestion-generate / INVALID_RESPONSE / SUGGESTION_UNAVAILABLE` | 202 |
| `generation-unavailable.json` / `provider-error` | POST `/api/v1/staffing/suggestions` | `SuggestionGenerateRequest` | `StaffingReceipt` | `suggestion-generate / PROVIDER_ERROR / SUGGESTION_UNAVAILABLE` | 202 |
| `generation-unavailable.json` / `configuration-error` | POST `/api/v1/staffing/suggestions` | `SuggestionGenerateRequest` | `StaffingReceipt` | `suggestion-generate / CONFIGURATION_ERROR / SUGGESTION_UNAVAILABLE` | 202 |
| `generation-unavailable.json` / `security-error` | POST `/api/v1/staffing/suggestions` | `SuggestionGenerateRequest` | `StaffingReceipt` | `suggestion-generate / SECURITY_ERROR / SUGGESTION_UNAVAILABLE` | 202 |
| `result-unknown.json` / `generation-interrupted` | GET `/api/v1/staffing/requests/{operation_kind}/{request_id}` | none | `StaffingReceipt` | `suggestion-generate / RESULT_UNKNOWN / GENERATION_INTERRUPTED` | 200 |
| `manager-response.json` / `manager-accepted` | POST `/api/v1/staffing/suggestions/{suggestion_id}/accept` | `ManagerResponseRequest` (`ACCEPT`) | `StaffingReceipt` | `manager-response / COMMITTED / MANAGER_RESPONSE_COMMITTED` | 200 |
| `manager-response.json` / `manager-modified` | POST `/api/v1/staffing/suggestions/{suggestion_id}/modify` | `ManagerResponseRequest` (`MODIFY`) | `StaffingReceipt` | `manager-response / COMMITTED / MANAGER_RESPONSE_COMMITTED` | 200 |
| `manager-response.json` / `manager-rejected` | POST `/api/v1/staffing/suggestions/{suggestion_id}/reject` | `ManagerResponseRequest` (`REJECT`) | `StaffingReceipt` | `manager-response / COMMITTED / MANAGER_RESPONSE_COMMITTED` | 200 |
| `manager-response.json` / `date-after-manager-response` | GET `/api/v1/staffing/dates/{service_date}` | none | `StaffingDateSnapshot` | — | 200 |

The three manager fixtures intentionally share the same public receipt triple;
their nested response kind/reason/plan branches remain independently closed.
Each committed manager record preserves the bounded `operator` attribution from
its request alongside the response kind, reason, note, plan, and commit time.
Across the inventory there are exactly sixteen unique receipt triples.

## Error exchange inventory

| Error code | Example route | HTTP |
|---|---|---:|
| `staffing_invalid_request` | POST `/api/v1/staffing/exceptions` | 400 |
| `staffing_request_not_found` | GET `/api/v1/staffing/requests/{operation_kind}/{request_id}` | 404 |
| `staffing_not_found` | GET `/api/v1/staffing/dates/{service_date}` | 404 |
| `staffing_conflict` | POST `/api/v1/staffing/roster-imports` | 409 |
| `staffing_exception_overlap` | POST `/api/v1/staffing/exceptions` | 409 |
| `staffing_stale_suggestion` | POST `/api/v1/staffing/suggestions/{suggestion_id}/accept` | 409 |
| `staffing_busy` | POST `/api/v1/staffing/suggestions` | 429 |
| `staffing_unavailable` | GET `/api/v1/staffing` | 503 |

These eight codes are the staffing-specific error inventory. Existing Manager API
transport errors remain outside `errors.json`; a consumer must fail closed on an
unknown code/status pair rather than silently reclassify it.

`staffing_exception_overlap` is an additive v1 code: a new exception record,
or a correction, whose half-open interval would intersect an active exception
of the same worker on the same service date is refused before any append. The
original record is untouched, the same request ID with the same body still
replays its original receipt, and recovery is a cancel or correction of the
existing record (or a non-overlapping interval) under a new request ID. It is
distinct from `staffing_conflict`, which keeps its idempotency, revision, and
lifecycle meanings.

## Domain-to-wire authority

| Domain replay authority | `operation_kind` | wire `state` | `record_kind` |
|---|---|---|---|
| `roster_imported` | `roster-import` | `COMMITTED` | `ROSTER_IMPORTED` |
| `exception_recorded` | `exception-record` | `COMMITTED` | `EXCEPTION_RECORDED` |
| `exception_cancelled` | `exception-cancel` | `COMMITTED` | `EXCEPTION_CANCELLED` |
| `exception_corrected` | `exception-correct` | `COMMITTED` | `EXCEPTION_CORRECTED` |
| `generation_reserved`, no attempt boundary | `suggestion-generate` | `RESERVED` | `GENERATION_RESERVED` |
| `generation_reserved` plus nonterminal attempt boundary | `suggestion-generate` | `IN_PROGRESS` | `GENERATION_IN_PROGRESS` |
| `generation_interrupted` | `suggestion-generate` | `RESULT_UNKNOWN` | `GENERATION_INTERRUPTED` |
| `suggestion_issued` | `suggestion-generate` | `SUCCEEDED` | `SUGGESTION_ISSUED` |
| `suggestion_unavailable` | `suggestion-generate` | unavailable terminal | `SUGGESTION_UNAVAILABLE` |
| `manager_response_committed` | `manager-response` | `COMMITTED` | `MANAGER_RESPONSE_COMMITTED` |

The future composition-owned `project_request_to_wire()` is the sole adapter for
this table. It copies only declared fields, maps domain gap `required`/`actual` to
`required_count`/`assigned_count`, adds the literal `COVERAGE_GAP`, and rejects an
impossible tuple instead of manufacturing a fallback record. The domain does not
import HTTP fields or construct Manager API envelopes.
