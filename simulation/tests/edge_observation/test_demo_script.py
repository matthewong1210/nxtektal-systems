"""The edge-observation demo stays deterministic, bounded, and honest."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = SIMULATION_ROOT / "scripts" / "edge_observation_adapter_demo.py"
DISCLAIMER = "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA"
EVIDENCE_ROOT = ("pilot-course-a", "pilot-a-edge-v0")


def _run_demo(out_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--out", str(out_dir)],
        cwd=SIMULATION_ROOT,
        capture_output=True,
        text=True,
    )


def _evidence_bytes(out_dir: Path) -> dict[str, bytes]:
    root = out_dir.joinpath(*EVIDENCE_ROOT)
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.startswith(".")
    }


def _evidence_json(out_dir: Path) -> dict:
    return json.loads(
        out_dir.joinpath(*EVIDENCE_ROOT, "edge_observation_demo.json").read_text(
            encoding="utf-8"
        )
    )


def test_demo_runs_deterministically_and_labels_synthetic_data(tmp_path):
    first = _run_demo(tmp_path / "first")
    assert first.returncode == 0, first.stderr
    assert first.stdout.startswith(DISCLAIMER)
    assert first.stdout.rstrip().endswith(DISCLAIMER)

    second = _run_demo(tmp_path / "second")
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    assert _evidence_bytes(tmp_path / "first") == _evidence_bytes(
        tmp_path / "second"
    )


def test_demo_reports_admission_rejection_and_evaluation(tmp_path):
    result = _run_demo(tmp_path / "out")
    assert result.returncode == 0, result.stderr
    payload = _evidence_json(tmp_path / "out")

    # Each row names the cycle it actually consumed, not a positional guess.
    assert [cycle["cycle_index"] for cycle in payload["cycles"]] == [
        0,
        1,
        2,
        3,
        4,
        None,
    ]
    kinds = [cycle["kind"] for cycle in payload["cycles"]]
    assert kinds == [
        "evaluated",
        "evaluated",
        "rejected",
        "rejected",
        "evaluated",
        "source_exhausted",
    ]
    failures = [
        cycle["site_runtime_failure"]["code"]
        for cycle in payload["cycles"]
        if cycle["site_runtime_failure"] is not None
    ]
    assert failures == ["stale_observation", "insufficient_data_quality"]
    verdicts = [
        cycle["verdict"] for cycle in payload["cycles"] if cycle["verdict"]
    ]
    assert verdicts[0] == "no_action"
    assert payload["runtime_status"]["evaluations_completed"] == 3


def test_demo_reports_adapter_coverage_including_the_gaps(tmp_path):
    _run_demo(tmp_path / "out")
    payload = _evidence_json(tmp_path / "out")
    coverage = payload["coverage"]
    assert coverage["adapter_version"] == "nxt-edge-observation/adapter/v0"
    assert coverage["adapter_produced_channels"]
    assert coverage["channels_without_a_v0_adapter_family"] == [
        "charger.site.queue_length",
        "scan.zone.Z1.balls",
        "staff.site.busy",
        "staff.site.queued",
        "station.ST1.queue_length",
    ]
    assert not set(coverage["adapter_produced_channels"]) & set(
        coverage["channels_without_a_v0_adapter_family"]
    )


def test_demo_shows_accepted_and_rejected_adapter_inputs(tmp_path):
    _run_demo(tmp_path / "out")
    payload = _evidence_json(tmp_path / "out")
    reports = [
        cycle["adapter_report"]
        for cycle in payload["cycles"]
        if cycle["adapter_report"] is not None
    ]
    assert len(reports) == 5
    assert all(report["accepted"] for report in reports)
    assert all(report["unmapped"] for report in reports)
    uncalibrated = reports[3]
    assert [item["code"] for item in uncalibrated["rejected"]] == [
        "calibration_missing"
    ]


def test_demo_states_the_absent_physical_boundary(tmp_path):
    _run_demo(tmp_path / "out")
    payload = _evidence_json(tmp_path / "out")
    boundary = payload["physical_boundary"]
    assert boundary["live_transport"] is False
    assert boundary["physical_device_connection"] is False
    assert boundary["robot_command_surface"] is False
    assert boundary["actuator_or_estop_control"] is False
    assert boundary["network_call"] is False
    assert payload["disclaimer"] == DISCLAIMER


def test_demo_refuses_to_append_over_an_existing_run(tmp_path):
    assert _run_demo(tmp_path / "out").returncode == 0
    repeat = _run_demo(tmp_path / "out")
    assert repeat.returncode != 0
    assert "append-only" in repeat.stderr


def test_demo_writes_only_inside_the_requested_output_directory(tmp_path):
    """The demo must not write into the checkout.

    The snapshot is scoped to the directories the demo could plausibly
    touch rather than the whole of ``simulation/``: this repository is
    routinely worked on by concurrent sessions, and a repo-wide snapshot
    would fail on an unrelated edit instead of on a real defect.
    """
    watched = (
        SIMULATION_ROOT / "scripts",
        SIMULATION_ROOT / "nxt_edge_observation",
        SIMULATION_ROOT / "reports",
    )

    def snapshot():
        found = set()
        for root in watched:
            if not root.exists():
                continue
            found |= {
                path.relative_to(SIMULATION_ROOT)
                for path in root.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            }
        return found

    before = snapshot()
    assert _run_demo(tmp_path / "out").returncode == 0
    assert snapshot() == before
