#!/usr/bin/env python3
"""Deterministic repository documentation and hygiene verification."""

from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]

EXCLUDED_PATHS = {
    ".claude/launch.json",
    ".mcp.json",
    "Start Jarvis.command",
    "dashboard.html",
    "docs/JARVIS_PROTOTYPE.md",
    "generate_assets.py",
    "index.html",
    "jarvis_ack.mp3",
    "jarvis_brief.mp3",
    "jarvis_brief.txt",
    "jarvis_data.js",
}
EXCLUDED_PREFIXES = (
    ".claude/skills/3d-asset-generator/",
    "assets/",
    "css/",
    "js/",
    "scripts/",
)
GENERATED_DIRS = {
    ".hypothesis",
    ".idea",
    ".mypy_cache",
    ".next",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".turbo",
    ".venv",
    ".vercel",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "out",
    "venv",
}
GENERATED_NAMES = {".coverage", ".DS_Store", "nohup.out"}
GENERATED_SUFFIXES = (".pyc", ".pyo", ".swp", ".swo", ".tsbuildinfo", "~")
SCREENSHOT_RE = re.compile(
    r"^screenshot(?:[-_ ]|\d).+\.(?:gif|jpe?g|png|webp)$", re.IGNORECASE
)

SECRET_PATTERNS = {
    "private key": re.compile(
        r"-----BEGIN " + r"(?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
    ),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)" + r"[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh" + r"[pousr]_[A-Za-z0-9]{36,255}\b"),
    "GitHub fine-grained token": re.compile(r"\bgithub_pat" + r"_[A-Za-z0-9_]{40,255}\b"),
    "Google API key": re.compile(r"\bAI" + r"za[0-9A-Za-z_-]{35}\b"),
    "OpenAI API key": re.compile(r"\bsk-" + r"(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "npm access token": re.compile(r"\bnpm" + r"_[A-Za-z0-9]{36}\b"),
    "PyPI API token": re.compile(r"\bpypi-" + r"AgEIcH[A-Za-z0-9_-]{50,}\b"),
    "Slack token": re.compile(r"\bxox" + r"[aboprs]-[A-Za-z0-9-]{10,}\b"),
    "Slack app token": re.compile(r"\bxapp-" + r"[A-Za-z0-9-]{10,}\b"),
    "Stripe live secret": re.compile(r"\bsk_" + r"live_[A-Za-z0-9]{16,}\b"),
    "Stripe restricted live key": re.compile(
        r"\brk_" + r"live_[A-Za-z0-9]{16,}\b"
    ),
}
MACHINE_PATH_PATTERNS = {
    "macOS user path": re.compile(r"/Users/" + r"[^/\s]+(?:/|$)"),
    "Linux user path": re.compile(r"/home/" + r"[^/\s]+(?:/|$)"),
    "Windows user path": re.compile(
        r"[A-Za-z]:\\Users\\" + r"[^\\\s]+(?:\\|$)"
    ),
}

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*$")
SETEXT_HEADING_RE = re.compile(r"^ {0,3}(?:=+|-+)\s*$")
HTML_ANCHOR_RE = re.compile(
    r"<[A-Za-z][^>]*\s(?:id|name)=[\"']([^\"']+)[\"']", re.I
)
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
BLOCK_INTERRUPT_RE = re.compile(
    r"^ {0,3}(?:>|(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$))"
)
INLINE_CODE_RE = re.compile(r"`+[^`]*`+")
LINKED_TEXT_RE = re.compile(
    r"!?\[([^\]]*)\](?:\((?:[^()]|\([^()]*\))*\)|\[[^\]]*\])"
)
REFERENCE_DEFINITION_RE = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*(.*)$")
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DOCKER_DIGEST_RE = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-f]{64}$")
JARVIS_DEPENDENCY_RE = re.compile(r"jarvis[-_.]ai[-_.]agent", re.IGNORECASE)
JARVIS_DEPENDENCY_LABEL = "jarvis" + "-ai-agent"
DEPENDENCY_FILENAMES = {
    "cargo.lock",
    "cargo.toml",
    "package-lock.json",
    "package.json",
    "pipfile",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "uv.lock",
    "yarn.lock",
}
EXECUTABLE_SUFFIXES = {".cjs", ".js", ".mjs", ".py", ".sh", ".ts", ".tsx"}
TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".json",
    ".jsonl",
    ".lock",
    ".md",
    ".rst",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_FILENAMES = {
    ".env",
    ".gitattributes",
    ".gitignore",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "containerfile",
    "dockerfile",
    "license",
    "makefile",
}
CONFLICT_START_RE = re.compile(r"^(?:<{7,}|\|{7,})(?:\s.*)?$")
CONFLICT_SEPARATOR_RE = re.compile(r"^={7,}$")
CONFLICT_END_RE = re.compile(r"^>{7,}(?:\s.*)?$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_PLACEHOLDER_RE = re.compile(
    r"\b(?:TODO|FIXME|Lorem ipsum)\b|\bplaceholder (?:instruction|text)\b",
    re.IGNORECASE,
)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return result.stdout


def tracked_paths() -> list[Path]:
    listed = {
        item
        for item in git(
            "ls-files", "--cached", "--others", "--exclude-standard", "-z"
        ).split("\0")
        if item
    }
    deleted = {
        item for item in git("ls-files", "--deleted", "-z").split("\0") if item
    }
    return [ROOT / item for item in sorted(listed - deleted)]


def gitlinks_from_index(index: str) -> list[str]:
    """Return checkout-relative paths recorded as Git submodules."""
    gitlinks: list[str] = []
    for record in index.split("\0"):
        if not record:
            continue
        metadata, separator, path = record.partition("\t")
        if not separator:
            raise ValueError("malformed git index entry")
        fields = metadata.split()
        if len(fields) != 3:
            raise ValueError("malformed git index metadata")
        mode, object_id, stage = fields
        if not re.fullmatch(r"[0-7]{6}", mode):
            raise ValueError("malformed git index mode")
        if not re.fullmatch(r"[0-9a-f]{40,64}", object_id):
            raise ValueError("malformed git index object id")
        if not stage.isdigit():
            raise ValueError("malformed git index stage")
        if mode == "160000":
            gitlinks.append(path)
    return sorted(gitlinks)


def tracked_gitlinks() -> list[str]:
    return gitlinks_from_index(git("ls-files", "--stage", "-z"))


def tracked_ignored_paths() -> list[str]:
    """Return force-tracked paths that still match effective ignore rules."""
    return sorted(
        item
        for item in git(
            "ls-files",
            "--cached",
            "--ignored",
            "--exclude-per-directory=.gitignore",
            "-z",
        ).split("\0")
        if item
    )


def text_content(path: Path) -> str | None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def lossy_text_content(path: Path) -> str | None:
    """Recover ASCII-compatible hygiene evidence from a regular file."""
    try:
        mode = path.lstat().st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    return path.read_bytes().decode("utf-8", errors="ignore")


def github_slug(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = LINKED_TEXT_RE.sub(lambda match: match.group(1), value)
    value = INLINE_CODE_RE.sub(lambda match: match.group(0).strip("`"), value)
    value = value.strip().lower()
    kept = []
    for char in value:
        category = unicodedata.category(char)
        if char in " -_" or category[0] in {"L", "N", "M"}:
            kept.append(char)
    return "".join(kept).replace(" ", "-")


def _is_escaped(value: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and value[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _blockquote_context(line: str) -> tuple[int, str]:
    """Return the leading blockquote depth and its remaining source text."""
    cursor = len(line) - len(line.lstrip(" "))
    if cursor > 3:
        return 0, line
    depth = 0
    while cursor < len(line) and line[cursor] == ">":
        depth += 1
        cursor += 1
        if cursor < len(line) and line[cursor] in " \t":
            cursor += 1
    return depth, line[cursor:]


def _next_backtick_run(
    line: str, cursor: int, *, honor_escapes: bool = True
) -> tuple[int, int] | None:
    while cursor < len(line):
        start = line.find("`", cursor)
        if start < 0:
            return None
        if honor_escapes and _is_escaped(line, start):
            cursor = start + 1
            continue
        end = start + 1
        while end < len(line) and line[end] == "`":
            end += 1
        return start, end - start
    return None


def _closing_backtick_run(
    line: str, cursor: int, delimiter_length: int
) -> int | None:
    while cursor < len(line):
        run = _next_backtick_run(line, cursor, honor_escapes=False)
        if run is None:
            return None
        start, length = run
        cursor = start + length
        if length == delimiter_length:
            return cursor
    return None


def _mask_markdown_comments_and_code(
    line: str, in_comment: bool, code_delimiter: int | None
) -> tuple[str, str, bool, int | None]:
    """Mask comments and code while preserving their cross-line states."""
    comments_masked = list(line)
    links_masked = list(line)
    cursor = 0
    while cursor < len(line):
        if in_comment:
            end = line.find("-->", cursor)
            if end < 0:
                comments_masked[cursor:] = " " * (len(line) - cursor)
                links_masked[cursor:] = " " * (len(line) - cursor)
                return (
                    "".join(comments_masked),
                    "".join(links_masked),
                    True,
                    code_delimiter,
                )
            comments_masked[cursor : end + 3] = " " * (end + 3 - cursor)
            links_masked[cursor : end + 3] = " " * (end + 3 - cursor)
            cursor = end + 3
            in_comment = False
            continue

        if code_delimiter is not None:
            closing = _closing_backtick_run(line, cursor, code_delimiter)
            if closing is None:
                links_masked[cursor:] = " " * (len(line) - cursor)
                return (
                    "".join(comments_masked),
                    "".join(links_masked),
                    False,
                    code_delimiter,
                )
            links_masked[cursor:closing] = " " * (closing - cursor)
            cursor = closing
            code_delimiter = None
            continue

        comment_start = line.find("<!--", cursor)
        backtick_run = _next_backtick_run(line, cursor)
        if comment_start < 0 and backtick_run is None:
            break
        if comment_start >= 0 and (
            backtick_run is None or comment_start < backtick_run[0]
        ):
            in_comment = True
            cursor = comment_start
            continue

        assert backtick_run is not None
        opener, code_delimiter = backtick_run
        closing = _closing_backtick_run(
            line, opener + code_delimiter, code_delimiter
        )
        if closing is None:
            links_masked[opener:] = " " * (len(line) - opener)
            return (
                "".join(comments_masked),
                "".join(links_masked),
                False,
                code_delimiter,
            )
        links_masked[opener:closing] = " " * (closing - opener)
        cursor = closing
        code_delimiter = None
    return (
        "".join(comments_masked),
        "".join(links_masked),
        in_comment,
        code_delimiter,
    )


def _closing_bracket(line: str, opening: int) -> int | None:
    depth = 1
    cursor = opening + 1
    while cursor < len(line):
        if _is_escaped(line, cursor):
            cursor += 1
        elif line[cursor] == "[":
            depth += 1
        elif line[cursor] == "]":
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return None


def _inline_destination(line: str, opening: int) -> tuple[str, int] | None:
    """Parse one inline-link destination starting at its opening parenthesis."""
    cursor = opening + 1
    if cursor < len(line) and line[cursor] == "<":
        end = cursor + 1
        while end < len(line):
            if line[end] == ">" and not _is_escaped(line, end):
                close = line.find(")", end + 1)
                if close >= 0:
                    return line[cursor + 1 : end], close + 1
                return None
            end += 1
        return None

    start = cursor
    nested = 0
    while cursor < len(line):
        char = line[cursor]
        if _is_escaped(line, cursor):
            cursor += 1
        elif char == "(":
            nested += 1
        elif char == ")":
            if nested == 0:
                return line[start:cursor], cursor + 1
            nested -= 1
        elif char.isspace() and nested == 0:
            destination = line[start:cursor]
            close = line.find(")", cursor)
            if close >= 0:
                return destination, close + 1
            return None
        cursor += 1
    return None


def _markdown_link_uses(
    line: str,
) -> tuple[list[str], list[str], list[str], bool]:
    """Return link uses and whether an inline destination is incomplete."""
    destinations: list[str] = []
    references: list[str] = []
    shortcuts: list[str] = []
    malformed_inline = False
    cursor = 0
    while cursor < len(line):
        opening = line.find("[", cursor)
        if opening < 0:
            break
        if _is_escaped(line, opening):
            cursor = opening + 1
            continue
        closing = _closing_bracket(line, opening)
        if closing is None:
            break
        label = line[opening + 1 : closing]
        following = closing + 1
        if following < len(line) and line[following] == "(":
            parsed = _inline_destination(line, following)
            if parsed is not None:
                destination, cursor = parsed
                destinations.append(destination)
                continue
            malformed_inline = True
            cursor = following + 1
            continue
        elif following < len(line) and line[following] == "[":
            reference_end = _closing_bracket(line, following)
            if reference_end is not None:
                reference = line[following + 1 : reference_end] or label
                references.append(_reference_label(reference))
                cursor = reference_end + 1
                continue
        shortcuts.append(_reference_label(label))
        cursor = closing + 1
    return destinations, references, shortcuts, malformed_inline


def _reference_destination(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if value.startswith("<"):
        end = value.find(">", 1)
        return None if end < 0 else value[1:end]
    return value.split(maxsplit=1)[0]


def _reference_label(value: str) -> str:
    return " ".join(value.split()).casefold()


def markdown_structure(path: Path, text: str) -> tuple[set[str], list[tuple[int, str]], list[str]]:
    anchors: set[str] = set()
    slug_counts: dict[str, int] = {}
    links: list[tuple[int, str]] = []
    reference_definitions: dict[str, str] = {}
    reference_uses: list[tuple[int, str]] = []
    shortcut_uses: list[tuple[int, str]] = []
    errors: list[str] = []
    fence_marker: str | None = None
    fence_line: int | None = None
    in_html_comment = False
    inline_code_delimiter: int | None = None
    inline_code_line: int | None = None
    inline_code_blockquote_depth: int | None = None
    multiline_label_stack: list[int] = []
    setext_candidate: str | None = None

    def add_heading(raw_heading: str) -> None:
        base_slug = github_slug(raw_heading)
        duplicate_index = slug_counts.get(base_slug, 0)
        slug_counts[base_slug] = duplicate_index + 1
        anchors.add(
            base_slug if duplicate_index == 0 else f"{base_slug}-{duplicate_index}"
        )

    for line_number, line in enumerate(text.splitlines(), start=1):
        if fence_marker is not None:
            fence = FENCE_RE.match(line)
            if fence:
                marker = fence.group(1)
                if (
                    marker[0] == fence_marker[0]
                    and len(marker) >= len(fence_marker)
                    and not fence.group(2).strip()
                ):
                    fence_marker = None
                    fence_line = None
            continue

        blockquote_depth, blockquote_body = _blockquote_context(line)
        same_blockquote_paragraph = (
            inline_code_delimiter is not None
            and inline_code_blockquote_depth is not None
            and blockquote_depth == inline_code_blockquote_depth
            and bool(blockquote_body.strip())
            and HEADING_RE.match(blockquote_body) is None
            and FENCE_RE.match(blockquote_body) is None
            and SETEXT_HEADING_RE.match(blockquote_body) is None
            and BLOCK_INTERRUPT_RE.match(blockquote_body) is None
        )
        starts_new_block = (
            not line.strip()
            or HEADING_RE.match(line) is not None
            or FENCE_RE.match(line) is not None
            or SETEXT_HEADING_RE.match(line) is not None
            or (
                BLOCK_INTERRUPT_RE.match(line) is not None
                and not same_blockquote_paragraph
            )
        )
        if inline_code_delimiter is not None and starts_new_block:
            errors.append(
                f"{path.relative_to(ROOT)}:{inline_code_line}: "
                "inline Markdown code span crosses a block boundary"
            )
            inline_code_delimiter = None
            inline_code_line = None
            inline_code_blockquote_depth = None

        if not in_html_comment and inline_code_delimiter is None:
            fence = FENCE_RE.match(line)
            if fence:
                marker = fence.group(1)
                fence_marker = marker
                fence_line = line_number
                setext_candidate = None
                continue
        previous_code_delimiter = inline_code_delimiter
        (
            visible,
            without_code,
            in_html_comment,
            inline_code_delimiter,
        ) = _mask_markdown_comments_and_code(
            line, in_html_comment, inline_code_delimiter
        )
        if previous_code_delimiter is None and inline_code_delimiter is not None:
            inline_code_line = line_number
            inline_code_blockquote_depth = blockquote_depth or None
        elif inline_code_delimiter is None:
            inline_code_line = None
            inline_code_blockquote_depth = None
        if line.startswith("\t") or line.startswith("    "):
            setext_candidate = None
            continue

        if SETEXT_HEADING_RE.match(visible):
            if setext_candidate is not None:
                add_heading(setext_candidate)
            setext_candidate = None
            continue

        heading = HEADING_RE.match(visible)
        if heading:
            raw_heading = re.sub(r"\s+#+\s*$", "", heading.group(2))
            add_heading(raw_heading)
            setext_candidate = None
        if (
            previous_code_delimiter is None
            and inline_code_delimiter is not None
            and heading is not None
        ):
            errors.append(
                f"{path.relative_to(ROOT)}:{inline_code_line}: "
                "inline Markdown code span crosses a block boundary"
            )
            inline_code_delimiter = None
            inline_code_line = None
            inline_code_blockquote_depth = None

        anchors.update(HTML_ANCHOR_RE.findall(without_code))
        definition = REFERENCE_DEFINITION_RE.match(without_code)
        if definition:
            label = _reference_label(definition.group(1))
            destination = _reference_destination(definition.group(2))
            if destination is None:
                errors.append(
                    f"{path.relative_to(ROOT)}:{line_number}: malformed reference link"
                )
            elif label in reference_definitions:
                errors.append(
                    f"{path.relative_to(ROOT)}:{line_number}: duplicate reference label: "
                    f"{definition.group(1)}"
                )
            else:
                reference_definitions[label] = destination
            setext_candidate = None
            continue
        if not without_code.strip():
            multiline_label_stack.clear()
        else:
            for index, char in enumerate(without_code):
                if _is_escaped(without_code, index):
                    continue
                if char == "[":
                    multiline_label_stack.append(line_number)
                    continue
                if char != "]" or not multiline_label_stack:
                    continue
                label_line = multiline_label_stack.pop()
                if (
                    label_line < line_number
                    and index + 1 < len(without_code)
                ):
                    following = index + 1
                    if without_code[following] == "(":
                        parsed_destination = _inline_destination(
                            without_code, following
                        )
                        if parsed_destination is None:
                            errors.append(
                                f"{path.relative_to(ROOT)}:{label_line}: "
                                "inline Markdown links must finish on one line"
                            )
                        else:
                            destination, _ = parsed_destination
                            links.append((label_line, destination))
                    elif without_code[following] == "[":
                        errors.append(
                            f"{path.relative_to(ROOT)}:{label_line}: "
                            "reference-link labels must finish on one line"
                        )
        inline_links, references, shortcuts, malformed_inline = _markdown_link_uses(
            without_code
        )
        if malformed_inline:
            errors.append(
                f"{path.relative_to(ROOT)}:{line_number}: "
                "inline Markdown links must finish on one line"
            )
        links.extend((line_number, target) for target in inline_links)
        reference_uses.extend((line_number, label) for label in references)
        shortcut_uses.extend((line_number, label) for label in shortcuts)
        if heading is None:
            setext_candidate = visible if visible.strip() else None

    if fence_marker is not None:
        errors.append(
            f"{path.relative_to(ROOT)}:{fence_line}: unclosed Markdown fence"
        )
    if inline_code_delimiter is not None:
        errors.append(
            f"{path.relative_to(ROOT)}:{inline_code_line}: "
            "unclosed inline Markdown code span"
        )
    for line_number, label in reference_uses:
        target = reference_definitions.get(label)
        if target is None:
            errors.append(
                f"{path.relative_to(ROOT)}:{line_number}: undefined reference link: {label}"
            )
        else:
            links.append((line_number, target))
    for line_number, label in shortcut_uses:
        target = reference_definitions.get(label)
        if target is not None:
            links.append((line_number, target))
    return anchors, links, errors


def verify_markdown(
    markdown: dict[Path, str], repository_paths: set[Path] | None = None
) -> list[str]:
    errors: list[str] = []
    available_paths = {
        path.resolve() for path in (repository_paths or set(markdown))
    }
    parsed = {path: markdown_structure(path, text) for path, text in markdown.items()}
    for _, _, structure_errors in parsed.values():
        errors.extend(structure_errors)

    for source, (_, links, _) in parsed.items():
        for line_number, raw_target in links:
            target = unquote(raw_target)
            split = urlsplit(target)
            if split.scheme or split.netloc:
                continue
            path_text = split.path
            fragment = split.fragment
            destination = source if not path_text else (source.parent / path_text).resolve()
            try:
                destination.relative_to(ROOT)
            except ValueError:
                errors.append(
                    f"{source.relative_to(ROOT)}:{line_number}: link escapes repository: {raw_target}"
                )
                continue
            if destination.is_dir():
                contains_repository_path = any(
                    candidate != destination
                    and candidate.is_relative_to(destination)
                    for candidate in available_paths
                )
                if not contains_repository_path:
                    errors.append(
                        f"{source.relative_to(ROOT)}:{line_number}: "
                        f"directory link has no tracked/nonignored target: {raw_target}"
                    )
                    continue
                if fragment:
                    errors.append(
                        f"{source.relative_to(ROOT)}:{line_number}: "
                        f"directory link cannot resolve an anchor: {raw_target}"
                    )
                continue
            if not destination.exists():
                errors.append(
                    f"{source.relative_to(ROOT)}:{line_number}: missing link target: {raw_target}"
                )
                continue
            if destination not in available_paths:
                errors.append(
                    f"{source.relative_to(ROOT)}:{line_number}: "
                    f"link target is not tracked/nonignored: {raw_target}"
                )
                continue
            if fragment and destination.suffix.lower() == ".md":
                target_text = markdown.get(destination)
                if target_text is None:
                    target_text = text_content(destination)
                if target_text is None:
                    errors.append(
                        f"{source.relative_to(ROOT)}:{line_number}: cannot read anchor target: {raw_target}"
                    )
                    continue
                anchors = parsed.get(destination)
                anchor_set = anchors[0] if anchors else markdown_structure(destination, target_text)[0]
                if fragment not in anchor_set:
                    errors.append(
                        f"{source.relative_to(ROOT)}:{line_number}: missing anchor: {raw_target}"
                    )
    return errors


def verify_paths(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        relative_path = path.relative_to(ROOT)
        relative = relative_path.as_posix()
        parts = set(relative_path.parts)
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            errors.append(f"cannot inspect repository path: {relative}: {exc}")
            continue
        if stat.S_ISLNK(mode):
            errors.append(f"unexpected repository symlink: {relative}")
            continue
        if not stat.S_ISREG(mode):
            errors.append(f"unexpected non-file repository path: {relative}")
            continue
        if relative in EXCLUDED_PATHS or relative.startswith(EXCLUDED_PREFIXES):
            errors.append(f"excluded legacy path is tracked: {relative}")
        if parts & GENERATED_DIRS:
            errors.append(f"generated/cache directory is tracked: {relative}")
        if any(part.endswith(".egg-info") for part in relative_path.parts):
            errors.append(f"generated package metadata is tracked: {relative}")
        if (
            path.name in GENERATED_NAMES
            or path.name.startswith(".coverage.")
            or path.name.endswith(GENERATED_SUFFIXES)
            or SCREENSHOT_RE.match(path.name)
        ):
            errors.append(f"generated/editor file is tracked: {relative}")
        if "reports" in relative_path.parts and relative != "simulation/reports/.gitkeep":
            errors.append(f"generated report is tracked: {relative}")
    return errors


def _frontmatter_value(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
        return decoded if isinstance(decoded, str) else None
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            return None
        inner = value[1:-1]
        cursor = 0
        while cursor < len(inner):
            if inner[cursor] != "'":
                cursor += 1
                continue
            if cursor + 1 >= len(inner) or inner[cursor + 1] != "'":
                return None
            cursor += 2
        return inner.replace("''", "'")
    if " #" in value or ": " in value:
        return None
    if value[0] in "[{!&*|>@`" or re.fullmatch(
        r"(?i:null|~|true|false|yes|no|on|off|[-+]?(?:\d+(?:\.\d*)?|\.\d+))",
        value,
    ):
        return None
    return value


def _skill_frontmatter(path: Path, text: str) -> tuple[dict[str, str], list[str]]:
    relative = path.relative_to(ROOT)
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}, [f"{relative}: skill metadata must start with YAML frontmatter"]
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return {}, [f"{relative}: skill metadata has no closing frontmatter delimiter"]

    metadata: dict[str, str] = {}
    errors: list[str] = []
    for line_number, line in enumerate(lines[1:closing], start=2):
        key, separator, value = line.partition(":")
        key = key.strip()
        if not separator or not key or not value.strip():
            errors.append(f"{relative}:{line_number}: malformed skill metadata")
            continue
        if key in metadata:
            errors.append(f"{relative}:{line_number}: duplicate skill metadata key: {key}")
            continue
        decoded = _frontmatter_value(value)
        if decoded is None:
            errors.append(
                f"{relative}:{line_number}: skill metadata values must be "
                "one-line strings"
            )
            continue
        metadata[key] = decoded
    unexpected = sorted(set(metadata) - {"name", "description"})
    if unexpected:
        errors.append(
            f"{relative}: unsupported skill metadata keys: {', '.join(unexpected)}"
        )
    missing = sorted({"name", "description"} - set(metadata))
    if missing:
        errors.append(f"{relative}: missing skill metadata keys: {', '.join(missing)}")
    return metadata, errors


def _quoted_yaml_string(value: str) -> str | None:
    value = value.strip()
    if not value or value[0] not in {"'", '"'}:
        return None
    return _frontmatter_value(value)


def _verify_skill_agent_metadata(path: Path, skill_name: str) -> list[str]:
    relative = path.relative_to(ROOT)
    text = text_content(path)
    if text is None:
        return [f"{relative}: skill agent metadata must be UTF-8 text"]
    lines = text.splitlines()
    if not lines or lines[0] != "interface:":
        return [f"{relative}: skill agent metadata must start with interface:"]

    values: dict[str, str] = {}
    errors: list[str] = []
    for line_number, line in enumerate(lines[1:], start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(
            r"  (display_name|short_description|default_prompt):\s*(.+)", line
        )
        if match is None:
            errors.append(
                f"{relative}:{line_number}: unsupported skill agent metadata"
            )
            continue
        key, raw_value = match.groups()
        if key in values:
            errors.append(
                f"{relative}:{line_number}: duplicate skill agent metadata key: {key}"
            )
            continue
        value = _quoted_yaml_string(raw_value)
        if value is None:
            errors.append(
                f"{relative}:{line_number}: skill agent metadata strings must be "
                "quoted and valid"
            )
            continue
        if not value:
            errors.append(
                f"{relative}:{line_number}: skill agent metadata strings must not be empty"
            )
            continue
        values[key] = value

    required = {"display_name", "short_description", "default_prompt"}
    missing = sorted(required - set(values))
    if missing:
        errors.append(
            f"{relative}: missing skill agent metadata keys: {', '.join(missing)}"
        )
    short_description = values.get("short_description")
    if short_description is not None and not 25 <= len(short_description) <= 64:
        errors.append(
            f"{relative}: short_description must contain 25 to 64 characters"
        )
    default_prompt = values.get("default_prompt")
    skill_token = re.compile(rf"\${re.escape(skill_name)}(?=$|[^a-z0-9-])")
    if default_prompt is not None and skill_token.search(default_prompt) is None:
        errors.append(
            f"{relative}: default_prompt must name the skill as ${skill_name}"
        )
    return errors


def verify_skills(paths: list[Path]) -> list[str]:
    """Validate repository-owned Codex skill structure and metadata."""
    errors: list[str] = []
    repository_paths = {
        path.relative_to(ROOT).as_posix()
        for path in paths
        if path.is_relative_to(ROOT)
    }
    skill_names = sorted(
        {
            relative.parts[2]
            for path in paths
            if path.is_relative_to(ROOT)
            for relative in (path.relative_to(ROOT),)
            if len(relative.parts) >= 3 and relative.parts[:2] == (".agent", "skills")
        }
    )
    for skill_name in skill_names:
        skill_prefix = f".agent/skills/{skill_name}"
        skill_relative = f"{skill_prefix}/SKILL.md"
        metadata_relative = f"{skill_prefix}/agents/openai.yaml"
        if not SKILL_NAME_RE.fullmatch(skill_name):
            errors.append(f"{skill_prefix}: invalid skill directory name")
        elif len(skill_name) > 64:
            errors.append(f"{skill_prefix}: skill directory name exceeds 64 characters")
        if skill_relative not in repository_paths:
            errors.append(f"{skill_prefix}: missing SKILL.md")
            continue

        skill_path = ROOT / skill_relative
        skill_text = text_content(skill_path)
        if skill_text is None:
            errors.append(f"{skill_relative}: skill instructions must be UTF-8 text")
            continue
        metadata, metadata_errors = _skill_frontmatter(skill_path, skill_text)
        errors.extend(metadata_errors)
        declared_name = metadata.get("name")
        if declared_name is not None and declared_name != skill_name:
            errors.append(
                f"{skill_relative}: frontmatter name {declared_name!r} "
                f"does not match directory {skill_name!r}"
            )
        description = metadata.get("description")
        if description is not None:
            if len(description) > 1024 or "<" in description or ">" in description:
                errors.append(
                    f"{skill_relative}: description violates skill metadata limits"
                )
            if not re.search(r"\bUse (?:for|when)\b", description):
                errors.append(
                    f"{skill_relative}: description must state when the skill should be used"
                )
        skill_lines = skill_text.splitlines()
        try:
            frontmatter_end = skill_lines.index("---", 1)
        except ValueError:
            frontmatter_end = -1
        if frontmatter_end >= 0 and not any(
            line.strip() for line in skill_lines[frontmatter_end + 1 :]
        ):
            errors.append(f"{skill_relative}: skill instructions must have a body")
        if SKILL_PLACEHOLDER_RE.search(skill_text):
            errors.append(f"{skill_relative}: unresolved scaffold placeholder")

        if metadata_relative not in repository_paths:
            errors.append(f"{skill_prefix}: missing agents/openai.yaml")
        else:
            errors.extend(
                _verify_skill_agent_metadata(ROOT / metadata_relative, skill_name)
            )
    return errors


def _workflow_key_at(line: str, cursor: int) -> tuple[str, int] | None:
    """Parse a plain or quoted YAML mapping key and its following colon."""
    while cursor < len(line) and line[cursor].isspace():
        cursor += 1
    if cursor >= len(line):
        return None
    if line[cursor] in {"'", '"'}:
        quote = line[cursor]
        end = line.find(quote, cursor + 1)
        if end < 0:
            return None
        key = line[cursor + 1 : end]
        cursor = end + 1
    else:
        match = re.match(r"[A-Za-z][A-Za-z0-9_-]*", line[cursor:])
        if match is None:
            return None
        key = match.group(0)
        cursor += len(key)
    while cursor < len(line) and line[cursor].isspace():
        cursor += 1
    if cursor >= len(line) or line[cursor] != ":":
        return None
    return key, cursor + 1


def _flow_value_end(line: str, cursor: int) -> int:
    nested: list[str] = []
    quote: str | None = None
    while cursor < len(line):
        char = line[cursor]
        if quote is not None:
            if quote == "'" and char == "'" and cursor + 1 < len(line) and line[cursor + 1] == "'":
                cursor += 2
                continue
            if char == quote and not _is_escaped(line, cursor):
                quote = None
            cursor += 1
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in "{[(":
            nested.append(char)
        elif char in "}])":
            if not nested:
                return cursor
            nested.pop()
        elif char == "," and not nested:
            return cursor
        cursor += 1
    return cursor


def _workflow_mapping_entries(line: str) -> list[tuple[str, str]]:
    """Return relevant block or single-line flow mapping entries."""
    entries: list[tuple[str, str]] = []
    cursor = len(line) - len(line.lstrip())
    if cursor < len(line) and line[cursor] == "-":
        cursor += 1
        while cursor < len(line) and line[cursor].isspace():
            cursor += 1

    block = _workflow_key_at(line, cursor)
    if block is not None:
        key, value_start = block
        if key in {"uses", "continue-on-error", "image"}:
            entries.append((key, line[value_start:]))
        return entries

    if cursor >= len(line) or line[cursor] != "{":
        return entries
    cursor += 1
    while cursor < len(line):
        parsed = _workflow_key_at(line, cursor)
        if parsed is None:
            break
        key, value_start = parsed
        value_end = _flow_value_end(line, value_start)
        if key in {"uses", "continue-on-error", "image"}:
            entries.append((key, line[value_start:value_end]))
        cursor = value_end
        if cursor >= len(line) or line[cursor] == "}":
            break
        cursor += 1
    return entries


def _yaml_value_without_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if quote is not None:
            if char == quote and not _is_escaped(value, index):
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _action_use_value(value: str) -> str:
    value = _yaml_value_without_comment(value).strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        decoded = _quoted_yaml_string(value)
        return decoded if decoded is not None else value
    return value


def _double_quoted_mapping_key_has_escape(line: str) -> bool:
    """Return whether a double-quoted YAML key uses escape decoding."""
    cursor = len(line) - len(line.lstrip())
    if cursor < len(line) and line[cursor] == "-":
        cursor += 1
        while cursor < len(line) and line[cursor].isspace():
            cursor += 1
    if cursor >= len(line) or line[cursor] != '"':
        return False
    cursor += 1
    escaped = False
    while cursor < len(line):
        if line[cursor] == "\\":
            escaped = True
            cursor += 2
            continue
        if line[cursor] == '"':
            cursor += 1
            break
        cursor += 1
    else:
        return False
    while cursor < len(line) and line[cursor].isspace():
        cursor += 1
    return escaped and cursor < len(line) and line[cursor] == ":"


def _yaml_structure_without_scalars(line: str) -> str:
    """Mask quoted scalars and comments while preserving structural positions."""
    masked = list(line)
    quote: str | None = None
    cursor = 0
    while cursor < len(line):
        char = line[cursor]
        if quote is not None:
            masked[cursor] = " "
            if quote == "'" and char == "'" and cursor + 1 < len(line) and line[cursor + 1] == "'":
                masked[cursor + 1] = " "
                cursor += 2
                continue
            if quote == '"' and char == "\\":
                if cursor + 1 < len(line):
                    masked[cursor + 1] = " "
                cursor += 2
                continue
            if char == quote:
                quote = None
            cursor += 1
            continue
        if char in {"'", '"'}:
            quote = char
            masked[cursor] = " "
        elif char == "#" and (cursor == 0 or line[cursor - 1].isspace()):
            masked[cursor:] = " " * (len(line) - cursor)
            break
        cursor += 1
    return "".join(masked)


def _starts_unclosed_flow_collection(value: str) -> bool:
    """Return whether a structural value opens a flow collection past this line."""
    value = value.lstrip()
    if not value.startswith(("[", "{")):
        return False
    expected: list[str] = []
    closing = {"[": "]", "{": "}"}
    for char in value:
        if char in closing:
            expected.append(closing[char])
        elif char in {"}", "]"}:
            if not expected or expected.pop() != char:
                return True
    return bool(expected)


def _unsupported_policy_yaml(line: str) -> list[str]:
    """Reject semantic YAML features the dependency-policy parser cannot decode."""
    reasons: list[str] = []
    if _double_quoted_mapping_key_has_escape(line):
        reasons.append("escaped double-quoted mapping key")
    structure = _yaml_structure_without_scalars(line)
    cursor = len(line) - len(line.lstrip())
    if cursor < len(line) and line[cursor] == "-":
        cursor += 1
        while cursor < len(line) and line[cursor].isspace():
            cursor += 1
    structural_value = structure[cursor:].lstrip()
    parsed = _workflow_key_at(line, cursor)
    mapping_value = structure[parsed[1] :].lstrip() if parsed is not None else ""
    flow_sequence_value = next(
        (
            value
            for value in (mapping_value, structural_value)
            if value.startswith("[")
        ),
        "",
    )
    unclosed_flow_collection = _starts_unclosed_flow_collection(
        structural_value
    ) or _starts_unclosed_flow_collection(mapping_value)

    if structural_value.startswith("?") and structural_value[1:2].isspace():
        reasons.append("explicit mapping key")
    if re.match(r"<<\s*:", structural_value):
        reasons.append("mapping merge key")
    if re.match(r"[&*][A-Za-z0-9_-]+(?:\b|$)", structural_value) or re.match(
        r"[&*][A-Za-z0-9_-]+(?:\b|$)", mapping_value
    ):
        reasons.append("anchor or alias")
    if structural_value.startswith("!") or mapping_value.startswith("!"):
        reasons.append("explicit YAML tag")
    if unclosed_flow_collection:
        reasons.append("multiline flow collection")
    elif (
        structural_value.startswith("{")
        or mapping_value.startswith("{")
        or (
            flow_sequence_value
            and ("{" in flow_sequence_value or ":" in flow_sequence_value)
        )
    ):
        reasons.append("flow mapping")
    if structure.lstrip().startswith(("%YAML", "%TAG")):
        reasons.append("YAML directive")
    return reasons


def _starts_yaml_block_scalar(line: str) -> bool:
    structure = _yaml_structure_without_scalars(line).rstrip()
    return re.search(r":\s*[|>](?:[1-9][+-]?|[+-][1-9]?)?\s*$", structure) is not None


def _local_use_error(value: str, repository_paths: set[str]) -> str | None:
    """Validate a local action or reusable-workflow reference without traversal."""
    if not value.startswith("./"):
        return None
    raw_path = value[2:]
    if "\\" in raw_path or "@" in raw_path:
        return "local action reference contains an unsupported separator or ref"
    raw_parts = raw_path.split("/") if raw_path else []
    if any(part in {"", ".", ".."} for part in raw_parts):
        return "local action reference is not a canonical repository-relative path"
    local_path = PurePosixPath(raw_path) if raw_path else PurePosixPath(".")
    normalized = "" if local_path == PurePosixPath(".") else local_path.as_posix()

    if normalized.startswith(".github/workflows/") and local_path.suffix in {".yml", ".yaml"}:
        if normalized not in repository_paths:
            return "local reusable workflow does not exist in the checkout"
        return None

    prefix = f"{normalized}/" if normalized else ""
    manifests = [
        candidate
        for candidate in (f"{prefix}action.yml", f"{prefix}action.yaml")
        if candidate in repository_paths
    ]
    if not manifests:
        return "local action has no action.yml or action.yaml in the checkout"
    if len(manifests) > 1:
        return "local action has both action.yml and action.yaml"
    return None


def _docker_image_error(raw_value: str) -> str | None:
    """Reject mutable or semantically encoded remote Docker action images."""
    lexical_value = _yaml_value_without_comment(raw_value).strip()
    if not lexical_value:
        return "remote Docker action image is empty or uses an unsupported multiline scalar"
    if lexical_value.startswith(("|", ">")):
        return "remote Docker action image uses an unsupported block scalar"
    if lexical_value.startswith(("'", '"')):
        value = _quoted_yaml_string(lexical_value)
        if value is None:
            return "remote Docker action image uses an invalid quoted scalar"
        if lexical_value.startswith('"') and "\\" in lexical_value[1:-1]:
            return "remote Docker action image uses unsupported YAML escapes"
    else:
        value = _action_use_value(raw_value)
    if value.startswith("docker://") and not DOCKER_DIGEST_RE.fullmatch(value):
        return (
            "remote Docker action image is not pinned to a sha256 digest: "
            f"{value}"
        )
    return None


def _content_hygiene_errors(
    relative: Path, text: str, *, check_whitespace: bool
) -> list[str]:
    errors: list[str] = []
    in_conflict = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if check_whitespace and line.endswith((" ", "\t")):
            errors.append(f"{relative}:{line_number}: trailing whitespace")
        if CONFLICT_START_RE.fullmatch(line):
            in_conflict = True
            errors.append(
                f"{relative}:{line_number}: unresolved merge conflict marker"
            )
        elif in_conflict and CONFLICT_SEPARATOR_RE.fullmatch(line):
            errors.append(
                f"{relative}:{line_number}: unresolved merge conflict marker"
            )
        elif CONFLICT_END_RE.fullmatch(line):
            in_conflict = False
            errors.append(
                f"{relative}:{line_number}: unresolved merge conflict marker"
            )
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(line):
                errors.append(f"{relative}:{line_number}: possible {label}")
        for label, pattern in MACHINE_PATH_PATTERNS.items():
            if pattern.search(line):
                errors.append(f"{relative}:{line_number}: machine-specific {label}")
    return errors


def verify_text(paths: list[Path]) -> tuple[list[str], dict[Path, str]]:
    errors: list[str] = []
    markdown: dict[Path, str] = {}
    repository_paths = {
        path.relative_to(ROOT).as_posix()
        for path in paths
        if path.is_relative_to(ROOT)
    }
    for path in paths:
        relative = path.relative_to(ROOT)
        lower_name = path.name.lower()
        is_dependency_file = (
            lower_name in DEPENDENCY_FILENAMES
            or lower_name.startswith("requirements")
        )
        is_workflow = relative.parts[:2] == (".github", "workflows")
        is_action_manifest = lower_name in {"action.yml", "action.yaml"}
        is_policy_yaml = is_workflow or is_action_manifest
        try:
            path_mode = path.lstat().st_mode
        except OSError:
            path_mode = 0
        is_executable = path.suffix.lower() in EXECUTABLE_SUFFIXES or bool(
            path_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        )
        is_skill_path = relative.parts[:2] == (".agent", "skills")
        is_expected_text = (
            path.suffix.lower() in TEXT_SUFFIXES
            or lower_name in TEXT_FILENAMES
            or lower_name.startswith(".env.")
            or is_dependency_file
            or is_policy_yaml
            or is_executable
            or is_skill_path
        )
        text = text_content(path)
        if text is None:
            recovered_text = lossy_text_content(path)
            if recovered_text is not None:
                errors.extend(
                    _content_hygiene_errors(
                        relative, recovered_text, check_whitespace=False
                    )
                )
                if (
                    is_dependency_file or is_policy_yaml or is_executable
                ) and JARVIS_DEPENDENCY_RE.search(recovered_text):
                    errors.append(
                        f"{relative}: forbidden dependency on "
                        f"{JARVIS_DEPENDENCY_LABEL}"
                    )
            if path.suffix.lower() == ".md" and not path.is_symlink():
                errors.append(
                    f"{relative}: Markdown must be UTF-8 text without NUL bytes"
                )
            elif is_policy_yaml and not path.is_symlink():
                errors.append(
                    f"{relative}: policy YAML must be UTF-8 text without NUL bytes"
                )
            elif is_expected_text and not path.is_symlink():
                errors.append(
                    f"{relative}: repository text must be UTF-8 without NUL bytes"
                )
            continue
        if path.suffix.lower() == ".md":
            markdown[path] = text
        if text and not text.endswith("\n"):
            errors.append(f"{relative}: text file has no final newline")
        errors.extend(_content_hygiene_errors(relative, text, check_whitespace=True))

        if (is_dependency_file or is_policy_yaml or is_executable) and JARVIS_DEPENDENCY_RE.search(text):
            errors.append(
                f"{relative}: forbidden dependency on {JARVIS_DEPENDENCY_LABEL}"
            )
        if is_policy_yaml:
            block_scalar_parent_indent: int | None = None
            for line_number, line in enumerate(text.splitlines(), start=1):
                indentation = len(line) - len(line.lstrip(" "))
                if block_scalar_parent_indent is not None:
                    if not line.strip() or indentation > block_scalar_parent_indent:
                        continue
                    block_scalar_parent_indent = None
                for reason in _unsupported_policy_yaml(line):
                    errors.append(
                        f"{relative}:{line_number}: unsupported policy YAML syntax "
                        f"({reason})"
                    )
                for key, raw_value in _workflow_mapping_entries(line):
                    if key == "uses":
                        value = _action_use_value(raw_value)
                        local_error = _local_use_error(value, repository_paths)
                        if local_error is not None:
                            errors.append(
                                f"{relative}:{line_number}: {local_error}: "
                                f"{value or '<empty>'}"
                            )
                        elif not value.startswith("./"):
                            ref = value.rsplit("@", 1)[1] if "@" in value else ""
                            if not COMMIT_SHA_RE.fullmatch(ref):
                                errors.append(
                                    f"{relative}:{line_number}: action is not pinned to a "
                                    f"full commit SHA: {value or '<empty>'}"
                                )
                    elif key == "image" and is_action_manifest:
                        image_error = _docker_image_error(raw_value)
                        if image_error is not None:
                            errors.append(
                                f"{relative}:{line_number}: {image_error}"
                            )
                    elif (
                        key == "continue-on-error"
                        and _yaml_value_without_comment(raw_value).strip() != "false"
                    ):
                        errors.append(
                            f"{relative}:{line_number}: continue-on-error hides failures"
                        )
                if _starts_yaml_block_scalar(line):
                    block_scalar_parent_indent = indentation
    return errors, markdown


def main() -> int:
    try:
        paths = tracked_paths()
        errors = verify_paths(paths)
        errors.extend(
            f"unexpected tracked submodule: {path}" for path in tracked_gitlinks()
        )
        errors.extend(
            f"tracked path matches repository ignore rules: {path}"
            for path in tracked_ignored_paths()
        )
        text_errors, markdown = verify_text(paths)
        errors.extend(text_errors)
        errors.extend(verify_skills(paths))
        errors.extend(verify_markdown(markdown, set(paths)))
        subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"repository verification could not run: {exc}", file=sys.stderr)
        return 2

    if errors:
        print("Repository verification failed:", file=sys.stderr)
        for error in sorted(set(errors)):
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        "Repository verification passed: "
        f"{len(paths)} tracked/nonignored paths, {len(markdown)} Markdown files, "
        "local links/anchors, fences, skills, conflict markers, secrets, machine "
        "paths, legacy paths, generated artifacts, submodules, and dependency "
        "boundaries checked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
