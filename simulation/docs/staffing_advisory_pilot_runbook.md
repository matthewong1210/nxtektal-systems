# Staffing Advisory Pilot Runbook

This runbook is for the local SIMULATION pilot only. The service binds to
loopback and has no authentication. Run it on a controlled operator computer;
do not bind, proxy, tunnel, or publish it to a LAN or the internet.

## Operating boundary

Staffing is an advisory evidence surface. It records roster inputs, operational
exceptions, model suggestions, and manager responses. It does not create an HR
schedule, prove labor compliance, notify employees, create robot tasks, or send
any execution command. A manager must review every proposal and remains
responsible for any separate operational action.

The `operator` field is self-declared attribution, not an authenticated
identity. Site Agent reset controls only Site Agent fixture evidence; it does
not reset staffing evidence.

## Stable evidence directory

Choose one absolute staffing state root on durable local storage. It must be
independent of every `--out` directory. Never place it inside a disposable run,
temporary directory, console export, or synced public folder.

Before first launch:

1. Create the parent directory as the account that will run the pilot.
2. Restrict the directory to that account (for example, directory mode `0700`)
   and use a restrictive process umask such as `077`.
3. Confirm that neither the root nor any existing parent component is a
   symbolic link.
4. Give backup operators access through the host's controlled account and disk
   encryption, not through permissive filesystem modes.

The service derives a site/deployment-specific `staffing-v1` directory below
this root. `--initialize` and a new `--out` value create new continuous run
evidence but preserve this stable staffing ledger.

## Launch modes

API keys are read only from the process environment. Set them through the
host's secret manager before launch. Do not place key values in shell history,
command-line flags, roster files, notes, screenshots, or this runbook.

China route, with an already populated `$MOONSHOT_API_KEY`:

```sh
python -B scripts/course_collection_execution_v4_service.py \
  --out /absolute/path/to/run-evidence \
  --initialize \
  --api-only \
  --staffing-state-root /absolute/path/to/stable-evidence \
  --staffing-region CN \
  --staffing-language zh-CN \
  --kimi-model YOUR_APPROVED_KIMI_MODEL
```

Global route, with the primary `$OPENAI_API_KEY` populated and, if the approved
backup route is wanted, `$ANTHROPIC_API_KEY` populated as well:

```sh
python -B scripts/course_collection_execution_v4_service.py \
  --out /absolute/path/to/run-evidence \
  --initialize \
  --api-only \
  --staffing-state-root /absolute/path/to/stable-evidence \
  --staffing-region GLOBAL \
  --staffing-language en \
  --openai-model YOUR_APPROVED_OPENAI_MODEL \
  --anthropic-model YOUR_APPROVED_ANTHROPIC_MODEL
```

The startup line retains the normal `url`, `disclaimer`, `transport`, and
`scope` fields and reports only `staffing: enabled`, `unavailable`, or
`disabled`. It does not reveal paths, model names, provider readiness, keys,
prompts, or responses.

Omitting `--staffing-state-root` disables staffing completely. Region and model
flags without a state root are accepted but inert; the process does not read
provider environment variables or create staffing storage.

## Initial roster import

Prepare a reviewed JSON request that follows
`docs/contracts/staffing-v1/schema.json`. A synthetic example is available at
`docs/contracts/staffing-v1/examples/roster-import.json`; replace its synthetic
site, deployment, timezone, people, eligibility, availability, assignment, and
coverage data with the approved pilot input. Use a unique `request_id`, set
`expected_roster_revision` to the revision currently shown by `GET
/api/v1/staffing`, and retain the source filename in `source_ref`.

With the service URL from startup and a local request file:

```sh
curl --fail-with-body \
  -H 'Content-Type: application/json' \
  --data-binary @/absolute/path/to/reviewed-roster-request.json \
  http://127.0.0.1:8767/api/v1/staffing/roster-imports
```

Verify the returned operation is `COMMITTED`, then read both the current and
dated projections:

```sh
curl --fail-with-body http://127.0.0.1:8767/api/v1/staffing
curl --fail-with-body http://127.0.0.1:8767/api/v1/staffing/dates/YYYY-MM-DD
```

Do not import real pilot data until local access controls and encrypted backup
handling have been reviewed.

## Manual and degraded operation

Provider configuration is optional. Roster import, exception
entry/correction/cancellation, current and dated reads, and manager evidence
remain available without a usable provider route. If the region or its required
primary model is absent, a generation POST is rejected before reservation with
`staffing_unavailable`; it creates no durable request, so there is no receipt to
recover by request ID. If the region and primary model are configured but the
primary API key is absent, an admitted request can reserve and then terminate as
`CONFIGURATION_ERROR` with `PROVIDER_UNCONFIGURED`. These generation outcomes
do not block local manual operations.

If staffing construction or integrity verification fails, the rest of Site
Agent remains online. Health, planning, task operations, and collection
execution continue; all staffing routes return the same generic
`staffing_unavailable` response. Preserve the stable root and investigate from
an offline copy. Never delete or rewrite the ledger to make startup succeed.

An operator can continue recording exceptions through the local staffing API
without a model. Existing roster and exception revisions remain the evidence
basis for later advisory generation.

## Request recovery and uncertain results

Every write uses a unique request ID. After a timeout, lost response, browser
refresh, or operator handoff, do not guess whether the write committed. Recover
the receipt with:

```text
GET /api/v1/staffing/requests/{operation-kind}/{url-encoded-request-id}
```

Re-sending the exact same request ID and body is an idempotent recovery path;
reusing the ID with different content is a conflict.

`RESULT_UNKNOWN` means the provider outcome cannot be proven from durable local
evidence. Do not silently treat it as success and do not overwrite it. After a
manager decides to retry, create a new request with a new `request_id` and set
`retry_of` to the prior result's `suggestion_id` (the generation ID), not its
`request_id`. Recover and retain both receipts.

## Overlapping exceptions

A second record for a worker whose interval intersects an active exception on
the same service date (for example the same LEAVE submitted twice under a new
request ID, or a LATE inside an existing leave) is refused with HTTP 409
`staffing_exception_overlap`. Nothing is appended and the existing record is
unchanged; the console shows it as an explicit, acknowledged rejection rather
than an unknown result. Re-sending the original request ID with the same body
still returns its original receipt. To change the existing record, cancel or
correct it through its exception ID under a new request ID; to add a different
interval, record one that does not overlap.

## Refused provider answers

When a provider answer passes the portable schema but the local decoder
refuses it, the generation ends as `INVALID_RESPONSE` with one of the closed
codes `invalid_provider_shape`, `provider_sensitive_key`,
`invalid_provider_timestamp`, or `invalid_candidate_set`; a gateway-level
`SCHEMA_MISMATCH` or `MALFORMED_PROVIDER_RESPONSE` ends the same way. The
service also appends one `staffing_generation_invalid_response` event to the
Site Agent diagnostics stream at
`<--out>/site-agent/<site_id>/<deployment_id>/service/service_events.jsonl`.
The event carries the generation, request, and operation identifiers, route
region, provider, model, template version, input and output digests, the
closed failure code, the closed field token that tripped (`rationale`,
`operational_warnings`, `candidate indexes`, `operations`, `operation`,
`role_code`, `timestamp`, ...), bounded counts (candidates, operations,
rationale length, warning count and length), and which text fields carried
control characters. It never contains provider text, aliases, names, notes,
keys, or prompts. Use it to tell a too-long or multi-line rationale from a bad
index or operation before retrying with a new request ID. The stream is
best-effort visibility; the ledger terminal is the record.

## Upgrading to prompt template v2

Current generations reserve prompt template `staffing-adjustment/v2`, which
states every local decoder bound to the provider. Ledgers written under
`staffing-adjustment/v1` replay unchanged: stop the service, keep the same
stable root, start the new build, and read the current projection before new
writes. Any v1 reservation that had not reached a terminal is recovered as
`RESULT_UNKNOWN` and is never resent; retry it with a new request ID and
`retry_of`, which reserves under v2. Nothing in the ledger is rewritten.

## Shutdown and restart

Stop the process with SIGINT or SIGTERM and wait for it to exit. Shutdown stops
the loopback server first, closes staffing and its bounded generation worker,
then closes the continuous runtime and Site Agent. If a provider call is still
settling, exit can wait while the bounded worker reaches a terminal state and
the ledger closes. Do not kill the process, power off the host, rotate evidence,
or start a second process against the same root during that interval.

After restart, use the same stable root and read the current projection before
new writes. Interrupted generation evidence is recovered locally; it is not
blindly resent to a provider.

## Backup, retention, and privacy

Back up staffing only while the service is fully stopped. Treat the entire
site/deployment `staffing-v1` directory as one unit: the JSONL ledger and anchor
must be copied, restored, retained, and deleted together. Use an encrypted
backup destination with access logging and a documented retention/deletion
period. Test restore into a separate controlled path; never edit records or the
anchor.

Provider wire data is minimized and pseudonymized, but it is not anonymous and
is not certified non-reidentifiable. Role, area, time, availability, and
exception patterns are quasi-identifiers. The protected local ledger, local
API/UI, and every backup retain real identity and free text and therefore
require access control and encrypted handling.

API keys, authorization headers, raw provider bodies, and hidden reasoning never
enter the ledger, public API, logs, or browser surfaces. Raw nonce values are
used only transiently inside the local process; they never enter provider
transport, the ledger, public API, logs, or browser surfaces. A nonce digest is
retained only in the protected local ledger for replay integrity; it never
enters provider transport, the public API, logs, or browser surfaces. Operators
must still never place secrets in display names, notes, source references, or
`operator` fields.

Selecting `CN` and Kimi controls provider routing only. It is not a claim of
data residency, regulatory compliance, or legal approval. Confirm provider,
contract, retention, cross-border, labor, and privacy requirements separately
before any real deployment.
