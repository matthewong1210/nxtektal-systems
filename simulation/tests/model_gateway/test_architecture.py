"""Mechanical isolation of the provider-neutral network leaf."""

import ast
from pathlib import Path
import re
import tomllib

import pytest

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = SIMULATION_ROOT / "nxt_model_gateway"
REPOSITORY_ROOT = SIMULATION_ROOT.parent

ALLOWED_STDLIB = {
    "__future__", "copy", "dataclasses", "enum", "hashlib", "http", "json",
    "math", "re", "socket", "ssl", "threading", "types", "typing",
    # Approved runtime contract checks and bounded HTTP parser reads only.
    "collections", "io",
}
ALLOWED_THIRD_PARTY = {"jsonschema"}
FORBIDDEN_DOMAIN_TOKENS = {
    "staffing", "roster", "shift", "employee", "facilitystate", "planning",
    "robot", "actuator", "directive", "dispatch", "safetyshield", "rclpy",
    "ros2", "ledger", "journal", "filesystem", "site_agent",
}
BANNED_CALLS = {
    "open", "open_code", "FileIO", "__import__", "eval", "exec", "compile", "getenv", "environ",
    "now", "utcnow", "today", "time", "time_ns", "uuid1", "uuid4",
    "random", "randint", "system", "popen",
}
REVERSE_GUARDS = {
    "pilot_ops/test_boundaries.py": ("UPSTREAM_PACKAGES", "CORE_BANNED_ROOTS"),
    "site_runtime/test_architecture.py": ("package_names",),
    "agent_runtime/test_architecture.py": ("BANNED_IMPORT_ROOTS", "OTHER_PACKAGES"),
    "edge_observation/test_architecture.py": ("BANNED_IMPORT_ROOTS", "OTHER_PACKAGES"),
    "workflow_enablement/test_architecture.py": ("BANNED_IMPORT_ROOTS", "OTHER_PACKAGES"),
    "course_world_model/test_architecture.py": ("BANNED_IMPORT_ROOTS", "OTHER_PACKAGES"),
    "edge_task/test_architecture.py": ("OTHER_PACKAGES",),
    "site_agent/test_architecture.py": ("BANNED_FIRST_PARTY_MENTIONS", "OTHER_PACKAGES"),
}


def forbidden_imports(source: str) -> set[str]:
    rejected = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:
                continue
            if node.level > 1:
                rejected.add("." * node.level + (node.module or ""))
                continue
            if node.module == "io":
                rejected.update(f"io.{alias.name}" for alias in node.names
                                if alias.name not in {"RawIOBase", "BufferedReader"})
            modules = [node.module or ""]
        else:
            continue
        for module in modules:
            root = module.split(".")[0]
            if root.startswith("nxt_") or root not in ALLOWED_STDLIB | ALLOWED_THIRD_PARTY:
                rejected.add(module)
    return rejected


def forbidden_tokens(source: str) -> set[str]:
    # Scan identifiers, comments and strings, treating underscores as separators.
    # Case folding also recognizes canonical names such as FacilityState.
    # Keep whole canonical names (FacilityState) as well as CamelCase words.
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", source)
    words = re.sub(r"([A-Z])([A-Z][a-z])", r"\1_\2", words)
    lowered = source.casefold() + "\n" + words.casefold()
    return {
        token for token in FORBIDDEN_DOMAIN_TOKENS
        if re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", lowered)
    }


def test_import_guard_negative_control():
    assert forbidden_imports("import nxt_facility\n") == {"nxt_facility"}


@pytest.mark.parametrize("name", ["FileIO", "open", "open_code"])
def test_io_allowance_does_not_allow_filesystem_imports(name):
    assert forbidden_imports(f"from io import {name}") == {f"io.{name}"}


@pytest.mark.parametrize("module", [
    "nxt_model_gateway", "openai", "anthropic", "moonshot", "requests", "httpx",
    "os", "pathlib", "subprocess", "random", "uuid", "datetime", "time",
])
def test_import_guard_rejects_forbidden_roots(module):
    assert forbidden_imports(f"from {module} import thing") == {module}


def test_domain_token_guard_negative_control():
    assert forbidden_tokens("def dispatch_robot_command():\n    return None\n") == {
        "dispatch", "robot"
    }


def test_token_guard_includes_comments_strings_and_casefolded_names():
    assert forbidden_tokens('# STAFFING\nlabel = "FacilityState site_agent"') == {
        "staffing", "facilitystate", "site_agent"
    }


def test_token_guard_recognizes_camel_case_identifiers():
    assert forbidden_tokens("def dispatchRobotCommand(): pass") == {"dispatch", "robot"}


def test_neutral_vocabulary_negative_control_is_clean():
    assert forbidden_tokens("structured_output = {'result': []}\nmodel_id = 'x'") == set()


def test_gateway_sources_have_only_approved_imports_and_neutral_tokens():
    files = list(PACKAGE_ROOT.rglob("*.py"))
    assert files
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert not forbidden_imports(source), (path.name, forbidden_imports(source))
        assert not forbidden_tokens(source), (path.name, forbidden_tokens(source))
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call):
                name = (node.func.id if isinstance(node.func, ast.Name)
                        else node.func.attr if isinstance(node.func, ast.Attribute) else None)
                if name == "compile" and isinstance(node.func, ast.Attribute):
                    assert isinstance(node.func.value, ast.Name) and node.func.value.id == "re"
                else:
                    assert name not in BANNED_CALLS, (path.name, name)


def test_other_packages_cannot_import_or_mention_gateway():
    for package in SIMULATION_ROOT.glob("nxt_*"):
        if not package.is_dir() or package == PACKAGE_ROOT:
            continue
        for path in package.rglob("*.py"):
            assert "nxt_model_gateway" not in path.read_text(encoding="utf-8"), path


@pytest.mark.parametrize("relative,names", REVERSE_GUARDS.items())
def test_existing_reverse_guards_cover_gateway(relative, names):
    tree = ast.parse((SIMULATION_ROOT / "tests" / relative).read_text(encoding="utf-8"))
    assignments = {
        target.id: {item.value for item in ast.walk(node.value)
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)}
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
        and target.id in names
    }
    for name in names:
        assert "nxt_model_gateway" in assignments[name], (relative, name)


def test_gateway_is_registered_with_only_the_approved_new_core_dependency():
    manifest = tomllib.loads((SIMULATION_ROOT / "pyproject.toml").read_text())
    assert manifest["project"]["dependencies"] == [
        "pydantic>=2.7", "pyyaml>=6.0", "jsonschema>=4.26,<5"
    ]
    assert "nxt_model_gateway" in manifest["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]


def test_stable_contract_and_verification_are_registered():
    assert (SIMULATION_ROOT / "docs/model_gateway_v1.md").is_file()
    for relative in (".github/workflows/verification.yml", "docs/CI.md", ".agent/workflows/testing.md"):
        text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        assert "tests/model_gateway" in text, relative
        assert "tests/model_gateway/test_architecture.py" in text, relative
