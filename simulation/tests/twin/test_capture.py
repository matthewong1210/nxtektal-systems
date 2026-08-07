"""Capture produces a complete, byte-reproducible artifact set."""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from facility_twin_capture import capture_episode  # noqa: E402

pytestmark = pytest.mark.slow  # full-episode re-runs


def _run(tmp_path: Path, tag: str) -> Path:
    return capture_episode(
        scenario="handoff_station_outage",
        policy="inventory_threshold",
        seed=7,
        every_steps=1,
        site_id="sim-baseline",
        deployment_id="dev",
        twin_root=tmp_path / tag / "digital_twin",
        demo_root=tmp_path / tag / "demo",
    )


def test_capture_writes_complete_artifact_set(tmp_path: Path):
    episode_dir = _run(tmp_path, "a")
    assert episode_dir.name == "handoff_station_outage-seed7"
    for name in ("layout.json", "facility_states.jsonl", "events.jsonl", "stream.meta.json"):
        assert (episode_dir / name).exists(), name
    meta = json.loads((episode_dir / "stream.meta.json").read_text())
    states = (episode_dir / "facility_states.jsonl").read_text().splitlines()
    # initial snapshot + one per control step
    assert meta["n_records"] == len(states)
    first = json.loads(states[0])
    assert first["meta"]["scenario_name"] == "handoff_station_outage"
    assert first["ball_flow"]["conserved"] is True
    # briefings sidecar lives OUTSIDE the twin store
    sidecar = tmp_path / "a" / "demo" / "handoff_station_outage-seed7" / "briefings.jsonl"
    assert sidecar.exists()
    brief0 = json.loads(sidecar.read_text().splitlines()[0])
    assert set(brief0) == {"seq", "t_s", "briefing", "recommendations"}
    assert "digital_twin" not in str(sidecar)


def test_capture_is_byte_reproducible(tmp_path: Path):
    dir_a = _run(tmp_path, "a")
    dir_b = _run(tmp_path, "b")
    for name in ("layout.json", "facility_states.jsonl", "events.jsonl", "stream.meta.json"):
        assert (dir_a / name).read_bytes() == (dir_b / name).read_bytes(), name
