"""Shared fixtures for nxt_range_agent tests.

Real scenarios are one full operating day (960 steps); tests shrink the day
to 40 minutes so every benchmark exercise runs in milliseconds while still
using the real simulator, real policies, and the real episode loop.
"""

from __future__ import annotations

import pytest

from nxt_range_ops.config.models import EpisodeConfig, OperatingHoursConfig, RangeOpsScenario
from nxt_range_ops.scenarios.generators import make_scenario


def short_day(name: str) -> RangeOpsScenario:
    """A named scenario compressed to a 06:00-06:40 operating day."""
    scenario = make_scenario(name)
    return scenario.model_copy(
        update={
            "hours": OperatingHoursConfig(open_minute=360, close_minute=400),
            "episode": EpisodeConfig(control_interval_s=60.0, max_steps=60),
        }
    )


@pytest.fixture
def short_scenario_factory():
    return short_day
