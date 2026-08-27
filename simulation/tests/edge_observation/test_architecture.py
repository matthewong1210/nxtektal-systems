"""Mechanical dependency, transport, and safety guards for the adapter kit."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = SIMULATION_ROOT / "nxt_edge_observation"

# The kit converts raw payloads into the canonical Observation contract and
# nothing else, so exactly one first-party module is reachable.
ALLOWED_FIRST_PARTY_MODULES = {"nxt_telemetry.observations"}

BANNED_IMPORT_ROOTS = {
    # every other first-party package, upstream and downstream
    "nxt_sim",
    "nxt_range_ops",
    "nxt_range_agent",
    "nxt_range_twin",
    "nxt_range_viewer",
    "nxt_range_demo",
    "nxt_facility",
    "nxt_memory",
    "nxt_commissioning",
    "nxt_pilot_ops",
    "nxt_site_runtime",
    "nxt_agent_runtime",
    # simulation / USD / robotics stacks
    "simpy",
    "gymnasium",
    "numpy",
    "pandas",
    "pyarrow",
    "pxr",
    "rclpy",
    "rospy",
    # physical transports and field-bus clients: V0 connects to nothing
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
    "can",
    "bleak",
    "usb",
    # network surfaces
    "socket",
    "ssl",
    "http",
    "urllib",
    "ftplib",
    "smtplib",
    "xmlrpc",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    # process and concurrency surfaces
    "subprocess",
    "multiprocessing",
    "threading",
    "asyncio",
    "selectors",
    "signal",
    # nondeterminism
    "time",
    "datetime",
    "uuid",
    "random",
    "secrets",
    "os",
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

# Word-boundary patterns: a bare substring such as "llm" also matches
# ordinary identifiers like ``fullmatch``.
LLM_PATTERNS = (
    r"\bopenai\b",
    r"\banthropic\b",
    r"\bllm\b",
    r"\blangchain\b",
    r"\bprompt\b",
    r"\bcompletion\b",
    r"\bgenerative\b",
)

# Canonical contracts this package must consume, never redefine.
FORBIDDEN_CLASS_DEFINITIONS = {
    "Observation",
    "ObservationFrame",
    "ObservationStatus",
    "SourceType",
    "SiteConfig",
    "UpstreamInputs",
    "SequencedObservationFrame",
    "FacilityState",
    "AssemblyReport",
    "FacilitySnapshotEnvelope",
    "RuntimeQuality",
    "OperationalSnapshot",
    "PolicyEvaluation",
    "Recommendation",
    "DecisionTrace",
}

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
    "nxt_workflow_enablement",
    # The Site Agent service shell receives adapter diagnostics as plain
    # data from composition roots and must not import the adapter kit.
    "nxt_site_agent",
)


def _package_files() -> list[Path]:
    files = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert files, "nxt_edge_observation sources not found"
    return files


def _code_text(path: Path) -> str:
    """Return executable source with docstrings and comments removed.

    Documentation is allowed -- and required -- to *name* the transports,
    robot interfaces, and safety systems this package deliberately does
    not touch.  The token guards below must therefore inspect real code,
    not prose that declares an absence.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def test_code_text_strips_prose_but_keeps_code():
    """Negative control for the docstring/comment stripper itself."""
    probe = PACKAGE_ROOT / "feed.py"
    raw = probe.read_text(encoding="utf-8")
    code = _code_text(probe)
    assert "at-least-once" in raw
    assert "at-least-once" not in code
    assert "class FixtureRawSampleFeed" in code


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


def test_adapters_import_only_the_canonical_observation_contract():
    for path in _package_files():
        for module in _imports_of(path):
            root = module.split(".")[0]
            assert root not in BANNED_IMPORT_ROOTS, (
                f"{path.name} imports banned module {module}"
            )
            if root.startswith("nxt_"):
                assert module in ALLOWED_FIRST_PARTY_MODULES, (
                    f"{path.name} imports unapproved first-party module {module}"
                )


def test_adapters_have_no_execution_foreign_or_llm_surface():
    for path in _package_files():
        code = _code_text(path)
        for token in EXECUTION_TOKENS + FOREIGN_SURFACE_TOKENS:
            assert token not in code, f"{path.name} mentions {token!r}"
        lowered = code.lower()
        for pattern in LLM_PATTERNS:
            assert re.search(pattern, lowered) is None, (
                f"{path.name} mentions {pattern!r}"
            )


def test_adapters_redefine_no_canonical_contract():
    defined: set[str] = set()
    for path in _package_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined.update(
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        )
    collisions = defined & FORBIDDEN_CLASS_DEFINITIONS
    assert collisions == set(), collisions


def test_no_wall_clock_uuid_or_randomness_in_the_adapter_package():
    banned_calls = {
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
    for path in _package_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = None
            if isinstance(callee, ast.Attribute):
                name = callee.attr
            elif isinstance(callee, ast.Name):
                name = callee.id
            assert name not in banned_calls, (
                f"{path.name} calls banned nondeterministic function {name}"
            )


def test_no_existing_package_depends_on_the_adapter_kit():
    offenders = []
    for package in OTHER_PACKAGES:
        package_root = SIMULATION_ROOT / package
        if not package_root.is_dir():
            continue
        for path in package_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "nxt_edge_observation" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(SIMULATION_ROOT)))
    assert offenders == []


def test_required_existing_packages_are_present():
    for package in ("nxt_telemetry", "nxt_commissioning", "nxt_site_runtime"):
        assert (SIMULATION_ROOT / package).is_dir(), package


def _import_probe(blocked_roots: tuple[str, ...]) -> subprocess.CompletedProcess:
    probe = textwrap.dedent(
        f"""
        import importlib.abc
        import sys

        BLOCKED = {blocked_roots!r}

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in BLOCKED:
                    raise ImportError(f"blocked import: {{fullname}}")
                return None

        sys.meta_path.insert(0, Blocker())

        import nxt_edge_observation

        surface = (
            nxt_edge_observation.EdgeObservationAdapterKit,
            nxt_edge_observation.LoadCellAdapter,
            nxt_edge_observation.DigitalIOAdapter,
            nxt_edge_observation.RobotStatusAdapter,
            nxt_edge_observation.AdapterBindingSet,
            nxt_edge_observation.FixtureRawSampleFeed,
            nxt_edge_observation.EdgeAdapterReport,
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


def test_the_kit_imports_without_any_runtime_simulation_or_transport_stack():
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
            "paho",
            "asyncua",
            "kafka",
            "socket",
            "nxt_sim",
            "nxt_range_ops",
            "nxt_range_twin",
            "nxt_range_viewer",
            "nxt_range_demo",
            "nxt_range_agent",
            "nxt_facility",
            "nxt_memory",
            "nxt_commissioning",
            "nxt_pilot_ops",
            "nxt_site_runtime",
            "nxt_agent_runtime",
        )
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_import_blocker_negative_control():
    result = _import_probe(("nxt_telemetry",))
    assert result.returncode != 0
    assert "blocked import" in result.stderr


def test_the_kit_is_registered_as_a_distribution_package():
    pyproject = (SIMULATION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"nxt_edge_observation"' in pyproject


def test_the_package_documents_its_absent_physical_boundary():
    text = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    for claim in ("Modbus", "MQTT", "ROS 2", "emergency stop", "fixture-backed"):
        assert claim in text, claim
