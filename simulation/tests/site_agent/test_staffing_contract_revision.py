"""Lockstep contract revision: the pinned value is recomputed from the covered
staffing sources and their declared dependencies, the dependency guard keeps
that set complete, every example is checked individually, line endings never
move a pin, and the revision is enforced in both mixed directions over an
isolated synthetic ledger (no demo or persistent ledger is touched)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import shutil

import pytest
from nxt_pilot_ops.staffing.contracts import StaffingError
from nxt_site_agent import SiteAgentError

from scripts import staffing_contract_revision as revision_module
from scripts.staffing_contract_revision import STAFFING_CONTRACT_REVISION
from scripts.staffing_operations import (
    admit_contract_revision,
    public_domain_error,
)
from tests.site_agent.test_staffing_composition import (
    FLOW_SERVICE_DATE,
    exchange_request,
    flow_generation,
    flow_leave,
    validator,
)
from tests.site_agent.test_staffing_review_wire import (
    MORNING,
    MovableClock,
    _evening_roster,
    _flow,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONSOLE_PIN = REPOSITORY_ROOT / "apps/site-agent-console/lib/staffing-revision.ts"
LOCKSTEP = REPOSITORY_ROOT / "simulation/docs/contracts/staffing-v1/lockstep.json"
CONTRACT = REPOSITORY_ROOT / revision_module.CONTRACT_DIRECTORY
FOREIGN_REVISION = "f" * 64
ZERO = "0" * 64


def _ledger_bytes(ledger_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ledger_root).as_posix(): path.read_bytes()
        for path in sorted(ledger_root.rglob("*"))
        if path.is_file()
    }


def _ledger_root(tmp_path: Path) -> Path:
    """The isolated synthetic ledger ``_flow`` opens under the test's own directory."""

    root = tmp_path / "flow"
    assert root.is_dir()
    return root


def _relative(paths: list[Path]) -> list[str]:
    return [path.relative_to(REPOSITORY_ROOT).as_posix() for path in paths]


def _crlf(raw: bytes) -> bytes:
    """CRLF line endings regardless of the checkout's own setting."""

    return raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def test_pinned_revision_is_the_digest_of_the_lockstep_source_set() -> None:
    computed = revision_module.compute_contract_revision(REPOSITORY_ROOT)
    assert re.fullmatch(r"[0-9a-f]{64}", computed)
    assert STAFFING_CONTRACT_REVISION == computed, (
        "the staffing implementation changed; regenerate every pin with "
        "`uv run --no-sync python -B scripts/staffing_contract_revision.py --write`"
    )
    pins = revision_module.pinned_values(REPOSITORY_ROOT)
    assert pins == {"python": computed, "console": computed, "manifest": computed}
    assert json.loads(LOCKSTEP.read_text(encoding="utf-8"))["contract_revision"] == computed
    assert re.search(
        r'^export const STAFFING_CONTRACT_REVISION = "%s";$' % computed,
        CONSOLE_PIN.read_text(encoding="utf-8"),
        re.M,
    )
    assert revision_module.example_revision_problems(REPOSITORY_ROOT, computed) == []
    assert revision_module.dependency_problems(REPOSITORY_ROOT) == []
    assert revision_module.check(REPOSITORY_ROOT) == []
    fingerprint = revision_module.contract_fingerprint(REPOSITORY_ROOT)
    assert revision_module.pinned_fingerprints(REPOSITORY_ROOT) == {
        "python": fingerprint,
        "console": fingerprint,
    }


def test_lockstep_covered_set_includes_sources_and_dependencies_and_excludes_every_pin() -> None:
    files = revision_module.manifest_files(REPOSITORY_ROOT)
    sources = _relative(files["sources"])
    dependencies = _relative(files["dependencies"])
    boundaries = _relative(files["boundaries"])
    covered = _relative(revision_module.lockstep_sources(REPOSITORY_ROOT))
    assert covered == sorted(set(sources) | set(dependencies))
    for required in (
        "simulation/nxt_pilot_ops/staffing/operations.py",
        "simulation/nxt_pilot_ops/staffing/review.py",
        "simulation/nxt_pilot_ops/staffing/time.py",
        "simulation/scripts/staffing_operations.py",
        "simulation/nxt_site_agent/api.py",
        "simulation/docs/contracts/staffing-v1/schema.json",
        "apps/site-agent-console/lib/staffing.ts",
        "apps/site-agent-console/lib/staffing-state.ts",
        "apps/site-agent-console/lib/staffing-actions.ts",
        "apps/site-agent-console/lib/staffing-labels.ts",
        "apps/site-agent-console/lib/site-time.ts",
        "apps/site-agent-console/components/StaffingPanel.tsx",
        "apps/site-agent-console/components/staffing/CandidateCards.tsx",
    ):
        assert required in sources, required
    # Staffing-facing dependencies enter the digest: canonical serialization
    # (stable_digest behind every ledger record), the provider gateway, the
    # Manager API constants, and the Console's shared transport and primitives.
    for required in (
        "simulation/nxt_pilot_ops/serialization.py",
        "simulation/nxt_model_gateway/__init__.py",
        "simulation/nxt_model_gateway/serialization.py",
        "simulation/nxt_model_gateway/routing.py",
        "simulation/nxt_model_gateway/adapters.py",
        "simulation/nxt_model_gateway/kimi.py",
        "simulation/nxt_model_gateway/openai.py",
        "simulation/nxt_model_gateway/anthropic.py",
        "simulation/nxt_model_gateway/transport.py",
        "simulation/nxt_site_agent/contracts.py",
        "apps/site-agent-console/lib/api.ts",
        "apps/site-agent-console/components/ui.tsx",
    ):
        assert required in dependencies, required
        assert required in covered, required
    for boundary in (
        "simulation/scripts/staffing_contract_revision.py",
        "apps/site-agent-console/lib/staffing-revision.ts",
        "simulation/nxt_site_agent/__init__.py",
        "simulation/nxt_site_agent/service.py",
    ):
        assert boundary in boundaries, boundary
        assert boundary not in covered, boundary
    assert "simulation/docs/contracts/staffing-v1/lockstep.json" not in covered
    assert not any("/tests/" in item or "/examples/" in item for item in covered)
    manifest = revision_module.lockstep_manifest_text(REPOSITORY_ROOT)
    assert manifest.count("\n") == len(covered)
    assert [line.split(" ")[0] for line in manifest.splitlines()] == covered


def test_dependency_guard_classifies_every_first_party_import_of_the_covered_set() -> None:
    """Every module the covered set reaches is a source, a dependency, or a
    declared boundary; nothing listed is unreachable; a reviewed reason stands
    behind every boundary."""

    files = revision_module.manifest_files(REPOSITORY_ROOT)
    classified = {path: category for category in files for path in files[category]}
    reached: set[Path] = set()
    for path in files["sources"] + files["dependencies"]:
        for imported in revision_module.first_party_imports(path, REPOSITORY_ROOT):
            assert imported in classified, f"{imported} imported by {path} is not classified"
            reached.add(imported)
    for path in files["dependencies"] + files["boundaries"]:
        assert path in reached, f"{path} is listed but nothing covered imports it"
    manifest = json.loads(LOCKSTEP.read_text(encoding="utf-8"))
    assert all(entry["reason"].strip() for entry in manifest["boundaries"])
    assert revision_module.dependency_problems(REPOSITORY_ROOT) == []
    # The user-visible examples of the gap: a byte in either moves the value.
    serialization = REPOSITORY_ROOT / "simulation/nxt_pilot_ops/serialization.py"
    gateway_serialization = REPOSITORY_ROOT / "simulation/nxt_model_gateway/serialization.py"
    assert "def stable_digest" in gateway_serialization.read_text(encoding="utf-8")
    assert serialization in reached and gateway_serialization in reached
    # Every name taken through a boundary is the one its entry declares, and
    # the package roots executed on a dotted import are classified too.
    declared = {entry["path"]: set(entry["names"]) for entry in manifest["boundaries"]}
    taken: dict[str, set[str]] = {}
    for path in files["sources"] + files["dependencies"]:
        for edge in revision_module.import_edges(path, REPOSITORY_ROOT):
            relative = edge.target.relative_to(REPOSITORY_ROOT).as_posix()
            if relative in declared:
                taken.setdefault(relative, set()).update(edge.names)
    assert taken["simulation/nxt_site_agent/__init__.py"] == {"SiteAgentError"}
    assert taken["simulation/nxt_site_agent/service.py"] == {"SiteAgentService"}
    assert taken["simulation/scripts/staffing_contract_revision.py"] == {"STAFFING_CONTRACT_REVISION"}
    assert taken["apps/site-agent-console/lib/staffing-revision.ts"] == {"STAFFING_CONTRACT_REVISION"}
    assert taken["simulation/nxt_pilot_ops/__init__.py"] == set(), "executed on the dotted path, nothing taken"
    for relative, names in taken.items():
        assert names <= declared[relative], (relative, names)
    assert "simulation/nxt_pilot_ops/__init__.py" in declared


def test_mutating_a_covered_dependency_moves_the_revision_and_fails_check(tmp_path: Path) -> None:
    """In an isolated copy of the covered files, changing ``stable_digest`` in
    ``nxt_model_gateway/serialization.py`` or canonical serialization in
    ``nxt_pilot_ops/serialization.py`` moves the revision, so ``--check`` fails
    until the pins are regenerated."""

    root = _copy_covered_repository(tmp_path)
    assert revision_module.compute_contract_revision(root) == STAFFING_CONTRACT_REVISION
    assert revision_module.check(root) == []
    for relative in ("simulation/nxt_model_gateway/serialization.py", "simulation/nxt_pilot_ops/serialization.py"):
        target = root / relative
        original = target.read_bytes()
        target.write_bytes(original + b"\n# mutation: a different digest rule\n")
        assert revision_module.compute_contract_revision(root) != STAFFING_CONTRACT_REVISION, relative
        problems = revision_module.check(root)
        assert any("contract revision pin" in line for line in problems), relative
        target.write_bytes(original)
        assert revision_module.compute_contract_revision(root) == STAFFING_CONTRACT_REVISION, relative


def _copy_covered_repository(tmp_path: Path) -> Path:
    """Copy every manifest path, the manifest, the pins, and the contract
    directory into an isolated root so mutations never touch the checkout."""

    root = tmp_path / "copy"
    files = revision_module.manifest_files(REPOSITORY_ROOT)
    for path in files["sources"] + files["dependencies"] + files["boundaries"]:
        target = root / path.relative_to(REPOSITORY_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)
    for relative in (
        revision_module.LOCKSTEP_MANIFEST,
        revision_module.PYTHON_PIN,
        revision_module.CONSOLE_PIN,
        revision_module.PYTHON_FINGERPRINT_PIN,
        revision_module.CONSOLE_FINGERPRINT_PIN,
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPOSITORY_ROOT / relative, target)
    shutil.copytree(CONTRACT, root / revision_module.CONTRACT_DIRECTORY, dirs_exist_ok=True)
    tsconfig = Path("apps/site-agent-console/tsconfig.json")
    (root / tsconfig).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPOSITORY_ROOT / tsconfig, root / tsconfig)
    return root


def test_lockstep_manifest_rejects_a_missing_or_malformed_source(tmp_path: Path) -> None:
    (tmp_path / "simulation/docs/contracts/staffing-v1").mkdir(parents=True)
    manifest = tmp_path / revision_module.LOCKSTEP_MANIFEST
    manifest.write_text(
        json.dumps(
            {
                "schema": "nxt-staffing-lockstep/v1",
                "contract_revision": ZERO,
                "sources": {"files": ["missing.py"], "directories": []},
                "dependencies": {"files": []},
                "boundaries": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(revision_module.LockstepError):
        revision_module.compute_contract_revision(tmp_path)
    for malformed in (
        '{"schema": "other"}',
        json.dumps({"schema": "nxt-staffing-lockstep/v1", "contract_revision": ZERO, "sources": {"files": [], "directories": []}}),
        json.dumps({"schema": "nxt-staffing-lockstep/v1", "contract_revision": ZERO, "sources": {"files": [], "directories": []}, "dependencies": {"files": []}, "boundaries": [{"path": "x.py", "reason": "r"}]}),
        json.dumps({"schema": "nxt-staffing-lockstep/v1", "contract_revision": ZERO, "sources": {"files": [], "directories": []}, "dependencies": {"files": []}, "boundaries": [{"path": "x.py", "reason": "  ", "names": []}]}),
        json.dumps({"schema": "nxt-staffing-lockstep/v1", "contract_revision": ZERO, "sources": {"files": [], "directories": []}, "dependencies": {"files": []}, "boundaries": [{"path": "x.py", "reason": "r", "names": [""]}]}),
    ):
        manifest.write_text(malformed, encoding="utf-8")
        with pytest.raises(revision_module.LockstepError):
            revision_module.compute_contract_revision(tmp_path)


def test_admission_strips_only_the_pinned_revision() -> None:
    body = {"schema": "nxt-staffing-exception/v1", "contract_revision": STAFFING_CONTRACT_REVISION, "x": 1}
    assert admit_contract_revision(body) == {"schema": "nxt-staffing-exception/v1", "x": 1}
    assert "contract_revision" in body, "the caller's body is not mutated"
    for missing in ({"schema": "nxt-staffing-exception/v1"}, {}, {"contract_revision": None}, {"contract_revision": 7}, {"contract_revision": "abc"}):
        with pytest.raises(SiteAgentError) as refused:
            admit_contract_revision(missing)
        assert refused.value.code == "staffing_invalid_request"
    with pytest.raises(SiteAgentError) as mismatch:
        admit_contract_revision({"schema": "nxt-staffing-exception/v1", "contract_revision": FOREIGN_REVISION})
    assert mismatch.value.code == "staffing_contract_mismatch"
    assert FOREIGN_REVISION not in mismatch.value.detail


def test_new_service_rejects_an_old_or_foreign_write_without_appending(tmp_path: Path) -> None:
    api, ledger, _ = _flow(tmp_path, MovableClock(MORNING))
    try:
        root = _ledger_root(tmp_path)
        api.route("POST", "/api/v1/staffing/roster-imports", _evening_roster("roster-1"))
        before = ledger.verify()
        bytes_before = _ledger_bytes(root)

        # A body from a console built before the field existed (02e1d7d and earlier).
        old_write = flow_leave("leave-old", roster=1, exception_set=0)
        del old_write["contract_revision"]
        with pytest.raises(SiteAgentError) as refused_old:
            api.route("POST", "/api/v1/staffing/exceptions", old_write)
        assert refused_old.value.code == "staffing_invalid_request"

        # A well-formed body from a console built with another checkout.
        foreign_write = flow_leave("leave-foreign", roster=1, exception_set=0)
        foreign_write["contract_revision"] = FOREIGN_REVISION
        for _ in range(2):
            with pytest.raises(SiteAgentError) as refused_foreign:
                api.route("POST", "/api/v1/staffing/exceptions", foreign_write)
            assert refused_foreign.value.code == "staffing_contract_mismatch"
            assert refused_foreign.value.detail == "console and service contract revisions differ"

        # Every POST route admits the revision before anything else.
        for path in (
            "/api/v1/staffing/roster-imports",
            "/api/v1/staffing/exceptions/exception-1/cancel",
            "/api/v1/staffing/exceptions/exception-1/correct",
            "/api/v1/staffing/suggestions",
            "/api/v1/staffing/suggestions/generation-1/accept",
            "/api/v1/staffing/suggestions/generation-1/modify",
            "/api/v1/staffing/suggestions/generation-1/reject",
        ):
            with pytest.raises(SiteAgentError) as refused_route:
                api.route("POST", path, {**foreign_write, "contract_revision": FOREIGN_REVISION})
            assert refused_route.value.code == "staffing_contract_mismatch", path
            with pytest.raises(SiteAgentError) as refused_missing:
                api.route("POST", path, {key: value for key, value in foreign_write.items() if key != "contract_revision"})
            assert refused_missing.value.code == "staffing_invalid_request", path

        assert ledger.verify() == before
        assert _ledger_bytes(root) == bytes_before
        for request_id in ("leave-old", "leave-foreign"):
            with pytest.raises(SiteAgentError) as missing:
                api.route("GET", f"/api/v1/staffing/requests/exception-record/{request_id}", {})
            assert missing.value.code == "staffing_request_not_found"
    finally:
        api.close()


def test_old_service_rejects_a_new_write_by_its_closed_key_rule(tmp_path: Path) -> None:
    """The service of 02e1d7d hands every POST body straight to the domain, whose
    request admission has always rejected a key outside its closed set. That
    unchanged rule is what refuses a body carrying ``contract_revision``."""

    api, ledger, _ = _flow(tmp_path, MovableClock(MORNING))
    try:
        owner = api._router._owner  # noqa: SLF001 - the domain the older route called directly
        before = _ledger_bytes(_ledger_root(tmp_path))
        with pytest.raises(StaffingError) as refused_roster:
            owner.import_roster(_evening_roster("roster-new"), recorded_at=MORNING)
        assert refused_roster.value.code == "staffing_unknown_field"
        assert public_domain_error(refused_roster.value).code == "staffing_invalid_request"
        assert _ledger_bytes(_ledger_root(tmp_path)) == before

        # With a roster in place the same rule refuses every other new body at
        # every domain write entry the older route called, before any lookup.
        api.route("POST", "/api/v1/staffing/roster-imports", _evening_roster("roster-1"))
        verified = ledger.verify()
        cancel = {**exchange_request("exception-correction.json", "exception-cancelled"), "exception_id": "exception-1"}
        correct = {**exchange_request("exception-correction.json", "exception-corrected"), "exception_id": "exception-1"}
        entries = [
            ("record_exception", lambda: owner.record_exception(flow_leave("leave-new", roster=1, exception_set=0), recorded_at=MORNING)),
            ("cancel_exception", lambda: owner.cancel_exception(cancel, recorded_at=MORNING)),
            ("correct_exception", lambda: owner.correct_exception(correct, recorded_at=MORNING)),
            ("commit_manager_response", lambda: owner.commit_manager_response(
                exchange_request("manager-response.json", "manager-accepted"), suggestion_id="generation-1", recorded_at=MORNING,
            )),
            ("worker.submit", lambda: api._worker.submit(flow_generation("generate-new", roster=1, exception_set=0, effective_plan=0))),  # noqa: SLF001
        ]
        for label, call in entries:
            with pytest.raises(StaffingError) as refused:
                call()
            assert refused.value.code in {"staffing_unknown_field", "staffing_invalid_request"}, label
            assert public_domain_error(refused.value).code == "staffing_invalid_request", label
        assert ledger.verify() == verified
    finally:
        api.close()


def test_pin_module_is_stdlib_only_and_imports_no_package() -> None:
    module = REPOSITORY_ROOT / revision_module.PYTHON_PIN
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "argparse", "ast", "hashlib", "json", "pathlib", "re", "sys"}, sorted(imported)
    assert not any(name.startswith("nxt_") or name in {"scripts", "socket", "urllib", "http", "subprocess", "os"} for name in imported)


def _layout(tmp_path: Path) -> Path:
    """A scratch repository with a small covered set, a dependency, a boundary,
    every pin file, and a frozen example directory in the real shapes."""

    (tmp_path / "src/dir/nested").mkdir(parents=True)
    (tmp_path / "src/shared").mkdir()
    (tmp_path / "src/__init__.py").write_bytes(b"")
    (tmp_path / "src/a.py").write_bytes(b"from .dep import VALUE\nfrom .edge import EDGE\nTOTAL = VALUE + EDGE\n")
    (tmp_path / "src/dep.py").write_bytes(b"VALUE = 1\n")
    (tmp_path / "src/edge.py").write_bytes(b"from .beyond import BEYOND\nEDGE = BEYOND\n")
    (tmp_path / "src/beyond.py").write_bytes(b"BEYOND = 2\n")
    (tmp_path / "src/stray.py").write_bytes(b"STRAY = 3\n")
    (tmp_path / "src/dir/top.ts").write_bytes(b'import { api } from "@/shared/api";\nimport { PIN } from "../pin";\nexport const top = api(PIN);\n')
    (tmp_path / "src/tsconfig.json").write_text('{"compilerOptions": {"paths": {"@/*": ["./*"]}}}\n', encoding="utf-8")
    (tmp_path / "src/dir/nested/b.ts").write_bytes(b"export const x = 1;\n")
    (tmp_path / "src/dir/nested/.hidden.ts").write_bytes(b"ignored\n")
    (tmp_path / "src/dir/__pycache__").mkdir()
    (tmp_path / "src/dir/__pycache__/c.ts").write_bytes(b"ignored\n")
    (tmp_path / "src/dir/other.txt").write_bytes(b"ignored\n")
    (tmp_path / "src/shared/api.ts").write_bytes(b"export const api = (value: string) => value;\n")
    (tmp_path / "src/pin.ts").write_bytes(b'export const PIN = "pin";\n')
    contract = tmp_path / revision_module.CONTRACT_DIRECTORY
    (contract / "examples").mkdir(parents=True)
    (contract / "schema.json").write_text("{}\n", encoding="utf-8")
    (contract / "examples/success.json").write_text(
        json.dumps(
            {
                "schema": "nxt-staffing-exchanges/v1",
                "exchanges": [
                    {"name": "read", "method": "GET", "body": {"data": {"contract_revision": ZERO}}},
                    {"name": "write", "method": "POST", "request": {"contract_revision": ZERO}, "body": {"data": {"contract_revision": ZERO}}},
                ],
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    (contract / "examples/errors.json").write_text('{"schema": "nxt-staffing-errors/v1", "exchanges": []}\n', encoding="utf-8")
    (contract / "lockstep.json").write_text(
        json.dumps(
            {
                "schema": "nxt-staffing-lockstep/v1",
                "contract_revision": ZERO,
                "sources": {"files": ["src/a.py"], "directories": [{"path": "src/dir", "suffixes": [".ts"]}]},
                "dependencies": {"files": ["src/dep.py", "src/shared/api.ts"]},
                "boundaries": [
                    {"path": "src/edge.py", "reason": "stops here", "names": ["EDGE"]},
                    {"path": "src/pin.ts", "reason": "the pin", "names": ["PIN"]},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    for relative, text in (
        (revision_module.PYTHON_PIN, 'STAFFING_CONTRACT_REVISION = "%s"\n' % ZERO),
        (revision_module.CONSOLE_PIN, 'export const STAFFING_CONTRACT_REVISION = "%s";\n' % ZERO),
        (revision_module.PYTHON_FINGERPRINT_PIN, 'STAFFING_CONTRACT_FINGERPRINT = "%s"\n' % ZERO),
        (revision_module.CONSOLE_FINGERPRINT_PIN, 'const STAFFING_CONTRACT_FINGERPRINT = "%s";\n' % ZERO),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _set_manifest(root: Path, **changes: object) -> None:
    path = root / revision_module.LOCKSTEP_MANIFEST
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(changes)
    path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")


def test_write_regenerates_every_pin_and_check_detects_any_source_drift(tmp_path: Path) -> None:
    root = _layout(tmp_path)
    covered = [path.relative_to(root).as_posix() for path in revision_module.lockstep_sources(root)]
    assert covered == ["src/a.py", "src/dep.py", "src/dir/nested/b.ts", "src/dir/top.ts", "src/shared/api.ts"], (
        "recursive, suffix-filtered, dotfiles and __pycache__ skipped, dependencies covered, boundaries not"
    )
    assert revision_module.dependency_problems(root) == []
    assert len(revision_module.check(root)) >= 3, "placeholder pins disagree with the computed value"

    result = revision_module.write(root)
    revision = result["contract_revision"]
    assert re.fullmatch(r"[0-9a-f]{64}", revision) and revision != ZERO
    assert result["problems"] == []
    assert revision_module.check(root) == []
    assert revision_module.pinned_values(root) == {"python": revision, "console": revision, "manifest": revision}
    assert revision_module.example_revision_problems(root, revision) == []
    example = (root / revision_module.CONTRACT_DIRECTORY / "examples/success.json").read_text(encoding="utf-8")
    assert example.count(revision) == 3 and '"contract_revision": "%s"' % revision in example, "spacing preserved"
    assert (root / revision_module.CONTRACT_DIRECTORY / "examples/errors.json").read_text(encoding="utf-8") == (
        '{"schema": "nxt-staffing-errors/v1", "exchanges": []}\n'
    )
    fingerprint = revision_module.contract_fingerprint(root)
    assert revision_module.pinned_fingerprints(root) == {"python": fingerprint, "console": fingerprint}
    assert revision_module.write(root)["changed"] == [], "a second write is a no-op"

    # One byte in a nested source or in a dependency moves the revision.
    for relative in ("src/dir/nested/b.ts", "src/dep.py", "src/shared/api.ts"):
        target = root / relative
        original = target.read_bytes()
        target.write_bytes(original + b"// mutation\n" if relative.endswith(".ts") else original + b"# mutation\n")
        drift = revision_module.check(root)
        assert drift, relative
        assert all("contract revision pin" in line or "contract_revision" in line for line in drift), (relative, drift)
        assert not any("unlisted" in line or "stale" in line or "fingerprint" in line for line in drift), (relative, drift)
        target.write_bytes(original)
    assert revision_module.check(root) == []
    # A byte behind a boundary does not: the digest deliberately stops there.
    (root / "src/beyond.py").write_bytes(b"BEYOND = 3\n")
    (root / "src/pin.ts").write_bytes(b'export const PIN = "other";\n')
    assert revision_module.check(root) == []

    # CRLF is folded before hashing, so a line-ending setting cannot move it.
    (root / "src/dep.py").write_bytes(b"VALUE = 1\r\n")
    assert revision_module.compute_contract_revision(root) == revision
    # A missing source fails loud rather than silently shrinking the set.
    (root / "src/a.py").unlink()
    with pytest.raises(revision_module.LockstepError):
        revision_module.compute_contract_revision(root)


def test_dependency_guard_fails_on_unlisted_stale_or_double_listed_modules(tmp_path: Path) -> None:
    root = _layout(tmp_path)
    revision_module.write(root)
    assert revision_module.check(root) == []

    # A new first-party import that the manifest does not classify.
    a = root / "src/a.py"
    original = a.read_bytes()
    a.write_bytes(original + b"from .stray import STRAY\n")
    problems = revision_module.check(root)
    assert any("unlisted first-party dependency src/stray.py imported by src/a.py" in line for line in problems), problems
    # Listing it as a dependency covers it (and moves the revision).
    _set_manifest(root, dependencies={"files": ["src/dep.py", "src/shared/api.ts", "src/stray.py"]})
    assert all("unlisted" not in line for line in revision_module.check(root))
    assert revision_module.write(root)["problems"] == []
    a.write_bytes(original)
    _set_manifest(root, dependencies={"files": ["src/dep.py", "src/shared/api.ts"]})
    revision_module.write(root)
    assert revision_module.check(root) == []

    # A Console relative import is walked the same way.
    top = root / "src/dir/top.ts"
    top_original = top.read_bytes()
    (root / "src/shared/extra.ts").write_bytes(b"export const extra = 1;\n")
    top.write_bytes(top_original + b'import { extra } from "../shared/extra";\nexport const e = extra;\n')
    assert any("unlisted first-party dependency src/shared/extra.ts imported by src/dir/top.ts" in line for line in revision_module.check(root))
    top.write_bytes(top_original)
    revision_module.write(root)
    assert revision_module.check(root) == []

    # A listed dependency or boundary that nothing covered imports is stale.
    a.write_bytes(b"from .edge import EDGE\nTOTAL = EDGE\n")
    revision_module.write(root)
    assert any("stale dependency src/dep.py" in line for line in revision_module.check(root))
    a.write_bytes(b"from .dep import VALUE\nTOTAL = VALUE\n")
    revision_module.write(root)
    assert any("stale boundary src/edge.py" in line for line in revision_module.check(root))
    a.write_bytes(original)
    revision_module.write(root)
    assert revision_module.check(root) == []

    # A module listed under two categories.
    boundaries = [{"path": "src/edge.py", "reason": "stops here", "names": ["EDGE"]}, {"path": "src/pin.ts", "reason": "the pin", "names": ["PIN"]}]
    _set_manifest(root, boundaries=[*boundaries, {"path": "src/dep.py", "reason": "conflict", "names": []}])
    assert any("src/dep.py is listed as both dependencies and boundaries" in line for line in revision_module.check(root))
    _set_manifest(root, boundaries=boundaries)
    assert revision_module.check(root) == []


def test_dependency_guard_follows_aliases_subpackages_package_roots_and_declared_names(tmp_path: Path) -> None:
    root = _layout(tmp_path)
    revision_module.write(root)
    assert revision_module.check(root) == []
    a = root / "src/a.py"
    original = a.read_bytes()
    top = root / "src/dir/top.ts"
    top_original = top.read_bytes()

    # The tsconfig path alias resolves like a relative import (top.ts already
    # takes api through "@/shared/api"); an aliased import of an unlisted file fails.
    (root / "src/shared/other.ts").write_bytes(b"export const other = 1;\n")
    top.write_bytes(top_original + b'import { other } from "@/shared/other";\nexport const o = other;\n')
    assert any("unlisted first-party dependency src/shared/other.ts imported by src/dir/top.ts" in line for line in revision_module.check(root))
    # Two statements on one line are both followed.
    top.write_bytes(b'import { api } from "@/shared/api"; import { other } from "../shared/other";\nexport const top = api(String(other));\n')
    problems = revision_module.check(root)
    assert any("src/shared/other.ts imported by src/dir/top.ts" in line for line in problems)
    assert any("stale boundary src/pin.ts" in line for line in problems)
    top.write_bytes(top_original)
    revision_module.write(root)
    assert revision_module.check(root) == []

    # `from . import subpackage` resolves the package directory, not only a module file.
    (root / "src/subpkg").mkdir()
    (root / "src/subpkg/__init__.py").write_bytes(b"SUB = 1\n")
    a.write_bytes(original + b"from . import subpkg\n")
    assert any("unlisted first-party dependency src/subpkg/__init__.py imported by src/a.py" in line for line in revision_module.check(root))
    a.write_bytes(original)

    # A dotted first-party import executes every package root on its path:
    # each is an edge that must be classified, with no names taken.
    (root / "simulation/nxt_fake/sub").mkdir(parents=True)
    (root / "simulation/nxt_fake/__init__.py").write_bytes(b"from .heavy import HEAVY\n")
    (root / "simulation/nxt_fake/heavy.py").write_bytes(b"HEAVY = 1\n")
    (root / "simulation/nxt_fake/sub/__init__.py").write_bytes(b"")
    (root / "simulation/nxt_fake/sub/mod.py").write_bytes(b"X = 1\n")
    a.write_bytes(original + b"from nxt_fake.sub.mod import X\n")
    problems = revision_module.check(root)
    for relative in ("simulation/nxt_fake/__init__.py", "simulation/nxt_fake/sub/__init__.py", "simulation/nxt_fake/sub/mod.py"):
        assert any(f"unlisted first-party dependency {relative} imported by src/a.py" in line for line in problems), (relative, problems)
    _set_manifest(
        root,
        dependencies={"files": ["src/dep.py", "src/shared/api.ts", "simulation/nxt_fake/sub/__init__.py", "simulation/nxt_fake/sub/mod.py"]},
        boundaries=[
            {"path": "src/edge.py", "reason": "stops here", "names": ["EDGE"]},
            {"path": "src/pin.ts", "reason": "the pin", "names": ["PIN"]},
            {"path": "simulation/nxt_fake/__init__.py", "reason": "package root executed on the way", "names": []},
        ],
    )
    assert revision_module.write(root)["problems"] == []
    covered = [path.relative_to(root).as_posix() for path in revision_module.lockstep_sources(root)]
    assert "simulation/nxt_fake/sub/mod.py" in covered and "simulation/nxt_fake/__init__.py" not in covered
    # heavy.py sits behind the boundary: not walked, not covered.
    assert "simulation/nxt_fake/heavy.py" not in covered

    # A name taken through a boundary that its entry does not declare.
    a.write_bytes(original.replace(b"from .edge import EDGE\n", b"from .edge import EDGE, OTHER\n"))
    assert any("boundary src/edge.py: src/a.py takes undeclared name(s) OTHER" in line for line in revision_module.check(root))
    a.write_bytes(original.replace(b"from .edge import EDGE\n", b"from . import edge\nEDGE = edge.EDGE\n"))
    assert any("boundary src/edge.py: src/a.py takes undeclared name(s) *" in line for line in revision_module.check(root))
    top.write_bytes(top_original.replace(b'import { PIN } from "../pin";', b'import * as pin from "../pin";\nconst PIN = pin.PIN;'))
    assert any("boundary src/pin.ts: src/dir/top.ts takes undeclared name(s) *" in line for line in revision_module.check(root))
    top.write_bytes(top_original)
    a.write_bytes(original + b"from nxt_fake.sub.mod import X\n")
    assert revision_module.check(root) == []

    # Dynamic imports are refused rather than silently unanalysed.
    a.write_bytes(original + b"from nxt_fake.sub.mod import X\nimport importlib\n")
    assert any("dynamic import in" in line and "importlib" in line for line in revision_module.check(root))
    a.write_bytes(original + b"from nxt_fake.sub.mod import X\nmodule = __import__('json')\n")
    assert any("dynamic import in" in line and "__import__" in line for line in revision_module.check(root))
    a.write_bytes(original + b"from nxt_fake.sub.mod import X\n")
    top.write_bytes(top_original + b'const lazy = require("../shared/api");\n')
    assert any("dynamic import in" in line and "require" in line for line in revision_module.check(root))
    top.write_bytes(top_original + b'export const later = () => import("../shared/api");\n')
    problems = revision_module.check(root)
    assert not any("dynamic import" in line or "unlisted" in line for line in problems), (
        "a dynamic import() of a covered dependency is an ordinary edge; only the covered byte change moves the pins",
        problems,
    )
    assert all("contract revision pin" in line or "contract_revision" in line for line in problems), problems
    top.write_bytes(top_original)
    assert revision_module.check(root) == []

    # A symlinked checkout resolves to the same answer.
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    assert revision_module.check(link) == []
    assert revision_module.compute_contract_revision(link) == revision_module.compute_contract_revision(root)
    # A symbolic link inside a covered directory fails loud on both sides
    # rather than being hashed by one and skipped by the other.
    (root / "src/dir/linked.ts").symlink_to(root / "src/shared/api.ts")
    with pytest.raises(revision_module.LockstepError):
        revision_module.compute_contract_revision(root)
    (root / "src/dir/linked.ts").unlink()
    assert revision_module.check(root) == []


def test_check_requires_the_revision_in_each_example_place_individually(tmp_path: Path) -> None:
    """A revision carried by one example never vouches for another: every
    exchange's data and every POST request is checked on its own, and an
    error example must carry none."""

    root = _copy_covered_repository(tmp_path)
    examples = root / revision_module.CONTRACT_DIRECTORY / "examples"
    assert revision_module.check(root) == []

    def rewrite(name: str, mutate) -> None:  # noqa: ANN001 - test helper
        path = examples / name
        document = json.loads(path.read_text(encoding="utf-8"))
        mutate(document)
        path.write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8")

    originals = {path.name: path.read_bytes() for path in examples.glob("*.json")}

    def restore() -> None:
        for name, raw in originals.items():
            (examples / name).write_bytes(raw)

    # One exchange's response loses the field while every other example still carries the pin.
    rewrite("manager-response.json", lambda doc: doc["exchanges"][2]["body"]["data"].pop("contract_revision"))
    problems = revision_module.check(root)
    assert any(line.startswith("manager-response.json#manager-rejected data: contract_revision is missing") for line in problems), problems
    assert all("manager-accepted" not in line for line in problems)
    restore()

    # One POST request names another checkout.
    def foreign_request(doc: dict) -> None:
        doc["exchanges"][0]["request"]["contract_revision"] = FOREIGN_REVISION

    rewrite("roster-import.json", foreign_request)
    problems = revision_module.check(root)
    assert any(line.startswith("roster-import.json#roster-committed request: contract_revision ") and FOREIGN_REVISION[:8] in line for line in problems), problems
    restore()

    # A GET exchange's data is checked too, and the fingerprint moves with the file.
    rewrite("cold-start.json", lambda doc: doc["exchanges"][1]["body"]["data"].update({"contract_revision": ZERO}))
    problems = revision_module.check(root)
    assert any(line.startswith("cold-start.json#date-empty data: contract_revision ") for line in problems)
    assert any("fingerprint pin" in line for line in problems)
    restore()

    # An error example must carry none.
    rewrite("errors.json", lambda doc: doc["exchanges"][0]["body"].update({"contract_revision": STAFFING_CONTRACT_REVISION}))
    assert any(line == "errors.json: an error example never carries contract_revision" for line in revision_module.check(root))
    restore()

    # The key may appear only where the contract defines it: a stray copy with
    # the right value elsewhere is still a problem (write() would rewrite it).
    rewrite("manager-response.json", lambda doc: doc["exchanges"][0]["body"]["data"]["record"].update({"contract_revision": STAFFING_CONTRACT_REVISION}))
    assert any(line == "manager-response.json: contract_revision at exchanges[0].body.data.record is outside the applicable places" for line in revision_module.check(root))
    restore()
    rewrite("cold-start.json", lambda doc: doc["exchanges"][0].update({"request": {"contract_revision": STAFFING_CONTRACT_REVISION}}))
    assert any(line == "cold-start.json: contract_revision at exchanges[0].request is outside the applicable places" for line in revision_module.check(root))
    restore()

    # write() rewrites existing places only; it never adds a missing one.
    rewrite("result-unknown.json", lambda doc: doc["exchanges"][0]["body"]["data"].pop("contract_revision"))
    result = revision_module.write(root)
    assert any(line.startswith("result-unknown.json#generation-interrupted data: contract_revision is missing") for line in result["problems"])
    restore()
    # That write moved the copy's fingerprint pins with the mutated example;
    # restoring the examples and regenerating returns to the real pins.
    assert revision_module.write(root)["problems"] == []
    assert revision_module.pinned_fingerprints(root) == revision_module.pinned_fingerprints(REPOSITORY_ROOT)
    assert revision_module.check(root) == []


def test_contract_fingerprint_folds_crlf_but_counts_every_other_byte(tmp_path: Path) -> None:
    pinned = revision_module.pinned_fingerprints(REPOSITORY_ROOT)["python"]
    assert revision_module.contract_fingerprint(REPOSITORY_ROOT) == pinned
    root = _copy_covered_repository(tmp_path)
    contract = root / revision_module.CONTRACT_DIRECTORY
    # The whole copy becomes a CRLF checkout: every covered file, the frozen
    # files, the manifest, and every pin file.
    converted = [
        *revision_module.lockstep_sources(root),
        *sorted((contract / "examples").glob("*.json")),
        root / revision_module.LOCKSTEP_MANIFEST,
        root / revision_module.PYTHON_PIN,
        root / revision_module.CONSOLE_PIN,
        root / revision_module.PYTHON_FINGERPRINT_PIN,
        root / revision_module.CONSOLE_FINGERPRINT_PIN,
    ]
    for path in converted:
        path.write_bytes(_crlf(path.read_bytes()))
        assert b"\r\n" in path.read_bytes(), path
    assert revision_module.contract_fingerprint(root) == pinned
    assert revision_module.compute_contract_revision(root) == STAFFING_CONTRACT_REVISION
    assert revision_module.check(root) == [], "an otherwise identical CRLF checkout passes every check"
    # --write on that checkout leaves every line ending exactly as found.
    for path in (root / revision_module.LOCKSTEP_MANIFEST, root / revision_module.PYTHON_PIN, root / revision_module.CONSOLE_PIN, root / revision_module.PYTHON_FINGERPRINT_PIN, root / revision_module.CONSOLE_FINGERPRINT_PIN):
        path.write_bytes(path.read_bytes().replace(STAFFING_CONTRACT_REVISION.encode(), ZERO.encode()).replace(pinned.encode(), ZERO.encode()))
    problems = revision_module.check(root)
    assert sum("contract revision pin" in line for line in problems) == 3 and sum("fingerprint pin" in line for line in problems) == 2
    assert revision_module.write(root)["problems"] == []
    for path in (root / revision_module.LOCKSTEP_MANIFEST, root / revision_module.PYTHON_PIN, root / revision_module.CONSOLE_PIN, root / revision_module.PYTHON_FINGERPRINT_PIN, root / revision_module.CONSOLE_FINGERPRINT_PIN):
        raw = path.read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), path
        assert ZERO.encode() not in raw, path
    assert revision_module.check(root) == []
    cold_start = contract / "examples/cold-start.json"
    cold_start.write_bytes(cold_start.read_bytes().replace(b"SIMULATION", b"SIMULATED", 1))
    assert revision_module.contract_fingerprint(root) != pinned
    assert any("fingerprint pin" in line for line in revision_module.check(root))


def test_snapshot_and_receipts_carry_the_pinned_revision_and_the_domain_never_sees_it(tmp_path: Path) -> None:
    api, ledger, _ = _flow(tmp_path, MovableClock(MORNING))
    try:
        receipt = api.route("POST", "/api/v1/staffing/roster-imports", _evening_roster("roster-1"))
        validator("#/$defs/StaffingReceipt").validate(receipt)
        assert receipt["contract_revision"] == STAFFING_CONTRACT_REVISION
        replay = api.route("GET", "/api/v1/staffing/requests/roster-import/roster-1", {})
        assert replay["contract_revision"] == STAFFING_CONTRACT_REVISION
        assert replay["disposition"] == "duplicate"
        duplicate = api.route("POST", "/api/v1/staffing/roster-imports", _evening_roster("roster-1"))
        assert duplicate["disposition"] == "duplicate"
        assert duplicate["contract_revision"] == STAFFING_CONTRACT_REVISION
        for path in ("/api/v1/staffing", f"/api/v1/staffing/dates/{FLOW_SERVICE_DATE}"):
            snapshot = api.route("GET", path, {})
            validator("#/$defs/StaffingDateSnapshot").validate(snapshot)
            assert snapshot["contract_revision"] == STAFFING_CONTRACT_REVISION
        exception = api.route("POST", "/api/v1/staffing/exceptions", flow_leave("leave-1", roster=1, exception_set=0))
        assert exception["contract_revision"] == STAFFING_CONTRACT_REVISION
        ledger.verify()
        text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in _ledger_root(tmp_path).rglob("*")
            if path.is_file()
        )
        assert "contract_revision" not in text, "the revision is a wire fact, never a ledger fact"
        assert STAFFING_CONTRACT_REVISION not in text
    finally:
        api.close()
