"""Deterministic clean-ball inventory projection."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict

from .contracts import InboundBallBatch

def _number(name: str, value: object, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if result < 0 or (positive and result <= 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be {qualifier}")
    return result


def project_stockout_minutes(
    *,
    initial_clean_balls: float,
    demand_rates_balls_per_minute: tuple[float, ...],
    demand_bucket_minutes: float,
    inbound_batches: tuple[InboundBallBatch, ...],
    horizon_minutes: float,
) -> float | None:
    """Return the first inventory exhaustion within ``horizon_minutes``.

    Demand is piecewise constant.  Committed arrivals at the exact exhaustion
    instant are treated as just-in-time.  Collector eligibility applies the
    stricter ``ETA < first stockout`` rule in :mod:`nxt_pilot_ops.guardian`.
    """

    inventory = _number("initial_clean_balls", initial_clean_balls)
    bucket = _number("demand_bucket_minutes", demand_bucket_minutes, positive=True)
    horizon = _number("horizon_minutes", horizon_minutes, positive=True)
    if type(demand_rates_balls_per_minute) is not tuple or not demand_rates_balls_per_minute:
        raise ValueError("demand rates must be a non-empty tuple")
    rates = tuple(
        _number(f"demand_rates[{index}]", rate)
        for index, rate in enumerate(demand_rates_balls_per_minute)
    )
    schedule_horizon = len(rates) * bucket
    if horizon > schedule_horizon:
        if not math.isclose(
            horizon, schedule_horizon, rel_tol=1e-12, abs_tol=0.0
        ):
            raise ValueError("projection horizon exceeds the supplied demand schedule")
        horizon = schedule_horizon
    if type(inbound_batches) is not tuple:
        raise TypeError("inbound_batches must be a tuple")

    arrivals: dict[float, list[InboundBallBatch]] = defaultdict(list)
    for batch in sorted(inbound_batches, key=lambda item: (item.eta_minutes, item.source_id)):
        if batch.eta_minutes <= horizon:
            arrivals[batch.eta_minutes].append(batch)

    boundaries = tuple(
        boundary
        for index in range(1, len(rates))
        if (boundary := index * bucket) < horizon
    )
    event_times = {0.0, horizon, *arrivals.keys(), *boundaries}

    previous = 0.0
    for event_time in sorted(event_times):
        if event_time < previous:
            raise ValueError("projection events moved backward in time")
        interval = max(0.0, event_time - previous)
        bucket_index = min(bisect_right(boundaries, previous), len(rates) - 1)
        rate = rates[bucket_index]
        consumption = rate * interval
        exhausted_exactly = consumption > 0 and inventory == consumption
        if inventory < consumption:
            if rate <= 0:
                raise AssertionError("positive consumption requires positive demand")
            return previous + inventory / rate

        inventory = max(0.0, inventory - consumption)
        same_time = arrivals.get(event_time, ())
        inventory += sum(batch.balls for batch in same_time)
        if exhausted_exactly and not same_time:
            return event_time
        previous = event_time

    return None
