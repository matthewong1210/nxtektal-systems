# Regional Model Gateway V1

`nxt_model_gateway` is a stateless, provider-neutral HTTPS generation leaf.
It owns no domain semantics or persistence and imports no first-party package.
Only a composition root in `simulation/scripts/` may compose it with a domain
owner. Model results are untrusted transient JSON proposals, never decisions,
commands, physical telemetry, facility truth, or an execution path. This is an
implemented library in the current checkout, not evidence of production deployment.

## Public contracts

The package exports `Provider` (KIMI, OPENAI, ANTHROPIC), `DeploymentRegion`
(CN, GLOBAL), `MessageRole` (system, user, assistant), `GenerationStatus`,
`FailureCode`, `GatewayContractError`, `GenerationMessage`, `ProviderConfig`,
`TokenUsage`, `GenerationRequest`, `GenerationResult`, `AttemptStarted`,
`AttemptRecord`, `AttemptObserver`, `AttemptObserverError`, `PreparedRequest`,
`AdapterOutcome`, `ProviderAdapter`, the three provider adapters, `ModelGateway`,
`RoutePolicy`, `RouteReadiness`, `RouteReadinessStatus`, `canonical_json`,
`stable_digest`, and `decode_validated_json`. The transport module supplies
`HttpTransport`, `HttpResponse`, `ProviderEndpoint`, `TransportFailure`, and
`StdlibHttpsTransport` for injected transport composition.

`ProviderConfig(provider, model_id, api_key)` accepts credentials only from
composition-root injection. The package reads no environment, configuration
files, credential store, wall clock, or random generator. A monotonic callable is
injected into both the gateway and transport; provider/model selection is
explicit configuration, not inferred from request contents.

Requests bind a caller-provided `request_id`, `template_version`, 1–64 messages,
an object-root output schema, `max_output_tokens` (1–16384), and a finite positive
`deadline_budget_s`. A message contains 1–32768 characters. Schemas use local
Draft 2020-12 validation, are bounded to 65536 canonical UTF-8 bytes and depth 20,
and reject `$ref`, `$dynamicRef`, and absolute/URI `$id` values before validation.
No schema retrieval occurs. JSON must be one object with unique keys, finite
numbers and valid Unicode; malformed JSON and schema mismatches are distinct.

Canonical JSON uses sorted keys, compact separators, UTF-8 and no NaN values.
The SHA-256 input digest covers template version, messages, output schema and
maximum output tokens; request ID and time budget do not change semantic identity.
Successful output has a verified digest and recursively immutable detached data.
Failure results contain no output. Ordered attempt metadata retains provider,
model, status, closed failure code, retryability/security flags, safe provider
metadata and input/output digests. Digests identify content, not its truth.

## Fixed endpoints

Production endpoints are private constants; UI, API, CSV and environment inputs
cannot change host, port, path or scheme. No arbitrary URL or base-URL option exists.

| Provider | Exact HTTPS endpoint |
|---|---|
| Kimi (CN) | `https://api.moonshot.cn:443/v1/chat/completions` |
| OpenAI | `https://api.openai.com:443/v1/responses` |
| Anthropic | `https://api.anthropic.com:443/v1/messages` |

Transport uses direct verified HTTPS with hostname verification/SNI, no proxy
discovery and no redirects or automatic retry. Request bodies are limited to
262144 bytes; responses to 524288 bytes. It sends `Connection: close` and
`Accept-Encoding: identity`; non-identity response encoding, excessive bodies,
ambiguous length/framing and invalid chunk framing fail closed. DNS resolution,
connection, TLS and reads share an attempt deadline. The caller's DNS wait is
bounded; a timed-out daemon resolver may outlive the call, but its late result
is discarded and it has no request body or key and cannot connect or send.
`io` is used only to clamp HTTP parser reads, not for file access.

## Provider envelopes

All three adapters prepare canonical bytes without network I/O, then perform
one `send(prepared, timeout_s=...)`. They validate provider envelopes and the
decoded JSON against the caller's local schema. Provider error bodies are never
used as model output or error detail.

| Provider | Request envelope | Accepted answer source |
|---|---|---|
| Kimi | `model`, `messages` (`role`, `content`), `max_tokens`, `response_format.type=json_schema`, `response_format.json_schema={name: structured_output, strict: true, schema: ...}`, `stream=false`; `kimi-k2.6` additionally sends `thinking={type: disabled}` (other model IDs omit it); Bearer authorization | `choices[0].message.content` with `finish_reason=stop`; `content_filter` or a nonempty string `message.refusal` is refused, `null`/absent is not a refusal, and any other refusal value is malformed |
| OpenAI | `model`, `input` (`role`, `content`), `max_output_tokens`, `text.format={type: json_schema, name: structured_output, strict: true, schema: ...}`, `store=false`; Bearer authorization | `status=completed`, exactly one `output_text` in message content; optional message status must be completed; refusal blocks fail; reasoning/tool blocks are not answers and Chat Completions `choices` is rejected |
| Anthropic | `model`, `max_tokens`, `messages`, optional single leading `system`, `tools=[{name: emit_structured_output, description: ..., input_schema: ...}]`, `tool_choice={type: tool, name: emit_structured_output}`; `x-api-key`, `anthropic-version: 2023-06-01` | `stop_reason=tool_use` and exactly one tool-use block named `emit_structured_output` with object `input`; `stop_reason=refusal` is refused; tool input is data and is never executed |

Optional metadata comes from `id` and the accepted finish/status field. Kimi
usage uses `prompt_tokens`, `completion_tokens`, `total_tokens`; OpenAI uses
`input_tokens`, `output_tokens`, `total_tokens`; Anthropic uses `input_tokens`
and `output_tokens` and derives their total. Counts must be nonnegative integers
and totals must match. Malformed envelopes/metadata fail closed.

## Failure mapping

| HTTP result | Failure code |
|---|---|
| 200 | Parse and validate the provider envelope and JSON object |
| 3xx | `REDIRECT_REFUSED` at the transport boundary |
| 400, 409, 422 | `INVALID_PROVIDER_REQUEST` |
| 401 | `AUTHENTICATION_FAILED` |
| 403 | `PERMISSION_DENIED` |
| 404 | `MODEL_NOT_FOUND` |
| 408 | `HTTP_TIMEOUT` |
| 429 | `RATE_LIMITED` |
| 500–599 | `PROVIDER_UNAVAILABLE` |
| Other 4xx | `PROVIDER_CLIENT_ERROR` |
| Other non-200 status | `MALFORMED_PROVIDER_RESPONSE` |

| Status | Failure codes |
|---|---|
| `UNAVAILABLE` | `DNS_FAILURE`, `CONNECT_TIMEOUT`, `CONNECT_FAILED`, `READ_TIMEOUT`, `CONNECTION_INTERRUPTED`, `HTTP_TIMEOUT`, `RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, `DEADLINE_EXHAUSTED`, `BACKUP_UNCONFIGURED` |
| `REFUSED` | `PROVIDER_REFUSED` |
| `INVALID_RESPONSE` | `MALFORMED_PROVIDER_RESPONSE`, `SCHEMA_MISMATCH` |
| `CONFIGURATION_ERROR` | `INPUT_TOO_LARGE`, `AUTHENTICATION_FAILED`, `PERMISSION_DENIED`, `MODEL_NOT_FOUND`, `INVALID_PROVIDER_REQUEST`, `PROVIDER_UNCONFIGURED` |
| `SECURITY_ERROR` | `RESPONSE_TOO_LARGE`, `UNSUPPORTED_CONTENT_ENCODING`, `REDIRECT_REFUSED`, `ENDPOINT_NOT_ALLOWED`, `TLS_VERIFICATION_FAILED` |
| `PROVIDER_ERROR` | `PROVIDER_CLIENT_ERROR` |

Only the eight availability codes enumerated in Regional routes are retryable.
Only the five `SECURITY_ERROR` codes set `security_failure=true`. Exceptions
from observers are separate fail-closed errors, not fallback-eligible results.

## Regional routes

CN uses Kimi only: one attempt, with no cross-region fallback. GLOBAL starts
with OpenAI and permits at most one Anthropic attempt, only after one of:
`DNS_FAILURE`, `CONNECT_TIMEOUT`, `CONNECT_FAILED`, `READ_TIMEOUT`,
`CONNECTION_INTERRUPTED`, `HTTP_TIMEOUT`, `RATE_LIMITED`, `PROVIDER_UNAVAILABLE`.
The gateway tests exact failure codes, never provider prose or the retryable
flag alone. Refusal, schema failure, authentication, invalid input and security
failure do not trigger fallback. There is no same-provider retry or backoff.

Missing primary configuration produces `PROVIDER_UNCONFIGURED` without an
attempt. GLOBAL with OpenAI but no Anthropic is usable with readiness
`DEGRADED_BACKUP_UNCONFIGURED`; if an eligible primary failure needs that missing
backup, the result is `BACKUP_UNCONFIGURED` and retains the primary attempt.
Readiness inspects configuration and makes no network call.

## Deadlines

| Route | Total cap | First attempt cap | Backup cap |
|---|---|---|---|
| CN | 15 seconds | Kimi: 15 seconds | None |
| GLOBAL | 20 seconds | OpenAI: 12 seconds | Anthropic: 8 seconds |

The total deadline begins at `generate()` and is the smaller of the request
budget and route cap. Preparation and observer time count against the total
budget. `AttemptStarted.timeout_s` is the approved upper bound, computed as
the smaller of remaining total time and the per-provider cap. After `started`
returns, the actual send timeout is recalculated and can shrink; it never
exceeds that approved upper bound. An exhausted budget prevents network send
and is recorded as `DEADLINE_EXHAUSTED` if a start was already observed.
An adapter result returning beyond the total deadline becomes
`DEADLINE_EXHAUSTED`. A successful output returning beyond only its attempt
deadline becomes `READ_TIMEOUT`; a terminal non-success returned before the
total deadline preserves its original classification. Synchronous observer
callbacks cannot be forcibly preempted; their elapsed time consumes subsequent
available budget.

## Attempt observer ordering

Every attempt follows preparation, `observer.started(AttemptStarted)`, at most
one send, then `observer.finished(AttemptRecord)`. Start approval must succeed
before sending. A finished callback must succeed before fallback or returning
the completed outcome; callback failure raises sanitized `AttemptObserverError`
with request/index/provider/phase and stops the route. A successful start
followed by deadline exhaustion produces a finished no-send timeout record.
Preflight failures before start produce no attempted network record.

The gateway owns no persistence. A caller may supply an observer that persists
metadata, but the gateway never writes files or claims durable storage. Observer
records do not contain raw requests, responses, credentials or output objects.

## Redaction

Credentials, message content, schemas, request bodies/headers and successful
output objects are excluded from dataclass reprs. Transport and contract errors
use repository-authored bounded detail strings, not user/provider text. Observer
exceptions have no retained original exception context/cause. Only bounded,
validated provider metadata and hashes accompany the closed outcome codes.
Consumers must still treat returned JSON as untrusted; successful schema
validation does not authorize action or establish domain correctness.

## Non-goals

No first-party imports, domain decisions, staffing/planning semantics, state,
telemetry, records, persistence, physical control, command admission or safety
loop exists here. No model call enters Agent Runtime, Site Agent, Edge Task,
simulator or robot/control packages. Only `simulation/scripts/` may compose
the leaf with a domain owner; keys enter through composition-root injection.
There are no provider SDKs, requests/httpx client, environment reads, filesystem
or process operations, random IDs, wall-clock reads, streaming, arbitrary
endpoints or live-API tests. Availability and offline fixture verification do
not prove model capability, model availability in a region, or deployment readiness.

Mechanical isolation and registration are checked by
`tests/model_gateway/test_architecture.py`, including negative controls and
reverse scans of every other `simulation/nxt_*` package. Focused verification:

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/model_gateway
```
