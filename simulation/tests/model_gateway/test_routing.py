"""Regional orchestration, exercised without network or wall-clock time."""

from dataclasses import FrozenInstanceError, fields, replace
from enum import StrEnum
import inspect
import traceback

import pytest

from nxt_model_gateway import (
    AdapterOutcome, AttemptObserverError, AttemptRecord, AttemptStarted,
    DeploymentRegion, FailureCode, GatewayContractError, GenerationStatus,
    KimiAdapter, OpenAIAdapter, AnthropicAdapter, PreparedRequest, Provider,
    TokenUsage, stable_digest,
)
from nxt_model_gateway.routing import (
    ModelGateway, RoutePolicy, RouteReadiness, RouteReadinessStatus,
)
from nxt_model_gateway.transport import (
    _KIMI_ENDPOINT, _OPENAI_ENDPOINT, _ANTHROPIC_ENDPOINT,
)
from .conftest import RecordingTransport, config, request, success_bytes
from .test_kimi_adapter import request_with_serialized_body_size


class ForeignProvider(StrEnum):
    KIMI = "KIMI"
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"


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
    row for row in FAILURE_DISPOSITIONS if row[0] not in {
        FailureCode.INPUT_TOO_LARGE, FailureCode.BACKUP_UNCONFIGURED,
        FailureCode.DEADLINE_EXHAUSTED, FailureCode.PROVIDER_UNCONFIGURED,
    }
)


class Clock:
    def __init__(self, *ticks):
        self.now = 0.0
        self.ticks = iter(ticks)

    def __call__(self):
        self.now = next(self.ticks, self.now)
        return self.now


class RecordingObserver:
    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls
        self.finished_records = []
        self.started_records = []

    def started(self, attempt):
        self.calls.append(f"observer.started:{attempt.attempt_index}")
        self.started_records.append(attempt)

    def finished(self, attempt):
        self.calls.append(f"observer.finished:{attempt.attempt_index}")
        self.finished_records.append(attempt)


def outcome(code, status, security_failure, falls_back):
    return AdapterOutcome(
        status=status, decoded_json=None, failure_code=code,
        provider_request_id=None, finish_reason=None, usage=None,
        input_digest=request().canonical_input_digest, output_digest=None,
        retryable=not falls_back, security_failure=security_failure,
    )


def success():
    return AdapterOutcome(
        status=GenerationStatus.SUCCEEDED, decoded_json={"result": []},
        failure_code=None, provider_request_id="provider-001", finish_reason="stop",
        usage=TokenUsage(10, 4, 14), input_digest=request().canonical_input_digest,
        output_digest=stable_digest({"result": []}), retryable=False,
        security_failure=False,
    )


def eligible_failure():
    return outcome(FailureCode.DNS_FAILURE, GenerationStatus.UNAVAILABLE, False, True)


class FakeAdapter:
    def __init__(self, provider, result=None, calls=None, *, events=None,
                 clock=None, prepare_elapsed=0.0, send_elapsed=0.0, prepare_error=None):
        self.provider = provider
        self.model_id = provider.value.lower() + "-pinned"
        self.result = success() if result is None else result
        self.calls = [] if calls is None else calls
        self.events = [] if events is None else events
        self.timeouts = []
        self.clock = clock
        self.prepare_elapsed = prepare_elapsed
        self.send_elapsed = send_elapsed
        self.prepare_error = prepare_error
        self.prepared = None

    def prepare(self, source):
        self.events.append("adapter.prepare")
        if self.clock:
            self.clock.now += self.prepare_elapsed
        if self.prepare_error is not None:
            raise self.prepare_error
        endpoint = {
            Provider.KIMI: _KIMI_ENDPOINT, Provider.OPENAI: _OPENAI_ENDPOINT,
            Provider.ANTHROPIC: _ANTHROPIC_ENDPOINT,
        }[self.provider]
        self.prepared = PreparedRequest(
            source.request_id, self.provider, self.model_id, endpoint, {}, b"{}",
            source.output_schema, source.canonical_input_digest,
        )
        return self.prepared

    def send(self, prepared, *, timeout_s):
        assert prepared is self.prepared
        self.events.append("adapter.send")
        self.calls.append(self.provider)
        self.timeouts.append(timeout_s)
        if self.clock:
            self.clock.now += self.send_elapsed
        return self.result


def gateway(**kwargs):
    kwargs.setdefault("monotonic", Clock())
    return ModelGateway(**kwargs)


@pytest.mark.parametrize("region,kimi,openai,anthropic,status,primary,backup,code", [
    (DeploymentRegion.CN, False, False, False, RouteReadinessStatus.UNAVAILABLE,
     Provider.KIMI, None, FailureCode.PROVIDER_UNCONFIGURED),
    (DeploymentRegion.CN, True, True, True, RouteReadinessStatus.READY,
     Provider.KIMI, None, None),
    (DeploymentRegion.GLOBAL, False, False, False, RouteReadinessStatus.UNAVAILABLE,
     Provider.OPENAI, None, FailureCode.PROVIDER_UNCONFIGURED),
    (DeploymentRegion.GLOBAL, False, False, True, RouteReadinessStatus.UNAVAILABLE,
     Provider.OPENAI, Provider.ANTHROPIC, FailureCode.PROVIDER_UNCONFIGURED),
    (DeploymentRegion.GLOBAL, False, True, False,
     RouteReadinessStatus.DEGRADED_BACKUP_UNCONFIGURED,
     Provider.OPENAI, None, FailureCode.BACKUP_UNCONFIGURED),
    (DeploymentRegion.GLOBAL, True, True, True, RouteReadinessStatus.READY,
     Provider.OPENAI, Provider.ANTHROPIC, None),
])
def test_readiness_exact_records(region, kimi, openai, anthropic, status, primary, backup, code):
    configured = gateway(
        kimi=FakeAdapter(Provider.KIMI) if kimi else None,
        openai=FakeAdapter(Provider.OPENAI) if openai else None,
        anthropic=FakeAdapter(Provider.ANTHROPIC) if anthropic else None,
    )
    assert configured.readiness(RoutePolicy(region)) == RouteReadiness(
        region=region, status=status, primary_provider=primary,
        backup_provider=backup, failure_code=code,
    )


def test_route_contract_records_are_exact_frozen_and_slotted():
    assert [(item.name, item.value) for item in RouteReadinessStatus] == [
        ("READY", "READY"),
        ("DEGRADED_BACKUP_UNCONFIGURED", "DEGRADED_BACKUP_UNCONFIGURED"),
        ("UNAVAILABLE", "UNAVAILABLE"),
    ]
    assert tuple(f.name for f in fields(RoutePolicy)) == ("region",)
    assert tuple(f.name for f in fields(RouteReadiness)) == (
        "region", "status", "primary_provider", "backup_provider", "failure_code",
    )
    policy = RoutePolicy(DeploymentRegion.CN)
    ready = gateway().readiness(policy)
    for record in (policy, ready):
        assert not hasattr(record, "__dict__")
        with pytest.raises(FrozenInstanceError):
            record.region = DeploymentRegion.GLOBAL


@pytest.mark.parametrize("slot", ["kimi", "openai", "anthropic"])
def test_constructor_rejects_adapter_slot_mismatch(slot):
    wrong = Provider.KIMI if slot != "kimi" else Provider.OPENAI
    with pytest.raises(GatewayContractError) as error:
        gateway(**{slot: FakeAdapter(wrong)})
    assert error.value.code is FailureCode.PROVIDER_UNCONFIGURED
    assert error.value.detail == "adapter provider does not match configured slot"


@pytest.mark.parametrize("provider", list(Provider))
@pytest.mark.parametrize("representation", ["string", "foreign_enum"])
def test_constructor_rejects_same_value_non_provider_before_readiness(provider, representation):
    adapter = FakeAdapter(provider)
    adapter.provider = (
        provider.value if representation == "string" else ForeignProvider(provider.value)
    )
    # Construction must fail, so readiness can never report this slot READY.
    with pytest.raises(GatewayContractError) as caught:
        gateway(**{provider.value.lower(): adapter})
    assert caught.value.code is FailureCode.PROVIDER_UNCONFIGURED
    assert caught.value.detail == "adapter provider does not match configured slot"
    assert adapter.events == adapter.calls == adapter.timeouts == []


def test_constructor_exposes_only_keyword_dependencies():
    parameters = inspect.signature(ModelGateway).parameters
    assert tuple(parameters) == ("kimi", "openai", "anthropic", "monotonic")
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())


def assert_identity(result, observer):
    assert result.attempts == tuple(observer.finished_records)
    assert [a.attempt_index for a in result.attempts] == list(range(len(result.attempts)))
    for start, record in zip(observer.started_records, result.attempts, strict=True):
        assert (start.request_id, start.provider, start.model_id, start.input_digest,
                start.attempt_index) == (
            record.request_id, record.provider, record.model_id, record.input_digest,
            record.attempt_index,
        )
        assert record.request_id == result.request_id
        assert record.input_digest == result.input_digest


@pytest.mark.parametrize("failure_code,expected_status,expected_security_failure,falls_back",
                         FAILURE_DISPOSITIONS)
def test_global_fallback_matrix(failure_code, expected_status, expected_security_failure, falls_back):
    calls, events = [], []
    observer = RecordingObserver(events)
    primary = FakeAdapter(Provider.OPENAI, outcome(
        failure_code, expected_status, expected_security_failure, falls_back,
    ), calls, events=events)
    backup = FakeAdapter(Provider.ANTHROPIC, success(), calls, events=events)
    result = gateway(openai=primary, anthropic=backup).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )
    assert calls == ([Provider.OPENAI, Provider.ANTHROPIC] if falls_back else [Provider.OPENAI])
    assert primary.timeouts == [12.0]
    assert backup.timeouts == ([8.0] if falls_back else [])
    first = observer.finished_records[0]
    assert (first.status, first.failure_code, first.security_failure) == (
        expected_status, failure_code, expected_security_failure,
    )
    assert result.status is (GenerationStatus.SUCCEEDED if falls_back else expected_status)
    assert result.failure_code is (None if falls_back else failure_code)
    assert result.selected_provider is (Provider.ANTHROPIC if falls_back else Provider.OPENAI)
    assert events == (
        ["adapter.prepare", "observer.started:0", "adapter.send", "observer.finished:0"]
        + (["adapter.prepare", "observer.started:1", "adapter.send", "observer.finished:1"]
           if falls_back else [])
    )
    assert_identity(result, observer)


@pytest.mark.parametrize("failure_code,expected_status,expected_security_failure,falls_back",
                         PROVIDER_OUTCOME_DISPOSITIONS)
def test_anthropic_outcome_is_terminal(failure_code, expected_status, expected_security_failure, falls_back):
    calls = []
    observer = RecordingObserver()
    primary = FakeAdapter(Provider.OPENAI, eligible_failure(), calls)
    backup = FakeAdapter(Provider.ANTHROPIC, outcome(
        failure_code, expected_status, expected_security_failure, falls_back,
    ), calls)
    result = gateway(openai=primary, anthropic=backup).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )
    assert calls == [Provider.OPENAI, Provider.ANTHROPIC]
    assert primary.timeouts == [12.0]
    assert backup.timeouts == [8.0]
    assert len(result.attempts) == 2
    assert result.status is expected_status
    assert result.failure_code is failure_code
    assert result.attempts[-1].security_failure is expected_security_failure
    assert result.selected_provider is Provider.ANTHROPIC
    assert_identity(result, observer)


@pytest.mark.parametrize("adapter_class,provider", [
    (KimiAdapter, Provider.KIMI), (OpenAIAdapter, Provider.OPENAI),
    (AnthropicAdapter, Provider.ANTHROPIC),
])
@pytest.mark.parametrize("http_status,code", [
    (401, FailureCode.AUTHENTICATION_FAILED), (403, FailureCode.PERMISSION_DENIED),
    (404, FailureCode.MODEL_NOT_FOUND), (408, FailureCode.HTTP_TIMEOUT),
    (429, FailureCode.RATE_LIMITED), (500, FailureCode.PROVIDER_UNAVAILABLE),
    (502, FailureCode.PROVIDER_UNAVAILABLE), (503, FailureCode.PROVIDER_UNAVAILABLE),
    (599, FailureCode.PROVIDER_UNAVAILABLE), (400, FailureCode.INVALID_PROVIDER_REQUEST),
    (409, FailureCode.INVALID_PROVIDER_REQUEST), (422, FailureCode.INVALID_PROVIDER_REQUEST),
    (499, FailureCode.PROVIDER_CLIENT_ERROR),
])
def test_adapter_http_classification_matches_route_table(adapter_class, provider, http_status, code):
    transport = RecordingTransport(status=http_status, body=b"secret provider body")
    adapter = adapter_class(config=config(provider), transport=transport)
    result = adapter.send(adapter.prepare(request()), timeout_s=1.5)
    _, status, security, _ = next(row for row in FAILURE_DISPOSITIONS if row[0] is code)
    assert (result.failure_code, result.status, result.security_failure) == (code, status, security)
    assert transport.calls == ["transport.post"]
    assert transport.sent.timeout_s == 1.5


@pytest.mark.parametrize("failure_code,status,security,falls_back", FAILURE_DISPOSITIONS)
def test_cn_every_disposition_stays_single_kimi(failure_code, status, security, falls_back):
    calls = []
    observer = RecordingObserver()
    kimi = FakeAdapter(Provider.KIMI, outcome(failure_code, status, security, falls_back), calls)
    openai = FakeAdapter(Provider.OPENAI, calls=calls)
    anthropic = FakeAdapter(Provider.ANTHROPIC, calls=calls)
    result = gateway(kimi=kimi, openai=openai, anthropic=anthropic).generate(
        request(), RoutePolicy(DeploymentRegion.CN), observer=observer,
    )
    assert calls == [Provider.KIMI]
    assert kimi.timeouts == [15.0]
    assert openai.timeouts == anthropic.timeouts == []
    assert (result.status, result.failure_code, result.attempts[0].security_failure) == (
        status, failure_code, security,
    )
    assert result.selected_provider is Provider.KIMI
    assert len(result.attempts) == 1
    assert_identity(result, observer)


@pytest.mark.parametrize("region,provider,slot,budget,elapsed,timeout", [
    (DeploymentRegion.CN, Provider.KIMI, "kimi", 100.0, 0.0, 15.0),
    (DeploymentRegion.GLOBAL, Provider.OPENAI, "openai", 100.0, 0.0, 12.0),
    (DeploymentRegion.CN, Provider.KIMI, "kimi", 4.0, 0.0, 4.0),
    (DeploymentRegion.GLOBAL, Provider.OPENAI, "openai", 4.0, 0.0, 4.0),
    (DeploymentRegion.CN, Provider.KIMI, "kimi", 4.0, 1.5, 2.5),
    (DeploymentRegion.GLOBAL, Provider.OPENAI, "openai", 4.0, 1.5, 2.5),
])
def test_deadline_timeout_caps_and_prepare_time(region, provider, slot, budget, elapsed, timeout):
    clock = Clock()
    adapter = FakeAdapter(provider, clock=clock, prepare_elapsed=elapsed)
    observer = RecordingObserver()
    source = replace(request(), deadline_budget_s=budget)
    result = gateway(**{slot: adapter}, monotonic=clock).generate(
        source, RoutePolicy(region), observer=observer,
    )
    assert result.status is GenerationStatus.SUCCEEDED
    assert adapter.timeouts == [timeout]
    assert observer.started_records[0].timeout_s == timeout
    assert len(result.attempts) == 1
    assert_identity(result, observer)


@pytest.mark.parametrize("region,provider,slot,limit", [
    (DeploymentRegion.CN, Provider.KIMI, "kimi", 15.0),
    (DeploymentRegion.GLOBAL, Provider.OPENAI, "openai", 20.0),
])
def test_deadline_exhausted_before_prepare_has_zero_attempts(region, provider, slot, limit):
    events = []
    adapter = FakeAdapter(provider, events=events)
    observer = RecordingObserver(events)
    result = gateway(**{slot: adapter}, monotonic=Clock(100.0, 100.0 + limit)).generate(
        replace(request(), deadline_budget_s=100.0), RoutePolicy(region), observer=observer,
    )
    assert result.status is GenerationStatus.UNAVAILABLE
    assert result.failure_code is FailureCode.DEADLINE_EXHAUSTED
    assert result.selected_provider is result.selected_model_id is None
    assert result.attempts == ()
    assert events == adapter.calls == []


def test_deadline_exhausted_during_prepare_has_zero_attempts():
    clock = Clock()
    events = []
    adapter = FakeAdapter(Provider.KIMI, clock=clock, prepare_elapsed=15.0, events=events)
    result = gateway(kimi=adapter, monotonic=clock).generate(
        request(), RoutePolicy(DeploymentRegion.CN), observer=RecordingObserver(events),
    )
    assert result.failure_code is FailureCode.DEADLINE_EXHAUSTED
    assert result.attempts == ()
    assert adapter.calls == []
    assert events == ["adapter.prepare"]


@pytest.mark.parametrize("region,provider,slot,elapsed", [
    (DeploymentRegion.CN, Provider.KIMI, "kimi", 15.01),
    (DeploymentRegion.GLOBAL, Provider.OPENAI, "openai", 20.01),
])
def test_deadline_late_return_discards_success(region, provider, slot, elapsed):
    clock = Clock()
    adapter = FakeAdapter(provider, clock=clock, send_elapsed=elapsed)
    observer = RecordingObserver()
    result = gateway(**{slot: adapter}, monotonic=clock).generate(
        replace(request(), deadline_budget_s=100.0), RoutePolicy(region), observer=observer,
    )
    assert result.status is GenerationStatus.UNAVAILABLE
    assert result.failure_code is FailureCode.DEADLINE_EXHAUSTED
    assert result.output is result.output_digest is None
    assert result.attempts[0].output_digest is None
    assert result.attempts[0].retryable is False
    assert result.selected_provider is provider
    assert len(result.attempts) == 1
    assert_identity(result, observer)


@pytest.mark.parametrize("region,configured", [
    (DeploymentRegion.CN, {}),
    (DeploymentRegion.GLOBAL, {}),
    (DeploymentRegion.GLOBAL, {"anthropic": Provider.ANTHROPIC}),
])
def test_missing_primary_is_zero_attempt_configuration_error(region, configured):
    events, calls = [], []
    adapters = {slot: FakeAdapter(provider, events=events, calls=calls)
                for slot, provider in configured.items()}
    result = gateway(**adapters).generate(
        request(), RoutePolicy(region), observer=RecordingObserver(events),
    )
    assert (result.status, result.failure_code) == (
        GenerationStatus.CONFIGURATION_ERROR, FailureCode.PROVIDER_UNCONFIGURED,
    )
    assert result.selected_provider is result.selected_model_id is None
    assert result.attempts == ()
    assert events == calls == []


def test_262145_byte_payload_returns_zero_attempt_configuration_error():
    calls = []
    oversized = request_with_serialized_body_size(262145)
    observer = RecordingObserver(calls)
    primary = KimiAdapter(config=config(Provider.KIMI), transport=RecordingTransport(calls))
    result = gateway(kimi=primary).generate(
        oversized, RoutePolicy(DeploymentRegion.CN), observer=observer,
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


def test_262144_byte_payload_remains_allowed_through_gateway():
    calls = []
    source = request_with_serialized_body_size(262144)
    observer = RecordingObserver(calls)
    transport = RecordingTransport(calls, body=success_bytes(Provider.KIMI))
    primary = KimiAdapter(config=config(Provider.KIMI), transport=transport)
    result = gateway(kimi=primary).generate(
        source, RoutePolicy(DeploymentRegion.CN), observer=observer,
    )
    assert result.status is GenerationStatus.SUCCEEDED
    assert len(transport.sent.body) == 262144
    assert transport.sent.timeout_s == 15.0
    assert calls == ["observer.started:0", "transport.post", "observer.finished:0"]
    assert_identity(result, observer)


@pytest.mark.parametrize("error", [
    GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "programming defect"),
    ValueError("programming defect"),
])
def test_prepare_other_exceptions_propagate_unchanged(error):
    events = []
    adapter = FakeAdapter(Provider.KIMI, events=events, prepare_error=error)
    with pytest.raises(type(error)) as caught:
        gateway(kimi=adapter).generate(
            request(), RoutePolicy(DeploymentRegion.CN), observer=RecordingObserver(events),
        )
    assert caught.value is error
    assert events == ["adapter.prepare"]
    assert adapter.calls == []


def test_backup_prepare_rejection_preserves_finished_primary():
    events, calls = [], []
    observer = RecordingObserver(events)
    primary = FakeAdapter(Provider.OPENAI, eligible_failure(), calls, events=events)
    backup = FakeAdapter(Provider.ANTHROPIC, calls=calls, events=events,
                         prepare_error=GatewayContractError(
                             FailureCode.INPUT_TOO_LARGE, "request exceeds byte limit"))
    result = gateway(openai=primary, anthropic=backup).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )
    assert (result.status, result.failure_code) == (
        GenerationStatus.CONFIGURATION_ERROR, FailureCode.INPUT_TOO_LARGE,
    )
    assert result.selected_provider is result.selected_model_id is None
    assert result.output is result.output_digest is None
    assert result.provider_request_id is result.finish_reason is result.usage is None
    assert calls == [Provider.OPENAI]
    assert primary.timeouts == [12.0]
    assert backup.timeouts == []
    assert events == ["adapter.prepare", "observer.started:0", "adapter.send",
                      "observer.finished:0", "adapter.prepare"]
    assert len(result.attempts) == 1
    assert_identity(result, observer)


def test_eligible_primary_without_backup_is_unavailable():
    primary = FakeAdapter(Provider.OPENAI, eligible_failure())
    observer = RecordingObserver()
    result = gateway(openai=primary).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )
    assert (result.status, result.failure_code) == (
        GenerationStatus.UNAVAILABLE, FailureCode.BACKUP_UNCONFIGURED,
    )
    assert primary.calls == [Provider.OPENAI]
    assert primary.timeouts == [12.0]
    assert len(result.attempts) == 1
    assert_identity(result, observer)


@pytest.mark.parametrize("budget,primary_elapsed,backup_timeout", [
    (100.0, 1.0, 8.0), (20.0, 11.0, 8.0), (14.0, 11.0, 3.0),
    (4.0, 3.75, 0.25),
])
def test_fallback_deadline_uses_positive_remainder(budget, primary_elapsed, backup_timeout):
    clock = Clock()
    calls = []
    primary = FakeAdapter(Provider.OPENAI, eligible_failure(), calls,
                          clock=clock, send_elapsed=primary_elapsed)
    backup = FakeAdapter(Provider.ANTHROPIC, calls=calls, clock=clock)
    observer = RecordingObserver()
    result = gateway(openai=primary, anthropic=backup, monotonic=clock).generate(
        replace(request(), deadline_budget_s=budget), RoutePolicy(DeploymentRegion.GLOBAL),
        observer=observer,
    )
    assert result.status is GenerationStatus.SUCCEEDED
    assert calls == [Provider.OPENAI, Provider.ANTHROPIC]
    assert primary.timeouts == [min(budget, 12.0)]
    assert backup.timeouts == [backup_timeout]
    assert [a.timeout_s for a in observer.started_records] == [
        min(budget, 12.0), backup_timeout,
    ]
    assert_identity(result, observer)


def test_fallback_deadline_exhausted_after_finished_prevents_backup_prepare():
    clock = Clock()
    calls, events = [], []
    primary = FakeAdapter(Provider.OPENAI, eligible_failure(), calls, events=events)
    backup = FakeAdapter(Provider.ANTHROPIC, calls=calls, events=events)

    class SlowObserver(RecordingObserver):
        def finished(self, attempt):
            super().finished(attempt)
            clock.now = 20.0

    observer = SlowObserver(events)
    result = gateway(openai=primary, anthropic=backup, monotonic=clock).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )
    assert result.failure_code is FailureCode.DEADLINE_EXHAUSTED
    assert result.output is None
    assert calls == [Provider.OPENAI]
    assert events == ["adapter.prepare", "observer.started:0", "adapter.send", "observer.finished:0"]
    assert len(result.attempts) == 1
    assert_identity(result, observer)


def test_observer_is_required_keyword_only():
    observer_parameter = inspect.signature(ModelGateway.generate).parameters["observer"]
    assert observer_parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert observer_parameter.default is inspect.Parameter.empty
    adapter = FakeAdapter(Provider.KIMI)
    model_gateway = gateway(kimi=adapter)
    with pytest.raises(TypeError):
        model_gateway.generate(request(), RoutePolicy(DeploymentRegion.CN))
    with pytest.raises(TypeError):
        model_gateway.generate(request(), RoutePolicy(DeploymentRegion.CN), RecordingObserver())
    assert adapter.events == []


def test_observer_none_rejected_before_prepare():
    adapter = FakeAdapter(Provider.KIMI)
    with pytest.raises(GatewayContractError) as caught:
        gateway(kimi=adapter).generate(
            request(), RoutePolicy(DeploymentRegion.CN), observer=None,
        )
    assert caught.value.code is FailureCode.INVALID_PROVIDER_REQUEST
    assert adapter.events == []


@pytest.mark.parametrize("phase", ["started", "finished"])
def test_observer_crash_is_secret_safe_and_stops_route(phase):
    calls, events = [], []

    class CrashingObserver(RecordingObserver):
        def started(self, attempt):
            super().started(attempt)
            if phase == "started":
                raise ValueError("secret-observer-token")

        def finished(self, attempt):
            super().finished(attempt)
            if phase == "finished":
                raise ValueError("secret-observer-token")

    primary = FakeAdapter(Provider.OPENAI, eligible_failure(), calls, events=events)
    backup = FakeAdapter(Provider.ANTHROPIC, calls=calls, events=events)
    with pytest.raises(AttemptObserverError) as caught:
        gateway(openai=primary, anthropic=backup).generate(
            request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=CrashingObserver(events),
        )
    error = caught.value
    assert (error.request_id, error.attempt_index, error.provider, error.phase) == (
        "req-1", 0, Provider.OPENAI, phase,
    )
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(error))
    assert "secret-observer-token" not in str(error)
    assert "secret-observer-token" not in repr(error)
    assert "secret-observer-token" not in rendered
    assert calls == ([] if phase == "started" else [Provider.OPENAI])
    assert primary.timeouts == ([] if phase == "started" else [12.0])
    assert backup.timeouts == []
    assert events == (
        ["adapter.prepare", "observer.started:0"] if phase == "started" else
        ["adapter.prepare", "observer.started:0", "adapter.send", "observer.finished:0"]
    )


@pytest.mark.parametrize("phase", ["started", "finished"])
def test_observer_crash_has_no_context_inside_callers_exception_handler(phase):
    calls, events = [], []

    class CrashingObserver(RecordingObserver):
        def started(self, attempt):
            super().started(attempt)
            if phase == "started":
                raise ValueError("inner-observer-secret")

        def finished(self, attempt):
            super().finished(attempt)
            if phase == "finished":
                raise ValueError("inner-observer-secret")

    primary = FakeAdapter(Provider.OPENAI, eligible_failure(), calls, events=events)
    backup = FakeAdapter(Provider.ANTHROPIC, calls=calls, events=events)
    model_gateway = gateway(openai=primary, anthropic=backup)
    observer = CrashingObserver(events)
    try:
        raise ValueError("outer-secret")
    except ValueError:
        with pytest.raises(AttemptObserverError) as caught:
            model_gateway.generate(
                request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
            )
    error = caught.value
    assert (error.request_id, error.attempt_index, error.provider, error.phase) == (
        "req-1", 0, Provider.OPENAI, phase,
    )
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(error))
    for secret in ("outer-secret", "inner-observer-secret"):
        assert secret not in str(error)
        assert secret not in repr(error)
        assert secret not in rendered
    assert calls == ([] if phase == "started" else [Provider.OPENAI])
    assert primary.timeouts == ([] if phase == "started" else [12.0])
    assert backup.timeouts == []
    assert events == (
        ["adapter.prepare", "observer.started:0"] if phase == "started" else
        ["adapter.prepare", "observer.started:0", "adapter.send", "observer.finished:0"]
    )


@pytest.mark.parametrize("elapsed,code,providers,backup_timeout", [
    (12.001, FailureCode.READ_TIMEOUT, [Provider.OPENAI, Provider.ANTHROPIC], 7.999),
    (13.0, FailureCode.READ_TIMEOUT, [Provider.OPENAI, Provider.ANTHROPIC], 7.0),
    (20.001, FailureCode.DEADLINE_EXHAUSTED, [Provider.OPENAI], None),
])
def test_late_primary_success_is_discarded_before_fallback(elapsed, code, providers, backup_timeout):
    calls, events = [], []
    clock = Clock()
    observer = RecordingObserver(events)
    primary = FakeAdapter(Provider.OPENAI, success(), calls, events=events,
                          clock=clock, send_elapsed=elapsed)
    backup = FakeAdapter(Provider.ANTHROPIC, calls=calls, events=events, clock=clock)
    result = gateway(openai=primary, anthropic=backup, monotonic=clock).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )
    assert calls == providers
    assert primary.timeouts == [12.0]
    assert backup.timeouts == ([] if backup_timeout is None else [pytest.approx(backup_timeout)])
    first = result.attempts[0]
    assert (first.status, first.failure_code, first.retryable, first.security_failure) == (
        GenerationStatus.UNAVAILABLE, code, code is FailureCode.READ_TIMEOUT, False,
    )
    assert first.output_digest is first.provider_request_id is first.finish_reason is first.usage is None
    if backup_timeout is None:
        assert result.output is result.output_digest is None
        assert result.status is GenerationStatus.UNAVAILABLE
        assert result.failure_code is FailureCode.DEADLINE_EXHAUSTED
    else:
        assert result.status is GenerationStatus.SUCCEEDED
        assert result.selected_provider is Provider.ANTHROPIC
    assert_identity(result, observer)


@pytest.mark.parametrize("elapsed,code", [
    (8.001, FailureCode.READ_TIMEOUT),
    (20.001, FailureCode.DEADLINE_EXHAUSTED),
])
def test_late_backup_success_is_terminal_and_discarded(elapsed, code):
    clock = Clock()
    calls = []
    primary = FakeAdapter(Provider.OPENAI, eligible_failure(), calls, clock=clock)
    backup = FakeAdapter(Provider.ANTHROPIC, calls=calls, clock=clock, send_elapsed=elapsed)
    observer = RecordingObserver()
    result = gateway(openai=primary, anthropic=backup, monotonic=clock).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )
    assert calls == [Provider.OPENAI, Provider.ANTHROPIC]
    assert primary.timeouts == [12.0]
    assert backup.timeouts == [8.0]
    assert result.status is GenerationStatus.UNAVAILABLE
    assert result.failure_code is code
    assert result.output is result.output_digest is None
    assert result.selected_provider is Provider.ANTHROPIC
    assert len(result.attempts) == 2
    assert_identity(result, observer)


def test_success_content_does_not_select_fallback():
    answer = {"result": ["rate limited, refusal, unavailable; poor downstream quality"]}
    primary = FakeAdapter(Provider.OPENAI, replace(
        success(), decoded_json=answer, output_digest=stable_digest(answer), retryable=True,
    ))
    backup = FakeAdapter(Provider.ANTHROPIC)
    observer = RecordingObserver()
    result = gateway(openai=primary, anthropic=backup).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )
    assert result.status is GenerationStatus.SUCCEEDED
    assert result.output["result"] == tuple(answer["result"])
    assert result.selected_provider is Provider.OPENAI
    assert result.selected_model_id == "openai-pinned"
    assert primary.calls == [Provider.OPENAI]
    assert primary.timeouts == [12.0]
    assert backup.events == []
    assert result.provider_request_id == "provider-001"
    assert result.finish_reason == "stop"
    assert result.usage == TokenUsage(10, 4, 14)
    assert result.output_digest == stable_digest(answer)
    assert_identity(result, observer)


@pytest.mark.parametrize("field,bad_value", [
    ("request_id", "wrong-request"),
    ("provider", Provider.ANTHROPIC),
    ("model_id", "wrong-model"),
    ("input_digest", "b" * 64),
])
def test_prepared_identity_mismatch_fails_before_observer(field, bad_value):
    events = []

    class WrongIdentityAdapter(FakeAdapter):
        def prepare(self, source):
            prepared = super().prepare(source)
            self.prepared = replace(prepared, **{field: bad_value})
            return self.prepared

    adapter = WrongIdentityAdapter(Provider.OPENAI, events=events)
    with pytest.raises(GatewayContractError):
        gateway(openai=adapter).generate(
            request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=RecordingObserver(events),
        )
    assert events == ["adapter.prepare"]
    assert adapter.calls == []


@pytest.mark.parametrize("provider", ["OPENAI", ForeignProvider.OPENAI])
def test_prepared_provider_requires_enum_identity_before_observer(provider):
    events = []

    class WrongProviderTypeAdapter(FakeAdapter):
        def prepare(self, source):
            return replace(super().prepare(source), provider=provider)

    adapter = WrongProviderTypeAdapter(Provider.OPENAI, events=events)
    with pytest.raises(GatewayContractError) as caught:
        gateway(openai=adapter).generate(
            request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=RecordingObserver(events),
        )
    assert caught.value.code is FailureCode.INVALID_PROVIDER_REQUEST
    assert caught.value.detail == "prepared request identity does not match route"
    assert events == ["adapter.prepare"]
    assert adapter.calls == adapter.timeouts == []


def test_outcome_input_digest_mismatch_fails_before_finished_or_fallback():
    events, calls = [], []
    primary = FakeAdapter(Provider.OPENAI, replace(eligible_failure(), input_digest="b" * 64),
                          calls, events=events)
    backup = FakeAdapter(Provider.ANTHROPIC, calls=calls, events=events)
    with pytest.raises(GatewayContractError):
        gateway(openai=primary, anthropic=backup).generate(
            request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=RecordingObserver(events),
        )
    assert calls == [Provider.OPENAI]
    assert events == ["adapter.prepare", "observer.started:0", "adapter.send"]


@pytest.mark.parametrize("observer_elapsed,send_elapsed", [(15.0, 4.0), (10.0, 9.0)])
def test_observer_time_reduces_actual_send_budget(observer_elapsed, send_elapsed):
    clock = Clock()
    observer = RecordingObserver()
    original_started = observer.started

    def advance_after_started(attempt):
        original_started(attempt)
        clock.now += observer_elapsed

    observer.started = advance_after_started
    primary = FakeAdapter(Provider.OPENAI, clock=clock, send_elapsed=send_elapsed)
    backup = FakeAdapter(Provider.ANTHROPIC, clock=clock)
    result = gateway(openai=primary, anthropic=backup, monotonic=clock).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )
    assert observer.started_records[0].timeout_s == 12.0
    assert primary.timeouts == [20.0 - observer_elapsed]
    assert result.status is GenerationStatus.SUCCEEDED
    assert primary.calls == [Provider.OPENAI]
    assert backup.events == []
    assert_identity(result, observer)


def test_observer_exhausts_deadline_finishes_started_attempt_without_send():
    clock = Clock()
    calls, events = [], []

    class ExhaustingObserver(RecordingObserver):
        def started(self, attempt):
            super().started(attempt)
            clock.now = 20.0

    primary = FakeAdapter(Provider.OPENAI, calls=calls, events=events, clock=clock)
    backup = FakeAdapter(Provider.ANTHROPIC, calls=calls, events=events, clock=clock)
    observer = ExhaustingObserver(events)
    result = gateway(openai=primary, anthropic=backup, monotonic=clock).generate(
        request(), RoutePolicy(DeploymentRegion.GLOBAL), observer=observer,
    )
    assert (result.status, result.failure_code) == (
        GenerationStatus.UNAVAILABLE, FailureCode.DEADLINE_EXHAUSTED,
    )
    assert calls == primary.timeouts == backup.timeouts == []
    assert events == ["adapter.prepare", "observer.started:0", "observer.finished:0"]
    assert len(result.attempts) == 1
    assert result.selected_provider is Provider.OPENAI
    assert result.output is result.output_digest is None
    assert_identity(result, observer)


def test_routing_public_exports_are_available():
    import nxt_model_gateway

    for value in (ModelGateway, RoutePolicy, RouteReadiness, RouteReadinessStatus):
        assert getattr(nxt_model_gateway, value.__name__) is value
        assert value.__name__ in nxt_model_gateway.__all__


class ForeignRegion(StrEnum):
    CN = "CN"


@pytest.mark.parametrize("region", [
    "CN", "GLOBAL", None, "unknown-secret-region", ForeignRegion.CN, object(), 1, True,
])
def test_route_policy_rejects_non_deployment_regions_before_routing(region):
    # Construction itself must fail: readiness/generate cannot receive a policy.
    with pytest.raises(GatewayContractError) as caught:
        RoutePolicy(region)
    assert caught.value.code is FailureCode.PROVIDER_UNCONFIGURED
    assert caught.value.detail == "invalid deployment region"
    assert "unknown-secret-region" not in str(caught.value)
