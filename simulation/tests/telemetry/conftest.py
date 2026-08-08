"""Fixtures for the observation-layer tests."""

from __future__ import annotations

import copy

import pytest

from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario

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
    return RangeSimulation(weekday, seed=321)


def rng_states(sim: RangeSimulation) -> dict:
    return {
        name: copy.deepcopy(getattr(sim, name).bit_generator.state)
        for name in RNG_STREAMS
    }
