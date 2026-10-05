"""Composition-root behavior: fixture storyline, seam, and demo script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.pilot_course_a_edge_fixture import (
    SENSOR_DISPENSER_COUNT,
    SENSOR_DISPENSER_SENSED,
    TOTAL_BALLS,
)
from scripts.site_agent_fixture import (
    SERVICE_CYCLES,
    service_cycle_catalog,
    service_raw_batch,
    service_site,
)

SIMULATION_ROOT = Path(__file__).resolve().parents[2]


def test_every_service_cycle_conserves_the_ball_population():
    for spec in SERVICE_CYCLES:
        assert spec.conserves(), spec.label
    assert [spec.cycle_index for spec in SERVICE_CYCLES] == [0, 1, 2, 3, 4, 5]


def test_missing_dispenser_batch_omits_both_dispenser_samples():
    silent_spec = SERVICE_CYCLES[2]
    assert silent_spec.variant == "missing_dispenser"
    batch = service_raw_batch(silent_spec)
    sensor_ids = {sample.sensor_id for sample in batch.load_cells}
    assert SENSOR_DISPENSER_COUNT not in sensor_ids
    assert SENSOR_DISPENSER_SENSED not in sensor_ids
    # the other load cells still report: the device, not the site, is silent
    assert len(batch.load_cells) == 2
    nominal = service_raw_batch(SERVICE_CYCLES[0])
    assert len(nominal.load_cells) == 4


def test_silent_dispenser_becomes_explicit_missing_not_zero():
    from scripts.pilot_course_a_edge_fixture import adapter_kit

    site = service_site()
    kit = adapter_kit(site)
    result = kit.convert(service_raw_batch(SERVICE_CYCLES[2]))
    by_channel = {
        observation.channel: observation
        for observation in result.observations
    }
    count = by_channel["inventory.dispenser.count"]
    assert count.status.value == "missing"
    assert count.value is None
    assert count.confidence == 0.0
    codes = {entry.code for entry in result.report.rejected}
    assert "no_sample" in codes


def test_uncalibrated_cycle_reports_calibration_rejection():
    from scripts.pilot_course_a_edge_fixture import adapter_kit

    site = service_site()
    kit = adapter_kit(site)
    result = kit.convert(service_raw_batch(SERVICE_CYCLES[4]))
    codes = {entry.code for entry in result.report.rejected}
    assert "calibration_missing" in codes


def test_cycle_catalog_is_labeled_simulated():
    catalog = service_cycle_catalog()
    assert len(catalog) == 6
    assert all(item["source"] == "SIMULATED" for item in catalog)
    assert catalog[0]["scenario_time"] == "17:30"
    assert catalog[2]["scenario_time"] == "19:00"
    assert TOTAL_BALLS == 8000


def _run_demo(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "scripts/site_agent_demo.py",
            *args,
        ],
        cwd=SIMULATION_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_demo_no_serve_runs_and_reports_health(tmp_path):
    result = _run_demo(
        "--out", str(tmp_path / "runs"), "--advance", "7", "--no-serve"
    )
    assert result.returncode == 0, result.stderr
    assert "SIMULATED PILOT SCENARIO" in result.stdout
    assert '"service_state": "stopped"' in result.stdout
    assert '"pending_recommendation_count": 2' in result.stdout
    # deterministic repeat into a fresh directory
    repeat = _run_demo(
        "--out", str(tmp_path / "runs2"), "--advance", "7", "--no-serve"
    )
    assert repeat.returncode == 0, repeat.stderr
    assert repeat.stdout == result.stdout
    streams = (
        "ledger.jsonl",
        "evaluations.jsonl",
        "snapshots.jsonl",
    )
    base = (
        Path(tmp_path)
        / "runs"
        / "run-001"
        / "pilot-course-a"
        / "pilot-a-site-agent-v0"
        / "range.closed_loop_collection_handoff"
    )
    other = (
        Path(tmp_path)
        / "runs2"
        / "run-001"
        / "pilot-course-a"
        / "pilot-a-site-agent-v0"
        / "range.closed_loop_collection_handoff"
    )
    for name in streams:
        assert (base / name).read_bytes() == (other / name).read_bytes()


def test_demo_broken_refuses_with_not_ready_report(tmp_path):
    result = _run_demo("--out", str(tmp_path / "runs"), "--broken")
    assert result.returncode == 3
    assert "NOT_READY" in result.stdout
    assert "refused" in result.stdout
    report = (
        Path(tmp_path)
        / "runs"
        / "broken"
        / "workflow_enablement_report.not_ready.json"
    )
    assert report.is_file()
