"""Guards: capture changes nothing, agrees with the viewer harness, stays pure."""
import ast
import json
import sys
from pathlib import Path

import pytest

SIM_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SIM_ROOT / "scripts"))

from facility_twin_capture import capture_episode  # noqa: E402
from nxt_range_viewer.replay import replay_episode  # noqa: E402

pytestmark = pytest.mark.slow

ARGS = dict(scenario="handoff_station_outage", policy="inventory_threshold", seed=7)


def test_capture_matches_viewer_replay_events_and_length(tmp_path: Path):
    """Trajectory neutrality + cross-harness consistency in one assertion set.

    replay_episode never calls build_facility_state; capture calls it every
    step. Identical event logs prove capture is trajectory- and RNG-neutral
    at capture cadence, and that both harnesses re-run the same episode.
    """
    episode_dir = capture_episode(
        **ARGS, every_steps=1, site_id="s", deployment_id="d",
        twin_root=tmp_path / "twin", demo_root=tmp_path / "demo",
    )
    captured_events = [
        json.loads(line)
        for line in (episode_dir / "events.jsonl").read_text().splitlines()
    ]
    result = replay_episode(ARGS["scenario"], ARGS["policy"], ARGS["seed"], event_kinds=None)
    assert captured_events == result.events
    states = (episode_dir / "facility_states.jsonl").read_text().splitlines()
    assert len(states) == result.n_steps + 1  # initial + one per control step


def test_capture_agrees_with_viewer_frames_on_shared_fields(tmp_path: Path):
    """episode.json frames vs facility_states.jsonl: shared per-entity fields agree."""
    episode_dir = capture_episode(
        **ARGS, every_steps=1, site_id="s", deployment_id="d",
        twin_root=tmp_path / "twin", demo_root=tmp_path / "demo",
    )
    states = [
        json.loads(line)
        for line in (episode_dir / "facility_states.jsonl").read_text().splitlines()
    ]
    result = replay_episode(ARGS["scenario"], ARGS["policy"], ARGS["seed"], event_kinds=None)
    # state[k+1] is the snapshot after control step k+1 == frame[k]
    for frame, state in zip(result.frames, states[1:]):
        frame_zone_balls = {z["zone_id"]: z["balls"] for z in frame["zones"]}
        state_zone_balls = {z["zone_id"]: z["balls"] for z in state["zones"]}
        assert frame_zone_balls == state_zone_balls
        frame_robot_loc = {r["robot_id"]: r["location"] for r in frame["robots"]}
        state_robot_loc = {r["robot_id"]: r["location"] for r in state["robots"]}
        assert frame_robot_loc == state_robot_loc


BANNED_IMPORTS = {"time", "datetime", "uuid"}


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_no_wallclock_or_uuid_in_twin_code():
    files = list((SIM_ROOT / "nxt_range_twin").glob("*.py"))
    files.append(SIM_ROOT / "scripts" / "facility_twin_capture.py")
    for path in files:
        assert not (_imports_of(path) & BANNED_IMPORTS), path


def test_capture_script_never_calls_rng_drawing_accessors():
    source = (SIM_ROOT / "scripts" / "facility_twin_capture.py").read_text()
    assert "sensed_zone_counts" not in source
    assert "sensed_battery_frac" not in source


def test_no_upstream_file_mentions_twin():
    """Mirror of test_no_upstream_file_mentions_nxt_facility, extended set."""
    upstream = ["nxt_sim", "nxt_range_ops", "nxt_facility", "nxt_memory",
                "nxt_range_viewer", "nxt_range_agent"]
    for package in upstream:
        for path in (SIM_ROOT / package).rglob("*.py"):
            assert "nxt_range_twin" not in path.read_text(), path
