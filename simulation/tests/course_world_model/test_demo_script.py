"""Demo determinism, labeling, refusal, and readiness isolation.

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
SCRIPT = SIMULATION_ROOT / "scripts" / "course_world_model_demo.py"
DISCLAIMER = "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA"
RANGE_OPS_ID = "range.closed_loop_collection_handoff"
GROUNDS_ID = "course.grounds_condition_intelligence"
PLAYER_CADDY_ID = "course.player_caddy_experience"
GROUNDS_MAP_PREREQUISITES = [
    "course_coordinate_reference",
    "course_model_version",
    "map_version",
]


def run_demo(out_dir: Path, *, hash_seed: str | None = None):
    environment = dict(os.environ)
    if hash_seed is not None:
        environment["PYTHONHASHSEED"] = hash_seed
    # Finite so a regression that hangs the demo fails the test instead
    # of blocking the suite; generous because a cold endpoint-scanner
    # pass over a fresh venv can make the first run legitimately slow.
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--out", str(out_dir)],
        capture_output=True,
        text=True,
        cwd=SIMULATION_ROOT,
        env=environment,
        timeout=300,
    )


def evidence_bytes(out_dir: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(out_dir)): path.read_bytes()
        for path in sorted(out_dir.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def first_run(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("cwm-demo-first")
    result = run_demo(out_dir)
    assert result.returncode == 0, result.stderr
    return out_dir, result


def report_root(out_dir: Path) -> Path:
    return out_dir / "pilot-course-a" / "pilot-a-enablement-v0"


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


class TestLabelingAndArtifacts:
    def test_stdout_is_wrapped_in_the_simulated_disclaimer(self, first_run):
        _, result = first_run
        assert result.stdout.startswith(DISCLAIMER)
        assert result.stdout.rstrip().endswith(DISCLAIMER)

    def test_the_model_artifact_verifies_by_content_digest(self, first_run):
        from nxt_course_world_model import (
            CourseWorldModelError,
            verify_model_payload,
        )

        first_dir, _ = first_run
        payload = json.loads(
            (report_root(first_dir) / "course_model.json").read_text()
        )
        verify_model_payload(payload)
        payload["site_id"] = "tampered-site"
        with pytest.raises(CourseWorldModelError):
            verify_model_payload(payload)

    def test_demo_evidence_states_the_absent_physical_boundary(
        self, first_run
    ):
        first_dir, _ = first_run
        evidence = json.loads(
            (
                report_root(first_dir) / "course_world_model_demo.json"
            ).read_text()
        )
        boundary = evidence["physical_boundary"]
        assert boundary["raw_scan_ingestion"] is False
        assert boundary["live_transport"] is False
        assert boundary["physical_device_connection"] is False
        assert boundary["cart_or_robot_positioning"] is False
        assert boundary["route_planning_or_navigation"] is False
        assert boundary["robot_command_surface"] is False
        assert boundary["actuator_or_estop_control"] is False
        assert boundary["network_call"] is False
        assert evidence["disclaimer"] == DISCLAIMER

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

    def test_no_generated_artifacts_appear_in_the_repository(
        self, tmp_path
    ):
        # Pre-existing worktree state is preserved, never blamed on the
        # demo: only a repository change introduced by this run fails.
        def repository_status() -> str:
            return subprocess.run(
                ["git", "status", "--porcelain", "--", "."],
                capture_output=True,
                text=True,
                cwd=SIMULATION_ROOT,
            ).stdout

        before = repository_status()
        result = run_demo(tmp_path / "hygiene-probe")
        assert result.returncode == 0, result.stderr
        assert repository_status() == before


class TestReadinessIsolation:
    def test_reports_verify_and_show_the_exact_readiness_change(
        self, first_run
    ):
        from nxt_workflow_enablement import verify_report_payload

        first_dir, _ = first_run
        root = report_root(first_dir)
        before = json.loads(
            (root / "workflow_enablement_before.json").read_text()
        )
        after = json.loads(
            (root / "workflow_enablement_after.json").read_text()
        )
        verify_report_payload(before)
        verify_report_payload(after)

        # Range Operations is byte-identical before and after.
        assert (
            before["workflows"][RANGE_OPS_ID]
            == after["workflows"][RANGE_OPS_ID]
        )
        assert before["workflows"][RANGE_OPS_ID]["verdict"] == (
            "READY_FOR_FIXTURE_SHADOW_MODE"
        )

        # Grounds gains exactly the map prerequisites; still NOT_READY.
        assert before["workflows"][GROUNDS_ID]["satisfied"] == []
        assert (
            after["workflows"][GROUNDS_ID]["satisfied"]
            == GROUNDS_MAP_PREREQUISITES
        )
        assert after["workflows"][GROUNDS_ID]["verdict"] == "NOT_READY"
        assert (
            after["workflows"][GROUNDS_ID]["unsupported_in_v0"]
            == before["workflows"][GROUNDS_ID]["unsupported_in_v0"]
        )
        assert (
            after["workflows"][GROUNDS_ID]["deferred"]
            == before["workflows"][GROUNDS_ID]["deferred"]
        )

        # Player Caddy gains exactly the map-query prerequisite.
        assert before["workflows"][PLAYER_CADDY_ID]["satisfied"] == []
        assert after["workflows"][PLAYER_CADDY_ID]["satisfied"] == [
            "course_world_model_map_query"
        ]
        assert after["workflows"][PLAYER_CADDY_ID]["verdict"] == "NOT_READY"

        # No workflow becomes ready from a map alone.
        for payload in (before, after):
            assert payload["summary"]["ready_workflow_ids"] == [
                RANGE_OPS_ID
            ]


class TestRefusal:
    def test_demo_refuses_a_non_empty_evidence_directory(self, tmp_path):
        first = run_demo(tmp_path)
        assert first.returncode == 0, first.stderr
        second = run_demo(tmp_path)
        assert second.returncode != 0
        assert "refusing to write into a non-empty evidence directory" in (
            second.stderr
        )

    def test_demo_refuses_a_file_valued_evidence_root(self, tmp_path):
        blocker = tmp_path / "pilot-course-a" / "pilot-a-enablement-v0"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("not a directory", encoding="utf-8")
        result = run_demo(tmp_path)
        assert result.returncode != 0
        assert "is not a directory" in result.stderr

    def test_demo_refuses_a_file_valued_parent_of_the_root(self, tmp_path):
        blocker = tmp_path / "pilot-course-a"
        blocker.write_text("not a directory", encoding="utf-8")
        result = run_demo(tmp_path)
        assert result.returncode != 0
        assert "refusing to write" in result.stderr
