"""Composition tests for the bounded staffing advisory service."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from nxt_model_gateway import (
    AdapterOutcome,
    AttemptRecord,
    AttemptStarted,
    DeploymentRegion,
    FailureCode,
    GatewayContractError,
    GenerationResult,
    GenerationStatus,
    Provider,
    ProviderConfig,
    RoutePolicy,
    RouteReadiness,
    RouteReadinessStatus,
    TokenUsage,
    stable_digest,
)
from nxt_model_gateway.adapters import PreparedRequest
from nxt_model_gateway.anthropic import AnthropicAdapter
from nxt_model_gateway.kimi import KimiAdapter
from nxt_model_gateway.openai import OpenAIAdapter
from nxt_model_gateway.routing import ModelGateway
from nxt_model_gateway.transport import HttpResponse
from nxt_pilot_ops.staffing.contracts import (
    AssignmentProjection,
    AttemptFinishedEvidence,
    AttemptRouteEvidence,
    AttemptStartedEvidence,
    CandidateOperationProjection,
    CandidateProjection,
    ConflictReceipt,
    CoverageGap,
    DateProjection,
    EffectivePlanState,
    ExceptionCommittedProjection,
    ExceptionProjection,
    GenerationCommittedProjection,
    GenerationProjectionView,
    ManagerCommittedProjection,
    ManagerResponseProjection,
    RequestProjection,
    ResultEvidence,
    RosterCommittedProjection,
    StaffingError,
)
from nxt_pilot_ops.staffing.ledger import StaffingLedger
from nxt_pilot_ops.staffing.operations import StaffingOperations
from nxt_pilot_ops.staffing.prompt import PROMPT_TEMPLATE_VERSION
from nxt_site_agent import SiteAgentApiServer, SiteAgentError
from scripts.staffing_operations import (
    BoundedGenerationWorker,
    ConfiguredGateway,
    LedgerAttemptObserver,
    StaffingApiOperations,
    StaffingProjectionError,
    StaffingProviderSettings,
    StaffingRouteAdapter,
    StaffingShutdownError,
    GenerationWorkItem,
    attempt_route,
    build_staffing_operations,
    candidate_to_wire,
    configured_gateway,
    coverage_gap_to_wire,
    finished_evidence,
    generation_capability,
    generation_route_evidence,
    load_provider_settings,
    offset_time,
    project_date_to_wire,
    project_request_to_wire,
    provenance_to_wire,
    public_domain_error,
    resolve_staffing_root,
    result_evidence,
    started_evidence,
    staffing_error_for_conflict,
    utc_time,
    with_runtime_context,
)


NOW = datetime(2026, 10, 6, 1, 2, 3, 4, tzinfo=timezone.utc)
CONTRACT = Path(__file__).resolve().parents[2] / "docs/contracts/staffing-v1"
SCHEMA = json.loads((CONTRACT / "schema.json").read_text(encoding="utf-8"))
EXAMPLES = CONTRACT / "examples"
FORMAT_CHECKER = FormatChecker()


def validator(reference: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": reference, "$defs": SCHEMA["$defs"]},
        format_checker=FORMAT_CHECKER,
    )


def exchange_request(filename: str, name: str) -> dict[str, object]:
    document = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
    return copy.deepcopy(next(row["request"] for row in document["exchanges"] if row["name"] == name))


def cn_settings(*, key: str | None = "moonshot-secret") -> StaffingProviderSettings:
    return StaffingProviderSettings(
        region=DeploymentRegion.CN,
        language="zh-CN",
        kimi_model="kimi-model",
        openai_model=None,
        anthropic_model=None,
        moonshot_api_key=key,
        openai_api_key=None,
        anthropic_api_key=None,
    )


def global_settings(
    *, openai_key: str | None = "openai-secret", anthropic_key: str | None = "anthropic-secret"
) -> StaffingProviderSettings:
    return StaffingProviderSettings(
        region=DeploymentRegion.GLOBAL,
        language="en",
        kimi_model=None,
        openai_model="gpt-model",
        anthropic_model="claude-model",
        moonshot_api_key=None,
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
    )


def configured_for(
    settings: StaffingProviderSettings, *, gateway: object | None = None
) -> ConfiguredGateway:
    base = configured_gateway(settings, monotonic=time.monotonic)
    return ConfiguredGateway(gateway or base.gateway, base.route, base.readiness)


def route_evidence(
    *, provider: str = "KIMI", region: str = "CN", index: int = 0
) -> AttemptRouteEvidence:
    role = "PRIMARY" if index == 0 else "BACKUP"
    return AttemptRouteEvidence(
        provider=provider,
        region=region,
        route_role=role,
        route_id=f"{region.lower()}-{provider.lower()}-v1",
        model_id="model-1",
    )


def started(
    *, digest: str = "a" * 64, provider: str = "KIMI", region: str = "CN", index: int = 0
) -> AttemptStartedEvidence:
    return AttemptStartedEvidence(
        request_id="generation-1",
        attempt_index=index,
        route=route_evidence(provider=provider, region=region, index=index),
        input_digest=digest,
        timeout_s=10.0 if index == 0 else 8.0,
    )


def finished_success(
    *, digest: str = "a" * 64, provider: str = "KIMI", region: str = "CN", index: int = 0
) -> AttemptFinishedEvidence:
    finish_reason = {"KIMI": "stop", "OPENAI": "completed", "ANTHROPIC": "tool_use"}[provider]
    return AttemptFinishedEvidence(
        request_id="generation-1",
        attempt_index=index,
        route=route_evidence(provider=provider, region=region, index=index),
        status="SUCCEEDED",
        failure_code=None,
        retryable=False,
        security_failure=False,
        provider_request_id="provider-request-1",
        finish_reason=finish_reason,
        output_digest="b" * 64,
        input_tokens=10,
        output_tokens=4,
    )


def candidate(*, valid: bool = True) -> CandidateProjection:
    gap = CoverageGap(
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 6, 9, tzinfo=timezone.utc),
        datetime(2026, 10, 6, 10, tzinfo=timezone.utc),
        2,
        1,
    )
    return CandidateProjection(
        candidate_index=1,
        valid=valid,
        rationale="coverage restored" if valid else "coverage remains short",
        operational_warnings=(),
        rejection_codes=() if valid else ("COVERAGE_GAP",),
        coverage_gaps=() if valid else (gap,),
        operations=(
            CandidateOperationProjection(
                operation="ADD",
                assignment_id=None,
                staff_id="staff-1",
                display_name="Operator One",
                role_code="RANGE_ATTENDANT",
                area_code="RANGE_A",
                start_at=datetime(2026, 10, 6, 9, tzinfo=timezone.utc),
                end_at=datetime(2026, 10, 6, 10, tzinfo=timezone.utc),
            ),
        ),
        materialized_schedule=() if valid else None,
        materialized_schedule_digest="c" * 64 if valid else None,
    )


def generation(
    state: str = "SUCCEEDED", *,
    attempts: tuple[AttemptFinishedEvidence, ...] | None = None,
    starts: tuple[AttemptStartedEvidence, ...] | None = None,
    failure_code: str | None = None,
) -> GenerationProjectionView:
    if attempts is None:
        attempts = (finished_success(),) if state in {"SUCCEEDED", "NO_VALID_SUGGESTION"} else ()
    if starts is None:
        starts = tuple(
            started(
                digest=row.route and "a" * 64,
                provider=row.route.provider,
                region=row.route.region,
                index=row.attempt_index,
            )
            for row in attempts
        )
    terminal_codes = {
        "RESULT_UNKNOWN": "RESULT_UNKNOWN",
        "NO_VALID_SUGGESTION": "NO_VALID_SUGGESTION",
        "UNAVAILABLE": "DEADLINE_EXHAUSTED",
        "REFUSED": "PROVIDER_REFUSED",
        "INVALID_RESPONSE": "SCHEMA_MISMATCH",
        "PROVIDER_ERROR": "PROVIDER_CLIENT_ERROR",
        "CONFIGURATION_ERROR": "PROVIDER_UNCONFIGURED",
        "SECURITY_ERROR": "TLS_VERIFICATION_FAILED",
    }
    candidates = (
        (candidate(valid=True),)
        if state == "SUCCEEDED"
        else ((candidate(valid=False),) if state == "NO_VALID_SUGGESTION" else ())
    )
    return GenerationProjectionView(
        generation_id="generation-1",
        request_id="request-1",
        operation_event_id="event-1",
        service_date=date(2026, 10, 6),
        basis_revisions=(1, 0, 0),
        basis_digest="d" * 64,
        retry_of=None,
        lifecycle_state=state,
        failure_code=terminal_codes.get(state) if failure_code is None else failure_code,
        route_region="CN",
        route_readiness="READY",
        primary_provider="KIMI",
        primary_model_id="model-1",
        backup_provider=None,
        backup_model_id=None,
        started_attempts=starts,
        finished_attempts=attempts,
        candidates=candidates,
    )


def generation_request(view: GenerationProjectionView) -> RequestProjection:
    committed = GenerationCommittedProjection(
        event_id=view.operation_event_id,
        occurred_at_utc=NOW,
        request_digest="e" * 64,
        generation_id=view.generation_id,
        service_date=view.service_date,
        basis_revisions=view.basis_revisions,
        basis_digest=view.basis_digest,
        retry_of=view.retry_of,
        route_region=view.route_region,
        route_readiness=view.route_readiness,
        primary_provider=view.primary_provider,
        backup_provider=view.backup_provider,
    )
    result = None
    if view.lifecycle_state not in {"RESERVED", "IN_PROGRESS", "RESULT_UNKNOWN"}:
        result_status = (
            "SUCCEEDED"
            if view.lifecycle_state in {"SUCCEEDED", "NO_VALID_SUGGESTION"}
            else view.lifecycle_state
        )
        result = ResultEvidence(
            request_id=view.generation_id,
            status=result_status,
            failure_code=None if result_status == "SUCCEEDED" else view.failure_code,
            selected_provider=(
                None if not view.finished_attempts else view.finished_attempts[-1].route.provider
            ),
            selected_model_id=(
                None if not view.finished_attempts else view.finished_attempts[-1].route.model_id
            ),
            provider_request_id=(
                None if not view.finished_attempts else view.finished_attempts[-1].provider_request_id
            ),
            finish_reason=(
                None if not view.finished_attempts else view.finished_attempts[-1].finish_reason
            ),
            input_digest=(
                "a" * 64 if not view.started_attempts else view.started_attempts[0].input_digest
            ),
            output_digest=(
                None if not view.finished_attempts else view.finished_attempts[-1].output_digest
            ),
            attempts=view.finished_attempts,
            candidate_count=len(view.candidates) if result_status == "SUCCEEDED" else 0,
            bounded_summary=None,
        )
    return RequestProjection(
        operation_kind="suggestion-generate",
        request_id=view.request_id,
        event_ids=(view.operation_event_id,),
        generation_id=view.generation_id,
        lifecycle_state=view.lifecycle_state,
        started_attempts=view.started_attempts,
        attempt_evidence=view.finished_attempts,
        result_evidence=result,
        candidates=view.candidates,
        manager_response=None,
        generation=view,
        committed_record=committed,
    )


def empty_date_projection(*, generations=(), responses=()) -> DateProjection:
    return DateProjection(
        site_id="site-1",
        deployment_id="deployment-1",
        service_date=date(2026, 10, 6),
        site_timezone="Asia/Shanghai",
        roster_revision=None,
        roster_digest=None,
        exception_set_revision=0,
        roster_effective_from=None,
        roster_effective_until=None,
        worker_count=0,
        assignment_count=0,
        coverage_count=0,
        effective_plan_schedule=None,
        availability=(),
        assignments=(),
        exceptions=(),
        effective_plan=EffectivePlanState(0, "NO_PLAN", "f" * 64, None, 0, ()),
        generations=generations,
        manager_responses=responses,
    )


def bounded_assignment_projection() -> AssignmentProjection:
    return AssignmentProjection(
        "assignment-1",
        "staff-1",
        "Operator One",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 6, 9, tzinfo=timezone.utc),
        datetime(2026, 10, 6, 10, tzinfo=timezone.utc),
    )


def rostered_date_projection(**changes) -> DateProjection:
    return replace(
        empty_date_projection(),
        roster_revision=1,
        roster_digest="b" * 64,
        roster_effective_from=date(2026, 10, 1),
        worker_count=1,
        coverage_count=1,
        **changes,
    )


def test_date_wire_keeps_regular_roster_and_effective_plan_assignments_separate() -> None:
    regular = bounded_assignment_projection()
    planned = replace(
        regular,
        assignment_id="assignment-2",
        staff_id="staff-2",
        display_name="Operator Two",
    )
    projection = rostered_date_projection(
        assignment_count=1,
        assignments=(regular,),
        effective_plan=EffectivePlanState(
            1, "CURRENT", "c" * 64, (planned,), 1, ()
        ),
        effective_plan_schedule=(planned,),
    )

    wire = with_runtime_context(
        projection,
        configured_for(cn_settings()),
        now=NOW,
        site_id="site-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )

    validator("#/$defs/StaffingDateSnapshot").validate(wire)
    assert [row["staff_id"] for row in wire["assignments"]] == ["staff-1"]
    assert [
        row["staff_id"] for row in wire["effective_plan"]["assignments"]
    ] == ["staff-2"]
    assert wire["assignments"][0]["start_at"] == "2026-10-06T09:00:00Z"


def test_resolve_staffing_root_is_exact_stable_and_symlink_safe(tmp_path: Path) -> None:
    state = tmp_path / "stable"
    volatile = tmp_path / "volatile"
    assert resolve_staffing_root(
        state,
        site_id="site-1",
        deployment_id="deployment-1",
        volatile_out=volatile,
    ) == state / "site-1" / "deployment-1" / "staffing-v1"
    lexical_alias = tmp_path / "unused" / ".." / "canonical-stable"
    canonical = resolve_staffing_root(
        lexical_alias,
        site_id="site-1",
        deployment_id="deployment-1",
        volatile_out=volatile,
    )
    assert canonical == (
        lexical_alias / "site-1" / "deployment-1" / "staffing-v1"
    ).resolve(strict=False)
    assert ".." not in canonical.parts
    for unsafe in ("", ".", "..", "a/b", "a\\b"):
        with pytest.raises(ValueError, match="safe path segment"):
            resolve_staffing_root(
                state, site_id=unsafe, deployment_id="deployment-1", volatile_out=volatile
            )
    with pytest.raises(ValueError, match="volatile"):
        resolve_staffing_root(
            volatile / "state",
            site_id="site-1",
            deployment_id="deployment-1",
            volatile_out=volatile,
        )
    outside = tmp_path / "outside"
    outside.mkdir()
    (state / "site-1").mkdir(parents=True)
    (state / "site-1" / "deployment-1").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        resolve_staffing_root(
            state,
            site_id="site-1",
            deployment_id="deployment-1",
            volatile_out=volatile,
        )
    internal = state / "internal-deployment"
    internal.mkdir()
    (state / "site-2").mkdir()
    (state / "site-2" / "deployment-2").symlink_to(
        internal, target_is_directory=True
    )
    with pytest.raises(ValueError, match="symlink"):
        resolve_staffing_root(
            state,
            site_id="site-2",
            deployment_id="deployment-2",
            volatile_out=volatile,
        )


def test_provider_settings_are_secret_safe_and_readiness_is_closed(capsys) -> None:
    sentinels = ("moonshot-super-secret", "openai-super-secret", "anthropic-super-secret")
    settings = load_provider_settings(
        {
            "MOONSHOT_API_KEY": f"  {sentinels[0]}  ",
            "OPENAI_API_KEY": sentinels[1],
            "ANTHROPIC_API_KEY": sentinels[2],
        },
        region="GLOBAL",
        language="en",
        kimi_model=None,
        openai_model=" gpt-model ",
        anthropic_model=" claude-model ",
    )
    assert settings.openai_model == "gpt-model"
    configured = configured_gateway(settings, monotonic=time.monotonic)
    assert generation_capability(configured) == {
        "status": "READY",
        "region": "GLOBAL",
        "primary_provider": "OPENAI",
        "backup_provider": "ANTHROPIC",
        "failure_code": None,
    }
    route = generation_route_evidence(configured, settings)
    assert route is not None
    assert route.primary_model_id == "gpt-model"
    assert route.backup_model_id == "claude-model"
    captured = capsys.readouterr()
    rendered = repr(settings) + str(settings) + captured.out + captured.err
    assert all(secret not in rendered for secret in sentinels)


@pytest.mark.parametrize(
    ("settings", "expected"),
    (
        (
            StaffingProviderSettings(None, "zh-CN", None, None, None, None, None, None),
            ("UNAVAILABLE", None, None, None, "PROVIDER_UNCONFIGURED"),
        ),
        (cn_settings(), ("READY", "CN", "KIMI", None, None)),
        (
            replace(cn_settings(), kimi_model=None),
            ("UNAVAILABLE", "CN", "KIMI", None, "PROVIDER_UNCONFIGURED"),
        ),
        (
            cn_settings(key=None),
            ("UNAVAILABLE", "CN", "KIMI", None, "PROVIDER_UNCONFIGURED"),
        ),
        (
            global_settings(anthropic_key=None),
            ("DEGRADED_BACKUP_UNCONFIGURED", "GLOBAL", "OPENAI", None, "BACKUP_UNCONFIGURED"),
        ),
        (
            global_settings(openai_key=None),
            ("UNAVAILABLE", "GLOBAL", "OPENAI", "ANTHROPIC", "PROVIDER_UNCONFIGURED"),
        ),
        (
            replace(global_settings(), openai_model=None),
            ("UNAVAILABLE", "GLOBAL", "OPENAI", "ANTHROPIC", "PROVIDER_UNCONFIGURED"),
        ),
    ),
)
def test_generation_capability_matrix(settings, expected) -> None:
    configured = configured_gateway(settings, monotonic=time.monotonic)
    capability = generation_capability(configured)
    assert tuple(capability.values()) == expected
    validator("#/$defs/GenerationCapability").validate(capability)
    evidence = generation_route_evidence(configured, settings)
    if settings.region is None or (
        settings.region is DeploymentRegion.CN and settings.kimi_model is None
    ) or (settings.region is DeploymentRegion.GLOBAL and settings.openai_model is None):
        assert evidence is None
    else:
        assert evidence is not None
        assert (evidence.primary_provider is None) == (evidence.primary_model_id is None)
        assert (evidence.backup_provider is None) == (evidence.backup_model_id is None)


def test_invalid_provider_model_configuration_never_exposes_secret(
    capsys,
) -> None:
    secret = "configuration-sentinel-secret"
    settings = replace(cn_settings(key=secret), kimi_model="invalid\nmodel")

    with pytest.raises(GatewayContractError) as invalid:
        configured_gateway(settings, monotonic=time.monotonic)

    captured = capsys.readouterr()
    rendered = (
        str(invalid.value)
        + repr(invalid.value)
        + repr(settings)
        + captured.out
        + captured.err
    )
    assert secret not in rendered


def test_time_projection_requires_frozen_profiles() -> None:
    assert utc_time(NOW) == "2026-10-06T01:02:03.000004Z"
    assert offset_time(datetime(2026, 10, 6, 9, tzinfo=timezone.utc)) == "2026-10-06T09:00:00Z"
    assert offset_time(
        datetime(2026, 10, 6, 9, tzinfo=timezone(timedelta(hours=8)))
    ) == "2026-10-06T09:00:00+08:00"
    for invalid in (
        datetime(2026, 10, 6, 9),
        datetime(2026, 10, 6, 9, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 6, 9, 0, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 6, 9, tzinfo=timezone(timedelta(seconds=30))),
    ):
        with pytest.raises(StaffingProjectionError):
            offset_time(invalid)
    with pytest.raises(StaffingProjectionError):
        utc_time(datetime(2026, 10, 6, 9, tzinfo=timezone(timedelta(hours=8))))


@pytest.mark.parametrize(
    ("required", "actual"),
    ((True, 0), (1, True), (0, 0), (10001, 0), (1, 1), (2, -1)),
)
def test_coverage_gap_rejects_non_integer_and_non_gap_counts(required, actual) -> None:
    gap = CoverageGap(
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 6, 9, tzinfo=timezone.utc),
        datetime(2026, 10, 6, 10, tzinfo=timezone.utc),
        required,
        actual,
    )
    with pytest.raises(StaffingProjectionError):
        coverage_gap_to_wire(gap)


def test_candidate_and_provenance_projection_obey_complete_wire_matrix() -> None:
    candidate_validator = validator("#/$defs/CandidateProjection")
    candidate_validator.validate(candidate_to_wire(candidate(valid=True)))
    candidate_validator.validate(candidate_to_wire(candidate(valid=False)))
    malformed = replace(candidate(valid=True), operational_warnings=("x",) * 6)
    with pytest.raises(StaffingProjectionError):
        candidate_to_wire(malformed)

    rows = provenance_to_wire((started(),), (finished_success(),))
    validator("#/$defs/ProviderProvenanceSequence").validate(rows)
    with pytest.raises(StaffingProjectionError, match="digest"):
        provenance_to_wire(
            (
                started(provider="OPENAI", region="GLOBAL", index=0),
                started(digest="9" * 64, provider="ANTHROPIC", region="GLOBAL", index=1),
            ),
            (
                replace(
                    finished_success(provider="OPENAI", region="GLOBAL", index=0),
                    status="UNAVAILABLE",
                    failure_code="CONNECT_TIMEOUT",
                    retryable=True,
                    provider_request_id=None,
                    finish_reason=None,
                    output_digest=None,
                    input_tokens=None,
                    output_tokens=None,
                ),
                finished_success(digest="9" * 64, provider="ANTHROPIC", region="GLOBAL", index=1),
            ),
        )

    with pytest.raises(StaffingProjectionError, match="backup"):
        provenance_to_wire(
            (
                started(provider="OPENAI", region="GLOBAL", index=0),
                started(provider="ANTHROPIC", region="GLOBAL", index=1),
            ),
            (),
        )

    with pytest.raises(StaffingProjectionError, match="timeout"):
        provenance_to_wire((replace(started(), timeout_s=15.1),), ())

    overlong_display = replace(
        candidate(valid=True),
        operations=(
            replace(candidate(valid=True).operations[0], display_name="x" * 101),
        ),
    )
    with pytest.raises(StaffingProjectionError, match="display_name"):
        candidate_to_wire(overlong_display)

    with pytest.raises(StaffingProjectionError, match="duplicate"):
        provenance_to_wire((started(), started()), (finished_success(),))
    with pytest.raises(StaffingProjectionError, match="pair"):
        provenance_to_wire((), (finished_success(),))
    for operation in (
        replace(candidate(valid=True).operations[0], staff_id=None),
        replace(
            candidate(valid=True).operations[0],
            operation="REMOVE",
            assignment_id=None,
        ),
    ):
        with pytest.raises(StaffingProjectionError, match="operation"):
            candidate_to_wire(replace(candidate(valid=True), operations=(operation,)))


@pytest.mark.parametrize("reverse_started", (False, True))
def test_provenance_rejects_input_tuples_out_of_attempt_order(
    reverse_started: bool,
) -> None:
    begins = (
        started(provider="OPENAI", region="GLOBAL"),
        started(provider="ANTHROPIC", region="GLOBAL", index=1),
    )
    primary = replace(
        finished_success(provider="OPENAI", region="GLOBAL"),
        status="UNAVAILABLE",
        failure_code="CONNECT_TIMEOUT",
        retryable=True,
        provider_request_id=None,
        finish_reason=None,
        output_digest=None,
        input_tokens=None,
        output_tokens=None,
    )
    finishes = (
        primary,
        finished_success(provider="ANTHROPIC", region="GLOBAL", index=1),
    )

    with pytest.raises(StaffingProjectionError, match="order"):
        provenance_to_wire(
            tuple(reversed(begins)) if reverse_started else begins,
            finishes if reverse_started else tuple(reversed(finishes)),
        )


@pytest.mark.parametrize(
    "state",
    (
        "RESERVED",
        "IN_PROGRESS",
        "RESULT_UNKNOWN",
        "SUCCEEDED",
        "NO_VALID_SUGGESTION",
        "UNAVAILABLE",
        "REFUSED",
        "INVALID_RESPONSE",
        "PROVIDER_ERROR",
        "CONFIGURATION_ERROR",
        "SECURITY_ERROR",
    ),
)
def test_request_projection_emits_every_closed_generation_branch(state: str) -> None:
    if state == "IN_PROGRESS":
        view = generation(state, starts=(started(),), attempts=(), failure_code=None)
    elif state == "RESULT_UNKNOWN":
        view = generation(state, starts=(started(),), attempts=(), failure_code="RESULT_UNKNOWN")
    elif state in {"UNAVAILABLE", "CONFIGURATION_ERROR"}:
        view = generation(state, starts=(), attempts=())
    elif state in {"REFUSED", "INVALID_RESPONSE", "PROVIDER_ERROR", "SECURITY_ERROR"}:
        failed = replace(
            finished_success(),
            status=state,
            failure_code={
                "REFUSED": "PROVIDER_REFUSED",
                "INVALID_RESPONSE": "SCHEMA_MISMATCH",
                "PROVIDER_ERROR": "PROVIDER_CLIENT_ERROR",
                "SECURITY_ERROR": "TLS_VERIFICATION_FAILED",
            }[state],
            retryable=False,
            security_failure=state == "SECURITY_ERROR",
            provider_request_id=None,
            finish_reason=None,
            output_digest=None,
            input_tokens=None,
            output_tokens=None,
        )
        view = generation(state, attempts=(failed,))
    else:
        view = generation(state)
    wire = project_request_to_wire(generation_request(view), disposition="duplicate")
    validator("#/$defs/StaffingReceipt").validate(wire)
    assert wire["state"] == state
    assert wire["operation_id"] == "event-1"


def test_request_projection_rejects_terminal_digest_and_manager_operator_mismatch() -> None:
    view = generation()
    projection = generation_request(view)
    assert projection.result_evidence is not None
    with pytest.raises(StaffingProjectionError, match="digest"):
        project_request_to_wire(
            replace(
                projection,
                result_evidence=replace(projection.result_evidence, input_digest="9" * 64),
            ),
            disposition="created",
        )

    manager = ManagerResponseProjection(
        generation_id="generation-1",
        decision="REJECT",
        reason_code="OTHER",
        operator="manager-1",
        note=None,
        effective_plan_revision=None,
        schedule_digest=None,
        effective_schedule=None,
    )
    committed = ManagerCommittedProjection(
        event_id="manager-event-1",
        occurred_at_utc=NOW,
        request_digest="e" * 64,
        generation_id="generation-1",
        operator="manager-2",
        note=None,
        decision="REJECT",
        reason_code="OTHER",
        effective_plan_revision=None,
        schedule_digest=None,
        effective_schedule=None,
    )
    request = RequestProjection(
        operation_kind="manager-response",
        request_id="request-manager-1",
        event_ids=("manager-event-1",),
        generation_id="generation-1",
        lifecycle_state="COMMITTED",
        started_attempts=(),
        attempt_evidence=(),
        result_evidence=None,
        candidates=(),
        manager_response=manager,
        generation=None,
        committed_record=committed,
    )
    with pytest.raises(StaffingProjectionError, match="mismatch"):
        project_request_to_wire(request, disposition="created")

    with pytest.raises(StaffingProjectionError, match="candidate_count"):
        project_request_to_wire(
            replace(
                projection,
                result_evidence=replace(projection.result_evidence, candidate_count=0),
            ),
            disposition="created",
        )

    with pytest.raises(StaffingProjectionError, match="committed event"):
        project_request_to_wire(
            replace(projection, event_ids=("different-event",)),
            disposition="created",
        )
    non_utc_committed = replace(
        projection.committed_record,
        occurred_at_utc=NOW.astimezone(timezone(timedelta(hours=8))),
    )
    with pytest.raises(StaffingProjectionError, match="UTC"):
        project_request_to_wire(
            replace(projection, committed_record=non_utc_committed),
            disposition="created",
        )
    missing_failure = generation_request(
        replace(generation("UNAVAILABLE"), failure_code=None)
    )
    assert missing_failure.result_evidence is not None
    missing_failure = replace(
        missing_failure,
        result_evidence=replace(
            missing_failure.result_evidence,
            status="UNAVAILABLE",
            failure_code=None,
        ),
    )
    with pytest.raises(StaffingProjectionError, match="terminal"):
        project_request_to_wire(missing_failure, disposition="created")

    wrong_record_class = replace(
        request,
        operation_kind="roster-import",
    )
    with pytest.raises(StaffingProjectionError, match="do not match"):
        project_request_to_wire(wrong_record_class, disposition="created")


def test_request_projection_rejects_attempts_outside_reserved_route() -> None:
    rogue_route = replace(route_evidence(), model_id="rogue-model")
    rogue_start = replace(started(), route=rogue_route)
    rogue_finish = replace(finished_success(), route=rogue_route)
    mismatched_model = generation_request(
        generation(starts=(rogue_start,), attempts=(rogue_finish,))
    )
    with pytest.raises(StaffingProjectionError, match="reserved route"):
        project_request_to_wire(mismatched_model, disposition="created")

    nonterminal_mismatch = generation_request(
        generation(
            "IN_PROGRESS",
            starts=(rogue_start,),
            attempts=(),
            failure_code=None,
        )
    )
    with pytest.raises(StaffingProjectionError, match="reserved route"):
        project_request_to_wire(nonterminal_mismatch, disposition="created")

    wrong_request_start = replace(started(), request_id="generation-other")
    wrong_request_finish = replace(finished_success(), request_id="generation-other")
    mismatched_request = generation_request(
        generation(starts=(wrong_request_start,), attempts=(wrong_request_finish,))
    )
    with pytest.raises(StaffingProjectionError, match="generation identity"):
        project_request_to_wire(mismatched_request, disposition="created")

    unavailable_success = generation_request(
        replace(generation(), route_readiness="UNAVAILABLE")
    )
    with pytest.raises(StaffingProjectionError, match="unavailable route"):
        project_request_to_wire(unavailable_success, disposition="created")


def test_exception_receipt_requires_exact_exception_identity() -> None:
    item = ExceptionProjection(
        exception_id="exception-other",
        service_date=date(2026, 10, 6),
        staff_id="staff-1",
        display_name="Operator One",
        kind="LEAVE",
        unavailable_start=datetime(2026, 10, 6, 9, tzinfo=timezone.utc),
        unavailable_end=datetime(2026, 10, 6, 10, tzinfo=timezone.utc),
        note=None,
    )
    committed = ExceptionCommittedProjection(
        event_id="event-1",
        event_type="exception_recorded",
        occurred_at_utc=NOW,
        request_digest="e" * 64,
        operator="manager-1",
        exception_id="exception-1",
        exception_set_revision=1,
        exception_digest="f" * 64,
        exception=item,
        replacement=None,
        note=None,
    )
    projection = RequestProjection(
        operation_kind="exception-record",
        request_id="request-1",
        event_ids=("event-1",),
        generation_id=None,
        lifecycle_state="COMMITTED",
        started_attempts=(),
        attempt_evidence=(),
        result_evidence=None,
        candidates=(),
        manager_response=None,
        generation=None,
        committed_record=committed,
    )
    with pytest.raises(StaffingProjectionError, match="identity"):
        project_request_to_wire(projection, disposition="created")


def test_non_generation_receipts_emit_every_frozen_operation_state_record_triple() -> None:
    recorded = ExceptionProjection(
        "exception-1",
        date(2026, 10, 6),
        "staff-1",
        "Operator One",
        "LEAVE",
        datetime(2026, 10, 6, 9, tzinfo=timezone.utc),
        datetime(2026, 10, 6, 10, tzinfo=timezone.utc),
        None,
    )
    replacement = replace(recorded, kind="UNAVAILABLE")
    rows = (
        (
            "roster-import",
            RosterCommittedProjection(
                "event-roster",
                NOW,
                "a" * 64,
                "manager-1",
                "upload.csv",
                1,
                "b" * 64,
                date(2026, 10, 1),
                None,
                1,
                1,
                1,
            ),
            None,
            "ROSTER_IMPORTED",
        ),
        (
            "exception-record",
            ExceptionCommittedProjection(
                "event-record",
                "exception_recorded",
                NOW,
                "a" * 64,
                "manager-1",
                "exception-1",
                1,
                "b" * 64,
                recorded,
                None,
                None,
            ),
            None,
            "EXCEPTION_RECORDED",
        ),
        (
            "exception-cancel",
            ExceptionCommittedProjection(
                "event-cancel",
                "exception_cancelled",
                NOW,
                "a" * 64,
                "manager-1",
                "exception-1",
                2,
                None,
                None,
                None,
                "coverage restored",
            ),
            None,
            "EXCEPTION_CANCELLED",
        ),
        (
            "exception-correct",
            ExceptionCommittedProjection(
                "event-correct",
                "exception_corrected",
                NOW,
                "a" * 64,
                "manager-1",
                "exception-1",
                2,
                "b" * 64,
                None,
                replacement,
                None,
            ),
            None,
            "EXCEPTION_CORRECTED",
        ),
        (
            "manager-response",
            ManagerCommittedProjection(
                "event-manager",
                NOW,
                "a" * 64,
                "generation-1",
                "manager-1",
                None,
                "REJECT",
                "OTHER",
                None,
                None,
                None,
            ),
            ManagerResponseProjection(
                "generation-1", "REJECT", "OTHER", "manager-1", None, None, None, None
            ),
            "MANAGER_RESPONSE_COMMITTED",
        ),
    )
    for operation_kind, committed, manager, record_kind in rows:
        projection = RequestProjection(
            operation_kind=operation_kind,
            request_id=f"request-{operation_kind}",
            event_ids=(committed.event_id,),
            generation_id=(
                "generation-1" if operation_kind == "manager-response" else None
            ),
            lifecycle_state="COMMITTED",
            started_attempts=(),
            attempt_evidence=(),
            result_evidence=None,
            candidates=(),
            manager_response=manager,
            generation=None,
            committed_record=committed,
        )
        wire = project_request_to_wire(projection, disposition="created")
        validator("#/$defs/StaffingReceipt").validate(wire)
        assert (wire["operation_kind"], wire["state"], wire["record"]["record_kind"]) == (
            operation_kind,
            "COMMITTED",
            record_kind,
        )
        if operation_kind == "exception-correct":
            assert wire["record"]["replaced_exception_id"] == "exception-1"
            assert wire["record"]["replacement"]["exception_id"] == "exception-1"


def test_request_projection_accepts_local_terminal_after_persisted_primary_attempt() -> None:
    begin = started(provider="OPENAI", region="GLOBAL")
    failed = replace(
        finished_success(provider="OPENAI", region="GLOBAL"),
        status="UNAVAILABLE",
        failure_code="CONNECT_TIMEOUT",
        retryable=True,
        provider_request_id=None,
        finish_reason=None,
        output_digest=None,
        input_tokens=None,
        output_tokens=None,
    )
    view = replace(
        generation(
            "UNAVAILABLE",
            starts=(begin,),
            attempts=(failed,),
            failure_code="BACKUP_UNCONFIGURED",
        ),
        route_region="GLOBAL",
        route_readiness="DEGRADED_BACKUP_UNCONFIGURED",
        primary_provider="OPENAI",
        primary_model_id="model-1",
        backup_provider=None,
        backup_model_id=None,
    )
    projection = generation_request(view)
    assert projection.result_evidence is not None
    projection = replace(
        projection,
        result_evidence=replace(
            projection.result_evidence,
            status="UNAVAILABLE",
            failure_code="BACKUP_UNCONFIGURED",
            selected_provider=None,
            selected_model_id=None,
            provider_request_id=None,
            finish_reason=None,
            output_digest=None,
        ),
    )

    wire = project_request_to_wire(projection, disposition="created")

    validator("#/$defs/StaffingReceipt").validate(wire)
    assert wire["state"] == "UNAVAILABLE"
    assert wire["record"]["failure_code"] == "BACKUP_UNCONFIGURED"
    assert len(wire["record"]["provenance"]) == 1


def test_input_too_large_terminal_provenance_has_one_exact_global_after_primary_shape() -> None:
    def failed_attempt(
        *, provider: str, region: str, code: str, status: str, retryable: bool
    ) -> AttemptFinishedEvidence:
        return replace(
            finished_success(provider=provider, region=region),
            status=status,
            failure_code=code,
            retryable=retryable,
            provider_request_id=None,
            finish_reason=None,
            output_digest=None,
            input_tokens=None,
            output_tokens=None,
        )

    def local_projection(view: GenerationProjectionView) -> RequestProjection:
        projection = generation_request(view)
        assert projection.result_evidence is not None
        return replace(
            projection,
            result_evidence=replace(
                projection.result_evidence,
                status="CONFIGURATION_ERROR",
                failure_code="INPUT_TOO_LARGE",
                selected_provider=None,
                selected_model_id=None,
                provider_request_id=None,
                finish_reason=None,
                output_digest=None,
            ),
        )

    initial_local = generation(
        "CONFIGURATION_ERROR",
        starts=(),
        attempts=(),
        failure_code="INPUT_TOO_LARGE",
    )
    initial_wire = project_request_to_wire(
        local_projection(initial_local), disposition="created"
    )
    assert initial_wire["record"]["provenance"] == []

    fallback_start = started(provider="OPENAI", region="GLOBAL")
    fallback_finish = failed_attempt(
        provider="OPENAI",
        region="GLOBAL",
        code="CONNECT_TIMEOUT",
        status="UNAVAILABLE",
        retryable=True,
    )
    global_route = {
        "route_region": "GLOBAL",
        "route_readiness": "READY",
        "primary_provider": "OPENAI",
        "primary_model_id": "model-1",
        "backup_provider": "ANTHROPIC",
        "backup_model_id": "model-1",
    }
    after_primary = replace(
        generation(
            "CONFIGURATION_ERROR",
            starts=(fallback_start,),
            attempts=(fallback_finish,),
            failure_code="INPUT_TOO_LARGE",
        ),
        **global_route,
    )
    after_primary_wire = project_request_to_wire(
        local_projection(after_primary), disposition="created"
    )
    validator("#/$defs/StaffingReceipt").validate(after_primary_wire)
    assert after_primary_wire["state"] == "CONFIGURATION_ERROR"
    assert after_primary_wire["record"]["failure_code"] == "INPUT_TOO_LARGE"
    assert len(after_primary_wire["record"]["provenance"]) == 1
    assert after_primary_wire["record"]["provenance"][0]["provider"] == "OPENAI"

    degraded = replace(
        after_primary,
        route_readiness="DEGRADED_BACKUP_UNCONFIGURED",
        backup_provider=None,
        backup_model_id=None,
    )
    cn_attempt = generation(
        "CONFIGURATION_ERROR",
        starts=(started(),),
        attempts=(
            failed_attempt(
                provider="KIMI",
                region="CN",
                code="CONNECT_TIMEOUT",
                status="UNAVAILABLE",
                retryable=True,
            ),
        ),
        failure_code="INPUT_TOO_LARGE",
    )
    nonfallback = replace(
        after_primary,
        finished_attempts=(
            failed_attempt(
                provider="OPENAI",
                region="GLOBAL",
                code="PROVIDER_REFUSED",
                status="REFUSED",
                retryable=False,
            ),
        ),
    )
    for invalid in (degraded, cn_attempt, nonfallback):
        with pytest.raises(StaffingProjectionError):
            project_request_to_wire(
                local_projection(invalid), disposition="created"
            )


def test_terminal_provenance_matrix_rejects_unfinished_global_fallback() -> None:
    cn_fallback = replace(
        finished_success(),
        status="UNAVAILABLE",
        failure_code="CONNECT_TIMEOUT",
        retryable=True,
        provider_request_id=None,
        finish_reason=None,
        output_digest=None,
        input_tokens=None,
        output_tokens=None,
    )
    cn_view = generation(
        "UNAVAILABLE",
        starts=(started(),),
        attempts=(cn_fallback,),
        failure_code="CONNECT_TIMEOUT",
    )
    validator("#/$defs/StaffingReceipt").validate(
        project_request_to_wire(generation_request(cn_view), disposition="created")
    )

    openai_refused = replace(
        finished_success(provider="OPENAI", region="GLOBAL"),
        status="REFUSED",
        failure_code="PROVIDER_REFUSED",
        provider_request_id=None,
        finish_reason=None,
        output_digest=None,
        input_tokens=None,
        output_tokens=None,
    )
    global_route = {
        "route_region": "GLOBAL",
        "route_readiness": "READY",
        "primary_provider": "OPENAI",
        "primary_model_id": "model-1",
        "backup_provider": "ANTHROPIC",
        "backup_model_id": "model-1",
    }
    refused_view = replace(
        generation(
            "REFUSED",
            starts=(started(provider="OPENAI", region="GLOBAL"),),
            attempts=(openai_refused,),
            failure_code="PROVIDER_REFUSED",
        ),
        **global_route,
    )
    validator("#/$defs/StaffingReceipt").validate(
        project_request_to_wire(
            generation_request(refused_view), disposition="created"
        )
    )

    openai_fallback = replace(
        openai_refused,
        status="UNAVAILABLE",
        failure_code="CONNECT_TIMEOUT",
        retryable=True,
    )
    anthropic_refused = replace(
        openai_refused,
        attempt_index=1,
        route=route_evidence(provider="ANTHROPIC", region="GLOBAL", index=1),
    )
    completed_fallback = replace(
        generation(
            "REFUSED",
            starts=(
                started(provider="OPENAI", region="GLOBAL"),
                started(provider="ANTHROPIC", region="GLOBAL", index=1),
            ),
            attempts=(openai_fallback, anthropic_refused),
            failure_code="PROVIDER_REFUSED",
        ),
        **global_route,
    )
    validator("#/$defs/StaffingReceipt").validate(
        project_request_to_wire(
            generation_request(completed_fallback), disposition="created"
        )
    )

    unfinished_fallback = replace(
        generation(
            "UNAVAILABLE",
            starts=(started(provider="OPENAI", region="GLOBAL"),),
            attempts=(openai_fallback,),
            failure_code="CONNECT_TIMEOUT",
        ),
        **global_route,
    )
    with pytest.raises(StaffingProjectionError, match="fallback"):
        project_request_to_wire(
            generation_request(unfinished_fallback), disposition="created"
        )

    gateway_attempt = AttemptRecord(
        "generation-1",
        0,
        Provider.OPENAI,
        "model-1",
        GenerationStatus.UNAVAILABLE,
        FailureCode.CONNECT_TIMEOUT,
        True,
        False,
        None,
        None,
        None,
        "a" * 64,
        None,
    )
    unfinished_result = GenerationResult(
        "generation-1",
        GenerationStatus.UNAVAILABLE,
        None,
        FailureCode.CONNECT_TIMEOUT,
        Provider.OPENAI,
        "model-1",
        None,
        None,
        None,
        "a" * 64,
        None,
        (gateway_attempt,),
    )
    with pytest.raises(StaffingProjectionError, match="fallback"):
        result_evidence(
            unfinished_result,
            region=DeploymentRegion.GLOBAL,
            candidate_count=0,
        )


@pytest.mark.parametrize(
    ("state", "failure_code"),
    (
        ("CONFIGURATION_ERROR", "INPUT_TOO_LARGE"),
        ("CONFIGURATION_ERROR", "PROVIDER_UNCONFIGURED"),
        ("UNAVAILABLE", "DEADLINE_EXHAUSTED"),
    ),
)
def test_terminal_projection_rejects_every_unmatched_attempt_start(
    state: str, failure_code: str
) -> None:
    view = generation(
        state,
        starts=(started(),),
        attempts=(),
        failure_code=failure_code,
    )

    with pytest.raises(StaffingProjectionError, match="attempt"):
        project_request_to_wire(generation_request(view), disposition="created")


def test_provider_provenance_rejects_domain_only_failure_codes() -> None:
    end = replace(
        finished_success(),
        status="INVALID_RESPONSE",
        failure_code="SCHEMA_MISMATCH",
        retryable=False,
        provider_request_id=None,
        finish_reason=None,
        output_digest=None,
        input_tokens=None,
        output_tokens=None,
    )
    object.__setattr__(end, "failure_code", "invalid_provider_shape")
    with pytest.raises(StaffingProjectionError, match="failure code"):
        provenance_to_wire((started(),), (end,))


def test_date_projection_joins_one_manager_and_uses_six_digit_server_time() -> None:
    view = generation()
    manager = ManagerResponseProjection(
        generation_id="generation-1",
        decision="REJECT",
        reason_code="OTHER",
        operator="manager-1",
        note="manual",
        effective_plan_revision=None,
        schedule_digest=None,
        effective_schedule=None,
    )
    projection = empty_date_projection(generations=(view,), responses=(manager,))
    configured = configured_for(cn_settings())
    wire = with_runtime_context(
        projection,
        configured,
        now=NOW,
        site_id="site-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    validator("#/$defs/StaffingDateSnapshot").validate(wire)
    assert wire["server_time_utc"] == "2026-10-06T01:02:03.000004Z"
    assert wire["generations"][0]["manager_response"]["operator"] == "manager-1"
    with pytest.raises(StaffingProjectionError, match="multiple"):
        project_date_to_wire(
            empty_date_projection(generations=(view,), responses=(manager, manager)),
            site_id="site-1",
            deployment_id="deployment-1",
            site_timezone="Asia/Shanghai",
        )

    rogue_route = replace(route_evidence(), model_id="rogue-model")
    rogue_generation = generation(
        starts=(replace(started(), route=rogue_route),),
        attempts=(replace(finished_success(), route=rogue_route),),
    )
    with pytest.raises(StaffingProjectionError, match="reserved route"):
        project_date_to_wire(
            empty_date_projection(generations=(rogue_generation,)),
            site_id="site-1",
            deployment_id="deployment-1",
            site_timezone="Asia/Shanghai",
        )

    for mismatched in (
        replace(empty_date_projection(), site_id="other-site"),
        replace(empty_date_projection(), deployment_id="other-deployment"),
        replace(empty_date_projection(), site_timezone="UTC"),
    ):
        with pytest.raises(StaffingProjectionError, match="identity"):
            project_date_to_wire(
                mismatched,
                site_id="site-1",
                deployment_id="deployment-1",
                site_timezone="Asia/Shanghai",
            )
    with pytest.raises(StaffingProjectionError, match="service date"):
        project_date_to_wire(
            replace(
                empty_date_projection(generations=(view,)),
                service_date=date(2026, 10, 7),
            ),
            site_id="site-1",
            deployment_id="deployment-1",
            site_timezone="Asia/Shanghai",
        )
    with pytest.raises(StaffingProjectionError, match="no generation"):
        project_date_to_wire(
            empty_date_projection(responses=(manager,)),
            site_id="site-1",
            deployment_id="deployment-1",
            site_timezone="Asia/Shanghai",
        )


def test_flattened_generation_coverage_gaps_enforce_schema_max_items() -> None:
    gap = CoverageGap(
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(2026, 10, 6, 9, tzinfo=timezone.utc),
        datetime(2026, 10, 6, 10, tzinfo=timezone.utc),
        2,
        1,
    )
    first = replace(candidate(valid=False), coverage_gaps=(gap,) * 2048)
    second = replace(
        candidate(valid=False), candidate_index=2, coverage_gaps=(gap,) * 2048
    )
    boundary = replace(
        generation("NO_VALID_SUGGESTION"), candidates=(first, second)
    )
    boundary_wire = project_request_to_wire(
        generation_request(boundary), disposition="created"
    )
    assert len(boundary_wire["record"]["coverage_gaps"]) == 4096
    validator("#/$defs/StaffingReceipt").validate(boundary_wire)

    overflow = replace(
        boundary,
        candidates=(first, replace(second, coverage_gaps=(gap,) * 2049)),
    )
    with pytest.raises(StaffingProjectionError, match="coverage gap"):
        project_request_to_wire(
            generation_request(overflow), disposition="created"
        )


@pytest.mark.parametrize("collection", ("assignments", "exceptions", "generations"))
def test_date_projection_collections_enforce_schema_max_items(
    collection: str,
) -> None:
    assignment = bounded_assignment_projection()
    exception = ExceptionProjection(
        "exception-1",
        date(2026, 10, 6),
        "staff-1",
        "Operator One",
        "LEAVE",
        datetime(2026, 10, 6, 9, tzinfo=timezone.utc),
        datetime(2026, 10, 6, 10, tzinfo=timezone.utc),
        None,
    )
    reserved = generation("RESERVED")
    values = {
        "assignments": assignment,
        "exceptions": exception,
        "generations": reserved,
    }

    def projection(size: int) -> DateProjection:
        rows = (values[collection],) * size
        if collection == "generations":
            return empty_date_projection(generations=rows)
        return rostered_date_projection(
            assignment_count=(size if collection == "assignments" else 0),
            **{collection: rows},
        )

    boundary_wire = project_date_to_wire(
        projection(4096),
        site_id="site-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    wire_key = "active_exceptions" if collection == "exceptions" else collection
    assert len(boundary_wire[wire_key]) == 4096
    with pytest.raises(StaffingProjectionError, match="maximum"):
        project_date_to_wire(
            projection(4097),
            site_id="site-1",
            deployment_id="deployment-1",
            site_timezone="Asia/Shanghai",
        )


def test_effective_plan_assignments_enforce_schema_max_items() -> None:
    assignment = bounded_assignment_projection()

    def projection(size: int) -> DateProjection:
        return rostered_date_projection(
            effective_plan=EffectivePlanState(
                1, "CURRENT", "c" * 64, (), 1, ()
            ),
            effective_plan_schedule=(assignment,) * size,
        )

    boundary_wire = project_date_to_wire(
        projection(4096),
        site_id="site-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    assert len(boundary_wire["effective_plan"]["assignments"]) == 4096
    with pytest.raises(StaffingProjectionError, match="maximum"):
        project_date_to_wire(
            projection(4097),
            site_id="site-1",
            deployment_id="deployment-1",
            site_timezone="Asia/Shanghai",
        )


class FakeOwner:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def date_projection(self, service_date):
        self.calls.append(("date", service_date))
        return empty_date_projection()

    def request_projection(self, operation_kind, request_id):
        self.calls.append(("request", operation_kind, request_id))
        raise AssertionError("request projection should not be reached")

    def cancel_exception(self, payload, *, recorded_at):
        self.calls.append(("cancel", payload, recorded_at))
        return ConflictReceipt("exception-cancel", payload["request_id"], "INVALID_TRANSITION")

    def correct_exception(self, payload, *, recorded_at):
        self.calls.append(("correct", payload, recorded_at))
        return ConflictReceipt("exception-correct", payload["request_id"], "INVALID_TRANSITION")


class FakeWorker:
    def submit(self, payload):
        return {"submitted": copy.deepcopy(payload)}

    def close(self):
        return None


def test_public_wrapper_sanitizes_projection_collection_overflow() -> None:
    assignment = bounded_assignment_projection()

    class OverflowOwner(FakeOwner):
        def date_projection(self, service_date):
            self.calls.append(("date", service_date))
            return rostered_date_projection(
                assignment_count=4097,
                assignments=(assignment,) * 4097,
            )

    router = StaffingRouteAdapter(
        owner=OverflowOwner(),
        worker=FakeWorker(),
        configured=configured_for(cn_settings()),
        site_id="site-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        audit_clock=lambda: NOW,
    )
    api = StaffingApiOperations(router=router, worker=FakeWorker(), ledger=object())
    with pytest.raises(SiteAgentError) as unavailable:
        api.route("GET", "/api/v1/staffing", {})
    assert unavailable.value.code == "staffing_unavailable"
    assert unavailable.value.detail == "staffing evidence is unavailable"


def test_route_dispatch_decodes_once_and_keeps_path_ids_out_of_closed_bodies() -> None:
    owner = FakeOwner()
    adapter = StaffingRouteAdapter(
        owner=owner,
        worker=FakeWorker(),
        configured=configured_for(cn_settings()),
        site_id="site-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        audit_clock=lambda: NOW,
    )
    with pytest.raises(SiteAgentError) as error:
        adapter.dispatch(
            "POST",
            "/api/v1/staffing/exceptions/exception%2D1/cancel",
            {"request_id": "request-1", "exception_id": "exception-1"},
        )
    assert error.value.code == "staffing_invalid_request"
    before_double_decode = tuple(owner.calls)
    with pytest.raises(SiteAgentError) as conflict:
        adapter.dispatch(
            "POST",
            "/api/v1/staffing/exceptions/exception%252D1/cancel",
            {"request_id": "request-1"},
        )
    assert conflict.value.code == "staffing_invalid_request"
    assert tuple(owner.calls) == before_double_decode
    with pytest.raises(SiteAgentError) as invalid_kind:
        adapter.dispatch(
            "GET",
            "/api/v1/staffing/requests/not-an-operation/request-1",
            {},
        )
    assert invalid_kind.value.code == "staffing_invalid_request"
    assert not any(call[0] == "request" for call in owner.calls)

    before = tuple(owner.calls)
    with pytest.raises(SiteAgentError) as invalid_id:
        adapter.dispatch(
            "POST",
            "/api/v1/staffing/exceptions/not%20an%20id/cancel",
            {"request_id": "request-2"},
        )
    assert invalid_id.value.code == "staffing_invalid_request"
    assert tuple(owner.calls) == before

    for suffix, body in (
        (
            "cancel",
            exchange_request("exception-correction.json", "exception-cancelled"),
        ),
        (
            "correct",
            exchange_request("exception-correction.json", "exception-corrected"),
        ),
    ):
        for malformed in (
            {**body, "unexpected": True},
            {key: value for key, value in body.items() if key != "operator"},
        ):
            before_closed_body = tuple(owner.calls)
            with pytest.raises(SiteAgentError) as invalid_body:
                adapter.dispatch(
                    "POST",
                    f"/api/v1/staffing/exceptions/exception-1/{suffix}",
                    malformed,
                )
            assert invalid_body.value.code == "staffing_invalid_request"
            assert tuple(owner.calls) == before_closed_body


def test_route_dispatch_covers_all_eleven_routes_and_keeps_manager_id_out_of_body(
    monkeypatch,
) -> None:
    import scripts.staffing_operations as composition

    class DispatchOwner:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def date_projection(self, service_date):
            self.calls.append(("date", service_date))
            return replace(empty_date_projection(), service_date=service_date)

        def request_projection(self, operation_kind, request_id):
            self.calls.append(("request", operation_kind, request_id))
            return replace(
                generation_request(generation()),
                operation_kind=operation_kind,
                request_id=request_id,
            )

        def import_roster(self, payload, *, recorded_at):
            self.calls.append(("roster", payload, recorded_at))
            return {"operation": "roster"}

        def record_exception(self, payload, *, recorded_at):
            self.calls.append(("record", payload, recorded_at))
            return {"operation": "record"}

        def cancel_exception(self, payload, *, recorded_at):
            self.calls.append(("cancel", payload, recorded_at))
            return {"operation": "cancel"}

        def correct_exception(self, payload, *, recorded_at):
            self.calls.append(("correct", payload, recorded_at))
            return {"operation": "correct"}

        def commit_manager_response(self, payload, *, suggestion_id, recorded_at):
            self.calls.append(("manager", payload, suggestion_id, recorded_at))
            return {"operation": payload["kind"]}

    class DispatchWorker:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def submit(self, payload):
            self.calls.append(payload)
            return {"operation": "suggestion"}

    owner = DispatchOwner()
    worker = DispatchWorker()
    clock = datetime(2026, 10, 5, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(
        composition,
        "with_runtime_context",
        lambda projection, *_args, **_kwargs: {"projection": projection},
    )
    monkeypatch.setattr(
        composition,
        "project_request_to_wire",
        lambda projection, **_kwargs: {"projection": projection},
    )
    monkeypatch.setattr(
        composition, "to_wire_receipt", lambda _owner, result: result
    )
    adapter = StaffingRouteAdapter(
        owner=owner,
        worker=worker,
        configured=configured_for(cn_settings()),
        site_id="site-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        audit_clock=lambda: clock,
    )

    adapter.dispatch("GET", "/api/v1/staffing", {})
    adapter.dispatch("GET", "/api/v1/staffing/dates/2026-10-05", {})
    adapter.dispatch(
        "GET", "/api/v1/staffing/requests/roster-import/request%2D1", {}
    )
    adapter.dispatch("POST", "/api/v1/staffing/roster-imports", {"route": 1})
    adapter.dispatch("POST", "/api/v1/staffing/exceptions", {"route": 2})
    cancel_body = exchange_request(
        "exception-correction.json", "exception-cancelled"
    )
    adapter.dispatch(
        "POST",
        "/api/v1/staffing/exceptions/exception%2D1/cancel",
        cancel_body,
    )
    correct_body = exchange_request(
        "exception-correction.json", "exception-corrected"
    )
    adapter.dispatch(
        "POST",
        "/api/v1/staffing/exceptions/exception%2D1/correct",
        correct_body,
    )
    adapter.dispatch("POST", "/api/v1/staffing/suggestions", {"route": 5})
    for suffix, kind in (("accept", "ACCEPT"), ("modify", "MODIFY"), ("reject", "REJECT")):
        adapter.dispatch(
            "POST",
            f"/api/v1/staffing/suggestions/generation%2D1/{suffix}",
            {"kind": kind, "request_id": f"manager-{suffix}"},
        )

    assert owner.calls[:3] == [
        ("date", date(2026, 10, 6)),
        ("date", date(2026, 10, 5)),
        ("request", "roster-import", "request-1"),
    ]
    assert owner.calls[5][1]["exception_id"] == "exception-1"
    assert owner.calls[6][1]["exception_id"] == "exception-1"
    manager_calls = [row for row in owner.calls if row[0] == "manager"]
    assert [row[2] for row in manager_calls] == ["generation-1"] * 3
    assert all("suggestion_id" not in row[1] and "generation_id" not in row[1] for row in manager_calls)
    assert worker.calls == [{"route": 5}]


def test_route_dispatch_rejects_owner_projection_for_a_different_lookup_identity() -> None:
    class WrongLookupOwner(FakeOwner):
        def date_projection(self, service_date):
            self.calls.append(("date", service_date))
            return empty_date_projection()

        def request_projection(self, operation_kind, request_id):
            self.calls.append(("request", operation_kind, request_id))
            return generation_request(generation())

    owner = WrongLookupOwner()
    adapter = StaffingRouteAdapter(
        owner=owner,
        worker=FakeWorker(),
        configured=configured_for(cn_settings()),
        site_id="site-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        audit_clock=lambda: NOW,
    )
    with pytest.raises(StaffingProjectionError, match="lookup identity"):
        adapter.dispatch("GET", "/api/v1/staffing/dates/2026-10-05", {})
    with pytest.raises(StaffingProjectionError, match="lookup identity"):
        adapter.dispatch(
            "GET", "/api/v1/staffing/requests/roster-import/request-2", {}
        )


def test_public_error_mapping_is_closed_and_redacts_internal_details() -> None:
    expected_conflicts = {
        "IDEMPOTENCY_CONFLICT": "staffing_conflict",
        "STALE_REQUEST": "staffing_conflict",
        "INVALID_TRANSITION": "staffing_conflict",
        "STALE_SUGGESTION": "staffing_stale_suggestion",
    }
    for code, expected in expected_conflicts.items():
        error = staffing_error_for_conflict(
            ConflictReceipt("suggestion-generate", "request-1", code)
        )
        assert error.code == expected

    unknown = ConflictReceipt(
        "suggestion-generate", "request-1", "INVALID_TRANSITION"
    )
    object.__setattr__(unknown, "code", "FUTURE_CONFLICT")
    assert staffing_error_for_conflict(unknown).code == "staffing_unavailable"

    for code, expected in (
        ("REQUEST_NOT_FOUND", "staffing_request_not_found"),
        ("UNKNOWN_GENERATION", "staffing_not_found"),
        ("staffing_roster_not_found", "staffing_not_found"),
        ("staffing_invalid_request", "staffing_invalid_request"),
        ("staffing_invalid_roster", "staffing_invalid_request"),
        ("staffing_unknown_field", "staffing_invalid_request"),
        ("staffing_identity_mismatch", "staffing_invalid_request"),
        ("invalid_exception_time", "staffing_invalid_request"),
        ("unknown_staff_or_shift", "staffing_invalid_request"),
    ):
        assert public_domain_error(StaffingError(code, "private-detail")).code == expected
    internal = public_domain_error(StaffingError("INTEGRITY_FAILURE", "sentinel-secret"))
    assert internal.code == "staffing_unavailable"
    assert "sentinel-secret" not in str(internal)


def test_gateway_evidence_mapper_rejects_wrong_route_disposition_reason_and_token_overflow() -> None:
    good_start = AttemptStarted("generation-1", 0, Provider.KIMI, "model-1", "a" * 64, 10.0)
    assert attempt_route(good_start, region=DeploymentRegion.CN).route_id == "cn-kimi-v1"
    assert started_evidence(good_start, region=DeploymentRegion.CN).attempt_index == 0
    good_finish = AttemptRecord(
        "generation-1",
        0,
        Provider.KIMI,
        "model-1",
        GenerationStatus.SUCCEEDED,
        None,
        False,
        False,
        "provider-request-1",
        "stop",
        TokenUsage(1_000_000, 1_000_000, 2_000_000),
        "a" * 64,
        "b" * 64,
    )
    assert finished_evidence(good_finish, region=DeploymentRegion.CN).input_tokens == 1_000_000
    for malformed in (
        replace(good_start, provider=Provider.OPENAI),
        replace(good_start, timeout_s=15.1),
    ):
        with pytest.raises(StaffingProjectionError):
            started_evidence(malformed, region=DeploymentRegion.CN)
    for malformed in (
        replace(good_finish, finish_reason=None),
        replace(good_finish, retryable=True),
        replace(good_finish, usage=TokenUsage(1_000_001, 0, 1_000_001)),
    ):
        with pytest.raises(StaffingProjectionError):
            finished_evidence(malformed, region=DeploymentRegion.CN)


def test_result_evidence_requires_exact_final_attempt_metadata() -> None:
    attempt = AttemptRecord(
        "generation-1",
        0,
        Provider.KIMI,
        "model-1",
        GenerationStatus.SUCCEEDED,
        None,
        False,
        False,
        "provider-request-1",
        "stop",
        None,
        "a" * 64,
        "b" * 64,
    )
    result = GenerationResult(
        request_id="generation-1",
        status=GenerationStatus.SUCCEEDED,
        output={"candidates": []},
        failure_code=None,
        selected_provider=Provider.KIMI,
        selected_model_id="model-1",
        provider_request_id="provider-request-1",
        finish_reason="stop",
        usage=None,
        input_digest="a" * 64,
        output_digest=stable_digest({"candidates": []}),
        attempts=(replace(attempt, output_digest=stable_digest({"candidates": []})),),
    )
    evidence = result_evidence(result, region=DeploymentRegion.CN, candidate_count=0)
    assert evidence.selected_provider == "KIMI"
    malformed = replace(result, attempts=())
    with pytest.raises(StaffingProjectionError):
        result_evidence(malformed, region=DeploymentRegion.CN, candidate_count=0)
    mismatched_digest = replace(result)
    object.__setattr__(mismatched_digest, "output_digest", "c" * 64)
    with pytest.raises(StaffingProjectionError, match="digest"):
        result_evidence(
            mismatched_digest,
            region=DeploymentRegion.CN,
            candidate_count=0,
        )

    refused_primary = AttemptRecord(
        "generation-1",
        0,
        Provider.OPENAI,
        "gpt-model",
        GenerationStatus.REFUSED,
        FailureCode.PROVIDER_REFUSED,
        False,
        False,
        None,
        None,
        None,
        "a" * 64,
        None,
    )
    successful_output_digest = stable_digest({"candidates": []})
    anthropic_success = replace(
        attempt,
        attempt_index=1,
        provider=Provider.ANTHROPIC,
        model_id="claude-model",
        finish_reason="tool_use",
        output_digest=successful_output_digest,
    )
    illegal_fallback = GenerationResult(
        "generation-1",
        GenerationStatus.SUCCEEDED,
        {"candidates": []},
        None,
        Provider.ANTHROPIC,
        "claude-model",
        "provider-request-1",
        "tool_use",
        None,
        "a" * 64,
        successful_output_digest,
        (refused_primary, anthropic_success),
    )
    with pytest.raises(StaffingProjectionError, match="fallback"):
        result_evidence(
            illegal_fallback,
            region=DeploymentRegion.GLOBAL,
            candidate_count=0,
        )

    failed_primary = replace(
        refused_primary,
        status=GenerationStatus.UNAVAILABLE,
        failure_code=FailureCode.CONNECT_TIMEOUT,
        retryable=True,
    )
    impossible_unconfigured = GenerationResult(
        "generation-1",
        GenerationStatus.CONFIGURATION_ERROR,
        None,
        FailureCode.PROVIDER_UNCONFIGURED,
        None,
        None,
        None,
        None,
        None,
        "a" * 64,
        None,
        (failed_primary,),
    )
    with pytest.raises(StaffingProjectionError, match="unconfigured"):
        result_evidence(
            impossible_unconfigured,
            region=DeploymentRegion.GLOBAL,
            candidate_count=0,
        )


def test_deadline_result_supports_provider_terminal_and_local_gateway_shapes() -> None:
    provider_deadline = AttemptRecord(
        "generation-1",
        0,
        Provider.KIMI,
        "model-1",
        GenerationStatus.UNAVAILABLE,
        FailureCode.DEADLINE_EXHAUSTED,
        False,
        False,
        None,
        None,
        None,
        "a" * 64,
        None,
    )
    provider_result = GenerationResult(
        request_id="generation-1",
        status=GenerationStatus.UNAVAILABLE,
        output=None,
        failure_code=FailureCode.DEADLINE_EXHAUSTED,
        selected_provider=Provider.KIMI,
        selected_model_id="model-1",
        provider_request_id=None,
        finish_reason=None,
        usage=None,
        input_digest="a" * 64,
        output_digest=None,
        attempts=(provider_deadline,),
    )
    provider_evidence = result_evidence(
        provider_result, region=DeploymentRegion.CN, candidate_count=0
    )
    provider_view = generation(
        "UNAVAILABLE",
        starts=(started(),),
        attempts=(finished_evidence(provider_deadline, region=DeploymentRegion.CN),),
        failure_code="DEADLINE_EXHAUSTED",
    )
    provider_projection = replace(
        generation_request(provider_view), result_evidence=provider_evidence
    )
    provider_wire = project_request_to_wire(
        provider_projection, disposition="created"
    )
    assert provider_evidence.selected_provider == "KIMI"
    assert provider_wire["record"]["provenance"][0]["failure_code"] == "DEADLINE_EXHAUSTED"
    with pytest.raises(StaffingProjectionError, match="deadline"):
        project_request_to_wire(
            replace(
                provider_projection,
                result_evidence=replace(
                    provider_evidence,
                    selected_provider=None,
                    selected_model_id=None,
                ),
            ),
            disposition="created",
        )

    failed_primary = AttemptRecord(
        "generation-1",
        0,
        Provider.OPENAI,
        "model-1",
        GenerationStatus.UNAVAILABLE,
        FailureCode.CONNECT_TIMEOUT,
        True,
        False,
        None,
        None,
        None,
        "a" * 64,
        None,
    )
    local_result = GenerationResult(
        request_id="generation-1",
        status=GenerationStatus.UNAVAILABLE,
        output=None,
        failure_code=FailureCode.DEADLINE_EXHAUSTED,
        selected_provider=None,
        selected_model_id=None,
        provider_request_id=None,
        finish_reason=None,
        usage=None,
        input_digest="a" * 64,
        output_digest=None,
        attempts=(failed_primary,),
    )
    local_evidence = result_evidence(
        local_result, region=DeploymentRegion.GLOBAL, candidate_count=0
    )
    local_view = replace(
        generation(
            "UNAVAILABLE",
            starts=(started(provider="OPENAI", region="GLOBAL"),),
            attempts=(
                finished_evidence(failed_primary, region=DeploymentRegion.GLOBAL),
            ),
            failure_code="DEADLINE_EXHAUSTED",
        ),
        route_region="GLOBAL",
        route_readiness="READY",
        primary_provider="OPENAI",
        primary_model_id="model-1",
        backup_provider="ANTHROPIC",
        backup_model_id="model-1",
    )
    local_projection = replace(
        generation_request(local_view), result_evidence=local_evidence
    )
    local_wire = project_request_to_wire(local_projection, disposition="created")
    assert local_evidence.selected_provider is None
    assert local_wire["record"]["provenance"][0]["failure_code"] == "CONNECT_TIMEOUT"
    with pytest.raises(StaffingProjectionError, match="deadline"):
        project_request_to_wire(
            replace(
                local_projection,
                result_evidence=replace(
                    local_evidence,
                    selected_provider="OPENAI",
                    selected_model_id="model-1",
                ),
            ),
            disposition="created",
        )

    zero_attempt = replace(local_result, attempts=())
    assert result_evidence(
        zero_attempt, region=DeploymentRegion.GLOBAL, candidate_count=0
    ).attempts == ()


class ScriptedTransport:
    def __init__(self, body: dict[str, object], *, entered=None, release=None) -> None:
        self.body = body
        self.entered = entered
        self.release = release
        self.calls = 0

    def post(self, *, endpoint, headers, body, timeout_s):
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(10)
        return HttpResponse(
            200,
            {"content-type": "application/json"},
            json.dumps(self.body, separators=(",", ":")).encode(),
        )


class SequenceTransport:
    def __init__(self, responses: list[tuple[int, dict[str, object]]]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []
        self.response_sizes: list[int] = []

    def post(self, *, endpoint, headers, body, timeout_s):
        self.calls.append(endpoint)
        status, response_body = self.responses.pop(0)
        encoded = json.dumps(response_body, separators=(",", ":")).encode()
        self.response_sizes.append(len(encoded))
        return HttpResponse(
            status,
            {"content-type": "application/json"},
            encoded,
        )


def valid_kimi_envelope(output: dict[str, object] | None = None, *, tokens=(10, 4)) -> dict[str, object]:
    return {
        "id": "provider-request-1",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(output or {"candidates": []}, separators=(",", ":")),
                },
            }
        ],
        "usage": {
            "prompt_tokens": tokens[0],
            "completion_tokens": tokens[1],
            "total_tokens": tokens[0] + tokens[1],
        },
    }


def valid_openai_envelope(
    output: dict[str, object] | None = None, *, tokens=(10, 4)
) -> dict[str, object]:
    return {
        "id": "provider-request-openai",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            output or {"candidates": []}, separators=(",", ":")
                        ),
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            "total_tokens": tokens[0] + tokens[1],
        },
    }


def valid_anthropic_envelope(
    output: dict[str, object] | None = None, *, tokens=(10, 4)
) -> dict[str, object]:
    return {
        "id": "provider-request-anthropic",
        "stop_reason": "tool_use",
        "content": [
            {
                "type": "tool_use",
                "id": "tool-use-1",
                "name": "emit_structured_output",
                "input": output or {"candidates": []},
            }
        ],
        "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]},
    }


def roster_request() -> dict[str, object]:
    return exchange_request("roster-import.json", "roster-committed")


def timezone_roster(
    *, timezone_name: str, service_date: str, weekday: int, start: str, end: str
) -> dict[str, object]:
    payload = roster_request()
    payload["site_timezone"] = timezone_name
    payload["effective_from_local_date"] = service_date
    payload["workers"].append(
        {
            "staff_id": "staff-002",
            "display_name": "Local Operator Two",
            "skill_codes": ["BALL_PICKING"],
            "eligibility": [
                {"role_code": "RANGE_ATTENDANT", "area_code": "RANGE_A"}
            ],
            "max_daily_minutes": 600,
        }
    )
    payload["availability"] = [
        {
            "staff_id": staff_id,
            "weekday": weekday,
            "start_local": start,
            "end_local": end,
        }
        for staff_id in ("staff-001", "staff-002")
    ]
    payload["regular_assignments"][0].update(
        {"weekday": weekday, "start_local": start, "end_local": end}
    )
    payload["coverage"][0].update(
        {"weekday": weekday, "start_local": start, "end_local": end}
    )
    return payload


def generation_payload(request_id: str) -> dict[str, object]:
    return {
        "schema": "nxt-staffing-suggestion-generate/v1",
        "request_id": request_id,
        "operator": "manager-1",
        "service_date": "2026-10-06",
        "expected_revisions": {"roster": 1, "exception_set": 0, "effective_plan": 0},
        "retry_of": None,
    }


def wait_for_state(api: StaffingApiOperations, request_id: str, states: set[str], timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        receipt = api.route(
            "GET", f"/api/v1/staffing/requests/suggestion-generate/{request_id}", {}
        )
        if receipt["state"] in states:
            return receipt
        time.sleep(0.01)
    raise AssertionError(f"request {request_id} did not reach {states}")


def test_real_composition_recovers_without_resend_and_closes_owned_ledger(tmp_path: Path) -> None:
    state_root = tmp_path / "stable"
    volatile = tmp_path / "volatile"
    api = build_staffing_operations(
        state_root,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        volatile_out=volatile,
        settings=cn_settings(key=None),
        audit_clock=lambda: NOW,
        monotonic=time.monotonic,
        nonce_factory=lambda: b"n" * 32,
    )
    receipt = api.route("POST", "/api/v1/staffing/roster-imports", roster_request())
    assert receipt["state"] == "COMMITTED"
    reserved = api.route("POST", "/api/v1/staffing/suggestions", generation_payload("request-1"))
    assert reserved["state"] == "RESERVED"
    terminal = wait_for_state(api, "request-1", {"CONFIGURATION_ERROR"})
    assert terminal["record"]["failure_code"] == "PROVIDER_UNCONFIGURED"
    assert terminal["record"]["provenance"] == []
    ledger = api._ledger
    api.close()
    api.close()
    assert ledger._lock_fd == -1
    assert ledger._root_fd == -1
    with pytest.raises(Exception):
        ledger.read()
    with pytest.raises(SiteAgentError) as closed:
        api.route("GET", "/api/v1/staffing", {})
    assert closed.value.code == "staffing_unavailable"


def test_real_exception_correction_preserves_exception_identity_through_api(
    tmp_path: Path,
) -> None:
    api = build_staffing_operations(
        tmp_path / "stable",
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        volatile_out=tmp_path / "volatile",
        settings=cn_settings(key=None),
        audit_clock=lambda: NOW,
        monotonic=time.monotonic,
        nonce_factory=lambda: b"n" * 32,
    )
    try:
        api.route("POST", "/api/v1/staffing/roster-imports", roster_request())
        recorded = api.route(
            "POST",
            "/api/v1/staffing/exceptions",
            exchange_request("exception-correction.json", "exception-recorded"),
        )
        exception_id = recorded["record"]["exception"]["exception_id"]
        correction = exchange_request(
            "exception-correction.json", "exception-corrected"
        )
        correction["expected_exception_set_revision"] = 1
        corrected = api.route(
            "POST",
            f"/api/v1/staffing/exceptions/{exception_id}/correct",
            correction,
        )
        assert corrected["record"]["replaced_exception_id"] == exception_id
        assert corrected["record"]["replacement"]["exception_id"] == exception_id
        validator("#/$defs/StaffingReceipt").validate(corrected)
    finally:
        api.close()


def test_post_reservation_projection_failure_fail_closes_without_orphan_queueing(
    tmp_path: Path, monkeypatch
) -> None:
    import scripts.staffing_operations as composition

    ledger = StaffingLedger(
        tmp_path / "ledger", site_id="site-cn-1", deployment_id="deployment-1"
    )
    owner = StaffingOperations(
        ledger,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    owner.import_roster(roster_request(), recorded_at=NOW)
    configured = configured_for(cn_settings(key=None))
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=cn_settings(key=None),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
    )

    def projection_failure(*_args, **_kwargs):
        raise RuntimeError("private receipt projection failure")

    monkeypatch.setattr(composition, "to_wire_receipt", projection_failure)
    try:
        with pytest.raises(RuntimeError, match="private receipt projection failure"):
            worker.submit(generation_payload("request-orphan"))
        projection = owner.request_projection(
            "suggestion-generate", "request-orphan"
        )
        assert projection.lifecycle_state == "RESERVED"
        with worker._condition:
            assert worker._active is None
            assert tuple(worker._waiting) == ()
            assert worker._failed_closed is True
        with pytest.raises(SiteAgentError) as unavailable:
            worker.submit(generation_payload("request-after-orphan"))
        assert unavailable.value.code == "staffing_unavailable"
    finally:
        worker.close()
        ledger.close()


def real_blocking_api(tmp_path: Path, *, join_timeout: float | None = None):
    root = tmp_path / "real-blocking"
    ledger = StaffingLedger(root, site_id="site-cn-1", deployment_id="deployment-1")
    owner = StaffingOperations(
        ledger,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    owner.import_roster(roster_request(), recorded_at=NOW)
    entered = threading.Event()
    release = threading.Event()
    transport = ScriptedTransport(valid_kimi_envelope(), entered=entered, release=release)
    adapter = KimiAdapter(
        config=ProviderConfig(Provider.KIMI, "kimi-model", "secret"),
        transport=transport,
    )
    gateway = ModelGateway(kimi=adapter, monotonic=time.monotonic)
    route = RoutePolicy(DeploymentRegion.CN)
    configured = ConfiguredGateway(gateway, route, gateway.readiness(route))
    nonces = iter(bytes([value]) * 32 for value in range(1, 50))
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: next(nonces),
    )
    if join_timeout is not None:
        worker.JOIN_TIMEOUT_S = join_timeout
    router = StaffingRouteAdapter(
        owner=owner,
        worker=worker,
        configured=configured,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        audit_clock=lambda: NOW,
    )
    return (
        StaffingApiOperations(router=router, worker=worker, ledger=ledger),
        owner,
        transport,
        entered,
        release,
    )


def test_worker_has_one_active_four_waiting_duplicate_first_and_no_network_lock(tmp_path: Path) -> None:
    api, owner, transport, entered, release = real_blocking_api(tmp_path)
    first = api.route("POST", "/api/v1/staffing/suggestions", generation_payload("request-1"))
    assert first["state"] == "RESERVED"
    assert entered.wait(5)
    waiting_generation_ids = []
    for index in range(2, 6):
        receipt = api.route(
            "POST", "/api/v1/staffing/suggestions", generation_payload(f"request-{index}")
        )
        assert receipt["state"] == "RESERVED"
        waiting_generation_ids.append(receipt["record"]["suggestion_id"])
    before = owner.ledger.verify()
    with pytest.raises(SiteAgentError) as busy:
        api.route("POST", "/api/v1/staffing/suggestions", generation_payload("request-6"))
    assert busy.value.code == "staffing_busy"
    assert owner.ledger.verify() == before
    duplicate = api.route(
        "POST", "/api/v1/staffing/suggestions", generation_payload("request-5")
    )
    assert duplicate["disposition"] == "duplicate"
    exception_body = exchange_request("exception-correction.json", "exception-recorded")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            "root": executor.submit(api.route, "GET", "/api/v1/staffing", {}),
            "date": executor.submit(
                api.route, "GET", "/api/v1/staffing/dates/2026-10-06", {}
            ),
            "request": executor.submit(
                api.route,
                "GET",
                "/api/v1/staffing/requests/suggestion-generate/request-1",
                {},
            ),
            "exception": executor.submit(
                api.route, "POST", "/api/v1/staffing/exceptions", exception_body
            ),
        }
        results = {name: future.result(timeout=2) for name, future in futures.items()}
    assert results["root"]["service_date"] == "2026-10-06"
    assert results["date"]["service_date"] == "2026-10-06"
    assert results["request"]["state"] == "IN_PROGRESS"
    assert results["exception"]["state"] == "COMMITTED"

    closed: list[BaseException] = []
    interrupted: list[str] = []
    original_interrupt = owner.interrupt_generation

    def recording_interrupt(generation_id, *, recorded_at):
        interrupted.append(generation_id)
        return original_interrupt(generation_id, recorded_at=recorded_at)

    owner.interrupt_generation = recording_interrupt

    def close_api():
        try:
            api.close()
        except BaseException as error:  # pragma: no cover - assertion reports it
            closed.append(error)

    closer = threading.Thread(target=close_api)
    closer.start()
    time.sleep(0.05)
    release.set()
    closer.join(5)
    assert not closer.is_alive()
    assert closed == []
    assert transport.calls == 1
    assert interrupted == waiting_generation_ids


def test_real_site_agent_health_remains_available_while_provider_is_blocked(
    tmp_path: Path, launch
) -> None:
    api, _owner, _transport, entered, release = real_blocking_api(
        tmp_path / "staffing"
    )
    service = launch(tmp_path / "site-agent")
    server = SiteAgentApiServer(service, port=0, staffing_operations=api.route)
    server.start_background()

    def request(method: str, path: str, payload=None):
        connection = http.client.HTTPConnection(
            server.host, server.port, timeout=2
        )
        body = None if payload is None else json.dumps(payload).encode()
        headers = {} if body is None else {"Content-Type": "application/json"}
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        status, reserved = request(
            "POST",
            "/api/v1/staffing/suggestions",
            generation_payload("health-while-provider-blocked"),
        )
        assert status == 202
        assert reserved["data"]["state"] == "RESERVED"
        assert entered.wait(5)

        with ThreadPoolExecutor(max_workers=1) as executor:
            health = executor.submit(request, "GET", "/api/v0/health")
            health_status, health_payload = health.result(timeout=2)
        assert health_status == 200
        assert health_payload["data"]["service_state"] == "serving"
        assert release.is_set() is False
    finally:
        release.set()
        api.close()
        server.shutdown()
        service.stop()


def test_attempt_boundaries_are_durable_before_transport_and_terminal(tmp_path: Path) -> None:
    api, owner, transport, entered, release = real_blocking_api(tmp_path)
    try:
        api.route(
            "POST", "/api/v1/staffing/suggestions", generation_payload("request-order")
        )
        assert entered.wait(5)
        before_send_returns = owner.ledger.read().events
        assert before_send_returns[-1].event_type == "provider_attempt_started"
        assert not any(
            event.event_type in {"suggestion_issued", "suggestion_unavailable"}
            for event in before_send_returns
        )

        release.set()
        terminal = wait_for_state(
            api,
            "request-order",
            {
                "SUCCEEDED",
                "NO_VALID_SUGGESTION",
                "UNAVAILABLE",
                "REFUSED",
                "INVALID_RESPONSE",
                "PROVIDER_ERROR",
                "CONFIGURATION_ERROR",
                "SECURITY_ERROR",
            },
        )
        assert terminal["state"] == "NO_VALID_SUGGESTION"
        event_types = [event.event_type for event in owner.ledger.read().events]
        assert event_types[-2:] == [
            "provider_attempt_finished",
            "suggestion_unavailable",
        ]
        assert transport.calls == 1
    finally:
        release.set()
        api.close()


def test_real_worker_close_timeout_keeps_ledger_open_then_retry_closes(tmp_path: Path) -> None:
    api, owner, _transport, entered, release = real_blocking_api(tmp_path, join_timeout=0.03)
    api.route("POST", "/api/v1/staffing/suggestions", generation_payload("request-timeout"))
    assert entered.wait(5)
    with pytest.raises(StaffingShutdownError, match="shutdown incomplete"):
        api.close()
    assert owner.ledger.verify()[0] >= 3
    with pytest.raises(SiteAgentError) as unavailable:
        api.route("GET", "/api/v1/staffing", {})
    assert unavailable.value.code == "staffing_unavailable"
    release.set()
    deadline = time.monotonic() + 5
    while api._worker._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    api.close()
    with pytest.raises(Exception):
        owner.ledger.verify()


@pytest.mark.parametrize("blocked_phase", ("observer", "commit"))
def test_blocked_observer_or_terminal_commit_requires_explicit_close_retry(
    tmp_path: Path, blocked_phase: str
) -> None:
    ledger = StaffingLedger(
        tmp_path / f"blocked-{blocked_phase}",
        site_id="site-cn-1",
        deployment_id="deployment-1",
    )
    real_owner = StaffingOperations(
        ledger,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    real_owner.import_roster(roster_request(), recorded_at=NOW)
    entered = threading.Event()
    release = threading.Event()

    class BlockingOwner:
        def __getattr__(self, name):
            return getattr(real_owner, name)

        def record_attempt_started(self, generation_id, evidence, *, recorded_at):
            if blocked_phase == "observer":
                entered.set()
                assert release.wait(10)
            return real_owner.record_attempt_started(
                generation_id, evidence, recorded_at=recorded_at
            )

        def commit_generation_result(
            self, generation_id, evidence, output, *, recorded_at
        ):
            if blocked_phase == "commit":
                entered.set()
                assert release.wait(10)
            return real_owner.commit_generation_result(
                generation_id, evidence, output, recorded_at=recorded_at
            )

    owner = BlockingOwner()
    transport = ScriptedTransport(valid_kimi_envelope())
    adapter = KimiAdapter(
        config=ProviderConfig(Provider.KIMI, "kimi-model", "secret"),
        transport=transport,
    )
    gateway = ModelGateway(kimi=adapter, monotonic=time.monotonic)
    route = RoutePolicy(DeploymentRegion.CN)
    configured = ConfiguredGateway(gateway, route, gateway.readiness(route))
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
    )
    worker.JOIN_TIMEOUT_S = 0.03
    router = StaffingRouteAdapter(
        owner=owner,
        worker=worker,
        configured=configured,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        audit_clock=lambda: NOW,
    )
    api = StaffingApiOperations(router=router, worker=worker, ledger=ledger)
    try:
        api.route(
            "POST",
            "/api/v1/staffing/suggestions",
            generation_payload(f"blocked-{blocked_phase}"),
        )
        assert entered.wait(5)
        with pytest.raises(StaffingShutdownError, match="shutdown incomplete") as error:
            api.close()
        assert "secret" not in str(error.value)
        assert ledger._lock_fd >= 0 and ledger._root_fd >= 0
        assert transport.calls == (0 if blocked_phase == "observer" else 1)
        with pytest.raises(SiteAgentError) as unavailable:
            api.route("GET", "/api/v1/staffing", {})
        assert unavailable.value.code == "staffing_unavailable"
    finally:
        release.set()
        deadline = time.monotonic() + 5
        while worker._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        api.close()
    assert ledger._lock_fd == -1 and ledger._root_fd == -1


@pytest.mark.parametrize("crash_stage", ("RESERVED", "STARTED", "FINISHED"))
def test_startup_recovery_never_resends_and_only_explicit_retry_calls_provider(
    tmp_path: Path, monkeypatch, crash_stage: str
) -> None:
    import scripts.staffing_operations as composition

    state_root = tmp_path / "stable"
    volatile = tmp_path / "volatile"
    root = resolve_staffing_root(
        state_root,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        volatile_out=volatile,
    )
    ledger = StaffingLedger(root, site_id="site-cn-1", deployment_id="deployment-1")
    owner = StaffingOperations(
        ledger,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    owner.import_roster(roster_request(), recorded_at=NOW)
    configured = configured_for(cn_settings())
    evidence = generation_route_evidence(configured, cn_settings())
    assert evidence is not None
    owner.reserve_generation(
        generation_payload("request-recover"),
        alias_nonce=b"r" * 32,
        route_evidence=evidence,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        language="zh-CN",
        recorded_at=NOW,
    )
    projection = owner.request_projection("suggestion-generate", "request-recover")
    assert projection.generation_id is not None
    generation_id = projection.generation_id
    if crash_stage in {"STARTED", "FINISHED"}:
        work = owner.generation_work(generation_id)
        route = AttemptRouteEvidence(
            "KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-model"
        )
        begin = AttemptStartedEvidence(
            generation_id, 0, route, work.input_digest, 10.0
        )
        owner.record_attempt_started(generation_id, begin, recorded_at=NOW)
        if crash_stage == "FINISHED":
            end = AttemptFinishedEvidence(
                generation_id,
                0,
                route,
                "SUCCEEDED",
                None,
                False,
                False,
                "provider-request-recovery",
                "stop",
                stable_digest({"candidates": []}),
                10,
                4,
            )
            owner.record_attempt_finished(generation_id, end, recorded_at=NOW)
    ledger.close()

    transport = ScriptedTransport(valid_kimi_envelope())
    adapter = KimiAdapter(
        config=ProviderConfig(Provider.KIMI, "kimi-model", "secret"),
        transport=transport,
    )
    gateway = ModelGateway(kimi=adapter, monotonic=time.monotonic)
    route = RoutePolicy(DeploymentRegion.CN)
    recovered_configured = ConfiguredGateway(gateway, route, gateway.readiness(route))
    monkeypatch.setattr(
        composition,
        "configured_gateway",
        lambda _settings, *, monotonic: recovered_configured,
    )

    api = build_staffing_operations(
        state_root,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        volatile_out=volatile,
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        monotonic=time.monotonic,
        nonce_factory=iter((b"z" * 32, b"y" * 32)).__next__,
    )
    try:
        recovered = api.route(
            "GET", "/api/v1/staffing/requests/suggestion-generate/request-recover", {}
        )
        assert recovered["state"] == "RESULT_UNKNOWN"
        assert recovered["record"]["failure_code"] == "RESULT_UNKNOWN"
        assert transport.calls == 0

        duplicate = api.route(
            "POST", "/api/v1/staffing/suggestions", generation_payload("request-recover")
        )
        assert duplicate["disposition"] == "duplicate"
        assert duplicate["state"] == "RESULT_UNKNOWN"
        assert transport.calls == 0

        retry = generation_payload(f"request-retry-{crash_stage.lower()}")
        retry["retry_of"] = generation_id
        reserved_retry = api.route("POST", "/api/v1/staffing/suggestions", retry)
        assert reserved_retry["state"] == "RESERVED"
        terminal = wait_for_state(
            api,
            retry["request_id"],
            {"SUCCEEDED", "NO_VALID_SUGGESTION"},
        )
        assert terminal["state"] == "NO_VALID_SUGGESTION"
        assert transport.calls == 1
    finally:
        api.close()


@pytest.mark.parametrize(
    (
        "timezone_name",
        "service_date",
        "weekday",
        "window",
        "start_at",
        "end_at",
    ),
    (
        (
            "UTC",
            "2026-10-05",
            0,
            ("09:00", "17:00"),
            "2026-10-05T10:00:00Z",
            "2026-10-05T10:30:00Z",
        ),
        (
            "Asia/Shanghai",
            "2026-10-05",
            0,
            ("09:00", "17:00"),
            "2026-10-05T10:00:00+08:00",
            "2026-10-05T10:30:00+08:00",
        ),
        (
            "America/New_York",
            "2026-11-01",
            6,
            ("00:00", "03:00"),
            "2026-11-01T01:15:00-04:00",
            "2026-11-01T01:30:00-04:00",
        ),
        (
            "America/New_York",
            "2026-11-01",
            6,
            ("00:00", "03:00"),
            "2026-11-01T01:15:00-05:00",
            "2026-11-01T01:30:00-05:00",
        ),
    ),
)
def test_restart_api_preserves_offsets_and_accepts_byte_unchanged_modify(
    tmp_path: Path,
    timezone_name: str,
    service_date: str,
    weekday: int,
    window: tuple[str, str],
    start_at: str,
    end_at: str,
) -> None:
    state_root = tmp_path / "stable"
    volatile = tmp_path / "volatile"
    root = resolve_staffing_root(
        state_root,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        volatile_out=volatile,
    )
    ledger = StaffingLedger(
        root, site_id="site-cn-1", deployment_id="deployment-1"
    )
    owner = StaffingOperations(
        ledger,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone=timezone_name,
    )
    owner.import_roster(
        timezone_roster(
            timezone_name=timezone_name,
            service_date=service_date,
            weekday=weekday,
            start=window[0],
            end=window[1],
        ),
        recorded_at=NOW,
    )
    request = generation_payload("generation-timezone")
    request["service_date"] = service_date
    settings = cn_settings()
    route = generation_route_evidence(configured_for(settings), settings)
    assert route is not None
    owner.reserve_generation(
        request,
        alias_nonce=b"t" * 32,
        route_evidence=route,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        language="zh-CN",
        recorded_at=NOW,
    )
    generation_id = owner.request_projection(
        "suggestion-generate", "generation-timezone"
    ).generation_id
    assert generation_id is not None
    work = owner.generation_work(generation_id)
    worker_alias = next(
        alias
        for alias, staff_id in work.worker_alias_to_staff_id
        if staff_id == "staff-002"
    )
    output = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [
                    {
                        "operation": "ADD",
                        "worker_alias": worker_alias,
                        "role_code": "RANGE_ATTENDANT",
                        "area_code": "RANGE_A",
                        "start_at": start_at,
                        "end_at": end_at,
                    }
                ],
                "rationale": "add reserve coverage",
                "operational_warnings": [],
            }
        ]
    }
    output_digest = stable_digest(output)
    attempt_route_evidence = AttemptRouteEvidence(
        "KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-model"
    )
    begin = AttemptStartedEvidence(
        generation_id, 0, attempt_route_evidence, work.input_digest, 10.0
    )
    finish = AttemptFinishedEvidence(
        generation_id,
        0,
        attempt_route_evidence,
        "SUCCEEDED",
        None,
        False,
        False,
        None,
        "stop",
        output_digest,
        None,
        None,
    )
    result = ResultEvidence(
        generation_id,
        "SUCCEEDED",
        None,
        "KIMI",
        "kimi-model",
        None,
        "stop",
        work.input_digest,
        output_digest,
        (finish,),
        1,
        None,
    )
    owner.record_attempt_started(generation_id, begin, recorded_at=NOW)
    owner.record_attempt_finished(generation_id, finish, recorded_at=NOW)
    owner.commit_generation_result(generation_id, result, output, recorded_at=NOW)
    ledger.close()

    api = build_staffing_operations(
        state_root,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone=timezone_name,
        volatile_out=volatile,
        settings=settings,
        audit_clock=lambda: NOW,
        monotonic=time.monotonic,
        nonce_factory=lambda: b"n" * 32,
    )
    try:
        date_wire = api.route(
            "GET", f"/api/v1/staffing/dates/{service_date}", {}
        )
        request_wire = api.route(
            "GET",
            "/api/v1/staffing/requests/suggestion-generate/generation-timezone",
            {},
        )
        recovered_after_lost_response = api.route(
            "GET",
            "/api/v1/staffing/requests/suggestion-generate/generation-timezone",
            {},
        )
        assert recovered_after_lost_response == request_wire
        validator("#/$defs/StaffingDateSnapshot").validate(date_wire)
        validator("#/$defs/StaffingReceipt").validate(request_wire)
        date_add = date_wire["generations"][0]["candidates"][0]["operations"][0]
        request_add = request_wire["record"]["candidates"][0]["operations"][0]
        assert date_add == request_add
        assert (request_add["start_at"], request_add["end_at"]) == (
            start_at,
            end_at,
        )
        assert "+00:00" not in request_add["start_at"]
        assert "-00:00" not in request_add["start_at"]
        public_json = json.dumps(
            {"date": date_wire, "request": request_wire},
            ensure_ascii=False,
            sort_keys=True,
        )
        for alias, _staff_id in work.worker_alias_to_staff_id:
            assert alias not in public_json
        for alias, _assignment_id in work.assignment_alias_to_assignment_id:
            assert alias not in public_json
        for forbidden in (
            "provider_payload",
            "alias_nonce",
            "prompt_template_version",
            "raw_response",
            "reasoning",
            "api_key",
            "headers",
            "endpoint",
            "latency",
        ):
            assert forbidden not in public_json
        assert roster_request()["source_ref"] not in public_json

        manager_add = {
            key: request_add[key]
            for key in (
                "operation",
                "staff_id",
                "role_code",
                "area_code",
                "start_at",
                "end_at",
            )
        }
        manager_body = {
            "schema": "nxt-staffing-manager-response/v1",
            "request_id": f"manager-{weekday}-{stable_digest(start_at)[:8]}",
            "operator": "manager-1",
            "kind": "MODIFY",
            "expected_revisions": {
                key: request_wire["record"]["basis"][key]
                for key in ("roster", "exception_set", "effective_plan")
            },
            "candidate_index": 1,
            "edited_operations": [manager_add],
            "reason_code": "APPROVED_WITH_CHANGES",
            "note": None,
        }
        if timezone_name == "UTC":
            for index, signed_zero in enumerate(("+00:00", "-00:00"), start=1):
                invalid = copy.deepcopy(manager_body)
                invalid["request_id"] = f"manager-signed-zero-{index}"
                invalid["edited_operations"][0]["start_at"] = start_at[:-1] + signed_zero
                invalid["edited_operations"][0]["end_at"] = end_at[:-1] + signed_zero
                with pytest.raises(SiteAgentError) as error:
                    api.route(
                        "POST",
                        f"/api/v1/staffing/suggestions/{generation_id}/modify",
                        invalid,
                    )
                assert error.value.code == "staffing_invalid_request"

        receipt = api.route(
            "POST",
            f"/api/v1/staffing/suggestions/{generation_id}/modify",
            manager_body,
        )
        validator("#/$defs/StaffingReceipt").validate(receipt)
        assert receipt["state"] == "COMMITTED"
        assert receipt["record"]["response_kind"] == "MODIFY"
        assert (
            receipt["record"]["effective_plan"]["schedule_digest"]
            == request_wire["record"]["candidates"][0]["schedule_digest"]
        )
        applied = next(
            row
            for row in receipt["record"]["effective_plan"]["assignments"]
            if row["staff_id"] == "staff-002"
        )
        parse_wire = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        assert parse_wire(applied["start_at"]).astimezone(timezone.utc) == parse_wire(
            start_at
        ).astimezone(timezone.utc)
        assert parse_wire(applied["end_at"]).astimezone(timezone.utc) == parse_wire(
            end_at
        ).astimezone(timezone.utc)
    finally:
        api.close()


class ConstructionOwner:
    def __init__(self, semantic: dict[str, object]) -> None:
        self.semantic = semantic
        self.committed: list[ResultEvidence] = []
        self.interrupted: list[str] = []

    def generation_work(self, generation_id):
        semantic = self.semantic

        class Work:
            basis_snapshot = object()
            provider_payload = object()
            input_digest = stable_digest(semantic)

        return Work()

    def commit_generation_result(self, generation_id, evidence, output, *, recorded_at):
        self.committed.append(evidence)

    def interrupt_generation(self, generation_id, *, recorded_at):
        self.interrupted.append(generation_id)


class RecordingGateway:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request, route, *, observer):
        self.calls += 1
        return GenerationResult(
            request.request_id,
            GenerationStatus.CONFIGURATION_ERROR,
            None,
            FailureCode.INPUT_TOO_LARGE,
            None,
            None,
            None,
            None,
            None,
            request.canonical_input_digest,
            None,
            (),
        )


class ObserverOwner(ConstructionOwner):
    def __init__(self, semantic: dict[str, object], *, fail_phase: str | None) -> None:
        super().__init__(semantic)
        self.fail_phase = fail_phase
        self.starts: list[AttemptStartedEvidence] = []
        self.finishes: list[AttemptFinishedEvidence] = []
        self.interrupt_fails = False

    def record_attempt_started(self, generation_id, evidence, *, recorded_at):
        if self.fail_phase == "started":
            raise RuntimeError("private started failure")
        self.starts.append(evidence)

    def record_attempt_finished(self, generation_id, evidence, *, recorded_at):
        if self.fail_phase == "finished":
            raise RuntimeError("private finished failure")
        self.finishes.append(evidence)

    def interrupt_generation(self, generation_id, *, recorded_at):
        if self.interrupt_fails:
            raise RuntimeError("private interruption failure")
        super().interrupt_generation(generation_id, recorded_at=recorded_at)


@pytest.mark.parametrize(("phase", "first_transport_calls"), (("started", 0), ("finished", 1)))
def test_observer_append_failure_interrupts_once_without_terminal_or_failed_close(
    monkeypatch, phase: str, first_transport_calls: int
) -> None:
    import scripts.staffing_operations as composition

    semantic = {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "messages": (
            {"role": "system", "content": "system"},
            {"role": "user", "content": "{}"},
        ),
        "output_schema": composition.STAFFING_SUGGESTION_OUTPUT_SCHEMA,
        "max_output_tokens": 2048,
    }
    owner = ObserverOwner(semantic, fail_phase=phase)
    transport = ScriptedTransport(valid_kimi_envelope())
    adapter = KimiAdapter(
        config=ProviderConfig(Provider.KIMI, "kimi-model", "secret"),
        transport=transport,
    )
    gateway = ModelGateway(kimi=adapter, monotonic=time.monotonic)
    route = RoutePolicy(DeploymentRegion.CN)
    configured = ConfiguredGateway(gateway, route, gateway.readiness(route))
    monkeypatch.setattr(composition, "canonical_generation_input", lambda *_args: semantic)
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
    )
    try:
        worker._execute(GenerationWorkItem("request-1", "event-1", "generation-1"))
        assert transport.calls == first_transport_calls
        assert owner.interrupted == ["generation-1"]
        assert owner.committed == []
        assert worker._failed_closed is False

        owner.fail_phase = None
        worker._execute(GenerationWorkItem("request-2", "event-2", "generation-2"))
        assert owner.interrupted == ["generation-1"]
        assert len(owner.committed) == 1
        assert owner.committed[0].request_id == "generation-2"
        assert worker._failed_closed is False
    finally:
        worker.close()


def test_observer_failure_with_failed_interrupt_fail_closes_generation_admission(
    monkeypatch,
) -> None:
    import scripts.staffing_operations as composition

    semantic = {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "messages": (
            {"role": "system", "content": "system"},
            {"role": "user", "content": "{}"},
        ),
        "output_schema": composition.STAFFING_SUGGESTION_OUTPUT_SCHEMA,
        "max_output_tokens": 2048,
    }
    owner = ObserverOwner(semantic, fail_phase="started")
    owner.interrupt_fails = True
    transport = ScriptedTransport(valid_kimi_envelope())
    adapter = KimiAdapter(
        config=ProviderConfig(Provider.KIMI, "kimi-model", "secret"),
        transport=transport,
    )
    gateway = ModelGateway(kimi=adapter, monotonic=time.monotonic)
    route = RoutePolicy(DeploymentRegion.CN)
    monkeypatch.setattr(composition, "canonical_generation_input", lambda *_args: semantic)
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=ConfiguredGateway(gateway, route, gateway.readiness(route)),
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
    )
    worker._execute(GenerationWorkItem("request-1", "event-1", "generation-1"))
    assert worker._failed_closed is True
    assert owner.committed == []
    with pytest.raises(SiteAgentError) as unavailable:
        worker.submit({})
    assert unavailable.value.code == "staffing_unavailable"
    worker.close()


def test_real_gateway_observer_rejects_null_success_finish_and_durably_interrupts(
    tmp_path: Path,
) -> None:
    class NullFinishKimi(KimiAdapter):
        def _parse(self, envelope, schema):
            return {"candidates": []}, "provider-request-null-finish", None, None

    transport = ScriptedTransport(valid_kimi_envelope())
    gateway = ModelGateway(
        kimi=NullFinishKimi(
            config=ProviderConfig(Provider.KIMI, "kimi-model", "secret"),
            transport=transport,
        ),
        monotonic=time.monotonic,
    )
    route = RoutePolicy(DeploymentRegion.CN)
    configured = ConfiguredGateway(gateway, route, gateway.readiness(route))
    ledger = StaffingLedger(
        tmp_path / "observer-null-finish",
        site_id="site-cn-1",
        deployment_id="deployment-1",
    )
    owner = StaffingOperations(
        ledger,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    owner.import_roster(roster_request(), recorded_at=NOW)
    baseline = len(ledger.read().events)
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"o" * 32,
    )
    router = StaffingRouteAdapter(
        owner=owner,
        worker=worker,
        configured=configured,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        audit_clock=lambda: NOW,
    )
    api = StaffingApiOperations(router=router, worker=worker, ledger=ledger)
    try:
        api.route(
            "POST",
            "/api/v1/staffing/suggestions",
            generation_payload("null-finish"),
        )
        terminal = wait_for_state(api, "null-finish", {"RESULT_UNKNOWN"})
        assert terminal["record"]["failure_code"] == "RESULT_UNKNOWN"
        assert terminal["record"]["provenance"] == []
        event_types = [event.event_type for event in ledger.read().events[baseline:]]
        assert event_types == [
            "generation_reserved",
            "provider_attempt_started",
            "generation_interrupted",
        ]
        assert "provider_attempt_finished" not in event_types
        assert "suggestion_issued" not in event_types
        assert "suggestion_unavailable" not in event_types
        assert worker._failed_closed is False
        assert transport.calls == 1
    finally:
        api.close()


@pytest.mark.parametrize("overflow_provider", ("KIMI", "OPENAI", "ANTHROPIC"))
@pytest.mark.parametrize(
    "overflow", ((1_000_001, 4), (4, 1_000_001)), ids=("input", "output")
)
def test_real_adapter_token_overflow_interrupts_and_worker_runs_next_item(
    monkeypatch, overflow_provider: str, overflow: tuple[int, int]
) -> None:
    import scripts.staffing_operations as composition

    semantic = {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "messages": (
            {"role": "system", "content": "system"},
            {"role": "user", "content": "{}"},
        ),
        "output_schema": composition.STAFFING_SUGGESTION_OUTPUT_SCHEMA,
        "max_output_tokens": 2048,
    }
    owner = ObserverOwner(semantic, fail_phase=None)
    if overflow_provider == "KIMI":
        transport = SequenceTransport(
            [
                (200, valid_kimi_envelope(tokens=overflow)),
                (200, valid_kimi_envelope()),
            ]
        )
        gateway = ModelGateway(
            kimi=KimiAdapter(
                config=ProviderConfig(Provider.KIMI, "kimi-model", "secret"),
                transport=transport,
            ),
            monotonic=time.monotonic,
        )
        route = RoutePolicy(DeploymentRegion.CN)
        settings = cn_settings()
    elif overflow_provider == "OPENAI":
        transport = SequenceTransport(
            [
                (200, valid_openai_envelope(tokens=overflow)),
                (200, valid_openai_envelope()),
            ]
        )
        gateway = ModelGateway(
            openai=OpenAIAdapter(
                config=ProviderConfig(Provider.OPENAI, "gpt-model", "secret"),
                transport=transport,
            ),
            monotonic=time.monotonic,
        )
        route = RoutePolicy(DeploymentRegion.GLOBAL)
        settings = global_settings(anthropic_key=None)
    else:
        transport = SequenceTransport(
            [
                (503, {}),
                (200, valid_anthropic_envelope(tokens=overflow)),
                (503, {}),
                (200, valid_anthropic_envelope()),
            ]
        )
        gateway = ModelGateway(
            openai=OpenAIAdapter(
                config=ProviderConfig(Provider.OPENAI, "gpt-model", "secret"),
                transport=transport,
            ),
            anthropic=AnthropicAdapter(
                config=ProviderConfig(Provider.ANTHROPIC, "claude-model", "secret"),
                transport=transport,
            ),
            monotonic=time.monotonic,
        )
        route = RoutePolicy(DeploymentRegion.GLOBAL)
        settings = global_settings()
    configured = ConfiguredGateway(gateway, route, gateway.readiness(route))
    monkeypatch.setattr(composition, "canonical_generation_input", lambda *_args: semantic)
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=settings,
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
    )
    try:
        worker._execute(GenerationWorkItem("request-1", "event-1", "generation-1"))
        assert owner.interrupted == ["generation-1"]
        assert owner.committed == []
        assert worker._failed_closed is False

        worker._execute(GenerationWorkItem("request-2", "event-2", "generation-2"))
        assert len(owner.committed) == 1
        assert owner.committed[0].request_id == "generation-2"
        assert worker._failed_closed is False
        assert transport.responses == []
        assert max(transport.response_sizes) <= 524_288
    finally:
        worker.close()


@pytest.mark.parametrize("provider_name", ("KIMI", "OPENAI", "ANTHROPIC"))
@pytest.mark.parametrize("oversized_kind", ("warnings", "operations"))
def test_schema_valid_oversized_provider_tree_is_domain_invalid_and_queue_stays_healthy(
    tmp_path: Path, provider_name: str, oversized_kind: str
) -> None:
    if oversized_kind == "warnings":
        oversized_output = {
            "candidates": [
                {
                    "candidate_index": 1,
                    "operations": [],
                    "rationale": "bounded later by the domain",
                    "operational_warnings": [""] * 4097,
                }
            ]
        }
    else:
        oversized_output = {
            "candidates": [
                {
                    "candidate_index": 1,
                    "operations": [
                        {"operation": "REMOVE", "assignment_alias": "assignment-alias"}
                        for _ in range(1500)
                    ],
                    "rationale": "bounded later by the domain",
                    "operational_warnings": [],
                }
            ]
        }
    if provider_name == "KIMI":
        transport = SequenceTransport(
            [
                (200, valid_kimi_envelope(oversized_output)),
                (200, valid_kimi_envelope()),
            ]
        )
        gateway = ModelGateway(
            kimi=KimiAdapter(
                config=ProviderConfig(Provider.KIMI, "kimi-model", "secret"),
                transport=transport,
            ),
            monotonic=time.monotonic,
        )
        route = RoutePolicy(DeploymentRegion.CN)
        settings = cn_settings()
    elif provider_name == "OPENAI":
        transport = SequenceTransport(
            [
                (200, valid_openai_envelope(oversized_output)),
                (200, valid_openai_envelope()),
            ]
        )
        gateway = ModelGateway(
            openai=OpenAIAdapter(
                config=ProviderConfig(Provider.OPENAI, "gpt-model", "secret"),
                transport=transport,
            ),
            monotonic=time.monotonic,
        )
        route = RoutePolicy(DeploymentRegion.GLOBAL)
        settings = global_settings(anthropic_key=None)
    else:
        transport = SequenceTransport(
            [
                (503, {}),
                (200, valid_anthropic_envelope(oversized_output)),
                (503, {}),
                (200, valid_anthropic_envelope()),
            ]
        )
        gateway = ModelGateway(
            openai=OpenAIAdapter(
                config=ProviderConfig(Provider.OPENAI, "gpt-model", "secret"),
                transport=transport,
            ),
            anthropic=AnthropicAdapter(
                config=ProviderConfig(Provider.ANTHROPIC, "claude-model", "secret"),
                transport=transport,
            ),
            monotonic=time.monotonic,
        )
        route = RoutePolicy(DeploymentRegion.GLOBAL)
        settings = global_settings()

    ledger = StaffingLedger(
        tmp_path / f"oversized-{provider_name.lower()}-{oversized_kind}",
        site_id="site-cn-1",
        deployment_id="deployment-1",
    )
    owner = StaffingOperations(
        ledger,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    owner.import_roster(roster_request(), recorded_at=NOW)
    configured = ConfiguredGateway(gateway, route, gateway.readiness(route))
    nonces = iter((b"a" * 32, b"b" * 32))
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=settings,
        audit_clock=lambda: NOW,
        nonce_factory=nonces.__next__,
    )
    router = StaffingRouteAdapter(
        owner=owner,
        worker=worker,
        configured=configured,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        audit_clock=lambda: NOW,
    )
    api = StaffingApiOperations(router=router, worker=worker, ledger=ledger)
    try:
        first_id = f"large-{provider_name.lower()}-{oversized_kind}"
        api.route("POST", "/api/v1/staffing/suggestions", generation_payload(first_id))
        invalid = wait_for_state(api, first_id, {"INVALID_RESPONSE"})
        assert invalid["record"]["failure_code"] == "invalid_provider_shape"
        assert worker._failed_closed is False

        second_id = f"next-{provider_name.lower()}-{oversized_kind}"
        api.route("POST", "/api/v1/staffing/suggestions", generation_payload(second_id))
        healthy = wait_for_state(api, second_id, {"NO_VALID_SUGGESTION"})
        assert healthy["record"]["failure_code"] == "NO_VALID_SUGGESTION"
        assert worker._failed_closed is False
        assert transport.responses == []
        assert max(transport.response_sizes) <= 524_288
    finally:
        api.close()


@pytest.mark.parametrize(
    "operation",
    (
        {
            "operation": "ADD",
            "worker_alias": "worker_unknown",
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_A",
            "start_at": "2026-10-06T09:00:00+08:00",
            "end_at": "2026-10-06T10:00:00+08:00",
        },
        {"operation": "REMOVE", "assignment_alias": "assignment_unknown"},
    ),
    ids=("worker", "assignment"),
)
def test_real_gateway_and_ledger_reject_unknown_provider_aliases(
    tmp_path: Path, operation: dict[str, object]
) -> None:
    output = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [operation],
                "rationale": "untrusted alias",
                "operational_warnings": [],
            }
        ]
    }
    transport = SequenceTransport([(200, valid_kimi_envelope(output))])
    gateway = ModelGateway(
        kimi=KimiAdapter(
            config=ProviderConfig(Provider.KIMI, "kimi-model", "secret"),
            transport=transport,
        ),
        monotonic=time.monotonic,
    )
    route = RoutePolicy(DeploymentRegion.CN)
    configured = ConfiguredGateway(gateway, route, gateway.readiness(route))
    ledger = StaffingLedger(
        tmp_path / f"unknown-{operation['operation'].lower()}",
        site_id="site-cn-1",
        deployment_id="deployment-1",
    )
    owner = StaffingOperations(
        ledger,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
    )
    owner.import_roster(roster_request(), recorded_at=NOW)
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"u" * 32,
    )
    router = StaffingRouteAdapter(
        owner=owner,
        worker=worker,
        configured=configured,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        audit_clock=lambda: NOW,
    )
    api = StaffingApiOperations(router=router, worker=worker, ledger=ledger)
    request_id = f"unknown-{operation['operation'].lower()}"
    try:
        api.route(
            "POST",
            "/api/v1/staffing/suggestions",
            generation_payload(request_id),
        )
        terminal = wait_for_state(api, request_id, {"INVALID_RESPONSE"})
        assert terminal["record"]["failure_code"] == "invalid_candidate_set"
        assert len(terminal["record"]["provenance"]) == 1
        assert worker._failed_closed is False
        assert transport.responses == []
    finally:
        api.close()


@pytest.mark.parametrize(
    ("fault", "expected_gateway_calls"),
    (("generation_work", 0), ("digest", 0), ("message_container", 0), ("commit", 1)),
)
def test_integrity_drift_fail_closes_and_never_promotes_queued_work(
    monkeypatch, fault: str, expected_gateway_calls: int
) -> None:
    import scripts.staffing_operations as composition

    messages: tuple[dict[str, str], ...] | list[dict[str, str]] = (
        {"role": "system", "content": "system"},
        {"role": "user", "content": "{}"},
    )
    if fault == "message_container":
        messages = list(messages)
    semantic = {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "messages": messages,
        "output_schema": composition.STAFFING_SUGGESTION_OUTPUT_SCHEMA,
        "max_output_tokens": 2048,
    }

    class IntegrityOwner(ConstructionOwner):
        def generation_work(self, generation_id):
            if fault == "generation_work":
                raise RuntimeError("private generation read failure")
            work = super().generation_work(generation_id)
            if fault == "digest":
                class DriftedWork:
                    basis_snapshot = work.basis_snapshot
                    provider_payload = work.provider_payload
                    input_digest = "9" * 64

                return DriftedWork()
            return work

        def commit_generation_result(self, generation_id, evidence, output, *, recorded_at):
            if fault == "commit":
                raise RuntimeError("private terminal commit failure")
            super().commit_generation_result(
                generation_id, evidence, output, recorded_at=recorded_at
            )

    owner = IntegrityOwner(semantic)
    gateway = RecordingGateway()
    route = RoutePolicy(DeploymentRegion.CN)
    configured = ConfiguredGateway(
        gateway,
        route,
        RouteReadiness(
            DeploymentRegion.CN,
            RouteReadinessStatus.READY,
            Provider.KIMI,
            None,
            None,
        ),
    )
    monkeypatch.setattr(composition, "canonical_generation_input", lambda *_args: semantic)
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
        start=False,
    )
    with worker._condition:
        worker._active = GenerationWorkItem("request-1", "event-1", "generation-1")
        worker._waiting.append(
            GenerationWorkItem("request-2", "event-2", "generation-2")
        )
    try:
        worker.start()
        deadline = time.monotonic() + 2
        while not worker._failed_closed and gateway.calls < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert worker._failed_closed is True
        assert gateway.calls == expected_gateway_calls
        assert tuple(worker._waiting) == (
            GenerationWorkItem("request-2", "event-2", "generation-2"),
        )
    finally:
        worker.close()


@pytest.mark.parametrize("fatal_phase", ("generation_work", "gateway", "commit"))
def test_base_exception_marks_failed_closed_preserves_waiting_and_is_re_raised(
    monkeypatch, fatal_phase: str
) -> None:
    import scripts.staffing_operations as composition

    class FatalSignal(BaseException):
        pass

    semantic = {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "messages": (
            {"role": "system", "content": "system"},
            {"role": "user", "content": "{}"},
        ),
        "output_schema": composition.STAFFING_SUGGESTION_OUTPUT_SCHEMA,
        "max_output_tokens": 2048,
    }

    class FatalOwner(ConstructionOwner):
        def generation_work(self, generation_id):
            if fatal_phase == "generation_work":
                raise FatalSignal("private fatal generation read")
            return super().generation_work(generation_id)

        def commit_generation_result(self, generation_id, evidence, output, *, recorded_at):
            if fatal_phase == "commit":
                raise FatalSignal("private fatal terminal commit")
            return super().commit_generation_result(
                generation_id, evidence, output, recorded_at=recorded_at
            )

    class FatalGateway(RecordingGateway):
        def generate(self, request, route, *, observer):
            if fatal_phase == "gateway":
                raise FatalSignal("private fatal gateway")
            return super().generate(request, route, observer=observer)

    owner = FatalOwner(semantic)
    gateway = FatalGateway()
    route = RoutePolicy(DeploymentRegion.CN)
    monkeypatch.setattr(composition, "canonical_generation_input", lambda *_args: semantic)
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=ConfiguredGateway(
            gateway,
            route,
            RouteReadiness(
                DeploymentRegion.CN,
                RouteReadinessStatus.READY,
                Provider.KIMI,
                None,
                None,
            ),
        ),
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
        start=False,
    )
    active = GenerationWorkItem("request-1", "event-1", "generation-1")
    waiting = GenerationWorkItem("request-2", "event-2", "generation-2")
    with worker._condition:
        worker._active = active
        worker._waiting.append(waiting)
    with pytest.raises(FatalSignal, match="private fatal"):
        worker._run()
    assert worker._failed_closed is True
    assert worker._active is None
    assert tuple(worker._waiting) == (waiting,)
    worker.close()


def test_background_base_exception_is_fail_closed_without_thread_output(
    monkeypatch, capfd
) -> None:
    sentinel = "PRIVATE-BACKGROUND-BASE-EXCEPTION"

    class FatalSignal(BaseException):
        pass

    class FatalOwner:
        def generation_work(self, generation_id):
            raise FatalSignal(sentinel)

    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
    worker = BoundedGenerationWorker(
        owner=FatalOwner(),
        configured=configured_for(cn_settings()),
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
        start=False,
    )
    with worker._condition:
        worker._active = GenerationWorkItem(
            "request-private", "event-private", "generation-private"
        )
    worker.start()
    worker._thread.join(timeout=2)
    assert worker._thread.is_alive() is False
    assert worker._failed_closed is True
    worker.close()
    captured = capfd.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err


@pytest.mark.parametrize(("size", "gateway_calls"), ((32768, 1), (32769, 0)))
def test_message_character_limit_is_healthy_zero_attempt_terminal(
    monkeypatch, size: int, gateway_calls: int
) -> None:
    from nxt_pilot_ops.staffing.projection import STAFFING_SUGGESTION_OUTPUT_SCHEMA
    import scripts.staffing_operations as composition

    semantic = {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "messages": (
            {"role": "system", "content": "x" * size},
            {"role": "user", "content": "{}"},
        ),
        "output_schema": STAFFING_SUGGESTION_OUTPUT_SCHEMA,
        "max_output_tokens": 2048,
    }
    owner = ConstructionOwner(semantic)
    gateway = RecordingGateway()
    configured = ConfiguredGateway(
        gateway,
        RoutePolicy(DeploymentRegion.CN),
        RouteReadiness(
            DeploymentRegion.CN,
            RouteReadinessStatus.READY,
            Provider.KIMI,
            None,
            None,
        ),
    )
    monkeypatch.setattr(composition, "canonical_generation_input", lambda *_args: semantic)
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
    )
    worker._execute(GenerationWorkItem("request-1", "event-1", "generation-1"))
    assert gateway.calls == gateway_calls
    assert owner.committed[-1].status == "CONFIGURATION_ERROR"
    assert owner.committed[-1].failure_code == "INPUT_TOO_LARGE"
    assert owner.committed[-1].attempts == ()
    assert worker._failed_closed is False
    worker.close()


@pytest.mark.parametrize(
    ("region", "readiness", "settings", "expected_code"),
    (
        (
            DeploymentRegion.CN,
            RouteReadiness(
                DeploymentRegion.CN,
                RouteReadinessStatus.READY,
                Provider.KIMI,
                None,
                None,
            ),
            cn_settings(),
            "INPUT_TOO_LARGE",
        ),
        (
            DeploymentRegion.GLOBAL,
            RouteReadiness(
                DeploymentRegion.GLOBAL,
                RouteReadinessStatus.DEGRADED_BACKUP_UNCONFIGURED,
                Provider.OPENAI,
                None,
                FailureCode.BACKUP_UNCONFIGURED,
            ),
            global_settings(anthropic_key=None),
            "INPUT_TOO_LARGE",
        ),
        (
            DeploymentRegion.CN,
            RouteReadiness(
                DeploymentRegion.CN,
                RouteReadinessStatus.UNAVAILABLE,
                Provider.KIMI,
                None,
                FailureCode.PROVIDER_UNCONFIGURED,
            ),
            cn_settings(key=None),
            "PROVIDER_UNCONFIGURED",
        ),
    ),
)
def test_unconfigured_route_takes_priority_over_oversized_valid_messages(
    monkeypatch,
    region: DeploymentRegion,
    readiness: RouteReadiness,
    settings: StaffingProviderSettings,
    expected_code: str,
) -> None:
    import scripts.staffing_operations as composition

    semantic = {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "messages": (
            {"role": "system", "content": "x" * 32769},
            {"role": "user", "content": "{}"},
        ),
        "output_schema": composition.STAFFING_SUGGESTION_OUTPUT_SCHEMA,
        "max_output_tokens": 2048,
    }
    owner = ConstructionOwner(semantic)
    gateway = RecordingGateway()
    monkeypatch.setattr(composition, "canonical_generation_input", lambda *_args: semantic)
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=ConfiguredGateway(gateway, RoutePolicy(region), readiness),
        settings=settings,
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
    )
    try:
        worker._execute(GenerationWorkItem("request-1", "event-1", "generation-1"))
        assert gateway.calls == 0
        assert owner.committed[-1].failure_code == expected_code
        assert owner.committed[-1].attempts == ()
        assert worker._failed_closed is False
    finally:
        worker.close()


def test_oversized_message_is_local_only_after_role_and_row_validation(
    monkeypatch,
) -> None:
    import scripts.staffing_operations as composition

    semantic = {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "messages": (
            {"role": "invalid-role", "content": "x" * 32769},
            {"role": "user", "content": "{}"},
        ),
        "output_schema": composition.STAFFING_SUGGESTION_OUTPUT_SCHEMA,
        "max_output_tokens": 2048,
    }
    owner = ConstructionOwner(semantic)
    gateway = RecordingGateway()
    route = RoutePolicy(DeploymentRegion.CN)
    monkeypatch.setattr(
        composition, "canonical_generation_input", lambda *_args: semantic
    )
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=ConfiguredGateway(
            gateway,
            route,
            RouteReadiness(
                DeploymentRegion.CN,
                RouteReadinessStatus.READY,
                Provider.KIMI,
                None,
                None,
            ),
        ),
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
        start=False,
    )
    worker._execute(GenerationWorkItem("request-1", "event-1", "generation-1"))
    assert gateway.calls == 0
    assert owner.committed == []
    assert worker._failed_closed is True
    worker.close()


def test_nonce_reuse_fail_close_notifies_idle_worker_without_leaking_nonce() -> None:
    semantic = {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "messages": (
            {"role": "system", "content": "system"},
            {"role": "user", "content": "{}"},
        ),
        "output_schema": {},
        "max_output_tokens": 2048,
    }
    nonce = b"private-nonce-material-123456789"[:32]
    gateway = RecordingGateway()
    route = RoutePolicy(DeploymentRegion.CN)
    worker = BoundedGenerationWorker(
        owner=ConstructionOwner(semantic),
        configured=ConfiguredGateway(
            gateway,
            route,
            RouteReadiness(
                DeploymentRegion.CN,
                RouteReadinessStatus.READY,
                Provider.KIMI,
                None,
                None,
            ),
        ),
        settings=cn_settings(),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: nonce,
    )
    with worker._condition:
        assert worker._fresh_nonce() == nonce
        with pytest.raises(SiteAgentError) as unavailable:
            worker._fresh_nonce()
    assert unavailable.value.code == "staffing_unavailable"
    assert nonce.decode(errors="ignore") not in str(unavailable.value)
    worker._thread.join(timeout=2)
    assert not worker._thread.is_alive()
    worker.close()


def test_constructor_failure_closes_ledger_before_worker_start(tmp_path: Path, monkeypatch) -> None:
    import scripts.staffing_operations as composition

    captured: list[StaffingLedger] = []
    real_ledger = StaffingLedger

    def tracking_ledger(*args, **kwargs):
        ledger = real_ledger(*args, **kwargs)
        captured.append(ledger)
        return ledger

    monkeypatch.setattr(composition, "StaffingLedger", tracking_ledger)
    with pytest.raises(Exception):
        build_staffing_operations(
            tmp_path / "stable",
            site_id="site-1",
            deployment_id="deployment-1",
            site_timezone="Not/A_Zone",
            volatile_out=tmp_path / "volatile",
            settings=cn_settings(),
            audit_clock=lambda: NOW,
            monotonic=time.monotonic,
            nonce_factory=lambda: b"n" * 32,
        )
    assert len(captured) == 1
    with pytest.raises(Exception):
        captured[0].verify()


class FatalConstructionSignal(BaseException):
    pass


@pytest.mark.parametrize("failure_type", (RuntimeError, FatalConstructionSignal))
def test_constructor_failure_after_worker_allocation_still_precedes_thread_start(
    tmp_path: Path, monkeypatch, failure_type: type[BaseException]
) -> None:
    import scripts.staffing_operations as composition

    ledgers: list[StaffingLedger] = []
    workers: list[BoundedGenerationWorker] = []
    real_ledger = StaffingLedger
    real_worker = BoundedGenerationWorker

    def tracking_ledger(*args, **kwargs):
        ledger = real_ledger(*args, **kwargs)
        ledgers.append(ledger)
        return ledger

    def tracking_worker(*args, **kwargs):
        worker = real_worker(*args, **kwargs)
        workers.append(worker)
        return worker

    class FailingRouter:
        def __init__(self, **_kwargs):
            raise failure_type("private router assembly failure")

    monkeypatch.setattr(composition, "StaffingLedger", tracking_ledger)
    monkeypatch.setattr(composition, "BoundedGenerationWorker", tracking_worker)
    monkeypatch.setattr(composition, "StaffingRouteAdapter", FailingRouter)
    with pytest.raises(failure_type, match="private router assembly failure"):
        build_staffing_operations(
            tmp_path / "stable",
            site_id="site-1",
            deployment_id="deployment-1",
            site_timezone="UTC",
            volatile_out=tmp_path / "volatile",
            settings=cn_settings(),
            audit_clock=lambda: NOW,
            monotonic=time.monotonic,
            nonce_factory=lambda: b"n" * 32,
        )
    assert len(workers) == len(ledgers) == 1
    assert workers[0]._started is False
    assert workers[0]._closing is True
    assert ledgers[0]._lock_fd == -1 and ledgers[0]._root_fd == -1


def test_worker_close_marks_failed_closed_and_rethrows_interrupt_base_exception() -> None:
    class FatalInterruptOwner:
        def interrupt_generation(self, generation_id, *, recorded_at):
            raise FatalConstructionSignal("fatal queued interruption")

    worker = BoundedGenerationWorker(
        owner=FatalInterruptOwner(),
        configured=configured_for(cn_settings(key=None)),
        settings=cn_settings(key=None),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
        start=False,
    )
    with worker._condition:
        worker._waiting.append(
            GenerationWorkItem("request-1", "event-1", "generation-1")
        )
    with pytest.raises(FatalConstructionSignal, match="fatal queued interruption"):
        worker.close()
    assert worker._closing is True
    assert worker._failed_closed is True
    assert tuple(worker._waiting) == ()


def test_close_stops_detached_interrupts_after_concurrent_fail_close() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingInterruptOwner:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def interrupt_generation(self, generation_id, *, recorded_at):
            self.calls.append(generation_id)
            if len(self.calls) == 1:
                entered.set()
                assert release.wait(5)

    owner = BlockingInterruptOwner()
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured_for(cn_settings(key=None)),
        settings=cn_settings(key=None),
        audit_clock=lambda: NOW,
        nonce_factory=lambda: b"n" * 32,
        start=False,
    )
    with worker._condition:
        worker._waiting.extend(
            GenerationWorkItem(f"request-{index}", f"event-{index}", f"generation-{index}")
            for index in range(3)
        )
    close_errors: list[BaseException] = []

    def close_worker() -> None:
        try:
            worker.close()
        except BaseException as error:  # pragma: no cover - assertion reports it
            close_errors.append(error)

    closer = threading.Thread(target=close_worker)
    closer.start()
    try:
        assert entered.wait(2)
        worker._mark_failed_closed()
    finally:
        release.set()
    closer.join(timeout=2)
    assert closer.is_alive() is False
    assert close_errors == []
    assert owner.calls == ["generation-0"]
    assert worker._failed_closed is True


def test_close_timeout_is_retryable_and_does_not_close_ledger(tmp_path: Path, monkeypatch) -> None:
    class BlockingWorker:
        def __init__(self):
            self.calls = 0

        def close(self):
            self.calls += 1
            if self.calls == 1:
                raise StaffingShutdownError("staffing shutdown incomplete")

    root = tmp_path / "ledger"
    ledger = StaffingLedger(root, site_id="site-1", deployment_id="deployment-1")
    worker = BlockingWorker()
    api = StaffingApiOperations(router=object(), worker=worker, ledger=ledger)
    with pytest.raises(StaffingShutdownError, match="shutdown incomplete"):
        api.close()
    assert ledger.verify() == (0, "0" * 64)
    with pytest.raises(SiteAgentError):
        api.route("GET", "/api/v1/staffing", {})
    api.close()
    with pytest.raises(Exception):
        ledger.verify()


def test_public_wrapper_redacts_internal_exception_prompt_and_provider_body(
    tmp_path: Path, capsys
) -> None:
    sentinel = "moonshot-secret raw-prompt raw-provider-body https://private-endpoint"

    class FailingRouter:
        def dispatch(self, method, path, payload):
            raise RuntimeError(sentinel)

    ledger = StaffingLedger(
        tmp_path / "redaction", site_id="site-1", deployment_id="deployment-1"
    )
    api = StaffingApiOperations(
        router=FailingRouter(), worker=FakeWorker(), ledger=ledger
    )
    try:
        with pytest.raises(SiteAgentError) as unavailable:
            api.route("GET", "/api/v1/staffing", {})
        assert unavailable.value.code == "staffing_unavailable"
        assert sentinel not in str(unavailable.value)
        captured = capsys.readouterr()
        assert sentinel not in captured.out + captured.err
    finally:
        api.close()
