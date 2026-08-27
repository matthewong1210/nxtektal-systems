"""Mechanical dependency, transport, and safety guards for workflow enablement."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = SIMULATION_ROOT / "nxt_workflow_enablement"

# The enablement layer consumes validated commissioned truth and nothing
# else first-party: adapter and runtime facts arrive as declared plain
# data from composition roots.
ALLOWED_FIRST_PARTY_ROOTS = {"nxt_commissioning"}

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
    "nxt_telemetry",
    "nxt_pilot_ops",
    "nxt_site_runtime",
    "nxt_agent_runtime",
    "nxt_edge_observation",
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
    # process, concurrency, and filesystem surfaces: evaluation is pure
    "subprocess",
    "multiprocessing",
    "threading",
    "asyncio",
    "selectors",
    "signal",
    "os",
    "pathlib",
    "io",
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

# Canonical contracts this package must reference, never redefine.
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
    "CommissionedSite",
    "SensorBinding",
    "CalibrationInfo",
    "AdapterBindingSet",
    "EdgeAdapterReport",
    "EvaluationRecord",
    "EvaluationCheckpoint",
    "RuntimeCheckpoint",
    "AgentRuntime",
    "RangeSimulation",
}

# nxt_site_agent is deliberately absent: the Site Agent service shell is
# a designated consumer that verifies enablement reports and launch plans
# through this package's public surface (see AGENTS.md).
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
)

# The exact runtime-bearing package names this package's *source* may
# never mention, even in prose: their reverse guards scan raw text.
RUNTIME_PACKAGE_LITERALS = (
    "nxt_site_runtime",
    "nxt_agent_runtime",
    "nxt_edge_observation",
)


def _package_files() -> list[Path]:
    files = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert files, "nxt_workflow_enablement sources not found"
    return files


def _code_text(path: Path) -> str:
    """Return executable source with docstrings removed.

    Documentation is allowed -- and required -- to name the transports
    and safety systems this package deliberately does not touch; the
    token guards below must inspect real code, not prose declaring an
    absence.
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
    """Negative control for the docstring stripper itself."""
    probe = PACKAGE_ROOT / "launch.py"
    raw = probe.read_text(encoding="utf-8")
    code = _code_text(probe)
    assert "not an unforgeable capability" in raw
    assert "not an unforgeable capability" not in code
    assert "class RangeOpsLaunchPlan" in code


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


def test_the_package_imports_only_commissioned_truth_first_party():
    for path in _package_files():
        for module in _imports_of(path):
            root = module.split(".")[0]
            assert root not in BANNED_IMPORT_ROOTS, (
                f"{path.name} imports banned module {module}"
            )
            if root.startswith("nxt_"):
                assert root in ALLOWED_FIRST_PARTY_ROOTS, (
                    f"{path.name} imports unapproved first-party module "
                    f"{module}"
                )


def test_the_package_never_mentions_a_runtime_package_by_name():
    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for literal in RUNTIME_PACKAGE_LITERALS:
            assert literal not in text, (
                f"{path.name} mentions {literal!r}; the enablement layer "
                "consumes runtime facts as declared data only"
            )


def test_the_package_has_no_execution_foreign_or_llm_surface():
    for path in _package_files():
        code = _code_text(path)
        for token in EXECUTION_TOKENS + FOREIGN_SURFACE_TOKENS:
            assert token not in code, f"{path.name} mentions {token!r}"
        lowered = code.lower()
        for pattern in LLM_PATTERNS:
            assert re.search(pattern, lowered) is None, (
                f"{path.name} matches {pattern!r}"
            )


def test_the_package_redefines_no_canonical_contract():
    defined: set[str] = set()
    for path in _package_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined.update(
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        )
    collisions = defined & FORBIDDEN_CLASS_DEFINITIONS
    assert collisions == set(), collisions


def test_no_wall_clock_uuid_or_randomness_in_the_package():
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


def test_no_existing_package_depends_on_workflow_enablement():
    offenders = []
    for package in OTHER_PACKAGES:
        package_root = SIMULATION_ROOT / package
        if not package_root.is_dir():
            continue
        for path in package_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "nxt_workflow_enablement" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(SIMULATION_ROOT)))
    assert offenders == []


def test_required_existing_packages_are_present():
    assert (SIMULATION_ROOT / "nxt_commissioning").is_dir()


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

        import nxt_workflow_enablement

        surface = (
            nxt_workflow_enablement.WorkflowRegistry,
            nxt_workflow_enablement.pilot_workflow_registry,
            nxt_workflow_enablement.evaluate_pilot_site,
            nxt_workflow_enablement.EnablementReport,
            nxt_workflow_enablement.plan_range_ops_launch,
            nxt_workflow_enablement.RangeOpsLaunchPlan,
            nxt_workflow_enablement.verify_report_payload,
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


def test_the_package_imports_without_any_runtime_simulation_or_transport_stack():
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
            "nxt_telemetry",
            "nxt_pilot_ops",
            "nxt_site_runtime",
            "nxt_agent_runtime",
            "nxt_edge_observation",
        )
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_import_blocker_negative_control():
    result = _import_probe(("nxt_commissioning",))
    assert result.returncode != 0
    assert "blocked import" in result.stderr


def test_the_package_is_registered_as_a_distribution_package():
    pyproject = (SIMULATION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"nxt_workflow_enablement"' in pyproject


def test_the_package_documents_its_absent_physical_boundary():
    text = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    for claim in ("Modbus", "MQTT", "ROS 2", "emergency stop", "fixture-backed"):
        assert claim in text, claim


def test_physical_execution_reachability_is_gated_false():
    """A context claiming reachability must fail the shared gate."""
    sys.path.insert(0, str(SIMULATION_ROOT))
    try:
        from nxt_workflow_enablement import (
            EnablementContext,
            OutputLocationPlan,
            SharedSiteExpectation,
            SharedSiteVerdict,
            TransportMode,
            evaluate_shared_site,
        )
        from scripts.pilot_course_a_enablement_fixture import (
            DEPLOYMENT_ID,
            SITE_ID,
            enablement_manifest_payload,
        )
    finally:
        sys.path.remove(str(SIMULATION_ROOT))
    context = EnablementContext(
        scenario_name="probe",
        scenario_t_s=0.0,
        transport_mode=TransportMode.FIXTURE_ONLY.value,
        physical_execution_reachable=True,
        output_locations=OutputLocationPlan(
            relative_paths=("probe.jsonl",), root_is_empty=True
        ),
    )
    _, result = evaluate_shared_site(
        enablement_manifest_payload(),
        expectation=SharedSiteExpectation(
            site_id=SITE_ID, deployment_id=DEPLOYMENT_ID
        ),
        context=context,
    )
    assert result.verdict is SharedSiteVerdict.INVALID
