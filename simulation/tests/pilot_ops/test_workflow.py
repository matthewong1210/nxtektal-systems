from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from nxt_pilot_ops.contracts import Recommendation, RecommendationAction
from nxt_pilot_ops.workflow import (
    CaseStatus,
    ExecutionAcknowledgement,
    ExecutionRequest,
    RecommendationCase,
    RecommendationOutcome,
    RecommendationResponse,
    ResponseKind,
)


NOW = datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)


def _recommendation() -> Recommendation:
    return Recommendation.create(
        site_id="site-1",
        deployment_id="deployment-1",
        issued_at=NOW,
        execute_before=NOW + timedelta(minutes=20),
        action=RecommendationAction.DISPATCH_COLLECTOR,
        target_robot_id="R1",
        policy_id="ball-availability-guardian",
        policy_version="0.1.0",
        decision_trace_id="trace-1",
        expected_stockout_without_action_minutes=30.0,
        expected_stockout_with_action_minutes=70.0,
        protected_through_horizon=False,
        projected_stockout_delay_minutes=40.0,
        summary="Recommend collector dispatch for human review.",
    )


def _response(
    recommendation: Recommendation,
    kind: ResponseKind,
    *,
    minute: int = 1,
) -> RecommendationResponse:
    return RecommendationResponse.create(
        recommendation_id=recommendation.recommendation_id,
        responded_at=NOW + timedelta(minutes=minute),
        operator_id="operator-1",
        kind=kind,
        reason_code="REVIEWED",
    )


def _request(case: RecommendationCase, *, minute: int = 2) -> ExecutionRequest:
    return ExecutionRequest.create(
        recommendation_id=case.recommendation.recommendation_id,
        effective_recommendation_id=case.effective_recommendation_id,
        requested_at=NOW + timedelta(minutes=minute),
        requested_by="operator-1",
        note="Human workflow request only.",
    )


def _acknowledgement(
    case: RecommendationCase,
    request: ExecutionRequest,
    *,
    minute: int = 3,
) -> ExecutionAcknowledgement:
    return ExecutionAcknowledgement.create(
        recommendation_id=case.recommendation.recommendation_id,
        effective_recommendation_id=case.effective_recommendation_id,
        request_id=request.request_id,
        acknowledged_at=NOW + timedelta(minutes=minute),
        acknowledged_by="operator-2",
    )


def test_accept_and_reject_are_single_immutable_responses() -> None:
    recommendation = _recommendation()
    accepted = RecommendationCase(recommendation).apply_response(
        _response(recommendation, ResponseKind.ACCEPT)
    )
    assert accepted.status is CaseStatus.ACCEPTED
    with pytest.raises(FrozenInstanceError):
        accepted.response.reason_code = "CHANGED"  # type: ignore[misc,union-attr]
    with pytest.raises(ValueError, match="only one"):
        accepted.apply_response(_response(recommendation, ResponseKind.REJECT, minute=2))

    rejected = RecommendationCase(recommendation).apply_response(
        _response(recommendation, ResponseKind.REJECT)
    )
    assert rejected.status is CaseStatus.REJECTED
    with pytest.raises(ValueError, match="accepted or modified"):
        rejected.apply_execution_request(_request(rejected))


def test_modify_preserves_original_and_creates_distinct_effective_record() -> None:
    recommendation = _recommendation()
    response = RecommendationResponse.create(
        recommendation_id=recommendation.recommendation_id,
        responded_at=NOW + timedelta(minutes=2),
        operator_id="operator-1",
        kind=ResponseKind.MODIFY,
        reason_code="USE_OPERATOR_INTERVENTION",
        note="Route conditions changed.",
        original_recommendation=recommendation,
        replacement_action=RecommendationAction.OPERATOR_INTERVENTION,
        replacement_execute_before=NOW + timedelta(minutes=8),
    )
    case = RecommendationCase(recommendation).apply_response(response)
    modified = response.modified_recommendation
    assert modified is not None
    assert modified.original_recommendation is recommendation
    assert modified.modified_recommendation_id != recommendation.recommendation_id
    assert case.effective_recommendation_id == modified.modified_recommendation_id
    assert case.status is CaseStatus.MODIFIED
    assert recommendation.action is RecommendationAction.DISPATCH_COLLECTOR
    assert modified.action is RecommendationAction.OPERATOR_INTERVENTION


def test_modify_rejects_deadline_before_the_response() -> None:
    recommendation = _recommendation()
    with pytest.raises(ValueError, match="cannot precede modification"):
        RecommendationResponse.create(
            recommendation_id=recommendation.recommendation_id,
            responded_at=NOW + timedelta(minutes=2),
            operator_id="operator-1",
            kind=ResponseKind.MODIFY,
            reason_code="CHANGE",
            original_recommendation=recommendation,
            replacement_action=RecommendationAction.OPERATOR_INTERVENTION,
            replacement_execute_before=NOW + timedelta(minutes=1),
        )


def test_execution_records_are_ordered_human_workflow_only() -> None:
    recommendation = _recommendation()
    case = RecommendationCase(recommendation).apply_response(
        _response(recommendation, ResponseKind.ACCEPT)
    )
    request = _request(case)
    case = case.apply_execution_request(request)
    assert case.status is CaseStatus.EXECUTION_REQUESTED
    acknowledgement = _acknowledgement(case, request)
    case = case.apply_execution_acknowledgement(acknowledgement)
    assert case.status is CaseStatus.EXECUTION_ACKNOWLEDGED
    outcome = RecommendationOutcome.create(
        recommendation_id=recommendation.recommendation_id,
        effective_recommendation_id=case.effective_recommendation_id,
        recorded_at=NOW + timedelta(minutes=10),
        executed=True,
        actual_start_at=NOW + timedelta(minutes=4),
        actual_completed_at=NOW + timedelta(minutes=9),
        balls_returned=3_800,
        stockout_occurred=False,
        stockout_minutes=0,
    )
    case = case.apply_outcome(outcome)
    assert case.status is CaseStatus.OUTCOME_RECORDED
    with pytest.raises(ValueError, match="only one recorded outcome"):
        case.apply_outcome(outcome)


def test_executed_outcome_requires_acknowledgement_and_correct_effective_id() -> None:
    recommendation = _recommendation()
    case = RecommendationCase(recommendation).apply_response(
        _response(recommendation, ResponseKind.ACCEPT)
    )
    outcome = RecommendationOutcome.create(
        recommendation_id=recommendation.recommendation_id,
        effective_recommendation_id="mod-unknown",
        recorded_at=NOW + timedelta(minutes=10),
        executed=True,
        actual_start_at=NOW + timedelta(minutes=4),
        actual_completed_at=NOW + timedelta(minutes=9),
        balls_returned=100,
        stockout_occurred=False,
        stockout_minutes=0,
    )
    with pytest.raises(ValueError, match="wrong effective recommendation"):
        case.apply_outcome(outcome)

    correct = RecommendationOutcome.create(
        recommendation_id=recommendation.recommendation_id,
        effective_recommendation_id=recommendation.recommendation_id,
        recorded_at=NOW + timedelta(minutes=10),
        executed=True,
        actual_start_at=NOW + timedelta(minutes=4),
        actual_completed_at=NOW + timedelta(minutes=9),
        balls_returned=100,
        stockout_occurred=False,
        stockout_minutes=0,
    )
    with pytest.raises(ValueError, match="requires execution acknowledgement"):
        case.apply_outcome(correct)

    with pytest.raises(ValueError, match="effective_recommendation_id"):
        RecommendationOutcome.create(
            recommendation_id=recommendation.recommendation_id,
            effective_recommendation_id="",
            recorded_at=NOW + timedelta(minutes=10),
            executed=False,
            actual_start_at=None,
            actual_completed_at=None,
            balls_returned=0,
            stockout_occurred=False,
            stockout_minutes=0,
        )


def test_outcome_and_response_time_ordering_is_enforced() -> None:
    recommendation = _recommendation()
    response = _response(recommendation, ResponseKind.REJECT, minute=5)
    case = RecommendationCase(recommendation).apply_response(response)
    too_early = RecommendationOutcome.create(
        recommendation_id=recommendation.recommendation_id,
        recorded_at=NOW + timedelta(minutes=4),
        executed=False,
        actual_start_at=None,
        actual_completed_at=None,
        balls_returned=0,
        stockout_occurred=True,
        stockout_minutes=1,
    )
    with pytest.raises(ValueError, match="cannot precede"):
        case.apply_outcome(too_early)

    outcome = RecommendationOutcome.create(
        recommendation_id=recommendation.recommendation_id,
        recorded_at=NOW + timedelta(minutes=6),
        executed=False,
        actual_start_at=None,
        actual_completed_at=None,
        balls_returned=0,
        stockout_occurred=False,
        stockout_minutes=0,
    )
    recorded = case.apply_outcome(outcome)
    with pytest.raises(ValueError, match="only one operator response"):
        recorded.apply_response(_response(recommendation, ResponseKind.ACCEPT, minute=7))
