"""Pure-logic tests for the demo viewer's bundle loading and robot tracks."""

from __future__ import annotations

import pytest

from nxt_range_demo.bundle import (
    compute_robot_tracks,
    load_bundle,
    node_positions,
    parse_action,
    sim_clock,
)


def test_load_bundle_reads_all_three_files(bundle_dir):
    bundle = load_bundle(bundle_dir)
    assert bundle.layout["scenario"] == "demo_scenario"
    assert bundle.episode["meta"]["seed"] == 101
    assert bundle.benchmark["rankings"]["overall"][0]["rank"] == 1


def test_load_bundle_benchmark_is_optional(bundle_dir):
    (bundle_dir / "benchmark.json").unlink()
    bundle = load_bundle(bundle_dir)
    assert bundle.benchmark is None


def test_load_bundle_missing_episode_raises_clear_error(bundle_dir):
    (bundle_dir / "episode.json").unlink()
    with pytest.raises(FileNotFoundError, match="episode.json"):
        load_bundle(bundle_dir)


def test_load_bundle_rejects_wrong_schema(bundle_dir):
    (bundle_dir / "layout.json").write_text('{"schema": "something/else"}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_bundle(bundle_dir)


def test_node_positions_covers_all_facility_nodes(bundle_dir):
    bundle = load_bundle(bundle_dir)
    positions = node_positions(bundle.layout)
    assert positions["dispenser"] == (0.0, 0.0)
    assert positions["charger"] == (-10.0, 5.0)
    assert positions["zone:Z1"] == (100.0, 0.0)
    assert positions["zone:Z2"] == (50.0, 20.0)
    assert positions["station:H1"] == (-5.0, -10.0)


def test_robot_tracks_interpolate_transit_linearly(bundle_dir):
    bundle = load_bundle(bundle_dir)
    positions = node_positions(bundle.layout)
    tracks = compute_robot_tracks(bundle.episode, positions)

    assert len(tracks) == len(bundle.episode["frames"])
    # R2 never moves off the charger.
    for frame_positions in tracks:
        assert frame_positions["R2"] == positions["charger"]

    # R1 travels charger -> Z1 over frames 1-2, arriving frame 3: the two
    # transit frames sit at 1/3 and 2/3 of the way along the leg.
    (cx, cy), (zx, zy) = positions["charger"], positions["zone:Z1"]
    x1, y1 = tracks[0]["R1"]
    x2, y2 = tracks[1]["R1"]
    assert x1 == pytest.approx(cx + (zx - cx) / 3)
    assert y1 == pytest.approx(cy + (zy - cy) / 3)
    assert x2 == pytest.approx(cx + (zx - cx) * 2 / 3)
    assert y2 == pytest.approx(cy + (zy - cy) * 2 / 3)
    assert tracks[2]["R1"] == positions["zone:Z1"]


def test_robot_tracks_transit_at_episode_end_heads_toward_destination(bundle_dir):
    bundle = load_bundle(bundle_dir)
    positions = node_positions(bundle.layout)
    tracks = compute_robot_tracks(bundle.episode, positions)

    # Final frame: R1 is in transit Z1 -> H1 with no arrival frame; it must
    # sit strictly between the two nodes, not on either.
    x, y = tracks[3]["R1"]
    (ax, ay), (bx, by) = positions["zone:Z1"], positions["station:H1"]
    assert (x, y) != (ax, ay)
    assert (x, y) != (bx, by)
    assert min(ax, bx) <= x <= max(ax, bx)
    assert min(ay, by) <= y <= max(ay, by)


def test_parse_action_extracts_verb_and_args():
    assert parse_action("wait") == ("wait", [])
    assert parse_action("assign_collection(R1,Z3)") == ("assign_collection", ["R1", "Z3"])
    assert parse_action("send_to_charge(R2)") == ("send_to_charge", ["R2"])
    assert parse_action("request_human_assistance(R1,robot_failure)") == (
        "request_human_assistance",
        ["R1", "robot_failure"],
    )


def test_sim_clock_formats_seconds_since_midnight():
    assert sim_clock(21600.0) == "06:00"
    assert sim_clock(21660.0) == "06:01"
    assert sim_clock(79140.0) == "21:59"
