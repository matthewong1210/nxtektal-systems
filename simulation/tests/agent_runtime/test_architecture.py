"""Mechanical dependency and safety guards for ``nxt_agent_runtime``."""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = SIMULATION_ROOT / "nxt_agent_runtime"

# The runtime composes existing downstream public surfaces only.
ALLOWED_FIRST_PARTY_MODULES = {
    "nxt_pilot_ops.adapters",
    "nxt_pilot_ops.contracts",
    "nxt_pilot_ops.guardian",
    "nxt_pilot_ops.ledger",
    "nxt_pilot_ops.serialization",
    "nxt_pilot_ops.workflow",
    "nxt_site_runtime",
    "nxt_site_runtime.checkpoints",
    "nxt_site_runtime.envelope",
    "nxt_site_runtime.pipeline",
    "nxt_site_runtime.ports",
    "nxt_telemetry.observations",
}

BANNED_IMPORT_ROOTS = {
    # simulator, execution, and upstream packages
    "nxt_sim",
    "nxt_range_ops",
    "nxt_range_agent",
    "nxt_range_twin",
    "nxt_range_viewer",
    "nxt_range_demo",
    "nxt_facility",
    "nxt_memory",
    "nxt_commissioning",
    # simulation / USD / robotics stacks
    "simpy",
    "gymnasium",
    "numpy",
    "pandas",
    "pyarrow",
    "pxr",
    "rclpy",
    "rospy",
    # network surfaces: the runtime makes no network call
    "socket",
    "ssl",
    "http",
    "urllib",
    "ftplib",
    "smtplib",
    "xmlrpc",
    "requests",
    # nondeterminism
    "time",
    "uuid",
    "random",
    "secrets",
}

EXECUTION_TOKENS = (
    "apply_directive(",
    "send_robot_command",
    "dispatch_robot_command",
    "enqueue_robot_command",
    "actuator_command",
    "motion_plan",
    "emergency_stop(",
    "return_to_charge(",
    "RobotTaskInterface",
    "HandoffController",
    "nav2",
)

FOREIGN_SURFACE_TOKENS = (
    "operational-replay",
    "operational_replay",
    "roi-engine",
    "roi_engine",
    "nxtektal-roi",
)

OTHER_PACKAGES = (
    "nxt_sim",
    "nxt_range_ops",
    "nxt_range_agent",
    "nxt_facility",
    "nxt_memory",
    "nxt_telemetry",
    "nxt_range_twin",
    "nxt_range_viewer",
    "nxt_range_demo",
    "nxt_pilot_ops",
    "nxt_commissioning",
    "nxt_site_runtime",
    "nxt_edge_observation",
)


def _package_files() -> list[Path]:
    files = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert files, "nxt_agent_runtime sources not found"
    return files


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # package-relative imports stay inside the package
            if node.module is not None:
                modules.add(node.module)
    return modules


def test_runtime_imports_only_approved_downstream_surfaces():
    for path in _package_files():
        for module in _imports_of(path):
            root = module.split(".")[0]
            assert root not in BANNED_IMPORT_ROOTS, (
                f"{path.name} imports banned module {module}"
            )
            if root.startswith("nxt_"):
                assert module in ALLOWED_FIRST_PARTY_MODULES, (
                    f"{path.name} imports unapproved first-party "
                    f"module {module}"
                )


def test_runtime_has_no_execution_or_foreign_surface_tokens():
    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for token in EXECUTION_TOKENS + FOREIGN_SURFACE_TOKENS:
            assert token not in text, f"{path.name} mentions {token!r}"


def test_no_wall_clock_or_uuid_calls_in_runtime_package():
    banned_calls = {"now", "utcnow", "today", "uuid1", "uuid4"}
    for path in _package_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = node.func
                name = None
                if isinstance(callee, ast.Attribute):
                    name = callee.attr
                elif isinstance(callee, ast.Name):
                    name = callee.id
                assert name not in banned_calls, (
                    f"{path.name} calls banned wall-clock/uuid "
                    f"function {name}"
                )


def test_upstream_and_existing_packages_never_mention_agent_runtime():
    for package in OTHER_PACKAGES:
        for path in (SIMULATION_ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            assert "nxt_agent_runtime" not in text, (
                f"{package}/{path.name} must not depend on nxt_agent_runtime"
            )


def _import_probe(blocked_roots: tuple[str, ...]) -> subprocess.CompletedProcess:
    probe = textwrap.dedent(
        f"""
        import importlib.abc
        import importlib.machinery
        import sys

        BLOCKED = {blocked_roots!r}

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                root = fullname.split(".")[0]
                if root in BLOCKED:
                    raise ImportError(f"blocked import: {{fullname}}")
                return None

        sys.meta_path.insert(0, Blocker())

        import nxt_agent_runtime

        surface = (
            nxt_agent_runtime.AgentRuntime,
            nxt_agent_runtime.EvaluationCheckpoint,
            nxt_agent_runtime.EvaluationJournal,
            nxt_agent_runtime.EvaluationRecord,
            nxt_agent_runtime.JsonlSnapshotPublisher,
            nxt_agent_runtime.ManagerDecisionQueue,
            nxt_agent_runtime.RuntimeStatus,
        )
        print("imported", len(surface))
        """
    )
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=SIMULATION_ROOT,
        capture_output=True,
        text=True,
    )


def test_contract_surface_imports_without_simulation_or_robot_stack():
    result = _import_probe(
        (
            "simpy",
            "gymnasium",
            "numpy",
            "pandas",
            "pyarrow",
            "pxr",
            "rclpy",
            "rospy",
            "nxt_sim",
            "nxt_range_ops",
            "nxt_range_twin",
            "nxt_range_viewer",
            "nxt_range_demo",
            "nxt_range_agent",
            "nxt_memory",
            "nxt_commissioning",
        )
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_import_blocker_negative_control():
    result = _import_probe(("nxt_pilot_ops",))
    assert result.returncode != 0
    assert "blocked import" in result.stderr


def test_runtime_is_registered_as_a_distribution_package():
    pyproject = (SIMULATION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "nxt_agent_runtime" in pyproject
