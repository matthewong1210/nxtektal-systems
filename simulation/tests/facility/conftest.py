"""Shared fixtures/helpers for the nxt_facility (Site OS state contract) tests."""

from __future__ import annotations

import copy

import pytest

from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario

# The five named RNG streams spawned in RangeSimulation.__init__. Facility
# snapshots must never advance any of them.
RNG_STREAMS = (
    "_rng_demand",
    "_rng_skills",
    "_rng_failures",
    "_rng_sensors",
    "_rng_forecast",
)


@pytest.fixture
def weekday():
    return make_scenario("normal_weekday")


@pytest.fixture
def sim(weekday) -> RangeSimulation:
    return RangeSimulation(weekday, seed=123)


def rng_states(sim: RangeSimulation) -> dict:
    """Deep-copied bit-generator states of all five RNG streams."""
    return {
        name: copy.deepcopy(getattr(sim, name).bit_generator.state)
        for name in RNG_STREAMS
    }
