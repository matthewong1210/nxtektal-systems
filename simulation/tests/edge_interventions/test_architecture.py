"""Mechanical dependency and boundary guards for ``nxt_edge_interventions``."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = SIMULATION_ROOT / "nxt_edge_interventions"
SCRIPTS = SIMULATION_ROOT / "scripts"
COMPOSITION_ROOTS = {"edge_intervention_service_v0.py", "edge_notification_receiver_v0.py", "edge_intervention_cli.py"}

FORBIDDEN_MODULES = {
    "socket", "http", "urllib", "ssl", "asyncio", "subprocess", "threading", "multiprocessing", "os", "pathlib", "shutil",
    "time", "random", "secrets", "uuid", "tempfile", "signal", "paho", "requests", "importlib",
}
FORBIDDEN_TOKENS = ("apply_directive", "RobotTaskInterface", "HandoffController", "SafetyShield", "datetime.now(", "utcnow(")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_package_is_stdlib_only_and_imports_no_repository_package() -> None:
    files = sorted(PACKAGE_ROOT.glob("*.py"))
    assert files
    for path in files:
        imported = _imports(path)
        assert not {name for name in imported if name.startswith("nxt_")}, f"{path.name} imports a repository package"
        assert not (imported & FORBIDDEN_MODULES), f"{path.name} imports {sorted(imported & FORBIDDEN_MODULES)}"
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            assert token not in text, f"{path.name} mentions {token}"


def test_no_existing_package_imports_the_intervention_leaf() -> None:
    for package in sorted(p for p in SIMULATION_ROOT.iterdir() if p.is_dir() and p.name.startswith("nxt_") and p.name != "nxt_edge_interventions"):
        for path in package.rglob("*.py"):
            assert "nxt_edge_interventions" not in path.read_text(encoding="utf-8"), f"{package.name}/{path.name} mentions nxt_edge_interventions"


def test_only_the_three_composition_roots_import_the_leaf() -> None:
    importers = {path.name for path in SCRIPTS.glob("*.py") if "nxt_edge_interventions" in path.read_text(encoding="utf-8")}
    assert importers == COMPOSITION_ROOTS


def test_composition_roots_never_import_the_executor_or_forge_robot_evidence() -> None:
    for name in COMPOSITION_ROOTS:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "nxt_edge_task.executor" not in text and "RobotCore" not in text, name
        assert "ROBOT_RECORD_KINDS" not in text and "robot_task_journal" not in text, name
        assert "task_created" not in text and "create_task(" not in text, f"{name} must not create tasks"
        assert "TaskEvent(" not in text and "event_topic(" not in text and "request_topic(" not in text, f"{name} must not publish task traffic"
        for token in FORBIDDEN_TOKENS[:4]:
            assert token not in text, f"{name} mentions {token}"


def test_service_writes_only_its_own_journal() -> None:
    text = (SCRIPTS / "edge_intervention_service_v0.py").read_text(encoding="utf-8")
    assert "EDGE_RECORD_KINDS" in text and "edge_journal.read()" in text
    assert "edge_journal.append" not in text and ".append_via" in text
    assert text.count("JsonlJournal(") == 2  # the intervention journal and the read-only Edge journal


def test_package_imports_cleanly_in_a_bare_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); import nxt_edge_interventions as m; print(m.INTERVENTION_JOURNAL_SCHEMA)" % str(SIMULATION_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "nxt-edge-interventions/journal/v1"


def test_package_is_registered() -> None:
    pyproject = (SIMULATION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"nxt_edge_interventions"' in pyproject
