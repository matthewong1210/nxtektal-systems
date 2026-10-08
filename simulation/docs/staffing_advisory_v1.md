# Staffing advisory domain v1

Status: normative contract for the staffing domain library in this current, unmerged checkout. Executable contracts and boundary tests remain authoritative when this document and code disagree.

## Current implementation boundary

The implemented surface is `nxt_pilot_ops.staffing`, a deterministic Shadow Ops evidence owner. It
normalizes weekly roster evidence and daily exceptions, builds privacy-minimized generation
inputs, validates candidate patches, records manager responses, derives local advisory plans, and
replays a protected local ledger.

On this branch the SIMULATION-only composition exists around the library:
`simulation/scripts/staffing_operations.py` (bounded generation worker, HTTP
callback, provider wire through `nxt_model_gateway`), the optional Site Agent
staffing routes, and the Manager Console staffing panel. There is still no
venue deployment, HR write, notification, formal schedule, or robot action.
Those absences are product facts, not merely future test work.

The package imports no model gateway, Site Agent, Edge, Agent Runtime, facility, simulator, provider SDK, network client, robot, or control package.
A future composition root may connect plain data to another package without changing this ownership rule.

`StaffingOperations` is intentionally imported from `nxt_pilot_ops.staffing.operations`; the `nxt_pilot_ops.staffing` package root stays narrow and does not re-export it.
Staffing is a subpackage of the existing `nxt_pilot_ops` wheel package, not a separate wheel root.

## Truth and authority

Operator-supplied roster and exception records are local operating evidence, not authoritative HR,
payroll, attendance, identity, or access-control records; this library synchronizes none of them.

The `operator` string on roster, exception, generation, and manager records is attribution only, not an authenticated identity, authorization decision, signature, or proof of authorship.

Provider output is an untrusted proposal; schema-valid output does not establish a staffing fact.
Every candidate is decoded through a closed shape, resolved against a frozen basis, and validated locally before display.

`ACCEPT` and `MODIFY` persist a complete local advisory plan. They do not create a formal schedule,
notify workers, update HR, authorize access, issue a task, or
prove that work happened. `REJECT` persists no plan.

The ledger is evidence of what this library accepted and replayed. It is not an external attestation, an identity system, or physical-world execution truth.

## Closed roster import

The exact top-level request shape is `schema, request_id, expected_roster_revision, site_id,
deployment_id, site_timezone, effective_from_local_date, effective_until_local_date, operator,
source_ref, workers, availability, assignment_rules, regular_assignments, coverage`.

`schema` is exactly `nxt-staffing-roster-import/v1`. Unknown, missing, or
mistyped fields fail closed. `site_id`, `deployment_id`, and `site_timezone`
must equal the owner configuration. Dates use `YYYY-MM-DD`; the inclusive end
date may be null. `expected_roster_revision` is a nonnegative integer.

Each worker has exactly `staff_id, display_name, skill_codes, eligibility[{role_code, area_code}],
max_daily_minutes`. Each availability row has exactly `staff_id, weekday, start_local, end_local`.
Each regular assignment has exactly `staff_id, weekday, role_code, area_code, start_local,
end_local`. Each assignment rule has exactly `role_code, area_code, required_skill_codes`. Each
coverage requirement has exactly `weekday, role_code, area_code, start_local, end_local,
minimum_staff`.

Weekdays are integers `0..6`; local minutes are exact `HH:MM`; codes use the
closed uppercase code syntax. Worker IDs and labels remain local evidence.
Workers and coverage are nonempty. Arrays, skills, eligibility pairs, and rules
must be duplicate-free: repetition rejects rather than being deduplicated, and
accepted values are sorted canonically before digesting. Each worker has at most
one regular assignment per weekday. Cross-references, eligibility, required
skills, availability, non-overlap, daily limits, and coverage-rule references
are validated before a revision exists.

The semantic `roster_digest` excludes request ID, expected revision, operator,
and source reference. Those are audit/CAS fields, not roster content.

## Revisions and service-day selection

A successful roster import creates revision `expected_roster_revision + 1`, but
the comparison is repeated against the latest verified history while holding
the ledger lock. Revisions are contiguous.

For a service date, all revisions whose inclusive effective range contains the
date are considered. The highest revision wins. If none applies, selection fails
with `staffing_roster_not_found`; ranges never silently backfill a date.

Exception-set revisions and effective-plan revisions are scoped to a service date. A generation
basis freezes `roster_revision, exception_set_revision, effective_plan_revision` and corresponding
semantic digests. Reservation and manager response both compare their expected vector under the
append lock. A manager response also
becomes stale after any later roster import, even when the old effective range
would otherwise still select the same service-day roster.

An accepted plan remains `CURRENT` when a newly recorded exception is merely
overlaid after acceptance. Cancelling or correcting an exception that was in the
accepted basis makes the plan `REVIEW_REQUIRED`. Replacing the applicable roster
makes the plan `NO_PLAN`; with no accepted or modified plan it is also `NO_PLAN`.

## Time and DST rules

The roster declares one IANA `site_timezone`. A local minute is resolved only
when round-tripping it through that zone identifies exactly one UTC instant.
Nonexistent spring-forward minutes and ambiguous fall-back minutes therefore
fail closed for roster materialization and exception normalization.

Materialized assignments and exceptions are timezone-aware instants at whole
minute precision. Intervals are half-open and require `end > start`. A provider
ADD must include an explicit offset; its wall time and offset must agree with
the site zone and both endpoints must remain on the requested service date.

Provider ADD offset minutes are persisted beside each operation. Replay restores
the recorded fixed offset before revalidation; it does not guess an offset from
the current timezone database. This preserves both folds of a valid ambiguous
fall-back minute and preserves an `OFFSET_TIMEZONE_MISMATCH` rejection.

Audit timestamps are injected, aware values and are normalized to canonical UTC. The domain reads no wall clock and creates no random or UUID identifiers.

## Daily exception requests

Record has schema `nxt-staffing-exception/v1` and exactly `schema, request_id,
expected_roster_revision, expected_exception_set_revision, service_date,
staff_id, kind, time_local, operator, note`.

Kinds `LEAVE` and `UNAVAILABLE` cover the whole materialized shift and require
`time_local: null`. `LATE` covers shift start through `time_local`.
`EARLY_DEPARTURE` covers `time_local` through shift end. Point times must be
inside the shift and resolve unambiguously in the site zone.

Cancel has schema `nxt-staffing-exception-cancel/v1` and exactly `schema,
request_id, exception_id, expected_exception_set_revision, operator, note`.
Correction has schema `nxt-staffing-exception-correct/v1` and exactly `schema,
request_id, exception_id, expected_exception_set_revision, operator,
replacement{kind, time_local, note}`.

An exception identity is deterministically derived from its record request ID. Correction preserves
that identity while storing the previous and full
replacement exception in one event. Cancellation stores the cancelled value;
neither operation deletes history. Active exceptions are replay-derived,
date-scoped, canonically ordered, and non-overlapping per worker.

A new record, or a correction, whose half-open interval would intersect an
active exception of the same worker on the same service date is refused under
the append lock with the closed `OVERLAPPING_EXCEPTION` conflict. It is a
business conflict, not evidence corruption: nothing is appended, the existing
record is untouched, the same request ID with the same body still replays its
original receipt, and recovery is a cancel or correction of the existing
record (or a non-overlapping interval) under a new request ID. Adjacent
half-open intervals and a correction that shrinks or moves its own record do
not conflict. Replay keeps its own non-overlap validation as defense in depth.
The composition root maps the conflict to HTTP 409 `staffing_exception_overlap`.

## Generation reservation and provider wire

The generation schema is `nxt-staffing-suggestion-generate/v1`; its exact
remaining fields are `request_id, operator, service_date,
expected_revisions{roster, exception_set, effective_plan}, retry_of`.

Reservation freezes the full local basis, a minimized provider payload, alias
maps, an alias-nonce digest, input digest, prompt version, language, route
evidence, and optional retry relation before any future outbound work.

The current prompt template is `staffing-adjustment/v2`; `staffing-adjustment/v1`
remains a supported template for replay only. A reservation freezes its
template version, and every later reading (replay, attempt binding, result
commit, manager basis comparison) rebuilds that reservation's basis, provider
payload, canonical input, and digest under the frozen version, so a ledger
written under v1 replays unchanged after the bump and the composition root
sends a v1 reservation exactly its v1 input. New reservations and projections
are created only under the current version; requesting a superseded version
fails closed. The two versions share the byte-identical portable output
schema; v2 only extends the fixed system text so the provider is told the
local decoder's count, index, operation, lexical, and text-length bounds that
the provider-common schema subset cannot express. No decoder bound is relaxed.

Only the provider wire is pseudonymous. A caller injects a fresh byte nonce of
at least 16 bytes; production composition is responsible for stronger nonce
generation. Domain-separated HMAC aliases have forms `worker_<24 hex>` and
`assignment_<24 hex>`. The nonce value is never persisted; its digest is retained
locally to prevent reuse and support replay integrity.

The provider wire contains exactly the service date, site timezone, aliased
workers, assignment rules, aliased assignments, aliased availability,
unavailable intervals, coverage, prompt template version, and language. It
contains no staff ID, assignment ID, display name, note, source reference,
local semantic digest, alias map, nonce material, credential, or raw prompt field.

The protected local ledger is not pseudonymous storage. It retains local IDs,
display names, notes, source references, alias maps, nonce digest, the complete
basis, and minimized provider-projection evidence needed for replay.

Public local projections expose only their declared local identifiers, names,
notes, and source references. They omit alias maps, nonce bytes/digests, private
provider payloads, credentials, raw provider responses, and hidden reasoning.

## Closed candidate patch

Provider output is one exact object: `candidates[{candidate_index, operations,
rationale, operational_warnings}]`.

There are at most two candidates, ordered contiguously as `[]`, `[1]`, or
`[1, 2]`. Each candidate has at most 32 operations, rationale at most 280
characters, and at most five warnings of at most 200 characters each.
Rationale and warnings are single-line text: any Unicode control character
(category C, which includes line breaks and tabs) rejects the whole answer as
`invalid_provider_shape`.

A provider remove is exactly `{operation: REMOVE, assignment_alias}`. A
provider add is exactly `{operation: ADD, worker_alias, role_code, area_code,
start_at, end_at}`.

Operation spelling is normalized to canonical uppercase after closed-branch
parsing. Aliases must exist in the reserved maps; an assignment may be removed
at most once. Timestamps must be RFC3339 values with an explicit offset.

Manager `MODIFY` uses the same semantic patch but local identifiers:
`REMOVE{operation, assignment_id}` or `ADD{operation, staff_id, role_code,
area_code, start_at, end_at}`.

Its timestamps are aware RFC3339 whole minutes. `ACCEPT` selects one stored
candidate; `MODIFY` selects an existing candidate index and supplies 1..32 local
operations; `REJECT` selects none. Every accepted or modified patch is
revalidated against the frozen/current matching basis before persistence.

## Candidate validation

The closed rejection-code set is exactly:

1. `UNKNOWN_ROLE_AREA`
2. `INELIGIBLE_ROLE_AREA`
3. `MISSING_REQUIRED_SKILL`
4. `INVALID_INTERVAL`
5. `INVALID_MINUTE_PRECISION`
6. `OFFSET_TIMEZONE_MISMATCH`
7. `OUTSIDE_SERVICE_DATE`
8. `OUTSIDE_AVAILABILITY`
9. `OVERLAPS_EXCEPTION`
10. `OVERLAPPING_ASSIGNMENTS`
11. `MAX_DAILY_MINUTES_EXCEEDED`
12. `COVERAGE_GAP`
13. `PROMPT_TEMPLATE_VERSION_MISMATCH`

Structural errors such as unknown aliases, duplicate removes, non-contiguous
candidate indexes, excessive counts, or malformed provider trees reject the
candidate set rather than inventing a business rejection code.

REMOVE operations apply to the baseline first. ADD operations are checked in
order against the remaining and already accepted assignments. A candidate with
any ADD rejection has no materialized schedule. If all ADDs pass, the complete
canonical schedule is built and then coverage is evaluated.

Coverage is segmented independently for every role/area requirement. Its start
and end plus every intersecting assignment, exception, and coverage boundary
become sorted UTC cut points. For each adjacent nonempty segment, `actual` is
the number of distinct matching staff whose assignment covers the whole
segment. `actual < minimum_staff` produces a canonical `CoverageGap`. Any gaps
make `COVERAGE_GAP` the sole rejection code and suppress the schedule/digest.

A valid candidate has empty rejection codes and gaps plus a complete canonical
schedule and matching schedule digest. An invalid candidate has nonempty closed
rejection codes and no schedule/digest. Persisted candidates are reconstructed
and revalidated during replay; recomputing outer hashes alone cannot legitimize
incoherent candidate evidence.

## Manager review: local explanations and the action window

`nxt_pilot_ops.staffing.review` derives two clock-free readings of every
stored candidate at projection time; neither is persisted and neither changes
replay.

The provider sees only aliases, so its `rationale` and `operational_warnings`
may name `worker_<24 hex>` or `assignment_<24 hex>` tokens. The public
`CandidateProjection` keeps that raw text and adds `rationale_local` and
`operational_warnings_local`, in which each token the reservation's alias maps
bind is replaced by the worker's local display name or by the shift label
`name (ROLE/AREA HH:MM–HH:MM)` rendered in the site timezone. A token the
reservation does not know is left as written rather than inventing a person.
The replacement is a local projection over ledger evidence: no display name,
staff ID, or assignment ID is added to the provider wire, and the strict
provider decoder is unchanged.

`action_window_end` is the latest `end_at` of any shift the candidate adds or
removes (a REMOVE whose alias no longer resolves contributes nothing); a
candidate with no timed operation stays open until local midnight after its
service date in the site timezone. The domain reads no clock: the composition
root compares the window with its audit clock to label the candidate
`CURRENT` or `EXPIRED` on the wire, and `commit_manager_response` compares the
window of the operations an ACCEPT (the stored candidate) or MODIFY (the
edited operations) would persist against the injected `recorded_at`. When that
instant has reached the window end the response is refused under the append
lock with the closed `EXPIRED_SUGGESTION` conflict: nothing is appended, the
suggestion keeps its candidates, and REJECT remains available as a recorded
decision. Replay never re-applies the rule, so a ledger whose response was
committed inside its window keeps replaying unchanged. The composition root
maps the conflict to HTTP 409 `staffing_suggestion_expired`.

## Events and state machine

The canonical event envelope is exactly `event_type, event_id, sequence,
site_id, deployment_id, occurred_at_utc, causation_id, payload`.

Event types are `roster_imported`; `exception_recorded`, `exception_cancelled`,
`exception_corrected`; `generation_reserved`, `generation_interrupted`;
`provider_attempt_started`, `provider_attempt_finished`; `suggestion_issued`,
`suggestion_unavailable`; and `manager_response_committed`.

Events have contiguous sequence numbers, stable content-derived identities,
one site/deployment identity, canonical timestamps, closed payload types, and
validated causation. Replay applies every transition to every prefix; validating
only the final record is insufficient.

One business transaction is one composite, self-contained canonical event.
Roster import stores the complete normalized roster. Exception correction stores
old and replacement evidence. Suggestion issuance stores all candidate evidence.
Manager ACCEPT/MODIFY stores the response, complete effective schedule, digest,
and next plan revision. A restart needs no source fixture to rebuild these facts.

A generation begins at `generation_reserved`, which may have no terminal and is
then unfinished. Attempts are contiguous, route-bound, and limited by the
reserved CN or GLOBAL route; legal `IN_PROGRESS` state may contain one unmatched
start. A generation has at most one terminal: `suggestion_issued`,
`suggestion_unavailable`, or `generation_interrupted`. Suggestion terminals
require every attempt start to be paired with a finish; interruption is the only
terminal allowed with an unmatched start. No further attempt or generation terminal may follow a terminal.

`manager_response_committed` requires one earlier `suggestion_issued` and at
most one response. It is not a generation terminal and cannot follow unavailable
or interrupted output. Recovery treats any reservation without a generation
terminal as unfinished, regardless of unrelated records.

An interrupted generation records `RESULT_UNKNOWN`. The old generation is never
resent. A new request ID may reserve once with `retry_of` pointing to that
interrupted generation; only one retry child is allowed.

## Idempotency and concurrency ordering

The idempotency key is `(site_id, deployment_id, operation_kind, request_id)`.

Operation kinds are `roster-import`, `exception-record`, `exception-cancel`, `exception-correct`,
`suggestion-generate`, and `manager-response`. Manager response digest additionally binds its
`suggestion_id`; other request digests
bind the exact detached request body.

Each mutation safely detaches a bounded request tree and derives its key and
digest before asking for the exclusive thread/file lock. Ingress may normalize
closed syntax, but it does not decide mutable business state. Under the lock the
complete persisted state is verified, then same key plus same digest returns the
original receipt and same key plus different digest returns
`IDEMPOTENCY_CONFLICT`. Only a new key reaches full business validation,
revision/lifecycle CAS checks, and event construction. Thus a committed retry
cannot later become stale.

Revision mismatches map to `STALE_REQUEST`; manager basis drift maps to
`STALE_SUGGESTION`; invalid lifecycle maps to `INVALID_TRANSITION`; an
exception record or correction that would overlap an active exception maps to
`OVERLAPPING_EXCEPTION`; an ACCEPT or MODIFY whose shifts have all ended at the
injected audit instant maps to `EXPIRED_SUGGESTION`. No conflict or failed
builder mutates the ledger. The narrow generation `probe_request`
supports duplicate-first queue admission, but reservation rechecks under lock
and the probe creates no TOCTOU authority.

## Ledger, anchor, recovery, and permissions

`staffing.StaffingLedger` owns canonical JSONL records linked by
`previous_hash`/`record_hash`. Its constructor pins the root, checks existing
component types and permissions, and opens the lock; it does not replay the full
ledger. Read, verify, probe, and append operations verify record framing,
canonical bytes, exact keys, identity, sequence, event ID, hash chain, semantic
replay, and the high-water anchor before returning or mutating state.

The co-located `staffing.anchor.json` stores schema, site/deployment identity,
record count, and anchored head hash. It is fsynced independently after the
ledger append and directory fsync. It detects rollback or rewrite at or before
the anchored prefix. It may legitimately lag a complete fsynced ledger suffix
after a crash.

A lagging anchor is accepted only when it matches an exact verified prefix. It
advances on a later successful append. Reads, verification, duplicate receipt
returns, conflict returns, and refused builders do not repair or advance it.

The anchor is local, co-located, and not an external or nonrepudiable witness.
It cannot detect coordinated replacement or deletion of both ledger and anchor.
An empty missing pair is indistinguishable from a fresh store. External anchoring
would be a separate deployment responsibility.

This differs from the legacy `nxt_pilot_ops.ledger.JsonlEventLedger`, which is
not externally anchored. Neither ledger should be described as providing
external nonrepudiation.

The staffing store is POSIX-only: the root is mode `0700`; ledger, anchor,
temporary anchor, and lock files are mode `0600`; path components and files must
be expected non-symlink types. A shared normalized-path thread lock plus POSIX
`fcntl` lock serializes instances. Unsupported platforms and any JSON, chain,
anchor, permission, symlink, I/O, or semantic replay fault fail closed as
`StaffingLedgerIntegrityError` without echoing private payload data.

## Privacy and execution prohibitions

The protocol has no credential or authorization-header field, and forbidden key
names fail closed. It does not semantically recognize every secret value inside
free text, so callers must never place secrets in `operator`, `note`,
`display_name`, or `source_ref`. Raw provider responses, raw `reasoning`, hidden
chain-of-thought, nonce values, and execution interfaces must not enter ledger
records or public projections. Bounded candidate `rationale` and
`operational_warnings` are approved proposal fields, not hidden reasoning.

`diagnose_provider_output()` summarizes one undecodable provider value as a
closed failure code, a closed field token (for example `rationale`,
`operational_warnings`, `candidate indexes`), bounded integer counts, and the
names of text fields that carried control characters. It retains no provider
text, alias, code, timestamp, identity, note, or prompt, so a composition root
may route it to noncanonical service diagnostics. It never enters the ledger
or the public API, and it does not change the terminal record.

The strings `KIMI`, `OPENAI`, and `ANTHROPIC` are legal closed provenance in
attempt evidence. They do not authorize importing provider clients or making a
network call from this package. Credential injection and HTTPS routing belong
outside the domain.

The domain exposes no command, robot, actuator, e-stop, execution, process, network, hidden-clock,
UUID, or random surface. A local advisory plan cannot be
translated into physical action by implication.

## Current verification record

Historical counts in other documents describe their named baselines only. After the
overlapping-exception conflict, prompt template v2, and provider-output diagnostics change
(2026-10-07, all-extras locked environment), the staffing/guard files under `tests/pilot_ops`
passed 793 tests, all `tests/pilot_ops` passed 1463 tests, the full Python
suite reported 5067 passed and 11 skipped (the mosquitto-dependent Edge Task integration cases), `scripts/validate_configs.py` reported no errors, `uv lock --check`
was current, the distribution built and passed inspection, and the console typecheck, lint,
837 vitest tests, and production build passed. The V3 and V4 witnesses were regenerated through
their authoritative flows because the engine fingerprint hashes every `nxt_*` source file; only
derived identities and digests moved. The `staffing-adjustment/v1` ledger fixture under
`tests/pilot_ops/fixtures/staffing_v1_ledger/` was written by the unmodified v1 code and must
keep replaying.

After the manager-review change (local explanations, the candidate action window, and the
`EXPIRED_SUGGESTION` admission conflict; 2026-10-08, all-extras locked environment), the
staffing, guard, API, composition, and architecture files (`tests/pilot_ops/test_staffing_*.py`,
`tests/pilot_ops/test_boundaries.py`, `tests/site_agent/test_staffing_*.py`,
`tests/site_agent/test_api.py`, `tests/site_agent/test_architecture.py`) passed 1153 tests,
`scripts/validate_configs.py` reported no errors, `uv lock --check` was current, and the console
typecheck, lint, 851 vitest tests, static build, loopback smoke, and `npm audit --omit=dev` passed.
The V3 and V4 witnesses were regenerated through their authoritative flows (22 regenerate-mode
passes, then 29 read-only passes and the console's two witness consumers) because the engine
fingerprint hashes every `nxt_*` source file; a structural comparison showed only identity and
digest leaves moved (80 leaves in the two-task witness, 42 in the normal-loop witness). With the
regenerated witnesses in place the full Python suite reported 5080 passed and 11 skipped (the
mosquitto-dependent Edge Task integration cases). The v1 ledger fixture replays unchanged.
