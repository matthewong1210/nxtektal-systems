"""Ball Availability Guardian v0.1.

The guardian emits deterministic recommendations and traces only.  It has no
robot-command, actuator, motion-planning, charging-control, or e-stop API.
"""

from __future__ import annotations

from datetime import timedelta

from .contracts import (
    BallAvailabilityPolicyConfig,
    DecisionTrace,
    EvaluationVerdict,
    InboundBallBatch,
    OperationalSnapshot,
    PolicyEvaluation,
    Recommendation,
    RecommendationAction,
    RobotCandidateTrace,
    RobotOperationalState,
    candidate_safety_exclusion_reasons,
    derive_demand_projection,
)
from .projection import project_stockout_minutes
from .serialization import stable_digest, to_primitive

class BallAvailabilityGuardian:
    def __init__(self, config: BallAvailabilityPolicyConfig | None = None) -> None:
        if config is None:
            config = BallAvailabilityPolicyConfig()
        elif not isinstance(config, BallAvailabilityPolicyConfig):
            raise TypeError("config must be a BallAvailabilityPolicyConfig or None")
        self._config = config

    @property
    def config(self) -> BallAvailabilityPolicyConfig:
        return self._config

    def evaluate(self, snapshot: OperationalSnapshot) -> PolicyEvaluation:
        demand = self._demand_projection(snapshot)
        if demand is None:
            demand_rates: tuple[float, ...] = ()
            demand_bucket = None
            projection_horizon = None
            demand_basis = "unavailable"
            baseline_stockout = None
        else:
            demand_rates, demand_bucket, projection_horizon, demand_basis = demand
            baseline_stockout = project_stockout_minutes(
                initial_clean_balls=snapshot.decision_inventory_balls,
                demand_rates_balls_per_minute=demand_rates,
                demand_bucket_minutes=demand_bucket,
                inbound_batches=snapshot.inbound_batches,
                horizon_minutes=projection_horizon,
            )

        candidates = tuple(
            self._evaluate_robot(
                robot=robot,
                snapshot=snapshot,
                demand_rates=demand_rates,
                demand_bucket=demand_bucket,
                projection_horizon=projection_horizon,
                baseline_stockout=baseline_stockout,
            )
            for robot in snapshot.robots
        )
        selected = self._select_candidate(candidates)
        latest_safe_delay = None
        if baseline_stockout is not None and selected is not None:
            assert selected.replenishment_eta_minutes is not None
            latest_safe_delay = (
                baseline_stockout
                - selected.replenishment_eta_minutes
                - self._config.safety_buffer_minutes
            )

        missing_reasons = self._missing_reasons(
            snapshot=snapshot,
            demand_available=demand is not None,
            candidates=candidates,
        )
        completeness_score = self._data_completeness(snapshot, demand is not None)
        rationale = self._build_rationale(
            snapshot=snapshot,
            demand_basis=demand_basis,
            demand_rates=demand_rates,
            projection_horizon=projection_horizon,
            baseline_stockout=baseline_stockout,
            selected=selected,
            candidates=candidates,
            latest_safe_delay=latest_safe_delay,
            missing_reasons=missing_reasons,
        )

        trace = DecisionTrace.create(
            policy_id=self._config.policy_id,
            policy_version=self._config.policy_version,
            policy_config=self._config,
            site_id=snapshot.site_id,
            deployment_id=snapshot.deployment_id,
            observed_at=snapshot.observed_at,
            source_sequence=snapshot.source_sequence,
            source_schema_version=snapshot.source_schema_version,
            source_ref=snapshot.source_ref,
            snapshot_digest=stable_digest(snapshot),
            range_open=snapshot.range_open,
            range_open_provenance=snapshot.range_open_provenance,
            collection_allowed=snapshot.collection_allowed,
            collection_permission_provenance=(
                snapshot.collection_permission_provenance
            ),
            collection_block_reason=snapshot.collection_block_reason,
            washer_available=snapshot.washer_available,
            washer_availability_provenance=(
                snapshot.washer_availability_provenance
            ),
            clean_available=snapshot.clean_available,
            clean_available_provenance=snapshot.clean_available_provenance,
            clean_sensed=snapshot.clean_sensed,
            clean_sensed_provenance=snapshot.clean_sensed_provenance,
            inventory_basis=snapshot.decision_inventory_basis,
            inventory_balls=snapshot.decision_inventory_balls,
            inventory_provenance=snapshot.decision_inventory_provenance,
            current_demand_balls_per_minute=(
                snapshot.current_demand_balls_per_minute
            ),
            current_demand_provenance=snapshot.current_demand_provenance,
            forecast_demand_balls_per_minute=(
                snapshot.forecast_demand_balls_per_minute
            ),
            forecast_bucket_minutes=snapshot.forecast_bucket_minutes,
            forecast_demand_provenance=snapshot.forecast_demand_provenance,
            minutes_to_close=snapshot.minutes_to_close,
            minutes_to_close_provenance=snapshot.minutes_to_close_provenance,
            selected_demand_basis=demand_basis,
            demand_rates_used_balls_per_minute=demand_rates,
            projection_horizon_minutes=projection_horizon,
            committed_inbound_batches=snapshot.inbound_batches,
            projected_stockout_without_action_minutes=baseline_stockout,
            candidates=candidates,
            selected_robot_id=selected.robot_id if selected else None,
            latest_safe_dispatch_delay_minutes=latest_safe_delay,
            data_completeness_score=completeness_score,
            missing_data_reasons=missing_reasons,
            rationale=rationale,
        )

        if demand is None:
            if snapshot.range_open is False:
                return PolicyEvaluation(EvaluationVerdict.NO_ACTION, trace, None)
            recommendation = self._operator_recommendation(
                snapshot=snapshot,
                trace=trace,
                baseline_stockout=None,
                reason="demand is unavailable, so clean-ball risk cannot be assessed",
            )
            return PolicyEvaluation(EvaluationVerdict.RECOMMEND, trace, recommendation)

        if baseline_stockout is None:
            return PolicyEvaluation(EvaluationVerdict.NO_ACTION, trace, None)

        if selected is not None and latest_safe_delay is not None:
            if latest_safe_delay > self._config.alert_lead_minutes:
                return PolicyEvaluation(EvaluationVerdict.NO_ACTION, trace, None)
            recommendation = self._dispatch_recommendation(
                snapshot=snapshot,
                trace=trace,
                selected=selected,
                baseline_stockout=baseline_stockout,
                latest_safe_delay=latest_safe_delay,
            )
            return PolicyEvaluation(EvaluationVerdict.RECOMMEND, trace, recommendation)

        recommendation = self._operator_recommendation(
            snapshot=snapshot,
            trace=trace,
            baseline_stockout=baseline_stockout,
            reason="no eligible collector can arrive before and extend the first stockout",
        )
        return PolicyEvaluation(EvaluationVerdict.RECOMMEND, trace, recommendation)

    def _demand_projection(
        self, snapshot: OperationalSnapshot
    ) -> tuple[tuple[float, ...], float, float, str] | None:
        return derive_demand_projection(
            current_demand_balls_per_minute=(
                snapshot.current_demand_balls_per_minute
            ),
            forecast_demand_balls_per_minute=(
                snapshot.forecast_demand_balls_per_minute
            ),
            forecast_bucket_minutes=snapshot.forecast_bucket_minutes,
            minutes_to_close=snapshot.minutes_to_close,
            config=self._config,
        )

    def _evaluate_robot(
        self,
        *,
        robot: RobotOperationalState,
        snapshot: OperationalSnapshot,
        demand_rates: tuple[float, ...],
        demand_bucket: float | None,
        projection_horizon: float | None,
        baseline_stockout: float | None,
    ) -> RobotCandidateTrace:
        reasons = list(
            candidate_safety_exclusion_reasons(
                robot=robot,
                config=self._config,
                range_open=snapshot.range_open,
                collection_allowed=snapshot.collection_allowed,
                collection_block_reason=snapshot.collection_block_reason,
                washer_available=snapshot.washer_available,
                first_stockout_minutes=baseline_stockout,
            )
        )

        arrival_before = bool(
            baseline_stockout is not None
            and robot.replenishment_eta_minutes is not None
            and robot.replenishment_eta_minutes < baseline_stockout
        )
        eligible = not reasons
        stockout_with_action = None
        protected = False
        extension = 0.0
        if (
            eligible
            and baseline_stockout is not None
            and demand_rates
            and demand_bucket is not None
            and projection_horizon is not None
        ):
            assert robot.replenishment_eta_minutes is not None
            assert robot.expected_clean_ball_yield is not None
            candidate_batch = InboundBallBatch(
                source_id=f"counterfactual:{robot.robot_id}",
                eta_minutes=robot.replenishment_eta_minutes,
                balls=robot.expected_clean_ball_yield,
                provenance=(
                    f"counterfactual only; eta={robot.eta_provenance}; "
                    f"yield={robot.yield_provenance}"
                ),
            )
            stockout_with_action = project_stockout_minutes(
                initial_clean_balls=snapshot.decision_inventory_balls,
                demand_rates_balls_per_minute=demand_rates,
                demand_bucket_minutes=demand_bucket,
                inbound_batches=snapshot.inbound_batches + (candidate_batch,),
                horizon_minutes=projection_horizon,
            )
            protected = stockout_with_action is None
            extension = (
                projection_horizon - baseline_stockout
                if stockout_with_action is None
                else max(0.0, stockout_with_action - baseline_stockout)
            )
            if extension <= 0.0:
                reasons.append("no_positive_counterfactual_extension")
                eligible = False

        return RobotCandidateTrace(
            robot_id=robot.robot_id,
            status=robot.status,
            raw_activity=robot.raw_activity,
            raw_health=robot.raw_health,
            battery_fraction=robot.battery_fraction,
            capabilities=(
                None if robot.capabilities is None else tuple(sorted(robot.capabilities))
            ),
            payload_balls=robot.payload_balls,
            current_task_id=robot.current_task_id,
            fault_code=robot.fault_code,
            requires_washing=robot.requires_washing,
            eligible=eligible,
            exclusion_reasons=tuple(reasons),
            replenishment_eta_minutes=robot.replenishment_eta_minutes,
            expected_clean_ball_yield=robot.expected_clean_ball_yield,
            yield_provenance=robot.yield_provenance,
            eta_provenance=robot.eta_provenance,
            arrival_before_first_stockout=arrival_before,
            projected_stockout_with_action_minutes=stockout_with_action,
            protected_through_horizon=protected,
            projected_extension_minutes=extension,
        )

    @staticmethod
    def _select_candidate(
        candidates: tuple[RobotCandidateTrace, ...],
    ) -> RobotCandidateTrace | None:
        eligible = [
            candidate
            for candidate in candidates
            if candidate.eligible
            and candidate.arrival_before_first_stockout
            and candidate.projected_extension_minutes > 0.0
        ]
        if not eligible:
            return None
        return min(
            eligible,
            key=lambda item: (
                -item.projected_extension_minutes,
                item.replenishment_eta_minutes
                if item.replenishment_eta_minutes is not None
                else float("inf"),
                item.robot_id,
            ),
        )

    @staticmethod
    def _data_completeness(
        snapshot: OperationalSnapshot, demand_available: bool
    ) -> float:
        checks = [
            snapshot.clean_sensed is not None,
            demand_available,
            snapshot.range_open is not None,
            snapshot.collection_allowed is not None,
            snapshot.washer_available is not None,
            bool(snapshot.robots),
            bool(snapshot.robots)
            and all(
                robot.capabilities is not None
                and robot.replenishment_eta_minutes is not None
                and robot.expected_clean_ball_yield is not None
                for robot in snapshot.robots
            ),
        ]
        return sum(checks) / len(checks)

    @staticmethod
    def _missing_reasons(
        *,
        snapshot: OperationalSnapshot,
        demand_available: bool,
        candidates: tuple[RobotCandidateTrace, ...],
    ) -> tuple[str, ...]:
        reasons = set(snapshot.missing_data_reasons)
        if not demand_available:
            reasons.add("no usable current or forecast demand")
        if snapshot.clean_sensed is None:
            reasons.add("clean_sensed unavailable; clean_available used")
        if snapshot.range_open is None:
            reasons.add("range_open unavailable")
        if snapshot.collection_allowed is None:
            reasons.add("collection permission unavailable")
        if snapshot.washer_available is None:
            reasons.add("washer availability unavailable")
        for candidate in candidates:
            for reason in candidate.exclusion_reasons:
                if reason.startswith("missing_") or reason.endswith("_unknown"):
                    reasons.add(f"{candidate.robot_id}: {reason}")
        return tuple(sorted(reasons))

    def _build_rationale(
        self,
        *,
        snapshot: OperationalSnapshot,
        demand_basis: str,
        demand_rates: tuple[float, ...],
        projection_horizon: float | None,
        baseline_stockout: float | None,
        selected: RobotCandidateTrace | None,
        candidates: tuple[RobotCandidateTrace, ...],
        latest_safe_delay: float | None,
        missing_reasons: tuple[str, ...],
    ) -> tuple[str, ...]:
        lines = [
            (
                f"Used {snapshot.decision_inventory_basis}="
                f"{snapshot.decision_inventory_balls:g} balls; retained "
                f"clean_available={snapshot.clean_available} separately."
            )
        ]
        if demand_rates:
            lines.append(
                f"Used {demand_basis} over a {projection_horizon:g}-minute horizon."
            )
        else:
            lines.append("No usable demand series was available for projection.")
        if baseline_stockout is None:
            if demand_rates:
                lines.append("No clean-ball stockout is projected inside the risk horizon.")
        else:
            lines.append(
                "First projected stockout without new action is in "
                f"{baseline_stockout:.1f} minutes."
            )
        if selected is not None:
            lines.append(
                f"Selected {selected.robot_id} for the greatest counterfactual extension; "
                "ties break by earlier ETA, then robot ID."
            )
            if latest_safe_delay is not None:
                lines.append(
                    f"Latest safe dispatch delay is {latest_safe_delay:.1f} minutes "
                    f"after a {self._config.safety_buffer_minutes:.1f}-minute buffer."
                )
        elif baseline_stockout is not None:
            if any(candidate.exclusion_reasons for candidate in candidates):
                lines.append(
                    "Every collector candidate was excluded; reasons are recorded "
                    "per robot."
                )
            else:
                lines.append("No collector candidate was supplied.")
        if missing_reasons:
            lines.append(
                f"Conservative missing-data handling recorded {len(missing_reasons)} reason(s)."
            )
        return tuple(lines)

    def _dispatch_recommendation(
        self,
        *,
        snapshot: OperationalSnapshot,
        trace: DecisionTrace,
        selected: RobotCandidateTrace,
        baseline_stockout: float,
        latest_safe_delay: float,
    ) -> Recommendation:
        execute_before = snapshot.observed_at + timedelta(
            minutes=max(0.0, latest_safe_delay)
        )
        canonical_deadline = to_primitive(execute_before)
        assert isinstance(canonical_deadline, str)
        return Recommendation.create(
            site_id=snapshot.site_id,
            deployment_id=snapshot.deployment_id,
            issued_at=snapshot.observed_at,
            execute_before=execute_before,
            action=RecommendationAction.DISPATCH_COLLECTOR,
            target_robot_id=selected.robot_id,
            policy_id=self._config.policy_id,
            policy_version=self._config.policy_version,
            decision_trace_id=trace.trace_id,
            expected_stockout_without_action_minutes=baseline_stockout,
            expected_stockout_with_action_minutes=(
                selected.projected_stockout_with_action_minutes
            ),
            protected_through_horizon=selected.protected_through_horizon,
            projected_stockout_delay_minutes=selected.projected_extension_minutes,
            summary=(
                f"Recommend operator dispatch of {selected.robot_id} by "
                f"{canonical_deadline} to extend clean-ball availability."
            ),
        )

    def _operator_recommendation(
        self,
        *,
        snapshot: OperationalSnapshot,
        trace: DecisionTrace,
        baseline_stockout: float | None,
        reason: str,
    ) -> Recommendation:
        delay = (
            0.0
            if baseline_stockout is None
            else max(0.0, baseline_stockout - self._config.safety_buffer_minutes)
        )
        execute_before = snapshot.observed_at + timedelta(minutes=delay)
        return Recommendation.create(
            site_id=snapshot.site_id,
            deployment_id=snapshot.deployment_id,
            issued_at=snapshot.observed_at,
            execute_before=execute_before,
            action=RecommendationAction.OPERATOR_INTERVENTION,
            target_robot_id=None,
            policy_id=self._config.policy_id,
            policy_version=self._config.policy_version,
            decision_trace_id=trace.trace_id,
            expected_stockout_without_action_minutes=baseline_stockout,
            expected_stockout_with_action_minutes=None,
            protected_through_horizon=False,
            projected_stockout_delay_minutes=0.0,
            summary=f"Recommend operator intervention: {reason}.",
        )
