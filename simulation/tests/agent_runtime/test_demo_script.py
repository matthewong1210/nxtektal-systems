"""The Pilot Course A demo script stays deterministic and honest."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = SIMULATION_ROOT / "scripts" / "agent_runtime_demo.py"
DISCLAIMER = "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA"


def _run_demo(out_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--out", str(out_dir)],
        cwd=SIMULATION_ROOT,
        capture_output=True,
        text=True,
    )


def _evidence_bytes(out_dir: Path) -> dict[str, bytes]:
    root = out_dir / "pilot-course-a" / "pilot-a-sim"
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.startswith(".")
    }


def test_demo_runs_deterministically_and_labels_synthetic_data(tmp_path):
    first = _run_demo(tmp_path / "first")
    assert first.returncode == 0, first.stderr
    assert first.stdout.startswith(DISCLAIMER)
    assert first.stdout.rstrip().endswith(DISCLAIMER)
    assert '"verdict": "no_action"' in first.stdout
    assert '"recommendation_action": "operator_intervention"' in first.stdout
    assert '"kind": "replay_skipped"' in first.stdout
    assert "manager accepted rec_" in first.stdout

    second = _run_demo(tmp_path / "second")
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    assert _evidence_bytes(tmp_path / "first") == _evidence_bytes(
        tmp_path / "second"
    )


def test_demo_refuses_to_append_over_an_existing_run(tmp_path):
    assert _run_demo(tmp_path / "out").returncode == 0
    repeat = _run_demo(tmp_path / "out")
    assert repeat.returncode != 0
    assert "append-only" in repeat.stderr
