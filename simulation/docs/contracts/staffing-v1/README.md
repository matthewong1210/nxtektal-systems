# Staffing advisory Manager API contract v1

This directory freezes the cross-language wire contract for the staffing
advisory Manager API that the SIMULATION composition root
(`scripts/staffing_operations.py`) serves through the injected
`/api/v1/staffing` Site Agent callback and the Manager Console consumes. It is
an executable contract: the Python and Console suites validate every example
and every live emission against it. The staffing domain remains a local
advisory library: it does not write HR/payroll/attendance data, notify staff,
issue robot commands, or turn an accepted suggestion into physical execution.

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

Every `StaffingDateSnapshot`, every `StaffingReceipt`, and every request body
also carries `contract_revision`, a 64-hex digest of the staffing
implementation sources of the checkout that built the sender (see Versioning
and lockstep rollout). It is a wire fact only: the service admits a write only
at its own value and strips the field before the domain, the Console decodes a
snapshot or receipt only at its own value, and the ledger never records it.

The six mutation request families are `RosterImportRequest`,
`ExceptionRecordRequest`, `ExceptionCancelRequest`, `ExceptionCorrectRequest`,
`SuggestionGenerateRequest`, and `ManagerResponseRequest`. Every request includes
the lockstep `contract_revision`, a caller-provided `request_id`, and an
attribution-only `operator`. That operator is not an authenticated identity. Correction inherits staff and service date from
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
review. Each candidate also carries four required review fields,
`rationale_local`, `operational_warnings_local`, `action_window_end_at`, and
`actionability`, which the 2026-10-08 revision of this contract added to the
closed `CandidateProjection` objects. They are not optional and the revision is
not backward-compatible: a Console built before it rejects every snapshot or
receipt that contains a candidate, and a Console built with it rejects every
candidate that lacks them (see Versioning and lockstep rollout).
`rationale_local` and `operational_warnings_local` are the provider's
`rationale` and `operational_warnings` with every reserved `worker_<hex>` or
`assignment_<hex>` token replaced by the local display name or the
`name (ROLE/AREA HH:MM–HH:MM)` shift label the reservation bound it to; when
the shift crosses a UTC offset change, or either wall time is repeated in the
site zone (a daylight-saving fall-back), each endpoint carries its own
` UTC±HH:MM` suffix, so the one-hour New York fall-back shift reads
`01:30 UTC-04:00–01:30 UTC-05:00`. The raw provider text stays beside them
unchanged, an alias the reservation does not know is left as written, and the
replacement happens only in the local projection, never on the provider wire.
`action_window_end_at` is an `OffsetTimestamp` naming the earliest end of any
shift the candidate adds or removes (local midnight after the service date when
the candidate has no timed operation); the service renders it in UTC, and the
frozen examples carry the equivalent site-offset form. Past it the candidate is
no longer a current action, because at least one affected shift has ended. `actionability` is
`CURRENT` or `EXPIRED` exactly as the snapshot's `server_time_utc`, or the
receipt's audit instant, compares against that end (`EXPIRED` once the instant
is reached). An `EXPIRED` candidate is historical: a console must not offer
ACCEPT or MODIFY for it, and the service refuses such a response with
`staffing_suggestion_expired` without appending anything. REJECT remains
allowed as a recorded manager decision. A console may additionally keep a
deadline to the earliest open `action_window_end_at`: the instant, on the
browser's monotonic clock, at which the read that fetched the snapshot began
plus the remainder the service measured (`action_window_end_at` minus
`server_time_utc`). The read began no later than the service stamped the
snapshot, so a slow response can only place the end earlier than the truth;
for a window already known the earliest end stands, so a repeated or older
server stamp never moves it later. When the deadline is reached the console
closes its controls and reads again, and the window stays closed until the
service's next `actionability` relabels the candidate or a different window
becomes the earliest open one; only the service relabels a candidate.

A manager modification sends the smaller `ManagerPatchOperation` union;
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
warnings are at most five. A local explanation is at most 2000 characters and a
local warning at most 1500: the proven ceilings are 1776 and 1135 because a
replacement only grows where a 31- or 35-character alias becomes at most a
100-character name or a 222-character shift label (a 100-character name, the
role and area codes, and a window carrying two dates and two ` UTC±HH:MM`
suffixes). Identifier, canonical-code, text, token, count, and
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
| `staffing_suggestion_expired` | POST `/api/v1/staffing/suggestions/{suggestion_id}/accept` | 409 |
| `staffing_contract_mismatch` | POST `/api/v1/staffing/roster-imports` | 409 |
| `staffing_busy` | POST `/api/v1/staffing/suggestions` | 429 |
| `staffing_unavailable` | GET `/api/v1/staffing` | 503 |

These ten codes are the staffing-specific error inventory. Existing Manager API
transport errors remain outside `errors.json`; a consumer must fail closed on an
unknown code/status pair rather than silently reclassify it.

`staffing_exception_overlap` was added to the closed v1 error inventory in
lockstep with its service rule: a new exception record,
or a correction, whose half-open interval would intersect an active exception
of the same worker on the same service date is refused before any append. The
original record is untouched, the same request ID with the same body still
replays its original receipt, and recovery is a cancel or correction of the
existing record (or a non-overlapping interval) under a new request ID. It is
distinct from `staffing_conflict`, which keeps its idempotency, revision, and
lifecycle meanings.

`staffing_suggestion_expired` was added the same way: an ACCEPT or MODIFY is
refused before any append once the service's audit instant has reached the
earliest end of any affected shift. For ACCEPT that is the stored candidate's
`action_window_end_at`; for MODIFY the stored candidate's `action_window_end_at`
is checked first (editing cannot revive an expired suggestion) and then the
earliest end of the edited operations. The suggestion keeps its stored
candidates and may still be rejected; the same request ID with the same body
replays its original receipt only if it had committed earlier. It is distinct
from `staffing_stale_suggestion`, which means the basis changed. Neither code
is additive for an older Console: it validates every error against its own
code/status table and reports an unknown pair as `invalid_staffing_response`,
which its controller treats as an unknown write outcome rather than a
rejection.

`staffing_contract_mismatch` was added on 2026-10-13 with the lockstep
`contract_revision` field: a write whose well-formed `contract_revision` names
another checkout is refused before any domain call, nothing is appended, and
the same body is refused identically every time. A missing or malformed field
is `staffing_invalid_request`. The Console reports a snapshot at another
revision under the same code before it sends any request, and a receipt at
another revision as a write whose outcome is unknown, because the service may
have saved it.

## Versioning and lockstep rollout

`nxt-staffing/v1` names a closed contract: every object has
`additionalProperties: false`, every field is required, and both the Python
emitter and the Console decoder reject an unknown or a missing key. Changing a
closed object's key set, a `const` or enum value, the meaning of an existing
field, or the error code/status inventory changes what every existing consumer
accepts or shows, whether or not the literal changes. Such a change is never
additive and never backward-compatible, and this README must not describe one
as either. The 2026-10-08 revision added the four required candidate review
fields and `staffing_suggestion_expired`; the 2026-10-13 revision added
`contract_revision` and `staffing_contract_mismatch`.

While this contract is unreleased (absent from `main`), a closed-shape or
semantic change ships only in lockstep: the Console export and the service are
built from one checkout, and the service serves the sibling
`apps/site-agent-console/out` of its own checkout for this reason. Mixed pairs
are unsupported, and nothing negotiates a version at runtime.

### What is mechanically enforced

A pin over the frozen files is not enough: commits `9e5b87b` and `02e1d7d`
carried identical `schema.json`, examples, and contract fingerprint while the
meaning of `action_window_end_at` (latest versus earliest affected end) and
the MODIFY admission rule differed. Lockstep is therefore enforced by a
contract revision derived from the implementation, not from the schema:

- `contract_revision` is a required 64-hex field of every request body, every
  `StaffingDateSnapshot`, and every `StaffingReceipt`. Its value is the SHA-256
  of a manifest (`<path> <sha256>` per file, in path order, CRLF folded to LF)
  over the covered set listed in [`lockstep.json`](lockstep.json). The
  `sources` are the staffing implementation: the `nxt_pilot_ops/staffing`
  package, `scripts/staffing_operations.py`, `nxt_site_agent/api.py`, this
  `schema.json`, and the Console's `lib/staffing*.ts`, `lib/site-time.ts`,
  `components/StaffingPanel.tsx`, and `components/staffing/*`. The
  `dependencies` are the first-party modules those sources import and that
  carry staffing-facing semantics, and they enter the digest too:
  `nxt_pilot_ops/serialization.py` (canonical JSON and `stable_digest` behind
  every ledger record and request digest), every `nxt_model_gateway` module
  the composition root reaches, `nxt_site_agent/contracts.py` (the error type
  and envelope constants), and the Console's `lib/api.ts` and
  `components/ui.tsx`. Any byte change in any covered file changes the value.
- The dependency guard makes that list complete rather than hand-maintained:
  `--check` and both suites walk every first-party import of the covered set
  and fail on a reached module that the manifest does not classify, on a
  listed dependency or boundary that nothing reaches, on a module listed
  twice, and on a dynamic import (`importlib`, `__import__`, `require`, or
  an `import()` whose first argument is not a string literal), which cannot
  be analysed. On the service side the walk follows absolute
  `nxt_*` and `scripts` imports together with every package `__init__`
  executed on the dotted path, relative imports, and `from pkg import name`
  where `name` is a submodule or subpackage; on the Console side it follows
  relative specifiers and the tsconfig `@/*` path alias in `import` and
  `export ... from` statements (any number per line; comments, template
  literals and regular-expression literals are blanked before matching, so
  a commented-out import or one inside a template is never an edge, while
  an ordinary string, which must close on its line and not be glued to a
  word or closing bracket, is kept because an import specifier is one and
  only the statement-start anchor keeps a string's contents from matching;
  a
  `/` opens a regular expression after an operator or opening bracket, a
  keyword such as `return` in code rather than JSX prose, a logical `!`,
  an arrow or the start of the file, and divides after an operand such as
  an identifier, a closing bracket or a JSX `}` or `>`, or when it would
  close on a comment opener, and a `/` that meets a backtick before it
  closes fails the check naming the file, so a regular expression that
  matches a backtick is written `\x60` and a template literal never sits
  after a misread slash; default and namespace binding names are ASCII
  identifiers; a
  leading byte-order mark is tolerated; an unterminated block comment or
  template literal, such as a lone backtick in JSX text, fails the check
  naming the file rather than silently hiding the statements after it;
  and both suites lex one shared corpus of source shapes to byte-identical
  blanked text and statements), side-effect imports, and `import()`. The
  `boundaries` are the imports the digest deliberately
  stops at, each with a recorded reason and the exact names it may export:
  the two pin modules (`STAFFING_CONTRACT_REVISION`), the Site Agent package
  root (`SiteAgentError`) and service (`SiteAgentService`) behind the HTTP
  boundary, the runtime (`AgentRuntime`) and readiness (`RangeOpsLaunchPlan`)
  types that `nxt_site_agent/contracts.py` names, and the Shadow Ops package
  root executed on the staffing subpackage's dotted path (no names). A name
  taken through a boundary that its entry does not declare, and a new import
  anywhere in the covered set, is therefore a failing check until it is
  covered or declared.
- The service admits a POST only when the body carries exactly its own pin: a
  missing or malformed field is `staffing_invalid_request` (400); a well-formed
  different value is `staffing_contract_mismatch` (409), raised before any
  domain call, so nothing is appended and the same body is refused identically
  every time. The field is stripped before the domain and never enters the
  ledger.
- The Console decodes a snapshot or receipt only when it carries exactly the
  Console's own pin: a missing field (a service built before this revision) or
  a different value is reported as `staffing_contract_mismatch` at the first
  read, before any write, and every request the Console sends carries its pin.
- The pins are `STAFFING_CONTRACT_REVISION` in
  `scripts/staffing_contract_revision.py` and
  `apps/site-agent-console/lib/staffing-revision.ts`, the `contract_revision`
  of `lockstep.json`, and the values in every frozen example. Those files and
  every test are outside the covered set, so rewriting a pin never changes the
  value it pins. They are regenerated, never edited by hand, with
  `uv run --no-sync python -B scripts/staffing_contract_revision.py --write`
  from `simulation/`; `--check` lists every pin that disagrees and checks
  every example individually: each exchange's response `data` and each POST
  exchange's `request` must carry exactly the pin, and `errors.json` must
  carry none. A value found in one example never vouches for another, and
  `--write` never adds a missing place; it only rewrites existing ones.
- Both suites recompute the digest from the manifest and compare it with every
  pin (`tests/site_agent/test_staffing_contract_revision.py` and
  `tests/staffing-contract.test.ts`), decode every example, and test both mixed
  directions: the new service against an old write (no field) and a foreign
  write (other value), the unchanged domain's closed key rule that an old
  service applies to a new write, and the new Console against an old and a
  foreign snapshot and receipt. A staffing source change without a regeneration
  fails both suites.
- The contract fingerprint (SHA-256 over `schema.json` followed by every example
  in name order, CRLF folded to LF, pinned identically in both suites) remains
  the drift guard for the frozen files themselves; because the examples carry
  the revision, it now moves with every revision as well. An otherwise
  identical checkout with CRLF line endings computes the same revision and the
  same fingerprint, and `--write` leaves its line endings as found; any other
  byte of a frozen file moves the fingerprint, and any other byte of a covered
  file moves the revision.

The set is deliberately over-inclusive: a change to `nxt_site_agent/api.py` for
another route, or to a gateway adapter, also moves the revision and costs one
regeneration, whereas omitting a file would reopen the gap above. What the
digest does not cover is exactly the declared boundaries, and only an import
edge into them: a change behind a boundary that alters staffing behaviour
without changing any covered file is possible only through those modules
(the Site Agent service and package root, the Shadow Ops package root, the
runtime and readiness types, and the two pin modules), so a review that finds
staffing meaning there moves the module from `boundaries` to `dependencies`
in the same commit.

### What a mixed pair looks like

A Console built before this revision against a newer service refuses every
snapshot as `invalid_staffing_response` (unknown key) and never writes. A
Console of this revision against an older service refuses the first snapshot
as `staffing_contract_mismatch` (missing field), and that older service refuses
a new write through the domain's closed key rule (`staffing_invalid_request`).
Two builds of this revision line from different checkouts refuse each other as
`staffing_contract_mismatch` on the first read and on every write. In every
case the Console shows the `不可用` notice with the code, disables every
staffing control, and the recovery is to rebuild the export from the running
checkout, restart, and reload (pilot runbook, "Upgrading the service and
console together").

Once `nxt-staffing/v1` is released, its bytes, examples, and this directory are
frozen. A later closed-shape or semantic change requires a new literal and
directory (`nxt-staffing/v2`, `staffing-v2/`), with the Console decoding the
versions it supports as an explicit union and rejecting all others; the
contract revision continues to distinguish builds within a released version.
The durable ledger directory `staffing-v1/`, record `schema_version: 1`, the
anchor schema, and the request literals are ledger and request versions, not
this wire version; a wire revision never renames or rewrites them, and replay
is unaffected.

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

The composition-owned `project_request_to_wire()` in
`scripts/staffing_operations.py` is the sole adapter for this table. It copies only declared fields, maps domain gap `required`/`actual` to
`required_count`/`assigned_count`, adds the literal `COVERAGE_GAP`, and rejects an
impossible tuple instead of manufacturing a fallback record. The domain does not
import HTTP fields or construct Manager API envelopes.
