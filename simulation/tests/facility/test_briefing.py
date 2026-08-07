"""Manager-briefing renderer tests (pure string formatting, no simulator)."""

from __future__ import annotations

from nxt_facility.briefing import render_briefing

from .test_decisions import make_state, robot, station, zone


def test_briefing_header_carries_status_stock_and_eta():
    state = make_state(
        clean=200,
        forecast=(10.0, 10.0, 10.0, 10.0),
        robots=(robot("R1"),),
        zones=(zone("Z1", balls=400),),
        stations=(station(buffer=0),),
    )
    text = render_briefing(state)
    assert "FACILITY BRIEFING" in text
    assert "unit" in text  # scenario name
    assert "10:00" in text  # minute_of_day 600 rendered as clock time
    assert "Clean stock 200" in text
    assert "stockout" in text.lower()
    assert "~20 min" in text  # supply-limited walk: 200 clean / 10 per min


def test_briefing_tiers_and_ordering():
    state = make_state(
        clean=200,
        forecast=(10.0, 10.0, 10.0, 10.0),
        robots=(robot("R1"), robot("R2", battery=0.10), robot("R3", payload=190)),
        zones=(zone("Z1", balls=400),),
        stations=(station(buffer=0),),
    )
    text = render_briefing(state)
    assert "DO NOW" in text
    # NOW tier (battery R2, dispatch R1->Z1) renders before SOON tier
    # (payload_stranded R3); empty tiers are omitted entirely.
    assert text.index("DO NOW") < text.index("DO NEXT HOUR")
    assert "R2" in text and "Z1" in text and "R3" in text


def test_briefing_all_clear_when_no_rules_fire():
    text = render_briefing(make_state(zones=(zone(balls=10),)))
    assert "No action needed" in text
    assert "NOMINAL" in text


def test_briefing_flags_forecast_confidence():
    state = make_state(
        clean=200,
        forecast=(10.0, 10.0, 10.0, 10.0),
        robots=(robot("R1"),),
        zones=(zone("Z1", balls=400),),
        stations=(station(buffer=0),),
    )
    text = render_briefing(state)
    # forecast-derived advice is visibly marked as such
    assert "forecast" in text.lower()


def test_briefing_carries_placeholder_disclaimer():
    text = render_briefing(make_state(zones=(zone(balls=10),)))
    assert "placeholder" in text.lower()
