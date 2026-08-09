"""Unit tests for deterministic piecewise inventory projection."""

from __future__ import annotations

import pytest

from nxt_pilot_ops.contracts import InboundBallBatch
from nxt_pilot_ops.projection import project_stockout_minutes


def project(
    *,
    inventory: float = 1_000,
    rates: tuple[float, ...] = (100.0,),
    bucket: float = 60.0,
    batches: tuple[InboundBallBatch, ...] = (),
    horizon: float = 60.0,
) -> float | None:
    return project_stockout_minutes(
        initial_clean_balls=inventory,
        demand_rates_balls_per_minute=rates,
        demand_bucket_minutes=bucket,
        inbound_batches=batches,
        horizon_minutes=horizon,
    )


def test_stockout_without_arrivals() -> None:
    assert project() == pytest.approx(10.0)


def test_piecewise_demand_uses_the_rate_for_each_bucket() -> None:
    # 150 balls: 100 consumed in bucket one, then 50 / 20 per minute.
    assert project(
        inventory=150,
        rates=(10.0, 20.0),
        bucket=10.0,
        horizon=20.0,
    ) == pytest.approx(12.5)


def test_arrival_at_exact_exhaustion_is_just_in_time() -> None:
    batch = InboundBallBatch("washer", 10.0, 1_000, "fixture:committed")
    assert project(batches=(batch,), horizon=15.0) is None


def test_arrival_after_exhaustion_cannot_hide_first_stockout() -> None:
    batch = InboundBallBatch("washer", 10.001, 1_000, "fixture:committed")
    assert project(batches=(batch,)) == pytest.approx(10.0)


def test_large_magnitude_near_boundary_arrival_cannot_hide_earlier_stockout() -> None:
    just_in_time_only_if_rounded = InboundBallBatch(
        "too-late", 1.0, 1, "fixture:committed"
    )
    assert project(
        inventory=999_999_999_999.5,
        rates=(1_000_000_000_000.0,),
        bucket=1.0,
        batches=(just_in_time_only_if_rounded,),
        horizon=1.0,
    ) == pytest.approx(0.9999999999995)


def test_arrival_at_zero_is_applied_before_inclusive_horizon_exhaustion() -> None:
    batch = InboundBallBatch("opening-buffer", 0.0, 100, "fixture:committed")
    assert project(inventory=0, batches=(batch,), horizon=1.0) == pytest.approx(1.0)


def test_zero_inventory_stockout_is_immediate_only_when_demand_is_positive() -> None:
    assert project(inventory=0, rates=(100.0,), horizon=1.0) == pytest.approx(0.0)
    assert project(inventory=0, rates=(0.0,), horizon=60.0) is None


def test_arrivals_after_horizon_are_ignored() -> None:
    late = InboundBallBatch("late", 61.0, 50_000, "fixture:committed")
    assert project(batches=(late,), horizon=60.0) == pytest.approx(10.0)


def test_arrival_fractionally_after_horizon_cannot_extend_the_projection() -> None:
    late = InboundBallBatch("late", 1.0000000005, 1, "fixture:committed")
    result = project(
        inventory=100.00000002,
        rates=(100.0,),
        bucket=1.0,
        batches=(late,),
        horizon=1.0,
    )
    assert result is None or result <= 1.0


def test_sub_epsilon_bucket_boundaries_are_not_skipped() -> None:
    assert project(
        inventory=1e-12,
        rates=(1.0, 1.0),
        bucket=1e-12,
        horizon=2e-12,
    ) == pytest.approx(1e-12, rel=1e-12, abs=0.0)


def test_projection_horizon_must_fit_supplied_schedule() -> None:
    with pytest.raises(ValueError, match="exceeds the supplied demand schedule"):
        project(rates=(10.0, 20.0), bucket=5.0, horizon=10.01)


@pytest.mark.parametrize(
    "overrides",
    [
        {"inventory": True},
        {"rates": (False,)},
        {"bucket": True},
        {"horizon": True},
    ],
)
def test_projection_rejects_booleans_as_numbers(overrides) -> None:
    with pytest.raises(TypeError, match="must be a number"):
        project(**overrides)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_projection_rejects_nonfinite_values(bad) -> None:
    with pytest.raises(ValueError, match="finite"):
        project(inventory=bad)
    with pytest.raises(ValueError, match="finite"):
        project(rates=(bad,))
    with pytest.raises(ValueError, match="finite"):
        project(bucket=bad)
    with pytest.raises(ValueError, match="finite"):
        project(horizon=bad)


def test_projection_rejects_empty_or_non_tuple_schedules() -> None:
    with pytest.raises(ValueError, match="non-empty tuple"):
        project(rates=())
    with pytest.raises(ValueError, match="non-empty tuple"):
        project(rates=[100.0])
    with pytest.raises(TypeError, match="inbound_batches must be a tuple"):
        project(batches=[])
