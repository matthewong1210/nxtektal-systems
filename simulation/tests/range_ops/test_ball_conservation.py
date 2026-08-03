"""Ball conservation: every ball is in exactly one place at all times."""

from __future__ import annotations

import pytest

from nxt_range_ops.core.ledger import BallConservationError, BallLedger
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.scenarios.generators import make_scenario

from tests.range_ops.conftest import run_policy_episode


class TestLedgerUnit:
    def test_move_is_atomic_and_clamped(self):
        ledger = BallLedger(["a", "b"], {"a": 10})
        assert ledger.move("a", "b", 4) == 4
        assert ledger.counts() == {"a": 6, "b": 4}
        # Clamped to availability, never negative.
        assert ledger.move("a", "b", 100) == 6
        assert ledger.counts() == {"a": 0, "b": 10}
        ledger.assert_conserved()

    def test_negative_move_rejected(self):
        ledger = BallLedger(["a", "b"], {"a": 5})
        with pytest.raises(ValueError):
            ledger.move("a", "b", -1)

    def test_unknown_location_rejected(self):
        ledger = BallLedger(["a"], {"a": 5})
        with pytest.raises(ValueError):
            ledger.move("a", "nope", 1)

    def test_corruption_detected(self):
        ledger = BallLedger(["a", "b"], {"a": 5})
        ledger._counts["b"] += 3  # simulate a bug
        with pytest.raises(BallConservationError):
            ledger.assert_conserved()


@pytest.mark.parametrize(
    "scenario_name,policy_name",
    [
        ("normal_weekday", "inventory_threshold"),
        ("weekend_peak", "nearest_available_robot"),
        ("repeated_docking_failure", "random_valid"),
        ("handoff_station_outage", "demand_forecast_dispatch"),
        ("robot_failure", "inventory_threshold"),
    ],
)
def test_full_episode_conserves_balls(scenario_name, policy_name):
    scenario = make_scenario(scenario_name)
    env = RangeOpsEnv(scenario)
    obs, info = env.reset(seed=11)
    total = env.sim.ledger.total
    assert total == scenario.total_balls

    _, _, infos, _ = run_policy_episode(env, policy_name, seed=11)
    # advance() asserts conservation internally every step; verify externally
    # too, and confirm the episode actually ran a full day.
    assert sum(env.sim.ledger.counts().values()) == scenario.total_balls
    env.sim.ledger.assert_conserved()
    assert len(infos) >= 900
