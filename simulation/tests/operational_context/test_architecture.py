"""Mechanical dependency, vocabulary and boundary guards for ``nxt_operational_context``."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = SIMULATION_ROOT / "nxt_operational_context"
SCRIPTS = SIMULATION_ROOT / "scripts"

#: Whitelist, not blacklist: every stdlib import root the leaf may use.
ALLOWED_STDLIB_ROOTS = {"__future__", "csv", "dataclasses", "datetime", "enum", "hashlib", "io", "json", "re", "typing", "zoneinfo"}
#: Composition roots allowed to import the leaf.
COMPOSITION_ROOTS = {"operational_context_fixture.py", "site_agent_fixture.py", "site_agent_demo.py"}

EXECUTION_TOKENS = (
    "apply_directive", "RobotTaskInterface", "HandoffController", "SafetyShield", "send_robot_command", "dispatch_collector",
    "rclpy", "rospy", "write_register", "write_coil", "emergency_stop",
)
LLM_PATTERNS = (r"\bopenai\b", r"\banthropic\b", r"\bllm\b", r"\bkimi\b", r"\bprompt\b", r"\bcompletion\b", r"\bgenerative\b")
#: Vocabulary this leaf must never speak in its contracts, adapters, importer,
#: or projections (the privacy module lists forbidden *headers* and is checked
#: separately): payroll and HR records, performance ranking, schedule writes,
#: inventory claims, and the reserved advisory words.
FORBIDDEN_VOCABULARY = (
    "payroll", "salary", "wage", "home_address", "phone_number", "medical", "rank(", "ranking", "performance_score",
    "approve_shift", "write_schedule", "dispensed", "available_now", "inventory_sufficient", "revenue", "savings",
    "recommend", "advice", "advisory", "forecast",
)
BANNED_CALL_NAMES = {"now", "utcnow", "today", "monotonic", "perf_counter", "time", "uuid1", "uuid4", "random", "randint", "getenv", "open", "urlopen"}


def _package_files() -> list[Path]:
    files = sorted(PACKAGE_ROOT.glob("*.py"))
    assert files
    return files


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_package_is_stdlib_only_within_the_whitelist_and_imports_no_repository_package() -> None:
    for path in _package_files():
        imported = _imports(path)
        assert not {name for name in imported if name.startswith("nxt_")}, f"{path.name} imports a repository package"
        assert imported <= ALLOWED_STDLIB_ROOTS, f"{path.name} imports {sorted(imported - ALLOWED_STDLIB_ROOTS)}"


def test_package_reads_no_clock_filesystem_network_or_randomness() -> None:
    for path in _package_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = node.func
                name = callee.attr if isinstance(callee, ast.Attribute) else callee.id if isinstance(callee, ast.Name) else None
                assert name not in BANNED_CALL_NAMES, f"{path.name} calls {name}()"


def test_package_speaks_no_execution_llm_or_forbidden_vocabulary() -> None:
    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for token in EXECUTION_TOKENS:
            assert token not in text, f"{path.name} mentions {token}"
        for pattern in LLM_PATTERNS:
            assert not re.search(pattern, text, flags=re.IGNORECASE), f"{path.name} matches {pattern}"
        if path.name == "privacy.py":
            continue  # it names forbidden headers in order to refuse them
        lowered = text.lower()
        for token in FORBIDDEN_VOCABULARY:
            assert token not in lowered, f"{path.name} mentions {token}"


def test_projection_keys_contain_no_inventory_or_availability_vocabulary() -> None:
    text = (PACKAGE_ROOT / "projection.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'"([a-z_]+)":', text))
    for key in keys:
        assert "dispens" not in key and "inventory" not in key and "available" not in key and "revenue" not in key, key


def test_no_existing_package_imports_or_mentions_the_leaf() -> None:
    for package in sorted(p for p in SIMULATION_ROOT.iterdir() if p.is_dir() and p.name.startswith("nxt_") and p.name != "nxt_operational_context"):
        for path in package.rglob("*.py"):
            assert "nxt_operational_context" not in path.read_text(encoding="utf-8"), f"{package.name}/{path.name} mentions nxt_operational_context"


def test_only_the_composition_roots_import_the_leaf() -> None:
    importers = {path.name for path in SCRIPTS.glob("*.py") if "nxt_operational_context" in path.read_text(encoding="utf-8")}
    assert importers <= COMPOSITION_ROOTS, importers
    assert "operational_context_fixture.py" in importers


def test_composition_roots_use_the_journal_builder_protocol_only() -> None:
    text = (SCRIPTS / "operational_context_fixture.py").read_text(encoding="utf-8")
    assert ".append_via(" in text and "journal.append(" not in text
    assert "nxt-operational-context/journal/v1" in text or "JOURNAL_SCHEMA" in text
    for token in EXECUTION_TOKENS:
        assert token not in text, f"operational_context_fixture.py mentions {token}"


def test_package_imports_cleanly_in_a_bare_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); import nxt_operational_context as m; print(m.JOURNAL_SCHEMA)" % str(SIMULATION_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "nxt-operational-context/journal/v1"


def test_package_is_registered_in_the_wheel_and_ci() -> None:
    pyproject = (SIMULATION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"nxt_operational_context"' in pyproject
    workflow = (SIMULATION_ROOT.parent / ".github" / "workflows" / "verification.yml").read_text(encoding="utf-8")
    assert "tests/operational_context" in workflow and "nxt_operational_context" in workflow


def test_fixture_files_carry_no_personal_information() -> None:
    fixture_dir = SCRIPTS / "operational_context_fixture"
    for path in sorted(fixture_dir.rglob("*.csv")):
        header = path.read_text(encoding="utf-8").splitlines()[0].lower()
        if "rejected" in str(path):
            continue
        for token in ("name", "phone", "address", "email", "wage", "salary", "note"):
            assert token not in header, f"{path.name} header mentions {token}"
        body = path.read_text(encoding="utf-8")
        assert "@" not in body and not re.search(r"\b\d{3}[- ]?\d{4}[- ]?\d{4}\b", body), path.name
        assert re.search(r"\bW-\d{3}\b", body) or "staff_ref" not in header
