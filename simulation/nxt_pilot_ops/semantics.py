"""Cross-contract semantic verification for Shadow Ops policy results."""

from __future__ import annotations

from datetime import timedelta

from .contracts import (
    DecisionTrace,
    EvaluationVerdict,
    InboundBallBatch,
    Recommendation,
    RecommendationAction,
    derive_demand_projection,
)
from .projection import project_stockout_minutes
from .serialization import to_primitive


def verify_trace_projections(trace: DecisionTrace) -> None:
    """Reproduce every recorded stockout from the trace's source facts."""

    expected_demand = derive_demand_projection(
        current_demand_balls_per_minute=(
            trace.current_demand_balls_per_minute
        ),
        forecast_demand_balls_per_minute=(
            trace.forecast_demand_balls_per_minute
        ),
        forecast_bucket_minutes=trace.forecast_bucket_minutes,
        minutes_to_close=trace.minutes_to_close,
        config=trace.policy_config,
    )
    if expected_demand is None:
        if (
            trace.selected_demand_basis != "unavailable"
            or trace.demand_rates_used_balls_per_minute
            or trace.projection_horizon_minutes is not None
            or trace.projected_stockout_without_action_minutes is not None
        ):
            raise ValueError("trace demand projection does not match policy derivation")
        return

    expected_rates, demand_bucket, expected_horizon, expected_basis = (
        expected_demand
    )
    if (
        trace.selected_demand_basis != expected_basis
        or trace.demand_rates_used_balls_per_minute != expected_rates
        or trace.projection_horizon_minutes != expected_horizon
    ):
        raise ValueError("trace demand projection does not match policy derivation")

    expected_baseline = project_stockout_minutes(
        initial_clean_balls=trace.inventory_balls,
        demand_rates_balls_per_minute=expected_rates,
        demand_bucket_minutes=demand_bucket,
        inbound_batches=trace.committed_inbound_batches,
        horizon_minutes=expected_horizon,
    )
    if expected_baseline != trace.projected_stockout_without_action_minutes:
        raise ValueError(
            "trace baseline stockout does not match its inventory projection"
        )

    for candidate in trace.candidates:
        has_only_counterfactual_reason = candidate.exclusion_reasons in {
            (),
            ("no_positive_counterfactual_extension",),
        }
        if expected_baseline is None or not has_only_counterfactual_reason:
            continue
        assert candidate.replenishment_eta_minutes is not None
        assert candidate.expected_clean_ball_yield is not None
        counterfactual = InboundBallBatch(
            source_id=f"counterfactual:{candidate.robot_id}",
            eta_minutes=candidate.replenishment_eta_minutes,
            balls=candidate.expected_clean_ball_yield,
            provenance="semantic replay of recorded counterfactual",
        )
        expected_with_action = project_stockout_minutes(
            initial_clean_balls=trace.inventory_balls,
            demand_rates_balls_per_minute=expected_rates,
            demand_bucket_minutes=demand_bucket,
            inbound_batches=trace.committed_inbound_batches + (counterfactual,),
            horizon_minutes=expected_horizon,
        )
        expected_protected = expected_with_action is None
        expected_extension = (
            expected_horizon - expected_baseline
            if expected_protected
            else max(0.0, expected_with_action - expected_baseline)
        )
        if (
            candidate.projected_stockout_with_action_minutes
            != expected_with_action
            or candidate.protected_through_horizon != expected_protected
            or candidate.projected_extension_minutes != expected_extension
        ):
            raise ValueError(
                f"candidate {candidate.robot_id} counterfactual does not match "
                "the recorded inventory projection"
            )


def expected_policy_result(
    trace: DecisionTrace,
) -> tuple[EvaluationVerdict, RecommendationAction | None]:
    """Return the only verdict/action pair admitted by the v0.1 policy."""

    if trace.selected_demand_basis == "unavailable":
        if trace.range_open is False:
            return EvaluationVerdict.NO_ACTION, None
        return EvaluationVerdict.RECOMMEND, RecommendationAction.OPERATOR_INTERVENTION
    if trace.projected_stockout_without_action_minutes is None:
        return EvaluationVerdict.NO_ACTION, None
    if trace.selected_robot_id is not None:
        assert trace.latest_safe_dispatch_delay_minutes is not None
        if (
            trace.latest_safe_dispatch_delay_minutes
            > trace.policy_config.alert_lead_minutes
        ):
            return EvaluationVerdict.NO_ACTION, None
        return EvaluationVerdict.RECOMMEND, RecommendationAction.DISPATCH_COLLECTOR
    return EvaluationVerdict.RECOMMEND, RecommendationAction.OPERATOR_INTERVENTION


def validate_recommendation_for_trace(
    recommendation: Recommendation,
    trace: DecisionTrace,
) -> None:
    """Validate exact policy action, projections, linkage, and deadline."""

    verify_trace_projections(trace)
    expected_verdict, expected_action = expected_policy_result(trace)
    if expected_verdict is not EvaluationVerdict.RECOMMEND:
        raise ValueError("decision trace does not admit a recommendation")
    assert expected_action is not None
    if recommendation.action is not expected_action:
        raise ValueError("recommendation action does not match the policy decision")
    if recommendation.decision_trace_id != trace.trace_id:
        raise ValueError("recommendation and trace IDs do not match")
    if (recommendation.site_id, recommendation.deployment_id) != (
        trace.site_id,
        trace.deployment_id,
    ):
        raise ValueError("recommendation and trace site/deployment do not match")
    if (recommendation.policy_id, recommendation.policy_version) != (
        trace.policy_id,
        trace.policy_version,
    ):
        raise ValueError("recommendation and trace policy do not match")
    if recommendation.issued_at != trace.observed_at:
        raise ValueError("recommendation issuance must equal trace observation time")
    if (
        recommendation.expected_stockout_without_action_minutes
        != trace.projected_stockout_without_action_minutes
    ):
        raise ValueError(
            "recommendation baseline stockout does not match the decision trace"
        )

    if expected_action is RecommendationAction.DISPATCH_COLLECTOR:
        if recommendation.target_robot_id != trace.selected_robot_id:
            raise ValueError(
                "dispatch target does not match the decision trace selection"
            )
        selected = next(
            candidate
            for candidate in trace.candidates
            if candidate.robot_id == trace.selected_robot_id
        )
        if (
            recommendation.expected_stockout_with_action_minutes
            != selected.projected_stockout_with_action_minutes
            or recommendation.protected_through_horizon
            != selected.protected_through_horizon
            or recommendation.projected_stockout_delay_minutes
            != selected.projected_extension_minutes
        ):
            raise ValueError(
                "dispatch recommendation projections do not match the selected "
                "candidate"
            )
        assert trace.latest_safe_dispatch_delay_minutes is not None
        expected_deadline = recommendation.issued_at + timedelta(
            minutes=max(0.0, trace.latest_safe_dispatch_delay_minutes)
        )
        canonical_deadline = to_primitive(expected_deadline)
        assert isinstance(canonical_deadline, str)
        expected_summary = (
            f"Recommend operator dispatch of {selected.robot_id} by "
            f"{canonical_deadline} to extend clean-ball availability."
        )
    else:
        if recommendation.target_robot_id is not None:
            raise ValueError("operator intervention must not target a robot")
        if (
            recommendation.expected_stockout_with_action_minutes is not None
            or recommendation.protected_through_horizon
            or recommendation.projected_stockout_delay_minutes != 0.0
        ):
            raise ValueError(
                "operator-intervention projections must remain explicitly "
                "non-dispatch"
            )
        baseline = trace.projected_stockout_without_action_minutes
        operator_delay = (
            0.0
            if baseline is None
            else max(0.0, baseline - trace.policy_config.safety_buffer_minutes)
        )
        expected_deadline = recommendation.issued_at + timedelta(
            minutes=operator_delay
        )
        reason = (
            "demand is unavailable, so clean-ball risk cannot be assessed"
            if baseline is None
            else (
                "no eligible collector can arrive before and extend the first "
                "stockout"
            )
        )
        expected_summary = f"Recommend operator intervention: {reason}."

    if recommendation.execute_before != expected_deadline:
        raise ValueError("recommendation deadline does not match the decision trace")
    if recommendation.summary != expected_summary:
        raise ValueError("recommendation summary does not match the policy decision")


def validate_policy_evaluation(
    *,
    verdict: EvaluationVerdict,
    trace: DecisionTrace,
    recommendation: Recommendation | None,
) -> None:
    """Validate a composed policy evaluation as one self-consistent record."""

    verify_trace_projections(trace)
    expected_verdict, _ = expected_policy_result(trace)
    if verdict is not expected_verdict:
        raise ValueError("evaluation verdict does not match the policy decision")
    if expected_verdict is EvaluationVerdict.NO_ACTION:
        if recommendation is not None:
            raise ValueError("no-action evaluation cannot contain a recommendation")
        return
    if recommendation is None:
        raise ValueError("recommend verdict requires a recommendation")
    validate_recommendation_for_trace(recommendation, trace)
