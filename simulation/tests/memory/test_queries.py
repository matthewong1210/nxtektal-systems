"""Golden tests for the pure read-only query functions."""

from __future__ import annotations

import pytest

from nxt_memory.queries import (
    NON_CAUSAL_CAVEAT,
    outcome_deltas_by_decision,
    rule_acceptance_rates,
    stockout_eta_calibration,
)
from nxt_memory.records import HumanVerdict, Verdict

from .conftest import make_window, metrics, sample_recommendations


def dicts(*windows):
    return [w.to_dict() for w in windows]


def test_rule_acceptance_rates_counts_offers_and_verdicts():
    w0 = make_window(
        seq=0,
        verdicts=(
            HumanVerdict(rec_index=0, rule_id="battery_reserve", verdict=Verdict.ACCEPTED, decided_by="op"),
            HumanVerdict(rec_index=1, rule_id="idle_capacity", verdict=Verdict.REJECTED, decided_by="op"),
        ),
    )
    w1 = make_window(
        seq=1,
        verdicts=(
            HumanVerdict(rec_index=0, rule_id="battery_reserve", verdict=Verdict.DEFERRED, decided_by="op"),
        ),
    )
    result = rule_acceptance_rates(dicts(w0, w1))
    battery = result["rules"]["battery_reserve"]["now"]
    assert battery == {
        "offered": 2,
        "accepted": 1,
        "rejected": 0,
        "deferred": 1,
        "unaddressed": 0,
    }
    idle = result["rules"]["idle_capacity"]["soon"]
    assert idle["offered"] == 2
    assert idle["rejected"] == 1
    assert idle["unaddressed"] == 1  # window 1 never addressed it


def test_stockout_eta_calibration_realized_and_censored():
    # Window 0 at t=21600 predicts stockout in 45 min (t=24300);
    # a STOCKOUT event occurs at t=24600 -> error +5 min.
    predicted = make_window(seq=0, eta=45.0, t_start=21600.0)
    later = make_window(
        seq=1,
        eta=None,
        t_start=23400.0,
        events=[{"t_s": 24600.0, "kind": "stockout", "payload": {}}],
    )
    # Window 2 predicts a stockout that never materializes -> censored.
    censored = make_window(seq=2, eta=30.0, t_start=30000.0)
    result = stockout_eta_calibration(dicts(predicted, later, censored))
    assert result["n_predictions"] == 2
    assert result["n_realized"] == 1
    assert result["n_censored"] == 1
    assert result["mean_error_minutes"] == pytest.approx(5.0)
    assert result["mean_abs_error_minutes"] == pytest.approx(5.0)


def test_calibration_never_matches_stockouts_across_episodes():
    # Episodes share the same simulated day clock. A prediction in episode
    # A with no stockout in A must stay CENSORED even when episode B logged
    # a stockout at an overlapping sim time — the critical cross-episode
    # pollution case caught by adversarial review.
    predicted_a = make_window(seq=0, eta=5.0, t_start=21600.0, episode_id="ep-A")
    stockout_b = make_window(
        seq=0,
        eta=None,
        t_start=21600.0,
        episode_id="ep-B",
        events=[{"t_s": 21900.0, "kind": "stockout", "payload": {}}],
    )
    result = stockout_eta_calibration(dicts(predicted_a, stockout_b))
    assert result["n_predictions"] == 1
    assert result["n_realized"] == 0
    assert result["n_censored"] == 1
    assert result["mean_error_minutes"] is None


def test_outcome_deltas_by_decision_partitions_and_caveats():
    accepted = make_window(
        seq=0,
        metrics_after=metrics(stockout_minutes=2.0, open_minutes_elapsed=90.0),
    )
    rejected = make_window(
        seq=1,
        verdicts=(
            HumanVerdict(rec_index=0, rule_id="battery_reserve", verdict=Verdict.REJECTED, decided_by="op"),
        ),
        metrics_after=metrics(stockout_minutes=8.0, open_minutes_elapsed=90.0),
    )
    unaddressed = make_window(seq=2, verdicts=())
    result = outcome_deltas_by_decision(dicts(accepted, rejected, unaddressed))
    assert result["caveat"] == NON_CAUSAL_CAVEAT
    groups = result["groups"]
    assert groups["any_accepted"]["n_windows"] == 1
    assert groups["any_accepted"]["mean_impact"]["stockout_minutes"] == pytest.approx(2.0)
    assert groups["none_accepted"]["n_windows"] == 1
    assert groups["none_accepted"]["mean_impact"]["stockout_minutes"] == pytest.approx(8.0)
    assert groups["no_verdicts"]["n_windows"] == 1


def test_queries_are_pure_and_deterministic():
    windows = dicts(make_window(seq=0), make_window(seq=1))
    snapshot = [dict(w) for w in windows]
    a = rule_acceptance_rates(windows)
    b = rule_acceptance_rates(windows)
    assert a == b
    assert windows == snapshot  # inputs never mutated


def test_acceptance_rates_empty_input():
    assert rule_acceptance_rates([]) == {"rules": {}, "n_windows": 0}


def test_sample_fixture_offers_both_rules():
    # guard: fixtures actually contain the two rules the goldens rely on
    rec_ids = [r["rule_id"] for r in sample_recommendations()]
    assert rec_ids == ["battery_reserve", "idle_capacity"]
