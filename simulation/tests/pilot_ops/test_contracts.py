"""Validation and normalization tests for the pure Shadow Ops contracts."""

from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

from nxt_pilot_ops.contracts import (
    BallAvailabilityPolicyConfig,
    EvaluationVerdict,
    InboundBallBatch,
    PolicyEvaluation,
    RobotStatus,
)


def test_contracts_are_frozen(snapshot_factory, robot_factory) -> None:
    snapshot = snapshot_factory()
    robot = robot_factory()

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.clean_available = 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        robot.battery_fraction = 0.0


def test_snapshot_normalizes_order_and_missing_reasons(
    snapshot_factory, robot_factory
) -> None:
    robots = (
        robot_factory(robot_id="R-zulu"),
        robot_factory(robot_id="R-alpha"),
    )
    batches = (
        InboundBallBatch("washer-z", 5.0, 100, "fixture:washer"),
        InboundBallBatch("washer-b", 2.0, 100, "fixture:washer"),
        InboundBallBatch("washer-a", 2.0, 100, "fixture:washer"),
    )

    snapshot = snapshot_factory(
        robots=robots,
        inbound_batches=batches,
        missing_data_reasons=("z reason", "a reason", "z reason"),
    )

    assert [robot.robot_id for robot in snapshot.robots] == ["R-alpha", "R-zulu"]
    assert [batch.source_id for batch in snapshot.inbound_batches] == [
        "washer-a",
        "washer-b",
        "washer-z",
    ]
    assert snapshot.missing_data_reasons == ("a reason", "z reason")


def test_sensed_inventory_and_accounting_inventory_remain_separate(
    snapshot_factory,
) -> None:
    sensed = snapshot_factory(clean_available=900, clean_sensed=123.5)
    assert sensed.clean_available == 900
    assert sensed.clean_sensed == 123.5
    assert sensed.decision_inventory_balls == 123.5
    assert sensed.decision_inventory_basis == "clean_sensed"
    assert sensed.decision_inventory_provenance == "fixture:dispenser-sensor"

    fallback = snapshot_factory(clean_available=900, clean_sensed=None)
    assert fallback.clean_available == 900
    assert fallback.clean_sensed is None
    assert fallback.decision_inventory_balls == 900.0
    assert fallback.decision_inventory_basis == "clean_available"
    assert fallback.decision_inventory_provenance == "fixture:accounting-ledger"


@pytest.mark.parametrize(
    "field",
    ["range_open", "collection_allowed", "washer_available"],
)
def test_snapshot_rejects_truthy_values_for_operational_booleans(
    snapshot_factory, field
) -> None:
    with pytest.raises(TypeError, match=field):
        snapshot_factory(**{field: 1})


def test_robot_rejects_truthy_value_for_washing_boolean(robot_factory) -> None:
    with pytest.raises(TypeError, match="requires_washing"):
        robot_factory(requires_washing=1)


@pytest.mark.parametrize("bad", [True, False])
def test_boolean_values_are_not_accepted_as_numbers(
    snapshot_factory, robot_factory, bad
) -> None:
    with pytest.raises(TypeError, match="clean_available"):
        snapshot_factory(clean_available=bad)
    with pytest.raises(TypeError, match="current_demand"):
        snapshot_factory(current_demand_balls_per_minute=bad)
    with pytest.raises(TypeError, match="battery_fraction"):
        robot_factory(battery_fraction=bad)
    with pytest.raises(TypeError, match="replenishment_eta"):
        robot_factory(replenishment_eta_minutes=bad)
    with pytest.raises(TypeError, match="eta_minutes"):
        InboundBallBatch("source", bad, 1, "fixture")
    with pytest.raises(TypeError, match="balls"):
        InboundBallBatch("source", 1.0, bad, "fixture")
    with pytest.raises(TypeError, match="minimum_battery_fraction"):
        BallAvailabilityPolicyConfig(minimum_battery_fraction=bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_values_are_rejected_everywhere_they_enter_core(
    snapshot_factory, robot_factory, bad
) -> None:
    with pytest.raises(ValueError, match="finite"):
        snapshot_factory(clean_sensed=bad)
    with pytest.raises(ValueError, match="finite"):
        snapshot_factory(current_demand_balls_per_minute=bad)
    with pytest.raises(ValueError, match="finite"):
        snapshot_factory(
            current_demand_balls_per_minute=None,
            forecast_demand_balls_per_minute=(bad,),
            forecast_bucket_minutes=10.0,
        )
    with pytest.raises(ValueError, match="finite"):
        snapshot_factory(minutes_to_close=bad)
    with pytest.raises(ValueError, match="finite"):
        robot_factory(battery_fraction=bad)
    with pytest.raises(ValueError, match="finite"):
        robot_factory(replenishment_eta_minutes=bad)
    with pytest.raises(ValueError, match="finite"):
        InboundBallBatch("source", bad, 1, "fixture")
    with pytest.raises(ValueError, match="finite"):
        BallAvailabilityPolicyConfig(risk_horizon_minutes=bad)


def test_snapshot_rejects_naive_observation_time(snapshot_factory) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        snapshot_factory(observed_at=datetime(2026, 8, 8, 14, 0))


def test_blocked_collection_requires_an_explanation(snapshot_factory) -> None:
    with pytest.raises(ValueError, match="collection_block_reason"):
        snapshot_factory(collection_allowed=False, collection_block_reason=None)


@pytest.mark.parametrize(
    ("forecast", "bucket", "error"),
    [
        ((), 10.0, "non-empty tuple"),
        ([10.0], 10.0, "non-empty tuple"),
        ((10.0,), None, "forecast_bucket_minutes"),
        (None, 10.0, "requires a forecast"),
    ],
)
def test_forecast_and_bucket_must_be_supplied_together(
    snapshot_factory, forecast, bucket, error
) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        snapshot_factory(
            forecast_demand_balls_per_minute=forecast,
            forecast_bucket_minutes=bucket,
        )


def test_robot_ids_must_be_unique_within_snapshot(
    snapshot_factory, robot_factory
) -> None:
    duplicate = robot_factory(robot_id="R-duplicate")
    with pytest.raises(ValueError, match="robot IDs must be unique"):
        snapshot_factory(robots=(duplicate, duplicate))


def test_inbound_batch_ids_must_be_unique_within_snapshot(snapshot_factory) -> None:
    duplicate = InboundBallBatch("same-source", 5.0, 100, "fixture:committed")
    with pytest.raises(ValueError, match="inbound batch source IDs must be unique"):
        snapshot_factory(inbound_batches=(duplicate, duplicate))


def test_committed_inbound_cannot_impersonate_a_counterfactual(
    snapshot_factory,
) -> None:
    reserved = InboundBallBatch(
        "counterfactual:R-1", 2.0, 1_000, "fixture:committed"
    )
    with pytest.raises(ValueError, match="reserved counterfactual"):
        snapshot_factory(inbound_batches=(reserved,))


def test_robot_requires_enum_status_and_strict_container_types(
    robot_factory, snapshot_factory
) -> None:
    with pytest.raises(TypeError, match="RobotStatus"):
        robot_factory(status="available")
    with pytest.raises(TypeError, match="frozenset"):
        robot_factory(capabilities={"collect"})
    with pytest.raises(TypeError, match="robots must be a tuple"):
        snapshot_factory(robots=[])


def test_policy_config_rejects_invalid_thresholds() -> None:
    with pytest.raises(ValueError, match="minimum_battery_fraction"):
        BallAvailabilityPolicyConfig(minimum_battery_fraction=1.01)
    with pytest.raises(ValueError, match="risk_horizon_minutes"):
        BallAvailabilityPolicyConfig(risk_horizon_minutes=0.0)
    with pytest.raises(ValueError, match="safety_buffer_minutes"):
        BallAvailabilityPolicyConfig(safety_buffer_minutes=-0.01)


def test_missing_operational_facts_are_explicit_not_defaulted(robot_factory) -> None:
    robot = robot_factory(
        expected_clean_ball_yield=None,
        replenishment_eta_minutes=None,
        capabilities=None,
        requires_washing=None,
    )
    assert robot.expected_clean_ball_yield is None
    assert robot.replenishment_eta_minutes is None
    assert robot.capabilities is None
    assert robot.requires_washing is None
    assert robot.status is RobotStatus.AVAILABLE


def test_content_ids_use_normalized_numeric_values(
    snapshot_factory, guardian
) -> None:
    trace = guardian.evaluate(snapshot_factory()).trace
    values = {
        item.name: getattr(trace, item.name)
        for item in dataclasses.fields(trace)
        if item.name != "trace_id"
    }
    assert trace.data_completeness_score == 1.0
    values["data_completeness_score"] = 1
    assert type(trace).create(**values).trace_id == trace.trace_id


def test_policy_evaluation_cannot_contradict_the_guardian_result(
    snapshot_factory, guardian
) -> None:
    result = guardian.evaluate(snapshot_factory())
    assert result.verdict is EvaluationVerdict.RECOMMEND

    with pytest.raises(ValueError, match="verdict does not match"):
        PolicyEvaluation(EvaluationVerdict.NO_ACTION, result.trace, None)
