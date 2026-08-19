"""Demo determinism, labeling, refusal, and on-disk workflow isolation.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = SIMULATION_ROOT / "scripts" / "pilot_site_workflow_enablement_demo.py"
DISCLAIMER = "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA"
RANGE_OPS_ID = "range.closed_loop_collection_handoff"


def run_demo(out_dir: Path, *, hash_seed: str | None = None):
    environment = dict(os.environ)
    if hash_seed is not None:
        environment["PYTHONHASHSEED"] = hash_seed
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--out", str(out_dir)],
        capture_output=True,
        text=True,
        cwd=SIMULATION_ROOT,
        env=environment,
    )


def evidence_bytes(out_dir: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(out_dir)): path.read_bytes()
        for path in sorted(out_dir.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def first_run(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("demo-first")
    result = run_demo(out_dir)
    assert result.returncode == 0, result.stderr
    return out_dir, result


class TestDeterminism:
    def test_two_runs_are_byte_identical_including_stdout(
        self, first_run, tmp_path
    ):
        first_dir, first = first_run
        second = run_demo(tmp_path)
        assert second.returncode == 0, second.stderr
        assert second.stdout == first.stdout
        assert evidence_bytes(tmp_path) == evidence_bytes(first_dir)

    @pytest.mark.parametrize("hash_seed", ["0", "1", "424242"])
    def test_evidence_is_stable_across_hash_seeds(
        self, first_run, tmp_path, hash_seed
    ):
        first_dir, first = first_run
        result = run_demo(tmp_path, hash_seed=hash_seed)
        assert result.returncode == 0, result.stderr
        assert result.stdout == first.stdout
        assert evidence_bytes(tmp_path) == evidence_bytes(first_dir)


class TestLabelingAndBoundary:
    def test_stdout_is_wrapped_in_the_simulated_disclaimer(self, first_run):
        _, result = first_run
        assert result.stdout.startswith(DISCLAIMER)
        assert result.stdout.rstrip().endswith(DISCLAIMER)

    def test_demo_evidence_states_the_absent_physical_boundary(
        self, first_run
    ):
        first_dir, _ = first_run
        root = first_dir / "pilot-course-a" / "pilot-a-enablement-v0"
        evidence = json.loads(
            (root / "pilot_site_workflow_enablement.json").read_text()
        )
        boundary = evidence["physical_boundary"]
        assert boundary["live_transport"] is False
        assert boundary["physical_device_connection"] is False
        assert boundary["robot_command_surface"] is False
        assert boundary["actuator_or_estop_control"] is False
        assert boundary["network_call"] is False
        assert evidence["disclaimer"] == DISCLAIMER

    def test_reports_verify_and_show_independent_readiness(self, first_run):
        from nxt_workflow_enablement import verify_report_payload

        first_dir, _ = first_run
        root = first_dir / "pilot-course-a" / "pilot-a-enablement-v0"
        not_ready = json.loads(
            (root / "workflow_enablement_report.not_ready.json").read_text()
        )
        ready = json.loads(
            (root / "workflow_enablement_report.ready.json").read_text()
        )
        verify_report_payload(not_ready)
        verify_report_payload(ready)
        assert (
            not_ready["workflows"][RANGE_OPS_ID]["verdict"] == "NOT_READY"
        )
        assert (
            ready["workflows"][RANGE_OPS_ID]["verdict"]
            == "READY_FOR_FIXTURE_SHADOW_MODE"
        )
        for payload in (not_ready, ready):
            assert payload["transport_mode"] == "FIXTURE_ONLY"
            assert payload["physical_execution_reachable"] is False
            for workflow_id in (
                "course.grounds_condition_intelligence",
                "course.player_caddy_experience",
            ):
                assert (
                    payload["workflows"][workflow_id]["verdict"]
                    == "NOT_READY"
                )
                assert payload["workflows"][workflow_id]["missing"]


class TestOnDiskWorkflowIsolation:
    def test_only_the_ready_workflow_has_an_evidence_directory(
        self, first_run
    ):
        first_dir, _ = first_run
        root = first_dir / "pilot-course-a" / "pilot-a-enablement-v0"
        directories = sorted(
            path.name for path in root.iterdir() if path.is_dir()
        )
        assert directories == [RANGE_OPS_ID]
        assert (root / RANGE_OPS_ID / "evaluations.jsonl").exists()
        assert (root / RANGE_OPS_ID / "ledger.jsonl").exists()
        assert (root / RANGE_OPS_ID / "snapshots.jsonl").exists()

    def test_all_output_stays_inside_the_requested_directory(
        self, first_run
    ):
        first_dir, _ = first_run
        unexpected = [
            str(path.relative_to(first_dir))
            for path in first_dir.iterdir()
            if path.name != "pilot-course-a"
        ]
        assert unexpected == []


class TestRefusal:
    def test_demo_refuses_a_non_empty_evidence_directory(self, tmp_path):
        first = run_demo(tmp_path)
        assert first.returncode == 0, first.stderr
        second = run_demo(tmp_path)
        assert second.returncode != 0
        assert "refusing to write into a non-empty evidence directory" in (
            second.stderr
        )
