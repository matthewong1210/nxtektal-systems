#!/usr/bin/env python3
"""Focused policy test for the Edge Gateway Compose smoke job."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "verification.yml"


def _mapping_block(text: str, *, key: str, indent: int) -> str:
    """Return one indentation-delimited YAML mapping block."""
    lines = text.splitlines()
    marker = f"{' ' * indent}{key}:"
    matches = [index for index, line in enumerate(lines) if line == marker]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one {key!r} mapping at indent {indent}, "
            f"found {len(matches)}"
        )
    start = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        current_indent = len(line) - len(line.lstrip(" "))
        if current_indent <= indent:
            end = index
            break
    return "\n".join(lines[start:end])


def _step_blocks(job: str) -> tuple[str, ...]:
    """Return the named step blocks from one top-level job."""
    lines = job.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^ {6}- name: \S", line)
    ]
    blocks: list[str] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        blocks.append("\n".join(lines[start:end]))
    return tuple(blocks)


def _single_step_with(steps: tuple[str, ...], token: str) -> str:
    matches = [step for step in steps if token in step]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one workflow step containing {token!r}, "
            f"found {len(matches)}"
        )
    return matches[0]


def _one_line(block: str) -> str:
    return " ".join(line.strip() for line in block.splitlines())


class EdgeGatewayComposeWorkflowPolicyTests(unittest.TestCase):
    def test_edge_gateway_compose_smoke_job_contract(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        job = _mapping_block(
            workflow,
            key="edge-gateway-compose-smoke",
            indent=2,
        )
        steps = _step_blocks(job)

        self.assertRegex(
            job,
            r"(?m)^ {4}name: edge-gateway-compose-smoke$",
        )
        self.assertRegex(job, r"(?m)^ {4}runs-on: ubuntu-latest$")
        self.assertRegex(job, r"(?m)^ {4}timeout-minutes: 25$")
        self.assertTrue(steps, "compose-smoke job must contain named steps")
        self.assertIn(
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
            job,
        )
        self.assertIn('python-version: "3.13.14"', job)

        compose_step = _single_step_with(steps, "up --build -d")
        compose_command = _one_line(compose_step)
        self.assertIn("working-directory: simulation", compose_step)
        self.assertIn("deploy/edge-gateway-v0/compose.yaml", compose_step)
        self.assertRegex(compose_command, r"docker compose .*\bconfig\b")
        self.assertRegex(
            compose_command,
            r"docker compose .*\bup --build -d\b",
        )
        self.assertRegex(
            compose_command,
            r"timeout .*\b10m docker compose .*\bup --build -d\b",
        )

        self.assertIn("publisher", compose_step)
        self.assertRegex(compose_command, r"\bwait\b.*\bpublisher\b")
        self.assertRegex(
            compose_command,
            r"timeout .*\b120s docker compose .*\bwait\b.*\bpublisher\b",
        )
        self.assertRegex(
            compose_command,
            r"\bps --all --quiet publisher\b",
        )
        self.assertIn(".State.ExitCode", compose_step)
        self.assertIn('test "$publisher_exit" -eq 0', compose_step)

        endpoint_step = _single_step_with(steps, "/healthz")
        self.assertIn("working-directory: simulation", endpoint_step)
        for endpoint in ("/healthz", "/readyz", "/api/v0/status"):
            self.assertIn(endpoint, endpoint_step)
        self.assertRegex(endpoint_step, r"json\.loads?\(")
        self.assertRegex(endpoint_step, r"\b(?:for|while)\b")
        self.assertIn("sleep", endpoint_step)
        self.assertIn("assert", endpoint_step)
        for field in (
            "schema",
            "mode",
            "site_id",
            "deployment_id",
            "broker_connected",
            "sensor_seen",
            "adapter_healthy",
            "runtime_ready",
            "ready",
            "current",
            "last_failure",
            "disclaimer",
        ):
            self.assertIn(field, endpoint_step)
        self.assertIn("nxt-edge-gateway/health/v0", endpoint_step)
        self.assertIn("nxt-edge-gateway/status/v0", endpoint_step)
        self.assertIn("HYBRID_RUNTIME_REHEARSAL", endpoint_step)
        self.assertIn("pilot-course-a", endpoint_step)
        self.assertIn("pilot-a-edge-v0", endpoint_step)
        self.assertIn("NOT LIVE CUSTOMER DATA", endpoint_step)
        self.assertIn("SIMULATION", endpoint_step)

        accepted_step = _single_step_with(steps, "message_result")
        accepted_command = _one_line(accepted_step)
        self.assertRegex(
            accepted_command,
            r"docker compose .*\blogs\b.*\bgateway\b",
        )
        self.assertIn("--no-log-prefix", accepted_step)
        self.assertRegex(accepted_step, r"json\.loads?\(")
        self.assertIn("assert", accepted_step)
        self.assertIn("accepted", accepted_step)
        for evidence in (
            "complete_facility_state",
            "runtime_outcome",
            "acknowledged",
            "source_type",
            "sensor",
            "simulation",
        ):
            self.assertIn(evidence, accepted_step)
        self.assertRegex(accepted_step, r"len\(live\)\s*==\s*1")
        self.assertRegex(accepted_step, r"len\(simulated\)\s*==\s*29")
        self.assertRegex(accepted_step, r"len\(observations\)\s*==\s*30")
        self.assertIn(
            "len(live) + len(simulated) == len(observations)", accepted_step
        )

        capture_steps = [
            step
            for step in steps
            if "if: always()" in step
            and re.search(
                r"\bdocker compose\b.*\bps\b.*(?:-a|--all)", _one_line(step)
            )
            and re.search(r"\bdocker compose\b.*\blogs\b.*--no-color", _one_line(step))
        ]
        self.assertEqual(
            len(capture_steps),
            1,
            "one always-run step must capture Compose ps -a/--all and logs",
        )
        capture_command = _one_line(capture_steps[0])
        self.assertRegex(capture_command, r"timeout 30s docker compose .*\bps\b")
        self.assertRegex(capture_command, r"timeout 60s docker compose .*\blogs\b")

        teardown_steps = [
            step
            for step in steps
            if "if: always()" in step
            and "down --volumes --remove-orphans" in step
        ]
        self.assertEqual(
            len(teardown_steps),
            1,
            "one always-run step must tear down volumes and orphans",
        )
        self.assertRegex(
            _one_line(teardown_steps[0]),
            r"timeout .*\b60s docker compose .*down --volumes --remove-orphans",
        )

        integrity_step = _single_step_with(steps, "git diff --exit-code HEAD --")
        self.assertIn("git ls-files --others --exclude-standard", integrity_step)


if __name__ == "__main__":
    unittest.main()
