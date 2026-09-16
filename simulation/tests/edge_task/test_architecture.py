"""Mechanical dependency, transport, and safety guards for ``nxt_edge_task``."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = SIMULATION_ROOT / "nxt_edge_task"

# Whitelist, not blacklist: every stdlib import root the package may use.
# ``os``/``fcntl`` exist only for the journal's fsync and advisory lock.
ALLOWED_STDLIB_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "fcntl",
    "hashlib",
    "json",
    "math",
    "os",
    "pathlib",
    "re",
    "types",
    "typing",
}

# The package imports no first-party package at all.
ALLOWED_FIRST_PARTY_MODULES: set[str] = set()

BANNED_CALL_NAMES = {
    "now",
    "utcnow",
    "today",
    "time",
    "sleep",
    "monotonic",
    "perf_counter",
    "uuid1",
    "uuid4",
    "random",
    "randint",
    "getenv",
    "environ",
    "system",
    "popen",
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
    "reset_estop",
    "clear_estop",
    "resume_robot",
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

LIVE_SWITCH_LITERALS = ('"LIVE"', '"PRODUCTION"', "'LIVE'", "'PRODUCTION'", "REAL_HARDWARE", "real_hardware")

# Canonical contracts this package must consume, never redefine.
FORBIDDEN_CLASS_DEFINITIONS = {
    "Observation",
    "ObservationFrame",
    "ObservationStatus",
    "SourceType",
    "SiteConfig",
    "RobotStatusSample",
    "RobotStatusAdapter",
    "FacilityState",
    "RobotStateSnapshot",
    "AssemblyReport",
    "FacilitySnapshotEnvelope",
    "OperationalSnapshot",
    "PolicyEvaluation",
    "Recommendation",
    "DecisionTrace",
    "RecommendationCase",
    "CaseStatus",
    "ExecutionRequest",
    "ExecutionAcknowledgement",
    "CommissionedSite",
    "RobotAsset",
}

# Advisory rule ids that live in nxt_facility.decisions and nxt_pilot_ops only.
FORBIDDEN_RULE_TOKENS = ("robot_down", "assist_backlog", "battery_reserve", "rule_id", "OPERATOR_INTERVENTION", "DISPATCH_COLLECTOR")

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


def _package_files() -> list[Path]:
    files = [p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts]
    assert files, "nxt_edge_task sources not found"
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


def test_package_imports_only_the_stdlib_whitelist() -> None:
    for path in _package_files():
        for module in _imports_of(path):
            root = module.split(".")[0]
            if root.startswith("nxt_"):
                assert module in ALLOWED_FIRST_PARTY_MODULES, f"{path.name} imports first-party {module}"
            else:
                assert root in ALLOWED_STDLIB_ROOTS, f"{path.name} imports {module} outside the whitelist"


def test_no_wall_clock_uuid_random_or_environment_calls() -> None:
    for path in _package_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = node.func
                name = callee.attr if isinstance(callee, ast.Attribute) else (callee.id if isinstance(callee, ast.Name) else None)
                assert name not in BANNED_CALL_NAMES, f"{path.name} calls banned function {name}"
            if isinstance(node, ast.Attribute):
                assert node.attr != "environ", f"{path.name} reads os.environ"
            if isinstance(node, ast.Name):
                assert node.id != "environ", f"{path.name} references environ"


PATH_LITERALS = ("reports/", "robot_task_journal", "edge_task_journal", "notification-receiver", ".edge.lock")


def test_package_names_no_role_directory_or_journal_path() -> None:
    """The package is path-agnostic: only composition roots know where journals live."""

    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for literal in PATH_LITERALS:
            assert literal not in text, f"{path.name} names the filesystem path fragment {literal!r}"


def test_no_execution_llm_or_live_switch_tokens() -> None:
    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for token in EXECUTION_TOKENS + LIVE_SWITCH_LITERALS:
            assert token not in text, f"{path.name} mentions {token!r}"
        lowered = text.lower()
        for pattern in LLM_PATTERNS:
            assert re.search(pattern, lowered) is None, f"{path.name} matches {pattern}"


def test_no_canonical_contract_redefinition_or_advisory_rule_tokens() -> None:
    for path in _package_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name not in FORBIDDEN_CLASS_DEFINITIONS, f"{path.name} redefines {node.name}"
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_RULE_TOKENS:
            assert token not in text, f"{path.name} mentions advisory token {token!r}"


def test_no_other_package_mentions_edge_task() -> None:
    for package in OTHER_PACKAGES:
        for path in (SIMULATION_ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            assert "nxt_edge_task" not in path.read_text(encoding="utf-8"), f"{package}/{path.name} mentions nxt_edge_task"


def test_package_never_mentions_other_first_party_packages() -> None:
    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for package in OTHER_PACKAGES:
            assert package not in text, f"{path.name} mentions {package}"


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
        import nxt_edge_task
        from nxt_edge_task import cases, executor
        print("imported", nxt_edge_task.TaskRequest.__name__, cases.EdgeView.__name__, executor.RobotCore.__name__)
        """
    )
    return subprocess.run([sys.executable, "-c", probe], cwd=SIMULATION_ROOT, capture_output=True, text=True)


def test_contract_surface_imports_without_any_other_stack() -> None:
    result = _import_probe(
        (
            "paho", "socket", "ssl", "http", "urllib", "threading", "subprocess", "time", "uuid", "random",
            "simpy", "gymnasium", "numpy", "pandas", "pyarrow", "pxr", "rclpy", "rospy", "serial", "pymodbus", "can",
        )
        + OTHER_PACKAGES
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_import_blocker_negative_control() -> None:
    result = _import_probe(("json",))
    assert result.returncode != 0
    assert "blocked import" in result.stderr


def test_package_is_registered_as_a_distribution_package() -> None:
    pyproject = (SIMULATION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"nxt_edge_task"' in pyproject


def test_environment_kind_vocabulary_has_one_member() -> None:
    from nxt_edge_task.contracts import ENVIRONMENT_KIND_SIMULATION, _environment  # noqa: PLC2701

    assert ENVIRONMENT_KIND_SIMULATION == "SIMULATION"
    source = (PACKAGE_ROOT / "contracts.py").read_text(encoding="utf-8")
    assert source.count("ENVIRONMENT_KIND_SIMULATION = ") == 1
    assert "ENVIRONMENT_KIND_LIVE" not in source
    del _environment
