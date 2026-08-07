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
