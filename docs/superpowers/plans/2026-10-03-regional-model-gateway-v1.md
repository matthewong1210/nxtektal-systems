# Regional Model Gateway V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a provider-neutral, region-routed model gateway that calls Kimi for `CN`, calls OpenAI with one narrowly allowed Anthropic fallback for `GLOBAL`, and returns schema-validated JSON plus redacted provenance without owning staffing or execution semantics.

**Architecture:** Introduce `nxt_model_gateway` as a first-party-independent network leaf. It owns strict generation contracts, a repository-controlled HTTPS transport, three protocol adapters, fixed regional routing, deadline/fallback classification, readiness, and attempt callbacks. It imports no other `nxt_*` package, reads no environment variables, persists nothing, and is composed with staffing only under `simulation/scripts/` in the later integration plan.

**Tech Stack:** Python 3.11+, frozen dataclasses, stdlib `http.client`/`ssl`/`socket`, injected monotonic clock and fake connections, `jsonschema` Draft 2020-12 validation, pytest, Hatch/uv.

**Spec:** `docs/superpowers/specs/2026-10-03-regional-ai-staffing-advisory-gateway-v1-design.md`

## Global Constraints

- Start implementation from the plan-delivery commit named in the handoff for this file; verify `86adac433ee746abb09f5e80b45bc663d6775448` is an ancestor and `907a5a1de521968bbd899e535eba1e70f32eb84d` remains the stacked product baseline.
- Keep the production endpoints private and exact: `api.moonshot.ai:443/v1/chat/completions`, `api.openai.com:443/v1/responses`, and `api.anthropic.com:443/v1/messages`. No production constructor, environment variable, CSV field, API field, or UI control may override scheme, host, port, or path.
- Do not add provider SDKs, `requests`, `httpx`, proxy support, redirects, streaming generation, hidden retries, web search, MCP, file/code/browser tools, or provider-selected tools. Add only a direct `jsonschema>=4.26,<5` dependency.
- The gateway imports no `nxt_*` package and contains no staffing, FacilityState, Planning, Edge, simulator, robot, actuator, ROS, safety, persistence, filesystem, secret-loading, or wall-clock semantics. A small daemon resolver thread may perform DNS only; it receives no request body/key and must check cancellation before any socket is opened.
- API keys and model IDs are injected through `ProviderConfig`; API keys must never appear in `repr`, results, exception details, diagnostics, fixtures, logs, snapshots, or git diffs. Model IDs have no implicit `latest` default.
- The semantic input digest includes template version, messages, output schema, and max output tokens; it excludes request ID, deadline, latency, API keys, and transport diagnostics. The validated output digest includes only canonical decoded JSON. Output schemas are local, canonical objects capped at 64 KiB/depth 20 and may not contain `$ref`, `$dynamicRef`, or a remote resource identifier, preventing schema validation from becoming another network path.
- Apply `effective_deadline = min(request.deadline_budget_s, 15)` for `CN` and `min(request.deadline_budget_s, 20)` for `GLOBAL`. OpenAI is capped at 12 seconds; Anthropic is capped at the positive remaining budget and 8 seconds.
- `CN` never leaves Kimi. `GLOBAL` falls back only after DNS/connect/read timeout, connection interruption, HTTP 408/429/5xx. Refusal, invalid JSON/schema, TLS/redirect/endpoint/compression/size failures, any other 4xx including 409/422, and downstream staffing validity never trigger fallback.
- Before each network call, the gateway must invoke an injected attempt observer. If the observer cannot durably record `started`, no network call occurs. If it cannot record `finished`, routing aborts without fallback so the later staffing recovery can report `RESULT_UNKNOWN` instead of risking a resend.
- Provider tests use frozen fake transports/connections only. Required verification must never call a paid or live model endpoint.
- Use failing tests first, `apply_patch` for edits, small conventional commits, and local branches only. Do not push, merge, or create a PR.

## Review Focus

- Prove direct TLS with hostname verification, ignored environment proxies, no redirect following, `Accept-Encoding: identity`, 256 KiB outbound and 512 KiB inbound hard limits, and redacted failures.
- Prove exact provider envelope differences: Kimi Chat Completions, OpenAI Responses, and Anthropic Messages/tool-use encoding. Never parse OpenAI Responses as `choices` or pretend Anthropic is OpenAI-compatible.
- Prove the complete routing matrix, exact attempt counts `0..2`, post-call late-response rejection, no hidden adapter retry, missing-backup degradation, and no fallback for non-availability failures.
- Prove the attempt observer order around the transport call and that observer failure prevents resend/fallback.
- Prove architecture isolation mechanically, including reverse-dependency guards and isolated-wheel import without simulator, robot, or provider SDK stacks.

---

### Task 1: Freeze provider-neutral contracts, failure codes, and canonical JSON

**Files:**
- Create: `simulation/nxt_model_gateway/__init__.py`
- Create: `simulation/nxt_model_gateway/contracts.py`
- Create: `simulation/nxt_model_gateway/serialization.py`
- Create: `simulation/tests/model_gateway/__init__.py`
- Create: `simulation/tests/model_gateway/test_contracts.py`

**Interfaces:**
- Consumes: caller-supplied request ID, fixed prompt messages, JSON Schema, max output token count, deadline budget, provider config, and injected observer.
- Produces: strict `GenerationRequest`, `GenerationResult`, ordered `AttemptRecord` values, stable failure codes, canonical digests, and observer events with no domain meaning.

- [ ] **Step 1: Add failing contract tests for exact enums, validation, redaction, and digests**

Pin these public enums and dataclasses in `test_contracts.py`:

```python
def request(
    *,
    request_id: str = "req-001",
    deadline_budget_s: float = 20.0,
    output_schema: Mapping[str, object] | None = None,
) -> GenerationRequest:
    schema = output_schema or {
        "type": "object",
        "additionalProperties": False,
        "required": ["x"],
        "properties": {"x": {"type": "integer"}},
    }
    return GenerationRequest(
        request_id=request_id,
        template_version="template-v1",
        messages=(GenerationMessage(MessageRole.USER, "return an object"),),
        output_schema=schema,
        max_output_tokens=256,
        deadline_budget_s=deadline_budget_s,
    )

EXPECTED_FAILURE_CODES = {
    "INPUT_TOO_LARGE", "RESPONSE_TOO_LARGE", "UNSUPPORTED_CONTENT_ENCODING",
    "REDIRECT_REFUSED", "ENDPOINT_NOT_ALLOWED", "TLS_VERIFICATION_FAILED",
    "DNS_FAILURE", "CONNECT_TIMEOUT", "CONNECT_FAILED", "READ_TIMEOUT",
    "CONNECTION_INTERRUPTED", "HTTP_TIMEOUT", "RATE_LIMITED",
    "PROVIDER_UNAVAILABLE", "AUTHENTICATION_FAILED", "PERMISSION_DENIED",
    "MODEL_NOT_FOUND", "INVALID_PROVIDER_REQUEST", "PROVIDER_CLIENT_ERROR",
    "PROVIDER_REFUSED", "MALFORMED_PROVIDER_RESPONSE", "SCHEMA_MISMATCH",
    "BACKUP_UNCONFIGURED", "DEADLINE_EXHAUSTED", "PROVIDER_UNCONFIGURED",
}

def test_generation_request_digest_excludes_operational_identity_and_deadline():
    first = request(request_id="req-a", deadline_budget_s=20.0)
    second = request(request_id="req-b", deadline_budget_s=7.0)
    assert first.canonical_input_digest == second.canonical_input_digest

def test_provider_config_never_reveals_api_key():
    config = ProviderConfig(Provider.OPENAI, "gpt-pinned", "super-secret")
    assert "super-secret" not in repr(config)

def test_generation_message_never_reveals_prompt_content():
    message = GenerationMessage(MessageRole.USER, "sensitive prompt")
    assert "sensitive prompt" not in repr(message)

def test_output_decoder_rejects_duplicate_keys_nan_non_object_and_schema_drift():
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[1]', b'{"x":"wrong"}'):
        with pytest.raises(GatewayContractError):
            decode_validated_json(raw, schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["x"],
                "properties": {"x": {"type": "integer"}},
            })

def test_generation_request_rejects_remote_or_dynamic_schema_references():
    for schema in ({"$ref": "https://example.invalid/schema"},
                   {"$dynamicRef": "#node"}):
        with pytest.raises(GatewayContractError):
            request(output_schema=schema)
```

- [ ] **Step 2: Run the contract test and verify RED**

Run from `simulation/`:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/model_gateway/test_contracts.py
```

Expected: import failure because `nxt_model_gateway` does not exist.

- [ ] **Step 3: Implement the exact enums and contract error**

Define in `contracts.py`:

```python
class Provider(StrEnum):
    KIMI = "KIMI"
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"

class DeploymentRegion(StrEnum):
    CN = "CN"
    GLOBAL = "GLOBAL"

class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

class GenerationStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    UNAVAILABLE = "UNAVAILABLE"
    REFUSED = "REFUSED"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    SECURITY_ERROR = "SECURITY_ERROR"

class FailureCode(StrEnum):
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    UNSUPPORTED_CONTENT_ENCODING = "UNSUPPORTED_CONTENT_ENCODING"
    REDIRECT_REFUSED = "REDIRECT_REFUSED"
    ENDPOINT_NOT_ALLOWED = "ENDPOINT_NOT_ALLOWED"
    TLS_VERIFICATION_FAILED = "TLS_VERIFICATION_FAILED"
    DNS_FAILURE = "DNS_FAILURE"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    CONNECT_FAILED = "CONNECT_FAILED"
    READ_TIMEOUT = "READ_TIMEOUT"
    CONNECTION_INTERRUPTED = "CONNECTION_INTERRUPTED"
    HTTP_TIMEOUT = "HTTP_TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    INVALID_PROVIDER_REQUEST = "INVALID_PROVIDER_REQUEST"
    PROVIDER_CLIENT_ERROR = "PROVIDER_CLIENT_ERROR"
    PROVIDER_REFUSED = "PROVIDER_REFUSED"
    MALFORMED_PROVIDER_RESPONSE = "MALFORMED_PROVIDER_RESPONSE"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    BACKUP_UNCONFIGURED = "BACKUP_UNCONFIGURED"
    DEADLINE_EXHAUSTED = "DEADLINE_EXHAUSTED"
    PROVIDER_UNCONFIGURED = "PROVIDER_UNCONFIGURED"

@dataclass(frozen=True, slots=True)
class GatewayContractError(ValueError):
    code: FailureCode
    detail: str

    def __str__(self) -> str:
        return f"{self.code.value}: {self.detail}"
```

`GatewayContractError.__post_init__` requires a `FailureCode` member and a nonempty constant `detail` no longer than 160 characters; its callers may select only repository-authored detail strings, and its `repr` must not include caller input.

- [ ] **Step 4: Add failing field-shape and immutability assertions**

Append assertions that instantiate every public record and compare `dataclasses.fields()` with these exact field tuples:

```python
EXPECTED_FIELDS = {
    GatewayContractError: ("code", "detail"),
    GenerationMessage: ("role", "content"),
    GenerationRequest: (
        "request_id", "template_version", "messages", "output_schema",
        "max_output_tokens", "deadline_budget_s", "canonical_input_digest",
    ),
    ProviderConfig: ("provider", "model_id", "api_key"),
    TokenUsage: ("input_tokens", "output_tokens", "total_tokens"),
    AttemptStarted: (
        "request_id", "attempt_index", "provider", "model_id",
        "input_digest", "timeout_s",
    ),
    AttemptRecord: (
        "request_id", "attempt_index", "provider", "model_id", "status",
        "failure_code", "retryable", "security_failure",
        "provider_request_id", "finish_reason", "usage", "input_digest",
        "output_digest",
    ),
    AttemptObserverError: (
        "request_id", "attempt_index", "provider", "phase",
    ),
    GenerationResult: (
        "request_id", "status", "output", "failure_code",
        "selected_provider", "selected_model_id", "provider_request_id",
        "finish_reason", "usage", "input_digest", "output_digest", "attempts",
    ),
}

def test_public_records_have_frozen_exact_fields():
    for record_type, expected in EXPECTED_FIELDS.items():
        assert tuple(field.name for field in dataclasses.fields(record_type)) == expected
        assert record_type.__dataclass_params__.frozen is True
```

Run the Step 2 command again. Expected: imports now reach missing records or field mismatches.

- [ ] **Step 5: Implement the complete secret-safe records**

Add these records exactly; normalize optional text to bounded ASCII identifiers and copy JSON values into immutable mappings in `__post_init__`:

```python
@dataclass(frozen=True, slots=True)
class GenerationMessage:
    role: MessageRole
    content: str = field(repr=False)

@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider: Provider
    model_id: str
    api_key: str = field(repr=False)

@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

@dataclass(frozen=True, slots=True)
class AttemptStarted:
    request_id: str
    attempt_index: int
    provider: Provider
    model_id: str
    input_digest: str
    timeout_s: float

@dataclass(frozen=True, slots=True)
class AttemptRecord:
    request_id: str
    attempt_index: int
    provider: Provider
    model_id: str
    status: GenerationStatus
    failure_code: FailureCode | None
    retryable: bool
    security_failure: bool
    provider_request_id: str | None
    finish_reason: str | None
    usage: TokenUsage | None
    input_digest: str
    output_digest: str | None

@dataclass(frozen=True, slots=True)
class AttemptObserverError(Exception):
    request_id: str
    attempt_index: int
    provider: Provider
    phase: str

    def __str__(self) -> str:
        return (
            f"attempt observer failed: provider={self.provider.value} "
            f"index={self.attempt_index} phase={self.phase}"
        )

@dataclass(frozen=True, slots=True)
class GenerationResult:
    request_id: str
    status: GenerationStatus
    output: Mapping[str, object] | None = field(repr=False)
    failure_code: FailureCode | None
    selected_provider: Provider | None
    selected_model_id: str | None
    provider_request_id: str | None
    finish_reason: str | None
    usage: TokenUsage | None
    input_digest: str
    output_digest: str | None
    attempts: tuple[AttemptRecord, ...]
```

`ProviderConfig.__post_init__` rejects blank model IDs/keys, model IDs longer than 128 printable ASCII characters, keys longer than 4096 printable ASCII characters, and any control/non-ASCII character in either field; validation errors are constant and never echo input. `TokenUsage` accepts nonnegative integers and requires `total_tokens == input_tokens + output_tokens`. Attempt indices are nonnegative; request/model/provider/input digest must agree between a start, its finish, and the final result. A `SUCCEEDED` record/result requires output and output digest, requires `stable_digest(output) == output_digest`, and forbids a failure code; every other status requires a failure code and forbids decoded output/output digest. Provider request ID and finish reason are ASCII strings capped at 160 characters. `AttemptObserverError.phase` accepts only `started` or `finished`, and the exception never chains or renders the observer's original exception. `GenerationResult.__post_init__` requires a tuple for `attempts` and recursively freezes any output mapping. Every record must use `repr=False` for data that can contain model text or secrets; tests assert the API key, message content, and decoded output do not appear in `repr`.

- [ ] **Step 6: Implement and test `GenerationRequest` validation**

Use the exact field surface below; the computed digest is not caller-supplied:

```python
@dataclass(frozen=True, slots=True)
class GenerationRequest:
    request_id: str
    template_version: str
    messages: tuple[GenerationMessage, ...] = field(repr=False)
    output_schema: Mapping[str, object] = field(repr=False)
    max_output_tokens: int
    deadline_budget_s: float
    canonical_input_digest: str = field(init=False)
```

`GenerationRequest.__post_init__` validates a nonempty ASCII request ID matching `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`, a nonempty template version up to 64 ASCII characters, a tuple of `1..64` messages with content `1..32768` Unicode characters, a valid Draft 2020-12 object schema, canonical schema size no more than 64 KiB, recursive depth no more than 20, no `$ref`/`$dynamicRef` at any depth, no absolute or remote `$id`, integer token cap `1..16384`, and finite positive deadline. It copies the tuple and recursively freezes a detached schema, then computes the content-derived digest.

Compute the digest from this exact semantic payload before freezing the schema. The local import avoids a module-import cycle and happens only when a request is instantiated:

```python
from .serialization import stable_digest

semantic_payload = {
    "template_version": self.template_version,
    "messages": [
        {"role": message.role.value, "content": message.content}
        for message in normalized_messages
    ],
    "output_schema": schema_copy,
    "max_output_tokens": self.max_output_tokens,
}
object.__setattr__(
    self,
    "canonical_input_digest",
    stable_digest(semantic_payload),
)
```

Neither `request_id` nor `deadline_budget_s` appears in `semantic_payload`; the tests in Step 1 must also prove changing template version, any message role/content, schema, or max output tokens changes the digest.

- [ ] **Step 7: Add the required observer seam without persistence knowledge**

Use a protocol whose methods have concrete fail-loud bodies. Do not provide a production no-op observer: every `generate` call must inject an observer explicitly.

```python
class AttemptObserver(Protocol):
    def started(self, attempt: AttemptStarted) -> None:
        raise AssertionError("protocol method executed")

    def finished(self, attempt: AttemptRecord) -> None:
        raise AssertionError("protocol method executed")
```

- [ ] **Step 8: Implement strict canonical serialization and schema decoding**

In `serialization.py`, provide:

```python
def canonical_json(value: object) -> str:
    return json.dumps(_canonical_tree(value), ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))

def stable_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def decode_validated_json(raw: bytes, *, schema: Mapping[str, Any]) -> Mapping[str, Any]:
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                       parse_constant=_reject_constant)
    if type(value) is not dict:
        raise GatewayContractError(
            FailureCode.MALFORMED_PROVIDER_RESPONSE,
            "output must be an object",
        )
    schema_copy = _canonical_tree(schema)
    _validate_local_object_schema(schema_copy)
    Draft202012Validator.check_schema(schema_copy)
    Draft202012Validator(schema_copy).validate(value)
    return _freeze_json(value)
```

Implement `_freeze_json` recursively: a `Mapping` with only string keys becomes `MappingProxyType({key: _freeze_json(child)})`, a list or tuple becomes a tuple of frozen children, and `None`/`bool`/finite `int`/finite `float`/`str` remain unchanged. Reject all other types. Implement `_canonical_tree` as the inverse JSON-shaped copy for every `Mapping` and tuple so `canonical_json` accepts frozen values. `_validate_local_object_schema` is the one shared recursive check used both by `GenerationRequest` and the direct decoder; it requires root `type: object`, applies the 64 KiB/depth-20 bounds, rejects `$ref`/`$dynamicRef`, and rejects absolute or remote `$id` before `jsonschema` is invoked. Translate decoder/schema library exceptions into stable errors whose first argument is a concrete `FailureCode` member and whose constant detail contains neither raw input nor output.

Add a nested mutation regression: construct a request and successful result from a schema/output containing a list of dictionaries, mutate the caller-owned list/dictionary, and assert the stored schema/output and both digests remain unchanged. Also assert `canonical_json(source) == canonical_json(_freeze_json(source))` before mutation.

- [ ] **Step 9: Export only symbols implemented by Task 1 and run GREEN**

Export only `DeploymentRegion`, `FailureCode`, `GatewayContractError`, `GenerationMessage`, `GenerationRequest`, `GenerationResult`, `GenerationStatus`, `MessageRole`, `AttemptObserver`, `AttemptObserverError`, `AttemptRecord`, `AttemptStarted`, `Provider`, `ProviderConfig`, `TokenUsage`, `canonical_json`, `decode_validated_json`, and `stable_digest` through `nxt_model_gateway/__init__.py`. Do not forward-reference or import `ModelGateway`, provider adapters, endpoint values, or transport helpers before Tasks 2-4 create them. Run the Step 2 command and verify all contract tests pass.

- [ ] **Step 10: Commit the contract layer**

```bash
git add simulation/nxt_model_gateway simulation/tests/model_gateway
git commit -m "feat(model-gateway): define neutral generation contracts"
```

### Task 2: Implement the bounded direct HTTPS transport

**Files:**
- Create: `simulation/nxt_model_gateway/transport.py`
- Create: `simulation/tests/model_gateway/test_transport.py`

**Interfaces:**
- Consumes: one private allowlisted `ProviderEndpoint`, redacted headers, canonical JSON bytes, and one per-attempt timeout.
- Produces: bounded `HttpResponse` or typed `TransportFailure`; never follows redirects or consults environment proxies.

- [ ] **Step 1: Add fake-connection tests for the complete security boundary**

Build `FakeHttpsConnection`/`FakeResponse` locally in `test_transport.py` and assert:

```python
def test_transport_uses_direct_tls_identity_encoding_and_closes(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:8080")
    fake = FakeHttpsConnection(response(200, b'{}'))
    transport = StdlibHttpsTransport(
        monotonic=clock(),
        resolver=fake_resolver("203.0.113.10"),
        connection_factory=lambda **_: fake,
    )
    result = transport.post(endpoint=_OPENAI_ENDPOINT, headers={"Authorization": "Bearer hidden"},
                            body=b'{}', timeout_s=2.0)
    assert result.status_code == 200
    assert fake.request_args == ("POST", "/v1/responses")
    assert fake.host_header == "api.openai.com"
    assert fake.tls_server_hostname == "api.openai.com"
    assert fake.request_headers["Accept-Encoding"] == "identity"
    assert fake.closed is True

@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_is_security_failure_and_is_never_followed(status):
    fake = FakeHttpsConnection(response(status, b"", headers={"Location": "https://example.invalid"}))
    with pytest.raises(TransportFailure) as raised:
        transport(fake).post(endpoint=_OPENAI_ENDPOINT, headers={}, body=b"{}", timeout_s=2.0)
    assert raised.value.code is FailureCode.REDIRECT_REFUSED
    assert raised.value.retryable is False
    assert raised.value.security_failure is True
    assert fake.request_count == 1

@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate"])
def test_compressed_response_is_refused(encoding):
    fake = FakeHttpsConnection(response(200, b"opaque", headers={"Content-Encoding": encoding}))
    with pytest.raises(TransportFailure) as raised:
        transport(fake).post(endpoint=_OPENAI_ENDPOINT, headers={}, body=b"{}", timeout_s=2.0)
    assert raised.value.code is FailureCode.UNSUPPORTED_CONTENT_ENCODING
    assert raised.value.retryable is False
    assert raised.value.security_failure is True

def test_request_over_256_kib_fails_before_connection_factory_runs():
    factory = RecordingFactory(response(200, b"{}"))
    with pytest.raises(TransportFailure) as raised:
        transport(factory=factory).post(endpoint=_OPENAI_ENDPOINT, headers={},
                                        body=b"x" * (256 * 1024 + 1), timeout_s=2.0)
    assert raised.value.code is FailureCode.INPUT_TOO_LARGE
    assert raised.value.retryable is False
    assert raised.value.security_failure is False
    assert factory.calls == []

def test_content_length_over_512_kib_is_rejected_before_body_read():
    fake_response = response(
        200,
        b"ignored",
        headers={"Content-Length": str(512 * 1024 + 1)},
    )
    fake = FakeHttpsConnection(fake_response)
    with pytest.raises(TransportFailure) as raised:
        transport(fake).post(
            endpoint=_OPENAI_ENDPOINT, headers={}, body=b"{}", timeout_s=2.0
        )
    assert raised.value.code is FailureCode.RESPONSE_TOO_LARGE
    assert raised.value.retryable is False
    assert raised.value.security_failure is True
    assert fake_response.read_calls == []
    assert fake.closed is True

def test_streamed_body_over_512_kib_reads_only_one_sentinel_byte_extra():
    fake_response = ChunkedFakeResponse(
        status=200,
        chunks=(b"x" * (512 * 1024), b"y"),
    )
    fake = FakeHttpsConnection(fake_response)
    with pytest.raises(TransportFailure) as raised:
        transport(fake).post(
            endpoint=_OPENAI_ENDPOINT, headers={}, body=b"{}", timeout_s=2.0
        )
    assert raised.value.code is FailureCode.RESPONSE_TOO_LARGE
    assert raised.value.retryable is False
    assert raised.value.security_failure is True
    assert fake_response.total_bytes_returned == 512 * 1024 + 1
    assert fake.closed is True
```

For TLS, DNS, connect timeout, read timeout, reset, and incomplete read, use this exact `FailureCase(exception, phase, code, retryable, security_failure)` table:

```python
FAILURE_CASES = (
    FailureCase(socket.gaierror(-2, "name failure"), TransportPhase.RESOLVING,
                FailureCode.DNS_FAILURE, True, False),
    FailureCase(TimeoutError(), TransportPhase.CONNECTING,
                FailureCode.CONNECT_TIMEOUT, True, False),
    FailureCase(ssl.SSLCertVerificationError(1, "certificate failure"),
                TransportPhase.CONNECTING,
                FailureCode.TLS_VERIFICATION_FAILED, False, True),
    FailureCase(TimeoutError(), TransportPhase.READING,
                FailureCode.READ_TIMEOUT, True, False),
    FailureCase(ConnectionResetError(), TransportPhase.READING,
                FailureCode.CONNECTION_INTERRUPTED, True, False),
    FailureCase(http.client.IncompleteRead(b"", 1), TransportPhase.READING,
                FailureCode.CONNECTION_INTERRUPTED, True, False),
)
```

The parameterized test must set the fake connection to raise at that exact phase and assert the expected fields, closed connection, and absence of raw body/key text in both `str` and `repr`.

Add explicit connection-phase coverage rather than treating all `OSError` instances alike:

```python
@pytest.mark.parametrize("failure", [
    ConnectionRefusedError(111, "refused"),
    OSError(113, "no route"),
])
def test_connect_os_errors_are_retryable_connect_failed(failure):
    fake = FakeHttpsConnection(connect_failure=failure)
    with pytest.raises(TransportFailure) as raised:
        transport(fake).post(
            endpoint=_OPENAI_ENDPOINT, headers={}, body=b"{}", timeout_s=2.0
        )
    assert raised.value.code is FailureCode.CONNECT_FAILED
    assert raised.value.retryable is True
    assert raised.value.security_failure is False
    assert fake.request_count == 0
    assert fake.closed is True
```

- [ ] **Step 2: Run the transport tests and verify RED**

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/model_gateway/test_transport.py
```

Expected: import failure because the transport is absent.

- [ ] **Step 3: Add private endpoints and complete transport records**

Use no public arbitrary URL string:

```python
@dataclass(frozen=True, slots=True)
class ProviderEndpoint:
    provider: Provider
    host: str
    port: int
    path: str

class TransportPhase(StrEnum):
    RESOLVING = "RESOLVING"
    CONNECTING = "CONNECTING"
    READING = "READING"

_KIMI_ENDPOINT = ProviderEndpoint(Provider.KIMI, "api.moonshot.ai", 443, "/v1/chat/completions")
_OPENAI_ENDPOINT = ProviderEndpoint(Provider.OPENAI, "api.openai.com", 443, "/v1/responses")
_ANTHROPIC_ENDPOINT = ProviderEndpoint(Provider.ANTHROPIC, "api.anthropic.com", 443, "/v1/messages")

@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)

@dataclass(frozen=True, slots=True)
class TransportFailure(Exception):
    code: FailureCode
    retryable: bool
    security_failure: bool
    detail: str

    def __str__(self) -> str:
        return f"{self.code.value}: {self.detail}"

class HttpTransport(Protocol):
    def post(
        self,
        *,
        endpoint: ProviderEndpoint,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: float,
    ) -> HttpResponse:
        raise AssertionError("protocol method executed")
```

The production transport rejects any `ProviderEndpoint` not equal to one of those constants. Fake transports remain injectable in adapter tests.

`HttpResponse.__post_init__` copies lower-cased response-header names into `MappingProxyType`, copies the body to immutable bytes, and validates status `100..599`. `TransportFailure.detail` must be one of a closed set of constant diagnostics; it never includes an exception string, endpoint, header, body, or provider payload.

`StdlibHttpsTransport.__init__` requires the monotonic callable and accepts keyword-only resolver/connection-factory test seams whose production defaults are `socket.getaddrinfo` and the private resolved-address HTTPS factory. Tests must always inject both seams; no unit test may resolve or connect to a live hostname.

- [ ] **Step 4: Implement the endpoint and outbound-size gate**

At the first line of `StdlibHttpsTransport.post`, compare the endpoint with the three private constants, validate header names case-insensitively, and reject caller-supplied `Host`, `Content-Length`, `Transfer-Encoding`, `Connection`, or `Accept-Encoding`. Emit repository-owned `Host`, byte-accurate `Content-Length`, `Connection: close`, and `Accept-Encoding: identity` with those exact canonical names. Reject `len(body) > 262144` before calling the resolver or connection factory. The transport keeps this check as defense in depth even though adapters will reject oversized prepared bodies earlier. Run the request-limit, reserved-header, CR/LF header-value, and endpoint tests and verify they pass while later transport tests remain red.

- [ ] **Step 5: Implement verified direct TLS and bounded DNS**

`StdlibHttpsTransport` must create `ssl.create_default_context()` with `check_hostname is True` and `verify_mode == ssl.CERT_REQUIRED`. Because the platform resolver is not reliably bounded by a socket timeout, run only `socket.getaddrinfo(host, port, type=SOCK_STREAM)` in a daemon resolver thread, wait only to the injected deadline, and set a cancellation event on expiry. The resolver thread receives only host/port, stores an address-or-error result, and never receives or calls the connection factory. The caller discards a late result and checks cancellation before it alone invokes that factory. Select the first returned address once; do not iterate addresses or reconnect inside one gateway attempt. After successful resolution, use a small `HTTPSConnection` subclass/factory that connects to that resolved address with the remaining timeout while preserving the original hostname for the HTTP `Host` header and `SSLContext.wrap_socket(connected_socket, server_hostname=host)` certificate/SNI validation. Issue one POST, reject 3xx before reading a follow-up location, reject any `Content-Encoding` other than absent/`identity`, reject `Content-Length > 524288`, and stream at most `524289` bytes while clamping socket timeouts to the injected monotonic deadline. Close in `finally`.

Add a blocking-resolver test: advance the fake monotonic deadline, assert retryable `DNS_FAILURE` is returned within the budget, then release the resolver and assert the connection factory and fake server still have zero calls. This prevents a timed-out DNS operation from producing a late outbound request.

Run the direct-TLS, ignored-proxy, blocking-resolver, late-DNS, and hostname/SNI tests. Expected: those tests pass while response and exception-matrix tests remain red.

- [ ] **Step 6: Implement response gates and phase-aware failure mapping**

Map exception classes using the current phase enum (`RESOLVING`, `CONNECTING`, `READING`), never exception strings containing request material:

```text
socket.gaierror                 -> DNS_FAILURE, retryable
TimeoutError before response   -> CONNECT_TIMEOUT, retryable
ConnectionRefusedError during connect
                                -> CONNECT_FAILED, retryable
OSError during connect         -> CONNECT_FAILED, retryable
TimeoutError while reading     -> READ_TIMEOUT, retryable
ConnectionResetError/BrokenPipeError/http.client.IncompleteRead
                                -> CONNECTION_INTERRUPTED, retryable
ssl.SSLError/certificate error -> TLS_VERIFICATION_FAILED, security
```

Check `ssl.SSLCertVerificationError`/`ssl.SSLError`, `TimeoutError`, `ConnectionRefusedError`, reset/broken-pipe, and incomplete-read branches before the general `OSError` branch because several inherit from `OSError`. Map any remaining non-TLS `OSError` raised in `CONNECTING` to retryable `CONNECT_FAILED`; map any remaining non-TLS `OSError` raised in `READING`, plus reset/broken-pipe/incomplete-read errors, to retryable `CONNECTION_INTERRUPTED`. `REDIRECT_REFUSED`, `UNSUPPORTED_CONTENT_ENCODING`, `RESPONSE_TOO_LARGE`, `ENDPOINT_NOT_ALLOWED`, and `TLS_VERIFICATION_FAILED` are non-retryable security failures; `INPUT_TOO_LARGE` is non-retryable and not a security failure. Reject redirects without following, reject compressed responses, bound `Content-Length`, and stream at most 524289 bytes. Close the connection exactly once in `finally`.

- [ ] **Step 7: Run transport tests and inspect captured diagnostics**

Run the Step 2 command. Add a final assertion that concatenated exception `repr` values contain neither `Authorization`, `x-api-key`, request body, nor fake response body. Verify GREEN.

- [ ] **Step 8: Commit the transport**

```bash
git add simulation/nxt_model_gateway/transport.py simulation/tests/model_gateway/test_transport.py
git commit -m "feat(model-gateway): add bounded https transport"
```

### Task 3: Freeze and implement Kimi, OpenAI, and Anthropic adapters

**Files:**
- Modify: `simulation/nxt_model_gateway/__init__.py:1-80` (Task 1-created export block; append only adapter records/classes created in this task)
- Create: `simulation/nxt_model_gateway/adapters.py`
- Create: `simulation/nxt_model_gateway/kimi.py`
- Create: `simulation/nxt_model_gateway/openai.py`
- Create: `simulation/nxt_model_gateway/anthropic.py`
- Create: `simulation/tests/model_gateway/conftest.py`
- Create: `simulation/tests/model_gateway/fixtures/kimi_success.json`
- Create: `simulation/tests/model_gateway/fixtures/openai_success.json`
- Create: `simulation/tests/model_gateway/fixtures/anthropic_success.json`
- Create: `simulation/tests/model_gateway/test_kimi_adapter.py`
- Create: `simulation/tests/model_gateway/test_openai_adapter.py`
- Create: `simulation/tests/model_gateway/test_anthropic_adapter.py`

**Interfaces:**
- Consumes: a neutral request and injected transport.
- Produces: a prepared request followed by exactly one `AdapterOutcome`; preparation performs all local validation before the attempt observer is told a network call may begin.

- [ ] **Step 1: Add failing exact-field tests for the two-phase adapter seam**

Pin the complete records in `test_kimi_adapter.py` so no later task has to infer fields:

```python
def test_prepared_request_and_outcome_have_exact_fields():
    assert tuple(f.name for f in dataclasses.fields(PreparedRequest)) == (
        "request_id", "provider", "model_id", "endpoint", "headers", "body",
        "output_schema", "input_digest",
    )
    assert tuple(f.name for f in dataclasses.fields(AdapterOutcome)) == (
        "status", "decoded_json", "failure_code", "provider_request_id",
        "finish_reason", "usage", "input_digest", "output_digest",
        "retryable", "security_failure",
    )
```

Run the adapter test command in Step 3. Expected: imports fail because the adapter records do not exist.

- [ ] **Step 2: Implement complete secret-safe adapter records and protocol**

Add this exact surface to `adapters.py`:

```python
@dataclass(frozen=True, slots=True)
class PreparedRequest:
    request_id: str
    provider: Provider
    model_id: str
    endpoint: ProviderEndpoint
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    output_schema: Mapping[str, object] = field(repr=False)
    input_digest: str

@dataclass(frozen=True, slots=True)
class AdapterOutcome:
    status: GenerationStatus
    decoded_json: Mapping[str, object] | None = field(repr=False)
    failure_code: FailureCode | None
    provider_request_id: str | None
    finish_reason: str | None
    usage: TokenUsage | None
    input_digest: str
    output_digest: str | None
    retryable: bool
    security_failure: bool

class ProviderAdapter(Protocol):
    provider: Provider
    model_id: str

    def prepare(self, request: GenerationRequest) -> PreparedRequest:
        raise AssertionError("protocol method executed")

    def send(
        self,
        prepared: PreparedRequest,
        *,
        timeout_s: float,
    ) -> AdapterOutcome:
        raise AssertionError("protocol method executed")
```

Both record constructors must detach inputs. `PreparedRequest` copies headers into `MappingProxyType`, calls `_freeze_json` for the schema, and copies the body with `bytes(body)`. `AdapterOutcome` calls `_freeze_json` for decoded JSON and, on success, requires `stable_digest(decoded_json) == output_digest`; non-success outcomes forbid both decoded JSON and output digest. Headers, bodies, schema, and decoded JSON stay out of `repr`; add assertions that neither `secret-key` nor `sensitive-output` appears in either representation. Mutate nested caller-owned schema/output values after construction and assert stored values and digests do not change.

Freeze this complete failure disposition in adapter and routing tests; no implementation may infer status or security from free text. Production adapters set `retryable=True` only for the eight availability codes in the first row. The neutral `AdapterOutcome` record does not make that boolean a routing authority: Task 4 deliberately inverts it in test doubles. `GLOBAL` fallback requires membership in the same eight-code set, configured Anthropic, and positive remaining budget; it never branches on the boolean alone.

| Failure codes | `GenerationStatus` | `security_failure` | GLOBAL fallback eligible |
|---|---|---:|---:|
| `DNS_FAILURE`, `CONNECT_TIMEOUT`, `CONNECT_FAILED`, `READ_TIMEOUT`, `CONNECTION_INTERRUPTED`, `HTTP_TIMEOUT`, `RATE_LIMITED`, `PROVIDER_UNAVAILABLE` | `UNAVAILABLE` | false | yes |
| `DEADLINE_EXHAUSTED`, `BACKUP_UNCONFIGURED` | `UNAVAILABLE` | false | no |
| `PROVIDER_REFUSED` | `REFUSED` | false | no |
| `MALFORMED_PROVIDER_RESPONSE`, `SCHEMA_MISMATCH` | `INVALID_RESPONSE` | false | no |
| `INPUT_TOO_LARGE`, `AUTHENTICATION_FAILED`, `PERMISSION_DENIED`, `MODEL_NOT_FOUND`, `INVALID_PROVIDER_REQUEST`, `PROVIDER_UNCONFIGURED` | `CONFIGURATION_ERROR` | false | no |
| `RESPONSE_TOO_LARGE`, `UNSUPPORTED_CONTENT_ENCODING`, `REDIRECT_REFUSED`, `ENDPOINT_NOT_ALLOWED`, `TLS_VERIFICATION_FAILED` | `SECURITY_ERROR` | true | no |
| `PROVIDER_CLIENT_ERROR` | `PROVIDER_ERROR` | false | no |

Assert the union of those rows equals `set(FailureCode)` so adding a code cannot silently inherit a default. The request-size case is deliberately configuration, not security; response-size and unsupported-encoding cases are security failures.

Add one shared exact HTTP classifier in `adapters.py`: 401 -> `AUTHENTICATION_FAILED`; 403 -> `PERMISSION_DENIED`; 404 -> `MODEL_NOT_FOUND`; 408 -> retryable `HTTP_TIMEOUT`; 429 -> retryable `RATE_LIMITED`; 500 through 599 -> retryable `PROVIDER_UNAVAILABLE`; 400, 409, and 422 -> non-retryable `INVALID_PROVIDER_REQUEST`; every other 4xx -> non-retryable `PROVIDER_CLIENT_ERROR`. Redirects never reach this classifier because transport rejects 3xx. Only HTTP 200 is a success envelope; other 2xx statuses map to non-retryable `MALFORMED_PROVIDER_RESPONSE`. Classify every non-200 response before parsing a provider body and never include that body in outcome diagnostics or exceptions.

- [ ] **Step 3: Add neutral provider-native fixtures and run RED**

Use these exact success fixtures; the gateway has no staffing vocabulary:

```json
{"id":"kimi_req_001","choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"{\"result\":[]}"}}],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}
```

```json
{"id":"resp_001","status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"{\"result\":[]}"}]}],"usage":{"input_tokens":10,"output_tokens":4,"total_tokens":14}}
```

```json
{"id":"msg_001","stop_reason":"tool_use","content":[{"type":"tool_use","id":"toolu_001","name":"emit_structured_output","input":{"result":[]}}],"usage":{"input_tokens":10,"output_tokens":4}}
```

Run:

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/model_gateway/test_kimi_adapter.py \
  tests/model_gateway/test_openai_adapter.py \
  tests/model_gateway/test_anthropic_adapter.py
```

Expected: exact-envelope tests fail because provider classes do not exist.

- [ ] **Step 4: Add the direct 256 KiB preparation boundary test**

Use an output schema and message content that serialize to 262145 bytes, then assert the adapter fails locally:

```python
def test_oversized_prepared_body_fails_before_transport():
    transport_calls: list[str] = []
    adapter = KimiAdapter(
        config=kimi_config(),
        transport=RecordingTransport(transport_calls),
    )
    oversized = request_with_serialized_body_size(262145)

    with pytest.raises(GatewayContractError) as raised:
        adapter.prepare(oversized)

    assert raised.value.code is FailureCode.INPUT_TOO_LARGE
    assert transport_calls == []

def test_prepared_body_at_256_kib_is_allowed():
    transport_calls: list[str] = []
    adapter = KimiAdapter(
        config=kimi_config(),
        transport=RecordingTransport(transport_calls),
    )

    prepared = adapter.prepare(request_with_serialized_body_size(262144))

    assert len(prepared.body) == 262144
    assert transport_calls == []
```

The helper must build a real `GenerationRequest` and find the exact body size by adjusting a benign message field; it must not monkeypatch the body length. This task proves the adapter does not reach transport. Because adapters never receive observers, Task 4 adds the `ModelGateway.generate()` assertion that the same rejection also produces zero observer calls. Run this test and verify RED.

- [ ] **Step 5: Implement the shared `prepare()` size and safety gate**

Each adapter constructor requires a `ProviderConfig` whose provider exactly matches that adapter and whose model ID is explicit; a mismatch raises `GatewayContractError(FailureCode.PROVIDER_UNCONFIGURED, "provider config does not match adapter")` without transport. Each provider `prepare()` must build its final `provider_payload` mapping, serialize it with `canonical_json(provider_payload).encode("utf-8")`, and reject a body larger than 262144 bytes before returning `PreparedRequest`. `ModelGateway` calls `prepare()` before the observer and calls `send()` only after a durable `started` callback, so a preparation rejection reaches neither callback nor transport. Direct `adapter.prepare()` retains the typed exception; Task 4 requires `ModelGateway.generate()` to normalize its `INPUT_TOO_LARGE` exception into a zero-attempt result. Keep the transport's own 256 KiB check as defense in depth. Verify the Step 4 test passes.

- [ ] **Step 6: Add the Kimi exact-envelope tests**

Assert this exact body and header set after redacting the key from assertions:

```python
expected_body = {
    "model": "kimi-pinned",
    "messages": [{"role": "user", "content": "return an object"}],
    "max_tokens": 256,
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "structured_output",
            "strict": True,
            "schema": request().output_schema,
        },
    },
    "stream": False,
}
assert sent.endpoint == _KIMI_ENDPOINT
assert json.loads(sent.body) == expected_body
assert set(sent.headers) == {"Authorization", "Content-Type", "Accept"}
assert hmac.compare_digest(
    sent.headers["Authorization"], f"Bearer {config.api_key}"
)
assert sent.headers["Content-Type"] == "application/json"
assert sent.headers["Accept"] == "application/json"
assert "x-api-key" not in sent.headers
```

Also test malformed JSON, duplicate answer fields, `reasoning_content` without ordinary content, explicit provider refusal, schema mismatch, 401/403/404/408/409/422/429, arbitrary 400/499, every representative 5xx, and one transport failure. Run only `test_kimi_adapter.py` and verify RED.

- [ ] **Step 7: Implement the Kimi Chat Completions adapter**

Build the exact Step 6 envelope and set `Authorization` to the literal `Bearer ` prefix concatenated with `config.api_key`; set `Content-Type: application/json` and `Accept: application/json`. Parse only `choices[0].message.content` as the answer document, and pass its UTF-8 bytes to `decode_validated_json`. Reject `reasoning_content` as an output source. Map only explicit protocol fields and HTTP status; never scan natural-language content. Run `test_kimi_adapter.py` and verify GREEN.

- [ ] **Step 8: Add the OpenAI Responses exact-envelope tests**

Pin the neutral request body:

```python
expected_body = {
    "model": "openai-pinned",
    "input": [{"role": "user", "content": "return an object"}],
    "max_output_tokens": 256,
    "text": {
        "format": {
            "type": "json_schema",
            "name": "structured_output",
            "strict": True,
            "schema": request().output_schema,
        }
    },
    "store": False,
}
```

Assert `_OPENAI_ENDPOINT`; header keys exactly `Authorization`, `Content-Type`, and `Accept`; `hmac.compare_digest(sent.headers["Authorization"], f"Bearer {config.api_key}")`; JSON content/accept values; absence of `x-api-key`; one call; response ID; finish reason; usage; decoded JSON; and digest. Reject `choices`, reasoning items as answer text, multiple `output_text` documents, explicit `refusal`, incomplete output, malformed JSON, schema mismatch, and the full HTTP matrix. Run only `test_openai_adapter.py` and verify RED.

- [ ] **Step 9: Implement the OpenAI Responses adapter**

Build the Step 8 envelope with no tools and parse only `output[].content[]` items. Accept exactly one `output_text` document when `status == "completed"`; store that status string as `finish_reason`. Map an explicit `refusal` item to `REFUSED/PROVIDER_REFUSED`; do not treat reasoning items as output. Use only the injected key in the `Authorization` header. Run `test_openai_adapter.py` and verify GREEN.

- [ ] **Step 10: Add the Anthropic Messages exact-envelope tests**

Pin the neutral tool-use envelope:

```python
expected_body = {
    "model": "anthropic-pinned",
    "max_tokens": 256,
    "system": "system instruction",
    "messages": [{"role": "user", "content": "return an object"}],
    "tools": [{
        "name": "emit_structured_output",
        "description": "Return only the JSON object requested by the caller.",
        "input_schema": request().output_schema,
    }],
    "tool_choice": {"type": "tool", "name": "emit_structured_output"},
}
```

Assert `_ANTHROPIC_ENDPOINT` and freeze the complete header contract:

```python
assert set(sent.headers) == {
    "x-api-key", "anthropic-version", "Content-Type", "Accept"
}
assert hmac.compare_digest(sent.headers["x-api-key"], config.api_key)
assert sent.headers["anthropic-version"] == "2023-06-01"
assert sent.headers["Content-Type"] == "application/json"
assert sent.headers["Accept"] == "application/json"
assert "Authorization" not in sent.headers
```

`x-api-key` is the only authentication header. Also assert exactly one matching tool block, provider request ID, stop reason, usage, decoded object, and digest. Reject unknown/multiple tool blocks, text-only/reasoning-only output, explicit refusal, schema mismatch, malformed envelope, and the full HTTP matrix. Run only `test_anthropic_adapter.py` and verify RED.

- [ ] **Step 11: Implement the Anthropic Messages adapter**

Allow zero or one leading system message and preserve the remaining user/assistant order; omit the `system` key when absent, and reject multiple or non-leading system messages during `prepare()`. Build the Step 10 envelope when a system message is present, accept exactly one `tool_use` named `emit_structured_output`, validate its `input` directly as the decoded object, and never execute the tool. Map only explicit HTTP/stop fields. Run `test_anthropic_adapter.py` and verify GREEN.

- [ ] **Step 12: Add one-call and no-hidden-retry tests**

For each adapter, assert this exact call list for a valid send:

```python
assert calls == ["transport.post"]
```

Parameterize every transport-owned code (`INPUT_TOO_LARGE`, `RESPONSE_TOO_LARGE`, `UNSUPPORTED_CONTENT_ENCODING`, `REDIRECT_REFUSED`, `ENDPOINT_NOT_ALLOWED`, `TLS_VERIFICATION_FAILED`, `DNS_FAILURE`, `CONNECT_TIMEOUT`, `CONNECT_FAILED`, `READ_TIMEOUT`, and `CONNECTION_INTERRUPTED`). Make the fake transport raise one `TransportFailure` with its production flags and assert each adapter returns one `AdapterOutcome` with the exact status/code/retryable/security disposition from Step 2 after exactly one transport call. Adapters never receive an observer, never choose another provider, and contain no retry loop. Run all three adapter files and verify GREEN.

- [ ] **Step 13: Export adapter records/classes and commit**

Add `AdapterOutcome`, `PreparedRequest`, `ProviderAdapter`, `KimiAdapter`, `OpenAIAdapter`, and `AnthropicAdapter` to `nxt_model_gateway/__init__.py`; keep endpoints and HTTP helpers private. Run the Step 3 command and verify GREEN, then:

```bash
git add simulation/nxt_model_gateway simulation/tests/model_gateway
git commit -m "feat(model-gateway): adapt kimi openai and anthropic"
```

### Task 4: Implement regional readiness, deadlines, and the complete fallback matrix

**Files:**
- Create: `simulation/nxt_model_gateway/routing.py`
- Create: `simulation/tests/model_gateway/test_routing.py`
- Modify: `simulation/nxt_model_gateway/__init__.py:1-80` (Task 1/3-created export block; append only the Task 4 routing exports)

**Interfaces:**
- Consumes: optional configured adapters, injected monotonic clock, `RoutePolicy(region)`, request, and attempt observer.
- Produces: visible readiness and one final normalized result with zero, one, or two ordered attempts.
- Exact calls: `ModelGateway.readiness(route: RoutePolicy) -> RouteReadiness` and `ModelGateway.generate(request: GenerationRequest, route: RoutePolicy, *, observer: AttemptObserver) -> GenerationResult`.
- The design's §9.1 two-argument notation is conceptual; this executable V1 contract requires the keyword-only observer so persistence can complete before transport.

- [ ] **Step 1: Write failing exact readiness-contract tests**

Pin the exact records first:

```python
class RouteReadinessStatus(StrEnum):
    READY = "READY"
    DEGRADED_BACKUP_UNCONFIGURED = "DEGRADED_BACKUP_UNCONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"

@dataclass(frozen=True, slots=True)
class RoutePolicy:
    region: DeploymentRegion

@dataclass(frozen=True, slots=True)
class RouteReadiness:
    region: DeploymentRegion
    status: RouteReadinessStatus
    primary_provider: Provider | None
    backup_provider: Provider | None
    failure_code: FailureCode | None
```

Test all five configurations: CN with/without Kimi, GLOBAL with neither, GLOBAL with OpenAI only, and GLOBAL with both providers. Assert CN never reports a backup and GLOBAL never treats Anthropic alone as ready.

- [ ] **Step 2: Write the complete failing disposition and fallback table**

Use enum values, a shared ordered-call recorder, and this closed table. The tuple fields are `(failure_code, expected_status, expected_security_failure, falls_back)`:

```python
class RecordingObserver:
    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls = [] if calls is None else calls
        self.finished_records: list[AttemptRecord] = []

    def started(self, attempt: AttemptStarted) -> None:
        self.calls.append(f"observer.started:{attempt.attempt_index}")

    def finished(self, attempt: AttemptRecord) -> None:
        self.calls.append(f"observer.finished:{attempt.attempt_index}")
        self.finished_records.append(attempt)

FAILURE_DISPOSITIONS = (
    (FailureCode.DNS_FAILURE, GenerationStatus.UNAVAILABLE, False, True),
    (FailureCode.CONNECT_TIMEOUT, GenerationStatus.UNAVAILABLE, False, True),
    (FailureCode.CONNECT_FAILED, GenerationStatus.UNAVAILABLE, False, True),
    (FailureCode.READ_TIMEOUT, GenerationStatus.UNAVAILABLE, False, True),
    (FailureCode.CONNECTION_INTERRUPTED, GenerationStatus.UNAVAILABLE, False, True),
    (FailureCode.HTTP_TIMEOUT, GenerationStatus.UNAVAILABLE, False, True),
    (FailureCode.RATE_LIMITED, GenerationStatus.UNAVAILABLE, False, True),
    (FailureCode.PROVIDER_UNAVAILABLE, GenerationStatus.UNAVAILABLE, False, True),
    (FailureCode.DEADLINE_EXHAUSTED, GenerationStatus.UNAVAILABLE, False, False),
    (FailureCode.BACKUP_UNCONFIGURED, GenerationStatus.UNAVAILABLE, False, False),
    (FailureCode.PROVIDER_REFUSED, GenerationStatus.REFUSED, False, False),
    (FailureCode.MALFORMED_PROVIDER_RESPONSE, GenerationStatus.INVALID_RESPONSE, False, False),
    (FailureCode.SCHEMA_MISMATCH, GenerationStatus.INVALID_RESPONSE, False, False),
    (FailureCode.INPUT_TOO_LARGE, GenerationStatus.CONFIGURATION_ERROR, False, False),
    (FailureCode.AUTHENTICATION_FAILED, GenerationStatus.CONFIGURATION_ERROR, False, False),
    (FailureCode.PERMISSION_DENIED, GenerationStatus.CONFIGURATION_ERROR, False, False),
    (FailureCode.MODEL_NOT_FOUND, GenerationStatus.CONFIGURATION_ERROR, False, False),
    (FailureCode.INVALID_PROVIDER_REQUEST, GenerationStatus.CONFIGURATION_ERROR, False, False),
    (FailureCode.PROVIDER_UNCONFIGURED, GenerationStatus.CONFIGURATION_ERROR, False, False),
    (FailureCode.RESPONSE_TOO_LARGE, GenerationStatus.SECURITY_ERROR, True, False),
    (FailureCode.UNSUPPORTED_CONTENT_ENCODING, GenerationStatus.SECURITY_ERROR, True, False),
    (FailureCode.REDIRECT_REFUSED, GenerationStatus.SECURITY_ERROR, True, False),
    (FailureCode.ENDPOINT_NOT_ALLOWED, GenerationStatus.SECURITY_ERROR, True, False),
    (FailureCode.TLS_VERIFICATION_FAILED, GenerationStatus.SECURITY_ERROR, True, False),
    (FailureCode.PROVIDER_CLIENT_ERROR, GenerationStatus.PROVIDER_ERROR, False, False),
)

assert {row[0] for row in FAILURE_DISPOSITIONS} == set(FailureCode)
assert len(FAILURE_DISPOSITIONS) == len(FailureCode)
PROVIDER_OUTCOME_DISPOSITIONS = tuple(
    row for row in FAILURE_DISPOSITIONS
    if row[0] not in {
        FailureCode.INPUT_TOO_LARGE,
        FailureCode.BACKUP_UNCONFIGURED,
        FailureCode.DEADLINE_EXHAUSTED,
        FailureCode.PROVIDER_UNCONFIGURED,
    }
)

def outcome(
    code: FailureCode,
    status: GenerationStatus,
    security_failure: bool,
    falls_back: bool,
) -> AdapterOutcome:
    return AdapterOutcome(
        status=status,
        decoded_json=None,
        failure_code=code,
        provider_request_id=None,
        finish_reason=None,
        usage=None,
        input_digest=request().canonical_input_digest,
        output_digest=None,
        # Deliberately invert this test-double hint: routing must use the closed
        # FailureCode set, never the adapter's broader retryable boolean.
        retryable=not falls_back,
        security_failure=security_failure,
    )

def success() -> AdapterOutcome:
    return AdapterOutcome(
        status=GenerationStatus.SUCCEEDED,
        decoded_json={"result": []},
        failure_code=None,
        provider_request_id="provider-001",
        finish_reason="stop",
        usage=TokenUsage(10, 4, 14),
        input_digest=request().canonical_input_digest,
        output_digest=stable_digest({"result": []}),
        retryable=False,
        security_failure=False,
    )

@pytest.mark.parametrize(
    "failure_code,expected_status,expected_security_failure,falls_back",
    FAILURE_DISPOSITIONS,
)
def test_global_fallback_matrix(
    failure_code, expected_status, expected_security_failure, falls_back,
):
    calls: list[Provider] = []
    observer = RecordingObserver()
    primary = FakeAdapter(
        Provider.OPENAI,
        outcome(
            failure_code,
            expected_status,
            expected_security_failure,
            falls_back,
        ),
        calls,
    )
    backup = FakeAdapter(Provider.ANTHROPIC, success(), calls)
    result = gateway(openai=primary, anthropic=backup).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL),
        observer=observer,
    )
    assert calls == (
        [Provider.OPENAI, Provider.ANTHROPIC] if falls_back else [Provider.OPENAI]
    )
    assert observer.finished_records[0].status is expected_status
    assert observer.finished_records[0].failure_code is failure_code
    assert observer.finished_records[0].security_failure is expected_security_failure
    assert result.attempts == tuple(observer.finished_records)
    assert result.status is (
        GenerationStatus.SUCCEEDED if falls_back else expected_status
    )
    assert result.failure_code is (None if falls_back else failure_code)
    assert result.selected_provider is (
        Provider.ANTHROPIC if falls_back else Provider.OPENAI
    )

@pytest.mark.parametrize(
    "failure_code,expected_status,expected_security_failure,_falls_back",
    PROVIDER_OUTCOME_DISPOSITIONS,
)
def test_anthropic_outcome_is_terminal(
    failure_code, expected_status, expected_security_failure, _falls_back,
):
    calls: list[Provider] = []
    observer = RecordingObserver()
    primary = FakeAdapter(
        Provider.OPENAI,
        outcome(
            FailureCode.DNS_FAILURE,
            GenerationStatus.UNAVAILABLE,
            False,
            True,
        ),
        calls,
    )
    backup = FakeAdapter(
        Provider.ANTHROPIC,
        outcome(
            failure_code,
            expected_status,
            expected_security_failure,
            _falls_back,
        ),
        calls,
    )

    result = gateway(openai=primary, anthropic=backup).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )

    assert calls == [Provider.OPENAI, Provider.ANTHROPIC]
    assert len(result.attempts) == 2
    assert result.attempts == tuple(observer.finished_records)
    assert result.status is expected_status
    assert result.failure_code is failure_code
    assert result.selected_provider is Provider.ANTHROPIC
```

Add a separate adapter HTTP-classification table asserting 401 -> `AUTHENTICATION_FAILED`, 403 -> `PERMISSION_DENIED`, 404 -> `MODEL_NOT_FOUND`, 408 -> `HTTP_TIMEOUT`, 429 -> `RATE_LIMITED`, representative 500/502/503/599 -> `PROVIDER_UNAVAILABLE`, 400/409/422 -> `INVALID_PROVIDER_REQUEST`, and 499 -> `PROVIDER_CLIENT_ERROR`. For every row, assert the exact status and security flag from `FAILURE_DISPOSITIONS`; this proves the route table receives stable codes and that 409/422 never fall back.

- [ ] **Step 3: Run routing tests and verify RED**

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/model_gateway/test_routing.py
```

Expected: imports fail because `routing.py` does not exist.

- [ ] **Step 4: Implement the exact route records and readiness projection**

Implement the records from Step 1 plus `ModelGateway.__init__` with only four keyword-only dependencies: optional Kimi/OpenAI/Anthropic adapters and a monotonic callable. Validate each non-null adapter's `provider` equals its named slot, copy those references into private slots, and reject a mismatch with `GatewayContractError(FailureCode.PROVIDER_UNCONFIGURED, "adapter provider does not match configured slot")`. `generate(request, route, *, observer)` requires an explicit non-null `AttemptObserver`; there is no implicit null observer on the network path. Do not accept region, provider, model, endpoint, retry count, or timeout overrides at construction.

```python
assert gateway(kimi=None, openai=None, anthropic=None).readiness(
    RoutePolicy(DeploymentRegion.CN)
) == RouteReadiness(
    region=DeploymentRegion.CN,
    status=RouteReadinessStatus.UNAVAILABLE,
    primary_provider=Provider.KIMI,
    backup_provider=None,
    failure_code=FailureCode.PROVIDER_UNCONFIGURED,
)
```

`CN` readiness is `READY` only with Kimi. `GLOBAL` is unavailable without OpenAI, ready with both, and `DEGRADED_BACKUP_UNCONFIGURED/BACKUP_UNCONFIGURED` with OpenAI only. Route policy contains no provider/model/endpoint selected by the caller.

- [ ] **Step 5: Add failing deadline and attempt-count tests**

Use a scripted monotonic clock and assert: zero attempts when the effective deadline is already exhausted; parameterize every row of `FAILURE_DISPOSITIONS` in CN and get exactly one Kimi attempt with the exact status/code while OpenAI/Anthropic stay at zero calls; one OpenAI attempt when it succeeds or fails ineligibly; two attempts only for eligible GLOBAL fallback; OpenAI receives at most 12 seconds; Anthropic receives the positive remainder capped at 8 seconds; Kimi receives at most 15 seconds. Assert attempt indices are exactly 0 then 1 and the final `GenerationResult.attempts` equals the observer-finished records in order.

Add this integration assertion for the 262145-byte boundary. The direct `adapter.prepare()` unit test in Task 3 still raises, but `ModelGateway.generate()` must normalize that exact local failure into a terminal-compatible result before `observer.started`:

```python
def test_262145_byte_payload_returns_zero_attempt_configuration_error():
    calls: list[str] = []
    oversized = request_with_serialized_body_size(262145)
    observer = RecordingObserver(calls)
    primary = KimiAdapter(
        config=kimi_config(),
        transport=RecordingTransport(calls),
    )

    result = gateway(kimi=primary).generate(
        oversized,
        RoutePolicy(DeploymentRegion.CN),
        observer=observer,
    )

    assert calls == []
    assert result.request_id == oversized.request_id
    assert result.status is GenerationStatus.CONFIGURATION_ERROR
    assert result.failure_code is FailureCode.INPUT_TOO_LARGE
    assert result.output is None
    assert result.selected_provider is None
    assert result.selected_model_id is None
    assert result.provider_request_id is None
    assert result.finish_reason is None
    assert result.usage is None
    assert result.input_digest == oversized.canonical_input_digest
    assert result.output_digest is None
    assert result.attempts == ()
```

The shared recorder is exactly empty, proving zero observer and zero transport calls through `ModelGateway.generate`; 262144 bytes remains allowed by the corresponding boundary test. Add a fallback-edge test in which OpenAI finishes with an eligible failure and Anthropic `prepare()` raises `INPUT_TOO_LARGE`: preserve the one finished OpenAI record, make zero Anthropic observer/transport calls, and return `CONFIGURATION_ERROR/INPUT_TOO_LARGE` with `selected_provider=None` and `selected_model_id=None`.

Add configuration branches: missing Kimi in CN and missing OpenAI in GLOBAL return `CONFIGURATION_ERROR/PROVIDER_UNCONFIGURED` with zero attempts; Anthropic alone is never called; and an eligible OpenAI failure with no Anthropic returns `UNAVAILABLE/BACKUP_UNCONFIGURED` after exactly one finished attempt.

- [ ] **Step 6: Implement deadline accounting**

Capture `started = monotonic()` once. Set `absolute_deadline = started + min(request.deadline_budget_s, 15.0)` for CN and `started + min(request.deadline_budget_s, 20.0)` for GLOBAL. Before each prepare/send, compute `remaining = absolute_deadline - monotonic()` and never start at `remaining <= 0`. Use `attempt_timeout = min(remaining, 15.0)` for Kimi, `min(remaining, 12.0)` for OpenAI, and `min(remaining, 8.0)` for Anthropic; set `attempt_deadline = monotonic_before_send + attempt_timeout`.

Call `adapter.prepare(request)` first. If and only if it raises `GatewayContractError` with `code is FailureCode.INPUT_TOO_LARGE`, return `CONFIGURATION_ERROR/INPUT_TOO_LARGE` with `selected_provider=None`, `selected_model_id=None`, and `attempts=tuple(completed_attempts)`; do not invoke the observer or transport for that provider, do not start another fallback, and do not hide any other contract/programming exception in this branch. The first-provider case is therefore the exact zero-attempt result pinned in Step 5, while a backup-prepare rejection retains the already finished primary record. Otherwise construct `AttemptStarted`, call `observer.started`, call `adapter.send(prepared, timeout_s=attempt_timeout)`, classify any post-return lateness, construct one `AttemptRecord`, and call `observer.finished` before deciding whether to return or fall back. If the response returns after `attempt_deadline`, discard decoded JSON and output digest and record retryable `UNAVAILABLE/READ_TIMEOUT`; this can trigger GLOBAL fallback when the overall deadline still has positive time. If it returns after `absolute_deadline`, record `UNAVAILABLE/DEADLINE_EXHAUSTED` and do not start another provider. The observer therefore receives the same final attempt record later included in `GenerationResult.attempts`.

Run only deadline tests and verify GREEN while fallback/observer tests remain red.

- [ ] **Step 7: Implement the closed fallback set**

Use exactly this constant and no broader `retryable` shortcut:

```python
GLOBAL_FALLBACK_CODES = frozenset({
    FailureCode.DNS_FAILURE,
    FailureCode.CONNECT_TIMEOUT,
    FailureCode.CONNECT_FAILED,
    FailureCode.READ_TIMEOUT,
    FailureCode.CONNECTION_INTERRUPTED,
    FailureCode.HTTP_TIMEOUT,
    FailureCode.RATE_LIMITED,
    FailureCode.PROVIDER_UNAVAILABLE,
})
```

Do not branch on response text, result quality, decoded object contents, or any downstream validator result. If fallback is eligible but absent, return `UNAVAILABLE/BACKUP_UNCONFIGURED`; if the positive remaining budget is exhausted, return `UNAVAILABLE/DEADLINE_EXHAUSTED` without starting Anthropic. Run the matrix and verify GREEN.

- [ ] **Step 8: Add and satisfy observer-crash and late-response tests**

Assert passing `observer=None` is rejected before `adapter.prepare`. Assert the exact normal order is `adapter.prepare`, `observer.started:0`, `adapter.send`, `observer.finished:0`. A `started` exception prevents `adapter.send`; a `finished` exception occurs after exactly one send and prevents Anthropic. A successful OpenAI response arriving after its 12-second attempt deadline but before the 20-second overall deadline is discarded, recorded as `READ_TIMEOUT`, and falls back with the positive remainder capped at 8 seconds. A successful OpenAI response arriving after the overall deadline is discarded, recorded as `DEADLINE_EXHAUSTED`, and does not fall back. `AttemptObserverError` must contain only request ID, attempt index/provider, and the phase (`started` or `finished`), never the original exception string. Run these tests and verify GREEN.

- [ ] **Step 9: Export routing only now and run the combined gateway tests**

Export `ModelGateway`, `RoutePolicy`, `RouteReadiness`, and `RouteReadinessStatus` from `nxt_model_gateway/__init__.py`. This is the first task allowed to expose `ModelGateway`.

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/model_gateway
```

Verify all tests pass and every matrix assertion checks the exact ordered provider list and timeout values.

- [ ] **Step 10: Commit routing**

```bash
git add simulation/nxt_model_gateway/routing.py simulation/nxt_model_gateway/__init__.py \
  simulation/tests/model_gateway/test_routing.py
git commit -m "feat(model-gateway): route regional model attempts"
```

### Task 5: Add package guards, packaging, CI, and stable architecture documentation

**Files:**
- Create: `simulation/tests/model_gateway/test_architecture.py`
- Create: `simulation/docs/model_gateway_v1.md`
- Modify: `simulation/pyproject.toml:7-10,47-84` (dependency and shipped-package ownership)
- Modify: `simulation/uv.lock:1-1544` (regenerated lock metadata only)
- Modify: `simulation/tests/pilot_ops/test_boundaries.py:10-32` (upstream/banned package sets)
- Modify: `simulation/tests/site_runtime/test_architecture.py:88-127` (reverse-dependency package set)
- Modify: `simulation/tests/agent_runtime/test_architecture.py:30-108` (banned/other package sets)
- Modify: `simulation/tests/edge_observation/test_architecture.py:19-82,142-162` (banned/other package sets)
- Modify: `simulation/tests/workflow_enablement/test_architecture.py:20-87,158-175` (banned/other package sets)
- Modify: `simulation/tests/course_world_model/test_architecture.py:19-88,164-185` (banned/other package sets)
- Modify: `simulation/tests/edge_task/test_architecture.py:117-135` (other package set)
- Modify: `simulation/tests/site_agent/test_architecture.py:15-119` (approved/banned/other package sets)
- Modify: `.github/workflows/verification.yml:150-258` (gateway suite, compile list, isolated-wheel imports)
- Modify: `docs/CI.md:95-197` (mirrored Python verification and wheel commands)
- Modify: `.agent/workflows/testing.md:15-115` (focused/architecture suites and AI evidence row)
- Modify: `.agent/context/package-map.md:18-63,85-100` (package row/count and verification owner)
- Modify: `.agent/context/architecture.md:158-207,244-293` (dependency map and mechanical guard table)
- Modify: `.agent/context/source-of-truth.md:11-35,145-170,190-220` (gateway truth and non-execution boundary)
- Modify: `.agent/context/deployment.md:11-116,214-258` (implementation status and deployment boundary)
- Modify: `.agent/context/product.md:21-31,33-90` (surface/vocabulary and honest scope)
- Modify: `.agent/workflows/architecture-review.md:25-74,99-157` (placement row and AI boundary card)
- Modify: `AGENTS.md:45-129,156-285,311-372` (non-negotiable boundary, package rules, verification)
- Modify: `docs/AGENT_OPERATING_MANUAL.md:70-87,216-261,298-340` (AI boundary, package owner, gate)
- Modify: `README.md:16-20,103-143,165-182` (layer/package map and honest limits)
- Modify: `simulation/README.md:37-66,109-116,166-177` (package map and architecture rule)
- Modify: `docs/ARCHITECTURE.md:86-192,230-248` (source-of-truth and package map)

**Interfaces:**
- Consumes: implemented gateway package and repository verification conventions.
- Produces: mechanical dependency/security guarantees, a shipped wheel package, reproducible dependency lock, and documented ownership.

- [ ] **Step 1: Add a failing architecture guard with negative controls**

The new guard must parse every gateway import and source token. Allow only:

```python
ALLOWED_STDLIB = {
    "__future__", "copy", "dataclasses", "enum", "hashlib", "http", "json",
    "math", "re", "socket", "ssl", "threading", "types", "typing",
}
ALLOWED_THIRD_PARTY = {"jsonschema"}

FORBIDDEN_DOMAIN_TOKENS = {
    "staffing", "roster", "shift", "employee", "facilitystate", "planning",
    "robot", "actuator", "directive", "dispatch", "safetyshield", "rclpy",
    "ros2", "ledger", "journal", "filesystem", "site_agent",
}
```

Reject every `nxt_*` import, provider SDK, environment/filesystem/process/random/UUID/wall-clock import, and the case-folded domain tokens above. Scan all other `simulation/nxt_*` packages and fail if they import or mention `nxt_model_gateway`; composition scripts are the only future allowed consumers. Use AST names for imports and identifier/comment-aware word matching for tokens so `model_id` does not trigger unrelated substrings.

- [ ] **Step 2: Add guard negative controls and run RED**

Prove the guard itself detects both classes of violation:

```python
def test_import_guard_negative_control():
    assert forbidden_imports("import nxt_facility\n") == {"nxt_facility"}

def test_domain_token_guard_negative_control():
    assert forbidden_tokens("def dispatch_robot_command():\n    return None\n") == {
        "dispatch", "robot"
    }

def test_neutral_vocabulary_negative_control_is_clean():
    assert forbidden_tokens("structured_output = {'result': []}\n") == set()
```

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/model_gateway/test_architecture.py
```

Expected: wheel registration/docs/reverse guards are not yet complete.

- [ ] **Step 3: Register the dependency and package**

In `pyproject.toml`, add exactly `"jsonschema>=4.26,<5"` to core dependencies and `"nxt_model_gateway"` to the Hatch package list. Add a comment naming it a stateless, provider-neutral network leaf that owns no domain or persistence semantics. Do not add a provider SDK or general HTTP dependency.

- [ ] **Step 4: Regenerate and verify only lock metadata**

```bash
cd simulation
uv lock
uv lock --check
```

Inspect `git diff -- simulation/uv.lock` and verify the only newly reachable production dependency is `jsonschema` and its resolver-selected transitive dependencies; no OpenAI, Anthropic, Moonshot, requests, or httpx package may appear.

- [ ] **Step 5a: Extend the Pilot Ops, Site Runtime, and Agent Runtime reverse guards**

Append the exact string `"nxt_model_gateway"` to the existing explicit tuple/set in `tests/pilot_ops/test_boundaries.py`, `tests/site_runtime/test_architecture.py`, and `tests/agent_runtime/test_architecture.py` at the ranges listed in Files. Do not change any existing member.

- [ ] **Step 5b: Extend the three pure-leaf reverse guards**

Append the exact string `"nxt_model_gateway"` to the corresponding `BANNED_IMPORT_ROOTS`/`OTHER_PACKAGES` collections in Edge Observation, Workflow Enablement, and Course World Model architecture tests without changing existing members.

- [ ] **Step 5c: Extend the Edge Task and Site Agent reverse guards**

Append `"nxt_model_gateway"` to Edge Task's `OTHER_PACKAGES` and to Site Agent's banned/other first-party collections. The Site Agent approved-import set must remain unchanged; gateway composition is script-only.

- [ ] **Step 6: Add the gateway suite to CI verification**

In `.github/workflows/verification.yml`, add a focused `tests/model_gateway` step after Site Agent, add `tests/model_gateway/test_architecture.py` to the architecture command, add `nxt_model_gateway` to `compileall`, and append it to the isolated `shipped` tuple. Mirror the exact same names and order in `docs/CI.md` and `.agent/workflows/testing.md`.

Run this drift check from the repository root:

```bash
rg -n "tests/model_gateway|nxt_model_gateway" \
  .github/workflows/verification.yml docs/CI.md .agent/workflows/testing.md
```

Expected: each of the three files names both the focused suite and architecture guard; CI/docs compile and wheel lists contain `nxt_model_gateway`.

- [ ] **Step 7: Run the architecture guard and verify GREEN**

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/model_gateway/test_architecture.py \
  tests/pilot_ops/test_boundaries.py \
  tests/site_runtime/test_architecture.py \
  tests/agent_runtime/test_architecture.py \
  tests/edge_observation/test_architecture.py \
  tests/workflow_enablement/test_architecture.py \
  tests/course_world_model/test_architecture.py \
  tests/edge_task/test_architecture.py \
  tests/site_agent/test_architecture.py
```

- [ ] **Step 8: Write the stable gateway contract document**

Write `simulation/docs/model_gateway_v1.md` with sections named `Public contracts`, `Fixed endpoints`, `Provider envelopes`, `Failure mapping`, `Regional routes`, `Deadlines`, `Attempt observer ordering`, `Redaction`, and `Non-goals`. Copy the exact endpoints, provider envelope keys, HTTP/failure table, fallback set, and time caps from Tasks 1-4; state that output is untrusted transient JSON, not a decision or command.

- [ ] **Step 9a: Update the canonical package maps and count**

Add one `nxt_model_gateway` row to `.agent/context/package-map.md` and `docs/ARCHITECTURE.md`. Change the shipped package count from 15 to 16 only where the count describes the Hatch wheel. The row must say: provider-neutral HTTPS generation, no domain semantics, no persistence, no first-party imports, and composition only in `simulation/scripts/`.

- [ ] **Step 9b: Update the two repository overviews**

Add the same bounded row to `README.md` and `simulation/README.md`, preserving their existing table vocabulary and making no deployment-readiness claim.

- [ ] **Step 10a: Update architecture, truth, deployment, and product context**

At the exact ranges listed in Files, update `.agent/context/architecture.md`, `source-of-truth.md`, `deployment.md`, and `product.md`. State consistently that provider results are untrusted transient proposals; model HTTPS is not physical telemetry/control; and keys enter only through composition-root injection.

- [ ] **Step 10b: Update governance workflows and operating manuals**

Update `.agent/workflows/architecture-review.md`, `AGENTS.md`, and `docs/AGENT_OPERATING_MANUAL.md` at the listed ranges. State that no model call enters Agent Runtime, Site Agent, Edge Task, or robot/control packages; production endpoints are private constants; and only `simulation/scripts/` may compose the gateway with a domain owner.

- [ ] **Step 11: Run a documentation consistency scan**

```bash
rg -n "nxt_model_gateway|16 packages|untrusted transient|composition root" \
  simulation/docs/model_gateway_v1.md .agent AGENTS.md \
  docs/AGENT_OPERATING_MANUAL.md README.md simulation/README.md docs/ARCHITECTURE.md
```

Inspect every hit and fix contradictory package counts or any claim that the gateway is a domain owner, persistence owner, telemetry source, or execution path.

- [ ] **Step 12: Run dependency, architecture, and focused regression checks**

```bash
cd simulation
uv sync --locked --all-extras
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/model_gateway tests/pilot_ops tests/site_agent
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/model_gateway/test_architecture.py \
  tests/pilot_ops/test_boundaries.py \
  tests/site_runtime/test_architecture.py \
  tests/agent_runtime/test_architecture.py \
  tests/edge_observation/test_architecture.py \
  tests/workflow_enablement/test_architecture.py \
  tests/course_world_model/test_architecture.py \
  tests/edge_task/test_architecture.py \
  tests/edge_task/test_scripts_guard.py \
  tests/site_agent/test_architecture.py
```

- [ ] **Step 13: Build and inspect the wheel**

```bash
cd simulation
build_dir="$(mktemp -d)"
uv build --out-dir "$build_dir"
uv run --no-sync python -B ../.github/scripts/verify_python_distribution.py "$build_dir"
```

Also install that one wheel into the repository's documented isolated environment command and assert `import nxt_model_gateway` succeeds while simulator-only tools remain absent.

- [ ] **Step 14: Commit package registration and documentation**

```bash
git add simulation/pyproject.toml simulation/uv.lock simulation/tests \
  simulation/docs/model_gateway_v1.md .github/workflows/verification.yml \
  docs/CI.md .agent AGENTS.md docs/AGENT_OPERATING_MANUAL.md README.md \
  simulation/README.md docs/ARCHITECTURE.md
git commit -m "docs(model-gateway): enforce package boundaries"
```

### Task 6: Close the gateway slice with full verification and self-review

**Files:**
- No edit is expected. If a verification command exposes a defect, first add the exact failing path and line range to this Files block, then edit only a file already introduced or listed in Tasks 1-5.

**Interfaces:**
- Consumes: the complete gateway slice.
- Produces: recorded verification evidence and a review-ready commit without live API traffic.

- [ ] **Step 1: Verify the lock and gateway suite**

```bash
cd simulation
uv lock --check
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/model_gateway
```

- [ ] **Step 2: Run the complete Python suite and config validation**

```bash
cd simulation
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider
uv run --no-sync python -B scripts/validate_configs.py
```

- [ ] **Step 3: Run package hygiene and secret scans**

From the repository root:

```bash
git diff --check
rg -n "MOONSHOT_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|Authorization: Bearer|x-api-key" \
  simulation/nxt_model_gateway simulation/tests/model_gateway
```

Expected: only deliberate header-name assertions appear; no secret values or unfinished implementation.

- [ ] **Step 4: Self-review every Review Focus item against named tests**

Record in the handoff which test covers TLS/proxy/redirect/size, each provider envelope, the full fallback table, observer crash ordering, late responses, redaction, reverse dependencies, and isolated-wheel import. If any item lacks a test, add that test and rerun the focused suite.

- [ ] **Step 5: Request an independent code review**

Use `superpowers:requesting-code-review`. Ask the reviewer to compare the diff with this plan and the approved spec, especially provider protocol fixtures, secret handling, the connection-failure map, and the no-fallback matrix.

- [ ] **Step 6: Address each validated review finding test-first**

For each accepted finding, add one narrowly failing test in the owning test file, run that file to show RED, make the smallest implementation change, and rerun the file to GREEN. If the fix needs a file not already in Tasks 1-5, stop and amend this plan before editing.

- [ ] **Step 7: Commit verification-only corrections when present**

```bash
git add simulation/nxt_model_gateway simulation/tests/model_gateway simulation/docs/model_gateway_v1.md
git commit -m "test(model-gateway): close routing and transport verification"
```

Skip this commit when the tree is already clean.
