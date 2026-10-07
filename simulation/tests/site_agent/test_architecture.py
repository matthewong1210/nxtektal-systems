"""Mechanical dependency and safety guards for ``nxt_site_agent``."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = SIMULATION_ROOT / "nxt_site_agent"

# The service shell composes existing downstream public surfaces only.
ALLOWED_FIRST_PARTY_MODULES = {
    "nxt_agent_runtime",
    "nxt_pilot_ops.contracts",
    "nxt_pilot_ops.ledger",
    "nxt_pilot_ops.serialization",
    "nxt_workflow_enablement",
}

# Whitelist, not blacklist: every stdlib import root the package may
# use.  ``http``/``threading`` exist for the loopback-only server;
# everything else (os, time, uuid, socket, subprocess, urllib, ...)
# is banned by omission.
ALLOWED_STDLIB_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "http",
    "json",
    "math",
    "pathlib",
    "threading",
    "typing",
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
)

FOREIGN_SURFACE_TOKENS = (
    "operational-replay",
    "operational_replay",
    "roi-engine",
    "roi_engine",
    "nxtektal-roi",
)

LLM_PATTERNS = (
    r"\bopenai",
    r"\banthropic",
    r"\bkimi",
    r"\bmoonshot",
    r"\bprovider",
    r"\bllm\b",
    r"\blangchain\b",
    r"\bprompt\b",
    r"\bcompletion\b",
    r"\bgenerative\b",
)

# The service may not mention any other first-party package: canonical
# semantics reach it only through its three approved import surfaces,
# and everything else arrives as plain data from composition roots.
BANNED_FIRST_PARTY_MENTIONS = (
    "nxt_sim",
    "nxt_range_ops",
    "nxt_range_agent",
    "nxt_facility",
    "nxt_memory",
    "nxt_telemetry",
    "nxt_range_twin",
    "nxt_range_viewer",
    "nxt_range_demo",
    "nxt_commissioning",
    "nxt_site_runtime",
    "nxt_edge_observation",
    # Course spatial truth is an independent sibling; the service shows
    # only Range Operations projections and never touches the map layer.
    "nxt_course_world_model",
    "nxt_edge_task",
    "nxt_model_gateway",
)

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
    "nxt_edge_task",
    "nxt_model_gateway",
)

BANNED_CALL_NAMES = {
    "now",
    "utcnow",
    "today",
    "monotonic",
    "perf_counter",
    "uuid1",
    "uuid4",
    "random",
    "randint",
    "getenv",
}

SERVICE_SCRIPTS = (
    "scripts/site_agent_fixture.py",
    "scripts/site_agent_demo.py",
    "scripts/course_collection_execution_service.py",
    "scripts/course_collection_execution_v4_service.py",
    "scripts/task_ops_service_capabilities.py",
)

COLLECTION_EXECUTION_SERVICE = (
    SIMULATION_ROOT / "scripts" / "course_collection_execution_service.py"
)
CONTINUOUS_COLLECTION_EXECUTION_SERVICE = (
    SIMULATION_ROOT / "scripts" / "course_collection_execution_v4_service.py"
)
CONTINUOUS_COLLECTION_EXECUTION_SERVICE_RELATIVE = (
    "scripts/course_collection_execution_v4_service.py"
)
STAFFING_COMPOSITION = SIMULATION_ROOT / "scripts" / "staffing_operations.py"
STAFFING_COMPOSITION_RELATIVE = "scripts/staffing_operations.py"

SCRIPT_BANNED_IMPORT_ROOTS = {
    "rclpy",
    "rospy",
    "serial",
    "pyserial",
    "pymodbus",
    "minimalmodbus",
    "paho",
    "asyncua",
    "opcua",
    "kafka",
    "confluent_kafka",
    "pika",
    "socket",
    "ssl",
    "urllib",
    "requests",
    "os",
    "subprocess",
    "multiprocessing",
    "time",
    "uuid",
    "random",
    "secrets",
}

STAFFING_BANNED_IMPORT_ROOTS = {
    "aiohttp",
    "anthropic",
    "google",
    "http",
    "langchain",
    "moonshot",
    "openai",
    "os",
    "requests",
    "secrets",
    "socket",
    "ssl",
    "time",
}

STAFFING_ALLOWED_STDLIB_TARGETS = {
    "__future__.annotations",
    "collections.abc.Callable",
    "collections.abc.Mapping",
    "collections.abc.Sequence",
    "collections.deque",
    "copy.deepcopy",
    "dataclasses.dataclass",
    "dataclasses.field",
    "datetime.date",
    "datetime.datetime",
    "datetime.timedelta",
    "datetime.timezone",
    "hashlib",
    "pathlib.Path",
    "re",
    "threading",
    "types.MappingProxyType",
    "typing.Any",
    "typing.Literal",
    "typing.TypeVar",
    "urllib.parse.unquote",
    "zoneinfo.ZoneInfo",
}

STAFFING_ALLOWED_GATEWAY_TARGETS = {
    "nxt_model_gateway.AttemptObserverError",
    "nxt_model_gateway.AttemptRecord",
    "nxt_model_gateway.AttemptStarted",
    "nxt_model_gateway.DeploymentRegion",
    "nxt_model_gateway.FailureCode",
    "nxt_model_gateway.GatewayContractError",
    "nxt_model_gateway.GenerationMessage",
    "nxt_model_gateway.GenerationRequest",
    "nxt_model_gateway.GenerationResult",
    "nxt_model_gateway.GenerationStatus",
    "nxt_model_gateway.MessageRole",
    "nxt_model_gateway.ModelGateway",
    "nxt_model_gateway.Provider",
    "nxt_model_gateway.ProviderConfig",
    "nxt_model_gateway.RoutePolicy",
    "nxt_model_gateway.RouteReadiness",
    "nxt_model_gateway.RouteReadinessStatus",
    "nxt_model_gateway.stable_digest",
    "nxt_model_gateway.anthropic.AnthropicAdapter",
    "nxt_model_gateway.kimi.KimiAdapter",
    "nxt_model_gateway.openai.OpenAIAdapter",
    "nxt_model_gateway.transport.StdlibHttpsTransport",
}

STAFFING_ALLOWED_DOMAIN_TARGETS = {
    "nxt_pilot_ops.staffing.contracts.AssignmentProjection",
    "nxt_pilot_ops.staffing.contracts.AttemptFinishedEvidence",
    "nxt_pilot_ops.staffing.contracts.AttemptRouteEvidence",
    "nxt_pilot_ops.staffing.contracts.AttemptStartedEvidence",
    "nxt_pilot_ops.staffing.contracts.CandidateOperationProjection",
    "nxt_pilot_ops.staffing.contracts.CandidateProjection",
    "nxt_pilot_ops.staffing.contracts.CommittedReceipt",
    "nxt_pilot_ops.staffing.contracts.ConflictReceipt",
    "nxt_pilot_ops.staffing.contracts.CoverageGap",
    "nxt_pilot_ops.staffing.contracts.DateProjection",
    "nxt_pilot_ops.staffing.contracts.DuplicateReceipt",
    "nxt_pilot_ops.staffing.contracts.ExceptionCommittedProjection",
    "nxt_pilot_ops.staffing.contracts.ExceptionProjection",
    "nxt_pilot_ops.staffing.contracts.GenerationCommittedProjection",
    "nxt_pilot_ops.staffing.contracts.GenerationProjectionView",
    "nxt_pilot_ops.staffing.contracts.GenerationRouteEvidence",
    "nxt_pilot_ops.staffing.contracts.ManagerCommittedProjection",
    "nxt_pilot_ops.staffing.contracts.ManagerResponseProjection",
    "nxt_pilot_ops.staffing.contracts.ReceiptResult",
    "nxt_pilot_ops.staffing.contracts.RequestProjection",
    "nxt_pilot_ops.staffing.contracts.ResultEvidence",
    "nxt_pilot_ops.staffing.contracts.RosterCommittedProjection",
    "nxt_pilot_ops.staffing.contracts.StaffingError",
    "nxt_pilot_ops.staffing.ledger.StaffingLedger",
    "nxt_pilot_ops.staffing.operations.StaffingOperations",
    "nxt_pilot_ops.staffing.projection.GenerationProjection",
    "nxt_pilot_ops.staffing.projection.ProviderOutputDiagnostic",
    "nxt_pilot_ops.staffing.projection.STAFFING_SUGGESTION_OUTPUT_SCHEMA",
    "nxt_pilot_ops.staffing.projection.canonical_generation_input",
    "nxt_pilot_ops.staffing.projection.diagnose_provider_output",
    "nxt_pilot_ops.staffing.prompt.PROMPT_TEMPLATE_VERSION",
    "nxt_pilot_ops.staffing.prompt.SUPPORTED_PROMPT_TEMPLATE_VERSIONS",
}

STAFFING_BANNED_FIRST_PARTY_PREFIXES = (
    "nxt_edge_task",
    "nxt_sim",
    "nxt_range_ops",
    "nxt_range_agent",
    "nxt_range_twin",
    "nxt_course_world_model",
    "nxt_agent_runtime",
    "scripts.course_collection",
    "scripts.course_session",
    "scripts.edge_task",
)

PROVIDER_ENDPOINT_TOKENS = (
    "api.openai.com",
    "api.anthropic.com",
    "api.moonshot.cn",
    "api.moonshot.ai",
)

ENDPOINT_OVERRIDE_NAMES = {
    "api_base",
    "base_url",
    "endpoint",
    "endpoint_url",
    "host",
}

DIRECT_NETWORK_CALL_PREFIXES = (
    "aiohttp.",
    "http.",
    "httpx.",
    "requests.",
    "socket.",
    "ssl.",
    "urllib.request.",
)

DIRECT_PROVIDER_CALL_PREFIXES = (
    "anthropic.",
    "google.generativeai.",
    "moonshot.",
    "openai.",
)

DIRECT_EXECUTION_CALL_PREFIXES = (
    "edge.",
    "robot.",
    "simulator.",
)

NON_PRODUCTION_TOOL_SCRIPTS = {
    "scripts/inspect_environment.py",
}


def _package_files() -> list[Path]:
    files = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert files, "nxt_site_agent sources not found"
    return files


def _llm_provider_surface_violations(source: str) -> list[str]:
    lowered = source.lower()
    return [
        pattern
        for pattern in LLM_PATTERNS
        if re.search(pattern, lowered) is not None
    ]


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


def _dynamic_import_aliases(tree: ast.AST) -> set[str]:
    aliases = {"__import__", "import_module"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module == "importlib":
            selected = "import_module"
        elif node.module == "builtins":
            selected = "__import__"
        else:
            continue
        for alias in node.names:
            if alias.name == selected:
                aliases.add(alias.asname or alias.name)
    return aliases


def _dynamic_import(
    call: ast.Call, aliases: set[str]
) -> tuple[bool, str | None]:
    is_dynamic = (
        isinstance(call.func, ast.Name)
        and call.func.id in aliases
    ) or (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in {"__import__", "import_module"}
    )
    if not is_dynamic:
        return False, None
    target = None
    if (
        call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    ):
        target = call.args[0].value
    return True, target


def _import_targets(source: str, *, relative: str | None = None) -> set[str]:
    tree = ast.parse(source)
    dynamic_aliases = _dynamic_import_aliases(tree)
    package_parts: tuple[str, ...] = ()
    if relative is not None:
        module_parts = tuple(Path(relative).with_suffix("").parts)
        package_parts = module_parts[:-1]
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module is None:
                    continue
                base = node.module
            else:
                if relative is None or node.level > len(package_parts):
                    continue
                retained = package_parts[: len(package_parts) - node.level + 1]
                suffix = () if node.module is None else tuple(node.module.split("."))
                base = ".".join((*retained, *suffix))
            modules.update(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
            )
        elif isinstance(node, ast.Call):
            is_dynamic, target = _dynamic_import(node, dynamic_aliases)
            if is_dynamic and target is not None:
                modules.add(target)
    return modules


def _dynamic_import_violations(source: str) -> list[str]:
    tree = ast.parse(source)
    dynamic_aliases = _dynamic_import_aliases(tree)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root == "builtins":
                    violations.append(f"builtins import {alias.name}")
                if root == "importlib" and not (
                    alias.name == "importlib.metadata"
                    and alias.asname is None
                    and node in tree.body
                    and len(node.names) == 1
                ):
                    violations.append(f"unsafe importlib import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in {"builtins", "importlib"}:
                violations.append(f"unsafe from-import {node.module}")
        elif isinstance(node, ast.Name) and node.id in dynamic_aliases:
            violations.append(f"dynamic import name {node.id}")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in {"__import__", "import_module"}
        ):
            violations.append(f"dynamic import attribute {node.attr}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec"}
        ):
            violations.append(f"dynamic source call {node.func.id}")
    return violations


def _dotted_expression(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _assignment_target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Attribute):
        return [target.attr]
    if isinstance(target, ast.Subscript):
        key = target.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return [key.value]
        return []
    if isinstance(target, (ast.List, ast.Tuple)):
        return [
            name
            for item in target.elts
            for name in _assignment_target_names(item)
        ]
    return []


def _repo_source_python_files() -> list[Path]:
    roots = [SIMULATION_ROOT / "scripts"]
    roots.extend(
        path
        for path in SIMULATION_ROOT.iterdir()
        if path.is_dir() and path.name.startswith("nxt_")
    )
    return [
        path
        for root in roots
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def _production_python_files() -> list[Path]:
    return [
        path
        for path in _repo_source_python_files()
        if path.relative_to(SIMULATION_ROOT).as_posix()
        not in NON_PRODUCTION_TOOL_SCRIPTS
    ]


def _production_source_texts() -> dict[str, str]:
    return {
        path.relative_to(SIMULATION_ROOT).as_posix(): path.read_text(
            encoding="utf-8"
        )
        for path in _production_python_files()
    }


def _owner_importers_in_sources(
    prefix: str, sources: dict[str, str]
) -> set[str]:
    importers = set()
    owner_path = prefix.replace(".", "/")
    for relative, source in sources.items():
        if relative == f"{owner_path}.py" or relative.startswith(
            f"{owner_path}/"
        ):
            continue
        targets = _import_targets(source, relative=relative)
        if any(
            target == prefix or target.startswith(f"{prefix}.")
            for target in targets
        ):
            importers.add(relative)
    return importers


def _staffing_owner_boundary_violations(
    sources: dict[str, str],
) -> list[str]:
    gateway_importers = _owner_importers_in_sources(
        "nxt_model_gateway", sources
    )
    staffing_domain_importers = _owner_importers_in_sources(
        "nxt_pilot_ops.staffing", sources
    )
    composition_importers = _owner_importers_in_sources(
        "scripts.staffing_operations", sources
    )
    expected_owner = {STAFFING_COMPOSITION_RELATIVE}
    expected_composition_importer = {
        CONTINUOUS_COLLECTION_EXECUTION_SERVICE_RELATIVE
    }
    violations: list[str] = []

    if gateway_importers != expected_owner:
        violations.append(f"gateway importers: {sorted(gateway_importers)!r}")
    if staffing_domain_importers != expected_owner:
        violations.append(
            "staffing-domain importers: "
            f"{sorted(staffing_domain_importers)!r}"
        )
    dual_owner_importers = gateway_importers & staffing_domain_importers
    if dual_owner_importers != expected_owner:
        violations.append(
            "dual-owner importers: "
            f"{sorted(dual_owner_importers)!r}"
        )
    package_gateway_importers = {
        relative
        for relative in gateway_importers
        if relative.split("/", 1)[0].startswith("nxt_")
    }
    if package_gateway_importers:
        violations.append(
            "package gateway importers: "
            f"{sorted(package_gateway_importers)!r}"
        )
    if composition_importers != expected_composition_importer:
        violations.append(
            "composition importers: "
            f"{sorted(composition_importers)!r}"
        )

    v4_source = sources.get(CONTINUOUS_COLLECTION_EXECUTION_SERVICE_RELATIVE)
    if v4_source is None:
        violations.append("V4 composition source missing")
    else:
        v4_targets = _import_targets(
            v4_source,
            relative=CONTINUOUS_COLLECTION_EXECUTION_SERVICE_RELATIVE,
        )
        if "scripts.staffing_operations" not in v4_targets:
            violations.append("V4 composition import missing")
        if any(
            target == "nxt_model_gateway"
            or target.startswith("nxt_model_gateway.")
            or target == "nxt_pilot_ops.staffing"
            or target.startswith("nxt_pilot_ops.staffing.")
            for target in v4_targets
        ):
            violations.append("V4 imports owner")
    return violations


def _staffing_composition_violations(source: str) -> list[str]:
    tree = ast.parse(source)
    violations = _dynamic_import_violations(source)
    for module in sorted(_import_targets(source)):
        root = module.split(".")[0]
        parts = module.split(".")
        allowed = module in STAFFING_ALLOWED_STDLIB_TARGETS
        if root == "nxt_model_gateway":
            allowed = module in STAFFING_ALLOWED_GATEWAY_TARGETS
        elif module.startswith("nxt_pilot_ops.staffing."):
            allowed = module in STAFFING_ALLOWED_DOMAIN_TARGETS
        elif module == "nxt_site_agent.SiteAgentError":
            allowed = True
        if module.endswith(".*") or any(
            part.startswith("_") for part in parts[1:]
        ):
            allowed = False
        if not allowed:
            violations.append(f"unapproved import {module}")
        if root in STAFFING_BANNED_IMPORT_ROOTS:
            violations.append(f"banned import {module}")
        if module.startswith(STAFFING_BANNED_FIRST_PARTY_PREFIXES):
            violations.append(f"execution import {module}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if "http://" in lowered or "https://" in lowered:
                violations.append("direct endpoint literal")
            for token in PROVIDER_ENDPOINT_TOKENS:
                if token in lowered:
                    violations.append(f"provider endpoint {token}")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _assignment_target_names(target):
                    if name.lower() in ENDPOINT_OVERRIDE_NAMES:
                        violations.append(
                            f"endpoint override assignment {name}"
                        )
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value.lower() in ENDPOINT_OVERRIDE_NAMES
                ):
                    violations.append(f"endpoint override key {key.value}")
        if isinstance(node, ast.Call):
            callee = _dotted_expression(node.func)
            if callee is not None and callee.startswith(
                DIRECT_NETWORK_CALL_PREFIXES
            ):
                violations.append(f"direct network call {callee}")
            if callee is not None and callee.startswith(
                DIRECT_PROVIDER_CALL_PREFIXES
            ):
                violations.append(f"direct provider SDK call {callee}")
            if callee is not None and callee.startswith(
                DIRECT_EXECUTION_CALL_PREFIXES
            ):
                violations.append(f"direct execution call {callee}")
            for keyword in node.keywords:
                if (
                    keyword.arg is not None
                    and keyword.arg.lower() in ENDPOINT_OVERRIDE_NAMES
                ):
                    violations.append(f"endpoint override {keyword.arg}")
    for token in EXECUTION_TOKENS:
        if token in source:
            violations.append(f"execution token {token}")
    return violations


def _privileged_v4_stdlib_violations(source: str) -> list[str]:
    tree = ast.parse(source)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    violations = _dynamic_import_violations(source)
    privileged = {"os", "time", "secrets"}
    import_counts = {name: 0 for name in privileged}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in privileged:
                    continue
                allowed = (
                    node in tree.body
                    and len(node.names) == 1
                    and alias.name == root
                    and alias.asname is None
                )
                if allowed:
                    import_counts[root] += 1
                else:
                    violations.append(f"privileged import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in privileged:
                violations.append(f"privileged from-import {node.module}")
    for name, count in import_counts.items():
        if count != 1:
            violations.append(f"privileged import {name} count {count}")

    counts = {"os.environ": 0, "time.monotonic": 0, "secrets.token_bytes": 0}
    allowed_module_loads: set[ast.Name] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        expression = f"{node.value.id}.{node.attr}"
        if node.value.id not in privileged:
            continue
        parent = parents.get(node)
        if expression == "os.environ":
            allowed = (
                isinstance(parent, ast.Call)
                and parent.args
                and parent.args[0] is node
                and isinstance(parent.func, ast.Attribute)
                and isinstance(parent.func.value, ast.Name)
                and parent.func.value.id == "staffing_operations"
                and parent.func.attr == "load_provider_settings"
                and len(parent.args) == 1
                and {
                    keyword.arg for keyword in parent.keywords
                }
                == {
                    "region",
                    "language",
                    "kimi_model",
                    "openai_model",
                    "anthropic_model",
                }
            )
        elif expression == "time.monotonic":
            allowed = (
                isinstance(parent, ast.keyword)
                and parent.arg == "monotonic"
                and parent.value is node
                and isinstance(parents.get(parent), ast.Call)
                and isinstance(parents[parent].func, ast.Attribute)
                and isinstance(parents[parent].func.value, ast.Name)
                and parents[parent].func.value.id == "staffing_operations"
                and parents[parent].func.attr == "build_staffing_operations"
            )
        elif expression == "secrets.token_bytes":
            call = parent
            lambda_node = parents.get(call) if isinstance(call, ast.Call) else None
            keyword = parents.get(lambda_node) if isinstance(lambda_node, ast.Lambda) else None
            owner_call = parents.get(keyword) if isinstance(keyword, ast.keyword) else None
            allowed = (
                isinstance(call, ast.Call)
                and call.func is node
                and len(call.args) == 1
                and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == 32
                and not call.keywords
                and isinstance(lambda_node, ast.Lambda)
                and not lambda_node.args.args
                and lambda_node.body is call
                and getattr(keyword, "arg", None) == "nonce_factory"
                and isinstance(owner_call, ast.Call)
                and isinstance(owner_call.func, ast.Attribute)
                and isinstance(owner_call.func.value, ast.Name)
                and owner_call.func.value.id == "staffing_operations"
                and owner_call.func.attr == "build_staffing_operations"
            )
        else:
            allowed = False
        if allowed:
            counts[expression] += 1
            allowed_module_loads.add(node.value)
        else:
            violations.append(f"privileged expression {expression}")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in privileged
            and node not in allowed_module_loads
        ):
            violations.append(f"privileged module load {node.id}")
    for expression, count in counts.items():
        if count != 1:
            violations.append(f"{expression} count {count}")
    return violations


def test_service_imports_only_the_approved_surfaces():
    for path in _package_files():
        for module in _imports_of(path):
            root = module.split(".")[0]
            if root.startswith("nxt_"):
                assert module in ALLOWED_FIRST_PARTY_MODULES, (
                    f"{path.name} imports unapproved first-party "
                    f"module {module}"
                )
            else:
                assert root in ALLOWED_STDLIB_ROOTS, (
                    f"{path.name} imports non-whitelisted module {module}"
                )


def test_service_has_no_execution_or_foreign_surface_tokens():
    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for token in EXECUTION_TOKENS + FOREIGN_SURFACE_TOKENS:
            assert token not in text, f"{path.name} mentions {token!r}"


def test_service_has_no_llm_provider_or_generative_agent_surface():
    for path in _package_files():
        violations = _llm_provider_surface_violations(
            path.read_text(encoding="utf-8")
        )
        assert violations == [], (
            f"{path.name} matches banned patterns {violations!r}"
        )


@pytest.mark.parametrize(
    "source",
    (
        "provider = object()",
        "client = KimiClient()",
        "prompt = build_prompt()",
        "generative = True",
    ),
)
def test_site_agent_llm_guard_rejects_synthetic_provider_semantics(source):
    assert _llm_provider_surface_violations(source)


def test_service_never_mentions_other_first_party_packages():
    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for name in BANNED_FIRST_PARTY_MENTIONS:
            assert name not in text, f"{path.name} mentions {name!r}"


def test_no_wall_clock_uuid_or_randomness_calls_in_service_package():
    for path in _package_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = node.func
                name = None
                if isinstance(callee, ast.Attribute):
                    name = callee.attr
                elif isinstance(callee, ast.Name):
                    name = callee.id
                assert name not in BANNED_CALL_NAMES, (
                    f"{path.name} calls banned nondeterministic "
                    f"function {name}"
                )


def test_no_existing_package_depends_on_the_service_shell():
    offenders = []
    for package in OTHER_PACKAGES:
        package_root = SIMULATION_ROOT / package
        if not package_root.is_dir():
            continue
        for path in package_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "nxt_site_agent" in path.read_text(encoding="utf-8"):
                offenders.append(f"{package}/{path.name}")
    assert offenders == [], (
        "no existing package may depend on nxt_site_agent: "
        f"{offenders}"
    )


def test_service_scripts_import_no_transport_or_robot_stack():
    for relative in SERVICE_SCRIPTS:
        path = SIMULATION_ROOT / relative
        assert path.is_file(), f"{relative} is missing"
        for module in _imports_of(path):
            root = module.split(".")[0]
            if (
                relative == "scripts/course_collection_execution_v4_service.py"
                and root in {"os", "secrets", "time"}
            ):
                continue
            assert root not in SCRIPT_BANNED_IMPORT_ROOTS, (
                f"{relative} imports banned module {module}"
            )


def test_staffing_has_one_unique_gateway_domain_composition_point():
    assert _staffing_owner_boundary_violations(
        _production_source_texts()
    ) == []


def test_owner_import_inventory_detects_alternate_import_syntax():
    assert "nxt_model_gateway.openai" in _import_targets(
        "import nxt_model_gateway.openai as provider"
    )
    assert "nxt_pilot_ops.staffing" in _import_targets(
        "from nxt_pilot_ops import staffing as domain"
    )
    assert "nxt_model_gateway" in _import_targets(
        'provider = __import__("nxt_model_gateway")'
    )
    assert "nxt_model_gateway.transport" in _import_targets(
        'provider = importlib.import_module("nxt_model_gateway.transport")'
    )
    assert "nxt_model_gateway" in _import_targets(
        'import builtins\nprovider = builtins.__import__("nxt_model_gateway")'
    )
    assert "nxt_model_gateway.transport" in _import_targets(
        "from importlib import import_module as load\n"
        'provider = load("nxt_model_gateway.transport")'
    )


@pytest.mark.parametrize(
    ("relative", "source", "expected_violation"),
    (
        (
            "nxt_site_agent/gateway_leak.py",
            "from nxt_model_gateway import Provider\n",
            "package gateway importers",
        ),
        (
            "nxt_site_agent/staffing_leak.py",
            "from nxt_pilot_ops.staffing import StaffingError\n",
            "staffing-domain importers",
        ),
        (
            "scripts/alternate_staffing.py",
            "import nxt_model_gateway.openai\n"
            "from nxt_pilot_ops import staffing\n",
            "dual-owner importers",
        ),
        (
            "scripts/alternate_consumer.py",
            "from scripts import staffing_operations\n",
            "composition importers",
        ),
        (
            "scripts/alternate_consumer.py",
            "from . import staffing_operations\n",
            "composition importers",
        ),
        (
            "scripts/course_collection_execution_v4_service.py",
            "from scripts import staffing_operations\n"
            "from nxt_model_gateway import Provider\n",
            "V4 imports owner",
        ),
    ),
)
def test_staffing_owner_guard_rejects_synthetic_boundary_bypasses(
    relative, source, expected_violation
):
    sources = {
        "scripts/staffing_operations.py": (
            "from nxt_model_gateway import Provider\n"
            "from nxt_pilot_ops.staffing.operations import "
            "StaffingOperations\n"
        ),
        "scripts/course_collection_execution_v4_service.py": (
            "from scripts import staffing_operations\n"
        ),
        "nxt_site_agent/__init__.py": "from .api import SiteAgentService\n",
    }
    sources[relative] = source

    assert any(
        expected_violation in violation
        for violation in _staffing_owner_boundary_violations(sources)
    )


def test_nonproduction_dynamic_guard_exclusion_preserves_owner_inventory():
    repo_sources = {
        path.relative_to(SIMULATION_ROOT).as_posix()
        for path in _repo_source_python_files()
    }
    production_sources = {
        path.relative_to(SIMULATION_ROOT).as_posix()
        for path in _production_python_files()
    }
    assert NON_PRODUCTION_TOOL_SCRIPTS <= repo_sources
    assert NON_PRODUCTION_TOOL_SCRIPTS.isdisjoint(production_sources)


def test_production_sources_have_no_dynamic_import_bypass():
    offenders = {
        path.relative_to(SIMULATION_ROOT).as_posix(): violations
        for path in _production_python_files()
        if (
            violations := _dynamic_import_violations(
                path.read_text(encoding="utf-8")
            )
        )
    }
    assert offenders == {}


def test_only_top_level_unaliased_importlib_metadata_is_allowed():
    assert _dynamic_import_violations("import importlib.metadata") == []
    for source in (
        "import importlib.util",
        "import importlib.metadata as metadata",
        "def nested():\n    import importlib.metadata",
    ):
        assert _dynamic_import_violations(source)


def test_staffing_composition_retains_injected_io_time_and_execution_boundary():
    source = STAFFING_COMPOSITION.read_text(encoding="utf-8")
    assert _staffing_composition_violations(source) == []


@pytest.mark.parametrize(
    "source",
    (
        "from http import client",
        "import ssl",
        "import httpx",
        "import openai as provider_sdk",
        "from pathlib import os",
        "from nxt_model_gateway.openai import *",
        "from nxt_model_gateway.openai import _DEFAULT_ENDPOINT",
        "from nxt_model_gateway.transport import AlternateTransport",
        "from nxt_pilot_ops.staffing.contracts import UnlistedContract",
        'provider = __import__("nxt_model_gateway")',
        'provider = __import__("nxt_model_gateway.transport", '
        'fromlist=["_OPENAI_ENDPOINT"])',
        'provider = import_module("nxt_model_gateway.transport")',
        'import builtins\nprovider = builtins.__import__("nxt_model_gateway")',
        "from importlib import import_module as load\n"
        'provider = load("nxt_model_gateway.transport")',
        "from nxt_edge_task import EdgeGateway",
        'BASE_URL = "https://api.openai.com/v1"',
        'PROVIDER_URL = "https://provider.invalid/v1"',
        "BASE_URL = provider_host",
        'exec("import socket")',
        'settings = {"base_url": provider_host}',
        'settings["endpoint"] = provider_host',
        "socket.create_connection(address)",
        "openai.OpenAI(api_key=api_key)",
        "simulator.step()",
        'build_gateway(endpoint="https://provider.invalid")',
        "send_robot_command(payload)",
    ),
)
def test_staffing_composition_guard_rejects_network_provider_and_execution_bypasses(
    source,
):
    assert _staffing_composition_violations(source)


def test_continuous_v4_has_only_exact_privileged_stdlib_uses():
    source = CONTINUOUS_COLLECTION_EXECUTION_SERVICE.read_text(encoding="utf-8")
    assert _privileged_v4_stdlib_violations(source) == []


@pytest.mark.parametrize(
    ("injection", "expected_violation"),
    (
        (
            "value = os.getenv('OPENAI_API_KEY')",
            "privileged expression os.getenv",
        ),
        ("value = dict(os.environ)", "privileged expression os.environ"),
        ("value = getattr(os, 'environ')", "privileged module load os"),
        ("value = __import__('os')", "dynamic import name __import__"),
        (
            "import builtins\nvalue = builtins.__import__('os')",
            "builtins import builtins",
        ),
        (
            "from importlib import import_module as load\nvalue = load('time')",
            "unsafe from-import importlib",
        ),
        (
            "import importlib.util",
            "unsafe importlib import importlib.util",
        ),
        ("load = __import__", "dynamic import name __import__"),
        ("value = time.time()", "privileged expression time.time"),
        (
            "value = secrets.token_hex(32)",
            "privileged expression secrets.token_hex",
        ),
        (
            "value = secrets.token_bytes(16)",
            "privileged expression secrets.token_bytes",
        ),
    ),
)
def test_continuous_v4_privileged_stdlib_guard_rejects_broader_access(
    injection, expected_violation
):
    source = (
        CONTINUOUS_COLLECTION_EXECUTION_SERVICE.read_text(encoding="utf-8")
        + f"\n{injection}\n"
    )
    assert expected_violation in _privileged_v4_stdlib_violations(source)


@pytest.mark.parametrize(
    ("injection", "expected_violation"),
    (
        (
            "def nested_import():\n    import os as ambient",
            "privileged import os",
        ),
        ("from os import getenv", "privileged from-import os"),
        (
            "if True:\n    import time",
            "privileged import time",
        ),
        (
            "import secrets as entropy",
            "privileged import secrets",
        ),
    ),
)
def test_continuous_v4_privileged_imports_are_exact_and_top_level(
    injection, expected_violation
):
    source = (
        CONTINUOUS_COLLECTION_EXECUTION_SERVICE.read_text(encoding="utf-8")
        + f"\n{injection}\n"
    )
    assert expected_violation in _privileged_v4_stdlib_violations(source)


def test_continuous_v4_provider_environment_call_has_exact_keywords():
    source = CONTINUOUS_COLLECTION_EXECUTION_SERVICE.read_text(encoding="utf-8")
    needle = "anthropic_model=args.anthropic_model,\n"
    assert source.count(needle) == 1
    broadened = source.replace(
        needle,
        needle + "                    endpoint_override=args.openai_model,\n",
    )
    assert "privileged expression os.environ" in (
        _privileged_v4_stdlib_violations(broadened)
    )


def test_collection_execution_service_has_one_simulator_path_and_no_mock_or_physical_path():
    text = COLLECTION_EXECUTION_SERVICE.read_text(encoding="utf-8")
    assert text.count("CourseCollectionExecutionDemo(") == 1
    assert text.count("self.demo.advance_once()") == 1
    for token in (
        "MockRobotDevice",
        "PilotDispatchRuntime",
        "RobotTaskInterface",
        "HandoffController",
        "apply_directive(",
        "rclpy",
        "rospy",
        "--live",
        "--real-robot",
        "--hardware",
    ):
        assert token not in text, f"collection service mentions {token!r}"


def test_continuous_collection_service_has_one_v3_path_and_no_fixed_mock_physical_or_browser_path():
    text = CONTINUOUS_COLLECTION_EXECUTION_SERVICE.read_text(encoding="utf-8")
    assert text.count("course_session_v3.run(") == 1
    for token in (
        "CourseCollectionExecutionDemo",
        "CollectionExecutionServiceRuntime",
        ".advance_once(",
        "MockRobotDevice",
        "PilotDispatchRuntime",
        "RobotTaskInterface",
        "HandoffController",
        "apply_directive(",
        "RangeSimulation",
        "BallLedger",
        "selenium",
        "playwright",
        "http.client",
        "rclpy",
        "rospy",
        "--live",
        "--real-robot",
        "--hardware",
    ):
        assert token not in text, f"continuous collection service mentions {token!r}"


def _import_probe(blocked_roots: tuple[str, ...]) -> subprocess.CompletedProcess:
    probe = textwrap.dedent(
        f"""
        import importlib.abc
        import importlib.machinery
        import sys

        BLOCKED = {blocked_roots!r}

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                root = fullname.split(".")[0]
                if root in BLOCKED:
                    raise ImportError(f"blocked import: {{fullname}}")
                return None

        sys.meta_path.insert(0, Blocker())

        import nxt_site_agent

        surface = (
            nxt_site_agent.SiteAgentService,
            nxt_site_agent.SiteAgentApiServer,
            nxt_site_agent.ServiceStorage,
            nxt_site_agent.CompositionSeam,
            nxt_site_agent.SourceCursor,
        )
        print("imported", len(surface))
        """
    )
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=SIMULATION_ROOT,
        capture_output=True,
        text=True,
    )


def test_service_imports_without_simulation_or_robot_stack():
    # nxt_commissioning is deliberately absent from this blocklist: the
    # workflow-enablement surface the service verifies reports through
    # legitimately consumes commissioning's public contracts.
    result = _import_probe(
        (
            "simpy",
            "gymnasium",
            "numpy",
            "pandas",
            "pyarrow",
            "pxr",
            "rclpy",
            "rospy",
            "serial",
            "pymodbus",
            "nxt_sim",
            "nxt_range_ops",
            "nxt_range_twin",
            "nxt_range_viewer",
            "nxt_range_demo",
            "nxt_range_agent",
            "nxt_memory",
        )
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_import_blocker_negative_control():
    result = _import_probe(("nxt_agent_runtime",))
    assert result.returncode != 0
    assert "blocked import" in result.stderr


def test_service_is_registered_as_a_distribution_package():
    pyproject = (SIMULATION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "nxt_site_agent" in pyproject
