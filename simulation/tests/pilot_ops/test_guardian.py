"""Decision-table tests for the Ball Availability Guardian."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nxt_pilot_ops.contracts import (
    BallAvailabilityPolicyConfig,
    EvaluationVerdict,
    InboundBallBatch,
    RecommendationAction,
    RobotStatus,
)
from nxt_pilot_ops.guardian import BallAvailabilityGuardian


def candidate(result, robot_id: str):
    return next(item for item in result.trace.candidates if item.robot_id == robot_id)


def assert_operator_intervention(result) -> None:
    assert result.verdict is EvaluationVerdict.RECOMMEND
    assert result.recommendation is not None
    assert result.recommendation.action is RecommendationAction.OPERATOR_INTERVENTION
    assert result.recommendation.target_robot_id is None
    assert result.trace.selected_robot_id is None


@pytest.mark.parametrize("invalid", [False, 0, {}, []])
def test_guardian_rejects_falsey_non_config_values(invalid) -> None:
    with pytest.raises(TypeError, match="BallAvailabilityPolicyConfig"):
        BallAvailabilityGuardian(invalid)


def test_guardian_projects_every_positive_configured_horizon(snapshot_factory) -> None:
    guardian = BallAvailabilityGuardian(
        BallAvailabilityPolicyConfig(risk_horizon_minutes=1e-12)
    )
    result = guardian.evaluate(
        snapshot_factory(
            clean_available=0,
            clean_sensed=0.0,
            current_demand_balls_per_minute=1.0,
            forecast_demand_balls_per_minute=None,
            forecast_bucket_minutes=None,
            minutes_to_close=1e-12,
            robots=(),
        )
    )

    assert result.trace.selected_demand_basis == "current_demand"
    assert result.trace.projection_horizon_minutes == pytest.approx(1e-12)
    assert result.trace.projected_stockout_without_action_minutes == 0.0


def test_safe_inventory_produces_no_action(snapshot_factory, guardian) -> None:
    result = guardian.evaluate(
        snapshot_factory(clean_available=20_000, clean_sensed=20_000.0)
    )

    assert result.verdict is EvaluationVerdict.NO_ACTION
    assert result.recommendation is None
    assert result.trace.projected_stockout_without_action_minutes is None


def test_nonurgent_dispatch_window_produces_no_action(
    snapshot_factory, guardian
) -> None:
    # Stockout at 50 minutes, ETA 2, safety buffer 12 -> 36 minutes of delay,
    # which is outside the default 15-minute alert lead.
    result = guardian.evaluate(
        snapshot_factory(clean_available=5_000, clean_sensed=5_000.0)
    )

    assert result.trace.projected_stockout_without_action_minutes == pytest.approx(50.0)
    assert result.trace.selected_robot_id == "R-1"
    assert result.trace.latest_safe_dispatch_delay_minutes == pytest.approx(36.0)
    assert result.verdict is EvaluationVerdict.NO_ACTION
    assert result.recommendation is None


def test_sensed_inventory_drives_policy_without_merging_accounting_inventory(
    snapshot_factory, guardian
) -> None:
    result = guardian.evaluate(
        snapshot_factory(
            clean_available=20_000,
            clean_available_provenance="ledger:clean-available",
            clean_sensed=1_000.0,
            clean_sensed_provenance="sensor:dispenser",
        )
    )

    assert result.trace.clean_available == 20_000
    assert result.trace.clean_available_provenance == "ledger:clean-available"
    assert result.trace.clean_sensed == 1_000.0
    assert result.trace.clean_sensed_provenance == "sensor:dispenser"
    assert result.trace.inventory_basis == "clean_sensed"
    assert result.trace.inventory_balls == 1_000.0
    assert result.trace.inventory_provenance == "sensor:dispenser"
    assert result.recommendation is not None
    assert result.recommendation.action is RecommendationAction.DISPATCH_COLLECTOR


def test_accounting_inventory_is_explicit_fallback_when_sensor_is_absent(
    snapshot_factory, guardian
) -> None:
    result = guardian.evaluate(
        snapshot_factory(
            clean_available=1_000,
            clean_available_provenance="ledger:clean-available",
            clean_sensed=None,
            clean_sensed_provenance="sensor:unavailable",
        )
    )

    assert result.trace.clean_available == 1_000
    assert result.trace.clean_sensed is None
    assert result.trace.inventory_basis == "clean_available"
    assert result.trace.inventory_balls == 1_000.0
    assert result.trace.inventory_provenance == "ledger:clean-available"
    assert "clean_sensed unavailable; clean_available used" in (
        result.trace.missing_data_reasons
    )


def test_low_battery_is_an_independent_exclusion(
    snapshot_factory, robot_factory, guardian
) -> None:
    robot = robot_factory(robot_id="R-low", battery_fraction=0.29)
    result = guardian.evaluate(snapshot_factory(robots=(robot,)))

    assert_operator_intervention(result)
    trace = candidate(result, "R-low")
    assert trace.eligible is False
    assert trace.exclusion_reasons == ("battery_below_minimum",)


def test_fault_code_excludes_even_an_available_robot(
    snapshot_factory, robot_factory, guardian
) -> None:
    robot = robot_factory(robot_id="R-fault", fault_code="DRIVE_FAULT")
    result = guardian.evaluate(snapshot_factory(robots=(robot,)))

    assert_operator_intervention(result)
    trace = candidate(result, "R-fault")
    assert trace.status is RobotStatus.AVAILABLE
    assert trace.exclusion_reasons == ("fault:DRIVE_FAULT",)


@pytest.mark.parametrize(
    "status",
    [RobotStatus.CHARGING, RobotStatus.OFFLINE, RobotStatus.ESTOPPED],
)
def test_charging_offline_and_estopped_robots_are_never_dispatchable(
    snapshot_factory, robot_factory, guardian, status
) -> None:
    robot = robot_factory(robot_id=f"R-{status.value}", status=status)
    result = guardian.evaluate(snapshot_factory(robots=(robot,)))

    assert_operator_intervention(result)
    trace = candidate(result, robot.robot_id)
    assert trace.eligible is False
    assert trace.exclusion_reasons == (f"status:{status.value}",)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"replenishment_eta_minutes": None}, "missing_replenishment_eta"),
        (
            {"expected_clean_ball_yield": None},
            "missing_expected_clean_ball_yield",
        ),
        ({"capabilities": None}, "missing_capabilities"),
        ({"requires_washing": None}, "washing_requirement_unknown"),
    ],
)
def test_missing_robot_facts_are_conservative_independent_exclusions(
    snapshot_factory, robot_factory, guardian, overrides, reason
) -> None:
    robot = robot_factory(robot_id="R-missing", **overrides)
    result = guardian.evaluate(snapshot_factory(robots=(robot,)))

    assert_operator_intervention(result)
    trace = candidate(result, "R-missing")
    assert trace.eligible is False
    assert reason in trace.exclusion_reasons


@pytest.mark.parametrize(
    ("allowed", "block_reason", "exclusion"),
    [
        (False, "maintenance_vehicle_on_route", "collection_blocked:maintenance_vehicle_on_route"),
        (None, None, "collection_permission_unknown"),
    ],
)
def test_blocked_or_unknown_collection_permission_never_dispatches(
    snapshot_factory, guardian, allowed, block_reason, exclusion
) -> None:
    result = guardian.evaluate(
        snapshot_factory(
            collection_allowed=allowed,
            collection_block_reason=block_reason,
        )
    )

    assert_operator_intervention(result)
    assert exclusion in result.trace.candidates[0].exclusion_reasons


@pytest.mark.parametrize(
    ("washer_available", "exclusion"),
    [
        (None, "washer_availability_unknown"),
        (False, "washer_unavailable"),
    ],
)
def test_washing_required_needs_known_available_washer(
    snapshot_factory, robot_factory, guardian, washer_available, exclusion
) -> None:
    robot = robot_factory(robot_id="R-wash", requires_washing=True)
    result = guardian.evaluate(
        snapshot_factory(robots=(robot,), washer_available=washer_available)
    )

    assert_operator_intervention(result)
    assert exclusion in candidate(result, "R-wash").exclusion_reasons


def test_washer_availability_does_not_block_a_clean_return(
    snapshot_factory, robot_factory, guardian
) -> None:
    robot = robot_factory(robot_id="R-clean", requires_washing=False)
    result = guardian.evaluate(
        snapshot_factory(robots=(robot,), washer_available=None)
    )

    assert result.recommendation is not None
    assert result.recommendation.action is RecommendationAction.DISPATCH_COLLECTOR
    assert result.recommendation.target_robot_id == "R-clean"
    assert candidate(result, "R-clean").eligible is True


@pytest.mark.parametrize("eta", [10.0, 10.001, 30.0])
def test_robot_at_or_after_first_stockout_never_dispatches(
    snapshot_factory, robot_factory, guardian, eta
) -> None:
    robot = robot_factory(robot_id="R-late", replenishment_eta_minutes=eta)
    result = guardian.evaluate(snapshot_factory(robots=(robot,)))

    assert result.trace.projected_stockout_without_action_minutes == pytest.approx(10.0)
    assert_operator_intervention(result)
    trace = candidate(result, "R-late")
    assert trace.arrival_before_first_stockout is False
    assert trace.projected_extension_minutes == 0.0
    assert "arrival_not_before_first_stockout" in trace.exclusion_reasons


def test_greatest_counterfactual_extension_wins_over_fastest_eta(
    snapshot_factory, robot_factory, guardian
) -> None:
    fast_small = robot_factory(
        robot_id="R-fast-small",
        replenishment_eta_minutes=1.0,
        expected_clean_ball_yield=100,
    )
    slower_large = robot_factory(
        robot_id="R-slower-large",
        replenishment_eta_minutes=3.0,
        expected_clean_ball_yield=2_000,
    )
    result = guardian.evaluate(
        snapshot_factory(robots=(fast_small, slower_large))
    )

    assert candidate(result, "R-fast-small").projected_extension_minutes == pytest.approx(1.0)
    assert candidate(result, "R-slower-large").projected_extension_minutes == pytest.approx(20.0)
    assert result.trace.selected_robot_id == "R-slower-large"
    assert result.recommendation is not None
    assert result.recommendation.target_robot_id == "R-slower-large"


def test_counterfactual_ties_break_by_earlier_eta_then_robot_id(
    snapshot_factory, robot_factory, guardian
) -> None:
    later = robot_factory(robot_id="R-a-later", replenishment_eta_minutes=3.0)
    earlier_z = robot_factory(robot_id="R-z-early", replenishment_eta_minutes=2.0)
    earlier_a = robot_factory(robot_id="R-a-early", replenishment_eta_minutes=2.0)

    result = guardian.evaluate(
        snapshot_factory(robots=(later, earlier_z, earlier_a))
    )

    extensions = {
        item.projected_extension_minutes for item in result.trace.candidates
    }
    assert extensions == {10.0}
    assert result.trace.selected_robot_id == "R-a-early"
    assert result.recommendation is not None
    assert result.recommendation.target_robot_id == "R-a-early"


def test_normalized_robot_and_inbound_order_produce_identical_ids(
    snapshot_factory, robot_factory, guardian
) -> None:
    robot_a = robot_factory(robot_id="R-a", expected_clean_ball_yield=1_500)
    robot_z = robot_factory(robot_id="R-z", expected_clean_ball_yield=500)
    batch_a = InboundBallBatch("batch-a", 30.0, 10, "fixture:committed")
    batch_z = InboundBallBatch("batch-z", 30.0, 10, "fixture:committed")

    first_snapshot = snapshot_factory(
        robots=(robot_z, robot_a), inbound_batches=(batch_z, batch_a)
    )
    second_snapshot = snapshot_factory(
        robots=(robot_a, robot_z), inbound_batches=(batch_a, batch_z)
    )
    first = guardian.evaluate(first_snapshot)
    second = guardian.evaluate(second_snapshot)

    assert first_snapshot == second_snapshot
    assert first.trace.trace_id == second.trace.trace_id
    assert first.trace.snapshot_digest == second.trace.snapshot_digest
    assert first.recommendation is not None and second.recommendation is not None
    assert first.recommendation.recommendation_id == (
        second.recommendation.recommendation_id
    )


def test_timezone_equivalent_inputs_produce_identical_recommendation_ids(
    snapshot_factory, guardian
) -> None:
    utc = datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)
    china = datetime(
        2026,
        8,
        8,
        22,
        0,
        tzinfo=timezone(timedelta(hours=8)),
    )

    first = guardian.evaluate(snapshot_factory(observed_at=utc))
    second = guardian.evaluate(snapshot_factory(observed_at=china))

    assert first.trace.trace_id == second.trace.trace_id
    assert first.recommendation is not None and second.recommendation is not None
    assert first.recommendation.recommendation_id == (
        second.recommendation.recommendation_id
    )


def test_policy_config_is_part_of_trace_and_recommendation_identity(
    snapshot_factory,
) -> None:
    snapshot = snapshot_factory()
    default = BallAvailabilityGuardian().evaluate(snapshot)
    changed = BallAvailabilityGuardian(
        BallAvailabilityPolicyConfig(safety_buffer_minutes=11.0)
    ).evaluate(snapshot)

    assert default.trace.trace_id != changed.trace.trace_id
    assert default.recommendation is not None and changed.recommendation is not None
    assert default.recommendation.recommendation_id != (
        changed.recommendation.recommendation_id
    )


@pytest.mark.parametrize(
    (
        "risk_horizon",
        "minutes_to_close",
        "forecast",
        "expected_horizon",
        "expected_rates",
    ),
    [
        (25.0, 18.0, (5.0, 6.0, 7.0, 8.0), 18.0, (5.0, 6.0)),
        (15.0, 60.0, (5.0, 6.0, 7.0, 8.0), 15.0, (5.0, 6.0)),
        (90.0, None, (5.0, 6.0, 7.0, 8.0), 40.0, (5.0, 6.0, 7.0, 8.0)),
    ],
)
def test_forecast_projection_horizon_is_capped_by_every_available_limit(
    snapshot_factory,
    risk_horizon,
    minutes_to_close,
    forecast,
    expected_horizon,
    expected_rates,
) -> None:
    guardian = BallAvailabilityGuardian(
        BallAvailabilityPolicyConfig(risk_horizon_minutes=risk_horizon)
    )
    result = guardian.evaluate(
        snapshot_factory(
            current_demand_balls_per_minute=None,
            forecast_demand_balls_per_minute=forecast,
            forecast_bucket_minutes=10.0,
            minutes_to_close=minutes_to_close,
            robots=(),
        )
    )

    assert result.trace.selected_demand_basis == "forecast_demand"
    assert result.trace.projection_horizon_minutes == pytest.approx(expected_horizon)
    assert result.trace.demand_rates_used_balls_per_minute == expected_rates


def test_no_demand_escalates_when_open_but_is_no_action_when_closed(
    snapshot_factory, guardian
) -> None:
    open_result = guardian.evaluate(
        snapshot_factory(
            current_demand_balls_per_minute=None,
            forecast_demand_balls_per_minute=None,
        )
    )
    assert_operator_intervention(open_result)

    closed_result = guardian.evaluate(
        snapshot_factory(
            current_demand_balls_per_minute=None,
            forecast_demand_balls_per_minute=None,
            range_open=False,
        )
    )
    assert closed_result.verdict is EvaluationVerdict.NO_ACTION
    assert closed_result.recommendation is None
