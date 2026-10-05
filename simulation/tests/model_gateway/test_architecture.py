"""Mechanical isolation of the provider-neutral network leaf."""

import ast
from pathlib import Path
import re
import textwrap
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
BANNED_CAPABILITY_NAMES = {
    "open", "open_code", "FileIO", "__import__", "eval", "exec", "compile", "getenv", "environ",
    "now", "utcnow", "today", "time", "time_ns", "uuid1", "uuid4",
    "random", "randint", "system", "popen",
}
BANNED_BUILTIN_NAMES = {
    "open", "__import__", "eval", "exec", "compile", "getattr", "setattr",
    "delattr", "vars", "globals", "locals", "__builtins__",
}
BANNED_CAPABILITY_NAMES |= BANNED_BUILTIN_NAMES
SENSITIVE_REFLECTION_ATTRIBUTES = {
    "__globals__", "__builtins__", "__dict__", "__code__", "__closure__",
    "__subclasses__", "__mro__", "__class__", "__bases__", "__base__",
    "__getattribute__", "__getattr__", "__func__", "__self__", "__wrapped__",
    "__reduce__", "__reduce_ex__", "__traceback__", "__context__", "__cause__",
    "tb_frame", "tb_next", "f_globals", "f_builtins", "f_locals", "f_back",
    "gi_frame", "gi_code", "cr_frame", "cr_code", "ag_frame", "ag_code",
    "mro", "cell_contents",
}
# An allowed root does not authorize its implementation's re-exported modules.
# Review this small surface when adding an import or a module-qualified member.
# In particular, io grants exactly the two HTTP parser wrappers, not file I/O.
ALLOWED_MODULE_MEMBERS = {
    "__future__": {"annotations"},
    "collections": {"abc"},
    "collections.abc": {"Callable", "Mapping"},
    "copy": {"copy", "deepcopy"},
    "dataclasses": {"dataclass", "field"},
    "enum": {"StrEnum"},
    "hashlib": {"sha256"},
    "http": {"client"},
    "http.client": {
        "HTTPException", "HTTPResponse", "HTTPSConnection", "IncompleteRead",
        "_MAXHEADERS", "_MAXLINE",
    },
    "io": {"RawIOBase", "BufferedReader"},
    "json": {"dumps", "loads"},
    "math": {"isfinite"},
    "re": {"compile", "error", "fullmatch", "match"},
    "socket": {"SOCK_STREAM", "gaierror", "getaddrinfo", "socket"},
    "ssl": {"SSLError", "create_default_context"},
    "threading": {"Event", "Thread"},
    "types": {"MappingProxyType"},
    "typing": {"Any", "NoReturn", "Protocol"},
    "jsonschema": {"Draft202012Validator", "exceptions"},
    "jsonschema.exceptions": {"SchemaError", "ValidationError"},
}
# Internal imports expose specific owned symbols, never Python module objects.
# Keep this separate from external namespaces: importing .transport does not
# authorize access to transport's socket/io/ssl implementation imports.
ALLOWED_RELATIVE_MEMBERS = {
    "contracts": {
        "AttemptObserver", "AttemptObserverError", "AttemptRecord", "AttemptStarted",
        "DeploymentRegion", "FailureCode", "GatewayContractError", "GenerationMessage",
        "GenerationRequest", "GenerationResult", "GenerationStatus", "MessageRole",
        "Provider", "ProviderConfig", "TokenUsage", "_digest", "_invalid",
        "_optional_metadata", "_outcome",
    },
    "serialization": {
        "_canonical_tree", "_freeze_json", "_validate_local_object_schema",
        "canonical_json", "decode_validated_json", "stable_digest",
    },
    "adapters": {
        "AdapterOutcome", "PreparedRequest", "ProviderAdapter", "_BaseAdapter",
        "_malformed", "_refused", "_usage",
    },
    "transport": {
        "HttpTransport", "HttpResponse", "ProviderEndpoint", "TransportFailure",
        "StdlibHttpsTransport", "_KIMI_ENDPOINT", "_OPENAI_ENDPOINT", "_ANTHROPIC_ENDPOINT",
    },
    "kimi": {"KimiAdapter"},
    "openai": {"OpenAIAdapter"},
    "anthropic": {"AnthropicAdapter"},
    "routing": {"ModelGateway", "RoutePolicy", "RouteReadiness", "RouteReadinessStatus"},
}
RELATIVE_IMPORT_ORIGINS = {
    f".{module}.{member}": f".{module}.{member}"
    for module, members in ALLOWED_RELATIVE_MEMBERS.items() for member in members
}
# Root-level concrete exports keep the defining symbol's provenance. Read only
# syntax, never execute/import gateway code or authorize a root module export.
for _node in ast.parse((PACKAGE_ROOT / "__init__.py").read_text()).body:
    if isinstance(_node, ast.ImportFrom) and _node.level == 1:
        for _alias in _node.names:
            _origin = f".{_node.module}.{_alias.name}"
            if _origin in RELATIVE_IMPORT_ORIGINS:
                RELATIVE_IMPORT_ORIGINS[f".{_alias.asname or _alias.name}"] = _origin


def _relative_enum_attributes() -> set[str]:
    """Permit declared enum values, not arbitrary internals of owned classes."""
    attributes = set()
    for module, members in ALLOWED_RELATIVE_MEMBERS.items():
        tree = ast.parse((PACKAGE_ROOT / f"{module}.py").read_text())
        for node in tree.body:
            if (isinstance(node, ast.ClassDef) and node.name in members
                    and any(isinstance(base, ast.Name) and base.id == "StrEnum"
                            for base in node.bases)):
                for statement in node.body:
                    if (isinstance(statement, ast.Assign)
                            and isinstance(statement.value, ast.Constant)
                            and isinstance(statement.value.value, str)):
                        attributes.update(
                            f".{module}.{node.name}.{target.id}"
                            for target in statement.targets if isinstance(target, ast.Name)
                        )
    return attributes


RELATIVE_ENUM_VALUES = _relative_enum_attributes()
RELATIVE_ENUM_TYPES = {name.rsplit(".", 1)[0] for name in RELATIVE_ENUM_VALUES}
RELATIVE_DATA_VALUES = RELATIVE_ENUM_VALUES | {
    ".transport._KIMI_ENDPOINT", ".transport._OPENAI_ENDPOINT", ".transport._ANTHROPIC_ENDPOINT",
}
RELATIVE_CLASS_NAMES = {
    f".{module}.{node.name}"
    for module, members in ALLOWED_RELATIVE_MEMBERS.items()
    for node in ast.parse((PACKAGE_ROOT / f"{module}.py").read_text()).body
    if isinstance(node, ast.ClassDef) and node.name in members
}
ALLOWED_QUALIFIED_NAMES = set(ALLOWED_MODULE_MEMBERS) | {
    f"{module}.{member}"
    for module, members in ALLOWED_MODULE_MEMBERS.items() for member in members
} | {"jsonschema.Draft202012Validator.check_schema"} | set(
    RELATIVE_IMPORT_ORIGINS.values()
) | RELATIVE_ENUM_VALUES
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
                module_prefix = f".{node.module}." if node.module else "."
                rejected.update(
                    module_prefix + alias.name for alias in node.names
                    if module_prefix + alias.name not in RELATIVE_IMPORT_ORIGINS
                )
                continue
            if node.level > 1:
                rejected.add("." * node.level + (node.module or ""))
                continue
            if node.module in ALLOWED_MODULE_MEMBERS:
                rejected.update(f"{node.module}.{alias.name}" for alias in node.names
                                if alias.name not in ALLOWED_MODULE_MEMBERS[node.module])
            modules = [node.module or ""]
        else:
            continue
        for module in modules:
            root = module.split(".")[0]
            if (root.startswith("nxt_") or root not in ALLOWED_STDLIB | ALLOWED_THIRD_PARTY
                    or module not in ALLOWED_MODULE_MEMBERS):
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


def forbidden_capabilities(source: str) -> set[str]:
    """Inspect references, not just calls; never import inspected source.

    Bind imports to qualified names and propagate direct aliases to a fixed
    point. Bindings are conservative unions across scopes/reassignments: local
    shadowing cannot erase a forbidden origin. Only approved paths propagate,
    so this finite analysis cannot grow indefinitely on cyclic assignments.
    Modules and internal capabilities may be qualified or directly aliased, not
    hidden in expressions this analysis cannot follow. Internal functions may
    be called directly; classes additionally have explicit declarative/type-test
    contexts. Endpoint instances and enum values are data, not namespace access.
    Reflection is banned independently of import/alias provenance.
    """
    rejected = forbidden_imports(source)
    tree = ast.parse(source)
    nodes = list(ast.walk(tree))
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
    shadowed_names = {
        node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    } | {
        node.name for node in nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    } | {node.arg for node in nodes if isinstance(node, ast.arg)}
    bindings: dict[str, set[str]] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                origin = alias.name if alias.asname else alias.name.split(".")[0]
                bindings.setdefault(name, set()).add(origin)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                prefix = "." * node.level + (f"{node.module}." if node.module else "")
                origin = prefix + alias.name
                bindings.setdefault(alias.asname or alias.name, set()).add(
                    RELATIVE_IMPORT_ORIGINS.get(origin, origin)
                )

    def origins(node: ast.AST) -> set[str]:
        if isinstance(node, ast.Name):
            return bindings.get(node.id, set())
        if isinstance(node, ast.Attribute):
            return {f"{name}.{node.attr}" for name in origins(node.value)}
        return set()

    def is_type_annotation(node: ast.AST) -> bool:
        """Recognize declarative annotation syntax, not arbitrary expressions."""
        child = node
        while (parent := parents.get(child)) is not None:
            if isinstance(parent, (ast.arg, ast.AnnAssign)) and parent.annotation is child:
                return True
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and parent.returns is child:
                return True
            if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.BitOr):
                child = parent
            elif isinstance(parent, ast.Tuple) and isinstance(parents.get(parent), ast.Subscript):
                child = parent
            elif isinstance(parent, ast.Subscript) and parent.slice is child:
                generic = parent.value
                builtin_generic = (
                    isinstance(generic, ast.Name) and generic.id in
                    {"tuple", "list", "dict", "set", "frozenset", "type"}
                    and generic.id not in shadowed_names and generic.id not in bindings
                )
                imported_generic = origins(generic)
                safe_generic = imported_generic and all(
                    name.startswith(("typing.", "collections.abc.")) for name in imported_generic
                )
                if not (builtin_generic or safe_generic):
                    return False
                child = parent
            else:
                return False
        return False

    def is_class_context(node: ast.AST) -> bool:
        parent = parents.get(node)
        if is_type_annotation(node):
            return True
        if isinstance(parent, ast.ClassDef) and node in parent.bases:
            return True
        if isinstance(parent, ast.ExceptHandler) and parent.type is node:
            return True
        if (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
                and parent.func.id in {"isinstance", "issubclass"}
                and parent.func.id not in shadowed_names and parent.func.id not in bindings
                and len(parent.args) == 2 and parent.args[1] is node and not parent.keywords):
            return True
        # Existing disposition coverage enumerates enum values; this creates
        # data rather than handing a class to a user-supplied callback.
        if (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
                and parent.func.id == "set" and parent.func.id not in shadowed_names
                and parent.func.id not in bindings and len(parent.args) == 1
                and parent.args[0] is node and not parent.keywords
                and origins(node) <= RELATIVE_ENUM_TYPES):
            return True
        return False

    aliases = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            targets, value = [node.target], node.value
        else:
            continue
        if value is not None and all(isinstance(target, ast.Name) for target in targets):
            aliases.append((targets, value))
    while True:
        changed = False
        for targets, value in aliases:
            paths = origins(value) & ALLOWED_QUALIFIED_NAMES
            for target in targets:
                existing = bindings.setdefault(target.id, set())
                if paths - existing:
                    existing.update(paths)
                    changed = True
        if not changed:
            break

    # A walrus may itself be passed/returned/embedded; tracking its target does
    # not authorize that expression's value to escape.
    alias_values = {value for _, value in aliases if isinstance(parents[value], (ast.Assign, ast.AnnAssign))}
    for node in nodes:
        if isinstance(node, ast.Attribute) and node.attr in SENSITIVE_REFLECTION_ATTRIBUTES:
            rejected.add(f"reflection: {node.attr}")
        if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
                and node.slice.value in SENSITIVE_REFLECTION_ATTRIBUTES):
            rejected.add(f"reflection: {node.slice.value}")
        if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(node.ctx, ast.Load):
            continue
        paths = origins(node)
        rejected.update(paths - ALLOWED_QUALIFIED_NAMES)
        name = node.id if isinstance(node, ast.Name) else node.attr
        parent = parents.get(node)
        direct_call = isinstance(parent, ast.Call) and parent.func is node
        forbidden_name = (
            name in BANNED_BUILTIN_NAMES if isinstance(node, ast.Name)
            else name in BANNED_CAPABILITY_NAMES
        )
        if (forbidden_name or direct_call and name in BANNED_CAPABILITY_NAMES) and paths != {"re.compile"}:
            rejected.add(name)
        qualified = isinstance(parent, ast.Attribute) and parent.value is node
        if paths & ALLOWED_MODULE_MEMBERS.keys():
            if not qualified and node not in alias_values:
                rejected.update(f"module escape: {path}" for path in paths)
        capabilities = {path for path in paths if path.startswith(".")} - RELATIVE_DATA_VALUES
        if capabilities and not (qualified or direct_call or node in alias_values):
            if not (capabilities <= RELATIVE_CLASS_NAMES and is_class_context(node)):
                rejected.update(f"capability escape: {path}" for path in capabilities)
    return rejected


@pytest.mark.parametrize("source", [
    "import io as streams; reader = streams.FileIO; reader('file')",
    "reader = open; reader('file')",
    "import io; writer = io.TextIOWrapper",
    "import io as streams; second = streams; writer = second.TextIOWrapper",
    "run = exec",
    "evaluate = eval",
    "load = __import__",
    "compile_source = compile",
    "from socket import os as settings; settings.environ['KEY']",
    "from socket import os as child; child.posix_spawn('x', [], {})",
    "from http.server import time as clock; clock.time()",
    "from http.server import time as clock; timer = clock.time",
    "from dataclasses import sys as runtime; runtime.modules",
    "from threading import _os as process; process.environ",
    "import socket as net; alias = net; settings = alias.os",
    "import http.server as server; clock = server.time",
    "from http import server; clock = server.time",
    "from socket import *",
    "import ssl as tls; raw = tls.os",
    "import io; first = second; second = io; first.FileIO",
    "import io; streams: object = io; streams.TextIOWrapper",
    "import io; (streams,) = (io,); streams.FileIO",
    "import socket; modules = [socket]; modules[0].os",
    "import io; getattr(io, 'FileIO')",
    "import io; reader = io; reader = reader.TextIOWrapper",
])
def test_production_capability_scan_rejects_references_and_reexports(source):
    assert forbidden_capabilities(source), source


@pytest.mark.parametrize("source", [
    "import re; pattern = re.compile('x')",
    "import io as streams; base = streams.RawIOBase; reader = streams.BufferedReader",
    "from io import RawIOBase, BufferedReader",
    "import re as patterns; factory = patterns.compile; factory('x')",
    "from re import compile; factory = compile; factory('x')",
    "from re import compile as factory; factory('x')",
    "from http import client as transport; base = transport.HTTPSConnection",
    "import io as streams; alias = streams; base = alias.RawIOBase",
    "system = 'prompt'; content = system",
])
def test_production_capability_scan_accepts_approved_references(source):
    assert forbidden_capabilities(source) == set()


@pytest.mark.parametrize("source", [
    "from .transport import socket as net; net.os.posix_spawn('example', ['example'], {})",
    "from .transport import io as streams; wrapper = streams.TextIOWrapper",
    "from . import transport as link; link.socket.os.posix_spawn('example', ['example'], {})",
    "from .transport import ssl as tls; hidden = tls.os",
    "from .serialization import json as data; module = data",
    "from .contracts import re as expressions; hidden = expressions.sys",
    "from . import serialization as codec; codec.json.loads('{}')",
    "from .transport import io as streams; alias = streams; wrapper = alias.TextIOWrapper",
    "from .transport import *",
    "from . import contracts; alias = contracts; hidden = alias.math",
    "from .serialization import canonical_json as encode; hidden = encode.__globals__",
    "from .contracts import Provider as Kind; hidden = Kind.__dict__",
])
def test_relative_imports_cannot_launder_modules_or_escape_symbols(source):
    assert forbidden_capabilities(source), source


@pytest.mark.parametrize("source", [
    "from .contracts import Provider as Kind; selected = Kind.KIMI",
    "from .serialization import canonical_json as encode; encode({'result': []})",
    "from .transport import _KIMI_ENDPOINT as endpoint; selected = endpoint",
    "from .transport import HttpTransport as Base; class_alias = Base",
    "from . import GenerationRequest as Request; contract = Request",
])
def test_relative_imports_keep_approved_concrete_symbols(source):
    assert forbidden_capabilities(source) == set()


@pytest.mark.parametrize("expression", [
    "alias, = (encode,); scope = alias.__globals__; reader = scope['__builtins__']['open']; reader('example', 'w')",
    "scope = getattr(encode, '__globals__')",
    "alias, = (encode,)",
    "saved = [encode]",
    "saved = {'encode': encode}",
    "saved = {encode}",
    "alias = [encode][0]",
    "consume(encode)",
    "consume(callback=encode)",
    "consume(alias := encode)",
    "def export():\n    return encode",
    "export = lambda: encode",
    "def capture(callback=encode):\n    return callback",
    "alias = encode if enabled else None",
    "alias = encode or fallback",
    "holder.callback = encode",
])
def test_internal_capabilities_cannot_escape_untracked_expressions(expression):
    source = "from .serialization import canonical_json as encode\n" + expression
    assert forbidden_capabilities(source), source


@pytest.mark.parametrize("attribute", [
    "__globals__", "__builtins__", "__dict__", "__code__", "__closure__",
    "__subclasses__", "__mro__", "__class__", "__bases__", "__base__",
    "__getattribute__", "__func__", "__self__", "__wrapped__",
    "mro", "cell_contents",
])
def test_reflective_attributes_are_banned_without_import_provenance(attribute):
    assert forbidden_capabilities(f"scope = unknown.{attribute}")


@pytest.mark.parametrize("builtin", ["getattr", "setattr", "delattr", "vars", "globals", "locals"])
def test_reflection_builtin_references_are_banned(builtin):
    assert forbidden_capabilities(f"reflect = {builtin}")


@pytest.mark.parametrize("source", [
    "from .serialization import canonical_json as encode; alias = encode; alias({'x': 1})",
    "from .transport import _KIMI_ENDPOINT as endpoint; post(endpoint=endpoint)",
    "from .contracts import Provider; consume(Provider.KIMI)",
    "from .contracts import ProviderConfig; valid = isinstance(value, ProviderConfig)",
    "from .contracts import ProviderConfig; holder: ProviderConfig | None = None",
    "from .adapters import _BaseAdapter\nclass Adapter(_BaseAdapter): pass",
    "from .contracts import FailureCode; codes = set(FailureCode)",
])
def test_internal_capability_allowed_contexts_remain_available(source):
    assert forbidden_capabilities(source) == set()


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
        assert not forbidden_capabilities(source), (path.name, forbidden_capabilities(source))
        assert not forbidden_tokens(source), (path.name, forbidden_tokens(source))


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


def test_isolated_wheel_ci_rejects_repository_provider_simulator_and_robot_stacks():
    workflow = (REPOSITORY_ROOT / ".github/workflows/verification.yml").read_text(
        encoding="utf-8"
    )
    step = workflow.split("- name: Install and import the wheel in isolation", 1)[1]
    inline = step.split("<<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    tree = ast.parse(textwrap.dedent(inline))
    assignments = {
        target.id: {
            item.value for item in ast.walk(node.value)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
    }
    expected_absent = {
        "nxt_range_agent", "nxt_range_viewer", "nxt_range_demo",
        "openai", "anthropic", "moonshot", "requests", "httpx",
        "simpy", "gymnasium", "pxr", "rclpy", "rospy",
    }
    assert assignments.get("absent") == expected_absent
    assert any(
        isinstance(node, ast.For)
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "absent"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "find_spec"
            for child in ast.walk(node)
        )
        and any(isinstance(child, ast.Assert) for child in ast.walk(node))
        for node in ast.walk(tree)
    )


def test_stable_contract_and_verification_are_registered():
    assert (SIMULATION_ROOT / "docs/model_gateway_v1.md").is_file()
    for relative in (".github/workflows/verification.yml", "docs/CI.md", ".agent/workflows/testing.md"):
        text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        assert "tests/model_gateway" in text, relative
        assert "tests/model_gateway/test_architecture.py" in text, relative
