"""Lockstep contract revision for the ``nxt-staffing/v1`` Manager API wire.

The service and the Console are built from one checkout. This module derives
one value, the contract revision, from the bytes of every source that gives the
wire its meaning (the staffing domain package, the composition root, the Site
Agent API module, the contract schema, and the Console's staffing decoder,
controller, actions, labels, time helpers, and components). Every request body
and every snapshot or receipt carries the value; the service refuses a write
whose revision is absent or different, and the Console refuses a snapshot or
receipt whose revision is absent or different. Two checkouts whose staffing
semantics differ therefore differ on the wire even when ``schema.json`` and the
frozen examples did not change.

The pinned ``STAFFING_CONTRACT_REVISION`` below is the runtime value. It is
regenerated, together with the Console pin, the frozen examples, the lockstep
manifest, and both contract fingerprint pins, by::

    cd simulation
    uv run --no-sync python -B scripts/staffing_contract_revision.py --write

``--check`` reports every pin that disagrees with the recomputed value and
exits non-zero. The Python and Console contract suites run the same comparison;
CI never passes ``--write``. The source set is the manifest
``docs/contracts/staffing-v1/lockstep.json``; this module, the Console pin
module, the examples, and every test are deliberately outside it so that
rewriting a pin never changes the value it pins.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

STAFFING_CONTRACT_REVISION = "0e951f1e3a6f91976a7d41735bd7e5bef398fd4f73f2b552761ee5713682bded"

LOCKSTEP_MANIFEST = "simulation/docs/contracts/staffing-v1/lockstep.json"
CONTRACT_DIRECTORY = "simulation/docs/contracts/staffing-v1"
PYTHON_PIN = "simulation/scripts/staffing_contract_revision.py"
CONSOLE_PIN = "apps/site-agent-console/lib/staffing-revision.ts"
PYTHON_FINGERPRINT_PIN = "simulation/tests/pilot_ops/test_staffing_wire_contract.py"
CONSOLE_FINGERPRINT_PIN = "apps/site-agent-console/tests/staffing-contract.test.ts"
MANIFEST_SCHEMA = "nxt-staffing-lockstep/v1"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_REVISION_VALUE = re.compile(r'("contract_revision":\s*")[0-9a-f]{64}(")')
_PYTHON_PIN = re.compile(r'^(STAFFING_CONTRACT_REVISION = ")[0-9a-f]{64}(")$', re.MULTILINE)
_CONSOLE_PIN = re.compile(
    r'^(export const STAFFING_CONTRACT_REVISION = ")[0-9a-f]{64}(";)$', re.MULTILINE
)
_PYTHON_FINGERPRINT = re.compile(
    r'^(STAFFING_CONTRACT_FINGERPRINT = ")[0-9a-f]{64}(")$', re.MULTILINE
)
_CONSOLE_FINGERPRINT = re.compile(
    r'^(const STAFFING_CONTRACT_FINGERPRINT = ")[0-9a-f]{64}(";)$', re.MULTILINE
)


class LockstepError(RuntimeError):
    """The lockstep manifest or one of its sources is unusable."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def load_manifest(root: Path) -> dict[str, object]:
    path = root / LOCKSTEP_MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LockstepError(f"unreadable lockstep manifest: {error}") from None
    if (
        type(manifest) is not dict
        or manifest.get("schema") != MANIFEST_SCHEMA
        or set(manifest) != {"schema", "contract_revision", "sources"}
        or type(manifest.get("contract_revision")) is not str
        or _HEX64.fullmatch(manifest["contract_revision"]) is None
    ):
        raise LockstepError("lockstep manifest has an unexpected shape")
    sources = manifest["sources"]
    if type(sources) is not dict or set(sources) != {"files", "directories"}:
        raise LockstepError("lockstep manifest sources have an unexpected shape")
    return manifest


def _skipped(name: str) -> bool:
    return name.startswith(".") or name == "__pycache__"


def _walk(directory: Path, suffixes: tuple[str, ...], found: set[Path]) -> None:
    """Recursive, so a later subpackage or component folder cannot escape the set."""

    for child in sorted(directory.iterdir()):
        if _skipped(child.name):
            continue
        if child.is_dir():
            _walk(child, suffixes, found)
        elif child.is_file() and child.suffix in suffixes:
            found.add(child)


def source_bytes(path: Path) -> bytes:
    """File bytes with CRLF folded to LF, so a checkout's line-ending setting
    cannot move the revision; every other byte counts."""

    return path.read_bytes().replace(b"\r\n", b"\n")


def lockstep_sources(root: Path) -> list[Path]:
    """Every file whose bytes enter the revision, as sorted repository paths."""

    sources = load_manifest(root)["sources"]
    assert type(sources) is dict
    found: set[Path] = set()
    for entry in sources["files"]:
        if type(entry) is not str or not entry:
            raise LockstepError("lockstep file entry is not a path")
        path = root / entry
        if not path.is_file():
            raise LockstepError(f"lockstep source is missing: {entry}")
        found.add(path)
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
    return sorted(found, key=lambda path: _relative(root, path))


def lockstep_manifest_text(root: Path) -> str:
    """One ``<path> <sha256>`` line per source, in path order."""

    lines = []
    for path in lockstep_sources(root):
        lines.append(f"{_relative(root, path)} {hashlib.sha256(source_bytes(path)).hexdigest()}\n")
    return "".join(lines)


def compute_contract_revision(root: Path | None = None) -> str:
    root = repository_root() if root is None else root
    return hashlib.sha256(lockstep_manifest_text(root).encode("utf-8")).hexdigest()


def contract_fingerprint(root: Path | None = None) -> str:
    """SHA-256 over ``schema.json`` followed by every example in name order."""

    root = repository_root() if root is None else root
    contract = root / CONTRACT_DIRECTORY
    digest = hashlib.sha256()
    digest.update((contract / "schema.json").read_bytes())
    for path in sorted((contract / "examples").glob("*.json")):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def example_paths(root: Path) -> list[Path]:
    return sorted((root / CONTRACT_DIRECTORY / "examples").glob("*.json"))


def example_revisions(root: Path) -> set[str]:
    """Every ``contract_revision`` value carried by the frozen examples."""

    values: set[str] = set()
    for path in example_paths(root):
        values.update(
            match.group(0).rsplit('"', 2)[1]
            for match in _REVISION_VALUE.finditer(path.read_text(encoding="utf-8"))
        )
    return values


def _pinned(pattern: re.Pattern[str], text: str, label: str) -> str:
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise LockstepError(f"{label}: expected exactly one pin, found {len(matches)}")
    found = pattern.search(text)
    assert found is not None
    return found.group(0).rsplit('"', 2)[1]


def pinned_values(root: Path) -> dict[str, str]:
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
    """Every pin that disagrees with the recomputed revision or fingerprint."""

    revision = compute_contract_revision(root)
    problems = []
    for label, value in pinned_values(root).items():
        if value != revision:
            problems.append(f"{label} contract revision pin {value} != {revision}")
    examples = example_revisions(root)
    if examples != {revision}:
        problems.append(f"example contract revisions {sorted(examples)} != {revision}")
    fingerprint = contract_fingerprint(root)
    for label, value in pinned_fingerprints(root).items():
        if value != fingerprint:
            problems.append(f"{label} contract fingerprint pin {value} != {fingerprint}")
    return problems


def _rewrite(
    path: Path, pattern: re.Pattern[str], value: str, label: str, *, required: bool = True
) -> bool:
    text = path.read_text(encoding="utf-8")
    if len(pattern.findall(text)) < 1:
        if required:
            raise LockstepError(f"{label}: no pin to rewrite in {path}")
        return False
    updated = pattern.sub(lambda match: f"{match.group(1)}{value}{match.group(2)}", text)
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def write(root: Path) -> dict[str, object]:
    """Rewrite every pin to the recomputed revision, then both fingerprint pins."""

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
    return {"contract_revision": revision, "contract_fingerprint": fingerprint, "changed": changed}


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
            return 0
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
