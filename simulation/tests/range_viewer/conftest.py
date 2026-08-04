"""Shared fixtures for nxt_range_viewer tests.

Real scenarios are one full operating day (960 steps); tests shrink the day
to 40 minutes so every replay exercise runs in milliseconds while still
using the real simulator, real policies, and the real episode loop.
"""

from __future__ import annotations

import pytest

from nxt_range_ops.config.models import EpisodeConfig, OperatingHoursConfig, RangeOpsScenario
from nxt_range_ops.evaluation.harness import DISCLAIMER
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


@pytest.fixture
def short_scenario():
    return short_day("normal_weekday")


@pytest.fixture
def mini_report() -> dict:
    """A structurally faithful miniature of reports/range_agent_e1/report.json."""
    return {
        "disclaimer": DISCLAIMER,
        "provenance": {
            "simulator_version": "0.5.0",
            "agent_version": "0.1.0",
            "git_commit": "abc1234",
            "placeholder_parameter_count": 26,
            "placeholder_parameters_by_scenario": {"normal_weekday": 26},
            "disclaimer": DISCLAIMER,
        },
        "config": {
            "policies": ["inventory_threshold", "random_valid"],
            "scenarios": ["normal_weekday"],
            "fixed_seeds": [101],
            "n_random_seeds": 0,
            "seed_pool_seed": 7,
            "bootstrap_resamples": 2000,
            "bootstrap_seed": 13,
            "thresholds": {
                "availability_floor": 0.95,
                "max_stockout_minutes_worst_seed": 60.0,
                "max_mean_human_interventions": 5.0,
            },
        },
        "seeds": [101],
        "rankings": {
            "overall": [
                {
                    "rank": 1,
                    "policy": "inventory_threshold",
                    "mean_composite_rank": 1.0,
                    "scenario_wins": 1,
                    "mean_reward": 12.0,
                },
                {
                    "rank": 2,
                    "policy": "random_valid",
                    "mean_composite_rank": 2.0,
                    "scenario_wins": 0,
                    "mean_reward": -3.0,
                },
            ],
            "per_scenario": [
                {
                    "scenario": "normal_weekday",
                    "winner": "inventory_threshold",
                    "ranking": [
                        {
                            "policy": "inventory_threshold",
                            "composite_rank": 1.0,
                            "reward_mean": 12.0,
                            "kpi_means": {
                                "service_availability": 1.0,
                                "stockout_minutes": 0.0,
                                "human_interventions": 0.0,
                                "estimated_operating_cost": 100.0,
                            },
                        }
                    ],
                }
            ],
        },
        "summary": [],
        "failures": {"cells": [], "per_policy_worst": [], "hardest_scenarios": []},
        "reward_vs_winner": [],
    }
