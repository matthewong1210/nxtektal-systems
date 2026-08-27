"""Mechanical deployment-composition, dependency, and safety guards."""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path


SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = SIMULATION_ROOT / "pyproject.toml"
LOCKFILE = SIMULATION_ROOT / "uv.lock"
GATEWAY_SCRIPT = SIMULATION_ROOT / "scripts" / "edge_gateway_live_input_v0.py"
PUBLISHER_SCRIPT = (
    SIMULATION_ROOT / "scripts" / "mock_edge_load_cell_publisher.py"
)
COMPOSITION_SCRIPTS = {GATEWAY_SCRIPT, PUBLISHER_SCRIPT}

SHIPPED_PACKAGES = {
    "nxt_sim",
    "nxt_range_ops",
    "nxt_facility",
    "nxt_memory",
    "nxt_telemetry",
    "nxt_range_twin",
    "nxt_pilot_ops",
    "nxt_commissioning",
    "nxt_site_runtime",
    "nxt_agent_runtime",
    "nxt_edge_observation",
    "nxt_workflow_enablement",
}

FORBIDDEN_CLASS_DEFINITIONS = {
    "Observation",
    "ObservationFrame",
    "ObservationStatus",
    "SourceType",
    "LoadCellSample",
    "RawSampleBatch",
    "EdgeAdapterReport",
    "FacilityState",
    "AssemblyReport",
    "FacilitySnapshotEnvelope",
    "OperationalSnapshot",
    "PolicyEvaluation",
    "Recommendation",
    "DecisionTrace",
    "StateStore",
    "DecisionEngine",
    "CommandSurface",
}

FORBIDDEN_IMPORT_ROOTS = {
    # Persistence and deferred integration surfaces.
    "sqlite3",
    "boto3",
    "botocore",
    "azure",
    "google.cloud",
    # Robotics, field buses, and execution stacks.
    "rclpy",
    "rospy",
    "nav2",
    "serial",
    "pymodbus",
    "minimalmodbus",
    # LLM/generative stacks.
    "openai",
    "anthropic",
    "langchain",
}

FORBIDDEN_EXECUTION_SYMBOLS = {
    "RobotTaskInterface",
    "HandoffController",
    "SafetyShield",
    "apply_directive",
    "send_robot_command",
    "dispatch_robot_command",
    "enqueue_robot_command",
    "actuator_command",
    "motion_plan",
    "emergency_stop",
    "reset_estop",
    "clear_estop",
    "return_to_charge",
    "write_register",
    "write_coil",
    "set_output",
    "ota_update",
    "cloud_sync",
    "llm",
}


def _toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _defined_classes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }


def _executable_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            symbols.add(node.attr)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.add(node.name)
    return symbols


def _paho_imports() -> list[Path]:
    offenders: list[Path] = []
    for path in SIMULATION_ROOT.rglob("*.py"):
        if (
            ".venv" in path.parts
            or "__pycache__" in path.parts
            or "tests" in path.parts
        ):
            continue
        if any(module.split(".")[0] == "paho" for module in _imports(path)):
            offenders.append(path)
    return offenders


def test_gateway_is_a_composition_root_not_a_new_shipped_package():
    project = _toml(PYPROJECT)
    packages = set(
        project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    )
    assert packages == SHIPPED_PACKAGES
    assert GATEWAY_SCRIPT.is_file()
    assert PUBLISHER_SCRIPT.is_file()
    assert "scripts" not in packages


def test_mqtt_is_one_small_optional_extra_and_not_a_core_or_dev_dependency():
    project = _toml(PYPROJECT)
    base_dependencies = project["project"]["dependencies"]
    dev_dependencies = project["dependency-groups"]["dev"]
    extras = project["project"]["optional-dependencies"]

    assert all("paho-mqtt" not in item.lower() for item in base_dependencies)
    assert all("paho-mqtt" not in item.lower() for item in dev_dependencies)
    assert "edge-gateway" in extras
    assert len(extras["edge-gateway"]) == 1
    assert extras["edge-gateway"][0].lower().startswith("paho-mqtt")
    assert all(
        "paho-mqtt" not in dependency.lower()
        for name, dependencies in extras.items()
        if name != "edge-gateway"
        for dependency in dependencies
    )


def test_intentional_lock_update_contains_the_optional_mqtt_extra():
    lock = _toml(LOCKFILE)
    packages = lock["package"]
    assert sum(item["name"] == "paho-mqtt" for item in packages) == 1

    nxt_sim = next(item for item in packages if item["name"] == "nxt-sim")
    optional = nxt_sim["optional-dependencies"]
    assert [item["name"] for item in optional["edge-gateway"]] == ["paho-mqtt"]
    requires_dist = nxt_sim["metadata"]["requires-dist"]
    mqtt_requirements = [
        item for item in requires_dist if item["name"] == "paho-mqtt"
    ]
    assert len(mqtt_requirements) == 1
    assert mqtt_requirements[0]["marker"] == "extra == 'edge-gateway'"
    assert "edge-gateway" in nxt_sim["metadata"]["provides-extras"]


def test_only_the_two_deployment_scripts_import_paho():
    assert set(_paho_imports()) == COMPOSITION_SCRIPTS


def test_gateway_defines_no_duplicate_domain_contract_store_or_engine():
    for path in COMPOSITION_SCRIPTS:
        collisions = _defined_classes(path) & FORBIDDEN_CLASS_DEFINITIONS
        assert collisions == set(), f"{path.name}: {sorted(collisions)}"
        assert not {
            name for name in _defined_classes(path) if name.endswith("StateStore")
        }


def test_gateway_has_no_execution_llm_sqlite_cloud_or_ota_surface():
    for path in COMPOSITION_SCRIPTS:
        imports = _imports(path)
        for module in imports:
            assert module not in FORBIDDEN_IMPORT_ROOTS
            assert module.split(".")[0] not in FORBIDDEN_IMPORT_ROOTS
        collisions = _executable_symbols(path) & FORBIDDEN_EXECUTION_SYMBOLS
        assert collisions == set(), f"{path.name}: {sorted(collisions)}"


def test_no_existing_package_imports_the_gateway_composition_root():
    offenders: list[str] = []
    for package_name in SHIPPED_PACKAGES:
        for path in (SIMULATION_ROOT / package_name).rglob("*.py"):
            imports = _imports(path)
            if any(
                module.startswith("scripts.edge_gateway_live_input_v0")
                or module.startswith("scripts.mock_edge_load_cell_publisher")
                for module in imports
            ):
                offenders.append(str(path.relative_to(SIMULATION_ROOT)))
    assert offenders == []


def _import_probe(source: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=SIMULATION_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_core_contracts_import_when_paho_is_mechanically_blocked():
    probe = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class BlockPaho(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] == "paho":
                    raise ImportError("blocked paho import: " + fullname)
                return None

        sys.meta_path.insert(0, BlockPaho())
        import nxt_commissioning
        import nxt_telemetry
        import nxt_edge_observation
        import nxt_site_runtime
        import nxt_agent_runtime
        print("core-imports-ok")
        """
    )
    result = _import_probe(probe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "core-imports-ok"


def test_paho_import_blocker_has_a_working_negative_control():
    probe = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class BlockPaho(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] == "paho":
                    raise ImportError("blocked paho import: " + fullname)
                return None

        sys.meta_path.insert(0, BlockPaho())
        import paho.mqtt.client
        """
    )
    result = _import_probe(probe)
    assert result.returncode != 0
    assert "blocked paho import" in result.stderr
