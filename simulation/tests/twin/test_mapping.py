import pytest

from nxt_range_twin.mapping import EMITTED_ATTRS, frame_opinions
from nxt_range_twin.placement import build_layout_index
from tests.twin.fixtures import LAYOUT, STATE  # Step 2 creates fixtures


def test_opinions_sorted_deterministic_and_traceable():
    index = build_layout_index(LAYOUT)
    ops = frame_opinions(STATE, index, ("R1", "R2"))
    assert ops == sorted(ops, key=lambda o: (o[0], o[1]))
    assert ops == frame_opinions(STATE, index, ("R1", "R2"))
    for _, attr, _, _ in ops:
        if attr.startswith("nxt:"):
            assert attr in EMITTED_ATTRS, attr


def test_robot_translate_uses_node_anchor_plus_offset():
    index = build_layout_index(LAYOUT)
    ops = frame_opinions(STATE, index, ("R1", "R2"))
    translates = {o[0]: o[3] for o in ops if o[1] == "xformOp:translate"}
    x, y, z = translates["/World/Site/Robots/R1"]
    assert z == 0.0
    # R1 at "zone:Z1" anchor (40,-25) plus its deterministic ring offset
    assert (x, y) != (40.0, -25.0)
    assert abs(x - 40.0) <= 2.5 and abs(y + 25.0) <= 2.5


def test_unknown_keys_fail_loud_both_directions():
    index = build_layout_index(LAYOUT)
    drifted = dict(STATE)
    drifted["new_group"] = {"x": 1}
    with pytest.raises(ValueError, match="new_group"):
        frame_opinions(drifted, index, ("R1", "R2"))
    drifted2 = dict(STATE)
    drifted2["robots"] = [dict(STATE["robots"][0], surprise=1)] + STATE["robots"][1:]
    with pytest.raises(ValueError, match="surprise"):
        frame_opinions(drifted2, index, ("R1", "R2"))


def test_transit_robot_holds_last_position_no_translate_opinion():
    # Real episodes emit robot.location == "transit" mid-travel (see
    # nxt_range_ops/core/sim.py). "transit" is not a placement node, so the
    # mapping must omit the xformOp:translate opinion for that robot/frame
    # rather than resolve it — USD naturally holds the last authored
    # translate sample. All other robot opinions, including the "transit"
    # location token itself, are still emitted.
    index = build_layout_index(LAYOUT)
    transit_state = dict(STATE)
    transit_state["robots"] = [
        dict(STATE["robots"][0], location="transit", destination="station:H1"),
        STATE["robots"][1],
    ]
    ops = frame_opinions(transit_state, index, ("R1", "R2"))

    translate_prims = {o[0] for o in ops if o[1] == "xformOp:translate"}
    assert "/World/Site/Robots/R1" not in translate_prims
    assert "/World/Site/Robots/R2" in translate_prims  # unaffected robot untouched

    r1_location = [o for o in ops if o[0] == "/World/Site/Robots/R1" and o[1] == "nxt:location"]
    assert r1_location == [("/World/Site/Robots/R1", "nxt:location", "token", "transit")]


def test_scalar_subgroup_drift_raises():
    # A new key inside a scalar sub-group (e.g. "washer") must fail loud too,
    # not just top-level state groups and per-entity dicts.
    index = build_layout_index(LAYOUT)
    drifted = dict(STATE)
    drifted["washer"] = dict(STATE["washer"], surprise_field=1)
    with pytest.raises(ValueError, match="surprise_field"):
        frame_opinions(drifted, index, ("R1", "R2"))


def test_zone_entity_drift_raises():
    index = build_layout_index(LAYOUT)
    drifted = dict(STATE)
    drifted["zones"] = [dict(STATE["zones"][0], surprise=1)] + STATE["zones"][1:]
    with pytest.raises(ValueError, match="surprise"):
        frame_opinions(drifted, index, ("R1", "R2"))


def test_station_entity_drift_raises():
    index = build_layout_index(LAYOUT)
    drifted = dict(STATE)
    drifted["stations"] = [dict(STATE["stations"][0], surprise=1)]
    with pytest.raises(ValueError, match="surprise"):
        frame_opinions(drifted, index, ("R1", "R2"))


def test_unknown_location_node_still_raises_key_error():
    # Genuinely unknown location nodes (contract drift, not the known
    # "transit" grammar) must still fail loud via resolve_location.
    index = build_layout_index(LAYOUT)
    drifted = dict(STATE)
    drifted["robots"] = [
        dict(STATE["robots"][0], location="warpgate"),
        STATE["robots"][1],
    ]
    with pytest.raises(KeyError, match="warpgate"):
        frame_opinions(drifted, index, ("R1", "R2"))
