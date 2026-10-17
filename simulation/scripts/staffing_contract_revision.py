"""Lockstep contract revision for the ``nxt-staffing/v1`` Manager API wire.

The service and the Console are built from one checkout. This module derives
one value, the contract revision, from the bytes of every source that gives the
wire its meaning (the staffing domain package, the composition root, the Site
Agent API module, the contract schema, and the Console's staffing decoder,
controller, actions, labels, time helpers, and components) together with every
first-party module those sources import and that carries staffing-facing
semantics (canonical serialization and digests, the provider gateway, the
Manager API error and envelope constants, the Console's shared transport and
UI primitives). Every request body and every snapshot or receipt carries the
value; the service refuses a write whose revision is absent or different, and
the Console refuses a snapshot or receipt whose revision is absent or
different. Two checkouts whose staffing semantics differ therefore differ on
the wire even when ``schema.json`` and the frozen examples did not change.

The source set is the manifest ``docs/contracts/staffing-v1/lockstep.json``:

- ``sources`` are the staffing implementation files and directories;
- ``dependencies`` are the first-party modules the set imports and that also
  enter the digest;
- ``boundaries`` are the first-party modules the set imports but that the
  digest deliberately stops at, each with a reason.

``check`` walks the first-party imports of the whole covered set on both sides
(Python ``import`` statements, Console relative imports) and reports any
reached module that is not classified, any listed dependency or boundary that
nothing reaches, and any module listed twice. A new import therefore cannot
escape the digest without a reviewed manifest change. This module, the Console
pin module, the examples, and every test are outside the set so that
rewriting a pin never changes the value it pins.

The pinned ``STAFFING_CONTRACT_REVISION`` below is the runtime value. It is
regenerated, together with the Console pin, the frozen examples, the lockstep
manifest, and both contract fingerprint pins, by::

    cd simulation
    uv run --no-sync python -B scripts/staffing_contract_revision.py --write

``--check`` reports every pin that disagrees with the recomputed value, every
example exchange whose ``contract_revision`` is absent or different, and every
dependency-guard finding, and exits non-zero. The Python and Console contract
suites run the same comparisons; CI never passes ``--write``. CRLF is folded to
LF before any file is hashed, so a checkout's line-ending setting moves neither
the revision nor the contract fingerprint.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

STAFFING_CONTRACT_REVISION = "0aa08a12af79cdce103b7f11057cac6c2b39b37a54bf054bbfb2a247e910bb1f"

LOCKSTEP_MANIFEST = "simulation/docs/contracts/staffing-v1/lockstep.json"
CONTRACT_DIRECTORY = "simulation/docs/contracts/staffing-v1"
PYTHON_PIN = "simulation/scripts/staffing_contract_revision.py"
CONSOLE_PIN = "apps/site-agent-console/lib/staffing-revision.ts"
PYTHON_FINGERPRINT_PIN = "simulation/tests/pilot_ops/test_staffing_wire_contract.py"
CONSOLE_FINGERPRINT_PIN = "apps/site-agent-console/tests/staffing-contract.test.ts"
MANIFEST_SCHEMA = "nxt-staffing-lockstep/v1"
SIMULATION_DIRECTORY = "simulation"
EXCHANGES_SCHEMA = "nxt-staffing-exchanges/v1"
ERRORS_SCHEMA = "nxt-staffing-errors/v1"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_REVISION_VALUE = re.compile(r'("contract_revision":\s*")[0-9a-f]{64}(")')
# Each pin pattern captures the whole tail of its line, carriage return
# included, so a rewrite preserves the checkout's line endings byte for byte.
_PYTHON_PIN = re.compile(r'^(STAFFING_CONTRACT_REVISION = ")[0-9a-f]{64}("\r?)$', re.MULTILINE)
_CONSOLE_PIN = re.compile(
    r'^(export const STAFFING_CONTRACT_REVISION = ")[0-9a-f]{64}(";\r?)$', re.MULTILINE
)
_PYTHON_FINGERPRINT = re.compile(
    r'^(STAFFING_CONTRACT_FINGERPRINT = ")[0-9a-f]{64}("\r?)$', re.MULTILINE
)
_CONSOLE_FINGERPRINT = re.compile(
    r'^(const STAFFING_CONTRACT_FINGERPRINT = ")[0-9a-f]{64}(";\r?)$', re.MULTILINE
)
_CONSOLE_SUFFIXES = (".ts", ".tsx")
_PYTHON_FIRST_PARTY_PREFIX = "nxt_"
_PYTHON_SCRIPTS_PACKAGE = "scripts"


class LockstepError(RuntimeError):
    """The lockstep manifest or one of its sources is unusable."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        raise LockstepError(f"{path} is outside the repository root {root}") from None


def _root(root: Path | None) -> Path:
    return (repository_root() if root is None else Path(root)).resolve()


def load_manifest(root: Path) -> dict[str, object]:
    root = _root(root)
    path = root / LOCKSTEP_MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LockstepError(f"unreadable lockstep manifest: {error}") from None
    if (
        type(manifest) is not dict
        or manifest.get("schema") != MANIFEST_SCHEMA
        or set(manifest) != {"schema", "contract_revision", "sources", "dependencies", "boundaries"}
        or type(manifest.get("contract_revision")) is not str
        or _HEX64.fullmatch(manifest["contract_revision"]) is None
    ):
        raise LockstepError("lockstep manifest has an unexpected shape")
    sources = manifest["sources"]
    if type(sources) is not dict or set(sources) != {"files", "directories"}:
        raise LockstepError("lockstep manifest sources have an unexpected shape")
    dependencies = manifest["dependencies"]
    if type(dependencies) is not dict or set(dependencies) != {"files"}:
        raise LockstepError("lockstep manifest dependencies have an unexpected shape")
    boundaries = manifest["boundaries"]
    if type(boundaries) is not list or any(
        type(entry) is not dict
        or set(entry) != {"path", "reason", "names"}
        or type(entry["path"]) is not str
        or type(entry["reason"]) is not str
        or not entry["reason"].strip()
        or type(entry["names"]) is not list
        or any(type(name) is not str or not name for name in entry["names"])
        for entry in boundaries
    ):
        raise LockstepError("lockstep manifest boundaries have an unexpected shape")
    return manifest


def _skipped(name: str) -> bool:
    return name.startswith(".") or name == "__pycache__"


def _walk(directory: Path, suffixes: tuple[str, ...], found: set[Path]) -> None:
    """Recursive, so a later subpackage or component folder cannot escape the set."""

    for child in sorted(directory.iterdir()):
        if _skipped(child.name):
            continue
        if child.is_symlink():
            raise LockstepError(f"symbolic link inside a covered directory: {child}")
        if child.is_dir():
            _walk(child, suffixes, found)
        elif child.is_file() and child.suffix in suffixes:
            found.add(child)


def source_bytes(path: Path) -> bytes:
    """File bytes with CRLF folded to LF, so a checkout's line-ending setting
    cannot move the revision or the fingerprint; every other byte counts."""

    return path.read_bytes().replace(b"\r\n", b"\n")


def _listed_file(root: Path, entry: object, label: str) -> Path:
    if type(entry) is not str or not entry:
        raise LockstepError(f"lockstep {label} entry is not a path")
    path = root / entry
    if not path.is_file():
        raise LockstepError(f"lockstep {label} is missing: {entry}")
    return path


def manifest_files(root: Path) -> dict[str, list[Path]]:
    """The manifest's three categories as sorted, existing repository paths.

    ``sources`` includes every file found under the listed directories;
    ``dependencies`` and ``boundaries`` are explicit files.
    """
    root = _root(root)

    manifest = load_manifest(root)
    sources = manifest["sources"]
    assert type(sources) is dict
    found: set[Path] = set()
    for entry in sources["files"]:
        found.add(_listed_file(root, entry, "source"))
    for entry in sources["directories"]:
        if (
            type(entry) is not dict
            or set(entry) != {"path", "suffixes"}
            or type(entry["path"]) is not str
            or type(entry["suffixes"]) is not list
            or not entry["suffixes"]
        ):
            raise LockstepError("lockstep directory entry has an unexpected shape")
        directory = root / entry["path"]
        if not directory.is_dir():
            raise LockstepError(f"lockstep directory is missing: {entry['path']}")
        _walk(directory, tuple(entry["suffixes"]), found)
    dependencies = manifest["dependencies"]
    assert type(dependencies) is dict
    boundaries = manifest["boundaries"]
    assert type(boundaries) is list
    key = lambda path: _relative(root, path)  # noqa: E731 - sort key
    return {
        "sources": sorted(found, key=key),
        "dependencies": sorted(
            {_listed_file(root, entry, "dependency") for entry in dependencies["files"]}, key=key
        ),
        "boundaries": sorted(
            {_listed_file(root, entry["path"], "boundary") for entry in boundaries}, key=key
        ),
    }


def lockstep_sources(root: Path) -> list[Path]:
    """Every file whose bytes enter the revision: the sources and the
    dependencies, as sorted repository paths."""
    root = _root(root)

    files = manifest_files(root)
    return sorted(set(files["sources"]) | set(files["dependencies"]), key=lambda path: _relative(root, path))


def lockstep_manifest_text(root: Path) -> str:
    """One ``<path> <sha256>`` line per covered file, in path order."""

    lines = []
    for path in lockstep_sources(root):
        lines.append(f"{_relative(root, path)} {hashlib.sha256(source_bytes(path)).hexdigest()}\n")
    return "".join(lines)


def compute_contract_revision(root: Path | None = None) -> str:
    root = _root(root)
    return hashlib.sha256(lockstep_manifest_text(root).encode("utf-8")).hexdigest()


ANY_NAME = "*"


class ImportEdge:
    """One import of ``target`` by a covered file with the names it takes:
    ``{"*"}`` for a whole module object, a set of attribute names for
    ``from m import a, b``, and the empty set for a package ``__init__`` that
    merely executes on the way to a deeper module."""

    __slots__ = ("target", "names")

    def __init__(self, target: Path, names: set[str]) -> None:
        self.target = target
        self.names = names


def _python_module_file(root: Path, module_parts: list[str]) -> Path | None:
    base = (root / SIMULATION_DIRECTORY).joinpath(*module_parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _package_file(base: Path, name: str) -> Path | None:
    for candidate in (base / f"{name}.py", base / name / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _python_edges(path: Path, root: Path) -> list[ImportEdge]:
    """First-party edges of a Python source: absolute ``nxt_*`` and
    ``scripts`` imports with every package ``__init__`` executed on the dotted
    path, every relative import, and, for ``from pkg import name``, the
    submodule or subpackage ``name`` when it exists. Dynamic imports are
    refused because they cannot be analysed."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as error:
        raise LockstepError(f"unparseable Python source {path}: {error}") from None
    edges: list[ImportEdge] = []

    def absolute(parts: list[str], names: set[str]) -> None:
        for depth in range(1, len(parts)):
            parent = _python_module_file(root, parts[:depth])
            if parent is not None and parent.name == "__init__.py":
                edges.append(ImportEdge(parent, set()))
        target = _python_module_file(root, parts)
        if target is None:
            raise LockstepError(f"unresolved first-party import in {path}: {'.'.join(parts)}")
        edges.append(ImportEdge(target, names))
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "importlib":
                    raise LockstepError(f"dynamic import in {path}: importlib cannot be analysed")
                if parts[0].startswith(_PYTHON_FIRST_PARTY_PREFIX) or parts[0] == _PYTHON_SCRIPTS_PACKAGE:
                    absolute(parts, {ANY_NAME})
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            parts = module.split(".") if module else []
            if parts and parts[0] == "importlib":
                raise LockstepError(f"dynamic import in {path}: importlib cannot be analysed")
            if node.level:
                package_dir = path.parent
                for _ in range(node.level - 1):
                    package_dir = package_dir.parent
                base = package_dir.joinpath(*parts)
                target = _package_file(base.parent, base.name) if parts else (
                    base / "__init__.py" if (base / "__init__.py").is_file() else None
                )
                if target is None and not parts:
                    # ``from . import name`` inside a namespace package: only the names resolve.
                    target = None
                elif target is None:
                    raise LockstepError(f"unresolved relative import in {path}: {'.' * node.level}{module}")
                attribute_names: set[str] = set()
                for alias in node.names:
                    submodule = _package_file(base if parts else package_dir, alias.name)
                    if submodule is not None:
                        edges.append(ImportEdge(submodule, {ANY_NAME}))
                    else:
                        attribute_names.add(alias.name)
                if target is not None:
                    edges.append(ImportEdge(target, attribute_names))
                elif attribute_names:
                    raise LockstepError(f"unresolved relative import in {path}: {'.' * node.level}{module}")
            elif parts and (
                parts[0].startswith(_PYTHON_FIRST_PARTY_PREFIX) or parts[0] == _PYTHON_SCRIPTS_PACKAGE
            ):
                attribute_names = set()
                for alias in node.names:
                    submodule = _python_module_file(root, parts + [alias.name])
                    if submodule is not None:
                        edges.append(ImportEdge(submodule, {ANY_NAME}))
                    else:
                        attribute_names.add(alias.name)
                absolute(parts, attribute_names)
        elif isinstance(node, ast.Call):
            callee = node.func
            if isinstance(callee, ast.Name) and callee.id == "__import__":
                raise LockstepError(f"dynamic import in {path}: __import__ cannot be analysed")
    return edges


_TSCONFIG_NAME = "tsconfig.json"


def _console_aliases(path: Path, root: Path) -> list[tuple[str, Path]]:
    """``compilerOptions.paths`` of the nearest tsconfig above ``path``, as
    (prefix, directory) pairs for patterns of the form ``alias/*`` mapped to
    one ``./dir/*`` target."""

    directory = path.parent
    while True:
        tsconfig = directory / _TSCONFIG_NAME
        if tsconfig.is_file():
            break
        if directory == root or directory.parent == directory:
            return []
        directory = directory.parent
    try:
        document = json.loads(tsconfig.read_text(encoding="utf-8"))
    except ValueError as error:
        raise LockstepError(f"unreadable {tsconfig}: {error}") from None
    options = document.get("compilerOptions", {}) if type(document) is dict else {}
    patterns = options.get("paths", {}) if type(options) is dict else {}
    aliases: list[tuple[str, Path]] = []
    for pattern, targets in patterns.items():
        if type(pattern) is not str or not pattern.endswith("/*") or type(targets) is not list or len(targets) != 1:
            raise LockstepError(f"unsupported path alias in {tsconfig}: {pattern!r}")
        target = targets[0]
        if type(target) is not str or not target.endswith("/*"):
            raise LockstepError(f"unsupported path alias target in {tsconfig}: {target!r}")
        aliases.append((pattern[:-1], (directory / target[:-2]).resolve()))
    return aliases


def _console_module_file(base: Path) -> Path | None:
    candidates = [Path(f"{base}{suffix}") for suffix in _CONSOLE_SUFFIXES]
    candidates += [base / f"index{suffix}" for suffix in _CONSOLE_SUFFIXES]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


# Statements are matched only at a statement start (a line start or after
# `;`, `{` or `}`) in text whose comments, template literals and
# regular-expression literals have been blanked, so a commented-out import,
# an import-like string, or a clause spanning a semicolon-less previous
# statement never yields an edge.
# A clause is one of the import/export forms the language allows (`*`,
# `* as ns`, `{ names }`, `Default`, `Default, { names }`, `Default, * as ns`,
# each optionally after `type`, with ASCII identifiers), so a statement never
# swallows the code of a semicolon-less statement before it.
_CONSOLE_CLAUSE = (
    r"(?:type\s+)?(?:\*\s*(?:as\s+[A-Za-z0-9_$]+)?|\{[^}]*\}"
    r"|[A-Za-z0-9_$]+(?:\s*,\s*(?:\{[^}]*\}|\*\s*as\s+[A-Za-z0-9_$]+))?)"
)
_CONSOLE_STATEMENT = re.compile(
    r'(?:^|[;{}])[ \t]*(?:import|export)\s+(?P<clause>' + _CONSOLE_CLAUSE + r')\s*from\s*["\'](?P<specifier>[^"\']+)["\']',
    re.MULTILINE,
)
_CONSOLE_SIDE_EFFECT = re.compile(
    r'(?:^|[;{}])[ \t]*import\s*["\'](?P<specifier>[^"\']+)["\']', re.MULTILINE
)
# A dynamic import or require whose first argument is a string literal (an
# ASCII boundary rather than `\b`, so a member such as `client.import(` and a
# word glued in prose never count); any other `import(`/`require(` call
# (a variable, a template, an expression) cannot be analysed and is refused.
_CONSOLE_DYNAMIC = re.compile(r'(?<![A-Za-z0-9_$.])(?:import|require)\(\s*["\'](?P<specifier>[^"\']+)["\']\s*[,)]')
_CONSOLE_DYNAMIC_UNANALYSABLE = re.compile(r'(?<![A-Za-z0-9_$.])(?:import|require)\((?!\s*["\'][^"\']+["\']\s*[,)])')
_ENTITY_NAME = re.compile(r"#(?:[0-9]+|[xX][0-9a-fA-F]+)|[A-Za-z][A-Za-z0-9]+")


# A `/` in code opens a regular-expression literal rather than dividing when
# it follows one of these characters, one of these keywords in code (not a
# member name or JSX prose), a logical `!`, the `>` of an arrow, or the start
# of the file; after an operand (an identifier, a literal, a closing bracket,
# a JSX `}` or `>`, a postfix `++`/`--` or a non-null `!`) it divides. A
# candidate that would close on a comment opener divides too, and one that
# meets a backtick first fails loud, so a misread never swallows a comment or
# a template's backtick.
_REGEX_AFTER_CHARS = frozenset("(,=:[&|?{;")
# The keywords a regular expression may follow in the middle of a line, and
# the two it may follow only at the start of a line; the other keywords never
# precede a regular-expression literal in practice but do occur as prose.
_EXPRESSION_KEYWORDS = frozenset(("return", "typeof", "yield", "await"))
_REGEX_AFTER_WORDS = _EXPRESSION_KEYWORDS | frozenset(("case", "else"))


def _is_word_char(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in ("_", "$"))


def _is_operand(char: str) -> bool:
    return _is_word_char(char) or char in (")", "]", "}")


def _keyword_opens(word: str, word_prefix: str, word_first: bool, prev_word: str) -> bool:
    """Whether the identifier ``word`` is a keyword after which a `/` opens a
    regular expression: one of the listed keywords, not a member name, and in
    code rather than JSX text, which it is when first on its line, after
    another keyword, or after something other than a word, `>` or `}`."""

    if word not in _REGEX_AFTER_WORDS or word_prefix == ".":
        return False
    if word_first:
        return True
    if word not in _EXPRESSION_KEYWORDS:
        return False
    return prev_word in _REGEX_AFTER_WORDS or not (_is_word_char(word_prefix) or word_prefix in (">", "}", "-"))


def _string_end(text: str, start: int) -> int | None:
    """Index of the quote closing the string literal opened at ``start``, or
    None when the line ends first: a string cannot span lines (unless a
    backslash continues it), so a quote without a close on its line is prose,
    such as an apostrophe in JSX text."""

    quote = text[start]
    j = start + 1
    length = len(text)
    while j < length:
        char = text[j]
        if char == "\\":
            j += 2
            continue
        if char == "\n":
            return None
        if char == quote:
            return j
        j += 1
    return None


def _entity_before(text: str, index: int) -> bool:
    """Whether the `;` at ``index`` closes an HTML entity such as `&nbsp;` or
    `&#160;`, which is JSX prose rather than a statement end."""

    j = index - 1
    while j >= 0 and (_is_word_char(text[j]) or text[j] == "#"):
        j -= 1
    if j < 0 or text[j] != "&" or (j > 0 and text[j - 1] == "&"):
        return False
    return _ENTITY_NAME.fullmatch(text[j + 1 : index]) is not None


def _regex_end(text: str, start: int) -> int | None:
    """Index of the `/` closing the regular-expression literal opened at
    ``start``, or None when the `/` divides instead because the line ends
    first or the closing `/` would begin a comment. A backtick before the
    close fails loud: whether the `/` is a regular expression matching a
    backtick or a misread division before a template literal, reading on
    would swallow the template's opening backtick and silently hide every
    statement up to the next one."""

    j = start + 1
    in_class = False
    length = len(text)
    while j < length:
        char = text[j]
        if char == "\\":
            j += 2
            continue
        if char == "\n":
            return None
        if char == "`":
            raise LockstepError(
                "a `/` before a backtick on the same line in a Console source: escape a backtick"
                " in a regular expression as \\x60 or move the template literal to its own line"
            )
        if in_class:
            if char == "]":
                in_class = False
        elif char == "[":
            in_class = True
        elif char == "/":
            return None if (text[j + 1] if j + 1 < length else "") in ("*", "/") else j
        j += 1
    return None


def _blank_console_noise(text: str) -> str:
    """The source with every comment, template literal and regular-expression
    literal replaced by spaces of the same length (newlines kept) and a leading
    byte-order mark replaced by a space, so that only code positions can match
    an import statement. Ordinary string literals, which must close on their
    line, are kept because an import specifier is one (a quote without a close
    on its line, or glued to a word or a closing bracket as in `Don't` or
    `{n}'s`, is prose); `${...}` expressions inside a template are kept as
    code.
    A `/` opens a regular expression after an operator or opening bracket, a
    keyword such as `return` in code rather than JSX prose, a logical `!`, the
    `>` of an arrow or the start of the file, and divides after an operand, so
    a JSX `/>` or prose slash, a member named like a keyword, a postfix `++` or
    a non-null `!` never opens one; a candidate closing on a comment opener
    divides too, and one meeting a backtick first fails loud. An unterminated
    block comment or template literal fails loud rather than silently blanking
    the rest of the file."""

    if text.startswith("\ufeff"):
        text = " " + text[1:]
    out: list[str] = []
    i = 0
    length = len(text)
    mode: list[str] = ["code"]
    brace_depth: list[int] = []
    prev = ""  # last significant code character; "" at the start, ")" after a literal
    prev2 = ""  # the significant code character before prev
    word = ""  # the identifier or keyword that ends at prev, else ""
    word_prefix = ""  # the significant code character before `word`
    word_first = True  # whether `word` is the first significant thing on its line
    prev_word = ""  # the identifier just before `word`, when only whitespace separates them
    joined = False  # whether the previous code character continues `word`
    line_first = True  # whether nothing significant has appeared on the current line
    arrow = False  # whether prev is the `>` of an arrow
    bang_opens = False  # whether the `!` at prev is a logical not, after which a `/` opens a regex
    while i < length:
        char = text[i]
        nxt = text[i + 1] if i + 1 < length else ""
        state = mode[-1]
        if state == "code":
            if char == "/" and nxt == "/":
                mode.append("line")
                out.append("  ")
                i += 2
                joined = False
                continue
            if char == "/" and nxt == "*":
                mode.append("block")
                out.append("  ")
                i += 2
                joined = False
                continue
            if char == "/":
                if word:
                    opens_regex = _keyword_opens(word, word_prefix, word_first, prev_word)
                elif prev == "":
                    opens_regex = True
                elif prev == ">":
                    opens_regex = arrow  # an arrow body; a JSX `/>` or prose slash never opens one
                elif prev in ("+", "-"):
                    opens_regex = prev2 != prev  # a postfix `++`/`--` is an operand
                elif prev == "!":
                    opens_regex = bang_opens
                else:
                    opens_regex = prev in _REGEX_AFTER_CHARS
                end = _regex_end(text, i) if opens_regex else None
                if end is not None:
                    out.append(" " * (end - i + 1))
                    i = end + 1
                    prev2, prev, word, joined, line_first, arrow = prev, ")", "", False, False, False
                    continue
            if char in ('"', "'") and not (i > 0 and (_is_word_char(text[i - 1]) or text[i - 1] in (")", "]", "}"))):
                end = _string_end(text, i)
                if end is not None:
                    out.append(text[i : end + 1])
                    i = end + 1
                    prev2, prev, word, joined, line_first, arrow = prev, ")", "", False, False, False
                    continue
            if char == "`":
                mode.append("template")
                out.append(" ")
                i += 1
                prev2, prev, word, joined, line_first, arrow = prev, ")", "", False, False, False
                continue
            if char == "}" and brace_depth and brace_depth[-1] == 0 and len(mode) > 1 and mode[-2] == "template":
                mode.pop()
                brace_depth.pop()
                out.append(" ")
                i += 1
                prev2, prev, word, joined, arrow = prev, ")", "", False, False
                continue
            if brace_depth:
                if char == "{":
                    brace_depth[-1] += 1
                elif char == "}":
                    brace_depth[-1] -= 1
            out.append(char)
            i += 1
            if _is_word_char(char):
                if not joined:
                    prev_word, word_prefix, word_first = word, prev, line_first
                word = word + char if joined else char
                prev2, prev, joined, line_first, arrow = prev, char, True, False, False
            elif char.isspace():
                joined = False
                if char == "\n":
                    line_first = True
            else:
                if char == "!":
                    # a logical not, unless the `!` asserts non-null after an operand
                    # (an identifier, a literal, a closing bracket, a JSX `}` or `>`)
                    bang_opens = (bool(word) and _keyword_opens(word, word_prefix, word_first, prev_word)) or not (
                        _is_operand(prev) or (prev == ">" and not arrow)
                    )
                arrow = char == ">" and prev == "="
                # the `;` of an HTML entity in JSX prose is an operand, not a statement end
                significant = ")" if char == ";" and _entity_before(text, i - 1) else char
                prev2, prev, word, joined, line_first = prev, significant, "", False, False
            continue
        if state == "line":
            if char == "\n":
                mode.pop()
                out.append(char)
                line_first = True
            else:
                out.append(" ")
            i += 1
            continue
        if state == "block":
            if char == "*" and nxt == "/":
                mode.pop()
                out.append("  ")
                i += 2
            else:
                out.append(char if char == "\n" else " ")
                if char == "\n":
                    line_first = True
                i += 1
            continue
        # template literal: blank everything except `${...}` expressions, which are code
        if char == "\\" and nxt:
            out.append("  ")
            i += 2
            continue
        if char == "`":
            mode.pop()
            out.append(" ")
            i += 1
            prev2, prev, word, joined, arrow = prev, ")", "", False, False
            continue
        if char == "$" and nxt == "{":
            mode.append("code")
            brace_depth.append(0)
            out.append("  ")
            i += 2
            prev2, prev, word, joined, arrow = prev, "(", "", False, False
            continue
        out.append(char if char == "\n" else " ")
        i += 1
    if "block" in mode:
        raise LockstepError("unterminated block comment in a Console source")
    if "template" in mode:
        raise LockstepError("unterminated template literal in a Console source")
    return "".join(out)


def _clause_names(clause: str) -> set[str]:
    """The names an import or export clause takes from its module."""

    clause = re.sub(r"^type\s+", "", clause.strip())
    names: set[str] = set()
    braces = re.search(r"\{(?P<inner>[^}]*)\}", clause)
    if braces:
        for item in braces.group("inner").split(","):
            item = re.sub(r"^type\s+", "", item.strip())
            if not item:
                continue
            names.add(re.split(r"\s+as\s+", item)[0].strip())
        clause = clause[: braces.start()] + clause[braces.end():]
    for item in clause.split(","):
        item = item.strip()
        if not item:
            continue
        if item.startswith("*"):
            names.add(ANY_NAME)
        else:
            names.add("default")
    return names


def _console_edges(path: Path, root: Path) -> list[ImportEdge]:
    """Edges of a Console source: relative specifiers and tsconfig path
    aliases, from ``import``/``export ... from`` statements (any number per
    line; comments, template literals and regular-expression literals are
    ignored), side-effect imports, and dynamic ``import()``; ``require`` is
    refused because it cannot be analysed."""

    try:
        text = _blank_console_noise(path.read_bytes().decode("utf-8").replace("\r\n", "\n"))
    except LockstepError as error:
        raise LockstepError(f"{error}: {path}") from None
    aliases = _console_aliases(path, root)
    edges: list[ImportEdge] = []

    def resolve(specifier: str) -> Path | None:
        if specifier.startswith("."):
            base = (path.parent / specifier).resolve()
        else:
            for prefix, directory in aliases:
                if specifier.startswith(prefix):
                    base = (directory / specifier[len(prefix):]).resolve()
                    break
            else:
                return None
        resolved = _console_module_file(base)
        if resolved is None:
            raise LockstepError(f"unresolved first-party import in {path}: {specifier}")
        return resolved

    for match in _CONSOLE_STATEMENT.finditer(text):
        target = resolve(match.group("specifier"))
        if target is not None:
            edges.append(ImportEdge(target, _clause_names(match.group("clause"))))
    for match in _CONSOLE_SIDE_EFFECT.finditer(text):
        target = resolve(match.group("specifier"))
        if target is not None:
            edges.append(ImportEdge(target, set()))
    for match in _CONSOLE_DYNAMIC.finditer(text):
        if match.group(0).startswith("require"):
            raise LockstepError(f"dynamic import in {path}: require cannot be analysed")
        target = resolve(match.group("specifier"))
        if target is not None:
            edges.append(ImportEdge(target, {ANY_NAME}))
    if _CONSOLE_DYNAMIC_UNANALYSABLE.search(text):
        raise LockstepError(f"dynamic import in {path}: a specifier that is not a string literal cannot be analysed")
    return edges


def import_edges(path: Path, root: Path) -> list[ImportEdge]:
    if path.suffix == ".py":
        return _python_edges(path, root)
    if path.suffix in _CONSOLE_SUFFIXES:
        return _console_edges(path, root)
    return []


def first_party_imports(path: Path, root: Path) -> set[Path]:
    return {edge.target for edge in import_edges(path, _root(root))}


def dependency_problems(root: Path) -> list[str]:
    """The dependency guard: walk first-party imports from the sources
    through the dependencies, stopping at boundaries, and report every module
    the walk reaches that the manifest does not classify, every dependency or
    boundary nothing reaches, every name taken from a boundary that its entry
    does not declare, every module listed under two categories, and every
    dynamic import."""

    root = _root(root)
    files = manifest_files(root)
    manifest = load_manifest(root)
    declared_names: dict[Path, set[str]] = {}
    for entry in manifest["boundaries"]:  # type: ignore[union-attr]
        declared_names[(root / entry["path"])] = set(entry["names"])  # type: ignore[index]
    classified: dict[Path, str] = {}
    problems: list[str] = []
    for category in ("sources", "dependencies", "boundaries"):
        for path in files[category]:
            if path in classified:
                problems.append(
                    f"{_relative(root, path)} is listed as both {classified[path]} and {category}"
                )
            classified[path] = category
    reached: set[Path] = set()
    seen: set[Path] = set()
    stack = list(files["sources"])
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            edges = import_edges(path, root)
        except LockstepError as error:
            problems.append(str(error))
            continue
        for edge in sorted(edges, key=lambda item: _relative(root, item.target)):
            category = classified.get(edge.target)
            if category is None:
                problems.append(
                    f"unlisted first-party dependency {_relative(root, edge.target)} "
                    f"imported by {_relative(root, path)}"
                )
                continue
            reached.add(edge.target)
            if category == "boundaries":
                allowed = declared_names.get(edge.target, set())
                undeclared = sorted(edge.names - allowed) if ANY_NAME not in allowed else []
                if undeclared:
                    problems.append(
                        f"boundary {_relative(root, edge.target)}: {_relative(root, path)} takes "
                        f"undeclared name(s) {', '.join(undeclared)}"
                    )
            elif edge.target not in seen:
                stack.append(edge.target)
    for category, singular in (("dependencies", "dependency"), ("boundaries", "boundary")):
        for path in files[category]:
            if path not in reached:
                problems.append(
                    f"stale {singular} {_relative(root, path)}: nothing in the covered set imports it"
                )
    return sorted(set(problems))


def contract_fingerprint(root: Path | None = None) -> str:
    """SHA-256 over ``schema.json`` followed by every example in name order,
    with CRLF folded to LF in each file."""

    root = _root(root)
    contract = root / CONTRACT_DIRECTORY
    digest = hashlib.sha256()
    digest.update(source_bytes(contract / "schema.json"))
    for path in sorted((contract / "examples").glob("*.json")):
        digest.update(source_bytes(path))
    return digest.hexdigest()


def example_paths(root: Path) -> list[Path]:
    root = _root(root)
    return sorted((root / CONTRACT_DIRECTORY / "examples").glob("*.json"))


def _revision_key_locations(value: object, prefix: str = "") -> list[str]:
    """JSON paths of every object that carries a ``contract_revision`` key."""

    found: list[str] = []
    if type(value) is dict:
        if "contract_revision" in value:
            found.append(prefix or "<root>")
        for key, child in value.items():
            found.extend(_revision_key_locations(child, f"{prefix}.{key}" if prefix else key))
    elif type(value) is list:
        for index, child in enumerate(value):
            found.extend(_revision_key_locations(child, f"{prefix}[{index}]"))
    return found


def example_revision_problems(root: Path, revision: str) -> list[str]:
    """Every applicable place in every frozen example, checked individually:
    each exchange's response ``data`` and, for a POST, its ``request`` must
    carry exactly ``revision``; an error example must carry none. A revision
    found in one example never vouches for another."""
    root = _root(root)

    problems: list[str] = []
    for path in example_paths(root):
        text = path.read_text(encoding="utf-8")
        try:
            document = json.loads(text)
        except ValueError as error:
            problems.append(f"{path.name}: not JSON ({error})")
            continue
        schema = document.get("schema") if type(document) is dict else None
        if schema == ERRORS_SCHEMA:
            if "contract_revision" in text:
                problems.append(f"{path.name}: an error example never carries contract_revision")
            continue
        if schema != EXCHANGES_SCHEMA:
            problems.append(f"{path.name}: unknown example document schema {schema!r}")
            continue
        exchanges = document.get("exchanges")
        if type(exchanges) is not list or not exchanges:
            problems.append(f"{path.name}: no exchanges")
            continue
        allowed_places = {
            f"exchanges[{index}].body.data" for index in range(len(exchanges))
        } | {
            f"exchanges[{index}].request"
            for index, exchange in enumerate(exchanges)
            if type(exchange) is dict and exchange.get("method") == "POST"
        }
        for location in _revision_key_locations(document):
            if location not in allowed_places:
                problems.append(
                    f"{path.name}: contract_revision at {location} is outside the applicable places"
                )
        for index, exchange in enumerate(exchanges):
            if type(exchange) is not dict:
                problems.append(f"{path.name}#{index}: exchange is not an object")
                continue
            label = f"{path.name}#{exchange.get('name', index)}"
            body = exchange.get("body")
            data = body.get("data") if type(body) is dict else None
            places = [("data", data)]
            if exchange.get("method") == "POST":
                places.append(("request", exchange.get("request")))
            for what, value in places:
                if type(value) is not dict:
                    problems.append(f"{label} {what}: not an object")
                elif "contract_revision" not in value:
                    problems.append(f"{label} {what}: contract_revision is missing")
                elif value["contract_revision"] != revision:
                    problems.append(
                        f"{label} {what}: contract_revision {value['contract_revision']!r} != {revision}"
                    )
    return problems


def _pinned(pattern: re.Pattern[str], text: str, label: str) -> str:
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise LockstepError(f"{label}: expected exactly one pin, found {len(matches)}")
    found = pattern.search(text)
    assert found is not None
    return found.group(0).rstrip("\r").rsplit('"', 2)[1]


def pinned_values(root: Path) -> dict[str, str]:
    root = _root(root)
    return {
        "python": _pinned(
            _PYTHON_PIN, (root / PYTHON_PIN).read_text(encoding="utf-8"), "python pin"
        ),
        "console": _pinned(
            _CONSOLE_PIN, (root / CONSOLE_PIN).read_text(encoding="utf-8"), "console pin"
        ),
        "manifest": str(load_manifest(root)["contract_revision"]),
    }


def pinned_fingerprints(root: Path) -> dict[str, str]:
    root = _root(root)
    return {
        "python": _pinned(
            _PYTHON_FINGERPRINT,
            (root / PYTHON_FINGERPRINT_PIN).read_text(encoding="utf-8"),
            "python fingerprint pin",
        ),
        "console": _pinned(
            _CONSOLE_FINGERPRINT,
            (root / CONSOLE_FINGERPRINT_PIN).read_text(encoding="utf-8"),
            "console fingerprint pin",
        ),
    }


def check(root: Path) -> list[str]:
    """Every finding: a dependency-guard problem, a pin that disagrees with the
    recomputed revision, an example place whose revision is absent or
    different, or a fingerprint pin that disagrees with the recomputed value."""
    root = _root(root)

    problems = dependency_problems(root)
    revision = compute_contract_revision(root)
    for label, value in pinned_values(root).items():
        if value != revision:
            problems.append(f"{label} contract revision pin {value} != {revision}")
    problems.extend(example_revision_problems(root, revision))
    fingerprint = contract_fingerprint(root)
    for label, value in pinned_fingerprints(root).items():
        if value != fingerprint:
            problems.append(f"{label} contract fingerprint pin {value} != {fingerprint}")
    return problems


def _rewrite(
    path: Path, pattern: re.Pattern[str], value: str, label: str, *, required: bool = True
) -> bool:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    if len(pattern.findall(text)) < 1:
        if required:
            raise LockstepError(f"{label}: no pin to rewrite in {path}")
        return False
    updated = pattern.sub(lambda match: f"{match.group(1)}{value}{match.group(2)}", text)
    if updated == text:
        return False
    # Bytes in, bytes out: a checkout's line endings are left exactly as found.
    path.write_bytes(updated.encode("utf-8"))
    return True


def write(root: Path) -> dict[str, object]:
    """Rewrite every pin to the recomputed revision, then both fingerprint
    pins. Rewriting never adds a missing place: ``check`` reports those."""
    root = _root(root)

    revision = compute_contract_revision(root)
    changed: list[str] = []
    for path, pattern, label in (
        (root / PYTHON_PIN, _PYTHON_PIN, "python pin"),
        (root / CONSOLE_PIN, _CONSOLE_PIN, "console pin"),
        (root / LOCKSTEP_MANIFEST, _REVISION_VALUE, "manifest pin"),
    ):
        if _rewrite(path, pattern, revision, label):
            changed.append(_relative(root, path))
    for path in example_paths(root):
        # Error exchanges carry no revision; every success exchange does.
        if _rewrite(path, _REVISION_VALUE, revision, "example", required=False):
            changed.append(_relative(root, path))
    fingerprint = contract_fingerprint(root)
    for path, pattern, label in (
        (root / PYTHON_FINGERPRINT_PIN, _PYTHON_FINGERPRINT, "python fingerprint pin"),
        (root / CONSOLE_FINGERPRINT_PIN, _CONSOLE_FINGERPRINT, "console fingerprint pin"),
    ):
        if _rewrite(path, pattern, fingerprint, label):
            changed.append(_relative(root, path))
    return {
        "contract_revision": revision,
        "contract_fingerprint": fingerprint,
        "changed": changed,
        "problems": check(root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify every pin; exit 1 on drift")
    mode.add_argument("--write", action="store_true", help="rewrite every pin to the recomputed value")
    args = parser.parse_args(argv)
    root = repository_root()
    try:
        if args.write:
            result = write(root)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 1 if result["problems"] else 0
        revision = compute_contract_revision(root)
        problems = check(root)
        print(json.dumps({"contract_revision": revision, "problems": problems}, indent=2, sort_keys=True))
        if args.check:
            return 1 if problems else 0
        return 0
    except LockstepError as error:
        print(f"lockstep error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
