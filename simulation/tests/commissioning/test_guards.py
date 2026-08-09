"""The commissioning source of truth is independent of runtime layers."""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = SIMULATION_ROOT / "nxt_commissioning"
FORBIDDEN = {
    "nxt_range_ops",
    "nxt_facility",
    "nxt_pilot_ops",
    "nxt_range_twin",
}
PUBLIC_MODULES = (
    "nxt_commissioning",
    "nxt_commissioning.contracts",
    "nxt_commissioning.provenance",
    "nxt_commissioning.spatial",
    "nxt_commissioning.equipment",
    "nxt_commissioning.sensors",
    "nxt_commissioning.validation",
    "nxt_commissioning.projections",
    "nxt_commissioning.store",
)


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _import_with_blocked(module: str, blocked: set[str]):
    probe = textwrap.dedent(
        f"""
        import importlib.abc
        import sys

        class Blocker(importlib.abc.MetaPathFinder):
            BLOCKED = {blocked!r}

            def find_spec(self, fullname, path=None, target=None):
                if fullname.split('.')[0] in self.BLOCKED:
                    raise ImportError('blocked by commissioning guard: ' + fullname)
                return None

        sys.meta_path.insert(0, Blocker())
        import {module}
        print('commissioning-guard-ok')
        """
    )
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=SIMULATION_ROOT,
        capture_output=True,
        text=True,
    )


def test_commissioning_source_has_no_forbidden_imports():
    for path in PACKAGE_ROOT.rglob("*.py"):
        forbidden = _import_roots(path) & FORBIDDEN
        assert not forbidden, f"{path.name} imports forbidden runtime layer(s): {forbidden}"


@pytest.mark.parametrize("module", PUBLIC_MODULES)
def test_public_modules_import_with_runtime_layers_blocked(module):
    result = _import_with_blocked(module, FORBIDDEN)
    assert result.returncode == 0, result.stderr
    assert "commissioning-guard-ok" in result.stdout


def test_import_blocker_has_a_negative_control():
    result = _import_with_blocked("json", {"json"})
    assert result.returncode != 0
    assert "blocked by commissioning guard: json" in result.stderr


def test_existing_runtime_packages_do_not_import_commissioning():
    upstream_packages = (
        "nxt_range_ops",
        "nxt_facility",
        "nxt_range_twin",
    )
    for package in upstream_packages:
        for path in (SIMULATION_ROOT / package).rglob("*.py"):
            assert "nxt_commissioning" not in _import_roots(path), path
