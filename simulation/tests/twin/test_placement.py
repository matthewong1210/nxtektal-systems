import math

import pytest

from nxt_range_twin.placement import (
    CROWD_RING_RADIUS_M,
    build_layout_index,
    resolve_location,
    robot_offset,
)

LAYOUT = {
    "dispenser": {"x_m": 0.0, "y_m": 0.0},
    "charger": {"position": {"x_m": 5.0, "y_m": -30.0}, "slots": 2},
    "zones": [{"zone_id": "Z1", "position": {"x_m": 40.0, "y_m": -25.0}}],
    "stations": [{"station_id": "H1", "position": {"x_m": 10.0, "y_m": -20.0}}],
}


def test_every_location_grammar_form_resolves():
    index = build_layout_index(LAYOUT)
    assert resolve_location("dispenser", index) == (0.0, 0.0)
    assert resolve_location("charger", index) == (5.0, -30.0)
    assert resolve_location("zone:Z1", index) == (40.0, -25.0)
    assert resolve_location("station:H1", index) == (10.0, -20.0)
    with pytest.raises(KeyError):
        resolve_location("zone:NOPE", index)
    with pytest.raises(KeyError):
        resolve_location("teleporter", index)


def test_robot_offsets_are_deterministic_and_disjoint():
    ids = ("R1", "R2", "R3")
    offsets = [robot_offset(r, ids) for r in ids]
    assert offsets == [robot_offset(r, ids) for r in ids]  # deterministic
    assert len(set(offsets)) == 3  # disjoint
    for dx, dy in offsets:
        assert math.hypot(dx, dy) == pytest.approx(CROWD_RING_RADIUS_M)
