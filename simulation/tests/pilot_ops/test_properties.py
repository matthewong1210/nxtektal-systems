"""Seeded and exhaustive properties for Shadow Ops policy safety."""

from __future__ import annotations

import itertools
import random

from nxt_pilot_ops.contracts import (
    InboundBallBatch,
    RecommendationAction,
    RobotStatus,
)
from nxt_pilot_ops.projection import project_stockout_minutes


def test_extra_committed_inbound_supply_never_moves_stockout_earlier() -> None:
    rng = random.Random(20260809)

    for case in range(500):
        bucket = rng.uniform(1.0, 30.0)
        bucket_count = rng.randint(1, 8)
        horizon = bucket * bucket_count
        rates = tuple(rng.uniform(0.0, 200.0) for _ in range(bucket_count))
        inventory = rng.uniform(0.0, 30_000.0)
        batches = tuple(
            InboundBallBatch(
                source_id=f"case-{case}-batch-{index}",
                eta_minutes=rng.uniform(0.0, horizon * 1.2),
                balls=rng.randint(1, 8_000),
                provenance="seeded-property:committed-inbound",
            )
            for index in range(rng.randint(0, 6))
        )
        baseline = project_stockout_minutes(
            initial_clean_balls=inventory,
            demand_rates_balls_per_minute=rates,
            demand_bucket_minutes=bucket,
            inbound_batches=batches,
            horizon_minutes=horizon,
        )
        extra = InboundBallBatch(
            source_id=f"case-{case}-extra",
            eta_minutes=rng.uniform(0.0, horizon),
            balls=rng.randint(1, 8_000),
            provenance="seeded-property:extra-inbound",
        )
        with_extra = project_stockout_minutes(
            initial_clean_balls=inventory,
            demand_rates_balls_per_minute=rates,
            demand_bucket_minutes=bucket,
            inbound_batches=batches + (extra,),
            horizon_minutes=horizon,
        )

        if baseline is None:
            assert with_extra is None, f"case {case}: supply introduced a stockout"
        elif with_extra is not None:
            assert with_extra + 1e-8 >= baseline, (
                f"case {case}: {with_extra=} moved earlier than {baseline=}"
            )


def test_dispatch_eligibility_is_the_conjunction_of_independent_safety_facts(
    snapshot_factory, robot_factory, guardian
) -> None:
    """Every one of nine independent facts can veto an otherwise valid dispatch."""

    for case, flags in enumerate(itertools.product((False, True), repeat=9)):
        (
            status_ok,
            battery_ok,
            fault_free,
            capability_ok,
            eta_ok,
            yield_ok,
            range_open,
            collection_ok,
            washer_ok,
        ) = flags
        robot = robot_factory(
            robot_id=f"R-{case:03d}",
            status=(RobotStatus.AVAILABLE if status_ok else RobotStatus.CHARGING),
            battery_fraction=(0.90 if battery_ok else 0.10),
            fault_code=(None if fault_free else "PROPERTY_FAULT"),
            capabilities=(
                frozenset({"collect"})
                if capability_ok
                else frozenset({"transport"})
            ),
            replenishment_eta_minutes=(2.0 if eta_ok else None),
            expected_clean_ball_yield=(1_000 if yield_ok else None),
            requires_washing=True,
        )
        snapshot = snapshot_factory(
            robots=(robot,),
            range_open=range_open,
            collection_allowed=collection_ok,
            collection_block_reason=(None if collection_ok else "property:block"),
            washer_available=washer_ok,
        )
        result = guardian.evaluate(snapshot)
        trace = result.trace.candidates[0]
        expected_dispatch = all(flags)

        assert trace.eligible is expected_dispatch, f"case {case}: {flags=}"
        if expected_dispatch:
            assert result.recommendation is not None
            assert (
                result.recommendation.action
                is RecommendationAction.DISPATCH_COLLECTOR
            )
            assert result.recommendation.target_robot_id == robot.robot_id
            assert trace.exclusion_reasons == ()
            assert trace.arrival_before_first_stockout is True
            assert trace.projected_extension_minutes > 0.0
        else:
            assert result.recommendation is not None
            assert (
                result.recommendation.action
                is RecommendationAction.OPERATOR_INTERVENTION
            )
            assert result.recommendation.target_robot_id is None


def test_all_robot_permutations_have_identical_policy_identity_and_selection(
    snapshot_factory, robot_factory, guardian
) -> None:
    robots = (
        robot_factory(robot_id="R-c", expected_clean_ball_yield=500),
        robot_factory(robot_id="R-a", expected_clean_ball_yield=1_500),
        robot_factory(robot_id="R-b", expected_clean_ball_yield=1_000),
    )
    identities = set()

    for order in itertools.permutations(robots):
        result = guardian.evaluate(snapshot_factory(robots=order))
        assert result.recommendation is not None
        identities.add(
            (
                result.trace.trace_id,
                result.recommendation.recommendation_id,
                result.trace.selected_robot_id,
                result.recommendation.target_robot_id,
            )
        )

    assert len(identities) == 1
    only = identities.pop()
    assert only[2:] == ("R-a", "R-a")
