"""Architectural guards for the downstream Shadow Ops package."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[2]
SHADOW_ROOT = SIM_ROOT / "nxt_pilot_ops"
STAFFING_ROOT = SHADOW_ROOT / "staffing"
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
    "nxt_model_gateway",
)
CORE_BANNED_ROOTS = {
    *UPSTREAM_PACKAGES,
    "simpy",
    "gymnasium",
    "numpy",
    "pxr",
    "rclpy",
    "rospy",
    "nxt_model_gateway",
}

_STAFFING_COMMON_STDLIB = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "hmac",
    "math",
    "re",
    "types",
    "typing",
    "unicodedata",
    "zoneinfo",
}
_STAFFING_LEDGER_STDLIB = {
    "contextlib",
    "fcntl",
    "json",
    "os",
    "pathlib",
    "stat",
    "threading",
    "weakref",
}
_STAFFING_FORBIDDEN_IDENTIFIER_TOKENS = {
    "actuator",
    "command",
    "estop",
    "execute",
    "execution",
    "robot",
}
_STAFFING_BARE_EFFECT_NAMES = {"__import__", "compile", "eval", "exec", "open"}
_STAFFING_LEDGER_OS_CALLS = {
    "os.close",
    "os.fchmod",
    "os.fdopen",
    "os.fspath",
    "os.fstat",
    "os.fsync",
    "os.mkdir",
    "os.open",
    "os.path.abspath",
    "os.replace",
    "os.stat",
}
_STAFFING_LEDGER_OS_VALUES = {
    "os.O_APPEND",
    "os.O_CREAT",
    "os.O_RDONLY",
    "os.O_RDWR",
    "os.O_TRUNC",
    "os.O_WRONLY",
    "os.name",
}
_STAFFING_LEDGER_OS_GETATTR = {"O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"}
_STAFFING_LEDGER_OS_REFERENCES = {
    "os",
    "os.path",
    *_STAFFING_LEDGER_OS_CALLS,
    *_STAFFING_LEDGER_OS_VALUES,
    *(f"os.{name}" for name in _STAFFING_LEDGER_OS_GETATTR),
}
_STAFFING_DATETIME_IMPORTS = {"date", "datetime", "time", "timedelta", "timezone"}
_STAFFING_CONTROLLED_NAMES = {"date", "datetime", "getattr", "os", "time", "timezone"}
_STAFFING_REJECTED_IMPORT_ROOTS = {"importlib", "random", "uuid"}
_STAFFING_CLOCK_CALL_LEAVES = {"now", "today", "utcnow"}
_STAFFING_NONDETERMINISTIC_CALL_LEAVES = {
    "getrandbits", "randint", "random", "randrange", "uuid1", "uuid3", "uuid4", "uuid5"
}
_STAFFING_DATETIME_CALLS = {
    "date.fromisoformat",
    "datetime.combine",
    "datetime.date",
    "datetime.date.fromisoformat",
    "datetime.datetime",
    "datetime.datetime.combine",
    "datetime.datetime.fromisoformat",
    "datetime.fromisoformat",
    "datetime.time",
    "datetime.timezone",
}
_STAFFING_DATETIME_TYPES = {
    "datetime.date", "datetime.datetime", "datetime.time", "datetime.timezone"
}
_STAFFING_DATETIME_VALUES = {"datetime.timezone.utc", "timezone.utc"}
_STAFFING_SERIALIZATION_EXPORTS = {
    "canonical_json", "canonical_json_bytes", "stable_digest", "to_primitive"
}
_STAFFING_LEDGER_EXPORTS = {
    "GENESIS_HASH",
    "EventCommit",
    "LedgerRecord",
    "StaffingLedger",
    "StaffingLedgerIntegrityError",
    "canonical_anchor_bytes",
    "exact_record_body",
    "parse_record",
    "receipt_from_record",
    "validate_conflict_decision",
}
_STAFFING_FORBIDDEN_RELATIVE_NAMES = {
    "Path", "_fcntl", "date", "datetime", "getattr", "os", "threading", "time", "timezone"
}
_STAFFING_PACKAGE = ("nxt_pilot_ops", "staffing")
_STAFFING_PRIVACY_KEYS = {
    "api_key", "headers", "metadata", "prompt", "raw_response", "reasoning", "secret"
}
_STAFFING_PRIVACY_ANCHORS = {
    Path("projection.py"): "FORBIDDEN_KEYS",
    Path("workflow.py"): "FORBIDDEN_EVENT_KEYS",
}
STAFFING_PRIVACY_PATTERN = re.compile(
    r"api[_-]?key|authorization|x-api-key|robottaskinterface|apply_directive",
    re.IGNORECASE,
)


def _staffing_python_files(root: Path = STAFFING_ROOT) -> tuple[Path, ...]:
    """Return every staffing Python file that the wheel package can include."""

    return tuple(
        sorted(
            path
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    )


def _staffing_module_path(relative_path: Path) -> tuple[str, ...]:
    parts = list(relative_path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return (*_STAFFING_PACKAGE, *parts)


_STAFFING_IMPORTABLE_MODULES = frozenset(
    _staffing_module_path(path.relative_to(STAFFING_ROOT))
    for path in _staffing_python_files()
)


def _relative_import_targets(
    node: ast.ImportFrom, relative_path: Path
) -> tuple[tuple[str, ...], ...]:
    module_path = _staffing_module_path(relative_path)
    package_path = (
        module_path if relative_path.name == "__init__.py" else module_path[:-1]
    )
    parents = node.level - 1
    if parents > len(package_path):
        return ()
    base = package_path[: len(package_path) - parents]
    if node.module is not None:
        return ((*base, *node.module.split(".")),)
    return tuple((*base, *alias.name.split(".")) for alias in node.names)


def _imports_staffing_module_object(
    node: ast.ImportFrom, relative_path: Path
) -> bool:
    if node.level == 0 or _relative_import_targets(node, relative_path) != (
        _STAFFING_PACKAGE,
    ):
        return False
    return any(
        (*_STAFFING_PACKAGE, *alias.name.split(".")) in _STAFFING_IMPORTABLE_MODULES
        for alias in node.names
    )


def _staffing_import_violations(source: str, relative_path: Path) -> tuple[str, ...]:
    """Return sorted import-seam violations for one staffing source file."""

    tree = ast.parse(source)
    allowed_absolute = set(_STAFFING_COMMON_STDLIB)
    if relative_path == Path("ledger.py"):
        allowed_absolute.update(_STAFFING_LEDGER_STDLIB)
    violations: set[str] = set()
    prefix = relative_path.as_posix()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in allowed_absolute:
                    violations.add(f"{prefix}: absolute import {alias.name}")
                elif (
                    alias.name.startswith("os.")
                    and alias.name not in {"os.path"}
                ):
                    violations.add(f"{prefix}: forbidden OS import {alias.name}")
                if root in {"datetime", "os"} and (
                    alias.asname is not None or alias.name != root
                ):
                    violations.add(
                        f"{prefix}: forbidden controlled import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "__builtins__" for alias in node.names):
                violations.add(f"{prefix}: forbidden builtins import")
            if node.level == 0:
                module = node.module or ""
                if module.split(".", 1)[0] not in allowed_absolute:
                    violations.add(f"{prefix}: absolute import {module or '<empty>'}")
                if module == "os" or module.startswith("os."):
                    for alias in node.names:
                        origin = f"{module}.{alias.name}"
                        if origin not in _STAFFING_LEDGER_OS_REFERENCES:
                            violations.add(
                                f"{prefix}: forbidden OS import {origin}"
                            )
                        else:
                            violations.add(
                                f"{prefix}: forbidden OS import form {origin}"
                            )
                if module == "datetime":
                    for alias in node.names:
                        if (
                            alias.asname is not None
                            or alias.name not in _STAFFING_DATETIME_IMPORTS
                        ):
                            violations.add(
                                f"{prefix}: forbidden datetime import {alias.name}"
                            )
            else:
                targets = _relative_import_targets(node, relative_path)
                allowed_targets = _STAFFING_IMPORTABLE_MODULES | {
                    ("nxt_pilot_ops", "serialization")
                }
                if not targets or any(target not in allowed_targets for target in targets):
                    dots = "." * node.level
                    violations.add(
                        f"{prefix}: relative import {dots}{node.module or '<empty>'}"
                    )
                if node.module is None:
                    violations.add(f"{prefix}: relative module-object import")
                if _imports_staffing_module_object(node, relative_path):
                    violations.add(f"{prefix}: staffing module-object import")
                if any(
                    alias.name in _STAFFING_FORBIDDEN_RELATIVE_NAMES
                    for alias in node.names
                ):
                    violations.add(f"{prefix}: forbidden relative capability import")
                if targets == (("nxt_pilot_ops", "serialization"),) and any(
                    alias.name not in _STAFFING_SERIALIZATION_EXPORTS
                    for alias in node.names
                ):
                    violations.add(f"{prefix}: forbidden serialization import")
                if targets == ((*_STAFFING_PACKAGE, "ledger"),) and any(
                    alias.name not in _STAFFING_LEDGER_EXPORTS
                    for alias in node.names
                ):
                    violations.add(f"{prefix}: forbidden ledger import")
    return tuple(sorted(violations))


def _qualified_name(node: ast.expr) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        base = _qualified_name(node.value)
        return (*base, node.attr) if base else ()
    return ()


def _identifier_tokens(value: str) -> tuple[str, ...]:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    camel_split = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", camel_split)
    return tuple(token for token in re.split(r"[^a-z0-9]+", camel_split.lower()) if token)


def _forbidden_identifier(value: str) -> bool:
    tokens = _identifier_tokens(value)
    pairs = set(zip(tokens, tokens[1:]))
    return bool(
        set(tokens) & _STAFFING_FORBIDDEN_IDENTIFIER_TOKENS
        or ("e", "stop") in pairs
        or ("emergency", "stop") in pairs
    )


def _forbidden_call_leaf(value: str) -> bool:
    return value in (
        _STAFFING_CLOCK_CALL_LEAVES | _STAFFING_NONDETERMINISTIC_CALL_LEAVES
    )


def _invoked_leaf(call: ast.Call) -> str:
    function = call.func
    while isinstance(function, ast.Attribute) and function.attr == "__call__":
        function = function.value
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _constant_getattr_leaf(call: ast.Call) -> str | None:
    if (
        isinstance(call.func, ast.Name)
        and call.func.id == "getattr"
        and len(call.args) >= 2
        and isinstance(call.args[1], ast.Constant)
        and type(call.args[1].value) is str
    ):
        return call.args[1].value
    return None


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _annotation_nodes(tree: ast.AST) -> set[ast.AST]:
    roots: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                roots.append(node.returns)
    return {item for root in roots for item in ast.walk(root)}


def _is_call_function(node: ast.AST, parent: ast.AST | None) -> bool:
    return isinstance(parent, ast.Call) and parent.func is node


def _is_type_identity(node: ast.AST, parent: ast.AST | None) -> bool:
    if not isinstance(parent, ast.Compare) or not all(
        isinstance(operator, (ast.Is, ast.IsNot)) for operator in parent.ops
    ):
        return False
    operands = (parent.left, *parent.comparators)
    return node in operands and any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == "type"
        for item in operands
    )


def _datetime_display(parts: tuple[str, ...]) -> str:
    if parts and parts[0] in {"date", "time", "timezone"}:
        parts = ("datetime", *parts)
    return ".".join(parts)


def _approved_os_getattr(call: ast.Call, relative_path: Path) -> bool:
    return bool(
        relative_path == Path("ledger.py")
        and isinstance(call.func, ast.Name)
        and call.func.id == "getattr"
        and len(call.args) in {2, 3}
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "os"
        and isinstance(call.args[1], ast.Constant)
        and type(call.args[1].value) is str
        and call.args[1].value in _STAFFING_LEDGER_OS_GETATTR
    )


def _controlled_getattr_origin(call: ast.Call) -> str | None:
    if (
        not isinstance(call.func, ast.Name)
        or call.func.id != "getattr"
        or len(call.args) < 2
    ):
        return None
    parts = _qualified_name(call.args[0])
    if not parts:
        return None
    attribute = call.args[1]
    leaf = (
        attribute.value
        if isinstance(attribute, ast.Constant) and type(attribute.value) is str
        else "*"
    )
    controlled = (
        parts[0] in _STAFFING_CONTROLLED_NAMES | _STAFFING_REJECTED_IMPORT_ROOTS
        or any(pair == ("ledger", "os") for pair in zip(parts, parts[1:]))
        or (parts == ("ledger",) and leaf == "os")
    )
    if not controlled:
        return None
    origin = (*parts, leaf)
    return _datetime_display(origin) if parts[0] in _STAFFING_CONTROLLED_NAMES else ".".join(origin)


def _staffing_effect_violations(source: str, relative_path: Path) -> tuple[str, ...]:
    """Return sorted effect/execution violations for one staffing source file."""

    tree = ast.parse(source)
    prefix = relative_path.as_posix()
    violations: set[str] = set()
    named_nodes: list[str] = []
    parents = _parent_map(tree)
    annotations = _annotation_nodes(tree)

    for node in ast.walk(tree):
        parent = parents.get(node)
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _STAFFING_REJECTED_IMPORT_ROOTS:
                    violations.add(f"{prefix}: forbidden source import {alias.name}")
                if root == "os" and (
                    relative_path != Path("ledger.py")
                    or alias.name != "os"
                    or alias.asname is not None
                ):
                    violations.add(f"{prefix}: forbidden controlled import {alias.name}")
                if root == "datetime" and (
                    alias.name != "datetime" or alias.asname is not None
                ):
                    violations.add(f"{prefix}: forbidden controlled import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if any(alias.name == "__builtins__" for alias in node.names):
                violations.add(f"{prefix}: forbidden builtins import")
            if node.level == 0 and root in _STAFFING_REJECTED_IMPORT_ROOTS:
                violations.add(f"{prefix}: forbidden source import {module}")
            if node.level == 0 and root == "os":
                violations.add(f"{prefix}: forbidden controlled import from {module}")
            if node.level == 0 and module == "datetime" and any(
                alias.asname is not None
                or alias.name not in _STAFFING_DATETIME_IMPORTS
                for alias in node.names
            ):
                violations.add(f"{prefix}: forbidden datetime import form")
            if node.level and node.module == "ledger" and any(
                alias.name in _STAFFING_CONTROLLED_NAMES for alias in node.names
            ):
                violations.add(f"{prefix}: controlled object import from ledger")
            if node.level and node.module is None and any(
                alias.name == "ledger" for alias in node.names
            ):
                violations.add(f"{prefix}: controlled ledger module import")
            if _imports_staffing_module_object(node, relative_path):
                violations.add(f"{prefix}: staffing module-object import")

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            named_nodes.append(node.name)
            if node.name in _STAFFING_CONTROLLED_NAMES:
                violations.add(f"{prefix}: controlled name rebound {node.name}")
        elif isinstance(node, ast.arg):
            named_nodes.append(node.arg)
            if node.arg in _STAFFING_CONTROLLED_NAMES:
                violations.add(f"{prefix}: controlled parameter {node.arg}")
        elif isinstance(node, ast.alias):
            named_nodes.append(node.name.rsplit(".", 1)[-1])
            if node.asname is not None:
                named_nodes.append(node.asname)
            bound = (
                node.asname
                or (
                    node.name.split(".", 1)[0]
                    if isinstance(parent, ast.Import)
                    else node.name.rsplit(".", 1)[-1]
                )
            )
            canonical = bool(
                node.asname is None
                and (
                    isinstance(parent, ast.Import)
                    and node.name in {"datetime", "os"}
                    or isinstance(parent, ast.ImportFrom)
                    and parent.level == 0
                    and parent.module == "datetime"
                    and node.name in _STAFFING_DATETIME_IMPORTS
                )
            )
            if bound in _STAFFING_CONTROLLED_NAMES and not canonical:
                violations.add(f"{prefix}: controlled name rebound {bound}")
        elif isinstance(node, ast.keyword) and node.arg is not None:
            named_nodes.append(node.arg)
        elif isinstance(node, ast.Name):
            named_nodes.append(node.id)
            if node.id == "__builtins__":
                violations.add(f"{prefix}: forbidden builtins capability")
            if node.id in _STAFFING_BARE_EFFECT_NAMES:
                violations.add(f"{prefix}: forbidden bare reference {node.id}")
            if node.id in _STAFFING_CONTROLLED_NAMES:
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    violations.add(f"{prefix}: controlled name rebound {node.id}")
                elif isinstance(node.ctx, ast.Load) and node not in annotations:
                    if node.id == "getattr":
                        if not _is_call_function(node, parent):
                            violations.add(
                                f"{prefix}: controlled name escaped getattr"
                            )
                    elif node.id == "os":
                        if isinstance(parent, ast.Attribute) and parent.value is node:
                            pass
                        elif (
                            isinstance(parent, ast.Call)
                            and parent.args
                            and parent.args[0] is node
                            and _approved_os_getattr(parent, relative_path)
                        ):
                            pass
                        else:
                            violations.add(f"{prefix}: controlled name escaped os")
                    elif isinstance(parent, ast.Attribute) and parent.value is node:
                        pass
                    elif _is_call_function(node, parent) or _is_type_identity(
                        node, parent
                    ):
                        pass
                    else:
                        violations.add(
                            f"{prefix}: controlled name escaped {node.id}"
                        )
        elif isinstance(node, ast.Attribute):
            named_nodes.append(node.attr)
            if not (isinstance(parent, ast.Attribute) and parent.value is node):
                parts = _qualified_name(node)
                origin = ".".join(parts)
                if any(
                    pair == ("ledger", "os") for pair in zip(parts, parts[1:])
                ):
                    violations.add(
                        f"{prefix}: forbidden controlled reference {origin}"
                    )
                elif parts and parts[0] == "os":
                    allowed = relative_path == Path("ledger.py") and (
                        origin in _STAFFING_LEDGER_OS_VALUES
                        or (
                            origin in _STAFFING_LEDGER_OS_CALLS
                            and _is_call_function(node, parent)
                        )
                    )
                    if not allowed:
                        violations.add(
                            f"{prefix}: forbidden source reference {origin}"
                        )
                elif parts and parts[0] in {
                    "date",
                    "datetime",
                    "time",
                    "timezone",
                }:
                    display = _datetime_display(parts)
                    allowed = (
                        origin in _STAFFING_DATETIME_VALUES
                        or (
                            origin in _STAFFING_DATETIME_CALLS
                            and _is_call_function(node, parent)
                        )
                        or (
                            origin in _STAFFING_DATETIME_TYPES
                            and (node in annotations or _is_type_identity(node, parent))
                        )
                    )
                    if not allowed:
                        violations.add(
                            f"{prefix}: forbidden source reference {display}"
                        )
                elif parts and parts[0] in _STAFFING_REJECTED_IMPORT_ROOTS:
                    violations.add(
                        f"{prefix}: forbidden source reference {origin}"
                    )
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                if name in _STAFFING_CONTROLLED_NAMES:
                    violations.add(f"{prefix}: controlled scope declaration {name}")
        elif isinstance(node, ast.ExceptHandler) and node.name in _STAFFING_CONTROLLED_NAMES:
            violations.add(f"{prefix}: controlled name rebound {node.name}")
        elif isinstance(node, (ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            bound = node.rest if isinstance(node, ast.MatchMapping) else node.name
            if bound is not None:
                named_nodes.append(bound)
                if bound in _STAFFING_CONTROLLED_NAMES:
                    violations.add(f"{prefix}: controlled name rebound {bound}")
        elif isinstance(node, ast.MatchClass):
            named_nodes.extend(node.kwd_attrs)

        if not isinstance(node, ast.Call):
            continue
        leaf = _invoked_leaf(node)
        if isinstance(node.func, ast.Name) and leaf in _STAFFING_BARE_EFFECT_NAMES:
            violations.add(f"{prefix}: forbidden bare call {leaf}")
        if _forbidden_call_leaf(leaf):
            violations.add(f"{prefix}: forbidden call leaf {leaf}")
        getattr_leaf = _constant_getattr_leaf(node)
        if getattr_leaf is not None and (
            _forbidden_call_leaf(getattr_leaf)
            or _forbidden_identifier(getattr_leaf)
        ):
            violations.add(f"{prefix}: forbidden getattr leaf {getattr_leaf}")
        getattr_origin = _controlled_getattr_origin(node)
        if getattr_origin is not None and not _approved_os_getattr(
            node, relative_path
        ):
            violations.add(
                f"{prefix}: forbidden source reference {getattr_origin}"
            )

    for name in named_nodes:
        if _forbidden_identifier(name):
            violations.add(f"{prefix}: forbidden identifier {name}")
    return tuple(sorted(violations))


def _privacy_anchor_literals(
    tree: ast.Module, anchor_name: str, source: str
) -> tuple[ast.Constant, ...] | None:
    assignments = tuple(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == anchor_name
    )
    if len(assignments) != 1:
        return None
    value = assignments[0].value
    if (
        not isinstance(value, ast.Call)
        or not isinstance(value.func, ast.Name)
        or value.func.id != "frozenset"
        or len(value.args) != 1
        or value.keywords
        or not isinstance(value.args[0], ast.Set)
    ):
        return None
    literals = tuple(value.args[0].elts)
    if any(
        not isinstance(item, ast.Constant) or type(item.value) is not str
        for item in literals
    ):
        return None
    typed_literals = tuple(item for item in literals if isinstance(item, ast.Constant))
    if (
        len(typed_literals) != len(_STAFFING_PRIVACY_KEYS)
        or {item.value for item in typed_literals} != _STAFFING_PRIVACY_KEYS
        or any(
            item.value == "api_key"
            and ast.get_source_segment(source, item) not in {'"api_key"', "'api_key'"}
            for item in typed_literals
        )
    ):
        return None
    return typed_literals


def _is_privacy_anchor_target(target: ast.AST, anchor_name: str) -> bool:
    for node in ast.walk(target):
        if isinstance(node, ast.Name) and node.id == anchor_name:
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in {"globals", "locals"}
            and not node.value.args
            and not node.value.keywords
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == anchor_name
        ):
            return True
    return False


def _privacy_anchor_rebound(tree: ast.Module, anchor_name: str) -> bool:
    canonical = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == anchor_name
        ),
        None,
    )
    for node in ast.walk(tree):
        if node is canonical:
            continue
        targets: tuple[ast.AST, ...] = ()
        if isinstance(node, ast.Assign):
            targets = tuple(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = (node.target,)
        elif isinstance(node, ast.Delete):
            targets = tuple(node.targets)
        if any(_is_privacy_anchor_target(target, anchor_name) for target in targets):
            return True
    return False


def _staffing_privacy_violations(
    source: str, relative_path: Path
) -> tuple[str, ...]:
    """Allow one exact API-key denylist literal at each reviewed AST anchor."""

    tree = ast.parse(source)
    prefix = relative_path.as_posix()
    violations: set[str] = set()
    approved: tuple[int, int, int] | None = None
    approved_literal: ast.Constant | None = None
    anchor_name = _STAFFING_PRIVACY_ANCHORS.get(relative_path)
    if anchor_name is not None:
        literals = _privacy_anchor_literals(tree, anchor_name, source)
        api_literals = (
            () if literals is None else tuple(item for item in literals if item.value == "api_key")
        )
        if len(api_literals) != 1:
            violations.add(f"{prefix}: malformed privacy anchor {anchor_name}")
        else:
            approved_literal = api_literals[0]
            approved = (
                approved_literal.lineno,
                approved_literal.col_offset,
                approved_literal.end_col_offset,
            )
        if _privacy_anchor_rebound(tree, anchor_name):
            violations.add(f"{prefix}: privacy anchor rebound {anchor_name}")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or type(node.value) is not str:
            continue
        matches = tuple(STAFFING_PRIVACY_PATTERN.finditer(node.value))
        if not matches:
            continue
        source_segment = ast.get_source_segment(source, node) or ""
        for match in matches:
            if node is approved_literal and match.group(0) == "api_key":
                continue
            if STAFFING_PRIVACY_PATTERN.search(source_segment) is None:
                violations.add(
                    f"{prefix}:{node.lineno}:{node.col_offset + 1}: "
                    f"forbidden decoded privacy token {match.group(0)}"
                )

    for line_number, line in enumerate(source.splitlines(), start=1):
        for match in STAFFING_PRIVACY_PATTERN.finditer(line):
            if (
                approved is not None
                and approved[0] == line_number
                and approved[1] <= match.start()
                and match.end() <= approved[2]
                and match.group(0) == "api_key"
            ):
                continue
            violations.add(
                f"{prefix}:{line_number}:{match.start() + 1}: forbidden privacy token {match.group(0)}"
            )
    return tuple(sorted(violations))


def _wheel_packages() -> list[str]:
    pyproject = tomllib.loads((SIM_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])


def _staffing_wheel_package_violations(packages: list[str]) -> tuple[str, ...]:
    violations: set[str] = set()
    for package in packages:
        normalized = "/".join(
            part
            for part in package.replace("\\", "/").split("/")
            if part not in {"", "."}
        )
        if (
            normalized == "nxt_pilot_ops/staffing"
            or normalized.startswith("nxt_pilot_ops/staffing/")
            or package == "nxt_pilot_ops.staffing"
            or package.startswith("nxt_pilot_ops.staffing.")
        ):
            violations.add(package)
    return tuple(sorted(violations))


def _staffing_root_export_violations(source: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    violations: set[str] = set()
    operation_bindings: set[str] = set()
    exported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.module == "operations":
            for alias in node.names:
                if alias.name in {"*", "StaffingOperations"}:
                    operation_bindings.add(alias.asname or alias.name)
                    violations.add("root imports StaffingOperations")
        elif (
            isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
            and isinstance(getattr(node, "target", None), ast.Name)
            and node.target.id == "__all__"
        ):
            exported_names.update(
                item.value
                for item in ast.walk(node.value)
                if isinstance(item, ast.Constant) and type(item.value) is str
            )
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            exported_names.update(
                item.value
                for item in ast.walk(node.value)
                if isinstance(item, ast.Constant) and type(item.value) is str
            )
    if operation_bindings & exported_names or "StaffingOperations" in exported_names:
        violations.add("root exports StaffingOperations")
    return tuple(sorted(violations))


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


def test_staffing_modules_use_only_the_frozen_import_seams() -> None:
    for path in _staffing_python_files():
        relative_path = path.relative_to(STAFFING_ROOT)
        assert _staffing_import_violations(
            path.read_text(encoding="utf-8"), relative_path
        ) == (), relative_path


def test_staffing_modules_have_no_hidden_effect_or_execution_surface() -> None:
    for path in _staffing_python_files():
        relative_path = path.relative_to(STAFFING_ROOT)
        assert _staffing_effect_violations(
            path.read_text(encoding="utf-8"), relative_path
        ) == (), relative_path


def test_staffing_import_guard_rejects_an_unlisted_standard_library_root() -> None:
    violations = _staffing_import_violations("import socket\n", Path("contracts.py"))

    assert violations == ("contracts.py: absolute import socket",)


def test_staffing_import_guard_freezes_ledger_only_and_relative_seams() -> None:
    assert _staffing_import_violations("import os\n", Path("contracts.py")) == (
        "contracts.py: absolute import os",
    )
    assert _staffing_import_violations("import os\n", Path("ledger.py")) == ()
    assert _staffing_import_violations(
        "from ..serialization import stable_digest\n", Path("operations.py")
    ) == ()
    assert _staffing_import_violations(
        "from . import contracts\n", Path("operations.py")
    )
    assert _staffing_import_violations(
        "from ..model_gateway import ModelGateway\n", Path("operations.py")
    ) == ("operations.py: relative import ..model_gateway",)
    assert _staffing_import_violations(
        "from os import urandom as nonce\n", Path("ledger.py")
    ) == ("ledger.py: forbidden OS import os.urandom",)


def test_staffing_effect_guard_rejects_normalized_execution_names() -> None:
    for name in (
        "dispatch_robot_command",
        "actuator_port",
        "emergency_stop_gate",
        "e_stop_gate",
        "EStopGate",
        "execute_plan",
        "execution_record",
    ):
        violations = _staffing_effect_violations(f"{name}()\n", Path("operations.py"))
        assert violations == (f"operations.py: forbidden identifier {name}",)


def test_staffing_ast_guards_allow_closed_provenance_and_schema_vocabulary() -> None:
    source = '''
import datetime
import re

def evidence(prompt, provider, model_id):
    """A description may discuss commands or execution without adding a surface."""
    opening = datetime.time(9, 0)
    service_date = datetime.date.fromisoformat("2026-10-06")
    start_at = datetime.datetime.combine(service_date, opening)
    assert type(start_at) is datetime.datetime
    utc = datetime.timezone.utc
    pattern = re.compile("closed")
    label = getattr(provider, "label", None)
    return prompt, model_id, start_at, utc, pattern, label, ("KIMI", "OPENAI", "ANTHROPIC")
'''

    assert _staffing_import_violations(source, Path("contracts.py")) == ()
    assert _staffing_effect_violations(source, Path("contracts.py")) == ()


def test_staffing_effect_guard_rejects_dynamic_nondeterministic_and_os_calls() -> None:
    source = '''
import datetime
import os
import random
import uuid
from datetime import date

__import__("socket")
open("evidence")
eval("value")
exec("value")
compile("value", "evidence", "exec")
datetime.now()
datetime.utcnow()
date.today()
uuid.uuid4()
random.random()
os.urandom(8)
os.getenv("TOKEN")
os.posix_spawn("program", (), {})
os.execv("program", ())
value = os.environ
'''
    violations = _staffing_effect_violations(source, Path("ledger.py"))

    assert "ledger.py: forbidden bare call __import__" in violations
    assert "ledger.py: forbidden bare reference __import__" in violations
    assert "ledger.py: forbidden bare call open" in violations
    assert "ledger.py: forbidden bare call eval" in violations
    assert "ledger.py: forbidden bare call exec" in violations
    assert "ledger.py: forbidden bare call compile" in violations
    for origin in (
        "datetime.now",
        "datetime.utcnow",
        "datetime.date.today",
        "uuid.uuid4",
        "random.random",
        "os.urandom",
        "os.getenv",
        "os.posix_spawn",
        "os.execv",
        "os.environ",
    ):
        assert f"ledger.py: forbidden source reference {origin}" in violations


def test_staffing_guard_file_inventory_is_recursive(tmp_path: Path) -> None:
    root = tmp_path / "staffing"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "contracts.py").write_text("", encoding="utf-8")
    (nested / "worker.py").write_text("", encoding="utf-8")

    assert tuple(path.relative_to(root) for path in _staffing_python_files(root)) == (
        Path("contracts.py"),
        Path("nested/worker.py"),
    )


def test_staffing_relative_import_guard_resolves_nested_module_paths() -> None:
    assert _staffing_import_violations(
        "from ..contracts import StaffingError\n", Path("nested/worker.py")
    ) == ()
    assert _staffing_import_violations(
        "from ...serialization import stable_digest\n", Path("nested/worker.py")
    ) == ()
    assert _staffing_import_violations(
        "from ...model_gateway import ModelGateway\n", Path("nested/worker.py")
    ) == ("nested/worker.py: relative import ...model_gateway",)
    assert _staffing_import_violations(
        "from . import ledger as vault\n", Path("operations.py")
    )


def test_staffing_relative_import_guard_freezes_exported_symbols() -> None:
    sources = (
        "from .ledger import Path as FilePath\n",
        "from .ledger import threading as threads\n",
        "from .ledger import NOT_EXPORTED\n",
        "from ..serialization import dumps\n",
        "from .. import serialization as vault\n",
        "from .contracts import date as service_date\n",
        "from .contracts import getattr as lookup\n",
    )

    for source in sources:
        assert _staffing_import_violations(source, Path("operations.py")), source


def test_staffing_relative_import_guard_rejects_resolved_module_objects() -> None:
    sources = (
        "from ..staffing import ledger as vault\nvault.os.abort()\n",
        "from ..staffing import contracts as domain\n",
    )
    results = tuple(
        (
            source,
            _staffing_import_violations(source, Path("operations.py")),
            _staffing_effect_violations(source, Path("operations.py")),
        )
        for source in sources
    )

    assert all(imports and effects for _, imports, effects in results), results


def test_staffing_import_guard_rejects_builtins_symbol_laundering() -> None:
    source = (
        'from dataclasses import __builtins__ as runtime\n'
        'runtime["__import__"]("socket")\n'
    )
    imports = _staffing_import_violations(source, Path("contracts.py"))
    effects = _staffing_effect_violations(source, Path("contracts.py"))

    assert imports and effects, (imports, effects)


def test_staffing_effect_guard_allows_harmless_local_call_names() -> None:
    source = """
def wait():
    return None

def sample():
    return None

def process_payload():
    wait()
    sample()

process_payload()
"""

    assert _staffing_effect_violations(source, Path("operations.py")) == ()


def test_staffing_effect_guard_rejects_os_sources_beyond_a_leaf_denylist() -> None:
    sources = (
        "import os\nos.abort()\n",
        "import os\nos.waitid(0, 0)\n",
        "import os\nos.getenvb(b'KEY')\n",
        "import os\nos.getpid()\n",
        "import os\nos.getppid()\n",
        "import os\nos.path.expandvars('$KEY')\n",
        "from os import getpid as alias\nalias()\n",
        "from os.path import expandvars as alias\nalias('$KEY')\n",
        "import os\ngetattr(os, 'abort')()\n",
        "import os\ngetattr(os.path, 'expandvars')('$KEY')\n",
        "import os\nname = 'abort'\ngetattr(os, name)()\n",
    )

    for source in sources:
        assert _staffing_effect_violations(source, Path("ledger.py")), source


def test_staffing_effect_guard_rejects_controlled_source_bypasses() -> None:
    sources = (
        "from datetime import datetime as clock\nclock.now()\n",
        "import uuid as ids\nids.uuid4()\n",
        "from random import sample as choose\nchoose((), 0)\n",
        "import os\nplatform = os\nplatform.abort()\n",
        'import datetime\ngetattr(datetime.datetime, "now")()\n',
        "import os as source\ndef dangerous():\n    source.abort()\n",
        "import os as source\nif False:\n    import datetime as source\nsource.abort()\n",
        "import datetime\ngetter = getattr\ngetter(datetime.datetime, 'now')()\n",
        "import datetime\ngetattr(datetime.datetime, name)()\n",
        "import random\ngetattr(random, name)()\n",
        "import uuid\ngetattr(uuid, name)()\n",
        "import importlib\ngetattr(importlib, name)('socket')\n",
        "import os\ndef f(source=os):\n    source.abort()\n",
        "import os\n(platform,) = (os,)\nplatform.abort()\n",
        "import os\nfor platform in (os,):\n    platform.abort()\n",
        "import os\n[x.abort() for x in (os,)]\n",
        "import os\nimport datetime\nsource = os\nclass Holder:\n"
        "    source = datetime\n    def dangerous(self):\n        source.abort()\n",
        "import os\nimport datetime\nsource = os\ndef dangerous():\n"
        "    global source\n    source.abort()\n    source = datetime.time\n",
        "import re as os\n",
        "from re import compile as getattr\n",
        "import os\nmatch object():\n    case os:\n        os.close(1)\n",
        "import os\nmatch []:\n    case [*os]:\n        os.close(1)\n",
        "import os\nmatch {}:\n    case {**os}:\n        os.close(1)\n",
        "from .ledger import os\n",
        "from . import ledger\nledger.os.abort()\n",
        "from . import ledger as vault\nvault.os.abort()\n",
        "from . import ledger\nvault = ledger\nvault.os.abort()\n",
        "from . import ledger\ngetattr(ledger, 'os').abort()\n",
    )

    for source in sources:
        assert _staffing_effect_violations(source, Path("ledger.py")), source


def test_staffing_effect_guard_rejects_clock_call_leaves_without_origin() -> None:
    sources = (
        "clock.now()\n",
        "service.utcnow()\n",
        "calendar.today()\n",
        "def use(clock):\n    return clock.now()\n",
        "service.clock().now()\n",
    )

    for source in sources:
        assert _staffing_effect_violations(source, Path("contracts.py")), source


def test_staffing_effect_guard_rejects_implicit_dynamic_capabilities() -> None:
    sources = (
        '__builtins__["__import__"]("socket")\n',
        'getattr(__builtins__, "__import__")("socket")\n',
        '__builtins__.__import__("socket")\n',
        'getattr(clock, "now")()\n',
        'getattr(worker, "dispatch_robot_command")()\n',
        "clock.now.__call__()\n",
        "clock.now.__call__.__call__()\n",
        "entropy.random()\n",
        "entropy.getrandbits(8)\n",
        "entropy.randint(1, 2)\n",
        "entropy.randrange(2)\n",
        "ids.uuid1()\n",
        "ids.uuid3(namespace, name)\n",
        "ids.uuid4()\n",
        "ids.uuid5(namespace, name)\n",
        "match value:\n    case Event(estop=gate):\n        pass\n",
        "estop_gate()\n",
    )

    for source in sources:
        assert _staffing_effect_violations(source, Path("contracts.py")), source

    assert _staffing_effect_violations("desktop()\nsample()\n", Path("contracts.py")) == ()


def test_staffing_is_a_narrow_subpackage_with_deep_operations_import() -> None:
    packages = _wheel_packages()

    assert packages.count("nxt_pilot_ops") == 1
    assert _staffing_wheel_package_violations(packages) == ()
    assert STAFFING_ROOT.is_dir()
    assert _staffing_root_export_violations(
        (STAFFING_ROOT / "__init__.py").read_text(encoding="utf-8")
    ) == ()

    import nxt_pilot_ops.staffing as staffing
    from nxt_pilot_ops.staffing import operations

    assert operations.__all__ == ("StaffingOperations",)
    assert "StaffingOperations" not in staffing.__all__
    assert not hasattr(staffing, "StaffingOperations")


def test_staffing_wheel_guard_rejects_hatch_style_subpackage_entry() -> None:
    assert _staffing_wheel_package_violations(
        ["nxt_pilot_ops", "nxt_pilot_ops/staffing"]
    )


def test_staffing_root_guard_rejects_operations_alias_exports() -> None:
    source = '''
from .operations import StaffingOperations as AdvisoryOperations

__all__ = ["AdvisoryOperations"]
'''

    assert _staffing_root_export_violations(source)


def test_staffing_privacy_scan_has_only_reviewed_api_key_denylist_controls() -> None:
    for path in _staffing_python_files():
        relative_path = path.relative_to(STAFFING_ROOT)
        assert _staffing_privacy_violations(
            path.read_text(encoding="utf-8"), relative_path
        ) == (), relative_path


def test_staffing_privacy_oracle_requires_exact_ast_anchors() -> None:
    projection = (STAFFING_ROOT / "projection.py").read_text(encoding="utf-8")
    workflow = (STAFFING_ROOT / "workflow.py").read_text(encoding="utf-8")

    assert _staffing_privacy_violations(projection, Path("projection.py")) == ()
    assert _staffing_privacy_violations(workflow, Path("workflow.py")) == ()

    without_approved_literal = projection.replace('"api_key", ', "", 1)
    assert _staffing_privacy_violations(
        without_approved_literal, Path("projection.py")
    )

    with_leak = workflow + "\nleaked_api_key = True\n"
    assert _staffing_privacy_violations(with_leak, Path("workflow.py"))

    decoded_leaks = (
        projection + '\nleak = "api_" "key"\n',
        projection + '\nleak = "\\x61uthorization"\n',
        projection.replace('"api_key"', '"api_" "key"', 1),
    )
    for source in decoded_leaks:
        assert _staffing_privacy_violations(source, Path("projection.py")), source

    anchor_mutations = (
        "FORBIDDEN_KEYS |= frozenset()",
        "FORBIDDEN_KEYS = frozenset()",
        "(FORBIDDEN_KEYS := frozenset())",
        "del FORBIDDEN_KEYS",
        'globals()["FORBIDDEN_KEYS"] = frozenset()',
        'locals()["FORBIDDEN_KEYS"] = frozenset()',
    )
    for mutation in anchor_mutations:
        source = projection + f"\n{mutation}\n"
        assert _staffing_privacy_violations(source, Path("projection.py")), mutation
