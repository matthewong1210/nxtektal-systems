"""Mechanical dependency and safety guards for ``nxt_site_agent``."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = SIMULATION_ROOT / "nxt_site_agent"

# The service shell composes existing downstream public surfaces only.
ALLOWED_FIRST_PARTY_MODULES = {
    "nxt_agent_runtime",
    "nxt_pilot_ops.contracts",
    "nxt_pilot_ops.ledger",
    "nxt_pilot_ops.serialization",
    "nxt_workflow_enablement",
}

# Whitelist, not blacklist: every stdlib import root the package may
# use.  ``http``/``threading`` exist for the loopback-only server;
# everything else (os, time, uuid, socket, subprocess, urllib, ...)
# is banned by omission.
ALLOWED_STDLIB_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "http",
    "json",
    "math",
    "pathlib",
    "threading",
    "typing",
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
    "SafetyShield",
    "nav2",
    "Nav2",
    "write_register",
    "write_coil",
    "set_output",
)

FOREIGN_SURFACE_TOKENS = (
    "operational-replay",
    "operational_replay",
    "roi-engine",
    "roi_engine",
    "nxtektal-roi",
)

LLM_PATTERNS = (
    r"\bopenai\b",
    r"\banthropic\b",
    r"\bllm\b",
    r"\blangchain\b",
    r"\bprompt\b",
    r"\bcompletion\b",
    r"\bgenerative\b",
)

# The service may not mention any other first-party package: canonical
# semantics reach it only through its three approved import surfaces,
# and everything else arrives as plain data from composition roots.
BANNED_FIRST_PARTY_MENTIONS = (
    "nxt_sim",
    "nxt_range_ops",
    "nxt_range_agent",
    "nxt_facility",
    "nxt_memory",
    "nxt_telemetry",
    "nxt_range_twin",
    "nxt_range_viewer",
    "nxt_range_demo",
    "nxt_commissioning",
    "nxt_site_runtime",
    "nxt_edge_observation",
    # Course spatial truth is an independent sibling; the service shows
    # only Range Operations projections and never touches the map layer.
    "nxt_course_world_model",
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
    "nxt_agent_runtime",
    "nxt_edge_observation",
    "nxt_workflow_enablement",
    "nxt_course_world_model",
)

BANNED_CALL_NAMES = {
    "now",
    "utcnow",
    "today",
    "monotonic",
    "perf_counter",
    "uuid1",
    "uuid4",
    "random",
    "randint",
    "getenv",
}

SERVICE_SCRIPTS = (
    "scripts/site_agent_fixture.py",
    "scripts/site_agent_demo.py",
)

SCRIPT_BANNED_IMPORT_ROOTS = {
    "rclpy",
    "rospy",
    "serial",
    "pyserial",
    "pymodbus",
    "minimalmodbus",
    "paho",
    "asyncua",
    "opcua",
    "kafka",
    "confluent_kafka",
    "pika",
    "socket",
    "ssl",
    "urllib",
    "requests",
    "subprocess",
    "multiprocessing",
    "time",
    "uuid",
    "random",
    "secrets",
}


def _package_files() -> list[Path]:
    files = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert files, "nxt_site_agent sources not found"
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


def test_service_imports_only_the_approved_surfaces():
    for path in _package_files():
        for module in _imports_of(path):
            root = module.split(".")[0]
            if root.startswith("nxt_"):
                assert module in ALLOWED_FIRST_PARTY_MODULES, (
                    f"{path.name} imports unapproved first-party "
                    f"module {module}"
                )
            else:
                assert root in ALLOWED_STDLIB_ROOTS, (
                    f"{path.name} imports non-whitelisted module {module}"
                )


def test_service_has_no_execution_or_foreign_surface_tokens():
    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for token in EXECUTION_TOKENS + FOREIGN_SURFACE_TOKENS:
            assert token not in text, f"{path.name} mentions {token!r}"


def test_service_has_no_llm_or_generative_agent_surface():
    for path in _package_files():
        lowered = path.read_text(encoding="utf-8").lower()
        for pattern in LLM_PATTERNS:
            assert re.search(pattern, lowered) is None, (
                f"{path.name} matches banned pattern {pattern!r}"
            )


def test_service_never_mentions_other_first_party_packages():
    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for name in BANNED_FIRST_PARTY_MENTIONS:
            assert name not in text, f"{path.name} mentions {name!r}"


def test_no_wall_clock_uuid_or_randomness_calls_in_service_package():
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
                assert name not in BANNED_CALL_NAMES, (
                    f"{path.name} calls banned nondeterministic "
                    f"function {name}"
                )


def test_no_existing_package_depends_on_the_service_shell():
    offenders = []
    for package in OTHER_PACKAGES:
        package_root = SIMULATION_ROOT / package
        if not package_root.is_dir():
            continue
        for path in package_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "nxt_site_agent" in path.read_text(encoding="utf-8"):
                offenders.append(f"{package}/{path.name}")
    assert offenders == [], (
        "no existing package may depend on nxt_site_agent: "
        f"{offenders}"
    )


def test_service_scripts_import_no_transport_or_robot_stack():
    for relative in SERVICE_SCRIPTS:
        path = SIMULATION_ROOT / relative
        assert path.is_file(), f"{relative} is missing"
        for module in _imports_of(path):
            root = module.split(".")[0]
            assert root not in SCRIPT_BANNED_IMPORT_ROOTS, (
                f"{relative} imports banned module {module}"
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

        import nxt_site_agent

        surface = (
            nxt_site_agent.SiteAgentService,
            nxt_site_agent.SiteAgentApiServer,
            nxt_site_agent.ServiceStorage,
            nxt_site_agent.CompositionSeam,
            nxt_site_agent.SourceCursor,
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


def test_service_imports_without_simulation_or_robot_stack():
    # nxt_commissioning is deliberately absent from this blocklist: the
    # workflow-enablement surface the service verifies reports through
    # legitimately consumes commissioning's public contracts.
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
            "serial",
            "pymodbus",
            "nxt_sim",
            "nxt_range_ops",
            "nxt_range_twin",
            "nxt_range_viewer",
            "nxt_range_demo",
            "nxt_range_agent",
            "nxt_memory",
        )
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_import_blocker_negative_control():
    result = _import_probe(("nxt_agent_runtime",))
    assert result.returncode != 0
    assert "blocked import" in result.stderr


def test_service_is_registered_as_a_distribution_package():
    pyproject = (SIMULATION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "nxt_site_agent" in pyproject
