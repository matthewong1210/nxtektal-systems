"""Architectural guards for the downstream Shadow Ops package."""

from __future__ import annotations

import ast
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[2]
SHADOW_ROOT = SIM_ROOT / "nxt_pilot_ops"
UPSTREAM_PACKAGES = (
    "nxt_sim",
    "nxt_range_ops",
    "nxt_facility",
    "nxt_memory",
    "nxt_telemetry",
    "nxt_range_twin",
    "nxt_range_viewer",
    "nxt_range_agent",
    "nxt_range_demo",
    "nxt_workflow_enablement",
    "nxt_course_world_model",
    "nxt_edge_task",
    "nxt_edge_interventions",
)
CORE_BANNED_ROOTS = {
    *UPSTREAM_PACKAGES,
    "simpy",
    "gymnasium",
    "numpy",
    "pxr",
    "rclpy",
    "rospy",
}


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_upstream_packages_do_not_import_or_mention_shadow_ops() -> None:
    offenders: list[str] = []
    for package in UPSTREAM_PACKAGES:
        for path in (SIM_ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "nxt_pilot_ops" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(SIM_ROOT)))
    assert not offenders, f"upstream files reference nxt_pilot_ops: {offenders}"


def test_policy_core_has_no_upstream_sim_twin_or_control_imports() -> None:
    for path in SHADOW_ROOT.rglob("*.py"):
        if "adapters" in path.parts or "__pycache__" in path.parts:
            continue
        banned = _import_roots(path) & CORE_BANNED_ROOTS
        assert not banned, f"{path.relative_to(SIM_ROOT)} imports {sorted(banned)}"


def test_adapter_is_the_only_upstream_dependency_boundary() -> None:
    adapter_root = SHADOW_ROOT / "adapters"
    for path in adapter_root.rglob("*.py"):
        roots = _import_roots(path)
        assert not (
            roots
            & {
                "nxt_sim",
                "nxt_range_ops",
                "nxt_memory",
                "nxt_telemetry",
                "nxt_range_twin",
                "nxt_range_viewer",
                "nxt_range_agent",
                "nxt_range_demo",
                "pxr",
                "rclpy",
                "rospy",
            }
        ), path


def test_shadow_ops_has_no_robot_command_or_actuator_surface() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SHADOW_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    forbidden = (
        "apply_directive(",
        "send_robot_command",
        "dispatch_robot_command",
        "enqueue_robot_command",
        "actuator_command",
        "motion_plan",
        "emergency_stop(",
        "return_to_charge(",
    )
    for token in forbidden:
        assert token not in source


def test_ids_do_not_read_wall_clock_or_uuid() -> None:
    for path in SHADOW_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"now", "utcnow", "uuid4"}, path
            elif isinstance(node.func, ast.Name):
                assert node.func.id not in {"uuid1", "uuid4"}, path


_PLANNING_PURE_FILES = ("planning_contracts.py", "planning.py", "planning_workflow.py")
_PLANNING_STDLIB = {"__future__", "copy", "dataclasses", "datetime", "math", "re", "typing"}


def _planning_effects(source: str) -> list[str]:
    violations = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            violations.extend(alias.name for alias in node.names
                              if alias.name.split('.')[0] not in _PLANNING_STDLIB)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            if node.module.split('.')[0] not in _PLANNING_STDLIB:
                violations.append(node.module)
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else None)
            if name in {"open", "exec", "eval", "__import__", "now", "utcnow", "uuid4", "random"}:
                violations.append(name)
    return violations


def test_planning_core_remains_pure_without_storage_transport_or_execution():
    for name in _PLANNING_PURE_FILES:
        assert _planning_effects((SHADOW_ROOT / name).read_text()) == [], name


def test_planning_purity_guard_negative_control():
    assert set(_planning_effects("import os\nopen('/tmp/x', 'w')")) == {"os", "open"}
