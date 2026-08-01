"""Architectural constraint: task logic is simulator-independent.

controllers/, interfaces/, and metrics/ must not import from adapters/, and
the mock adapter must not import task logic. Enforced by parsing import
statements so a violation fails CI immediately.
"""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "nxt_sim"


def _imports_of(path: Path) -> set[str]:
    """All module paths a file imports, including `from pkg import name`
    (as pkg.name) and relative imports resolved against the file's package —
    so `from nxt_sim import adapters` and `from .. import adapters` are
    caught, not just `import nxt_sim.adapters`."""
    rel = path.relative_to(PKG.parent)
    package_parts = list(rel.parts[:-1])  # e.g. ['nxt_sim', 'controllers']
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                anchor = package_parts[: len(package_parts) - (node.level - 1)]
                base = ".".join(anchor + ([node.module] if node.module else []))
            if base:
                modules.add(base)
            for alias in node.names:
                modules.add(f"{base}.{alias.name}" if base else alias.name)
    return modules


def _assert_no_imports(dir_name: str, forbidden: str) -> None:
    for path in (PKG / dir_name).rglob("*.py"):
        for module in _imports_of(path):
            assert forbidden not in module, (
                f"{path.relative_to(PKG.parent)} imports '{module}' — "
                f"'{dir_name}' must not depend on '{forbidden}'"
            )


def test_controllers_do_not_import_adapters():
    _assert_no_imports("controllers", "adapters")


def test_interfaces_do_not_import_adapters_or_controllers():
    _assert_no_imports("interfaces", "adapters")
    _assert_no_imports("interfaces", "controllers")


def test_metrics_do_not_import_adapters():
    _assert_no_imports("metrics", "adapters")


def test_mock_adapter_does_not_import_task_logic():
    _assert_no_imports("adapters", "controllers")
    _assert_no_imports("adapters", "scenarios")
